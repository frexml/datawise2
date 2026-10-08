"""
Estate Knowledge Graph + Evidence Ledger - SQLAlchemy models.

These are the new system-of-record tables per Plan v2. Legacy jobs/stages/links
remain for backward compat; new code lives here.

Design notes (Plan v2 §5, Critic fixes):
  - LedgerEvent is append-only, hash-chained (prev_hash + sha256(payload)).
  - Estate is the top-level container; everything hangs off it.
  - IR versions are JSONB blobs (versioned).
  - MigrationPlan + DiffRun + ContinuityCheck model the bridge pipeline.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from dsxlineage.db.database import Base

JsonCol = JSONB().with_variant(JSON(), "sqlite")


class Estate(Base):
    __tablename__ = "estates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)  # e.g. NORTHSTAR
    display_name = Column(String, default="NorthStar Synthetic Estate")
    description = Column(Text, default="On-prem Oracle DW: SAP + Salesforce + Flat Files -> DW -> BI. Synthetic POC.")
    source_type = Column(String, default="synthetic")  # synthetic | catalog_dump | hybrid
    estate_type = Column(String, default="banking", index=True)  # banking | telecom - for multi-estate allowance
    status = Column(String, default="ACTIVE")  # PENDING | ACTIVE | ARCHIVED - PENDING until survey completes
    # Mock connection for demo - {host, port, user, db_type, status, latency_ms, last_tested_at}
    connection = Column(JsonCol, default=dict)
    # Survey Todo - list of {key, label, status, count, detail} for live progress
    survey_todo = Column(JsonCol, default=list)
    current_stage = Column(String, default="pending")  # pending | discovering | extracting_tables | parsing_views | analyzing_procedures | mapping_etl | mapping_bi | building_graph | computing_analytics | done | failed
    last_survey_at = Column(DateTime(timezone=True))
    # IR versioning: which semver was last computed
    ir_version = Column(String, default="0.0.0")
    ir_generated_at = Column(DateTime(timezone=True))
    # counts cached for quick dashboard
    node_count = Column(Integer, default=0)
    edge_count = Column(Integer, default=0)
    unresolved_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    artifacts = relationship("EstateArtifact", back_populates="estate", cascade="all, delete-orphan")
    ledger_events = relationship("LedgerEvent", back_populates="estate", cascade="all, delete-orphan")
    migration_plans = relationship("MigrationPlan", back_populates="estate", cascade="all, delete-orphan")
    diff_runs = relationship("DiffRun", back_populates="estate", cascade="all, delete-orphan")


class EstateArtifact(Base):
    __tablename__ = "estate_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    estate_id = Column(Integer, ForeignKey("estates.id"), index=True, nullable=False)
    artifact_type = Column(String, index=True)  # ddl | procedure | etl | schedule | bi | csv
    fqn = Column(String, index=True)  # e.g. DW.CUSTOMER_DIM
    file_path = Column(String)  # relative to data/synthetic_estate/
    raw_hash = Column(String)  # sha256 of file content
    parsed_json = Column(JsonCol)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    estate = relationship("Estate", back_populates="artifacts")


class LedgerEvent(Base):
    """
    Append-only, hash-chained evidence ledger.

    Every state transition (ingest, AI action, plan approval, diff result,
    continuity decision, promote) is a row. prev_hash chains them like a
    simple blockchain; any tamper is detectable.

    event_type enum: ingest | ir_generated | ai_proposed | plan_created
                     | plan_approved | diff_run | continuity_checked
                     | promoted | chat_query

    Concurrency: append_ledger_event uses SELECT ... FOR UPDATE (where supported)
    and a UNIQUE constraint on (estate_id, id) + hash verification to detect races.
    Actor is now actor_id (UUID) + actor_email_hash for audit; `actor` kept as
    display name for backward compat.

    P0 fix: hash uses canonical_bytes (orjson if available) stored alongside.
    """

    __tablename__ = "ledger_events"

    id = Column(Integer, primary_key=True, index=True)
    estate_id = Column(Integer, ForeignKey("estates.id"), index=True, nullable=False)
    event_type = Column(String, index=True, nullable=False)
    actor = Column(String, default="system")  # display name (kept for compat)
    actor_id = Column(String, nullable=True)  # UUID from IdP (mocked in POC)
    actor_email_hash = Column(String, nullable=True)  # SHA-256 of email (PII-safe)
    idp_verified = Column(Boolean, default=False)
    payload = Column(JsonCol, nullable=False)  # event-specific JSON
    canonical_payload_hash = Column(String, nullable=True)  # sha256 of canonical payload bytes
    prev_hash = Column(String)  # hex of previous event's hash, or "GENESIS"
    event_hash = Column(String, index=True)  # sha256(prev_hash + canonical_payload_hash)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    estate = relationship("Estate", back_populates="ledger_events")

    @staticmethod
    def _canonical_bytes(payload: dict) -> bytes:
        # Use orjson if available for deterministic canonical, else json with sort_keys
        try:
            import orjson

            # orjson OPT_SORT_KEYS available in 3.10+
            return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)  # type: ignore
        except Exception:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    @staticmethod
    def compute_hash(prev_hash: str, payload: dict) -> str:
        canonical = LedgerEvent._canonical_bytes(payload)
        # Store hash of canonical separately; event_hash = sha256(prev_hash + ":" + canonical_hash)
        # For backward compat, keep old behavior as fallback: sha256(prev_hash + ":" + canonical_str)
        # Here we use canonical bytes hash
        payload_hash = hashlib.sha256(canonical).hexdigest()
        return hashlib.sha256(f"{prev_hash}:{payload_hash}".encode()).hexdigest()

    @staticmethod
    def compute_canonical_hash(payload: dict) -> str:
        return hashlib.sha256(LedgerEvent._canonical_bytes(payload)).hexdigest()


class MigrationPlan(Base):
    __tablename__ = "migration_plans"

    id = Column(Integer, primary_key=True, index=True)
    estate_id = Column(Integer, ForeignKey("estates.id"), index=True, nullable=False)
    name = Column(String, nullable=False)  # e.g. "Snowflake Wedge - Finance Mart"
    target_platform = Column(String, default="snowflake")  # snowflake | bigquery | synapse
    scope_description = Column(Text)
    # scope: list of FQNs included in this wedge
    scope_fqns = Column(JsonCol, default=list)
    status = Column(String, default="draft")  # draft | pending_approval | approved | rejected | executed
    # cost/risk estimates
    estimated_days = Column(Float)
    risk_level = Column(String, default="medium")  # low | medium | high
    # approval
    approved_by = Column(String)
    approved_at = Column(DateTime(timezone=True))
    # generated artifacts (after approval -> executed)
    generated_ddl_path = Column(String)
    generated_terraform_path = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    estate = relationship("Estate", back_populates="migration_plans")


class DiffRun(Base):
    __tablename__ = "diff_runs"

    id = Column(Integer, primary_key=True, index=True)
    estate_id = Column(Integer, ForeignKey("estates.id"), index=True, nullable=False)
    plan_id = Column(Integer, ForeignKey("migration_plans.id"), index=True)
    status = Column(String, default="pending")  # pending | running | passed | failed
    # tolerances used
    tolerances = Column(JsonCol)
    # results
    total_rows_compared = Column(Integer)
    mismatched_rows = Column(Integer, default=0)
    masked_columns = Column(JsonCol, default=list)
    sampling_method = Column(String, default="full")
    sampling_n = Column(Integer)
    bound_95 = Column(String)  # e.g. "3/n = 0.0003"
    # per-table breakdown
    per_table_results = Column(JsonCol)
    # lineage continuity sub-check
    continuity_passed = Column(Boolean, default=False)
    continuity_flags = Column(JsonCol, default=list)  # flagged diffs needing approval
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    estate = relationship("Estate", back_populates="diff_runs")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    estate_id = Column(Integer, ForeignKey("estates.id"), index=True, nullable=False)
    title = Column(String, default="New conversation")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), index=True, nullable=False)
    role = Column(String, nullable=False)  # user | assistant | tool
    content = Column(Text, nullable=False)
    # citations: list of FQNs cited
    citations = Column(JsonCol, default=list)
    # tool calls made
    tool_calls = Column(JsonCol, default=list)
    # refusal flag
    was_refused = Column(Boolean, default=False)
    # latency / cost
    latency_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")
