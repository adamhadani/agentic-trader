"""Six fixed factor scores share causal support and strict unknown-outcome accounting."""

import copy
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha import panel_forecast
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.factor_controls import compute_factor_controls
from agentic_trader.research.alpha.factor_controls_plan import FACTOR_MODELS, FactorControlsPlan
from agentic_trader.research.alpha.information import ICPolicy, summarize_expected_values
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.retained_forecasts import paired_ic_comparison
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


@pytest.fixture
def factor_case():
    clock = pd.date_range("2021-01-04", periods=470, freq="B", tz="America/New_York")
    rng = np.random.default_rng(491)
    stocks, factors = tuple(f"A{i:02d}" for i in range(20)), tuple(f"F{i}" for i in range(9))
    factor_returns = rng.normal(0, 0.007, (len(clock), 9))
    returns = factor_returns @ rng.normal(0, 0.3, (9, 20)) + rng.normal(0.0003, 0.008, (len(clock), 20))
    all_returns = np.column_stack([returns, factor_returns])
    closes = 100 * np.cumprod(1 + all_returns, axis=0)
    opened = np.vstack([np.full(29, 100), closes[:-1]])
    frames = {}
    for j, symbol in enumerate((*stocks, *factors)):
        frame = pd.DataFrame(
            {
                "open": opened[:, j],
                "high": np.maximum(opened[:, j], closes[:, j]) * 1.001,
                "low": np.minimum(opened[:, j], closes[:, j]) * 0.999,
                "close": closes[:, j],
                "volume": 1000.0,
            },
            index=clock,
        )
        frame.attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d")
        frames[symbol] = frame
    parent = PanelForecastPlan(
        campaign_id="factor-fixture",
        selection=ForecastSelection("stocks", "a" * 64, "b" * 64, "c" * 64, pd.Timestamp("2024-01-01T12:00Z")),
        cohorts=(ForecastCohort("stocks", stocks, 8, 16), ForecastCohort("factors", factors, 2, 4)),
        start=clock[0].date(),
        end=clock[-1].date(),
        folds=(PanelFold("test", clock[390].date(), clock[-1].date()),),
        features=(
            PanelHypothesis("momentum60", "roc(close,60)"),
            PanelHypothesis("reversal5", "-roc(close,5)"),
            PanelHypothesis("volatility20", "realized_vol(returns,20)"),
        ),
        economic_features=("momentum60", "reversal5"),
        train_sessions=40,
        min_train_sessions=10,
        min_train_rows=30,
        refit_sessions=20,
        history_sessions=61,
        target=ForecastTarget("1d", 20, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(1.0, 5.0),
        ic=ICPolicy(min_assets=16, min_observations=22, hac_lags=20, observations_per_year=252),
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    batch = DailyStudyInputs(frames)
    original = panel_forecast.compute_panel_forecast(batch, clock, parent, sessions)
    plan = FactorControlsPlan(parent, "stocks", "d" * 64, factors)
    return SimpleNamespace(batch=batch, clock=clock, sessions=sessions, parent=original, plan=plan)


def compute(case):
    return compute_factor_controls(case.batch, case.clock, case.plan, case.sessions, parent_result=case.parent)


def trial(result, model="ridge"):
    return next(t for t in result["trials"] if t["model"] == model)


def test_all_six_arms_use_shared_support_and_reproduce_frozen_ridge_before_masking(factor_case, monkeypatch):
    c = factor_case
    parent = copy.deepcopy(c.parent)
    monkeypatch.setattr(panel_forecast, "_fit", lambda *a, **k: pytest.fail("Frozen Ridge must not refit"))
    result = compute(c)
    original = next(row for row in parent["trials"] if row["cohort"] == "stocks" and row["model"] == "ridge")
    assert trial(result)["predictions"] == original["predictions"]
    assert c.parent == parent
    assert len(result["trials"]) == 6 and result["charged_trials"] == result["completed_comparisons"] == 18
    assert result["supervised_refitted_models"] == 0 and not result["authorizes_promotion"]
    assert all(row["verified"] for row in result["parent_ridge_reproduced"])
    assert {row["model"] for row in result["trials"]} == set(FACTOR_MODELS)
    masks = [np.isfinite(np.array(row["predictions"]["values"], dtype=float)) for row in result["trials"]]
    assert all(np.array_equal(mask, masks[0]) for mask in masks)
    json.dumps(result, allow_nan=False)


def test_raw_momentum_and_rank_blends_have_exact_fixed_boundaries(factor_case):
    c = factor_case
    result = compute(c)
    symbols = c.plan.parent.cohorts[0].symbols
    expected = [c.batch.frames[s].close.iloc[369] / c.batch.frames[s].close.iloc[138] - 1 for s in symbols]
    assert trial(result, "skipped_month_momentum")["predictions"]["values"][0] == pytest.approx(expected)
    base = pd.Series(trial(result, "rank_blend")["predictions"]["values"][0])
    residual = pd.Series(trial(result, "residual_momentum")["predictions"]["values"][0])
    expected_blend = (base.rank(method="average", pct=True) + residual.rank(method="average", pct=True)) / 2
    assert trial(result, "residual_rank_blend")["predictions"]["values"][0] == pytest.approx(expected_blend)


@pytest.mark.parametrize("endpoint", ["entry_bar", "exit_bar"])
def test_future_endpoint_evidence_never_selects_initial_members_or_weights(factor_case, endpoint):
    c = factor_case
    baseline = compute(c)
    basket = trial(baseline)["baskets"][0]
    held = next(s for s, w in basket["weights"].items() if w)
    c.batch.frames[held].loc[pd.Timestamp(basket[endpoint]), "volume"] = 0
    result = compute(c)
    assert result["supports"][0]["common_eligible"][0] == baseline["supports"][0]["common_eligible"][0]
    for before, after in zip(baseline["trials"], result["trials"], strict=True):
        assert before["predictions"]["values"][0] == after["predictions"]["values"][0]
        assert before["baskets"][0]["weights"] == after["baskets"][0]["weights"]
    ridge = trial(result)
    assert ridge["baskets"][0]["status"] == "unavailable"
    assert basket["decision_bar"] in ridge["ic_missing_label_dates"]
    assert all(not row["curve_complete"] and row["net_return"] is None for row in ridge["cost_summaries"])
    assert all(row["expected_baskets"] == 3 for row in result["paired_comparisons"])
    assert any(row["missing_baskets"] > 0 for row in result["paired_comparisons"])


def test_past_missing_volume_reduces_common_breadth_without_silent_symbol_deletion(factor_case):
    c = factor_case
    for symbol in c.plan.parent.cohorts[0].symbols[:5]:
        c.batch.frames[symbol].iloc[200, c.batch.frames[symbol].columns.get_loc("volume")] = 0
    result = compute(c)
    support = result["supports"][0]
    assert len(support["symbols"]) == 20
    assert sum(support["common_eligible"][0]) == 15
    assert all(row["baskets"][0]["status"] == "abstained" for row in result["trials"])
    assert support["stage_counts"]["common_symbol_decisions"] < support["stage_counts"]["frozen_ridge_symbol_decisions"]


def test_parent_ridge_accounting_tamper_fails_before_factor_evaluation(factor_case):
    c = factor_case
    original = next(row for row in c.parent["trials"] if row["cohort"] == "stocks" and row["model"] == "ridge")
    original["baskets"][0]["gross_return"] = 999
    with pytest.raises(ValueError, match="Retained Ridge accounting"):
        compute(c)


def test_exposure_evidence_is_past_only_and_paired_ic_keeps_expected_dates(factor_case):
    result = compute(factor_case)
    exposures = result["basket_factor_exposures"]
    assert len(exposures) == 6 * 3
    assert all(row["status"] == "observed" and row["missing_held_symbols"] == [] for row in exposures)
    assert all(len(row["net_loadings"]) == 9 for row in exposures)
    assert len(result["paired_ic"]) == 5
    for row in result["paired_ic"]:
        assert row["statistics"]["expected"] == 60
        assert row["statistics"]["observed"] == 60
        assert row["statistics"]["mean"] == pytest.approx(np.mean([o["difference"] for o in row["observations"]]))


def test_missing_recent_factor_loading_does_not_invent_exposure_or_change_skipped_features(factor_case):
    c = factor_case
    baseline = compute(c)
    factor = c.plan.factor_symbols[0]
    c.batch.frames[factor].iloc[385, c.batch.frames[factor].columns.get_loc("volume")] = 0
    result = compute(c)
    for before, after in zip(baseline["trials"], result["trials"], strict=True):
        assert before["predictions"]["values"][0] == after["predictions"]["values"][0]
        assert before["baskets"][0]["weights"] == after["baskets"][0]["weights"]
    first = result["basket_factor_exposures"][0]
    assert first["status"] == "unavailable" and first["missing_held_symbols"]
    assert first["net_loadings"] is None and first["absolute_weighted_loadings"] is None


def test_shared_hac_statistics_do_not_stitch_over_unknown_dates():
    policy = ICPolicy(hac_lags=2, min_observations=5)
    result = summarize_expected_values([0.1, 0.2, None, 0.3, 0.4, 0.5], policy)
    assert result["expected"] == 6 and result["observed"] == 5
    assert result["mean"] == pytest.approx(0.3)
    assert result["inference_unavailable"] == "missing_observations" and result["hac_t"] is None


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), "1"])
def test_shared_statistics_reject_invalid_observations(invalid):
    with pytest.raises(ValueError, match="Finite scalar"):
        summarize_expected_values([0.1, invalid], ICPolicy())


@pytest.mark.parametrize("fault", ["timestamp", "fold", "length"])
def test_paired_ic_rejects_clock_misalignment(fault):
    first = {"ic": {"observations": [{"timestamp": "2023-01-02", "fold": "fold", "ic": 0.1}]}}
    second = copy.deepcopy(first)
    if fault == "length":
        second["ic"]["observations"].clear()
    else:
        second["ic"]["observations"][0][fault] = "different"
    with pytest.raises(ValueError, match="identical expected clocks"):
        paired_ic_comparison(first, second, ICPolicy())
