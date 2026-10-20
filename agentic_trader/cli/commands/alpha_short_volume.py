"""Research-only FINRA daily short-sale volume acquisition (digest-pinned cache); no database, no trading."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path

import click
import httpx

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.market.session import AlpacaCalendarProvider
from agentic_trader.research.short_volume import (
    REQUESTS_PER_SECOND,
    ShortVolumeStore,
    acquire_short_volume,
    write_private,
)
from agentic_trader.transport.alpaca import BoundedTradingClient


def _pacer() -> Callable[[], Awaitable[None]]:
    interval = 1.0 / REQUESTS_PER_SECOND
    last = 0.0

    async def pace() -> None:
        nonlocal last
        wait = last + interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last = time.monotonic()

    return pace


async def _exchange_sessions(start: date, end: date) -> list[date]:
    """The sessions in ``[start, end]`` on Alpaca's exchange calendar, the calendar the pooled cube build uses.

    The offline calendar is not used: it lacks ad-hoc closures (the 2018-12-05 and 2025-01-09 days of
    mourning), which would otherwise be requested and recorded as absent sessions the build never reads.
    """
    config = await asyncio.to_thread(load_config)
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise click.ClickException("Alpaca credentials are required")
    trading = BoundedTradingClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        paper=config.alpaca_paper,
        request_timeout=config.market_data.timeout_seconds,
    )
    try:
        days = await AlpacaCalendarProvider(trading_client=trading).get_calendar_range(start, end)
    finally:
        trading._session.close()
    return [day.date for day in days if day.is_trading_day]


@click.command("short-volume")
@click.option("--from", "start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--through", "end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option(
    "--cache",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="The directory of daily files (DIR/YYYY-MM-DD.txt and .meta.json); pooled --short-volume takes the same DIR.",
)
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--allow-absent",
    "allow_absent",
    multiple=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="An exchange-calendar session inside the window whose file FINRA did not post; repeatable.",
)
@coro
async def short_volume_cmd(start, end, cache: Path, output: Path, allow_absent):
    """Fetch and cache FINRA Reg SHO daily short volume for each NYSE session (Alpaca's calendar); research only."""
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    start_date, end_date = start.date(), end.date()
    if end_date < start_date:
        raise click.ClickException("--through must not precede --from")
    sessions = await _exchange_sessions(start_date, end_date)
    if not sessions:
        raise click.ClickException("No NYSE sessions in the window")
    allowed = [moment.date() for moment in allow_absent]
    for day in allowed:
        if day not in sessions:
            raise click.ClickException(
                f"--allow-absent {day.isoformat()} is not an NYSE session in "
                f"[{start_date.isoformat()}, {end_date.isoformat()}]"
            )
    store = ShortVolumeStore(cache)
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            manifest = await acquire_short_volume(client, sessions, store, allow_absent=allowed, pace=_pacer())
    except (ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(str(exc)) from exc
    manifest["calendar"] = "alpaca"
    manifest["allow_absent"] = [day.isoformat() for day in allowed]
    manifest["command"] = list(sys.argv)
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    write_private(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str).encode())
    click.echo(
        f"{manifest['sessions_present']} present, {len(manifest['sessions_absent'])} absent sessions; "
        f"{output / 'manifest.json'}"
    )
