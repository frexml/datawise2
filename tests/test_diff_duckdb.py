"""DuckDB diff harness — real value compare with tolerances."""

import csv
import tempfile
from pathlib import Path

from dsxlineage.estate.bridge import _duckdb_diff_table, run_diff_harness
from dsxlineage.estate.extractor import extract_estate_ir
from dsxlineage.estate.ir import Tolerances


def test_duckdb_same_file_zero_mismatch():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    tol = Tolerances(numeric_epsilon=0.01, collation="case_insensitive", masked_columns=[])
    # Use real CSV
    csv_path = Path("data/synthetic_estate/data/DW_CUSTOMER_DIM.csv")
    if not csv_path.exists():
        csv_path = Path("data/synthetic_estate/data/SAP_ECC_SAP_CUSTOMER.csv")
    src_rows, tgt_rows, mismatched = _duckdb_diff_table(csv_path, csv_path, "DW.CUSTOMER_DIM", tol)
    assert mismatched == 0
    assert src_rows == tgt_rows
    assert src_rows > 0


def test_duckdb_numeric_epsilon():
    # Create two CSVs with numeric diff within and outside epsilon
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "src.csv"
        tgt_within = tmp / "tgt_within.csv"
        tgt_outside = tmp / "tgt_outside.csv"
        header = ["ID", "AMOUNT"]
        rows_src = [{"ID": "1", "AMOUNT": "100.00"}, {"ID": "2", "AMOUNT": "200.00"}]
        rows_within = [{"ID": "1", "AMOUNT": "100.005"}, {"ID": "2", "AMOUNT": "200.009"}]  # within 0.01
        rows_outside = [{"ID": "1", "AMOUNT": "100.05"}, {"ID": "2", "AMOUNT": "200.00"}]  # outside 0.01
        for path, rows in [(src, rows_src), (tgt_within, rows_within), (tgt_outside, rows_outside)]:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=header)
                w.writeheader()
                w.writerows(rows)
        tol = Tolerances(numeric_epsilon=0.01, collation="case_insensitive", masked_columns=[])
        _, _, mism_within = _duckdb_diff_table(src, tgt_within, "DW.FACT_ORDERS", tol)
        _, _, mism_outside = _duckdb_diff_table(src, tgt_outside, "DW.FACT_ORDERS", tol)
        assert mism_within == 0, f"within epsilon should be 0, got {mism_within}"
        assert mism_outside > 0, f"outside epsilon should be >0, got {mism_outside}"


def test_duckdb_masked_columns():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "src.csv"
        tgt = tmp / "tgt.csv"
        header = ["ID", "AMOUNT", "GENERATED_KEY"]
        rows_src = [{"ID": "1", "AMOUNT": "100.00", "GENERATED_KEY": "abc"}]
        rows_tgt = [{"ID": "1", "AMOUNT": "100.00", "GENERATED_KEY": "xyz"}]  # different masked col
        for path, rows in [(src, rows_src), (tgt, rows_tgt)]:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=header)
                w.writeheader()
                w.writerows(rows)
        tol_no_mask = Tolerances(numeric_epsilon=0.01, masked_columns=[])
        _, _, mism_no_mask = _duckdb_diff_table(src, tgt, "DW.FACT_ORDERS", tol_no_mask)
        assert mism_no_mask > 0
        tol_mask = Tolerances(numeric_epsilon=0.01, masked_columns=["DW.FACT_ORDERS.GENERATED_KEY"])
        _, _, mism_mask = _duckdb_diff_table(src, tgt, "DW.FACT_ORDERS", tol_mask)
        assert mism_mask == 0


def test_diff_harness_with_synthetic_dir_uses_duckdb_fastpath():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    # Scope with tables that have CSVs
    result = run_diff_harness(ir, scope_fqns=["DW.CUSTOMER_DIM", "DW.FACT_ORDERS"], synthetic_data_dir=Path("data/synthetic_estate/data"))
    assert result.passed is True
    assert result.total_rows_compared > 0
    assert result.bound_95 is not None
    assert "3/n" in result.bound_95
