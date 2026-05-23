"""Structured JSON logging for Azure Container Apps / Log Analytics.

When running on Azure Container Apps, stdout is collected automatically and
appears in Log Analytics under the table ContainerAppConsoleLogs_CL (field: Log_s).
Because every line is valid JSON, KQL can parse fields with parse_json(Log_s).

Public API
----------
setup_structured_logging()       — call once at the very top of main()
log_event(event: dict)           — call per LLM event inside run_one_batch()
log_startup_config(config: dict) — call once after setup to record runner config
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

_SERVICE = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry-poc")
_ENV     = os.getenv("ENVIRONMENT", "poc")


class JSONFormatter(logging.Formatter):
    """Format every log record as a single-line JSON object.

    Standard fields: timestamp, level, logger, message, module, funcName,
                     service_name, environment, exception (when present).
    Extra fields: any key injected via logging.info(..., extra={...}).
    """

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "timestamp":    datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":        record.levelname,
            "logger":       record.name,
            "message":      record.getMessage(),
            "module":       record.module,
            "funcName":     record.funcName,
            "service_name": _SERVICE,
            "environment":  _ENV,
        }
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)

        # Carry through any `extra=` fields injected by the caller.
        # Skip Python-internal LogRecord attributes to avoid noise.
        std_keys = set(logging.LogRecord.__dict__) | set(doc)
        for key, val in record.__dict__.items():
            if key not in std_keys:
                doc[key] = val

        return json.dumps(doc, default=str)


def setup_structured_logging() -> None:
    """Replace all root logger handlers with a single JSON-to-stdout handler.

    Call this once at the very start of main(), before any other logging call.
    Safe to call multiple times — subsequent calls replace the handler cleanly.
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_event(event: dict[str, Any]) -> None:
    """Emit one structured JSON log line per LLM event.

    Fields are chosen to be useful as Log Analytics filter/aggregate dimensions.

    Example KQL to query in Log Analytics:
        ContainerAppConsoleLogs_CL
        | extend e = parse_json(Log_s)
        | where e.event_type == "telemetry_event"
        | summarize avg(todouble(e.latency_ms)) by tostring(e.model_name)
    """
    logging.getLogger("generator.telemetry_event").info(
        "telemetry_event",
        extra={
            "event_type":        "telemetry_event",
            "request_id":        event.get("request_id"),
            "model_name":        event.get("model_name"),
            "model_provider":    event.get("model_provider"),
            "operation_name":    event.get("operation_name"),
            "status":            event.get("status"),
            "latency_ms":        event.get("latency_ms"),
            "cost_usd":          event.get("cost_usd"),
            "total_tokens":      event.get("total_tokens"),
            "prompt_tokens":     event.get("prompt_tokens"),
            "completion_tokens": event.get("completion_tokens"),
            "error_type":        event.get("error_type"),
            "http_status_code":  event.get("http_status_code"),
            "client_name":       event.get("client_name"),
        },
    )


def log_startup_config(config: dict[str, Any]) -> None:
    """Emit runner startup configuration as a structured JSON line."""
    logging.getLogger("generator.runner").info(
        "runner_startup",
        extra={"event_type": "startup_config", **config},
    )
