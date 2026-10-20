"""Column-level lineage helpers for T-SQL fragments (T-SQL dialect).

Two entry points:

* :func:`lineage_from_write_sql` - maps ``INSERT ... SELECT`` and ``MERGE``
  statements (the shape every ETL target write uses) to ``ColumnLineage`` rows.
* :func:`lineage_from_view_sql` - resolves a ``CREATE VIEW`` SELECT via
  sqlglot's scope-aware lineage resolver.

Rows whose upstream table is not defined anywhere are marked ``unresolved`` with
a human-readable reason; :mod:`dsxlineage.estate.build` later enriches hops and
full paths through the estate-wide column graph.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage

from dsxlineage.estate.ingest.sqlrefs import normalize_ref, table_fqn
from dsxlineage.estate.ir import ColumnLineage

_MAX_ROWS_PER_STATEMENT = 400


def _unresolved_row(target_fqn: str, reason: str, column: str = "*") -> ColumnLineage:
    return ColumnLineage(
        target_table=target_fqn,
        target_column=column,
        source_table="",
        source_column="",
        confidence=0.0,
        unresolved=True,
        unresolved_reason=reason,
        target_schema=target_fqn.split(".")[0] if "." in target_fqn else "",
        hops=1,
        full_path=f"{target_fqn}.{column} (unresolved)",
    )


def _alias_map(scope: exp.Expression) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for table in scope.find_all(exp.Table):
        if not table.alias:
            continue
        ref = table_fqn(table) or (table.name.upper() if table.name else None)
        if ref:
            mapping.setdefault(table.alias.upper(), ref)
    for subquery in scope.find_all(exp.Subquery):
        if not subquery.alias or subquery.alias.upper() in mapping:
            continue
        inner = [ref for ref in (table_fqn(t) for t in subquery.find_all(exp.Table)) if ref]
        if len(set(inner)) == 1:
            mapping[subquery.alias.upper()] = inner[0]
    return mapping


def _source_rows(
    expr: exp.Expression,
    target_fqn: str,
    transform_expr: str | None,
    aliases: Mapping[str, str],
    default_table: str | None,
    known_fqns: set[str],
) -> list[ColumnLineage]:
    """One row per referenced column, or a synthetic row for literals."""
    columns = [col for col in expr.find_all(exp.Column)]
    if not columns:
        sql_text = expr.sql(dialect="tsql")
        is_aggregate = bool(expr.find(exp.Count) or expr.find(exp.Sum) or expr.find(exp.Max) or expr.find(exp.Min) or expr.find(exp.Avg))
        source = default_table or ""
        derived = bool(transform_expr)
        unresolved = not source and not derived
        return [
            ColumnLineage(
                target_table=target_fqn,
                target_column="",
                source_table=source,
                source_column="*",
                transform_expr=transform_expr,
                confidence=0.0 if unresolved else (0.9 if is_aggregate else 0.6),
                unresolved=unresolved,
                unresolved_reason=None if not unresolved else f"no traceable source column for [{sql_text[:60]}]",
                target_schema=target_fqn.split(".")[0] if "." in target_fqn else "",
                source_schema=source.split(".")[0] if "." in source else "",
                hops=1,
                full_path=f"{target_fqn} <- [{sql_text[:80]}]" if derived and not source else "",
            )
        ]
    rows: list[ColumnLineage] = []
    for col in columns:
        source_table = default_table
        if col.table:
            source_table = aliases.get(col.table.upper(), col.table.upper())
            if "." not in source_table and default_table:
                source_table = aliases.get(col.table.upper(), default_table)
        source_table = source_table or ""
        missing = bool(source_table) and known_fqns is not None and source_table not in known_fqns
        rows.append(
            ColumnLineage(
                target_table=target_fqn,
                target_column="",
                source_table=source_table,
                source_column=col.name.upper(),
                transform_expr=transform_expr,
                confidence=0.4 if missing else (0.85 if transform_expr else 0.95),
                unresolved=missing,
                unresolved_reason=(
                    f"Upstream table {source_table} is referenced but never defined in the estate"
                    if missing
                    else None
                ),
                target_schema=target_fqn.split(".")[0] if "." in target_fqn else "",
                source_schema=source_table.split(".")[0] if "." in source_table else "",
                hops=1,
                full_path=f"{source_table}.{col.name.upper()} -> {target_fqn}" if source_table else "",
            )
        )
    return rows


def _projections_with_targets(
    select: exp.Expression,
    insert_columns: list[str],
    columns_by_table: Mapping[str, list[str]],
    target_fqn: str,
) -> list[tuple[str, exp.Expression]]:
    """Pair each SELECT projection with its destination column name."""
    projections: list[exp.Expression] = []
    if isinstance(select, exp.Select):
        projections = list(select.expressions)
    elif isinstance(select, exp.Union):
        inner = select.this if isinstance(select.this, exp.Select) else None
        projections = list(inner.expressions) if inner else []
    pairs: list[tuple[str, exp.Expression]] = []
    target_columns = columns_by_table.get(target_fqn, [])
    for idx, projection in enumerate(projections):
        expr = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(expr, exp.Star):
            if target_columns:
                pairs.extend((col, exp.column(col)) for col in target_columns)
            else:
                pairs.append(("*", expr))
            continue
        alias = projection.alias_or_name if projection.alias else None
        if insert_columns and idx < len(insert_columns):
            column = insert_columns[idx].upper()
        elif alias:
            column = alias.upper()
        elif target_columns and idx < len(target_columns):
            column = target_columns[idx]
        else:
            column = expr.name.upper() if expr.name else f"COL_{idx}"
        pairs.append((column, expr))
    return pairs


def _lineage_from_insert(
    insert: exp.Insert,
    known_fqns: set[str] | None,
    columns_by_table: Mapping[str, list[str]],
    target_hint: str | None,
) -> list[ColumnLineage]:
    target = insert.this
    insert_columns: list[str] = []
    if isinstance(target, exp.Schema):
        insert_columns = [c.name.upper() for c in target.expressions if isinstance(c, exp.Identifier)]
        target = target.this
    if not isinstance(target, exp.Table):
        return []
    target_fqn = table_fqn(target) or target_hint
    if not target_fqn:
        return []
    select = insert.expression
    if select is None or not isinstance(select, (exp.Select, exp.Union)):
        return []
    aliases = _alias_map(select)
    tables = [
        ref
        for ref in (table_fqn(t) for t in select.find_all(exp.Table))
        if ref
    ]
    default_table = tables[0] if len(tables) == 1 else None
    cte_names = {cte.alias.upper() for cte in select.find_all(exp.CTE) if cte.alias}
    if default_table and default_table.split(".")[-1] in cte_names:
        default_table = None

    rows: list[ColumnLineage] = []
    for column, expr in _projections_with_targets(select, insert_columns, columns_by_table, target_fqn):
        if column == "*":
            rows.append(_unresolved_row(target_fqn, "SELECT * from an unknown projection list"))
            continue
        transform = None if isinstance(expr, exp.Column) else expr.sql(dialect="tsql")[:400]
        for row in _source_rows(expr, target_fqn, transform, aliases, default_table, known_fqns):
            row.target_column = column
            if not row.full_path and row.source_table:
                row.full_path = f"{row.source_table}.{row.source_column} -> {target_fqn}.{column}"
            rows.append(row)
        if len(rows) >= _MAX_ROWS_PER_STATEMENT:
            break
    return rows


def _lineage_from_merge(
    merge: exp.Merge,
    known_fqns: set[str] | None,
    columns_by_table: Mapping[str, list[str]],
    target_hint: str | None,
) -> list[ColumnLineage]:
    target = merge.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table):
        return []
    target_fqn = table_fqn(target) or target_hint
    using = merge.args.get("using")
    if not target_fqn or using is None:
        return []
    select = using.this if isinstance(using, exp.Subquery) else using
    if isinstance(select, exp.Subquery):
        select = select.this
    if not isinstance(select, (exp.Select, exp.Union)):
        return []
    aliases = _alias_map(select)
    tables = [ref for ref in (table_fqn(t) for t in select.find_all(exp.Table)) if ref]
    default_table = tables[0] if len(tables) == 1 else None
    rows: list[ColumnLineage] = []
    for column, expr in _projections_with_targets(select, [], columns_by_table, target_fqn):
        if column == "*":
            continue
        transform = None if isinstance(expr, exp.Column) else expr.sql(dialect="tsql")[:400]
        for row in _source_rows(expr, target_fqn, transform, aliases, default_table, known_fqns):
            row.target_column = column
            row.confidence = min(row.confidence, 0.75)
            rows.append(row)
        if len(rows) >= _MAX_ROWS_PER_STATEMENT:
            break
    return rows


def lineage_from_write_sql(
    sql: str,
    target_hint: str | None,
    known_fqns: set[str] | None,
    columns_by_table: Mapping[str, list[str]] | None = None,
) -> list[ColumnLineage]:
    """Column lineage for every INSERT/MERGE statement in ``sql``.

    Passing ``known_fqns=None`` defers missing-table judgement to the estate
    builder, which knows the full object universe. Unparseable fragments
    produce a single unresolved row so the UI can surface the artifact as an
    Attention Required case rather than silently dropping it.
    """
    if not sql or not sql.strip():
        return []
    columns_by_table = columns_by_table or {}
    try:
        trees = sqlglot.parse(sql, read="tsql")
    except Exception as exc:
        if target_hint:
            return [_unresolved_row(target_hint, f"could not parse write statement: {exc}")]
        return []
    rows: list[ColumnLineage] = []
    for tree in trees:
        if tree is None:
            continue
        try:
            if isinstance(tree, exp.Insert):
                rows.extend(_lineage_from_insert(tree, known_fqns, columns_by_table, target_hint))
            elif isinstance(tree, exp.Merge):
                rows.extend(_lineage_from_merge(tree, known_fqns, columns_by_table, target_hint))
        except Exception:
            continue
    return rows


def lineage_from_select_sql(
    sql: str,
    target_fqn: str,
    known_fqns: set[str] | None,
    columns_by_table: Mapping[str, list[str]] | None = None,
) -> list[ColumnLineage]:
    """Map a bare SELECT's projections onto ``target_fqn`` columns by alias."""
    if not sql or not sql.strip():
        return []
    try:
        tree = sqlglot.parse_one(sql, read="tsql")
    except Exception as exc:
        return [_unresolved_row(target_fqn, f"could not parse projection SQL: {exc}")]
    if tree is None or not isinstance(tree, (exp.Select, exp.Union)):
        return []
    aliases = _alias_map(tree)
    tables = [ref for ref in (table_fqn(t) for t in tree.find_all(exp.Table)) if ref]
    default_table = tables[0] if len(tables) == 1 else None
    rows: list[ColumnLineage] = []
    for column, expr in _projections_with_targets(tree, [], columns_by_table or {}, target_fqn):
        transform = None if isinstance(expr, exp.Column) else expr.sql(dialect="tsql")[:400]
        for row in _source_rows(expr, target_fqn, transform, aliases, default_table, known_fqns):
            row.target_column = column
            rows.append(row)
        if len(rows) >= _MAX_ROWS_PER_STATEMENT:
            break
    return rows


def _leaves(node) -> list:
    out, stack = [], [node]
    while stack:
        current = stack.pop()
        if current.downstream:
            stack.extend(current.downstream)
        else:
            out.append(current)
    return out


def lineage_from_view_sql(
    view_fqn: str,
    sql: str,
    known_fqns: set[str],
    source_fqns: list[str] | None = None,
) -> list[ColumnLineage]:
    """Column lineage for a view SELECT, tolerant of T-SQL and CTEs."""
    source_fqns = source_fqns or []
    try:
        tree = sqlglot.parse_one(sql, read="tsql")
        if tree is None:
            raise ValueError("empty parse")
        select = tree if isinstance(tree, (exp.Select, exp.Union)) else next(tree.find_all(exp.Select), None)
        if select is None:
            raise ValueError("no SELECT found")
        result = lineage(None, select, dialect="tsql")
    except Exception as exc:
        return [_unresolved_row(view_fqn, f"column lineage resolution failed: {exc}")]

    rows: list[ColumnLineage] = []
    for target_col, node in result.items():
        leaves = [leaf for leaf in _leaves(node) if isinstance(leaf.source, exp.Table)]
        if not leaves:
            rows.append(
                ColumnLineage(
                    target_table=view_fqn,
                    target_column=target_col.upper(),
                    source_table=source_fqns[0] if source_fqns else "",
                    source_column="*",
                    transform_expr=node.expression.sql(dialect="tsql")[:400],
                    confidence=0.6,
                    unresolved=not source_fqns,
                    unresolved_reason=None if source_fqns else "no traceable source column",
                    target_schema=view_fqn.split(".")[0] if "." in view_fqn else "",
                    hops=1,
                )
            )
            continue
        inner = node.expression.this if isinstance(node.expression, exp.Alias) else node.expression
        transform = None if isinstance(inner, exp.Column) else inner.sql(dialect="tsql")[:400]
        for leaf in leaves:
            source_table_ref = table_fqn(leaf.source) or ""
            missing = bool(source_table_ref) and known_fqns is not None and source_table_ref not in known_fqns
            rows.append(
                ColumnLineage(
                    target_table=view_fqn,
                    target_column=target_col.upper(),
                    source_table=source_table_ref,
                    source_column=leaf.name.split(".")[-1].upper(),
                    transform_expr=transform,
                    confidence=0.4 if missing else (0.85 if transform else 0.95),
                    unresolved=missing,
                    unresolved_reason=(
                        f"Upstream table {source_table_ref} is referenced but never defined in the estate"
                        if missing
                        else None
                    ),
                    target_schema=view_fqn.split(".")[0] if "." in view_fqn else "",
                    source_schema=source_table_ref.split(".")[0] if "." in source_table_ref else "",
                    hops=1,
                    full_path=f"{source_table_ref}.{leaf.name.split('.')[-1].upper()} -> {view_fqn}.{target_col.upper()}",
                )
            )
        if len(rows) >= _MAX_ROWS_PER_STATEMENT:
            break
    return rows
