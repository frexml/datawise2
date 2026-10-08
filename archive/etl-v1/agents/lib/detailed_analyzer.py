import json
import os
import sys
from collections import defaultdict

class DSXAnalyzer:
    def __init__(self, file_path=None, data=None):
        self.file_path = file_path
        self.data = data
        self.stages = {}
        self.links = {}
        self.annotations = {}
        self.containers = {}
        self.others = []
        self.records = []
        self.job_properties = {}

    def load(self):
        if self.data:
            return

        if not self.file_path:
            raise ValueError("No file path or data provided")

        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"File {self.file_path} not found.")
        
        with open(self.file_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

    def analyze(self):
        # Extract Job Properties (Root level)
        for key, value in self.data.items():
            if key not in ['DSRECORD', 'DSSUBRECORD', '__type__', '__children__', 'DSJOB', 'HEADER']:
                if isinstance(value, str):
                    self.job_properties[key] = value

        # Find all DSRECORDs
        # Check root first
        if 'DSRECORD' in self.data:
            records = self.data['DSRECORD']
            if isinstance(records, list):
                self.records.extend(records)
            else:
                self.records.append(records)
        
        # Check inside DSJOB (common structure)
        if 'DSJOB' in self.data and isinstance(self.data['DSJOB'], list):
            for job in self.data['DSJOB']:
                if 'DSRECORD' in job:
                    records = job['DSRECORD']
                    if isinstance(records, list):
                        self.records.extend(records)
                    else:
                        self.records.append(records)
                
                # Also extract job properties from DSJOB
                for key, value in job.items():
                    if key not in ['DSRECORD', 'DSSUBRECORD', '__type__', '__children__'] and isinstance(value, str):
                        self.job_properties[key] = value
        
        # Classify Records
        for record in self.records:
            ole_type = record.get('OLEType', 'Unknown')
            identifier = record.get('Identifier', 'Unknown')
            
            if ole_type.startswith('CStage') or ole_type.startswith('CTransformerStage') or ole_type.startswith('CCustomStage') or ole_type == 'CJSJobActivity' or ole_type == 'CJSRoutineActivity' or ole_type == 'CJSSequencer':
                self.stages[identifier] = record
            elif ole_type.startswith('CLink') or ole_type.startswith('CTrxInput') or ole_type.startswith('CTrxOutput') or ole_type.startswith('CCustomInput') or ole_type.startswith('CCustomOutput') or ole_type == 'CJSActivityInput' or ole_type == 'CJSActivityOutput':
                self.links[identifier] = record
            elif ole_type == 'CAnnotation':
                self.annotations[identifier] = record
            elif ole_type == 'CContainerView':
                self.containers[identifier] = record
            else:
                self.others.append(record)

    def generate_json_report(self):
        report = {
            "job_properties": self.job_properties,
            "components": {
                "stages": self.stages,
                "links": self.links,
                "annotations": self.annotations,
                "containers": self.containers,
                "others": self.others
            }
        }
        return json.dumps(report, indent=4)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 detailed_analyzer.py <json_file>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    analyzer = DSXAnalyzer(file_path)
    try:
        analyzer.load()
        analyzer.analyze()
        print(analyzer.generate_json_report())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
