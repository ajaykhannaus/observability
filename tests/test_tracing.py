"""Tests for the Bucket 1 tracing layer.

We exercise the public API of ``generator.otel_tracing`` against an
in-memory span exporter so the assertions don't depend on a running
Collector. The InMemorySpanExporter is part of the OTel SDK's test utilities.
"""
from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from generator import otel_tracing as tracing
from generator import semantic_conventions as sc
from generator.synthetic_generator import generate_event


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    """Replace the global tracer provider with one that exports in-memory."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # Refresh module-level tracer reference so helpers pick up the new provider.
    tracing._TRACER = provider.get_tracer("test")
    tracing._INITIALISED = True
    yield exporter
    exporter.clear()


def test_batch_span_emits(memory_exporter: InMemorySpanExporter) -> None:
    with tracing.batch_span():
        pass
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == sc.SPAN_BATCH


def test_request_span_carries_identity_attributes(memory_exporter: InMemorySpanExporter) -> None:
    event = generate_event()
    with tracing.request_span(event):
        pass
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == sc.SPAN_REQUEST
    assert span.attributes[sc.ATTR_TENANT_ID]    == event["client_name"]
    assert span.attributes[sc.ATTR_MODEL_NAME]   == event["model_name"]
    assert span.attributes[sc.ATTR_REQUEST_ID]   == event["request_id"]
    assert span.attributes[sc.ATTR_TOKENS_TOTAL] == event["total_tokens"]
    assert span.attributes[sc.ATTR_COST_USD]     == event["cost_usd"]


def test_phase_spans_emitted_under_request(memory_exporter: InMemorySpanExporter) -> None:
    event = generate_event()
    with tracing.request_span(event):
        with tracing.phase_span(sc.SPAN_QUEUE_WAIT, event["queue_wait_ms"]):
            pass
        with tracing.phase_span(sc.SPAN_MODEL_INFERENCE, event["model_inference_ms"]):
            pass

    spans = memory_exporter.get_finished_spans()
    # 1 request span + 2 phase spans.
    assert len(spans) == 3
    request_span = next(s for s in spans if s.name == sc.SPAN_REQUEST)
    phase_spans = [s for s in spans if s.name != sc.SPAN_REQUEST]
    for ps in phase_spans:
        assert ps.parent.span_id == request_span.context.span_id
        assert ps.attributes["ai.phase.duration_ms"] >= 0


def test_traceparent_returns_active_span_context(memory_exporter: InMemorySpanExporter) -> None:
    with tracing.batch_span():
        tp = tracing.current_traceparent()
        assert tp is not None
        # W3C format: 00-<32 hex trace_id>-<16 hex span_id>-<2 hex flags>
        parts = tp.split("-")
        assert len(parts) == 4
        assert parts[0] == "00"
        assert len(parts[1]) == 32
        assert len(parts[2]) == 16


def test_traceparent_outside_span_is_none_or_invalid(memory_exporter: InMemorySpanExporter) -> None:
    # No active span -> propagator either returns nothing or an all-zero
    # traceparent. Either is acceptable; ensure we don't blow up.
    tp = tracing.current_traceparent()
    if tp is not None:
        # If returned, it must be the well-formed but invalid (all-zero) form.
        parts = tp.split("-")
        assert len(parts) == 4
        assert parts[0] == "00"


def test_error_request_span_marks_error_status(memory_exporter: InMemorySpanExporter) -> None:
    # Force an error by setting a very high error_rate.
    event = generate_event(error_rate=1.0)
    assert event["status"] == "error"
    with tracing.request_span(event):
        pass
    span = memory_exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"


def test_latency_phases_sum_to_total() -> None:
    """The four latency phases must reconstruct the total latency."""
    for _ in range(50):
        event = generate_event()
        total = (
            event["queue_wait_ms"]
            + event["model_inference_ms"]
            + event["first_token_ms"]
            + event["stream_response_ms"]
        )
        assert abs(total - event["latency_ms"]) < 5.0, (
            f"phase sum {total} differs from latency_ms {event['latency_ms']}"
        )
