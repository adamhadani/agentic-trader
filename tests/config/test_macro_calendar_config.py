"""RiskConfig's macro-calendar keys: defaults, validation, and the fixture config's synthetic calendar."""

import pytest
from pydantic import ValidationError

from agentic_trader.config import WORKSPACE_ROOT, RiskConfig, load_config


def test_risk_config_defaults_and_validation():
    risk = RiskConfig()
    assert risk.macro_calendar_path == WORKSPACE_ROOT / "config/calendar/us-tier1-v1.json"
    assert (risk.macro_calendar_sha256, risk.macro_calendar_unknown, risk.macro_calendar_renewal_days) == (
        None,
        "open",
        30,
    )
    assert RiskConfig(macro_calendar_sha256="a" * 64).macro_calendar_sha256 == "a" * 64
    assert RiskConfig(macro_calendar_unknown="closed").macro_calendar_unknown == "closed"
    for bad in (
        {"macro_calendar_sha256": "ABC"},
        {"macro_calendar_sha256": "A" * 64},
        {"macro_calendar_unknown": "maybe"},
        {"macro_calendar_renewal_days": 0},
    ):
        with pytest.raises(ValidationError):
            RiskConfig(**bad)


def test_a_relative_calendar_path_resolves_against_the_workspace_and_an_absolute_one_is_kept(tmp_path):
    assert RiskConfig(macro_calendar_path="calendars/x.json").macro_calendar_path == WORKSPACE_ROOT / "calendars/x.json"
    assert RiskConfig(macro_calendar_path=tmp_path / "x.json").macro_calendar_path == tmp_path / "x.json"


def test_fixture_config_uses_the_synthetic_calendar():
    risk = load_config().risk
    assert risk.macro_calendar_path.name == "us-tier1-test-v1.json"
    assert risk.macro_calendar_path.exists()
    assert risk.macro_calendar_sha256 is None
