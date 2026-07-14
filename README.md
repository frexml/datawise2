# DataWise

A web app for reverse-engineering legacy ETL exports — **IBM DataStage** (`.dsx`), **SSIS** (`.dtsx`), and **Informatica PowerCenter** (`.xml`) — into governed, column-level data lineage. Users upload a job export (or a batch of many, tagged by domain/wave); a LangGraph multi-agent pipeline detects the dialect and parses it deterministically, extracts lineage, generates separate **technical** and **business** summaries via an LLM, mirrors the job into a **Neo4j** property graph to flag structural inefficiencies, and routes every AI-generated summary through a **human review/approval gate** before it's considered governed. Approved jobs are automatically pushed to an **OpenMetadata** data-governance catalog. Results are rendered as an interactive lineage graph, tabbed job dashboards, a cross-job review queue, a portfolio-level coverage dashboard (by domain/wave), and downloadable CSV/Excel/PDF artifacts — including a regulatory evidence pack and an LLM-generated delivery-effort estimate (ScopeIQ).

Built by [ML arteka](https://ML arteka.ca).

---

## Architecture

End-to-end journey of a single file — dialect detection, parse, LLM enrichment, persistence, graph mirroring, the human review gate (including reject → edit-or-rerun), catalog push, and every downstream output:

```mermaid
flowchart TD
    classDef client fill:#0ea5e9,color:#ffffff,stroke:#0369a1,stroke-width:2px
    classDef ingest fill:#6366f1,color:#ffffff,stroke:#4338ca,stroke-width:2px
    classDef parse fill:#8b5cf6,color:#ffffff,stroke:#6d28d9,stroke-width:2px
    classDef llm fill:#ec4899,color:#ffffff,stroke:#be185d,stroke-width:2px
    classDef store fill:#10b981,color:#ffffff,stroke:#047857,stroke-width:2px
    classDef neo fill:#f97316,color:#ffffff,stroke:#c2410c,stroke-width:2px
    classDef decision fill:#334155,color:#ffffff,stroke:#0f172a,stroke-width:2px
    classDef pending fill:#eab308,color:#111111,stroke:#a16207,stroke-width:2px
    classDef rejected fill:#dc2626,color:#ffffff,stroke:#991b1b,stroke-width:2px
    classDef approved fill:#22c55e,color:#ffffff,stroke:#15803d,stroke-width:2px
    classDef output fill:#06b6d4,color:#ffffff,stroke:#0e7490,stroke-width:2px
    classDef external fill:#64748b,color:#ffffff,stroke:#334155,stroke-width:2px

    U[/"👤 Analyst uploads .dsx/.dtsx/.xml<br/>single or bulk, optional domain/wave tag"/]:::client
    U -->|HTTPS| FE["🖥️ Frontend nginx<br/>React SPA · proxies /api/*"]:::client
    FE -->|"POST /api/upload"| BE["⚙️ FastAPI Backend"]:::ingest
    BE -->|"save file"| VOL[("📁 shared uploads volume")]:::ingest
    BE -->|"Job row · PENDING<br/>+ domain/wave"| PG[("🗄️ Postgres<br/>jobs · results · stages · links<br/>annotations · reviews · lineages<br/>scopeiq_estimates")]:::store
    BE -->|"enqueue task"| RD[("📨 Redis<br/>broker + backend")]:::ingest
    RD --> W["🔧 Celery Worker"]:::ingest

    subgraph PIPE["🔬 LangGraph pipeline — streamed node-by-node, real progress"]
        direction TB
        DETECT["🔎 Detect dialect<br/>extension + XML root-tag sniff"]:::parse
        P1A["📄 Parse DataStage .dsx<br/>BEGIN/END block grammar"]:::parse
        P1B["📄 Parse SSIS .dtsx<br/>namespaced XML"]:::parse
        P1C["📄 Parse Informatica .xml<br/>POWERMART/REPOSITORY XML"]:::parse
        P2["🧩 Analyze structure<br/>→ generic stages/links shape"]:::parse
        P3["🗺️ Map lineage<br/>partner/pin · SSIS paths · Informatica connectors"]:::parse
        P4["🧠 Deep-analyze<br/>concurrent LLM calls per stage/link/annotation<br/>dialect-aware prompts"]:::llm
        P5["📝 Generate executive summaries<br/>Technical + Business"]:::llm
        OAI["🤖 OpenAI API<br/>gpt-4o + gpt-4o-mini"]:::external
        DETECT --> P1A --> P2
        DETECT --> P1B --> P2
        DETECT --> P1C --> P2
        P2 --> P3 --> P4 --> P5
        P4 -.->|LLM calls| OAI
        P5 -.->|LLM calls| OAI
    end
    W --> DETECT

    P5 --> SAVE["💾 Persist Job / Result / Stage /<br/>Link / Annotation rows"]:::store
    SAVE --> PG
    SAVE --> REV0["📋 Create Review<br/>status = pending_review"]:::pending
    REV0 -.-> PG
    SAVE -.->|"fire-and-forget"| LIN["🧵 generate_lineage_task<br/>DFS path-finding"]:::parse
    LIN --> PG

    SAVE --> SYNC["🔁 Mirror stages + links<br/>into Neo4j"]:::neo
    SYNC --> NEO[("🕸️ Neo4j<br/>Job → Stage → Links")]:::neo
    NEO --> INEFF["⚡ Cypher inefficiency<br/>detection queries"]:::neo
    INEFF --> NEO
    INEFF --> DONE["✅ Job.status = COMPLETED"]:::store
    INEFF --> OUT5["⚡ Inefficiency<br/>Findings panel"]:::output

    DONE -.->|"on-demand button"| SCOPEIQ["📦 ScopeIQ Agent<br/>decompose → 4 dimensions → aggregate"]:::llm
    SCOPEIQ -.->|"5 LLM calls"| OAI
    SCOPEIQ --> OUT6["📄 ScopeIQ Estimate PDF<br/>role-day breakdown · uplift · risk"]:::output

    REV0 --> GATE{"🚦 Human Review Gate<br/>Pending Reviews queue"}:::decision
    GATE -->|Approve| GOV["🛡️ Governed<br/>approved"]:::approved
    GATE -->|"Reject + required reason"| REJ["❌ Rejected"]:::rejected
    REJ --> CHOICE{"Edit or Re-run?"}:::decision
    CHOICE -->|"✏️ Edit"| EDIT["Reviewer hand-edits<br/>the text directly"]:::rejected
    EDIT --> GOV
    CHOICE -->|"🔁 Re-run"| RERUN["Regenerate summary —<br/>feedback fed into the prompt"]:::llm
    RERUN -.->|LLM call| OAI
    RERUN --> REV0

    GOV --> OUT1["🕸️ Interactive<br/>Lineage Graph"]:::output
    GOV --> OUT2["📄 Regulatory Evidence<br/>Pack PDF"]:::output
    GOV --> OUT3["📊 S2T Excel register"]:::output
    GOV --> OUT4["📋 Stage / End-to-End<br/>lineage tables"]:::output
    GOV -.->|"fire-and-forget, best-effort<br/>fires again on re-approval"| CATPUSH["📚 Push to Catalog<br/>REST API"]:::neo
    CATPUSH --> OM[("🗂️ OpenMetadata<br/>Tables · Pipeline · Lineage")]:::external
```

**Job progress is real, not simulated.** `process_file()` streams the LangGraph pipeline (`stream_mode="updates"`) instead of a single blocking `invoke()`; the worker persists `Job.current_stage` after every node completes (`parsing → analyzing → mapping_lineage → generating_summaries → saving_results → detecting_inefficiencies → completed`). The Home page polls this and animates a live pipeline stepper.

**Multi-dialect, one generic pipeline.** `agents/lib/dialect_detection.py` picks a dialect by extension (`.dsx`→DataStage, `.dtsx`→SSIS) or, for the ambiguous `.xml` extension, by sniffing the XML root tag (`POWERMART`→Informatica) rather than trusting the extension alone. Each dialect has its own parser (`dsx_parser.py`, `ssis_parser.py`, `informatica_parser.py`) and analyzer (`detailed_analyzer.py`, `ssis_analyzer.py`, `informatica_analyzer.py`), but all three converge on the same generic `{stages, links, annotations}` shape before hitting the shared deep-analysis, lineage, persistence, Neo4j, and review-gate code — none of that downstream code is dialect-aware except the LLM prompts themselves ("You are an expert {DataStage|SSIS|Informatica} Developer").

**Human review gate.** Every completed job creates a `Review` row (`status="pending_review"`) for its executive summary. Nothing is "governed" until a named reviewer approves or rejects it — from either the job's own dashboard (status badge + link) or the dedicated **Pending Reviews** queue (`/reviews`), which is the primary triage surface across all jobs. Both hit the same `POST /api/reviews/{id}`.

**Catalog push is additive, gated by approval.** On approval (including re-approval after an edit/rerun), `push_to_catalog_task` best-effort pushes a Pipeline entity (job) and Table entities (source/target tables from the job's `Lineage` rows), wired together with real lineage edges, into OpenMetadata via its REST API. A catalog outage logs a warning and does **not** fail the approval.

**ScopeIQ is decoupled from the review gate.** It's available as an on-demand button once a job reaches `COMPLETED`, independent of review status — a delivery-estimation agent decomposes the job into four fixed research dimensions (tech stack, compliance/regulatory, integration patterns, delivery risk), researches each with its own LLM call grounded in the job's actual lineage/inefficiency data, and aggregates role-level day estimates, complexity uplift %, and risk-day adjustments into a PDF.

**Neo4j is additive, not a hard dependency.** Postgres remains the system of record for the API and frontend contract (`GET /api/jobs/{id}/full` never changes shape). The worker mirrors stages/links into Neo4j and runs inefficiency detection there in a best-effort step — a Neo4j failure logs a warning and does not fail the job.

Uploads are written to a **shared filesystem** (Docker volume locally, Azure Files in Azure) so backend and worker both see the same `/app/uploads/` directory.

### Pipeline stages

The `Job.current_stage` values that drive the Home page's animated stepper — a simplified read of the same pipeline shown in full detail above:

```mermaid
flowchart LR
    A(["📦 Upload\n(PENDING)"]) --> B["🔬 Parsing\nETL export"]
    B --> C["🧩 Analyzing\nstructure"]
    C --> D["🗺️ Mapping\nlineage"]
    D --> E["🧠 Generating\nsummaries (LLM)"]
    E --> F["💾 Saving\nresults"]
    F --> G["⚡ Detecting\ninefficiencies"]
    G --> H(["✅ Completed"])

    style A fill:#e5e7eb,color:#111
    style H fill:#bbf7d0,color:#111
```

---

## Frontend pages

| Route | Component | Purpose |
| --- | --- | --- |
| `/` | `Home.jsx` | Hero landing page, KPI tiles, "Start New Run" (single file) or "Bulk Upload" (many files, shared domain/wave tag), animated pipeline stepper; auto-navigates to the job dashboard on single-file completion |
| `/history` | `JobHistory.jsx` | Full list of processed jobs — filterable by domain/wave (deep-linkable via `?domain=`/`?wave=`), status badges, domain/wave badges, delete |
| `/portfolio` | `Portfolio.jsx` | Documentation coverage broken down by domain and by wave — for engagements tracking many jobs as a program (e.g. "94% approved in Wave 1"), not one job at a time |
| `/jobs/:jobId` | `JobDetails.jsx` | Per-job tabbed dashboard — see below |
| `/reviews` | `PendingReviews.jsx` | Cross-job review queue — approve/reject with an inline summary preview |

**Job dashboard (`/jobs/:jobId`)** has a persistent header (Job Overview — Review Status, editable Domain/Wave, Governance Catalog link once pushed) and six tabs:

- **Summary** — technical and business summaries side by side, each with a copy-to-clipboard button
- **Inefficiency Findings** — Cypher-detected structural patterns (high fan-in/out, long derivation chains, orphan stages, repeated stage types), with severity badges
- **Lineage Graph** — interactive ReactFlow graph (click any node/link for its LLM explanation), with Evidence Pack PDF and Excel (S2T register) export
- **Stage Lineage** — searchable, CSV-exportable stage-level table
- **End-to-End Lineage** — searchable, CSV-exportable source→target lineage table
- **ScopeIQ Estimate** — on-demand delivery-effort estimate: generate button → live polling → per-dimension role-day breakdown, uplift signals, risk adjustments, PDF export

A dark-mode toggle (persisted, `prefers-color-scheme`-aware) is available from the top nav on every page.

---

## Repo layout

```
.
├── src/
│   └── dsxlineage/               # FastAPI service (uv-managed Python package)
│       ├── agents/               # LangGraph agents + workflow.py (graph definition, streaming)
│       │   │                     # parser/analyzer/lineage/deep_analyzer/inefficiency/scopeiq
│       │   └── lib/               # Dialect-specific parsing (no LLM):
│       │       ├── dialect_detection.py    # extension + XML root-tag sniff
│       │       ├── dsx_parser.py / detailed_analyzer.py / partner_extractor.py   # DataStage
│       │       ├── ssis_parser.py / ssis_analyzer.py                             # SSIS
│       │       └── informatica_parser.py / informatica_analyzer.py              # Informatica
│       ├── api/endpoints.py      # REST routes mounted at /api
│       ├── core/config.py        # Pydantic settings (Postgres, Redis, Neo4j, OpenAI, OpenMetadata)
│       ├── db/                   # SQLAlchemy models, session, graph.py (Neo4j driver + sync)
│       ├── services/              # lineage_analyzer.py (DFS path-finding), s2t_export.py (Excel),
│       │                          # evidence_pack.py / scopeiq_pdf.py / pdf_text.py (reportlab PDFs),
│       │                          # catalog_push.py (OpenMetadata REST client)
│       ├── worker.py              # Celery app: process_dsx_task, generate_lineage_task,
│       │                          # regenerate_summary_task, generate_scopeiq_estimate_task,
│       │                          # push_to_catalog_task
│       └── main.py               # FastAPI entry
├── tests/                        # Parser/lineage verification scripts
├── scripts/                      # Ops/debug scripts (check_db.py, show_lineage.py, ...)
├── migrations/                   # Hand-written SQL migrations (run manually per env)
├── data/
│   ├── samples/                  # Sample .dsx/.dtsx/.xml exports + reference outputs
│   └── end_to_end_linage/        # Generated lineage CSVs for samples
├── frontend/                     # Vite + React (JSX)
│   ├── src/
│   │   ├── App.jsx                # Routes, top nav, dark-mode toggle
│   │   ├── main.jsx
│   │   └── components/
│   │       ├── Home.jsx           # Landing hero + single/bulk upload + animated run stepper + KPIs
│   │       ├── FileUpload.jsx     # Single-file upload (+ optional domain/wave)
│   │       ├── BulkUpload.jsx     # Multi-file upload, one shared domain/wave tag, live batch progress
│   │       ├── JobHistory.jsx     # Processed jobs list, domain/wave filters
│   │       ├── Portfolio.jsx      # Coverage-by-domain / coverage-by-wave dashboard
│   │       ├── JobDetails.jsx     # Tabbed per-job dashboard, lineage graph, ScopeIQ tab
│   │       └── PendingReviews.jsx # Cross-job review queue
│   ├── public/                   # favicons, logo (served at root)
│   ├── nginx.conf.template       # Runtime-templated reverse proxy
│   ├── vite.config.js            # Dev proxy for /api → backend
│   └── Dockerfile                # node build → nginx:alpine serve
├── terraform/                    # Azure IaC (see terraform/README.md) — does not yet provision OpenMetadata
├── docs/                         # Design notes, deploy patterns
├── datawise-docs/                # Product vision docs, use cases (reviewed against, not consumed by, the app)
├── Dockerfile                    # python:3.12-slim + uv (backend + worker image)
├── pyproject.toml / uv.lock      # uv-managed Python deps
├── docker-compose.yml            # Local dev stack — app services + OpenMetadata catalog stack
└── .env.example                  # Required env vars
```

---

## Quickstart (local)

```bash
cp .env.example .env
# Edit .env and set a real OPENAI_API_KEY

docker compose up --build
```

OpenMetadata's schema migration (`openmetadata-ops.sh migrate`) runs automatically as a one-shot `openmetadata_migrate` service gated on MySQL's healthcheck, and `openmetadata_server` won't start until it exits successfully — this is what used to require a manual step on every fresh `openmetadata_mysql_data` volume (e.g. after `docker compose down -v`, or any teardown that drops the volume). The migration step is idempotent, so it's a no-op (a few seconds) on a volume that's already up to date — no manual intervention needed either way.

Wait for `curl -sf http://localhost:8585/api/v1/system/version` to return `200` before pushing anything to the catalog — Elasticsearch index bootstrapping takes a bit after a fresh start.

| Service        | URL                             |
| -------------- | -------------------------------- |
| Frontend UI    | http://localhost:5173            |
| Backend API    | http://localhost:8000            |
| API docs       | http://localhost:8000/docs       |
| Postgres       | `localhost:5433` (`postgres` / `postgres` / `dsx_db`) |
| Redis          | `localhost:6379`                 |
| Neo4j Browser  | http://localhost:7474 (`neo4j` / see `NEO4J_PASSWORD`) |
| Neo4j Bolt     | `localhost:7687`                 |
| OpenMetadata UI | http://localhost:8585 (`admin@open-metadata.org` / `admin`, default local creds) |
| OpenMetadata ingestion/Airflow | http://localhost:8080 — unused (catalog push is REST-only, no scheduled connectors) |

Watch logs in another tab:

```bash
docker compose logs -f backend         # FastAPI
docker compose logs -f celery_worker   # async processing + LLM calls + Neo4j sync + catalog push
```

Upload a sample export via the UI ("Start New Run" or "Bulk Upload" on the home page) or via curl:

```bash
# Single file, optionally tagged
curl -F "file=@data/samples/BNCMRXALLInsSTGTransactionActual.dsx" \
     -F "domain=Fees" -F "wave=Wave 1" \
     http://localhost:8000/api/upload

# SSIS and Informatica samples work the same way
curl -F "file=@data/samples/Sample_Customer_ETL.dtsx" http://localhost:8000/api/upload
curl -F "file=@data/samples/Sample_Customer_ETL_Informatica.xml" http://localhost:8000/api/upload
```

Tear down with `docker compose down` (keeps DB/graph/catalog data) or `docker compose down -v` (clean slate — required after pulling schema changes, since `Base.metadata.create_all` only adds new tables, not columns on existing ones; apply `migrations/*.sql` by hand against a non-disposable database — and re-run the OpenMetadata migration step above against a fresh `openmetadata_mysql_data` volume).

---

## API surface

All routes are mounted at `/api`.

| Method   | Path                                  | Purpose                                              |
| -------- | -------------------------------------- | ----------------------------------------------------- |
| `POST`   | `/api/upload`                          | Upload a `.dsx`/`.dtsx`/`.xml`, optional `domain`/`wave` form fields, returns `{job_id}`, kicks off the pipeline |
| `GET`    | `/api/jobs`                            | List jobs; optional `?domain=`/`?wave=` filters (`Unassigned` matches untagged) |
| `PATCH`  | `/api/jobs/{id}`                       | Update a job's `domain`/`wave` tags post-hoc          |
| `GET`    | `/api/portfolio`                       | Coverage breakdown by domain and by wave, plus distinct-value lists for autocomplete |
| `GET`    | `/api/stats`                           | Dashboard KPIs — job counts, review coverage %, inefficiency count |
| `GET`    | `/api/jobs/{id}`                       | Job metadata, `status`, `current_stage`, counts       |
| `GET`    | `/api/jobs/{id}/full`                  | Job + stages + links + annotations (the graph contract) |
| `GET`    | `/api/results/{id}`                    | Technical + business summary, raw parse, analysis JSON |
| `GET`    | `/api/jobs/{id}/reviews`               | Review rows for one job                               |
| `GET`    | `/api/reviews`                         | Cross-job review queue (`?status=pending_review` default, or `all`) |
| `POST`   | `/api/reviews/{id}`                    | Approve/reject a review `{reviewer, decision, feedback}` — triggers catalog push on approval |
| `POST`   | `/api/reviews/{id}/edit`               | Hand-edit + approve a rejected summary — triggers catalog push |
| `POST`   | `/api/reviews/{id}/rerun`              | Regenerate a rejected summary async, feedback fed into the prompt |
| `GET`    | `/api/stages/{id}/explanation`         | LLM-generated stage explanation                       |
| `GET`    | `/api/links/{id}/explanation`          | LLM-generated link explanation                        |
| `GET`    | `/api/jobs/{id}/lineage`               | End-to-end lineage (Postgres-backed)                  |
| `GET`    | `/api/jobs/{id}/inefficiencies`        | Flagged structural inefficiency patterns (Neo4j-backed) |
| `GET`    | `/api/jobs/{id}/export/s2t`            | Source-to-target mapping register (`.xlsx` download)  |
| `POST`   | `/api/jobs/{id}/scopeiq/generate`      | Kick off async ScopeIQ delivery-estimate generation   |
| `GET`    | `/api/jobs/{id}/scopeiq`               | Poll ScopeIQ estimate status/result                    |
| `GET`    | `/api/jobs/{id}/export/scopeiq-estimate` | ScopeIQ estimate PDF download (once completed)       |
| `GET`    | `/api/jobs/{id}/export/evidence-pack`  | Regulatory lineage evidence pack (`.pdf` download)     |
| `GET`    | `/api/jobs/{id}/stage-lineage`         | Stage-level lineage (legacy CSV-backed, optional)     |
| `DELETE` | `/api/jobs/{id}`                       | Delete a job                                          |

Interactive OpenAPI / Swagger UI is at `/docs` (FastAPI).

---

## Configuration

All config flows through env vars. Sensitive values come from a `.env` file locally and from Azure Key Vault in Azure (see [`terraform/README.md`](terraform/README.md)).

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | Used by the deep-analyzer and ScopeIQ agents for all LLM calls |
| `OPENAI_MODEL` | no (default `gpt-4o`) | Main model for stage analysis + ScopeIQ; link/annotation/summary calls use `gpt-4o-mini` |
| `DATABASE_URL` | no | If set, wins over `POSTGRES_*` components |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_SERVER` / `POSTGRES_PORT` / `POSTGRES_DB` | no | Default to docker-compose values |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | no | Both default to local Redis; in Azure both point to Azure Cache (`rediss://…?ssl_cert_reqs=CERT_REQUIRED`) |
| `NEO4J_URI` | no (default `bolt://neo4j:7687`) | Self-hosted Neo4j Community — internal-only in Azure |
| `NEO4J_USER` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | no | Password is Terraform-generated and Key-Vault-sourced in Azure |
| `OPENMETADATA_API_URL` | no (default `http://openmetadata_server:8585/api/v1`) | Container-to-container URL the backend/worker call |
| `OPENMETADATA_UI_URL` | no (default `http://localhost:8585`) | Browser-facing URL used to build `Job.catalog_url` links |
| `OPENMETADATA_ADMIN_EMAIL` / `OPENMETADATA_ADMIN_PASSWORD` | no (defaults are OpenMetadata's stock local-dev admin) | Used to obtain a bearer token per catalog push — replace with a real bot token before any non-local deployment |
| `BACKEND_URL` | frontend prod | nginx in the frontend image substitutes this into its `/api/*` proxy_pass |
| `VITE_BACKEND_URL` | frontend dev | Vite dev-server proxy target for `npm run dev` |

---

## Deployment

The project deploys to **Azure Container Apps** with managed Postgres, managed Redis, self-hosted Neo4j, Key Vault, and Azure Files. Infrastructure is fully described in Terraform under `terraform/`.

See **[`terraform/README.md`](terraform/README.md)** for the end-to-end runbook (state bootstrap, first apply, image build/push, second apply, rollout patterns).

The compose services map to four Container Apps in a shared environment:

| Compose service | Container App                | Ingress                    |
| --------------- | ----------------------------- | --------------------------- |
| `backend`       | `ca-dsxlineage-dev-backend`   | public :8000                |
| `frontend`      | `ca-dsxlineage-dev-frontend`  | public :80                  |
| `celery_worker` | `ca-dsxlineage-dev-worker`    | none                        |
| `neo4j`         | `ca-dsxlineage-dev-neo4j`     | internal only, TCP :7687    |

Postgres and Redis are managed Azure PaaS (Flexible Server / Cache for Redis), not containers — Neo4j has no equivalent managed offering, so it runs self-hosted as a fourth Container App with an Azure Files-backed volume for `/data` and a Terraform-generated password sourced from Key Vault.

**OpenMetadata is not yet provisioned in Terraform** — it currently only runs via the local docker-compose stack. Standing it up in Azure would follow the same self-hosted-Container-App pattern as Neo4j (its own MySQL and Elasticsearch have no Azure-managed equivalent either), plus swapping the default local admin credentials for a real bot token sourced from Key Vault.

---

## Tech Stack

**Backend:** Python 3.12, FastAPI, Uvicorn, SQLAlchemy, Pydantic v2, Celery, LangGraph (streamed execution), LangChain, OpenAI SDK, Neo4j Python driver, openpyxl, reportlab, requests, uv
**Frontend:** React 18, Vite, Tailwind CSS (dark mode), axios, React Router, ReactFlow, dagre, react-markdown, jsPDF/html-to-image
**Data:** PostgreSQL (system of record), Neo4j Community (graph mirror + inefficiency detection), Redis (Celery broker), Azure Files (shared uploads)
**Governance:** OpenMetadata (open-source data catalog — tables, pipelines, lineage), pushed via plain REST (not the full `openmetadata-ingestion` SDK)
**Cloud:** Azure Container Apps, Azure Container Registry, Azure Key Vault, Azure Database for PostgreSQL Flexible Server, Azure Cache for Redis, Azure Storage, Azure Log Analytics
**IaC:** Terraform (azurerm ~> 3.110)

---

## Project conventions

- **Date awareness** — never hardcode dates; derive from `date.today()` (Python) or inject `created_on=$(date -u +%F)` (Terraform CI).
- **Dialect-agnostic downstream** — a new ETL dialect needs a parser + analyzer that produce the existing generic `{stages, links, annotations}` shape (see `agents/lib/ssis_*.py` for the pattern); nothing past the analyzer step should ever branch on dialect except LLM prompt wording.
- **Secrets** — never in source, `.tfvars`, or committed config. Local: `.env` (gitignored). Azure: Key Vault, referenced by Container App secret blocks with versionless URIs.
- **Image tags** — never `:latest`. Tags are timestamps (`$(date -u +%Y%m%d-%H%M%S)`); a fresh tag forces a new Container Apps revision so KV-sourced secrets are re-fetched.
- **Platform** — on Apple Silicon, always `docker build --platform linux/amd64` so images run in Azure (linux/amd64 only).
- **Schema changes** — `Base.metadata.create_all` only creates new tables; new columns need both a model change and a hand-written file in `migrations/` (applied manually per environment).

---

## Known dev compromises

These are acceptable for dev but **must be addressed before prod**:

- Postgres + Redis + Storage Account have `public_network_access_enabled = true`. Prod needs private endpoints with VNet integration.
- ACR uses admin credentials (tenant restriction blocked `AcrPull` role assignment). Prod should use a managed identity with `AcrPull` granted by an ops principal who can write role assignments.
- Key Vault `purge_protection_enabled = false` so dev teardown is clean. Prod must enable it.
- No Front Door / WAF in front of public ingress.
- Container Apps auto-scaling uses replica bounds only; no KEDA HTTP/queue triggers configured.
- CORS in backend is wide-open (`allow_origins=["*"]`); tighten before exposing publicly.
- Neo4j Community has no clustering/HA — single replica; acceptable for a demo, not for production lineage-of-record.
- Review-gate identity is a free-text reviewer name, not authenticated — fine for a demo, needs real auth/SSO before production use.
- OpenMetadata catalog push authenticates as the default local admin account (`admin@open-metadata.org`/`admin`) — replace with a scoped bot token before any shared or production deployment.
- Business-glossary/term-linking to the catalog is not implemented — only tables, a pipeline, and lineage edges are pushed; column-to-business-term mapping would need a glossary source that doesn't exist yet.
- Bulk upload submits files sequentially from the browser (not parallelized, no batch-level backend entity) — fine at demo scale, would need real concurrency control and a first-class "batch" record for very large (100+) simultaneous uploads.
- ScopeIQ's role-day estimates are LLM-generated per job, not benchmarked against actual delivery history — useful as a structured starting estimate, not a substitute for engagement-lead judgment.

---

## License

Internal — ML arteka.
