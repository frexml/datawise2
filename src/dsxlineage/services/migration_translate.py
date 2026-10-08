"""Per-expression translation of DataStage Transformer derivations into
PySpark - the piece migration_ir.py deliberately doesn't do, since it's
pure deterministic parsing and expression translation genuinely needs
judgment calls.

Two tiers, in order:
1. Deterministic fast path (_translate_deterministic) - bare `Link.Column`
   references, literals, and a handful of DataStage keywords/builtins are
   unambiguous; translating them with an LLM would be strictly worse (slower,
   costs money, and introduces a chance of getting an unambiguous thing
   wrong). Verified against real job data: ~90% of a Transformer stage's
   derivations are exactly this simple.
2. LLM translation, scoped to ONE stage's *remaining* unresolved expressions
   per call (never a whole job) - structured output only, so every result
   is either a usable PySpark snippet or an explicit low-confidence flag,
   never free prose. This is the one piece of this feature that's
   inherently non-deterministic; keeping its blast radius to "one
   expression's worth of translation" (not "summarize this stage" or
   "write this stage's code") is what keeps it checkable.
"""
import re
from datetime import date

from pydantic import BaseModel, Field

_MAX_TOKENS = 4000

_LITERAL_RE = re.compile(r"^(-?\d+(\.\d+)?|'[^']*'|\"[^\"]*\")$")
_DIRECT_REF_RE = re.compile(r"^[A-Za-z_]\w*\.([A-Za-z_]\w*)$")
_KEYWORD_MAP = {"@true": "True", "@false": "False", "@null": "None"}


def translate_deterministic(expression: str) -> str | None:
    """Returns a PySpark expression string, or None if this needs the LLM
    tier (or should just be flagged unresolved)."""
    expr = expression.strip()

    m = _DIRECT_REF_RE.match(expr)
    if m:
        return f'F.col("{m.group(1)}")'

    if _LITERAL_RE.match(expr):
        return f"F.lit({expr})"

    if expr.lower() in _KEYWORD_MAP:
        return f"F.lit({_KEYWORD_MAP[expr.lower()]})"

    if expr.lower() == "set_null()":
        return "F.lit(None)"

    return None


class ExpressionTranslation(BaseModel):
    output_column: str
    expression: str = Field(
        ..., description="Echo back ONLY the original DataStage expression (the part after "
        "' = '), exactly as given, with no output column name or ' = ' prefix attached - "
        "used to match this result back to its source, since the same output_column can "
        "appear more than once with different expressions."
    )
    pyspark_expression: str = Field(
        ...,
        description="A single PySpark expression producing this column's value, "
        "referencing `F` (pyspark.sql.functions) and column names via F.col(...). "
        "Must be a real, syntactically valid expression, not prose.",
    )
    confident: bool = Field(
        ..., description="False if you are not confident this is a correct translation."
    )
    note: str | None = Field(
        None, description="Required if confident=False - what's uncertain, or what "
        "DataStage-specific behavior you couldn't verify without the original job's runtime context."
    )


class StageExpressionTranslations(BaseModel):
    translations: list[ExpressionTranslation]


_SYSTEM_PROMPT = (
    "You are translating IBM DataStage Transformer-stage column derivations "
    "into PySpark expressions. DataStage's expression language is BASIC-like: "
    f"functions such as substring_1(str, start, len), trim_leading_trailing(str), "
    "date_from_string/string_from_date/timestamp_from_string for date handling, "
    "@TRUE/@FALSE/@NULL literals, and bare identifiers that may reference a "
    "stage-local variable declared elsewhere in the job (not a column).\n"
    "CRITICAL - column references: an expression like `Link.Column` (e.g. "
    "`TransactionIn.MTW_BACKDATEFLAG`) means \"column MTW_BACKDATEFLAG on "
    "upstream link TransactionIn\". Translate it as F.col(\"MTW_BACKDATEFLAG\") - "
    "the column name ONLY, never F.col(\"TransactionIn.MTW_BACKDATEFLAG\") "
    "(that would look up a literal column named with a dot in it, which is wrong).\n"
    "CRITICAL - bare identifiers: a bare identifier with NO dot in it (e.g. "
    "InterVar0_3, NullSetVar0) is a DataStage stage-local variable, not a "
    "column - it holds a value computed elsewhere in the Transformer's BASIC "
    "code that you cannot see. You MUST set confident=False for any expression "
    "that references a bare, dot-less identifier, even if you also produce a "
    "best-guess pyspark_expression. Do not mark these confident=True.\n"
    f"Today's date is {date.today().isoformat()}. "
    "Translate ONLY what's given - do not invent columns or business logic that "
    "isn't in the expression itself. If uncertain, say so via confident=False; "
    "do not silently produce a plausible-looking but unverified translation."
)


async def translate_stage_expressions(
    llm, stage_name: str, unresolved: list[dict]
) -> list[ExpressionTranslation]:
    """unresolved: [{"output_column": ..., "expression": ...}, ...] - already
    filtered to exclude anything translate_deterministic() handled. llm is a
    ChatOpenAI instance already bound with .with_structured_output(StageExpressionTranslations)."""
    if not unresolved:
        return []

    from langchain_core.messages import HumanMessage, SystemMessage

    human = HumanMessage(content=(
        f"Stage: {stage_name}\n\n"
        "Translate each of these DataStage derivations into a PySpark expression. "
        "Echo the exact expression text back in the `expression` field of your response "
        "for each one, unmodified:\n\n"
        + "\n".join(f"- {u['output_column']} = {u['expression']}" for u in unresolved)
    ))
    result = await llm.ainvoke([SystemMessage(content=_SYSTEM_PROMPT), human])
    return [_strip_echoed_column_prefix(t) for t in result.translations]


def _strip_echoed_column_prefix(t: "ExpressionTranslation") -> "ExpressionTranslation":
    """Defensive normalization: models sometimes echo "COLUMN = expr" in the
    `expression` field despite being told to return just `expr`. Strip that
    prefix if present so callers can match on the raw expression reliably."""
    prefix = f"{t.output_column} ="
    if t.expression.startswith(prefix):
        return t.model_copy(update={"expression": t.expression[len(prefix):].strip()})
    return t
