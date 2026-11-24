import json
from datetime import UTC, datetime

import pytest

from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.ledger import LEDGER_STREAM, LedgerStore
from agentic_trader.storage.models import DomainEventRecord
from agentic_trader.storage.workflow import WorkflowStore


@pytest.fixture
async def ledger(temp_db):
    return LedgerStore(temp_db.workflows)


async def test_revisions_reversions_retractions_replay_and_fencing(ledger):
    a = {"id": "a", "activity_type": "FILL", "price": "1"}
    token = await ledger.begin("account")
    assert await ledger.commit(token, [a], {"report": "first"})
    token = await ledger.begin("account")
    await ledger.commit(token, [a], {"report": "same"})
    assert len(await ledger.store.events("activity/a")) == 1
    for price in ("2", "1"):
        token = await ledger.begin("account")
        await ledger.commit(token, [{**a, "price": price}], {"report": price})
    assert len(await ledger.store.events("activity/a")) == 3
    stale = await ledger.begin("account")
    current = await ledger.begin("account")
    assert not await ledger.commit(stale, [], {})
    await ledger.commit(current, [], {"report": "removed"})
    assert await ledger.activities() == []
    await ledger.rebuild()
    assert await ledger.activities() == []
    assert (await ledger.status())["report"] == "removed"
    with pytest.raises(ValueError, match="account"):
        await ledger.begin("different-account")


async def test_failure_preserves_evidence_and_replay_preserves_failure(ledger):
    token = await ledger.begin("account")
    await ledger.commit(token, [{"id": "a", "qty": "1"}], {"report": "prior"})
    token = await ledger.begin("account")
    await ledger.fail(token, "TimeoutError")
    assert (await ledger.status())["error"] == "TimeoutError"
    assert len(await ledger.activities()) == 1
    await ledger.rebuild()
    assert (await ledger.status())["error"] == "TimeoutError"
    assert len(await ledger.activities()) == 1


async def test_late_failure_cannot_invalidate_new_checkpoint(ledger):
    old = await ledger.begin("account")
    new = await ledger.begin("account")
    await ledger.commit(new, [], {"report": "current"})
    await ledger.fail(old, "TimeoutError")
    assert (await ledger.status())["error"] is None


async def test_scopes_do_not_share_activities(temp_db, ledger):
    token = await ledger.begin("account")
    await ledger.commit(token, [{"id": "a"}], {})

    other = WorkflowStore(temp_db)
    other.scope = "test/other-account"
    assert await LedgerStore(other).activities() == []


async def test_first_request_failure_is_fenced_and_replayable(ledger):
    first = await ledger.begin()
    await ledger.fail(first, "TimeoutError")
    await ledger.rebuild()
    assert (await ledger.status())["error"] == "TimeoutError"
    stale = await ledger.begin()
    current = await ledger.begin()
    await ledger.bind(current, "account")
    await ledger.commit(current, [], {"report": "fresh"})
    await ledger.fail(stale, "TimeoutError")
    with pytest.raises(RuntimeError, match="superseded"):
        await ledger.bind(stale, "account")
    assert (await ledger.status())["error"] is None


async def _checkpoint_event(ledger, recorded_at: datetime, risk: dict | None, *, key: str) -> None:
    payload = {"report": key, "risk": risk, "error": None}
    async with ledger.store.db.session_factory() as session, session.begin():
        session.add(
            DomainEventRecord(
                scope=ledger.scope,
                event_key=key,
                stream=LEDGER_STREAM,
                kind=EventKind.LEDGER_CHECKPOINT,
                payload=json.dumps(payload),
                recorded_at=recorded_at,
            )
        )


async def test_adjusted_equity_at_reads_the_latest_checkpoint_not_after_the_instant(ledger):
    assert await ledger.adjusted_equity_at(datetime(2026, 11, 14, tzinfo=UTC)) is None
    await _checkpoint_event(ledger, datetime(2026, 11, 7, 14, tzinfo=UTC), {"adjusted_equity": "100000.50"}, key="a")
    await _checkpoint_event(ledger, datetime(2026, 11, 10, 14, tzinfo=UTC), {"adjusted_equity": "101000"}, key="b")
    # A checkpoint with no risk block (a refresh that never produced one) is skipped, not read as zero.
    await _checkpoint_event(ledger, datetime(2026, 11, 12, 14, tzinfo=UTC), None, key="c")
    await _checkpoint_event(ledger, datetime(2026, 11, 13, 14, tzinfo=UTC), {"adjusted_equity": "102500.25"}, key="d")

    assert await ledger.adjusted_equity_at(datetime(2026, 11, 6, tzinfo=UTC)) is None
    assert await ledger.adjusted_equity_at(datetime(2026, 11, 8, tzinfo=UTC)) == pytest.approx(100000.50)
    assert await ledger.adjusted_equity_at(datetime(2026, 11, 12, 15, tzinfo=UTC)) == pytest.approx(101000.0)
    assert await ledger.adjusted_equity_at(datetime(2026, 11, 14, tzinfo=UTC)) == pytest.approx(102500.25)

    other = WorkflowStore(ledger.store.db)
    other.scope = "test/other-account"
    assert await LedgerStore(other).adjusted_equity_at(datetime(2026, 11, 14, tzinfo=UTC)) is None
