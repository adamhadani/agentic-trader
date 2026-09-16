"""Regression cases from the 2026-09-15 database/valuation incident. All data is synthetic."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import BrokerPosition, OrderResult, ReconciliationEvent
from agentic_trader.cli.main import cli
from agentic_trader.cli.utils import get_copilot_and_config
from agentic_trader.constants import SignalStatus, normalize_asset_class
from agentic_trader.presentation.formatters import TelegramHtmlFormatter, TerminalFormatter
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import SignalRecord


@pytest.fixture
def incident(app_config, temp_db):
    app_config.execution_mode = "alpaca"
    client = MagicMock()
    copilot = TradingCopilot(app_config, db=temp_db, broker=AlpacaBroker(app_config, client=client))
    copilot.broker._connected = True
    copilot.notifier = MagicMock(send_exit_alert=AsyncMock(return_value=987))
    copilot.manage_trailing_stops = AsyncMock()
    return copilot, client


async def add_position(db, symbol="IWM", direction="SHORT", quantity=105, entry=285.18):
    sid = await db.record_signal(
        contract=symbol,
        strategy="regression",
        direction=direction,
        entry_price=entry,
        stop_loss=287.29,
        take_profit=280.96,
        risk_dollars=200,
        quantity=quantity,
        asset_class="EQUITY",
        status=SignalStatus.EXECUTED,
    )
    await db.update_signal_execution(sid, "entry-iwm")
    return sid


@pytest.mark.asyncio
async def test_broker_valuation_uses_actual_cost_basis_in_both_reports(incident):
    copilot, _ = incident
    sid = await add_position(copilot.db)
    copilot.broker.get_positions = AsyncMock(
        return_value=[
            BrokerPosition(
                symbol="IWM",
                direction="SHORT",
                quantity=105,
                entry_price=285.40,
                current_price=285.24,
                unrealized_pnl=16.80,
            )
        ]
    )
    copilot.data_fetcher.fetch_latest_price = MagicMock(side_effect=AssertionError("Do not mix quote feeds"))
    report = await copilot.get_positions_report()
    assert report.positions[0].entry_price == 285.40
    assert report.total_unrealized_pnl == 16.80
    assert "$16.80" in TerminalFormatter.format_positions_table(report)
    assert "$16.80" in TelegramHtmlFormatter.format_positions_html(report)
    copilot.broker.get_positions.assert_awaited_once()
    events = await copilot.db.get_audit_events()
    evidence = next(e for e in events if e["event_type"] == "positions_valuation")
    assert evidence["payload"]["positions"][0]["tracked_entries"] == [285.18]
    assert evidence["payload"]["positions"][0]["signal_ids"] == [sid]


@pytest.mark.asyncio
async def test_broker_only_position_and_duplicate_tracked_signals_are_shown_once(incident):
    copilot, _ = incident
    await add_position(copilot.db)
    await add_position(copilot.db)
    copilot.broker.get_positions = AsyncMock(
        return_value=[
            BrokerPosition(symbol="IWM", direction="SHORT", quantity=105, entry_price=285.40, unrealized_pnl=16.8),
            BrokerPosition(symbol="AMD", direction="LONG", quantity=45, entry_price=502.47, unrealized_pnl=45.6),
        ]
    )
    report = await copilot.get_positions_report()
    assert report.active_count == 2
    assert report.total_unrealized_pnl == 62.4
    assert "2 matching" in report.notes and "0 matching" in report.notes


@pytest.mark.asyncio
async def test_failed_snapshot_is_never_reported_as_empty_or_zero(incident):
    copilot, _ = incident
    copilot.broker.get_positions = AsyncMock(side_effect=RuntimeError("offline"))
    with pytest.raises(RuntimeError, match="Broker positions unavailable"):
        await copilot.get_positions_report()
    assert (await copilot.db.get_audit_events())[0]["event_type"] == "valuation_failed"


@pytest.mark.asyncio
async def test_entry_fill_corrects_cost_basis_and_notional_once(incident):
    copilot, client = incident
    sid = await add_position(copilot.db)
    client.get_order_by_id.return_value = {
        "id": "entry-iwm",
        "symbol": "IWM",
        "side": "sell",
        "status": "filled",
        "filled_avg_price": "285.40",
        "filled_qty": "105",
        "filled_at": datetime.now(UTC),
        "legs": [],
    }
    await copilot.sync_entry_executions(await copilot.db.get_active_positions())
    after = await copilot.db.get_signal_by_id(sid)
    assert after["entry_price"] == 285.4 and after["executed_at"]
    assert after["notional_value"] == pytest.approx(285.4 * 105)
    events_before = await copilot.db.get_audit_events()
    await copilot.sync_entry_executions(await copilot.db.get_active_positions())
    assert len(await copilot.db.get_audit_events()) == len(events_before)


@pytest.mark.asyncio
async def test_old_unrelated_39_share_spy_exit_cannot_close_synthetic_10_share_entry(incident):
    copilot, client = incident
    await add_position(copilot.db, symbol="SPY", direction="LONG", quantity=10, entry=500)
    client.get_order_by_id.side_effect = ValueError("No such entry order")
    client.get_orders.return_value = [
        {
            "id": "old-exit",
            "symbol": "SPY",
            "side": "sell",
            "status": "filled",
            "filled_avg_price": "759.549231",
            "filled_qty": "39",
            "filled_at": datetime.now(UTC) - timedelta(hours=3),
        }
    ]
    await copilot.monitor_positions()
    assert len(await copilot.db.get_active_positions()) == 1
    assert (await copilot.db.get_closed_positions_stats())["total_pnl"] == 0
    copilot.notifier.send_exit_alert.assert_not_called()
    client.get_orders.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["unrelated", "old", "wrong_side", "wrong_symbol", "partial", "wrong_quantity"])
async def test_exit_evidence_must_belong_to_entry_and_cover_entire_fill(incident, bad):
    copilot, client = incident
    await add_position(copilot.db)
    now = datetime.now(UTC)
    leg = {
        "id": "leg",
        "symbol": "IWM",
        "side": "buy",
        "status": "filled",
        "filled_avg_price": "280",
        "filled_qty": "105",
        "filled_at": now,
        "order_type": "limit",
    }
    entry = {
        "id": "entry-iwm",
        "symbol": "IWM",
        "side": "sell",
        "status": "filled",
        "filled_avg_price": "285.40",
        "filled_qty": "105",
        "filled_at": now - timedelta(minutes=1),
        "legs": [leg],
    }
    if bad == "unrelated":
        entry["legs"] = []
        client.get_orders.return_value = [leg]
    elif bad == "old":
        leg["filled_at"] = now - timedelta(hours=3)
    elif bad == "wrong_side":
        leg["side"] = "sell"
    elif bad == "wrong_symbol":
        leg["symbol"] = "SPY"
    elif bad == "partial":
        leg["status"] = "partially_filled"
    else:
        leg["filled_qty"] = "39"
    client.get_order_by_id.return_value = entry
    assert await copilot.broker.reconcile_positions(await copilot.db.get_active_positions()) == []
    entry["legs"] = [dict(leg, symbol="IWM", side="buy", status="filled", filled_qty="105", filled_at=now)]
    events = await copilot.broker.reconcile_positions(await copilot.db.get_active_positions())
    assert len(events) == 1 and events[0].realized_pnl == pytest.approx(567)


@pytest.mark.asyncio
async def test_atomic_close_suppresses_duplicate_alert_from_stale_snapshot(incident):
    copilot, _ = incident
    sid = await add_position(copilot.db)
    stale = await copilot.db.get_active_positions()
    event = ReconciliationEvent(
        signal_id=sid,
        symbol="IWM",
        direction="SHORT",
        exit_price=280,
        realized_pnl=567,
        broker_order_id="exit-iwm",
        order_side="buy",
    )
    assert await copilot.process_reconciliation_event(event, stale)
    assert not await copilot.process_reconciliation_event(event, stale)
    copilot.notifier.send_exit_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_quarantine_preserves_evidence_but_excludes_operational_queries(temp_db):
    sid = await add_position(temp_db)
    await temp_db.close_position(sid, 280, "TAKE_PROFIT", 567, SignalStatus.CLOSED_WIN)
    with pytest.raises(ValueError, match="does not match"):
        await temp_db.quarantine_signal(sid, "confirmed fixture", {"quantity": 39})
    assert await temp_db.quarantine_signal(sid, "confirmed fixture", {"quantity": 105, "broker_order_id": "entry-iwm"})
    assert (await temp_db.get_closed_positions_stats())["total_trades"] == 0
    assert await temp_db.get_recent_signals() == []
    assert not await temp_db.is_duplicate_recent("IWM", "regression")
    async with temp_db.session_factory() as session:
        record = (await session.execute(select(SignalRecord).where(SignalRecord.id == sid))).scalar_one()
        assert record.is_quarantined and record.realized_pnl == 567
    audit = (await temp_db.get_audit_events(sid))[0]
    assert audit["event_type"] == "signal_quarantined"
    assert audit["payload"]["before"]["is_quarantined"] is False


@pytest.mark.asyncio
async def test_manual_close_pending_or_rejected_never_fabricates_profit(incident):
    copilot, _ = incident
    sid = await add_position(copilot.db)
    copilot.broker.get_positions = AsyncMock(
        return_value=[BrokerPosition(symbol="IWM", direction="SHORT", quantity=105)]
    )
    copilot.broker.get_entry_execution = AsyncMock(return_value=None)
    copilot.broker.reconcile_positions = AsyncMock(return_value=[])
    copilot.broker.submit_position_close = AsyncMock(return_value=OrderResult(success=False, error_message="rejected"))
    assert "rejected" in await copilot.close_position_manual(sid, exit_price=1)
    assert (await copilot.db.get_signal_by_id(sid))["status"] == SignalStatus.EXECUTED
    copilot.broker.submit_position_close.return_value = OrderResult(success=True, order_id="manual-exit")
    copilot.broker.get_entry_execution = AsyncMock(return_value=None)
    copilot.broker.reconcile_positions = AsyncMock(return_value=[])
    assert "awaiting" in await copilot.close_position_manual(sid, exit_price=1)
    assert (await copilot.db.get_signal_by_id(sid))["broker_exit_order_id"] == "manual-exit"
    assert (await copilot.db.get_closed_positions_stats())["total_trades"] == 0
    copilot.notifier.send_exit_alert.assert_not_called()


def test_dry_run_constructs_separate_database_and_simulated_broker(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://production/agentic_trader")
    monkeypatch.setenv("DB_PATH", "")
    monkeypatch.setenv("EXECUTION_MODE", "alpaca")
    copilot, config = get_copilot_and_config(dry_run=True)
    assert "copilot-dry-run" in config.resolved_db_url
    assert not copilot.broker.authoritative_positions
    assert not copilot.notifier.is_configured()


def test_test_alert_defaults_to_preview_without_runtime_construction(monkeypatch):
    monkeypatch.setattr(
        "agentic_trader.cli.commands.trade.get_copilot_and_config", MagicMock(side_effect=AssertionError)
    )
    result = CliRunner().invoke(cli, ["test-alert"])
    assert result.exit_code == 0 and "[TEST]" in result.output


@pytest.mark.asyncio
async def test_same_signal_can_only_be_claimed_once(temp_db):

    sid = await temp_db.record_signal(
        contract="SPY",
        strategy="claim",
        direction="LONG",
        entry_price=100,
        stop_loss=99,
        take_profit=102,
        risk_dollars=1,
    )
    results = await asyncio.gather(temp_db.claim_signal(sid), temp_db.claim_signal(sid))
    assert sorted(results) == [False, True]
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.SUBMITTING


@pytest.mark.asyncio
async def test_alpaca_performance_excludes_unverified_closed_records(app_config, tmp_path):

    app_config.execution_mode = "alpaca"
    app_config.db_path = str(tmp_path / "verified.db")
    db = SignalDatabase(config=app_config)
    try:
        missing = await add_position(db)
        await db.close_position(missing, 280, "TAKE_PROFIT", 567, SignalStatus.CLOSED_WIN)
        verified = await add_position(db)
        await db.update_signal_execution(verified, "entry-verified", fill_price=285.4, executed_at=datetime.now(UTC))
        await db.close_position(
            verified, 280, "TAKE_PROFIT", 567, SignalStatus.CLOSED_WIN, broker_exit_order_id="exit-verified"
        )
        stats = await db.get_closed_positions_stats()
        assert stats["total_trades"] == 1 and stats["total_pnl"] == 567
        assert stats["unverified_closed_count"] == 1
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "value, expected", [("equities", "equity"), ("EQUITY", "equity"), ("futures", "futures"), ("crypto", "crypto")]
)
def test_asset_class_normalization(value, expected):

    assert normalize_asset_class(value) == expected
