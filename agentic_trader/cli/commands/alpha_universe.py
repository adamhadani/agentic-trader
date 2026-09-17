"""Composition for read-only prospective equity cohort evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
import httpx

from agentic_trader.cli.commands.alpha import alpha_repository, research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.data.equity_metadata import EquityMetadataSource
from agentic_trader.research.alpha.equity_universe import EquityUniversePlan
from agentic_trader.research.alpha.universe_workflow import EquityUniverseService
from agentic_trader.transport.alpaca import BoundedTradingClient


@click.command("universe-snapshot")
@click.argument("protocol", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--previous", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@coro
async def universe_snapshot_cmd(protocol: Path, output: Path, previous: Path | None):
    """Freeze current non-ETF equity candidates; no prices, trading or historical membership claim."""
    plan = await asyncio.to_thread(lambda: EquityUniversePlan.from_document(json.loads(protocol.read_text())))
    old = await asyncio.to_thread(lambda: json.loads(previous.read_text())) if previous else None
    config = await asyncio.to_thread(load_config)
    trading = BoundedTradingClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        paper=config.alpaca_paper,
        request_timeout=config.market_data.timeout_seconds,
    )
    try:
        with httpx.Client(timeout=config.market_data.timeout_seconds, follow_redirects=False) as http:
            async with alpha_repository() as repository:
                result = await EquityUniverseService(repository, EquityMetadataSource(trading, http)).run(
                    plan, output, environment=await asyncio.to_thread(research_environment), previous=old
                )
    finally:
        trading._session.close()
    click.echo(
        f"Prospective equity candidates: {result['status']}; {result.get('selected_count', 0)} selected; {output / 'result.json'}"
    )
    if result["status"] != "completed":
        raise click.ClickException("Metadata capture failed; immutable evidence retained")
    if not result["target_met"]:
        raise click.ClickException("Metadata captured, but the declared cohort target was not met")
