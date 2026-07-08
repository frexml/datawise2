# DataWise

A web app for reverse-engineering IBM DataStage `.dsx` exports into column-level data lineage. Users upload a job export through the UI; the backend parses it deterministically, a LangGraph multi-agent workflow enriches stages and links with LLM-generated explanations, and the result is rendered as interactive lineage graphs, stage breakdowns, and downloadable CSV/JSON artifacts.

Built by [mobileLIVE](https://mobilelive.ca).

---

## Architecture

```
                            ┌──────────────────────┐
                            │  Frontend (nginx)    │
                            │  React + Vite build  │
 Browser ─── HTTPS ────▶    │  Serves SPA, proxies │
                            │  /api/* to backend   │
                            └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │  Backend (FastAPI)   │
                            │  ── enqueues Celery  │
                            │  tasks, reads DB     │
                            └──┬─────────┬─────────┘
                               │         │
                  ┌────────────▼──┐   ┌──▼───────────┐
                  │  Postgres     │   │  Redis       │
                  │  jobs/stages/ │   │  Celery      │
                  │  links/results│   │  broker+back │
                  └───────────────┘   └──┬───────────┘
                                         │
                            ┌────────────▼──────────┐
                            │  Worker (Celery)      │
                            │  Same image as backend│
                            │  Runs LangGraph flow: │
                            │  parse → analyze →    │
                            │  deep-analyze (LLM) → │
                            │  lineage             │
                            └───────────────────────┘
```

Uploads are written to a **shared filesystem** (Docker volume locally, Azure Files in Azure) so backend and worker both see the same `/app/uploads/` directory.

---

## Repo layout

```
.
├── src/
│   └── dsxlineage/               # FastAPI service (uv-managed Python package)
│       ├── agents/               # LangGraph agents (parser, analyzer, deep_analyzer, lineage, workflow)
│       ├── api/endpoints.py      # REST routes mounted at /api
│       ├── core/config.py        # Pydantic settings
│       ├── db/                   # SQLAlchemy models + session
│       ├── services/
│       ├── worker.py             # Celery app + task
│       └── main.py               # FastAPI entry
├── tests/                        # Parser/lineage verification scripts
├── scripts/                      # Ops/debug scripts (check_db.py, show_lineage.py, ...)
├── migrations/                   # Hand-written SQL migrations (run manually per env)
├── data/
│   ├── samples/                  # Sample .dsx + reference outputs
│   └── end_to_end_linage/        # Generated lineage CSVs for samples
├── frontend/                     # Vite + React (JSX)
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   └── components/{Dashboard,FileUpload,JobDetails}.jsx
│   ├── public/                   # favicons, logo (served at root)
│   ├── nginx.conf.template       # Runtime-templated reverse proxy
│   ├── vite.config.js            # Dev proxy for /api → backend
│   └── Dockerfile                # node build → nginx:alpine serve
├── terraform/                    # Azure IaC (see terraform/README.md)
├── docs/                         # Design notes, deploy patterns
├── Dockerfile                    # python:3.12-slim + uv (backend + worker image)
├── pyproject.toml / uv.lock      # uv-managed Python deps
├── docker-compose.yml            # Local dev stack
└── .env.example                  # Required env vars
```

---

## Quickstart (local)

```bash
cp .env.example .env
# Edit .env and set a real OPENAI_API_KEY

docker-compose up --build
```

Then:

| Service      | URL                          |
| ------------ | ---------------------------- |
| Frontend UI  | http://localhost:5173        |
| Backend API  | http://localhost:8000        |
| API docs     | http://localhost:8000/docs   |
| Postgres     | `localhost:5432` (`postgres` / `postgres` / `dsx_db`) |
| Redis        | `localhost:6379`             |

Watch logs in another tab:

```bash
docker-compose logs -f backend         # FastAPI
docker-compose logs -f celery_worker   # async processing + LLM calls
```

Upload a sample DSX via the UI or via curl:

```bash
curl -F "file=@data/samples/BNCMRXALLInsSTGTransactionActual.dsx" \
  http://localhost:8000/api/upload
```

Tear down with `docker-compose down` (keeps DB data) or `docker-compose down -v` (clean slate).

---

## API surface

All routes are mounted at `/api`.

| Method   | Path                                  | Purpose                                  |
| -------- | ------------------------------------- | ---------------------------------------- |
| `POST`   | `/api/upload`                         | Upload a `.dsx`, returns `{job_id}`      |
| `GET`    | `/api/jobs`                           | List jobs                                |
| `GET`    | `/api/jobs/{id}`                      | Job metadata + counts                    |
| `GET`    | `/api/jobs/{id}/full`                 | Job + stages + links + annotations       |
| `GET`    | `/api/jobs/{id}/lineage`              | End-to-end lineage (CSV-backed)          |
| `GET`    | `/api/jobs/{id}/stage-lineage`        | Stage-level lineage (CSV-backed)         |
| `GET`    | `/api/results/{id}`                   | Result artifact for a completed job      |
| `GET`    | `/api/stages/{id}/explanation`        | LLM-generated stage explanation          |
| `GET`    | `/api/links/{id}/explanation`         | LLM-generated link explanation           |
| `DELETE` | `/api/jobs/{id}`                      | Delete a job                             |

Interactive OpenAPI / Swagger UI is at `/docs` (FastAPI).

---

## Configuration

All config flows through env vars. Sensitive values come from a `.env` file locally and from Azure Key Vault in Azure (see [`terraform/README.md`](terraform/README.md)).

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | Used by deep-analyzer agent and any LLM-enriched endpoints |
| `OPENAI_MODEL` | no (default `gpt-4o`) | Override if you want a cheaper/faster model for dev |
| `DATABASE_URL` | no | If set, wins over `POSTGRES_*` components |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_SERVER` / `POSTGRES_PORT` / `POSTGRES_DB` | no | Default to docker-compose values |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | no | Both default to local Redis; in Azure both point to Azure Cache (`rediss://…?ssl_cert_reqs=CERT_REQUIRED`) |
| `BACKEND_URL` | frontend prod | nginx in the frontend image substitutes this into its `/api/*` proxy_pass |
| `VITE_BACKEND_URL` | frontend dev | Vite dev-server proxy target for `npm run dev` |

---

## Deployment

The project deploys to **Azure Container Apps** with managed Postgres, Redis, Key Vault, and Azure Files. Infrastructure is fully described in Terraform under `terraform/`.

See **[`terraform/README.md`](terraform/README.md)** for the end-to-end runbook (state bootstrap, first apply, image build/push, second apply, rollout patterns).

The three services map to three Container Apps in a shared environment:

| Compose service | Container App                  | Ingress      |
| --------------- | ------------------------------ | ------------ |
| `backend`       | `ca-dsxlineage-dev-backend`    | public :8000 |
| `frontend`      | `ca-dsxlineage-dev-frontend`   | public :80   |
| `celery_worker` | `ca-dsxlineage-dev-worker`     | none         |

---

## Tech Stack

**Backend:** Python 3.12, FastAPI, Uvicorn, SQLAlchemy, Pydantic v2, Celery, LangGraph, LangChain, OpenAI SDK, uv  
**Frontend:** React 18, Vite, Tailwind CSS, axios, React Router, ReactFlow  
**Runtime:** Docker, nginx (alpine), Docker Compose (local)  
**Data:** PostgreSQL, Redis, Azure Files (shared uploads)  
**Cloud:** Azure Container Apps, Azure Container Registry, Azure Key Vault, Azure Database for PostgreSQL Flexible Server, Azure Cache for Redis, Azure Storage, Azure Log Analytics  
**IaC:** Terraform (azurerm ~> 3.110)

---

## Project conventions

- **Date awareness** — never hardcode dates; derive from `date.today()` (Python) or inject `created_on=$(date -u +%F)` (Terraform CI).
- **Secrets** — never in source, `.tfvars`, or committed config. Local: `.env` (gitignored). Azure: Key Vault, referenced by Container App secret blocks with versionless URIs.
- **Image tags** — never `:latest`. Tags are timestamps (`$(date -u +%Y%m%d-%H%M%S)`); a fresh tag forces a new Container Apps revision so KV-sourced secrets are re-fetched.
- **Platform** — on Apple Silicon, always `docker build --platform linux/amd64` so images run in Azure (linux/amd64 only).

---

## Known dev compromises

These are acceptable for dev but **must be addressed before prod**:

- Postgres + Redis + Storage Account have `public_network_access_enabled = true`. Prod needs private endpoints with VNet integration.
- ACR uses admin credentials (tenant restriction blocked `AcrPull` role assignment). Prod should use a managed identity with `AcrPull` granted by an ops principal who can write role assignments.
- Key Vault `purge_protection_enabled = false` so dev teardown is clean. Prod must enable it.
- No Front Door / WAF in front of public ingress.
- Container Apps auto-scaling uses replica bounds only; no KEDA HTTP/queue triggers configured.
- CORS in backend is wide-open (`allow_origins=["*"]`); tighten before exposing publicly.

---

## License

Internal — mobileLIVE.
