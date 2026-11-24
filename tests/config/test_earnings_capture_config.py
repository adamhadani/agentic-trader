"""Earnings capture config: defaults, validation and the loader passing `earnings_capture:`."""

import textwrap

import pytest

from agentic_trader.config import AppConfig, EarningsCaptureConfig, load_config


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def test_defaults():
    cfg = EarningsCaptureConfig()
    assert cfg.enabled is False
    assert cfg.horizon_days == 28
    assert AppConfig().earnings_capture == cfg


@pytest.mark.parametrize("bad", [{"horizon_days": 6}, {"horizon_days": 43}, {"unknown": 1}])
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValueError):
        EarningsCaptureConfig(**bad)


def test_loader_passes_the_block(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """
            earnings_capture: {enabled: true, horizon_days: 21}
            database: {path: test.db}
            """
        )
    )
    config = load_config(str(path), environ=PRODUCTION)
    assert config.earnings_capture.enabled is True
    assert config.earnings_capture.horizon_days == 21
