"""
Structure Engine — Deterministic + Adaptive

Turns heterogeneous legacy artifacts into Facts.

Facts: {type, evidence: {fqn, snippet}, confidence, source_ref}
Contract: Structure -> Facts
"""

from __future__ import annotations

from typing import Any

from dsxlineage.estate.ir import EstateIR

# Extensible unknown pattern registry
_UNKNOWN_REGISTRY: dict[str, callable] = {}


def register_unknown_parser(name: str, fn):
    _UNKNOWN_REGISTRY[name] = fn


def analyze_structure(ir: EstateIR) -> dict[str, Any]:
    """Extract Facts from EstateIR — deterministic."""
    facts = []

    # Generic parsing fact
    facts.append({
        "type": "parsing",
        "evidence": {"fqn": f"{ir.estate_name} ({ir.total_nodes} nodes)", "snippet": f"{len(ir.tables)} tables, {len(ir.views)} views, {len(ir.procedures)} procedures"},
        "confidence": 0.95,
        "source_ref": "extractor.py:generic parsing",
    })

    # Jobs/stages/links
    facts.append({
        "type": "jobs",
        "evidence": {"fqn": f"{len(ir.etl_jobs)} ETL jobs", "snippet": ", ".join(j.fqn for j in ir.etl_jobs[:3])},
        "confidence": 0.9,
        "source_ref": "extractor.py:ETL",
    })

    # Schema/metadata
    facts.append({
        "type": "schema",
        "evidence": {"fqn": f"{len(ir.systems)} systems", "snippet": ", ".join(ir.systems)},
        "confidence": 0.9,
        "source_ref": "extractor.py:schema",
    })

    # Per-node facts — enables drawer tabs to filter by fqn.
    # Generic facts above stay for estate context; these give per-node evidence.
    for t in ir.tables:
        facts.append({
            "type": "table",
            "evidence": {"fqn": t.fqn, "snippet": f"{t.system} · {len(t.columns)} columns: {', '.join(c.name for c in t.columns[:5])}"},
            "confidence": 0.95,
            "source_ref": f"DDL:{t.fqn}",
        })
    for v in ir.views:
        facts.append({
            "type": "view",
            "evidence": {"fqn": v.fqn, "snippet": (v.sql[:120] + "…") if len(v.sql) > 120 else v.sql},
            "confidence": 0.5 if v.unresolved else 0.9,
            "source_ref": f"VIEW:{v.fqn}",
        })
    for p in ir.procedures:
        facts.append({
            "type": "procedure",
            "evidence": {"fqn": p.fqn, "snippet": f"reads {', '.join(p.reads[:3]) if p.reads else '—'} → writes {', '.join(p.writes[:2]) if p.writes else '—'}"},
            "confidence": 0.9,
            "source_ref": p.fqn,
        })
    for j in ir.etl_jobs:
        facts.append({
            "type": "etl_job",
            "evidence": {"fqn": j.fqn, "snippet": f"{j.source_fqn} → {j.target_fqn} ({j.dialect})"},
            "confidence": 0.9,
            "source_ref": j.fqn,
        })
    for s in ir.schedules:
        facts.append({
            "type": "schedule",
            "evidence": {"fqn": s.fqn, "snippet": f"{s.schedule_expr} {s.frequency} · deps {', '.join(s.depends_on[:4])}"},
            "confidence": 0.85,
            "source_ref": s.fqn,
        })
    for d in ir.dashboards:
        facts.append({
            "type": "dashboard",
            "evidence": {"fqn": d.fqn, "snippet": f"renders {d.source_fqn} ({d.tool})"},
            "confidence": 0.9,
            "source_ref": d.fqn,
        })

    # Unknowns — extensible
    unknowns = []
    for name, fn in _UNKNOWN_REGISTRY.items():
        try:
            result = fn(ir)
            if result:
                unknowns.extend(result if isinstance(result, list) else [result])
        except Exception:
            pass

    # Detect unknown patterns: dynamic SQL, cursors without handling
    for p in ir.procedures:
        if p.has_dynamic:
            unknowns.append({"type": "unknown_pattern", "evidence": {"fqn": p.fqn, "snippet": "EXECUTE IMMEDIATE"}, "confidence": 0.8, "source_ref": "structure.py:dynamic"})
    for v in ir.views:
        if v.unresolved:
            unknowns.append({"type": "unknown_pattern", "evidence": {"fqn": v.fqn, "snippet": v.unresolved_reason}, "confidence": 0.7, "source_ref": "structure.py:view"})

    return {
        "facts": facts,
        "unknowns": unknowns,
        "fact_count": len(facts),
        "unknown_count": len(unknowns),
    }
