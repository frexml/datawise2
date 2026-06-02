#!/usr/bin/env python3
"""
Test partner pin relationship extraction
"""

# Mock parsed data with the example you provided
mock_parsed_data = {
    "DSJOB": [{
        "DSRECORD": [
            {
                "Identifier": "V56S0",
                "OLEType": "CCustomStage",
                "Name": "Lookup_2",
                "InputPins": "V56S0P4|V56S0P7",
                "OutputPins": "V56S0P8",
                "StageType": "PxLookup"
            },
            {
                "Identifier": "V56S0P8",
                "OLEType": "CCustomOutput",
                "Name": "DSLink339",
                "Partner": "V0S554|V0S554P1"
            },
            {
                "Identifier": "V0S554",
                "OLEType": "CTransformerStage",
                "Name": "Transformer_554",
                "InputPins": "V0S554P1",
                "OutputPins": "V0S554P2|V0S554P3"
            },
            {
                "Identifier": "CONTAINER_VIEW",
                "OLEType": "CContainerView",
                "StageNames": "Lookup_2|Transformer_554",
                "StageList": "V56S0|V0S554",
                "LinkNames": "DSLink339",
                "TargetStageIDs": "V0S554",
                "LinkSourcePinIDs": "V56S0P8",
                "StageXPos": "100|300",
                "StageYPos": "100|100"
            }
        ]
    }]
}

# Simulate the lineage agent logic
parsed_data = mock_parsed_data

# Get DSRECORD
ds_records = parsed_data.get("DSRECORD", [])
if not ds_records:
    dsjob_list = parsed_data.get("DSJOB", [])
    if isinstance(dsjob_list, list) and len(dsjob_list) > 0:
        ds_records = dsjob_list[0].get("DSRECORD", [])

print(f"✓ Found {len(ds_records)} DSRECORD entries")

# Find CContainerView
container_view = None
for record in ds_records:
    if record.get("OLEType") == "CContainerView":
        container_view = record
        break

if not container_view:
    print("✗ CContainerView not found")
    exit(1)

print("✓ Found CContainerView")

# Build stage map
stage_names = container_view.get("StageNames", "").split("|")
stage_ids = container_view.get("StageList", "").split("|")
stage_map = dict(zip(stage_ids, stage_names))

print(f"✓ Stage map: {stage_map}")

# Extract partner relationships
link_partners = {}
for record in ds_records:
    ole_type = record.get("OLEType", "")
    if "Output" in ole_type:
        pin_id = record.get("Identifier", "")
        link_name = record.get("Name", "")
        partner_str = record.get("Partner", "")
        
        if partner_str and "|" in partner_str:
            parts = partner_str.split("|")
            partner_stage_id = parts[0]
            partner_pin_id = parts[1] if len(parts) > 1 else None
            
            if link_name:
                link_partners[link_name] = {
                    "partner_id": partner_stage_id,
                    "partner_pin": partner_pin_id,
                    "source_pin": pin_id
                }
                print(f"✓ Found partner for link '{link_name}': {partner_stage_id} (pin: {partner_pin_id})")

if not link_partners:
    print("✗ No link partners found")
    exit(1)

print(f"✓ Total link partners: {len(link_partners)}")

# Build edges
link_groups = container_view.get("LinkNames", "").split("|")
source_groups = container_view.get("LinkSourcePinIDs", "").split("|")

edges = []
for i in range(len(link_groups)):
    links = [l.strip() for l in link_groups[i].split(",") if l.strip()]
    sources = [s.strip() for s in source_groups[i].split(",") if s.strip()] if i < len(source_groups) else []
    
    for j, link_name in enumerate(links):
        source_pin_id = sources[j] if j < len(sources) else None
        source_stage_id = None
        if source_pin_id and 'P' in source_pin_id:
            source_stage_id = source_pin_id.split('P')[0]
        
        source_name = stage_map.get(source_stage_id, "Unknown") if source_stage_id else "Unknown"
        
        # Check partner relationship
        target_pin_id = None
        target_id = None
        target_name = "Unknown"
        
        if link_name in link_partners:
            partner_info = link_partners[link_name]
            partner_stage_id = partner_info["partner_id"]
            partner_pin_id = partner_info["partner_pin"]
            
            if partner_stage_id:
                target_id = partner_stage_id
                target_name = stage_map.get(partner_stage_id, "Unknown")
                target_pin_id = partner_pin_id
                print(f"✓ Using partner relationship for '{link_name}'")
        
        edges.append({
            "link_name": link_name,
            "source": source_name,
            "source_pin": source_pin_id,
            "target": target_name,
            "target_pin": target_pin_id
        })

print(f"\n✓ Generated {len(edges)} edges")
for edge in edges:
    print(f"  {edge['source']} -> {edge['target']} via {edge['link_name']}")
    print(f"    Source pin: {edge['source_pin']}, Target pin: {edge['target_pin']}")

# Verify expected result
expected_edge = edges[0]
if (expected_edge['source'] == 'Lookup_2' and 
    expected_edge['target'] == 'Transformer_554' and
    expected_edge['link_name'] == 'DSLink339' and
    expected_edge['target_pin'] == 'V0S554P1'):
    print("\n✅ TEST PASSED: Partner relationship correctly extracted!")
    print(f"   Lookup_2 -> Transformer_554 via DSLink339 (target pin: V0S554P1)")
else:
    print("\n❌ TEST FAILED: Partner relationship not correctly extracted")
    print(f"   Expected: Lookup_2 -> Transformer_554 via DSLink339 (target pin: V0S554P1)")
    print(f"   Got: {expected_edge['source']} -> {expected_edge['target']} via {expected_edge['link_name']} (target pin: {expected_edge['target_pin']})")
    exit(1)
