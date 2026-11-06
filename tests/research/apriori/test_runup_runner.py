import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agentic_trader.research.apriori import runup_runner
from agentic_trader.research.apriori.catalog import RunupEntry, load_runup_entry
from agentic_trader.research.apriori.runup_runner import build_runup_inputs


REPO = Path(__file__).resolve().parents[3]
SHIPPED = REPO / "config/research/apriori/runup-v1.json"
ENTRY = load_runup_entry(SHIPPED).entry
DAYS = tuple(date(2021, 1, 4) + timedelta(days=i) for i in range(10))
SYMBOL = "AAPL"


def _entry(**update) -> RunupEntry:
    return RunupEntry.model_validate({**ENTRY.model_dump(mode="json"), **update})


def _protocol():
    return runup_runner.load_campaign_protocol(REPO / ENTRY.pooled_protocol).protocol


def _frame():
    return pd.DataFrame(
        {"Close": 1.0}, index=pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in DAYS], name="timestamp")
    )


def _install(monkeypatch, tmp_path, *, cube_sha=None, cohort_sha=None, calls=None):
    protocol = _protocol()

    async def fake_build(cohort, spec, **kwargs):
        assert kwargs["require_cached"] is True and kwargs["load_hourly"] is True
        cube = SimpleNamespace(sha256=cube_sha or ENTRY.cube_sha256)
        return SimpleNamespace(cube=cube, trading_days=DAYS, adjusted={}, hourly={})

    async def fake_market(timeframe, bars, cache_dir, start, end, pace):
        if calls is not None:
            calls.append((timeframe, cache_dir))
        directory = cache_dir / f"SPY_{timeframe}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{timeframe}.npz").write_bytes(b"x")
        return _frame()

    pages = SimpleNamespace(rows_by_symbol={}, pages_sha256="p" * 64, pages=3, duplicates=1)
    monkeypatch.setattr(runup_runner, "build_cube_inputs", fake_build)
    monkeypatch.setattr(runup_runner, "_market_frame", fake_market)
    monkeypatch.setattr(runup_runner, "load_event_sources", lambda inputs, sources: (pages, ()))
    monkeypatch.setattr(
        runup_runner,
        "load_cohort",
        lambda path: SimpleNamespace(
            sha256=cohort_sha or protocol.cohort_sha256, cohort=SimpleNamespace(symbols=(SYMBOL,))
        ),
    )


def _build(entry=ENTRY, tmp_path=None):
    return build_runup_inputs(
        entry,
        repo_root=REPO,
        bars=object(),
        calendar=object(),
        cache_dir=tmp_path / "pooled",
        earnings_pages=tmp_path / "pages",
        actions_file=tmp_path / "actions.json",
        market_cache_dir=tmp_path / "market",
        pace=None,
    )


async def test_builds_inputs_with_provenance(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, tmp_path, calls=calls)
    inputs = await _build(tmp_path=tmp_path)
    assert inputs.provenance["cube_sha256"] == ENTRY.cube_sha256
    assert inputs.provenance["symbols_without_archive_rows"] == [SYMBOL]
    assert inputs.provenance["market_daily_source"] == "fetched"
    assert set(inputs.provenance["market_files_sha256"]) == {"SPY_1d/1d.npz", "SPY_1h/1h.npz"}
    json.dumps(inputs.provenance)
    assert {cache for _, cache in calls} == {tmp_path / "market"}
    assert not (tmp_path / "pooled").exists()


async def test_protocol_digest_mismatch_names_both(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match=r"pooled_protocol_sha256 " + "0" * 64):
        await _build(_entry(pooled_protocol_sha256="0" * 64), tmp_path)


async def test_non_events_protocol_is_refused(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    other = "config/research/pooled/cohort3-daily-v1.json"
    sha = runup_runner.hashlib.sha256((REPO / other).read_bytes()).hexdigest()
    with pytest.raises(TypeError, match="not an events protocol"):
        await _build(_entry(pooled_protocol=other, pooled_protocol_sha256=sha), tmp_path)


async def test_cohort_digest_mismatch_names_both(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, cohort_sha="c" * 64)
    with pytest.raises(ValueError, match="c" * 64) as info:
        await _build(tmp_path=tmp_path)
    assert _protocol().cohort_sha256 in str(info.value)


async def test_cube_digest_mismatch_names_both(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, cube_sha="d" * 64)
    with pytest.raises(ValueError, match="d" * 64) as info:
        await _build(tmp_path=tmp_path)
    assert ENTRY.cube_sha256 in str(info.value)


async def test_pages_digest_mismatch_propagates_before_any_fetch(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    calls = []

    def refuse(inputs, sources):
        raise ValueError(f"earnings pages digest {'e' * 64} does not match {inputs.earnings.pages_sha256}")

    monkeypatch.setattr(runup_runner, "load_event_sources", refuse)
    monkeypatch.setattr(runup_runner, "_market_frame", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="e" * 64) as info:
        await _build(tmp_path=tmp_path)
    assert _protocol().event_inputs.earnings.pages_sha256 in str(info.value) and calls == []
