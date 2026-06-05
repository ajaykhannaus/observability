# Shared helpers for Azure Container Apps deploy scripts.
# shellcheck shell=bash

[[ -n "${_AZURE_DEPLOY_COMMON_LOADED:-}" ]] && return 0
_AZURE_DEPLOY_COMMON_LOADED=1

# Escape a value for use as the replacement side of awk gsub().
awk_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g'
}

# OTLP gRPC endpoint for Container Apps (never localhost on Azure).
resolve_azure_otel_endpoint() {
  local cae_name=$1 rg=$2 otel_app=${3:-otel-collector-dev}
  local env_ep=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
  if [[ -z "$env_ep" || "$env_ep" == *localhost* || "$env_ep" == *127.0.0.1* ]]; then
    local domain
    domain=$(az containerapp env show --name "$cae_name" --resource-group "$rg" \
      --query properties.defaultDomain -o tsv 2>/dev/null || true)
    if [[ -n "$domain" ]]; then
      echo "http://${otel_app}.internal.${domain}:4317"
      return 0
    fi
  fi
  [[ -n "$env_ep" ]] && echo "$env_ep" || echo ""
}

# OTLP HTTP endpoint for logs (port 4318 — more reliable than gRPC on ACA internal ingress).
resolve_azure_otel_logs_endpoint() {
  local cae_name=$1 rg=$2 otel_app=${3:-otel-collector-dev}
  local env_ep=${OTEL_EXPORTER_OTLP_LOGS_ENDPOINT:-}
  if [[ -z "$env_ep" || "$env_ep" == *localhost* || "$env_ep" == *127.0.0.1* ]]; then
    local domain
    domain=$(az containerapp env show --name "$cae_name" --resource-group "$rg" \
      --query properties.defaultDomain -o tsv 2>/dev/null || true)
    if [[ -n "$domain" ]]; then
      echo "http://${otel_app}.internal.${domain}:4318"
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
