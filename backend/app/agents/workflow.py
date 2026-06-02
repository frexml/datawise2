from typing import Dict, Any, TypedDict
from langgraph.graph import StateGraph, END
from app.agents.parser_agent import ParserAgent
from app.agents.analyzer_agent import AnalyzerAgent
from app.agents.lineage_agent import LineageAgent
from app.agents.deep_analyzer_agent import DeepAnalyzerAgent

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

def process_file(file_path: str):
    """
    Entry point to run the graph.
    """
    initial_state = {"file_path": file_path, "parsed_data": {}, "analysis_result": {}, "llm_explanation": ""}
    result = app.invoke(initial_state)
    return result
