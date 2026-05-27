"""Prompt and response logging (FR-003, FR-009, NFR-011).

Hybrid strategy — two parallel sinks:

  Loki (via OTel Collector → Loki exporter)
    Emit a structured ``prompt_log_event`` log line containing:
      - ``prompt_hash``        sha256 of the original prompt
      - ``response_hash``      sha256 of the original response
      - ``prompt_truncated``   first 32 + last 32 chars of the *redacted* text
      - ``response_truncated`` same
      - ``pii_entity_counts``  {"EMAIL": 2, …} — what was found (not the text)
      - ``model_name``, ``tenant_id``, ``request_id``, ``trace_id``

  WORM Blob (Azure Blob Storage with immutability policy)
    Async-write the full *original* (pre-redaction) text as a newline-
    delimited JSON record, keyed by ``{year}/{month}/{day}/{trace_id}.jsonl``.
    This is the forensic audit trail that a privileged audit role can access.

    If Azure Blob is not configured (``AUDIT_BLOB_CONNECTION_STRING`` absent)
    the module logs a warning once and writes to a local JSONL file under
    ``AUDIT_LOCAL_PATH`` (default ``/tmp/audit_log.jsonl``). This means every
    environment — local dev, CI, staging — always produces an audit file.

Configuration
-------------
  ``AUDIT_BLOB_CONNECTION_STRING``  Azure Storage connection string
  ``AUDIT_BLOB_CONTAINER``          Container name (default: ``ai-audit-log``)
  ``AUDIT_LOCAL_PATH``              Fallback local file (default: ``/tmp/audit_log.jsonl``)
  ``PROMPT_LOG_ENABLED``            ``true`` | ``false``  (default: ``true``)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_PROMPT_LOG_ENABLED: bool = os.getenv("PROMPT_LOG_ENABLED", "true").lower() == "true"
_AUDIT_LOCAL_PATH: str    = os.getenv("AUDIT_LOCAL_PATH", "/tmp/audit_log.jsonl")
_AUDIT_CONTAINER: str     = os.getenv("AUDIT_BLOB_CONTAINER", "ai-audit-log")

# Warn once, not per-event, when Blob isn't configured.
_blob_warned_once = False
_blob_client_lock = threading.Lock()
_blob_service_client: Any = None

# Local file write lock — multiple threads may call log_prompt() concurrently.
_local_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Blob client (lazy init)
# ---------------------------------------------------------------------------


def _get_blob_service_client() -> Any:
    global _blob_service_client, _blob_warned_once
    with _blob_client_lock:
        if _blob_service_client is not None:
            return _blob_service_client

        conn_str = os.getenv("AUDIT_BLOB_CONNECTION_STRING", "").strip()
        if not conn_str:
            if not _blob_warned_once:
                logger.warning(
                    "AUDIT_BLOB_CONNECTION_STRING not set — prompt audit writing "
                    "to local file %s. Set this in production.",
                    _AUDIT_LOCAL_PATH,
                )
                _blob_warned_once = True
            return None

        try:
            from azure.storage.blob import BlobServiceClient  # type: ignore

            _blob_service_client = BlobServiceClient.from_connection_string(conn_str)
            # Ensure the container exists. If it already has an immutability
            # policy this call is a no-op; if it doesn't, the policy must be
            # applied separately (az storage container immutability-policy create).
            cc = _blob_service_client.get_container_client(_AUDIT_CONTAINER)
            try:
                cc.create_container()
                logger.info("Audit Blob container created: %s", _AUDIT_CONTAINER)
            except Exception:
                pass  # container already exists

            logger.info("Audit Blob client ready → container=%s", _AUDIT_CONTAINER)
            return _blob_service_client

        except ImportError:
            if not _blob_warned_once:
                logger.warning(
                    "azure-storage-blob not installed — audit writing to %s",
                    _AUDIT_LOCAL_PATH,
                )
                _blob_warned_once = True
            return None
        except Exception as exc:
            if not _blob_warned_once:
                logger.error("Audit Blob init failed (%s) — writing to %s", exc, _AUDIT_LOCAL_PATH)
                _blob_warned_once = True
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _truncate(text: str | None, head: int = 32, tail: int = 32) -> str:
    """Return first ``head`` + last ``tail`` chars separated by ``[…]``."""
    if not text:
        return ""
    if len(text) <= head + tail:
        return text
    return text[:head] + "[…]" + text[-tail:]


def _current_trace_id() -> str:
    """Pull the active span's trace_id (hex, no dashes).

    Returns empty string when no active span or when OTel SDK is absent.
    """
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        tid = ctx.trace_id
        return format(tid, "032x") if tid else ""
    except Exception:
        return ""


def _write_to_blob(blob_name: str, record: str) -> None:
    """Append one JSONL record to an Azure Blob. Each trace_id gets its own
    blob so concurrent writers don't collide. WORM immutability is enforced
    at the container level via az CLI / Terraform (not this code).
    """
    client = _get_blob_service_client()
    if client is None:
        return

    try:
        blob = client.get_container_client(_AUDIT_CONTAINER).get_blob_client(blob_name)
        # append_block is only available on Append Blob type.  We can't
        # convert an existing Block Blob, so write a new blob per record.
        # For true append semantics in production use Azure Data Lake Gen2 or
        # a dedicated append-blob container.
        blob.upload_blob(
            record.encode("utf-8"),
            overwrite=False,  # safety: never overwrite an existing audit record
        )
    except Exception as exc:
        logger.error("Audit Blob write failed (%s) — falling back to local file", exc)
        _write_to_local(record)


def _write_to_local(record: str) -> None:
    try:
        with _local_file_lock, open(_AUDIT_LOCAL_PATH, "a", encoding="utf-8") as f:
            f.write(record + "\n")
    except Exception as exc:
        logger.error("Audit local file write failed: %s", exc)


def _build_blob_name(trace_id: str, request_id: str) -> str:
    now = datetime.now(timezone.utc)
    prefix = now.strftime("%Y/%m/%d")
    # Fall back to request_id if trace_id is empty (tracing disabled).
    key = trace_id or request_id
    return f"{prefix}/{key}.jsonl"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def log_prompt(
    event: dict[str, Any],
    prompt_text: str | None = None,
    response_text: str | None = None,
    prompt_pii: Any = None,
    response_pii: Any = None,
) -> None:
    """Emit the dual-sink prompt log for one event.

    Parameters
    ----------
    event:
        The LLM event dict from generate_event().
    prompt_text / response_text:
        Raw (pre-redaction) text. Pass ``None`` when not yet available.
    prompt_pii / response_pii:
        ``RedactionResult`` from ``pii_scanner.scan()``.  If not provided,
        the function hashes the raw text directly.
    """
    if not _PROMPT_LOG_ENABLED:
        return

    trace_id   = _current_trace_id()
    request_id = event.get("request_id", "")

    prompt_hash  = prompt_pii.original_hash  if prompt_pii  else _sha256(prompt_text)
    response_hash = response_pii.original_hash if response_pii else _sha256(response_text)

    redacted_prompt   = prompt_pii.redacted_text   if prompt_pii   else (prompt_text or "")
    redacted_response = response_pii.redacted_text if response_pii else (response_text or "")

    # ── Loki (low-fidelity, searchable) ──────────────────────────────────
    loki_record: dict[str, Any] = {
        "event_type":          "prompt_log_event",
        "request_id":          request_id,
        "session_id":          event.get("session_id"),
        "turn_number":         event.get("turn_number"),
        "trace_id":            trace_id,
        "tenant_id":           event.get("client_name"),
        "model_name":          event.get("model_name"),
        "operation_name":      event.get("operation_name"),
        "data_classification": event.get("data_classification"),
        "prompt_hash":         prompt_hash,
        "prompt_truncated":    _truncate(redacted_prompt),
        "response_hash":       response_hash,
        "response_truncated":  _truncate(redacted_response),
        "pii_detected":        (prompt_pii.pii_detected if prompt_pii else False)
                               or (response_pii.pii_detected if response_pii else False),
        "pii_entity_counts":   {
            **(prompt_pii.entity_counts   if prompt_pii   else {}),
            **(response_pii.entity_counts if response_pii else {}),
        },
        "status":              event.get("status"),
        "cost_usd":            event.get("cost_usd"),
        "timestamp":           datetime.now(timezone.utc).isoformat(),
    }
    logging.getLogger("generator.prompt_log").info(
        "prompt_log_event", extra=loki_record,
    )

    # ── WORM Blob (full forensic record, async, fire-and-forget) ─────────
    blob_record = {
        **loki_record,
        "prompt_text":         prompt_text,      # original — may contain PII
        "response_text":       response_text,    # original — may contain PII
        "prompt_redacted":     redacted_prompt,
        "response_redacted":   redacted_response,
        "pii_backend":         (prompt_pii.backend if prompt_pii else "none"),
    }
    blob_name = _build_blob_name(trace_id, request_id)
    blob_json = json.dumps(blob_record, default=str)

    client = _get_blob_service_client()
    if client is not None:
        threading.Thread(
            target=_write_to_blob,
            args=(blob_name, blob_json),
            daemon=True,
            name=f"audit-blob-{request_id[:8]}",
        ).start()
    else:
        _write_to_local(blob_json)
