#!/usr/bin/env bash
# Fix ai-telemetry-runner-dev 404 (no healthy replicas / ACR pull failure).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"
RECREATE=false
SKIP_PULL=false
BUILD_IMAGE=false

usage() {
  cat <<EOF
Usage: $0 [--recreate] [--build] [--no-git-pull]

  Redeploys the telemetry runner with ACR admin auth embedded in YAML.
  Requires .env.azure (from bootstrap) with Event Hub settings.

  --recreate  Delete app first (default if app exists but returns 404)
  --build     Force rebuild ai-telemetry-runner:latest in ACR
EOF
}

for arg in "$@"; do
  case "$arg" in
    --recreate) RECREATE=true ;;
    --build) BUILD_IMAGE=true ;;
    --no-git-pull) SKIP_PULL=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

log() { echo "[fix-runner] $*"; }

preflight_dockerfile() {
  if ! grep -q 'COPY observability/' "$ROOT/Dockerfile.runner"; then
    log "ERROR: Dockerfile.runner is missing 'COPY observability/' — run: git pull"
    exit 1
  fi
  if ! grep -q 'runner import ok' "$ROOT/Dockerfile.runner"; then
    log "WARN: Dockerfile.runner has no build-time import check — git pull recommended"
  fi
}

if [[ "$SKIP_PULL" != "true" && -d "$ROOT/.git" ]]; then
  log "Updating repo..."
  git -C "$ROOT" pull --ff-only origin master 2>/dev/null \
    || git -C "$ROOT" pull --ff-only origin main 2>/dev/null \
    || log "WARN: git pull failed"
  log "Repo: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE — run ./scripts/bootstrap-azure.sh first"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in $ENV_FILE}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in $ENV_FILE}"
: "${EVENTHUB_NAMESPACE:?Set EVENTHUB_NAMESPACE in $ENV_FILE}"
: "${EVENTHUB_CONNECTION_STRING:?Set EVENTHUB_CONNECTION_STRING in $ENV_FILE}"

ACR_NAME="${ACR_NAME:-acrtelemetrydevaj}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
AZURE_LOCATION="${AZURE_LOCATION:-eastus}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(az acr show --name "$ACR_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query loginServer -o tsv 2>/dev/null \
  || az acr show --name "$ACR_NAME" --query loginServer -o tsv)}"

runner_fqdn() {
  az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true
}

runner_serving() {
  local fqdn
  fqdn=$(runner_fqdn)
  [[ -n "$fqdn" ]] && runner_metrics_ok "https://${fqdn}/metrics"
}

runner_otlp_ok() {
  local ep logs_ep expected_logs
  ep=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
  logs_ep=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
  expected_logs="$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"
  [[ -n "$ep" && "$ep" != *localhost* && "$ep" != *127.0.0.1* \
    && -n "$logs_ep" && "$logs_ep" == "$expected_logs" ]]
}

render_runner_yaml() {
  local dest=$1 env_id user pass eh_conn otel_ep domain eh_name runner_digest runner_image
  env_id=$(az containerapp env show --name "$CAE_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)
  acr_admin_credentials "$ACR_NAME"
  user="$ACR_ADMIN_USER"
  pass="$ACR_ADMIN_PASS"
  eh_conn="$(awk_escape "$EVENTHUB_CONNECTION_STRING")"
  eh_name="${EVENTHUB_NAME:-ai-telemetry-events}"
  otel_ep="$(awk_escape "$(resolve_azure_otel_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")")"
  otel_logs_ep="$(awk_escape "$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")")"
  user="$(awk_escape "$user")"
  pass="$(awk_escape "$pass")"
  # Pin by digest: ACA dedupes revisions by image-reference STRING, so re-applying
  # ":latest" (unchanged) creates NO new revision and NO re-pull — a freshly built
  # image would never go live. The digest changes on every rebuild → forces a pull.
  if runner_digest=$(acr_latest_digest "$ACR_NAME" "ai-telemetry-runner" 2>/dev/null); then
    runner_image="${ACR_LOGIN_SERVER}/ai-telemetry-runner@${runner_digest}"
  else
    runner_image="${ACR_LOGIN_SERVER}/ai-telemetry-runner:latest"
  fi
  awk -v loc="$AZURE_LOCATION" \
      -v env_id="$env_id" \
      -v acr_server="$ACR_LOGIN_SERVER" \
      -v acr_user="$user" \
      -v acr_pass="$pass" \
      -v image="$runner_image" \
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
      }' "$ROOT/infra/runner-acr-admin.template.yaml" > "$dest"
}

diagnose_runner_failure() {
  local fqdn=$1
  log "=== Runner diagnostics ==="
  az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "{status:properties.runningStatus,provisioning:properties.provisioningState,revision:properties.latestRevisionName,fqdn:properties.configuration.ingress.fqdn}" \
    -o json 2>/dev/null || true
  log "Replicas:"
  az containerapp replica list --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" -o table 2>/dev/null \
    || log "  (no replicas — usually ACR pull failure)"
  log "System logs (image pull / probes):"
  az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --type system --tail 40 2>/dev/null || true
  log "Console logs (runner startup / Event Hub):"
  az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --type console --tail 40 2>/dev/null || true
  if [[ -n "$fqdn" ]]; then
    log "HTTP checks:"
    curl -sS -o /dev/null -w "  /metrics → HTTP %{http_code}\n" --max-time 15 "https://${fqdn}/metrics" 2>/dev/null || true
    curl -sS -o /dev/null -w "  /healthz (port 8080 internal) — use console logs if 404 on metrics\n" --max-time 5 "https://${fqdn}/metrics" 2>/dev/null || true
  fi
  log "Common fixes:"
  log "  1. git pull && ./scripts/fix-runner.sh --recreate --build"
  log "  2. Check .env.azure has EVENTHUB_NAMESPACE + EVENTHUB_CONNECTION_STRING + EVENTHUB_NAME"
  log "  3. Close other Cloud Shell tabs; wait 2 min if ContainerAppOperationInProgress"
  log "  4. Console Traceback with 'No module named observability' → rebuild image (git pull first)"
  local console_log
  console_log=$(az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --type console --tail 50 2>/dev/null || true)
  if echo "$console_log" | grep -q 'No module named .observability'; then
    log "DETECTED: ModuleNotFoundError observability — image built from old Dockerfile"
    log "  Fix: cd ~/observability && git pull && ./scripts/fix-runner-now.sh"
  fi
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${fqdn}/metrics" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]] && runner_metrics_ok "https://${fqdn}/metrics"; then
    log "NOTE: /metrics HTTP 200 with Prometheus data — runner may already be healthy"
  fi
  if echo "$console_log" | grep -q 'Runner crashed during startup'; then
    log "DETECTED: startup exception in console logs (see Traceback above)"
  fi
}

wait_for_provisioning() {
  local i state
  log "Waiting for Azure to finish provisioning $APP_NAME ..."
  for i in $(seq 1 36); do
    state=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
      --query "properties.provisioningState" -o tsv 2>/dev/null || echo "Unknown")
    case "$state" in
      Succeeded) log "provisioningState=Succeeded"; return 0 ;;
      Failed)
        log "ERROR: provisioningState=Failed"
        return 1
        ;;
      *)
        (( i == 1 || i % 6 == 0 )) && log "  provisioning ($i/36): $state"
        sleep 10
        ;;
    esac
  done
  log "WARN: provisioning still not Succeeded — continuing to poll /metrics"
  return 0
}

runner_replica_summary() {
  local count
  count=$(az containerapp replica list --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "length(@)" -o tsv 2>/dev/null || echo "0")
  log "  replicas running: ${count:-0}"
  if [[ "${count:-0}" == "0" ]]; then
    az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
      --type system --tail 5 2>/dev/null | sed 's/^/[fix-runner]   system: /' || true
  fi
}

wait_for_runner() {
  local fqdn=$1 i code body
  log "Polling https://${fqdn}/metrics (up to ~10 min) ..."
  log "  (404 or HTTP 000 for the first few minutes is normal while the image pulls and replicas start)"
  for i in $(seq 1 40); do
    body=$(curl -sf --max-time 30 "https://${fqdn}/metrics" 2>/dev/null | head -c 65536 || true)
    if [[ -n "$body" ]] && echo "$body" | grep -qE 'ai_gateway|kube_pod_info|ai_telemetry_runner|# TYPE'; then
      log "Runner is healthy: https://${fqdn}/metrics"
      return 0
    fi
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://${fqdn}/metrics" 2>/dev/null || true)
    [[ -z "$code" || "$code" == "000" ]] && code="000 (not reachable yet)"
    if (( i == 1 || i % 4 == 0 )); then
      log "  waiting ($i/40) — /metrics HTTP ${code}"
      runner_replica_summary
    fi
    sleep 15
  done
  return 1
}

# ── main ─────────────────────────────────────────────────────────────────────

log "Runner repair: $APP_NAME in $AZURE_RESOURCE_GROUP"

if [[ "$BUILD_IMAGE" == "true" ]] || ! az acr repository show --name "$ACR_NAME" --image ai-telemetry-runner:latest >/dev/null 2>&1; then
  preflight_dockerfile
  log "Building $ACR_NAME/ai-telemetry-runner:latest in ACR (~5–12 min on first run) ..."
  log "  (apt/debconf lines during build are normal — wait for 'Run ID' to finish)"
  log "  (expect steps: COPY observability/ then RUN python3 ... runner import ok)"
  acr_build_image "$ACR_NAME" "$AZURE_RESOURCE_GROUP" "$ACR_LOGIN_SERVER" \
    "ai-telemetry-runner:latest" "$ROOT/Dockerfile.runner" "$ROOT"
fi

if az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "{status:properties.runningStatus,provisioning:properties.provisioningState,fqdn:properties.configuration.ingress.fqdn,replicas:properties.template.scale}" -o json
  log "Replicas:"
  az containerapp replica list --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" -o table 2>/dev/null \
    || log "  (no replicas)"
  if runner_serving && runner_otlp_ok && [[ "$BUILD_IMAGE" != "true" ]]; then
    log "Runner already serving metrics with correct OTLP endpoints — nothing to do"
    echo "Metrics: https://$(runner_fqdn)/metrics"
    exit 0
  fi
  if [[ "$BUILD_IMAGE" == "true" ]]; then
    log "Rebuilt image (--build) — forcing redeploy so the new revision goes live"
  fi
  if runner_serving && ! runner_otlp_ok; then
    log "Runner serving metrics but OTLP endpoint is wrong (localhost?) — redeploying env"
  fi
  [[ "$RECREATE" != "true" ]] && RECREATE=true
else
  RECREATE=false
fi

rendered="$ROOT/infra/runner.rendered.yaml"
render_runner_yaml "$rendered"
log "Rendered $rendered"

if [[ "$RECREATE" == "true" ]] && az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  log "Deleting $APP_NAME ..."
  az containerapp delete --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --yes --output none
  for _ in $(seq 1 36); do
    az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1 || break
    sleep 10
  done
  sleep 30
fi

if az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  log "Updating $APP_NAME ..."
  az containerapp update --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$rendered"
else
  log "Creating $APP_NAME (ACR admin in YAML, blocking) ..."
  az containerapp create --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --yaml "$rendered"
fi

rm -f "$rendered"

wait_for_provisioning || true

FQDN=$(runner_fqdn)
[[ -z "$FQDN" ]] && { log "ERROR: no ingress FQDN"; exit 1; }

if wait_for_runner "$FQDN"; then
  echo ""
  echo "Metrics: https://${FQDN}/metrics"
  echo "Health:  https://${FQDN}/healthz  (via port 8080 mapping if exposed)"
  exit 0
fi

log "Runner still not healthy after 40 polls (~10 min):"
diagnose_runner_failure "$FQDN"
exit 1
