"""Actual paginated SDK reads, panel diagnostics and journal replay across DB backends."""

import hashlib
import json
from dataclasses import fields
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.config import DailyAcquisitionConfig, MarketDataEvidenceConfig
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.market.bars import FIXED_BAR_LAYOUT
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis, PanelStudyPlan
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.research.alpha.persistent_study import BookTriage, PersistentStudyPlan, compute_persistent_study
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget
from agentic_trader.research.alpha.volume import VolumeContract, VolumePolicy
from agentic_trader.research.alpha.volume_study import VolumeFold, VolumeStudyPlan, compute_volume_study
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def panel_repository(request, temp_db):
    db = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await db.init_db()
    try:
        yield AlphaRepository(db.workflows)
    finally:
        await db.engine.dispose()


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("fault", [None, "missing", "provider"])
@pytest.mark.parametrize("study", ["baskets", "book", "volume"])
@pytest.mark.parametrize("feed", ["sip", "iex"])
async def test_sdk_panel_preserves_every_attempt_and_excludes_all_members_before_reads(
    alpaca_http, panel_repository, tmp_path, fault, study, feed
):
    venue, broker = alpaca_http
    repo = panel_repository
    clock = pd.date_range("2022-01-03", periods=100, freq="B", tz="America/New_York")
    plan = PanelStudyPlan(
        "fixture",
        ("AAA", "BBB", "CCC", "DDD"),
        "SPY",
        clock[0].date(),
        clock[-1].date(),
        (PanelFold("one", clock[40].date(), clock[-1].date()),),
        (PanelHypothesis("momentum", "roc(close,20)"),),
        ForecastTarget("1d", 5, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        (0.0, 5.0),
        1,
        ICPolicy(min_assets=4, min_observations=10, hac_lags=5),
        feed=f"alpaca:{feed}",
    )
    compute = None
    if study == "book":
        arguments = {f.name: getattr(plan, f.name) for f in fields(plan)}
        arguments["hypotheses"] = (*plan.hypotheses, PanelHypothesis("reversal", "-roc(close,5)"))
        plan = PersistentStudyPlan(
            **arguments,
            blend_window=10,
            borrow_bps=(0.0, 300.0),
            funding_bps=(0.0, 500.0),
            triage=BookTriage(primary_cost_bps=0.0),
        )
        compute = compute_persistent_study
    if study == "volume":
        plan = VolumeStudyPlan(
            "fixture",
            tuple(sorted(plan.acquisition_symbols)),
            clock[0].date(),
            clock[-1].date(),
            (VolumeFold("one", clock[10].date(), clock[59].date(), clock[60].date(), clock[-1].date()),),
            VolumeContract(f"alpaca:{feed}", "1d", "all", FIXED_BAR_LAYOUT),
            VolumePolicy(lookback=5, min_observations=30),
        )
        compute = compute_volume_study
    requests = []

    def response(method, path, query, body):
        if path == "/v2/calendar":
            return 200, [{"date": t.date().isoformat(), "open": "09:30", "close": "16:00"} for t in clock]
        if path == "/v2/stocks/bars":
            symbol = query["symbols"][0]
            requests.append((symbol, query))
            assert (
                query["timeframe"] == ["1Day"] and query["adjustment"] == [plan.adjustment] and query["feed"] == [feed]
            )
            assert pd.Timestamp(query["start"][0]) == clock[0]
            assert pd.Timestamp(query["end"][0]) == clock[-1] + pd.DateOffset(days=1) - pd.Timedelta(microseconds=1)
            if fault == "provider" and symbol == "BBB":
                return 403, {"message": "fixture unavailable", "code": 40310000}
            offset = 50 if "page_token" in query else 0
            rng = np.random.default_rng(sum(ord(c) for c in symbol))
            prices = 100 * np.exp(rng.normal(0, 0.01, 100).cumsum())
            bars = [
                {
                    "t": t.tz_convert("UTC").isoformat(),
                    "o": float(v),
                    "h": float(v * 1.01),
                    "l": float(v * 0.99),
                    "c": float(v),
                    "v": 1000,
                    "n": 10,
                    "vw": float(v),
                }
                for i, (t, v) in enumerate(zip(clock, prices, strict=True))
                if offset <= i < offset + 50 and not (fault == "missing" and symbol == "AAA" and i == 60)
            ]
            return 200, {"bars": {symbol: bars}, "next_page_token": "page2" if offset == 0 else None}
        return None

    venue.override = response
    source = AlpacaSessionSource(
        AlpacaDataProvider(
            stock_client=broker.data_client,
            feed=feed,
            evidence=BarEvidenceStore(tmp_path / "raw", MarketDataEvidenceConfig()),
        ),
        broker.client,
    )
    output = tmp_path / "panel"
    result = await AlphaPanelService(
        repo, source, acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0), compute=compute
    ).run(plan, output, environment={"fixture": True}, as_of=pd.Timestamp("2023-01-01T00:00Z"))
    assert result["status"] == ("completed" if fault is None else "failed")
    assert result["charged_trials"] == plan.trial_count and not result["authorizes_promotion"]
    if fault == "missing":
        assert result["coverage"]["AAA"]["missing_dates"] == [clock[60].date().isoformat()]
    for name in ("manifest", "inputs", "calendar"):
        assert result[f"{name}_hash"] == hashlib.sha256((output / f"{name}.json").read_bytes()).hexdigest()
    assert (await repo.get("family/all"))["trial_count"] == plan.trial_count
    assert (await repo.snapshot()).generation == 0
    assert await repo.get(repo._variance_family_key("1d")) is None
    inputs = json.loads((output / "inputs.json").read_text())
    assert all(r["requested_at"] <= r["received_at"] for r in inputs["receipts"])
    if fault == "provider":
        assert inputs["failures"]["BBB"]["error_type"] == "BarAcquisitionError"
        raw = inputs["failures"]["BBB"]["evidence"]
        assert json.loads(Path(raw["artifact"]).read_text())["error_type"] == "APIError"
    else:
        assert len(requests) == 10 and len(inputs["datasets"]) == 5
    if study == "volume" and fault is None:
        assert len(result["profiles"]) == 5
        for profile in result["profiles"]:
            assert profile["calibration"]["contract"]["feed"] == f"alpaca:{feed}"
            assert profile["threshold"] == 1.0
            assert profile["evaluation_surge_fraction"] == 0.0
            assert profile["training_observations"] == 50
    assert all(c[0] == "GET" for c in venue.calls)
    evidence = await repo.get(f"diagnostic/{result['run_id']}")
    assert evidence["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    async with repo.store.db.session_factory() as session:
        events = (await session.scalars(select(DomainEventRecord).order_by(DomainEventRecord.id))).all()
    exclusions = [
        (e, json.loads(e.payload)["value"])
        for e in events
        if json.loads(e.payload)["key"].startswith("external-observation/")
    ]
    assert {v["symbol"] for _, v in exclusions} == set(plan.acquisition_symbols)
    first_read = pd.Timestamp(inputs["receipts"][0]["requested_at"])
    assert all(
        pd.Timestamp(e.recorded_at).tz_localize("UTC") <= first_read
        if pd.Timestamp(e.recorded_at).tzinfo is None
        else pd.Timestamp(e.recorded_at) <= first_read
        for e, _ in exclusions
    )
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == evidence
    with pytest.raises(FileExistsError):
        await AlphaPanelService(
            repo, source, acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0), compute=compute
        ).run(plan, output, environment={})
    assert (await repo.get("family/all"))["trial_count"] == plan.trial_count
