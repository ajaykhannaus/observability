"""Synthetic LLM request event generator.

Produces events structurally identical to real AI gateway traffic so that
switching to live traffic requires only changing the event source.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Model catalogue
# ---------------------------------------------------------------------------

MODEL_CONFIG: dict[str, dict[str, Any]] = {
    "claude-haiku-3-5": {
        "weight": 0.61,
        "provider": "anthropic",
        "latency_mean": 620.0,
        "latency_std": 180.0,
        "prompt_tokens_mean": 420.0,
        "prompt_tokens_std": 120.0,
        "completion_tokens_mean": 180.0,
        "completion_tokens_std": 60.0,
        "cache_read_tokens_mean": 85.0,
        "cost_input_per_m": 0.80,
        "cost_output_per_m": 4.00,
        "cost_cache_per_m": 0.08,
    },
    "claude-sonnet-4-5": {
        "weight": 0.24,
        "provider": "anthropic",
        "latency_mean": 1400.0,
        "latency_std": 420.0,
        "prompt_tokens_mean": 680.0,
        "prompt_tokens_std": 200.0,
        "completion_tokens_mean": 310.0,
        "completion_tokens_std": 100.0,
        "cache_read_tokens_mean": 140.0,
        "cost_input_per_m": 3.00,
        "cost_output_per_m": 15.00,
        "cost_cache_per_m": 0.30,
    },
    "gpt-4o": {
        "weight": 0.10,
        "provider": "openai",
        "latency_mean": 2100.0,
        "latency_std": 680.0,
        "prompt_tokens_mean": 820.0,
        "prompt_tokens_std": 250.0,
        "completion_tokens_mean": 420.0,
        "completion_tokens_std": 130.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 5.00,
        "cost_output_per_m": 15.00,
        "cost_cache_per_m": 0.00,
    },
    "claude-opus-4-6": {
        "weight": 0.05,
        "provider": "anthropic",
        "latency_mean": 3800.0,
        "latency_std": 920.0,
        "prompt_tokens_mean": 1100.0,
        "prompt_tokens_std": 320.0,
        "completion_tokens_mean": 580.0,
        "completion_tokens_std": 180.0,
        "cache_read_tokens_mean": 220.0,
        "cost_input_per_m": 15.00,
        "cost_output_per_m": 75.00,
        "cost_cache_per_m": 1.50,
    },
}

CLIENT_TEAMS: list[tuple[str, float]] = [
    ("healthcare-portal", 0.20),
    ("dev-agency", 0.18),
    ("ecommerce-brand", 0.16),
    ("legal-firm", 0.15),
    ("financial-svc", 0.14),
    ("internal-tools", 0.10),
    ("data-science", 0.07),
]

OPERATIONS: list[tuple[str, float]] = [
    ("chat_completion", 0.34),
    ("code_generation", 0.28),
    ("summarisation", 0.22),
    ("text_generation", 0.16),
]

AUTH_METHODS: list[str] = ["api_key", "jwt_apigee", "jwt_azure_ad"]
STOP_REASONS: list[str] = ["stop", "max_tokens", "stop_sequence"]
ERROR_TYPES: list[str] = ["timeout", "rate_limit", "model_unavailable"]
_USER_DOMAINS: list[str] = ["acme.com", "healthcare.org", "dev.io", "legal.net", "finco.com"]

# Traffic multiplier indexed by UTC hour
TRAFFIC_BY_HOUR: list[float] = [
    0.20, 0.15, 0.12, 0.10, 0.10, 0.15,  # 00-05
    0.30, 0.55, 0.80, 0.95, 1.00, 1.00,  # 06-11
    0.95, 0.90, 0.90, 0.85, 0.80, 0.75,  # 12-17
    0.65, 0.55, 0.45, 0.38, 0.30, 0.25,  # 18-23
]

_MODEL_NAMES: list[str] = list(MODEL_CONFIG.keys())
_MODEL_WEIGHTS: list[float] = [MODEL_CONFIG[m]["weight"] for m in _MODEL_NAMES]
_CLIENT_NAMES: list[str] = [t[0] for t in CLIENT_TEAMS]
_CLIENT_WEIGHTS: list[float] = [t[1] for t in CLIENT_TEAMS]
_OP_NAMES: list[str] = [o[0] for o in OPERATIONS]
_OP_WEIGHTS: list[float] = [o[1] for o in OPERATIONS]


def traffic_multiplier() -> float:
    """Return the traffic scaling factor for the current UTC hour."""
    return TRAFFIC_BY_HOUR[datetime.now(timezone.utc).hour]


def _clamp_positive(value: float) -> int:
    return max(1, int(round(value)))


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
) -> float:
    """Return request cost in USD from token counts and per-model pricing."""
    cfg = MODEL_CONFIG[model]
    return round(
        prompt_tokens * cfg["cost_input_per_m"] / 1_000_000
        + completion_tokens * cfg["cost_output_per_m"] / 1_000_000
        + cache_read_tokens * cfg["cost_cache_per_m"] / 1_000_000,
        8,
    )


def generate_event(error_rate: float = 0.008) -> dict[str, Any]:
    """Return one synthetic LLM request event dict."""
    model_name: str = random.choices(_MODEL_NAMES, weights=_MODEL_WEIGHTS, k=1)[0]
    cfg = MODEL_CONFIG[model_name]

    client_name: str = random.choices(_CLIENT_NAMES, weights=_CLIENT_WEIGHTS, k=1)[0]
    operation_name: str = random.choices(_OP_NAMES, weights=_OP_WEIGHTS, k=1)[0]

    latency_ms = max(50.0, random.gauss(cfg["latency_mean"], cfg["latency_std"]))
    prompt_tokens = _clamp_positive(
        random.gauss(cfg["prompt_tokens_mean"], cfg["prompt_tokens_std"])
    )
    completion_tokens = _clamp_positive(
        random.gauss(cfg["completion_tokens_mean"], cfg["completion_tokens_std"])
    )
    cache_mean = cfg["cache_read_tokens_mean"]
    cache_read_tokens = (
        _clamp_positive(random.gauss(cache_mean, cache_mean * 0.3))
        if cache_mean > 0
        else 0
    )
    total_tokens = prompt_tokens + completion_tokens + cache_read_tokens

    is_error = random.random() < error_rate
    status = "error" if is_error else "success"
    error_type: str | None = random.choice(ERROR_TYPES) if is_error else None
    http_status_code = random.choice([429, 504, 503]) if is_error else 200
    stop_reason: str | None = None if is_error else random.choice(STOP_REASONS)
    data_quality = (
        "full" if not is_error else random.choice(["full", "partial"])
    )

    cost_usd = calculate_cost(model_name, prompt_tokens, completion_tokens, cache_read_tokens)
    user_email = f"user-{random.randint(1000, 9999)}@{random.choice(_USER_DOMAINS)}"

    return {
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "user_email": user_email,
        "client_name": client_name,
        "project_id": f"proj-{client_name}-{random.randint(100, 999)}",
        "auth_method": random.choice(AUTH_METHODS),
        "operation_name": operation_name,
        "model_name": model_name,
        "model_provider": cfg["provider"],
        "timestamp_start": datetime.now(timezone.utc).isoformat(),
        "latency_ms": round(latency_ms, 2),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "status": status,
        "error_type": error_type,
        "http_status_code": http_status_code,
        "stop_reason": stop_reason,
        "streaming": random.random() < 0.4,
        "data_quality": data_quality,
    }
