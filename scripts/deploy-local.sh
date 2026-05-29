#!/usr/bin/env bash
# =============================================================================
# Local Azure deploy — secrets stay in .env only (never GitHub).
# All default resource names use the *-dev suffix.
#
# Usage:
#   cp .env.example .env          # fill in SP credentials
#   chmod +x scripts/deploy-local.sh
#   ./scripts/deploy-local.sh login
#   ./scripts/deploy-local.sh provision   # one-time Azure setup
#   ./scripts/deploy-local.sh build       # docker build + push to ACR
#   ./scripts/deploy-local.sh deploy      # Container Apps
#   ./scripts/deploy-local.sh grafana     # import dashboard
#   ./scripts/deploy-local.sh verify      # health checks
#   ./scripts/deploy-local.sh all         # provision → build → deploy → grafana
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

# ── Dev resource names (override any in .env) ───────────────────────────────
export AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-ai-telemetry-dev}"
export AZURE_LOCATION="${AZURE_LOCATION:-eastus}"
export ACR_NAME="${ACR_NAME:-acrtelemetrydev}"
export CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
export APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
export PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"
export GRAFANA_NAME="${GRAFANA_NAME:-grafana-ai-telemetry-dev}"
export PROM_WS="${PROM_WS:-telemetry-prometheus-dev}"
export EH_NS="${EH_NS:-evhns-telemetry-dev}"
export EH_NAME="${EVENTHUB_NAME:-ai-telemetry-events}"

log() { echo "[deploy-local] $*"; }

load_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Run: cp .env.example .env" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

cmd_login() {
  if [[ "${SKIP_AZ_LOGIN:-false}" == "true" ]]; then
    if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
      az account set --subscription "$AZURE_SUBSCRIPTION_ID" 2>/dev/null || true
    fi
    log "Using Cloud Shell session (SKIP_AZ_LOGIN=true)"
    return 0
  fi
  # shellcheck source=/dev/null
  source "$ROOT/scripts/azure-local-login.sh"
}

cmd_provision() {
  load_env
  cmd_login

  if [[ "${USE_EXISTING_RG:-false}" == "true" ]]; then
    if ! az group show --name "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
      echo "ERROR: Resource group '$AZURE_RESOURCE_GROUP' not found." >&2
      exit 1
    fi
    AZURE_LOCATION=$(az group show --name "$AZURE_RESOURCE_GROUP" --query location -o tsv)
    export AZURE_LOCATION
    log "Using existing RG $AZURE_RESOURCE_GROUP (location: $AZURE_LOCATION)"
  fi

  log "Registering Azure providers..."
  for ns in Microsoft.App Microsoft.Monitor Microsoft.Dashboard \
            Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.EventHub; do
    state=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
    if [[ "$state" != "Registered" ]]; then
      log "  Registering $ns ..."
      # Provider registration requires Contributor/Owner at subscription scope.
      # Skip with a warning if the account lacks that permission.
      az provider register --namespace "$ns" --output none 2>/dev/null || \
        log "  WARNING: could not register $ns (insufficient subscription-level permissions — ask a subscription Owner)."
    fi
  done

  log "Bootstrapping base infra (ACR, CAE, Event Hubs)..."
  BOOTSTRAP_ARGS=(
    --resource-group "$AZURE_RESOURCE_GROUP"
    --location       "$AZURE_LOCATION"
    --acr-name       "$ACR_NAME"
    --cae-name       "$CAE_NAME"
    --app-name       "$APP_NAME"
    --eventhub-ns    "$EH_NS"
    --eventhub-name  "$EH_NAME"
  )
  if [[ "${USE_EXISTING_RG:-false}" == "true" ]]; then
    BOOTSTRAP_ARGS+=(--use-existing-rg)
  fi
  "$ROOT/infra/bootstrap.sh" "${BOOTSTRAP_ARGS[@]}"

  log "Creating Azure Managed Prometheus ($PROM_WS)..."
  if ! az monitor account show --name "$PROM_WS" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    az monitor account create \
      --name "$PROM_WS" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      --output none
  fi

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
    log "PROM_REMOTE_WRITE_URL=$PROM_REMOTE_WRITE_URL"
  else
    log "WARN: Could not auto-detect Prometheus remote_write URL. Set PROM_REMOTE_WRITE_URL in .env manually."
    PROM_REMOTE_WRITE_URL=""
  fi

  log "Creating Azure Managed Grafana ($GRAFANA_NAME)..."
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

  EH_CONN=$(az eventhubs namespace authorization-rule keys list \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --namespace-name "$EH_NS" \
    --name RootManageSharedAccessKey \
    --query primaryConnectionString -o tsv)

  ACR_LOGIN=$(az acr show --name "$ACR_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv)
  GRAFANA_URL=$(az grafana show --name "$GRAFANA_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.endpoint" -o tsv)

  echo ""
  echo "============================================================"
  echo "  Provision complete — copy into .env if not already set:"
  echo "============================================================"
  echo "  AZURE_RESOURCE_GROUP=$AZURE_RESOURCE_GROUP"
  echo "  AZURE_LOCATION=$AZURE_LOCATION"
  echo "  ACR_NAME=$ACR_NAME"
  echo "  CAE_NAME=$CAE_NAME"
  echo "  APP_NAME=$APP_NAME"
  echo "  PROM_APP_NAME=$PROM_APP_NAME"
  echo "  GRAFANA_NAME=$GRAFANA_NAME"
  echo "  PROM_WS=$PROM_WS"
  echo "  EH_NS=$EH_NS"
  echo "  ACR_LOGIN_SERVER=$ACR_LOGIN"
  echo "  EVENTHUB_NAMESPACE=${EH_NS}.servicebus.windows.net"
  echo "  EVENTHUB_CONNECTION_STRING=$EH_CONN"
  [[ -n "${PROM_REMOTE_WRITE_URL:-}" ]] && echo "  PROM_REMOTE_WRITE_URL=$PROM_REMOTE_WRITE_URL"
  echo "  GRAFANA_URL=$GRAFANA_URL"
  echo "============================================================"
  echo "  Next: ./scripts/deploy-local.sh build && ./scripts/deploy-local.sh deploy"
  echo "============================================================"
}

cmd_build() {
  load_env
  cmd_login

  ACR_LOGIN="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)}"
  az acr login --name "$ACR_NAME"

  log "Building ai-telemetry-runner (linux/amd64)..."
  docker buildx build --platform linux/amd64 \
    -f "$ROOT/Dockerfile.runner" \
    -t "${ACR_LOGIN}/ai-telemetry-runner:latest" \
    --push "$ROOT"

  log "Building prometheus-scraper (linux/amd64)..."
  docker buildx build --platform linux/amd64 \
    -f "$ROOT/Dockerfile.prometheus" \
    -t "${ACR_LOGIN}/prometheus-scraper:latest" \
    --push "$ROOT"

  log "Images pushed to $ACR_LOGIN"
}

render_containerapp_yaml() {
  local rendered="$ROOT/infra/containerapp.rendered.yaml"
  local env_id acr_login image

  env_id=$(az containerapp env show \
    --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
  acr_login="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)}"
  image="${acr_login}/ai-telemetry-runner:latest"

  sed -e "s|__LOCATION__|$AZURE_LOCATION|g" \
      -e "s|__MANAGED_ENV_ID__|$env_id|g" \
      -e "s|__ACR_LOGIN_SERVER__|$acr_login|g" \
      -e "s|__IMAGE__|$image|g" \
      "$ROOT/infra/containerapp.template.yaml" > "$rendered"

  echo "$rendered"
}

ensure_acr_pull() {
  local principal acr_id
  principal=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query identity.principalId -o tsv 2>/dev/null || true)
  [[ -z "$principal" ]] && return 0
  acr_id=$(az acr show --name "$ACR_NAME" --query id -o tsv)
  az role assignment create --assignee "$principal" --role AcrPull --scope "$acr_id" \
    --output none 2>/dev/null || true
}

containerapp_exists() {
  az containerapp show --name "$1" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1
}

cmd_deploy() {
  load_env
  cmd_login

  : "${EVENTHUB_NAMESPACE:?Set EVENTHUB_NAMESPACE in .env (run provision first)}"
  : "${EVENTHUB_CONNECTION_STRING:?Set EVENTHUB_CONNECTION_STRING in .env}"

  az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

  rendered=$(render_containerapp_yaml)
  log "Rendered $rendered"

  az containerapp secret set \
    --name "$APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets \
      "eventhub-namespace=$EVENTHUB_NAMESPACE" \
      "eventhub-connection-string=$EVENTHUB_CONNECTION_STRING" \
    2>/dev/null || true

  if containerapp_exists "$APP_NAME"; then
    log "Container app $APP_NAME already exists — skipping create"
    if [[ "${SKIP_EXISTING_CONTAINERS:-false}" == "true" ]]; then
      log "SKIP_EXISTING_CONTAINERS=true — leaving $APP_NAME unchanged"
    else
      log "Updating $APP_NAME ..."
      az containerapp update \
        --name "$APP_NAME" \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --yaml "$rendered"
    fi
  else
    log "Creating $APP_NAME ..."
    az containerapp create \
      --name "$APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --yaml "$rendered"
    ensure_acr_pull
    az containerapp update \
      --name "$APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --yaml "$rendered"
  fi

  if [[ -n "${PROM_REMOTE_WRITE_URL:-}" ]]; then
    fqdn=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
      --query "properties.configuration.ingress.fqdn" -o tsv)
    acr_login="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)}"

    log "Deploying $PROM_APP_NAME (scrape target: $fqdn) ..."
    if containerapp_exists "$PROM_APP_NAME"; then
      log "Container app $PROM_APP_NAME already exists — skipping create"
      if [[ "${SKIP_EXISTING_CONTAINERS:-false}" != "true" ]]; then
        az containerapp update \
          --name "$PROM_APP_NAME" \
          --resource-group "$AZURE_RESOURCE_GROUP" \
          --image "${acr_login}/prometheus-scraper:latest" \
          --set-env-vars \
            "SCRAPE_TARGET=$fqdn" \
            "PROM_REMOTE_WRITE_URL=$PROM_REMOTE_WRITE_URL"
      else
        log "SKIP_EXISTING_CONTAINERS=true — leaving $PROM_APP_NAME unchanged"
      fi
    else
      az containerapp create \
        --name "$PROM_APP_NAME" \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --environment "$CAE_NAME" \
        --image "${acr_login}/prometheus-scraper:latest" \
        --ingress internal --target-port 9090 \
        --min-replicas 1 --max-replicas 1 \
        --cpu 0.25 --memory 0.5Gi \
        --env-vars \
          "SCRAPE_TARGET=$fqdn" \
          "PROM_REMOTE_WRITE_URL=$PROM_REMOTE_WRITE_URL"
    fi
  else
    log "Skipping $PROM_APP_NAME — PROM_REMOTE_WRITE_URL not set in .env"
  fi

  log "Deploy complete."
}

cmd_grafana() {
  load_env
  cmd_login

  az extension add --name amg --upgrade --yes --output none 2>/dev/null || true
  az grafana dashboard import \
    --name "$GRAFANA_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --definition @"$ROOT/dashboards/grafana_dashboard.json" \
    --overwrite true

  url=$(az grafana show --name "$GRAFANA_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.endpoint" -o tsv)
  log "Dashboard imported. Open: $url"
}

cmd_verify() {
  load_env
  cmd_login

  status=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.runningStatus" -o tsv)
  fqdn=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv)

  log "Container App status: $status"
  log "Metrics URL: https://${fqdn}/metrics"
  curl -sf "https://${fqdn}/metrics" | grep -m3 ai_gateway || log "WARN: no ai_gateway metrics yet (wait ~2 min)"
}

usage() {
  sed -n '2,14p' "$0"
  echo ""
  echo "Commands: login | provision | build | deploy | grafana | verify | all"
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    login)     cmd_login ;;
    provision) cmd_provision ;;
    build)     cmd_build ;;
    deploy)    cmd_deploy ;;
    grafana)   cmd_grafana ;;
    verify)    cmd_verify ;;
    all)
      cmd_provision
      load_env
      cmd_build
      cmd_deploy
      cmd_grafana
      cmd_verify
      ;;
    *) usage; exit 1 ;;
  esac
}

main "${1:-}"
