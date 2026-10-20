"""Golden tests for the vendored DataWise external estate ingestion.

Pins the approved plan gates (docs/external-estate-plan.md): full object/job
counts, truncation flags, the 16 genuine dangling references, DSX truncation
recovery, alias resolution regressions and live-date provenance.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dsxlineage.estate.ingest.build import build_datawise_ir
from dsxlineage.estate.ir import EdgeType

SNAPSHOT = Path("data/external_estates/datawise_platform")

pytestmark = pytest.mark.skipif(
    not (SNAPSHOT / "jobs" / "DATAWISE_PLATFORM.json").exists(),
    reason="vendored DataWise snapshot not present",
)

PLAN_DANGLING = {
    "FACT.FACT_CAPITAL",
    "DIM.DIM_LOAN_STATUS",
    "DW_AUDIT.ETL_BATCH_LOG",
    "DIM.LOANOFFICERS",
    "DIM.CUSTOMERS",
    "DIM.BRANCHES",
    "DIM.PRODUCTS",
    "FACT.LOANS",
    "FACT.FACT_GL_TRANSACTION",
    "FACT.VW_GL_SUMMARY",
    "FACT.VW_PAYMENT_SUMMARY",
    "FACT.VW_TRANSACTION_SUMMARY",
    "MART.VW_BRANCH_PERFORMANCE",
    "MART.VW_CREDIT_RISK_SUMMARY",
    "AUDIT.AUDIT_CONFIG",
    "AUDIT.AUDIT_LOG",
}

TRUNCATED_VIEWS = {
    "DIM.VW_CUSTOMER_HISTORY",
    "FACT.VW_ACTIVE_LOANS",
    "MART.VW_RISK_DASHBOARD",
    "BI.VW_FRAUD_MONITOR",
}

TRUNCATED_PROCEDURES = {"FACT.PROC_RECON_MASTER", "ML.PROC_FEATURE_REFRESH"}

CSV_FIXTURES = {
    "data/loan_db/LOAN_DB_TEST.csv": "LOAN_SRC.LOAN_DB_RAW",
    "data/retail_db/RETAIL_DB_TEST.csv": "RETAIL_SRC.RETAIL_DB_RAW",
}


@pytest.fixture(scope="module")
def ir():
    return build_datawise_ir(SNAPSHOT)


def test_object_and_job_counts(ir):
    assert len(ir.views) == 13
    assert len(ir.procedures) == 5
    assert len(ir.etl_jobs) == 19
    assert len(ir.tables) >= 43
    assert [d.fqn for d in ir.dashboards] == ["PBI.FINANCE_DASHBOARD"]


def test_all_manifest_jobs_present(ir):
    manifest = json.loads((SNAPSHOT / "jobs" / "DATAWISE_PLATFORM.json").read_text())
    names = {j.get("JobName") or j.get("name") for j in manifest.get("Jobs", manifest if isinstance(manifest, list) else [])}
    known = {job.fqn for job in ir.etl_jobs}
    assert names <= known, names - known
    declared = {job.fqn for job in ir.etl_jobs if job.declared_only}
    assert declared == {
        "STAGE2_TAL_DIM_DATE_POPULATE",
        "STAGE6_ML_LOAN_APPROVAL_SCORING",
        "STAGE6_ML_FRAUD_SCORING",
    }


def test_plan_dangling_refs_are_attention_cases(ir):
    refs = {ref.fqn for ref in ir.unresolved_refs}
    assert PLAN_DANGLING <= refs
    assert len(refs) >= len(PLAN_DANGLING)


def test_truncated_artifacts_flagged_not_dropped(ir):
    assert {v.fqn for v in ir.views if v.unresolved} == TRUNCATED_VIEWS
    assert {p.fqn for p in ir.procedures if p.unresolved} == TRUNCATED_PROCEDURES


def test_dsx_truncation_recovery(ir):
    job = next(j for j in ir.etl_jobs if j.fqn == "STAGE4_DS_FACT_GL_BUILD")
    assert "FACT.FACT_PAYMENT" in job.source_fqns
    assert "MART.MART_GL_PERIOD_SUMMARY" in job.target_fqns
    rows = [row for row in ir.column_lineage if row.target_table == "FACT.FACT_GL"]
    assert rows
    assert any(not row.unresolved for row in rows)


def test_column_lineage_alias_resolution(ir):
    junk = {"LA", "PA", "TA", "VEL"}
    assert not [row for row in ir.column_lineage if row.source_table in junk]
    unresolved = [row for row in ir.column_lineage if row.unresolved]
    assert len(unresolved) < len(ir.column_lineage) / 2


def test_dashboard_renders_finance_summary(ir):
    assert any(
        edge.edge_type == EdgeType.DASHBOARD_RENDERS
        and edge.source_fqn == "MART.MART_FINANCE_SUMMARY"
        and edge.target_fqn == "PBI.FINANCE_DASHBOARD"
        for edge in ir.edges
    )


def test_provenance_and_live_date(ir):
    provenance = json.loads((SNAPSHOT / "PROVENANCE.json").read_text())
    assert ir.source_commit == provenance.get("commit_sha")
    assert "github" in ir.source_repo
    assert ir.generated_on == date.today()


def test_extractor_dispatches_to_git_builder(ir):
    from dsxlineage.estate.extractor import extract_estate_ir

    dispatched = extract_estate_ir(SNAPSHOT, estate_name="DATAWISE_PLATFORM")
    assert dispatched.source == "git"
    assert len(dispatched.etl_jobs) == len(ir.etl_jobs)


def test_runtime_evidence_from_run_summaries(ir):
    rows = {row.job_fqn: row for row in ir.runtime_evidence}
    assert len(ir.runtime_evidence) == 12
    writeback = rows["STAGE6B_DS_ML_SCORE_WRITEBACK"]
    assert writeback.status == "COMPLETED"
    assert writeback.failed_steps == ["DQ_CHECK", "AUDIT_END"]
    assert writeback.skipped_steps == ["DQ_CHECK", "AUDIT_END"]
    assert rows["ML_SCORE_FRAUD"].status == "COMPLETED_WITH_ERRORS"
    assert rows["ML_SCORE_FRAUD"].failed_steps == ["ML_SCORE_FRAUD"]
    assert all(row.ran_on for row in ir.runtime_evidence)


def test_reject_routes_resolved_to_tables(ir):
    assert len(ir.reject_flows) == 9
    targets = {flow.target_table for flow in ir.reject_flows}
    assert {"DW_AUDIT.ETL_REJECT_LOG", "MART.MART_ML_UNMATCHED"} <= targets
    assert all(flow.route == "REJECT" for flow in ir.reject_flows)


def test_ml_routines_and_model_assets(ir):
    pipelines = {routine.fqn: routine for routine in ir.routines if routine.kind == "ml_pipeline"}
    assert set(pipelines) == {"ML.LOAN_APPROVAL_PIPELINE", "ML.FRAUD_DETECTION_PIPELINE"}
    assert len(pipelines["ML.LOAN_APPROVAL_PIPELINE"].features) == 28
    assert len(pipelines["ML.FRAUD_DETECTION_PIPELINE"].features) == 17
    assert "loan_approval_model.pkl" in pipelines["ML.LOAN_APPROVAL_PIPELINE"].model_files
    assert pipelines["ML.FRAUD_DETECTION_PIPELINE"].reads == ["ML.TRANSACTION_FEATURES"]
    assert pipelines["ML.FRAUD_DETECTION_PIPELINE"].writes == ["ML.MODEL_SCORE_FRAUD"]
    assets = [routine for routine in ir.routines if routine.kind == "model_asset"]
    assert len(assets) == 6


def test_unknown_assets_registered(ir):
    kinds = {(asset.path, asset.kind) for asset in ir.unknown_assets}
    assert ("git_upload_etls/ssis/DataWise_SSIS.ispac", "binary") in kinds
    assert ("data/loan_db/LOAN_DB_RAW.csv", "pii_fixture") in kinds
    assert (".env.example", "credential_template") in kinds


def test_csv_fixtures_are_header_only_and_matched(ir):
    for rel, table_fqn in CSV_FIXTURES.items():
        lines = (SNAPSHOT / rel).read_text().splitlines()
        assert len(lines) == 1, f"{rel} still contains row data"
        status = ir.file_status[rel]
        assert status.startswith("headers_only")
        assert f"match {table_fqn}" in status
        assert " 0 match" not in status


def test_declared_manifest_edges_fully_covered(ir):
    from dsxlineage.estate.ingest.build import UNVERIFIED_DECLARATION_VIA

    expected = json.loads((SNAPSHOT / "EXPECTED_LINEAGE.json").read_text())
    assert expected["source_commit"] == ir.source_commit
    assert set(expected["jobs"]) == {job.fqn for job in ir.etl_jobs}
    edge_pairs = {
        (edge.source_fqn, edge.target_fqn)
        for edge in ir.edges
        if edge.edge_type in (EdgeType.ETL_READS, EdgeType.ETL_WRITES)
    }
    undefined = {ref.fqn for ref in ir.unresolved_refs}
    missing = []
    for item in expected["job_edges"]:
        pair = (item["ref"], item["job"]) if item["direction"] == "reads" else (item["job"], item["ref"])
        if pair not in edge_pairs and item["ref"] not in undefined:
            missing.append(pair)
    assert not missing, missing
    # Declarations the artifacts never mention stay in the IR but flagged.
    unverified = [
        edge for edge in ir.edges if edge.via == UNVERIFIED_DECLARATION_VIA
    ]
    assert len(unverified) == 31
    assert all(edge.confidence <= 0.5 for edge in unverified)


def test_no_duplicate_fqns_across_artifact_copies(ir):
    for collection in (ir.tables, ir.views, ir.procedures, ir.etl_jobs):
        fqns = [item.fqn for item in collection]
        assert len(fqns) == len(set(fqns))
    provenance = json.loads((SNAPSHOT / "PROVENANCE.json").read_text())
    excluded = {entry["path"] for entry in provenance["excluded"]}
    assert {"docker-deploy/ddl", "docker-deploy/datastage/files"} <= excluded
    assert not any(key.startswith("docker-deploy/ddl") for key in ir.file_status)


def test_api_create_and_graph_missing_nodes():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from dsxlineage.db.database import Base, get_db
    import dsxlineage.estate.models  # noqa: F401
    import dsxlineage.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    def override():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    from dsxlineage.main import app

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    created = client.post(
        "/api/estates",
        json={
            "name": "DATAWISE_PLATFORM",
            "display_name": "DataWise Platform",
            "source_type": "git",
            "estate_type": "datawise_platform",
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["node_count"] >= 100
    assert payload["edge_count"] >= 300
    assert payload["unresolved_count"] >= len(PLAN_DANGLING)

    graph = client.get(f"/api/estates/{payload['id']}/graph").json()
    missing = {n["id"] for n in graph["nodes"] if n.get("data", {}).get("kind") == "missing"}
    assert PLAN_DANGLING <= missing
    # Header counts must describe exactly what the graph draws (regression:
    # header showed IR totals 114/334 while the graph rendered 151 nodes).
    counts = graph["estate"]["counts"]
    assert counts["nodes"] == len(graph["nodes"])
    assert counts["edges"] == len(graph["edges"])
    assert counts["unresolved"] == len(missing) == 37
    assert counts["column_rows"] == 935
    # Unverified declaration-only edges never reach the graph: every drawn edge
    # must be evidenced by an artifact (user requirement "A -> B must be real").
    from dsxlineage.estate.ingest.build import UNVERIFIED_DECLARATION_VIA

    ir_full = build_datawise_ir(SNAPSHOT)
    hidden_pairs = {
        (e.source_fqn, e.target_fqn)
        for e in ir_full.edges
        if e.via == UNVERIFIED_DECLARATION_VIA
    }
    assert counts["declared_unverified"] == len(hidden_pairs) == 31
    drawn_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert not (hidden_pairs & drawn_pairs)
    routine_nodes = [n for n in graph["nodes"] if n.get("data", {}).get("kind") == "routine"]
    assert len(routine_nodes) == 8

    behavior = client.get(f"/api/estates/{payload['id']}/behavior/flows").json()
    assert len(behavior["rejects"]) >= 9
    assert len(behavior["failures"]) >= 2
    structure = client.get(f"/api/estates/{payload['id']}/structure/facts").json()
    assert structure["unknown_count"] >= 7
    app.dependency_overrides.pop(get_db, None)
