import pytest
from pydantic import ValidationError

from agentic_trader.config import RegimeConfig


def test_unknown_policy_defaults_are_half_size_with_breakouts_off():
    cfg = RegimeConfig()
    assert (cfg.unknown_risk_multiplier, cfg.unknown_breakout_allowed) == (0.5, False)


@pytest.mark.parametrize("value", [0.10, 0.5, 0.99])
def test_unknown_risk_multiplier_inside_the_clamp_is_accepted(value):
    assert RegimeConfig(unknown_risk_multiplier=value).unknown_risk_multiplier == value


@pytest.mark.parametrize("value", [1.0, 0.05, 1.5, 0.0])
def test_unknown_risk_multiplier_outside_the_clamp_is_refused(value):
    with pytest.raises(ValidationError):
        RegimeConfig(unknown_risk_multiplier=value)


def test_removed_toggles_in_an_old_yaml_are_ignored():
    cfg = RegimeConfig(credit_oas_enabled=True, inflation_breakeven_enabled=False)
    assert not hasattr(cfg, "credit_oas_enabled")
    assert not hasattr(cfg, "inflation_breakeven_enabled")
