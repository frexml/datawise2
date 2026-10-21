"""
Human-readable audit — translates ledger events into What/Who/When/Relationship.

Keeps underlying ledger_events untouched; this is a view layer for Praveen #3.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dsxlineage.estate.models import LedgerEvent


def _relative_when(dt: datetime | None) -> str:
    if not dt:
        return "unknown"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _relationship_for_payload(payload: dict, event_type: str) -> str:
    # Try to infer relationship from payload
    if event_type in ("survey_completed", "ir_generated"):
        nodes = payload.get("nodes", "?")
        return f"estate → {nodes} nodes"
    if event_type == "plan_approved":
        return f"plan {payload.get('plan_id')} → {payload.get('target')}"
    if event_type == "diff_run":
        return f"diff {payload.get('diff_run_id')} → {payload.get('status')}"
    if event_type == "continuity_checked":
        return f"continuity {payload.get('matched')}/{payload.get('total_columns')} matched"
    if event_type == "survey_todo_created" or event_type == "survey_approved":
        return f"survey → {payload.get('todo_count', '?')} steps"
    if event_type == "column_edited":
        tgt = payload.get("target", "?")
        src = payload.get("source", "?")
        return f"{src} → {tgt} ({payload.get('hops', '?')} hops)"
    if event_type == "human_review_pending":
        return f"message {payload.get('message_id')} → review (confidence {payload.get('confidence')})"
    if event_type == "human_review_decision":
        return f"message {payload.get('message_id')} → {payload.get('decision')} by {payload.get('reviewer')}"
    if event_type == "wedge_recommended":
        return f"wedge → {payload.get('target_platform')} ({len(payload.get('scope_fqns', []))} objects)"
    if event_type == "estate_rerun":
        changes = payload.get("changes") or {}
        return (
            f"estate → v{payload.get('version')} "
            f"({len(changes.get('added_objects', []))}+ / {len(changes.get('removed_objects', []))}- objects)"
        )
    if event_type == "remediation_proposed":
        return f"unresolved → {payload.get('item_count', 0)} AI proposals ({payload.get('model')})"
    if event_type == "remediation_decided":
        return (
            f"remediation → {len(payload.get('decisions', []))} decisions "
            f"by {payload.get('approver')}"
        )
    if event_type == "remediation_applied":
        return (
            f"lineage → {payload.get('applied_count', 0)} fixes applied "
            f"(v{payload.get('version')})"
        )
    # Generic fallback
    if "target_table" in payload and "target_column" in payload:
        return f"{payload['target_table']}.{payload['target_column']} → downstream"
    return payload.get("fqn") or payload.get("name") or "-"


def _what_for_event(ev: LedgerEvent) -> str:
    p = ev.payload or {}
    et = ev.event_type
    if et == "estate_created":
        return f"Created estate {p.get('name')} ({p.get('estate_type')})"
    if et == "connection_tested":
        return f"Tested connection to {p.get('host')}:{p.get('port')} ({p.get('latency_ms')}ms)"
    if et == "survey_todo_created":
        return f"Survey Todo created — {p.get('todo_count')} steps"
    if et == "survey_approved":
        return f"Approved survey — {p.get('todo_count')} steps"
    if et == "survey_completed":
        return f"Survey completed — {p.get('nodes')} nodes, {p.get('edges')} edges"
    if et == "ir_generated":
        return f"Generated IR v{p.get('version')} — {p.get('nodes')} nodes"
    if et == "graph_synced":
        return f"Synced graph for estate {p.get('estate_id')}"
    if et == "estate_rerun":
        changes = p.get("changes") or {}
        if changes.get("baseline"):
            return f"Re-ran estate v{p.get('version')} — baseline fingerprint recorded"
        logic = changes.get("logic_changes", [])
        attributes = changes.get("attribute_changes", [])
        logic_note = f", {len(logic)} logic change{'s' if len(logic) != 1 else ''}" if logic else ""
        attribute_note = (
            f", {len(attributes)} attribute change{'s' if len(attributes) != 1 else ''}"
            if attributes
            else ""
        )
        return (
            f"Re-ran estate v{p.get('version')} — "
            f"{len(changes.get('added_objects', []))} objects added, "
            f"{len(changes.get('removed_objects', []))} removed, "
            f"{len(changes.get('added_edges', []))}+/{len(changes.get('removed_edges', []))}- edges"
            f"{logic_note}{attribute_note}"
        )
    if et == "remediation_proposed":
        return f"Generated {p.get('item_count', 0)} remediation proposals ({p.get('model')}) — awaiting human approval"
    if et == "remediation_decided":
        decisions = p.get("decisions", [])
        approved = sum(1 for d in decisions if d.get("decision") == "approved")
        overridden = sum(1 for d in decisions if d.get("decision") == "overridden")
        rejected = sum(1 for d in decisions if d.get("decision") == "rejected")
        parts = [f"{approved} approved"]
        if overridden:
            parts.append(f"{overridden} overridden")
        parts.append(f"{rejected} rejected")
        return f"Remediation decision — {', '.join(parts)} by {p.get('approver')}"
    if et == "remediation_applied":
        before = p.get("before") or {}
        after = p.get("after") or {}
        return (
            f"Applied {p.get('applied_count', 0)} lineage fixes — v{p.get('version')}; "
            f"unresolved {before.get('unresolved', '?')} → {after.get('unresolved', '?')}"
        )
    if et == "plan_created":
        return f"Created migration plan {p.get('name')} → {p.get('target')}"
    if et == "plan_approved":
        ddl_preview = p.get("ddl_preview", "")[:60]
        return f"Approved plan {p.get('plan_id')} → {p.get('target')} (approver {p.get('approver')})"
    if et == "diff_run":
        return f"Diff run {p.get('diff_run_id')}: {p.get('status')} ({p.get('mismatched', 0)} mismatched) — {p.get('bound_95', '')[:20]}"
    if et == "continuity_checked":
        return f"Continuity: {p.get('matched')}/{p.get('total_columns')} matched, {p.get('requires_approval', 0)} need approval"
    if et == "promoted":
        return f"Promoted estate {p.get('estate_id')}"
    if et == "column_edited":
        return f"Edited {p.get('target')} — {p.get('source')} transform changed"
    if et == "chat_query":
        conf = p.get("confidence")
        suffix = f" (conf {conf})" if conf is not None else ""
        return f"Asked: {p.get('question', '')[:50]}{suffix}"
    if et == "human_review_pending":
        return f"Pending review — msg {p.get('message_id')}: {p.get('question', '')[:50]} (conf {p.get('confidence')})"
    if et == "human_review_decision":
        return f"Reviewed msg {p.get('message_id')} → {p.get('decision')} by {p.get('reviewer')}"
    if et == "wedge_recommended":
        return f"Recommended wedge → {p.get('target_platform')} ({len(p.get('scope_fqns', []))} objects, {p.get('estimated_days')}d)"
    # Generic
    return f"{et}: {str(p)[:80]}"


def _change_snippets(
    before: str | None, after: str | None, context: int = 60, tail: int = 120
) -> tuple[str, str]:
    """Return short before/after excerpts centred on the first differing region."""
    before = before or "(none)"
    after = after or "(none)"
    shorter = min(len(before), len(after))
    start = 0
    while start < shorter and before[start] == after[start]:
        start += 1
    end_before = len(before)
    end_after = len(after)
    while (
        end_before > start
        and end_after > start
        and before[end_before - 1] == after[end_after - 1]
    ):
        end_before -= 1
        end_after -= 1
    lo = max(0, start - context)
    before_snippet = before[lo : min(len(before), end_before + tail)]
    after_snippet = after[lo : min(len(after), end_after + tail)]
    prefix = "…" if lo > 0 else ""
    suffix_before = "…" if end_before + tail < len(before) else ""
    suffix_after = "…" if end_after + tail < len(after) else ""
    return f"{prefix}{before_snippet}{suffix_before}", f"{prefix}{after_snippet}{suffix_after}"


def _details_for_event(ev: LedgerEvent) -> list[str]:
    """Compact before/after detail lines for events that carry change payloads."""
    payload = ev.payload or {}
    details: list[str] = []
    if ev.event_type == "estate_rerun":
        changes = payload.get("changes") or {}
        for change in (changes.get("logic_changes") or [])[:8]:
            before, after = _change_snippets(change.get("before"), change.get("after"))
            details.append(f"{change.get('key')}: {before} → {after}")
        if len(changes.get("logic_changes") or []) > 8:
            details.append(f"… {len(changes['logic_changes']) - 8} more logic changes")
        for change in (changes.get("attribute_changes") or [])[:8]:
            before, after = _change_snippets(change.get("before"), change.get("after"))
            details.append(f"{change.get('key')}: {before} → {after}")
        if len(changes.get("attribute_changes") or []) > 8:
            details.append(f"… {len(changes['attribute_changes']) - 8} more attribute changes")
    elif ev.event_type == "remediation_proposed":
        for proposal in (payload.get("proposals") or [])[:6]:
            details.append(
                f"{proposal.get('fqn')} → {proposal.get('action')} "
                f"({round(float(proposal.get('confidence') or 0) * 100)}%)"
            )
        if len(payload.get("proposals") or []) > 6:
            details.append(f"… {len(payload['proposals']) - 6} more proposals")
    elif ev.event_type == "remediation_decided":
        for decision in payload.get("decisions") or []:
            details.append(
                f"{decision.get('proposal_id')} → {decision.get('decision')}"
                + (f" ({decision['note']})" if decision.get("note") else "")
            )
    elif ev.event_type == "remediation_applied":
        for effect in payload.get("proposals") or []:
            target = effect.get("target_fqn") or effect.get("subject_fqn")
            details.append(f"{effect.get('action')}: {effect.get('subject_fqn')} → {target}")
        before = payload.get("before") or {}
        after = payload.get("after") or {}
        details.append(
            f"unresolved {before.get('unresolved', '?')} → {after.get('unresolved', '?')} · "
            f"nodes {before.get('nodes', '?')} → {after.get('nodes', '?')} · "
            f"edges {before.get('edges', '?')} → {after.get('edges', '?')}"
        )
    return details


def humanize_event(ev: LedgerEvent, ir=None) -> dict[str, Any]:
    """Translate a single LedgerEvent to human-readable."""
    payload = ev.payload or {}
    # Enrich relationship with lineage hops if possible
    relationship = _relationship_for_payload(payload, ev.event_type)
    # If payload has target_table/column, try to enrich with downstream via ir
    if ir and "target_table" in payload and "target_column" in payload:
        try:
            tgt = f"{payload['target_table']}.{payload['target_column']}"
            # Find downstream via column lineage
            downstream = [f"{c.target_table}.{c.target_column}" for c in ir.column_lineage if c.source_table == payload["target_table"] and c.source_column == payload["target_column"]]
            if downstream:
                relationship = f"{tgt} → {', '.join(downstream[:2])} ({payload.get('hops', '?')} hops)"
        except Exception:
            pass

    return {
        "id": ev.id,
        "event_type": ev.event_type,
        "what": _what_for_event(ev),
        "who": ev.actor or "system",
        "who_id": ev.actor_id,
        "when": ev.created_at.isoformat() if ev.created_at else None,
        "when_relative": _relative_when(ev.created_at),
        "relationship": relationship,
        "details": _details_for_event(ev),
        "hops": payload.get("hops"),
        "full_path": payload.get("full_path"),
        "payload": payload,
        "prev_hash": ev.prev_hash,
        "event_hash": ev.event_hash,
    }


def humanize_ledger(events: list[LedgerEvent], ir=None) -> list[dict[str, Any]]:
    return [humanize_event(ev, ir) for ev in events]
