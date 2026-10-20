"""ML pipeline parser: python training/scoring scripts → :class:`RoutineDef`.

The pipelines are genuine executable code, so they are parsed statically (never
executed): docstring ``Reads:``/``Writes:`` declarations, ``FEATURE_COLS`` lists
and ``MODEL_DIR / "<name>.pkl"`` artifact names. Model binaries themselves are
not vendored; the routine records which files the pipeline generates.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from dsxlineage.estate.ingest.sqlrefs import normalize_ref
from dsxlineage.estate.ir import RoutineDef

ML_PIPELINE_PATHS: tuple[tuple[str, str], ...] = (
    ("docker-deploy/ml/ml_pipeline.py", "ML.LOAN_APPROVAL_PIPELINE"),
    ("docker-deploy/ml/fraud_pipeline.py", "ML.FRAUD_DETECTION_PIPELINE"),
)

_IO_RE = re.compile(r"^\s*(Reads|Writes)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_REF_RE = re.compile(r"\b([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)\b")
_MODEL_FILE_RE = re.compile(r'MODEL_DIR\s*/\s*"([^"]+\.pkl)"')
_FEATURES_ASSIGN = "FEATURE_COLS"


def _feature_columns(text: str) -> list[str]:
    """Read a module-level ``FEATURE_COLS = [...]`` list literal."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if not targets:
            continue
        name = getattr(targets[0], "id", None)
        if name != _FEATURES_ASSIGN or not isinstance(node.value, ast.List):
            continue
        return [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    return []


def _io_refs(text: str) -> tuple[list[str], list[str]]:
    """Parse ``Reads:`` / ``Writes:`` docstring lines into normalised FQNs."""
    reads: list[str] = []
    writes: list[str] = []
    for kind, value in _IO_RE.findall(text):
        refs = [normalize_ref(match.group(0)) for match in _REF_RE.finditer(value)]
        bucket = reads if kind.lower() == "reads" else writes
        bucket.extend(ref for ref in refs if ref)
    return list(dict.fromkeys(reads)), list(dict.fromkeys(writes))


def parse_ml_pipelines(root: Path) -> list[RoutineDef]:
    """Parse every vendored ML pipeline into pipeline + model-asset routines."""
    root = Path(root)
    routines: list[RoutineDef] = []
    for rel, fqn in ML_PIPELINE_PATHS:
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        reads, writes = _io_refs(text)
        features = _feature_columns(text)
        model_files = list(dict.fromkeys(_MODEL_FILE_RE.findall(text)))
        routines.append(
            RoutineDef(
                fqn=fqn,
                kind="ml_pipeline",
                language="python",
                artifact=rel,
                description=next((line.strip() for line in text.splitlines() if line.strip() and not line.startswith('"""')), ""),
                reads=reads,
                writes=writes,
                features=features,
                model_files=model_files,
            )
        )
        for model_file in model_files:
            routines.append(
                RoutineDef(
                    fqn=f"ML.ASSET_{model_file.removesuffix('.pkl').upper()}",
                    kind="model_asset",
                    language="pickle",
                    artifact=rel,
                    description=f"Generated at train time by {fqn}",
                    model_files=[model_file],
                )
            )
    return routines
