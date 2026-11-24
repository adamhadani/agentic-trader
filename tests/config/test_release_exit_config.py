"""Release exit config: defaults, the YAML `off` spelling, bounds and the loader passing `release_exit:`."""

import textwrap

import pytest
import yaml
from pydantic import ValidationError

from agentic_trader.config import AppConfig, ReleaseExitConfig, load_config


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def test_defaults():
    cfg = ReleaseExitConfig()
    assert cfg.mode == "off" and cfg.minutes_before_close == 30
    assert AppConfig().release_exit == cfg


def test_a_bare_yaml_off_means_off_and_on_stays_invalid():
    assert ReleaseExitConfig(**yaml.safe_load("mode: off")).mode == "off"
    for mode in ("preview", "enforce"):
        assert ReleaseExitConfig(mode=mode).mode == mode
    with pytest.raises(ValidationError):
        ReleaseExitConfig(**yaml.safe_load("mode: on"))
    with pytest.raises(ValidationError):
        ReleaseExitConfig(mode="close")


def test_minutes_before_close_run_from_ten_to_one_hundred_twenty():
    assert ReleaseExitConfig(minutes_before_close=10).minutes_before_close == 10
    assert ReleaseExitConfig(minutes_before_close=120).minutes_before_close == 120
    for bad in (9, 121):
        with pytest.raises(ValidationError):
            ReleaseExitConfig(minutes_before_close=bad)


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError):
        ReleaseExitConfig(unknown=1)


def test_loader_passes_the_block(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """
            release_exit:
              mode: preview
              minutes_before_close: 45
            database: {path: test.db}
            """
        )
    )
    config = load_config(str(path), environ=PRODUCTION)
    assert config.release_exit.mode == "preview" and config.release_exit.minutes_before_close == 45
