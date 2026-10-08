"""Pushes an approved job's lineage and summary to the open-source data
governance catalog (OpenMetadata).

Fires on review approval (see worker.push_to_catalog_task) - an
ungoverned (not-yet-approved) summary never reaches the catalog, matching
the Platform Development Guide's "Catalog Integration" output, just backed
by OpenMetadata instead of Collibra/Atlan/Alation.

Uses OpenMetadata's plain REST API directly rather than its full
`openmetadata-ingestion` SDK, which pulls in many connector-specific
dependencies not needed for a simple push integration. Every create call
is idempotent (PUT = create-or-update), so re-pushing after a re-approval
just updates the existing entities.
"""
import base64

import requests
from sqlalchemy.orm import Session

from dsxlineage.core.config import settings
from dsxlineage.db import models

_DATABASE_SERVICE = "DataWise"
_DATABASE_NAME = "dsx_lineage"
_SCHEMA_NAME = "public"
_PIPELINE_SERVICE = "DataWisePipelines"
_TIMEOUT = 15


def _get_token() -> str:
    password_b64 = base64.b64encode(settings.OPENMETADATA_ADMIN_PASSWORD.encode()).decode()
    resp = requests.post(
        f"{settings.OPENMETADATA_API_URL}/users/login",
        json={"email": settings.OPENMETADATA_ADMIN_EMAIL, "password": password_b64},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["accessToken"]


class _CatalogClient:
    def __init__(self) -> None:
        self._token = _get_token()

    def _put(self, path: str, payload: dict) -> dict:
        resp = requests.put(
            f"{settings.OPENMETADATA_API_URL}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        # /lineage returns 200 with an empty body on success; every other
        # entity endpoint returns the created/updated entity as JSON.
        return resp.json() if resp.text.strip() else {}

    def ensure_database_service(self) -> None:
        self._put("/services/databaseServices", {
            "name": _DATABASE_SERVICE,
            "serviceType": "CustomDatabase",
            "connection": {"config": {
                "type": "CustomDatabase",
                "sourcePythonClass": "custom_database.CustomDatabaseSource",
            }},
        })

    def ensure_database(self) -> None:
        self._put("/databases", {"name": _DATABASE_NAME, "service": _DATABASE_SERVICE})

    def ensure_schema(self) -> None:
        self._put("/databaseSchemas", {
            "name": _SCHEMA_NAME,
            "database": f"{_DATABASE_SERVICE}.{_DATABASE_NAME}",
        })

    def upsert_table(self, name: str, columns: list[str]) -> dict:
        return self._put("/tables", {
            "name": name,
            "databaseSchema": f"{_DATABASE_SERVICE}.{_DATABASE_NAME}.{_SCHEMA_NAME}",
            "columns": [{"name": c, "dataType": "UNKNOWN"} for c in columns],
        })

    def ensure_pipeline_service(self) -> None:
        self._put("/services/pipelineServices", {
            "name": _PIPELINE_SERVICE,
            "serviceType": "CustomPipeline",
            "connection": {"config": {
                "type": "CustomPipeline",
                "sourcePythonClass": "custom_pipeline.CustomPipelineSource",
            }},
        })

    def upsert_pipeline(self, name: str, display_name: str, description: str) -> dict:
        return self._put("/pipelines", {
            "name": name,
            "displayName": display_name,
            "description": description,
            "service": _PIPELINE_SERVICE,
        })

    def add_lineage(self, from_id: str, from_type: str, to_id: str, to_type: str) -> None:
        self._put("/lineage", {
            "edge": {
                "fromEntity": {"id": from_id, "type": from_type},
                "toEntity": {"id": to_id, "type": to_type},
            },
        })


def push_job_to_catalog(job_id: int, db: Session) -> str:
    """Pushes a job's tables, pipeline, and lineage to the catalog. Returns
    the browsable catalog URL for the job's Pipeline entity."""
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    result = db.query(models.Result).filter(models.Result.job_id == job_id).first()
    lineage_rows = db.query(models.Lineage).filter(models.Lineage.job_id == job_id).all()

    client = _CatalogClient()
    client.ensure_database_service()
    client.ensure_database()
    client.ensure_schema()

    # Merge columns across BOTH source and target roles before upserting -
    # a table acting as an intermediate staging point appears as a target
    # in one lineage row and a source in another, and must get one entity
    # carrying the union of columns, not two competing partial upserts.
    columns_by_table: dict[str, set[str]] = {}
    source_table_names: set[str] = set()
    target_table_names: set[str] = set()
    for row in lineage_rows:
        if row.source_table:
            source_table_names.add(row.source_table)
            columns_by_table.setdefault(row.source_table, set()).add(row.source_field or "_unknown")
        if row.target_table:
            target_table_names.add(row.target_table)
            columns_by_table.setdefault(row.target_table, set()).add(row.target_field or "_unknown")

    tables = {
        name: client.upsert_table(name, sorted(cols))
        for name, cols in columns_by_table.items()
    }

    client.ensure_pipeline_service()
    description = "\n\n".join(filter(None, [
        result.llm_explanation if result else None,
        result.business_summary if result else None,
    ])) or "No governed summary available."
    pipeline = client.upsert_pipeline(
        name=f"job_{job.id}",
        display_name=job.filename,
        description=description,
    )
    pipeline_id = pipeline["id"]

    for name in source_table_names:
        client.add_lineage(tables[name]["id"], "table", pipeline_id, "pipeline")
    for name in target_table_names:
        client.add_lineage(pipeline_id, "pipeline", tables[name]["id"], "table")

    return f"{settings.OPENMETADATA_UI_URL}/pipeline/{pipeline['fullyQualifiedName']}"
