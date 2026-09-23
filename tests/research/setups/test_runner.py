import json
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.config import load_config
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider
from agentic_trader.research.setups import runner
from agentic_trader.research.setups.features import SECTOR_ETF
from agentic_trader.research.setups.labels import BracketHit, BracketOutcome
from agentic_trader.research.setups.replay import SetupRecord
from agentic_trader.research.setups.runner import build_setup_frames
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
        "features_version": "setup_features_v1",
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
    assert len(first_source.calls) == 2 * len(UNIVERSE)  # 1d + 1h per symbol, nothing cached yet

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
