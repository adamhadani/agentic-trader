"""The shared artifact lifecycle preserves selection before diagnostic holdouts."""

import json

import pytest

from agentic_trader.research.alpha import power_artifacts, power_study, promotion
from agentic_trader.research.alpha.power_study import PowerProtocol


@pytest.fixture
def power_plan():
    return PowerProtocol(seed=73591, observations=600, development_replicates=1, null_replicates=1, edge_replicates=1)


def test_manifest_and_selection_checkpoint_precede_holdout_and_keep_failure(tmp_path, monkeypatch, power_plan):
    output = tmp_path / "power"
    seen = []

    def evaluation(job, protocol, snapshot, *, checkpoint):
        assert (output / "manifest.json").exists()
        checkpoint({"selected_version": "frozen-winner", "run": {"trial_count": 16}, "authorizes_promotion": False})
        assert (
            json.loads((output / "selection" / f"{job.identity}.json").read_text())["selected_version"]
            == "frozen-winner"
        )
        seen.append(job)
        raise RuntimeError("after frozen selection")

    monkeypatch.setattr(power_artifacts, "evaluate_power_job", evaluation)
    result = power_artifacts.execute_power_study(power_plan, output, {}, None)
    assert result["status"] == "incomplete"
    assert seen and all(j.phase == "development" for j in seen)
    assert len(list((output / "selection").glob("*.json"))) == len(seen)
    assert result["missing_jobs"] > 0 and result["failed_jobs"] == len(seen)
    assert (output / "protocol.json").exists() and (output / "completion.json").exists()
    with pytest.raises(FileExistsError):
        power_artifacts.execute_power_study(power_plan, output, {}, None)


def test_snapshot_mismatch_rejected_before_creating_output(tmp_path, power_plan):
    protocol = power_plan.model_copy(update={"family_snapshot_hash": "f" * 64})
    with pytest.raises(ValueError, match="snapshot"):
        power_artifacts.execute_power_study(protocol, tmp_path / "bad", {}, None)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("failure", ["bootstrap", "trace"])
def test_development_calculation_failure_preserves_evidence_and_leaves_validation_unseen(
    tmp_path, monkeypatch, power_plan, failure
):

    def broken(*args, **kwargs):
        raise ValueError("injected mechanical failure")

    if failure == "bootstrap":
        monkeypatch.setattr(promotion, "block_bootstrap_mean", broken)
    else:
        monkeypatch.setattr(power_study, "execution_diagnostics", broken)
    output = tmp_path / failure
    result = power_artifacts.execute_power_study(power_plan, output, {}, None)
    records = [json.loads(path.read_text()) for path in (output / "replicates").glob("*.json")]
    assert result["failed_jobs"] == len(records) == 8
    assert result["missing_jobs"] == 8
    assert all(record["job_key"].startswith("development/") for record in records)
    for record in records:
        assert (output / "selection" / f"{record['job_id']}.json").exists()
        assert record["routes"]["known"]["measurement_hash"]
        assert record["routes"]["known"]["measurement"]["holdout_measurement"]
        assert record["routes"]["known"]["status"] == "failed"
