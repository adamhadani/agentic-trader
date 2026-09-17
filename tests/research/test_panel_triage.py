import copy

import pytest

from agentic_trader.research.alpha.panel_study import PanelTriagePolicy, assess_panel_hypothesis


@pytest.fixture
def complete_panel_trials():
    return [
        {
            "hypothesis": "lead",
            "fold": name,
            "status": "completed",
            "ic": {"folds": {name: {"coverage": 1.0, "observed": 240, "mean_ic": 0.05}}},
            "cost_summaries": [
                {"cost_bps": cost, "baskets": 50, "net_return": net, "missing_features": 0}
                for cost, net in [(0.0, 0.08), (1.0, 0.06), (5.0, 0.03)]
            ],
            "payoffs": [{"costs": [{"cost_bps": 1.0, "net_return": 0.001}]} for _ in range(50)],
        }
        for name in ["2022", "2023"]
    ]


@pytest.mark.parametrize(
    "fault,reason",
    [
        (None, None),
        ("missing", "incomplete_experiment"),
        ("ic", "ic_fold_stability"),
        ("cost", "stress_stability"),
        ("sample", "sample_size"),
        ("feature", "coverage"),
        ("duplicate", "incomplete_experiment"),
    ],
)
def test_fixed_panel_triage_cannot_promote_or_discard_failed_parts(complete_panel_trials, fault, reason):
    trials = copy.deepcopy(complete_panel_trials)
    if fault == "missing":
        trials.pop()
    if fault == "duplicate":
        trials[1] = trials[0]
    if fault == "ic":
        trials[0]["ic"]["folds"]["2022"]["mean_ic"] = -0.01
    if fault == "cost":
        trials[0]["cost_summaries"][2]["net_return"] = -0.01
    if fault == "sample":
        trials[0]["cost_summaries"][1]["baskets"] = 1
    if fault == "feature":
        trials[0]["cost_summaries"][1]["missing_features"] = 1
    result = assess_panel_hypothesis(trials, ("2022", "2023"), PanelTriagePolicy())
    assert not result["authorizes_promotion"]
    assert result["advance_to_further_research"] == (fault is None)
    if reason:
        assert reason in result["reasons"]


@pytest.mark.parametrize(
    "options",
    [
        {"primary_cost_bps": True},
        {"minimum_mean_ic": float("nan")},
        {"minimum_baskets": False},
        {"maximum_positive_basket_share": 2},
    ],
)
def test_invalid_panel_acceptance_policy(options):
    with pytest.raises(ValueError):
        PanelTriagePolicy(**options)
