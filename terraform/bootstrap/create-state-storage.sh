#!/usr/bin/env bash
# Bootstrap the Azure Blob backend used by Terraform remote state.
# Run ONCE per subscription before the first `terraform init`.
#
# Usage:
#   ./create-state-storage.sh <env>           # e.g. dev
#
# Idempotent: re-running is safe.

set -euo pipefail

ENV="${1:?usage: $0 <env>}"
LOCATION="canadacentral"
PROJECT="dsxlineage"
RG="rg-${PROJECT}-tfstate-${ENV}"
SA="st${PROJECT}tfstate${ENV}"     # 3-24 chars, lowercase alphanumeric
CONTAINER="tfstate"
TODAY="$(date -u +%F)"

echo "==> Subscription: $(az account show --query name -o tsv)"
echo "==> Resource group: $RG"
echo "==> Storage account: $SA"

az group create \
  --name "$RG" \
  --location "$LOCATION" \
  --tags env="$ENV" project="$PROJECT" managed_by=terraform created_on="$TODAY" purpose=tfstate \
  >/dev/null

az storage account create \
  --name "$SA" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false \
  --tags env="$ENV" project="$PROJECT" managed_by=terraform created_on="$TODAY" purpose=tfstate \
  >/dev/null

az storage container create \
  --name "$CONTAINER" \
  --account-name "$SA" \
  --auth-mode key \
  >/dev/null

cat <<EOF

State backend ready. Configure terraform/envs/${ENV}/backend.tf with:

  resource_group_name  = "${RG}"
  storage_account_name = "${SA}"
  container_name       = "${CONTAINER}"
  key                  = "${ENV}.terraform.tfstate"

EOF
