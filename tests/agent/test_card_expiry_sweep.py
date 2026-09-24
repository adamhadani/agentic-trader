"""``TradingCopilot.expire_stale_cards``: the scheduler-facing wrapper around the DB sweep.

It must never raise into the scheduler, must pass through ``now``/the configured
contract set, and must log the expired signal ids only when the sweep actually changed
something.
"""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot


@pytest.fixture
def sweep_desk(app_config, temp_db, mock_notifier):
    app_config.copilot_chat_enabled = False
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        entry_service=AsyncMock(),
        alpha_repository=AsyncMock(),
    )
    copilot.db.expire_stale_signals = AsyncMock(return_value=[])
    return copilot


@pytest.mark.asyncio
async def test_expire_stale_cards_calls_db_with_now_and_configured_contracts(sweep_desk):
    sweep_desk.config.contracts = {"SPY": object(), "/MES": object()}

    await sweep_desk.expire_stale_cards()

    sweep_desk.db.expire_stale_signals.assert_awaited_once()
    args, kwargs = sweep_desk.db.expire_stale_signals.call_args
    assert isinstance(args[0], datetime) and args[0].tzinfo is UTC
    assert kwargs["configured_contracts"] == sweep_desk.config.contracts


@pytest.mark.asyncio
async def test_expire_stale_cards_logs_signal_ids_when_nonempty(sweep_desk, caplog):
    sweep_desk.db.expire_stale_signals = AsyncMock(return_value=[7, 9])

    with caplog.at_level(logging.INFO, logger="copilot"):
        await sweep_desk.expire_stale_cards()

    events = [r for r in caplog.records if getattr(r, "event", None) == "cards_expired"]
    assert len(events) == 1
    assert events[0].signal_ids == [7, 9]


@pytest.mark.asyncio
async def test_expire_stale_cards_logs_nothing_when_no_cards_expired(sweep_desk, caplog):
    with caplog.at_level(logging.INFO, logger="copilot"):
        await sweep_desk.expire_stale_cards()

    events = [r for r in caplog.records if getattr(r, "event", None) == "cards_expired"]
    assert events == []


@pytest.mark.asyncio
async def test_expire_stale_cards_swallows_db_exceptions(sweep_desk, caplog):
    sweep_desk.db.expire_stale_signals = AsyncMock(side_effect=RuntimeError("db exploded"))

    with caplog.at_level(logging.ERROR, logger="copilot"):
        await sweep_desk.expire_stale_cards()  # must not raise

    failures = [r for r in caplog.records if getattr(r, "event", None) == "card_expiry_sweep_failed"]
    assert len(failures) == 1


@pytest.mark.asyncio
async def test_expire_stale_cards_runs_regardless_of_halt_state(sweep_desk):
    # Expiring an untapped card releases no risk and adds none, so the sweep must keep
    # running even while new entries are blocked -- unlike the scan jobs, it is never
    # halt-gated.
    sweep_desk.is_halted = True

    await sweep_desk.expire_stale_cards()

    sweep_desk.db.expire_stale_signals.assert_awaited_once()
