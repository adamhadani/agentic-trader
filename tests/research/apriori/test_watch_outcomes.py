from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.watch_contract import frozen_watch_contract
from agentic_trader.research.apriori.watch_outcomes import (
    decision_frame,
    label_watch_events,
    summarize_watch,
    watch_bars,
)


SESSION = date(2026, 10, 5)
BRACKET = frozen_watch_contract().spec.bracket
COST_BPS = frozen_watch_contract().spec.decision_cost_bps


def _event(symbol: str, outcome: str, *, atr: float | None = 2.0) -> dict:
    event = {
        "event_id": f"{symbol}-1",
        "symbol": symbol,
        "filing_date": "2026-10-02",
        "session": SESSION.isoformat(),
        "decision_at": datetime.combine(SESSION, time(10, 35), tzinfo=ET_TZ).astimezone(UTC).isoformat(),
        "buyers": 3,
        "opp_dollars": 900_000.0,
        "outcome": outcome,
    }
    if atr is not None:
        event["atr"] = atr
    return event


def _payload(scan_id: str, events: list[dict]) -> dict:
    return {"scan_id": scan_id, "session": SESSION.isoformat(), "status": "completed", "events": events}


def _hourly(prices: list[float]) -> pd.DataFrame:
    index = [datetime.combine(SESSION, time(9 + k), tzinfo=ET_TZ).astimezone(UTC) for k in range(len(prices))]
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p, "Volume": 1.0}, index=pd.DatetimeIndex(index))


def _daily(returns: np.ndarray, start_price: float) -> pd.DataFrame:
    days = pd.bdate_range(end=pd.Timestamp(SESSION) - pd.Timedelta(days=1), periods=len(returns) + 1)
    closes = start_price * np.concatenate([[1.0], np.cumprod(1.0 + returns)])
    index = pd.DatetimeIndex([pd.Timestamp(day).tz_localize(ET_TZ) for day in days])
    return pd.DataFrame({"Close": closes}, index=index)


def test_decision_frame_keeps_sent_and_eligible_unsent_events_and_dedupes_scans():
    payloads = [
        _payload(
            "s1", [_event("AAA", "sent"), _event("BBB", "skipped: owned"), _event("CCC", "ineligible: x", atr=None)]
        ),
        _payload("s1", [_event("AAA", "sent")]),
    ]
    frame = decision_frame(payloads)
    assert sorted(frame["symbol"]) == ["AAA", "BBB"]
    assert frame.set_index("symbol")["sent"].to_dict() == {"AAA": True, "BBB": False}
    assert decision_frame([]).empty


def test_labels_use_the_frozen_bracket_and_the_beta_matched_excess():
    frame = decision_frame([_payload("s1", [_event("AAA", "sent"), _event("BBB", "no capacity")])])
    hourly = {"AAA": _hourly([100.0, 100.0, 100.0, 112.0, 112.0]), "BBB": _hourly([100.0, 100.0, 100.0, 90.0, 90.0])}
    market_returns = np.random.default_rng(1).normal(0.0, 0.01, 130)
    daily = {"AAA": _daily(2.0 * market_returns, 50.0), "BBB": _daily(1.0 * market_returns, 80.0)}
    labels = label_watch_events(
        frame,
        hourly,
        daily,
        _hourly([400.0, 400.0, 400.0, 404.0, 404.0]),
        _daily(market_returns, 400.0),
        BRACKET,
        COST_BPS,
    )
    row = labels.set_index("symbol").loc["AAA"]
    assert row["hit"] == "target"
    assert row["r_cost"] < row["r"] <= BRACKET.target_r + 1e-9
    assert abs(row["beta"] - 2.0) < 1e-6
    assert abs(row["market_r"] - 0.25) < 1e-6  # +1% over the hold, 100 / (2 * 2 ATR) risk
    assert abs(row["beta_excess_r"] - (row["r_cost"] - 0.5)) < 1e-6

    signals = [{"strategy": "insider_watch_long", "status": "EXECUTED", "executed_at": "2026-10-05 14:36:00"}]
    summary = summarize_watch(frame, labels, signals)
    assert summary["events"] == 2 and summary["sent"] == 1 and summary["taps"] == 1 and summary["fills"] == 1
    assert summary["all"]["n"] == 2 and summary["sent_events"]["n"] == 1 and summary["unsent_events"]["n"] == 1
    assert summary["sent_events"]["mean_r_cost"] == row["r_cost"]
    assert summary["all"]["mean_beta_excess_r"] is not None


def test_a_missing_bar_series_is_a_reason_not_a_crash_and_summary_handles_no_evidence():
    frame = decision_frame([_payload("s1", [_event("AAA", "sent")])])
    labels = label_watch_events(frame, {}, {}, None, None, BRACKET, COST_BPS)
    assert labels.iloc[0]["reason"] == "no_hourly_bars" and pd.isna(labels.iloc[0]["r_cost"])
    summary = summarize_watch(frame, labels, [])
    assert summary["all"] == {"n": 0, "mean_r_cost": None, "mean_beta_excess_r": None}
    assert summary["taps"] == 0 and summary["unlabelled"] == {"no_hourly_bars": 1}
    empty = summarize_watch(
        decision_frame([]), label_watch_events(decision_frame([]), {}, {}, None, None, BRACKET, COST_BPS), []
    )
    assert empty["events"] == 0


def test_watch_bars_fetches_each_symbol_and_the_proxy_once_and_records_failures():
    frame = decision_frame([_payload("s1", [_event("AAA", "sent"), _event("BBB", "x")])])
    calls: list[tuple[str, str]] = []

    def fetch(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        calls.append((symbol, timeframe))
        if symbol == "BBB":
            raise RuntimeError("boom")
        assert start < datetime.combine(SESSION, time.min, tzinfo=UTC) - timedelta(days=5)
        return _hourly([1.0])

    bars = watch_bars(fetch, frame, datetime.now(UTC))
    assert set(bars.hourly) == {"AAA"} and set(bars.daily) == {"AAA"}
    assert bars.failures == {"BBB": "RuntimeError: boom"}
    assert sorted(calls) == [("AAA", "1d"), ("AAA", "1h"), ("BBB", "1h"), ("SPY", "1d"), ("SPY", "1h")]


def test_decision_frame_dedupes_by_event_id_across_scans_and_keeps_the_sent_row():
    rerun = [_payload("s1", [_event("AAA", "sent")]), _payload("s2", [_event("AAA", "insider watch budget spent")])]
    for payloads in (rerun, rerun[::-1]):
        frame = decision_frame(payloads)
        assert list(frame["symbol"]) == ["AAA"] and list(frame["scan_id"]) == ["s1"] and frame["sent"].all()
    # Two events of one scan are two rows.
    two = decision_frame([_payload("s1", [_event("AAA", "sent"), _event("BBB", "not sent")])])
    assert sorted(two["event_id"]) == ["AAA-1", "BBB-1"]
