"""Column-level lineage correctness — regression for the JOIN-alias bug.

VW_BRANCH_PERFORMANCE joins two tables with short aliases:
    SELECT b.BRANCH_ID, b.BRANCH_NAME, p.PERF_DATE, p.REVENUE, p.MARGIN
    FROM DW.BRANCH_DIM b JOIN DW.BRANCH_PERF p ON b.BRANCH_ID = p.BRANCH_ID

A hand-rolled alias matcher previously misattributed every column from the
2nd+ joined table back to the 1st (PERF_DATE/REVENUE/MARGIN all showed
DW.BRANCH_DIM as source instead of DW.BRANCH_PERF) because it only handled
fully-qualified schema.table.column references, not aliases. This pins down
correct per-column source attribution so that regresses loudly.
"""
from pathlib import Path

from dsxlineage.estate.extractor import extract_estate_ir


def _lineage_for(ir, target_table: str) -> dict:
    return {cl.target_column: cl for cl in ir.column_lineage if cl.target_table == target_table}


def test_join_view_attributes_columns_to_correct_source_table():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    cols = _lineage_for(ir, "VW_BRANCH_PERFORMANCE")

    assert cols["BRANCH_ID"].source_table == "DW.BRANCH_DIM"
    assert cols["BRANCH_NAME"].source_table == "DW.BRANCH_DIM"

    # The bug: these three used to show DW.BRANCH_DIM (the first join table)
    # instead of DW.BRANCH_PERF (where they actually live).
    assert cols["PERF_DATE"].source_table == "DW.BRANCH_PERF"
    assert cols["REVENUE"].source_table == "DW.BRANCH_PERF"
    assert cols["MARGIN"].source_table == "DW.BRANCH_PERF"

    for col in ("BRANCH_ID", "BRANCH_NAME", "PERF_DATE", "REVENUE", "MARGIN"):
        assert not cols[col].unresolved
        assert cols[col].transform_expr is None
        assert cols[col].confidence >= 0.9


def test_another_join_view_resolves_both_sides():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    cols = _lineage_for(ir, "VW_LOAN_PIPELINE")
    # FROM DW.LOAN_FACT l JOIN DW.CUSTOMER_DIM c ON l.CUSTOMER_ID = c.CUSTOMER_ID
    assert cols["APP_ID"].source_table == "DW.LOAN_FACT"
    assert cols["AMOUNT_REQUESTED"].source_table == "DW.LOAN_FACT"
    assert cols["CUSTOMER_NAME"].source_table == "DW.CUSTOMER_DIM"


def test_transform_detection_and_telecom_estate():
    ir = extract_estate_ir(Path("data/synthetic_estate_telco"))
    cols = _lineage_for(ir, "VW_CDR_AGG")
    assert cols["SUBSCRIBER_ID"].transform_expr is None
    total_bytes = cols["TOTAL_BYTES"]
    assert total_bytes.transform_expr is not None
    assert "SUM" in total_bytes.transform_expr.upper()
    assert total_bytes.source_table == "DW_TELCO.CDR_FACT"
    assert total_bytes.source_column == "BYTES"


def test_unresolved_columns_are_flagged_not_dropped():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    cols = _lineage_for(ir, "VW_REVENUE_DAILY")
    # COUNT(*) has no traceable single source column.
    assert cols["ORDER_COUNT"].unresolved is True
    assert cols["ORDER_COUNT"].unresolved_reason
    assert cols["ORDER_COUNT"].confidence == 0.0
    # DAILY_REVENUE (SUM(AMOUNT)) resolves fine.
    assert cols["DAILY_REVENUE"].unresolved is False
    assert cols["DAILY_REVENUE"].source_table == "DW.FACT_ORDERS"
