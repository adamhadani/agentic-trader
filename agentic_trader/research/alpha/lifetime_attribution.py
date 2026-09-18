"""Paired trade-lifetime counterfactuals over a frozen alpha selection.

This module is deliberately pure. It applies versioned execution policies to one
already-selected daily definition and replays the same observations and scores;
it never searches, promotes, reads runtime storage, or talks to a broker.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final, Literal

import pandas as pd
from pydantic import Field, model_validator

from agentic_trader.execution.lifetime_policy import (
    TRADE_LIFETIME_VERSION_INDEPENDENT,
    TradeLifetimePolicy,
)
from agentic_trader.market.bars import FixedDailyClockPolicy
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.power_study import PROFILES
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.strategy import TimedAlphaExecutionPolicy, alpha_scores
from agentic_trader.research.alpha.study import (
    DAILY_NOISE,
    INTRABAR_STEPS,
    LOG_VOL_NOISE,
    SEARCH_METHODS,
    FrozenModel,
    MarketScenario,
    StudyPhase,
    StudyStatus,
    market_bars,
    study_catalog,
    study_seed,
)
from agentic_trader.research.alpha.validation import ValidationPolicy


LIFETIME_ATTRIBUTION_VERSION = "alpha_lifetime_attribution_v1"
LIFETIME_SCHEMA: Final = "alpha_lifetime_attribution_v1"
DEFAULT_ENTRY_LIFETIME_SECONDS = 86_400
DEFAULT_HOLDING_LIFETIME_SECONDS = 86_400
MAX_LIFETIME_SEARCHES = 800


class LifetimeVariant(StrEnum):
    ORIGINAL = "p0_original"
    ENTRY_ONLY = "p1_entry_only"
    ENTRY_AND_HOLDING = "p2_entry_and_holding"


@dataclass(frozen=True)
class LifetimeVariantSpec:
    variant: LifetimeVariant
    resting_seconds: int | None
    holding_seconds: int | None


def _digest(document: Any) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, allow_nan=False).encode()).hexdigest()


class LifetimeAttributionProtocol(FrozenModel):
    """Predeclared paired-search budget; all output remains diagnostic-only."""

    schema_version: Literal["alpha_lifetime_attribution_v1"] = LIFETIME_SCHEMA
    seed: int = Field(ge=0, lt=2**128)
    observations: int = Field(default=2500, ge=600, le=5000)
    development_replicates: int = Field(default=4, ge=1, le=8)
    null_replicates: int = Field(default=128, ge=1, le=256)
    edge_replicates: int = Field(default=64, ge=1, le=128)
    generated_candidates: int = Field(default=8, ge=1, le=16)
    search_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    entry_lifetime_seconds: int = Field(default=DEFAULT_ENTRY_LIFETIME_SECONDS, ge=1, le=31 * 86400)
    holding_lifetime_seconds: int = Field(default=DEFAULT_HOLDING_LIFETIME_SECONDS, ge=1, le=31 * 86400)

    @model_validator(mode="after")
    def bounded(self):
        if len(tuple(lifetime_jobs(self))) > MAX_LIFETIME_SEARCHES:
            raise ValueError("Lifetime attribution search budget exceeded")
        if len(tuple(lifetime_jobs(self))) * self.trial_budget * self.observations > 100_000_000:
            raise ValueError("Lifetime attribution trial-bar budget exceeded")
        return self

    @property
    def trial_budget(self) -> int:
        return len(study_catalog().list_alphas()) + self.generated_candidates

    @property
    def identity(self) -> str:
        return _digest(self.document())

    def document(self) -> dict[str, Any]:
        jobs = tuple(lifetime_jobs(self))
        return {
            **self.model_dump(mode="json"),
            "contracts": {
                "profiles": PROFILES,
                "methods": SEARCH_METHODS,
                "generator": {
                    "intrabar_steps": INTRABAR_STEPS,
                    "daily_noise": DAILY_NOISE,
                    "log_vol_noise": LOG_VOL_NOISE,
                },
                "variants": [spec.variant.value for spec in lifetime_variant_specs()],
                "selection": "frozen_once_before_paired_lifetime_replay",
                "synthetic_only": True,
                "authorizes_promotion": False,
            },
            "budget": {
                "datasets": len(jobs),
                "searches": len(jobs),
                "expression_evaluations": len(jobs) * self.trial_budget,
                "variant_replays_max": len(jobs) * 2 * 3,
            },
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> LifetimeAttributionProtocol:
        protocol = cls.model_validate_json(
            json.dumps({key: value for key, value in document.items() if key not in ("contracts", "budget")})
        )
        if _digest(protocol.document()) != _digest(document):
            raise ValueError("Frozen lifetime attribution contracts differ from current implementation")
        return protocol


@dataclass(frozen=True)
class LifetimeJob:
    phase: StudyPhase
    scenario: MarketScenario
    effect: float
    replicate: int
    method: str
    kind: str = "search"

    @property
    def key(self) -> str:
        return f"{self.phase}/lifetime/{self.scenario.name}/{self.effect}/{self.method}/{self.replicate}"

    @property
    def identity(self) -> str:
        return _digest({"schema": LIFETIME_SCHEMA, "key": self.key})

    @property
    def observations(self) -> int:
        return self.scenario.observations


def lifetime_jobs(protocol: LifetimeAttributionProtocol):
    for phase in StudyPhase:
        for profile, (interval, positive, persistence) in PROFILES.items():
            for effect in (0.0, positive):
                count = (
                    protocol.development_replicates
                    if phase == StudyPhase.DEVELOPMENT
                    else (protocol.null_replicates if effect == 0 else protocol.edge_replicates)
                )
                scenario = MarketScenario(
                    name=profile,
                    observations=protocol.observations,
                    interval=interval,
                    effect=effect,
                    volatility_persistence=persistence,
                )
                for method in SEARCH_METHODS:
                    for replicate in range(count):
                        yield LifetimeJob(phase, scenario, effect, replicate, method)


def lifetime_seeds(job: LifetimeJob, protocol: LifetimeAttributionProtocol) -> dict[str, int]:
    return {
        role: study_seed(protocol.seed, job.phase, f"{LIFETIME_SCHEMA}/{job.scenario.name}", job.replicate, role)
        for role in ("data", f"search/{job.method}")
    }


def lifetime_endpoint_rows(job: LifetimeJob, protocol: LifetimeAttributionProtocol) -> list[dict[str, Any]]:
    return [
        {
            "phase": str(job.phase),
            "profile": job.scenario.name,
            "effect": job.effect,
            "method": job.method,
            "route": route,
            "variant": variant.value,
            "status": None,
        }
        for route in ("known", "winner")
        for variant in LifetimeVariant
    ]


def select_lifetime_candidates(
    bars: pd.DataFrame, protocol: LifetimeAttributionProtocol, *, search_seed: int, method: str
) -> dict[str, Any]:
    miner = AlphaMiner(seed=search_seed, catalog=study_catalog(), policy=ValidationPolicy())
    ranked = miner.mine(
        bars,
        iterations=protocol.generated_candidates,
        symbol="SYNTH",
        method=method,
        min_sharpe=-math.inf,
        min_dsr=0,
        min_ic=-math.inf,
        max_seconds=protocol.search_timeout_seconds,
    )
    run = miner.last_run
    known: dict[str, Any] = next(
        (trial for trial in run["trials"] if trial["definition"]["alpha_id"] == "synthetic_pulse"), {}
    )
    return {
        "run": run,
        "selected_version": ranked[0].definition.version_id if ranked else None,
        "known_discovery_rank": next(
            (index + 1 for index, candidate in enumerate(ranked) if candidate.definition.alpha_id == "synthetic_pulse"),
            None,
        ),
        "routes": {"known": known.get("candidate"), "winner": ranked[0].to_dict() if ranked else None},
        "authorizes_promotion": False,
    }


def evaluate_lifetime_job(
    job: LifetimeJob,
    protocol: LifetimeAttributionProtocol,
    *,
    checkpoint=None,
) -> dict[str, Any]:
    seeds = lifetime_seeds(job, protocol)
    bars = market_bars(job.scenario, seed=seeds["data"])
    selection = select_lifetime_candidates(bars, protocol, search_seed=seeds[f"search/{job.method}"], method=job.method)
    if checkpoint:
        checkpoint(selection)
    result: dict[str, Any] = {
        "job_id": job.identity,
        "job_key": job.key,
        "protocol_id": protocol.identity,
        "status": StudyStatus.COMPLETED,
        "seeds": seeds,
        "selection": selection,
        "routes": {},
        "rows": lifetime_endpoint_rows(job, protocol),
        "synthetic_only": True,
        "authorizes_promotion": False,
    }
    if selection["run"]["status"] != "completed" or selection["run"]["trial_count"] != protocol.trial_budget:
        result.update(status=StudyStatus.FAILED, error="Incomplete discovery; declared budget retained")
        return result
    for route, candidate in selection["routes"].items():
        if candidate is None:
            result["routes"][route] = {"status": "unavailable", "reason": "no_selected_candidate"}
            continue
        try:
            definition = AlphaDefinition.from_dict(candidate["definition"])
            report = evaluate_lifetime_attribution(
                definition,
                bars,
                entry_seconds=protocol.entry_lifetime_seconds,
                holding_seconds=protocol.holding_lifetime_seconds,
            )
            result["routes"][route] = {"status": "completed", **report}
            for row in result["rows"]:
                if row["route"] == route:
                    row["status"] = "completed"
        except ValueError as exc:
            # A selected non-daily candidate is evidence that this route cannot be
            # paired under the frozen clock, not permission to alter its identity.
            result["routes"][route] = {"status": "unavailable", "reason": str(exc)}
            for row in result["rows"]:
                if row["route"] == route:
                    row["status"] = "unavailable"
    return result


def summarize_lifetime_study(protocol: LifetimeAttributionProtocol, records: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {job.identity: job for job in lifetime_jobs(protocol)}
    supplied = {record["job_id"]: record for record in records}
    if len(supplied) != len(records) or set(supplied) - set(expected):
        raise ValueError("Duplicate or unknown lifetime attribution record")
    rows = [row for record in records for row in record.get("rows", [])]
    completed = sum(record.get("status") == StudyStatus.COMPLETED for record in records)
    unavailable = sum(row.get("status") == "unavailable" for row in rows)
    return {
        "schema_version": LIFETIME_SCHEMA,
        "protocol_id": protocol.identity,
        "synthetic_only": True,
        "authorizes_promotion": False,
        "status": StudyStatus.COMPLETED
        if len(supplied) == len(expected) and completed == len(records)
        else StudyStatus.INCOMPLETE,
        "expected_jobs": len(expected),
        "recorded_jobs": len(records),
        "completed_jobs": completed,
        "unavailable_endpoints": unavailable,
        "rows": rows,
    }


def lifetime_variant_specs(
    *, entry_seconds: int = DEFAULT_ENTRY_LIFETIME_SECONDS, holding_seconds: int = DEFAULT_HOLDING_LIFETIME_SECONDS
) -> tuple[LifetimeVariantSpec, ...]:
    """Return the fixed diagnostic matrix; only P0 is confirmatory in the plan."""
    return (
        LifetimeVariantSpec(LifetimeVariant.ORIGINAL, None, None),
        LifetimeVariantSpec(LifetimeVariant.ENTRY_ONLY, entry_seconds, None),
        LifetimeVariantSpec(LifetimeVariant.ENTRY_AND_HOLDING, entry_seconds, holding_seconds),
    )


def _timed_definition(definition: AlphaDefinition, spec: LifetimeVariantSpec) -> AlphaDefinition:
    if definition.clock is not None or definition.timeframe != "1d" or definition.data_feed != "synthetic":
        raise ValueError("Lifetime attribution requires an unclocked synthetic daily definition")
    if spec.variant == LifetimeVariant.ORIGINAL:
        return definition
    if isinstance(definition.execution, TimedAlphaExecutionPolicy):
        raise TypeError("Lifetime attribution source must not already contain a lifetime")
    lifetime = TradeLifetimePolicy(
        resting_seconds=spec.resting_seconds,
        holding_seconds=spec.holding_seconds,
        version=TRADE_LIFETIME_VERSION_INDEPENDENT,
    )
    execution = TimedAlphaExecutionPolicy(**definition.execution.to_dict(), lifetime=lifetime)
    return replace(
        definition,
        execution=execution,
        semantics_version=4,
        clock=FixedDailyClockPolicy(),
    )


def evaluate_lifetime_attribution(
    definition: AlphaDefinition,
    bars: pd.DataFrame,
    *,
    scores: pd.Series | None = None,
    entry_seconds: int = DEFAULT_ENTRY_LIFETIME_SECONDS,
    holding_seconds: int = DEFAULT_HOLDING_LIFETIME_SECONDS,
    trace: bool = True,
) -> dict[str, Any]:
    """Replay one frozen selection under P0/P1/P2 with paired observations.

    The caller supplies the already-selected definition and, when available, a
    precomputed score series. We intentionally do not rank or reselect per
    lifetime so changes are attributable to execution lifetime only.
    """
    if not isinstance(definition, AlphaDefinition):
        raise TypeError("Frozen AlphaDefinition required")
    paired_scores = alpha_scores(definition, bars) if scores is None else scores
    variants = []
    for spec in lifetime_variant_specs(entry_seconds=entry_seconds, holding_seconds=holding_seconds):
        variant_definition = _timed_definition(definition, spec)
        result = simulate_strategy(variant_definition, bars, scores=paired_scores, trace=trace)
        variants.append(
            {
                "variant": spec.variant.value,
                "definition_id": variant_definition.version_id,
                "source_definition_id": definition.version_id,
                "entry_lifetime_seconds": spec.resting_seconds,
                "holding_lifetime_seconds": spec.holding_seconds,
                "total_return_pct": result["total_return_pct"],
                "total_trades": result["total_trades"],
                "open_position": result["open_position"],
                "pending_entry": result["pending_entry"],
                "entry_expiries": sum(e["kind"] == "entry_expired" for e in result.get("events", [])),
                "holding_expiries": sum(e["kind"] == "holding_expired" for e in result.get("events", [])),
                "trades": result["trades"],
                "events": result.get("events", []) if trace else None,
            }
        )
    if {row["source_definition_id"] for row in variants} != {definition.version_id}:
        raise AssertionError("Lifetime variants lost the frozen source identity")
    return {
        "schema_version": LIFETIME_ATTRIBUTION_VERSION,
        "synthetic_only": True,
        "authorizes_promotion": False,
        "selection": {
            "definition_id": definition.version_id,
            "alpha_id": definition.alpha_id,
            "expression": definition.expression,
            "researched_once": True,
        },
        "variants": variants,
    }
