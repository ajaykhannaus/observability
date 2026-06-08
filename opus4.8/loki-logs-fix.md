# Loki "Live Telemetry Events" — No Logs: Fix + Diagnose

**Symptom:** Grafana panel "Live Telemetry Events" is empty. `diagnose-grafana` shows
`Loki labels (0)` and `telemetry_event (15m): 0` — **nothing has ever reached Loki** —
even though Prometheus metrics flow fine (90+ series).

**Why:** Metrics export over **gRPC :4317** (proven working). Logs were the *only* signal
using **HTTP :4318**. So the fix is to send logs over the same proven `:4317` gRPC channel.
(The diagnose "no OTLP log exporter line" check reads only the last 80 log lines and is an
unreliable false-negative — trust the collector self-telemetry counters in STEP 3, not that grep.)

> NOTE: The lines below are real commands. The earlier "RG = ..." block was a reference
> table, not commands — that's why you got `command not found`. Use the assignments here
> (no spaces around `=`).

---

## STEP 1 — Apply the fix (route logs over gRPC :4317)

Copy/paste this whole block:

```bash
RG=az03-al-titan-sandbox-rg
RUNNER=ai-telemetry-runner-dev
OTEL_GRPC=http://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io:4317

az containerapp update -n "$RUNNER" -g "$RG" \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_GRPC" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "DEPLOY_STAMP=$(date +%s)" \
  --output none

echo "env updated — new revision rolling out"
```

---

## STEP 2 — Confirm the exporter actually starts (reliable, catches startup)

A restart was just triggered by STEP 1. Immediately stream the NEW container's logs —
`--follow` catches the once-only startup line that `--tail` misses:

```bash
timeout 90 az containerapp logs show -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg \
  --type console --follow 2>/dev/null \
  | grep --line-buffered -iE "OTLP log exporter|exporter init failed|is not set|mock mode|publisher" \
  | head -8
```

- See `OTLP log exporter → ...:4317 (grpc...)` → exporter is UP. Wait ~2 min, go to STEP 4.
- See `init failed` / `is not set` → paste that line back to me.
- See nothing in 90s → go to STEP 3 (check the collector receives anything).

---

## STEP 3 — Localize precisely (collector self-telemetry)

Run these 3 in **Grafana → Explore → Prometheus datasource** (the UI, not the terminal —
the collector's :8888 is internal-only):

```promql
sum(otelcol_receiver_accepted_log_records_total)
```
```promql
sum(otelcol_exporter_sent_log_records_total{exporter="otlphttp/loki"})
```
```promql
sum(otelcol_exporter_send_failed_log_records_total{exporter="otlphttp/loki"})
```

| accepted | sent | failed | Meaning | Next |
|---|---|---|---|---|
| **0** | 0 | 0 | Collector still not receiving logs from runner | STEP 1 didn't take / runner not emitting — paste STEP 2 output |
| >0 | 0 | **>0** | Collector reaches Loki but Loki **rejects** | STEP 5 (rebuild Loki) |
| >0 | >0 | 0 | Logs ARE in Loki — panel/query issue only | STEP 4 should pass |

---

## STEP 4 — Verify logs landed

```bash
./scripts/diagnose-grafana-azure.sh
```

Expect `telemetry_event (15m)` **> 0** and `Loki labels` non-zero. In Grafana → Explore → Loki:

```logql
{service_name=~".+"} | json | event_type="telemetry_event"
```

Then the "Live Telemetry Events" panel populates.

---

## STEP 5 — Only if STEP 3 shows accepted>0 AND failed>0 (Loki rejecting)

Get the real rejection reason:

```bash
az containerapp logs show -n otel-collector-dev -g az03-al-titan-sandbox-rg \
  --type console --tail 200 2>/dev/null \
  | grep -iE "loki|otlphttp" | grep -iE "error|failed|refused|404|400|429|permanent"
```

Then rebuild Loki + collector from current config:

```bash
./scripts/fix-loki-logs-azure.sh
```

Common rejections: `404` on `/otlp/v1/logs` (Loki missing native-OTLP config) →
rebuild fixes it; `connection refused` (Loki ingress not mapping 443→3100).
