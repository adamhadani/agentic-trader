"""Daemon wiring for the weekly scorecard: one 15-minute interval job, none while disabled."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from agentic_trader.cli.commands import service
from agentic_trader.config import ScorecardConfig


def test_the_scorecard_job_ticks_every_fifteen_minutes_and_at_start(app_config):
    scheduler, copilot = MagicMock(), MagicMock()
    service.register_scorecard_job(scheduler, copilot, app_config)

    [call] = scheduler.add_job.call_args_list
    assert call.args == (copilot.publish_weekly_scorecard, "interval")
    assert call.kwargs["minutes"] == 15 and call.kwargs["id"] == "weekly_scorecard"
    assert call.kwargs["max_instances"] == 1 and call.kwargs["coalesce"] is True
    next_run_time = call.kwargs["next_run_time"]
    assert isinstance(next_run_time, datetime) and next_run_time.tzinfo is UTC


def test_no_scorecard_job_while_disabled(app_config):
    scheduler = MagicMock()
    config = app_config.model_copy(update={"scorecard": ScorecardConfig(enabled=False)})
    service.register_scorecard_job(scheduler, MagicMock(), config)
    scheduler.add_job.assert_not_called()
