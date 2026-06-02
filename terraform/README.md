# DataWise — Azure Deployment (Terraform)

Infrastructure-as-Code for the dev environment of the DataWise lineage platform on **Azure Container Apps**.

## What gets created

| Resource | Purpose | SKU (dev) |
| --- | --- | --- |
| Resource Group | All resources | — |
| Azure Container Registry | Image store, admin user enabled | Basic |
| User-assigned Managed Identity | Key Vault secret retrieval (Get/List) | — |
| Postgres Flexible Server | App database | B_Standard_B1ms |
| Azure Cache for Redis | Celery broker + result backend (TLS) | Basic C0 |
| Azure Storage Account | Shared file share for uploads | Standard LRS |
| Azure File Share | Mounted at `/app/uploads` on backend + worker | 50 GB |
| Key Vault | OpenAI key, DB password, DB URL, Redis URL | standard |
| Log Analytics Workspace | Container Apps logs | PerGB2018 |
| Container Apps Environment | Hosts the 3 apps + registers the file share | Consumption |
| Container App `ca-…-backend` | FastAPI, public ingress :8000 | 0.5 CPU / 1Gi, 1–3 replicas |
| Container App `ca-…-frontend` | nginx + Vite build, public :80 | 0.25 CPU / 0.5Gi, 1–3 replicas |
| Container App `ca-…-worker` | Celery, no ingress | 1 CPU / 2Gi, 1–5 replicas |

### Key architectural decisions

- **ACR uses admin credentials, not `AcrPull` managed identity.** The host tenant restricts `Microsoft.Authorization/roleAssignments/write`, so the cleaner role-based path isn't available. Container Apps authenticate to ACR with admin username/password supplied as a Container App secret.
- **First apply is bootstrap-only.** Apps are gated on `image_tag != "bootstrap"` so the first apply creates infra (ACR, Postgres, Redis, KV, file share, CAE) but not the three Container Apps. After images are built and pushed to ACR, a second apply with the real tag creates the apps.
- **Shared `/app/uploads` via Azure Files.** Backend writes uploaded `.dsx` files; worker reads them. The share is registered with the Container Apps Environment as a named storage (`uploads`) and mounted by both apps.
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
│   ├── providers.tf                # azurerm ~> 3.110
│   ├── variables.tf
│   ├── main.tf                     # wires the four modules
│   ├── outputs.tf
│   └── dev.tfvars.example
└── modules/
    ├── registry/   # ACR (admin) + user-assigned identity
    ├── data/       # Postgres + Redis + Storage Account + File Share
    ├── secrets/    # Key Vault + 4 secrets + access policies
    └── apps/       # CAE + CAE storage + 3 Container Apps
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

## First apply — infra only (no Container Apps yet)

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

After this, ACR / Postgres / Redis / KV / file share / CAE exist. The three Container Apps do NOT.

## Build + push + create the apps

```bash
cd ../../..   # repo root
export ACR_NAME=$(cd terraform/envs/dev && terraform output -raw acr_name)
export ACR=$(cd terraform/envs/dev && terraform output -raw acr_login_server)
az acr login --name "$ACR_NAME"

export TAG=$(date -u +%Y%m%d-%H%M%S)
echo "TAG=$TAG"

# ALL builds must target linux/amd64 — Container Apps don't run arm64.
docker build --platform linux/amd64 -t "$ACR/dsx-backend:$TAG"  ./backend
docker push   "$ACR/dsx-backend:$TAG"

# Worker is the same image as backend, retagged (TF sets the celery command at runtime).
docker tag  "$ACR/dsx-backend:$TAG" "$ACR/dsx-worker:$TAG"
docker push "$ACR/dsx-worker:$TAG"

docker build --platform linux/amd64 -t "$ACR/dsx-frontend:$TAG" ./frontend
docker push   "$ACR/dsx-frontend:$TAG"

cd terraform/envs/dev
terraform apply \
  -var-file=dev.tfvars.example \
  -var="created_on=$(date -u +%F)" \
  -var="image_tag=$TAG"
```

Outputs include `backend_fqdn` and `frontend_fqdn` — visit those URLs.

## Steady-state release

The build/push/apply block above is also the regular release flow. New tag → new revision → fresh secrets fetched from KV.

For a hot frontend-only rollout without bumping all three:

```bash
az containerapp update \
  -n ca-dsxlineage-dev-frontend \
  -g rg-dsxlineage-dev \
  --image "$ACR/dsx-frontend:$NEW_TAG"
```

Be aware: the next `terraform apply` will roll the frontend back to whatever `image_tag` you pass, so prefer the TF-driven path for anything beyond hotfixes.

## Forcing a secret refresh

Container Apps read KV secrets only at revision creation. If you've updated a secret in KV (via `terraform apply` of a `data` or `secrets` module change), trigger a new revision so apps pick it up. The simplest way is to retag existing images and re-apply:

```bash
OLD_TAG=$(az containerapp show -n ca-dsxlineage-dev-backend -g rg-dsxlineage-dev \
  --query "properties.template.containers[0].image" -o tsv | awk -F: '{print $2}')
export TAG=$(date -u +%Y%m%d-%H%M%S)

for repo in dsx-backend dsx-frontend dsx-worker; do
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
| Frontend nginx proxies fail with 404/connection-reset | `proxy_set_header Host $host` sends the frontend FQDN to backend ingress | Already fixed: nginx config sends `$proxy_host` + enables `proxy_ssl_server_name`. |
| `rediss:// URL must have parameter ssl_cert_reqs` | Celery's redis backend requires the parameter | Already fixed: `data` module's `redis_connection_url` appends `?ssl_cert_reqs=CERT_REQUIRED`. |
| Worker `FileNotFoundError: /app/uploads/...` | Backend and worker filesystems aren't shared | Already fixed: Azure Files share mounted at `/app/uploads` on both apps. |
| OpenAI 401 on `sk-proj-…<suffix>` | Key has been revoked (OpenAI auto-detects leaked keys) | Rotate at https://platform.openai.com/api-keys, set `TF_VAR_openai_api_key`, `terraform apply`, bump image tag to force revision refresh. |
| ACR push → `authentication required` | Token expired (~3h lifetime) | `az acr login --name "$ACR_NAME"` |

## Known dev compromises (must fix before prod)

- Postgres + Redis + Storage Account use public network access with firewall rules. Prod needs private endpoints + VNet integration.
- ACR admin user is enabled. Prod should use managed identity + `AcrPull` (requires an ops principal with `roleAssignments/write`).
- Key Vault `purge_protection_enabled = false` for easy teardown. Prod must enable it.
- No Front Door / WAF in front of public ingress.
- Container Apps scale on replica bounds only; add KEDA HTTP / Celery-queue triggers in prod.
- Backend CORS allows all origins; tighten before public exposure.
