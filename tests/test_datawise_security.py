"""Security gates for the vendored DataWise snapshot.

The approved plan (docs/external-estate-plan.md) requires that no secret
material or row-level PII is committed to the repository. These tests are the
CI enforcement point: they scan every vendored text artifact, verify the
redaction pass is effective, and prove the snapshot writer never persists
fixture rows.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from dsxlineage.estate.ingest.snapshot import (
    _HEADER_ONLY_FILES,
    _redact,
    snapshot_repository,
)

SNAPSHOT = Path("data/external_estates/datawise_platform")

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)AccountKey=[A-Za-z0-9+/=]{20,}"),
    re.compile(r"(?i)SharedAccessSignature=[A-Za-z0-9%+/=]{20,}"),
    re.compile(r"(?i)\b(password|pwd|passwd)\s*[=:]\s*[\"'][^\"'\n]{6,}[\"']"),
    re.compile(r"(?im)^\s*(password|pwd|passwd)\s*=\s*[A-Za-z0-9_\-]{6,}\s*$"),
    re.compile(r"\w+://[^/\s:@]+:[^@\s]{4,}@"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{16,}"),
)

_PLACEHOLDER_VALUES = {
    "***redacted***",
    "your_password_here",
    "changeme",
    "password",
    "***",
    "<password>",
    "${password}",
    "${db_password}",
    "%password%",
    "$password",
}


def _vendored_text_files() -> list[Path]:
    return [
        path
        for path in sorted(SNAPSHOT.rglob("*"))
        if path.is_file()
        and path.suffix.lower() not in {".pbix", ".ispac", ".pkl", ".database", ".slnx"}
        and path.name != "LIVE_DW_TABLES.json"
    ]


def test_redaction_scrubs_known_secret_shapes():
    samples = [
        ('<DTS:Password DTS:Name="Password" Encrypted="1">c2VjcmV0</DTS:Password>',
         "***REDACTED***", 1),
        ('<Configuration name="db_password" value="hunter2" />',
         "***REDACTED***", 1),
        ('password="supersecret"', "***REDACTED***", 1),
    ]
    for raw, expected, minimum in samples:
        scrubbed, hits = _redact(raw)
        assert expected in scrubbed
        assert hits >= minimum
        assert "supersecret" not in scrubbed
        assert "hunter2" not in scrubbed
        assert "c2VjcmV0" not in scrubbed


def test_vendored_snapshot_contains_no_unredacted_passwords():
    offenders: list[str] = []
    for path in _vendored_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"(?is)<DTS:Password\b[^>]*>(.*?)</DTS:Password>", text):
            value = match.group(1).strip()
            if value and value != "***REDACTED***":
                offenders.append(f"{path}: unredacted DTS:Password")
        for pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if pattern.groups and match.lastindex:
                    value = None
                    if "password" in pattern.pattern.lower():
                        assigned = re.split(r"[=:]", match.group(0).strip(), maxsplit=1)
                        value = assigned[1].strip("\"'") if len(assigned) > 1 else ""
                    if value is not None and value.lower() in _PLACEHOLDER_VALUES:
                        continue
                offenders.append(f"{path}: matched {pattern.pattern}")
    assert not offenders, "\n".join(sorted(set(offenders)))


def test_no_credential_template_or_raw_pii_files_vendored():
    assert not (SNAPSHOT / ".env").exists()
    assert not (SNAPSHOT / ".env.example").exists()
    assert not (SNAPSHOT / "data/loan_db/LOAN_DB_RAW.csv").exists()
    assert not (SNAPSHOT / "data/retail_db/RETAIL_DB_RAW.csv").exists()


def test_provenance_records_redaction_and_header_only_files():
    provenance = json.loads((SNAPSHOT / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["redactions"] >= 20
    assert set(provenance["header_only"]) == set(_HEADER_ONLY_FILES)
    excluded = {entry["path"] for entry in provenance["excluded"]}
    assert {".env.example", "data/loan_db/LOAN_DB_RAW.csv", "data/retail_db/RETAIL_DB_RAW.csv"} <= excluded


def test_fixture_csvs_have_no_row_data():
    for rel in _HEADER_ONLY_FILES:
        lines = (SNAPSHOT / rel).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert len(lines[0].split(",")) > 10


def test_snapshot_writer_strips_rows_and_inline_secrets(tmp_path):
    repo = tmp_path / "repo"
    etl = repo / "git_upload_etls" / "ssis"
    etl.mkdir(parents=True)
    (etl / "PACKAGE.dtsx").write_text(
        '<DTS:Password DTS:Name="Password" Encrypted="1">c2VjcmV0</DTS:Password>',
        encoding="utf-8",
    )
    fixture = repo / "data" / "loan_db" / "LOAN_DB_TEST.csv"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        "LoanID,CustomerName,NationalID\n1,Jane Doe,123-45-6789\n2,John Roe,987-65-4321\n",
        encoding="utf-8",
    )
    (repo / ".env.example").write_text("DB_PASSWORD=hunter2\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)

    dest = tmp_path / "snapshot"
    provenance = snapshot_repository(dest=dest, local_path=repo)

    dtsx = (dest / "git_upload_etls" / "ssis" / "PACKAGE.dtsx").read_text(encoding="utf-8")
    assert "***REDACTED***" in dtsx
    assert "c2VjcmV0" not in dtsx

    rows = (dest / "data" / "loan_db" / "LOAN_DB_TEST.csv").read_text(encoding="utf-8")
    assert rows.splitlines() == ["LoanID,CustomerName,NationalID"]
    assert "Jane Doe" not in rows

    assert not (dest / ".env.example").exists()
    assert provenance["header_only"] == ["data/loan_db/LOAN_DB_TEST.csv"]
    assert provenance["redactions"] >= 1
    excluded = {entry["path"] for entry in provenance["excluded"]}
    assert ".env.example" in excluded
