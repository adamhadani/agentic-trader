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
from agentic_trader.backtest.reporting import (
    format_backtest_report,
    format_instantaneous_shock_report,
    format_stress_test_report,
)
from agentic_trader.backtest.stress import (
    CRISIS_CATALOG,
    CrisisReplayEngine,
    CrisisScenario,
    InstantaneousShockResult,
    ScenarioStressResult,
)


__all__ = [
    "CRISIS_CATALOG",
    "AssetClassAttribution",
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
    "CrisisReplayEngine",
    "CrisisScenario",
    "EquityPoint",
    "FactorAttribution",
    "InstantaneousShockResult",
    "MonteCarloResult",
    "PerformanceAttributionResult",
    "RegimeAttribution",
    "ScenarioStressResult",
    "calculate_performance_attribution",
    "format_backtest_report",
    "format_instantaneous_shock_report",
    "format_stress_test_report",
    "run_monte_carlo_simulation",
]
