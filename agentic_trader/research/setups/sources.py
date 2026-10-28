"""Read paths for journaled full-universe-scan evidence, shared by the CLI and the daemon.

``copilot cards outcomes`` and the daemon's ``card_stats`` worker both read the
``scan_candidates_ranked`` journal and label it on the configured feed; neither imports the other.
Both read beta and per-name cost inputs from SIP daily bars that end ``SIP_HISTORY_DELAY`` before now.
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
    import pandas as pd

    from agentic_trader.config import AppConfig
    from agentic_trader.research.setups.runner import BarSource
    from agentic_trader.storage.db import SignalDatabase

__all__ = ["SIP_HISTORY_DELAY", "DelayedBars", "build_bar_source", "build_daily_bar_source", "scan_ranked_events"]

# Without the SIP subscription, historical SIP bars must end at least 15 minutes ago (an end of now is HTTP 403:
# docs/research-data-sources.md); one minute of margin.
SIP_HISTORY_DELAY = timedelta(minutes=16)


class DelayedBars:
    """A bar source whose every read ends no later than ``through`` (the delayed-SIP bound)."""

    def __init__(self, provider: BarSource, through: datetime):
        self.provider, self.through = provider, through

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, adjustment: str
    ) -> pd.DataFrame:
        return self.provider.fetch_bars(symbol, timeframe, start, min(end, self.through), adjustment=adjustment)


def build_bar_source(config: AppConfig, *, feed: str | None = None) -> AlpacaDataProvider:
    """The suggestion scan's own market-data provider path: raw adjustment, the configured feed.

    ``feed`` overrides ``config.market_data.alpaca_feed`` when given.
    """
    return AlpacaDataProvider(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        feed=feed if feed is not None else config.market_data.alpaca_feed,
        request_timeout=config.market_data.timeout_seconds,
        evidence=BarEvidenceStore(state_directory() / "market-data", config.market_data.evidence),
    )


def build_daily_bar_source(config: AppConfig, now: datetime) -> DelayedBars:
    """The daily-bar source for beta and cost inputs: SIP in every caller, every read ending ``SIP_HISTORY_DELAY``
    before ``now``."""
    return DelayedBars(build_bar_source(config, feed="sip"), now - SIP_HISTORY_DELAY)


async def scan_ranked_events(db: SignalDatabase, days: int, *, now: datetime) -> list[dict[str, Any]]:
    """Every ``scan_candidates_ranked`` event journaled on each ET calendar date in the window.

    ``_journal_scan_ranking`` stamps one event per scan under stream ``scan/{et_date}`` (a date
    may hold more than one, since a session may run more than one full-universe scan); the reader
    has no range/prefix query, so this walks each date's exact stream.
    """
    et_today = now.astimezone(ET_TZ).date()
    events: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        events.extend(event for event in day_events if event.get("kind") == EventKind.SCAN_CANDIDATES_RANKED)
    return events
