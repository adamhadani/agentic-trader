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
    type=click.IntRange(min=1),
    default=None,
    help="Number of near-term option expirations (defaults to options.max_expirations)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output raw JSON data model instead of ASCII table",
)
@coro
async def gex(symbol: str, expirations: int | None, output_json: bool) -> None:
    """Analyze market maker gamma exposure (GEX), pinning walls, and volatility surface."""
    config = load_config()
    fetcher = OptionsDataFetcher(
        risk_free_rate=config.options.risk_free_rate,
        cache_ttl_seconds=config.options.cache_ttl_seconds,
    )
    click.echo(f"Fetching option chains and estimating gamma for {symbol.upper()}...", err=True)
    try:
        profile = await asyncio.to_thread(
            fetcher.fetch_and_calculate_gex,
            symbol,
            expirations if expirations is not None else config.options.max_expirations,
        )
        if output_json:
            click.echo(profile.model_dump_json(indent=2))
        else:
            click.echo(format_gex_report(profile))
    except Exception as e:
        logger.exception("Failed to calculate GEX for %s", symbol)
        raise click.ClickException(f"GEX unavailable for {symbol}: {e}") from e
