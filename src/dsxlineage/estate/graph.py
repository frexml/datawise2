"""
Estate Knowledge Graph sync - Neo4j (HTTP) + Postgres dual store.

Neo4j remains the graph-native query engine (blast radius, paths).
Postgres (estates + ledger) is the system of record. Sync is best-effort:
graph failure never fails estate creation (same guard as legacy worker).
"""

from __future__ import annotations

from typing import List

from dsxlineage.core.config import settings
from dsxlineage.db.graph import run_cypher as _run_cypher
from dsxlineage.estate.ir import EstateIR


def sync_estate_to_graph(estate_id: int, estate_name: str, ir: EstateIR) -> None:
    """Mirror EstateIR nodes + edges into Neo4j in one transaction.

    Label set per Plan v2 §5: System, Table, View, Procedure, ETLJob,
    Schedule, Dashboard, ColumnIdentity.
    """
    statements: list[dict] = []

    # Estate node
    statements.append({
        "cypher": "MERGE (e:Estate {estate_id: $estate_id}) SET e.name = $name, e.version = $version",
        "params": {"estate_id": estate_id, "name": estate_name, "version": ir.version},
    })

    # Systems
    for sys_name in ir.systems:
        statements.append({
            "cypher": "MERGE (s:System {estate_id: $estate_id, fqn: $fqn}) SET s.name = $name WITH s MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_SYSTEM]->(s)",
            "params": {"estate_id": estate_id, "fqn": sys_name, "name": sys_name},
        })

    # Tables
    for t in ir.tables:
        statements.append({
            "cypher": """
                MERGE (n:Table {estate_id: $estate_id, fqn: $fqn})
                SET n.system = $system, n.column_count = $col_count
                WITH n MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_TABLE]->(n)
            """,
            "params": {"estate_id": estate_id, "fqn": t.fqn, "system": t.system, "col_count": len(t.columns)},
        })

    # Views
    for v in ir.views:
        statements.append({
            "cypher": """
                MERGE (n:View {estate_id: $estate_id, fqn: $fqn})
                SET n.complexity = $complexity, n.unresolved = $unresolved
                WITH n MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_VIEW]->(n)
            """,
            "params": {"estate_id": estate_id, "fqn": v.fqn, "complexity": v.complexity, "unresolved": v.unresolved},
        })

    # Procedures
    for p in ir.procedures:
        statements.append({
            "cypher": """
                MERGE (n:Procedure {estate_id: $estate_id, fqn: $fqn})
                SET n.language = $lang, n.complexity = $complexity, n.has_dynamic = $has_dynamic, n.has_cursor = $has_cursor
                WITH n MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_PROCEDURE]->(n)
            """,
            "params": {"estate_id": estate_id, "fqn": p.fqn, "lang": p.language, "complexity": p.complexity, "has_dynamic": p.has_dynamic, "has_cursor": p.has_cursor},
        })

    # ETL Jobs
    for j in ir.etl_jobs:
        statements.append({
            "cypher": """
                MERGE (n:ETLJob {estate_id: $estate_id, fqn: $fqn})
                SET n.dialect = $dialect, n.source_fqn = $src, n.target_fqn = $tgt
                WITH n MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_ETL_JOB]->(n)
            """,
            "params": {"estate_id": estate_id, "fqn": j.fqn, "dialect": j.dialect, "src": j.source_fqn, "tgt": j.target_fqn},
        })

    # Schedules
    for s in ir.schedules:
        statements.append({
            "cypher": """
                MERGE (n:Schedule {estate_id: $estate_id, fqn: $fqn})
                SET n.scheduler = $scheduler, n.frequency = $freq
                WITH n MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_SCHEDULE]->(n)
            """,
            "params": {"estate_id": estate_id, "fqn": s.fqn, "scheduler": s.scheduler, "freq": s.frequency},
        })

    # Dashboards
    for d in ir.dashboards:
        statements.append({
            "cypher": """
                MERGE (n:Dashboard {estate_id: $estate_id, fqn: $fqn})
                SET n.tool = $tool, n.source_fqn = $src
                WITH n MATCH (e:Estate {estate_id: $estate_id}) MERGE (e)-[:HAS_DASHBOARD]->(n)
            """,
            "params": {"estate_id": estate_id, "fqn": d.fqn, "tool": d.tool, "src": d.source_fqn},
        })

    # Edges - typed relationship per edge_type
    rel_map = {
        "VIEW_DEPENDS": "VIEW_DEPENDS_ON",
        "SP_READS": "SP_READS",
        "SP_WRITES": "SP_WRITES",
        "SP_CALLS": "SP_CALLS",
        "ETL_READS": "ETL_READS",
        "ETL_WRITES": "ETL_WRITES",
        "SCHEDULE_DEPENDS": "SCHEDULE_DEPENDS_ON",
        "DASHBOARD_RENDERS": "DASHBOARD_RENDERS",
        "UNRESOLVED": "UNRESOLVED",
    }
    # Edges need to match any label - use generic Node match by fqn + estate_id
    for e in ir.edges:
        rel = rel_map.get(e.edge_type.value, "DEPENDS_ON")
        statements.append({
            "cypher": f"""
                MATCH (src {{estate_id: $estate_id, fqn: $source_fqn}})
                MATCH (tgt {{estate_id: $estate_id, fqn: $target_fqn}})
                MERGE (src)-[r:{rel} {{estate_id: $estate_id}}]->(tgt)
                SET r.confidence = $confidence, r.unresolved = $unresolved
            """,
            "params": {
                "estate_id": estate_id,
                "source_fqn": e.source_fqn,
                "target_fqn": e.target_fqn,
                "confidence": e.confidence,
                "unresolved": e.unresolved,
            },
        })

    if not statements:
        return
    _run_cypher(statements)


def lineage_query(estate_id: int, fqn: str, direction: str = "both", max_hops: int = 6) -> list[dict]:
    """Run lineage traversal Cypher for a given FQN. Best-effort."""
    if direction == "upstream":
        cypher = """
            MATCH (n {estate_id: $estate_id, fqn: $fqn})
            MATCH path = (upstream)-[*1..%d]->(n)
            RETURN upstream.fqn AS fqn, labels(upstream)[0] AS label, length(path) AS hops
            LIMIT 100
        """ % max_hops
    elif direction == "downstream":
        cypher = """
            MATCH (n {estate_id: $estate_id, fqn: $fqn})
            MATCH path = (n)-[*1..%d]->(downstream)
            RETURN downstream.fqn AS fqn, labels(downstream)[0] AS label, length(path) AS hops
            LIMIT 100
        """ % max_hops
    else:
        cypher = """
            MATCH (n {estate_id: $estate_id, fqn: $fqn})
            OPTIONAL MATCH up_path = (upstream)-[*1..%d]->(n)
            OPTIONAL MATCH down_path = (n)-[*1..%d]->(downstream)
            WITH collect(DISTINCT {fqn: upstream.fqn, label: labels(upstream)[0], hops: length(up_path), dir: 'upstream'}) AS ups,
                 collect(DISTINCT {fqn: downstream.fqn, label: labels(downstream)[0], hops: length(down_path), dir: 'downstream'}) AS downs
            UNWIND (ups + downs) AS node
            RETURN node.fqn AS fqn, node.label AS label, node.hops AS hops, node.dir AS dir
            LIMIT 100
        """ % (max_hops, max_hops)
    try:
        rows = _run_cypher([{"cypher": cypher, "params": {"estate_id": estate_id, "fqn": fqn}}])[0]
        return rows
    except Exception as exc:
        print(f"Warning: lineage_query failed for estate {estate_id} fqn {fqn}: {exc}")
        return []


def blast_radius(estate_id: int, fqn: str, max_hops: int = 20) -> list[dict]:
    """All downstream nodes reachable from fqn (impact analysis)."""
    cypher = """
        MATCH (n {estate_id: $estate_id, fqn: $fqn})
        MATCH path = (n)-[*1..%d]->(downstream)
        RETURN downstream.fqn AS fqn, labels(downstream)[0] AS label, length(path) AS hops, [x IN nodes(path) | x.fqn] AS path
        ORDER BY hops ASC
        LIMIT 200
    """ % max_hops
    try:
        return _run_cypher([{"cypher": cypher, "params": {"estate_id": estate_id, "fqn": fqn}}])[0]
    except Exception as exc:
        print(f"Warning: blast_radius failed: {exc}")
        return []
