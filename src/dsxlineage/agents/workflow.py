from typing import Callable, Dict, Any, Optional, TypedDict
from langgraph.graph import StateGraph, END
from dsxlineage.agents.parser_agent import ParserAgent
from dsxlineage.agents.analyzer_agent import AnalyzerAgent
from dsxlineage.agents.lineage_agent import LineageAgent
from dsxlineage.agents.deep_analyzer_agent import DeepAnalyzerAgent

class AgentState(TypedDict):
    file_path: str
    parsed_data: Dict[str, Any]
    analysis_result: Dict[str, Any]
    lineage_result: Dict[str, Any] # Added lineage result
    llm_explanation: str
    # Structured analysis results
    stages: list
    links: list
    annotations: list
    executive_summary: str
    executive_summary_business: str

def run_parser(state: AgentState):
    agent = ParserAgent()
    return agent.run(state)

def run_analyzer(state: AgentState):
    agent = AnalyzerAgent()
    return agent.run(state)

def run_lineage(state: AgentState):
    agent = LineageAgent()
    return agent.run(state)

def run_deep_analyzer(state: AgentState):
    agent = DeepAnalyzerAgent()
    return agent.run(state)

# Define the graph
workflow = StateGraph(AgentState)

workflow.add_node("parser", run_parser)
workflow.add_node("analyzer", run_analyzer)
workflow.add_node("lineage", run_lineage)
workflow.add_node("deep_analyzer", run_deep_analyzer)

workflow.set_entry_point("parser")
workflow.add_edge("parser", "analyzer")
workflow.add_edge("analyzer", "lineage")
workflow.add_edge("lineage", "deep_analyzer")
workflow.add_edge("deep_analyzer", END)

app = workflow.compile()

# LangGraph node name -> user-facing pipeline stage, used to drive the
# animated progress view. Node names are internal; these are what the
# frontend renders.
NODE_STAGE_NAMES = {
    "parser": "parsing",
    "analyzer": "analyzing",
    "lineage": "mapping_lineage",
    "deep_analyzer": "generating_summaries",
}

def process_file(file_path: str, on_stage: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """
    Entry point to run the graph.

    Streams node-by-node (stream_mode="updates" — the default) instead of a
    single blocking invoke() so callers can observe real pipeline progress.
    Each yielded step is {node_name: partial_state}; since no reducers are
    defined on AgentState, merging is a plain shallow overwrite — the same
    semantics invoke() used internally.
    """
    state: Dict[str, Any] = {
        "file_path": file_path, "parsed_data": {}, "analysis_result": {}, "llm_explanation": ""
    }
    for step in app.stream(state):
        for node_name, partial_state in step.items():
            state.update(partial_state)
            if on_stage:
                on_stage(NODE_STAGE_NAMES.get(node_name, node_name))
    return state
