from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import click
import pandas as pd

from agentic_trader.cli.utils import coro
from agentic_trader.config import AppConfig, load_config
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import EVENT_COLUMNS
from agentic_trader.research.apriori.pead_study import label_events
from agentic_trader.research.apriori.probe_outcomes import decision_frame, probe_signals, summarize_probe
from agentic_trader.research.setups.outcomes import (
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    label_journaled,
    summarize,
)
from agentic_trader.runtime import state_directory
from agentic_trader.storage.alpha import AlphaRepository
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

    ``_journal_scan_ranking`` stamps one event per scan under stream ``scan/{et_date}``
    (a date may hold more than one, since a session may run more than one suggestion
    scan); the reader has no range/prefix query, so this walks each date's exact stream.
    """
    et_today = now.astimezone(ET_TZ).date()
    events: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        events.extend(event for event in day_events if event.get("kind") == EventKind.SCAN_CANDIDATES_RANKED)
    return events


async def _pead_decision_events(db: SignalDatabase, days: int, *, now: datetime) -> list[dict[str, Any]]:
    """Every ``pead_decision`` event's payload journaled on each ET calendar date in the window.

    Mirrors ``_scan_ranked_events``' per-day stream walk, but returns the journaled
    payload itself (``decision_frame`` consumes payloads, not the wrapped event).
    """
    et_today = now.astimezone(ET_TZ).date()
    payloads: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        payloads.extend(
            event.get("payload", event) for event in day_events if event.get("kind") == EventKind.PEAD_DECISION
        )
    return payloads


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
        frame = label_journaled(
            events,
            bars,
            max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
            cost_bps=DEFAULT_COST_BPS_PER_SIDE,
            now=now,
            max_requests_per_minute=config.market_data.max_requests_per_minute,
        )
        click.echo(json.dumps(summarize(frame), indent=2, default=str))
        if frame.empty:
            click.echo(f"No scan_candidates_ranked events in the last {days} day(s).")
        else:
            click.echo(frame.drop(columns=["decided_at"]).to_string(index=False))

        payloads = await _pead_decision_events(db, days, now=now)
        if payloads:
            entry = load_pead_entry(Path(config.apriori.pead_entry_path)).entry
            decisions = decision_frame(payloads)
            bar_failures: dict[str, str] = {}
            hourly: dict[str, pd.DataFrame] = {}
            if not decisions.empty:
                sip = AlpacaDataProvider(
                    api_key=config.alpaca_api_key,
                    api_secret=config.alpaca_api_secret,
                    feed="sip",
                    request_timeout=config.market_data.timeout_seconds,
                )
                pacer = RequestPacer(max(1, config.market_data.max_requests_per_minute))
                for symbol, group in decisions.groupby("symbol"):
                    start = datetime.combine(min(group["session"]) - timedelta(days=7), time.min, tzinfo=UTC)
                    await asyncio.to_thread(pacer.acquire)
                    try:
                        hourly[symbol] = await asyncio.to_thread(
                            sip.fetch_bars, symbol, "1h", start, now, adjustment="all"
                        )
                    except Exception as exc:
                        bar_failures[symbol] = f"{type(exc).__name__}: {exc}"
            labels, _ = await asyncio.to_thread(label_events, decisions[list(EVENT_COLUMNS)], hourly, entry)
            labels = labels[(labels["direction"] == "LONG") & labels["is_leg"]] if not labels.empty else labels
            report = [
                r for r in await AlphaRepository(db.workflows).probe_report(now=now) if r.get("kind") == "apriori"
            ]
            summary = summarize_probe(
                decisions,
                labels,
                await probe_signals(db, now - timedelta(days=days)),
                report[0]["forward"] if report else None,
                report[0]["study_mean_r"] if report else None,
            )
            summary["bar_failures"] = bar_failures
            click.echo("PEAD probe:")
            click.echo(json.dumps(summary, indent=2, default=str))
    finally:
        await db.engine.dispose()
