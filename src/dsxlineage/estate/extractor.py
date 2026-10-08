"""
EstateIR Extractor - Phase 1a/1b/1c.

Parses the synthetic estate (and later real catalog dumps) into EstateIR.

Phasing gates (Plan v2):
  1a: catalog + views via sqlglot (DDL -> TableDef, ViewDef.source_fqns)
  1b: stored procedures (clean + unresolved flag)
  1c: scheduler + BI

Uses sqlglot for view lineage (supports Oracle dialect), with fallback
regex for edge cases the parser doesn't handle. All edges are typed
with confidence + unresolved markers.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List

import sqlglot
from sqlglot import exp

from dsxlineage.estate.ir import (
    ColumnDef,
    DashboardDef,
    EdgeType,
    ETLJobDef,
    EstateIR,
    LineageEdge,
    ProcedureDef,
    ScheduleDef,
    TableDef,
    ViewDef,
)


def _parse_view_sources(sql: str) -> tuple[list[str], bool, str | None]:
    """Extract source FQNs from a VIEW SELECT via sqlglot (Oracle dialect).

    Returns (source_fqns, unresolved, reason).
    """
    is_dynamic_sql = "EXECUTE IMMEDIATE" in sql.upper() or "dynamic" in sql.lower()

    try:
        tree = sqlglot.parse_one(sql, read="oracle")
        if tree is None:
            raise ValueError("parse returned None")
        # Find all table references
        tables = list(tree.find_all(exp.Table))
        fqns: list[str] = []
        for t in tables:
            # sqlglot Table: db may be schema, catalog may be db
            parts = []
            if t.catalog:
                parts.append(t.catalog.upper())
            if t.db:
                parts.append(t.db.upper())
            parts.append(t.name.upper() if hasattr(t, "name") and t.name else t.sql().upper().strip('"'))
            # Reconstruct FQN: handle View-as-source case (bare name) - keep as upper
            if len(parts) == 1:
                fqns.append(parts[0])
            elif len(parts) == 2:
                fqns.append(f"{parts[0]}.{parts[1]}")
            else:
                fqns.append(".".join(parts))
        # Also detect CTE-introduced names vs real tables: sqlglot includes them,
        # but for synthetic views there are no CTEs so this is fine.
        # Deduplicate preserving order
        seen = set()
        deduped = []
        for f in fqns:
            if f not in seen:
                seen.add(f)
                deduped.append(f)
        unresolved = is_dynamic_sql or any("DYNAMIC" in f for f in deduped)
        return deduped, unresolved, ("dynamic: EXECUTE IMMEDIATE" if is_dynamic_sql else None)
    except Exception as e:
        # Fallback: regex FROM/JOIN extraction (used for dynamic views)
        sources = re.findall(r"(?:FROM|JOIN)\s+([\w\.]+)", sql, re.IGNORECASE)
        fqns = [s.upper().strip().rstrip(",;") for s in sources if s.upper() not in ("SELECT", "WHERE", "ON")]
        is_dynamic = "DYNAMIC" in sql.upper() or "EXECUTE IMMEDIATE" in sql.upper()
        return fqns, is_dynamic, (f"sqlglot fallback: {e}" if is_dynamic else None)


def _parse_procedure_file(path: Path) -> ProcedureDef:
    text = path.read_text(encoding="utf-8", errors="replace")
    # Header is -- PROCEDURE: ... lines
    meta = {}
    for line in text.splitlines():
        m = re.match(r"--\s*(\w+):\s*(.*)", line)
        if m:
            meta[m.group(1).strip().upper()] = m.group(2).strip()
    name = meta.get("PROCEDURE", path.stem)
    reads = [s.strip() for s in meta.get("READS", "").split(",") if s.strip() and s.strip() != "NONE"]
    writes = [s.strip() for s in meta.get("WRITES", "").split(",") if s.strip() and s.strip() != "NONE"]
    calls = [s.strip() for s in meta.get("CALLS", "").split(",") if s.strip() and s.strip() != "NONE"]
    has_dynamic = meta.get("HAS_DYNAMIC", "False").lower() == "true"
    has_cursor = meta.get("HAS_CURSOR", "False").lower() == "true"
    complexity = meta.get("COMPLEXITY", "low").lower()
    lang = meta.get("LANGUAGE", "PL/SQL")
    typ = meta.get("TYPE", "transform")
    # Normalize FQNs: bare names like SAP_ORDERS stay as-is; DW.FACT should keep dot
    return ProcedureDef(
        fqn=name,
        language=lang,
        proc_type=typ,
        reads=reads,
        writes=writes,
        calls=calls,
        has_dynamic=has_dynamic,
        has_cursor=has_cursor,
        complexity=complexity if complexity in ("low", "medium", "high") else "low",
        source_text=text[:4000],
    )


def _parse_schedule_file(path: Path) -> ScheduleDef:
    # Be tolerant of leading whitespace before <?xml (synthetic files have it)
    text = path.read_text(encoding="utf-8", errors="replace").lstrip()
    root = ET.fromstring(text)
    name = root.get("name", path.stem)
    scheduler = root.get("type", "Control-M")
    sched = root.get("schedule", "")
    freq = root.get("frequency", "daily")
    deps = [e.text.strip() for e in root.findall("DependsOn") if e.text and e.text.strip()]
    return ScheduleDef(fqn=name, scheduler=scheduler, schedule_expr=sched, depends_on=deps, frequency=freq)


def _parse_bi_file(path: Path) -> DashboardDef | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip()
        root = ET.fromstring(text)
        # Tableau: <dashboard name=...> or <workbook><dashboard>
        # Cognos: <cognosReport name=...>
        name = root.get("name") or path.stem
        # Try to find dashboard element if root is workbook
        dash = root.find(".//dashboard")
        if dash is not None and dash.get("name"):
            name = dash.get("name")
        # Find source table/view
        source = ""
        # Tableau: <relation table="..."> or <datasource><relation>
        rel = root.find(".//relation")
        if rel is not None and rel.get("table"):
            source = rel.get("table")
        else:
            # Cognos: <source table="...">
            src = root.find(".//source")
            if src is not None and src.get("table"):
                source = src.get("table")
        if not source:
            # fallback: look for any table attribute
            for el in root.iter():
                if el.get("table"):
                    source = el.get("table")
                    break
        tool = "Tableau" if path.suffix == ".twb" else "Cognos"
        desc = ""
        title_el = root.find(".//title")
        if title_el is not None and title_el.text:
            desc = title_el.text.strip()
        return DashboardDef(fqn=name, tool=tool, source_fqn=source, description=desc)
    except Exception:
        return None


def extract_estate_ir(
    estate_root: Path,
    estate_name: str = "NORTHSTAR",
    source: str = "synthetic",
) -> EstateIR:
    """Build EstateIR from a synthetic estate directory on disk."""
    estate_root = Path(estate_root)
    ir = EstateIR(estate_name=estate_name, source=source, generated_on=date.today())

    # Track FQNs for system inference
    systems_set: set[str] = set()

    # ---- Tables: DDL/*.sql and FLAT_FILES_*.sql ----
    ddl_dir = estate_root / "DDL"
    table_defs: dict[str, TableDef] = {}
    if ddl_dir.exists():
        for sql_path in sorted(ddl_dir.glob("*.sql")):
            if sql_path.name.startswith("VIEW_") or sql_path.parent.name == "procedures":
                continue
            # Infer system.table from filename: SYSTEM_TABLE.sql
            stem = sql_path.stem  # e.g. DW_CUSTOMER_DIM
            # Need to split correctly: systems contain underscores? Use known systems
            # Try longest prefix match among known systems
            known_systems = ["SAP_ECC", "SALESFORCE", "FLAT_FILES", "DW"]
            system = "DW"
            table = stem
            for ks in sorted(known_systems, key=len, reverse=True):
                if stem.startswith(ks + "_"):
                    system = ks
                    table = stem[len(ks) + 1 :]
                    break
            fqn = f"{system}.{table}" if system != "DW" or table.startswith("DW_") is False else f"DW.{table}"
            # Normalize: SYSTEM.TABLE
            if "." not in fqn:
                # stem already was SYSTEM_TABLE, we split; reconstruct
                fqn = f"{system}.{table}"
            # For DW tables, ensure DW. prefix
            text = sql_path.read_text(encoding="utf-8", errors="replace")
            # Extract columns via simple regex: "COLNAME TYPE"
            cols: list[ColumnDef] = []
            # Find inside CREATE TABLE (...) block
            m = re.search(r"\((.*)\)", text, re.DOTALL)
            if m:
                inner = m.group(1)
                for col_line in inner.split(","):
                    col_line = col_line.strip()
                    cm = re.match(r"(\w+)\s+(\w+)", col_line)
                    if cm:
                        cols.append(ColumnDef(name=cm.group(1).upper(), data_type=cm.group(2)))
            td = TableDef(fqn=fqn.upper(), system=system.upper(), columns=cols)
            table_defs[fqn.upper()] = td
            systems_set.add(system.upper())
            ir.tables.append(td)

    # ---- Views: DDL/VIEW_*.sql ----
    for sql_path in sorted(ddl_dir.glob("VIEW_*.sql")):
        text = sql_path.read_text(encoding="utf-8", errors="replace")
        # Extract view name and SELECT
        m = re.search(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+(\w+)\s+AS\s*(.*);", text, re.IGNORECASE | re.DOTALL)
        if not m:
            m = re.search(r"VIEW\s+(\w+).*?AS\s*(.*);", text, re.IGNORECASE | re.DOTALL)
        if m:
            view_name = m.group(1).upper()
            select_sql = m.group(2).strip()
            # Determine complexity / unresolved from content
            is_dynamic = "DYNAMIC" in text.upper() or "EXECUTE IMMEDIATE" in text.upper()
            complexity = "dynamic" if is_dynamic else "simple"
            if "JOIN" in select_sql.upper():
                complexity = "join" if not is_dynamic else "dynamic"
            if "GROUP BY" in select_sql.upper():
                complexity = "aggregate" if not is_dynamic else "dynamic"
            # Check if source is another view (nested)
            sources, unresolved, reason = _parse_view_sources(select_sql)
            # If any source matches a view name, mark nested
            view_names_upper = {v.fqn for v in ir.views}
            if any(s in view_names_upper for s in sources) and not is_dynamic:
                complexity = "nested_view"
            # Cross-system if any source starts with SAP_ECC/SALESFORCE/FLAT_FILES
            if any(s.startswith(("SAP_ECC", "SALESFORCE", "FLAT_FILES")) for s in sources) and not is_dynamic:
                complexity = "cross_system"
            vd = ViewDef(
                fqn=view_name,
                sql=select_sql,
                complexity=complexity,
                source_fqns=sources,
                unresolved=is_dynamic or unresolved,
                unresolved_reason="EXECUTE IMMEDIATE / dynamic predicate" if is_dynamic else reason,
            )
            ir.views.append(vd)
            # Edges for view deps
            for src in sources:
                ir.edges.append(
                    LineageEdge(
                        source_fqn=src,
                        target_fqn=view_name,
                        edge_type=EdgeType.VIEW_DEPENDS,
                        confidence=0.0 if is_dynamic else 1.0,
                        unresolved=is_dynamic,
                        unresolved_reason="dynamic" if is_dynamic else None,
                    )
                )
            if is_dynamic:
                ir.edges.append(
                    LineageEdge(
                        source_fqn=view_name,
                        target_fqn="UNRESOLVED_DYNAMIC",
                        edge_type=EdgeType.UNRESOLVED,
                        confidence=0.0,
                        unresolved=True,
                        unresolved_reason="EXECUTE IMMEDIATE",
                    )
                )

    # ---- Procedures ----
    proc_dir = ddl_dir / "procedures"
    if proc_dir.exists():
        for sql_path in sorted(proc_dir.glob("*.sql")):
            pd = _parse_procedure_file(sql_path)
            ir.procedures.append(pd)
            for r in pd.reads:
                ir.edges.append(LineageEdge(source_fqn=r, target_fqn=pd.fqn, edge_type=EdgeType.SP_READS, confidence=0.0 if pd.has_dynamic else 1.0, unresolved=pd.has_dynamic))
            for w in pd.writes:
                ir.edges.append(LineageEdge(source_fqn=pd.fqn, target_fqn=w, edge_type=EdgeType.SP_WRITES, confidence=0.0 if pd.has_dynamic else 1.0, unresolved=pd.has_dynamic))
            for c in pd.calls:
                ir.edges.append(LineageEdge(source_fqn=pd.fqn, target_fqn=c, edge_type=EdgeType.SP_CALLS, confidence=1.0))
            if pd.has_dynamic:
                ir.edges.append(LineageEdge(source_fqn=pd.fqn, target_fqn="UNRESOLVED_DYNAMIC", edge_type=EdgeType.UNRESOLVED, confidence=0.0, unresolved=True, unresolved_reason="EXECUTE IMMEDIATE"))

    # ---- ETL jobs ----
    etl_dir = estate_root / "etl"
    if etl_dir.exists():
        for etl_path in sorted(etl_dir.glob("*.*")):
            # Infer job name from file stem
            job_name = etl_path.stem
            # Read header to get source/target if DataStage
            dialect = "datastage" if etl_path.suffix == ".dsx" else "informatica" if etl_path.suffix == ".xml" and "POWERMART" in etl_path.read_text(errors="replace")[:500] else "ssis"
            # Try to extract source/target from content
            text = etl_path.read_text(encoding="utf-8", errors="replace")[:4000]
            # For synthetic DSX: look for _Source / _Target stage names
            src = ""
            tgt = ""
            m_src = re.search(r'Name\s+"([^"]+)_Source"', text)
            m_tgt = re.search(r'Name\s+"([^"]+)_Target"', text)
            if m_src:
                src = m_src.group(1)
            if m_tgt:
                tgt = m_tgt.group(1)
            # Informatica/SSIS fallback: look for mappings
            if not src:
                m = re.search(r'FROMINSTANCE="([^"]+)"', text)
                if m:
                    src = m.group(1).replace("SQ_", "")
            if not tgt:
                m = re.search(r'TOINSTANCE="([^"]+)"', text)
                if m:
                    tgt = m.group(1)
            # SSIS - parse via OLEDB_ components, capturing dot in names like DW.RISK_STAGING
            if not src and "OLEDB_" in text:
                # Find both source and destination components
                ole_names = re.findall(r'name="OLEDB_([^"]+)"', text)
                if ole_names:
                    src = ole_names[0] if len(ole_names) >= 1 else src
                    tgt = ole_names[1] if len(ole_names) >= 2 else tgt
                else:
                    m = re.search(r"OLEDB_([\w\.]+)", text)
                    if m:
                        src = m.group(1)
            # As last resort, use job name parts
            if not src or not tgt:
                # ETL_SAP_ORDERS_TO_DW -> SAP_ORDERS, DW.FACT_ORDERS
                parts = job_name.replace("ETL_", "").split("_TO_")
                if len(parts) == 2:
                    src = src or parts[0]
                    tgt = tgt or parts[1]
                    # Map DW short name to DW.FACT_ORDERS style
                    if not tgt.startswith("DW."):
                        # Check DW tables
                        for t in ir.tables:
                            if t.fqn.endswith("." + tgt):
                                tgt = t.fqn
                                break
            # Normalize to FQN where possible
            src_fqn = src.upper()
            tgt_fqn = tgt.upper()
            # Try to resolve tgt to DW. prefix
            if "." not in tgt_fqn:
                for t in ir.tables:
                    if t.fqn.upper().endswith("." + tgt_fqn):
                        tgt_fqn = t.fqn.upper()
                        break
                if "." not in tgt_fqn:
                    tgt_fqn = f"DW.{tgt_fqn}"
            # src may be SAP_ECC.SAP_ORDERS or bare
            if "." not in src_fqn:
                # Try to find matching table
                for t in ir.tables:
                    if t.fqn.upper().endswith("." + src_fqn) or t.fqn.upper() == src_fqn:
                        src_fqn = t.fqn.upper()
                        break
            jd = ETLJobDef(fqn=job_name, dialect=dialect, source_fqn=src_fqn, target_fqn=tgt_fqn, raw_path=str(etl_path))
            ir.etl_jobs.append(jd)
            ir.edges.append(LineageEdge(source_fqn=src_fqn, target_fqn=job_name, edge_type=EdgeType.ETL_READS, confidence=1.0))
            ir.edges.append(LineageEdge(source_fqn=job_name, target_fqn=tgt_fqn, edge_type=EdgeType.ETL_WRITES, confidence=1.0))

    # ---- Schedules ----
    sched_dir = estate_root / "schedules"
    if sched_dir.exists():
        for xml_path in sorted(sched_dir.glob("*.xml")):
            sd = _parse_schedule_file(xml_path)
            ir.schedules.append(sd)
            for dep in sd.depends_on:
                ir.edges.append(LineageEdge(source_fqn=dep, target_fqn=sd.fqn, edge_type=EdgeType.SCHEDULE_DEPENDS, confidence=1.0))

    # ---- BI / Dashboards ----
    bi_dir = estate_root / "bi"
    if bi_dir.exists():
        for bi_path in sorted(bi_dir.glob("*.*")):
            dd = _parse_bi_file(bi_path)
            if dd:
                ir.dashboards.append(dd)
                if dd.source_fqn:
                    ir.edges.append(LineageEdge(source_fqn=dd.source_fqn.upper(), target_fqn=dd.fqn, edge_type=EdgeType.DASHBOARD_RENDERS, confidence=1.0))

    # ---- Column lineage (derived from view SQL where possible) ----
    # For POC, generate simple column lineage for a few key views
    # Real implementation would use sqlglot column-level lineage
    # Here we synthesize plausible edges for demo determinism
    for vd in ir.views:
        if vd.unresolved or not vd.source_fqns:
            continue
        # For each source, map columns 1:1 for demo (first 3 cols)
        # Find source table def
        for src_fqn in vd.source_fqns[:2]:
            src_table = next((t for t in ir.tables if t.fqn == src_fqn), None)
            if src_table and src_table.columns:
                for col in src_table.columns[:2]:
                    ir.column_lineage.append(
                        __import__("dsxlineage.estate.ir", fromlist=["ColumnLineage"]).ColumnLineage(
                            target_table=vd.fqn,
                            target_column=col.name,
                            source_table=src_fqn,
                            source_column=col.name,
                            transform_expr=None,
                            confidence=0.9,
                        )
                    )

    # ---- Column identities (entity resolution stub) ----
    # Group columns by canonical lowercased name sans suffix
    from collections import defaultdict

    col_groups: dict[str, list[str]] = defaultdict(list)
    for t in ir.tables:
        for c in t.columns:
            canonical = re.sub(r"_ID$|_KEY$|_FK$", "_ID", c.name.upper())
            canonical = canonical.lower()
            col_groups[canonical].append(f"{t.fqn}.{c.name}")
    for vd in ir.views:
        # Views don't have column defs in IR yet; use target_table alias
        for cl in [cl for cl in ir.column_lineage if cl.target_table == vd.fqn]:
            canonical = re.sub(r"_ID$|_KEY$|_FK$", "_ID", cl.target_column.upper()).lower()
            col_groups[canonical].append(f"{vd.fqn}.{cl.target_column}")
    for canonical, fqns in col_groups.items():
        if len(fqns) >= 2:
            fqns_sorted = sorted(set(fqns))
            primary = fqns_sorted[0]
            for fqn in fqns_sorted:
                ir.column_identities.append(
                    __import__("dsxlineage.estate.ir", fromlist=["ColumnIdentity"]).ColumnIdentity(
                        canonical_id=canonical,
                        fqn=fqn,
                        aliases=[x for x in fqns_sorted if x != fqn],
                        confidence=1.0 if canonical.endswith("_id") else 0.85,
                        match_method="alias" if canonical.endswith("_id") else "embedding",
                    )
                )

    # ---- Tolerances: load from synthetic expected file if present ----
    tol_path = estate_root / "EXPECTED_DIFF_TOLERANCES.json"
    if tol_path.exists():
        try:
            import json as _json

            tol_data = _json.loads(tol_path.read_text(encoding="utf-8"))
            from dsxlineage.estate.ir import Tolerances as _Tol

            ir.tolerances = _Tol(
                numeric_epsilon=tol_data.get("numeric_epsilon", 0.01),
                temporal_tolerance_seconds=tol_data.get("temporal_tolerance_seconds", 1),
                collation="case_insensitive" if tol_data.get("collation") == "case_insensitive" else "case_sensitive",
                masked_columns=tol_data.get("masked_columns", []),
                sampling_method="full",
            )
        except Exception:
            pass

    # ---- Systems list ----
    systems_set.update(["SAP_ECC", "SALESFORCE", "FLAT_FILES", "DW"])
    ir.systems = sorted(systems_set)

    # ---- Summaries + dedup edges ----
    # Deduplicate edges by (source, target, type)
    seen_edges: set[tuple] = set()
    deduped = []
    for e in ir.edges:
        key = (e.source_fqn, e.target_fqn, e.edge_type)
        if key not in seen_edges:
            seen_edges.add(key)
            deduped.append(e)
    ir.edges = deduped
    ir.compute_summaries()
    return ir
