from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import click

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.execution.durable import EventKind
from agentic_trader.execution.release_exit import RELEASE_EXIT_STREAM
from agentic_trader.market.session import ET_TZ
from agentic_trader.presentation.scorecard import format_scorecard
from agentic_trader.research.setups.execution_evidence import summarize_execution
from agentic_trader.research.setups.outcomes import (
    CF_COLUMN_PREFIX,
    filing_veto_summary,
    release_exit_summary,
    scan_supply,
)
from agentic_trader.research.setups.scorecard import (
    build_scorecard,
    decision_events,
    insider_watch_summary,
    native_outcomes,
    pead_probe_summary,
    sip_reader,
)
from agentic_trader.research.setups.sources import (
    SIP_HISTORY_DELAY,
    build_bar_source,
    build_daily_bar_source,
    scan_ranked_events,
)
from agentic_trader.storage.db import SignalDatabase


__all__ = ["cards_group"]


@click.group("cards", help="Suggestion-card evidence: read-only reports over journaled scans")
def cards_group() -> None:
    """Suggestion-card evidence commands."""


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
        native = await native_outcomes(
            db, config, events, build_bar_source(config), build_daily_bar_source(config, now=now), now=now
        )
        frame, execution, summary = native.frame, native.execution, native.summary
        summary["supply"] = scan_supply(events)
        summary["filing_veto"] = filing_veto_summary(events, frame)
        summary["release_exit"] = release_exit_summary(
            await db.workflows.events(RELEASE_EXIT_STREAM),
            since=now.astimezone(ET_TZ).date() - timedelta(days=days - 1),
        )
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

        # The PEAD and insider watch records are labelled on SIP, never the configured feed (``sip_reader``).
        signals_since = now - timedelta(days=days)
        sip = sip_reader(config, now - SIP_HISTORY_DELAY)

        payloads = await decision_events(db, days, now=now, kind=EventKind.PEAD_DECISION)
        pead = await pead_probe_summary(db, config, payloads, sip=sip, now=now, signals_since=signals_since)
        if pead is not None:
            click.echo("PEAD probe:")
            click.echo(json.dumps(pead, indent=2, default=str))

        watch_payloads = await decision_events(db, days, now=now, kind=EventKind.INSIDER_WATCH_DECISION)
        watch = await insider_watch_summary(db, config, watch_payloads, sip=sip, now=now, signals_since=signals_since)
        if watch is not None:
            click.echo("Insider watch:")
            click.echo(json.dumps({"insider_watch": watch}, indent=2, default=str))
    finally:
        await db.engine.dispose()


@cards_group.command(
    "scorecard",
    help="Weekly per-lane scorecard (native, PEAD probe, insider watch); read-only, sends nothing",
)
@click.option(
    "--days",
    type=click.IntRange(1, 90),
    default=7,
    show_default=True,
    help="New York days of activity (the realized and counterfactual records trail 90 days)",
)
@coro
async def scorecard_cmd(days: int) -> None:
    config = load_config()
    db = SignalDatabase(config=config)
    try:
        card = await build_scorecard(
            db, config, now=datetime.now(UTC), activity_days=days, bars=build_bar_source(config)
        )
        click.echo(format_scorecard(card))
    finally:
        await db.engine.dispose()
