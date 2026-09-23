import pytest

from agentic_trader.constants import SignalStatus


def _signal(**overrides):
    kwargs = {
        "contract": "CRM",
        "strategy": "TREND_PULLBACK",
        "direction": "LONG",
        "entry_price": 240.0,
        "stop_loss": 230.0,
        "take_profit": 260.0,
        "risk_dollars": 10.0,
        "asset_class": "EQUITY",
        "quantity": 1,
        "timeframe": "4h",
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
async def test_failed_card_does_not_block_the_same_setup(temp_db):
    # A system-side failure (e.g. a preflight refusal) must not consume the operator's
    # setup for the rest of the duplicate window.
    signal_id = await temp_db.record_signal(**_signal())
    await temp_db.update_signal_status(signal_id, SignalStatus.FAILED)
    assert not await temp_db.is_duplicate_recent("CRM", "TREND_PULLBACK", hours=12, timeframe="4h")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        SignalStatus.PENDING,
        SignalStatus.DISMISSED,
        SignalStatus.EXPIRED,
        SignalStatus.EXECUTED,
        SignalStatus.SUBMITTING,
    ],
)
async def test_offered_or_acted_cards_still_block_the_same_setup(temp_db, status):
    signal_id = await temp_db.record_signal(**_signal())
    if status != SignalStatus.PENDING:
        await temp_db.update_signal_status(signal_id, status)
    assert await temp_db.is_duplicate_recent("CRM", "TREND_PULLBACK", hours=12, timeframe="4h")
