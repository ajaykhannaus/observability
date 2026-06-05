#!/usr/bin/env bash
# Diagnose Grafana datasource connectivity and Prometheus data on Azure.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/azure-deploy-common.sh
source "$ROOT/scripts/lib/azure-deploy-common.sh"
ENV_FILE="${ENV_FILE:-$ROOT/.env.azure}"

log() { echo "[diagnose-grafana] $*"; }
ok() { log "OK  $*"; }
fail() { log "FAIL $*"; }

[[ -f "$ENV_FILE" ]] || { log "ERROR: Missing $ENV_FILE"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

GRAFANA_APP_NAME="${GRAFANA_APP_NAME:-grafana-telemetry-dev}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
PROM_APP_NAME="${PROM_APP_NAME:-prometheus-scraper-dev}"
OTEL_APP_NAME="${OTEL_APP_NAME:-otel-collector-dev}"
LOKI_APP_NAME="${LOKI_APP_NAME:-loki-telemetry-dev}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

GRAFANA_FQDN=$(az containerapp show --name "$GRAFANA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
GRAFANA_URL="https://${GRAFANA_FQDN}"

log "Grafana: $GRAFANA_URL"
log "Runner FQDN: $(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)"

CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
EXPECTED_OTEL="http://${OTEL_APP_NAME}.internal.$(cae_default_domain "$CAE_NAME" "$AZURE_RESOURCE_GROUP")"
EXPECTED_LOKI_OTLP="$(resolve_azure_loki_otlp_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$LOKI_APP_NAME" 2>/dev/null || echo "")"
EXPECTED_OTEL_LOGS="$(resolve_azure_otel_logs_endpoint "$CAE_NAME" "$AZURE_RESOURCE_GROUP" "$OTEL_APP_NAME")"

echo ""
log "=== Runner OTLP env (logs need this → OTel Collector → Loki) ==="
RUNNER_OTEL=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_ENDPOINT' || name=='OTEL_EXPORTER_OTLP_LOGS_ENDPOINT' || name=='ALLOW_MOCK_MODE' || name=='OTEL_SERVICE_NAME']" -o table 2>/dev/null \
  || fail "Could not read runner env"
if [[ -z "$RUNNER_OTEL" || "$RUNNER_OTEL" == *localhost* || "$RUNNER_OTEL" == *127.0.0.1* ]]; then
  fail "Runner OTEL_EXPORTER_OTLP_ENDPOINT missing or points at localhost"
  log "  Expected: ${EXPECTED_OTEL}:4317"
  log "  Fix: ./scripts/fix-runner.sh --no-git-pull"
elif [[ "$RUNNER_OTEL" != "${EXPECTED_OTEL}:4317" ]]; then
  fail "Runner OTLP endpoint does not match collector internal URL"
  log "  Got:      $RUNNER_OTEL"
  log "  Expected: ${EXPECTED_OTEL}:4317"
else
  ok "Runner OTLP endpoint → collector"
fi
RUNNER_OTEL_LOGS=$(az containerapp show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
if [[ -z "$RUNNER_OTEL_LOGS" ]]; then
  fail "Runner OTEL_EXPORTER_OTLP_LOGS_ENDPOINT not set — logs may fail on ACA gRPC ingress"
  log "  Expected: $EXPECTED_OTEL_LOGS"
elif [[ "$RUNNER_OTEL_LOGS" != "$EXPECTED_OTEL_LOGS" ]]; then
  fail "Runner OTLP logs endpoint mismatch"
  log "  Got:      $RUNNER_OTEL_LOGS"
  log "  Expected: $EXPECTED_OTEL_LOGS"
else
  ok "Runner OTLP logs endpoint → collector :4318 (HTTP)"
fi

echo ""
log "=== OTel Collector → Loki wiring ==="
COLLECTOR_LOKI=$(az containerapp show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='LOKI_OTLP_ENDPOINT'].value | [0]" -o tsv 2>/dev/null || true)
COLLECTOR_IMAGE=$(az containerapp show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)
LOKI_IMAGE=$(az containerapp show --name "$LOKI_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)
log "  collector image: ${COLLECTOR_IMAGE:-unknown}"
log "  loki image:      ${LOKI_IMAGE:-unknown}"
log "  LOKI_OTLP_ENDPOINT: ${COLLECTOR_LOKI:-<unset>}"
log "  expected:           $EXPECTED_LOKI_OTLP"
if [[ -z "$COLLECTOR_LOKI" ]]; then
  fail "Collector LOKI_OTLP_ENDPOINT not set — logs pipeline cannot export to Loki"
  log "  Fix: export FORCE_CONTAINER_DEPLOY=true && ./scripts/deploy-observability-stack.sh --build --from otel"
elif [[ "$COLLECTOR_LOKI" != "$EXPECTED_LOKI_OTLP" ]]; then
  fail "Collector LOKI_OTLP_ENDPOINT mismatch"
else
  ok "Collector Loki OTLP endpoint"
fi
if [[ -n "$LOKI_IMAGE" && "$LOKI_IMAGE" == grafana/loki:* ]]; then
  fail "Loki is using stock grafana/loki image (no baked OTLP config) — rebuild with Dockerfile.loki"
  log "  Fix: ./scripts/fix-loki-logs-azure.sh"
fi

echo ""
log "=== Observability apps ==="
for app in "$LOKI_APP_NAME" "$OTEL_APP_NAME"; do
  state=$(az containerapp show --name "$app" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "{running:properties.runningStatus,prov:properties.provisioningState,replicas:properties.template.scale.minReplicas}" -o json 2>/dev/null \
    || echo '{"error":"missing"}')
  log "  $app: $state"
done

echo ""
log "=== Runner console — OTLP log exporter (last 80 lines) ==="
RUNNER_LOGS=$(az containerapp logs show --name "$APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 80 2>/dev/null || true)
if echo "$RUNNER_LOGS" | grep -q "OTLP log exporter"; then
  ok "Runner initialized OTLP log exporter"
  echo "$RUNNER_LOGS" | grep "OTLP log exporter" | tail -1 | sed 's/^/[diagnose-grafana]   /'
elif echo "$RUNNER_LOGS" | grep -qi "OTEL_EXPORTER_OTLP_ENDPOINT is not set"; then
  fail "Runner started without OTLP endpoint — logs will not leave the runner"
elif echo "$RUNNER_LOGS" | grep -qi "OTLP log exporter init failed"; then
  fail "Runner OTLP log exporter failed to initialize"
  echo "$RUNNER_LOGS" | grep -i "OTLP log exporter" | tail -3 | sed 's/^/[diagnose-grafana]   /'
else
  fail "No 'OTLP log exporter' line in runner logs — runner may need redeploy"
  log "  Fix: ./scripts/fix-runner.sh --build --no-git-pull"
fi

echo ""
log "=== Prometheus remote write receiver ==="
PROM_FQDN=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)
PROM_IMAGE=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)
log "  image: ${PROM_IMAGE:-unknown}"
PROM_CMD=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].command" -o tsv 2>/dev/null || true)
PROM_ARGS=$(az containerapp show --name "$PROM_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "properties.template.containers[0].args" -o tsv 2>/dev/null || true)
log "  command: ${PROM_CMD:-<image default>}"
log "  args:    ${PROM_ARGS:-<none>}"
if [[ -n "$PROM_CMD" && "$PROM_CMD" != *prometheus-entrypoint* ]]; then
  fail "Prometheus container command overrides entrypoint — remote write flag may be missing"
  log "  Fix: ./scripts/fix-prometheus-remote-write-azure.sh"
fi
if [[ -n "$PROM_FQDN" ]]; then
  PROM_RW_CODE=$(curl -sk -o /dev/null -w '%{http_code}' -X POST \
    "https://${PROM_FQDN}/api/v1/write" --max-time 15 2>/dev/null || echo "000")
  if [[ "$PROM_RW_CODE" == "404" ]]; then
    fail "Prometheus /api/v1/write returns 404 — remote write receiver not enabled"
    log "  Fix: ./scripts/fix-prometheus-remote-write-azure.sh"
  elif [[ "$PROM_RW_CODE" == "000" ]]; then
    log "  (could not probe https://${PROM_FQDN}/api/v1/write from this host)"
  else
    ok "Prometheus remote write endpoint reachable (POST → HTTP $PROM_RW_CODE)"
  fi
else
  fail "Prometheus has no ingress FQDN"
fi

echo ""
log "=== OTel Collector console — export errors (last 80 lines) ==="
COLLECTOR_LOGS=$(az containerapp logs show --name "$OTEL_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --type console --tail 80 2>/dev/null || true)
if echo "$COLLECTOR_LOGS" | grep -q 'remote write returned HTTP status 404'; then
  fail "Collector → Prometheus remote write still failing (404)"
  log "  Fix: ./scripts/fix-prometheus-remote-write-azure.sh"
  echo "$COLLECTOR_LOGS" | grep 'remote write returned HTTP status 404' | tail -3 | sed 's/^/[diagnose-grafana]   /'
elif echo "$COLLECTOR_LOGS" | grep -Eiq 'error|failed|refused|404|401|tls|handshake'; then
  echo "$COLLECTOR_LOGS" | grep -Ei 'error|failed|refused|404|401|tls|handshake|loki|otlphttp|prometheusremotewrite' | tail -8 | sed 's/^/[diagnose-grafana]   /'
else
  log "  (no obvious export errors in recent collector logs)"
fi
if echo "$COLLECTOR_LOGS" | grep -q "invalid OTel Collector config"; then
  fail "Collector config validation failed on last start"
fi

python3 - "$GRAFANA_URL" "$GRAFANA_ADMIN_PASSWORD" <<'PY'
import base64
import json
import sys
import urllib.error
import urllib.request

grafana, password = sys.argv[1:3]
auth = base64.b64encode(f"admin:{password}".encode()).decode()

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{grafana}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(r, timeout=45) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}

print("\n=== Datasources ===")
for ds in req("GET", "/api/datasources"):
    uid = ds["uid"]
    print(f"  {ds['name']} ({uid}): {ds.get('url')}")
    if ds["type"] != "tempo":
        try:
            h = req("GET", f"/api/datasources/uid/{uid}/health")
            print(f"    health: {h.get('status', h)}")
        except urllib.error.HTTPError as exc:
            print(f"    health: HTTP {exc.code}")

print("\n=== Prometheus queries (via Grafana proxy) ===")
queries = [
    ("raw counter", "ai_gateway_request_count_total"),
    ("recording rule 6h SLI", "ai_gateway:sli:availability:6h"),
    ("runner batch metric", "ai_telemetry_runner_batch_duration_seconds_count"),
]
for label, promql in queries:
    try:
        body = {
            "queries": [{
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": "prometheus-ds"},
                "expr": promql,
                "instant": True,
            }],
            "from": "now-1h",
            "to": "now",
        }
        result = req("POST", "/api/ds/query", body)
        frames = result.get("results", {}).get("A", {}).get("frames", [])
        n = sum(len(f.get("data", {}).get("values", [[]])[0]) for f in frames) if frames else 0
        print(f"  {label}: {n} series/points")
        if n == 0:
            print(f"    WARN: no data for `{promql}` — runner scrape or recording rules may be missing")
    except Exception as exc:
        print(f"  {label}: ERROR {exc}")

print("\n=== Loki label names (via Grafana → Loki) ===")
try:
    labels = req("GET", "/api/datasources/proxy/uid/loki-ds/loki/api/v1/labels")
    names = labels.get("data") or []
    print(f"  labels ({len(names)}): {', '.join(names[:20])}{'...' if len(names) > 20 else ''}")
    if names and "service_name" not in names:
        print("    WARN: no service_name label — OTLP logs may not be ingested yet")
except Exception as exc:
    print(f"  ERROR listing labels: {exc}")

print("\n=== Loki queries (Live Telemetry Events panel) ===")
loki_queries = [
    ("any service_name streams (15m)", 'sum(count_over_time({service_name=~".+"} [15m]))'),
    (
        "telemetry_event (15m)",
        'sum(count_over_time({service_name=~".+"} | json | event_type="telemetry_event" [15m]))',
    ),
    (
        "startup_config (15m)",
        'sum(count_over_time({service_name=~".+"} | json | event_type="startup_config" [15m]))',
    ),
]
loki_stream_count = None
for label, logql in loki_queries:
    try:
        body = {
            "queries": [{
                "refId": "A",
                "datasource": {"type": "loki", "uid": "loki-ds"},
                "expr": logql,
                "queryType": "instant",
                "instant": True,
            }],
            "from": "now-15m",
            "to": "now",
        }
        result = req("POST", "/api/ds/query", body)
        err = result.get("results", {}).get("A", {}).get("error")
        if err:
            print(f"  {label}: ERROR {err}")
            continue
        frames = result.get("results", {}).get("A", {}).get("frames", [])
        total = 0.0
        for frame in frames:
            for col in frame.get("data", {}).get("values") or []:
                for v in col:
                    if v is not None:
                        try:
                            total += float(v)
                        except (TypeError, ValueError):
                            pass
        print(f"  {label}: {int(total)}")
        if label == "any service_name streams (15m)":
            loki_stream_count = int(total)
        if total == 0 and label == "any service_name streams (15m)":
            print("    WARN: no Loki streams — runner → collector → Loki pipeline is broken")
            print("    Fix: ./scripts/fix-loki-logs-azure.sh")
        elif (
            total == 0
            and "telemetry_event" in label
            and loki_stream_count is not None
            and loki_stream_count > 0
        ):
            print("    WARN: logs exist in Loki but no telemetry_event — check runner batches / LogQL")
    except Exception as exc:
        print(f"  {label}: ERROR {exc}")

print("\n=== Dashboards in Grafana ===")
for d in req("GET", "/api/search?type=dash-db"):
    print(f"  {d.get('title')} uid={d.get('uid')} url={d.get('url')}")
PY

log "If Prometheus remote write 404: ./scripts/fix-prometheus-remote-write-azure.sh"
log "If Prometheus OK but Loki empty: ./scripts/fix-loki-logs-azure.sh"
log "If runner missing OTLP log exporter line: ./scripts/fix-runner.sh --build --no-git-pull"
log "If collector LOKI_OTLP_ENDPOINT wrong: export FORCE_CONTAINER_DEPLOY=true && ./scripts/deploy-observability-stack.sh --build --from otel"
log "If dashboards missing, run: ./scripts/fix-grafana-datasources.sh"
