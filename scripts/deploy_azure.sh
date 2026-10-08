#!/usr/bin/env bash
# DataWise -> Azure Container Apps. See DEPLOY-AZURE.md for the full guide.
#
# Usage:
#   ./scripts/deploy_azure.sh                 # build, push, apply (full deploy)
#   ./scripts/deploy_azure.sh --skip-build    # reuse IMAGE_TAG=<tag> from env, just re-apply
#   ./scripts/deploy_azure.sh --destroy       # tear down the entire resource group
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$REPO_ROOT/terraform/envs/dev"
TFVARS="$TF_DIR/dev.tfvars.example"

SKIP_BUILD=false
DESTROY=false
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    --destroy) DESTROY=true ;;
    *) echo "Unknown flag: $arg (supported: --skip-build, --destroy)" >&2; exit 1 ;;
  esac
done

# ---- Preflight ------------------------------------------------------------
for bin in az terraform docker; do
  command -v "$bin" >/dev/null 2>&1 || { echo "Missing required tool: $bin" >&2; exit 1; }
done

az account show >/dev/null 2>&1 || { echo "Not logged into Azure. Run: az login" >&2; exit 1; }

# Sensitive vars: shell env wins, then .env, then error. Never sourced from
# dev.tfvars.example — that file is committed and non-sensitive-only.
if [ -z "${TF_VAR_openrouter_api_key:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  # `|| true` matters: under `set -euo pipefail`, a `.env` with no matching
  # line makes grep exit 1, which kills the whole script right here — before
  # the check below ever runs — silently.
  TF_VAR_openrouter_api_key="$(grep -E '^OPENROUTER_API_KEY=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2- || true)"
  export TF_VAR_openrouter_api_key
fi
if [ -z "${TF_VAR_openrouter_api_key:-}" ]; then
  echo "No OpenRouter API key found. Export TF_VAR_openrouter_api_key, or set OPENROUTER_API_KEY in .env." >&2
  exit 1
fi

if [ -z "${TF_VAR_postgres_admin_password:-}" ]; then
  export TF_VAR_postgres_admin_password="$(openssl rand -base64 24)"
  echo "Note: generated a fresh Postgres admin password for this apply."
  echo "      Only takes effect if the Postgres server doesn't exist yet — Azure ignores"
  echo "      administrator_password changes on an existing flexible server."
fi

CREATED_ON="$(date -u +%F)"

if $DESTROY; then
  echo "==> Destroying the entire dev resource group. This cannot be undone."
  read -r -p "    Type the resource group name to confirm: " CONFIRM
  cd "$TF_DIR"
  terraform init -upgrade >/dev/null
  RG="$(terraform output -raw resource_group_name 2>/dev/null || true)"
  if [ -z "$RG" ] || [ "$CONFIRM" != "$RG" ]; then
    echo "Confirmation did not match resource group name ('$RG'). Aborting." >&2
    exit 1
  fi
  terraform destroy \
    -var-file="$TFVARS" \
    -var="created_on=$CREATED_ON" \
    -var="image_tag=bootstrap"
  exit 0
fi

# ---- Pass 1: bootstrap (infra only — no image-backed apps yet) ----------
cd "$TF_DIR"
terraform init -upgrade

echo "==> Bootstrap apply: ACR, Postgres, Redis, Key Vault, Neo4j, OpenMetadata mysql/ES..."
terraform apply -auto-approve \
  -var-file="$TFVARS" \
  -var="created_on=$CREATED_ON" \
  -var="image_tag=bootstrap"

ACR_NAME="$(terraform output -raw acr_name)"
ACR="$(terraform output -raw acr_login_server)"
RG="$(terraform output -raw resource_group_name)"
MIGRATE_JOB="$(terraform output -raw openmetadata_migrate_job_name)"

cd "$REPO_ROOT"

# ---- Build + push ----------------------------------------------------------
if ! $SKIP_BUILD; then
  TAG="$(date -u +%Y%m%d-%H%M%S)"
  echo "==> Building images (linux/amd64) — tag $TAG..."
  docker build --platform linux/amd64 -f Dockerfile.web -t "$ACR/dsx-web:$TAG"    .
  docker build --platform linux/amd64 -f Dockerfile     -t "$ACR/dsx-worker:$TAG" .

  echo "==> Pushing to $ACR..."
  az acr login --name "$ACR_NAME"
  docker push "$ACR/dsx-web:$TAG"
  docker push "$ACR/dsx-worker:$TAG"
else
  TAG="${IMAGE_TAG:?--skip-build requires IMAGE_TAG=<existing pushed tag> in env}"
  echo "==> Skipping build, reusing images tagged $TAG"
fi

# ---- OpenMetadata schema migration (idempotent, safe to rerun) -----------
echo "==> Running OpenMetadata schema migration against mysql..."
az containerapp job start -n "$MIGRATE_JOB" -g "$RG" >/dev/null

echo "    waiting for the migration job execution to finish..."
STATUS="Unknown"
for i in $(seq 1 60); do
  STATUS="$(az containerapp job execution list -n "$MIGRATE_JOB" -g "$RG" -o json | python3 -c '
import json, sys
execs = json.load(sys.stdin)
if not execs:
    print("Pending"); sys.exit()
def started(e):
    return e.get("properties", {}).get("startTime") or e.get("startTime") or ""
latest = sorted(execs, key=started)[-1]
print(latest.get("properties", {}).get("status") or latest.get("status") or "Unknown")
')"
  echo "    [$i/60] migrate job status: $STATUS"
  case "$STATUS" in
    Succeeded) break ;;
    Failed)
      echo "Migration job failed. Inspect with:" >&2
      echo "  az containerapp job logs show -n $MIGRATE_JOB -g $RG" >&2
      exit 1
      ;;
  esac
  sleep 10
done
if [ "$STATUS" != "Succeeded" ]; then
  echo "Timed out waiting for the migration job to complete." >&2
  exit 1
fi

# ---- Pass 2: release (creates/updates web, worker, openmetadata_server) --
cd "$TF_DIR"
echo "==> Release apply (image_tag=$TAG)..."
terraform apply -auto-approve \
  -var-file="$TFVARS" \
  -var="created_on=$CREATED_ON" \
  -var="image_tag=$TAG"

echo
echo "Deployed."
echo "  App:          $(terraform output -raw service_url)"
echo "  OpenMetadata: $(terraform output -raw openmetadata_url)"
