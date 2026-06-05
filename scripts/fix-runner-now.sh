#!/usr/bin/env bash
# One-shot fix for runner crash: ModuleNotFoundError: No module named 'observability'
# or stale ACR :latest cache. Pulls code, rebuilds image (cache-bust), deploys by digest.
#
# Usage (Cloud Shell):
#   cd ~/observability && git pull && chmod +x scripts/fix-runner-now.sh
#   ./scripts/fix-runner-now.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"

ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"
ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
AZURE_LOCATION="${AZURE_LOCATION:-eastus}"
IMAGE_REPO="ai-telemetry-runner"

log() { echo "[fix-runner-now] $*"; }

if [[ -d "$ROOT/.git" ]]; then
  log "1/5 — git pull"
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || { log "ERROR: git pull failed"; exit 1; }
  log "  commit: $(git -C "$ROOT" rev-parse --short HEAD)"
else
  log "1/5 — skip git pull (not a git clone)"
fi

log "2/5 — preflight"
[[ -d "$ROOT/observability" ]] || { log "ERROR: missing observability/ — git pull or clone repo"; exit 1; }
grep -q 'COPY observability/' "$ROOT/Dockerfile.runner" \
  || { log "ERROR: Dockerfile.runner missing COPY observability/ — git pull"; exit 1; }
grep -q 'runner import ok' "$ROOT/Dockerfile.runner" \
  || log "WARN: no build-time import check in Dockerfile (git pull recommended)"
[[ -f "$ENV_FILE" ]] || { log "ERROR: missing $ENV_FILE — run ./scripts/bootstrap-azure.sh first"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $ENV_FILE}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $ENV_FILE}"
: "${EVENTHUB_NAMESPACE:?Set EVENTHUB_NAMESPACE in $ENV_FILE}"
: "${EVENTHUB_CONNECTION_STRING:?Set EVENTHUB_CONNECTION_STRING in $ENV_FILE}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv)}"

log "3/5 — ACR build (cache-bust, ~5–12 min)"
log "  registry: $ACR_NAME"
log "  expect log lines: COPY observability/ and runner import ok"
acr_build_image "$ACR_NAME" "$AZURE_RESOURCE_GROUP" "$ACR_LOGIN_SERVER" \
  "${IMAGE_REPO}:latest" "$ROOT/Dockerfile.runner" "$ROOT"

DIGEST=$(az acr repository show-manifests --name "$ACR_NAME" \
  --repository "$IMAGE_REPO" --orderby time_desc --top 1 \
  --query "[0].digest" -o tsv)
[[ -n "$DIGEST" && "$DIGEST" != "None" ]] || { log "ERROR: could not read image digest from ACR"; exit 1; }
IMAGE_BY_DIGEST="${ACR_LOGIN_SERVER}/${IMAGE_REPO}@${DIGEST}"
log "  image digest: $DIGEST"

log "4/5 — deploy Container App (full YAML + digest pin)"
env_id=$(az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
acr_admin_credentials "$ACR_NAME"
eh_conn="$(awk_escape "$EVENTHUB_CONNECTION_STRING")"
eh_name="${EVENTHUB_NAME:-ai-telemetry-events}"
otel_ep="$(awk_escape "$(resolve_azure_otel_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")")"
otel_logs_ep="$(awk_escape "$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")")"
user="$(awk_escape "$ACR_ADMIN_USER")"
pass="$(awk_escape "$ACR_ADMIN_PASS")"

rendered="$ROOT/infra/runner-now.rendered.yaml"
awk -v loc="$AZURE_LOCATION" \
    -v env_id="$env_id" \
    -v acr_server="$ACR_LOGIN_SERVER" \
    -v acr_user="$user" \
    -v acr_pass="$pass" \
    -v image="$IMAGE_BY_DIGEST" \
    -v eh_ns="$EVENTHUB_NAMESPACE" \
    -v eh_conn="$eh_conn" \
    -v eh_name="$eh_name" \
    -v otel_ep="$otel_ep" \
    -v otel_logs_ep="$otel_logs_ep" \
    '{
      gsub(/__LOCATION__/, loc)
      gsub(/__MANAGED_ENV_ID__/, env_id)
      gsub(/__ACR_LOGIN_SERVER__/, acr_server)
      gsub(/__ACR_USERNAME__/, acr_user)
      gsub(/__ACR_ADMIN_PASSWORD__/, acr_pass)
      gsub(/__IMAGE__/, image)
      gsub(/__EVENTHUB_NAMESPACE__/, eh_ns)
      gsub(/__EVENTHUB_CONNECTION_STRING__/, eh_conn)
      gsub(/__EVENTHUB_NAME__/, eh_name)
      gsub(/__OTEL_ENDPOINT__/, otel_ep)
      gsub(/__OTEL_LOGS_ENDPOINT__/, otel_logs_ep)
      print
    }' "$ROOT/infra/runner-acr-admin.template.yaml" > "$rendered"

if az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp update --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$rendered"
else
  az containerapp create --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$rendered"
fi
rm -f "$rendered"

log "5/5 — wait for healthy /metrics (~10 min max)"
FQDN=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
[[ -n "$FQDN" ]] || { log "ERROR: no ingress FQDN"; exit 1; }

for i in $(seq 1 40); do
  if runner_metrics_ok "https://${FQDN}/metrics"; then
    echo ""
    log "SUCCESS — runner is healthy"
    echo "  Metrics: https://${FQDN}/metrics"
    curl -sf --max-time 30 "https://${FQDN}/metrics" 2>/dev/null | grep -m3 -E 'ai_gateway|kube_pod' || true
    exit 0
  fi
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${FQDN}/metrics" 2>/dev/null || echo "000")
  (( i == 1 || i % 4 == 0 )) && log "  poll ($i/40) — /metrics HTTP ${code}"
  sleep 15
done

log "FAILED — /metrics did not look healthy. Console logs:"
az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 25 2>/dev/null || true
if az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 50 2>/dev/null | grep -q 'No module named .observability'; then
  log "Still missing observability — paste ACR build log (COPY observability step) for support"
fi
exit 1
