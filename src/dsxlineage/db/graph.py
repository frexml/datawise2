"""Neo4j graph mirror of job/stage/link data — HTTP, not bolt.

Additive to the Postgres-backed API — Postgres remains the system of record.
This module is best-effort: callers should not let a Neo4j failure fail the
job (see worker.py), since the graph is used for lineage/inefficiency
queries, not for anything the frontend depends on directly.

Uses Neo4j's transactional Cypher HTTP endpoint (same interface the Neo4j
Browser itself talks to, enabled by default, no bolt driver needed) rather
than the bolt protocol — bolt needs raw TCP, and internal TCP ingress is
unreliable on this project's Azure Container Apps Environment (see
terraform/modules/apps/main.tf's neo4j resource comment).
"""
import requests
from dsxlineage.core.config import settings


def run_cypher(statements: list[dict]) -> list[list[dict]]:
    """Execute one or more Cypher statements in a single Neo4j transaction.

    statements: [{"cypher": "...", "params": {...}}, ...] — all run and
    committed atomically in one request.
    Returns one list-of-row-dicts (keyed by column name) per statement, in
    the same order. Raises requests.RequestException / RuntimeError on
    failure — every caller already wraps Neo4j calls in a best-effort
    try/except, so this deliberately doesn't add its own.
    """
    url = f"{settings.NEO4J_HTTP_URL}/db/{settings.NEO4J_DATABASE}/tx/commit"
    payload = {
        "statements": [
            {"statement": s["cypher"], "parameters": s.get("params", {})}
            for s in statements
        ]
    }
    resp = requests.post(
        url, json=payload,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Neo4j query error: {body['errors']}")

    return [
        [dict(zip(result["columns"], row["row"])) for row in result["data"]]
        for result in body["results"]
    ]


def sync_job_to_graph(job_id: int, filename: str, stages: list[dict], links: list[dict]) -> None:
    """Mirror a completed job's stages/links into the Neo4j property graph.

    stages: list of {"stage_id", "name", "type"}
    links: list of {"name", "source_stage", "target_stage", "source_pin", "target_pin"}

    All statements run in one Neo4j transaction (one HTTP round trip) rather
    than one bolt session.run() per stage/link — atomic, and avoids paying a
    per-statement HTTP round trip for what used to be a persistent bolt
    connection's per-call cost.
    """
    statements = [{
        "cypher": "MERGE (j:Job {job_id: $job_id}) SET j.filename = $filename",
        "params": {"job_id": job_id, "filename": filename},
    }]
    for stage in stages:
        statements.append({
            "cypher": """
                MERGE (s:Stage {job_id: $job_id, stage_id: $stage_id})
                SET s.name = $name, s.type = $type
                WITH s
                MATCH (j:Job {job_id: $job_id})
                MERGE (j)-[:HAS_STAGE]->(s)
            """,
            "params": {
                "job_id": job_id,
                "stage_id": stage.get("stage_id"),
                "name": stage.get("name"),
                "type": stage.get("type"),
            },
        })
    for link in links:
        # Link.source_stage/target_stage store the stage *name* (see
        # PartnerExtractor._build_edges), not Stage.stage_id — match
        # accordingly, same as the frontend's pin-based fallback logic.
        statements.append({
            "cypher": """
                MATCH (src:Stage {job_id: $job_id, name: $source_stage})
                MATCH (tgt:Stage {job_id: $job_id, name: $target_stage})
                MERGE (src)-[l:LINKS_TO {name: $name}]->(tgt)
                SET l.source_pin = $source_pin, l.target_pin = $target_pin
            """,
            "params": {
                "job_id": job_id,
                "source_stage": link.get("source_stage"),
                "target_stage": link.get("target_stage"),
                "name": link.get("name"),
                "source_pin": link.get("source_pin"),
                "target_pin": link.get("target_pin"),
            },
        })
    run_cypher(statements)
