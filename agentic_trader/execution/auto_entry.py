"""Paper auto-entry switch: the one resolver and the card marker rule."""

from typing import Any

from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.execution.durable import ScanTrigger


AUTO_ENTRY_KEY = "auto_entry"
AUTO_ENTRY_MARKER = {"mode": "paper"}


def auto_entry_active(config: AppConfig) -> bool:
    """True only on Alpaca paper with session-bounded entries and the switch on; the simulator is inactive."""
    return (
        config.execution.auto_entry == "paper"
        and config.execution_mode == ExecutionMode.ALPACA
        and config.alpaca_paper
        and config.execution.native_entry_lifetime == "session"
    )


def auto_entry_marker(
    config: AppConfig,
    *,
    trigger: ScanTrigger,
    is_catalog: bool,
    alpha: bool,
    lifetime_marker: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The marker an eligible card records: a native, lifetime-marked card of a scheduled suggestion scan."""
    if (
        not auto_entry_active(config)
        or trigger != ScanTrigger.SUGGESTION_SCAN
        or is_catalog
        or alpha
        or lifetime_marker is None
    ):
        return None
    return dict(AUTO_ENTRY_MARKER)
