"""The auto-entry switch: one resolver and one card marker rule."""

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.execution.auto_entry import (
    AUTO_ENTRY_KEY,
    AUTO_ENTRY_MARKER,
    auto_entry_active,
    auto_entry_marker,
    auto_entry_status_line,
)
from agentic_trader.execution.durable import ScanTrigger


LIFETIME = {"version": 3}


def _config(*, mode=ExecutionMode.ALPACA, paper=True, lifetime="session", auto="paper") -> AppConfig:
    config = AppConfig(execution_mode=mode, alpaca_paper=paper)
    config.execution.native_entry_lifetime = lifetime
    config.execution.auto_entry = auto
    return config


@pytest.mark.parametrize(
    ("config", "active"),
    [
        (_config(mode=ExecutionMode.PAPER), False),
        (_config(), True),
        (_config(lifetime="off"), False),
        (_config(paper=False), False),
        (_config(auto="off"), False),
    ],
)
def test_auto_entry_active_truth_table(config, active):
    assert auto_entry_active(config) is active


@pytest.mark.parametrize(
    ("config", "line"),
    [
        (_config(), "Auto-entry: paper (on)"),
        (_config(auto="off"), "Auto-entry: off"),
        (_config(mode=ExecutionMode.PAPER), "Auto-entry: inactive (not an Alpaca paper account)"),
        (_config(lifetime="off"), "Auto-entry: inactive (not an Alpaca paper account)"),
    ],
)
def test_status_shows_auto_entry_line(config, line):
    assert auto_entry_status_line(config) == line


def test_constants():
    assert AUTO_ENTRY_KEY == "auto_entry" and AUTO_ENTRY_MARKER == {"mode": "paper"}


@pytest.mark.parametrize(
    ("trigger", "is_catalog", "alpha", "lifetime", "expected"),
    [
        (ScanTrigger.SWING_SCAN, False, False, LIFETIME, None),
        (ScanTrigger.OPERATOR_SCAN, False, False, LIFETIME, None),
        (ScanTrigger.SUGGESTION_SCAN, True, False, LIFETIME, None),
        (ScanTrigger.SUGGESTION_SCAN, False, True, LIFETIME, None),
        (ScanTrigger.SUGGESTION_SCAN, False, False, None, None),
        (ScanTrigger.SUGGESTION_SCAN, False, False, LIFETIME, {"mode": "paper"}),
    ],
)
def test_auto_entry_marker_only_for_native_suggestion_cards(trigger, is_catalog, alpha, lifetime, expected):
    marker = auto_entry_marker(_config(), trigger=trigger, is_catalog=is_catalog, alpha=alpha, lifetime_marker=lifetime)

    assert marker == expected


def test_marker_is_a_fresh_copy_and_inactive_config_gives_none():
    marker = auto_entry_marker(
        _config(), trigger=ScanTrigger.SUGGESTION_SCAN, is_catalog=False, alpha=False, lifetime_marker=LIFETIME
    )
    marker["mode"] = "x"
    assert AUTO_ENTRY_MARKER == {"mode": "paper"}
    assert (
        auto_entry_marker(
            _config(auto="off"),
            trigger=ScanTrigger.SUGGESTION_SCAN,
            is_catalog=False,
            alpha=False,
            lifetime_marker=LIFETIME,
        )
        is None
    )
