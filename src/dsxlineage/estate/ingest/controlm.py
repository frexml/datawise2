"""Control-M orchestration manifest parser.

``jobs/DATAWISE_PLATFORM.json`` declares the 19 production jobs: tool, artifact,
host, source/target declarations and the ``DependsOn`` DAG. Declarations are the
orchestration team's source of truth and act as an independent oracle for the
artifact parsers (declared vs parsed drift is a feature, not a bug).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dsxlineage.estate.ingest.sqlrefs import normalize_ref
from dsxlineage.estate.ingest.types import Manifest, ManifestJob

_KNOWN_SCHEMAS = (
    "LOAN_SRC|RETAIL_SRC|STAGE|STG|DIM|FACT|MART|ML|BI|AUDIT|DW_AUDIT|DATAWISE"
)
_REF_RE = re.compile(
    rf"\b((?:{_KNOWN_SCHEMAS})\.(?:[A-Za-z_][\w]*|\*))", re.IGNORECASE
)


_BARE_VIEW_RE = re.compile(r"\b(VW_[A-Z0-9_]+)\b", re.IGNORECASE)


def declared_refs(text: str) -> list[str]:
    """Extract table/view references from a prose declaration."""
    text = text or ""
    refs: list[str] = []
    for token in _REF_RE.findall(text):
        if token.endswith(".*"):
            refs.append(token.upper())
            continue
        ref = normalize_ref(token)
        if ref:
            refs.append(ref)
    refs.extend(match.upper() for match in _BARE_VIEW_RE.findall(text))
    return list(dict.fromkeys(refs))


def parse_manifest(path: Path) -> Manifest:
    """Parse a DataWise Control-M manifest into a vendor-neutral :class:`Manifest`."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not payload:
        return Manifest(schedule_fqn="UNKNOWN")

    schedule_name, job_map = next(iter(payload.items()))
    manifest = Manifest(schedule_fqn=str(schedule_name).upper(), source_path=str(path))
    for name, spec in job_map.items():
        depends = [
            str(dep.get("JobName", "")).upper()
            for dep in spec.get("DependsOn", [])
            if dep.get("JobName")
        ]
        manifest.jobs.append(
            ManifestJob(
                name=name,
                job_type=str(spec.get("Type", "Job:Script")),
                tool=str(spec.get("Tool", "")),
                description=str(spec.get("Description", "")),
                artifact=str(spec.get("Artifact", "")),
                host=str(spec.get("Host", "")),
                declared_sources=declared_refs(spec.get("Source", "")),
                declared_targets=declared_refs(spec.get("Target", "")),
                depends_on=depends,
            )
        )
        manifest.depends_on.extend(depends)
    return manifest
