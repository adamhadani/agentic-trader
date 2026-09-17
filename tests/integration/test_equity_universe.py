"""Real SDK/TCP metadata capture and replay on SQLite and disposable PostgreSQL."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.data.equity_metadata import EquityMetadataSource
from agentic_trader.research.alpha.equity_universe import EquityUniversePlan, candidate_symbols
from agentic_trader.research.alpha.universe_workflow import EquityUniverseService
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def universe_repository(request, temp_db):
    db = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await db.init_db()
    try:
        yield AlphaRepository(db.workflows)
    finally:
        await db.engine.dispose()


@pytest.fixture
def metadata_http(alpaca_http, monkeypatch):
    venue, broker = alpaca_http
    base = str(broker.client._base_url)
    monkeypatch.setattr("agentic_trader.data.equity_metadata.NASDAQ_DIRECTORY_ROOT", base)
    assets = [
        {
            "id": str(UUID(int=i + 1)),
            "symbol": symbol,
            "class": "us_equity",
            "exchange": "NASDAQ",
            "name": "Deliberately uninformative",
            "status": "active",
            "tradable": True,
            "marginable": True,
            "shortable": True,
            "easy_to_borrow": True,
            "fractionable": True,
        }
        for i, symbol in enumerate(("AAA", "BBB", "ETF"))
    ]
    footer = f"File Creation Time: {datetime.now(UTC):%m%d%Y}16:00|||||||\n"
    directories = {
        "nasdaqlisted": "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\nAAA|AAA|Q|N|N|100|N|N\nBBB|BBB|Q|N|N|100|N|N\nETF|ETF|Q|N|N|100|Y|N\n"
        + footer,
        "otherlisted": "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nCCC|CCC|N|CCC|N|100|N|CCC\n"
        + footer,
    }

    # The existing SDK fixture speaks JSON; use real HTTP for SDK and HTTPX's transport for plain text directories.
    def metadata(request):
        name = request.url.path.removeprefix("/").removesuffix(".txt")
        return httpx.Response(200, text=directories[name])

    with httpx.Client(transport=httpx.MockTransport(metadata), timeout=1) as http:
        yield venue, broker, assets, directories, http


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("fault", [None, "sdk_parse", "directory_parse", "asset_denied"])
async def test_snapshot_preserves_failures_charges_before_io_and_replays(
    metadata_http, universe_repository, tmp_path, fault
):
    venue, broker, assets, directories, http = metadata_http
    if fault == "sdk_parse":
        assets[0]["id"] = "invalid-uuid"
    if fault == "directory_parse":
        directories["otherlisted"] = "broken\n"

    def response(method, path, query, body):
        if path == "/v2/assets":
            assert method == "GET" and query == {"asset_class": ["us_equity"]}
            if fault == "asset_denied":
                return 403, {"message": "fixture denial", "code": 40310000}
            return 200, assets
        return None

    venue.override = response
    repo = universe_repository
    plan = EquityUniversePlan("fixture", target_size=2)
    output = tmp_path / "cohort"
    service = EquityUniverseService(repo, EquityMetadataSource(broker.client, http))
    result = await service.run(plan, output, environment={"fixture": True})
    assert result["status"] == ("completed" if fault is None else "failed")
    assert (await repo.get("family/all"))["trial_count"] == 1
    assert not result["price_reads"] and not result["authorizes_promotion"]
    assert (await repo.snapshot()).generation == 0
    inputs = json.loads((output / "inputs.json").read_text())
    if fault in {"sdk_parse", "directory_parse"}:
        assert json.loads((output / "alpaca-assets.json").read_text())["response"] == assets
    if fault is None:
        snapshot = json.loads((output / "snapshot.json").read_text())
        assert set(candidate_symbols(snapshot, at=datetime.now(UTC))) == {"AAA", "BBB"}
        assert snapshot["selected_count"] == 2 and snapshot["target_met"]
        assert len(inputs["sources"]) == 3
    else:
        assert inputs["receipts"][-1]["error_type"]
        assert not (output / "snapshot.json").exists()
    assert all(c[0] == "GET" and c[1] == "/v2/assets" for c in venue.calls)
    assert all(r["requested_at"] <= r["received_at"] for r in inputs["receipts"])
    async with repo.store.db.session_factory() as session:
        events = (await session.scalars(select(DomainEventRecord).order_by(DomainEventRecord.id))).all()
    reservation = next(e for e in events if json.loads(e.payload)["key"].startswith("research/reservation/"))
    recorded = pd.Timestamp(reservation.recorded_at)
    if recorded.tzinfo is None:
        recorded = recorded.tz_localize("UTC")
    assert recorded <= pd.Timestamp(inputs["receipts"][0]["requested_at"])
    assert not any(json.loads(e.payload)["key"].startswith("external-observation/") for e in events)
    evidence = await repo.get(f"diagnostic/{result['run_id']}")
    assert evidence["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    for reference in inputs["sources"].values():
        assert hashlib.sha256(Path(reference["artifact"]).read_bytes()).hexdigest() == reference["sha256"]
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == evidence
    with pytest.raises(FileExistsError):
        await service.run(plan, output, environment={})
    assert (await repo.get("family/all"))["trial_count"] == 1
