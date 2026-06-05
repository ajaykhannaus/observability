#!/usr/bin/env bash
# Wire runner + collector OTLP log paths and restart — no image rebuilds (no Docker Hub pulls).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"

log() { echo "[wire-loki-otlp] $*"; }

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
LOKI_APP_NAME="${LOKI_APP_NAME:-loki-telemetry-dev}"
TEMPO_APP_NAME="${TEMPO_APP_NAME:-tempo-telemetry-dev}"
PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

OTEL_ENDPOINT="$(resolve_azure_otel_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
OTEL_LOGS_ENDPOINT="$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
EXPECTED_LOKI="$(resolve_azure_loki_otlp_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$LOKI_APP_NAME")"

log "Step 1/3 — Refresh collector backends (LOKI_OTLP_ENDPOINT=$EXPECTED_LOKI)..."
refresh_collector_backends "$OTEL_APP_NAME" "$CAE_NAME" "$AZURE_RESOURCE_GROUP" \
  "$PROM_APP_NAME" "$LOKI_APP_NAME" "$TEMPO_APP_NAME"
restart_containerapp_revision "$OTEL_APP_NAME" "$AZURE_RESOURCE_GROUP" || true

log "Step 2/3 — Refresh runner OTLP log export (:4318 HTTP)..."
log "  OTEL_EXPORTER_OTLP_ENDPOINT=$OTEL_ENDPOINT"
log "  OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_LOGS_ENDPOINT"
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}" \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=${OTEL_LOGS_ENDPOINT}" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "OTEL_SERVICE_NAME=${APP_NAME}" \
    "DEPLOY_STAMP=$(date +%s)" \
  --output none
restart_containerapp_revision "$APP_NAME" "$AZURE_RESOURCE_GROUP" || true

log "Step 3/3 — Wait 120s for batches, then verify env + diagnose..."
sleep 120

RUNNER_LOGS_EP=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
COLLECTOR_LOKI_EP=$(az containerapp show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='LOKI_OTLP_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
log "  runner OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=${RUNNER_LOGS_EP:-<unset>}"
log "  collector LOKI_OTLP_ENDPOINT=${COLLECTOR_LOKI_EP:-<unset>}"

RUNNER_LOGS=$(az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 100 2>/dev/null || true)
if echo "$RUNNER_LOGS" | grep -q "OTLP log exporter"; then
  log "  OK  runner OTLP log exporter initialized"
else
  log "  WARN: no 'OTLP log exporter' in runner logs — check runner image / env"
fi

COLLECTOR_LOGS=$(az containerapp logs show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 60 2>/dev/null || true)
if echo "$COLLECTOR_LOGS" | grep -Eiq 'otlphttp/loki.*error|error.*loki|failed.*loki'; then
  log "  WARN: collector Loki export errors:"
  echo "$COLLECTOR_LOGS" | grep -Ei 'loki|otlphttp' | grep -Ei 'error|failed|404|401' | tail -3 | sed 's/^/[wire-loki-otlp]   /'
fi

"$ROOT/scripts/diagnose-grafana-azure.sh" || true

echo ""
log "If Loki still empty, paste wire-loki output + diagnose 'Runner OTLP' and 'Collector → Loki' sections."
