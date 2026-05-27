"""Tests for the lightweight health/readiness server."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from generator import health_server


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode()) if exc.fp else {}
        return exc.code, body


def _reset_state() -> None:
    health_server._state["last_batch_at"] = 0.0
    health_server._state["ready"] = False
    health_server._state["publisher_healthy"] = True
    health_server._state["started_at"] = time.time()


def test_liveness_before_first_batch():
    _reset_state()
    port = _free_port()
    server = health_server.start(port)
    assert server is not None
    try:
        time.sleep(0.1)
        status, body = _get(f"http://127.0.0.1:{port}/healthz")
        assert status == 200
        assert body["status"] == "alive"
    finally:
        server.shutdown()


def test_readiness_flips_after_heartbeat():
    _reset_state()
    port = _free_port()
    server = health_server.start(port)
    assert server is not None
    try:
        time.sleep(0.1)
        status, _ = _get(f"http://127.0.0.1:{port}/readyz")
        assert status == 503

        health_server.heartbeat(publisher_healthy=True)
        status, body = _get(f"http://127.0.0.1:{port}/readyz")
        assert status == 200
        assert body["ready"] is True
    finally:
        server.shutdown()


def test_unknown_path_returns_404():
    _reset_state()
    port = _free_port()
    server = health_server.start(port)
    assert server is not None
    try:
        time.sleep(0.1)
        status, body = _get(f"http://127.0.0.1:{port}/nope")
        assert status == 404
        assert body["error"] == "not found"
    finally:
        server.shutdown()
