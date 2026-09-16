"""Offline order lifecycle rehearsal: fake SDK, real coordinator/database/handlers."""

import asyncio
import time
from copy import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from alpaca.common.exceptions import APIError
from click.testing import CliRunner

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.cli.main import cli
from agentic_trader.constants import CloseRequestStatus, SignalStatus, SystemStateKey
from agentic_trader.execution.closing import PositionCloseService
from agentic_trader.notifier.telegram_bot import TelegramNotifier


class FakeTradingClient:
    def __init__(self):
        self.positions = {}
        self.orders = {}
        self.actions = []
        self.market_open = True
        self.cancel_pending_reads = 1
        self.never_cancel = False
        self.fill_during_cancel = False
        self.lose_submit_response = False
        self.fill_close = False
        self.read_delay = 0
        self.omit_held_stops = False
        self.omit_history = False
        self.submissions = []
        self.cancelled = []
        self.add_position("IWM", "short", 105, 285.4)
        self.add_position("AMD", "long", 45, 502.47)

    def add_position(self, symbol, side, quantity, entry):
        self.positions[symbol] = SimpleNamespace(
            symbol=symbol,
            side=side,
            qty=str(quantity if side == "long" else -quantity),
            qty_available="0",
            avg_entry_price=str(entry),
            current_price=str(entry),
            unrealized_pl="0",
            asset_class="us_equity",
        )
        exit_side = "sell" if side == "long" else "buy"
        legs = [
            {
                "id": f"{symbol}-{kind}",
                "symbol": symbol,
                "side": exit_side,
                "status": "new",
                "type": kind,
                "order_type": kind,
                "filled_qty": "0",
            }
            for kind in ("limit", "stop")
        ]
        self.orders.update({leg["id"]: leg for leg in legs})
        self.orders[f"{symbol}-entry"] = {
            "id": f"{symbol}-entry",
            "symbol": symbol,
            "side": "buy" if side == "long" else "sell",
            "status": "filled",
            "filled_qty": str(quantity),
            "filled_avg_price": str(entry),
            "filled_at": datetime.now(UTC) - timedelta(hours=1),
            "legs": legs,
        }

    def get_all_positions(self):
        return list(self.positions.values())

    def get_open_position(self, symbol):
        time.sleep(self.read_delay)
        return self.positions[symbol]

    def get_clock(self):
        return SimpleNamespace(is_open=self.market_open, next_open="next session")

    def get_orders(self, request):
        if request.status.value == "all":
            return (
                []
                if self.omit_history
                else [o for o in self.orders.values() if o["symbol"] in request.symbols and o.get("legs")]
            )
        return [
            o
            for o in self.orders.values()
            if o["symbol"] in request.symbols
            and o["status"] in ("new", "held", "pending_cancel", "accepted", "partially_filled")
            and not (self.omit_held_stops and o["status"] == "held")
        ]

    def get_order_by_id(self, order_id, *args):
        order = self.orders[order_id]
        if order["status"] == "pending_cancel" and not self.never_cancel:
            order["reads"] -= 1
            if order["reads"] <= 0:
                order["status"] = "filled" if self.fill_during_cancel else "canceled"
                self.actions.append(("terminal", order_id))
                symbol = order["symbol"]
                self.positions[symbol].qty_available = str(abs(float(self.positions[symbol].qty)))
                if self.fill_during_cancel:
                    self.positions[symbol].qty = "1"
        return order

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)
        self.actions.append(("cancel", order_id))
        self.orders[order_id].update(status="pending_cancel", reads=self.cancel_pending_reads)

    def submit_order(self, request):
        self.actions.append(("submit", request.symbol))
        assert all(
            o["status"] not in ("new", "pending_cancel", "held")
            for o in self.orders.values()
            if o["symbol"] == request.symbol
        )
        self.submissions.append(request)
        order = {
            "id": f"exit-{request.symbol}",
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side.value,
            "type": "market",
            "order_type": "market",
            "status": "accepted",
            "filled_qty": "0",
        }
        if self.fill_close:
            order.update(
                status="filled", filled_qty=str(request.qty), filled_avg_price="300", filled_at=datetime.now(UTC)
            )
            self.positions.pop(request.symbol)
        self.orders[order["id"]] = order
        if self.lose_submit_response:
            raise TimeoutError("lost acknowledgement")
        return order

    def get_order_by_client_id(self, request_id):
        for order in self.orders.values():
            if order.get("client_order_id") == request_id:
                return order
        raise APIError('{"message":"not found"}', SimpleNamespace(response=SimpleNamespace(status_code=404)))


@pytest.fixture
def desk(app_config, temp_db):
    client = FakeTradingClient()
    app_config.execution.close_cancel_timeout_seconds = 0.5
    app_config.execution.close_cancel_poll_seconds = 0.001
    app_config.copilot_chat_enabled = False
    broker = AlpacaBroker(app_config, client=client)
    broker._connected = True
    notifier = MagicMock(send_exit_alert=AsyncMock(return_value=True))
    copilot = TradingCopilot(app_config, db=temp_db, broker=broker, notifier=notifier)
    return copilot, client


async def seed(copilot, symbol="IWM", direction="SHORT", quantity=105, entry=285.4):
    sid = await copilot.db.record_signal(
        contract=symbol,
        direction=direction,
        quantity=quantity,
        entry_price=entry,
        stop_loss=290,
        take_profit=280,
        risk_dollars=200,
        strategy="offline-close-rehearsal",
        status=SignalStatus.EXECUTED,
    )
    await copilot.db.update_signal_execution(sid, f"{symbol}-entry", executed_at=datetime.now(UTC) - timedelta(hours=1))
    return sid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "symbol,direction,quantity,entry,expected_side",
    [
        ("IWM", "SHORT", 105, 285.4, "buy"),
        ("AMD", "LONG", 45, 502.47, "sell"),
    ],
)
async def test_close_waits_for_cancellation_and_uses_actual_fill(
    desk, symbol, direction, quantity, entry, expected_side
):
    copilot, client = desk
    sid = await seed(copilot, symbol, direction, quantity, entry)
    client.cancel_pending_reads = 3
    client.fill_close = True
    await copilot.close_position_manual(sid, exit_price=1)
    row = await copilot.db.get_signal_by_id(sid)
    assert row["exit_price"] == 300
    assert row["realized_pnl"] == pytest.approx((300 - entry) * quantity * (1 if direction == "LONG" else -1))
    assert client.submissions[0].side.value == expected_side
    assert all(key.startswith(symbol) for key in client.cancelled)
    assert client.actions[-1] == ("submit", symbol)
    assert row["broker_exit_order_id"] == f"exit-{symbol}"
    await copilot.outbox.drain()
    copilot.notifier.send_exit_alert.assert_awaited_once()
    assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) is None
    events = await copilot.db.get_audit_events(sid)
    assert any(e["payload"].get("phase") == "cancellations_confirmed" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("market_closed", "market is closed"),
        ("cancel_timeout", "not confirmed"),
        ("filled_during_cancel", "filled during cancellation"),
        ("working_market", "already working"),
        ("entry_mismatch", "must be fully filled"),
        ("quantity_mismatch", "differs from the broker"),
    ],
)
async def test_no_close_when_preconditions_or_cancellation_fail(desk, scenario, expected):
    copilot, client = desk
    sid = await seed(copilot)
    if scenario == "market_closed":
        client.market_open = False
    elif scenario == "cancel_timeout":
        copilot.config.execution.close_cancel_timeout_seconds = 0.03
        client.never_cancel = True
    elif scenario == "filled_during_cancel":
        client.fill_during_cancel = True
    elif scenario == "working_market":
        client.orders["IWM-limit"]["type"] = "market"
    elif scenario == "entry_mismatch":
        client.orders["IWM-entry"]["status"] = "partially_filled"
    else:
        client.positions["IWM"].qty = "-104"
    response = await copilot.close_position_manual(sid)
    assert expected in response
    assert not client.submissions
    if scenario in ("market_closed", "working_market", "entry_mismatch", "quantity_mismatch"):
        assert not client.cancelled
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.EXECUTED
    copilot.notifier.send_exit_alert.assert_not_called()


@pytest.mark.asyncio
async def test_lost_ack_recovers_exact_order_after_new_coordinator_without_replay(desk):
    copilot, client = desk
    sid = await seed(copilot)
    client.lose_submit_response = True
    await copilot.close_position_manual(sid)
    requests = await copilot.db.active_close_requests()
    assert requests[0]["status"] == CloseRequestStatus.UNKNOWN
    new_service = PositionCloseService(copilot.broker, copilot.db)
    await new_service.recover()
    assert (await copilot.db.get_signal_by_id(sid))["broker_exit_order_id"] == "exit-IWM"
    await new_service.close_signal(sid)
    assert len(client.submissions) == 1
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.EXECUTED


@pytest.mark.asyncio
async def test_two_coordinators_claim_before_cancelling(desk):
    copilot, client = desk
    sid = await seed(copilot)
    second = PositionCloseService(copilot.broker, copilot.db)
    await asyncio.gather(copilot.close_service.close_signal(sid), second.close_signal(sid))
    assert len(client.submissions) == 1
    assert len(client.cancelled) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("halted", [None, "true", "false"])
async def test_flatten_closes_broker_only_positions_and_preserves_halt(desk, halted):
    copilot, client = desk
    await seed(copilot)
    if halted:
        await copilot.db.set_state(SystemStateKey.TRADING_HALTED, halted)
    response = await copilot.flatten_positions(confirm=True)
    assert {r.symbol for r in client.submissions} == {"IWM", "AMD"}
    assert "halt state unchanged" in response
    assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) == halted
    assert (await copilot.db.get_closed_positions_stats())["total_trades"] == 0


@pytest.mark.asyncio
async def test_flatten_partial_failure_continues_and_reports_each_symbol(desk):
    copilot, client = desk
    client.orders["IWM-limit"]["type"] = "market"
    response = await copilot.flatten_positions(confirm=True)
    assert "already working" in response and "AMD" in response
    assert [r.symbol for r in client.submissions] == ["AMD"]


@pytest.mark.asyncio
async def test_preview_is_read_only(desk):
    copilot, client = desk
    before = await copilot.db.get_audit_events()
    response = await copilot.flatten_positions()
    assert "[DRY RUN]" in response and "IWM" in response and "AMD" in response
    assert not client.cancelled and not client.submissions
    assert await copilot.db.get_audit_events() == before
    assert await copilot.db.active_close_requests() == []


@pytest.mark.asyncio
async def test_slow_sdk_does_not_block_event_loop(desk):
    copilot, client = desk
    client.read_delay = 0.06
    sid = await seed(copilot)
    closing = asyncio.create_task(copilot.close_service.close_signal(sid))
    ticks = 0
    while not closing.done():
        await asyncio.sleep(0.005)
        ticks += 1
    await closing
    assert ticks >= 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args,authorized,confirmation",
    [
        ([], True, False),
        (["dry-run"], True, False),
        (["confirm"], True, True),
        (["yes"], True, None),
        (["confirm"], False, None),
    ],
)
async def test_telegram_flatten_routes_only_explicit_authorized_confirmation(args, authorized, confirmation):
    handler = AsyncMock(return_value="[DRY RUN] Test response")
    notifier = TelegramNotifier(None, "test-chat", flatten_handler=handler)
    update = MagicMock(effective_chat=SimpleNamespace(id="test-chat" if authorized else "other"))
    update.message.reply_text = AsyncMock()
    await notifier.handle_flatten_command(update, SimpleNamespace(args=args))
    if confirmation is None:
        handler.assert_not_called()
    else:
        handler.assert_awaited_once_with(confirmation)


@pytest.mark.parametrize(
    "args,confirm", [([], False), (["--dry-run"], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)]
)
def test_cli_flatten_explicit_confirmation(monkeypatch, args, confirm):
    copilot = MagicMock(flatten_positions=AsyncMock(return_value="Preview"))
    copilot.broker.connect = AsyncMock(return_value=True)
    monkeypatch.setattr("agentic_trader.cli.commands.trade.get_copilot_and_config", lambda: (copilot, None))
    result = CliRunner().invoke(cli, ["flatten", *args])
    assert result.exit_code == 0, result.output
    copilot.flatten_positions.assert_awaited_once_with(confirm=confirm)


def test_cli_close_does_not_require_simulated_price(monkeypatch):
    copilot = MagicMock(close_position_manual=AsyncMock(return_value="Awaiting fill"))
    copilot.broker.connect = AsyncMock(return_value=True)
    monkeypatch.setattr("agentic_trader.cli.commands.trade.get_copilot_and_config", lambda: (copilot, None))
    result = CliRunner().invoke(cli, ["close", "5"])
    assert result.exit_code == 0, result.output
    copilot.close_position_manual.assert_awaited_once_with(5, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filled_qty,expected_status", [("0", CloseRequestStatus.FAILED), ("1", CloseRequestStatus.UNKNOWN)]
)
async def test_terminal_unfilled_exit_can_retry_but_partial_exit_stays_exclusive(desk, filled_qty, expected_status):
    copilot, client = desk
    sid = await seed(copilot)
    await copilot.close_position_manual(sid)
    client.orders["exit-IWM"].update(status="canceled", filled_qty=filled_qty)
    await copilot.close_service.recover()
    active = await copilot.db.active_close_requests()
    if expected_status == CloseRequestStatus.FAILED:
        assert active == []
        assert (await copilot.db.get_signal_by_id(sid))["broker_exit_order_id"] is None
    else:
        assert active[0]["status"] == expected_status
        await copilot.close_position_manual(sid)
        assert len(client.submissions) == 1
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.EXECUTED


@pytest.mark.asyncio
async def test_empty_and_ambiguous_flatten_are_explicit(desk):
    copilot, client = desk
    await seed(copilot)
    await seed(copilot)
    result = await copilot.flatten_positions(confirm=True)
    assert "Tracking mismatch" in result
    assert [r.symbol for r in client.submissions] == ["AMD"]
    client.positions.clear()
    assert "No open broker positions" in await copilot.flatten_positions(confirm=True)


@pytest.mark.asyncio
async def test_menu_registers_flatten_in_all_three_scopes():
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock(return_value=True)
    app.bot.set_chat_menu_button = AsyncMock(return_value=True)
    notifier = TelegramNotifier("test-token", "123", application=app)
    assert await notifier.setup_bot_commands()
    calls = app.bot.set_my_commands.call_args_list
    assert len(calls) == 3
    assert all("flatten" in {command.command for command in call.args[0]} for call in calls)
    registered = [call.args[0] for call in app.add_handler.call_args_list]
    assert any("flatten" in getattr(handler, "commands", ()) for handler in registered)


@pytest.mark.asyncio
async def test_waits_for_broker_reserved_quantity_to_release(desk):
    copilot, client = desk
    sid = await seed(copilot)
    read_position = client.get_open_position
    reads = 0

    def delayed_release(symbol):
        nonlocal reads
        position = copy(read_position(symbol))
        reads += 1
        if reads < 4:
            position.qty_available = "0"
        return position

    client.get_open_position = delayed_release
    response = await copilot.close_position_manual(sid)
    assert "awaiting" in response
    assert reads >= 4
    assert len(client.submissions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tracked", [True, False])
async def test_held_stop_omitted_by_open_query_is_resolved_and_confirmed(desk, tracked):
    copilot, client = desk
    sid = await seed(copilot) if tracked else None
    client.omit_held_stops = True
    client.orders["IWM-limit"]["order_class"] = "bracket"
    client.orders["IWM-stop"].update(status="held", order_class="bracket")
    if tracked:
        await copilot.close_position_manual(sid)
    else:
        await copilot.flatten_positions(confirm=True)
    assert "IWM-stop" in client.cancelled
    assert client.orders["IWM-stop"]["status"] == "canceled"
    assert any(request.symbol == "IWM" for request in client.submissions)


@pytest.mark.asyncio
async def test_unresolved_broker_only_bracket_keeps_protection(desk):
    copilot, client = desk
    client.omit_held_stops = client.omit_history = True
    client.orders["IWM-limit"]["order_class"] = "bracket"
    client.orders["IWM-stop"].update(status="held", order_class="bracket")
    response = await copilot.flatten_positions(confirm=True)
    assert "Cannot identify the exact bracket" in response
    assert not any(order_id.startswith("IWM") for order_id in client.cancelled)
    assert [request.symbol for request in client.submissions] == ["AMD"]


@pytest.mark.asyncio
async def test_empty_open_list_does_not_hide_tracked_held_stop(desk):
    copilot, client = desk
    sid = await seed(copilot)
    client.omit_held_stops = True
    client.orders["IWM-limit"]["status"] = "canceled"
    client.orders["IWM-stop"].update(status="held", order_class="bracket")
    await copilot.close_position_manual(sid)
    assert client.cancelled == ["IWM-stop"]
    assert len(client.submissions) == 1


@pytest.mark.asyncio
async def test_emergency_close_can_queue_after_hours_while_manual_close_preserves_protection(desk):
    copilot, client = desk
    sid = await seed(copilot)
    client.market_open = False
    assert "market is closed" in await copilot.close_position_manual(sid)
    assert not client.cancelled
    copilot.broker.cancel_all_orders = AsyncMock(return_value=0)
    copilot.notifier.send_message = AsyncMock(return_value=True)
    report = await copilot.emergency_panic_halt("Offline emergency queue rehearsal")
    assert report.is_halted and report.liquidated_positions_count == 0
    assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) == "true"
    assert len(client.submissions) == 1
