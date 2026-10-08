"""Read paths for journaled suggestion-scan evidence, shared by the CLI and the daemon.

``copilot cards outcomes`` and the daemon's ``card_stats`` worker both read the
``scan_candidates_ranked`` journal and label it on the configured feed; neither imports the other.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.runtime import state_directory


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.storage.db import SignalDatabase

__all__ = ["build_bar_source", "scan_ranked_events"]


def build_bar_source(config: AppConfig) -> AlpacaDataProvider:
    """The suggestion scan's own market-data provider path: raw adjustment, the configured feed."""
    return AlpacaDataProvider(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        feed=config.market_data.alpaca_feed,
        request_timeout=config.market_data.timeout_seconds,
        evidence=BarEvidenceStore(state_directory() / "market-data", config.market_data.evidence),
    )


async def scan_ranked_events(db: SignalDatabase, days: int, *, now: datetime) -> list[dict[str, Any]]:
    """Every ``scan_candidates_ranked`` event journaled on each ET calendar date in the window.

    ``_journal_scan_ranking`` stamps one event per scan under stream ``scan/{et_date}`` (a date
    may hold more than one, since a session may run more than one suggestion scan); the reader
    has no range/prefix query, so this walks each date's exact stream.
    """
    et_today = now.astimezone(ET_TZ).date()
    events: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        events.extend(event for event in day_events if event.get("kind") == EventKind.SCAN_CANDIDATES_RANKED)
    return events
