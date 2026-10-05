# agentic_trader/research/pooled/search_power.py
"""Check B: can each family's real search recover a planted near-seed edge?

Reads discovery-window cells only and charges nothing. For seed i, the family is
``power_search.families[i mod n]``.

1. **Hidden expression.** Draw one or two mutations of one of the family's seeds, with the
   family's own operators and windows. It must compile, be dimensionless, not be a seed,
   and pick on at least ``discovery_gate.min_sessions`` sessions.
2. **Plant.** Add ``power_search.delta`` R (both cost levels) to its discovery picks.
3. **Search.** Run the family's real search on the shifted discovery view, with its
   campaign seed and budget.
4. **Recovery.** Seed i recovers when a formula passing the discovery gate overlaps the
   hidden picks at Jaccard >= ``dedupe_jaccard``.

This certifies recovery near the seeds only. With 16-17 formulas per family, the search
cannot reach edges far from them.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from agentic_trader.research.alpha.search import TypedGeneticSearch, canonical_expression
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    DiscoveryEvaluator,
    Family,
    LoadedProtocol,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, LabelCube, check_coverage
from agentic_trader.research.pooled.formula import Picks, jaccard_codes
from agentic_trader.research.pooled.genetic import NullCharger, family_search
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook, vet_expression
from agentic_trader.research.pooled.study import check_power_gate
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["HIDDEN_ATTEMPTS", "HIDDEN_SEED_STRIDE", "execute_search_power", "hidden_expression", "run_search_power"]

HIDDEN_ATTEMPTS = 200
HIDDEN_SEED_STRIDE = 1000


def hidden_expression(
    family: Family, *, protocol: CampaignProtocol, rng: random.Random, book: ScoreBook, view: CubeView
) -> tuple[str, Picks]:
    """A near-seed expression the family's grammar can reach, and its discovery picks."""
    mutator = TypedGeneticSearch(
        rng.getrandbits(32),
        archive_size=1,
        seeds=family.seeds,
        operators=family.mutation_operators,
        constant_windows=family.constant_windows,
    )
    seeds = {canonical_expression(seed) for seed in family.seeds}
    for _ in range(HIDDEN_ATTEMPTS):
        expression = canonical_expression(rng.choice(family.seeds))
        for _ in range(rng.choice((1, 2))):
            expression = mutator.mutate(expression)
        vetted = vet_expression(expression, protocol.search.forbidden_operators, seeds)
        if vetted.reason is not None:
            continue
        picks = protocol.select(*book.formula(vetted.expression).panel(view), view)
        if np.unique(picks.session_idx).size >= protocol.discovery_gate.min_sessions:
            return vetted.expression, picks
    raise ValueError(f"no usable hidden expression for family {family.id} after {HIDDEN_ATTEMPTS} draws")


def run_search_power(
    protocol: CampaignProtocol, cube: LabelCube, book: ScoreBook, *, progress: Callable[[str], None] | None = None
) -> dict:
    spec = protocol.power_search
    if spec.seed is None:
        raise ValueError("this protocol has no power_search.seed (campaign v1); check B needs protocol v2")
    view = cube.window(*protocol.windows.discovery)
    indexed = {family.id: (index, family) for index, family in enumerate(protocol.families)}
    budgets = protocol.family_budgets()
    seeds = []
    for i in range(spec.seeds):
        index, family = indexed[spec.families[i % len(spec.families)]]
        rng = random.Random(spec.seed * HIDDEN_SEED_STRIDE + i)
        hidden, picks = hidden_expression(family, protocol=protocol, rng=rng, book=book, view=view)
        shifted = cube.with_shift(picks.session_idx + view.offset, picks.symbol_idx, spec.delta)
        evaluator = DiscoveryEvaluator(shifted.window(*protocol.windows.discovery), protocol)
        run = family_search(
            family,
            index,
            budgets[family.id],
            protocol=protocol,
            evaluator=evaluator,
            book=book,
            charger=NullCharger(),
            seen=set(),
        )
        planted = picks.codes(len(view.symbols))
        overlaps = [(jaccard_codes(score.codes, planted), bool(score.row["passes"])) for score in run.scores]
        best = max((jaccard for jaccard, _ in overlaps), default=0.0)
        best_passing = max((jaccard for jaccard, passes in overlaps if passes), default=0.0)
        recovered = best_passing >= protocol.dedupe_jaccard
        seeds.append(
            {
                **run.summary(),
                "seed": i,
                "hidden": hidden,
                "hidden_sessions": int(np.unique(picks.session_idx).size),
                "best_jaccard": best,
                "best_passing_jaccard": best_passing,
                "recovered": recovered,
            }
        )
        if progress is not None:
            verdict = "recovered" if recovered else "missed"
            progress(
                f"search power {i + 1}/{spec.seeds}: {family.id} {verdict} (best passing Jaccard {best_passing:.2f})"
            )
    total = sum(1 for seed in seeds if seed["recovered"])
    return {
        "status": "passed" if total >= spec.min_recovered else "gate_failed",
        "recovered": total,
        "seeds_run": spec.seeds,
        "min_recovered": spec.min_recovered,
        "delta": spec.delta,
        # Formulas whose evaluation raised, over all seeds: must be 0 before a campaign.
        "errors_total": sum(seed["errors"] for seed in seeds),
        "seeds": seeds,
    }


async def execute_search_power(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    power_result: Mapping,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    protocol = loaded.protocol
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "search_power",
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "power_cube_sha256": power_result.get("cube_sha256"),
            "reads": "discovery window cells only",
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
        book = ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
        outcome = await asyncio.to_thread(run_search_power, protocol, built.cube, book, progress=progress)
        result = {**outcome, **base, "cube_sha256": built.cube.sha256}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", **base}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
