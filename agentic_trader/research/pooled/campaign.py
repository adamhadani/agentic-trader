# agentic_trader/research/pooled/campaign.py
"""The budgeted pooled campaign: frozen protocol, and (below) its pure stage logic.

The protocol is committed before any real-data run of the pooled lane; its SHA-256 is
recorded by every Part 1 manifest. Discovery, selection and confirmation are pure
functions over cube windows with an injected ledger, so power check A runs them
unchanged on synthetic cubes with an in-memory ledger.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.alpha.search import MUTATION_OPERATORS
from agentic_trader.research.pooled.cohort import UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CoverageSpec, CubeSpec
from agentic_trader.research.pooled.formula import require_dimensionless


__all__ = ["CampaignProtocol", "LoadedProtocol", "load_campaign_protocol"]


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
