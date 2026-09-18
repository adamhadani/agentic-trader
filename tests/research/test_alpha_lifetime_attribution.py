"""Paired lifetime counterfactuals keep selection fixed and remain pure."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.lifetime_artifacts import execute_lifetime_study
from agentic_trader.research.alpha.lifetime_attribution import (
    LifetimeAttributionProtocol,
    LifetimeVariant,
    evaluate_lifetime_attribution,
    lifetime_jobs,
    lifetime_variant_specs,
)
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.study import MarketScenario, market_bars


@pytest.fixture
def source_definition():
    return AlphaDefinition(
        "fixture_daily",
        "Fixture daily",
        "volume",
        timeframe="1d",
        eligible_symbols=("SYNTH",),
        data_feed="synthetic",
    )


def test_variant_matrix_has_one_original_and_independent_deadlines():
    specs = lifetime_variant_specs()
    assert [spec.variant for spec in specs] == [
        LifetimeVariant.ORIGINAL,
        LifetimeVariant.ENTRY_ONLY,
        LifetimeVariant.ENTRY_AND_HOLDING,
    ]
    assert specs[0].resting_seconds is None and specs[0].holding_seconds is None
    assert specs[1].holding_seconds is None and specs[2].holding_seconds is not None


def test_protocol_roundtrips_and_declares_bounded_paired_budget():
    protocol = LifetimeAttributionProtocol(
        seed=441,
        development_replicates=1,
        null_replicates=1,
        edge_replicates=1,
        generated_candidates=1,
        observations=600,
        search_timeout_seconds=5,
    )
    restored = LifetimeAttributionProtocol.from_document(protocol.document())
    assert restored == protocol
    assert protocol.document()["budget"]["variant_replays_max"] == len(tuple(lifetime_jobs(protocol))) * 6


def test_small_lifetime_artifact_run_is_pure_and_retains_selection(tmp_path):
    protocol = LifetimeAttributionProtocol(
        seed=442,
        development_replicates=1,
        null_replicates=1,
        edge_replicates=1,
        generated_candidates=1,
        observations=600,
        search_timeout_seconds=5,
    )
    summary = execute_lifetime_study(protocol, tmp_path / "run", {"fixture": True})
    assert summary["status"] == "completed"
    assert summary["recorded_jobs"] == summary["expected_jobs"]
    assert summary["authorizes_promotion"] is False
    assert list((tmp_path / "run" / "selection").glob("*.json"))


def test_lifetime_attribution_reuses_selection_and_scores(source_definition):
    bars = market_bars(
        MarketScenario(name="fixture", observations=600, interval=8, effect=0.0, volatility_persistence=0.0), seed=12
    )
    scores = pd.Series(np.linspace(-2, 2, len(bars)), index=bars.index)
    report = evaluate_lifetime_attribution(source_definition, bars, scores=scores)
    assert report["synthetic_only"] and not report["authorizes_promotion"]
    assert len(report["variants"]) == 3
    assert {v["source_definition_id"] for v in report["variants"]} == {source_definition.version_id}
    assert len({v["definition_id"] for v in report["variants"]}) == 3
    original = simulate_strategy(source_definition, bars, scores=scores, trace=True)
    reference = report["variants"][0]
    assert reference["total_return_pct"] == original["total_return_pct"]
    assert reference["trades"] == original["trades"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeframe": "4h"},
        {"data_feed": "alpaca:iex"},
    ],
)
def test_lifetime_attribution_rejects_non_synthetic_daily_source(source_definition, overrides):
    definition = replace(source_definition, **overrides)
    with pytest.raises(ValueError, match="synthetic daily"):
        evaluate_lifetime_attribution(
            definition,
            market_bars(
                MarketScenario(name="fixture", observations=600, interval=8, effect=0.0, volatility_persistence=0.0),
                seed=12,
            ),
        )
