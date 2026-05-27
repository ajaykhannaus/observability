"""Verify that the publisher's ``headers`` argument is wired correctly so
``traceparent`` flows into Event Hub message properties.
"""
from __future__ import annotations

from generator.kafka_publisher import (
    KafkaPublisher,
    _serialise_headers,
)


def test_serialise_headers_skips_empty() -> None:
    out = _serialise_headers({"traceparent": "", "other": "value", "blank": None})
    assert out == [("other", b"value")]


def test_serialise_headers_returns_none_when_all_empty() -> None:
    assert _serialise_headers({}) is None
    assert _serialise_headers({"traceparent": ""}) is None
    assert _serialise_headers(None) is None


def test_serialise_headers_bytes_passthrough() -> None:
    out = _serialise_headers({"x-key": b"already-bytes"})
    assert out == [("x-key", b"already-bytes")]


def test_publish_with_headers_mock_mode(monkeypatch) -> None:
    """In mock mode the publisher logs the traceparent and returns True."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("ALLOW_MOCK_MODE", "true")
    monkeypatch.delenv("EVENTHUB_NAMESPACE", raising=False)
    monkeypatch.delenv("EVENTHUB_CONNECTION_STRING", raising=False)

    pub = KafkaPublisher()
    event = {
        "request_id":           "req-1",
        "session_id":           "s-1",
        "turn_number":          1,
        "user_id":              "u-1",
        "user_email":           "u-1@example.com",
        "client_name":          "test-tenant",
        "project_id":           "proj-1",
        "auth_method":          "api_key",
        "timestamp_start":      "2026-05-27T00:00:00Z",
        "operation_name":       "chat_completion",
        "model_name":           "claude-haiku-3-5",
        "model_provider":       "anthropic",
        "streaming":            False,
        "latency_ms":           120.0,
        "prompt_tokens":        100,
        "completion_tokens":    50,
        "cache_read_tokens":    0,
        "total_tokens":         150,
        "cost_usd":             0.001,
        "status":               "success",
        "error_type":           None,
        "stop_reason":          "stop",
        "http_status_code":     200,
    }
    headers = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    assert pub.publish_start_event("evt-1", event, headers=headers) is True
    assert pub.publish_end_event("evt-1", event, headers=headers) is True
