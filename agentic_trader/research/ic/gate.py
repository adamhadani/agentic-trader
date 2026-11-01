"""The ``composite-ic`` development gate (research only).

**Pass:** on the decisive cohort, target and window, mean IC above zero with bootstrap ``t`` at or above the
protocol's minimum, and IC above zero in enough of the equal consecutive blocks of the evaluated sessions.

The gate refuses unless A-IC and C-IC passed for the same composite, sector map, cube and clean code revision.
The secondary cohort and the other horizons are reported and never decide. A failure closes the composite; a
pass keeps it alive as a hypothesis on a read window and a survivor cohort, and authorizes nothing.

**The decomposition,** on both cohorts, is reported and never decides. A rank statistic carries name-linked biases
through the low-risk theme that the checks cannot see, so each read says which theme drove it. Each theme and the
composite excluding low risk go through the same combine, with the minimum share applied to their own members. The
reading of a pass that low risk carried is fixed here, before the read: it is a reading, not a decision rule.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np

from agentic_trader.research.ic.arrays import ICArrays, build_ic_arrays
from agentic_trader.research.ic.checks import (
    Progress,
    _say,
    failed_outcome,
    gate_read,
    mapped_floor_reason,
    mapped_fraction,
    require_checks,
    require_pinned_sectors,
    write_manifest,
)
from agentic_trader.research.ic.composite import composite_panel, ex_low_risk_panel, member_panels, theme_panel
from agentic_trader.research.ic.protocol import CompositeProtocol, LoadedComposite
from agentic_trader.research.ic.sectors import LoadedSectors
from agentic_trader.research.ic.statistic import (
    ic_inference,
    pearson_ic_series,
    rank_ic_series,
    render_tear_sheet,
    tear_sheet,
)
from agentic_trader.research.pooled.campaign_run import require_clean_revision
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.research.short_volume import write_private
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["CARRIED_BY_LOW_RISK", "GATE_KIND", "LAG_SESSIONS", "declared_reading", "execute_gate", "lagged_panel"]

GATE_KIND = "ic_gate"
LAG_SESSIONS = 252
CARRIED_BY_LOW_RISK = "carried_by_low_risk"

Builder = Callable[[], Awaitable[tuple]]


def lagged_panel(scores: np.ndarray, sessions: int = LAG_SESSIONS) -> np.ndarray:
    """The panel shifted down ``sessions`` rows: decision ``D`` reads ``D - sessions``'s score, NaN before that."""
    out = np.full(scores.shape, np.nan)
    out[sessions:] = scores[:-sessions]
    return out


def _decomposition(protocol: CompositeProtocol, arrays: ICArrays, scores: np.ndarray) -> dict:
    """Which part of the composite a read came from. Nothing in the gate's rule or the decision reads this."""
    target, mask = arrays.residuals[protocol.horizon], arrays.eligible

    def rank(panel: np.ndarray) -> dict:
        ic = rank_ic_series(panel, target, mask, min_names=protocol.min_names)
        return ic_inference(ic, protocol.horizon, draws=protocol.bootstrap.draws, seed=protocol.bootstrap.seed)

    def pearson_mean(panel: np.ndarray) -> float:
        ic = pearson_ic_series(panel, target, mask, min_names=protocol.min_names)
        kept = ic[np.isfinite(ic)]
        return float(kept.mean()) if kept.size else float("nan")

    def both(panel: np.ndarray) -> dict:
        return {**rank(panel), "pearson_mean": pearson_mean(panel)}

    panels = member_panels(protocol, arrays)
    return {
        "themes": {theme.theme: both(theme_panel(protocol, theme, panels)) for theme in protocol.themes},
        "ex_low_risk": both(ex_low_risk_panel(protocol, panels)),
        "composite_pearson": pearson_mean(scores),
        "lagged_252": rank(lagged_panel(scores)),
    }


def declared_reading(passes: bool, ex_low_risk_t: float, min_t: float) -> str | None:
    """A pass whose composite excluding low risk does not reach ``min_t``, a NaN ``t`` included, is carried by it."""
    return CARRIED_BY_LOW_RISK if passes and not ex_low_risk_t >= min_t else None


def _read_cohort(protocol: CompositeProtocol, built, spy, sectors: LoadedSectors) -> dict:
    """The composite's gate rule, tear sheet and decomposition on one cohort."""
    groups = sectors.groups(built.cube.symbols)
    arrays = build_ic_arrays(built, spy, groups, protocol.window, horizons=protocol.reported_horizons)
    scores = composite_panel(protocol, arrays)
    return {
        "cube_sha256": built.cube.sha256,
        "mapped_fraction": mapped_fraction(groups),
        "n_sessions": len(arrays.sessions),
        "n_symbols": len(arrays.symbols),
        "gate": gate_read(scores, arrays.residuals[protocol.horizon], arrays.eligible, protocol),
        "decomposition": _decomposition(protocol, arrays, scores),
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
        return failed_outcome(exc, "the secondary cohort (reported only)", progress)
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
        return {
            "status": "refused",
            "reason": "the cached cube is not the cube the checks ran on",
            "cube_sha256": cube_sha256,
            "checks_cube_sha256": checks["cube_sha256"],
        }
    groups = sectors.groups(built.cube.symbols)
    floor = mapped_floor_reason(protocol, groups)
    if floor is not None:
        return {
            "status": "refused",
            "reason": floor,
            "cube_sha256": cube_sha256,
            "mapped_fraction": mapped_fraction(groups),
        }
    _say(progress, "reading the decisive cohort")
    decisive = await asyncio.to_thread(_read_cohort, protocol, built, spy, sectors)
    return {
        "status": "completed",
        "decision": "pass" if decisive["gate"]["passes"] else "fail",
        "declared_reading": declared_reading(
            decisive["gate"]["passes"], decisive["decomposition"]["ex_low_risk"]["t"], protocol.gate.min_t
        ),
        **decisive,
        "secondary": await _read_secondary(protocol, build_secondary, sectors, progress),
    }


def _gate_line(gate: dict) -> str:
    power = " ".join(f"{ic}: {value:.3f}" for ic, value in gate["power_at"].items())
    return (
        f"t {gate['t']:.2f} mean IC {gate['mean']:.4f} se {gate['se']:.4f} positive blocks {gate['positive_blocks']} "
        f"min detectable IC {gate['min_detectable_ic']:.4f} power at IC {power} "
        f"median pairs/session {gate['median_pairs_per_session']:.1f}"
    )


def _rank_line(name: str, read: dict) -> str:
    line = (
        f"{name}: rank IC mean {read['mean']:.4f} se {read['se']:.4f} t {read['t']:.2f} "
        f"sessions {read['n_sessions']} unavailable {read['n_unavailable']}"
    )
    return f"{line} Pearson mean {read['pearson_mean']:.4f}" if "pearson_mean" in read else line


def _decomposition_lines(decomposition: dict) -> list[str]:
    return [
        "decomposition (reported, never decides)",
        *(_rank_line(f"theme {name}", read) for name, read in decomposition["themes"].items()),
        _rank_line("excluding low risk", decomposition["ex_low_risk"]),
        f"composite Pearson mean {decomposition['composite_pearson']:.4f}",
        _rank_line(f"composite lagged {LAG_SESSIONS} sessions", decomposition["lagged_252"]),
    ]


def _render(result: dict) -> str:
    lines = [
        f"composite-ic gate: {result['decision']} (a hypothesis on a read window and a survivor cohort at most)",
        f"declared reading: {result['declared_reading'] or 'none'}",
        "",
        "decisive cohort",
        _gate_line(result["gate"]),
        *_decomposition_lines(result["decomposition"]),
        render_tear_sheet(result["tear_sheet"]),
    ]
    secondary = result["secondary"]
    if secondary["status"] == "completed":
        lines += [
            "secondary cohort (reported, never decides)",
            _gate_line(secondary["gate"]),
            *_decomposition_lines(secondary["decomposition"]),
            render_tear_sheet(secondary["tear_sheet"]),
        ]
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
        outcome = failed_outcome(exc, "the gate", progress)
    result = {**outcome, **base}
    save_json_report(_finite_json(result), output / "result.json")
    if result["status"] == "completed":
        write_private(output / "tear_sheet.txt", _render(result).encode())
    return result
