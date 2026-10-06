from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.broker.base import (
    BrokerEntryContext,
    EntryAccountEvidence,
    EntryAssetEvidence,
    EntryQuoteEvidence,
    OrderRequest,
)
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


@pytest.fixture
def broker_entry_context() -> BrokerEntryContext:
    """Fresh, funded Alpaca-shaped admission evidence for one SPY entry at $100."""
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
    return BrokerEntryContext(
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
