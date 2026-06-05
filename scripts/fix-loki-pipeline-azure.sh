#!/usr/bin/env bash
# Rebuild Loki + OTel Collector by digest, wire runner OTLP logs, verify Loki ingestion.
# Use when wire-loki-otlp-azure.sh leaves Loki empty (env-only fix is not enough).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"
RUNNER_BUILD=false

usage() {
  cat <<EOF
Usage: $0 [--runner-build] [--no-git-pull]

  Rebuilds Loki (OTLP config) and OTel Collector (otlphttp/loki), deploys by digest,
  refreshes OTLP env on runner + collector, restarts apps, then diagnoses.

  --runner-build  Also rebuild ai-telemetry-runner (needed if runner lacks OTLP log exporter)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --runner-build) RUNNER_BUILD=true ;;
    --no-git-pull) ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

log() { echo "[fix-loki-pipeline] $*"; }

if [[ "$*" != *--no-git-pull* && -d "$ROOT/.git" ]]; then
  log "Updating repo..."
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || log "WARN: git pull failed"
  log "  commit: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $ENV_FILE}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $ENV_FILE}"

ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"
CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
LOKI_APP_NAME="${LOKI_APP_NAME:-loki-telemetry-dev}"
TEMPO_APP_NAME="${TEMPO_APP_NAME:-tempo-telemetry-dev}"
PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"
AZURE_LOCATION="${AZURE_LOCATION:-eastus}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv)}"

log "Step 0/6 — Triage before rebuild..."
triage_loki_pipeline "$APP_NAME" "$OTEL_APP_NAME" "$LOKI_APP_NAME" \
  "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "[fix-loki-pipeline]" || true

deploy_acr_app_by_digest() {
  local app_name=$1 template=$2 repo=$3 dockerfile=$4
  local digest image_ref rendered env_id user pass

  log "  ACR build ${repo}:latest (~3–10 min)..."
  acr_build_image "$ACR_NAME" "$AZURE_RESOURCE_GROUP" "$ACR_LOGIN_SERVER" \
    "${repo}:latest" "$dockerfile" "$ROOT"

  digest=$(acr_latest_digest "$ACR_NAME" "$repo") \
    || { log "ERROR: could not read digest for ${repo}"; exit 1; }
  image_ref="${ACR_LOGIN_SERVER}/${repo}@${digest}"
  log "  ${repo} digest: ${digest}"

  rendered="$ROOT/infra/${repo}-pipeline.rendered.yaml"
  env_id=$(az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
  acr_admin_credentials "$ACR_NAME"
  user="$(awk_escape "$ACR_ADMIN_USER")"
  pass="$(awk_escape "$ACR_ADMIN_PASS")"

  if [[ "$repo" == "otel-collector" ]]; then
    local tempo_ep loki_ep prom_ep
    tempo_ep="http://${TEMPO_APP_NAME}.internal.$(cae_default_domain "$CAE_NAME" "$AZURE_RESOURCE_GROUP"):4317"
    loki_ep="$(resolve_azure_loki_otlp_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$LOKI_APP_NAME")"
    prom_ep="$(resolve_azure_prom_write_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$PROM_APP_NAME")"
    awk -v loc="$AZURE_LOCATION" \
        -v env_id="$env_id" \
        -v acr_server="$ACR_LOGIN_SERVER" \
        -v acr_user="$user" \
        -v acr_pass="$pass" \
        -v image="$image_ref" \
        -v tempo_ep="$tempo_ep" \
        -v loki_ep="$loki_ep" \
        -v prom_ep="$prom_ep" \
        '{
          gsub(/__LOCATION__/, loc)
          gsub(/__MANAGED_ENV_ID__/, env_id)
          gsub(/__ACR_LOGIN_SERVER__/, acr_server)
          gsub(/__ACR_USERNAME__/, acr_user)
          gsub(/__ACR_ADMIN_PASSWORD__/, acr_pass)
          gsub(/__IMAGE__/, image)
          gsub(/__TEMPO_ENDPOINT__/, tempo_ep)
          gsub(/__LOKI_OTLP_ENDPOINT__/, loki_ep)
          gsub(/__PROM_WRITE_ENDPOINT__/, prom_ep)
          print
        }' "$template" > "$rendered"
  else
    awk -v loc="$AZURE_LOCATION" \
        -v env_id="$env_id" \
        -v acr_server="$ACR_LOGIN_SERVER" \
        -v acr_user="$user" \
        -v acr_pass="$pass" \
        -v image="$image_ref" \
        '{
          gsub(/__LOCATION__/, loc)
          gsub(/__MANAGED_ENV_ID__/, env_id)
          gsub(/__ACR_LOGIN_SERVER__/, acr_server)
          gsub(/__ACR_USERNAME__/, acr_user)
          gsub(/__ACR_ADMIN_PASSWORD__/, acr_pass)
          gsub(/__IMAGE__/, image)
          print
        }' "$template" > "$rendered"
  fi

  if az containerapp show --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    az containerapp update --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$rendered"
  else
    az containerapp create --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$rendered"
  fi
  rm -f "$rendered"
}

log "Step 1/6 — Rebuild + deploy Loki by digest..."
deploy_acr_app_by_digest "$LOKI_APP_NAME" "$ROOT/infra/loki-acr-admin.template.yaml" \
  "loki" "$ROOT/Dockerfile.loki"
wait_for_app_running() {
  local name=$1 label=$2 i prov run
  for i in $(seq 1 30); do
    prov=$(az containerapp show --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" \
      --query "properties.provisioningState" -o tsv 2>/dev/null || echo "")
    run=$(az containerapp show --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" \
      --query "properties.runningStatus" -o tsv 2>/dev/null || echo "")
    if [[ "$prov" == "Succeeded" && "$run" == "Running" ]]; then
      log "  OK  $label Running"
      return 0
    fi
    (( i % 4 == 0 )) && log "  waiting for $label ($i/30)..."
    sleep 10
  done
  log "  WARN: $label not confirmed Running"
  return 1
}
wait_for_app_running "$LOKI_APP_NAME" "Loki" || true

log "Step 2/6 — Rebuild + deploy OTel Collector by digest..."
deploy_acr_app_by_digest "$OTEL_APP_NAME" "$ROOT/infra/otel-collector-acr-admin.template.yaml" \
  "otel-collector" "$ROOT/Dockerfile.collector"
wait_for_app_running "$OTEL_APP_NAME" "OTel Collector" || true

log "Step 3/6 — Refresh collector backends + restart..."
refresh_collector_backends "$OTEL_APP_NAME" "$CAE_NAME" "$AZURE_RESOURCE_GROUP" \
  "$PROM_APP_NAME" "$LOKI_APP_NAME" "$TEMPO_APP_NAME"
restart_containerapp_revision "$OTEL_APP_NAME" "$AZURE_RESOURCE_GROUP" || true

log "Step 4/6 — Wire runner OTLP log export (:4318 HTTP)..."
OTEL_ENDPOINT="$(resolve_azure_otel_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
OTEL_LOGS_ENDPOINT="$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
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

if [[ "$RUNNER_BUILD" == "true" ]]; then
  log "Step 4b/6 — Rebuild runner (OTLP log exporter in image)..."
  "$ROOT/scripts/fix-runner-now.sh" || log "WARN: fix-runner-now failed — continuing"
fi

log "Step 5/6 — Wait 150s for OTLP logs..."
sleep 150

log "Step 6/6 — Triage + diagnose..."
triage_loki_pipeline "$APP_NAME" "$OTEL_APP_NAME" "$LOKI_APP_NAME" \
  "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "[fix-loki-pipeline]" || true
"$ROOT/scripts/diagnose-grafana-azure.sh" || true

echo ""
log "Done. In Grafana Explore (Loki):"
log '  {service_name=~".+"} | json | event_type="telemetry_event"'
log "If still 0 streams, re-run with: ./scripts/fix-loki-pipeline-azure.sh --runner-build"
