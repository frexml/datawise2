"""Extractor complex-SQL robustness — gate 1a realism.

Tests sqlglot handling of Oracle-isms that break the naive regex fallback:
  NVL, DECODE, parallel hint, quoted identifiers, WITH, PIVOT, DBLINK, etc.
"""

from pathlib import Path

from dsxlineage.estate.extractor import _parse_view_sources


def test_parse_nvl_decode():
    sql = "SELECT NVL(a.amt,0) * DECODE(b.tier, 'A', 1, 0) FROM DW.FACT_ORDERS a JOIN DW.CUSTOMER_DIM b ON a.CUSTOMER_ID = b.CUSTOMER_ID"
    fqns, unresolved, reason = _parse_view_sources(sql)
    assert "DW.FACT_ORDERS" in fqns
    assert "DW.CUSTOMER_DIM" in fqns
    assert unresolved is False


def test_parse_parallel_hint():
    sql = "SELECT /*+ PARALLEL(4) */ CUSTOMER_ID, AMOUNT FROM DW.FACT_ORDERS"
    fqns, unresolved, _ = _parse_view_sources(sql)
    assert "DW.FACT_ORDERS" in fqns


def test_parse_quoted_identifiers():
    sql = 'SELECT "My View".CUSTOMER_ID FROM DW.CUSTOMER_DIM'
    fqns, unresolved, _ = _parse_view_sources(sql)
    # sqlglot should handle quoted, but at least not crash and return something
    assert isinstance(fqns, list)


def test_parse_with_clause():
    sql = "WITH cte AS (SELECT CUSTOMER_ID FROM DW.CUSTOMER_DIM) SELECT * FROM cte JOIN DW.FACT_ORDERS o ON cte.CUSTOMER_ID = o.CUSTOMER_ID"
    fqns, unresolved, _ = _parse_view_sources(sql)
    assert "DW.CUSTOMER_DIM" in fqns
    assert "DW.FACT_ORDERS" in fqns


def test_parse_dblink():
    sql = "SELECT * FROM DW.FACT_ORDERS@REMOTE_DB"
    fqns, unresolved, _ = _parse_view_sources(sql)
    # Should not crash; FQN may include DBLINK suffix
    assert len(fqns) >= 1


def test_parse_dynamic_flag():
    sql = "SELECT CUSTOMER_ID, EXPOSURE_AMT FROM DW.RISK_EXPOSURE WHERE RUN_DATE = TRUNC(SYSDATE) -- dynamic: EXECUTE IMMEDIATE"
    fqns, unresolved, reason = _parse_view_sources(sql)
    assert unresolved is True
    assert reason is not None and "dynamic" in reason.lower()


def test_full_ir_with_complex_view():
    """Create a synthetic view file with complex SQL and ensure extractor handles it."""
    import tempfile
    from dsxlineage.estate.extractor import extract_estate_ir

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Minimal estate with one complex view
        (tmp_path / "DDL").mkdir()
        (tmp_path / "DDL" / "DW_TEST_TABLE.sql").write_text("CREATE TABLE DW.TEST_TABLE (ID VARCHAR2(100), AMOUNT NUMBER(18,2));")
        (tmp_path / "DDL" / "VIEW_VW_COMPLEX.sql").write_text(
            "CREATE OR REPLACE VIEW VW_COMPLEX AS SELECT /*+ PARALLEL */ NVL(a.AMOUNT,0) FROM DW.TEST_TABLE a JOIN DW.FACT_ORDERS b ON a.ID = b.CUSTOMER_ID;"
        )
        (tmp_path / "etl").mkdir()
        (tmp_path / "schedules").mkdir()
        (tmp_path / "bi").mkdir()
        ir = extract_estate_ir(tmp_path, estate_name="TEST_COMPLEX")
        vw = next((v for v in ir.views if v.fqn == "VW_COMPLEX"), None)
        assert vw is not None
        assert not vw.unresolved
        assert "DW.TEST_TABLE" in vw.source_fqns or "DW.FACT_ORDERS" in vw.source_fqns
