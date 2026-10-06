"""Preflight runs the macro check on the simulated branch too (spec decision 6), after the cheap drift check."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from agentic_trader.broker.base import BrokerEntryContext, OrderRequest, OrderResult, SimulatedEntryContext
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import WorkStatus
from agentic_trader.execution.entries import EntryExecutionService


LOCKOUT = "Macro event lockout: CPI at 12:30 UTC."


@pytest.fixture
def simulated_service(store, app_config):
    """An entry service whose broker returns the simulator's empty admission context."""

    def make(macro_check):
        broker = AsyncMock()
        broker.entry_market_context.return_value = SimulatedEntryContext()
        broker.find_entry_order.return_value = None
        executor = AsyncMock()
        executor.execute_order.return_value = OrderResult(success=True, order_id="exact-entry-id")
        return EntryExecutionService(app_config, store, broker, executor, macro_check)

    return make


@pytest.fixture
def broker_service(store, app_config, broker_entry_context):
    """An entry service whose broker returns Alpaca-shaped admission evidence."""

    def make(macro_check, context: BrokerEntryContext = broker_entry_context):
        broker = AsyncMock()
        broker.entry_market_context.return_value = context
        broker.find_entry_order.return_value = None
        executor = AsyncMock()
        executor.execute_order.return_value = OrderResult(success=True, order_id="exact-entry-id")
        return EntryExecutionService(app_config, store, broker, executor, macro_check)

    return make


async def _queued(service, entry):
    item, reason = await service.store.enqueue_entry(await entry(), service.config)
    assert item is not None and item.status == WorkStatus.QUEUED, reason
    return item, OrderRequest.model_validate(item.payload)


async def test_simulated_preflight_rejects_during_macro_lockout(simulated_service, entry):
    service = simulated_service(AsyncMock(return_value=LOCKOUT))
    item, request = await _queued(service, entry)
    rejection, context = await service._preflight(item, request, None)
    assert rejection == LOCKOUT
    assert isinstance(context, SimulatedEntryContext)
    service.macro_check.assert_awaited_once()


async def test_simulated_preflight_passes_without_a_lockout(simulated_service, entry):
    service = simulated_service(AsyncMock(return_value=None))
    item, request = await _queued(service, entry)
    rejection, context = await service._preflight(item, request, None)
    assert rejection is None
    assert isinstance(context, SimulatedEntryContext)
    service.macro_check.assert_awaited_once()


async def test_simulated_dispatch_rejects_during_macro_lockout_before_submission(simulated_service, store, entry):
    service = simulated_service(AsyncMock(return_value=LOCKOUT))
    item, _request = await _queued(service, entry)
    assert await service.dispatch_one()
    service.executor.execute_order.assert_not_awaited()
    result = await store.get_work(item.id)
    assert result.status == WorkStatus.REJECTED
    assert result.result["error_message"] == LOCKOUT
    assert (await store.db.get_signal_by_id(item.payload["signal_id"]))["status"] == SignalStatus.FAILED


async def test_broker_preflight_reports_price_drift_before_awaiting_the_macro_check(
    broker_service, broker_entry_context, entry
):
    drifted = broker_entry_context.model_copy(update={"price": Decimal(200)})
    service = broker_service(AsyncMock(return_value=LOCKOUT), drifted)
    item, request = await _queued(service, entry)
    rejection, context = await service._preflight(item, request, None)
    assert rejection == "Market price changed to 200; original approved limit 100 retained."
    assert context is drifted
    service.macro_check.assert_not_awaited()


async def test_broker_preflight_runs_the_macro_check_when_the_price_holds(broker_service, entry):
    service = broker_service(AsyncMock(return_value=LOCKOUT))
    item, request = await _queued(service, entry)
    rejection, context = await service._preflight(item, request, None)
    assert rejection == LOCKOUT
    assert isinstance(context, BrokerEntryContext)
    service.macro_check.assert_awaited_once()
