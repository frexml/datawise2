from typing import Dict, Any, List
from app.agents.lib.partner_extractor import extract_partner_connections

class LineageAgent:
    def __init__(self):
        pass

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
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
