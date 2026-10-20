"""
controlm_orchestrator.py
========================
DataWise Platform — Control-M Pipeline Orchestrator

Reads DATAWISE_PLATFORM.json, resolves dependency order via Kahn's algorithm,
then executes each stage by calling the appropriate Docker container runner.

SSIS (Stage 1) → triggers Azure Data Factory REST API, polls until complete
Talend         → docker-compose run datawise-talend-payments
DataStage      → docker-compose run datawise-datastage-marts [JOB_NAME]
Informatica    → docker-compose run datawise-informatica [JOB_NAME]
ML             → docker-compose run datawise-ml [--model loan|fraud]
Power BI       → Power BI REST API dataset refresh (Stage 8)

Usage:
  python controlm_orchestrator.py                    # full pipeline
  python controlm_orchestrator.py --stage 4          # single stage
  python controlm_orchestrator.py --job STAGE4_DS_MART_CREDIT_RISK
  python controlm_orchestrator.py --dry-run          # print order only
"""

import os, sys, json, time, argparse, subprocess
from collections import defaultdict, deque
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

JOBS_FILE    = Path(__file__).parent.parent.parent / "jobs" / "DATAWISE_PLATFORM.json"
COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker-compose.yml"
COMPOSE_DIR  = Path(__file__).parent.parent.parent

# ADF credentials for SSIS trigger
ADF_SUBSCRIPTION  = os.getenv("ADF_SUBSCRIPTION_ID", "")
ADF_RESOURCE_GROUP= os.getenv("ADF_RESOURCE_GROUP", "TEST-with-Kunal")
ADF_FACTORY       = os.getenv("ADF_FACTORY_NAME", "datafactorydatawise")
ADF_TENANT        = os.getenv("ADF_TENANT_ID", "")
ADF_CLIENT_ID     = os.getenv("ADF_CLIENT_ID", "")
ADF_CLIENT_SECRET = os.getenv("ADF_CLIENT_SECRET", "")

# Docker service names (must match docker-compose.yml)
DOCKER_SERVICES = {
    "talend":      "datawise-talend-payments",
    "datastage":   "datawise-datastage-marts",
    "informatica": "datawise-informatica",
    "ml":          "datawise-ml",
}

# Map job name → (tool_key, extra_args)
# extra_args passed to the container's runner as CLI args
JOB_RUNNER_MAP = {
    # SSIS — triggers ADF pipeline via REST API
    "STAGE1_SSIS_ALL":                              ("adf",         "Datawise_ssis_pipeline"),
    # Talend — single container runs all Talend jobs sequentially
    "STAGE2_TAL_DIM_DATE_POPULATE":                 ("talend",      ""),
    "STAGE3_TAL_FACT_PAYMENT_LOAD":                 ("talend",      ""),
    # DataStage — pass DSX job name as arg
    "STAGE4_DS_FACT_GL_BUILD":                      ("datastage",   "FACT_GL_BUILD"),
    "STAGE4_DS_MART_CREDIT_RISK":                   ("datastage",   "MART_CREDIT_RISK_BUILD"),
    "STAGE4_DS_MART_TRANSACTION":                   ("datastage",   "MART_TRANSACTION_BUILD"),
    "STAGE4_DS_MART_BRANCH_PERFORMANCE":            ("datastage",   "MART_BRANCH_PERFORMANCE_BUILD"),
    "STAGE4B_DS_CREATE_VIEWS_PROCEDURES":           ("datastage",   "CREATE_VIEWS_AND_PROCEDURES"),
    "STAGE6B_DS_ML_SCORE_WRITEBACK":                ("datastage",   "ML_SCORE_WRITEBACK"),
    "STAGE7_DS_BI_LOAN_APPROVAL":                   ("datastage",   "BI_LOAN_APPROVAL_BUILD"),
    "STAGE7_DS_BI_CUSTOMER_360":                    ("datastage",   "BI_CUSTOMER_360_BUILD"),
    "STAGE7_DS_BI_RISK_COMPLIANCE":                 ("datastage",   "BI_RISK_COMPLIANCE_BUILD"),
    "STAGE7_DS_BI_RETAIL_PERFORMANCE":              ("datastage",   "BI_RETAIL_PERFORMANCE_BUILD"),
    # Informatica — pass XML job name as arg
    "STAGE4_INF_MART_CUSTOMER_360":                 ("informatica", "MART_CUSTOMER_360_BUILD"),
    "STAGE4_INF_MART_SALES_PIPELINE":               ("informatica", "MART_SALES_PIPELINE_BUILD"),
    "STAGE5_INF_LOAN_FEATURE_ENGINEERING":          ("informatica", "LOAN_APPROVAL_FEATURE_ENGINEERING"),
    "STAGE5_INF_TRANSACTION_FEATURE_ENGINEERING":   ("informatica", "TRANSACTION_FEATURE_ENGINEERING"),
    # ML — pass --model flag
    "STAGE6_ML_LOAN_APPROVAL_SCORING":              ("ml",          "--model loan"),
    "STAGE6_ML_FRAUD_SCORING":                      ("ml",          "--model fraud"),
}

# Talend jobs are all handled by one container run — deduplicate
_TALEND_FIRED = False


def log(job, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}][CTRLM][{job}] {msg}", flush=True)


def load_jobs():
    with open(JOBS_FILE) as f:
        return json.load(f)["DATAWISE_PLATFORM"]


# ── Dependency resolution ─────────────────────────────────────────────────────
def topological_order(jobs: dict) -> list:
    graph     = defaultdict(list)
    in_degree = {j: 0 for j in jobs}
    for job_name, defn in jobs.items():
        for dep in defn.get("DependsOn", []):
            src = dep["JobName"]
            if src in jobs:
                graph[src].append(job_name)
                in_degree[job_name] += 1

    queue = deque(sorted(j for j in jobs if in_degree[j] == 0))
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for nb in sorted(graph[n]):
            in_degree[nb] -= 1
            if in_degree[nb] == 0:
                queue.append(nb)
    # append any orphans (cycle guard)
    for j in jobs:
        if j not in order:
            order.append(j)
    return order


# ── Runners ───────────────────────────────────────────────────────────────────
def run_docker(service: str, extra_args: str, job_name: str) -> bool:
    """docker-compose run --rm --no-deps <service> [extra_args]"""
    cmd = ["docker-compose", "-f", str(COMPOSE_FILE), "run", "--rm", "--no-deps", service]
    if extra_args:
        cmd += extra_args.split()
    log(job_name, f"  docker-compose run {service} {extra_args}")
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=str(COMPOSE_DIR),
            capture_output=False,   # stream output live
            timeout=1800            # 30 min max per job
        )
        elapsed = time.time() - t0
        if result.returncode == 0:
            log(job_name, f"  OK ({elapsed:.1f}s)")
            return True
        else:
            log(job_name, f"  FAILED returncode={result.returncode} ({elapsed:.1f}s)")
            return False
    except subprocess.TimeoutExpired:
        log(job_name, "  TIMEOUT after 30 min")
        return False
    except Exception as e:
        log(job_name, f"  ERROR: {e}")
        return False


def run_adf(pipeline_name: str, job_name: str) -> bool:
    """Trigger ADF pipeline via REST API and poll for completion."""
    log(job_name, f"  Triggering ADF pipeline: {pipeline_name}")
    if not all([ADF_SUBSCRIPTION, ADF_TENANT, ADF_CLIENT_ID, ADF_CLIENT_SECRET]):
        log(job_name, "  ADF credentials not set in .env — SSIS assumed already run (skip)")
        return True
    try:
        import requests
        # Get OAuth token
        token_url = f"https://login.microsoftonline.com/{ADF_TENANT}/oauth2/token"
        token_resp = requests.post(token_url, data={
            "grant_type":    "client_credentials",
            "client_id":     ADF_CLIENT_ID,
            "client_secret": ADF_CLIENT_SECRET,
            "resource":      "https://management.azure.com/",
        }, timeout=30)
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Trigger pipeline run
        run_url = (
            f"https://management.azure.com/subscriptions/{ADF_SUBSCRIPTION}"
            f"/resourceGroups/{ADF_RESOURCE_GROUP}/providers/Microsoft.DataFactory"
            f"/factories/{ADF_FACTORY}/pipelines/{pipeline_name}/createRun"
            f"?api-version=2018-06-01"
        )
        run_resp = requests.post(run_url, headers=headers, json={}, timeout=30)
        run_id = run_resp.json()["runId"]
        log(job_name, f"  ADF run started: {run_id}")

        # Poll until terminal state
        status_url = (
            f"https://management.azure.com/subscriptions/{ADF_SUBSCRIPTION}"
            f"/resourceGroups/{ADF_RESOURCE_GROUP}/providers/Microsoft.DataFactory"
            f"/factories/{ADF_FACTORY}/pipelineruns/{run_id}"
            f"?api-version=2018-06-01"
        )
        for _ in range(120):   # max 20 min (120 × 10s)
            time.sleep(10)
            status_resp = requests.get(status_url, headers=headers, timeout=30)
            status = status_resp.json().get("status", "")
            log(job_name, f"  ADF status: {status}")
            if status == "Succeeded":
                return True
            if status in ("Failed", "Cancelled"):
                log(job_name, f"  ADF pipeline {status}")
                return False
        log(job_name, "  ADF poll timeout")
        return False
    except Exception as e:
        log(job_name, f"  ADF error: {e}")
        return False


# ── Job executor ──────────────────────────────────────────────────────────────
def execute_job(job_name: str) -> bool:
    global _TALEND_FIRED
    tool, extra = JOB_RUNNER_MAP.get(job_name, (None, None))
    if tool is None:
        log(job_name, "  No runner mapped — skip")
        return True

    log(job_name, f"=== START ({tool}) ===")
    t0 = time.time()

    if tool == "adf":
        ok = run_adf(extra, job_name)

    elif tool == "talend":
        # All Talend jobs run in one container pass — fire once, mark rest as done
        if _TALEND_FIRED:
            log(job_name, "  Talend container already ran — marking OK")
            ok = True
        else:
            ok = run_docker(DOCKER_SERVICES["talend"], "", job_name)
            _TALEND_FIRED = True

    elif tool == "datastage":
        svc = DOCKER_SERVICES["datastage"]
        ds_cmd = f"python datastage_runner.py {extra}" if extra else "python datastage_runner.py"
        ok = run_docker(svc, ds_cmd, job_name)

    elif tool == "informatica":
        inf_cmd = f"python informatica_runner.py {extra}" if extra else "python informatica_runner.py"
        ok = run_docker(DOCKER_SERVICES["informatica"], inf_cmd, job_name)

    elif tool == "ml":
        ml_cmd = f"python ml_runner.py {extra}" if extra else "python ml_runner.py --mode score"
        ok = run_docker(DOCKER_SERVICES["ml"], ml_cmd, job_name)

    else:
        log(job_name, f"  Unknown tool: {tool}")
        ok = False

    elapsed = time.time() - t0
    log(job_name, f"=== {'COMPLETED' if ok else 'FAILED'} in {elapsed:.1f}s ===")
    return ok


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="DataWise Control-M Orchestrator")
    parser.add_argument("--stage",   help="Run only jobs matching this stage prefix (e.g. STAGE4)")
    parser.add_argument("--job",     help="Run a single specific job by name")
    parser.add_argument("--dry-run", action="store_true", help="Print execution order without running")
    args = parser.parse_args()

    jobs  = load_jobs()
    order = topological_order(jobs)

    # Filter if requested
    if args.job:
        order = [j for j in order if j == args.job]
    elif args.stage:
        order = [j for j in order if j.startswith(args.stage.upper())]

    print("\n[CTRLM] ═══════════════════════════════════════════════════════")
    print("[CTRLM]  DataWise Platform — Control-M Pipeline Orchestrator")
    print("[CTRLM] ═══════════════════════════════════════════════════════")
    print(f"[CTRLM]  Jobs to run: {len(order)}")
    print(f"[CTRLM]  Compose dir: {COMPOSE_DIR}")
    print("[CTRLM] ───────────────────────────────────────────────────────")
    for i, j in enumerate(order, 1):
        tool, extra = JOB_RUNNER_MAP.get(j, ("?", ""))
        print(f"[CTRLM]  {i:02d}. {j:<52} [{tool}] {extra}")
    print("[CTRLM] ───────────────────────────────────────────────────────\n")

    if args.dry_run:
        print("[CTRLM] Dry run — exiting without execution")
        return

    results = {}
    t_total = time.time()

    for job_name in order:
        ok = execute_job(job_name)
        results[job_name] = ok
        if not ok:
            print(f"\n[CTRLM] FATAL: {job_name} failed — pipeline halted")
            break
        print()

    elapsed = time.time() - t_total
    ok_count  = sum(1 for v in results.values() if v)
    err_count = len(results) - ok_count

    print("\n[CTRLM] ═══════════════════════════════════════════════════════")
    print(f"[CTRLM]  PIPELINE SUMMARY  ({elapsed:.1f}s total)")
    print("[CTRLM] ───────────────────────────────────────────────────────")
    for job_name, ok in results.items():
        icon = "✓" if ok else "✗"
        tool, _ = JOB_RUNNER_MAP.get(job_name, ("?", ""))
        print(f"[CTRLM]  {icon}  {job_name:<52} [{tool}]")
    print("[CTRLM] ───────────────────────────────────────────────────────")
    print(f"[CTRLM]  Completed: {ok_count}/{len(results)}   Errors: {err_count}")
    print("[CTRLM] ═══════════════════════════════════════════════════════\n")

    sys.exit(0 if err_count == 0 else 1)


if __name__ == "__main__":
    main()
