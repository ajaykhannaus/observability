"""Unit tests for the synthetic event generator."""
from __future__ import annotations

import pytest

from generator.synthetic_generator import (
    CLIENT_PROFILES,
    MODEL_CONFIG,
    REGIONS,
    calculate_cost,
    generate_event,
    get_anomaly_summary,
    get_client_budget_status,
    maybe_inject_anomaly,
    traffic_multiplier,
)

REQUIRED_FIELDS = {
    "request_id",
    "session_id",
    "user_id",
    "user_email",
    "client_name",
    "project_id",
    "auth_method",
    "operation_name",
    "model_name",
    "model_provider",
    "timestamp_start",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "total_tokens",
    "cost_usd",
    "status",
    "http_status_code",
    "streaming",
    "data_classification",
    "sla_target_ms",
    "sla_breached",
}


def test_required_fields_present():
    """Every event must include the fields downstream consumers rely on."""
    for _ in range(50):
        ev = generate_event()
        missing = REQUIRED_FIELDS - set(ev.keys())
        assert not missing, f"missing fields: {missing}"


def test_cost_matches_token_counts():
    for _ in range(200):
        ev = generate_event()
        expected = calculate_cost(
            ev["model_name"],
            ev["prompt_tokens"],
            ev["completion_tokens"],
            ev["cache_read_tokens"],
        )
        assert abs(ev["cost_usd"] - expected) < 1e-9


@pytest.mark.parametrize("model", list(MODEL_CONFIG.keys()))
def test_cost_zero_for_zero_tokens(model: str):
    assert calculate_cost(model, 0, 0, 0) == 0.0


def test_status_consistency():
    for _ in range(200):
        ev = generate_event(error_rate=0.5)
        if ev["status"] == "success":
            assert ev["http_status_code"] == 200
            assert ev["error_type"] is None
            assert ev["stop_reason"] is not None
        else:
            assert ev["status"] == "error"
            assert ev["http_status_code"] != 200
            assert ev["error_type"] is not None
            assert ev["stop_reason"] is None


def test_traffic_multiplier_in_unit_range():
    val = traffic_multiplier()
    assert 0.0 <= val <= 1.2  # weighted across regions, never exceeds 1


def test_client_and_model_known():
    for _ in range(50):
        ev = generate_event()
        assert ev["client_name"] in CLIENT_PROFILES
        assert ev["model_name"] in MODEL_CONFIG
        assert ev["region"] in REGIONS


def test_budget_status_shape():
    status = get_client_budget_status()
    assert set(status.keys()) == set(CLIENT_PROFILES.keys())
    for entry in status.values():
        assert {"spent_usd", "budget_usd", "pct"} <= set(entry.keys())


def test_anomaly_state_machine_idempotent():
    maybe_inject_anomaly()
    summary = get_anomaly_summary()
    assert {"degraded_model", "rate_limited_client", "cascade_active"} <= set(summary.keys())
