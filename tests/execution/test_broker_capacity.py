"""Fresh brokerage evidence, exact ownership and finite aggregate commitments."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agentic_trader.broker.base import BrokerPosition, OrderRequest
from agentic_trader.execution.capacity import assess_entry_capacity
from agentic_trader.execution.durable import OrderObservation


@pytest.fixture
def capacity_case(app_config):
    now = datetime.now(UTC)
    account = {
        "account_id": "account",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": Decimal(100000),
        "equity": Decimal(100000),
        "buying_power": Decimal(100000),
        "regt_buying_power": Decimal(100000),
        "non_marginable_buying_power": Decimal(100000),
        "multiplier": Decimal(2),
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
        "shorting_enabled": True,
    }
    context = SimpleNamespace(
        account=SimpleNamespace(**account),
        account_before=SimpleNamespace(**account),
        asset=SimpleNamespace(
            symbol="SPY",
            asset_class="us_equity",
            status="active",
            tradable=True,
            marginable=True,
            shortable=True,
            fractionable=True,
            borrow_status="easy_to_borrow",
        ),
        quote=SimpleNamespace(symbol="SPY", bid_price=Decimal(99), ask_price=Decimal(101), timestamp=now, feed="iex"),
        price=Decimal(100),
        trade_timestamp=now,
        requested_at=now,
        observed_at=now,
        session_closes_at=now + timedelta(hours=1),
        positions=(),
        orders=(),
        simulated=False,
    )
    order = OrderRequest(
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        quantity=10,
    )
    risk = SimpleNamespace(account_id="account", equity=Decimal(100000), drawdown_pct=Decimal(0))
    return order, context, app_config, risk


def assess(case, reservations=()):
    order, context, config, risk = case
    return assess_entry_capacity(order, list(reservations), context, config, account_risk=risk)


@pytest.mark.parametrize("direction,required", [("LONG", "1010"), ("SHORT", "1040.30")])
def test_funding_uses_direction_and_quote_without_broker_order_double_count(capacity_case, direction, required):
    order, context, _, _ = capacity_case
    if direction == "SHORT":
        order.direction, order.side, order.stop_loss, order.take_profit = direction, "SELL", 105, 90
    context.account.buying_power = Decimal(required)
    result = assess(capacity_case)
    assert result.required_buying_power == Decimal(required)
    context.account.buying_power -= Decimal("0.01")
    with pytest.raises(ValueError, match="buying power"):
        assess(capacity_case)


@pytest.mark.parametrize(
    "target,field,value,reason",
    [
        ("account", "trading_blocked", True, "blocked"),
        ("account", "account_blocked", True, "blocked"),
        ("account", "trade_suspended_by_user", True, "blocked"),
        ("account", "status", "INACTIVE", "active"),
        ("account", "currency", "EUR", "USD"),
        ("account", "account_id", "other", "identity"),
        ("asset", "tradable", False, "tradable"),
        ("asset", "status", "inactive", "active"),
        ("asset", "symbol", "OTHER", "identity"),
        ("asset", "asset_class", "crypto", "equity"),
        ("account", "regt_buying_power", Decimal(1009), "buying power"),
        ("account_before", "buying_power", Decimal(1009), "buying power"),
    ],
)
def test_known_restrictions_refuse(capacity_case, target, field, value, reason):
    setattr(getattr(capacity_case[1], target), field, value)
    with pytest.raises(ValueError, match=reason):
        assess(capacity_case)


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("account", "shorting_enabled", False),
        ("account", "equity", Decimal(1999)),
        ("account", "multiplier", Decimal(1)),
        ("asset", "shortable", False),
        ("asset", "marginable", False),
        ("asset", "borrow_status", None),
        ("asset", "borrow_status", "hard_to_borrow"),
    ],
)
def test_short_borrow_evidence_required(capacity_case, target, field, value):
    order, context, _, _ = capacity_case
    order.direction, order.side, order.stop_loss, order.take_profit = "SHORT", "SELL", 105, 90
    setattr(getattr(context, target), field, value)
    with pytest.raises(ValueError, match="short|borrow|margin"):
        assess(capacity_case)


def test_nonmarginable_long_uses_its_own_capacity(capacity_case):
    context = capacity_case[1]
    context.asset.marginable = False
    context.account.non_marginable_buying_power = Decimal(1000)
    with pytest.raises(ValueError, match="buying power"):
        assess(capacity_case)


def test_current_quote_exposure_cannot_bypass_notional_ceiling(capacity_case):
    capacity_case[2].portfolio.max_notional_exposure = 1005
    with pytest.raises(ValueError, match="notional"):
        assess(capacity_case)


@pytest.mark.parametrize("clock", ["requested_at", "trade_timestamp", "quote"])
def test_age_covers_entire_acquisition_and_market_observations(capacity_case, clock):
    context = capacity_case[1]
    if clock == "quote":
        context.quote.timestamp -= timedelta(minutes=5)
    else:
        setattr(context, clock, getattr(context, clock) - timedelta(minutes=5))
    with pytest.raises(ValueError, match="stale|expired"):
        assess(capacity_case)


def observed_order(identity, *, symbol="IBM", side="buy", status="new", filled="0", parent=None, stop=None):
    return OrderObservation(
        order_id=identity,
        client_order_id=identity + "-client",
        symbol=symbol,
        side=side,
        status=status,
        quantity="10",
        filled_quantity=filled,
        average_fill_price="100" if filled != "0" else None,
        limit_price="100" if stop is None else None,
        stop_price=stop,
        order_type="limit" if stop is None else "stop",
        order_class="bracket",
        updated_at=datetime.now(UTC),
        parent_order_id=parent,
    )


def reservation(**updates):
    return {
        "id": 1,
        "contract": "IBM",
        "asset_class": "EQUITY",
        "status": "EXECUTED",
        "direction": "LONG",
        "quantity": 10,
        "entry_price": 100,
        "stop_loss": 95,
        "risk_dollars": 50,
        "notional_value": 1000,
        "broker_order_id": "entry",
        "take_profit": 110,
        **updates,
    }


def test_broker_reflected_pending_entry_is_not_charged_twice(capacity_case):
    context = capacity_case[1]
    context.account.buying_power = Decimal(1010)
    context.orders = (
        observed_order("entry"),
        observed_order("stop", side="sell", status="held", parent="entry", stop="95"),
        observed_order("profit", side="sell", status="held", parent="entry").model_copy(update={"limit_price": "110"}),
    )
    assert assess(capacity_case, [reservation()]).required_buying_power == Decimal(1010)


def test_later_fifo_reservations_keep_risk_but_do_not_preempt_head_funding(capacity_case):
    context = capacity_case[1]
    context.account.buying_power = Decimal(1010)
    result = assess(capacity_case, [reservation(status="SUBMITTING", broker_order_id=None)])
    assert result.aggregate_stop_risk == Decimal(100)


@pytest.mark.parametrize("case", ["external-holding", "unrelated-exits", "partial-fill", "missing-stop"])
def test_ambiguous_broker_exposure_refuses_new_entries(capacity_case, case):
    context = capacity_case[1]
    context.positions = (
        BrokerPosition(
            symbol="IBM", asset_class="EQUITY", direction="LONG", quantity=10, entry_price=100, current_price=110
        ),
    )
    rows = [reservation()]
    context.orders = (observed_order("entry", status="filled", filled="10"),)
    if case == "external-holding":
        rows = []
    elif case == "unrelated-exits":
        context.orders += (observed_order("exit1", side="sell"), observed_order("exit2", side="sell"))
    elif case == "partial-fill":
        context.orders = (observed_order("entry", status="partially_filled", filled="5"),)
    with pytest.raises(ValueError, match="exposure|protection|partial|unrelated"):
        assess(capacity_case, rows)


@pytest.fixture
def protected_capacity_case(capacity_case):
    context = capacity_case[1]
    context.positions = (
        BrokerPosition(
            symbol="IBM", asset_class="EQUITY", direction="LONG", quantity=10, entry_price=100, current_price=110
        ),
    )
    context.orders = (
        observed_order("entry", status="filled", filled="10"),
        observed_order("stop", side="sell", parent="entry", stop="95"),
    )
    return capacity_case


def test_exact_protected_position_retains_original_risk_and_mark_to_stop_giveback(protected_capacity_case):
    assert assess(protected_capacity_case, [reservation()]).aggregate_stop_risk == Decimal(200)


@pytest.mark.parametrize("fill_price", [None, "0", "99", "101"])
def test_current_inventory_requires_compatible_confirmed_entry_basis(protected_capacity_case, fill_price):
    context = protected_capacity_case[1]
    context.orders = (context.orders[0].model_copy(update={"average_fill_price": fill_price}), *context.orders[1:])
    with pytest.raises(ValueError, match="basis|ownership"):
        assess(protected_capacity_case, [reservation()])


@pytest.mark.parametrize("change", ["no-bracket", "changed-limit", "missing-stop", "changed-stop"])
def test_pending_entry_must_retain_exact_approved_bracket(capacity_case, change):
    context = capacity_case[1]
    root = observed_order("entry")
    stop = observed_order("stop", side="sell", status="held", parent="entry", stop="95")
    profit = observed_order("profit", side="sell", status="held", parent="entry").model_copy(
        update={"limit_price": "110"}
    )
    if change == "no-bracket":
        root = root.model_copy(update={"order_class": "simple"})
    elif change == "changed-limit":
        root = root.model_copy(update={"limit_price": "130"})
    elif change == "changed-stop":
        stop = stop.model_copy(update={"stop_price": "80"})
    context.orders = (root, profit) if change == "missing-stop" else (root, stop, profit)
    with pytest.raises(ValueError, match="pending|Pending|bracket|protection"):
        assess(capacity_case, [reservation(take_profit=110)])


@pytest.mark.parametrize("status,filled", [("filled", "10"), ("canceled", "2")])
def test_terminal_exit_execution_cannot_be_hidden_by_same_size_reopened_holding(capacity_case, status, filled):
    context = capacity_case[1]
    context.positions = (
        BrokerPosition(
            symbol="IBM", asset_class="EQUITY", direction="LONG", quantity=10, entry_price=100, current_price=110
        ),
    )
    context.orders = (
        observed_order("entry", status="filled", filled="10"),
        observed_order("stop", side="sell", parent="entry", stop="95"),
        observed_order("exit", side="sell", status=status, filled=filled, parent="entry"),
    )
    with pytest.raises(ValueError, match="partial|exit|ownership"):
        assess(capacity_case, [reservation()])


@pytest.mark.parametrize("status", ["pending_new", "accepted", "pending_replace"])
def test_nonworking_exit_does_not_establish_active_protection(capacity_case, status):
    context = capacity_case[1]
    context.positions = (
        BrokerPosition(
            symbol="IBM", asset_class="EQUITY", direction="LONG", quantity=10, entry_price=100, current_price=110
        ),
    )
    context.orders = (
        observed_order("entry", status="filled", filled="10"),
        observed_order("stop", side="sell", status=status, parent="entry", stop="95"),
    )
    with pytest.raises(ValueError, match="protection"):
        assess(capacity_case, [reservation()])


# Card #21 CRM incident (2026-09-24): XOM 46 @ 161.32 filled bracket entry (root
# limit 161.9), take-profit leg 172.48 status `new`, stop leg 156.61 status `held`.
# Alpaca holds a filled bracket's stop of an OCO exit pair; it is real protection.
def incident_orders(*, stop_status="held", tp_status="new", stop_quantity="46"):
    root = observed_order("root", symbol="XOM", status="filled", filled="46").model_copy(
        update={"quantity": "46", "limit_price": "161.9", "average_fill_price": "161.32"}
    )
    profit = observed_order("profit", symbol="XOM", side="sell", status=tp_status, parent="root").model_copy(
        update={"quantity": "46", "limit_price": "172.48"}
    )
    stop = observed_order(
        "stop", symbol="XOM", side="sell", status=stop_status, parent="root", stop="156.61"
    ).model_copy(update={"quantity": stop_quantity})
    return root, profit, stop


def incident_reservation(**updates):
    return reservation(
        contract="XOM",
        quantity=46,
        entry_price=161.32,
        stop_loss=156.61,
        take_profit=172.48,
        broker_order_id="root",
        **updates,
    )


@pytest.fixture
def incident_capacity_case(capacity_case):
    context = capacity_case[1]
    context.positions = (
        BrokerPosition(
            symbol="XOM", asset_class="EQUITY", direction="LONG", quantity=46, entry_price=161.32, current_price=164.14
        ),
    )
    context.orders = incident_orders()
    return capacity_case


# aggregate_stop_risk also includes the candidate SPY order's own risk (10 * |100-95| == 50).
INCIDENT_STOP_DISTANCE_RISK = Decimal(46) * (Decimal("164.14") - Decimal("156.61"))


def test_incident_held_bracket_stop_is_exact_protection(incident_capacity_case):
    result = assess(incident_capacity_case, [incident_reservation()])
    assert result.aggregate_stop_risk == INCIDENT_STOP_DISTANCE_RISK + Decimal(50)


def test_incident_new_stop_is_unchanged(incident_capacity_case):
    context = incident_capacity_case[1]
    context.orders = incident_orders(stop_status="new")
    result = assess(incident_capacity_case, [incident_reservation()])
    assert result.aggregate_stop_risk == INCIDENT_STOP_DISTANCE_RISK + Decimal(50)


def test_incident_canceled_stop_still_refuses_with_the_same_message(incident_capacity_case):
    context = incident_capacity_case[1]
    context.orders = incident_orders(stop_status="canceled")
    with pytest.raises(ValueError, match="Exact active stop protection is unavailable"):
        assess(incident_capacity_case, [incident_reservation()])


def test_incident_missing_stop_refuses_with_the_same_message(incident_capacity_case):
    context = incident_capacity_case[1]
    root, profit, _ = incident_orders()
    context.orders = (root, profit)
    with pytest.raises(ValueError, match="Exact active stop protection is unavailable"):
        assess(incident_capacity_case, [incident_reservation()])


def test_held_stop_with_terminal_take_profit_sibling_refuses(incident_capacity_case):
    # Round 1 fix, scenario A: root filled, TP canceled/expired, stop held. Alpaca only holds
    # the stop while its OCO take-profit sibling is live; a terminal TP sibling means the held
    # stop is an anomaly/transient, not confirmed exact protection, even though the leg loop
    # skips a terminal sibling entirely.
    context = incident_capacity_case[1]
    context.orders = incident_orders(tp_status="canceled")
    with pytest.raises(ValueError, match="Exact active stop protection is unavailable"):
        assess(incident_capacity_case, [incident_reservation()])


def test_held_stop_with_held_take_profit_sibling_refuses(incident_capacity_case):
    # Round 1 fix, scenario B: root filled, TP held, stop held. HELD is in WORKING_STATUSES, so
    # the trailing leg loop alone would accept this; the take-profit sibling must specifically
    # be NEW (live), not merely non-terminal.
    context = incident_capacity_case[1]
    context.orders = incident_orders(tp_status="held")
    with pytest.raises(ValueError, match="Exact active stop protection is unavailable"):
        assess(incident_capacity_case, [incident_reservation()])


def test_incident_new_and_held_stop_together_refuse(incident_capacity_case):
    # One NEW and one HELD stop leg: each individually satisfies PROTECTIVE_STOP_STATUSES, so
    # this only refuses if the code actually enforces "exactly one" rather than picking the
    # first/any protective match. A NEW-only filter would wrongly find just the NEW stop and
    # admit, silently ignoring the duplicate HELD stop anomaly.
    context = incident_capacity_case[1]
    root, profit, stop = incident_orders(stop_status="new")
    second_stop = stop.model_copy(update={"order_id": "stop2", "client_order_id": "stop2-client", "status": "held"})
    context.orders = (root, profit, stop, second_stop)
    with pytest.raises(ValueError, match="Exact active stop protection is unavailable"):
        assess(incident_capacity_case, [incident_reservation()])


def test_incident_held_stop_wrong_quantity_refuses(incident_capacity_case):
    context = incident_capacity_case[1]
    context.orders = incident_orders(stop_quantity="45")
    with pytest.raises(ValueError, match="Exact protection differs"):
        assess(incident_capacity_case, [incident_reservation()])


def test_incident_mark_at_or_below_held_stop_refuses(incident_capacity_case):
    context = incident_capacity_case[1]
    context.positions = (
        BrokerPosition(
            symbol="XOM", asset_class="EQUITY", direction="LONG", quantity=46, entry_price=161.32, current_price=156.61
        ),
    )
    with pytest.raises(ValueError, match="reconcile before new exposure"):
        assess(incident_capacity_case, [incident_reservation()])
