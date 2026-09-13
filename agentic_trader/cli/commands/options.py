from __future__ import annotations

import asyncio
import logging

import click

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.options import OptionsDataFetcher, format_gex_report


logger = logging.getLogger("copilot")


@click.command("gex", help="Analyze market maker gamma exposure (GEX), pinning walls, and volatility surface")
@click.argument("symbol", type=str, default="SPY", required=False)
@click.option(
    "--expirations",
    type=int,
    default=3,
    help="Number of near-term option expirations to aggregate (default: 3)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output raw JSON data model instead of ASCII table",
)
@coro
async def gex(symbol: str, expirations: int, output_json: bool) -> None:
    """Analyze market maker gamma exposure (GEX), pinning walls, and volatility surface."""
    config = load_config()
    fetcher = OptionsDataFetcher(
        risk_free_rate=config.options.risk_free_rate,
        cache_ttl_seconds=config.options.cache_ttl_seconds,
    )
    click.echo(f"Fetching option chains and computing dealer gamma for {symbol.upper()}...")
    try:
        profile = await asyncio.to_thread(
            fetcher.fetch_and_calculate_gex,
            symbol,
            expirations,
        )
        if output_json:
            click.echo(profile.model_dump_json(indent=2))
        else:
            click.echo(format_gex_report(profile))
    except Exception as e:
        logger.error("Failed to calculate GEX: %s", e)
        click.echo(f"Error analyzing gamma exposure for {symbol}: {e}")
