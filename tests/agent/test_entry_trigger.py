"""``execute_signal_by_id`` threads its trigger to authorization and the tap journal."""

from agentic_trader.execution.durable import EntryTrigger
from tests.agent.test_card_freshness_tap import record_card, tap_desk, tap_events  # noqa: F401  (fixture + helpers)


async def test_tap_journals_tap_trigger(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db)

    await tap_desk.execute_signal_by_id(sid)

    assert tap_desk.entry_service.authorize.await_args.kwargs["trigger"] is EntryTrigger.TAP
    assert (await tap_events(temp_db, sid))[0]["payload"]["entry_trigger"] == "tap"


async def test_auto_trigger_journals_auto(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db)

    await tap_desk.execute_signal_by_id(sid, trigger=EntryTrigger.AUTO)

    assert tap_desk.entry_service.authorize.await_args.kwargs["trigger"] is EntryTrigger.AUTO
    assert (await tap_events(temp_db, sid))[0]["payload"]["entry_trigger"] == "auto"
