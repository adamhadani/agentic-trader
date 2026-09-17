"""Expiry never converts partial/ambiguous broker evidence into released exposure."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.execution.durable import OrderObservation
from agentic_trader.execution.lifetime_policy import TradeLifetimePolicy
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
