from __future__ import annotations

import math
from datetime import UTC, date, datetime
from types import SimpleNamespace

from agentic_trader.agent.macro import MacroInput, MacroStressLevel
from agentic_trader.agent.regime import REGIME_BLOCK_KEYS, RegimeSnapshot, regime_journal_block
from agentic_trader.constants import VolatilityRegime


def _snapshot(**overrides):
    fields = {
        "vix": 17.25,
        "vix_regime": VolatilityRegime.NORMAL,
        "tnx": 4.1,
        "dxy": 102.3,
        "breakout_allowed": True,
        "min_rr_threshold": 1.5,
        "timestamp": datetime(2026, 10, 14, 14, 35, tzinfo=UTC),
        "summary_text": "VIX: 17.25 (NORMAL)",
        "macro_report": None,
        "risk_multiplier": 0.9,
        "macro_unavailable_reason": None,
    }
    return RegimeSnapshot(**{**fields, **overrides})


def test_block_has_exactly_the_declared_keys_and_json_safe_values():
    stress = SimpleNamespace(level=MacroStressLevel.MODERATE)
    vix = MacroInput("vix", 17.25, "yfinance:^VIX", date(2026, 10, 13), True, None)
    block = regime_journal_block(_snapshot(macro_stress=stress, macro_inputs={"vix": vix}))
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert block == {
        "vix": 17.25,
        "vix_regime": "NORMAL",
        "breakout_allowed": True,
        "risk_multiplier": 0.9,
        "min_rr_threshold": 1.5,
        "macro_stress": "MODERATE",
        "observed_at": "2026-10-14T14:35:00+00:00",
        "macro_enabled": True,
        "inputs": {
            "vix": {
                "value": 17.25,
                "source": "yfinance:^VIX",
                "observed_on": "2026-10-13",
                "decision": True,
                "error": None,
            }
        },
        "missing": [],
        "missing_decision": [],
        "clock_regime": "EDT",
    }


def test_block_without_macro_stress_has_no_stress():
    block = regime_journal_block(_snapshot())
    assert block["macro_stress"] is None and block["macro_enabled"] is False
    assert block["vix_regime"] == "NORMAL" and block["breakout_allowed"] is True
    assert (block["inputs"], block["missing"], block["missing_decision"]) == ({}, [], [])


def test_block_from_a_stub_is_all_none():
    """The scan fixture's detector returns a namespace with only summary_text; nothing may raise."""
    block = regime_journal_block(SimpleNamespace(summary_text="test"))
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert all(value is None for value in block.values())


def test_non_finite_and_non_numeric_values_are_none():
    block = regime_journal_block(_snapshot(vix=math.nan, risk_multiplier="x", min_rr_threshold=math.inf))
    assert block["vix"] is None and block["risk_multiplier"] is None and block["min_rr_threshold"] is None


def test_a_raising_attribute_yields_an_all_none_block():
    class Broken:
        @property
        def vix(self):
            raise RuntimeError("boom")

    block = regime_journal_block(Broken())
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert all(value is None for value in block.values())
