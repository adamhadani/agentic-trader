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
from agentic_trader.config import load_config


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
) -> None:
    """Run offline historical backtest across watchlist assets."""
    config = load_config()
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    engine = BacktestEngine(
        config=config,
        initial_cash=cash,
        risk_free_rate=risk_free_rate,
        apply_friction=not no_friction,
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
        result.monte_carlo = run_monte_carlo_simulation(
            result.trades,
            starting_cash=cash,
            n_simulations=mc_sims,
        )
    report = format_backtest_report(result, sym_list, lookback, strategy)
    click.echo(report)
