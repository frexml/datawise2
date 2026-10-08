"""ScopeIQ delivery-effort estimation agent.

Produces a per-job estimate for the downstream ScopeIQ estimation tool
(see the DataWise Platform Development Guide, §3.5): decompose the job
into a fixed set of research dimensions, run purpose-built research per
dimension (role-level day breakdown, complexity uplift signals, risk
adjustments), then deterministically aggregate into a final estimate.

Dimensions are a fixed taxonomy (not decided per job) so estimates are
comparable across an entire engagement's portfolio at the PMO level:
tech_stack, compliance_regulatory, integration_patterns, delivery_risk.

Agent contract:
  role: ScopeIQ delivery-estimation research agent, specialized per dimension
  goal: a defensible, role-level effort estimate for one ETL job
  constraints: never expose PII; every numeric field is Pydantic-validated;
    bounded max_tokens; today's date is injected into every system prompt;
    ground every number in the job's actual data, never generic assumptions
  output_schema: DimensionResearch / ScopeDecomposition (below)
"""
import asyncio
from datetime import date, datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from dsxlineage.core.config import settings

_MAX_TOKENS = 1200

_DIMENSIONS = [
    {
        "name": "tech_stack",
        "purpose": (
            "Technology and platform complexity: source/target systems, stage "
            "types, transformation depth, and dialect-specific quirks that "
            "affect build effort."
        ),
    },
    {
        "name": "compliance_regulatory",
        "purpose": (
            "Regulatory exposure: PII/financial data handling, audit and "
            "evidence requirements, governance sign-off overhead implied by "
            "this job's lineage."
        ),
    },
    {
        "name": "integration_patterns",
        "purpose": (
            "Upstream/downstream integration complexity: number of source and "
            "target tables, join/dedup/lookup/sort patterns, cross-system "
            "dependencies."
        ),
    },
    {
        "name": "delivery_risk",
        "purpose": (
            "Delivery risk: ambiguous business logic, detected inefficiencies "
            "or anti-patterns, review-cycle friction, and knowledge gaps that "
            "could slip the schedule."
        ),
    },
]

_ROLES = ["data_architect", "ai_engineer", "compliance_lead", "migration_engineer", "engagement_lead"]


class RoleDays(BaseModel):
    data_architect: float = Field(0, ge=0, le=60)
    ai_engineer: float = Field(0, ge=0, le=60)
    compliance_lead: float = Field(0, ge=0, le=60)
    migration_engineer: float = Field(0, ge=0, le=60)
    engagement_lead: float = Field(0, ge=0, le=60)


class UpliftSignal(BaseModel):
    signal: str = Field(..., description="Short label, e.g. 'regulatory'")
    uplift_pct: float = Field(..., ge=0, le=50, description="Percentage uplift this signal adds to the dimension's base effort")
    rationale: str


class RiskAdjustment(BaseModel):
    description: str
    impact_days: float = Field(..., ge=0, le=30)
    likelihood: str = Field(..., description="low, medium, or high")


class DimensionResearch(BaseModel):
    findings: str = Field(..., description="2-4 sentence research narrative justifying the numbers below")
    role_days: RoleDays
    uplift_signals: list[UpliftSignal] = Field(default_factory=list)
    risks: list[RiskAdjustment] = Field(default_factory=list)


class ScopeDecomposition(BaseModel):
    tech_stack_brief: str = Field(..., description="What specifically to research for THIS job under tech_stack")
    compliance_regulatory_brief: str
    integration_patterns_brief: str
    delivery_risk_brief: str


def _complexity_tier(total_days: float) -> str:
    if total_days < 15:
        return "low"
    if total_days < 40:
        return "medium"
    return "high"


class ScopeIQAgent:
    def __init__(self) -> None:
        base_llm = ChatOpenAI(
            model=settings.OPENROUTER_MODEL,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            max_tokens=_MAX_TOKENS,
        )
        self._decompose_llm = base_llm.with_structured_output(ScopeDecomposition)
        self._research_llm = base_llm.with_structured_output(DimensionResearch)

    @staticmethod
    def _build_job_context(job, result, lineage_rows: list, inefficiencies: list[dict]) -> str:
        lines = [
            f"Job: {job.filename} (ID {job.id})",
            f"Technical summary: {((result.llm_explanation if result else '') or '(none)')[:1500]}",
            f"Business summary: {((result.business_summary if result else '') or '(none)')[:1000]}",
            f"Lineage rules: {len(lineage_rows)} source-to-target transformation path(s).",
        ]
        if lineage_rows:
            target_tables = sorted({r.target_table for r in lineage_rows if r.target_table})
            transform_types = sorted({r.transformation_type for r in lineage_rows if r.transformation_type})
            lines.append(f"Target tables: {', '.join(target_tables[:10]) or '(unnamed)'}")
            lines.append(f"Transformation types observed: {', '.join(transform_types) or '(none)'}")

        if inefficiencies:
            lines.append(f"Detected inefficiency patterns ({len(inefficiencies)}):")
            for pattern in inefficiencies[:10]:
                desc = f"  - [{pattern.get('severity', '?')}] {pattern.get('pattern_type', '?')}: {pattern.get('description', '')}"
                lines.append(desc[:220])
        else:
            lines.append("No inefficiency patterns detected.")

        return "\n".join(lines)

    async def _decompose(self, job_context: str) -> ScopeDecomposition:
        system = SystemMessage(content=(
            "You are a ScopeIQ delivery-estimation research agent at mobileLIVE. "
            f"Today's date is {date.today().isoformat()}. "
            "Goal: scope a defensible effort estimate for one ETL migration/documentation job. "
            "Constraints: never expose PII; ground every claim in the job context given; "
            "if uncertain, say so explicitly rather than inventing detail."
        ))
        human = HumanMessage(content=(
            "Job context:\n" + job_context + "\n\n"
            "For each of the four fixed research dimensions below, write a 1-3 sentence "
            "scope brief that narrows down what is actually relevant to research for THIS "
            "specific job — not a generic description of the dimension:\n"
            "- tech_stack\n- compliance_regulatory\n- integration_patterns\n- delivery_risk"
        ))
        return await self._decompose_llm.ainvoke([system, human])

    async def _research_dimension(self, dimension: dict, scope_brief: str, job_context: str) -> DimensionResearch:
        system = SystemMessage(content=(
            "You are a ScopeIQ delivery-estimation research agent at mobileLIVE, "
            f"specialized in the '{dimension['name']}' dimension. Today's date is "
            f"{date.today().isoformat()}. Goal: produce a role-level day breakdown "
            "(Data Architect, AI Engineer, Compliance Lead, Migration Engineer, "
            "Engagement Lead) for the effort THIS dimension contributes to delivering "
            "this one ETL job, plus any complexity uplift signals and risk adjustments. "
            "Constraints: never expose PII; base every number on the job context and "
            "scope brief given, not generic assumptions; use 0 days for roles this "
            "dimension does not involve; if uncertain, say so in the findings narrative "
            "rather than inflating numbers."
        ))
        human = HumanMessage(content=(
            f"Dimension purpose: {dimension['purpose']}\n\n"
            f"Scope brief for this job: {scope_brief}\n\n"
            f"Full job context:\n{job_context}"
        ))
        return await self._research_llm.ainvoke([system, human])

    async def _run_async(self, job, result, lineage_rows: list, inefficiencies: list[dict]) -> dict:
        job_context = self._build_job_context(job, result, lineage_rows, inefficiencies)
        decomposition = await self._decompose(job_context)

        briefs = {
            "tech_stack": decomposition.tech_stack_brief,
            "compliance_regulatory": decomposition.compliance_regulatory_brief,
            "integration_patterns": decomposition.integration_patterns_brief,
            "delivery_risk": decomposition.delivery_risk_brief,
        }

        research_results = await asyncio.gather(*(
            self._research_dimension(dim, briefs[dim["name"]], job_context)
            for dim in _DIMENSIONS
        ))

        dimensions_out = []
        role_day_totals = {role: 0.0 for role in _ROLES}
        uplift_adjustments = []
        risk_adjustments = []
        total_uplift_pct = 0.0
        total_risk_days = 0.0

        for dim, research in zip(_DIMENSIONS, research_results):
            role_days_dict = research.role_days.model_dump()
            for role in _ROLES:
                role_day_totals[role] += role_days_dict[role]

            for signal in research.uplift_signals:
                total_uplift_pct += signal.uplift_pct
                uplift_adjustments.append({
                    "dimension": dim["name"],
                    "signal": signal.signal,
                    "uplift_pct": signal.uplift_pct,
                    "rationale": signal.rationale,
                })
            for risk in research.risks:
                total_risk_days += risk.impact_days
                risk_adjustments.append({
                    "dimension": dim["name"],
                    "description": risk.description,
                    "impact_days": risk.impact_days,
                    "likelihood": risk.likelihood,
                })

            dimensions_out.append({
                "dimension": dim["name"],
                "purpose": dim["purpose"],
                "scope_brief": briefs[dim["name"]],
                "findings": research.findings,
                "role_days": role_days_dict,
                "uplift_signals": [s.model_dump() for s in research.uplift_signals],
                "risks": [r.model_dump() for r in research.risks],
            })

        total_days_base = round(sum(role_day_totals.values()), 2)
        total_days_after_uplift = total_days_base * (1 + total_uplift_pct / 100)
        total_days_adjusted = round(total_days_after_uplift + total_risk_days, 2)

        return {
            "package_id": f"scopeiq-job{job.id}-{date.today().isoformat()}",
            "dimensions": dimensions_out,
            "role_day_totals": {role: round(v, 2) for role, v in role_day_totals.items()},
            "uplift_adjustments": uplift_adjustments,
            "risk_adjustments": risk_adjustments,
            "total_days_base": total_days_base,
            "total_days_adjusted": total_days_adjusted,
            "complexity_tier": _complexity_tier(total_days_adjusted),
            "generated_at": datetime.now(timezone.utc),
        }

    def run(self, job, result, lineage_rows: list, inefficiencies: list[dict]) -> dict:
        return asyncio.run(self._run_async(job, result, lineage_rows, inefficiencies))


def generate_estimate_for_job(job_id: int) -> dict:
    """Sync entry point for the Celery task — fetches its own data (Postgres
    + best-effort Neo4j), mirroring the self-contained style of
    `inefficiency_agent.detect_inefficiencies`."""
    from dsxlineage.db.database import SessionLocal
    from dsxlineage.db import models

    db = SessionLocal()
    try:
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        if not job:
            raise ValueError(f"Job {job_id} not found")
        result = db.query(models.Result).filter(models.Result.job_id == job_id).first()
        lineage_rows = db.query(models.Lineage).filter(models.Lineage.job_id == job_id).all()

        inefficiencies: list[dict] = []
        try:
            from dsxlineage.db.graph import run_cypher

            inefficiencies = run_cypher([{
                "cypher": """
                    MATCH (:Job {job_id: $job_id})-[:HAS_PATTERN]->(p:InefficiencyPattern)
                    RETURN p.pattern_type AS pattern_type, p.severity AS severity, p.description AS description
                """,
                "params": {"job_id": job_id},
            }])[0]
        except Exception as exc:  # noqa: BLE001 — best-effort, same guard as /api/stats
            print(f"Warning: could not fetch inefficiency patterns for ScopeIQ estimate (job {job_id}): {exc}")

        agent = ScopeIQAgent()
        return agent.run(job, result, lineage_rows, inefficiencies)
    finally:
        db.close()
