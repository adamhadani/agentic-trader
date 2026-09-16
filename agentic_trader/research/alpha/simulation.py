"""Causal OHLC simulator for the immutable formulaic bracket policy.

One unit of starting capital, fully funded per entry, no pyramiding. Close-bar
signals create GTC limits at the observed close, eligible from the next bar. Limits
remain pending until touched; no fill may violate the limit. A bar
that touches both exits takes the stop first. Trailing observes the bar close and
becomes effective next bar; intrabar live trailing is an explicitly unmodelled
execution difference. Intrabar entry cannot claim a target observed before its
fill. Liquidity/partial fills and admission latency require shadow execution
evidence. Open trades and unfilled orders remain censored at a fold boundary.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.strategy import (
    alpha_scores,
    bracket_prices,
    entry_directions,
    entry_limit,
    strategy_atr,
    trailing_price,
)


def return_statistics(returns: pd.Series, trades: list[dict], annual_factor: float | None = None) -> dict:
    valid = returns.dropna()
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
    return {
        "sharpe": per_bar * math.sqrt(annual_factor),
        "per_bar_sharpe": per_bar,
        "annual_factor": annual_factor,
        "total_return_pct": total * 100,
        "annualized_return_pct": ((1 + total) ** (annual_factor / len(valid)) - 1) * 100
        if len(valid) and total > -1
        else 0,
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
) -> dict:
    end = len(bars) if end is None else end
    if not 0 <= start < end <= len(bars):
        raise ValueError("Invalid simulation interval")
    frame = bars.rename(columns=str.lower)
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
    scores = alpha_scores(definition, frame) if scores is None else scores
    if not scores.index.equals(frame.index):
        raise ValueError("Scores and prices must align exactly")
    directions = entry_directions(scores, definition)
    policy = definition.execution
    atr = strategy_atr(frame, policy)
    lows = frame.low.rolling(policy.swing_window).min()
    highs = frame.high.rolling(policy.swing_window).max()
    cash = equity = 1.0
    quantity = 0.0
    direction = 0
    entry = stop = target = initial_risk = entry_fee = entry_equity = 0.0
    entry_timestamp = ""
    trades = []
    entries = []
    pending = None
    returns = pd.Series(np.nan, index=frame.index[start:end], dtype=float)
    for i in range(start, end):
        before = equity
        had_position = bool(direction)
        intrabar_entry = False
        o, h, low, close = values[i]
        # Evaluate only prior complete observations; no inherited fold position.
        if direction == 0 and pending is None and i > 0 and directions.iloc[i - 1] and pd.notna(atr.iloc[i - 1]):
            proposed = int(directions.iloc[i - 1])
            try:
                next_stop, next_target = bracket_prices(
                    entry_limit(float(frame.close.iloc[i - 1]), policy),
                    proposed,
                    float(atr.iloc[i - 1]),
                    float(lows.iloc[i - 1]),
                    float(highs.iloc[i - 1]),
                    policy,
                )
            except ValueError:
                next_stop = next_target = math.nan
            if math.isfinite(next_stop):
                pending = (proposed, entry_limit(float(frame.close.iloc[i - 1]), policy), next_stop, next_target)
        if pending is not None:
            proposed, limit, next_stop, next_target = pending
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
                entries.append({"timestamp": entry_timestamp, "price": entry, "limit": limit, "direction": direction})
                pending = None
        if direction:
            exit_price = None
            if not intrabar_entry and (direction * (o - stop) <= 0 or direction * (o - target) >= 0):
                exit_price = o
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
                direction, quantity = 0, 0
            else:
                stop = trailing_price(entry, close, stop, initial_risk, direction, policy)
        equity = cash + direction * quantity * close
        if equity <= 0:
            raise ValueError("Strategy exhausted research capital")
        if i > 0 and (pd.notna(scores.iloc[i - 1]) or direction or had_position):
            returns.iloc[i - start] = equity / before - 1
    result = return_statistics(returns, trades)
    result["execution_scope"] = (
        "daily_bar_policy" if definition.timeframe == "1d" else "diagnostic_intraday_session_unverified"
    )
    result["open_position"] = bool(direction)
    result["pending_entry"] = pending is not None
    result["entries"] = entries
    return result
