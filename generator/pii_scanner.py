"""PII detection and redaction for AI telemetry (FR-014, NFR-005).

Two-layer defence:

  Layer 1 — this module (Python, runs in-process before any emit):
    Regex-based redaction of common PII patterns. Fast, no external call.
    Produces a ``RedactionResult`` with the scrubbed text, hit-count per
    entity type, and a binary ``pii_detected`` flag.

  Layer 2 — OTel Collector transform processor (infra/otel-collector-pii-config.yaml):
    Belt-and-braces attribute-level deletion of any field that was not
    redacted in-process (e.g. because the runner was upgraded without
    the scanner, or because the field arrived via a third-party library).

Presidio (Microsoft) provides higher recall than regex alone.  When
``microsoft-presidio`` is installed, this module delegates to it for
English-language analysis; otherwise it falls back to the built-in
regex patterns, which cover the most common enterprise cases.

Enabling the Presidio path
--------------------------
  pip install presidio-analyzer presidio-anonymizer
  python3 -m spacy download en_core_web_lg   # required by Presidio

Configure via environment variables:

  ``PII_BACKEND``    ``presidio`` | ``regex`` (default: auto-detect)
  ``PII_SAMPLE_RATE`` Fraction of events to scan  (default 1.0 = all)
  ``PII_LOG_HITS``   ``true`` | ``false``  — log every redaction hit (default false)
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Presidio import
# ---------------------------------------------------------------------------

try:
    from presidio_analyzer import AnalyzerEngine  # type: ignore
    from presidio_anonymizer import AnonymizerEngine  # type: ignore

    _PRESIDIO_AVAILABLE = True
except ImportError:
    _PRESIDIO_AVAILABLE = False

_PII_BACKEND: str = os.getenv("PII_BACKEND", "auto").lower()
_PII_SAMPLE_RATE: float = float(os.getenv("PII_SAMPLE_RATE", "1.0"))
_PII_LOG_HITS: bool = os.getenv("PII_LOG_HITS", "false").lower() == "true"

# Entities Presidio should detect (superset of what our regexes cover).
_PRESIDIO_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "US_PASSPORT",
    "UK_NHS",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_DRIVER_LICENSE",
    "DATE_TIME",
    "NRP",           # Name / Race / Political opinion — sensitive classes
    "PERSON",
    "LOCATION",
    "MEDICAL_LICENSE",
]

# ---------------------------------------------------------------------------
# Regex patterns (fallback / pre-filter)
# ---------------------------------------------------------------------------

_REDACT_PLACEHOLDER = "[REDACTED]"

# (entity_type, compiled_regex)
_REGEX_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Email
    ("EMAIL",       re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # US phone — various formats
    ("PHONE",       re.compile(r"\b(?:\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b")),
    # US SSN
    ("SSN",         re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")),
    # Credit card (Visa / MC / Amex / Discover loose)
    ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[\s\-]?){3}\d{4}\b")),
    # IPv4
    ("IP",          re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # UK NHS number  XXX XXX XXXX
    ("NHS",         re.compile(r"\b\d{3}[\s]?\d{3}[\s]?\d{4}\b")),
    # IBAN (loose)
    ("IBAN",        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    # AWS access key  AKIA…
    ("AWS_KEY",     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Generic API key patterns  (sk-, Bearer token prefix)
    ("API_KEY",     re.compile(r"\b(?:sk-|Bearer\s)[A-Za-z0-9_\-]{20,}\b")),
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RedactionResult:
    original_hash: str              # sha256(original_text) for immutable audit
    redacted_text: str              # scrubbed text safe to emit to Loki
    pii_detected: bool
    entity_counts: dict[str, int] = field(default_factory=dict)
    backend: str = "none"           # "regex" | "presidio" | "none"

    @property
    def truncated_text(self) -> str:
        """First and last 32 chars with `[…]` in the middle — safe for Loki labels."""
        if len(self.redacted_text) <= 64:
            return self.redacted_text
        return self.redacted_text[:32] + "[…]" + self.redacted_text[-32:]


# ---------------------------------------------------------------------------
# Presidio singleton
# ---------------------------------------------------------------------------

_presidio_analyzer: Any = None
_presidio_anonymizer: Any = None


def _get_presidio() -> tuple[Any, Any] | None:
    global _presidio_analyzer, _presidio_anonymizer
    if _presidio_analyzer is None and _PRESIDIO_AVAILABLE:
        try:
            _presidio_analyzer  = AnalyzerEngine()
            _presidio_anonymizer = AnonymizerEngine()
        except Exception as exc:
            logger.warning("Presidio init failed: %s — falling back to regex", exc)
            return None
    return (_presidio_analyzer, _presidio_anonymizer) if _presidio_analyzer else None


# ---------------------------------------------------------------------------
# Scanning implementations
# ---------------------------------------------------------------------------


def _scan_regex(text: str) -> RedactionResult:
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    counts: dict[str, int] = {}
    scrubbed = text
    for entity_type, pattern in _REGEX_PATTERNS:
        matches = pattern.findall(scrubbed)
        if matches:
            counts[entity_type] = len(matches)
            scrubbed = pattern.sub(_REDACT_PLACEHOLDER, scrubbed)
    return RedactionResult(
        original_hash=original_hash,
        redacted_text=scrubbed,
        pii_detected=bool(counts),
        entity_counts=counts,
        backend="regex",
    )


def _scan_presidio(text: str, analyzer: Any, anonymizer: Any) -> RedactionResult:
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    try:
        results = analyzer.analyze(
            text=text, entities=_PRESIDIO_ENTITIES, language="en",
        )
        counts: dict[str, int] = {}
        for r in results:
            counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
        anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
        return RedactionResult(
            original_hash=original_hash,
            redacted_text=anonymized.text,
            pii_detected=bool(counts),
            entity_counts=counts,
            backend="presidio",
        )
    except Exception as exc:
        logger.warning("Presidio scan failed (%s) — falling back to regex", exc)
        return _scan_regex(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(text: str | None) -> RedactionResult:
    """Scan and redact PII in ``text``.

    Returns a :class:`RedactionResult` regardless of the active backend or
    whether the sample-rate gate skips the scan. If skipped, ``backend``
    is ``"none"`` and ``redacted_text == text``.
    """
    if not text:
        return RedactionResult(
            original_hash="",
            redacted_text=text or "",
            pii_detected=False,
            backend="none",
        )

    if _PII_SAMPLE_RATE < 1.0 and random.random() > _PII_SAMPLE_RATE:
        return RedactionResult(
            original_hash=hashlib.sha256(text.encode()).hexdigest(),
            redacted_text=text,
            pii_detected=False,
            backend="none",
        )

    use_presidio = (
        _PII_BACKEND == "presidio"
        or (_PII_BACKEND == "auto" and _PRESIDIO_AVAILABLE)
    )

    if use_presidio:
        pair = _get_presidio()
        if pair:
            result = _scan_presidio(text, *pair)
        else:
            result = _scan_regex(text)
    else:
        result = _scan_regex(text)

    if result.pii_detected and _PII_LOG_HITS:
        logger.info(
            "PII detected | backend=%s entities=%s",
            result.backend,
            result.entity_counts,
        )

    return result


def scan_event_fields(event: dict[str, Any]) -> dict[str, Any]:
    """Scan PII-sensitive fields in an event dict and return a new dict
    with those fields replaced by their :class:`RedactionResult`.

    The returned dict carries extra keys:
      ``prompt_pii``    — :class:`RedactionResult` for prompt_text
      ``response_pii``  — :class:`RedactionResult` for response_text
    """
    out = dict(event)
    for field_key, result_key in (
        ("prompt_text",   "prompt_pii"),
        ("response_text", "response_pii"),
    ):
        raw = event.get(field_key)
        result = scan(raw)
        out[field_key]   = result.redacted_text
        out[result_key]  = result
    return out
