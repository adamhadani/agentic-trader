"""The daemon's filings-capture composition: readiness component, the corporate-action symbols and the gate."""

import asyncio
from types import SimpleNamespace

import agentic_trader.cli.commands.service as service_module
from agentic_trader.cli.commands.service import run_filings_capture
from agentic_trader.config import FilingVetoConfig
from agentic_trader.data.filings_capture import FilingsCaptureStore
from agentic_trader.diagnostics.readiness import HealthComponent


async def test_the_daemon_worker_observes_filings_capture_over_the_corporate_action_symbols(
    temp_db, app_config, monkeypatch
):
    seen = []
    loaded = []

    async def symbols_of(copilot, config):
        loaded.append((copilot, config))
        return ["AAA", "BBB"]

    async def worker(config, store, observe, shutdown, *, symbols):
        assert isinstance(store, FilingsCaptureStore)
        assert store.workflows is temp_db.workflows
        assert await symbols() == ["AAA", "BBB"]
        await observe(True, "captured; latest pass")

    monkeypatch.setattr(service_module, "corporate_action_symbols", symbols_of)
    monkeypatch.setattr(service_module, "run_filings_capture_worker", worker)

    async def observe(component, ok, detail):
        seen.append((component, ok, detail))

    copilot = SimpleNamespace(
        db=SimpleNamespace(workflows=temp_db.workflows),
        readiness=SimpleNamespace(observe=observe),
        _shutdown_event=asyncio.Event(),
    )
    config = app_config.model_copy(update={"filing_veto": FilingVetoConfig(capture=True)})
    await run_filings_capture(copilot, config)
    assert seen == [(HealthComponent.FILINGS_CAPTURE, True, "captured; latest pass")]
    assert loaded == [(copilot, config)]


async def test_the_daemon_starts_no_capture_task_when_capture_is_off(app_config, monkeypatch):
    started = []

    async def worker(copilot, config):
        started.append(config.filing_veto.capture)

    monkeypatch.setattr(service_module, "run_filings_capture", worker)
    off = app_config.model_copy(update={"filing_veto": FilingVetoConfig(capture=False)})
    on = app_config.model_copy(update={"filing_veto": FilingVetoConfig(capture=True)})
    assert service_module.start_filings_capture(object(), off) is None
    task = service_module.start_filings_capture(object(), on)
    assert task is not None
    await task
    assert started == [True]
