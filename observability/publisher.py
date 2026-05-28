from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from typing import Any

from observability.envelope import build_envelope

logger = logging.getLogger(__name__)

try:
    from confluent_kafka import KafkaException, Producer  # type: ignore

    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.warning("confluent-kafka not installed")


class PublisherConfigError(RuntimeError):
    pass


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _serialise_headers(headers: dict[str, str] | None) -> list[tuple[str, bytes]] | None:
    if not headers:
        return None
    out: list[tuple[str, bytes]] = []
    for key, val in headers.items():
        if val is None or val == "":
            continue
        out.append((key, val.encode("utf-8") if isinstance(val, str) else val))
    return out or None


class EventHubPublisher:
    def __init__(self) -> None:
        self._producer: Any = None
        self._mock_mode = False
        self._topic = os.getenv("EVENTHUB_NAME", "observability-events")
        self._setup()

    @property
    def is_healthy(self) -> bool:
        return self._mock_mode or self._producer is not None

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    @property
    def queue_depth(self) -> int:
        if self._mock_mode or self._producer is None:
            return 0
        try:
            return int(len(self._producer))  # type: ignore[arg-type]
        except Exception:
            return 0

    def _setup(self) -> None:
        connection_string = os.getenv("EVENTHUB_CONNECTION_STRING", "").strip()
        namespace = os.getenv("EVENTHUB_NAMESPACE", "").strip()
        environment = os.getenv("ENVIRONMENT", "prod").strip().lower()
        allow_mock = _truthy(os.getenv("ALLOW_MOCK_MODE", "false"))
        missing = not connection_string or not namespace

        if not _KAFKA_AVAILABLE or missing:
            reason = (
                "confluent-kafka not installed"
                if not _KAFKA_AVAILABLE
                else "EVENTHUB_NAMESPACE / EVENTHUB_CONNECTION_STRING missing"
            )
            if environment == "prod" and not allow_mock:
                raise PublisherConfigError(
                    f"Refusing to start in mock mode: {reason}. "
                    "Configure Event Hubs or set ALLOW_MOCK_MODE=true."
                )
            logger.warning("EventHubPublisher mock mode (%s)", reason)
            self._mock_mode = True
            return

        bootstrap_server = f"{namespace}:9093"
        client_id = f"{os.getenv('OTEL_SERVICE_NAME', 'observability')}-{uuid.uuid4().hex[:8]}"
        conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap_server,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": "$ConnectionString",
            "sasl.password": connection_string,
            "client.id": client_id,
            "acks": "all",
            "enable.idempotence": True,
            "retries": 5,
            "retry.backoff.ms": 500,
            "message.send.max.retries": 5,
            "delivery.timeout.ms": 30_000,
            "socket.timeout.ms": 10_000,
            "linger.ms": 50,
            "compression.type": "snappy",
        }

        try:
            self._producer = Producer(conf)
            logger.info("EventHubPublisher connected to %s topic=%s", bootstrap_server, self._topic)
        except Exception as exc:
            if environment == "prod" and not allow_mock:
                raise PublisherConfigError(f"Kafka producer init failed: {exc}") from exc
            logger.error("EventHubPublisher init failed (%s) — mock mode", exc)
            self._mock_mode = True

    def _delivery_cb(self, err: Any, msg: Any) -> None:
        if err:
            logger.error("Delivery failed [%s]: %s", msg.topic() if msg else "?", err)

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        **envelope_kwargs: Any,
    ) -> bool:
        envelope = build_envelope(event_type, payload, **envelope_kwargs)
        return self._publish_envelope(envelope, headers=headers)

    def _publish_envelope(
        self,
        envelope: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        if self._mock_mode:
            tp = (headers or {}).get("traceparent", "-")
            logger.info(
                "[MOCK traceparent=%s] %s → %s",
                tp[:55],
                envelope.get("event_type"),
                json.dumps(envelope)[:160],
            )
            return True

        serialized = json.dumps(envelope).encode("utf-8")
        kafka_headers = _serialise_headers(headers)

        for attempt in range(3):
            try:
                self._producer.produce(  # type: ignore[union-attr]
                    self._topic,
                    value=serialized,
                    headers=kafka_headers,
                    callback=self._delivery_cb,
                )
                self._producer.poll(0)  # type: ignore[union-attr]
                return True
            except BufferError as exc:
                wait_s = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
                logger.warning("Producer queue full (%s); retry in %.2fs", exc, wait_s)
                try:
                    self._producer.poll(1.0)  # type: ignore[union-attr]
                except Exception:
                    pass
                time.sleep(wait_s)
            except KafkaException as exc:  # type: ignore[name-defined]
                wait_s = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
                if attempt < 2:
                    logger.warning("Publish attempt %d failed (%s)", attempt + 1, exc)
                    time.sleep(wait_s)
                else:
                    logger.error("Permanent publish failure: %s", exc)
        return False

    def flush(self, timeout: float = 10.0) -> None:
        if self._mock_mode or self._producer is None:
            return
        try:
            remaining = self._producer.flush(timeout)  # type: ignore[union-attr]
            if remaining:
                logger.warning("%d message(s) undelivered after flush", remaining)
        except Exception as exc:
            logger.error("Flush error: %s", exc)
