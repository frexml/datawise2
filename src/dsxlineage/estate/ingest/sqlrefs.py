"""Table read/write extraction from T-SQL fragments.

Artifacts in the external estate are frequently truncated, so every helper
degrades gracefully: try the SQL parser first, fall back to regex scanning that
keeps partial results. FQNs are normalised to ``SCHEMA.OBJECT`` upper-case.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

_SYSTEM_SCHEMAS = frozenset(
    {"SYS", "INFORMATION_SCHEMA", "DBO", "MASTER", "TEMPDB", "PG_CATALOG"}
)

_REF_TOKEN = r"[\[\]\"\w$#@\.]+"

_READ_RE = re.compile(rf"\b(?:FROM|JOIN)\s+({_REF_TOKEN})", re.IGNORECASE)
_WRITE_RES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(rf"\bINSERT\s+INTO\s+({_REF_TOKEN})", re.IGNORECASE), 1),
    (re.compile(rf"\bMERGE\s+INTO\s+({_REF_TOKEN})", re.IGNORECASE), 1),
    (re.compile(rf"\bUPDATE\s+({_REF_TOKEN})", re.IGNORECASE), 1),
    (re.compile(rf"\bDELETE\s+FROM\s+({_REF_TOKEN})", re.IGNORECASE), 1),
    (re.compile(rf"\bTRUNCATE\s+TABLE\s+({_REF_TOKEN})", re.IGNORECASE), 1),
    (re.compile(rf"\bSELECT\s+.*?\s+INTO\s+({_REF_TOKEN})", re.IGNORECASE | re.DOTALL), 1),
)
_CTE_RE = re.compile(r"WITH\s+([A-Za-z_#][\w$#]*)\s+AS\s*\(", re.IGNORECASE)
_CTE_NEXT_RE = re.compile(r",\s*([A-Za-z_#][\w$#]*)\s+AS\s*\(", re.IGNORECASE)
_ALIAS_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+({_REF_TOKEN})\s+(?:AS\s+)?([A-Za-z_#][\w$#]*)\b",
    re.IGNORECASE,
)
_RESERVED_ALIASES = frozenset(
    {"ON", "WHERE", "GROUP", "ORDER", "HAVING", "INNER", "LEFT", "RIGHT", "FULL",
     "CROSS", "OUTER", "WITH", "SELECT", "UNION", "EXCEPT", "INTERSECT"}
)


def normalize_ref(raw: str | None) -> str | None:
    """Normalise one table reference to ``SCHEMA.OBJECT`` upper-case.

    Strips bracket quoting, rejects variables/temp objects/system schemas and
    collapses three-part names whose database is known (``DataWise.dbo.X``).
    """
    if not raw:
        return None
    value = raw.strip().strip("()[],;\"' \t\r\n")
    value = value.replace("[", "").replace("]", "").replace('"', "")
    if not value or "." not in value:
        return None
    if value.startswith(("@", "#")):
        return None
    parts = [p for p in value.split(".") if p]
    if not parts:
        return None
    if len(parts) >= 3 and parts[0].upper() in {"DATAWISE", "DATAWISE_ADW", "DW"}:
        parts = parts[1:]
    if len(parts) >= 3:
        parts = parts[-2:]
    fqn = ".".join(parts).upper()
    if "." in fqn and fqn.split(".", 1)[0] in _SYSTEM_SCHEMAS:
        return None
    return fqn


def _cte_names(sql: str) -> set[str]:
    """Collect CTE names so they are never mistaken for physical tables."""
    names = {m.group(1).upper() for m in _CTE_RE.finditer(sql)}
    names.update(m.group(1).upper() for m in _CTE_NEXT_RE.finditer(sql))
    return names


def _alias_map(sql: str) -> dict[str, str]:
    """Map ``alias -> SCHEMA.OBJECT`` for every FROM/JOIN occurrence."""
    mapping: dict[str, str] = {}
    for match in _ALIAS_RE.finditer(sql):
        ref = normalize_ref(match.group(1))
        alias = match.group(2).upper()
        if ref and alias not in _RESERVED_ALIASES:
            mapping.setdefault(alias, ref)
    if not mapping:
        try:
            for tree in sqlglot.parse(sql, read="tsql"):
                if tree is None:
                    continue
                for subquery in tree.find_all(exp.Subquery):
                    if not subquery.alias:
                        continue
                    inner = [ref for ref in (table_fqn(t) for t in subquery.find_all(exp.Table)) if ref]
                    if len(set(inner)) == 1:
                        mapping.setdefault(subquery.alias.upper(), inner[0])
        except Exception:
            pass
    return mapping


def table_fqn(table: exp.Table) -> str | None:
    """Build a normalised FQN directly from a sqlglot ``Table`` node."""
    parts = [part for part in (table.catalog, table.db, table.name) if part]
    if len(parts) < 2:
        return None
    return normalize_ref(".".join(parts))


def _inside_stored_procedure(node: exp.Expression) -> bool:
    """True when a table node is the routine header, not a body reference."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.StoredProcedure):
            return True
        parent = parent.parent
    return False


def _from_sqlglot(sql: str) -> tuple[list[str], list[str]] | None:
    try:
        trees = sqlglot.parse(sql, read="tsql")
    except Exception:
        return None
    if not trees:
        return None

    reads: list[str] = []
    writes: list[str] = []
    for tree in trees:
        if tree is None:
            continue
        ctes = {cte.alias.upper() for cte in tree.find_all(exp.CTE) if cte.alias}
        write_nodes: set[int] = set()
        skip_nodes: set[int] = set()
        for cls in (exp.Insert, exp.Update, exp.Delete, exp.Merge):
            for node in tree.find_all(cls):
                target = node.this
                if isinstance(target, exp.Schema):
                    target = target.this
                if isinstance(target, exp.Table):
                    write_nodes.add(id(target))
        for cls in (exp.Create, exp.Drop, exp.Alter):
            for node in tree.find_all(cls):
                target = node.this
                if isinstance(target, exp.Schema):
                    target = target.this
                if isinstance(target, exp.Table):
                    skip_nodes.add(id(target))
        for table in tree.find_all(exp.Table):
            if _inside_stored_procedure(table):
                continue
            name = table_fqn(table)
            if not name or name in ctes or id(table) in skip_nodes:
                continue
            (writes if id(table) in write_nodes else reads).append(name)
    if not reads and not writes:
        return None
    return _dedupe(reads), _dedupe(writes)


def _from_regex(sql: str) -> tuple[list[str], list[str]]:
    ctes = _cte_names(sql)
    aliases = _alias_map(sql)
    reads = [
        ref
        for ref in (normalize_ref(m.group(1)) for m in _READ_RE.finditer(sql))
        if ref and ref not in ctes
    ]
    writes: list[str] = []
    for pattern, group in _WRITE_RES:
        for match in pattern.finditer(sql):
            raw = match.group(group)
            ref = aliases.get(raw.strip().strip("[]").upper()) or normalize_ref(raw)
            if ref:
                writes.append(ref)
    return _dedupe(reads), _dedupe(writes)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


_DYNAMIC_SQL_RE = re.compile(r"'((?:[^']|'')*)'")
_DYNAMIC_SQL_KEYWORDS = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE)\b", re.IGNORECASE
)


def _dynamic_sql_bodies(sql: str) -> list[str]:
    """Extract SQL executed through string literals (``EXEC('...')``).

    DataStage and Talend wrappers commonly run their DDL/DML through dynamic
    SQL, where the statement text sits inside a string literal. Those are real
    executed statements, so their table references must be extracted too.
    """
    bodies: list[str] = []
    for match in _DYNAMIC_SQL_RE.finditer(sql):
        body = match.group(1).replace("''", "'").strip()
        if len(body) < 20 or not _DYNAMIC_SQL_KEYWORDS.search(body):
            continue
        bodies.append(body)
    return bodies


def _extract_once(sql: str) -> tuple[list[str], list[str]]:
    parsed = _from_sqlglot(sql)
    regex_reads, regex_writes = _from_regex(sql)
    if parsed is None:
        return regex_reads, regex_writes
    reads, writes = parsed
    if not reads:
        reads = regex_reads
    writes = _dedupe(writes + regex_writes)
    return _dedupe(reads), writes


def extract_table_refs(sql: str) -> tuple[list[str], list[str]]:
    """Return ``(reads, writes)`` table FQNs for a T-SQL fragment.

    Uses sqlglot (T-SQL dialect) when the fragment parses, otherwise a regex
    scan that tolerates truncation and vendor extensions. SQL wrapped in string
    literals (dynamic ``EXEC``) is unwrapped and scanned one level deep.
    """
    if not sql or not sql.strip():
        return [], []
    reads, writes = _extract_once(sql)
    for body in _dynamic_sql_bodies(sql):
        body_reads, body_writes = _extract_once(body)
        reads = _dedupe(reads + body_reads)
        writes = _dedupe(writes + body_writes)
    return reads, writes
