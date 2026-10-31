import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.dsl import AlphaDSLSyntaxError, AlphaExpressionEvaluator, compile_expression
from agentic_trader.research.alpha.panel import cross_sectional_regress_out, group_zscore
from agentic_trader.research.pooled.formula import Formula, require_dimensionless


EVALUATOR = AlphaExpressionEvaluator()


def _fields(n=80, seed=3):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    x = pd.Series(rng.normal(size=n), index=index)
    y = pd.Series(rng.normal(size=n), index=index)
    return index, x, y


def test_ts_beta_recovers_a_known_slope():
    _, x, noise = _fields()
    y = 2 * x + 0.3 * noise
    out = EVALUATOR.evaluate("ts_beta(returns, mkt_ret, 20)", {"returns": y, "mkt_ret": x})
    assert out.iloc[:19].isna().all()
    for end in (19, 40, 79):
        window = slice(end - 19, end + 1)
        slope = np.polyfit(x.iloc[window].to_numpy(), y.iloc[window].to_numpy(), 1)[0]
        assert abs(out.iloc[end] - slope) < 1e-9


def test_ts_beta_is_nan_when_x_has_no_variance():
    index, _, y = _fields()
    out = EVALUATOR.evaluate("ts_beta(returns, mkt_ret, 20)", {"returns": y, "mkt_ret": pd.Series(0.01, index=index)})
    assert out.isna().all()


def test_ts_resid_std_matches_a_single_ols_fit_per_window():
    _, x, y = _fields()
    out = EVALUATOR.evaluate("ts_resid_std(returns, mkt_ret, 20)", {"returns": y, "mkt_ret": x})
    for end in (19, 40, 79):
        xs, ys = x.iloc[end - 19 : end + 1].to_numpy(), y.iloc[end - 19 : end + 1].to_numpy()
        slope, intercept = np.polyfit(xs, ys, 1)
        expected = np.sqrt(((ys - slope * xs - intercept) ** 2).sum() / 18)
        assert out.iloc[end] == pytest.approx(expected)
    assert out.iloc[:19].isna().all()


def test_ts_resid_std_floors_tiny_negative_variance():
    _, x, _ = _fields()
    out = EVALUATOR.evaluate("ts_resid_std(returns, mkt_ret, 20)", {"returns": 3 * x, "mkt_ret": x})
    assert out.iloc[19:].notna().all()
    assert out.iloc[19:].to_numpy() == pytest.approx(0.0, abs=1e-6)


def test_ts_beta_of_matching_units_is_dimensionless():
    require_dimensionless("ts_beta(close, close, 60)")
    require_dimensionless("ts_beta(returns, mkt_ret, 60)")


def test_beta_over_price_fails_require_dimensionless():
    with pytest.raises(ValueError, match="dimensionless"):
        require_dimensionless("ts_beta(close, close, 60) / close")


def test_ts_resid_std_of_prices_fails_require_dimensionless():
    with pytest.raises(ValueError, match="dimensionless"):
        require_dimensionless("ts_resid_std(close, close, 60)")


def test_ts_beta_units_refuse_mixed_arguments():
    with pytest.raises(AlphaDSLSyntaxError):
        compile_expression("ts_beta(close, volume, 60)")


def test_mkt_ret_is_refused_on_a_frame_without_it():
    _, x, _ = _fields()
    with pytest.raises(ValueError, match="mkt_ret"):
        EVALUATOR.evaluate("ts_beta(returns, mkt_ret, 20)", pd.DataFrame({"close": 100 + x.cumsum()}))


def test_existing_identity_unchanged():
    formula = Formula(score="-1.0 * roc(close, 5)", k=3)
    assert formula.identity == "7c3c5cda2db4200599323fce78a6fd8b32b21442e14b8b130883f8f09c5880ff"


def _panel(rows=6, cols=12, seed=1):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-02", periods=rows, freq="B")
    return pd.DataFrame(rng.normal(size=(rows, cols)), index=index, columns=[f"S{i}" for i in range(cols)])


def test_group_zscore_is_zero_mean_unit_std_inside_each_group():
    panel = _panel()
    groups = pd.Series(["a"] * 6 + ["b"] * 6, index=panel.columns)
    out = group_zscore(panel, groups, min_names=5)
    for name in ("a", "b"):
        block = out[groups.index[groups == name]]
        assert block.mean(axis=1).abs().max() < 1e-12
        assert block.std(axis=1).to_numpy() == pytest.approx(1.0)


def test_group_zscore_is_nan_below_min_names():
    panel = _panel()
    groups = pd.Series(["a"] * 6 + ["b"] * 6, index=panel.columns)
    panel.loc[panel.index[0], ["S0", "S1"]] = np.nan  # group a has 4 finite names on row 0
    out = group_zscore(panel, groups, min_names=5)
    assert out.iloc[0][[f"S{i}" for i in range(6)]].isna().all()
    assert out.iloc[0][[f"S{i}" for i in range(6, 12)]].notna().all()
    assert out.iloc[1].notna().all()


def test_regress_out_residual_is_uncorrelated_with_regressors():
    panel = _panel(rows=4, cols=40)
    a, b = _panel(4, 40, seed=7), _panel(4, 40, seed=8)
    out = cross_sectional_regress_out(panel + 2 * a - b, [a, b], min_rows=30)
    for i in range(4):
        assert abs(np.corrcoef(out.iloc[i], a.iloc[i])[0, 1]) < 1e-10
        assert abs(np.corrcoef(out.iloc[i], b.iloc[i])[0, 1]) < 1e-10
        assert out.iloc[i].mean() == pytest.approx(0.0, abs=1e-10)


def test_regress_out_needs_all_regressors_finite_and_min_rows():
    panel = _panel(rows=3, cols=30)
    reg = _panel(3, 30, seed=5)
    reg.iloc[0, 0] = np.nan  # 29 usable of 30 on row 0
    out = cross_sectional_regress_out(panel, [reg], min_rows=30)
    assert out.iloc[0].isna().all()
    assert out.iloc[1:].notna().all().all()


def test_regress_out_validates_like_the_other_kernels():
    panel = _panel()
    with pytest.raises(ValueError):
        cross_sectional_regress_out(panel, [panel.iloc[:-1]], min_rows=5)
    with pytest.raises(ValueError):
        cross_sectional_regress_out(panel, [panel.rename(columns={"S0": "Z"})], min_rows=5)
