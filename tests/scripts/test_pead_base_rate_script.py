"""The offline PEAD base-rate script: reproduces the study's labels, writes a loadable pinned file, or refuses."""

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_base_rate import RECENT_FROM, load_pead_base_rate
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.apriori.pead_study import label_events, save_frame
from scripts import pead_base_rate


ENTRY_PATH = Path(__file__).resolve().parents[2] / "config/research/apriori/pead-v2.json"
LOADED = load_pead_entry(ENTRY_PATH)
FIRST = date(2025, 6, 2)  # a Monday, inside the recent window


def hourly(prices_by_day: dict[date, list[float]]) -> pd.DataFrame:
    rows = {}
    for day, prices in prices_by_day.items():
        for k, price in enumerate(prices):
            rows[datetime.combine(day, time(9 + k), tzinfo=ET_TZ).astimezone(UTC)] = price
    series = pd.Series(rows, dtype=float).sort_index()
    return pd.DataFrame(
        {"Open": series, "High": series, "Low": series, "Close": series, "Volume": 1000.0},
        index=pd.DatetimeIndex(series.index),
    )


def sessions(n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(FIRST, periods=n)]


def daily(seed: int, market: pd.DataFrame | None = None) -> pd.DataFrame:
    days = pd.bdate_range(end=FIRST - timedelta(days=1), periods=200)
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, 200) if market is None else market["Close"].pct_change().fillna(0).to_numpy() * 1.2
    close = 100 * np.cumprod(1 + base + rng.normal(0, 0.003, 200))
    index = pd.DatetimeIndex(days).tz_localize(ET_TZ).tz_convert(UTC)
    return pd.DataFrame({"Close": close, "Volume": 2_000_000.0}, index=index)


@pytest.fixture
def world(tmp_path):
    days = sessions(25)
    bars = {
        "UP": hourly({d: [100.0] * 7 if i == 0 else [110.0] * 7 for i, d in enumerate(days)}),
        "DOWN": hourly({d: [100.0] * 7 if i == 0 else [90.0] * 7 for i, d in enumerate(days)}),
    }
    cache = tmp_path / "pead-cache"
    spy_d = daily(1)
    for symbol, frame in bars.items():
        save_dataset(frame, cache / "bars" / f"{symbol}_1h", symbol)
        save_dataset(daily({"UP": 2, "DOWN": 3}[symbol], spy_d), cache / "bars" / f"{symbol}_1d", symbol)
    save_dataset(spy_d, cache / "bars" / "SPY_1d", "SPY")
    spy_h = hourly({d: [400.0 + i + k for k in range(7)] for i, d in enumerate(days)})
    first_half, second_half = spy_h.iloc[: 7 * 15], spy_h.iloc[7 * 10 :]  # overlap on five sessions
    spy_a, spy_b = tmp_path / "spy-a", tmp_path / "spy-b"
    save_dataset(first_half, spy_a / "SPY_1h", "a")
    save_dataset(second_half, spy_b / "SPY_1h", "b")
    when = decision_at(FIRST, time(10, 35))
    base = {"session": FIRST, "decision_at": when, "atr": 1.0, "median_dollar_volume": 1e9}
    events = pd.DataFrame([{**base, "symbol": "UP", "leg": "LONG"}, {**base, "symbol": "DOWN", "leg": "SHORT"}])
    labels, _ = label_events(events, bars, LOADED.entry)
    study = tmp_path / "study"
    study.mkdir()
    save_frame(events, study / "events.csv.gz")
    save_frame(labels, study / "labels.csv.gz")
    (study / "protocol.json").write_bytes(ENTRY_PATH.read_bytes())
    (study / "manifest.json").write_text(json.dumps({"sha256": LOADED.sha256}))
    return study, cache, [spy_a, spy_b], labels


def test_script_writes_file_and_prints_digest(world, tmp_path, capsys):
    study, cache, spy, _ = world
    out = tmp_path / "rate.json"
    pead_base_rate.main(study, cache, spy, out)
    digest = capsys.readouterr().out.strip()
    rate = load_pead_base_rate(out, digest)
    assert rate.entry_sha256 == LOADED.sha256
    assert rate.recent_from == RECENT_FROM
    assert (rate.n_leg, rate.n_all, rate.n_no_beta, rate.n_no_market) == (1, 2, 0, 0)
    assert (rate.window_start, rate.window_end) == (date(2016, 8, 1), date(2026, 7, 31))
    assert rate.recent.n == 1 and rate.by_year[0].year == 2025
    assert "events_sha256" in rate.source and not any("/" in v and v.startswith("/") for v in rate.source.values())


def test_script_refuses_when_labels_do_not_reproduce(world, tmp_path):
    study, cache, spy, labels = world
    tampered = labels.copy()
    tampered.loc[tampered["direction"] == "LONG", "r"] += 0.5
    (study / "labels.csv.gz").unlink()
    save_frame(tampered, study / "labels.csv.gz")
    out = tmp_path / "rate.json"
    with pytest.raises(ValueError, match="r mismatch"):
        pead_base_rate.main(study, cache, spy, out)
    assert not out.exists()


def test_script_refuses_a_study_of_a_different_entry(world, tmp_path):
    study, cache, spy, _ = world
    (study / "manifest.json").write_text(json.dumps({"sha256": "0" * 64}))
    with pytest.raises(SystemExit, match="entry digest"):
        pead_base_rate.main(study, cache, spy, tmp_path / "rate.json")
    assert not (tmp_path / "rate.json").exists()


def test_script_refuses_a_listed_spy_cache_without_bars(world, tmp_path):
    study, cache, spy, _ = world
    with pytest.raises(SystemExit, match="missing from a listed cache"):
        pead_base_rate.main(study, cache, [*spy, tmp_path / "typo"], tmp_path / "rate.json")
    assert not (tmp_path / "rate.json").exists()


def test_recorded_source_identifies_the_caches_and_changes_with_their_contents(world, tmp_path, capsys):
    study, cache, spy, _ = world

    def source() -> dict[str, str]:
        out = tmp_path / "rate.json"
        out.unlink(missing_ok=True)
        pead_base_rate.main(study, cache, spy, out)
        return load_pead_base_rate(out, capsys.readouterr().out.strip()).source

    before = source()
    assert before["pead_cache"] == "pead-cache" and before["pead_cache_range_sha256"] == "absent"
    assert before["spy_hourly_caches"] == "spy-a, spy-b"
    (cache / "bars" / "cache_range.json").write_text('{"start": 1}')
    ranged = source()
    assert ranged["pead_cache_range_sha256"] != "absent"
    save_dataset(hourly({FIRST: [100.0] * 7}), cache / "bars" / "UP_1h", "extra")  # a new file for a used symbol
    grown = source()
    assert grown["pead_cache_files_sha256"] != before["pead_cache_files_sha256"]
    assert grown["spy_hourly_files_sha256"] == before["spy_hourly_files_sha256"]
    save_dataset(hourly({FIRST: [400.0] * 7}), spy[0] / "SPY_1h", "extra")
    assert source()["spy_hourly_files_sha256"] != before["spy_hourly_files_sha256"]


def test_spy_cache_argument_expands_a_tilde_after_each_comma(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert pead_base_rate.parse_caches("~/a,~/b") == [tmp_path / "a", tmp_path / "b"]
