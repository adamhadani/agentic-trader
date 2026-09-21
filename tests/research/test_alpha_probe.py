import math
from dataclasses import asdict

import pytest

from agentic_trader.research.alpha.probe import (
    PROBE_POLICY_VERSION,
    ProbePolicy,
    assess_probe,
    forward_record,
    is_paper_scope,
)


def criterion(value, status="pass"):
    return {
        "value": value,
        "threshold": None,
        "comparison": "ge",
        "status": status,
        "passed": None if status == "unavailable" else status == "pass",
        "unavailable_reason": "boom" if status == "unavailable" else None,
        "reason_code": None,
    }


def decision(**overrides):
    criteria = {
        "holdout_available": criterion(True),
        "holdout_sharpe": criterion(1.31, "fail"),  # fails ValidationPolicy's 1.0? irrelevant: probe reads the value
        "holdout_trade_count": criterion(7, "fail"),
        "cost_stress": criterion(2.4),
        "deployment_data_contract": criterion({"feed": "alpaca:iex"}),
        "intraday_session_execution_unverified": criterion("1d"),
        "recursive_feature_requires_shared_initialization": criterion(False),
    }
    criteria.update(overrides)
    return {"qualified": False, "reasons": ["holdout_dsr"], "criteria": criteria}


def test_policy_defaults_are_the_frozen_spec_values():
    assert asdict(ProbePolicy()) == {
        "version": PROBE_POLICY_VERSION,
        "min_holdout_sharpe": 0.0,
        "min_cost_stressed_return_pct": 0.0,
        "min_holdout_trades": 5,
        "max_term_days": 180,
        "kill_r": -4.0,
    }


def test_statistically_rejected_but_economically_positive_candidate_is_eligible():
    result = assess_probe(decision())
    assert result.eligible and result.reasons == ()
    assert result.observations == {"holdout_sharpe": 1.31, "holdout_trade_count": 7, "cost_stress": 2.4}


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"holdout_sharpe": criterion(0.0, "fail")}, "holdout_sharpe_below_probe_floor"),
        ({"holdout_sharpe": criterion(-0.2, "fail")}, "holdout_sharpe_below_probe_floor"),
        ({"cost_stress": criterion(0.0, "fail")}, "cost_stress_below_probe_floor"),
        ({"holdout_trade_count": criterion(4, "fail")}, "holdout_trade_count_below_probe_floor"),
        ({"holdout_sharpe": criterion(None, "unavailable")}, "holdout_sharpe_unavailable"),
        ({"cost_stress": criterion(math.nan)}, "cost_stress_invalid"),
        ({"holdout_trade_count": criterion(True)}, "holdout_trade_count_invalid"),
        ({"holdout_available": criterion(False, "unavailable")}, "holdout_available_failed"),
        ({"deployment_data_contract": criterion({}, "fail")}, "deployment_data_contract_failed"),
        (
            {"intraday_session_execution_unverified": criterion("4h", "fail")},
            "intraday_session_execution_unverified_failed",
        ),
        (
            {"recursive_feature_requires_shared_initialization": criterion(True, "fail")},
            "recursive_feature_requires_shared_initialization_failed",
        ),
    ],
)
def test_each_probe_criterion_fails_closed(override, reason):
    result = assess_probe(decision(**override))
    assert not result.eligible
    assert reason in result.reasons


def test_missing_document_or_criteria_is_rejected_not_treated_as_zero():
    assert assess_probe(None).reasons == ("qualification_missing",)
    missing = decision()
    del missing["criteria"]["cost_stress"]
    assert "cost_stress_missing" in assess_probe(missing).reasons


def test_forward_record_sums_r_and_ignores_unknown_outcomes():
    record = forward_record([(50.0, 100.0), (-100.0, 100.0), (None, 100.0), (10.0, 0.0), (math.inf, 100.0)])
    assert record == {"trades": 2, "unknown": 3, "cumulative_r": -0.5, "kill_r": -4.0, "killed": False}


def test_forward_record_kills_at_the_threshold_inclusive():
    assert forward_record([(-100.0, 100.0)] * 4)["killed"] is True
    assert forward_record([(-100.0, 100.0)] * 3 + [(-99.0, 100.0)])["killed"] is False


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("production/alpaca:paper", True),
        ("development/alpaca:paper", True),
        ("production/alpaca:live", False),
        ("production/paper", False),
        ("production/unknown", False),
        ("alpaca:paper", False),
    ],
)
def test_only_brokerage_paper_scope_is_a_probe_scope(scope, expected):
    assert is_paper_scope(scope) is expected
