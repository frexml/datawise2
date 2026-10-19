"""Loan Onboarding — RETAIL_CUSTOMER source + ADW target + per-node Facts filter."""

from pathlib import Path

from dsxlineage.estate.api import _fqn_matches
from dsxlineage.estate.behavior import analyze_behavior
from dsxlineage.estate.extractor import extract_estate_ir
from dsxlineage.estate.semantic import analyze_semantic
from dsxlineage.estate.structure import analyze_structure

LOAN_ROOT = Path("data/synthetic_estate_loan")


def test_loan_has_retail_source_and_adw_target():
    ir = extract_estate_ir(LOAN_ROOT, estate_name="LOAN_ONBOARDING")
    fqns = {t.fqn for t in ir.tables}
    assert "RETAIL_CUSTOMER.CUSTOMER" in fqns
    assert "RETAIL_CUSTOMER.ACCOUNT" in fqns
    assert "ADW.LOAN_RISK_MART" in fqns
    assert "ADW.BORROWER_MART" in fqns
    assert "RETAIL_CUSTOMER" in ir.systems
    assert "ADW" in ir.systems
    assert "DW_LOAN" in ir.systems

    etl_by_name = {j.fqn: j for j in ir.etl_jobs}
    assert "ETL_RETAIL_CUSTOMER_TO_DW" in etl_by_name
    assert etl_by_name["ETL_RETAIL_CUSTOMER_TO_DW"].source_fqn == "RETAIL_CUSTOMER.CUSTOMER"
    assert etl_by_name["ETL_RETAIL_CUSTOMER_TO_DW"].target_fqn == "DW_LOAN.CONTACT"
    assert "ETL_DW_LOAN_TO_ADW_RISK" in etl_by_name
    assert etl_by_name["ETL_DW_LOAN_TO_ADW_RISK"].target_fqn == "ADW.LOAN_RISK_MART"
    assert "ETL_DW_LOAN_TO_ADW_BORROWER" in etl_by_name

    sched_by_name = {s.fqn: s for s in ir.schedules}
    assert "JOB_ADW_MART_LOAD" in sched_by_name
    assert set(sched_by_name["JOB_ADW_MART_LOAD"].depends_on) == {
        "ETL_DW_LOAN_TO_ADW_RISK",
        "ETL_DW_LOAN_TO_ADW_BORROWER",
    }
    assert "ETL_RETAIL_CUSTOMER_TO_DW" in sched_by_name["JOB_LOAN_ONBOARDING"].depends_on

    dash_by_name = {d.fqn: d for d in ir.dashboards}
    assert "ADW_RISK_MART_DASH" in dash_by_name


def test_structure_has_per_node_facts():
    ir = extract_estate_ir(LOAN_ROOT, estate_name="LOAN_ONBOARDING")
    res = analyze_structure(ir)
    by_fqn = {f["evidence"]["fqn"]: f for f in res["facts"]}
    assert "RETAIL_CUSTOMER.CUSTOMER" in by_fqn
    assert "ADW.LOAN_RISK_MART" in by_fqn
    assert "DW_LOAN.CONTACT" in by_fqn


def test_fqn_matches_helper():
    assert _fqn_matches("DW_LOAN.CONTACT", "DW_LOAN.CONTACT")
    assert _fqn_matches("DW_LOAN.CONTACT", "DW_LOAN.CONTACT.FIRSTNAME")
    assert _fqn_matches("VW_LOAN_RISK_SUMMARY", "VW_LOAN_RISK_SUMMARY.RISK_BAND")
    assert _fqn_matches("ETL_DW_LOAN_TO_ADW_RISK", "ETL_DW_LOAN_TO_ADW_RISK -> JOB_ADW_MART_LOAD")
    assert _fqn_matches("JOB_ADW_MART_LOAD", "ETL_DW_LOAN_TO_ADW_RISK -> JOB_ADW_MART_LOAD")
    assert not _fqn_matches("DW_LOAN.CONTACT", "ADW.LOAN_RISK_MART")
    assert _fqn_matches(None, "DW_LOAN.CONTACT")


def test_semantic_behavior_cover_loan_nodes():
    ir = extract_estate_ir(LOAN_ROOT, estate_name="LOAN_ONBOARDING")
    sem = analyze_semantic(ir)
    # RISK_BAND business rule lives on the risk summary view
    assert any(
        r["evidence"]["fqn"] == "VW_LOAN_RISK_SUMMARY.RISK_BAND" for r in sem["rules"]
    )
    beh = analyze_behavior(ir, estate_root=LOAN_ROOT)
    assert any(f["evidence"]["fqn"] == "PROC_RISK_ASSESSMENT" for f in beh["failures"])
    assert any(
        "JOB_ADW_MART_LOAD" in d["evidence"]["fqn"] for d in beh["dependencies"]
    )
