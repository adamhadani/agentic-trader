from datetime import UTC, date as _date, datetime
from decimal import Decimal

import pytest

from agentic_trader.accounting.ledger import AccountSnapshot, reconcile, unsupported_activity_issue
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind


def fill(activity_id, side, qty, price):
    return {
        "id": activity_id,
        "activity_type": "FILL",
        "side": side,
        "qty": str(qty),
        "price": str(price),
        "symbol": "SPY",
        "order_id": "order-" + activity_id,
        "transaction_time": datetime.now(UTC).isoformat(),
    }


@pytest.mark.parametrize(
    "side,exit_side,qty,basis,cash,profit",
    [
        ("buy", "sell", "5", "500", "9550", "50"),
        ("sell_short", "buy", "-5", "-500", "10450", "-50"),
    ],
)
def test_partial_external_fills_use_broker_signed_basis(side, exit_side, qty, basis, cash, profit):
    activities = [
        {"id": "deposit", "activity_type": "JNLC", "net_amount": "10000"},
        fill("a", side, 4, 100),
        fill("b", side, 6, 100),
        fill("c", exit_side, 5, 110),
    ]
    snapshot = AccountSnapshot(
        account_id="account",
        cash=cash,
        positions={"SPY": {"qty": qty, "cost_basis": basis, "unrealized_pl": "7.25", "asset_class": "us_equity"}},
    )
    report = reconcile(activities, snapshot)
    assert report.ready, report.issues
    assert report.gross_realized == Decimal(profit)
    assert report.unrealized == Decimal("7.25")
    assert report.fill_count == 3


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"cash": "9001"}, "cash"),
        ({"positions": {}}, "quantity"),
    ],
)
def test_discrepancies_withhold_pnl(change, reason):
    snapshot = AccountSnapshot(
        account_id="account",
        cash="9000",
        positions={"SPY": {"qty": "10", "cost_basis": "1000", "unrealized_pl": "0", "asset_class": "us_equity"}},
    )
    report = reconcile(
        [{"id": "deposit", "activity_type": "CSD", "net_amount": "10000"}, fill("a", "buy", 10, 100)],
        AccountSnapshot.model_validate({**snapshot.model_dump(), **change}),
    )
    assert not report.ready
    assert report.gross_realized is None
    assert reason in " ".join(report.issues).lower()


@pytest.mark.parametrize("kind,net,ready", [("FEE", "-1", True), ("DIV", "2", True), ("SPLIT", "0", False)])
def test_fees_income_and_unsupported_actions(kind, net, ready):
    report = reconcile(
        [{"id": "a", "activity_type": kind, "net_amount": net}],
        AccountSnapshot(account_id="account", cash=net, positions={}),
    )
    assert report.ready is ready
    if ready:
        assert report.net_realized == Decimal(net)


@pytest.mark.parametrize(
    "side,qty,price", [("unknown", "1", "1"), ("buy", "NaN", "1"), ("buy", "1", "Infinity"), ("buy", "-1", "10")]
)
def test_invalid_fill_is_visible_and_not_valued(side, qty, price):
    report = reconcile([fill("a", side, qty, price)], AccountSnapshot(account_id="account", cash="0", positions={}))
    assert not report.ready
    assert report.gross_realized is None


@pytest.mark.parametrize("qty", [None, "100"])
def test_dividend_entitlement_quantity_does_not_change_inventory(qty):
    report = reconcile(
        [{"id": "a", "activity_type": "DIV", "net_amount": "1", "qty": qty}],
        AccountSnapshot(account_id="account", cash="1", positions={}),
    )
    assert report.ready and report.income == 1


SPLIT = CorporateAction(
    id="s",
    kind=CorporateActionKind.FORWARD_SPLIT,
    symbol="NVDA",
    effective_on=_date(2024, 6, 10),
    old_rate=1.0,
    new_rate=10.0,
)
DIVIDEND = CorporateAction(
    id="d", kind=CorporateActionKind.CASH_DIVIDEND, symbol="NVDA", effective_on=_date(2024, 6, 12), rate=0.01
)


def test_unsupported_activity_names_a_matching_structural_action():
    activity = {
        "id": "a1",
        "activity_type": "SSP",
        "symbol": "NVDA",
        "date": "2024-06-10",
        "qty": "90",
        "net_amount": "0",
    }
    assert unsupported_activity_issue("SSP", activity, [DIVIDEND, SPLIT]) == (
        "Unsupported activity type: SSP (forward split 10-for-1 NVDA effective 2024-06-10 in corporate-action evidence)"
    )


@pytest.mark.parametrize(
    "activity",
    [
        {"activity_type": "SSP", "symbol": "NVDA", "date": "2024-07-15"},  # outside ten days
        {"activity_type": "SSP", "symbol": "AAPL", "date": "2024-06-10"},  # other symbol
        {"activity_type": "SSP", "symbol": "NVDA", "date": "not-a-date"},
        {"activity_type": "SSP"},
    ],
)
def test_unsupported_activity_without_a_match_keeps_the_plain_text(activity):
    assert unsupported_activity_issue("SSP", activity, [SPLIT]) == "Unsupported activity type: SSP"
    assert unsupported_activity_issue("SSP", activity, []) == "Unsupported activity type: SSP"


def test_reconcile_threads_actions_into_the_issue_and_still_withholds_totals():
    snapshot = AccountSnapshot(account_id="acct", cash=Decimal(1000), positions={})
    activities = [
        {"id": "deposit", "activity_type": "JNLC", "net_amount": "1000"},
        {"id": "a1", "activity_type": "SSP", "symbol": "NVDA", "date": "2024-06-10", "qty": "90", "net_amount": "0"},
    ]
    report = reconcile(activities, snapshot, corporate_actions=[SPLIT])
    assert report.issues == [
        "Unsupported activity type: SSP (forward split 10-for-1 NVDA effective 2024-06-10 in corporate-action evidence)"
    ]
    assert report.gross_realized is None and report.ready is False
    assert reconcile(activities, snapshot).issues == ["Unsupported activity type: SSP"]
