"""Actual SDK daily pages, complete cohort evidence, and durable screen accounting."""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.config import DailyAcquisitionConfig, MarketDataEvidenceConfig
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.research.alpha.liquidity import EquityLiquidityPlan, LiquidityMember, compute_liquidity_study
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def liquidity_repository(request, temp_db):
    db = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await db.init_db()
    try:
        yield AlphaRepository(db.workflows)
    finally:
        await db.engine.dispose()


def read_reference(reference, directory=None):
    path = Path(reference["artifact"])
    if directory is not None:
        path = directory / path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
    assert path.stat().st_mode & 0o777 == 0o600
    return json.loads(path.read_text())


@pytest.mark.parametrize(
    "fault", [None, "empty", "malformed", "provider", "invalid_data", "cleaned_nan", "null_only", "mixed_null"]
)
async def test_sdk_liquidity_retains_every_member_without_selecting_around_provider_failures(
    alpaca_http, liquidity_repository, tmp_path, fault
):
    venue, broker = alpaca_http
    repo = liquidity_repository
    loop = asyncio.get_running_loop()
    clock = pd.date_range("2024-06-03", periods=5, freq="B", tz="America/New_York")
    plan = EquityLiquidityPlan(
        campaign_id="fixture",
        snapshot_id="a" * 64,
        members=tuple(
            LiquidityMember(str(UUID(int=i + 1)), symbol, "unknown") for i, symbol in enumerate(("AAA", "BBB", "CCC"))
        ),
        snapshot_observed_at=pd.Timestamp("2024-06-10T12:00:00Z"),
        start=clock[0].date(),
        end=clock[-1].date(),
        feed="alpaca:iex",
        lookback=3,
        min_positive_volume_sessions=3,
        selection_size=2,
    )
    output = tmp_path / "screen"
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
        assert query["feed"] == ["iex"] and query["timeframe"] == ["1Day"] and query["adjustment"] == ["raw"]
        assert pd.Timestamp(query["start"][0]) == clock[0]
        assert pd.Timestamp(query["end"][0]) == clock[-1] + pd.DateOffset(days=1) - pd.Timedelta(microseconds=1)
        second = "page_token" in query
        if symbol == "BBB":
            if fault == "empty":
                return 200, {"bars": {}, "next_page_token": None}
            if fault == "null_only":
                return 200, {"bars": {"BBB": [None]}, "next_page_token": None}
            if fault == "malformed":
                return 200, {"bars": {"BBB": {}}, "next_page_token": None}
            if fault == "provider" and second:
                return 403, {"message": "fixture provider refusal", "code": 40310000}
        volume = {"AAA": 100, "BBB": 400, "CCC": 300}[symbol]
        selected = clock[2:] if second else clock[:2]
        bars = [
            {
                "t": t.tz_convert("UTC").isoformat(),
                "o": 10,
                "h": 11,
                "l": 9,
                "c": "NaN" if fault == "cleaned_nan" and symbol == "BBB" and t == clock[-1] else 10,
                "v": -1 if fault == "invalid_data" and symbol == "BBB" and t == clock[-1] else volume,
                "n": 1,
                "vw": 10,
                "vendor_extra": {"retained": True},
            }
            for t in selected
        ]
        if fault == "mixed_null" and symbol == "BBB" and not second:
            bars.append(None)
        return 200, {"bars": {symbol: bars}, "next_page_token": None if second else "two"}

    venue.override = response
    source = AlpacaSessionSource(
        AlpacaDataProvider(
            stock_client=broker.data_client,
            feed="iex",
            evidence=BarEvidenceStore(tmp_path / "raw", MarketDataEvidenceConfig()),
        ),
        broker.client,
    )
    result = await AlphaPanelService(
        repo,
        source,
        acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
        compute=compute_liquidity_study,
    ).run(plan, output, environment={"fixture": True}, as_of=plan.snapshot_observed_at)

    assert prior_accounting == [True]
    assert result["status"] == ("completed" if fault in (None, "empty") else "failed")
    assert result["charged_trials"] == 1
    assert result["completed_comparisons"] == int(fault in (None, "empty"))
    assert not result["authorizes_promotion"] and not result["point_in_time_historical_membership"]
    inputs = json.loads((output / "inputs.json").read_text())
    checkpoints = [read_reference(ref, output) for ref in inputs["members"]]
    assert len(checkpoints) == 3 and {row["symbol"] for row in checkpoints} == set(plan.acquisition_symbols)
    assert len(inputs["receipts"]) == 4 and all(row["requested_at"] <= row["received_at"] for row in inputs["receipts"])
    assert {row["symbol"] for row in result["members"]} == set(plan.acquisition_symbols)
    assert inputs["datasets"]["CCC"]["rows"] == 5  # Acquisition continues after another member's failed read.
    assert result["selection_available"] == (fault in (None, "empty"))
    assert [row["symbol"] for row in result["selected"]] == (
        ["BBB", "CCC"] if fault is None else ["CCC", "AAA"] if fault == "empty" else []
    )
    member = next(row for row in result["members"] if row["symbol"] == "BBB")
    if fault in ("malformed", "provider"):
        assert inputs["failures"]["BBB"]["error_type"] == "BarAcquisitionError"
        assert member["reasons"] == ["acquisition_failed"]
        raw = read_reference(inputs["failures"]["BBB"]["evidence"])
        assert raw["status"] == "failed" and len(raw["pages"]) == 1
        assert raw["error_type"] == ("BarResponseError" if fault == "malformed" else "APIError")
        read_reference(raw["pages"][0])
        assert "BBB" not in inputs["datasets"]
    else:
        raw = read_reference(inputs["datasets"]["BBB"]["attrs"]["evidence"])
        assert raw["status"] == "complete"
        assert len(raw["pages"]) == (1 if fault in ("empty", "null_only") else 2)
        assert all(read_reference(ref)["response"] for ref in raw["pages"])
        assert member["reasons"] == (
            [] if fault is None else ["missing_window_sessions"] if fault == "empty" else ["invalid_source_evidence"]
        )
        if fault in ("cleaned_nan", "null_only", "mixed_null"):
            quality = inputs["datasets"]["BBB"]["attrs"]["source_quality"]
            assert quality["normalization_dropped_rows"] + quality["sdk_omitted_rows"] == 1
            assert raw["normalization"]["source_quality"] == quality
        if fault == "empty":
            assert raw["normalization"]["normalized_rows"] == 0
            assert member["observed_sessions"] == 0 and len(member["missing_dates"]) == 5
    assert all(method == "GET" for method, *_ in venue.calls)
    assert (await repo.get("family/all"))["trial_count"] == 1
    assert (await repo.snapshot()).generation == 0
    assert await repo.get(repo._variance_family_key("1d")) is None
    evidence = await repo.get(f"diagnostic/{result['run_id']}")
    assert evidence["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == evidence
    assert (await repo.snapshot()).generation == 0
