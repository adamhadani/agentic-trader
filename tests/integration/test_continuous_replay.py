"""Actual SDK acquisition chunks feed one uninterrupted simulation and journal attempt."""

import json
from datetime import date

import pandas as pd
import pytest

from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.replay import ReplayPlan
from agentic_trader.research.alpha.replay_workflow import AlphaReplayService
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.mark.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
@pytest.mark.parametrize("mode", ["held", "pending", "failed_chunk"])
async def test_continuous_sdk_replay_never_resets_at_acquisition_boundaries(
    alpaca_http, temp_db, tmp_path, request, monkeypatch, backend, mode
):
    venue, broker = alpaca_http
    index = pd.date_range("2024-10-15 13:30Z", periods=390, freq="min").append(
        pd.date_range("2024-11-29 14:30Z", periods=210, freq="min")
    )
    requests = []

    def response(method, path, query, body):
        if path == "/v2/calendar":
            return 200, [
                {"date": "2024-10-15", "open": "09:30", "close": "16:00"},
                {"date": "2024-11-29", "open": "09:30", "close": "13:00"},
            ]
        if path == "/v2/stocks/bars":
            requests.append(query)
            if mode == "failed_chunk" and len(requests) == 2:
                return 403, {"code": 40310000, "message": "fixture missing entitlement"}
            starts, ends = pd.Timestamp(query["start"][0]), pd.Timestamp(query["end"][0])
            selected = index[(index >= starts) & (index <= ends)]
            bars = []
            for t in selected:
                price = (
                    101
                    if mode == "pending" and pd.Timestamp("2024-10-15 14:00Z") <= t < pd.Timestamp("2024-11-29 14:30Z")
                    else 100
                )
                bars.append(
                    {
                        "t": t.isoformat(),
                        "o": price,
                        "h": price + 0.5,
                        "l": price - 0.5,
                        "c": price,
                        "v": 1000,
                        "n": 10,
                        "vw": price,
                    }
                )
            return 200, {"bars": {"SPY": bars}, "next_page_token": None}
        return None

    venue.override = response

    def scores(definition, signals):
        values = pd.Series(float("nan"), index=signals.index)
        values.iloc[1] = 2
        return values

    monkeypatch.setattr("agentic_trader.research.alpha.replay.alpha_scores", scores)
    definition = AlphaDefinition(
        "continuous",
        "Continuous",
        "close",
        semantics_version=3,
        clock=SessionClockPolicy(),
        timeframe="15m",
        normalization_window=2,
        eligible_symbols=("SPY",),
        data_feed="alpaca:iex",
        execution=AlphaExecutionPolicy(atr_window=2, swing_window=1, friction_per_side=0, trail_trigger_r=100),
    )
    plan = ReplayPlan("SPY", date(2024, 10, 15), date(2024, 11, 29), definition)
    db = SignalDatabase(db_url=request.getfixturevalue("postgres_test_db")) if backend == "postgres" else temp_db
    await db.init_db()
    repository = AlphaRepository(db.workflows)
    source = AlpacaSessionSource(AlpacaDataProvider(stock_client=broker.data_client, feed="iex"), broker.client)
    try:
        result = await AlphaReplayService(repository, source).run(
            plan, tmp_path / "run", environment={"fixture": True}, as_of=pd.Timestamp("2024-12-01", tz="UTC")
        )
        assert len(requests) == 2
        assert (await repository.get("family/all"))["trial_count"] == 1
        assert (await repository.snapshot()).generation == 0
        assert all(c[0] == "GET" for c in venue.calls)
        if mode == "failed_chunk":
            assert result["status"] == "failed"
            assert len(result["acquisition"]) == 2 and result["acquisition"][-1]["error_type"] == "APIError"
            assert not (tmp_path / "run" / "observations.json").exists()
        else:
            assert result["status"] == "completed"
            assert result["sample_length"] == 600 and result["open_position"]
            assert len(result["entries"]) == 1 and not result["trades"]
            expected = "2024-10-15 14:01Z" if mode == "held" else "2024-11-29 14:30Z"
            assert pd.Timestamp(result["entries"][0]["timestamp"]) == pd.Timestamp(expected)
            assert sum(e["kind"] == "order_created" for e in result["events"]) == 1
            observations = json.loads((tmp_path / "run" / "observations.json").read_text())
            assert len(observations["attrs"]["acquisition"]) == 2
    finally:
        await db.engine.dispose()
