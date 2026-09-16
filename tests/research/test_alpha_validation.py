import random
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from agentic_trader.research.alpha.metrics import calculate_deflated_sharpe_ratio
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.validation import ValidationPolicy, purged_folds


@pytest.fixture
def market():
    rng = np.random.default_rng(420)
    close = 100 * np.exp(rng.normal(0, 0.01, 800).cumsum())
    bars = pd.DataFrame(
        {
            "open": close,
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": rng.integers(1000, 10000, 800),
        },
        index=pd.date_range("2021-01-01", periods=800, freq="B", tz="UTC"),
    )
    bars.attrs["timeframe"] = "1d"
    return bars


def test_dsr_reference_per_observation_formula():
    sr, n, skew, kurt = 0.12, 150, -0.2, 4.0
    expected = norm.cdf(sr * np.sqrt(n - 1) / np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2))
    assert calculate_deflated_sharpe_ratio(sr, 1, 0, n, skew, kurt) == pytest.approx(expected, abs=1e-12)


def test_search_never_reads_holdout(market):
    first, second = AlphaMiner(seed=5), AlphaMiner(seed=5)
    options = {"iterations": 5, "timeframe": "1d", "min_sharpe": -100, "min_dsr": 0, "min_ic": -1}
    result = first.mine(market, **options)
    altered = market.copy()
    altered.iloc[640:, :4] *= 3
    rerun = second.mine(altered, **options)
    assert [r.to_dict() for r in result] == [r.to_dict() for r in rerun]
    assert first.last_run["trial_count"] == 12
    assert len(first.last_run["trials"]) == 12
    assert first.last_run["holdout_start"] == 640


def test_purged_folds_have_no_label_overlap():

    policy = ValidationPolicy(label_horizon=5, embargo_bars=3)
    folds = purged_folds(800, policy)
    assert len(folds) == 3
    for fold in folds:
        assert fold.train_end + policy.label_horizon + policy.embargo_bars <= fold.validation_start
        assert fold.validation_end <= 640
    assert all(a.validation_end <= b.validation_start for a, b in pairwise(folds))


def test_mismatched_sampling_rejected(market):
    with pytest.raises(ValueError, match="timeframe"):
        AlphaMiner().evaluate_alpha(AlphaDefinition("alpha_bad", "Bad", "close", timeframe="4h"), market)


def test_seed_does_not_mutate_global_random_state():

    state = random.getstate()
    AlphaMiner().generate_candidate_expression(seed=123)
    assert random.getstate() == state


def test_metrics_keep_sample_moments_for_trial_adjustment(market):

    definition = AlphaDefinition("alpha_units", "Units", "delta(close,3)", timeframe="1d", entry_threshold=0.5)
    candidate = AlphaMiner().evaluate_alpha(definition, market)
    sim = simulate_strategy(definition, market, start=560)
    assert candidate.metrics.per_bar_sharpe == pytest.approx(sim["per_bar_sharpe"])
    assert candidate.metrics.sample_length == sim["sample_length"]
    assert candidate.metrics.dsr == pytest.approx(
        calculate_deflated_sharpe_ratio(
            sim["per_bar_sharpe"], 1, 0, sim["sample_length"], sim["skewness"], sim["kurtosis"]
        )
    )


def test_unique_search_exhaustion_preserves_completed_trials(market, monkeypatch):
    miner = AlphaMiner(seed=5)
    definition = AlphaDefinition("alpha_identical", "Identical", "close")
    monkeypatch.setattr(miner, "generate_candidate_expression", lambda: definition)
    miner.mine(market, iterations=2, include_catalog=False, timeframe="1d", symbol="SPY")
    assert miner.last_run["status"] == "search_exhausted"
    assert miner.last_run["trial_count"] == 1
