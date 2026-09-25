import json
import math
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.config import load_config
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider
from agentic_trader.research.setups import features, runner
from agentic_trader.research.setups.features import CROSS_SECTIONAL, MARKET, SECTOR_ETF, cross_section
from agentic_trader.research.setups.labels import BracketHit, BracketOutcome
from agentic_trader.research.setups.ranker import live_cross_section, setup_features
from agentic_trader.research.setups.replay import SetupRecord
from agentic_trader.research.setups.runner import _fetch_cached, build_setup_frames
from agentic_trader.research.setups.study import SetupStudyProtocol


# --- Test doubles -----------------------------------------------------------------------


def _daily_frame(end: datetime, days: int = 30) -> pd.DataFrame:
    end_ts = pd.Timestamp(end.date(), tz="UTC")
    dates = pd.date_range(end=end_ts, periods=days, freq="1D")
    closes = [100.0 + i * 0.1 for i in range(days)]
    return pd.DataFrame(
        {
            "Open": [c - 0.05 for c in closes],
            "High": [c + 0.2 for c in closes],
            "Low": [c - 0.2 for c in closes],
            "Close": closes,
            "Volume": [1000.0 + i for i in range(days)],
        },
        index=pd.DatetimeIndex(dates, name="timestamp"),
    )


def _hourly_frame(end: datetime, days: int = 10) -> pd.DataFrame:
    end_date = end.date()
    starts: list[datetime] = []
    d = end_date - timedelta(days=days)
    while d <= end_date:
        if d.weekday() < 5:
            starts.extend(datetime.combine(d, time(hour, 0), tzinfo=ET_TZ) for hour in (10, 11, 12, 13, 14, 15))
        d += timedelta(days=1)
    index = pd.DatetimeIndex([s.astimezone(UTC) for s in starts], name="timestamp")
    closes = [100.0 + i * 0.01 for i in range(len(index))]
    return pd.DataFrame(
        {
            "Open": [c - 0.01 for c in closes],
            "High": [c + 0.05 for c in closes],
            "Low": [c - 0.05 for c in closes],
            "Close": closes,
            "Volume": [500.0 for _ in closes],
        },
        index=index,
    )


class FakeBarSource:
    """Deterministic synthetic bars; tracks calls and can simulate per-symbol failures."""

    def __init__(self, *, fail: frozenset[str] = frozenset()):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, adjustment: str
    ) -> pd.DataFrame:
        self.calls.append((symbol, timeframe))
        if symbol in self.fail:
            raise RuntimeError(f"synthetic fetch failure for {symbol}")
        if timeframe == "1d":
            return _daily_frame(end)
        return _hourly_frame(end)


SYMBOLS = ("AAA", "BBB", "CCC", "DDD")
UNIVERSE = [(s, "technology") for s in SYMBOLS] + [("SPY", "etf_broad_equity"), ("XLK", "technology")]


class _SyncFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _SyncExecutor:
    """In-process stand-in for ProcessPoolExecutor: no pickling, no real subprocess."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args, **kwargs):
        return _SyncFuture(fn(*args, **kwargs))


@pytest.fixture(autouse=True)
def sync_process_pool(monkeypatch):
    """Every test runs replay in-process; picklability of production args is a
    separate, deliberate contract documented on ``build_setup_frames``/``replay_symbol``."""
    monkeypatch.setattr(runner, "ProcessPoolExecutor", _SyncExecutor)
    monkeypatch.setattr(runner, "as_completed", lambda futures: iter(futures))


def _protocol_kwargs(**overrides) -> dict:
    fields = {
        "version": "setup_outcomes_v1",
        "universe_source": "test universe",
        "feed": "alpaca:sip",
        "adjustment": "all",
        "scan_times_et": ("10:35", "14:35"),
        "max_hold_sessions": 5,
        "cost_bps_per_side": (0.0, 5.0),
        "development": (date(2024, 1, 2), date(2024, 1, 12)),
        "holdout": (date(2024, 2, 1), date(2024, 2, 9)),
        "data_cutoff": date(2024, 3, 1),
        "embargo_sessions": 1,
        "bootstrap": {"block_mean": 2, "draws": 10, "seed": 1},
        "cv_folds": 2,
        "hgb_params": {
            "max_depth": 2,
            "learning_rate": 0.2,
            "max_iter": 10,
            "min_samples_leaf": 2,
            "random_state": 1,
        },
        "ridge_alpha": 1.0,
        "logistic_C": 1.0,
        "acceptance": {"top_k": 2, "min_sessions": 1, "ci": 0.90},
        "features_version": "setup_features_v2",
        "sector_etf": {"technology": "XLK", "etf_broad_equity": "SPY"},
        "strategy_config": {"mode": "parallel"},
    }
    fields.update(overrides)
    return fields


def _protocol(**overrides) -> SetupStudyProtocol:
    return SetupStudyProtocol(**_protocol_kwargs(**overrides))


def _fake_replay_symbol_factory(instants_by_symbol_seen: list[str]):
    def fake_replay_symbol(symbol, daily_all, hourly_all, instants, config, dedup_hours):
        instants_by_symbol_seen.append(symbol)
        return [
            SetupRecord(
                decision_at=t,
                symbol=symbol,
                strategy="trend_pullback",
                timeframe="1h",
                direction="LONG",
                setup_quality=0.7,
                entry=100.0,
                stop=98.0,
                target=104.0,
                atr=1.0,
            )
            for t in instants
        ]

    return fake_replay_symbol


def _fake_label_bracket_factory(calls: list[float]):
    def fake_label_bracket(levels, decision_at, hourly, *, max_hold_sessions, cost_bps_per_side=0.0):
        calls.append(cost_bps_per_side)
        return BracketOutcome(
            hit=BracketHit.TARGET,
            entry_time=decision_at,
            entry_price=100.0,
            exit_time=decision_at,
            exit_price=104.0,
            r=1.0,
            r_cost=1.0 - cost_bps_per_side / 1000.0,
            holding_sessions=1,
        )

    return fake_label_bracket


CALENDAR = DeterministicCalendarProvider()


# --- Tests ------------------------------------------------------------------------------


async def test_missing_symbol_excluded_before_labelling(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory(seen))

    bars = FakeBarSource(fail=frozenset({"CCC"}))
    config = load_config()
    protocol = _protocol()

    _development, _holdout, coverage = await build_setup_frames(
        protocol, UNIVERSE, bars, CALENDAR, tmp_path / "cache", config, max_workers=2
    )

    assert coverage["CCC"]["included"] is False
    assert coverage["CCC"]["reason"] is not None
    assert "CCC" not in seen
    for symbol in ("AAA", "BBB", "DDD", "SPY", "XLK"):
        assert coverage[symbol]["included"] is True
        assert symbol in seen


async def test_cache_reused_without_refetch(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory(seen))
    config = load_config()
    protocol = _protocol()
    cache_dir = tmp_path / "cache"

    first_source = FakeBarSource()
    await build_setup_frames(protocol, UNIVERSE, first_source, CALENDAR, cache_dir, config, max_workers=2)
    assert {call for call in first_source.calls} == {(s, tf) for s, _ in UNIVERSE for tf in ("1d", "1h")}

    second_source = FakeBarSource()
    await build_setup_frames(protocol, UNIVERSE, second_source, CALENDAR, cache_dir, config, max_workers=2)
    assert second_source.calls == []  # every frame served from the dataset cache


async def test_labels_only_computed_inside_callables(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory(seen))
    label_calls: list[float] = []
    monkeypatch.setattr(runner, "label_bracket", _fake_label_bracket_factory(label_calls))
    config = load_config()
    protocol = _protocol()

    development, holdout, _coverage = await build_setup_frames(
        protocol, UNIVERSE, FakeBarSource(), CALENDAR, tmp_path / "cache", config, max_workers=2
    )
    assert label_calls == []

    dev_frame = development()
    assert len(label_calls) > 0
    assert not dev_frame.empty

    before_holdout = len(label_calls)
    hold_frame = holdout()
    assert len(label_calls) > before_holdout
    assert not hold_frame.empty


async def test_holdout_callable_only_labels_holdout_window(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory(seen))
    monkeypatch.setattr(runner, "label_bracket", _fake_label_bracket_factory([]))
    config = load_config()
    protocol = _protocol()

    development, holdout, _coverage = await build_setup_frames(
        protocol, UNIVERSE, FakeBarSource(), CALENDAR, tmp_path / "cache", config, max_workers=2
    )

    dev_frame = development()
    hold_frame = holdout()

    dev_start, dev_end = protocol.development
    hold_start, hold_end = protocol.holdout

    assert not dev_frame.empty and not hold_frame.empty
    assert dev_frame["session"].between(dev_start, dev_end).all()
    assert hold_frame["session"].between(hold_start, hold_end).all()
    # Disjoint windows: no development session leaks into the holdout frame or vice versa.
    assert not hold_frame["session"].between(dev_start, dev_end).any()
    assert not dev_frame["session"].between(hold_start, hold_end).any()

    expected_columns = {
        "decision_at",
        "session",
        "symbol",
        "strategy",
        "timeframe",
        "direction",
        "hit",
        "r",
        "r_cost",
        "setup_quality",
        "stop_atr",
        "reward_risk",
    }
    assert expected_columns.issubset(set(dev_frame.columns))


# --- CLI ----------------------------------------------------------------------------------


def _protocol_document(**overrides) -> dict:
    doc = _protocol_kwargs(**overrides)
    doc["development"] = [d.isoformat() for d in doc["development"]]
    doc["holdout"] = [d.isoformat() for d in doc["holdout"]]
    doc["data_cutoff"] = doc["data_cutoff"].isoformat()
    doc["sector_etf"] = dict(SECTOR_ETF)
    return doc


def test_cli_refuses_existing_output_and_strategy_config_mismatch(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document(strategy_config={"mode": "single"})))

    existing_output = tmp_path / "existing"
    existing_output.mkdir()
    result = CliRunner().invoke(cli, ["alpha", "setup-study", str(protocol_path), "--output", str(existing_output)])
    assert result.exit_code != 0
    assert "already exists" in result.output

    fresh_output = tmp_path / "fresh"
    result = CliRunner().invoke(cli, ["alpha", "setup-study", str(protocol_path), "--output", str(fresh_output)])
    assert result.exit_code != 0
    assert "strategy_config" in result.output
    assert not fresh_output.exists()


# --- Study cross-section == live cross-section ---------------------------------------------------
#
# Daily bars below are Alpaca-shaped: one per business day, stamped at New York midnight
# in UTC (04:00Z/05:00Z). The "live" side is built independently of runner/replay helpers:
# providers.py fetches period="1y" as start = now(UTC) - timedelta(days=365), and Alpaca
# returns every bar stamped at or after start -- including today's in-progress bar.

LIVE_LOOKBACK = timedelta(days=365)
PANEL_SECTORS = {
    "AAA": "technology",
    "BBB": "technology",
    "CCC": "financial_services",
    "DDD": "financial_services",
    "NEW": "technology",  # listed ~10 months before the decision: short history
    "XLK": "technology",
    "XLF": "financial_services",
    "SPY": "etf_broad_equity",
}
DECISION_DATE = date(2025, 6, 12)  # a Thursday
SCANS = tuple(datetime.combine(DECISION_DATE, time(h, 35), tzinfo=ET_TZ).astimezone(UTC) for h in (10, 14))


def _ny_midnight_utc(day: date) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, time(0, 0), tzinfo=ET_TZ)).tz_convert("UTC")


def _alpaca_daily(seed: int, days: list[date]) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 50.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(days))))
    return pd.DataFrame(
        {
            "Open": close * (1 + rng.normal(0, 0.002, len(days))),
            "High": close * (1 + np.abs(rng.normal(0, 0.01, len(days)))),
            "Low": close * (1 - np.abs(rng.normal(0, 0.01, len(days)))),
            "Close": close,
            "Volume": rng.uniform(1e5, 5e6, len(days)),
        },
        index=pd.DatetimeIndex([_ny_midnight_utc(day) for day in days], name="timestamp"),
    )


def _study_panel() -> tuple[dict[str, pd.DataFrame], list[date]]:
    """Two years of full history (and a month *after* the decision), as the study caches it."""
    trading_days = [d.date() for d in pd.bdate_range("2023-05-01", "2025-07-15")]
    daily = {symbol: _alpaca_daily(seed, trading_days) for seed, symbol in enumerate(PANEL_SECTORS)}
    listed = DECISION_DATE - timedelta(days=300)
    daily["NEW"] = daily["NEW"].loc[pd.DatetimeIndex(daily["NEW"].index).date >= listed]
    return daily, trading_days


def _live_fetch(frame: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """What fetch_data(daily_period="1y") returns at ``now``: bars stamped in [now - 365d, now],
    with today's bar replaced by an in-progress one (only part of the session has traded)."""
    stamps = pd.DatetimeIndex(frame.index)
    fetched = frame.loc[(stamps >= now - LIVE_LOOKBACK) & (stamps <= now)].copy()
    today = pd.DatetimeIndex(fetched.index).date == DECISION_DATE
    assert today.sum() == 1
    fetched.loc[today, ["Close", "High"]] *= 1.07
    fetched.loc[today, "Volume"] *= 0.1
    return fetched


RECORDS = [
    ("AAA", "LONG", "TREND_PULLBACK", "4h", 0.7, 100.0, 97.0, 106.0, 1.5),
    ("CCC", "SHORT", "SQUEEZE_BREAKOUT", "1h", 0.4, 50.0, 51.0, 47.5, 0.8),
    ("NEW", "LONG", "TREND_PULLBACK", "1h", 0.55, 20.0, 19.0, 22.0, 0.6),
]


def _records() -> list[SetupRecord]:
    return [
        SetupRecord(
            decision_at=scan,
            symbol=symbol,
            strategy=strategy,
            timeframe=timeframe,
            direction=direction,
            setup_quality=quality,
            entry=entry,
            stop=stop,
            target=target,
            atr=atr,
        )
        for scan in SCANS
        for symbol, direction, strategy, timeframe, quality, entry, stop, target, atr in RECORDS
    ]


def _study_rows(monkeypatch, daily: dict[str, pd.DataFrame], trading_days: list[date]) -> pd.DataFrame:
    monkeypatch.setattr(runner, "label_bracket", _fake_label_bracket_factory([]))
    hourly = {symbol: _hourly_frame(datetime.combine(DECISION_DATE, time(16), tzinfo=UTC)) for symbol in daily}
    protocol = _protocol()
    return runner._build_frame(
        _records(),
        daily,
        hourly,
        PANEL_SECTORS,
        protocol.max_hold_sessions,
        protocol.cost_bps_per_side,
        trading_days,
        {DECISION_DATE: SCANS[0]},
    )


def _live_vector(now: datetime, daily: dict[str, pd.DataFrame], record: tuple) -> dict[str, float]:
    symbol, direction, strategy, timeframe, quality, entry, stop, target, atr = record
    fetched = {s: _live_fetch(frame, now) for s, frame in daily.items()}
    cs = live_cross_section(fetched, PANEL_SECTORS, DECISION_DATE)
    return setup_features(
        cs,
        symbol=symbol,
        direction=direction,
        strategy=strategy,
        timeframe=timeframe,
        setup_quality=quality,
        entry=entry,
        stop=stop,
        target=target,
        atr_14=atr,
    )


def _same(left: float, right: float) -> bool:
    return (math.isnan(left) and math.isnan(right)) or left == right


def test_study_vector_equals_the_live_vector_for_the_same_instant(monkeypatch):
    daily, trading_days = _study_panel()
    rows = _study_rows(monkeypatch, daily, trading_days)
    assert len(rows) == len(SCANS) * len(RECORDS)

    for scan in SCANS:
        for record in RECORDS:
            live = _live_vector(scan, daily, record)
            [row] = rows[(rows["decision_at"] == scan) & (rows["symbol"] == record[0])].to_dict("records")
            mismatched = {k: (row[k], v) for k, v in live.items() if not _same(float(row[k]), float(v))}
            assert not mismatched, (scan, record[0], mismatched)

    # The mature names have every feature: nothing is NaN only because of the live window.
    [aaa] = rows[(rows["decision_at"] == SCANS[0]) & (rows["symbol"] == "AAA")].to_dict("records")
    assert all(math.isfinite(aaa[name]) for name in (*CROSS_SECTIONAL, *MARKET))


def test_a_feature_longer_than_the_live_window_is_nan_in_the_study_as_it_is_live(monkeypatch):
    """A feature needing more rows than any one-year fetch holds (at most 261 weekdays; real
    calendars give ~250, which is why v1's 253-row mom_252_21 was always NaN live) must be
    NaN in the study too, not computed from the longer history the study has cached."""
    original = features._basic_features

    def with_long_probe(frame: pd.DataFrame) -> dict[str, float]:
        values = original(frame)
        close = frame["Close"].to_numpy(dtype=float)
        values["long_probe"] = close[-22] / close[-270] - 1.0 if len(close) >= 270 else float("nan")
        return values

    monkeypatch.setattr(features, "_basic_features", with_long_probe)
    monkeypatch.setattr(features, "CROSS_SECTIONAL", (*CROSS_SECTIONAL, "long_probe"))

    daily, trading_days = _study_panel()
    as_of = trading_days[trading_days.index(DECISION_DATE) - 1]
    # Full cached history would populate it: this test can see the leak.
    assert cross_section(daily, PANEL_SECTORS, as_of).ranks["long_probe"].notna().sum() >= 4

    rows = _study_rows(monkeypatch, daily, trading_days)
    assert rows["long_probe"].isna().all()
    for scan in SCANS:
        for record in RECORDS:
            assert math.isnan(_live_vector(scan, daily, record)["long_probe"])


async def test_build_setup_frames_windows_each_date_at_its_first_scan(tmp_path, monkeypatch):
    """Both scans of a date share one cross-section, windowed at the date's earliest scan."""
    seen: list[str] = []
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory(seen))
    monkeypatch.setattr(runner, "label_bracket", _fake_label_bracket_factory([]))
    windows: list[datetime] = []
    original = runner.live_daily_window

    def spy(frame, t):
        windows.append(t)
        return original(frame, t)

    monkeypatch.setattr(runner, "live_daily_window", spy)
    protocol = _protocol()
    development, _holdout, _coverage = await build_setup_frames(
        protocol, UNIVERSE, FakeBarSource(), CALENDAR, tmp_path / "cache", load_config(), max_workers=2
    )
    frame = development()

    assert not frame.empty
    by_date: dict[date, set[datetime]] = {}
    for t in windows:
        by_date.setdefault(t.astimezone(ET_TZ).date(), set()).add(t)
    assert set(by_date) == set(frame["session"])
    for ny_date, starts in by_date.items():
        [t] = starts  # one window per date, shared by both scans
        decisions = frame.loc[frame["session"] == ny_date, "decision_at"]
        assert decisions.nunique() == 2
        assert t == min(decisions)
        assert t.astimezone(ET_TZ).time() == time(10, 35)


class _RangeRecordingSource(FakeBarSource):
    def __init__(self):
        super().__init__()
        self.ranges: list[tuple[str, datetime, datetime]] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.ranges.append((timeframe, start, end))
        return super().fetch_bars(symbol, timeframe, start, end, adjustment=adjustment)


async def test_long_histories_are_fetched_in_bounded_contiguous_chunks(tmp_path):
    # The raw-evidence store caps one acquisition at 100 pages, which six years of
    # hourly bars for a liquid name exceeds; each request must stay within a year.
    source = _RangeRecordingSource()

    async def no_pace():
        return None

    start, end = datetime(2020, 4, 27, tzinfo=UTC), datetime(2026, 9, 22, tzinfo=UTC)
    frame = await _fetch_cached("AAA", "1h", source, tmp_path, start, end, "all", no_pace)
    spans = [(s, e) for _, s, e in source.ranges]
    assert spans[0][0] == start and spans[-1][1] == end
    assert all(e - s <= timedelta(days=366) for s, e in spans)
    assert all(prev[1] == nxt[0] for prev, nxt in pairwise(spans))
    assert frame.index.is_unique and frame.index.is_monotonic_increasing


async def test_fetch_cached_honours_a_single_chunk(tmp_path):
    calls = []

    class Bars:
        def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
            calls.append((start, end))
            index = pd.DatetimeIndex([start], tz="UTC")
            return pd.DataFrame(
                {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0]}, index=index
            )

    async def pace():
        return None

    start = datetime(2016, 1, 1, tzinfo=UTC)
    end = datetime(2026, 9, 1, tzinfo=UTC)
    await runner._fetch_cached("AAA", "1d", Bars(), tmp_path, start, end, "all", pace, chunk=end - start)
    assert calls == [(start, end)]


class _FlakySource(FakeBarSource):
    """Fails the first request for chosen (symbol, timeframe) pairs, then succeeds."""

    def __init__(self, flaky: set[tuple[str, str]], *, always_fail: set[tuple[str, str]] = frozenset()):
        super().__init__()
        self.flaky = set(flaky)
        self.always_fail = set(always_fail)

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        key = (symbol, timeframe)
        if key in self.always_fail:
            self.calls.append(key)
            raise RuntimeError(f"permanent failure for {key}")
        if key in self.flaky:
            self.flaky.discard(key)
            self.calls.append(key)
            raise RuntimeError(f"transient failure for {key}")
        return super().fetch_bars(symbol, timeframe, start, end, adjustment=adjustment)


async def test_transient_fetch_failures_are_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_trader.research.setups.runner._RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory([]))
    protocol, config = _protocol(), load_config()
    source = _FlakySource({("SPY", "1h")})
    _dev, _hold, coverage = await build_setup_frames(
        protocol, UNIVERSE, source, CALENDAR, tmp_path / "cache", config, max_workers=2
    )
    assert coverage["SPY"]["included"] is True


async def test_daily_only_symbols_still_feed_the_cross_section(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_trader.research.setups.runner._RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory([]))
    protocol, config = _protocol(), load_config()
    source = _FlakySource(set(), always_fail={("XLK", "1h")})
    _dev, _hold, coverage = await build_setup_frames(
        protocol, UNIVERSE, source, CALENDAR, tmp_path / "cache", config, max_workers=2
    )
    assert coverage["XLK"]["included"] is False
    assert coverage["XLK"]["cross_section_only"] is True


async def test_missing_reference_daily_bars_abort_before_labelling(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_trader.research.setups.runner._RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory([]))
    protocol, config = _protocol(), load_config()
    source = _FlakySource(set(), always_fail={("SPY", "1d")})
    with pytest.raises(RuntimeError, match="SPY"):
        await build_setup_frames(protocol, UNIVERSE, source, CALENDAR, tmp_path / "cache", config, max_workers=2)


async def test_cache_records_its_range_and_refuses_a_request_outside_it(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory([]))
    protocol, config = _protocol(), load_config()
    cache = tmp_path / "cache"
    await build_setup_frames(protocol, UNIVERSE, FakeBarSource(), CALENDAR, cache, config, max_workers=2)
    recorded = json.loads((cache / "cache_range.json").read_text())
    assert recorded["start"] <= recorded["end"]

    later = _protocol(data_cutoff=protocol.data_cutoff + timedelta(days=30))
    with pytest.raises(ValueError, match="cache"):
        await build_setup_frames(later, UNIVERSE, FakeBarSource(), CALENDAR, cache, config, max_workers=2)


async def test_non_empty_cache_without_a_recorded_range_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory([]))
    cache = tmp_path / "cache"
    (cache / "AAA_1d").mkdir(parents=True)
    with pytest.raises(ValueError, match="cache_range.json"):
        await build_setup_frames(_protocol(), UNIVERSE, FakeBarSource(), CALENDAR, cache, load_config(), max_workers=2)
