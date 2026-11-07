"""Daemon wiring for the release exit: one five-minute interval job, none while off or without a broker calendar."""

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agentic_trader.cli.commands import service
from agentic_trader.config import ReleaseExitConfig


def test_the_release_exit_job_runs_every_five_minutes_and_at_start(app_config):
    scheduler, copilot = MagicMock(), MagicMock()
    config = app_config.model_copy(update={"release_exit": ReleaseExitConfig(mode="preview")})
    service.register_release_exit_job(scheduler, copilot, config)

    [call] = scheduler.add_job.call_args_list
    assert call.args == (copilot.run_release_exit, "interval")
    assert call.kwargs["minutes"] == 5 and call.kwargs["id"] == "release_exit"
    next_run_time = call.kwargs["next_run_time"]
    assert isinstance(next_run_time, datetime) and next_run_time.tzinfo is UTC


def test_no_release_exit_job_while_off(app_config):
    scheduler = MagicMock()
    config = app_config.model_copy(update={"release_exit": ReleaseExitConfig(mode="off")})
    service.register_release_exit_job(scheduler, MagicMock(), config)
    scheduler.add_job.assert_not_called()


@pytest.mark.parametrize("mode", ["preview", "enforce"])
def test_no_release_exit_job_without_a_broker_calendar_and_one_startup_log(app_config, caplog, mode):
    """The simulator without Alpaca credentials has no broker trading calendar: no job, one log, no daily failure."""
    scheduler, copilot = MagicMock(), MagicMock()
    copilot.release_exit.calendar = None
    config = app_config.model_copy(update={"release_exit": ReleaseExitConfig(mode=mode)})
    with caplog.at_level(logging.WARNING):
        service.register_release_exit_job(scheduler, copilot, config)
    scheduler.add_job.assert_not_called()
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "release_exit_disabled"]
    assert record.__dict__["reason"] == "no_trading_calendar"
