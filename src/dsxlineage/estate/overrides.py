"""Apply approved lineage recommendations to an extracted IR.

Recommendations are generated against unresolved/dynamic areas of the lineage.
Once a named human approves (or overrides) one, it is stored as a persistent
`LineageOverride` and re-applied on every read/extraction so the correction
survives re-runs, graph syncs and analytics recomputation.
"""

from __future__ import annotations

from dsxlineage.estate.ir import (
    EdgeType,
    EstateIR,
    LineageEdge,
    TableDef,
)

_ALIAS_ACTIONS = {"map_alias", "correct_reference"}
_STUB_ACTIONS = {"define_ddl", "confirm_external"}
_RUNTIME_ACTIONS = {"capture_runtime_sql"}


def known_fqns(ir: EstateIR) -> set[str]:
    """Public helper: every object FQN currently present in the IR."""
    return _all_fqns(ir)


def _all_fqns(ir: EstateIR) -> set[str]:
    fqns = {t.fqn for t in ir.tables}
    fqns.update(v.fqn for v in ir.views)
    fqns.update(p.fqn for p in ir.procedures)
    fqns.update(j.fqn for j in ir.etl_jobs)
    fqns.update(s.fqn for s in ir.schedules)
    fqns.update(d.fqn for d in ir.dashboards)
    fqns.update(r.fqn for r in ir.routines)
    return fqns


def _kind_of(ir: EstateIR, fqn: str) -> str:
    if any(t.fqn == fqn for t in ir.tables):
        return "table"
    if any(v.fqn == fqn for v in ir.views):
        return "view"
    if any(p.fqn == fqn for p in ir.procedures):
        return "procedure"
    if any(j.fqn == fqn for j in ir.etl_jobs):
        return "etl"
    if any(s.fqn == fqn for s in ir.schedules):
        return "schedule"
    if any(d.fqn == fqn for d in ir.dashboards):
        return "dashboard"
    if any(r.fqn == fqn for r in ir.routines):
        return "routine"
    return "unknown"


def _resolved_edge_type(ir: EstateIR, edge: LineageEdge) -> EdgeType:
    if edge.edge_type != EdgeType.UNRESOLVED:
        return edge.edge_type
    target_kind = _kind_of(ir, edge.target_fqn)
    source_kind = _kind_of(ir, edge.source_fqn)
    if target_kind == "view":
        return EdgeType.VIEW_DEPENDS
    if target_kind == "procedure":
        return EdgeType.SP_READS
    if target_kind == "etl":
        return EdgeType.ETL_READS
    if target_kind == "schedule":
        return EdgeType.SCHEDULE_DEPENDS
    if target_kind == "dashboard":
        return EdgeType.DASHBOARD_RENDERS
    if source_kind == "procedure":
        return EdgeType.SP_WRITES
    if source_kind == "etl":
        return EdgeType.ETL_WRITES
    return EdgeType.VIEW_DEPENDS


def _mark_resolved(ir: EstateIR, edge: LineageEdge, via: str) -> None:
    if edge.unresolved:
        edge.edge_type = _resolved_edge_type(ir, edge)
        edge.unresolved = False
        edge.unresolved_reason = None
        edge.confidence = 1.0
        edge.via = f"{edge.via or 'declared'} [{via}]"


def _remove_unresolved_ref(ir: EstateIR, fqn: str) -> None:
    ir.unresolved_refs = [ref for ref in ir.unresolved_refs if ref.fqn != fqn]


def _rewrite_fqn(ir: EstateIR, old: str, new: str) -> int:
    """Point every reference to ``old`` at ``new``; returns references touched."""
    touched = 0
    for edge in ir.edges:
        changed = False
        if edge.source_fqn == old:
            edge.source_fqn = new
            changed = True
        if edge.target_fqn == old:
            edge.target_fqn = new
            changed = True
        if changed:
            touched += 1
            _mark_resolved(ir, edge, "manual override")
    for view in ir.views:
        for index, source in enumerate(view.source_fqns):
            if source == old:
                view.source_fqns[index] = new
                touched += 1
    for proc in ir.procedures:
        for values in (proc.reads, proc.writes):
            for index, ref in enumerate(values):
                if ref == old:
                    values[index] = new
                    touched += 1
    for job in ir.etl_jobs:
        if job.source_fqn == old:
            job.source_fqn = new
            touched += 1
        if job.target_fqn == old:
            job.target_fqn = new
            touched += 1
        for values in (job.source_fqns, job.target_fqns):
            for index, ref in enumerate(values):
                if ref == old:
                    values[index] = new
                    touched += 1
    for schedule in ir.schedules:
        for index, ref in enumerate(schedule.depends_on):
            if ref == old:
                schedule.depends_on[index] = new
                touched += 1
    for dashboard in ir.dashboards:
        if dashboard.source_fqn == old:
            dashboard.source_fqn = new
            touched += 1
    for routine in ir.routines:
        for values in (routine.reads, routine.writes):
            for index, ref in enumerate(values):
                if ref == old:
                    values[index] = new
                    touched += 1
    for column in ir.column_lineage:
        if column.source_table == old:
            column.source_table = new
            touched += 1
        if column.target_table == old:
            column.target_table = new
            touched += 1
        if old in (column.full_path or ""):
            column.full_path = column.full_path.replace(old, new)
    for identity in ir.column_identities:
        if identity.fqn == old:
            identity.fqn = new
            touched += 1
        identity.aliases = [new if alias == old else alias for alias in identity.aliases]
    _remove_unresolved_ref(ir, old)
    return touched


def _add_stub_table(ir: EstateIR, fqn: str) -> None:
    schema = fqn.split(".")[0] if "." in fqn else fqn
    ir.tables.append(TableDef(fqn=fqn, system=schema, columns=[]))
    if schema not in ir.systems:
        ir.systems.append(schema)


def _clear_subject_unresolved(ir: EstateIR, fqn: str, via: str) -> int:
    cleared = 0
    for edge in ir.edges:
        if fqn in (edge.source_fqn, edge.target_fqn) and edge.unresolved:
            _mark_resolved(ir, edge, via)
            cleared += 1
    for view in ir.views:
        if view.fqn == fqn and view.unresolved:
            view.unresolved = False
            view.unresolved_reason = None
            cleared += 1
    for proc in ir.procedures:
        if proc.fqn == fqn and proc.unresolved:
            proc.unresolved = False
            proc.unresolved_reason = None
            cleared += 1
    for routine in ir.routines:
        if routine.fqn == fqn and routine.unresolved:
            routine.unresolved = False
            routine.unresolved_reason = None
            cleared += 1
    _remove_unresolved_ref(ir, fqn)
    return cleared


def _add_runtime_sources(ir: EstateIR, subject: str, sources: list[str]) -> int:
    known = _all_fqns(ir)
    subject_kind = _kind_of(ir, subject)
    if subject_kind == "procedure":
        edge_type = EdgeType.SP_READS
    elif subject_kind == "routine":
        edge_type = EdgeType.ETL_READS
    else:
        edge_type = EdgeType.VIEW_DEPENDS
    existing = {(edge.source_fqn, edge.target_fqn, edge.edge_type) for edge in ir.edges}
    added = 0
    for source in sources:
        if source not in known:
            continue
        key = (source, subject, edge_type)
        if key in existing:
            continue
        ir.edges.append(
            LineageEdge(
                source_fqn=source,
                target_fqn=subject,
                edge_type=edge_type,
                confidence=1.0,
                via="manual override (runtime SQL sources)",
            )
        )
        existing.add(key)
        added += 1
    return added


def _dedupe_edges(ir: EstateIR) -> None:
    best: dict[tuple[str, str, EdgeType], LineageEdge] = {}
    for edge in ir.edges:
        key = (edge.source_fqn, edge.target_fqn, edge.edge_type)
        current = best.get(key)
        if current is None or edge.confidence > current.confidence or (current.unresolved and not edge.unresolved):
            best[key] = edge
    ir.edges = list(best.values())


def apply_overrides(ir: EstateIR, overrides: list[dict]) -> tuple[EstateIR, list[dict]]:
    """Return a copy of ``ir`` with all approved overrides applied.

    Each override is a dict with ``action``, ``subject_fqn`` and optional
    ``target_fqn`` / ``sources`` / ``status``. Stale overrides (target no
    longer exists) are skipped and reported in the notes.
    """
    effective = ir.model_copy(deep=True)
    notes: list[dict] = []

    for override in overrides:
        action = str(override.get("action") or "")
        subject = str(override.get("subject_fqn") or "")
        target = str(override.get("target_fqn") or subject)
        sources = [str(source) for source in (override.get("sources") or []) if source]
        note: dict = {
            "proposal_id": override.get("proposal_id"),
            "action": action,
            "subject_fqn": subject,
            "target_fqn": target if action not in _RUNTIME_ACTIONS else None,
            "applied": False,
        }

        if not subject:
            note["reason"] = "missing subject"
            notes.append(note)
            continue

        if action in _ALIAS_ACTIONS:
            if target not in _all_fqns(effective):
                note["reason"] = f"target {target} no longer exists"
                notes.append(note)
                continue
            note["references_rewired"] = _rewrite_fqn(effective, subject, target)
            note["applied"] = True
        elif action in _STUB_ACTIONS:
            if target not in _all_fqns(effective):
                _add_stub_table(effective, target)
            if target != subject:
                _rewrite_fqn(effective, subject, target)
            note["unresolved_cleared"] = _clear_subject_unresolved(
                effective, target, "confirmed external" if action == "confirm_external" else "defined DDL"
            )
            note["applied"] = True
        elif action in _RUNTIME_ACTIONS:
            note["edges_added"] = _add_runtime_sources(effective, subject, sources)
            note["unresolved_cleared"] = _clear_subject_unresolved(effective, subject, "accepted runtime evidence")
            note["sources"] = sources
            note["applied"] = True
        else:
            note["reason"] = f"unsupported action {action}"
        notes.append(note)

    _dedupe_edges(effective)
    effective.compute_summaries()
    return effective, notes
