"""The journal walk and bar source shared by `copilot cards outcomes` and the daemon."""

from datetime import UTC, datetime

import agentic_trader.cli.commands.cards as cards_module
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups import sources


async def _append(db, stream, kind, payload, key):
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(session, stream=stream, kind=kind, payload=payload, key=key)


async def test_scan_ranked_events_walks_each_et_date_and_keeps_only_ranked_events(temp_db):
    await _append(
        temp_db, "scan/2026-03-02", EventKind.SCAN_CANDIDATES_RANKED, {"scan_id": "a"}, "scan_candidates_ranked/a"
    )
    await _append(temp_db, "scan/2026-03-02", EventKind.PEAD_DECISION, {"scan_id": "p"}, "pead_decision/p")
    await _append(
        temp_db, "scan/2026-02-01", EventKind.SCAN_CANDIDATES_RANKED, {"scan_id": "old"}, "scan_candidates_ranked/old"
    )
    # 2026-03-03 15:00 UTC is 10:00 New York: two ET dates back are 03-03 and 03-02.
    events = await sources.scan_ranked_events(temp_db, 2, now=datetime(2026, 3, 3, 15, 0, tzinfo=UTC))
    assert [event["payload"]["scan_id"] for event in events] == ["a"]


def test_the_cli_uses_the_shared_helpers():
    assert cards_module.build_bar_source is sources.build_bar_source
    assert cards_module.scan_ranked_events is sources.scan_ranked_events
    assert not hasattr(cards_module, "_scan_ranked_events")
