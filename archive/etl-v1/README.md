# Archive — ETL-only v1 (frozen at v1-etl-final)

This snapshot is the last ETL-only DataWise before the Estate-only pivot (2026-10-08).

- **API:** `src/dsxlineage/api/endpoints.py` — `/api/upload`, `/api/jobs`, `/api/jobs/{id}/full`, `/api/lineage`, `/api/reviews`, `/api/portfolio`, `/api/stats`
- **Agents:** `src/dsxlineage/agents/` — `parser_agent`, `analyzer_agent`, `lineage_agent`, `deep_analyzer_agent`, `scopeiq_agent` + `agents/lib/` parsers (dsx/ssis/informatica)
- **Frontend:** `Home`, `JobHistory`, `JobDetails` (2,224-line God), `Portfolio`, `PendingReviews`, `FileUpload`, `BulkUpload`
- **Tests:** `test_real_dsx.py`, `test_full_flow.py`, `test_edge_building.py`, `test_link_matching.py`, `test_parser_header.py`

Restored via: `git show v1-etl-final:src/dsxlineage/api/endpoints.py`
Parsers remain as a **pure library** at `src/dsxlineage/parsers/` (re-export of `src/dsxlineage/agents/lib/`).

Do not modify — estate is now the product (`/api/estates`).
