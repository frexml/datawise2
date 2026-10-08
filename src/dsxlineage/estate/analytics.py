"""
Estate Analytics Engine - computed from EstateIR (Postgres) with optional
Neo4j Cypher for graph-native metrics.

Metrics per Plan v2 §3.2:
  orphan / dead code, hot tables, usage frequency, blast radius helpers,
  complexity ranking, dashboard lineage, coverage, circular detection.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from dsxlineage.estate.ir import EstateIR


def compute_analytics(ir: EstateIR) -> dict[str, Any]:
    """Compute all estate analytics from EstateIR.

    Returns a dict with keys:
      orphan_tables, orphan_views, orphan_procedures,
      hot_tables, usage_frequency,
      complexity_ranking, dashboard_lineage,
      circular_dependencies, coverage, unresolved
    """
    # Build adjacency from edges
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    edge_count_by_node: Counter = Counter()
    for e in ir.edges:
        if e.edge_type.value == "UNRESOLVED":
            continue
        outgoing[e.source_fqn].append(e.target_fqn)
        incoming[e.target_fqn].append(e.source_fqn)
        edge_count_by_node[e.source_fqn] += 1
        edge_count_by_node[e.target_fqn] += 1

    all_table_fqns = {t.fqn for t in ir.tables}
    all_view_fqns = {v.fqn for v in ir.views}
    all_proc_fqns = {p.fqn for p in ir.procedures}
    all_dash_fqns = {d.fqn for d in ir.dashboards}

    # ---- Orphan detection: nodes with zero incoming AND zero outgoing (or only self) ----
    def is_orphan(fqn: str) -> bool:
        return fqn not in outgoing and fqn not in incoming

    orphan_tables = sorted([fqn for fqn in all_table_fqns if is_orphan(fqn)])
    orphan_views = sorted([fqn for fqn in all_view_fqns if is_orphan(fqn)])
    orphan_procs = sorted([fqn for fqn in all_proc_fqns if is_orphan(fqn)])

    # Known orphans from synthetic spec: TMP_OLD_* etc - also flag tables that are
    # only written to but never read downstream (dead writes)
    # For POC, also consider tables with no incoming (no ETL/procedure writes them)
    # but are not source-system tables - those are data quality orphans.
    # Simpler: use is_orphan above; synthetic TMP_* have zero edges so they appear.

    # ---- Hot tables: most depended-on (highest incoming fan-in) ----
    incoming_counts = Counter({fqn: len(incoming.get(fqn, [])) for fqn in all_table_fqns | all_view_fqns})
    # Also count dashboard renders as fan-in
    hot_tables = sorted(incoming_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    hot_tables = [{"fqn": fqn, "fan_in": cnt} for fqn, cnt in hot_tables if cnt > 0]

    # ---- Usage frequency (degree centrality) ----
    usage = sorted(edge_count_by_node.items(), key=lambda x: x[1], reverse=True)[:15]
    usage_frequency = [{"fqn": fqn, "degree": cnt} for fqn, cnt in usage]

    # ---- Complexity ranking for procedures ----
    complexity_order = {"high": 3, "medium": 2, "low": 1}
    procs_ranked = sorted(ir.procedures, key=lambda p: (complexity_order.get(p.complexity, 0), len(p.reads) + len(p.writes)), reverse=True)
    complexity_ranking = [
        {
            "fqn": p.fqn,
            "complexity": p.complexity,
            "language": p.language,
            "has_dynamic": p.has_dynamic,
            "has_cursor": p.has_cursor,
            "reads": p.reads,
            "writes": p.writes,
            "score": complexity_order.get(p.complexity, 0) * 10 + len(p.reads) + len(p.writes),
        }
        for p in procs_ranked
    ]

    # ---- Dashboard lineage: dashboard -> view -> table path ----
    dashboard_lineage = []
    for d in ir.dashboards:
        # Resolve view chain: dashboard.source_fqn may be a view; follow to tables
        chain = [d.source_fqn]
        visited = set()
        queue = [d.source_fqn]
        tables_reached: set[str] = set()
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            # Find view that has this fqn
            view = next((v for v in ir.views if v.fqn == cur), None)
            if view:
                for src in view.source_fqns:
                    if src.startswith("DW.") or src.startswith("SAP") or src.startswith("SALESFORCE") or src.startswith("FLAT"):
                        tables_reached.add(src)
                    else:
                        # It may be another view
                        if src not in visited:
                            queue.append(src)
                            chain.append(src)
                    chain.append(src)
            else:
                # cur is a table or unknown
                if cur.startswith("DW.") or cur.startswith("SAP"):
                    tables_reached.add(cur)
        dashboard_lineage.append({
            "dashboard": d.fqn,
            "tool": d.tool,
            "source_view": d.source_fqn,
            "upstream_tables": sorted(tables_reached),
            "chain": chain,
        })

    # ---- Circular dependencies (from schedules + view cycles) ----
    # Simple DFS cycle detection on schedule graph + view deps
    def find_cycles(nodes: set[str], adj: dict[str, list[str]]) -> list[list[str]]:
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def dfs(node: str):
            visited.add(node)
            stack.append(node)
            on_stack.add(node)
            for nb in adj.get(node, []):
                if nb not in visited:
                    dfs(nb)
                elif nb in on_stack:
                    # Found cycle
                    idx = stack.index(nb)
                    cycles.append(stack[idx:] + [nb])
            stack.pop()
            on_stack.remove(node)

        for n in nodes:
            if n not in visited:
                dfs(n)
        return cycles

    schedule_nodes = {s.fqn for s in ir.schedules}
    circular = find_cycles(schedule_nodes, outgoing)
    # Also view cycles (VW_RISK_DASHBOARD -> VW_RISK_EXPOSURE -> ... none circular in synthetic, but check)
    view_nodes = {v.fqn for v in ir.views}
    view_cycles = find_cycles(view_nodes, outgoing)
    circular.extend(view_cycles)

    # ---- Coverage: share of estate nodes with lineage edges ----
    # 126 is the full estate (tables+views+procedures+ETL+schedules+dashboards), same as graph header.
    # Previously this was 92 (core only), which mismatched the graph's 126.
    total_nodes = ir.total_nodes  # 126 for NorthStar synthetic
    all_estate_fqns = (
        all_table_fqns | all_view_fqns | all_proc_fqns
        | {j.fqn for j in ir.etl_jobs}
        | {s.fqn for s in ir.schedules}
        | {d.fqn for d in ir.dashboards}
    )
    nodes_with_edges = len(
        ({e.source_fqn for e in ir.edges} | {e.target_fqn for e in ir.edges}) & all_estate_fqns
    )
    coverage_pct = round((nodes_with_edges / total_nodes * 100) if total_nodes else 0, 1)

    # ---- Unresolved (dynamic SQL etc) ----
    unresolved = [
        {"source": e.source_fqn, "target": e.target_fqn, "reason": e.unresolved_reason or "unresolved", "type": e.edge_type.value}
        for e in ir.edges if e.unresolved
    ]

    # ---- New: Estate Health & Migration Readiness (0-100) ----
    # Health = 100 − (unresolved*8 + circular*15 + orphan%*0.5 + high%*0.7) clamped 0-100
    orphan_pct = (len(orphan_tables) + len(orphan_views) + len(orphan_procs)) / total_nodes * 100 if total_nodes else 0
    high_count = sum(1 for p in ir.procedures if p.complexity == "high")
    high_pct = high_count / len(ir.procedures) * 100 if ir.procedures else 0
    health_raw = 100 - (len(unresolved) * 8 + len(circular) * 15 + orphan_pct * 0.5 + high_pct * 0.7)
    estate_health = max(0, min(100, round(health_raw)))
    health_label = "Healthy" if estate_health >= 80 else "Moderate Risk" if estate_health >= 60 else "At Risk"
    health_color = "emerald" if estate_health >= 80 else "amber" if estate_health >= 60 else "red"

    # Readiness = (1 − unresolved/total) × coverage × (1 − circular/total) -> 0-100
    readiness = round((1 - len(unresolved) / total_nodes if total_nodes else 1) * (coverage_pct / 100) * (1 - len(circular) / max(total_nodes, 1)) * 100, 1)

    # Orphan cost - synthetic: $900/yr per orphan table (storage + confusion/migration waste)
    orphan_cost_usd = (len(orphan_tables) * 900) + (len(orphan_views) * 300) + (len(orphan_procs) * 500)
    orphan_cost_label = f"${orphan_cost_usd:,}/yr"

    # ---- Lineage depth histogram (view depth = longest path from source tables) ----
    # BFS depth for each view
    depth_histogram: dict[int, int] = {}
    view_depths: dict[str, int] = {}
    for v in ir.views:
        # BFS from sources to this view
        queue = [(v.fqn, 0)]
        visited_depth: dict[str, int] = {v.fqn: 0}
        max_d = 0
        q = [v.fqn]
        depth_map = {v.fqn: 0}
        # Walk backwards via incoming
        stack = [(v.fqn, 0)]
        seen = set()
        max_depth = 0
        # Simple: depth = number of VIEW_DEPENDS hops from any table
        # Use incoming to walk to tables
        def view_depth(fqn: str, seen: set[str]) -> int:
            if fqn in seen:
                return 0
            seen.add(fqn)
            view = next((x for x in ir.views if x.fqn == fqn), None)
            if not view or not view.source_fqns:
                return 1 if fqn.startswith("DW.") or fqn.startswith("SAP") else 0
            return 1 + max((view_depth(src, set(seen)) for src in view.source_fqns), default=0)
        # For views, compute depth via source_fqns
        if v.source_fqns:
            d = 1
            # Count how many view layers deep
            # 1 = direct table, 2 = view->view, etc.
            view_src_count = sum(1 for s in v.source_fqns if any(x.fqn == s for x in ir.views))
            table_src_count = len(v.source_fqns) - view_src_count
            # Depth heuristic: views that depend on views are deeper
            max_depth = 1 + (2 if view_src_count > 0 else 0) + (1 if any(any(y.fqn == s for y in ir.views) and any(z.fqn in [a for a in y.source_fqns] for z in ir.views) for s in v.source_fqns for y in ir.views if y.fqn == s) else 0)
            # Simpler bucket: 1=table-only, 2=view->table, 3=view->view->table
            if view_src_count > 0:
                max_depth = 2
                # check if any source view itself has view sources
                for src in v.source_fqns:
                    src_view = next((x for x in ir.views if x.fqn == src), None)
                    if src_view and any(any(y.fqn == s for y in ir.views) for s in src_view.source_fqns):
                        max_depth = 3
                        break
            else:
                max_depth = 1
        else:
            max_depth = 1
        view_depths[v.fqn] = max_depth
        depth_histogram[max_depth] = depth_histogram.get(max_depth, 0) + 1
    # Ensure buckets 1-3 present
    for k in [1, 2, 3]:
        depth_histogram.setdefault(k, 0)
    lineage_depth = {"histogram": dict(sorted(depth_histogram.items())), "view_depths": view_depths, "max_depth": max(view_depths.values()) if view_depths else 0}

    # ---- Quadrant: Risk (complexity) vs Value (usage) ----
    # For procedures and hot tables/views
    quadrant = []
    # Dashboard downstream count per table/view
    dash_downstream: dict[str, int] = {}
    for d in ir.dashboards:
        # Count upstream tables that feed this dashboard (via dashboard_lineage)
        dl = next((x for x in dashboard_lineage if x["dashboard"] == d.fqn), None)
        if dl:
            for tbl in dl["upstream_tables"]:
                dash_downstream[tbl] = dash_downstream.get(tbl, 0) + 1
    for fqn, cnt in incoming_counts.items():
        if cnt == 0:
            continue
        # Value = fan_in + dashboard downstream
        value = cnt + dash_downstream.get(fqn, 0) * 2
        # Risk: if it's a procedure, use complexity; else use 0-1 based on unresolved
        proc = next((p for p in ir.procedures if p.fqn == fqn), None)
        if proc:
            risk = complexity_order.get(proc.complexity, 1) + (1 if proc.has_dynamic else 0) + (1 if proc.has_cursor else 0)
        else:
            # Views/tables: risk = 1 if view is unresolved/dynamic else 0-2 based on depth
            v = next((x for x in ir.views if x.fqn == fqn), None)
            risk = (2 if v and v.unresolved else 1 if v and v.complexity in ("join", "aggregate") else 0)
            if fqn in all_table_fqns and fqn.startswith("DW.TMP"):
                risk = 0
        quadrant.append({"fqn": fqn, "value": value, "risk": risk, "fan_in": cnt, "dashboards": dash_downstream.get(fqn, 0)})

    # ---- Blast radius stats (p50/p90/max) ----
    import statistics
    blast_sizes = []
    for fqn in all_estate_fqns:
        # BFS downstream size
        visited = set([fqn])
        queue = [fqn]
        count = -1  # exclude self
        idx = 0
        while idx < len(queue):
            cur = queue[idx]; idx += 1; count += 1
            for nb in outgoing.get(cur, []):
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        if count > 0:
            blast_sizes.append(count)
    blast_sizes_sorted = sorted(blast_sizes)
    if blast_sizes_sorted:
        p50 = blast_sizes_sorted[len(blast_sizes_sorted)//2]
        p90 = blast_sizes_sorted[int(len(blast_sizes_sorted)*0.9)] if len(blast_sizes_sorted) > 1 else blast_sizes_sorted[0]
        p_max = max(blast_sizes_sorted)
        p_mean = round(sum(blast_sizes_sorted)/len(blast_sizes_sorted), 1)
    else:
        p50 = p90 = p_max = p_mean = 0
    blast_stats = {"p50": p50, "p90": p90, "max": p_max, "mean": p_mean, "count": len(blast_sizes_sorted)}

    # ---- Dashboard criticality (synthetic heuristic) ----
    criticality_map = {
        "RISK_REPORT": "regulatory",
        "REVENUE_DASH": "revenue",
        "BRANCH_PERF_DASH": "revenue",
        "SALES_PIPELINE_DASH": "revenue",
        "RECON_DASH": "ops",
        "KYC_PENDING_REPORT": "regulatory",
        "LOAN_PIPELINE_REPORT": "ops",
        "MARKET_SNAPSHOT_REPORT": "ops",
    }
    for dl in dashboard_lineage:
        dl["criticality"] = criticality_map.get(dl["dashboard"], "ops")
        # Synthetic last_run: daily for regulatory/revenue, weekly for ops
        dl["last_run"] = "daily 06:00" if dl["criticality"] in ("regulatory", "revenue") else "weekly"

    return {
        "orphan_tables": orphan_tables,
        "orphan_views": orphan_views,
        "orphan_procedures": orphan_procs,
        "orphan_count": len(orphan_tables) + len(orphan_views) + len(orphan_procs),
        "hot_tables": hot_tables,
        "usage_frequency": usage_frequency,
        "complexity_ranking": complexity_ranking,
        "dashboard_lineage": dashboard_lineage,
        "circular_dependencies": circular,
        "circular_count": len(circular),
        "coverage_pct": coverage_pct,
        "total_nodes": total_nodes,
        "nodes_with_edges": nodes_with_edges,
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "total_edges": len(ir.edges),
        "total_tables": len(ir.tables),
        "total_views": len(ir.views),
        "total_procedures": len(ir.procedures),
        "total_etl_jobs": len(ir.etl_jobs),
        "total_schedules": len(ir.schedules),
        "total_dashboards": len(ir.dashboards),
        # New narrative metrics
        "estate_health": estate_health,
        "health_label": health_label,
        "health_color": health_color,
        "migration_readiness": readiness,
        "orphan_cost_usd": orphan_cost_usd,
        "orphan_cost_label": orphan_cost_label,
        "orphan_pct": round(orphan_pct, 1),
        "lineage_depth": lineage_depth,
        "quadrant": quadrant,
        "blast_stats": blast_stats,
    }
