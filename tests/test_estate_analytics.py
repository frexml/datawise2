"""Analytics engine tests — orphans, hot tables, circular, complexity."""

from pathlib import Path

from dsxlineage.estate.extractor import extract_estate_ir
from dsxlineage.estate.analytics import compute_analytics


def test_analytics_orphans():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    ana = compute_analytics(ir)
    # Known orphans: TMP_OLD_* tables have no edges
    assert "DW.TMP_OLD_CUSTOMER" in ana["orphan_tables"]
    assert "DW.TMP_OLD_ORDERS" in ana["orphan_tables"]
    # Also check count
    assert ana["orphan_count"] >= 3


def test_analytics_hot_tables():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    ana = compute_analytics(ir)
    # DW.CUSTOMER_DIM and DW.FACT_ORDERS are hot (high fan-in)
    hot_fqns = [h["fqn"] for h in ana["hot_tables"]]
    assert "DW.CUSTOMER_DIM" in hot_fqns or "DW.FACT_ORDERS" in hot_fqns
    assert len(ana["hot_tables"]) > 0


def test_analytics_circular():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    ana = compute_analytics(ir)
    assert ana["circular_count"] >= 1
    # Should contain the circular pair
    found = any("JOB_CIRCULAR_A" in cycle and "JOB_CIRCULAR_B" in cycle for cycle in ana["circular_dependencies"])
    assert found, f"Expected circular JOB_CIRCULAR_A↔B, got {ana['circular_dependencies']}"


def test_analytics_complexity():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    ana = compute_analytics(ir)
    top = ana["complexity_ranking"][0]
    assert top["complexity"] == "high"
    # PROC_CALC_RISK should be near top
    assert any(c["fqn"] == "PROC_CALC_RISK" for c in ana["complexity_ranking"][:5])


def test_analytics_dashboard_lineage():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    ana = compute_analytics(ir)
    risk_dash = next((d for d in ana["dashboard_lineage"] if d["dashboard"] == "RISK_REPORT"), None)
    assert risk_dash is not None
    assert "VW_RISK_DASHBOARD" in risk_dash["chain"] or risk_dash["source_view"] == "VW_RISK_DASHBOARD"
    assert len(risk_dash["upstream_tables"]) > 0


def test_analytics_coverage():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    ana = compute_analytics(ir)
    assert ana["coverage_pct"] > 50
    assert ana["total_edges"] == len(ir.edges)
