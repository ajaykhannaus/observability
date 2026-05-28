# AI Gateway Telemetry — Production Guide

> **Status:** POC completed on personal Azure. This document captures everything learned so the team can deploy to company Azure production with zero trial-and-error.

---

## Table of Contents

1. [What We Built](#what-we-built)
2. [Architecture (Production)](#architecture-production)
3. [Every Mistake We Made (and the Fix)](#every-mistake-we-made-and-the-fix)
4. [Step-by-Step Production Setup (Windows)](#step-by-step-production-setup-windows)
5. [GitHub Secrets Required](#github-secrets-required)
6. [Azure Resources to Provision](#azure-resources-to-provision)
7. [Cost Breakdown](#cost-breakdown)
8. [Synthetic Data — What It Generates](#synthetic-data--what-it-generates)
9. [Grafana Dashboard Guide](#grafana-dashboard-guide)
10. [Monitoring & Alert Rules](#monitoring--alert-rules)
11. [How to Swap Synthetic → Real LLM Traffic](#how-to-swap-synthetic--real-llm-traffic)
12. [Security Checklist](#security-checklist)

---

## What We Built

An end-to-end AI gateway observability pipeline that tracks every LLM call across 6 models, 7 enterprise client teams, and 4 regions — with cost tracking, SLA monitoring, anomaly detection, and structured logs.

```
Synthetic Generator (Container App)
    │
    ├── Event Hubs (Kafka)     ← START/END events per request
    │       │
    │       └── (future) Stream Analytics / Databricks consumer
    │
    ├── Prometheus /metrics    ← scraped by Prometheus Container App
    │       │
    │       └── remote_write → Azure Managed Prometheus
    │                               │
    │                               └── Azure Managed Grafana (27 panels)
    │
    └── stdout JSON logs       ← Container Apps captures automatically
            │
            └── Log Analytics (ContainerAppConsoleLogs_CL)
                        │
                        └── Azure Managed Grafana (Azure Monitor datasource)
```

---

## Architecture (Production)

### Services used

| Service | Purpose | SKU for Prod |
|---|---|---|
| **Azure Container Apps** | Hosts the generator + Prometheus scraper | Consumption plan, min-replicas=1 |
| **Azure Container Registry** | Stores Docker images | Basic (can upgrade to Standard for geo-replication) |
| **Azure Managed Prometheus** | Time-series metrics store | Standard |
| **Azure Managed Grafana** | Dashboards + alerts | Standard |
| **Azure Event Hubs** | Kafka-compatible event stream | Standard (1 TU to start) |
| **Azure Log Analytics** | Structured log storage + KQL queries | Pay-as-you-go |
| **Azure Key Vault** | Secret storage for Event Hub connection string | Standard |

### What runs where

```
Your Windows Machine
  └── git push → GitHub Actions (Linux runners)
                      ├── Job 1: build ai-telemetry-fn image → ACR
                      ├── Job 2: build ai-telemetry-runner image → ACR → deploy Container App
                      ├── Job 3: build prometheus-scraper image → ACR → deploy Container App
                      └── Job 4: import dashboard → Azure Managed Grafana

Azure (always running)
  ├── ai-telemetry-runner Container App  (0.5 vCPU, 1Gi, :8000/metrics)
  ├── prometheus-scraper  Container App  (0.25 vCPU, 0.5Gi, scrapes runner + remote_write)
  ├── Azure Managed Prometheus           (receives remote_write, serves Grafana queries)
  └── Azure Managed Grafana              (27 panels, Azure AD login, native Prom integration)
```

---

## Every Mistake We Made (and the Fix)

These cost us hours during POC. **Read this before you start.**

### 1. OTel metric naming — unit suffix auto-appended
**Problem:** Dashboard queried `ai_gateway_request_cost_total` but the actual metric was `ai_gateway_request_cost_USD_total`.
**Root cause:** OpenTelemetry SDK automatically appends the unit to the metric name. `unit="USD"` → `_USD_`, `unit="ms"` → `_milliseconds_`.
**Fix:** Always check actual metric names in Prometheus UI (`/graph`) before writing dashboard queries. Query `{__name__=~"ai_gateway.*"}` to list all real names.

### 2. ARM64 vs AMD64 — Apple Silicon → Azure mismatch
**Problem:** Docker image built on MacBook (ARM64) crashed on Azure Container Apps (requires AMD64).
**Fix:** Always build with `docker buildx build --platform linux/amd64`. Add this flag to every `docker build` command and every GitHub Actions step.
> **Windows note:** Docker Desktop on Windows already defaults to AMD64 — but still specify `--platform linux/amd64` explicitly to be safe.

### 3. Azure provider not registered
**Problem:** `az containerapp create` failed — `Subscription not registered for Microsoft.App`.
**Fix:** Run these once per subscription before any other commands:
```powershell
az provider register -n Microsoft.App                 --wait
az provider register -n Microsoft.Monitor             --wait
az provider register -n Microsoft.Dashboard           --wait
az provider register -n Microsoft.OperationalInsights --wait
```

### 4. ACR admin credentials blocked by company policy
**Problem:** Enabling ACR admin account (long-lived credentials) is blocked in enterprise subscriptions.
**Fix:** Use Managed Identity for Container Apps (no credentials at all) or assign `AcrPull` role to a Service Principal. Never enable ACR admin in company Azure.

### 5. Create Container App AFTER pushing image
**Problem:** `az containerapp create` with `--image myacr.azurecr.io/app:latest` fails if the image doesn't exist yet.
**Fix:** Always push the image first, then create/update the Container App. In GitHub Actions, the build-and-push step must complete before the deploy step.

### 6. Editing dashboard JSON file does NOT update Grafana
**Problem:** Edited `dashboards/grafana_dashboard.json` locally, refreshed Grafana — still old dashboard.
**Fix:**
- **Local Grafana:** Must POST to `/api/dashboards/db` with `"overwrite": true`.
- **Azure Managed Grafana:** Use `az grafana dashboard import --overwrite true` or the GitHub Actions Job 4.
- File edits only take effect if Grafana is restarted from scratch (not practical).

### 7. Two OAuth scopes — write token ≠ read token
**Problem:** Used the Prometheus write token (`https://monitor.azure.com/` scope) to query Grafana — got HTTP 401.
**Fix:** Two separate tokens needed for the same Service Principal:
```powershell
# Write token (Prometheus remote_write → Azure Managed Prometheus ingest)
az account get-access-token --resource "https://monitor.azure.com/" --query accessToken -o tsv

# Read token (Grafana → Azure Managed Prometheus query endpoint)
az account get-access-token --resource "https://prometheus.monitor.azure.com" --query accessToken -o tsv
```
> **In production (Azure Managed Grafana):** This is handled automatically by Managed Identity — you never touch tokens.

### 8. Token refresh daemon silently freezing
**Problem:** Refresh daemon process showed as running (same PID) but hadn't refreshed the token in 24 hours. Prometheus sent 400K samples — all silently rejected with HTTP 401. Data gap in Grafana.
**Fix in POC:** Kill and restart daemon. Check token file timestamp, not just process status.
**Fix in production:** Use Azure Managed Grafana + Managed Identity — no token daemon needed at all. This entire class of problem disappears.

### 9. Prometheus Container App can't resolve internal FQDN
**Problem:** Prometheus scraper Container App couldn't resolve the runner's Container Apps internal DNS name.
**Fix:** Use the public external HTTPS FQDN for `SCRAPE_TARGET` (the runner has external ingress enabled). Internal DNS only works for apps in the same Container Apps Environment using the internal FQDN suffix.

### 10. GitHub Actions SP auth — wrong JSON format
**Problem:** `azure/login@v2` failed: "client-id and tenant-id not supplied".
**Fix:** `AZURE_CREDENTIALS` secret must be exactly this JSON structure:
```json
{
  "clientId": "...",
  "clientSecret": "...",
  "subscriptionId": "...",
  "tenantId": "..."
}
```
Generate it with: `az ad sp create-for-rbac --sdk-auth --role Contributor --scopes /subscriptions/<id>/resourceGroups/<rg>`

### 11. Grafana variable filter breaks "All" selection
**Problem:** Template variable `$client_name` caused "No data" when "All" was selected.
**Root cause:** Query used `{client_name='$client_name'}` (exact match) — doesn't work for "All".
**Fix:** Use `{client_name=~'$client_name'}` (regex match). Grafana sets `$client_name` to `.+` when "All" is selected — only works with `=~`.

### 12. Session state resetting every batch
**Problem:** Multi-turn session simulation wasn't working — every event was turn=1 (new session).
**Root cause:** `_active_sessions` was a local variable inside the function, reinitialised every call.
**Fix:** Module-level dict. Python module-level state persists for the lifetime of the process.

### 13. az grafana extension not installed
**Problem:** `az grafana` command not found in GitHub Actions or fresh Windows terminal.
**Fix:** Always run first: `az extension add --name amg --upgrade --yes`

### 14. Windows-specific: python3 not found
**Problem:** `python3` command not recognised on Windows even after Python installation.
**Fix:** On Windows, Python Launcher installs as `python` (not `python3`). Use `python` everywhere. Activate venv with `.venv\Scripts\Activate.ps1` not `source .venv/bin/activate`.

---

## Step-by-Step Production Setup (Windows)

### Prerequisites (one-time, run as Administrator)
```powershell
winget install Microsoft.AzureCLI         # Azure CLI
winget install Docker.DockerDesktop        # Docker (requires restart)
winget install Python.Python.3.11         # Python
winget install Git.Git                    # Git

# After restart:
docker buildx install                     # Enable buildx for cross-platform builds
az extension add --name containerapp --upgrade --yes
az extension add --name amg --upgrade --yes
```

### 1. Clone repo and set up Python
```powershell
git clone https://github.com/ajaykhannaus/observability.git
cd observability

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r generator\requirements.txt
pip install opentelemetry-exporter-prometheus
```

### 2. Login to Azure
```powershell
az login
az account set --subscription "YOUR_COMPANY_SUBSCRIPTION_ID"
```

### 3. Run full Azure provisioning (one-time)
```powershell
.\azure\setup-company.ps1 `
  -ResourceGroup "rg-ai-telemetry-dev" `
  -Location "eastus" `
  -AcrName "acrtelemetryprod"
```
This takes ~15 minutes and provisions everything. Copy the output values — you'll need them for GitHub secrets.

### 4. Set GitHub secrets
Go to: **GitHub repo → Settings → Secrets and variables → Actions**

| Secret | Value |
|---|---|
| `AZURE_CREDENTIALS` | SP JSON from provisioning output |
| `ACR_LOGIN_SERVER` | e.g. `acrtelemetryprod.azurecr.io` |
| `AZURE_RESOURCE_GROUP` | `rg-ai-telemetry-dev` |
| `AZURE_CONTAINER_APP_NAME` | `ai-telemetry-runner` |
| `AZURE_PROM_APP_NAME` | `prometheus-scraper` |
| `PROM_REMOTE_WRITE_URL` | DCR write URL from provisioning output |
| `AZURE_GRAFANA_NAME` | `grafana-ai-telemetry` |
| `EVENTHUB_CONNECTION_STRING` | From provisioning output |

### 5. First deploy — push to master
```powershell
git push origin master
```
Watch GitHub Actions: 4 jobs run, ~5 minutes total. After all green:
- Open Grafana URL from provisioning output
- Login with your company Azure AD account
- All 27 panels should show data within 10 minutes

### 6. Verify pipeline health
```powershell
# Check Container App is running
az containerapp show --name ai-telemetry-runner --resource-group rg-ai-telemetry-dev `
  --query "properties.runningStatus" -o tsv

# Check metrics endpoint is live
curl https://<runner-fqdn>/metrics | Select-String "ai_gateway"

# Stream live logs
az containerapp logs show --name ai-telemetry-runner --resource-group rg-ai-telemetry-dev --follow
```

---

## GitHub Secrets Required

| Secret | Description | How to get it |
|---|---|---|
| `AZURE_CREDENTIALS` | Service Principal JSON for GitHub Actions Azure login | `az ad sp create-for-rbac --sdk-auth --role Contributor --scopes /subscriptions/<id>/resourceGroups/<rg>` |
| `ACR_LOGIN_SERVER` | ACR hostname | `az acr show --name <acr> --query loginServer -o tsv` |
| `AZURE_RESOURCE_GROUP` | Resource group name | Whatever you set in setup-company.ps1 |
| `AZURE_CONTAINER_APP_NAME` | Runner app name | `ai-telemetry-runner` |
| `AZURE_PROM_APP_NAME` | Prometheus scraper app name | `prometheus-scraper` |
| `PROM_REMOTE_WRITE_URL` | Azure Managed Prometheus DCR ingest URL | Output of setup-company.ps1 |
| `AZURE_GRAFANA_NAME` | Managed Grafana instance name | `grafana-ai-telemetry` |
| `EVENTHUB_CONNECTION_STRING` | Event Hub connection string | `az eventhubs namespace authorization-rule keys list ... --query primaryConnectionString` |

---

## Azure Resources to Provision

Run `.\azure\setup-company.ps1` — it creates all of these automatically.

| Resource | Type | Purpose |
|---|---|---|
| `rg-ai-telemetry-dev` | Resource Group | Contains everything |
| `acrtelemetryprod` | Container Registry (Basic) | Stores 3 Docker images |
| `telemetry-prometheus-ws` | Azure Monitor Account | Managed Prometheus workspace |
| `grafana-ai-telemetry` | Azure Managed Grafana (Standard) | Dashboards + alerts, Azure AD auth |
| `cae-telemetry` | Container Apps Environment | Hosts both Container Apps |
| `ai-telemetry-runner` | Container App | Runs the synthetic generator |
| `prometheus-scraper` | Container App | Scrapes runner + remote_write |
| `evhns-telemetry` | Event Hub Namespace (Standard) | Kafka-compatible event stream |
| `ai-telemetry-events` | Event Hub | The actual event topic |

**Not needed in production (vs POC):**
- ❌ Redis Cache — was provisioned in POC but never used by the pipeline
- ❌ Key Vault — use Container App secret refs directly instead
- ❌ Second Log Analytics workspace — one is auto-created by Container Apps Environment

---

## Cost Breakdown

| Resource | SKU | Est. $/month |
|---|---|---|
| Container App: runner | 0.5 vCPU, 1Gi, always-on | ~$6 |
| Container App: prometheus-scraper | 0.25 vCPU, 0.5Gi, always-on | ~$3 |
| Container Registry | Basic | ~$5 |
| Event Hubs | Standard, 1 TU | ~$10 |
| Azure Managed Prometheus | ~40 metric series | ~$0.10 |
| Azure Managed Grafana | Standard tier | ~$15 |
| Log Analytics | ~2GB/month (under free tier) | ~$0 |
| **Total** | | **~$39/month** |

**POC cost was $23/month** (no Managed Grafana — local Grafana was free). Production adds ~$15/month for Azure Managed Grafana.

**Cost levers:**
- Scale `max-replicas=1` to `max-replicas=3` if you increase `BASE_BATCH_SIZE` — Container Apps scales to zero when idle
- Event Hubs Standard 1 TU handles ~1M events/day — upgrade to 2 TU only if needed
- Log Analytics: set retention to 30 days (not 90) to avoid runaway storage cost

---

## Synthetic Data — What It Generates

### 6 Models

| Model | Provider | Cost Input | Cost Output | Best for |
|---|---|---|---|---|
| `claude-haiku-3-5` | Anthropic | $0.80/M | $4.00/M | High-volume, fast tasks |
| `claude-sonnet-4-5` | Anthropic | $3.00/M | $15.00/M | Balanced quality/cost |
| `claude-opus-4-6` | Anthropic | $15.00/M | $75.00/M | Complex reasoning |
| `gpt-4o` | OpenAI | $5.00/M | $15.00/M | General purpose |
| `gpt-4o-mini` | OpenAI | $0.15/M | $0.60/M | Cost-sensitive tasks |
| `gemini-1.5-flash` | Google | $0.075/M | $0.30/M | Cheapest option |

### 7 Client Profiles

| Client | SLA Tier | Daily Budget | Preferred Model | Domain Operations |
|---|---|---|---|---|
| `healthcare-portal` | Premium (p95 < 2000ms) | $150 | claude-sonnet-4-5 | clinical_note_analysis, summarisation |
| `legal-firm` | Premium (p95 < 4000ms) | $200 | claude-opus-4-6 | contract_review, summarisation |
| `ecommerce-brand` | Standard (p95 < 1500ms) | $90 | gpt-4o-mini | product_description, chat |
| `financial-svc` | Premium (p95 < 1000ms) | $180 | claude-sonnet-4-5 | risk_assessment, report_generation |
| `dev-agency` | Standard (p95 < 3000ms) | $60 | gpt-4o | code_generation, code_review |
| `internal-tools` | Basic (p95 < 5000ms) | $30 | claude-haiku-3-5 | chat_completion, summarisation |
| `data-science` | Standard (p95 < 8000ms) | $40 | claude-opus-4-6 | data_analysis, code_generation |

### Event Schema (37 fields)

```python
{
  # Identity
  "request_id":          "uuid4",
  "session_id":          "uuid4 — stable across multi-turn conversation",
  "turn_number":         1,        # increments per session turn
  "user_id":             "u-04231",
  "user_email":          "u-04231@health.org",
  "client_name":         "healthcare-portal",
  "project_id":          "proj-heal-412",
  "auth_method":         "jwt_azure_ad",
  "data_classification": "phi",    # phi | pii | confidential | internal

  # Routing
  "model_name":          "claude-sonnet-4-5",
  "model_provider":      "anthropic",
  "capability_tier":     "balanced",  # fast | balanced | premium
  "routing_reason":      "latency_optimised",

  # Request
  "operation_name":      "clinical_note_analysis",
  "region":              "us-east-1",
  "availability_zone":   "us-east-1a",
  "timestamp_start":     "2025-06-02T09:15:32.441Z",
  "streaming":           False,

  # Performance
  "latency_ms":          1247.3,
  "sla_tier":            "premium",
  "sla_target_ms":       2000,
  "sla_breached":        False,

  # Tokens & cost
  "prompt_tokens":       682,
  "completion_tokens":   315,
  "cache_read_tokens":   142,
  "total_tokens":        1139,
  "cost_usd":            0.006822,
  "daily_spend_usd":     47.23,
  "budget_usd":          150.0,
  "budget_exhausted":    False,

  # Outcome
  "status":              "success",
  "http_status_code":    200,
  "stop_reason":         "stop",
  "error_type":          None,
  "error_category":      None,
  "is_retried":          False,
  "retry_count":         0,
}
```

### Anomaly Patterns

| Anomaly | Trigger Probability | Duration | Effect |
|---|---|---|---|
| Model degradation | 0.5%/batch | 2–5 min | 3× latency, 4× error rate on one model |
| Rate-limit storm | 0.3%/batch | 1–3 min | 50–85% 429 errors on one client |
| Cascade failure | 0.1%/batch | 30–90 s | All models: +20% errors, 1.5× latency |

---

## Grafana Dashboard Guide

Dashboard file: `dashboards/grafana_dashboard.json`

### Row 1 — Summary Stats (6 stat panels)
- Total Requests (24h), Avg Latency p50, Total Cost (24h), Error Rate, Total Tokens (24h), Cost/Request

### Row 2 — Traffic & Latency (4 panels)
- Request Volume by Model (timeseries), Latency Percentiles p50/p95/p99 (timeseries), Model Routing Mix (pie), Modal Routing Mix 24h (bar)

### Row 3 — Cost & Tokens (4 panels)
- Cost by Client (5 min rate, timeseries), Token Consumption by Type (timeseries), Exception Count by Model (timeseries), Request Rate Success vs Error (timeseries)

### Row 4 — Infrastructure / Pod Health (9 panels)
- Running Pods (stat), HPA Current/Desired Replicas (stat), HPA Min/Max (stat), Total Pod Restarts (stat)
- HPA Scaling Over Time (timeseries), Container Memory RSS (timeseries), Container CPU Usage (timeseries)
- Node Memory Available (timeseries), Pod Restart Events (timeseries)

### Row 5 — Structured Logs (3 panels via Azure Monitor datasource)
- Log Events/min (timeseries from ContainerAppConsoleLogs_CL)
- Recent ERROR Logs (table)
- Events by Model — log-derived (timeseries)

### Key KQL queries for Log Analytics

```kql
-- All telemetry events
ContainerAppConsoleLogs_CL
| extend e = parse_json(Log_s)
| where e.event_type == "telemetry_event"

-- Average latency by model
| summarize avg(todouble(e.latency_ms)) by tostring(e.model_name), bin(TimeGenerated, 5m)

-- SLA breaches by client
| where tobool(e.sla_breached) == true
| summarize count() by tostring(e.client_name), bin(TimeGenerated, 1h)

-- Top 5 clients by cost (last 24h)
| summarize total_cost = sum(todouble(e.cost_usd)) by tostring(e.client_name)
| top 5 by total_cost

-- Session length distribution
| summarize max_turn = max(toint(e.turn_number)) by tostring(e.session_id)
| summarize count() by max_turn
```

---

## Monitoring & Alert Rules

Three Grafana alert rules (in `rules.yml`, also importable to Managed Grafana):

| Alert | Condition | Evaluation | Severity |
|---|---|---|---|
| `ErrorRateHigh` | `rate(ai_gateway_exception_count_total[5m]) / rate(ai_gateway_request_count_total[5m]) > 0.05` | Every 1m, for 2m | Warning |
| `PodRestartingFrequently` | `increase(kube_pod_container_status_restarts_total[5m]) > 3` | Every 1m, for 1m | Critical |
| `BudgetNearExhaustion` | Log Analytics: `e.budget_exhausted == true` | Every 5m | Warning |

---

## How to Swap Synthetic → Real LLM Traffic

The synthetic generator is a drop-in replacement for a real AI gateway interceptor. **Only one change needed:**

In `generator/runner.py`, replace:
```python
event = generate_event(error_rate=error_rate)
```

With your real gateway interceptor:
```python
event = your_gateway.get_next_event()  # must return same 37-field dict
```

Everything downstream (Kafka publisher, OTel metrics, structured logs, Grafana) works unchanged.

### Minimum required fields from real traffic
```python
{
  "request_id":       str,    # unique per request
  "model_name":       str,    # must match MODEL_CONFIG keys or add new entry
  "model_provider":   str,
  "operation_name":   str,
  "status":           "success" | "error",
  "latency_ms":       float,
  "prompt_tokens":    int,
  "completion_tokens":int,
  "cost_usd":         float,
  "client_name":      str,
  # All other fields optional — defaults to None/0
}
```

---

## Security Checklist

Before company production deploy, verify all of these:

- [ ] `.env` file is in `.gitignore` — **never committed**
- [ ] `git log --all --full-diff -p -- .env` returns no output
- [ ] Service Principal scoped to resource group only (not subscription)
- [ ] SP roles: `Contributor` on RG, `AcrPull` on ACR — nothing else
- [ ] Container Apps use Managed Identity for ACR pull (no SP secret in app config)
- [ ] Event Hub connection string stored as Container App secret ref (not plain env var)
- [ ] Key Vault used for any additional secrets
- [ ] Log Analytics retention set to 30 days (cost + data minimisation)
- [ ] ACR admin account disabled (use SP or Managed Identity)
- [ ] Grafana access via Azure AD only — no local `admin/admin`
- [ ] GitHub Actions secrets set per-repo (not per-org unless intentional)
- [ ] `PROM_REMOTE_WRITE_URL` contains no embedded credentials (auth is via Managed Identity)
- [ ] No hardcoded subscription IDs, tenant IDs, or resource IDs in committed code
- [ ] `data_classification` field in events is correct for your real data (PHI requires HIPAA controls)

---

## File Structure

```
Telemetry/
├── generator/
│   ├── synthetic_generator.py   ← 6 models, 7 clients, sessions, anomalies, routing
│   ├── runner.py                ← batch loop, anomaly state machine, SLA tracking
│   ├── otel_metrics.py          ← 5 OTel instruments + Prometheus exporter
│   ├── azure_logger.py          ← 37-field JSON structured logs
│   ├── kafka_publisher.py       ← Event Hubs publisher (START + END events)
│   ├── pod_metrics_simulator.py ← kube-state-metrics simulation (HPA, pods, nodes)
│   └── requirements.txt
├── azure/
│   ├── setup-company.ps1        ← Windows PowerShell one-time provisioning
│   ├── windows-quickstart.ps1   ← Dev helper functions for Windows
│   └── prometheus-entrypoint.sh ← Env-var substitution for Prometheus Container App
├── dashboards/
│   └── grafana_dashboard.json   ← 27-panel dashboard (import to Managed Grafana)
├── .github/workflows/
│   └── deploy.yml               ← 4-job CI/CD pipeline
├── Dockerfile.runner            ← Generator Container App image
├── Dockerfile.prometheus        ← Prometheus scraper Container App image
├── prometheus.yml               ← Scrape + remote_write config (template placeholders)
├── rules.yml                    ← Prometheus alert rules
├── validation/check_data.py     ← Data quality test suite
└── PRODUCTION_GUIDE.md          ← This file
```
