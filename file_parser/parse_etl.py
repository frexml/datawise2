import json
import os
import sys
from dsx_parser import parse_dsx

def main():
    file_path = 'BNCMRXALLInsSTGTransactionActual.dsx'
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        sys.exit(1)

    try:
        print(f"Parsing {file_path}...")
        data = parse_dsx(file_path)
        
        # Output to JSON for verification
        output_file = 'BNCMRXALLInsSTGTransactionActual.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            
        print(f"Successfully parsed {file_path}. Output saved to {output_file}")
        
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
