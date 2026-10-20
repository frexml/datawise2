"""Informatica PowerCenter POWERMART XML parser.

Each export contains ``SOURCE`` definitions (``SQ_`` prefixed, carrying the real
``OWNERNAME``), ``TARGET`` definitions, a ``Source Qualifier`` transformation
whose ``Sql Query`` table attribute carries the extraction SQL, and the mapping
connectors. We reduce the export to one ETL job: sources -> target, plus column
lineage from the Source Qualifier projection.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from dsxlineage.estate.ingest.columns import lineage_from_select_sql
from dsxlineage.estate.ingest.sqlrefs import extract_table_refs, normalize_ref
from dsxlineage.estate.ingest.types import ParsedJob

_PREFIX_RE = re.compile(r"^(?:SQ_|TGT_|SRC_)", re.IGNORECASE)


def _object_fqn(owner: str | None, name: str | None) -> str | None:
    """Build the physical FQN from OWNERNAME + logical definition name."""
    if not name:
        return None
    logical = _PREFIX_RE.sub("", name).upper()
    if owner:
        return f"{owner.upper()}.{logical}"
    return normalize_ref(logical)


def parse_powermart(path: Path) -> list[ParsedJob]:
    """Parse one POWERMART export into a single :class:`ParsedJob`."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    job_name = Path(path).stem.upper()

    reads: list[str] = []
    writes: list[str] = []
    for source in root.iter("SOURCE"):
        fqn = _object_fqn(source.get("OWNERNAME"), source.get("NAME"))
        if fqn:
            reads.append(fqn)
    for target in root.iter("TARGET"):
        fqn = _object_fqn(target.get("OWNERNAME"), target.get("NAME"))
        if fqn:
            writes.append(fqn)

    projection_sql = ""
    for transformation in root.iter("TRANSFORMATION"):
        for attribute in transformation.findall("TABLEATTRIBUTE"):
            if attribute.get("NAME", "").lower() == "sql query":
                projection_sql = attribute.get("VALUE", "") or ""
    if projection_sql:
        sql_reads, sql_writes = extract_table_refs(projection_sql)
        reads.extend(sql_reads)
        writes.extend(sql_writes)

    target_fqn = writes[0] if writes else None
    column_lineage = (
        lineage_from_select_sql(projection_sql, target_fqn, None) if target_fqn and projection_sql else []
    )

    return [
        ParsedJob(
            fqn=job_name,
            dialect="informatica",
            artifact=Path(path).name,
            reads=list(dict.fromkeys(reads)),
            writes=list(dict.fromkeys(writes)),
            column_lineage=column_lineage,
            source_text=(projection_sql or text)[:20000],
        )
    ]
