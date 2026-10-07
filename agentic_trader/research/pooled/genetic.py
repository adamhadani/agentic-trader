# agentic_trader/research/pooled/genetic.py
"""The budgeted per-family genetic search over the discovery window, and the full campaign run.

Each family searches with its own seeds, mutation operators and constant windows
(``TypedGeneticSearch``), seeded ``search.seed * FAMILY_SEED_STRIDE + family index``, for
its share of the formula budget (``CampaignProtocol.family_budgets``).

**Vetting.** A proposal is vetted before anything is spent. One that does not compile,
repeats an earlier proposal of this campaign, calls a forbidden operator or is not
dimensionless is recorded as rejected and is never charged or evaluated.

**Charging.** An accepted proposal is charged (``Charger.charge``) *before* it is scored on
the discovery view, and its fitness is told back to the search.

**Label blindness.** The search sees fitness numbers only, and formula panels read no label
(``ScoreBook``).

``run_search_stages`` then runs Part 1a's pooled discovery finish, selection and
confirmation unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from agentic_trader.research.alpha.search import TypedGeneticSearch
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    DiscoveryEvaluator,
    DiscoveryScore,
    Family,
    Ledger,
    ScoredFormula,
    finish_discovery,
    later_stages,
)
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.scoring import ScoreBook, vet_expression


__all__ = [
    "FAMILY_SEED_STRIDE",
    "MAX_CONSECUTIVE_REJECTIONS",
    "Charger",
    "FamilyRun",
    "NullCharger",
    "SearchOutcome",
    "family_search",
    "run_search_stages",
    "search_campaign",
]

FAMILY_SEED_STRIDE = 100
MAX_CONSECUTIVE_REJECTIONS = 200


class Charger(Protocol):
    def reserve_family(self, family_id: str, budget: int) -> None: ...

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None: ...


class NullCharger:
    """Checks B and C run the same search but never touch the ledger."""

    def reserve_family(self, family_id: str, budget: int) -> None:
        return None

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None:
        return None


@dataclass(frozen=True)
class FamilyRun:
    family_id: str
    budget: int
    charged: int
    errors: int
    rejected: dict[str, int]
    stopped_short: str | None  # why the family ended before its budget; None when it spent it
    scores: tuple[DiscoveryScore, ...]
    formulas: dict[str, ScoredFormula]
    expressions: dict[str, str]
    records: tuple[dict, ...]
    mutation_count: int  # the search's successful mutations; always 0 in fixed_set mode

    def summary(self) -> dict:
        return {
            "family": self.family_id,
            "budget": self.budget,
            "charged": self.charged,
            "evaluated": len(self.scores),
            "errors": self.errors,
            "rejected": dict(self.rejected),
            "stopped_short": self.stopped_short,
        }


@dataclass(frozen=True)
class SearchOutcome:
    runs: tuple[FamilyRun, ...]

    @property
    def scores(self) -> tuple[DiscoveryScore, ...]:
        return tuple(score for run in self.runs for score in run.scores)

    @property
    def formulas(self) -> dict[str, ScoredFormula]:
        return {fid: formula for run in self.runs for fid, formula in run.formulas.items()}

    @property
    def expressions(self) -> dict[str, str]:
        return {fid: expression for run in self.runs for fid, expression in run.expressions.items()}

    @property
    def families(self) -> dict[str, str]:
        return {fid: run.family_id for run in self.runs for fid in run.formulas}

    @property
    def records(self) -> tuple[dict, ...]:
        return tuple(record for run in self.runs for record in run.records)

    @property
    def codes(self) -> dict[str, np.ndarray]:
        return {score.row["formula_id"]: score.codes for score in self.scores}


def family_search(
    family: Family,
    index: int,
    budget: int,
    *,
    protocol: CampaignProtocol,
    evaluator: DiscoveryEvaluator,
    book: ScoreBook,
    charger: Charger,
    seen: set[str],
) -> FamilyRun:
    """Spend one family's budget: vet, charge, score and tell, until the budget or the grammar runs out."""
    search = TypedGeneticSearch(
        protocol.search.seed * FAMILY_SEED_STRIDE + index,
        archive_size=protocol.search.archive_size,
        seeds=family.seeds,
        operators=family.mutation_operators,
        constant_windows=family.constant_windows,
    )
    charger.reserve_family(family.id, budget)
    scores: list[DiscoveryScore] = []
    formulas: dict[str, ScoredFormula] = {}
    expressions: dict[str, str] = {}
    records: list[dict] = []
    rejected: Counter[str] = Counter()
    charged = errors = streak = 0
    stopped: str | None = None
    # Fixed-set contract: every family has no mutation operators and its budget equals its seed
    # count (``CampaignProtocol.family_budgets``), so the loop ends once the seeds are charged and
    # never reaches ``mutate``. Should a seed be rejected, the next ``ask`` raises "no mutation
    # operators" and the family stops short instead of mutating.
    while charged < budget:
        try:
            proposal = search.ask()
        except ValueError as exc:  # no unseen expression is left in this family's grammar, or no operators
            stopped = f"search exhausted: {exc}"
            break
        vetted = vet_expression(proposal, protocol.search.forbidden_operators, seen)
        if vetted.reason is not None:
            rejected[vetted.reason] += 1
            records.append(
                {"family": family.id, "expression": vetted.expression, "status": "rejected", "reason": vetted.reason}
            )
            streak += 1
            if streak >= MAX_CONSECUTIVE_REJECTIONS:
                stopped = f"{streak} consecutive rejected proposals"
                break
            continue
        streak = 0
        seen.add(vetted.expression)
        formula = book.formula(vetted.expression)
        charger.charge(family.id, vetted.expression, formula.formula_id, formula.nodes)
        charged += 1
        formulas[formula.formula_id] = formula
        expressions[formula.formula_id] = vetted.expression
        base = {"family": family.id, "expression": vetted.expression, "formula_id": formula.formula_id}
        try:
            score = evaluator.score(formula)
        except Exception as exc:  # recorded and charged, never retried with changes
            errors += 1
            records.append({**base, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            search.tell(vetted.expression, float("-inf"))
            continue
        scores.append(score)
        row = score.row
        records.append(
            {
                **base,
                "status": "evaluated",
                "fitness": row["fitness"],
                "edge_t": row["edge"]["t"],
                "edge_mean": row["edge"]["mean"],
                "leg_mean": row["leg"]["mean"],
                "n_sessions": int(row["edge"]["n_sessions"]),
                "passes": bool(row["passes"]),
            }
        )
        search.tell(vetted.expression, row["fitness"])
    return FamilyRun(
        family_id=family.id,
        budget=budget,
        charged=charged,
        errors=errors,
        rejected=dict(rejected),
        stopped_short=stopped,
        scores=tuple(scores),
        formulas=formulas,
        expressions=expressions,
        records=tuple(records),
        mutation_count=search.mutation_count,
    )


def search_campaign(
    protocol: CampaignProtocol,
    view: CubeView,
    book: ScoreBook,
    charger: Charger,
    *,
    progress: Callable[[str], None] | None = None,
) -> SearchOutcome:
    """Every family's search on the discovery view, in file order, sharing one seen-set."""
    evaluator = DiscoveryEvaluator(view, protocol)
    budgets = protocol.family_budgets()
    seen: set[str] = set()
    runs = []
    for index, family in enumerate(protocol.families):
        run = family_search(
            family,
            index,
            budgets[family.id],
            protocol=protocol,
            evaluator=evaluator,
            book=book,
            charger=charger,
            seen=seen,
        )
        runs.append(run)
        if progress is not None:
            note = f"; stopped: {run.stopped_short}" if run.stopped_short else ""
            progress(
                f"family {family.id}: {run.charged}/{run.budget} charged, {len(run.scores)} evaluated, "
                f"{run.errors} errors, {sum(run.rejected.values())} rejected{note}"
            )
    return SearchOutcome(tuple(runs))


def run_search_stages(
    protocol: CampaignProtocol,
    windows: CampaignWindows,
    book: ScoreBook,
    ledger: Ledger,
    charger: Charger,
    *,
    campaign_id: str,
    on_frozen: Callable[[list[str], SearchOutcome], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, SearchOutcome]:
    """The whole campaign: per-family search, pooled discovery finish, selection and confirmation."""
    search = search_campaign(protocol, windows.discovery(), book, charger, progress=progress)
    discovery, carried = finish_discovery(search.scores, protocol)
    hook = None if on_frozen is None else (lambda frozen: on_frozen(frozen, search))
    outcome = later_stages(
        list(search.formulas.values()),
        discovery,
        carried,
        windows,
        ledger,
        protocol,
        campaign_id=campaign_id,
        on_frozen=hook,
    )
    return outcome, search
