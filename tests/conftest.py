"""Pytest configuration.

Forces a non-prod environment + mock mode so tests don't accidentally try
to talk to Event Hubs even if a developer has credentials in their shell.
"""
import os
import sys

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("ALLOW_MOCK_MODE", "true")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
