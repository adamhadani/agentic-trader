# agentic_trader/research/pooled/campaign.py
"""The budgeted pooled campaign: frozen protocol, and (below) its pure stage logic.

The protocol is committed before any real-data run of the pooled lane; its SHA-256 is
recorded by every Part 1 manifest. Discovery, selection and confirmation are pure
functions over cube windows with an injected ledger, so power check A runs them
unchanged on synthetic cubes with an in-memory ledger.
"""

from __future__ import annotations

import ast
import calendar
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.alpha.search import MUTATION_OPERATORS
from agentic_trader.research.pooled.cohort import UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CoverageSpec, CubeSpec, CubeView, LabelCube
from agentic_trader.research.pooled.formula import Picks, require_dimensionless, select_picks
from agentic_trader.research.pooled.stats import (
    bootstrap_draws,
    leg_mean_test,
    paired_edge_test,
    session_table,
    trimmed_mean,
)
from agentic_trader.research.setups.study import holm


__all__ = [
    "CampaignProtocol",
    "CampaignWindows",
    "InMemoryLedger",
    "Ledger",
    "LoadedProtocol",
    "ScoredFormula",
    "load_campaign_protocol",
    "run_stages",
]


def _calls(expression: str) -> set[str]:
    return {
        node.func.id.lower()
        for node in ast.walk(ast.parse(expression, mode="eval"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


class StageWindows(BaseModel, frozen=True, extra="forbid"):
    discovery: tuple[date, date]
    selection: tuple[date, date]
    confirmation: tuple[date, date]

    @model_validator(mode="after")
    def _ordered(self) -> StageWindows:
        spans = (self.discovery, self.selection, self.confirmation)
        if any(start > end for start, end in spans):
            raise ValueError("each window must be ordered")
        if not (self.discovery[1] < self.selection[0] and self.selection[1] < self.confirmation[0]):
            raise ValueError("windows must be disjoint and in stage order")
        return self


class Family(BaseModel, frozen=True, extra="forbid"):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    rationale: str
    seeds: tuple[str, ...] = Field(min_length=1)
    mutation_operators: tuple[str, ...]

    @field_validator("seeds")
    @classmethod
    def _dimensionless(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for seed in value:
            require_dimensionless(seed)
        return value

    @field_validator("mutation_operators")
    @classmethod
    def _known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - set(MUTATION_OPERATORS)
        if unknown:
            raise ValueError(f"unknown mutation operators: {sorted(unknown)}")
        return value


class ExcludedFamily(BaseModel, frozen=True, extra="forbid"):
    id: str
    reason: str


class SearchSpec(BaseModel, frozen=True, extra="forbid"):
    seed: int
    archive_size: int = Field(ge=1)
    forbidden_operators: tuple[str, ...]


class CampaignBootstrap(BaseModel, frozen=True, extra="forbid"):
    block_mean: float = Field(gt=1)
    discovery_draws: int = Field(ge=100)
    selection_draws: int = Field(ge=100)
    confirmation_draws: int = Field(ge=100)
    seed: int


class DiscoveryGate(BaseModel, frozen=True, extra="forbid"):
    min_t: float = Field(gt=0)
    blocks: int = Field(ge=2)
    min_positive_blocks: int = Field(ge=1)
    min_sessions: int = Field(ge=1)
    carry: int = Field(ge=1)


class SelectionGate(BaseModel, frozen=True, extra="forbid"):
    min_fraction_of_discovery: float = Field(gt=0, le=1)
    min_sessions: int = Field(ge=1)


class ConfirmationGate(BaseModel, frozen=True, extra="forbid"):
    alpha: float = Field(gt=0, lt=1)
    trim_fraction: float = Field(ge=0, lt=0.5)
    recent_months: int = Field(ge=1)
    min_sessions: int = Field(ge=1)


class PowerSpec(BaseModel, frozen=True, extra="forbid"):
    replicates: int = Field(ge=1)
    null_formulas: int = Field(ge=1)
    ar_phi: float = Field(gt=0, lt=1)
    deltas: tuple[float, ...] = Field(min_length=1)
    detection_delta: float
    min_detection: float = Field(gt=0, le=1)
    max_false_acceptance: float = Field(ge=0, lt=1)
    seed: int


class PowerSearchSpec(BaseModel, frozen=True, extra="forbid"):
    families: tuple[str, ...] = Field(min_length=1)
    seeds: int = Field(ge=1)
    delta: float = Field(gt=0)
    min_recovered: int = Field(ge=1)


class CampaignProtocol(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-campaign"]
    version: int = Field(ge=1)
    title: str
    cohort: str
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    bars_from: date
    bars_through: date
    windows: StageWindows
    k: int = Field(ge=1)
    bracket: BracketSpec
    universe: UniverseSpec
    decision_cost_bps: float = Field(ge=0)
    coverage: CoverageSpec
    formula_budget: int = Field(ge=1)
    families: tuple[Family, ...] = Field(min_length=1)
    excluded_families: tuple[ExcludedFamily, ...]
    search: SearchSpec
    complexity_penalty_per_node: float = Field(ge=0)
    dedupe_jaccard: float = Field(gt=0, le=1)
    bootstrap: CampaignBootstrap
    discovery_gate: DiscoveryGate
    selection_gate: SelectionGate
    confirmation_gate: ConfirmationGate
    power: PowerSpec
    power_search: PowerSearchSpec

    @model_validator(mode="after")
    def _consistent(self) -> CampaignProtocol:
        forbidden = set(self.search.forbidden_operators)
        ids = [family.id for family in self.families]
        if len(ids) != len(set(ids)):
            raise ValueError("family ids must be unique")
        for family in self.families:
            if forbidden & set(family.mutation_operators):
                raise ValueError(f"family {family.id} mutates with a forbidden operator")
            for seed in family.seeds:
                if forbidden & _calls(seed):
                    raise ValueError(f"family {family.id} seed {seed!r} uses a forbidden operator")
        if self.formula_budget < len(self.families):
            raise ValueError("formula_budget must cover at least one formula per family")
        if self.discovery_gate.min_positive_blocks > self.discovery_gate.blocks:
            raise ValueError("min_positive_blocks cannot exceed blocks")
        if self.power.detection_delta not in self.power.deltas or 0.0 not in self.power.deltas:
            raise ValueError("power deltas must include 0 and the detection delta")
        if set(self.power_search.families) - set(ids):
            raise ValueError("power_search families must be declared families")
        if self.power_search.min_recovered > self.power_search.seeds:
            raise ValueError("min_recovered cannot exceed seeds")
        if not self.bars_from < self.windows.discovery[0] < self.windows.confirmation[1] < self.bars_through:
            raise ValueError("bars must span every window")
        return self

    def cube_spec(self) -> CubeSpec:
        return CubeSpec(
            feed=self.feed,
            bars_from=self.bars_from,
            decisions=(self.windows.discovery[0], self.windows.confirmation[1]),
            bars_through=self.bars_through,
            bracket=self.bracket,
            universe=self.universe,
            decision_cost_bps=self.decision_cost_bps,
        )


@dataclass(frozen=True)
class LoadedProtocol:
    protocol: CampaignProtocol
    sha256: str
    path: Path


def load_campaign_protocol(path: Path) -> LoadedProtocol:
    raw = path.read_bytes()
    return LoadedProtocol(
        protocol=CampaignProtocol.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path
    )


@dataclass(frozen=True)
class ScoredFormula:
    formula_id: str
    nodes: int
    # (scores[S, N], allowed[S, N]) for the given view's sessions.
    panel: Callable[[CubeView], tuple[np.ndarray, np.ndarray]]


class Ledger(Protocol):
    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None: ...


class InMemoryLedger:
    """Power checks and tests: the same single-use rule as the journal ledger, in memory."""

    def __init__(self) -> None:
        self.consumed: list[dict] = []

    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None:
        start, end = interval
        for item in self.consumed:
            if item["cohort_sha256"] == cohort_sha256 and not (
                end < item["interval"][0] or item["interval"][1] < start
            ):
                raise ValueError(f"confirmation interval already consumed by campaign {item['campaign_id']}")
        self.consumed.append(
            {"cohort_sha256": cohort_sha256, "interval": interval, "campaign_id": campaign_id, "candidates": candidates}
        )


class CampaignWindows:
    """Opens the stage windows in order; confirmation only after its consumption is recorded."""

    def __init__(self, cube: LabelCube, windows: StageWindows, cohort_sha256: str):
        self._cube = cube
        self._windows = windows
        self._cohort_sha256 = cohort_sha256
        self._opened: list[str] = []

    @property
    def opened(self) -> tuple[str, ...]:
        return tuple(self._opened)

    def discovery(self) -> CubeView:
        self._opened.append("discovery")
        return self._cube.window(*self._windows.discovery)

    def selection(self) -> CubeView:
        self._opened.append("selection")
        return self._cube.window(*self._windows.selection)

    def confirmation(self, ledger: Ledger, *, campaign_id: str, candidates: tuple[str, ...]) -> CubeView:
        ledger.consume_confirmation(
            cohort_sha256=self._cohort_sha256,
            interval=self._windows.confirmation,
            campaign_id=campaign_id,
            candidates=candidates,
        )
        self._opened.append("confirmation")
        return self._cube.window(*self._windows.confirmation)


def _picks(formula: ScoredFormula, view: CubeView, k: int) -> Picks:
    scores, allowed = formula.panel(view)
    return select_picks(scores, allowed, view, k)


def _restrict(picks: Picks, lo: int, hi: int) -> Picks:
    keep = (picks.session_idx >= lo) & (picks.session_idx < hi)
    return Picks(picks.session_idx[keep] - lo, picks.symbol_idx[keep])


def _draws(view: CubeView, count: int, protocol: CampaignProtocol) -> np.ndarray:
    return bootstrap_draws(len(view.sessions), protocol.bootstrap.block_mean, count, protocol.bootstrap.seed)


def _jaccard(a: set, b: set) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _discovery(
    formulas: Sequence[ScoredFormula], view: CubeView, protocol: CampaignProtocol
) -> tuple[list[dict], list[str]]:
    gate = protocol.discovery_gate
    draws = _draws(view, protocol.bootstrap.discovery_draws, protocol)
    bounds = np.linspace(0, len(view.sessions), gate.blocks + 1).astype(int)
    results: list[dict[str, Any]] = []
    cells: dict[str, set[tuple[int, int]]] = {}
    for formula in formulas:
        picks = _picks(formula, view, protocol.k)
        table = session_table(picks, view, purge=True)
        edge = paired_edge_test(table, draws)
        leg = leg_mean_test(table, draws)
        block_means = []
        for lo, hi in pairwise(bounds.tolist()):
            sub = session_table(_restrict(picks, lo, hi), view.sub(lo, hi), purge=True)
            weight = np.isfinite(sub.edge)
            block_means.append(float(sub.edge[weight].mean()) if weight.any() else float("nan"))
        positive = sum(1 for m in block_means if np.isfinite(m) and m > 0)
        t = edge["t"]
        passes = bool(
            np.isfinite(t)
            and t >= gate.min_t
            and positive >= gate.min_positive_blocks
            and np.isfinite(leg["mean"])
            and leg["mean"] > 0
            and edge["n_sessions"] >= gate.min_sessions
        )
        fitness = (t if np.isfinite(t) else float("-inf")) - protocol.complexity_penalty_per_node * formula.nodes
        results.append(
            {
                "formula_id": formula.formula_id,
                "edge": edge,
                "leg": leg,
                "block_means": block_means,
                "passes": passes,
                "fitness": fitness,
            }
        )
        cells[formula.formula_id] = picks.cells()
    passing = sorted((r for r in results if r["passes"]), key=lambda r: (-r["fitness"], r["formula_id"]))
    kept: list[dict[str, Any]] = []
    for candidate in passing:
        if all(
            _jaccard(cells[candidate["formula_id"]], cells[k["formula_id"]]) < protocol.dedupe_jaccard for k in kept
        ):
            kept.append(candidate)
    return results, [r["formula_id"] for r in kept[: gate.carry]]


def _selection(
    formulas: Sequence[ScoredFormula],
    carried: Sequence[str],
    discovery: Sequence[dict],
    view: CubeView,
    protocol: CampaignProtocol,
) -> tuple[list[dict], list[str]]:
    gate = protocol.selection_gate
    draws = _draws(view, protocol.bootstrap.selection_draws, protocol)
    by_id = {f.formula_id: f for f in formulas}
    discovered = {r["formula_id"]: r["edge"]["mean"] for r in discovery}
    results: list[dict[str, Any]] = []
    for formula_id in carried:
        table = session_table(_picks(by_id[formula_id], view, protocol.k), view, purge=True)
        edge = paired_edge_test(table, draws)
        keep = bool(
            np.isfinite(edge["mean"])
            and edge["mean"] > 0
            and edge["mean"] >= gate.min_fraction_of_discovery * discovered[formula_id]
            and edge["n_sessions"] >= gate.min_sessions
        )
        results.append({"formula_id": formula_id, "edge": edge, "kept": keep})
    return results, [r["formula_id"] for r in results if r["kept"]]


def _months_before(day: date, months: int) -> date:
    year, month_index = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _confirmation(
    formulas: Sequence[ScoredFormula], frozen: Sequence[str], view: CubeView, protocol: CampaignProtocol
) -> tuple[list[dict], list[str]]:
    gate = protocol.confirmation_gate
    draws = _draws(view, protocol.bootstrap.confirmation_draws, protocol)
    by_id = {f.formula_id: f for f in formulas}
    recent_from = _months_before(view.sessions[-1], gate.recent_months)
    recent_lo = next((i for i, d in enumerate(view.sessions) if d > recent_from), len(view.sessions))
    results: dict[str, dict] = {}
    for formula_id in frozen:
        picks = _picks(by_id[formula_id], view, protocol.k)
        table = session_table(picks, view, purge=False)
        recent = table.edge[recent_lo:]
        recent = recent[np.isfinite(recent)]
        results[formula_id] = {
            "edge": paired_edge_test(table, draws),
            "leg": leg_mean_test(table, draws),
            "trimmed_mean": trimmed_mean(table.pick_rows["r_cost"].to_numpy(float), gate.trim_fraction),
            "recent_edge": float(recent.mean()) if recent.size else float("nan"),
        }
    adjusted = holm({fid: r["edge"]["p_one_sided"] for fid, r in results.items()})
    rows, confirmed = [], []
    for formula_id, r in results.items():
        passes = bool(
            adjusted[formula_id] <= gate.alpha
            and np.isfinite(r["leg"]["ci90"][0])
            and r["leg"]["ci90"][0] > 0
            and np.isfinite(r["trimmed_mean"])
            and r["trimmed_mean"] > 0
            and np.isfinite(r["recent_edge"])
            and r["recent_edge"] > 0
            and r["edge"]["n_sessions"] >= gate.min_sessions
        )
        rows.append({"formula_id": formula_id, **r, "holm_p": adjusted[formula_id], "passes": passes})
        if passes:
            confirmed.append(formula_id)
    return rows, confirmed


def run_stages(
    formulas: Sequence[ScoredFormula],
    windows: CampaignWindows,
    ledger: Ledger,
    protocol: CampaignProtocol,
    *,
    campaign_id: str,
) -> dict:
    ids = [formula.formula_id for formula in formulas]
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate formula_id in campaign: {duplicated}")
    discovery, carried = _discovery(formulas, windows.discovery(), protocol)
    outcome: dict = {
        "discovery": discovery,
        "carried": carried,
        "selection": [],
        "frozen": [],
        "confirmation": [],
        "confirmed": [],
    }
    if not carried:
        return {**outcome, "status": "no_finalists"}
    selection, frozen = _selection(formulas, carried, discovery, windows.selection(), protocol)
    outcome.update(selection=selection, frozen=frozen)
    if not frozen:
        return {**outcome, "status": "no_confirmation_candidates"}
    view = windows.confirmation(ledger, campaign_id=campaign_id, candidates=tuple(frozen))
    confirmation, confirmed = _confirmation(formulas, frozen, view, protocol)
    outcome.update(confirmation=confirmation, confirmed=confirmed)
    return {**outcome, "status": "confirmed" if confirmed else "none_confirmed"}
