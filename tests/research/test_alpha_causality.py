"""Causal and bounded expression contracts, independent of a broker or runtime DB."""

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.dsl import AlphaDSLSyntaxError, AlphaExpressionEvaluator


@pytest.fixture
def bars():
    rng = np.random.default_rng(124)
    close = 100 + rng.normal(size=200).cumsum()
    return pd.DataFrame(
        {
            "close": close,
            "open": close + 0.2,
            "high": close + 1,
            "low": close - 1,
            "volume": rng.uniform(1000, 10000, 200),
        },
        index=pd.date_range("2025-01-01", periods=200, tz="UTC"),
    )


@pytest.mark.parametrize(
    "expression",
    [
        "delay(close, 5)",
        "delta(close, 5)",
        "ts_rank(close, 10)",
        "ts_corr(close, volume, 10)",
        "zscore(close, 20)",
        "decay_linear(delta(close, 3), 8)",
        "ema(close, 10)",
        "cond(delay(close, 2) > open, close, -close)",
    ],
)
def test_every_prefix_equals_full_history(expression, bars):
    evaluator = AlphaExpressionEvaluator()
    full = evaluator.evaluate(expression, bars)
    for end in (1, 3, 5, 9, 20, 70, 200):
        pd.testing.assert_series_equal(evaluator.evaluate(expression, bars.iloc[:end]), full.iloc[:end])
    perturbed = bars.copy()
    perturbed.iloc[70:] *= 13
    pd.testing.assert_series_equal(evaluator.evaluate(expression, perturbed).iloc[:70], full.iloc[:70])


@pytest.mark.parametrize(
    "expression",
    [
        "rank(close)",
        "scale(close)",
        "delay(close, -1)",
        "delta(close, -1)",
        "sma(close, 0)",
        "sma(close, 1.5)",
        "sma(close)",
        "sma(close, 2, 3)",
        "sma(close, d=5)",
        "close ** 100000",
        "close ** volume",
        "close // 2",
        "close is open",
        "sma",
        "1e999",
        "'hello'",
        "sign(close, 1)",
    ],
)
def test_invalid_expressions_fail_validation_and_execution(expression, bars):
    evaluator = AlphaExpressionEvaluator()
    assert not evaluator.validate(expression)
    with pytest.raises(AlphaDSLSyntaxError):
        evaluator.evaluate(expression, bars)


@pytest.mark.parametrize(
    ("expression", "warmup"),
    [
        ("delay(close, 5)", 5),
        ("delta(close, 5)", 5),
        ("ts_rank(close, 10)", 9),
        ("ts_std(close, 10)", 9),
        ("decay_linear(delta(close, 3), 8)", 10),
        ("cond(delay(close, 2) > open, close, -close)", 2),
    ],
)
def test_unobserved_history_is_not_fabricated(expression, warmup, bars):
    result = AlphaExpressionEvaluator().evaluate(expression, bars)
    assert result.iloc[:warmup].isna().all()
    assert result.iloc[warmup:].notna().all()


@pytest.mark.parametrize("expression", ["volume", "vwap", "hl_spread"])
def test_required_missing_observations_raise(expression, bars):
    with pytest.raises(ValueError, match="[Mm]issing"):
        AlphaExpressionEvaluator().evaluate(expression, bars[["close"]])


@pytest.mark.parametrize("expression", ["1/0", "close/0", "zscore(0*close, 10)"])
def test_undefined_values_remain_missing(expression, bars):
    assert AlphaExpressionEvaluator().evaluate(expression, bars).isna().all()


@pytest.mark.parametrize("expression", ["1/2", "sign(1)", "cond(1, 2, 3)"])
def test_scalars_broadcast(expression, bars):
    result = AlphaExpressionEvaluator().evaluate(expression, bars)
    assert result.index.equals(bars.index)
    assert result.notna().all()


def test_unaligned_inputs_and_duplicate_timestamps_rejected(bars):
    evaluator = AlphaExpressionEvaluator()
    with pytest.raises(ValueError, match="align"):
        evaluator.evaluate("close + open", {"close": bars.close, "open": bars.open.iloc[1:]})
    with pytest.raises(ValueError, match="unique"):
        evaluator.evaluate("close", pd.concat([bars, bars]))
