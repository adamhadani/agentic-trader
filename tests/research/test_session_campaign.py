"""Freeze the economic experiment and test its arithmetic before reading market results."""

import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from scripts.run_session_campaign import FrozenSessionSource, campaign_jobs, summarize_trial, triage


@pytest.fixture
def campaign_protocol():
    return json.loads((Path(__file__).parents[2] / "config/research/etf-session-v1.json").read_text())


def test_frozen_matrix_has_exact_budget_unique_plans_and_shared_execution(campaign_protocol):
    jobs = campaign_jobs(campaign_protocol)
    assert len(jobs) == 81 == campaign_protocol["budget"]
    assert len({job["plan"].identity for job in jobs}) == 81
    assert {j["plan"].definition.data_feed for j in jobs} == {"alpaca:sip"}
    assert {j["plan"].definition.semantics_version for j in jobs} == {3}
    assert {j["plan"].definition.execution.friction_per_side for j in jobs} == {0, 0.0001, 0.0005}


@pytest.mark.parametrize("defect", ["budget", "duplicate", "cost", "overlap", "lookahead"])
def test_invalid_campaign_fails_before_provider_or_journal_access(campaign_protocol, defect):
    p = campaign_protocol
    if defect == "budget":
        p["budget"] += 1
    elif defect == "duplicate":
        p["symbols"][1] = p["symbols"][0]
    elif defect == "cost":
        p["cost_bps_per_side"][1] = float("nan")
    elif defect == "overlap":
        p["windows"][1] = p["windows"][0]
    else:
        p["hypotheses"][0]["expression"] = "delay(close,-1)"
    with pytest.raises(ValueError):
        campaign_jobs(p)


@pytest.mark.parametrize("failure", [False, True])
def test_comparisons_share_one_snapshot_and_provider_failures_are_not_retried(failure):
    frame = pd.DataFrame({"close": [1.0]})
    underlying = Mock()
    underlying.minutes.side_effect = TimeoutError("fixture") if failure else None
    underlying.minutes.return_value = frame
    source = FrozenSessionSource(underlying)
    for _ in range(2):
        if failure:
            with pytest.raises(TimeoutError):
                source.minutes("SPY", "a", "b", "alpaca:sip")
        else:
            copy = source.minutes("SPY", "a", "b", "alpaca:sip")
            assert copy.iloc[0, 0] == 1
            copy.iloc[0, 0] = 20
    underlying.minutes.assert_called_once()


def test_summary_uses_full_clock_log_influence_and_passive_entry_cost():
    index = pd.date_range("2024-01-01 15:00Z", periods=3, freq="D")
    result = {
        "status": "completed",
        "net_returns": [
            {"bar_start": t.isoformat(), "net_return": v} for t, v in zip(index, [0.1, -0.05, 0.02], strict=True)
        ],
        "total_trades": 2,
        "entries": [],
        "open_position": True,
        "pending_entry": False,
        "coverage": {"expected_minutes": 3, "observed_minutes": 3, "missing_minutes": 0},
        "signal_feature_coverage": {"bars": 10, "scored_bars": 8},
    }
    raw = pd.DataFrame({"open": [100, 100, 100], "close": [100, 100, 110]}, index=index)
    summary = summarize_trial(result, raw, 1)
    assert summary["net_return"] == pytest.approx(1.1 * 0.95 * 1.02 - 1)
    assert summary["benchmark_return"] == pytest.approx(0.1 - 0.0001)
    assert sum(summary["daily_log_returns"]) == pytest.approx(np.log(1.1 * 0.95 * 1.02))
    assert summary["open_position"] and summary["feature_coverage"] == 0.8


@pytest.mark.parametrize("defect", [None, "missing", "concentrated", "costs", "trades", "benchmark"])
def test_triage_keeps_missing_concentrated_or_cost_fragile_candidates_unselected(campaign_protocol, defect):
    primary = [
        {
            "status": "completed",
            "coverage_complete": True,
            "feature_coverage": 1.0,
            "closed_trades": 12,
            "net_return": 0.03,
            "benchmark_return": 0.01,
            "daily_log_returns": [np.log(1.03) / 10] * 10,
        }
        for _ in range(3)
    ]
    stressed = [{**r, "net_return": 0.01} for r in primary]
    if defect == "missing":
        primary[0]["status"] = "failed"
    elif defect == "concentrated":
        primary[0]["daily_log_returns"] = [0.2, -0.2 + np.log(1.03)]
    elif defect == "costs":
        stressed[0]["net_return"] = -0.5
    elif defect == "trades":
        primary[0]["closed_trades"] = 0
    elif defect == "benchmark":
        primary[0]["benchmark_return"] = 0.5
    result = triage(primary, stressed, campaign_protocol["triage"], expected_blocks=3)
    assert result["advance_to_further_research"] is (defect is None)
    assert result["authorizes_promotion"] is False


def test_timed_campaign_creates_new_policy_without_rewriting_frozen_protocol(campaign_protocol):
    original = campaign_jobs(campaign_protocol)[0]["plan"].identity
    campaign_protocol["execution"]["lifetime"] = {"resting_seconds": 300, "holding_seconds": 86400}
    timed = campaign_jobs(campaign_protocol)[0]["plan"]
    assert timed.identity != original
    assert timed.definition.execution.lifetime.resting_seconds == 300


def test_continuous_timed_protocol_freezes_new_search_and_adequate_trade_screen():
    protocol = json.loads((Path(__file__).parents[2] / "config/research/etf-continuous-timed-v1.json").read_text())
    jobs = campaign_jobs(protocol)
    assert len(jobs) == 48 and len({job["plan"].identity for job in jobs}) == 48
    assert len({job["hypothesis"] for job in jobs}) == 4
    assert all(364 <= (job["plan"].end - job["plan"].start).days <= 365 for job in jobs)
    assert all(job["plan"].definition.execution.lifetime.resting_seconds == 300 for job in jobs)
    assert all(job["plan"].definition.execution.lifetime.holding_seconds == 86400 for job in jobs)
    assert protocol["triage"]["minimum_closed_trades"] == 100
    assert protocol["triage"]["minimum_positive_blocks"] == len(protocol["windows"]) == 2
    assert not protocol["forward_diagnostics"]["symbols"]
