from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="4h")
    # Generate random walk with drift
    ret = np.random.normal(0.001, 0.02, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(np.random.normal(0, 0.01, size=n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.01, size=n)))
    open_p = (high + low) / 2.0
    volume = np.random.uniform(500, 2000, size=n)

    frame = pd.DataFrame(
        {
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )
    frame.attrs["timeframe"] = "4h"
    return frame


def test_generate_candidate_expression():
    miner = AlphaMiner()
    defn = miner.generate_candidate_expression(seed=123)
    assert isinstance(defn, AlphaDefinition)
    assert defn.alpha_id.startswith("alpha_m_")
    assert len(defn.expression) > 0
    assert defn.timeframe == "4h"


@pytest.mark.parametrize("seed", range(16))
def test_generated_candidates_use_only_acquired_causal_fields(synthetic_ohlcv: pd.DataFrame, seed: int):
    miner = AlphaMiner()
    available = set(miner.evaluator.prepare_data_fields(synthetic_ohlcv))
    definition = miner.generate_candidate_expression(seed=seed, available_fields=available)

    assert miner.evaluator.validate(definition.expression)
    result = miner.evaluator.evaluate(definition.expression, synthetic_ohlcv)
    assert result.index.equals(synthetic_ohlcv.index)
    assert definition.expression.count("vwap") == 0


def test_generated_candidates_fail_closed_when_derived_fields_are_unavailable():
    miner = AlphaMiner()
    definition = miner.generate_candidate_expression(seed=123, available_fields={"close", "volume"})

    assert miner.evaluator.validate(definition.expression)
    assert all(field not in definition.expression for field in ("open_gap", "hl_spread", "oc_spread", "returns"))


def test_evaluate_alpha(synthetic_ohlcv: pd.DataFrame):
    miner = AlphaMiner()
    defn = AlphaDefinition(
        alpha_id="alpha_test_eval",
        name="Test Eval",
        expression="delta(close, 3)",
        direction="bi_directional",
        entry_threshold=0.5,
    )
    candidate = miner.evaluate_alpha(defn, synthetic_ohlcv, total_trials=5)
    assert candidate is not None
    assert candidate.definition.alpha_id == "alpha_test_eval"
    assert candidate.metrics is not None
    assert isinstance(candidate.metrics.sharpe_is, float)
    assert isinstance(candidate.metrics.sharpe_oos, float)
    assert 0.0 <= candidate.metrics.dsr <= 1.0


def test_mine_with_relaxed_gates(synthetic_ohlcv: pd.DataFrame):
    miner = AlphaMiner()
    # Relaxed gating criteria to ensure some candidates pass on synthetic data
    candidates = miner.mine(
        synthetic_ohlcv,
        timeframe="4h",
        iterations=5,
        include_catalog=True,
        min_sharpe=-2.0,
        min_dsr=0.0,
        min_ic=-1.0,
        max_correlation=1.0,
    )
    assert isinstance(candidates, list)
    assert len(candidates) > 0
    # Confirm sorted by composite score descending
    scores = [
        0.5 * c.metrics.sharpe_oos + 0.3 * c.metrics.dsr + 0.2 * max(0.0, min(2.0, c.metrics.rank_ic_ir))
        for c in candidates
        if c.metrics is not None
    ]
    assert scores == sorted(scores, reverse=True)
