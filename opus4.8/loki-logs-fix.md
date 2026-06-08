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
| **0** | 0 | 0 | Collector still not receiving logs from runner | run the control queries below |
| >0 | 0 | **>0** | Collector reaches Loki but Loki **rejects** | STEP 5 (rebuild Loki) |
| >0 | >0 | 0 | Logs ARE in Loki — panel/query issue only | STEP 4 should pass |

### STEP 3b — Control queries (run if the log query shows "No data")

"No data" on the log counter is meaningful — a zero-series counter doesn't render. Confirm the
collector self-telemetry is reaching Prometheus at all by running two metrics that MUST exist:

```promql
otelcol_receiver_accepted_metric_points_total
```
```promql
otelcol_process_uptime_seconds
```

- **Control queries = data, log query = "No data"** → collector is alive and scraped, receiving
  **metrics** but **zero logs**. The runner's OTLP log exporter never sends — problem is
  **runner-side**, not the port or collector→Loki. Capture the runner boot line with STEP 3c.
- **Control queries also "No data"** → collector self-metrics aren't in Prometheus; diagnose
  separately (collector `prometheus/self` scrape / remote-write).

### STEP 3c — Capture the runner's real boot line (no Kafka-publisher noise)

```bash
RG=az03-al-titan-sandbox-rg
RUNNER=ai-telemetry-runner-dev
REV=$(az containerapp show -n "$RUNNER" -g "$RG" --query properties.latestRevisionName -o tsv)
az containerapp revision restart -n "$RUNNER" -g "$RG" --revision "$REV"

# capture the FIRST ~45s of boot, keep only otel-logging lines, drop publisher noise
timeout 45 az containerapp logs show -n "$RUNNER" -g "$RG" --type console --follow 2>/dev/null \
  | grep --line-buffered -iE "otel|otlp|log exporter|logger provider|endpoint" \
  | grep --line-buffered -viE "publisher|kafka|undelivered|timed out" \
  | head -10
```

- See `OTLP log exporter → ...:4317 (grpc...)` → exporter IS up; the break is collector→Loki.
- See `init failed` / `is not set` / nothing → the exporter bailed early; runner code/runtime fix.

#### If STEP 3c shows `UNAVAILABLE` / `DEADLINE_EXCEEDED` on :4317 → collector is DOWN

```
ERROR ...exporter Failed to export logs to ...:4317, error code: StatusCode.DEADLINE_EXCEEDED
ERROR ...exporter Failed to export metrics to ...:4317, error code: StatusCode.UNAVAILABLE
```

This is the real root cause when Loki is empty **and** `otelcol_process_uptime_seconds` shows
"No data": the runner exporter works, but the **OTel Collector is unreachable** — `UNAVAILABLE`
= can't connect, `DEADLINE_EXCEEDED` = connects but never responds. (The "metrics flow fine"
you see in Grafana is the runner's own `:8000/metrics` scraped **directly** by Prometheus —
that path never touches the collector, so it stays green while the collector is down.)

Go to **STEP 3d** to confirm whether the collector is scaled-to-zero or crash-looping.

### STEP 3d — Check the collector is actually running

```bash
RG=az03-al-titan-sandbox-rg
OTEL=otel-collector-dev

# running state + replica bounds (minReplicas:0 = it can scale to zero and go cold)
az containerapp show -n "$OTEL" -g "$RG" \
  --query "{running:properties.runningStatus, minReplicas:properties.template.scale.minReplicas, maxReplicas:properties.template.scale.maxReplicas, latestRev:properties.latestRevisionName}" -o table

# per-revision replica counts + health
az containerapp revision list -n "$OTEL" -g "$RG" \
  --query "[].{rev:name, active:properties.active, replicas:properties.replicas, state:properties.runningState, created:properties.createdTime}" -o table

# recent collector logs — crash-loop / bad-config?
az containerapp logs show -n "$OTEL" -g "$RG" --type console --tail 60 2>/dev/null
```

- `replicas: 0` / `minReplicas: 0` → collector scaled to zero (OTLP push doesn't keep it warm).
  Pin it always-on: `az containerapp update -n "$OTEL" -g "$RG" --min-replicas 1`
- `runningState: Failed` or repeating error lines in logs → crash-looping on config; capture the
  error and rebuild: `./scripts/fix-loki-logs-azure.sh`
- Collector healthy with replicas ≥ 1 but runner still times out → internal ingress/DNS for
  `:4317` — confirm the collector ingress exposes `targetPort: 4317` (transport http2).

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
