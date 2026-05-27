"""Tests for the OpenAI-as-judge evaluator.

We mock the OpenAI client so tests run without a real API key. The tests
verify the sampler gate, budget tracker, result parsing, and the span
attribute side-effect.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from generator.evaluator import EvalResult, Evaluator, _DailyBudgetTracker
from generator.synthetic_generator import generate_event

# ---------------------------------------------------------------------------
# DailyBudgetTracker
# ---------------------------------------------------------------------------

def test_budget_allows_within_limit():
    tracker = _DailyBudgetTracker(1000)
    assert tracker.consume(400) is True
    assert tracker.consume(400) is True
    assert tracker.consume(300) is False  # would exceed
    assert tracker.remaining == 200


def test_budget_resets_on_new_day():
    tracker = _DailyBudgetTracker(100)
    tracker.consume(90)
    # Force date rollover
    tracker._date = "1970-01-01"
    assert tracker.consume(90) is True


# ---------------------------------------------------------------------------
# Evaluator sampler gate
# ---------------------------------------------------------------------------

def test_evaluator_skips_error_events(monkeypatch):
    monkeypatch.setenv("EVAL_ENABLED", "true")
    monkeypatch.setenv("EVAL_SAMPLE_RATE", "1.0")
    ev = generate_event(error_rate=1.0)
    assert ev["status"] == "error"
    evaluator = Evaluator()
    result = evaluator.maybe_evaluate(ev, "prompt", "response")
    assert result is None  # error events never evaluated


def test_evaluator_skips_when_no_text(monkeypatch):
    monkeypatch.setenv("EVAL_ENABLED", "true")
    monkeypatch.setenv("EVAL_SAMPLE_RATE", "1.0")
    ev = generate_event(error_rate=0.0)
    ev["status"] = "success"
    evaluator = Evaluator()
    assert evaluator.maybe_evaluate(ev, None, None) is None


def test_evaluator_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("EVAL_ENABLED", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    evaluator = Evaluator()
    ev = generate_event(error_rate=0.0)
    ev["status"] = "success"
    assert evaluator.maybe_evaluate(ev, "p", "r") is None


def test_evaluator_respects_sample_rate(monkeypatch):
    """With sample_rate=0, never evaluate."""
    monkeypatch.setenv("EVAL_ENABLED", "true")
    monkeypatch.setenv("EVAL_SAMPLE_RATE", "0.0")
    evaluator = Evaluator()
    # Patch _EVAL_SAMPLE_RATE directly
    import generator.evaluator as mod
    monkeypatch.setattr(mod, "_EVAL_SAMPLE_RATE", 0.0)
    ev = generate_event(error_rate=0.0)
    ev["status"] = "success"
    evaluator._enabled = True
    assert evaluator.maybe_evaluate(ev, "p", "r") is None


# ---------------------------------------------------------------------------
# Evaluator call with mocked OpenAI
# ---------------------------------------------------------------------------

def _make_mock_response(scores: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = json.dumps(scores)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.total_tokens = 42
    return resp


def test_evaluator_parses_scores(monkeypatch):
    monkeypatch.setenv("EVAL_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    scores = {"faithfulness": 8, "relevance": 9, "groundedness": 7}
    mock_resp = _make_mock_response(scores)

    evaluator = Evaluator()
    evaluator._enabled = True

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    evaluator._client = mock_client

    ev = generate_event(error_rate=0.0)
    ev["status"] = "success"

    result = evaluator._call_judge("What is 2+2?", "4", ev)
    assert result.faithfulness == 8.0
    assert result.relevance    == 9.0
    assert result.groundedness == 7.0
    assert result.tokens_used  == 42
    assert result.error        == ""


def test_evaluator_handles_bad_json(monkeypatch):
    monkeypatch.setenv("EVAL_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    msg = MagicMock()
    msg.content = "NOT JSON"
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.total_tokens = 10

    evaluator = Evaluator()
    evaluator._enabled = True
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    evaluator._client = mock_client

    ev = generate_event(error_rate=0.0)
    ev["status"] = "success"
    result = evaluator._call_judge("p", "r", ev)
    assert "json_parse_error" in result.error


def test_eval_result_to_dict():
    r = EvalResult(faithfulness=8, relevance=9, groundedness=7,
                   model="gpt-4o-mini", tokens_used=42, latency_ms=120.5)
    d = r.to_dict()
    assert d["faithfulness"] == 8
    assert d["model"] == "gpt-4o-mini"
    assert "error" in d
