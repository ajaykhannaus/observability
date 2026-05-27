# Architecture — AI Gateway Telemetry

## 1. System Overview

```mermaid
graph TD
    subgraph DEV["👨‍💻 Developer Machine"]
        CODE[Code changes]
    end

    subgraph CICD["🔄 CI/CD — GitHub Actions"]
        GH[GitHub\najaykhannaus/azure-telemetry-llm]
        VM_RUNNER["🖥️ Company Windows Server VM\nself-hosted runner\nJobs 1 & 2"]
        GH_RUNNER["☁️ GitHub-hosted ubuntu\nJobs 3 & 4 optional"]
    end

    subgraph ACR["🗄️ Azure Container Registry"]
        IMG_FN[ai-telemetry-fn]
        IMG_RUNNER[ai-telemetry-runner]
        IMG_PROM[prometheus-scraper]
    end

    subgraph ACA["☁️ Azure Container Apps"]
        RUNNER["🤖 ai-telemetry-runner\nmin-replicas=2 max=4\n:8000/metrics  :8080/healthz"]
        PROM_APP[prometheus-scraper]
    end

    subgraph SIGNALS["📡 Signals emitted per batch every 5 s"]
        SIG_TRACE[W3C Traces\nOTLP gRPC]
        SIG_METRIC[Metrics\nOTLP + Prometheus]
        SIG_LOG[Structured Logs\nOTLP + stdout JSON]
        SIG_EVENT[Events\nKafka START+END]
    end

    subgraph OTEL["⚙️ OTel Collector"]
        MEM[memory_limiter]
        RES[resource processor]
        SAMPLE[tail_sampling\n100% errors · 10% success]
        DROP[drop_high_cardinality]
        BATCH[batch]
    end

    subgraph BACKENDS["🗃️ Observability Backends"]
        TEMPO[Grafana Tempo\nTraces · 7 d]
        PROMETHEUS[Prometheus\nMetrics · 30 d]
        LOKI[Grafana Loki\nLogs · 30 d]
        LA[Azure Log Analytics\nStdout logs parallel sink]
        EH[Azure Event Hubs\nKafka · durable events]
    end

    subgraph GRAFANA["📊 Grafana"]
        DASH[7 Dashboards\nMetrics · Traces · Logs\nSLO · Errors · Costs · Pods]
        ALERTS[SLO Alerts\nFast burn P1\nSlow burn P2\nLatency P99 > 5 s]
    end

    CODE -->|git push| GH
    GH -->|trigger| VM_RUNNER
    GH -->|trigger| GH_RUNNER
    VM_RUNNER -->|docker build + push| ACR
    VM_RUNNER -->|az containerapp update| ACA
    GH_RUNNER -.->|optional| ACR

    ACR --> RUNNER

    RUNNER --> SIG_TRACE
    RUNNER --> SIG_METRIC
    RUNNER --> SIG_LOG
    RUNNER --> SIG_EVENT

    SIG_TRACE -->|:4317 gRPC| OTEL
    SIG_METRIC -->|:4317 gRPC| OTEL
    SIG_LOG -->|:4317 gRPC| OTEL
    SIG_EVENT --> EH

    SIG_LOG -->|stdout JSON| LA
    SIG_METRIC -->|scrape :8000| PROM_APP
    PROM_APP -->|remote_write| PROMETHEUS

    MEM --> RES --> SAMPLE --> BATCH
    RES --> DROP --> BATCH

    OTEL -->|traces| TEMPO
    OTEL -->|metrics remote_write| PROMETHEUS
    OTEL -->|logs push| LOKI

    TEMPO --> GRAFANA
    PROMETHEUS --> GRAFANA
    LOKI --> GRAFANA
    LA -.->|Azure Monitor datasource| GRAFANA
```

---

## 2. Signal Flow (Detailed)

```mermaid
sequenceDiagram
    participant R as runner.py<br/>(batch loop every 5 s)
    participant SG as synthetic_generator.py
    participant PII as pii_scanner.py
    participant PL as prompt_logger.py
    participant EV as evaluator.py
    participant T as otel_tracing.py
    participant M as otel_metrics.py
    participant K as kafka_publisher.py
    participant OC as OTel Collector<br/>:4317
    participant EH as Azure Event Hubs<br/>(Kafka)
    participant TEMPO as Grafana Tempo
    participant PROM as Prometheus
    participant LOKI as Grafana Loki

    R->>T: start_batch_span("ai.batch.run")
    loop for each event in batch
        R->>SG: generate_event(error_rate)
        SG-->>R: event dict (50+ fields)
        R->>PII: scan(prompt_text)
        PII-->>R: RedactionResult (pii_detected, scrubbed)
        R->>PL: log_prompt(event) → audit WORM log
        R->>T: start_span("ai.request")
        T-->>R: span context + traceparent header
        R->>K: publish_start_event(id, event, headers={traceparent})
        K->>EH: START record (Kafka, port 9093)
        R->>M: record_metrics(event)
        M->>OC: OTLP metrics gRPC
        R->>T: end_span(status, latency)
        T->>OC: OTLP trace gRPC
        R->>K: publish_end_event(id, event)
        K->>EH: END record (Kafka, port 9093)
        R->>EV: evaluate(event) async [1% sample]
        EV-->>R: quality_score (OpenAI-as-judge)
    end
    R->>OC: OTLP logs gRPC (batch summary)
    OC->>TEMPO: traces (tail-sampled)
    OC->>PROM: metrics (remote_write)
    OC->>LOKI: logs (push)
```

---

## 3. OTel Span Tree (per request)

```
ai.batch.run  [parent span — entire 5-s batch]
└── ai.request  [one per LLM call]
    ├── ai.publish.start   [Kafka START event]
    ├── ai.latency.ttft    [time-to-first-token phase]
    ├── ai.latency.gen     [generation phase]
    ├── ai.latency.total   [full end-to-end]
    └── ai.publish.end     [Kafka END event]

W3C traceparent propagated into every Kafka message header
→ downstream consumers can continue the same trace
```

---

## 4. OTel Collector Pipeline

```mermaid
graph LR
    subgraph RECV["Receivers"]
        OTLP_GRPC["OTLP gRPC :4317"]
        OTLP_HTTP["OTLP HTTP :4318"]
        SELF["self-scrape :8888"]
    end

    subgraph PROC["Processors  traces · metrics · logs"]
        ML["memory_limiter\n400 MiB hard cap"]
        RP["resource\nstamps cloud.provider=azure\ndeployment.environment\ncloud.region"]
        TS["tail_sampling\n100% errors + budget_exhausted\n10% success"]
        DH["drop_high_cardinality\nai.request.id\nai.session.id\nai.user.*"]
        BA["batch\n5 s · 1 000 per send"]
    end

    subgraph EXP["Exporters"]
        TEMPO["Grafana Tempo\notlp gRPC"]
        PROMRW["Prometheus\nremote_write"]
        LOKI["Grafana Loki\nHTTP push"]
        DBG["debug\nsampled logging"]
    end

    OTLP_GRPC --> ML
    OTLP_HTTP --> ML
    SELF --> ML

    ML --> RP
    RP -->|traces| TS --> BA -->|traces| TEMPO
    RP -->|metrics| DH --> BA -->|metrics| PROMRW
    RP -->|logs| BA -->|logs| LOKI
    BA -.->|all| DBG
```

---

## 5. Azure Infrastructure

```mermaid
graph TD
    subgraph RG["Resource Group: rg-ai-telemetry-dev"]

        subgraph CAE["Container Apps Environment: cae-telemetry-prod"]
            CA["ai-telemetry-runner\nSystem-assigned Managed Identity\nmin=2 / max=4 replicas\nCPU 0.5 · RAM 1 GiB"]
            PROM_CA["prometheus-scraper\n(optional)"]
        end

        ACR["Azure Container Registry\nacrcompanyprod.azurecr.io\nSKU: Basic"]
        LA_WS["Log Analytics Workspace\nauto-created with CAE\nstdout → ContainerAppConsoleLogs_CL"]
        EH_NS["Event Hubs Namespace\nKafka endpoint :9093\nai-telemetry-events hub"]
        AMP["Azure Managed Prometheus\nremote_write target"]
        AMG["Azure Managed Grafana\nproduction dashboard host"]
    end

    CA -->|AcrPull via Managed Identity| ACR
    CA -->|stdout JSON| LA_WS
    CA -->|Kafka SASL/SSL| EH_NS
    PROM_CA -->|scrape :8000/metrics| CA
    PROM_CA -->|remote_write| AMP
    AMP -->|data source| AMG
    LA_WS -->|Azure Monitor data source| AMG
```

---

## 6. CI/CD Pipeline

```mermaid
graph TD
    PUSH[git push master/main]

    subgraph GHA["GitHub Actions"]
        J1["Job 1: build-fn\n🖥️ self-hosted Windows VM\naz acr login\ndocker build Dockerfile\ndocker push → ACR"]
        J2["Job 2: build-and-deploy-runner\n🖥️ self-hosted Windows VM\ndocker buildx --platform linux/amd64\nRender containerapp.template.yaml\nSync EventHub secrets\naz containerapp update/create"]
        J3["Job 3: build-and-deploy-prometheus\n☁️ ubuntu-latest\ncontinue-on-error: true\nOptional Prom scraper"]
        J4["Job 4: deploy-grafana-dashboard\n☁️ ubuntu-latest\ncontinue-on-error: true\naz grafana dashboard import"]
    end

    PUSH --> J1
    PUSH --> J2
    J2 --> J3
    J3 --> J4

    J1 -->|ai-telemetry-fn:sha| ACR2[ACR]
    J2 -->|ai-telemetry-runner:sha| ACR2
    J2 -->|az containerapp update| ACA2[Azure Container Apps]

    style J3 fill:#f5f5f5,stroke:#999
    style J4 fill:#f5f5f5,stroke:#999
```

---

## 7. Security & PII Layers

```mermaid
graph LR
    RAW[Raw event\n prompt + completion text]

    subgraph L1["Layer 1 — In-process (pii_scanner.py)"]
        REGEX["Regex patterns\nEmail · Phone · SSN\nCredit card · IP · NHS"]
        PRESIDIO["Microsoft Presidio\nif installed — higher recall\nEnglish NER model"]
    end

    subgraph L2["Layer 2 — OTel Collector (otel-collector-pii-config.yaml)"]
        TRANSFORM["transform processor\ndelete ai.user.email\ndelete ai.user.id\ndelete prompt_text attr"]
    end

    subgraph AUDIT["Audit trail (prompt_logger.py)"]
        WORM["Azure Blob WORM\nimmutable audit log\nretention-locked"]
        LOCAL["Local JSONL fallback\n/tmp/audit_log.jsonl\ndev / staging"]
    end

    RAW --> REGEX --> PRESIDIO
    PRESIDIO -->|scrubbed event| L2
    L2 -->|clean signals| OC[OTel Collector]
    PRESIDIO --> AUDIT
```

---

## 8. Generator Modules

| Module | Role |
|---|---|
| `synthetic_generator.py` | Generates 50-field LLM event dicts (model, tokens, cost, latency, errors) |
| `kafka_publisher.py` | Publishes START+END pairs to Event Hubs over Kafka protocol with W3C traceparent |
| `otel_tracing.py` | W3C span tree: `ai.batch.run` → `ai.request` → 5 child spans |
| `otel_metrics.py` | 6 OTel instruments + Prometheus exporter on :8000/metrics |
| `otel_logging.py` | OTLP log export (structured JSON to Loki via Collector) |
| `azure_logger.py` | Structured JSON to stdout → Log Analytics (parallel Azure sink) |
| `pii_scanner.py` | In-process PII redaction (regex + optional Presidio) before any emit |
| `prompt_logger.py` | Append-only audit log of every prompt (WORM Blob / local JSONL) |
| `evaluator.py` | OpenAI-as-judge quality scoring — async, 1% sample, token-budgeted |
| `pod_metrics_simulator.py` | Simulated kube-state-metrics (pod phase, resource pressure) |
| `health_server.py` | HTTP :8080/healthz and /readyz for Container App liveness probes |
| `runner.py` | Main batch loop — orchestrates all modules every 5 s |

---

## 9. Local Dev Stack (docker-compose.observability.yml)

```
docker compose -f docker-compose.observability.yml up -d

localhost:3000  →  Grafana        (admin/admin)
localhost:9090  →  Prometheus     (query UI)
localhost:3200  →  Grafana Tempo  (trace search)
localhost:3100  →  Grafana Loki   (log query)
localhost:4317  →  OTel Collector (OTLP gRPC receiver)
localhost:4318  →  OTel Collector (OTLP HTTP receiver)
localhost:8888  →  Collector self-metrics
```
