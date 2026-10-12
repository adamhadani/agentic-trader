from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind
from agentic_trader.research.apriori.catalog import load_exdiv_entry
from agentic_trader.research.apriori.exdiv_events import EXDIV_EVENT_COLUMNS, build_exdiv_events
from agentic_trader.research.apriori.pead_events import MarketData, decision_at


ENTRY = load_exdiv_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/exdiv-v1.json").entry


def sessions(start: str, count: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, periods=count)]


def daily_frame(days, closes, volume=2_000_000.0, spread=1.0):
    index = pd.DatetimeIndex([datetime.combine(d, time(5, 0), tzinfo=UTC) for d in days])
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + spread / 2,
            "Low": closes - spread / 2,
            "Close": closes,
            "Volume": np.full(len(closes), volume),
        },
        index=index,
    )


def wiggle(n, base=50.0, step=0.2):
    return base + np.array([step if i % 2 else 0.0 for i in range(n)])


def market(days, *, symbols=("XYZ",), static=25, volume=2_000_000.0, raw=None):
    daily = {s: daily_frame(days, wiggle(len(days)), volume=volume) for s in symbols}
    for i in range(static):
        daily[f"S{i:02d}"] = daily_frame(days, wiggle(len(days)), volume=1_000_000.0)
    liquidity = dict(daily)
    if raw:
        liquidity.update(raw)
    return MarketData(tuple(days), daily, tuple(f"S{i:02d}" for i in range(static)), liquidity_daily=liquidity)


def dividend(symbol, ex, *, rate=0.5, special=False, foreign=False, action_id=None):
    return CorporateAction(
        id=action_id or f"{symbol}-{ex.isoformat()}",
        kind=CorporateActionKind.CASH_DIVIDEND,
        symbol=symbol,
        effective_on=ex,
        process_date=ex + timedelta(days=30),
        rate=rate,
        special=special,
        foreign=foreign,
    )


def split(symbol, on):
    return CorporateAction(
        id=f"{symbol}-split-{on.isoformat()}",
        kind=CorporateActionKind.FORWARD_SPLIT,
        symbol=symbol,
        effective_on=on,
        process_date=on,
        old_rate=1.0,
        new_rate=2.0,
    )


def entry_with(days, **trade):
    window = ENTRY.window.model_copy(
        update={
            "decisions": (days[0], days[-1]),
            "recent_from": days[-1],
            "bars_through": days[-1] + timedelta(days=40),
        }
    )
    actions_window = (days[0] - timedelta(days=200), days[-1] + timedelta(days=40))
    updated = ENTRY.model_copy(update={"window": window, "actions_window": actions_window})
    if trade:
        updated = updated.model_copy(update={"trade": ENTRY.trade.model_copy(update=trade)})
    return updated


DAYS = sessions("2021-01-04", 120)
PREVIOUS = DAYS[20]  # a previous ex-date
EX = DAYS[20 + 63]  # ~91 calendar days later on this calendar


def _quarterly(symbol="XYZ"):
    return [dividend(symbol, PREVIOUS), dividend(symbol, EX)]


def test_a_regular_dividend_yields_a_leg_and_a_mid_quarter_control():
    raw = daily_frame(DAYS, wiggle(len(DAYS), base=80.0))  # raw prices differ from the adjusted ones
    frame, counts = build_exdiv_events(_quarterly(), market(DAYS, raw={"XYZ": raw}), entry_with(DAYS))
    assert list(frame.columns) == list(EXDIV_EVENT_COLUMNS)
    assert list(EXDIV_EVENT_COLUMNS[-2:]) == ["reference", "raw_close"]
    assert (counts["dividends"], counts["legs"], counts["controls"], counts["unpaired_legs"]) == (2, 1, 1, 0)
    assert counts["payers"] == 1 and counts["reasons"] == {"no_predecessor": 1}
    leg = frame[frame["role"] == "leg"].iloc[0]
    control = frame[frame["role"] == "control"].iloc[0]
    e = DAYS.index(EX)
    assert leg["session"] == DAYS[e - 5] and leg["decision_at"] == decision_at(DAYS[e - 5], time(10, 35))
    assert leg["ex_date"] == EX and leg["previous_ex_date"] == PREVIOUS and leg["gap_days"] == (EX - PREVIOUS).days
    assert leg["control_session"] is None and leg["rate"] == 0.5 and not leg["foreign"]
    assert control["pair_id"] == leg["pair_id"] and control["session"] == leg["session"]
    assert control["control_session"] == DAYS[e - 5 + 32]
    assert control["decision_at"] == decision_at(DAYS[e - 5 + 32], time(10, 35))
    assert leg["atr"] > 0 and control["atr"] > 0 and leg["median_dollar_volume"] >= leg["reference"]
    assert set(counts["reference_names"]) == {DAYS[e - 6].isoformat(), DAYS[e - 5 + 31].isoformat()}
    # The raw close as of the session before each decision (the yield denominator), not the adjusted one.
    assert leg["raw_close"] == raw["Close"].iloc[e - 6] and control["raw_close"] == raw["Close"].iloc[e - 5 + 31]


def test_specials_irregular_gaps_and_missing_predecessors_are_counted_not_emitted():
    actions = [
        dividend("XYZ", DAYS[5]),
        dividend("XYZ", DAYS[20]),  # 15 sessions after: irregular
        dividend("XYZ", DAYS[50], special=True),  # excluded, and not a predecessor
        dividend("XYZ", EX),  # regular against DAYS[20]
    ]
    frame, counts = build_exdiv_events(actions, market(DAYS), entry_with(DAYS))
    assert counts["legs"] == 1 and frame[frame["role"] == "leg"].iloc[0]["previous_ex_date"] == DAYS[20]
    assert counts["reasons"] == {"no_predecessor": 1, "irregular_gap": 1, "special_dividend": 1}
    assert counts["reasons_by_year"]["special_dividend"] == {2021: 1}


def test_a_non_session_ex_date_and_an_early_calendar_are_counted():
    saturday = EX + timedelta(days=(5 - EX.weekday()) % 7 or 7)
    frame, counts = build_exdiv_events(
        [dividend("XYZ", PREVIOUS), dividend("XYZ", saturday)], market(DAYS), entry_with(DAYS)
    )
    assert frame.empty and counts["reasons"] == {"no_predecessor": 1, "non_session_ex_date": 1}
    # The predecessor's ex-date precedes the calendar (outside_calendar), yet it is still the predecessor;
    # the decision 5 sessions before index 12 then lacks ATR history (outside_calendar again).
    actions = [dividend("XYZ", DAYS[12] - timedelta(days=91)), dividend("XYZ", DAYS[12])]
    frame, counts = build_exdiv_events(actions, market(DAYS), entry_with(DAYS))
    assert frame.empty and counts["reasons"] == {"outside_calendar": 2}
    assert counts["reasons_by_year"]["outside_calendar"] == {2020: 1, 2021: 1}


def test_an_ex_date_before_or_after_the_calendar_counts_outside_calendar_not_an_eligibility_reason():
    before, after = DAYS[0] - timedelta(days=91), DAYS[-1] + timedelta(days=91)
    _, counts = build_exdiv_events(
        [dividend("XYZ", before), *_quarterly(), dividend("XYZ", after)], market(DAYS), entry_with(DAYS)
    )
    # `before` counts outside_calendar (not no_predecessor) yet stays PREVIOUS's predecessor (irregular gap).
    assert counts["legs"] == 1 and counts["reasons"] == {"outside_calendar": 2, "irregular_gap": 1}


def test_dividends_outside_the_decision_window_count_outside_window_and_still_serve_as_predecessors():
    late = entry_with(DAYS).model_copy(
        update={"window": entry_with(DAYS).window.model_copy(update={"decisions": (DAYS[30], DAYS[-1])})}
    )
    frame, counts = build_exdiv_events(_quarterly(), market(DAYS), late)
    assert counts["reasons"] == {"outside_window": 1}  # PREVIOUS decides at DAYS[15]; not no_predecessor
    assert counts["legs"] == 1 and frame.iloc[0]["previous_ex_date"] == PREVIOUS


def test_a_regular_dividend_sharing_its_predecessors_ex_date_is_counted_once():
    actions = [*_quarterly(), dividend("XYZ", EX, action_id="XYZ-duplicate")]
    frame, counts = build_exdiv_events(actions, market(DAYS), entry_with(DAYS))
    assert counts["legs"] == 1 and counts["controls"] == 1 and len(frame) == 2
    assert counts["reasons"] == {"no_predecessor": 1, "duplicate_ex_date": 1}


def test_payers_counts_symbols_with_any_cash_dividend():
    actions = [*_quarterly(), dividend("ABC", EX, special=True), split("DEF", EX)]
    _, counts = build_exdiv_events(actions, market(DAYS, symbols=("XYZ", "ABC", "DEF")), entry_with(DAYS))
    assert counts["payers"] == 2 and counts["dividends"] == 3


def test_a_structural_action_near_the_span_excludes_the_leg():
    e = DAYS.index(EX)
    actions = [*_quarterly(), split("XYZ", DAYS[e - 5] - timedelta(days=10))]  # exactly at the pad: excluded
    _, counts = build_exdiv_events(actions, market(DAYS), entry_with(DAYS))
    assert counts["legs"] == 0 and counts["reasons"]["structural_action_nearby"] == 1
    actions = [*_quarterly(), split("XYZ", EX + timedelta(days=11))]  # one day beyond: kept
    _, counts = build_exdiv_events(actions, market(DAYS), entry_with(DAYS))
    assert counts["legs"] == 1


def test_another_ex_date_inside_the_legs_span_excludes_the_leg():
    e = DAYS.index(EX)
    for edge in (e - 6, e - 1):  # D - 1 and E - 1, the edges of the cum-dividend span
        special = dividend("XYZ", DAYS[edge], special=True)
        frame, counts = build_exdiv_events([*_quarterly(), special], market(DAYS), entry_with(DAYS))
        assert frame.empty and counts["legs"] == 0
        assert counts["reasons"] == {"no_predecessor": 1, "special_dividend": 1, "leg_overlaps_ex_date": 1}
    outside = dividend("XYZ", DAYS[e - 7], special=True)
    _, counts = build_exdiv_events([*_quarterly(), outside], market(DAYS), entry_with(DAYS))
    assert counts["legs"] == 1


def test_a_control_that_overlaps_any_ex_date_or_a_structural_action_is_dropped_but_the_leg_stays():
    e = DAYS.index(EX)
    inside = dividend("XYZ", DAYS[e - 5 + 32 + 5], special=True)  # the last session of the overlap span
    _, counts = build_exdiv_events([*_quarterly(), inside], market(DAYS), entry_with(DAYS))
    assert counts["legs"] == 1 and counts["controls"] == 0 and counts["unpaired_legs"] == 1
    assert counts["reasons"] == {"no_predecessor": 1, "special_dividend": 1, "control_overlaps_ex_date": 1}
    lower = dividend("XYZ", DAYS[e - 5 + 32 - 1], special=True)  # D_c - 1, the first session of the span
    _, counts = build_exdiv_events([*_quarterly(), lower], market(DAYS), entry_with(DAYS))
    assert counts["controls"] == 0 and counts["reasons"]["control_overlaps_ex_date"] == 1
    before = dividend("XYZ", DAYS[e - 5 + 32 - 2], special=True)  # one session earlier: the control stays
    _, counts = build_exdiv_events([*_quarterly(), before], market(DAYS), entry_with(DAYS))
    assert counts["controls"] == 1
    nearby = split("XYZ", DAYS[e - 5 + 32] + timedelta(days=3))
    _, counts = build_exdiv_events([*_quarterly(), nearby], market(DAYS), entry_with(DAYS))
    assert counts["controls"] == 0 and counts["reasons"]["control_structural_action_nearby"] == 1


def test_a_control_beyond_the_calendar_is_counted_and_the_leg_stays():
    short = DAYS[: DAYS.index(EX) + 3]
    _, counts = build_exdiv_events(_quarterly(), market(short), entry_with(short))
    assert counts["legs"] == 1 and counts["controls"] == 0
    assert counts["reasons"]["control_outside_calendar"] == 1


def test_liquidity_reasons_use_raw_bars_as_of_the_session_before_the_decision():
    cheap = daily_frame(DAYS, wiggle(len(DAYS), base=5.0))  # raw close below the 10.0 floor
    _, counts = build_exdiv_events(_quarterly(), market(DAYS, raw={"XYZ": cheap}), entry_with(DAYS))
    assert counts["legs"] == 0 and counts["reasons"]["illiquid_price"] == 1
    thin = market(DAYS, volume=10.0)
    _, counts = build_exdiv_events(_quarterly(), thin, entry_with(DAYS))
    assert counts["reasons"]["illiquid_volume"] == 1
    e = DAYS.index(EX)
    short = daily_frame(DAYS[e - 23 : e - 5], wiggle(18))  # 18 raw sessions through D - 1: below 20
    _, counts = build_exdiv_events(_quarterly(), market(DAYS, raw={"XYZ": short}), entry_with(DAYS))
    assert counts["legs"] == 0 and counts["reasons"]["short_history"] == 1
    gap = [day for day in DAYS if day != DAYS[e - 6]]  # every raw session but D - 1
    _, counts = build_exdiv_events(
        _quarterly(), market(DAYS, raw={"XYZ": daily_frame(gap, wiggle(len(gap)))}), entry_with(DAYS)
    )
    assert counts["legs"] == 0 and counts["reasons"]["no_raw_close"] == 1


def test_a_control_gate_failure_drops_the_control_under_a_control_reason():
    e = DAYS.index(EX)
    closes = wiggle(len(DAYS))
    closes[e - 5 + 32 - 1] = 5.0  # a raw dip below the 10.0 floor only at D_c - 1
    raw = daily_frame(DAYS, closes)
    _, counts = build_exdiv_events(_quarterly(), market(DAYS, raw={"XYZ": raw}), entry_with(DAYS))
    assert counts["legs"] == 1 and counts["controls"] == 0 and counts["unpaired_legs"] == 1
    assert counts["reasons"] == {"no_predecessor": 1, "control_illiquid_price": 1}


def test_decisions_outside_the_window_and_symbols_without_bars_are_counted():
    narrow = entry_with(DAYS).model_copy(
        update={
            "window": entry_with(DAYS).window.model_copy(
                update={"decisions": (DAYS[0], DAYS[10]), "recent_from": DAYS[10]}
            )
        }
    )
    _, counts = build_exdiv_events(_quarterly(), market(DAYS), narrow)
    assert counts["reasons"] == {"outside_window": 2}  # both, before any eligibility reason
    _, counts = build_exdiv_events(_quarterly("QQQQ"), market(DAYS), entry_with(DAYS))
    assert counts["reasons"]["no_daily_bars"] == 1
    _, counts = build_exdiv_events(_quarterly("BRK.B"), market(DAYS), entry_with(DAYS))
    assert counts["reasons"]["unsupported_symbol"] == 2  # both dividends of an unsupported name


def test_output_is_deterministic_and_sorted():
    actions = [*_quarterly("XYZ"), *_quarterly("ABC")]
    mkt = market(DAYS, symbols=("XYZ", "ABC"))
    first, _ = build_exdiv_events(actions, mkt, entry_with(DAYS))
    second, _ = build_exdiv_events(list(reversed(actions)), mkt, entry_with(DAYS))
    pd.testing.assert_frame_equal(first, second)
    assert list(first["symbol"]) == ["ABC", "ABC", "XYZ", "XYZ"] and list(first["role"]) == ["leg", "control"] * 2
