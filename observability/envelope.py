from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"

EVENT_AI_REQUEST_START = "ai.request.start"
EVENT_AI_REQUEST_END = "ai.request.end"
EVENT_AI_PROMPT_LOG = "ai.prompt.log"
EVENT_APP_LOG = "app.log"
EVENT_APP_METRIC = "app.metric"
EVENT_APP_AUDIT = "app.audit"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def app_id() -> str:
    return (
        os.getenv("OBS_APP_ID", "").strip()
        or os.getenv("APP_NAME", "").strip()
        or os.getenv("OTEL_SERVICE_NAME", "unknown-app")
    )


def service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME", app_id())


def environment() -> str:
    return os.getenv("ENVIRONMENT", "dev")


def region() -> str:
    return os.getenv("AZURE_LOCATION", os.getenv("CLOUD_REGION", "eastus"))


def build_envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    occurred_at: str | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    tenant_id: str | None = None,
    app: str | None = None,
    service: str | None = None,
    env: str | None = None,
    ingest_region: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": occurred_at or _now_iso(),
        "ingested_at": _now_iso(),
        "app_id": app or app_id(),
        "service_name": service or service_name(),
        "environment": env or environment(),
        "tenant_id": tenant_id or payload.get("tenant_id") or payload.get("client_name"),
        "trace_id": trace_id or payload.get("trace_id") or "",
        "correlation_id": correlation_id or payload.get("request_id") or payload.get("event_id") or "",
        "region": ingest_region or region(),
        "payload": payload,
    }
