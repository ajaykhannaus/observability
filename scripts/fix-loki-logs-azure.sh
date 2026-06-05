#!/usr/bin/env bash
# Redeploy Loki + OTel Collector for native OTLP log ingestion, rewire runner, refresh Grafana.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"

log() { echo "[fix-loki-logs] $*"; }

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE"; exit 1; }

if [[ -d "$ROOT/.git" ]]; then
  log "Updating repo..."
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || log "WARN: git pull failed"
fi

export ENV_FILE
export FORCE_CONTAINER_DEPLOY=true

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

log "Step 1/5 — Rebuild Loki (OTLP-ready) + OTel Collector (otlphttp/loki exporter)..."
"$ROOT/scripts/deploy-observability-stack.sh" --build --from loki --no-git-pull

log "Step 2/5 — Refresh collector backend env + restart..."
EXPECTED_LOKI="$(resolve_azure_loki_otlp_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$LOKI_APP_NAME")"
log "  LOKI_OTLP_ENDPOINT: $EXPECTED_LOKI"
refresh_collector_backends "$OTEL_APP_NAME" "$CAE_NAME" "$AZURE_RESOURCE_GROUP" \
  "$PROM_APP_NAME" "$LOKI_APP_NAME" "$TEMPO_APP_NAME"
restart_containerapp_revision "$OTEL_APP_NAME" "$AZURE_RESOURCE_GROUP" || true

log "Step 3/5 — Rebuild runner + wire OTLP (HTTP logs on :4318)..."
"$ROOT/scripts/deploy-observability-stack.sh" --from otlp --no-git-pull
"$ROOT/scripts/fix-runner.sh" --build --no-git-pull || true

OTEL_ENDPOINT="$(resolve_azure_otel_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
OTEL_LOGS_ENDPOINT="$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
log "Step 3b — Force runner OTLP env refresh: $OTEL_ENDPOINT (logs: $OTEL_LOGS_ENDPOINT)"
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}" \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=${OTEL_LOGS_ENDPOINT}" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "DEPLOY_STAMP=$(date +%s)" \
  --output none
restart_containerapp_revision "$APP_NAME" "$AZURE_RESOURCE_GROUP" || true

log "Step 4/5 — Refresh Grafana datasources + dashboards..."
python3 "$ROOT/dashboards/generate_dashboards.py"
export GRAFANA_URL GRAFANA_ADMIN_PASSWORD
# shellcheck disable=SC1090
source "$ENV_FILE"
"$ROOT/scripts/fix-grafana-datasources.sh"

log "Step 5/5 — Wait for runner batches + verify Loki (2–3 min)..."
log "  waiting 150s for OTLP logs to reach Loki..."
sleep 150
"$ROOT/scripts/diagnose-grafana-azure.sh" || true

echo ""
log "Done. In Grafana Explore (Loki), run:"
log '  {service_name=~".+"} | json | event_type="telemetry_event"'
log "Hard-refresh Dashboard 2 if panels are still empty."
