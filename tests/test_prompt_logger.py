"""Tests for the prompt logger (dual-sink: Loki structured log + local file fallback)."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from generator.pii_scanner import scan
from generator.prompt_logger import _sha256, _truncate, log_prompt
from generator.synthetic_generator import generate_event


def test_sha256_deterministic():
    assert _sha256("hello") == _sha256("hello")
    assert _sha256("hello") != _sha256("world")


def test_sha256_none():
    assert _sha256(None) == ""


def test_truncate_short():
    assert _truncate("abc") == "abc"


def test_truncate_long():
    t = _truncate("a" * 200)
    assert len(t) < 200
    assert "[…]" in t


def test_log_prompt_writes_local_file(monkeypatch, tmp_path):
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOCAL_PATH", str(audit_file))
    monkeypatch.setenv("PROMPT_LOG_ENABLED", "true")
    monkeypatch.delenv("AUDIT_BLOB_CONNECTION_STRING", raising=False)

    # Reset module-level state so it picks up the new env var.
    import generator.prompt_logger as pm
    monkeypatch.setattr(pm, "_blob_warned_once", False)
    monkeypatch.setattr(pm, "_blob_service_client", None)
    monkeypatch.setattr(pm, "_AUDIT_LOCAL_PATH", str(audit_file))

    event = generate_event()
    prompt = "What is the capital of France?"
    response = "The capital of France is Paris."
    prompt_pii = scan(prompt)
    response_pii = scan(response)

    log_prompt(event, prompt_text=prompt, response_text=response,
               prompt_pii=prompt_pii, response_pii=response_pii)

    assert audit_file.exists()
    with open(audit_file) as f:
        records = [json.loads(line) for line in f if line.strip()]

    assert len(records) == 1
    r = records[0]
    assert r["prompt_hash"] == prompt_pii.original_hash
    assert r["response_hash"] == response_pii.original_hash
    assert r["event_type"] == "prompt_log_event"
    assert "prompt_text" in r          # full text in audit record
    assert "prompt_truncated" in r     # truncated in Loki record
    assert r["pii_detected"] is False  # clean inputs


def test_log_prompt_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMPT_LOG_ENABLED", "false")
    import generator.prompt_logger as pm
    monkeypatch.setattr(pm, "_PROMPT_LOG_ENABLED", False)

    audit_file = tmp_path / "audit2.jsonl"
    monkeypatch.setattr(pm, "_AUDIT_LOCAL_PATH", str(audit_file))

    event = generate_event()
    log_prompt(event, prompt_text="test", response_text="test")
    assert not audit_file.exists()


def test_log_prompt_pii_detected_flag(monkeypatch, tmp_path):
    audit_file = tmp_path / "pii_audit.jsonl"
    import generator.prompt_logger as pm
    monkeypatch.setattr(pm, "_blob_warned_once", False)
    monkeypatch.setattr(pm, "_blob_service_client", None)
    monkeypatch.setattr(pm, "_AUDIT_LOCAL_PATH", str(audit_file))
    monkeypatch.setattr(pm, "_PROMPT_LOG_ENABLED", True)

    event = generate_event()
    prompt = "Call me at 555-987-6543 or email bob@company.com"
    prompt_pii = scan(prompt)
    assert prompt_pii.pii_detected is True

    log_prompt(event, prompt_text=prompt, response_text="OK",
               prompt_pii=prompt_pii)

    with open(audit_file) as f:
        r = json.loads(f.readline())
    assert r["pii_detected"] is True
