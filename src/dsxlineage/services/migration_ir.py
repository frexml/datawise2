"""Deterministic extraction of a per-stage migration IR from a job's raw
.dsx export - DataStage only for now (see migration_scaffold.py's module
docstring for the SSIS/Informatica fallback).

Re-parses the raw file (via dsx_parser.parse_dsx + DSXAnalyzer, the same
pipeline the initial upload already runs - see agents/parser_agent.py and
agents/analyzer_agent.py) rather than reading the already-persisted
Stage.properties, because pin-level config (e.g. a PxSequentialFile's
actual file path) lives on CCustomInput/CCustomOutput pin records' nested
DSSUBRECORD trees, which the existing ingestion pipeline never flattens
into Stage.properties before persisting.

DataStage encodes repeatable multi-value stage properties (join/sort/dedup
key lists, connector file paths) in a compact internal format:
    \\(3)<prop-name>\\(2)<value>\\(2)0
repeated and separated by \\(1), with a nested sub-property (e.g. a sort
key's asc/desc direction) as a doubled \\(3)\\(3)<name>\\(2)<value>\\(2)0
immediately following the key entry it describes.
_decode_ds_property_list decodes this into an ordered list of (name, value)
pairs; callers reconstruct the specific shape they need (a flat key list,
or key+direction pairs) from that - verified against real job data:
    join key="\\(2)\\(2)0\\(1)\\(3)key\\(2)MTW_TRADENUM\\(2)0\\(1)\\(3)key\\(2)CTL_PORTFOLIO\\(2)0"
    sort key="...\\(3)key\\(2)MTW_TRADENUM\\(2)0\\(1)\\(3)\\(3)asc\\\\desc\\(2)asc\\(2)0..."
    file path="\\(2)\\(2)0\\(1)\\(3)file \\(2)#DwhTresDirectories.DirSrc#/#$PARAMSRCFILE#\\(2)0"
"""
import re
from dataclasses import dataclass, field

from dsxlineage.agents.lib.detailed_analyzer import DSXAnalyzer
from dsxlineage.agents.lib.dsx_parser import parse_dsx


@dataclass
class StageIR:
    identifier: str
    name: str
    stage_type: str
    derivations: list[dict] = field(default_factory=list)  # [{"output_link", "output_column", "expression"}]
    join_keys: list[str] = field(default_factory=list)
    join_type: str | None = None  # DataStage operator name, e.g. "leftouterjoin"
    sort_keys: list[tuple[str, str]] = field(default_factory=list)  # (column, "asc"|"desc")
    dedup_keys: list[str] = field(default_factory=list)
    dedup_keep: str | None = None  # "first" | "last"
    connector_table: str | None = None
    before_sql: str | None = None
    after_sql: str | None = None
    file_path: str | None = None


def _decode_ds_property_list(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    pairs = []
    for segment in raw.split("\\(3)"):
        if "\\(2)" not in segment:
            continue
        name, _, rest = segment.partition("\\(2)")
        name = name.strip()
        if not name:
            continue
        value = rest.split("\\(2)0", 1)[0]
        pairs.append((name, value))
    return pairs


def _extract_keys(raw: str | None) -> list[str]:
    return [value for name, value in _decode_ds_property_list(raw) if name == "key"]


def _extract_sort_keys(raw: str | None) -> list[tuple[str, str]]:
    result = []
    current_col = None
    for name, value in _decode_ds_property_list(raw):
        if name == "key":
            current_col = value
        # Backslash count between "asc" and "desc" in this property name has
        # been observed as both 1 and 2 literal backslashes depending on
        # export - match loosely rather than pin to an exact count.
        elif name.startswith("asc") and "desc" in name and current_col:
            result.append((current_col, value))
            current_col = None
    return result


def _extract_file_path(raw: str | None) -> str | None:
    for name, value in _decode_ds_property_list(raw):
        if name == "file":
            return value
    return None


_CDATA_FIELD_RE = {
    "table": re.compile(r"<TableName[^>]*><!\[CDATA\[(.*?)\]\]>", re.DOTALL),
    "before_sql": re.compile(r"<BeforeSQL[^>]*><!\[CDATA\[(.*?)\]\]>", re.DOTALL),
    "after_sql": re.compile(r"<AfterSQL[^>]*><!\[CDATA\[(.*?)\]\]>", re.DOTALL),
}


def _extract_xml_properties(xml_props: str | None) -> dict[str, str]:
    """Best-effort - verified against Oracle connector exports; other PX
    connector variants (DB2, Teradata, ODBC, ...) share a similar <Usage>
    XML schema in DataStage 8.x+ but haven't been verified against real
    samples of those specifically."""
    if not xml_props:
        return {}
    out = {}
    for key, pattern in _CDATA_FIELD_RE.items():
        m = pattern.search(xml_props)
        if m:
            out[key] = m.group(1).strip()
    return out


_TRX_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*(.+?);\s*$", re.MULTILINE)


def _extract_derivations(trx_gen_code: str | None) -> list[dict]:
    """TrxGenCode is DataStage's generated pseudo-C++ for a Transformer
    stage - matches `OutputLink.Column = expression;` lines from its
    mainloop{} block. Naturally excludes local-variable declarations/
    assignments (no dot) and comments."""
    if not trx_gen_code:
        return []
    return [
        {"output_link": link, "output_column": col, "expression": expr.strip()}
        for link, col, expr in _TRX_ASSIGNMENT_RE.findall(trx_gen_code)
    ]


def _flatten_config_properties(record: dict) -> dict[str, str]:
    """DataStage nests most of a record's real config - a stage's own
    "key"/"operator"/"keep"/"TrxGenCode"/"XMLProperties", or a pin's
    "file " path - as DSSUBRECORD children with a Name/Value pair, not as
    top-level scalar properties on the DSRECORD itself (confirmed by
    re-parsing real job data: e.g. a PxJoin stage's top-level dict has no
    "key"/"operator" keys at all, but its DSSUBRECORD list has
    {"Name": "operator", "Value": "leftouterjoin"} and
    {"Name": "key", "Value": "..."} entries). This applies uniformly to
    stage records and pin records alike.

    Column-definition DSSUBRECORDs (schema info on a pin) are also
    DSSUBRECORD children but have no top-level "Value" key - they carry
    SqlType/Precision/... instead - so the "Value" in sub check here
    naturally excludes them."""
    return {
        sub["Name"].strip(): sub.get("Value")
        for sub in record.get("DSSUBRECORD", [])
        if isinstance(sub, dict) and sub.get("Name") and "Value" in sub
    }


def extract_datastage_ir(raw_dsx_path: str) -> dict[str, StageIR]:
    """{stage_name: StageIR} for every stage record in the given raw .dsx -
    includes orchestration activities too; migration_scaffold.py is
    responsible for filtering those out, same as it already does."""
    parsed = parse_dsx(raw_dsx_path)
    analyzer = DSXAnalyzer(data=parsed)
    analyzer.load()
    analyzer.analyze()

    stages_raw = analyzer.stages  # {identifier: DSRECORD dict}
    pins_raw = analyzer.links     # {identifier: DSRECORD dict} - CCustomInput/Output, CTrxInput/Output

    ir_by_name: dict[str, StageIR] = {}
    for identifier, stage in stages_raw.items():
        name = stage.get("Name")
        if not name:
            continue
        stage_type = stage.get("StageType") or stage.get("OLEType") or ""
        ir = StageIR(identifier=identifier, name=name, stage_type=stage_type)

        config = _flatten_config_properties(stage)

        ir.derivations = _extract_derivations(config.get("TrxGenCode"))

        type_lower = stage_type.lower()
        key_list = _extract_keys(config.get("key"))
        if "join" in type_lower:
            ir.join_keys = key_list
            ir.join_type = config.get("operator")
        elif "sort" in type_lower:
            ir.sort_keys = _extract_sort_keys(config.get("key"))
        elif "remdup" in type_lower or "dedup" in type_lower:
            ir.dedup_keys = key_list
            ir.dedup_keep = config.get("keep")

        xml_props = _extract_xml_properties(config.get("XMLProperties"))
        ir.connector_table = xml_props.get("table")
        ir.before_sql = xml_props.get("before_sql")
        ir.after_sql = xml_props.get("after_sql")

        pin_ids = []
        for pins_field in ("OutputPins", "InputPins"):
            value = stage.get(pins_field)
            if value:
                pin_ids.extend(value.split("|"))
        for pin_id in pin_ids:
            pin = pins_raw.get(pin_id)
            if not pin:
                continue
            pin_config = _flatten_config_properties(pin)
            path = _extract_file_path(pin_config.get("file"))
            if path:
                ir.file_path = path
                break

        ir_by_name[name] = ir

    return ir_by_name
