"""Prospective cohort semantics: no inferred common-stock or historical eligibility."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.data.symbol_directory import parse_directory
from agentic_trader.research.alpha.equity_universe import (
    EquityUniversePlan,
    build_snapshot,
    candidate_symbols,
)


@pytest.fixture
def universe_metadata():
    assets = [
        {
            "id": str(UUID(int=i + 1)),
            "symbol": symbol,
            "class": "us_equity",
            "exchange": "NASDAQ",
            "status": "active",
            "tradable": True,
            "shortable": True,
            "easy_to_borrow": True,
        }
        for i, symbol in enumerate(("AAA", "BBB", "ETF", "TEST", "MISSING", "INACTIVE"))
    ]
    assets[-1]["status"] = "inactive"
    text = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    text += "AAA|An arbitrary name|Q|N|N|100|N|N\nBBB|Another arbitrary name|Q|N|N|100|N|N\n"
    text += "ETF|Uninformative name|Q|N|N|100|Y|N\nTEST|Test|Q|Y|N|100|N|N\n"
    text += "INACTIVE|Inactive|Q|N|N|100|N|N\nFile Creation Time: 0917202616:00|||||||\n"
    return assets, parse_directory(text, "nasdaqlisted")


def test_snapshot_stable_hash_sampling_unknown_subtypes_and_all_exclusions(universe_metadata):
    assets, directory = universe_metadata
    now = datetime(2026, 9, 17, 21, tzinfo=UTC)
    plan = EquityUniversePlan("fixture", target_size=1)
    result = build_snapshot(plan, assets, (directory,), observed_at=now)
    reversed_result = build_snapshot(plan, list(reversed(assets)), (directory,), observed_at=now)
    assert result == reversed_result
    assert len(result["members"]) == 6 and len(result["selected"]) == 1
    by_symbol = {row["symbol"]: row for row in result["members"]}
    assert by_symbol["AAA"]["instrument_subtype"] == "unknown"
    assert by_symbol["BBB"]["classification"] == "listed_non_etf_equity_candidate"
    assert by_symbol["ETF"]["reasons"] == ["etf"]
    assert by_symbol["TEST"]["reasons"] == ["test_issue"]
    assert by_symbol["MISSING"]["reasons"] == ["missing_directory"]
    assert by_symbol["INACTIVE"]["reasons"] == ["inactive"]
    assert sum(row["selection"] == "candidate_cap" for row in result["members"]) == 1
    assert result["point_in_time_historical_membership"] is False
    assert candidate_symbols(result, at=now) == tuple(row["symbol"] for row in result["selected"])
    with pytest.raises(ValueError, match="prospective"):
        candidate_symbols(result, at=datetime(2026, 9, 16, tzinfo=UTC))
    result["selected"][0]["symbol"] = "FORGED"
    with pytest.raises(ValueError, match="identity"):
        candidate_symbols(result, at=now)


@pytest.mark.parametrize(
    "field,value",
    [("target_size", 0), ("target_size", 501), ("target_size", True), ("seed", ""), ("max_metadata_age_days", 0)],
)
def test_plan_validated_and_exact_identity(field, value):
    with pytest.raises(ValueError):
        replace(EquityUniversePlan("fixture"), **{field: value})
    plan = EquityUniversePlan("fixture")
    assert EquityUniversePlan.from_document(plan.document()) == plan
    with pytest.raises(ValueError):
        EquityUniversePlan.from_document({**plan.document(), "unknown": 1})


@pytest.mark.parametrize("fault", ["duplicate", "stale", "future", "unknown_flag"])
def test_directory_faults_fail_closed(universe_metadata, fault):
    assets, directory = universe_metadata
    if fault == "duplicate":
        assets.append(dict(assets[0]))
    elif fault == "stale":
        directory["file_date"] = "2020-01-01"
    elif fault == "future":
        directory["file_date"] = "2030-01-01"
    else:
        directory["rows"][0]["ETF"] = "?"
    with pytest.raises(ValueError):
        build_snapshot(
            EquityUniversePlan("fixture"), assets, (directory,), observed_at=datetime(2026, 9, 17, 21, tzinfo=UTC)
        )


def test_removals_and_renames_remain_evidence(universe_metadata):
    assets, directory = universe_metadata
    now = datetime(2026, 9, 17, 21, tzinfo=UTC)
    plan = EquityUniversePlan("fixture")
    old = build_snapshot(plan, assets, (directory,), observed_at=now)
    assets = [dict(a) for a in assets[:-1]]
    assets[0]["symbol"] = "NEW"
    result = build_snapshot(plan, assets, (directory,), observed_at=now, previous=old)
    assert result["changes"]["removed_asset_ids"] == [old["members"][-1]["asset_id"]]
    assert result["changes"]["changed"][0]["before"]["symbol"] == "AAA"
    assert result["changes"]["changed"][0]["after"]["symbol"] == "NEW"
    assert result["previous_snapshot_id"] == old["snapshot_id"]


def test_cli_uses_journal_without_price_or_notification_clients(universe_metadata, tmp_path, monkeypatch):
    assets, directory = universe_metadata
    directory["file_date"] = datetime.now(UTC).date().isoformat()
    other = {"name": "otherlisted", "file_date": directory["file_date"], "rows": []}
    source = SimpleNamespace(
        assets=lambda _: assets, directory=lambda name, _: directory if name == "nasdaqlisted" else other
    )
    client = SimpleNamespace(_session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("agentic_trader.cli.commands.alpha_universe.BoundedTradingClient", lambda *a, **k: client)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha_universe.EquityMetadataSource", lambda *a: source)
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps(EquityUniversePlan("fixture", target_size=2).document()))
    result = CliRunner().invoke(
        cli, ["alpha", "universe-snapshot", str(protocol), "--output", str(tmp_path / "cohort")]
    )
    assert result.exit_code == 0, result.output
    assert "2 selected" in result.output
    status = CliRunner().invoke(cli, ["alpha", "status"])
    state = json.loads(status.output)
    assert state["research_family"]["trial_count"] == 1
    assert state["generation"] == 0 and state["active"] == 0
