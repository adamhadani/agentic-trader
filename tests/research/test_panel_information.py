"""Independent examples for cross-sectional rank statistics and their units."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from agentic_trader.research.alpha.information import ICPolicy, cross_sectional_ic
from agentic_trader.research.alpha.targets import ForecastTarget


@pytest.fixture
def rank_panel():
    clock = pd.date_range("2022-01-03", periods=40, freq="B", tz="America/New_York")
    scores = pd.DataFrame(np.tile([1.0, 2.0, 2.0, 4.0], (40, 1)), index=clock, columns=list("ABCD"))
    labels = pd.DataFrame(np.tile([4.0, 2.0, 1.0, 3.0], (40, 1)), index=clock, columns=scores.columns)
    folds = pd.Series("one", index=clock)
    return scores, labels, folds


def report(scores, labels, folds, **kwargs):
    return cross_sectional_ic(
        scores,
        labels,
        folds,
        ICPolicy(min_assets=3, min_observations=10, hac_lags=3, **kwargs),
        expected_index=scores.index,
        target=ForecastTarget("1d"),
    ).document()


@pytest.mark.parametrize("direction", [1, -1])
def test_spearman_preserves_ties_and_reports_constant_ic_without_inventing_significance(rank_panel, direction):
    scores, labels, folds = rank_panel
    result = report(scores, labels * direction, folds)
    expected = stats.spearmanr(scores.iloc[0], labels.iloc[0] * direction).statistic
    assert result["axis"] == "cross_sectional"
    assert all(o["ic"] == pytest.approx(expected) and o["pairs"] == 4 for o in result["observations"])
    summary = result["folds"]["one"]
    assert summary["mean_ic"] == pytest.approx(expected)
    assert summary["icir_per_observation"] is None
    assert summary["iid_t"] is None and summary["hac_t"] is None
    assert summary["inference_unavailable"] == "zero_variance"


@pytest.mark.parametrize(
    "fault,reason",
    [("constant_score", "constant_score"), ("constant_label", "constant_target"), ("missing", "insufficient_breadth")],
)
def test_missing_rank_observations_are_retained_and_never_compressed_for_inference(rank_panel, fault, reason):
    scores, labels, folds = rank_panel
    labels.iloc[1] = [1, 4, 2, 3]
    if fault == "constant_score":
        scores.iloc[4] = 1
    elif fault == "constant_label":
        labels.iloc[4] = 1
    else:
        scores.iloc[4, :2] = np.nan
    result = report(scores, labels, folds, observations_per_year=252)
    assert len(result["observations"]) == 40
    assert result["observations"][4]["ic"] is None and result["observations"][4]["reason"] == reason
    s = result["folds"]["one"]
    assert s["observed"] == 39 and s["expected"] == 40
    assert s["mean_ic"] is not None and s["icir_annualized_iid"] is None
    assert s["iid_t"] is None and s["hac_t"] is None
    assert s["inference_unavailable"] == "missing_observations"


def test_units_sample_variance_and_independent_hac_reference(rank_panel):
    scores, labels, folds = rank_panel
    rng = np.random.default_rng(19)
    labels.iloc[:] = rng.normal(size=labels.shape)
    result = report(scores, labels, folds, observations_per_year=252)
    x = np.array([stats.spearmanr(a, b).statistic for a, b in zip(scores.to_numpy(), labels.to_numpy(), strict=True)])
    s = result["folds"]["one"]
    mean = x.mean()
    std = x.std(ddof=1)
    n = len(x)
    assert s["sample_std_ic"] == pytest.approx(std)
    assert s["icir_per_observation"] == pytest.approx(mean / std)
    assert s["icir_annualized_iid"] == pytest.approx(mean / std * np.sqrt(252))
    assert s["iid_t"] == pytest.approx(s["icir_annualized_iid"] * np.sqrt(n / 252))
    assert s["iid_t"] == pytest.approx(stats.ttest_1samp(x, 0).statistic)
    # Bartlett-weighted covariance of the intercept with finite-sample correction.
    e = x - mean
    meat = e @ e
    for lag in range(1, 4):
        meat += 2 * (1 - lag / 4) * (e[lag:] @ e[:-lag])
    se = np.sqrt(meat / n**2 * n / (n - 1))
    assert s["hac_standard_error"] == pytest.approx(se)
    assert s["hac_t"] == pytest.approx(mean / se)
    assert s["hac_ci_low"] == pytest.approx(mean - stats.norm.ppf(0.975) * se)
    assert not result["authorizes_promotion"]


def test_fold_inference_does_not_stitch_disjoint_windows(rank_panel):
    scores, labels, folds = rank_panel
    labels.iloc[1::2] = [1, 4, 3, 2]
    folds.iloc[20:] = "two"
    first = report(scores, labels, folds)["folds"]["one"]
    labels.iloc[20:] = labels.iloc[20:].to_numpy()[:, ::-1]
    assert report(scores, labels, folds)["folds"]["one"] == first


@pytest.mark.parametrize("fault", ["missing_row", "columns", "infinite", "unordered", "fold_reappears"])
def test_ambiguous_axes_fail_loudly(rank_panel, fault):
    scores, labels, folds = rank_panel
    clock = scores.index
    if fault == "missing_row":
        scores = scores.drop(scores.index[4])
    elif fault == "columns":
        labels = labels[labels.columns[::-1]]
    elif fault == "infinite":
        scores.iloc[0, 0] = np.inf
    elif fault == "unordered":
        scores = scores.iloc[::-1]
    else:
        folds.iloc[2:4] = "second"
    with pytest.raises(ValueError):
        cross_sectional_ic(
            scores, labels, folds, ICPolicy(min_assets=3), expected_index=clock, target=ForecastTarget("1d")
        )


@pytest.mark.parametrize(
    "options",
    [
        {"min_assets": 2},
        {"min_observations": 1},
        {"hac_lags": -1},
        {"observations_per_year": float("inf")},
        {"observations_per_year": True},
    ],
)
def test_invalid_statistical_policy_rejected(options):
    with pytest.raises(ValueError):
        ICPolicy(**options)


def test_persistent_ic_requires_larger_uncertainty_than_iid():
    rng = np.random.default_rng(911)
    clock = pd.date_range("2020-01-01", periods=600, freq="B", tz="UTC")
    signal = np.zeros(len(clock))
    for i in range(1, len(clock)):
        signal[i] = 0.95 * signal[i - 1] + rng.normal(0, 0.25)
    base = np.linspace(-1, 1, 30)
    scores = pd.DataFrame(np.tile(base, (len(clock), 1)), index=clock)
    labels = pd.DataFrame(signal[:, None] * base[None, :] + rng.normal(0, 0.8, scores.shape), index=clock)
    result = cross_sectional_ic(
        scores,
        labels,
        pd.Series("dependent", index=clock),
        ICPolicy(hac_lags=20),
        expected_index=clock,
        target=ForecastTarget("1d"),
    ).document()
    summary = result["folds"]["dependent"]
    assert summary["hac_standard_error"] > 2 * summary["sample_std_ic"] / np.sqrt(summary["observed"])
    assert abs(summary["hac_t"]) < abs(summary["iid_t"]) / 2
    assert summary["icir_annualized_iid"] is None  # No inferred frequency.
