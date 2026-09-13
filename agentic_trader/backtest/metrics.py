import numpy as np
import pandas as pd

from agentic_trader.backtest.models import BacktestTrade
from agentic_trader.constants import (
    DEFAULT_RISK_FREE_RATE,
    FLOAT_EPSILON,
    INFINITE_RATIO_SENTINEL,
    TRADING_DAYS_PER_YEAR,
)


def calculate_win_rate(trades: list[BacktestTrade]) -> float:
    """Calculate the percentage of closed winning trades."""
    if not trades:
        return 0.0
    winning = sum(1 for t in trades if (t.pnl_dollars or 0.0) > 0.0)
    return round((winning / len(trades)) * 100.0, 2)


def calculate_profit_factor(trades: list[BacktestTrade]) -> float:
    """Calculate the ratio of gross profits to gross losses."""
    if not trades:
        return 0.0
    gross_profit = sum((t.pnl_dollars for t in trades if t.pnl_dollars is not None and t.pnl_dollars > 0.0), 0.0)
    gross_loss = abs(sum((t.pnl_dollars for t in trades if t.pnl_dollars is not None and t.pnl_dollars < 0.0), 0.0))

    if gross_loss == 0.0:
        return INFINITE_RATIO_SENTINEL if gross_profit > 0.0 else 0.0
    return round(gross_profit / gross_loss, 2)


def calculate_drawdown(equity_series: pd.Series) -> tuple[float, pd.Series]:
    """Calculate maximum drawdown percentage and running drawdown percentage series.

    Returns:
        (max_drawdown_pct, drawdown_series)
    """
    if equity_series.empty or len(equity_series) < 1:
        return 0.0, pd.Series(dtype=float)

    running_max = equity_series.cummax()
    drawdown_series = ((equity_series - running_max) / running_max) * 100.0
    max_dd = abs(float(drawdown_series.min()))
    return round(max_dd, 2), drawdown_series


def calculate_sharpe_ratio(
    daily_returns: pd.Series,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Calculate annualized Sharpe ratio from a series of periodic returns."""
    cleaned = daily_returns.dropna()
    if len(cleaned) < 2:
        return 0.0

    daily_rf = risk_free_rate / periods_per_year
    excess_returns = cleaned - daily_rf
    std = float(cleaned.std())

    if std < FLOAT_EPSILON or np.isnan(std):
        return 0.0

    sharpe = float((excess_returns.mean() / std) * np.sqrt(periods_per_year))
    return round(sharpe, 2)


def calculate_sortino_ratio(
    daily_returns: pd.Series,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Calculate annualized Sortino ratio considering downside volatility."""
    cleaned = daily_returns.dropna()
    if len(cleaned) < 2:
        return 0.0

    total_std = float(cleaned.std())
    if total_std < FLOAT_EPSILON or np.isnan(total_std):
        return 0.0

    daily_rf = risk_free_rate / periods_per_year
    excess_returns = cleaned - daily_rf
    downside_returns = excess_returns[excess_returns < 0.0]

    if downside_returns.empty:
        return INFINITE_RATIO_SENTINEL if excess_returns.mean() > 0.0 else 0.0

    downside_std = float(np.sqrt(np.mean(downside_returns**2)))
    if downside_std < FLOAT_EPSILON or np.isnan(downside_std):
        return 0.0

    sortino = float((excess_returns.mean() / downside_std) * np.sqrt(periods_per_year))
    return round(sortino, 2)
