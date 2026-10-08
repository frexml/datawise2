from typing import Dict, Any
import json
from dsxlineage.agents.lib.detailed_analyzer import DSXAnalyzer
from dsxlineage.agents.lib.ssis_analyzer import analyze_ssis
from dsxlineage.agents.lib.informatica_analyzer import analyze_informatica

class AnalyzerAgent:
    def __init__(self):
        pass

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        parsed_data = state.get("parsed_data")
        if not parsed_data:
             raise ValueError("No parsed data provided in state")

        dialect = state.get("dialect")
        if dialect == "ssis":
            print("AnalyzerAgent: Analyzing SSIS package")
            return {"analysis_result": analyze_ssis(parsed_data)}
        if dialect == "informatica":
            print("AnalyzerAgent: Analyzing Informatica mapping")
            return {"analysis_result": analyze_informatica(parsed_data)}

        # DataStage: DSXAnalyzer is designed to load from file, but since we
        # already have parsed_data in state we instantiate it with data=
        # directly instead (it skips load() when data is already set).
        print(f"AnalyzerAgent: Analyzing data from memory")
        analyzer = DSXAnalyzer(data=parsed_data)
        analyzer.load() # Will skip since data is set
        analyzer.analyze()

        # generate_json_report returns a string, we want a dict
        report_json_str = analyzer.generate_json_report()
        analysis_result = json.loads(report_json_str)

        return {"analysis_result": analysis_result}
