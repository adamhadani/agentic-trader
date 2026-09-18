"""CLI composition never opens a provider or reads retained prices before charging."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_controls as command
from agentic_trader.cli.main import cli
from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.research.alpha.forecast_controls_plan import ForecastControlsPlan
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan


@pytest.mark.parametrize("invalid", [None, "promotion", "cost_order"])
def test_retained_cli_composition(tmp_path, monkeypatch, invalid):

    parent = PanelForecastPlan.from_document(
        json.loads((Path(__file__).parents[2] / "config/research/screened-equity-forecast-iex-v1.json").read_text())[
            "plan"
        ]
    )
    plan = ForecastControlsPlan(parent, "equities", "a" * 64)
    acquisition = DailyAcquisitionConfig(min_request_interval_seconds=0)
    doc = {"plan": plan.document(), "acquisition": acquisition.model_dump(mode="json")}
    if invalid == "promotion":
        doc["plan"]["authorizes_promotion"] = True
    elif invalid == "cost_order":
        doc["plan"]["costs_bps"] = [5.0, 1.0]
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps(doc))
    retained = tmp_path / "retained"
    retained.mkdir()  # Empty: CLI composition must not read it ahead of reservation.
    output = tmp_path / "result"
    calls = []

    @asynccontextmanager
    async def repository():
        calls.append("repository")
        yield "repository"

    class Service:
        def __init__(self, repo, source, *, acquisition, compute):
            assert repo == "repository" and isinstance(source, command.RetainedPanelSource)
            assert source.directory == retained and source.plan == plan
            assert acquisition.min_request_interval_seconds == 0 and callable(compute)

        async def run(self, actual, directory, *, environment):
            assert actual == plan and directory == output
            assert environment["input_origin"]["kind"] == "retained_artifacts"
            directory.mkdir()
            result = {"status": "completed", "authorizes_promotion": False, "charged_trials": 90}
            (directory / "result.json").write_text(json.dumps(result))
            calls.append("run")
            return result

    monkeypatch.setattr(command, "alpha_repository", repository)
    monkeypatch.setattr(command, "research_environment", lambda: {"fixture": True})
    monkeypatch.setattr(command, "AlphaPanelService", Service)
    response = CliRunner().invoke(
        cli, ["alpha", "forecast-controls", str(protocol), "--parent", str(retained), "--output", str(output)]
    )
    assert response.exit_code == int(bool(invalid)), response.output
    assert calls == ([] if invalid else ["repository", "run"])
