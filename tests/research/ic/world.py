"""Small deterministic worlds for the IC lane tests: 8 names (48 from ``long_world``), SPY and a cube on weekdays."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.ic.arrays import ICArrays
from agentic_trader.research.ic.protocol import CompositeProtocol, LoadedComposite, load_composite
from agentic_trader.research.ic.sectors import LoadedSectors
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.storage.artifacts import save_json_report


COMPOSITE_PATH = Path(__file__).resolve().parents[3] / "config" / "research" / "ic" / "composite-ic-v1.json"
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


def make_cube(
    *,
    bar_files: tuple[str, ...] = (SPY_FILE,),
    ineligible: tuple[int, ...] = (),
    names: tuple[str, ...] = NAMES,
    days: tuple[date, ...] = TRADING_DAYS,
) -> LabelCube:
    sessions = days[CUBE_START:]
    rng = np.random.default_rng(11)
    shape = (len(sessions), len(names))
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
        symbols=names,
        arrays=arrays,
        coverage={"bar_file_list": list(bar_files)},
    )


def make_world(
    *,
    bar_files: tuple[str, ...] = (SPY_FILE,),
    ineligible: tuple[int, ...] = (),
    names: tuple[str, ...] = NAMES,
    groups: np.ndarray = GROUPS,
    days: tuple[date, ...] = TRADING_DAYS,
    window: tuple[date, date] = WINDOW,
) -> World:
    adjusted = {name: bars(100 + j, days) for j, name in enumerate(names)}
    cube = make_cube(bar_files=bar_files, ineligible=ineligible, names=names, days=days)
    build = CubeBuild(cube=cube, trading_days=days, adjusted=adjusted, bar_failures={}, static_used=())
    return World(build, bars(7, days), groups, window)


# A world large enough for the composite: the members warm up over 253 bars, a regression needs 30 names.
LONG_DAYS = weekdays(date(2016, 1, 4), 560)
LONG_WINDOW = (LONG_DAYS[260], LONG_DAYS[520])
SECTORS = ("a", "b", "c", "d")


@lru_cache(maxsize=4)
def long_world(prefix: str = "S", per_sector: int = 12) -> World:
    """Shared between tests, which never mutate a world: ``poisoned_world`` and ``with_bars`` return new ones."""
    names = tuple(f"{prefix}{j}" for j in range(per_sector * len(SECTORS)))
    groups = np.repeat(np.array(SECTORS), per_sector)
    return make_world(names=names, groups=groups, days=LONG_DAYS, window=LONG_WINDOW)


def poisoned(frame: pd.DataFrame, first_day: date, factor: float = 1000.0) -> pd.DataFrame:
    """A copy of ``frame`` with every price and volume from ``first_day`` on multiplied by ``factor``."""
    out = frame.copy()
    from_day = pd.DatetimeIndex(out.index).tz_convert(ET_TZ).date >= first_day
    columns = ["Open", "High", "Low", "Close", "Volume"]
    out.loc[from_day, columns] = out.loc[from_day, columns].to_numpy() * factor
    return out


def poisoned_world(world: World, first_day: date) -> World:
    adjusted = {name: poisoned(frame, first_day) for name, frame in world.build.adjusted.items()}
    return world.with_bars(adjusted, poisoned(world.spy, first_day))


class StubBook:
    """Hands back canned member panels, in the shape ``ScoreBook.panel`` does."""

    def __init__(self, panels: Mapping[str, np.ndarray]):
        self.panels = dict(panels)

    def panel(self, expression: str) -> np.ndarray:
        return self.panels[expression]


def stub_arrays(
    panels: Mapping[str, np.ndarray],
    groups: np.ndarray,
    *,
    eligible: np.ndarray | None = None,
    beta: np.ndarray | None = None,
    log_adv: np.ndarray | None = None,
    target: np.ndarray | None = None,
    horizon: int = 20,
    seed: int = 3,
) -> ICArrays:
    """``ICArrays`` over canned member panels: no bars or DSL, for tests of the construction."""
    shape = next(iter(panels.values())).shape
    rng = np.random.default_rng(seed)
    target = rng.normal(size=shape) if target is None else target
    return ICArrays(
        sessions=weekdays(date(2017, 1, 2), shape[0]),
        symbols=tuple(f"N{j}" for j in range(shape[1])),
        offset=0,
        eligible=np.ones(shape, bool) if eligible is None else eligible,
        groups=np.asarray(groups, dtype=str),
        log_adv=rng.normal(16.0, 1.0, shape) if log_adv is None else log_adv,
        beta_60=rng.normal(1.0, 0.3, shape) if beta is None else beta,
        returns={horizon: target},
        residuals={horizon: target},
        spy_returns={horizon: np.zeros(shape[0])},
        book=StubBook(panels),
    )


def shipped_composite() -> LoadedComposite:
    return load_composite(COMPOSITE_PATH)


def shrunk(
    protocol: CompositeProtocol,
    *,
    window: tuple[date, date] | None = None,
    reported_horizons: tuple[int, ...] | None = None,
    draws: int = 200,
    c_replicates: int = 4,
    shuffle_replicates: int = 2,
    max_acceptance: float | None = None,
    a_replicates: int = 3,
    decisive_lambda: float | None = None,
    reported_lambdas: tuple[float, ...] = (),
    sectors_sha256: str | None = None,
) -> CompositeProtocol:
    """The shipped protocol with small check loops, so a test stays fast; its construction is untouched."""
    c_ic = protocol.checks.c_ic.model_copy(
        update={
            "replicates": c_replicates,
            "session_shuffle_replicates": shuffle_replicates,
            "max_acceptance": protocol.checks.c_ic.max_acceptance if max_acceptance is None else max_acceptance,
        }
    )
    a_ic = protocol.checks.a_ic.model_copy(
        update={
            "replicates": a_replicates,
            "reported_lambdas": reported_lambdas,
            "decisive_lambda": protocol.checks.a_ic.decisive_lambda if decisive_lambda is None else decisive_lambda,
        }
    )
    update: dict = {
        "bootstrap": protocol.bootstrap.model_copy(update={"draws": draws}),
        "checks": protocol.checks.model_copy(update={"c_ic": c_ic, "a_ic": a_ic}),
    }
    if window is not None:
        update["window"] = window
    if reported_horizons is not None:
        update["reported_horizons"] = reported_horizons
    if sectors_sha256 is not None:
        update["sectors"] = protocol.sectors.model_copy(update={"sha256": sectors_sha256})
    return protocol.model_copy(update=update)


def sector_map(names_groups: Mapping[str, str], sha256: str = "f" * 64) -> LoadedSectors:
    table = {name: {"cik": 1, "sic": 1, "group": group} for name, group in names_groups.items()}
    return LoadedSectors(document={"symbols": table}, sha256=sha256)


def world_sectors(*worlds: World, sha256: str = "f" * 64) -> LoadedSectors:
    pairs = {
        symbol: str(group)
        for world in worlds
        for symbol, group in zip(world.build.cube.symbols, world.groups, strict=True)
    }
    return sector_map(pairs, sha256)


def environment(revision: str = "abc123") -> dict:
    return {"runtime": {"revision": revision}}


def as_builder(world: World) -> Callable[[], Awaitable[tuple[CubeBuild, pd.DataFrame]]]:
    async def build() -> tuple[CubeBuild, pd.DataFrame]:
        return world.build, world.spy

    return build


def passed_check(
    directory: Path,
    *,
    composite_sha256: str,
    sectors_sha256: str,
    cube_sha256: str,
    revision: str = "abc123",
    status: str = "passed",
    kind: str = "ic_checks",
) -> Path:
    """A check directory as ``execute_checks`` leaves one, without running the replicates."""
    directory.mkdir(parents=True)
    save_json_report(
        {"check": kind, "environment": environment(revision), "composite_sha256": composite_sha256},
        directory / "manifest.json",
    )
    save_json_report(
        {
            "status": status,
            "composite_sha256": composite_sha256,
            "sectors_sha256": sectors_sha256,
            "cube_sha256": cube_sha256,
        },
        directory / "result.json",
    )
    return directory


def loaded_composite(protocol: CompositeProtocol, sha256: str = "e" * 64) -> LoadedComposite:
    return LoadedComposite(protocol=protocol, sha256=sha256, path=COMPOSITE_PATH)
