"""The daily paper auto-entry digest: exact copy, rows from stored state, and its close + 15 minute due time."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update

from agentic_trader.constants import AuditEventType, SignalStatus
from agentic_trader.execution.auto_entry import AutoEntryAttempt, AutoEntryDigestRow
from agentic_trader.execution.durable import NotificationKind, WorkKind, WorkStatus
from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.presentation.auto_entry_digest import format_auto_entry_digest
from agentic_trader.storage.models import SignalRecord, WorkItemRecord
from agentic_trader.storage.workflow import encode
from tests.agent.test_auto_entry_sweep import auto_desk, notifications, record_auto_card, temp_db  # noqa: F401


NY = ZoneInfo("America/New_York")
REGULAR = MarketCalendarDay(date(2026, 10, 21), True, False, time(9, 30), time(16, 0))
EARLY = MarketCalendarDay(date(2026, 11, 27), True, True, time(9, 30), time(13, 0))
WEEKEND = MarketCalendarDay(date(2026, 10, 24), False, False, None, None)
FOOTER = "Paper fills are optimistic: no queue position or market impact."


class FakeCalendar:
    def __init__(self, *days: MarketCalendarDay, error: Exception | None = None):
        self.days = {day.date: day for day in days}
        self.error = error

    async def get_trading_day(self, target_date: date) -> MarketCalendarDay:
        if self.error is not None:
            raise self.error
        return self.days[target_date]

    async def get_calendar_range(self, start_date: date, end_date: date) -> list[MarketCalendarDay]:
        return list(self.days.values())


def test_digest_lines_for_each_outcome():
    rows = [
        AutoEntryDigestRow(1, "SPY", "filled", "10 @ $100.25 (open)"),
        AutoEntryDigestRow(2, "QQQ", "filled", "5 @ $400.00 (closed)"),
        AutoEntryDigestRow(3, "IWM", "cancelled_unfilled"),
        AutoEntryDigestRow(4, "DIA", "still_resting"),
        AutoEntryDigestRow(5, "XLK", "refused", "cap reached"),
        AutoEntryDigestRow(6, "XLF", "dismissed"),
        AutoEntryDigestRow(7, "XLE", "cancelled_by_operator"),
        AutoEntryDigestRow(8, "XLV", "abandoned"),
        AutoEntryDigestRow(9, "XLY", "refused", "price <$5 & thin"),
    ]

    assert format_auto_entry_digest(rows, et_date=date(2026, 10, 21)).split("\n") == [
        "🤖 Auto-entry digest — 2026-10-21",
        "SPY #1: filled 10 @ $100.25 (open)",
        "QQQ #2: filled 5 @ $400.00 (closed)",
        "IWM #3: cancelled unfilled at the close",
        "DIA #4: not yet resolved (entry or cancel pending)",
        "XLK #5: refused: cap reached",
        "XLF #6: dismissed before placement",
        "XLE #7: cancelled by you",
        "XLV #8: not placed in time",
        "XLY #9: refused: price &lt;$5 &amp; thin",
        FOOTER,
    ]


def test_digest_footer_verbatim():
    assert format_auto_entry_digest([], et_date=date(2026, 10, 21)).endswith(f"\n{FOOTER}")


async def plant_work(db, sid, kind, status, *, payload=None, result=None):
    now = datetime.now(UTC)
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        session.add(
            WorkItemRecord(
                id=f"{kind}-{sid}",
                scope=db.workflows.scope,
                kind=kind,
                status=status,
                dedup_key=str(sid),
                sequence=None,
                payload=encode({"signal_id": sid, **(payload or {})}),
                result=encode(result or {}),
                attempts=1,
                created_at=now,
                available_at=now,
            )
        )


async def set_signal(db, sid, **values):
    async with db.session_factory() as session, session.begin():
        await session.execute(update(SignalRecord).where(SignalRecord.id == sid).values(**values))


async def attempt(db, sid, outcome, reason):
    await db.workflows.record_auto_entry_attempt(
        AutoEntryAttempt(sid, None, outcome, reason, None, "PENDING", datetime.now(UTC)), recard=None, entered=False
    )


async def test_rows_are_derived_from_stored_state_in_rank_order(temp_db):  # noqa: F811
    issued = datetime(2026, 10, 21, 10, 35, tzinfo=NY)
    ids = {}
    for rank, name in enumerate(
        [
            "filled",
            "closed",
            "unfilled",
            "resting",
            "refused",
            "rejected",
            "dismissed",
            "operator",
            "abandoned",
            "broker",
            "broker_unrecorded",
        ],
        1,
    ):
        ids[name] = await record_auto_card(temp_db, rank=rank, contract=f"S{rank}", issued_at=issued)
    when = datetime.now(UTC)
    await set_signal(temp_db, ids["filled"], status=SignalStatus.EXECUTED, executed_at=when, entry_price=100.25)
    await set_signal(temp_db, ids["closed"], status=SignalStatus.CLOSED_WIN, executed_at=when, entry_price=50.0)
    await set_signal(temp_db, ids["unfilled"], status=SignalStatus.FAILED)
    await plant_work(temp_db, ids["unfilled"], WorkKind.ENTRY, WorkStatus.ACCEPTED, payload={"entry_trigger": "auto"})
    await plant_work(
        temp_db,
        ids["unfilled"],
        WorkKind.ENTRY_CANCEL,
        WorkStatus.ACCEPTED,
        payload={"cause": "deadline"},
    )
    await set_signal(temp_db, ids["resting"], status=SignalStatus.EXECUTED)
    await plant_work(
        temp_db, ids["resting"], WorkKind.ENTRY_CANCEL, WorkStatus.SUBMITTING, payload={"cause": "deadline"}
    )
    await attempt(temp_db, ids["refused"], "refused", "cap reached")
    await set_signal(temp_db, ids["rejected"], status=SignalStatus.FAILED)
    await plant_work(
        temp_db,
        ids["rejected"],
        WorkKind.ENTRY,
        WorkStatus.REJECTED,
        payload={"entry_trigger": "auto"},
        result={"error_message": "HTTPError: secret broker detail"},
    )
    await set_signal(temp_db, ids["dismissed"], status=SignalStatus.DISMISSED)
    await set_signal(temp_db, ids["operator"], status=SignalStatus.FAILED)
    await plant_work(
        temp_db, ids["operator"], WorkKind.ENTRY_CANCEL, WorkStatus.ACCEPTED, payload={"cause": "operator"}
    )
    await attempt(temp_db, ids["abandoned"], "abandoned", "card too old")
    for name in ("broker", "broker_unrecorded"):
        await set_signal(temp_db, ids[name], status=SignalStatus.FAILED)
        await plant_work(temp_db, ids[name], WorkKind.ENTRY, WorkStatus.ACCEPTED, payload={"entry_trigger": "auto"})
    await temp_db.record_audit(
        AuditEventType.ENTRY_EXECUTION_UPDATED,
        {"order_id": "entry-1", "status": "expired", "filled_quantity": "0"},
        signal_id=ids["broker"],
    )
    # A 14:35 card of rank 1 sorts after every 10:35 card.
    late = await record_auto_card(temp_db, rank=1, contract="LATE", issued_at=datetime(2026, 10, 21, 14, 35, tzinfo=NY))
    await set_signal(temp_db, late, status=SignalStatus.DISMISSED)

    rows = await temp_db.workflows.auto_entry_digest_rows(date(2026, 10, 21))

    assert [(row.symbol, row.outcome, row.detail) for row in rows] == [
        ("S1", "filled", "10 @ $100.25 (open)"),
        ("S2", "filled", "10 @ $50.00 (closed)"),
        ("S3", "cancelled_unfilled", None),
        ("S4", "still_resting", None),
        ("S5", "refused", "cap reached"),
        ("S6", "refused", "rejected after it was queued"),
        ("S7", "dismissed", None),
        ("S8", "cancelled_by_operator", None),
        ("S9", "abandoned", None),
        ("S10", "refused", "broker ended the order expired without fills"),
        ("S11", "refused", "broker ended the order without fills"),
        ("LATE", "dismissed", None),
    ]
    assert "secret" not in repr(rows)
    assert await temp_db.workflows.auto_entry_digest_rows(date(2026, 10, 22)) == []


@pytest.mark.parametrize(
    ("day", "before", "due"),
    [
        (REGULAR, time(15, 59), time(16, 15)),
        (EARLY, time(13, 14), time(13, 15)),
    ],
    ids=["regular", "early_close"],
)
async def test_digest_due_at_close_plus_15(auto_desk, temp_db, day, before, due):  # noqa: F811
    auto_desk.lifetime_service.calendar = FakeCalendar(day)
    sid = await record_auto_card(temp_db, issued_at=datetime.combine(day.date, time(10, 35), NY))
    await set_signal(temp_db, sid, status=SignalStatus.DISMISSED)
    key = f"auto-entry-digest/{day.date.isoformat()}"

    assert await auto_desk.publish_auto_entry_digest(datetime.combine(day.date, before, NY)) is False
    assert key not in await notifications(temp_db)

    assert await auto_desk.publish_auto_entry_digest(datetime.combine(day.date, due, NY)) is True
    assert (
        await auto_desk.publish_auto_entry_digest(datetime.combine(day.date, due, NY) + timedelta(minutes=5)) is False
    )

    queued = [k for k in await notifications(temp_db) if k.startswith("auto-entry-digest/")]
    assert queued == [key]
    message = (await notifications(temp_db))[key]
    assert message["kind"] == NotificationKind.MESSAGE
    assert message["arguments"]["formatted"] is True
    assert message["arguments"]["text"].split("\n") == [
        f"🤖 Auto-entry digest — {day.date.isoformat()}",
        f"SPY #{sid}: dismissed before placement",
        FOOTER,
    ]


async def test_no_digest_without_auto_cards(auto_desk, temp_db):  # noqa: F811
    auto_desk.lifetime_service.calendar = FakeCalendar(REGULAR)
    # A tap-era card (no marker) is not reported.
    await temp_db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        asset_class="EQUITY",
        quantity=1.0,
        decision_provenance={"rank": 1},
    )
    at = datetime(2026, 10, 21, 16, 20, tzinfo=NY)

    assert await auto_desk.publish_auto_entry_digest(at) is False
    assert not [k for k in await notifications(temp_db) if k.startswith("auto-entry-digest/")]


@pytest.mark.parametrize(
    "calendar",
    [FakeCalendar(WEEKEND), FakeCalendar(REGULAR, error=RuntimeError("calendar down")), None],
    ids=["not_a_trading_day", "calendar_failure", "no_calendar"],
)
async def test_no_digest_without_a_trading_day_calendar_read(auto_desk, temp_db, calendar):  # noqa: F811
    auto_desk.lifetime_service.calendar = calendar
    await record_auto_card(temp_db, issued_at=datetime(2026, 10, 24, 10, 35, tzinfo=NY))

    assert await auto_desk.publish_auto_entry_digest(datetime(2026, 10, 24, 17, 0, tzinfo=NY)) is False
    assert not [k for k in await notifications(temp_db) if k.startswith("auto-entry-digest/")]


async def test_digest_not_due_at_16_10_and_due_at_16_15(auto_desk, temp_db):  # noqa: F811
    auto_desk.lifetime_service.calendar = FakeCalendar(REGULAR)
    sid = await record_auto_card(temp_db, issued_at=datetime(2026, 10, 21, 10, 35, tzinfo=NY))
    await set_signal(temp_db, sid, status=SignalStatus.DISMISSED)

    assert await auto_desk.publish_auto_entry_digest(datetime(2026, 10, 21, 16, 10, tzinfo=NY)) is False
    assert await auto_desk.publish_auto_entry_digest(datetime(2026, 10, 21, 16, 15, tzinfo=NY)) is True


async def test_calendar_failure_warns_once_per_date_with_the_type_name(auto_desk, temp_db, caplog):  # noqa: F811
    auto_desk.lifetime_service.calendar = FakeCalendar(REGULAR, error=RuntimeError("secret calendar detail"))
    at = datetime(2026, 10, 21, 16, 20, tzinfo=NY)

    with caplog.at_level("DEBUG", logger="copilot"):
        for _ in range(3):
            assert await auto_desk.publish_auto_entry_digest(at) is False

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "RuntimeError" in warnings[0].getMessage()
    assert "secret" not in warnings[0].getMessage() and warnings[0].exc_info is None
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_sent_digest_is_a_cheap_no_op_that_skips_the_calendar(auto_desk, temp_db):  # noqa: F811
    calendar = FakeCalendar(REGULAR)
    auto_desk.lifetime_service.calendar = calendar
    sid = await record_auto_card(temp_db, issued_at=datetime(2026, 10, 21, 10, 35, tzinfo=NY))
    await set_signal(temp_db, sid, status=SignalStatus.DISMISSED)
    at = datetime(2026, 10, 21, 16, 20, tzinfo=NY)
    assert await auto_desk.publish_auto_entry_digest(at) is True

    calendar.error = RuntimeError("must not be read")

    assert await auto_desk.publish_auto_entry_digest(at + timedelta(minutes=5)) is False
