"""Runtime evidence parser: ``run_summary.json`` from the executed pipeline.

The DataStage and Informatica runners publish per-run summaries (status, elapsed
seconds, failed/skipped steps). They are the only ground truth about what
*actually ran* versus what the artifacts merely declare, so they become
:class:`~dsxlineage.estate.ir.RuntimeEvidence` facts on the IR.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from dsxlineage.estate.ir import RuntimeEvidence

logger = logging.getLogger(__name__)

RUN_SUMMARY_PATHS: tuple[str, ...] = (
    "docker-deploy/datastage/run_summary.json",
    "docker-deploy/informatica/run_summary.json",
)


def parse_run_summaries(root: Path) -> list[RuntimeEvidence]:
    """Read every vendored run summary into ``RuntimeEvidence`` rows.

    Job names are kept as published by the runner; :mod:`build` remaps them to
    manifest job FQNs where the artifact stem matches.
    """
    root = Path(root)
    evidence: list[RuntimeEvidence] = []
    for rel in RUN_SUMMARY_PATHS:
        path = root / rel
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("run summary unreadable: %s (%s)", rel, exc)
            continue
        ran_on = str(payload.get("run_time") or "") or None
        mode = str(payload.get("mode") or "") or None
        for job in payload.get("jobs", []):
            name = str(job.get("job") or "").upper()
            if not name:
                continue
            evidence.append(
                RuntimeEvidence(
                    job_fqn=name,
                    artifact=rel,
                    status=str(job.get("status") or "UNKNOWN").upper(),
                    ran_on=ran_on,
                    elapsed_s=float(job["elapsed_s"]) if job.get("elapsed_s") is not None else None,
                    failed_steps=[str(step) for step in job.get("failed", [])],
                    skipped_steps=[str(step) for step in job.get("skipped", [])],
                    mode=mode,
                )
            )
    return evidence


def map_to_jobs(
    evidence: list[RuntimeEvidence],
    artifact_to_job: dict[str, str],
) -> list[RuntimeEvidence]:
    """Remap raw runner job names to IR job FQNs via artifact-stem lookup.

    ``artifact_to_job`` maps ``ARTIFACT_STEM -> IR FQN`` (e.g.
    ``FACT_GL_BUILD -> STAGE4_DS_FACT_GL_BUILD``). Names that match already or
    have no mapping are left untouched so nothing is silently dropped.
    """
    for row in evidence:
        mapped = artifact_to_job.get(row.job_fqn.upper())
        if mapped:
            row.job_fqn = mapped
    return evidence
