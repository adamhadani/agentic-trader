from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.research.alpha.equity_universe import EquityUniversePlan, build_snapshot, document_hash
from agentic_trader.research.alpha.mining_universe import resolve_mining_universe


def _snapshot(tmp_path):
    observed = datetime.now(UTC) - timedelta(minutes=1)
    rows = []
    directory_rows = []
    for index, symbol in enumerate(("AAA", "BBB", "CCC"), start=1):
        rows.append(
            {
                "id": f"00000000-0000-0000-0000-{index:012d}",
                "symbol": symbol,
                "class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
            }
        )
        directory_rows.append(
            {"Symbol": symbol, "ETF": "N", "Test Issue": "N", "NextShares": "N", "Financial Status": "N"}
        )
    directory = {"name": "nasdaqlisted", "file_date": observed.date().isoformat(), "rows": directory_rows}
    other = {"name": "otherlisted", "file_date": observed.date().isoformat(), "rows": []}
    snapshot = build_snapshot(
        EquityUniversePlan("fixture", target_size=3), rows, (directory, other), observed_at=observed
    )
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot))
    return path, snapshot


def test_snapshot_universe_is_capped_and_keeps_snapshot_identity(tmp_path):
    path, snapshot = _snapshot(tmp_path)

    result = resolve_mining_universe(universe="snapshot", snapshot_path=path, max_symbols=2)

    assert result.symbols == tuple(row["symbol"] for row in snapshot["selected"][:2])
    assert result.version == f"snapshot:{snapshot['snapshot_id']}:cap:2"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"universe": "snapshot"}, "requires"),
        ({"universe": "etf32", "max_symbols": 500}, "exceeds"),
    ],
)
def test_mining_universe_rejects_ambiguous_or_unbounded_selection(kwargs, message):
    with pytest.raises(ValueError, match=message):
        resolve_mining_universe(**kwargs)


def test_explicit_universe_is_canonical_and_provenance_stable():
    result = resolve_mining_universe(universe="explicit", symbols="qqq, spy, QQQ")

    assert result.symbols == ("QQQ", "SPY")
    assert result.version == "explicit:QQQ,SPY"


def test_snapshot_universe_rejects_explicit_symbols(tmp_path):
    path, _ = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="combined"):
        resolve_mining_universe(universe="snapshot", symbols="AAA", snapshot_path=path)


def test_screen_universe_preserves_screen_identity_and_order(tmp_path):
    screen = {
        "version": "equity_liquidity_screen_v1",
        "status": "completed",
        "selection_available": True,
        "authorizes_promotion": False,
        "point_in_time_historical_membership": False,
        "common_stock_classification": False,
        "market_capacity_estimate": False,
        "selected_count": 2,
        "selected": [
            {"asset_id": "00000000-0000-0000-0000-000000000001", "symbol": "AAA"},
            {"asset_id": "00000000-0000-0000-0000-000000000002", "symbol": "BBB"},
        ],
    }
    path = tmp_path / "screen.json"
    path.write_text(json.dumps(screen))

    result = resolve_mining_universe(universe="screen", snapshot_path=path, max_symbols=2)

    assert result.name == "screened_equity_cohort"
    assert result.symbols == ("AAA", "BBB")
    assert result.version == f"screen:{document_hash(screen)}:cap:2"


def test_screen_universe_rejects_incomplete_result(tmp_path):
    path = tmp_path / "screen.json"
    path.write_text(json.dumps({"version": "equity_liquidity_screen_v1", "selected": []}))

    with pytest.raises(ValueError, match="incomplete"):
        resolve_mining_universe(universe="screen", snapshot_path=path)
