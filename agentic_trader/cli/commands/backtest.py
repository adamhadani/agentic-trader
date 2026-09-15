from __future__ import annotations

import asyncio
import logging

import click

from agentic_trader.backtest import (
    BacktestEngine,
    format_backtest_report,
    run_monte_carlo_simulation,
)
from agentic_trader.cli.utils import coro
from agentic_trader.config import TrailingStopConfig, load_config


logger = logging.getLogger("copilot")


@click.command("backtest", help="Run offline historical backtest across watchlist assets")
@click.option(
    "--symbols",
    type=str,
    default="ES,NQ,MES,MNQ",
    help="Comma-separated symbols to backtest (default: 'ES,NQ,MES,MNQ')",
)
@click.option(
    "--strategy",
    type=click.Choice(["all", "trend_pullback", "squeeze_breakout"], case_sensitive=False),
    default="all",
    help="Strategy filter to backtest (default: all)",
)
@click.option(
    "--lookback",
    type=str,
    default="1y",
    help="Historical lookback period (e.g. 60d, 1y, 2y; default: 1y)",
)
@click.option(
    "--cash",
    type=float,
    default=100000.0,
    help="Starting portfolio cash balance (default: 100000.0)",
)
@click.option(
    "--risk-free-rate",
    type=float,
    default=0.045,
    help="Annualized risk-free rate for Sharpe/Sortino ratios (default: 0.045)",
)
@click.option(
    "--monte-carlo",
    is_flag=True,
    default=False,
    help="Run Monte Carlo bootstrap simulation on trade sequence",
)
@click.option(
    "--mc-sims",
    type=int,
    default=1000,
    help="Number of Monte Carlo simulation runs (default: 1000)",
)
@click.option(
    "--no-friction",
    is_flag=True,
    default=False,
    help="Disable commissions and bid-ask slippage (frictionless execution)",
)
@click.option(
    "--no-attribution",
    is_flag=True,
    default=False,
    help="Disable factor and regime performance attribution breakdown",
)
@click.option(
    "--trailing-stop-mode",
    type=click.Choice(["none", "breakeven_and_trail", "chandelier_atr"], case_sensitive=False),
    default="chandelier_atr",
    help="Trailing stop policy mode (default: chandelier_atr)",
)
@click.option(
    "--trail-trigger-r",
    type=float,
    default=1.5,
    help="R-multiple required before trailing stop activates (default: 1.5)",
)
@click.option(
    "--trail-atr-multiple",
    type=float,
    default=1.5,
    help="ATR multiplier for trailing distance (default: 1.5)",
)
@click.option(
    "--breakeven-trigger-r",
    type=float,
    default=None,
    help="Optional R-multiple to move stop to breakeven (default: None for chandelier_atr)",
)
@coro
async def backtest(
    symbols: str,
    strategy: str,
    lookback: str,
    cash: float,
    risk_free_rate: float,
    monte_carlo: bool,
    mc_sims: int,
    no_friction: bool,
    no_attribution: bool,
    trailing_stop_mode: str,
    trail_trigger_r: float,
    trail_atr_multiple: float,
    breakeven_trigger_r: float | None,
) -> None:
    """Run offline historical backtest across watchlist assets."""
    config = load_config()
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]

    ts_config = None
    if trailing_stop_mode == "none":
        ts_config = TrailingStopConfig(enabled=False, mode="fixed")
    elif trailing_stop_mode == "breakeven_and_trail":
        ts_config = TrailingStopConfig(
            enabled=True,
            mode="breakeven_and_trail",
            breakeven_trigger_r=breakeven_trigger_r if breakeven_trigger_r is not None else 1.0,
            trail_trigger_r=trail_trigger_r,
            trail_atr_multiple=trail_atr_multiple,
        )
    else:  # chandelier_atr
        ts_config = TrailingStopConfig(
            enabled=True,
            mode="chandelier_atr",
            breakeven_trigger_r=breakeven_trigger_r,
            trail_trigger_r=trail_trigger_r,
            trail_atr_multiple=trail_atr_multiple,
        )

    engine = BacktestEngine(
        config=config,
        initial_cash=cash,
        risk_free_rate=risk_free_rate,
        apply_friction=not no_friction,
        trailing_stop=ts_config,
    )
    logger.info(
        "Running offline backtest across %s (lookback: %s, strategy: %s)...",
        sym_list,
        lookback,
        strategy,
    )
    result = await asyncio.to_thread(
        engine.run,
        symbols=sym_list,
        strategy_filter=strategy,
        lookback=lookback,
        enable_attribution=not no_attribution,
    )
    if monte_carlo and result.trades:
        result.monte_carlo = await asyncio.to_thread(
            run_monte_carlo_simulation,
            result.trades,
            starting_cash=cash,
            n_simulations=mc_sims,
        )
    report = format_backtest_report(result, sym_list, lookback, strategy)
    click.echo(report)
