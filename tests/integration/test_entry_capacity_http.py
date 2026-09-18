"""Shared admission through real Alpaca SDK, sockets, journal and durable outbox."""

from datetime import timedelta
from decimal import Decimal

import pytest

from agentic_trader.execution.durable import EventKind, WorkKind, WorkStatus


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("funding", "buying power"),
        ("overnight", "buying power"),
        ("nonmarginable", "buying power"),
        ("borrow", "borrow"),
        ("account-block", "blocked"),
        ("not-tradable", "tradable"),
        ("expired-evidence", "stale"),
        ("external-order", "unrelated"),
    ],
)
async def test_capacity_refusals_never_reach_post_and_retain_outbox_evidence(
    risk_execution_desk, change, reason, broker_order_payload
):
    copilot, venue, state, request = risk_execution_desk
    if change == "funding":
        state["account"]["buying_power"] = "1"
    elif change == "overnight":
        state["account"]["regt_buying_power"] = "1"
    elif change == "nonmarginable":
        venue.asset["marginable"] = False
        state["account"]["non_marginable_buying_power"] = "1"
    elif change == "borrow":
        request.direction, request.side, request.stop_loss, request.take_profit = "SHORT", "SELL", 103, 90
        venue.asset["borrow_status"] = "hard_to_borrow"
    elif change == "account-block":
        state["account"]["trading_blocked"] = True
    elif change == "not-tradable":
        venue.asset["tradable"] = False
    elif change == "expired-evidence":
        copilot.config.execution.entry_evidence_max_age_seconds = 0.000001
    else:
        external = broker_order_payload(symbol="IBM", order_class="simple", limit_price="100", side="sell")
        venue.orders[external["id"]] = external
    item, _ = await copilot.entry_service.authorize(request)
    assert item.status == WorkStatus.REJECTED
    assert reason in item.result["error_message"].lower()
    assert not [call for call in venue.calls if call[0] in ("POST", "PATCH", "DELETE")]
    evidence = item.result["raw_response"]["broker_context"]
    assert evidence["account"]["account_id"] == "account"
    notices = await copilot.db.workflows.list_work(WorkKind.NOTIFICATION)
    assert len(notices) == 1 and reason in str(notices[0].payload).lower()


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_successful_submission_journals_capacity_snapshot_once(risk_execution_desk):
    copilot, venue, _, request = risk_execution_desk
    item, _ = await copilot.entry_service.authorize(request)
    assert item.status == WorkStatus.ACCEPTED
    events = await copilot.db.workflows.events(f"entry/{item.id}")
    submissions = [event for event in events if event["kind"] == EventKind.ENTRY_SUBMITTING]
    assert len(submissions) == 1
    payload = submissions[0]["payload"]
    assert Decimal(payload["capacity"]["required_buying_power"]) == Decimal(991)
    assert Decimal(payload["capacity"]["aggregate_stop_risk"]) == Decimal(40)
    assert payload["broker_context"]["quote"]["feed"] == copilot.config.alpaca_data_feed
    assert payload["broker_context"]["asset"]["borrow_status"] == "easy_to_borrow"
    assert len([c for c in venue.calls if c[0] == "POST"]) == 1


async def test_capacity_freshness_is_rechecked_by_final_transaction(risk_execution_desk):
    copilot, venue, _, request = risk_execution_desk
    store = copilot.db.workflows
    item, _ = await store.enqueue_entry(request, copilot.config)
    claim = await store.claim_entry(lease_seconds=60)
    risk = await copilot.ledger.current_risk()
    context = await copilot.broker.entry_market_context(request)
    context = context.model_copy(update={"requested_at": context.requested_at - timedelta(seconds=31)})
    reason = await store.begin_submission(
        claim, copilot.config, risk_fingerprint=risk.fingerprint, broker_context=context
    )
    assert "stale" in reason.lower()
    assert (await store.get_work(item.id)).status == WorkStatus.CHECKING
    assert not [c for c in venue.calls if c[0] == "POST"]
