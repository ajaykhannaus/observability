"""Azure Event Hubs publisher via Kafka protocol.

Falls back to mock mode (local logging) when Event Hubs credentials are
absent or confluent-kafka is not installed, so the runner works without
any Azure connectivity.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

try:
    from confluent_kafka import Producer, KafkaException  # type: ignore

    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.warning("confluent-kafka not installed — publisher in mock mode")


class KafkaPublisher:
    """Publishes paired START/END events to Azure Event Hubs via Kafka protocol."""

    def __init__(self) -> None:
        self._producer: Any = None
        self._mock_mode = False
        self._topic: str = os.getenv("EVENTHUB_NAME", "ai-telemetry-events")
        self._setup()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        connection_string = os.getenv("EVENTHUB_CONNECTION_STRING", "").strip()
        namespace = os.getenv("EVENTHUB_NAMESPACE", "").strip()

        if not _KAFKA_AVAILABLE or not connection_string or not namespace:
            logger.warning(
                "EventHub not configured (EVENTHUB_NAMESPACE / EVENTHUB_CONNECTION_STRING"
                " missing) — publisher in mock mode"
            )
            self._mock_mode = True
            return

        bootstrap_server = f"{namespace}:9093"
        conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap_server,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": "$ConnectionString",
            "sasl.password": connection_string,
            "client.id": f"ai-telemetry-poc-{uuid.uuid4().hex[:8]}",
            "acks": "all",
            "retries": 0,  # manual retry below
            "socket.timeout.ms": 10000,
        }

        try:
            self._producer = Producer(conf)
            logger.info("Kafka producer connected to %s", bootstrap_server)
        except Exception as exc:
            logger.error("Kafka producer init failed (%s) — falling back to mock mode", exc)
            self._mock_mode = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _delivery_cb(self, err: Any, msg: Any) -> None:
        if err:
            logger.error("Delivery failed [%s]: %s", msg.topic() if msg else "?", err)
        else:
            logger.debug(
                "Delivered → %s [p%d] offset %d",
                msg.topic(),
                msg.partition(),
                msg.offset(),
            )

    def _publish_with_retry(self, payload: dict[str, Any]) -> bool:
        if self._mock_mode:
            logger.info("[MOCK] → %s", json.dumps(payload)[:140])
            return True

        serialized = json.dumps(payload).encode("utf-8")
        for attempt in range(3):
            try:
                self._producer.produce(  # type: ignore[union-attr]
                    self._topic,
                    value=serialized,
                    callback=self._delivery_cb,
                )
                self._producer.poll(0)  # type: ignore[union-attr]
                return True
            except KafkaException as exc:  # type: ignore[name-defined]
                wait_s = (attempt + 1) * 2
                if attempt < 2:
                    logger.warning(
                        "Publish attempt %d failed (%s); retry in %ds", attempt + 1, exc, wait_s
                    )
                    time.sleep(wait_s)
                else:
                    logger.error(
                        "Permanent publish failure after %d attempts: %s", attempt + 1, exc
                    )
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish_start_event(self, event_id: str, event: dict[str, Any]) -> bool:
        """Emit the START event before the simulated LLM call."""
        payload: dict[str, Any] = {
            "event_id":        event_id,
            "usage_event_type":"start",
            "service_name":    os.getenv("OTEL_SERVICE_NAME", "ai-telemetry-poc"),
            "service":         event.get("service", "ai-gateway"),
            "environment":     event.get("environment", "poc"),
            "region":          event.get("region", "us-east-1"),
            "timestamp":       event["timestamp_start"],
            "user_email":      event["user_email"],
            "client_name":     event["client_name"],
            "project_id":      event["project_id"],
            "auth_method":     event["auth_method"],
            "operation_name":  event["operation_name"],
            "model_name":      event["model_name"],
            "model_provider":  event["model_provider"],
            "streaming":       event["streaming"],
        }
        return self._publish_with_retry(payload)

    def publish_end_event(self, event_id: str, event: dict[str, Any]) -> bool:
        """Emit the END event after the simulated LLM call."""
        payload: dict[str, Any] = {
            "event_id": event_id,
            "usage_event_type": "end",
            "service_name": os.getenv("OTEL_SERVICE_NAME", "ai-telemetry-poc"),
            "timestamp": event["timestamp_start"],
            "user_email": event["user_email"],
            "client_name": event["client_name"],
            "project_id": event["project_id"],
            "auth_method": event["auth_method"],
            "operation_name": event["operation_name"],
            "model_name": event["model_name"],
            "model_provider": event["model_provider"],
            "streaming": event["streaming"],
            "latency_ms": event["latency_ms"],
            "prompt_tokens": event["prompt_tokens"],
            "completion_tokens": event["completion_tokens"],
            "cache_read_tokens": event["cache_read_tokens"],
            "total_tokens": event["total_tokens"],
            "cost_usd": event["cost_usd"],
            "status": event["status"],
            "error_type": event["error_type"],
            "stop_reason": event["stop_reason"],
            "http_status_code": event["http_status_code"],
            "data_quality": event["data_quality"],
        }
        return self._publish_with_retry(payload)

    def flush(self, timeout: float = 10.0) -> None:
        """Block until all enqueued messages are delivered."""
        if self._mock_mode or self._producer is None:
            return
        try:
            remaining = self._producer.flush(timeout)  # type: ignore[union-attr]
            if remaining:
                logger.warning("%d message(s) undelivered after flush", remaining)
        except Exception as exc:
            logger.error("Flush error: %s", exc)
