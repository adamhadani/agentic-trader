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


def _config(*, mode=ExecutionMode.ALPACA, paper=True, lifetime="session", auto="paper", catalog=()) -> AppConfig:
    config = AppConfig(execution_mode=mode, alpaca_paper=paper)
    config.execution.native_entry_lifetime = lifetime
    config.execution.auto_entry = auto
    config.execution.auto_entry_catalog = catalog
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
        (_config(auto="off", catalog=("pead",)), "Auto-entry: off (catalog list inert)"),
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


# --- catalog sources (PEAD probe, insider watch): marked by source, no LLM approval, no native lifetime ----------


def _catalog_marker(config, *, source="pead", trigger=ScanTrigger.SUGGESTION_SCAN, alpha=False, llm_verdict=None):
    return auto_entry_marker(
        config,
        trigger=trigger,
        is_catalog=True,
        alpha=alpha,
        lifetime_marker=None,
        llm_verdict=llm_verdict,
        catalog_source=source,
    )


def test_code_default_enables_no_catalog_source():
    assert AppConfig().execution.auto_entry_catalog == ()
    assert _catalog_marker(_config()) is None


@pytest.mark.parametrize("source", ["pead", "insider_watch"])
def test_an_enabled_catalog_source_is_marked_without_an_llm_approval_or_native_lifetime(source):
    config = _config(catalog=("pead", "insider_watch"))

    assert _catalog_marker(config, source=source) == {"mode": "paper"}
    # Commentary (applied False), an erroring commentary call and a missing verdict never block it.
    assert _catalog_marker(config, source=source, llm_verdict={**APPROVED, "applied": False}) == {"mode": "paper"}
    assert _catalog_marker(config, source=source, llm_verdict={**APPROVED, "applied": False, "error": "T"}) == {
        "mode": "paper"
    }


def test_each_source_is_enabled_on_its_own():
    assert _catalog_marker(_config(catalog=("pead",)), source="insider_watch") is None
    assert _catalog_marker(_config(catalog=("insider_watch",)), source="pead") is None
    assert _catalog_marker(_config(catalog=("insider_watch",)), source="insider_watch") == {"mode": "paper"}


def test_a_catalog_source_outside_the_list_or_unnamed_gets_no_marker():
    config = _config(catalog=("pead",))

    assert _catalog_marker(config, source="exdiv") is None
    assert _catalog_marker(config, source=None) is None


@pytest.mark.parametrize(
    "config",
    [
        _config(catalog=("pead",), auto="off"),
        _config(catalog=("pead",), paper=False),
        _config(catalog=("pead",), mode=ExecutionMode.PAPER),
        _config(catalog=("pead",), lifetime="off"),
    ],
)
def test_an_inactive_account_never_marks_a_catalog_card(config):
    assert _catalog_marker(config) is None


@pytest.mark.parametrize("trigger", [ScanTrigger.SWING_SCAN, ScanTrigger.OPERATOR_SCAN])
def test_only_the_scheduled_suggestion_scan_marks_a_catalog_card(trigger):
    assert _catalog_marker(_config(catalog=("pead",)), trigger=trigger) is None


def test_an_alpha_card_is_never_marked_through_a_catalog_source():
    assert _catalog_marker(_config(catalog=("pead",)), alpha=True) is None


def test_a_native_card_still_needs_the_llm_whatever_the_catalog_list_says():
    config = _config(catalog=("pead", "insider_watch"))
    native = {"trigger": ScanTrigger.SUGGESTION_SCAN, "is_catalog": False, "alpha": False, "lifetime_marker": LIFETIME}

    assert auto_entry_marker(config, **native, llm_verdict=None) is None
    assert auto_entry_marker(config, **native, llm_verdict={**APPROVED, "applied": False}) is None
    assert auto_entry_marker(config, **native, llm_verdict=APPROVED) == {"mode": "paper"}
    # A native card (no catalog source) with no lifetime marker stays a tap card.
    assert auto_entry_marker(config, **{**native, "lifetime_marker": None}, llm_verdict=APPROVED) is None


def test_a_catalog_card_without_a_source_name_is_the_old_refusal():
    config = _config(catalog=("pead",))

    assert (
        auto_entry_marker(
            config,
            trigger=ScanTrigger.SUGGESTION_SCAN,
            is_catalog=True,
            alpha=False,
            lifetime_marker=LIFETIME,
            llm_verdict=APPROVED,
        )
        is None
    )
