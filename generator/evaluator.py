"""OpenAI-as-judge model quality evaluator (FR-012).

Scores a sampled fraction of successful LLM responses on three dimensions:
  faithfulness   — does the response stick to facts in the prompt?
  relevance      — does the response address what was asked?
  groundedness   — are claims attributable to retrievable sources?

Architecture
------------
  - A ``Evaluator`` instance is created once per runner process.
  - ``maybe_evaluate(event, prompt_text, response_text)`` is called after
    every successful event. It rolls a die against ``EVAL_SAMPLE_RATE``
    (default 0.01 = 1 %) and, when selected, calls the OpenAI Chat API
    with a structured judge prompt, parses the JSON scores, and:
      1. Records them as OTel histogram attributes on the *active span*.
      2. Emits ``ai_gateway_eval_*`` counters for Prometheus.
      3. Logs a structured ``eval_result`` event to Loki.

  - All API calls are made on a background ``ThreadPoolExecutor`` so the
    main batch loop is never blocked.

  - A daily token budget (``EVAL_DAILY_TOKEN_BUDGET``, default 50 000)
    prevents runaway cost if sample rate is misconfigured.

Configuration
-------------
  ``OPENAI_API_KEY``            Required when backend = openai
  ``EVAL_MODEL``                Judge model (default: ``gpt-4o-mini``)
  ``EVAL_SAMPLE_RATE``          Fraction of events to evaluate (default: ``0.01``)
  ``EVAL_DAILY_TOKEN_BUDGET``   Max tokens/day to spend on evaluation (default: ``50000``)
  ``EVAL_ENABLED``              ``true`` | ``false`` (default: ``true`` when API key set)
  ``EVAL_TIMEOUT_S``            Per-call timeout in seconds (default: ``15``)
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_EVAL_MODEL             = os.getenv("EVAL_MODEL",              "gpt-4o-mini")
_EVAL_SAMPLE_RATE       = float(os.getenv("EVAL_SAMPLE_RATE",  "0.01"))
_EVAL_DAILY_BUDGET      = int(os.getenv("EVAL_DAILY_TOKEN_BUDGET", "50000"))
_EVAL_TIMEOUT_S         = float(os.getenv("EVAL_TIMEOUT_S",    "15"))

# Lazy-detect enabled state: enabled only when API key is present, unless
# explicitly overridden.
def _eval_enabled() -> bool:
    override = os.getenv("EVAL_ENABLED", "").lower()
    if override in ("true", "1", "yes"):
        return True
    if override in ("false", "0", "no"):
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# Judge prompt
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """You are a strict AI-quality evaluator. Given the PROMPT and RESPONSE below, return a JSON object with exactly these three integer scores (0–10):

  "faithfulness" : how well the response avoids hallucination and sticks to facts (10 = no hallucination)
  "relevance"    : how directly the response addresses the prompt (10 = perfectly on-topic)
  "groundedness" : how attributable the claims are to verifiable sources (10 = fully grounded)

Return ONLY valid JSON like: {{"faithfulness": 8, "relevance": 9, "groundedness": 7}}
No explanation, no markdown, no extra keys.

PROMPT:
{prompt}

RESPONSE:
{response}"""


# ---------------------------------------------------------------------------
# Daily budget tracker
# ---------------------------------------------------------------------------

class _DailyBudgetTracker:
    def __init__(self, budget: int) -> None:
        self._budget  = budget
        self._used    = 0
        self._date    = ""
        self._lock    = threading.Lock()

    def consume(self, tokens: int) -> bool:
        """Attempt to consume ``tokens`` from today's budget. Returns False
        if the budget is exhausted or the tokens would exceed it.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if today != self._date:
                self._date = today
                self._used = 0
            if self._used + tokens > self._budget:
                return False
            self._used += tokens
            return True

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self._budget - self._used)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class EvalResult:
    __slots__ = ("faithfulness", "relevance", "groundedness",
                 "model", "tokens_used", "latency_ms", "error")

    def __init__(
        self,
        faithfulness: float = 0.0,
        relevance:    float = 0.0,
        groundedness: float = 0.0,
        model:        str   = "",
        tokens_used:  int   = 0,
        latency_ms:   float = 0.0,
        error:        str   = "",
    ) -> None:
        self.faithfulness  = faithfulness
        self.relevance     = relevance
        self.groundedness  = groundedness
        self.model         = model
        self.tokens_used   = tokens_used
        self.latency_ms    = latency_ms
        self.error         = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "faithfulness":  self.faithfulness,
            "relevance":     self.relevance,
            "groundedness":  self.groundedness,
            "model":         self.model,
            "tokens_used":   self.tokens_used,
            "latency_ms":    self.latency_ms,
            "error":         self.error,
        }


class Evaluator:
    """Thin wrapper around the OpenAI Chat API for judge-style evaluation."""

    def __init__(self) -> None:
        self._budget    = _DailyBudgetTracker(_EVAL_DAILY_BUDGET)
        self._executor  = ThreadPoolExecutor(max_workers=4, thread_name_prefix="evaluator")
        self._client: Any = None
        self._init_lock = threading.Lock()
        self._enabled   = _eval_enabled()

        if self._enabled:
            logger.info(
                "Evaluator ready | model=%s | sample_rate=%.2f%% | daily_budget=%d tokens",
                _EVAL_MODEL, _EVAL_SAMPLE_RATE * 100, _EVAL_DAILY_BUDGET,
            )
        else:
            logger.info(
                "Evaluator disabled (EVAL_ENABLED=false or OPENAI_API_KEY not set)",
            )

    def _get_client(self) -> Any:
        with self._init_lock:
            if self._client is not None:
                return self._client
            try:
                import openai  # type: ignore

                self._client = openai.OpenAI(
                    api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=_EVAL_TIMEOUT_S,
                )
            except ImportError:
                logger.warning("openai package not installed — evaluator disabled")
                self._enabled = False
            except Exception as exc:
                logger.error("OpenAI client init failed: %s", exc)
                self._enabled = False
        return self._client

    def _call_judge(
        self,
        prompt_text: str,
        response_text: str,
        event: dict[str, Any],
    ) -> EvalResult:
        """Synchronous judge call — runs on the executor thread."""
        t0 = time.monotonic()
        client = self._get_client()
        if client is None:
            return EvalResult(error="client_unavailable")

        # Rough token estimate to pre-check the budget. Assume ~3 chars/token.
        est_tokens = (len(prompt_text) + len(response_text)) // 3 + 200
        if not self._budget.consume(est_tokens):
            logger.warning(
                "Eval daily token budget exhausted (remaining=%d) — skipping",
                self._budget.remaining,
            )
            return EvalResult(error="budget_exhausted")

        judge_content = _JUDGE_PROMPT.format(
            prompt=prompt_text[:2000],     # cap to keep cost bounded
            response=response_text[:2000],
        )

        try:
            resp = client.chat.completions.create(
                model=_EVAL_MODEL,
                messages=[{"role": "user", "content": judge_content}],
                temperature=0,
                max_tokens=80,
                response_format={"type": "json_object"},
            )
            latency_ms = (time.monotonic() - t0) * 1000
            raw = resp.choices[0].message.content or "{}"
            scores = json.loads(raw)
            actual_tokens = getattr(resp.usage, "total_tokens", est_tokens)

            result = EvalResult(
                faithfulness  = float(scores.get("faithfulness", 0)),
                relevance     = float(scores.get("relevance", 0)),
                groundedness  = float(scores.get("groundedness", 0)),
                model         = _EVAL_MODEL,
                tokens_used   = actual_tokens,
                latency_ms    = round(latency_ms, 1),
            )
        except json.JSONDecodeError as exc:
            result = EvalResult(error=f"json_parse_error: {exc}")
        except Exception as exc:
            result = EvalResult(error=str(exc))

        # Emit scores to the active span and to Loki.
        self._record(event, result)
        return result

    def _record(self, event: dict[str, Any], result: EvalResult) -> None:
        """Stamp eval scores on the active span + emit structured log."""
        if not result.error:
            try:
                from opentelemetry import trace

                span = trace.get_current_span()
                span.set_attribute("ai.eval.faithfulness",  result.faithfulness)
                span.set_attribute("ai.eval.relevance",     result.relevance)
                span.set_attribute("ai.eval.groundedness",  result.groundedness)
                span.set_attribute("ai.eval.model",         result.model)
                span.set_attribute("ai.eval.tokens_used",   result.tokens_used)
            except Exception:
                pass

        logging.getLogger("generator.eval").info(
            "eval_result",
            extra={
                "event_type":      "eval_result",
                "request_id":      event.get("request_id"),
                "tenant_id":       event.get("client_name"),
                "model_name":      event.get("model_name"),
                "operation_name":  event.get("operation_name"),
                **result.to_dict(),
                "timestamp":       datetime.now(timezone.utc).isoformat(),
            },
        )

    def maybe_evaluate(
        self,
        event: dict[str, Any],
        prompt_text: str | None,
        response_text: str | None,
    ) -> Future[EvalResult] | None:
        """Submit an evaluation on the thread pool if the sample-rate gate passes.

        Returns the :class:`~concurrent.futures.Future` for tests; callers
        can ignore it (fire-and-forget).
        """
        if not self._enabled:
            return None
        if event.get("status") != "success":
            return None
        if not prompt_text or not response_text:
            return None
        if random.random() > _EVAL_SAMPLE_RATE:
            return None

        return self._executor.submit(
            self._call_judge, prompt_text, response_text, event,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_evaluator: Evaluator | None = None
_evaluator_lock = threading.Lock()


def get_evaluator() -> Evaluator:
    global _evaluator
    with _evaluator_lock:
        if _evaluator is None:
            _evaluator = Evaluator()
    return _evaluator
