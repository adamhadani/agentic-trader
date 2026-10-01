import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def _study(tmp_path, power, out):
    return _invoke("study", ENTRY, "--power", str(power), "--output", str(out), "--cache", str(tmp_path / "cache"))


def _power(tmp_path, out, *extra):
    return _invoke("power", PROTOCOL, "--output", str(out), "--cache", str(tmp_path / "cache"), *extra)


def _passed_power(tmp_path, **override) -> Path:
    entry = load_pooled_entry(Path(ENTRY)).entry
    power = tmp_path / "power"
    power.mkdir()
    document = {
        "status": "passed",
        "cohort_sha256": entry.cohort_sha256,
        "campaign_protocol_sha256": entry.campaign_protocol_sha256,
        **override,
    }
    (power / "result.json").write_text(json.dumps(document))
    return power


def _tamper_cohort(monkeypatch):
    real = alpha_cli.load_cohort

    def tampered(path):
        loaded = real(path)
        return type(loaded)(cohort=loaded.cohort, sha256="f" * 64, path=loaded.path)

    monkeypatch.setattr(alpha_cli, "load_cohort", tampered)


def test_study_refuses_an_existing_output(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    _forbid_clients(monkeypatch)
    result = _study(tmp_path, tmp_path, out)
    assert result.exit_code != 0 and "refusing to overwrite" in result.output
    assert list(out.iterdir()) == []


def test_study_refuses_without_a_passed_power_result_before_any_provider_access(tmp_path, monkeypatch):
    power = _passed_power(tmp_path, status="gate_failed")
    _forbid_clients(monkeypatch)
    result = _study(tmp_path, power, tmp_path / "out")
    assert result.exit_code != 0
    assert "power check A has not passed (status 'gate_failed')" in result.output
    assert not (tmp_path / "out").exists()


def test_study_refuses_a_missing_power_result(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    result = _study(tmp_path, tmp_path, tmp_path / "out")
    assert result.exit_code != 0 and "No power result" in result.output
    assert not (tmp_path / "out").exists()


def test_study_refuses_a_power_result_for_another_cohort(tmp_path, monkeypatch):
    power = _passed_power(tmp_path, cohort_sha256="0" * 64)
    _forbid_clients(monkeypatch)
    result = _study(tmp_path, power, tmp_path / "out")
    assert result.exit_code != 0 and "power check A was run on a different cohort" in result.output
    assert not (tmp_path / "out").exists()


def test_study_refuses_a_cohort_file_that_does_not_match_the_entry_pin(tmp_path, monkeypatch):
    power = _passed_power(tmp_path)
    _tamper_cohort(monkeypatch)
    _forbid_clients(monkeypatch)
    result = _study(tmp_path, power, tmp_path / "out")
    assert result.exit_code != 0 and "Cohort file does not match the entry's cohort_sha256" in result.output
    assert not (tmp_path / "out").exists()


def test_power_refuses_an_existing_output(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    _forbid_clients(monkeypatch)
    result = _power(tmp_path, out)
    assert result.exit_code != 0 and "refusing to overwrite" in result.output
    assert list(out.iterdir()) == []


@pytest.mark.parametrize(
    "args",
    [
        ("power", PROTOCOL, "--output", "OUT"),
        ("study", ENTRY, "--power", "POWER", "--output", "OUT"),
    ],
)
def test_both_commands_require_the_shared_cache(tmp_path, monkeypatch, args):
    (tmp_path / "power").mkdir()
    out = tmp_path / "out"
    _forbid_clients(monkeypatch)
    result = _invoke(*(str(out) if a == "OUT" else str(tmp_path / "power") if a == "POWER" else a for a in args))
    assert result.exit_code != 0 and "Missing option '--cache'" in result.output
    assert not out.exists()


def _dummy_clients():
    @contextmanager
    def clients():
        yield SimpleNamespace(bars="bars", calendar="calendar", static_symbols=["SPY"], pace="pace")

    return clients


def test_power_passes_workers_through_and_prints_a_summary(tmp_path, monkeypatch):
    seen = {}

    async def recorder(loaded, directory, **kwargs):
        seen.update(kwargs)
        return {"status": "passed", "curve": {}}

    monkeypatch.setattr(alpha_cli, "execute_power_check", recorder)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    result = _power(tmp_path, tmp_path / "new", "--workers", "4")
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
    assert _power(tmp_path, tmp_path / "a").exit_code == 0
    assert seen["workers"] == 1
    assert _power(tmp_path, tmp_path / "b", "--workers", "0").exit_code != 0


def test_power_exits_nonzero_when_not_passed(tmp_path, monkeypatch):
    async def recorder(loaded, directory, **kwargs):
        return {"status": "gate_failed", "curve": {}}

    monkeypatch.setattr(alpha_cli, "execute_power_check", recorder)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    result = _power(tmp_path, tmp_path / "new")
    assert result.exit_code != 0 and "Power check A gate_failed; see result.json" in result.output


def test_pooled_commands_refuse_a_cohort_file_that_does_not_match_the_pin(tmp_path, monkeypatch):
    _tamper_cohort(monkeypatch)
    _forbid_clients(monkeypatch)
    result = _power(tmp_path, tmp_path / "out")
    assert result.exit_code != 0 and "Cohort file does not match the protocol's cohort_sha256" in result.output
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("result", "exit_code", "message"),
    [
        ({"status": "failed", "error": "ValueError: coverage"}, 1, "Pooled study failed; see result.json"),
        # A completed study whose entry failed its pass rule is a successful run.
        ({"status": "completed", "decision": "failed"}, 0, '"decision": "failed"'),
        ({"status": "completed", "decision": "eligible_for_probe"}, 0, '"decision": "eligible_for_probe"'),
    ],
)
def test_study_exit_code_follows_the_run_status_not_the_decision(tmp_path, monkeypatch, result, exit_code, message):
    async def recorder(loaded, directory, **kwargs):
        return result

    monkeypatch.setattr(alpha_cli, "execute_pooled_study", recorder)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    outcome = _study(tmp_path, _passed_power(tmp_path), tmp_path / "out")
    assert outcome.exit_code == exit_code, outcome.output
    assert message in outcome.output


@pytest.mark.parametrize("command", ["power", "study"])
def test_both_commands_build_in_the_given_cache_and_report_progress_on_stderr(tmp_path, monkeypatch, command):
    seen = {}

    async def fake_build_cube_inputs(cohort, spec, **kwargs):
        seen.update(kwargs)
        kwargs["progress"]("daily bars 25/480 symbols")
        return "built"

    async def executor(loaded, directory, **kwargs):
        seen["built"] = await kwargs["build"]()
        return {"status": "passed" if command == "power" else "completed", "curve": {}}

    monkeypatch.setattr(alpha_cli, "build_cube_inputs", fake_build_cube_inputs)
    monkeypatch.setattr(alpha_cli, "execute_power_check", executor)
    monkeypatch.setattr(alpha_cli, "execute_pooled_study", executor)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", _dummy_clients())
    if command == "power":
        result = _power(tmp_path, tmp_path / "out")
    else:
        result = _study(tmp_path, _passed_power(tmp_path), tmp_path / "out")
    assert result.exit_code == 0, result.output
    assert seen["built"] == "built"
    assert seen["cache_dir"] == tmp_path / "cache"
    assert (seen["bars"], seen["calendar"], seen["static_symbols"], seen["pace"]) == (
        "bars",
        "calendar",
        ["SPY"],
        "pace",
    )
    assert "daily bars 25/480 symbols" in result.stderr
    assert "daily bars 25/480 symbols" not in result.stdout
