"""Actual SDK HTTP calendar + paginated minute data feed the replay workflow."""

import asyncio
import json
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.market.bars import SessionClockPolicy, SessionSchedule, SessionSnapshot, build_session_bars
from agentic_trader.research.alpha.data import load_dataset
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot
from agentic_trader.research.alpha.replay import ReplayPlan, simulate_session_strategy
from agentic_trader.research.alpha.replay_workflow import AlphaReplayService
from agentic_trader.research.alpha.shadow import AlphaShadowService
from agentic_trader.research.alpha.validation import DatasetManifest
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import AlphaProjectionRecord


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("fault", [None, "missing_minute", "empty_calendar"])
async def test_sdk_calendar_pagination_and_replay_remain_read_only(alpaca_http, temp_db, tmp_path, fault):
    venue, broker = alpaca_http

    def response(method, path, query, body):
        if path == "/v2/calendar":
            return 200, [] if fault == "empty_calendar" else [
                {"date": "2024-11-27", "open": "09:30", "close": "16:00"},
                {"date": "2024-11-29", "open": "09:30", "close": "13:00"},
            ]
        if path == "/v2/stocks/bars":
            second = "page_token" in query
            count = 210 if second else 390
            index = pd.date_range("2024-11-29 14:30Z" if second else "2024-11-27 14:30Z", periods=count, freq="min")
            if fault == "missing_minute" and second:
                index = index[1:]
            bars = [
                {"t": t.isoformat(), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000, "n": 10, "vw": 100}
                for t in index
            ]
            return 200, {"bars": {"SPY": bars}, "next_page_token": None if second else "next"}
        return None

    venue.override = response
    source = AlpacaSessionSource(AlpacaDataProvider(stock_client=broker.data_client, feed="iex"), broker.client)
    plan = ReplayPlan(
        "SPY",
        date(2024, 11, 27),
        date(2024, 11, 29),
        AlphaDefinition(
            "replay",
            "Replay",
            "close",
            semantics_version=3,
            clock=SessionClockPolicy(),
            timeframe="1h",
            eligible_symbols=("SPY",),
            data_feed="alpaca:iex",
        ),
    )
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    try:
        result = await AlphaReplayService(repo, source).run(
            plan, tmp_path / "run", environment={"fixture": True}, as_of=pd.Timestamp("2024-11-30", tz="UTC")
        )
        assert result["status"] == ("completed" if fault is None else "failed")
        assert (await repo.get("family/all"))["trial_count"] == 1
        assert (await repo.snapshot()).generation == 0
        assert all(method == "GET" for method, *_ in venue.calls)
        if fault != "empty_calendar":
            calls = [c for c in venue.calls if c[1] == "/v2/stocks/bars"]
            assert len(calls) == 2
            assert all(
                c[2]["timeframe"] == ["1Min"] and c[2]["feed"] == ["iex"] and c[2]["adjustment"] == ["raw"]
                for c in calls
            )
            assert result["coverage"]["missing_minutes"] == (1 if fault else 0)
        if fault is None:
            assert result["sample_length"] == 600
            assert len(result["signal_bars"]) == 11
            assert result["signal_bars"][-1]["closed_at"] == "2024-11-29T18:00:00+00:00"
            # Real SDK → retained artifact → session clock cannot be re-labelled
            # as existing fixed-duration qualification evidence.
            directory = tmp_path / "run"
            observations = json.loads((directory / "observations.json").read_text())
            schedule = SessionSchedule.from_document(json.loads((directory / "calendar.json").read_text()))
            session_bars = build_session_bars(
                load_dataset(directory / observations["dataset"]),
                schedule,
                "1h",
                as_of=pd.Timestamp("2024-11-30", tz="UTC"),
            )
            with pytest.raises(ValueError, match="clock"):
                DatasetManifest.from_frame(
                    session_bars.signals,
                    symbol="SPY",
                    timeframe="1h",
                    feed="alpaca:iex",
                    adjustment="raw",
                    universe_version="fixture",
                )
    finally:
        await temp_db.engine.dispose()


@pytest.mark.postgres
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
async def test_diagnostic_completion_is_immutable_across_postgres_clients(postgres_test_db):
    first, second = SignalDatabase(db_url=postgres_test_db), SignalDatabase(db_url=postgres_test_db)
    repos = [AlphaRepository(db.workflows) for db in (first, second)]
    try:
        await asyncio.gather(*(r.reserve_run("replay", symbol="SPY", timeframe="15m", trials=1) for r in repos))
        results = await asyncio.gather(
            *(
                r.record_diagnostic("replay", {"status": "completed", "artifact_hash": str(i)})
                for i, r in enumerate(repos)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(r, ValueError) for r in results) == 1
        assert (await repos[0].get("family/all"))["trial_count"] == 1
        before = await repos[0].get("diagnostic/replay")
        await repos[1].rebuild()
        assert await repos[0].get("diagnostic/replay") == before
        assert (await repos[0].get("research/latest"))["artifact_hash"] == before["artifact_hash"]
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def session_db(request, temp_db):
    db = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await db.init_db()
    try:
        yield db
    finally:
        await db.engine.dispose()


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
async def test_sdk_session_snapshot_scores_agree_and_replay_never_grants_credit(alpaca_http, session_db):

    venue, broker = alpaca_http

    def response(method, path, query, body):
        if path == "/v2/calendar":
            return 200, [{"date": "2024-11-27", "open": "09:30", "close": "16:00"}]
        if path == "/v2/stocks/bars":
            index = pd.date_range("2024-11-27 14:30Z", periods=390, freq="min")
            bars = []
            for i, t in enumerate(index):
                price = 100 + i**2 / 100000
                bars.append(
                    {
                        "t": t.isoformat(),
                        "o": price,
                        "h": price + 1,
                        "l": price - 1,
                        "c": price,
                        "v": 1000,
                        "n": 10,
                        "vw": price,
                    }
                )
            return 200, {"bars": {"SPY": bars}, "next_page_token": None}
        return None

    venue.override = response
    source = AlpacaSessionSource(AlpacaDataProvider(stock_client=broker.data_client, feed="sip"), broker.client)
    day = date(2024, 11, 27)
    sessions = await asyncio.to_thread(source.calendar, day, day)
    schedule = SessionSchedule(day, day, sessions, "alpaca_calendar")
    minutes = await asyncio.to_thread(source.minutes, "SPY", sessions[0].open, sessions[0].close, "alpaca:sip")
    definition = AlphaDefinition(
        "session",
        "Session",
        "delta(close,3)",
        semantics_version=3,
        clock=SessionClockPolicy(),
        timeframe="15m",
        data_feed="alpaca:sip",
        eligible_symbols=("SPY",),
        normalization_window=10,
        entry_threshold=0.1,
    )
    now = pd.Timestamp("2024-11-27 20:01Z")
    bars = build_session_bars(minutes, schedule, "15m", as_of=now)
    snapshot = SessionSnapshot(bars, symbol="SPY", requested_at=now - pd.Timedelta(seconds=2), received_at=now)
    data = ContractMarketData(symbol="SPY", session_bars={"15m": snapshot})
    candidates = FormulaicAlphaStrategy(definition, clock=lambda: now).evaluate(data)
    replay = simulate_session_strategy(definition, bars)
    assert candidates[0].alpha_score == pytest.approx(replay["decisions"][-1]["score"])
    repo = AlphaRepository(session_db.workflows)
    await repo.register(definition, actor="fixture")
    await repo.set_shadow(definition.version_id, actor="fixture", expected_generation=0)
    registry = await repo.snapshot()
    for _ in range(2):
        observed = await AlphaShadowService(repo).observe(registry, data, as_of=now)
        assert observed[0]["score"] == pytest.approx(candidates[0].alpha_score)
        assert observed[0]["received_at"] == snapshot.received_at.isoformat()
        assert observed[0]["requested_at"] == snapshot.requested_at.isoformat()
    with pytest.raises(ValueError, match="Session-clock activation"):
        await repo.promote(definition.version_id, actor="fixture", expected_generation=registry.generation)
    async with session_db.session_factory() as db:
        records = (
            await db.scalars(select(AlphaProjectionRecord).where(AlphaProjectionRecord.key.like("forecast/%")))
        ).all()
    assert len(records) == 1
    assert await repo.get(f"shadow/{definition.version_id}") is None
    await repo.rebuild()
    assert await repo.snapshot() == RegistrySnapshot(1, (), (definition,))
    assert await repo.get(f"shadow/{definition.version_id}") is None
    assert all(method == "GET" for method, *_ in venue.calls)
