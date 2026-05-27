"""OpenTelemetry tracing for the AI telemetry runner.

A single ``setup_tracing()`` call configures the global tracer provider with
an OTLP gRPC exporter pointed at ``OTEL_EXPORTER_OTLP_ENDPOINT`` (the
in-cluster OTel Collector). Spans are batched and exported asynchronously.

The module is safe to import even when the OTel SDK is not installed — in
that case every helper degrades to a no-op so the runner can still execute
under minimal dependencies (e.g. in lightweight CI containers).

Initialisation order
--------------------
``setup_tracing()`` MUST be called before any ``run_one_batch()`` call but
AFTER ``azure_logger.setup_structured_logging()``, so structured-log
formatting (and any future ``LoggingInstrumentor`` hook) sees the tracer
provider already in place.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from generator import semantic_conventions as sc  # noqa: E402

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.context import Context
    from opentelemetry.propagate import inject as _inject_context
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    logger.warning("opentelemetry-sdk not installed — tracing disabled")

try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    _OTLP_AVAILABLE = True
except ImportError:
    _OTLP_AVAILABLE = False

_TRACER: Any = None
_INITIALISED = False


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_tracing() -> None:
    """Configure the global tracer provider. Idempotent."""
    global _TRACER, _INITIALISED

    if _INITIALISED:
        return
    _INITIALISED = True

    if not _OTEL_AVAILABLE:
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry")
    environment  = os.getenv("ENVIRONMENT", "prod")
    version      = os.getenv("SERVICE_VERSION", "0.0.0")
    region       = os.getenv("AZURE_LOCATION", os.getenv("AWS_REGION", "unknown"))

    resource = Resource.create({
        sc.RES_SERVICE_NAME:    service_name,
        sc.RES_SERVICE_VERSION: version,
        sc.RES_DEPLOYMENT_ENV:  environment,
        sc.RES_CLOUD_PROVIDER:  "azure",
        sc.RES_CLOUD_REGION:    region,
    })

    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if otlp_endpoint and _OTLP_AVAILABLE:
        try:
            insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info(
                "OTLP trace exporter → %s (insecure=%s)", otlp_endpoint, insecure,
            )
        except Exception as exc:
            logger.warning("OTLP trace exporter init failed: %s", exc)
    elif otlp_endpoint and not _OTLP_AVAILABLE:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry-exporter-otlp-proto-grpc "
            "is not installed — spans will be created but not exported.",
        )
    else:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set — spans will be created but not exported.")

    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(service_name, version)
    logger.info(
        "Tracing ready | service=%s | env=%s | version=%s", service_name, environment, version,
    )


def _tracer() -> Any:
    if _TRACER is None and _OTEL_AVAILABLE:
        setup_tracing()
    return _TRACER


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


@contextmanager
def batch_span() -> Iterator[Any]:
    """Open the root ``ai.batch.run`` span for one runner iteration."""
    tr = _tracer()
    if tr is None:
        yield _NoOpSpan()
        return
    with tr.start_as_current_span(sc.SPAN_BATCH, kind=SpanKind.INTERNAL) as span:
        yield span


@contextmanager
def request_span(event: dict[str, Any]) -> Iterator[Any]:
    """Open the per-event ``ai.request`` span and stamp identity attributes."""
    tr = _tracer()
    if tr is None:
        yield _NoOpSpan()
        return

    attrs = {
        sc.ATTR_REQUEST_ID:        event["request_id"],
        sc.ATTR_SESSION_ID:        event["session_id"],
        sc.ATTR_TURN_NUMBER:       event["turn_number"],
        sc.ATTR_USER_ID:           event["user_id"],
        sc.ATTR_TENANT_ID:         event["client_name"],
        sc.ATTR_PROJECT_ID:        event["project_id"],
        sc.ATTR_AUTH_METHOD:       event["auth_method"],
        sc.ATTR_DATA_CLASS:        event["data_classification"],
        sc.ATTR_MODEL_NAME:        event["model_name"],
        sc.ATTR_MODEL_PROVIDER:    event["model_provider"],
        sc.ATTR_CAPABILITY_TIER:   event["capability_tier"],
        sc.ATTR_ROUTING_REASON:    event["routing_reason"],
        sc.ATTR_OPERATION_NAME:    event["operation_name"],
        sc.ATTR_REGION:            event["region"],
        sc.ATTR_AVAILABILITY_ZONE: event["availability_zone"],
        sc.ATTR_STREAMING:         event["streaming"],
        sc.ATTR_LATENCY_MS:        event["latency_ms"],
        sc.ATTR_SLA_TARGET_MS:     event["sla_target_ms"],
        sc.ATTR_SLA_TIER:          event["sla_tier"],
        sc.ATTR_SLA_BREACHED:      event["sla_breached"],
        sc.ATTR_TOKENS_PROMPT:     event["prompt_tokens"],
        sc.ATTR_TOKENS_COMPLETION: event["completion_tokens"],
        sc.ATTR_TOKENS_CACHE_READ: event["cache_read_tokens"],
        sc.ATTR_TOKENS_TOTAL:      event["total_tokens"],
        sc.ATTR_COST_USD:          event["cost_usd"],
        sc.ATTR_DAILY_SPEND_USD:   event["daily_spend_usd"],
        sc.ATTR_BUDGET_USD:        event["budget_usd"],
        sc.ATTR_BUDGET_EXHAUSTED:  event["budget_exhausted"],
        sc.ATTR_STATUS:            event["status"],
        sc.ATTR_HTTP_STATUS:       event["http_status_code"],
        sc.ATTR_RETRIED:           event["is_retried"],
        sc.ATTR_RETRY_COUNT:       event["retry_count"],
    }
    if event.get("stop_reason"):
        attrs[sc.ATTR_STOP_REASON] = event["stop_reason"]
    if event.get("error_type"):
        attrs[sc.ATTR_ERROR_TYPE]     = event["error_type"]
        attrs[sc.ATTR_ERROR_CATEGORY] = event.get("error_category") or "unknown"

    with tr.start_as_current_span(
        sc.SPAN_REQUEST, kind=SpanKind.INTERNAL, attributes=attrs,
    ) as span:
        if event["status"] == "error":
            span.set_status(Status(StatusCode.ERROR, event.get("error_type") or ""))
        yield span


@contextmanager
def phase_span(name: str, duration_ms: float) -> Iterator[Any]:
    """Record one latency phase as a span. ``name`` must be one of the SPAN_*
    phase constants from semantic_conventions.
    """
    tr = _tracer()
    if tr is None:
        yield _NoOpSpan()
        return
    with tr.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
        # Self-attribute the duration so downstream queries can reconstruct
        # the phase breakdown even if span start/end timestamps are imprecise.
        span.set_attribute("ai.phase.duration_ms", duration_ms)
        yield span


@contextmanager
def publish_span(kind: str) -> Iterator[Any]:
    """Open a publish-path span. ``kind`` is "start" or "end"."""
    tr = _tracer()
    if tr is None:
        yield _NoOpSpan()
        return
    span_name = sc.SPAN_PUBLISH_START if kind == "start" else sc.SPAN_PUBLISH_END
    with tr.start_as_current_span(span_name, kind=SpanKind.PRODUCER) as span:
        yield span


def current_traceparent() -> str | None:
    """Return the W3C ``traceparent`` value for the active span, or None."""
    if not _OTEL_AVAILABLE:
        return None
    carrier: dict[str, str] = {}
    _inject_context(carrier)
    return carrier.get("traceparent")


def set_batch_attributes(span: Any, **attrs: Any) -> None:
    """Stamp batch-level aggregate attributes on the active batch span."""
    if span is None or not hasattr(span, "set_attribute"):
        return
    for key, val in attrs.items():
        if val is None:
            continue
        try:
            span.set_attribute(key, val)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# No-op fallback used when the OTel SDK is unavailable
# ---------------------------------------------------------------------------


class _NoOpSpan:
    def set_attribute(self, *_a: Any, **_kw: Any) -> None: ...
    def set_status(self, *_a: Any, **_kw: Any) -> None: ...
    def add_event(self, *_a: Any, **_kw: Any) -> None: ...
    def record_exception(self, *_a: Any, **_kw: Any) -> None: ...
    def __enter__(self) -> _NoOpSpan: return self
    def __exit__(self, *_a: Any) -> None: ...
