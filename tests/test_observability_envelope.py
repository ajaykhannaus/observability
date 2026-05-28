from __future__ import annotations

import pytest

from observability.envelope import (
    EVENT_AI_REQUEST_END,
    EVENT_APP_LOG,
    SCHEMA_VERSION,
    build_envelope,
)
from observability.publisher import EventHubPublisher


def test_build_envelope_shape(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "billing-api")
    monkeypatch.setenv("OBS_APP_ID", "billing-api")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("AZURE_LOCATION", "eastus")

    doc = build_envelope(
        EVENT_APP_LOG,
        {"level": "info", "message": "ok"},
        tenant_id="finance",
        trace_id="abc123",
    )

    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["event_type"] == EVENT_APP_LOG
    assert doc["app_id"] == "billing-api"
    assert doc["tenant_id"] == "finance"
    assert doc["trace_id"] == "abc123"
    assert doc["payload"]["message"] == "ok"


def test_ai_end_envelope(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "ai-telemetry-runner-dev")
    payload = {"request_id": "r-1", "latency_ms": 42.0, "status": "success"}
    doc = build_envelope(EVENT_AI_REQUEST_END, payload, tenant_id="healthcare-portal")
    assert doc["event_type"] == EVENT_AI_REQUEST_END
    assert doc["payload"]["request_id"] == "r-1"


def test_publisher_mock_mode(monkeypatch):
    monkeypatch.delenv("EVENTHUB_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("EVENTHUB_NAMESPACE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    pub = EventHubPublisher()
    assert pub.mock_mode is True
    assert pub.publish(EVENT_APP_LOG, {"message": "x"}) is True
