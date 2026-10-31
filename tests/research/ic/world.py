"""A small deterministic world for the IC lane tests: 8 names, SPY and a cube on a weekday calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.runner import CubeBuild


NAMES = tuple(f"S{j}" for j in range(8))
GROUPS = np.array(["a", "a", "a", "b", "b", "b", "c", "unknown"])
SPY_FILE = "SPY|1d|all|abc.npz"


def weekdays(start: date, count: int) -> tuple[date, ...]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


TRADING_DAYS = weekdays(date(2016, 1, 4), 420)
CUBE_START = 20  # cube sessions are TRADING_DAYS[20:]
WINDOW = (TRADING_DAYS[40], TRADING_DAYS[300])


def _index(days: tuple[date, ...]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d).tz_localize(ET_TZ) for d in days]).tz_convert("UTC")


def bars(seed: int, days: tuple[date, ...] = TRADING_DAYS) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(days)
    close = 50.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))
    return pd.DataFrame(
        {
            "Open": close * (1 + rng.normal(0.0, 0.005, n)),
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": rng.lognormal(13.0, 0.4, n),
        },
        index=_index(days),
    )


@dataclass
class World:
    build: CubeBuild
    spy: pd.DataFrame
    groups: np.ndarray
    window: tuple[date, date]

    def with_bars(self, adjusted: dict[str, pd.DataFrame], spy: pd.DataFrame | None = None) -> World:
        build = CubeBuild(
            cube=self.build.cube,
            trading_days=self.build.trading_days,
            adjusted=adjusted,
            bar_failures={},
            static_used=(),
        )
        return World(build, self.spy if spy is None else spy, self.groups, self.window)


def make_cube(*, bar_files: tuple[str, ...] = (SPY_FILE,), ineligible: tuple[int, ...] = ()) -> LabelCube:
    sessions = TRADING_DAYS[CUBE_START:]
    rng = np.random.default_rng(11)
    shape = (len(sessions), len(NAMES))
    dollar_volume = rng.lognormal(16.0, 0.5, shape)
    dollar_volume[30, 1] = 0.0  # a non-positive dollar volume, inside the window (cube row 30 = TRADING_DAYS[50])
    eligible = np.ones(shape, bool)
    eligible[:, list(ineligible)] = False
    arrays = {
        "eligible": eligible,
        "labelled": np.ones(shape, bool),
        "r_gross": np.zeros(shape),
        "r_cost": np.zeros(shape),
        "holding": np.ones(shape, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": np.zeros(shape, np.uint64),
        "dollar_volume": dollar_volume,
    }
    return LabelCube(
        spec_identity="spec",
        cohort_sha256="c" * 64,
        sessions=sessions,
        symbols=NAMES,
        arrays=arrays,
        coverage={"bar_file_list": list(bar_files)},
    )


def make_world(*, bar_files: tuple[str, ...] = (SPY_FILE,), ineligible: tuple[int, ...] = ()) -> World:
    adjusted = {name: bars(100 + j) for j, name in enumerate(NAMES)}
    cube = make_cube(bar_files=bar_files, ineligible=ineligible)
    build = CubeBuild(cube=cube, trading_days=TRADING_DAYS, adjusted=adjusted, bar_failures={}, static_used=())
    return World(build, bars(7), GROUPS, WINDOW)
