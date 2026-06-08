#!/usr/bin/env bash
# Fix Grafana Container App ACR auth (401 / ImagePullBackOff) without delete/recreate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${BOOTSTRAP_CONFIG:-$ROOT/azure/bootstrap-azure.env}"
USE_MANAGED_IDENTITY=false
SKIP_PULL=false
FORCE_UNSTICK=false
RECREATE_APP=false

usage() {
  cat <<EOF
Usage: $0 [--force] [--recreate] [--no-git-pull]

  Fixes ACR pull for Grafana using ACR admin credentials (default).

  --force     Scale to 0/1 first (stuck InProgress / Failed)
  --recreate  Delete and redeploy the Container App (last resort)
  Config: azure/bootstrap-azure.env
EOF
}

for arg in "$@"; do
  case "$arg" in
    --try-managed-identity) USE_MANAGED_IDENTITY=true ;;
    --admin-only|--force) FORCE_UNSTICK=true ;;
    --recreate) RECREATE_APP=true; FORCE_UNSTICK=true ;;
    --no-git-pull) SKIP_PULL=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

log() { echo "[fix-grafana-acr] $*"; }

if [[ "$SKIP_PULL" != "true" && -d "$ROOT/.git" ]]; then
  log "Updating repo..."
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || log "WARN: git pull failed — continuing with local copy"
  log "Repo: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

[[ -f "$CONFIG" ]] || { echo "ERROR: Missing $CONFIG" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "$CONFIG"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $CONFIG}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $CONFIG}"

ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"
GRAFANA_APP_NAME="${GRAFANA_APP_NAME:-grafana-telemetry-dev}"
# Max ~2 min wait for provisioningState (broken apps often stay InProgress forever).
GRAFANA_ACR_WAIT_MAX="${GRAFANA_ACR_WAIT_MAX:-12}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv 2>/dev/null \
  || az acr show --name "$ACR_NAME" --query loginServer -o tsv)}"

containerapp_provisioning_state() {
  az containerapp show --name "$GRAFANA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.provisioningState" -o tsv 2>/dev/null || echo "Unknown"
}

is_op_in_progress_error() {
  echo "$1" | grep -qiE 'ContainerAppOperationInProgress|active provisioning operation|OperationInProgress'
}

unstick_containerapp() {
  log "Unstick: scale to 0 (cancel stuck operations), then back to 1 ..."
  az containerapp update \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --min-replicas 0 --max-replicas 0 \
    --output none 2>/dev/null || true
  sleep 25
  az containerapp update \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --min-replicas 1 --max-replicas 1 \
    --output none 2>/dev/null || true
  sleep 15
}

brief_wait_for_idle() {
  local i state="Unknown" max=$GRAFANA_ACR_WAIT_MAX
  for i in $(seq 1 "$max"); do
    state=$(containerapp_provisioning_state)
    case "$state" in
      Succeeded|Failed) return 0 ;;
      *)
        log "Azure state=$state ($i/$max) — brief wait ..."
        sleep 10
        ;;
    esac
  done
  log "WARN: still state=$state after ~$(( max * 10 ))s — continuing (will retry each CLI call)"
  return 0
}

retry_containerapp() {
  local label=$1
  shift
  local attempt out
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if out=$("$@" 2>&1); then
      [[ -n "$out" ]] && echo "$out"
      return 0
    fi
    if is_op_in_progress_error "$out"; then
      log "$label: busy ($attempt/12) — wait 25s"
      sleep 25
      continue
    fi
    log "ERROR: $label failed: $out"
    return 1
  done
  log "ERROR: $label still blocked — run: ./scripts/fix-grafana-acr.sh --force"
  return 1
}

show_registry_config() {
  log "Registry config:"
  az containerapp registry list \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    -o json 2>/dev/null | sed 's/^/[fix-grafana-acr]   /' || log "   (none)"
}

remove_registry_config() {
  log "Removing registry config for $ACR_LOGIN_SERVER (if any) ..."
  retry_containerapp "registry remove" \
    az containerapp registry remove \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --server "$ACR_LOGIN_SERVER" \
    --output none || true
}

configure_acr_admin_registry() {
  local user pass grafana_pass
  grafana_pass="${GRAFANA_ADMIN_PASSWORD:-admin}"

  remove_registry_config

  log "Enabling ACR admin on $ACR_NAME ..."
  az acr update --name "$ACR_NAME" --admin-enabled true --output none
  user=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
  pass=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)

  log "Setting grafana-admin-password secret ..."
  retry_containerapp "secret set" \
    az containerapp secret set \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets "grafana-admin-password=$grafana_pass" \
    --output none

  log "Binding ACR admin registry ..."
  retry_containerapp "registry set" \
    az containerapp registry set \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --server "$ACR_LOGIN_SERVER" \
    --username "$user" \
    --password "$pass"

  show_registry_config
}

force_grafana_image_pull() {
  local image="${ACR_LOGIN_SERVER}/grafana:latest"
  log "New revision: $image"
  retry_containerapp "image update" \
    az containerapp update \
    --name "$GRAFANA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$image" \
    --no-wait
}

wait_for_grafana_health() {
  local fqdn=$1 i
  log "Polling https://${fqdn}/api/health (up to ~10 min) ..."
  for i in $(seq 1 40); do
    if curl -sf --max-time 15 "https://${fqdn}/api/health" >/dev/null 2>&1; then
      log "Grafana is healthy: https://${fqdn}"
      echo ""
      echo "Open: https://${fqdn}  (login: admin / ${GRAFANA_ADMIN_PASSWORD:-admin})"
      return 0
    fi
    (( i == 1 || i % 4 == 0 )) && log "  waiting ($i/40) ..."
    sleep 15
  done
  return 1
}

recreate_grafana_via_bootstrap() {
  log "Recreate: delete + redeploy $GRAFANA_APP_NAME (ACR admin, no image rebuild) ..."
  chmod +x "$ROOT/scripts/bootstrap-azure.sh"
  GRAFANA_RECREATE=true \
  GRAFANA_ACR_USE_ADMIN=true \
  FORCE_IMAGE_BUILD=false \
  BOOTSTRAP_CONFIG="$CONFIG" \
  WRITE_ENV_FILE="${WRITE_ENV_FILE:-.env.azure}" \
  "$ROOT/scripts/bootstrap-azure.sh" --grafana-only --no-build
}

# ── main ─────────────────────────────────────────────────────────────────────

log "Grafana ACR repair: $GRAFANA_APP_NAME  ACR: $ACR_LOGIN_SERVER"
log "Repo: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [[ "$RECREATE_APP" == "true" ]]; then
  recreate_grafana_via_bootstrap
  FQDN=$(az containerapp show --name "$GRAFANA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
  [[ -n "$FQDN" ]] && wait_for_grafana_health "$FQDN"
  exit $?
fi

if ! az containerapp show --name "$GRAFANA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  log "ERROR: app missing — run ./scripts/fix-grafana.sh first"
  exit 1
fi

if ! az acr repository show --name "$ACR_NAME" --image grafana:latest >/dev/null 2>&1; then
  log "ERROR: grafana:latest not in ACR — run FORCE_IMAGE_BUILD=true ./scripts/bootstrap-azure.sh --grafana-only"
  exit 1
fi

az containerapp show --name "$GRAFANA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "{status:properties.runningStatus,provisioning:properties.provisioningState,fqdn:properties.configuration.ingress.fqdn,revision:properties.latestRevisionName}" \
  -o json
show_registry_config

PROV_STATE=$(containerapp_provisioning_state)
if [[ "$PROV_STATE" == "Failed" || "$PROV_STATE" == "InProgress" ]]; then
  log "provisioningState=$PROV_STATE — auto unstick"
  FORCE_UNSTICK=true
fi

[[ "$FORCE_UNSTICK" == "true" ]] && unstick_containerapp
[[ "$FORCE_UNSTICK" != "true" ]] && brief_wait_for_idle

if [[ "$USE_MANAGED_IDENTITY" == "true" ]]; then
  log "WARN: --try-managed-identity ignored in fast path; using ACR admin (sandbox)"
fi

configure_acr_admin_registry
force_grafana_image_pull

FQDN=$(az containerapp show --name "$GRAFANA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
wait_for_grafana_health "$FQDN" || {
  log "Not healthy. Last resort: ./scripts/fix-grafana-acr.sh --recreate"
  az containerapp replica list -n "$GRAFANA_APP_NAME" -g "$AZURE_RESOURCE_GROUP" -o table 2>/dev/null || true
  exit 1
}
