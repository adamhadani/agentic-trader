from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest
from pydantic import ValidationError

from agentic_trader.config import WORKSPACE_ROOT, load_config
from agentic_trader.research.setups.bracket_counterfactual import (
    HYPOTHESES,
    LIVE_ARM_NAME,
    ArmSpec,
    BracketCounterfactualProtocol,
)
from agentic_trader.research.setups.features import SECTOR_ETF
from agentic_trader.research.setups.labels import LIVE_ARM, BracketArm, TrailSpec


_TRAIL = {"trigger_r": 1.5, "distance_r": 1.5}


def _arms():
    return [
        {"name": "A0_live", "target_r": "planned", "trail": None},
        {"name": "A1_3r", "target_r": 3.0, "trail": None},
        {"name": "A2_2r_trail", "target_r": 2.0, "trail": dict(_TRAIL)},
        {"name": "A3_3r_trail", "target_r": 3.0, "trail": dict(_TRAIL)},
        {"name": "A4_trail_only", "target_r": None, "trail": dict(_TRAIL)},
    ]


def _protocol_kwargs(**overrides):
    base = {
        "version": "bracket_counterfactual_v1",
        "window": ["2021-06-01", "2026-01-29"],
        "data_cutoff": "2026-09-22",
        "robustness_window": ["2017-01-03", "2021-04-30"],
        "robustness_data_cutoff": "2021-06-15",
        "confirmation_window": ["2026-03-02", "2026-08-14"],
        "confirmation_data_cutoff": "2026-09-22",
        "confirmation_prior_use": "prior",
        "embargo_sessions": 20,
        "scan_times_et": ["10:35", "14:35"],
        "max_hold_sessions": 20,
        "cost_bps_per_side": [0.0, 5.0],
        "arms": _arms(),
        "r_cap": 10.0,
        "bootstrap": {"block_mean": 10, "draws": 2000, "seed": 20261018},
        "holm_alpha": 0.05,
        "min_setups": 3000,
        "min_sessions": 600,
        "min_mature_fraction": 0.95,
        "min_divergent": 300,
        "feed": "alpaca:sip",
        "adjustment": "all",
        "strategy_config": {"x": 1},
        "sector_etf": {"technology": "XLK"},
        "hypotheses": "h",
        "decision_rule": "d",
    }
    base.update(overrides)
    return base


def _protocol(**overrides):
    return BracketCounterfactualProtocol(**_protocol_kwargs(**overrides))


def test_constants():
    assert HYPOTHESES == ("B1", "B2", "B3", "B4", "B5")
    assert LIVE_ARM_NAME == "A0_live"


def test_protocol_identity_stable_and_sensitive():
    assert _protocol().identity == _protocol().identity
    assert len(_protocol().identity) == 64
    assert _protocol(r_cap=9.0).identity != _protocol().identity
    assert _protocol().document()["window"] == ["2021-06-01", "2026-01-29"]


def _swap(arms, i, j):
    arms = [dict(a) for a in arms]
    arms[i], arms[j] = arms[j], arms[i]
    return arms


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"data_cutoff": "2026-02-10"}, "data_cutoff"),
        ({"robustness_data_cutoff": "2021-05-10"}, "data_cutoff"),
        ({"confirmation_data_cutoff": "2026-08-20"}, "data_cutoff"),
        (
            {"robustness_window": ["2017-01-03", "2021-05-20"], "robustness_data_cutoff": "2021-07-30"},
            "one label horizon",
        ),
        ({"confirmation_window": ["2026-02-10", "2026-08-14"]}, "embargo"),
        ({"arms": _arms()[1:]}, "A0_live"),
        ({"arms": _swap(_arms(), 0, 1)}, "A0_live"),
        ({"arms": [{**_arms()[0], "target_r": 2.0}, *_arms()[1:]]}, "A0_live"),
        ({"arms": [{**_arms()[0], "trail": dict(_TRAIL)}, *_arms()[1:]]}, "A0_live"),
        ({"arms": [*_arms()[:1], {"name": "A1_3r", "target_r": "planned", "trail": None}, *_arms()[2:]]}, "A0_live"),
        ({"arms": [*_arms(), {"name": "A1_3r", "target_r": 4.0, "trail": None}]}, "unique"),
        ({"r_cap": 0.0}, "r_cap"),
        ({"holm_alpha": 0.6}, "holm_alpha"),
        ({"max_hold_sessions": 0}, "max_hold_sessions"),
        ({"cost_bps_per_side": []}, "cost_bps_per_side"),
        ({"arms": [*_arms()[:2], {"name": "A2", "target_r": 2.0, "trail": {"trigger_r": 1.5}}]}, "trail"),
        (
            {"arms": [*_arms()[:2], {"name": "A2", "target_r": 2.0, "trail": {"trigger_r": 0.0, "distance_r": 1.5}}]},
            "trail",
        ),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_protocol_rejects_each_bad_shape(overrides, match):
    with pytest.raises(ValidationError, match=match):
        _protocol(**overrides)


def test_bracket_arms_maps_planned_to_live_arm_and_dicts_to_trailspec():
    protocol = _protocol()
    arms = protocol.bracket_arms()
    assert arms[0] is LIVE_ARM
    assert arms[1] == BracketArm("A1_3r", 3.0, None)
    assert arms[2].trail == TrailSpec(1.5, 1.5) and arms[2].target_r == 2.0
    assert arms[4].target_r is None and arms[4].trail == TrailSpec(1.5, 1.5)
    assert protocol.counterfactual_arm_names() == ("A1_3r", "A2_2r_trail", "A3_3r_trail", "A4_trail_only")
    assert ArmSpec(name="A0_live", target_r="planned", trail=None).to_arm() is LIVE_ARM


def test_frozen_protocol_file_matches_the_spec_and_the_loaded_config():
    path = WORKSPACE_ROOT / "config" / "research" / "bracket-counterfactual-v1.json"
    # File SHA-256 prefix at freeze time: recorded in the task report, deliberately not asserted.
    _ = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    protocol = BracketCounterfactualProtocol(**json.loads(path.read_text()))
    assert protocol.version == "bracket_counterfactual_v1"
    assert protocol.window == (date(2021, 6, 1), date(2026, 1, 29)) and protocol.data_cutoff == date(2026, 9, 22)
    assert protocol.robustness_window == (date(2017, 1, 3), date(2021, 4, 30))
    assert protocol.robustness_data_cutoff == date(2021, 6, 15)
    assert protocol.confirmation_window == (date(2026, 3, 2), date(2026, 8, 14))
    assert protocol.confirmation_data_cutoff == date(2026, 9, 22)
    assert protocol.confirmation_prior_use == (
        "setup-outcomes-v1 one-shot holdout, read once on 2026-09-23 for the shadow ranker's acceptance test "
        "(docs/setup-outcomes-2026-09-23.md); declared second use, different hypothesis, read at most once "
        "for at most one arm"
    )
    assert protocol.embargo_sessions == 20 and protocol.scan_times_et == ("10:35", "14:35")
    assert protocol.max_hold_sessions == 20 and protocol.cost_bps_per_side == (0.0, 5.0)
    assert [(a.name, a.target_r, a.trail) for a in protocol.arms] == [
        ("A0_live", "planned", None),
        ("A1_3r", 3.0, None),
        ("A2_2r_trail", 2.0, _TRAIL),
        ("A3_3r_trail", 3.0, _TRAIL),
        ("A4_trail_only", None, _TRAIL),
    ]
    assert protocol.r_cap == 10.0 and protocol.holm_alpha == 0.05
    assert protocol.bootstrap == {"block_mean": 10, "draws": 2000, "seed": 20261018}
    assert (protocol.min_setups, protocol.min_sessions, protocol.min_mature_fraction, protocol.min_divergent) == (
        3000,
        600,
        0.95,
        300,
    )
    assert protocol.feed == "alpaca:sip" and protocol.adjustment == "all"
    deployed = load_config(str(WORKSPACE_ROOT / "config" / "config.yaml"))
    assert protocol.strategy_config == deployed.strategies.model_dump(mode="json")
    assert dict(protocol.sector_etf) == dict(SECTOR_ETF)
    assert protocol.strategy_config["trend_pullback"]["allow_short"] is False
    assert "B5" in protocol.hypotheses and "eligible_bracket_change" in protocol.decision_rule
