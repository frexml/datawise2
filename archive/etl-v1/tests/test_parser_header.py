from dsxlineage.agents.lib.dsx_parser import parse_dsx
import json
import os

file_path = "data/samples/BNCMRXALLInsSTGTransactionActual.dsx"
if not os.path.exists(file_path):
    print(f"File not found: {file_path}")
    exit(1)

try:
    data = parse_dsx(file_path)
    print("Root keys:", list(data.keys()))
    
    if "HEADER" in data:
        print("\nHEADER found:")
        print(json.dumps(data["HEADER"], indent=2))
    else:
        print("\nHEADER not found in root keys")
        
    if "DSJOB" in data:
        print("\nDSJOB found:")
        # Print first few keys of DSJOB
        dsjob = data["DSJOB"]
        if isinstance(dsjob, list):
            print("DSJOB is a list, showing first item keys:", list(dsjob[0].keys()))
            print("Identifier:", dsjob[0].get("Identifier"))
            print("DateModified:", dsjob[0].get("DateModified"))
            print("TimeModified:", dsjob[0].get("TimeModified"))
        else:
            print("DSJOB keys:", list(dsjob.keys()))
            
except Exception as e:
    print(f"Error parsing: {e}")
