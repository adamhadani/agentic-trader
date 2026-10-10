import asyncio
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.research.setups import runner
from agentic_trader.research.spread.formation import fit_pair
from agentic_trader.research.spread.panel import (
    PanelBuild,
    SpreadPanel,
    build_spread_panel,
    shift_panel,
    synthetic_panel,
)
from agentic_trader.research.spread.protocol import FormationRule, PowerSpec, SpreadCohort
from tests.research.spread.test_schedule import weekdays


SESSIONS = weekdays(date(2020, 1, 6), date(2021, 12, 31))


class FakeCalendar:
    async def get_calendar_range(self, start_date, end_date):
        out, d = [], start_date
        while d <= end_date:
            out.append(MarketCalendarDay(date=d, is_trading_day=d.weekday() < 5, is_early_close=False))
            d += timedelta(days=1)
        return out


class FakeBars:
    def __init__(self):
        self.calls: list[tuple[str, datetime, datetime]] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, start, end))
        assert timeframe == "1d" and adjustment == "all"
        if symbol == "BROKEN":
            return pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex([datetime(2020, 1, 6, 5, tzinfo=UTC)]))
        if symbol == "BAD":
            raise RuntimeError("provider down")
        if symbol == "EMPTY":
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([], tz=UTC))
        days = [d for d in SESSIONS if start.date() <= d <= end.date()]
        index = pd.DatetimeIndex([datetime(d.year, d.month, d.day, 5, tzinfo=UTC) for d in days])
        base = 100.0 if symbol == "AAA" else 50.0
        closes = base + np.arange(len(days), dtype=float)
        frame = pd.DataFrame(
            {"Open": closes - 0.5, "High": closes + 1, "Low": closes - 1, "Close": closes, "Volume": 1e6}, index=index
        )
        if symbol == "GAPPY":
            frame = frame.drop(index=[i for i in frame.index if i.date() == SESSIONS[10]])
        return frame


async def pace():
    return None


def test_build_panel_aligns_to_sessions_records_failures_and_uses_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_RETRY_DELAYS", ())  # a raised fetch must not sleep through the retry delays
    bars = FakeBars()
    build = asyncio.run(
        build_spread_panel(
            ["AAA", "GAPPY", "BAD", "EMPTY"],
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2021, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    assert isinstance(build, PanelBuild) and isinstance(build.panel, SpreadPanel)
    panel = build.panel
    assert panel.sessions == SESSIONS and panel.symbols == ("AAA", "BAD", "EMPTY", "GAPPY")
    assert panel.closes.shape == (len(SESSIONS), 4) and panel.opens.shape == panel.closes.shape
    assert panel.closes.index.tz is None and panel.closes.index[0] == pd.Timestamp(SESSIONS[0])
    assert panel.closes["AAA"].iloc[0] == 100.0 and panel.opens["AAA"].iloc[0] == 99.5
    assert np.isnan(panel.closes["GAPPY"].iloc[10]) and panel.closes["GAPPY"].notna().sum() == len(SESSIONS) - 1
    assert build.bar_failures == {"BAD": "RuntimeError", "EMPTY": "empty"}
    assert panel.closes["BAD"].isna().all() and panel.closes["EMPTY"].isna().all()
    assert (tmp_path / "cache_range.json").exists() and (tmp_path / "AAA_1d").exists()
    before = len(bars.calls)
    asyncio.run(
        build_spread_panel(
            ["AAA"],
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2021, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    assert len(bars.calls) == before  # served from the cache


def test_build_panel_refuses_a_range_outside_the_cache_claim(tmp_path):
    bars = FakeBars()
    asyncio.run(
        build_spread_panel(
            ["AAA"],
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2020, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    with pytest.raises(ValueError, match="fresh cache directory"):
        asyncio.run(
            build_spread_panel(
                ["AAA"],
                bars=bars,
                calendar=FakeCalendar(),
                cache_dir=tmp_path,
                start=date(2020, 1, 6),
                through=date(2021, 12, 31),
                adjustment="all",
                pace=pace,
            )
        )


def test_malformed_cached_frame_is_recorded_not_raised(tmp_path):
    build = asyncio.run(
        build_spread_panel(
            ["AAA", "BROKEN"],
            bars=FakeBars(),
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2020, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    assert build.bar_failures == {"BROKEN": "malformed"}
    assert build.panel.closes["BROKEN"].isna().all() and build.panel.opens["BROKEN"].isna().all()
    assert build.panel.closes["AAA"].notna().all()


def _panel_from(log_closes: dict[str, np.ndarray], sessions=SESSIONS) -> SpreadPanel:
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in sessions[: len(next(iter(log_closes.values())))]])
    closes = pd.DataFrame({k: np.exp(v) for k, v in log_closes.items()}, index=index)
    opens = closes.shift(1).fillna(closes.iloc[0]) * 1.001
    return SpreadPanel(sessions=tuple(d.date() for d in index), closes=closes, opens=opens)


def test_shift_panel_preserves_marginals_and_breaks_comovement():
    rng = np.random.default_rng(3)
    common = np.cumsum(rng.normal(0, 0.01, 400))
    a = common + np.cumsum(rng.normal(0, 0.002, 400))
    b = common + np.cumsum(rng.normal(0, 0.002, 400))
    panel = _panel_from({"A": a, "B": b})
    panel.closes.loc[panel.closes.index[50], "B"] = np.nan
    panel.opens.loc[panel.opens.index[50], "B"] = np.nan
    shifted = shift_panel(panel, seed=7, block_sessions=21)
    assert shifted.sessions == panel.sessions and list(shifted.closes.columns) == ["A", "B"]
    for name in ("A", "B"):
        original = np.diff(np.log(panel.closes[name].dropna().to_numpy()))
        moved = np.diff(np.log(shifted.closes[name].dropna().to_numpy()))
        assert np.allclose(np.sort(original), np.sort(moved), atol=1e-9)
        assert shifted.closes[name].iloc[0] == panel.closes[name].iloc[0]
    assert np.isnan(shifted.closes["B"].iloc[50]) and np.isnan(shifted.opens["B"].iloc[50])
    ra, rb = np.diff(np.log(panel.closes["A"])), np.diff(np.log(panel.closes["B"]))
    sa, sb = np.diff(np.log(shifted.closes["A"])), np.diff(np.log(shifted.closes["B"]))
    both = np.isfinite(rb) & np.isfinite(sb)  # the planted gap blanks the returns around it
    corr_before = np.corrcoef(ra[both], rb[both])[0, 1]
    corr_after = np.corrcoef(sa[both], sb[both])[0, 1]
    assert corr_before > 0.9
    assert abs(corr_after) < 0.3
    other = shift_panel(panel, seed=8, block_sessions=21)
    assert not np.allclose(other.closes["A"].to_numpy(), shifted.closes["A"].to_numpy())


COHORT = SpreadCohort.model_validate(
    {
        "id": "spread-cohort",
        "version": 1,
        "survivorship": "t",
        "market": "SPY",
        "sectors": {"tech": [f"T{c}" for c in "ABCDEFGH"], "energy": [f"E{c}" for c in "ABCDEF"]},
    }
)
SPEC = PowerSpec(
    seeds=1,
    min_pass=1,
    planted_pairs=4,
    half_life_sessions=10.0,
    innovation_std=0.008,
    hedge_ratio=(0.6, 1.6),
    market_vol=0.01,
    sector_vol=0.007,
    idiosyncratic_vol=0.012,
    overnight_vol=0.003,
)
RULE = FormationRule(
    coint_max_lag=1,
    p_value=0.05,
    half_life_sessions=(5, 42),
    hedge_ratio_abs=(0.25, 4.0),
    top_pairs=20,
    min_eligible_names=2,
)


def test_synthetic_panel_plants_recoverable_same_sector_pairs():
    sessions = weekdays(date(2016, 1, 4), date(2019, 12, 31))
    panel, planted = synthetic_panel(COHORT, sessions, SPEC, seed=11)
    assert panel.symbols == tuple(sorted([*COHORT.symbols, "SPY"])) and len(panel.sessions) == len(sessions)
    assert len(planted) == 4 and len({s for pair in planted for s in pair}) == 8  # disjoint names
    sector_of = {s: sec for sec, members in COHORT.sectors.items() for s in members}
    assert all(sector_of[y] == sector_of[x] and y < x for y, x in planted)
    assert (panel.closes > 0).all().all() and (panel.opens > 0).all().all()
    logs = np.log(panel.closes.iloc[-504:])
    recovered = sum(
        fit_pair(logs[y].to_numpy(), logs[x].to_numpy(), RULE, y=y, x=x, sector="s").eligible for y, x in planted
    )
    assert recovered >= 3
    unplanted = [(y, x) for y, x, _ in COHORT.pairs() if (y, x) not in planted][:20]
    false = sum(
        fit_pair(logs[y].to_numpy(), logs[x].to_numpy(), RULE, y=y, x=x, sector="s").eligible for y, x in unplanted
    )
    assert false <= 6
    other, _ = synthetic_panel(COHORT, sessions, SPEC, seed=12)
    assert not np.allclose(other.closes.to_numpy(), panel.closes.to_numpy())


def test_grouped_shift_gives_distinct_offsets_within_a_group():
    names = [f"N{i}" for i in range(6)]
    block, n = 21, 600
    rng = np.random.default_rng(5)
    common = np.cumsum(rng.normal(0, 0.01, n))
    logs = {name: common + np.cumsum(rng.normal(0, 0.001, n)) for name in names}
    panel = _panel_from(logs, sessions=weekdays(date(2020, 1, 6), date(2023, 12, 31)))
    original = {name: np.diff(np.log(panel.closes[name].to_numpy())) for name in names}
    for seed in range(20):
        shifted = shift_panel(panel, seed=seed, block_sessions=block, groups={"g": names})
        moved = {name: np.diff(np.log(shifted.closes[name].to_numpy())) for name in names}
        offsets = []
        for name in names:
            matches = [
                k for k in range(1, (n - 1) // block) if np.allclose(np.roll(original[name], k * block), moved[name])
            ]
            assert len(matches) == 1
            offsets.append(matches[0])
        assert len(set(offsets)) == 6
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                assert abs(np.corrcoef(moved[a], moved[b])[0, 1]) < 0.3


def test_synthetic_panel_fails_loudly_when_it_cannot_plant_enough_pairs():
    cohort = SpreadCohort.model_validate(
        {"id": "spread-cohort", "version": 1, "survivorship": "t", "market": "SPY", "sectors": {"s": ["AAA", "BBB"]}}
    )
    spec = SPEC.model_copy(update={"planted_pairs": 2})
    with pytest.raises(ValueError, match="only 1 disjoint"):
        synthetic_panel(cohort, weekdays(date(2016, 1, 4), date(2016, 12, 30)), spec, seed=1)
