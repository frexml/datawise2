"""Multi-object T-SQL file parser (Azure SQL / T-SQL dialect).

The external estate ships 15 DDL scripts with several objects per file, markdown
fences, ``GO`` batch separators and - in the published repository - several files
truncated mid-statement. This parser:

* strips markdown fences and BOMs,
* splits batches on ``GO`` and statements on top-level semicolons,
* extracts every ``CREATE TABLE`` / ``CREATE VIEW`` / ``CREATE PROCEDURE``,
* flags truncated artifacts (``unresolved``) instead of failing the whole file.

FQN convention throughout: ``SCHEMA.OBJECT`` upper-case.
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from dsxlineage.estate.ingest.sqlrefs import extract_table_refs, normalize_ref, table_fqn
from dsxlineage.estate.ingest.types import ParsedTSQL
from dsxlineage.estate.ir import ColumnDef, ProcedureDef, TableDef, ViewDef

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_GO_RE = re.compile(r"^\s*GO(?:\s+\d+)?\s*;?\s*$", re.IGNORECASE)

_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?P<name>[\[\]\"\w$#\.]+)", re.IGNORECASE
)
_CREATE_VIEW_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+(?P<name>[\[\]\"\w$#\.]+)"
    r"(?:\s*\([^)]*\))?\s+AS\b",
    re.IGNORECASE,
)
_CREATE_PROC_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\s+(?P<name>[\[\]\"\w$#\.]+)",
    re.IGNORECASE,
)

_CONSTRAINT_PREFIXES = (
    "CONSTRAINT",
    "PRIMARY",
    "UNIQUE",
    "CHECK",
    "FOREIGN",
    "INDEX",
    "KEY",
)
_COLUMN_NAME_RE = re.compile(r"^([\[\]\"\w$#]+)\s+(.*)$", re.DOTALL)
_DATA_TYPE_RE = re.compile(r"^([A-Za-z][\w]*(?:\s*\([^)]*\))?)")

_PROC_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("SCD2", "transform"),
    ("RECON", "reconciliation"),
    ("BASEL", "business_logic"),
    ("IFRS9", "business_logic"),
    ("PROVISION", "business_logic"),
    ("FEATURE", "ml_feature"),
    ("WRITEBACK", "ml_writeback"),
)


def strip_fences(text: str) -> str:
    """Remove a leading BOM and markdown code fences from an artifact."""
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    while lines and _FENCE_RE.match(lines[-1]):
        lines = lines[:-1]
    return "\n".join(lines)


def split_batches(text: str) -> list[str]:
    """Split a T-SQL script into ``GO``-delimited batches."""
    batches: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _GO_RE.match(line):
            if current:
                batches.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        batches.append("\n".join(current))
    return [b for b in (b.strip() for b in batches) if b]


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas outside parens and quotes."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    in_single = False
    in_bracket = False
    i = 0
    while i < len(body):
        ch = body[i]
        nxt = body[i + 1] if i + 1 < len(body) else ""
        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 1
                else:
                    in_single = False
        elif in_bracket:
            buf.append(ch)
            if ch == "]":
                in_bracket = False
        elif ch == "'":
            in_single = True
            buf.append(ch)
        elif ch == "[":
            in_bracket = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _strip_comments(body: str) -> str:
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", body)


def _balanced_parens(text: str, start: int) -> tuple[str, bool]:
    """Return the parenthesised group starting at ``text[start] == '('``.

    The second element is True when the group closes; False means the artifact
    was truncated before the closing parenthesis.
    """
    depth = 0
    in_single = False
    in_bracket = False
    i = start
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_single:
            if ch == "'":
                if nxt == "'":
                    i += 1
                else:
                    in_single = False
        elif in_bracket:
            if ch == "]":
                in_bracket = False
        elif ch == "'":
            in_single = True
        elif ch == "[":
            in_bracket = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], True
        i += 1
    return text[start + 1 :], False


def parse_create_table(
    statement: str, source: str = ""
) -> tuple[TableDef | None, bool]:
    """Parse the first ``CREATE TABLE`` in ``statement``.

    Returns ``(table_def, truncated)`` - ``None`` when no CREATE TABLE exists.
    """
    match = _CREATE_TABLE_RE.search(statement)
    if not match:
        return None, False
    fqn = normalize_ref(match.group("name"))
    if not fqn or fqn.startswith("@"):
        return None, False
    paren = statement.find("(", match.end())
    if paren == -1:
        return None, True
    body, closed = _balanced_parens(statement, paren)
    columns: list[ColumnDef] = []
    for raw_item in _split_top_level(body):
        item = _strip_comments(raw_item).strip()
        if not item:
            continue
        upper = item.upper()
        if upper.startswith(_CONSTRAINT_PREFIXES):
            continue
        col_match = _COLUMN_NAME_RE.match(item)
        if not col_match:
            continue
        name = col_match.group(1).replace("[", "").replace("]", "").replace('"', "")
        remainder = col_match.group(2).strip()
        type_match = _DATA_TYPE_RE.match(remainder)
        data_type = type_match.group(1).upper().replace(" ", "") if type_match else "VARCHAR(100)"
        columns.append(
            ColumnDef(
                name=name.upper(),
                data_type=data_type,
                nullable="NOT NULL" not in upper,
                pk="PRIMARY KEY" in upper,
            )
        )
    schema = fqn.split(".", 1)[0] if "." in fqn else "DW"
    return TableDef(fqn=fqn, system=schema, columns=columns), not closed


def _view_sources(sql: str) -> tuple[list[str], str | None]:
    """Extract view dependencies, tolerating truncated statements."""
    try:
        tree = sqlglot.parse_one(sql, read="tsql")
    except Exception:
        tree = None
    if tree is not None:
        ctes = {cte.alias.upper() for cte in tree.find_all(exp.CTE) if cte.alias}
        sources: list[str] = []
        for table in tree.find_all(exp.Table):
            ref = table_fqn(table)
            if ref and ref not in ctes:
                sources.append(ref)
        if sources:
            deduped = list(dict.fromkeys(sources))
            return deduped, None
        return [], "sqlglot parsed the view but found no table references"
    reads, _ = extract_table_refs(sql)
    if reads:
        return reads, "sqlglot could not parse the view - regex fallback"
    return [], "could not determine view sources"


def _complexity(sql: str, sources: list[str]) -> str:
    upper = sql.upper()
    if "GROUP BY" in upper or "SUM(" in upper or "COUNT(" in upper:
        return "aggregate"
    if "JOIN" in upper:
        return "join"
    if len(sources) > 1:
        return "cross_system"
    return "simple"


def _proc_type(name: str) -> str:
    upper = name.upper()
    for hint, proc_type in _PROC_TYPE_HINTS:
        if hint in upper:
            return proc_type
    return "transform"


def parse_create_procedure(body: str) -> ProcedureDef | None:
    """Parse a procedure body already isolated from its batch."""
    match = _CREATE_PROC_RE.search(body)
    if not match:
        return None
    fqn = normalize_ref(match.group("name")) or "UNKNOWN_PROC"
    statement = body[match.start() :]
    reads, writes = extract_table_refs(statement)
    calls = [
        ref
        for ref in (
            normalize_ref(m.group(1))
            for m in re.finditer(r"\bEXEC(?:UTE)?\s+([\[\]\"\w$#\.]+)", statement, re.IGNORECASE)
        )
        if ref and not ref.upper().startswith(("SP_", "XP_"))
    ]
    reads = [r for r in reads if r not in writes]
    upper = statement.upper()
    has_dynamic = bool(re.search(r"\bSP_EXECUTESQL\b|\bEXEC(?:UTE)?\s*\(", upper))
    has_cursor = "CURSOR" in upper
    terminated = bool(re.search(r"\bEND\s*;?\s*$", statement.rstrip(), re.IGNORECASE))
    if has_dynamic:
        complexity = "high"
    elif has_cursor or len(reads) + len(writes) >= 3:
        complexity = "medium"
    else:
        complexity = "low"
    return ProcedureDef(
        fqn=fqn,
        language="T-SQL",
        proc_type=_proc_type(fqn),
        reads=reads,
        writes=writes,
        calls=calls,
        has_dynamic=has_dynamic,
        has_cursor=has_cursor,
        complexity=complexity,
        source_text=statement[:4000],
        unresolved=not terminated,
        unresolved_reason=None if terminated else "artifact truncated before END",
    )


def parse_tsql_text(text: str, source: str = "") -> ParsedTSQL:
    """Parse one T-SQL script (possibly fenced/truncated) into IR fragments."""
    result = ParsedTSQL()
    seen_tables: set[str] = set()
    seen_views: set[str] = set()
    seen_procs: set[str] = set()

    for batch in split_batches(strip_fences(text)):
        for match in _CREATE_TABLE_RE.finditer(batch):
            table, truncated = parse_create_table(batch[match.start() :], source)
            if table is None or table.fqn in seen_tables:
                continue
            seen_tables.add(table.fqn)
            if truncated:
                table.columns = table.columns
            result.tables.append(table)

        for match in _CREATE_VIEW_RE.finditer(batch):
            fqn = normalize_ref(match.group("name"))
            if not fqn or fqn in seen_views:
                continue
            seen_views.add(fqn)
            next_match = _CREATE_VIEW_RE.search(batch, match.end()) or _CREATE_PROC_RE.search(
                batch, match.end()
            )
            end = next_match.start() if next_match else len(batch)
            select_sql = batch[match.end() : end].strip()
            select_sql = re.sub(r"\bGO\s*$", "", select_sql, flags=re.IGNORECASE).strip()
            without_trailing_comments = re.sub(r"(?:--[^\n]*\s*)+$", "", select_sql).rstrip()
            truncated = not without_trailing_comments.endswith(";")
            sources, parse_note = _view_sources(select_sql)
            reason = None
            if truncated:
                reason = "artifact truncated mid-statement"
            elif parse_note:
                reason = parse_note
            result.views.append(
                ViewDef(
                    fqn=fqn,
                    sql=select_sql,
                    complexity="dynamic" if truncated else _complexity(select_sql, sources),
                    source_fqns=sources,
                    unresolved=bool(reason),
                    unresolved_reason=reason,
                )
            )

        proc_matches = list(_CREATE_PROC_RE.finditer(batch))
        for idx, match in enumerate(proc_matches):
            body_end = proc_matches[idx + 1].start() if idx + 1 < len(proc_matches) else len(batch)
            proc = parse_create_procedure(batch[match.start() : body_end])
            if proc and proc.fqn not in seen_procs:
                seen_procs.add(proc.fqn)
                result.procedures.append(proc)

    return result


def parse_tsql_dir(directory: Path) -> ParsedTSQL:
    """Parse every ``*.sql`` file in ``directory`` (sorted for determinism)."""
    result = ParsedTSQL()
    seen_tables: set[str] = set()
    seen_views: set[str] = set()
    seen_procs: set[str] = set()
    for path in sorted(Path(directory).glob("*.sql")):
        parsed = parse_tsql_text(path.read_text(encoding="utf-8", errors="replace"), str(path))
        for table in parsed.tables:
            if table.fqn not in seen_tables:
                seen_tables.add(table.fqn)
                result.tables.append(table)
        for view in parsed.views:
            if view.fqn not in seen_views:
                seen_views.add(view.fqn)
                result.views.append(view)
        for proc in parsed.procedures:
            if proc.fqn not in seen_procs:
                seen_procs.add(proc.fqn)
                result.procedures.append(proc)
    return result
