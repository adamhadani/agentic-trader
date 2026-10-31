"""The zero-trial re-score of the tested formula-cohort pairs (research only).

The formulas and windows were already spent, so this charges nothing and journals nothing: no database.

**Rehearsal, before any IC.** Each pair's discovery ``edge_mean`` and ``n_sessions`` are recomputed through the
campaign's own scoring path and compared with its ``formulas.jsonl``: within 1e-9 and exactly. A batch with any
mismatch is ``not_reproduced``, records its mismatches, gets no IC read and no verdict, and the others continue.

**The IC read.** Per pair, the rank IC against the horizon's sector-neutral residual return, over discovery and
selection separately, with the expression as written. Bars are cut at the selection end before anything reads
them, so nothing reads the reserve. A discovery decision's forward return can reach into selection, which is
expected: both are development dates. An hourly batch reads through 10:00 on ``D``, after ``D``'s open, so its
return starts at the next open (``entry_delay``); that blunts the intraday batches by one session.

**The batch-mean rule.** A batch carries only if the mean IC over its formulas is above zero on discovery and on
selection. Carrying is not a pass: it only makes a member eligible for a later spec, and nothing here authorizes
anything.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.ic.arrays import build_ic_arrays, truncate_bars
from agentic_trader.research.ic.checks import Progress, _say, failed_outcome, mapped_fraction
from agentic_trader.research.ic.protocol import Bootstrap
from agentic_trader.research.ic.sectors import LoadedSectors
from agentic_trader.research.ic.statistic import ic_inference, rank_ic_series, tear_sheet
from agentic_trader.research.pooled.campaign import CampaignProtocol, DiscoveryEvaluator, load_campaign_protocol
from agentic_trader.research.pooled.campaign_run import require_clean_revision
from agentic_trader.research.pooled.entry import REPO_ROOT
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook, formula_id, score_book
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.research.short_volume import write_private
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "EDGE_TOLERANCE",
    "RESCORE_KIND",
    "LoadedRescore",
    "RescoreBatch",
    "RescoreProtocol",
    "batch_verdict",
    "execute_rescore",
    "load_rescore",
    "read_pairs",
    "rehearse_batch",
]

RESCORE_KIND = "ic_rescore"
EDGE_TOLERANCE = 1e-9
REPRODUCED = "reproduced"
NOT_REPRODUCED = "not_reproduced"
FAILED = "failed"
WINDOWS = ("discovery", "selection")
_ENTRY_DELAY = {"daily": 0, "hourly": 1}

Builder = Callable[[], Awaitable[tuple[CubeBuild, pd.DataFrame]]]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BatchInputs(_Frozen):
    """A protocol's extra field sources, relative to the research root."""

    earnings_pages: str | None = None
    corporate_actions: str | None = None
    short_volume: str | None = None


class RescoreBatch(_Frozen):
    id: str
    protocol: str  # relative to the repository
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_dir: str  # relative to the research root
    formulas_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: Literal["daily", "hourly"]
    entry_delay: int
    inputs: BatchInputs = BatchInputs()

    @model_validator(mode="after")
    def _delay_follows_score(self) -> RescoreBatch:
        if self.entry_delay != _ENTRY_DELAY[self.score]:
            raise ValueError(f"a {self.score} batch has entry_delay {_ENTRY_DELAY[self.score]}, not {self.entry_delay}")
        return self


class EdgeStatisticReference(_Frozen):
    """The 64 edge t-statistics as already reported, for the IC t-statistics to be read beside."""

    t_mean: float
    t_sd: float


class RescoreProtocol(_Frozen):
    id: Literal["ic-rescore"]
    version: int
    rationale: str
    sectors_path: str  # relative to the repository
    sectors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    horizon: int = Field(ge=1)
    min_names: int = Field(ge=1)
    bootstrap: Bootstrap
    edge_statistic_reference: EdgeStatisticReference
    known_families: tuple[str, ...]
    batches: tuple[RescoreBatch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> RescoreProtocol:
        ids = [batch.id for batch in self.batches]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate batch id")
        return self


@dataclass(frozen=True)
class LoadedRescore:
    protocol: RescoreProtocol
    sha256: str
    path: Path


def load_rescore(path: Path) -> LoadedRescore:
    raw = path.read_bytes()
    return LoadedRescore(
        protocol=RescoreProtocol.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path
    )


@dataclass(frozen=True)
class _PinnedBatch:
    batch: RescoreBatch
    protocol: CampaignProtocol
    recorded: tuple[dict, ...]


def _recorded_id(row: Mapping) -> str | None:
    """The id a recorded expression compiles to; None for one that is missing or no longer compiles."""
    try:
        return formula_id(canonical_expression(row["expression"]))
    except KeyError, SyntaxError, ValueError:  # AlphaDSLSyntaxError is a ValueError
        return None


def _check_recorded(batch: RescoreBatch, protocol: CampaignProtocol, recorded: Sequence[dict]) -> None:
    if len(recorded) != protocol.formula_budget:
        raise ValueError(
            f"{batch.id}: {len(recorded)} formulas recorded, the protocol's formula budget is {protocol.formula_budget}"
        )
    for row in recorded:
        if row.get("status") != "evaluated":
            raise ValueError(f"{batch.id}: a recorded formula was not evaluated, so it has no edge to reproduce")
        expected = _recorded_id(row)
        if expected is None or row.get("formula_id") != expected:
            raise ValueError(f"{batch.id}: a recorded formula_id is not its expression's")


def _pin_batch(batch: RescoreBatch, research_root: Path) -> _PinnedBatch:
    """The batch's protocol and formula list, each matching its digest. Every refusal's text is fixed."""
    try:
        loaded = load_campaign_protocol(REPO_ROOT / batch.protocol)
        raw = (research_root / batch.campaign_dir / "formulas.jsonl").read_bytes()
    except (OSError, ValueError) as exc:  # Their text can hold a path or a value; the type name is enough.
        raise ValueError(f"{batch.id}: a pinned file is unreadable ({type(exc).__name__})") from exc
    if loaded.sha256 != batch.protocol_sha256:
        raise ValueError(f"{batch.id}: the protocol is {loaded.sha256}, not the pinned {batch.protocol_sha256}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != batch.formulas_sha256:
        raise ValueError(f"{batch.id}: formulas.jsonl digest {digest} is not the pinned {batch.formulas_sha256}")
    if loaded.protocol.score_inputs != batch.score:
        raise ValueError(
            f"{batch.id}: the protocol scores {loaded.protocol.score_inputs}, the batch says {batch.score}"
        )
    try:
        recorded = tuple(json.loads(line) for line in raw.decode().splitlines() if line.strip())
    except ValueError as exc:
        raise ValueError(f"{batch.id}: formulas.jsonl is unreadable ({type(exc).__name__})") from exc
    _check_recorded(batch, loaded.protocol, recorded)
    return _PinnedBatch(batch=batch, protocol=loaded.protocol, recorded=recorded)


def _finite(value: float | None) -> bool:
    """A saved document holds a non-finite number as None."""
    return value is not None and math.isfinite(value)


def _edge_matches(recomputed: float, recorded: float | None) -> bool:
    """Within the tolerance; an edge with no finite value matches only another with none."""
    if recorded is None or not _finite(recorded) or not _finite(recomputed):
        return not _finite(recorded) and not _finite(recomputed)
    return abs(recomputed - recorded) <= EDGE_TOLERANCE


def rehearse_batch(
    batch: RescoreBatch, built: CubeBuild, book: ScoreBook, recorded: Sequence[dict], protocol: CampaignProtocol
) -> dict:
    """Each recorded discovery edge recomputed through the campaign's own path; no IC is read here."""
    evaluator = DiscoveryEvaluator(built.cube.window(*protocol.windows.discovery), protocol)
    mismatches: list[dict] = []
    edges: dict[str, dict] = {}
    for row in recorded:
        identity = {"formula_id": row["formula_id"], "family": row.get("family")}
        try:
            edge = evaluator.score(book.formula(row["expression"])).row["edge"]
        except Exception as exc:  # A formula the code can no longer score has drifted; recorded, never retried.
            mismatches.append({**identity, "field": "error", "error": type(exc).__name__})
            continue
        mean, n_sessions = float(edge["mean"]), int(edge["n_sessions"])
        if not _edge_matches(mean, row["edge_mean"]):
            mismatches.append({**identity, "field": "edge_mean", "recomputed": mean, "recorded": row["edge_mean"]})
        if n_sessions != row["n_sessions"]:
            mismatches.append(
                {**identity, "field": "n_sessions", "recomputed": n_sessions, "recorded": row["n_sessions"]}
            )
        edges[row["formula_id"]] = {
            "edge_mean": mean,
            "recorded_edge_mean": row["edge_mean"],
            "n_sessions": n_sessions,
            "recorded_n_sessions": row["n_sessions"],
        }
    return {
        "batch": batch.id,
        "status": NOT_REPRODUCED if mismatches else REPRODUCED,
        "mismatches": mismatches,
        "edges": edges,
    }


def _mean_or_nan(values: Sequence[float]) -> float:
    return float(np.mean(values)) if values else float("nan")


def batch_verdict(rows: Sequence[dict]) -> dict:
    """The batch-mean rule. A formula with no evaluated session on a window is left out of that window's mean;
    ``n_without_sessions`` counts the formulas missing from at least one of the two."""
    means = {window: [row[window]["mean"] for row in rows if _finite(row[window]["mean"])] for window in WINDOWS}
    discovery, selection = _mean_or_nan(means["discovery"]), _mean_or_nan(means["selection"])
    return {
        "discovery_mean": discovery,
        "selection_mean": selection,
        "n_formulas": len(rows),
        "n_without_sessions": sum(1 for row in rows if not all(_finite(row[window]["mean"]) for window in WINDOWS)),
        "carries": bool(discovery > 0 and selection > 0),  # False for a NaN mean
    }


def read_pairs(
    rescore: RescoreProtocol,
    protocol: CampaignProtocol,
    built: CubeBuild,
    spy: pd.DataFrame,
    book: ScoreBook,
    recorded: Sequence[dict],
    groups: np.ndarray,
    *,
    entry_delay: int,
) -> tuple[list[dict], list[dict]]:
    """One row per pair with both windows' inference, and one tear sheet per pair over the batch's whole read."""
    discovery, selection = protocol.windows.discovery, protocol.windows.selection
    horizon = rescore.horizon
    arrays = build_ic_arrays(
        built, spy, groups, (discovery[0], selection[1]), horizons=(horizon,), entry_delay=entry_delay
    )
    target = arrays.residuals[horizon]
    stop = arrays.offset + len(arrays.sessions)
    masks = {
        name: np.array([low <= day <= high for day in arrays.sessions])
        for name, (low, high) in zip(WINDOWS, (discovery, selection), strict=True)
    }
    draws, seed = rescore.bootstrap.draws, rescore.bootstrap.seed
    rows: list[dict] = []
    sheets: list[dict] = []
    for row in recorded:
        scores = book.panel(row["expression"])[arrays.offset : stop]
        ic = rank_ic_series(scores, target, arrays.eligible, min_names=rescore.min_names)
        identity = {"formula_id": row["formula_id"], "family": row["family"], "expression": row["expression"]}
        rows.append(
            {
                **identity,
                **{name: ic_inference(ic[mask], horizon, draws=draws, seed=seed) for name, mask in masks.items()},
            }
        )
        sheets.append(
            {
                **identity,
                "tear_sheet": tear_sheet(
                    scores, arrays, horizons=(horizon,), draws=draws, seed=seed, min_names=rescore.min_names
                ),
            }
        )
    return rows, sheets


def _read_batch(
    pinned: _PinnedBatch,
    built: CubeBuild,
    spy: pd.DataFrame,
    sectors: LoadedSectors,
    rescore: RescoreProtocol,
    progress: Progress,
) -> tuple[dict, list[dict], list[dict]]:
    """The batch's record, pair rows and tear sheets. Bars are cut first; the rehearsal comes before any IC."""
    batch, protocol, recorded = pinned.batch, pinned.protocol, pinned.recorded
    window_end = protocol.windows.selection[1]
    truncated = dataclasses.replace(
        built, adjusted=truncate_bars(built.adjusted, window_end), hourly=truncate_bars(built.hourly, window_end)
    )
    book = score_book(protocol, truncated)
    _say(progress, f"{batch.id}: rehearsing {len(recorded)} recorded edges")
    rehearsal = rehearse_batch(batch, truncated, book, recorded, protocol)
    record = {"status": rehearsal["status"], "mismatches": rehearsal["mismatches"], "cube_sha256": built.cube.sha256}
    if rehearsal["status"] != REPRODUCED:
        _say(progress, f"{batch.id}: not reproduced ({len(rehearsal['mismatches'])} mismatches), no IC read")
        return record, [], []
    _say(progress, f"{batch.id}: reproduced, reading rank IC")
    groups = sectors.groups(truncated.cube.symbols)
    rows, sheets = read_pairs(rescore, protocol, truncated, spy, book, recorded, groups, entry_delay=batch.entry_delay)
    labelled = [{"batch": batch.id, **row, "rehearsal": rehearsal["edges"][row["formula_id"]]} for row in rows]
    record.update(mapped_fraction=mapped_fraction(groups), verdict=batch_verdict(labelled))
    return record, labelled, [{"batch": batch.id, **sheet} for sheet in sheets]


async def _run_batch(
    pinned: _PinnedBatch, build: Builder, sectors: LoadedSectors, rescore: RescoreProtocol, progress: Progress
) -> tuple[dict, list[dict], list[dict]]:
    """A batch whose inputs fail stays out by its error's type name; the others continue."""
    batch = pinned.batch
    identity = {
        "id": batch.id,
        "protocol_sha256": batch.protocol_sha256,
        "formulas_sha256": batch.formulas_sha256,
        "score": batch.score,
        "entry_delay": batch.entry_delay,
        "n_pairs": len(pinned.recorded),
    }
    try:
        _say(progress, f"{batch.id}: building")
        built, spy = await build()
        record, rows, sheets = await asyncio.to_thread(_read_batch, pinned, built, spy, sectors, rescore, progress)
    except Exception as exc:
        return {**identity, **failed_outcome(exc, f"batch {batch.id}", progress)}, [], []
    return {**identity, **record}, rows, sheets


def _ids(records: Sequence[dict], status: str) -> list[str]:
    return [record["id"] for record in records if record["status"] == status]


def _summary(rescore: RescoreProtocol, records: Sequence[dict], rows: Sequence[dict]) -> dict:
    """The IC t-statistics over the covered pairs, beside the reported edge statistic, and where known names landed."""
    carries = {record["id"]: record["verdict"]["carries"] for record in records if "verdict" in record}
    windows = {}
    for window in WINDOWS:
        ts = [row[window]["t"] for row in rows if _finite(row[window]["t"])]
        windows[window] = {
            "n_t": len(ts),
            "t_mean": _mean_or_nan(ts),
            "t_sd": float(np.std(ts, ddof=1)) if len(ts) > 1 else float("nan"),
        }
    return {
        "n_pairs": sum(record["n_pairs"] for record in records),
        "n_covered": len(rows),
        **windows,
        "edge_statistic_reference": rescore.edge_statistic_reference.model_dump(),
        "known_families": [
            {
                "batch": row["batch"],
                "family": row["family"],
                "expression": row["expression"],
                "batch_carries": carries[row["batch"]],
                **{f"{window}_{key}": row[window][key] for window in WINDOWS for key in ("mean", "t")},
            }
            for row in rows
            if row["family"] in rescore.known_families
        ],
    }


def _jsonl(rows: Sequence[dict]) -> bytes:
    return "".join(json.dumps(_finite_json(row), sort_keys=True, allow_nan=False) + "\n" for row in rows).encode()


def _write_manifest(loaded: LoadedRescore, sectors: LoadedSectors, environment: dict, output: Path, **details) -> None:
    """A new private directory and its manifest, before anything is computed."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report(
        {
            "check": RESCORE_KIND,
            "rescore_sha256": loaded.sha256,
            "sectors_sha256": sectors.sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
            **details,
        },
        output / "manifest.json",
    )


async def _rescore_outcome(
    loaded: LoadedRescore,
    output: Path,
    *,
    research_root: Path,
    builders: Mapping[str, Builder],
    sectors: LoadedSectors,
    environment: dict,
    progress: Progress,
) -> dict:
    rescore = loaded.protocol
    try:
        require_clean_revision(environment)
        if sectors.sha256 != rescore.sectors_sha256:
            raise ValueError(f"the sector map is {sectors.sha256}, not the pinned {rescore.sectors_sha256}")
        missing = [batch.id for batch in rescore.batches if batch.id not in builders]
        if missing:
            raise ValueError(f"no builder for batches {missing}")
        pins = [await asyncio.to_thread(_pin_batch, batch, research_root) for batch in rescore.batches]
    except ValueError as exc:  # Only these deliberate refusals: their text is fixed, a build's error is not.
        return {"status": "refused", "reason": str(exc)}
    records: list[dict] = []
    rows: list[dict] = []
    sheets: list[dict] = []
    for pinned in pins:
        record, batch_rows, batch_sheets = await _run_batch(
            pinned, builders[pinned.batch.id], sectors, rescore, progress
        )
        records.append(record)
        rows.extend(batch_rows)
        sheets.extend(batch_sheets)
        # A score book's closures hold its bars in a reference cycle: free them before the next, large, build.
        await asyncio.to_thread(gc.collect)
    write_private(output / "pairs.jsonl", _jsonl(rows))
    write_private(output / "tear_sheets.jsonl", _jsonl(sheets))
    save_json_report(_finite_json({"batches": records}), output / "batches.json")
    not_reproduced, failed = _ids(records, NOT_REPRODUCED), _ids(records, FAILED)
    outcome = {
        "status": "partial" if not_reproduced or failed else "completed",
        "batches_reproduced": _ids(records, REPRODUCED),
        "batches_not_reproduced": not_reproduced,
        "batches_failed": failed,
        "batches_carrying": [record["id"] for record in records if record.get("verdict", {}).get("carries")],
        "summary": _summary(rescore, records, rows),
    }
    if outcome["status"] == "partial":
        outcome["reason"] = f"not reproduced: {not_reproduced}; failed: {failed}"
    return outcome


async def execute_rescore(
    loaded: LoadedRescore,
    output: Path,
    *,
    research_root: Path,
    builders: Mapping[str, Builder],
    sectors: LoadedSectors,
    environment: dict,
    progress: Progress = None,
) -> dict:
    """Every exit after the manifest writes ``result.json``, with no timestamp and errors by type name only."""
    _write_manifest(
        loaded,
        sectors,
        environment,
        output,
        reads="every pinned pair's discovery edge, then its rank IC on discovery and selection; never the reserve",
        research_root=str(research_root),
    )
    base = {"rescore_sha256": loaded.sha256, "sectors_sha256": sectors.sha256, "authorizes_promotion": False}
    try:
        outcome = await _rescore_outcome(
            loaded,
            output,
            research_root=research_root,
            builders=builders,
            sectors=sectors,
            environment=environment,
            progress=progress,
        )
    except Exception as exc:
        outcome = failed_outcome(exc, "the re-score", progress)
    result = _finite_json({**outcome, **base})
    save_json_report(result, output / "result.json")
    return result
