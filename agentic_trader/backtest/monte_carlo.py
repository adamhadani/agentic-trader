import logging

import numpy as np

from agentic_trader.backtest.models import BacktestTrade, MonteCarloResult
from agentic_trader.constants import (
    DEFAULT_MONTE_CARLO_SIMULATIONS,
    DEFAULT_RANDOM_SEED,
    DEFAULT_RUIN_THRESHOLD_HIGH_PCT,
    DEFAULT_RUIN_THRESHOLD_LOW_PCT,
    DEFAULT_VAR_CONFIDENCE_PCT,
    MIN_TRADES_FOR_MONTE_CARLO,
)


logger = logging.getLogger(__name__)


def run_monte_carlo_simulation(
    trades: list[BacktestTrade],
    starting_cash: float,
    n_simulations: int = DEFAULT_MONTE_CARLO_SIMULATIONS,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> MonteCarloResult | None:
    """IID resampling of closed-trade dollar P&L, conditional on the observed trades.

    Paths add fixed dollar outcomes without resizing or reinvestment. They do not
    preserve serial dependence, overlapping exposure, or portfolio return timing.
    VaR/CVaR are empirical per-trade losses relative to the same starting capital,
    floored at zero for reporting; they are not portfolio-horizon risk limits.
    """
    if (
        isinstance(starting_cash, bool)
        or not np.isfinite(starting_cash)
        or starting_cash <= 0
        or type(n_simulations) is not int
        or n_simulations < 1
    ):
        raise ValueError("Positive finite starting capital and integer simulation count required")
    closed_trades = [t for t in trades if t.pnl_dollars is not None]
    if len(closed_trades) < MIN_TRADES_FOR_MONTE_CARLO:
        logger.warning(
            "Insufficient trades (%d) to run Monte Carlo simulation (minimum %d required).",
            len(closed_trades),
            MIN_TRADES_FOR_MONTE_CARLO,
        )
        return None

    pnls = np.array([t.pnl_dollars for t in closed_trades], dtype=float)
    if not np.isfinite(pnls).all():
        raise ValueError("Closed trades require finite observed dollar P&L")
    loss_pcts = -pnls / starting_cash * 100.0
    if not np.isfinite(loss_pcts).all():
        raise ValueError("Per-trade loss percentages exceed finite numeric range")
    n_trades = len(pnls)

    rng = np.random.default_rng(random_seed)

    # Matrix of resampled trade indices: shape (n_simulations, n_trades)
    resample_idx = rng.choice(n_trades, size=(n_simulations, n_trades), replace=True)
    resampled_pnls = pnls[resample_idx]  # shape (n_simulations, n_trades)

    # Cumulative equity paths: shape (n_simulations, n_trades + 1)
    equity_paths = np.empty((n_simulations, n_trades + 1), dtype=float)
    equity_paths[:, 0] = starting_cash
    equity_paths[:, 1:] = starting_cash + np.cumsum(resampled_pnls, axis=1)
    if not np.isfinite(equity_paths).all():
        raise ValueError("Resampled equity exceeds finite numeric range")

    # Calculate Max Drawdown for each simulation path
    running_maxes = np.maximum.accumulate(equity_paths, axis=1)
    drawdown_paths = ((running_maxes - equity_paths) / running_maxes) * 100.0
    max_drawdowns = np.max(drawdown_paths, axis=1)  # shape (n_simulations,)

    ending_equities = equity_paths[:, -1]

    # Percentiles
    median_equity = round(float(np.percentile(ending_equities, 50)), 2)
    ci_5th_equity = round(float(np.percentile(ending_equities, 5)), 2)
    ci_95th_equity = round(float(np.percentile(ending_equities, 95)), 2)

    median_dd = round(float(np.percentile(max_drawdowns, 50)), 2)
    ci_95th_dd = round(float(np.percentile(max_drawdowns, 95)), 2)

    # Risk of ruin (percentage of paths reaching DD >= threshold)
    ror_10 = round(float(np.mean(max_drawdowns >= DEFAULT_RUIN_THRESHOLD_LOW_PCT) * 100.0), 2)
    ror_20 = round(float(np.mean(max_drawdowns >= DEFAULT_RUIN_THRESHOLD_HIGH_PCT) * 100.0), 2)

    # Empirical quantile and exact upper-tail mass, including a fractional boundary
    # observation. Averaging every value tied at an interpolated cutoff changes
    # the intended tail probability for small/discrete samples.
    loss_quantile = float(np.percentile(loss_pcts, DEFAULT_VAR_CONFIDENCE_PCT, method="inverted_cdf"))
    tail_mass = n_trades * (100.0 - DEFAULT_VAR_CONFIDENCE_PCT) / 100.0
    whole = int(tail_mass)
    ordered = np.sort(loss_pcts)[::-1]
    expected_shortfall = float((ordered[:whole].sum() + (tail_mass - whole) * ordered[whole]) / tail_mass)
    var_95 = round(max(0.0, loss_quantile), 2)
    cvar_95 = round(max(0.0, expected_shortfall), 2)

    return MonteCarloResult(
        n_simulations=n_simulations,
        median_equity=median_equity,
        ci_5th_equity=ci_5th_equity,
        ci_95th_equity=ci_95th_equity,
        median_drawdown_pct=median_dd,
        ci_95th_drawdown_pct=ci_95th_dd,
        median_sharpe=None,
        ci_5th_sharpe=None,
        risk_of_ruin_10pct=ror_10,
        risk_of_ruin_20pct=ror_20,
        var_95_pct=var_95,
        cvar_95_pct=cvar_95,
        sharpe_unavailable_reason="missing_observed_portfolio_return_clock",
    )
