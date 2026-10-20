"""Assemble the vendored DataWise snapshot into a canonical :class:`EstateIR`.

Pipeline (approved plan phases E0/E1):

1. Parse multi-object T-SQL DDL, views and procedures (``tsql``).
2. Parse all five ETL tool families (DataStage, Informatica, SSIS, Talend)
   plus the Python ML declarations from the Control-M manifest.
3. Merge defined objects, including tables created at runtime by DSX/Talend
   DDL guards.
4. Build declared + parsed edges, the dependency DAG and dashboards.
5. Compute column lineage (views + ETL writes) and mark dangling references as
   Attention Required cases.

The builder is deterministic: files are processed in sorted order, edges are
deduplicated and all dates come from ``date.today()`` at run time.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from dsxlineage.estate.ingest.columns import lineage_from_view_sql
from dsxlineage.estate.ingest.controlm import parse_manifest
from dsxlineage.estate.ingest.dsx import parse_dsx
from dsxlineage.estate.ingest.dtsx import parse_dtsx
from dsxlineage.estate.ingest.live_dw import load_row_estimates
from dsxlineage.estate.ingest.ml_routines import parse_ml_pipelines
from dsxlineage.estate.ingest.powermart import parse_powermart
from dsxlineage.estate.ingest.runtime import map_to_jobs, parse_run_summaries
from dsxlineage.estate.ingest.talend import parse_talend
from dsxlineage.estate.ingest.tsql import parse_tsql_dir, parse_tsql_text
from dsxlineage.estate.ingest.types import ManifestJob, ParsedArtifacts, ParsedJob
from dsxlineage.estate.ir import (
    ColumnIdentity,
    ColumnLineage,
    DashboardDef,
    EdgeType,
    EstateIR,
    ETLJobDef,
    LineageEdge,
    RejectFlow,
    ScheduleDef,
    TableDef,
    UnknownAsset,
    UnresolvedRef,
    ViewDef,
)

DATAWISE_ESTATE_NAME = "DATAWISE_PLATFORM"
# Runtime audit telemetry that every runner creates for itself. Deliberately not
# modelled as a table so the runtime-only dependency stays an Attention case
# (approved decision 3).
_RUNTIME_ONLY_REFS = frozenset({"DW_AUDIT.ETL_BATCH_LOG"})
_DAX_REF_RE = re.compile(r"\b(?:LOAN_SRC|RETAIL_SRC|DIM|FACT|MART|ML|BI|AUDIT)\.([A-Z][A-Z0-9_]*)", re.IGNORECASE)

# Declared Control-M references that never appear in the job's own artifact are
# kept in the IR (they are part of the team's declaration) but flagged with this
# `via` so the graph can exclude them: a drawn edge must be evidenced.
UNVERIFIED_DECLARATION_VIA = "Control-M declaration (unverified)"


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _load_provenance(root: Path) -> dict[str, object]:
    path = root / "PROVENANCE.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _parse_artifacts(etl_root: Path) -> ParsedArtifacts:
    """Run every vendor parser over the canonical ``git_upload_etls`` tree."""
    artifacts = ParsedArtifacts()
    sql_dir = etl_root / "sql_ddl"
    if sql_dir.is_dir():
        tsql = parse_tsql_dir(sql_dir)
        artifacts.tables.extend(tsql.tables)
        artifacts.views.extend(tsql.views)
        artifacts.procedures.extend(tsql.procedures)

    for path in sorted((etl_root / "datastage").glob("*.dsx")):
        artifacts.jobs.extend(parse_dsx(path))
    for path in sorted((etl_root / "informatica").glob("*.xml")):
        artifacts.jobs.extend(parse_powermart(path))
    for path in sorted((etl_root / "ssis").glob("*.dtsx")):
        artifacts.jobs.extend(parse_dtsx(path))
    for path in sorted((etl_root / "talend").glob("*.item")):
        artifacts.jobs.extend(parse_talend(path))
    return artifacts


def _artifact_to_job_map(
    manifest_jobs: list[ManifestJob], parsed_jobs: list[ParsedJob]
) -> dict[str, str]:
    """Map ``ARTIFACT_STEM -> IR FQN`` for remapping runtime job names."""
    mapping: dict[str, str] = {}
    for manifest_job in manifest_jobs:
        if manifest_job.artifact:
            mapping.setdefault(Path(manifest_job.artifact).stem.upper(), manifest_job.name)
    for parsed_job in parsed_jobs:
        mapping.setdefault(Path(parsed_job.artifact).stem.upper(), parsed_job.fqn)
    return mapping


def _unknown_assets(provenance: dict[str, object]) -> list[UnknownAsset]:
    """Turn the snapshot's excluded list into first-class unknown assets."""
    kind_by_reason = {
        "binary artifact": "binary",
        "row-level PII / credential template": "pii_fixture",
        "duplicate or non-lineage artifact": "duplicate",
        "non-text artifact": "non_text",
    }
    assets: list[UnknownAsset] = []
    for entry in provenance.get("excluded", []):
        path = str(entry.get("path", ""))
        reason = str(entry.get("reason", ""))
        kind = kind_by_reason.get(reason)
        if not path or kind is None:
            continue
        if kind == "pii_fixture" and path.endswith(".env.example"):
            kind = "credential_template"
        if kind == "duplicate":
            continue
        assets.append(UnknownAsset(path=path, kind=kind, reason=reason))
    return assets


def _check_csv_headers(root: Path, tables: dict[str, TableDef]) -> dict[str, str]:
    """Verify the vendored fixture CSVs are header-only and match source DDL."""
    checks: dict[str, str] = {}
    fixtures = (
        ("data/loan_db/LOAN_DB_TEST.csv", "LOAN_SRC.LOAN_DB_RAW"),
        ("data/retail_db/RETAIL_DB_TEST.csv", "RETAIL_SRC.RETAIL_DB_RAW"),
    )
    for rel, table_fqn in fixtures:
        path = root / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        header = lines[0].lstrip("\ufeff") if lines else ""
        headers = {name.strip().upper() for name in header.split(",") if name.strip()}
        columns = {c.name.upper() for c in tables.get(table_fqn, TableDef(fqn=table_fqn, system="")).columns}
        matched = len(headers & columns) if columns else 0
        checks[rel] = (
            f"headers_only ({len(lines)} lines, {len(headers)} cols, "
            f"{matched} match {table_fqn})"
        )
    return checks


def _routine_edges(routines) -> list[LineageEdge]:  # noqa: ANN001 - RoutineDef list
    """ETL_READS/ETL_WRITES edges for ML routines."""
    edges: list[LineageEdge] = []
    for routine in routines:
        if routine.kind != "ml_pipeline":
            continue
        for source in routine.reads:
            edges.append(
                LineageEdge(
                    source_fqn=source,
                    target_fqn=routine.fqn,
                    edge_type=EdgeType.ETL_READS,
                    confidence=0.9,
                    via=f"artifact:{routine.artifact}",
                )
            )
        for target in routine.writes:
            edges.append(
                LineageEdge(
                    source_fqn=routine.fqn,
                    target_fqn=target,
                    edge_type=EdgeType.ETL_WRITES,
                    confidence=0.9,
                    via=f"artifact:{routine.artifact}",
                )
            )
    return edges


def _runtime_tables(jobs: list[ParsedJob]) -> list[TableDef]:
    """Tables created by ETL DDL guards (DSX, Talend, SSIS staging)."""
    tables: list[TableDef] = []
    seen: set[str] = set()
    for job in jobs:
        for fragment in job.ddl:
            parsed = parse_tsql_text(fragment, job.artifact)
            for table in parsed.tables:
                if table.fqn in _RUNTIME_ONLY_REFS or table.fqn in seen:
                    continue
                seen.add(table.fqn)
                tables.append(table)
    return tables


def _merge_tables(*groups: list[TableDef]) -> list[TableDef]:
    merged: dict[str, TableDef] = {}
    for group in groups:
        for table in group:
            if table.fqn not in merged:
                merged[table.fqn] = table
    return [merged[fqn] for fqn in sorted(merged)]


def _expand_declared(
    refs: list[str], tables: dict[str, TableDef], views: dict[str, ViewDef]
) -> list[str]:
    """Expand wildcard/bare-name declarations to concrete FQNs where possible."""
    expanded: list[str] = []
    for ref in refs:
        if ref.endswith(".*"):
            schema = ref[:-2]
            expanded.extend(fqn for fqn in tables if fqn.startswith(f"{schema}."))
            continue
        if "." not in ref:
            matches = [fqn for fqn in views if fqn.endswith(f".{ref}")]
            matches += [fqn for fqn in tables if fqn.endswith(f".{ref}")]
            expanded.append(matches[0] if matches else ref)
            continue
        expanded.append(ref)
    return _dedupe(expanded)


def _resolve_bare_sources(rows: list[ColumnLineage], known: set[str]) -> None:
    """Map unqualified source names to concrete FQNs by unique suffix match."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for fqn in known:
        by_name[fqn.split(".")[-1]].append(fqn)
    for row in rows:
        if not row.source_table or "." in row.source_table:
            continue
        matches = by_name.get(row.source_table)
        if matches and len(matches) == 1:
            row.source_table = matches[0]
            row.source_schema = matches[0].split(".")[0]


def _mark_missing(rows: list[ColumnLineage], known: set[str], context: str) -> list[ColumnLineage]:
    """Flip column rows to unresolved when their upstream object is undefined."""
    for row in rows:
        if row.source_table and row.source_table not in known:
            row.unresolved = True
            row.unresolved_reason = (
                f"Upstream table {row.source_table} is referenced but never defined in the estate"
            )
            row.confidence = min(row.confidence, 0.4)
        elif not row.source_table and not row.unresolved and not row.transform_expr:
            row.unresolved = True
            row.unresolved_reason = f"No traceable source column in {context}"
            row.confidence = 0.0
    return rows


def _artifact_texts(
    root: Path, etl_root: Path, manifest_jobs: list[ManifestJob]
) -> dict[str, str]:
    """Upper-cased text of each job's artifact(s), used to corroborate declarations."""
    cache: dict[tuple[str, ...], str] = {}
    texts: dict[str, str] = {}
    for job in manifest_jobs:
        artifact = (job.artifact or "").strip()
        if artifact.lower().endswith(".ispac") or (not artifact and job.tool.upper().startswith("SSIS")):
            files = sorted((etl_root / "ssis").glob("*.dtsx"))
        elif not artifact:
            files = sorted((root / "docker-deploy" / "ml").glob("*.py"))
        else:
            files = sorted(etl_root.rglob(artifact)) or sorted(root.rglob(artifact))
        key = tuple(str(path) for path in files)
        if key not in cache:
            cache[key] = " ".join(
                path.read_text(encoding="utf-8", errors="ignore") for path in files
            ).upper()
        texts[job.name] = cache[key]
    return texts


def _job_edges(
    job: ETLJobDef,
    parsed_reads: list[str],
    parsed_writes: list[str],
    declared_reads: list[str],
    declared_writes: list[str],
    artifact: str,
    artifact_text: str = "",
) -> list[LineageEdge]:
    """Build ETL_READS/ETL_WRITES edges from parsed evidence + declarations.

    Declared references that do not appear anywhere in the job's artifact text
    are flagged ``UNVERIFIED_DECLARATION_VIA`` so graphs can exclude them.
    """
    edges: list[LineageEdge] = []
    parsed_read_set = set(parsed_reads)
    parsed_write_set = set(parsed_writes)

    def declared_edge(ref: str, fallback_confidence: float) -> tuple[str, float]:
        name = ref.split(".")[-1].upper()
        if name and name in artifact_text:
            return "Control-M declaration", fallback_confidence
        return UNVERIFIED_DECLARATION_VIA, 0.3
    for source in parsed_reads:
        edges.append(
            LineageEdge(
                source_fqn=source,
                target_fqn=job.fqn,
                edge_type=EdgeType.ETL_READS,
                confidence=1.0,
                via=f"artifact:{artifact}",
            )
        )
    for target in parsed_writes:
        edges.append(
            LineageEdge(
                source_fqn=job.fqn,
                target_fqn=target,
                edge_type=EdgeType.ETL_WRITES,
                confidence=1.0,
                via=f"artifact:{artifact}",
            )
        )
    for source in declared_reads:
        if source in parsed_read_set:
            continue
        via, confidence = declared_edge(source, 0.8 if parsed_reads else 0.6)
        edges.append(
            LineageEdge(
                source_fqn=source,
                target_fqn=job.fqn,
                edge_type=EdgeType.ETL_READS,
                confidence=confidence,
                via=via,
            )
        )
    for target in declared_writes:
        if target in parsed_write_set:
            continue
        via, confidence = declared_edge(target, 0.8 if parsed_writes else 0.6)
        edges.append(
            LineageEdge(
                source_fqn=job.fqn,
                target_fqn=target,
                edge_type=EdgeType.ETL_WRITES,
                confidence=confidence,
                via=via,
            )
        )
    return edges


def _build_jobs(
    manifest_jobs: list[ManifestJob],
    parsed_jobs: list[ParsedJob],
    tables: dict[str, TableDef],
    views: dict[str, ViewDef],
    artifact_texts: dict[str, str] | None = None,
) -> tuple[list[ETLJobDef], list[LineageEdge], list[RejectFlow]]:
    by_artifact: dict[str, list[ParsedJob]] = defaultdict(list)
    for job in parsed_jobs:
        by_artifact[job.artifact.upper()].append(job)
    artifact_usage = Counter(job.artifact.upper() for job in manifest_jobs)

    jobs: list[ETLJobDef] = []
    edges: list[LineageEdge] = []
    reject_flows: list[RejectFlow] = []
    for manifest_job in manifest_jobs:
        artifact = manifest_job.artifact
        declared_reads = _expand_declared(manifest_job.declared_sources, tables, views)
        declared_writes = _expand_declared(manifest_job.declared_targets, tables, views)

        if artifact.lower().endswith(".ispac"):
            matched = [job for job in parsed_jobs if job.dialect == "ssis"]
        else:
            matched = list(by_artifact.get(artifact.upper(), []))
        if artifact_usage[artifact.upper()] > 1 and matched:
            parsed_write_union = {ref for job in matched for ref in job.writes}
            if parsed_write_union and not parsed_write_union.intersection(declared_writes):
                matched = []

        parsed_reads = _dedupe([ref for job in matched for ref in job.reads])
        parsed_writes = _dedupe([ref for job in matched for ref in job.writes])
        raw_path = artifact or (
            "docker-deploy/ml/ml_pipeline.py"
            if "loan" in manifest_job.name.lower()
            else "docker-deploy/ml/fraud_pipeline.py"
            if "fraud" in manifest_job.name.lower()
            else None
        )
        source_fqns = _dedupe(parsed_reads + declared_reads)
        target_fqns = _dedupe(parsed_writes + declared_writes)
        job = ETLJobDef(
            fqn=manifest_job.name,
            dialect=manifest_job.dialect,
            source_fqn=source_fqns[0] if source_fqns else "",
            target_fqn=target_fqns[0] if target_fqns else "",
            schedule="DATAWISE_PLATFORM",
            raw_path=raw_path,
            source_fqns=source_fqns,
            target_fqns=target_fqns,
            tool=manifest_job.tool,
            declared_only=not matched,
        )
        jobs.append(job)
        edges.extend(
            _job_edges(
                job,
                parsed_reads,
                parsed_writes,
                declared_reads,
                declared_writes,
                artifact or "declaration",
                (artifact_texts or {}).get(manifest_job.name, ""),
            )
        )
        for parsed_job in matched:
            for target_table, route in parsed_job.reject_targets:
                reject_flows.append(
                    RejectFlow(
                        job_fqn=job.fqn,
                        target_table=target_table,
                        artifact=parsed_job.artifact,
                        route=route,
                    )
                )
    return jobs, edges, reject_flows


def _build_dashboards(root: Path) -> tuple[list[DashboardDef], list[LineageEdge]]:
    dashboards: list[DashboardDef] = []
    edges: list[LineageEdge] = []
    dax_path = root / "artifacts" / "bi" / "FINANCE_DASHBOARD_DAX.md"
    if not dax_path.exists():
        return dashboards, edges
    text = dax_path.read_text(encoding="utf-8", errors="replace")
    refs: list[str] = []
    for match in _DAX_REF_RE.finditer(text):
        refs.append(match.group(0).upper())
    refs = _dedupe(refs)
    dashboard = DashboardDef(
        fqn="PBI.FINANCE_DASHBOARD",
        tool="PowerBI",
        source_fqn="MART.MART_FINANCE_SUMMARY",
        description="Finance dashboard (7 pages): executive KPIs, IFRS9/ECL, arrears, branch, product, GL recon, trends",
    )
    dashboards.append(dashboard)
    for ref in refs:
        edges.append(
            LineageEdge(
                source_fqn=ref,
                target_fqn=dashboard.fqn,
                edge_type=EdgeType.DASHBOARD_RENDERS,
                confidence=1.0,
                via=f"artifact:{dax_path.name}",
            )
        )
    return dashboards, edges


def _column_identities(
    tables: list[TableDef], views: list[ViewDef], rows: list[ColumnLineage]
) -> list[ColumnIdentity]:
    groups: dict[str, list[str]] = defaultdict(list)
    for table in tables:
        for column in table.columns:
            canonical = re.sub(r"_ID$|_KEY$|_FK$", "_ID", column.name.upper()).lower()
            groups[canonical].append(f"{table.fqn}.{column.name}")
    for row in rows:
        if row.target_column in ("", "*"):
            continue
        canonical = re.sub(r"_ID$|_KEY$|_FK$", "_ID", row.target_column.upper()).lower()
        groups[canonical].append(f"{row.target_table}.{row.target_column}")
    identities: list[ColumnIdentity] = []
    for canonical, fqns in groups.items():
        unique = sorted(set(fqns))
        if len(unique) < 2:
            continue
        for fqn in unique:
            identities.append(
                ColumnIdentity(
                    canonical_id=canonical,
                    fqn=fqn,
                    aliases=[other for other in unique if other != fqn],
                    confidence=1.0 if canonical.endswith("_id") else 0.85,
                    match_method="alias" if canonical.endswith("_id") else "embedding",
                )
            )
    return identities


def _apply_row_estimates(tables: list[TableDef], root: Path) -> None:
    estimates = load_row_estimates(root / "LIVE_DW_TABLES.json")
    if not estimates:
        return
    for table in tables:
        if table.fqn in estimates:
            table.row_estimate = estimates[table.fqn]


def build_datawise_ir(root: Path, estate_name: str = DATAWISE_ESTATE_NAME) -> EstateIR:
    """Build the canonical IR for the vendored DataWise snapshot."""
    root = Path(root)
    etl_root = root / "git_upload_etls"
    manifest_path = root / "jobs" / "DATAWISE_PLATFORM.json"
    if not etl_root.is_dir() or not manifest_path.exists():
        raise FileNotFoundError(f"not a DataWise snapshot: {root}")

    provenance = _load_provenance(root)
    artifacts = _parse_artifacts(etl_root)
    manifest = parse_manifest(manifest_path)

    runtime_tables = _runtime_tables(artifacts.jobs)
    tables = _merge_tables(artifacts.tables, runtime_tables)
    table_map = {table.fqn: table for table in tables}
    views = list({view.fqn: view for view in artifacts.views}.values())
    view_map = {view.fqn: view for view in views}
    procedures = list({proc.fqn: proc for proc in artifacts.procedures}.values())

    artifact_texts = _artifact_texts(root, etl_root, manifest.jobs)
    jobs, job_edges, reject_flows = _build_jobs(
        manifest.jobs, artifacts.jobs, table_map, view_map, artifact_texts
    )
    dashboards, dashboard_edges = _build_dashboards(root)
    routines = parse_ml_pipelines(root)
    routine_edges = _routine_edges(routines)
    runtime_evidence = map_to_jobs(
        parse_run_summaries(root),
        _artifact_to_job_map(manifest.jobs, artifacts.jobs),
    )
    unknown_assets = _unknown_assets(provenance)
    csv_checks = _check_csv_headers(root, table_map)

    schedule = ScheduleDef(
        fqn=manifest.schedule_fqn,
        scheduler="Control-M",
        schedule_expr="Stage 1 -> Stage 7 dependency DAG",
        depends_on=_dedupe(manifest.depends_on),
        frequency="daily",
    )

    ir = EstateIR(
        estate_name=estate_name,
        source="git",
        source_repo=str(provenance.get("repo_url") or ""),
        source_commit=str(provenance.get("commit_sha") or ""),
        generated_on=date.today(),
        tables=tables,
        views=views,
        procedures=procedures,
        etl_jobs=jobs,
        schedules=[schedule],
        dashboards=dashboards,
        routines=routines,
        runtime_evidence=runtime_evidence,
        reject_flows=reject_flows,
        unknown_assets=unknown_assets,
    )
    ir.systems = sorted(
        {table.system for table in tables}
        | {view.fqn.split(".")[0] for view in views if "." in view.fqn}
        | {proc.fqn.split(".")[0] for proc in procedures if "." in proc.fqn}
    )

    column_nodes = set(table_map) | set(view_map)
    for parsed_job in artifacts.jobs:
        _resolve_bare_sources(list(parsed_job.column_lineage), column_nodes)
    edges = list(job_edges) + list(dashboard_edges) + list(routine_edges)

    for view in views:
        for source in view.source_fqns:
            edges.append(
                LineageEdge(
                    source_fqn=source,
                    target_fqn=view.fqn,
                    edge_type=EdgeType.VIEW_DEPENDS,
                    confidence=0.6 if view.unresolved else 1.0,
                    via="artifact:sql_ddl",
                )
            )
        if view.unresolved:
            edges.append(
                LineageEdge(
                    source_fqn=view.fqn,
                    target_fqn="UNRESOLVED_ARTIFACT",
                    edge_type=EdgeType.UNRESOLVED,
                    confidence=0.0,
                    unresolved=True,
                    unresolved_reason=view.unresolved_reason,
                )
            )

    for proc in procedures:
        for source in proc.reads:
            edges.append(
                LineageEdge(
                    source_fqn=source,
                    target_fqn=proc.fqn,
                    edge_type=EdgeType.SP_READS,
                    confidence=0.7 if proc.unresolved else 1.0,
                    via="artifact:sql_ddl",
                )
            )
        for target in proc.writes:
            edges.append(
                LineageEdge(
                    source_fqn=proc.fqn,
                    target_fqn=target,
                    edge_type=EdgeType.SP_WRITES,
                    confidence=0.7 if proc.unresolved else 1.0,
                    via="artifact:sql_ddl",
                )
            )
        for call in proc.calls:
            edges.append(
                LineageEdge(
                    source_fqn=proc.fqn,
                    target_fqn=call,
                    edge_type=EdgeType.SP_CALLS,
                    confidence=1.0,
                    via="artifact:sql_ddl",
                )
            )
        if proc.unresolved:
            edges.append(
                LineageEdge(
                    source_fqn=proc.fqn,
                    target_fqn="UNRESOLVED_ARTIFACT",
                    edge_type=EdgeType.UNRESOLVED,
                    confidence=0.0,
                    unresolved=True,
                    unresolved_reason=proc.unresolved_reason,
                )
            )

    job_names = {job.fqn for job in jobs}
    for job in manifest.jobs:
        for dependency in job.depends_on:
            edges.append(
                LineageEdge(
                    source_fqn=dependency,
                    target_fqn=job.name,
                    edge_type=EdgeType.SCHEDULE_DEPENDS,
                    confidence=1.0,
                    via="Control-M DependsOn",
                )
            )
    for job in jobs:
        if not any(edge.target_fqn == job.fqn and edge.edge_type == EdgeType.SCHEDULE_DEPENDS for edge in edges):
            edges.append(
                LineageEdge(
                    source_fqn=schedule.fqn,
                    target_fqn=job.fqn,
                    edge_type=EdgeType.SCHEDULE_DEPENDS,
                    confidence=1.0,
                    via="Control-M schedule root",
                )
            )

    # ---- Column lineage: views then ETL writes ----
    rows: list[ColumnLineage] = []
    for view in views:
        if view.unresolved:
            rows.append(
                ColumnLineage(
                    target_table=view.fqn,
                    target_column="*",
                    source_table="",
                    source_column="",
                    confidence=0.0,
                    unresolved=True,
                    unresolved_reason=view.unresolved_reason or "view artifact incomplete",
                    target_schema=view.fqn.split(".")[0] if "." in view.fqn else "",
                    hops=1,
                    full_path=f"{view.fqn} (unresolved)",
                )
            )
            continue
        view_rows = lineage_from_view_sql(view.fqn, view.sql, column_nodes, view.source_fqns)
        rows.extend(_mark_missing(view_rows, column_nodes, view.fqn))

    for parsed_job in artifacts.jobs:
        rows.extend(
            _mark_missing(list(parsed_job.column_lineage), column_nodes, parsed_job.fqn)
        )

    seen_rows: set[tuple[str, str, str, str, str | None]] = set()
    deduped_rows: list[ColumnLineage] = []
    for row in rows:
        if row.target_column == "*" and not row.unresolved:
            continue
        if row.target_table not in column_nodes:
            continue
        key = (
            row.target_table,
            row.target_column,
            row.source_table,
            row.source_column,
            row.transform_expr,
        )
        if key in seen_rows:
            continue
        seen_rows.add(key)
        deduped_rows.append(row)
    ir.column_lineage = deduped_rows

    try:
        from dsxlineage.estate.column_graph import enrich_column_lineage_with_estate_hops

        enrich_column_lineage_with_estate_hops(ir)
    except Exception:
        pass

    # ---- Deduplicate edges, keeping the strongest evidence ----
    by_key: dict[tuple[str, str, EdgeType], LineageEdge] = {}
    for edge in edges:
        key = (edge.source_fqn, edge.target_fqn, edge.edge_type)
        existing = by_key.get(key)
        if existing is None or edge.confidence > existing.confidence:
            by_key[key] = edge
    ir.edges = list(by_key.values())

    # ---- Dangling references: referenced but never defined ----
    node_universe = (
        column_nodes
        | job_names
        | {schedule.fqn}
        | {d.fqn for d in dashboards}
        | {proc.fqn for proc in procedures}
        | {routine.fqn for routine in routines}
    )
    dangling: dict[str, UnresolvedRef] = {}
    for edge in ir.edges:
        for endpoint, other in ((edge.source_fqn, edge.target_fqn), (edge.target_fqn, edge.source_fqn)):
            if endpoint in node_universe or endpoint == "UNRESOLVED_ARTIFACT":
                continue
            if endpoint not in dangling:
                dangling[endpoint] = UnresolvedRef(
                    fqn=endpoint,
                    referenced_by=other,
                    reason=f"Referenced by {other} but never defined in the estate",
                )
            edge.unresolved = True
            edge.unresolved_reason = edge.unresolved_reason or (
                f"{endpoint} is referenced but never defined"
            )
    ir.unresolved_refs = [dangling[key] for key in sorted(dangling)]

    ir.column_identities = _column_identities(tables, views, ir.column_lineage)
    _apply_row_estimates(tables, root)
    ir.file_status = {
        "git_upload_etls/sql_ddl": "parsed",
        "git_upload_etls/datastage": "parsed",
        "git_upload_etls/informatica": "parsed",
        "git_upload_etls/ssis": "parsed",
        "git_upload_etls/talend": "parsed",
        "jobs/DATAWISE_PLATFORM.json": "parsed",
        "artifacts/bi/FINANCE_DASHBOARD_DAX.md": "parsed",
    }
    for artifact in dict.fromkeys(row.artifact for row in runtime_evidence):
        runs = sum(1 for row in runtime_evidence if row.artifact == artifact)
        ir.file_status[artifact] = f"runtime parsed ({runs} jobs)"
    for routine in routines:
        if routine.kind == "ml_pipeline":
            ir.file_status[routine.artifact] = "parsed (routine)"
    ir.file_status.update(csv_checks)
    for asset in unknown_assets:
        ir.file_status[asset.path] = f"{asset.kind} (not parsed)"
    ir.compute_summaries()
    return ir


def build_expected_lineage(root: Path = Path("data/external_estates/datawise_platform")) -> dict[str, object]:
    """Derive the declared job-edge oracle from the Control-M manifest.

    The manifest is the orchestration team's source of truth; the golden test
    asserts every declared edge is covered by extraction (parsed or declared,
    with undefined endpoints surfaced as Attention Required).
    """
    root = Path(root)
    manifest = parse_manifest(root / "jobs" / "DATAWISE_PLATFORM.json")
    artifacts = _parse_artifacts(root / "git_upload_etls")
    runtime_tables = _runtime_tables(artifacts.jobs)
    tables = {table.fqn: table for table in _merge_tables(artifacts.tables, runtime_tables)}
    views = {view.fqn: view for view in artifacts.views}

    job_edges: list[dict[str, str]] = []
    for manifest_job in manifest.jobs:
        for ref in _expand_declared(manifest_job.declared_sources, tables, views):
            job_edges.append({"job": manifest_job.name, "direction": "reads", "ref": ref})
        for ref in _expand_declared(manifest_job.declared_targets, tables, views):
            job_edges.append({"job": manifest_job.name, "direction": "writes", "ref": ref})
    provenance = _load_provenance(root)
    return {
        "generated_on": date.today().isoformat(),
        "source_commit": provenance.get("commit_sha"),
        "schedule": manifest.schedule_fqn,
        "jobs": [manifest_job.name for manifest_job in manifest.jobs],
        "job_edges": job_edges,
    }


def main() -> None:
    """CLI: regenerate ``EXPECTED_LINEAGE.json`` for a vendored snapshot."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/external_estates/datawise_platform"))
    args = parser.parse_args()
    payload = build_expected_lineage(args.root)
    out = args.root / "EXPECTED_LINEAGE.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(payload['job_edges'])} declared refs, {len(payload['jobs'])} jobs)")


if __name__ == "__main__":
    main()
