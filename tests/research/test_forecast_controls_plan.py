"""Retained comparisons freeze contracts before reading any research artifacts."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from agentic_trader.research.alpha.forecast_controls_plan import ForecastControlsPlan
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan


@pytest.fixture
def controls_plan():
    path = Path(__file__).parents[2] / "config/research/screened-equity-forecast-iex-v1.json"
    parent = PanelForecastPlan.from_document(json.loads(path.read_text())["plan"])
    return ForecastControlsPlan(parent, "equities", "a" * 64, (1.0, 5.0))


def test_exact_frozen_contract(controls_plan):
    plan = controls_plan
    assert ForecastControlsPlan.from_document(json.loads(json.dumps(plan.document()))) == plan
    assert plan.trial_count == 90
    assert plan.acquisition_symbols == plan.parent.acquisition_symbols  # Entire retained artifact is inspected.
    assert len(plan.acquisition_symbols) == 73
    assert plan.start == plan.parent.start and plan.end == plan.parent.end
    assert plan.feed == "alpaca:iex" and plan.adjustment == "all"
    plan.validate_as_of(pd.Timestamp("2026-09-18T00:00Z"))
    assert not plan.document()["authorizes_promotion"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("cohort_name", "absent"),
        ("parent_result_sha256", "bad"),
        ("costs_bps", ()),
        ("costs_bps", (1.0, 1.0)),
        ("costs_bps", (float("nan"),)),
        ("costs_bps", (2.0,)),
        ("costs_bps", (True,)),
        ("costs_bps", (5.0, 1.0)),
    ],
)
def test_rejects_unbounded_or_unmatched_policy(controls_plan, field, value):
    with pytest.raises(ValueError):
        replace(controls_plan, **{field: value})


@pytest.mark.parametrize(
    "field,value", [("charged_trials", 1), ("authorizes_promotion", True), ("unknown", 0), ("controls", [])]
)
def test_no_implicit_contract_defaults(controls_plan, field, value):
    doc = copy.deepcopy(controls_plan.document())
    doc[field] = value
    with pytest.raises(ValueError):
        ForecastControlsPlan.from_document(doc)
