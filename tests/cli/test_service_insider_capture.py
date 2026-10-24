"""The daemon's insider-capture composition: readiness component and the user-agent boundary."""

import asyncio
from types import SimpleNamespace

import pytest

import agentic_trader.cli.commands.service as service_module
from agentic_trader.cli.commands.service import run_insider_capture
from agentic_trader.config import InsiderWatchConfig
from agentic_trader.data.insider_capture import InsiderArchiveMismatch
from agentic_trader.diagnostics.readiness import HealthComponent
from tests.data.test_insider_capture import _archive, _trades


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


async def test_the_daemon_scope_loader_refuses_an_archive_that_is_not_the_pinned_one(
    temp_db, app_config, monkeypatch, tmp_path
):
    archive, _ = _archive(tmp_path, {"2026q3": _trades()})  # a ticker map, but not the protocol's archive
    seen: list[BaseException] = []

    async def worker(config, store, observe, shutdown, *, scope):
        with pytest.raises(InsiderArchiveMismatch) as raised:
            await scope()
        seen.append(raised.value)

    monkeypatch.setattr(service_module, "run_insider_capture_worker", worker)
    copilot = SimpleNamespace(
        db=SimpleNamespace(workflows=temp_db.workflows),
        readiness=SimpleNamespace(observe=None),
        _shutdown_event=asyncio.Event(),
    )
    config = app_config.model_copy(update={"insider_watch": InsiderWatchConfig(enabled=True, archive_dir=archive)})
    await run_insider_capture(copilot, config)
    assert len(seen) == 1
