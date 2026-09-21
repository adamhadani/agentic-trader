"""CLI surface for enrolling/renewing paper probes.

`@coro`-wrapped commands run their own `asyncio.run` event loop (see
`agentic_trader.cli.utils.coro`), so any aiosqlite connection created inside a
pytest-asyncio test loop must never be reused from that CLI-owned loop.
Following the idiom in tests/cli/test_cli_alpha.py, these tests stay plain
(non-async) functions: async setup work runs to completion (and disposes its
engine) via `asyncio.run(...)` before `CliRunner().invoke(...)` is called
directly, so `invoke` never nests inside an already-running loop. The patched
`alpha_repository` context manager builds a fresh `SignalDatabase` on the same
sqlite file inside the CLI's own loop and disposes it on exit.
"""

import asyncio
from contextlib import asynccontextmanager

from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.storage.alpha import AlphaRepository
from tests.research.probe_fixtures import criterion, make_definition, paper_database, qualification, seed


def patched_repository(monkeypatch, tmp_path):
    @asynccontextmanager
    async def manager():
        fresh = paper_database(tmp_path)
        try:
            yield AlphaRepository(fresh.workflows)
        finally:
            await fresh.engine.dispose()

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.alpha_repository", manager)


def prepared(tmp_path, decision=None):
    async def setup():
        database = paper_database(tmp_path)
        await database.init_db()
        repository = AlphaRepository(database.workflows)
        definition = make_definition()
        await seed(repository, definition, decision)
        await database.engine.dispose()
        return definition

    return asyncio.run(setup())


def test_default_test_scope_refuses_probe_enrolment():
    result = CliRunner().invoke(cli, ["alpha", "probe", "0" * 64, "--generation", "0"])
    assert result.exit_code != 0
    assert "Unknown alpha version" in result.output or "Alpaca paper" in result.output


def test_probe_command_enrols_and_lists(tmp_path, monkeypatch):
    definition = prepared(tmp_path)
    patched_repository(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["alpha", "probe", definition.version_id, "--generation", "0", "--days", "30"])
    assert result.exit_code == 0, result.output
    assert "paper probe" in result.output.lower() and "generation 1" in result.output
    listing = runner.invoke(cli, ["alpha", "list"])
    assert f"{definition.version_id} | probe" in listing.output


def test_probe_command_reports_each_unmet_criterion(tmp_path, monkeypatch):
    definition = prepared(tmp_path, qualification(cost_stress=criterion(-1.0, "fail")))
    patched_repository(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli, ["alpha", "probe", definition.version_id, "--generation", "0"])
    assert result.exit_code != 0
    assert "cost_stress_below_probe_floor" in result.output
