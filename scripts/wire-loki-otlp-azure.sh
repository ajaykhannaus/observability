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

log "Step 3/3 — Wait 120s for batches, then diagnose..."
sleep 120
"$ROOT/scripts/diagnose-grafana-azure.sh" || true

echo ""
log "If Loki still empty, check diagnose sections:"
log "  - Runner console — OTLP log exporter"
log "  - OTel Collector → Loki wiring"
log "  - Collector export errors (loki/otlphttp)"
