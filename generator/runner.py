"""
Continuous runner / batch executor for the AI telemetry POC.

Two usage modes:
  - Standalone:  python3 generator/runner.py
  - Library:     from generator.runner import run_one_batch  (used by Azure Function)
"""
from __future__ import annotations

import logging
import math
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

# Ensure the project root (parent of generator/) is on sys.path so absolute
# package imports work when runner.py is executed directly.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
except ImportError:
    pass

from generator.synthetic_generator import generate_event, traffic_multiplier  # noqa: E402
from generator.kafka_publisher import KafkaPublisher  # noqa: E402
import generator.otel_metrics as otel  # noqa: E402
import generator.pod_metrics_simulator as pod_sim  # noqa: E402
import generator.azure_logger as azure_logger  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

BATCH_INTERVAL_S: float = float(os.getenv("BATCH_INTERVAL_S", "5"))
BASE_BATCH_SIZE: int = int(os.getenv("BASE_BATCH_SIZE", "8"))
ERROR_WINDOW_PROB: float = float(os.getenv("ERROR_WINDOW_PROB", "0.03"))
ERROR_WINDOW_MIN_S: float = float(os.getenv("ERROR_WINDOW_MIN_S", "90"))
ERROR_WINDOW_MAX_S: float = float(os.getenv("ERROR_WINDOW_MAX_S", "180"))
SIMULATE_LATENCY: bool = os.getenv("SIMULATE_LATENCY", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_publisher: KafkaPublisher | None = None
_otel_ready: bool = False
_pod_sim_ready: bool = False
_running: bool = True

# Error window state (epoch seconds when the current window closes; 0 = no window)
_error_window_end: float = 0.0


def _get_publisher() -> KafkaPublisher:
    global _publisher
    if _publisher is None:
        _publisher = KafkaPublisher()
    return _publisher


def _ensure_otel() -> None:
    global _otel_ready
    if not _otel_ready:
        otel.setup_otel()
        _otel_ready = True


def _ensure_pod_sim() -> None:
    global _pod_sim_ready
    if not _pod_sim_ready:
        pod_sim.start_simulation()
        _pod_sim_ready = True


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------


def _batch_size() -> int:
    """Return a Poisson-ish batch size scaled by traffic multiplier."""
    raw = BASE_BATCH_SIZE * traffic_multiplier() + random.gauss(0, 1.0)
    return max(1, math.ceil(raw))


def _current_error_rate() -> float:
    """Return the active error rate, opening a new error window when the dice roll hits."""
    global _error_window_end
    now = time.monotonic()

    if now > _error_window_end:
        # Per-batch probability derived from per-minute probability
        per_batch_prob = ERROR_WINDOW_PROB / 60.0 * BATCH_INTERVAL_S
        if random.random() < per_batch_prob:
            duration = random.uniform(ERROR_WINDOW_MIN_S, ERROR_WINDOW_MAX_S)
            _error_window_end = now + duration
            logger.warning("Error window opened — duration %.0fs, elevated rate 8%%", duration)

    return 0.08 if now <= _error_window_end else 0.008


# ---------------------------------------------------------------------------
# Public batch entry point
# ---------------------------------------------------------------------------


def run_one_batch() -> dict[str, Any]:
    """Generate, publish, and record metrics for one batch of events.

    Returns a summary dict suitable for logging by the Azure Function.
    """
    _ensure_otel()
    _ensure_pod_sim()
    publisher = _get_publisher()

    batch_size = _batch_size()
    error_rate = _current_error_rate()

    successes = errors = 0
    total_cost = 0.0
    total_tokens = 0

    for _ in range(batch_size):
        try:
            event = generate_event(error_rate=error_rate)
            event_id = event["request_id"]

            publisher.publish_start_event(event_id, event)

            if SIMULATE_LATENCY:
                time.sleep(event["latency_ms"] / 1000.0)

            publisher.publish_end_event(event_id, event)
            otel.record_metrics(event)
            azure_logger.log_event(event)

            if event["status"] == "success":
                successes += 1
            else:
                errors += 1

            total_cost += event["cost_usd"]
            total_tokens += event["total_tokens"]

        except Exception as exc:
            logger.error("Event processing error: %s", exc)
            errors += 1

    try:
        publisher.flush()
    except Exception as exc:
        logger.error("Producer flush error: %s", exc)

    # Drive HPA scaling in the pod simulator based on current throughput
    pod_sim.update_load_signal(batch_size / BATCH_INTERVAL_S)

    summary: dict[str, Any] = {
        "batch_size": batch_size,
        "successes": successes,
        "errors": errors,
        "error_rate": round(errors / batch_size, 4) if batch_size else 0.0,
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "batch: size=%d success=%d error=%d cost=$%.5f tokens=%d",
        batch_size,
        successes,
        errors,
        total_cost,
        total_tokens,
    )
    return summary


# ---------------------------------------------------------------------------
# Signal handlers & local runner entry point
# ---------------------------------------------------------------------------


def _signal_handler(signum: int, _frame: Any) -> None:
    global _running
    logger.info("Signal %d received — shutting down after current batch", signum)
    _running = False


def main() -> None:
    # Must be first — installs JSON handler before any other logging call.
    # In Container Apps, stdout JSON lines are collected to Log Analytics.
    azure_logger.setup_structured_logging()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    azure_logger.log_startup_config({
        "batch_interval_s":  BATCH_INTERVAL_S,
        "base_batch_size":   BASE_BATCH_SIZE,
        "error_window_prob": ERROR_WINDOW_PROB,
        "simulate_latency":  SIMULATE_LATENCY,
        "prometheus_port":   int(os.getenv("PROMETHEUS_PORT", "0")),
        "otel_service_name": os.getenv("OTEL_SERVICE_NAME", "ai-telemetry-poc"),
        "environment":       os.getenv("ENVIRONMENT", "poc"),
        "eventhub_namespace": os.getenv("EVENTHUB_NAMESPACE", ""),
        "eventhub_name":     os.getenv("EVENTHUB_NAME", "ai-telemetry-events"),
    })

    logger.info(
        "Runner starting | interval=%.1fs | base_batch=%d | error_prob=%.2f%%",
        BATCH_INTERVAL_S,
        BASE_BATCH_SIZE,
        ERROR_WINDOW_PROB * 100,
    )

    while _running:
        tick = time.monotonic()
        try:
            run_one_batch()
        except Exception as exc:
            logger.error("Batch failed unexpectedly: %s", exc)

        elapsed = time.monotonic() - tick
        sleep_for = max(0.0, BATCH_INTERVAL_S - elapsed)
        if sleep_for > 0 and _running:
            time.sleep(sleep_for)

    logger.info("Runner stopped cleanly")


if __name__ == "__main__":
    main()
