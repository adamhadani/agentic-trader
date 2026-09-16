"""Calibration instruments are deterministic diagnostics, never activation evidence."""

import json

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.calibration import (
    CalibrationPlan,
    joint_block_max_test,
    run_calibration,
    synthetic_pulse_bars,
)
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import assess_qualification, assess_statistical_evidence
from agentic_trader.research.alpha.strategy import alpha_scores
from agentic_trader.research.alpha.validation import ValidationPolicy


@pytest.fixture
def panel():
    rng = np.random.default_rng(91)
    return pd.DataFrame(
        rng.normal(size=(200, 3)), columns=["a", "b", "c"], index=pd.date_range("2020-01-01", periods=200, tz="UTC")
    )


def test_joint_resampling_duplicate_permutation_and_scale_invariance(panel):
    options = {"samples": 199, "block_length": 10, "seed": 5}
    original = joint_block_max_test(panel, **options)
    changed = panel[["c", "a", "b"]].mul([3, 0.2, 7]).assign(duplicate=panel.a)
    repeated = joint_block_max_test(changed, **options)
    for name in panel:
        assert repeated["adjusted_pvalues"][name] == original["adjusted_pvalues"][name]
    assert repeated["adjusted_pvalues"]["duplicate"] == repeated["adjusted_pvalues"]["a"]
    assert original["scope"] == "fixed_candidates_only"
    assert original == joint_block_max_test(panel, **options)


def test_joint_resampling_matches_independent_loop_reference(panel):
    samples, block, seed = 99, 10, 7
    result = joint_block_max_test(panel, samples=samples, block_length=block, seed=seed)
    values = panel.to_numpy()
    centered = values - values.mean(axis=0)
    observed = values.mean(axis=0) / values.std(axis=0, ddof=1) * np.sqrt(len(values))
    rng = np.random.default_rng(seed)
    null_max = []
    for _ in range(samples):
        starts = rng.integers(0, len(values), size=int(np.ceil(len(values) / block)))
        indices = np.array([(s + j) % len(values) for s in starts for j in range(block)])[: len(values)]
        draw = centered[indices]
        null_max.append(max(draw.mean(axis=0) / draw.std(axis=0, ddof=1) * np.sqrt(len(draw))))
    expected = {name: (1 + sum(x >= observed[i] for x in null_max)) / (samples + 1) for i, name in enumerate(panel)}
    assert result["adjusted_pvalues"] == expected


@pytest.mark.parametrize("defect", ["nan", "duplicate_time", "duplicate_column", "constant", "unsorted"])
def test_invalid_panel_fails_without_imputed_evidence(panel, defect):
    if defect == "nan":
        panel.iloc[0, 0] = np.nan
    elif defect == "duplicate_time":
        panel.index = pd.DatetimeIndex([panel.index[0]] * len(panel))
    elif defect == "duplicate_column":
        panel.columns = ["a", "a", "c"]
    elif defect == "constant":
        panel["a"] = 0
    else:
        panel = panel.iloc[::-1]
    with pytest.raises(ValueError):
        joint_block_max_test(panel, samples=99, block_length=10, seed=1)


@pytest.mark.parametrize("kwargs", [{"samples": 0}, {"samples": True}, {"block_length": 0}, {"block_length": 101}])
def test_invalid_resampling_budget(panel, kwargs):
    with pytest.raises(ValueError):
        joint_block_max_test(panel, **{"samples": 99, "block_length": 10, "seed": 1, **kwargs})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"seeds": 0},
        {"seeds": True},
        {"observations": 100},
        {"family_trials": 0},
        {"trial_variance": float("nan")},
        {"trial_variance": True},
        {"trial_variance": "0.1"},
        {"bootstrap_samples": 10},
    ],
)
def test_invalid_calibration_plan(kwargs):
    with pytest.raises(ValueError):
        CalibrationPlan(**kwargs)


def test_synthetic_generator_and_scores_are_prefix_causal():
    short = synthetic_pulse_bars(seed=7, observations=600, effect=0.02, interval=8)
    long = synthetic_pulse_bars(seed=7, observations=800, effect=0.02, interval=8)
    pd.testing.assert_frame_equal(short, long.iloc[:600])
    definition = AlphaDefinition("synthetic", "Synthetic", "volume", timeframe="1d", data_feed="synthetic")
    pd.testing.assert_series_equal(alpha_scores(definition, short), alpha_scores(definition, long).iloc[:600])
    assert short.attrs["feed"] == "synthetic"


def test_small_calibration_is_reproducible_and_never_claims_qualification():
    plan = CalibrationPlan(seeds=1, observations=600, bootstrap_samples=99)
    result = run_calibration(plan)
    assert result == run_calibration(plan)
    assert result["synthetic_only"] and not result["authorizes_promotion"]
    assert result["protocol"] == plan.to_dict()
    # Paired effects share data innovations, while resampling/assessment gets an
    # independent child stream. Reusing a data RNG seed can bias a size study.
    streams = result["random_streams"]
    assert len(streams) == plan.seeds
    assert len(set(streams[0]["seeds"].values())) == 4
    assert streams[0]["seeds"]["panel_resampling"] == result["family_controls"][0]["test"]["seed"]
    assert result["strategy_controls"] and result["family_controls"]
    assert all(0 <= row["acceptance_rate"] <= 1 for row in result["strategy_summary"])
    assert all(
        row["interval_95"][0] <= row["acceptance_rate"] <= row["interval_95"][1] for row in result["strategy_summary"]
    )
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("effect,expected", [(0, False), (0.02, True)])
def test_actual_pipeline_recognizes_controls_but_cannot_qualify_synthetic_data(effect, expected):
    policy = ValidationPolicy()
    definition = AlphaDefinition(
        "synthetic", "Synthetic", "volume", timeframe="1d", eligible_symbols=("SYNTH",), data_feed="synthetic"
    )
    bars = synthetic_pulse_bars(seed=19, observations=2500, effect=effect, interval=8)
    miner = AlphaMiner(seed=19, catalog=AlphaCatalog([definition]), policy=policy)
    miner.mine(bars, iterations=0, timeframe="1d", symbol="SYNTH")
    manifest = {"feed": "synthetic", "adjustment": "raw", "incumbents": []}
    family = {"trial_count": 7049, "trial_variance": 0.0028764548818829777}
    scientific = assess_statistical_evidence(definition, bars, miner.last_run, manifest, family, policy=policy)
    assert scientific["passed"] is expected, scientific["reasons"]
    deployed = assess_qualification(definition, bars, miner.last_run, manifest, family, policy=policy)
    assert not deployed["qualified"]
    assert "deployment_data_contract_mismatch" in deployed["reasons"]
