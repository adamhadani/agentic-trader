"""Research-only rank IC lane commands; no database, broker or Telegram."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path

import click
import httpx

from agentic_trader.cli.utils import coro
from agentic_trader.research.ic.sectors import UNKNOWN, acquire_sectors, load_ff12
from agentic_trader.research.insider_filings import _TIMEOUT_SECONDS, REQUESTS_PER_SECOND
from agentic_trader.research.pooled.cohort import load_cohort
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


@click.group("ic")
def ic_group():
    """Rank IC lane (research only)."""


@ic_group.command("sectors")
@click.option("--cohort", "cohorts", multiple=True, required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--ff12", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@coro
async def sectors_cmd(cohorts: tuple[Path, ...], ff12: Path, output: Path):
    """Map cohort tickers to Fama-French 12 groups through SEC SIC codes; research only.

    Requires EDGAR_USER_AGENT (a declared contact, per the SEC fair-access policy).
    """
    user_agent = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not user_agent:
        raise click.ClickException(
            "EDGAR_USER_AGENT is required: the SEC's fair-access policy needs a declared contact"
        )
    if output.exists():
        raise click.ClickException(f"Output exists: {output}")
    loaded = [load_cohort(path) for path in cohorts]
    symbols = sorted({symbol for item in loaded for symbol in item.cohort.symbols})
    ff12_map = load_ff12(ff12)
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": user_agent}, follow_redirects=False, timeout=_TIMEOUT_SECONDS
        ) as client:
            document = await acquire_sectors(
                symbols,
                client=client,
                pace=_pacer(),
                ff12=ff12_map,
                progress=lambda done, total: (
                    click.echo(f"{done}/{total}", err=True) if done % 100 == 0 or done == total else None
                ),
            )
    except (ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(type(exc).__name__) from exc
    document["cohort_sha256s"] = [item.sha256 for item in loaded]
    document["command"] = ["copilot", *sys.argv[1:]]  # no local path: this file is committed
    output.parent.mkdir(parents=True, exist_ok=True)
    write_private(output, json.dumps(document, indent=1, sort_keys=True).encode() + b"\n")
    table = document["symbols"]
    counts = Counter(entry["group"] for entry in table.values())
    for group, count in sorted(counts.items()):
        click.echo(f"{group}: {count}")
    click.echo(f"failures: {len(document['failures'])}")
    for item in loaded:
        mapped = sum(1 for s in item.cohort.symbols if table.get(s, {}).get("group", UNKNOWN) != UNKNOWN)
        click.echo(
            f"{item.path.name}: {mapped}/{len(item.cohort.symbols)} mapped ({mapped / len(item.cohort.symbols):.1%})"
        )
