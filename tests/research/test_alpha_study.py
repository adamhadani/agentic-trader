"""Calibration studies freeze selection, retain failures and never grant deployment."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from arch.bootstrap import SPA
from scipy.stats import binomtest

from agentic_trader.research.alpha import study
from agentic_trader.research.alpha.study import (
    StudyPhase,
    StudyProtocol,
    endpoint_rows,
    evaluate_job,
    market_bars,
    panel_comparison,
    search_comparison,
    study_jobs,
    study_seed,
    summarize_study,
)
from agentic_trader.research.alpha.study_artifacts import execute_study


@pytest.fixture
def small_protocol():
    return StudyProtocol(
        seed=918273,
        development_search_replicates=1,
        development_panel_replicates=1,
        validation_null_search_replicates=1,
        validation_edge_search_replicates=1,
        validation_null_panel_replicates=1,
        validation_edge_panel_replicates=1,
        generated_candidates=1,
        bootstrap_samples=99,
        block_lengths=(5, 20),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"seed": True},
        {"bootstrap_samples": 0},
        {"unknown": 3},
        {"block_lengths": (20, 20)},
        {"block_lengths": (5, 10)},
        {"validation_null_search_replicates": 0},
    ],
)
def test_protocol_rejects_invalid_or_ambiguous_evidence_budgets(change):
    with pytest.raises(ValueError):
        StudyProtocol(**change)


def test_protocol_roundtrip_freezes_identity_and_independent_phase_streams(small_protocol):
    restored = StudyProtocol.model_validate_json(small_protocol.model_dump_json())
    assert restored == small_protocol and restored.identity == small_protocol.identity
    streams = {
        study_seed(small_protocol.seed, phase, "case", 0, role)
        for phase in StudyPhase
        for role in ("data", "search", "bootstrap")
    }
    assert len(streams) == 6
    assert len(list(study_jobs(restored))) > 0
    assert StudyProtocol.from_document(small_protocol.document()) == small_protocol
    changed = small_protocol.document()
    changed["contracts"]["test_level"] = 0.2
    with pytest.raises(ValueError, match="contracts"):
        StudyProtocol.from_document(changed)


def test_original_study_cannot_be_reinterpreted_with_changed_return_clock():
    original = Path(__file__).parents[2] / "config/research/a1b-v1.json"
    with pytest.raises(ValueError, match="contracts"):
        StudyProtocol.from_document(json.loads(original.read_text()))


def test_real_panel_job_supports_full_study_seed_entropy(small_protocol):
    job = next(j for j in study_jobs(small_protocol) if j.kind == "panel")
    record = evaluate_job(job, small_protocol)
    assert record["status"] == "completed"
    assert all(type(r["accepted"]) is bool for r in record["rows"])


def test_simultaneous_bounds_include_every_declared_primary_endpoint(small_protocol):
    records = []
    for job in study_jobs(small_protocol):
        rows = endpoint_rows(job, small_protocol)
        for row in rows:
            row["accepted"] = row["target"] != "null"
        records.append(
            {"job_id": job.identity, "protocol_id": small_protocol.identity, "status": "completed", "rows": rows}
        )
    summary = summarize_study(small_protocol, records)
    assert summary["primary_endpoints"] == 24
    assert summary["status"] == "criteria_not_met"  # One replicate cannot establish calibration.
    primary = [r for r in summary["summaries"] if r["criterion_passed"] is not None]
    assert all(r["confidence"] == pytest.approx(1 - 0.05 / 24) for r in primary)
    with pytest.raises(ValueError, match="Duplicate"):
        summarize_study(small_protocol, [*records, records[0]])


@pytest.mark.parametrize("scenario_index", [0, 1, 2, 3])
def test_synthetic_market_is_prefix_causal_with_consistent_ohlc(small_protocol, scenario_index):
    case = small_protocol.market_scenarios[scenario_index]
    a = market_bars(case, seed=101, observations=600)
    b = market_bars(case, seed=101, observations=800)
    pd.testing.assert_frame_equal(a, b.iloc[:600])
    assert a.attrs["feed"] == "synthetic"
    assert (a.high >= a[["open", "close"]].max(axis=1)).all()
    assert (a.low <= a[["open", "close"]].min(axis=1)).all()


def test_spa_adapter_uses_loss_orientation_and_conservative_pvalue(small_protocol):
    rng = np.random.default_rng(144)
    panel = pd.DataFrame(
        rng.normal(size=(100, 3)), columns=list("abc"), index=pd.date_range("2020-01-01", periods=100, tz="UTC")
    )
    panel.a += 0.3
    result = panel_comparison(panel, samples=99, block=20, seed=2)
    reference = SPA(
        np.zeros(len(panel)),
        -panel,
        block_size=20,
        reps=99,
        bootstrap="stationary",
        studentize=True,
        nested=False,
        seed=2,
    )
    reference.compute()
    assert result["spa_upper"] == pytest.approx(float(reference.pvalues["upper"]))
    assert 0 <= result["joint_max"] <= 1


def test_planted_effect_starts_only_after_the_observed_volume_pulse(small_protocol):
    case = small_protocol.market_scenarios[2]
    positive = market_bars(case, seed=711, observations=600)
    null = market_bars(case.model_copy(update={"effect": 0.0}), seed=711, observations=600)
    observed = np.log(positive.close / positive.open) - np.log(null.close / null.open)
    expected = (positive.volume > 1_000_000).shift(1, fill_value=False).astype(float) * case.effect
    np.testing.assert_allclose(observed, expected, atol=1e-14)


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_adaptive_search_cannot_read_future_holdout(small_protocol, method):
    case = small_protocol.market_scenarios[2]
    bars = market_bars(case, seed=90, observations=600)
    first = search_comparison(bars, small_protocol, search_seed=77, bootstrap_seed=88, method=method)
    changed = bars.copy()
    changed.iloc[480:, :4] *= 3
    second = search_comparison(changed, small_protocol, search_seed=77, bootstrap_seed=88, method=method)
    assert first["run"] == second["run"]
    assert first["winner"] == second["winner"]
    assert first["reserved_trials"] == 9
    assert first["authorizes_promotion"] is False


def test_failed_or_missing_replicates_cannot_look_like_low_false_positive_rates(small_protocol):
    summary = summarize_study(small_protocol, [])
    assert summary["status"] == "incomplete"
    assert summary["missing_jobs"] == len(list(study_jobs(small_protocol)))
    assert not summary["authorizes_promotion"]
    assert all(r["rate"] is None for r in summary["summaries"])


@pytest.mark.parametrize("stage", ["assessment", "bootstrap"])
def test_comparison_failure_retains_frozen_winner_and_completed_search(monkeypatch, small_protocol, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("injected comparison failure")

    if stage == "assessment":
        monkeypatch.setattr(study, "assess_statistical_evidence", fail)
    else:
        monkeypatch.setattr(study.StationaryBootstrap, "conf_int", fail)
    job = next(j for j in study_jobs(small_protocol) if j.scenario.name == "dense_edge" and j.method == "random")
    record = evaluate_job(job, small_protocol)
    detail = record["detail"]
    assert record["status"] == "failed"
    assert detail["error"] == "RuntimeError: injected comparison failure"
    assert detail["run"]["status"] == "completed"
    assert detail["run"]["trial_count"] == len(detail["run"]["trials"]) == small_protocol.trial_budget
    assert detail["winner"]
    assert all(row["accepted"] is None for row in record["rows"])
    assert not record["authorizes_promotion"]


def test_duplicate_endpoint_cannot_silently_collapse(small_protocol):
    job = next(study_jobs(small_protocol))
    rows = endpoint_rows(job, small_protocol)
    for row in rows:
        row["accepted"] = False
    record = {
        "job_id": job.identity,
        "protocol_id": small_protocol.identity,
        "status": "completed",
        "rows": [*rows, rows[0]],
    }
    with pytest.raises(ValueError, match="endpoint"):
        summarize_study(small_protocol, [record])


def test_artifact_manifest_precedes_computation_and_failure_is_retained(tmp_path, small_protocol):
    destination = tmp_path / "study"

    def fail(job):
        assert json.loads((destination / "manifest.json").read_text())["protocol_id"] == small_protocol.identity
        raise ValueError("injected interruption")

    summary = execute_study(small_protocol, destination, {"revision": "fixture"}, evaluator=fail)
    assert summary["status"] == "incomplete"
    assert summary["failed_jobs"] > 0
    assert json.loads((destination / "completion.json").read_text()) == summary
    assert list((destination / "replicates").glob("*.json"))
    with pytest.raises(FileExistsError):
        execute_study(small_protocol, destination, {}, evaluator=fail)


def test_exact_binomial_reference_for_zero_errors():
    # Primary simultaneous bounds must not turn zero observed errors into certainty.
    upper = binomtest(0, 128, alternative="less").proportion_ci(confidence_level=1 - 0.05 / 24).high
    assert upper == pytest.approx(1 - (0.05 / 24) ** (1 / 128))
