"""Factor comparisons bind the complete retained parent before any artifact access."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from agentic_trader.research.alpha.factor_controls_plan import FACTOR_MODELS, FactorControlsPlan
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan


@pytest.fixture
def factor_plan():
    path = Path(__file__).parents[2] / "config/research/screened-equity-forecast-iex-v1.json"
    parent = PanelForecastPlan.from_document(json.loads(path.read_text())["plan"])
    factors = next(c.symbols for c in parent.cohorts if c.name == "sector_etfs")
    return FactorControlsPlan(parent, "equities", "a" * 64, factors)


def test_frozen_factor_contract_and_full_parent_exclusions(factor_plan):
    plan = factor_plan
    assert FactorControlsPlan.from_document(json.loads(json.dumps(plan.document()))) == plan
    assert plan.trial_count == 54 and len(FACTOR_MODELS) == 6
    assert plan.acquisition_symbols == plan.parent.acquisition_symbols and len(plan.acquisition_symbols) == 73
    assert plan.start == plan.parent.start and plan.end == plan.parent.end
    assert plan.feed == "alpaca:iex" and plan.adjustment == "all"
    assert plan.document()["residual_feature"]["fit_sessions"] == 126
    assert plan.document()["residual_feature"]["skip_sessions"] == 21
    assert plan.document()["residual_feature"]["lookback_sessions"] == 252
    assert not plan.document()["authorizes_promotion"]
    plan.validate_as_of(pd.Timestamp("2026-09-18T12:00Z"))
    assert replace(plan, parent_result_sha256="b" * 64).identity != plan.identity


@pytest.mark.parametrize(
    "field,value",
    [
        ("cohort_name", "sector_etfs"),
        ("cohort_name", "absent"),
        ("parent_result_sha256", "bad"),
        ("factor_symbols", ("SPY",)),
        ("factor_symbols", ()),
        ("costs_bps", (0.0, 1.0)),
        ("costs_bps", (True, 5.0)),
        ("costs_bps", (5.0, 1.0)),
    ],
)
def test_factor_plan_rejects_unmatched_contracts(factor_plan, field, value):
    with pytest.raises(ValueError):
        replace(factor_plan, **{field: value})


@pytest.mark.parametrize("fault", ["target", "tail", "hac", "feed", "factors_order"])
def test_factor_plan_keeps_declared_horizon_breadth_and_source(factor_plan, fault):
    parent = factor_plan.parent
    if fault == "target":
        parent = replace(parent, target=replace(parent.target, horizon_bars=5))
    elif fault == "tail":
        parent = replace(parent, cohorts=(replace(parent.cohorts[0], top_k=7), parent.cohorts[1]))
    elif fault == "hac":
        parent = replace(parent, ic=replace(parent.ic, hac_lags=5))
    elif fault == "feed":
        parent = replace(parent, feed="alpaca:sip")
    else:
        with pytest.raises(ValueError):
            replace(factor_plan, factor_symbols=tuple(reversed(factor_plan.factor_symbols)))
        return
    with pytest.raises(ValueError):
        replace(factor_plan, parent=parent)


@pytest.mark.parametrize("field", ["charged_trials", "authorizes_promotion", "residual_feature", "models", "extra"])
def test_factor_document_has_no_implicit_defaults_or_mutable_semantics(factor_plan, field):
    doc = copy.deepcopy(factor_plan.document())
    doc[field] = None
    with pytest.raises(ValueError):
        FactorControlsPlan.from_document(doc)
