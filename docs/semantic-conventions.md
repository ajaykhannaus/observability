# AI Telemetry Semantic Conventions

**Status:** v1.0 — locked for Bucket 1
**Owner:** Telemetry Platform team
**Implementation:** [`generator/semantic_conventions.py`](../generator/semantic_conventions.py)

This document defines the span names, resource attributes, span attributes, and metric names every instrumented AI service in this platform MUST use. It exists so that:

- Every Grafana dashboard query is built against a stable schema.
- Every alert rule uses the same label vocabulary.
- Any future LLM gateway / agent / RAG service that wants to plug into this pipeline knows exactly what shape its spans must take.

If you add a row here, also add the corresponding constant in `semantic_conventions.py`. If you change a name, treat it as a breaking change to the platform contract.

---

## 1. Span names

Spans form a tree rooted at `ai.batch.run`:

```
ai.batch.run                       (server span — one per batch tick)
└── ai.request                     (one per LLM gateway request)
    ├── ai.request.queue_wait      (time spent in the gateway queue)
    ├── ai.request.model_inference (model TTFB→completion)
    ├── ai.request.first_token     (TTFB only, streaming requests)
    ├── ai.request.stream_response (tokens streamed back to caller)
    ├── ai.publish.start_event     (Event Hubs START emission)
    └── ai.publish.end_event       (Event Hubs END emission)
```

| Span name | Kind | Description |
|---|---|---|
| `ai.batch.run` | `INTERNAL` | One iteration of the runner loop. Carries batch-level aggregate attributes. |
| `ai.request` | `INTERNAL` | One LLM gateway request. Parent of latency-phase spans. |
| `ai.request.queue_wait` | `INTERNAL` | Time the request spent waiting in the gateway queue before dispatch. |
| `ai.request.model_inference` | `CLIENT` | Inference call to the model provider. |
| `ai.request.first_token` | `INTERNAL` | Time-to-first-token (streaming only — zero on non-streaming requests). |
| `ai.request.stream_response` | `INTERNAL` | Time streaming the response back to the caller. |
| `ai.publish.start_event` | `PRODUCER` | Emit the START event to Event Hubs. |
| `ai.publish.end_event` | `PRODUCER` | Emit the END event to Event Hubs. |

**Latency invariant:** for any `ai.request` span, the four child phase durations MUST sum to within ±5 ms of the parent's `ai.latency.total_ms` attribute. Tests enforce this.

---

## 2. Resource attributes

Set once on the `TracerProvider` and `MeterProvider` resource — never per span.

| Key | Type | Example | Source env var |
|---|---|---|---|
| `service.name` | string | `ai-telemetry` | `OTEL_SERVICE_NAME` |
| `service.version` | string | `1.4.0` | `SERVICE_VERSION` (git SHA at build time) |
| `deployment.environment` | string | `prod` / `staging` / `dev` | `ENVIRONMENT` |
| `cloud.provider` | string | `azure` | hard-coded for now |
| `cloud.region` | string | `eastus` | `AZURE_LOCATION` |

---

## 3. Span attributes

All attributes live under the `ai.*` namespace. Use the constants in `semantic_conventions.py` — never raw strings.

### 3.1 Identity (set on every `ai.request` span)

| Attribute | Type | Cardinality | Notes |
|---|---|---|---|
| `ai.request.id` | string (UUID) | very high | Per-request UUID. Do not put on metrics. |
| `ai.session.id` | string (UUID) | high | Sticky across multi-turn conversations. |
| `ai.session.turn` | int | low | 1, 2, 3, … |
| `ai.user.id` | string | medium | Hash of email; safe for traces, gated on metrics. |
| `ai.user.email` | string | high | **Never** on metrics. Redacted by collector at the prompt-text boundary. |
| `ai.tenant.id` | string | low (~10) | = `client_name`. **Required** on every signal for read-side enforcement. |
| `ai.project.id` | string | medium | Stable per-project identifier. |
| `ai.auth.method` | string | low | `api_key` / `jwt_apigee` / `jwt_azure_ad` / `mtls` |
| `ai.data.classification` | string | low | `phi` / `pii` / `confidential` / `internal` |

### 3.2 Model & routing

| Attribute | Type | Cardinality | Notes |
|---|---|---|---|
| `ai.model.name` | string | low (~10) | `claude-haiku-3-5`, `gpt-4o-mini`, … |
| `ai.model.provider` | string | low (~5) | `anthropic` / `openai` / `google` |
| `ai.model.version` | string | low | Provider-specific version pin. |
| `ai.model.capability_tier` | string | low | `fast` / `balanced` / `premium` |
| `ai.routing.reason` | string | low | `cost_optimised` / `capability_match` / `user_pinned` / `fallback` / `load_balanced` / `latency_optimised` |

### 3.3 Operation

| Attribute | Type | Notes |
|---|---|---|
| `ai.operation.name` | string | `chat_completion`, `summarisation`, `clinical_note_analysis`, … |
| `ai.region` | string | `us-east-1`, `eu-west-1`, … |
| `ai.availability_zone` | string | `us-east-1a` |
| `ai.streaming` | bool | True if response was streamed. |

### 3.4 Performance

| Attribute | Type | Unit | Notes |
|---|---|---|---|
| `ai.latency.total_ms` | float | ms | End-to-end. |
| `ai.latency.queue_wait_ms` | float | ms | Child phase. |
| `ai.latency.model_inference_ms` | float | ms | Child phase. |
| `ai.latency.first_token_ms` | float | ms | Streaming requests only; 0 otherwise. |
| `ai.latency.stream_response_ms` | float | ms | Streaming requests only; 0 otherwise. |
| `ai.sla.target_ms` | int | ms | Per-client SLA target. |
| `ai.sla.tier` | string | — | `premium` / `standard` / `basic` |
| `ai.sla.breached` | bool | — | `total_ms > target_ms`. |

### 3.5 Tokens & cost

| Attribute | Type | Unit | Notes |
|---|---|---|---|
| `ai.tokens.prompt` | int | tokens | |
| `ai.tokens.completion` | int | tokens | |
| `ai.tokens.cache_read` | int | tokens | Cached prefix tokens. |
| `ai.tokens.total` | int | tokens | = prompt + completion + cache_read. |
| `ai.tokens.per_second` | float | tokens/s | Streaming throughput. |
| `ai.cost.usd` | float | USD | Computed via `calculate_cost`. |
| `ai.cost.daily_spend_usd` | float | USD | Running per-tenant total today. |
| `ai.cost.budget_usd` | float | USD | Per-tenant daily budget. |
| `ai.cost.budget_exhausted` | bool | — | `daily_spend >= budget`. |

### 3.6 Outcome

| Attribute | Type | Notes |
|---|---|---|
| `ai.status` | string | `success` / `error` |
| `ai.http.status_code` | int | 200, 429, 504, … |
| `ai.response.stop_reason` | string | `stop` / `max_tokens` / `stop_sequence` / `tool_use` |
| `ai.error.type` | string | `rate_limit` / `timeout` / `model_unavailable` / `context_length` / … |
| `ai.error.category` | string | `throttling` / `availability` / `input_validation` / `auth` / `server` |
| `ai.retry.attempted` | bool | — |
| `ai.retry.count` | int | 0–3 |

### 3.7 Batch-level (on `ai.batch.run` span only)

| Attribute | Type | Notes |
|---|---|---|
| `ai.batch.size` | int | Events in this batch. |
| `ai.batch.successes` | int | |
| `ai.batch.errors` | int | |
| `ai.batch.sla_breaches` | int | |
| `ai.batch.cost_usd` | float | |
| `ai.batch.tokens_total` | int | |

### 3.8 Anomaly state (on `ai.batch.run`)

| Attribute | Type | Notes |
|---|---|---|
| `ai.anomaly.degraded_model` | string \| null | Name of model currently degraded. |
| `ai.anomaly.cascade_active` | bool | |
| `ai.anomaly.rate_limited_client` | string \| null | |

---

## 4. Metric names

All metrics share the `ai_gateway_*` namespace (Prometheus convention — underscores, not dots).

| Metric | Type | Unit | Labels |
|---|---|---|---|
| `ai_gateway_request_count` | counter | 1 | `model_name`, `model_provider`, `operation_name`, `status`, `service`, `environment`, `region` |
| `ai_gateway_request_duration` | histogram | ms | same as above, with exemplars to traces |
| `ai_gateway_request_token` | counter | 1 | same + `token_type` (`prompt` / `completion` / `cache_read`) |
| `ai_gateway_request_cost` | counter | USD | same as `request_count` |
| `ai_gateway_exception_count` | counter | 1 | same + `error_type`, `error_category`, `http_status` |

### 4.1 Runner self-metrics (NFR-014)

| Metric | Type | Unit | Labels |
|---|---|---|---|
| `ai_telemetry_runner_batch_duration_seconds` | histogram | s | `service`, `environment` |
| `ai_telemetry_runner_publish_errors_total` | counter | 1 | `service`, `environment`, `reason` (`flush_error` / `produce_error` / `mock_mode`) |
| `ai_telemetry_runner_kafka_queue_depth` | gauge | 1 | `service`, `environment` |
| `ai_telemetry_runner_health_scrapes_total` | counter | 1 | `service`, `environment`, `endpoint` |

---

## 5. Trace context propagation

The runner uses W3C Trace Context (`traceparent`) — the OTel default. When emitting events to Event Hubs:

- The current span's context is serialised as a `traceparent` Kafka message header.
- Downstream consumers (ADX ingestion shim, Bucket 2 audit consumer, future evaluator) MUST read this header and continue the trace.
- Per the W3C spec, header format is `00-<32 hex trace_id>-<16 hex span_id>-<2 hex flags>`.

**Why W3C and not the legacy B3 / Jaeger format:** OTel's default propagator chain is `tracecontext,baggage`. Tempo, Loki (via derived fields), and Prometheus exemplars all key off the W3C `trace_id` natively — picking anything else costs us out-of-the-box correlation.

---

## 6. Cardinality budget

To keep Prometheus + Tempo cheap, the following attributes are **whitelisted for metrics labels**:

`service`, `environment`, `region`, `model_name`, `model_provider`, `operation_name`, `status`, `token_type`, `error_type`, `error_category`, `http_status`, `tenant_id`

Everything else is allowed on **spans** but never on metric labels. The OTel Collector enforces this via an `attributes/drop` processor.

---

## 7. Versioning

This document is versioned semver-style. Adding a new optional attribute is a minor bump. Removing or renaming any of the above is a major bump and requires a deprecation period documented in `CHANGELOG.md`.

Current version: **1.0** (locked Bucket 1).
