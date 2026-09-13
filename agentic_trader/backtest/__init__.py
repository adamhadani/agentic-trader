"""Offline backtesting and performance analytics package."""

from agentic_trader.backtest.engine import BacktestEngine
from agentic_trader.backtest.models import BacktestResult, BacktestTrade, EquityPoint
from agentic_trader.backtest.reporting import format_backtest_report


__all__ = ["BacktestEngine", "BacktestResult", "BacktestTrade", "EquityPoint", "format_backtest_report"]
