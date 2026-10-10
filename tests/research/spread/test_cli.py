# tests/research/spread/test_cli.py
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_spread
from agentic_trader.cli.main import cli
from agentic_trader.research.spread.panel import PanelBuild, synthetic_panel
from tests.research.spread.test_schedule import weekdays
from tests.research.spread.world import ENVIRONMENT, gate_dir, write_world


SESSIONS = weekdays(date(2016, 1, 4), date(2019, 12, 31))


@pytest.fixture
def world(tmp_path):
    return write_world(tmp_path / "world")


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(alpha_spread, "research_environment", lambda: ENVIRONMENT)
    monkeypatch.setattr(alpha_spread, "_uncommitted_research_files", lambda: "")


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.store = SimpleNamespace(
            scope="test/paper",
            db=SimpleNamespace(
                engine=SimpleNamespace(
                    dialect=SimpleNamespace(name="postgresql"), url=SimpleNamespace(database="test_x")
                )
            ),
        )

    async def consume_lane_confirmation(self, lane, **kwargs):
        self.calls.append((lane, kwargs))
        return {
            "start": kwargs["interval"][0].isoformat(),
            "end": kwargs["interval"][1].isoformat(),
            "consumed_at": "t",
        }


@pytest.fixture
def fakes(monkeypatch, world):
    loaded, cohort = world
    panel, _ = synthetic_panel(cohort.cohort, SESSIONS, loaded.protocol.power, seed=5)
    requests = []

    async def fake_build_spread_panel(symbols, **kwargs):
        requests.append((tuple(symbols), kwargs["start"], kwargs["through"], kwargs["adjustment"]))
        return PanelBuild(panel=panel, bar_failures={})

    @contextmanager
    def fake_clients():
        yield SimpleNamespace(bars=object(), calendar=object(), pace=None)

    repository = FakeRepository()

    @asynccontextmanager
    async def fake_repository():
        yield repository

    monkeypatch.setattr(alpha_spread, "build_spread_panel", fake_build_spread_panel)
    monkeypatch.setattr(alpha_spread, "_apriori_clients", fake_clients)
    monkeypatch.setattr(alpha_spread, "alpha_repository", fake_repository)
    return SimpleNamespace(requests=requests, repository=repository)


def test_spread_power_runs_and_refuses_an_existing_output(world, tmp_path, clean):
    loaded, _ = world
    out = tmp_path / "power"
    result = CliRunner().invoke(cli, ["alpha", "spread-power", str(loaded.path), "--output", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(next(line for line in result.output.splitlines() if line.startswith("{")))
    assert payload["status"] == "passed" and (out / "result.json").exists()
    again = CliRunner().invoke(cli, ["alpha", "spread-power", str(loaded.path), "--output", str(out)])
    assert again.exit_code != 0 and "already exists" in again.output


def test_spread_null_requests_the_full_bar_range_and_writes_a_result(world, tmp_path, clean, fakes):
    loaded, cohort = world
    out = tmp_path / "null"
    result = CliRunner().invoke(
        cli, ["alpha", "spread-null", str(loaded.path), "--output", str(out), "--cache", str(tmp_path / "cache")]
    )
    assert result.exit_code == 0, result.output
    assert fakes.requests == [((*cohort.cohort.symbols, "SPY"), date(2016, 1, 4), date(2019, 12, 31), "all")]
    assert json.loads((out / "result.json").read_text())["check"] == "null_c"


def test_spread_study_journals_and_completes(world, tmp_path, clean, fakes):
    loaded, cohort = world
    power = gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort)
    null = gate_dir(tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort)
    out = tmp_path / "study"
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "spread-study",
            str(loaded.path),
            "--power",
            str(power),
            "--null-check",
            str(null),
            "--output",
            str(out),
            "--cache",
            str(tmp_path / "cache"),
            "--journal-scope",
            "test/paper",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads((out / "result.json").read_text())
    assert payload["status"] == "completed" and payload["decision"] in ("confirmed", "failed_confirmation")
    assert fakes.repository.calls and fakes.repository.calls[0][0] == "spread"
    assert fakes.repository.calls[0][1]["protocol_sha256"] == loaded.sha256
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["journal"] == {"scope": "test/paper", "dialect": "postgresql", "database": "test_x"}


def test_spread_study_refuses_a_journal_scope_mismatch_before_building(world, tmp_path, clean, fakes):
    loaded, cohort = world
    power = gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort)
    null = gate_dir(tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort)
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "spread-study",
            str(loaded.path),
            "--power",
            str(power),
            "--null-check",
            str(null),
            "--output",
            str(tmp_path / "s"),
            "--cache",
            str(tmp_path / "cache"),
            "--journal-scope",
            "production/alpaca:paper",
        ],
    )
    assert (
        result.exit_code != 0
        and "does not match" in result.output
        and fakes.requests == []
        and not (tmp_path / "s").exists()
    )


def test_spread_study_refuses_uncommitted_files_and_dirty_revisions(world, tmp_path, monkeypatch, fakes):
    loaded, cohort = world
    power = gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort)
    null = gate_dir(tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort)
    args = [
        "alpha",
        "spread-study",
        str(loaded.path),
        "--power",
        str(power),
        "--null-check",
        str(null),
        "--output",
        str(tmp_path / "s"),
        "--cache",
        str(tmp_path / "cache"),
        "--journal-scope",
        "test/paper",
    ]
    monkeypatch.setattr(alpha_spread, "research_environment", lambda: ENVIRONMENT)
    monkeypatch.setattr(alpha_spread, "_uncommitted_research_files", lambda: " M agentic_trader/x.py")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0 and "uncommitted" in result.output
    monkeypatch.setattr(alpha_spread, "_uncommitted_research_files", lambda: "")
    monkeypatch.setattr(alpha_spread, "research_environment", lambda: {"runtime": {"revision": "abc1234-dirty"}})
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0 and "dirty" in result.output and fakes.requests == []
