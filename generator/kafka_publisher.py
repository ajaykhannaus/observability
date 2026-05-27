"""Azure Event Hubs publisher via Kafka protocol.

In production (``ENVIRONMENT=prod``) the publisher REQUIRES Event Hubs
credentials and raises :class:`PublisherConfigError` at startup if any are
missing. The silent mock-mode fallback only activates when
``ALLOW_MOCK_MODE=true`` is set explicitly (intended for local development
and CI).
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

try:
    from confluent_kafka import KafkaException, Producer  # type: ignore

    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.warning("confluent-kafka not installed")


class PublisherConfigError(RuntimeError):
    """Raised when Event Hubs is not configured in a non-mock environment."""


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class KafkaPublisher:
    """Publishes paired START/END events to Azure Event Hubs via Kafka protocol.

    Production behaviour
    --------------------
    * Idempotent producer (``enable.idempotence=true``) with up to 5 retries
      and 500 ms backoff — protects against transient broker errors and
      partition leader changes during AKS / Event Hubs upgrades.
    * ``acks=all`` so every event is acknowledged by all in-sync replicas.
    * Snappy compression to reduce egress costs.
    * In ``ENVIRONMENT=prod``, missing credentials raise
      :class:`PublisherConfigError` instead of silently dropping events.
    """

    def __init__(self) -> None:
        self._producer: Any = None
        self._mock_mode = False
        self._topic: str = os.getenv("EVENTHUB_NAME", "ai-telemetry-events")
        self._setup()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        """True if the publisher has a working producer (or is in mock mode)."""
        return self._mock_mode or self._producer is not None

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        connection_string = os.getenv("EVENTHUB_CONNECTION_STRING", "").strip()
        namespace = os.getenv("EVENTHUB_NAMESPACE", "").strip()
        environment = os.getenv("ENVIRONMENT", "prod").strip().lower()
        allow_mock = _truthy(os.getenv("ALLOW_MOCK_MODE", "false"))

        missing_eh_config = not connection_string or not namespace

        if not _KAFKA_AVAILABLE or missing_eh_config:
            reason = (
                "confluent-kafka not installed"
                if not _KAFKA_AVAILABLE
                else "EVENTHUB_NAMESPACE / EVENTHUB_CONNECTION_STRING missing"
            )

            if environment == "prod" and not allow_mock:
                raise PublisherConfigError(
                    f"Refusing to start in mock mode: {reason}. "
                    "Configure Event Hubs or set ALLOW_MOCK_MODE=true "
                    "(non-prod environments only)."
                )

            logger.warning(
                "Publisher running in MOCK mode (%s). Events will be logged "
                "to stdout only and NOT delivered to Event Hubs.",
                reason,
            )
            self._mock_mode = True
            return

        bootstrap_server = f"{namespace}:9093"
        service_name = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry")
        conf: dict[str, Any] = {
            "bootstrap.servers":         bootstrap_server,
            "security.protocol":         "SASL_SSL",
            "sasl.mechanisms":           "PLAIN",
            "sasl.username":             "$ConnectionString",
            "sasl.password":             connection_string,
            "client.id":                 f"{service_name}-{uuid.uuid4().hex[:8]}",
            # ── Durability + reliability ──
            "acks":                      "all",
            "enable.idempotence":        True,
            "retries":                   5,
            "retry.backoff.ms":          500,
            "message.send.max.retries":  5,
            "delivery.timeout.ms":       30_000,
            "socket.timeout.ms":         10_000,
            # ── Throughput ──
            "linger.ms":                 50,
            "compression.type":          "snappy",
        }

        try:
            self._producer = Producer(conf)
            logger.info("Kafka producer connected to %s", bootstrap_server)
        except Exception as exc:
            if environment == "prod" and not allow_mock:
                raise PublisherConfigError(
                    f"Kafka producer init failed: {exc}"
                ) from exc
            logger.error(
                "Kafka producer init failed (%s) — falling back to mock mode", exc
            )
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
        # Application-level retry sits in front of librdkafka's own retry
        # to handle local-queue-full conditions during traffic spikes.
        for attempt in range(3):
            try:
                self._producer.produce(  # type: ignore[union-attr]
                    self._topic,
                    value=serialized,
                    callback=self._delivery_cb,
                )
                self._producer.poll(0)  # type: ignore[union-attr]
                return True
            except BufferError as exc:
                # local queue full — flush & retry with exponential backoff + jitter
                wait_s = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
                logger.warning(
                    "Producer queue full (%s); flushing and retrying in %.2fs (attempt %d/3)",
                    exc, wait_s, attempt + 1,
                )
                try:
                    self._producer.poll(1.0)  # type: ignore[union-attr]
                except Exception:
                    pass
                time.sleep(wait_s)
            except KafkaException as exc:  # type: ignore[name-defined]
                wait_s = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
                if attempt < 2:
                    logger.warning(
                        "Publish attempt %d failed (%s); retry in %.2fs",
                        attempt + 1, exc, wait_s,
                    )
                    time.sleep(wait_s)
                else:
                    logger.error(
                        "Permanent publish failure after %d attempts: %s",
                        attempt + 1, exc,
                    )
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish_start_event(self, event_id: str, event: dict[str, Any]) -> bool:
        """Emit the START event before the simulated LLM call."""
        payload: dict[str, Any] = {
            "event_id": event_id,
            "usage_event_type": "start",
            "service_name": os.getenv("OTEL_SERVICE_NAME", "ai-telemetry"),
            "timestamp": event["timestamp_start"],
            "user_email": event["user_email"],
            "client_name": event["client_name"],
            "project_id": event["project_id"],
            "auth_method": event["auth_method"],
            "operation_name": event["operation_name"],
            "model_name": event["model_name"],
            "model_provider": event["model_provider"],
            "streaming": event["streaming"],
        }
        return self._publish_with_retry(payload)

    def publish_end_event(self, event_id: str, event: dict[str, Any]) -> bool:
        """Emit the END event after the simulated LLM call."""
        payload: dict[str, Any] = {
            "event_id": event_id,
            "usage_event_type": "end",
            "service_name": os.getenv("OTEL_SERVICE_NAME", "ai-telemetry"),
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
