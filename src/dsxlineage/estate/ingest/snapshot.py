"""Vendor a pinned, secret-scrubbed snapshot of the external estate repo.

The demo estate is the DataWise Platform repository maintained by the data
engineering team. Survey runs must be deterministic, offline-capable and
provenance-traceable, so we vendor the parseable subset of the repository at a
fixed commit rather than cloning at request time.

Scope (approved decisions 2 and 4):

* ``git_upload_etls/`` is the canonical ETL package (DSX, POWERMART, DTSX,
  Talend, T-SQL DDL, Control-M manifest).
* ``docker-deploy/`` duplicates are excluded except runtime summaries and the
  orchestration/runner scripts, which feed the Behaviour engine.
* Binary artifacts (``.pbix``, ``.ispac``, ``.pkl``, ``.database``) are recorded
  as excluded metadata; they are surfaced as unknown facts, never parsed.
* Secret material is redacted before anything is written to disk: SSIS
  ``DTS:Password`` blobs and the Talend encrypted password context value.

Usage::

    uv run python -m dsxlineage.estate.ingest.snapshot
    uv run python -m dsxlineage.estate.ingest.snapshot --local /path/to/clone
    uv run python -m dsxlineage.estate.ingest.snapshot --refresh --dest data/external_estates/datawise_platform
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/Kunal-dataengineer/Datawise-Datamodel.git"
DEFAULT_DEST = Path("data/external_estates/datawise_platform")
CANONICAL_ETL_DIR = "git_upload_etls"

_CANONICAL_FILES: tuple[str, ...] = (
    "README.md",
    "docker-compose.yml",
    "jobs/DATAWISE_PLATFORM.json",
    "api/main.py",
    "docker-deploy/controlm/controlm_orchestrator.py",
    "docker-deploy/datastage/datastage_runner.py",
    "docker-deploy/informatica/informatica_runner.py",
    "docker-deploy/talend/talend_runner.py",
    "docker-deploy/ml/ml_runner.py",
    "docker-deploy/ml/ml_pipeline.py",
    "docker-deploy/ml/fraud_pipeline.py",
    "docker-deploy/datastage/run_summary.json",
    "docker-deploy/informatica/run_summary.json",
    "artifacts/bi/FINANCE_DASHBOARD_DAX.md",
    "data/loan_db/LOAN_DB_TEST.csv",
    "data/retail_db/RETAIL_DB_TEST.csv",
)

_BINARY_SUFFIXES = frozenset({".pbix", ".ispac", ".pkl", ".database", ".slnx"})

# Test fixtures that carry PII rows; vendored header-only (schema cross-check
# material, never row data) per the approved security gate.
_HEADER_ONLY_FILES: frozenset[str] = frozenset(
    {"data/loan_db/LOAN_DB_TEST.csv", "data/retail_db/RETAIL_DB_TEST.csv"}
)

# Text artifacts are rewritten through these patterns before they hit disk.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r'(<(?P<tag>[\w:]+)(?=[^>]*(?:Sensitive|Encrypted)="1")[^>]*>).*?(</(?P=tag)>)',
            re.DOTALL,
        ),
        r"\1***REDACTED***\3",
    ),
    (
        re.compile(r"(<DTS:Password\b[^>]*>).*?(</DTS:Password>)", re.DOTALL),
        r"\1***REDACTED***\2",
    ),
    (
        re.compile(r'(name="db_password"[^>]*?value=")[^"]*(")'),
        r"\1***REDACTED***\2",
    ),
    (
        re.compile(r"(?i)(\bpassword\s*=\s*)([\"'])(?:(?!\2).)+(\2)"),
        r"\1\2***REDACTED***\3",
    ),
)


def _redact(text: str) -> tuple[str, int]:
    """Redact secret material from one artifact.

    Returns the scrubbed text and the number of substitutions applied.
    """
    count = 0
    for pattern, replacement in _REDACTION_PATTERNS:
        text, hits = pattern.subn(replacement, text)
        count += hits
    return text, count


def _git_commit(repo: Path) -> str:
    """Return the HEAD commit SHA for a local git checkout."""
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _clone(repo_url: str) -> tuple[Path, str]:
    """Shallow-clone ``repo_url`` into a temporary directory."""
    tmp = Path(tempfile.mkdtemp(prefix="datawise_datamodel_"))
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(tmp)],
        capture_output=True,
        text=True,
        check=True,
    )
    return tmp, _git_commit(tmp)


def _iter_snapshot_files(source: Path) -> tuple[list[Path], list[dict[str, str]]]:
    """Select the parseable subset and record what was intentionally excluded."""
    selected: list[Path] = []
    excluded: list[dict[str, str]] = []

    etl_root = source / CANONICAL_ETL_DIR
    for path in sorted(etl_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if path.suffix.lower() in _BINARY_SUFFIXES:
            excluded.append({"path": str(rel), "reason": "binary artifact"})
            continue
        selected.append(path)

    for rel in _CANONICAL_FILES:
        path = source / rel
        if path.is_file():
            selected.append(path)
        else:
            logger.warning("canonical file missing from snapshot: %s", rel)

    for rel in ("docker-deploy/ddl", "docker-deploy/datastage/files", "docker-deploy/informatica/files", "artifacts/ssis", "graphical"):
        if (source / rel).exists():
            excluded.append({"path": rel, "reason": "duplicate or non-lineage artifact"})

    for rel in ("data/loan_db/LOAN_DB_RAW.csv", "data/retail_db/RETAIL_DB_RAW.csv", ".env.example"):
        if (source / rel).exists():
            excluded.append({"path": rel, "reason": "row-level PII / credential template"})

    return selected, excluded


def snapshot_repository(
    dest: Path = DEFAULT_DEST,
    *,
    repo_url: str = REPO_URL,
    local_path: Path | None = None,
    refresh: bool = False,
) -> dict[str, object]:
    """Create a pinned, redacted snapshot of the external estate repository.

    Args:
        dest: Destination directory for the vendored snapshot.
        repo_url: Repository to clone when ``local_path`` is not supplied.
        local_path: Existing checkout to copy from (offline/CI use).
        refresh: Replace an existing snapshot instead of returning early.

    Returns:
        The provenance manifest written to ``PROVENANCE.json``.
    """
    dest = Path(dest)
    provenance_path = dest / "PROVENANCE.json"
    if provenance_path.exists() and not refresh:
        logger.info("snapshot already present at %s (use --refresh to replace)", dest)
        return json.loads(provenance_path.read_text(encoding="utf-8"))

    tmp_clone: Path | None = None
    if local_path is not None:
        source = Path(local_path)
        if not source.is_dir():
            raise FileNotFoundError(f"local repository not found: {source}")
        commit = _git_commit(source)
    else:
        tmp_clone, commit = _clone(repo_url)
        source = tmp_clone

    try:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        selected, excluded = _iter_snapshot_files(source)
        redactions = 0
        byte_count = 0
        header_only: list[str] = []
        for path in selected:
            rel = path.relative_to(source)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                excluded.append({"path": str(rel), "reason": "non-text artifact"})
                continue
            text, hits = _redact(text)
            redactions += hits
            if str(rel) in _HEADER_ONLY_FILES:
                first_line = text.splitlines()[0] if text else ""
                text = first_line + "\n" if first_line else ""
                header_only.append(str(rel))
            payload = text.encode("utf-8")
            target.write_bytes(payload)
            byte_count += len(payload)

        provenance: dict[str, object] = {
            "repo_url": repo_url,
            "commit_sha": commit,
            "snapshot_at": date.today().isoformat(),
            "snapshot_utc": datetime.now(timezone.utc).isoformat(),
            "canonical_root": CANONICAL_ETL_DIR,
            "file_count": len(selected),
            "bytes": byte_count,
            "redactions": redactions,
            "header_only": sorted(header_only),
            "excluded": sorted(excluded, key=lambda item: item["path"]),
        }
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        logger.info(
            "snapshot complete: %s files, %s redactions, commit %s",
            provenance["file_count"],
            redactions,
            commit[:12],
        )
        return provenance
    finally:
        if tmp_clone is not None:
            shutil.rmtree(tmp_clone, ignore_errors=True)


def main() -> None:
    """CLI entry point for vendoring or refreshing the snapshot."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO_URL, help="repository URL to clone")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="snapshot destination")
    parser.add_argument("--local", type=Path, default=None, help="copy from an existing checkout")
    parser.add_argument("--refresh", action="store_true", help="replace an existing snapshot")
    args = parser.parse_args()
    snapshot_repository(args.dest, repo_url=args.repo, local_path=args.local, refresh=args.refresh)


if __name__ == "__main__":
    main()
