"""Research-only corporate-action acquisition for a symbol list or a frozen cohort file."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.data.corporate_actions import AlpacaCorporateActionsSource, CorporateActionsSource
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.research.corporate_actions import fetch_corporate_actions_cached
from agentic_trader.transport.alpaca import BoundedCorporateActionsClient


def _build_source() -> CorporateActionsSource:
    config = load_config()
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise click.ClickException("Alpaca data credentials are required")
    client = BoundedCorporateActionsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    return AlpacaCorporateActionsSource(
        client,
        pacer=RequestPacer(config.corporate_actions.max_requests_per_minute),
        chunk_size=config.corporate_actions.chunk_size,
    )


def _cohort_symbols(path: Path) -> list[str]:
    document = json.loads(path.read_text())
    if "sectors" in document:
        return [symbol for members in document["sectors"].values() for symbol in members]
    if "symbols" in document:
        return list(document["symbols"])
    raise click.ClickException("Cohort file needs a 'sectors' mapping or a 'symbols' list")


async def _noop_pace() -> None:
    return None


@click.command("corporate-actions")
@click.option("--symbol", "symbols", multiple=True, help="A symbol; repeatable.")
@click.option(
    "--cohort", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="A frozen cohort JSON file."
)
@click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--cache", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@coro
async def corporate_actions_cmd(symbols, cohort: Path | None, start, end, cache: Path, output: Path):
    """Fetch and cache corporate actions (process_date window) for research; no database, no trading."""
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    wanted = list(symbols) + (_cohort_symbols(cohort) if cohort else [])
    if not wanted:
        raise click.ClickException("Pass --symbol and/or --cohort")
    start_date, end_date = start.date(), end.date()
    if end_date < start_date:
        raise click.ClickException("--end must not precede --start")
    actions, manifest = await fetch_corporate_actions_cached(
        _build_source(), wanted, start_date, end_date, cache, pace=_noop_pace
    )
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(parents=True)
    (output / "actions.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in actions], indent=2, sort_keys=True)
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    click.echo(f"{len(actions)} corporate actions for {len(manifest['symbols'])} symbols; {output / 'manifest.json'}")
