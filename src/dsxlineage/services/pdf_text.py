"""Shared LLM-text-to-reportlab formatting, used by every generated PDF.

LLM output routinely contains markdown (**bold**, `code` identifiers) that
reportlab's Paragraph doesn't understand natively — it uses a small XML tag
set instead. This converts the common cases and escapes everything else so
stray '<'/'&' in generated text can't break the Paragraph XML parser.
"""
import re
import xml.sax.saxutils as saxutils

_LEADING_TITLE_RE = re.compile(r"^\s*\*\*[^\n*]+\*\*\s*\n+")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def markdown_to_reportlab(text: str | None, strip_leading_title: bool = True) -> str:
    if not text:
        return "Not available."
    if strip_leading_title:
        # Strip a leading **Title** line — it usually duplicates a heading we already render.
        text = _LEADING_TITLE_RE.sub("", text, count=1)
    escaped = saxutils.escape(text)
    escaped = _CODE_RE.sub(lambda m: f'<font face="Courier">{m.group(1)}</font>', escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    return escaped.replace("\n\n", "<br/><br/>").replace("\n", "<br/>")
