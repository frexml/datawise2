"""
EstateIR - versioned, vendor-neutral intermediate representation for the
estate modernization accelerator.

This is the system-of-record IR per Brief §3 / Plan v2. Unlike the legacy
generic {stages,links} dict, EstateIR is typed (Pydantic v2), versioned,
and covers the full estate: tables, views, columns, procedures, jobs,
dashboards, and edges with confidence + unresolved markers.

Implements hard problems from Brief §4.2:
  - semantic mismatches modeled via Tolerances
  - stored procedures dynamic SQL flagged as unresolved
  - lineage identity via ColumnIdentity (entity resolution, not string compare)
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SystemType(str, Enum):
    SOURCE = "source"
    WAREHOUSE = "warehouse"
    BI = "bi"
    ORCHESTRATION = "orchestration"
    ETL = "etl"


class EdgeType(str, Enum):
    VIEW_DEPENDS = "VIEW_DEPENDS"
    SP_READS = "SP_READS"
    SP_WRITES = "SP_WRITES"
    SP_CALLS = "SP_CALLS"
    ETL_READS = "ETL_READS"
    ETL_WRITES = "ETL_WRITES"
    SCHEDULE_DEPENDS = "SCHEDULE_DEPENDS"
    DASHBOARD_RENDERS = "DASHBOARD_RENDERS"
    COLUMN_LINEAGE = "COLUMN_LINEAGE"
    UNRESOLVED = "UNRESOLVED"


class Confidence(float, Enum):
    HIGH = 1.0
    MEDIUM = 0.7
    LOW = 0.4
    UNRESOLVED = 0.0


class ColumnDef(BaseModel):
    name: str = Field(description="Column name, upper-cased")
    data_type: str = Field(default="VARCHAR2(100)")
    nullable: bool = True
    pk: bool = False


class TableDef(BaseModel):
    fqn: str = Field(description="Fully qualified name, e.g. DW.CUSTOMER_DIM")
    system: str = Field(description="System name, e.g. SAP_ECC, DW, SALESFORCE")
    columns: list[ColumnDef] = Field(default_factory=list)
    row_estimate: int | None = None
    last_modified: date | None = None
    # For analytics: how many downstream consumers (computed post-IR)
    downstream_count: int = 0


class ViewDef(BaseModel):
    fqn: str
    sql: str
    is_materialized: bool = False
    complexity: Literal["simple", "join", "aggregate", "nested_view", "cross_system", "dynamic"] = "simple"
    # Parsed sources - filled by sqlglot extractor
    source_fqns: list[str] = Field(default_factory=list)
    unresolved: bool = False
    unresolved_reason: str | None = None


class ProcedureDef(BaseModel):
    fqn: str
    language: str = Field(description="PL/SQL, T-SQL, etc.")
    proc_type: str = Field(default="transform")
    reads: list[str] = Field(default_factory=list, description="Table/View FQNs read")
    writes: list[str] = Field(default_factory=list, description="Table/View FQNs written")
    calls: list[str] = Field(default_factory=list, description="Procedures called")
    has_dynamic: bool = False
    has_cursor: bool = False
    complexity: Literal["low", "medium", "high"] = "low"
    # Source text for determinism checks
    source_text: str | None = None


class ETLJobDef(BaseModel):
    fqn: str
    dialect: Literal["datastage", "ssis", "informatica"] = "datastage"
    source_fqn: str
    target_fqn: str
    schedule: str | None = None
    raw_path: str | None = None


class ScheduleDef(BaseModel):
    fqn: str
    scheduler: str = Field(description="Control-M, DataStage Sequence, cron")
    schedule_expr: str
    depends_on: list[str] = Field(default_factory=list)
    frequency: str = "daily"


class DashboardDef(BaseModel):
    fqn: str
    tool: str = Field(description="Tableau, Cognos, PowerBI")
    source_fqn: str = Field(description="View or table rendered")
    description: str = ""


class LineageEdge(BaseModel):
    source_fqn: str
    target_fqn: str
    edge_type: EdgeType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    via: str | None = Field(default=None, description="SQL or derivation that produced this edge")
    unresolved: bool = False
    unresolved_reason: str | None = None


class ColumnLineage(BaseModel):
    target_table: str
    target_column: str
    source_table: str
    source_column: str
    transform_expr: str | None = None
    confidence: float = 1.0
    via_edge: str | None = None


class ColumnIdentity(BaseModel):
    """Entity-resolution for lineage continuity (hard problem #4)."""

    canonical_id: str = Field(description="Stable ID for the logical column, e.g. customer_id")
    fqn: str = Field(description="Physical FQN, e.g. DW.CUSTOMER_DIM.CUSTOMER_ID")
    aliases: list[str] = Field(default_factory=list, description="Other FQNs that resolve to same canonical")
    data_type: str = "VARCHAR2(100)"
    confidence: float = 1.0
    match_method: Literal["fk", "alias", "embedding", "manual"] = "alias"


class Tolerances(BaseModel):
    numeric_epsilon: float = 0.01
    temporal_tolerance_seconds: int = 1
    collation: Literal["case_sensitive", "case_insensitive"] = "case_insensitive"
    null_equals_empty: bool = False
    masked_columns: list[str] = Field(default_factory=list, description="FQN.COL to exclude from diffs")
    sampling_method: Literal["full", "hash_stratified"] = "full"


class EstateIR(BaseModel):
    """Versioned, vendor-neutral IR - core IP."""

    version: str = Field(default="1.0.0")
    estate_name: str = Field(default="NORTHSTAR")
    generated_on: date = Field(default_factory=date.today)
    source: str = Field(default="synthetic", description="synthetic | catalog_dump | hybrid")

    systems: list[str] = Field(default_factory=list)
    tables: list[TableDef] = Field(default_factory=list)
    views: list[ViewDef] = Field(default_factory=list)
    procedures: list[ProcedureDef] = Field(default_factory=list)
    etl_jobs: list[ETLJobDef] = Field(default_factory=list)
    schedules: list[ScheduleDef] = Field(default_factory=list)
    dashboards: list[DashboardDef] = Field(default_factory=list)

    edges: list[LineageEdge] = Field(default_factory=list, description="All graph edges, deduplicated")
    column_lineage: list[ColumnLineage] = Field(default_factory=list)
    column_identities: list[ColumnIdentity] = Field(default_factory=list)

    tolerances: Tolerances = Field(default_factory=Tolerances)

    # Computed summaries
    total_nodes: int = 0
    total_edges: int = 0
    unresolved_count: int = 0

    def compute_summaries(self) -> None:
        self.total_nodes = len(self.tables) + len(self.views) + len(self.procedures) + len(self.etl_jobs) + len(self.schedules) + len(self.dashboards)
        self.total_edges = len(self.edges)
        self.unresolved_count = sum(1 for e in self.edges if e.unresolved)

    model_config = {"extra": "forbid"}
