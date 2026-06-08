# Shared helpers for Azure Container Apps deploy scripts.
# shellcheck shell=bash

[[ -n "${_AZURE_DEPLOY_COMMON_LOADED:-}" ]] && return 0
_AZURE_DEPLOY_COMMON_LOADED=1

# Escape a value for use as the replacement side of awk gsub().
awk_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g'
}

# OTLP gRPC endpoint for Container Apps (never localhost on Azure).
#
# IMPORTANT (ACA Consumption env): raw container ports (4317/4318) and additionalPortMappings
# are NOT routable app-to-app — only the ingress on 80/443 is. So we send OTLP gRPC through the
# ingress on :80 (h2c). The ingress (transport: http2) forwards to the collector's targetPort
# 4317 gRPC receiver. The collector ingress must have allowInsecure: true for plaintext h2c, and
# the runner must set OTEL_EXPORTER_OTLP_PROTOCOL=grpc + OTEL_EXPORTER_OTLP_INSECURE=true.
resolve_azure_otel_endpoint() {
  local cae_name=$1 rg=$2 otel_app=${3:-otel-collector-dev}
  local env_ep=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
  if [[ -z "$env_ep" || "$env_ep" == *localhost* || "$env_ep" == *127.0.0.1* ]]; then
    local domain
    domain=$(az containerapp env show --name "$cae_name" --resource-group "$rg" \
      --query properties.defaultDomain -o tsv 2>/dev/null || true)
    if [[ -n "$domain" ]]; then
      echo "http://${otel_app}.internal.${domain}:80"
      return 0
    fi
  fi
  [[ -n "$env_ep" ]] && echo "$env_ep" || echo ""
}

# OTLP endpoint for logs — same :80 gRPC ingress path as metrics (see resolve_azure_otel_endpoint).
# NOTE: do NOT use :4318 HTTP here — that raw port is unreachable app-to-app in ACA Consumption.
resolve_azure_otel_logs_endpoint() {
  local cae_name=$1 rg=$2 otel_app=${3:-otel-collector-dev}
  local env_ep=${OTEL_EXPORTER_OTLP_LOGS_ENDPOINT:-}
  if [[ -z "$env_ep" || "$env_ep" == *localhost* || "$env_ep" == *127.0.0.1* ]]; then
    local domain
    domain=$(az containerapp env show --name "$cae_name" --resource-group "$rg" \
      --query properties.defaultDomain -o tsv 2>/dev/null || true)
    if [[ -n "$domain" ]]; then
      echo "http://${otel_app}.internal.${domain}:80"
      return 0
    fi
  fi
  [[ -n "$env_ep" ]] && echo "$env_ep" || echo ""
}

# Loki native OTLP ingest — HTTPS on internal ingress (ACA cannot duplicate targetPort 3100).
resolve_azure_loki_otlp_endpoint() {
  local cae_name=$1 rg=$2 loki_app=${3:-loki-telemetry-dev}
  local domain
  domain=$(az containerapp env show --name "$cae_name" --resource-group "$rg" \
    --query properties.defaultDomain -o tsv 2>/dev/null || true)
  [[ -n "$domain" ]] || return 1
  echo "https://${loki_app}.internal.${domain}/otlp"
}

acr_admin_credentials() {
  local acr_name=$1
  az acr update --name "$acr_name" --admin-enabled true --output none 2>/dev/null || true
  ACR_ADMIN_USER=$(az acr credential show --name "$acr_name" --query username -o tsv 2>/dev/null || true)
  ACR_ADMIN_PASS=$(az acr credential show --name "$acr_name" --query 'passwords[0].value' -o tsv 2>/dev/null || true)
}

# True when /metrics returns Prometheus text with runner instruments (not just HTTP 200).
runner_metrics_ok() {
  local metrics_url=$1 body
  body=$(curl -sf --compressed --max-time 30 "$metrics_url" 2>/dev/null | head -c 65536 || true)
  [[ -n "$body" ]] || return 1
  echo "$body" | grep -qE 'ai_gateway|kube_pod_info|ai_telemetry_runner|# TYPE|# HELP'
}

# HTTPS URL for a Container App on internal ingress (same CAE as Grafana).
cae_internal_https_url() {
  local app=$1 cae_name=$2 rg=$3
  local domain
  domain=$(cae_default_domain "$cae_name" "$rg") || return 1
  echo "https://${app}.internal.${domain}"
}

cae_default_domain() {
  local cae_name=$1 rg=$2 domain
  domain=$(az containerapp env show --name "$cae_name" --resource-group "$rg" \
    --query properties.defaultDomain -o tsv 2>/dev/null || true)
  [[ -n "$domain" ]] || return 1
  echo "$domain"
}

domain_from_internal_fqdn() {
  local fqdn=$1
  if [[ "$fqdn" =~ \.internal\.([a-zA-Z0-9.-]+) ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

resolve_cae_domain() {
  local cae_name=$1 rg=$2 ref_app=$3
  local domain fqdn url

  domain=$(cae_default_domain "$cae_name" "$rg" 2>/dev/null || true)
  [[ -n "$domain" ]] && { echo "$domain"; return 0; }

  fqdn=$(az containerapp show --name "$ref_app" --resource-group "$rg" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
  domain=$(domain_from_internal_fqdn "$fqdn")
  [[ -n "$domain" ]] && { echo "$domain"; return 0; }

  for url in "${PROMETHEUS_URL:-}" "${LOKI_URL:-}" "${TEMPO_URL:-}"; do
    domain=$(domain_from_internal_fqdn "$url")
    [[ -n "$domain" ]] && { echo "$domain"; return 0; }
    if [[ "$url" =~ \.internal\.([a-zA-Z0-9.-]+) ]]; then
      echo "${BASH_REMATCH[1]}"
      return 0
    fi
  done
  return 1
}

build_grafana_ds_url() {
  local env_url=$1 app=$2 domain=$3 port=$4 url

  if [[ -n "$env_url" && "$env_url" == *"://"* ]]; then
    url="$env_url"
    url="${url/http:/https:}"
    url="${url//:${port}/}"
    if [[ "$url" =~ ^(https://[^/?#]+) ]]; then
      url="${BASH_REMATCH[1]}"
    fi
    if [[ "$url" =~ ^https://[a-zA-Z0-9.-]+\.internal\. ]]; then
      echo "$url"
      return 0
    fi
  fi

  echo "https://${app}.internal.${domain}"
}

grafana_datasource_urls() {
  local cae_name=$1 rg=$2 prom_app=$3 loki_app=$4 tempo_app=$5
  local domain prom loki tempo

  domain=$(resolve_cae_domain "$cae_name" "$rg" "$prom_app") || {
    echo "grafana_datasource_urls: cannot resolve CAE domain" >&2
    return 1
  }

  prom=$(build_grafana_ds_url "${PROMETHEUS_URL:-}" "$prom_app" "$domain" 9090)
  loki=$(build_grafana_ds_url "${LOKI_URL:-}" "$loki_app" "$domain" 3100)
  tempo=$(build_grafana_ds_url "${TEMPO_URL:-}" "$tempo_app" "$domain" 3200)

  printf '%s\n' "$prom" "$loki" "$tempo"
}

prometheus_deploy_sandbox() {
  local prom_app=$1 cae_name=$2 rg=$3 acr_name=$4 acr_login=$5 runner_fqdn=$6
  local image_ref=${7:-"${acr_login}/prometheus-scraper:latest"}
  local user pass deploy_stamp container_name

  if [[ -z "$runner_fqdn" ]]; then
    echo "[prometheus] ERROR: runner FQDN required" >&2
    return 1
  fi

  acr_admin_credentials "$acr_name"
  user="$ACR_ADMIN_USER"
  pass="$ACR_ADMIN_PASS"
  deploy_stamp=$(date +%s)
  container_name=$(az containerapp show --name "$prom_app" --resource-group "$rg" \
    --query "properties.template.containers[0].name" -o tsv 2>/dev/null || echo "$prom_app")
  [[ -n "$container_name" && "$container_name" != "None" ]] || container_name="$prom_app"

  bind_prometheus_acr_registry() {
    az containerapp registry set \
      --name "$prom_app" \
      --resource-group "$rg" \
      --server "$acr_login" \
      --username "$user" \
      --password "$pass" \
      --output none
  }

  # Stock prom/prometheus deploys often pin /bin/prometheus + args without the remote-write
  # receiver flag. Force our entrypoint so --web.enable-remote-write-receiver is always set.
  if az containerapp show --name "$prom_app" --resource-group "$rg" >/dev/null 2>&1; then
    echo "[prometheus] Updating $prom_app (container=$container_name) ..."
    bind_prometheus_acr_registry
    az containerapp update \
      --name "$prom_app" \
      --resource-group "$rg" \
      --container-name "$container_name" \
      --image "$image_ref" \
      --command "/prometheus-entrypoint.sh" \
      --args "" \
      --set-env-vars \
        "SCRAPE_TARGET=${runner_fqdn}" \
        "DEPLOY_STAMP=${deploy_stamp}" \
      --output none
  else
    echo "[prometheus] Creating $prom_app ..."
    az containerapp create \
      --name "$prom_app" \
      --resource-group "$rg" \
      --environment "$cae_name" \
      --image "$image_ref" \
      --command "/prometheus-entrypoint.sh" \
      --registry-server "$acr_login" \
      --registry-username "$user" \
      --registry-password "$pass" \
      --ingress internal --target-port 9090 \
      --min-replicas 1 --max-replicas 1 \
      --cpu 0.25 --memory 0.5Gi \
      --env-vars \
        "SCRAPE_TARGET=${runner_fqdn}" \
        "DEPLOY_STAMP=${deploy_stamp}" \
      --output none
  fi
}

prometheus_console_logs() {
  local prom_app=$1 rg=$2 tail=${3:-60}
  az containerapp logs show --name "$prom_app" --resource-group "$rg" \
    --type console --tail "$tail" 2>/dev/null || true
}

# True when Prometheus finished starting (console logs).
prometheus_server_ready() {
  local logs=$1
  echo "$logs" | grep -qiE 'Server is ready to receive web requests|Listening on|Completed loading'
}

# True when Prometheus responds on /-/ready or logs confirm startup.
prometheus_health_ok() {
  local fqdn=${1:-} logs=${2:-}
  if [[ -n "$fqdn" ]] && curl -sfk --max-time 12 "https://${fqdn}/-/ready" >/dev/null 2>&1; then
    return 0
  fi
  [[ -n "$logs" ]] && prometheus_server_ready "$logs"
}

# True when an HTTP body is ACA's "Unavailable" page (not a Prometheus API 404).
aca_unavailable_response() {
  local body=$1
  echo "$body" | grep -qi 'Azure Container App - Unavailable'
}

# HTTPS Prometheus remote_write URL for OTel Collector (internal ACA ingress).
resolve_azure_prom_write_endpoint() {
  local cae_name=$1 rg=$2 prom_app=${3:-prometheus-scraper-dev}
  local domain
  domain=$(cae_default_domain "$cae_name" "$rg" 2>/dev/null || true)
  [[ -n "$domain" ]] || return 1
  echo "https://${prom_app}.internal.${domain}/api/v1/write"
}

# True when collector logs show Prometheus remote-write 404 errors.
collector_prom_rw_failing() {
  local logs=$1
  echo "$logs" | grep -q 'remote write returned HTTP status 404'
}

# True when collector logs show Prometheus remote-write 404 within the last N minutes.
collector_prom_rw_failing_recent() {
  local logs=$1 minutes=${2:-10}
  COLLECTOR_LOG_TEXT="$logs" RECENT_RW_MINUTES="$minutes" python3 - <<'PY'
import json
import os
import re
from datetime import datetime, timedelta, timezone

text = os.environ.get("COLLECTOR_LOG_TEXT", "")
minutes = int(os.environ.get("RECENT_RW_MINUTES", "10"))
cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
needle = "remote write returned HTTP status 404"

for line in text.splitlines():
    if needle not in line:
        continue
    ts = None
    try:
        obj = json.loads(line)
        raw = obj.get("TimeStamp") or obj.get("timestamp")
        if raw:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (json.JSONDecodeError, TypeError, ValueError):
        m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)", line)
        if m:
            raw = m.group(1).replace("Z", "+00:00")
            try:
                ts = datetime.fromisoformat(raw)
            except ValueError:
                ts = None
    if ts is None:
        continue
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts >= cutoff:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

restart_containerapp_revision() {
  local app=$1 rg=$2
  local rev
  rev=$(az containerapp show --name "$app" --resource-group "$rg" \
    --query "properties.latestRevisionName" -o tsv 2>/dev/null || true)
  [[ -n "$rev" && "$rev" != "None" ]] || return 1
  az containerapp revision restart \
    --name "$app" \
    --resource-group "$rg" \
    --revision "$rev" \
    --output none
}

# Wait for Prometheus on ACA (/-/ready often unreachable from Cloud Shell).
wait_for_prometheus_app() {
  local prom_app=$1 rg=$2
  local fqdn i prov run prom_logs ready_code

  fqdn=$(az containerapp show --name "$prom_app" --resource-group "$rg" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
  echo "[prometheus] Waiting for $prom_app ..."
  for i in $(seq 1 30); do
    prov=$(az containerapp show --name "$prom_app" --resource-group "$rg" \
      --query "properties.provisioningState" -o tsv 2>/dev/null || echo "")
    run=$(az containerapp show --name "$prom_app" --resource-group "$rg" \
      --query "properties.runningStatus" -o tsv 2>/dev/null || echo "")
    prom_logs=$(prometheus_console_logs "$prom_app" "$rg" 40)
    if [[ "$prov" == "Succeeded" && "$run" == "Running" ]]; then
      if prometheus_health_ok "$fqdn" "$prom_logs"; then
        echo "[prometheus] ready"
        return 0
      fi
      if [[ "$i" -ge 6 ]]; then
        echo "[prometheus] Running (ingress probe inconclusive from deploy host; continuing)"
        return 0
      fi
    fi
    ready_code="n/a"
    [[ -n "$fqdn" ]] && ready_code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
      "https://${fqdn}/-/ready" 2>/dev/null || echo "000")
    (( i % 4 == 0 )) && echo "[prometheus]  still waiting ($i/30) — prov=$prov run=$run ready_http=$ready_code"
    sleep 10
  done
  echo "[prometheus] WARN: not confirmed ready"
  return 1
}

# Import a container image into ACR (tries fallbacks; retries on Docker Hub 429).
ensure_acr_hub_import() {
  local acr=$1 primary_source=$2 acr_tag=$3
  shift 3
  local -a sources=("$primary_source" "$@")
  local src attempt out

  if az acr repository show --name "$acr" --image "$acr_tag" >/dev/null 2>&1; then
    return 0
  fi

  for attempt in 1 2 3; do
    for src in "${sources[@]}"; do
      [[ -z "$src" ]] && continue
      echo "[acr-import] ${src} -> ${acr_tag} (attempt ${attempt}/3)"
      if out=$(az acr import --name "$acr" \
          --source "$src" \
          --image "$acr_tag" \
          --force \
          --output none 2>&1); then
        return 0
      fi
      if echo "$out" | grep -qiE 'TOOMANYREQUESTS|Too Many Requests|429'; then
        echo "[acr-import] WARN: rate limited on ${src}; waiting 90s before retry ..."
        sleep 90
        break
      fi
      echo "[acr-import] WARN: import failed for ${src}: ${out}"
    done
  done
  echo "[acr-import] ERROR: could not import ${acr_tag}" >&2
  return 1
}

# Import Dockerfile base layers into ACR before az acr build.
prepare_acr_build_bases() {
  local acr=$1 dockerfile=$2
  local name
  name=$(basename "$dockerfile")
  case "$name" in
    Dockerfile.loki)
      ensure_acr_hub_import "$acr" "docker.io/grafana/loki:3.2.1" "imported/loki:3.2.1"
      ;;
    Dockerfile.tempo)
      ensure_acr_hub_import "$acr" "docker.io/grafana/tempo:2.6.1" "imported/tempo:2.6.1"
      ;;
    Dockerfile.prometheus)
      ensure_acr_hub_import "$acr" "docker.io/prom/prometheus:v2.54.1" "imported/prometheus:v2.54.1"
      ;;
    Dockerfile.collector)
      ensure_acr_hub_import "$acr" "docker.io/otel/opentelemetry-collector-contrib:0.111.0" \
        "imported/otel-collector-contrib:0.111.0" \
      && ensure_acr_hub_import "$acr" "mcr.microsoft.com/mirror/docker/library/alpine:3.20" \
        "imported/alpine:3.20" "docker.io/library/alpine:3.20"
      ;;
    Dockerfile.runner)
      ensure_acr_hub_import "$acr" "mcr.microsoft.com/mirror/docker/library/python:3.11-slim" \
        "imported/python:3.11-slim" "docker.io/library/python:3.11-slim"
      ;;
    Dockerfile.grafana)
      ensure_acr_hub_import "$acr" "mcr.microsoft.com/mirror/docker/library/python:3.12-alpine" \
        "imported/python:3.12-alpine" "docker.io/library/python:3.12-alpine" \
      && ensure_acr_hub_import "$acr" "docker.io/grafana/grafana:11.3.0" "imported/grafana:11.3.0"
      ;;
    *)
      return 0
      ;;
  esac
}

# Build-arg overrides that point FROM lines at ACR-imported bases.
acr_build_arg_overrides() {
  local login=$1 dockerfile=$2
  local name args=()
  name=$(basename "$dockerfile")
  case "$name" in
    Dockerfile.loki)
      args+=(--build-arg "LOKI_BASE=${login}/imported/loki:3.2.1") ;;
    Dockerfile.tempo)
      args+=(--build-arg "TEMPO_BASE=${login}/imported/tempo:2.6.1") ;;
    Dockerfile.prometheus)
      args+=(--build-arg "PROMETHEUS_BASE=${login}/imported/prometheus:v2.54.1") ;;
    Dockerfile.collector)
      args+=(
        --build-arg "OTEL_COLLECTOR_BASE=${login}/imported/otel-collector-contrib:0.111.0"
        --build-arg "ALPINE_BASE=${login}/imported/alpine:3.20"
      ) ;;
    Dockerfile.runner)
      args+=(--build-arg "PYTHON_BASE=${login}/imported/python:3.11-slim") ;;
    Dockerfile.grafana)
      args+=(
        --build-arg "PYTHON_BASE=${login}/imported/python:3.12-alpine"
        --build-arg "GRAFANA_BASE=${login}/imported/grafana:11.3.0"
      ) ;;
  esac
  printf '%s\n' "${args[@]}"
}

# Build and push an image via ACR Tasks using imported base layers.
acr_build_image() {
  local acr=$1 rg=$2 login=$3 tag=$4 dockerfile=$5 root=$6
  shift 6 || true
  local -a base_args=()
  if ! prepare_acr_build_bases "$acr" "$dockerfile"; then
    echo "[acr-build] ERROR: base image import failed for $(basename "$dockerfile")" >&2
    return 1
  fi
  while IFS= read -r arg; do
    [[ -n "$arg" ]] && base_args+=("$arg")
  done < <(acr_build_arg_overrides "$login" "$dockerfile")
  az acr build --registry "$acr" --resource-group "$rg" \
    --platform linux/amd64 \
    --build-arg "CACHEBUST=$(date +%s)" \
    "${base_args[@]}" \
    --image "$tag" \
    -f "$dockerfile" "$root" \
    "$@"
}

# Latest image digest in ACR (repo name without registry).
acr_latest_digest() {
  local acr=$1 repo=$2 digest
  digest=$(az acr repository show-manifests --name "$acr" \
    --repository "$repo" --orderby time_desc --top 1 \
    --query "[0].digest" -o tsv 2>/dev/null || true)
  [[ -n "$digest" && "$digest" != "None" ]] || return 1
  echo "$digest"
}

# Read-only Loki pipeline triage. Sets LOKI_TRIAGE_FIX to recommended script (if any).
triage_loki_pipeline() {
  local runner_app=$1 otel_app=$2 loki_app=$3 cae_name=$4 rg=$5
  local prefix=${6:-"[loki-triage]"}
  local acr_login=${ACR_LOGIN_SERVER:-}
  local runner_image collector_image loki_image
  local runner_logs_ep collector_loki_ep expected_logs expected_loki
  local runner_logs collector_logs
  local -a issues=()

  LOKI_TRIAGE_FIX=""

  runner_image=$(az containerapp show --name "$runner_app" --resource-group "$rg" \
    --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)
  collector_image=$(az containerapp show --name "$otel_app" --resource-group "$rg" \
    --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)
  loki_image=$(az containerapp show --name "$loki_app" --resource-group "$rg" \
    --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)

  runner_logs_ep=$(az containerapp show --name "$runner_app" --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
  collector_loki_ep=$(az containerapp show --name "$otel_app" --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='LOKI_OTLP_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
  expected_logs="$(resolve_azure_otel_logs_endpoint "$cae_name" "$rg" "$otel_app" 2>/dev/null || true)"
  expected_loki="$(resolve_azure_loki_otlp_endpoint "$cae_name" "$rg" "$loki_app" 2>/dev/null || true)"

  echo "$prefix === Loki pipeline triage ==="
  echo "$prefix   runner image:     ${runner_image:-unknown}"
  echo "$prefix   collector image:  ${collector_image:-unknown}"
  echo "$prefix   loki image:       ${loki_image:-unknown}"
  echo "$prefix   runner logs ep:   ${runner_logs_ep:-<unset>}"
  echo "$prefix   expected logs ep: ${expected_logs:-unknown}"
  echo "$prefix   collector loki:   ${collector_loki_ep:-<unset>}"
  echo "$prefix   expected loki:    ${expected_loki:-unknown}"

  if [[ -n "$loki_image" && "$loki_image" == grafana/loki:* ]]; then
    issues+=("Loki uses stock grafana/loki (no baked OTLP config)")
  elif [[ -n "$acr_login" && -n "$loki_image" && "$loki_image" != *"${acr_login}/loki"* ]]; then
    issues+=("Loki image is not ACR loki:latest")
  fi

  if [[ -n "$collector_image" && "$collector_image" == otel/opentelemetry-collector-contrib:* ]]; then
    issues+=("Collector uses stock image (no otlphttp/loki pipeline)")
  elif [[ -n "$acr_login" && -n "$collector_image" && "$collector_image" != *"${acr_login}/otel-collector"* ]]; then
    issues+=("Collector image is not ACR otel-collector:latest")
  fi

  if [[ -z "$runner_logs_ep" || ( -n "$expected_logs" && "$runner_logs_ep" != "$expected_logs" ) ]]; then
    issues+=("Runner OTLP logs endpoint missing or wrong")
  fi
  if [[ -z "$collector_loki_ep" || ( -n "$expected_loki" && "$collector_loki_ep" != "$expected_loki" ) ]]; then
    issues+=("Collector LOKI_OTLP_ENDPOINT missing or wrong")
  fi

  runner_logs=$(az containerapp logs show --name "$runner_app" --resource-group "$rg" \
    --type console --tail 120 2>/dev/null || true)
  if echo "$runner_logs" | grep -q "OTLP log exporter"; then
    echo "$prefix   runner OTLP logs: OK (exporter initialized)"
  elif echo "$runner_logs" | grep -qi "OTEL_EXPORTER_OTLP_ENDPOINT is not set"; then
    issues+=("Runner started without OTLP endpoint")
    echo "$prefix   runner OTLP logs: FAIL (no OTLP endpoint)"
  elif echo "$runner_logs" | grep -qi "OTLP log exporter init failed"; then
    issues+=("Runner OTLP log exporter failed to initialize")
    echo "$prefix   runner OTLP logs: FAIL (init failed)"
  else
    issues+=("Runner logs lack 'OTLP log exporter' line (stale image?)")
    echo "$prefix   runner OTLP logs: FAIL (no exporter line in console)"
  fi

  collector_logs=$(az containerapp logs show --name "$otel_app" --resource-group "$rg" \
    --type console --tail 80 2>/dev/null || true)
  if echo "$collector_logs" | grep -Eiq 'otlphttp/loki.*error|error.*loki|failed.*loki|Exporting failed.*loki'; then
    issues+=("Collector → Loki export errors in recent logs")
    echo "$prefix   collector → loki: FAIL (export errors)"
    echo "$collector_logs" | grep -Ei 'loki|otlphttp' | grep -Ei 'error|failed|404|401|refused|Permanent' | tail -3 | sed "s/^/${prefix}     /"
  else
    echo "$prefix   collector → loki: no obvious export errors"
  fi

  if [[ ${#issues[@]} -eq 0 ]]; then
    echo "$prefix   verdict: env/images look OK — wait for batches or check collector accepted_log_records (OTLP gRPC via :80 ingress)"
    return 0
  fi

  echo "$prefix   issues:"
  local issue
  for issue in "${issues[@]}"; do
    echo "$prefix     - $issue"
  done

  local need_rebuild=false need_runner=false
  for issue in "${issues[@]}"; do
    [[ "$issue" == *"Loki"* || "$issue" == *"Collector"* ]] && need_rebuild=true
    [[ "$issue" == *"Runner"* ]] && need_runner=true
  done

  if [[ "$need_rebuild" == "true" ]]; then
    LOKI_TRIAGE_FIX="./scripts/fix-loki-pipeline-azure.sh"
    echo "$prefix   recommended: $LOKI_TRIAGE_FIX"
    if [[ "$need_runner" == "true" ]]; then
      echo "$prefix   also run:    ./scripts/fix-loki-pipeline-azure.sh --runner-build"
    fi
  elif [[ "$need_runner" == "true" ]]; then
    LOKI_TRIAGE_FIX="./scripts/fix-loki-pipeline-azure.sh --runner-build"
    echo "$prefix   recommended: $LOKI_TRIAGE_FIX"
  else
    LOKI_TRIAGE_FIX="./scripts/wire-loki-otlp-azure.sh"
    echo "$prefix   recommended: $LOKI_TRIAGE_FIX"
  fi
  return 1
}

# Refresh OTel Collector backend env vars and force a new revision.
refresh_collector_backends() {
  local otel_app=$1 cae_name=$2 rg=$3 prom_app=${4:-prometheus-scraper-dev} \
        loki_app=${5:-loki-telemetry-dev} tempo_app=${6:-tempo-telemetry-dev}
  local tempo_ep loki_ep prom_ep
  tempo_ep="http://${tempo_app}.internal.$(cae_default_domain "$cae_name" "$rg"):4317"
  loki_ep="$(resolve_azure_loki_otlp_endpoint "$cae_name" "$rg" "$loki_app")"
  prom_ep="$(resolve_azure_prom_write_endpoint "$cae_name" "$rg" "$prom_app")"
  az containerapp update \
    --name "$otel_app" \
    --resource-group "$rg" \
    --set-env-vars \
      "TEMPO_ENDPOINT=${tempo_ep}" \
      "LOKI_OTLP_ENDPOINT=${loki_ep}" \
      "PROM_WRITE_ENDPOINT=${prom_ep}" \
      "DEPLOY_STAMP=$(date +%s)" \
    --output none
}
