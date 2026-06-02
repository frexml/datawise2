from typing import Dict, Any
import json
from app.agents.lib.detailed_analyzer import DSXAnalyzer

class AnalyzerAgent:
    def __init__(self):
        pass

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # We need to pass the data to the analyzer. 
        # The DSXAnalyzer currently reads from a file. 
        # We can either modify it to read from dict or save to temp file.
        # For simplicity and reusing existing code, we'll instantiate it with the file path 
        # (since it does the same load internally) OR we can modify DSXAnalyzer.
        
        # Actually, since we already have parsed_data in state, passing it directly would be better 
        # but DSXAnalyzer is designed to load from file.
        # Let's just use the file path again for the analyzer to keep it simple and robust 
        # with the existing tested code.
        
        parsed_data = state.get("parsed_data")
        if not parsed_data:
             raise ValueError("No parsed data provided in state")
            
        print(f"AnalyzerAgent: Analyzing data from memory")
        analyzer = DSXAnalyzer(data=parsed_data)
        analyzer.load() # Will skip since data is set
        analyzer.analyze()
        
        # generate_json_report returns a string, we want a dict
        report_json_str = analyzer.generate_json_report()
        analysis_result = json.loads(report_json_str)
        
        return {"analysis_result": analysis_result}
