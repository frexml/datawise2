#!/usr/bin/env python3
"""
Test complete flow: Parse DSX -> Extract Partners -> Build Edges -> Simulate DB Save
"""
import sys
sys.path.insert(0, 'backend')

from app.agents.lib.dsx_parser import parse_dsx

dsx_file = "samples/BNCMRXALLInsSTGTransactionActual.dsx"
print(f"Testing complete flow with {dsx_file}\n")

# Step 1: Parse DSX
parsed_data = parse_dsx(dsx_file)
print("✓ Step 1: Parsed DSX file")

# Step 2: Extract DSRECORD (simulating lineage agent)
ds_records = parsed_data.get("DSRECORD", [])
if not ds_records:
    dsjob_list = parsed_data.get("DSJOB", [])
    if isinstance(dsjob_list, list) and len(dsjob_list) > 0:
        ds_records = dsjob_list[0].get("DSRECORD", [])

print(f"✓ Step 2: Found {len(ds_records)} DSRECORD entries")

# Step 3: Find CContainerView
container_view = None
for record in ds_records:
    if record.get("OLEType") == "CContainerView":
        container_view = record
        break

print("✓ Step 3: Found CContainerView")

# Step 4: Build stage map
stage_names = container_view.get("StageNames", "").split("|")
stage_ids = container_view.get("StageList", "").split("|")
stage_map = dict(zip(stage_ids, stage_names))

print(f"✓ Step 4: Built stage map with {len(stage_map)} stages")

# Step 5: Extract partner relationships
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

print(f"✓ Step 5: Extracted {len(link_partners)} partner relationships")

# Step 6: Build edges (simulating lineage agent)
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
        
        edges.append({
            "link_name": link_name,
            "source": source_name,
            "source_pin": source_pin_id,
            "target": target_name,
            "target_pin": target_pin_id
        })

print(f"✓ Step 6: Built {len(edges)} edges")

# Step 7: Check edges have target_pin
edges_with_target_pin = [e for e in edges if e['target_pin']]
edges_without_target_pin = [e for e in edges if not e['target_pin']]

print(f"\n✓ Step 7: Edge validation")
print(f"  Edges WITH target_pin: {len(edges_with_target_pin)}")
print(f"  Edges WITHOUT target_pin: {len(edges_without_target_pin)}")

if edges_with_target_pin:
    print(f"\n✓ Sample edges WITH target_pin:")
    for edge in edges_with_target_pin[:3]:
        print(f"  {edge['link_name']}: {edge['source']} -> {edge['target']}")
        print(f"    source_pin={edge['source_pin']}, target_pin={edge['target_pin']}")

# Step 8: Simulate what worker saves to DB
print(f"\n✓ Step 8: Simulating database save")
print(f"  Worker would save {len(edges)} Link records")
print(f"  Each with: source_stage, target_stage, source_pin, target_pin")

# Step 9: Check frontend rendering logic
print(f"\n✓ Step 9: Frontend rendering check")
print(f"  Frontend creates edge if: link.source_pin AND link.target_pin")
print(f"  Edges that WILL render: {len(edges_with_target_pin)}")
print(f"  Edges that WON'T render: {len(edges_without_target_pin)}")

if len(edges_with_target_pin) == len(link_partners):
    print(f"\n✅ SUCCESS: All {len(link_partners)} partner relationships will be stored and rendered!")
else:
    print(f"\n❌ ISSUE: {len(link_partners)} partners found but only {len(edges_with_target_pin)} edges have target_pin")
