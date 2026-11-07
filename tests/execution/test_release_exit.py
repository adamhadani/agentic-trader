"""The release exit (spec 2026-11-06 section 3): the Friday boundary, the two-source decision and the daily pass."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from agentic_trader.agent.earnings import EarningsEvent, EarningsLookup, EarningsTiming
from agentic_trader.broker.base import BrokerPosition, OrderResult
from agentic_trader.config import ReleaseExitConfig
from agentic_trader.constants import AssetClass, CloseRequestStatus, SignalStatus, SystemStateKey
from agentic_trader.data.earnings_capture import CapturedReports
from agentic_trader.execution.closing import PositionCloseService
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.execution.release_exit import (
    RELEASE_EXIT_STREAM,
    ReleaseExitService,
    decide,
    exits_today,
    release_request_id,
)
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.screeners.earnings_drift import PEAD_STRATEGY_ID
from agentic_trader.storage.models import WorkItemRecord


PM, AH, UNSPEC = EarningsTiming.PRE_MARKET, EarningsTiming.AFTER_HOURS, EarningsTiming.UNSPECIFIED
THURSDAY, FRIDAY, SATURDAY, SUNDAY = date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8)
MONDAY, TUESDAY = date(2026, 11, 9), date(2026, 11, 10)


def no_stop(signal_id: int) -> str:
    return f"The position keeps its stop; /close {signal_id} closes it manually."


def check_stop(signal_id: int) -> str:
    return f"Check its protective orders at the broker; /close {signal_id} closes it manually."


# --- exits_today -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("report_date", "timing", "exits"),
    [
        (FRIDAY, AH, True),
        (FRIDAY, UNSPEC, True),
        (FRIDAY, PM, False),
        (SATURDAY, PM, True),
        (SATURDAY, AH, True),
        (SUNDAY, UNSPEC, True),
        (MONDAY, PM, True),
        (MONDAY, UNSPEC, True),
        (MONDAY, AH, False),
        (TUESDAY, PM, False),
        (THURSDAY, AH, False),
    ],
)
def test_exits_today_on_friday_with_monday_next(report_date, timing, exits):
    assert exits_today(report_date, timing, FRIDAY, MONDAY) is exits


def test_a_holiday_between_today_and_the_next_session_exits_today():
    wednesday, thanksgiving, black_friday = date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)
    assert exits_today(thanksgiving, AH, wednesday, black_friday) is True
    assert exits_today(black_friday, PM, wednesday, black_friday) is True
    assert exits_today(black_friday, AH, wednesday, black_friday) is False


# --- decide ----------------------------------------------------------------------------------------------------


def _live(event: EarningsEvent | None, *, verified: bool) -> EarningsLookup:
    return EarningsLookup(event=event, verified=verified, horizon_end=MONDAY)


def _captured(rows: dict[date, tuple[tuple[str, EarningsTiming], ...]]) -> CapturedReports:
    return CapturedReports(capture_date=THURSDAY, rows=rows)


def test_verified_live_without_a_report_and_no_capture_is_none():
    decision = decide("AAPL", _live(None, verified=True), None, FRIDAY, MONDAY)
    assert decision.status == "none"
    assert decision.sources == ("live",) and decision.live_verified is True
    assert decision.report_date is None and decision.timing is None


def test_unverified_live_with_a_fresh_capture_without_the_symbol_is_none_from_the_capture():
    captured = _captured({FRIDAY: (("MSFT", AH),), MONDAY: ()})
    decision = decide("AAPL", _live(None, verified=False), captured, FRIDAY, MONDAY)
    assert decision.status == "none"
    assert decision.sources == ("capture",) and decision.live_verified is False


def test_unverified_live_and_no_capture_is_unknown_never_none():
    decision = decide("AAPL", _live(None, verified=False), None, FRIDAY, MONDAY)
    assert decision.status == "unknown" and decision.sources == () and decision.live_verified is False
    assert decide("AAPL", None, None, FRIDAY, MONDAY).status == "unknown"


def test_disagreeing_timings_take_the_conservative_reading():
    live = _live(EarningsEvent("AAPL", MONDAY, AH), verified=True)
    captured = _captured({FRIDAY: (), MONDAY: (("AAPL", PM),)})
    decision = decide("AAPL", live, captured, FRIDAY, MONDAY)
    assert decision.status == "exit"
    assert (decision.report_date, decision.timing) == (MONDAY, PM)
    assert decision.sources == ("live", "capture") and decision.live_verified is True


def test_the_capture_alone_decides_an_exit_when_the_live_lookup_is_down():
    captured = _captured({FRIDAY: (("AAPL", AH),), MONDAY: ()})
    decision = decide("aapl", None, captured, FRIDAY, MONDAY)
    assert decision.status == "exit" and decision.sources == ("capture",)
    assert (decision.report_date, decision.timing) == (FRIDAY, AH)


def test_a_monday_after_hours_report_is_mondays_pass():
    live = _live(EarningsEvent("AAPL", MONDAY, AH), verified=True)
    assert decide("AAPL", live, None, FRIDAY, MONDAY).status == "none"


# --- the service -----------------------------------------------------------------------------------------------


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET_TZ)


class FakeCalendar:
    """Weekdays trade 09:30-16:00 unless a day is given; weekends never trade."""

    def __init__(self, *days: MarketCalendarDay, error: Exception | None = None):
        self.days = {day.date: day for day in days}
        self.error = error

    async def get_trading_day(self, target: date) -> MarketCalendarDay:
        if self.error is not None:
            raise self.error
        if target in self.days:
            return self.days[target]
        if target.weekday() >= 5:
            return MarketCalendarDay(target, False, False, None, None)
        return MarketCalendarDay(target, True, False, time(9, 30), time(16, 0))

    async def get_calendar_range(self, start: date, end: date) -> list[MarketCalendarDay]:
        days, day = [], start
        while day <= end:
            days.append(await self.get_trading_day(day))
            day += timedelta(days=1)
        return days


class FakeEarnings:
    def __init__(self, events: dict[str, EarningsEvent] | None = None, *, unverified=(), errors=()):
        self.events, self.unverified, self.errors = events or {}, set(unverified), set(errors)
        self.calls: list[tuple[str, int]] = []

    async def next_earnings(self, symbol: str, now: datetime, horizon_days: int) -> EarningsLookup:
        self.calls.append((symbol, horizon_days))
        if symbol in self.errors:
            raise RuntimeError("secret lookup detail")
        today = now.astimezone(ET_TZ).date()
        return EarningsLookup(self.events.get(symbol), symbol not in self.unverified, today + timedelta(horizon_days))


class FakeCaptures:
    def __init__(self, result: CapturedReports | None = None, errors: list[Exception] | None = None):
        self.result, self.errors = result, list(errors or [])
        self.calls: list[tuple[date, date, date]] = []

    async def reports_between(self, start: date, end: date, *, today: date) -> CapturedReports | None:
        self.calls.append((start, end, today))
        if self.errors:
            raise self.errors.pop(0)
        return self.result


class FakeBroker:
    """The broker surface ``PositionCloseService`` reads: positions, exact recovery and one close submission."""

    authoritative_positions = True

    def __init__(self, positions: list[BrokerPosition], result: OrderResult | None = None):
        self.positions = positions
        self.result = result or OrderResult(success=True, order_id="broker-close-1")
        self.submits: list = []

    async def get_positions(self) -> list[BrokerPosition]:
        return list(self.positions)

    async def find_position_close(self, request_id: str):
        return None

    async def submit_position_close(self, request, observe):
        self.submits.append(request)
        return self.result


def held(symbol: str, direction: str = "LONG", quantity: float = 5.0) -> BrokerPosition:
    return BrokerPosition(symbol=symbol, asset_class=AssetClass.EQUITY, direction=direction, quantity=quantity)


async def position(db, symbol, *, direction="LONG", strategy="TREND_PULLBACK", asset_class=AssetClass.EQUITY, **extra):
    return await db.record_signal(
        contract=symbol,
        strategy=strategy,
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0 if direction == "LONG" else 105.0,
        take_profit=110.0 if direction == "LONG" else 90.0,
        risk_dollars=25.0,
        status=SignalStatus.EXECUTED,
        asset_class=asset_class,
        quantity=5.0,
        **extra,
    )


def scope_of(position: dict) -> str:
    if position["strategy"] == PEAD_STRATEGY_ID:
        return "pead"
    if (position.get("decision_provenance") or {}).get("paper_probe"):
        return "probe"
    return "alpha" if position.get("alpha_version") or position.get("alpha_policy") else "native"


def service(
    db, *, mode="preview", earnings=None, captures=None, calendar=None, broker=None, **extra
) -> ReleaseExitService:
    return ReleaseExitService(
        ReleaseExitConfig(mode=mode),
        db,
        close_service=PositionCloseService(broker or FakeBroker([]), db),
        earnings=earnings or FakeEarnings(),
        captures=captures or FakeCaptures(),
        calendar=calendar or FakeCalendar(),
        scope_of=scope_of,
        **extra,
    )


async def release_events(db) -> list[dict]:
    return await db.workflows.events(RELEASE_EXIT_STREAM)


async def notices(db) -> dict[str, str]:
    async with db.session_factory() as session:
        rows = (await session.scalars(select(WorkItemRecord).where(WorkItemRecord.kind == WorkKind.NOTIFICATION))).all()
    return {row.dedup_key: json.loads(row.payload)["arguments"]["text"] for row in rows}


def _entries(event: dict) -> dict[str, dict]:
    return {entry["symbol"]: entry for entry in event["payload"]["positions"]}


async def test_preview_journals_one_pass_with_native_and_exempt_entries_and_one_notice(temp_db):
    aapl = await position(temp_db, "AAPL")
    msft = await position(temp_db, "MSFT", direction="SHORT")
    nvda = await position(temp_db, "NVDA", strategy=PEAD_STRATEGY_ID)
    amd = await position(temp_db, "AMD")
    probe = await position(temp_db, "QQQ", decision_provenance={"paper_probe": True})
    await position(temp_db, "ES", asset_class=AssetClass.FUTURES)
    earnings = FakeEarnings(
        {
            "AAPL": EarningsEvent("AAPL", MONDAY, PM),
            "NVDA": EarningsEvent("NVDA", FRIDAY, AH),
            "ES": EarningsEvent("ES", FRIDAY, AH),
        },
        unverified={"AMD"},
    )
    captures = FakeCaptures(None)
    broker = FakeBroker([held("AAPL")])
    release = service(temp_db, earnings=earnings, captures=captures, broker=broker)

    assert await release.run(at(FRIDAY, 15, 30)) == "done"

    [event] = await release_events(temp_db)
    assert event["kind"] == EventKind.RELEASE_EXIT_PASS
    payload = event["payload"]
    assert {k: payload[k] for k in ("mode", "trading_day", "next_trading_day", "close_at", "capture_date")} == {
        "mode": "preview",
        "trading_day": "2026-11-06",
        "next_trading_day": "2026-11-09",
        "close_at": "2026-11-06T16:00:00-05:00",
        "capture_date": None,
    }
    entries = _entries(event)
    assert set(entries) == {"AAPL", "MSFT", "NVDA", "AMD", "QQQ"}  # the futures position is skipped silently
    assert entries["AAPL"] == {
        "signal_id": aapl,
        "symbol": "AAPL",
        "direction": "LONG",
        "scope": "native",
        "status": "would_close",
        "decision": "exit",
        "report_date": "2026-11-09",
        "timing": "pre_market",
        "sources": ["live"],
        "live_verified": True,
    }
    assert entries["MSFT"]["status"] == "none" and entries["MSFT"]["signal_id"] == msft
    assert entries["MSFT"]["direction"] == "SHORT" and entries["MSFT"]["sources"] == ["live"]
    assert entries["NVDA"]["signal_id"] == nvda
    assert (entries["NVDA"]["scope"], entries["NVDA"]["status"], entries["NVDA"]["decision"]) == (
        "pead",
        "exempt",
        "exit",
    )
    assert (entries["NVDA"]["report_date"], entries["NVDA"]["timing"]) == ("2026-11-06", "after_hours")
    assert (entries["AMD"]["signal_id"], entries["AMD"]["status"], entries["AMD"]["live_verified"]) == (
        amd,
        "unknown",
        False,
    )
    assert (entries["QQQ"]["signal_id"], entries["QQQ"]["scope"], entries["QQQ"]["status"]) == (
        probe,
        "probe",
        "exempt",
    )

    assert await notices(temp_db) == {
        f"release-exit/2026-11-06/{aapl}/2026-11-09": (
            "Release exit (preview): AAPL reports 2026-11-09 before open. In enforce the desk would close this "
            f"long at about 15:30 ET today. Nothing was sent to the broker; /close {aapl} closes it manually."
        )
    }
    assert broker.submits == [] and await temp_db.active_close_requests() == []
    assert {horizon for _, horizon in earnings.calls} == {3} and "ES" not in {s for s, _ in earnings.calls}
    assert captures.calls == [(FRIDAY, MONDAY, FRIDAY)]


async def test_a_second_tick_and_a_restart_the_same_day_are_present(temp_db):
    await position(temp_db, "AAPL")
    earnings = FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)})
    release = service(temp_db, earnings=earnings)
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    calls = len(earnings.calls)
    assert await release.run(at(FRIDAY, 15, 35)) == "present"
    assert await service(temp_db, earnings=earnings).run(at(FRIDAY, 15, 40)) == "present"
    assert len(earnings.calls) == calls
    assert len(await release_events(temp_db)) == 1 and len(await notices(temp_db)) == 1


async def test_waiting_until_close_minus_the_configured_minutes(temp_db):
    await position(temp_db, "AAPL")
    release = service(temp_db)
    assert await release.run(at(FRIDAY, 9, 0)) == "waiting"
    assert await release.run(at(FRIDAY, 15, 29)) == "waiting"
    assert await release_events(temp_db) == []
    assert await release.run(at(FRIDAY, 15, 30)) == "done"


async def test_an_early_close_runs_at_12_30(temp_db):
    black_friday = date(2026, 11, 27)
    calendar = FakeCalendar(MarketCalendarDay(black_friday, True, True, time(9, 30), time(13, 0)))
    release = service(temp_db, calendar=calendar)
    assert await release.run(at(black_friday, 12, 29)) == "waiting"
    assert await release.run(at(black_friday, 12, 30)) == "done"
    [event] = await release_events(temp_db)
    assert event["payload"]["close_at"] == "2026-11-27T13:00:00-05:00"
    assert event["payload"]["next_trading_day"] == "2026-11-30"


async def test_off_and_closed_market(temp_db):
    calendar = FakeCalendar(error=AssertionError("off never reads the calendar"))
    assert await service(temp_db, mode="off", calendar=calendar).run(at(FRIDAY, 15, 30)) == "off"
    assert await service(temp_db).run(at(SATURDAY, 15, 30)) == "closed_market"
    assert await release_events(temp_db) == []


async def test_a_failed_live_lookup_is_unverified_and_logs_the_type_name_only(temp_db, caplog):
    await position(temp_db, "AMD")
    release = service(temp_db, earnings=FakeEarnings(errors={"AMD"}))
    with caplog.at_level(logging.WARNING):
        assert await release.run(at(FRIDAY, 15, 30)) == "done"
    [event] = await release_events(temp_db)
    assert _entries(event)["AMD"]["status"] == "unknown" and _entries(event)["AMD"]["live_verified"] is False
    assert "secret" not in caplog.text
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "release_exit_lookup_failed"]
    assert record.__dict__["reason"] == "RuntimeError"


async def test_the_capture_date_is_journaled(temp_db):
    await position(temp_db, "AAPL")
    captured = CapturedReports(THURSDAY, {FRIDAY: (("AAPL", UNSPEC),), MONDAY: ()})
    release = service(temp_db, earnings=FakeEarnings(unverified={"AAPL"}), captures=FakeCaptures(captured))
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    [event] = await release_events(temp_db)
    assert event["payload"]["capture_date"] == "2026-11-05"
    entry = _entries(event)["AAPL"]
    assert (entry["status"], entry["sources"], entry["timing"]) == ("would_close", ["capture"], "unspecified")
    [text] = (await notices(temp_db)).values()
    assert "AAPL reports 2026-11-06 timing unspecified." in text


async def test_a_short_would_close_notice_says_short(temp_db):
    tsla = await position(temp_db, "TSLA", direction="SHORT")
    release = service(temp_db, earnings=FakeEarnings({"TSLA": EarningsEvent("TSLA", MONDAY, UNSPEC)}))
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    assert await notices(temp_db) == {
        f"release-exit/2026-11-06/{tsla}/2026-11-09": (
            "Release exit (preview): TSLA reports 2026-11-09 timing unspecified. In enforce the desk would close "
            f"this short at about 15:30 ET today. Nothing was sent to the broker; /close {tsla} closes it manually."
        )
    }


# --- enforce ---------------------------------------------------------------------------------------------------


def _request_id(signal_id: int, report_date: str, trading_day: str = "2026-11-06") -> str:
    key = f"native/{signal_id}/{report_date}/{trading_day}"
    return "release-" + hashlib.sha256(key.encode()).hexdigest()[:32]


async def test_enforce_closes_once_with_the_stable_request_id_and_context(temp_db):
    aapl = await position(temp_db, "AAPL")
    broker = FakeBroker([held("AAPL")])
    earnings = FakeEarnings({"AAPL": EarningsEvent("AAPL", MONDAY, PM)})
    release = service(temp_db, mode="enforce", earnings=earnings, broker=broker)

    assert await release.run(at(FRIDAY, 15, 30)) == "done"

    request_id = _request_id(aapl, "2026-11-09")
    assert release_request_id("native", aapl, MONDAY, FRIDAY) == request_id
    assert [request.client_order_id for request in broker.submits] == [request_id]
    [requested] = [
        e for e in await temp_db.workflows.events(f"close/{request_id}") if e["kind"] == EventKind.CLOSE_REQUESTED
    ]
    assert requested["payload"]["context"] == {
        "reason": "release_exit",
        "report_date": "2026-11-09",
        "timing": "pre_market",
        "sources": ["live"],
    }
    passed, closed = await release_events(temp_db)
    assert passed["kind"] == EventKind.RELEASE_EXIT_PASS and closed["kind"] == EventKind.RELEASE_EXIT_CLOSE
    assert passed["id"] < closed["id"]  # the pass is journaled before any close call
    entry = _entries(passed)["AAPL"]
    assert (entry["status"], entry["request_id"]) == ("close", request_id)
    assert closed["payload"] == {
        "trading_day": "2026-11-06",
        "signal_id": aapl,
        "symbol": "AAPL",
        "request_id": request_id,
        "report_date": "2026-11-09",
        "close_result": "Close submitted; awaiting a confirmed broker fill.",
        "close_status": CloseRequestStatus.SUBMITTED,
        "outcome": "submitted",
        "notified": False,
    }
    assert not [key for key in await notices(temp_db) if key.startswith("release-exit")]

    # A restart the same day: the pass is present and nothing is called again.
    restarted = service(temp_db, mode="enforce", earnings=earnings, broker=broker)
    assert await restarted.run(at(FRIDAY, 15, 35)) == "present"
    assert len(broker.submits) == 1 and len(await release_events(temp_db)) == 2


async def test_a_crash_between_the_pass_and_the_close_event_recalls_only_the_idempotent_close(temp_db, monkeypatch):
    aapl = await position(temp_db, "AAPL")
    broker = FakeBroker([held("AAPL")])
    earnings = FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)})
    first = service(temp_db, mode="enforce", earnings=earnings, broker=broker)

    async def crash(*args, **kwargs):
        raise RuntimeError("crash after the close call")

    monkeypatch.setattr(first.store, "record_close", crash)
    assert await first.run(at(FRIDAY, 15, 30)) == "failed"
    assert len(broker.submits) == 1 and [e["kind"] for e in await release_events(temp_db)] == [
        EventKind.RELEASE_EXIT_PASS
    ]

    restarted = service(temp_db, mode="enforce", earnings=earnings, broker=broker)
    calls: list[tuple[int, str]] = []
    close_signal = restarted.close_service.close_signal

    async def spy(signal_id, **kwargs):
        calls.append((signal_id, kwargs["request_id"]))
        return await close_signal(signal_id, **kwargs)

    monkeypatch.setattr(restarted.close_service, "close_signal", spy)
    assert await restarted.run(at(FRIDAY, 15, 35)) == "present"
    request_id = _request_id(aapl, "2026-11-06")
    assert calls == [(aapl, request_id)]
    assert len(broker.submits) == 1  # the stable request ID made the repeated call a no-op
    _, closed = await release_events(temp_db)
    assert closed["payload"]["outcome"] == "submitted"
    assert closed["payload"]["close_result"] == "Close already submitted; awaiting confirmed broker reconciliation."
    assert await restarted.run(at(FRIDAY, 15, 40)) == "present" and len(calls) == 1


async def test_a_broker_refusal_queues_a_notice_and_never_halts(temp_db):
    aapl = await position(temp_db, "AAPL")
    refusal = (
        "Regular market is closed. No close submitted or queued. Next regular open: 2026-11-09 14:30 UTC. "
        "Retry during regular market hours. Existing protective orders were left unchanged by this request."
    )
    broker = FakeBroker([held("AAPL")], OrderResult(success=False, error_message=refusal))
    release = service(
        temp_db, mode="enforce", earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)}), broker=broker
    )
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    _, closed = await release_events(temp_db)
    assert (closed["payload"]["outcome"], closed["payload"]["close_status"]) == ("failed", CloseRequestStatus.FAILED)
    assert (await notices(temp_db))[f"release-exit/2026-11-06/{aapl}/2026-11-06/failed"] == (
        f"Release exit could not close AAPL: {refusal.removesuffix('.')}. {check_stop(aapl)}"
    )
    assert await temp_db.get_state(SystemStateKey.TRADING_HALTED) is None


def test_the_request_id_is_stable_within_a_day_and_new_on_the_next():
    assert release_request_id("native", 7, MONDAY, FRIDAY) == release_request_id("native", 7, MONDAY, FRIDAY)
    assert release_request_id("native", 7, MONDAY, FRIDAY) != release_request_id("native", 7, MONDAY, MONDAY)


async def test_a_close_failed_on_one_pass_is_attempted_again_on_the_next_days_pass(temp_db):
    """A Monday report at an unspecified time qualifies on Friday's pass and on Monday's own pass."""
    aapl = await position(temp_db, "AAPL")
    refusal = "Broker rejected the close. Existing protective orders were left unchanged by this request."
    broker = FakeBroker([held("AAPL")], OrderResult(success=False, error_message=refusal))
    release = service(
        temp_db, mode="enforce", earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", MONDAY, UNSPEC)}), broker=broker
    )
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    assert await release.run(at(FRIDAY, 15, 35)) == "present"  # stable within the day: nothing called again

    broker.result = OrderResult(success=True, order_id="broker-close-2")
    assert await release.run(at(MONDAY, 15, 30)) == "done"

    friday_id, monday_id = _request_id(aapl, "2026-11-09"), _request_id(aapl, "2026-11-09", "2026-11-09")
    assert friday_id != monday_id
    assert [request.client_order_id for request in broker.submits] == [friday_id, monday_id]
    friday_pass, friday_close, monday_pass, monday_close = await release_events(temp_db)
    assert _entries(friday_pass)["AAPL"]["request_id"] == friday_id
    assert _entries(monday_pass)["AAPL"]["request_id"] == monday_id
    assert (friday_close["payload"]["outcome"], monday_close["payload"]["outcome"]) == ("failed", "submitted")
    assert [key for key in await notices(temp_db) if key.startswith("release-exit")] == [
        f"release-exit/2026-11-06/{aapl}/2026-11-09/failed"
    ]


async def test_a_report_qualifying_on_two_passes_gets_a_preview_notice_on_each(temp_db):
    aapl = await position(temp_db, "AAPL")
    release = service(temp_db, earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", MONDAY, UNSPEC)}))
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    assert await release.run(at(MONDAY, 15, 30)) == "done"
    assert sorted(await notices(temp_db)) == [
        f"release-exit/2026-11-06/{aapl}/2026-11-09",
        f"release-exit/2026-11-09/{aapl}/2026-11-09",
    ]


async def test_a_refusal_before_any_broker_step_says_the_stop_is_kept(temp_db):
    aapl = await position(temp_db, "AAPL")
    release = service(
        temp_db,
        mode="enforce",
        earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)}),
        broker=FakeBroker([]),  # no broker position
    )
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    _, closed = await release_events(temp_db)
    assert (closed["payload"]["outcome"], closed["payload"]["close_status"]) == ("refused", None)
    assert (await notices(temp_db))[f"release-exit/2026-11-06/{aapl}/2026-11-06/failed"] == (
        "Release exit could not close AAPL: No open broker position; awaiting reconciliation of existing orders. "
        + no_stop(aapl)
    )
    assert await temp_db.get_state(SystemStateKey.TRADING_HALTED) is None


async def test_a_raising_close_is_recorded_by_type_name_and_never_retried(temp_db, monkeypatch):
    aapl = await position(temp_db, "AAPL")
    release = service(temp_db, mode="enforce", earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)}))
    calls = []

    async def boom(signal_id, **kwargs):
        calls.append(signal_id)
        raise ConnectionError("secret broker detail")

    monkeypatch.setattr(release.close_service, "close_signal", boom)
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    assert await release.run(at(FRIDAY, 15, 35)) == "present"
    assert calls == [aapl]
    _, closed = await release_events(temp_db)
    assert (closed["payload"]["outcome"], closed["payload"]["close_result"]) == ("error", "ConnectionError")
    assert (await notices(temp_db))[f"release-exit/2026-11-06/{aapl}/2026-11-06/failed"] == (
        f"Release exit could not close AAPL: the close raised ConnectionError. {no_stop(aapl)}"
    )


async def test_a_position_already_exiting_elsewhere_gets_no_failure_notice(temp_db):
    aapl = await position(temp_db, "AAPL")
    await temp_db.record_exit_request(aapl, "manual-close-order")
    release = service(
        temp_db,
        mode="enforce",
        earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)}),
        broker=FakeBroker([held("AAPL")]),
    )
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    _, closed = await release_events(temp_db)
    assert closed["payload"]["outcome"] == "already_exiting"
    assert not [key for key in await notices(temp_db) if key.startswith("release-exit")]


async def test_each_close_holds_the_guard_and_the_lookups_never_do(temp_db, monkeypatch):
    await position(temp_db, "AAPL")
    await position(temp_db, "MSFT", direction="SHORT")
    lock = asyncio.Lock()
    seen: list[tuple[str, bool]] = []

    class WatchedEarnings(FakeEarnings):
        async def next_earnings(self, symbol, now, horizon_days):
            seen.append(("lookup", lock.locked()))
            return await super().next_earnings(symbol, now, horizon_days)

    class WatchedCaptures(FakeCaptures):
        async def reports_between(self, start, end, *, today):
            seen.append(("capture", lock.locked()))
            return await super().reports_between(start, end, today=today)

    events = {"AAPL": EarningsEvent("AAPL", FRIDAY, AH), "MSFT": EarningsEvent("MSFT", MONDAY, PM)}
    release = service(
        temp_db,
        mode="enforce",
        earnings=WatchedEarnings(events),
        captures=WatchedCaptures(None),
        broker=FakeBroker([held("AAPL"), held("MSFT", "SHORT")]),
        close_guard=lambda: lock,
    )
    close_signal, record_close = release.close_service.close_signal, release.store.record_close

    async def close_spy(signal_id, **kwargs):
        seen.append(("close", lock.locked()))
        return await close_signal(signal_id, **kwargs)

    async def record_spy(*args, **kwargs):
        seen.append(("record", lock.locked()))
        return await record_close(*args, **kwargs)

    monkeypatch.setattr(release.close_service, "close_signal", close_spy)
    monkeypatch.setattr(release.store, "record_close", record_spy)
    assert await release.run(at(FRIDAY, 15, 30)) == "done"
    assert seen == [
        ("capture", False),
        ("lookup", False),
        ("lookup", False),
        ("close", True),
        ("record", True),
        ("close", True),
        ("record", True),
    ]
    assert not lock.locked()


async def _interrupted_enforce_pass(db, monkeypatch, broker) -> int:
    """An ``enforce`` pass journaled at 15:30 whose closes never ran (a crash right after the journal)."""
    signal_id = await position(db, "AAPL")
    first = service(
        db, mode="enforce", earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, AH)}), broker=broker
    )

    async def crash(*args, **kwargs):
        raise RuntimeError("crash after the pass journal")

    monkeypatch.setattr(first, "_finish_closes", crash)
    assert await first.run(at(FRIDAY, 15, 30)) == "failed"
    assert [e["kind"] for e in await release_events(db)] == [EventKind.RELEASE_EXIT_PASS] and broker.submits == []
    return signal_id


async def test_a_preview_restart_never_finishes_an_interrupted_enforce_pass(temp_db, monkeypatch):
    broker = FakeBroker([held("AAPL")])
    await _interrupted_enforce_pass(temp_db, monkeypatch, broker)
    preview = service(temp_db, mode="preview", earnings=FakeEarnings(), broker=broker)
    calls = []

    async def spy(signal_id, **kwargs):
        calls.append(signal_id)
        return "unexpected"

    monkeypatch.setattr(preview.close_service, "close_signal", spy)
    assert await preview.run(at(FRIDAY, 15, 35)) == "present"
    assert calls == [] and broker.submits == [] and await temp_db.active_close_requests() == []
    assert [e["kind"] for e in await release_events(temp_db)] == [EventKind.RELEASE_EXIT_PASS]


@pytest.mark.parametrize(("hour", "minute", "notified"), [(16, 30, True), (16, 31, False)])
async def test_an_owed_close_failure_is_notified_only_until_close_plus_30(temp_db, monkeypatch, hour, minute, notified):
    signal_id = await _interrupted_enforce_pass(temp_db, monkeypatch, FakeBroker([]))  # no broker position
    restarted = service(temp_db, mode="enforce", broker=FakeBroker([]))
    assert await restarted.run(at(FRIDAY, hour, minute)) == "present"
    _, closed = await release_events(temp_db)
    assert (closed["payload"]["outcome"], closed["payload"]["notified"]) == ("refused", notified)
    queued = [key for key in await notices(temp_db) if key.startswith("release-exit")]
    assert queued == ([f"release-exit/2026-11-06/{signal_id}/2026-11-06/failed"] if notified else [])


# --- failure of the pass itself --------------------------------------------------------------------------------


async def test_an_exception_before_the_journal_retries_on_the_next_tick(temp_db, caplog):
    await position(temp_db, "AAPL")
    captures = FakeCaptures(None, errors=[RuntimeError("secret capture detail")])
    release = service(temp_db, captures=captures)
    with caplog.at_level(logging.WARNING):
        assert await release.run(at(FRIDAY, 15, 30)) == "failed"
    assert await release_events(temp_db) == []
    assert await release.run(at(FRIDAY, 15, 35)) == "done"
    assert len(await release_events(temp_db)) == 1
    assert "secret" not in caplog.text
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "release_exit_failed"]
    assert record.__dict__["reason"] == "RuntimeError"


async def test_a_pass_that_never_completes_queues_one_unavailable_notice_at_the_close(temp_db):
    await position(temp_db, "AAPL")
    captures = FakeCaptures(None, errors=[RuntimeError("secret capture detail")] * 10)
    release = service(temp_db, captures=captures)
    assert await release.run(at(FRIDAY, 15, 30)) == "failed"
    assert await release.run(at(FRIDAY, 15, 55)) == "failed"
    assert len(captures.calls) == 2
    assert await notices(temp_db) == {}
    assert await release.run(at(FRIDAY, 16, 0)) == "failed"
    assert await release.run(at(FRIDAY, 16, 5)) == "failed"
    assert len(captures.calls) == 2  # never run after the close
    assert await notices(temp_db) == {
        "release-exit-unavailable/2026-11-06": (
            "Release exit unavailable for 2026-11-06: the pass did not complete before the close (RuntimeError). "
            "Open native equity positions were not checked for earnings releases; /positions lists them."
        )
    }
    assert await release_events(temp_db) == []


@pytest.mark.parametrize(("hour", "minute", "queued"), [(16, 30, True), (16, 31, False)])
async def test_the_unavailable_notice_is_queued_only_until_close_plus_30(temp_db, caplog, hour, minute, queued):
    await position(temp_db, "AAPL")
    captures = FakeCaptures(None, errors=[RuntimeError("secret capture detail")] * 10)
    release = service(temp_db, captures=captures)
    assert await release.run(at(FRIDAY, 15, 30)) == "failed"
    with caplog.at_level(logging.WARNING):
        assert await release.run(at(FRIDAY, hour, minute)) == "failed"
        assert await release.run(at(FRIDAY, hour, minute) + timedelta(minutes=5)) == "failed"
    assert list(await notices(temp_db)) == (["release-exit-unavailable/2026-11-06"] if queued else [])
    logged = [r for r in caplog.records if r.__dict__.get("event") == "release_exit_unavailable"]
    assert len(logged) == (0 if queued else 1)
    assert "secret" not in caplog.text


async def test_a_restart_after_the_close_skips_the_date(temp_db):
    await position(temp_db, "AAPL")
    captures = FakeCaptures(None)
    release = service(temp_db, captures=captures)
    assert await release.run(at(FRIDAY, 16, 10)) == "closed_market"
    assert captures.calls == [] and await release_events(temp_db) == [] and await notices(temp_db) == {}


async def test_a_calendar_failure_is_a_failed_tick(temp_db):
    release = service(temp_db, calendar=FakeCalendar(error=ConnectionError("secret")))
    assert await release.run(at(FRIDAY, 15, 30)) == "failed"
    assert await release_events(temp_db) == []
