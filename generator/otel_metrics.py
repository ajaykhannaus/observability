"""OpenTelemetry metrics for the AI Gateway telemetry POC.

Five instruments are registered at module-load time against no-op stubs so
they are always importable.  Calling setup_otel() replaces them with real
SDK instruments backed by an OTLP exporter.  When the opentelemetry packages
are absent the module degrades gracefully to debug logging.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional OTel import block — module stays importable without the packages
# ---------------------------------------------------------------------------

try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    logger.warning("opentelemetry packages not found — metrics will be debug-logged only")

try:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

    _OTLP_AVAILABLE = True
except ImportError:
    _OTLP_AVAILABLE = False


# ---------------------------------------------------------------------------
# No-op stubs — replaced by setup_otel()
# ---------------------------------------------------------------------------


class _NoOpCounter:
    def add(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        logger.debug("noop counter +%s %s", amount, attributes)


class _NoOpHistogram:
    def record(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        logger.debug("noop histogram %s %s", amount, attributes)


request_count: Any = _NoOpCounter()
request_duration: Any = _NoOpHistogram()
request_token: Any = _NoOpCounter()
request_cost: Any = _NoOpCounter()
exception_count: Any = _NoOpCounter()

_initialized = False


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_otel() -> None:
    """Configure the real OTel MeterProvider and replace module-level instruments."""
    global request_count, request_duration, request_token, request_cost, exception_count, _initialized

    if _initialized:
        return

    if not _OTEL_AVAILABLE:
        logger.warning("OTel unavailable — metrics will be debug-logged only")
        _initialized = True
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry-poc")
    environment = os.getenv("ENVIRONMENT", "poc")
    export_interval_ms = int(os.getenv("OTEL_EXPORT_INTERVAL_MS", "30000"))
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )

    readers = []
    if _OTLP_AVAILABLE and otlp_endpoint:
        try:
            exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
            reader = PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=export_interval_ms,
            )
            readers.append(reader)
            logger.info("OTLP metric exporter → %s (interval %dms)", otlp_endpoint, export_interval_ms)
        except Exception as exc:
            logger.warning("OTLP exporter init failed (%s) — no-op mode", exc)

    provider = MeterProvider(resource=resource, metric_readers=readers)
    _otel_metrics.set_meter_provider(provider)
    meter = _otel_metrics.get_meter(service_name)

    request_count = meter.create_counter(
        name="ai_gateway_request_count",
        unit="1",
        description="Total AI gateway requests",
    )
    request_duration = meter.create_histogram(
        name="ai_gateway_request_duration",
        unit="ms",
        description="AI gateway request latency",
    )
    request_token = meter.create_counter(
        name="ai_gateway_request_token",
        unit="1",
        description="Token consumption by type",
    )
    request_cost = meter.create_counter(
        name="ai_gateway_request_cost",
        unit="USD",
        description="Accumulated request cost",
    )
    exception_count = meter.create_counter(
        name="ai_gateway_exception_count",
        unit="1",
        description="Failed requests",
    )

    _initialized = True
    logger.info("OTel metrics initialised for service '%s'", service_name)


# ---------------------------------------------------------------------------
# Recording helper
# ---------------------------------------------------------------------------


def record_metrics(event: dict[str, Any]) -> None:
    """Record all five OTel instruments for one event dict."""
    base: dict[str, Any] = {
        "model_name": event["model_name"],
        "model_provider": event["model_provider"],
        "client_name": event["client_name"],
        "operation_name": event["operation_name"],
        "status": event["status"],
        "project_id": event["project_id"],
    }

    try:
        request_count.add(1, base)
        request_duration.record(event["latency_ms"], base)

        for token_type, count in (
            ("prompt", event["prompt_tokens"]),
            ("completion", event["completion_tokens"]),
            ("cache_read", event["cache_read_tokens"]),
        ):
            request_token.add(count, {**base, "token_type": token_type})

        request_cost.add(event["cost_usd"], base)

        if event["status"] == "error":
            exception_count.add(
                1,
                {**base, "error_type": event.get("error_type") or "unknown"},
            )
    except Exception as exc:
        logger.error("OTel record_metrics failed: %s", exc)
