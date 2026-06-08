#!/usr/bin/env bash
# Deploy Loki + Tempo + OTel Collector + Prometheus scraper on Azure Container Apps.
# Wires runner OTLP export and writes datasource URLs to .env.azure for Grafana.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"
SKIP_PULL=false
BUILD_IMAGES=false
SKIP_RUNNER_OTLP=false
FROM_STEP="loki"
FORCE_DEPLOY="${FORCE_CONTAINER_DEPLOY:-false}"

usage() {
  cat <<EOF
Usage: $0 [--build] [--no-git-pull] [--skip-runner-otlp] [--from STEP]

  Deploys the full observability backend in the Container Apps environment:
    Loki → logs store
    Tempo → traces store
    Prometheus scraper → metrics store (sandbox mode, no AMP)
    OTel Collector → receives OTLP from runner (logs/traces/metrics)
    Runner OTLP env → collector (unless --skip-runner-otlp)

  Skips any component that already exists and passes its health check (no redeploy).
  Set FORCE_CONTAINER_DEPLOY=true to redeploy everything.

  --from STEP   Resume from STEP: loki | tempo | prometheus | otel | otlp
                (skips earlier steps entirely — use after a partial run)
                otlp = runner OTLP env wiring only (no redeploy)

  --build       Force rebuild tempo/collector/prometheus images in ACR
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) BUILD_IMAGES=true; shift ;;
    --no-git-pull) SKIP_PULL=true; shift ;;
    --skip-runner-otlp) SKIP_RUNNER_OTLP=true; shift ;;
    --from=*) FROM_STEP="${1#*=}"; shift ;;
    --from) FROM_STEP="${2:?--from requires loki|tempo|prometheus|otel}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

log() { echo "[obs-stack] $*"; }

if [[ "$SKIP_PULL" != "true" && -d "$ROOT/.git" ]]; then
  log "Updating repo..."
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || log "WARN: git pull failed"
fi

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE — run ./scripts/bootstrap-azure.sh first"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $ENV_FILE}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $ENV_FILE}"

ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"
CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"
LOKI_APP_NAME="${LOKI_APP_NAME:-loki-telemetry-dev}"
TEMPO_APP_NAME="${TEMPO_APP_NAME:-tempo-telemetry-dev}"
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
AZURE_LOCATION="${AZURE_LOCATION:-eastus}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv 2>/dev/null \
  || az acr show --name "$ACR_NAME" --query loginServer -o tsv)}"

cae_domain() {
  az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query properties.defaultDomain -o tsv
}

internal_host() {
  echo "${1}.internal.$(cae_domain)"
}

app_fqdn() {
  az containerapp show --name "$1" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true
}

containerapp_exists() {
  az containerapp show --name "$1" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1
}

acr_image_exists() {
  az acr repository show --name "$ACR_NAME" --image "$1" >/dev/null 2>&1
}

build_if_needed() {
  local tag=$1 dockerfile=$2
  if [[ "$BUILD_IMAGES" == "true" ]] || ! acr_image_exists "$tag"; then
    log "Building $ACR_NAME/$tag ..."
    acr_build_image "$ACR_NAME" "$AZURE_RESOURCE_GROUP" "$ACR_LOGIN_SERVER" \
      "$tag" "$dockerfile" "$ROOT"
  else
    log "Reuse ACR image $tag"
  fi
}

render_loki_yaml() {
  local dest=$1 env_id
  env_id=$(az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
  sed \
    -e "s|__LOCATION__|${AZURE_LOCATION}|g" \
    -e "s|__MANAGED_ENV_ID__|${env_id}|g" \
    "$ROOT/infra/loki.template.yaml" > "$dest"
}

render_acr_admin_yaml() {
  local template=$1 dest=$2 image=$3
  shift 3
  local env_id user pass
  env_id=$(az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
  acr_admin_credentials "$ACR_NAME"
  user="$(awk_escape "$ACR_ADMIN_USER")"
  pass="$(awk_escape "$ACR_ADMIN_PASS")"
  awk -v loc="$AZURE_LOCATION" \
      -v env_id="$env_id" \
      -v acr_server="$ACR_LOGIN_SERVER" \
      -v acr_user="$user" \
      -v acr_pass="$pass" \
      -v image="$image" \
      "$@" \
      '{
        gsub(/__LOCATION__/, loc)
        gsub(/__MANAGED_ENV_ID__/, env_id)
        gsub(/__ACR_LOGIN_SERVER__/, acr_server)
        gsub(/__ACR_USERNAME__/, acr_user)
        gsub(/__ACR_ADMIN_PASSWORD__/, acr_pass)
        gsub(/__IMAGE__/, image)
        print
      }' "$template" > "$dest"
}

deploy_yaml_app() {
  local name=$1 yaml=$2
  if containerapp_exists "$name"; then
    log "Updating $name ..."
    az containerapp update --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$yaml"
  else
    log "Creating $name ..."
    az containerapp create --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$yaml"
  fi
}

wait_for_app() {
  local name=$1 check_cmd=$2 label=$3
  local i
  log "Waiting for $label ($name) ..."
  for i in $(seq 1 30); do
    if eval "$check_cmd"; then
      log "$label ready"
      return 0
    fi
    (( i % 4 == 0 )) && log "  still waiting ($i/30) ..."
    sleep 10
  done
  log "WARN: $label not confirmed ready"
  return 1
}

step_enabled() {
  local step=$1
  case "$FROM_STEP" in
    loki)       return 0 ;;
    tempo)      [[ "$step" != "loki" ]] ;;
    prometheus) [[ "$step" != "loki" && "$step" != "tempo" ]] ;;
    otel)       [[ "$step" == "otel" || "$step" == "otlp" ]] ;;
    otlp)       [[ "$step" == "otlp" ]] ;;
    *)
      log "ERROR: unknown --from step '$FROM_STEP' (use loki|tempo|prometheus|otel|otlp)"
      exit 1
      ;;
  esac
}

app_deployed_ok() {
  local name=$1 prov
  prov=$(az containerapp show --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.provisioningState" -o tsv 2>/dev/null || echo "")
  [[ "$prov" == "Succeeded" ]]
}

wait_for_app_running() {
  local name=$1 label=$2
  local i prov run
  log "Waiting for $label ($name) provisioning ..."
  for i in $(seq 1 30); do
    prov=$(az containerapp show --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" \
      --query "properties.provisioningState" -o tsv 2>/dev/null || echo "")
    run=$(az containerapp show --name "$name" --resource-group "$AZURE_RESOURCE_GROUP" \
      --query "properties.runningStatus" -o tsv 2>/dev/null || echo "")
    if [[ "$prov" == "Succeeded" && "$run" == "Running" ]]; then
      log "$label ready (Running)"
      return 0
    fi
    (( i % 4 == 0 )) && log "  still waiting ($i/30) — prov=$prov run=$run"
    sleep 10
  done
  log "WARN: $label not confirmed Running"
  return 1
}

skip_if_healthy() {
  local name=$1 label=$2 step_key=${3:-}
  if [[ -n "$step_key" && "$FROM_STEP" == "$step_key" ]]; then
    return 1
  fi
  if [[ "$FORCE_DEPLOY" == "true" ]]; then
    return 1
  fi
  if containerapp_exists "$name" && app_deployed_ok "$name"; then
    log "SKIP $label ($name) — already deployed (Succeeded)"
    return 0
  fi
  return 1
}

write_datasource_env() {
  local prom_url loki_url tempo_url otel_endpoint
  read -r prom_url loki_url tempo_url < <(grafana_datasource_urls \
    "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$PROM_APP_NAME" "$LOKI_APP_NAME" "$TEMPO_APP_NAME")
  otel_endpoint="http://$(internal_host "$OTEL_APP_NAME"):80"

  log "Datasource URLs:"
  log "  PROMETHEUS_URL=$prom_url"
  log "  LOKI_URL=$loki_url"
  log "  TEMPO_URL=$tempo_url"
  log "  OTEL_EXPORTER_OTLP_ENDPOINT=$otel_endpoint"

  upsert_env() {
    local key=$1 val=$2
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
      echo "${key}=${val}" >> "$ENV_FILE"
    fi
  }

  upsert_env LOKI_APP_NAME "$LOKI_APP_NAME"
  upsert_env TEMPO_APP_NAME "$TEMPO_APP_NAME"
  upsert_env OTEL_APP_NAME "$OTEL_APP_NAME"
  upsert_env PROMETHEUS_URL "$prom_url"
  upsert_env LOKI_URL "$loki_url"
  upsert_env TEMPO_URL "$tempo_url"
  upsert_env OTEL_EXPORTER_OTLP_ENDPOINT "$otel_endpoint"
}

# ── Runner FQDN for Prometheus scrape ────────────────────────────────────────

RUNNER_FQDN=$(app_fqdn "$APP_NAME")
if [[ -z "$RUNNER_FQDN" ]]; then
  log "ERROR: Runner $APP_NAME has no ingress FQDN — run ./scripts/fix-runner.sh first"
  exit 1
fi

if ! runner_metrics_ok "https://${RUNNER_FQDN}/metrics"; then
  log "WARN: Runner /metrics not confirmed yet — Prometheus will scrape once runner is healthy"
fi

if [[ "$FROM_STEP" != "loki" ]]; then
  log "Resume from '$FROM_STEP' — skipping earlier steps"
fi
if [[ "$FORCE_DEPLOY" == "true" ]]; then
  log "FORCE_CONTAINER_DEPLOY=true — redeploying all components"
fi

PROM_FQDN=""

# ── 1. Loki (ACR image — OTLP-ready config) ───────────────────────────────────

if step_enabled loki; then
  log "=== Loki ==="
  if skip_if_healthy "$LOKI_APP_NAME" "Loki" "loki"; then
    :
  else
    build_if_needed "loki:latest" "$ROOT/Dockerfile.loki"
    loki_yaml="$ROOT/infra/loki.rendered.yaml"
    render_acr_admin_yaml "$ROOT/infra/loki-acr-admin.template.yaml" "$loki_yaml" \
      "${ACR_LOGIN_SERVER}/loki:latest"
    deploy_yaml_app "$LOKI_APP_NAME" "$loki_yaml"
    rm -f "$loki_yaml"
    wait_for_app_running "$LOKI_APP_NAME" "Loki" || true
  fi
else
  log "SKIP Loki — --from $FROM_STEP"
fi

# ── 2. Tempo (ACR image + admin) ─────────────────────────────────────────────

if step_enabled tempo; then
  log "=== Tempo ==="
  if skip_if_healthy "$TEMPO_APP_NAME" "Tempo" "tempo"; then
    :
  else
    build_if_needed "tempo:latest" "$ROOT/Dockerfile.tempo"
    tempo_yaml="$ROOT/infra/tempo.rendered.yaml"
    render_acr_admin_yaml "$ROOT/infra/tempo-acr-admin.template.yaml" "$tempo_yaml" \
      "${ACR_LOGIN_SERVER}/tempo:latest"
    deploy_yaml_app "$TEMPO_APP_NAME" "$tempo_yaml"
    rm -f "$tempo_yaml"
    wait_for_app_running "$TEMPO_APP_NAME" "Tempo" || true
  fi
else
  log "SKIP Tempo — --from $FROM_STEP"
fi

# ── 3. Prometheus scraper (sandbox, no AMP) ──────────────────────────────────

if step_enabled prometheus; then
  log "=== Prometheus scraper ==="
  if skip_if_healthy "$PROM_APP_NAME" "Prometheus" "prometheus"; then
    PROM_FQDN=$(app_fqdn "$PROM_APP_NAME")
  else
    build_if_needed "prometheus-scraper:latest" "$ROOT/Dockerfile.prometheus"
    prometheus_deploy_sandbox "$PROM_APP_NAME" "$CAE_NAME" "$AZURE_RESOURCE_GROUP" \
      "$ACR_NAME" "$ACR_LOGIN_SERVER" "$RUNNER_FQDN"
    PROM_FQDN=$(app_fqdn "$PROM_APP_NAME")
    wait_for_prometheus_app "$PROM_APP_NAME" "$AZURE_RESOURCE_GROUP" || true
  fi
else
  log "SKIP Prometheus — --from $FROM_STEP"
  PROM_FQDN=$(app_fqdn "$PROM_APP_NAME")
fi

# ── 4. OTel Collector ────────────────────────────────────────────────────────

if step_enabled otel; then
  log "=== OTel Collector ==="
  if skip_if_healthy "$OTEL_APP_NAME" "OTel Collector" "otel"; then
    :
  else
    build_if_needed "otel-collector:latest" "$ROOT/Dockerfile.collector"

    TEMPO_EP="http://$(internal_host "$TEMPO_APP_NAME"):4317"
    LOKI_OTLP_EP="$(resolve_azure_loki_otlp_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$LOKI_APP_NAME")"
    PROM_EP="https://$(internal_host "$PROM_APP_NAME")/api/v1/write"

    otel_yaml="$ROOT/infra/otel.rendered.yaml"
    env_id=$(az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
    acr_admin_credentials "$ACR_NAME"
    user="$(awk_escape "$ACR_ADMIN_USER")"
    pass="$(awk_escape "$ACR_ADMIN_PASS")"
    tempo_ep="$(awk_escape "$TEMPO_EP")"
    loki_otlp_ep="$(awk_escape "$LOKI_OTLP_EP")"
    prom_ep="$(awk_escape "$PROM_EP")"
    awk -v loc="$AZURE_LOCATION" \
        -v env_id="$env_id" \
        -v acr_server="$ACR_LOGIN_SERVER" \
        -v acr_user="$user" \
        -v acr_pass="$pass" \
        -v image="${ACR_LOGIN_SERVER}/otel-collector:latest" \
        -v tempo_ep="$TEMPO_EP" \
        -v loki_otlp_ep="$LOKI_OTLP_EP" \
        -v prom_ep="$PROM_EP" \
        '{
          gsub(/__LOCATION__/, loc)
          gsub(/__MANAGED_ENV_ID__/, env_id)
          gsub(/__ACR_LOGIN_SERVER__/, acr_server)
          gsub(/__ACR_USERNAME__/, acr_user)
          gsub(/__ACR_ADMIN_PASSWORD__/, acr_pass)
          gsub(/__IMAGE__/, image)
          gsub(/__TEMPO_ENDPOINT__/, tempo_ep)
          gsub(/__LOKI_OTLP_ENDPOINT__/, loki_otlp_ep)
          gsub(/__PROM_WRITE_ENDPOINT__/, prom_ep)
          print
        }' "$ROOT/infra/otel-collector-acr-admin.template.yaml" > "$otel_yaml"

    deploy_yaml_app "$OTEL_APP_NAME" "$otel_yaml"
    rm -f "$otel_yaml"
    if ! wait_for_app_running "$OTEL_APP_NAME" "OTel Collector"; then
      log "WARN: OTel Collector not Running — recent logs:"
      az containerapp logs show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
        --tail 40 2>/dev/null || true
    fi
  fi
else
  log "SKIP OTel Collector — --from $FROM_STEP"
fi

# ── 5. Wire runner OTLP ──────────────────────────────────────────────────────

if step_enabled otlp && [[ "$SKIP_RUNNER_OTLP" != "true" ]]; then
  log "=== Runner OTLP wiring ==="
  OTEL_ENDPOINT="http://$(internal_host "$OTEL_APP_NAME"):80"
  OTEL_LOGS_ENDPOINT="$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
  log "Setting OTEL_EXPORTER_OTLP_ENDPOINT=$OTEL_ENDPOINT on $APP_NAME"
  log "Setting OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_LOGS_ENDPOINT (gRPC via :80 ingress)"
  # ACA Consumption only routes app-to-app via the ingress on 80/443; raw 4317/4318 are
  # unreachable. Serve plaintext h2c on the collector and send OTLP gRPC over :80 (insecure).
  az containerapp ingress update --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --allow-insecure --output none 2>/dev/null || true
  az containerapp update \
    --name "$APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --set-env-vars \
      "OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}" \
      "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=${OTEL_LOGS_ENDPOINT}" \
      "OTEL_EXPORTER_OTLP_PROTOCOL=grpc" \
      "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
      "OTEL_EXPORTER_OTLP_INSECURE=true" \
    --output none
fi

write_datasource_env

echo ""
echo "============================================================"
echo "  Observability stack deployed"
echo "============================================================"
echo "  Loki:        https://$(app_fqdn "$LOKI_APP_NAME")"
echo "  Tempo:       https://$(app_fqdn "$TEMPO_APP_NAME")"
echo "  Prometheus:  https://${PROM_FQDN}"
echo "  Collector:   http://$(internal_host "$OTEL_APP_NAME"):80 (OTLP gRPC via ingress)"
echo ""
echo "  Next: redeploy Grafana with datasource URLs:"
echo "    export FORCE_CONTAINER_DEPLOY=true"
echo "    ./scripts/bootstrap-azure.sh --grafana-only"
echo "============================================================"
