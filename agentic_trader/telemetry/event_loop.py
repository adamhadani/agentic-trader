"""Detect event-loop stalls regardless of which service caused them."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agentic_trader.config import TelemetryConfig
from agentic_trader.constants import AuditEventType
from agentic_trader.telemetry.collector import MetricsCollector


logger = logging.getLogger(__name__)


async def monitor_event_loop(
    config: TelemetryConfig,
    metrics: MetricsCollector,
    audit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    loop = asyncio.get_running_loop()
    while True:
        expected = loop.time() + config.event_loop_sample_seconds
        await asyncio.sleep(config.event_loop_sample_seconds)
        delay = max(0.0, loop.time() - expected)
        metrics.set_gauge("trader_event_loop_lag_seconds", delay)
        metrics.observe_histogram("trader_event_loop_lag_observed_seconds", delay)
        if delay >= config.event_loop_warning_seconds:
            logger.warning("Event loop stalled for %.3f seconds", delay)
            metrics.inc_counter("trader_event_loop_stalls_total")
            try:
                await audit(AuditEventType.EVENT_LOOP_STALL, {"delay_seconds": delay})
            except Exception:
                logger.exception("Could not persist event-loop stall audit")
