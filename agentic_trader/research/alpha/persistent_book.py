"""Self-financing research book in adjusted-price units, never broker quantities.

Targets observed at one close fix quantities for the next observed session open.
Prices, carry, turnover and terminal liquidation have one chronological ledger.
"""

import itertools
from dataclasses import dataclass
from enum import StrEnum

import cvxpy as cp
import numpy as np
import pandas as pd

from agentic_trader.market.bars import TradingSession
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.forecast_policy import BASIS_POINTS
from agentic_trader.research.alpha.panel import validate_panel


NUMERICAL_TOLERANCE = 1e-10
SECONDS_PER_ACT365_YEAR = 365 * 24 * 60 * 60
MAX_CARRY_BPS = 10000
MAX_BOOK_SESSIONS = 252


class BookMode(StrEnum):
    RESET = "reset"
    PERSISTENT = "persistent"
    BUFFERED = "buffered"


@dataclass(frozen=True)
class CostScenario:
    cost_bps: float
    borrow_bps: float
    funding_bps: float

    def __post_init__(self):
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not 0 <= v <= MAX_CARRY_BPS
            for v in (self.cost_bps, self.borrow_bps, self.funding_bps)
        ):
            raise ValueError("Finite nonnegative bounded fee/carry assumptions required")


@dataclass(frozen=True)
class BookPolicy:
    mode: BookMode
    rebalance_sessions: int
    turnover_buffer: float

    def __post_init__(self):
        if (
            not isinstance(self.mode, BookMode)
            or type(self.rebalance_sessions) is not int
            or not 1 <= self.rebalance_sessions <= MAX_BOOK_SESSIONS
            or isinstance(self.turnover_buffer, bool)
            or not np.isfinite(self.turnover_buffer)
            or not 0 <= self.turnover_buffer <= 1
        ):
            raise ValueError("Explicit bounded book mode, rebalance cadence and buffer required")


def simulate_book(
    opening: pd.DataFrame,
    close: pd.DataFrame,
    targets: pd.DataFrame,
    sessions: tuple[TradingSession, ...],
    policy: BookPolicy,
    costs: CostScenario,
):
    """Execute prior-close quantities, net trades and mark every session including cash.

    Cash earns zero interest; no short rebate. Borrow/funding accrue ACT/365:
    previous close notionals overnight, post-trade opening notionals intraday.
    RESET closes/reopens at the same open (a turnover control, no gap advantage).
    BUFFERED skips the entire rebalance if prior-close L1 weight drift <= buffer.
    Gross/net can drift after the decision; their observed maxima are reported.
    """
    for frame in (opening, close, targets):
        validate_panel(frame)
        if (
            not frame.index.equals(close.index)
            or not frame.columns.equals(close.columns)
            or not np.isfinite(frame.to_numpy(dtype=float)).all()
        ):
            raise ValueError("Complete exactly aligned prices and targets required")
    if (
        not isinstance(close.index, pd.DatetimeIndex)
        or close.index.tz is None
        or not close.index.equals(close.index.tz_convert(ET_TZ).normalize())
        or len(close) < 2
        or (opening <= 0).any().any()
        or (close <= 0).any().any()
    ):
        raise ValueError("Positive prices and aware multi-session clock required")
    if (
        len(sessions) != len(close)
        or [s.date for s in sessions] != list(close.index.date)
        or any(a.close >= b.open for a, b in itertools.pairwise(sessions))
    ):
        raise ValueError("Exact ordered observed exchange sessions required")
    if (targets.abs().sum(axis=1) > 1 + NUMERICAL_TOLERANCE).any() or (
        targets.sum(axis=1).abs() > NUMERICAL_TOLERANCE
    ).any():
        raise ValueError("Declared zero-net, gross-at-most-one targets required")
    q = np.zeros(len(close.columns))
    cash = 1.0
    equity = 1.0
    daily = []
    rebalances = 0
    for i, stamp in enumerate(close.index):
        op = opening.iloc[i].to_numpy(dtype=float)
        cl = close.iloc[i].to_numpy(dtype=float)
        previous = close.iloc[i - 1].to_numpy(dtype=float) if i else op
        overnight = (sessions[i].open - sessions[i - 1].close).total_seconds() / SECONDS_PER_ACT365_YEAR if i else 0.0
        intraday = (sessions[i].close - sessions[i].open).total_seconds() / SECONDS_PER_ACT365_YEAR
        borrow = float(np.maximum(-q * previous, 0).sum() * costs.borrow_bps / BASIS_POINTS * overnight)
        funding = max(-cash, 0) * costs.funding_bps / BASIS_POINTS * overnight
        price_pnl = float(q @ (op - previous)) if i else 0.0
        old_cash = cash
        cash -= borrow + funding
        fee = 0.0
        turnover = 0.0
        submitted = np.zeros_like(q)
        if i and (i - 1) % policy.rebalance_sessions == 0:
            # The opening price and overnight financing cannot affect order sizing.
            observed_equity = old_cash + float(q @ previous)
            desired = targets.iloc[i - 1].to_numpy(dtype=float) * observed_equity / previous
            drift = float(np.abs((desired - q) * previous).sum() / observed_equity)
            execute = policy.mode != BookMode.BUFFERED or drift > policy.turnover_buffer
            if execute:
                submitted = desired - q
                turnover = (
                    float((np.abs(q) + np.abs(desired)) @ op)
                    if policy.mode == BookMode.RESET
                    else float(np.abs(submitted) @ op)
                )
                fee = turnover * costs.cost_bps / BASIS_POINTS
                cash -= float(submitted @ op) + fee
                if turnover > NUMERICAL_TOLERANCE:
                    rebalances += 1
                q = desired
        # Opening mark is descriptive, not a clairvoyant risk resize.
        opening_equity = cash + float(q @ op)
        if not np.isfinite(opening_equity) or opening_equity <= 0:
            raise ValueError("Book insolvent at opening mark")
        opening_gross = float(np.abs(q * op).sum() / opening_equity)
        opening_net = float(q @ op / opening_equity)
        intraday_borrow = float(np.maximum(-q * op, 0).sum() * costs.borrow_bps / BASIS_POINTS * intraday)
        intraday_funding = max(-cash, 0) * costs.funding_bps / BASIS_POINTS * intraday
        borrow += intraday_borrow
        funding += intraday_funding
        cash -= intraday_borrow + intraday_funding
        price_pnl += float(q @ (cl - op))
        pre_liquidation = dict(zip(close.columns, q.tolist(), strict=True))
        pre_terminal_equity = cash + float(q @ cl)
        if not np.isfinite(pre_terminal_equity) or pre_terminal_equity <= 0:
            raise ValueError("Book insolvent at closing mark")
        marked_gross = float(np.abs(q * cl).sum() / pre_terminal_equity)
        marked_net = float(q @ cl / pre_terminal_equity)
        if i == len(close) - 1:
            terminal = float(np.abs(q) @ cl)
            terminal_fee = terminal * costs.cost_bps / BASIS_POINTS
            cash += float(q @ cl) - terminal_fee
            fee += terminal_fee
            turnover += terminal
            q = np.zeros_like(q)
        marked = q * cl
        new_equity = cash + float(marked.sum())
        if (
            not np.isfinite(new_equity)
            or new_equity <= 0
            or not np.isclose(
                new_equity - equity,
                price_pnl - fee - borrow - funding,
                atol=NUMERICAL_TOLERANCE,
                rtol=NUMERICAL_TOLERANCE,
            )
        ):
            raise ValueError("Invalid self-financing book identity or insolvency")
        daily.append(
            {
                "bar": stamp.isoformat(),
                "cash": float(cash),
                "equity": float(new_equity),
                "return": float(new_equity / equity - 1),
                "price_pnl": price_pnl,
                "fees": fee,
                "borrow": borrow,
                "funding": funding,
                "turnover": turnover,
                "gross": max(opening_gross, marked_gross),
                "net": marked_net,
                "opening_net": opening_net,
                "opening_at": sessions[i].open.isoformat(),
                "closing_at": sessions[i].close.isoformat(),
                "assumed_decision_at": (close.index[i - 1] + pd.DateOffset(days=1)).isoformat()
                if i and (i - 1) % policy.rebalance_sessions == 0
                else None,
                "units": dict(zip(close.columns, q.tolist(), strict=True)),
                "marked_inventory": dict(zip(close.columns, marked.tolist(), strict=True)),
                "submitted_units": dict(zip(close.columns, submitted.tolist(), strict=True)),
                "pre_terminal_units": pre_liquidation if i == len(close) - 1 else None,
                "decision_bar": close.index[i - 1].isoformat()
                if i and (i - 1) % policy.rebalance_sessions == 0
                else None,
            }
        )
        equity = new_equity
    wealth = np.array([1.0, *[r["equity"] for r in daily]])
    return {
        "daily": daily,
        "net_return": equity - 1,
        "daily_marked_drawdown": float(np.max(1 - wealth / np.maximum.accumulate(wealth))),
        "rebalances": rebalances,
        "turnover_initial_capital": sum(r["turnover"] for r in daily),
        "fees_initial_capital": sum(r["fees"] for r in daily),
        "borrow_initial_capital": sum(r["borrow"] for r in daily),
        "funding_initial_capital": sum(r["funding"] for r in daily),
        "maximum_marked_gross": max(r["gross"] for r in daily),
        "maximum_absolute_marked_net": max(max(abs(r["net"]), abs(r["opening_net"])) for r in daily),
        "terminal_inventory": daily[-1]["units"],
    }


def dependence_blend(components: dict[str, pd.DataFrame], *, window: int, shrinkage: float):
    """Causal long-only minimum-variance blend of cross-sectional rank scores.

    Estimate score covariance from trailing complete day×asset rank observations,
    with fixed diagonal shrinkage. This learns redundancy, NOT expected returns.
    Exact duplicate histories are one factor before shrinkage, not extra votes.
    Warmup stays unavailable. No labels/outcomes enter the fit.
    """
    if (
        not components
        or type(window) is not int
        or not 2 <= window <= MAX_BOOK_SESSIONS
        or not np.isfinite(shrinkage)
        or not 0 < shrinkage <= 1
    ):
        raise ValueError("Bounded complete blend contract required")
    first = next(iter(components.values()))
    for frame in components.values():
        validate_panel(frame)
        if not frame.index.equals(first.index) or not frame.columns.equals(first.columns):
            raise ValueError("Blend factors must have exactly aligned clocks/members")
    ranks = {k: v.rank(axis=1, pct=True).sub(0.5) for k, v in components.items()}
    result = pd.DataFrame(np.nan, index=first.index, columns=first.columns)
    evidence = []
    for i in range(window - 1, len(first)):
        names = []
        histories: list[np.ndarray] = []
        for name, frame in ranks.items():
            values = frame.iloc[i - window + 1 : i + 1].to_numpy().ravel()
            if not np.isfinite(values).all():
                break
            if not any(np.array_equal(values, past) for past in histories):
                names.append(name)
                histories.append(values)
        else:
            sample = np.stack(histories, axis=1)
            covariance = np.atleast_2d(np.cov(sample, rowvar=False, ddof=1))
            covariance = (1 - shrinkage) * covariance + shrinkage * np.diag(np.diag(covariance))
            weights = cp.Variable(len(names))
            problem = cp.Problem(
                cp.Minimize(cp.quad_form(weights, cp.psd_wrap(covariance))), [weights >= 0, cp.sum(weights) == 1]
            )
            problem.solve(solver=cp.CLARABEL)
            if (
                problem.status != cp.OPTIMAL
                or weights.value is None
                or not np.isfinite(weights.value).all()
                or np.min(weights.value) < -NUMERICAL_TOLERANCE
                or abs(sum(weights.value) - 1) > NUMERICAL_TOLERANCE
            ):
                raise ValueError("No feasible optimal dependence blend")
            w = np.maximum(weights.value, 0)
            w /= w.sum()
            result.iloc[i] = sum(w[j] * ranks[n].iloc[i] for j, n in enumerate(names))
            evidence.append(
                {
                    "bar": first.index[i].isoformat(),
                    "trained_through": first.index[i].isoformat(),
                    "observations": len(sample),
                    "weights": dict(zip(names, w.tolist(), strict=True)),
                    "covariance": covariance.tolist(),
                    "solver_status": problem.status,
                }
            )
    return result, evidence
