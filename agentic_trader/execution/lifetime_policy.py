"""Pure elapsed-time contract shared by research and broker lifecycle services."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from zoneinfo import ZoneInfo

from agentic_trader.constants import executes_asset_class


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
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


# Provenance key of a native card's entry lifetime. It is never `alpha_policy`, which also switches on
# alpha-only trailing, re-pricing and sizing behaviour.
NATIVE_ENTRY_LIFETIME_KEY = "entry_lifetime"
NATIVE_ENTRY_LIFETIME_VERSION = "session_close_v1"
# Five minutes after the regular close (16:05, or 13:05 on an early close): the closing auction has
# printed, and the host is still held awake (scripts/awake.sh holds 09:15-16:30 New York).
NATIVE_ENTRY_CLOSE_BUFFER_SECONDS = 300


@dataclass(frozen=True)
class NativeEntryLifetimePolicy:
    """A native card's resting bound: the earlier of its submission session's close + buffer and
    ``submitted_at + resting_seconds`` (the one-session outer bound). No holding deadline.

    Only the provenance marker resolves to this type; alpha, catalog and probe policies never do.
    """

    resting_seconds: int = DAILY_ENTRY_LIFETIME_SECONDS
    close_buffer_seconds: int = NATIVE_ENTRY_CLOSE_BUFFER_SECONDS
    version: str = NATIVE_ENTRY_LIFETIME_VERSION
    holding_seconds: ClassVar[None] = None

    def __post_init__(self):
        if self.version != NATIVE_ENTRY_LIFETIME_VERSION:
            raise ValueError("Unsupported native entry lifetime version")
        if type(self.resting_seconds) is not int or not 1 <= self.resting_seconds <= MAX_TRADE_LIFETIME_SECONDS:
            raise ValueError("Trade lifetimes require bounded positive integer seconds")
        if type(self.close_buffer_seconds) is not int or not 0 <= self.close_buffer_seconds <= 3_600:
            raise ValueError("The close buffer is an integer in 0..3600 seconds")

    def entry_deadline(self, submitted_at: datetime, sessions: Sequence[MarketCalendarDay] | None = None) -> datetime:
        """``sessions`` is the broker calendar for the submission date; ``None`` leaves the outer bound alone."""
        outer = aware_utc(submitted_at) + timedelta(seconds=self.resting_seconds)
        if sessions is None:
            return outer
        submitted = aware_utc(submitted_at).astimezone(_NEW_YORK)
        day = next((d for d in sessions if d.date == submitted.date() and d.is_trading_day), None)
        if day is None or day.close_time is None:
            raise SessionEvidenceError("submission_outside_regular_session")
        close = datetime.combine(day.date, day.close_time, tzinfo=_NEW_YORK) + timedelta(
            seconds=self.close_buffer_seconds
        )
        if submitted > close:
            raise SessionEvidenceError("submission_outside_regular_session")
        return min(outer, close.astimezone(UTC))

    def holding_deadline(self, filled_at: datetime) -> None:
        return None


LifetimePolicy = TradeLifetimePolicy | SessionLifetimePolicy | NativeEntryLifetimePolicy


def has_holding_deadline(policy: LifetimePolicy) -> bool:
    """Whether a filled position under ``policy`` still has a deadline the lifetime service must time."""
    return isinstance(policy, SessionLifetimePolicy) or policy.holding_seconds is not None


def lifetime_from_dict(document: Mapping[str, Any]) -> TradeLifetimePolicy | SessionLifetimePolicy:
    """Deserialize a stored alpha or catalog lifetime without rewriting historical documents."""
    if document.get("version") == SESSION_LIFETIME_VERSION:
        return SessionLifetimePolicy(**document)
    return TradeLifetimePolicy(**document)


def native_lifetime_from_dict(document: Mapping[str, Any]) -> NativeEntryLifetimePolicy:
    """A native card's marker. Markers written before the session-close bound hold the daily
    elapsed policy itself (``elapsed_utc_v2``, 57,600 s, no holding) and read as the same policy."""
    if document.get("version") == NATIVE_ENTRY_LIFETIME_VERSION:
        return NativeEntryLifetimePolicy(**document)
    if dict(document) == asdict(daily_entry_lifetime()):
        return NativeEntryLifetimePolicy()
    raise ValueError("Unsupported native entry lifetime marker")


def native_entry_lifetime_marker(config: AppConfig, asset_class: str) -> dict[str, Any] | None:
    """The marker a new native card records, or None: switch off, a broker that cannot cancel, or a
    card the broker cannot execute (a futures card under alpaca)."""
    if (
        config.execution.native_entry_lifetime != "session"
        or config.execution_mode != "alpaca"
        or not executes_asset_class(config.execution_mode, asset_class)
    ):
        return None
    return asdict(NativeEntryLifetimePolicy())


def _document(value: Any) -> Mapping[str, Any]:
    """A stored JSON column as an object: the ORM text, a parsed dict, or empty when absent."""
    if isinstance(value, str | bytes):
        value = json.loads(value) if value else None
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Stored policy document is not an object")  # noqa: TRY004
    return value


def entry_lifetime_of(signal: Mapping[str, Any]) -> LifetimePolicy | None:
    """The one entry-lifetime resolver: `alpha_policy["lifetime"]`, else a native card's marker.

    Raises ValueError for a malformed document, including a present but null alpha lifetime;
    lifetime callers send that to review.
    """
    alpha = _document(signal.get("alpha_policy"))
    native = "lifetime" not in alpha
    if not native:
        declared = alpha["lifetime"]
        if declared is None:
            raise ValueError("Alpha lifetime is declared but null")
    else:
        declared = _document(signal.get("decision_provenance")).get(NATIVE_ENTRY_LIFETIME_KEY)
    if declared is None:
        return None
    if not isinstance(declared, Mapping):
        raise ValueError("Entry lifetime is not an object")  # noqa: TRY004
    try:
        return native_lifetime_from_dict(declared) if native else lifetime_from_dict(declared)
    except TypeError as exc:
        raise ValueError(f"Malformed entry lifetime: {exc}") from exc
