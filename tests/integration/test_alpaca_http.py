"""Contract and lifecycle tests through alpaca-py, loopback HTTP and real storage."""

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.asyncio.server import serve

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import AuditEventType, SignalStatus, SystemStateKey
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1"])]


@pytest.fixture
async def desk(alpaca_http, temp_db, app_config, request):
    db = (
        SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
        if getattr(request, "param", "sqlite") == "postgres"
        else temp_db
    )
    venue, broker = alpaca_http
    notifier = MagicMock(
        send_exit_alert=AsyncMock(return_value=7),
        send_trailing_stop_alert=AsyncMock(return_value=8),
        send_message=AsyncMock(return_value=9),
    )
    # These are admission/broker contract tests of a legacy tap; tap-time card freshness
    # has its own coverage in tests/agent/test_card_freshness_tap.py.
    app_config.execution.card_freshness.enabled = False
    copilot = TradingCopilot(app_config, db=db, broker=broker, notifier=notifier)
    copilot.entry_service.macro_check = AsyncMock(return_value=None)
    sid = await db.record_signal(
        contract="SPY",
        direction="LONG",
        quantity=10,
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        risk_dollars=40,
        strategy="integration",
        asset_class="EQUITY",
        status=SignalStatus.PENDING,
        raw_response="Original thesis",
    )
    yield copilot, venue, sid
    await db.engine.dispose()


async def track_existing(copilot, venue, sid):
    await copilot.db.update_signal_execution(sid, venue.entry["id"], status=SignalStatus.EXECUTED)
    await copilot.sync_entry_executions(await copilot.db.get_active_positions())


@pytest.mark.parametrize("desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_entry_partial_full_close_and_performance_use_actual_fills(desk):
    copilot, venue, sid = desk
    position = venue.position
    venue.position = None
    venue.take_profit["status"] = "held"
    success = (await copilot.execute_signal_by_id(sid)).ok
    assert success
    venue.position = position
    venue.take_profit["status"] = "new"
    post = next(body for method, _, _, body in venue.calls if method == "POST")
    assert post["client_order_id"].startswith("entry-")
    assert post["type"] == "limit" and post["order_class"] == "bracket"
    assert post["take_profit"] == {"limit_price": 110.0}
    assert post["stop_loss"] == {"stop_price": 95.0}
    assert (await copilot.db.get_signal_by_id(sid))["executed_at"] is None

    venue.entry.update(
        status="partially_filled", filled_qty="3", filled_avg_price="100", filled_at=datetime.now(UTC).isoformat()
    )
    assert await copilot.monitor_positions() == 0
    row = await copilot.db.get_signal_by_id(sid)
    assert row["quantity"] == 10 and row["executed_at"] is None
    venue.entry.update(status="filled", filled_qty="10")
    await copilot.monitor_positions()
    row = await copilot.db.get_signal_by_id(sid)
    assert row["entry_price"] == 100 and row["executed_at"]
    report = await copilot.get_positions_report()
    assert report.positions[0].unrealized_pnl == 50

    preview = await copilot.flatten_positions()
    assert "DRY RUN" in preview
    assert not any(method == "DELETE" for method, *_ in venue.calls)
    await copilot.close_position_manual(sid, exit_price=1)
    row = await copilot.db.get_signal_by_id(sid)
    assert row["exit_price"] == 105 and row["realized_pnl"] == 50
    assert (await copilot.db.get_closed_positions_stats())["total_pnl"] == 50
    assert [path for method, path, *_ in venue.calls if method == "DELETE"] == [
        f"/v2/orders/{venue.take_profit['id']}",
        f"/v2/orders/{venue.stop['id']}",
    ]
    assert await copilot.monitor_positions() == 0
    await copilot.outbox.drain()
    copilot.notifier.send_exit_alert.assert_awaited_once()
    audits = await copilot.db.get_audit_events()
    assert {
        AuditEventType.CLOSE_REQUEST,
        AuditEventType.CLOSE_BROKER_STEP,
        AuditEventType.POSITION_CLOSED,
        AuditEventType.EXIT_NOTIFICATION,
    } <= {event["event_type"] for event in audits}


@pytest.mark.parametrize("replacement_status", ["new", "pending_replace", "rejected"])
async def test_stop_confirmation_preserves_thesis_and_initial_risk(desk, replacement_status):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    copilot.config.trailing_stop.enabled = True
    copilot.config.trailing_stop.breakeven_trigger_r = None
    copilot.data_fetcher = MagicMock(fetch_latest_price=MagicMock(return_value=120))
    venue.replacement_status = replacement_status
    count = await copilot.manage_trailing_stops(await copilot.db.get_active_positions())
    row = await copilot.db.get_signal_by_id(sid)
    assert count == (1 if replacement_status == "new" else 0)
    assert row["stop_loss"] == (112.5 if replacement_status == "new" else 95)
    assert row["risk_dollars"] == 50 and row["raw_response"] == "Original thesis"
    assert [path for method, path, *_ in venue.calls if method == "PATCH"] == [f"/v2/orders/{venue.stop['id']}"]
    await copilot.outbox.drain()
    assert copilot.notifier.send_trailing_stop_alert.await_count == count
    events = await copilot.db.get_audit_events(signal_id=sid)
    assert any(e["event_type"] == AuditEventType.STOP_REPLACEMENT and e["payload"]["phase"] == "result" for e in events)


async def test_stop_lookup_failure_never_patches_or_searches_by_symbol(alpaca_http):
    venue, broker = alpaca_http
    venue.override = lambda method, path, query, body: (503, {"message": "unavailable"})
    result = await broker.modify_order_stop(order_id=venue.entry["id"], symbol="SPY", new_stop_price=98)
    assert not result.success
    assert [method for method, *_ in venue.calls] == ["GET"]


@pytest.mark.parametrize("failure", ["gateway-timeout", "socket-timeout"])
async def test_ambiguous_entry_never_replays_and_halts_new_risk(desk, failure):
    copilot, venue, sid = desk

    def fail(method, path, query, body):
        if method == "POST":
            if failure == "socket-timeout":
                # Outlast the SDK socket deadline: the submission reached the venue and the
                # acknowledgement was lost.
                time.sleep(copilot.broker.client.request_timeout + 0.5)
            return 504, {"code": 50410000, "message": "unknown outcome"}

    venue.override = fail
    venue.position = None
    venue.take_profit["status"] = "held"
    reply = await copilot.execute_signal_by_id(sid)
    success, message = reply.ok, reply.text
    assert not success and "No automatic resubmission" in message
    assert len([1 for method, *_ in venue.calls if method == "POST"]) == 1
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.SUBMITTING
    assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) == "true"
    assert any(
        event["event_type"] == AuditEventType.ENTRY_SUBMISSION_UNKNOWN for event in await copilot.db.get_audit_events()
    )
    await copilot.execute_signal_by_id(sid)
    assert len([1 for method, *_ in venue.calls if method == "POST"]) == 1


async def test_bulk_cancel_counts_individual_acceptances(alpaca_http):
    _, broker = alpaca_http
    assert await broker.cancel_all_orders() == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"order_type": "STOP"},
        {"entry_price": None},
        {"side": "SELL"},
        {"time_in_force": "IOC", "stop_loss": 95, "take_profit": 110},
        {"symbol": "BTC/USD", "asset_class": "CRYPTO", "stop_loss": 95, "take_profit": 110},
    ],
)
async def test_unsupported_entry_contracts_do_not_silently_change_order_type(alpaca_http, changes):
    venue, broker = alpaca_http
    result = await broker.submit_entry_order(OrderRequest(**{"symbol": "SPY", "entry_price": 100, **changes}))
    assert not result.success and not venue.calls


async def test_sdk_calls_do_not_block_event_loop(alpaca_http):
    venue, broker = alpaca_http
    venue.override = lambda *args: time.sleep(0.05) or (200, [venue.position])
    task = asyncio.create_task(broker.get_positions())
    await asyncio.sleep(0.01)
    assert not task.done()
    assert len(await task) == 1


async def test_replaced_stop_fill_reconciles_by_exact_chain_once(desk):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    result = await copilot.broker.modify_order_stop(order_id=venue.entry["id"], symbol="SPY", new_stop_price=102)
    assert result.success
    replacement = venue.orders[result.order_id]
    replacement.update(
        status="filled", filled_qty="10", filled_avg_price="101.5", filled_at=datetime.now(UTC).isoformat()
    )
    venue.position = None
    await asyncio.gather(copilot.monitor_positions(), copilot.monitor_positions())
    row = await copilot.db.get_signal_by_id(sid)
    assert row["realized_pnl"] == 15 and row["broker_exit_order_id"] == result.order_id
    assert row["exit_reason"] == "STOP_LOSS"
    await copilot.outbox.drain()
    copilot.notifier.send_exit_alert.assert_awaited_once()


async def test_close_after_stop_replacement_cancels_current_leg(desk):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    result = await copilot.broker.modify_order_stop(order_id=venue.entry["id"], symbol="SPY", new_stop_price=102)
    assert result.success
    await copilot.close_position_manual(sid)
    row = await copilot.db.get_signal_by_id(sid)
    assert row["realized_pnl"] == 50
    deleted = [path for method, path, *_ in venue.calls if method == "DELETE"]
    assert f"/v2/orders/{result.order_id}" in deleted
    assert f"/v2/orders/{venue.stop['id']}" not in deleted


@pytest.mark.parametrize("operation", ["flatten", "panic"])
async def test_closed_session_preserves_protection_except_explicit_emergency(desk, operation):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    venue.market_open = False
    venue.close_status = "accepted"
    if operation == "panic":
        # Return no bulk cancellations here: per-position workflow must still verify legs.
        venue.override = lambda method, path, query, body: (
            (207, []) if method == "DELETE" and path == "/v2/orders" else None
        )
        report = await copilot.emergency_panic_halt()
        assert report.is_halted
        assert any(method == "POST" for method, *_ in venue.calls)
    else:
        await copilot.flatten_positions(confirm=True)
        assert not any(method in ("POST", "DELETE") for method, *_ in venue.calls)
        assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) is None
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.EXECUTED
    copilot.notifier.send_exit_alert.assert_not_awaited()


async def test_accepted_close_with_lost_ack_recovers_without_second_post(desk):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    dispatch = venue.dispatch
    lost = False

    def lose_ack(method, path, query, body):
        nonlocal lost
        result = dispatch(method, path, query, body)
        if method == "POST" and not lost:
            lost = True
            return 504, {"code": 50410000, "message": "response lost after fill"}
        return result

    venue.dispatch = lose_ack
    await copilot.close_position_manual(sid)
    await copilot.monitor_positions()
    assert (await copilot.db.get_signal_by_id(sid))["realized_pnl"] == 50
    assert len([1 for method, *_ in venue.calls if method == "POST"]) == 1
    await copilot.outbox.drain()
    copilot.notifier.send_exit_alert.assert_awaited_once()


async def test_real_sdk_websocket_fill_wakes_rest_reconciliation(desk):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    venue.take_profit.update(
        status="filled", filled_qty="10", filled_avg_price="110", filled_at=datetime.now(UTC).isoformat()
    )
    venue.position = None
    finished = asyncio.Event()
    messages = []

    async def stream(socket):
        messages.append(json.loads(await socket.recv())["action"])
        await socket.send(json.dumps({"stream": "authorization", "data": {"status": "authorized"}}))
        messages.append(json.loads(await socket.recv())["action"])
        payload = {
            "stream": "trade_updates",
            "data": {
                "event": "fill",
                "order": venue.take_profit,
                "timestamp": datetime.now(UTC).isoformat(),
                "price": "1",
                "qty": "10",
                "position_qty": "0",
            },
        }
        # Paper streaming uses binary frames; stream price is deliberately wrong:
        # the exact REST order fill must govern accounting.
        await socket.send(json.dumps(payload).encode())
        await finished.wait()

    async def callback(event):
        assert copilot.broker.trade_stream_connected
        await copilot.on_stream_trade_update(event)
        finished.set()

    async with serve(stream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        copilot.broker.api_key, copilot.broker.api_secret = "fake-key", "fake-secret"
        copilot.broker.base_url = f"ws://127.0.0.1:{port}"
        task = asyncio.create_task(copilot.broker.start_trade_stream(callback))
        try:
            await asyncio.wait_for(finished.wait(), timeout=3)
        finally:
            finished.set()
            await copilot.broker.stop_trade_stream()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert not copilot.broker.trade_stream_connected
    assert messages == ["authenticate", "listen"]
    assert (await copilot.db.get_signal_by_id(sid))["realized_pnl"] == 100
    await copilot.monitor_positions()
    await copilot.outbox.drain()
    copilot.notifier.send_exit_alert.assert_awaited_once()


async def test_sdk_type_field_maps_protective_leg_ids(alpaca_http):
    venue, broker = alpaca_http
    result = await broker.submit_entry_order(OrderRequest(symbol="SPY", entry_price=100, stop_loss=95, take_profit=110))
    assert result.success
    assert result.bracket_orders == {"stop_loss_id": venue.stop["id"], "take_profit_id": venue.take_profit["id"]}


@pytest.mark.parametrize(
    "changed", ["closed-session", "session-ended", "stale-quote", "price-moved", "existing-position"]
)
async def test_entry_admission_uses_real_sdk_evidence_before_any_mutation(desk, changed):

    copilot, venue, sid = desk
    venue.take_profit["status"] = "held"
    if changed != "existing-position":
        venue.position = None
    if changed == "closed-session":
        venue.market_open = False
    elif changed == "session-ended":
        venue.session_closes_at = datetime.now(UTC) - timedelta(seconds=1)
    elif changed == "stale-quote":
        venue.quote_time -= timedelta(minutes=5)
    elif changed == "price-moved":
        venue.quote_price = 110
    success = (await copilot.execute_signal_by_id(sid)).ok
    assert not success
    assert not any(method in ("POST", "PATCH", "DELETE") for method, *_ in venue.calls)
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.FAILED


async def test_lost_entry_ack_recovers_client_id_once_through_sdk(desk, broker_order_payload):

    copilot, venue, sid = desk
    venue.position = None
    venue.take_profit["status"] = "held"

    def accept_then_lose_ack(method, path, query, body):
        if method == "POST":
            venue.entry = broker_order_payload(**body, status="accepted")
            venue.orders[venue.entry["id"]] = venue.entry
            return 504, {"code": 50410000, "message": "lost acknowledgement"}

    venue.override = accept_then_lose_ack
    assert not (await copilot.execute_signal_by_id(sid)).ok
    assert await copilot.entry_service.recover() == 1
    assert await copilot.entry_service.recover() == 0
    assert (await copilot.db.get_signal_by_id(sid))["broker_order_id"] == venue.entry["id"]
    assert len([1 for method, *_ in venue.calls if method == "POST"]) == 1
    # Recovery deliberately preserves an explicit halt until operator review/resume.
    assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) == "true"


async def test_sdk_partial_order_journal_rebuilds_without_inventing_closure(desk):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    venue.stop.update(status="partially_filled", filled_qty="3", filled_avg_price="95.00")
    await copilot.monitor_positions()
    await copilot.monitor_positions()
    before = await copilot.db.workflows.order_views()
    partial = next(o for o in before if o.order_id == venue.stop["id"])
    assert partial.filled_quantity == "3" and partial.parent_order_id == venue.entry["id"]
    await copilot.db.workflows.rebuild_order_views()
    assert await copilot.db.workflows.order_views() == before
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.EXECUTED


async def test_failed_history_journal_does_not_prevent_exact_exit_reconciliation(desk):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    venue.take_profit.update(
        status="filled", filled_qty="10", filled_avg_price="110", filled_at=datetime.now(UTC).isoformat()
    )
    venue.override = lambda method, path, query, body: (
        (503, {"message": "history unavailable"}) if path == "/v2/orders" and query.get("status") == ["all"] else None
    )
    assert await copilot.monitor_positions() == 1
    assert (await copilot.db.get_signal_by_id(sid))["realized_pnl"] == 100
    health = await copilot.db.workflows.events(f"health/{copilot.readiness.run_id}/reconciliation", limit=1)
    assert not health[0]["payload"]["success"]
    assert "order_journal" in health[0]["payload"]["detail"]


@pytest.mark.parametrize("operation", ["close", "flatten"])
@pytest.mark.parametrize("desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_closed_session_reason_reaches_telegram_reply_and_durable_notice(desk, operation):
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    venue.market_open = False
    venue.override = lambda method, path, query, body: (
        (
            200,
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "is_open": False,
                "next_open": "2026-09-17T09:30:00-04:00",
                "next_close": "2026-09-17T16:00:00-04:00",
            },
        )
        if path == "/v2/clock"
        else None
    )
    notifier = TelegramNotifier(
        None,
        "test-chat",
        close_handler=copilot.close_position_manual,
        flatten_handler=copilot.flatten_positions,
    )
    update = MagicMock(effective_chat=SimpleNamespace(id="test-chat"))
    update.message.reply_text = AsyncMock()
    handler = notifier.handle_close_command if operation == "close" else notifier.handle_flatten_command
    await handler(update, SimpleNamespace(args=[str(sid)] if operation == "close" else ["confirm"]))
    reply = update.message.reply_text.await_args.args[0]
    await copilot.outbox.drain()
    notices = [call.args[0] for call in copilot.notifier.send_message.await_args_list]
    result_notices = [text for text in notices if "market is closed" in text]
    assert len(result_notices) == 1
    for text in (reply, result_notices[0]):
        assert "market is closed" in text
        assert "No close submitted or queued" in text
        assert "Next regular open: 2026-09-17 13:30 UTC" in text
        assert "Existing protective orders were left unchanged by this request" in text
        assert "Retry during regular market hours" in text
    assert "SPY" in result_notices[0]
    assert not any(method != "GET" for method, *_ in venue.calls)
    assert await copilot.db.active_close_requests() == []
    assert await copilot.db.get_state(SystemStateKey.TRADING_HALTED) is None


@pytest.mark.parametrize("failure", ["session_ended", "cancel_ack_lost", "broker_read_error", "submit_ack_lost"])
async def test_close_failure_notice_tracks_mutation_phase_and_hides_raw_broker_errors(desk, failure, request):
    if failure == "cancel_ack_lost" and request.config.getoption("--alpaca-transport") != "socket":
        pytest.skip("A lost DELETE acknowledgement requires the real SDK socket deadline")
    copilot, venue, sid = desk
    await track_existing(copilot, venue, sid)
    original = venue.dispatch
    clock_reads = 0

    def fail(method, path, query, body):
        nonlocal clock_reads
        response = original(method, path, query, body)
        if path == "/v2/clock":
            clock_reads += 1
            if failure == "session_ended" and clock_reads == 2:
                response[1]["is_open"] = False
            elif failure == "broker_read_error":
                return 403, {"code": 40310000, "message": "private broker payload <secret>"}
        if failure == "cancel_ack_lost" and method == "DELETE":
            # Outlast the real SDK socket deadline; cancellation already reached the venue.
            time.sleep(copilot.broker.client.request_timeout + 0.5)
        if failure == "submit_ack_lost" and method == "POST":
            return 504, {"code": 50410000, "message": "private broker payload <secret>"}
        return response

    venue.dispatch = fail
    reply = await copilot.close_service.close_signal(sid)
    await copilot.outbox.drain()
    notices = [call.args[0] for call in copilot.notifier.send_message.await_args_list]
    assert len(notices) == 2
    for text in (reply, notices[-1]):
        assert "private broker payload" not in text and "&lt;secret&gt;" not in text
        if failure == "broker_read_error":
            assert "HTTP 403" in text
            assert "Existing protective orders were left unchanged by this request" in text
        else:
            assert "Protective orders may have been cancelled" in text
            assert "left unchanged" not in text
    assert sum(method == "POST" for method, *_ in venue.calls) == (failure == "submit_ack_lost")
    failed = [e for e in await copilot.db.get_audit_events(sid) if e["payload"].get("phase") == "failed"]
    assert len(failed) == 1
    assert failed[0]["payload"]["cancellation_attempted"] == (failure != "broker_read_error")
    attempted = [
        e for e in await copilot.db.get_audit_events(sid) if e["payload"].get("phase") == "cancellation_requested"
    ]
    assert len(attempted) == sum(method == "DELETE" for method, *_ in venue.calls)
    if failure == "cancel_ack_lost":
        assert len(attempted) == 1  # A delivery/transport failure never replays the mutation.
    if failure == "submit_ack_lost":
        assert "HTTP 504" in reply and "Do not resubmit" in reply
        assert failed[0]["payload"]["submission_uncertain"]
        assert len(await copilot.db.active_close_requests()) == 1
