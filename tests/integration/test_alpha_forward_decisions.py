"""Real SDK HTTP -> receipt-aware scoring -> journal/replay, across independent clients."""

import asyncio
from dataclasses import replace

import pandas as pd
import pytest

from agentic_trader.config import AlphaPipelineConfig, MarketDataEvidenceConfig, SessionDecisionConfig
from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha.decisions import SessionDecisionService
from agentic_trader.research.alpha.evidence import load_forward_evidence
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def test_concurrent_real_sdk_session_decisions_consume_once_and_replay(
    alpaca_http, temp_db, tmp_path, request, backend
):
    venue, broker = alpaca_http
    now = [pd.Timestamp("2024-11-27 19:59Z")]
    revision = [0.0]

    def response(method, path, query, body):
        if path == "/v2/calendar":
            return 200, [{"date": "2024-11-27", "open": "09:30", "close": "16:00"}]
        if path == "/v2/stocks/bars":
            return 200, {
                "bars": {
                    "SPY": [
                        {
                            "t": t.isoformat(),
                            "o": 100 + i * i / 100000,
                            "h": 110,
                            "l": 99,
                            "c": 100 + i * i / 100000 + revision[0],
                            "v": 1000,
                            "n": 10,
                            "vw": 100,
                        }
                        for i, t in enumerate(
                            pd.date_range("2024-11-27 14:30Z", "2024-11-27 20:00Z", freq="min", inclusive="left")
                        )
                    ]
                },
                "next_page_token": None,
            }
        return None

    venue.override = response
    if backend == "postgres":
        url = request.getfixturevalue("postgres_test_db")
        databases = [SignalDatabase(db_url=url) for _ in range(2)]
    else:
        await temp_db.init_db()
        databases = [temp_db, temp_db]
    policy = SessionDecisionConfig(enabled=True, feed="alpaca:iex", max_candidates=1)
    repos = [AlphaRepository(db.workflows, policy=AlphaPipelineConfig(decisions=policy)) for db in databases]
    definition = AlphaDefinition(
        "sdk-forward",
        "SDK forward",
        "delta(close,3)",
        timeframe="15m",
        eligible_symbols=("SPY",),
        normalization_window=10,
        data_feed="alpaca:iex",
        semantics_version=3,
        clock=SessionClockPolicy(),
    )
    source = AlpacaSessionSource(
        AlpacaDataProvider(
            stock_client=broker.data_client,
            feed="iex",
            evidence=BarEvidenceStore(tmp_path / "raw", MarketDataEvidenceConfig()),
        ),
        broker.client,
    )
    services = [
        SessionDecisionService(
            repo,
            source,
            policy,
            directory=tmp_path / str(i),
            clock=lambda: now[0],
            runtime={"run_id": str(i)},
        )
        for i, repo in enumerate(repos)
    ]
    try:
        await repos[0].register(definition, actor="fixture")
        await repos[0].set_shadow(definition.version_id, actor="fixture", expected_generation=0)
        other = replace(definition, alpha_id="sip-control", data_feed="alpaca:sip")
        await repos[0].register(other, actor="fixture")
        await repos[0].set_shadow(other.version_id, actor="fixture", expected_generation=1)
        await asyncio.gather(*(service.run_once() for service in services))
        now[0] = pd.Timestamp("2024-11-27 20:01Z")
        results = await asyncio.gather(*(service.run_once() for service in services))
        (first,) = [result for batch in results for result in batch]
        assert first["status"] == "scored" and first["artifact_hash"]
        assert not first["forecast"]["valid"]
        requests = [c for c in venue.calls if c[1] == "/v2/stocks/bars"]
        assert len(requests) == 1
        assert requests[0][2]["feed"] == ["iex"] and requests[0][2]["timeframe"] == ["1Min"]
        assert requests[0][2]["adjustment"] == ["raw"]
        assert all(method == "GET" for method, *_ in venue.calls)
        _, before = await load_forward_evidence(repos[0], now=now[0])
        own = next(r for r in before["candidates"] if r["feed"] == policy.feed)
        foreign = next(r for r in before["candidates"] if r["feed"] != policy.feed)
        assert own["counts"]["scored"] == 1 and own["receipt_lag_seconds"]["count"] == 1
        assert not own["coverage_warnings"]
        assert "feed_not_configured" in foreign["coverage_warnings"]
        assert foreign["enrolled_at"] is None
        revision[0] = 0.1
        assert await services[1].run_once() == []
        await repos[1].rebuild()
        _, after = await load_forward_evidence(repos[1], now=now[0])
        assert after == before
        assert await repos[0].get(f"session-decision/{first['decision_id']}") == first
        assert await repos[0].get(f"shadow/{definition.version_id}") is None
        assert not (await repos[0].status())["pending_session_decisions"]
    finally:
        for db in databases:
            await db.engine.dispose()
