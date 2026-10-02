# agentic_trader/research/pooled/null_check.py
"""Check C: the full campaign on null panels where real formulas can have no edge.

**Null panels.** Each replicate block-resamples whole discovery sessions onto the full
calendar, as check A does. At each cost level, every labelled R then moves by its name's
offset: ``R - mean(name) + mean(all)``, both means over the labelled discovery cells.
Every cross-section is a real session, so names keep their real co-movement and factor
exposures. But no name's average differs from the cohort's, so no formula has a true
edge. The leg mean keeps its real level, so the leg gates behave as they will on real data.

**Stages.** The real per-family search, discovery finish, selection and confirmation run
unchanged with an in-memory ledger. A replicate that confirms anything is a false
acceptance.

**Reported, not gated.** The discovery t of each family's seed expressions, evaluated
before any selection in every replicate, is an unselected null sample. A standard
deviation well above 1 means the bootstrap understates the variance of
persistent-exposure formulas.

Scores use real bars over the full calendar; labels come from discovery cells only;
nothing is charged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    InMemoryLedger,
    LoadedProtocol,
    NullCheckSpec,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, check_coverage
from agentic_trader.research.pooled.genetic import NullCharger, run_search_stages
from agentic_trader.research.pooled.power import synthetic_cube
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook
from agentic_trader.research.pooled.study import check_power_gate
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["demeaned", "execute_null_check", "null_replicate", "run_null_check", "summarize_null"]


def demeaned(base: CubeView) -> CubeView:
    """Each name's labelled R moved so its mean is the mean over all labelled cells (each cost level)."""
    labelled = base.labelled
    counts = labelled.sum(axis=0)
    out: dict[str, np.ndarray] = {}
    for column in ("r_gross", "r_cost"):
        original = getattr(base, column)
        values = np.where(labelled, original, 0.0)
        overall = values.sum() / max(int(labelled.sum()), 1)
        name_mean = np.divide(values.sum(axis=0), counts, out=np.zeros(values.shape[1]), where=counts > 0)
        out[column] = np.where(labelled, original - name_mean + overall, np.nan)
    return replace(base, r_gross=out["r_gross"], r_cost=out["r_cost"])


def null_replicate(
    protocol: CampaignProtocol,
    base: CubeView,
    calendar: Sequence[date],
    cohort_sha256: str,
    book: ScoreBook,
    rep: int,
) -> dict:
    """One null campaign on a resampled demeaned panel; seeded only by ``[null_check.seed, rep]``."""
    spec = protocol.null_check
    if spec is None:
        raise ValueError("this protocol has no null_check (campaign v1); check C needs protocol v2")
    rng = np.random.default_rng([spec.seed, rep])
    cube = synthetic_cube(base, calendar, rng, protocol.bootstrap.block_mean, cohort_sha256=cohort_sha256)
    windows = CampaignWindows(cube, protocol.windows, cohort_sha256=cohort_sha256)
    outcome, search = run_search_stages(
        protocol, windows, book, InMemoryLedger(), NullCharger(), campaign_id=f"null-{rep}"
    )
    seeds = {canonical_expression(seed) for family in protocol.families for seed in family.seeds}
    seed_t = {
        record["expression"]: record["edge_t"]
        for record in search.records
        if record["status"] == "evaluated" and record["expression"] in seeds
    }
    return {
        "replicate": rep,
        "status": outcome["status"],
        "confirmed": [search.expressions[fid] for fid in outcome["confirmed"]],
        "survivors": sum(1 for row in outcome["discovery"] if row["passes"]),
        "carried": len(outcome["carried"]),
        "frozen": len(outcome["frozen"]),
        "evaluated": len(search.scores),
        "errors": sum(run.errors for run in search.runs),  # charged formulas whose evaluation raised
        "seed_t": seed_t,
    }


def summarize_null(replicates: Sequence[Mapping], spec: NullCheckSpec) -> dict:
    n = len(replicates)
    false = sum(1 for replicate in replicates if replicate["confirmed"])
    t_values = np.array(
        [t for replicate in replicates for t in replicate["seed_t"].values() if t is not None and np.isfinite(t)],
        dtype=float,
    )
    upper = 1.0 if false >= n else float(beta.ppf(0.95, false + 1, n - false))  # one-sided Clopper-Pearson
    return {
        "status": "passed" if false <= spec.max_false_acceptances else "gate_failed",
        "false_acceptances": false,
        "replicates": n,
        "false_acceptance_rate": false / n if n else float("nan"),
        "false_acceptance_upper95": upper,
        "max_false_acceptances": spec.max_false_acceptances,
        # Formulas whose evaluation raised, over all replicates: must be 0 before a campaign.
        "errors_total": sum(replicate["errors"] for replicate in replicates),
        "stage_counts": {
            "with_survivors": sum(1 for replicate in replicates if replicate["survivors"]),
            "with_carried": sum(1 for replicate in replicates if replicate["carried"]),
            "reached_confirmation": sum(1 for replicate in replicates if replicate["frozen"]),
        },
        "seed_t": {
            "n": int(t_values.size),
            "mean": float(t_values.mean()) if t_values.size else float("nan"),
            "sd": float(t_values.std(ddof=1)) if t_values.size > 1 else float("nan"),
        },
    }


_WORKER: dict = {}


def _init_worker(
    protocol: CampaignProtocol,
    base: CubeView,
    calendar: Sequence[date],
    cohort_sha256: str,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> None:
    _WORKER.update(
        protocol=protocol,
        base=base,
        calendar=calendar,
        cohort_sha256=cohort_sha256,
        book=ScoreBook(adjusted, trading_days, calendar, symbols),
    )


def _worker_replicate(rep: int) -> tuple[int, dict]:
    w = _WORKER
    return rep, null_replicate(w["protocol"], w["base"], w["calendar"], w["cohort_sha256"], w["book"], rep)


def run_null_check(
    protocol: CampaignProtocol,
    discovery: CubeView,
    calendar: Sequence[date],
    cohort_sha256: str,
    *,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
) -> dict:
    spec = protocol.null_check
    if spec is None:
        raise ValueError("this protocol has no null_check (campaign v1); check C needs protocol v2")
    base = demeaned(discovery)
    results: dict[int, dict] = {}

    def done(rep: int, detail: dict) -> None:
        results[rep] = detail
        if progress is not None:
            progress(f"null replicate {len(results)}/{spec.replicates}: {detail['status']}")

    if workers > 1:
        pool = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(
                protocol,
                base,
                tuple(calendar),
                cohort_sha256,
                dict(adjusted),
                tuple(trading_days),
                tuple(symbols),
            ),
        )
        try:
            for future in as_completed([pool.submit(_worker_replicate, rep) for rep in range(spec.replicates)]):
                done(*future.result())
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)  # stop promptly instead of finishing every replicate
            raise
        pool.shutdown()
    else:
        book = ScoreBook(adjusted, trading_days, calendar, symbols)
        for rep in range(spec.replicates):
            done(rep, null_replicate(protocol, base, calendar, cohort_sha256, book, rep))
    ordered = [results[rep] for rep in sorted(results)]
    return {**summarize_null(ordered, spec), "replicates_detail": ordered}


async def execute_null_check(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    power_result: Mapping,
    environment: dict,
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
) -> dict:
    protocol = loaded.protocol
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "null_check",
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "power_cube_sha256": power_result.get("cube_sha256"),
            "reads": "discovery window cells only (per-name demeaned); real bars for scores",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {"cohort_sha256": cohort.sha256, "campaign_protocol_sha256": loaded.sha256, "authorizes_promotion": False}
    try:
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        check_power_gate(power_result, cohort_sha256=cohort.sha256, campaign_protocol_sha256=loaded.sha256)
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        check_power_gate(
            power_result,
            cohort_sha256=cohort.sha256,
            campaign_protocol_sha256=loaded.sha256,
            cube_sha256=built.cube.sha256,
        )
        outcome = await asyncio.to_thread(
            run_null_check,
            protocol,
            built.cube.window(*protocol.windows.discovery),
            built.cube.sessions,
            cohort.sha256,
            adjusted=built.adjusted,
            trading_days=built.trading_days,
            symbols=built.cube.symbols,
            progress=progress,
            workers=workers,
        )
        result = {**outcome, **base, "cube_sha256": built.cube.sha256}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", **base}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
