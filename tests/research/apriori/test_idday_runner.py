import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agentic_trader.research.apriori import idday_runner
from agentic_trader.research.apriori.catalog import IddayEntry, load_idday_entry
from agentic_trader.research.apriori.idday_runner import build_idday_inputs
from agentic_trader.research.pooled.campaign import load_campaign_protocol


REPO = Path(__file__).resolve().parents[3]
BASE = load_idday_entry(Path(__file__).parent / "fixtures" / "idday-v1-test.json").entry
DAYS = tuple(date(2021, 1, 4) + timedelta(days=i) for i in range(10))
EVENTS = "9" * 64
CUBE_V3, CUBE_XU, CUBE_M = "b" * 64, "d" * 64, "m" * 64
IDS = ("a", "b")


def _sha(path: str) -> str:
    return hashlib.sha256((REPO / path).read_bytes()).hexdigest()


def _document(errors=0, **update):
    labels = [{"event_id": i, "correct": n >= errors, "reason": "ok"} for n, i in enumerate(IDS)]
    return {"events_sha256": EVENTS, "seed": 7, "sample_size": 2, "labels": labels, **update}


def _entry(document_bytes: bytes, **update) -> IddayEntry:
    raw = BASE.model_dump(mode="json")
    raw["cohorts"][0].update(pooled_protocol_sha256=_sha(raw["cohorts"][0]["pooled_protocol"]), cube_sha256=CUBE_V3)
    raw["cohorts"][1].update(pooled_protocol_sha256=_sha(raw["cohorts"][1]["pooled_protocol"]), cube_sha256=CUBE_XU)
    raw["investor_days"].update(content_sha256="e" * 64, events_sha256=EVENTS)
    raw["parser_check"].update(
        sha256=hashlib.sha256(document_bytes).hexdigest(), seed=7, sample_size=2, max_errors=1, excluded_symbols=[]
    )
    raw.update(update)
    return IddayEntry.model_validate(raw)


def _frame():
    return pd.DataFrame(
        {"Close": 1.0}, index=pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in DAYS], name="timestamp")
    )


def _install(monkeypatch, *, events_sha=EVENTS, acquisition="e" * 64, calls=None):
    async def fake_build(cohort, spec, **kwargs):
        assert kwargs["require_cached"] is True and kwargs["load_hourly"] is True
        sha = CUBE_V3 if "events" in kwargs else CUBE_XU
        return SimpleNamespace(cube=SimpleNamespace(sha256=sha), trading_days=DAYS, adjusted={}, hourly={})

    async def fake_market(timeframe, bars, cache_dir, start, end, pace):
        calls.append(timeframe)
        directory = cache_dir / f"SPY_{timeframe}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{timeframe}.npz").write_bytes(b"x")
        return _frame()

    def fake_cohort(path):
        for name in ("cohort3-events-v1", "cohort-xu-insider-v1"):
            protocol = load_campaign_protocol(REPO / f"config/research/pooled/{name}.json").protocol
            if REPO / protocol.cohort == path:
                return SimpleNamespace(sha256=protocol.cohort_sha256, cohort=SimpleNamespace(symbols=("AAA",)))
        raise AssertionError(path)

    pages = SimpleNamespace(rows_by_symbol={}, pages_sha256="p" * 64, pages=3, duplicates=1)
    store = SimpleNamespace(issuers=lambda: (), unmapped=lambda: ())
    table = SimpleNamespace(events_sha256=events_sha, events=())
    sample = {"events_sha256": EVENTS, "seed": 7, "sample_size": 2, "sample": [{"event_id": i} for i in IDS]}
    merged = SimpleNamespace(sha256=CUBE_M, sessions=DAYS, symbols=("AAA", "BBB"))
    patches = {
        "build_cube_inputs": fake_build,
        "_market_frame": fake_market,
        "load_event_sources": lambda inputs, sources: (pages, ()),
        "load_actions": lambda path: ((), "9f38dffded5b73c53d38bc06ca34cf884549fa9ff91044e1bb74be62fa66c10d"),
        "load_cohort": fake_cohort,
        "merge_cubes": lambda a, b: merged,
        "InvestorDayStore": lambda path: store,
        "content_sha256": lambda s: acquisition,
        "parse_filings": lambda s: table,
        "cohort_bands": lambda a, b: {"AAA": "v3", "BBB": "xu"},
        "idday_candidates": lambda inputs, entry: (pd.DataFrame(), {}),
        "sample_parser_check": lambda c, t, e: sample,
    }
    for name, value in patches.items():
        monkeypatch.setattr(idday_runner, name, value)


def _build(entry, tmp_path, check_path):
    return build_idday_inputs(
        entry,
        repo_root=REPO,
        bars=object(),
        calendar=object(),
        cache_dir=tmp_path / "pooled",
        earnings_pages=tmp_path / "pages",
        actions_v3=tmp_path / "v3.json",
        actions_xu=tmp_path / "xu.json",
        investor_days_dir=tmp_path / "days",
        parser_check=check_path,
        market_cache_dir=tmp_path / "market",
        pace=None,
    )


def _check_file(tmp_path, document):
    data = json.dumps(document).encode()
    path = tmp_path / "parser_check.json"
    path.write_bytes(data)
    return path, data


async def test_builds_inputs_with_provenance(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls=calls)
    path, data = _check_file(tmp_path, _document(errors=1))
    inputs = await _build(_entry(data), tmp_path, path)
    p = inputs.provenance
    assert (p["merged_cube_sha256"], p["cube_sha256_v3"], p["cube_sha256_xu"]) == (CUBE_M, CUBE_V3, CUBE_XU)
    assert p["parser_check"] == {"errors": 1, "sample_size": 2, "precision": 0.5}
    assert calls == ["1d", "1h"] and inputs.band_of == {"AAA": "v3", "BBB": "xu"}
    json.dumps(p)


async def test_runner_refuses_wrong_protocol_digest(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls=calls)
    path, data = _check_file(tmp_path, _document())
    entry = _entry(data)
    raw = entry.model_dump(mode="json")
    raw["cohorts"][1]["pooled_protocol_sha256"] = "0" * 64
    with pytest.raises(ValueError, match=r"pooled_protocol_sha256 " + "0" * 64):
        await _build(IddayEntry.model_validate(raw), tmp_path, path)
    assert calls == []


async def test_runner_refuses_events_digest_mismatch(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, events_sha="1" * 64, calls=calls)
    path, data = _check_file(tmp_path, _document())
    with pytest.raises(ValueError, match="events_sha256"):
        await _build(_entry(data), tmp_path, path)
    assert calls == []


async def test_runner_refuses_failing_parser_check(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls=calls)
    path, data = _check_file(tmp_path, _document(errors=2))
    with pytest.raises(ValueError, match="2 errors"):
        await _build(_entry(data), tmp_path, path)
    assert calls == []


async def test_runner_refuses_parser_check_file_digest_mismatch(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls=calls)
    path, data = _check_file(tmp_path, _document())
    entry = _entry(data)
    path.write_bytes(data + b" ")
    with pytest.raises(ValueError, match=r"parser_check\.sha256"):
        await _build(entry, tmp_path, path)
    assert calls == []


async def test_runner_refuses_noncontiguous_merged_sessions(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls=calls)
    gapped = SimpleNamespace(sha256=CUBE_M, sessions=(DAYS[0], DAYS[2]), symbols=("AAA",))
    monkeypatch.setattr(idday_runner, "merge_cubes", lambda a, b: gapped)
    path, data = _check_file(tmp_path, _document())
    with pytest.raises(ValueError, match="contiguous"):
        await _build(_entry(data), tmp_path, path)
    assert calls == []
