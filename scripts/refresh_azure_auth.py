#!/usr/bin/env python3
"""Azure token refresh daemon for AI Gateway telemetry POC.

Refreshes two Azure AD tokens every REFRESH_INTERVAL_S seconds (default 50 min):
  1. Write token  → /tmp/azure_prom_write_token.txt   (Prometheus reads for remote_write)
  2. Read token   → Grafana datasource patched via API  (Grafana queries Azure Prom)

Requires: az CLI logged in  OR  AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID env vars.
Run once to seed tokens before starting Prometheus.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WRITE_TOKEN_FILE   = Path(os.getenv("AZURE_WRITE_TOKEN_FILE", "/tmp/azure_prom_write_token.txt"))
REFRESH_INTERVAL_S = int(os.getenv("REFRESH_INTERVAL_S", str(50 * 60)))  # 50 minutes

# Azure AD
TENANT_ID     = os.getenv("AZURE_TENANT_ID",     "")
CLIENT_ID     = os.getenv("AZURE_CLIENT_ID",     "")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")

# Azure Managed Prometheus
PROM_QUERY_URL = os.getenv(
    "AZURE_PROM_QUERY_URL",
    "",   # set AZURE_PROM_QUERY_URL in .env (see .env.example)
)
PROM_READ_AUDIENCE  = "https://prometheus.monitor.azure.com"   # Grafana query
PROM_WRITE_AUDIENCE = "https://monitor.azure.com/"              # Prometheus remote_write ingestion
PROM_AUDIENCE = PROM_WRITE_AUDIENCE  # default for backwards compat

# Grafana
GRAFANA_URL      = os.getenv("GRAFANA_URL",      "http://localhost:3000")
GRAFANA_USER     = os.getenv("GRAFANA_USER",     "admin")
GRAFANA_PASSWORD = os.getenv("GRAFANA_PASSWORD", "admin")
GRAFANA_DS_UID   = os.getenv("GRAFANA_DS_UID",   "cfmykhk0ub2m8d")   # azure-managed-prometheus


# ---------------------------------------------------------------------------
# Token acquisition
# ---------------------------------------------------------------------------

def _get_token_via_sp(audience: str) -> str:
    """OAuth2 client_credentials flow using service principal."""
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    scope = audience if audience.endswith("/.default") else f"{audience.rstrip('/')}/.default"
    body = (
        f"grant_type=client_credentials"
        f"&client_id={CLIENT_ID}"
        f"&client_secret={urllib.request.quote(CLIENT_SECRET)}"
        f"&scope={urllib.request.quote(scope)}"
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


def _get_token_via_cli(resource: str) -> str:
    """Fall back to Azure CLI (works when az login session is active)."""
    result = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", resource,
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"az CLI failed: {result.stderr.strip()}")
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("az CLI returned empty token")
    return token


def get_token(audience: str) -> str:
    """Obtain an Azure AD token for the given audience/resource."""
    if CLIENT_ID and CLIENT_SECRET:
        logger.info("Fetching token (SP) for %s", audience)
        return _get_token_via_sp(audience)
    logger.info("Fetching token (az CLI) for %s", audience)
    return _get_token_via_cli(audience)


# ---------------------------------------------------------------------------
# Apply tokens
# ---------------------------------------------------------------------------

def write_token_file(token: str) -> None:
    """Write the bare token to the credentials_file Prometheus polls."""
    WRITE_TOKEN_FILE.write_text(token)
    logger.info("Write token → %s", WRITE_TOKEN_FILE)


def patch_grafana_datasource(token: str) -> None:
    """Update the Azure Managed Prometheus datasource in Grafana with the new token."""
    import base64
    credentials = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()

    payload = json.dumps({
        "uid":   GRAFANA_DS_UID,
        "name":  "azure-managed-prometheus",
        "type":  "prometheus",
        "url":   PROM_QUERY_URL,
        "access": "proxy",
        "basicAuth": False,
        "isDefault": False,
        "jsonData": {
            "httpMethod":      "GET",
            "timeInterval":    "10s",
            "httpHeaderName1": "Authorization",
        },
        "secureJsonData": {
            "httpHeaderValue1": f"Bearer {token}",
        },
    }).encode()

    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/datasources/uid/{GRAFANA_DS_UID}",
        data=payload,
        method="PUT",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {credentials}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.info("Grafana datasource patched: %s", result.get("message", "ok"))
    except urllib.error.HTTPError as exc:
        logger.warning("Grafana patch failed %s: %s", exc.code, exc.read().decode()[:200])


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def refresh_once() -> bool:
    """Fetch both tokens and apply everywhere. Returns True if both succeed."""
    ok = True

    # Write token — ingestion audience
    try:
        write_token = get_token(PROM_WRITE_AUDIENCE)
        write_token_file(write_token)
    except Exception as exc:
        logger.error("Write token refresh failed: %s", exc)
        ok = False

    # Read token — query audience → patch Grafana
    try:
        read_token = get_token(PROM_READ_AUDIENCE)
        patch_grafana_datasource(read_token)
    except Exception as exc:
        logger.error("Read token refresh failed: %s", exc)
        ok = False

    if ok:
        logger.info("Token refresh complete (next in %ds)", REFRESH_INTERVAL_S)
    return ok


def main() -> None:
    logger.info(
        "Azure auth refresh daemon starting | interval=%ds | SP=%s",
        REFRESH_INTERVAL_S,
        "yes" if CLIENT_ID else "no (az CLI)",
    )

    # Always refresh immediately on start
    if not refresh_once():
        logger.error("Initial token fetch failed — Prometheus remote_write will 401 until resolved")

    while True:
        time.sleep(REFRESH_INTERVAL_S)
        refresh_once()


if __name__ == "__main__":
    main()
