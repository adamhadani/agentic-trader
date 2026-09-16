from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.diagnostics.probe import probe_readiness
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
