"""
Estate REST API - /api/estates/*

All new estate endpoints. Mounted at /api/estates by main.py.

Design:
  POST   /api/estates                  - create estate from synthetic (or catalog dump)
  POST   /api/estates/reset-demo       - wipe all demo estate data (confirm=true)
  GET    /api/estates                  - list estates
  GET    /api/estates/{id}             - estate detail + IR summary
  POST   /api/estates/{id}/extract     - run extractor -> IR -> KG sync -> ledger
  GET    /api/estates/{id}/ir          - full EstateIR
  GET    /api/estates/{id}/graph       - graph nodes+edges for ReactFlow
  GET    /api/estates/{id}/lineage     - lineage traversal ?fqn=&direction=&hops=
  GET    /api/estates/{id}/blast-radius - impact analysis ?fqn=
  GET    /api/estates/{id}/analytics   - all analytics
  POST   /api/estates/{id}/chat        - NL question -> grounded answer (session-aware)
  GET    /api/estates/{id}/chat/sessions - list sessions
  GET    /api/estates/{id}/ledger      - ledger events
  GET    /api/estates/{id}/ledger/verify - hash chain verify
  POST   /api/estates/{id}/bridge/recommend - recommend wedge
  POST   /api/estates/{id}/bridge/plan - create migration plan
  POST   /api/estates/{id}/bridge/plan/{planId}/approve - approve plan
  GET    /api/estates/{id}/bridge/plan/{planId}/ddl - generated DDL
  GET    /api/estates/{id}/bridge/plan/{planId}/terraform - generated Terraform
  POST   /api/estates/{id}/bridge/diff - run diff harness
  POST   /api/estates/{id}/bridge/continuity - run continuity check
  POST   /api/estates/{id}/bridge/promote - gated promote (checks ledger)
  GET    /api/estates/{id}/bridge/plans - list plans
  GET    /api/estates/{id}/bridge/diffs - list diff runs
"""

from __future__ import annotations

import json
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from dsxlineage.db.database import get_db
from dsxlineage.estate.analytics import compute_analytics
from dsxlineage.estate.bridge import (
    check_continuity,
    generate_snowflake_ddl,
    generate_terraform,
    recommend_wedge,
    run_diff_harness,
)
from dsxlineage.estate.chat import answer_question
from dsxlineage.estate.drift import bump_version, diff_fingerprints, fingerprint_ir
from dsxlineage.estate.extractor import extract_estate_ir
from dsxlineage.estate.graph import blast_radius as graph_blast_radius, delete_estate_from_graph
from dsxlineage.estate.ir import EstateIR
from dsxlineage.estate.ledger import append_ledger_event, can_promote, verify_ledger_chain
from dsxlineage.estate.models import (
    ChatMessage,
    ChatSession,
    DiffRun,
    Estate,
    EstateArtifact,
    LedgerEvent,
    LineageOverride,
    MigrationPlan,
)
from dsxlineage.estate.overrides import apply_overrides, known_fqns
from dsxlineage.estate.remediation import (
    DETERMINISTIC_MODEL,
    propose_remediations,
    unresolved_items,
)

router = APIRouter()

SYNTHETIC_ROOT = Path("data/synthetic_estate")
TELCO_ROOT = Path("data/synthetic_estate_telco")
LOAN_ROOT = Path("data/synthetic_estate_loan")
DATAWISE_ROOT = Path("data/external_estates/datawise_platform")

def _synthetic_root_for_estate(estate: Estate) -> Path:
    """Pick estate dir per estate_type — banking/telecom/loan/datawise."""
    et = getattr(estate, "estate_type", "banking")
    if et == "telecom":
        return TELCO_ROOT if TELCO_ROOT.exists() else SYNTHETIC_ROOT
    if et == "loan_onboarding":
        return LOAN_ROOT if LOAN_ROOT.exists() else SYNTHETIC_ROOT
    if et == "datawise_platform":
        return DATAWISE_ROOT
    return SYNTHETIC_ROOT


# In-process cache for the (expensive - sqlglot column lineage + full DDL/view/
# procedure parse) extracted IR. Keyed by (estate.id, name, estate_type,
# ir_version) - id+version captures "valid until the next re-survey" in prod,
# while name+type disambiguate isolated test DBs that reuse id=1 across
# separate in-memory engines in the same process. Read-only endpoints should
# use _get_ir(); endpoints that are themselves computing a fresh IR (those
# write paths) call extract_estate_ir() directly as before.
_IR_CACHE: dict[tuple, EstateIR] = {}


def _override_dicts(db: Session, estate_id: int) -> list[dict]:
    """Serialize persistent lineage overrides for the pure apply function."""
    rows = (
        db.query(LineageOverride)
        .filter(LineageOverride.estate_id == estate_id)
        .order_by(LineageOverride.id.asc())
        .all()
    )
    return [
        {
            "proposal_id": row.proposal_id,
            "action": row.action,
            "subject_fqn": row.subject_fqn,
            "target_fqn": row.target_fqn,
            "sources": list(row.sources or []),
            "status": row.status,
        }
        for row in rows
    ]


def _get_ir(estate: Estate, db: Session) -> EstateIR:
    """Extract once per (estate, version, override revision), then apply overrides."""
    revision = (
        db.query(LineageOverride.id)
        .filter(LineageOverride.estate_id == estate.id)
        .order_by(LineageOverride.id.desc())
        .first()
    )
    revision_id = revision[0] if revision else 0
    key = (
        estate.id,
        getattr(estate, "name", ""),
        getattr(estate, "estate_type", ""),
        estate.ir_version or "",
        revision_id,
    )
    cached = _IR_CACHE.get(key)
    if cached is None:
        base_ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
        cached, _ = apply_overrides(base_ir, _override_dicts(db, estate.id))
        _IR_CACHE[key] = cached
    return cached


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EstateCreate(BaseModel):
    name: str = "NORTHSTAR"
    display_name: str = "NorthStar Synthetic Estate"
    source_type: str = "synthetic"  # synthetic | catalog_dump | git
    estate_type: str = "banking"  # banking | telecom | loan_onboarding | datawise_platform
    synthetic_path: str | None = None  # override path if needed
    connection: dict | None = None  # {host, port, user, db_type} - mock for demo
    defer_discovery: bool = False  # True: skip auto-extract, leave PENDING for a real survey/approve flow


class ResetDemoRequest(BaseModel):
    confirm: bool = False  # destructive: must be explicitly true


class ConnectionTestRequest(BaseModel):
    host: str = "oracle-prod.example.local"
    port: int = 1521
    user: str = "ESTATE_RO"
    db_type: str = "oracle"  # oracle | teradata | postgres


class SurveyRequest(BaseModel):
    synthetic_path: str | None = None


class SurveyApprove(BaseModel):
    approver: str  # named approver, not free-text; validated non-empty - same rule as PlanApprove
    actor_email: str | None = None  # for ledger actor_email_hash + IdP mock


class ChatRequest(BaseModel):
    question: str
    session_id: int | None = None
    use_llm: bool | None = None
    human_loop: bool = False
    generate_sql: bool = False
    explain: bool = False


class PlanCreate(BaseModel):
    name: str = "Snowflake Wedge - Finance Mart"
    target_platform: Literal["snowflake", "databricks", "aws", "microsoft", "other"] = "snowflake"
    scope_fqns: list[str] | None = None


class BridgeRecommendRequest(BaseModel):
    target: Literal["snowflake", "databricks", "aws", "microsoft", "other"] = "snowflake"


class ChatFeedbackRequest(BaseModel):
    message_id: int
    decision: Literal["approve", "reject", "edit"] = "approve"
    comment: str | None = None
    reviewer: str | None = None


class PlanApprove(BaseModel):
    approver: str  # named approver, not free-text; validated non-empty
    comment: str | None = None
    actor_email: str | None = None  # for ledger actor_email_hash + IdP mock


class DiffRequest(BaseModel):
    scope_fqns: list[str] | None = None
    sampling: Literal["full", "hash_stratified"] = "full"
    n: int | None = None


class ContinuityRequest(BaseModel):
    scope_fqns: list[str] | None = None


# ---------------------------------------------------------------------------
# Estates CRUD
# ---------------------------------------------------------------------------

def _estate_to_dict(e: Estate) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "display_name": e.display_name,
        "source_type": e.source_type,
        "estate_type": getattr(e, "estate_type", "banking"),
        "status": e.status,
        "connection": getattr(e, "connection", None),
        "survey_todo": getattr(e, "survey_todo", None),
        "survey_preview": getattr(e, "survey_preview", None),
        "current_stage": getattr(e, "current_stage", None),
        "last_survey_at": e.last_survey_at.isoformat() if getattr(e, "last_survey_at", None) else None,
        "ir_version": e.ir_version,
        "ir_generated_at": e.ir_generated_at.isoformat() if e.ir_generated_at else None,
        "node_count": e.node_count,
        "edge_count": e.edge_count,
        "unresolved_count": e.unresolved_count,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def _bucket_estimate(n: int) -> str:
    """Round down to the nearest 25 - a ballpark for the approval-gate
    preview, deliberately never the exact final count."""
    return f"{(n // 25) * 25}+" if n >= 25 else f"~{n}"


def _build_survey_todo(ir: EstateIR) -> list[dict]:
    """Build 8-item Todo from IR counts - mirrors Plan's Survey -> Todo."""
    return [
        {"key": "discover_systems", "label": "Discover systems", "status": "done", "count": len(ir.systems), "detail": f"{', '.join(ir.systems)}"},
        {"key": "extract_tables", "label": "Extract tables", "status": "done", "count": len(ir.tables), "detail": f"{len(ir.tables)} tables"},
        {"key": "parse_views", "label": "Parse views", "status": "done", "count": len(ir.views), "detail": f"{len(ir.views)} views ({sum(1 for v in ir.views if v.unresolved)} unresolved)"},
        {"key": "analyze_procedures", "label": "Analyze procedures", "status": "done", "count": len(ir.procedures), "detail": f"{len(ir.procedures)} SPs ({sum(1 for p in ir.procedures if p.has_dynamic or p.has_cursor)} unresolved)"},
        {"key": "map_etl", "label": "Map ETL & schedules", "status": "done", "count": len(ir.etl_jobs) + len(ir.schedules), "detail": f"{len(ir.etl_jobs)} ETL + {len(ir.schedules)} schedules"},
        {"key": "map_bi", "label": "Map BI", "status": "done", "count": len(ir.dashboards), "detail": f"{len(ir.dashboards)} dashboards"},
        {"key": "build_graph", "label": "Build knowledge graph", "status": "done", "count": ir.total_nodes, "detail": f"{ir.total_nodes} nodes · {ir.total_edges} edges"},
        {"key": "compute_analytics", "label": "Compute analytics", "status": "done", "count": 1, "detail": f"Health + quadrant + blast p50"},
    ]


def _latest_run_fingerprint(db: Session, estate_id: int) -> dict[str, object] | None:
    """Most recent IR fingerprint recorded in the ledger for this estate."""
    from dsxlineage.estate.models import LedgerEvent

    events = (
        db.query(LedgerEvent)
        .filter(
            LedgerEvent.estate_id == estate_id,
            LedgerEvent.event_type.in_(
                ("ir_generated", "survey_completed", "estate_rerun", "remediation_applied")
            ),
        )
        .order_by(LedgerEvent.id.desc())
        .limit(20)
        .all()
    )
    for event in events:
        fingerprint = (event.payload or {}).get("fingerprint")
        if isinstance(fingerprint, dict):
            return fingerprint
    return None


def _rerun_estate(
    db: Session,
    estate: Estate,
    actor: str = "system",
    actor_email: str | None = None,
) -> dict[str, object]:
    """Re-ingest an existing estate, diff against the last recorded run, log it.

    The new IR fingerprint and the full added/removed object/edge lists are
    appended to the ledger as an ``estate_rerun`` event (hash-chained).
    """
    root = _synthetic_root_for_estate(estate)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Estate source not found on disk")

    base_ir = extract_estate_ir(root, estate_name=estate.name)
    override_rows = _override_dicts(db, estate.id)
    ir, override_notes = apply_overrides(base_ir, override_rows)
    current = fingerprint_ir(ir)
    previous = _latest_run_fingerprint(db, estate.id)
    changes = diff_fingerprints(previous, current)
    previous_version = estate.ir_version or "0.0.0"
    version = bump_version(previous_version, bool(changes["changed"]))

    estate.ir_version = version
    estate.ir_generated_at = datetime.now(timezone.utc)
    estate.node_count = ir.total_nodes
    estate.edge_count = ir.total_edges
    estate.unresolved_count = ir.unresolved_count
    estate.survey_todo = _build_survey_todo(ir)
    estate.current_stage = "done"
    estate.last_survey_at = datetime.now(timezone.utc)
    estate.status = "ACTIVE"
    db.commit()

    event = append_ledger_event(
        db,
        estate.id,
        "estate_rerun",
        {
            "previous_version": previous_version,
            "version": version,
            "nodes": ir.total_nodes,
            "edges": ir.total_edges,
            "unresolved": ir.unresolved_count,
            "changes": changes,
            "fingerprint": current,
            "overrides_applied": sum(1 for note in override_notes if note.get("applied")),
            "overrides_stale": sum(1 for note in override_notes if note.get("reason") and not note.get("applied")),
        },
        actor=actor,
        actor_email=actor_email,
    )
    db.commit()

    try:
        from dsxlineage.estate.graph import sync_estate_to_graph

        sync_estate_to_graph(estate.id, estate.name, ir)
        append_ledger_event(
            db,
            estate.id,
            "graph_synced",
            {"estate_id": estate.id, "rerun": True},
            actor=actor,
            actor_email=actor_email,
        )
        db.commit()
    except Exception as exc:
        print(f"Warning: graph sync failed for estate {estate.id}: {exc}")

    return {
        "estate_id": estate.id,
        "rerun": True,
        "ir_version": version,
        "previous_version": previous_version,
        "changes": changes,
        "ledger_event_id": event.id,
    }


@router.post("")
def create_estate(payload: EstateCreate, db: Session = Depends(get_db)):
    existing = db.query(Estate).filter(Estate.name == payload.name).first()
    if existing:
        summary = _rerun_estate(db, existing)
        result = _estate_to_dict(existing)
        result.update(
            {
                "rerun": True,
                "previous_version": summary["previous_version"],
                "changes": summary["changes"],
                "ledger_event_id": summary["ledger_event_id"],
            }
        )
        return result
    estate = Estate(
        name=payload.name,
        display_name=payload.display_name,
        source_type=payload.source_type,
        estate_type=payload.estate_type,
        status="PENDING",  # wait for test-connection -> survey
        connection=payload.connection or {},
        survey_todo=[],
        current_stage="pending",
        ir_version="0.0.0",
    )
    db.add(estate)
    db.commit()
    db.refresh(estate)

    # Auto-extract for backward compat (tests create with synthetic and expect immediate IR).
    # Skipped when defer_discovery=True so the interactive demo flow goes through a real
    # pending_approval -> approve -> staged survey instead of landing on ACTIVE/done instantly.
    if not payload.defer_discovery:
        if payload.synthetic_path:
            synth_path = Path(payload.synthetic_path)
        elif payload.estate_type == "telecom":
            synth_path = TELCO_ROOT
        elif payload.estate_type == "loan_onboarding":
            synth_path = LOAN_ROOT
        elif payload.estate_type == "datawise_platform":
            synth_path = DATAWISE_ROOT
        else:
            synth_path = SYNTHETIC_ROOT
        if payload.source_type in ("synthetic", "git") and synth_path.exists():
            try:
                ir = extract_estate_ir(synth_path, estate_name=estate.name)
                estate.ir_version = "1.0.0"
                estate.ir_generated_at = datetime.now(timezone.utc)
                estate.node_count = ir.total_nodes
                estate.edge_count = ir.total_edges
                estate.unresolved_count = ir.unresolved_count
                estate.survey_todo = _build_survey_todo(ir)
                estate.current_stage = "done"
                estate.last_survey_at = datetime.now(timezone.utc)
                estate.status = "ACTIVE"
                db.commit()
                append_ledger_event(db, estate.id, "ir_generated", {"version": ir.version, "nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count, "estate_type": payload.estate_type, "fingerprint": fingerprint_ir(ir)})
                try:
                    from dsxlineage.estate.graph import sync_estate_to_graph
                    sync_estate_to_graph(estate.id, estate.name, ir)
                    append_ledger_event(db, estate.id, "graph_synced", {"estate_id": estate.id})
                except Exception as e:
                    print(f"Warning: graph sync failed for estate {estate.id}: {e}")
                db.commit()
            except Exception as e:
                print(f"Warning: auto-extract failed for estate {estate.id}: {e}")

    append_ledger_event(db, estate.id, "estate_created", {"name": estate.name, "source_type": estate.source_type, "estate_type": payload.estate_type})
    db.commit()
    db.refresh(estate)
    return _estate_to_dict(estate)


@router.post("/reset-demo")
def reset_demo(payload: ResetDemoRequest, db: Session = Depends(get_db)):
    """Wipe every estate and its evidence so the demo can start from scratch.

    Deletes estates + ledger, artifacts, plans, diff runs, chat sessions and
    messages. Graph mirrors are removed best-effort. Schema is preserved.
    Requires ``{"confirm": true}`` - this is irreversible.
    """
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Reset requires confirm=true")

    estates = db.query(Estate).all()
    estate_ids = [e.id for e in estates]
    for estate_id in estate_ids:
        delete_estate_from_graph(estate_id)

    chat_messages_removed = db.query(ChatMessage).delete(synchronize_session=False)
    chat_sessions_removed = db.query(ChatSession).delete(synchronize_session=False)
    diff_runs_removed = db.query(DiffRun).delete(synchronize_session=False)
    plans_removed = db.query(MigrationPlan).delete(synchronize_session=False)
    overrides_removed = db.query(LineageOverride).delete(synchronize_session=False)
    ledger_removed = db.query(LedgerEvent).delete(synchronize_session=False)
    artifacts_removed = db.query(EstateArtifact).delete(synchronize_session=False)
    estates_removed = db.query(Estate).delete(synchronize_session=False)
    db.commit()
    _IR_CACHE.clear()

    return {
        "reset": True,
        "estates_removed": estates_removed,
        "ledger_events_removed": ledger_removed,
        "artifacts_removed": artifacts_removed,
        "plans_removed": plans_removed,
        "overrides_removed": overrides_removed,
        "diff_runs_removed": diff_runs_removed,
        "chat_sessions_removed": chat_sessions_removed,
        "chat_messages_removed": chat_messages_removed,
    }


# ---------------------------------------------------------------------------
# Mock Connection + Survey (new workflow: connect -> survey -> Todo live)
# ---------------------------------------------------------------------------

@router.post("/{estate_id}/test-connection")
def test_connection(estate_id: int, payload: ConnectionTestRequest, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    # Mock connection test - always succeeds after 400-800ms unless host == "fail"
    import time, random
    time.sleep(0.4 + random.random() * 0.4)
    if "fail" in payload.host.lower():
        raise HTTPException(status_code=502, detail=f"Could not connect to {payload.host}:{payload.port} - host unreachable (mock)")
    conn = {
        "host": payload.host,
        "port": payload.port,
        "user": payload.user,
        "db_type": payload.db_type,
        "status": "connected",
        "latency_ms": int(400 + random.random() * 400),
        "last_tested_at": datetime.now(timezone.utc).isoformat(),
    }
    estate.connection = conn
    estate.current_stage = "connected"
    db.commit()
    append_ledger_event(db, estate_id, "connection_tested", {"host": payload.host, "port": payload.port, "latency_ms": conn["latency_ms"]})
    db.commit()
    return {"estate_id": estate_id, "status": "connected", "connection": conn}


@router.post("/{estate_id}/survey")
def start_survey(estate_id: int, payload: SurveyRequest = SurveyRequest(), db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    if not estate.connection or estate.connection.get("status") != "connected":
        if estate.source_type not in ("synthetic", "git"):
            raise HTTPException(status_code=409, detail="Test connection first - POST /test-connection")
    if estate.survey_todo and all(t.get("status") == "done" for t in estate.survey_todo):
        return {"estate_id": estate_id, "status": "done", "todo": estate.survey_todo}
    if estate.status == "ACTIVE" and estate.current_stage == "done" and estate.survey_todo:
        return {"estate_id": estate_id, "status": "done", "todo": estate.survey_todo}

    # Create Todo as pending_approval — user must approve before execution
    todo_pending = [
        {"key": "discover_systems", "label": "Discover systems", "status": "pending", "count": None, "detail": ""},
        {"key": "extract_tables", "label": "Extract tables", "status": "pending", "count": None, "detail": ""},
        {"key": "parse_views", "label": "Parse views", "status": "pending", "count": None, "detail": ""},
        {"key": "analyze_procedures", "label": "Analyze procedures", "status": "pending", "count": None, "detail": ""},
        {"key": "map_etl", "label": "Map ETL & schedules", "status": "pending", "count": None, "detail": ""},
        {"key": "map_bi", "label": "Map BI", "status": "pending", "count": None, "detail": ""},
        {"key": "build_graph", "label": "Build knowledge graph", "status": "pending", "count": None, "detail": ""},
        {"key": "compute_analytics", "label": "Compute analytics", "status": "pending", "count": None, "detail": ""},
    ]
    estate.survey_todo = todo_pending
    estate.current_stage = "pending_approval"
    estate.status = "PENDING_APPROVAL"
    # Coarse preview for the gate - systems + a rounded estimate, never the exact
    # count. Best-effort: a parse hiccup here must never block reaching the gate.
    try:
        synth = payload.synthetic_path or str(_synthetic_root_for_estate(estate))
        sp = Path(synth) if Path(synth).exists() else _synthetic_root_for_estate(estate)
        preview_ir = extract_estate_ir(sp, estate_name=estate.name)
        estate.survey_preview = {"systems": preview_ir.systems, "estimated_objects": _bucket_estimate(preview_ir.total_nodes)}
    except Exception:
        estate.survey_preview = {"systems": [], "estimated_objects": None}
    db.commit()
    # No per-stage counts yet — step labels + the coarse preview above only.
    # Per-stage counts/details are revealed as each one completes during
    # approve_survey's staged run, not spoiled upfront here.
    append_ledger_event(db, estate_id, "survey_todo_created", {"todo_count": len(todo_pending), "estate_type": estate.estate_type})
    db.commit()
    return {"estate_id": estate_id, "status": "pending_approval", "todo": todo_pending, "survey_preview": estate.survey_preview}


@router.post("/{estate_id}/survey/approve")
def approve_survey(estate_id: int, payload: SurveyApprove, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    if estate.current_stage not in ("pending_approval", "surveying", "PENDING"):
        # Allow re-approve if already surveying
        if estate.status == "ACTIVE" and estate.current_stage == "done":
            return {"estate_id": estate_id, "status": "done", "todo": estate.survey_todo}
    if not estate.survey_todo:
        raise HTTPException(status_code=409, detail="No survey Todo to approve — POST /survey first")
    if not payload.approver or not payload.approver.strip():
        raise HTTPException(status_code=400, detail="Approver name is required (not free-text reviewer, named approver)")
    if len(payload.approver.strip()) < 2:
        raise HTTPException(status_code=400, detail="Approver name must be at least 2 characters")
    approver = payload.approver.strip()

    estate.current_stage = "surveying"
    estate.status = "PENDING"
    db.commit()
    append_ledger_event(
        db, estate_id, "survey_approved", {"todo_count": len(estate.survey_todo), "approver": approver},
        actor=approver, actor_email=payload.actor_email,
    )
    db.commit()

    estate_name = estate.name
    estate_type = estate.estate_type
    bind_engine = db.get_bind()
    # Use file-based path for synthetic per estate_type
    synth = str(
        TELCO_ROOT
        if estate_type == "telecom"
        else LOAN_ROOT
        if estate_type == "loan_onboarding"
        else DATAWISE_ROOT
        if estate_type == "datawise_platform"
        else SYNTHETIC_ROOT
    )

    import threading

    def _run_bg(eid: int, ename: str, etype: str, synth_path: str, engine, actor: str, actor_email: str | None):
        import time
        from sqlalchemy.orm import sessionmaker
        # 5s initial view - user sees the full Todo list before ticking (as requested)
        time.sleep(5)
        Session2 = sessionmaker(bind=engine)
        db2 = Session2()
        try:
            # Per-stage durations weighted by how "heavy" the stage should feel — not
            # uniform ticks. Procedure/ETL analysis reads as the hard part; discovery
            # and the final rollup are quick.
            stage_seconds = {
                "discover_systems": 5,
                "extract_tables": 6,
                "parse_views": 8,
                "analyze_procedures": 10,
                "map_etl": 9,
                "map_bi": 6,
                "build_graph": 7,
                "compute_analytics": 5,
            }
            order = ["discover_systems", "extract_tables", "parse_views", "analyze_procedures", "map_etl", "map_bi", "build_graph", "compute_analytics"]
            sp_path = Path(synth_path) if Path(synth_path).exists() else SYNTHETIC_ROOT
            try:
                ir = extract_estate_ir(sp_path, estate_name=ename)
                base = _build_survey_todo(ir)  # real counts/details, pulled in only as each stage completes
            except Exception:
                base = [
                    {"key": k, "label": k.replace("_", " ").title(), "status": "done", "count": None, "detail": ""}
                    for k in order
                ]
            blank_template = [dict(item, status="pending", count=None, detail="") for item in base]

            from sqlalchemy.orm.attributes import flag_modified

            def _snapshot(done_idx: int, running_idx: int | None) -> list[dict]:
                snap = []
                for j, item in enumerate(blank_template):
                    new_item = dict(item)
                    if j <= done_idx:
                        new_item["status"] = "done"
                        new_item["count"] = base[j]["count"]
                        new_item["detail"] = base[j]["detail"]
                    elif j == running_idx:
                        new_item["status"] = "running"
                    snap.append(new_item)
                return snap

            for idx, key in enumerate(order):
                est = db2.query(Estate).filter(Estate.id == eid).first()
                if not est:
                    break
                # Mark this stage running first (no count/detail yet - nothing to reveal mid-flight)
                est.survey_todo = _snapshot(done_idx=idx - 1, running_idx=idx)
                flag_modified(est, "survey_todo")
                est.current_stage = key
                db2.commit()

                time.sleep(stage_seconds[key])

                est = db2.query(Estate).filter(Estate.id == eid).first()
                if not est:
                    break
                # Flip to done and reveal this stage's real count/detail
                est.survey_todo = _snapshot(done_idx=idx, running_idx=None)
                flag_modified(est, "survey_todo")
                db2.commit()
            est = db2.query(Estate).filter(Estate.id == eid).first()
            if est:
                sp_path = Path(synth_path) if Path(synth_path).exists() else SYNTHETIC_ROOT
                ir = extract_estate_ir(sp_path, estate_name=ename)
                est.survey_todo = _build_survey_todo(ir)
                for it in est.survey_todo:
                    it["status"] = "done"
                flag_modified(est, "survey_todo")
                est.current_stage = "done"
                est.last_survey_at = datetime.now(timezone.utc)
                est.ir_version = "1.0.0"
                est.ir_generated_at = datetime.now(timezone.utc)
                est.node_count = ir.total_nodes
                est.edge_count = ir.total_edges
                est.unresolved_count = ir.unresolved_count
                est.status = "ACTIVE"
                db2.commit()
                append_ledger_event(
                    db2, eid, "survey_completed", {"nodes": ir.total_nodes, "edges": ir.total_edges, "estate_type": etype, "approver": actor, "fingerprint": fingerprint_ir(ir)},
                    actor=actor, actor_email=actor_email,
                )
                try:
                    from dsxlineage.estate.graph import sync_estate_to_graph
                    sync_estate_to_graph(eid, ename, ir)
                    append_ledger_event(db2, eid, "graph_synced", {"estate_id": eid}, actor=actor, actor_email=actor_email)
                except Exception as e:
                    print(f"Survey graph sync failed: {e}")
                db2.commit()
        finally:
            db2.close()

    t = threading.Thread(target=_run_bg, args=(estate_id, estate_name, estate_type, synth, bind_engine, approver, payload.actor_email), daemon=True)
    t.start()
    return {"estate_id": estate_id, "status": "surveying", "todo": estate.survey_todo}


@router.get("/{estate_id}/survey/todo")
def get_survey_todo(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    return {"estate_id": estate_id, "current_stage": estate.current_stage, "status": estate.status, "todo": estate.survey_todo or [], "survey_preview": estate.survey_preview, "counts": {"nodes": estate.node_count, "edges": estate.edge_count, "unresolved": estate.unresolved_count}}


@router.get("")
def list_estates(db: Session = Depends(get_db)):
    estates = db.query(Estate).order_by(Estate.created_at.desc()).all()
    return [_estate_to_dict(e) for e in estates]


@router.get("/{estate_id}")
def get_estate(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    return _estate_to_dict(estate)


@router.post("/{estate_id}/extract")
def extract_estate(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    if not _synthetic_root_for_estate(estate).exists():
        raise HTTPException(status_code=404, detail="Synthetic estate not found on disk")

    ir = _get_ir(estate, db)
    estate.ir_version = ir.version
    estate.ir_generated_at = datetime.now(timezone.utc)
    estate.node_count = ir.total_nodes
    estate.edge_count = ir.total_edges
    estate.unresolved_count = ir.unresolved_count
    db.commit()
    ev = append_ledger_event(db, estate_id, "ir_generated", {"version": ir.version, "nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count, "fingerprint": fingerprint_ir(ir)})
    db.commit()

    # Graph sync best-effort
    try:
        from dsxlineage.estate.graph import sync_estate_to_graph
        sync_estate_to_graph(estate_id, estate.name, ir)
        append_ledger_event(db, estate_id, "graph_synced", {"estate_id": estate_id})
        db.commit()
    except Exception as e:
        print(f"Warning: graph sync failed: {e}")

    return {"estate_id": estate_id, "ir_version": ir.version, "nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count, "ledger_event_id": ev.id}


@router.post("/{estate_id}/rerun")
def rerun_estate(estate_id: int, db: Session = Depends(get_db)):
    """Re-ingest an existing estate and log the delta vs the last run."""
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    summary = _rerun_estate(db, estate)
    return {**summary, "estate": _estate_to_dict(estate)}


# ---------------------------------------------------------------------------
# Unresolved references - AI remediation proposals (human approval required)
# ---------------------------------------------------------------------------

class RemediationRecommendRequest(BaseModel):
    use_ai: bool | None = None  # None = auto (LLM when OPENROUTER_API_KEY is set)


class RemediationOverride(BaseModel):
    target_fqn: str | None = None
    sources: list[str] = Field(default_factory=list)
    note: str | None = None


class RemediationDecisionItem(BaseModel):
    proposal_id: str
    decision: Literal["approved", "rejected", "overridden"]
    override: RemediationOverride | None = None
    note: str | None = None


class RemediationDecideRequest(BaseModel):
    approver: str
    decisions: list[RemediationDecisionItem]
    actor_email: str | None = None


def _unresolved_state(db: Session, estate: Estate) -> dict:
    """Unresolved items + latest proposals with their human decisions applied."""
    from dsxlineage.estate.models import LedgerEvent

    ir = _get_ir(estate, db)
    items = unresolved_items(ir)
    events = (
        db.query(LedgerEvent)
        .filter(
            LedgerEvent.estate_id == estate.id,
            LedgerEvent.event_type.in_(("remediation_proposed", "remediation_decided")),
        )
        .order_by(LedgerEvent.id.asc())
        .all()
    )

    latest_proposed = None
    decisions: dict[str, dict] = {}
    for event in events:
        payload = event.payload or {}
        if event.event_type == "remediation_proposed":
            latest_proposed = event
        else:
            for decision in payload.get("decisions", []):
                decisions[str(decision.get("proposal_id", ""))] = {
                    "status": decision.get("decision"),
                    "decided_by": payload.get("approver"),
                    "decided_at": event.created_at.isoformat() if event.created_at else None,
                    "note": decision.get("note"),
                }

    proposals: list[dict] = []
    model = None
    proposed_at = None
    if latest_proposed:
        payload = latest_proposed.payload or {}
        proposals = [dict(proposal) for proposal in payload.get("proposals", [])]
        model = payload.get("model")
        proposed_at = latest_proposed.created_at.isoformat() if latest_proposed.created_at else None
        for proposal in proposals:
            proposal.update(decisions.get(str(proposal.get("id", "")), {"status": "pending"}))
            proposal.setdefault("status", "pending")

    applied = [
        {
            "proposal_id": row.proposal_id,
            "action": row.action,
            "subject_fqn": row.subject_fqn,
            "target_fqn": row.target_fqn,
            "sources": list(row.sources or []),
            "status": row.status,
            "approver": row.approver,
            "applied_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in db.query(LineageOverride)
        .filter(LineageOverride.estate_id == estate.id)
        .order_by(LineageOverride.id.asc())
        .all()
    ]

    current_fqns = {item["fqn"] for item in items}
    covered_fqns = {str(proposal.get("fqn")) for proposal in proposals}
    pending_fqns = {
        str(proposal.get("fqn"))
        for proposal in proposals
        if proposal.get("status") == "pending"
    }
    stale = bool(pending_fqns - current_fqns) or bool(current_fqns - covered_fqns)

    return {
        "estate_id": estate.id,
        "counts": {
            "total": len(items),
            "missing_objects": sum(1 for item in items if item["kind"] == "missing_object"),
            "unresolved_views": sum(1 for item in items if item["kind"] == "unresolved_view"),
            "unresolved_procedures": sum(1 for item in items if item["kind"] == "unresolved_procedure"),
            "unresolved_routines": sum(1 for item in items if item["kind"] == "unresolved_routine"),
            "applied_overrides": len(applied),
        },
        "items": items,
        "proposals": proposals,
        "applied": applied,
        "stale": stale,
        "model": model,
        "proposed_at": proposed_at,
        "requires_approval": True,
    }


@router.get("/{estate_id}/unresolved")
def get_unresolved(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    return _unresolved_state(db, estate)


@router.post("/{estate_id}/unresolved/recommend")
def recommend_unresolved(
    estate_id: int,
    payload: RemediationRecommendRequest = RemediationRecommendRequest(),
    db: Session = Depends(get_db),
):
    """Generate AI remediation proposals and record them in the ledger."""
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = _get_ir(estate, db)
    proposals = propose_remediations(ir, use_ai=payload.use_ai)
    model = proposals[0].model_used if proposals else DETERMINISTIC_MODEL
    append_ledger_event(
        db,
        estate_id,
        "remediation_proposed",
        {
            "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
            "model": model,
            "generated_on": date.today().isoformat(),
            "item_count": len(proposals),
        },
    )
    db.commit()
    return _unresolved_state(db, estate)


@router.post("/{estate_id}/unresolved/decide")
def decide_unresolved(
    estate_id: int,
    payload: RemediationDecideRequest,
    db: Session = Depends(get_db),
):
    """Record a named human decision and apply approved/overridden lineage fixes.

    Rejections are recorded only. Approved proposals become persistent
    ``LineageOverride`` patches: aliases are re-pointed, missing objects are
    confirmed/defined, runtime sources are attached. The effective IR, graph,
    estate counts and ledger are updated end to end.
    """
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    approver = (payload.approver or "").strip()
    if len(approver) < 2:
        raise HTTPException(status_code=400, detail="Approver name is required (min 2 characters)")
    if not payload.decisions:
        raise HTTPException(status_code=400, detail="At least one decision is required")

    from dsxlineage.estate.models import LedgerEvent

    latest = (
        db.query(LedgerEvent)
        .filter(
            LedgerEvent.estate_id == estate_id,
            LedgerEvent.event_type == "remediation_proposed",
        )
        .order_by(LedgerEvent.id.desc())
        .first()
    )
    if not latest:
        raise HTTPException(status_code=409, detail="Generate remediation proposals first")
    proposals_by_id = {
        str(proposal.get("id")): proposal
        for proposal in (latest.payload or {}).get("proposals", [])
    }
    known_ids = set(proposals_by_id)
    unknown = [item.proposal_id for item in payload.decisions if item.proposal_id not in known_ids]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown proposal ids: {', '.join(unknown)}")

    base_ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    known = known_fqns(base_ir)
    for decision in payload.decisions:
        proposal = proposals_by_id[decision.proposal_id]
        action = str(proposal.get("action") or "")
        subject = str(proposal.get("fqn") or "")
        existing = (
            db.query(LineageOverride)
            .filter(
                LineageOverride.estate_id == estate_id,
                LineageOverride.proposal_id == decision.proposal_id,
            )
            .first()
        )
        if decision.decision == "rejected":
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Proposal {decision.proposal_id} was already applied; "
                        "override it with a corrected target instead of rejecting it"
                    ),
                )
            continue

        target = str(proposal.get("target_fqn") or subject)
        sources: list[str] = []
        note = decision.note
        if decision.decision == "overridden":
            override = decision.override or RemediationOverride()
            target = str(override.target_fqn or target)
            sources = [source.strip() for source in override.sources if source.strip()]
            note = override.note or note
        if action in ("map_alias", "correct_reference") and target not in known:
            raise HTTPException(
                status_code=400,
                detail=f"Override target '{target}' is not an existing object in this estate",
            )
        missing_sources = [source for source in sources if source not in known]
        if missing_sources:
            raise HTTPException(
                status_code=400,
                detail=f"Override sources not found in this estate: {', '.join(missing_sources)}",
            )

        if existing:
            db.delete(existing)
            db.flush()
        db.add(
            LineageOverride(
                estate_id=estate_id,
                proposal_id=decision.proposal_id,
                action=action,
                subject_fqn=subject,
                target_fqn=target if action not in ("capture_runtime_sql",) else None,
                sources=sources,
                note=note,
                status=decision.decision,
                approver=approver,
            )
        )
    db.flush()

    previous_version = estate.ir_version or "0.0.0"
    before = {
        "version": previous_version,
        "nodes": estate.node_count or 0,
        "edges": estate.edge_count or 0,
        "unresolved": estate.unresolved_count or 0,
    }
    effective_ir, effects = apply_overrides(base_ir, _override_dicts(db, estate_id))
    applied_effects = [effect for effect in effects if effect.get("applied")]

    if applied_effects:
        estate.ir_version = bump_version(previous_version, True)
        estate.node_count = effective_ir.total_nodes
        estate.edge_count = effective_ir.total_edges
        estate.unresolved_count = effective_ir.unresolved_count
        estate.survey_todo = _build_survey_todo(effective_ir)
        estate.ir_generated_at = datetime.now(timezone.utc)
    db.commit()

    append_ledger_event(
        db,
        estate_id,
        "remediation_decided",
        {
            "approver": approver,
            "decisions": [decision.model_dump(mode="json") for decision in payload.decisions],
            "proposal_count": len(known_ids),
        },
        actor=approver,
        actor_email=payload.actor_email,
    )

    if applied_effects:
        append_ledger_event(
            db,
            estate_id,
            "remediation_applied",
            {
                "version": estate.ir_version,
                "previous_version": previous_version,
                "approver": approver,
                "applied_count": len(applied_effects),
                "proposals": applied_effects,
                "before": before,
                "after": {
                    "version": estate.ir_version,
                    "nodes": effective_ir.total_nodes,
                    "edges": effective_ir.total_edges,
                    "unresolved": effective_ir.unresolved_count,
                },
                "fingerprint": fingerprint_ir(effective_ir),
            },
            actor=approver,
            actor_email=payload.actor_email,
        )
        db.commit()
        try:
            from dsxlineage.estate.graph import sync_estate_to_graph

            sync_estate_to_graph(estate.id, estate.name, effective_ir)
            append_ledger_event(db, estate_id, "graph_synced", {"estate_id": estate_id, "after_override": True})
            db.commit()
        except Exception as exc:
            print(f"Warning: graph sync failed after override for estate {estate.id}: {exc}")

    db.commit()
    return _unresolved_state(db, estate)


@router.get("/{estate_id}/ir")
def get_estate_ir(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    if not _synthetic_root_for_estate(estate).exists():
        raise HTTPException(status_code=404, detail="Synthetic estate not found")
    ir = _get_ir(estate, db)
    return ir.model_dump()


@router.get("/{estate_id}/graph")
def get_estate_graph(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = _get_ir(estate, db)
    # Build nodes for ReactFlow: each table/view/procedure/etc is a node
    nodes = []
    edges = []
    # Lay out nodes in a readable grid - 6 columns, 80px row height, grouped by layer then grid
    # Previously every node in a layer shared x=0, so 126 nodes collapsed to 5 visible stacks.
    layer_order = {"source": 0, "warehouse": 1, "etl": 2, "ml": 2.5, "orchestration": 3, "bi": 4}
    # Sort tables so warehouse tables are together, source tables together, etc., then grid
    _SOURCE_SYSTEMS = ("SAP_ECC", "SALESFORCE", "FLAT_FILES", "RETAIL_CUSTOMER", "BSCS", "CDR", "NMS", "FLAT_TELCO", "CRM_TELCO", "LOAN_SRC", "RETAIL_SRC")
    def _layer_of(node_type: str, system: str = "") -> str:
        if node_type == "table":
            return "source" if system in _SOURCE_SYSTEMS else "warehouse"
        if node_type == "view": return "warehouse"
        if node_type == "procedure": return "warehouse"
        if node_type == "etl": return "etl"
        if node_type == "schedule": return "orchestration"
        return "bi"

    # Build a flat list with (layer_order, fqn) for stable grid - keeps layers roughly grouped
    # but guarantees every node gets a unique (x,y)
    all_items = []
    for t in ir.tables:
        layer = "source" if t.system in _SOURCE_SYSTEMS else "warehouse"
        all_items.append((layer_order[layer], t.fqn, "table", t))
    for v in ir.views:
        all_items.append((layer_order["warehouse"], v.fqn, "view", v))
    for p in ir.procedures:
        all_items.append((layer_order["warehouse"], p.fqn, "procedure", p))
    for j in ir.etl_jobs:
        all_items.append((layer_order["etl"], j.fqn, "etl", j))
    for s in ir.schedules:
        all_items.append((layer_order["orchestration"], s.fqn, "schedule", s))
    for d in ir.dashboards:
        all_items.append((layer_order["bi"], d.fqn, "dashboard", d))
    for r in getattr(ir, "routines", []):
        all_items.append((layer_order["ml"], r.fqn, "routine", r))
    known_node_ids = {fqn for _, fqn, _, _ in all_items}
    for ref in ir.unresolved_refs:
        if ref.fqn not in known_node_ids:
            all_items.append((layer_order["warehouse"], ref.fqn, "missing", ref))
    all_items.sort(key=lambda x: (x[0], x[1]))

    nodes: list[dict] = []
    for idx, (_, fqn, kind, obj) in enumerate(all_items):
        x = (idx % 6) * 220
        y = (idx // 6) * 85
        if kind == "table":
            t = obj
            nodes.append({"id": t.fqn, "label": t.fqn, "type": "table", "system": t.system, "layer": "source" if t.system in _SOURCE_SYSTEMS else "warehouse", "data": {"label": t.fqn, "fqn": t.fqn, "kind": "table", "system": t.system}, "position": {"x": x, "y": y}})
        elif kind == "view":
            v = obj
            nodes.append({"id": v.fqn, "label": v.fqn, "type": "view", "complexity": v.complexity, "unresolved": v.unresolved, "data": {"label": v.fqn, "fqn": v.fqn, "kind": "view", "complexity": v.complexity, "unresolved": v.unresolved}, "position": {"x": x, "y": y}})
        elif kind == "procedure":
            p = obj
            nodes.append({"id": p.fqn, "label": p.fqn, "type": "procedure", "complexity": p.complexity, "has_dynamic": p.has_dynamic, "data": {"label": p.fqn, "fqn": p.fqn, "kind": "procedure", "complexity": p.complexity}, "position": {"x": x, "y": y}})
        elif kind == "etl":
            j = obj
            nodes.append({"id": j.fqn, "label": j.fqn, "type": "etl", "dialect": j.dialect, "data": {"label": j.fqn, "fqn": j.fqn, "kind": "etl"}, "position": {"x": x, "y": y}})
        elif kind == "schedule":
            s = obj
            nodes.append({"id": s.fqn, "label": s.fqn, "type": "schedule", "data": {"label": s.fqn, "fqn": s.fqn, "kind": "schedule"}, "position": {"x": x, "y": y}})
        elif kind == "missing":
            ref = obj
            nodes.append({"id": ref.fqn, "label": f"{ref.fqn} (missing)", "type": "table", "system": ref.fqn.split(".")[0] if "." in ref.fqn else "", "layer": "warehouse", "unresolved": True, "data": {"label": f"{ref.fqn} (missing)", "fqn": ref.fqn, "kind": "missing", "unresolved": True, "reason": ref.reason}, "position": {"x": x, "y": y}})
        elif kind == "routine":
            r = obj
            nodes.append({"id": r.fqn, "label": r.fqn, "type": "routine", "data": {"label": r.fqn, "fqn": r.fqn, "kind": "routine", "routine_kind": r.kind, "features": len(r.features), "model_files": r.model_files}, "position": {"x": x, "y": y}})
        else:
            d = obj
            nodes.append({"id": d.fqn, "label": d.fqn, "type": "dashboard", "tool": d.tool, "data": {"label": d.fqn, "fqn": d.fqn, "kind": "dashboard"}, "position": {"x": x, "y": y}})

    # Every drawn edge must be evidenced: Control-M references that never appear
    # in the job's own artifact are excluded from the graph (kept in the IR with
    # via=UNVERIFIED_DECLARATION_VIA, counted below).
    from dsxlineage.estate.ingest.build import UNVERIFIED_DECLARATION_VIA

    declared_unverified = 0
    for e in ir.edges:
        if e.edge_type.value == "UNRESOLVED":
            continue
        if e.via == UNVERIFIED_DECLARATION_VIA:
            declared_unverified += 1
            continue
        edges.append({"id": f"{e.source_fqn}->{e.target_fqn}:{e.edge_type.value}", "source": e.source_fqn, "target": e.target_fqn, "type": e.edge_type.value, "unresolved": e.unresolved, "label": e.edge_type.value})

    # Counts describe what the graph actually draws: object nodes + missing-ref
    # nodes, and edges after UNRESOLVED-type and unverified-declaration edges are
    # excluded. `unresolved` matches the amber "(missing)" nodes;
    # `unresolved_edges` keeps the IR-level flagged-edge count used by the
    # evidence gate banner; `declared_unverified` counts hidden declaration-only
    # references (scheduled job names a table its artifact never mentions).
    return {
        "nodes": nodes,
        "edges": edges,
        "estate": {
            "id": estate.id,
            "name": estate.name,
            "ir_version": ir.version,
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "unresolved": len(ir.unresolved_refs),
                "unresolved_edges": ir.unresolved_count,
                "column_rows": len(ir.column_lineage),
                "declared_unverified": declared_unverified,
            },
        },
    }


# ---------------------------------------------------------------------------
# Lineage & blast radius
# ---------------------------------------------------------------------------

@router.get("/{estate_id}/lineage")
def get_lineage(estate_id: int, fqn: str, direction: str = "both", hops: int = 6, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = _get_ir(estate, db)
    # BFS in IR (Postgres-native, no Neo4j needed)
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    for e in ir.edges:
        if e.edge_type.value == "UNRESOLVED":
            continue
        outgoing.setdefault(e.source_fqn, []).append(e.target_fqn)
        incoming.setdefault(e.target_fqn, []).append(e.source_fqn)

    def bfs(start: str, adj: dict[str, list[str]], max_hops: int):
        visited: set[str] = set([start])
        queue: list[tuple[str, int]] = [(start, 0)]
        result: list[dict] = []
        while queue:
            cur, depth = queue.pop(0)
            if depth > 0:
                result.append({"fqn": cur, "hops": depth})
            if depth >= max_hops:
                continue
            for nb in adj.get(cur, []):
                if nb not in visited:
                    visited.add(nb)
                    queue.append((nb, depth + 1))
        return result

    upstream = bfs(fqn, incoming, hops) if direction in ("both", "upstream") else []
    downstream = bfs(fqn, outgoing, hops) if direction in ("both", "downstream") else []
    return {"fqn": fqn, "direction": direction, "upstream": upstream, "downstream": downstream}


@router.get("/{estate_id}/blast-radius")
def get_blast_radius(estate_id: int, fqn: str, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = _get_ir(estate, db)
    outgoing: dict[str, list[str]] = {}
    for e in ir.edges:
        if e.edge_type.value == "UNRESOLVED":
            continue
        outgoing.setdefault(e.source_fqn, []).append(e.target_fqn)
    visited: set[str] = set([fqn])
    queue = [fqn]
    downstream: list[dict] = []
    while queue:
        cur = queue.pop(0)
        for nb in outgoing.get(cur, []):
            if nb not in visited:
                visited.add(nb)
                downstream.append({"fqn": nb, "via": cur})
                queue.append(nb)
    return {"fqn": fqn, "blast_radius": downstream, "count": len(downstream)}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.get("/{estate_id}/analytics")
def get_analytics(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = _get_ir(estate, db)
    ana = compute_analytics(ir)
    return ana


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@router.post("/{estate_id}/chat")
def post_chat(estate_id: int, payload: ChatRequest, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question is required")
    ir = _get_ir(estate, db)

    # Session handling
    session_id = payload.session_id
    if session_id:
        sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.estate_id == estate_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Chat session not found")
    else:
        sess = ChatSession(estate_id=estate_id, title=payload.question[:60])
        db.add(sess)
        db.flush()

    # Persist user message
    user_msg = ChatMessage(session_id=sess.id, role="user", content=payload.question.strip())
    db.add(user_msg)
    db.flush()

    # Handle generate_sql / explain tools
    import time as _time
    t0 = _time.time()
    if payload.generate_sql:
        # Use reasoning endpoint logic inline
        from dsxlineage.estate.semantic import analyze_semantic
        # Simple: generate SQL for the FQN mentioned in question
        fqn = payload.question.strip().split()[-1] if payload.question.strip() else ""
        # Try to find a view/table in question
        import re
        m = re.search(r"(VW_\w+|DW[_\w]*\.\w+)", payload.question)
        if m:
            fqn = m.group(1)
        ans = answer_question(f"Generate SQL for {fqn}", ir, use_llm=payload.use_llm)
        # Append SQL generation
        try:
            from dsxlineage.estate.api import _synthetic_root_for_estate  # noqa
            # Call generate_sql logic
            cols = [c for c in ir.column_lineage if c.target_table == fqn or c.source_table == fqn]
            if cols:
                ans.answer += f"\n\n**Generated SQL for {fqn}:**\n```sql\nSELECT {', '.join(c.target_column for c in cols[:5])} FROM {fqn} -- {len(cols)} columns, hops {max(c.hops for c in cols)}\n```"
        except Exception:
            pass
    elif payload.explain:
        ans = answer_question(f"Explain {payload.question}", ir, use_llm=payload.use_llm)
        ans.answer = f"**Explanation for {payload.question}:**\n" + ans.answer
    else:
        ans = answer_question(payload.question.strip(), ir, use_llm=payload.use_llm)
    # Human-in-the-loop: low confidence -> mark for review + persist review queue row
    needs_review = bool(payload.human_loop and ans.confidence is not None and ans.confidence < 0.6 and not ans.was_refused)
    if needs_review:
        ans.answer += "\n\n*Low confidence — routed to human review (Pending Review).*"
        ans.tool_calls = (ans.tool_calls or []) + ["human_review"]
    latency = int((_time.time() - t0) * 1000)

    assistant_msg = ChatMessage(
        session_id=sess.id,
        role="assistant",
        content=ans.answer,
        citations=ans.citations,
        tool_calls=ans.tool_calls,
        was_refused=ans.was_refused,
        latency_ms=latency,
    )
    db.add(assistant_msg)
    db.flush()

    # Ledger: chat_query
    append_ledger_event(db, estate_id, "chat_query", {
        "question": payload.question.strip()[:500],
        "citations": ans.citations,
        "was_refused": ans.was_refused,
        "latency_ms": latency,
        "confidence": ans.confidence,
        "message_id": assistant_msg.id,
    }, actor="user")
    if needs_review:
        append_ledger_event(db, estate_id, "human_review_pending", {
            "message_id": assistant_msg.id,
            "session_id": sess.id,
            "question": payload.question.strip()[:500],
            "answer": ans.answer[:1000],
            "confidence": ans.confidence,
            "citations": ans.citations,
            "status": "pending",
        }, actor="user")
    db.commit()
    db.refresh(assistant_msg)

    return {
        "session_id": sess.id,
        "answer": ans.answer,
        "citations": ans.citations,
        "confidence": ans.confidence,
        "was_refused": ans.was_refused,
        "refusal_reason": ans.refusal_reason,
        "tool_calls": ans.tool_calls,
        "latency_ms": latency,
        "message_id": assistant_msg.id,
        "needs_review": needs_review,
    }


@router.get("/{estate_id}/chat/sessions")
def list_chat_sessions(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    sessions = db.query(ChatSession).filter(ChatSession.estate_id == estate_id).order_by(ChatSession.created_at.desc()).all()
    return sessions


@router.get("/{estate_id}/chat/sessions/{session_id}")
def get_chat_session(estate_id: int, session_id: int, db: Session = Depends(get_db)):
    sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.estate_id == estate_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Chat session not found")
    msgs = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc()).all()
    return {"session": sess, "messages": msgs}


@router.post("/{estate_id}/chat/feedback")
def post_chat_feedback(estate_id: int, payload: ChatFeedbackRequest, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    msg = db.query(ChatMessage).filter(ChatMessage.id == payload.message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Chat message not found")
    sess = db.query(ChatSession).filter(ChatSession.id == msg.session_id, ChatSession.estate_id == estate_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Chat session not found for estate")
    ev = append_ledger_event(db, estate_id, "human_review_decision", {
        "message_id": payload.message_id,
        "session_id": msg.session_id,
        "decision": payload.decision,
        "comment": payload.comment,
        "reviewer": payload.reviewer or "reviewer",
        "answer": (msg.content or "")[:1000],
    }, actor=payload.reviewer or "reviewer")
    msg.tool_calls = (msg.tool_calls or []) + [f"human_{payload.decision}"]
    db.commit()
    return {"message_id": payload.message_id, "decision": payload.decision, "ledger_event_id": ev.id}


@router.get("/{estate_id}/chat/reviews")
def list_chat_reviews(estate_id: int, status: str | None = None, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    from dsxlineage.estate.models import LedgerEvent
    events = db.query(LedgerEvent).filter(
        LedgerEvent.estate_id == estate_id,
        LedgerEvent.event_type.in_(["human_review_pending", "human_review_decision"]),
    ).order_by(LedgerEvent.id.desc()).limit(100).all()
    out = []
    for e in events:
        p = e.payload or {}
        out.append({
            "id": e.id,
            "event_type": e.event_type,
            "message_id": p.get("message_id"),
            "session_id": p.get("session_id"),
            "question": p.get("question"),
            "decision": p.get("decision"),
            "status": p.get("status", "decided" if e.event_type == "human_review_decision" else "pending"),
            "confidence": p.get("confidence"),
            "reviewer": p.get("reviewer"),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    if status:
        out = [o for o in out if o["status"] == status]
    return out


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

@router.get("/{estate_id}/ledger")
def get_ledger(estate_id: int, limit: int = 100, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    from dsxlineage.estate.models import LedgerEvent
    events = db.query(LedgerEvent).filter(LedgerEvent.estate_id == estate_id).order_by(LedgerEvent.id.asc()).limit(limit).all()
    return events


@router.get("/{estate_id}/ledger/verify")
def verify_ledger(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ok, reason = verify_ledger_chain(db, estate_id)
    return {"estate_id": estate_id, "verified": ok, "reason": reason}


@router.get("/{estate_id}/audit")
def get_audit(estate_id: int, raw: bool = False, limit: int = 100, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    from dsxlineage.estate.models import LedgerEvent
    from dsxlineage.estate.audit import humanize_ledger
    events = db.query(LedgerEvent).filter(LedgerEvent.estate_id == estate_id).order_by(LedgerEvent.id.asc()).limit(limit).all()
    if raw:
        return events
    # Try to load IR for relationship enrichment
    try:
        ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    except Exception:
        ir = None
    human = humanize_ledger(events, ir)
    return human


def _fqn_matches(query: str | None, evidence_fqn: str | None) -> bool:
    """Case-insensitive per-node match for drawer tabs (?fqn=).

    Matches when either side is a substring of the other (handles
    TABLE vs SCHEMA.TABLE vs SCHEMA.TABLE.COLUMN), plus edge strings
    like 'SRC -> TGT' and comma-separated FQN lists.
    """
    if not query:
        return True
    q = query.strip().upper()
    e = (evidence_fqn or "").upper()
    if not e:
        return False
    if q in e or e in q:
        return True
    norm = e.replace("→", "->")
    if "->" in norm:
        for part in norm.split("->"):
            part = part.strip()
            if q in part or part in q:
                return True
    if "," in e:
        for part in e.split(","):
            part = part.strip()
            if q in part or part in q:
                return True
    return False


def _filter_items(items: list[dict], query: str | None) -> list[dict]:
    if not query:
        return items
    return [it for it in items if _fqn_matches(query, (it.get("evidence") or {}).get("fqn"))]


@router.get("/{estate_id}/structure/facts")
def get_structure_facts(estate_id: int, fqn: str | None = None, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    from dsxlineage.estate.structure import analyze_structure
    res = analyze_structure(ir)
    if fqn:
        res = {
            **res,
            "facts": _filter_items(res.get("facts", []), fqn),
            "unknowns": _filter_items(res.get("unknowns", []), fqn),
            "fqn": fqn,
        }
    return res


@router.get("/{estate_id}/semantic/rules")
def get_semantic_rules(estate_id: int, fqn: str | None = None, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    from dsxlineage.estate.semantic import analyze_semantic
    res = analyze_semantic(ir)
    if fqn:
        res = {
            **res,
            "rules": _filter_items(res.get("rules", []), fqn),
            "implicit_contracts": _filter_items(res.get("implicit_contracts", []), fqn),
            "similar": _filter_items(res.get("similar", []), fqn),
            "conflicting": _filter_items(res.get("conflicting", []), fqn),
            "fqn": fqn,
        }
    return res


@router.get("/{estate_id}/behavior/flows")
def get_behavior_flows(estate_id: int, fqn: str | None = None, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    from dsxlineage.estate.behavior import analyze_behavior
    res = analyze_behavior(ir, estate_root=_synthetic_root_for_estate(estate))
    if fqn:
        res = {
            **res,
            "failures": _filter_items(res.get("failures", []), fqn),
            "dependencies": _filter_items(res.get("dependencies", []), fqn),
            "rejects": _filter_items(res.get("rejects", []), fqn),
            "data_quality": _filter_items(res.get("data_quality", []), fqn),
            "operational": _filter_items(res.get("operational", []), fqn),
            "fqn": fqn,
        }
    return res


@router.get("/{estate_id}/columns/{fqn:path}/lineage")
def get_column_lineage(estate_id: int, fqn: str, db: Session = Depends(get_db)):
    """Estate-wide column lineage for a single column FQN (e.g., VW_RISK_EXPOSURE.CUSTOMER_ID)."""
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    from dsxlineage.estate.column_graph import get_column_journey
    # fqn is like "VW_RISK_EXPOSURE.CUSTOMER_ID" or "DW.FACT_ORDERS.ORDER_ID"
    # Normalize: if it doesn't contain '.', try to find it
    if "." not in fqn:
        raise HTTPException(status_code=400, detail="fqn must be table.column")
    # Ensure it exists in column lineage
    journey = get_column_journey(ir, fqn)
    # Enrich with hops already in ir
    # Find the specific column lineage entry for this fqn
    target_table, target_col = fqn.rsplit(".", 1)
    cl = next((c for c in ir.column_lineage if c.target_table == target_table and c.target_column == target_col.upper()), None)
    if not cl:
        cl = next((c for c in ir.column_lineage if f"{c.target_table}.{c.target_column}" == fqn.upper()), None)
    return {
        "fqn": fqn,
        "column": cl.model_dump() if cl else None,
        "journey": journey,
    }


MIGRATION_TARGETS = ["snowflake", "databricks", "aws", "microsoft", "other"]
MIGRATION_PATTERNS = {
    "snowflake": "SnowConvert / Snowflake",
    "databricks": "Lakebridge",
    "aws": "AWS Transform / Glue",
    "microsoft": "Microsoft Fabric / Azure",
    "other": "Custom",
}


@router.get("/{estate_id}/knowledge-graph")
def get_knowledge_graph(estate_id: int, fqn: str | None = None, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    from dsxlineage.estate.structure import analyze_structure
    from dsxlineage.estate.semantic import analyze_semantic
    from dsxlineage.estate.behavior import analyze_behavior
    struct = analyze_structure(ir)
    sem = analyze_semantic(ir)
    beh = analyze_behavior(ir, estate_root=_synthetic_root_for_estate(estate))
    if fqn:
        struct_facts = _filter_items(struct.get("facts", []), fqn)
        sem_rules = _filter_items(sem.get("rules", []), fqn) + _filter_items(sem.get("implicit_contracts", []), fqn)
        beh_deps = _filter_items(beh.get("dependencies", []), fqn)
        beh_failures = _filter_items(beh.get("failures", []), fqn)
        beh_oper = _filter_items(beh.get("operational", []), fqn)
        cols = [c for c in ir.column_lineage if _fqn_matches(fqn, f"{c.target_table}.{c.target_column}") or _fqn_matches(fqn, f"{c.source_table}.{c.source_column}")]
        tables = [t for t in ir.tables if _fqn_matches(fqn, t.fqn)]
        views = [v for v in ir.views if _fqn_matches(fqn, v.fqn)]
    else:
        struct_facts = struct.get("facts", [])
        sem_rules = sem.get("rules", []) + sem.get("implicit_contracts", [])
        beh_deps = beh.get("dependencies", [])
        beh_failures = beh.get("failures", [])
        beh_oper = beh.get("operational", [])
        cols = ir.column_lineage
        tables = ir.tables
        views = ir.views
    # Knowledge Graph overview: exactly 6 subgraphs as in PNG.
    # Jobs/orchestration counts fold into Dependencies (ETL + schedules are edges).
    jobs_count = len(ir.etl_jobs) + len(ir.schedules)
    result = {
        "central": "Knowledge Graph",
        "subgraphs": [
            {"name": "Rules", "count": len(sem_rules), "color": "purple"},
            {"name": "Dependencies", "count": len(beh_deps) + (0 if fqn else jobs_count), "color": "orange"},
            {"name": "Metadata", "count": len(tables) + len(views), "color": "teal"},
            {"name": "Datasets", "count": len(tables), "color": "green"},
            {"name": "Behavior", "count": len(beh_failures) + len(beh_oper), "color": "red"},
            {"name": "Lineage", "count": len(cols), "color": "cyan"},
        ],
        "facts": len(struct_facts) + len(sem_rules) + len(beh_deps) + len(beh_failures) + len(beh_oper),
        "unknowns": len(_filter_items(struct.get("unknowns", []), fqn)) if fqn else struct["unknown_count"],
    }
    if fqn:
        result["fqn"] = fqn
    return result


@router.post("/{estate_id}/reasoning/explain")
def post_reasoning_explain(estate_id: int, payload: dict, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    fqn = payload.get("fqn", "")
    # Use semantic engine to explain
    from dsxlineage.estate.semantic import analyze_semantic
    sem = analyze_semantic(ir)
    # Find rule for fqn
    rule = next((r for r in sem.get("rules", []) if fqn in r["evidence"]["fqn"]), None)
    if rule:
        return {"fqn": fqn, "explanation": rule["meaning"], "confidence": rule["confidence"], "source_ref": rule["source_ref"]}
    return {"fqn": fqn, "explanation": f"No specific business rule found for {fqn}, but it is part of {ir.total_nodes} nodes with {ir.unresolved_count} unresolved.", "confidence": 0.5, "source_ref": "ir"}


@router.post("/{estate_id}/reasoning/generate-sql")
def post_generate_sql(estate_id: int, payload: dict, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    fqn = payload.get("fqn", "")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    cols = [c for c in ir.column_lineage if c.target_table == fqn or c.source_table == fqn]
    if not cols:
        # Try to find view
        view = next((v for v in ir.views if v.fqn == fqn), None)
        if view:
            return {"fqn": fqn, "sql": view.sql, "hops": 1}
        return {"fqn": fqn, "sql": f"SELECT * FROM {fqn} -- no lineage found", "hops": 0}
    # Build SQL from column lineage
    target_cols = {}
    for c in cols:
        if c.target_table == fqn:
            target_cols[c.target_column] = c
    if target_cols:
        # Generate SELECT for target
        cols_sql = ", ".join([f"{c.source_table}.{c.source_column}" + (f" AS {c.target_column}" if c.transform_expr else "") for c in target_cols.values()])
        # Find source tables
        sources = ", ".join(set(c.source_table for c in target_cols.values()))
        sql = f"SELECT {cols_sql} FROM {sources} -- {len(target_cols)} columns, hops {max(c.hops for c in target_cols.values())}"
        return {"fqn": fqn, "sql": sql, "hops": max(c.hops for c in target_cols.values()), "columns": list(target_cols.keys())}
    return {"fqn": fqn, "sql": f"SELECT * FROM {fqn}", "hops": 1}


@router.post("/{estate_id}/migration/recommend")
def post_migration_recommend(estate_id: int, payload: dict = {}, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    target = payload.get("target", "databricks") if isinstance(payload, dict) else "databricks"
    if target not in MIGRATION_TARGETS:
        target = "snowflake"
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    from dsxlineage.estate.bridge import recommend_wedge
    rec = recommend_wedge(ir, target=target)
    # Map to new migration recommendation format (recommend-only, not execute)
    return {
        "target": target,
        "pattern": MIGRATION_PATTERNS.get(target, "Custom"),
        "evidence": {"nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count, "hops": max((c.hops for c in ir.column_lineage), default=1)},
        "recommendation": rec.model_dump(),
        "alternatives": [t for t in MIGRATION_TARGETS if t != target],
        "note": "Recommendation only — not execution. Export pack for evidence.",
    }


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

@router.post("/{estate_id}/bridge/recommend")
def bridge_recommend(estate_id: int, payload: BridgeRecommendRequest | None = None, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    target = payload.target if payload else "snowflake"
    ir = _get_ir(estate, db)
    rec = recommend_wedge(ir, target=target)
    append_ledger_event(db, estate_id, "wedge_recommended", rec.model_dump())
    db.commit()
    return rec.model_dump()


@router.post("/{estate_id}/bridge/plan")
def bridge_create_plan(estate_id: int, payload: PlanCreate, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = _get_ir(estate, db)
    if payload.scope_fqns is None:
        rec = recommend_wedge(ir, target=payload.target_platform)
        scope = rec.scope_fqns
        est_days = rec.estimated_days
        risk = rec.risk_level
    else:
        scope = payload.scope_fqns
        est_days = round(len(scope) * 1.2, 1)
        risk = "medium"
    plan = MigrationPlan(
        estate_id=estate_id,
        name=payload.name,
        target_platform=payload.target_platform,
        scope_fqns=scope,
        scope_description=f"Wedge of {len(scope)} objects -> {payload.target_platform}",
        status="pending_approval",
        estimated_days=est_days,
        risk_level=risk,
    )
    db.add(plan)
    db.flush()
    append_ledger_event(db, estate_id, "plan_created", {"plan_id": plan.id, "name": plan.name, "target": plan.target_platform, "scope_count": len(scope)})
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/{estate_id}/bridge/plans")
def bridge_list_plans(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    plans = db.query(MigrationPlan).filter(MigrationPlan.estate_id == estate_id).order_by(MigrationPlan.created_at.desc()).all()
    return plans


@router.post("/{estate_id}/bridge/plan/{plan_id}/approve")
def bridge_approve_plan(estate_id: int, plan_id: int, payload: PlanApprove, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    plan = db.query(MigrationPlan).filter(MigrationPlan.id == plan_id, MigrationPlan.estate_id == estate_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status not in ("pending_approval", "draft"):
        raise HTTPException(status_code=409, detail=f"Plan is already {plan.status}")
    if not payload.approver or not payload.approver.strip():
        raise HTTPException(status_code=400, detail="Approver name is required (not free-text reviewer, named approver)")
    # Named approver required - no empty, no single char
    if len(payload.approver.strip()) < 2:
        raise HTTPException(status_code=400, detail="Approver name must be at least 2 characters")
    plan.status = "approved"
    plan.approved_by = payload.approver.strip()
    plan.approved_at = datetime.now(timezone.utc)

    # Generate artifacts upon approval
    ir = _get_ir(estate, db)
    ddl = generate_snowflake_ddl(ir, plan.scope_fqns or [])
    tf = generate_terraform(ir, plan.scope_fqns or [], target=plan.target_platform)
    # Store as ledger artifacts (for POC, store inline in payload; prod would write to Files)
    plan.generated_ddl_path = f"generated/{estate.name}/plan_{plan.id}_ddl.sql"
    plan.generated_terraform_path = f"generated/{estate.name}/plan_{plan.id}_terraform.tf"

    db.commit()
    append_ledger_event(db, estate_id, "plan_approved", {
        "plan_id": plan.id, "approver": plan.approved_by, "target": plan.target_platform,
        "ddl_preview": ddl[:2000], "terraform_preview": tf[:1500]
    }, actor=payload.approver.strip(), actor_email=payload.actor_email)
    db.commit()
    db.refresh(plan)
    return {"plan": plan, "ddl_preview": ddl[:4000], "terraform_preview": tf[:3000]}


@router.get("/{estate_id}/bridge/plan/{plan_id}/ddl", response_class=PlainTextResponse)
def bridge_get_ddl(estate_id: int, plan_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    plan = db.query(MigrationPlan).filter(MigrationPlan.id == plan_id, MigrationPlan.estate_id == estate_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status != "approved":
        raise HTTPException(status_code=409, detail="Plan must be approved before generating DDL")
    ir = _get_ir(estate, db)
    ddl = generate_snowflake_ddl(ir, plan.scope_fqns or [])
    return ddl


@router.get("/{estate_id}/bridge/plan/{plan_id}/terraform", response_class=PlainTextResponse)
def bridge_get_terraform(estate_id: int, plan_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    plan = db.query(MigrationPlan).filter(MigrationPlan.id == plan_id, MigrationPlan.estate_id == estate_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status != "approved":
        raise HTTPException(status_code=409, detail="Plan must be approved before generating Terraform")
    ir = _get_ir(estate, db)
    tf = generate_terraform(ir, plan.scope_fqns or [], target=plan.target_platform)
    return tf


@router.get("/{estate_id}/bridge/plan/{plan_id}/scaffold", response_class=PlainTextResponse)
def bridge_get_scaffold(estate_id: int, plan_id: int, job_fqn: str | None = None, target: str = "pyspark", db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    plan = db.query(MigrationPlan).filter(MigrationPlan.id == plan_id, MigrationPlan.estate_id == estate_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status != "approved":
        raise HTTPException(status_code=409, detail="Plan must be approved before generating migration code")
    ir = _get_ir(estate, db)
    scope = set(plan.scope_fqns or [])
    eligible = [
        j for j in ir.etl_jobs
        if j.fqn in scope and j.dialect == "datastage" and j.raw_path and Path(j.raw_path).exists()
    ]
    if job_fqn:
        job = next((j for j in eligible if j.fqn == job_fqn), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"ETL job '{job_fqn}' not found in this plan's scope, or not eligible (DataStage only, raw file required)")
    else:
        if not eligible:
            raise HTTPException(status_code=409, detail="No DataStage ETL job with a retained raw file in this plan's scope - scaffold generation is only supported for DataStage jobs today")
        job = eligible[0]
    from dsxlineage.estate.scaffold import generate_pyspark_scaffold
    try:
        return generate_pyspark_scaffold(job.raw_path, job.fqn, target=target)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Scaffold generation failed: {exc}")


@router.post("/{estate_id}/bridge/diff")
def bridge_run_diff(estate_id: int, payload: DiffRequest, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    # Check plan approved gate - must have at least one approved plan
    approved = db.query(MigrationPlan).filter(MigrationPlan.estate_id == estate_id, MigrationPlan.status == "approved").first()
    if not approved:
        raise HTTPException(status_code=409, detail="At least one plan must be approved before running diff (ledger gate)")
    ir = _get_ir(estate, db)
    scope = payload.scope_fqns or approved.scope_fqns or [t.fqn for t in ir.tables[:8]]
    result = run_diff_harness(ir, scope_fqns=scope, synthetic_data_dir=_synthetic_root_for_estate(estate) / "data", sampling=payload.sampling, n=payload.n)
    # Persist DiffRun
    run = DiffRun(
        estate_id=estate_id,
        plan_id=approved.id,
        status="passed" if result.passed else "failed",
        tolerances=result.tolerances.model_dump(),
        total_rows_compared=result.total_rows_compared,
        mismatched_rows=result.mismatched_rows,
        masked_columns=result.masked_columns,
        sampling_method=result.sampling_method,
        sampling_n=result.sampling_n,
        bound_95=result.bound_95,
        per_table_results=[p.model_dump() for p in result.per_table],
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    append_ledger_event(db, estate_id, "diff_run", {
        "diff_run_id": run.id, "status": run.status, "total_rows": run.total_rows_compared,
        "mismatched": run.mismatched_rows, "bound_95": run.bound_95, "sampling": run.sampling_method
    })
    db.commit()
    db.refresh(run)
    return {"diff_run": run, "result": result.model_dump()}


@router.post("/{estate_id}/bridge/continuity")
def bridge_check_continuity(estate_id: int, payload: ContinuityRequest, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    # Must have a passed diff run (gate)
    last_diff = db.query(DiffRun).filter(DiffRun.estate_id == estate_id, DiffRun.status == "passed").order_by(DiffRun.id.desc()).first()
    if not last_diff:
        raise HTTPException(status_code=409, detail="A passed diff run is required before continuity check (ledger gate)")
    ir = _get_ir(estate, db)
    result = check_continuity(ir, target_ir=None, scope_fqns=payload.scope_fqns)
    # Update diff run with continuity
    last_diff.continuity_passed = result.passed
    last_diff.continuity_flags = [f.model_dump() for f in result.flags if f.requires_approval]
    db.commit()
    append_ledger_event(db, estate_id, "continuity_checked", {
        "diff_run_id": last_diff.id, "passed": result.passed, "total_columns": result.total_columns,
        "matched": result.matched, "mismatched": result.mismatched, "requires_approval": result.requires_approval_count
    })
    db.commit()
    return result.model_dump()


@router.post("/{estate_id}/bridge/promote")
def bridge_promote(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ok, missing = can_promote(db, estate_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"Promote blocked - missing ledger evidence: {', '.join(missing)}")
    # Verify chain integrity
    verified, reason = verify_ledger_chain(db, estate_id)
    if not verified:
        raise HTTPException(status_code=409, detail=f"Ledger chain verification failed: {reason}")
    ev = append_ledger_event(db, estate_id, "promoted", {"estate_id": estate_id, "promoted_at": datetime.now(timezone.utc).isoformat()})
    db.commit()
    return {"estate_id": estate_id, "promoted": True, "ledger_event_id": ev.id, "verified": verified}


@router.get("/{estate_id}/bridge/diffs")
def bridge_list_diffs(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    runs = db.query(DiffRun).filter(DiffRun.estate_id == estate_id).order_by(DiffRun.created_at.desc()).all()
    return runs
