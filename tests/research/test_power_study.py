"""Diagnostic controls follow the same search, observations and frozen policy."""

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha import power_study
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.power_study import (
    FamilySnapshot,
    PowerProtocol,
    family_scenarios,
    power_jobs,
    power_seeds,
    select_power_candidates,
    summarize_power_study,
)
from agentic_trader.research.alpha.study import StudyPhase, market_bars


@pytest.fixture
def power_protocol():
    return PowerProtocol(
        seed=9357913579, observations=600, development_replicates=1, null_replicates=1, edge_replicates=1
    )


def test_protocol_declares_finite_paired_budget_and_freezes_contracts():
    protocol = PowerProtocol(seed=7)
    jobs = list(power_jobs(protocol))
    assert len(jobs) == 800
    assert protocol.trial_budget == 16
    assert len({power_seeds(job, protocol)["data"] for job in jobs}) == 264
    # Matched effects share innovations; 400 datasets, 264 innovation streams.
    assert protocol.document()["budget"]["datasets"] == 400
    assert protocol.document()["budget"]["expression_evaluations"] == 12800
    assert PowerProtocol.from_document(protocol.document()) == protocol
    document = protocol.document()
    document["contracts"]["policy"]["minimum_trades"] = 1
    with pytest.raises(ValueError, match="contracts"):
        PowerProtocol.from_document(document)


@pytest.mark.parametrize("profile", ["dense", "sparse"])
def test_null_and_planted_cases_share_innovations_without_phase_leakage(power_protocol, profile):
    jobs = [j for j in power_jobs(power_protocol) if j.scenario.name == profile and j.method == "random"]
    development = [j for j in jobs if j.phase == StudyPhase.DEVELOPMENT]
    seeds = [power_seeds(j, power_protocol) for j in development]
    assert seeds[0] == seeds[1]
    a, b = [market_bars(j.scenario, seed=s["data"]) for j, s in zip(development, seeds, strict=True)]
    difference = np.log(b.close / b.open) - np.log(a.close / a.open)
    expected = (a.volume > 1_000_000).shift(1, fill_value=False).astype(float) * development[1].effect
    np.testing.assert_allclose(difference, expected, atol=1e-14)
    assert seeds[0]["data"] != power_seeds(jobs[-1], power_protocol)["data"]
    assert len(set(seeds[0].values())) == len(seeds[0])


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_selection_and_known_control_are_frozen_before_any_holdout(power_protocol, method):
    job = next(j for j in power_jobs(power_protocol) if j.effect and j.method == method)
    bars = market_bars(job.scenario, seed=18)
    first = select_power_candidates(bars, power_protocol, search_seed=19, method=method)
    changed = bars.copy()
    changed.iloc[int(len(bars) * 0.8) :, :4] *= 3
    second = select_power_candidates(changed, power_protocol, search_seed=19, method=method)
    assert first == second
    assert first["run"]["trial_count"] == 16
    known = first["routes"]["known"]
    assert known["definition"]["expression"] == "volume"
    assert known["evidence"]["trial_count"] == 16
    assert first["selected_version"] == first["routes"]["winner"]["evidence"]["version_id"]


def test_count_and_variance_sensitivities_do_not_change_each_other(power_protocol):
    run = {"trial_count": 16, "trials": [{"candidate": {"metrics": {"per_bar_sharpe": x}}} for x in [0.1, 0.2, 0.3]]}
    families = family_scenarios(run, power_protocol, None)
    assert families["current"] is None
    assert families["local"]["trial_count"] == families["variance_only"]["trial_count"] == 16
    assert families["local"]["trial_variance"] == families["count_only"]["trial_variance"]
    assert families["historical"]["trial_count"] == families["count_only"]["trial_count"] == 7065
    assert families["historical"]["trial_variance"] == families["variance_only"]["trial_variance"]


def test_sourced_current_family_appends_this_search_once(power_protocol):
    snapshot = FamilySnapshot(
        captured_at="2026-09-18T10:00:00Z",
        source_revision="a" * 40,
        global_count=7826,
        sharpes=(0.1, 0.2),
        projection_events={"family/all": 10, "family/1d/complete_observed_bars_v1": 9},
    )
    protocol = power_protocol.model_copy(update={"family_snapshot_hash": snapshot.identity})
    run = {"trial_count": 16, "trials": [{"candidate": {"metrics": {"per_bar_sharpe": 0.3}}}]}
    current = family_scenarios(run, protocol, snapshot)["current"]
    assert current["trial_count"] == 7842
    assert current["trial_variance"] == pytest.approx(np.var([0.1, 0.2, 0.3], ddof=1))
    assert current["variance_observations"] == 3
    with pytest.raises(ValueError, match="snapshot"):
        family_scenarios(run, power_protocol, snapshot)


def test_missing_replicates_retain_full_denominators(power_protocol):
    result = summarize_power_study(power_protocol, [])
    assert result["status"] == "incomplete"
    assert result["primary_endpoints"] == 16
    assert result["missing_jobs"] == len(list(power_jobs(power_protocol)))
    assert all(row["rate"] is None for row in result["summaries"])
    assert result["authorizes_promotion"] is False


def test_partial_discovery_keeps_run_before_missing_control(monkeypatch, power_protocol):

    partial = {"status": "timed_out", "trial_count": 1, "trials": [{"definition": {"alpha_id": "other"}}]}

    class Miner:
        def __init__(self, **kwargs):
            self.last_run = partial

        def mine(self, *args, **kwargs):
            return []

    monkeypatch.setattr(power_study, "AlphaMiner", Miner)
    result = select_power_candidates(None, power_protocol, search_seed=1, method="random")
    assert result["run"] == partial
    assert result["routes"] == {"known": None, "winner": None}


def test_predictor_includes_first_execution_decision(monkeypatch):

    bars = pd.DataFrame({"open": [100.0] * 5, "close": [100.0, 100.0, 101.0, 100.0, 100.0]})
    scores = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(power_study, "alpha_scores", lambda *a: scores)
    definition = AlphaDefinition("pulse", "Pulse", "volume", entry_threshold=0.5)
    report = power_study.predictor_diagnostics(definition, bars, start=2)
    assert report["observations"] == 3
    assert report["decision_start"] == 1 and report["target_start"] == 2
    assert report["action_fraction"] == pytest.approx(1 / 3)
    assert report["mean_next_open_close_action_payoff"] == pytest.approx(0.01 / 3)


def test_confirmed_empty_daily_family_uses_new_samples_not_historical_variance(power_protocol):
    snapshot = FamilySnapshot(
        captured_at="2026-09-18T10:00:00Z",
        source_revision="a" * 40,
        global_count=7826,
        sharpes=(),
        projection_events={"family/all": 10},
    )
    protocol = power_protocol.model_copy(update={"family_snapshot_hash": snapshot.identity})
    run = {"trial_count": 16, "trials": [{"candidate": {"metrics": {"per_bar_sharpe": x}}} for x in [0.1, 0.3]]}
    families = family_scenarios(run, protocol, snapshot)
    assert families["current"]["trial_variance"] == families["local"]["trial_variance"]
    assert families["current"]["trial_count"] == 7842
    assert families["current"]["prior_variance_observations"] == 0
    assert families["current"]["variance_observations"] == 2


def test_same_known_and_selected_version_reuses_expensive_measurement(monkeypatch, power_protocol):
    job = next(j for j in power_jobs(power_protocol) if j.effect)
    select = power_study.select_power_candidates
    measure = power_study.measure_statistical_evidence
    measured = []

    def same_route(*args, **kwargs):
        selected = select(*args, **kwargs)
        selected["routes"]["winner"] = selected["routes"]["known"]
        return selected

    def counted(*args, **kwargs):
        measured.append(args[0].version_id)
        return measure(*args, **kwargs)

    monkeypatch.setattr(power_study, "select_power_candidates", same_route)
    monkeypatch.setattr(power_study, "measure_statistical_evidence", counted)
    record = power_study.evaluate_power_job(job, power_protocol, None, checkpoint=lambda selected: None)
    assert record["status"] == "completed"
    assert len(measured) == 1
    assert record["routes"]["known"] == record["routes"]["winner"]
    assert len(record["routes"]["known"]["assessments"]) == 5
