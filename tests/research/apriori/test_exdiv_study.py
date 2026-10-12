import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import LoadedExdivEntry, load_exdiv_entry
from agentic_trader.research.apriori.exdiv_study import (
    ExdivInputs,
    evaluate_exdiv,
    execute_exdiv_study,
    label_exdiv_events,
)
from agentic_trader.research.apriori.pead_events import MarketData, decision_at


ENTRY_PATH = Path(__file__).resolve().parents[3] / "config/research/apriori/exdiv-v1.json"
LOADED = load_exdiv_entry(ENTRY_PATH)
ENTRY = LOADED.entry


def hourly_day(day: date, prices: list[float]) -> pd.DataFrame:
    index = [datetime.combine(day, time(9 + k), tzinfo=ET_TZ).astimezone(UTC) for k in range(len(prices))]
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p, "Volume": 1.0}, index=pd.DatetimeIndex(index))


def hourly(days, price_by_day) -> pd.DataFrame:
    return pd.concat([hourly_day(d, [price_by_day(d)] * 7) for d in days])


def _event(symbol, session, *, role="leg", pair_id="p1", control_session=None, atr=1.0, ex_date=None, rate=0.5):
    decision_day = control_session or session
    return {
        "pair_id": pair_id,
        "role": role,
        "symbol": symbol,
        "session": session,
        "control_session": control_session,
        "decision_at": decision_at(decision_day, time(10, 35)),
        "ex_date": ex_date or session + timedelta(days=7),
        "previous_ex_date": session - timedelta(days=84),
        "gap_days": 91,
        "rate": rate,
        "foreign": False,
        "atr": atr,
        "median_dollar_volume": 5e6,
        "reference": 1e6,
    }


def _sessions(n, start="2022-03-01"):
    return [d.date() for d in pd.bdate_range(start, periods=n)]


def test_time_exit_is_the_close_of_the_last_cum_dividend_session():
    days = _sessions(14)  # room for the 10-session secondary hold to mature
    session, ex = days[2], days[7]  # five sessions before the ex-date
    bars = hourly(days, lambda d: 100.0 + days.index(d))  # rises one dollar per session; no bracket hit at ATR 10
    events = pd.DataFrame([_event("XYZ", session, atr=10.0, ex_date=ex)])
    labels, counts = label_exdiv_events(events, {"XYZ": bars}, ENTRY)
    assert not [reason for reason in counts if reason not in ("by_year", "by_role")]  # no misses
    row = labels.iloc[0]
    assert row["hit"] == "timeout" and row["holding_sessions"] == 5
    assert pd.Timestamp(row["exit_at"]).tz_convert(ET_TZ).date() == days[6]  # the session before the ex-date
    assert row["decision_price"] == 102.0 and row["r"] > 0 and row["r_cost_5bps"] < row["r_cost_0bps"]
    assert np.isfinite(row["hold_secondary_r_cost"])  # the 10-session hold crosses the ex-date


def test_a_label_that_would_exit_on_or_after_the_ex_date_is_excluded_and_counted():
    days = _sessions(12)
    session, ex = days[2], days[7]
    gappy = [d for d in days if d != days[4]]  # one missing session shifts the fifth distinct date onto the ex-date
    bars = hourly(gappy, lambda d: 100.0)
    events = pd.DataFrame([_event("XYZ", session, atr=10.0, ex_date=ex)])
    labels, counts = label_exdiv_events(events, {"XYZ": bars}, ENTRY)
    assert labels.empty and counts["exit_on_or_after_ex_date"] == 1


def test_control_rows_are_labelled_without_a_secondary_hold_and_misses_count_by_role():
    days = _sessions(60)
    bars = hourly(days, lambda d: 100.0)
    events = pd.DataFrame(
        [
            _event("XYZ", days[2], atr=10.0, ex_date=days[7]),
            _event("XYZ", days[2], role="control", control_session=days[34], atr=10.0, ex_date=days[7]),
            _event("ABC", days[2], pair_id="p2", atr=10.0, ex_date=days[7]),  # no hourly bars
        ]
    )
    labels, counts = label_exdiv_events(events, {"XYZ": bars}, ENTRY)
    assert list(labels["role"]) == ["leg", "control"]
    assert np.isnan(labels.iloc[1]["hold_secondary_r_cost"]) and labels.iloc[1]["hit"] == "timeout"
    assert counts["no_hourly_bars"] == 1 and counts["by_role"]["no_hourly_bars"] == {"leg": 1}


def _labels(rows):
    return pd.DataFrame(rows)


def _row(pair_id, session, role, r_cost):
    return {
        "pair_id": pair_id,
        "session": session,
        "role": role,
        "r_cost": r_cost,
        "rate": 0.5,
        "decision_price": 100.0,
        "median_dollar_volume": 1e6,
        "foreign": False,
        "hit": "timeout",
        "hold_secondary_r_cost": 0.0,
        "r_cost_0bps": r_cost,
        "r_cost_5bps": r_cost,
        "r_cost_10bps": r_cost,
        "holding_sessions": 5,
    }


def test_evaluate_passes_a_clear_edge_over_its_own_controls_and_pairs_by_pair_id():
    rng = np.random.default_rng(0)
    rows = []
    for k, session in enumerate(_sessions(400, "2022-06-01")):
        rows.append(_row(f"p{k}", session, "leg", 0.4 + 0.1 * rng.standard_normal()))
        rows.append(_row(f"p{k}", session, "control", -0.1 + 0.1 * rng.standard_normal()))
    result = evaluate_exdiv(_labels(rows), ENTRY)
    assert result["passes"] and result["unpaired_legs"] == 0 and result["p4"]["n_recent"] >= 100
    frame = _labels(rows)
    per_pair = frame.pivot(index="pair_id", columns="role", values="r_cost")
    assert abs(result["p2"]["mean_diff"] - float((per_pair["leg"] - per_pair["control"]).mean())) < 1e-9


def test_evaluate_drops_unpaired_legs_from_p2_only():
    rows = []
    for k, session in enumerate(_sessions(400, "2022-06-01")):
        rows.append(_row(f"p{k}", session, "leg", 0.5))
        if k < 300:
            rows.append(_row(f"p{k}", session, "control", 0.1))
    result = evaluate_exdiv(_labels(rows), ENTRY)
    assert result["p1"]["n"] == 400 and result["p2"]["n_leg"] == 300 and result["unpaired_legs"] == 100


def test_evaluate_fails_p4_and_p3_on_small_or_recent_losing_samples():
    rows = [_row(f"p{k}", s, "leg", 0.5) for k, s in enumerate(_sessions(50, "2024-01-01"))]
    assert not evaluate_exdiv(_labels(rows), ENTRY)["p4"]["holds"]
    rows = [
        _row(f"p{k}", s, "leg", 0.5 if s < date(2023, 1, 1) else -0.2)
        for k, s in enumerate(_sessions(700, "2021-01-01"))
    ]
    assert not evaluate_exdiv(_labels(rows), ENTRY)["p3"]["holds"]


def _dividend(symbol, ex, special=False):
    return CorporateAction(
        id=f"{symbol}-{ex}",
        kind=CorporateActionKind.CASH_DIVIDEND,
        symbol=symbol,
        effective_on=ex,
        process_date=ex + timedelta(days=30),
        rate=0.5,
        special=special,
    )


def _daily(days, closes, volume=2_000_000.0):
    index = pd.DatetimeIndex([datetime.combine(d, time(5), tzinfo=UTC) for d in days])
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": np.full(len(c), volume)}, index=index
    )


def _synthetic_inputs(*, actions=None):
    days = _sessions(140, "2021-01-04")
    closes = 50.0 + np.arange(len(days)) % 2 * 0.2
    daily = {"XYZ": _daily(days, closes), **{f"S{i:02d}": _daily(days, closes, 1_000_000.0) for i in range(25)}}
    market = MarketData(tuple(days), daily, tuple(f"S{i:02d}" for i in range(25)), liquidity_daily=daily)
    if actions is None:
        actions = (_dividend("XYZ", days[20]), _dividend("XYZ", days[83]))
    return ExdivInputs(
        actions=tuple(actions),
        actions_manifest={"request_sha256": "r" * 64, "content_sha256": "c" * 64, "count": len(actions)},
        cohort={"path": "cohort.json", "sha256": "h" * 64, "symbols": 1},
        market=market,
        hourly={"XYZ": hourly(days, lambda d: 50.0)},
        bar_failures={},
        static_requested=tuple(f"S{i:02d}" for i in range(25)),
    )


def _small_window_entry():
    days = _sessions(140, "2021-01-04")
    window = ENTRY.window.model_copy(
        update={
            "decisions": (days[0], days[-1]),
            "recent_from": days[-1],
            "bars_through": days[-1] + timedelta(days=40),
        }
    )
    entry = ENTRY.model_copy(
        update={"window": window, "actions_window": (days[0] - timedelta(days=200), days[-1] + timedelta(days=40))}
    )
    return LoadedExdivEntry(entry=entry, sha256=LOADED.sha256, path=LOADED.path)


async def test_execute_writes_protocol_and_manifest_before_building_and_records_failure(tmp_path):
    directory = tmp_path / "run"
    seen = {}

    async def build():
        seen["manifest"] = (directory / "manifest.json").exists() and (directory / "protocol.json").exists()
        raise RuntimeError("provider down")

    result = await execute_exdiv_study(LOADED, directory, build=build, environment={"e": 1})
    assert seen == {"manifest": True}
    assert (
        result["status"] == "failed" and "RuntimeError" in result["error"] and result["authorizes_promotion"] is False
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    assert (
        manifest["entry_id"] == "exdiv"
        and manifest["sha256"] == LOADED.sha256
        and manifest["authorizes_promotion"] is False
    )


async def test_execute_completes_on_synthetic_inputs_and_fails_closed_on_zero_actions(tmp_path):
    inputs = _synthetic_inputs()

    async def build():
        return inputs

    result = await execute_exdiv_study(_small_window_entry(), tmp_path / "ok", build=build, environment={})
    assert result["status"] == "completed" and result["decision"] in {"eligible_for_probe", "failed"}
    assert result["events"]["legs"] == 1 and result["labels"]["rows"] == 2
    assert result["actions"]["content_sha256"] == "c" * 64 and result["cohort"]["sha256"] == "h" * 64
    assert set(result["universe"]) >= {"static_symbols", "static_symbols_sha256", "reference_names"}
    assert (tmp_path / "ok" / "events.csv.gz").exists() and (tmp_path / "ok" / "labels.csv.gz").exists()
    assert result["authorizes_promotion"] is False and result["leg"]["p4"]["holds"] is False

    empty = _synthetic_inputs(actions=())

    async def build_empty():
        return empty

    result = await execute_exdiv_study(_small_window_entry(), tmp_path / "empty", build=build_empty, environment={})
    assert result["status"] == "failed" and "no corporate actions" in result["error"]
