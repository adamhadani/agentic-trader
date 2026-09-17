"""Paginated SDK observations and durable accounting for current-cohort forecasts."""

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.config import DailyAcquisitionConfig, MarketDataEvidenceConfig
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_forecast import compute_panel_forecast
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def forecast_repository(request, temp_db):
    db = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await db.init_db()
    try:
        yield AlphaRepository(db.workflows)
    finally:
        await db.engine.dispose()


@pytest.fixture
def forecast_sdk_case():
    clock = pd.date_range("2024-01-02", periods=120, freq="B", tz="America/New_York")
    symbols = ("AAA", "BBB", "CCC", "DDD")
    plan = PanelForecastPlan(
        campaign_id="sdk_fixture",
        cohorts=(ForecastCohort("equities", symbols, top_k=1, min_assets=4),),
        start=clock[0].date(),
        end=clock[-1].date(),
        folds=(
            PanelFold("first", clock[50].date(), clock[84].date()),
            PanelFold("second", clock[85].date(), clock[-1].date()),
        ),
        features=(PanelHypothesis("momentum", "roc(close,3)"), PanelHypothesis("reversal", "-roc(close,2)")),
        economic_features=("momentum", "reversal"),
        target=ForecastTarget("1d", 2, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 5.0),
        ic=ICPolicy(min_assets=4, min_observations=10, hac_lags=2),
        selection=ForecastSelection("equities", "a" * 64, "b" * 64, "c" * 64, pd.Timestamp("2024-07-01T12:00:00Z")),
        feed="alpaca:iex",
        ridge_alpha=100,
        train_sessions=30,
        min_train_sessions=10,
        min_train_rows=20,
        refit_sessions=5,
        history_sessions=5,
    )
    bars = {}
    for symbol in symbols:
        rng = np.random.default_rng(sum(ord(c) for c in symbol))
        close = 100 * np.exp(rng.normal(0, 0.01, len(clock)).cumsum())
        opening = close * np.exp(rng.normal(0, 0.002, len(clock)))
        bars[symbol] = [
            {
                "t": t.tz_convert("UTC").isoformat(),
                "o": float(o),
                "c": float(c),
                "h": float(max(o, c) * 1.01),
                "l": float(min(o, c) * 0.99),
                "v": 1000,
                "n": 10,
                "vw": float(c),
                "vendor_extra": {"retained": True},
            }
            for t, o, c in zip(clock, opening, close, strict=True)
        ]
    return clock, plan, bars


def _read_reference(reference, directory=None):
    path = Path(reference["artifact"])
    if directory is not None:
        path = directory / path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
    assert path.stat().st_mode & 0o777 == 0o600
    return json.loads(path.read_text())


@pytest.mark.parametrize("fault", [None, "late_ipo", "malformed", "provider", "cleaned_nan", "null_only"])
async def test_sdk_forecast_preserves_dynamic_membership_and_every_frozen_attempt(
    alpaca_http, forecast_repository, forecast_sdk_case, tmp_path, fault
):
    venue, broker = alpaca_http
    repo = forecast_repository
    clock, plan, bars = forecast_sdk_case
    loop = asyncio.get_running_loop()
    output = tmp_path / "forecast"
    prior_accounting = []

    async def verify_accounting_before_access():
        assert (await repo.get("family/all"))["trial_count"] == plan.trial_count
        async with repo.store.db.session_factory() as session:
            events = (await session.scalars(select(DomainEventRecord))).all()
        exclusions = [
            json.loads(event.payload)["value"]
            for event in events
            if json.loads(event.payload)["key"].startswith("external-observation/")
        ]
        assert {row["symbol"] for row in exclusions} == set(plan.acquisition_symbols)
        assert all(pd.Timestamp(row["start"]) == clock[0] for row in exclusions)
        assert all(pd.Timestamp(row["end"]) == clock[-1] + pd.DateOffset(days=1) for row in exclusions)
        prior_accounting.append(True)

    def response(method, path, query, body):
        assert method == "GET"
        if not prior_accounting:
            assert json.loads((output / "manifest.json").read_text())["plan"] == plan.document()
            asyncio.run_coroutine_threadsafe(verify_accounting_before_access(), loop).result(timeout=5)
        if path == "/v2/calendar":
            return 200, [{"date": t.date().isoformat(), "open": "09:30", "close": "16:00"} for t in clock]
        assert path == "/v2/stocks/bars"
        symbol = query["symbols"][0]
        assert query["feed"] == ["iex"] and query["timeframe"] == ["1Day"] and query["adjustment"] == ["all"]
        assert pd.Timestamp(query["start"][0]) == clock[0]
        assert pd.Timestamp(query["end"][0]) == clock[-1] + pd.DateOffset(days=1) - pd.Timedelta(microseconds=1)
        second = "page_token" in query
        if symbol == "BBB":
            if fault == "malformed":
                return 200, {"bars": {"BBB": {}}, "next_page_token": None}
            if fault == "provider" and second:
                return 403, {"message": "fixture provider refusal", "code": 40310000}
            if fault == "null_only":
                return 200, {"bars": {"BBB": [None]}, "next_page_token": None}
        selected = []
        for index in range(60 if second else 0, 120 if second else 60):
            if fault == "late_ipo" and symbol == "BBB" and index < 70:
                continue
            row = dict(bars[symbol][index])
            if fault == "cleaned_nan" and symbol == "BBB" and index == 90:
                row["c"] = "NaN"
            selected.append(row)
        return 200, {"bars": {symbol: selected}, "next_page_token": None if second else "page2"}

    venue.override = response
    source = AlpacaSessionSource(
        AlpacaDataProvider(
            stock_client=broker.data_client,
            feed="iex",
            evidence=BarEvidenceStore(tmp_path / "raw", MarketDataEvidenceConfig()),
        ),
        broker.client,
    )
    service = AlphaPanelService(
        repo, source, acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0), compute=compute_panel_forecast
    )
    result = await service.run(plan, output, environment={"fixture": True}, as_of=pd.Timestamp("2025-01-01T00:00Z"))
    completed = fault in (None, "late_ipo")
    assert prior_accounting == [True]
    assert result["status"] == ("completed" if completed else "failed"), result
    assert result["charged_trials"] == plan.trial_count and not result["authorizes_promotion"]
    assert result["completed_comparisons"] == (plan.trial_count if completed else 0)
    if completed:
        assert len(result["supports"]) == 2 and len(result["trials"]) == 8
        support_by_fold = {row["fold"]: row for row in result["supports"]}
        for fold in plan.folds:
            support = support_by_fold[fold.name]
            expected = clock[(clock.date >= fold.start) & (clock.date <= fold.end)]
            assert support["cohort"] == "equities" and support["symbols"] == list(plan.acquisition_symbols)
            assert [pd.Timestamp(value) for value in support["dates"]] == list(expected)
            eligible = np.asarray(support["eligible"])
            assert eligible.shape == (len(expected), 4)
            assert eligible[:, [0, 2, 3]].all()
            assert eligible[:, 1].tolist() == (
                [t >= clock[74] for t in expected] if fault == "late_ipo" else [True] * len(expected)
            )
            assert support["stage_counts"]["matured_decision_dates"] == len(expected) - plan.target.horizon_bars
            assert support["fits"] and all(fit["status"] == "fitted" for fit in support["fits"])
            for fit in support["fits"]:
                assert pd.Timestamp(fit["last_training_label_available_at"]) < pd.Timestamp(fit["decision_at"])
                assert fit["training_dates"] <= plan.train_sessions and fit["training_rows"] >= plan.min_train_rows
                assert len(fit["fitted_model"]["standardization"]["mean"]) == len(plan.features)
        # Features remain available at the data boundary; unknowable future labels stay missing.
        terminal = support_by_fold["second"]
        assert np.asarray(terminal["eligible"])[-2:].all()
        assert terminal["targets"][-2:] == [[None] * 4] * 2
        assert {(row["model"], row["fold"]) for row in result["trials"]} == {
            (model, fold.name) for model in ("momentum", "reversal", "ridge", "training_mean") for fold in plan.folds
        }
        for trial in result["trials"]:
            support = support_by_fold[trial["fold"]]
            assert trial["predictions"]["dates"] == support["dates"]
            assert trial["predictions"]["symbols"] == support["symbols"]
    for name in ("manifest", "inputs", "calendar"):
        assert result[f"{name}_hash"] == hashlib.sha256((output / f"{name}.json").read_bytes()).hexdigest()
    inputs = json.loads((output / "inputs.json").read_text())
    checkpoints = [_read_reference(ref, output) for ref in inputs["members"]]
    assert len(checkpoints) == 4 and {row["symbol"] for row in checkpoints} == set(plan.acquisition_symbols)
    assert len(inputs["receipts"]) == 5
    assert all(row["requested_at"] <= row["received_at"] for row in inputs["receipts"])
    assert inputs["datasets"]["DDD"]["rows"] == 120
    if fault in ("malformed", "provider"):
        failure = inputs["failures"]["BBB"]
        assert failure["error_type"] == "BarAcquisitionError"
        assert "BBB" not in inputs["datasets"]
        raw = _read_reference(failure["evidence"])
        assert raw["status"] == "failed" and len(raw["pages"]) == 1
        assert raw["error_type"] == ("BarResponseError" if fault == "malformed" else "APIError")
        _read_reference(raw["pages"][0])
    else:
        assert not inputs["failures"] and len(inputs["datasets"]) == 4
        dataset = inputs["datasets"]["BBB"]
        quality = dataset["attrs"]["source_quality"]
        raw = _read_reference(dataset["attrs"]["evidence"])
        assert raw["status"] == "complete" and len(raw["pages"]) == (1 if fault == "null_only" else 2)
        assert raw["normalization"]["source_quality"] == quality
        assert all(_read_reference(ref)["response"] for ref in raw["pages"])
        assert quality["sdk_omitted_rows"] + quality["normalization_dropped_rows"] == int(not completed)
        assert dataset["rows"] == {None: 120, "late_ipo": 50, "cleaned_nan": 119, "null_only": 0}[fault]
    assert all(method == "GET" for method, *_ in venue.calls)
    assert (await repo.get("family/all"))["trial_count"] == plan.trial_count
    assert (await repo.snapshot()).generation == 0
    assert await repo.get(repo._variance_family_key("1d")) is None
    evidence = await repo.get(f"diagnostic/{result['run_id']}")
    assert evidence["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    calls_before_replay = len(venue.calls)
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == evidence
    assert (await repo.snapshot()).generation == 0 and len(venue.calls) == calls_before_replay
    with pytest.raises(FileExistsError):
        await service.run(plan, output, environment={})
    assert (await repo.get("family/all"))["trial_count"] == plan.trial_count
    assert len(venue.calls) == calls_before_replay
