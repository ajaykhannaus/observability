# AI Gateway Telemetry POC

End-to-end observability pipeline: synthetic LLM traffic → Azure Event Hubs → OpenTelemetry → local Grafana.

## Run locally (3 commands)

```bash
pip install -r generator/requirements.txt
export EVENTHUB_CONNECTION_STRING="Endpoint=sb://evhns-telemetry-poc.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=YOUR_KEY"
python3 generator/runner.py
```

Without `EVENTHUB_CONNECTION_STRING` the runner operates in **mock mode** — events are logged locally and no Azure connectivity is required.

## Get the Event Hubs connection string

```bash
az eventhubs namespace authorization-rule keys list \
  --resource-group rg-ai-telemetry-poc \
  --namespace-name evhns-telemetry-poc \
  --name RootManageSharedAccessKey \
  --query primaryConnectionString -o tsv
```

## Validate synthetic data

```bash
python3 validation/check_data.py
```

Generates 1 000 events and checks model distribution, field completeness, error rate, and cost accuracy.

## Force an error-window spike (for alert testing)

```bash
ERROR_WINDOW_PROB=1.0 python3 generator/runner.py
```

Error rate climbs to ~8 %. Grafana alert fires within 2 minutes.

## Deploy as Azure Function (timer fires every 30 s)

```bash
az acr login --name acrtelemetrypoc
docker build -t acrtelemetrypoc.azurecr.io/ai-telemetry-fn:v1 .
docker push acrtelemetrypoc.azurecr.io/ai-telemetry-fn:v1
```

## Grafana dashboard

```bash
brew install grafana && brew services start grafana
open http://localhost:3000          # admin / admin
```

1. **Add data source** → Prometheus → paste your Azure Monitor Prometheus query endpoint URL.
2. **Dashboards → Import** → upload `dashboards/grafana_dashboard.json`.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `EVENTHUB_NAMESPACE` | Yes* | — | `evhns-telemetry-poc.servicebus.windows.net` |
| `EVENTHUB_CONNECTION_STRING` | Yes* | — | Full `Endpoint=sb://…` string |
| `EVENTHUB_NAME` | No | `ai-telemetry-events` | Event Hub name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `http://localhost:4317` | OTLP gRPC endpoint |
| `OTEL_SERVICE_NAME` | No | `ai-telemetry-poc` | Service label |
| `OTEL_EXPORT_INTERVAL_MS` | No | `30000` | OTel export interval |
| `ENVIRONMENT` | No | `poc` | Deployment label |
| `BATCH_INTERVAL_S` | No | `5` | Seconds between batches (local runner) |
| `BASE_BATCH_SIZE` | No | `8` | Mean events per batch |
| `ERROR_WINDOW_PROB` | No | `0.03` | Per-minute probability of error spike |
| `SIMULATE_LATENCY` | No | `false` | Sleep `latency_ms/1000` s per event |

\* Without these the publisher runs in mock mode.

## Project structure

```
Telemetry/
├── generator/
│   ├── synthetic_generator.py   # event generation & cost calculation
│   ├── kafka_publisher.py       # Event Hubs publisher (START + END pairs)
│   ├── otel_metrics.py          # 5 OTel instruments + setup_otel()
│   ├── runner.py                # batch loop & run_one_batch() export
│   └── requirements.txt
├── function_app/
│   ├── function_app.py          # Azure Functions v2 timer trigger
│   ├── host.json
│   └── local.settings.json
├── dashboards/
│   └── grafana_dashboard.json   # 13-panel Grafana dashboard (schema v39)
├── validation/
│   └── check_data.py            # data-quality validation suite
├── Dockerfile                   # Azure Functions container
├── .env.example
└── README.md
```

## Architecture

```
[Timer / runner.py]
       │
       ▼ generate_event()
[synthetic_generator.py]  ──► each event is structurally identical to
                               real gateway traffic
       │
       ▼ publish_start_event() + publish_end_event()
[kafka_publisher.py]  ──► Azure Event Hubs (Kafka protocol, port 9093)
       │
       ▼ record_metrics()
[otel_metrics.py]  ──► OTLP gRPC ──► Azure Monitor Prometheus
                                              │
                                              ▼
                                      [Grafana localhost:3000]
```

Swapping synthetic traffic for real LLM traffic: replace the `generate_event()` call in `runner.py` with your gateway interceptor. All downstream components (Kafka publisher, OTel metrics, Grafana) remain unchanged.
