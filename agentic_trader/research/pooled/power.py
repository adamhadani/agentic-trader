"""Power check A: can the campaign's stages detect a planted pooled edge, and reject noise?

Only discovery-window cells are read. Each replicate block-resamples whole discovery
sessions (keeping the real same-day dependence, eligibility and bracket outcomes) onto the
campaign's full session calendar, scores 200 null formulas and one planted formula as
per-name AR(1) fields, adds delta R to the planted formula's picks, and runs the
campaign's ``run_stages`` unchanged with an in-memory ledger.

Each replicate is seeded only by ``[seed, replicate]``, so the curve is identical for any
worker count.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    InMemoryLedger,
    LoadedProtocol,
    PowerSpec,
    ScoredFormula,
    run_stages,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import _ARRAYS, CubeView, LabelCube, check_coverage
from agentic_trader.research.pooled.formula import select_picks
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.setups.study import _finite_json, _stationary_index_draws
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["ar1_scores", "execute_power_check", "run_power", "synthetic_cube"]

PLANTED = "planted"


def synthetic_cube(base: CubeView, calendar: Sequence[date], rng: np.random.Generator, block_mean: float) -> LabelCube:
    """Whole base sessions, stationary-block resampled, laid over the full calendar.

    The calendar is longer than the base window, so it is filled by independent
    resamples of the base, each a full stationary bootstrap of its sessions.
    """
    n = len(base.sessions)
    chunks = int(np.ceil(len(calendar) / n))
    order = _stationary_index_draws(n, block_mean, chunks, int(rng.integers(0, 2**31)))
    index = order.reshape(-1)[: len(calendar)]
    arrays = {name: getattr(base, name)[index] for name in _ARRAYS}
    return LabelCube(
        spec_identity="synthetic",
        cohort_sha256="synthetic",
        sessions=tuple(calendar),
        symbols=base.symbols,
        arrays=arrays,
        coverage={},
    )


def ar1_scores(shape: tuple[int, int], phi: float, seed: int) -> np.ndarray:
    """Stationary unit-variance AR(1) per column (name): x[t] = phi x[t-1] + sqrt(1-phi^2) e[t]."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(shape)
    out = np.empty(shape)
    out[0] = noise[0]
    if shape[0] > 1:
        scale = np.sqrt(1 - phi**2)
        out[1:], _ = lfilter([scale], [1.0, -phi], noise[1:], axis=0, zi=(phi * noise[0])[None, :])
    return out


def _formula(formula_id: str, seed: int, phi: float, names: int) -> ScoredFormula:
    def panel(view: CubeView):
        # Only the prefix through this window is needed; the field is deterministic in (seed, shape).
        scores = ar1_scores((view.offset + len(view.sessions), names), phi, seed)
        return scores[view.offset :], np.ones(view.eligible.shape, dtype=bool)

    return ScoredFormula(formula_id=formula_id, nodes=3, panel=panel)


def tally_confirmed(confirmed: Iterable[str]) -> tuple[bool, bool]:
    """(planted formula confirmed, any null formula confirmed) for one campaign run."""
    ids = set(confirmed)
    return PLANTED in ids, bool(ids - {PLANTED})


def summarize_power(results: Mapping[int, Mapping[float, tuple[bool, bool]]], spec: PowerSpec) -> dict:
    """Detection/false-acceptance curve and the gate decision from per-replicate tallies."""
    detected = {d: sum(results[r][d][0] for r in results) for d in spec.deltas}
    false = {d: sum(results[r][d][1] for r in results) for d in spec.deltas}
    curve = {
        f"{delta:g}" if delta else "0.0": {
            "detection": detected[delta] / spec.replicates,
            "false_acceptance": false[delta] / spec.replicates,
        }
        for delta in spec.deltas
    }
    detection = detected[spec.detection_delta] / spec.replicates
    false_acceptance = false[0.0] / spec.replicates
    passed = detection >= spec.min_detection and false_acceptance <= spec.max_false_acceptance
    return {
        "status": "passed" if passed else "gate_failed",
        "curve": curve,
        "detection_at_gate": detection,
        "false_acceptance_at_zero": false_acceptance,
        "replicates": spec.replicates,
        "null_formulas": spec.null_formulas,
    }


def _replicate(
    protocol: CampaignProtocol, base: CubeView, calendar: Sequence[date], cohort_sha256: str, rep: int
) -> dict[float, tuple[bool, bool]]:
    """One replicate: per delta, (planted confirmed, any null confirmed)."""
    spec = protocol.power
    names = len(base.symbols)
    rng = np.random.default_rng([spec.seed, rep])
    cube = synthetic_cube(base, calendar, rng, protocol.bootstrap.block_mean)
    seeds = rng.integers(0, 2**31, spec.null_formulas + 1)
    planted = _formula(PLANTED, int(seeds[0]), spec.ar_phi, names)
    nulls = [_formula(f"null-{i:03d}", int(s), spec.ar_phi, names) for i, s in enumerate(seeds[1:])]
    rows, cols = [], []
    for start, end in (protocol.windows.discovery, protocol.windows.selection, protocol.windows.confirmation):
        view = cube.window(start, end)
        scores, allowed = planted.panel(view)
        picks = select_picks(scores, allowed, view, protocol.k)
        rows.append(picks.session_idx + view.offset)
        cols.append(picks.symbol_idx)
    cells = (np.concatenate(rows), np.concatenate(cols))
    out: dict[float, tuple[bool, bool]] = {}
    for delta in spec.deltas:
        shifted = cube.with_shift(*cells, delta) if delta else cube
        windows = CampaignWindows(shifted, protocol.windows, cohort_sha256=cohort_sha256)
        outcome = run_stages([planted, *nulls], windows, InMemoryLedger(), protocol, campaign_id=f"power-{rep}-{delta}")
        confirmed = set(outcome["confirmed"])
        out[delta] = tally_confirmed(confirmed)
    return out


_WORKER: dict = {}


def _init_worker(protocol: CampaignProtocol, base: CubeView, calendar: Sequence[date], cohort_sha256: str) -> None:
    _WORKER.update(protocol=protocol, base=base, calendar=calendar, cohort_sha256=cohort_sha256)


def _worker_replicate(rep: int) -> tuple[int, dict[float, tuple[bool, bool]]]:
    w = _WORKER
    return rep, _replicate(w["protocol"], w["base"], w["calendar"], w["cohort_sha256"], rep)


def run_power(
    protocol: CampaignProtocol,
    base: CubeView,
    calendar: Sequence[date],
    *,
    cohort_sha256: str,
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
) -> dict:
    spec = protocol.power
    results: dict[int, dict[float, tuple[bool, bool]]] = {}

    def done(rep: int, outcome: dict[float, tuple[bool, bool]]) -> None:
        results[rep] = outcome
        if progress is not None:
            progress(f"replicate {len(results)}/{spec.replicates}")

    if workers > 1:
        pool = ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(protocol, base, tuple(calendar), cohort_sha256)
        )
        try:
            for future in as_completed([pool.submit(_worker_replicate, rep) for rep in range(spec.replicates)]):
                done(*future.result())
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)  # stop promptly instead of finishing every replicate
            raise
        pool.shutdown()
    else:
        for rep in range(spec.replicates):
            done(rep, _replicate(protocol, base, calendar, cohort_sha256, rep))

    return summarize_power(results, spec)


async def execute_power_check(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    environment: dict,
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
) -> dict:
    protocol = loaded.protocol
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "power_a",
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "reads": "discovery window cells only",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {
        "cohort_sha256": cohort.sha256,
        "campaign_protocol_sha256": loaded.sha256,
        "authorizes_promotion": False,
    }
    try:
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        view = built.cube.window(*protocol.windows.discovery)
        calendar = built.cube.sessions
        outcome = await asyncio.to_thread(
            run_power, protocol, view, calendar, cohort_sha256=cohort.sha256, progress=progress, workers=workers
        )
        result = {**outcome, **base, "cube_sha256": built.cube.sha256}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", **base}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
