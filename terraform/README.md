# DataWise — Azure Deployment (Terraform)

Infrastructure-as-Code for the dev environment of the DataWise lineage platform on **Azure Container Apps**. See `../DEPLOY-AZURE.md` at the repo root for the one-command path via `scripts/deploy_azure.sh` — this file covers the manual/step-by-step flow and the architecture underneath it.

## What gets created

| Resource | Purpose | SKU (dev) |
| --- | --- | --- |
| Resource Group | All resources | — |
| Azure Container Registry | Image store, admin user enabled | Basic |
| User-assigned Managed Identity | Key Vault secret retrieval (Get/List) | — |
| Postgres Flexible Server | App database | B_Standard_B1ms |
| MySQL Flexible Server | OpenMetadata's backing store | B_Standard_B1ms |
| Azure Cache for Redis | Celery broker + result backend (TLS) | Basic C0 |
| Azure Storage Account | Shared file shares (uploads, Neo4j data) | Standard LRS |
| Key Vault | OpenAI key, DB password, DB URL, Redis URL, Neo4j password, OpenMetadata mysql password | standard |
| Log Analytics Workspace | Container Apps logs | PerGB2018 |
| Container Apps Environment | Hosts all apps + registers the file shares | Consumption |
| Container App `ca-…-web` | FastAPI + built frontend behind Caddy, one image, public ingress :8080 | 1 CPU / 2Gi, 1–3 replicas |
| Container App `ca-…-worker` | Celery, no ingress | 1 CPU / 2Gi, 1–5 replicas |
| Container App `ca-…-neo4j` | Neo4j Community, internal-only :7687 | 1 CPU / 2Gi, single instance |
| Container App `ca-…-om-es` | OpenMetadata's Elasticsearch, internal-only :9200 | 1 CPU / 2Gi, single instance |
| Container App `ca-…-om-server` | OpenMetadata governance catalog UI + API, public ingress :8585 | 2 CPU / 4Gi, single instance |
| Container App Job `caj-…-om-migrate` | One-shot OpenMetadata schema migration, manually triggered | 1 CPU / 2Gi |

## Key architectural decisions

- **One image, one URL for the web tier.** `Dockerfile.web` builds the Vite frontend and the FastAPI backend into a single image; Caddy listens on :8080, serves the frontend statically, and reverse-proxies `/api/*` to the backend on `127.0.0.1:8000` — same-container, no CORS, no cross-FQDN TLS/SNI handling. This replaced the previous two-Container-App split (see the removed troubleshooting entry below for what that used to cost). The Celery worker stays a separate Container App — no ingress, independent scaling, and combining it into the web image would trade crash isolation for nothing.
- **ACR uses admin credentials, not `AcrPull` managed identity.** The host tenant restricts `Microsoft.Authorization/roleAssignments/write`, so the cleaner role-based path isn't available. Container Apps authenticate to ACR with admin username/password supplied as a Container App secret.
- **First apply is bootstrap-only.** The web, worker, and openmetadata_server Container Apps are gated on `image_tag != "bootstrap"`, so the first apply creates infra (ACR, Postgres, MySQL, Redis, KV, file shares, CAE, Neo4j, OpenMetadata's Elasticsearch, the migrate job) but not those three. After images are built, pushed, and the migrate job has run, a second apply with the real tag creates them. `scripts/deploy_azure.sh` automates this whole two-pass flow, including running and waiting on the migrate job in between.
- **OpenMetadata's MySQL is a managed Flexible Server, not self-hosted.** An earlier version of this module ran MySQL as a Container App on an Azure Files volume — InnoDB's redo-log file locking doesn't work over SMB, and it crash-looped on `Unable to lock ./#innodb_redo/#ib_redo0` / assertion failures the one time this was tried. Neo4j and Elasticsearch stay self-hosted (no managed Azure equivalent exists for either, and neither has MySQL's file-locking requirements); MySQL does have one, so there's no reason to fight the network-storage incompatibility.
- **OpenMetadata schema migration runs as a Container App Job, not at server startup.** `openmetadata_server`'s own entrypoint does not migrate its MySQL schema — a fresh database crash-loops on `Table 'openmetadata_db.ACT_GE_PROPERTY' doesn't exist` without an explicit `openmetadata-ops.sh migrate` run first (the exact issue this project's `docker-compose.yml` also had to solve locally, via a one-shot `openmetadata_migrate` service gated on a MySQL healthcheck). Azure Container Apps has no compose-style health-gated startup ordering, so the equivalent here is a **Container Apps Job** (`azurerm_container_app_job.openmetadata_migrate`) with `manual_trigger_config` — the deploy script runs it (`az containerapp job start`) and polls `az containerapp job execution list` until it succeeds, *before* the release apply creates `openmetadata_server`. The migration is idempotent, so rerunning it against an already-migrated database is safe and fast.
- **Shared `/app/uploads` via Azure Files.** The web app writes uploaded `.dsx`/`.dtsx`/XML files; the worker reads them. The share is registered with the Container Apps Environment as a named storage (`uploads`) and mounted by both. Neo4j's `/data` and OpenMetadata mysql's `/var/lib/mysql` get their own Azure Files shares the same way, so both survive redeploys.
- **Secrets pinned via versionless KV URIs.** Container Apps fetch secret values at *revision creation time* only — they don't poll. To roll a new secret value, bump `image_tag` (forces a new revision).
- **State backend uses access-key auth.** `use_azuread_auth` is off because the same tenant restriction blocks self-granting `Storage Blob Data Contributor`. The control-plane Owner role on the RG includes `listKeys`, which is enough.

## Layout

```
terraform/
├── README.md                       # this file
├── .gitignore                      # blocks *.tfvars and state files
├── bootstrap/create-state-storage.sh
├── envs/dev/
│   ├── backend.tf                  # Azure Blob state backend
│   ├── providers.tf                # azurerm ~> 4.20
│   ├── variables.tf
│   ├── main.tf                     # wires the four modules
│   ├── outputs.tf
│   └── dev.tfvars.example
└── modules/
    ├── registry/   # ACR (admin) + user-assigned identity
    ├── data/       # Postgres + MySQL + Redis + Storage Account + 2 File Shares
    ├── secrets/    # Key Vault + 6 secrets + access policies
    └── apps/       # CAE + CAE storage + web/worker/neo4j/OpenMetadata apps + migrate job
```

## One-time bootstrap

```bash
# 0a. Issue / rotate the OpenAI key (https://platform.openai.com/api-keys).
#     Never commit it.
# 0b. Authenticate to Azure.
az login
az account set --subscription "<your-subscription>"

# 0c. Create the Azure Blob backend for Terraform state.
chmod +x terraform/bootstrap/create-state-storage.sh
./terraform/bootstrap/create-state-storage.sh dev
```

The rest of this file is the manual, step-by-step version of what `../scripts/deploy_azure.sh` automates. Prefer the script; read on if you need to run a step in isolation or understand what it's doing.

## First apply — infra only (no image-backed Container Apps yet)

```bash
cd terraform/envs/dev

# Sensitive vars come from env, never .tfvars.
export TF_VAR_openai_api_key="<your-key>"
export TF_VAR_postgres_admin_password="$(openssl rand -base64 24)"

terraform init
terraform apply \
  -var-file=dev.tfvars.example \
  -var="created_on=$(date -u +%F)" \
  -var="image_tag=bootstrap"
```

After this: ACR / Postgres / Redis / KV / file shares / CAE / Neo4j / OpenMetadata mysql+ES / the migrate job all exist. `web`, `worker`, and `openmetadata_server` do NOT yet.

## Build + push + migrate + create the apps

```bash
cd ../../..   # repo root
export ACR_NAME=$(cd terraform/envs/dev && terraform output -raw acr_name)
export ACR=$(cd terraform/envs/dev && terraform output -raw acr_login_server)
export RG=$(cd terraform/envs/dev && terraform output -raw resource_group_name)
export MIGRATE_JOB=$(cd terraform/envs/dev && terraform output -raw openmetadata_migrate_job_name)
az acr login --name "$ACR_NAME"

export TAG=$(date -u +%Y%m%d-%H%M%S)
echo "TAG=$TAG"

# ALL builds must target linux/amd64 — Container Apps don't run arm64.
docker build --platform linux/amd64 -f Dockerfile.web -t "$ACR/dsx-web:$TAG"    .
docker push  "$ACR/dsx-web:$TAG"

docker build --platform linux/amd64 -f Dockerfile     -t "$ACR/dsx-worker:$TAG" .
docker push  "$ACR/dsx-worker:$TAG"

# Migrate OpenMetadata's schema before openmetadata_server ever starts.
az containerapp job start -n "$MIGRATE_JOB" -g "$RG"
az containerapp job execution list -n "$MIGRATE_JOB" -g "$RG" -o table   # poll until Succeeded

cd terraform/envs/dev
terraform apply \
  -var-file=dev.tfvars.example \
  -var="created_on=$(date -u +%F)" \
  -var="image_tag=$TAG"
```

Outputs include `service_url` and `openmetadata_url` — visit those.

## Steady-state release

The build/push/migrate/apply block above is also the regular release flow. New tag → new revision → fresh secrets fetched from KV. The migrate step is idempotent, so it's safe (and fast) to run on every release even though most releases don't touch OpenMetadata's schema.

## Forcing a secret refresh

Container Apps read KV secrets only at revision creation. If you've updated a secret in KV (via `terraform apply` of a `data` or `secrets` module change), trigger a new revision so apps pick it up. The simplest way is to retag existing images and re-apply:

```bash
OLD_TAG=$(az containerapp show -n ca-dsxlineage-dev-web -g rg-dsxlineage-dev \
  --query "properties.template.containers[0].image" -o tsv | awk -F: '{print $2}')
export TAG=$(date -u +%Y%m%d-%H%M%S)

for repo in dsx-web dsx-worker; do
  docker pull "$ACR/$repo:$OLD_TAG"
  docker tag  "$ACR/$repo:$OLD_TAG" "$ACR/$repo:$TAG"
  docker push "$ACR/$repo:$TAG"
done

terraform apply -var-file=dev.tfvars.example \
  -var="created_on=$(date -u +%F)" \
  -var="image_tag=$TAG"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `terraform init` → 403 on state container | `use_azuread_auth = true` + tenant blocks data-plane role assignment | Already removed. If you re-add it, you must self-grant `Storage Blob Data Contributor` on the state SA. |
| `terraform apply` → `Operation expired` on Container App creation | Apps trying to provision against a placeholder image that doesn't match the ingress port | Use `image_tag=bootstrap` on first apply to skip app creation entirely. |
| `terraform apply` → `roleAssignments/write` 403 | Tenant restriction on writing role assignments | Already worked around by using ACR admin auth. Don't reintroduce `azurerm_role_assignment.acr_pull`. |
| `terraform apply` → "resource already exists" on Container App | Previous failed apply left orphans | `az containerapp delete -n <name> -g rg-dsxlineage-dev --yes` then re-apply. |
| `openmetadata_server` crash-loops on `Table 'openmetadata_db.ACT_GE_PROPERTY' doesn't exist` | The migrate job hasn't run yet (or didn't succeed) against this MySQL | `az containerapp job start -n <migrate job> -g rg-dsxlineage-dev`, wait for `Succeeded` via `az containerapp job execution list`, then re-apply with the real tag. |
| `az containerapp job execution list` never reaches `Succeeded` | Migrate job failing against mysql/ES | `az containerapp job logs show -n <migrate job> -g rg-dsxlineage-dev` |
| `rediss:// URL must have parameter ssl_cert_reqs` | Celery's redis backend requires the parameter | Already fixed: `data` module's `redis_connection_url` appends `?ssl_cert_reqs=CERT_REQUIRED`. |
| Worker `FileNotFoundError: /app/uploads/...` | Web app and worker filesystems aren't shared | Already fixed: Azure Files share mounted at `/app/uploads` on both. |
| OpenAI 401 on `sk-proj-…<suffix>` | Key has been revoked (OpenAI auto-detects leaked keys) | Rotate at https://platform.openai.com/api-keys, set `TF_VAR_openai_api_key`, `terraform apply`, bump image tag to force revision refresh. |
| ACR push → `authentication required` | Token expired (~3h lifetime) | `az acr login --name "$ACR_NAME"` |

## Known dev compromises (must fix before prod)

- Postgres + Redis + Storage Account use public network access with firewall rules. Prod needs private endpoints + VNet integration.
- ACR admin user is enabled. Prod should use managed identity + `AcrPull` (requires an ops principal with `roleAssignments/write`).
- Key Vault `purge_protection_enabled = false` for easy teardown. Prod must enable it.
- No Front Door / WAF in front of public ingress.
- Container Apps scale on replica bounds only; add KEDA HTTP / Celery-queue triggers in prod.
- Backend CORS allows all origins; tighten before public exposure.
- Neo4j and OpenMetadata's Elasticsearch are single-instance, self-hosted Container Apps with no managed-service equivalent used — acceptable for a demo, but they're single points of failure with no automated backup beyond the underlying Azure Files snapshot policy (none configured, and Elasticsearch doesn't even use a persistent volume — its indices rebuild from MySQL on restart).
