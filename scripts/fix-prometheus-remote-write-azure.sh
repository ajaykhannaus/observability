#!/usr/bin/env bash
# Rebuild prometheus-scraper with --web.enable-remote-write-receiver and deploy by digest.
# Fixes OTel Collector prometheusremotewrite 404 errors on Azure sandbox Prometheus.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"

log() { echo "[fix-prometheus-rw] $*"; }

if [[ -d "$ROOT/.git" ]]; then
  log "Updating repo..."
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || { log "ERROR: git pull failed"; exit 1; }
  log "  commit: $(git -C "$ROOT" rev-parse --short HEAD)"
fi

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE"; exit 1; }
grep -q 'web.enable-remote-write-receiver' "$ROOT/azure/prometheus-entrypoint.sh" \
  || { log "ERROR: azure/prometheus-entrypoint.sh missing --web.enable-remote-write-receiver — git pull"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $ENV_FILE}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $ENV_FILE}"

ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"
PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
IMAGE_REPO="prometheus-scraper"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv)}"

RUNNER_FQDN=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
[[ -n "$RUNNER_FQDN" ]] || { log "ERROR: Runner $APP_NAME has no FQDN"; exit 1; }

log "Step 1/3 — ACR build prometheus-scraper (cache-bust, ~3–8 min)..."
az acr build --registry "$ACR_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --platform linux/amd64 \
  --build-arg "CACHEBUST=$(date +%s)" \
  --image "${IMAGE_REPO}:latest" \
  -f "$ROOT/Dockerfile.prometheus" "$ROOT"

DIGEST=$(az acr repository show-manifests --name "$ACR_NAME" \
  --repository "$IMAGE_REPO" --orderby time_desc --top 1 \
  --query "[0].digest" -o tsv)
[[ -n "$DIGEST" && "$DIGEST" != "None" ]] || { log "ERROR: could not read image digest from ACR"; exit 1; }
IMAGE_BY_DIGEST="${ACR_LOGIN_SERVER}/${IMAGE_REPO}@${DIGEST}"
log "  image digest: $DIGEST"

log "Step 2/3 — Deploy Prometheus by digest (forces new revision)..."
prometheus_deploy_sandbox "$PROM_APP_NAME" "${CAE_NAME:-cae-telemetry-dev}" \
  "$AZURE_RESOURCE_GROUP" "$ACR_NAME" "$ACR_LOGIN_SERVER" "$RUNNER_FQDN" "$IMAGE_BY_DIGEST"

PROM_FQDN=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)

log "Step 3/3 — Wait for Prometheus + verify remote write receiver..."
for i in $(seq 1 24); do
  prov=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.provisioningState" -o tsv 2>/dev/null || echo "")
  run=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.runningStatus" -o tsv 2>/dev/null || echo "")
  if [[ "$prov" == "Succeeded" && "$run" == "Running" && -n "$PROM_FQDN" ]]; then
    code=$(curl -sk -o /dev/null -w '%{http_code}' -X POST \
      "https://${PROM_FQDN}/api/v1/write" --max-time 15 2>/dev/null || echo "000")
    if [[ "$code" != "404" && "$code" != "000" ]]; then
      log "OK  Prometheus remote write receiver active (POST /api/v1/write → HTTP $code)"
      echo ""
      log "Done. Re-run: ./scripts/diagnose-grafana-azure.sh"
      log "Collector prometheusremotewrite 404s should stop within 1–2 minutes."
      exit 0
    fi
    (( i % 4 == 0 )) && log "  waiting ($i/24) — prov=$prov run=$run rw_http=$code"
  fi
  sleep 10
done

log "WARN: Could not confirm remote write receiver from this host."
log "  If POST /api/v1/write still returns 404 internally, check prometheus console logs:"
az containerapp logs show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 20 2>/dev/null || true
exit 1
