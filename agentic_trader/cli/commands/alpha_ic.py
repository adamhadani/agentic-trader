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

from agentic_trader.cli.commands.alpha import _apriori_clients, _pooled_build, _pooled_protocol, research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.research.ic.arrays import load_spy
from agentic_trader.research.ic.checks import execute_checks
from agentic_trader.research.ic.gate import execute_gate
from agentic_trader.research.ic.protocol import load_composite
from agentic_trader.research.ic.sectors import UNKNOWN, acquire_sectors, load_ff12, load_sectors
from agentic_trader.research.insider_filings import _TIMEOUT_SECONDS, REQUESTS_PER_SECOND
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.entry import REPO_ROOT
from agentic_trader.research.short_volume import write_private


_COMPOSITE_HELP = "The frozen composite file"
_CACHE_HELP = "The pooled cache directory the cubes were built in; nothing is fetched into it"
_OUTPUT_HELP = "New private directory; no overwrite"


def _progress(message: str) -> None:
    click.echo(f"[ic] {message}", err=True)


def _refuse_existing(output: Path) -> None:
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")


async def _verified_pins(composite: Path):
    """The composite and what it pins: the sector map and both cohort protocols, each checked against its digest."""
    try:
        loaded = await asyncio.to_thread(load_composite, composite)
        protocol = loaded.protocol
        sectors = await asyncio.to_thread(load_sectors, REPO_ROOT / protocol.sectors.path, protocol.sectors.sha256)
        decisive = await _pooled_protocol(REPO_ROOT / protocol.decisive.protocol)
        secondary = await _pooled_protocol(REPO_ROOT / protocol.secondary.protocol)
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"{type(exc).__name__}: {exc}") from exc
    for name, pin, (pooled, _cohort) in (
        ("decisive", protocol.decisive, decisive),
        ("secondary", protocol.secondary, secondary),
    ):
        if pooled.sha256 != pin.sha256:
            raise click.ClickException(f"The {name} protocol is {pooled.sha256}, not the pinned {pin.sha256}")
    return loaded, sectors, decisive, secondary


def _cube_builder(clients, pooled, cache: Path):
    """An async build of a pooled protocol's cached cube and the SPY bars it lists; no bar is fetched."""
    loaded, cohort = pooled
    inner = _pooled_build(clients, cohort, loaded.protocol.cube_spec(), cache, require_cached=True)

    async def build():
        built = await inner()
        return built, await asyncio.to_thread(load_spy, built, cache)

    return build


def _echo(result: dict, completed: str) -> None:
    """The result's summary on stdout. ``reason`` is fixed text or a type name; a fault's message went to ``_progress``."""
    keys = ("status", "decision", "reason", "composite_sha256", "sectors_sha256", "cube_sha256")
    summary = {key: result[key] for key in keys if key in result}
    if "secondary" in result:
        summary["secondary_status"] = result["secondary"]["status"]
    click.echo(json.dumps(summary, default=str))
    if result.get("status") != completed:
        raise click.ClickException(f"{result.get('status')}: {result.get('reason')}")


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


@ic_group.command("checks")
@click.option(
    "--composite", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help=_COMPOSITE_HELP
)
@click.option("--cache", required=True, type=click.Path(path_type=Path), help=_CACHE_HELP)
@click.option("--output", required=True, type=click.Path(path_type=Path), help=_OUTPUT_HELP)
@coro
async def checks_cmd(composite: Path, cache: Path, output: Path):
    """C-IC and A-IC on the gate's cohort, target and window; research only. A failed check exits non-zero."""
    _refuse_existing(output)
    loaded, sectors, decisive, _secondary = await _verified_pins(composite)
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_checks(
            loaded,
            output,
            build=_cube_builder(clients, decisive, cache),
            sectors=sectors,
            environment=environment,
            progress=_progress,
        )
    _echo(result, "passed")


@ic_group.command("gate")
@click.option(
    "--composite", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help=_COMPOSITE_HELP
)
@click.option(
    "--checks",
    "checks_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="The directory of `alpha ic checks`, run at this code revision",
)
@click.option("--cache", required=True, type=click.Path(path_type=Path), help=_CACHE_HELP)
@click.option("--output", required=True, type=click.Path(path_type=Path), help=_OUTPUT_HELP)
@coro
async def gate_cmd(composite: Path, checks_dir: Path, cache: Path, output: Path):
    """The composite's development gate; research only, authorizes nothing. A gate fail is a completed read, exit 0."""
    _refuse_existing(output)
    loaded, sectors, decisive, secondary = await _verified_pins(composite)
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_gate(
            loaded,
            output,
            checks_dir=checks_dir,
            build=_cube_builder(clients, decisive, cache),
            build_secondary=_cube_builder(clients, secondary, cache),
            sectors=sectors,
            environment=environment,
            progress=_progress,
        )
    _echo(result, "completed")
