"""Talend Open Studio ``.item`` job parser.

Talend exports are XMI XML. Database work happens in ``tMSSqlRow`` components
whose ``QUERY`` parameter is a Java string literal (XML-escaped, newline-escaped)
containing the real T-SQL. We extract every query, reduce to reads/writes and
INSERT/MERGE column lineage, and keep runtime DDL for table registration.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from dsxlineage.estate.ingest.columns import lineage_from_write_sql
from dsxlineage.estate.ingest.sqlrefs import extract_table_refs
from dsxlineage.estate.ingest.types import ParsedJob

_QUERY_RE = re.compile(r'name="QUERY"\s+value="([^"]*)"', re.DOTALL)
_DDL_RE = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)


def _unescape_java_literal(raw: str) -> str:
    """Decode an XML-escaped Talend Java string literal into plain SQL."""
    value = html.unescape(raw)
    value = value.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.strip()


def parse_talend(path: Path) -> list[ParsedJob]:
    """Parse one Talend job item into a single :class:`ParsedJob`."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    statements = [_unescape_java_literal(match.group(1)) for match in _QUERY_RE.finditer(text)]
    statements = [statement for statement in statements if statement]
    blob = "\nGO\n".join(statements)
    reads, writes = extract_table_refs(blob)
    ddl = [statement for statement in statements if _DDL_RE.search(statement)]
    return [
        ParsedJob(
            fqn=Path(path).stem.upper(),
            dialect="talend",
            artifact=Path(path).name,
            reads=reads,
            writes=writes,
            column_lineage=lineage_from_write_sql(blob, None, None),
            ddl=ddl,
            source_text=blob[:20000],
        )
    ]
