# tests/research/pooled/test_runner.py
import asyncio
import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled import runner as runner_module
from agentic_trader.research.pooled.cohort import Cohort, CohortSource, LoadedCohort, UniverseSpec
from agentic_trader.research.pooled.cube import _ARRAYS, BracketSpec, CubeSpec, LabelCube, save_cube
from agentic_trader.research.pooled.runner import build_cube_inputs
from agentic_trader.research.setups import runner as setups_runner


@dataclass
class Day:
    date: date
    is_trading_day: bool


class FakeCalendar:
    async def get_calendar_range(self, start, end):
        out, d = [], start
        while d <= end:
            out.append(Day(d, d.weekday() < 5))
            d += timedelta(days=1)
        return out


class FakeBars:
    """``fail`` keys raise; ``empty`` keys return the provider's empty frame (UTC DatetimeIndex)."""

    def __init__(self, *, fail=(), empty=()):
        self.calls = []
        self.spans = {}
        self.fail = set(fail)
        self.empty = set(empty)

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment="all", **kwargs):
        key = (symbol, timeframe, adjustment)
        self.calls.append(key)
        self.spans.setdefault(key, []).append((start, end))
        if key in self.fail:
            raise RuntimeError("provider down")
        columns = ["Open", "High", "Low", "Close", "Volume"]
        if key in self.empty:
            return pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
        days, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        if timeframe == "1h":
            index = [datetime.combine(x, time(h), tzinfo=ET_TZ).astimezone(UTC) for x in days for h in range(9, 16)]
        else:
            index = [datetime.combine(x, time(0), tzinfo=ET_TZ).astimezone(UTC) for x in days]
        n = len(index)
        price = np.full(n, 50.0)
        return pd.DataFrame(
            {"Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": price, "Volume": 1e6},
            index=pd.DatetimeIndex(index),
        )


SPEC = CubeSpec(
    feed="alpaca:sip",
    bars_from=date(2021, 1, 1),
    decisions=(date(2021, 3, 1), date(2021, 3, 5)),
    bars_through=date(2021, 5, 1),
    bracket=BracketSpec(
        decision_time_et="10:35", stop_atr_multiple=2.0, atr_window=14, target_r=3.0, max_hold_sessions=5
    ),
    universe=UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=1),
    decision_cost_bps=5.0,
)
WINDOW = SPEC.decisions
STATIC = [f"R{i:02d}" for i in range(20)]  # the live gate needs 20 reference names


def cohort(sha256: str = "c" * 64) -> LoadedCohort:
    c = Cohort(
        id="pooled-cohort",
        version=1,
        survivorship="test",
        sources=(CohortSource(kind="config_groups", description="t", identity="x", symbols=("AAA", "BBB")),),
        excluded={},
        symbols=("AAA", "BBB"),
    )
    return LoadedCohort(cohort=c, sha256=sha256, path=Path("cohort.json"))


async def _no_pace():
    return None


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(setups_runner, "_RETRY_DELAYS", ())


def build(bars, cache_dir, *, static=STATIC, loaded=None, progress=None, spec=SPEC):
    return asyncio.run(
        build_cube_inputs(
            loaded or cohort(),
            spec,
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=cache_dir,
            static_symbols=static,
            pace=_no_pace,
            progress=progress,
        )
    )


def cached(cache_dir: Path, folder: str, symbol: str, timeframe: str) -> list[Path]:
    return sorted((cache_dir / folder / f"{symbol}_{timeframe}").glob("*.npz"))


def test_build_fetches_once_and_reuses_the_cached_cube(tmp_path):
    bars = FakeBars()
    first = build(bars, tmp_path)
    assert first.cube.window(*WINDOW).eligible.all()
    calls = len(bars.calls)
    second = build(bars, tmp_path)
    assert second.cube.sha256 == first.cube.sha256
    assert len(bars.calls) == calls  # bars and the cube came from the cache
    assert {adj for _, tf, adj in bars.calls if tf == "1d"} == {"all", "raw"}


@pytest.mark.parametrize(
    ("failing", "reason", "refetched"),
    [
        (("AAA", "1d", "all"), "1d/all: RuntimeError: provider down", [("AAA", "1d", "all"), ("AAA", "1h", "all")]),
        (("AAA", "1d", "raw"), "1d/raw: RuntimeError: provider down", [("AAA", "1d", "raw"), ("AAA", "1h", "all")]),
        (("AAA", "1h", "all"), "1h/all: RuntimeError: provider down", [("AAA", "1h", "all")]),
    ],
)
def test_a_raised_fetch_fails_the_build_after_every_symbol_was_attempted_and_a_rerun_resumes(
    tmp_path, failing, reason, refetched
):
    broken = FakeBars(fail={failing})
    with pytest.raises(ValueError, match="bar acquisition failed for 1 symbol: AAA") as raised:
        build(broken, tmp_path)
    assert reason in str(raised.value) and "rerun to resume from the cache" in str(raised.value)
    # Every other fetch was attempted and cached before the build failed; no cube was written.
    for symbol in ("BBB", *STATIC):
        assert cached(tmp_path, "bars", symbol, "1d") and cached(tmp_path, "bars_raw", symbol, "1d")
    assert cached(tmp_path, "bars", "BBB", "1h")
    assert not list(tmp_path.glob("cube-*.npz"))

    healthy = FakeBars()
    rerun = build(healthy, tmp_path)
    assert healthy.calls == refetched  # only what failed (and what depended on it) is fetched
    assert rerun.cube.window(*WINDOW).eligible.all()
    assert rerun.bar_failures == {}


def test_every_failing_symbol_is_listed(tmp_path):
    broken = FakeBars(fail={("AAA", "1d", "all"), ("AAA", "1d", "raw"), ("R03", "1d", "raw")})
    with pytest.raises(ValueError, match="bar acquisition failed for 2 symbols") as raised:
        build(broken, tmp_path)
    message = str(raised.value)
    assert "AAA (1d/all: RuntimeError: provider down; 1d/raw: RuntimeError: provider down)" in message
    assert "R03 (1d/raw: RuntimeError: provider down)" in message


def test_an_empty_frame_is_recorded_and_the_symbol_is_ineligible_or_unlabelled(tmp_path):
    bars = FakeBars(empty={("AAA", "1d", "raw"), ("BBB", "1h", "all")})
    built = build(bars, tmp_path)
    coverage = built.cube.coverage
    assert coverage["bar_failures"] == {"AAA": "1d/raw: empty", "BBB": "1h/all: empty"}
    assert built.bar_failures == coverage["bar_failures"]
    view = built.cube.window(*WINDOW)
    assert not view.eligible[:, 0].any()  # AAA: no raw bars, never eligible (and no hourly fetch)
    assert ("AAA", "1h", "all") not in bars.calls
    assert view.eligible[:, 1].all() and not view.labelled[:, 1].any()  # BBB: eligible, unlabelled
    assert coverage["unlabelled_by_year"]["no_hourly_bars"] == {"2021": len(view.sessions)}


def test_a_static_symbol_without_raw_bars_is_not_in_the_reference_set(tmp_path):
    static = [*reversed(STATIC), "R20"]
    built = build(FakeBars(empty={("R20", "1d", "raw")}), tmp_path, static=static)
    assert built.static_used == tuple(STATIC)  # sorted, without R20
    coverage = built.cube.coverage
    assert coverage["static_used"] == STATIC
    assert coverage["static_used_sha256"] == hashlib.sha256("\n".join(STATIC).encode()).hexdigest()
    assert coverage["bar_failures"] == {"R20": "1d/raw: empty"}
    assert coverage["reference_names"] == {"min": 20, "max": 20}


def test_the_build_records_a_digest_of_every_bar_cache_file_it_read(tmp_path):
    built = build(FakeBars(), tmp_path)

    def line(symbol: str, timeframe: str, adjustment: str) -> str:
        folder = "bars_raw" if adjustment == "raw" else "bars"
        return f"{symbol}|{timeframe}|{adjustment}|{cached(tmp_path, folder, symbol, timeframe)[0].name}"

    lines = [line(symbol, "1d", adjustment) for symbol in ("AAA", "BBB", *STATIC) for adjustment in ("all", "raw")]
    lines += [line(symbol, "1h", "all") for symbol in ("AAA", "BBB")]
    coverage = built.cube.coverage
    assert coverage["bar_files"] == len(lines) == 46
    assert coverage["bars_sha256"] == hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def test_hourly_bars_cover_a_span_that_does_not_depend_on_the_symbols_eligibility(tmp_path):
    bars = FakeBars()
    build(bars, tmp_path)
    expected = (
        datetime.combine(SPEC.decisions[0], time.min, tzinfo=UTC) - timedelta(days=7),
        datetime.combine(SPEC.bars_through, time.max, tzinfo=UTC),
    )
    assert bars.spans[("AAA", "1h", "all")] == bars.spans[("BBB", "1h", "all")] == [expected]


def test_a_cached_cube_for_another_cohort_is_refused(tmp_path):
    build(FakeBars(), tmp_path)
    (cube_file,) = tmp_path.glob("cube-*.npz")
    other = cohort("d" * 64)
    shutil.copy(cube_file, tmp_path / cube_file.name.replace("c" * 16, "d" * 16))
    with pytest.raises(ValueError, match="does not match this cohort/spec"):
        build(FakeBars(), tmp_path, loaded=other)


def test_a_cached_cube_without_build_provenance_is_refused(tmp_path):
    built = build(FakeBars(), tmp_path)
    (cube_file,) = tmp_path.glob("cube-*.npz")
    view = built.cube.window(*WINDOW)
    bare = LabelCube(
        spec_identity=built.cube.spec_identity,
        cohort_sha256=built.cube.cohort_sha256,
        sessions=view.sessions,
        symbols=view.symbols,
        arrays={name: getattr(view, name) for name in _ARRAYS},
        coverage={k: v for k, v in built.cube.coverage.items() if k != "bars_sha256"},
    )
    assert bare.sha256 == built.cube.sha256  # the same cube: provenance is outside the hash
    cube_file.unlink()
    save_cube(bare, cube_file)
    with pytest.raises(ValueError, match="records no build provenance"):
        build(FakeBars(), tmp_path)


def test_a_cache_hit_reports_what_the_build_saw_without_fetching_or_recomputing_eligibility(tmp_path, monkeypatch):
    bars = FakeBars(empty={("BBB", "1h", "all"), ("R20", "1d", "raw")})
    first = build(bars, tmp_path, static=[*STATIC, "R20"])
    assert first.bar_failures == {"BBB": "1h/all: empty", "R20": "1d/raw: empty"}
    calls = len(bars.calls)

    def forbidden(*args, **kwargs):
        raise AssertionError("eligibility must not be recomputed on a cube cache hit")

    monkeypatch.setattr(runner_module, "point_in_time_eligibility", forbidden)
    monkeypatch.setattr(runner_module, "build_cube", forbidden)
    messages: list[str] = []
    # A different static list on the hit: the reference set is the one the build used.
    second = build(bars, tmp_path, static=["ZZZ"], progress=messages.append)
    assert len(bars.calls) == calls  # no fetch
    assert second.cube.sha256 == first.cube.sha256
    assert second.bar_failures == first.bar_failures
    assert second.static_used == first.static_used == tuple(STATIC)
    assert second.trading_days == first.trading_days
    assert sorted(second.adjusted) == sorted(first.adjusted) == ["AAA", "BBB"]
    for symbol in ("AAA", "BBB"):
        assert np.array_equal(second.adjusted[symbol].to_numpy(), first.adjusted[symbol].to_numpy())
        assert (second.adjusted[symbol].index == first.adjusted[symbol].index).all()
    assert any("cache hit" in message for message in messages)


def test_progress_reports_each_phase_of_a_build(tmp_path):
    messages: list[str] = []
    build(FakeBars(), tmp_path, progress=messages.append)
    phases = ("calendar", "daily bars 22/22", "eligibility: start", "eligibility: done", "hourly bars 2/2")
    phases += ("cube build: start", "cube saved")
    order = [next((i for i, m in enumerate(messages) if phase in m), None) for phase in phases]
    assert None not in order, (phases, messages)
    assert order == sorted(order)
    assert "10 eligible cells" in messages[order[3]]  # 5 sessions x 2 symbols
    assert "10 labelled of 10 eligible cells" in messages[order[6]]


def test_hourly_requests_cover_the_whole_spec_span_in_contiguous_chunks(tmp_path):
    spec = SPEC.model_copy(update={"bars_through": date(2022, 6, 1)})
    bars = FakeBars()
    build(bars, tmp_path, spec=spec)
    start = datetime.combine(spec.decisions[0], time.min, tzinfo=UTC) - timedelta(days=7)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    for symbol in ("AAA", "BBB"):
        chunks = bars.spans[(symbol, "1h", "all")]
        assert len(chunks) >= 2
        assert chunks[0][0] == start and chunks[-1][1] == end
        assert all(left[1] == right[0] for left, right in pairwise(chunks))


def test_a_cache_hit_rechecks_every_bar_file_the_cube_was_built_from(tmp_path):
    first = build(FakeBars(), tmp_path)
    assert len(first.cube.coverage["bar_file_list"]) == first.cube.coverage["bar_files"] == 46
    messages: list[str] = []
    build(FakeBars(), tmp_path, progress=messages.append)
    assert any("bar files re-checked: 46 unchanged" in message for message in messages)
    # A new cache file that sorts first would be loaded instead: the cached cube is refused.
    original = cached(tmp_path, "bars", "AAA", "1d")[0]
    shutil.copy(original, original.parent / ("0" * 8 + original.name))
    with pytest.raises(ValueError, match="bar files that changed"):
        build(FakeBars(), tmp_path)


def test_a_cube_built_before_the_file_list_is_loaded_with_a_note(tmp_path):
    built = build(FakeBars(), tmp_path)
    (cube_file,) = tmp_path.glob("cube-*.npz")
    view = built.cube.window(*WINDOW)
    older = LabelCube(
        spec_identity=built.cube.spec_identity,
        cohort_sha256=built.cube.cohort_sha256,
        sessions=view.sessions,
        symbols=view.symbols,
        arrays={name: getattr(view, name) for name in _ARRAYS},
        coverage={k: v for k, v in built.cube.coverage.items() if k != "bar_file_list"},
    )
    cube_file.unlink()
    save_cube(older, cube_file)
    messages: list[str] = []
    build(FakeBars(), tmp_path, progress=messages.append)
    assert any("not re-checked" in message for message in messages)


def test_five_consecutive_raised_fetches_stop_acquisition_early(tmp_path):
    fail = {(symbol, "1d", adjustment) for symbol in ("AAA", "BBB", *STATIC) for adjustment in ("all", "raw")}
    bars = FakeBars(fail=fail)
    messages: list[str] = []
    with pytest.raises(ValueError, match="provider unavailable: 5 consecutive fetches failed"):
        build(bars, tmp_path, progress=messages.append)
    assert len({call[0] for call in bars.calls}) < len(STATIC) + 2  # stopped before every symbol
    assert any("fetch failed: AAA 1d/all: RuntimeError: provider down" in message for message in messages)
