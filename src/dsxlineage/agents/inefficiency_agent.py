"""Inefficiency detection over the Neo4j graph mirror.

Per DataWise's documented design, inefficiency detection runs as Cypher
pattern-matches against the property graph, not as a code-level static
analyzer. Runs after db.graph.sync_job_to_graph has mirrored a job's
stages/links into Neo4j — see worker.py.

DataStage transform logic lives in embedded C++ (TrxGenCode), not SQL, so the
patterns here are structural (graph-topology) rather than the SQL-regex
patterns (redundant JOIN/aggregation) used by SQL-dialect ETL tools — the
honest signal available from the current graph schema (Job/Stage/LINKS_TO).
"""
from dsxlineage.db.graph import get_driver
from dsxlineage.core.config import settings

# Demo-scale thresholds — tune per estate in config/dialects/inefficiency_thresholds.yaml
# once this graduates beyond prototype scope.
_HIGH_FANOUT_THRESHOLD = 4
_LONG_CHAIN_THRESHOLD = 6
_REPEATED_STAGE_TYPE_THRESHOLD = 3

_QUERIES = [
    {
        "pattern_type": "high_fan_in_out",
        "severity": "medium",
        "cypher": """
            MATCH (j:Job {job_id: $job_id})-[:HAS_STAGE]->(s:Stage)
            OPTIONAL MATCH (s)<-[in:LINKS_TO]-()
            OPTIONAL MATCH (s)-[out:LINKS_TO]->()
            WITH s, count(DISTINCT in) AS in_count, count(DISTINCT out) AS out_count
            WHERE in_count > $threshold OR out_count > $threshold
            RETURN s.name AS name, in_count, out_count
        """,
        "params": {"threshold": _HIGH_FANOUT_THRESHOLD},
        "describe": lambda r: (
            f"Stage '{r['name']}' has unusually high connectivity "
            f"({r['in_count']} inbound / {r['out_count']} outbound links) — "
            f"candidate bottleneck or over-consolidated stage."
        ),
        # Static — this pattern type recurring across jobs is itself the
        # cross-job signal, no finer sub-type available from this query.
        "signature": lambda r: "high_fan_in_out",
    },
    {
        "pattern_type": "long_derivation_chain",
        "severity": "medium",
        "cypher": """
            MATCH (src:Stage {job_id: $job_id}) WHERE NOT ()-[:LINKS_TO]->(src)
            MATCH path = (src)-[:LINKS_TO*1..20]->(sink:Stage)
            WHERE NOT (sink)-[:LINKS_TO]->(:Stage)
            WITH path, length(path) AS hops
            WHERE hops > $threshold
            RETURN hops, [n IN nodes(path) | n.name] AS chain
            ORDER BY hops DESC
            LIMIT 5
        """,
        "params": {"threshold": _LONG_CHAIN_THRESHOLD},
        "describe": lambda r: (
            f"Derivation chain of {r['hops']} hops: {' → '.join(r['chain'])} — "
            f"consider simplifying or breaking into intermediate outputs."
        ),
        "signature": lambda r: "long_derivation_chain",
    },
    {
        "pattern_type": "orphan_stage",
        "severity": "low",
        "cypher": """
            MATCH (j:Job {job_id: $job_id})-[:HAS_STAGE]->(s:Stage)
            WHERE NOT (s)-[:LINKS_TO]-()
            RETURN s.name AS name
        """,
        "params": {},
        "describe": lambda r: (
            f"Stage '{r['name']}' has no inbound or outbound links — "
            f"likely dead/unused, safe to remove after confirmation."
        ),
        "signature": lambda r: "orphan_stage",
    },
    {
        "pattern_type": "repeated_stage_type",
        "severity": "info",
        "cypher": """
            MATCH (j:Job {job_id: $job_id})-[:HAS_STAGE]->(s:Stage)
            WITH s.type AS type, collect(s.name) AS stages, count(*) AS cnt
            WHERE cnt > $threshold
            RETURN type, stages, cnt
        """,
        "params": {"threshold": _REPEATED_STAGE_TYPE_THRESHOLD},
        "describe": lambda r: (
            f"{r['cnt']} stages of type '{r['type']}' in this job "
            f"({', '.join(r['stages'][:5])}{'…' if len(r['stages']) > 5 else ''}) — "
            f"review for consolidation opportunity."
        ),
        # Sub-typed by the actual recurring stage type (e.g. "PxJoin") —
        # this is the one pattern with a natural cross-job-comparable key,
        # matching the docs' "same join/aggregation appears in N+ jobs".
        "signature": lambda r: f"repeated_stage_type:{r['type']}",
    },
]


def detect_inefficiencies(job_id: int) -> list[dict]:
    """Run all inefficiency queries for a job, persist findings to Neo4j, return them."""
    driver = get_driver()
    findings = []
    with driver.session(database=settings.NEO4J_DATABASE) as session:
        for q in _QUERIES:
            params = {"job_id": job_id, **q["params"]}
            for record in session.run(q["cypher"], **params):
                findings.append({
                    "pattern_type": q["pattern_type"],
                    "severity": q["severity"],
                    "description": q["describe"](record),
                    "signature": q["signature"](record),
                })

        for finding in findings:
            session.run(
                """
                MERGE (p:InefficiencyPattern {
                    job_id: $job_id,
                    pattern_type: $pattern_type,
                    description: $description
                })
                SET p.severity = $severity, p.signature = $signature
                WITH p
                MATCH (j:Job {job_id: $job_id})
                MERGE (j)-[:HAS_PATTERN]->(p)
                """,
                job_id=job_id,
                pattern_type=finding["pattern_type"],
                description=finding["description"],
                severity=finding["severity"],
                signature=finding["signature"],
            )

    return findings
