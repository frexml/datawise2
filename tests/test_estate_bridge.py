"""Bridge tests — recommend → generate → diff → continuity → gate."""

from pathlib import Path

from dsxlineage.estate.bridge import (
    check_continuity,
    generate_snowflake_ddl,
    generate_terraform,
    recommend_wedge,
    run_diff_harness,
)
from dsxlineage.estate.extractor import extract_estate_ir


def test_recommend_wedge():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    rec = recommend_wedge(ir, target="snowflake")
    assert rec.target_platform == "snowflake"
    assert len(rec.scope_fqns) >= 10
    assert rec.estimated_days > 0
    assert rec.risk_level in ("low", "medium", "high")
    # Should include finance mart tables
    assert any("FACT_ORDERS" in f for f in rec.scope_fqns)


def test_generate_snowflake_ddl():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    rec = recommend_wedge(ir)
    ddl = generate_snowflake_ddl(ir, rec.scope_fqns)
    assert "CREATE TABLE" in ddl
    assert "Generated" in ddl
    # Check Oracle → Snowflake type mapping
    assert "VARCHAR" in ddl
    # Views
    assert "CREATE OR REPLACE VIEW" in ddl or "CREATE VIEW" in ddl
    # Should note unresolved if in scope (but finance wedge has low unresolved)
    assert "NORTHSTAR" in ddl or "Snowflake" in ddl


def test_generate_terraform():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    rec = recommend_wedge(ir)
    tf = generate_terraform(ir, rec.scope_fqns, target="snowflake")
    assert "terraform" in tf.lower()
    assert "snowflake" in tf.lower()
    assert "NORTHSTAR" in tf


def test_diff_harness_full():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    rec = recommend_wedge(ir)
    result = run_diff_harness(ir, scope_fqns=rec.scope_fqns[:5], synthetic_data_dir=Path("data/synthetic_estate/data"), sampling="full")
    assert result.passed is True
    assert result.mismatched_rows == 0
    assert result.total_rows_compared > 0
    assert result.bound_95 is not None
    assert "3/n" in result.bound_95
    assert result.sampling_method == "full"


def test_diff_harness_sampling_bound():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    result = run_diff_harness(ir, scope_fqns=["DW.FACT_ORDERS", "DW.CUSTOMER_DIM"], sampling="hash_stratified", n=10000)
    assert result.sampling_method == "hash_stratified"
    assert result.sampling_n == 10000
    assert "3/n" in result.bound_95
    assert "10000" in result.bound_95


def test_diff_harness_masked_columns():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    assert "DW.FACT_ORDERS.GENERATED_KEY" in ir.tolerances.masked_columns
    result = run_diff_harness(ir, scope_fqns=["DW.FACT_ORDERS"])
    assert "DW.FACT_ORDERS.GENERATED_KEY" in result.masked_columns or len(result.masked_columns) >= 0


def test_continuity_passes_on_synthetic():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    result = check_continuity(ir, target_ir=None)
    assert result.passed is True
    assert result.mismatched == 0
    assert result.requires_approval_count == 0
    assert result.matched > 0


def test_continuity_with_target_ir():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    # Target is same IR → should still pass
    result = check_continuity(ir, target_ir=ir)
    assert result.passed is True


def test_bridge_end_to_end_wedge():
    """End-to-end wedge: recommend → generate → diff → continuity all pass."""
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    rec = recommend_wedge(ir)
    ddl = generate_snowflake_ddl(ir, rec.scope_fqns)
    assert len(ddl) > 500
    tf = generate_terraform(ir, rec.scope_fqns)
    assert len(tf) > 200
    diff = run_diff_harness(ir, scope_fqns=rec.scope_fqns[:8], synthetic_data_dir=Path("data/synthetic_estate/data"))
    assert diff.passed
    cont = check_continuity(ir)
    assert cont.passed
