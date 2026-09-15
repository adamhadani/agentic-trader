from __future__ import annotations

from typing import Any

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
@click.option(
    "--bypass-session-filter",
    is_flag=True,
    default=False,
    help="Bypass market session hours and holiday halt gating",
)
@click.option(
    "--strategy",
    type=str,
    default=None,
    help="Explicit strategy to run (e.g. 'trend_pullback', 'squeeze_breakout')",
)
@click.option(
    "--strategy-mode",
    type=click.Choice(["single", "parallel"], case_sensitive=False),
    default=None,
    help="Strategy execution mode override ('single' or 'parallel')",
)
@click.option(
    "--timeframe",
    type=click.Choice(["15m", "1h", "4h", "1d"], case_sensitive=False),
    default=None,
    help="Target candle timeframe for scan (e.g. '15m', '1h', '4h', '1d')",
)
@coro
async def scan(
    no_llm: bool,
    dry_run: bool,
    asset_class: str,
    symbols: str | None,
    bypass_session_filter: bool = False,
    strategy: str | None = None,
    strategy_mode: str | None = None,
    timeframe: str | None = None,
) -> None:
    """Scan watchlists and run LLM risk evaluation."""
    copilot, _config = get_copilot_and_config(dry_run=True) if dry_run else get_copilot_and_config()
    if not dry_run:
        await copilot.broker.connect()
    else:
        click.echo("[DRY RUN] Isolated simulation with an empty portfolio; no Telegram or broker execution.")

    sym_list = [s.strip() for s in symbols.split(",")] if symbols else None
    scan_kwargs: dict[str, Any] = {
        "use_llm": not no_llm,
        "dry_run": dry_run,
        "asset_class": asset_class,
        "symbols": sym_list,
        "bypass_session_filter": bypass_session_filter,
    }
    if strategy is not None:
        scan_kwargs["strategy"] = strategy
    if strategy_mode is not None:
        scan_kwargs["strategy_mode"] = strategy_mode
    if timeframe is not None:
        scan_kwargs["timeframe"] = timeframe

    await copilot.run_scan(**scan_kwargs)
