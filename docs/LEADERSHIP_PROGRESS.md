# AI Gateway Telemetry — Leadership Progress Updates

**Project:** End-to-end observability for enterprise AI gateway traffic (metrics, logs, traces, cost, SLA)  
**Audience:** Leadership / program stakeholders  
**Owner:** AI Telemetry team  
**Last updated:** 2026-05-27

Use this document for recurring status updates. Each section below is one update — add a new **Update #N** block at the top as work progresses.

---

## Update #1 — Synthetic data pipeline & first 3 dashboards

**Date:** 2026-05-27  
**Status:** In progress — foundation complete  
**Summary:** Built a realistic synthetic AI gateway traffic generator and wired it into an observability stack. Three Grafana dashboards are live (Executive, Traffic, Latency) so we can demo and validate the platform before real production LLM traffic is connected.

**100-word executive update (copy/paste):**

We completed the foundation for AI gateway observability. A synthetic data generator now simulates realistic enterprise LLM traffic across six models, seven tenants, and four regions—covering cost, latency, SLA breaches, and errors—so we can validate the platform before production traffic is connected.

Three Grafana dashboards are live: **Executive Overview** (availability, error budget, cost, SLO burn rate), **Traffic Analytics** (usage by tenant/model and error breakdown), and **Latency & Performance** (p50/p95/p99 and phase-level timing). Next: four remaining dashboards and company Azure deployment.

### What we delivered

| Area | Deliverable | Business value |
|------|-------------|----------------|
| Synthetic data | Enterprise-grade LLM event generator | Demo, load-test, and validate dashboards without waiting on production gateway integration |
| Telemetry pipeline | OpenTelemetry metrics → Prometheus; structured logs → Loki / Log Analytics | Single standard for monitoring AI usage across tenants and models |
| Dashboards (3 of 7) | Executive Overview, Traffic & Request Analytics, Latency & Performance | Leadership visibility into health, usage, and performance |

---

### 1. Synthetic data generator

**Module:** `generator/synthetic_generator.py`  
**Runs as:** Azure Container App (`ai-telemetry-runner`), emitting events every 5 seconds

The generator produces events that mirror real AI gateway traffic — same shape we will use when production LLM calls are connected.

#### Scope of synthetic traffic

| Dimension | Coverage |
|-----------|----------|
| **LLM models** | 6 models — Claude Haiku/Sonnet/Opus, GPT-4o, GPT-4o-mini, Gemini 1.5 Flash |
| **Enterprise tenants** | 7 client profiles (e.g. healthcare, legal, financial services, e-commerce) with SLA tiers and daily budgets |
| **Regions** | 4 regions with realistic diurnal traffic patterns |
| **Operations** | Chat, code generation, summarisation, contract review, clinical analysis, and more — per tenant profile |
| **Sessions** | Multi-turn conversations with growing context (session ID, turn number) |

#### Data captured per request (50+ fields)

Each synthetic request includes:

- **Identity & tenancy** — request ID, session ID, tenant/client, user, data classification (PHI, PII, confidential, internal)
- **Routing** — model name, provider, routing reason (cost-optimised, latency-optimised, fallback, user-pinned)
- **Performance** — end-to-end latency, SLA target, SLA breach flag, latency phases (queue wait, model inference, time-to-first-token, stream response)
- **Cost & tokens** — prompt/completion/cache-read tokens, cost in USD, daily spend vs budget, budget-exhausted flag
- **Outcome** — success/error, HTTP status, error type/category (rate limit, timeout, model unavailable, etc.), retries
- **Streaming** — streaming flag, tokens per second, first-token latency

#### Realistic scenarios (for demo & alert testing)

- Rate-limit storms, model degradation, budget exhaustion, error cascades  
- PII snippets in prompts (for safety/compliance dashboard testing)  
- Model routing and SLA breach detection per tenant  

#### Downstream signals (from each batch)

| Signal | Destination | Purpose |
|--------|-------------|---------|
| Metrics | Prometheus (via OpenTelemetry) | Dashboards & SLO alerts |
| Traces | Grafana Tempo | Request-level debugging, exemplar links |
| Logs | Loki / Azure Log Analytics | Per-request detail, routing, PII audit |
| Events | Azure Event Hubs (Kafka) | Durable START/END event stream for future analytics |

---

### 2. Dashboard 1 — Executive Overview

**Audience:** Leadership, on-call leads  
**Question answered:** *Are we healthy? Are we within SLO and budget?*

| Panel | Key metrics | What leadership sees |
|-------|-------------|----------------------|
| Availability (6h) | `ai_gateway:sli:availability:6h` | % of successful requests over the last 6 hours |
| Error budget remaining | `ai_gateway:slo:error_budget_remaining` | How much of the monthly 99.5% SLO budget is left |
| Requests / min | `ai_gateway_request_count_total` | Current traffic volume |
| Error rate | exceptions ÷ total requests | % of requests failing now |
| p99 latency (5m) | `ai_gateway:sli:latency_p99_ms:5m` | Slowest 1% of requests — user experience risk |
| Total cost today | `ai_gateway_request_cost_USD_total` | Daily LLM spend (USD) |
| Request rate by model | request count by `model_name` | Which models are busiest |
| Error rate over time | error rate trend | Is reliability improving or degrading? |
| SLO burn rate | availability burn formula | How fast we are consuming error budget (alert if burning too fast) |
| Requests & cost by tenant | by `tenant_id` | Which clients drive usage and spend |

**SLO target:** 99.5% availability over 30 days (0.5% error budget).

---

### 3. Dashboard 2 — Traffic & Request Analytics

**Audience:** Product, operations, client success  
**Question answered:** *Who is using what, and where are failures coming from?*

| Panel | Key metrics / sources | What leadership sees |
|-------|----------------------|----------------------|
| Requests / min by model | `ai_gateway_request_count_total` by `model_name` | Model popularity and load |
| Requests / min by tenant | by `tenant_id` | Client traffic share |
| Requests / min by operation | by `operation_name` | Use-case mix (chat vs code vs summarisation) |
| Model distribution | hourly request share by model | Model mix over time |
| Model provider distribution | by `model_provider` | Anthropic vs OpenAI vs Google split |
| Error rate by type | `ai_gateway_exception_count_total` by `error_type` | rate_limit vs timeout vs auth failures |
| Errors by HTTP status | by `http_status` | 429 vs 504 vs 500 breakdown |
| Error category mix | by `error_category` | throttling vs availability vs validation |
| SLA breach rate by tenant | error rate filtered by tenant | Which clients hit SLA limits |
| Routing reason | Loki logs — `routing_reason` | Why a model was chosen (cost, latency, fallback) |
| Live telemetry events | Loki log stream | Raw event feed for investigations |

---

### 4. Dashboard 3 — Latency & Performance

**Audience:** SRE, platform engineering  
**Question answered:** *Why is it slow, and where is time spent?*

| Panel | Key metrics / sources | What leadership sees |
|-------|----------------------|----------------------|
| p50 / p95 / p99 latency | `ai_gateway_request_duration_milliseconds` (histogram) | Full latency distribution |
| Current p99 / p95 / p50 | percentile stats | At-a-glance latency |
| Average latency | duration sum ÷ count | Simple mean response time |
| Latency heatmap | duration buckets over time | Visual pattern of slow periods |
| p95 by model | percentile by `model_name` | Which models are slowest |
| p95 by SLA tier | percentile by environment/tier | Premium vs standard client experience |
| Latency phase breakdown | Loki — `queue_wait_ms`, `model_inference_ms`, `stream_response_ms` | Where time is spent (queue vs model vs streaming) |
| Tokens / second | Loki — `tokens_per_second` | Streaming throughput |
| First-token latency | Loki — `first_token_ms` | Time-to-first-token for streaming responses |
| p99 with trace exemplars | duration + Tempo link | Click a slow point → open full request trace |

**Alert:** p99 latency > 5 seconds for 5 minutes triggers `AIGatewayHighLatencyP99`.

---

### Core metrics powering all three dashboards

These five metrics are recorded on **every** LLM request:

| Metric | Measures | Used in dashboards |
|--------|----------|-------------------|
| `ai_gateway_request_count_total` | Request volume | 01, 02 |
| `ai_gateway_request_duration_milliseconds` | End-to-end latency | 01, 03 |
| `ai_gateway_request_token_total` | Tokens (prompt / completion / cache read) | (primarily dashboard 04 — upcoming) |
| `ai_gateway_request_cost_USD_total` | Spend in USD | 01 |
| `ai_gateway_exception_count_total` | Failed requests only | 01, 02 |

**Dimensions available on every metric:** tenant, model, provider, operation, status, environment, region.

---

### What's next (planned for Update #2)

| Item | Description |
|------|-------------|
| Dashboard 4 | Token & Cost Analytics — FinOps chargeback, budget utilisation |
| Dashboard 5 | Model Quality & Evaluation — faithfulness, relevance, groundedness scores |
| Dashboard 6 | Safety & PII — detection rates, audit coverage, data classification |
| Dashboard 7 | Infra & Runner Health — pipeline health, Kafka queue, pod metrics |
| Production cutover | Deploy to company Azure; swap synthetic → real gateway traffic |

---

## Update template (copy for future updates)

```markdown
## Update #N — [Title]

**Date:** YYYY-MM-DD  
**Status:** [On track / At risk / Blocked]  
**Summary:** [2–3 sentences for leadership]

### Completed this period
- 

### In progress
- 

### Risks / blockers
- 

### Metrics / KPIs (if applicable)
- 

### Next period
- 
```

---

## Reference links (internal)

| Topic | Document |
|-------|----------|
| Architecture | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| Full dashboard metric catalogue (all 7 dashboards) | [DASHBOARD_METRICS.md](./DASHBOARD_METRICS.md) |
| Production deployment | [../PRODUCTION_GUIDE.md](../PRODUCTION_GUIDE.md) |
