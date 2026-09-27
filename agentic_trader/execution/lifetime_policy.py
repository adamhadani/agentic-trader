"""Pure elapsed-time contract shared by research and broker lifecycle services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo


if TYPE_CHECKING:
    from agentic_trader.market.session import MarketCalendarDay

MAX_TRADE_LIFETIME_SECONDS = int(timedelta(days=31).total_seconds())
TRADE_LIFETIME_VERSION = "elapsed_utc_v1"
TRADE_LIFETIME_VERSION_INDEPENDENT = "elapsed_utc_v2"

# One regular trading session. Research stamps a daily order at its entry bar's label, and
# validate_sampling guarantees consecutive daily labels are at least 24 h apart, so label + 16 h
# always expires before the next bar's fill check. Live stamps the order at submission inside
# 09:30-16:00 New York, so + 16 h expires before the next open. 86,400 would give a live order
# two partial sessions; anything under 23,400 would not survive its own session.
DAILY_ENTRY_LIFETIME_SECONDS = 57_600


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Lifetime clocks require timezone-aware datetimes")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TradeLifetimePolicy:
    resting_seconds: int | None
    holding_seconds: int | None
    version: str = TRADE_LIFETIME_VERSION

    def __post_init__(self):
        if self.version not in (TRADE_LIFETIME_VERSION, TRADE_LIFETIME_VERSION_INDEPENDENT):
            raise ValueError("Unsupported trade lifetime version")
        if self.version == TRADE_LIFETIME_VERSION and (self.resting_seconds is None or self.holding_seconds is None):
            raise ValueError("elapsed_utc_v1 requires both entry and holding lifetimes")
        if (
            self.version == TRADE_LIFETIME_VERSION_INDEPENDENT
            and self.resting_seconds is None
            and self.holding_seconds is None
        ):
            raise ValueError("At least one independent trade lifetime is required")
        for value in (self.resting_seconds, self.holding_seconds):
            if value is not None and (type(value) is not int or not 1 <= value <= MAX_TRADE_LIFETIME_SECONDS):
                raise ValueError("Trade lifetimes require bounded positive integer seconds")

    def entry_deadline(self, submitted_at: datetime) -> datetime | None:
        if self.resting_seconds is None:
            return None
        return aware_utc(submitted_at) + timedelta(seconds=self.resting_seconds)

    def holding_deadline(self, filled_at: datetime) -> datetime | None:
        if self.holding_seconds is None:
            return None
        return aware_utc(filled_at) + timedelta(seconds=self.holding_seconds)


def daily_entry_lifetime() -> TradeLifetimePolicy:
    """The only lifetime a native-daily (semantics version 5) alpha may declare."""
    return TradeLifetimePolicy(
        resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS,
        holding_seconds=None,
        version=TRADE_LIFETIME_VERSION_INDEPENDENT,
    )


SESSION_LIFETIME_VERSION = "session_count_v1"
MAX_HOLDING_SESSIONS = 60
SESSION_CLOSE_BUFFER = timedelta(minutes=15)
# A closing-auction print can be stamped just after the bell (16:00:00.250, or 13:00:00.x on an
# early close). It is still that session's fill, so it counts as session 1.
SESSION_FILL_GRACE = timedelta(seconds=60)
_NEW_YORK = ZoneInfo("America/New_York")


class SessionEvidenceError(ValueError):
    """The broker calendar cannot establish a session-counted deadline; callers REVIEW, never guess."""


def _clock(value: str) -> time:
    try:
        hours, minutes = (int(part) for part in value.split(":"))
        return time(hours, minutes)
    except (ValueError, TypeError) as exc:
        raise ValueError("close_time_et requires HH:MM") from exc


@dataclass(frozen=True)
class SessionLifetimePolicy:
    """One-session resting entry, then a holding exit at ``close_time_et`` New York on session N.

    The fill's own regular session is session 1, exactly as the study's ``label_bracket``
    counts it. On an early-close session the exit moves to 15 minutes before that close.
    """

    resting_seconds: int
    holding_sessions: int
    close_time_et: str = "15:45"
    version: str = SESSION_LIFETIME_VERSION

    def __post_init__(self):
        if self.version != SESSION_LIFETIME_VERSION:
            raise ValueError("Unsupported session lifetime version")
        if type(self.resting_seconds) is not int or not 1 <= self.resting_seconds <= MAX_TRADE_LIFETIME_SECONDS:
            raise ValueError("Trade lifetimes require bounded positive integer seconds")
        if type(self.holding_sessions) is not int or not 1 <= self.holding_sessions <= MAX_HOLDING_SESSIONS:
            raise ValueError(f"Holding sessions must be an integer in 1..{MAX_HOLDING_SESSIONS}")
        _clock(self.close_time_et)

    def entry_deadline(self, submitted_at: datetime) -> datetime:
        return aware_utc(submitted_at) + timedelta(seconds=self.resting_seconds)

    def holding_deadline(self, filled_at: datetime, sessions: Sequence[MarketCalendarDay]) -> datetime:
        fill = aware_utc(filled_at).astimezone(_NEW_YORK)
        regular = sorted(
            (d for d in sessions if d.is_trading_day and d.open_time is not None and d.close_time is not None),
            key=lambda d: d.date,
        )
        first = next((d for d in regular if d.date == fill.date()), None)
        if first is None or first.open_time is None or first.close_time is None:
            raise SessionEvidenceError("fill_outside_regular_session")
        opened = datetime.combine(first.date, first.open_time, tzinfo=_NEW_YORK)
        closed = datetime.combine(first.date, first.close_time, tzinfo=_NEW_YORK)
        if not opened <= fill <= closed + SESSION_FILL_GRACE:
            raise SessionEvidenceError("fill_outside_regular_session")
        held = [d for d in regular if d.date >= first.date]
        if len(held) < self.holding_sessions:
            raise SessionEvidenceError("session_calendar_short")
        last = held[self.holding_sessions - 1]
        if last.close_time is None:
            raise SessionEvidenceError("session_calendar_short")
        target = datetime.combine(last.date, _clock(self.close_time_et), tzinfo=_NEW_YORK)
        close = datetime.combine(last.date, last.close_time, tzinfo=_NEW_YORK)
        return min(target, close - SESSION_CLOSE_BUFFER).astimezone(UTC)


LifetimePolicy = TradeLifetimePolicy | SessionLifetimePolicy


def lifetime_from_dict(document: Mapping[str, Any]) -> LifetimePolicy:
    """Deserialize a stored lifetime without rewriting historical documents."""
    if document.get("version") == SESSION_LIFETIME_VERSION:
        return SessionLifetimePolicy(**document)
    return TradeLifetimePolicy(**document)
