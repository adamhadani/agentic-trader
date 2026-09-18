"""Matched controls preserve frozen forecasts and outcome-independent portfolios."""

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.forecast_controls import compute_forecast_controls
from agentic_trader.research.alpha.forecast_controls_plan import ForecastControlsPlan
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_forecast import compute_panel_forecast
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


@pytest.fixture
def control_case():
    clock = pd.date_range("2023-01-02", periods=160, freq="B", tz="America/New_York")
    rng = np.random.default_rng(44)
    symbols = tuple(f"A{i}" for i in range(6))
    returns = rng.normal(0.0003, np.linspace(0.003, 0.02, 6), (len(clock), 6))
    close = 100 * np.cumprod(1 + returns, axis=0)
    opened = np.vstack([np.full(6, 100), close[:-1]])
    frames = {}
    for j, symbol in enumerate(symbols):
        frames[symbol] = pd.DataFrame(
            {
                "open": opened[:, j],
                "high": np.maximum(opened[:, j], close[:, j]) * 1.001,
                "low": np.minimum(opened[:, j], close[:, j]) * 0.999,
                "close": close[:, j],
                "volume": 1000.0,
            },
            index=clock,
        )
        frames[symbol].attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d")
    parent = PanelForecastPlan(
        campaign_id="fixture",
        selection=ForecastSelection("stocks", "a" * 64, "b" * 64, "c" * 64, pd.Timestamp("2024-01-01T12:00Z")),
        cohorts=(ForecastCohort("stocks", symbols, top_k=1, min_assets=3),),
        start=clock[0].date(),
        end=clock[-1].date(),
        folds=(PanelFold("test", clock[100].date(), clock[-1].date()),),
        features=(
            PanelHypothesis("momentum60", "roc(close,60)"),
            PanelHypothesis("reversal5", "-roc(close,5)"),
            PanelHypothesis("volatility20", "realized_vol(returns,20)"),
        ),
        economic_features=("momentum60", "reversal5"),
        train_sessions=40,
        min_train_sessions=10,
        min_train_rows=20,
        refit_sessions=5,
        history_sessions=61,
        target=ForecastTarget("1d", 2, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 1.0, 5.0),
        ic=ICPolicy(min_assets=3, min_observations=10, hac_lags=2, observations_per_year=252),
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    batch = DailyStudyInputs(frames)
    result = compute_panel_forecast(batch, clock, parent, sessions)
    plan = ForecastControlsPlan(parent=parent, cohort_name="stocks", parent_result_sha256="d" * 64)
    return SimpleNamespace(batch=batch, clock=clock, sessions=sessions, parent=result, plan=plan)


def compute(case):
    return compute_forecast_controls(case.batch, case.clock, case.plan, case.sessions, parent_result=case.parent)


def trial(result, model="ridge", scope="finite_source_price"):
    return next(t for t in result["trials"] if t["model"] == model and t["outcome_scope"] == scope)


def test_frozen_ridge_price_scope_reproduces_parent_without_refitting(control_case):
    c = control_case
    original = copy.deepcopy(c.parent)
    result = compute(c)
    parent = next(row for row in c.parent["trials"] if row["model"] == "ridge")
    current = trial(result)
    assert current["predictions"] == parent["predictions"]
    assert current["ic"] == parent["ic"]
    assert current["cost_summaries"] == [r for r in parent["cost_summaries"] if r["cost_bps"] in c.plan.costs_bps]
    for before, after in zip(parent["baskets"], current["baskets"], strict=True):
        before = {**before, "costs": [r for r in before["costs"] if r["cost_bps"] in c.plan.costs_bps]}
        assert before == after
    assert c.parent == original
    assert len(result["trials"]) == 10 and result["charged_trials"] == 30
    assert result["refitted_models"] == 0 and not result["authorizes_promotion"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("endpoint", ["entry_bar", "exit_bar"])
def test_volume_scope_masks_outcomes_after_weights_are_frozen(control_case, held, endpoint):
    c = control_case
    baseline = compute(c)
    basket = trial(baseline)["baskets"][0]
    symbol = next(s for s, weight in basket["weights"].items() if bool(weight) == held)
    c.batch.frames[symbol].loc[pd.Timestamp(basket[endpoint]), "volume"] = 0
    changed = compute(c)
    assert trial(changed) == trial(baseline)
    for before, after in zip(baseline["trials"], changed["trials"], strict=True):
        assert before["predictions"] == after["predictions"]
        assert [r["weights"] for r in before["baskets"]] == [r["weights"] for r in after["baskets"]]
    strict = trial(changed, scope="positive_endpoint_volume")
    assert strict["baskets"][0]["status"] == ("unavailable" if held else "observed")
    assert basket["decision_bar"] in strict["ic_missing_label_dates"]
    assert strict["cost_summaries"][0]["curve_complete"] is (not held)


def test_long_only_context_is_equal_weight_and_has_undefined_rank_ic(control_case):
    current = trial(compute(control_case), "equal_weight_long_only")
    for basket in current["baskets"]:
        assert list(basket["weights"].values()) == pytest.approx([1 / 6] * 6)
        assert basket["entry_gross"] == pytest.approx(1)
    assert all(o["ic"] is None and o["reason"] == "constant_score" for o in current["ic"]["observations"])


def test_negative_momentum_and_rank_blend_have_predeclared_directions(control_case):
    c = control_case
    result = compute(c)
    expected = {"reversal60": [], "volatility20": []}
    for frame in c.batch.frames.values():
        expected["reversal60"].append(-(frame.close.iloc[100] / frame.close.iloc[40] - 1))
        recent_returns = frame.close.iloc[81:101].to_numpy() / frame.close.iloc[80:100].to_numpy() - 1
        expected["volatility20"].append(float(np.std(recent_returns, ddof=1)))
    for model, values in expected.items():
        assert trial(result, model)["predictions"]["values"][0] == pytest.approx(values)
    percentile_ranks = [(np.argsort(np.argsort(values)) + 1) / 6 for values in expected.values()]
    assert trial(result, "rank_blend")["predictions"]["values"][0] == pytest.approx(np.mean(percentile_ranks, axis=0))


def test_controls_match_finite_ridge_support_and_retain_pair_denominators(control_case):
    c = control_case
    c.plan = replace(c.plan, parent=replace(c.plan.parent, min_train_rows=1000))
    c.parent = compute_panel_forecast(c.batch, c.clock, c.plan.parent, c.sessions)
    result = compute(c)
    for current in result["trials"]:
        assert current["predictions"]["values"][0] == [None] * 6
        assert current["baskets"][0]["status"] == "abstained"
    paired = result["paired_comparisons"][0]
    assert paired["expected_baskets"] == len(trial(result)["baskets"])
    assert paired["paired_baskets"] == paired["expected_baskets"]
    assert paired["observations"][0]["difference"] == 0


def test_paired_missing_outcome_retains_dates_and_withholds_complete_difference(control_case):
    c = control_case
    original = compute(c)
    first = trial(original)["baskets"][0]
    symbol = next(s for s, weight in first["weights"].items() if weight)
    c.batch.frames[symbol].loc[pd.Timestamp(first["entry_bar"]), "volume"] = 0
    result = compute(c)
    for comparison in result["paired_comparisons"]:
        if comparison["outcome_scope"] == "positive_endpoint_volume":
            assert comparison["observations"][0]["difference"] is None
            assert comparison["paired_baskets"] < comparison["expected_baskets"]
            assert comparison["complete_arithmetic_difference_sum"] is None
            assert "net_return" not in comparison


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_support",
        "duplicate_trial",
        "symbols",
        "dates",
        "shape",
        "nonfinite",
        "false_eligibility",
        "wrong_target",
        "wrong_cohort",
        "wrong_model",
    ],
)
def test_malformed_parent_axes_and_values_fail_closed(control_case, fault):
    c = control_case
    support = c.parent["supports"][0]
    ridge = next(t for t in c.parent["trials"] if t["model"] == "ridge")
    if fault == "duplicate_support":
        c.parent["supports"].append(copy.deepcopy(support))
    elif fault == "duplicate_trial":
        c.parent["trials"].append(copy.deepcopy(ridge))
    elif fault in ("symbols", "dates"):
        ridge["predictions"][fault] = list(reversed(ridge["predictions"][fault]))
    elif fault == "shape":
        ridge["predictions"]["values"][0].pop()
    elif fault == "nonfinite":
        ridge["predictions"]["values"][0][0] = float("inf")
    elif fault == "false_eligibility":
        support["eligible"][0][0] = False
    elif fault == "wrong_target":
        support["targets"][0][0] = 1.23
    elif fault == "wrong_cohort":
        ridge["cohort"] = "other"
    else:
        ridge["model"] = "other"
    with pytest.raises(ValueError):
        compute(c)
