"""Tiny liveness/readiness HTTP server.

Run on a separate port from the Prometheus metrics endpoint so that
``/healthz`` cannot be DoS'd by a misbehaving Prometheus scrape, and
so that probe failures cannot mask metric scrape failures (and vice
versa).

The probe is intentionally cheap: it returns 200 if the runner has
reported a heartbeat within ``HEALTH_STALE_AFTER_S`` seconds, and 503
otherwise. Container Apps / Kubernetes can then restart the pod.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger(__name__)

# Treat the runner as wedged if no batch has completed in this many seconds.
HEALTH_STALE_AFTER_S: float = float(os.getenv("HEALTH_STALE_AFTER_S", "120"))

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "started_at":        time.time(),
    "last_batch_at":     0.0,
    "publisher_healthy": True,
    "ready":             False,
}


def heartbeat(publisher_healthy: bool = True) -> None:
    """Call from the runner after every successful batch."""
    with _state_lock:
        _state["last_batch_at"]     = time.time()
        _state["publisher_healthy"] = publisher_healthy
        _state["ready"]             = True


def _snapshot() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # silence stdlib access log
        logger.debug("health probe " + fmt, *args)

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        snap = _snapshot()
        now = time.time()
        last = snap["last_batch_at"]
        stale = (last == 0.0) or (now - last > HEALTH_STALE_AFTER_S)

        if self.path in ("/healthz", "/livez"):
            # Liveness: process is up. Restart only if it's been silent for
            # a long time AND has never become ready (likely deadlock).
            wedged = stale and snap["ready"]
            self._respond(503 if wedged else 200, {
                "status":            "wedged" if wedged else "alive",
                "uptime_s":          round(now - snap["started_at"], 1),
                "last_batch_age_s":  round(now - last, 1) if last else None,
            })
        elif self.path in ("/readyz", "/ready"):
            # Readiness: we've completed at least one batch and publisher is healthy.
            ready = snap["ready"] and snap["publisher_healthy"] and not stale
            self._respond(200 if ready else 503, {
                "ready":             ready,
                "publisher_healthy": snap["publisher_healthy"],
                "last_batch_age_s":  round(now - last, 1) if last else None,
            })
        else:
            self._respond(404, {"error": "not found", "path": self.path})

    def _respond(self, code: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start(port: int) -> ThreadingHTTPServer | None:
    """Start the health server in a background daemon thread."""
    if port <= 0:
        logger.info("Health server disabled (HEALTH_PORT<=0)")
        return None
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except OSError as exc:
        logger.error("Health server bind failed on port %d: %s", port, exc)
        return None
    threading.Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,
    ).start()
    logger.info("Health server listening on :%d (/healthz, /readyz)", port)
    return server
