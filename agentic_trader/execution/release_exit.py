"""The release exit (docs/superpowers/specs/2026-11-06-release-exit-filing-veto-design.md, section 3).

Once per New York trading day, on the first five-minute tick at or after ``close - minutes_before_close`` (the
trading calendar's close, so an early close is honoured), one pass looks at every tracked ``EXECUTED`` equity
position and asks whether its symbol reports earnings before the next session's open (``exits_today``). The
evidence is the union of the live Nasdaq lookup (the earnings blackout's calendar and cache) and the latest dated
capture: a qualifying report in either source counts, and with neither source usable the status is ``unknown``,
which never closes. Native positions are ``would_close`` in ``preview`` and ``close`` in ``enforce``; catalog,
probe and alpha-versioned positions are journaled ``exempt`` with their kind and never closed.

The pass is journaled once per date (stream ``release-exit``, key ``release-exit/{et_date}``), with its preview
notices in the same transaction. In ``enforce`` each ``close`` entry then goes through
``PositionCloseService.close_signal`` with a request ID stable across ticks and restarts within the trading day
(and new on the next day's pass), and the outcome is appended as its own ``release_exit_close`` event; a
non-submitted outcome queues a notice. Request IDs and notice keys carry the pass's trading day. Nothing here
halts trading, opens a lifetime review or retries a broker mutation, and journal, log and notice text carry
exception type names only. A notice names ``/close <signal_id>``, the single-position close.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sqlalchemy import select

from agentic_trader.agent.earnings import (
    EarningsCalendarProtocol,
    EarningsCaptureSource,
    EarningsLookup,
    EarningsTiming,
    timing_word,
)
from agentic_trader.constants import AssetClass, CloseRequestStatus, SignalStatus, normalize_asset_class
from agentic_trader.execution.durable import EventKind, NotificationKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.models import DomainEventRecord


if TYPE_CHECKING:
    from agentic_trader.config import ReleaseExitConfig
    from agentic_trader.data.earnings_capture import CapturedReports
    from agentic_trader.market.session import MarketCalendarProtocol
    from agentic_trader.storage.db import SignalDatabase
    from agentic_trader.storage.workflow import WorkflowStore

__all__ = [
    "NATIVE_SCOPE",
    "RELEASE_EXIT_STREAM",
    "ReleaseDecision",
    "ReleaseExitService",
    "ReleaseExitStore",
    "RunStatus",
    "decide",
    "exits_today",
    "failure_key",
    "preview_key",
    "release_request_id",
]

logger = logging.getLogger(__name__)

RELEASE_EXIT_STREAM = "release-exit"
NATIVE_SCOPE = "native"
SOURCE_LIVE, SOURCE_CAPTURE = "live", "capture"
# Far enough ahead to reach the next session across any exchange-holiday cluster.
NEXT_SESSION_SEARCH_DAYS = 10
# Notices are queued only up to the trading day's close plus this bound (16:30 on a regular day, the end of the
# operator alert window); later outcomes are journaled and logged only, never sent outside market hours.
NOTICE_BOUND_AFTER_CLOSE = timedelta(minutes=30)
_SUBMITTED = (CloseRequestStatus.SUBMITTED, CloseRequestStatus.COMPLETED)
_EQUITY = normalize_asset_class(AssetClass.EQUITY)

ReleaseStatus = Literal["exit", "none", "unknown"]
RunStatus = Literal["off", "closed_market", "waiting", "present", "done", "failed"]


class TradingCalendarUnavailable(RuntimeError):
    """The desk has no broker trading calendar, so the pass can never be timed."""


class NoNextTradingDay(RuntimeError):
    """The calendar shows no session within ``NEXT_SESSION_SEARCH_DAYS``."""


@dataclass(frozen=True)
class ReleaseDecision:
    """Whether a symbol reports before the next session's open, and on what evidence (spec section 3.4).

    ``exit``: a qualifying report in either source (``report_date``/``timing`` are the earliest qualifying reading;
    ``sources`` are the sources that show the symbol reporting in the window). ``none``: no qualifying report and at
    least one usable source (``sources`` are the usable ones). ``unknown``: no qualifying report, the live lookup
    unverified and the capture unusable.
    """

    status: ReleaseStatus
    report_date: date | None
    timing: EarningsTiming | None
    sources: tuple[str, ...]
    live_verified: bool


def exits_today(report_date: date, timing: EarningsTiming, today: date, next_day: date) -> bool:
    """Whether a report falls before ``next_day``'s open, seen from ``today``'s pass (spec section 3.3).

    On ``today`` a report after the close or at an unspecified time exits (a pre-market one has already happened);
    any date strictly between ``today`` and ``next_day`` (a weekend or holiday) exits; on ``next_day`` a pre-market
    or unspecified report exits, and an after-hours one is ``next_day``'s own pass.
    """
    if report_date == today:
        return timing != EarningsTiming.PRE_MARKET
    if today < report_date < next_day:
        return True
    if report_date == next_day:
        return timing != EarningsTiming.AFTER_HOURS
    return False


def decide(
    symbol: str,
    live: EarningsLookup | None,
    captured: CapturedReports | None,
    today: date,
    next_day: date,
) -> ReleaseDecision:
    """The union of the live lookup (None when it failed) and the capture (None when unusable) for one symbol.

    A report either source shows counts, and where the two disagree on timing any reading that exits today wins.
    """
    symbol = symbol.upper()
    live_verified = live is not None and live.verified
    readings: list[tuple[str, date, EarningsTiming]] = []
    if live is not None and live.event is not None and today <= live.event.date <= next_day:
        readings.append((SOURCE_LIVE, live.event.date, live.event.timing))
    if captured is not None:
        for report_date, rows in sorted(captured.rows.items()):
            if today <= report_date <= next_day:
                readings.extend((SOURCE_CAPTURE, report_date, timing) for name, timing in rows if name == symbol)
    qualifying = [reading for reading in readings if exits_today(reading[1], reading[2], today, next_day)]
    if qualifying:
        _, report_date, timing = min(qualifying, key=lambda reading: (reading[1], reading[0] != SOURCE_LIVE))
        sources = tuple(source for source in (SOURCE_LIVE, SOURCE_CAPTURE) if any(r[0] == source for r in readings))
        return ReleaseDecision("exit", report_date, timing, sources, live_verified)
    usable = tuple(
        source for source, ok in ((SOURCE_LIVE, live_verified), (SOURCE_CAPTURE, captured is not None)) if ok
    )
    if usable:
        return ReleaseDecision("none", None, None, usable, live_verified)
    return ReleaseDecision("unknown", None, None, (), live_verified)


def release_request_id(scope: str, signal_id: int, report_date: date, trading_day: date) -> str:
    """The close request ID of one pass: stable across ticks and restarts within ``trading_day``, so a repeated
    ``close_signal`` is a no-op, and new on the next trading day's pass (a report on the next session at an
    unspecified time qualifies on both passes, and a close that failed on the first must not block the second)."""
    key = f"{scope}/{signal_id}/{report_date.isoformat()}/{trading_day.isoformat()}"
    return f"release-{hashlib.sha256(key.encode()).hexdigest()[:32]}"


def preview_key(trading_day: date, signal_id: int, report_date: date) -> str:
    """The preview notice's outbox key: once per pass (trading day), position and report."""
    return f"{RELEASE_EXIT_STREAM}/{trading_day.isoformat()}/{signal_id}/{report_date.isoformat()}"


def failure_key(trading_day: date, signal_id: int, report_date: date) -> str:
    """The enforce failure notice's outbox key: once per pass (trading day), position and report."""
    return f"{preview_key(trading_day, signal_id, report_date)}/failed"


def _manual_close(signal_id: int) -> str:
    # The single-position Telegram close (``/close <signal_id>``); ``/flatten`` would close every broker position,
    # including the exempt catalog, probe and alpha ones.
    return f"/close {signal_id} closes it manually."


def preview_notice(
    symbol: str, signal_id: int, report_date: date, timing: EarningsTiming, direction: str, at: datetime
) -> str:
    """The spec's preview notice (section 3.5); ``at`` is the pass time in New York."""
    return (
        f"Release exit (preview): {symbol} reports {report_date.isoformat()} {timing_word(timing)}. "
        f"In enforce the desk would close this {direction.lower()} at about {at.astimezone(ET_TZ):%H:%M} ET today. "
        f"Nothing was sent to the broker; {_manual_close(signal_id)}"
    )


def failure_notice(symbol: str, signal_id: int, reason: str, *, broker_step: bool) -> str:
    """The enforce failure notice (spec section 3.6).

    Only a refusal before this request reached the broker can say the stop is kept: once the close service ran
    its broker step, the reason carries the broker's own protection statement (contract 2: a cancellation attempt
    must warn that protection may have been removed), so the notice points at the protective orders instead.
    """
    tail = (
        f"Check its protective orders at the broker; {_manual_close(signal_id)}"
        if broker_step
        else f"The position keeps its stop; {_manual_close(signal_id)}"
    )
    return f"Release exit could not close {symbol}: {reason.rstrip().removesuffix('.')}. {tail}"


def unavailable_notice(day: date, reason: str) -> str:
    return (
        f"Release exit unavailable for {day.isoformat()}: the pass did not complete before the close ({reason}). "
        "Open native equity positions were not checked for earnings releases; /positions lists them."
    )


class CloseService(Protocol):
    async def close_signal(
        self, signal_id: int, *, allow_queued: bool = False, request_id: str | None = None, context: dict | None = None
    ) -> str: ...


class ReleaseExitStore:
    """The ``release-exit`` journal: one pass per New York date and one close result per position and date."""

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    @staticmethod
    def pass_key(day: date) -> str:
        return f"{RELEASE_EXIT_STREAM}/{day.isoformat()}"

    @staticmethod
    def close_key(day: date, signal_id: int) -> str:
        return f"{RELEASE_EXIT_STREAM}/{day.isoformat()}/{signal_id}"

    async def recorded_pass(self, day: date) -> dict[str, Any] | None:
        """The date's journaled pass payload, or None."""
        async with self.workflows.db.session_factory() as session:
            raw = await session.scalar(
                select(DomainEventRecord.payload).where(
                    DomainEventRecord.scope == self.workflows.scope,
                    DomainEventRecord.event_key == self.pass_key(day),
                )
            )
        return json.loads(raw) if raw is not None else None

    async def closed(self, day: date, signal_ids: Iterable[int]) -> set[int]:
        """The positions among ``signal_ids`` whose close result is already journaled for the date."""
        keys = {self.close_key(day, signal_id): signal_id for signal_id in signal_ids}
        if not keys:
            return set()
        async with self.workflows.db.session_factory() as session:
            found = await session.scalars(
                select(DomainEventRecord.event_key).where(
                    DomainEventRecord.scope == self.workflows.scope,
                    DomainEventRecord.event_key.in_(list(keys)),
                )
            )
            return {keys[key] for key in found}

    async def record_pass(self, day: date, payload: Mapping[str, Any], notices: Sequence[tuple[str, str]]) -> bool:
        """Journal the date's pass and its notices in one transaction; False when the date is already journaled."""
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session)
            existing = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope,
                    DomainEventRecord.event_key == self.pass_key(day),
                )
            )
            if existing is not None:
                return False
            await self.workflows.append(
                session,
                stream=RELEASE_EXIT_STREAM,
                kind=EventKind.RELEASE_EXIT_PASS,
                payload=dict(payload),
                key=self.pass_key(day),
            )
            for key, text in notices:
                await self.workflows.add_notification(session, key, NotificationKind.MESSAGE, {"text": text})
        return True

    async def record_close(
        self, day: date, signal_id: int, payload: Mapping[str, Any], notice: tuple[str, str] | None
    ) -> None:
        """Journal one close result and its notice, if any, in one transaction (both idempotent by key)."""
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session)
            await self.workflows.append(
                session,
                stream=RELEASE_EXIT_STREAM,
                kind=EventKind.RELEASE_EXIT_CLOSE,
                payload=dict(payload),
                key=self.close_key(day, signal_id),
            )
            if notice is not None:
                await self.workflows.add_notification(session, notice[0], NotificationKind.MESSAGE, {"text": notice[1]})

    async def queue_unavailable(self, day: date, text: str) -> None:
        """Queue the date's one ``release-exit-unavailable/{date}`` notice (deduplicated by key)."""
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session)
            await self.workflows.add_notification(
                session, f"release-exit-unavailable/{day.isoformat()}", NotificationKind.MESSAGE, {"text": text}
            )


class ReleaseExitService:
    """One release-exit tick per ``run``; the daemon calls it every five minutes and it never raises.

    ``scope_of`` names a tracked position's scope: ``native``, or the exempt kind (``pead``, ``insider_watch``,
    ``probe``, ``alpha``) that the desk derives from its catalog sources and the card's provenance.
    ``close_guard`` returns the context held around each individual close (the ``close_signal`` call and its
    journaled outcome): the desk's reconciliation lock, which every other daemon close path holds. It is never
    held across the pass, so the earnings lookups never stall the position monitor.
    """

    def __init__(
        self,
        config: ReleaseExitConfig,
        db: SignalDatabase,
        *,
        close_service: CloseService,
        earnings: EarningsCalendarProtocol,
        captures: EarningsCaptureSource,
        calendar: MarketCalendarProtocol | None,
        scope_of: Callable[[Mapping[str, Any]], str],
        close_guard: Callable[[], AbstractAsyncContextManager[Any]] = nullcontext,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config, self.db = config, db
        self.close_service, self.earnings, self.captures = close_service, earnings, captures
        self.calendar, self.scope_of, self.close_guard = calendar, scope_of, close_guard
        self.store = ReleaseExitStore(db.workflows)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        # Process memory only; the journal is the authority across restarts.
        self._settled: date | None = None  # a date whose pass (and its closes) this process has seen complete
        self._failure: tuple[date, str] | None = None  # the date's last failed attempt, by exception type name
        self._unavailable_queued: date | None = None
        self._warned: set[tuple[date, str]] = set()

    async def run(self, now: datetime | None = None) -> RunStatus:
        """One tick: ``off``, ``closed_market``, ``waiting``, ``present`` (already journaled), ``done`` or ``failed``."""
        current = (now or self._clock()).astimezone(ET_TZ)
        async with self._lock:
            try:
                return await self._run(current)
            except Exception as exc:
                self._failed(current.date(), type(exc).__name__)
                return "failed"

    def _failed(self, day: date, reason: str) -> None:
        self._failure = (day, reason)
        self._warned = {key for key in self._warned if key[0] == day}
        if (day, reason) not in self._warned:
            self._warned.add((day, reason))
            logger.warning(
                "Release exit pass failed (%s); retrying on the next tick until the close",
                reason,
                extra={"event": "release_exit_failed", "reason": reason, "date": day.isoformat()},
            )

    async def _run(self, now: datetime) -> RunStatus:
        if self.config.mode == "off":
            return "off"
        today = now.date()
        if self._settled == today:
            return "present"
        if self.calendar is None:
            raise TradingCalendarUnavailable
        day = await self.calendar.get_trading_day(today)
        if not day.is_trading_day or day.close_time is None:
            return "closed_market"
        close_at = datetime.combine(today, day.close_time, tzinfo=ET_TZ)
        if now < close_at - timedelta(minutes=self.config.minutes_before_close):
            return "waiting"
        recorded = await self.store.recorded_pass(today)
        if recorded is not None:
            await self._finish_closes(today, recorded, now, close_at)
            self._settled = today
            return "present"
        if now >= close_at:
            return await self._missed(today, now, close_at)
        payload, notices = await self._pass(now, close_at)
        journaled = await self.store.record_pass(today, payload, notices)
        if not journaled:  # another process journaled the date between the read and the write
            payload = await self.store.recorded_pass(today) or {}
        await self._finish_closes(today, payload, now, close_at)
        self._settled = today
        return "done" if journaled else "present"

    async def _missed(self, today: date, now: datetime, close_at: datetime) -> RunStatus:
        """At or after the close with no pass: one unavailable notice if this process failed it, else skip the date.

        The notice is queued only up to ``close_at + NOTICE_BOUND_AFTER_CLOSE``; later it is logged only.
        """
        if self._failure is None or self._failure[0] != today:
            return "closed_market"  # a restart (or a wake) after the close: the date is skipped, as journaled
        if self._unavailable_queued != today:
            reason = self._failure[1]
            if now <= close_at + NOTICE_BOUND_AFTER_CLOSE:
                await self.store.queue_unavailable(today, unavailable_notice(today, reason))
            else:
                logger.warning(
                    "Release exit unavailable for %s (%s); past the notice bound, logged only",
                    today.isoformat(),
                    reason,
                    extra={"event": "release_exit_unavailable", "date": today.isoformat(), "reason": reason},
                )
            self._unavailable_queued = today
        return "failed"

    async def _next_trading_day(self, today: date) -> date:
        assert self.calendar is not None
        days = await self.calendar.get_calendar_range(
            today + timedelta(days=1), today + timedelta(days=NEXT_SESSION_SEARCH_DAYS)
        )
        for day in days:
            if day.is_trading_day:
                return day.date
        raise NoNextTradingDay

    async def _live(self, symbol: str, now: datetime, horizon_days: int) -> EarningsLookup | None:
        """The live lookup, or None when it raised (read as unverified)."""
        try:
            return await self.earnings.next_earnings(symbol, now, horizon_days)
        except Exception as exc:
            logger.warning(
                "Release exit earnings lookup failed for %s (%s)",
                symbol,
                type(exc).__name__,
                extra={"event": "release_exit_lookup_failed", "symbol": symbol, "reason": type(exc).__name__},
            )
            return None

    async def _pass(self, now: datetime, close_at: datetime) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        """The pass payload (spec section 3.5) and its preview notices; reads only, writes nothing."""
        today = now.date()
        next_day = await self._next_trading_day(today)
        captured = await self.captures.reports_between(today, next_day, today=today)
        entries: list[dict[str, Any]] = []
        notices: list[tuple[str, str]] = []
        for position in await self.db.get_active_positions():
            if normalize_asset_class(str(position.get("asset_class") or "")) != _EQUITY:
                continue
            symbol = str(position["contract"]).strip("/").upper()
            scope = self.scope_of(position)
            decision = decide(symbol, await self._live(symbol, now, (next_day - today).days), captured, today, next_day)
            direction = str(position["direction"])
            status: str = decision.status
            if scope != NATIVE_SCOPE:
                status = "exempt"  # counted with its decision, never closed
            elif decision.status == "exit":
                status = "would_close" if self.config.mode == "preview" else "close"
            entry: dict[str, Any] = {
                "signal_id": position["id"],
                "symbol": symbol,
                "direction": direction,
                "scope": scope,
                "status": status,
                "decision": decision.status,
                "report_date": decision.report_date.isoformat() if decision.report_date else None,
                "timing": decision.timing.value if decision.timing else None,
                "sources": list(decision.sources),
                "live_verified": decision.live_verified,
            }
            if status == "close":
                assert decision.report_date is not None
                entry["request_id"] = release_request_id(scope, position["id"], decision.report_date, today)
            elif status == "would_close":
                assert decision.report_date is not None and decision.timing is not None
                notices.append(
                    (
                        preview_key(today, position["id"], decision.report_date),
                        preview_notice(symbol, position["id"], decision.report_date, decision.timing, direction, now),
                    )
                )
            entries.append(entry)
        payload = {
            "mode": self.config.mode,
            "trading_day": today.isoformat(),
            "next_trading_day": next_day.isoformat(),
            "close_at": close_at.isoformat(),
            "capture_date": captured.capture_date.isoformat() if captured is not None else None,
            "positions": entries,
        }
        return payload, notices

    async def _finish_closes(self, today: date, payload: Mapping[str, Any], now: datetime, close_at: datetime) -> None:
        """Close every journaled ``close`` entry that has no close result yet (all of them on the pass's own tick).

        Only while the configured mode is ``enforce``: a process running ``preview`` (or restarted into it after an
        interrupted ``enforce`` pass) never calls the broker. Each close holds ``close_guard`` for its own call
        and journaled outcome only.
        """
        if self.config.mode != "enforce":
            return
        owed = [entry for entry in payload.get("positions") or [] if entry.get("status") == "close"]
        if not owed:
            return
        done = await self.store.closed(today, [entry["signal_id"] for entry in owed])
        notify = now <= close_at + NOTICE_BOUND_AFTER_CLOSE
        for entry in owed:
            if entry["signal_id"] not in done:
                async with self.close_guard():
                    await self._close(today, entry, notify=notify)

    async def _close(self, today: date, entry: Mapping[str, Any], *, notify: bool) -> None:
        """One ``close_signal`` call (never retried here) and its journaled outcome.

        A non-submitted outcome queues a notice only when ``notify`` (within the notice bound); otherwise the
        outcome is journaled (``notified: false``) and logged.
        """
        signal_id, symbol, request_id = entry["signal_id"], entry["symbol"], entry["request_id"]
        context = {
            "reason": "release_exit",
            "report_date": entry["report_date"],
            "timing": entry["timing"],
            "sources": list(entry["sources"]),
        }
        raised: str | None = None
        result = ""
        try:
            result = await self.close_service.close_signal(signal_id, request_id=request_id, context=context)
        except Exception as exc:
            raised = type(exc).__name__
        record = await self.db.get_close_request(request_id)
        status = record["status"] if record is not None else None
        if status in _SUBMITTED:
            outcome = "submitted"
        elif record is None and await self._already_exiting(signal_id):
            outcome = "already_exiting"
        elif raised is not None:
            outcome = "error"
        elif record is None:
            outcome = "refused"
        else:
            outcome = "failed"
        close_result = raised if raised is not None else result
        notice = None
        if outcome in ("refused", "failed", "error"):
            reason = f"the close raised {raised}" if raised is not None else result
            if notify:
                notice = (
                    failure_key(today, signal_id, date.fromisoformat(entry["report_date"])),
                    failure_notice(symbol, signal_id, reason, broker_step=record is not None),
                )
            else:
                logger.warning(
                    "Release exit could not close %s (%s); past the notice bound, journaled only",
                    symbol,
                    outcome,
                    extra={"event": "release_exit_close_not_submitted", "symbol": symbol, "outcome": outcome},
                )
        await self.store.record_close(
            today,
            signal_id,
            {
                "trading_day": today.isoformat(),
                "signal_id": signal_id,
                "symbol": symbol,
                "request_id": request_id,
                "report_date": entry["report_date"],
                "close_result": close_result,
                "close_status": status,
                "outcome": outcome,
                "notified": notice is not None,
            },
            notice,
        )

    async def _already_exiting(self, signal_id: int) -> bool:
        """Whether the position is already closed, or an exit order or another close request owns it."""
        signal = await self.db.get_signal_by_id(signal_id)
        if signal is None or signal["status"] != SignalStatus.EXECUTED or signal.get("broker_exit_order_id"):
            return True
        return any(row["signal_id"] == signal_id for row in await self.db.active_close_requests())
