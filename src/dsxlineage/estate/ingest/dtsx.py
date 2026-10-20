"""SSIS ``.dtsx`` package parser.

Packages execute SQL through ``ExecuteSQLTask`` steps whose
``SqlStatementSource`` attribute (namespaced, XML-escaped) carries the real
statements: DDL guards, ``BULK INSERT`` landings, ``INSERT ... SELECT``
promotions and audit logging. We combine every statement in the package and
reduce it to table reads/writes, column lineage and runtime DDL.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from dsxlineage.estate.ingest.columns import lineage_from_write_sql
from dsxlineage.estate.ingest.sqlrefs import extract_table_refs
from dsxlineage.estate.ingest.types import ParsedJob

_DDL_RE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)


def _local_name(qualified: str) -> str:
    return qualified.rsplit("}", 1)[-1]


def _attrs(element: ET.Element, local: str) -> list[str]:
    return [value for key, value in element.attrib.items() if _local_name(key) == local]


def parse_dtsx(path: Path) -> list[ParsedJob]:
    """Parse one SSIS package into one :class:`ParsedJob`."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    statements: list[str] = []
    for element in root.iter():
        for value in _attrs(element, "SqlStatementSource"):
            if value and value.strip():
                statements.append(value)

    package_name = Path(path).stem.upper()
    package_titles = _attrs(root, "ObjectName")
    if package_titles:
        package_name = package_titles[0].upper()

    blob = "\nGO\n".join(statements)
    reads, writes = extract_table_refs(blob)
    ddl = [statement for statement in statements if _DDL_RE.search(statement)]
    return [
        ParsedJob(
            fqn=package_name,
            dialect="ssis",
            artifact=Path(path).name,
            reads=reads,
            writes=writes,
            column_lineage=lineage_from_write_sql(blob, None, None),
            ddl=ddl,
            source_text=blob[:20000],
        )
    ]
