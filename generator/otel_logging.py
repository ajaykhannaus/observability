"""OpenTelemetry log export — ships structured JSON logs to Loki via the OTel Collector.

Pairs with :mod:`generator.azure_logger` which formats each line as JSON.
The OTLP log *body* carries the raw JSON string (human-readable), while each
``extra=`` field is also sent as an OTLP log *attribute*. Loki's native OTLP
ingestion stores those attributes as structured metadata, so Grafana panels
filter/unwrap them directly — e.g. ``{service_name=~".+"} | event_type="telemetry_event"``
and ``... | unwrap latency_ms`` — WITHOUT a ``| json`` parser stage.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from opentelemetry._logs import SeverityNumber, get_logger, set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    _OTEL_LOGS_AVAILABLE = True
except ImportError:
    _OTEL_LOGS_AVAILABLE = False
    logger.warning("opentelemetry-sdk logs API not available")

try:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
        OTLPLogExporter as OTLPGrpcLogExporter,
    )

    _OTLP_GRPC_LOGS_AVAILABLE = True
except ImportError:
    _OTLP_GRPC_LOGS_AVAILABLE = False

try:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import (
        OTLPLogExporter as OTLPHttpLogExporter,
    )

    _OTLP_HTTP_LOGS_AVAILABLE = True
except ImportError:
    _OTLP_HTTP_LOGS_AVAILABLE = False

_OTLP_LOGS_AVAILABLE = _OTLP_GRPC_LOGS_AVAILABLE or _OTLP_HTTP_LOGS_AVAILABLE

_INITIALISED = False
_OTEL_LOGGER: Any = None

_LEVEL_TO_SEVERITY = {
    logging.DEBUG: SeverityNumber.DEBUG,
    logging.INFO: SeverityNumber.INFO,
    logging.WARNING: SeverityNumber.WARN,
    logging.ERROR: SeverityNumber.ERROR,
    logging.CRITICAL: SeverityNumber.FATAL,
}

# Standard LogRecord attributes — excluded so only caller ``extra=`` fields are
# promoted to OTLP log attributes (→ Loki structured metadata).
_LOG_RECORD_STD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


def _record_attributes(record: logging.LogRecord) -> dict[str, Any]:
    """Promote caller ``extra=`` fields to OTLP log attributes.

    Loki's native OTLP ingestion maps log-record attributes to *structured
    metadata*, which dashboards query directly (``| event_type="telemetry_event"``,
    ``unwrap latency_ms``) WITHOUT a ``| json`` parser stage. ``None`` values are
    dropped (OTLP forbids null) and non-primitives are stringified.
    """
    attrs: dict[str, Any] = {
        "level":  record.levelname,
        "logger": record.name,
    }
    for key, val in record.__dict__.items():
        if key in _LOG_RECORD_STD_KEYS or val is None:
            continue
        attrs[key] = val if isinstance(val, (str, bool, int, float)) else str(val)
    return attrs


def _normalize_http_logs_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/logs"):
        return endpoint
    return f"{endpoint}/v1/logs"


class _OTLPJSONHandler(logging.Handler):
    """Forwards JSON-formatted log lines to OTLP with the JSON as the log body."""

    def emit(self, record: logging.LogRecord) -> None:
        if _OTEL_LOGGER is None:
            return
        try:
            line = self.format(record)
            _OTEL_LOGGER.emit(
                body=line,
                timestamp=int(record.created * 1_000_000_000),
                observed_timestamp=int(time.time() * 1_000_000_000),
                severity_number=_LEVEL_TO_SEVERITY.get(
                    record.levelno, SeverityNumber.INFO,
                ),
                severity_text=record.levelname,
                # Promote extra= fields to structured metadata so Loki panels can
                # filter/unwrap them WITHOUT a `| json` parser stage.
                attributes=_record_attributes(record),
            )
        except Exception:
            self.handleError(record)


def setup_otel_logging(json_formatter: logging.Formatter | None = None) -> None:
    """Attach an OTLP JSON handler to the root logger. Idempotent."""
    global _INITIALISED, _OTEL_LOGGER
    if _INITIALISED:
        return
    _INITIALISED = True

    if not _OTEL_LOGS_AVAILABLE:
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry")
    environment = os.getenv("ENVIRONMENT", "prod")
    version = os.getenv("SERVICE_VERSION", "0.0.0")

    resource = Resource.create({
        "service.name":           service_name,
        "service.version":        version,
        "deployment.environment": environment,
    })

    provider = LoggerProvider(resource=resource)
    set_logger_provider(provider)
    _OTEL_LOGGER = get_logger("generator.telemetry")

    otlp_endpoint = (
        os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "").strip()
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    )
    if not otlp_endpoint:
        logger.error(
            "OTEL_EXPORTER_OTLP_ENDPOINT is not set — telemetry_event logs will NOT reach Loki. "
            "Set it to the OTel Collector URL (e.g. http://otel-collector-dev.internal.<domain>:4318)."
        )
        return
    if otlp_endpoint and _OTLP_LOGS_AVAILABLE:
        try:
            logs_protocol = os.getenv(
                "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
                os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
            ).lower()
            use_http = logs_protocol in ("http/protobuf", "http/json") or ":4318" in otlp_endpoint
            if use_http and _OTLP_HTTP_LOGS_AVAILABLE:
                exporter = OTLPHttpLogExporter(
                    endpoint=_normalize_http_logs_endpoint(otlp_endpoint),
                )
                transport = "http/protobuf"
            elif _OTLP_GRPC_LOGS_AVAILABLE:
                insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"
                exporter = OTLPGrpcLogExporter(endpoint=otlp_endpoint, insecure=insecure)
                transport = f"grpc (insecure={insecure})"
            else:
                raise RuntimeError("no OTLP log exporter available for configured protocol")
            provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
            handler = _OTLPJSONHandler()
            if json_formatter is not None:
                handler.setFormatter(json_formatter)
            logging.getLogger().addHandler(handler)
            logger.info("OTLP log exporter → %s (%s)", otlp_endpoint, transport)
        except Exception as exc:
            logger.warning("OTLP log exporter init failed: %s", exc)
    elif otlp_endpoint:
        logger.warning("OTLP log endpoint set but log exporter package unavailable")
