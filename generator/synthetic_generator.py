"""Synthetic LLM request event generator — v2.

Produces events structurally identical to real AI gateway traffic.
New in v2:
  - 6 models incl. GPT-4o-mini and Gemini 1.5 Flash
  - 7 rich client profiles (SLA tier, daily budget, preferred models, domain ops)
  - 4 regions with per-region diurnal traffic patterns
  - Persistent session threads (multi-turn conversations)
  - Anomaly injection (rate-limit storm, model degradation, budget exhaustion, cascade)
  - Model routing decisions (cost-opt, capability, fallback, user-pinned)
  - SLA breach detection per request
  - Data classification tags (PHI, PII, confidential, internal)
"""
from __future__ import annotations

import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Model catalogue — pricing in USD per million tokens
# ---------------------------------------------------------------------------

MODEL_CONFIG: dict[str, dict[str, Any]] = {
    "claude-haiku-3-5": {
        "provider":               "anthropic",
        "weight":                 0.38,
        "latency_mean_ms":        620.0,
        "latency_std_ms":         180.0,
        "prompt_tokens_mean":     420.0,
        "prompt_tokens_std":      120.0,
        "completion_tokens_mean": 180.0,
        "completion_tokens_std":  60.0,
        "cache_read_tokens_mean": 85.0,
        "cost_input_per_m":       0.80,
        "cost_output_per_m":      4.00,
        "cost_cache_per_m":       0.08,
        "capability_tier":        "fast",
        "supports_streaming":     True,
    },
    "claude-sonnet-4-5": {
        "provider":               "anthropic",
        "weight":                 0.22,
        "latency_mean_ms":        1_400.0,
        "latency_std_ms":         420.0,
        "prompt_tokens_mean":     680.0,
        "prompt_tokens_std":      200.0,
        "completion_tokens_mean": 310.0,
        "completion_tokens_std":  100.0,
        "cache_read_tokens_mean": 140.0,
        "cost_input_per_m":       3.00,
        "cost_output_per_m":      15.00,
        "cost_cache_per_m":       0.30,
        "capability_tier":        "balanced",
        "supports_streaming":     True,
    },
    "claude-opus-4-6": {
        "provider":               "anthropic",
        "weight":                 0.05,
        "latency_mean_ms":        3_800.0,
        "latency_std_ms":         920.0,
        "prompt_tokens_mean":     1_100.0,
        "prompt_tokens_std":      320.0,
        "completion_tokens_mean": 580.0,
        "completion_tokens_std":  180.0,
        "cache_read_tokens_mean": 220.0,
        "cost_input_per_m":       15.00,
        "cost_output_per_m":      75.00,
        "cost_cache_per_m":       1.50,
        "capability_tier":        "premium",
        "supports_streaming":     True,
    },
    "gpt-4o": {
        "provider":               "openai",
        "weight":                 0.10,
        "latency_mean_ms":        2_100.0,
        "latency_std_ms":         680.0,
        "prompt_tokens_mean":     820.0,
        "prompt_tokens_std":      250.0,
        "completion_tokens_mean": 420.0,
        "completion_tokens_std":  130.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m":       5.00,
        "cost_output_per_m":      15.00,
        "cost_cache_per_m":       0.00,
        "capability_tier":        "balanced",
        "supports_streaming":     True,
    },
    "gpt-4o-mini": {
        "provider":               "openai",
        "weight":                 0.16,
        "latency_mean_ms":        480.0,
        "latency_std_ms":         130.0,
        "prompt_tokens_mean":     380.0,
        "prompt_tokens_std":      110.0,
        "completion_tokens_mean": 160.0,
        "completion_tokens_std":  50.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m":       0.15,
        "cost_output_per_m":      0.60,
        "cost_cache_per_m":       0.00,
        "capability_tier":        "fast",
        "supports_streaming":     True,
    },
    "gemini-1.5-flash": {
        "provider":               "google",
        "weight":                 0.09,
        "latency_mean_ms":        390.0,
        "latency_std_ms":         110.0,
        "prompt_tokens_mean":     510.0,
        "prompt_tokens_std":      150.0,
        "completion_tokens_mean": 200.0,
        "completion_tokens_std":  65.0,
        "cache_read_tokens_mean": 0.0,
        "cost_input_per_m":       0.075,
        "cost_output_per_m":      0.30,
        "cost_cache_per_m":       0.00,
        "capability_tier":        "fast",
        "supports_streaming":     False,
    },
}

# ---------------------------------------------------------------------------
# Client profiles — realistic enterprise teams
# ---------------------------------------------------------------------------

CLIENT_PROFILES: dict[str, dict[str, Any]] = {
    "healthcare-portal": {
        "weight":            0.20,
        "sla_tier":          "premium",       # p95 latency target ms
        "p95_latency_ms":    2_000,
        "daily_budget_usd":  150.0,
        "preferred_models":  ["claude-sonnet-4-5", "claude-haiku-3-5"],
        "fallback_model":    "gpt-4o-mini",
        "operations":        {
            "clinical_note_analysis": 0.35,
            "summarisation":          0.30,
            "chat_completion":        0.25,
            "code_generation":        0.10,
        },
        "region_weights":    {"us-east-1": 0.60, "us-west-2": 0.40},
        "data_class":        "phi",
        "avg_session_turns": 3,
        "user_count":        200,
    },
    "legal-firm": {
        "weight":            0.15,
        "sla_tier":          "premium",
        "p95_latency_ms":    4_000,
        "daily_budget_usd":  200.0,
        "preferred_models":  ["claude-opus-4-6", "claude-sonnet-4-5"],
        "fallback_model":    "claude-sonnet-4-5",
        "operations":        {
            "contract_review":       0.45,
            "summarisation":         0.35,
            "chat_completion":       0.20,
        },
        "region_weights":    {"us-east-1": 0.80, "eu-west-1": 0.20},
        "data_class":        "confidential",
        "avg_session_turns": 5,
        "user_count":        80,
    },
    "ecommerce-brand": {
        "weight":            0.18,
        "sla_tier":          "standard",
        "p95_latency_ms":    1_500,
        "daily_budget_usd":  90.0,
        "preferred_models":  ["gpt-4o-mini", "claude-haiku-3-5"],
        "fallback_model":    "gemini-1.5-flash",
        "operations":        {
            "product_description":   0.40,
            "chat_completion":       0.35,
            "text_generation":       0.25,
        },
        "region_weights":    {"us-east-1": 0.40, "eu-west-1": 0.35, "ap-southeast-1": 0.25},
        "data_class":        "pii",
        "avg_session_turns": 2,
        "user_count":        5000,
    },
    "financial-svc": {
        "weight":            0.14,
        "sla_tier":          "premium",
        "p95_latency_ms":    1_000,
        "daily_budget_usd":  180.0,
        "preferred_models":  ["claude-sonnet-4-5", "gpt-4o"],
        "fallback_model":    "claude-haiku-3-5",
        "operations":        {
            "risk_assessment":       0.30,
            "report_generation":     0.30,
            "chat_completion":       0.25,
            "summarisation":         0.15,
        },
        "region_weights":    {"us-east-1": 0.50, "eu-west-1": 0.50},
        "data_class":        "confidential",
        "avg_session_turns": 2,
        "user_count":        150,
    },
    "dev-agency": {
        "weight":            0.16,
        "sla_tier":          "standard",
        "p95_latency_ms":    3_000,
        "daily_budget_usd":  60.0,
        "preferred_models":  ["gpt-4o", "claude-sonnet-4-5"],
        "fallback_model":    "gpt-4o-mini",
        "operations":        {
            "code_generation":       0.55,
            "code_review":           0.30,
            "chat_completion":       0.15,
        },
        "region_weights":    {"us-east-1": 0.30, "us-west-2": 0.40, "eu-west-1": 0.30},
        "data_class":        "internal",
        "avg_session_turns": 4,
        "user_count":        300,
    },
    "internal-tools": {
        "weight":            0.10,
        "sla_tier":          "basic",
        "p95_latency_ms":    5_000,
        "daily_budget_usd":  30.0,
        "preferred_models":  ["claude-haiku-3-5", "gpt-4o-mini", "gemini-1.5-flash"],
        "fallback_model":    "gemini-1.5-flash",
        "operations":        {
            "chat_completion":       0.50,
            "summarisation":         0.30,
            "text_generation":       0.20,
        },
        "region_weights":    {"us-east-1": 1.00},
        "data_class":        "internal",
        "avg_session_turns": 2,
        "user_count":        500,
    },
    "data-science": {
        "weight":            0.07,
        "sla_tier":          "standard",
        "p95_latency_ms":    8_000,
        "daily_budget_usd":  40.0,
        "preferred_models":  ["claude-opus-4-6", "gpt-4o"],
        "fallback_model":    "claude-sonnet-4-5",
        "operations":        {
            "data_analysis":         0.45,
            "code_generation":       0.35,
            "summarisation":         0.20,
        },
        "region_weights":    {"us-east-1": 0.60, "us-west-2": 0.40},
        "data_class":        "internal",
        "avg_session_turns": 6,
        "user_count":        60,
    },
}

# ---------------------------------------------------------------------------
# Regions — with UTC hour offsets for realistic diurnal traffic
# ---------------------------------------------------------------------------

REGIONS: dict[str, dict[str, Any]] = {
    "us-east-1": {
        "tz_offset_h":  -5,
        "global_weight": 0.45,
        "az":           "us-east-1a",
    },
    "us-west-2": {
        "tz_offset_h":  -8,
        "global_weight": 0.20,
        "az":           "us-west-2b",
    },
    "eu-west-1": {
        "tz_offset_h":  +1,
        "global_weight": 0.25,
        "az":           "eu-west-1a",
    },
    "ap-southeast-1": {
        "tz_offset_h":  +8,
        "global_weight": 0.10,
        "az":           "ap-southeast-1a",
    },
}

# Traffic multiplier by local hour (0-23)
_HOURLY_TRAFFIC: list[float] = [
    0.10, 0.08, 0.06, 0.05, 0.05, 0.10,   # 00-05  night
    0.25, 0.50, 0.80, 0.95, 1.00, 1.00,   # 06-11  morning ramp
    0.95, 0.90, 0.90, 0.88, 0.85, 0.78,   # 12-17  afternoon
    0.65, 0.52, 0.40, 0.30, 0.22, 0.15,   # 18-23  evening wind-down
]

# ---------------------------------------------------------------------------
# Anomaly state — module-level singletons
# ---------------------------------------------------------------------------

_anomaly_state: dict[str, Any] = {
    "degraded_model":       None,   # model name currently degraded
    "degraded_until":       0.0,    # monotonic epoch
    "rate_limited_client":  None,   # client being rate-limited
    "rate_limit_until":     0.0,
    "cascade_active":       False,
    "cascade_until":        0.0,
}

# Daily spend tracker per client  {client_name: float}
_daily_spend: dict[str, float] = defaultdict(float)
_spend_date: str = ""   # YYYY-MM-DD; resets on date change


# Per-client active sessions  {client_name: [session_id, ...]}
_active_sessions: dict[str, list[str]] = defaultdict(list)
_session_turn_counts: dict[str, int] = defaultdict(int)  # session_id → turns so far

# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

ERROR_TAXONOMY: dict[str, dict[str, Any]] = {
    "rate_limit":        {"http": 429, "retryable": True,  "category": "throttling"},
    "timeout":           {"http": 504, "retryable": True,  "category": "availability"},
    "model_unavailable": {"http": 503, "retryable": True,  "category": "availability"},
    "context_length":    {"http": 400, "retryable": False, "category": "input_validation"},
    "auth_failure":      {"http": 401, "retryable": False, "category": "auth"},
    "bad_request":       {"http": 400, "retryable": False, "category": "input_validation"},
    "internal_error":    {"http": 500, "retryable": True,  "category": "server"},
}

STOP_REASONS = ["stop", "max_tokens", "stop_sequence", "tool_use"]
AUTH_METHODS = ["api_key", "jwt_apigee", "jwt_azure_ad", "mtls"]
ROUTING_REASONS = [
    "cost_optimised",    # cheapest model that meets quality bar
    "capability_match",  # model matches task complexity
    "user_pinned",       # user explicitly requested this model
    "fallback",          # primary model unavailable
    "load_balanced",     # distributed across equivalent models
    "latency_optimised", # fastest model for SLA tier
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _local_hour_for_region(region: str) -> int:
    offset = REGIONS[region]["tz_offset_h"]
    return (datetime.now(timezone.utc).hour + offset) % 24


def _traffic_multiplier_for_region(region: str) -> float:
    return _HOURLY_TRAFFIC[_local_hour_for_region(region)]


def traffic_multiplier() -> float:
    """Weighted-average traffic multiplier across all active regions."""
    total = 0.0
    for name, cfg in REGIONS.items():
        total += _traffic_multiplier_for_region(name) * cfg["global_weight"]
    return total


def _clamp_positive(val: float) -> int:
    return max(1, int(round(val)))


def _reset_daily_spend_if_needed() -> None:
    global _spend_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _spend_date:
        _daily_spend.clear()
        _spend_date = today


def _pick_region_for_client(client_name: str) -> str:
    profile = CLIENT_PROFILES[client_name]
    rw = profile["region_weights"]
    return random.choices(list(rw.keys()), weights=list(rw.values()), k=1)[0]


def _pick_model_for_client(client_name: str, anomaly_model: str | None) -> tuple[str, str]:
    """Return (model_name, routing_reason).

    Respects client preferred_models list, budget state, anomaly state.
    """
    profile = CLIENT_PROFILES[client_name]
    preferred = profile["preferred_models"]
    fallback = profile["fallback_model"]

    # Budget exhausted → force cheapest model
    _reset_daily_spend_if_needed()
    budget = profile["daily_budget_usd"]
    if _daily_spend[client_name] >= budget * 0.95:
        cheap = min(preferred, key=lambda m: MODEL_CONFIG[m]["cost_input_per_m"])
        return cheap, "cost_optimised"

    # Anomaly: primary model degraded → fallback
    if anomaly_model and anomaly_model in preferred:
        return fallback, "fallback"

    # 10 % chance user pins to a specific model explicitly
    if random.random() < 0.10:
        return random.choice(preferred), "user_pinned"

    # 20 % chance latency SLA is tight → pick fastest
    if profile["sla_tier"] == "premium" and random.random() < 0.20:
        fastest = min(preferred, key=lambda m: MODEL_CONFIG[m]["latency_mean_ms"])
        return fastest, "latency_optimised"

    # Default: weighted choice among preferred
    weights = [MODEL_CONFIG[m]["weight"] for m in preferred]
    chosen = random.choices(preferred, weights=weights, k=1)[0]
    reason = random.choice(["capability_match", "load_balanced"])
    return chosen, reason


def _get_or_create_session(client_name: str, avg_turns: int) -> tuple[str, int]:
    """Return (session_id, turn_number). Creates or continues an existing session."""
    sessions = _active_sessions[client_name]

    # Reuse an existing session 60 % of the time if one exists
    if sessions and random.random() < 0.60:
        sid = random.choice(sessions)
        _session_turn_counts[sid] += 1
        turn = _session_turn_counts[sid]
        # Close session when it reaches avg_turns * 2
        if turn >= avg_turns * 2:
            sessions.remove(sid)
            del _session_turn_counts[sid]
        return sid, turn

    # New session
    sid = str(uuid.uuid4())
    sessions.append(sid)
    if len(sessions) > 50:           # cap per-client active sessions
        old = sessions.pop(0)
        _session_turn_counts.pop(old, None)
    _session_turn_counts[sid] = 1
    return sid, 1


def _apply_anomaly(
    model_name: str,
    client_name: str,
    base_latency_ms: float,
    base_error_rate: float,
) -> tuple[float, float]:
    """Return (adjusted_latency_ms, adjusted_error_rate) under active anomalies."""
    import time as _time
    now = _time.monotonic()
    state = _anomaly_state
    latency = base_latency_ms
    err_rate = base_error_rate

    # Model degradation → 3× latency spike on affected model
    if state["degraded_model"] == model_name and now < state["degraded_until"]:
        latency *= 3.0
        err_rate = min(err_rate * 4, 0.40)

    # Rate-limit storm on a specific client → many 429s
    if state["rate_limited_client"] == client_name and now < state["rate_limit_until"]:
        err_rate = min(err_rate + 0.50, 0.85)

    # Cascade failure → all models elevated error rate
    if state["cascade_active"] and now < state["cascade_until"]:
        err_rate = min(err_rate + 0.20, 0.60)
        latency *= 1.5
    elif state["cascade_active"] and now >= state["cascade_until"]:
        state["cascade_active"] = False

    return latency, err_rate


def _maybe_inject_anomaly() -> None:
    """Randomly open new anomaly windows (called once per batch in runner)."""
    import time as _time
    now = _time.monotonic()
    state = _anomaly_state
    model_names = list(MODEL_CONFIG.keys())
    client_names = list(CLIENT_PROFILES.keys())

    # 0.5 % chance per call to start model degradation (lasts 2-5 min)
    if state["degraded_model"] is None or now >= state["degraded_until"]:
        if random.random() < 0.005:
            state["degraded_model"] = random.choice(model_names)
            state["degraded_until"] = now + random.uniform(120, 300)

    # 0.3 % chance to start a rate-limit storm on a client (lasts 1-3 min)
    if state["rate_limited_client"] is None or now >= state["rate_limit_until"]:
        if random.random() < 0.003:
            state["rate_limited_client"] = random.choice(client_names)
            state["rate_limit_until"] = now + random.uniform(60, 180)

    # 0.1 % chance of cascade failure (lasts 30-90 s)
    if not state["cascade_active"] or now >= state["cascade_until"]:
        if random.random() < 0.001:
            state["cascade_active"] = True
            state["cascade_until"] = now + random.uniform(30, 90)


# ---------------------------------------------------------------------------
# Cost calculator
# ---------------------------------------------------------------------------

def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
) -> float:
    cfg = MODEL_CONFIG[model]
    return round(
        prompt_tokens     * cfg["cost_input_per_m"]  / 1_000_000
        + completion_tokens * cfg["cost_output_per_m"] / 1_000_000
        + cache_read_tokens * cfg["cost_cache_per_m"]  / 1_000_000,
        8,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_CLIENT_NAMES:   list[str]   = list(CLIENT_PROFILES.keys())
_CLIENT_WEIGHTS: list[float] = [p["weight"] for p in CLIENT_PROFILES.values()]


def maybe_inject_anomaly() -> None:
    """Call once per batch from runner to advance anomaly state machine."""
    _maybe_inject_anomaly()


def get_anomaly_summary() -> dict[str, Any]:
    """Return current anomaly state for logging/metrics."""
    import time as _time
    now = _time.monotonic()
    s = _anomaly_state
    return {
        "degraded_model":      s["degraded_model"] if now < s["degraded_until"] else None,
        "rate_limited_client": s["rate_limited_client"] if now < s["rate_limit_until"] else None,
        "cascade_active":      s["cascade_active"] and now < s["cascade_until"],
    }


def get_client_budget_status() -> dict[str, dict[str, float]]:
    """Return {client: {spent, budget, pct}} for all clients."""
    _reset_daily_spend_if_needed()
    result = {}
    for name, profile in CLIENT_PROFILES.items():
        spent = _daily_spend[name]
        budget = profile["daily_budget_usd"]
        result[name] = {
            "spent_usd":  round(spent, 4),
            "budget_usd": budget,
            "pct":        round(spent / budget * 100, 1),
        }
    return result


def generate_event(error_rate: float = 0.008) -> dict[str, Any]:
    """Return one rich synthetic LLM request event."""

    # ── Client & region ──────────────────────────────────────────────────
    client_name: str = random.choices(_CLIENT_NAMES, weights=_CLIENT_WEIGHTS, k=1)[0]
    profile = CLIENT_PROFILES[client_name]
    region = _pick_region_for_client(client_name)

    # ── Model routing ────────────────────────────────────────────────────
    degraded = _anomaly_state.get("degraded_model")
    model_name, routing_reason = _pick_model_for_client(client_name, degraded)
    cfg = MODEL_CONFIG[model_name]

    # ── Session & turn ───────────────────────────────────────────────────
    session_id, turn_number = _get_or_create_session(client_name, profile["avg_session_turns"])

    # ── Operation ────────────────────────────────────────────────────────
    ops = profile["operations"]
    operation_name: str = random.choices(list(ops.keys()), weights=list(ops.values()), k=1)[0]

    # ── Token generation ─────────────────────────────────────────────────
    # Longer conversations accumulate context → more prompt tokens per turn
    context_factor = 1.0 + (turn_number - 1) * 0.15
    prompt_tokens = _clamp_positive(
        random.gauss(cfg["prompt_tokens_mean"] * context_factor, cfg["prompt_tokens_std"])
    )
    completion_tokens = _clamp_positive(
        random.gauss(cfg["completion_tokens_mean"], cfg["completion_tokens_std"])
    )
    cache_mean = cfg["cache_read_tokens_mean"] * min(context_factor, 3.0)
    cache_read_tokens = (
        _clamp_positive(random.gauss(cache_mean, cache_mean * 0.3))
        if cache_mean > 0 else 0
    )
    total_tokens = prompt_tokens + completion_tokens + cache_read_tokens

    # ── Latency (region adds ~50-150 ms of network jitter) ───────────────
    network_jitter = random.uniform(50, 150) if region != "us-east-1" else 0.0
    base_latency = max(50.0, random.gauss(cfg["latency_mean_ms"], cfg["latency_std_ms"]))
    adjusted_latency, adjusted_error_rate = _apply_anomaly(
        model_name, client_name, base_latency + network_jitter, error_rate
    )
    latency_ms = round(adjusted_latency, 2)

    # ── SLA breach ───────────────────────────────────────────────────────
    sla_target_ms = profile["p95_latency_ms"]
    sla_breached = latency_ms > sla_target_ms

    # ── Error simulation ─────────────────────────────────────────────────
    is_error = random.random() < adjusted_error_rate
    if is_error:
        # Rate-limit storm → bias toward 429
        if _anomaly_state["rate_limited_client"] == client_name:
            error_type = "rate_limit"
        # Cascade / degradation → bias toward timeout or unavailable
        elif _anomaly_state["cascade_active"] or _anomaly_state["degraded_model"] == model_name:
            error_type = random.choice(["timeout", "model_unavailable", "internal_error"])
        else:
            error_type = random.choices(
                list(ERROR_TAXONOMY.keys()),
                weights=[3, 3, 2, 1, 0.5, 0.5, 1],
                k=1,
            )[0]
        err_info = ERROR_TAXONOMY[error_type]
        status = "error"
        http_status_code = err_info["http"]
        stop_reason = None
        is_retried = err_info["retryable"] and random.random() < 0.40
        retry_count = random.randint(1, 3) if is_retried else 0
    else:
        error_type = None
        err_info = {}
        status = "success"
        http_status_code = 200
        stop_reason = random.choice(STOP_REASONS)
        is_retried = False
        retry_count = 0

    # ── Cost & budget tracking ───────────────────────────────────────────
    cost_usd = calculate_cost(model_name, prompt_tokens, completion_tokens, cache_read_tokens)
    _reset_daily_spend_if_needed()
    _daily_spend[client_name] += cost_usd
    budget_exhausted = _daily_spend[client_name] >= profile["daily_budget_usd"]

    # ── Streaming ────────────────────────────────────────────────────────
    streaming = cfg["supports_streaming"] and random.random() < 0.40

    # ── User identity (stable within a session) ──────────────────────────
    user_id = f"u-{hash(session_id) % profile['user_count']:05d}"
    user_domain = {
        "healthcare-portal": "health.org",
        "legal-firm":        "legalco.net",
        "ecommerce-brand":   "shop.io",
        "financial-svc":     "finco.com",
        "dev-agency":        "dev.io",
        "internal-tools":    "acme.internal",
        "data-science":      "ds.acme.com",
    }[client_name]
    user_email = f"{user_id}@{user_domain}"

    return {
        # ── Identity ─────────────────────────────────────────────────────
        "request_id":        str(uuid.uuid4()),
        "session_id":        session_id,
        "turn_number":       turn_number,
        "user_id":           user_id,
        "user_email":        user_email,
        "client_name":       client_name,
        "project_id":        f"proj-{client_name[:4]}-{abs(hash(session_id)) % 900 + 100}",
        "auth_method":       random.choice(AUTH_METHODS),
        "data_classification": profile["data_class"],

        # ── Routing ──────────────────────────────────────────────────────
        "model_name":        model_name,
        "model_provider":    cfg["provider"],
        "capability_tier":   cfg["capability_tier"],
        "routing_reason":    routing_reason,

        # ── Request ──────────────────────────────────────────────────────
        "operation_name":    operation_name,
        "region":            region,
        "availability_zone": REGIONS[region]["az"],
        "timestamp_start":   datetime.now(timezone.utc).isoformat(),
        "streaming":         streaming,

        # ── Performance ──────────────────────────────────────────────────
        "latency_ms":        latency_ms,
        "sla_target_ms":     sla_target_ms,
        "sla_tier":          profile["sla_tier"],
        "sla_breached":      sla_breached,

        # ── Tokens & cost ────────────────────────────────────────────────
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens":      total_tokens,
        "cost_usd":          cost_usd,
        "daily_spend_usd":   round(_daily_spend[client_name], 6),
        "budget_usd":        profile["daily_budget_usd"],
        "budget_exhausted":  budget_exhausted,

        # ── Outcome ──────────────────────────────────────────────────────
        "status":            status,
        "http_status_code":  http_status_code,
        "stop_reason":       stop_reason,
        "error_type":        error_type,
        "error_category":    err_info.get("category"),
        "is_retried":        is_retried,
        "retry_count":       retry_count,
    }
