from typing import Dict, Any, List
from dsxlineage.agents.lib.partner_extractor import extract_partner_connections
from dsxlineage.agents.lib.ssis_analyzer import extract_ssis_lineage
from dsxlineage.agents.lib.informatica_analyzer import extract_informatica_lineage

class LineageAgent:
    def __init__(self):
        pass

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        dialect = state.get("dialect")
        if dialect == "ssis":
            print("LineageAgent: Starting SSIS lineage extraction...")
            lineage_result = extract_ssis_lineage(state.get("analysis_result", {}))
            print(f"LineageAgent: Extracted {len(lineage_result.get('edges', []))} edges from SSIS paths")
            return {"lineage_result": lineage_result}
        if dialect == "informatica":
            print("LineageAgent: Starting Informatica lineage extraction...")
            lineage_result = extract_informatica_lineage(state.get("analysis_result", {}))
            print(f"LineageAgent: Extracted {len(lineage_result.get('edges', []))} edges from Informatica connectors")
            return {"lineage_result": lineage_result}

        print("LineageAgent: Starting programmatic lineage analysis...")
        parsed_data = state.get("parsed_data", {})

        # Use new partner extractor module
        partner_data = extract_partner_connections(parsed_data)
        edges = partner_data.get("edges", [])
        
        print(f"LineageAgent: Extracted {len(edges)} edges using new partner extractor")
        if edges:
            print(f"LineageAgent: Sample edge - {edges[0]}")
        
        # Get positions from old logic
        positions = self._extract_positions(parsed_data)
        
        return {
            "lineage_result": {
                "stages": partner_data.get("stage_map", {}),
                "positions": positions,
                "pins": partner_data.get("pins", {}),
                "edges": edges,
                "link_partners": partner_data.get("partners", {})
            }
        }
    
    def _extract_positions(self, parsed_data: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        """Extract stage positions from CContainerView"""
        container_view = self._find_container_view(parsed_data)
        
        if not container_view:
            print("LineageAgent: CContainerView not found.")
            return {}

        stage_x_pos = container_view.get("StageXPos", "").split("|")
        stage_y_pos = container_view.get("StageYPos", "").split("|")
        stage_ids = container_view.get("StageList", "").split("|")
        
        stage_positions = {}
        for i, sid in enumerate(stage_ids):
            x = 0
            y = 0
            if i < len(stage_x_pos):
                try: x = int(stage_x_pos[i])
                except: pass
            if i < len(stage_y_pos):
                try: y = int(stage_y_pos[i])
                except: pass
            stage_positions[sid] = {"x": x, "y": y}
        
        return stage_positions
    
    def _find_container_view(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """Find CContainerView in parsed data"""
        ds_records = parsed_data.get("DSRECORD", [])
        
        if isinstance(ds_records, list):
            for record in ds_records:
                if record.get("OLEType") == "CContainerView":
                    return record
        
        # Recursive search
        def search(data):
            if isinstance(data, dict):
                if data.get("OLEType") == "CContainerView":
                    return data
                for v in data.values():
                    result = search(v)
                    if result:
                        return result
            elif isinstance(data, list):
                for item in data:
                    result = search(item)
                    if result:
                        return result
            return None
        
        return search(parsed_data)
