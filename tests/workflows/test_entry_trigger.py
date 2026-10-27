"""The entry trigger (tap or auto) is stored on the work item and the entry_queued event."""

import json

from sqlalchemy import select

from agentic_trader.execution.durable import EntryTrigger, EventKind
from agentic_trader.storage.models import WorkItemRecord
from agentic_trader.storage.workflow import encode


async def _queued(store, item):
    return [e for e in await store.events(stream=f"entry/{item.id}") if e["kind"] == EventKind.ENTRY_QUEUED]


async def test_default_trigger_is_tap(store, entry, app_config):
    item, _ = await store.enqueue_entry(await entry(), app_config)

    assert item.payload["entry_trigger"] == "tap"
    assert (await _queued(store, item))[0]["payload"]["entry_trigger"] == "tap"


async def test_auto_trigger_is_journaled(store, entry, app_config):
    item, _ = await store.enqueue_entry(await entry(), app_config, trigger=EntryTrigger.AUTO)

    assert item.payload["entry_trigger"] == "auto"
    assert (await _queued(store, item))[0]["payload"]["entry_trigger"] == "auto"
    assert EntryTrigger.AUTO == "auto" and EntryTrigger.TAP == "tap"


async def test_entry_trigger_of_reads_latest_entry_and_defaults_tap(store, entry, app_config, temp_db):
    request = await entry()
    async with temp_db.session_factory() as session:
        assert await store.entry_trigger_of(session, request.signal_id) is None
    await store.enqueue_entry(request, app_config, trigger=EntryTrigger.AUTO)
    async with temp_db.session_factory() as session:
        assert await store.entry_trigger_of(session, request.signal_id) is EntryTrigger.AUTO

    # A payload written before the key existed reads as a tap.
    other = await entry("QQQ")
    item, _ = await store.enqueue_entry(other, app_config)
    async with temp_db.session_factory() as session, session.begin():
        row = await session.scalar(select(WorkItemRecord).where(WorkItemRecord.id == item.id))
        payload = json.loads(row.payload)
        del payload["entry_trigger"]
        row.payload = encode(payload)
    async with temp_db.session_factory() as session:
        assert await store.entry_trigger_of(session, other.signal_id) is EntryTrigger.TAP
