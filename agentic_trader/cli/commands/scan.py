from __future__ import annotations

import click

from agentic_trader.cli.utils import coro, get_copilot_and_config


@click.command("scan", help="Scan watchlists and run LLM risk evaluation")
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Bypass LLM risk gate and run pure algorithmic screener",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Scan and evaluate without staging signals or sending orders",
)
@click.option(
    "--asset-class",
    type=click.Choice(["crypto", "equities", "futures", "all"], case_sensitive=False),
    default="all",
    help="Asset class filter to scan (default: all)",
)
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma-separated symbols to scan (e.g. 'ES,NQ' or 'BTC/USD')",
)
@coro
async def scan(
    no_llm: bool,
    dry_run: bool,
    asset_class: str,
    symbols: str | None,
) -> None:
    """Scan watchlists and run LLM risk evaluation."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()

    sym_list = [s.strip() for s in symbols.split(",")] if symbols else None
    await copilot.run_scan(
        use_llm=not no_llm,
        dry_run=dry_run,
        asset_class=asset_class,
        symbols=sym_list,
    )
