"""Deep analysis of parsed DSX jobs via concurrent LLM calls.

Performance characteristics (compared to the original sequential implementation):
- All stage analyses run concurrently, bounded by _CONCURRENCY.
- Link and annotation analyses use a smaller/cheaper model (gpt-4o-mini) and
  run concurrently in their own pool.
- The growing context_summary string is replaced by a small, stable job overview
  computed once. Each stage prompt is self-contained → no inter-stage dependency,
  which is what made parallelism possible.
- Every LLM call has a bounded max_tokens.

The public surface (`DeepAnalyzerAgent().run(state) -> dict`) is unchanged; the
LangGraph workflow does not need to change.
"""
import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from dsxlineage.core.config import settings


# Bound concurrent LLM calls through OpenRouter. 24 saturates most upstream
# provider tiers without triggering 429s; LangChain's ChatOpenAI client
# retries automatically on the rare burst-induced rate limit.
_CONCURRENCY = 24

# max_tokens per call kind. Stage analyses can be lengthy (multi-section markdown
# + C++ walkthroughs); link/annotation summaries are short by design.
_STAGE_MAX_TOKENS = 1500
_LINK_MAX_TOKENS = 500
_ANNO_MAX_TOKENS = 400
_SUMMARY_MAX_TOKENS = 800

_MINI_MODEL = "openai/gpt-4o-mini"

_DIALECT_LABELS = {
    "datastage": "DataStage",
    "ssis": "SSIS (SQL Server Integration Services)",
    "informatica": "Informatica PowerCenter",
}


class DeepAnalyzerAgent:
    def __init__(self) -> None:
        # Main model: stage analyses (where C++ TrxGenCode may be present) + final summary.
        self.llm = ChatOpenAI(
            model=settings.OPENROUTER_MODEL,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            max_tokens=_STAGE_MAX_TOKENS,
        )
        # Mini model: links + annotations (schema/text summaries, low-stakes).
        self.llm_mini = ChatOpenAI(
            model=_MINI_MODEL,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            max_tokens=_LINK_MAX_TOKENS,
        )
        # Lazily initialized inside the event loop; required for asyncio.Semaphore.
        self._sem: Optional[asyncio.Semaphore] = None

    # ------------------------------------------------------------------
    # Property extraction (unchanged from the original implementation).
    # ------------------------------------------------------------------
    def _extract_subrecords(self, record: Dict[str, Any]) -> Dict[str, Any]:
        properties: Dict[str, Any] = {}

        for key, value in record.items():
            if key not in ("DSSUBRECORD", "InputPins", "OutputPins", "MetaBag"):
                properties[key] = value

        if "DSSUBRECORD" in record:
            subrecords = record["DSSUBRECORD"]
            if isinstance(subrecords, list):
                for sub in subrecords:
                    if not isinstance(sub, dict):
                        continue
                    name = sub.get("Name", "")
                    value = sub.get("Value", "")
                    if name:
                        if value:
                            properties[name] = value
                        else:
                            sub_props = {
                                k: v for k, v in sub.items()
                                if k not in ("Name", "__type__", "__children__", "Owner")
                            }
                            if sub_props:
                                properties[name] = sub_props
                    if "DSSUBRECORD" in sub:
                        for k, v in self._extract_subrecords(sub).items():
                            properties[f"{name}.{k}"] = v

        return properties

    # ------------------------------------------------------------------
    # Prompt builders (pure, no I/O).
    # ------------------------------------------------------------------
    @staticmethod
    def _build_stage_prompt(
        stage_name: str,
        stage_type: str,
        properties: Dict[str, Any],
        job_context: str,
        trx_code: str,
        trx_class: str,
        trx_cache: str,
        trx_warnings: str,
        dialect_label: str = "DataStage",
    ) -> str:
        prompt = f"""You are an expert {dialect_label} Developer.

Job Context:
{job_context}

Stage to Analyze:
Name: {stage_name}
Type: {stage_type}

Configuration Properties:
{json.dumps(properties, indent=2)}
"""

        if trx_code:
            prompt += f"""

C++ TRANSFORMATION CODE FOUND (TrxGenCode):
Class Name: {trx_class}
Cache Settings: {trx_cache}
Warnings: {trx_warnings}

CODE:
```cpp
{trx_code}
```

SPECIAL INSTRUCTION FOR C++ CODE:
Analyze the provided C++ code in detail.
1. Identify every output column assignment.
2. Explain the logic/transformation for each column.
3. Note any conditional logic (if/else), data type conversions, or string manipulations.
"""

        prompt += """

Task:
Provide a comprehensive technical analysis of this stage.

1. **Stage Overview**: What is this stage and what is its primary purpose?
2. **Configuration Analysis**: Explain key settings found in the properties.
3. **Transformation Logic** (Crucial):
   - If C++ code is present, explain it column-by-column.
   - If no code, explain the implicit logic based on properties.
4. **Data Flow**: Summary of inputs and outputs.
5. **One-Sentence Summary**: For downstream context.

Output Format:
<analysis>
(Detailed Markdown analysis)
</analysis>
<summary>
(One sentence summary)
</summary>
"""
        return prompt

    @staticmethod
    def _build_link_prompt(link_name: str, properties: Dict[str, Any], dialect_label: str = "DataStage") -> str:
        return f"""You are an expert {dialect_label} Developer.

Link to Analyze:
Name: {link_name}

Link Properties (Schema & Configuration):
{json.dumps(properties, indent=2)}

Task:
Provide a detailed technical analysis of this link, focusing on the DATA SCHEMA.

1. **Link Overview**: What is this link connecting?
2. **Schema Analysis** (Crucial):
   - Identify key columns, data types (SQLType), length/precision.
   - Note any nullable columns.
   - Explain the structure of the data being passed.
3. **Configuration**: Any specific partitioning, sorting, or buffering settings?

Output Format:
<analysis>
(Detailed schema and configuration analysis)
</analysis>
"""

    @staticmethod
    def _build_annotation_prompt(anno_name: str, properties: Dict[str, Any], dialect_label: str = "DataStage") -> str:
        return f"""You are an expert {dialect_label} Developer.

Annotation to Analyze:
Name: {anno_name}

Properties (Text & Style):
{json.dumps(properties, indent=2)}

Task:
Explain the purpose of this annotation.
1. **Content**: What does the annotation say?
2. **Context**: Based on the text, what part of the job logic does it likely describe?

Output Format:
<analysis>
(Concise explanation)
</analysis>
"""

    # ------------------------------------------------------------------
    # Tag extraction helpers.
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_tag(content: str, tag: str) -> str:
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        if open_tag not in content or close_tag not in content:
            return ""
        return content.split(open_tag, 1)[1].split(close_tag, 1)[0].strip()

    # ------------------------------------------------------------------
    # Async analysis primitives.
    # ------------------------------------------------------------------
    async def _analyze_stage(
        self, stage_id: str, stage: Dict[str, Any], job_context: str, dialect_label: str = "DataStage"
    ) -> Dict[str, Any]:
        stage_name = stage.get("Name", "Unnamed")
        stage_type = stage.get("StageType", stage.get("OLEType", "Unknown"))
        properties = self._extract_subrecords(stage)

        trx_code = properties.get("TrxGenCode", "")
        trx_class = properties.get("TrxClassName", "")
        trx_cache = properties.get("TrxGenCache", "")
        trx_warnings = properties.get("TrxGenWarnings", "")

        # Avoid duplicating large code in the prompt: pulled into its own section above.
        prompt_props = {k: v for k, v in properties.items() if k != "TrxGenCode"}

        prompt = self._build_stage_prompt(
            stage_name, stage_type, prompt_props, job_context,
            trx_code, trx_class, trx_cache, trx_warnings, dialect_label,
        )

        try:
            async with self._sem:
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content
            analysis = self._extract_tag(content, "analysis") or content.strip()
            summary = self._extract_tag(content, "summary")
            return {
                "stage_id": stage_id,
                "name": stage_name,
                "type": stage_type,
                "properties": properties,
                "llm_explanation": analysis,
                "_summary": summary,
            }
        except Exception as exc:  # noqa: BLE001 — fail one stage, not the whole job
            return {
                "stage_id": stage_id,
                "name": stage_name,
                "type": stage_type,
                "properties": properties,
                "llm_explanation": f"Error analyzing stage: {exc}",
                "_summary": "",
            }

    async def _analyze_link(self, link_id: str, link: Dict[str, Any], dialect_label: str = "DataStage") -> Dict[str, Any]:
        link_name = link.get("Name", "Unnamed")
        properties = self._extract_subrecords(link)
        prompt = self._build_link_prompt(link_name, properties, dialect_label)

        try:
            async with self._sem:
                response = await self.llm_mini.ainvoke([HumanMessage(content=prompt)])
            analysis = self._extract_tag(response.content, "analysis") or response.content.strip()
            return {
                "link_id": link_id,
                "name": link_name,
                "source_stage": "Unknown",
                "target_stage": "Unknown",
                "properties": properties,
                "llm_explanation": analysis,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "link_id": link_id,
                "name": link_name,
                "source_stage": "Unknown",
                "target_stage": "Unknown",
                "properties": properties,
                "llm_explanation": f"Error analyzing link: {exc}",
            }

    async def _analyze_annotation(
        self, anno_id: str, anno: Dict[str, Any], dialect_label: str = "DataStage"
    ) -> Dict[str, Any]:
        anno_name = anno.get("Name", "Unnamed")
        properties = self._extract_subrecords(anno)
        prompt = self._build_annotation_prompt(anno_name, properties, dialect_label)

        try:
            async with self._sem:
                response = await self.llm_mini.ainvoke(
                    [HumanMessage(content=prompt)],
                    max_tokens=_ANNO_MAX_TOKENS,
                )
            analysis = self._extract_tag(response.content, "analysis") or response.content.strip()
            return {
                "annotation_id": anno_id,
                "name": anno_name,
                "text": anno.get("Text", ""),
                "properties": properties,
                "llm_explanation": analysis,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "annotation_id": anno_id,
                "name": anno_name,
                "text": anno.get("Text", ""),
                "properties": properties,
                "llm_explanation": f"Error analyzing annotation: {exc}",
            }

    # ------------------------------------------------------------------
    # Stage execution order (unchanged logic, isolated for clarity).
    # ------------------------------------------------------------------
    @staticmethod
    def _determine_stage_order(
        stages: Dict[str, Any], containers: Dict[str, Any]
    ) -> List[str]:
        for container in containers.values():
            if container.get("OLEType") == "CContainerView":
                stage_list_str = container.get("StageList", "")
                if stage_list_str:
                    return [
                        s for s in stage_list_str.split("|")
                        if s and (not s.startswith("V") or "S" in s)
                    ]

        def sort_key(x: str) -> Tuple[int, Any]:
            if "V0S" in x:
                try:
                    return (0, int(x.replace("V0S", "")))
                except ValueError:
                    return (1, x)
            return (1, x)

        return sorted(stages.keys(), key=sort_key)

    # ------------------------------------------------------------------
    # Job context builder — replaces the rolling context_summary string.
    # ------------------------------------------------------------------
    @staticmethod
    def _build_job_context(
        job_props: Dict[str, Any], job_type: str,
        stage_order: List[str], stages: Dict[str, Any],
    ) -> str:
        names = [stages.get(sid, {}).get("Name", sid) for sid in stage_order]
        lines = [
            f"Job: {job_props.get('Name', 'Unnamed')}",
            f"Type: {job_type}",
            f"Total stages: {len(stage_order)}",
        ]
        if names:
            sample = ", ".join(names[:12]) + (" …" if len(names) > 12 else "")
            lines.append(f"Stage sequence: {sample}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Async driver.
    # ------------------------------------------------------------------
    async def _run_async(self, state: Dict[str, Any]) -> Dict[str, Any]:
        analysis_result = state.get("analysis_result")
        if not analysis_result:
            raise ValueError("No analysis result provided")

        self._sem = asyncio.Semaphore(_CONCURRENCY)

        job_props = analysis_result.get("job_properties", {})
        job_type = state.get("parsed_data", {}).get("_metadata", {}).get("job_type", "Unknown")
        components = analysis_result.get("components", {})
        stages: Dict[str, Any] = components.get("stages", {})
        links: Dict[str, Any] = components.get("links", {})
        annotations: Dict[str, Any] = components.get("annotations", {})
        containers: Dict[str, Any] = components.get("containers", {})

        stage_order = self._determine_stage_order(stages, containers)
        job_context = self._build_job_context(job_props, job_type, stage_order, stages)
        dialect_label = _DIALECT_LABELS.get(state.get("dialect", "datastage"), "ETL")

        print(
            f"DeepAnalyzerAgent: analyzing {len(stage_order)} stages, "
            f"{len(links)} links, {len(annotations)} annotations "
            f"(concurrency={_CONCURRENCY}, dialect={dialect_label})"
        )

        # Fan out — all three groups concurrently. asyncio.gather inside gather
        # is fine; outer gather just waits for the three inner gathers.
        stage_results, link_results, anno_results = await asyncio.gather(
            asyncio.gather(*(
                self._analyze_stage(sid, stages[sid], job_context, dialect_label)
                for sid in stage_order if sid in stages
            )),
            asyncio.gather(*(
                self._analyze_link(lid, link, dialect_label) for lid, link in links.items()
            )),
            asyncio.gather(*(
                self._analyze_annotation(aid, anno, dialect_label) for aid, anno in annotations.items()
            )),
        )

        analyzed_stages = [
            {k: v for k, v in s.items() if k != "_summary"} for s in stage_results
        ]
        analyzed_links = list(link_results)
        analyzed_annotations = list(anno_results)

        # Executive summaries — technical and business are two independent
        # audiences/prompts (per DataWise's documented governance model: every
        # job gets a precise technical summary AND a plain-language business
        # summary, reviewed separately). Both are synthesis tasks over the
        # per-stage one-liners already extracted above — mini model, run
        # concurrently.
        bullets = []
        for s in stage_results:
            summary = s.get("_summary") or s.get("llm_explanation", "")[:200]
            bullets.append(f"- {s['name']} ({s['type']}): {summary}")
        bullets_text = "\n".join(bullets) or "(no stages analyzed)"

        executive_summary, business_summary = await self._generate_both_summaries(bullets_text)

        return {
            "executive_summary": executive_summary,
            "executive_summary_business": business_summary,
            "stages": analyzed_stages,
            "links": analyzed_links,
            "annotations": analyzed_annotations,
        }

    # ------------------------------------------------------------------
    # Executive summary prompts — shared by the first-pass pipeline run
    # (above) and the standalone re-run-on-rejection path (below). A
    # reviewer's rejection feedback, when present, is appended as a
    # correction directive rather than discarded.
    # ------------------------------------------------------------------
    @staticmethod
    def _build_technical_prompt(bullets_text: str, feedback: Optional[str] = None) -> str:
        feedback_block = ""
        if feedback:
            feedback_block = f"""

A human reviewer rejected the previous version of this summary with the
following feedback — address it directly in your rewrite:
"{feedback}"
"""
        return f"""You are a senior data engineer reviewing an ETL job.

Here is a summary of the job's stages:
{bullets_text}
{feedback_block}
Task:
Write a technical executive summary of what this entire job does, using data
engineering terminology. Do not infer business intent beyond what the stages
show. Structure it exactly as the McKinsey pyramid-principle sections below —
lead with the answer, then support it. Use markdown; bold each bullet's
lead-in phrase. Keep the whole thing under 250 words.

Output:
**Bottom Line:** <the single governing insight — what this job does and its
technical criticality — in one sentence>

**Situation:** <1-2 sentences: job type, scale/volume signals, where it sits
in the pipeline>

**Key Findings:**
- **<lead-in>:** <supporting technical detail>
- **<lead-in>:** <supporting technical detail>
- **<lead-in>:** <supporting technical detail>

**Technical Risks:** <1-2 sentences on fragility, inefficiencies, or
dependencies observed in the stages — omit if none are evident>

**Recommended Actions:** <1-2 concrete, prioritized next steps for a data
engineer>
"""

    @staticmethod
    def _build_business_prompt(bullets_text: str, feedback: Optional[str] = None) -> str:
        feedback_block = ""
        if feedback:
            feedback_block = f"""

A human reviewer rejected the previous version of this summary with the
following feedback — address it directly in your rewrite:
"{feedback}"
"""
        return f"""You are a business analyst translating an ETL job into plain language.

Here is a summary of the job's stages:
{bullets_text}
{feedback_block}
Task:
Write a business summary explaining what business question or process this
job serves. Avoid technical jargon — a finance director should be able to
read it. Structure it exactly as the McKinsey pyramid-principle sections
below — lead with the answer, then support it. Use markdown; bold each
bullet's lead-in phrase. Keep the whole thing under 200 words.

Output:
**Bottom Line:** <the single governing insight — what business outcome this
job delivers — in one sentence, in plain language>

**Situation:** <1-2 sentences of business context: what process or question
this job supports>

**Key Findings:**
- **<lead-in>:** <supporting detail in plain language>
- **<lead-in>:** <supporting detail in plain language>
- **<lead-in>:** <supporting detail in plain language>

**Business Impact:** <1-2 sentences on what depends on this job, or what
happens if it fails or is delayed — omit if not evident>

**Recommended Next Steps:** <1-2 concrete, prioritized actions for a business
stakeholder>
"""

    async def _generate_both_summaries(self, bullets_text: str, feedback: Optional[str] = None) -> Tuple[str, str]:
        technical_prompt = self._build_technical_prompt(bullets_text, feedback)
        business_prompt = self._build_business_prompt(bullets_text, feedback)
        return await asyncio.gather(
            self._generate_summary(technical_prompt, "technical executive summary"),
            self._generate_summary(business_prompt, "business summary"),
        )

    async def _generate_summary(self, prompt: str, label: str) -> str:
        try:
            resp = await self.llm_mini.ainvoke(
                [HumanMessage(content=prompt)],
                max_tokens=_SUMMARY_MAX_TOKENS,
            )
            return resp.content.strip()
        except Exception as exc:  # noqa: BLE001
            return f"Error generating {label}: {exc}"

    # ------------------------------------------------------------------
    # Public entry points.
    # ------------------------------------------------------------------
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Preserves the sync signature workflow.py expects."""
        return asyncio.run(self._run_async(state))

    def regenerate_executive_summary(self, bullets_text: str, feedback: Optional[str] = None) -> Tuple[str, str]:
        """Re-run just the executive summary (technical, business) — used when
        a reviewer rejects a summary and chooses "Re-run" instead of editing
        it by hand. `bullets_text` is rebuilt from persisted Stage.llm_explanation
        rows since the original per-stage one-liners aren't stored (see worker.py).
        """
        return asyncio.run(self._generate_both_summaries(bullets_text, feedback))
