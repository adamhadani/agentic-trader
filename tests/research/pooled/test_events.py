import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.apriori.earnings_history import CalendarPageStore
from agentic_trader.research.apriori.pead_events import by_session, reaction_z
from agentic_trader.research.pooled.campaign import EventInputs
from agentic_trader.research.pooled.events import (
    EVENT_FIELDS,
    LoadedCalendar,
    build_event_frames,
    load_actions,
    load_calendar_pages,
    pages_sha256,
    verify_event_sources,
)
from agentic_trader.research.pooled.formula import prepare_daily
from agentic_trader.storage.workflow import encode
from tests.research.pooled.test_formula import daily, weekdays


DAYS = weekdays(date(2021, 1, 4), 300)  # 300 trading days
INPUTS = EventInputs.model_validate(
    {
        "fields_version": "events_fields_v1",
        "earnings": {
            "source": "nasdaq-calendar",
            "pages_sha256": "a" * 64,
            "calendar_from": "2021-01-04",
            "calendar_through": "2022-03-31",
            "min_estimates": 1,
            "min_abs_forecast": 0.05,
            "vol_window": 20,
            "benchmark": "SPY",
        },
        "corporate_actions": {
            "source": "alpaca",
            "content_sha256": "b" * 64,
            "window": ["2021-01-01", "2022-12-31"],
            "trailing_sessions": 252,
        },
    }
)


def _row(
    symbol: str, day: date, eps: float | None = 1.1, forecast: float | None = 1.0, n: int | None = 3
) -> CalendarRow:
    return CalendarRow(
        symbol=symbol,
        date=day,
        timing=EarningsTiming.AFTER_HOURS,
        eps=eps,
        eps_forecast=forecast,
        surprise_pct_reported=None,
        n_estimates=n,
        fiscal_quarter=None,
    )


def _calendar(rows_by_symbol: dict) -> LoadedCalendar:
    return LoadedCalendar(
        rows_by_symbol={k: tuple(v) for k, v in rows_by_symbol.items()}, pages_sha256="a" * 64, pages=1, duplicates=0
    )


def _dividend(
    symbol: str, ex: date, rate: float | None = 0.5, special: bool = False, kind: str = "cash_dividend"
) -> CorporateAction:
    return CorporateAction(
        id=f"{symbol}-{ex.isoformat()}",
        kind=kind,
        symbol=symbol,
        effective_on=ex,
        process_date=ex,
        rate=rate,
        special=special,
    )


def _build(inputs, calendar, actions, adjusted, days, symbols, raw=None):
    """Raw bars default to the adjusted ones (no later actions: factor 1)."""
    return build_event_frames(inputs, calendar, actions, adjusted, adjusted if raw is None else raw, days, symbols)


def _world(prices_aaa: list[float] | None = None):
    closes = prices_aaa or list(np.linspace(50, 80, len(DAYS)))
    return {
        "AAA": daily(DAYS, closes),
        "SPY": daily(DAYS, list(np.linspace(300, 330, len(DAYS)))),
    }


def test_report_is_visible_from_the_day_after_with_age_zero_and_carried_forward():
    d = DAYS[100]
    frames = _build(INPUTS, _calendar({"AAA": [_row("AAA", d)]}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    assert list(frames.columns) == list(EVENT_FIELDS)
    assert np.isnan(frames["earnings_age"].iloc[99]) and np.isnan(frames["earnings_age"].iloc[100])
    assert frames["earnings_age"].iloc[101] == 0.0 and frames["earnings_age"].iloc[150] == 49.0
    assert frames["earnings_surprise"].iloc[101] == pytest.approx(10.0)  # 100 * (1.1 - 1.0) / 1.0
    assert frames["earnings_surprise"].iloc[299] == pytest.approx(10.0)
    assert np.isfinite(frames["earnings_reaction"].iloc[101])
    assert frames["earnings_reaction"].iloc[101] == frames["earnings_reaction"].iloc[299]


def test_pre_market_and_after_hours_reports_on_the_same_day_give_identical_fields():
    d = DAYS[100]
    pre = _row("AAA", d)
    pre = CalendarRow(**{**pre.__dict__, "timing": EarningsTiming.PRE_MARKET})
    a = _build(INPUTS, _calendar({"AAA": [pre]}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    b = _build(INPUTS, _calendar({"AAA": [_row("AAA", d)]}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    pd.testing.assert_frame_equal(a, b)


def test_next_report_replaces_the_previous_from_its_visibility_day():
    rows = [_row("AAA", DAYS[100], eps=1.1), _row("AAA", DAYS[160], eps=0.9)]
    f = _build(INPUTS, _calendar({"AAA": rows}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    assert f["earnings_age"].iloc[160] == 59.0 and f["earnings_surprise"].iloc[160] == pytest.approx(10.0)
    assert f["earnings_age"].iloc[161] == 0.0 and f["earnings_surprise"].iloc[161] == pytest.approx(-10.0)


def test_surprise_is_nan_without_estimates_or_with_a_tiny_forecast_but_age_advances():
    rows = [_row("AAA", DAYS[100], n=0), _row("AAA", DAYS[160], forecast=0.01)]
    f = _build(INPUTS, _calendar({"AAA": rows}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    assert np.isnan(f["earnings_surprise"].iloc[120]) and f["earnings_age"].iloc[120] == 19.0
    assert np.isnan(f["earnings_surprise"].iloc[170]) and f["earnings_age"].iloc[170] == 9.0


def test_reaction_is_nan_without_reaction_bars_but_age_advances():
    closes = list(np.linspace(50, 80, len(DAYS)))
    closes[101] = float("nan")  # no close at D+1
    f = _build(INPUTS, _calendar({"AAA": [_row("AAA", DAYS[100])]}), [], _world(closes), DAYS, ["AAA"]).frames["AAA"]
    assert np.isnan(f["earnings_reaction"].iloc[101]) and f["earnings_age"].iloc[101] == 0.0


def test_reaction_matches_the_pead_definition():
    world = _world()
    f = _build(INPUTS, _calendar({"AAA": [_row("AAA", DAYS[100])]}), [], world, DAYS, ["AAA"]).frames["AAA"]
    expected = reaction_z(by_session(world["AAA"]), by_session(world["SPY"]), DAYS, 100, 20)
    assert f["earnings_reaction"].iloc[101] == pytest.approx(expected)


def test_non_session_reports_are_skipped_and_counted():
    saturday = DAYS[100] + timedelta(days=(5 - DAYS[100].weekday()) % 7 or 7)
    assert saturday.weekday() == 5
    built = _build(INPUTS, _calendar({"AAA": [_row("AAA", saturday)]}), [], _world(), DAYS, ["AAA"])
    assert built.coverage.non_session_reports == 1
    assert np.isnan(built.frames["AAA"]["earnings_age"]).all()


def test_dividend_yield_trailing_window_and_coverage_rules():
    actions = [_dividend("AAA", DAYS[260]), _dividend("AAA", DAYS[10])]
    f = _build(INPUTS, _calendar({}), actions, _world(), DAYS, ["AAA"]).frames["AAA"]
    assert np.isnan(f["dividend_yield"].iloc[250])  # first 251 days of the window are NaN
    close = _world()["AAA"]["Close"].to_numpy(float)
    assert f["dividend_yield"].iloc[251] == pytest.approx(0.5 / close[251])  # DAYS[10] is inside [0, 251]
    assert f["dividend_yield"].iloc[262] == pytest.approx(0.5 / close[262])  # DAYS[10] fell out, DAYS[260] is in
    assert f["dividend_yield"].iloc[259] == pytest.approx(0.5 / close[259])  # DAYS[10] still in (259 - 251 = 8 <= 10)


def test_dividend_yield_ignores_special_and_unpriced_rows():
    actions = [
        _dividend("AAA", DAYS[260], special=True),
        _dividend("AAA", DAYS[261], rate=None),
        _dividend("AAA", DAYS[262], kind="forward_split", rate=2.0),
        _dividend("AAA", date(2020, 6, 1)),  # before the first trading day: ignored
    ]
    built = _build(INPUTS, _calendar({}), actions, _world(), DAYS, ["AAA"])
    assert built.frames["AAA"]["dividend_yield"].iloc[251] == 0.0
    assert built.frames["AAA"]["dividend_yield"].iloc[270] == 0.0
    assert built.coverage.unpriced_dividends == 1


def test_dividend_on_a_non_session_counts_from_the_next_session():
    ex = DAYS[260] + timedelta(days=(5 - DAYS[260].weekday()) % 7 or 7)  # a Saturday
    f = _build(INPUTS, _calendar({}), [_dividend("AAA", ex)], _world(), DAYS, ["AAA"]).frames["AAA"]
    k = next(i for i, d in enumerate(DAYS) if d >= ex)
    assert f["dividend_yield"].iloc[k - 1] == 0.0 and f["dividend_yield"].iloc[k] > 0.0


def test_symbol_without_reports_or_actions_is_all_nan_or_zero_and_reported():
    built = _build(INPUTS, _calendar({}), [], _world(), DAYS, ["AAA"])
    assert built.coverage.symbols_without_reports == ("AAA",) and built.coverage.symbols_without_actions == ("AAA",)
    f = built.frames["AAA"]
    assert np.isnan(f["earnings_age"]).all() and f["dividend_yield"].iloc[-1] == 0.0
    assert built.coverage.first_finite["AAA"]["earnings_age"] is None
    assert built.coverage.first_finite["AAA"]["dividend_yield"] == DAYS[251].isoformat()


def test_frames_align_with_prepare_daily_index():
    f = _build(INPUTS, _calendar({}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    keyed = prepare_daily(_world())["AAA"]
    assert f.index.equals(keyed.index)


def _write_pages(directory: Path, days: list[date], symbol_rows: dict[date, list[dict]]) -> None:
    store = CalendarPageStore(directory)
    for day in days:
        body = json.dumps({"data": {"rows": symbol_rows.get(day, [])}}).encode()
        store.save(day, body, datetime.now(UTC))


def test_load_calendar_pages_is_offline_dedupes_and_digests(tmp_path):
    days = weekdays(date(2021, 1, 4), 5)
    row = {"symbol": "aaa", "time": "time-after-hours", "eps": "1.1", "epsForecast": "1.0", "noOfEsts": "3"}
    _write_pages(tmp_path, list(days), {days[1]: [row, row]})
    loaded = load_calendar_pages(tmp_path, days[0], days[-1])
    assert loaded.pages == 5 and loaded.duplicates == 1
    assert [r.date for r in loaded.rows_by_symbol["AAA"]] == [days[1]]
    assert loaded.pages_sha256 == pages_sha256(tmp_path, days[0], days[-1])
    metas = sorted(
        f"{d.isoformat()}:{json.loads((tmp_path / f'{d.isoformat()}.meta.json').read_text())['sha256']}" for d in days
    )
    assert loaded.pages_sha256 == hashlib.sha256("\n".join(metas).encode()).hexdigest()


def test_load_calendar_pages_fails_closed_on_a_missing_page(tmp_path):
    days = weekdays(date(2021, 1, 4), 5)
    _write_pages(tmp_path, list(days[:-1]), {})
    with pytest.raises(ValueError, match=days[-1].isoformat()):
        load_calendar_pages(tmp_path, days[0], days[-1])


def test_load_actions_returns_rows_and_the_acquisitions_content_digest(tmp_path):
    rows = [_dividend("AAA", DAYS[10]).model_dump(mode="json")]
    path = tmp_path / "actions.json"
    path.write_text(json.dumps(rows))
    actions, digest = load_actions(path)
    assert actions[0].symbol == "AAA" and digest == hashlib.sha256(encode(rows).encode()).hexdigest()


def test_verify_event_sources_names_the_mismatching_digest():
    calendar = _calendar({})
    verify_event_sources(INPUTS, calendar, "b" * 64)
    with pytest.raises(ValueError, match="corporate-actions"):
        verify_event_sources(INPUTS, calendar, "c" * 64)
    with pytest.raises(ValueError, match="earnings pages"):
        verify_event_sources(INPUTS, LoadedCalendar({}, "d" * 64, 1, 0), "b" * 64)


def test_dividend_yield_is_invariant_to_a_split_after_t():
    raw_close = np.linspace(200.0, 240.0, len(DAYS))
    adjusted_close = raw_close.copy()
    adjusted_close[:280] /= 4.0  # a 4:1 split on day 280 rescales every earlier adjusted close
    adjusted = {"AAA": daily(DAYS, adjusted_close), "SPY": daily(DAYS, list(np.linspace(300, 330, len(DAYS))))}
    raw = {"AAA": daily(DAYS, raw_close), "SPY": adjusted["SPY"]}
    f = _build(INPUTS, _calendar({}), [_dividend("AAA", DAYS[260], rate=1.0)], adjusted, DAYS, ["AAA"], raw=raw).frames[
        "AAA"
    ]
    assert f["dividend_yield"].iloc[270] == pytest.approx(1.0 / raw_close[270])
    assert f["dividend_yield"].iloc[290] == pytest.approx(0.25 / adjusted_close[290])


def test_dividend_without_a_scale_factor_is_dropped_and_counted():
    adjusted = _world()
    adjusted["AAA"].iloc[:261, adjusted["AAA"].columns.get_loc("Close")] = float("nan")
    built = _build(INPUTS, _calendar({}), [_dividend("AAA", DAYS[260])], adjusted, DAYS, ["AAA"])
    assert built.coverage.unscaled_dividends == 1 and built.frames["AAA"]["dividend_yield"].iloc[270] == 0.0


def test_symbol_without_raw_bars_has_nan_dividend_yield_and_is_reported():
    adjusted = _world()
    built = build_event_frames(INPUTS, _calendar({}), [], adjusted, {"SPY": adjusted["SPY"]}, DAYS, ["AAA"])
    assert built.coverage.symbols_without_raw == ("AAA",)
    assert np.isnan(built.frames["AAA"]["dividend_yield"]).all()


def test_reaction_is_nan_when_the_benchmark_lacks_the_reaction_bar():
    world = _world()
    world["SPY"].iloc[101, world["SPY"].columns.get_loc("Close")] = float("nan")
    f = _build(INPUTS, _calendar({"AAA": [_row("AAA", DAYS[100])]}), [], world, DAYS, ["AAA"]).frames["AAA"]
    assert np.isnan(f["earnings_reaction"].iloc[101]) and f["earnings_age"].iloc[101] == 0.0


def test_overlapping_dividends_sum_and_early_report_has_nan_reaction():
    close = _world()["AAA"]["Close"].to_numpy(float)
    actions = [_dividend("AAA", DAYS[255], rate=0.4), _dividend("AAA", DAYS[260], rate=0.6)]
    f = _build(INPUTS, _calendar({"AAA": [_row("AAA", DAYS[5])]}), actions, _world(), DAYS, ["AAA"]).frames["AAA"]
    assert f["dividend_yield"].iloc[261] == pytest.approx(1.0 / close[261])
    assert f["earnings_age"].iloc[6] == 0.0 and np.isnan(f["earnings_reaction"].iloc[6])
