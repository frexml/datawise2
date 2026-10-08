#!/usr/bin/env python3
"""
Test complete edge building with partner relationships
"""
from dsxlineage.agents.lib.dsx_parser import parse_dsx

dsx_file = "data/samples/BNCMRXALLInsSTGTransactionActual.dsx"
print(f"Parsing {dsx_file}...\n")
parsed_data = parse_dsx(dsx_file)

# Get DSRECORD
ds_records = parsed_data.get("DSRECORD", [])
if not ds_records:
    dsjob_list = parsed_data.get("DSJOB", [])
    if isinstance(dsjob_list, list) and len(dsjob_list) > 0:
        ds_records = dsjob_list[0].get("DSRECORD", [])

# Find CContainerView
container_view = None
for record in ds_records:
    if record.get("OLEType") == "CContainerView":
        container_view = record
        break

# Build stage map
stage_names = container_view.get("StageNames", "").split("|")
stage_ids = container_view.get("StageList", "").split("|")
stage_map = dict(zip(stage_ids, stage_names))

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

print(f"Partner relationships: {len(link_partners)}")

# Build edges
link_groups = container_view.get("LinkNames", "").split("|")
target_groups = container_view.get("TargetStageIDs", "").split("|")
source_groups = container_view.get("LinkSourcePinIDs", "").split("|")

edges = []
partner_used = 0
fallback_used = 0

for i in range(len(link_groups)):
    links = [l.strip() for l in link_groups[i].split(",") if l.strip()]
    targets = [t.strip() for t in target_groups[i].split(",") if t.strip()] if i < len(target_groups) else []
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
        used_partner = False
        
        if link_name in link_partners:
            partner_info = link_partners[link_name]
            partner_stage_id = partner_info["partner_id"]
            partner_pin_id = partner_info["partner_pin"]
            
            if partner_stage_id:
                target_id = partner_stage_id
                target_name = stage_map.get(partner_stage_id, "Unknown")
                target_pin_id = partner_pin_id
                used_partner = True
                partner_used += 1
        else:
            target_id = targets[j] if j < len(targets) else None
            target_name = stage_map.get(target_id, "Unknown") if target_id else "Unknown"
            fallback_used += 1
        
        edges.append({
            "link_name": link_name,
            "source": source_name,
            "source_pin": source_pin_id,
            "target": target_name,
            "target_pin": target_pin_id,
            "used_partner": used_partner
        })

print(f"Total edges: {len(edges)}")
print(f"Edges using partner: {partner_used}")
print(f"Edges using fallback: {fallback_used}")

print("\nSample edges with partner relationships:")
partner_edges = [e for e in edges if e['used_partner']]
for edge in partner_edges[:5]:
    print(f"  {edge['source']} -> {edge['target']} via {edge['link_name']}")
    print(f"    Target pin: {edge['target_pin']}")

if partner_used == len(link_partners):
    print(f"\n✅ All {partner_used} partner relationships correctly used in edges!")
else:
    print(f"\n❌ Mismatch: {len(link_partners)} partners found but only {partner_used} used")
