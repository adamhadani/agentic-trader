"""Research-only FINRA daily short-sale volume acquisition (digest-pinned cache); no database, no trading."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path

import click
import httpx

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.market.session import DeterministicCalendarProvider
from agentic_trader.research.short_volume import (
    REQUESTS_PER_SECOND,
    ShortVolumeStore,
    acquire_short_volume,
)


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


def _write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


@click.command("short-volume")
@click.option("--from", "start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--through", "end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--cache", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--allow-absent",
    "allow_absent",
    multiple=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="A session whose file FINRA does not serve; repeatable.",
)
@coro
async def short_volume_cmd(start, end, cache: Path, output: Path, allow_absent):
    """Fetch and cache FINRA Reg SHO daily short volume for each NYSE session; research only."""
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    start_date, end_date = start.date(), end.date()
    if end_date < start_date:
        raise click.ClickException("--through must not precede --from")
    calendar = await DeterministicCalendarProvider().get_calendar_range(start_date, end_date)
    sessions: list[date] = [day.date for day in calendar if day.is_trading_day]
    if not sessions:
        raise click.ClickException("No NYSE sessions in the window")
    store = ShortVolumeStore(cache / "short-volume")
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            manifest = await acquire_short_volume(
                client, sessions, store, allow_absent=[d.date() for d in allow_absent], pace=_pacer()
            )
    except (ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(str(exc)) from exc
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    _write_private(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str).encode())
    click.echo(
        f"{manifest['sessions_present']} present, {len(manifest['sessions_absent'])} absent sessions; "
        f"{output / 'manifest.json'}"
    )
