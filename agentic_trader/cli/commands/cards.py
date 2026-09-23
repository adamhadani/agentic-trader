from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import click

from agentic_trader.cli.utils import coro
from agentic_trader.config import AppConfig, load_config
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.outcomes import label_journaled, summarize
from agentic_trader.runtime import state_directory
from agentic_trader.storage.db import SignalDatabase


__all__ = ["cards_group"]


@click.group("cards", help="Suggestion-card evidence: read-only reports over journaled scans")
def cards_group() -> None:
    """Suggestion-card evidence commands."""


def build_bar_source(config: AppConfig) -> AlpacaDataProvider:
    """The suggestion scan's own market-data provider path: raw adjustment, the configured feed."""
    return AlpacaDataProvider(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        feed=config.market_data.alpaca_feed,
        request_timeout=config.market_data.timeout_seconds,
        evidence=BarEvidenceStore(state_directory() / "market-data", config.market_data.evidence),
    )


async def _scan_ranked_events(db: SignalDatabase, days: int, *, now: datetime) -> list[dict[str, Any]]:
    """Every ``scan_candidates_ranked`` event journaled on each ET calendar date in the window.

    Task 7's ``_journal_scan_ranking`` stamps one event per stream ``scan/{et_date}``;
    the reader has no range/prefix query, so this walks each date's exact stream.
    """
    et_today = now.astimezone(ET_TZ).date()
    events: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        events.extend(event for event in day_events if event.get("kind") == EventKind.SCAN_CANDIDATES_RANKED)
    return events


@cards_group.command(
    "outcomes", help="Label journaled scan_candidates_ranked outcomes; read-only, no orders or Telegram"
)
@click.option("--days", type=click.IntRange(1, 90), default=30, show_default=True, help="ET calendar days to inspect")
@coro
async def outcomes_cmd(days: int) -> None:
    config = load_config()
    db = SignalDatabase(config=config)
    try:
        now = datetime.now(UTC)
        events = await _scan_ranked_events(db, days, now=now)
        bars = build_bar_source(config)
        frame = label_journaled(events, bars, max_hold_sessions=20, cost_bps=5.0, now=now)
        click.echo(json.dumps(summarize(frame), indent=2, default=str))
        if frame.empty:
            click.echo(f"No scan_candidates_ranked events in the last {days} day(s).")
        else:
            click.echo(frame.drop(columns=["decided_at"]).to_string(index=False))
    finally:
        await db.engine.dispose()
