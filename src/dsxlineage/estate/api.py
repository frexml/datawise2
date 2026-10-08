"""
Estate REST API - /api/estates/*

All new estate endpoints. Mounted at /api/estates by main.py.

Design:
  POST   /api/estates                  - create estate from synthetic (or catalog dump)
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
from pydantic import BaseModel
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
from dsxlineage.estate.extractor import extract_estate_ir
from dsxlineage.estate.graph import blast_radius as graph_blast_radius
from dsxlineage.estate.ir import EstateIR
from dsxlineage.estate.ledger import append_ledger_event, can_promote, verify_ledger_chain
from dsxlineage.estate.models import ChatMessage, ChatSession, DiffRun, Estate, MigrationPlan

router = APIRouter()

SYNTHETIC_ROOT = Path("data/synthetic_estate")
TELCO_ROOT = Path("data/synthetic_estate_telco")

def _synthetic_root_for_estate(estate: Estate) -> Path:
    """Pick synthetic dir per estate_type - banking vs telecom."""
    if getattr(estate, "estate_type", "banking") == "telecom":
        return TELCO_ROOT if TELCO_ROOT.exists() else SYNTHETIC_ROOT
    return SYNTHETIC_ROOT


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EstateCreate(BaseModel):
    name: str = "NORTHSTAR"
    display_name: str = "NorthStar Synthetic Estate"
    source_type: str = "synthetic"  # synthetic | catalog_dump
    estate_type: str = "banking"  # banking | telecom
    synthetic_path: str | None = None  # override path if needed
    connection: dict | None = None  # {host, port, user, db_type} - mock for demo
    defer_discovery: bool = False  # True: skip auto-extract, leave PENDING for a real survey/approve flow


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


class PlanCreate(BaseModel):
    name: str = "Snowflake Wedge - Finance Mart"
    target_platform: Literal["snowflake", "bigquery", "synapse"] = "snowflake"
    scope_fqns: list[str] | None = None


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


@router.post("")
def create_estate(payload: EstateCreate, db: Session = Depends(get_db)):
    existing = db.query(Estate).filter(Estate.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Estate {payload.name} already exists (id={existing.id})")
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
        synth_path = Path(payload.synthetic_path) if payload.synthetic_path else (Path("data/synthetic_estate_telco") if payload.estate_type == "telecom" else SYNTHETIC_ROOT)
        # Try to pick correct synthetic dir per estate_type
        if payload.estate_type == "telecom" and not synth_path.exists():
            synth_path = Path("data/synthetic_estate_telco")
        if payload.source_type == "synthetic" and synth_path.exists():
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
                append_ledger_event(db, estate.id, "ir_generated", {"version": ir.version, "nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count, "estate_type": payload.estate_type})
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
        if estate.source_type != "synthetic":
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
    db.commit()
    # No counts yet — step labels only. Counts/details are revealed per-stage as each
    # one completes during approve_survey's staged run, not spoiled upfront here.
    append_ledger_event(db, estate_id, "survey_todo_created", {"todo_count": len(todo_pending), "estate_type": estate.estate_type})
    db.commit()
    return {"estate_id": estate_id, "status": "pending_approval", "todo": todo_pending}


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
    synth = str(Path("data/synthetic_estate_telco") if estate_type == "telecom" else SYNTHETIC_ROOT)

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
                    db2, eid, "survey_completed", {"nodes": ir.total_nodes, "edges": ir.total_edges, "estate_type": etype, "approver": actor},
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
    return {"estate_id": estate_id, "current_stage": estate.current_stage, "status": estate.status, "todo": estate.survey_todo or [], "counts": {"nodes": estate.node_count, "edges": estate.edge_count, "unresolved": estate.unresolved_count}}


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

    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    estate.ir_version = ir.version
    estate.ir_generated_at = datetime.now(timezone.utc)
    estate.node_count = ir.total_nodes
    estate.edge_count = ir.total_edges
    estate.unresolved_count = ir.unresolved_count
    db.commit()
    ev = append_ledger_event(db, estate_id, "ir_generated", {"version": ir.version, "nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count})
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


@router.get("/{estate_id}/ir")
def get_estate_ir(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    if not _synthetic_root_for_estate(estate).exists():
        raise HTTPException(status_code=404, detail="Synthetic estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    return ir.model_dump()


@router.get("/{estate_id}/graph")
def get_estate_graph(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    # Build nodes for ReactFlow: each table/view/procedure/etc is a node
    nodes = []
    edges = []
    # Lay out nodes in a readable grid - 6 columns, 80px row height, grouped by layer then grid
    # Previously every node in a layer shared x=0, so 126 nodes collapsed to 5 visible stacks.
    layer_order = {"source": 0, "warehouse": 1, "etl": 2, "orchestration": 3, "bi": 4}
    # Sort tables so warehouse tables are together, source tables together, etc., then grid
    def _layer_of(node_type: str, system: str = "") -> str:
        if node_type == "table":
            return "source" if system in ("SAP_ECC", "SALESFORCE", "FLAT_FILES") else "warehouse"
        if node_type == "view": return "warehouse"
        if node_type == "procedure": return "warehouse"
        if node_type == "etl": return "etl"
        if node_type == "schedule": return "orchestration"
        return "bi"

    # Build a flat list with (layer_order, fqn) for stable grid - keeps layers roughly grouped
    # but guarantees every node gets a unique (x,y)
    all_items = []
    for t in ir.tables:
        layer = "source" if t.system in ("SAP_ECC", "SALESFORCE", "FLAT_FILES") else "warehouse"
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
    all_items.sort(key=lambda x: (x[0], x[1]))

    nodes: list[dict] = []
    for idx, (_, fqn, kind, obj) in enumerate(all_items):
        x = (idx % 6) * 220
        y = (idx // 6) * 85
        if kind == "table":
            t = obj
            nodes.append({"id": t.fqn, "label": t.fqn, "type": "table", "system": t.system, "layer": "source" if t.system in ("SAP_ECC", "SALESFORCE", "FLAT_FILES") else "warehouse", "data": {"label": t.fqn, "fqn": t.fqn, "kind": "table", "system": t.system}, "position": {"x": x, "y": y}})
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
        else:
            d = obj
            nodes.append({"id": d.fqn, "label": d.fqn, "type": "dashboard", "tool": d.tool, "data": {"label": d.fqn, "fqn": d.fqn, "kind": "dashboard"}, "position": {"x": x, "y": y}})

    for e in ir.edges:
        if e.edge_type.value == "UNRESOLVED":
            continue
        edges.append({"id": f"{e.source_fqn}->{e.target_fqn}:{e.edge_type.value}", "source": e.source_fqn, "target": e.target_fqn, "type": e.edge_type.value, "unresolved": e.unresolved, "label": e.edge_type.value})

    return {"nodes": nodes, "edges": edges, "estate": {"id": estate.id, "name": estate.name, "ir_version": ir.version, "counts": {"nodes": ir.total_nodes, "edges": ir.total_edges, "unresolved": ir.unresolved_count}}}


# ---------------------------------------------------------------------------
# Lineage & blast radius
# ---------------------------------------------------------------------------

@router.get("/{estate_id}/lineage")
def get_lineage(estate_id: int, fqn: str, direction: str = "both", hops: int = 6, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)

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

    # Answer
    import time as _time
    t0 = _time.time()
    ans = answer_question(payload.question.strip(), ir, use_llm=payload.use_llm)
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

    # Ledger: chat_query
    append_ledger_event(db, estate_id, "chat_query", {
        "question": payload.question.strip()[:500],
        "citations": ans.citations,
        "was_refused": ans.was_refused,
        "latency_ms": latency,
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


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

@router.post("/{estate_id}/bridge/recommend")
def bridge_recommend(estate_id: int, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    rec = recommend_wedge(ir)
    append_ledger_event(db, estate_id, "wedge_recommended", rec.model_dump())
    db.commit()
    return rec.model_dump()


@router.post("/{estate_id}/bridge/plan")
def bridge_create_plan(estate_id: int, payload: PlanCreate, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
    tf = generate_terraform(ir, plan.scope_fqns or [], target=plan.target_platform)
    return tf


@router.post("/{estate_id}/bridge/diff")
def bridge_run_diff(estate_id: int, payload: DiffRequest, db: Session = Depends(get_db)):
    estate = db.query(Estate).filter(Estate.id == estate_id).first()
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    # Check plan approved gate - must have at least one approved plan
    approved = db.query(MigrationPlan).filter(MigrationPlan.estate_id == estate_id, MigrationPlan.status == "approved").first()
    if not approved:
        raise HTTPException(status_code=409, detail="At least one plan must be approved before running diff (ledger gate)")
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
    ir = extract_estate_ir(_synthetic_root_for_estate(estate), estate_name=estate.name)
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
