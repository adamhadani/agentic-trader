"""Real SDK native-day captures through the journal, causal math and terminal outcomes."""

import asyncio
import hashlib
import json
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import agentic_trader.research.alpha.daily_observations as daily_module
from agentic_trader.config import DailyAcquisitionConfig, DailyPanelWorkerConfig, MarketDataEvidenceConfig
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_observations import DailyPanelService
from agentic_trader.research.alpha.daily_plan import DailyPanelPlan
from agentic_trader.storage.alpha_daily import DailyCampaignRepository


@pytest.fixture(scope="module")
def native_panel():
    rng = np.random.default_rng(932)
    dates = pd.date_range("2021-01-04", periods=750, freq="B", tz="America/New_York")
    symbols, factors = tuple(f"A{i:02}" for i in range(18)), tuple(f"F{i}" for i in range(9))
    factor_returns = rng.normal(0, 0.005, (len(dates), 9))
    stock_returns = factor_returns @ rng.normal(0, 0.25, (9, 18)) + rng.normal(0.0002, 0.008, (len(dates), 18))
    prices = 100 * np.cumprod(1 + np.column_stack([stock_returns, factor_returns]), axis=0)
    opens = np.vstack([np.full(27, 100), prices[:-1]])
    bars = {
        symbol: [
            {
                "t": t.tz_convert("UTC").isoformat(),
                "o": float(o),
                "h": float(max(o, c) * 1.001),
                "l": float(min(o, c) * 0.999),
                "c": float(c),
                "v": 1000,
                "n": 10,
                "vw": float(c),
            }
            for t, o, c in zip(dates, opens[:, column], prices[:, column], strict=True)
        ]
        for column, symbol in enumerate((*symbols, *factors))
    }
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in dates
    )
    plan = DailyPanelPlan(
        "sdk-daily",
        symbols,
        factors,
        dates[700].date(),
        dates[700].date() + timedelta(days=1),
        dates[0].date(),
        "a" * 64,
        "b" * 64,
    )
    return SimpleNamespace(dates=dates, bars=bars, sessions=sessions, plan=plan)


def load_reference(reference):
    raw = Path(reference["artifact"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference["artifact_hash"]
    return json.loads(raw)


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("fault", [None, "missing", "provider"])
async def test_real_sdk_daily_forecast_and_outcome_preserve_claims_and_source_gaps(
    native_panel, alpaca_http, temp_db, tmp_path, monkeypatch, fault
):
    c, (venue, broker) = native_panel, alpaca_http
    # This is a large-response functional contract test. The shared 200ms fixture
    # deadline is for small order/timeout tests; retain a bounded budget here too.
    broker.client.request_timeout = 2.0
    broker.data_client.request_timeout = 2.0
    await temp_db.init_db()
    repo = DailyCampaignRepository(temp_db.workflows)
    window = c.plan.decision_window(c.plan.start_date, c.sessions)
    now = [window.native_closed_at - pd.Timedelta(hours=1)]

    def clock():
        now[0] += pd.Timedelta(microseconds=1)
        return now[0]

    async def database_clock(_session):
        return clock()

    monkeypatch.setattr(repo, "_now", database_clock)
    excluded = []
    exclude = repo.exclude_observed_interval

    async def recorded_exclusion(**kwargs):
        await exclude(**kwargs)
        excluded.append(kwargs["symbol"])

    monkeypatch.setattr(repo, "exclude_observed_interval", recorded_exclusion)
    requests = []

    def response(method, path, query, body):
        if path == "/v2/calendar":
            start, end = query["start"][0], query["end"][0]
            return 200, [
                {"date": t.date().isoformat(), "open": "09:30", "close": "16:00"}
                for t in c.dates
                if start <= t.date().isoformat() <= end
            ]
        if path != "/v2/stocks/bars":
            return None
        symbol = query["symbols"][0]
        assert set(excluded[-len(c.plan.acquisition_symbols) :]) == set(c.plan.acquisition_symbols)
        assert query["timeframe"] == ["1Day"] and query["feed"] == ["iex"] and query["adjustment"] == ["all"]
        requests.append(symbol)
        if symbol == c.plan.symbols[0] and fault == "provider":
            return 403, {"code": 40310000, "message": "fixture source unavailable"}
        start, end = pd.Timestamp(query["start"][0]), pd.Timestamp(query["end"][0])
        rows = (
            []
            if symbol == c.plan.symbols[0] and fault == "missing"
            else [bar for bar in c.bars[symbol] if start <= pd.Timestamp(bar["t"]) <= end]
        )
        return 200, {"bars": {symbol: rows}, "next_page_token": None}

    venue.override = response
    source = AlpacaSessionSource(
        AlpacaDataProvider(
            stock_client=broker.data_client,
            feed="iex",
            evidence=BarEvidenceStore(tmp_path / "raw", MarketDataEvidenceConfig()),
        ),
        broker.client,
    )

    def worker():
        return DailyPanelService(
            repo,
            source,
            c.plan,
            DailyPanelWorkerConfig(),
            acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
            directory=tmp_path / "campaign",
            runtime={"fixture": True},
            clock=clock,
        )

    service = worker()
    assert (await service.run_once())["decisions"] == 0
    assert not requests and (await repo.get("family/all"))["trial_count"] == 4
    now[0] = window.available_at + pd.Timedelta(minutes=1)
    computing = threading.Event()
    compute = daily_module.compute_daily_forecasts

    def tracked_compute(*args, **kwargs):
        computing.set()
        try:
            return compute(*args, **kwargs)
        finally:
            computing.clear()

    monkeypatch.setattr(daily_module, "compute_daily_forecasts", tracked_compute)
    task = asyncio.create_task(service.run_once())
    heartbeats = 0
    while not task.done():
        if computing.is_set():
            heartbeats += 1
        await asyncio.sleep(0.002)
    assert (await task)["decisions"] == 1
    if fault != "provider":
        assert heartbeats > 0, "Real forecast math blocked the asyncio event loop"
    assert requests == list(c.plan.acquisition_symbols)
    records = (await repo.records(c.plan.campaign_id))["records"]
    assert len(records) == 1
    decision = records[0]
    inputs = load_reference(decision["evidence"]["inputs"])
    assert set(inputs["receipts"]) == set(c.plan.acquisition_symbols)
    assert len(list((tmp_path / "campaign").rglob("members/*.json"))) == len(c.plan.acquisition_symbols)
    if fault == "provider":
        assert decision["status"] == "unavailable"
        assert inputs["failures"][c.plan.symbols[0]]["error_type"] == "BarAcquisitionError"
        assert (await repo.campaign(c.plan.campaign_id))["state_ref"] is None
        assert "forecast" not in decision["evidence"]
    else:
        assert decision["status"] == "scored", decision["evidence"]
        forecast = load_reference(decision["evidence"]["forecast"])
        campaign = await repo.campaign(c.plan.campaign_id)
        state = load_reference(campaign["state_ref"])
        assert state == forecast["residual_state"] and campaign["state_generation"] == 1
        assert forecast["ridge_fit"]["training_sessions"] == 504
        assert forecast["ridge_fit"]["last_label_date"] == c.dates[699].date().isoformat()
        assert forecast["economic_scheduled"] and len(forecast["arms"]) == 4
        assert not forecast["authorizes_promotion"] and not inputs["failures"]
        if fault == "missing":
            assert forecast["common_support"][c.plan.symbols[0]] is False
            assert sum(forecast["common_support"].values()) == 17
        else:
            assert all(forecast["common_support"].values())
        now[0] = window.outcome_available_at + pd.Timedelta(minutes=1)
        assert (await service.run_once())["outcomes"] == 1
        outcome_record = (await repo.records(c.plan.campaign_id, kind="outcome"))["records"][0]
        assert outcome_record["status"] == "complete", outcome_record["evidence"]
        outcome = load_reference(outcome_record["evidence"]["outcome"])
        assert outcome["forecast_id"] == forecast["forecast_id"]
        assert all(arm["ic"] is not None and arm["basket"]["gross_return"] is not None for arm in outcome["arms"])
        assert load_reference(decision["evidence"]["forecast"]) == forecast
        assert len(requests) == 2 * len(c.plan.acquisition_symbols)

    # Repeated polls and a fresh process must not reacquire an already consumed claim.
    before_requests = list(requests)
    before_files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*.json")}
    before_decisions = await repo.records(c.plan.campaign_id)
    before_outcomes = await repo.records(c.plan.campaign_id, kind="outcome")
    assert (await service.run_once())["decisions"] == 0
    assert (await worker().run_once())["outcomes"] == 0
    assert requests == before_requests
    assert before_files == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*.json")}
    await repo.rebuild()
    assert await repo.records(c.plan.campaign_id) == before_decisions
    assert await repo.records(c.plan.campaign_id, kind="outcome") == before_outcomes
    assert (await repo.get("family/all"))["trial_count"] == 4
    assert (await repo.snapshot()).generation == 0
    assert all(call[0] == "GET" for call in venue.calls)
    await temp_db.engine.dispose()
