"""
informatica_runner.py
Informatica PowerCenter 10.5 runner for DataWise platform.

Execution order:
  1. LOAN_APPROVAL_FEATURE_ENGINEERING  -> ML.LOAN_APPROVAL_FEATURES   (SQL via XML)
  2. TRANSACTION_FEATURE_ENGINEERING    -> ML.TRANSACTION_FEATURES      (SQL via XML)
  3. ML_SCORE_LOAN                      -> ML.MODEL_SCORE_LOAN          (real sklearn/XGBoost model)
  4. ML_SCORE_FRAUD                     -> ML.MODEL_SCORE_FRAUD         (real sklearn/XGBoost model)
  5. MART_CUSTOMER_360_BUILD            -> MART.MART_CUSTOMER_360       (SQL via XML)
  6. MART_SALES_PIPELINE_BUILD          -> MART.MART_SALES_PIPELINE     (SQL via XML)

ML mode: INFERENCE only - applies pre-trained model artifacts from /models/
  loan_approval_model.pkl + loan_approval_scaler.pkl + loan_approval_encoders.pkl
  fraud_model.pkl         + fraud_scaler.pkl         + fraud_encoders.pkl
"""

import os, sys, time, re, json
from pathlib import Path
from dotenv import load_dotenv
import pyodbc

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

SQL_SERVER   = os.getenv("SQL_SERVER")
SQL_DATABASE = os.getenv("SQL_DATABASE")
SQL_USERNAME = os.getenv("SQL_USERNAME")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")

CONN_STR = (
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};"
    f"UID={SQL_USERNAME};PWD={SQL_PASSWORD};"
    f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
)

XML_DIR   = Path(__file__).parent / "files"
ML_DIR    = Path(__file__).parent.parent / "ml"   # docker-deploy/ml
MODEL_DIR = Path(__file__).parent.parent / "ml" / "models"

NON_FATAL = ["DQ", "AUDIT"]


def log(job, msg):
    print(f"[{time.strftime('%H:%M:%S')}][INFA][{job}] {msg}", flush=True)


# ── DB connection ─────────────────────────────────────────────────────────────
def get_conn():
    return pyodbc.connect(CONN_STR, autocommit=False)


# ── XML parser: extract source SQL + target table ─────────────────────────────
def parse_xml(path: Path) -> list:
    """Returns list of {name, source_sql, target_schema, target_table, write_mode}."""
    s = path.read_bytes().decode("utf-8", errors="replace")
    mappings = []

    for mm in re.finditer(r'<MAPPING\s+NAME="([^"]+)"', s):
        name = mm.group(1)

        sq = re.search(r'<TABLEATTRIBUTE\s+NAME="Sql Query"\s+VALUE="([^"]*)"', s)
        sql = sq.group(1).strip() if sq else ""

        # Parse target: find the TARGET element and extract NAME + OWNERNAME
        tgt_elem = re.search(r'<TARGET\s([^>]+)>', s)
        if tgt_elem:
            attrs = dict(re.findall(r'(\w+)="([^"]+)"', tgt_elem.group(1)))
            raw_name      = attrs.get("NAME", "")
            target_schema = attrs.get("OWNERNAME", "")
            target_table  = raw_name[4:] if raw_name.upper().startswith("TGT_") else raw_name
            write_mode    = attrs.get("TABLEOPTIONS", "TRUNCATE_INSERT")
        else:
            target_table = target_schema = write_mode = ""

        mappings.append({
            "name": name, "source_sql": sql,
            "target_schema": target_schema, "target_table": target_table,
            "write_mode": write_mode,
        })

    return mappings


# ── Execute a SQL-based mapping ───────────────────────────────────────────────
def execute_sql_mapping(conn, job_name: str, m: dict) -> bool:
    sql   = m["source_sql"]
    schema = m["target_schema"]
    table  = m["target_table"]
    full_table = f"{schema}.{table}" if schema else table
    mode  = m["write_mode"].upper()

    if not sql.strip() or not table:
        log(job_name, f"  SKIP [{m['name']}] no SQL or target")
        return True

    log(job_name, f"  EXEC [{m['name']}] -> {full_table}")
    t0 = time.time()
    try:
        cur = conn.cursor()
        if "TRUNCATE" in mode:
            cur.execute(f"TRUNCATE TABLE {full_table};")

        # Get non-identity, non-computed columns using sys tables (more reliable than COLUMNPROPERTY)
        cur.execute(
            f"SELECT c.name FROM sys.columns c "
            f"WHERE c.object_id = OBJECT_ID('{schema}.{table}') "
            f"AND c.is_identity = 0 AND c.is_computed = 0 "
            f"ORDER BY c.column_id"
        )
        cols = [r[0] for r in cur.fetchall()]
        log(job_name, f"  INSERT cols ({len(cols)}): {cols[:5]}...")
        if cols:
            col_list = ", ".join(f"[{c}]" for c in cols)
            insert_sql = f"INSERT INTO {full_table} ({col_list})\n{sql}"
        else:
            insert_sql = f"INSERT INTO {full_table}\n{sql}"

        cur.execute(insert_sql)
        conn.commit()
        log(job_name, f"  OK   [{m['name']}] {time.time()-t0:.2f}s")
        return True
    except pyodbc.Error as e:
        log(job_name, f"  FAIL [{m['name']}] {time.time()-t0:.2f}s - {str(e)[:300]}")
        try: conn.rollback()
        except: pass
        return False


# ── Run real ML model (inference only) ───────────────────────────────────────
def run_ml_score_loan(job_name: str) -> bool:
    log(job_name, "  Loading loan approval model artifacts...")
    t0 = time.time()
    try:
        if str(ML_DIR) not in sys.path:
            sys.path.insert(0, str(ML_DIR))
        import importlib.util
        spec = importlib.util.spec_from_file_location("ml_pipeline", ML_DIR / "ml_pipeline.py")
        ml   = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ml)
        ml.MODEL_DIR = MODEL_DIR  # point to docker-deploy/ml/models/

        try:
            conn_check = get_conn()
            cur = conn_check.cursor()
            cur.execute("SELECT COUNT(*) FROM ML.LOAN_APPROVAL_FEATURES")
            cnt = cur.fetchone()[0]
            conn_check.close()
        except Exception:
            cnt = 0

        if cnt == 0:
            log(job_name, "  ML.LOAN_APPROVAL_FEATURES empty - using synthetic fallback")

        log(job_name, "  Running loan approval inference (score mode)...")
        scores_df = ml.score()
        log(job_name, f"  OK   [{job_name}] {len(scores_df)} scores written in {time.time()-t0:.1f}s")
        return True
    except Exception as e:
        log(job_name, f"  FAIL [{job_name}] {time.time()-t0:.1f}s - {e}")
        return False


def run_ml_score_fraud(job_name: str) -> bool:
    log(job_name, "  Loading fraud detection model artifacts...")
    t0 = time.time()
    try:
        if str(ML_DIR) not in sys.path:
            sys.path.insert(0, str(ML_DIR))
        import importlib.util
        spec = importlib.util.spec_from_file_location("fraud_pipeline", ML_DIR / "fraud_pipeline.py")
        fp   = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fp)
        fp.MODEL_DIR = MODEL_DIR  # point to docker-deploy/ml/models/

        try:
            conn_check = get_conn()
            cur = conn_check.cursor()
            cur.execute("SELECT COUNT(*) FROM ML.TRANSACTION_FEATURES")
            cnt = cur.fetchone()[0]
            conn_check.close()
        except Exception:
            cnt = 0

        if cnt == 0:
            log(job_name, "  ML.TRANSACTION_FEATURES empty - using synthetic fallback")

        log(job_name, "  Running fraud detection inference (score mode)...")
        scores_df = fp.score()
        log(job_name, f"  OK   [{job_name}] {len(scores_df)} scores written in {time.time()-t0:.1f}s")
        return True
    except Exception as e:
        log(job_name, f"  FAIL [{job_name}] {time.time()-t0:.1f}s - {e}")
        return False


# ── Job runner ────────────────────────────────────────────────────────────────
def run_job(xml_path: Path, conn) -> dict:
    job_name = xml_path.stem
    log(job_name, f"=== START {job_name} ===")
    t0 = time.time()

    # ML scoring jobs use real model — not SQL
    if job_name == "ML_SCORE_LOAN":
        ok = run_ml_score_loan(job_name)
        elapsed = time.time() - t0
        status = "COMPLETED" if ok else "COMPLETED_WITH_ERRORS"
        log(job_name, f"=== {status} in {elapsed:.1f}s ===")
        return {"job": job_name, "status": status, "elapsed_s": round(elapsed, 1),
                "mappings": 1, "failed": [] if ok else ["ML_SCORE_LOAN"], "skipped": []}

    if job_name == "ML_SCORE_FRAUD":
        ok = run_ml_score_fraud(job_name)
        elapsed = time.time() - t0
        status = "COMPLETED" if ok else "COMPLETED_WITH_ERRORS"
        log(job_name, f"=== {status} in {elapsed:.1f}s ===")
        return {"job": job_name, "status": status, "elapsed_s": round(elapsed, 1),
                "mappings": 1, "failed": [] if ok else ["ML_SCORE_FRAUD"], "skipped": []}

    # All other jobs: parse XML and execute SQL
    mappings = parse_xml(xml_path)
    log(job_name, f"Parsed: {len(mappings)} mapping(s)")

    failed, skipped = [], []
    for m in mappings:
        ok = execute_sql_mapping(conn, job_name, m)
        if not ok:
            failed.append(m["name"])
            if any(kw in m["name"].upper() for kw in NON_FATAL):
                log(job_name, f"  NON-FATAL [{m['name']}] - continuing")
                skipped.append(m["name"])
            else:
                log(job_name, f"  FATAL [{m['name']}] - aborting job")
                break

    elapsed = time.time() - t0
    fatal   = [f for f in failed if f not in skipped]
    status  = "COMPLETED" if not fatal else "COMPLETED_WITH_ERRORS"
    log(job_name, f"=== {status} in {elapsed:.1f}s ===")
    if fatal:
        log(job_name, f"  Failed: {fatal}")

    return {"job": job_name, "status": status, "elapsed_s": round(elapsed, 1),
            "mappings": len(mappings), "failed": failed, "skipped": skipped}


# ── Main ──────────────────────────────────────────────────────────────────────
JOB_ORDER = [
    ("LOAN_APPROVAL_FEATURE_ENGINEERING.xml", "LOAN_APPROVAL_FEATURE_ENGINEERING"),
    ("TRANSACTION_FEATURE_ENGINEERING.xml",   "TRANSACTION_FEATURE_ENGINEERING"),
    ("ML_SCORE_LOAN.xml",                     "ML_SCORE_LOAN"),
    ("ML_SCORE_FRAUD.xml",                    "ML_SCORE_FRAUD"),
    ("MART_CUSTOMER_360_BUILD.xml",           "MART_CUSTOMER_360_BUILD"),
    ("MART_SALES_PIPELINE_BUILD.xml",         "MART_SALES_PIPELINE_BUILD"),
]


def main():
    requested = sys.argv[1:] if len(sys.argv) > 1 else None
    jobs = [(XML_DIR / f, n) for f, n in JOB_ORDER if (XML_DIR / f).exists()]
    if requested:
        jobs = [(p, n) for p, n in jobs if any(r.upper() in n.upper() for r in requested)]

    if not jobs:
        print("[INFA] No XML files found.")
        sys.exit(1)

    print("[INFA] DataWise Informatica Runner (Inference Pipeline)")
    print(f"[INFA] Jobs   : {[n for _, n in jobs]}")
    print(f"[INFA] Target : {SQL_SERVER}/{SQL_DATABASE}")
    print(f"[INFA] Models : {MODEL_DIR}")
    print("[INFA] Mode   : INFERENCE - pre-trained model artifacts applied to new data")
    print("[INFA] " + "-"*60)

    print("[INFA] Connecting to Azure SQL...")
    try:
        conn = get_conn()
        print("[INFA] Connected.")
    except pyodbc.Error as e:
        print(f"[INFA] CONNECTION FAILED: {e}")
        sys.exit(1)

    results = []
    t_total = time.time()

    for xml_path, _ in jobs:
        result = run_job(xml_path, conn)
        results.append(result)
        print("[INFA] " + "-"*60)

    conn.close()

    print(f"\n[INFA] ====== PIPELINE SUMMARY ({time.time()-t_total:.1f}s) ======")
    for r in results:
        icon = "OK " if r["status"] == "COMPLETED" else "ERR"
        print(f"[INFA]  {icon}  {r['job']:<42} {r['elapsed_s']:>6.1f}s  mappings={r['mappings']}")
        if r["failed"]:
            print(f"[INFA]       failed: {r['failed']}")

    ok  = sum(1 for r in results if r["status"] == "COMPLETED")
    err = len(results) - ok
    print(f"[INFA] Completed: {ok}/{len(results)}  Errors: {err}")

    summary_path = Path(__file__).parent / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump({"run_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "total_elapsed_s": round(time.time()-t_total, 1),
                   "mode": "inference",
                   "jobs": results}, f, indent=2)
    print(f"[INFA] Summary -> {summary_path}")
    sys.exit(0 if err == 0 else 1)


if __name__ == "__main__":
    main()
