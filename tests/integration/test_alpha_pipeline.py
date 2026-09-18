"""Real PostgreSQL transactions and real Alpaca SDK HTTP parsing for alpha evidence."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from agentic_trader.broker.base import OrderRequest
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import AlphaPromotionService
from agentic_trader.research.alpha.validation import DatasetManifest, ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import AlphaProjectionRecord


@pytest.mark.postgres
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
async def test_alpha_transactions_replay_and_concurrent_holdout_claim(postgres_test_db):
    first = SignalDatabase(db_url=postgres_test_db)
    second = SignalDatabase(db_url=postgres_test_db)
    repositories = [AlphaRepository(db.workflows) for db in (first, second)]
    definition = AlphaDefinition(
        "alpha_pg", "Postgres", "delta(close,3)", timeframe="1d", eligible_symbols=("SPY",), data_feed="alpaca:sip"
    )
    try:
        await repositories[0].register(definition, actor="integration")
        await asyncio.gather(
            *(
                repository.reserve_run("concurrent", symbol="SPY", timeframe="1d", trials=1)
                for repository in repositories
            )
        )
        assert (await repositories[0].get("family/all"))["trial_count"] == 1
        outcomes = await asyncio.gather(
            *(
                repository.set_shadow(definition.version_id, actor="integration", expected_generation=0)
                for repository in repositories
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
        run = {
            "policy": asdict(ValidationPolicy()),
            "trial_count": 1,
            "trials": [{"definition": definition.to_dict(), "status": "evaluated"}],
            "holdout_start": 600,
        }
        manifest = {
            "symbol": "SPY",
            "timeframe": "1d",
            "feed": "alpaca:sip",
            "adjustment": "raw",
            "holdout_start": "2025-01-01",
            "end": "2026-01-01",
            "content_hash": "test",
        }
        await repositories[0].record_run("concurrent", run, manifest)
        outcomes = await asyncio.gather(
            *(repository.begin_holdout("concurrent", definition.version_id) for repository in repositories),
            return_exceptions=True,
        )
        assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
        expected = await repositories[0].snapshot()
        async with first.session_factory() as session, session.begin():
            await session.execute(delete(AlphaProjectionRecord))
        await repositories[1].rebuild()
        assert await repositories[0].snapshot() == expected
        consumption = await repositories[0].get("consumption/concurrent")
        assert consumption["return_timeline"] == ValidationPolicy().return_timeline
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
def test_real_sdk_historical_pagination_retains_feed_and_adjustment(alpaca_http):
    venue, broker = alpaca_http

    def response(method, path, query, body):
        if path != "/v2/stocks/bars":
            return None
        page = query.get("page_token", [None])[0]
        day = "2026-09-14" if page is None else "2026-09-15"
        return 200, {
            "bars": {
                "SPY": [
                    {"t": day + "T04:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000, "n": 10, "vw": 100.5}
                ]
            },
            "next_page_token": "next" if page is None else None,
        }

    venue.override = response
    provider = AlpacaDataProvider(stock_client=broker.data_client, feed="iex")
    bars = provider.fetch_bars(
        "SPY", "1d", start=datetime(2026, 9, 14, tzinfo=UTC), end=datetime(2026, 9, 16, tzinfo=UTC)
    )
    assert len(bars) == 2
    assert bars.attrs == {
        "feed": "alpaca:iex",
        "adjustment": "raw",
        "timeframe": "1d",
        "source_quality": {
            "version": "bar_source_quality_v1",
            "raw_rows": 2,
            "parsed_rows": 2,
            "normalized_rows": 2,
            "sdk_omitted_rows": 0,
            "normalization_dropped_rows": 0,
        },
    }
    calls = [call for call in venue.calls if call[1] == "/v2/stocks/bars"]
    assert len(calls) == 2
    assert all(call[2]["feed"] == ["iex"] and call[2]["adjustment"] == ["raw"] for call in calls)


async def test_full_discovery_qualification_replay_stays_fail_closed(temp_db):
    await temp_db.init_db()
    repository = AlphaRepository(temp_db.workflows)
    rng = np.random.default_rng(5)
    close = 100 * np.exp(rng.normal(0, 0.01, 600).cumsum())
    bars = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1000, 10000, 600),
        },
        index=pd.date_range("2020-01-01", periods=600, tz="UTC"),
    )
    bars.attrs.update(timeframe="1d", feed="alpaca:sip", adjustment="raw")
    miner = AlphaMiner(seed=4)
    miner.mine(bars, iterations=2, include_catalog=False, timeframe="1d", symbol="SPY")
    definition = AlphaDefinition.from_dict(miner.last_run["trials"][0]["definition"])
    manifest = DatasetManifest.from_frame(
        bars, symbol="SPY", timeframe="1d", feed="alpaca:sip", adjustment="raw", universe_version="test"
    ).to_dict()
    manifest.update(holdout_start=str(bars.index[480]), incumbents=[])
    await repository.register(definition, actor="integration")
    await repository.record_run("full", miner.last_run, manifest)
    result = await AlphaPromotionService(repository).qualify("full", definition.version_id, bars)
    assert not result["qualified"]
    assert result["reasons"]
    assert result["policy"] == asdict(ValidationPolicy())
    assert result["holdout"]["sample_length"] == 118
    assert result["holdout"]["feature_coverage"]["bars"] == 118
    with pytest.raises(ValueError, match="qualification"):
        await repository.promote(definition.version_id, actor="integration", expected_generation=0)
    await repository.rebuild()
    decision = await repository.get(f"qualification/{definition.version_id}")
    assert decision["reasons"] == result["reasons"]
    with pytest.raises(ValueError, match="consumed"):
        await AlphaPromotionService(repository).qualify("full", definition.version_id, bars)
    await temp_db.engine.dispose()


@pytest.mark.postgres
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("demote_first", [True, False])
@pytest.mark.parametrize("change", ["demotion", "obsolete_qualification"])
async def test_independent_registry_and_submission_clients_have_a_commit_boundary(
    postgres_test_db, app_config, demote_first, change
):
    first, second = SignalDatabase(db_url=postgres_test_db), SignalDatabase(db_url=postgres_test_db)
    repository, operator = AlphaRepository(first.workflows), AlphaRepository(second.workflows)
    definition = AlphaDefinition(
        "alpha_fenced", "Fenced", "close", timeframe="1d", eligible_symbols=("SPY",), data_feed="alpaca:sip"
    )
    try:
        await repository.register(definition, actor="fixture")
        # Fixture grants registry state only; statistical qualification has its
        # own public-service integration test above.
        async with first.session_factory() as session, session.begin():
            await first.workflows.lock(session, resource="alpha")
            await repository._append(
                session,
                "registry",
                {"generation": 1, "active": [definition.version_id], "shadow": []},
                EventKind.ALPHA_REGISTRY,
                "fixture",
            )
            await repository._append(
                session,
                f"qualification/{definition.version_id}",
                {"policy": asdict(ValidationPolicy())},
                EventKind.ALPHA_RESEARCH,
                "fixture",
            )

        async def invalidate():
            if change == "demotion":
                await operator.demote(definition.version_id, actor="test", expected_generation=1)
            else:
                async with second.session_factory() as session, session.begin():
                    await second.workflows.lock(session, resource="alpha")
                    await operator._append(
                        session,
                        f"qualification/{definition.version_id}",
                        {"policy": {}},
                        EventKind.ALPHA_RESEARCH,
                        "fixture",
                    )

        sid = await first.record_signal(
            "SPY",
            definition.alpha_id,
            "LONG",
            100,
            98,
            104,
            2,
            asset_class="EQUITY",
            quantity=1,
            timeframe="1d",
            alpha_version=definition.version_id,
            alpha_policy=definition.execution.to_dict(),
        )
        request = OrderRequest(
            signal_id=sid,
            symbol="SPY",
            asset_class="EQUITY",
            quantity=1,
            entry_price=100,
            stop_loss=98,
            take_profit=104,
        )
        item, reason = await first.workflows.enqueue_entry(request, app_config)
        assert item, reason
        claim = await first.workflows.claim_entry(lease_seconds=60)
        if demote_first:
            await invalidate()
        assert (await first.workflows.begin_submission(claim, app_config) is None) == (not demote_first)
        if not demote_first:
            await invalidate()
            assert (await second.workflows.get_work(item.id)).status == WorkStatus.SUBMITTING
    finally:
        await first.engine.dispose()
        await second.engine.dispose()
