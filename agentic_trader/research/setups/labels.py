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
    "LIVE_ARM",
    "REGULAR_SESSION_HOURS_NY",
    "BracketArm",
    "BracketHit",
    "BracketOutcome",
    "SetupLevels",
    "TrailSpec",
    "label_bracket",
    "regular_session_bars",
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
                f"SHORT requires target < entry < stop, got target={self.target}, entry={self.entry}, stop={self.stop}"
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
    final_stop: float | None = None  # stop in force at exit (planned stop if never ratcheted)
    ratchet_moves: int = 0


@dataclass(frozen=True)
class TrailSpec:
    """Close-based ratchet: once gain reaches trigger_r, trail distance_r behind the close."""

    trigger_r: float
    distance_r: float


@dataclass(frozen=True)
class BracketArm:
    """An alternative exit policy; target_r None means no target (except for LIVE_ARM)."""

    name: str
    target_r: float | None
    trail: TrailSpec | None


LIVE_ARM = BracketArm(name="A0_live", target_r=None, trail=None)


def regular_session_bars(hourly: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(hourly.index)
    local_hour = index.tz_convert(ET_TZ).hour
    return hourly.loc[local_hour.isin(REGULAR_SESSION_HOURS_NY)]


def _immature(
    holding_sessions: int = 0,
    entry_time: datetime | None = None,
    entry_price: float | None = None,
    *,
    final_stop: float | None = None,
    ratchet_moves: int = 0,
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
        final_stop=final_stop,
        ratchet_moves=ratchet_moves,
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
    final_stop: float,
    ratchet_moves: int,
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
        final_stop=final_stop,
        ratchet_moves=ratchet_moves,
    )


def label_bracket(
    levels: SetupLevels,
    decision_at: datetime,
    hourly: pd.DataFrame,
    *,
    max_hold_sessions: int,
    cost_bps_per_side: float = 0.0,
    arm: BracketArm | None = None,
) -> BracketOutcome:
    """Walk regular-session hourly bars to see whether the target or stop hits first.

    Conservative by construction: a bar that touches both levels counts as the
    stop, and a gap through either level fills at that bar's open rather than
    the level itself. An optional ``arm`` swaps the target (a multiple of the
    planned risk, or none) and adds a close-based trailing ratchet that takes
    effect from the next bar.
    """
    regular = regular_session_bars(hourly)
    if regular.empty:
        return _immature(final_stop=levels.stop)

    entry_positions = regular.index >= decision_at
    if not entry_positions.any():
        return _immature(final_stop=levels.stop)

    entry_idx = int(entry_positions.argmax())
    entry_bar = regular.iloc[entry_idx]
    entry_time = regular.index[entry_idx]
    entry_price = float(entry_bar["Open"])
    risk_unit = abs(levels.entry - levels.stop)
    is_long = levels.direction == "LONG"
    sign = 1.0 if is_long else -1.0
    target: float | None
    if arm is None or arm == LIVE_ARM:
        target = levels.target
    elif arm.target_r is None:
        target = None
    else:
        target = levels.entry + sign * arm.target_r * risk_unit
    trail = None if arm is None else arm.trail
    stop = levels.stop
    moves = 0

    # Gap at entry: the entry bar's own open has already jumped through a level.
    entry_open = float(entry_bar["Open"])
    if is_long:
        gapped_stop = entry_open <= levels.stop
        gapped_target = target is not None and entry_open >= target
    else:
        gapped_stop = entry_open >= levels.stop
        gapped_target = target is not None and entry_open <= target
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
            final_stop=stop,
            ratchet_moves=moves,
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
            final_stop=stop,
            ratchet_moves=moves,
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
                    final_stop=stop,
                    ratchet_moves=moves,
                )

        bar_open = float(bar["Open"])
        bar_high = float(bar["High"])
        bar_low = float(bar["Low"])

        if is_long:
            stop_hit = bar_low <= stop
            target_hit = target is not None and bar_high >= target
        else:
            stop_hit = bar_high >= stop
            target_hit = target is not None and bar_low <= target

        if stop_hit:
            fill = min(bar_open, stop) if is_long else max(bar_open, stop)
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
                final_stop=stop,
                ratchet_moves=moves,
            )
        if target_hit and target is not None:
            fill = max(bar_open, target) if is_long else min(bar_open, target)
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
                final_stop=stop,
                ratchet_moves=moves,
            )

        if trail is not None:
            bar_close = float(bar["Close"])
            if sign * (bar_close - entry_price) >= trail.trigger_r * risk_unit:
                proposed = bar_close - sign * trail.distance_r * risk_unit
                if sign * (proposed - stop) > 0:
                    stop = proposed
                    moves += 1

        prev_bar = bar
        prev_time = bar_time

    return _immature(
        holding_sessions=len(dates_seen),
        entry_time=entry_time,
        entry_price=entry_price,
        final_stop=stop,
        ratchet_moves=moves,
    )
