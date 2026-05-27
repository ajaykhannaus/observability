"""Continuous runner / batch executor for the AI telemetry pipeline.

Two usage modes:
  - Standalone:  python3 -m generator.runner
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

import generator.azure_logger as azure_logger  # noqa: E402
import generator.health_server as health_server  # noqa: E402
import generator.otel_metrics as otel  # noqa: E402
import generator.otel_tracing as tracing  # noqa: E402
import generator.pod_metrics_simulator as pod_sim  # noqa: E402
import generator.semantic_conventions as sc  # noqa: E402
from generator.evaluator import get_evaluator  # noqa: E402
from generator.kafka_publisher import KafkaPublisher, PublisherConfigError  # noqa: E402
from generator.pii_scanner import scan as pii_scan  # noqa: E402
from generator.prompt_logger import log_prompt  # noqa: E402
from generator.synthetic_generator import (  # noqa: E402
    generate_event,
    get_anomaly_summary,
    get_client_budget_status,
    maybe_inject_anomaly,
    traffic_multiplier,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

BATCH_INTERVAL_S: float = float(os.getenv("BATCH_INTERVAL_S", "5"))
BASE_BATCH_SIZE: int    = int(os.getenv("BASE_BATCH_SIZE", "8"))
ERROR_WINDOW_PROB: float = float(os.getenv("ERROR_WINDOW_PROB", "0.03"))
ERROR_WINDOW_MIN_S: float = float(os.getenv("ERROR_WINDOW_MIN_S", "90"))
ERROR_WINDOW_MAX_S: float = float(os.getenv("ERROR_WINDOW_MAX_S", "180"))
SIMULATE_LATENCY: bool = os.getenv("SIMULATE_LATENCY", "false").lower() == "true"
HEALTH_PORT: int       = int(os.getenv("HEALTH_PORT", "8080"))

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_publisher: KafkaPublisher | None = None
_otel_ready: bool = False
_pod_sim_ready: bool = False
_tracing_ready: bool = False
_running: bool = True

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


def _ensure_tracing() -> None:
    global _tracing_ready
    if not _tracing_ready:
        tracing.setup_tracing()
        _tracing_ready = True


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
        per_batch_prob = ERROR_WINDOW_PROB / 60.0 * BATCH_INTERVAL_S
        if random.random() < per_batch_prob:
            duration = random.uniform(ERROR_WINDOW_MIN_S, ERROR_WINDOW_MAX_S)
            _error_window_end = now + duration
            logger.warning(
                "Error window opened — duration %.0fs, elevated rate 8%%", duration,
            )

    return 0.08 if now <= _error_window_end else 0.008


def _publish_event(publisher: KafkaPublisher, event: dict[str, Any]) -> tuple[bool, bool]:
    """Publish START + END for one event with publish-path spans + traceparent.

    Returns ``(start_ok, end_ok)``. The current span context is captured into
    a W3C ``traceparent`` Kafka header so downstream consumers can continue
    the trace.
    """
    event_id = event["request_id"]
    headers = {"traceparent": tracing.current_traceparent() or ""}

    with tracing.publish_span("start"):
        start_ok = publisher.publish_start_event(event_id, event, headers=headers)

    if SIMULATE_LATENCY:
        time.sleep(event["latency_ms"] / 1000.0)

    with tracing.publish_span("end"):
        end_ok = publisher.publish_end_event(event_id, event, headers=headers)

    return start_ok, end_ok


def _record_latency_phases(event: dict[str, Any]) -> None:
    """Open one short-lived child span per latency phase. Spans carry the
    declared duration as an attribute; we don't synchronously sleep because
    the runner is processing many events per batch.
    """
    with tracing.phase_span(sc.SPAN_QUEUE_WAIT, event.get("queue_wait_ms", 0.0)):
        pass
    with tracing.phase_span(sc.SPAN_MODEL_INFERENCE, event.get("model_inference_ms", 0.0)):
        pass
    if event.get("streaming"):
        with tracing.phase_span(sc.SPAN_FIRST_TOKEN, event.get("first_token_ms", 0.0)):
            pass
        with tracing.phase_span(sc.SPAN_STREAM_RESPONSE, event.get("stream_response_ms", 0.0)):
            pass


# ---------------------------------------------------------------------------
# Public batch entry point
# ---------------------------------------------------------------------------


def run_one_batch() -> dict[str, Any]:
    """Generate, publish, and record metrics for one batch of events."""
    _ensure_otel()
    _ensure_tracing()
    _ensure_pod_sim()
    publisher = _get_publisher()

    batch_size = _batch_size()
    error_rate = _current_error_rate()

    maybe_inject_anomaly()

    successes = errors = 0
    sla_breaches = 0
    total_cost = 0.0
    total_tokens = 0
    publish_errors = 0

    batch_started = time.monotonic()

    with tracing.batch_span() as batch_span:
        for _ in range(batch_size):
            try:
                event = generate_event(error_rate=error_rate)
                with tracing.request_span(event):
                    _record_latency_phases(event)
                    start_ok, end_ok = _publish_event(publisher, event)
                    otel.record_metrics(event)
                    azure_logger.log_event(event)

                    # ── Safety + audit (FR-003, FR-014, FR-012) ──────────
                    # Synthetic events don't carry real prompt/response text.
                    # The hooks below are no-ops until real text is supplied;
                    # they exercise the code path so it's ready for the real
                    # gateway cutover described in docs/improvement-plan.md §6.
                    _prompt_text   = event.get("prompt_text")
                    _response_text = event.get("response_text")
                    if _prompt_text or _response_text:
                        from generator.pii_scanner import scan_event_fields
                        ev_scanned = scan_event_fields(event)
                        log_prompt(
                            ev_scanned,
                            prompt_text=_prompt_text,
                            response_text=_response_text,
                            prompt_pii=ev_scanned.get("prompt_pii"),
                            response_pii=ev_scanned.get("response_pii"),
                        )
                        get_evaluator().maybe_evaluate(
                            event,
                            prompt_text=_prompt_text,
                            response_text=_response_text,
                        )

                if not (start_ok and end_ok):
                    publish_errors += 1

                if event["status"] == "success":
                    successes += 1
                else:
                    errors += 1

                if event.get("sla_breached"):
                    sla_breaches += 1

                total_cost += event["cost_usd"]
                total_tokens += event["total_tokens"]

            except Exception as exc:
                logger.exception("Event processing error: %s", exc)
                errors += 1
                publish_errors += 1
                otel.record_self_metric(
                    sc.METRIC_SELF_PUBLISH_ERRORS, 1, {"reason": "exception"},
                )

        try:
            publisher.flush()
            publisher_ok = True
        except Exception as exc:
            logger.error("Producer flush error: %s", exc)
            publisher_ok = False
            otel.record_self_metric(
                sc.METRIC_SELF_PUBLISH_ERRORS, 1, {"reason": "flush_error"},
            )

        batch_duration = time.monotonic() - batch_started
        otel.record_self_metric(sc.METRIC_SELF_BATCH_DURATION, batch_duration, {})
        otel.record_self_metric(
            sc.METRIC_SELF_QUEUE_DEPTH,
            float(publisher.queue_depth),
            {},
        )

        anomaly = get_anomaly_summary()
        tracing.set_batch_attributes(
            batch_span,
            **{
                sc.ATTR_BATCH_SIZE:        batch_size,
                sc.ATTR_BATCH_OK:          successes,
                sc.ATTR_BATCH_ERR:         errors,
                sc.ATTR_BATCH_SLA_BREACH:  sla_breaches,
                sc.ATTR_BATCH_COST_USD:    round(total_cost, 6),
                sc.ATTR_BATCH_TOKENS:      total_tokens,
                sc.ATTR_ANOMALY_DEGRADED:  anomaly["degraded_model"],
                sc.ATTR_ANOMALY_CASCADE:   anomaly["cascade_active"],
                sc.ATTR_ANOMALY_RATELIMIT: anomaly["rate_limited_client"],
            },
        )

    health_server.heartbeat(publisher_healthy=publisher_ok and publisher.is_healthy)

    pod_sim.update_load_signal(batch_size / BATCH_INTERVAL_S)

    budget_status = get_client_budget_status()
    exhausted_clients = [c for c, s in budget_status.items() if s["pct"] >= 95]

    summary: dict[str, Any] = {
        "batch_size":        batch_size,
        "successes":         successes,
        "errors":            errors,
        "sla_breaches":      sla_breaches,
        "error_rate":        round(errors / batch_size, 4) if batch_size else 0.0,
        "sla_breach_rate":   round(sla_breaches / batch_size, 4) if batch_size else 0.0,
        "total_cost_usd":    round(total_cost, 6),
        "total_tokens":      total_tokens,
        "anomaly":           anomaly,
        "budget_alerts":     exhausted_clients,
        "publish_errors":    publish_errors,
        "batch_duration_s":  round(batch_duration, 4),
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }

    log_parts = (
        f"batch={batch_size} ok={successes} err={errors} "
        f"sla_breach={sla_breaches} cost=${total_cost:.5f} tokens={total_tokens} "
        f"dur={batch_duration:.2f}s"
    )
    if anomaly["degraded_model"]:
        log_parts += f" degraded={anomaly['degraded_model']}"
    if anomaly["cascade_active"]:
        log_parts += " CASCADE_ACTIVE"
    if exhausted_clients:
        log_parts += f" budget_exhausted={exhausted_clients}"

    logger.info(log_parts)
    return summary


# ---------------------------------------------------------------------------
# Signal handlers & local runner entry point
# ---------------------------------------------------------------------------


def _signal_handler(signum: int, _frame: Any) -> None:
    global _running
    logger.info("Signal %d received — shutting down after current batch", signum)
    _running = False


def main() -> int:
    """Run the continuous batch loop.

    Returns a process exit code (0 = clean shutdown, 2 = startup misconfiguration).
    """
    azure_logger.setup_structured_logging()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    azure_logger.log_startup_config({
        "batch_interval_s":   BATCH_INTERVAL_S,
        "base_batch_size":    BASE_BATCH_SIZE,
        "error_window_prob":  ERROR_WINDOW_PROB,
        "simulate_latency":   SIMULATE_LATENCY,
        "prometheus_port":    int(os.getenv("PROMETHEUS_PORT", "0")),
        "health_port":        HEALTH_PORT,
        "otel_service_name":  os.getenv("OTEL_SERVICE_NAME", "ai-telemetry"),
        "otel_otlp_endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        "environment":        os.getenv("ENVIRONMENT", "prod"),
        "eventhub_namespace": os.getenv("EVENTHUB_NAMESPACE", ""),
        "eventhub_name":      os.getenv("EVENTHUB_NAME", "ai-telemetry-events"),
    })

    logger.info(
        "Runner starting | interval=%.1fs | base_batch=%d | error_prob=%.2f%%",
        BATCH_INTERVAL_S,
        BASE_BATCH_SIZE,
        ERROR_WINDOW_PROB * 100,
    )

    try:
        _get_publisher()
    except PublisherConfigError as exc:
        logger.error("Startup aborted — publisher misconfigured: %s", exc)
        return 2

    _ensure_tracing()
    health_server.start(HEALTH_PORT)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
