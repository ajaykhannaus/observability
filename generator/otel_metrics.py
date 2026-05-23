"""OpenTelemetry metrics for the AI Gateway telemetry POC.

Five instruments are registered at module-load time against no-op stubs so
they are always importable.  Calling setup_otel() replaces them with real
SDK instruments.

When PROMETHEUS_PORT is set, metrics are exposed on http://localhost:{port}/metrics
so Grafana can scrape them directly — no extra collector needed.
When the opentelemetry packages are absent the module degrades to debug logging.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional OTel SDK
# ---------------------------------------------------------------------------

try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    logger.warning("opentelemetry-sdk not found — metrics will be debug-logged only")

try:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

    _OTLP_AVAILABLE = True
except ImportError:
    _OTLP_AVAILABLE = False

try:
    from opentelemetry.exporter.prometheus import PrometheusMetricReader as _PrometheusReader
    from prometheus_client import start_http_server as _start_http_server

    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False


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

# Fixed process-level labels (set once from env, not random per event)
_PROCESS_SERVICE     = os.getenv("AI_SERVICE",   "ai-gateway")
_PROCESS_ENVIRONMENT = os.getenv("ENVIRONMENT",  "poc")
_PROCESS_REGION      = os.getenv("AWS_REGION",   "us-east-1")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_otel() -> None:
    """Configure the real OTel MeterProvider and replace module-level instruments.

    Readers activated based on environment:
      PROMETHEUS_PORT  → Prometheus /metrics HTTP endpoint (for local Grafana)
      OTEL_EXPORTER_OTLP_ENDPOINT → OTLP gRPC (for Azure Monitor)
    Both can be active simultaneously.
    """
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
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    prometheus_port = int(os.getenv("PROMETHEUS_PORT", "0"))

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )

    readers: list[Any] = []

    # ── Prometheus reader (local Grafana scrape) ──────────────────────────
    if prometheus_port:
        if _PROMETHEUS_AVAILABLE:
            try:
                readers.append(_PrometheusReader())
                _start_http_server(prometheus_port)
                logger.info(
                    "Prometheus metrics exposed on http://localhost:%d/metrics", prometheus_port
                )
            except Exception as exc:
                logger.warning("Prometheus exporter init failed: %s", exc)
        else:
            logger.warning(
                "PROMETHEUS_PORT=%d set but opentelemetry-exporter-prometheus not installed",
                prometheus_port,
            )

    # ── OTLP reader (Azure Monitor / remote) ─────────────────────────────
    if otlp_endpoint and _OTLP_AVAILABLE:
        try:
            exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
            readers.append(
                PeriodicExportingMetricReader(
                    exporter, export_interval_millis=export_interval_ms
                )
            )
            logger.info("OTLP metric exporter → %s (interval %dms)", otlp_endpoint, export_interval_ms)
        except Exception as exc:
            logger.warning("OTLP exporter init failed: %s", exc)

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
        description="AI gateway request latency in milliseconds",
    )
    request_token = meter.create_counter(
        name="ai_gateway_request_token",
        unit="1",
        description="Token consumption by type",
    )
    request_cost = meter.create_counter(
        name="ai_gateway_request_cost",
        unit="USD",
        description="Accumulated request cost in USD",
    )
    exception_count = meter.create_counter(
        name="ai_gateway_exception_count",
        unit="1",
        description="Failed requests",
    )

    _initialized = True
    logger.info(
        "OTel metrics ready | service=%s | readers=%d", service_name, len(readers)
    )


# ---------------------------------------------------------------------------
# Recording helper
# ---------------------------------------------------------------------------


def record_metrics(event: dict[str, Any]) -> None:
    """Record all five OTel instruments for one event dict.

    Labels are kept intentionally low-cardinality so Prometheus counters
    accumulate on the same series across requests and rate() returns non-zero.
    High-cardinality fields (project_id, client_name, per-event service/env/region)
    are excluded here — they belong in the raw Event Hub events only.
    """
    base: dict[str, Any] = {
        "model_name":     event["model_name"],
        "model_provider": event["model_provider"],
        "operation_name": event["operation_name"],
        "status":         event["status"],
        "service":        _PROCESS_SERVICE,
        "environment":    _PROCESS_ENVIRONMENT,
        "region":         _PROCESS_REGION,
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
                {
                    **base,
                    "error_type":     event.get("error_type")     or "unknown",
                    "error_category": event.get("error_category") or "unknown",
                    "http_status":    str(event.get("http_status_code", 0)),
                },
            )
    except Exception as exc:
        logger.error("OTel record_metrics failed: %s", exc)
