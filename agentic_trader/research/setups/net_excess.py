"""The net-excess unit ``net_excess_r_v1``: cost-adjusted R net of the beta-matched market move.

One pure unit shared by the labeller, the frozen replay prior and the daily ``card_stats``
snapshot. ``cost_r = 2 * c / 1e4 * fill / risk_unit`` charges a per-name round-trip cost ``c`` (a
floor plus a square-root impact term from the name's past volatility and dollar volume), the
net R is clipped at +/- ``R_CLIP``, the beta-matched market return is subtracted and the result is
clipped again. Only sessions strictly before the decision's New York date feed the cost inputs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ


if TYPE_CHECKING:
    from datetime import date


__all__ = [
    "COST_CAP_BPS",
    "COST_FLOOR_BPS",
    "COST_MIN_SESSIONS",
    "COST_WINDOW_SESSIONS",
    "IMPACT_COEFFICIENT",
    "NET_EXCESS_UNIT",
    "REFERENCE_RISK_USD",
    "R_CLIP",
    "CostInputs",
    "cost_inputs",
    "name_cost_bps",
    "net_excess_r",
]

NET_EXCESS_UNIT = "net_excess_r_v1"
COST_FLOOR_BPS = 5.0
IMPACT_COEFFICIENT = 1.0
REFERENCE_RISK_USD = 1000.0
COST_CAP_BPS = 200.0
COST_WINDOW_SESSIONS = 20
COST_MIN_SESSIONS = 15
R_CLIP = 10.0


@dataclass(frozen=True)
class CostInputs:
    """Past volatility and dollar volume of a name; ``reason`` is set when either is missing."""

    sigma: float | None
    adv_usd: float | None
    reason: str | None


def cost_inputs(symbol_daily: pd.DataFrame | None, session: date) -> CostInputs:
    """Daily log-return volatility and median dollar volume over the sessions before ``session``.

    Uses the last ``COST_WINDOW_SESSIONS + 1`` finite closes dated strictly before ``session`` (New
    York date): sigma is the ddof=1 standard deviation of their log returns and ADV the median
    close * volume of the same ``COST_WINDOW_SESSIONS`` later sessions.
    """
    if symbol_daily is None or symbol_daily.empty or "Close" not in symbol_daily.columns:
        return CostInputs(None, None, "no_daily_bars")
    index = pd.DatetimeIndex(symbol_daily.index)
    if index.tz is None:
        return CostInputs(None, None, "no_daily_bars")
    volume = symbol_daily["Volume"] if "Volume" in symbol_daily.columns else pd.Series(np.nan, index=symbol_daily.index)
    frame = pd.DataFrame(
        {
            "close": pd.to_numeric(symbol_daily["Close"], errors="coerce").to_numpy(dtype=float),
            "volume": pd.to_numeric(volume, errors="coerce").to_numpy(dtype=float),
        },
        index=np.array(index.tz_convert(ET_TZ).date, dtype=object),
    )
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame[(frame.index < session) & np.isfinite(frame["close"]) & (frame["close"] > 0)]
    if frame.empty:
        return CostInputs(None, None, "no_daily_bars")
    window = frame.iloc[-(COST_WINDOW_SESSIONS + 1) :]
    returns = np.diff(np.log(window["close"].to_numpy()))
    if len(returns) < COST_MIN_SESSIONS:
        return CostInputs(None, None, "short_history")
    dollar_volume = (window["close"] * window["volume"]).iloc[1:].to_numpy()
    dollar_volume = dollar_volume[np.isfinite(dollar_volume)]
    if len(dollar_volume) == 0:
        return CostInputs(None, None, "short_history")
    sigma = float(np.std(returns, ddof=1))
    adv = float(np.median(dollar_volume))
    if not (math.isfinite(sigma) and math.isfinite(adv) and adv > 0):
        return CostInputs(None, None, "short_history")
    return CostInputs(sigma, adv, None)


def name_cost_bps(fill: float, risk_unit: float, inputs: CostInputs) -> float:
    """Per-side cost in bp: the floor plus square-root impact of the reference-risk order, capped."""
    if inputs.sigma is None or inputs.adv_usd is None or not (math.isfinite(fill) and math.isfinite(risk_unit)):
        return COST_FLOOR_BPS
    if risk_unit <= 0 or fill <= 0 or inputs.adv_usd <= 0:
        return COST_FLOOR_BPS
    notional = REFERENCE_RISK_USD * fill / risk_unit
    impact = IMPACT_COEFFICIENT * inputs.sigma * math.sqrt(notional / inputs.adv_usd) * 1e4
    return min(COST_CAP_BPS, COST_FLOOR_BPS + impact)


def _finite(*values: float | None) -> bool:
    return all(value is not None and math.isfinite(value) for value in values)


def net_excess_r(
    r: float | None,
    fill: float | None,
    risk_unit: float | None,
    cost_bps: float,
    beta: float | None,
    market_r: float | None,
) -> float | None:
    """``clip(clip(r - cost_r) - beta * market_r)``; None when any needed input is missing."""
    if not _finite(r, fill, risk_unit, cost_bps, beta, market_r):
        return None
    assert r is not None and fill is not None and risk_unit is not None
    assert beta is not None and market_r is not None
    if risk_unit <= 0:
        return None
    cost_r = 2 * cost_bps / 1e4 * fill / risk_unit
    net = min(R_CLIP, max(-R_CLIP, r - cost_r))
    return min(R_CLIP, max(-R_CLIP, net - beta * market_r))
