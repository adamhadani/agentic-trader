"""Pure, observed exchange sessions and regular-session bar construction.

Minute bars are start-labelled. A missing minute is unknown market evidence, not
permission to invent a flat price. No weekday calendar or price fallback is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

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
MAX_DECISION_SECONDS = 86400


@dataclass(frozen=True)
class SessionClockPolicy:
    """Immutable eligibility window for a new session-bar decision, not an order TTL."""

    decision_delay_seconds: int = 60
    max_lateness_seconds: int = 120
    bar_layout: str = SESSION_BAR_LAYOUT

    def __post_init__(self):
        if self.bar_layout != SESSION_BAR_LAYOUT:
            raise ValueError("Unsupported session clock layout")
        for name, minimum in (("decision_delay_seconds", 0), ("max_lateness_seconds", 1)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= MAX_DECISION_SECONDS:
                raise ValueError(f"{name} requires bounded integer seconds")

    def windows(self, closes: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
        available = closes + pd.Timedelta(seconds=self.decision_delay_seconds)
        return available, available + pd.Timedelta(seconds=self.max_lateness_seconds)


class ObservationStatus(StrEnum):
    CAPTURING = "capturing"
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


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
class SignalBarWindow:
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp


def session_bar_windows(session: TradingSession, timeframe: str) -> tuple[SignalBarWindow, ...]:
    """One canonical session-open anchored clock for replay and forward sampling."""
    if timeframe not in BAR_DURATIONS:
        raise ValueError("Supported signal timeframe required")
    begin = session.open
    windows = []
    while begin < session.close:
        end = min(begin + BAR_DURATIONS[timeframe], session.close)
        windows.append(SignalBarWindow(begin, end))
        begin = end
    return tuple(windows)


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


def session_execution_minutes(schedule: SessionSchedule, *, as_of) -> pd.DatetimeIndex:
    """Expected complete minute starts from the observed calendar, without imputation."""
    as_of = utc_timestamp(as_of).floor("min")
    expected = pd.DatetimeIndex([], tz="UTC")
    for session in schedule.sessions:
        end = min(session.close, as_of)
        if end > session.open:
            expected = expected.append(pd.date_range(session.open, end, freq="min", inclusive="left"))
    return expected


@dataclass(frozen=True)
class SessionBars:
    execution: pd.DataFrame
    signals: pd.DataFrame
    closed_at: pd.DatetimeIndex
    coverage: dict
    schedule: SessionSchedule

    def validate(self, timeframe: str) -> None:
        """Reject mismatched clocks even when a caller constructs a container directly."""
        if (
            timeframe not in BAR_DURATIONS
            or self.signals.attrs.get("bar_layout") != SESSION_BAR_LAYOUT
            or self.signals.attrs.get("timeframe") != timeframe
        ):
            raise ValueError("Session signal clock/layout does not match the definition")
        for index in (self.signals.index, self.execution.index, self.closed_at):
            if (
                not isinstance(index, pd.DatetimeIndex)
                or index.tz is None
                or index.hasnans
                or not index.is_unique
                or not index.is_monotonic_increasing
            ):
                raise ValueError("Unique ordered aware session clock required")
        if len(self.signals) != len(self.closed_at) or (self.closed_at <= self.signals.index).any():
            raise ValueError("Signal starts and session closes must align")
        if (self.closed_at - self.signals.index > BAR_DURATIONS[timeframe]).any():
            raise ValueError("Session signal duration exceeds its timeframe")
        if len(self.signals) > 1 and (self.closed_at[:-1] > self.signals.index[1:]).any():
            raise ValueError("Overlapping session signal intervals")
        if (
            self.execution.empty
            or self.execution.attrs.get("bar_layout") != SESSION_BAR_LAYOUT
            or self.execution.attrs.get("timeframe") != "1m"
        ):
            raise ValueError("Observed session execution clock required")
        for field in ("feed", "adjustment"):
            if self.execution.attrs.get(field) != self.signals.attrs.get(field):
                raise ValueError("Inconsistent session data contracts")
        end = self.execution.index[-1] + EXECUTION_BAR_DURATION
        if not self.execution.index.equals(session_execution_minutes(self.schedule, as_of=end)):
            raise ValueError("Incomplete or out-of-session execution clock")
        windows = [
            w
            for session in self.schedule.sessions
            for w in session_bar_windows(session, timeframe)
            if w.closed_at <= end
        ]
        starts = pd.DatetimeIndex([w.opened_at for w in windows], tz="UTC")
        closes = pd.DatetimeIndex([w.closed_at for w in windows], tz="UTC")
        if not starts.equals(self.signals.index) or not closes.equals(self.closed_at):
            raise ValueError("Signal intervals disagree with the observed session calendar")


@dataclass(frozen=True)
class SessionSnapshot:
    """A live read's actual transport receipt, separate from historical bar closure."""

    bars: SessionBars
    requested_at: pd.Timestamp
    received_at: pd.Timestamp
    symbol: str

    def __post_init__(self):
        if not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError("Explicit normalized snapshot symbol required")
        for name in ("requested_at", "received_at"):
            object.__setattr__(self, name, utc_timestamp(getattr(self, name)))
        self.bars.validate(self.bars.signals.attrs.get("timeframe"))
        if (
            self.requested_at > self.received_at
            or (self.bars.closed_at > self.received_at).any()
            or (self.bars.execution.index + EXECUTION_BAR_DURATION > self.received_at).any()
        ):
            raise ValueError("Snapshot receipt must follow request and every completed observation")


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
    minute = EXECUTION_BAR_DURATION
    frame = minutes.loc[minutes.index + minute <= as_of].rename(columns=str.lower).copy()
    frame.index = frame.index.tz_convert("UTC")
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Unique ordered minute observations required")
    if not set(OHLCV) <= set(frame.columns):
        raise ValueError("OHLCV observations required")
    expected = session_execution_minutes(schedule, as_of=as_of)
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
        for window in session_bar_windows(session, timeframe):
            begin, end = window.opened_at, window.closed_at
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
    signals = pd.DataFrame(rows, columns=OHLCV, index=pd.DatetimeIndex(starts, tz="UTC"), dtype=float)
    signals.attrs.update(minutes.attrs, timeframe=timeframe, bar_layout=SESSION_BAR_LAYOUT)
    execution.attrs.update(minutes.attrs, timeframe="1m", bar_layout=SESSION_BAR_LAYOUT)
    return SessionBars(execution, signals, pd.DatetimeIndex(closes, tz="UTC"), coverage, schedule)
