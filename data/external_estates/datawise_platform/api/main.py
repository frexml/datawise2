"""
DataWise Control-M API
======================
FastAPI server that:
  - Serves the pipeline runner UI
  - Exposes /run/pipeline  → runs full pipeline via docker-compose
  - Exposes /run/stage     → runs a single stage
  - Exposes /run/job       → runs a single job (for UI node buttons)
  - Streams live logs via SSE  GET /logs/{run_id}
  - SSIS (Stage 1) + Power BI (Stage 8) = dummy skip (marked OK instantly)
  - All other stages = real docker-compose run
"""

import json, uuid, os, subprocess, sys, threading, time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests as _requests

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

BASE        = Path(__file__).parent.parent          # datawise-platform/
JOBS_FILE   = BASE / "jobs" / "DATAWISE_PLATFORM.json"
UI_FILE     = BASE / "graphical" / "pipeline_runner.html"
UI_OLD_FILE = BASE / "graphical" / "index.html"
COMPOSE     = Path("/app/platform/docker-compose.yml")

app = FastAPI(title="DataWise Control-M API", version="9.21.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── in-memory state ───────────────────────────────────────────────────────────
run_registry: dict[str, dict] = {}   # run_id → {status, logs[], jobs{}}

# ── helpers ───────────────────────────────────────────────────────────────────
def load_jobs() -> dict:
    with open(JOBS_FILE) as f:
        return json.load(f)["DATAWISE_PLATFORM"]

def topo_order(jobs: dict) -> list:
    graph     = defaultdict(list)
    in_degree = {j: 0 for j in jobs}
    for name, defn in jobs.items():
        for dep in defn.get("DependsOn", []):
            src = dep["JobName"]
            if src in jobs:
                graph[src].append(name)
                in_degree[name] += 1
    queue = deque(sorted(j for j in jobs if in_degree[j] == 0))
    order = []
    while queue:
        n = queue.popleft(); order.append(n)
        for nb in sorted(graph[n]):
            in_degree[nb] -= 1
            if in_degree[nb] == 0: queue.append(nb)
    for j in jobs:
        if j not in order: order.append(j)
    return order

# ── ADF trigger for SSIS Stage 1 ─────────────────────────────────────────────
ADF_SUBSCRIPTION  = os.getenv("ADF_SUBSCRIPTION_ID", "").strip()
ADF_RESOURCE_GROUP= os.getenv("ADF_RESOURCE_GROUP", "").strip()
ADF_FACTORY       = os.getenv("ADF_FACTORY_NAME", "").strip()
ADF_TENANT        = os.getenv("ADF_TENANT_ID", "").strip()
ADF_CLIENT_ID     = os.getenv("ADF_CLIENT_ID", "").strip()
ADF_CLIENT_SECRET = os.getenv("ADF_CLIENT_SECRET", "").strip()
ADF_PIPELINE_NAME = "Datawise_ssis_pipeline"

def _run_adf(run_id: str, job: str) -> bool:
    """Trigger ADF pipeline and poll until Succeeded/Failed."""
    if not all([ADF_SUBSCRIPTION, ADF_TENANT, ADF_CLIENT_ID, ADF_CLIENT_SECRET]):
        _append_log(run_id, job, "ADF credentials not set — SSIS skipped (marked OK)")
        return True
    try:
        # Get OAuth token
        _append_log(run_id, job, "Acquiring ADF OAuth token...")
        token_resp = _requests.post(
            f"https://login.microsoftonline.com/{ADF_TENANT}/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": ADF_CLIENT_ID,
                  "client_secret": ADF_CLIENT_SECRET, "resource": "https://management.azure.com/"},
            timeout=30
        )
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        _append_log(run_id, job, "Token OK — triggering ADF pipeline...")

        # Trigger pipeline run
        run_url = (
            f"https://management.azure.com/subscriptions/{ADF_SUBSCRIPTION}"
            f"/resourceGroups/{ADF_RESOURCE_GROUP}/providers/Microsoft.DataFactory"
            f"/factories/{ADF_FACTORY}/pipelines/{ADF_PIPELINE_NAME}/createRun"
            f"?api-version=2018-06-01"
        )
        run_resp = _requests.post(run_url, headers=headers, json={}, timeout=30)
        if run_resp.status_code not in (200, 202):
            _append_log(run_id, job, f"ADF trigger failed: {run_resp.status_code} {run_resp.text[:200]}")
            return False
        run_id_adf = run_resp.json()["runId"]
        _append_log(run_id, job, f"ADF run started: {run_id_adf}")

        # Poll until terminal state
        status_url = (
            f"https://management.azure.com/subscriptions/{ADF_SUBSCRIPTION}"
            f"/resourceGroups/{ADF_RESOURCE_GROUP}/providers/Microsoft.DataFactory"
            f"/factories/{ADF_FACTORY}/pipelineruns/{run_id_adf}"
            f"?api-version=2018-06-01"
        )
        for _ in range(120):  # max 20 min
            time.sleep(10)
            status = _requests.get(status_url, headers=headers, timeout=30).json().get("status", "")
            _append_log(run_id, job, f"ADF status: {status}")
            if status == "Succeeded":
                return True
            if status in ("Failed", "Cancelled"):
                _append_log(run_id, job, f"ADF pipeline {status}")
                return False
        _append_log(run_id, job, "ADF poll timeout after 20 min")
        return False
    except Exception as e:
        _append_log(run_id, job, f"ADF error: {e}")
        return False

# Map job → (service, extra_args)  None service = dummy skip
JOB_MAP = {
    "STAGE1_SSIS_ALL":                              ("__ADF__",                  ""),
    "STAGE2_TAL_DIM_DATE_POPULATE":                 ("datawise-talend-payments", ""),
    "STAGE3_TAL_FACT_PAYMENT_LOAD":                 ("datawise-talend-payments", ""),
    "STAGE4_DS_FACT_GL_BUILD":                      ("datawise-datastage-marts", "FACT_GL_BUILD"),
    "STAGE4_DS_MART_CREDIT_RISK":                   ("datawise-datastage-marts", "MART_CREDIT_RISK_BUILD"),
    "STAGE4_DS_MART_TRANSACTION":                   ("datawise-datastage-marts", "MART_TRANSACTION_BUILD"),
    "STAGE4_DS_MART_BRANCH_PERFORMANCE":            ("datawise-datastage-marts", "MART_BRANCH_PERFORMANCE_BUILD"),
    "STAGE4_INF_MART_CUSTOMER_360":                 ("datawise-informatica",     "MART_CUSTOMER_360_BUILD"),
    "STAGE4_INF_MART_SALES_PIPELINE":               ("datawise-informatica",     "MART_SALES_PIPELINE_BUILD"),
    "STAGE4B_DS_CREATE_VIEWS_PROCEDURES":           ("datawise-datastage-marts", "CREATE_VIEWS_AND_PROCEDURES"),
    "STAGE5_INF_LOAN_FEATURE_ENGINEERING":          ("datawise-informatica",     "LOAN_APPROVAL_FEATURE_ENGINEERING"),
    "STAGE5_INF_TRANSACTION_FEATURE_ENGINEERING":   ("datawise-informatica",     "TRANSACTION_FEATURE_ENGINEERING"),
    "STAGE6_ML_LOAN_APPROVAL_SCORING":              ("datawise-ml",              "loan"),
    "STAGE6_ML_FRAUD_SCORING":                      ("datawise-ml",              "fraud"),
    "STAGE6B_DS_ML_SCORE_WRITEBACK":                ("datawise-datastage-marts", "ML_SCORE_WRITEBACK"),
    "STAGE7_DS_BI_LOAN_APPROVAL":                   ("datawise-datastage-marts", "BI_LOAN_APPROVAL_BUILD"),
    "STAGE7_DS_BI_CUSTOMER_360":                    ("datawise-datastage-marts", "BI_CUSTOMER_360_BUILD"),
    "STAGE7_DS_BI_RISK_COMPLIANCE":                 ("datawise-datastage-marts", "BI_RISK_COMPLIANCE_BUILD"),
    "STAGE7_DS_BI_RETAIL_PERFORMANCE":              ("datawise-datastage-marts", "BI_RETAIL_PERFORMANCE_BUILD"),
}

# Talend container runs once for all Talend jobs
_TALEND_SERVICES = {"datawise-talend-payments"}
_talend_done: set = set()   # per run_id

def _append_log(run_id: str, job: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}][{job}] {msg}"
    run_registry[run_id]["logs"].append(line)
    print(line, flush=True)

def _set_job_status(run_id: str, job: str, status: str):
    run_registry[run_id]["jobs"][job] = status

# Service → entrypoint override so extra args are passed as script arguments
SERVICE_ENTRY = {
    "datawise-talend-payments": ["python", "talend_runner.py"],
    "datawise-datastage-marts": ["python", "datastage_runner.py"],
    "datawise-informatica":     ["python", "informatica_runner.py"],
    "datawise-ml":              ["python", "ml_runner.py", "--mode", "score", "--model"],
}

def _run_docker(run_id: str, job: str, service: str, extra: str) -> bool:
    """Fire docker-compose run --rm <service> [extra] and stream output into logs."""
    # Talend: all jobs share one container run — fire once
    if service in _TALEND_SERVICES:
        if run_id in _talend_done:
            _append_log(run_id, job, f"Talend container already ran — marking OK")
            return True
        _talend_done.add(run_id)

    cmd = ["docker-compose", "-f", str(COMPOSE), "run", "--rm", service]
    entry = SERVICE_ENTRY.get(service, [])
    if entry:
        cmd += entry
    if extra:
        cmd += extra.split()

    _append_log(run_id, job, f"docker-compose run {service} {extra}")
    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(COMPOSE.parent),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            run_registry[run_id]["logs"].append(line.rstrip())
        proc.wait(timeout=1800)
        elapsed = round(time.time() - t0, 1)
        ok = proc.returncode == 0
        _append_log(run_id, job, f"{'OK' if ok else 'FAILED'} (exit {proc.returncode}, {elapsed}s)")
        return ok
    except subprocess.TimeoutExpired:
        proc.kill()
        _append_log(run_id, job, "TIMEOUT after 30 min")
        return False
    except Exception as e:
        _append_log(run_id, job, f"ERROR: {e}")
        return False

def _execute_job(run_id: str, job: str) -> bool:
    service, extra = JOB_MAP.get(job, (None, ""))
    if service is None:
        _append_log(run_id, job, "SKIP (dummy, marked OK)")
        return True
    if service == "__ADF__":
        return _run_adf(run_id, job)
    return _run_docker(run_id, job, service, extra)

def _run_jobs_thread(run_id: str, jobs_to_run: list):
    """Background thread — executes jobs in order, updates registry."""
    reg = run_registry[run_id]
    reg["status"] = "running"
    t0 = time.time()

    for job in jobs_to_run:
        if reg.get("abort"):
            _append_log(run_id, job, "ABORTED")
            _set_job_status(run_id, job, "aborted")
            continue

        _append_log(run_id, job, "=== START ===")
        _set_job_status(run_id, job, "running")
        ok = _execute_job(run_id, job)
        status = "ok" if ok else "failed"
        _set_job_status(run_id, job, status)
        _append_log(run_id, job, f"=== {'COMPLETED' if ok else 'FAILED'} ===")

        if not ok:
            # Power BI failures are non-fatal
            if not job.startswith("STAGE8_"):
                _append_log(run_id, "PIPELINE", f"FATAL: {job} failed — halting")
                reg["status"] = "failed"
                reg["endTime"] = datetime.utcnow().isoformat()
                reg["elapsed"] = round(time.time() - t0, 1)
                return

    reg["status"] = "completed"
    reg["endTime"] = datetime.utcnow().isoformat()
    reg["elapsed"] = round(time.time() - t0, 1)
    _append_log(run_id, "PIPELINE", f"ALL DONE in {reg['elapsed']}s")

# ── Auth ──────────────────────────────────────────────────────────────────────
class LoginReq(BaseModel):
    username: str
    password: str

@app.post("/session/login")
def login(req: LoginReq):
    if req.username == "datawise" and req.password == "datawise123":
        return {"token": "ctrlm-datawise-token-2026", "version": "9.21.0"}
    raise HTTPException(401, "Invalid credentials")

# ── Config ────────────────────────────────────────────────────────────────────
@app.get("/config/jobs")
def get_jobs(authorization: str = Header(None)):
    jobs = load_jobs()
    result = []
    for name, defn in jobs.items():
        result.append({
            "jobName":   name,
            "tool":      defn.get("Tool"),
            "source":    defn.get("Source"),
            "target":    defn.get("Target"),
            "artifact":  defn.get("Artifact"),
            "host":      defn.get("Host"),
            "dependsOn": defn.get("DependsOn", []),
            "description": defn.get("Description"),
        })
    return {"count": len(result), "jobs": result}

@app.get("/lineage/{job_name}")
def lineage(job_name: str, authorization: str = Header(None)):
    jobs = load_jobs()
    if job_name not in jobs:
        raise HTTPException(404, f"{job_name} not found")
    defn = jobs[job_name]
    upstream = defn.get("DependsOn", [])
    downstream = [
        {"jobName": n, "tool": d.get("Tool"), "target": d.get("Target")}
        for n, d in jobs.items()
        if any(dep["JobName"] == job_name for dep in d.get("DependsOn", []))
    ]
    return {
        "jobName": job_name, "source": defn.get("Source"), "target": defn.get("Target"),
        "tool": defn.get("Tool"), "upstream": upstream, "downstream": downstream,
        "lineageDepth": {"upstreamCount": len(upstream), "downstreamCount": len(downstream)},
    }

# ── Run endpoints ─────────────────────────────────────────────────────────────
@app.post("/run/pipeline")
def run_pipeline(authorization: str = Header(None)):
    """Start full pipeline in background thread."""
    jobs  = load_jobs()
    order = topo_order(jobs)
    run_id = str(uuid.uuid4())[:8].upper()
    run_registry[run_id] = {
        "runId": run_id, "type": "pipeline",
        "status": "queued", "startTime": datetime.utcnow().isoformat(),
        "endTime": None, "elapsed": None,
        "jobs": {j: "waiting" for j in order},
        "logs": [f"[PIPELINE] Starting full pipeline — {len(order)} jobs"],
    }
    t = threading.Thread(target=_run_jobs_thread, args=(run_id, order), daemon=True)
    t.start()
    return {"runId": run_id, "jobs": len(order), "status": "queued"}

@app.post("/run/stage/{stage_prefix}")
def run_stage(stage_prefix: str, authorization: str = Header(None)):
    """Run all jobs matching a stage prefix e.g. STAGE4"""
    jobs  = load_jobs()
    order = [j for j in topo_order(jobs) if j.upper().startswith(stage_prefix.upper())]
    if not order:
        raise HTTPException(404, f"No jobs found for stage {stage_prefix}")
    run_id = str(uuid.uuid4())[:8].upper()
    run_registry[run_id] = {
        "runId": run_id, "type": f"stage:{stage_prefix}",
        "status": "queued", "startTime": datetime.utcnow().isoformat(),
        "endTime": None, "elapsed": None,
        "jobs": {j: "waiting" for j in order},
        "logs": [f"[STAGE] Running {stage_prefix} — {len(order)} jobs"],
    }
    t = threading.Thread(target=_run_jobs_thread, args=(run_id, order), daemon=True)
    t.start()
    return {"runId": run_id, "jobs": len(order), "status": "queued"}

@app.post("/run/job/{job_name}")
def run_job(job_name: str, authorization: str = Header(None)):
    """Run a single job."""
    jobs = load_jobs()
    if job_name not in jobs:
        raise HTTPException(404, f"{job_name} not found")
    run_id = str(uuid.uuid4())[:8].upper()
    run_registry[run_id] = {
        "runId": run_id, "type": f"job:{job_name}",
        "status": "queued", "startTime": datetime.utcnow().isoformat(),
        "endTime": None, "elapsed": None,
        "jobs": {job_name: "waiting"},
        "logs": [f"[JOB] Running {job_name}"],
    }
    t = threading.Thread(target=_run_jobs_thread, args=(run_id, [job_name]), daemon=True)
    t.start()
    return {"runId": run_id, "jobName": job_name, "status": "queued"}

@app.get("/run/status/{run_id}")
def run_status(run_id: str, authorization: str = Header(None)):
    if run_id not in run_registry:
        raise HTTPException(404, "Run not found")
    r = run_registry[run_id]
    return {
        "runId":     r["runId"],
        "status":    r["status"],
        "elapsed":   r["elapsed"],
        "jobs":      r["jobs"],
        "logCount":  len(r["logs"]),
    }

@app.get("/run/logs/{run_id}")
def run_logs(run_id: str, since: int = 0, authorization: str = Header(None)):
    """Return logs from line `since` onwards — UI polls this."""
    if run_id not in run_registry:
        raise HTTPException(404, "Run not found")
    logs = run_registry[run_id]["logs"]
    return {"runId": run_id, "lines": logs[since:], "total": len(logs)}

@app.post("/run/abort/{run_id}")
def abort_run(run_id: str, authorization: str = Header(None)):
    if run_id not in run_registry:
        raise HTTPException(404, "Run not found")
    run_registry[run_id]["abort"] = True
    return {"runId": run_id, "status": "aborting"}

@app.get("/run/jobs/status")
def all_status(authorization: str = Header(None)):
    return {"runs": [
        {"runId": r["runId"], "type": r["type"], "status": r["status"], "elapsed": r["elapsed"]}
        for r in run_registry.values()
    ]}

# ── UI ────────────────────────────────────────────────────────────────────────
@app.get("/ui", response_class=HTMLResponse)
def serve_ui():
    if not UI_FILE.exists():
        raise HTTPException(404, "UI not found")
    return UI_FILE.read_text(encoding="utf-8")

@app.get("/monitor", response_class=HTMLResponse)
def serve_monitor():
    f = UI_OLD_FILE if UI_OLD_FILE.exists() else UI_FILE
    return f.read_text(encoding="utf-8")

@app.get("/health")
def health():
    return {"status": "ok", "jobs_file": str(JOBS_FILE), "compose": str(COMPOSE)}
