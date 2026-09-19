import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.panel import cross_sectional_rank, group_neutralize
from agentic_trader.research.alpha.search import SEED_EXPRESSIONS, TypedGeneticSearch, canonical_expression
from agentic_trader.research.alpha.universe import ETF_RESEARCH_UNIVERSE


@pytest.mark.parametrize(
    "expression",
    [
        "roc(close, 5)",
        "realized_vol(returns, 10)",
        "ts_slope(close, 10)",
        "ts_residual(close, 10)",
        "ts_mad(close, 10)",
        "clip(zscore(close, 20), -2, 2)",
        "ts_sum(close*volume,10)",
        "open_gap",
        "roc(close,10)/(realized_vol(returns,20)+1e-6)",
        "(close-ts_min(low,20))/(ts_max(high,20)-ts_min(low,20)+1e-6)",
        "sign(returns)*zscore(volume,20)",
        "ts_corr(returns,delay(returns,1),10)",
        "zscore(ts_slope(close,20),10)",
    ],
)
def test_extended_operators_are_causal(expression):
    rng = np.random.default_rng(19)
    close = 100 + rng.normal(size=100).cumsum()
    frame = pd.DataFrame(
        {
            "open": close + 0.2,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 1000 + rng.integers(0, 100, 100),
        },
        index=pd.date_range("2025-01-01", periods=100),
    )
    evaluator = AlphaExpressionEvaluator()
    full = evaluator.evaluate(expression, frame)
    prefix = evaluator.evaluate(expression, frame.iloc[:70])
    pd.testing.assert_series_equal(full.iloc[:70], prefix)
    assert full.iloc[-1:].notna().all()


def test_genetic_search_is_seeded_bounded_and_actually_crosses_parents():

    first = TypedGeneticSearch(seed=17)
    second = TypedGeneticSearch(seed=17)
    for search in (first, second):
        search.tell("delta(close, 3)", 1)
        search.tell("ts_mean(close, 10)", 2)
    proposals = [first.ask() for _ in range(20)]
    assert proposals == [second.ask() for _ in range(20)]
    assert all(AlphaExpressionEvaluator().validate(p) for p in proposals)
    assert len(set(proposals)) > 10
    child = first.crossover("ts_mean(delta(close,3),10)", "ts_mean(delta(open,5),20)")
    assert AlphaExpressionEvaluator().validate(child)
    assert first.crossover_count > 0


def test_genetic_search_evaluates_each_declared_seed_before_evolution():
    search = TypedGeneticSearch(seed=23)

    proposals = [search.ask() for _ in SEED_EXPRESSIONS]

    assert {canonical_expression(p) for p in proposals} == {canonical_expression(p) for p in SEED_EXPRESSIONS}
    assert search.mutation_count == 0


def test_panel_rank_is_cross_sectional_and_label_aligned():

    frame = pd.DataFrame({"A": [1, 5], "B": [2, 4], "C": [3, 3]}, index=pd.date_range("2020-01-01", periods=2))
    ranked = cross_sectional_rank(frame)
    assert ranked.loc[frame.index[0], "C"] == 1
    assert ranked.loc[frame.index[1], "A"] == 1
    pd.testing.assert_frame_equal(cross_sectional_rank(frame.iloc[:1]), ranked.iloc[:1])
    neutral = group_neutralize(frame, pd.Series({"A": "x", "B": "x", "C": "y"}))
    np.testing.assert_allclose(neutral[["A", "B"]].sum(axis=1), 0)
    assert (neutral.C == 0).all()


def test_research_universe_is_versioned_and_cannot_claim_historical_membership():

    assert 25 <= len(ETF_RESEARCH_UNIVERSE.symbols) <= 40
    assert len(set(ETF_RESEARCH_UNIVERSE.symbols)) == len(ETF_RESEARCH_UNIVERSE.symbols)
    assert ETF_RESEARCH_UNIVERSE.version_id
    assert not ETF_RESEARCH_UNIVERSE.point_in_time_membership
