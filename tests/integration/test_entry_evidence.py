"""Entry admission evidence through real SDK serialization and loopback HTTP."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from agentic_trader.broker.base import BrokerEntryContext, OrderRequest, SimulatedEntryContext
from agentic_trader.broker.paper import PaperBroker
from agentic_trader.execution.capacity import _exposure


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1"])]


@pytest.fixture
def entry_request():
    return OrderRequest(
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        quantity=1,
        entry_price=99,
        stop_loss=95,
        take_profit=110,
    )


@pytest.mark.parametrize("feed", ["sip", "iex"])
async def test_entry_snapshot_retains_exact_account_asset_quote_and_bracket_evidence(alpaca_http, entry_request, feed):
    venue, broker = alpaca_http
    broker.config.alpaca_data_feed = feed
    venue.account["buying_power"] = "123456789.123456789"
    context = await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))
    assert isinstance(context, BrokerEntryContext) and not context.simulated
    assert context.account.account_id == "account"
    assert context.account.buying_power == Decimal("123456789.123456789")
    assert context.asset.symbol == "SPY" and context.asset.borrow_status == "easy_to_borrow"
    assert context.quote.ask_price == Decimal("99.1") and context.price == Decimal(99)
    assert context.quote.feed == feed
    assert context.trade_timestamp == context.quote.timestamp == venue.quote_time
    assert context.requested_at <= context.observed_at < context.session_closes_at
    assert len(context.positions) == 1
    orders = {order.order_id: order for order in context.orders}
    assert set(orders) == {venue.entry["id"], venue.stop["id"], venue.take_profit["id"]}
    assert orders[venue.entry["id"]].parent_order_id is None
    assert orders[venue.stop["id"]].parent_order_id == venue.entry["id"]
    assert orders[venue.take_profit["id"]].parent_order_id == venue.entry["id"]
    paths = [path for _, path, *_ in venue.calls]
    assert paths.count("/v2/account") == 2
    assert paths.index("/v2/account") < paths.index("/v2/orders") < len(paths) - 1
    root_reads = [query for _, path, query, _ in venue.calls if path == f"/v2/orders/{venue.entry['id']}"]
    assert root_reads == [{"nested": ["True"]}] * 2
    assert all(method == "GET" for method, *_ in venue.calls)
    for _, path, query, _ in venue.calls:
        if path in ("/v2/stocks/quotes/latest", "/v2/stocks/trades/latest"):
            assert query["feed"] == [feed]
    with pytest.raises(ValueError, match="frozen"):
        context.account.buying_power = Decimal(0)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("account", "buying_power", "NaN"),
        ("account", "buying_power", True),
        ("account", "cash", "Infinity"),
        ("account", "equity", None),
        ("account", "trading_blocked", "false"),
        ("account", "account_blocked", 0),
        ("account", "trade_suspended_by_user", None),
        ("account", "id", ""),
        ("asset", "id", "not-an-asset-id"),
        ("asset", "tradable", "true"),
        ("asset", "marginable", None),
        ("asset", "symbol", "QQQ"),
        ("asset", "class", "crypto"),
    ],
)
async def test_malformed_wire_evidence_fails_closed(alpaca_http, entry_request, section, field, value):
    venue, broker = alpaca_http
    getattr(venue, section)[field] = value
    with pytest.raises(ValueError):
        await broker.entry_market_context(entry_request)
    assert all(method == "GET" for method, *_ in venue.calls)


@pytest.mark.parametrize(
    "field", ["buying_power", "regt_buying_power", "non_marginable_buying_power", "shorting_enabled"]
)
async def test_missing_account_fields_are_not_defaulted(alpaca_http, entry_request, field):
    venue, broker = alpaca_http
    venue.account.pop(field)
    with pytest.raises(ValueError):
        await broker.entry_market_context(entry_request)


@pytest.mark.parametrize("borrow_status", [None, "hard_to_borrow", "new_unsupported_status"])
async def test_borrow_status_is_current_wire_evidence_not_legacy_boolean_fallback(
    alpaca_http, entry_request, borrow_status
):
    venue, broker = alpaca_http
    venue.asset.pop("easy_to_borrow", None)
    if borrow_status is None:
        venue.asset.pop("borrow_status")
    else:
        venue.asset["borrow_status"] = borrow_status
    context = await broker.entry_market_context(entry_request)
    assert context.asset.borrow_status == borrow_status


@pytest.mark.parametrize("field", ["id", "cash", "multiplier", "shorting_enabled"])
async def test_account_changes_during_book_capture_refuse_without_retry(alpaca_http, entry_request, field):
    venue, broker = alpaca_http
    account_reads = 0

    def override(method, path, query, body):
        nonlocal account_reads
        if path == "/v2/account":
            account_reads += 1
            if account_reads == 2:
                value = False if field == "shorting_enabled" else "changed" if field == "id" else "1"
                return 200, {**venue.account, field: value}
        return None

    venue.override = override
    with pytest.raises(ValueError, match="changed during"):
        await broker.entry_market_context(entry_request)
    assert account_reads == 2
    assert all(method == "GET" for method, *_ in venue.calls)


async def test_changing_market_marks_preserve_both_funding_observations(alpaca_http, entry_request):
    venue, broker = alpaca_http
    account_reads = 0

    def override(method, path, query, body):
        nonlocal account_reads
        if path == "/v2/account":
            account_reads += 1
            if account_reads == 2:
                return 200, {**venue.account, "equity": "9999", "buying_power": "19900"}
        return None

    venue.override = override
    context = await broker.entry_market_context(entry_request)
    assert context.account_before.equity == Decimal(10050)
    assert context.account.equity == Decimal(9999)
    assert context.account_before.buying_power == Decimal(20000)
    assert context.account.buying_power == Decimal(19900)


@pytest.mark.parametrize("field,value", [("qty", "9"), ("cost_basis", "999"), ("side", "short")])
async def test_inventory_changes_during_capture_refuse(alpaca_http, entry_request, field, value):
    venue, broker = alpaca_http
    position_reads = 0

    def override(method, path, query, body):
        nonlocal position_reads
        if path == "/v2/positions":
            position_reads += 1
            if position_reads == 2:
                changed = {**venue.position, field: value}
                if field == "side":
                    changed.update(qty="-10", cost_basis="-1000")
                return 200, [changed]
        return None

    venue.override = override
    with pytest.raises(ValueError, match="changed during"):
        await broker.entry_market_context(entry_request)
    assert position_reads == 2


@pytest.mark.parametrize("fault", ["different_root", "conflicting_duplicate", "wrong_parent", "saturated"])
async def test_ambiguous_order_identity_or_truncation_fails_closed(alpaca_http, entry_request, fault):
    venue, broker = alpaca_http

    def override(method, path, query, body):
        if path == f"/v2/orders/{venue.entry['id']}" and fault == "different_root":
            return 200, venue.take_profit
        if path == "/v2/orders":
            if fault == "saturated":
                return 200, [venue.take_profit] * 500
            if fault == "conflicting_duplicate":
                return 200, [venue.take_profit, {**venue.take_profit, "qty": "11"}]
            if fault == "wrong_parent":
                return 200, [{**venue.stop, "legs": [venue.take_profit]}]
        return None

    venue.override = override
    with pytest.raises(ValueError):
        await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))
    assert all(method == "GET" for method, *_ in venue.calls)


async def test_nested_open_orders_are_flattened_even_without_known_local_owner(alpaca_http, entry_request):
    venue, broker = alpaca_http
    parent = deepcopy(venue.entry)
    parent.update(status="accepted", filled_qty="0", filled_avg_price=None, filled_at=None)
    venue.override = lambda method, path, query, body: (200, [parent]) if path == "/v2/orders" else None
    context = await broker.entry_market_context(entry_request)
    assert len(context.orders) == 3
    assert sum(order.parent_order_id == parent["id"] for order in context.orders) == 2


async def test_quote_entitlement_failure_never_falls_back_to_trade_or_other_feed(alpaca_http, entry_request):
    venue, broker = alpaca_http
    venue.override = lambda method, path, query, body: (
        (403, {"message": "feed entitlement unavailable"}) if path == "/v2/stocks/quotes/latest" else None
    )
    with pytest.raises(Exception, match="entitlement unavailable"):
        await broker.entry_market_context(entry_request)
    quotes = [query for _, path, query, _ in venue.calls if path == "/v2/stocks/quotes/latest"]
    assert len(quotes) == 1
    assert all(method == "GET" for method, *_ in venue.calls)


@pytest.mark.parametrize("bid,ask", [(0, 99.1), (98.9, 0), (99.2, 99.1), (float("nan"), 99.1)])
async def test_invalid_quotes_are_not_replaced_with_last_trade(alpaca_http, entry_request, bid, ask):
    venue, broker = alpaca_http
    venue.bid_price, venue.ask_price = bid, ask
    with pytest.raises(ValueError):
        await broker.entry_market_context(entry_request)


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_missing_or_changed_book_cannot_be_hidden_by_stable_account(alpaca_http, entry_request, kind):
    venue, broker = alpaca_http
    reads = 0

    def override(method, path, query, body):
        nonlocal reads
        if path == f"/v2/{kind}":
            reads += 1
            if reads == 2:
                return 200, []
        return None

    venue.override = override
    with pytest.raises(ValueError, match="changed during"):
        await broker.entry_market_context(entry_request)
    assert reads == 2


async def test_inventory_market_marks_may_move_without_masking_inventory_identity(alpaca_http, entry_request):
    venue, broker = alpaca_http
    reads = 0

    def override(method, path, query, body):
        nonlocal reads
        if path == "/v2/positions":
            reads += 1
            if reads == 2:
                return 200, [{**venue.position, "current_price": "106", "unrealized_pl": "60"}]
        return None

    venue.override = override
    context = await broker.entry_market_context(entry_request)
    assert context.positions[0].current_price == 106
    assert context.positions[0].quantity == 10


@pytest.mark.parametrize(
    "field,value", [("qty", None), ("filled_qty", None), ("filled_qty", "11"), ("limit_price", "NaN")]
)
async def test_incomplete_order_money_is_not_coerced_to_zero(alpaca_http, entry_request, field, value):
    venue, broker = alpaca_http
    venue.take_profit[field] = value
    with pytest.raises(ValueError):
        await broker.entry_market_context(entry_request)


async def test_simulated_entry_has_explicit_empty_evidence(app_config, entry_request):
    context = await PaperBroker(app_config, data_fetcher=MagicMock()).entry_market_context(
        entry_request, entry_order_ids=()
    )
    assert isinstance(context, SimulatedEntryContext)
    assert context.simulated and context.positions == context.orders == ()


def _shift_timestamps(order: dict, delta: timedelta) -> dict:
    shifted = dict(order)
    for key in ("updated_at", "submitted_at"):
        shifted[key] = (datetime.fromisoformat(order[key]) + delta).isoformat()
    return shifted


async def test_duplicate_leg_with_sub_millisecond_timestamp_skew_is_the_same_order(alpaca_http, entry_request):
    # Observed on the paper desk (September 23): the open-orders list and the nested
    # by-ID root serialize the same bracket leg's timestamps one microsecond apart.
    venue, broker = alpaca_http
    skewed = _shift_timestamps(venue.take_profit, -timedelta(microseconds=1))
    venue.override = lambda method, path, query, body: (200, [skewed]) if path == "/v2/orders" else None
    context = await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))
    orders = {order.order_id: order for order in context.orders}
    assert orders[venue.take_profit["id"]].parent_order_id == venue.entry["id"]


async def test_duplicate_leg_with_material_timestamp_difference_still_fails_closed(alpaca_http, entry_request):
    venue, broker = alpaca_http
    skewed = _shift_timestamps(venue.take_profit, -timedelta(milliseconds=2))
    venue.override = lambda method, path, query, body: (200, [skewed]) if path == "/v2/orders" else None
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))


async def test_admission_evidence_follows_a_replaced_stop_to_its_current_held_leg(alpaca_http, entry_request):
    # Round 1 fix: after a trailing-stop ratchet (frequent), the root's nested legs still show
    # the stale `replaced` stop; the live replacement is often `held` and absent from the OPEN
    # query. Admission evidence must follow `replaced_by` (the same bounded, verified chain
    # `_current_order`/`_expand_close_orders` already use for closes) or capacity wrongly finds
    # zero stops and refuses every new entry again.
    venue, broker = alpaca_http
    venue.replacement_status = "held"
    # 98 tightens the sell-stop protecting the long SPY position (original stop 95, mark 105);
    # a looser price is a documented no-op in modify_order_stop and would never PATCH/replace.
    result = await broker.modify_order_stop(order_id=venue.entry["id"], symbol="SPY", new_stop_price=98)
    assert result.success
    new_stop_id = result.order_id
    assert venue.stop["status"] == "replaced" and venue.stop["replaced_by"] == new_stop_id
    assert venue.orders[new_stop_id]["status"] == "held"
    open_orders = [o["id"] for o in venue.dispatch("GET", "/v2/orders", {}, None)[1]]
    assert new_stop_id not in open_orders and venue.stop["id"] not in open_orders

    context = await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))
    orders = {order.order_id: order for order in context.orders}
    assert new_stop_id in orders
    new_stop = orders[new_stop_id]
    assert new_stop.status == "held" and new_stop.parent_order_id == venue.entry["id"]
    assert new_stop.side == "sell" and Decimal(new_stop.stop_price) == Decimal(98)
    # the superseded stop is retained too, as terminal evidence, not silently discarded.
    assert orders[venue.stop["id"]].status == "replaced"

    reservation = {
        "contract": "SPY",
        "asset_class": "EQUITY",
        "status": "EXECUTED",
        "direction": "LONG",
        "quantity": 10,
        "entry_price": 100,
        "stop_loss": 98,
        "risk_dollars": 50,
        "notional_value": 1000,
        "broker_order_id": venue.entry["id"],
        "take_profit": 110,
    }
    # Capacity admits: the existing holding's protection resolves to the new held stop, not a
    # refusal from the stale replaced leg or a missing-stop error. Exercised through `_exposure`
    # directly (the exact function `capacity.py` uses for this check) rather than the full
    # `assess_entry_capacity` pipeline, whose separate same-symbol netting policy would refuse a
    # second SPY entry regardless of protection evidence.
    exposure = _exposure([reservation], context)
    assert len(exposure) == 1
    assert exposure[0]["risk_dollars"] == Decimal(10) * (Decimal(105) - Decimal(98))


async def test_admission_evidence_follows_a_bounded_multi_hop_replacement_chain(
    alpaca_http, entry_request, broker_order_payload
):
    # Two ratchets: stop -> mid (replaced) -> final (held). `_current_order` resolves a leg
    # straight to its live head in one pass (mirroring `_expand_close_orders`), so only the raw
    # stale leg from the nested root (`stop`) and the fully-resolved current leg (`final`) are
    # retained as evidence; the intermediate `mid` hop is chain-following detail, not evidence.
    venue, broker = alpaca_http
    mid = broker_order_payload(type="stop", status="replaced", stop_price="96", replaces=venue.stop["id"])
    final = broker_order_payload(type="stop", status="held", stop_price="97", replaces=mid["id"])
    venue.stop["status"], venue.stop["replaced_by"] = "replaced", mid["id"]
    mid["replaced_by"] = final["id"]
    venue.orders[mid["id"]] = mid
    venue.orders[final["id"]] = final

    context = await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))
    orders = {order.order_id: order for order in context.orders}
    assert {venue.stop["id"], final["id"]} <= set(orders)
    assert orders[final["id"]].status == "held" and orders[final["id"]].parent_order_id == venue.entry["id"]
    assert (
        orders[venue.stop["id"]].status == "replaced" and orders[venue.stop["id"]].parent_order_id == venue.entry["id"]
    )

    # Bounded: a cyclic replacement chain fails closed instead of looping forever.
    final["replaced_by"] = venue.stop["id"]
    with pytest.raises(ValueError, match="[Cc]yclic|replacement chain"):
        await broker.entry_market_context(entry_request, entry_order_ids=(venue.entry["id"],))
