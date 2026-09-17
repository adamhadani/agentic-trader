import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.baselines import ForecastBenchmarkPlan, ForecastTarget, benchmark_models
from agentic_trader.research.alpha.miner import AlphaMiner


@pytest.fixture
def baseline_market():
    rng = np.random.default_rng(9)
    close = 100 * np.exp(rng.normal(0, 0.01, 500).cumsum())
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1000, 10000, 500),
        },
        index=pd.date_range("2020-01-01", periods=500, tz="UTC"),
    )
    frame.attrs["timeframe"] = "1d"
    return frame


@pytest.mark.parametrize("method", ["ridge", "boosted", "genetic"])
def test_methods_have_fixed_budget_and_never_select_on_holdout(baseline_market, method):
    def run(frame):
        if method == "genetic":
            miner = AlphaMiner(seed=17)
            miner.mine(frame, iterations=3, include_catalog=False, method=method)
            return miner.last_run
        return benchmark_models(
            frame, ForecastBenchmarkPlan(ForecastTarget("1d"), method=method, seed=17, budget=3)
        ).document()

    initial = run(baseline_market)
    changed = baseline_market.copy()
    changed.iloc[400:, :4] *= 7
    assert initial == run(changed)
    assert initial["trial_count"] == 3
    assert len(initial["trials"]) == 3
