import sys
sys.path.insert(0, '/app')

from app.agents.parser_agent import ParserAgent
import json

# Parse the DSX file
parser = ParserAgent()
file_path = "../samples/Batc_BNCMRXMTLTrxStaging.dsx"

parse_state = parser.run({"file_path": file_path})
parsed_data = parse_state.get("parsed_data", {})

print("Searching for InputPins/OutputPins in parsed_data...\n")

def search_for_pins(data, path="", max_depth=5):
    """Recursively search for InputPins or OutputPins"""
    if max_depth == 0:
        return
    
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            # Check if this dict has InputPins or OutputPins
            if "InputPins" in data or "OutputPins" in data:
                print(f"Found pins at: {current_path}")
                print(f"  OLEType: {data.get('OLEType', 'N/A')}")
                print(f"  Identifier: {data.get('Identifier', 'N/A')}")
                if "InputPins" in data:
                    print(f"  InputPins: {data['InputPins']}")
                if "OutputPins" in data:
                    print(f"  OutputPins: {data['OutputPins']}")
                print()
                return  # Don't recurse further into this object
            
            # Recurse
            search_for_pins(value, current_path, max_depth - 1)
    
    elif isinstance(data, list):
        for i, item in enumerate(data):
            current_path = f"{path}[{i}]"
            search_for_pins(item, current_path, max_depth - 1)

search_for_pins(parsed_data)

print("\nSearching for CContainerView with V0S27 (InsertLoadID)...")
# Try to find the specific stage
for i, record in enumerate(parsed_data.get("DSRECORD", [])):
    if record.get("Identifier") == "V0S27":
        print(f"\nFound V0S27 at DSRECORD[{i}]:")
        print(f"  OLEType: {record.get('OLEType')}")
        print(f"  Keys: {list(record.keys())[:30]}")
        if "InputPins" in record:
            print(f"  InputPins: {record['InputPins']}")
        if "OutputPins" in record:
            print(f"  OutputPins: {record['OutputPins']}")
        break
