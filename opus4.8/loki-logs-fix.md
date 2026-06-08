# Loki "Live Telemetry Events" — No Logs: Fix + Diagnose

**Symptom:** Grafana panel "Live Telemetry Events" is empty. `diagnose-grafana` shows
`Loki labels (0)` and `telemetry_event (15m): 0` — **nothing has ever reached Loki** —
even though Prometheus metrics flow fine (90+ series).

**Why (CONFIRMED via STEP 3e ingress read):** The collector's main HTTP/2 ingress is
`targetPort: 4317, allowInsecure: false` — so `:4317` is reachable **only via 443 (TLS)**, NOT
as a raw port at the internal FQDN. The runner pointed at raw `:4317` → every export failed with
`UNAVAILABLE`/`DEADLINE_EXCEEDED`. Only **`:4318` (HTTP)** is an exposed raw TCP port
(`additionalPortMappings exposedPort: 4318`). So the working fix is to send logs over **HTTP :4318**.
(Metrics "flowing fine" in Grafana is the runner's own `:8000/metrics` scraped directly by
Prometheus — that path never touches the collector, so it masked the collector being unreachable.)

> NOTE: The lines below are real commands. Use the assignments here (no spaces around `=`).

---

## STEP 1 — Apply the fix (route logs over HTTP :4318 — the only exposed OTLP port)

Copy/paste this whole block:

```bash
RG=az03-al-titan-sandbox-rg
RUNNER=ai-telemetry-runner-dev
OTEL_HTTP=http://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io:4318

az containerapp update -n "$RUNNER" -g "$RG" \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_HTTP" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "DEPLOY_STAMP=$(date +%s)" \
  --output none

echo "env updated — logs repointed to :4318 HTTP — new revision rolling out"
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

# capture boot; show exporter status AND export errors (do NOT filter "timed out" — that hides
# the very log-export timeout we need to see)
timeout 60 az containerapp logs show -n "$RUNNER" -g "$RG" --type console --follow 2>/dev/null \
  | grep --line-buffered -iE "OTLP log exporter|Failed to export|StatusCode|is not set|exporter init" \
  | head -15
```

- See `OTLP log exporter → ...:80 (grpc...)` with NO `StatusCode`/`Failed to export` → exporter UP
  and sending; the break (if any) is collector→Loki.
- See `StatusCode.UNAVAILABLE/DEADLINE_EXCEEDED` / `Failed to export` → still unreachable; switch
  to the 443-TLS alternative in STEP 3h.
- See `init failed` / `is not set` / nothing → exporter bailed early; paste it back.

> NOTE: an EMPTY capture (only "Restart succeeded") is inconclusive — the once-only success line
> can scroll past before `--follow` attaches. Confirm via the collector counter instead:
> STEP 3 `sum(otelcol_receiver_accepted_log_records_total)` should now be > 0.

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
  `:4317` — go to **STEP 3e** to read the actual exposed ports.

### STEP 3e — Collector is UP but runner can't reach :4317 → ingress port mismatch

If STEP 3d shows the collector `Running` with `Everything is ready` in its logs AND its startup
shows `Starting GRPC server ... endpoint: 0.0.0.0:4317`, the collector process is fine. The
runner's `UNAVAILABLE`/`DEADLINE_EXCEEDED` then means **Azure Container Apps ingress is not
exposing port 4317 at the internal FQDN**.

ACA gotcha: an HTTP/2 (gRPC) ingress is reachable at the FQDN over **80/443**, NOT on the
container's `targetPort`. Raw ports like 4317/4318 are reachable only if declared as
`exposedPort` inside `additionalPortMappings`. Read the truth:

```bash
az containerapp show -n otel-collector-dev -g az03-al-titan-sandbox-rg \
  --query "properties.configuration.ingress" -o json
```

Interpret:
- **`additionalPortMappings` exposes `4317` (exposedPort:4317)** → endpoint is correct; problem is
  `transport`/TLS. For h2c plaintext use `http://...:4317` + `OTEL_EXPORTER_OTLP_INSECURE=true`.
- **Only ingress is http2 on `targetPort:4317`, no exposed 4317** → the FQDN listens on 80/443,
  not 4317. Point the runner at the FQDN **without** the `:4317` suffix:

  ```bash
  RG=az03-al-titan-sandbox-rg
  RUNNER=ai-telemetry-runner-dev
  # internal http2 ingress → plaintext h2c on :80
  OTEL_EP=http://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io

  az containerapp update -n "$RUNNER" -g "$RG" \
    --set-env-vars \
      "OTEL_EXPORTER_OTLP_ENDPOINT=$OTEL_EP" \
      "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_EP" \
      "OTEL_EXPORTER_OTLP_PROTOCOL=grpc" \
      "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
      "OTEL_EXPORTER_OTLP_INSECURE=true" \
      "DEPLOY_STAMP=$(date +%s)" \
    --output none
  ```

After the fix, re-run STEP 3c (boot line should show successful export, no UNAVAILABLE) and
STEP 3 query `sum(otelcol_receiver_accepted_log_records_total)` should climb above 0.

> **RESOLVED for this env (2026-06-08):** ingress read showed `targetPort: 4317, transport: Http2,
> allowInsecure: false` with `additionalPortMappings` exposing only 4318/8888/13133. So `:4317` is
> NOT a raw exposed port (TLS-only on 443) and `:4318` HTTP IS. Fix = route logs over `:4318` HTTP
> (see corrected STEP 1). gRPC `:4317` would require `https://...:443` + `INSECURE=false`, which
> hits internal-cert-trust issues — avoid it; use `:4318`.

### STEP 3f — BOTH :4317 and :4318 time out → cross-environment reachability

If after STEP 1 (logs on `:4318`) the boot line shows the **HTTP** exporter also failing:

```
ERROR [opentelemetry.exporter.otlp.proto.http._log_exporter] Failed to export logs batch due to timeout
```

…and `:4317` (traces/metrics) also times out, then EVERY port to a healthy collector is
unreachable. That is **not** a port-exposure problem — it is **network reachability between the
two Container Apps**. Most common cause: the runner and collector are in **different Container
Apps Environments**, so the `.internal.` FQDN does not route (internal ingress only works within
the same environment). Decisive check:

```bash
RG=az03-al-titan-sandbox-rg

echo "runner env:"
az containerapp show -n ai-telemetry-runner-dev -g "$RG" --query "properties.environmentId" -o tsv

echo "collector env:"
az containerapp show -n otel-collector-dev -g "$RG" --query "properties.environmentId" -o tsv
```

Interpret:
- **`environmentId` values DIFFER** → root cause. Cross-environment internal ingress doesn't work.
  Fix: move the runner into the collector's environment, OR give the collector **external** ingress
  and point the runner at the public FQDN (still OTLP, just a routable host).
- **Values are IDENTICAL** → intra-environment networking (DNS/policy). Probe directly from inside
  the runner (health port 13133 is an exposed raw port):

  Go to STEP 3g for the full probe.

> **RESULT for this env (2026-06-08):** both IDs are identical
> (`.../managedEnvironments/cae-telemetry-dev`). NOT cross-environment → intra-environment
> networking. Proceed to STEP 3g.

### STEP 3g — Intra-env probe: DNS + raw TCP connect from inside the runner

Same environment but every port times out → test name resolution and per-port TCP connect from
*inside* the runner container (the runner image has `python3`).

> Do NOT pass a multi-line `python3 -c` via `--command` — `az exec` mangles the newlines
> ("unterminated string literal"). Enter the shell first, then run a heredoc.

**1) Open a shell in the runner:**

```bash
az containerapp exec -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg
```

**2) Paste this heredoc at the container prompt (no quoting issues):**

```bash
python3 <<'EOF'
import socket
h="otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io"
try:
    print("DNS", socket.gethostbyname(h))
except Exception as e:
    print("DNS FAIL", e)
for p in (4317,4318,8888,13133,80,443):
    s=socket.socket(); s.settimeout(5)
    try:
        s.connect((h,p)); print("TCP OK", p)
    except Exception as e:
        print("TCP FAIL", p, e)
    finally:
        s.close()
EOF
```

Then `exit` (or Ctrl-D) to leave the container.

Interpret:
- **`DNS FAIL`** → internal name resolution broken; the FQDN/domain is wrong or env DNS is down.
- **DNS OK but ALL `TCP FAIL`** → connectivity blocked at the env (the `.internal.` host resolves
  to the env LB but nothing accepts) — likely the collector ingress isn't really publishing these
  ports to peers. Workaround: give the collector **external** ingress and point the runner there,
  or co-locate via the short name.
- **`TCP OK 4318` (or 80/443) but the HTTP export still times out** → TCP path is fine; the
  collector's OTLP HTTP receiver isn't answering — check collector logs (STEP 5).
- **`TCP OK 80`/`443` only** → the OTLP receivers are reachable only behind the http2 ingress;
  use OTLP **gRPC** through the ingress — apply STEP 3h.

> **RESULT for this env (2026-06-08):** `DNS ... -> 100.100.0.206` (resolves), but
> `TCP FAIL 4317/4318/8888/13133` and `TCP OK 80`, `TCP OK 443`. Only the ingress ports 80/443
> are reachable app-to-app — raw OTLP ports and `additionalPortMappings` ports are NOT routable
> between apps in this (Consumption) environment. Only viable path = **OTLP gRPC through the
> ingress** (forwards to container `targetPort: 4317`). HTTP OTLP on `:4318` can never work here.

### STEP 3h — Fix: route OTLP gRPC through the ingress (port 80 h2c)

Only 80/443 are reachable and the ingress forwards to the gRPC receiver (`targetPort 4317`), so
send OTLP **gRPC** through the ingress. Port 80 plaintext h2c avoids internal-TLS-cert trust
issues — but `allowInsecure` must be on.

**1) Enable plaintext h2c on the collector ingress:**

```bash
az containerapp ingress update -n otel-collector-dev -g az03-al-titan-sandbox-rg \
  --allow-insecure
```

**2) Point the runner at the collector over port 80, gRPC, insecure:**

```bash
RG=az03-al-titan-sandbox-rg
RUNNER=ai-telemetry-runner-dev
OTEL_80=http://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io:80

az containerapp update -n "$RUNNER" -g "$RG" \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_ENDPOINT=$OTEL_80" \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_80" \
    "OTEL_EXPORTER_OTLP_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_INSECURE=true" \
    "DEPLOY_STAMP=$(date +%s)" \
  --output none
```

Re-run STEP 3c. Success = boot line `OTLP log exporter → http://...:80 (grpc insecure=true)` and
NO more `UNAVAILABLE`/`DEADLINE_EXCEEDED`. Then STEP 3 `accepted_log_records` climbs > 0.

**Alternative (no collector change): gRPC over TLS on 443** — may fail on internal-cert trust:

```bash
OTEL_443=https://otel-collector-dev.internal.bravesand-913bfe11.eastus.azurecontainerapps.io:443
az containerapp update -n ai-telemetry-runner-dev -g az03-al-titan-sandbox-rg \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_ENDPOINT=$OTEL_443" \
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=$OTEL_443" \
    "OTEL_EXPORTER_OTLP_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc" \
    "OTEL_EXPORTER_OTLP_INSECURE=false" \
    "DEPLOY_STAMP=$(date +%s)" \
  --output none
```

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

---

# METRICS — Prometheus panels show "No data"

Same shape as the logs problem: runner app metrics (`ai_gateway_*`, `kube_*`)
reach Prometheus via **OTLP from the runner → collector → prometheusremotewrite**
(the collector has NO scrape job for the runner's :8000). If `otelcol_*` series
render in Grafana but `ai_gateway_*` don't, the runner's OTLP *metrics* aren't
landing. Code/config are correct: metric names, unit `ms`→`_milliseconds`, and
labels (`department`, `region`, `model_name`, `model_provider`) all match the
dashboards — so this is a live pipeline-state issue.

## STEP M1 — localize in Grafana → Explore → Prometheus (range = Last 1h)

```promql
group by (__name__) ({__name__=~"ai_gateway.*"})                                  # runner app metrics present?
group by (__name__) ({__name__=~"kube_.*"})                                       # pod-sim metrics present?
sum(rate(otelcol_receiver_accepted_metric_points_total[5m]))                      # collector receiving?
sum(rate(otelcol_exporter_sent_metric_points_total{exporter="prometheusremotewrite"}[5m]))  # forwarding?
sum(rate(otelcol_exporter_send_failed_metric_points_total[5m]))                   # remote-write failures?
```

Read it:
- M1/M2 return series → metrics ARE in Prom; dashboard "No data" = time range or
  template vars. Set range Last 1h; set `$department/$region/$model` to **All**.
- M1/M2 empty, receive+forward climbing → name mismatch; capture the series names.
- receive flat (only self) → runner not exporting → restart runner (STEP M3).
- forward ~0 / failed climbing → remote-write broken →
  `./scripts/fix-prometheus-remote-write-azure.sh`.

## STEP M2 — confirm runner is exporting (Azure terminal)

```bash
RG=az03-al-titan-sandbox-rg
az containerapp logs show -n ai-telemetry-runner-dev -g "$RG" --tail 300 \
  | grep -iE "OTLP metric exporter|Prometheus reader|OTel metrics ready|exporter init failed"
az containerapp show -n ai-telemetry-runner-dev -g "$RG" \
  --query "properties.template.containers[0].env[?starts_with(name,'OTEL_')]" -o table
```

Expect a boot line: `OTLP metric exporter (gRPC) → http://otel-collector-dev...:80`.

## STEP M3 — restart runner + re-verify

```bash
RG=az03-al-titan-sandbox-rg
REV=$(az containerapp show -n ai-telemetry-runner-dev -g "$RG" --query properties.latestRevisionName -o tsv)
az containerapp revision restart -n ai-telemetry-runner-dev -g "$RG" --revision "$REV"
```

---

# LOKI PANELS — "No data" + JSONParserErr on hover

Symptom: hovering a Loki panel shows
`pipeline error: 'JSONParserErr' … "Value looks like object, but can't find
closing '}' symbol"` and the panel renders **No data**.

Root cause: the panel LogQL used `{service_name=~".+"} | json | …`, but with
Loki **native OTLP ingestion** every log attribute is already **structured
metadata** (visible as labels: `event_type`, `model_name`, `latency_ms`,
`user_id`, …) and the log *body* is just the message string ("telemetry_event"),
NOT JSON. The `| json` stage tries to parse the body, throws JSONParserErr, and
poisons every `count_over_time` / `unwrap` aggregation → No data.

Fix (already in repo): `dashboards/generate_dashboards.py` `_LOKI_STREAM` dropped
the `| json` stage — filter structured metadata directly
(`{service_name=~".+"} | event_type="telemetry_event" | department=~"$department"`).
Regenerate + redeploy Grafana:

```bash
python3 dashboards/generate_dashboards.py      # rewrites the 9 JSON files
# IMPORTANT: dashboards are BAKED into the Grafana image (Dockerfile.grafana).
# fix-grafana-acr.sh only fixes ACR auth + pulls the EXISTING image — it does
# NOT rebuild dashboards. To pick up changed JSON you must rebuild the image:
FORCE_IMAGE_BUILD=true ./scripts/bootstrap-azure.sh --grafana-only
```

Verify in Grafana → Explore (Loki) that this no longer errors:

```logql
sum(count_over_time({service_name=~".+"} | event_type="telemetry_event" [5m]))
```
