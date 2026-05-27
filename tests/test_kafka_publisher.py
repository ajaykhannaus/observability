"""Tests for the Kafka publisher's prod-mode safety rail."""
from __future__ import annotations

import os

import pytest

from generator.kafka_publisher import KafkaPublisher, PublisherConfigError


def _clear_eventhub_env(monkeypatch):
    for key in (
        "EVENTHUB_CONNECTION_STRING",
        "EVENTHUB_NAMESPACE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_prod_without_credentials_raises(monkeypatch):
    """ENVIRONMENT=prod + no Event Hubs config must fail fast."""
    _clear_eventhub_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("ALLOW_MOCK_MODE", "false")

    with pytest.raises(PublisherConfigError):
        KafkaPublisher()


def test_prod_with_allow_mock_falls_back(monkeypatch):
    """If ALLOW_MOCK_MODE=true is set explicitly, mock is permitted in prod."""
    _clear_eventhub_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("ALLOW_MOCK_MODE", "true")

    pub = KafkaPublisher()
    assert pub.mock_mode is True
    assert pub.is_healthy is True


def test_dev_without_credentials_falls_back(monkeypatch):
    """Non-prod environments may run in mock mode without ALLOW_MOCK_MODE."""
    _clear_eventhub_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("ALLOW_MOCK_MODE", raising=False)

    pub = KafkaPublisher()
    assert pub.mock_mode is True


def test_mock_publish_returns_true(monkeypatch):
    _clear_eventhub_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("ALLOW_MOCK_MODE", "true")

    pub = KafkaPublisher()
    payload = {"hello": "world"}
    assert pub._publish_with_retry(payload) is True


def test_flush_is_noop_in_mock(monkeypatch):
    _clear_eventhub_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("ALLOW_MOCK_MODE", "true")
    pub = KafkaPublisher()
    # Should not raise
    pub.flush(timeout=0.1)
