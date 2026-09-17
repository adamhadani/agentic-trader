"""Frozen forecast protocols bind the whole screened cohort before price access."""

import copy
import hashlib
import json
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_forecast as command
from agentic_trader.cli.main import cli
from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


@pytest.fixture
def forecast_protocol():
    manifest = json.dumps({"as_of": "2026-09-17T19:00:00Z", "plan_id": "b" * 64}).encode()
    inputs = json.dumps(
        {"receipts": [{"requested_at": "2026-09-17T19:00:01Z", "received_at": "2026-09-17T19:03:00Z"}]}
    ).encode()
    result = json.dumps(
        {
            "status": "completed",
            "selection_available": True,
            "shortfall": 0,
            "selected_count": 4,
            "snapshot_id": "a" * 64,
            "plan_id": "b" * 64,
            "manifest_hash": hashlib.sha256(manifest).hexdigest(),
            "inputs_hash": hashlib.sha256(inputs).hexdigest(),
            "selected": [{"asset_id": str(i), "symbol": s} for i, s in enumerate(("AAA", "BBB", "CCC", "DDD"))],
        }
    ).encode()
    plan = PanelForecastPlan(
        campaign_id="fixture",
        cohorts=(ForecastCohort("equities", ("AAA", "BBB", "CCC", "DDD"), 1, 4),),
        start=date(2021, 1, 1),
        end=date(2025, 12, 31),
        folds=(PanelFold("y2023", date(2023, 1, 1), date(2023, 12, 31)),),
        features=(PanelHypothesis("momentum", "roc(close,60)"), PanelHypothesis("reversal", "-roc(close,5)")),
        economic_features=("momentum", "reversal"),
        target=ForecastTarget("1d", 20, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 1.0, 5.0),
        ic=ICPolicy(min_assets=4, hac_lags=20, observations_per_year=252),
        selection=ForecastSelection(
            "equities", "a" * 64, "b" * 64, hashlib.sha256(result).hexdigest(), pd.Timestamp("2026-09-17T19:03:00Z")
        ),
    )
    return plan, result, manifest, inputs


def test_exact_roundtrip_and_source_binding(forecast_protocol):
    plan, result, manifest, inputs = forecast_protocol
    assert PanelForecastPlan.from_document(json.loads(json.dumps(plan.document()))) == plan
    plan.validate_selection(result, manifest, inputs)
    plan.validate_as_of(pd.Timestamp("2026-09-18T00:00:00Z"))
    assert plan.trial_count == 16
    assert plan.acquisition_symbols == ("AAA", "BBB", "CCC", "DDD")
    assert plan.adjustment == "all" and not plan.document()["authorizes_promotion"]


@pytest.mark.parametrize(
    "fault",
    [
        "result_hash",
        "manifest_hash",
        "inputs_hash",
        "receipt_time",
        "subset",
        "snapshot",
        "selection_failed",
        "backdated",
    ],
)
def test_selection_cannot_be_substituted_or_backdated(forecast_protocol, fault):
    plan, result, manifest, inputs = forecast_protocol
    if fault == "result_hash":
        result += b" "
    elif fault == "manifest_hash":
        manifest += b" "
    elif fault == "inputs_hash":
        inputs += b" "
    elif fault == "receipt_time":
        plan = replace(plan, selection=replace(plan.selection, observed_at=pd.Timestamp("2026-09-17T19:00:00Z")))
    elif fault == "subset":
        plan = replace(plan, cohorts=(ForecastCohort("equities", ("AAA", "BBB", "CCC"), 1, 3),))
    elif fault == "snapshot":
        plan = replace(plan, selection=replace(plan.selection, snapshot_id="d" * 64))
    elif fault == "selection_failed":
        doc = json.loads(result)
        doc["status"] = "failed"
        result = json.dumps(doc).encode()
        plan = replace(plan, selection=replace(plan.selection, result_sha256=hashlib.sha256(result).hexdigest()))
    else:
        with pytest.raises(ValueError):
            plan.validate_as_of(pd.Timestamp("2026-09-17T18:59:00Z"))
        return
    with pytest.raises(ValueError):
        plan.validate_selection(result, manifest, inputs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ridge_alpha", float("nan")),
        ("ridge_alpha", True),
        ("train_sessions", 10),
        ("min_train_rows", 0),
        ("history_sessions", 1),
        ("refit_sessions", 0),
        ("economic_features", ("missing",)),
        ("feed", "yfinance"),
        ("costs_bps", (1.0, 1.0)),
    ],
)
def test_bounded_explicit_policy(forecast_protocol, field, value):
    with pytest.raises(ValueError):
        replace(forecast_protocol[0], **{field: value})


@pytest.mark.parametrize("field,value", [("authorizes_promotion", True), ("charged_trials", 1), ("unknown", 1)])
def test_document_has_no_implicit_defaults(forecast_protocol, field, value):
    document = copy.deepcopy(forecast_protocol[0].document())
    document[field] = value
    with pytest.raises(ValueError):
        PanelForecastPlan.from_document(document)


@pytest.mark.parametrize("tampered", [False, True])
def test_cli_validates_dependency_before_composing_research(forecast_protocol, tmp_path, monkeypatch, tampered):
    plan, result, manifest, inputs = forecast_protocol
    selection = tmp_path / "selection"
    selection.mkdir()
    for name, data in (
        ("result.json", result + (b" " if tampered else b"")),
        ("manifest.json", manifest),
        ("inputs.json", inputs),
    ):
        (selection / name).write_bytes(data)
    protocol = tmp_path / "protocol.json"
    acquisition = DailyAcquisitionConfig()
    protocol.write_text(json.dumps({"plan": plan.document(), "acquisition": acquisition.model_dump(mode="json")}))
    output = tmp_path / "output"
    calls = []

    @asynccontextmanager
    async def repository():
        calls.append("repository")
        yield "repository"

    class Service:
        def __init__(self, repository, source, *, acquisition, compute):
            assert repository == "repository" and source == "source"
            assert acquisition == DailyAcquisitionConfig() and compute is command.compute_panel_forecast

        async def run(self, actual, directory, *, environment):
            assert actual == plan and directory == output and environment == {"fixture": True}
            directory.mkdir()
            result = {"status": "completed", "authorizes_promotion": False, "charged_trials": plan.trial_count}
            (directory / "result.json").write_text(json.dumps(result))
            calls.append("run")
            return result

    monkeypatch.setattr(command, "load_config", lambda: "config")
    monkeypatch.setattr(command, "alpha_repository", repository)
    monkeypatch.setattr(
        command,
        "session_source",
        lambda config, feed: nullcontext("source") if (config, feed) == ("config", "iex") else None,
    )
    monkeypatch.setattr(command, "research_environment", lambda: {"fixture": True})
    monkeypatch.setattr(command, "AlphaPanelService", Service)
    response = CliRunner().invoke(
        cli, ["alpha", "forecast-study", str(protocol), "--selection", str(selection), "--output", str(output)]
    )
    assert response.exit_code == (1 if tampered else 0), response.output
    assert calls == ([] if tampered else ["repository", "run"])
    assert output.exists() is not tampered
