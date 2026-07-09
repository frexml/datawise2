"""Parser for SSIS (.dtsx) package exports.

Unlike DataStage's .dsx (a proprietary BEGIN/END text-block format), .dtsx
is genuine namespaced XML — this is a straightforward ElementTree walk, no
hand-rolled grammar needed.

Only "Microsoft.Pipeline" executables (Data Flow Tasks) are parsed down to
component/path/column detail, since that's where column-level lineage
lives. Other task types (Execute SQL, Script, etc.) and containers
(Sequence, ForLoop) are still surfaced — as opaque stages or recursed into
respectively — so a package with mixed task types doesn't fail to parse,
it just won't have lineage detail for the non-pipeline tasks.
"""
import xml.etree.ElementTree as ET

_DTS_NS = "www.microsoft.com/SqlServer/Dts"
_NS = {"DTS": _DTS_NS}


def _dts_attr(element, name, default=None):
    return element.get(f"{{{_DTS_NS}}}{name}", default)


def _parse_connection_managers(root) -> list[dict]:
    managers = []
    for cm in root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", _NS):
        obj_data = cm.find("DTS:ObjectData/DTS:ConnectionManager", _NS)
        managers.append({
            "ref_id": _dts_attr(cm, "refId"),
            "name": _dts_attr(cm, "ObjectName"),
            "creation_name": _dts_attr(cm, "CreationName"),
            "connection_string": _dts_attr(obj_data, "ConnectionString") if obj_data is not None else None,
        })
    return managers


def _parse_variables(root) -> list[dict]:
    variables = []
    for var in root.findall("DTS:Variables/DTS:Variable", _NS):
        value_el = var.find("DTS:VariableValue", _NS)
        variables.append({
            "name": _dts_attr(var, "Name"),
            "namespace": _dts_attr(var, "Namespace"),
            "value": (value_el.text or "").strip() if value_el is not None else None,
        })
    return variables


def _property_key(name, name1):
    return f"{name}.{name1}" if name1 else name


def _parse_component(component) -> dict:
    properties = {}
    for prop in component.findall("properties/property"):
        key = _property_key(prop.get("name"), prop.get("name1"))
        properties[key] = (prop.text or "").strip()

    connections = [
        {
            "name": conn.get("name"),
            "connection_manager_ref_id": conn.get("connectionManagerRefId"),
        }
        for conn in component.findall("connections/connection")
    ]

    inputs = []
    for inp in component.findall("inputs/input"):
        columns = [
            {"name": col.get("name"), "data_type": col.get("dataType"), "length": col.get("length")}
            for col in inp.findall("inputColumns/inputColumn")
        ]
        inputs.append({"ref_id": inp.get("refId"), "columns": columns})

    outputs = []
    for out in component.findall("outputs/output"):
        columns = [
            {"name": col.get("name"), "data_type": col.get("dataType"), "length": col.get("length")}
            for col in out.findall("outputColumns/outputColumn")
        ]
        outputs.append({"ref_id": out.get("refId"), "columns": columns})

    return {
        "ref_id": component.get("refId"),
        "component_class_id": component.get("componentClassID"),
        "name": component.get("name"),
        "description": component.get("description", ""),
        "properties": properties,
        "connections": connections,
        "inputs": inputs,
        "outputs": outputs,
    }


def _parse_pipeline(pipeline_el) -> dict:
    components = [_parse_component(c) for c in pipeline_el.findall("components/component")]
    paths = [
        {
            "ref_id": p.get("refId"),
            "name": (p.get("refId") or "").rsplit("\\", 1)[-1],
            "start_id": p.get("startId"),
            "end_id": p.get("endId"),
        }
        for p in pipeline_el.findall("paths/path")
    ]
    return {"components": components, "paths": paths}


def _parse_opaque_task(executable) -> dict:
    """Best-effort property capture for non-Pipeline executables (Execute
    SQL, Script Task, etc.) — no per-vendor schema assumed, just flatten
    whatever attributes the ObjectData subtree exposes."""
    properties = {}
    obj_data = executable.find("DTS:ObjectData", _NS)
    if obj_data is not None:
        for child in obj_data.iter():
            for attr_name, attr_value in child.attrib.items():
                local_name = attr_name.split("}")[-1]
                properties[local_name] = attr_value
    return properties


def _parse_executables(container, _NS=_NS) -> list[dict]:
    result = []
    for executable in container.findall("DTS:Executables/DTS:Executable", _NS):
        entry = {
            "ref_id": _dts_attr(executable, "refId"),
            "object_name": _dts_attr(executable, "ObjectName"),
            "creation_name": _dts_attr(executable, "CreationName"),
            "executable_type": _dts_attr(executable, "ExecutableType"),
        }

        pipeline_el = executable.find("DTS:ObjectData/pipeline", _NS)
        nested_executables = executable.findall("DTS:Executables/DTS:Executable", _NS)

        if pipeline_el is not None:
            entry.update(_parse_pipeline(pipeline_el))
        elif nested_executables:
            entry["executables"] = _parse_executables(executable, _NS)
        else:
            entry["properties"] = _parse_opaque_task(executable)

        result.append(entry)
    return result


def parse_dtsx(file_path: str) -> dict:
    """Parses an SSIS .dtsx package export into a raw Python dictionary."""
    tree = ET.parse(file_path)
    root = tree.getroot()

    return {
        "package_name": _dts_attr(root, "ObjectName", "UnnamedPackage"),
        "creation_date": _dts_attr(root, "CreationDate"),
        "connection_managers": _parse_connection_managers(root),
        "variables": _parse_variables(root),
        "executables": _parse_executables(root),
    }
