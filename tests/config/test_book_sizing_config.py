import pytest
from pydantic import ValidationError

from agentic_trader.config import BookSizingConfig


def test_defaults_are_preview_with_the_agreed_budget():
    cfg = BookSizingConfig()
    assert cfg.mode == "preview"
    assert cfg.max_portfolio_daily_vol_pct == 0.008
    assert cfg.lookback_sessions == 120
    assert cfg.min_observations == 60
    assert cfg.shadow_optimizer is True


def test_yaml_false_means_off():
    assert BookSizingConfig(mode=False).mode == "off"


@pytest.mark.parametrize(
    "bad",
    [
        {"max_portfolio_daily_vol_pct": 0.0},
        {"max_portfolio_daily_vol_pct": 0.5},
        {"lookback_sessions": 30, "min_observations": 60},
        {"min_observations": 5},
        {"unknown": 1},
    ],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        BookSizingConfig(**bad)
