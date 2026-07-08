"""Neo4j graph mirror of job/stage/link data.

Additive to the Postgres-backed API — Postgres remains the system of record.
This module is best-effort: callers should not let a Neo4j failure fail the
job (see worker.py), since the graph is used for lineage/inefficiency
queries, not for anything the frontend depends on directly.
"""
from neo4j import GraphDatabase
from dsxlineage.core.config import settings

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


def sync_job_to_graph(job_id: int, filename: str, stages: list[dict], links: list[dict]) -> None:
    """Mirror a completed job's stages/links into the Neo4j property graph.

    stages: list of {"stage_id", "name", "type"}
    links: list of {"name", "source_stage", "target_stage", "source_pin", "target_pin"}
    """
    driver = get_driver()
    with driver.session(database=settings.NEO4J_DATABASE) as session:
        session.run(
            "MERGE (j:Job {job_id: $job_id}) SET j.filename = $filename",
            job_id=job_id, filename=filename,
        )
        for stage in stages:
            session.run(
                """
                MERGE (s:Stage {job_id: $job_id, stage_id: $stage_id})
                SET s.name = $name, s.type = $type
                WITH s
                MATCH (j:Job {job_id: $job_id})
                MERGE (j)-[:HAS_STAGE]->(s)
                """,
                job_id=job_id,
                stage_id=stage.get("stage_id"),
                name=stage.get("name"),
                type=stage.get("type"),
            )
        for link in links:
            # Link.source_stage/target_stage store the stage *name* (see
            # PartnerExtractor._build_edges), not Stage.stage_id — match
            # accordingly, same as the frontend's pin-based fallback logic.
            session.run(
                """
                MATCH (src:Stage {job_id: $job_id, name: $source_stage})
                MATCH (tgt:Stage {job_id: $job_id, name: $target_stage})
                MERGE (src)-[l:LINKS_TO {name: $name}]->(tgt)
                SET l.source_pin = $source_pin, l.target_pin = $target_pin
                """,
                job_id=job_id,
                source_stage=link.get("source_stage"),
                target_stage=link.get("target_stage"),
                name=link.get("name"),
                source_pin=link.get("source_pin"),
                target_pin=link.get("target_pin"),
            )
