"""Tests for the PII scanner module."""
from __future__ import annotations

import os

import pytest

from generator.pii_scanner import RedactionResult, scan, scan_event_fields


def test_email_redacted():
    result = scan("Send results to alice@example.com please")
    assert result.pii_detected is True
    assert "alice@example.com" not in result.redacted_text
    assert "[REDACTED]" in result.redacted_text
    assert result.entity_counts.get("EMAIL", 0) >= 1


def test_ssn_redacted():
    result = scan("SSN: 123-45-6789")
    assert result.pii_detected is True
    assert "123-45-6789" not in result.redacted_text


def test_credit_card_redacted():
    result = scan("Card number: 4111 1111 1111 1111")
    assert result.pii_detected is True
    assert "4111" not in result.redacted_text


def test_no_pii_clean():
    result = scan("Please summarise this quarterly report.")
    assert result.pii_detected is False
    assert result.redacted_text == "Please summarise this quarterly report."


def test_none_input():
    result = scan(None)
    assert result.pii_detected is False
    assert result.redacted_text == ""


def test_empty_string():
    result = scan("")
    assert result.pii_detected is False


def test_original_hash_computed():
    text = "My email is test@test.com"
    result = scan(text)
    import hashlib
    assert result.original_hash == hashlib.sha256(text.encode()).hexdigest()


def test_truncated_text_long():
    text = "a" * 200
    result = scan(text)
    assert len(result.truncated_text) < len(text)
    assert "[…]" in result.truncated_text


def test_truncated_text_short():
    text = "short text"
    result = scan(text)
    assert result.truncated_text == text


def test_scan_event_fields_no_text(monkeypatch):
    """Events without prompt_text/response_text should pass through unchanged."""
    from generator.synthetic_generator import generate_event
    event = generate_event()
    out = scan_event_fields(event)
    # No prompt_pii because text fields don't exist in synthetic events
    assert "prompt_pii" in out
    assert out["prompt_pii"].pii_detected is False


def test_sample_rate_zero_skips_scan(monkeypatch):
    monkeypatch.setenv("PII_SAMPLE_RATE", "0.0")
    # Re-import so the module-level constant is refreshed.
    import importlib

    import generator.pii_scanner as mod
    monkeypatch.setattr(mod, "_PII_SAMPLE_RATE", 0.0)
    result = mod.scan("alice@example.com")
    # With 0.0 sample rate the scan is always skipped.
    assert result.backend == "none"


def test_api_key_pattern_redacted():
    result = scan("Authorization: Bearer sk-abc123xyz456def789ghi012jkl345mno678")
    assert result.pii_detected is True


def test_multiple_pii_all_redacted():
    text = "Contact john@doe.com or call 555-123-4567"
    result = scan(text)
    assert "john@doe.com" not in result.redacted_text
    assert "555-123-4567" not in result.redacted_text
    assert result.entity_counts["EMAIL"] >= 1
    assert result.entity_counts["PHONE"] >= 1
