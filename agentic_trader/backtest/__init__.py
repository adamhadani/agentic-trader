"""Offline backtesting and performance analytics package."""

from agentic_trader.backtest.attribution import calculate_performance_attribution
from agentic_trader.backtest.engine import BacktestEngine
from agentic_trader.backtest.models import (
    AssetClassAttribution,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    FactorAttribution,
    MonteCarloResult,
    PerformanceAttributionResult,
    RegimeAttribution,
)
from agentic_trader.backtest.monte_carlo import run_monte_carlo_simulation
from agentic_trader.backtest.reporting import format_backtest_report


__all__ = [
    "AssetClassAttribution",
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
    "EquityPoint",
    "FactorAttribution",
    "MonteCarloResult",
    "PerformanceAttributionResult",
    "RegimeAttribution",
    "calculate_performance_attribution",
    "format_backtest_report",
    "run_monte_carlo_simulation",
]
