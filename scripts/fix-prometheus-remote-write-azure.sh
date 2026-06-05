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
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
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
acr_build_image "$ACR_NAME" "$AZURE_RESOURCE_GROUP" "$ACR_LOGIN_SERVER" \
  "${IMAGE_REPO}:latest" "$ROOT/Dockerfile.prometheus" "$ROOT"

DIGEST=$(az acr repository show-manifests --name "$ACR_NAME" \
  --repository "$IMAGE_REPO" --orderby time_desc --top 1 \
  --query "[0].digest" -o tsv)
[[ -n "$DIGEST" && "$DIGEST" != "None" ]] || { log "ERROR: could not read image digest from ACR"; exit 1; }
IMAGE_BY_DIGEST="${ACR_LOGIN_SERVER}/${IMAGE_REPO}@${DIGEST}"
log "  image digest: $DIGEST"

log "Step 2/3 — Deploy Prometheus by digest + force entrypoint (clears stale /bin/prometheus args)..."
BEFORE_CMD=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].{command:command,args:args}" -o json 2>/dev/null || echo "{}")
log "  before: $BEFORE_CMD"
prometheus_deploy_sandbox "$PROM_APP_NAME" "${CAE_NAME:-cae-telemetry-dev}" \
  "$AZURE_RESOURCE_GROUP" "$ACR_NAME" "$ACR_LOGIN_SERVER" "$RUNNER_FQDN" "$IMAGE_BY_DIGEST"
AFTER_CMD=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].{command:command,args:args}" -o json 2>/dev/null || echo "{}")
log "  after:  $AFTER_CMD"

PROM_FQDN=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)

log "Step 3/4 — Wait for Prometheus ready..."
PROM_READY=false
for i in $(seq 1 30); do
  prov=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.provisioningState" -o tsv 2>/dev/null || echo "")
  run=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.runningStatus" -o tsv 2>/dev/null || echo "")
  prom_logs=$(prometheus_console_logs "$PROM_APP_NAME" "$AZURE_RESOURCE_GROUP" 40)
  if [[ "$prov" == "Succeeded" && "$run" == "Running" ]]; then
    if prometheus_health_ok "$PROM_FQDN" "$prom_logs"; then
      PROM_READY=true
      log "OK  Prometheus healthy (/-/ready or console logs)"
      break
    fi
    if [[ "$i" -ge 12 ]] && echo "$AFTER_CMD" | grep -q 'prometheus-entrypoint'; then
      PROM_READY=true
      log "OK  Prometheus Running with /prometheus-entrypoint.sh (≥2 min; log probe inconclusive from Cloud Shell)"
      break
    fi
  fi
  ready_code="n/a"
  [[ -n "$PROM_FQDN" ]] && ready_code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
    "https://${PROM_FQDN}/-/ready" 2>/dev/null || echo "000")
  (( i % 4 == 0 )) && log "  waiting ($i/30) — prov=$prov run=$run ready_http=$ready_code"
  sleep 10
done

if [[ "$PROM_READY" != "true" ]]; then
  log "WARN: Prometheus not confirmed healthy:"
  prometheus_console_logs "$PROM_APP_NAME" "$AZURE_RESOURCE_GROUP" 25 | sed 's/^/[fix-prometheus-rw]   /' || true
  exit 1
fi

CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
EXPECTED_PROM_EP="$(resolve_azure_prom_write_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$PROM_APP_NAME")"
COLLECTOR_PROM_EP=$(az containerapp show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='PROM_WRITE_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)

log "Step 4/4 — Refresh collector PROM_WRITE_ENDPOINT + restart (clears permanent exporter error)..."
log "  expected PROM_WRITE_ENDPOINT: $EXPECTED_PROM_EP"
log "  current PROM_WRITE_ENDPOINT:  ${COLLECTOR_PROM_EP:-<unset>}"
if [[ -n "$EXPECTED_PROM_EP" && "$COLLECTOR_PROM_EP" != "$EXPECTED_PROM_EP" ]]; then
  az containerapp update \
    --name "$OTEL_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --set-env-vars \
      "PROM_WRITE_ENDPOINT=${EXPECTED_PROM_EP}" \
      "DEPLOY_STAMP=$(date +%s)" \
    --output none
else
  az containerapp update \
    --name "$OTEL_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --set-env-vars "DEPLOY_STAMP=$(date +%s)" \
    --output none
fi
restart_containerapp_revision "$OTEL_APP_NAME" "$AZURE_RESOURCE_GROUP" || true

log "  waiting 90s for collector to reconnect..."
sleep 90

COLLECTOR_LOGS=$(az containerapp logs show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 40 2>/dev/null || true)
if collector_prom_rw_failing_recent "$COLLECTOR_LOGS" 10; then
  log "WARN: Collector still reports recent Prometheus remote write 404:"
  echo "$COLLECTOR_LOGS" | grep 'remote write returned HTTP status 404' | tail -3 | sed 's/^/[fix-prometheus-rw]   /'
  exit 1
fi

echo ""
log "SUCCESS — Prometheus redeployed; collector restarted without recent remote-write 404s."
log "Re-run: ./scripts/diagnose-grafana-azure.sh"
