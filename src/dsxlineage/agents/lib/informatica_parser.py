"""Parser for Informatica PowerCenter repository exports (.xml).

Like SSIS's .dtsx, this is genuine XML - but PowerCenter exports use no XML
namespace at all (plain tags: POWERMART/REPOSITORY/FOLDER/...), unlike SSIS's
DTS-prefixed, namespaced format. A plain ElementTree walk is all this needs.

Column-level lineage here is richer than DataStage or SSIS out of the box:
Informatica's <CONNECTOR> elements are field-level edges between named
instances, not just stage-to-stage links - the analyzer groups them by
instance pair into stage-level links while keeping the field mapping detail.
"""
import xml.etree.ElementTree as ET


def _attrs(element, keys: dict) -> dict:
    """Pulls a fixed set of XML attributes into a lowercase-keyed dict."""
    return {out_key: element.get(xml_key) for out_key, xml_key in keys.items()}


def _parse_source(source_el) -> dict:
    fields = [
        _attrs(f, {
            "name": "NAME", "data_type": "DATATYPE", "precision": "PRECISION",
            "scale": "SCALE", "key_type": "KEYTYPE", "nullable": "NULLABLE",
        })
        for f in source_el.findall("SOURCEFIELD")
    ]
    return {
        **_attrs(source_el, {"name": "NAME", "database_type": "DATABASETYPE", "description": "DESCRIPTION"}),
        "fields": fields,
    }


def _parse_target(target_el) -> dict:
    fields = [
        _attrs(f, {
            "name": "NAME", "data_type": "DATATYPE", "precision": "PRECISION",
            "scale": "SCALE", "key_type": "KEYTYPE", "nullable": "NULLABLE",
        })
        for f in target_el.findall("TARGETFIELD")
    ]
    return {
        **_attrs(target_el, {"name": "NAME", "database_type": "DATABASETYPE", "description": "DESCRIPTION"}),
        "fields": fields,
    }


def _parse_transformation(trans_el) -> dict:
    fields = [
        _attrs(f, {
            "name": "NAME", "port_type": "PORTTYPE", "data_type": "DATATYPE",
            "expression": "EXPRESSION", "expression_type": "EXPRESSIONTYPE",
        })
        for f in trans_el.findall("TRANSFORMFIELD")
    ]
    table_attributes = {
        attr.get("NAME"): attr.get("VALUE") for attr in trans_el.findall("TABLEATTRIBUTE")
    }
    return {
        **_attrs(trans_el, {"name": "NAME", "type": "TYPE", "description": "DESCRIPTION"}),
        "fields": fields,
        "table_attributes": table_attributes,
    }


def _parse_mapping(mapping_el) -> dict:
    transformations = [_parse_transformation(t) for t in mapping_el.findall("TRANSFORMATION")]
    instances = [
        _attrs(i, {"name": "NAME", "transformation_type": "TRANSFORMATION_TYPE", "type": "TYPE"})
        for i in mapping_el.findall("INSTANCE")
    ]
    connectors = [
        _attrs(c, {
            "from_instance": "FROMINSTANCE", "from_instance_type": "FROMINSTANCETYPE", "from_field": "FROMFIELD",
            "to_instance": "TOINSTANCE", "to_instance_type": "TOINSTANCETYPE", "to_field": "TOFIELD",
        })
        for c in mapping_el.findall("CONNECTOR")
    ]
    return {
        **_attrs(mapping_el, {"name": "NAME", "description": "DESCRIPTION"}),
        "transformations": transformations,
        "instances": instances,
        "connectors": connectors,
    }


def parse_informatica_xml(file_path: str) -> dict:
    """Parses an Informatica PowerCenter repository export into a raw dict."""
    tree = ET.parse(file_path)
    root = tree.getroot()

    if root.tag != "POWERMART":
        raise ValueError(f"Not a recognized Informatica PowerCenter export (root tag was <{root.tag}>)")

    repository_el = root.find("REPOSITORY")
    folders = repository_el.findall("FOLDER") if repository_el is not None else []

    sources, targets, mappings = [], [], []
    for folder in folders:
        sources.extend(_parse_source(s) for s in folder.findall("SOURCE"))
        targets.extend(_parse_target(t) for t in folder.findall("TARGET"))
        mappings.extend(_parse_mapping(m) for m in folder.findall("MAPPING"))

    return {
        "repository_name": repository_el.get("NAME") if repository_el is not None else None,
        "folder_name": folders[0].get("NAME") if folders else None,
        "creation_date": root.get("CREATION_DATE"),
        "sources": sources,
        "targets": targets,
        "mappings": mappings,
    }
