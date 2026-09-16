"""Pure, observed exchange sessions and regular-session bar construction.

Minute bars are start-labelled. A missing minute is unknown market evidence, not
permission to invent a flat price. No weekday calendar or price fallback is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ


BAR_DURATIONS = {
    "15m": pd.Timedelta(minutes=15),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}
EXECUTION_BAR_DURATION = pd.Timedelta(minutes=1)
SESSION_BAR_LAYOUT = "rth_open_v1"
FIXED_BAR_LAYOUT = "fixed_duration_v1"
OHLCV = ("open", "high", "low", "close", "volume")


def utc_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError("Explicit timezone-aware timestamp required")
    return timestamp.tz_convert("UTC")


def fixed_bar_closes(frame: pd.DataFrame, timeframe: str) -> pd.DatetimeIndex:
    """The fixed-duration clock used by existing alpha versions.

    Unlabelled inputs retain that established contract. Explicit session/unknown
    layouts cannot be reinterpreted through it. Naive indices retain the existing
    UTC convention; deployment research manifests separately require aware data.
    """
    if frame.attrs.get("bar_layout", FIXED_BAR_LAYOUT) != FIXED_BAR_LAYOUT:
        raise ValueError("Incompatible bar clock: fixed-duration observations required")
    if timeframe not in BAR_DURATIONS or frame.attrs.get("timeframe", timeframe) != timeframe:
        raise ValueError("Bar timeframe does not match the fixed-duration clock")
    if (
        not isinstance(frame.index, pd.DatetimeIndex)
        or frame.index.hasnans
        or not frame.index.is_unique
        or not frame.index.is_monotonic_increasing
    ):
        raise ValueError("Unique, ordered, known observation timestamps required")
    index = frame.index.tz_localize("UTC") if frame.index.tz is None else frame.index.tz_convert("UTC")
    return index + BAR_DURATIONS[timeframe]


def completed_fixed_bars(frame: pd.DataFrame, timeframe: str, *, as_of=None) -> pd.DataFrame:
    """Select complete fixed-duration bars; this does not infer exchange closes."""
    if frame.empty:
        return frame
    closes = fixed_bar_closes(frame, timeframe)
    now = utc_timestamp(as_of if as_of is not None else datetime.now(UTC))
    result = frame.loc[closes <= now].copy()
    result.attrs.update(timeframe=timeframe, bar_layout=FIXED_BAR_LAYOUT)
    return result


@dataclass(frozen=True)
class TradingSession:
    date: date
    open: pd.Timestamp
    close: pd.Timestamp

    def __post_init__(self):
        for field in ("open", "close"):
            object.__setattr__(self, field, utc_timestamp(getattr(self, field)))
        if (
            self.open >= self.close
            or self.open.tz_convert(ET_TZ).date() != self.date
            or self.close.tz_convert(ET_TZ).date() != self.date
            or any(t != t.floor("min") for t in (self.open, self.close))
        ):
            raise ValueError("Ordered minute-aligned same-day exchange session required")


@dataclass(frozen=True)
class SessionSchedule:
    start: date
    end: date
    sessions: tuple[TradingSession, ...]
    source: str

    def __post_init__(self):
        if not self.source or self.start > self.end or not self.sessions:
            raise ValueError("Explicit observed session calendar required")
        dates = [s.date for s in self.sessions]
        if dates != sorted(set(dates)) or any(not self.start <= d <= self.end for d in dates):
            raise ValueError("Unique ordered sessions within the requested calendar range required")

    def document(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "source": self.source,
            "sessions": [
                {"date": s.date.isoformat(), "open": s.open.isoformat(), "close": s.close.isoformat()}
                for s in self.sessions
            ],
        }

    @classmethod
    def from_document(cls, document: dict) -> SessionSchedule:
        return cls(
            date.fromisoformat(document["start"]),
            date.fromisoformat(document["end"]),
            tuple(
                TradingSession(date.fromisoformat(s["date"]), pd.Timestamp(s["open"]), pd.Timestamp(s["close"]))
                for s in document["sessions"]
            ),
            document["source"],
        )


class SessionCoverageError(ValueError):
    def __init__(self, coverage: dict):
        self.coverage = coverage
        super().__init__(f"Unknown execution prices: {coverage['missing_minutes']} missing regular-session minutes")


@dataclass(frozen=True)
class SessionBars:
    execution: pd.DataFrame
    signals: pd.DataFrame
    closed_at: pd.DatetimeIndex
    coverage: dict


def build_session_bars(minutes: pd.DataFrame, schedule: SessionSchedule, timeframe: str, *, as_of) -> SessionBars:
    """Aggregate only completed RTH minutes, anchored at each observed session open.

    The last signal bar is clipped at an observed early/regular close. Session
    gaps are retained. Historical corrected bars are not point-in-time snapshots.
    """
    if timeframe not in BAR_DURATIONS:
        raise ValueError("Supported signal timeframe required")
    if minutes.attrs.get("timeframe") != "1m" or minutes.attrs.get("adjustment") != "raw":
        raise ValueError("Explicit raw one-minute observations required")
    if not isinstance(minutes.index, pd.DatetimeIndex) or minutes.index.tz is None or minutes.index.hasnans:
        raise ValueError("Timezone-aware minute observations required")
    as_of = utc_timestamp(as_of)
    duration = BAR_DURATIONS[timeframe]
    minute = EXECUTION_BAR_DURATION
    frame = minutes.loc[minutes.index + minute <= as_of].rename(columns=str.lower).copy()
    frame.index = frame.index.tz_convert("UTC")
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Unique ordered minute observations required")
    if not set(OHLCV) <= set(frame.columns):
        raise ValueError("OHLCV observations required")
    expected = pd.DatetimeIndex([], tz="UTC")
    for session in schedule.sessions:
        end = min(session.close, as_of.floor("min"))
        if end > session.open:
            expected = expected.append(pd.date_range(session.open, end, freq="min", inclusive="left"))
    if expected.empty:
        raise ValueError("No completed execution session minutes")
    present = frame.index.intersection(expected)
    missing = expected.difference(frame.index)
    coverage = {
        "expected_minutes": len(expected),
        "observed_minutes": len(present),
        "missing_minutes": len(missing),
        "missing_examples": [t.isoformat() for t in missing[:10]],
        "excluded_minutes": len(frame) - len(present),
    }
    if len(missing):
        raise SessionCoverageError(coverage)
    execution = frame.loc[expected, list(OHLCV)].copy()
    values = execution.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("Finite positive OHLC and nonnegative volume required")
    if (
        (execution.high < execution[["open", "low", "close"]].max(axis=1))
        | (execution.low > execution[["open", "high", "close"]].min(axis=1))
    ).any():
        raise ValueError("Consistent execution OHLC required")
    rows, starts, closes = [], [], []
    for session in schedule.sessions:
        begin = session.open
        while begin < session.close:
            end = min(begin + duration, session.close)
            if end > as_of:
                break
            observed = execution.iloc[execution.index.searchsorted(begin) : execution.index.searchsorted(end)]
            rows.append(
                [
                    observed.open.iloc[0],
                    observed.high.max(),
                    observed.low.min(),
                    observed.close.iloc[-1],
                    observed.volume.sum(),
                ]
            )
            starts.append(begin)
            closes.append(end)
            begin = end
    signals = pd.DataFrame(rows, columns=OHLCV, index=pd.DatetimeIndex(starts, tz="UTC"), dtype=float)
    signals.attrs.update(minutes.attrs, timeframe=timeframe, bar_layout=SESSION_BAR_LAYOUT)
    execution.attrs.update(minutes.attrs, timeframe="1m", bar_layout=SESSION_BAR_LAYOUT)
    return SessionBars(execution, signals, pd.DatetimeIndex(closes, tz="UTC"), coverage)
