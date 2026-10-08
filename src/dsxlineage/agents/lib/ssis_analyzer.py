"""Converts the raw SSIS parse tree (ssis_parser.parse_dtsx output) into the
same {job_properties, components: {stages, links, annotations, containers,
others}} shape DataStage's DSXAnalyzer produces, and the same {edges, ...}
shape partner_extractor.extract_partner_connections produces - so every
downstream agent (DeepAnalyzerAgent, worker.py persistence, lineage_analyzer)
works unchanged regardless of source dialect.
"""
from typing import Any


def analyze_ssis(parsed_data: dict) -> dict:
    stages: dict[str, Any] = {}
    links: dict[str, Any] = {}
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return f"S{seq:03d}"

    def add_pipeline(ex: dict) -> None:
        comp_ref_to_id: dict[str, str] = {}
        io_ref_to_comp_ref: dict[str, str] = {}

        for comp in ex["components"]:
            sid = next_id()
            comp_ref_to_id[comp["ref_id"]] = sid
            stages[sid] = {
                "Identifier": sid,
                "Name": comp["name"],
                "StageType": comp["component_class_id"],
                "OLEType": comp["component_class_id"],
                "Description": comp.get("description", ""),
                "Properties": comp.get("properties", {}),
                "Connections": comp.get("connections", []),
                "Inputs": comp.get("inputs", []),
                "Outputs": comp.get("outputs", []),
            }
            for io in comp.get("inputs", []) + comp.get("outputs", []):
                io_ref_to_comp_ref[io["ref_id"]] = comp["ref_id"]

        for path in ex.get("paths", []):
            lid = next_id()
            source_comp_ref = io_ref_to_comp_ref.get(path["start_id"])
            target_comp_ref = io_ref_to_comp_ref.get(path["end_id"])
            source_stage_id = comp_ref_to_id.get(source_comp_ref)

            source_columns = []
            if source_stage_id:
                for out in stages[source_stage_id].get("Outputs", []):
                    if out["ref_id"] == path["start_id"]:
                        source_columns = out.get("columns", [])
                        break

            links[lid] = {
                "Identifier": lid,
                "Name": path["name"],
                "OLEType": "SSISPath",
                "Columns": source_columns,
                "_source_stage_id": source_stage_id,
                "_target_stage_id": comp_ref_to_id.get(target_comp_ref),
                "_source_ref": path["start_id"],
                "_target_ref": path["end_id"],
            }

    def walk(executables: list[dict]) -> None:
        for ex in executables:
            if "components" in ex:
                add_pipeline(ex)
            elif "executables" in ex:
                walk(ex["executables"])
            else:
                sid = next_id()
                type_name = ex.get("creation_name") or ex.get("executable_type") or "Unknown"
                stages[sid] = {
                    "Identifier": sid,
                    "Name": ex.get("object_name") or "Unnamed Task",
                    "StageType": type_name,
                    "OLEType": type_name,
                    "Properties": ex.get("properties") or {},
                }

    walk(parsed_data.get("executables", []))

    return {
        "job_properties": {"Name": parsed_data.get("package_name", "Unnamed Package")},
        "components": {
            "stages": stages,
            "links": links,
            "annotations": {},
            "containers": {},
            "others": [],
        },
    }


def extract_ssis_lineage(analysis_result: dict) -> dict:
    """SSIS equivalent of partner_extractor.extract_partner_connections -
    paths already carry direct start/end references, so no pin-string
    parsing is needed; this just resolves stage names for the edges list
    worker.py expects."""
    components = analysis_result.get("components", {})
    stages = components.get("stages", {})
    links = components.get("links", {})

    edges = []
    for link in links.values():
        source_stage = stages.get(link.get("_source_stage_id"), {})
        target_stage = stages.get(link.get("_target_stage_id"), {})
        edges.append({
            "link_name": link.get("Name"),
            "source": source_stage.get("Name", "Unknown"),
            "source_pin": link.get("_source_ref"),
            "target": target_stage.get("Name", "Unknown"),
            "target_pin": link.get("_target_ref"),
        })

    return {"stages": {}, "positions": {}, "pins": {}, "edges": edges, "link_partners": {}}
