"""The daemon's insider-capture composition: readiness component and the user-agent boundary."""

import asyncio
from types import SimpleNamespace

from agentic_trader.cli.commands.service import run_insider_capture
from agentic_trader.config import InsiderWatchConfig
from agentic_trader.diagnostics.readiness import HealthComponent


async def test_the_daemon_worker_observes_insider_capture_and_idles_without_a_user_agent(
    temp_db, app_config, monkeypatch
):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    shutdown = asyncio.Event()
    seen = []

    async def observe(component, ok, detail):
        seen.append((component, ok, detail))
        shutdown.set()

    copilot = SimpleNamespace(
        db=SimpleNamespace(workflows=temp_db.workflows),
        readiness=SimpleNamespace(observe=observe),
        _shutdown_event=shutdown,
    )
    config = app_config.model_copy(update={"insider_watch": InsiderWatchConfig(enabled=True)})
    await asyncio.wait_for(run_insider_capture(copilot, config), timeout=5)
    assert seen == [(HealthComponent.INSIDER_CAPTURE, False, "missing_user_agent")]
