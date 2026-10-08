"""
Pure parser library - re-export of agents/lib parsers.

Estate-only pivot: parsers are a stateless library with no FastAPI/Celery.
Import from here, not from agents.lib, for all new estate code.

  from dsxlineage.parsers import parse_dsx, parse_dtsx, parse_informatica_xml, detect_dialect, DSXAnalyzer

Legacy agents (LangGraph + LLM) remain in src/dsxlineage/agents/ for archival
but are not mounted in the estate API.
"""
from dsxlineage.agents.lib.dsx_parser import parse_dsx  # noqa: F401
from dsxlineage.agents.lib.dialect_detection import detect_dialect  # noqa: F401
from dsxlineage.agents.lib.detailed_analyzer import DSXAnalyzer  # noqa: F401
from dsxlineage.agents.lib.informatica_parser import parse_informatica_xml  # noqa: F401
from dsxlineage.agents.lib.ssis_parser import parse_dtsx  # noqa: F401

# Keep partner/ssis/informatica analyzers as they are used by estate extractor
# (estate/extractor.py still imports from agents.lib - will migrate to parsers/ next)
