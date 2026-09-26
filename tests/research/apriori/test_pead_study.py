import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
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


def test_evaluate_leg_fails_p2_when_the_control_matches_the_leg():
    # No separate control rows: every direction row is a leg row with the same value, so
    # mean_diff == 0 everywhere -- P1 (an unconditional positive mean) still holds, but P2
    # (an edge over control) cannot.
    data = {s: [("LONG", True, 0.3)] for s in _sessions(400, date(2022, 6, 1))}
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert result["p1"]["holds"]
    assert not result["p2"]["holds"] and result["p2"]["mean_diff"] == 0.0
    assert not result["passes"]


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
    assert result == {"status": "failed", "error": "RuntimeError: provider down", "authorizes_promotion": False}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["sha256"] == LOADED.sha256 and manifest["authorizes_promotion"] is False
    assert json.loads((output / "result.json").read_text())["status"] == "failed"
    with pytest.raises(FileExistsError):
        await execute_pead_study(LOADED, output, build=build, environment={})


async def test_execute_fails_closed_on_a_gappy_calendar(tmp_path):
    acquisition = CalendarAcquisition(
        rows=(),
        days=tuple(_sessions(100)),
        fetched_dates=97,
        reused_dates=0,
        failed_dates=tuple(_sessions(3)),
        empty_dates=(),
    )

    async def build():
        return PeadInputs(
            acquisition=acquisition,
            rows=(),
            duplicates=0,
            market=MarketData((), {}, (), liquidity_daily={}),
            hourly={},
            bar_failures={},
            static_requested=(),
        )

    result = await execute_pead_study(LOADED, tmp_path / "run", build=build, environment={})
    assert result["status"] == "failed" and "calendar" in result["error"]


def _event(symbol: str, session: date, leg: str | None, atr: float = 1.0) -> dict:
    return {
        "symbol": symbol,
        "report_date": session - timedelta(days=2),
        "session": session,
        "decision_at": decision_at(session, time(10, 35)),
        "z": 2.0,
        "surprise_pct": 10.0,
        "surprise_pct_reported": 10.0,
        "atr": atr,
        "median_dollar_volume": 1e8,
        "reference": 1e7,
        "leg": leg,
        "surprise_long": leg == "LONG",
        "surprise_short": leg == "SHORT",
        "reaction_long": leg == "LONG",
        "reaction_short": leg == "SHORT",
    }


def test_label_events_counts_missing_labels_by_year_and_leg():
    day, hourly = _market_and_hourly()
    late = date(2022, 3, 1)
    hourly = dict(hourly, LATE=hourly_bars(late, [100.0] * 7))  # one session only: immature
    events = pd.DataFrame(
        [
            _event("GONE", day, "LONG"),  # no hourly bars
            _event("GONE", date(2022, 2, 1), None),  # no hourly bars, no leg
            _event("XYZ", day + timedelta(days=5), "SHORT"),  # a Saturday: no bar on its decision date
            _event("XYZ", day, "SHORT", atr=20.0),  # SHORT target below zero: degenerate, the leg's side
            _event("XYZ", day, "LONG", atr=20.0),  # degenerate SHORT side only: not a LONG-leg miss
            _event("LATE", late, "LONG"),  # both directions immature
        ]
    )
    _, counts = label_events(events, hourly, ENTRY)
    assert counts["no_hourly_bars"] == 2 and counts["no_decision_price"] == 1
    assert counts["degenerate_levels"] == 2 and counts["immature"] == 2
    assert counts["by_year"] == {
        "no_hourly_bars": {2021: 1, 2022: 1},
        "no_decision_price": {2021: 1},
        "degenerate_levels": {2021: 2},
        "immature": {2022: 2},
    }
    # A per-direction miss counts against a leg only when it is the leg's own direction.
    assert counts["legs"] == {
        "no_hourly_bars": {"LONG": 1, "SHORT": 0},
        "no_decision_price": {"LONG": 0, "SHORT": 1},
        "degenerate_levels": {"LONG": 0, "SHORT": 1},
        "immature": {"LONG": 1, "SHORT": 0},
    }


# --- Executor end to end on synthetic inputs ------------------------------------------------

_HOLIDAY = date(2021, 4, 2)


def _daily(days, closes, volume=2_000_000.0):
    index = pd.DatetimeIndex([datetime.combine(d, time(5, 0), tzinfo=UTC) for d in days])
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": closes, "High": closes + 0.5, "Low": closes - 0.5, "Close": closes, "Volume": volume},
        index=index,
    )


def _synthetic_inputs(*, empty_dates=(), static_extra=()) -> PeadInputs:
    weekdays = [d.date() for d in pd.bdate_range("2021-03-01", periods=61)]
    days = [d for d in weekdays if d != _HOLIDAY]
    base = 50.0 + np.array([0.2 if i % 2 else 0.0 for i in range(len(days))])
    up, down = base.copy(), base.copy()
    up[40:] *= 1.10
    down[40:] *= 0.90
    daily = {
        "SPY": _daily(days, base * 8),
        "UP": _daily(days, up),
        "DN": _daily(days, down),
        "MIX": _daily(days, base),
        **{f"S{i:02d}": _daily(days, base, volume=1_000_000.0) for i in range(25)},
    }
    liquidity = {s: f for s, f in daily.items() if s != "SPY"}
    static = tuple(f"S{i:02d}" for i in range(25))
    report_day, session = days[40], days[42]

    def row(symbol, eps):
        return CalendarRow(symbol, report_day, EarningsTiming.UNSPECIFIED, eps, 1.00, None, 5, "Mar/2021")

    rows = (row("UP", 1.10), row("DN", 0.80), row("MIX", 1.10))
    flat = pd.concat([hourly_bars(d.date(), [100.0] * 7) for d in pd.bdate_range(session, periods=30)])
    acquisition = CalendarAcquisition(
        rows=rows,
        days=tuple(weekdays),
        fetched_dates=len(weekdays),
        reused_dates=0,
        failed_dates=(),
        empty_dates=tuple(empty_dates),
    )
    return PeadInputs(
        acquisition=acquisition,
        rows=rows,
        duplicates=0,
        market=MarketData(tuple(days), daily, static, liquidity_daily=liquidity),
        hourly={"UP": flat, "DN": flat},  # MIX has no hourly bars
        bar_failures={s: "daily_raw: RuntimeError: gone" for s in static_extra},
        static_requested=(*static, *static_extra),
    )


def _small_window_entry():
    window = ENTRY.window.model_copy(
        update={
            "decisions": (date(2021, 3, 1), date(2021, 6, 30)),
            "bars_through": date(2021, 8, 31),
            "recent_from": date(2021, 4, 1),
        }
    )
    entry = ENTRY.model_copy(update={"window": window})
    return type(LOADED)(entry=entry, sha256=LOADED.sha256, path=LOADED.path)


async def test_execute_completes_on_synthetic_inputs(tmp_path):
    inputs = _synthetic_inputs(empty_dates=(_HOLIDAY, date(2021, 3, 10)), static_extra=("S99",))

    async def build():
        return inputs

    output = tmp_path / "run"
    result = await execute_pead_study(_small_window_entry(), output, build=build, environment={})
    assert result["status"] == "completed", result.get("error")
    assert result["authorizes_promotion"] is False
    for name in ("events.csv.gz", "labels.csv.gz", "result.json", "manifest.json", "protocol.json"):
        assert (output / name).stat().st_mode & 0o777 == 0o600
    for direction in ("LONG", "SHORT"):
        assert {"p1", "p2", "p3", "p4", "passes"} <= set(result["legs"][direction])
        assert result["legs"][direction]["p4"]["n"] == 1 and result["decisions"][direction] == "failed"
    assert result["labels"]["no_hourly_bars"] == 1 and result["events"]["events"] == 3

    # F3: only the empty *session* page counts (the holiday page does not).
    assert result["calendar"]["empty_sessions_by_year"] == {2021: 1}
    assert result["calendar"]["requested_sessions_by_year"] == {2021: 60}
    # F4: the static liquidity universe actually used is recorded.
    universe = result["universe"]
    used = [f"S{i:02d}" for i in range(25)]
    assert universe["static_symbols"] == used
    assert universe["static_symbols_sha256"] == hashlib.sha256("\n".join(used).encode()).hexdigest()
    assert universe["static_missing"] == {"S99": "daily_raw: RuntimeError: gone"}
    assert universe["reference_names"] == {"min": 25, "median": 25.0, "max": 25, "dates": 1}

    saved = json.loads((output / "result.json").read_text())
    assert saved["status"] == "completed" and saved["universe"]["static_symbols"] == used
    assert saved["calendar"]["empty_sessions_by_year"] == {"2021": 1}


async def test_execute_fails_closed_on_too_many_empty_session_pages(tmp_path):
    sessions = [d.date() for d in pd.bdate_range("2021-03-22", periods=11) if d.date() != _HOLIDAY]
    assert len(sessions) == 10
    acquisition = CalendarAcquisition(
        rows=(),
        days=tuple(sorted([*sessions, _HOLIDAY])),
        fetched_dates=11,
        reused_dates=0,
        failed_dates=(),
        empty_dates=(_HOLIDAY, *sessions[:3]),  # 3/10 empty trading-session pages; the holiday is not one
    )

    async def build():
        return PeadInputs(
            acquisition=acquisition,
            rows=(),
            duplicates=0,
            market=MarketData(tuple(sessions), {}, (), liquidity_daily={}),
            hourly={},
            bar_failures={},
            static_requested=(),
        )

    result = await execute_pead_study(_small_window_entry(), tmp_path / "run", build=build, environment={})
    assert result["status"] == "failed" and "2021" in result["error"] and "empty" in result["error"]
