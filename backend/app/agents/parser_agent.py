from typing import Dict, Any
from app.agents.lib.dsx_parser import parse_dsx

class ParserAgent:
    def __init__(self):
        pass

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        file_path = state.get("file_path")
        if not file_path:
            raise ValueError("No file path provided")
        
        print(f"ParserAgent: Parsing {file_path}")
        parsed_data = parse_dsx(file_path)
        
        # Detect Job Type
        job_type_code = parsed_data.get("JobType", "Unknown")
        job_type = "Unknown"
        if job_type_code == "3":
            job_type = "Parallel (ETL)"
        elif job_type_code == "2":
            job_type = "Sequence (Batch)"
        elif job_type_code == "1":
            job_type = "Server"
        elif job_type_code == "0":
            job_type = "General"
            
        print(f"ParserAgent: Detected Job Type: {job_type} (Code: {job_type_code})")
        
        # Add metadata to parsed data
        metadata = {
            "job_type": job_type,
            "job_type_code": job_type_code
        }
        
        # Extract HEADER info
        if "HEADER" in parsed_data and isinstance(parsed_data["HEADER"], list) and len(parsed_data["HEADER"]) > 0:
            header = parsed_data["HEADER"][0]
            metadata.update({
                "server_name": header.get("ServerName"),
                "tool_version": header.get("ToolVersion"),
                "export_date": header.get("Date"),
                "export_time": header.get("Time"),
                "character_set": header.get("CharacterSet")
            })
            
        # Extract DSJOB info
        if "DSJOB" in parsed_data and isinstance(parsed_data["DSJOB"], list) and len(parsed_data["DSJOB"]) > 0:
            dsjob = parsed_data["DSJOB"][0]
            metadata.update({
                "job_identifier": dsjob.get("Identifier"),
                "date_modified": dsjob.get("DateModified"),
                "time_modified": dsjob.get("TimeModified"),
                "description": dsjob.get("Description") # Might be in a sub-record, but checking here
            })
            
        parsed_data["_metadata"] = metadata
        
        return {"parsed_data": parsed_data}
