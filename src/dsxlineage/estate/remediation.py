"""AI-assisted remediation proposals for unresolved references.

Proposals are advisory only: nothing is applied automatically. Each proposal is
returned to the caller and recorded in the evidence ledger for a named human to
approve or reject. The LLM receives only FQNs, reference reasons and counts —
never row data, credentials or PII. Without an API key (or on any LLM failure)
a deterministic rule set produces the same shape of proposal.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import uuid
from datetime import date

from pydantic import BaseModel, Field

from dsxlineage.core.config import settings
from dsxlineage.estate.ir import EstateIR

DETERMINISTIC_MODEL = "deterministic"

_EXTERNAL_SCHEMAS = {
    "SAP_ECC",
    "SALESFORCE",
    "FLAT_FILES",
    "RETAIL_CUSTOMER",
    "BSCS",
    "CDR",
    "NMS",
    "FLAT_TELCO",
    "CRM_TELCO",
    "LOAN_SRC",
    "RETAIL_SRC",
}

_ACTIONS = {
    "define_ddl",
    "map_alias",
    "capture_runtime_sql",
    "confirm_external",
    "correct_reference",
}


class RemediationProposal(BaseModel):
    """One advisory remediation for an unresolved object, awaiting human approval."""

    id: str
    fqn: str
    kind: str
    referenced_by: str = ""
    reason: str = ""
    action: str
    recommendation: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_used: str = DETERMINISTIC_MODEL
    generated_on: date = Field(default_factory=date.today)
    target_fqn: str | None = None
    target_candidates: list[str] = Field(default_factory=list)
    impact: dict[str, int] = Field(default_factory=dict)


class _AiProposal(BaseModel):
    fqn: str
    action: str
    recommendation: str
    rationale: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


def unresolved_items(ir: EstateIR) -> list[dict[str, str]]:
    """Collect object-level unresolved items: dangling refs + dynamic assets."""
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(fqn: str, kind: str, reason: str, referenced_by: str = "") -> None:
        key = (fqn, kind)
        if key in seen:
            return
        seen.add(key)
        items.append(
            {"fqn": fqn, "kind": kind, "reason": reason, "referenced_by": referenced_by}
        )

    for ref in sorted(ir.unresolved_refs, key=lambda r: r.fqn):
        add(ref.fqn, "missing_object", ref.reason, ref.referenced_by)
    for view in ir.views:
        if view.unresolved:
            add(
                view.fqn,
                "unresolved_view",
                "View SQL contains dynamic statements; lineage is incomplete.",
                "view definition",
            )
    for proc in ir.procedures:
        if proc.unresolved:
            add(
                proc.fqn,
                "unresolved_procedure",
                "Procedure uses dynamic SQL/cursors; referenced tables are not statically visible.",
                "procedure definition",
            )
    for routine in ir.routines:
        if routine.unresolved:
            add(
                routine.fqn,
                "unresolved_routine",
                routine.unresolved_reason or "Routine references could not be resolved.",
                routine.artifact or "routine",
            )
    return items


def _proposal_id(fqn: str, kind: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{fqn}|{kind}"))


def _target_candidates(ir: EstateIR, fqn: str) -> list[str]:
    """Existing objects that could be the intended target of a missing ref."""
    known = [t.fqn for t in ir.tables]
    known += [v.fqn for v in ir.views]
    known += [p.fqn for p in ir.procedures]
    known += [r.fqn for r in ir.routines]
    leaf = fqn.rsplit(".", 1)[-1].upper()
    exact = [candidate for candidate in known if candidate.rsplit(".", 1)[-1].upper() == leaf and candidate.upper() != fqn.upper()]
    by_leaf = {
        candidate.rsplit(".", 1)[-1].upper(): candidate
        for candidate in known
        if candidate.upper() != fqn.upper()
    }
    near = [
        by_leaf[match]
        for match in difflib.get_close_matches(leaf, list(by_leaf), n=3, cutoff=0.75)
    ]
    ordered = exact + [candidate for candidate in near if candidate not in exact]
    return ordered[:3]


def _impact(ir: EstateIR, fqn: str) -> dict[str, int]:
    incoming = {edge.source_fqn for edge in ir.edges if edge.target_fqn == fqn}
    outgoing = {edge.target_fqn for edge in ir.edges if edge.source_fqn == fqn}
    touching = sum(1 for edge in ir.edges if fqn in (edge.source_fqn, edge.target_fqn))
    return {"upstream": len(incoming), "downstream": len(outgoing), "edges": touching}


def _deterministic_proposal(item: dict[str, str], ir: EstateIR | None = None) -> RemediationProposal:
    fqn = item["fqn"]
    kind = item["kind"]
    schema = fqn.split(".")[0].upper() if "." in fqn else ""
    name = fqn.split(".")[-1].upper()
    candidates = _target_candidates(ir, fqn) if ir is not None else []
    target_fqn: str | None = None

    if kind in ("unresolved_view", "unresolved_procedure", "unresolved_routine"):
        action = "capture_runtime_sql"
        recommendation = (
            f"Capture the runtime SQL executed by {fqn} (sp_executesql / variable-built "
            "statements) or refactor it into static SQL so its inputs can be parsed."
        )
        rationale = (
            "Dynamic SQL hides table references from static analysis; runtime capture "
            "converts this entry into evidenced lineage."
        )
        confidence = 0.7
    elif schema in _EXTERNAL_SCHEMAS:
        action = "confirm_external"
        recommendation = (
            f"Confirm {fqn} is a real external source. If it is, add its schema/DDL to "
            "the estate snapshot; if it is stale, remove the reference from the pipeline."
        )
        rationale = (
            f"{schema} is an upstream source system, so the object may exist outside the "
            "captured warehouse DDL."
        )
        confidence = 0.6
    elif schema == "DW_AUDIT" or "audit" in name.lower():
        action = "confirm_external"
        recommendation = (
            f"Treat {fqn} as runtime infrastructure: confirm the ETL framework creates it "
            "at execution time, then exclude it from migration scope."
        )
        rationale = "Audit/batch-log tables are usually created by the scheduler, not the DDL."
        confidence = 0.65
    elif schema == "BI":
        action = "correct_reference"
        target_fqn = candidates[0] if candidates else None
        recommendation = (
            f"Correct the BI reference to {fqn}: point it at an existing warehouse/MART "
            "object or add the missing semantic-layer definition."
        )
        if target_fqn:
            recommendation = f"Re-point the BI reference from {fqn} to existing object {target_fqn}."
        rationale = "BI assets must resolve to a warehouse object for lineage to be complete."
        confidence = 0.55
    elif candidates:
        action = "map_alias"
        target_fqn = candidates[0]
        recommendation = (
            f"Map the missing reference {fqn} to existing object {target_fqn} and re-run so the "
            "unresolved edge becomes evidenced lineage."
        )
        rationale = "An existing object with a matching/similar name is the most likely intended target."
        confidence = 0.7
    else:
        action = "define_ddl"
        recommendation = (
            f"Create DDL for {fqn} or map the reference to an existing table with the same "
            "grain; then add it to EXPECTED_LINEAGE so the fix is verified."
        )
        rationale = (
            "The object is referenced by real artifacts but never defined in the captured estate."
        )
        confidence = 0.6

    return RemediationProposal(
        id=_proposal_id(fqn, kind),
        fqn=fqn,
        kind=kind,
        referenced_by=item.get("referenced_by", ""),
        reason=item.get("reason", ""),
        action=action,
        recommendation=recommendation,
        rationale=rationale,
        confidence=confidence,
        model_used=DETERMINISTIC_MODEL,
        target_fqn=target_fqn,
        target_candidates=candidates,
        impact=_impact(ir, fqn) if ir is not None else {},
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text


async def _ai_proposals(items: list[dict[str, str]], ir: EstateIR) -> list[_AiProposal]:
    """Ask the LLM for advisory proposals; caller falls back on any failure."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    catalogue = [
        {
            "fqn": item["fqn"],
            "kind": item["kind"],
            "reason": item["reason"],
            "referenced_by": item["referenced_by"],
        }
        for item in items
    ]
    system = SystemMessage(
        content=(
            "You are a senior data-platform engineer assessing unresolved lineage in a "
            f"data estate. Today's date is {date.today().isoformat()}.\n\n"
            "For every unresolved object supplied, propose one remediation for a human to "
            "approve. Use only the provided FQNs and reasons; never invent objects.\n"
            f"Allowed actions: {', '.join(sorted(_ACTIONS))}.\n"
            "Return JSON only: {\"proposals\": [{\"fqn\": str, \"action\": str, "
            "\"recommendation\": str, \"rationale\": str, \"confidence\": number}]}.\n"
            "Keep recommendation and rationale to one or two sentences. Do not include "
            "row data, credentials or personal information."
        )
    )
    human = HumanMessage(
        content=(
            f"Estate {ir.estate_name}: {len(items)} unresolved objects.\n"
            f"Items: {json.dumps(catalogue, ensure_ascii=True)}"
        )
    )
    llm = ChatOpenAI(
        model=settings.OPENROUTER_MODEL,
        temperature=0,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        max_tokens=2000,
    )
    response = await llm.ainvoke([system, human])
    content = response.content if isinstance(response.content, str) else str(response.content)
    data = json.loads(_strip_fences(content))
    raw = data.get("proposals", []) if isinstance(data, dict) else data
    proposals: list[_AiProposal] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            proposal = _AiProposal.model_validate(entry)
        except Exception:
            continue
        if proposal.action not in _ACTIONS:
            continue
        proposals.append(proposal)
    return proposals


def propose_remediations(
    ir: EstateIR, use_ai: bool | None = None
) -> list[RemediationProposal]:
    """Return one advisory proposal per unresolved object.

    ``use_ai=None`` uses the LLM when ``OPENROUTER_API_KEY`` is configured.
    Any LLM/parse failure falls back to the deterministic rules.
    """
    items = unresolved_items(ir)
    deterministic = {item["fqn"]: _deterministic_proposal(item, ir) for item in items}
    should_use_ai = use_ai if use_ai is not None else bool(settings.OPENROUTER_API_KEY)
    if not should_use_ai or not settings.OPENROUTER_API_KEY or not items:
        return [deterministic[item["fqn"]] for item in items]

    try:
        ai_proposals = asyncio.run(_ai_proposals(items, ir))
    except Exception:
        return [deterministic[item["fqn"]] for item in items]

    merged: list[RemediationProposal] = []
    ai_by_fqn = {proposal.fqn.upper(): proposal for proposal in ai_proposals}
    for item in items:
        base = deterministic[item["fqn"]]
        ai = ai_by_fqn.get(item["fqn"].upper())
        if not ai:
            merged.append(base)
            continue
        merged.append(
            base.model_copy(
                update={
                    "action": ai.action,
                    "recommendation": ai.recommendation.strip(),
                    "rationale": ai.rationale.strip(),
                    "confidence": ai.confidence,
                    "model_used": settings.OPENROUTER_MODEL,
                }
            )
        )
    return merged
