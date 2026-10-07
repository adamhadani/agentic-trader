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

from agentic_trader.research.alpha.operators import OPERATOR_SPECS
from agentic_trader.research.alpha.search import MUTATION_OPERATORS, WINDOWS, canonical_expression
from agentic_trader.research.pooled.cohort import UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CoverageSpec, CubeSpec, CubeView, LabelCube
from agentic_trader.research.pooled.formula import (
    Picks,
    jaccard_codes,
    require_dimensionless,
    select_picks,
    select_top_fraction,
)
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
    "DiscoveryEvaluator",
    "DiscoveryScore",
    "Family",
    "InMemoryLedger",
    "Ledger",
    "LoadedProtocol",
    "NullCheckSpec",
    "ScoredFormula",
    "Selection",
    "finish_discovery",
    "later_stages",
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
    # Campaign v2+: the windows that integer constants inside this family's expressions may
    # mutate to (wrapper operators keep the miner's global set). None (v1) is the global set.
    windows: tuple[int, ...] | None = None

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

    @field_validator("windows")
    @classmethod
    def _windows(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        if value is not None and (not value or list(value) != sorted(set(value)) or value[0] < 2):
            raise ValueError("windows must be non-empty, sorted, unique integers >= 2")
        return value

    @property
    def constant_windows(self) -> tuple[int, ...]:
        return WINDOWS if self.windows is None else self.windows


class ExcludedFamily(BaseModel, frozen=True, extra="forbid"):
    id: str
    reason: str


class SearchSpec(BaseModel, frozen=True, extra="forbid"):
    seed: int
    archive_size: int = Field(ge=1)
    forbidden_operators: tuple[str, ...]

    @field_validator("forbidden_operators")
    @classmethod
    def _known_operators(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # A misspelt name would silently ban nothing.
        unknown = {op.lower() for op in value} - {name.lower() for name in OPERATOR_SPECS}
        if unknown:
            raise ValueError(f"unknown forbidden operators: {sorted(unknown)}")
        return value


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
    seed: int | None = None  # check B's hidden-expression RNG (campaign v2+)


class NullCheckSpec(BaseModel, frozen=True, extra="forbid"):
    """Check C: the full campaign on per-name-demeaned discovery panels with real formulas."""

    replicates: int = Field(ge=1)
    max_false_acceptances: int = Field(ge=0)
    seed: int

    @model_validator(mode="after")
    def _bounded(self) -> NullCheckSpec:
        if self.max_false_acceptances > self.replicates:
            raise ValueError("max_false_acceptances cannot exceed replicates")
        return self


class Selection(BaseModel, frozen=True, extra="forbid"):
    """How a formula's picks are chosen each session: top k with hold-skipping, or a top-fraction basket."""

    rule: Literal["top_k", "top_fraction"]
    k: int | None = Field(default=None, ge=1)
    fraction: float | None = Field(default=None, gt=0, lt=1)

    @model_validator(mode="after")
    def _one_parameter(self) -> Selection:
        if self.rule == "top_k" and (self.k is None or self.fraction is not None):
            raise ValueError("top_k needs k and no fraction")
        if self.rule == "top_fraction" and (self.fraction is None or self.k is not None):
            raise ValueError("top_fraction needs fraction and no k")
        return self


SearchMode = Literal["genetic", "fixed_set"]


class CampaignProtocol(BaseModel, frozen=True, extra="forbid"):
    """A frozen pooled campaign protocol, pinned by the SHA-256 of its file.

    ``search_mode`` (v4+; earlier files omit it and mean ``genetic``) decides how the formula
    budget is spent. ``genetic`` mutates each family's seeds within its budget and needs
    ``power_search`` (check B). ``fixed_set`` scores exactly the predeclared seeds, one formula
    per seed with no mutation, so check B does not apply: it forbids ``power_search`` and
    requires ``null_check`` (check C).
    """

    id: Literal["pooled-campaign"]
    version: int = Field(ge=1)
    title: str
    rationale: str | None = None
    search_mode: SearchMode = "genetic"
    cohort: str
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    bars_from: date
    bars_through: date
    windows: StageWindows
    k: int | None = Field(default=None, ge=1)
    selection: Selection | None = None
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
    power_search: PowerSearchSpec | None = None
    null_check: NullCheckSpec | None = None

    @model_validator(mode="after")
    def _consistent(self) -> CampaignProtocol:
        if (self.k is None) == (self.selection is None):
            raise ValueError("a protocol has exactly one of k (top_k with hold-skipping) or selection")
        forbidden = {op.lower() for op in self.search.forbidden_operators}
        ids = [family.id for family in self.families]
        if len(ids) != len(set(ids)):
            raise ValueError("family ids must be unique")
        for family in self.families:
            if forbidden & {op.lower() for op in family.mutation_operators}:
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
        if self.search_mode == "fixed_set":
            self._fixed_set_consistent()
        else:
            if self.power_search is None:
                raise ValueError("a genetic protocol needs power_search (check B)")
            if set(self.power_search.families) - set(ids):
                raise ValueError("power_search families must be declared families")
            if self.power_search.min_recovered > self.power_search.seeds:
                raise ValueError("min_recovered cannot exceed seeds")
        if not self.bars_from < self.windows.discovery[0] < self.windows.confirmation[1] < self.bars_through:
            raise ValueError("bars must span every window")
        return self

    def _fixed_set_consistent(self) -> None:
        if self.power_search is not None:
            raise ValueError("a fixed_set protocol has no power_search: check B does not apply without search")
        if self.null_check is None:
            raise ValueError("a fixed_set protocol needs null_check (check C)")
        for family in self.families:
            if family.mutation_operators:
                raise ValueError(f"family {family.id}: mutation_operators must be empty in fixed_set mode")
            if family.windows is not None:
                raise ValueError(f"family {family.id}: windows must be null in fixed_set mode")
        seeds = self.seed_expressions
        if len(set(seeds)) != len(seeds):
            raise ValueError("fixed_set seeds must be unique across families (canonical expressions)")
        if self.formula_budget != len(seeds):
            raise ValueError("fixed_set formula_budget must equal the number of seeds")

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

    @property
    def selection_rule(self) -> Selection:
        """The effective rule; a v1/v2 ``k`` means top_k with hold-skipping."""
        return self.selection if self.selection is not None else Selection(rule="top_k", k=self.k)

    def select(self, scores: np.ndarray, allowed: np.ndarray, view: CubeView) -> Picks:
        """Every stage, check and overlap picks through this one rule."""
        rule = self.selection_rule
        if rule.rule == "top_fraction" and rule.fraction is not None:
            return select_top_fraction(scores, allowed, view, rule.fraction)
        if rule.rule == "top_k" and rule.k is not None:
            return select_picks(scores, allowed, view, rule.k)
        raise ValueError(f"unusable selection rule: {rule}")

    @property
    def seed_expressions(self) -> tuple[str, ...]:
        """Every family's seeds as canonical expressions (the search's ``seen`` form), in file order."""
        return tuple(canonical_expression(seed) for family in self.families for seed in family.seeds)

    @property
    def requires_search_power(self) -> bool:
        """Whether a campaign needs search-power check B: only a genetic search has one."""
        return self.search_mode == "genetic"

    @property
    def gates(self) -> tuple[tuple[str, str], ...]:
        """(gate name, the ``check`` its manifest must record), by search mode."""
        if self.search_mode == "fixed_set":
            return (("power", "power_a"), ("null_check", "null_check"))
        return (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))

    def family_budgets(self) -> dict[str, int]:
        """Each family's share of the formula budget.

        Genetic: split evenly across families, the remainder to the first in file order. Fixed
        set: each family's seed count (the validators make these sum to ``formula_budget``), so
        every predeclared seed is scored whatever the families' sizes.
        """
        if self.search_mode == "fixed_set":
            return {family.id: len(family.seeds) for family in self.families}
        base, remainder = divmod(self.formula_budget, len(self.families))
        return {family.id: base + (1 if index < remainder else 0) for index, family in enumerate(self.families)}

    def require_campaign_ready(self) -> None:
        """Refuse a protocol that lacks a check its mode needs: C always, B's seed in genetic mode."""
        if self.null_check is None or (
            self.requires_search_power and (self.power_search is None or self.power_search.seed is None)
        ):
            raise ValueError(
                "this protocol predates checks B and C (campaign v1); a campaign needs protocol v2 or later"
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
    """Power checks and tests: the journal ledger's lane-wide single-use rule, in memory."""

    def __init__(self) -> None:
        self.consumed: list[dict] = []

    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None:
        start, end = interval
        for item in self.consumed:
            if not (end < item["interval"][0] or item["interval"][1] < start):
                raise ValueError(f"confirmation interval already consumed by campaign {item['campaign_id']}")
        self.consumed.append(
            {"cohort_sha256": cohort_sha256, "interval": interval, "campaign_id": campaign_id, "candidates": candidates}
        )


class CampaignWindows:
    """Opens the stage windows in order; confirmation only after its consumption is recorded."""

    def __init__(self, cube: LabelCube, windows: StageWindows, cohort_sha256: str):
        if cube.cohort_sha256 != cohort_sha256:
            raise ValueError(f"the cube was built for cohort {cube.cohort_sha256[:16]}, not {cohort_sha256[:16]}")
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


DESCRIPTIVE_TOP_K = 3  # the descriptive top-k reported beside a top-fraction basket (never a gate)


def _picks(formula: ScoredFormula, view: CubeView, protocol: CampaignProtocol) -> Picks:
    scores, allowed = formula.panel(view)
    return protocol.select(scores, allowed, view)


def _restrict(picks: Picks, lo: int, hi: int) -> Picks:
    keep = (picks.session_idx >= lo) & (picks.session_idx < hi)
    return Picks(picks.session_idx[keep] - lo, picks.symbol_idx[keep])


def _draws(view: CubeView, count: int, protocol: CampaignProtocol) -> np.ndarray:
    return bootstrap_draws(len(view.sessions), protocol.bootstrap.block_mean, count, protocol.bootstrap.seed)


@dataclass(frozen=True)
class DiscoveryScore:
    """One formula's discovery result row and its pick codes (for dedupe and overlap)."""

    row: dict[str, Any]
    codes: np.ndarray  # sorted int64 session * names + symbol


class DiscoveryEvaluator:
    """Scores formulas on the discovery view one at a time, so a search can use each fitness."""

    def __init__(self, view: CubeView, protocol: CampaignProtocol):
        self._view = view
        self._protocol = protocol
        self._draws = _draws(view, protocol.bootstrap.discovery_draws, protocol)
        self._bounds = np.linspace(0, len(view.sessions), protocol.discovery_gate.blocks + 1).astype(int)

    def score(self, formula: ScoredFormula) -> DiscoveryScore:
        gate = self._protocol.discovery_gate
        view = self._view
        picks = _picks(formula, view, self._protocol)
        table = session_table(picks, view, purge=True)
        edge = paired_edge_test(table, self._draws)
        leg = leg_mean_test(table, self._draws)
        block_means = []
        for lo, hi in pairwise(self._bounds.tolist()):
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
        fitness = (t if np.isfinite(t) else float("-inf")) - self._protocol.complexity_penalty_per_node * formula.nodes
        row = {
            "formula_id": formula.formula_id,
            "edge": edge,
            "leg": leg,
            "block_means": block_means,
            "passes": passes,
            "fitness": fitness,
        }
        return DiscoveryScore(row=row, codes=picks.codes(len(view.symbols)))


def finish_discovery(scores: Sequence[DiscoveryScore], protocol: CampaignProtocol) -> tuple[list[dict], list[str]]:
    """Every discovery row, and the gate's survivors deduplicated by pick overlap, then capped."""
    results = [score.row for score in scores]
    codes = {score.row["formula_id"]: score.codes for score in scores}
    passing = sorted((r for r in results if r["passes"]), key=lambda r: (-r["fitness"], r["formula_id"]))
    kept: list[dict[str, Any]] = []
    for candidate in passing:
        if all(
            jaccard_codes(codes[candidate["formula_id"]], codes[k["formula_id"]]) < protocol.dedupe_jaccard
            for k in kept
        ):
            kept.append(candidate)
    return results, [r["formula_id"] for r in kept[: protocol.discovery_gate.carry]]


def _discovery(
    formulas: Sequence[ScoredFormula], view: CubeView, protocol: CampaignProtocol
) -> tuple[list[dict], list[str]]:
    evaluator = DiscoveryEvaluator(view, protocol)
    return finish_discovery([evaluator.score(formula) for formula in formulas], protocol)


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
        table = session_table(_picks(by_id[formula_id], view, protocol), view, purge=True)
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
        scores, allowed = by_id[formula_id].panel(view)
        picks = protocol.select(scores, allowed, view)
        table = session_table(picks, view, purge=False)
        recent = table.edge[recent_lo:]
        recent = recent[np.isfinite(recent)]
        results[formula_id] = {
            "edge": paired_edge_test(table, draws),
            "leg": leg_mean_test(table, draws),
            "trimmed_mean": trimmed_mean(table.pick_rows["r_cost"].to_numpy(float), gate.trim_fraction),
            "recent_edge": float(recent.mean()) if recent.size else float("nan"),
        }
        if protocol.selection_rule.rule == "top_fraction":
            # Descriptive only: live cards trade the top names of a confirmed basket.
            top = session_table(select_picks(scores, allowed, view, DESCRIPTIVE_TOP_K), view, purge=False)
            results[formula_id]["top3_edge"] = paired_edge_test(top, draws)
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


def later_stages(
    formulas: Sequence[ScoredFormula],
    discovery: list[dict],
    carried: list[str],
    windows: CampaignWindows,
    ledger: Ledger,
    protocol: CampaignProtocol,
    *,
    campaign_id: str,
    on_frozen: Callable[[list[str]], None] | None = None,
) -> dict:
    """Selection and confirmation after discovery.

    ``on_frozen`` runs once the candidates are frozen, before the confirmation interval is
    consumed (the campaign writes the frozen documents there).
    """
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
    if on_frozen is not None:
        on_frozen(list(frozen))
    view = windows.confirmation(ledger, campaign_id=campaign_id, candidates=tuple(frozen))
    confirmation, confirmed = _confirmation(formulas, frozen, view, protocol)
    outcome.update(confirmation=confirmation, confirmed=confirmed)
    return {**outcome, "status": "confirmed" if confirmed else "none_confirmed"}


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
    return later_stages(formulas, discovery, carried, windows, ledger, protocol, campaign_id=campaign_id)
