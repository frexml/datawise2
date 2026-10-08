"""Converts the raw Informatica parse tree (informatica_parser output) into
the same {job_properties, components: {stages, links, annotations,
containers, others}} shape DataStage's DSXAnalyzer and SSIS's analyze_ssis
produce, plus the same {edges, ...} lineage shape — so every downstream
agent works unchanged regardless of source dialect.

Informatica's CONNECTOR elements are field-level edges, richer than
DataStage/SSIS's stage-to-stage links — grouped here by (from_instance,
to_instance) pair into one link per stage-pair, with the individual field
mappings preserved as link properties.
"""
from typing import Any


def analyze_informatica(parsed_data: dict) -> dict:
    stages: dict[str, Any] = {}
    links: dict[str, Any] = {}
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return f"S{seq:03d}"

    sources_by_name = {s["name"]: s for s in parsed_data.get("sources", [])}
    targets_by_name = {t["name"]: t for t in parsed_data.get("targets", [])}

    for mapping in parsed_data.get("mappings", []):
        transformations_by_name = {t["name"]: t for t in mapping.get("transformations", [])}
        instance_to_stage_id: dict[str, str] = {}

        for instance in mapping.get("instances", []):
            name = instance["name"]
            sid = next_id()
            instance_to_stage_id[name] = sid

            if instance["type"] == "SOURCE":
                source = sources_by_name.get(name, {})
                stages[sid] = {
                    "Identifier": sid,
                    "Name": name,
                    "StageType": "Source Definition",
                    "OLEType": "Source Definition",
                    "Description": source.get("description", ""),
                    "Properties": {"DatabaseType": source.get("database_type")},
                    "Fields": source.get("fields", []),
                }
            elif instance["type"] == "TARGET":
                target = targets_by_name.get(name, {})
                stages[sid] = {
                    "Identifier": sid,
                    "Name": name,
                    "StageType": "Target Definition",
                    "OLEType": "Target Definition",
                    "Description": target.get("description", ""),
                    "Properties": {"DatabaseType": target.get("database_type")},
                    "Fields": target.get("fields", []),
                }
            else:  # TRANSFORMATION
                trans = transformations_by_name.get(name, {})
                stages[sid] = {
                    "Identifier": sid,
                    "Name": name,
                    "StageType": trans.get("type", "Transformation"),
                    "OLEType": trans.get("type", "Transformation"),
                    "Description": trans.get("description", ""),
                    "Properties": trans.get("table_attributes", {}),
                    "Fields": trans.get("fields", []),
                }

        # Group field-level connectors into stage-to-stage links.
        grouped: dict[tuple, list] = {}
        for conn in mapping.get("connectors", []):
            key = (conn["from_instance"], conn["to_instance"])
            grouped.setdefault(key, []).append({
                "from_field": conn["from_field"],
                "to_field": conn["to_field"],
            })

        for (from_instance, to_instance), field_mappings in grouped.items():
            lid = next_id()
            links[lid] = {
                "Identifier": lid,
                "Name": f"{from_instance} -> {to_instance}",
                "OLEType": "InformaticaConnector",
                "FieldMappings": field_mappings,
                "_source_stage_id": instance_to_stage_id.get(from_instance),
                "_target_stage_id": instance_to_stage_id.get(to_instance),
            }

    job_name = None
    if parsed_data.get("mappings"):
        job_name = parsed_data["mappings"][0].get("name")
    job_name = job_name or parsed_data.get("folder_name") or "Unnamed Informatica Job"

    return {
        "job_properties": {"Name": job_name},
        "components": {
            "stages": stages,
            "links": links,
            "annotations": {},
            "containers": {},
            "others": [],
        },
    }


def extract_informatica_lineage(analysis_result: dict) -> dict:
    """Informatica equivalent of partner_extractor/ssis_analyzer's lineage
    extraction — links already carry resolved stage ids, just need stage
    names for the edges list worker.py expects."""
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
            "source_pin": link.get("_source_stage_id"),
            "target": target_stage.get("Name", "Unknown"),
            "target_pin": link.get("_target_stage_id"),
        })

    return {"stages": {}, "positions": {}, "pins": {}, "edges": edges, "link_partners": {}}
