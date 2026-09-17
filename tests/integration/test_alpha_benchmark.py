"""SDK pagination -> immutable dataset -> forecast diagnostics -> journal replay."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.research.alpha.baselines import ForecastBenchmarkPlan, ForecastTarget
from agentic_trader.research.alpha.benchmark_workflow import AlphaBenchmarkService
from agentic_trader.research.alpha.data import load_dataset, save_dataset
from agentic_trader.research.alpha.validation import DatasetManifest, ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import AlphaProjectionRecord


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def benchmark_repository(request, temp_db):
    db = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await db.init_db()
    try:
        yield AlphaRepository(db.workflows)
    finally:
        await db.engine.dispose()


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
async def test_sdk_forecast_evidence_is_replayable_and_not_promotable(alpaca_http, benchmark_repository, tmp_path):
    venue, broker = alpaca_http
    rng = np.random.default_rng(11)
    close = 100 * np.exp(rng.normal(0, 0.01, 600).cumsum())
    dates = pd.date_range("2020-01-01", periods=600, tz="UTC")
    rows = [
        {
            "t": t.isoformat(),
            "o": float(c),
            "h": float(c * 1.01),
            "l": float(c * 0.99),
            "c": float(c),
            "v": 1000 + i % 17,
            "n": 10,
            "vw": float(c),
        }
        for i, (t, c) in enumerate(zip(dates, close, strict=True))
    ]

    def response(method, path, query, body):
        if path != "/v2/stocks/bars":
            return None
        offset = 0 if "page_token" not in query else 300
        return 200, {"bars": {"SPY": rows[offset : offset + 300]}, "next_page_token": "next" if offset == 0 else None}

    venue.override = response
    provider = AlpacaDataProvider(stock_client=broker.data_client, feed="sip")
    bars = await asyncio.to_thread(
        provider.fetch_bars, "SPY", "1d", start=datetime(2020, 1, 1, tzinfo=UTC), end=datetime(2022, 1, 1, tzinfo=UTC)
    )
    manifest = DatasetManifest.from_frame(
        bars, symbol="SPY", timeframe="1d", feed="alpaca:sip", adjustment="raw", universe_version="fixture"
    ).to_dict()
    path = save_dataset(bars, tmp_path, manifest["content_hash"])
    manifest.update(artifact=str(path), holdout_start=str(bars.index[480]))
    repo = benchmark_repository
    await repo.record_run(
        "sdk-source",
        {"policy": asdict(ValidationPolicy()), "trial_count": 0, "trials": [], "holdout_start": 480},
        manifest,
    )
    result = await AlphaBenchmarkService(repo).run(
        "sdk-source",
        ForecastBenchmarkPlan(ForecastTarget("1d", 5), budget=2),
        tmp_path / "forecast",
        environment={"test": "sdk"},
    )
    assert result["status"] == "completed", result
    assert not result["authorizes_promotion"]
    assert (await repo.get("family/all"))["trial_count"] == 2
    assert (await repo.snapshot()).active == ()
    assert await repo.get(f"run/{result['run_id']}") is None
    assert not (await repo.get(repo._variance_family_key("1d")))["sharpes"]
    evidence = await repo.get(f"diagnostic/{result['run_id']}")
    async with repo.store.db.session_factory() as session, session.begin():
        await session.execute(delete(AlphaProjectionRecord))
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == evidence
    for trial in result["trials"]:
        predictions = load_dataset(tmp_path / "forecast" / trial["predictions_artifact"])
        assert predictions.index.max() < bars.index[480]
        assert set(predictions.columns) == {"prediction", "target", "training_mean", "fold"}
    assert len([c for c in venue.calls if c[1] == "/v2/stocks/bars"]) == 2
    assert all(method == "GET" for method, *_ in venue.calls)
    assert json.loads((tmp_path / "forecast" / "manifest.json").read_text())["source_manifest"] == manifest
