"""Real SDK/TCP and PostgreSQL evidence boundaries for the forward observer."""

import asyncio

import pandas as pd
import pytest

from agentic_trader.config import SessionObservationConfig
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.research.alpha.observation import SessionObservationService
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("missing", [False, True])
async def test_actual_sdk_forward_receipts_and_revisions(alpaca_http, temp_db, tmp_path, missing):
    venue, broker = alpaca_http
    revision = [0]
    now = [pd.Timestamp("2024-11-29 14:45:05Z")]

    def response(method, path, query, body):
        if path == "/v2/calendar":
            return 200, [{"date": "2024-11-29", "open": "09:30", "close": "13:00"}]
        if path == "/v2/stocks/bars":
            index = pd.date_range("2024-11-29 14:30Z", periods=15, freq="min")
            if missing:
                index = index[1:]
            return 200, {
                "bars": {
                    "SPY": [
                        {
                            "t": t.isoformat(),
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100 + revision[0],
                            "v": 1000,
                            "n": 10,
                            "vw": 100,
                        }
                        for t in index
                    ]
                },
                "next_page_token": None,
            }
        return None

    venue.override = response
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    source = AlpacaSessionSource(AlpacaDataProvider(stock_client=broker.data_client, feed="iex"), broker.client)
    observer = SessionObservationService(
        repo,
        source,
        SessionObservationConfig(enabled=True),
        feed="alpaca:iex",
        directory=tmp_path / "forward",
        clock=lambda: now[0],
        runtime={"run_id": "sdk_fixture"},
    )
    first = (await observer.run_once())[0]
    revision[0] = 0.5
    now[0] += pd.Timedelta(seconds=30)
    second = (await observer.run_once())[0]
    assert first["status"] == second["status"] == ("unavailable" if missing else "complete")
    assert first["artifact_hash"] != second["artifact_hash"]
    assert first["coverage"]["missing_minutes"] == int(missing)
    assert all(method == "GET" for method, *_ in venue.calls)
    requests = [c for c in venue.calls if c[1] == "/v2/stocks/bars"]
    assert len(requests) == 2
    assert all(
        c[2]["feed"] == ["iex"] and c[2]["adjustment"] == ["raw"] and c[2]["timeframe"] == ["1Min"] for c in requests
    )
    if not missing:
        bar = await repo.get(first["bar_key"])
        assert bar["revision_count"] == 1
        assert bar["first_observed_at"] == first["received_at"]
    assert (await repo.get("family/all"))["trial_count"] == 0
    assert (await repo.snapshot()).generation == 0
    await temp_db.engine.dispose()


@pytest.mark.postgres
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
async def test_postgres_concurrent_observation_receipts_preserve_first_and_latest(postgres_test_db):
    databases = [SignalDatabase(db_url=postgres_test_db) for _ in range(2)]
    repos = [AlphaRepository(db.workflows) for db in databases]
    captures = [
        {
            "observation_id": str(i),
            "symbol": "SPY",
            "status": "capturing",
            "started_at": f"2024-11-29T14:45:{i}5+00:00",
            "bar_key": "observed-bar/fixture",
            "authorizes_promotion": False,
        }
        for i in range(2)
    ]
    try:
        await asyncio.gather(*(r.begin_observation(str(i), captures[i]) for i, r in enumerate(repos)))
        completed = [
            {**c, "status": "complete", "content_hash": str(i), "received_at": f"2024-11-29T14:45:{i}6+00:00"}
            for i, c in enumerate(captures)
        ]
        # Complete in reverse order, as slow requests/journal commits can do.
        await repos[1].finish_observation("1", completed[1])
        await repos[0].finish_observation("0", completed[0])
        bar = await repos[0].get("observed-bar/fixture")
        assert bar["first_observed_at"] == completed[0]["received_at"]
        assert bar["last_observed_at"] == completed[1]["received_at"]
        assert bar["content_hash"] == "1" and bar["revision_count"] == 1
        assert (await repos[0].get("observation/latest"))["observation_id"] == "1"
        with pytest.raises(ValueError, match="pending"):
            await repos[1].finish_observation("0", completed[0])
        await repos[1].rebuild()
        assert await repos[0].get("observed-bar/fixture") == bar
    finally:
        for db in databases:
            await db.engine.dispose()
