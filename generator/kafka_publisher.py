from __future__ import annotations

import os
from typing import Any

from observability.envelope import (
    EVENT_AI_REQUEST_END,
    EVENT_AI_REQUEST_START,
    build_envelope,
)
from observability.publisher import EventHubPublisher, PublisherConfigError

__all__ = ["KafkaPublisher", "PublisherConfigError"]


def _start_payload(event_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "request_id": event.get("request_id"),
        "session_id": event.get("session_id"),
        "turn_number": event.get("turn_number"),
        "user_email": event.get("user_email"),
        "client_name": event.get("client_name"),
        "tenant_id": event.get("client_name"),
        "project_id": event.get("project_id"),
        "auth_method": event.get("auth_method"),
        "operation_name": event.get("operation_name"),
        "model_name": event.get("model_name"),
        "model_provider": event.get("model_provider"),
        "streaming": event.get("streaming"),
        "timestamp_start": event.get("timestamp_start"),
    }


def _end_payload(event_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "request_id": event.get("request_id"),
        "session_id": event.get("session_id"),
        "turn_number": event.get("turn_number"),
        "user_id": event.get("user_id"),
        "user_email": event.get("user_email"),
        "client_name": event.get("client_name"),
        "tenant_id": event.get("client_name"),
        "project_id": event.get("project_id"),
        "auth_method": event.get("auth_method"),
        "data_classification": event.get("data_classification"),
        "operation_name": event.get("operation_name"),
        "model_name": event.get("model_name"),
        "model_provider": event.get("model_provider"),
        "routing_reason": event.get("routing_reason"),
        "streaming": event.get("streaming"),
        "region": event.get("region"),
        "timestamp_start": event.get("timestamp_start"),
        "latency_ms": event.get("latency_ms"),
        "queue_wait_ms": event.get("queue_wait_ms"),
        "model_inference_ms": event.get("model_inference_ms"),
        "first_token_ms": event.get("first_token_ms"),
        "stream_response_ms": event.get("stream_response_ms"),
        "tokens_per_second": event.get("tokens_per_second"),
        "prompt_tokens": event.get("prompt_tokens"),
        "completion_tokens": event.get("completion_tokens"),
        "cache_read_tokens": event.get("cache_read_tokens"),
        "total_tokens": event.get("total_tokens"),
        "cost_usd": event.get("cost_usd"),
        "status": event.get("status"),
        "error_type": event.get("error_type"),
        "stop_reason": event.get("stop_reason"),
        "http_status_code": event.get("http_status_code"),
        "is_retried": event.get("is_retried"),
        "retry_count": event.get("retry_count"),
        "sla_breached": event.get("sla_breached"),
    }


class KafkaPublisher:
    def __init__(self) -> None:
        self._publisher = EventHubPublisher()

    @property
    def is_healthy(self) -> bool:
        return self._publisher.is_healthy

    @property
    def mock_mode(self) -> bool:
        return self._publisher.mock_mode

    @property
    def queue_depth(self) -> int:
        return self._publisher.queue_depth

    def publish_start_event(
        self,
        event_id: str,
        event: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        payload = _start_payload(event_id, event)
        envelope = build_envelope(
            EVENT_AI_REQUEST_START,
            payload,
            event_id=event_id,
            occurred_at=str(event.get("timestamp_start", "")),
            tenant_id=event.get("client_name"),
            correlation_id=event.get("request_id"),
        )
        return self._publisher._publish_envelope(envelope, headers=headers)

    def publish_end_event(
        self,
        event_id: str,
        event: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        payload = _end_payload(event_id, event)
        envelope = build_envelope(
            EVENT_AI_REQUEST_END,
            payload,
            event_id=event_id,
            occurred_at=str(event.get("timestamp_start", "")),
            tenant_id=event.get("client_name"),
            correlation_id=event.get("request_id"),
        )
        return self._publisher._publish_envelope(envelope, headers=headers)

    def flush(self, timeout: float = 10.0) -> None:
        self._publisher.flush(timeout=timeout)

    def _publish_with_retry(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        event_type = payload.get("event_type") or payload.get("usage_event_type", "app.log")
        if event_type in {"start", "end"}:
            event_type = f"ai.request.{event_type}"
        return self._publisher.publish(event_type, payload, headers=headers)
