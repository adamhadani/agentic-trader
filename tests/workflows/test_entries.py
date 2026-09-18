import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import (
    BrokerEntryContext,
    BrokerPosition,
    EntryAccountEvidence,
    EntryAssetEvidence,
    EntryQuoteEvidence,
    OrderResult,
    ReconciliationEvent,
)
from agentic_trader.constants import SignalStatus, SystemStateKey
from agentic_trader.execution import entries
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.execution.entries import EntryExecutionService
from agentic_trader.notifier.outbox import NotificationDispatcher


@pytest.fixture
def service(store, app_config):
    now = datetime.now(UTC)
    account = EntryAccountEvidence(
        account_id="fixture-account",
        status="ACTIVE",
        currency="USD",
        cash="100000",
        equity="100000",
        buying_power="200000",
        regt_buying_power="200000",
        non_marginable_buying_power="100000",
        multiplier="2",
        trading_blocked=False,
        account_blocked=False,
        trade_suspended_by_user=False,
        shorting_enabled=True,
    )
    context = BrokerEntryContext(
        account_before=account,
        account=account,
        asset=EntryAssetEvidence(
            asset_id="00000000-0000-0000-0000-000000000001",
            symbol="SPY",
            asset_class="us_equity",
            status="active",
            tradable=True,
            marginable=True,
            shortable=True,
            fractionable=True,
            borrow_status="easy_to_borrow",
        ),
        quote=EntryQuoteEvidence(symbol="SPY", bid_price="99.9", ask_price="100.1", timestamp=now, feed="iex"),
        price="100",
        trade_timestamp=now,
        requested_at=now,
        observed_at=now,
        session_closes_at=now + timedelta(hours=6),
        orders=(),
        positions=(),
    )
    broker = AsyncMock()
    broker.entry_market_context.return_value = context
    broker.find_entry_order.return_value = None
    executor = AsyncMock()
    executor.execute_order.return_value = OrderResult(success=True, order_id="exact-entry-id")
    return EntryExecutionService(app_config, store, broker, executor, AsyncMock(return_value=None))


@pytest.mark.parametrize(
    "change,reason",
    [
        ("stale-quote", "stale"),
        ("price-drift", "Market price changed"),
        ("halt", "halt"),
        ("expired-approval", "expired"),
        ("macro", "Macro"),
        ("unknown-price", "invalid"),
        ("broker-position", "Untracked broker exposure"),
        ("broker-error", "unavailable"),
    ],
)
async def test_conditions_changed_since_approval_fail_before_post(service, store, entry, change, reason):
    item, _ = await store.enqueue_entry(await entry(), service.config)
    context = service.broker.entry_market_context.return_value
    if change == "stale-quote":
        context = context.model_copy(update={"trade_timestamp": context.trade_timestamp - timedelta(hours=1)})
    elif change == "price-drift":
        context = context.model_copy(update={"price": Decimal(200)})
    elif change == "unknown-price":
        service.broker.entry_market_context.side_effect = ValueError("Broker market-data price is invalid")
    elif change == "halt":
        await store.db.set_state(SystemStateKey.TRADING_HALTED, "true")
    elif change == "expired-approval":
        service.config.execution.entry_queue_max_age_seconds = 0.000001
    elif change == "macro":
        service.macro_check.return_value = "Macro lockout changed"
    elif change == "broker-position":
        context = context.model_copy(
            update={
                "positions": (
                    BrokerPosition(symbol="SPY", quantity=10, entry_price=100, current_price=100, asset_class="EQUITY"),
                )
            }
        )
    else:
        service.broker.entry_market_context.side_effect = TimeoutError
    service.broker.entry_market_context.return_value = context
    assert await service.dispatch_one()
    service.executor.execute_order.assert_not_awaited()
    result = await store.get_work(item.id)
    assert result.status == WorkStatus.REJECTED
    assert reason.lower() in result.result["error_message"].lower()
    assert (await store.db.get_signal_by_id(item.payload["signal_id"]))["status"] == SignalStatus.FAILED
    assert len(await store.list_work(WorkKind.NOTIFICATION)) == 1


async def test_independent_executors_submit_only_once(service, entry):
    request = await entry()
    other = EntryExecutionService(service.config, service.store, service.broker, service.executor, service.macro_check)
    await asyncio.gather(service.authorize(request), other.authorize(request))
    service.executor.execute_order.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["uncertain", "crash"])
async def test_recovery_after_post_never_resubmits(service, store, entry, outcome):
    request = await entry()
    if outcome == "uncertain":
        service.executor.execute_order.return_value = OrderResult(success=False, submission_uncertain=True)
        await service.authorize(request)
    else:
        service.executor.execute_order.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await service.authorize(request)
    assert not await service.dispatch_one()
    assert await service.recover() == 0  # 404/absence is not permission to replay
    service.broker.find_entry_order.return_value = OrderResult(success=True, order_id="persisted-broker-id")
    assert await service.recover() == 1
    assert await service.recover() == 0
    service.executor.execute_order.assert_awaited_once()
    row = await store.db.get_signal_by_id(request.signal_id)
    assert row["status"] == SignalStatus.EXECUTED and row["broker_order_id"] == "persisted-broker-id"


async def test_close_rolls_back_if_outbox_cannot_be_written(store, entry, monkeypatch):
    req = await entry()
    await store.db.update_signal_execution(req.signal_id, "entry-id")
    monkeypatch.setattr(store.db.workflows, "add_notification", AsyncMock(side_effect=RuntimeError("disk")))
    with pytest.raises(RuntimeError):
        await store.db.close_position(
            req.signal_id, 110, "take_profit", 100, SignalStatus.CLOSED_WIN, notification={"contract": "SPY"}
        )
    assert (await store.db.get_signal_by_id(req.signal_id))["status"] == SignalStatus.EXECUTED
    assert not await store.events(f"signal/{req.signal_id}")


async def test_outbox_retry_is_delivery_only(store, app_config, mock_notifier):
    mock_notifier.send_exit_alert.side_effect = [None, 77]
    item_id = await store.enqueue_notification("trade-closed", "exit", {"contract": "SPY"})
    app_config.execution.notification_retry_seconds = 0.000001
    dispatcher = NotificationDispatcher(store, mock_notifier, app_config.execution)
    await dispatcher.dispatch_one()
    assert (await store.get_work(item_id)).status == WorkStatus.QUEUED
    await dispatcher.dispatch_one()
    assert (await store.get_work(item_id)).status == WorkStatus.DELIVERED
    assert mock_notifier.send_exit_alert.await_count == 2


async def test_price_freshness_rechecked_after_slow_macro_admission(service, store, entry, monkeypatch):
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = datetime.now(UTC)
    monkeypatch.setattr(entries, "datetime", clock)
    service.config.execution.entry_evidence_max_age_seconds = 600

    async def slow_macro(*args):
        clock.now.return_value += timedelta(minutes=5)

    service.macro_check.side_effect = slow_macro
    item, _ = await service.authorize(await entry())
    assert item.status == WorkStatus.REJECTED
    assert "market trade" in item.result["error_message"].lower()
    service.executor.execute_order.assert_not_awaited()


@pytest.mark.parametrize("expires,reason", [("approval", "expired"), ("signal", "stale"), ("session", "session")])
async def test_admission_deadlines_are_rechecked_before_submission(service, entry, monkeypatch, expires, reason):
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = datetime.now(UTC)
    monkeypatch.setattr(entries, "datetime", clock)
    policy = service.config.execution
    policy.entry_quote_max_age_seconds = 600
    policy.entry_evidence_max_age_seconds = 600
    policy.entry_queue_max_age_seconds = 30 if expires == "approval" else 600
    policy.signal_max_age_seconds = 30 if expires == "signal" else 600
    if expires == "session":
        service.broker.entry_market_context.return_value = service.broker.entry_market_context.return_value.model_copy(
            update={"session_closes_at": clock.now() + timedelta(seconds=30)}
        )

    async def slow_macro(*args):
        clock.now.return_value += timedelta(seconds=60)

    service.macro_check.side_effect = slow_macro
    item, _ = await service.authorize(await entry())
    assert item.status == WorkStatus.REJECTED
    assert reason in item.result["error_message"].lower()
    service.executor.execute_order.assert_not_awaited()


async def test_trade_accounting_does_not_wait_for_telegram_delivery(store, app_config, entry, mock_notifier):
    app_config.copilot_chat_enabled = False
    copilot = TradingCopilot(app_config, db=store.db, notifier=mock_notifier)
    request = await entry()
    await store.db.update_signal_execution(request.signal_id, "entry-id")
    event = ReconciliationEvent(
        signal_id=request.signal_id,
        symbol="SPY",
        direction="LONG",
        exit_price=110,
        realized_pnl=100,
        broker_order_id="exit-id",
        order_side="sell",
    )
    assert await asyncio.wait_for(copilot.process_reconciliation_event(event), timeout=2)
    mock_notifier.send_exit_alert.assert_not_awaited()
    assert (await store.list_work(WorkKind.NOTIFICATION))[0].status == WorkStatus.QUEUED
    await copilot.outbox.drain()
    mock_notifier.send_exit_alert.assert_awaited_once()


@pytest.mark.parametrize(
    "policy,setting,value,reason",
    [
        ("portfolio", "max_notional_exposure", 999, "portfolio notional"),
        ("portfolio", "max_equity_exposure", 999, "asset-class"),
        ("sizing", "max_trade_notional_cap", 999, "per-trade notional"),
        ("sizing", "max_risk_pct_cap", 0.0001, "risk cap"),
        ("sizing", "max_shares_per_trade", 9, "quantity cap"),
    ],
)
async def test_entry_reservations_enforce_configured_caps(store, app_config, entry, policy, setting, value, reason):
    setattr(getattr(app_config, policy), setting, value)
    request = await entry()
    result, detail = await store.enqueue_entry(request, app_config)
    assert result is None and reason in detail
    assert (await store.db.get_signal_by_id(request.signal_id))["status"] == SignalStatus.PENDING


async def test_unconfigured_notifier_does_not_consume_outbox(store, app_config, mock_notifier):
    mock_notifier.is_configured.return_value = False
    item_id = await store.enqueue_notification("unconfigured", "message", {"text": "example"})
    assert not await NotificationDispatcher(store, mock_notifier, app_config.execution).dispatch_one()
    assert (await store.get_work(item_id)).attempts == 0
