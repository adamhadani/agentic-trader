"""Causal factor residuals use observed return endpoints and strictly prior fits."""

import json

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.factor_features import ResidualMomentumSpec, residual_momentum_features


@pytest.fixture(scope="module")
def factor_case():
    clock = pd.date_range("2021-01-04", periods=410, freq="B", tz="America/New_York")
    factors = tuple(f"ETF{i}" for i in range(9))
    rng = np.random.default_rng(419)
    factor_returns = rng.normal(0, 0.003, (len(clock), len(factors)))
    factor_returns[0] = 0
    coefficients = np.linspace(-0.2, 0.3, len(factors))
    stock = 0.0001 + factor_returns @ coefficients
    stock[0] = 0
    stock[127] += 0.01  # Outside the very first fit, observed only on the next bar.
    returns = pd.DataFrame(np.column_stack([stock, factor_returns]), index=clock, columns=("AAA", *factors))
    closes = 100 * (1 + returns).cumprod()
    volumes = pd.DataFrame(1000.0, index=clock, columns=closes.columns)
    return closes, volumes, factors, coefficients


def compute(case, *, end=None, closes=None, volumes=None):
    original_closes, original_volumes, factors, _ = case
    return residual_momentum_features(
        (original_closes if closes is None else closes).iloc[:end],
        (original_volumes if volumes is None else volumes).iloc[:end],
        symbols=("AAA",),
        factors=factors,
        feed="alpaca:iex",
    )


def fit_at(result, position):
    return result.fits[position]


def test_known_coefficients_use_exact_prior_126_returns_and_exclude_current_shock(factor_case):
    closes, _, factors, coefficients = factor_case
    result = compute(factor_case, end=130)
    assert result.loadings["AAA"].iloc[:127].isna().all().all()
    np.testing.assert_allclose(result.loadings["AAA"].iloc[127], [0.0001, *coefficients], atol=1e-12)
    assert result.residuals.AAA.iloc[127] == pytest.approx(0.01, abs=1e-12)
    assert not np.allclose(result.loadings["AAA"].iloc[128], result.loadings["AAA"].iloc[127])
    evidence = fit_at(result, 127)
    assert evidence["training_start"] == closes.index[1].isoformat()
    assert evidence["training_end"] == closes.index[126].isoformat()
    assert evidence["training_rows"] == evidence["required_training_rows"] == 126
    assert evidence["coefficient_names"] == ["intercept", *factors]
    assert evidence["rank"] == 10
    assert len(evidence["training_source_hash"]) == len(evidence["evaluation_source_hash"]) == 64
    assert pd.Timestamp(evidence["assumed_training_available_at"]) < pd.Timestamp(
        evidence["assumed_residual_available_at"]
    )


def test_raw_and_residual_momentum_have_exact_skipped_month_windows(factor_case):
    closes, _, _, _ = factor_case
    result = compute(factor_case)
    assert result.raw_momentum.AAA.iloc[:252].isna().all()
    assert result.residual_momentum.AAA.iloc[:378].isna().all()
    position = 390
    expected = result.residuals.AAA.iloc[position - 251 : position - 20]
    assert len(expected) == 231 and expected.notna().all()
    assert result.residual_momentum.AAA.iloc[position] == pytest.approx(expected.mean() / expected.std(ddof=1))
    assert result.raw_momentum.AAA.iloc[position] == pytest.approx(
        closes.AAA.iloc[position - 21] / closes.AAA.iloc[position - 252] - 1
    )


def test_future_prices_volumes_and_longer_prefix_cannot_rewrite_past_evidence(factor_case):
    closes, volumes, _, _ = factor_case
    short = compute(factor_case, end=395)
    changed_closes, changed_volumes = closes.copy(), volumes.copy()
    changed_closes.iloc[395:] *= 10
    changed_volumes.iloc[395:] = 0
    extended = compute(factor_case, closes=changed_closes, volumes=changed_volumes)
    for field in ("raw_momentum", "residual_momentum", "residuals"):
        pd.testing.assert_frame_equal(getattr(short, field), getattr(extended, field).iloc[:395])
    pd.testing.assert_frame_equal(short.loadings["AAA"], extended.loadings["AAA"].iloc[:395])
    assert short.fits == extended.fits[:395]


@pytest.mark.parametrize("column", ["AAA", "ETF0"])
@pytest.mark.parametrize("invalid", [0.0, -1.0, np.nan, np.inf])
def test_bad_endpoint_volume_withholds_two_returns_without_shortening_fit_window(factor_case, column, invalid):
    _, volumes, _, _ = factor_case
    changed = volumes.iloc[:132].copy()
    changed.loc[changed.index[127], column] = invalid
    result = compute(factor_case, end=132, volumes=changed)
    assert result.loadings["AAA"].iloc[127].notna().all()  # Current outcome cannot alter its preceding fit.
    assert result.residuals.AAA.iloc[127:129].isna().all()
    assert result.loadings["AAA"].iloc[128].isna().all()
    assert fit_at(result, 128)["reason"] == "incomplete_training_returns"
    assert fit_at(result, 128)["training_rows"] == 125


@pytest.mark.parametrize("column", ["AAA", "ETF0"])
@pytest.mark.parametrize("invalid", [0.0, -1.0, np.nan, np.inf])
def test_bad_prices_are_unavailable_evidence_not_invented_returns(factor_case, column, invalid):
    closes, _, _, _ = factor_case
    changed = closes.iloc[:130].copy()
    changed.loc[changed.index[127], column] = invalid
    result = compute(factor_case, end=130, closes=changed)
    assert result.residuals.AAA.iloc[127:129].isna().all()
    assert result.loadings["AAA"].iloc[127].notna().all()
    assert result.loadings["AAA"].iloc[128].isna().all()


@pytest.mark.parametrize("noise,reason", [(0.0, "rank_deficient_factors"), (1e-6, "ill_conditioned_factors")])
def test_rank_and_conditioning_fail_closed_with_numerical_evidence(factor_case, noise, reason):
    closes, _, _, _ = factor_case
    changed = closes.iloc[:130].copy()
    returns = changed.ETF0.pct_change(fill_method=None).fillna(0)
    returns = returns + noise * changed.ETF1.pct_change(fill_method=None).fillna(0)
    changed["ETF1"] = 100 * (1 + returns).cumprod()
    result = compute(factor_case, end=130, closes=changed)
    assert result.loadings["AAA"].iloc[127:].isna().all().all()
    assert fit_at(result, 127)["reason"] == reason
    assert fit_at(result, 127)["singular_values"]


def test_perfect_factor_fit_does_not_turn_roundoff_into_momentum(factor_case):
    closes, _, factors, coefficients = factor_case
    changed = closes.copy()
    returns = changed.loc[:, factors].pct_change(fill_method=None).fillna(0).to_numpy() @ coefficients + 0.0001
    returns[0] = 0
    changed["AAA"] = 100 * np.cumprod(1 + returns)
    result = compute(factor_case, closes=changed)
    assert result.residuals.AAA.iloc[127:].abs().max() < 1e-12
    assert result.residual_momentum.AAA.isna().all()


def test_recent_skipped_month_never_changes_either_momentum_score(factor_case):
    closes, volumes, _, _ = factor_case
    original = compute(factor_case)
    changed_closes, changed_volumes = closes.copy(), volumes.copy()
    position = 390
    changed_closes.iloc[position - 20 : position + 1] *= 5
    changed_volumes.iloc[position - 20 : position + 1] = 0
    changed = compute(factor_case, closes=changed_closes, volumes=changed_volumes)
    for field in ("raw_momentum", "residual_momentum"):
        assert getattr(changed, field).AAA.iloc[position] == pytest.approx(getattr(original, field).AAA.iloc[position])


def test_missing_interior_observation_is_preserved_in_scores_and_full_clock(factor_case):
    _, volumes, _, _ = factor_case
    changed = volumes.copy()
    changed.loc[changed.index[150], "AAA"] = 0
    result = compute(factor_case, volumes=changed)
    assert result.raw_momentum.index.equals(volumes.index)
    assert pd.isna(result.raw_momentum.AAA.iloc[390])
    assert pd.isna(result.residual_momentum.AAA.iloc[390])
    assert len(result.fits) == len(volumes)


def test_positive_volume_scale_preserves_features_but_changes_causal_source_evidence(factor_case):
    _, volumes, _, _ = factor_case
    original = compute(factor_case, end=130)
    changed = compute(factor_case, end=130, volumes=volumes * 2)
    pd.testing.assert_frame_equal(original.residuals, changed.residuals)
    pd.testing.assert_frame_equal(original.loadings["AAA"], changed.loadings["AAA"])
    assert fit_at(original, 127)["training_source_hash"] != fit_at(changed, 127)["training_source_hash"]


def test_document_is_json_safe_binds_policy_and_input_hashes_without_mutation(factor_case):
    closes, volumes, _, _ = factor_case
    before_closes, before_volumes = closes.copy(), volumes.copy()
    result = compute(factor_case, end=130)
    document = result.document()
    assert document["contract"]["spec"] == ResidualMomentumSpec().document()
    assert document["contract"]["adjustment"] == "all"
    assert document["authorizes_promotion"] is False
    assert len(document["input_hashes"]["closes"]) == 64
    assert json.dumps(document, allow_nan=False)
    pd.testing.assert_frame_equal(closes, before_closes)
    pd.testing.assert_frame_equal(volumes, before_volumes)


@pytest.mark.parametrize("change", ["clock", "columns", "duplicate_factor", "factor_count", "symbol_overlap"])
def test_alignment_and_explicit_factor_identity_are_required(factor_case, change):
    closes, volumes, factors, _ = factor_case
    closes, volumes = closes.iloc[:3], volumes.iloc[:3].copy()
    symbols = ("AAA",)
    if change == "clock":
        volumes.index = volumes.index + pd.Timedelta(days=1)
    elif change == "columns":
        volumes = volumes.loc[:, list(reversed(volumes.columns))]
    elif change == "duplicate_factor":
        factors = (*factors[:-1], factors[0])
    elif change == "factor_count":
        factors = factors[:-1]
    else:
        symbols = ("ETF0",)
    with pytest.raises(ValueError):
        residual_momentum_features(closes, volumes, symbols=symbols, factors=factors, feed="alpaca:iex")
