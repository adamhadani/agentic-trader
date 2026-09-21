"""Expiry never converts partial/ambiguous broker evidence into released exposure."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.execution.durable import OrderObservation
from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    TRADE_LIFETIME_VERSION_INDEPENDENT,
    TradeLifetimePolicy,
    daily_entry_lifetime,
)
from agentic_trader.execution.lifetimes import EntryIdentity, LifetimeAction, assess_lifetime


@pytest.fixture
def lifetime_order():
    now = datetime(2026, 9, 17, 15, tzinfo=UTC)
    identity = EntryIdentity("entry", "client", "SPY", "buy", "10")
    order = OrderObservation(
        order_id="entry",
        client_order_id="client",
        symbol="SPY",
        side="buy",
        status="new",
        quantity="10",
        filled_quantity="0",
        order_type="limit",
        order_class="bracket",
        submitted_at=now - timedelta(seconds=120),
        updated_at=now,
    )
    return now, identity, order, TradeLifetimePolicy(resting_seconds=120, holding_seconds=180)


@pytest.mark.parametrize(
    "offset,expected", [(-1, LifetimeAction.NONE), (0, LifetimeAction.CANCEL_ENTRY), (1, LifetimeAction.CANCEL_ENTRY)]
)
def test_exact_entry_boundary(lifetime_order, offset, expected):
    now, identity, order, policy = lifetime_order
    assert assess_lifetime(policy, identity, order, now + timedelta(seconds=offset)).action == expected


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"status": "filled", "filled_quantity": "10"}, LifetimeAction.CLOSE_POSITION),
        ({"status": "partially_filled", "filled_quantity": "2"}, LifetimeAction.REVIEW),
        ({"status": "canceled", "filled_quantity": "2"}, LifetimeAction.REVIEW),
        ({"status": "canceled"}, LifetimeAction.NONE),
        ({"status": "pending_cancel"}, LifetimeAction.REVIEW),
        ({"status": "pending_replace"}, LifetimeAction.REVIEW),
        ({"client_order_id": "other"}, LifetimeAction.REVIEW),
        ({"replaced_by": "other"}, LifetimeAction.REVIEW),
        ({"quantity": "11"}, LifetimeAction.REVIEW),
        ({"submitted_at": None}, LifetimeAction.REVIEW),
    ],
)
def test_lifetime_action_requires_exact_broker_evidence(lifetime_order, changes, expected):
    now, identity, order, policy = lifetime_order
    order = order.model_copy(update={"filled_at": now - timedelta(seconds=180), **changes})
    assert assess_lifetime(policy, identity, order, now).action == expected


@pytest.mark.parametrize(
    ("resting_seconds", "holding_seconds", "reason"),
    [
        (None, 180, "resting_lifetime_disabled"),
        (120, None, "holding_lifetime_disabled"),
    ],
)
def test_independent_v2_deadlines_can_be_disabled(lifetime_order, resting_seconds, holding_seconds, reason):
    now, identity, order, _ = lifetime_order
    policy = TradeLifetimePolicy(
        resting_seconds=resting_seconds,
        holding_seconds=holding_seconds,
        version=TRADE_LIFETIME_VERSION_INDEPENDENT,
    )
    if holding_seconds is None:
        order = order.model_copy(update={"status": "filled", "filled_quantity": order.quantity, "filled_at": now})
    decision = assess_lifetime(policy, identity, order, now + timedelta(days=31))
    assert decision.action == LifetimeAction.NONE and decision.reason == reason and decision.deadline is None


@pytest.mark.parametrize(
    "fields",
    [
        {"resting_seconds": None, "holding_seconds": None},
        {"resting_seconds": None, "holding_seconds": 0},
    ],
)
def test_independent_v2_requires_one_positive_deadline(fields):
    with pytest.raises(ValueError):
        TradeLifetimePolicy(version=TRADE_LIFETIME_VERSION_INDEPENDENT, **fields)


def test_daily_entry_lifetime_cancels_after_one_session_and_never_holds():
    """The version-5 lifetime (one fixed entry-only deadline, no holding deadline)."""
    now = datetime(2026, 9, 17, 15, tzinfo=UTC)
    identity = EntryIdentity("entry", "client", "SPY", "buy", "10")
    policy = daily_entry_lifetime()
    resting = OrderObservation(
        order_id="entry",
        client_order_id="client",
        symbol="SPY",
        side="buy",
        status="new",
        quantity="10",
        filled_quantity="0",
        order_type="limit",
        order_class="bracket",
        submitted_at=now - timedelta(seconds=DAILY_ENTRY_LIFETIME_SECONDS - 1),
        updated_at=now,
    )
    before = assess_lifetime(policy, identity, resting, now)
    assert before.action == LifetimeAction.NONE

    expired = resting.model_copy(update={"submitted_at": now - timedelta(seconds=DAILY_ENTRY_LIFETIME_SECONDS)})
    at_deadline = assess_lifetime(policy, identity, expired, now)
    assert at_deadline.action == LifetimeAction.CANCEL_ENTRY

    filled = resting.model_copy(
        update={"status": "filled", "filled_quantity": "10", "filled_at": now - timedelta(days=365)}
    )
    held = assess_lifetime(policy, identity, filled, now)
    assert held.action == LifetimeAction.NONE
    assert held.reason == "holding_lifetime_disabled"
