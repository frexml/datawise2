from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, Text, Boolean, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from dsxlineage.db.database import Base

# Use JSONB on Postgres for indexable, binary-packed storage of hot JSON
# columns. Falls back to plain JSON if SQLAlchemy ever runs against SQLite
# (e.g., backend/app.db dev leftover).
JsonCol = JSONB().with_variant(JSON(), "sqlite")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    status = Column(String, default="PENDING") # PENDING, PROCESSING, COMPLETED, FAILED
    current_stage = Column(String)  # parsing, analyzing, mapping_lineage, generating_summaries,
                                     # saving_results, detecting_inefficiencies, completed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    results = relationship("Result", back_populates="job", cascade="all, delete-orphan")
    stages = relationship("Stage", back_populates="job", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="job", cascade="all, delete-orphan")
    annotations = relationship("Annotation", back_populates="job", cascade="all, delete-orphan")
    lineages = relationship("Lineage", back_populates="job", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="job", cascade="all, delete-orphan")
    scopeiq_estimate = relationship("ScopeIQEstimate", back_populates="job", uselist=False, cascade="all, delete-orphan")

class Result(Base):
    __tablename__ = "results"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    raw_json = Column(JsonCol)  # The full parsed JSON
    analysis_summary = Column(JsonCol)  # The detailed analysis report
    llm_explanation = Column(Text) # The AI generated explanation (Executive Summary, technical)
    business_summary = Column(Text)  # Plain-language business summary (see deep_analyzer_agent)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job", back_populates="results")

class Stage(Base):
    __tablename__ = "stages"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    stage_id = Column(String, index=True) # The internal ID (e.g., V0S1)
    name = Column(String)
    type = Column(String)
    properties = Column(JsonCol)  # Extracted properties (including TrxGenCode)
    llm_explanation = Column(Text)  # The AI generated explanation for this stage

    job = relationship("Job", back_populates="stages")

class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    link_id = Column(String, index=True) # The internal ID
    name = Column(String)
    source_stage = Column(String) # Name or ID of source
    target_stage = Column(String) # Name or ID of target
    source_pin = Column(String) # Source pin ID (e.g., V0S27P2)
    target_pin = Column(String) # Target pin ID (e.g., V0S57P1)
    properties = Column(JsonCol)  # Schema and other properties
    llm_explanation = Column(Text)  # The AI generated explanation for this link

    job = relationship("Job", back_populates="links")

class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    annotation_id = Column(String, index=True)
    name = Column(String)
    text = Column(Text)  # The content of the annotation
    properties = Column(JsonCol)
    llm_explanation = Column(Text) # The AI generated explanation
    
    job = relationship("Job", back_populates="annotations")

class Lineage(Base):
    __tablename__ = "lineages"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    target_table = Column(String)
    target_field = Column(String)
    source_table = Column(String)
    source_field = Column(String)
    source_link = Column(String)
    target_link = Column(String)
    full_path = Column(Text)
    total_hops = Column(Integer)
    transformation_logic = Column(Text)
    transformation_explanation = Column(Text)
    transformation_type = Column(String)
    cardinality = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job", back_populates="lineages")

class Review(Base):
    """Human review/approval audit trail for LLM-generated summaries.

    Decoupled from Job.status (PENDING/PROCESSING/COMPLETED/FAILED), which
    tracks pipeline execution only — the frontend polls and branches on that
    field and must not be affected by review state.
    """
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    target_type = Column(String)  # e.g. "executive_summary"
    target_id = Column(Integer)  # id of the target row (e.g. Result.id)
    status = Column(String, default="pending_review")  # pending_review, approved, rejected, regenerating
    reviewer = Column(String)
    feedback = Column(Text)  # required on reject; carried forward as context if re-run is chosen
    edited_by_reviewer = Column(Boolean, default=False)  # True if approved via direct hand-edit, not as-is
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job", back_populates="reviews")

class ScopeIQEstimate(Base):
    """Per-job delivery-effort estimate produced by the ScopeIQ agent.

    One row per job (regenerating overwrites in place, like Result) — this
    is a derived analytical product, not an audit trail like Review.
    """
    __tablename__ = "scopeiq_estimates"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), unique=True, index=True)
    status = Column(String, default="pending")  # pending, generating, completed, failed
    package_id = Column(String)
    dimensions = Column(JsonCol)  # list of {dimension, scope_brief, findings, role_days, uplift_signals, risks}
    role_day_totals = Column(JsonCol)  # {role: days} summed across dimensions, pre-adjustment
    uplift_adjustments = Column(JsonCol)  # [{dimension, signal, uplift_pct, rationale}]
    risk_adjustments = Column(JsonCol)  # [{dimension, description, impact_days, likelihood}]
    total_days_base = Column(Float)
    total_days_adjusted = Column(Float)
    complexity_tier = Column(String)  # low, medium, high
    error = Column(Text)  # populated when status == "failed"
    generated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job", back_populates="scopeiq_estimate")
