"""Stage evaluation: the four predeclared pass rules and descriptive tables (never gates)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

import numpy as np
import statsmodels.api as sm

from agentic_trader.research.pooled.stats import trimmed_mean
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive
from agentic_trader.research.setups.study import _stationary_index_draws
from agentic_trader.research.spread.protocol import PassRules, SpreadProtocol
from agentic_trader.research.spread.trading import Trade, trade_net_return


__all__ = ["evaluate_stage"]

_MIN_BETA_OBSERVATIONS = 30
_HAC_LAGS = 5
_SESSIONS_PER_YEAR = 252


def _nan() -> float:
    return float("nan")


def _s1(lane: np.ndarray, protocol: SpreadProtocol) -> dict:
    n = lane.size
    if n == 0:
        return {"mean": _nan(), "ci90": (_nan(), _nan()), "p_one_sided": _nan(), "n_sessions": 0, "holds": False}
    mean = float(lane.mean())
    draws = _stationary_index_draws(n, protocol.bootstrap.block_mean, protocol.bootstrap.draws, protocol.bootstrap.seed)
    boot = lane[draws].mean(axis=1)
    ci90 = _ci90(boot)
    return {
        "mean": mean,
        "ci90": ci90,
        "p_one_sided": _p_one_sided_positive(boot),
        "n_sessions": int(n),
        "holds": bool(mean > 0 and math.isfinite(ci90[0]) and ci90[0] > 0),
    }


def _s2(nets: np.ndarray, rules: PassRules) -> dict:
    if nets.size == 0:
        return {"mean": _nan(), "trimmed_mean": _nan(), "holds": False}
    mean = float(nets.mean())
    trimmed = trimmed_mean(nets, rules.trim_fraction)
    return {"mean": mean, "trimmed_mean": trimmed, "holds": bool(mean > 0 and math.isfinite(trimmed) and trimmed > 0)}


def _window_means(trades: Sequence[Trade], nets: np.ndarray) -> dict[int, float]:
    sums: dict[int, list[float]] = {}
    for trade, net in zip(trades, nets, strict=True):
        sums.setdefault(trade.window, []).append(float(net))
    return {w: float(np.mean(v)) for w, v in sorted(sums.items())}


def _s3(trades: Sequence[Trade], nets: np.ndarray, rules: PassRules) -> dict:
    means = _window_means(trades, nets)
    positive = sum(1 for m in means.values() if m > 0)
    fraction = positive / len(means) if means else 0.0
    return {
        "closed_trades": len(trades),
        "windows_with_trades": len(means),
        "positive_window_fraction": fraction,
        "holds": bool(
            len(trades) >= rules.min_closed_trades
            and len(means) >= rules.min_windows
            and fraction >= rules.min_positive_window_fraction
        ),
    }


def _s4(lane: np.ndarray, market: np.ndarray, rules: PassRules) -> dict:
    mask = np.isfinite(lane) & np.isfinite(market)
    n = int(mask.sum())
    if n < _MIN_BETA_OBSERVATIONS:
        return {"beta": _nan(), "se_hac": _nan(), "n_sessions": n, "holds": False}
    x, y = market[mask], lane[mask]
    if np.ptp(x) == 0.0:
        return {"beta": _nan(), "se_hac": _nan(), "n_sessions": n, "holds": False}
    fit = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HAC", cov_kwds={"maxlags": _HAC_LAGS, "use_correction": True})
    beta = float(fit.params[1])
    return {
        "beta": beta,
        "se_hac": float(fit.bse[1]),
        "n_sessions": n,
        "holds": bool(math.isfinite(beta) and abs(beta) <= rules.max_abs_market_beta),
    }


def _by_cost(trades: Sequence[Trade], lane_by_cost: Mapping[float, np.ndarray]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cost, lane in lane_by_cost.items():
        nets = np.array([trade_net_return(t, cost) for t in trades], dtype=float)
        std = float(lane.std(ddof=1)) if lane.size > 1 else _nan()
        out[str(float(cost))] = {
            "closed_trades": len(trades),
            "mean_trade_net": float(nets.mean()) if nets.size else _nan(),
            "hit_rate": float((nets > 0).mean()) if nets.size else _nan(),
            "lane_mean_daily": float(lane.mean()) if lane.size else _nan(),
            "sharpe_annualised": float(lane.mean() / std * math.sqrt(_SESSIONS_PER_YEAR))
            if std and std > 0
            else _nan(),
        }
    return out


def evaluate_stage(
    trades: Sequence[Trade],
    lane_by_cost: Mapping[float, np.ndarray],
    market: np.ndarray,
    *,
    windows: int,
    protocol: SpreadProtocol,
    rules: PassRules,
) -> dict:
    cost = protocol.decision_cost_bps
    lane = np.asarray(lane_by_cost[cost], dtype=float)
    market = np.asarray(market, dtype=float)
    nets = np.array([trade_net_return(t, cost) for t in trades], dtype=float)
    s1, s2, s3, s4 = _s1(lane, protocol), _s2(nets, rules), _s3(trades, nets, rules), _s4(lane, market, rules)
    window_means = _window_means(trades, nets)
    window_counts = Counter(t.window for t in trades)
    sectors: dict[str, list[float]] = {}
    for trade, net in zip(trades, nets, strict=True):
        sectors.setdefault(trade.sector, []).append(float(net))
    by_cost = _by_cost(trades, lane_by_cost)
    descriptives = {
        "decision_cost_bps": cost,
        "by_cost": by_cost,
        "sharpe_annualised": by_cost[str(float(cost))]["sharpe_annualised"],
        "hit_rate": by_cost[str(float(cost))]["hit_rate"],
        "mean_holding_sessions": float(np.mean([t.holding_sessions for t in trades])) if trades else _nan(),
        "exit_reasons": dict(sorted(Counter(t.exit_reason for t in trades).items())),
        "stage_windows": windows,
        "windows": [
            {"window": w, "trades": window_counts[w], "mean_net": m, "positive": m > 0} for w, m in window_means.items()
        ],
        "sectors": {s: {"trades": len(v), "mean_net": float(np.mean(v))} for s, v in sorted(sectors.items())},
    }
    return {
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "s4": s4,
        "passes": bool(s1["holds"] and s2["holds"] and s3["holds"] and s4["holds"]),
        "descriptives": descriptives,
    }
