# tests/research/pooled/mini_world.py
"""A small real-DSL world for the pooled campaign tests: 12 names on the campaign calendar."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    EventsCampaignProtocol,
    Family,
    InsiderCampaignProtocol,
    IntradayCampaignProtocol,
    NullCheckSpec,
    Selection,
    ShortVolumeCampaignProtocol,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.events import EVENT_FIELDS, EventCoverage, EventFrames
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook
from agentic_trader.research.pooled.short_fields import SHORT_FIELDS, ShortCoverage, ShortFrames
from tests.research.pooled.test_formula import hourly


REPO = Path(__file__).resolve().parents[3]
V1 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol
COHORT = "c" * 64
NAMES = tuple(f"N{j:02d}" for j in range(12))


def weekdays(start: date, end: date) -> tuple[date, ...]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


BAR_DAYS = weekdays(date(2016, 1, 4), V1.windows.confirmation[1])
SESSIONS = weekdays(V1.windows.discovery[0], V1.windows.confirmation[1])


def adjusted_bars(seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n = len(BAR_DAYS)
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(ET_TZ) for d in BAR_DAYS]).tz_convert("UTC")
    frames = {}
    for name in NAMES:
        close = 50.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))
        spread = np.abs(rng.normal(0.0, 0.01, n))
        frames[name] = pd.DataFrame(
            {
                "Open": close * (1 + rng.normal(0.0, 0.005, n)),
                "High": close * (1 + spread),
                "Low": close * (1 - spread),
                "Close": close,
                "Volume": rng.lognormal(13.0, 0.4, n),
            },
            index=index,
        )
    return frames


ADJUSTED = adjusted_bars()

FAMILIES = (
    Family(
        id="reversal",
        rationale="r",
        seeds=("-1.0 * roc(close, 5)", "-1.0 * roc(close, 21)"),
        mutation_operators=("ts_mean", "zscore"),
        windows=(3, 5, 10, 21),
    ),
    Family(
        id="range_location",
        rationale="r",
        seeds=("(close - ts_min(low, 20)) / (ts_max(high, 20) - ts_min(low, 20) + 1e-6)",),
        mutation_operators=("ts_rank", "ts_mean"),
        windows=(10, 20, 40),
    ),
    Family(
        id="abnormal_volume",
        rationale="r",
        seeds=("volume / ts_mean(volume, 50)",),
        mutation_operators=("ts_mean", "zscore"),
        windows=(5, 10, 20, 50),
    ),
)


def book(capacity: int = 64) -> ScoreBook:
    return ScoreBook(ADJUSTED, BAR_DAYS, SESSIONS, NAMES, capacity=capacity)


def label_cube(seed: int = 1) -> LabelCube:
    rng = np.random.default_rng(seed)
    shape = (len(SESSIONS), len(NAMES))
    r = rng.normal(0.05, 1.0, shape)
    arrays = {
        "eligible": np.ones(shape, bool),
        "labelled": np.ones(shape, bool),
        "r_gross": r + 0.01,
        "r_cost": r,
        "holding": np.ones(shape, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": rng.integers(0, 2**62, shape).astype(np.uint64),
        "dollar_volume": np.ones(shape),
    }
    coverage = {"sessions": len(SESSIONS), "skipped_sessions": {}, "unlabelled_by_year": {}, "eligible_by_year": {}}
    return LabelCube(
        spec_identity="spec", cohort_sha256=COHORT, sessions=SESSIONS, symbols=NAMES, arrays=arrays, coverage=coverage
    )


def planted_cube(expression: str, delta: float, seed: int = 1, protocol=V1) -> LabelCube:
    """Labels with ``delta`` R added to every cell ``expression`` picks (under ``protocol``) over the calendar."""
    cube = label_cube(seed)
    view = cube.window(cube.sessions[0], cube.sessions[-1])
    picks = protocol.select(*book().formula(expression).panel(view), view)
    return cube.with_shift(picks.session_idx, picks.symbol_idx, delta)


def cube_build(cube: LabelCube) -> CubeBuild:
    return CubeBuild(cube=cube, trading_days=BAR_DAYS, adjusted=ADJUSTED, bar_failures={}, static_used=())


def mini_protocol(**update):
    base = {
        "families": FAMILIES,
        "formula_budget": 9,
        "cohort_sha256": COHORT,
        "bootstrap": V1.bootstrap.model_copy(
            update={"discovery_draws": 200, "selection_draws": 200, "confirmation_draws": 200}
        ),
        "power_search": V1.power_search.model_copy(
            update={
                "families": ("reversal", "range_location", "abnormal_volume"),
                "seeds": 3,
                "min_recovered": 2,
                "seed": 20261002,
            }
        ),
        "null_check": NullCheckSpec(replicates=3, max_false_acceptances=0, seed=20261003),
    }
    base.update(update)
    return V1.model_copy(update=base)


class RecordingCharger:
    def __init__(self) -> None:
        self.reserved: list[tuple[str, int]] = []
        self.charged: list[tuple[str, str, str]] = []

    def reserve_family(self, family_id: str, budget: int) -> None:
        self.reserved.append((family_id, budget))

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None:
        self.charged.append((family_id, expression, formula_id))


def mini_v3_protocol(**update):
    """The mini protocol under v3's top-decile selection (12 names: 2 picks per session)."""
    return mini_protocol(k=None, selection=Selection(rule="top_fraction", fraction=0.10), **update)


def mini_fixed_set_protocol(**update):
    """The mini protocol as a validated fixed set: every mini family's seeds, scored once, no mutation."""
    families = tuple(family.model_copy(update={"mutation_operators": (), "windows": None}) for family in FAMILIES)
    protocol = mini_protocol(
        search_mode="fixed_set",
        power_search=None,
        families=families,
        formula_budget=sum(len(family.seeds) for family in families),
        **update,
    )
    return CampaignProtocol.model_validate(protocol.model_dump())  # model_copy skips the validators


_HOURLY: dict[str, pd.DataFrame] = {}


def hourly_bars(seed: int = 0) -> dict[str, pd.DataFrame]:
    """Regular-session hourly bars for every name over the campaign sessions (built once, lazily)."""
    if not _HOURLY:
        rng = np.random.default_rng(seed)
        bars_per_day = 7
        for name in NAMES:
            closes = 50.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, len(SESSIONS) * bars_per_day)))
            _HOURLY[name] = hourly(list(SESSIONS), closes_by_bar=list(closes))
    return _HOURLY


def intraday_cube_build(cube: LabelCube) -> CubeBuild:
    return CubeBuild(
        cube=cube, trading_days=BAR_DAYS, adjusted=ADJUSTED, bar_failures={}, static_used=(), hourly=hourly_bars()
    )


def mini_intraday_protocol(**update):
    """The mini fixed-set protocol reading hourly bars one bar before the decision."""
    document = mini_fixed_set_protocol(**update).model_dump(mode="json")
    document.update(id="pooled-intraday-campaign", score_inputs="hourly", score_read={"bars_before_decision": 1})
    return IntradayCampaignProtocol.model_validate(document)


def event_frames(seed: int = 3) -> dict[str, pd.DataFrame]:
    """Synthetic events_fields_v1 frames on BAR_DAYS: a report every ~63 sessions per name."""
    rng = np.random.default_rng(seed)
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in BAR_DAYS])
    n = len(BAR_DAYS)
    frames = {}
    for j, name in enumerate(NAMES):
        age = np.full(n, np.nan)
        surprise = np.full(n, np.nan)
        reaction = np.full(n, np.nan)
        starts = list(range(30 + j, n, 63))
        for k, start in enumerate(starts):
            stop = starts[k + 1] if k + 1 < len(starts) else n
            age[start:stop] = np.arange(stop - start, dtype=float)
            surprise[start:stop] = rng.normal(0.0, 10.0)
            reaction[start:stop] = rng.normal(0.0, 1.0)
        dividend_yield = np.full(n, np.nan)
        dividend_yield[251:] = 0.01 * (j % 4)
        frames[name] = pd.DataFrame(
            {
                "earnings_age": age,
                "earnings_surprise": surprise,
                "earnings_reaction": reaction,
                "dividend_yield": dividend_yield,
            },
            index=index,
        )
    return frames


EVENT_FRAMES = event_frames()


def short_frames(seed: int = 4) -> dict[str, pd.DataFrame]:
    """Synthetic short_fields_v1 frames on BAR_DAYS; NaN for the first 20 rows (the baseline warm-up)."""
    rng = np.random.default_rng(seed)
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in BAR_DAYS])
    n = len(BAR_DAYS)
    frames = {}
    for name in NAMES:
        columns = {
            "short_ratio": rng.uniform(0.2, 0.7, n),
            "short_ratio_z": rng.normal(0.0, 1.0, n),
            "short_volume_rel": rng.normal(1.0, 0.2, n),
            "offex_share": rng.uniform(0.2, 0.6, n),
        }
        for values in columns.values():
            values[:20] = np.nan
        frames[name] = pd.DataFrame(columns, index=index)
    return frames


SHORT_FRAMES = short_frames()


def mini_events_protocol(**update):
    """The mini fixed-set protocol reading daily bars plus the events_fields_v1 columns."""
    document = mini_fixed_set_protocol(**update).model_dump(mode="json")
    document.update(
        id="pooled-events-campaign",
        score_inputs="daily",
        event_inputs={
            "fields_version": "events_fields_v1",
            "earnings": {
                "source": "nasdaq-calendar",
                "pages_sha256": "a" * 64,
                "calendar_from": "2016-02-23",
                "calendar_through": "2026-07-31",
                "min_estimates": 1,
                "min_abs_forecast": 0.05,
                "vol_window": 20,
                "benchmark": "SPY",
            },
            "corporate_actions": {
                "source": "alpaca",
                "content_sha256": "b" * 64,
                "window": ["2016-01-01", "2027-03-31"],
                "trailing_sessions": 252,
            },
        },
    )
    return EventsCampaignProtocol.model_validate(document)


def mini_short_protocol(**update):
    """The mini fixed-set protocol reading daily bars plus the short_fields_v1 columns."""
    document = mini_fixed_set_protocol().model_dump(mode="json")
    document.update(
        id="pooled-short-volume-campaign",
        score_inputs="daily",
        short_inputs={
            "fields_version": "short_fields_v1",
            "short_volume": {
                "source": "finra-regsho-cnms",
                "files_sha256": "d" * 64,
                "files_from": "2018-08-01",
                "files_through": "2026-09-01",
                "z_window": 20,
                "min_total_volume": 10000,
                "max_offex_share": 1.5,
            },
            "corporate_actions": {
                "source": "alpaca",
                "content_sha256": "b" * 64,
                "window": ["2016-01-01", "2027-03-31"],
                "trailing_sessions": 252,
            },
        },
    )
    document.update(update)
    return ShortVolumeCampaignProtocol.model_validate(document)


def mini_insider_protocol(**update):
    """The mini fixed-set protocol reading daily bars plus the insider_fields_v1 columns."""
    document = mini_fixed_set_protocol().model_dump(mode="json")
    document.update(
        id="pooled-insider-campaign",
        score_inputs="daily",
        insider_inputs={
            "fields_version": "insider_fields_v1",
            "insider_filings": {
                "source": "sec-form345-datasets",
                "files_sha256": "e" * 64,
                "quarters_from": "2015q1",
                "quarters_through": "2026q3",
                "buy_window": 63,
                "net_window": 126,
                "owner_roles": ["Officer", "Director"],
                "exclude_10b5_1": True,
            },
            "corporate_actions": {
                "source": "alpaca",
                "content_sha256": "b" * 64,
                "window": ["2016-01-01", "2027-03-31"],
                "trailing_sessions": 252,
            },
        },
    )
    document.update(update)
    return InsiderCampaignProtocol.model_validate(document)


def events_cube_build(cube: LabelCube) -> CubeBuild:
    coverage = EventCoverage(
        symbols_without_reports=(),
        symbols_without_actions=(),
        non_session_reports=0,
        unpriced_dividends=0,
        unscaled_dividends=0,
        symbols_without_raw=(),
        identity_breaks={},
        first_finite={name: dict.fromkeys(EVENT_FIELDS, BAR_DAYS[251].isoformat()) for name in NAMES},
    )
    return CubeBuild(
        cube=cube,
        trading_days=BAR_DAYS,
        adjusted=ADJUSTED,
        raw=ADJUSTED,
        bar_failures={},
        static_used=(),
        events=EventFrames(frames=EVENT_FRAMES, coverage=coverage),
    )


def short_cube_build(cube: LabelCube) -> CubeBuild:
    coverage = ShortCoverage(
        symbols_without_rows=(),
        unscored_rows=0,
        thin_days=0,
        offex_clipped=0,
        identity_breaks={},
        first_finite={name: dict.fromkeys(SHORT_FIELDS, BAR_DAYS[20].isoformat()) for name in NAMES},
    )
    return CubeBuild(
        cube=cube,
        trading_days=BAR_DAYS,
        adjusted=ADJUSTED,
        raw=ADJUSTED,
        bar_failures={},
        static_used=(),
        short=ShortFrames(frames=SHORT_FRAMES, coverage=coverage),
    )
