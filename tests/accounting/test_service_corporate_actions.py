"""The ledger service passes the latest capture's actions to reconcile; a store or parse failure degrades to none."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from agentic_trader.accounting.ledger import AccountSnapshot
from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.config import AccountingConfig


def _broker(activities):
    broker = MagicMock()
    broker.account_snapshot = AsyncMock(
        return_value=AccountSnapshot(account_id="acct", cash=Decimal(1000), positions={})
    )
    broker.account_activities = AsyncMock(return_value=activities)
    return broker


def _store():
    store = MagicMock()
    store.begin = AsyncMock(return_value="token")
    store.bind = AsyncMock()
    store.commit = AsyncMock(return_value=True)
    store.fail = AsyncMock()
    return store


CAPTURE = {
    "content_sha256": "x",
    "as_of": "2024-06-10",
    "actions": [
        {
            "id": "s",
            "kind": "forward_split",
            "symbol": "NVDA",
            "effective_on": "2024-06-10",
            "old_rate": 1.0,
            "new_rate": 10.0,
        }
    ],
}
DEPOSIT = {"id": "deposit", "activity_type": "JNLC", "net_amount": "1000"}
SSP = {"id": "a1", "activity_type": "SSP", "symbol": "NVDA", "date": "2024-06-10", "qty": "90", "net_amount": "0"}


async def test_refresh_names_the_action_from_the_latest_capture():
    actions = MagicMock()
    actions.latest_capture = AsyncMock(return_value=CAPTURE)
    service = AccountLedgerService(_broker([DEPOSIT, SSP]), _store(), AccountingConfig(), corporate_actions=actions)
    report = await service.refresh()
    assert report.issues == [
        "Unsupported activity type: SSP (forward split 10-for-1 NVDA effective 2024-06-10 in corporate-action evidence)"
    ]


async def test_refresh_without_a_store_or_with_a_failing_store_keeps_plain_text():
    plain = await AccountLedgerService(_broker([DEPOSIT, SSP]), _store(), AccountingConfig()).refresh()
    assert plain.issues == ["Unsupported activity type: SSP"]
    failing = MagicMock()
    failing.latest_capture = AsyncMock(side_effect=RuntimeError("db down"))
    degraded = await AccountLedgerService(
        _broker([DEPOSIT, SSP]), _store(), AccountingConfig(), corporate_actions=failing
    ).refresh()
    assert degraded.issues == ["Unsupported activity type: SSP"]
    assert datetime.now(UTC) >= degraded.observed_at


async def test_refresh_survives_a_capture_row_that_fails_validation(caplog):
    actions = MagicMock()
    actions.latest_capture = AsyncMock(return_value={**CAPTURE, "actions": [*CAPTURE["actions"], {"id": "x"}]})
    service = AccountLedgerService(_broker([DEPOSIT, SSP]), _store(), AccountingConfig(), corporate_actions=actions)
    with caplog.at_level(logging.WARNING, logger="agentic_trader.accounting.service"):
        report = await service.refresh()
    assert report.issues == ["Unsupported activity type: SSP"]
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "ledger_corporate_actions_unavailable"]
    assert record.reason == "ValidationError"
