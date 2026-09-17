"""Causal fixed-horizon panel diagnostics, with no execution authority."""

from dataclasses import replace
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.panel import align_daily_panel
from agentic_trader.research.alpha.panel_study import (
    PanelStudyPlan,
    basket_weights,
    compute_panel_study,
)
from agentic_trader.research.alpha.targets import ForecastTarget


@pytest.mark.parametrize(
    "scores,expected",
    [
        ([1, 2, 3, 4], [-0.5, 0, 0, 0.5]),
        ([1, 1, 3, 3], [-0.25, -0.25, 0.25, 0.25]),
        ([1, 1, 1, 1], [0, 0, 0, 0]),
    ],
)
def test_tied_rank_allocations_preserve_zero_net_and_bounded_gross(scores, expected):
    result = basket_weights(pd.Series(scores, index=list("ABCD"), dtype=float), 1)
    np.testing.assert_allclose(result, expected)
    assert abs(result.sum()) < 1e-12 and result.abs().sum() <= 1


def test_exact_proxy_cash_inventory_fees_and_disjoint_holding_intervals(panel_study_input):
    frames, clock, plan = panel_study_input
    result = compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)
    assert result["charged_trials"] == 16 and not result["authorizes_promotion"]
    for trial in result["trials"]:
        assert trial["status"] == "completed"
        fold = next(f for f in plan.folds if f.name == trial["fold"])
        dates = clock[(clock.date >= fold.start) & (clock.date <= fold.end)]
        obs = trial["payoffs"]
        assert len(obs) == len(dates[:-5:5])
        for item in obs:
            t = pd.Timestamp(item["signal_bar"])
            i = clock.get_loc(t)
            assert pd.Timestamp(item["entry_bar"]) == clock[i + 1]
            assert pd.Timestamp(item["exit_bar"]) == clock[i + 5]
            weights = item["weights"]
            gross = 0
            exit_notional = 0
            for symbol, weight in weights.items():
                price_ratio = frames[symbol].close.iloc[i + 5] / frames[symbol].open.iloc[i + 1]
                gross += weight * (price_ratio - 1)
                exit_notional += abs(weight) * price_ratio
            for scenario in item["costs"]:
                assert scenario["net_return"] == pytest.approx(
                    gross - scenario["cost_bps"] / 10000 * (sum(abs(w) for w in weights.values()) + exit_notional)
                )
        assert all(pd.Timestamp(a["exit_bar"]) < pd.Timestamp(b["entry_bar"]) for a, b in pairwise(obs))
        assert trial["ic"]["folds"][fold.name]["expected"] == len(dates) - 5


def test_later_fold_prices_cannot_change_earlier_report_or_predictor_weights(panel_study_input):
    frames, clock, plan = panel_study_input
    original = compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)
    for f in frames.values():
        f.iloc[120:, :4] *= 2
    changed = compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)
    for a, b in zip(original["trials"], changed["trials"], strict=True):
        if a["fold"] == "first":
            assert a == b
    # Change only the future outcome of the first decision: allocation stays fixed.
    for f in frames.values():
        f.iloc[75, :4] *= 1.1
    changed_again = compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)
    assert original["trials"][0]["payoffs"][0]["weights"] == changed_again["trials"][0]["payoffs"][0]["weights"]


def test_missing_prices_fail_the_declared_panel_without_selecting_a_smaller_universe(panel_study_input):
    frames, clock, plan = panel_study_input
    frames["AAA"] = frames["AAA"].drop(clock[90])
    with pytest.raises(ValueError, match="coverage"):
        compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)


@pytest.mark.parametrize(
    "change",
    [
        {"symbols": ("AAA", "AAA")},
        {"top_k": 3},
        {"costs_bps": (5.0, 1.0)},
        {"target": ForecastTarget("1d", 5)},
        {"hypotheses": ()},
        {"feed": "yfinance"},
    ],
)
def test_invalid_panel_plan_is_rejected_before_acquisition(panel_study_input, change):
    with pytest.raises(ValueError):
        replace(panel_study_input[2], **change)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "unknown"),
        ("charged_trials", 1),
        ("target", {}),
        ("availability", "same_close"),
        ("unrecognized", True),
    ],
)
def test_protocol_metadata_cannot_be_ignored(panel_study_input, field, value):
    plan = panel_study_input[2]
    document = plan.document()
    document[field] = value
    with pytest.raises(ValueError):
        PanelStudyPlan.from_document(document)


def test_frozen_protocol_round_trip(panel_study_input):
    plan = panel_study_input[2]
    assert PanelStudyPlan.from_document(plan.document()).identity == plan.identity


def test_native_day_signal_label_is_not_mislabeled_as_decision_or_fill_time(panel_study_input):
    frames, clock, plan = panel_study_input
    trial = compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)["trials"][0]
    for payoff in trial["payoffs"]:
        assert pd.Timestamp(payoff["assumed_decision_at"]) == pd.Timestamp(payoff["signal_bar"]) + pd.DateOffset(days=1)
        assert pd.Timestamp(payoff["assumed_decision_at"]) <= pd.Timestamp(payoff["entry_bar"])
        assert "entry_at" not in payoff and "exit_at" not in payoff


def test_ic_report_carries_its_explicit_forecast_target(panel_study_input):
    frames, clock, plan = panel_study_input
    trial = compute_panel_study(align_daily_panel(frames, clock, feed=plan.feed), plan)["trials"][0]
    assert trial["ic"]["target"] == plan.target.document()
