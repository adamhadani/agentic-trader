from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import pytest

from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData, MarketDataFetcher
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.setups.replay import (
    LIVE_DAILY_LOOKBACK_DAYS,
    LIVE_HOURLY_DAYS,
    SetupRecord,
    decision_instants,
    frames_at,
    live_daily_window,
    replay_symbol,
)
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.strategies import StrategyEngine


def daily_frame(n: int, end_date: date, start_price: float = 100.0) -> pd.DataFrame:
    """``n`` consecutive calendar days of daily bars, ending on ``end_date`` (inclusive)."""
    dates = pd.date_range(end=pd.Timestamp(end_date, tz="UTC"), periods=n, freq="1D")
    closes = [start_price + i * 0.1 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": [c - 0.05 for c in closes],
            "High": [c + 0.2 for c in closes],
            "Low": [c - 0.2 for c in closes],
            "Close": closes,
            "Volume": [1000.0 + i for i in range(n)],
        },
        index=pd.DatetimeIndex(dates, name="timestamp"),
    )


def hourly_frame(starts_et: list[datetime], start_price: float = 100.0) -> pd.DataFrame:
    """Hourly bars whose index is each bar's *start* time (UTC), from ET wall-clock starts."""
    index = pd.DatetimeIndex([dt.astimezone(UTC) for dt in starts_et], name="timestamp")
    closes = [start_price + i * 0.01 for i in range(len(starts_et))]
    return pd.DataFrame(
        {
            "Open": [c - 0.01 for c in closes],
            "High": [c + 0.02 for c in closes],
            "Low": [c - 0.02 for c in closes],
            "Close": closes,
            "Volume": [100.0 + i for i in range(len(starts_et))],
        },
        index=index,
    )


def daily_rows(pairs: list[tuple[date, float]]) -> pd.DataFrame:
    """Explicit (session_date, close) daily rows, tz-aware UTC index like providers.py."""
    dates = [pd.Timestamp(d, tz="UTC") for d, _ in pairs]
    closes = [c for _, c in pairs]
    return pd.DataFrame(
        {
            "Open": [c - 0.05 for c in closes],
            "High": [c + 0.2 for c in closes],
            "Low": [c - 0.2 for c in closes],
            "Close": closes,
            "Volume": [1000.0 for _ in closes],
        },
        index=pd.DatetimeIndex(dates, name="timestamp"),
    )


def hourly_rows(pairs: list[tuple[datetime, float]]) -> pd.DataFrame:
    """Explicit (bar_start_et, close) hourly rows."""
    index = pd.DatetimeIndex([dt.astimezone(UTC) for dt, _ in pairs], name="timestamp")
    closes = [c for _, c in pairs]
    return pd.DataFrame(
        {
            "Open": [c - 0.01 for c in closes],
            "High": [c + 0.02 for c in closes],
            "Low": [c - 0.02 for c in closes],
            "Close": closes,
            "Volume": [100.0 for _ in closes],
        },
        index=index,
    )


def et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET_TZ)


def make_candidate(
    contract: str = "AAPL",
    strategy: str = "TREND_PULLBACK",
    timeframe: str = "4h",
    direction: str = "LONG",
    price: float = 100.0,
    setup_quality: float = 0.5,
) -> ScreenerCandidate:
    return ScreenerCandidate(
        contract=contract,
        timeframe=timeframe,
        strategy=strategy,
        direction=direction,
        current_price=price,
        ema_20=price,
        ema_50=price - 1,
        ema_200=price - 2,
        rsi_14=50.0,
        atr_14=2.0,
        candle_timestamp="2026-09-01T14:00:00Z",
        recent_swing_low=price - 5,
        recent_swing_high=price + 5,
        trigger_detail="test",
        setup_quality=setup_quality,
    )


# ---------------------------------------------------------------------------
# decision_instants
# ---------------------------------------------------------------------------


def test_decision_instants_convert_ny_and_drop_after_early_close():
    normal_est = MarketCalendarDay(
        date=date(2026, 3, 5),  # before 2026 US DST starts (Mar 8): EST, UTC-5
        is_trading_day=True,
        is_early_close=False,
        open_time=time(9, 30),
        close_time=time(16, 0),
    )
    normal_edt = MarketCalendarDay(
        date=date(2026, 3, 9),  # after DST starts: EDT, UTC-4
        is_trading_day=True,
        is_early_close=False,
        open_time=normal_est.open_time,
        close_time=normal_est.close_time,
    )
    early_close = MarketCalendarDay(
        date=date(2026, 11, 27),
        is_trading_day=True,
        is_early_close=True,
        open_time=normal_est.open_time,
        close_time=time(13, 0),
    )
    non_trading = MarketCalendarDay(date=date(2026, 3, 7), is_trading_day=False, is_early_close=False)

    instants = decision_instants([normal_est, normal_edt, early_close, non_trading], ["10:35", "14:35"])

    assert instants == [
        et(2026, 3, 5, 10, 35).astimezone(UTC),
        et(2026, 3, 5, 14, 35).astimezone(UTC),
        et(2026, 3, 9, 10, 35).astimezone(UTC),
        et(2026, 3, 9, 14, 35).astimezone(UTC),
        et(2026, 11, 27, 10, 35).astimezone(UTC),
    ]
    # DST offsets differ by an hour in UTC for the same ET wall-clock time.
    assert instants[0].hour == 15  # EST: 10:35 ET == 15:35 UTC
    assert instants[2].hour == 14  # EDT: 10:35 ET == 14:35 UTC
    # Early close drops the 14:35 scan entirely (13:00 close < 14:35).
    assert len(instants) == 5


# ---------------------------------------------------------------------------
# frames_at
# ---------------------------------------------------------------------------


def test_frames_at_hides_bars_ending_after_t():
    day = date(2026, 9, 15)
    bar_10 = et(2026, 9, 15, 10, 0)
    hourly_all = hourly_frame([bar_10])
    daily_all = daily_frame(10, day - timedelta(days=1))

    at_10_35 = frames_at("AAPL", daily_all, hourly_all, et(2026, 9, 15, 10, 35).astimezone(UTC))
    assert len(at_10_35.hourly) == 0  # bar ends 11:00 > 10:35

    at_11_00 = frames_at("AAPL", daily_all, hourly_all, et(2026, 9, 15, 11, 0).astimezone(UTC))
    assert len(at_11_00.hourly) == 1  # bar ends 11:00 <= 11:00
    assert at_11_00.hourly.index[0] == bar_10.astimezone(UTC)


def test_frames_at_partial_4h_bucket_matches_live_resample():
    day = date(2026, 9, 15)
    starts = [et(2026, 9, 15, h, 0) for h in (7, 8, 9, 10)]
    hourly_all = hourly_frame(starts)
    daily_all = daily_frame(10, day - timedelta(days=1))
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)

    result = frames_at("AAPL", daily_all, hourly_all, t)

    hourly_index = pd.DatetimeIndex(hourly_all.index)
    mask = (hourly_index + pd.Timedelta(hours=1)) <= t
    expected_slice = hourly_all.loc[mask]
    fetcher = MarketDataFetcher(provider=object())
    expected = fetcher.compute_intraday_indicators(fetcher.resample_to_4h(expected_slice))

    pd.testing.assert_frame_equal(result.four_hour, expected)


def test_frames_at_in_progress_daily_bar():
    day = date(2026, 9, 15)
    daily_all = daily_frame(10, day - timedelta(days=1), start_price=200.0)
    starts = [et(2026, 9, 15, h, 0) for h in (8, 9)]  # bars ending 9:00 and 10:00 ET, both closed by 10:35
    hourly_all = hourly_frame(starts, start_price=300.0)
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)

    result = frames_at("AAPL", daily_all, hourly_all, t)

    last_row = result.daily.iloc[-1]
    assert pd.DatetimeIndex(result.daily.index).date[-1] == day
    assert last_row["Open"] == pytest.approx(hourly_all["Open"].iloc[0])
    assert last_row["High"] == pytest.approx(hourly_all["High"].max())
    assert last_row["Low"] == pytest.approx(hourly_all["Low"].min())
    assert last_row["Close"] == pytest.approx(hourly_all["Close"].iloc[-1])
    assert last_row["Volume"] == pytest.approx(hourly_all["Volume"].sum())
    # The prior 10 sessions are all present, strictly before day.
    assert pd.DatetimeIndex(result.daily.index).date[-2] == day - timedelta(days=1)


def test_frames_at_no_in_progress_bar_when_none_closed():
    day = date(2026, 9, 15)
    daily_all = daily_frame(10, day - timedelta(days=1))
    hourly_all = hourly_frame([et(2026, 9, 15, 10, 0)])
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)  # bar hasn't closed yet

    result = frames_at("AAPL", daily_all, hourly_all, t)

    assert len(result.daily) == len(daily_all)
    assert pd.DatetimeIndex(result.daily.index).date[-1] == day - timedelta(days=1)


def test_frames_at_daily_window_length_matches_365_day_lookback():
    """Exact completed-session window for two instants straddling a year boundary,
    computed independently of replay.py's own helpers: providers.py fetches
    ``period="1y"`` as ``start = now(UTC) - timedelta(days=365)`` and Alpaca returns
    bars *stamped* at or after ``start``. A session-dated bar for the cutoff date is
    stamped at its midnight, before ``start``, so live never sees it -- for UTC-midnight
    and New York-midnight (Alpaca's actual) stamps alike."""
    for t_et in (et(2026, 9, 15, 10, 35), et(2026, 1, 5, 10, 35)):
        t = t_et.astimezone(UTC)
        ny_date = t_et.date()
        cutoff_date = (t - timedelta(days=365)).astimezone(ET_TZ).date()
        assert (ny_date - cutoff_date).days == 365

        utc_midnight = daily_frame(800, ny_date - timedelta(days=1))  # comfortably covers >365d back
        ny_midnight = utc_midnight.set_axis(
            pd.DatetimeIndex(
                [
                    pd.Timestamp(datetime.combine(d, time(0), tzinfo=ET_TZ)).tz_convert(UTC)
                    for d in utc_midnight.index.date
                ],
                name="timestamp",
            )
        )
        for daily_all in (utc_midnight, ny_midnight):
            result = frames_at("AAPL", daily_all, hourly_frame([]), t)

            stamps = pd.DatetimeIndex(daily_all.index)
            expected = daily_all.index[(stamps >= t - timedelta(days=365)) & (stamps.date < ny_date)]
            pd.testing.assert_index_equal(pd.DatetimeIndex(result.daily.index), pd.DatetimeIndex(expected))
            assert len(result.daily) == 364
            assert pd.DatetimeIndex(result.daily.index).date[0] == cutoff_date + timedelta(days=1)
            assert pd.DatetimeIndex(result.daily.index).date[-1] == ny_date - timedelta(days=1)


def test_live_daily_window_is_the_period_1y_fetch_at_t():
    t = et(2026, 9, 15, 14, 35).astimezone(UTC)
    start = t - timedelta(days=365)
    stamps = [
        start - timedelta(microseconds=1),  # just before start: excluded
        start,  # exactly start: included (Alpaca's start is inclusive)
        t,  # a bar stamped at t itself: included
        t + timedelta(microseconds=1),  # after t: not yet fetchable
    ]
    frame = pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0]}, index=pd.DatetimeIndex(stamps, name="timestamp"))

    assert live_daily_window(frame, t)["Close"].tolist() == [2.0, 3.0]
    assert live_daily_window(frame.iloc[0:0], t).empty


def test_frames_at_in_progress_bar_stamped_at_ny_midnight():
    day = date(2026, 9, 15)  # EDT: NY midnight == 04:00 UTC, not 00:00 UTC
    daily_all = daily_frame(5, day - timedelta(days=1))  # tz-aware UTC index, like providers.py
    hourly_all = hourly_frame([et(2026, 9, 15, 9, 0)])
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)

    result = frames_at("AAPL", daily_all, hourly_all, t)

    result_index = pd.DatetimeIndex(result.daily.index)
    assert result_index.tz is not None
    assert str(result_index.tz) == str(pd.DatetimeIndex(daily_all.index).tz)  # concat stayed tz-consistent

    stamp = result_index[-1]
    expected = datetime.combine(day, time(0, 0), tzinfo=ET_TZ).astimezone(UTC)
    assert stamp == pd.Timestamp(expected)
    assert stamp.hour == 4  # NY midnight in EDT, not UTC midnight


# ---------------------------------------------------------------------------
# replay_symbol
# ---------------------------------------------------------------------------


def _rich_history(last_session_day: date, days: int = LIVE_DAILY_LOOKBACK_DAYS + 5):
    daily_all = daily_frame(days, last_session_day - timedelta(days=1), start_price=150.0)
    today_starts = [et(last_session_day.year, last_session_day.month, last_session_day.day, h, 0) for h in (9, 10)]
    hourly_all = hourly_frame(today_starts, start_price=150.0)
    return daily_all, hourly_all


def test_replay_uses_live_strategy_engine(config, monkeypatch):
    daily_all, hourly_all = _rich_history(date(2026, 9, 15))
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)

    calls = []

    def spy(self, data, asset_class=AssetClass.FUTURES, **kwargs):
        calls.append((data, asset_class))
        return []

    monkeypatch.setattr(StrategyEngine, "scan_contract", spy)

    replay_symbol("AAPL", daily_all, hourly_all, [t], config, dedup_hours=12)

    assert len(calls) == 1
    data, asset_class = calls[0]
    assert isinstance(data, ContractMarketData)
    assert data.contract == "AAPL"
    assert asset_class == AssetClass.EQUITY


def test_out_of_window_perturbation_does_not_change_setups(config, monkeypatch):
    """A correct implementation must be invariant to values living entirely
    outside its declared windows. This directly exercises the four historical
    off-by-one leaks: ``<=`` (not ``<``) on the daily cutoff, ``bar_start<=t``
    (not ``bar_end<=t``) on the hourly cutoff, and dropping either lower bound
    (60d hourly / 365d daily). If any leaks a row in, perturbing that row's
    value moves a captured stat derived from daily, hourly AND four_hour (and,
    through the spy's price, the final ``SetupRecord`` too); perturbing a
    correctly-excluded row must change nothing.
    """
    day = date(2026, 9, 15)
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)
    window_start = t - timedelta(days=LIVE_DAILY_LOOKBACK_DAYS)
    cutoff_date = window_start.astimezone(ET_TZ).date()

    daily_all = daily_rows(
        [
            (date(2025, 9, 10), 111.0),  # stale: before the 365d cutoff
            (cutoff_date, 115.0),  # the cutoff date: stamped at its midnight, before t - 365d
            (cutoff_date + timedelta(days=1), 120.0),  # the first session stamped after t - 365d
            (date(2026, 9, 10), 150.0),
            (date(2026, 9, 14), 151.0),  # the last completed session, strictly before ny_date
            (date(2026, 9, 15), 999.0),  # == ny_date: must never be "completed"
            (date(2026, 9, 16), 1000.0),  # a later session
        ]
    )
    hourly_all = hourly_rows(
        [
            (et(2026, 6, 1, 9, 0), 222.0),  # stale: more than 60d before t
            (et(2026, 9, 14, 15, 0), 140.0),
            (et(2026, 9, 15, 9, 0), 145.0),  # closes 10:00: the freshest correctly-closed bar
            (et(2026, 9, 15, 10, 0), 333.0),  # still open at t (closes 11:00): must be excluded
            (et(2026, 9, 15, 14, 0), 888.0),  # a later session
        ]
    )

    captured: list[tuple] = []

    def spy(self, data, asset_class=AssetClass.EQUITY, **kwargs):
        daily_sum = float(data.daily["Close"].sum())
        hourly_sum = float(data.hourly["Close"].sum()) if len(data.hourly) else 0.0
        four_hour_sum = float(data.four_hour["Close"].sum()) if len(data.four_hour) else 0.0
        captured.append(
            (
                len(data.daily),
                float(data.daily["Close"].iloc[-1]),
                daily_sum,
                len(data.hourly),
                hourly_sum,
                len(data.four_hour),
                four_hour_sum,
            )
        )
        # Derived from all three frames, so a leak anywhere reaches the final SetupRecord too.
        price = daily_sum + hourly_sum * 1e-6 + four_hour_sum * 1e-9
        return [make_candidate(price=price)]

    monkeypatch.setattr(StrategyEngine, "scan_contract", spy)

    baseline = replay_symbol("AAPL", daily_all, hourly_all, [t], config, dedup_hours=12)
    baseline_stats = captured.pop()

    future_daily = daily_all.copy()
    future_daily_mask = pd.DatetimeIndex(future_daily.index).date >= day
    future_daily.loc[future_daily_mask, ["Open", "High", "Low", "Close"]] *= 1.5

    future_hourly = hourly_all.copy()
    future_hourly_mask = (pd.DatetimeIndex(future_hourly.index) + pd.Timedelta(hours=1)) > t
    future_hourly.loc[future_hourly_mask, ["Open", "High", "Low", "Close"]] *= 1.5

    future_perturbed = replay_symbol("AAPL", future_daily, future_hourly, [t], config, dedup_hours=12)
    future_stats = captured.pop()

    stale_daily = daily_all.copy()
    stale_daily_mask = pd.DatetimeIndex(stale_daily.index) < window_start
    stale_daily.loc[stale_daily_mask, ["Open", "High", "Low", "Close"]] *= 1.5

    stale_hourly = hourly_all.copy()
    stale_hourly_mask = pd.DatetimeIndex(stale_hourly.index) < (t - timedelta(days=LIVE_HOURLY_DAYS))
    stale_hourly.loc[stale_hourly_mask, ["Open", "High", "Low", "Close"]] *= 1.5

    stale_perturbed = replay_symbol("AAPL", stale_daily, stale_hourly, [t], config, dedup_hours=12)
    stale_stats = captured.pop()

    assert baseline_stats == future_stats == stale_stats
    assert baseline == future_perturbed == stale_perturbed
    assert len(baseline) == 1


def test_dedup_boundary_exactly_4h_suppressed_for_hourly_timeframe(config, monkeypatch):
    """The live suggestion-scan gap itself (10:35 -> 14:35 same day) is exactly
    4 hours; a 1h-timeframe setup's effective dedup window is min(dedup_hours, 4),
    so the boundary is inclusive and the 14:35 repeat must be suppressed."""
    day = date(2026, 9, 15)
    daily_all, hourly_all = _rich_history(day)

    monkeypatch.setattr(
        StrategyEngine,
        "scan_contract",
        lambda self, data, asset_class=AssetClass.EQUITY, **kwargs: [make_candidate(timeframe="1h")],
    )

    first = et(2026, 9, 15, 10, 35).astimezone(UTC)
    second = et(2026, 9, 15, 14, 35).astimezone(UTC)
    assert second - first == timedelta(hours=4)

    records = replay_symbol("AAPL", daily_all, hourly_all, [first, second], config, dedup_hours=12)

    assert [r.decision_at for r in records] == [first]


def test_dedup_matches_live_rule(config, monkeypatch):
    day = date(2026, 9, 15)
    daily_all, hourly_all = _rich_history(day)

    monkeypatch.setattr(
        StrategyEngine,
        "scan_contract",
        lambda self, data, asset_class=AssetClass.EQUITY, **kwargs: [make_candidate(timeframe="1h")],
    )

    base = et(2026, 9, 15, 10, 35).astimezone(UTC)
    instants = [base, base + timedelta(hours=3), base + timedelta(hours=5)]
    # dedup_hours=12, but timeframe=1h narrows the effective window to min(12, 4) = 4.
    records = replay_symbol("AAPL", daily_all, hourly_all, instants, config, dedup_hours=12)

    assert [r.decision_at for r in records] == [base, base + timedelta(hours=5)]


def test_replay_skips_when_daily_history_shorter_than_lookback(config, monkeypatch):
    day = date(2026, 9, 15)
    # History starts a few days later than the 365d lookback requires.
    daily_all, hourly_all = _rich_history(day, days=LIVE_DAILY_LOOKBACK_DAYS - 5)
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)
    cutoff_instant = t - timedelta(days=LIVE_DAILY_LOOKBACK_DAYS)
    assert pd.DatetimeIndex(daily_all.index).min() > cutoff_instant  # confirm the fixture is actually short

    monkeypatch.setattr(
        StrategyEngine,
        "scan_contract",
        lambda self, data, asset_class=AssetClass.EQUITY, **kwargs: [make_candidate()],
    )

    records = replay_symbol("AAPL", daily_all, hourly_all, [t], config, dedup_hours=12)

    assert records == []


def test_replay_produces_setup_record_from_levels(config, monkeypatch):
    day = date(2026, 9, 15)
    daily_all, hourly_all = _rich_history(day)
    t = et(2026, 9, 15, 10, 35).astimezone(UTC)

    monkeypatch.setattr(
        StrategyEngine,
        "scan_contract",
        lambda self, data, asset_class=AssetClass.EQUITY, **kwargs: [make_candidate(price=150.0)],
    )

    records = replay_symbol("AAPL", daily_all, hourly_all, [t], config, dedup_hours=12)

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, SetupRecord)
    assert record.symbol == "AAPL"
    assert record.decision_at == t
    assert record.entry == pytest.approx(150.0)
    assert record.stop < record.entry < record.target
    assert record.atr == pytest.approx(2.0)


def test_live_hourly_days_and_daily_lookback_constants():
    assert LIVE_DAILY_LOOKBACK_DAYS == 365
    assert LIVE_HOURLY_DAYS == 60
