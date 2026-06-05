#!/usr/bin/env bash
# Rebuild Grafana image (baked dashboards) and redeploy on Azure Container Apps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"

log() { echo "[rebuild-grafana] $*"; }

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $ENV_FILE}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $ENV_FILE}"

ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

log "Regenerating dashboard JSON (per-dashboard filter sets) ..."
python3 "$ROOT/dashboards/generate_dashboards.py"

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv)}"

log "Building $ACR_NAME/grafana:latest (baked dashboard UIDs) ..."
acr_build_image "$ACR_NAME" "$AZURE_RESOURCE_GROUP" "$ACR_LOGIN_SERVER" \
  "grafana:latest" "$ROOT/Dockerfile.grafana" "$ROOT"

log "Redeploying Grafana Container App ..."
export FORCE_CONTAINER_DEPLOY=true
export BOOTSTRAP_CONFIG="${BOOTSTRAP_CONFIG:-$ROOT/azure/bootstrap-azure.sandbox.env}"
export WRITE_ENV_FILE="$ENV_FILE"
"$ROOT/scripts/bootstrap-azure.sh" --grafana-only --no-build

log "Waiting 30s for Azure provisioning lock to clear ..."
sleep 30

log "Configuring datasources + dashboards ..."
export SKIP_GRAFANA_ENV_UPDATE="${SKIP_GRAFANA_ENV_UPDATE:-false}"
"$ROOT/scripts/fix-grafana-datasources.sh"

log "Grafana rebuild complete."
