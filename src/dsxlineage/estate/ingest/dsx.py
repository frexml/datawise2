"""IBM DataStage ``.dsx`` job parser (truncation-tolerant).

DSX exports are XML: each ``<Record>`` is a transformation stage with
``<Property>`` children (``sql_query`` CDATA, ``table_name``, ``write_mode``,
``derivations``, ``routes`` ...). Four of the ten published artifacts are
truncated before their closing ``</Job></DSExport>`` tags, so this parser uses a
tolerant regex scan that still recovers every complete ``<Record>`` block.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from dsxlineage.estate.ingest.columns import lineage_from_write_sql
from dsxlineage.estate.ingest.sqlrefs import extract_table_refs, normalize_ref
from dsxlineage.estate.ingest.types import ParsedJob

_DDL_RE = re.compile(r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE)\b", re.IGNORECASE)
_AUDIT_TABLES = frozenset({"DW_AUDIT.ETL_BATCH_LOG"})
_JOB_RE = re.compile(r"<Job\b([^>]*)>(.*?)(?=</Job>|<Job\b|\Z)", re.DOTALL)
_RECORD_RE = re.compile(r"<Record\b([^>]*)>(.*?)</Record>", re.DOTALL)
_IDENTIFIER_RE = re.compile(r'Identifier="([^"]+)"')
_PROPERTY_RE = re.compile(r'<Property\s+Name="([^"]+)"\s*>(.*?)</Property>', re.DOTALL)
_LINK_RE = re.compile(r"<Link\b([^>]*)/>")
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _clean(text: str) -> str:
    value = text.strip()
    if value.startswith("<![CDATA["):
        value = value[len("<![CDATA[") :]
    if value.endswith("]]>"):
        value = value[: -len("]]>")]
    return html.unescape(value).strip()


def _properties(body: str) -> dict[str, str]:
    return {match.group(1): _clean(match.group(2)) for match in _PROPERTY_RE.finditer(body)}


def _reject_targets(job_body: str, stage_tables: dict[str, str]) -> list[tuple[str, str]]:
    """Follow ``Route="REJECT"`` links to the stage that writes a reject table."""
    links: list[dict[str, str]] = [
        dict(_ATTR_RE.findall(attrs)) for attrs in _LINK_RE.findall(job_body)
    ]
    by_source: dict[str, list[dict[str, str]]] = {}
    for link in links:
        by_source.setdefault(link.get("SourceStage", ""), []).append(link)
    found: list[tuple[str, str]] = []
    for link in links:
        route = link.get("Route", "")
        if "REJECT" not in route.upper():
            continue
        pending = [link.get("TargetStage", "")]
        seen: set[str] = set()
        while pending:
            stage = pending.pop(0)
            if not stage or stage in seen:
                continue
            seen.add(stage)
            table = stage_tables.get(stage)
            if table:
                found.append((table, route.upper()))
                break
            pending.extend(nxt.get("TargetStage", "") for nxt in by_source.get(stage, []))
    return list(dict.fromkeys(found))


def parse_dsx(path: Path) -> list[ParsedJob]:
    """Parse one DSX export into one :class:`ParsedJob` per ``<Job>``."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    jobs: list[ParsedJob] = []
    for job_match in _JOB_RE.finditer(text):
        identifier = _IDENTIFIER_RE.search(job_match.group(1))
        job_name = (identifier.group(1) if identifier else Path(path).stem).upper()
        reads: list[str] = []
        writes: list[str] = []
        ddl: list[str] = []
        sql_snippets: list[str] = []
        lineage = []
        stage_tables: dict[str, str] = {}
        for record_match in _RECORD_RE.finditer(job_match.group(2)):
            identifier = _IDENTIFIER_RE.search(record_match.group(1))
            props = _properties(record_match.group(2))
            sql = props.get("sql_query", "")
            table_name = normalize_ref(props.get("table_name"))
            if identifier and table_name:
                stage_tables.setdefault(identifier.group(1), table_name)
            if sql:
                sql_snippets.append(sql)
                if _DDL_RE.search(sql) and not re.search(r"\bINSERT\b|\bMERGE\b", sql, re.IGNORECASE):
                    ddl.append(sql)
                statement_reads, statement_writes = extract_table_refs(sql)
                reads.extend(statement_reads)
                writes.extend(statement_writes)
                lineage.extend(lineage_from_write_sql(sql, None, None))
            if table_name and (props.get("write_mode") or table_name not in reads):
                writes.append(table_name)
        jobs.append(
            ParsedJob(
                fqn=job_name,
                dialect="datastage",
                artifact=Path(path).name,
                reads=[ref for ref in dict.fromkeys(reads) if ref not in _AUDIT_TABLES],
                writes=list(dict.fromkeys(writes)),
                column_lineage=lineage,
                ddl=ddl,
                reject_targets=_reject_targets(job_match.group(2), stage_tables),
                source_text="\n".join(sql_snippets)[:20000],
            )
        )
    return jobs
