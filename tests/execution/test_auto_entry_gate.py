"""The auto-entry switch: one resolver and one card marker rule.

A card is marked only when an LLM evaluated it and approved it: its verdict present, applied, ``approved`` exactly
``True`` and error-free. A ``--no-llm`` scan has no verdict, so its native cards are ordinary tap cards.
"""

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
# The scan's ``llm`` block of a native card the LLM approved (``TradingCopilot._llm_block``).
APPROVED = {
    "approved": True,
    "rejection_reason": None,
    "stop_loss": 98.0,
    "take_profit": 104.0,
    "applied": True,
    "model": "gpt-5.6-sol",
    "prompt_sha256": "ab" * 32,
    "error": None,
    "malformed": False,
}


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
    marker = auto_entry_marker(
        _config(), trigger=trigger, is_catalog=is_catalog, alpha=alpha, lifetime_marker=lifetime, llm_verdict=APPROVED
    )

    assert marker == expected


@pytest.mark.parametrize(
    ("llm_verdict", "expected"),
    [
        (APPROVED, {"mode": "paper"}),
        # No LLM saw the card: a `--no-llm` (deterministic) scan, or an evaluation without a verdict.
        (None, None),
        # The verdict was commentary, not the gate.
        ({**APPROVED, "applied": False}, None),
        # The LLM vetoed it.
        ({**APPROVED, "approved": False, "rejection_reason": "thesis weak"}, None),
        # The gate errored: never marked, whatever its approval says.
        ({**APPROVED, "error": "Timeout"}, None),
        ({**APPROVED, "approved": False, "error": "LLMUnavailableEarlierInScan", "model": None}, None),
        # Only the boolean True approves.
        ({**APPROVED, "approved": "true"}, None),
        ({**APPROVED, "approved": 1}, None),
        ({**APPROVED, "applied": 1}, None),
        ({key: value for key, value in APPROVED.items() if key != "approved"}, None),
        ({key: value for key, value in APPROVED.items() if key != "applied"}, None),
    ],
)
def test_auto_entry_marker_requires_an_applied_error_free_llm_approval(llm_verdict, expected):
    marker = auto_entry_marker(
        _config(),
        trigger=ScanTrigger.SUGGESTION_SCAN,
        is_catalog=False,
        alpha=False,
        lifetime_marker=LIFETIME,
        llm_verdict=llm_verdict,
    )

    assert marker == expected


def test_marker_is_a_fresh_copy_and_inactive_config_gives_none():
    marker = auto_entry_marker(
        _config(),
        trigger=ScanTrigger.SUGGESTION_SCAN,
        is_catalog=False,
        alpha=False,
        lifetime_marker=LIFETIME,
        llm_verdict=APPROVED,
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
            llm_verdict=APPROVED,
        )
        is None
    )
