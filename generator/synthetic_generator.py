"""Synthetic LLM request event generator.

Produces events structurally identical to real AI gateway traffic so that
switching to live traffic requires only changing the event source.

Model / provider distribution:
  OpenAI 40% | Cohere 28% | Mistral 17% | Anthropic 10% | HuggingFace 5%

Error system:
  13 specific error types across 6 categories.
  Each error type carries its own HTTP status, latency behaviour, and
  a realistic provider error message so dashboards can slice by both
  high-level category and granular type.
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
    # ── OpenAI (40%) ──────────────────────────────────────────────────────
    "gpt-4o-mini": {
        "weight": 0.25, "provider": "openai",
        "latency_mean": 1320.0, "latency_std": 380.0,
        "prompt_tokens_mean": 580.0, "prompt_tokens_std": 160.0,
        "completion_tokens_mean": 240.0, "completion_tokens_std": 80.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 0.15, "cost_output_per_m": 0.60, "cost_cache_per_m": 0.0,
    },
    "gpt-4o": {
        "weight": 0.15, "provider": "openai",
        "latency_mean": 2460.0, "latency_std": 680.0,
        "prompt_tokens_mean": 820.0, "prompt_tokens_std": 250.0,
        "completion_tokens_mean": 420.0, "completion_tokens_std": 130.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 5.00, "cost_output_per_m": 15.00, "cost_cache_per_m": 0.0,
    },
    # ── Cohere (28%) ──────────────────────────────────────────────────────
    "command-r-plus": {
        "weight": 0.28, "provider": "cohere",
        "latency_mean": 1500.0, "latency_std": 420.0,
        "prompt_tokens_mean": 650.0, "prompt_tokens_std": 190.0,
        "completion_tokens_mean": 280.0, "completion_tokens_std": 90.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 3.00, "cost_output_per_m": 15.00, "cost_cache_per_m": 0.0,
    },
    # ── Anthropic (10%) ───────────────────────────────────────────────────
    "claude-3-haiku": {
        "weight": 0.07, "provider": "anthropic",
        "latency_mean": 1780.0, "latency_std": 480.0,
        "prompt_tokens_mean": 520.0, "prompt_tokens_std": 150.0,
        "completion_tokens_mean": 220.0, "completion_tokens_std": 70.0,
        "cache_read_tokens_mean": 90.0,
        "cost_input_per_m": 0.25, "cost_output_per_m": 1.25, "cost_cache_per_m": 0.03,
    },
    "claude-3-opus": {
        "weight": 0.03, "provider": "anthropic",
        "latency_mean": 2930.0, "latency_std": 780.0,
        "prompt_tokens_mean": 980.0, "prompt_tokens_std": 290.0,
        "completion_tokens_mean": 490.0, "completion_tokens_std": 150.0,
        "cache_read_tokens_mean": 200.0,
        "cost_input_per_m": 15.00, "cost_output_per_m": 75.00, "cost_cache_per_m": 1.50,
    },
    # ── Mistral (17%) ─────────────────────────────────────────────────────
    "mistral-large": {
        "weight": 0.10, "provider": "mistral",
        "latency_mean": 1210.0, "latency_std": 330.0,
        "prompt_tokens_mean": 590.0, "prompt_tokens_std": 170.0,
        "completion_tokens_mean": 250.0, "completion_tokens_std": 80.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 4.00, "cost_output_per_m": 12.00, "cost_cache_per_m": 0.0,
    },
    "mixtral-8x7b": {
        "weight": 0.07, "provider": "mistral",
        "latency_mean": 870.0, "latency_std": 240.0,
        "prompt_tokens_mean": 480.0, "prompt_tokens_std": 140.0,
        "completion_tokens_mean": 200.0, "completion_tokens_std": 65.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 0.60, "cost_output_per_m": 0.60, "cost_cache_per_m": 0.0,
    },
    # ── HuggingFace (5%) ──────────────────────────────────────────────────
    "llama-3-70b": {
        "weight": 0.05, "provider": "huggingface",
        "latency_mean": 1050.0, "latency_std": 290.0,
        "prompt_tokens_mean": 420.0, "prompt_tokens_std": 120.0,
        "completion_tokens_mean": 180.0, "completion_tokens_std": 60.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m": 0.59, "cost_output_per_m": 0.79, "cost_cache_per_m": 0.0,
    },
}

# ---------------------------------------------------------------------------
# Error catalogue — 13 types across 6 categories
#
# latency_mode:
#   "timeout"  → latency near/beyond the provider's timeout ceiling (~15-45 s)
#   "fast"     → request rejected immediately (<300 ms)
#   "medium"   → provider responds with error after partial processing (0.5-3 s)
#
# retry_after_s: only meaningful for rate_limit; -1 means not applicable.
# ---------------------------------------------------------------------------

ERROR_CATALOGUE: dict[str, dict[str, Any]] = {
    # ── Timeouts ──────────────────────────────────────────────────────────
    "request_timeout": {
        "category":     "timeout",
        "http_status":  408,
        "weight":       0.10,
        "latency_mode": "timeout",
        "retry_after_s": -1,
        "messages": [
            "Request timed out after 30s waiting for model response",
            "Upstream model did not respond within the configured timeout",
            "Operation timed out: no data received within 30000ms",
        ],
    },
    "gateway_timeout": {
        "category":     "timeout",
        "http_status":  504,
        "weight":       0.08,
        "latency_mode": "timeout",
        "retry_after_s": -1,
        "messages": [
            "504 Gateway Timeout: upstream LLM provider did not respond in time",
            "Gateway timeout after 60s — provider may be under heavy load",
        ],
    },
    "read_timeout": {
        "category":     "timeout",
        "http_status":  504,
        "weight":       0.04,
        "latency_mode": "timeout",
        "retry_after_s": -1,
        "messages": [
            "Read timeout: response stream interrupted after partial tokens received",
            "Stream stalled — no new tokens for 30s, connection closed",
        ],
    },

    # ── Rate limits (429) ─────────────────────────────────────────────────
    "rate_limit_tokens": {
        "category":     "rate_limit",
        "http_status":  429,
        "weight":       0.18,
        "latency_mode": "fast",
        "retry_after_s": 20,  # typical TPM retry window
        "messages": [
            "Rate limit exceeded: 90000 TPM limit reached, retry after 20s",
            "Too many tokens per minute. Current usage exceeds quota.",
            "tokens_per_minute rate limit hit — reduce request frequency",
        ],
    },
    "rate_limit_requests": {
        "category":     "rate_limit",
        "http_status":  429,
        "weight":       0.14,
        "latency_mode": "fast",
        "retry_after_s": 5,
        "messages": [
            "Rate limit exceeded: 3500 RPM limit reached, retry after 5s",
            "Too many requests. Please back off and retry.",
            "requests_per_minute quota exhausted for this API key",
        ],
    },
    "quota_exceeded": {
        "category":     "rate_limit",
        "http_status":  429,
        "weight":       0.06,
        "latency_mode": "fast",
        "retry_after_s": 3600,  # hourly / daily quota
        "messages": [
            "Monthly token quota of 10B tokens exceeded — upgrade plan or wait for reset",
            "Billing hard limit reached: $500 spend cap hit this month",
            "Daily quota exceeded. Resets at 00:00 UTC.",
        ],
    },

    # ── Provider / model errors ───────────────────────────────────────────
    "model_unavailable": {
        "category":     "provider_error",
        "http_status":  503,
        "weight":       0.10,
        "latency_mode": "medium",
        "retry_after_s": 30,
        "messages": [
            "Service unavailable: model endpoint is temporarily down (503)",
            "The model is currently unavailable. Please try again shortly.",
            "503 Service Unavailable — provider is performing maintenance",
        ],
    },
    "model_overloaded": {
        "category":     "provider_error",
        "http_status":  529,          # Anthropic overload code
        "weight":       0.08,
        "latency_mode": "medium",
        "retry_after_s": 10,
        "messages": [
            "Overloaded: the model is currently receiving too many requests",
            "529 API overloaded — exponential backoff recommended",
            "System at capacity. Request queued and rejected after 10s.",
        ],
    },
    "server_error": {
        "category":     "provider_error",
        "http_status":  500,
        "weight":       0.06,
        "latency_mode": "medium",
        "retry_after_s": -1,
        "messages": [
            "Internal server error on the model provider side (500)",
            "500 Internal Server Error — provider logged incident #P2-4821",
            "Unexpected error in inference engine. Request could not be completed.",
        ],
    },

    # ── Invalid request errors ────────────────────────────────────────────
    "context_length_exceeded": {
        "category":     "invalid_request",
        "http_status":  400,
        "weight":       0.06,
        "latency_mode": "fast",
        "retry_after_s": -1,
        "messages": [
            "Context length exceeded: prompt (18432 tokens) > model limit (16384)",
            "Input too long: reduce prompt by at least 2048 tokens",
            "maximum_context_length exceeded — truncate your input",
        ],
    },
    "content_policy_violation": {
        "category":     "invalid_request",
        "http_status":  400,
        "weight":       0.04,
        "latency_mode": "fast",
        "retry_after_s": -1,
        "messages": [
            "Request blocked: content policy violation detected in prompt",
            "Output filtered: response triggered safety classifier",
            "400 Bad Request: prompt contains disallowed content category",
        ],
    },

    # ── Authentication errors ─────────────────────────────────────────────
    "authentication_error": {
        "category":     "auth_error",
        "http_status":  401,
        "weight":       0.03,
        "latency_mode": "fast",
        "retry_after_s": -1,
        "messages": [
            "401 Unauthorized: invalid or expired API key",
            "Authentication failed — check your API key and org ID",
            "Incorrect API key provided. Rotate credentials and retry.",
        ],
    },

    # ── Network / infrastructure errors ───────────────────────────────────
    "connection_error": {
        "category":     "network_error",
        "http_status":  0,            # no HTTP response received
        "weight":       0.03,
        "latency_mode": "fast",
        "retry_after_s": -1,
        "messages": [
            "Connection refused: could not reach provider endpoint",
            "Network error: SSL handshake failed (TLS certificate mismatch)",
            "DNS resolution failed for api.openai.com",
            "Max retries exceeded with url: /v1/chat/completions",
        ],
    },
}

# Pre-compute for random.choices
_ERR_NAMES:    list[str]   = list(ERROR_CATALOGUE.keys())
_ERR_WEIGHTS:  list[float] = [ERROR_CATALOGUE[e]["weight"] for e in _ERR_NAMES]

# ---------------------------------------------------------------------------
# Traffic / routing catalogues
# ---------------------------------------------------------------------------

OPERATIONS: list[tuple[str, float]] = [
    ("chat_completion",   0.38),
    ("generate_summary",  0.22),
    ("embeddings",        0.15),
    ("classify_text",     0.11),
    ("translate_text",    0.07),
    ("rerank_documents",  0.04),
    ("moderation_check",  0.03),
]

CLIENT_TEAMS: list[tuple[str, float]] = [
    ("healthcare-portal", 0.20),
    ("dev-agency",        0.18),
    ("ecommerce-brand",   0.16),
    ("legal-firm",        0.15),
    ("financial-svc",     0.14),
    ("internal-tools",    0.10),
    ("data-science",      0.07),
]

ENVIRONMENTS: list[tuple[str, float]] = [
    ("staging",     0.50),
    ("production",  0.35),
    ("development", 0.15),
]

REGIONS: list[tuple[str, float]] = [
    ("us-west-2",      0.40),
    ("us-east-1",      0.30),
    ("eu-west-1",      0.20),
    ("ap-southeast-1", 0.10),
]

AUTH_METHODS: list[str] = ["api_key", "jwt_apigee", "jwt_azure_ad"]
STOP_REASONS: list[str] = ["stop", "max_tokens", "stop_sequence"]
SERVICES:     list[str] = ["ai-gateway", "api-service", "batch-processor"]
_USER_DOMAINS: list[str] = ["acme.com", "healthcare.org", "dev.io", "legal.net", "finco.com"]

# Traffic multiplier by UTC hour
TRAFFIC_BY_HOUR: list[float] = [
    0.20, 0.15, 0.12, 0.10, 0.10, 0.15,
    0.30, 0.55, 0.80, 0.95, 1.00, 1.00,
    0.95, 0.90, 0.90, 0.85, 0.80, 0.75,
    0.65, 0.55, 0.45, 0.38, 0.30, 0.25,
]

# Pre-computed pick lists
_MODEL_NAMES:    list[str]   = list(MODEL_CONFIG.keys())
_MODEL_WEIGHTS:  list[float] = [MODEL_CONFIG[m]["weight"]  for m in _MODEL_NAMES]
_CLIENT_NAMES:   list[str]   = [t[0] for t in CLIENT_TEAMS]
_CLIENT_WEIGHTS: list[float] = [t[1] for t in CLIENT_TEAMS]
_OP_NAMES:       list[str]   = [o[0] for o in OPERATIONS]
_OP_WEIGHTS:     list[float] = [o[1] for o in OPERATIONS]
_ENV_NAMES:      list[str]   = [e[0] for e in ENVIRONMENTS]
_ENV_WEIGHTS:    list[float] = [e[1] for e in ENVIRONMENTS]
_REG_NAMES:      list[str]   = [r[0] for r in REGIONS]
_REG_WEIGHTS:    list[float] = [r[1] for r in REGIONS]


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
        prompt_tokens       * cfg["cost_input_per_m"]  / 1_000_000
        + completion_tokens * cfg["cost_output_per_m"] / 1_000_000
        + cache_read_tokens * cfg["cost_cache_per_m"]  / 1_000_000,
        8,
    )


def _error_latency(err: dict[str, Any], model_mean: float) -> float:
    """Return a realistic latency (ms) for a given error type."""
    mode = err["latency_mode"]
    if mode == "timeout":
        # Simulate hitting a 15-45 s timeout ceiling
        return max(model_mean * 2.5, random.gauss(25_000, 8_000))
    if mode == "fast":
        # Provider rejects immediately — fast path
        return max(10.0, random.gauss(80, 40))
    # "medium" — provider starts processing, then fails
    return max(200.0, random.gauss(900, 400))


def generate_event(error_rate: float = 0.008) -> dict[str, Any]:
    """Return one synthetic LLM request event dict."""
    model_name: str = random.choices(_MODEL_NAMES, weights=_MODEL_WEIGHTS, k=1)[0]
    cfg = MODEL_CONFIG[model_name]

    client_name:    str = random.choices(_CLIENT_NAMES, weights=_CLIENT_WEIGHTS, k=1)[0]
    operation_name: str = random.choices(_OP_NAMES,     weights=_OP_WEIGHTS,     k=1)[0]
    environment:    str = random.choices(_ENV_NAMES,    weights=_ENV_WEIGHTS,    k=1)[0]
    region:         str = random.choices(_REG_NAMES,    weights=_REG_WEIGHTS,    k=1)[0]
    service:        str = random.choice(SERVICES)

    is_error = random.random() < error_rate

    # ── Error details ────────────────────────────────────────────────────
    if is_error:
        err_type: str           = random.choices(_ERR_NAMES, weights=_ERR_WEIGHTS, k=1)[0]
        err_def: dict[str, Any] = ERROR_CATALOGUE[err_type]
        error_category: str | None  = err_def["category"]
        http_status_code: int       = err_def["http_status"]
        error_message: str | None   = random.choice(err_def["messages"])
        retry_after_s: int | None   = (
            err_def["retry_after_s"] if err_def["retry_after_s"] >= 0 else None
        )
        latency_ms = _error_latency(err_def, cfg["latency_mean"])
        stop_reason: str | None = None
        status = "error"
        data_quality = random.choice(["full", "partial"])

        # Errors produce fewer/no tokens depending on type
        if err_def["latency_mode"] == "timeout":
            # Partial tokens may have been produced before the timeout
            prompt_tokens     = _clamp_positive(random.gauss(cfg["prompt_tokens_mean"],     cfg["prompt_tokens_std"]))
            completion_tokens = _clamp_positive(random.gauss(cfg["completion_tokens_mean"] * 0.3, 30))
            cache_read_tokens = 0
        elif err_def["latency_mode"] == "fast":
            # Rejected before any processing — no tokens billed
            prompt_tokens     = 0
            completion_tokens = 0
            cache_read_tokens = 0
        else:
            # medium — prompt was processed but output failed
            prompt_tokens     = _clamp_positive(random.gauss(cfg["prompt_tokens_mean"], cfg["prompt_tokens_std"]))
            completion_tokens = 0
            cache_read_tokens = 0
    else:
        err_type          = None   # type: ignore[assignment]
        error_category    = None
        http_status_code  = 200
        error_message     = None
        retry_after_s     = None
        latency_ms        = max(50.0, random.gauss(cfg["latency_mean"], cfg["latency_std"]))
        stop_reason       = random.choice(STOP_REASONS)
        status            = "success"
        data_quality      = "full"
        prompt_tokens     = _clamp_positive(random.gauss(cfg["prompt_tokens_mean"],     cfg["prompt_tokens_std"]))
        completion_tokens = _clamp_positive(random.gauss(cfg["completion_tokens_mean"], cfg["completion_tokens_std"]))
        cache_mean        = cfg["cache_read_tokens_mean"]
        cache_read_tokens = (
            _clamp_positive(random.gauss(cache_mean, cache_mean * 0.3)) if cache_mean > 0 else 0
        )

    total_tokens = prompt_tokens + completion_tokens + cache_read_tokens
    cost_usd     = calculate_cost(model_name, prompt_tokens, completion_tokens, cache_read_tokens)
    user_email   = f"user-{random.randint(1000, 9999)}@{random.choice(_USER_DOMAINS)}"

    return {
        # ── Identity ──────────────────────────────────────────────────────
        "request_id":        str(uuid.uuid4()),
        "session_id":        str(uuid.uuid4()),
        "service":           service,
        "environment":       environment,
        "region":            region,
        "user_email":        user_email,
        "client_name":       client_name,
        "project_id":        f"proj-{client_name}-{random.randint(100, 999)}",
        "auth_method":       random.choice(AUTH_METHODS),
        # ── Routing ───────────────────────────────────────────────────────
        "operation_name":    operation_name,
        "model_name":        model_name,
        "model_provider":    cfg["provider"],
        "streaming":         random.random() < 0.4,
        # ── Timing ────────────────────────────────────────────────────────
        "timestamp_start":   datetime.now(timezone.utc).isoformat(),
        "latency_ms":        round(latency_ms, 2),
        # ── Tokens & cost ─────────────────────────────────────────────────
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens":      total_tokens,
        "cost_usd":          cost_usd,
        # ── Outcome ───────────────────────────────────────────────────────
        "status":            status,
        "stop_reason":       stop_reason,
        "http_status_code":  http_status_code,
        "data_quality":      data_quality,
        # ── Error details (None on success) ───────────────────────────────
        "error_type":        err_type,
        "error_category":    error_category,
        "error_message":     error_message,
        "retry_after_s":     retry_after_s,
    }
