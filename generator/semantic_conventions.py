"""AI telemetry semantic conventions.

Single source of truth for every span name and attribute key the runner
(and any future instrumented service) emits. Keep this file and
``docs/semantic-conventions.md`` in lock-step — if you add a constant here,
update the catalogue doc; if you add a row to the catalogue doc, add a
constant here.

Naming follows the OpenTelemetry conventions ``<namespace>.<thing>.<field>``
so signals integrate cleanly with upstream OTel processors and Grafana's
auto-derived fields.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Span names
# ---------------------------------------------------------------------------
# A batch run is the unit of work the runner schedules every BATCH_INTERVAL_S.
SPAN_BATCH = "ai.batch.run"

# One LLM gateway request — parent of all latency-phase spans for the request.
SPAN_REQUEST = "ai.request"

# Latency-phase child spans (must sum to the parent latency_ms).
SPAN_QUEUE_WAIT       = "ai.request.queue_wait"
SPAN_MODEL_INFERENCE  = "ai.request.model_inference"
SPAN_FIRST_TOKEN      = "ai.request.first_token"
SPAN_STREAM_RESPONSE  = "ai.request.stream_response"

# Publish path (Event Hubs).
SPAN_PUBLISH_START = "ai.publish.start_event"
SPAN_PUBLISH_END   = "ai.publish.end_event"

# ---------------------------------------------------------------------------
# Resource attributes (process-level — set once at TracerProvider creation)
# ---------------------------------------------------------------------------
RES_SERVICE_NAME       = "service.name"
RES_SERVICE_VERSION    = "service.version"
RES_DEPLOYMENT_ENV     = "deployment.environment"
RES_CLOUD_PROVIDER     = "cloud.provider"
RES_CLOUD_REGION       = "cloud.region"

# ---------------------------------------------------------------------------
# Span attributes — namespaced under ``ai.*``
# ---------------------------------------------------------------------------

# ── Identity ───────────────────────────────────────────────────────────────
ATTR_REQUEST_ID         = "ai.request.id"
ATTR_SESSION_ID         = "ai.session.id"
ATTR_TURN_NUMBER        = "ai.session.turn"
ATTR_USER_ID            = "ai.user.id"
ATTR_USER_EMAIL         = "ai.user.email"
ATTR_TENANT_ID          = "ai.tenant.id"              # = client_name today
ATTR_PROJECT_ID         = "ai.project.id"
ATTR_AUTH_METHOD        = "ai.auth.method"
ATTR_DATA_CLASS         = "ai.data.classification"    # phi | pii | confidential | internal

# ── Model / routing ────────────────────────────────────────────────────────
ATTR_MODEL_NAME         = "ai.model.name"
ATTR_MODEL_PROVIDER     = "ai.model.provider"
ATTR_MODEL_VERSION      = "ai.model.version"
ATTR_CAPABILITY_TIER    = "ai.model.capability_tier"
ATTR_ROUTING_REASON     = "ai.routing.reason"

# ── Operation ──────────────────────────────────────────────────────────────
ATTR_OPERATION_NAME     = "ai.operation.name"
ATTR_REGION             = "ai.region"
ATTR_AVAILABILITY_ZONE  = "ai.availability_zone"
ATTR_STREAMING          = "ai.streaming"

# ── Performance ────────────────────────────────────────────────────────────
ATTR_LATENCY_MS              = "ai.latency.total_ms"
ATTR_LATENCY_QUEUE_MS        = "ai.latency.queue_wait_ms"
ATTR_LATENCY_INFERENCE_MS    = "ai.latency.model_inference_ms"
ATTR_LATENCY_FIRST_TOKEN_MS  = "ai.latency.first_token_ms"
ATTR_LATENCY_STREAM_MS       = "ai.latency.stream_response_ms"
ATTR_SLA_TARGET_MS           = "ai.sla.target_ms"
ATTR_SLA_TIER                = "ai.sla.tier"
ATTR_SLA_BREACHED            = "ai.sla.breached"

# ── Tokens & cost ──────────────────────────────────────────────────────────
ATTR_TOKENS_PROMPT      = "ai.tokens.prompt"
ATTR_TOKENS_COMPLETION  = "ai.tokens.completion"
ATTR_TOKENS_CACHE_READ  = "ai.tokens.cache_read"
ATTR_TOKENS_TOTAL       = "ai.tokens.total"
ATTR_TOKENS_PER_SECOND  = "ai.tokens.per_second"
ATTR_COST_USD           = "ai.cost.usd"
ATTR_DAILY_SPEND_USD    = "ai.cost.daily_spend_usd"
ATTR_BUDGET_USD         = "ai.cost.budget_usd"
ATTR_BUDGET_EXHAUSTED   = "ai.cost.budget_exhausted"

# ── Outcome ────────────────────────────────────────────────────────────────
ATTR_STATUS             = "ai.status"                 # success | error
ATTR_HTTP_STATUS        = "ai.http.status_code"
ATTR_STOP_REASON        = "ai.response.stop_reason"
ATTR_ERROR_TYPE         = "ai.error.type"
ATTR_ERROR_CATEGORY     = "ai.error.category"
ATTR_RETRIED            = "ai.retry.attempted"
ATTR_RETRY_COUNT        = "ai.retry.count"

# ── Batch-span attributes ──────────────────────────────────────────────────
ATTR_BATCH_SIZE         = "ai.batch.size"
ATTR_BATCH_OK           = "ai.batch.successes"
ATTR_BATCH_ERR          = "ai.batch.errors"
ATTR_BATCH_SLA_BREACH   = "ai.batch.sla_breaches"
ATTR_BATCH_COST_USD     = "ai.batch.cost_usd"
ATTR_BATCH_TOKENS       = "ai.batch.tokens_total"

# ── Anomaly & policy ───────────────────────────────────────────────────────
ATTR_ANOMALY_DEGRADED   = "ai.anomaly.degraded_model"
ATTR_ANOMALY_CASCADE    = "ai.anomaly.cascade_active"
ATTR_ANOMALY_RATELIMIT  = "ai.anomaly.rate_limited_client"

# ---------------------------------------------------------------------------
# Metric names — keep in sync with otel_metrics.py and rules.yml
# ---------------------------------------------------------------------------
METRIC_REQUEST_COUNT      = "ai_gateway_request_count"
METRIC_REQUEST_DURATION   = "ai_gateway_request_duration"
METRIC_REQUEST_TOKEN      = "ai_gateway_request_token"
METRIC_REQUEST_COST       = "ai_gateway_request_cost"
METRIC_EXCEPTION_COUNT    = "ai_gateway_exception_count"

# Runner self-metrics (NFR-014: observe the observer).
METRIC_SELF_BATCH_DURATION    = "ai_telemetry_runner_batch_duration_seconds"
METRIC_SELF_PUBLISH_ERRORS    = "ai_telemetry_runner_publish_errors_total"
METRIC_SELF_QUEUE_DEPTH       = "ai_telemetry_runner_kafka_queue_depth"
METRIC_SELF_HEALTH_SCRAPES    = "ai_telemetry_runner_health_scrapes_total"

__all__ = [name for name in dir() if name.startswith(("SPAN_", "RES_", "ATTR_", "METRIC_"))]
