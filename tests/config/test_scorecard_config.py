"""Weekly scorecard config: code defaults, validation, and the loader passing the `scorecard:` block."""

import textwrap

import pytest

from agentic_trader.config import AppConfig, ScorecardConfig, load_config


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_defaults_match_the_spec():
    cfg = ScorecardConfig()
    assert cfg.enabled is True
    assert (cfg.due_weekday, cfg.due_time_et, cfg.activity_days, cfg.record_days) == (5, "09:00", 7, 90)
    assert AppConfig().scorecard == cfg


@pytest.mark.parametrize(
    "bad",
    [
        {"due_weekday": -1},
        {"due_weekday": 7},
        {"due_time_et": "9:00"},
        {"due_time_et": "25:00"},
        {"due_time_et": "09:60"},
        {"activity_days": 0},
        {"activity_days": 91},
        {"record_days": 6},
        {"record_days": 181},
        {"activity_days": 30, "record_days": 20},
        {"unknown": 1},
    ],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValueError):
        ScorecardConfig(**bad)


def test_loader_passes_the_scorecard_block(tmp_path):
    path = write(
        tmp_path,
        """
        scorecard: {enabled: false, due_weekday: 6, due_time_et: "18:30", activity_days: 14, record_days: 60}
        database: {path: test.db}
        """,
    )
    config = load_config(path, environ=PRODUCTION)
    assert config.scorecard == ScorecardConfig(
        enabled=False, due_weekday=6, due_time_et="18:30", activity_days=14, record_days=60
    )


def test_loader_without_the_block_keeps_the_defaults(tmp_path):
    config = load_config(write(tmp_path, "database: {path: test.db}\n"), environ=PRODUCTION)
    assert config.scorecard == ScorecardConfig()
