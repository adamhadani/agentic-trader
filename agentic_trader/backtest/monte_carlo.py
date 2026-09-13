import logging

import numpy as np

from agentic_trader.backtest.models import BacktestTrade, MonteCarloResult
from agentic_trader.constants import (
    DEFAULT_MONTE_CARLO_SIMULATIONS,
    DEFAULT_RANDOM_SEED,
    DEFAULT_RUIN_THRESHOLD_HIGH_PCT,
    DEFAULT_RUIN_THRESHOLD_LOW_PCT,
    DEFAULT_VAR_CONFIDENCE_PCT,
    FLOAT_EPSILON,
    MIN_TRADES_FOR_MONTE_CARLO,
)


logger = logging.getLogger(__name__)


def run_monte_carlo_simulation(
    trades: list[BacktestTrade],
    starting_cash: float,
    n_simulations: int = DEFAULT_MONTE_CARLO_SIMULATIONS,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> MonteCarloResult | None:
    """Run bootstrap Monte Carlo resampling across historical executed trades.

    Resamples trade sequences with replacement to evaluate final equity, drawdown
    distributions, risk of ruin, and Value at Risk (VaR / CVaR) bounds.
    """
    closed_trades = [t for t in trades if t.pnl_dollars is not None]
    if len(closed_trades) < MIN_TRADES_FOR_MONTE_CARLO:
        logger.warning(
            "Insufficient trades (%d) to run Monte Carlo simulation (minimum %d required).",
            len(closed_trades),
            MIN_TRADES_FOR_MONTE_CARLO,
        )
        return None

    pnls = np.array([t.pnl_dollars for t in closed_trades], dtype=float)
    pnl_pcts = np.array(
        [
            t.pnl_pct if t.pnl_pct is not None else ((t.pnl_dollars or 0.0) / starting_cash * 100.0)
            for t in closed_trades
        ],
        dtype=float,
    )
    n_trades = len(pnls)

    rng = np.random.default_rng(random_seed)

    # Matrix of resampled trade indices: shape (n_simulations, n_trades)
    resample_idx = rng.choice(n_trades, size=(n_simulations, n_trades), replace=True)
    resampled_pnls = pnls[resample_idx]  # shape (n_simulations, n_trades)

    # Cumulative equity paths: shape (n_simulations, n_trades + 1)
    equity_paths = np.empty((n_simulations, n_trades + 1), dtype=float)
    equity_paths[:, 0] = starting_cash
    equity_paths[:, 1:] = starting_cash + np.cumsum(resampled_pnls, axis=1)

    # Calculate Max Drawdown for each simulation path
    running_maxes = np.maximum.accumulate(equity_paths, axis=1)
    # Avoid zero-division if starting cash were zero
    drawdown_paths = np.where(
        running_maxes > 0,
        ((running_maxes - equity_paths) / running_maxes) * 100.0,
        0.0,
    )
    max_drawdowns = np.max(drawdown_paths, axis=1)  # shape (n_simulations,)

    ending_equities = equity_paths[:, -1]

    # Calculate Sharpe for each simulation path (assuming annualized rate based on trade frequency)
    sim_means = np.mean(resampled_pnls, axis=1)
    sim_stds = np.std(resampled_pnls, axis=1)
    # Annualization factor approximation (assume ~50 trades/year if not specified)
    annual_factor = np.sqrt(max(10, n_trades))
    valid_mask = sim_stds > FLOAT_EPSILON
    sim_sharpes = np.zeros_like(sim_means)
    sim_sharpes[valid_mask] = (sim_means[valid_mask] / sim_stds[valid_mask]) * annual_factor

    # Percentiles
    median_equity = round(float(np.percentile(ending_equities, 50)), 2)
    ci_5th_equity = round(float(np.percentile(ending_equities, 5)), 2)
    ci_95th_equity = round(float(np.percentile(ending_equities, 95)), 2)

    median_dd = round(float(np.percentile(max_drawdowns, 50)), 2)
    ci_95th_dd = round(float(np.percentile(max_drawdowns, 95)), 2)

    median_sharpe = round(float(np.percentile(sim_sharpes, 50)), 2)
    ci_5th_sharpe = round(float(np.percentile(sim_sharpes, 5)), 2)

    # Risk of ruin (percentage of paths reaching DD >= threshold)
    ror_10 = round(float(np.mean(max_drawdowns >= DEFAULT_RUIN_THRESHOLD_LOW_PCT) * 100.0), 2)
    ror_20 = round(float(np.mean(max_drawdowns >= DEFAULT_RUIN_THRESHOLD_HIGH_PCT) * 100.0), 2)

    # Value at Risk (VaR 95%) and Conditional VaR (CVaR 95% / Expected Shortfall) on trade returns
    var_tail_pct = 100.0 - DEFAULT_VAR_CONFIDENCE_PCT
    pnl_5th = float(np.percentile(pnl_pcts, var_tail_pct))
    var_95 = round(abs(min(0.0, pnl_5th)), 2)

    tail_losses = pnl_pcts[pnl_pcts <= pnl_5th]
    cvar_95 = round(abs(float(np.mean(tail_losses))) if len(tail_losses) > 0 else var_95, 2)

    return MonteCarloResult(
        n_simulations=n_simulations,
        median_equity=median_equity,
        ci_5th_equity=ci_5th_equity,
        ci_95th_equity=ci_95th_equity,
        median_drawdown_pct=median_dd,
        ci_95th_drawdown_pct=ci_95th_dd,
        median_sharpe=median_sharpe,
        ci_5th_sharpe=ci_5th_sharpe,
        risk_of_ruin_10pct=ror_10,
        risk_of_ruin_20pct=ror_20,
        var_95_pct=var_95,
        cvar_95_pct=cvar_95,
    )
