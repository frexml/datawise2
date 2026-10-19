"""
Semantic AI Engine — Meaning & Intent

Facts + Context -> Meaning
Extracts business rules, implicit contracts, similar/conflicting logic.
"""

from __future__ import annotations

from typing import Any
import difflib
import re

from dsxlineage.estate.ir import EstateIR


def analyze_semantic(ir: EstateIR) -> dict[str, Any]:
    rules = []
    implicit_contracts = []
    similar = []
    conflicting = []

    # Business rules: e.g., RISK_SCORE bands, LTV
    for cl in ir.column_lineage:
        if cl.transform_expr and "CASE WHEN" in cl.transform_expr.upper():
            # Extract CASE WHEN as business rule
            rules.append({
                "type": "business_rule",
                "evidence": {"fqn": f"{cl.target_table}.{cl.target_column}", "snippet": cl.transform_expr[:80]},
                "confidence": 0.85,
                "source_ref": f"{cl.target_table}:{cl.target_column}",
                "meaning": f"Business rule for {cl.target_column}: {cl.transform_expr[:60]}",
            })

    # Implicit contracts: KYC Approved before disbursement, etc.
    for p in ir.procedures:
        if "KYC" in p.fqn and p.reads:
            implicit_contracts.append({
                "type": "implicit_contract",
                "evidence": {"fqn": p.fqn, "snippet": f"Reads {p.reads}"},
                "confidence": 0.8,
                "source_ref": p.fqn,
                "meaning": "KYC status must be Approved before loan disbursement (implicit contract)",
            })

    # Similar/conflicting logic: hash transform_expr
    transforms = {}
    for cl in ir.column_lineage:
        if cl.transform_expr:
            key = re.sub(r"\s+", " ", cl.transform_expr.strip().upper())
            transforms.setdefault(key, []).append(cl)
    for expr, cols in transforms.items():
        if len(cols) > 1:
            # Similar logic (same transform in multiple columns)
            similar.append({
                "type": "similar_logic",
                "evidence": {"fqn": ", ".join(f"{c.target_table}.{c.target_column}" for c in cols[:3]), "snippet": expr[:60]},
                "confidence": 0.9,
                "source_ref": "semantic.py:similar",
            })
        # Conflicting: same target col, different transforms (e.g., RISK_SCORE computed differently in two views)
        # For demo, check if same target_column appears with different transforms
    # Group by target_column
    by_target = {}
    for cl in ir.column_lineage:
        by_target.setdefault(cl.target_column, []).append(cl)
    for col, lst in by_target.items():
        exprs = set(c.transform_expr for c in lst if c.transform_expr)
        if len(exprs) > 1:
            conflicting.append({
                "type": "conflicting_logic",
                "evidence": {"fqn": col, "snippet": f"{len(exprs)} different transforms: {', '.join(list(exprs)[:2])}"},
                "confidence": 0.75,
                "source_ref": "semantic.py:conflicting",
            })

    return {
        "rules": rules,
        "implicit_contracts": implicit_contracts,
        "similar": similar,
        "conflicting": conflicting,
        "fact_count": len(rules) + len(implicit_contracts),
    }
