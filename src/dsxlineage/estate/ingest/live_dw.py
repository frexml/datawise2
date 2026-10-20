"""Query the deployed Azure SQL warehouse for final table row counts.

The external estate ships DDL, but the deployed database is the ground truth for
which tables actually exist and carry data after the pipeline runs. The data
engineering team supplied this query; we execute it read-only and persist the
result as a snapshot fixture so surveys stay deterministic and offline.

Credentials come from the environment (``SQL_SERVER``, ``SQL_DATABASE``,
``SQL_USERNAME``, ``SQL_PASSWORD``) and are never logged or written to disk.

Usage::

    uv run python -m dsxlineage.estate.ingest.live_dw
    uv run python -m dsxlineage.estate.ingest.live_dw --out data/external_estates/datawise_platform/LIVE_DW_TABLES.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_OUT = Path("data/external_estates/datawise_platform/LIVE_DW_TABLES.json")

FINAL_DW_TABLES_QUERY = """
SELECT
    t.TABLE_SCHEMA,
    t.TABLE_NAME,
    OBJECT_NAME(OBJECT_ID(t.TABLE_SCHEMA + '.' + t.TABLE_NAME)) AS tbl,
    SUM(p.rows) AS row_count
FROM INFORMATION_SCHEMA.TABLES t
JOIN sys.partitions p
  ON p.object_id = OBJECT_ID(t.TABLE_SCHEMA + '.' + t.TABLE_NAME)
 AND p.index_id IN (0, 1)
WHERE t.TABLE_TYPE = 'BASE TABLE'
GROUP BY t.TABLE_SCHEMA, t.TABLE_NAME
HAVING SUM(p.rows) > 0
ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME
"""


def _connection_settings() -> dict[str, str]:
    """Read SQL connection settings from the environment, failing fast."""
    required = ("SQL_SERVER", "SQL_DATABASE", "SQL_USERNAME", "SQL_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing SQL environment variables: {', '.join(missing)}")
    return {
        "server": os.environ["SQL_SERVER"],
        "database": os.environ["SQL_DATABASE"],
        "user": os.environ["SQL_USERNAME"],
        "password": os.environ["SQL_PASSWORD"],
    }


def fetch_dw_tables(timeout: int = 30) -> list[dict[str, object]]:
    """Execute the final-table inventory query against Azure SQL.

    Args:
        timeout: Connection/login timeout in seconds.

    Returns:
        Rows with ``table_schema``, ``table_name`` and ``row_count`` keys.
    """
    import pymssql

    settings = _connection_settings()
    logger.info("querying %s/%s as %s", settings["server"], settings["database"], settings["user"])
    try:
        connection = pymssql.connect(
            server=settings["server"],
            database=settings["database"],
            user=settings["user"],
            password=settings["password"],
            login_timeout=timeout,
            timeout=timeout,
            as_dict=True,
            encryption="require",
            tds_version="7.4",
        )
    except TypeError:  # older pymssql without the encryption kwarg
        connection = pymssql.connect(
            server=settings["server"],
            database=settings["database"],
            user=settings["user"],
            password=settings["password"],
            login_timeout=timeout,
            timeout=timeout,
            as_dict=True,
        )

    try:
        with connection.cursor() as cursor:
            cursor.execute(FINAL_DW_TABLES_QUERY)
            rows = cursor.fetchall()
    finally:
        connection.close()

    return [
        {
            "table_schema": str(row["TABLE_SCHEMA"]).upper(),
            "table_name": str(row["TABLE_NAME"]).upper(),
            "row_count": int(row["row_count"]),
        }
        for row in rows
    ]


def save_inventory(rows: list[dict[str, object]], out: Path = DEFAULT_OUT) -> dict[str, object]:
    """Write the live inventory with provenance next to the vendored snapshot."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "source": "azure_sql",
        "pulled_at": date.today().isoformat(),
        "pulled_utc": datetime.now(timezone.utc).isoformat(),
        "server": os.environ.get("SQL_SERVER", ""),
        "database": os.environ.get("SQL_DATABASE", ""),
        "table_count": len(rows),
        "tables": sorted(rows, key=lambda row: (row["table_schema"], row["table_name"])),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s tables to %s", len(rows), out)
    return payload


def load_row_estimates(path: Path = DEFAULT_OUT) -> dict[str, int]:
    """Load ``SCHEMA.TABLE -> row_count`` estimates from a saved inventory."""
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        f"{row['table_schema']}.{row['table_name']}".upper(): int(row["row_count"])
        for row in payload.get("tables", [])
    }


def main() -> None:
    """CLI entry point for pulling the deployed warehouse inventory."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    rows = fetch_dw_tables(timeout=args.timeout)
    save_inventory(rows, out=args.out)


if __name__ == "__main__":
    main()
