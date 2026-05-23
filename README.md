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

## Deploy as Azure Container App (always-on runner) 🚀

The runner deploys as a long-lived container on Azure Container Apps, generating
synthetic telemetry continuously. GitHub Actions builds and deploys on every push
to `main`/`master`.

### One-time Azure setup (run manually once)

```bash
RG="rg-ai-telemetry-poc"
SP_ID="62599542-f963-470f-8676-78da96a86231"
SP_SECRET="<AZURE_CLIENT_SECRET from .env>"
EVENTHUB_CONN="<EVENTHUB_CONNECTION_STRING from .env>"

# Install Container Apps CLI extension
az extension add --name containerapp --upgrade --yes

# Create the Container Apps Environment (auto-creates a Log Analytics workspace)
az containerapp env create \
  --name cae-telemetry-poc \
  --resource-group "$RG" \
  --location eastus

# Create the Container App (min-replicas=1 keeps it always-on)
az containerapp create \
  --name ai-telemetry-runner \
  --resource-group "$RG" \
  --environment cae-telemetry-poc \
  --image acrtelemetrypoc.azurecr.io/ai-telemetry-runner:latest \
  --registry-server acrtelemetrypoc.azurecr.io \
  --registry-username "$SP_ID" \
  --registry-password "$SP_SECRET" \
  --ingress external --target-port 8000 \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.5 --memory 1Gi \
  --env-vars \
      OTEL_SERVICE_NAME=ai-telemetry-poc \
      ENVIRONMENT=poc \
      EVENTHUB_NAMESPACE=evhns-telemetry-poc.servicebus.windows.net \
      EVENTHUB_NAME=ai-telemetry-events \
      PROMETHEUS_PORT=8000 \
      BATCH_INTERVAL_S=5 \
      BASE_BATCH_SIZE=8 \
      EVENTHUB_CONNECTION_STRING=secretref:eventhub-conn-str \
  --secrets eventhub-conn-str="$EVENTHUB_CONN"

# Get the FQDN — update prometheus.yml scrape target with this value
az containerapp show \
  --name ai-telemetry-runner --resource-group "$RG" \
  --query properties.configuration.ingress.fqdn -o tsv
```

After updating `prometheus.yml`, the data flow is:
```
Container App :8000/metrics  →  local Prometheus  →  remote_write  →  Azure Managed Prometheus  →  Grafana
Container App stdout (JSON)  →  Log Analytics  →  Grafana (Azure Monitor datasource)
Container App events         →  Event Hubs (Kafka)
```

### GitHub Actions secrets required

| Secret | Value |
|---|---|
| `AZURE_CREDENTIALS` | SP credentials JSON (existing) |
| `AZURE_CONTAINER_APP_NAME` | `ai-telemetry-runner` |

Add them at **GitHub repo → Settings → Secrets and variables → Actions**.

Subsequent deploys happen automatically on push to `main` or `master`.

---

## Grafana — Azure Monitor datasource (logs)

```bash
curl -s -X POST http://localhost:3000/api/datasources \
  -u admin:admin -H "Content-Type: application/json" -d '{
    "name": "Azure Monitor",
    "type": "grafana-azure-monitor-datasource",
    "access": "proxy",
    "jsonData": {
      "cloudName": "azuremonitor",
      "tenantId": "8fc3515f-414b-4737-b9af-4be4339a2f2b",
      "clientId": "62599542-f963-470f-8676-78da96a86231",
      "subscriptionId": "40d762c9-7c01-4602-9166-5117453d0747",
      "azureAuthType": "clientsecret"
    },
    "secureJsonData": {
      "clientSecret": "<AZURE_CLIENT_SECRET>"
    }
  }'
```

Then open the datasource in Grafana UI and set the **Default Log Analytics workspace**
to the workspace auto-created with `cae-telemetry-poc`. Container App logs appear in
`ContainerAppConsoleLogs_CL` within ~3 minutes of the container starting.

---

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
