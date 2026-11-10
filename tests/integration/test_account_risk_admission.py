"""Real Alpaca SDK/HTTP + journal + sizing/admission risk boundaries."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from agentic_trader.accounting.risk import AccountRiskSnapshot
from agentic_trader.cli.main import cli
from agentic_trader.constants import AuditEventType
from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.execution.entries import EntryExecutionService
from agentic_trader.storage import workflow


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


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
    if stage == "preflight":
        assert "drawdown" in reason.lower()
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
async def test_worse_checkpoint_after_preflight_is_refused_by_capacity_recheck(risk_execution_desk):
    copilot, venue, state, order = risk_execution_desk
    store = copilot.db.workflows
    item, reason = await store.enqueue_entry(order, copilot.config)
    assert item is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    checked = await copilot.ledger.current_risk()
    record_loss(state, 700)
    await copilot.ledger.refresh()
    reason = await store.begin_submission(
        claim,
        copilot.config,
        preflight_risk_fingerprint=checked.fingerprint,
        broker_context=await copilot.broker.entry_market_context(order),
    )
    assert "drawdown" in reason.lower()
    assert (await store.get_work(claim.id)).status == WorkStatus.CHECKING
    assert not mutations(venue)


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_benign_refresh_during_preflight_submits_once(risk_execution_desk, caplog):
    copilot, venue, _state, order = risk_execution_desk
    entry = copilot.entry_service
    item, reason = await entry.store.enqueue_entry(order, copilot.config)
    assert item is not None, reason

    async def during_preflight(*args):
        await copilot.ledger.refresh()

    entry.macro_check.side_effect = during_preflight
    with caplog.at_level("INFO"):
        assert await entry.dispatch_one()
    refreshed = [r for r in caplog.records if getattr(r, "event", None) == "entry_risk_refreshed"]
    assert len(refreshed) == 1 and refreshed[0].signal_id == order.signal_id
    assert (await entry.store.get_work(item.id)).status == WorkStatus.ACCEPTED
    posts = mutations(venue)
    assert len(posts) == 1 and posts[0][0] == "POST"
    submitted = [e for e in await entry.store.events() if e["kind"] == EventKind.ENTRY_SUBMITTING]
    assert len(submitted) == 1
    payload = submitted[0]["payload"]
    current = await copilot.ledger.current_risk()
    assert payload["preflight_risk_fingerprint"] != payload["risk_fingerprint"]
    assert payload["risk_fingerprint"] == current.fingerprint
    assert payload["risk_fingerprint"] == AccountRiskSnapshot.model_validate(payload["account_risk"]).fingerprint


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_store_level_benign_refresh_is_not_refused(risk_execution_desk):
    copilot, _venue, _state, order = risk_execution_desk
    store = copilot.db.workflows
    item, reason = await store.enqueue_entry(order, copilot.config)
    assert item is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    checked = await copilot.ledger.current_risk()
    await copilot.ledger.refresh()
    context = await copilot.broker.entry_market_context(order)
    reason = await store.begin_submission(
        claim, copilot.config, preflight_risk_fingerprint=checked.fingerprint, broker_context=context
    )
    assert reason is None
    assert (await copilot.ledger.current_risk()).fingerprint != checked.fingerprint
    assert (await store.get_work(claim.id)).status == WorkStatus.SUBMITTING


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_withdrawal_after_preflight_is_refused_by_equity_recheck(risk_execution_desk):
    copilot, venue, state, order = risk_execution_desk
    store = copilot.db.workflows
    item, reason = await store.enqueue_entry(order, copilot.config)
    assert item is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    checked = await copilot.ledger.current_risk()
    context = await copilot.broker.entry_market_context(order)
    state["account"]["cash"] = "3000"
    state["activities"].append({"id": "withdraw", "activity_type": "CSW", "net_amount": "-7000"})
    await copilot.ledger.refresh()
    reason = await store.begin_submission(
        claim, copilot.config, preflight_risk_fingerprint=checked.fingerprint, broker_context=context
    )
    assert reason is not None and "per-trade risk" in reason.lower()
    assert (await copilot.ledger.current_risk()).drawdown_pct == 0
    assert (await store.get_work(claim.id)).status == WorkStatus.CHECKING
    assert not mutations(venue)


async def test_risk_without_preflight_fingerprint_is_refused(risk_execution_desk):
    copilot, venue, _state, order = risk_execution_desk
    store = copilot.db.workflows
    item, reason = await store.enqueue_entry(order, copilot.config)
    assert item is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    context = await copilot.broker.entry_market_context(order)
    reason = await store.begin_submission(
        claim, copilot.config, preflight_risk_fingerprint=None, broker_context=context
    )
    assert "not observed at preflight" in reason
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
    reason = await store.begin_submission(claim, copilot.config, preflight_risk_fingerprint=risk.fingerprint)
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
