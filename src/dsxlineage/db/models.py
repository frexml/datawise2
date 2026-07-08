from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, Text
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    results = relationship("Result", back_populates="job", cascade="all, delete-orphan")
    stages = relationship("Stage", back_populates="job", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="job", cascade="all, delete-orphan")
    annotations = relationship("Annotation", back_populates="job", cascade="all, delete-orphan")
    lineages = relationship("Lineage", back_populates="job", cascade="all, delete-orphan")

class Result(Base):
    __tablename__ = "results"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    raw_json = Column(JsonCol)  # The full parsed JSON
    analysis_summary = Column(JsonCol)  # The detailed analysis report
    llm_explanation = Column(Text) # The AI generated explanation (Executive Summary)
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
