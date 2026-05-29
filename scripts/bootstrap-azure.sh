#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${BOOTSTRAP_CONFIG:-$ROOT/azure/bootstrap-azure.env}"
SKIP_BUILD=false
CLI_PREFLIGHT=false

usage() {
  cat <<EOF
Usage: $0 [--preflight] [--no-build]

  Config: azure/bootstrap-azure.env
  Output: .env.azure (copy to .env)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --preflight) CLI_PREFLIGHT=true ;;
    --no-build)  SKIP_BUILD=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

log() { echo "[bootstrap-azure] $*"; }

[[ -f "$CONFIG" ]] || {
  echo "ERROR: Missing $CONFIG" >&2
  echo "       cp azure/bootstrap-azure.env.example azure/bootstrap-azure.env" >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$CONFIG"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $CONFIG}"

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$AZURE_SUBSCRIPTION_ID" == "00000000-0000-0000-0000-000000000000" ]]; then
  if [[ -n "${AZURE_SUBSCRIPTION_NAME:-}" ]]; then
    AZURE_SUBSCRIPTION_ID=$(az account list \
      --query "[?name=='$AZURE_SUBSCRIPTION_NAME'].id | [0]" -o tsv 2>/dev/null || true)
    [[ -n "$AZURE_SUBSCRIPTION_ID" ]] || {
      echo "ERROR: Subscription '$AZURE_SUBSCRIPTION_NAME' not found." >&2
      exit 1
    }
    log "subscription $AZURE_SUBSCRIPTION_NAME → $AZURE_SUBSCRIPTION_ID"
  else
    echo "ERROR: Set AZURE_SUBSCRIPTION_ID or AZURE_SUBSCRIPTION_NAME in $CONFIG" >&2
    exit 1
  fi
fi

USE_EXISTING_RG="${USE_EXISTING_RG:-true}"
PROVISION_OBSERVABILITY="${PROVISION_OBSERVABILITY:-true}"
PROVISION_ADX="${PROVISION_ADX:-true}"
BUILD_IMAGES="${BUILD_IMAGES:-true}"
WRITE_ENV_FILE="${WRITE_ENV_FILE:-.env.azure}"
PREFLIGHT="${PREFLIGHT:-false}"
ADX_CLUSTER="${ADX_CLUSTER:-adxtelemetrydev}"
ADX_DATABASE="${ADX_DATABASE:-observability}"
ADX_ENV="${ADX_ENV:-dev}"
[[ "$CLI_PREFLIGHT" == "true" ]] && PREFLIGHT=true
[[ "$SKIP_BUILD" == "true" ]] && BUILD_IMAGES=false

ACR_NAME="${ACR_NAME:-acrtelemetrydev}"
CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"
GRAFANA_NAME="${GRAFANA_NAME:-grafana-telemetry-dev}"
PROM_WS="${PROM_WS:-telemetry-prometheus-dev}"
EH_NS="${EH_NS:-evhns-telemetry-dev}"
EH_NAME="${EVENTHUB_NAME:-ai-telemetry-events}"
AZURE_LOCATION="${AZURE_LOCATION:-eastus}"

[[ "$WRITE_ENV_FILE" != /* ]] && WRITE_ENV_FILE="$ROOT/$WRITE_ENV_FILE"

if ! az account show >/dev/null 2>&1; then
  if [[ -n "${AZURE_CLIENT_ID:-}" && -n "${AZURE_CLIENT_SECRET:-}" && -n "${AZURE_TENANT_ID:-}" ]]; then
    log "service principal login"
    az login --service-principal \
      -u "$AZURE_CLIENT_ID" \
      -p "$AZURE_CLIENT_SECRET" \
      --tenant "$AZURE_TENANT_ID" \
      --output none
  else
    echo "ERROR: Not logged in. Use Azure Cloud Shell or set AZURE_CLIENT_* in $CONFIG." >&2
    exit 1
  fi
fi

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

SUB_NAME=$(az account show --query name -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

echo ""
echo "Azure bootstrap"
echo "  subscription : $SUB_NAME"
echo "  tenant       : $TENANT_ID"
echo "  rg           : $AZURE_RESOURCE_GROUP"
echo "  acr          : $ACR_NAME"
echo "  cae          : $CAE_NAME"
echo "  eventhub     : $EH_NS"
echo "  output       : $WRITE_ENV_FILE"
echo "  mode         : $([[ "$PREFLIGHT" == "true" ]] && echo preflight || echo apply)"
echo ""

[[ "$USE_EXISTING_RG" == "true" ]] && \
  AZURE_LOCATION=$(az group show --name "$AZURE_RESOURCE_GROUP" --query location -o tsv)

BOOTSTRAP_ARGS=(
  --resource-group "$AZURE_RESOURCE_GROUP"
  --location       "$AZURE_LOCATION"
  --acr-name       "$ACR_NAME"
  --cae-name       "$CAE_NAME"
  --app-name       "$APP_NAME"
  --eventhub-ns    "$EH_NS"
  --eventhub-name  "$EH_NAME"
)
[[ "$USE_EXISTING_RG" == "true" ]] && BOOTSTRAP_ARGS+=(--use-existing-rg)

if [[ "$PREFLIGHT" == "true" ]]; then
  chmod +x "$ROOT/infra/bootstrap.sh"
  "$ROOT/infra/bootstrap.sh" "${BOOTSTRAP_ARGS[@]}" --preflight
  log "preflight ok"
  exit 0
fi

log "core infra"
chmod +x "$ROOT/infra/bootstrap.sh"
"$ROOT/infra/bootstrap.sh" \
  "${BOOTSTRAP_ARGS[@]}" \
  --write-env "$WRITE_ENV_FILE" \
  --skip-print-secrets

PROM_REMOTE_WRITE_URL=""
AZURE_PROM_QUERY_URL=""
GRAFANA_URL=""

if [[ "$PROVISION_OBSERVABILITY" == "true" ]]; then
  log "observability"
  for ns in Microsoft.Monitor Microsoft.Dashboard; do
    state=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
    if [[ "$state" != "Registered" ]]; then
      if ! az provider register --namespace "$ns" --output none 2>/dev/null; then
        log "WARNING: cannot register $ns (insufficient subscription-level permissions)."
        log "  Ask a subscription Owner to run: az provider register --namespace $ns"
        log "  Skipping Azure Monitor / Managed Grafana provisioning."
        log "  Self-hosted Grafana Container App will still be deployed."
        PROVISION_OBSERVABILITY=false
        break
      fi
    fi
  done
fi

# Re-check: provider registration may have set this to false above.
if [[ "$PROVISION_OBSERVABILITY" == "true" ]]; then
  if ! az monitor account show --name "$PROM_WS" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    az monitor account create \
      --name "$PROM_WS" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      --output none
  fi

  AZURE_PROM_QUERY_URL=$(az monitor account show \
    --name "$PROM_WS" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "metrics.prometheusQueryEndpoint" -o tsv)

  PROM_WORKSPACE_ID=$(az monitor account show \
    --name "$PROM_WS" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)

  MANAGED_RG="MA_${PROM_WS}_${AZURE_LOCATION}_managed"
  DCR_ID=""
  DCE=""
  for _ in $(seq 1 18); do
    DCR_ID=$(az monitor data-collection rule list \
      --resource-group "$MANAGED_RG" \
      --query "[0].immutableId" -o tsv 2>/dev/null || true)
    DCE=$(az monitor data-collection endpoint list \
      --resource-group "$MANAGED_RG" \
      --query "[0].properties.logsIngestion.endpoint" -o tsv 2>/dev/null || true)
    [[ -n "$DCR_ID" && -n "$DCE" ]] && break
    sleep 10
  done

  if [[ -n "$DCR_ID" && -n "$DCE" ]]; then
    PROM_REMOTE_WRITE_URL="${DCE}/dataCollectionRules/${DCR_ID}/streamName/Microsoft-PrometheusMetrics/api/v1/write?api-version=2023-04-24"
  else
    log "WARN: PROM_REMOTE_WRITE_URL not detected"
  fi

  az extension add --name amg --upgrade --yes --output none 2>/dev/null || true
  if ! az grafana show --name "$GRAFANA_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    az grafana create \
      --name "$GRAFANA_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      --sku Standard \
      --output none
  fi

  az grafana integrations add \
    --name "$GRAFANA_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --workspace-id "$PROM_WORKSPACE_ID" \
    --output none 2>/dev/null || true

  GRAFANA_URL=$(az grafana show --name "$GRAFANA_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.endpoint" -o tsv)
fi

ADX_CLUSTER_URI=""
if [[ "$PROVISION_ADX" == "true" ]]; then
  log "adx database"
  chmod +x "$ROOT/infra/adx-data-connection.sh"
  ADX_OUT=$("$ROOT/infra/adx-data-connection.sh" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" \
    --cluster-name "$ADX_CLUSTER" \
    --db-name "$ADX_DATABASE" \
    --eventhub-ns "$EH_NS" \
    --eventhub-name "$EH_NAME" \
    --env "$ADX_ENV" 2>&1) || true
  echo "$ADX_OUT"
  ADX_CLUSTER_URI=$(echo "$ADX_OUT" | awk -F= '/^  ADX_CLUSTER_URI=/ {print $2; exit}')
  [[ -z "$ADX_CLUSTER_URI" ]] && ADX_CLUSTER_URI=$(az kusto cluster show \
    --name "$ADX_CLUSTER" --resource-group "$AZURE_RESOURCE_GROUP" --query uri -o tsv 2>/dev/null || true)
fi

{
  echo ""
  echo "PROM_WS=$PROM_WS"
  echo "PROM_APP_NAME=$PROM_APP_NAME"
  echo "GRAFANA_NAME=$GRAFANA_NAME"
  [[ -n "$PROM_REMOTE_WRITE_URL" ]] && echo "PROM_REMOTE_WRITE_URL=$PROM_REMOTE_WRITE_URL"
  [[ -n "$AZURE_PROM_QUERY_URL" ]] && echo "AZURE_PROM_QUERY_URL=$AZURE_PROM_QUERY_URL"
  [[ -n "$GRAFANA_URL" ]] && echo "GRAFANA_URL=$GRAFANA_URL"
  [[ -n "$ADX_CLUSTER_URI" ]] && echo "ADX_CLUSTER_URI=$ADX_CLUSTER_URI"
  [[ -n "$ADX_DATABASE" ]] && echo "ADX_DATABASE=$ADX_DATABASE"
  echo "OBS_APP_ID=$APP_NAME"
  echo ""
  echo "OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317"
  echo "OTEL_SERVICE_NAME=$APP_NAME"
  echo "OTEL_EXPORT_INTERVAL_MS=30000"
  echo "ENVIRONMENT=dev"
  echo "ALLOW_MOCK_MODE=true"
  echo "PROMETHEUS_PORT=8000"
  echo "HEALTH_PORT=8080"
  echo "BATCH_INTERVAL_S=5"
  echo "BASE_BATCH_SIZE=8"
  echo "ERROR_WINDOW_PROB=0.03"
  echo "ERROR_WINDOW_MIN_S=90"
  echo "ERROR_WINDOW_MAX_S=180"
  echo "SIMULATE_LATENCY=false"
  echo "PII_BACKEND=auto"
  echo "PROMPT_LOG_ENABLED=true"
  echo "EVAL_ENABLED=false"
} >> "$WRITE_ENV_FILE"

acr_image_exists() {
  az acr repository show --name "$ACR_NAME" --image "$1" >/dev/null 2>&1
}

build_image_if_missing() {
  local tag=$1 dockerfile=$2
  if [[ "${FORCE_IMAGE_BUILD:-false}" != "true" ]] && acr_image_exists "$tag"; then
    log "reuse $ACR_NAME/$tag — skipping build"
    return 0
  fi
  log "building $ACR_NAME/$tag"
  az acr build --registry "$ACR_NAME" --platform linux/amd64 \
    --image "$tag" -f "$dockerfile" "$ROOT"
}

if [[ "$BUILD_IMAGES" == "true" ]]; then
  build_image_if_missing "ai-telemetry-runner:latest" "$ROOT/Dockerfile.runner"
  build_image_if_missing "prometheus-scraper:latest" "$ROOT/Dockerfile.prometheus"
  build_image_if_missing "grafana:latest" "$ROOT/Dockerfile.grafana"
fi

# ── Self-hosted Grafana Container App ────────────────────────────────────────
deploy_grafana() {
  local grafana_app="${GRAFANA_APP_NAME:-grafana-telemetry-dev}"
  local grafana_image="$ACR_LOGIN_SERVER/grafana:latest"
  local admin_pass="${GRAFANA_ADMIN_PASSWORD:-admin}"

  local prom_fqdn
  prom_fqdn=$(az containerapp show \
    --name "$PROM_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)

  local prom_url="http://prometheus:9090"
  [[ -n "$prom_fqdn" ]] && prom_url="https://${prom_fqdn}"

  local env_id
  env_id=$(az containerapp env show \
    --name "$CAE_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --query id -o tsv)

  local rendered="$ROOT/infra/grafana.rendered.yaml"
  sed \
    -e "s|__LOCATION__|${AZURE_LOCATION}|g" \
    -e "s|__MANAGED_ENV_ID__|${env_id}|g" \
    -e "s|__ACR_LOGIN_SERVER__|${ACR_LOGIN_SERVER}|g" \
    -e "s|__IMAGE__|${grafana_image}|g" \
    -e "s|__PROMETHEUS_URL__|${prom_url}|g" \
    -e "s|__GRAFANA_ADMIN_PASSWORD__|${admin_pass}|g" \
    "$ROOT/infra/grafana.template.yaml" > "$rendered"

  if az containerapp show --name "$grafana_app" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    log "grafana: update $grafana_app"
    az containerapp update \
      --name "$grafana_app" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --yaml "$rendered" --output none
  else
    log "grafana: create $grafana_app"
    az containerapp create \
      --name "$grafana_app" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --yaml "$rendered" --output none

    local principal_id
    principal_id=$(az containerapp show \
      --name "$grafana_app" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --query identity.principalId -o tsv)
    local acr_id
    acr_id=$(az acr show --name "$ACR_NAME" --query id -o tsv)
    az role assignment create \
      --assignee "$principal_id" \
      --role AcrPull \
      --scope "$acr_id" --output none 2>/dev/null || true
    az containerapp update \
      --name "$grafana_app" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --yaml "$rendered" --output none
  fi

  GRAFANA_URL=$(az containerapp show \
    --name "$grafana_app" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
  [[ -n "$GRAFANA_URL" ]] && GRAFANA_URL="https://${GRAFANA_URL}"

  {
    echo "GRAFANA_APP_NAME=$grafana_app"
    echo "GRAFANA_URL=${GRAFANA_URL:-}"
    echo "GRAFANA_ADMIN_USER=admin"
    echo "GRAFANA_ADMIN_PASSWORD=${admin_pass}"
  } >> "$WRITE_ENV_FILE"

  rm -f "$rendered"
}

log "self-hosted grafana"
deploy_grafana

echo ""
echo "Done"
echo "  env    : $WRITE_ENV_FILE"
echo "  grafana: ${GRAFANA_URL:-n/a}  (login: admin / ${GRAFANA_ADMIN_PASSWORD:-admin})"
echo "  adx    : ${ADX_CLUSTER_URI:-n/a} / ${ADX_DATABASE}"
echo ""
echo "  cp $(basename "$WRITE_ENV_FILE") .env"
echo "  ./scripts/deploy-local.sh deploy"
