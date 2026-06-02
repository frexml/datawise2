"""
Partner Connection Extractor Module
Extracts all partner relationships from DSX files, handling multi-output links
"""
from typing import Dict, List, Any, Tuple

class PartnerExtractor:
    """Extract and manage partner connections from DSX data"""
    
    def __init__(self, parsed_data: Dict[str, Any]):
        self.parsed_data = parsed_data
        self.stage_map = {}  # stage_id -> stage_name
        self.pins = {}  # pin_id -> {stage_id, stage_name, type}
        self.partners = {}  # link_name -> [(source_pin, target_stage, target_pin)]
        
    def extract_all_connections(self) -> Dict[str, Any]:
        """Main method to extract all connections"""
        # Step 1: Build stage map from CContainerView
        self._build_stage_map()
        
        # Step 2: Extract all DSRECORD entries
        ds_records = self._get_all_dsrecords()
        
        # Step 3: Extract pins from stage records
        self._extract_pins(ds_records)
        
        # Step 4: Extract partner relationships from pin records
        self._extract_partners(ds_records)
        
        # Step 5: Build edges from partners
        edges = self._build_edges()
        
        return {
            "stage_map": self.stage_map,
            "pins": self.pins,
            "partners": self.partners,
            "edges": edges
        }
    
    def _build_stage_map(self):
        """Build stage ID to name mapping from CContainerView"""
        container_view = self._find_container_view()
        if not container_view:
            return
        
        stage_names = container_view.get("StageNames", "").split("|")
        stage_ids = container_view.get("StageList", "").split("|")
        
        for sid, sname in zip(stage_ids, stage_names):
            if sid and sname:
                self.stage_map[sid] = sname
                self.stage_map[sname] = sid  # Reverse mapping
    
    def _find_container_view(self) -> Dict[str, Any]:
        """Find CContainerView in parsed data"""
        ds_records = self.parsed_data.get("DSRECORD", [])
        
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
        
        return search(self.parsed_data)
    
    def _get_all_dsrecords(self) -> List[Dict[str, Any]]:
        """Get all DSRECORD entries from parsed data"""
        ds_records = self.parsed_data.get("DSRECORD", [])
        
        # Also check under DSJOB
        if not ds_records:
            dsjob_list = self.parsed_data.get("DSJOB", [])
            if isinstance(dsjob_list, list) and len(dsjob_list) > 0:
                ds_records = dsjob_list[0].get("DSRECORD", [])
        
        return ds_records if isinstance(ds_records, list) else []
    
    def _extract_pins(self, ds_records: List[Dict[str, Any]]):
        """Extract all pins from stage records"""
        for record in ds_records:
            ole_type = record.get("OLEType", "")
            
            # Only process stage records
            if "Stage" not in ole_type:
                continue
            
            stage_id = record.get("Identifier")
            if not stage_id or stage_id not in self.stage_map:
                continue
            
            stage_name = self.stage_map.get(stage_id)
            
            # Extract output pins
            output_pins_str = record.get("OutputPins", "")
            if output_pins_str:
                for pin_id in output_pins_str.split("|"):
                    pin_id = pin_id.strip()
                    if pin_id:
                        self.pins[pin_id] = {
                            "stage_id": stage_id,
                            "stage_name": stage_name,
                            "type": "output"
                        }
            
            # Extract input pins
            input_pins_str = record.get("InputPins", "")
            if input_pins_str:
                for pin_id in input_pins_str.split("|"):
                    pin_id = pin_id.strip()
                    if pin_id:
                        self.pins[pin_id] = {
                            "stage_id": stage_id,
                            "stage_name": stage_name,
                            "type": "input"
                        }
    
    def _extract_partners(self, ds_records: List[Dict[str, Any]]):
        """Extract all partner relationships from output pin records"""
        for record in ds_records:
            ole_type = record.get("OLEType", "")
            
            # Only process output pin records
            if "Output" not in ole_type:
                continue
            
            pin_id = record.get("Identifier", "")
            link_name = record.get("Name", "")
            partner_field = record.get("Partner", "")
            
            if not link_name or not partner_field:
                continue
            
            # Partner can be a single value or a list (if multiple DSRECORD with same Name)
            partner_values = []
            if isinstance(partner_field, list):
                partner_values = partner_field
            else:
                partner_values = [partner_field]
            
            # Process each partner value
            for partner_str in partner_values:
                if not partner_str or "|" not in partner_str:
                    continue
                
                # Partner format: "stage_id|pin_id"
                parts = partner_str.split("|")
                if len(parts) >= 2:
                    target_stage_id = parts[0]
                    target_pin_id = parts[1]
                    
                    # Initialize list for this link if not exists
                    if link_name not in self.partners:
                        self.partners[link_name] = []
                    
                    # Add partner connection
                    self.partners[link_name].append({
                        "source_pin": pin_id,
                        "target_stage_id": target_stage_id,
                        "target_pin_id": target_pin_id
                    })
    
    def _build_edges(self) -> List[Dict[str, Any]]:
        """Build edges from partner relationships"""
        edges = []
        
        for link_name, partner_list in self.partners.items():
            for partner in partner_list:
                source_pin = partner["source_pin"]
                target_stage_id = partner["target_stage_id"]
                target_pin_id = partner["target_pin_id"]
                
                # Get source stage from pin
                source_stage_name = "Unknown"
                if source_pin in self.pins:
                    source_stage_name = self.pins[source_pin]["stage_name"]
                
                # Get target stage name
                target_stage_name = self.stage_map.get(target_stage_id, "Unknown")
                
                edges.append({
                    "link_name": link_name,
                    "source": source_stage_name,
                    "source_pin": source_pin,
                    "target": target_stage_name,
                    "target_pin": target_pin_id
                })
        
        return edges


def extract_partner_connections(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main function to extract all partner connections from DSX data
    
    Args:
        parsed_data: Parsed DSX data dictionary
        
    Returns:
        Dictionary containing stage_map, pins, partners, and edges
    """
    extractor = PartnerExtractor(parsed_data)
    return extractor.extract_all_connections()
