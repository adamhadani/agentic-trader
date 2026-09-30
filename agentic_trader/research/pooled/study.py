# agentic_trader/research/pooled/study.py
"""Executor for a pooled literature entry (research only; grants no credit).

Writes ``protocol.json`` and ``manifest.json`` before calling ``build`` (the only provider
access) and records any failure as ``status: failed`` instead of raising. Refuses to test
unless power check A passed for the same cohort, cube and campaign protocol.
"""

from __future__ import annotations

import asyncio
import gzip
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import HIT_CODES, check_coverage
from agentic_trader.research.pooled.entry import LoadedEntry, evaluate_entry
from agentic_trader.research.pooled.formula import allowed_mask, evaluate_panel, select_picks
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.stats import (
    bootstrap_draws,
    calendar_time_newey_west,
    design_effect,
    leg_mean_test,
    paired_edge_test,
    session_table,
    two_way_clustered,
)
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["check_power_gate", "execute_pooled_study"]

_HIT_NAMES = {code: str(hit) for hit, code in HIT_CODES.items()}


def check_power_gate(
    power_result: Mapping, *, cohort_sha256: str, campaign_protocol_sha256: str, cube_sha256: str | None = None
) -> None:
    if power_result.get("status") != "passed":
        raise ValueError(f"power check A has not passed (status {power_result.get('status')!r})")
    if power_result.get("cohort_sha256") != cohort_sha256:
        raise ValueError("power check A was run on a different cohort")
    if power_result.get("campaign_protocol_sha256") != campaign_protocol_sha256:
        raise ValueError("power check A was run under a different campaign protocol")
    if cube_sha256 is not None and power_result.get("cube_sha256") != cube_sha256:
        raise ValueError("power check A was run on a different cube")


def _save_frame(frame: pd.DataFrame, path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as target:
        target.write(frame.to_csv(index=False).encode())


def _mean_n(values: np.ndarray) -> dict:
    finite = values[np.isfinite(values)]
    return {"n": int(finite.size), "mean": float(finite.mean()) if finite.size else float("nan")}


def _diagnostics(table, rows: pd.DataFrame, view, cohort: LoadedCohort) -> dict:
    years = np.array([d.year for d in table.sessions])
    by_year = {int(y): _mean_n(table.edge[years == y]) for y in sorted(set(years.tolist()))}
    terciles = {}
    if len(rows) >= 3:
        volume = view.dollar_volume[rows["session_idx"].to_numpy(), rows["symbol_idx"].to_numpy()]
        # pick_rows has a RangeIndex, so the tercile Series aligns with it by position.
        buckets = pd.qcut(pd.Series(volume).rank(method="first"), 3, labels=["low", "mid", "high"])
        terciles = {str(k): _mean_n(g.to_numpy(float)) for k, g in rows["residual"].groupby(buckets, observed=True)}
    sources = pd.Series(
        ["+".join(cohort.cohort.source_of(view.symbols[j])) for j in rows["symbol_idx"]], dtype=object
    ).value_counts()
    per_session = table.pick_n[table.pick_n > 0]
    return {
        "edge_by_year": by_year,
        "residual_by_liquidity_tercile": terciles,
        "hits": {_HIT_NAMES[int(k)]: int(v) for k, v in rows["hit"].value_counts().items()},
        "picks_per_session": {"mean": float(per_session.mean()) if per_session.size else 0.0},
        "picks_by_source": {str(k): int(v) for k, v in sources.items()},
        "mean_r_gross": float(rows["r_gross"].mean()) if len(rows) else float("nan"),
    }


async def execute_pooled_study(
    loaded: LoadedEntry,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    power_result: Mapping,
    environment: dict,
) -> dict:
    entry = loaded.entry
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**entry.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "entry_id": entry.id,
            "version": entry.version,
            "sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "campaign_protocol_sha256": entry.campaign_protocol_sha256,
            "cube_spec_identity": entry.cube_spec().identity,
            "power_cube_sha256": power_result.get("cube_sha256"),
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        if cohort.sha256 != entry.cohort_sha256:
            raise ValueError("cohort file does not match the entry's cohort_sha256")
        check_power_gate(
            power_result, cohort_sha256=entry.cohort_sha256, campaign_protocol_sha256=entry.campaign_protocol_sha256
        )
        built = await build()
        check_coverage(built.cube, entry.coverage)
        check_power_gate(
            power_result,
            cohort_sha256=entry.cohort_sha256,
            campaign_protocol_sha256=entry.campaign_protocol_sha256,
            cube_sha256=built.cube.sha256,
        )
        view = built.cube.window(*entry.window.decisions)
        formula = entry.formula
        scores = await asyncio.to_thread(
            evaluate_panel, formula.score, built.adjusted, built.trading_days, view.sessions, view.symbols
        )
        filters = [
            await asyncio.to_thread(
                evaluate_panel, f.expression, built.adjusted, built.trading_days, view.sessions, view.symbols
            )
            for f in formula.filters
        ]
        allowed = allowed_mask(formula, filters, view.eligible)
        picks = select_picks(scores, allowed, view, formula.k)
        table = session_table(picks, view, purge=False)
        draws = bootstrap_draws(
            len(view.sessions), entry.bootstrap.block_mean, entry.bootstrap.draws, entry.bootstrap.seed
        )
        verdict = evaluate_entry(table, entry, draws)
        # The same picks and draws at 0 bps, reported alongside; the decisive cost alone decides.
        gross = session_table(picks, view, purge=False, column="r_gross")
        rows = table.pick_rows
        picks_frame = rows.assign(
            session=[view.sessions[i].isoformat() for i in rows["session_idx"]],
            symbol=[view.symbols[j] for j in rows["symbol_idx"]],
        )
        await asyncio.to_thread(_save_frame, picks_frame, directory / "picks.csv.gz")
        result = {
            "status": "completed",
            "decision": "eligible_for_probe" if verdict["passes"] else "failed",
            "pass_rule": verdict,
            "gross": {"paired_edge": paired_edge_test(gross, draws), "leg_mean": leg_mean_test(gross, draws)},
            "cross_checks": {
                "two_way_clustered": two_way_clustered(rows),
                # Each pick's residual is spread across its hold, so calendar-time serial
                # dependence reaches max_hold - 1 sessions (a hold property, not the bootstrap's).
                "calendar_time_newey_west": calendar_time_newey_west(
                    rows, len(view.sessions), lag=entry.bracket.max_hold_sessions - 1
                ),
                "design_effect": design_effect(rows),
            },
            "diagnostics": _diagnostics(table, rows, view, cohort),
            "picks": {
                "kept": len(rows),
                "dropped_unlabelled": table.dropped_unlabelled,
                "dropped_purged": table.dropped_purged,
            },
            "cube": {"sha256": built.cube.sha256, "coverage": built.cube.coverage},
            # As stored in the cube's coverage by the run that built it (the power check).
            "bar_failures": dict(built.bar_failures),
            "static_used": list(built.static_used),
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
