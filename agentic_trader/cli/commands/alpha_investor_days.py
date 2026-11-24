"""Research-only SEC investor-day acquisition (digest-pinned cache); no database, no trading."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import click
import httpx

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.data.edgar_http import TIMEOUT_SECONDS, edgar_pacer
from agentic_trader.research.apriori.catalog import IddayCohort, load_idday_entry
from agentic_trader.research.apriori.idday_events import candidate_legs, issuer_reports, sample_parser_check
from agentic_trader.research.apriori.idday_parser import load_event_table, parse_filings, save_event_table
from agentic_trader.research.investor_days import (
    InvestorDayStore,
    acquire_investor_days,
    check_coverage,
    content_sha256,
)
from agentic_trader.research.pooled.campaign import (
    EventsCampaignProtocol,
    InsiderCampaignProtocol,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.cohort import LoadedCohort, load_cohort
from agentic_trader.research.pooled.cube import load_cube
from agentic_trader.research.pooled.events import load_calendar_pages
from agentic_trader.research.pooled.runner import cube_cache_path
from agentic_trader.research.short_volume import write_private


DEFAULT_ENTRY = Path("config/research/apriori/idday-v1.json")


def _client(user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": user_agent}, follow_redirects=False, timeout=TIMEOUT_SECONDS)


def _progress(line: str) -> None:
    click.echo(line, err=True)


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
                progress=_progress,
            )
    except (ValueError, httpx.HTTPError, TimeoutError) as exc:
        raise click.ClickException(str(exc) or type(exc).__name__) from exc
    manifest["user_agent_set"] = True
    manifest["command"] = list(sys.argv)
    manifest["cohorts"] = [str(path) for path in cohorts]
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    write_private(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str).encode())
    click.echo(f"{manifest['issuers']} issuers, {manifest['hits']} hits; {output / 'manifest.json'}")


@investor_days_group.command("events")
@click.option("--cache", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def events_cmd(cache: Path, output: Path):
    """Parse cached filings into the frozen idday-parser-v1 event table (offline)."""
    if output.exists():
        raise click.ClickException(f"Output file exists: {output}")
    table = parse_filings(InvestorDayStore(cache))
    output.parent.mkdir(parents=True, exist_ok=True)
    save_event_table(table, output)
    counts = Counter(event.status for event in table.events)
    click.echo(f"events_sha256 {table.events_sha256}")
    click.echo(f"announced {counts['announced']} event_only {counts['event_only']}")


def _pinned_cohort(repo_root: Path, pinned: IddayCohort) -> tuple[Any, LoadedCohort]:
    """The entry's pooled protocol for one cohort and that protocol's cohort file, both digest-checked."""
    protocol_path = repo_root / pinned.pooled_protocol
    digest = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    if digest != pinned.pooled_protocol_sha256:
        raise ValueError(
            f"{pinned.pooled_protocol} has SHA-256 {digest}, but the entry pins {pinned.pooled_protocol_sha256}"
        )
    protocol = load_campaign_protocol(protocol_path).protocol
    expected = EventsCampaignProtocol if pinned.name == "v3" else InsiderCampaignProtocol
    if not isinstance(protocol, expected):
        raise ValueError(f"{pinned.pooled_protocol} is a {protocol.id} protocol, not the {pinned.name} one")  # noqa: TRY004 -- a CLI refusal
    cohort = load_cohort(repo_root / protocol.cohort)
    if cohort.sha256 != protocol.cohort_sha256:
        raise ValueError(f"the cohort file has SHA-256 {cohort.sha256}, but the protocol pins {protocol.cohort_sha256}")
    return protocol, cohort


def _parser_sample(
    cache: Path, events_file: Path, repo_root: Path, entry_file: Path, earnings_pages: Path, pooled_cache: Path
) -> dict[str, Any]:
    """The parser-check sample from the price-free candidate legs on the cached v3 cube's sessions."""
    entry = load_idday_entry(entry_file).entry
    store = InvestorDayStore(cache)
    table = load_event_table(events_file)
    if table.content_sha256 != content_sha256(store):
        raise ValueError("the event table was parsed from another acquisition (content_sha256 differs from --cache)")
    protocol, cohort = _pinned_cohort(repo_root, entry.cohorts[0])
    _, cohort_xu = _pinned_cohort(repo_root, entry.cohorts[1])
    # The merged cube's symbols are the two cohorts' symbols: a wrong cache is refused before any sampling.
    check_coverage(
        store.issuers(),
        store.unmapped(),
        (*cohort.cohort.symbols, *cohort_xu.cohort.symbols),
        what="cohorts v3 and xu",
    )
    spec = protocol.cube_spec()
    cube_path = cube_cache_path(pooled_cache, cohort, spec)
    if not cube_path.exists():
        raise ValueError(f"no cached cube {cube_path.name} in --pooled-cache for the v3 events protocol")
    cube = load_cube(cube_path)
    if cube.spec_identity != spec.identity or cube.cohort_sha256 != cohort.sha256:
        raise ValueError(f"cached cube {cube_path.name} does not match the v3 cohort and spec")
    earnings = protocol.event_inputs.earnings
    pages = load_calendar_pages(earnings_pages, earnings.calendar_from, earnings.calendar_through)
    if pages.pages_sha256 != earnings.pages_sha256:
        raise ValueError("earnings pages digest does not match the protocol's event_inputs.earnings.pages_sha256")
    # The archive is market-wide: the same pages give cohort xu's report dates.
    reports = {record.cik: issuer_reports(pages.rows_by_symbol, record.symbols) for record in store.issuers()}
    sessions = tuple(cube.sessions)
    candidates, counts = candidate_legs(table, sessions, reports, entry)
    sample = sample_parser_check(candidates, table, entry)
    return {
        **sample,
        "parser_version": table.parser_version,
        "content_sha256": table.content_sha256,
        "cube_sha256_v3": cube.sha256,
        "candidate_counts": counts,
        "sessions": {
            "source": "cube",
            "first": sessions[0].isoformat(),
            "last": sessions[-1].isoformat(),
            "count": len(sessions),
        },
    }


@investor_days_group.command("sample")
@click.option("--cache", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--events", "events_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--repo-root", default=".", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--entry",
    "entry_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The idday catalog entry (a draft before the freeze); default <repo-root>/config/research/apriori/idday-v1.json.",
)
@click.option("--earnings-pages", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pooled-cache", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def sample_cmd(
    cache: Path,
    events_file: Path,
    repo_root: Path,
    entry_file: Path | None,
    earnings_pages: Path,
    pooled_cache: Path,
    output: Path,
):
    """Draw the seeded parser-check sample from the price-free candidate legs (offline, no provider call)."""
    if output.exists():
        raise click.ClickException(f"Output file exists: {output}")
    try:
        body = _parser_sample(
            cache, events_file, repo_root, entry_file or repo_root / DEFAULT_ENTRY, earnings_pages, pooled_cache
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    write_private(output, json.dumps(body, indent=2, sort_keys=True).encode())
    click.echo(f"events_sha256 {body['events_sha256']}")
    click.echo(f"sampled {body['sample_size']} of {body['population']} candidate legs; {output}")
