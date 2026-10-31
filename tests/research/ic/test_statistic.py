from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from scipy.signal import lfilter

from agentic_trader.research.alpha.information import ICPolicy, cross_sectional_ic
from agentic_trader.research.alpha.targets import ForecastTarget
from agentic_trader.research.ic import statistic
from agentic_trader.research.ic.arrays import ICArrays
from agentic_trader.research.ic.statistic import (
    gate_verdict,
    ic_inference,
    pearson_ic_series,
    rank_ic_series,
    render_tear_sheet,
    tear_sheet,
)
from agentic_trader.research.pooled.stats import bootstrap_draws


def _panel(seed=0, s=50, n=40):
    rng = np.random.default_rng(seed)
    scores = np.round(rng.normal(size=(s, n)), 1)  # rounding makes ties
    target = np.round(0.3 * scores + rng.normal(size=(s, n)), 1)
    scores[rng.random((s, n)) < 0.1] = np.nan
    target[rng.random((s, n)) < 0.1] = np.nan
    return scores, target


def test_rank_ic_matches_cross_sectional_ic():
    scores, target = _panel()
    mask = np.ones(scores.shape, bool)
    ours = rank_ic_series(scores, target, mask, min_names=3)
    clock = pd.date_range("2022-01-03", periods=scores.shape[0], freq="B", tz="America/New_York")
    cols = [f"N{j}" for j in range(scores.shape[1])]
    report = cross_sectional_ic(
        pd.DataFrame(scores, index=clock, columns=cols),
        pd.DataFrame(target, index=clock, columns=cols),
        pd.Series("one", index=clock),
        ICPolicy(min_assets=3, min_observations=10, hac_lags=3),
        expected_index=clock,
        target=ForecastTarget("1d"),
    )
    theirs = np.array([np.nan if o.ic is None else o.ic for o in report.observations])
    assert np.array_equal(np.isnan(ours), np.isnan(theirs))
    assert np.nanmax(np.abs(ours - theirs)) < 1e-12


def test_session_below_min_names_is_nan():
    scores, target = _panel(s=3, n=40)
    mask = np.ones(scores.shape, bool)
    mask[1, 15:] = False
    out = rank_ic_series(scores, target, mask, min_names=30)
    assert np.isnan(out[1]) and np.isfinite(out[0])


def test_constant_score_or_target_is_nan_not_an_exception():
    scores, target = _panel(s=3, n=40)
    scores[0] = 1.0
    target[1] = 2.0
    out = rank_ic_series(scores, target, np.ones(scores.shape, bool), min_names=3)
    assert np.isnan(out[0]) and np.isnan(out[1]) and np.isfinite(out[2])


def test_all_nan_score_gives_all_nan_series():
    scores, target = _panel()
    out = rank_ic_series(np.full(scores.shape, np.nan), target, np.ones(scores.shape, bool))
    assert np.isnan(out).all()


def test_pearson_ic_matches_numpy_corrcoef_over_the_pairs_each_session_keeps():
    scores, target = _panel()
    mask = np.ones(scores.shape, bool)
    mask[:, ::4] = False
    out = pearson_ic_series(scores, target, mask, min_names=3)
    assert np.isfinite(out).all()
    for s in range(scores.shape[0]):
        keep = mask[s] & np.isfinite(scores[s]) & np.isfinite(target[s])
        assert out[s] == pytest.approx(np.corrcoef(scores[s][keep], target[s][keep])[0, 1], abs=1e-14)


def test_pearson_ic_reads_the_values_where_rank_ic_reads_their_order():
    rng = np.random.default_rng(2)
    scores = rng.normal(size=(5, 40))
    target = scores.copy()
    target[:, 0] = 1000.0  # the top rank, but far from the line
    rank = rank_ic_series(scores, target, np.ones(scores.shape, bool), min_names=3)
    pearson = pearson_ic_series(scores, target, np.ones(scores.shape, bool), min_names=3)
    assert (pearson < 0.5).all() and (rank > 0.5).all()


def test_pearson_ic_has_rank_ics_unavailable_sessions():
    scores, target = _panel(s=6, n=40)
    mask = np.ones(scores.shape, bool)
    mask[1, 15:] = False  # below min_names
    scores[2] = 1.0  # constant score
    target[3] = 2.0  # constant target
    scores[4] = np.nan  # no score
    pearson = pearson_ic_series(scores, target, mask, min_names=20)
    rank = rank_ic_series(scores, target, mask, min_names=20)
    assert np.array_equal(np.isnan(pearson), np.isnan(rank))
    assert np.isnan(pearson[1:5]).all() and np.isfinite(pearson[[0, 5]]).all()


def test_pearson_ic_refuses_mismatched_shapes():
    with pytest.raises(ValueError, match="one shape"):
        pearson_ic_series(np.zeros((2, 3)), np.zeros((2, 4)), np.ones((2, 3), bool))


def test_inference_drops_unavailable_sessions_and_counts_them():
    ic = np.array([0.1, np.nan, 0.2, 0.05, np.nan, 0.15] * 10)
    out = ic_inference(ic, 1, draws=200, seed=1)
    assert out["n_sessions"] == 40 and out["n_unavailable"] == 20
    assert out["mean"] == pytest.approx(np.nanmean(ic))


def test_inference_with_no_evaluated_sessions_returns_nan_fields():
    out = ic_inference(np.full(10, np.nan), 5, draws=100, seed=1)
    assert out["n_sessions"] == 0 and out["n_unavailable"] == 10
    assert all(np.isnan(out[k]) for k in ("mean", "se", "t", "newey_west_t", "icir", "p_one_sided"))


def test_block_mean_is_two_h():
    ic = np.random.default_rng(3).normal(0.02, 0.1, 100)
    with patch.object(statistic, "bootstrap_draws", wraps=bootstrap_draws) as spy:
        ic_inference(ic, 5, draws=50, seed=9)
    spy.assert_called_once_with(100, 10.0, 50, 9)


def test_newey_west_uses_two_h_lags_on_retained_sessions():
    ic = np.random.default_rng(4).normal(0.02, 0.1, 80)
    ic[[3, 10]] = np.nan
    with patch.object(statistic, "summarize_expected_values", wraps=statistic.summarize_expected_values) as spy:
        ic_inference(ic, 5, draws=50, seed=1)
    values, policy = spy.call_args.args
    assert len(values) == 78 and policy.hac_lags == 10


def test_inference_is_deterministic_for_a_seed():
    ic = np.random.default_rng(5).normal(0.02, 0.1, 100)
    assert ic_inference(ic, 5, draws=100, seed=2) == ic_inference(ic, 5, draws=100, seed=2)


def _planted(t_target_blocks):
    """100 sessions with four blocks of 25; block means are exactly the given values."""
    return np.concatenate([np.full(25, m) + np.tile([-0.01, 0.01], 13)[:25] for m in t_target_blocks])


def test_gate_requires_t_and_blocks():
    kw = {"min_t": 2.0, "blocks": 4, "min_positive_blocks": 3, "draws": 300, "seed": 1}
    two_of_four = gate_verdict(_planted([0.2, 0.2, -0.001, -0.001]), 1, **kw)
    assert two_of_four["t"] >= 2 and two_of_four["positive_blocks"] == 2 and not two_of_four["passes"]
    noise = np.random.default_rng(1).normal(0, 0.3, 100)
    noise -= np.array([noise[i * 25 : (i + 1) * 25].mean() for i in range(4)]).repeat(25)  # exact block means
    weak = gate_verdict(noise + np.repeat([0.02, 0.02, 0.02, -0.02], 25), 1, **kw)
    assert weak["positive_blocks"] == 3 and weak["t"] < 2 and not weak["passes"]
    both = gate_verdict(_planted([0.1, 0.1, 0.1, -0.001]), 1, **kw)
    assert both["t"] >= 2 and both["positive_blocks"] == 3 and both["passes"]


def test_gate_blocks_are_equal_consecutive_blocks_of_evaluated_sessions():
    ic = np.array([1.0, np.nan, 1.0, -1.0, np.nan, -1.0, 2.0, 2.0])
    out = gate_verdict(ic, 1, min_t=0.0, blocks=3, min_positive_blocks=1, draws=50, seed=1)
    kept = ic[np.isfinite(ic)]
    assert out["block_means"] == [float(p.mean()) for p in np.array_split(kept, 3)]


def test_overlapping_null_guard_catches_only_a_gross_failure():
    # A guard against a gross failure, not a calibration: 3 of these 40 paths (7.5%) reach t >= 2, the bar is 15%.
    rng = np.random.default_rng(0)
    n, hits = 1000, 0
    for _ in range(40):
        path = lfilter([1.0], [1.0, -0.95], rng.normal(size=n + 300))[300:] * 0.01  # AR(0.95), true mean 0
        hits += ic_inference(path, 20, draws=1000, seed=1)["t"] >= 2
    assert hits / 40 < 0.15


def _arrays(scores_target_noise=1.0, s=60, n=60, seed=0):
    rng = np.random.default_rng(seed)
    latent = rng.normal(size=(s, n))
    ret = 0.01 * latent + 0.01 * rng.normal(size=(s, n)) * scores_target_noise
    sessions = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(s))
    eligible = np.ones((s, n), bool)
    arrays = ICArrays(
        sessions=sessions,
        symbols=tuple(f"S{j}" for j in range(n)),
        offset=0,
        eligible=eligible,
        groups=np.array(["a"] * n),
        log_adv=np.zeros((s, n)),
        beta_60=np.ones((s, n)),
        returns={1: ret, 5: ret * 2},
        residuals={1: ret - ret.mean(axis=1, keepdims=True), 5: ret},
        spy_returns={1: np.zeros(s), 5: np.zeros(s)},
        book=None,
    )
    return latent, arrays


def test_tear_sheet_decile_spread_sign_on_a_planted_score():
    latent, arrays = _arrays()
    good = tear_sheet(latent, arrays, horizons=[1], draws=100, seed=1)["horizons"]["1"]["raw"]
    bad = tear_sheet(-latent, arrays, horizons=[1], draws=100, seed=1)["horizons"]["1"]["raw"]
    assert good["deciles"]["top_minus_bottom_pct"] > 0 > bad["deciles"]["top_minus_bottom_pct"]
    assert good["mean"] > 0


def test_tear_sheet_lists_each_horizon_and_both_targets():
    latent, arrays = _arrays()
    sheet = tear_sheet(latent, arrays, horizons=[1, 5], draws=100, seed=1)
    assert set(sheet["horizons"]) == {"1", "5"}
    for per in sheet["horizons"].values():
        assert set(per) == {"raw", "residual"}
        assert len(per["raw"]["block_means"]) == 4 and per["raw"]["by_year"]
    assert sheet["score"]["median_names"] == 60


def test_render_mentions_every_horizon():
    latent, arrays = _arrays()
    sheet = tear_sheet(latent, arrays, horizons=[1, 5], draws=100, seed=1)
    text = render_tear_sheet(sheet)
    assert "h=1 " in text and "h=5 " in text and "residual" in text
    assert text == render_tear_sheet(tear_sheet(latent, arrays, horizons=[1, 5], draws=100, seed=1))
