"""
datastage_runner.py
IBM DataStage 11.7 job runner for DataWise platform.

Parses all 10 .dsx job files, builds topological execution graph
from <Link> elements, executes SQL nodes against Azure SQL in order.
Non-fatal nodes: DQ, AUDIT_END, ROWCOUNT.
"""

import os, sys, time, re, json
from collections import defaultdict, deque
from pathlib import Path
from dotenv import load_dotenv
import pyodbc

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
DSX_DIR      = Path(__file__).parent / "files"
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

NON_FATAL = ["DQ", "AUDIT_END", "ROWCOUNT", "BUILD_SCORE_SUMMARY", "SCORE_SUMMARY", "MODEL_PERFORMANCE", "MERGE_MODEL"]

JOB_ORDER = [
    "MART_TRANSACTION_BUILD",
    "MART_CREDIT_RISK_BUILD",
    "MART_BRANCH_PERFORMANCE_BUILD",
    "FACT_GL_BUILD",
    "ML_SCORE_WRITEBACK",
    "BI_LOAN_APPROVAL_BUILD",
    "BI_CUSTOMER_360_BUILD",
    "BI_RETAIL_PERFORMANCE_BUILD",
    "BI_RISK_COMPLIANCE_BUILD",
    "CREATE_VIEWS_AND_PROCEDURES",
]

# ── Logging ───────────────────────────────────────────────────────────────────
def log(job, msg):
    print(f"[{time.strftime('%H:%M:%S')}][DS][{job}] {msg}", flush=True)


# ── DSX Parser ────────────────────────────────────────────────────────────────
def parse_dsx(path: Path) -> tuple:
    """
    Regex-based parser (avoids ET choking on CDATA).
    Returns (job_name, nodes, graph, in_degree).
    """
    s = path.read_bytes().decode("utf-8", errors="replace")

    m = re.search(r'<Job\s+Identifier="([^"]+)"', s)
    job_name = m.group(1) if m else path.stem

    nodes     = {}
    graph     = defaultdict(list)
    in_degree = defaultdict(int)

    for m in re.finditer(
        r'<Record\s+Identifier="([^"]+)"\s+Type="([^"]+)"[^>]*>(.*?)</Record>',
        s, re.DOTALL
    ):
        rid, rtype, body = m.group(1), m.group(2), m.group(3)
        if re.match(r'^L\d+$', rid):
            continue

        def prop(name, b=body):
            pm = re.search(
                r'<Property\s+Name="' + re.escape(name) + r'">(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</Property>',
                b, re.DOTALL
            )
            return pm.group(1).strip() if pm else ""

        nodes[rid] = {
            "name":         prop("Name") or rid,
            "type":         rtype,
            "sql":          prop("sql_query"),
            "target_table": prop("table_name"),
            "write_mode":   prop("write_mode"),
        }
        in_degree.setdefault(rid, 0)

    for m in re.finditer(r'<Link\s([^/]*)/>', s):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        src, tgt = attrs.get("SourceStage", ""), attrs.get("TargetStage", "")
        if src and tgt and src in nodes and tgt in nodes:
            graph[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1
            in_degree.setdefault(src, 0)

    return job_name, nodes, dict(graph), dict(in_degree)


# ── Topological sort ──────────────────────────────────────────────────────────
def topological_sort(nodes, graph, in_degree):
    deg   = dict(in_degree)
    queue = deque(sorted([n for n in nodes if deg.get(n, 0) == 0]))
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for nb in sorted(graph.get(n, [])):
            deg[nb] -= 1
            if deg[nb] == 0:
                queue.append(nb)
    for n in nodes:
        if n not in order:
            order.append(n)
    return order


# ── SQL Execution ─────────────────────────────────────────────────────────────
def execute_node(conn, job_name, node):
    sql = node["sql"]

    # target nodes with TRUNCATE_INSERT but no explicit SQL: generate TRUNCATE
    if node["write_mode"] == "TRUNCATE_INSERT" and node["target_table"] and not sql.strip():
        sql = f"TRUNCATE TABLE {node['target_table']};"

    if not sql.strip():
        log(job_name, f"  SKIP [{node['name']}] no SQL")
        return True

    log(job_name, f"  EXEC [{node['name']}] ({node['type']})")
    t0 = time.time()
    try:
        cur = conn.cursor()
        stmts = [s.strip() for s in re.split(r'\bGO\b', sql, flags=re.IGNORECASE) if s.strip()]
        for stmt in (stmts or [sql]):
            cur.execute(stmt)
        conn.commit()
        log(job_name, f"  OK   [{node['name']}] {time.time()-t0:.2f}s")
        return True
    except pyodbc.Error as e:
        log(job_name, f"  FAIL [{node['name']}] {time.time()-t0:.2f}s - {str(e)[:300]}")
        try: conn.rollback()
        except: pass
        return False


# ── Job Runner ────────────────────────────────────────────────────────────────
def run_job(dsx_path: Path, conn) -> dict:
    job_name = dsx_path.stem
    log(job_name, f"=== START {job_name} ===")
    t0 = time.time()

    _, nodes, graph, in_degree = parse_dsx(dsx_path)
    log(job_name, f"Parsed: {len(nodes)} nodes, {sum(len(v) for v in graph.values())} links")

    order = topological_sort(nodes, graph, in_degree)
    log(job_name, f"Execution order ({len(order)} nodes):")
    for i, nid in enumerate(order, 1):
        log(job_name, f"  {i:02d}. {nodes[nid]['name']}")

    failed, skipped = [], []
    for nid in order:
        node = nodes[nid]
        ok = execute_node(conn, job_name, node)
        if not ok:
            failed.append(node["name"])
            if any(kw in node["name"].upper() for kw in NON_FATAL):
                log(job_name, f"  NON-FATAL [{node['name']}] - continuing")
                skipped.append(node["name"])
            else:
                log(job_name, f"  FATAL [{node['name']}] - aborting job")
                break

    elapsed = time.time() - t0
    fatal   = [f for f in failed if f not in skipped]
    status  = "COMPLETED" if not fatal else "COMPLETED_WITH_ERRORS"
    log(job_name, f"=== {status} in {elapsed:.1f}s ===")
    if fatal:
        log(job_name, f"  Failed: {fatal}")

    return {
        "job": job_name, "status": status,
        "elapsed_s": round(elapsed, 1),
        "nodes": len(nodes), "failed": failed, "skipped": skipped,
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    requested = sys.argv[1:] if len(sys.argv) > 1 else None

    dsx_files = sorted(DSX_DIR.glob("*.dsx"), key=lambda f: JOB_ORDER.index(f.stem) if f.stem in JOB_ORDER else 99)
    if requested:
        dsx_files = [f for f in dsx_files if any(r.upper() in f.stem.upper() for r in requested)]

    if not dsx_files:
        print("[DS] No DSX files found.")
        sys.exit(1)

    print("[DS] DataWise DataStage Runner")
    print(f"[DS] Jobs : {[f.stem for f in dsx_files]}")
    print(f"[DS] Target: {SQL_SERVER}/{SQL_DATABASE}")
    print("[DS] " + "-"*60)

    print("[DS] Connecting to Azure SQL...")
    try:
        conn = pyodbc.connect(CONN_STR, autocommit=False)
        print("[DS] Connected.")
    except pyodbc.Error as e:
        print(f"[DS] CONNECTION FAILED: {e}")
        sys.exit(1)

    results = []
    t_total = time.time()

    for dsx_path in dsx_files:
        result = run_job(dsx_path, conn)
        results.append(result)
        print("[DS] " + "-"*60)

    conn.close()

    print(f"\n[DS] ====== PIPELINE SUMMARY ({time.time()-t_total:.1f}s) ======")
    for r in results:
        icon = "OK " if r["status"] == "COMPLETED" else "ERR"
        print(f"[DS]  {icon}  {r['job']:<42} {r['elapsed_s']:>6.1f}s  nodes={r['nodes']}")
        if r["failed"]:
            print(f"[DS]       failed: {r['failed']}")

    ok  = sum(1 for r in results if r["status"] == "COMPLETED")
    err = sum(1 for r in results if r["status"] == "COMPLETED_WITH_ERRORS" and [f for f in r["failed"] if f not in r["skipped"]])
    print(f"[DS] Completed: {ok}/{len(results)}  Errors: {err}")

    summary_path = Path(__file__).parent / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump({"run_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "total_elapsed_s": round(time.time()-t_total, 1),
                   "jobs": results}, f, indent=2)
    print(f"[DS] Summary -> {summary_path}")

    sys.exit(0 if err == 0 else 1)


if __name__ == "__main__":
    main()
