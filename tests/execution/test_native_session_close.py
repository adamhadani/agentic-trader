"""A native entry is due at its submission session's close + 5 minutes, capped by the one-session bound.

Alpha, PEAD/catalog and probe policies keep their own semantics; only the marker-resolved native
policy reads the submission session's close or lets a working partial fill rest.
"""

from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.execution.durable import OrderObservation
from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    NATIVE_ENTRY_LIFETIME_KEY,
    NATIVE_ENTRY_LIFETIME_VERSION,
    NativeEntryLifetimePolicy,
    SessionEvidenceError,
    SessionLifetimePolicy,
    TradeLifetimePolicy,
    daily_entry_lifetime,
    entry_lifetime_of,
    has_holding_deadline,
    native_entry_lifetime_marker,
)
from agentic_trader.execution.lifetimes import EntryIdentity, LifetimeAction, TradeLifetimeService, assess_lifetime
from agentic_trader.market.session import MarketCalendarDay


NY = ZoneInfo("America/New_York")
NATIVE = NativeEntryLifetimePolicy()
IDENTITY = EntryIdentity("entry", "client", "SPY", "buy", "10")
# Wednesday 2026-10-21 (regular close 16:00) and Friday 2026-11-27 (early close 13:00).
REGULAR = MarketCalendarDay(date(2026, 10, 21), True, False, time(9, 30), time(16, 0))
EARLY = MarketCalendarDay(date(2026, 11, 27), True, True, time(9, 30), time(13, 0))


def _order(submitted_at: datetime, **changes) -> OrderObservation:
    fields = {
        "order_id": "entry",
        "client_order_id": "client",
        "symbol": "SPY",
        "side": "buy",
        "status": "new",
        "quantity": "10",
        "filled_quantity": "0",
        "order_type": "limit",
        "order_class": "bracket",
        "submitted_at": submitted_at,
        "updated_at": submitted_at,
    }
    return OrderObservation(**{**fields, **changes})


@pytest.mark.parametrize(
    ("day", "submitted", "due"),
    [
        (REGULAR, datetime(2026, 10, 21, 10, 40, tzinfo=NY), datetime(2026, 10, 21, 16, 5, tzinfo=NY)),
        (EARLY, datetime(2026, 11, 27, 9, 45, tzinfo=NY), datetime(2026, 11, 27, 13, 5, tzinfo=NY)),
    ],
)
def test_native_entry_is_due_five_minutes_after_its_session_close(day, submitted, due):
    assert NATIVE.entry_deadline(submitted, [day]) == due.astimezone(UTC)
    order = _order(submitted)
    before = assess_lifetime(NATIVE, IDENTITY, order, due - timedelta(seconds=1), [day])
    at = assess_lifetime(NATIVE, IDENTITY, order, due, [day])
    assert (before.action, at.action) == (LifetimeAction.NONE, LifetimeAction.CANCEL_ENTRY)
    assert at.deadline == due.astimezone(UTC)


def test_the_one_session_outer_bound_still_applies():
    # A synthetic all-day session: submission + 16 h (23:00) comes before close + 5 min (00:04).
    submitted = datetime(2026, 10, 21, 7, 0, tzinfo=NY)
    late_close = MarketCalendarDay(date(2026, 10, 21), True, False, time(0, 0), time(23, 59))
    outer = submitted.astimezone(UTC) + timedelta(seconds=DAILY_ENTRY_LIFETIME_SECONDS)
    assert NATIVE.entry_deadline(submitted, [late_close]) == outer
    # Without a session calendar the outer bound alone governs; nothing is guessed.
    assert NATIVE.entry_deadline(submitted, None) == outer
    decision = assess_lifetime(NATIVE, IDENTITY, _order(submitted), outer - timedelta(seconds=1), None)
    assert decision.action == LifetimeAction.NONE and decision.deadline == outer


@pytest.mark.parametrize(
    "submitted",
    [datetime(2026, 10, 24, 11, 0, tzinfo=NY), datetime(2026, 10, 21, 16, 6, tzinfo=NY)],
    ids=["non_trading_day", "after_close_buffer"],
)
def test_a_submission_outside_its_regular_session_is_reviewed(submitted):
    with pytest.raises(SessionEvidenceError):
        NATIVE.entry_deadline(submitted, [REGULAR])
    decision = assess_lifetime(NATIVE, IDENTITY, _order(submitted), submitted + timedelta(hours=1), [REGULAR])
    assert (decision.action, decision.reason) == (LifetimeAction.REVIEW, "submission_outside_regular_session")


def test_native_partial_fill_rests_until_the_deadline_then_reviews():
    submitted = datetime(2026, 10, 21, 10, 40, tzinfo=NY)
    due = datetime(2026, 10, 21, 16, 5, tzinfo=NY)
    partial = _order(submitted, status="partially_filled", filled_quantity="2")
    before = assess_lifetime(NATIVE, IDENTITY, partial, due - timedelta(seconds=1), [REGULAR])
    at = assess_lifetime(NATIVE, IDENTITY, partial, due, [REGULAR])
    assert (before.action, before.reason) == (LifetimeAction.NONE, "within_resting_lifetime")
    assert (at.action, at.reason) == (LifetimeAction.REVIEW, "partial_entry_requires_protection_review")


def test_a_terminal_native_partial_is_reviewed_before_the_deadline():
    submitted = datetime(2026, 10, 21, 10, 40, tzinfo=NY)
    canceled = _order(submitted, status="canceled", filled_quantity="2")
    decision = assess_lifetime(NATIVE, IDENTITY, canceled, submitted + timedelta(minutes=5), [REGULAR])
    assert decision.action == LifetimeAction.REVIEW


@pytest.mark.parametrize(
    "policy",
    [
        daily_entry_lifetime(),
        TradeLifetimePolicy(resting_seconds=120, holding_seconds=180),
        SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20),
    ],
    ids=["alpha_daily", "alpha_timed", "catalog_session"],
)
def test_alpha_and_catalog_policies_keep_their_entry_semantics(policy):
    """A pre-deadline partial still reviews, and the resting deadline ignores any session close."""
    submitted = datetime(2026, 10, 21, 10, 40, tzinfo=NY)
    partial = _order(submitted, status="partially_filled", filled_quantity="2")
    decision = assess_lifetime(policy, IDENTITY, partial, submitted + timedelta(seconds=60), [REGULAR])
    assert (decision.action, decision.reason) == (LifetimeAction.REVIEW, "partial_entry_requires_protection_review")
    resting = assess_lifetime(policy, IDENTITY, _order(submitted), submitted + timedelta(seconds=60), [REGULAR])
    assert resting.deadline == submitted.astimezone(UTC) + timedelta(seconds=policy.resting_seconds)


def test_native_policy_has_no_holding_deadline():
    assert not has_holding_deadline(NATIVE)
    assert not has_holding_deadline(daily_entry_lifetime())
    assert has_holding_deadline(TradeLifetimePolicy(resting_seconds=120, holding_seconds=180))
    assert has_holding_deadline(SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20))
    filled = _order(
        datetime(2026, 10, 21, 10, 40, tzinfo=NY),
        status="filled",
        filled_quantity="10",
        filled_at=datetime(2026, 10, 21, 11, 0, tzinfo=NY),
    )
    decision = assess_lifetime(NATIVE, IDENTITY, filled, datetime(2026, 12, 1, tzinfo=UTC))
    assert (decision.action, decision.reason) == (LifetimeAction.NONE, "holding_lifetime_disabled")


def test_marker_round_trips_and_the_written_v2_marker_reads_as_equivalent():
    config = AppConfig(execution_mode=ExecutionMode.ALPACA)
    config.execution.native_entry_lifetime = "session"
    marker = native_entry_lifetime_marker(config, "EQUITY")
    assert marker == asdict(NATIVE) and marker["version"] == NATIVE_ENTRY_LIFETIME_VERSION
    assert entry_lifetime_of({"decision_provenance": {NATIVE_ENTRY_LIFETIME_KEY: marker}}) == NATIVE
    legacy = asdict(daily_entry_lifetime())
    assert entry_lifetime_of({"decision_provenance": {NATIVE_ENTRY_LIFETIME_KEY: legacy}}) == NATIVE


@pytest.mark.parametrize("asset_class", ["FUTURES", "futures", "CRYPTO"])
def test_no_marker_for_a_card_the_broker_cannot_execute(asset_class):
    config = AppConfig(execution_mode=ExecutionMode.ALPACA)
    config.execution.native_entry_lifetime = "session"
    assert native_entry_lifetime_marker(config, asset_class) is None


def test_a_native_policy_is_never_read_from_alpha_policy():
    with pytest.raises(ValueError):
        entry_lifetime_of({"alpha_policy": {"lifetime": asdict(NATIVE)}})
    # Any other marker shape is malformed, not reinterpreted.
    other = asdict(TradeLifetimePolicy(resting_seconds=120, holding_seconds=180))
    with pytest.raises(ValueError):
        entry_lifetime_of({"decision_provenance": {NATIVE_ENTRY_LIFETIME_KEY: other}})


class _Calendar:
    def __init__(self, error: Exception | None = None):
        self.requests: list[tuple[date, date]] = []
        self.error = error

    async def get_calendar_range(self, start, end):
        self.requests.append((start, end))
        if self.error:
            raise self.error
        return [REGULAR] if start == REGULAR.date else []


def _service(calendar, broker=None, repository=None):
    return TradeLifetimeService(
        repository or SimpleNamespace(store=None), broker, None, config=SimpleNamespace(), calendar=calendar
    )


async def test_the_service_reads_only_the_submission_date_for_a_native_entry():
    calendar = _Calendar()
    sessions = await _service(calendar)._sessions(NATIVE, _order(datetime(2026, 10, 21, 10, 40, tzinfo=NY)))
    assert sessions == [REGULAR]
    assert calendar.requests == [(REGULAR.date, REGULAR.date)]


async def test_a_calendar_failure_leaves_the_native_outer_bound_and_never_reviews():
    sessions = await _service(_Calendar(RuntimeError("down")))._sessions(
        NATIVE, _order(datetime(2026, 10, 21, 10, 40, tzinfo=NY))
    )
    assert sessions is None


def _filled_signal(policy_document: dict, *, alpha: bool = False) -> dict:
    return {
        "id": 7,
        "contract": "SPY",
        "status": "EXECUTED",
        "executed_at": "2026-10-21T15:00:00+00:00",
        "broker_order_id": "entry",
        "alpha_policy": {"lifetime": policy_document} if alpha else None,
        "decision_provenance": None if alpha else {NATIVE_ENTRY_LIFETIME_KEY: policy_document},
    }


def _desk(signal: dict):
    db = SimpleNamespace(get_active_positions=AsyncMock(return_value=[signal]), get_close_request=AsyncMock())
    store = SimpleNamespace(db=db, list_work=AsyncMock(return_value=[]), observe_orders=AsyncMock())
    repository = SimpleNamespace(
        store=store,
        review=AsyncMock(),
        entry=AsyncMock(return_value=None),
        order=AsyncMock(return_value=None),
        missed_bound=AsyncMock(),
    )
    broker = SimpleNamespace(read_entry_group=AsyncMock(side_effect=ConnectionError("transient")))
    return _service(_Calendar(), broker, repository), broker, repository


async def test_a_filled_native_position_is_never_read_and_a_read_error_cannot_halt():
    service, broker, repository = _desk(_filled_signal(asdict(NATIVE)))
    await service.reconcile()
    assert await service.resume_blockers() == []
    broker.read_entry_group.assert_not_awaited()
    repository.review.assert_not_awaited()


async def test_a_filled_alpha_position_with_a_holding_deadline_is_still_read():
    timed = asdict(TradeLifetimePolicy(resting_seconds=120, holding_seconds=180))
    service, _broker, repository = _desk(_filled_signal(timed, alpha=True))
    await service.reconcile()
    # Its durable identity is missing in this fixture, so the alpha path still reviews as before.
    repository.review.assert_awaited_once()
    assert await service.resume_blockers() == [7]
