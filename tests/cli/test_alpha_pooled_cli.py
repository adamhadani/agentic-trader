import json
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha as alpha_cli
from agentic_trader.research.pooled.entry import REPO_ROOT, load_pooled_entry


ENTRY = str(REPO_ROOT / "config/research/pooled/high52-v1.json")
PROTOCOL = str(REPO_ROOT / "config/research/pooled/campaign-v1.json")


def _forbid_clients(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("provider clients must not be created")

    monkeypatch.setattr(alpha_cli, "_apriori_clients", boom)


def _invoke(*args):
    return CliRunner().invoke(alpha_cli.alpha_group, ["pooled", *args])


def test_study_refuses_an_existing_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = _invoke("study", ENTRY, "--power", str(tmp_path), "--output", str(out))
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def test_study_refuses_without_a_passed_power_result_before_any_provider_access(tmp_path, monkeypatch):
    power = tmp_path / "power"
    power.mkdir()
    (power / "result.json").write_text(json.dumps({"status": "gate_failed"}))
    _forbid_clients(monkeypatch)
    result = _invoke("study", ENTRY, "--power", str(power), "--output", str(tmp_path / "out"))
    assert result.exit_code != 0 and "power" in result.output
    assert not (tmp_path / "out").exists()


def test_study_refuses_a_missing_power_result(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    result = _invoke("study", ENTRY, "--power", str(tmp_path), "--output", str(tmp_path / "out"))
    assert result.exit_code != 0 and "No power result" in result.output
    assert not (tmp_path / "out").exists()


def test_study_refuses_a_power_result_for_another_cohort(tmp_path, monkeypatch):
    entry = load_pooled_entry(Path(ENTRY)).entry
    power = tmp_path / "power"
    power.mkdir()
    (power / "result.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "cohort_sha256": "0" * 64,
                "campaign_protocol_sha256": entry.campaign_protocol_sha256,
            }
        )
    )
    _forbid_clients(monkeypatch)
    result = _invoke("study", ENTRY, "--power", str(power), "--output", str(tmp_path / "out"))
    assert result.exit_code != 0 and "different cohort" in result.output
    assert not (tmp_path / "out").exists()


def test_power_refuses_an_existing_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = _invoke("power", PROTOCOL, "--output", str(out))
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def _dummy_clients():
    @contextmanager
    def clients():
        yield object()

    return clients


def test_power_passes_workers_through_and_prints_a_summary(tmp_path, monkeypatch):
    seen = {}

    async def recorder(loaded, directory, **kwargs):
        seen.update(kwargs)
        return {"status": "passed", "curve": {}}

    monkeypatch.setattr(alpha_cli, "execute_power_check", recorder)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    result = _invoke("power", PROTOCOL, "--output", str(tmp_path / "new"), "--workers", "4")
    assert result.exit_code == 0, result.output
    assert seen["workers"] == 4
    assert '"status": "passed"' in result.output


def test_power_defaults_to_one_worker_and_rejects_zero(tmp_path, monkeypatch):
    seen = {}

    async def recorder(loaded, directory, **kwargs):
        seen.update(kwargs)
        return {"status": "passed", "curve": {}}

    monkeypatch.setattr(alpha_cli, "execute_power_check", recorder)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    assert _invoke("power", PROTOCOL, "--output", str(tmp_path / "a")).exit_code == 0
    assert seen["workers"] == 1
    assert _invoke("power", PROTOCOL, "--output", str(tmp_path / "b"), "--workers", "0").exit_code != 0


def test_power_exits_nonzero_when_not_passed(tmp_path, monkeypatch):
    async def recorder(loaded, directory, **kwargs):
        return {"status": "gate_failed", "curve": {}}

    monkeypatch.setattr(alpha_cli, "execute_power_check", recorder)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    result = _invoke("power", PROTOCOL, "--output", str(tmp_path / "new"))
    assert result.exit_code != 0 and "gate_failed" in result.output


def test_pooled_commands_refuse_a_cohort_file_that_does_not_match_the_pin(tmp_path, monkeypatch):
    real = alpha_cli.load_cohort

    def tampered(path):
        loaded = real(path)
        return type(loaded)(cohort=loaded.cohort, sha256="f" * 64, path=loaded.path)

    monkeypatch.setattr(alpha_cli, "load_cohort", tampered)
    _forbid_clients(monkeypatch)
    result = _invoke("power", PROTOCOL, "--output", str(tmp_path / "out"))
    assert result.exit_code != 0 and "cohort_sha256" in result.output
    assert not (tmp_path / "out").exists()
