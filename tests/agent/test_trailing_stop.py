from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import OrderResult
from agentic_trader.constants import AuditEventType, SignalStatus


@pytest.fixture
async def position(config, temp_db, mock_notifier):
    config.trailing_stop.enabled = True
    config.trailing_stop.breakeven_trigger_r = 1.0
    config.trailing_stop.breakeven_buffer_dollars = 5.0
    config.trailing_stop.trail_trigger_r = 1.5
    config.trailing_stop.trail_atr_multiple = 1.5
    config.trailing_stop.trail_step_ticks = 2
    copilot = TradingCopilot(config, db=temp_db, notifier=mock_notifier)
    copilot.data_fetcher = MagicMock()

    async def create(contract, direction, entry, stop, risk):
        sid = await temp_db.record_signal(
            contract=contract,
            strategy="test",
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            take_profit=entry * 2,
            risk_dollars=risk,
            quantity=1,
            status=SignalStatus.EXECUTED,
            raw_response="Original thesis",
        )
        return sid

    return copilot, create


@pytest.mark.parametrize(
    "contract,direction,entry,stop,risk,quote,breakeven,expected,reason",
    [
        ("/MES", "LONG", 5800, 5760, 200, 5845, 1.0, 5801, "BREAKEVEN"),
        ("/MNQ", "SHORT", 20000, 20050, 100, 19940, 1.0, 19997.5, "BREAKEVEN"),
        ("/MES", "LONG", 5800, 5760, 200, 5880, 1.0, 5820, "TRAILING_STOP"),
        ("/MES", "SHORT", 5800, 5840, 200, 5720, 1.0, 5780, "TRAILING_STOP"),
        ("/MES", "LONG", 5800, 5760, 200, 5848, None, 5760, None),
        ("/MES", "LONG", 5800, 5760, 200, 5880, None, 5820, "TRAILING_STOP"),
    ],
)
async def test_trailing_policy_preserves_initial_risk_and_thesis(
    position, contract, direction, entry, stop, risk, quote, breakeven, expected, reason
):
    copilot, create = position
    copilot.config.trailing_stop.breakeven_trigger_r = breakeven
    copilot.data_fetcher.fetch_latest_price.return_value = quote
    sid = await create(contract, direction, entry, stop, risk)
    assert await copilot.manage_trailing_stops(await copilot.db.get_active_positions()) == int(reason is not None)
    row = await copilot.db.get_signal_by_id(sid)
    assert (row["stop_loss"], row["risk_dollars"], row["raw_response"]) == (expected, risk, "Original thesis")
    if reason:
        copilot.notifier.send_trailing_stop_alert.assert_awaited_once()
        assert copilot.notifier.send_trailing_stop_alert.call_args.kwargs["reason"] == reason
        # A subsequent adverse move cannot loosen the acknowledged stop.
        assert not await copilot.db.update_position_stop(sid, stop, reason="stale update")
        assert any(e["event_type"] == AuditEventType.STOP_UPDATED for e in await copilot.db.get_audit_events())
    else:
        copilot.notifier.send_trailing_stop_alert.assert_not_awaited()


async def test_failed_broker_replacement_never_updates_or_alerts(position):
    copilot, create = position
    sid = await create("/MES", "LONG", 5800, 5760, 200)
    copilot.data_fetcher.fetch_latest_price.return_value = 5880
    copilot.broker.modify_order_stop = AsyncMock(return_value=OrderResult(success=False, error_message="pending"))
    assert await copilot.manage_trailing_stops(await copilot.db.get_active_positions()) == 0
    assert (await copilot.db.get_signal_by_id(sid))["stop_loss"] == 5760
    copilot.notifier.send_trailing_stop_alert.assert_not_awaited()
