#!/usr/bin/env python3
"""
Test if link names from CContainerView match output pin record names
"""
import sys
sys.path.insert(0, 'backend')

from app.agents.lib.dsx_parser import parse_dsx

# Parse real DSX file
dsx_file = "samples/BNCMRXALLInsSTGTransactionActual.dsx"
print(f"Parsing {dsx_file}...")
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

# Get link names from CContainerView
link_groups = container_view.get("LinkNames", "").split("|")
container_links = set()
for group in link_groups:
    for link in group.split(","):
        link = link.strip()
        if link:
            container_links.add(link)

print(f"Links in CContainerView: {len(container_links)}")
print(f"Sample: {list(container_links)[:5]}")

# Get link names from output pin records with Partner
output_link_names = {}
for record in ds_records:
    ole_type = record.get("OLEType", "")
    if "Output" in ole_type:
        link_name = record.get("Name", "")
        partner_str = record.get("Partner", "")
        if partner_str and "|" in partner_str:
            output_link_names[link_name] = partner_str

print(f"\nOutput pins with Partner: {len(output_link_names)}")
print(f"Sample: {list(output_link_names.keys())[:5]}")

# Check matching
matched = 0
unmatched_container = []
unmatched_output = []

for link in container_links:
    if link in output_link_names:
        matched += 1
    else:
        unmatched_container.append(link)

for link in output_link_names:
    if link not in container_links:
        unmatched_output.append(link)

print(f"\n✓ Matched links: {matched}")
print(f"✗ Links in CContainerView but not in output pins: {len(unmatched_container)}")
if unmatched_container[:3]:
    print(f"  Examples: {unmatched_container[:3]}")
print(f"✗ Links in output pins but not in CContainerView: {len(unmatched_output)}")
if unmatched_output[:3]:
    print(f"  Examples: {unmatched_output[:3]}")

if matched > 0:
    print(f"\n✅ {matched} links match between CContainerView and output pins")
else:
    print("\n❌ NO MATCHING LINKS - This is the problem!")
