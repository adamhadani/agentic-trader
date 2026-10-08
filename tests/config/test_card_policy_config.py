"""The operator's card policy: off by default, preview before enforce, wired through load_config."""

import textwrap
from pathlib import Path

import pytest
import yaml

from agentic_trader.config import AppConfig, CardPolicyConfig, load_config


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}
REPO = Path(__file__).resolve().parents[2]


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_defaults_are_off_and_unchanged_validity():
    policy = CardPolicyConfig()
    assert policy.mode == "off" and policy.min_measured_ev is None
    assert policy.min_mature_cards == 20 and policy.stats_window_days == 90
    assert policy.stats_time_et == "08:30" and policy.stats_poll_seconds == 300
    assert policy.stats_max_age_seconds == 345600
    assert policy.validity == "session_close"
    assert AppConfig().card_policy == policy


@pytest.mark.parametrize("mode", ["preview", "enforce"])
def test_an_active_mode_requires_a_threshold(mode):
    with pytest.raises(ValueError, match="min_measured_ev"):
        CardPolicyConfig(mode=mode)
    assert CardPolicyConfig(mode=mode, min_measured_ev=0.0).min_measured_ev == 0.0


@pytest.mark.parametrize(
    "fields",
    [
        {"unknown_key": 1},
        {"mode": "on"},
        {"validity": "forever"},
        {"stats_time_et": "8:30"},
        {"stats_time_et": "24:00"},
        {"min_mature_cards": 0},
        {"min_measured_ev": float("nan"), "mode": "enforce"},
        {"stats_poll_seconds": 5},
        {"stats_max_age_seconds": 300},  # not longer than the poll interval
    ],
)
def test_invalid_blocks_are_rejected(fields):
    with pytest.raises(ValueError):
        CardPolicyConfig(**fields)


def test_load_config_round_trips_the_card_policy_block(tmp_path):
    path = write(
        tmp_path,
        """
        card_policy:
          mode: preview
          min_measured_ev: -0.05
          min_mature_cards: 25
          stats_window_days: 60
          stats_time_et: "07:45"
          stats_poll_seconds: 120
          stats_max_age_seconds: 259200
          validity: next_session_close
        """,
    )
    config = load_config(path, environ=PRODUCTION)
    assert config.card_policy == CardPolicyConfig(
        mode="preview",
        min_measured_ev=-0.05,
        min_mature_cards=25,
        stats_window_days=60,
        stats_time_et="07:45",
        stats_poll_seconds=120,
        stats_max_age_seconds=259200,
        validity="next_session_close",
    )


def test_an_unquoted_yaml_off_means_off(tmp_path):
    # YAML 1.1 reads a bare `off` as false; it must still mean "off".
    config = load_config(write(tmp_path, "card_policy:\n  mode: off\n"), environ=PRODUCTION)
    assert config.card_policy.mode == "off"


def test_an_unquoted_yaml_on_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, "card_policy:\n  mode: on\n  min_measured_ev: 0.0\n"), environ=PRODUCTION)


def test_unknown_keys_fail_the_load(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, "card_policy:\n  min_measured_r: 0.0\n"), environ=PRODUCTION)


@pytest.mark.parametrize("body", ["card_policy:\n", "contracts: {}\n"])
def test_a_bare_or_missing_block_loads_the_defaults(tmp_path, body):
    assert load_config(write(tmp_path, body), environ=PRODUCTION).card_policy == CardPolicyConfig()


def test_the_shipped_config_keeps_the_policy_off():
    shipped = yaml.safe_load((REPO / "config" / "config.yaml").read_text())["card_policy"]
    assert CardPolicyConfig(**shipped) == CardPolicyConfig()
