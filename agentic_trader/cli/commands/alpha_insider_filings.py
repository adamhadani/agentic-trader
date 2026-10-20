"""Research-only SEC insider-transactions acquisition (digest-pinned cache); no database, no trading."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import click
import httpx

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.research.insider_filings import (
    _TIMEOUT_SECONDS,
    REQUESTS_PER_SECOND,
    InsiderFilingStore,
    acquire_insider_filings,
    parse_quarter,
    quarters_between,
)
from agentic_trader.research.short_volume import write_private


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


@click.command("insider-filings")
@click.option("--from", "start", required=True, help="First quarter, e.g. 2015q1.")
@click.option("--through", "end", required=True, help="Last quarter, e.g. 2026q3.")
@click.option(
    "--cache",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="The directory of quarter archives and the ticker map (digest-pinned).",
)
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@coro
async def insider_filings_cmd(start: str, end: str, cache: Path, output: Path):
    """Fetch and cache the SEC Form 3/4/5 insider-transactions data sets; research only.

    Requires EDGAR_USER_AGENT (a declared contact, per the SEC fair-access policy).
    """
    user_agent = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not user_agent:
        raise click.ClickException(
            "EDGAR_USER_AGENT is required: the SEC's fair-access policy needs a declared contact"
        )
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    try:
        quarters = quarters_between(parse_quarter(start), parse_quarter(end))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    store = InsiderFilingStore(cache)
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": user_agent}, follow_redirects=False, timeout=_TIMEOUT_SECONDS
        ) as client:
            manifest = await acquire_insider_filings(client, quarters, store, pace=_pacer())
    except (ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(str(exc)) from exc
    manifest["user_agent_set"] = True
    manifest["command"] = list(sys.argv)
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    write_private(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str).encode())
    click.echo(
        f"{manifest['quarters_fetched']} fetched, {manifest['quarters_reused']} reused quarters; "
        f"{output / 'manifest.json'}"
    )
