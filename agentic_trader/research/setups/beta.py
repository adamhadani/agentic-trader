"""Trailing market beta of a candidate, frozen at its decision (descriptive context, never a gate).

``trailing_beta`` is the OLS slope of the candidate's daily close-to-close simple returns on the
market proxy's over the ``BETA_WINDOW_SESSIONS`` sessions ending at the last session whose New York
date precedes the decision's. Only past bars count, so the number is the one a reader could have
computed at the decision. Both series are expected to be dividend- and split-adjusted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ


if TYPE_CHECKING:
    from datetime import date


__all__ = ["BETA_MIN_OBSERVATIONS", "BETA_WINDOW_SESSIONS", "trailing_beta"]

BETA_WINDOW_SESSIONS = 120
BETA_MIN_OBSERVATIONS = 100


def _closes_by_date(daily: pd.DataFrame | None, session: date) -> pd.Series:
    """Finite closes keyed by New York date, for sessions strictly before ``session``."""
    if daily is None or daily.empty or "Close" not in daily.columns:
        return pd.Series(dtype=float)
    index = pd.DatetimeIndex(daily.index)
    if index.tz is None:
        return pd.Series(dtype=float)
    dates = np.array(index.tz_convert(ET_TZ).date, dtype=object)
    closes = pd.Series(pd.to_numeric(daily["Close"], errors="coerce").to_numpy(dtype=float), index=dates)
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    return closes[(closes.index < session) & np.isfinite(closes) & (closes > 0)]


def trailing_beta(
    symbol_daily: pd.DataFrame | None,
    market_daily: pd.DataFrame | None,
    session: date,
    *,
    window: int = BETA_WINDOW_SESSIONS,
    min_observations: int = BETA_MIN_OBSERVATIONS,
) -> tuple[float | None, str | None]:
    """``(beta, reason)``: the slope over the ``window`` returns ending before ``session``.

    The window is the ``window + 1`` closes ending at the symbol's last bar dated before
    ``session`` (New York date). A return counts only when both series have it over the same
    one-session step: the symbol's previous close must sit on the market's previous session, so a
    day missing from either series drops the returns it would have spanned instead of mixing
    horizons. ``reason`` is None when beta is finite, else ``no_daily_bars`` (an input has no usable
    bars before the session) or ``short_history`` (fewer than ``min_observations`` paired returns,
    or zero market variance).
    """
    symbol = _closes_by_date(symbol_daily, session)
    market = _closes_by_date(market_daily, session)
    if symbol.empty or market.empty:
        return None, "no_daily_bars"
    symbol = symbol.iloc[-(window + 1) :]
    market_dates = list(market.index)
    previous_market = dict(zip(market_dates[1:], market_dates[:-1], strict=True))
    market_ret = market.pct_change()
    symbol_ret = symbol.pct_change()
    symbol_prev = dict(zip(list(symbol.index)[1:], list(symbol.index)[:-1], strict=True))
    xs: list[float] = []
    ys: list[float] = []
    for day, ret in symbol_ret.iloc[1:].items():
        if previous_market.get(day) != symbol_prev[day] or day not in market_ret.index:
            continue
        beta_x = float(market_ret.loc[day])
        if np.isfinite(ret) and np.isfinite(beta_x):
            xs.append(beta_x)
            ys.append(float(ret))
    if len(xs) < max(min_observations, 2):
        return None, "short_history"
    x, y = np.array(xs), np.array(ys)
    variance = float(np.var(x, ddof=1))
    if not variance > 0.0:
        return None, "short_history"
    beta = float(np.cov(y, x, ddof=1)[0, 1] / variance)
    if not np.isfinite(beta):
        return None, "short_history"
    return beta, None
