"""A fill landing during an activity import is re-read, never persisted as a torn checkpoint.

2026-11-23 10:39 ET: CHYM's auto-entry filled between the import's two account snapshots, so the persisted
checkpoint read "Broker accounting changed during import" and the next two auto-entries (PDD, BFH) were refused
on it within a second. The importer now re-reads (GETs only) while a read crosses an account change: its
snapshots differ, or the account moved since the last committed checkpoint.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from agentic_trader.accounting.ledger import AccountPosition, AccountSnapshot
from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.config import AccountingConfig


CHANGED = "Broker accounting changed during import; retry on next refresh"
FEED_BEHIND = "Activity feed behind a recent account change; retry on next refresh"
DEPOSIT = {"id": "deposit", "activity_type": "JNLC", "net_amount": "1000"}
UNSUPPORTED = {"id": "weird", "activity_type": "WEIRD", "symbol": "XYZ", "date": "2026-11-23"}
FILL = {
    "id": "fill",
    "activity_type": "FILL",
    "qty": "10",
    "price": "50",
    "side": "buy",
    "symbol": "XYZ",
    "order_id": "order",
    "transaction_time": "2026-11-23T15:39:47Z",
}


def _pre() -> AccountSnapshot:
    return AccountSnapshot(account_id="acct", cash=Decimal(1000), positions={})


def _post() -> AccountSnapshot:
    position = AccountPosition(
        qty=Decimal(10), cost_basis=Decimal(500), unrealized_pl=Decimal(0), asset_class="us_equity"
    )
    return AccountSnapshot(account_id="acct", cash=Decimal(500), positions={"XYZ": position})


def _broker(snapshots: list, activities: list) -> MagicMock:
    broker = MagicMock()
    broker.account_snapshot = AsyncMock(side_effect=snapshots)
    broker.account_activities = AsyncMock(side_effect=activities)
    return broker


def _store(committed: AccountSnapshot | dict | None = None) -> MagicMock:
    """A ledger store whose last committed checkpoint holds ``committed`` (a snapshot or a raw payload)."""
    store = MagicMock()
    raw = committed.model_dump(mode="json") if isinstance(committed, AccountSnapshot) else committed
    store.status = AsyncMock(return_value={} if raw is None else {"snapshot": raw, "report": {}})
    store.begin = AsyncMock(return_value="token")
    store.bind = AsyncMock()
    store.commit = AsyncMock(return_value=True)
    store.fail = AsyncMock()
    return store


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    waited: list[float] = []

    async def sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr("agentic_trader.accounting.service.asyncio.sleep", sleep)
    return waited


def _committed(store: MagicMock) -> tuple[list, dict]:
    store.commit.assert_awaited_once()
    _, activities, checkpoint = store.commit.await_args.args
    return activities, checkpoint


async def test_a_fill_landing_mid_import_is_re_read_and_only_the_consistent_import_is_persisted(sleeps):
    final = _post()
    store = _store()
    broker = _broker([_pre(), _post(), _post(), final], [[DEPOSIT], [DEPOSIT, FILL]])
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert report.ready and report.issues == []
    activities, checkpoint = _committed(store)
    assert activities == [DEPOSIT, FILL]
    assert checkpoint["snapshot"] == final.model_dump(mode="json")
    assert checkpoint["report"]["observed_at"] == checkpoint["snapshot"]["observed_at"]
    assert sleeps == [2.0]
    store.fail.assert_not_awaited()


async def test_an_activity_feed_still_behind_a_stable_account_is_re_read_until_it_catches_up(sleeps):
    """The 11-23 shape: the account already holds the fill while the activity feed does not yet."""
    store = _store()
    snapshots = [_pre(), _post(), _post(), _post(), _post(), _post()]
    broker = _broker(snapshots, [[DEPOSIT], [DEPOSIT], [DEPOSIT, FILL]])
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert report.ready
    assert broker.account_activities.await_count == 3
    assert _committed(store)[0] == [DEPOSIT, FILL]
    assert sleeps == [2.0, 2.0]


async def test_an_account_still_moving_after_every_attempt_persists_one_report_naming_the_race(sleeps):
    store = _store()
    broker = _broker([_pre(), _post(), _pre(), _post(), _pre(), _post()], [[DEPOSIT]] * 3)
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert not report.ready and report.gross_realized is None and report.net_realized is None
    assert CHANGED in report.issues and FEED_BEHIND not in report.issues
    assert broker.account_activities.await_count == 3
    assert CHANGED in _committed(store)[1]["report"]["issues"]
    assert len(sleeps) == 2


async def test_a_feed_still_behind_after_every_attempt_persists_one_report_saying_so(sleeps):
    store = _store()
    broker = _broker([_pre(), _post(), _post(), _post(), _post(), _post()], [[DEPOSIT]] * 3)
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert not report.ready
    assert FEED_BEHIND in report.issues and CHANGED not in report.issues
    assert broker.account_activities.await_count == 3
    assert FEED_BEHIND in _committed(store)[1]["report"]["issues"]


async def test_a_fill_landing_just_before_the_refresh_is_re_read_against_the_last_committed_account(sleeps):
    """Card N's fill reaches the account before card N+1's preflight refresh starts; the feed still lags it."""
    store = _store(committed=_pre())
    broker = _broker([_post()] * 4, [[DEPOSIT], [DEPOSIT, FILL]])
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert report.ready
    assert broker.account_activities.await_count == 2
    assert _committed(store)[0] == [DEPOSIT, FILL]


async def test_a_movement_with_an_issue_a_re_read_cannot_clear_is_persisted_after_one_read(sleeps):
    store = _store(committed=_pre())
    broker = _broker([_pre(), _post()], [[DEPOSIT, UNSUPPORTED]])
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert not report.ready and CHANGED in report.issues
    assert any(issue.startswith("Unsupported activity type: WEIRD") for issue in report.issues)
    assert broker.account_activities.await_count == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "committed",
    [
        pytest.param(None, id="first-import"),
        pytest.param(_post(), id="already-committed"),
        pytest.param({"account_id": "acct"}, id="invalid-committed-snapshot"),
        pytest.param(
            AccountSnapshot(account_id="other", cash=Decimal(1000), positions={}), id="other-account-committed"
        ),
    ],
)
async def test_a_steady_mismatch_is_persisted_without_a_re_read(sleeps, committed):
    store = _store(committed=committed)
    broker = _broker([_post(), _post()], [[DEPOSIT]])
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert not report.ready and CHANGED not in report.issues and FEED_BEHIND not in report.issues
    assert broker.account_activities.await_count == 1
    assert sleeps == []
    _committed(store)


async def test_a_clean_import_reads_once(sleeps):
    store = _store()
    broker = _broker([_post(), _post()], [[DEPOSIT, FILL]])
    report = await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    assert report.ready
    assert broker.account_activities.await_count == 1
    assert sleeps == []


async def test_a_read_failure_on_a_re_read_fails_the_refresh_without_persisting(sleeps):
    store = _store()
    broker = _broker([_pre(), _post(), _post()], [[DEPOSIT], ConnectionError("reset")])
    with pytest.raises(ConnectionError):
        await AccountLedgerService(broker, store, AccountingConfig()).refresh()
    store.commit.assert_not_awaited()
    store.fail.assert_awaited_once_with("token", "ConnectionError")


async def test_a_single_attempt_keeps_the_old_one_read_behaviour(sleeps):
    store = _store()
    broker = _broker([_pre(), _post()], [[DEPOSIT]])
    report = await AccountLedgerService(broker, store, AccountingConfig(import_attempts=1)).refresh()
    assert CHANGED in report.issues and not report.ready
    assert sleeps == []


async def test_the_configured_delay_separates_attempts(sleeps):
    store = _store()
    broker = _broker([_pre(), _post(), _post(), _post()], [[DEPOSIT], [DEPOSIT, FILL]])
    config = AccountingConfig(import_retry_delay_seconds=0.5)
    assert (await AccountLedgerService(broker, store, config).refresh()).ready
    assert sleeps == [0.5]


def test_import_retry_settings_are_bounded():
    config = AccountingConfig()
    assert (config.import_attempts, config.import_retry_delay_seconds) == (3, 2.0)
    for bad in ({"import_attempts": 0}, {"import_attempts": 4}):
        with pytest.raises(ValidationError):
            AccountingConfig(**bad)
    for delay in (-1, 3.5, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            AccountingConfig(import_retry_delay_seconds=delay)
