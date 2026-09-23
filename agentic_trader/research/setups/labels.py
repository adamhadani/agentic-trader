"""Bracket labeler: measures whether a setup's stop or target would hit first.

Pure and I/O-free so it can be shared verbatim by an offline study and by a
live, read-only outcomes report. Reuses ``REGULAR_SESSION_HOURS_NY`` (the
New York regular-session hour buckets already used to gate hourly-bar
coverage) so bracket walking only rests on the hours Alpaca's hourly bars
actually cover during regular trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.screeners.coverage import REGULAR_SESSION_HOURS_NY


__all__ = [
    "REGULAR_SESSION_HOURS_NY",
    "BracketHit",
    "BracketOutcome",
    "SetupLevels",
    "label_bracket",
]


class BracketHit(StrEnum):
    TARGET = "target"
    STOP = "stop"
    TIMEOUT = "timeout"
    IMMATURE = "immature"


@dataclass(frozen=True)
class SetupLevels:
    """A planned bracket: entry is the risk-unit reference, not necessarily the fill."""

    direction: str  # "LONG" | "SHORT"
    entry: float
    stop: float
    target: float

    def __post_init__(self) -> None:
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be 'LONG' or 'SHORT', got {self.direction!r}")
        if self.direction == "LONG":
            if not (self.stop < self.entry < self.target):
                raise ValueError(
                    f"LONG requires stop < entry < target, got stop={self.stop}, "
                    f"entry={self.entry}, target={self.target}"
                )
        elif not (self.target < self.entry < self.stop):
            raise ValueError(
                f"SHORT requires target < entry < stop, got target={self.target}, "
                f"entry={self.entry}, stop={self.stop}"
            )


@dataclass(frozen=True)
class BracketOutcome:
    hit: BracketHit
    entry_time: datetime | None
    entry_price: float | None
    exit_time: datetime | None
    exit_price: float | None
    r: float | None  # None iff IMMATURE
    r_cost: float | None
    holding_sessions: int  # distinct NY session dates touched from entry bar through exit bar


def _regular_session_bars(hourly: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(hourly.index)
    local_hour = index.tz_convert(ET_TZ).hour
    return hourly.loc[local_hour.isin(REGULAR_SESSION_HOURS_NY)]


def _immature(
    holding_sessions: int = 0,
    entry_time: datetime | None = None,
    entry_price: float | None = None,
) -> BracketOutcome:
    return BracketOutcome(
        hit=BracketHit.IMMATURE,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=None,
        exit_price=None,
        r=None,
        r_cost=None,
        holding_sessions=holding_sessions,
    )


def _resolved(
    *,
    hit: BracketHit,
    entry_time: datetime,
    entry_price: float,
    exit_time: datetime,
    exit_price: float,
    holding_sessions: int,
    levels: SetupLevels,
    risk_unit: float,
    cost_bps_per_side: float,
) -> BracketOutcome:
    sign = 1.0 if levels.direction == "LONG" else -1.0
    r = sign * (exit_price - entry_price) / risk_unit
    r_cost = r - 2 * cost_bps_per_side / 1e4 * entry_price / risk_unit
    return BracketOutcome(
        hit=hit,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        r=r,
        r_cost=r_cost,
        holding_sessions=holding_sessions,
    )


def label_bracket(
    levels: SetupLevels,
    decision_at: datetime,
    hourly: pd.DataFrame,
    *,
    max_hold_sessions: int,
    cost_bps_per_side: float = 0.0,
) -> BracketOutcome:
    """Walk regular-session hourly bars to see whether the target or stop hits first.

    Conservative by construction: a bar that touches both levels counts as the
    stop, and a gap through either level fills at that bar's open rather than
    the level itself.
    """
    regular = _regular_session_bars(hourly)
    if regular.empty:
        return _immature()

    entry_positions = regular.index >= decision_at
    if not entry_positions.any():
        return _immature()

    entry_idx = int(entry_positions.argmax())
    entry_bar = regular.iloc[entry_idx]
    entry_time = regular.index[entry_idx]
    entry_price = float(entry_bar["Open"])
    risk_unit = abs(levels.entry - levels.stop)
    is_long = levels.direction == "LONG"

    # Gap at entry: the entry bar's own open has already jumped through a level.
    entry_open = float(entry_bar["Open"])
    if is_long:
        gapped_stop = entry_open <= levels.stop
        gapped_target = entry_open >= levels.target
    else:
        gapped_stop = entry_open >= levels.stop
        gapped_target = entry_open <= levels.target
    if gapped_stop:
        return _resolved(
            hit=BracketHit.STOP,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=entry_time,
            exit_price=entry_open,
            holding_sessions=1,
            levels=levels,
            risk_unit=risk_unit,
            cost_bps_per_side=cost_bps_per_side,
        )
    if gapped_target:
        return _resolved(
            hit=BracketHit.TARGET,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=entry_time,
            exit_price=entry_open,
            holding_sessions=1,
            levels=levels,
            risk_unit=risk_unit,
            cost_bps_per_side=cost_bps_per_side,
        )

    walk = regular.iloc[entry_idx:]
    et_dates = pd.DatetimeIndex(walk.index).tz_convert(ET_TZ).normalize()

    dates_seen: list[pd.Timestamp] = []
    prev_bar: pd.Series | None = None
    prev_time: pd.Timestamp | None = None
    for position in range(len(walk)):
        bar = walk.iloc[position]
        bar_time = walk.index[position]
        bar_date = et_dates[position]

        if bar_date not in dates_seen:
            dates_seen.append(bar_date)
            if len(dates_seen) == max_hold_sessions + 1:
                if prev_bar is None or prev_time is None:
                    raise ValueError("max_hold_sessions must be at least 1")
                return _resolved(
                    hit=BracketHit.TIMEOUT,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_time=prev_time,
                    exit_price=float(prev_bar["Close"]),
                    holding_sessions=max_hold_sessions,
                    levels=levels,
                    risk_unit=risk_unit,
                    cost_bps_per_side=cost_bps_per_side,
                )

        bar_open = float(bar["Open"])
        bar_high = float(bar["High"])
        bar_low = float(bar["Low"])

        if is_long:
            stop_hit = bar_low <= levels.stop
            target_hit = bar_high >= levels.target
        else:
            stop_hit = bar_high >= levels.stop
            target_hit = bar_low <= levels.target

        if stop_hit:
            if is_long:
                fill = bar_open if bar_open <= levels.stop else levels.stop
            else:
                fill = bar_open if bar_open >= levels.stop else levels.stop
            return _resolved(
                hit=BracketHit.STOP,
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=bar_time,
                exit_price=fill,
                holding_sessions=len(dates_seen),
                levels=levels,
                risk_unit=risk_unit,
                cost_bps_per_side=cost_bps_per_side,
            )
        if target_hit:
            if is_long:
                fill = bar_open if bar_open >= levels.target else levels.target
            else:
                fill = bar_open if bar_open <= levels.target else levels.target
            return _resolved(
                hit=BracketHit.TARGET,
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=bar_time,
                exit_price=fill,
                holding_sessions=len(dates_seen),
                levels=levels,
                risk_unit=risk_unit,
                cost_bps_per_side=cost_bps_per_side,
            )

        prev_bar = bar
        prev_time = bar_time

    return _immature(holding_sessions=len(dates_seen), entry_time=entry_time, entry_price=entry_price)
