from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import click
import pandas as pd

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import EVENT_COLUMNS
from agentic_trader.research.apriori.pead_study import label_events
from agentic_trader.research.apriori.probe_outcomes import decision_frame, probe_signals, summarize_probe
from agentic_trader.research.apriori.watch_contract import frozen_watch_contract
from agentic_trader.research.apriori.watch_outcomes import (
    completed_bars,
    decision_frame as decision_frame_watch,
    label_watch_events,
    summarize_watch,
    watch_bars,
    watch_signals,
)
from agentic_trader.research.setups.execution_evidence import collect_execution, summarize_execution
from agentic_trader.research.setups.outcomes import (
    CF_COLUMN_PREFIX,
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    attach_execution,
    label_journaled,
    scan_supply,
    summarize,
)
from agentic_trader.research.setups.sources import (
    SIP_HISTORY_DELAY,
    DelayedBars,
    build_bar_source,
    build_daily_bar_source,
    scan_ranked_events,
)
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


__all__ = ["cards_group"]


@click.group("cards", help="Suggestion-card evidence: read-only reports over journaled scans")
def cards_group() -> None:
    """Suggestion-card evidence commands."""


async def _decision_events(db: SignalDatabase, days: int, *, now: datetime, kind: EventKind) -> list[dict[str, Any]]:
    """Every ``kind`` decision event's payload journaled on each ET calendar date in the window.

    Mirrors ``scan_ranked_events``' per-day stream walk, but returns the journaled
    payload itself (``decision_frame`` consumes payloads, not the wrapped event).
    """
    et_today = now.astimezone(ET_TZ).date()
    payloads: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        payloads.extend(event.get("payload", event) for event in day_events if event.get("kind") == kind)
    return payloads


@cards_group.command(
    "outcomes", help="Label journaled scan_candidates_ranked outcomes; read-only, no orders or Telegram"
)
@click.option(
    "--days",
    type=click.IntRange(1, 180),
    default=30,
    show_default=True,
    help="ET calendar days to inspect (up to 180, the shipped card_policy.stats_window_days)",
)
@coro
async def outcomes_cmd(days: int) -> None:
    config = load_config()
    db = SignalDatabase(config=config)
    try:
        now = datetime.now(UTC)
        events = await scan_ranked_events(db, days, now=now)
        bars = build_bar_source(config)
        frame = label_journaled(
            events,
            bars,
            daily_bars=build_daily_bar_source(config, now=now),
            max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
            cost_bps=DEFAULT_COST_BPS_PER_SIDE,
            now=now,
            max_requests_per_minute=config.market_data.max_requests_per_minute,
        )
        execution = await collect_execution(db, frame)
        summary = summarize(
            attach_execution(frame, execution),
            asset_classes=config.universe.outcome_asset_classes(),
            slots=config.scheduler.suggestion_scan_times_et,
        )
        summary["supply"] = scan_supply(events)
        summary["execution"] = summarize_execution(execution)
        click.echo(json.dumps(summary, indent=2, default=str))
        if frame.empty:
            click.echo(f"No journaled candidates in {days} day(s) ({len(events)} scan events).")
        else:
            hidden = [c for c in frame.columns if c.startswith(CF_COLUMN_PREFIX)] + [
                "decided_at",
                "entry_time",
                "exit_time",
            ]
            click.echo(frame.drop(columns=hidden).to_string(index=False))
        if not execution.empty:
            click.echo("Execution evidence:")
            click.echo(execution.to_string(index=False))

        # The PEAD and insider watch records are labelled on SIP, the feed their studies (and the watch card's
        # +0.087R line) were measured on, never the configured feed: one provider, built on first use. Every
        # read ends at ``sip_through`` (delayed SIP), which is also both blocks' maturity cutoff.
        sip_through = now - SIP_HISTORY_DELAY
        sip_provider: DelayedBars | None = None

        def sip() -> DelayedBars:
            nonlocal sip_provider
            if sip_provider is None:
                provider = AlpacaDataProvider(
                    api_key=config.alpaca_api_key,
                    api_secret=config.alpaca_api_secret,
                    feed="sip",
                    request_timeout=config.market_data.timeout_seconds,
                )
                sip_provider = DelayedBars(provider, sip_through)
            return sip_provider

        payloads = await _decision_events(db, days, now=now, kind=EventKind.PEAD_DECISION)
        if payloads:
            entry = load_pead_entry(Path(config.apriori.pead_entry_path)).entry
            decisions = decision_frame(payloads)
            bar_failures: dict[str, str] = {}
            hourly: dict[str, pd.DataFrame] = {}
            if not decisions.empty:
                pead_source = sip()
                pacer = RequestPacer(max(1, config.market_data.max_requests_per_minute))
                for symbol, group in decisions.groupby("symbol"):
                    start = datetime.combine(min(group["session"]) - timedelta(days=7), time.min, tzinfo=UTC)
                    await asyncio.to_thread(pacer.acquire)
                    try:
                        read = await asyncio.to_thread(
                            pead_source.fetch_bars, symbol, "1h", start, sip_through, adjustment="all"
                        )
                        hourly[symbol] = completed_bars(read, sip_through)
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

        watch_payloads = await _decision_events(db, days, now=now, kind=EventKind.INSIDER_WATCH_DECISION)
        if watch_payloads:
            watch = decision_frame_watch(watch_payloads)
            contract = frozen_watch_contract()
            pacer = RequestPacer(max(1, config.market_data.max_requests_per_minute))
            watch_source = sip()

            def fetch(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
                pacer.acquire()
                return watch_source.fetch_bars(symbol, timeframe, start, end, adjustment="all")

            watch_data = await asyncio.to_thread(watch_bars, fetch, watch, sip_through)
            watch_labels = label_watch_events(
                watch,
                watch_data.hourly,
                watch_data.daily,
                watch_data.market_hourly,
                watch_data.market_daily,
                contract.spec.bracket,
                contract.spec.decision_cost_bps,
                as_of=sip_through,
            )
            watch_summary = summarize_watch(watch, watch_labels, await watch_signals(db, now - timedelta(days=days)))
            watch_summary["bar_failures"] = watch_data.failures
            click.echo("Insider watch:")
            click.echo(json.dumps({"insider_watch": watch_summary}, indent=2, default=str))
    finally:
        await db.engine.dispose()
