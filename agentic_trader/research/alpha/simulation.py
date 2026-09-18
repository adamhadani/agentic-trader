"""Causal OHLC simulator for the immutable formulaic bracket policy.

One unit of starting capital, fully funded per entry, no pyramiding. Close-bar
signals create GTC limits at the observed close, eligible from the next bar. Limits
remain pending until touched; no fill may violate the limit. A bar
that touches both exits takes the stop first. Trailing observes the bar close and
becomes effective next bar; intrabar live trailing is an explicitly unmodelled
execution difference. Intrabar entry cannot claim a target observed before its
fill. Liquidity/partial fills and admission latency require shadow execution
evidence. Open trades and unfilled orders remain censored at a fold boundary.
Every supplied execution bar has a known equity return, including cash/warmup.
Unavailable features suppress new signals; unavailable prices invalidate the run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd
from scipy import stats

from agentic_trader.market.bars import FixedDailyClockPolicy, utc_timestamp
from agentic_trader.research.alpha.metrics import observed_return_values
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.strategy import (
    AlphaExecutionPolicy,
    TimedAlphaExecutionPolicy,
    alpha_scores,
    bracket_prices,
    entry_directions,
    entry_limit,
    strategy_atr,
    trailing_price,
)


def return_statistics(returns: pd.Series, trades: list[dict], annual_factor: float | None = None) -> dict:
    observed_return_values(returns)
    valid = returns
    if annual_factor is None:
        if len(valid) < 2 or not isinstance(valid.index, pd.DatetimeIndex):
            annual_factor = 0.0
        else:
            elapsed = (valid.index[-1] - valid.index[0]).total_seconds()
            annual_factor = (len(valid) - 1) * 365.25 * 24 * 3600 / elapsed if elapsed > 0 else 0.0
    equity = (1 + valid).cumprod()
    total = float(equity.iloc[-1] - 1) if len(equity) else 0.0
    std = float(valid.std()) if len(valid) > 1 else 0
    per_bar = float(valid.mean() / std) if std > 0 else 0.0
    peak = equity.cummax().clip(lower=1)
    pnls = [trade["net_return"] for trade in trades]
    gains = sum(max(0, value) for value in pnls)
    losses = -sum(min(0, value) for value in pnls)
    try:
        annualized = ((1 + total) ** (annual_factor / len(valid)) - 1) * 100 if len(valid) and total > -1 else 0.0
    except OverflowError:
        annualized = None
    if annualized is not None and not math.isfinite(annualized):
        annualized = None
    return {
        "sharpe": per_bar * math.sqrt(annual_factor),
        "per_bar_sharpe": per_bar,
        "annual_factor": annual_factor,
        "total_return_pct": total * 100,
        "annualized_return_pct": annualized,
        "max_drawdown_pct": float(((peak - equity) / peak).max() * 100) if len(equity) else 0,
        "total_trades": len(trades),
        "profit_factor": gains / losses if losses > 0 else None,
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else 0,
        "skewness": float(stats.skew(valid, bias=False)) if len(valid) > 3 and std > 0 else 0.0,
        "kurtosis": float(stats.kurtosis(valid, fisher=False, bias=False)) if len(valid) > 3 and std > 0 else 3.0,
        "sample_length": len(valid),
        "net_returns": returns,
        "trades": trades,
    }


def simulate_strategy(
    definition: AlphaDefinition,
    bars: pd.DataFrame,
    *,
    start: int = 0,
    end: int | None = None,
    scores: pd.Series | None = None,
    trace: bool = False,
) -> dict:
    if definition.clock is not None and not isinstance(definition.clock, FixedDailyClockPolicy):
        raise ValueError("Versioned session strategies require session/minute replay")
    end = len(bars) if end is None else end
    if not 0 <= start < end <= len(bars):
        raise ValueError("Invalid simulation interval")
    # A fold cannot inspect even the validity of observations after its boundary.
    frame = bars.iloc[:end].rename(columns=str.lower)
    required = ["open", "high", "low", "close"]
    if not set(required) <= set(frame.columns):
        raise ValueError("OHLC observations required for bracket validation")
    values = frame[required].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Nonfinite, missing or nonpositive OHLC observations")
    if (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("Inconsistent OHLC observations")
    if scores is None:
        scores = alpha_scores(definition, frame)
    else:
        scores = scores.iloc[:end]
        if not scores.index.equals(frame.index):
            raise ValueError("Scores and prices must align exactly")
    if np.isinf(scores.to_numpy(dtype=float)).any():
        raise ValueError("Infinite feature scores are invalid")
    observations = entry_intents(definition, frame, scores)
    intents = {i: proposal for i in range(max(1, start), end) if (proposal := observations[i - 1]) is not None}
    result = simulate_execution(frame, intents, definition.execution, start=start, trace=trace)
    scored = int(scores.shift(1).iloc[start:end].notna().sum())
    result["feature_coverage"] = {
        "bars": len(result["net_returns"]),
        "scored_bars": scored,
        "unscored_bars": len(result["net_returns"]) - scored,
        "score_fraction": scored / len(result["net_returns"]),
    }
    result["execution_scope"] = (
        "daily_bar_policy" if definition.timeframe == "1d" else "diagnostic_intraday_session_unverified"
    )
    return result


class SimulationEventKind(StrEnum):
    ORDER_CREATED = "order_created"
    ENTRY_EXPIRED = "entry_expired"
    HOLDING_EXPIRED = "holding_expired"
    ENTRY_FILLED = "entry_filled"
    EXIT_FILLED = "exit_filled"
    STOP_UPDATED = "stop_updated"


@dataclass(frozen=True)
class BracketIntent:
    direction: int
    limit: float
    stop: float
    target: float
    signal_timestamp: str


def entry_intents(definition: AlphaDefinition, frame: pd.DataFrame, scores: pd.Series) -> list[BracketIntent | None]:
    """Build immutable proposals from each completed signal bar, without execution state."""
    directions = entry_directions(scores, definition)
    policy = definition.execution
    atr = strategy_atr(frame, policy)
    lows = frame.low.rolling(policy.swing_window).min()
    highs = frame.high.rolling(policy.swing_window).max()
    proposals = []
    for i in range(len(frame)):
        proposal = None
        if directions.iloc[i] and pd.notna(atr.iloc[i]):
            direction = int(directions.iloc[i])
            limit = entry_limit(float(frame.close.iloc[i]), policy)
            try:
                stop, target = bracket_prices(
                    limit, direction, float(atr.iloc[i]), float(lows.iloc[i]), float(highs.iloc[i]), policy
                )
                proposal = BracketIntent(direction, limit, stop, target, str(frame.index[i]))
            except ValueError:
                pass
        proposals.append(proposal)
    return proposals


def simulate_execution(
    frame: pd.DataFrame, intents: dict[int, BracketIntent], policy: AlphaExecutionPolicy, *, start=0, trace=False
) -> dict:
    """One execution state machine for coarse validation and observed minute replay.

    Event times identify OHLC bar starts, not fabricated exchange execution times.
    A close-observed stop update affects only the following execution bar.
    """
    end = len(frame)
    if not 0 <= start < end:
        raise ValueError("Invalid execution interval")
    values = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    events = []
    order_number = 0

    def emit(kind, **payload):
        if trace:
            events.append({"kind": kind, "bar_start": str(frame.index[i]), "order_number": order_number, **payload})

    cash = equity = 1.0
    quantity = 0.0
    direction = 0
    entry = stop = target = initial_risk = entry_fee = entry_equity = 0.0
    entry_timestamp = ""
    trades = []
    entries = []
    pending = None
    lifetime = policy.lifetime if isinstance(policy, TimedAlphaExecutionPolicy) else None
    pending_deadline = holding_deadline = None
    returns = pd.Series(0.0, index=frame.index[start:end], dtype=float)
    for i in range(start, end):
        before = equity
        intrabar_entry = False
        o, h, low, close = values[i]
        now = utc_timestamp(frame.index[i]) if lifetime else None
        if pending is not None and pending_deadline is not None and now is not None and now >= pending_deadline:
            emit(SimulationEventKind.ENTRY_EXPIRED, deadline=pending_deadline.isoformat())
            pending, pending_deadline = None, None
        # A fold starts flat; only proposals eligible on this clock may enter.
        if direction == 0 and pending is None and i in intents:
            pending = intents[i]
            pending_deadline = lifetime.entry_deadline(now) if lifetime and now is not None else None
            order_number += 1
            emit(
                SimulationEventKind.ORDER_CREATED,
                signal_timestamp=pending.signal_timestamp,
                direction=pending.direction,
                limit=pending.limit,
                stop=pending.stop,
                target=pending.target,
            )
        if pending is not None:
            proposed, limit, next_stop, next_target = pending.direction, pending.limit, pending.stop, pending.target
            marketable = proposed * (o - limit) <= 0
            touched = low <= limit if proposed == 1 else h >= limit
            if marketable or touched:
                direction, stop, target = proposed, next_stop, next_target
                entry, entry_equity = (o if marketable else limit), equity
                intrabar_entry = not marketable
                quantity = equity / entry
                initial_risk = direction * (limit - stop)  # same original reservation risk as the live journal
                entry_fee = quantity * entry * policy.friction_per_side
                cash -= direction * quantity * entry + entry_fee
                entry_timestamp = str(frame.index[i])
                holding_deadline = lifetime.holding_deadline(now) if lifetime and now is not None else None
                entries.append({"timestamp": entry_timestamp, "price": entry, "limit": limit, "direction": direction})
                emit(
                    SimulationEventKind.ENTRY_FILLED,
                    price=entry,
                    limit=limit,
                    direction=direction,
                    intrabar=intrabar_entry,
                    quantity=quantity,
                    fee=entry_fee,
                    entry_equity=entry_equity,
                )
                pending = None
        if direction:
            exit_price = None
            if not intrabar_entry and (direction * (o - stop) <= 0 or direction * (o - target) >= 0):
                exit_price = o
            elif holding_deadline is not None and now is not None and now >= holding_deadline:
                exit_price = o
                emit(SimulationEventKind.HOLDING_EXPIRED, deadline=holding_deadline.isoformat())
            elif low <= stop if direction == 1 else h >= stop:
                exit_price = stop
            elif (h >= target if direction == 1 else low <= target) and (
                not intrabar_entry or direction * (close - target) >= 0
            ):
                exit_price = target
            if exit_price is not None:
                exit_fee = quantity * exit_price * policy.friction_per_side
                cash += direction * quantity * exit_price - exit_fee
                pnl = direction * quantity * (exit_price - entry) - entry_fee - exit_fee
                trades.append(
                    {
                        "entry_timestamp": entry_timestamp,
                        "exit_timestamp": str(frame.index[i]),
                        "entry_price": entry,
                        "exit_price": exit_price,
                        "direction": direction,
                        "net_return": pnl / entry_equity,
                    }
                )
                emit(
                    SimulationEventKind.EXIT_FILLED,
                    price=exit_price,
                    net_return=pnl / entry_equity,
                    quantity=quantity,
                    fee=exit_fee,
                    entry_equity=entry_equity,
                    gross_pnl=direction * quantity * (exit_price - entry),
                    net_pnl=pnl,
                    phase="open" if exit_price == o and not intrabar_entry else "intrabar",
                )
                direction, quantity = 0, 0
            else:
                next_stop = trailing_price(entry, close, stop, initial_risk, direction, policy)
                if next_stop != stop:
                    emit(
                        SimulationEventKind.STOP_UPDATED,
                        old_stop=stop,
                        stop=next_stop,
                        phase="close",
                        effective="next_execution_bar",
                    )
                stop = next_stop
        equity = cash + direction * quantity * close
        if equity <= 0:
            raise ValueError("Strategy exhausted research capital")
        returns.iloc[i - start] = equity / before - 1
    result = return_statistics(returns, trades)
    if trace:
        result["events"] = events
    result["open_position"] = bool(direction)
    result["pending_entry"] = pending is not None
    result["entries"] = entries
    return result
