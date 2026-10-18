from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from agentic_trader.cli.commands import alpha as alpha_cmd
from agentic_trader.cli.main import cli
from agentic_trader.config import WORKSPACE_ROOT, load_config
from agentic_trader.research.setups.bracket_counterfactual import (
    HYPOTHESES,
    LIVE_ARM_NAME,
    ArmSpec,
    BracketCounterfactualProtocol,
    confirmation,
    discovery,
    execute_bracket_counterfactual,
    holm,
    pair_arms,
    paired_contrast,
    sample_floors,
    winsorized,
)
from agentic_trader.research.setups.features import SECTOR_ETF
from agentic_trader.research.setups.labels import LIVE_ARM, BracketArm, TrailSpec
from agentic_trader.research.setups.runner import ARM_FRAME_COLUMNS


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


# ---------------------------------------------------------------- statistics (Task 4)

_ARM_NAMES = ("A0_live", "A1_3r", "A2_2r_trail", "A3_3r_trail", "A4_trail_only")


def _long(n_sessions=40, arms=_ARM_NAMES, per_session=3, seed=7, offsets=None, hits=None, exit_shift=None, market=0.0):
    """Synthetic long per-arm frame: every arm has a row for every setup.

    ``offsets[arm]`` is added to a shared noise draw (so arms are perfectly paired), ``hits[arm]`` sets
    the hit string and ``exit_shift[arm]`` shifts the exit time by that many hours.
    """
    offsets, hits, exit_shift = offsets or {}, hits or {}, exit_shift or {}
    rng = np.random.default_rng(seed)
    sessions = pd.bdate_range("2024-01-02", periods=n_sessions)
    rows = []
    for session in sessions:
        for k in range(per_session):
            base = float(rng.normal(0.0, 1.0))
            decision = session + pd.Timedelta(hours=10, minutes=35)
            for arm in arms:
                r_cost = base + offsets.get(arm, 0.0)
                rows.append(
                    {
                        "decision_at": decision,
                        "session": session.date(),
                        "symbol": f"S{k}",
                        "strategy": "trend_pullback",
                        "timeframe": "1d",
                        "direction": "long",
                        "arm": arm,
                        "hit": hits.get(arm, "target"),
                        "exit_time": decision + pd.Timedelta(days=2, hours=exit_shift.get(arm, 0)),
                        "r": r_cost,
                        "r_cost": r_cost,
                        "market_r": market,
                        "excess_r": r_cost - market,
                    }
                )
    return pd.DataFrame(rows)


def _stat_protocol(**overrides):
    kwargs = {"bootstrap": {"block_mean": 5, "draws": 200, "seed": 11}}
    kwargs.update(overrides)
    return _protocol(**kwargs)


def test_winsorized_clips_statistics_only():
    frame = _long(n_sessions=3, arms=("A0_live",), per_session=1)
    frame.loc[0, ["r", "r_cost"]] = 1546.0
    frame.loc[1, ["r", "r_cost"]] = -50.0
    frame.loc[1, "market_r"] = np.nan
    before = frame.copy()
    out = winsorized(frame, 10.0)
    assert out.loc[0, "r"] == 10.0 and out.loc[0, "r_cost"] == 10.0 and out.loc[0, "excess_r"] == 10.0
    assert out.loc[1, "r_cost"] == -10.0 and np.isnan(out.loc[1, "excess_r"])
    pd.testing.assert_frame_equal(frame, before)


def test_pair_arms_inner_joins_resolved_rows_only():
    frame = _long(n_sessions=5, arms=("A0_live", "A1_3r"), per_session=2)
    victim = frame[(frame["arm"] == "A1_3r")].index[0]
    frame.loc[victim, ["hit", "r", "r_cost", "excess_r"]] = ["immature", np.nan, np.nan, np.nan]
    pairs = pair_arms(frame, "A0_live", "A1_3r")
    assert len(pairs) == 9
    assert list(pairs.columns) == [
        "session",
        "excess_live",
        "excess_other",
        "r_cost_live",
        "r_cost_other",
        "divergent",
    ]


def test_divergent_counts_differing_hit_or_exit_time():
    frame = _long(
        n_sessions=4,
        arms=("A0_live", "A1_3r", "A2_2r_trail"),
        per_session=1,
        hits={"A1_3r": "stop"},
        exit_shift={"A2_2r_trail": 5},
    )
    assert pair_arms(frame, "A0_live", "A1_3r")["divergent"].all()
    assert pair_arms(frame, "A0_live", "A2_2r_trail")["divergent"].all()
    same = _long(n_sessions=4, arms=("A0_live", "A1_3r"), per_session=1)
    assert not pair_arms(same, "A0_live", "A1_3r")["divergent"].any()


def test_paired_contrast_identical_arms_is_exactly_zero_with_zero_width_ci():
    pairs = pair_arms(_long(arms=("A0_live", "A1_3r")), "A0_live", "A1_3r")
    out = paired_contrast(pairs, _stat_protocol())
    assert out["mean_diff"] == 0.0 and out["ci90"] == [0.0, 0.0]
    assert out["n_pairs"] == 120 and out["n_sessions"] == 40 and out["n_divergent"] == 0
    assert out["finite_draws"] == 200


def test_paired_contrast_recovers_a_planted_advantage():
    frame = _long(arms=("A0_live", "A1_3r"), offsets={"A1_3r": 0.2})
    out = paired_contrast(pair_arms(frame, "A0_live", "A1_3r"), _stat_protocol())
    assert out["mean_diff"] == pytest.approx(0.2)
    assert out["ci90"][0] > 0 and out["p_one_sided"] < 0.05
    assert all(type(out[k]) in (int, float) for k in ("n_pairs", "n_sessions", "n_divergent", "mean_diff"))


def test_paired_contrast_uses_one_session_draw_for_both_arms():
    rng = np.random.default_rng(3)
    frame = _long(arms=("A0_live", "A1_3r"), offsets={"A1_3r": 0.1})
    frame.loc[frame["arm"] == "A1_3r", ["r_cost", "excess_r"]] += rng.normal(0, 0.5, 120)[:, None]
    protocol = _stat_protocol()
    fwd = paired_contrast(pair_arms(frame, "A0_live", "A1_3r"), protocol)
    rev = paired_contrast(pair_arms(frame, "A1_3r", "A0_live"), protocol)
    assert rev["mean_diff"] == -fwd["mean_diff"]
    assert rev["ci90"] == pytest.approx([-fwd["ci90"][1], -fwd["ci90"][0]])


def test_holm_step_down():
    out = holm({"A1": 0.01, "A2": 0.02, "A3": 0.04, "A4": 0.5}, 0.05)
    assert out["A1"]["holm_p"] == pytest.approx(0.04) and out["A1"]["rejected"]
    assert out["A2"]["holm_p"] == pytest.approx(0.06) and not out["A2"]["rejected"]
    assert out["A3"]["holm_p"] == pytest.approx(0.08) and not out["A3"]["rejected"]
    assert out["A4"]["holm_p"] == pytest.approx(0.5) and not out["A4"]["rejected"]
    out = holm({"A1": 0.004, "A2": 0.01, "A3": 0.04, "A4": 0.5}, 0.05)
    assert [out[a]["rejected"] for a in ("A1", "A2", "A3", "A4")] == [True, True, False, False]
    nan = holm({"A1": 0.01, "A2": float("nan"), "A3": 0.02}, 0.05)
    assert nan["A1"]["holm_p"] == pytest.approx(0.02) and nan["A1"]["rejected"]
    assert nan["A3"]["holm_p"] == pytest.approx(0.02) and nan["A3"]["rejected"]
    assert np.isnan(nan["A2"]["holm_p"]) and not nan["A2"]["rejected"]
    capped = holm({"A1": 0.9}, 0.05)
    assert capped["A1"]["holm_p"] == 0.9
    assert holm({"A1": 0.9, "A2": 0.8}, 0.05)["A1"]["holm_p"] == 1.0


def test_sample_floors_use_a0_counts_and_per_arm_divergence():
    frame = _long(n_sessions=10, arms=("A0_live", "A1_3r", "A2_2r_trail"), per_session=2, hits={"A1_3r": "stop"})
    idx = frame[frame["arm"] == "A0_live"].index[:2]
    frame.loc[idx, "hit"] = "immature"
    pairs = {a: pair_arms(frame, "A0_live", a) for a in ("A1_3r", "A2_2r_trail")}
    protocol = _stat_protocol(min_setups=20, min_sessions=10, min_mature_fraction=0.9, min_divergent=10)
    floors = sample_floors(frame, pairs, protocol)
    assert floors["n_setups"] == 20 and floors["n_sessions"] == 10
    assert floors["mature_fraction"] == pytest.approx(0.9)
    assert floors["arms"]["A1_3r"] == {"n_divergent": 18, "holds": True}
    assert floors["arms"]["A2_2r_trail"] == {"n_divergent": 0, "holds": False}
    strict = sample_floors(
        frame, pairs, _stat_protocol(min_setups=21, min_sessions=10, min_mature_fraction=0.9, min_divergent=1)
    )
    assert not strict["arms"]["A1_3r"]["holds"]


def _floor_free(**overrides):
    return _stat_protocol(min_setups=1, min_sessions=1, min_mature_fraction=0.5, min_divergent=1, **overrides)


def test_discovery_selects_largest_b1_among_passing_and_breaks_ties_by_order():
    hits = dict.fromkeys(_ARM_NAMES[1:], "stop")
    offsets = {"A1_3r": 0.3, "A2_2r_trail": 0.5, "A3_3r_trail": 0.5, "A4_trail_only": -0.5}
    kwargs = {"offsets": offsets, "hits": hits}
    primary = _long(n_sessions=40, **kwargs)
    robust = _long(n_sessions=40, seed=9, **kwargs)
    # A0 must be a non-negative-mean arm for b2: lift every arm by a common constant.
    for frame in (primary, robust):
        frame["r_cost"] += 1.0
        frame["r"] += 1.0
        frame["excess_r"] += 1.0
    out = discovery(primary, robust, _floor_free())
    assert out["passing"] == ["A1_3r", "A2_2r_trail", "A3_3r_trail"]
    assert out["selected_arm"] == "A2_2r_trail"
    assert out["arms"]["A4_trail_only"]["b3"]["holds"] is False
    assert out["arms"]["A1_3r"]["b1"]["holds"] is True
    assert set(out["arms"]["A1_3r"]) == {"b1", "b2", "b3", "b4"}
    assert set(out["holm"]) == set(_ARM_NAMES[1:]) and "floors" in out


def test_discovery_no_pass_has_no_selected_arm():
    frame = _long(n_sessions=30)
    out = discovery(frame, frame, _floor_free())
    assert out["passing"] == [] and out["selected_arm"] is None


def test_confirmation_holds_requires_positive_contrast_and_nonnegative_mean():
    protocol = _stat_protocol()
    good = _long(arms=("A0_live", "A1_3r"), offsets={"A1_3r": 1.2, "A0_live": 1.0})
    out = confirmation(good, "A1_3r", protocol)
    assert out["holds"] is True and out["contrast"]["mean_diff"] == pytest.approx(0.2)
    assert out["arm_mean"]["mean_r_cost"] > 0
    flat = _long(arms=("A0_live", "A1_3r"), offsets={"A1_3r": 1.0, "A0_live": 1.0})
    assert confirmation(flat, "A1_3r", protocol)["holds"] is False
    negative = _long(arms=("A0_live", "A1_3r"), offsets={"A1_3r": -4.8, "A0_live": -5.0})
    assert negative.loc[negative["arm"] == "A1_3r", "r_cost"].mean() < 0
    assert confirmation(negative, "A1_3r", protocol)["holds"] is False


# ---------------------------------------------------------------- executor (Task 5)


def _exec_protocol():
    return _floor_free(bootstrap={"block_mean": 5, "draws": 200, "seed": 11})


def _lifted(frame, extra=0.0):
    out = frame.copy()
    for column in ("r", "r_cost", "excess_r"):
        out[column] += 1.0 + extra
    return out


def _winning_frame(seed=7, offset=0.5):
    hits = dict.fromkeys(_ARM_NAMES[1:], "stop")
    return _lifted(_long(n_sessions=40, seed=seed, offsets={"A1_3r": offset}, hits=hits))


def _losing_confirmation():
    hits = dict.fromkeys(_ARM_NAMES[1:], "stop")
    return _lifted(_long(n_sessions=30, seed=3, offsets={"A1_3r": -0.5}, hits=hits))


def _run(tmp_path, primary, robustness, confirmation_callable, environment=None):
    out = tmp_path / "out"
    frames = {
        "primary": lambda: primary,
        "robustness": lambda: robustness,
        "confirmation": confirmation_callable,
    }
    result = execute_bracket_counterfactual(
        _exec_protocol(), out, frames=frames, environment=environment or {"python": "3.14"}
    )
    return out, result


def _never():
    raise AssertionError("confirmation must not be read")


def test_executor_writes_protocol_manifest_frames_and_result_in_order(tmp_path):
    calls = []

    def read_confirmation():
        out = tmp_path / "out"
        calls.append(sorted(p.name for p in out.iterdir()))
        assert (out / "confirmation.json").exists(), "confirmation read without declaration"
        return _winning_frame(seed=5, offset=0.5)

    out, result = _run(tmp_path, _winning_frame(7), _winning_frame(9), read_confirmation)
    protocol = _exec_protocol()
    assert json.loads((out / "protocol.json").read_text())["identity"] == protocol.identity
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["protocol_id"] == protocol.identity
    assert manifest["confirmation_declaration"] == "confirmation.json"
    assert manifest["hypotheses"] == list(HYPOTHESES)
    assert manifest["arms"] == [arm.name for arm in protocol.arms]
    assert manifest["authorizes_promotion"] is False
    assert set(manifest["windows"]) == {"primary", "robustness", "confirmation"}
    assert manifest["windows"]["primary"] == ["2021-06-01", "2026-01-29", "2026-09-22"]
    assert {"protocol.json", "manifest.json", "frames"} <= set(calls[0])
    assert "result.json" not in calls[0]
    saved = json.loads((out / "result.json").read_text())
    assert saved["status"] == "completed" and saved["authorizes_promotion"] is False
    assert saved["selected_arm"] == "A1_3r" and saved["decision"] == "eligible_bracket_change"
    assert set(saved["b1"]) == set(protocol.counterfactual_arm_names())
    assert set(saved["b1"]["A1_3r"]) >= {"mean_diff", "holds"}
    assert "mean_r_cost" in saved["b2"]["A1_3r"] and "holds" in saved["b3"]["A1_3r"]
    assert "n_divergent" in saved["b4"]["A1_3r"]
    assert saved["b5"]["holds"] is True
    assert saved["passing"] == ["A1_3r"] and "holm" in saved and "floors" in saved
    assert saved["n_rows"] == {"primary": 600, "robustness": 600, "confirmation": 600}
    assert {"started_at", "finished_at"} <= set(saved)
    for window in ("primary", "robustness", "confirmation"):
        block = saved["descriptives"][window]
        assert block["arms"]["A0_live"]["n"] > 0
        assert "trend_pullback/1d" in block["by_setup"]
        assert "2024" in block["by_year"]["A1_3r"]
        assert "A0_live" in block["zero_cost"]
        assert (out / "frames" / f"{window}.csv.gz").exists()
    assert result["decision"] == saved["decision"]


def test_no_pass_never_calls_confirmation(tmp_path):
    frame = _long(n_sessions=30)
    out, result = _run(tmp_path, frame, frame, _never)
    assert result["status"] == "completed" and result["decision"] == "no_change"
    assert result["selected_arm"] is None and result["b5"] is None
    assert not (out / "confirmation.json").exists()
    assert not (out / "frames" / "confirmation.csv.gz").exists()
    assert "confirmation" not in result["descriptives"] and "confirmation" not in result["n_rows"]


def test_declaration_written_before_confirmation_read(tmp_path):
    def read_confirmation():
        if not (tmp_path / "out" / "confirmation.json").exists():
            raise AssertionError("confirmation read without declaration")
        return _winning_frame(seed=5)

    out, result = _run(tmp_path, _winning_frame(7), _winning_frame(9), read_confirmation)
    declaration = json.loads((out / "confirmation.json").read_text())
    assert declaration["confirmation_read"] is True and declaration["confirmation_arm"] == "A1_3r"
    assert declaration["protocol_id"] == _exec_protocol().identity and "declared_at" in declaration
    assert result["status"] == "completed"


def test_pass_then_confirmation_fail_is_no_change_with_selected_arm_recorded(tmp_path):
    out, result = _run(tmp_path, _winning_frame(7), _winning_frame(9), _losing_confirmation)
    assert result["selected_arm"] == "A1_3r" and result["decision"] == "no_change"
    assert result["b5"]["holds"] is False
    assert json.loads((out / "result.json").read_text())["selected_arm"] == "A1_3r"


def test_pass_then_confirmation_pass_is_eligible_bracket_change(tmp_path):
    _, result = _run(tmp_path, _winning_frame(7), _winning_frame(9), lambda: _winning_frame(seed=5))
    assert result["decision"] == "eligible_bracket_change" and result["b5"]["holds"] is True


def test_frame_failure_writes_status_failed(tmp_path):
    def boom():
        raise RuntimeError("boom")

    frames = {"primary": boom, "robustness": boom, "confirmation": _never}
    out = tmp_path / "failed"
    result = execute_bracket_counterfactual(_exec_protocol(), out, frames=frames, environment={})
    saved = json.loads((out / "result.json").read_text())
    assert saved["status"] == "failed" and "RuntimeError" in saved["error"] and "boom" in saved["error"]
    assert saved["authorizes_promotion"] is False and result == saved


def test_frames_are_private_gzip(tmp_path):
    out, _ = _run(tmp_path, _winning_frame(7), _winning_frame(9), lambda: _winning_frame(seed=5))
    for name in ("primary", "robustness", "confirmation"):
        path = out / "frames" / f"{name}.csv.gz"
        assert path.stat().st_mode & 0o777 == 0o600
        assert len(pd.read_csv(path)) == 600


# ---------------------------------------------------------------- CLI (Task 6)


def _protocol_document(**overrides) -> dict:
    document = json.loads((WORKSPACE_ROOT / "config" / "research" / "bracket-counterfactual-v1.json").read_text())
    document.update(overrides)
    document["strategy_config"] = load_config().strategies.model_dump(mode="json")
    return document


def _cache_args(tmp_path) -> list[str]:
    return ["--cache", str(tmp_path / "cache-a"), "--robustness-cache", str(tmp_path / "cache-b")]


def _invoke(tmp_path, document, *extra):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(document))
    return CliRunner().invoke(
        cli,
        [
            "alpha",
            "bracket-counterfactual",
            str(protocol_path),
            "--output",
            str(tmp_path / "out"),
            *_cache_args(tmp_path),
        ],
    )


def test_cli_bracket_counterfactual_is_registered():
    result = CliRunner().invoke(cli, ["alpha", "bracket-counterfactual", "--help"])
    assert result.exit_code == 0
    assert "--robustness-cache" in result.output


def test_cli_refuses_existing_output(tmp_path):
    (tmp_path / "out").mkdir()
    result = _invoke(tmp_path, _protocol_document())
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_cli_refuses_a_stale_strategy_config(tmp_path):
    document = _protocol_document()
    document["strategy_config"] = {"mode": "stale"}
    result = _invoke(tmp_path, document)
    assert result.exit_code != 0
    assert "strategy_config" in result.output


def test_cli_refuses_a_stale_sector_map(tmp_path):
    result = _invoke(tmp_path, _protocol_document(sector_etf={"technology": "XXX"}))
    assert result.exit_code != 0
    assert "sector_etf" in result.output


def test_cli_fails_fast_on_a_mismatched_cache_range(tmp_path, monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("build_window_frames must not run after a cache-range mismatch")

    monkeypatch.setattr(alpha_cmd, "build_window_frames", never)
    stale = tmp_path / "cache-b"
    stale.mkdir()
    (stale / "cache_range.json").write_text(
        json.dumps({"start": "2030-01-01T00:00:00+00:00", "end": "2030-02-01T00:00:00+00:00"})
    )
    result = _invoke(tmp_path, _protocol_document())
    assert result.exit_code != 0
    assert "use a fresh cache directory" in result.output
    assert not (tmp_path / "out").exists()


def test_cli_refuses_a_non_sip_feed_and_mismatched_cutoffs(tmp_path):
    result = _invoke(tmp_path, _protocol_document(feed="alpaca:iex"))
    assert result.exit_code != 0
    assert "feed" in result.output
    result = _invoke(tmp_path, _protocol_document(confirmation_data_cutoff="2026-09-21"))
    assert result.exit_code != 0
    assert "confirmation_data_cutoff" in result.output


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self._session = SimpleNamespace(close=lambda: None)


def _fake_command(monkeypatch, result: dict) -> dict:
    captured: dict = {"builds": []}

    async def fake_build(windows, universe, bars, calendar, cache_dir, config, **kwargs):
        captured["builds"].append({"windows": dict(windows), "cache": cache_dir, **kwargs})
        return {name: (lambda: pd.DataFrame(columns=list(ARM_FRAME_COLUMNS))) for name in windows}, {}

    def fake_execute(protocol, output, *, frames, environment):
        captured["frames"] = set(frames)
        captured["environment"] = environment
        return result

    for name in ("BoundedTradingClient", "BoundedStockDataClient"):
        monkeypatch.setattr(alpha_cmd, name, _FakeClient)
    for name in ("AlpacaDataProvider", "BarEvidenceStore", "AlpacaCalendarProvider"):
        monkeypatch.setattr(alpha_cmd, name, lambda *args, **kwargs: object())
    monkeypatch.setattr(alpha_cmd, "build_window_frames", fake_build)
    monkeypatch.setattr(alpha_cmd, "execute_bracket_counterfactual", fake_execute)
    monkeypatch.setattr(alpha_cmd, "research_environment", dict)
    return captured


def test_cli_builds_primary_and_confirmation_from_one_replay(tmp_path, monkeypatch):
    captured = _fake_command(monkeypatch, {"status": "completed"})
    result = _invoke(tmp_path, _protocol_document())
    assert result.exit_code == 0, result.output
    protocol = BracketCounterfactualProtocol(**_protocol_document())
    first, second = captured["builds"]
    assert set(first["windows"]) == {"primary", "confirmation"}
    assert first["cache"] == tmp_path / "cache-a" and first["arms"] == protocol.bracket_arms()
    assert first["data_cutoff"] == protocol.data_cutoff
    assert set(second["windows"]) == {"robustness"}
    assert second["cache"] == tmp_path / "cache-b" and second["arms"] == protocol.bracket_arms()
    assert second["data_cutoff"] == protocol.robustness_data_cutoff
    assert captured["frames"] == {"primary", "robustness", "confirmation"}
    caches = captured["environment"]["caches"]
    assert caches["primary"]["path"] == str(tmp_path / "cache-a")
    assert caches["robustness"]["path"] == str(tmp_path / "cache-b")
    start = min(protocol.window[0], protocol.confirmation_window[0]) - timedelta(days=400)
    assert caches["primary"]["range"]["start"].startswith(start.isoformat())
    assert caches["primary"]["range"]["end"].startswith("2026-09-22")
    assert caches["robustness"]["range"]["end"].startswith("2021-06-15")


def test_cli_exits_nonzero_when_the_study_fails(tmp_path, monkeypatch):
    _fake_command(monkeypatch, {"status": "failed", "error": "RuntimeError: boom"})
    result = _invoke(tmp_path, _protocol_document())
    assert result.exit_code != 0
    assert "bracket counterfactual failed: RuntimeError: boom" in result.output
