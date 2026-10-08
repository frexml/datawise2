"""Estate-side migration-scaffold adapter.

Generates a PySpark/Glue scaffold for one ETL job's real underlying .dsx
export, reusing services/migration_scaffold.py's rendering logic (topo
order, per-stage-type code emission, honest TODO/UNRESOLVED markers)
without that module's Job/Stage DB coupling - estate ETL jobs
(estate/ir.py's ETLJobDef) have no persisted Stage/Link rows, only a path
to the raw file (EstateIR.etl_jobs[].raw_path).

Scope, deliberately:
  - DataStage dialect only, same as the original module documents for
    SSIS/Informatica (this app doesn't parse those at DataStage's fidelity).
  - No LLM call. The deterministic join/sort/dedup/connector-path
    extraction (services/migration_ir.py) and deterministic expression
    translation (services/migration_translate.py's fast path) tiers run;
    anything neither resolves gets the same honest `# UNRESOLVED:` / TODO
    treatment the original renderer already produces. This keeps scaffold
    generation instant and free instead of an uncontrolled per-click LLM
    cost - the per-stage LLM explanation / LLM-translation tiers remain
    the old per-job pipeline's territory.
  - Edges: the synthetic .dsx samples carry no real CLink/CCustomInput /
    CCustomOutput pin records (verified across every sample under
    data/synthetic_estate{,_telco}/etl/), so there is no per-pin link data
    to reconstruct a dependency graph from. Falls back to the job's
    CContainerView.StageList ordering (present in every sample) to build a
    linear-chain edge list. If a job's raw export ever does carry real
    link records, they are not consumed here - verify stage ordering by
    hand for anything beyond the linear source -> transform -> sink shape
    the current samples have.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dsxlineage.agents.lib.detailed_analyzer import DSXAnalyzer
from dsxlineage.agents.lib.dsx_parser import parse_dsx
from dsxlineage.services.migration_ir import extract_datastage_ir
from dsxlineage.services.migration_scaffold import (
    _ORCHESTRATION_TYPES,
    _app_name,
    _build_body_lines,
    _guess_dialect,
    _render_glue,
    _render_header,
    _render_pyspark,
    _topo_order,
)


@dataclass
class _EstateStage:
    name: str
    type: str
    llm_explanation: str | None = None


class _JobStub:
    """Duck-typed stand-in for models.Job - _render_header only reads .filename."""

    def __init__(self, filename: str):
        self.filename = filename


def _stage_list_edges(containers: dict, id_to_name: dict[str, str]) -> list[tuple[str, str]]:
    """Build a linear-chain edge list from CContainerView.StageList ("V0S1|V0S2|V0S3")."""
    edges: list[tuple[str, str]] = []
    for container in containers.values():
        stage_list = container.get("StageList")
        if isinstance(stage_list, list):
            stage_list = stage_list[0] if stage_list else None
        if not stage_list:
            continue
        ids = [s for s in stage_list.split("|") if s in id_to_name]
        for src_id, tgt_id in zip(ids, ids[1:]):
            edges.append((id_to_name[src_id], id_to_name[tgt_id]))
    return edges


def generate_pyspark_scaffold(raw_path: str, job_label: str, target: str = "pyspark") -> str:
    """Generate a migration scaffold for one DataStage job's raw .dsx export.

    `job_label` (the estate ETL job FQN) is used only for the header
    comment / generated app name - there is no Job.filename here.
    """
    if target not in ("pyspark", "glue"):
        raise ValueError(f"Unsupported target '{target}' - expected 'pyspark' or 'glue'")
    if not Path(raw_path).exists():
        raise FileNotFoundError(f"Raw .dsx file not found: {raw_path}")

    parsed = parse_dsx(raw_path)
    analyzer = DSXAnalyzer(data=parsed)
    analyzer.load()
    analyzer.analyze()

    stages_raw = analyzer.stages  # {identifier: record}
    dialect = _guess_dialect({s.get("StageType") or s.get("OLEType") or "" for s in stages_raw.values()})

    id_to_name: dict[str, str] = {}
    dataflow_stages: list[_EstateStage] = []
    orchestration_stages: list[_EstateStage] = []
    for identifier, record in stages_raw.items():
        ole_type = record.get("OLEType", "") or ""
        name = record.get("Name") or identifier
        stage_type = record.get("StageType") or ole_type
        stage = _EstateStage(name=name, type=stage_type)
        id_to_name[identifier] = name
        if ole_type in _ORCHESTRATION_TYPES:
            orchestration_stages.append(stage)
        else:
            dataflow_stages.append(stage)

    stage_by_name = {s.name: s for s in dataflow_stages}
    names = list(stage_by_name.keys())
    edges = [
        (src, tgt) for src, tgt in _stage_list_edges(analyzer.containers, id_to_name)
        if src in stage_by_name and tgt in stage_by_name
    ]

    in_degree = {n: 0 for n in names}
    out_degree = {n: 0 for n in names}
    predecessors: dict[str, list[str]] = {n: [] for n in names}
    for src, tgt in edges:
        out_degree[src] += 1
        in_degree[tgt] += 1
        predecessors[tgt].append(src)

    order, had_cycle = _topo_order(names, edges)

    ir_by_name = None
    translations_by_stage: dict[str, list] = {}
    if dialect == "IBM DataStage":
        try:
            ir_by_name = extract_datastage_ir(raw_path)
            ir_status = (
                "real join/sort/dedup keys, connector paths, and per-column derivations extracted "
                "from the raw .dsx for JOIN/SORT/DEDUP/SOURCE/SINK/TRANSFORM stages where present; "
                "LOOKUP/FILTER/ROUTER/AGGREGATE/UNION and anything not deterministically resolvable "
                "use honest TODO/UNRESOLVED markers (no LLM call made for this estate-level scaffold)"
            )
        except Exception as exc:  # pragma: no cover - defensive, mirrors the original module
            ir_status = f"extraction failed ({exc}) - falling back to TODO markers for all stages"
    else:
        ir_status = "not attempted - only implemented for DataStage jobs so far"

    unresolved_report: list[str] = []
    job = _JobStub(filename=job_label)
    target_label = "PySpark" if target == "pyspark" else "AWS Glue"

    header_lines = _render_header(
        job, target_label, dataflow_stages, orchestration_stages, len(edges), dialect, had_cycle,
        ir_status, unresolved_report,
    )

    if not dataflow_stages:
        body_lines = [
            "",
            "# No data-flow stages found in this job - it appears to be a pure",
            "# job-sequence/orchestration job with no direct data transformations",
            "# to scaffold. See the excluded activities listed above.",
        ]
    else:
        body_lines = _build_body_lines(
            order, stage_by_name, in_degree, out_degree, predecessors,
            ir_by_name, translations_by_stage, unresolved_report,
        )
        header_lines = _render_header(
            job, target_label, dataflow_stages, orchestration_stages, len(edges), dialect, had_cycle,
            ir_status, unresolved_report,
        )

    app_name = _app_name(job_label)
    return (
        _render_pyspark(header_lines, body_lines, app_name)
        if target == "pyspark"
        else _render_glue(header_lines, body_lines, app_name)
    )
