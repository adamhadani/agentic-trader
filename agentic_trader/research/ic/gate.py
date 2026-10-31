"""The ``composite-ic`` development gate (research only).

**Pass:** on the decisive cohort, target and window, mean IC above zero with bootstrap ``t`` at or above the
protocol's minimum, and IC above zero in enough of the equal consecutive blocks of the evaluated sessions.

The gate refuses unless A-IC and C-IC passed for the same composite, sector map, cube and clean code revision.
The secondary cohort and the other horizons are reported and never decide. A failure closes the composite; a
pass keeps it alive as a hypothesis on a read window and a survivor cohort, and authorizes nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from agentic_trader.research.ic.arrays import build_ic_arrays
from agentic_trader.research.ic.checks import (
    Progress,
    _say,
    gate_read,
    mapped_floor_reason,
    mapped_fraction,
    require_checks,
    require_pinned_sectors,
    write_manifest,
)
from agentic_trader.research.ic.composite import composite_panel
from agentic_trader.research.ic.protocol import CompositeProtocol, LoadedComposite
from agentic_trader.research.ic.sectors import LoadedSectors
from agentic_trader.research.ic.statistic import render_tear_sheet, tear_sheet
from agentic_trader.research.pooled.campaign_run import require_clean_revision
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.research.short_volume import write_private
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["GATE_KIND", "execute_gate"]

GATE_KIND = "ic_gate"

Builder = Callable[[], Awaitable[tuple]]


def _read_cohort(protocol: CompositeProtocol, built, spy, sectors: LoadedSectors) -> dict:
    """The composite's gate rule and tear sheet on one cohort."""
    groups = sectors.groups(built.cube.symbols)
    arrays = build_ic_arrays(built, spy, groups, protocol.window, horizons=protocol.reported_horizons)
    scores = composite_panel(protocol, arrays)
    return {
        "cube_sha256": built.cube.sha256,
        "mapped_fraction": mapped_fraction(groups),
        "n_sessions": len(arrays.sessions),
        "n_symbols": len(arrays.symbols),
        "gate": gate_read(scores, arrays.residuals[protocol.horizon], arrays.eligible, protocol),
        "tear_sheet": tear_sheet(
            scores,
            arrays,
            horizons=protocol.reported_horizons,
            draws=protocol.bootstrap.draws,
            seed=protocol.bootstrap.seed,
            min_names=protocol.min_names,
        ),
    }


async def _read_secondary(
    protocol: CompositeProtocol, build_secondary: Builder, sectors: LoadedSectors, progress: Progress
) -> dict:
    """The secondary cohort's read. It never decides, so a failure here is reported and changes nothing."""
    _say(progress, "reading the secondary cohort (reported only)")
    try:
        built, spy = await build_secondary()
        read = await asyncio.to_thread(_read_cohort, protocol, built, spy, sectors)
    except Exception as exc:
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "completed", **read}


async def _gate_outcome(
    loaded: LoadedComposite,
    *,
    checks_dir: Path,
    build: Builder,
    build_secondary: Builder,
    sectors: LoadedSectors,
    environment: dict,
    progress: Progress,
) -> dict:
    protocol = loaded.protocol
    try:
        revision = require_clean_revision(environment)
        require_pinned_sectors(protocol, sectors)
        checks = require_checks(
            checks_dir, composite_sha256=loaded.sha256, sectors_sha256=sectors.sha256, revision=revision
        )
    except ValueError as exc:
        return {"status": "refused", "reason": str(exc)}
    built, spy = await build()
    cube_sha256 = built.cube.sha256
    if cube_sha256 != checks["cube_sha256"]:
        return {"status": "refused", "reason": "the cached cube is not the cube the checks ran on"}
    floor = mapped_floor_reason(protocol, sectors.groups(built.cube.symbols))
    if floor is not None:
        return {"status": "refused", "reason": floor, "cube_sha256": cube_sha256}
    _say(progress, "reading the decisive cohort")
    decisive = await asyncio.to_thread(_read_cohort, protocol, built, spy, sectors)
    return {
        "status": "completed",
        "decision": "pass" if decisive["gate"]["passes"] else "fail",
        **decisive,
        "secondary": await _read_secondary(protocol, build_secondary, sectors, progress),
    }


def _render(result: dict) -> str:
    gate = result["gate"]
    lines = [
        f"composite-ic gate: {result['decision']} (a hypothesis on a read window and a survivor cohort at most)",
        f"t {gate['t']:.2f} mean IC {gate['mean']:.4f} positive blocks {gate['positive_blocks']}",
        "",
        "decisive cohort",
        render_tear_sheet(result["tear_sheet"]),
    ]
    secondary = result["secondary"]
    if secondary["status"] == "completed":
        lines += ["secondary cohort (reported, never decides)", render_tear_sheet(secondary["tear_sheet"])]
    else:
        lines += [f"secondary cohort (reported, never decides): {secondary['reason']}", ""]
    return "\n".join(lines)


async def execute_gate(
    loaded: LoadedComposite,
    output: Path,
    *,
    checks_dir: Path,
    build: Builder,
    build_secondary: Builder,
    sectors: LoadedSectors,
    environment: dict,
    progress: Progress = None,
) -> dict:
    """Every exit after the manifest writes ``result.json``, with no timestamp. A completed read adds its tear sheet."""
    write_manifest(
        GATE_KIND,
        loaded,
        sectors,
        environment,
        output,
        reads="the real composite on the decisive and secondary cohorts, through the window's end",
        checks_directory=str(checks_dir),
    )
    base = {"composite_sha256": loaded.sha256, "sectors_sha256": sectors.sha256, "authorizes_promotion": False}
    try:
        outcome = await _gate_outcome(
            loaded,
            checks_dir=checks_dir,
            build=build,
            build_secondary=build_secondary,
            sectors=sectors,
            environment=environment,
            progress=progress,
        )
    except Exception as exc:
        outcome = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    result = {**outcome, **base}
    save_json_report(_finite_json(result), output / "result.json")
    if result["status"] == "completed":
        write_private(output / "tear_sheet.txt", _render(result).encode())
    return result
