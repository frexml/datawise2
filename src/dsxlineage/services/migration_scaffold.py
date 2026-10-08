"""PySpark / AWS Glue migration scaffold generator.

Generates a skeleton script wired according to the job's actual stage/link
graph - sources, transforms, and sinks sequenced in topological order -
with each stage annotated by its original dialect-specific type and its
AI-derived transformation summary (Stage.llm_explanation).

For DataStage (.dsx) jobs whose raw upload is still on disk
(Job.stored_filename), JOIN/SORT/DEDUP/SOURCE/SINK/TRANSFORM stages are
backed by real data re-extracted from the raw file (services/migration_ir.py):
actual join/sort/dedup keys, connector table names and file paths, and
per-column derivations translated into real PySpark expressions - most
deterministically (services/migration_translate.py's fast path), the
remainder via a structured-output LLM call scoped to one stage's unresolved
expressions at a time. Anything the LLM isn't confident about is left as an
explicit `# UNRESOLVED:` comment rather than a guess presented as fact.

LOOKUP/FILTER/ROUTER/AGGREGATE/UNION stages, any DataStage job whose raw
file isn't available, and all SSIS/Informatica jobs fall back to the
original behavior: a generic TODO placeholder plus the AI-derived prose
summary (Stage.llm_explanation) - this app doesn't parse SSIS/Informatica
expressions at the fidelity DataStage's TrxGenCode allows, and pretending
otherwise would be worse than an honest TODO.

Orchestration activities (DataStage Job Sequence stages: CJobActivity /
CRoutineActivity / CSequencer, and their SSIS/Informatica equivalents) are
job-control, not data transforms - excluded from the scaffold body, but
listed in the header so nothing is silently dropped.
"""
import io
import os
import re
from datetime import date

from sqlalchemy.orm import Session

from dsxlineage.db import models
from dsxlineage.services.migration_ir import StageIR, extract_datastage_ir
from dsxlineage.services.migration_translate import (
    ExpressionTranslation,
    translate_deterministic,
)

UPLOAD_DIR = "uploads"

_JOIN_TYPE_MAP = {
    "innerjoin": "inner",
    "leftouterjoin": "left",
    "rightouterjoin": "right",
    "fullouterjoin": "full",
}

_ORCHESTRATION_TYPES = {
    "CJobActivity", "CRoutineActivity", "CSequencer",
    "CJSJobActivity", "CJSRoutineActivity", "CJSSequencer",
}

_RULE_WIDTH = 70


def _categorize(stage_type: str) -> str:
    """Type-name pattern match - used only for stages with both an upstream
    and a downstream link. Endpoints (no upstream / no downstream) are
    classified as SOURCE/SINK by graph topology instead (see
    generate_migration_scaffold), since the same connector stage type
    (e.g. DataStage's PxSequentialFile) is used for both roles depending on
    configuration, not on a distinct type string."""
    t = (stage_type or "").lower()
    if "join" in t:
        return "JOIN"
    if "sort" in t:
        return "SORT"
    if "remdup" in t or "dedup" in t or "duplicate" in t:
        return "DEDUP"
    if "aggreg" in t:
        return "AGGREGATE"
    if "lookup" in t:
        return "LOOKUP"
    if "filter" in t:
        return "FILTER"
    if "router" in t or "conditionalsplit" in t or "switch" in t:
        return "ROUTER"
    if "funnel" in t or "unionall" in t or t == "union":
        return "UNION"
    return "TRANSFORM"  # generic fallback - covers CTransformerStage, Expression, DerivedColumn, ...


def _guess_format(stage_type: str) -> str:
    """Best-effort connector-format hint for the TODO - not authoritative."""
    t = (stage_type or "").lower()
    if any(k in t for k in ("oracle", "db2", "connector", "jdbc", "oledb", "definition", "sql")):
        return "jdbc"
    return "csv"


def _guess_dialect(stage_types: set[str]) -> str:
    """Cosmetic only - for the header comment."""
    types = [t for t in stage_types if t]
    if any("microsoft." in t.lower() for t in types):
        return "SSIS"
    if any(t in ("Source Definition", "Target Definition", "InformaticaConnector") for t in types):
        return "Informatica PowerCenter"
    if any(t.startswith("Px") or t.startswith("C") for t in types):
        return "IBM DataStage"
    return "unknown ETL dialect"


def _safe_var(name: str, seen: dict[str, int]) -> str:
    base = re.sub(r"\W", "_", name or "stage").strip("_") or "stage"
    if base[0].isdigit():
        base = f"s_{base}"
    if base not in seen:
        seen[base] = 0
        return base
    seen[base] += 1
    return f"{base}_{seen[base]}"


_ANALYSIS_TAG_RE = re.compile(r"</?analysis>", re.IGNORECASE)
_MD_HEADER_RE = re.compile(r"^#{1,6}\s*.*$", re.MULTILINE)
_STAGE_OVERVIEW_RE = re.compile(
    r"###\s*Stage Overview\s*\n+(.+?)(?:\n\s*\n|\n#{1,6}\s|\Z)", re.IGNORECASE | re.DOTALL
)


def _todo_summary(stage: models.Stage) -> str:
    """deep_analyzer_agent's per-stage explanations are markdown with a
    "### Stage Overview" section, "### Configuration Analysis", etc. - a
    plain first-N-characters truncation mostly grabs boilerplate headers.
    Pull the actual overview paragraph instead; fall back to a generic
    strip-and-truncate for explanations that don't follow that shape
    (e.g. link/annotation text)."""
    raw = (stage.llm_explanation or "").strip()
    if not raw:
        return "no AI-derived summary available - inspect the original stage directly"

    text = _ANALYSIS_TAG_RE.sub("", raw)
    match = _STAGE_OVERVIEW_RE.search(text)
    summary = match.group(1) if match else _MD_HEADER_RE.sub("", text)
    summary = re.sub(r"\s+", " ", summary).strip()

    if len(summary) > 200:
        summary = summary[:200].rstrip() + "…"
    return summary or "no AI-derived summary available - inspect the original stage directly"


def _topo_order(names: list[str], edges: list[tuple[str, str]]) -> tuple[list[str], bool]:
    """Kahn's algorithm, stable (ties break by original stage order).
    Returns (order, had_cycle) - stages left over due to a cycle are
    appended at the end in original order rather than dropped."""
    in_degree = {n: 0 for n in names}
    adjacency: dict[str, list[str]] = {n: [] for n in names}
    for src, tgt in edges:
        if src in adjacency and tgt in in_degree:
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

    remaining = dict(in_degree)
    queue = [n for n in names if remaining[n] == 0]
    order: list[str] = []
    while queue:
        queue.sort(key=names.index)  # stable: always prefer original order among ties
        node = queue.pop(0)
        order.append(node)
        for nxt in adjacency[node]:
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                queue.append(nxt)

    had_cycle = len(order) != len(names)
    if had_cycle:
        order.extend(n for n in names if n not in order)
    return order, had_cycle


def _group_derivations_by_link(derivations: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for d in derivations:
        grouped.setdefault(d["output_link"], []).append(d)
    return grouped


def _emit_transform_body(
    var: str,
    src: str,
    ir: StageIR,
    translations: list[ExpressionTranslation],
    unresolved_report: list[str],
    stage_name: str,
) -> list[str]:
    """Real per-derivation .withColumn() calls, sequential (not chained) so
    350+ derivation stages stay readable. translations covers whatever
    translate_deterministic() couldn't resolve, keyed by (output_column,
    expression) - see migration_translate.py for why that pair, not just
    output_column, is the match key."""
    translation_by_key = {(t.output_column, t.expression): t for t in translations}

    grouped = _group_derivations_by_link(ir.derivations)
    link_names = list(grouped.keys())

    lines: list[str] = []
    if len(link_names) > 1:
        lines.append(
            f"# NOTE: this Transformer stage fans out to {len(link_names)} output links "
            f"({', '.join(link_names)}) - each gets its own DataFrame below (df_{var}__<link>). "
            f"Verify which downstream stage should actually consume each one; this scaffold "
            f"only wires df_{var} to the link with the most derivations as a best guess."
        )
    primary_link = max(link_names, key=lambda l: len(grouped[l])) if link_names else None

    for link_name in link_names:
        link_var = var if link_name == primary_link and len(link_names) == 1 else f"{var}__{_safe_var(link_name, {})}"
        lines.append(f"df_{link_var} = df_{src}")
        for d in grouped[link_name]:
            col, expr = d["output_column"], d["expression"]
            spark_expr = translate_deterministic(expr)
            if spark_expr is not None:
                lines.append(f'df_{link_var} = df_{link_var}.withColumn("{col}", {spark_expr})')
                continue

            match = translation_by_key.get((col, expr))
            if match is not None and match.confident:
                lines.append(f'df_{link_var} = df_{link_var}.withColumn("{col}", {match.pyspark_expression})')
            elif match is not None:
                lines.append(f"# UNRESOLVED: {col} = {expr}")
                lines.append(f"#   best-effort guess (unverified): {match.pyspark_expression}")
                lines.append(f"#   why: {match.note}")
                unresolved_report.append(f"{stage_name}.{col} = {expr}  -  {match.note}")
            else:
                lines.append(f"# UNRESOLVED: {col} = {expr}  (no translation available)")
                unresolved_report.append(f"{stage_name}.{col} = {expr}  -  no translation available")

    if len(link_names) > 1:
        primary_var = f"{var}__{_safe_var(primary_link, {})}"
        lines.append(f"df_{var} = df_{primary_var}  # best-guess default - see NOTE above")

    return lines


def _build_body_lines(
    order: list[str],
    stage_by_name: dict[str, models.Stage],
    in_degree: dict[str, int],
    out_degree: dict[str, int],
    predecessors: dict[str, list[str]],
    ir_by_name: dict[str, StageIR] | None,
    translations_by_stage: dict[str, list[ExpressionTranslation]],
    unresolved_report: list[str],
) -> list[str]:
    var_names: dict[str, str] = {}
    seen_vars: dict[str, int] = {}
    for n in order:
        var_names[n] = _safe_var(n, seen_vars)

    lines: list[str] = []
    for stage_name in order:
        stage = stage_by_name[stage_name]
        var = var_names[stage_name]
        pred_vars = [var_names[p] for p in predecessors.get(stage_name, []) if p in var_names]
        ir = (ir_by_name or {}).get(stage_name)

        if in_degree[stage_name] == 0:
            category = "SOURCE"
        elif out_degree[stage_name] == 0:
            category = "SINK"
        else:
            category = _categorize(stage.type)

        lines.append("")
        lines.append("# " + "─" * _RULE_WIDTH)
        lines.append(f"# {stage_name}  [{category}, original type: {stage.type}]")
        lines.append(f"# {_todo_summary(stage)}")
        lines.append("# " + "─" * _RULE_WIDTH)

        if category == "SOURCE":
            fmt = _guess_format(stage.type)
            if ir and ir.file_path:
                lines.append(
                    f'df_{var} = spark.read.format("{fmt}").load("{ir.file_path}")  '
                    f"# NOTE: path may contain DataStage job parameters (e.g. #Param#) - substitute real values"
                )
            elif ir and ir.connector_table:
                lines.append(
                    f'df_{var} = spark.read.format("jdbc").option("dbtable", "{ir.connector_table}").load()  '
                    f"# NOTE: set JDBC url/driver/user/password options for the target connection"
                )
            else:
                lines.append(
                    f'df_{var} = spark.read.format("{fmt}").load('
                    f'"TODO: source path/table for \'{stage_name}\'")'
                )
        elif category == "SINK":
            fmt = _guess_format(stage.type)
            if pred_vars and ir and ir.connector_table:
                lines.append(
                    f'df_{pred_vars[0]}.write.format("jdbc").mode("overwrite")'
                    f'.option("dbtable", "{ir.connector_table}").save()  '
                    f"# NOTE: set JDBC url/driver/user/password options for the target connection"
                )
                if ir.before_sql:
                    lines.append(f"# Original BeforeSQL (reference only, not auto-executed):")
                    lines.extend(f"#   {sql_line}" for sql_line in ir.before_sql.splitlines())
                if ir.after_sql:
                    lines.append(f"# Original AfterSQL (reference only, not auto-executed):")
                    lines.extend(f"#   {sql_line}" for sql_line in ir.after_sql.splitlines())
            elif pred_vars and ir and ir.file_path:
                lines.append(
                    f'df_{pred_vars[0]}.write.format("{fmt}").mode("overwrite").save("{ir.file_path}")  '
                    f"# NOTE: path may contain DataStage job parameters (e.g. #Param#) - substitute real values"
                )
            elif pred_vars:
                lines.append(
                    f'df_{pred_vars[0]}.write.format("{fmt}").mode("overwrite").save('
                    f'"TODO: target path/table for \'{stage_name}\'")'
                )
            else:
                lines.append(f"# TODO: SINK stage has no detected upstream input - verify manually")
        elif category == "JOIN":
            if ir and ir.join_keys and len(pred_vars) >= 2:
                how = _JOIN_TYPE_MAP.get((ir.join_type or "").lower())
                note = "" if how else f"  # NOTE: unrecognized DataStage join operator '{ir.join_type}' - defaulted how, verify"
                how = how or "inner"
                lines.append(
                    f'df_{var} = df_{pred_vars[0]}.join(df_{pred_vars[1]}, on={ir.join_keys!r}, how="{how}")' + note
                )
            elif len(pred_vars) >= 2:
                lines.append(
                    f'df_{var} = df_{pred_vars[0]}.join(df_{pred_vars[1]}, '
                    f'on=["TODO_JOIN_KEY"], how="inner")  '
                    f"# TODO: verify join keys/type from the original stage"
                )
            elif pred_vars:
                lines.append(
                    f"df_{var} = df_{pred_vars[0]}  "
                    f"# TODO: JOIN stage had only one detected upstream input - verify the second"
                )
            else:
                lines.append(f"df_{var} = None  # TODO: JOIN stage with no detected upstream inputs - verify manually")
        elif category == "SORT":
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            if ir and ir.sort_keys:
                order_exprs = ", ".join(f'F.col("{c}").{d}()' for c, d in ir.sort_keys)
                lines.append(f"df_{var} = df_{src}.orderBy({order_exprs})")
            else:
                lines.append(f'df_{var} = df_{src}.orderBy("TODO_SORT_KEY")')
        elif category == "DEDUP":
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            if ir and ir.dedup_keys:
                lines.append(f"df_{var} = df_{src}.dropDuplicates({ir.dedup_keys!r})")
                keep_note = f"keep='{ir.dedup_keep}'" if ir.dedup_keep else "keep mode unspecified"
                lines.append(
                    f"# NOTE: original DataStage Remove Duplicates stage used {keep_note} - Spark's "
                    f"dropDuplicates() has no deterministic first/last row-ordering guarantee. If which "
                    f"row survives matters, sort deterministically upstream and verify against DataStage semantics."
                )
            else:
                lines.append(f'df_{var} = df_{src}.dropDuplicates(["TODO_KEY_COLUMNS"])')
        elif category == "AGGREGATE":
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            lines.append(f'df_{var} = df_{src}.groupBy("TODO_GROUP_COLUMNS").agg()  # TODO: port aggregation logic')
        elif category == "LOOKUP":
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            ref = pred_vars[1] if len(pred_vars) > 1 else "TODO_LOOKUP_DF"
            lines.append(
                f'df_{var} = df_{src}.join(df_{ref}, on=["TODO_LOOKUP_KEY"], how="left")  '
                f"# TODO: verify lookup source + keys"
            )
        elif category == "FILTER":
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            lines.append(f'df_{var} = df_{src}.filter("TODO_CONDITION")')
        elif category == "ROUTER":
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            lines.append(
                f"df_{var} = df_{src}  # TODO: ROUTER/conditional-split stage - port each output "
                f"branch's condition manually (this scaffold reads the same upstream for all branches)"
            )
        elif category == "UNION":
            if len(pred_vars) >= 2:
                stmt = f"df_{var} = df_{pred_vars[0]}"
                for p in pred_vars[1:]:
                    stmt += f".unionByName(df_{p}, allowMissingColumns=True)"
                lines.append(stmt)
            else:
                src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
                lines.append(f"df_{var} = df_{src}  # TODO: UNION/funnel stage with fewer than 2 detected inputs")
        else:  # TRANSFORM - generic fallback, or real derivations if IR is available
            src = pred_vars[0] if pred_vars else "TODO_UPSTREAM_DF"
            if ir and ir.derivations:
                lines.extend(
                    _emit_transform_body(
                        var, src, ir, translations_by_stage.get(stage_name, []), unresolved_report, stage_name
                    )
                )
            else:
                lines.append(
                    f"df_{var} = df_{src}  # TODO: port transformation logic per the summary above - "
                    f"original derivations live in the source tool's expression/routine language, not captured here"
                )

    return lines


def _render_header(
    job: models.Job,
    target_label: str,
    dataflow_stages: list[models.Stage],
    orchestration_stages: list[models.Stage],
    edge_count: int,
    dialect: str,
    had_cycle: bool,
    ir_status: str,
    unresolved_report: list[str],
) -> list[str]:
    lines = [
        "# " + "═" * _RULE_WIDTH,
        f"# Auto-generated {target_label} migration scaffold for: {job.filename}",
        f"# Generated by DataWise on {date.today().isoformat()}",
        "#",
        "# This scaffold wires stages in dependency order from the parsed lineage",
        "# graph and annotates each with its original stage type. Where real",
        "# derivation/key/path data was recoverable from the raw source (see below),",
        "# it drives actual PySpark code, not a placeholder. Everything else falls",
        "# back to a TODO plus an AI-derived summary. Review every UNRESOLVED and TODO",
        "# marker by hand before running this against real data.",
        "#",
        f"# Source dialect (best guess): {dialect}",
        f"# Data-flow stages: {len(dataflow_stages)}  |  Links: {edge_count}",
        f"# Orchestration activities excluded (job-control, not data transforms): {len(orchestration_stages)}",
        f"# Derivation/key/path fidelity: {ir_status}",
    ]
    if had_cycle:
        lines += [
            "#",
            "# WARNING: a cycle was detected in the stage graph. Stage order past that",
            "# point falls back to original parse order and MUST be verified by hand.",
        ]
    if unresolved_report:
        lines.append("#")
        lines.append(f"# Human review required - {len(unresolved_report)} derivation(s) could not be confidently translated:")
        for item in unresolved_report:
            lines.append(f"#   - {item}")
    if orchestration_stages:
        lines.append("#")
        lines.append("# Excluded orchestration activities (review the original job for control flow):")
        for s in orchestration_stages:
            lines.append(f"#   - {s.name} ({s.type})")
    lines.append("# " + "═" * _RULE_WIDTH)
    return lines


def _app_name(filename: str) -> str:
    base = re.sub(r"\W", "_", (filename or "datawise_job").rsplit(".", 1)[0]).strip("_")
    return f"{base or 'datawise_job'}_migration"


def _render_pyspark(header_lines: list[str], body_lines: list[str], app_name: str) -> str:
    parts = header_lines + [
        "",
        "from pyspark.sql import SparkSession",
        "from pyspark.sql import functions as F",
        "",
        f'spark = SparkSession.builder.appName("{app_name}").getOrCreate()',
    ] + body_lines + [
        "",
        "spark.stop()",
        "",
    ]
    return "\n".join(parts)


def _render_glue(header_lines: list[str], body_lines: list[str], app_name: str) -> str:
    parts = header_lines + [
        "",
        "import sys",
        "from awsglue.transforms import *",
        "from awsglue.utils import getResolvedOptions",
        "from pyspark.context import SparkContext",
        "from awsglue.context import GlueContext",
        "from awsglue.job import Job",
        "from pyspark.sql import functions as F",
        "",
        'args = getResolvedOptions(sys.argv, ["JOB_NAME"])',
        "sc = SparkContext()",
        "glueContext = GlueContext(sc)",
        "spark = glueContext.spark_session",
        "job = Job(glueContext)",
        f'job.init(args["JOB_NAME"], args)',
    ] + body_lines + [
        "",
        "job.commit()",
        "",
    ]
    return "\n".join(parts)


async def _build_ir_and_translations(
    raw_path: str, dataflow_stages: list[models.Stage]
) -> tuple[dict[str, StageIR], dict[str, list[ExpressionTranslation]]]:
    """Deterministic IR extraction (always), plus one LLM call per stage that
    has any derivation translate_deterministic() couldn't resolve (never a
    whole-job call - see migration_translate.py's module docstring)."""
    ir_by_name = extract_datastage_ir(raw_path)

    stages_needing_llm: dict[str, list[dict]] = {}
    for stage in dataflow_stages:
        ir = ir_by_name.get(stage.name)
        if not ir or not ir.derivations:
            continue
        unresolved = [
            {"output_column": d["output_column"], "expression": d["expression"]}
            for d in ir.derivations
            if translate_deterministic(d["expression"]) is None
        ]
        if unresolved:
            stages_needing_llm[stage.name] = unresolved

    translations_by_stage: dict[str, list[ExpressionTranslation]] = {}
    if stages_needing_llm:
        from langchain_openai import ChatOpenAI

        from dsxlineage.core.config import settings
        from dsxlineage.services.migration_translate import (
            StageExpressionTranslations,
            translate_stage_expressions,
        )

        llm = ChatOpenAI(
            model=settings.OPENROUTER_MODEL,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            max_tokens=4000,
        ).with_structured_output(StageExpressionTranslations)

        for stage_name, unresolved in stages_needing_llm.items():
            translations_by_stage[stage_name] = await translate_stage_expressions(llm, stage_name, unresolved)

    return ir_by_name, translations_by_stage


async def generate_migration_scaffold(job_id: int, db: Session, target: str = "pyspark") -> io.BytesIO:
    if target not in ("pyspark", "glue"):
        raise ValueError(f"Unsupported target '{target}' - expected 'pyspark' or 'glue'")

    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    all_stages = db.query(models.Stage).filter(models.Stage.job_id == job_id).all()
    links = db.query(models.Link).filter(models.Link.job_id == job_id).all()

    dataflow_stages = [s for s in all_stages if (s.type or "") not in _ORCHESTRATION_TYPES]
    orchestration_stages = [s for s in all_stages if (s.type or "") in _ORCHESTRATION_TYPES]
    dialect = _guess_dialect({s.type for s in all_stages})

    stage_by_name = {s.name: s for s in dataflow_stages}
    names = list(stage_by_name.keys())
    edges = [
        (link.source_stage, link.target_stage) for link in links
        if link.source_stage in stage_by_name and link.target_stage in stage_by_name
    ]

    in_degree = {n: 0 for n in names}
    out_degree = {n: 0 for n in names}
    predecessors: dict[str, list[str]] = {n: [] for n in names}
    for src, tgt in edges:
        out_degree[src] += 1
        in_degree[tgt] += 1
        predecessors[tgt].append(src)

    order, had_cycle = _topo_order(names, edges)

    ir_by_name: dict[str, StageIR] | None = None
    translations_by_stage: dict[str, list[ExpressionTranslation]] = {}
    ir_status = "not attempted - only implemented for DataStage jobs so far"

    if dialect == "IBM DataStage":
        if not job.stored_filename:
            ir_status = "unavailable - this job predates raw-source retention; falling back to AI-summary TODOs"
        else:
            raw_path = os.path.join(UPLOAD_DIR, job.stored_filename)
            if not os.path.exists(raw_path):
                ir_status = "unavailable - raw source file is missing from disk; falling back to AI-summary TODOs"
            else:
                try:
                    ir_by_name, translations_by_stage = await _build_ir_and_translations(raw_path, dataflow_stages)
                    ir_status = (
                        "real join/sort/dedup keys, connector paths, and per-column derivations extracted "
                        "from the raw .dsx for JOIN/SORT/DEDUP/SOURCE/SINK/TRANSFORM stages; "
                        "LOOKUP/FILTER/ROUTER/AGGREGATE/UNION still use AI-summary TODOs"
                    )
                except Exception as exc:  # pragma: no cover - defensive: never fail the whole export over this
                    ir_status = f"extraction failed ({exc}) - falling back to AI-summary TODOs for all stages"
                    ir_by_name, translations_by_stage = None, {}

    unresolved_report: list[str] = []

    header_lines = _render_header(
        job, "PySpark" if target == "pyspark" else "AWS Glue",
        dataflow_stages, orchestration_stages, len(edges), dialect, had_cycle,
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
        # unresolved_report is populated as a side effect of _build_body_lines;
        # re-render the header now that it's known.
        header_lines = _render_header(
            job, "PySpark" if target == "pyspark" else "AWS Glue",
            dataflow_stages, orchestration_stages, len(edges), dialect, had_cycle,
            ir_status, unresolved_report,
        )

    app_name = _app_name(job.filename)
    text = (
        _render_pyspark(header_lines, body_lines, app_name)
        if target == "pyspark"
        else _render_glue(header_lines, body_lines, app_name)
    )

    buffer = io.BytesIO(text.encode("utf-8"))
    buffer.seek(0)
    return buffer
