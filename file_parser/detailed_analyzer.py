import json
import os
import sys
from collections import defaultdict

class DSXAnalyzer:
    def __init__(self, file_path):
        self.file_path = file_path
        self.data = None
        self.records = []
        self.stages = {}
        self.links = {}
        self.annotations = []
        self.containers = []
        self.others = []
        self.job_properties = {}

    def load(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"File {self.file_path} not found.")
        
        with open(self.file_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

    def analyze(self):
        # Extract Job Properties (Root level)
        for key, value in self.data.items():
            if key not in ['DSRECORD', 'DSSUBRECORD', '__type__', '__children__']:
                if isinstance(value, str):
                    self.job_properties[key] = value

        # Find all DSRECORDs
        if 'DSRECORD' in self.data:
            # It could be a list or a single dict (though usually list in our parser)
            records = self.data['DSRECORD']
            if isinstance(records, list):
                self.records = records
            else:
                self.records = [records]
        
        # Classify Records
        for record in self.records:
            ole_type = record.get('OLEType', 'Unknown')
            identifier = record.get('Identifier', 'Unknown')
            
            if ole_type.startswith('CStage') or ole_type.startswith('CTransformerStage') or ole_type.startswith('CCustomStage') or ole_type == 'CJSJobActivity':
                self.stages[identifier] = record
            elif ole_type.startswith('CLink') or ole_type.startswith('CTrxInput') or ole_type.startswith('CTrxOutput') or ole_type.startswith('CCustomInput') or ole_type.startswith('CCustomOutput') or ole_type == 'CJSActivityInput' or ole_type == 'CJSActivityOutput':
                self.links[identifier] = record
            elif ole_type == 'CAnnotation':
                self.annotations.append(record)
            elif ole_type == 'CContainerView':
                self.containers.append(record)
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
