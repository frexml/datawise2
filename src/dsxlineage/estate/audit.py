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
        "hops": payload.get("hops"),
        "full_path": payload.get("full_path"),
        "payload": payload,
        "prev_hash": ev.prev_hash,
        "event_hash": ev.event_hash,
    }


def humanize_ledger(events: list[LedgerEvent], ir=None) -> list[dict[str, Any]]:
    return [humanize_event(ev, ir) for ev in events]
