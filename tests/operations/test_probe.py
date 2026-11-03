from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agentic_trader.config import OperationsConfig, QuietHoursConfig
from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.diagnostics.probe import probe_readiness
from agentic_trader.execution.durable import WorkKind
from agentic_trader.storage.operations import OperationsStore


@pytest.mark.parametrize("problem", [None, "unready", "stale", "bad_status", "inconsistent", "malformed", "timeout"])
async def test_passive_probe_contract(app_config, problem):
    now = datetime.now(UTC)
    payload = {
        "ready": problem != "unready",
        "run_id": "run",
        "started_at": (now - timedelta(minutes=10)).isoformat(),
        "checked_at": (now - timedelta(minutes=5) if problem == "stale" else now).isoformat(),
        "checks": {"worker": {"ready": problem != "unready"}},
    }
    if problem == "inconsistent":
        payload["ready"] = False

    def respond(request):
        assert request.method == "GET" and request.url.path == "/readyz"
        if problem == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(
            500 if problem == "bad_status" else 503 if problem == "unready" else 200,
            json=[] if problem == "malformed" else payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await probe_readiness(app_config, client)
    assert report["ready"] is (problem is None)
    assert ("endpoint" in report["checks"]) is (problem not in (None, "unready"))


async def test_startup_grace_and_missing_checks_cannot_clear_incident(temp_db, app_config):
    store = OperationsStore(temp_db.workflows)
    monitor = OperationsMonitor(store, app_config.operations)
    now = datetime.now(UTC)
    report = {"checked_at": now.isoformat(), "started_at": now.isoformat(), "checks": {"worker": {"ready": False}}}
    assert await monitor.observe(report) == []
    assert not any(i["component"] == "worker" for i in await store.incidents())
    report["started_at"] = (now - timedelta(hours=1)).isoformat()
    await monitor.observe(report)
    report["checked_at"] = (now + timedelta(seconds=120)).isoformat()
    assert len(await monitor.observe(report)) == 1
    report = {
        "checked_at": (now + timedelta(seconds=121)).isoformat(),
        "checks": {"endpoint": {"ready": False, "detail": "timeout"}},
    }
    await monitor.observe(report)
    assert next(i for i in await store.incidents() if i["component"] == "worker")["phase"] == "open"


def quiet_report(at, **checks):
    return {"checked_at": at.isoformat(), "checks": checks}


async def test_quiet_hours_mute_ordinary_components_but_not_broker_work(temp_db):
    monitor = OperationsMonitor(
        OperationsStore(temp_db.workflows),
        OperationsConfig(failure_seconds=10, quiet_hours=QuietHoursConfig(enabled=True)),
    )
    night = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    checks = {"scan": {"ready": False}, "entry_recovery": {"ready": False, "unresolved": 1}}
    await monitor.observe(quiet_report(night, **checks))
    notices = await monitor.observe(quiet_report(night + timedelta(seconds=10), **checks))
    assert [n.component for n in notices] == ["entry_recovery"]


async def held_scan(temp_db):
    store = OperationsStore(temp_db.workflows)
    monitor = OperationsMonitor(
        store, OperationsConfig(failure_seconds=120, quiet_hours=QuietHoursConfig(enabled=True))
    )
    night = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    for at in (night, night + timedelta(minutes=2)):
        assert await monitor.observe(quiet_report(at, scan={"ready": False})) == []
    return store, monitor


async def test_held_incident_pages_two_minutes_into_the_window(temp_db):
    _, monitor = await held_scan(temp_db)
    opening = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)
    assert await monitor.observe(quiet_report(opening, scan={"ready": False})) == []
    notices = await monitor.observe(quiet_report(opening + timedelta(minutes=2), scan={"ready": False}))
    assert [(n.component, n.kind) for n in notices] == [("scan", "opened")]


async def test_held_incident_that_clears_early_in_the_window_stays_silent(temp_db):
    store, monitor = await held_scan(temp_db)
    opening = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)
    assert await monitor.observe(quiet_report(opening, scan={"ready": False})) == []
    for minute in (1, 2):
        assert await monitor.observe(quiet_report(opening + timedelta(minutes=minute), scan={"ready": True})) == []
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    assert [i["phase"] for i in await store.incidents() if i["component"] == "scan"] == ["healthy"]
