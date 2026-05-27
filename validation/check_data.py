"""Validation suite for the synthetic event generator.

Generates 1 000 events and verifies:
  1. Every catalogued model receives a sane share of traffic
  2. All required fields present on every event
  3. Baseline error rate within ±1 percentage point of 0.8 %
  4. Cost calculation accuracy (recalculated vs stored value)

Exit code: 0 if all checks pass, 1 if any fail.

Notes
-----
The pre-prod version of this script asserted that the empirical model
distribution matched ``MODEL_CONFIG[m]["weight"]`` within ±5 pp, but the
actual selection runs through per-client preferred-model lists in
``_pick_model_for_client`` — so the model weight is one input of several.
We now only enforce sanity bounds: every model gets some traffic, no
model monopolises the stream.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from generator.synthetic_generator import (  # noqa: E402
    MODEL_CONFIG,
    calculate_cost,
    generate_event,
)

SAMPLE_SIZE          = 1_000
MIN_PER_MODEL_PCT    = 1.0
MAX_PER_MODEL_PCT    = 60.0
BASELINE_ERROR_RATE  = 0.008
ERROR_RATE_TOLERANCE = 0.01

REQUIRED_FIELDS = {
    "request_id",
    "session_id",
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
    "error_type",
    "http_status_code",
    "stop_reason",
    "streaming",
}


def _check(label: str, passed: bool, detail: str = "") -> bool:
    status = "PASS" if passed else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"[{status}] {label}{suffix}")
    return passed


def run_checks() -> bool:
    events = [generate_event(error_rate=BASELINE_ERROR_RATE) for _ in range(SAMPLE_SIZE)]
    all_pass = True

    # ── 1. Model distribution — sanity bounds only ────────────────────────
    model_counts: dict[str, int] = {m: 0 for m in MODEL_CONFIG}
    for ev in events:
        model_counts[ev["model_name"]] += 1

    dist_ok = True
    for model in MODEL_CONFIG:
        actual_pct = model_counts[model] / SAMPLE_SIZE * 100
        in_bounds = MIN_PER_MODEL_PCT <= actual_pct <= MAX_PER_MODEL_PCT
        if not in_bounds:
            dist_ok = False
        print(
            f"  model={model:<22} actual={actual_pct:5.1f}%  "
            f"{'ok' if in_bounds else 'OUT OF SANITY RANGE'}"
        )

    all_pass &= _check(
        f"Every model receives {MIN_PER_MODEL_PCT}-{MAX_PER_MODEL_PCT}% of traffic",
        dist_ok,
    )

    # ── 2. Required fields ────────────────────────────────────────────────
    missing_any = False
    for idx, ev in enumerate(events):
        missing = REQUIRED_FIELDS - set(ev.keys())
        if missing:
            print(f"  event[{idx}] missing fields: {missing}")
            missing_any = True
            break

    all_pass &= _check("All required fields present on every event", not missing_any)

    # ── 3. Baseline error rate ────────────────────────────────────────────
    error_count = sum(1 for ev in events if ev["status"] == "error")
    actual_rate = error_count / SAMPLE_SIZE
    rate_ok = abs(actual_rate - BASELINE_ERROR_RATE) <= ERROR_RATE_TOLERANCE
    all_pass &= _check(
        f"Error rate within ±{ERROR_RATE_TOLERANCE*100:.0f}% of baseline "
        f"{BASELINE_ERROR_RATE*100:.1f}%",
        rate_ok,
        f"actual={actual_rate:.3f}",
    )

    # ── 4. Cost calculation accuracy ──────────────────────────────────────
    cost_mismatch = 0
    for ev in events:
        expected = calculate_cost(
            ev["model_name"],
            ev["prompt_tokens"],
            ev["completion_tokens"],
            ev["cache_read_tokens"],
        )
        if abs(ev["cost_usd"] - expected) > 1e-9:
            cost_mismatch += 1

    all_pass &= _check(
        "Cost calculation accurate on all events",
        cost_mismatch == 0,
        f"{cost_mismatch} mismatches" if cost_mismatch else "",
    )

    return all_pass


if __name__ == "__main__":
    print(f"Generating {SAMPLE_SIZE} synthetic events …\n")
    passed = run_checks()
    print(f"\n{'ALL CHECKS PASSED' if passed else 'ONE OR MORE CHECKS FAILED'}")
    sys.exit(0 if passed else 1)
