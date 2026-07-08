from dsxlineage.agents.parser_agent import ParserAgent
from dsxlineage.agents.lineage_agent import LineageAgent
import json

# Parse the DSX file
parser = ParserAgent()
file_path = "data/samples/Batc_BNCMRXMTLTrxStaging.dsx"

print(f"Parsing file: {file_path}\n")

# Run parser
parse_state = parser.run({"file_path": file_path})
parsed_data = parse_state.get("parsed_data", {})

# Run lineage agent
lineage_agent = LineageAgent()
lineage_state = lineage_agent.run({"parsed_data": parsed_data})
lineage_result = lineage_state.get("lineage_result", {})

# Print results
print("=" * 80)
print("STAGES")
print("=" * 80)

stages = lineage_result.get("stages", {})
positions = lineage_result.get("positions", {})

# Get unique stage IDs (exclude reverse mappings)
stage_ids = [k for k in stages.keys() if k.startswith('V')]

print(f"\nTotal Stages: {len(stage_ids)}\n")

for i, stage_id in enumerate(stage_ids, 1):
    stage_name = stages.get(stage_id, "Unknown")
    pos = positions.get(stage_id, {})
    x = pos.get("x", 0)
    y = pos.get("y", 0)
    print(f"{i}. {stage_name}")
    print(f"   ID: {stage_id}")
    print(f"   Position: ({x}, {y})")
    print()

print("=" * 80)
print("PINS")
print("=" * 80)

pins = lineage_result.get("pins", {})
print(f"\nTotal Pins: {len(pins)}\n")

# Group pins by stage
pins_by_stage = {}
for pin_id, pin_info in pins.items():
    stage_name = pin_info.get("stage_name")
    if stage_name not in pins_by_stage:
        pins_by_stage[stage_name] = {"input": [], "output": []}
    pin_type = pin_info.get("type")
    pins_by_stage[stage_name][pin_type].append(pin_id)

for stage_name in sorted(pins_by_stage.keys()):
    print(f"{stage_name}:")
    if pins_by_stage[stage_name]["input"]:
        print(f"  Input Pins:  {', '.join(pins_by_stage[stage_name]['input'])}")
    if pins_by_stage[stage_name]["output"]:
        print(f"  Output Pins: {', '.join(pins_by_stage[stage_name]['output'])}")
    print()

print("=" * 80)
print("LINKS / EDGES")
print("=" * 80)

edges = lineage_result.get("edges", [])
print(f"\nTotal Links: {len(edges)}\n")

for i, edge in enumerate(edges, 1):
    link_name = edge.get("link_name")
    source = edge.get("source")
    target = edge.get("target")
    source_pin = edge.get("source_pin", "N/A")
    target_pin = edge.get("target_pin", "N/A")
    
    print(f"{i}. {link_name}")
    print(f"   Flow: {source} [{source_pin}] → {target} [{target_pin}]")
    print()

print("=" * 80)
print("LINEAGE FLOW DIAGRAM")
print("=" * 80)
print()

# Build a simple flow diagram
stage_links = {}
for edge in edges:
    source = edge.get("source")
    target = edge.get("target")
    link_name = edge.get("link_name")
    
    if source not in stage_links:
        stage_links[source] = []
    stage_links[source].append(f"{link_name} → {target}")

for stage in stage_ids:
    stage_name = stages.get(stage)
    print(f"{stage_name}")
    if stage_name in stage_links:
        for link in stage_links[stage_name]:
            print(f"  ├─ {link}")
    print()
