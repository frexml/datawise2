"""Estate drift detection between ingestion runs.

A fingerprint is a compact, JSON-serialisable snapshot of the IR's objects and
edges. It is stored in the ledger with every run so a re-run can diff the new
estate against the last recorded one and log exactly what changed.
"""

from __future__ import annotations

from collections import defaultdict

from dsxlineage.estate.ir import EstateIR


def _normalise(text: str | None) -> str:
    """Collapse whitespace so formatting-only edits don't register as drift."""
    return " ".join((text or "").split())


def _logic_map(ir: EstateIR) -> dict[str, str]:
    """Per-object business logic: transform expressions, view SQL, procedure bodies.

    Keys are stable identifiers so a later run can report exactly what changed
    (e.g. ``SUM(Months)/12`` -> ``SUM(Months)/6``) with before/after values.
    """
    logic: dict[str, str] = {}
    columns: dict[str, set[str]] = defaultdict(set)
    for row in ir.column_lineage:
        expression = _normalise(row.transform_expr)
        if not expression:
            continue
        key = (
            f"column:{row.target_table}.{row.target_column}"
            f"<-{row.source_table}.{row.source_column}"
        )
        columns[key].add(expression)
    for key, expressions in columns.items():
        logic[key] = " || ".join(sorted(expressions))
    for view in ir.views:
        sql = _normalise(view.sql)
        if sql:
            logic[f"view:{view.fqn}"] = sql
    for procedure in ir.procedures:
        body = _normalise(procedure.source_text)
        if body:
            logic[f"procedure:{procedure.fqn}"] = body
    return logic


def _attribute_map(ir: EstateIR) -> dict[str, str]:
    """Non-SQL attributes for every object, so metadata edits also show as drift."""
    attributes: dict[str, str] = {}

    for table in ir.tables:
        columns = "|".join(
            f"{column.name}:{column.data_type}:{int(column.pk)}:{int(column.nullable)}"
            for column in table.columns
        )
        attributes[f"table:{table.fqn}"] = _normalise(
            f"system={table.system}; row_estimate={table.row_estimate}; columns={columns}"
        )
    for view in ir.views:
        attributes[f"view_meta:{view.fqn}"] = _normalise(
            f"materialized={view.is_materialized}; complexity={view.complexity}; "
            f"unresolved={view.unresolved}; reason={view.unresolved_reason}; "
            f"sources={','.join(sorted(view.source_fqns))}"
        )
    for procedure in ir.procedures:
        attributes[f"procedure_meta:{procedure.fqn}"] = _normalise(
            f"language={procedure.language}; type={procedure.proc_type}; "
            f"reads={','.join(sorted(procedure.reads))}; "
            f"writes={','.join(sorted(procedure.writes))}; "
            f"calls={','.join(sorted(procedure.calls))}; "
            f"dynamic={procedure.has_dynamic}; cursor={procedure.has_cursor}; "
            f"unresolved={procedure.unresolved}; reason={procedure.unresolved_reason}"
        )
    for job in ir.etl_jobs:
        attributes[f"etl:{job.fqn}"] = _normalise(
            f"dialect={job.dialect}; source={job.source_fqn}; target={job.target_fqn}; "
            f"schedule={job.schedule}; tool={job.tool}; declared_only={job.declared_only}; "
            f"sources={','.join(sorted(job.source_fqns))}; "
            f"targets={','.join(sorted(job.target_fqns))}"
        )
    for schedule in ir.schedules:
        attributes[f"schedule:{schedule.fqn}"] = _normalise(
            f"scheduler={schedule.scheduler}; expr={schedule.schedule_expr}; "
            f"frequency={schedule.frequency}; "
            f"depends_on={','.join(sorted(schedule.depends_on))}"
        )
    for dashboard in ir.dashboards:
        attributes[f"dashboard:{dashboard.fqn}"] = _normalise(
            f"tool={dashboard.tool}; source={dashboard.source_fqn}; "
            f"description={dashboard.description}"
        )
    for routine in ir.routines:
        attributes[f"routine:{routine.fqn}"] = _normalise(
            f"kind={routine.kind}; language={routine.language}; artifact={routine.artifact}; "
            f"reads={','.join(sorted(routine.reads))}; writes={','.join(sorted(routine.writes))}; "
            f"features={','.join(sorted(routine.features))}; "
            f"models={','.join(sorted(routine.model_files))}; "
            f"unresolved={routine.unresolved}; reason={routine.unresolved_reason}"
        )
    return attributes


def _object_fqns(ir: EstateIR) -> list[str]:
    """All node FQNs in the IR (tables, views, procs, ETL, schedules, dashboards, routines)."""
    return sorted(
        [t.fqn for t in ir.tables]
        + [v.fqn for v in ir.views]
        + [p.fqn for p in ir.procedures]
        + [j.fqn for j in ir.etl_jobs]
        + [s.fqn for s in ir.schedules]
        + [d.fqn for d in ir.dashboards]
        + [r.fqn for r in ir.routines]
    )


def _edge_keys(ir: EstateIR) -> list[str]:
    """Canonical string keys for every edge (type included)."""
    return sorted(f"{e.source_fqn}->{e.target_fqn}:{e.edge_type.value}" for e in ir.edges)


def fingerprint_ir(ir: EstateIR) -> dict[str, object]:
    """Build the ledger-storable fingerprint for an IR."""
    return {
        "nodes": ir.total_nodes,
        "edges": ir.total_edges,
        "unresolved": ir.unresolved_count,
        "objects": _object_fqns(ir),
        "edge_keys": _edge_keys(ir),
        "unresolved_refs": sorted(ref.fqn for ref in ir.unresolved_refs),
        "logic": _logic_map(ir),
        "attributes": _attribute_map(ir),
    }


def _as_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _counts(fingerprint: dict[str, object] | None) -> dict[str, int] | None:
    if not fingerprint:
        return None
    return {
        "nodes": int(fingerprint.get("nodes") or 0),
        "edges": int(fingerprint.get("edges") or 0),
        "unresolved": int(fingerprint.get("unresolved") or 0),
    }


def diff_fingerprints(
    previous: dict[str, object] | None, current: dict[str, object]
) -> dict[str, object]:
    """Diff two fingerprints; returns added/removed objects and edges + counts.

    ``baseline`` is true when no previous fingerprint exists (first recorded run).
    """
    if not previous:
        return {
            "changed": False,
            "baseline": True,
            "added_objects": [],
            "removed_objects": [],
            "added_edges": [],
            "removed_edges": [],
            "logic_changes": [],
            "attribute_changes": [],
            "counts_before": None,
            "counts_after": _counts(current),
        }

    previous_objects = _as_set(previous.get("objects"))
    current_objects = _as_set(current.get("objects"))
    previous_edges = _as_set(previous.get("edge_keys"))
    current_edges = _as_set(current.get("edge_keys"))
    previous_logic = previous.get("logic") if isinstance(previous.get("logic"), dict) else {}
    current_logic = current.get("logic") if isinstance(current.get("logic"), dict) else {}
    previous_attributes = previous.get("attributes") if isinstance(previous.get("attributes"), dict) else {}
    current_attributes = current.get("attributes") if isinstance(current.get("attributes"), dict) else {}

    added_objects = sorted(current_objects - previous_objects)
    removed_objects = sorted(previous_objects - current_objects)
    added_edges = sorted(current_edges - previous_edges)
    removed_edges = sorted(previous_edges - current_edges)

    logic_changes: list[dict[str, str | None]] = []
    for key in sorted(set(previous_logic) | set(current_logic)):
        before = previous_logic.get(key)
        after = current_logic.get(key)
        if before != after:
            logic_changes.append(
                {
                    "key": key,
                    "kind": key.split(":", 1)[0],
                    "before": before,
                    "after": after,
                }
            )

    attribute_changes: list[dict[str, str | None]] = []
    for key in sorted(set(previous_attributes) | set(current_attributes)):
        before = previous_attributes.get(key)
        after = current_attributes.get(key)
        if before != after:
            attribute_changes.append(
                {
                    "key": key,
                    "kind": key.split(":", 1)[0],
                    "before": before,
                    "after": after,
                }
            )

    counts_before = _counts(previous)
    counts_after = _counts(current)

    return {
        "changed": bool(
            added_objects
            or removed_objects
            or added_edges
            or removed_edges
            or logic_changes
            or attribute_changes
            or counts_before != counts_after
        ),
        "baseline": False,
        "added_objects": added_objects,
        "removed_objects": removed_objects,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
        "logic_changes": logic_changes,
        "attribute_changes": attribute_changes,
        "counts_before": counts_before,
        "counts_after": counts_after,
    }


def bump_version(current: str, changed: bool) -> str:
    """Bump major.minor.patch: minor on any detected change, patch otherwise."""
    if not current or current == "0.0.0":
        current = "1.0.0"
    try:
        major, minor, patch = (int(part) for part in current.split("."))
    except ValueError:
        major, minor, patch = 1, 0, 0
    if changed:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
