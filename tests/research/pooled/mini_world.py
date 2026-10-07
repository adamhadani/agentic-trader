# tests/research/pooled/mini_world.py
"""A small real-DSL world for the pooled campaign tests: 12 names on the campaign calendar."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    Family,
    NullCheckSpec,
    Selection,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook


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
