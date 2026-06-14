"""Structured logging for Azure Container Apps / Log Analytics.

Stdout format is controlled by ``LOG_STDOUT_FORMAT``:

  plain — human-readable gateway lines (default when ``ALLOW_MOCK_MODE=true``)
  json  — one JSON object per line for ``parse_json(Log_s)`` in Log Analytics

Structured fields are always exported to Loki via OTLP (JSON body) regardless of
stdout format.

Public API
----------
setup_structured_logging()       — call once at the very top of main()
log_event(event: dict)           — call per LLM event inside run_one_batch()
log_login_event(event: dict)     — call when a new session starts (turn 1)
log_startup_config(config: dict) — call once after setup to record runner config
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from generator.log_sanitize import sanitize_string, sanitize_value

_SERVICE = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry")
_ENV     = os.getenv("ENVIRONMENT", "prod")

# Standard LogRecord attributes — must never appear in exported JSON logs.
_LOG_RECORD_STD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


def stdout_format() -> str:
    """Return ``plain`` or ``json`` for container stdout."""
    explicit = os.getenv("LOG_STDOUT_FORMAT", "").strip().lower()
    if explicit in ("plain", "json"):
        return explicit
    mock = os.getenv("ALLOW_MOCK_MODE", "").lower() in ("true", "1", "yes")
    return "plain" if mock else "json"


def record_to_log_doc(record: logging.LogRecord) -> dict[str, Any]:
    """Build the structured log document for JSON export and HTML views."""
    doc: dict[str, Any] = {
        "timestamp":    datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "level":        record.levelname,
        "logger":       record.name,
        "message":      sanitize_string(record.getMessage()),
        "module":       record.module,
        "funcName":     record.funcName,
        "service_name": _SERVICE,
        "environment":  _ENV,
    }
    if record.exc_info:
        doc["exception"] = sanitize_string(logging.Formatter().formatException(record.exc_info))

    for key, val in record.__dict__.items():
        if key not in _LOG_RECORD_STD_KEYS and key not in doc:
            doc[key] = sanitize_value(val)
    return doc


def record_to_event(record: logging.LogRecord) -> dict[str, Any]:
    """Extract caller ``extra=`` fields from a log record as an event dict."""
    return {
        key: val
        for key, val in record.__dict__.items()
        if key not in _LOG_RECORD_STD_KEYS
    }


class JSONFormatter(logging.Formatter):
    """Format every log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record_to_log_doc(record), default=str)


def _stdout_formatter() -> logging.Formatter:
    if stdout_format() == "plain":
        from generator.plain_stdout_formatter import PlainStdoutFormatter  # noqa: WPS433

        return PlainStdoutFormatter()
    return JSONFormatter()


def setup_structured_logging() -> None:
    """Configure stdout logging and OTLP export. Idempotent."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)

    stdout_fmt = _stdout_formatter()
    handler = logging.StreamHandler()
    handler.setFormatter(stdout_fmt)
    root.addHandler(handler)

    from generator.telemetry_log_handler import TelemetryLogHandler  # noqa: WPS433

    capture = TelemetryLogHandler()
    capture.setFormatter(stdout_fmt)
    root.addHandler(capture)

    root.setLevel(logging.INFO)

    from generator import otel_logging  # noqa: WPS433 — avoid circular import at module load

    otel_logging.setup_otel_logging(json_formatter=JSONFormatter())


def _guardrail_action(event: dict[str, Any]) -> str:
    """Derive the guardrail outcome for safety dashboards.

    block  — prompt injection / jailbreak detected → request stopped
    redact — sensitive content or compliance violation → response sanitised
    allow  — clean request, passed through untouched
    """
    if event.get("jailbreak_attempt") or event.get("prompt_injection_detected"):
        return "block"
    if event.get("compliance_violation") or event.get("data_classification") in ("phi", "pii"):
        return "redact"
    return "allow"


def log_event(event: dict[str, Any]) -> None:
    """Emit one structured JSON log line per LLM event.

    Fields are chosen to be useful as Log Analytics filter/aggregate dimensions.

    Example KQL to query in Log Analytics:
        ContainerAppConsoleLogs_CL
        | extend e = parse_json(Log_s)
        | where e.event_type == "telemetry_event"
        | summarize avg(todouble(e.latency_ms)) by tostring(e.model_name)
    """
    from generator.prompt_logger import _current_trace_id

    extra: dict[str, Any] = {
            # ── Identity ─────────────────────────────────────────────────
            "event_type":          "telemetry_event",
            "request_id":          event.get("request_id"),
            "trace_id":            _current_trace_id() or event.get("trace_id"),
            "session_id":          event.get("session_id"),
            "turn_number":         event.get("turn_number"),
            "user_id":             event.get("user_id"),
            "user_email":          event.get("user_email"),
            "organization":        event.get("organization"),
            "department":          event.get("department"),
            "department_name":     event.get("department_name"),
            "client_name":         event.get("client_name"),
            "tenant_id":           event.get("department"),
            "data_classification": event.get("data_classification"),

            # ── User observability / adoption ────────────────────────────
            "eligible_user_count": event.get("eligible_user_count"),
            "is_new_user":         event.get("is_new_user"),
            "feature_id":          event.get("feature_id"),
            "task_completed":          event.get("task_completed"),
            "response_accepted":     event.get("response_accepted"),
            "regeneration":            event.get("regeneration"),
            "prompt_abandoned":        event.get("prompt_abandoned"),
            "conversation_abandoned":  event.get("conversation_abandoned"),
            "escalation":              event.get("escalation"),
            "hallucination_feedback":  event.get("hallucination_feedback"),
            "task_automated":          event.get("task_automated"),
            "ai_assisted":             event.get("ai_assisted"),
            "time_saved_ms":           event.get("time_saved_ms"),
            "productivity_gain_pct":   event.get("productivity_gain_pct"),
            "baseline_resolution_ms":  event.get("baseline_resolution_ms"),
            "resolution_time_ms":      event.get("resolution_time_ms"),
            "revenue_influence_usd":   event.get("revenue_influence_usd"),
            "cost_avoidance_usd":      event.get("cost_avoidance_usd"),
            "conversion_lift_pct":     event.get("conversion_lift_pct"),
            "message_count":           event.get("message_count"),
            "hour_of_day":             event.get("hour_of_day"),
            "sensitive_data_exposure": event.get("sensitive_data_exposure"),
            "pii_submitted":           event.get("pii_submitted"),
            "policy_violation":        event.get("policy_violation"),
            "unsafe_output":           event.get("unsafe_output"),
            "audit_logged":            event.get("audit_logged"),
            "access_violation":        event.get("access_violation"),
            "human_review":            event.get("human_review"),
            "compliance_pass":         event.get("compliance_pass"),

            # ── Routing ──────────────────────────────────────────────────
            "model_name":          event.get("model_name"),
            "model_provider":      event.get("model_provider"),
            "capability_tier":     event.get("capability_tier"),
            "routing_reason":      event.get("routing_reason"),
            "operation_name":      event.get("operation_name"),
            "region":              event.get("region"),

            # ── Performance ──────────────────────────────────────────────
            "latency_ms":          event.get("latency_ms"),
            "session_time_ms":     event.get("session_time_ms"),
            "queue_wait_ms":       event.get("queue_wait_ms"),
            "model_inference_ms":  event.get("model_inference_ms"),
            "first_token_ms":      event.get("first_token_ms"),
            "stream_response_ms":  event.get("stream_response_ms"),
            "streaming":           event.get("streaming"),
            "tokens_per_second":   event.get("tokens_per_second"),
            "sla_tier":            event.get("sla_tier"),
            "sla_target_ms":       event.get("sla_target_ms"),
            "sla_breached":        event.get("sla_breached"),

            # ── Tokens & cost ────────────────────────────────────────────
            "prompt_tokens":       event.get("prompt_tokens"),
            "completion_tokens":   event.get("completion_tokens"),
            "cache_read_tokens":   event.get("cache_read_tokens"),
            "total_tokens":        event.get("total_tokens"),
            "context_window_tokens":          event.get("context_window_tokens"),
            "context_window_utilization_pct": event.get("context_window_utilization_pct"),
            "cost_usd":            event.get("cost_usd"),
            "cache_savings_usd":   event.get("cache_savings_usd"),
            "daily_spend_usd":     event.get("daily_spend_usd"),
            "budget_exhausted":    event.get("budget_exhausted"),
            # cache_hit: any prompt-cache read tokens billed at cache price.
            "cache_hit":           (float(event.get("cache_read_tokens") or 0) > 0),

            # ── Outcome ──────────────────────────────────────────────────
            "status":              event.get("status"),
            "http_status_code":    event.get("http_status_code"),
            "error_type":          event.get("error_type"),
            "error_category":      event.get("error_category"),
            "is_retried":          event.get("is_retried"),
        "retry_count":         event.get("retry_count"),

            # ── Safety & security ────────────────────────────────────────
            "toxicity_score":              event.get("toxicity_score"),
            "prompt_injection_detected":   event.get("prompt_injection_detected"),
            "jailbreak_attempt":           event.get("jailbreak_attempt"),
            "compliance_violation":        event.get("compliance_violation"),
            "guardrail_action":            _guardrail_action(event),
    }
    logging.getLogger("generator.telemetry_event").info("telemetry_event", extra=extra)
    if stdout_format() == "plain":
        logging.getLogger("generator.access").info(
            "access",
            extra={**extra, "event_type": "access_log"},
        )


def log_login_event(event: dict[str, Any]) -> None:
    """Emit a login/session-start event for user-level observability dashboards."""
    from generator.prompt_logger import _current_trace_id

    logging.getLogger("generator.login_event").info(
        "login_event",
        extra={
            "event_type":   "login_event",
            "organization": event.get("organization"),
            "department":   event.get("department"),
            "department_name": event.get("department_name"),
            "region":       event.get("region"),
            "user_id":      event.get("user_id"),
            "user_email":   event.get("user_email"),
            "session_id":   event.get("session_id"),
            "tenant_id":    event.get("department"),
            "client_name":  event.get("client_name"),
            "auth_method":  event.get("auth_method"),
            "trace_id":     _current_trace_id() or event.get("trace_id"),
            "project_id":   event.get("project_id"),
            "is_new_user":  event.get("is_new_user"),
        },
    )


def log_startup_config(config: dict[str, Any]) -> None:
    """Emit runner startup configuration as a structured JSON line."""
    logging.getLogger("generator.runner").info(
        "runner_startup",
        extra={"event_type": "startup_config", **config},
    )
