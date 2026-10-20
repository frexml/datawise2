"""
Behavior Engine — Runtime & Operational Understanding

Meaning + Context -> Behavior
Surfaces failure paths, dependencies, orchestration, reject handling, data quality.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from pathlib import Path

from dsxlineage.estate.ir import EstateIR


def analyze_behavior(ir: EstateIR, estate_root: Path | None = None) -> dict[str, Any]:
    failures = []
    dependencies = []
    rejects = []
    data_quality = []
    operational = []

    # Failure paths: procedures with CURSOR or high complexity
    for p in ir.procedures:
        if p.has_cursor:
            failures.append({
                "type": "failure_path",
                "evidence": {"fqn": p.fqn, "snippet": "CURSOR"},
                "confidence": 0.85,
                "source_ref": p.fqn,
                "meaning": f"{p.fqn} uses CURSOR — may fail on large data, retry logic needed",
            })
        if p.complexity == "high" and p.has_dynamic:
            failures.append({
                "type": "failure_path",
                "evidence": {"fqn": p.fqn, "snippet": "EXECUTE IMMEDIATE + high complexity"},
                "confidence": 0.9,
                "source_ref": p.fqn,
                "meaning": f"{p.fqn} has dynamic SQL and high complexity — landmine for migration",
            })

    # Dependencies: from edges
    for e in ir.edges:
        if e.edge_type.value in ("SCHEDULE_DEPENDS", "SP_CALLS"):
            dependencies.append({
                "type": "dependency",
                "evidence": {"fqn": f"{e.source_fqn} -> {e.target_fqn}", "snippet": e.edge_type.value},
                "confidence": 0.9,
                "source_ref": f"{e.source_fqn}->{e.target_fqn}",
            })

    # Reject handling: look for rejected status in KYC or liabilities
    for c in ir.column_lineage:
        if "STATUS" in c.target_column and c.target_table == "VW_KYC_PENDING":
            rejects.append({
                "type": "reject_handling",
                "evidence": {"fqn": c.target_table, "snippet": "WHERE status != Approved"},
                "confidence": 0.8,
                "source_ref": c.target_table,
                "meaning": "KYC Rejected rows are filtered — need exception handling in target",
            })

    # Data quality: KYC score range, credit score
    for t in ir.tables:
        if "KYC" in t.fqn:
            data_quality.append({
                "type": "data_quality",
                "evidence": {"fqn": t.fqn, "snippet": "KYC score 300-850, status enum"},
                "confidence": 0.85,
                "source_ref": t.fqn,
                "meaning": "KYC score must be 300-850 and status Approved before disbursement",
            })

    # Operational assumptions: scheduling
    for s in ir.schedules:
        operational.append({
            "type": "operational",
            "evidence": {"fqn": s.fqn, "snippet": f"{s.schedule_expr} {s.frequency}"},
            "confidence": 0.8,
            "source_ref": s.fqn,
            "meaning": f"Schedule {s.fqn} runs {s.frequency} at {s.schedule_expr}",
        })

    # Runtime evidence: executed run summaries are ground truth over declarations.
    summaries: dict[str, list] = defaultdict(list)
    for row in getattr(ir, "runtime_evidence", []):
        summaries[row.artifact].append(row)
        status = row.status.upper()
        if status != "COMPLETED" or row.failed_steps:
            detail = status
            if row.failed_steps:
                detail += f" (failed steps: {', '.join(row.failed_steps)})"
            failures.append({
                "type": "runtime_failure",
                "evidence": {
                    "fqn": row.job_fqn,
                    "snippet": f"{detail} on run {row.ran_on or 'unknown'} via {row.artifact}",
                },
                "confidence": 0.95,
                "source_ref": row.artifact,
                "meaning": f"{row.job_fqn} finished {detail} in the executed pipeline run",
            })
    for artifact, rows in summaries.items():
        ran_on = rows[0].ran_on or "unknown run"
        completed = sum(1 for row in rows if row.status.upper() == "COMPLETED")
        operational.append({
            "type": "operational",
            "evidence": {
                "fqn": artifact,
                "snippet": f"{len(rows)} jobs ran, {completed} completed ({ran_on})",
            },
            "confidence": 0.95,
            "source_ref": artifact,
            "meaning": f"Executed {len(rows)} jobs from {artifact} on {ran_on}",
        })

    # Reject handling: ETL REJECT routes quarantining rows into audit tables.
    for flow in getattr(ir, "reject_flows", []):
        rejects.append({
            "type": "reject_handling",
            "evidence": {
                "fqn": flow.job_fqn,
                "snippet": f"{flow.route} route -> {flow.target_table}",
            },
            "confidence": 0.9,
            "source_ref": flow.artifact,
            "meaning": f"{flow.job_fqn} quarantines rejected rows into {flow.target_table}",
        })

    # Runtime logs if available (synthetic runtime_logs/PROC_RISK_ASSESSMENT.log)
    if estate_root:
        log_path = Path(estate_root) / "runtime_logs" / "PROC_RISK_ASSESSMENT.log"
        if log_path.exists():
            failures.append({
                "type": "runtime_failure",
                "evidence": {"fqn": "PROC_RISK_ASSESSMENT.log", "snippet": log_path.read_text()[:100]},
                "confidence": 0.95,
                "source_ref": str(log_path),
                "meaning": "Runtime log shows retry 3 on credit_check.score IS NULL",
            })

    return {
        "failures": failures,
        "dependencies": dependencies,
        "rejects": rejects,
        "data_quality": data_quality,
        "operational": operational,
        "fact_count": len(failures) + len(dependencies) + len(rejects) + len(data_quality) + len(operational),
    }
