"""Exercise the actual campaign runner, SDK/TCP, artifacts and disposable journal."""

import json
from pathlib import Path

import pandas as pd
import pytest

from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from scripts.run_session_campaign import execute_campaign


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def test_campaign_freezes_shared_inputs_charges_all_attempts_and_replays(
    alpaca_http, temp_db, tmp_path, request, backend
):
    venue, broker = alpaca_http
    protocol = json.loads((Path(__file__).parents[2] / "config/research/etf-session-v1.json").read_text())
    protocol.update(
        symbols=["SPY"],
        windows=[["2024-11-27", "2024-11-29"]],
        hypotheses=protocol["hypotheses"][:1],
        cost_bps_per_side=[1.0, 5.0],
        budget=2,
        feed="alpaca:iex",
        normalization_window=10,
    )
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    index = pd.date_range("2024-11-27 14:30Z", periods=390, freq="min").append(
        pd.date_range("2024-11-29 14:30Z", periods=210, freq="min")
    )

    def response(method, endpoint, query, body):
        if endpoint == "/v2/calendar":
            return 200, [
                {"date": "2024-11-27", "open": "09:30", "close": "16:00"},
                {"date": "2024-11-29", "open": "09:30", "close": "13:00"},
            ]
        if endpoint == "/v2/stocks/bars":
            return 200, {
                "bars": {
                    "SPY": [
                        {
                            "t": t.isoformat(),
                            "o": 100 + i * i / 1000000,
                            "h": 101,
                            "l": 99,
                            "c": 100 + i * i / 1000000,
                            "v": 1000,
                            "n": 10,
                            "vw": 100,
                        }
                        for i, t in enumerate(index)
                    ]
                },
                "next_page_token": None,
            }
        return None

    venue.override = response
    if backend == "postgres":
        db = SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    else:
        db = temp_db
        await db.init_db()
    repo = AlphaRepository(db.workflows)
    source = AlpacaSessionSource(AlpacaDataProvider(stock_client=broker.data_client, feed="iex"), broker.client)
    output = tmp_path / "campaign"
    try:
        await execute_campaign(path, output, repo, source, environment={"test": True})
        result = json.loads((output / "result.json").read_text())
        assert len(result["rows"]) == 2 and not result["authorizes_promotion"]
        assert all(r["summary"]["status"] == "completed" for r in result["rows"])
        assert sum(c[1] == "/v2/stocks/bars" for c in venue.calls) == 1
        assert len(result["source_receipts"]) == 2
        assert all(c[0] == "GET" for c in venue.calls)
        hashes = [
            json.loads((p / "observations.json").read_text())["content_hash"] for p in sorted(output.glob("trial-*"))
        ]
        assert len(set(hashes)) == 1
        assert (await repo.get("family/all"))["trial_count"] == 2
        await repo.rebuild()
        for row in result["rows"]:
            assert (await repo.get(f"diagnostic/{row['run_id']}"))["status"] == "completed"
        assert (await repo.snapshot()).generation == 0
        with pytest.raises(FileExistsError):
            await execute_campaign(path, output, repo, source, environment={"test": True})
        assert (await repo.get("family/all"))["trial_count"] == 2
    finally:
        await db.engine.dispose()
