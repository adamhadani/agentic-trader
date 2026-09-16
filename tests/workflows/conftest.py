import pytest

from agentic_trader.broker.base import OrderRequest
from agentic_trader.storage.workflow import WorkflowStore


@pytest.fixture
async def store(temp_db):
    return WorkflowStore(temp_db)


@pytest.fixture
async def entry(temp_db):
    async def make(symbol="SPY"):
        sid = await temp_db.record_signal(
            contract=symbol,
            strategy="queue-test",
            direction="LONG",
            entry_price=100,
            stop_loss=95,
            take_profit=110,
            risk_dollars=50,
            quantity=10,
            notional_value=1000,
            asset_class="EQUITY",
        )
        return OrderRequest(
            signal_id=sid,
            symbol=symbol,
            contract=symbol,
            asset_class="EQUITY",
            direction="LONG",
            quantity=10,
            entry_price=100,
            stop_loss=95,
            take_profit=110,
        )

    return make
