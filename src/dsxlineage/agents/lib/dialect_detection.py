"""Determines which ETL dialect an uploaded export file belongs to.

Extension alone is enough for DataStage (.dsx) and SSIS (.dtsx) - both are
tool-specific extensions. Informatica PowerCenter exports use the generic
.xml extension, so those need a peek at the root tag to avoid silently
mis-parsing an unrelated XML upload as Informatica.
"""
import os
import xml.etree.ElementTree as ET


class UnsupportedDialectError(ValueError):
    pass


def detect_dialect(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".dsx":
        return "datastage"
    if ext == ".dtsx":
        return "ssis"
    if ext == ".xml":
        try:
            root_tag = None
            for _event, elem in ET.iterparse(file_path, events=("start",)):
                root_tag = elem.tag
                break
        except ET.ParseError as exc:
            raise UnsupportedDialectError(f"'{file_path}' is not valid XML: {exc}") from exc

        if root_tag == "POWERMART":
            return "informatica"
        raise UnsupportedDialectError(
            f"'{file_path}' is XML but its root element <{root_tag}> doesn't match any "
            "supported dialect (expected <POWERMART> for Informatica)."
        )

    raise UnsupportedDialectError(f"Unsupported file extension '{ext}' - expected .dsx, .dtsx, or .xml")
