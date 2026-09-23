import json
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from agentic_trader.cli.main import cli
from agentic_trader.config import WORKSPACE_ROOT, load_config
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider
from agentic_trader.research.setups import runner
from agentic_trader.research.setups.baserates import SetupBaseRateProtocol, execute_baserates
from agentic_trader.research.setups.features import SECTOR_ETF
from agentic_trader.research.setups.labels import BracketHit, BracketOutcome
from agentic_trader.research.setups.replay import SetupRecord
from agentic_trader.research.setups.runner import build_window_frames


# --- Protocol kwargs ----------------------------------------------------------------------


def _protocol_kwargs(**overrides) -> dict:
    fields = {
        "version": "setup_baserates_v1",
        "window": (date(2017, 1, 3), date(2021, 4, 30)),
        "data_cutoff": date(2021, 6, 15),
        "scan_times_et": ("10:35", "14:35"),
        "max_hold_sessions": 20,
        "cost_bps_per_side": (0.0, 5.0),
        "bootstrap": {"block_mean": 10, "draws": 200, "seed": 20260923},
        "feed": "alpaca:sip",
        "adjustment": "all",
        "strategy_config": {"mode": "parallel"},
        "sector_etf": {"technology": "XLK", "etf_broad_equity": "SPY"},
        "hypotheses": "S1: short mean r_cost < 0. S2: long - short mean r_cost > 0.",
        "decision_rule": "If S1 and S2 both hold: suppress native shorts. Otherwise: no change.",
    }
    fields.update(overrides)
    return fields


def _protocol(**overrides) -> SetupBaseRateProtocol:
    return SetupBaseRateProtocol(**_protocol_kwargs(**overrides))


# --- Protocol identity and validation ------------------------------------------------------


def test_protocol_identity_is_stable_and_changes_with_content():
    a = _protocol()
    b = _protocol()
    assert a.identity == b.identity

    c = _protocol(max_hold_sessions=21)
    assert c.identity != a.identity


def test_data_cutoff_must_be_at_least_30_days_after_window_end():
    _protocol(data_cutoff=date(2021, 4, 30) + timedelta(days=30))  # exactly 30 days: allowed

    with pytest.raises(ValidationError, match="data_cutoff"):
        _protocol(data_cutoff=date(2021, 4, 30) + timedelta(days=29))


def test_window_must_be_ordered_and_non_empty():
    with pytest.raises(ValidationError):
        _protocol(window=(date(2021, 4, 30), date(2021, 4, 30)))


def test_committed_protocol_matches_config_and_sector_etf_and_window():
    path = WORKSPACE_ROOT / "config" / "research" / "setup-baserates-short-v1.json"
    data = json.loads(path.read_text())

    with open(WORKSPACE_ROOT / "config" / "config.yaml") as handle:
        config = yaml.safe_load(handle)

    assert data["strategy_config"] == config["strategies"]
    assert data["sector_etf"] == dict(SECTOR_ETF)
    assert data["window"] == ["2017-01-03", "2021-04-30"]
    assert data["data_cutoff"] == "2021-06-15"

    protocol = SetupBaseRateProtocol(**data)
    assert protocol.version == "setup_baserates_v1"
    assert protocol.max_hold_sessions == 20
    assert protocol.cost_bps_per_side[-1] == 5.0
    assert protocol.bootstrap == {"block_mean": 10, "draws": 2000, "seed": 20260923}


# --- Synthetic base-rate computation ------------------------------------------------------


def _synthetic_frame(
    n_sessions: int,
    *,
    seed: int,
    short_mean: float,
    short_std: float,
    long_mean: float,
    long_std: float,
    skip_long_sessions: frozenset[int] = frozenset(),
    skip_short_sessions: frozenset[int] = frozenset(),
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for i in range(n_sessions):
        session = date(2018, 1, 1) + timedelta(days=i)
        decision_at = datetime.combine(session, time(14, 35), tzinfo=UTC)
        if i not in skip_short_sessions:
            r_cost = float(rng.normal(short_mean, short_std))
            rows.append(
                {
                    "decision_at": decision_at,
                    "session": session,
                    "symbol": "AAA",
                    "strategy": "trend_pullback",
                    "timeframe": "1h",
                    "direction": "SHORT",
                    "hit": "target" if r_cost > 0 else "stop",
                    "r": r_cost,
                    "r_cost": r_cost,
                }
            )
        if i not in skip_long_sessions:
            r_cost = float(rng.normal(long_mean, long_std))
            rows.append(
                {
                    "decision_at": decision_at,
                    "session": session,
                    "symbol": "BBB",
                    "strategy": "trend_pullback",
                    "timeframe": "1h",
                    "direction": "LONG",
                    "hit": "target" if r_cost > 0 else "stop",
                    "r": r_cost,
                    "r_cost": r_cost,
                }
            )
    return pd.DataFrame(rows)


def test_planted_negative_short_and_positive_long_suppresses(tmp_path):
    frame = _synthetic_frame(60, seed=7, short_mean=-0.03, short_std=0.004, long_mean=0.03, long_std=0.004)
    protocol = _protocol(bootstrap={"block_mean": 3, "draws": 500, "seed": 7})

    result = execute_baserates(protocol, tmp_path / "out", frame=lambda: frame, environment={})

    assert result["status"] == "completed"
    assert result["s1"]["holds"] is True
    assert result["s1"]["ci90"][1] < 0
    assert result["s2"]["holds"] is True
    assert result["s2"]["ci90"][0] > 0
    assert result["decision"] == "suppress_native_shorts"

    saved = json.loads((tmp_path / "out" / "result.json").read_text())
    assert saved["decision"] == "suppress_native_shorts"
    assert (tmp_path / "out" / "protocol.json").exists()
    assert (tmp_path / "out" / "manifest.json").exists()


def test_noise_gives_no_change(tmp_path):
    frame = _synthetic_frame(30, seed=11, short_mean=0.0, short_std=0.05, long_mean=0.0, long_std=0.05)
    protocol = _protocol(bootstrap={"block_mean": 3, "draws": 300, "seed": 11})

    result = execute_baserates(protocol, tmp_path / "out", frame=lambda: frame, environment={})

    assert result["status"] == "completed"
    assert result["decision"] == "no_change"
    assert not (result["s1"]["holds"] and result["s2"]["holds"])


def test_pairing_uses_only_sessions_with_both_directions(tmp_path):
    # Sessions 0..4 have both; 5..7 short-only; 8..10 long-only.
    frame = _synthetic_frame(
        11,
        seed=3,
        short_mean=-0.01,
        short_std=0.001,
        long_mean=0.01,
        long_std=0.001,
        skip_long_sessions=frozenset({5, 6, 7}),
        skip_short_sessions=frozenset({8, 9, 10}),
    )
    protocol = _protocol(bootstrap={"block_mean": 2, "draws": 50, "seed": 3})

    result = execute_baserates(protocol, tmp_path / "out", frame=lambda: frame, environment={})

    assert result["s2"]["n_sessions_paired"] == 5


def test_non_finite_values_saved_as_null(tmp_path):
    # No LONG rows at all: the S2 pairing is empty, so its CI/p are non-finite.
    frame = _synthetic_frame(10, seed=1, short_mean=-0.02, short_std=0.001, long_mean=0.0, long_std=0.0)
    frame = frame[frame["direction"] == "SHORT"].reset_index(drop=True)
    protocol = _protocol(bootstrap={"block_mean": 2, "draws": 50, "seed": 1})

    result = execute_baserates(protocol, tmp_path / "out", frame=lambda: frame, environment={})

    assert result["s2"]["n_sessions_paired"] == 0
    saved = json.loads((tmp_path / "out" / "result.json").read_text())
    assert saved["s2"]["ci90"] == [None, None]
    assert saved["s2"]["p_one_sided"] is None
    assert saved["s2"]["mean_diff"] is None


def test_failure_writes_status_failed(tmp_path):
    def boom():
        raise RuntimeError("synthetic failure")

    protocol = _protocol()
    result = execute_baserates(protocol, tmp_path / "out", frame=boom, environment={})

    assert result["status"] == "failed"
    saved = json.loads((tmp_path / "out" / "result.json").read_text())
    assert saved["status"] == "failed"
    assert "synthetic failure" in saved["error"]
    # protocol.json/manifest.json are saved before the frame() call.
    assert (tmp_path / "out" / "protocol.json").exists()
    assert (tmp_path / "out" / "manifest.json").exists()


# --- build_window_frames --------------------------------------------------------------------


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
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe))
        if timeframe == "1d":
            return _daily_frame(end)
        return _hourly_frame(end)


UNIVERSE = [("AAA", "technology"), ("BBB", "technology"), ("SPY", "etf_broad_equity"), ("XLK", "technology")]
CALENDAR = DeterministicCalendarProvider()


class _SyncFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _SyncExecutor:
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
    monkeypatch.setattr(runner, "ProcessPoolExecutor", _SyncExecutor)
    monkeypatch.setattr(runner, "as_completed", lambda futures: iter(futures))


def _fake_replay_symbol_factory(seen: list[str]):
    def fake_replay_symbol(symbol, daily_all, hourly_all, instants, config, dedup_hours):
        seen.append(symbol)
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


async def test_build_window_frames_labels_only_inside_the_callable_and_only_its_window(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(runner, "replay_symbol", _fake_replay_symbol_factory(seen))
    label_calls: list[float] = []

    def fake_label_bracket(levels, decision_at, hourly, *, max_hold_sessions, cost_bps_per_side=0.0):
        label_calls.append((decision_at, cost_bps_per_side))
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

    monkeypatch.setattr(runner, "label_bracket", fake_label_bracket)
    config = load_config()

    windows = {
        "alpha": (date(2024, 1, 2), date(2024, 1, 5)),
        "beta": (date(2024, 2, 1), date(2024, 2, 5)),
    }
    frames, coverage = await build_window_frames(
        windows,
        UNIVERSE,
        FakeBarSource(),
        CALENDAR,
        tmp_path / "cache",
        config,
        data_cutoff=date(2024, 3, 1),
        scan_times_et=("10:35", "14:35"),
        adjustment="all",
        max_hold_sessions=5,
        cost_bps_per_side=(0.0, 5.0),
        max_workers=2,
    )

    assert set(frames) == {"alpha", "beta"}
    assert coverage["AAA"]["included"] is True
    assert label_calls == []  # nothing labelled before either callable runs

    alpha_frame = frames["alpha"]()
    assert len(label_calls) > 0
    assert not alpha_frame.empty
    alpha_start, alpha_end = windows["alpha"]
    assert alpha_frame["session"].between(alpha_start, alpha_end).all()

    before_beta = len(label_calls)
    beta_frame = frames["beta"]()
    assert len(label_calls) > before_beta
    assert not beta_frame.empty
    beta_start, beta_end = windows["beta"]
    assert beta_frame["session"].between(beta_start, beta_end).all()
    # Disjoint: no beta session leaks into the alpha frame or vice versa.
    assert not alpha_frame["session"].between(beta_start, beta_end).any()
    assert not beta_frame["session"].between(alpha_start, alpha_end).any()


# --- CLI --------------------------------------------------------------------------------


def _protocol_document(**overrides) -> dict:
    doc = _protocol_kwargs(**overrides)
    doc["window"] = [d.isoformat() for d in doc["window"]]
    doc["data_cutoff"] = doc["data_cutoff"].isoformat()
    doc["sector_etf"] = dict(SECTOR_ETF)
    doc["strategy_config"] = load_config().strategies.model_dump(mode="json")
    return doc


def test_cli_setup_baserates_refuses_existing_output(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))

    existing_output = tmp_path / "existing"
    existing_output.mkdir()
    result = CliRunner().invoke(cli, ["alpha", "setup-baserates", str(protocol_path), "--output", str(existing_output)])
    assert result.exit_code != 0
    assert "already exists" in result.output
