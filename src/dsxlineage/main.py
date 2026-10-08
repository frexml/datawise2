from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dsxlineage.db.database import engine, Base

# Import estate models so they are registered on Base before create_all
# Legacy jobs tables (src/dsxlineage/db/models.py) are still imported for
# create_all history but not exposed via API - estate is the product.
import dsxlineage.db.models  # noqa: F401 - keep legacy tables for history
import dsxlineage.estate.models  # noqa: F401

# Create tables - best-effort at import (fails gracefully when DB not reachable,
# e.g. during tests or when running without Docker). Tables are also created
# on FastAPI startup.
try:
    Base.metadata.create_all(bind=engine)
except Exception as _e:  # noqa: BLE001
    print(f"Warning: could not create tables at import: {_e}")

app = FastAPI(
    title="Estate Modernization Accelerator API",
    description="On-prem estate lineage, analytics, grounded chat, and migration bridge - knowledge graph + evidence ledger as system of record.",
    version="2.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from dsxlineage.estate.api import router as estate_router
app.include_router(estate_router, prefix="/api/estates", tags=["estates"])
# Legacy ETL API (/api/jobs, /api/upload, /api/reviews, /api/portfolio …)
# is archived at archive/etl-v1/api/endpoints.py and tag v1-etl-final.
# Parsers remain as a pure library at src/dsxlineage/parsers/.


@app.on_event("startup")
def _create_tables_on_startup():
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as _e:  # noqa: BLE001
        print(f"Warning: could not create tables on startup: {_e}")


@app.get("/")
def read_root():
    return {
        "message": "Welcome to Estate Modernization Accelerator API - estate-only (v2)",
        "version": "2.0.0",
        "docs": "/docs",
        "estates": "/api/estates",
        "legacy_archived": "archive/etl-v1 - tag v1-etl-final",
    }
