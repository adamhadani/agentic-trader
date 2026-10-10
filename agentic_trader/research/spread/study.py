"""Stage runner, check gates and the three executors of the spread-reversion lane.

Executors write ``protocol.json`` and ``manifest.json`` before any input is built, record every
failure in ``result.json`` instead of raising, and never read a confirmation session before the
journal has recorded the interval's single use.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from agentic_trader.research.pooled.campaign_run import require_clean_revision
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.research.spread.evaluate import evaluate_stage
from agentic_trader.research.spread.formation import CoverageError, PairFit, select_pairs
from agentic_trader.research.spread.panel import PanelBuild, SpreadPanel, shift_panel, synthetic_panel
from agentic_trader.research.spread.protocol import (
    LANE,
    LoadedSpreadCohort,
    LoadedSpreadProtocol,
    SpreadCohort,
    SpreadProtocol,
)
from agentic_trader.research.spread.schedule import Window, stage_windows
from agentic_trader.research.spread.trading import PairSimulation, Trade, lane_series, simulate_pair
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "StageRun",
    "execute_spread_null",
    "execute_spread_power",
    "execute_spread_study",
    "run_stage",
    "spread_gate",
    "truncate_panel",
]

Stage = Literal["discovery", "confirmation"]
POWER_CHECK = "power_a"
NULL_CHECK = "null_c"


@dataclass
class StageRun:
    status: str
    error: str | None
    evaluation: dict
    trades: list[Trade]
    lane_by_cost: dict[float, np.ndarray]
    market: np.ndarray
    windows: list[dict] = field(default_factory=list)


def truncate_panel(panel: SpreadPanel, *, through: date) -> SpreadPanel:
    """Sessions on or before ``through`` only: the discovery stage and the null check never see later bars."""
    keep = np.array([s <= through for s in panel.sessions], dtype=bool)
    return SpreadPanel(
        sessions=tuple(s for s, k in zip(panel.sessions, keep, strict=True) if k),
        closes=panel.closes.loc[keep],
        opens=panel.opens.loc[keep],
    )


def _selected_record(fit: PairFit) -> dict:
    return {
        "y": fit.y,
        "x": fit.x,
        "sector": fit.sector,
        "beta": fit.beta,
        "t_stat": fit.t_stat,
        "half_life": fit.half_life,
    }


def _window_record(
    window: Window, sessions: Sequence[date], counts: dict, selected: Sequence[PairFit], planted: set
) -> dict:
    record = {
        "index": window.index,
        "formation": (sessions[window.formation[0]].isoformat(), sessions[window.formation[1] - 1].isoformat()),
        "trading": (sessions[window.trading[0]].isoformat(), sessions[window.trading[1] - 1].isoformat()),
        "formation_counts": counts,
        "selected": [_selected_record(f) for f in selected],
    }
    if planted:
        chosen = {f.key for f in selected}
        record["planted_recall"] = len(chosen & planted) / len(planted)
        record["planted_share"] = len(chosen & planted) / len(chosen) if chosen else 0.0
    return record


def run_stage(
    panel: SpreadPanel,
    protocol: SpreadProtocol,
    cohort: SpreadCohort,
    stage: Stage,
    *,
    planted: Sequence[tuple[str, str]] = (),
    progress: Callable[[str], None] | None = None,
) -> StageRun:
    """Formation, trading and evaluation over the stage's tiles; fails closed on thin coverage."""
    sessions = panel.sessions
    windows = stage_windows(
        sessions,
        protocol.stage(stage),
        formation_sessions=protocol.schedule.formation_sessions,
        trading_sessions=protocol.schedule.trading_sessions,
    )
    names = [s for s in cohort.symbols if s in panel.closes.columns]
    pairs = cohort.pairs()
    planted_set = set(planted)
    trades: list[Trade] = []
    lanes: dict[float, list[np.ndarray]] = {c: [] for c in protocol.costs_bps_per_side}
    market_parts: list[np.ndarray] = []
    records: list[dict] = []
    market_closes = panel.closes[cohort.market].to_numpy(dtype=float) if cohort.market in panel.closes else None
    for window in windows:
        f0, f1 = window.formation
        t0, t1 = window.trading
        try:
            selected, counts = select_pairs(panel.closes.iloc[f0:f1][names], pairs, protocol.formation)
        except CoverageError as exc:
            return StageRun("coverage_failed", f"window {window.index}: {exc}", {}, trades, {}, np.zeros(0), records)
        simulations: list[PairSimulation] = []
        for fit in selected:
            cy = panel.closes[fit.y].to_numpy(dtype=float)[t0:t1]
            cx = panel.closes[fit.x].to_numpy(dtype=float)[t0:t1]
            oy = panel.opens[fit.y].to_numpy(dtype=float)[t0:t1]
            ox = panel.opens[fit.x].to_numpy(dtype=float)[t0:t1]
            simulations.append(simulate_pair(fit, cy, cx, oy, ox, protocol.trading, window=window.index))
        for cost in protocol.costs_bps_per_side:
            lanes[cost].append(
                lane_series(simulations, slots=protocol.formation.top_pairs, cost_bps=cost, length=t1 - t0)
            )
        if market_closes is not None:
            prices = market_closes[t0 - 1 : t1]
            with np.errstate(invalid="ignore", divide="ignore"):
                market_parts.append(prices[1:] / prices[:-1] - 1.0)
        else:
            market_parts.append(np.full(t1 - t0, np.nan))
        for simulation in simulations:
            trades.extend(simulation.trades)
        records.append(_window_record(window, sessions, counts, selected, planted_set))
        if progress is not None:
            progress(
                f"{stage} window {window.index + 1}/{len(windows)}: {counts['selected']} pairs, {len(trades)} trades so far"
            )
    lane_by_cost = {c: (np.concatenate(parts) if parts else np.zeros(0)) for c, parts in lanes.items()}
    market = np.concatenate(market_parts) if market_parts else np.zeros(0)
    evaluation = evaluate_stage(
        trades, lane_by_cost, market, windows=len(windows), protocol=protocol, rules=protocol.rules(stage)
    )
    return StageRun("completed", None, evaluation, trades, lane_by_cost, market, records)


def spread_gate(directory: Path, *, check: str, protocol_sha256: str, cohort_sha256: str, revision: str) -> dict:
    """A check's passed result for this protocol and cohort at this exact code revision."""
    manifest = json.loads((directory / "manifest.json").read_text())
    result = json.loads((directory / "result.json").read_text())
    if manifest.get("check") != check:
        raise ValueError(f"{directory} holds a {manifest.get('check')!r} result, not {check!r}")
    if result.get("status") != "passed":
        raise ValueError(f"{check} has not passed (status {result.get('status')!r})")
    if result.get("protocol_sha256") != protocol_sha256:
        raise ValueError(f"{check} was run under a different protocol")
    if result.get("cohort_sha256") != cohort_sha256:
        raise ValueError(f"{check} was run on a different cohort")
    ran_at = manifest.get("environment", {}).get("runtime", {}).get("revision")
    if ran_at != revision:
        raise ValueError(f"{check} ran at code revision {ran_at!r}; this study runs at {revision!r}")
    return result


def _start(
    loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort, directory: Path, manifest: dict, environment: dict
) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**loaded.protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "lane": LANE,
            "entry_id": loaded.protocol.id,
            "version": loaded.protocol.version,
            "protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
            **manifest,
        },
        directory / "manifest.json",
    )


def _require_cohort(loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort) -> None:
    if cohort.sha256 != loaded.protocol.cohort_sha256:
        raise ValueError("cohort file does not match the protocol's cohort_sha256")


def _holds(evaluation: dict) -> dict:
    return {k: bool(evaluation.get(k, {}).get("holds", False)) for k in ("s1", "s2", "s3", "s4")}


def _seed_summary(seed: int, run: StageRun) -> dict:
    return {
        "seed": seed,
        "status": run.status,
        "error": run.error,
        "passes": bool(run.evaluation.get("passes", False)),
        "holds": _holds(run.evaluation),
        "closed_trades": len(run.trades),
        "planted_recall": float(np.mean([w.get("planted_recall", np.nan) for w in run.windows]))
        if run.windows
        else float("nan"),
        "lane_mean_daily": run.evaluation.get("s1", {}).get("mean", float("nan")),
    }


def _finish(directory: Path, result: dict) -> dict:
    save_json_report(_finite_json(result), directory / "result.json")
    return result


def _base(loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort, check: str | None) -> dict:
    base = {
        "lane": LANE,
        "protocol_sha256": loaded.sha256,
        "cohort_sha256": cohort.sha256,
        "authorizes_promotion": False,
    }
    return {**base, "check": check} if check else base


async def execute_spread_power(
    loaded: LoadedSpreadProtocol,
    directory: Path,
    *,
    cohort: LoadedSpreadCohort,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Check A: the discovery pipeline on synthetic worlds with planted cointegrated pairs."""
    protocol = loaded.protocol
    _start(loaded, cohort, directory, {"check": POWER_CHECK, "reads": "synthetic panels only"}, environment)
    base = _base(loaded, cohort, POWER_CHECK)
    try:
        _require_cohort(loaded, cohort)
        sessions = tuple(d.date() for d in pd.bdate_range(protocol.bars.start, protocol.windows.discovery[1]))
        seeds: list[dict] = []
        for seed in range(protocol.power.seeds):
            panel, planted = await asyncio.to_thread(
                synthetic_panel, cohort.cohort, sessions, protocol.power, seed=seed
            )
            run = await asyncio.to_thread(
                run_stage, panel, protocol, cohort.cohort, "discovery", planted=planted, progress=progress
            )
            seeds.append(_seed_summary(seed, run))
        passes = sum(1 for s in seeds if s["passes"])
        result = {
            **base,
            "status": "passed" if passes >= protocol.power.min_pass else "failed",
            "calendar": "weekdays",
            "seeds": seeds,
            "passes": passes,
            "required": protocol.power.min_pass,
        }
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return _finish(directory, result)


async def execute_spread_null(
    loaded: LoadedSpreadProtocol,
    directory: Path,
    *,
    cohort: LoadedSpreadCohort,
    build: Callable[[], Awaitable[PanelBuild]],
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Check C: the discovery pipeline on per-symbol shifted discovery bars (no comovement to find)."""
    protocol = loaded.protocol
    _start(
        loaded,
        cohort,
        directory,
        {"check": NULL_CHECK, "reads": "discovery sessions only, shifted per symbol"},
        environment,
    )
    base = _base(loaded, cohort, NULL_CHECK)
    try:
        _require_cohort(loaded, cohort)
        built = await build()
        panel = truncate_panel(built.panel, through=protocol.windows.discovery[1])
        seeds: list[dict] = []
        for seed in range(protocol.null_check.seeds):
            shifted = await asyncio.to_thread(
                shift_panel,
                panel,
                seed=seed,
                block_sessions=protocol.null_check.shift_block_sessions,
                groups=cohort.cohort.sectors,
            )
            run = await asyncio.to_thread(run_stage, shifted, protocol, cohort.cohort, "discovery", progress=progress)
            seeds.append(_seed_summary(seed, run))
        passes = sum(1 for s in seeds if s["passes"])
        result = {
            **base,
            "status": "passed" if passes <= protocol.null_check.max_pass else "failed",
            "seeds": seeds,
            "passes": passes,
            "allowed": protocol.null_check.max_pass,
            "bar_failures": dict(built.bar_failures),
        }
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return _finish(directory, result)


def _save_frame(frame: pd.DataFrame, path: Path) -> None:
    with gzip.open(path, "wt", newline="") as handle:
        frame.to_csv(handle, index=False)


def _stage_payload(run: StageRun) -> dict:
    return {
        "status": run.status,
        "error": run.error,
        "evaluation": run.evaluation,
        "windows": run.windows,
        "closed_trades": len(run.trades),
    }


def _persist_stage(directory: Path, stage: str, run: StageRun) -> None:
    _save_frame(pd.DataFrame([asdict(t) for t in run.trades]), directory / f"{stage}-trades.csv.gz")
    lane = pd.DataFrame({f"lane_{c:g}bps": v for c, v in run.lane_by_cost.items()})
    lane["market"] = run.market
    _save_frame(lane, directory / f"{stage}-lane.csv.gz")


async def execute_spread_study(
    loaded: LoadedSpreadProtocol,
    directory: Path,
    *,
    cohort: LoadedSpreadCohort,
    build: Callable[[], Awaitable[PanelBuild]],
    environment: dict,
    power_dir: Path,
    null_dir: Path,
    journal: Mapping,
    confirm: Callable[[tuple[date, date], Mapping], Awaitable[Mapping]],
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Discovery, then (only after the journal records the interval) confirmation; never promotes."""
    protocol = loaded.protocol
    _start(
        loaded,
        cohort,
        directory,
        {"journal": dict(journal), "gates": {"power": str(power_dir), "null": str(null_dir)}},
        environment,
    )
    base = _base(loaded, cohort, None)
    try:
        revision = require_clean_revision(environment)
        _require_cohort(loaded, cohort)
        gates = {
            "power": spread_gate(
                power_dir,
                check=POWER_CHECK,
                protocol_sha256=loaded.sha256,
                cohort_sha256=cohort.sha256,
                revision=revision,
            ),
            "null": spread_gate(
                null_dir,
                check=NULL_CHECK,
                protocol_sha256=loaded.sha256,
                cohort_sha256=cohort.sha256,
                revision=revision,
            ),
        }
        built = await build()
        discovery_panel = truncate_panel(built.panel, through=protocol.windows.discovery[1])
        discovery = await asyncio.to_thread(
            run_stage, discovery_panel, protocol, cohort.cohort, "discovery", progress=progress
        )
        await asyncio.to_thread(_persist_stage, directory, "discovery", discovery)
        result: dict = {
            **base,
            "status": "completed",
            "code_revision": revision,
            "gates": {k: {"status": v["status"], "passes": v.get("passes")} for k, v in gates.items()},
            "bar_failures": dict(built.bar_failures),
            "discovery": _stage_payload(discovery),
            "confirmation": None,
            "confirmation_record": None,
        }
        if discovery.status != "completed" or not discovery.evaluation["passes"]:
            result["decision"] = "failed_discovery"
            return _finish(directory, result)
        detail = {
            "protocol_sha256": loaded.sha256,
            "code_revision": revision,
            "discovery": {k: discovery.evaluation[k] for k in ("s1", "s2", "s3", "s4")},
        }
        record = await confirm(protocol.windows.confirmation, detail)
        confirmation = await asyncio.to_thread(
            run_stage, built.panel, protocol, cohort.cohort, "confirmation", progress=progress
        )
        await asyncio.to_thread(_persist_stage, directory, "confirmation", confirmation)
        result["confirmation_record"] = dict(record)
        result["confirmation"] = _stage_payload(confirmation)
        passed = confirmation.status == "completed" and confirmation.evaluation["passes"]
        result["decision"] = "confirmed" if passed else "failed_confirmation"
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "confirmation": None}
    return _finish(directory, result)
