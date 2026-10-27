"""Daemon wiring for paper auto-entry: the suggestion job's ``finally`` sweep and the interval job."""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.cli.commands import service
from agentic_trader.constants import ExecutionMode


def _active(config):
    config.execution_mode = ExecutionMode.ALPACA
    config.alpaca_paper = True
    config.execution.native_entry_lifetime = "session"
    config.execution.auto_entry = "paper"
    return config


def _job(config, *, run_scan: AsyncMock, sweep: AsyncMock, digest: AsyncMock, order: list[str]):
    copilot = MagicMock()
    copilot.config = config
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    copilot.run_scan = run_scan
    copilot.auto_enter_pending = sweep
    copilot.publish_scan_digest = digest
    sweep.side_effect = sweep.side_effect or (lambda *a, **k: order.append("sweep"))
    digest.side_effect = digest.side_effect or (lambda *a, **k: order.append("digest"))
    return service.make_suggestion_scan(copilot, use_llm=False)


async def test_suggestion_job_sweeps_in_finally_even_when_run_scan_raises(config):
    order: list[str] = []
    sweep, digest = AsyncMock(), AsyncMock()
    job = _job(
        _active(config), run_scan=AsyncMock(side_effect=RuntimeError("scan")), sweep=sweep, digest=digest, order=order
    )

    with pytest.raises(RuntimeError, match="scan"):
        await job(digest=True, scheduled_time_et="14:35")

    sweep.assert_awaited_once_with()
    assert order == ["sweep", "digest"]  # the sweep runs before the end-of-session digest


async def test_suggestion_job_sweeps_after_a_clean_scan(config):
    order: list[str] = []
    sweep = AsyncMock()
    job = _job(_active(config), run_scan=AsyncMock(), sweep=sweep, digest=AsyncMock(), order=order)

    await job(digest=False, scheduled_time_et="10:35")

    sweep.assert_awaited_once_with()
    assert order == ["sweep"]


async def test_a_sweep_failure_never_breaks_the_digest_or_masks_the_scan(config, caplog):
    order: list[str] = []
    digest = AsyncMock()
    job = _job(
        _active(config),
        run_scan=AsyncMock(side_effect=RuntimeError("scan")),
        sweep=AsyncMock(side_effect=ValueError("sweep")),
        digest=digest,
        order=order,
    )

    with caplog.at_level(logging.ERROR, logger="copilot"), pytest.raises(RuntimeError, match="scan"):
        await job(digest=True, scheduled_time_et="14:35")

    digest.assert_awaited_once()
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "auto_entry_sweep_failed"]
    assert record.exc_info is not None and record.exc_info[0] is ValueError


async def test_no_sweep_when_auto_entry_is_inactive(config):
    config.execution.auto_entry = "off"
    sweep = AsyncMock()
    job = _job(config, run_scan=AsyncMock(), sweep=sweep, digest=AsyncMock(), order=[])

    await job(digest=True, scheduled_time_et="14:35")

    sweep.assert_not_awaited()


def test_auto_entry_jobs_registered_only_when_active(config):
    inactive = MagicMock()
    config.execution.auto_entry = "off"
    service.register_auto_entry_jobs(inactive, MagicMock(), config)
    inactive.add_job.assert_not_called()

    scheduler, copilot = MagicMock(), MagicMock()
    service.register_auto_entry_jobs(scheduler, copilot, _active(config))

    scheduler.add_job.assert_called_once()
    call = scheduler.add_job.call_args
    assert call.args[0] is copilot.auto_enter_pending
    assert call.args[1] == "interval"
    assert call.kwargs["minutes"] == 1
    assert call.kwargs["id"] == "auto_entry_sweep"
    next_run_time = call.kwargs["next_run_time"]
    assert isinstance(next_run_time, datetime) and next_run_time.tzinfo is UTC  # also runs at daemon start


def test_the_simulator_registers_no_auto_entry_job(config):
    _active(config).execution_mode = ExecutionMode.PAPER
    scheduler = MagicMock()
    service.register_auto_entry_jobs(scheduler, MagicMock(), config)
    scheduler.add_job.assert_not_called()
