"""Checks C-IC and A-IC, which must pass before the gate reads anything (research only).

Both run on the gate's own cohort, target and window, through ``gate_read``, the gate's own rule.

**C-IC, the negative control.** Decisive: one within-sector name permutation applied to the composite for all
sessions, one seed per replicate. The score keeps its persistence and sector shape and loses name identity,
which keeps the autocorrelation that makes an overlapping target dangerous. Reported: names permuted within
session and sector, which removes that persistence. It passes if the gate rule accepts at most the protocol's
share of the fixed-permutation replicates.

**A-IC, the planted effect.** ``score = sqrt(1 - lam^2) z(base) + lam z(rank(target))``, per session over pairs
with a finite base and a finite target in the mask. Decisive base: the fixed-permutation composite. Reported: a
pure per-name AR(1) base and the other values of ``lam``. It passes if the gate rule accepts at least the
protocol's share of the replicates at the decisive ``lam``.

A-IC catches a read whose target has drifted from the target the effect was planted on, once the drift removes
the target's own overlap: at a 20-session horizon a one-session shift keeps about 95% of the plant and still
passes, and a shift of the whole horizon removes it. A fault common to both, such as arrays misdated as a whole,
is out of its reach. ``test_arrays`` pins the arrays' dates, session by session.

A-IC is a plumbing check, not a power statement: the permuted base breaks the link between a name's score and its
own factor loadings, so its power is overstated. Every read therefore reports ``min_detectable_ic``, the gate's
``t`` times the read's own ``se``, which is the level with 50% power, and ``power_at``, the power at fixed levels of
IC from the same ``se``. On the real composite those are the honest measures. They decide nothing.

Neither check computes the IC of the real, unpermuted composite. That read belongs to the gate alone.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from agentic_trader.research.alpha.panel import cross_sectional_rank, cross_sectional_zscore
from agentic_trader.research.ic.arrays import build_ic_arrays
from agentic_trader.research.ic.composite import composite_panel
from agentic_trader.research.ic.protocol import CompositeProtocol, LoadedComposite
from agentic_trader.research.ic.sectors import UNKNOWN, LoadedSectors
from agentic_trader.research.ic.statistic import gate_verdict, rank_ic_series
from agentic_trader.research.pooled.campaign_run import require_clean_revision
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "CHECKS_KIND",
    "a_ic",
    "ar1_base",
    "c_ic",
    "execute_checks",
    "failed_outcome",
    "fixed_permutation",
    "gate_read",
    "mapped_floor_reason",
    "mapped_fraction",
    "planted_score",
    "power_at",
    "require_checks",
    "require_pinned_sectors",
    "session_shuffle",
    "write_manifest",
]

CHECKS_KIND = "ic_checks"
FIXED_PERMUTATION = "fixed_permutation"
AR1 = "ar1"
POWER_AT_IC = (0.02, 0.03)

Progress = Callable[[str], None] | None


def _say(progress: Progress, message: str) -> None:
    if progress is not None:
        progress(message)


def failed_outcome(exc: Exception, what: str, progress: Progress) -> dict:
    """A fault's outcome. Provider text can sit in the message, so the result keeps the type name only."""
    _say(progress, f"{what} failed: {type(exc).__name__}: {exc}")
    return {"status": "failed", "reason": type(exc).__name__}


def mapped_fraction(groups: np.ndarray) -> float:
    return float((np.asarray(groups, dtype=str) != UNKNOWN).mean())


def mapped_floor_reason(protocol: CompositeProtocol, groups: np.ndarray) -> str | None:
    """Why the decisive cube's sector coverage stops the lane, or None when it is at or above the floor."""
    fraction, floor = mapped_fraction(groups), protocol.sectors.min_mapped_fraction
    if fraction >= floor:
        return None
    return f"only {fraction:.1%} of the decisive cohort's names have a sector; the floor is {floor:.1%}"


def require_pinned_sectors(protocol: CompositeProtocol, sectors: LoadedSectors) -> None:
    if sectors.sha256 != protocol.sectors.sha256:
        raise ValueError(f"the sector map is {sectors.sha256}, not the {protocol.sectors.sha256} the composite pins")


def write_manifest(
    kind: str, loaded: LoadedComposite, sectors: LoadedSectors, environment: dict, output: Path, **details
) -> None:
    """A new private directory and its manifest, before anything is computed."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report(
        {
            "check": kind,
            "composite_sha256": loaded.sha256,
            "sectors_sha256": sectors.sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
            **details,
        },
        output / "manifest.json",
    )


def power_at(se: float, min_t: float) -> dict[str, float]:
    """``Phi(ic / se - min_t)`` per level of IC; NaN when the read has no usable ``se``."""
    if not math.isfinite(se) or se <= 0:
        return {str(ic): float("nan") for ic in POWER_AT_IC}
    return {str(ic): float(norm.cdf(ic / se - min_t)) for ic in POWER_AT_IC}


def gate_read(score: np.ndarray, target: np.ndarray, mask: np.ndarray, protocol: CompositeProtocol) -> dict:
    """The gate's rule on one score panel: the one path by which the gate and both checks read a score.

    ``min_detectable_ic``, ``power_at`` and ``median_pairs_per_session`` (over evaluated sessions) describe the read;
    the rule does not use them.
    """
    rule = protocol.gate
    ic = rank_ic_series(score, target, mask, min_names=protocol.min_names)
    verdict = gate_verdict(
        ic,
        protocol.horizon,
        min_t=rule.min_t,
        blocks=rule.blocks,
        min_positive_blocks=rule.min_positive_blocks,
        draws=protocol.bootstrap.draws,
        seed=protocol.bootstrap.seed,
    )
    pairs = (np.asarray(mask, dtype=bool) & np.isfinite(score) & np.isfinite(target)).sum(axis=1)
    evaluated = pairs[np.isfinite(ic)]
    return {
        **verdict,
        "min_detectable_ic": rule.min_t * verdict["se"],
        "power_at": power_at(verdict["se"], rule.min_t),
        "median_pairs_per_session": float(np.median(evaluated)) if evaluated.size else float("nan"),
    }


def fixed_permutation(groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Column order ``[N]``: name ``i`` takes the score of name ``order[i]``, always one of its own sector."""
    groups = np.asarray(groups, dtype=str)
    order = np.arange(groups.size)
    for group in np.unique(groups):
        columns = np.flatnonzero(groups == group)
        order[columns] = rng.permutation(columns)
    return order


def session_shuffle(composite: np.ndarray, groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Names permuted within session and sector, independently each session."""
    groups = np.asarray(groups, dtype=str)
    out = composite.copy()
    for group in np.unique(groups):
        columns = np.flatnonzero(groups == group)
        out[:, columns] = rng.permuted(composite[:, columns], axis=1)
    return out


def ar1_base(shape: tuple[int, int], phi: float, rng: np.random.Generator) -> np.ndarray:
    """Independent unit-variance AR(1) noise per name, stationary from the first session."""
    noise = rng.standard_normal(shape)
    scale = math.sqrt(1.0 - phi * phi)
    out = np.empty(shape)
    out[0] = noise[0]
    for s in range(1, shape[0]):
        out[s] = phi * out[s - 1] + scale * noise[s]
    return out


def _plant_terms(base: np.ndarray, target: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``z(base)`` and ``z(rank(target))`` per session, over pairs with both finite in the mask."""
    pairs = np.asarray(mask, dtype=bool) & np.isfinite(base) & np.isfinite(target)
    z_base = cross_sectional_zscore(pd.DataFrame(np.where(pairs, base, np.nan)))
    z_rank = cross_sectional_zscore(cross_sectional_rank(pd.DataFrame(np.where(pairs, target, np.nan))))
    return z_base.to_numpy(dtype=float), z_rank.to_numpy(dtype=float)


def _plant(z_base: np.ndarray, z_rank: np.ndarray, lam: float) -> np.ndarray:
    return math.sqrt(1.0 - lam * lam) * z_base + lam * z_rank


def planted_score(base: np.ndarray, target: np.ndarray, mask: np.ndarray, lam: float) -> np.ndarray:
    return _plant(*_plant_terms(base, target, mask), lam)


def _finite_mean(values: Sequence[float]) -> float:
    kept = [value for value in values if math.isfinite(value)]
    return float(np.mean(kept)) if kept else float("nan")


def _finite_sd(values: Sequence[float]) -> float:
    kept = [value for value in values if math.isfinite(value)]
    return float(np.std(kept, ddof=1)) if len(kept) > 1 else float("nan")


def _finite_median(values: Sequence[float]) -> float:
    kept = [value for value in values if math.isfinite(value)]
    return float(np.median(kept)) if kept else float("nan")


def _tally(reads: Sequence[dict]) -> dict:
    accepted = sum(1 for read in reads if read["passes"])
    return {
        "replicates": len(reads),
        "accepted": accepted,
        "acceptance": accepted / len(reads) if reads else float("nan"),
        "mean_ic": _finite_mean([read["mean"] for read in reads]),
        "t_mean": _finite_mean([read["t"] for read in reads]),
        "t_sd": _finite_sd([read["t"] for read in reads]),
        "sessions_evaluated": max((read["n_sessions"] for read in reads), default=0),
        "median_pairs_per_session": _finite_median([read["median_pairs_per_session"] for read in reads]),
    }


def c_ic(
    protocol: CompositeProtocol,
    composite: np.ndarray,
    target: np.ndarray,
    mask: np.ndarray,
    groups: np.ndarray,
    *,
    progress: Progress = None,
) -> dict:
    spec = protocol.checks.c_ic
    fixed = []
    for replicate in range(spec.replicates):
        order = fixed_permutation(groups, np.random.default_rng(spec.seed + replicate))
        fixed.append(gate_read(composite[:, order], target, mask, protocol))
        _say(progress, f"C-IC fixed permutation {replicate + 1}/{spec.replicates}")
    shuffled = []
    for replicate in range(spec.session_shuffle_replicates):
        rng = np.random.default_rng(spec.seed + replicate)
        shuffled.append(gate_read(session_shuffle(composite, groups, rng), target, mask, protocol))
        _say(progress, f"C-IC session shuffle {replicate + 1}/{spec.session_shuffle_replicates}")
    decisive = _tally(fixed)
    return {
        "passes": decisive["acceptance"] <= spec.max_acceptance,
        "max_acceptance": spec.max_acceptance,
        FIXED_PERMUTATION: decisive,
        "session_shuffle": _tally(shuffled),
    }


def a_ic(
    protocol: CompositeProtocol,
    composite: np.ndarray,
    target: np.ndarray,
    mask: np.ndarray,
    groups: np.ndarray,
    *,
    progress: Progress = None,
) -> dict:
    spec = protocol.checks.a_ic
    lambdas = sorted({spec.decisive_lambda, *spec.reported_lambdas})
    reads: dict[str, dict[float, list[dict]]] = {
        base: {lam: [] for lam in lambdas} for base in (FIXED_PERMUTATION, AR1)
    }
    for replicate in range(spec.replicates):
        rng = np.random.default_rng(spec.seed + replicate)
        bases = {
            FIXED_PERMUTATION: composite[:, fixed_permutation(groups, rng)],
            AR1: ar1_base(composite.shape, spec.ar1_phi, rng),
        }
        for name, base in bases.items():
            z_base, z_rank = _plant_terms(base, target, mask)
            for lam in lambdas:
                reads[name][lam].append(gate_read(_plant(z_base, z_rank, lam), target, mask, protocol))
        _say(progress, f"A-IC replicate {replicate + 1}/{spec.replicates}")

    def table(name: str) -> dict[str, dict]:
        rows = {}
        for lam, kept in reads[name].items():
            tally = _tally(kept)
            rows[str(lam)] = {
                "power": tally["acceptance"],
                "accepted": tally["accepted"],
                "replicates": tally["replicates"],
                "mean_ic": tally["mean_ic"],
                "t_mean": tally["t_mean"],
                "t_sd": tally["t_sd"],
                "median_pairs_per_session": tally["median_pairs_per_session"],
            }
        return rows

    decisive = table(FIXED_PERMUTATION)
    power = decisive[str(spec.decisive_lambda)]["power"]
    return {
        "passes": power >= spec.min_power,
        "power": power,
        "decisive_lambda": spec.decisive_lambda,
        "min_power": spec.min_power,
        FIXED_PERMUTATION: decisive,
        AR1: table(AR1),
    }


def _run_replicates(protocol: CompositeProtocol, built, spy, groups: np.ndarray, progress: Progress) -> dict:
    arrays = build_ic_arrays(built, spy, groups, protocol.window, horizons=(protocol.horizon,))
    composite = composite_panel(protocol, arrays)
    target = arrays.residuals[protocol.horizon]
    return {
        "n_sessions": len(arrays.sessions),
        "n_symbols": len(arrays.symbols),
        "c_ic": c_ic(protocol, composite, target, arrays.eligible, arrays.groups, progress=progress),
        "a_ic": a_ic(protocol, composite, target, arrays.eligible, arrays.groups, progress=progress),
    }


def _failure_reason(outcome: dict) -> str | None:
    reasons = []
    if not outcome["c_ic"]["passes"]:
        reasons.append(
            f"C-IC accepted {outcome['c_ic'][FIXED_PERMUTATION]['acceptance']:.1%} of the fixed-permutation "
            "replicates: the statistic is wrong, so fix it and re-run before anything else"
        )
    if not outcome["a_ic"]["passes"]:
        reasons.append(
            f"A-IC power {outcome['a_ic']['power']:.1%} at lambda {outcome['a_ic']['decisive_lambda']}: "
            "the window cannot decide this question and the gate does not run"
        )
    return "; ".join(reasons) or None


async def _checks_outcome(
    protocol: CompositeProtocol,
    build: Callable[[], Awaitable[tuple]],
    sectors: LoadedSectors,
    environment: dict,
    progress: Progress,
) -> dict:
    try:
        require_clean_revision(environment)
        require_pinned_sectors(protocol, sectors)
    except ValueError as exc:  # Only these deliberate refusals: their text is fixed, a build's error is not.
        return {"status": "failed", "reason": str(exc)}
    built, spy = await build()
    cube = built.cube
    groups = sectors.groups(cube.symbols)
    outcome: dict = {"cube_sha256": cube.sha256, "mapped_fraction": mapped_fraction(groups)}
    floor = mapped_floor_reason(protocol, groups)
    if floor is not None:
        return {**outcome, "status": "failed", "reason": floor}
    replicates = await asyncio.to_thread(_run_replicates, protocol, built, spy, groups, progress)
    reason = _failure_reason(replicates)
    return {**outcome, **replicates, "status": "failed" if reason else "passed", "reason": reason}


async def execute_checks(
    loaded: LoadedComposite,
    output: Path,
    *,
    build: Callable[[], Awaitable[tuple]],
    sectors: LoadedSectors,
    environment: dict,
    progress: Progress = None,
) -> dict:
    """C-IC and A-IC on the decisive cohort; every exit after the manifest writes ``result.json``, with no timestamp."""
    write_manifest(
        CHECKS_KIND,
        loaded,
        sectors,
        environment,
        output,
        reads="the decisive cohort's residual target against permuted and synthetic scores; never the real composite",
    )
    base = {"composite_sha256": loaded.sha256, "sectors_sha256": sectors.sha256, "authorizes_promotion": False}
    try:
        outcome = await _checks_outcome(loaded.protocol, build, sectors, environment, progress)
    except Exception as exc:
        outcome = failed_outcome(exc, "the checks", progress)
    result = {**outcome, **base}
    save_json_report(_finite_json(result), output / "result.json")
    return result


def _read_document(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"no readable check document at {path} ({type(exc).__name__})") from exc


def require_checks(directory: Path, *, composite_sha256: str, sectors_sha256: str, revision: str) -> dict:
    """The passed checks of this composite and sector map, at this exact code revision; a ``ValueError`` otherwise."""
    manifest = _read_document(directory / "manifest.json")
    result = _read_document(directory / "result.json")
    if manifest.get("check") != CHECKS_KIND:
        raise ValueError(f"{directory} holds a {manifest.get('check')!r} result, not {CHECKS_KIND!r}")
    if result.get("status") != "passed":
        raise ValueError(f"the checks have not passed (status {result.get('status')!r})")
    if result.get("composite_sha256") != composite_sha256:
        raise ValueError("the checks ran on a different composite")
    if result.get("sectors_sha256") != sectors_sha256:
        raise ValueError("the checks ran on a different sector map")
    ran_at = manifest.get("environment", {}).get("runtime", {}).get("revision")
    if ran_at != revision:
        raise ValueError(f"the checks ran at code revision {ran_at!r}; this read runs at {revision!r}")
    if not result.get("cube_sha256"):
        raise ValueError("the checks record no cube")
    return result
