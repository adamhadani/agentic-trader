"""One immutable scientific measurement supports separately labelled family sensitivity."""

import json
from dataclasses import FrozenInstanceError, asdict, replace
from unittest.mock import Mock

import pandas as pd
import pytest

from agentic_trader.research.alpha import promotion
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.validation import ValidationPolicy


@pytest.fixture
def assessment_case(monkeypatch):
    policy = ValidationPolicy(min_dsr=0.4)
    definition = AlphaDefinition(
        "measurement", "Measurement", "close", timeframe="1d", eligible_symbols=("SYNTH",), data_feed="synthetic"
    )
    bars = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        index=pd.date_range("2020-01-01", periods=150, tz="UTC"),
    )
    metrics = {
        "sharpe_oos": 1.2,
        "dsr": 0.5,
        "rank_ic_mean": 0.02,
        "total_trades": 12,
        "per_bar_sharpe": 0.0,
        "sample_length": 100,
        "skewness": 0.0,
        "kurtosis": 3.0,
    }
    run = {
        "policy": asdict(policy),
        "holdout_start": 100,
        "seed": 5,
        "trials": [
            {
                "definition": definition.to_dict(),
                "candidate": {
                    "metrics": metrics,
                    "evidence": {"fold_sharpes": [1.0] * 3, "calibration": {"kind": "fixture"}},
                },
            }
        ],
    }
    manifest = {"symbol": "SYNTH", "feed": "synthetic", "adjustment": "raw", "incumbents": []}
    result = {
        "sharpe": 1.2,
        "total_trades": 12,
        "max_drawdown_pct": 4.0,
        "total_return_pct": 5.0,
        "per_bar_sharpe": 0.0,
        "sample_length": 100,
        "skewness": 0.0,
        "kurtosis": 3.0,
        "net_returns": pd.Series([0.001, 0.002] * 50),
        "trades": [],
        "entries": [],
    }
    stress = {**result, "total_return_pct": 1.0}
    simulator = Mock(
        side_effect=lambda candidate, _bars, **kwargs: (
            result if candidate.execution.friction_per_side == definition.execution.friction_per_side else stress
        )
    )
    bootstrap = Mock(return_value=(0.001, 0.003))
    monkeypatch.setattr(promotion, "simulate_strategy", simulator)
    monkeypatch.setattr(promotion, "block_bootstrap_mean", bootstrap)
    return definition, bars, run, manifest, policy, result, stress, simulator, bootstrap


def assess(case, family=None):
    definition, bars, run, manifest, policy, *_ = case
    return promotion.assess_statistical_evidence(
        definition, bars, run, manifest, family or {"trial_count": 1, "trial_variance": 0}, policy=policy
    )


@pytest.mark.parametrize("scenario", ["pass", "all-thresholds", "simulation-unavailable", "bootstrap-unavailable"])
def test_existing_decisions_and_ordered_reasons_are_preserved(assessment_case, scenario):
    _, _, run, manifest, policy, result, stress, simulator, bootstrap = assessment_case
    family = {"trial_count": 1, "trial_variance": 0}
    reasons = []
    if scenario == "all-thresholds":
        run["trials"][0]["candidate"]["metrics"].update(sharpe_oos=0, dsr=0, rank_ic_mean=0, total_trades=0)
        run["trials"][0]["candidate"]["evidence"]["fold_sharpes"] = [-1, 1, 1]
        result.update(sharpe=0, total_trades=0, max_drawdown_pct=25)
        stress["total_return_pct"] = 0
        bootstrap.return_value = (0, 0.001)
        family = {"trial_count": 100, "trial_variance": 0.1}
        reasons = [
            "validation_sharpe_oos",
            "validation_dsr",
            "validation_rank_ic_mean",
            "validation_total_trades",
            "unstable_validation_folds",
            "family_adjusted_validation_dsr",
            "holdout_sharpe",
            "holdout_trade_count",
            "holdout_dsr",
            "holdout_drawdown",
            "cost_stress",
            "bootstrap_uncertainty",
        ]
    elif scenario.endswith("unavailable"):
        failing = simulator if scenario == "simulation-unavailable" else bootstrap
        failing.side_effect = ValueError("fixture unavailable")
        reasons = ["holdout_unavailable:fixture unavailable"]
    decision = assess(assessment_case, family)
    assert decision["passed"] is (not reasons)
    assert decision["passed"] == all(row["status"] == "pass" for row in decision["criteria"].values())
    assert decision["reasons"] == reasons
    assert decision["policy"] == asdict(policy)
    assert decision["manifest"] == manifest and decision["family"] == family
    assert decision["eligible_symbols"] == ["SYNTH"]
    assert decision["novelty"] == {"status": "no_incumbents"}
    assert decision["calibration"] == {"kind": "fixture"}
    if scenario == "pass":
        assert decision["family_validation_dsr"] == 0.5
        assert decision["holdout"] == {
            **{key: value for key, value in result.items() if key not in ("net_returns", "trades", "entries")},
            "dsr": 0.5,
            "bootstrap_mean_interval": (0.001, 0.003),
            "stressed_return_pct": 1.0,
        }
    elif scenario.endswith("unavailable"):
        assert decision["holdout"] == {}


def test_measure_once_assess_many_preserves_original_result_and_only_family_criteria_vary(assessment_case):
    definition, bars, run, manifest, policy, _, _, simulator, bootstrap = assessment_case
    expected = assess(assessment_case)
    simulator.reset_mock()
    bootstrap.reset_mock()
    measurements = promotion.measure_statistical_evidence(definition, bars, run, manifest, policy=policy)
    baseline = promotion.assess_statistical_measurements(
        measurements, {"trial_count": 1, "trial_variance": 0}, policy=policy
    )
    assert baseline == expected
    for count, variance in [(1, 0.1), (10, 0.1), (1000, 0.5)]:
        decision = promotion.assess_statistical_measurements(
            measurements, {"trial_count": count, "trial_variance": variance}, policy=policy
        )
        assert {
            key: value
            for key, value in decision["criteria"].items()
            if key
            not in (
                "family_adjusted_validation_dsr",
                "holdout_dsr",
            )
        } == {
            key: value
            for key, value in baseline["criteria"].items()
            if key
            not in (
                "family_adjusted_validation_dsr",
                "holdout_dsr",
            )
        }
        assert decision["passed"] == all(row["status"] == "pass" for row in decision["criteria"].values())
        assert "qualified" not in decision
    assert simulator.call_count == 2 and bootstrap.call_count == 1


def test_measurement_bundle_is_canonical_detached_and_policy_bound(assessment_case):
    definition, bars, run, manifest, policy, *_ = assessment_case
    measured = promotion.measure_statistical_evidence(definition, bars, run, manifest, policy=policy)
    original = measured.document()
    original["manifest"]["symbol"] = "MUTATED"
    run["trials"][0]["candidate"]["metrics"]["sharpe_oos"] = -999
    assert measured.document()["manifest"]["symbol"] == "SYNTH"
    assert len(measured.identity) == 64
    assert json.dumps(measured.document(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        measured.canonical_json = "{}"
    with pytest.raises(ValueError, match="policy"):
        promotion.assess_statistical_measurements(
            measured, {"trial_count": 1, "trial_variance": 0}, policy=replace(policy, min_sharpe=2)
        )


@pytest.mark.parametrize("value,label", [(None, "missing"), (float("nan"), "nonfinite"), (True, "invalid_type")])
def test_absent_and_invalid_validation_measurements_are_unavailable(assessment_case, value, label):
    run = assessment_case[2]
    run["trials"][0]["candidate"]["metrics"]["rank_ic_mean"] = value
    decision = assess(assessment_case)
    criterion = decision["criteria"]["validation_rank_ic_mean"]
    assert criterion["value"] is None
    assert criterion["status"] == "unavailable" and criterion["passed"] is None
    assert criterion["unavailable_reason"] == label
    assert "validation_rank_ic_mean" in decision["reasons"]
    assert not decision["passed"]


@pytest.mark.parametrize("field", ["sharpe", "total_trades", "max_drawdown_pct"])
def test_nonfinite_holdout_cannot_pass_by_false_nan_comparison(assessment_case, field):
    assessment_case[5][field] = float("nan")
    decision = assess(assessment_case)
    assert not decision["passed"]
    assert any(reason.startswith("holdout_unavailable:") for reason in decision["reasons"])
    assert decision["holdout"] == {}
    assert any(row["status"] == "unavailable" for row in decision["criteria"].values())


def test_discovery_policy_mismatch_prevents_all_measurement(assessment_case):
    definition, bars, run, manifest, policy, _, _, simulator, bootstrap = assessment_case
    run["policy"] = {}
    with pytest.raises(ValueError, match="policy"):
        promotion.measure_statistical_evidence(definition, bars, run, manifest, policy=policy)
    simulator.assert_not_called()
    bootstrap.assert_not_called()


def test_synthetic_pass_cannot_qualify(assessment_case):
    definition, bars, run, manifest, policy, *_ = assessment_case
    decision = promotion.assess_qualification(
        definition, bars, run, manifest, {"trial_count": 1, "trial_variance": 0}, policy=policy
    )
    assert not decision["qualified"]
    assert decision["reasons"] == ["deployment_data_contract_mismatch"]
    assert decision["qualified"] == all(row["status"] == "pass" for row in decision["criteria"].values())


@pytest.mark.parametrize("field", promotion.SAMPLING_FIELDS)
def test_missing_sampling_moments_are_never_replaced_with_defaults(assessment_case, field):
    metrics = assessment_case[2]["trials"][0]["candidate"]["metrics"]
    metrics.pop(field)
    decision = assess(assessment_case)
    assert not decision["passed"] and decision["family_validation_dsr"] is None
    criterion = decision["criteria"]["family_adjusted_validation_dsr"]
    assert criterion["status"] == "unavailable" and criterion["value"] is None
    assert field in criterion["unavailable_reason"] and "missing" in criterion["unavailable_reason"]
    assert "invalid_validation_sharpe_evidence" in decision["reasons"]


@pytest.mark.parametrize("seed", [None, 1234])
def test_bootstrap_seed_is_explicit_and_bound_in_measurements(assessment_case, seed):
    definition, bars, run, manifest, policy, _, _, _, bootstrap = assessment_case
    measured = promotion.measure_statistical_evidence(
        definition, bars, run, manifest, policy=policy, bootstrap_seed=seed
    )
    expected = run["seed"] if seed is None else seed
    assert bootstrap.call_args.kwargs["seed"] == measured.document()["bootstrap_seed"] == expected


@pytest.mark.parametrize("seed", [True, -1, 1.5])
def test_invalid_bootstrap_seed_prevents_measurement(assessment_case, seed):
    definition, bars, run, manifest, policy, _, _, simulator, bootstrap = assessment_case
    with pytest.raises(ValueError, match="Bootstrap seed"):
        promotion.measure_statistical_evidence(definition, bars, run, manifest, policy=policy, bootstrap_seed=seed)
    simulator.assert_not_called()
    bootstrap.assert_not_called()


def test_bootstrap_failure_does_not_hide_measured_sharpe_and_drawdown(assessment_case):
    assessment_case[8].side_effect = ValueError("fixture bootstrap unavailable")
    decision = assess(assessment_case)
    assert not decision["passed"]
    assert decision["criteria"]["bootstrap_uncertainty"]["status"] == "unavailable"
    for key in ("holdout_sharpe", "holdout_trade_count", "holdout_dsr", "holdout_drawdown", "cost_stress"):
        assert decision["criteria"][key]["status"] == "pass"


@pytest.mark.parametrize("failing_index", [7, 8])
def test_empty_exception_messages_still_withhold_measurement(assessment_case, failing_index):
    assessment_case[failing_index].side_effect = ValueError()
    decision = assess(assessment_case)
    assert decision["reasons"] == ["holdout_unavailable:"]
    assert not decision["passed"]
    assert decision["criteria"]["holdout_available"]["status"] == "unavailable"


def test_novelty_measurement_is_reused_across_family_scenarios(assessment_case, monkeypatch):
    definition, bars, run, manifest, policy, _, _, simulator, bootstrap = assessment_case
    incumbent = replace(definition, alpha_id="incumbent", expression="volume")
    manifest["incumbents"] = [incumbent.to_dict()]
    scores = Mock(return_value=pd.Series(range(len(bars)), index=bars.index, dtype=float))
    novelty = Mock(
        return_value={
            "novel": True,
            "residual_ic": 0.02,
            "p_value": 0.05,
            "training_observations": 40,
            "validation_observations": 40,
            "coefficients": [1, 2],
        }
    )
    monkeypatch.setattr(promotion, "alpha_scores", scores)
    monkeypatch.setattr(promotion, "residual_validation", novelty)
    measured = promotion.measure_statistical_evidence(definition, bars, run, manifest, policy=policy)
    for count in (1, 10, 100):
        decision = promotion.assess_statistical_measurements(
            measured, {"trial_count": count, "trial_variance": 0.01}, policy=policy
        )
        assert decision["novelty"] == novelty.return_value
        assert decision["criteria"]["incremental_predictive_evidence"]["status"] == "pass"
    assert novelty.call_count == 1 and scores.call_count == 2
    assert simulator.call_count == 2 and bootstrap.call_count == 1


@pytest.mark.parametrize(
    "family", [{"trial_count": None, "trial_variance": 0}, {"trial_count": 1, "trial_variance": float("nan")}]
)
def test_unknown_family_parameters_withhold_family_statistics_and_remain_json_safe(assessment_case, family):
    decision = assess(assessment_case, family)
    assert not decision["passed"] and decision["holdout"] == {}
    for key in ("family_adjusted_validation_dsr", "holdout_dsr"):
        assert decision["criteria"][key]["status"] == "unavailable"
        assert decision["criteria"][key]["value"] is None
    assert decision["passed"] == all(row["status"] == "pass" for row in decision["criteria"].values())
    json.dumps(decision, allow_nan=False)


@pytest.mark.parametrize(
    "folds,error", [([1, float("nan"), 1], "nonfinite"), (None, "missing"), ([True, 1, 1], "invalid_type")]
)
def test_fold_unavailability_keeps_original_cause(assessment_case, folds, error):
    assessment_case[2]["trials"][0]["candidate"]["evidence"]["fold_sharpes"] = folds
    row = assess(assessment_case)["criteria"]["unstable_validation_folds"]
    assert row["status"] == "unavailable" and row["unavailable_reason"] == error


def test_null_incumbent_snapshot_is_not_a_known_empty_universe(assessment_case):
    assessment_case[3]["incumbents"] = None
    decision = assess(assessment_case)
    assert not decision["passed"]
    assert decision["criteria"]["incremental_predictive_evidence"]["status"] == "unavailable"
