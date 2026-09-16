from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.operators import (
    decay_linear,
    ts_corr,
    ts_rank,
)


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    base_price = 100.0 + np.cumsum(np.random.randn(n) * 1.5)
    high = base_price + np.random.uniform(0.5, 2.0, size=n)
    low = base_price - np.random.uniform(0.5, 2.0, size=n)
    open_p = (high + low) / 2.0 + np.random.randn(n) * 0.2
    volume = np.random.uniform(10_000, 50_000, size=n)

    return pd.DataFrame(
        {
            "open": open_p,
            "high": high,
            "low": low,
            "close": base_price,
            "volume": volume,
        },
        index=dates,
    )


def test_dsl_validation_valid_expressions():
    evaluator = AlphaExpressionEvaluator()
    valid = [
        "ts_rank(volume, 10) * -1 * delta(close, 5)",
        "ts_corr(close, volume, 20)",
        "decay_linear(returns, 10)",
        "cond(close > open, volume, -volume)",
        "zscore(close, 20) + ts_std(returns, 10)",
        "ts_rank(delta(close, 3), 10)",
    ]
    for expr in valid:
        assert evaluator.validate(expr) is True, f"Expected '{expr}' to be valid"


def test_dsl_validation_rejects_malicious_or_unknown():
    evaluator = AlphaExpressionEvaluator()
    invalid = [
        "__import__('os').system('echo pwned')",
        "eval('1 + 1')",
        "exec('close = 10')",
        "unknown_func(close)",
        "close + malicious_field",
        "open[0]",
        "lambda x: x",
    ]
    for expr in invalid:
        assert evaluator.validate(expr) is False, f"Expected '{expr}' to be rejected"


def test_dsl_prepare_data_fields(sample_ohlcv):
    fields = AlphaExpressionEvaluator.prepare_data_fields(sample_ohlcv)
    assert "open" in fields
    assert "high" in fields
    assert "low" in fields
    assert "close" in fields
    assert "volume" in fields
    assert "returns" in fields
    assert "hl_spread" in fields
    assert "oc_spread" in fields
    assert "vwap" not in fields
    assert len(fields["returns"]) == len(sample_ohlcv)
    assert fields["returns"].iloc[0] != fields["returns"].iloc[0]
    assert fields["returns"].iloc[1:].notna().all()


def test_dsl_evaluate_basic_expressions(sample_ohlcv):
    evaluator = AlphaExpressionEvaluator()

    expr1 = "delta(close, 1)"
    s1 = evaluator.evaluate(expr1, sample_ohlcv)
    assert isinstance(s1, pd.Series)
    assert len(s1) == len(sample_ohlcv)
    assert np.isclose(s1.iloc[-1], sample_ohlcv["close"].diff(1).iloc[-1])

    expr2 = "ts_rank(volume, 10) * -1"
    s2 = evaluator.evaluate(expr2, sample_ohlcv)
    assert len(s2) == len(sample_ohlcv)
    assert (s2.dropna() <= 0.0).all()
    assert (s2.dropna() >= -1.0).all()

    expr3 = "cond(close > open, 1.0, -1.0)"
    s3 = evaluator.evaluate(expr3, sample_ohlcv)
    assert len(s3) == len(sample_ohlcv)
    assert set(s3.unique()).issubset({-1.0, 1.0})


def test_dsl_evaluate_complex_institutional_alphas(sample_ohlcv):
    evaluator = AlphaExpressionEvaluator()

    # WorldQuant Alpha #6: -1 * ts_corr(open, volume, 10)
    wq6 = "-1 * ts_corr(open, volume, 10)"
    res6 = evaluator.evaluate(wq6, sample_ohlcv)
    assert len(res6) == len(sample_ohlcv)
    assert res6.iloc[:9].isna().all()
    assert res6.iloc[9:].notna().all()
    assert not np.isinf(res6).any()

    # Alpha #54: -1 * (low - close) * (open ** 5) / ((low - high) * (close ** 5))
    wq54 = "-1 * (low - close) * (open ** 5) / ((low - high) * (close ** 5))"
    res54 = evaluator.evaluate(wq54, sample_ohlcv)
    assert len(res54) == len(sample_ohlcv)
    assert not res54.isna().any()
    assert not np.isinf(res54).any()


def test_operators_mathematical_properties(sample_ohlcv):
    close = sample_ohlcv["close"]
    vol = sample_ohlcv["volume"]

    # ts_rank is normalized between 0 and 1
    r = ts_rank(close, 10)
    assert (r.dropna() >= 0.0).all() and (r.dropna() <= 1.0).all()

    # ts_corr is bounded between -1 and 1
    c = ts_corr(close, vol, 15)
    valid_c = c.dropna()
    assert (valid_c >= -1.0 - 1e-6).all() and (valid_c <= 1.0 + 1e-6).all()

    # decay_linear weights more recent observations higher
    series_step = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    dl = decay_linear(series_step, 3)
    assert dl.iloc[-1] > 0
