# AI Gateway Telemetry

End-to-end observability pipeline: synthetic LLM traffic → Azure Event Hubs → OpenTelemetry → Grafana.

```
GitHub push → GitHub Actions
  ├── Build ai-telemetry-fn image  (Azure Functions)
  └── Build ai-telemetry-runner image → Azure Container Apps (always-on)
                    │
                    ├── Event Hubs (Kafka)  — START/END events every 5 s
                    ├── :8000/metrics       — Prometheus scrape endpoint
                    └── stdout JSON logs    → Log Analytics → Grafana
```

---

## Quick start — run locally (no Azure needed)

```bash
cp .env.example .env          # fill in values, or leave blank for mock mode
pip install -r generator/requirements.txt
python3 -m generator.runner
```

Without `EVENTHUB_CONNECTION_STRING` the runner runs in **mock mode** — events are logged locally and no Azure connectivity is required.

---

## Deploy to Azure

### Prerequisites

- Azure CLI logged in: `az login`
- Docker Desktop running
- GitHub repo with Actions enabled

### Step 1 — Provision Azure infrastructure (run once)

```bash
chmod +x infra/bootstrap.sh

./infra/bootstrap.sh \
  --resource-group  rg-ai-telemetry-prod \
  --location        eastus \
  --acr-name        acrtelemetryprod \
  --cae-name        cae-telemetry-prod \
  --app-name        ai-telemetry-runner \
  --eventhub-ns     your-namespace.servicebus.windows.net \
  --eventhub-conn   "Endpoint=sb://your-namespace..."
```

The script creates the resource group, ACR, Container Apps Environment, and a Service Principal, then **prints the exact GitHub secrets to copy-paste**.

### Step 2 — Set GitHub secrets

Go to **GitHub repo → Settings → Secrets and variables → Actions** and add:

| Secret | Description | Example |
|---|---|---|
| `AZURE_CREDENTIALS` | Service Principal JSON (`az ad sp create-for-rbac --sdk-auth`) | `{"clientId":"...","clientSecret":"..."}` |
| `AZURE_RESOURCE_GROUP` | Resource group name | `rg-ai-telemetry-prod` |
| `ACR_LOGIN_SERVER` | Full ACR login server | `acrtelemetryprod.azurecr.io` |
| `ACR_PASSWORD` | ACR admin password | `az acr credential show --name <acr>` |
| `AZURE_CONTAINER_APP_NAME` | Container App name | `ai-telemetry-runner` |
| `AZURE_CAE_NAME` | Container Apps Environment name | `cae-telemetry-prod` |

Optional (jobs 3 & 4 skip gracefully if not set):

| Secret | Description |
|---|---|
| `PROM_REMOTE_WRITE_URL` | Azure Managed Prometheus ingestion URL |
| `AZURE_GRAFANA_NAME` | Azure Managed Grafana resource name |

### Step 3 — Push to deploy

```bash
git push origin main
```

GitHub Actions builds both images, pushes to ACR, and deploys the Container App. Subsequent pushes are rolling updates — zero downtime.

---

## Verify the deployment

```bash
# 1. Container App is running
az containerapp show \
  --name ai-telemetry-runner \
  --resource-group <your-rg> \
  --query "{status:properties.runningStatus, fqdn:properties.configuration.ingress.fqdn}"

# 2. Metrics endpoint is live
curl -s https://<fqdn>/metrics | grep ai_gateway

# 3. Logs in Log Analytics (after ~3 min)
# Azure Portal → Log Analytics workspace → Logs:
# ContainerAppConsoleLogs_CL
# | where ContainerAppName_s == "ai-telemetry-runner"
# | take 10
```

---

## Grafana setup

### Local Grafana (dev)

```bash
brew install grafana && brew services start grafana
open http://localhost:3000          # admin / admin
```

1. **Add data source** → Prometheus → paste your Azure Monitor Prometheus query endpoint URL.
2. **Dashboards → Import** → upload `dashboards/grafana_dashboard.json`.

### Azure Monitor datasource (for Log Analytics logs)

```bash
curl -s -X POST http://localhost:3000/api/datasources \
  -u admin:admin -H "Content-Type: application/json" -d '{
    "name": "Azure Monitor",
    "type": "grafana-azure-monitor-datasource",
    "access": "proxy",
    "jsonData": {
      "cloudName": "azuremonitor",
      "tenantId": "<AZURE_TENANT_ID>",
      "clientId": "<AZURE_CLIENT_ID>",
      "subscriptionId": "<AZURE_SUBSCRIPTION_ID>",
      "azureAuthType": "clientsecret"
    },
    "secureJsonData": { "clientSecret": "<AZURE_CLIENT_SECRET>" }
  }'
```

Set the **Default Log Analytics workspace** to the workspace auto-created with the Container Apps Environment.

---

## Validate synthetic data

```bash
python3 validation/check_data.py
```

Generates 1 000 events and checks model distribution, field completeness, error rate, and cost accuracy.

## Trigger an error-window spike (alert testing)

```bash
ERROR_WINDOW_PROB=1.0 python3 -m generator.runner
```

Error rate climbs to ~8 %. Grafana alert fires within 2 minutes.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `EVENTHUB_NAMESPACE` | Yes* | — | Event Hubs namespace FQDN |
| `EVENTHUB_CONNECTION_STRING` | Yes* | — | Full `Endpoint=sb://…` string |
| `EVENTHUB_NAME` | No | `ai-telemetry-events` | Event Hub name |
| `AZURE_CLIENT_ID` | No | — | SP client ID (local Prometheus auth) |
| `AZURE_CLIENT_SECRET` | No | — | SP client secret (local Prometheus auth) |
| `AZURE_TENANT_ID` | No | — | Azure tenant ID |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `http://localhost:4317` | OTLP gRPC endpoint |
| `OTEL_SERVICE_NAME` | No | `ai-telemetry-poc` | Service label in metrics/logs |
| `OTEL_EXPORT_INTERVAL_MS` | No | `30000` | OTel export interval |
| `ENVIRONMENT` | No | `poc` | Deployment label |
| `PROMETHEUS_PORT` | No | `8000` | Port to expose `/metrics` (0 = disabled) |
| `BATCH_INTERVAL_S` | No | `5` | Seconds between batches |
| `BASE_BATCH_SIZE` | No | `8` | Mean events per batch |
| `ERROR_WINDOW_PROB` | No | `0.03` | Per-minute probability of error spike |
| `SIMULATE_LATENCY` | No | `false` | Sleep `latency_ms/1000` s per event |

\* Without these the publisher runs in mock mode — events are logged locally only.

---

## Project structure

```
Telemetry/
├── generator/
│   ├── synthetic_generator.py   # event generation & cost calculation
│   ├── kafka_publisher.py       # Event Hubs publisher (START + END pairs)
│   ├── otel_metrics.py          # OTel instruments + Prometheus exporter
│   ├── azure_logger.py          # structured JSON logging → Log Analytics
│   ├── pod_metrics_simulator.py # simulated kube-state-metrics
│   ├── runner.py                # main batch loop
│   └── requirements.txt
├── function_app/
│   ├── function_app.py          # Azure Functions v2 timer trigger (30 s)
│   └── host.json
├── dashboards/
│   └── grafana_dashboard.json   # Grafana dashboard (schema v39)
├── infra/
│   └── bootstrap.sh             # one-command Azure resource provisioning
├── azure/
│   ├── windows-quickstart.ps1   # Windows developer setup
│   └── prometheus-entrypoint.sh # Prometheus Container App entrypoint
├── validation/
│   └── check_data.py            # data-quality validation suite
├── .github/workflows/
│   └── deploy.yml               # CI/CD: build → push ACR → deploy Container App
├── Dockerfile                   # Azure Functions container image
├── Dockerfile.runner            # telemetry runner container image
├── prometheus.yml               # Prometheus scrape + remote_write config
├── .env.example                 # environment variable template (copy to .env)
└── README.md
```

## Architecture

```
[runner.py — Azure Container App, min-replicas=1]
       │
       ▼ generate_event()
[synthetic_generator.py]  ── realistic LLM gateway traffic shape
       │
       ├─▶ kafka_publisher.py  ──▶  Azure Event Hubs (Kafka, port 9093)
       │
       ├─▶ otel_metrics.py     ──▶  :8000/metrics  ──▶  Prometheus
       │                                                      │
       │                                               remote_write
       │                                                      ▼
       │                                         Azure Managed Prometheus
       │                                                      │
       │                                                      ▼
       └─▶ azure_logger.py     ──▶  stdout JSON ──▶  Log Analytics
                                                              │
                                                              ▼
                                                     Grafana (dashboards)
```

To use real LLM traffic: replace the `generate_event()` call in `runner.py` with your gateway interceptor. All downstream components stay unchanged.
