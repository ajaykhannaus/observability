# Loki "Live Telemetry Events" — No Logs: Diagnose + Fix

**Symptom:** Grafana panel "Live Telemetry Events" is empty. `diagnose-grafana` shows
`Loki labels (0)` and `telemetry_event (15m): 0` — i.e. **nothing has ever reached Loki**,
even though Prometheus metrics flow fine (90+ series).

**Root-cause logic:** Metrics export over **gRPC :4317** (works). Logs are the only signal
using **HTTP :4318**. So the break is specific to the logs path. The triage's
"no OTLP log exporter line" is a **false negative** — that line prints once at startup and
scrolls past the `--tail 80` window. Trust **collector self-telemetry + 0 Loki streams**, not
the grep.

Environment values used below (from the deployed dev stack):

```
RG    = az03-al-titan-sandbox-rg
RUNNER= ai-telemetry-runner-dev
OTEL  = otel-collector-dev
LOKI  = loki-telemetry-dev
DOMAIN= bravesand-913bfe11.eastus.azurecontainerapps.io
```

---

## STEP 1 — Localize the break with collector self-telemetry

Run these 3 queries in **Grafana → Explore → Prometheus datasource**:

```promql
sum(otelcol_receiver_accepted_log_records_total)
```
```promql
sum(otelcol_exporter_sent_log_records_total{exporter="otlphttp/loki"})
```
```promql
sum(otelcol_exporter_send_failed_log_records_total{exporter="otlphttp/loki"})
```

| accepted | sent | failed | Meaning | Go to |
|---|---|---|---|---|
| **0** | 0 | 0 | Runner not exporting logs to collector (`:4318`) | STEP 2 |
| >0 | 0 | **>0** | Collector reaches Loki but Loki **rejects** | STEP 3 |
| >0 | >0 | 0 | Logs ARE in Loki — panel/query issue, not pipeline | STEP 4 |

---

## STEP 1b — Confirm the runner's real startup line (not tail-80)

```bash
az containerapp logs show -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg \
  --type console --tail 500 2>/dev/null \
  | grep -iE "OTLP log exporter|exporter init failed|OTLP_LOGS|is not set"
```

- See `OTLP log exporter → ...:4318 (http/protobuf)` → exporter is UP, break is downstream → STEP 3.
- See `init failed` or nothing at all → runner export path is broken → STEP 2.

---

## STEP 2 — Fix: route logs over the proven gRPC :4317 path

Metrics already succeed over gRPC :4317. Send logs over the **same** port instead of the
separate HTTP :4318. `otel_logging.py` auto-selects the gRPC exporter when the endpoint is
NOT `:4318` and protocol is `grpc`.

```bash
az containerapp update -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io:4317" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "DEPLOY_STAMP=$(date +%s)"
```

Force a fresh revision + wait:

```bash
az containerapp revision restart -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg \
  --revision $(az containerapp show -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg \
      --query properties.latestRevisionName -o tsv)
sleep 150
```

Re-check STEP 1 — `accepted` should now climb above 0, then verify in STEP 4.

---

## STEP 3 — Fix: collector → Loki rejection (failed > 0)

Get the actual Loki export error from the collector:

```bash
az containerapp logs show -n otel-collector-dev -g az03-al-titan-sandbox-rg \
  --type console --tail 200 2>/dev/null \
  | grep -iE "loki|otlphttp" | grep -iE "error|failed|refused|404|400|429|permanent"
```

Check Loki is actually serving the native OTLP endpoint:

```bash
az containerapp logs show -n loki-telemetry-dev -g az03-al-titan-sandbox-rg \
  --type console --tail 100 2>/dev/null \
  | grep -iE "otlp|push|error|level=warn|level=error"
```

Common causes:
- **404 on /otlp/v1/logs** → Loki image lacks native-OTLP config (`allow_structured_metadata: true`,
  schema v13). Rebuild Loki: `./scripts/fix-loki-logs-azure.sh`
- **connection refused / 503** → Loki internal ingress not mapping 443→3100, or Loki restarting.
- **400 / structured metadata** → schema mismatch; rebuild Loki with current config.

---

## STEP 4 — Verify logs are landing

Grafana → Explore → **Loki** datasource:

```logql
{service_name=~".+"} | json | event_type="telemetry_event"
```

Or re-run the full diagnose:

```bash
./scripts/diagnose-grafana-azure.sh
```

Expect `telemetry_event (15m)` to be **> 0** and `Loki labels` to be non-zero.
Then the "Live Telemetry Events" panel populates.

---

## Quick reference — copy/paste block (STEP 2 fix, the most likely one)

```bash
RG=az03-al-titan-sandbox-rg
RUNNER=ai-telemetry-runner-dev
OTEL_GRPC=http://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io:4317

az containerapp update -n "$RUNNER" -g "$RG" \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_GRPC" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "DEPLOY_STAMP=$(date +%s)"

az containerapp revision restart -n "$RUNNER" -g "$RG" \
  --revision $(az containerapp show -n "$RUNNER" -g "$RG" \
      --query properties.latestRevisionName -o tsv)

sleep 150
./scripts/diagnose-grafana-azure.sh
```
