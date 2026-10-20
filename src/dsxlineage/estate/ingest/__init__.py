"""Ingestion adapters for external demo estates.

Each module parses one artifact family from a pinned repository snapshot and
maps it into :mod:`dsxlineage.estate.ir`. Nothing here executes estate code or
contacts the source database; artifacts are treated as read-only evidence.
"""

import logging

# T-SQL scripts contain PRINT/TRY-CATCH/batch syntax sqlglot cannot fully parse.
# Those warnings are expected for this estate; parsers fall back to regex.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

from dsxlineage.estate.ingest.snapshot import (
    DEFAULT_DEST,
    REPO_URL,
    snapshot_repository,
)

__all__ = ["DEFAULT_DEST", "REPO_URL", "snapshot_repository"]
