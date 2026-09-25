import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.earnings_history import CalendarAcquisition
from agentic_trader.research.apriori.pead_events import MarketData, decision_at
from agentic_trader.research.apriori.pead_study import (
    PeadInputs,
    decision_price,
    evaluate_leg,
    execute_pead_study,
    label_events,
    levels_for,
)


ENTRY_PATH = Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json"
LOADED = load_pead_entry(ENTRY_PATH)
ENTRY = LOADED.entry


def hourly_bars(day: date, prices: list[float]) -> pd.DataFrame:
    """Regular-session hourly bars 09:00..15:00 New York for one day, one price each."""
    index = [datetime.combine(day, time(9 + k), tzinfo=ET_TZ).astimezone(UTC) for k in range(len(prices))]
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p, "Volume": 1.0}, index=pd.DatetimeIndex(index))


def test_decision_price_is_the_close_of_the_last_bar_ending_by_the_decision():
    day = date(2021, 4, 5)
    bars = hourly_bars(day, [100.0, 101.0, 102.0])
    assert decision_price(bars, decision_at(day, time(10, 35))) == 100.0  # the 09:00 bar ends 10:00
    assert decision_price(bars.iloc[1:], decision_at(day, time(10, 35))) is None  # no bar ending by 10:35
    earlier = hourly_bars(day - timedelta(days=1), [99.0] * 7)
    assert decision_price(earlier, decision_at(day, time(10, 35))) is None  # never a prior day's price


def test_levels_follow_atr_multiples_and_reject_degenerate_brackets():
    long = levels_for("LONG", 100.0, 2.0, ENTRY)
    assert (long.stop, long.target) == (96.0, 112.0)
    short = levels_for("SHORT", 100.0, 2.0, ENTRY)
    assert (short.stop, short.target) == (104.0, 88.0)
    assert levels_for("LONG", 3.0, 2.0, ENTRY) is None  # stop at or below zero


def _labels(values_by_session: dict[date, list[tuple[str, bool, float]]]) -> pd.DataFrame:
    rows = []
    for session, items in values_by_session.items():
        for direction, is_leg, r_cost in items:
            rows.append({"session": session, "direction": direction, "is_leg": is_leg, "r_cost": r_cost})
    return pd.DataFrame(rows)


def _sessions(n: int, start=date(2020, 1, 1)) -> list[date]:
    return [start + timedelta(days=k) for k in range(n)]


def test_evaluate_leg_passes_a_clear_positive_edge_over_control():
    rng = np.random.default_rng(0)
    data = {}
    for session in _sessions(400, date(2022, 6, 1)):
        data[session] = [("LONG", True, 0.4 + 0.1 * rng.standard_normal())] + [
            ("LONG", False, -0.1 + 0.1 * rng.standard_normal()) for _ in range(3)
        ]
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert result["p1"]["holds"] and result["p2"]["holds"] and result["p3"]["holds"] and result["p4"]["holds"]
    assert result["passes"] and result["p1"]["n"] == 400 and result["p4"]["n_recent"] >= 100


def test_evaluate_leg_fails_p3_when_recent_decisions_lose():
    data = {}
    for session in _sessions(700, date(2021, 6, 1)):
        edge = 0.5 if session < date(2023, 1, 1) else -0.2
        data[session] = [("LONG", True, edge), ("LONG", False, -0.3)]
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert result["p1"]["holds"] and not result["p3"]["holds"] and not result["passes"]


def test_evaluate_leg_fails_p3_on_an_outlier_driven_mean():
    data = {s: [("SHORT", True, -0.2), ("SHORT", False, -0.3)] for s in _sessions(500, date(2021, 1, 1))}
    first = next(iter(data))
    data[first] = [("SHORT", True, 5000.0), ("SHORT", False, -0.3)]
    result = evaluate_leg(_labels(data), "SHORT", ENTRY)
    assert result["p3"]["trimmed_mean"] < 0 and not result["p3"]["holds"] and not result["passes"]


def test_evaluate_leg_fails_p4_on_too_few_events():
    data = {s: [("LONG", True, 1.0), ("LONG", False, 0.0)] for s in _sessions(50, date(2023, 2, 1))}
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert not result["p4"]["holds"] and not result["passes"]


def _market_and_hourly():
    day = date(2021, 4, 5)
    # 30 flat sessions: neither bracket level is touched, so both directions time out at 20 sessions.
    frames = [hourly_bars(d.date(), [100.0] * 7) for d in pd.bdate_range(day, periods=30)]
    return day, {"XYZ": pd.concat(frames)}


def test_label_events_labels_each_event_both_ways_and_flags_leg_rows():
    day, hourly = _market_and_hourly()
    events = pd.DataFrame(
        [
            {
                "symbol": "XYZ",
                "report_date": day - timedelta(days=2),
                "session": day,
                "decision_at": decision_at(day, time(10, 35)),
                "z": 2.0,
                "surprise_pct": 10.0,
                "surprise_pct_reported": 10.0,
                "atr": 1.0,
                "median_dollar_volume": 1e8,
                "reference": 1e7,
                "leg": "LONG",
                "surprise_long": True,
                "surprise_short": False,
                "reaction_long": True,
                "reaction_short": False,
            },
            {
                "symbol": "GONE",
                "report_date": day - timedelta(days=2),
                "session": day,
                "decision_at": decision_at(day, time(10, 35)),
                "z": 0.1,
                "surprise_pct": None,
                "surprise_pct_reported": None,
                "atr": 1.0,
                "median_dollar_volume": 1e8,
                "reference": 1e7,
                "leg": None,
                "surprise_long": False,
                "surprise_short": False,
                "reaction_long": False,
                "reaction_short": False,
            },
        ]
    )
    labels, counts = label_events(events, hourly, ENTRY)
    assert counts["no_hourly_bars"] == 1
    assert sorted(zip(labels["direction"], labels["is_leg"], strict=True)) == [("LONG", True), ("SHORT", False)]
    assert {"r_cost", "r_cost_0bps", "r_cost_5bps", "hold_secondary_r_cost", "hit"} <= set(labels.columns)
    assert set(labels["hit"]) == {"timeout"} and labels["r"].abs().max() < 0.51  # entry 100 vs decision price 100


async def test_execute_writes_manifest_before_building_and_records_failure(tmp_path):
    output = tmp_path / "run"

    async def build():
        assert (output / "manifest.json").exists() and (output / "protocol.json").exists()
        raise RuntimeError("provider down")

    result = await execute_pead_study(LOADED, output, build=build, environment={"revision": "abc"})
    assert result == {"status": "failed", "error": "RuntimeError: provider down"}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["sha256"] == LOADED.sha256 and manifest["authorizes_promotion"] is False
    assert json.loads((output / "result.json").read_text())["status"] == "failed"
    with pytest.raises(FileExistsError):
        await execute_pead_study(LOADED, output, build=build, environment={})


async def test_execute_fails_closed_on_a_gappy_calendar(tmp_path):
    acquisition = CalendarAcquisition(
        rows=(), requested_dates=100, fetched_dates=97, reused_dates=0, failed_dates=tuple(_sessions(3))
    )

    async def build():
        return PeadInputs(
            acquisition=acquisition,
            rows=(),
            duplicates=0,
            market=MarketData((), {}, ()),
            hourly={},
            bar_failures={},
        )

    result = await execute_pead_study(LOADED, tmp_path / "run", build=build, environment={})
    assert result["status"] == "failed" and "calendar" in result["error"]
