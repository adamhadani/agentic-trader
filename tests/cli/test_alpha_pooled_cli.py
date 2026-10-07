import json
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha as alpha_cli
from agentic_trader.research.pooled.campaign import load_campaign_protocol
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


SCREEN_RULE = str(REPO_ROOT / "config/research/pooled/screen-v2.json")


def test_screen_refuses_an_existing_output_without_creating_clients(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    result = _invoke(
        "screen", SCREEN_RULE, "--snapshot", str(snapshot), "--output", str(out), "--cache", str(tmp_path / "cache")
    )
    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output


def test_screen_reads_raw_daily_bars_through_the_batched_provider(tmp_path, monkeypatch):
    seen = {}

    class Bars:
        def fetch_daily_many(self, symbols, start, end, *, adjustment):
            seen["adjustment"] = adjustment
            return {}

    @contextmanager
    def clients():
        yield SimpleNamespace(bars=Bars(), calendar=object(), pace=None)

    async def fake_execute(loaded, snapshot_path, output, **kwargs):
        kwargs["fetch"](["AAA"], None, None)
        seen.update(rule_path=kwargs["rule_path"], configured=len(kwargs["config_symbols"]))
        return {"status": "completed"}

    monkeypatch.setattr(alpha_cli, "_apriori_clients", clients)
    monkeypatch.setattr(alpha_cli, "execute_screen", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", dict)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    result = _invoke(
        "screen",
        SCREEN_RULE,
        "--snapshot",
        str(snapshot),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "cache"),
    )
    assert result.exit_code == 0, result.output
    assert seen["adjustment"] == "raw"
    assert seen["rule_path"] == "config/research/pooled/screen-v2.json"
    assert seen["configured"] > 0


PROTOCOL_V2 = str(REPO_ROOT / "config/research/pooled/campaign-v2.json")
PROTOCOL_V3 = str(REPO_ROOT / "config/research/pooled/campaign-v3.json")
PROTOCOL_V4 = str(REPO_ROOT / "config/research/pooled/campaign-v4.json")  # fixed_set: checks A and C only


def _gates(tmp_path, revision="abc1234", *, protocol=PROTOCOL_V2, **revisions) -> list[Path]:
    """Passed power, search-power and null-check directories for ``protocol``, in that order."""
    loaded = load_campaign_protocol(Path(protocol))
    dirs = []
    for name, check in (("power", "power_a"), ("search", "search_power"), ("null", "null_check")):
        directory = tmp_path / name
        directory.mkdir()
        manifest = {"check": check, "environment": {"runtime": {"revision": revisions.get(name, revision)}}}
        result = {
            "status": "passed",
            "cohort_sha256": loaded.protocol.cohort_sha256,
            "campaign_protocol_sha256": loaded.sha256,
            "cube_sha256": "k" * 64,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "result.json").write_text(json.dumps(result))
        dirs.append(directory)
    return dirs


SCOPE = "production/alpaca:paper"


def _campaign(tmp_path, dirs, *extra, scope=SCOPE, protocol=PROTOCOL_V2, search_power=True):
    power, search, null = dirs
    return _invoke(
        "campaign",
        protocol,
        "--power",
        str(power),
        *(("--search-power", str(search)) if search_power else ()),
        "--null-check",
        str(null),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "cache"),
        *(() if scope is None else ("--journal-scope", scope)),
        *extra,
    )


def _forbid_repository(monkeypatch):
    def boom():
        raise AssertionError("the ledger must not be opened")

    monkeypatch.setattr(alpha_cli, "alpha_repository", boom)


def _clean_tree(monkeypatch, changes=""):
    monkeypatch.setattr(alpha_cli, "_uncommitted_research_files", lambda: changes)


def _journal(monkeypatch, *, scope=SCOPE, dialect="postgresql"):
    """A fake journal: its scope and dialect are what the campaign command checks."""
    engine = SimpleNamespace(dialect=SimpleNamespace(name=dialect), url=SimpleNamespace(database="trader"))
    fake = SimpleNamespace(store=SimpleNamespace(scope=scope, db=SimpleNamespace(engine=engine)))

    @asynccontextmanager
    async def repository():
        yield fake

    monkeypatch.setattr(alpha_cli, "alpha_repository", repository)
    return fake


def _campaign_clients(monkeypatch):
    @contextmanager
    def clients():
        yield SimpleNamespace(bars=None, calendar=None, static_symbols=[], pace=None)

    monkeypatch.setattr(alpha_cli, "_apriori_clients", clients)


def test_search_power_refuses_a_failed_power_check_without_creating_clients(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    power = tmp_path / "power"
    power.mkdir()
    (power / "result.json").write_text(json.dumps({"status": "gate_failed"}))
    result = _invoke(
        "search-power",
        PROTOCOL_V2,
        "--power",
        str(power),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "c"),
    )
    assert result.exit_code != 0 and "has not passed" in result.output


def test_null_check_passes_the_worker_count_through(tmp_path, monkeypatch):
    seen = {}
    power, _, _ = _gates(tmp_path)

    @contextmanager
    def clients():
        yield SimpleNamespace(bars=None, calendar=None, static_symbols=[], pace=None)

    async def fake_execute(loaded, output, **kwargs):
        seen.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(alpha_cli, "_apriori_clients", clients)
    monkeypatch.setattr(alpha_cli, "execute_null_check", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", dict)
    result = _invoke(
        "null-check",
        PROTOCOL_V2,
        "--power",
        str(power),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "c"),
        "--workers",
        "4",
    )
    assert result.exit_code == 0, result.output
    assert seen["workers"] == 4 and seen["power_result"]["status"] == "passed"


def test_campaign_refuses_a_dirty_revision_before_opening_the_ledger(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234-dirty"}})
    result = _campaign(tmp_path, _gates(tmp_path))
    assert result.exit_code != 0 and "dirty" in result.output


def test_campaign_refuses_a_gate_from_another_revision(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    _clean_tree(monkeypatch)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path, null="def5678"))
    assert result.exit_code != 0 and "null_check ran at code revision 'def5678'" in result.output


def test_campaign_hands_the_checked_gates_and_the_literature_entries_to_the_executor(tmp_path, monkeypatch):
    seen = {}
    journal = _journal(monkeypatch)
    _campaign_clients(monkeypatch)
    _clean_tree(monkeypatch)

    async def fake_execute(loaded, output, **kwargs):
        seen.update(kwargs)
        return {"status": "none_confirmed"}

    monkeypatch.setattr(alpha_cli, "execute_campaign", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path))
    assert result.exit_code == 0, result.output
    assert set(seen["gates"]) == {"power", "search_power", "null_check"}
    assert seen["repository"] is journal
    assert [entry.entry.id for entry in seen["entries"]] == ["high52", "reversal-lowmax"]


def _recording_campaign(monkeypatch) -> dict:
    seen = {}

    async def fake_execute(loaded, output, **kwargs):
        seen.update(kwargs, protocol_sha256=loaded.sha256)
        return {"status": "none_confirmed"}

    monkeypatch.setattr(alpha_cli, "execute_campaign", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    return seen


def _forbid_campaign(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("the campaign must not run")

    monkeypatch.setattr(alpha_cli, "execute_campaign", forbidden)
    monkeypatch.setattr(alpha_cli, "execute_campaign_recovery", forbidden)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})


def test_campaign_with_a_fixed_set_protocol_takes_two_gates(tmp_path, monkeypatch):
    journal = _journal(monkeypatch)
    _campaign_clients(monkeypatch)
    _clean_tree(monkeypatch)
    seen = _recording_campaign(monkeypatch)
    dirs = _gates(tmp_path, protocol=PROTOCOL_V4)
    result = _campaign(tmp_path, dirs, protocol=PROTOCOL_V4, search_power=False)
    assert result.exit_code == 0, result.output
    assert set(seen["gates"]) == {"power", "null_check"}
    assert seen["protocol_sha256"] == load_campaign_protocol(Path(PROTOCOL_V4)).sha256
    assert seen["repository"] is journal
    # Each gate is checked against its own manifest: a swapped directory is refused.
    (tmp_path / "again").mkdir()
    power, search, null = _gates(tmp_path / "again", protocol=PROTOCOL_V4)
    swapped = _campaign(tmp_path, (null, search, power), protocol=PROTOCOL_V4, search_power=False)
    assert swapped.exit_code != 0 and "holds a 'null_check' result, not 'power_a'" in swapped.output


def test_campaign_refuses_search_power_for_a_fixed_set_protocol(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    _forbid_campaign(monkeypatch)
    _clean_tree(monkeypatch)
    result = _campaign(tmp_path, _gates(tmp_path, protocol=PROTOCOL_V4), protocol=PROTOCOL_V4)
    assert result.exit_code == 1
    assert "fixed_set protocols take no search-power check (check B does not apply)" in result.output
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("protocol", [PROTOCOL_V2, PROTOCOL_V3])
def test_campaign_requires_search_power_for_a_genetic_protocol(tmp_path, monkeypatch, protocol):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    _forbid_campaign(monkeypatch)
    _clean_tree(monkeypatch)
    result = _campaign(tmp_path, _gates(tmp_path, protocol=protocol), protocol=protocol, search_power=False)
    assert result.exit_code == 1
    assert "genetic protocols require --search-power (check B)" in result.output
    assert not (tmp_path / "out").exists()


def test_search_power_refuses_a_fixed_set_protocol_before_any_cube_read(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)

    async def forbidden(*args, **kwargs):
        raise AssertionError("no cube may be read or built")

    monkeypatch.setattr(alpha_cli, "build_cube_inputs", forbidden)
    monkeypatch.setattr(alpha_cli, "execute_search_power", forbidden)
    power, _, _ = _gates(tmp_path, protocol=PROTOCOL_V4)
    result = _invoke(
        "search-power",
        PROTOCOL_V4,
        "--power",
        str(power),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "c"),
    )
    assert result.exit_code == 1
    assert "check B does not apply to a fixed_set protocol" in result.output
    assert not (tmp_path / "out").exists()


def test_campaign_requires_the_journal_scope(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    result = _campaign(tmp_path, _gates(tmp_path), scope=None)
    assert result.exit_code != 0 and "Missing option '--journal-scope'" in result.output


@pytest.mark.parametrize(
    ("scope", "dialect", "message"),
    [
        ("development/paper", "postgresql", "does not match this journal's scope 'production/alpaca:paper'"),
        (SCOPE, "sqlite", "only on the PostgreSQL journal"),
    ],
)
def test_campaign_refuses_another_journal_before_any_reservation(tmp_path, monkeypatch, scope, dialect, message):
    _journal(monkeypatch, scope=SCOPE, dialect=dialect)
    _forbid_clients(monkeypatch)
    _clean_tree(monkeypatch)

    async def forbidden(*args, **kwargs):
        raise AssertionError("nothing may be reserved")

    monkeypatch.setattr(alpha_cli, "execute_campaign", forbidden)
    monkeypatch.setattr(alpha_cli, "execute_campaign_recovery", forbidden)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path), scope=scope)
    assert result.exit_code != 0 and message in result.output
    assert not (tmp_path / "out").exists()


def test_campaign_refuses_uncommitted_or_untracked_files_before_opening_the_ledger(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    _clean_tree(monkeypatch, "?? agentic_trader/research/pooled/new_module.py")
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path))
    assert result.exit_code != 0
    assert "uncommitted or untracked" in result.output and "new_module.py" in result.output


def test_the_tree_check_asks_git_for_changes_and_untracked_files_under_code_config_and_tests(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen["args"] = args
        return SimpleNamespace(stdout=" M config/research/pooled/campaign-v2.json\n")

    monkeypatch.setattr(alpha_cli.subprocess, "run", run)
    assert alpha_cli._uncommitted_research_files() == "M config/research/pooled/campaign-v2.json"
    assert seen["args"] == [
        "git",
        "-C",
        str(REPO_ROOT),
        "status",
        "--porcelain",
        "--",
        "agentic_trader",
        "config",
        "tests",
    ]


def test_campaign_recover_runs_the_recovery_after_the_same_checks(tmp_path, monkeypatch):
    seen = {}
    journal = _journal(monkeypatch)
    _campaign_clients(monkeypatch)
    _clean_tree(monkeypatch)

    async def forbidden(*args, **kwargs):
        raise AssertionError("--recover never runs the campaign")

    async def fake_recover(loaded, output, **kwargs):
        seen.update(kwargs)
        return {"status": "confirmed", "recovered": True, "confirmed": ["f1"]}

    monkeypatch.setattr(alpha_cli, "execute_campaign", forbidden)
    monkeypatch.setattr(alpha_cli, "execute_campaign_recovery", fake_recover)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path), "--recover")
    assert result.exit_code == 0, result.output
    assert seen["repository"] is journal and '"recovered": true' in result.output
    (tmp_path / "again").mkdir()
    refused = _campaign(tmp_path, _gates(tmp_path / "again", null="def5678"), "--recover")
    assert refused.exit_code != 0 and "null_check ran at code revision 'def5678'" in refused.output


@pytest.mark.parametrize("command", ["search-power", "null-check", "campaign"])
def test_later_checks_and_the_campaign_require_the_cached_cube(tmp_path, monkeypatch, command):
    seen = {}

    async def fake_build_cube_inputs(cohort, spec, **kwargs):
        seen["require_cached"] = kwargs.get("require_cached")
        return "built"

    async def executor(loaded, directory, **kwargs):
        seen["built"] = await kwargs["build"]()
        return {"status": "passed"}

    monkeypatch.setattr(alpha_cli, "build_cube_inputs", fake_build_cube_inputs)
    monkeypatch.setattr(alpha_cli, "execute_search_power", executor)
    monkeypatch.setattr(alpha_cli, "execute_null_check", executor)
    monkeypatch.setattr(alpha_cli, "execute_campaign", executor)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    _campaign_clients(monkeypatch)
    _journal(monkeypatch)
    _clean_tree(monkeypatch)
    dirs = _gates(tmp_path)
    if command == "campaign":
        result = _campaign(tmp_path, dirs)
    else:
        result = _invoke(
            command, PROTOCOL_V2, "--power", str(dirs[0]), "--output", str(tmp_path / "out"), "--cache", str(tmp_path)
        )
    assert result.exit_code == 0, result.output
    assert seen == {"require_cached": True, "built": "built"}
