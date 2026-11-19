"""Research-only SEC investor-day acquisition (digest-pinned cache); no database, no trading."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

import click
import httpx

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.data.edgar_http import TIMEOUT_SECONDS, edgar_pacer
from agentic_trader.research.investor_days import InvestorDayStore, acquire_investor_days
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.short_volume import write_private


def _client(user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": user_agent}, follow_redirects=False, timeout=TIMEOUT_SECONDS)


@click.group("investor-days")
def investor_days_group():
    """Investor-day study acquisition; research only."""


@investor_days_group.command("fetch")
@click.option("--cohort", "cohorts", multiple=True, required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--cache", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--from", "start", default="2016-01-01", type=click.DateTime(["%Y-%m-%d"]))
@click.option("--through", "end", default="2026-07-31", type=click.DateTime(["%Y-%m-%d"]))
@coro
async def fetch_cmd(cohorts: tuple[Path, ...], cache: Path, output: Path, start, end):
    """Fetch and cache SEC 8-K investor-day search hits and documents for the cohorts' issuers.

    Requires EDGAR_USER_AGENT (a declared contact, per the SEC fair-access policy).
    """
    user_agent = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not user_agent:
        raise click.ClickException(
            "EDGAR_USER_AGENT is required: the SEC's fair-access policy needs a declared contact"
        )
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    symbols = list(dict.fromkeys(s for path in cohorts for s in load_cohort(path).cohort.symbols))
    store = InvestorDayStore(cache)
    try:
        async with _client(user_agent) as client:
            manifest = await acquire_investor_days(
                client,
                symbols,
                store,
                user_agent=user_agent,
                filings_from=date(start.year, start.month, start.day),
                filings_through=date(end.year, end.month, end.day),
                pace=edgar_pacer(),
            )
    except (ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(str(exc)) from exc
    manifest["user_agent_set"] = True
    manifest["command"] = list(sys.argv)
    manifest["cohorts"] = [str(path) for path in cohorts]
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    write_private(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str).encode())
    click.echo(f"{manifest['issuers']} issuers, {manifest['hits']} hits; {output / 'manifest.json'}")
