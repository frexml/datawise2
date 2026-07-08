#!/usr/bin/env python3
"""
Test partner pin extraction with real DSX file
"""
from dsxlineage.agents.lib.dsx_parser import parse_dsx

# Parse real DSX file
dsx_file = "data/samples/BNCMRXALLInsSTGTransactionActual.dsx"
print(f"Parsing {dsx_file}...")
parsed_data = parse_dsx(dsx_file)

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

print(f"✓ Found {len(stage_map)} stages")

# Extract partner relationships
link_partners = {}
output_records_count = 0

for record in ds_records:
    ole_type = record.get("OLEType", "")
    if "Output" in ole_type:
        output_records_count += 1
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

print(f"✓ Found {output_records_count} output pin records")
print(f"✓ Found {len(link_partners)} link partners with Partner field")

if link_partners:
    print("\nSample partner relationships:")
    for i, (link_name, partner_info) in enumerate(list(link_partners.items())[:5]):
        partner_stage = stage_map.get(partner_info["partner_id"], "Unknown")
        print(f"  {link_name} -> {partner_stage} (pin: {partner_info['partner_pin']})")
    
    print(f"\n✅ Partner extraction works! Found {len(link_partners)} partner relationships")
else:
    print("\n⚠️  No partner relationships found in this DSX file")
    print("This might be normal if the file doesn't use partner-based connections")
