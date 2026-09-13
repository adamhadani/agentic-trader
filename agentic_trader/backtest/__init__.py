"""Offline backtesting and performance analytics package."""

from agentic_trader.backtest.engine import BacktestEngine
from agentic_trader.backtest.models import (
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    MonteCarloResult,
)
from agentic_trader.backtest.monte_carlo import run_monte_carlo_simulation
from agentic_trader.backtest.reporting import format_backtest_report


__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
    "EquityPoint",
    "MonteCarloResult",
    "format_backtest_report",
    "run_monte_carlo_simulation",
]
