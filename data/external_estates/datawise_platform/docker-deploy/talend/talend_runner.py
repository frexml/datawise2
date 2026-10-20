"""
talend_runner.py
Simulates Talend Open Studio job execution by:
1. Parsing FACT_PAYMENT_LOAD.item XML
2. Building the multidirectional execution graph from <connection> elements
3. Executing each SQL node in topological order against Azure SQL
"""

import os, sys, time, xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dotenv import load_dotenv
import pyodbc

load_dotenv()

ITEM_PATH = os.getenv("TALEND_ITEM", os.path.join(os.path.dirname(__file__), "FACT_PAYMENT_LOAD.item"))
JOB_NAME  = os.getenv("TALEND_JOB",  "FACT_PAYMENT_LOAD")

SERVER   = os.getenv("SQL_SERVER")
DATABASE = os.getenv("SQL_DATABASE")
USERNAME = os.getenv("SQL_USERNAME")
PASSWORD = os.getenv("SQL_PASSWORD")

CONN_STR = (
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={SERVER};"
    f"DATABASE={DATABASE};"
    f"UID={USERNAME};"
    f"PWD={PASSWORD};"
    f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
)


def log(msg):
    print(f"[TALEND][{JOB_NAME}] {msg}", flush=True)


def parse_item(path):
    """Parse .item XML — extract nodes and connections."""
    tree = ET.parse(path)
    root = tree.getroot()

    nodes = {}   # unique_name -> {label, sql}
    graph = defaultdict(list)   # source -> [targets]
    in_degree = defaultdict(int)

    for elem in root.iter():
        tag = elem.tag.split("}")[-1]

        if tag == "node":
            params = {p.get("name"): p.get("value") for p in elem.iter() if p.tag.split("}")[-1] == "elementParameter"}
            name  = params.get("UNIQUE_NAME", "")
            label = params.get("LABEL", name)
            sql   = params.get("QUERY", "")
            # strip wrapping &quot; added by Talend (already decoded by XML parser as ")
            sql = sql.strip().strip('"')
            if name:
                nodes[name] = {"label": label, "sql": sql}
                in_degree[name] = in_degree.get(name, 0)

        if tag == "connection":
            src = elem.get("source")
            tgt = elem.get("target")
            if src and tgt and src in nodes or tgt:
                graph[src].append(tgt)
                in_degree[tgt] = in_degree.get(tgt, 0) + 1
                if src not in in_degree:
                    in_degree[src] = 0

    return nodes, graph, in_degree


def topological_order(nodes, graph, in_degree):
    """Kahn's algorithm — respects multidirectional fan-out/converge."""
    queue = deque([n for n in nodes if in_degree.get(n, 0) == 0])
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbour in graph.get(node, []):
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)
    return order


def execute_sql(conn, node_name, label, sql):
    """Execute a SQL block — handles multi-statement batches."""
    if not sql.strip():
        log(f"  SKIP {label} — no SQL")
        return True

    log(f"  EXEC [{label}]")
    t0 = time.time()
    try:
        cursor = conn.cursor()
        # split on GO if present, otherwise execute as single batch
        statements = [s.strip() for s in sql.split("\nGO\n") if s.strip()]
        if not statements:
            statements = [sql]
        for stmt in statements:
            cursor.execute(stmt)
        conn.commit()
        elapsed = time.time() - t0
        log(f"  OK   [{label}] {elapsed:.2f}s")
        return True
    except pyodbc.Error as e:
        log(f"  FAIL [{label}] {e}")
        conn.rollback()
        return False


def run():
    log(f"Starting job: {JOB_NAME}")
    log(f"Item file:    {ITEM_PATH}")
    log(f"Target DB:    {SERVER}/{DATABASE}")

    # parse item
    log("Parsing .item XML...")
    nodes, graph, in_degree = parse_item(ITEM_PATH)
    log(f"Nodes found: {len(nodes)}")

    # build execution order
    order = topological_order(nodes, graph, dict(in_degree))
    log(f"Execution order ({len(order)} nodes):")
    for i, n in enumerate(order, 1):
        log(f"  {i:02d}. {nodes[n]['label']}")

    # connect to Azure SQL
    log("Connecting to Azure SQL...")
    try:
        conn = pyodbc.connect(CONN_STR, autocommit=False)
        log("Connected.")
    except pyodbc.Error as e:
        log(f"CONNECTION FAILED: {e}")
        sys.exit(1)

    # execute nodes in order
    failed = []
    t_start = time.time()
    for node_name in order:
        node = nodes[node_name]
        success = execute_sql(conn, node_name, node["label"], node["sql"])
        if not success:
            failed.append(node["label"])
            # only stop on critical nodes — audit/DQ failures are non-fatal
            if not any(x in node["label"] for x in ["DQ", "ROWCOUNT", "AUDIT_END"]):
                log(f"FATAL: {node['label']} failed — aborting pipeline")
                break

    conn.close()
    elapsed = time.time() - t_start

    if failed:
        log(f"Job COMPLETED WITH ERRORS in {elapsed:.1f}s — failed: {failed}")
        sys.exit(1)
    else:
        log(f"Job COMPLETED SUCCESSFULLY in {elapsed:.1f}s")
        sys.exit(0)


if __name__ == "__main__":
    run()
