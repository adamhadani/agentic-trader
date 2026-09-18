"""Real Alpaca SDK/HTTP + journal + sizing/admission risk boundaries."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import OrderRequest
from agentic_trader.cli.main import cli
from agentic_trader.constants import AuditEventType, ExecutionMode, SignalStatus
from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.execution.entries import EntryExecutionService
from agentic_trader.storage import workflow


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.fixture
async def risk_execution_desk(ledger_desk, app_config):
    ledger, venue, state = ledger_desk
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.portfolio.cash = 10000
    app_config.copilot_chat_enabled = False
    venue.position = None
    venue.take_profit["status"] = "held"
    state["account"]["cash"] = "10000"
    state["activities"] = [{"id": "deposit", "activity_type": "CSD", "net_amount": "10000"}]
    await ledger.refresh()
    copilot = TradingCopilot(
        app_config, db=ledger.store.store.db, broker=ledger.broker, ledger=ledger, notifier=MagicMock()
    )
    copilot.entry_service.macro_check = AsyncMock(return_value=None)
    signal = await copilot.db.record_signal(
        contract="SPY",
        direction="LONG",
        strategy="risk-integration",
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        risk_dollars=40,
        quantity=10,
        asset_class="EQUITY",
        status=SignalStatus.PENDING,
    )
    order = OrderRequest(
        signal_id=signal,
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        quantity=10,
    )
    return copilot, venue, state, order


def record_loss(state, amount):
    state["account"]["cash"] = str(10000 - amount)
    state["activities"].append({"id": "fee", "activity_type": "FEE", "net_amount": str(-amount)})


def mutations(venue):
    return [call for call in venue.calls if call[0] in ("POST", "PATCH", "DELETE")]


@pytest.mark.parametrize("stage", ["reservation", "queued", "preflight", "expired"])
async def test_worsening_or_stale_risk_blocks_real_sdk_post(risk_execution_desk, stage):
    copilot, venue, state, order = risk_execution_desk
    entry = copilot.entry_service
    if stage == "reservation":
        record_loss(state, 700)
        await copilot.ledger.refresh()
        item, reason = await entry.authorize(order)
        assert item is None
        audit = await copilot.db.get_audit_events(event_type=AuditEventType.ENTRY_ADMISSION_REJECTED)
        assert audit[0]["payload"]["reason"] == reason
    else:
        item, reason = await entry.store.enqueue_entry(order, copilot.config)
        assert item is not None, reason
        if stage == "queued":
            record_loss(state, 700)
        else:

            async def during_preflight(*args):
                if stage == "expired":
                    copilot.config.accounting.max_age_seconds = 0.000001
                else:
                    record_loss(state, 700)
                    await copilot.ledger.refresh()

            entry.macro_check.side_effect = during_preflight
        assert await entry.dispatch_one()
        item = await entry.store.get_work(item.id)
        assert item.status == WorkStatus.REJECTED
        reason = item.result["error_message"]
    assert any(word in reason.lower() for word in ("drawdown", "risk", "stale"))
    assert not mutations(venue)


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_sdk_submission_persists_exact_risk_and_remains_single(risk_execution_desk):
    copilot, venue, _state, order = risk_execution_desk
    service = copilot.entry_service
    other = EntryExecutionService(
        copilot.config, service.store, service.broker, service.executor, service.macro_check, ledger=copilot.ledger
    )
    await asyncio.gather(service.authorize(order), other.authorize(order))
    posts = mutations(venue)
    assert len(posts) == 1 and posts[0][0] == "POST"
    events = await service.store.events()
    submitted = [event for event in events if event["kind"] == EventKind.ENTRY_SUBMITTING]
    assert len(submitted) == 1
    risk = await copilot.ledger.current_risk()
    assert submitted[0]["payload"]["risk_fingerprint"] == risk.fingerprint
    assert submitted[0]["payload"]["account_risk"]["baseline_equity"] == "10000"


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_new_checkpoint_fences_an_already_checked_claim(risk_execution_desk):
    copilot, venue, state, order = risk_execution_desk
    store = copilot.db.workflows
    item, reason = await store.enqueue_entry(order, copilot.config)
    assert item is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    checked = await copilot.ledger.current_risk()
    record_loss(state, 700)
    await copilot.ledger.refresh()
    reason = await store.begin_submission(claim, copilot.config, risk_fingerprint=checked.fingerprint)
    assert "risk changed" in reason.lower()
    assert (await store.get_work(claim.id)).status == WorkStatus.CHECKING
    assert not mutations(venue)


async def test_waiting_for_risk_lock_cannot_extend_submission_lease(risk_execution_desk, monkeypatch):
    copilot, venue, _state, order = risk_execution_desk
    store = copilot.db.workflows
    item, reason = await store.enqueue_entry(order, copilot.config)
    assert item is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    risk = await copilot.ledger.current_risk()
    original = store._entry_risk

    async def delayed_lock(*args):
        result = await original(*args)
        clock = MagicMock(wraps=datetime)
        clock.now.return_value = datetime.now(UTC) + timedelta(seconds=61)
        monkeypatch.setattr(workflow, "datetime", clock)
        return result

    monkeypatch.setattr(store, "_entry_risk", delayed_lock)
    reason = await store.begin_submission(claim, copilot.config, risk_fingerprint=risk.fingerprint)
    assert "expired" in reason.lower()
    assert (await store.get_work(claim.id)).status == WorkStatus.CHECKING
    assert not mutations(venue)


async def test_withdrawal_to_zero_equity_does_not_authorize_new_risk(risk_execution_desk):
    copilot, venue, state, order = risk_execution_desk
    state["account"]["cash"] = "0"
    state["activities"].append({"id": "withdraw", "activity_type": "CSW", "net_amount": "-10000"})
    await copilot.ledger.refresh()
    risk = await copilot.ledger.current_risk()
    assert risk.drawdown_pct == 0 and risk.equity == 0
    item, reason = await copilot.entry_service.authorize(order)
    assert item is None and "nonpositive" in reason
    assert not mutations(venue)


async def test_cached_cli_risk_summary_redacts_identities(risk_execution_desk, monkeypatch):
    copilot, venue, _state, _order = risk_execution_desk
    monkeypatch.setattr("agentic_trader.cli.commands.db.SignalDatabase", lambda **kwargs: copilot.db)
    calls = len(venue.calls)
    result = await asyncio.to_thread(CliRunner().invoke, cli, ["db", "ledger"])
    assert result.exit_code == 0, result.exception
    payload = json.loads(result.output)
    assert "account_id" not in payload["risk"]
    assert "cash_transfer_fingerprints" not in payload["risk"]
    assert "baseline_cash_journal_fingerprints" not in payload["risk"]
    assert payload["risk"]["drawdown_pct"] == "0"
    assert len(venue.calls) == calls
