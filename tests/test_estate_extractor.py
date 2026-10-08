"""Phase 1a–1c gate: EstateIR extraction recall ≥0.95 on synthetic estate."""

from pathlib import Path
import json

from dsxlineage.estate.extractor import extract_estate_ir


def test_extractor_counts():
    ir = extract_estate_ir(Path("data/synthetic_estate"), estate_name="NORTHSTAR")
    assert len(ir.tables) == 50, f"expected 50 tables, got {len(ir.tables)}"
    assert len(ir.views) == 24, f"expected 24 views, got {len(ir.views)}"
    assert len(ir.procedures) == 18
    assert len(ir.etl_jobs) == 14
    assert len(ir.schedules) == 12
    assert len(ir.dashboards) == 8
    assert ir.total_edges >= 140
    assert ir.unresolved_count == 5  # PROC_DYNAMIC_RISK + VW_DYNAMIC_RISK + their UNRESOLVED edges


def test_view_sources_via_sqlglot():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    vw = next(v for v in ir.views if v.fqn == "VW_RISK_EXPOSURE")
    # Should have parsed sources via sqlglot
    assert "DW.RISK_EXPOSURE" in vw.source_fqns
    assert "DW.KYC_STATUS" in vw.source_fqns
    assert not vw.unresolved

    vw_dyn = next(v for v in ir.views if v.fqn == "VW_DYNAMIC_RISK")
    assert vw_dyn.unresolved is True
    assert vw_dyn.unresolved_reason is not None


def test_lineage_recall_suffix_aware():
    """Suffix-aware recall ≥0.95 — the Gate A criterion (Plan v2)."""
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    exp = json.loads(Path("data/synthetic_estate/EXPECTED_LINEAGE.json").read_text())

    def matches(exp_src: str, exp_tgt: str) -> bool:
        for e in ir.edges:
            src_match = (
                exp_src.upper() == e.source_fqn.upper()
                or e.source_fqn.upper().endswith("." + exp_src.upper())
                or exp_src.upper().endswith("." + e.source_fqn.upper())
            )
            tgt_match = (
                exp_tgt.upper() == e.target_fqn.upper()
                or e.target_fqn.upper().endswith("." + exp_tgt.upper())
                or exp_tgt.upper().endswith("." + e.target_fqn.upper())
            )
            if src_match and tgt_match:
                return True
        return False

    exp_filtered = [(e["source"], e["target"]) for e in exp if e["type"] != "UNRESOLVED"]
    matched = sum(1 for s, t in exp_filtered if matches(s, t))
    recall = matched / len(exp_filtered)
    assert recall >= 0.95, f"Recall {recall:.3f} < 0.95 ({matched}/{len(exp_filtered)})"


def test_procedure_unresolved_flag():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    proc_dyn = next(p for p in ir.procedures if p.fqn == "PROC_DYNAMIC_RISK")
    assert proc_dyn.has_dynamic is True
    # Should have an UNRESOLVED edge
    assert any(e.source_fqn == "PROC_DYNAMIC_RISK" and e.edge_type.value == "UNRESOLVED" for e in ir.edges)

    proc_ok = next(p for p in ir.procedures if p.fqn == "PROC_CALC_RISK")
    assert proc_ok.has_dynamic is False


def test_column_identities():
    ir = extract_estate_ir(Path("data/synthetic_estate"))
    # Should have grouped CUSTOMER_ID aliases
    cust_ids = [ci for ci in ir.column_identities if ci.canonical_id == "customer_id"]
    assert len(cust_ids) >= 2
