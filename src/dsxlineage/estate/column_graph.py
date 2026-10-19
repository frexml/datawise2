"""
Estate-wide column lineage graph — for all 87 columns.

Builds a graph where nodes are fully-qualified columns (table.column) and edges are
column_lineage entries. Enables BFS to find full estate journey for any column:
e.g., SAP_ECC.SAP_ORDERS.ORDER_ID -> DW.FACT_ORDERS.ORDER_ID (1) -> VW_ORDER_SUMMARY.ORDER_ID (2) -> ... -> RISK_REPORT.ORDER_ID (3).

Used to populate hops and full_path correctly (not just per-view 1-hop).
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from dsxlineage.estate.ir import EstateIR, ColumnLineage


def build_column_graph(ir: EstateIR) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, ColumnLineage]]:
    """Build adjacency for column graph.

    Returns (outgoing, incoming, edge_by_target_col_key) where keys are fqn.col
    """
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    # Map from target fqn.col to edge for quick lookup
    edge_map: dict[str, ColumnLineage] = {}
    for cl in ir.column_lineage:
        if cl.unresolved:
            continue
        src = f"{cl.source_table}.{cl.source_column}"
        tgt = f"{cl.target_table}.{cl.target_column}"
        outgoing[src].append(tgt)
        incoming[tgt].append(src)
        edge_map[tgt] = cl
    return outgoing, incoming, edge_map


def get_column_journey(ir: EstateIR, fqn_col: str) -> dict[str, Any]:
    """Get full estate journey for a column fqn.col.

    Returns {fqn, upstream: [{fqn, hops, transform}], downstream: [...], full_path, max_hops}
    """
    outgoing, incoming, edge_map = build_column_graph(ir)

    # BFS upstream (follow incoming)
    upstream = []
    visited = set()
    queue = deque([(fqn_col, 0)])
    visited.add(fqn_col)
    while queue:
        cur, d = queue.popleft()
        for src in incoming.get(cur, []):
            if src not in visited:
                visited.add(src)
                # Find edge for this hop
                edge = next((c for c in ir.column_lineage if f"{c.target_table}.{c.target_column}" == cur and f"{c.source_table}.{c.source_column}" == src), None)
                upstream.append({
                    "fqn": src,
                    "hops": d + 1,
                    "transform": edge.transform_expr if edge else None,
                    "confidence": edge.confidence if edge else 0.9,
                })
                queue.append((src, d + 1))

    # BFS downstream
    downstream = []
    visited = set()
    queue = deque([(fqn_col, 0)])
    visited.add(fqn_col)
    while queue:
        cur, d = queue.popleft()
        for tgt in outgoing.get(cur, []):
            if tgt not in visited:
                visited.add(tgt)
                edge = next((c for c in ir.column_lineage if f"{c.target_table}.{c.target_column}" == tgt and f"{c.source_table}.{c.source_column}" == cur), None)
                # Try reverse lookup
                if not edge:
                    edge = edge_map.get(tgt)
                downstream.append({
                    "fqn": tgt,
                    "hops": d + 1,
                    "transform": edge.transform_expr if edge else None,
                    "confidence": edge.confidence if edge else 0.9,
                })
                queue.append((tgt, d + 1))

    # Sort by hops
    upstream.sort(key=lambda x: x["hops"])
    downstream.sort(key=lambda x: x["hops"])

    # Full path: upstream reversed + self + downstream sorted
    all_nodes = list(reversed([u["fqn"] for u in upstream])) + [fqn_col] + [d["fqn"] for d in downstream]
    full_path = " -> ".join(all_nodes) if len(all_nodes) > 1 else fqn_col
    max_hops = max(len(upstream), len(downstream), 0)
    # If no upstream/downstream, hops is 0 or 1
    if not upstream and not downstream:
        max_hops = 0

    return {
        "fqn": fqn_col,
        "upstream": upstream,
        "downstream": downstream,
        "full_path": full_path,
        "hops": max_hops,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
    }


def enrich_column_lineage_with_estate_hops(ir: EstateIR) -> None:
    """Mutate ir.column_lineage in-place to set hops and full_path based on estate-wide graph."""
    outgoing, incoming, _ = build_column_graph(ir)
    # For each column lineage, compute hops as distance from ultimate source
    # Ultimate source has no incoming, so hops = BFS distance from root
    # We can compute for each lineage entry: hops = 1 + max hops of its source
    # Instead, use get_column_journey for each target
    for cl in ir.column_lineage:
        if cl.unresolved:
            cl.hops = 1
            cl.full_path = f"{cl.target_table}.{cl.target_column} (unresolved)"
            continue
        fqn = f"{cl.target_table}.{cl.target_column}"
        journey = get_column_journey(ir, fqn)
        # Hops is max of upstream length + 1, or downstream? For target, hops = upstream hops + 1
        # journey upstream is from this column backwards
        cl.hops = max(1, journey["hops"] if journey["hops"] > 0 else 1)
        # If journey has upstream, full_path is the full chain; else just this edge
        if journey["full_path"] and "->" in journey["full_path"]:
            cl.full_path = journey["full_path"]
        else:
            cl.full_path = f"{cl.source_table}.{cl.source_column} -> {fqn}"
        # Also set schemas
        cl.target_schema = cl.target_table.split(".")[0] if "." in cl.target_table else ""
        cl.source_schema = cl.source_table.split(".")[0] if "." in cl.source_table else ""
