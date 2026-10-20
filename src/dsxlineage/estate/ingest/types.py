"""Shared result types for external-estate artifact parsers.

Each parser returns vendor-neutral fragments; :mod:`dsxlineage.estate.ingest.build`
merges them into a single :class:`~dsxlineage.estate.ir.EstateIR`. Keeping the
fragments free of estate-specific wiring is what lets the same parsers be reused
for other Git-ingested estates later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dsxlineage.estate.ir import ColumnLineage, DashboardDef, ProcedureDef, TableDef, ViewDef


@dataclass
class ParsedJob:
    """One ETL artifact (DSX job, mapping, package, Talend job) reduced to edges."""

    fqn: str
    dialect: str
    artifact: str
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    column_lineage: list[ColumnLineage] = field(default_factory=list)
    ddl: list[str] = field(default_factory=list)  # CREATE/ALTER fragments found in the artifact
    reject_targets: list[tuple[str, str]] = field(default_factory=list)  # (target table, route name)
    source_text: str | None = None


@dataclass
class ParsedTSQL:
    """Everything a directory of T-SQL scripts contributes."""

    tables: list[TableDef] = field(default_factory=list)
    views: list[ViewDef] = field(default_factory=list)
    procedures: list[ProcedureDef] = field(default_factory=list)


@dataclass
class ManifestJob:
    """A Control-M manifest entry."""

    name: str
    job_type: str
    tool: str
    description: str
    artifact: str
    host: str
    declared_sources: list[str] = field(default_factory=list)
    declared_targets: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    @property
    def dialect(self) -> str:
        """Map the declared tool to an IR ETL dialect."""
        tool = self.tool.lower()
        if "datastage" in tool:
            return "datastage"
        if "informatica" in tool:
            return "informatica"
        if "ssis" in tool or "adf" in tool:
            return "ssis"
        if "talend" in tool:
            return "talend"
        if "python" in tool:
            return "python"
        return "controlm"


@dataclass
class Manifest:
    """The whole Control-M manifest: one schedule plus its jobs."""

    schedule_fqn: str
    jobs: list[ManifestJob] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass
class ParsedArtifacts:
    """Aggregate of all vendor parsers, ready for :func:`build_datawise_ir`."""

    tables: list[TableDef] = field(default_factory=list)
    views: list[ViewDef] = field(default_factory=list)
    procedures: list[ProcedureDef] = field(default_factory=list)
    jobs: list[ParsedJob] = field(default_factory=list)
    dashboards: list[DashboardDef] = field(default_factory=list)
    file_status: dict[str, str] = field(default_factory=dict)
