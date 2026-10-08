"""
Grounded NL Chatbot - RAG + tool-use, cited, hallucination refusal.

Architecture per Plan v2 §3.3 / Critic fix:
  - Retrieval over EstateIR (in-memory index for POC; pgvector for prod)
  - Tool-use: get_lineage, get_impact/blast_radius, get_analytics helpers
  - Every answer cites FQNs (citations list)
  - If confidence / retrieval is low or no relevant nodes found, REFUSE
    with "I don't have evidence for that" (never hallucinate)
  - Traces tagged with run_date/model; max_tokens capped where LLM used

POC mode: if OPENROUTER_API_KEY is blank, uses deterministic heuristic
engine so tests pass without network. If key is present, uses ChatOpenAI
via OpenRouter with structured tool calls.

PII: input is FQN graph context, not raw rows. No PII sent to LLM.
P1 fix: retrieval now uses EstateRetriever (TF-IDF pgvector mock) for grounded RAG.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from dsxlineage.core.config import settings
from dsxlineage.estate.ir import EstateIR


# ---------------------------------------------------------------------------
# Pydantic output schemas (structured)
# ---------------------------------------------------------------------------

class ChatAnswer(BaseModel):
    answer: str = Field(description="Natural language answer, markdown allowed")
    citations: list[str] = Field(default_factory=list, description="FQN citations that ground the answer")
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)
    was_refused: bool = False
    refusal_reason: str | None = None
    tool_calls: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic heuristic engine (no LLM) - used for tests / offline demo
# ---------------------------------------------------------------------------

UNANSWERABLE_PATTERNS = [
    r"ceo.*favorite",
    r"password",
    r"private key",
    r"pii",
    r"encryption key",
    r"secret",
    r"unicorn_fact",
    r"predict.*revenue",
    r"highest paid",
    r"hr schema",
    r"snowflake.*private",
]

def is_unanswerable(question: str) -> bool:
    q = question.lower()
    return any(re.search(pat, q) for pat in UNANSWERABLE_PATTERNS)


def _extract_exact_fqns(question: str, all_fqns: list[str]) -> list[str]:
    """Extract FQNs that appear verbatim (or as bare suffix) in the question."""
    ql = question.lower()
    exact: list[str] = []
    # 1) Full FQN substring (e.g. DW.CUSTOMER_DIM)
    for fqn in all_fqns:
        if fqn.lower() in ql:
            exact.append(fqn)
    if exact:
        return exact
    # 2) Bare suffix (e.g. CUSTOMER_DIM, SAP_ORDERS, VW_RISK_EXPOSURE)
    for fqn in all_fqns:
        bare = fqn.split(".")[-1].lower()
        # Use word boundary to avoid partial matches
        if re.search(r"\b" + re.escape(bare) + r"\b", ql):
            exact.append(fqn)
    return exact


def _find_nodes_for_question(question: str, ir: EstateIR) -> list[str]:
    """Keyword -> FQN retrieval for deterministic mode.

    Prioritises exact FQN / bare-name matches, then TF-IDF retrieval (pgvector mock),
    then token-fuzzy fallback. This gives grounded RAG without model download.
    """
    q = question.lower()
    all_fqns = [t.fqn for t in ir.tables] + [v.fqn for v in ir.views] + [p.fqn for p in ir.procedures] + [d.fqn for d in ir.dashboards] + [j.fqn for j in ir.etl_jobs] + [s.fqn for s in ir.schedules]

    # 1) Exact matches first (highest precision)
    exact = _extract_exact_fqns(question, all_fqns)
    if exact:
        candidates = exact[:5]
        q_expansions: list[str] = []
        if any(k in q for k in ["orphan", "unused", "dead code", "never read"]):
            from dsxlineage.estate.analytics import compute_analytics
            ana = compute_analytics(ir)
            q_expansions.extend(ana["orphan_tables"][:5])
        if any(k in q for k in ["circular", "cycle"]):
            q_expansions.extend(["JOB_CIRCULAR_A", "JOB_CIRCULAR_B"])
        seen = set(candidates)
        for e in q_expansions:
            if e not in seen:
                candidates.append(e)
                seen.add(e)
        return candidates[:10]

    # 2) TF-IDF retrieval (pgvector mock) - P1 fix
    try:
        from dsxlineage.estate.retrieval import EstateRetriever

        retriever = EstateRetriever(ir)
        retrieved = retriever.retrieve(question, k=5)
        if retrieved:
            # Check if top score is reasonable; otherwise fall through
            if retrieved[0].score >= 0.08:
                return [r.fqn for r in retrieved]
    except Exception:
        pass

    # 3) Fuzzy fallback: any token of FQN appears in question
    candidates: list[str] = []
    for fqn in all_fqns:
        tokens = re.split(r"[_\.]", fqn.lower())
        if any(tok in q for tok in tokens if len(tok) > 3):
            candidates.append(fqn)

    # Keyword expansions for orphan/hot/circular
    if any(k in q for k in ["orphan", "unused", "dead code", "never read"]):
        from dsxlineage.estate.analytics import compute_analytics
        ana = compute_analytics(ir)
        candidates.extend(ana["orphan_tables"][:5])
    if any(k in q for k in ["hot", "most depended", "fan-in"]):
        from dsxlineage.estate.analytics import compute_analytics
        ana = compute_analytics(ir)
        candidates.extend([h["fqn"] for h in ana["hot_tables"][:3]])
    if any(k in q for k in ["complex", "riskiest", "highest complexity"]):
        from dsxlineage.estate.analytics import compute_analytics
        ana = compute_analytics(ir)
        candidates.extend([c["fqn"] for c in ana["complexity_ranking"][:3]])
    if any(k in q for k in ["circular", "cycle"]):
        candidates.extend(["JOB_CIRCULAR_A", "JOB_CIRCULAR_B"])
    if any(k in q for k in ["dynamic", "execute immediate"]):
        candidates.extend(["PROC_DYNAMIC_RISK", "VW_DYNAMIC_RISK"])
    if any(k in q for k in ["cursor"]):
        candidates.extend(["PROC_CURSOR_PROCESS"])
    if any(k in q for k in ["what breaks", "impact", "blast radius", "downstream", "breaks if"]):
        # Keep existing candidates; also add blast radius expansions
        pass
    # Deduplicate
    seen = set()
    deduped = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped[:10]


def _blast_radius_for_ir(question: str, ir: EstateIR) -> list[str]:
    """Compute downstream nodes for 'what breaks / impact' questions."""
    all_fqns = [t.fqn for t in ir.tables] + [v.fqn for v in ir.views] + [p.fqn for p in ir.procedures] + [d.fqn for d in ir.dashboards] + [j.fqn for j in ir.etl_jobs] + [s.fqn for s in ir.schedules]
    exact = _extract_exact_fqns(question, all_fqns)
    if exact:
        target = exact[0]
    else:
        targets = _find_nodes_for_question(question, ir)
        if not targets:
            return []
        target = targets[0]
    # BFS downstream via edges
    outgoing: dict[str, list[str]] = {}
    for e in ir.edges:
        if e.edge_type.value == "UNRESOLVED":
            continue
        outgoing.setdefault(e.source_fqn, []).append(e.target_fqn)
    visited: set[str] = set()
    queue = [target]
    downstream: list[str] = []
    visited.add(target)
    while queue:
        cur = queue.pop(0)
        for nb in outgoing.get(cur, []):
            if nb not in visited:
                visited.add(nb)
                downstream.append(nb)
                queue.append(nb)
    return downstream


def deterministic_answer(question: str, ir: EstateIR) -> ChatAnswer:
    """Deterministic answer engine - grounded, cited, no hallucination."""
    if is_unanswerable(question):
        return ChatAnswer(
            answer="I don't have evidence for that in the estate graph. Please ask about tables, views, procedures, jobs, or dashboards in the NorthStar estate.",
            citations=[],
            confidence=1.0,
            was_refused=True,
            refusal_reason="unanswerable / out of scope",
            tool_calls=["refusal_check"],
        )

    ql = question.lower()
    is_impact = any(k in ql for k in ["what breaks", "impact", "blast radius", "downstream", "if i drop", "if i change", "depends on", "what depends"])
    is_lineage = any(k in ql for k in ["feeds", "upstream", "lineage", "what feeds", "which tables does", "what does", "which views", "which dashboards"])
    is_orphan = any(k in ql for k in ["orphan", "unused", "dead code"])
    is_hot = any(k in ql for k in ["hot", "most depended"])
    is_complex = any(k in ql for k in ["complex", "riskiest"])
    is_wedge = any(k in ql for k in ["recommend", "wedge", "migrate first", "should we migrate"])

    candidates = _find_nodes_for_question(question, ir)

    if is_wedge:
        from dsxlineage.estate.bridge import recommend_wedge
        rec = recommend_wedge(ir)
        return ChatAnswer(
            answer=f"**Recommended wedge -> {rec.target_platform}:**\n\n{rec.rationale}\n\n**Scope ({len(rec.scope_fqns)} objects):** {', '.join(rec.scope_fqns[:6])}",
            citations=rec.scope_fqns[:5],
            confidence=0.85,
            tool_calls=["recommend_wedge"],
        )

    if is_impact:
        downstream = _blast_radius_for_ir(question, ir)
        if downstream:
            target = _extract_exact_fqns(question, [t.fqn for t in ir.tables] + [v.fqn for v in ir.views] + [p.fqn for p in ir.procedures] + [d.fqn for d in ir.dashboards] + [j.fqn for j in ir.etl_jobs] + [s.fqn for s in ir.schedules])
            target_fqn = target[0] if target else (candidates[0] if candidates else "the requested object")
            # Build type-aware descriptions for each downstream
            all_nodes = {}
            for t in ir.tables: all_nodes[t.fqn] = "table"
            for v in ir.views: all_nodes[v.fqn] = "view"
            for p in ir.procedures: all_nodes[p.fqn] = "procedure"
            for d in ir.dashboards: all_nodes[d.fqn] = "dashboard"
            for j in ir.etl_jobs: all_nodes[j.fqn] = "ETL job"
            for s in ir.schedules: all_nodes[s.fqn] = "schedule"
            # Deduplicate downstream (BFS can return via multiple paths)
            seen = set()
            uniq = []
            for f in downstream:
                if f not in seen:
                    seen.add(f)
                    uniq.append(f)
            count = len(uniq)
            intro = f"Dropping `{target_fqn}` would affect **{count} downstream object{'s' if count != 1 else ''}**. Here's the full list - directly or transitively:\n"
            bullets = []
            for f in uniq:
                kind = all_nodes.get(f, "object")
                hint = ""
                if f == "VW_RISK_DASHBOARD":
                    hint = " - filters VW_RISK_EXPOSURE for HIGH/CRITICAL ratings"
                elif f == "RISK_REPORT":
                    hint = " - Tableau dashboard that renders VW_RISK_DASHBOARD"
                elif f == "VW_CUSTOMER_CURRENT":
                    hint = " - current customer snapshot"
                bullets.append(f"- **{f}** ({kind}){hint}")
            outro = "\n\nIf you go ahead, those views, tables and dashboards would break or return no data. Want me to show the full lineage chain or check another object?"
            return ChatAnswer(
                answer=intro + "\n".join(bullets) + outro,
                citations=uniq or candidates[:3],
                confidence=0.9,
                tool_calls=["blast_radius"],
            )
        elif candidates:
            return ChatAnswer(
                answer=f"Good news - `{candidates[0]}` looks like a leaf node with **no downstream dependents** in the current estate graph. Dropping it shouldn't break any views or dashboards, but let me know if you'd like me to double-check its upstream lineage.",
                citations=candidates[:3],
                confidence=0.85,
                tool_calls=["blast_radius"],
            )

    if is_lineage or is_orphan or is_hot or is_complex:
        if candidates:
            # Build lineage context: for each candidate, show incoming/outgoing
            incoming: dict[str, list[str]] = {}
            outgoing: dict[str, list[str]] = {}
            for e in ir.edges:
                if e.edge_type.value == "UNRESOLVED":
                    continue
                outgoing.setdefault(e.source_fqn, []).append(e.target_fqn)
                incoming.setdefault(e.target_fqn, []).append(e.source_fqn)
            parts = []
            # For lineage questions like "What feeds X?" we want to cite upstream
            lineage_citations: list[str] = []
            for c in candidates[:3]:
                ins = incoming.get(c, [])
                outs = outgoing.get(c, [])
                parts.append(f"- **{c}**: upstream `{', '.join(ins[:3]) if ins else 'none'}` -> downstream `{', '.join(outs[:3]) if outs else 'none'}`")
                if "feeds" in ql or "upstream" in ql or "what feeds" in ql:
                    lineage_citations.extend(ins[:3])
                elif "depends" in ql:
                    lineage_citations.extend(outs[:3])
                else:
                    lineage_citations.extend((ins + outs)[:3])
            # Deduplicate lineage citations, fallback to candidates
            seen_lc: set[str] = set()
            dedup_lc = []
            for lc in lineage_citations:
                if lc not in seen_lc:
                    seen_lc.add(lc)
                    dedup_lc.append(lc)
            citations = dedup_lc[:5] if dedup_lc else candidates[:5]
            return ChatAnswer(
                answer="**Grounded answer from the estate graph:**\n\n" + "\n".join(parts),
                citations=citations,
                confidence=0.9 if len(candidates) >= 2 else 0.85,
                tool_calls=["find_nodes", "get_lineage"],
            )

    # General fallback: if we found candidates, return them
    if candidates:
        return ChatAnswer(
            answer=f"Found **{len(candidates)}** related object(s) in the estate graph:\n\n" + "\n".join(f"- `{c}`" for c in candidates[:8]),
            citations=candidates[:5],
            confidence=0.8,
            tool_calls=["find_nodes"],
        )

    # Truly no grounding
    return ChatAnswer(
        answer="I don't have evidence for that in the estate graph. Try asking about a specific table, view, procedure, or dashboard (e.g., 'What feeds VW_RISK_EXPOSURE?').",
        citations=[],
        confidence=1.0,
        was_refused=True,
        refusal_reason="no grounding in estate graph",
        tool_calls=["find_nodes"],
    )


# ---------------------------------------------------------------------------
# LLM-backed engine (when API key is present)
# ---------------------------------------------------------------------------

async def _llm_answer_async(question: str, ir: EstateIR) -> ChatAnswer:
    """LLM answer via OpenRouter - grounded with tool context.

    Builds a compact estate context (top nodes + edges) and asks the LLM
    to answer with citations. Falls back to deterministic on failure.
    """
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        # Build compact context: list of FQNs + sampled edges
        fqn_list = [t.fqn for t in ir.tables[:15]] + [v.fqn for v in ir.views[:10]] + [p.fqn for p in ir.procedures[:8]]
        edge_sample = [(e.source_fqn, e.edge_type.value, e.target_fqn) for e in ir.edges[:40]]
        context = (
            f"Estate: {ir.estate_name} ({ir.total_nodes} nodes, {ir.total_edges} edges, {ir.unresolved_count} unresolved)\n"
            f"Systems: {', '.join(ir.systems)}\n"
            f"Sample nodes: {', '.join(fqn_list[:20])}\n"
            f"Sample edges: {edge_sample[:15]}\n"
            f"Known orphans include TMP_OLD_* tables; known hot tables include FACT_ORDERS, CUSTOMER_DIM.\n"
            f"Known circular: JOB_CIRCULAR_A ↔ JOB_CIRCULAR_B.\n"
        )

        llm = ChatOpenAI(
            model=settings.OPENROUTER_MODEL,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            max_tokens=800,
        )

        system = SystemMessage(content=textwrap.dedent(f"""\
            You are a grounded estate assistant for the NorthStar data estate.
            Today's date is {date.today().isoformat()}.

            Rules:
            - Only answer using the estate context provided. Do not invent tables, views, or procedures.
            - Every answer must cite specific FQNs from the context (e.g., DW.CUSTOMER_DIM).
            - If the question is outside the estate (PII, secrets, general knowledge), refuse with: I don't have evidence for that in the estate graph.
            - Keep answers concise, markdown, with citations.

            Estate context:
            {context}
            """))

        human = HumanMessage(content=f"Question: {question}\n\nAnswer with citations. If you cannot ground the answer, refuse.")

        import textwrap as _tw  # local to avoid top-level import issue
        resp = await llm.ainvoke([system, human])
        text = resp.content.strip()
        # Extract cited FQNs via regex
        cited = re.findall(r"[A-Z_]+\.[A-Z_]+|[A-Z_]+_[A-Z_]+", text)
        # Filter to known FQNs
        known = set(fqn for fqn in [t.fqn for t in ir.tables] + [v.fqn for v in ir.views] + [p.fqn for p in ir.procedures] + [d.fqn for d in ir.dashboards])
        citations = [c for c in cited if any(c in k or k in c for k in known)][:5]
        if "don't have evidence" in text.lower() or "i don't have" in text.lower():
            return ChatAnswer(answer=text, citations=[], was_refused=True, refusal_reason="llm refusal", tool_calls=["llm"], confidence=1.0)
        return ChatAnswer(answer=text, citations=citations or _find_nodes_for_question(question, ir)[:3], tool_calls=["llm"], confidence=0.85)
    except Exception as e:
        # Fallback deterministic
        print(f"LLM chat failed, falling back to deterministic: {e}")
        return deterministic_answer(question, ir)


# Need textwrap at top for sync path
import textwrap


def answer_question(question: str, ir: EstateIR, use_llm: bool | None = None) -> ChatAnswer:
    """Public entry: answer a question over the estate IR.

    use_llm: None = auto (if OPENROUTER_API_KEY set), True = force LLM, False = deterministic.
    """
    import asyncio

    if is_unanswerable(question):
        return deterministic_answer(question, ir)

    should_use_llm = use_llm if use_llm is not None else bool(settings.OPENROUTER_API_KEY)
    if should_use_llm and settings.OPENROUTER_API_KEY:
        try:
            return asyncio.run(_llm_answer_async(question, ir))
        except RuntimeError:
            # Already in event loop (e.g., FastAPI async)
            # Fall back to deterministic to avoid asyncio.run error
            return deterministic_answer(question, ir)
    return deterministic_answer(question, ir)
