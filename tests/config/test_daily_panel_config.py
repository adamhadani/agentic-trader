"""Prospective daily research is opt-in and takes source identity from its protocol."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.config import AlphaPipelineConfig, DailyPanelWorkerConfig


def test_daily_panel_defaults_do_not_start_collection_or_select_a_protocol():
    policy = AlphaPipelineConfig().daily_panel
    assert policy == DailyPanelWorkerConfig()
    assert not policy.enabled and policy.protocol_path is None
    assert policy.poll_seconds == 60 and policy.max_age_seconds == 1200
    assert policy.calendar_refresh_seconds == 300


def test_daily_panel_enrollment_requires_an_explicit_protocol_path():
    policy = DailyPanelWorkerConfig(enabled=True, protocol_path="config/research/example.json")
    assert policy.protocol_path == Path("config/research/example.json")


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": True},
        {"protocol_path": ""},
        {"protocol_path": "   "},
        {"poll_seconds": True},
        {"poll_seconds": 5},
        {"poll_seconds": 301},
        {"max_age_seconds": 59},
        {"max_age_seconds": 3601},
        {"poll_seconds": 120, "max_age_seconds": 60},
        {"calendar_refresh_seconds": 29},
        {"calendar_refresh_seconds": 3601},
        {"feed": "alpaca:sip"},
    ],
)
def test_daily_panel_rejects_implicit_or_unbounded_policy(values):
    with pytest.raises(ValidationError):
        DailyPanelWorkerConfig(**values)
