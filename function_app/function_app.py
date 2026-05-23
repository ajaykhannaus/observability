"""Azure Functions v2 timer-triggered app — fires every 30 seconds.

The generator/ package lives alongside this file in the container image.
"""
from __future__ import annotations

import logging
import os
import sys

import azure.functions as func

# Make the generator package importable from /home/site/wwwroot/generator/
_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in (_HERE, os.path.join(_HERE, "generator")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from generator.runner import run_one_batch  # noqa: E402

logger = logging.getLogger(__name__)

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0/30 * * * * *",
    arg_name="timer",
    run_on_startup=True,
    use_monitor=False,
)
def telemetry_batch(timer: func.TimerRequest) -> None:
    """Generate and publish one batch of synthetic telemetry events."""
    if timer.past_due:
        logger.warning("Timer is past due — processing immediately")

    try:
        summary = run_one_batch()
        logger.info("Batch complete: %s", summary)
    except Exception as exc:
        logger.error("Timer trigger batch failed: %s", exc)
        raise  # re-raise so the Functions host marks the invocation as failed
