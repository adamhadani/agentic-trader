"""The daemon's earnings-capture composition: readiness component, store directory and the enabled gate."""

import asyncio
from types import SimpleNamespace

import agentic_trader.cli.commands.service as service_module
from agentic_trader.cli.commands.service import run_earnings_capture
from agentic_trader.config import EarningsCaptureConfig
from agentic_trader.data.earnings_capture import EarningsCaptureStore, default_capture_directory
from agentic_trader.diagnostics.readiness import HealthComponent


async def test_the_daemon_worker_observes_earnings_capture_with_the_default_directory(temp_db, app_config, monkeypatch):
    seen = []

    async def worker(config, store, observe, shutdown):
        assert isinstance(store, EarningsCaptureStore)
        assert store.workflows is temp_db.workflows
        assert store.directory == default_capture_directory()
        await observe(True, "latest capture")

    monkeypatch.setattr(service_module, "run_earnings_capture_worker", worker)

    async def observe(component, ok, detail):
        seen.append((component, ok, detail))

    copilot = SimpleNamespace(
        db=SimpleNamespace(workflows=temp_db.workflows),
        readiness=SimpleNamespace(observe=observe),
        _shutdown_event=asyncio.Event(),
    )
    config = app_config.model_copy(update={"earnings_capture": EarningsCaptureConfig(enabled=True)})
    await run_earnings_capture(copilot, config)
    assert seen == [(HealthComponent.EARNINGS_CAPTURE, True, "latest capture")]


async def test_the_daemon_starts_no_capture_task_when_disabled(app_config, monkeypatch):
    started = []

    async def worker(copilot, config):
        started.append(config.earnings_capture.enabled)

    monkeypatch.setattr(service_module, "run_earnings_capture", worker)
    off = app_config.model_copy(update={"earnings_capture": EarningsCaptureConfig(enabled=False)})
    on = app_config.model_copy(update={"earnings_capture": EarningsCaptureConfig(enabled=True)})
    assert service_module.start_earnings_capture(object(), off) is None
    task = service_module.start_earnings_capture(object(), on)
    assert task is not None
    await task
    assert started == [True]
