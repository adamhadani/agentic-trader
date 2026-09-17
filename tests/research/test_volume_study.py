import json
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.market.bars import FIXED_BAR_LAYOUT, TradingSession
from agentic_trader.research.alpha.volume import VolumeContract, VolumePolicy
from agentic_trader.research.alpha.volume_study import VolumeFold, VolumeStudyPlan, compute_volume_study


@pytest.fixture
def volume_study_case():
    clock = pd.date_range("2021-01-04", periods=110, freq="B", tz="America/New_York")
    frames = {}
    for symbol in ("AAA", "BBB"):
        frame = pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": np.random.default_rng(ord(symbol[0])).lognormal(8, 0.3, len(clock)),
            },
            index=clock,
        )
        frame.attrs.update(feed="alpaca:iex", timeframe="1d", adjustment="all")
        frames[symbol] = frame
    plan = VolumeStudyPlan(
        "fixture",
        tuple(frames),
        clock[0].date(),
        clock[-1].date(),
        (VolumeFold("one", clock[10].date(), clock[59].date(), clock[60].date(), clock[-1].date()),),
        VolumeContract("alpaca:iex", "1d", "all", FIXED_BAR_LAYOUT),
        VolumePolicy(lookback=5, min_observations=30),
    )
    return frames, clock, plan


def test_frozen_identity_and_past_only_calibration(volume_study_case):
    frames, clock, plan = volume_study_case
    assert VolumeStudyPlan.from_document(json.loads(json.dumps(plan.document()))) == plan
    assert plan.trial_count == 4
    result = compute_volume_study(frames, clock, plan, ())
    assert not result["authorizes_promotion"]
    assert len(result["profiles"]) == 2
    changed = {s: f.copy(deep=True) for s, f in frames.items()}
    for frame in changed.values():
        frame.loc[clock[90] :, "volume"] *= 100
    other = compute_volume_study(changed, clock, plan, ())
    assert [r["calibration"] for r in result["profiles"]] == [r["calibration"] for r in other["profiles"]]
    for first, second in zip(result["profiles"], other["profiles"], strict=True):
        assert first["observations"][:30] == second["observations"][:30]


@pytest.mark.parametrize("fault", ["future_train", "missing", "wrong_feed", "wrong_adjustment"])
def test_study_refuses_invalid_evidence(volume_study_case, fault):
    frames, clock, plan = volume_study_case
    with pytest.raises(ValueError):
        if fault == "future_train":
            replace(plan, folds=(replace(plan.folds[0], training_end=clock[60].date()),))
        else:
            if fault == "missing":
                frames["AAA"] = frames["AAA"].drop(clock[80])
            else:
                frames["AAA"].attrs["feed" if fault == "wrong_feed" else "adjustment"] = "other"
            compute_volume_study(frames, clock, plan, ())


@pytest.mark.parametrize("fault", [None, "missing"])
def test_cli_charges_before_source_and_preserves_registry(volume_study_case, tmp_path, monkeypatch, fault):
    frames, clock, plan = volume_study_case
    if fault:
        frames["AAA"] = frames["AAA"].drop(clock[80])
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    source = SimpleNamespace(calendar=lambda *_: sessions, daily=lambda symbol, *_: frames[symbol].copy(deep=True))
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.session_source", lambda *_: nullcontext(source))
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(plan.document()))
    output = tmp_path / "study"
    result = CliRunner().invoke(cli, ["alpha", "volume-study", str(path), "--output", str(output)])
    assert (result.exit_code == 0) == (fault is None), result.output
    saved = json.loads((output / "result.json").read_text())
    assert saved["charged_trials"] == 4 and not saved["authorizes_promotion"]
    status = CliRunner().invoke(cli, ["alpha", "status"])
    assert status.exit_code == 0
    state = json.loads(status.output)
    assert state["research_family"]["trial_count"] == 4
    assert state["generation"] == 0 and state["active"] == 0
