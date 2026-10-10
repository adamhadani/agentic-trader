"""Frozen protocol and cohort files for the spread-reversion lane.

Identity is the SHA-256 of the file bytes; a changed file is a new version. The protocol
pins its cohort's hash; executors refuse a cohort whose hash differs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.apriori.catalog import BootstrapSpec


__all__ = [
    "BarRange",
    "FormationRule",
    "LoadedSpreadCohort",
    "LoadedSpreadProtocol",
    "NullSpec",
    "PassRules",
    "PowerSpec",
    "Schedule",
    "SpreadCohort",
    "SpreadProtocol",
    "StageWindows",
    "TradingRule",
    "load_spread_cohort",
    "load_spread_protocol",
]

SYMBOL = re.compile(r"^[A-Z]{1,5}$")
LANE = "spread"


class SpreadCohort(BaseModel, frozen=True, extra="forbid"):
    id: Literal["spread-cohort"]
    version: int = Field(ge=1)
    survivorship: str = Field(min_length=1)
    market: str
    sectors: dict[str, tuple[str, ...]]

    @field_validator("market")
    @classmethod
    def _market_symbol(cls, value: str) -> str:
        if not SYMBOL.match(value):
            raise ValueError(f"market symbol {value!r} is not an upper-case ticker")
        return value

    @model_validator(mode="after")
    def _well_formed(self) -> SpreadCohort:
        seen: dict[str, str] = {}
        for sector, members in self.sectors.items():
            if len(members) < 2:
                raise ValueError(f"sector {sector!r} needs at least two symbols")
            for symbol in members:
                if not SYMBOL.match(symbol):
                    raise ValueError(f"symbol {symbol!r} in sector {sector!r} is not an upper-case ticker")
                if symbol in seen:
                    raise ValueError(f"symbol {symbol!r} appears in more than one sector ({seen[symbol]}, {sector})")
                seen[symbol] = sector
        if self.market in seen:
            raise ValueError("market symbol must not be a cohort member")
        return self

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(s for members in self.sectors.values() for s in members))

    def pairs(self) -> tuple[tuple[str, str, str], ...]:
        """Same-sector pairs ``(y, x, sector)`` with ``y < x``; sectors and pairs in sorted order."""
        out: list[tuple[str, str, str]] = []
        for sector in sorted(self.sectors):
            members = sorted(self.sectors[sector])
            for i, y in enumerate(members):
                out.extend((y, x, sector) for x in members[i + 1 :])
        return tuple(out)


@dataclass(frozen=True)
class LoadedSpreadCohort:
    cohort: SpreadCohort
    sha256: str
    path: Path


def load_spread_cohort(path: Path) -> LoadedSpreadCohort:
    raw = path.read_bytes()
    return LoadedSpreadCohort(SpreadCohort.model_validate_json(raw), hashlib.sha256(raw).hexdigest(), path)


class BarRange(BaseModel, frozen=True, extra="forbid"):
    start: date
    through: date

    @model_validator(mode="after")
    def _ordered(self) -> BarRange:
        if self.start >= self.through:
            raise ValueError("bars.start must precede bars.through")
        return self


class StageWindows(BaseModel, frozen=True, extra="forbid"):
    discovery: tuple[date, date]
    confirmation: tuple[date, date]

    @model_validator(mode="after")
    def _ordered(self) -> StageWindows:
        for name, (start, end) in (("discovery", self.discovery), ("confirmation", self.confirmation)):
            if start >= end:
                raise ValueError(f"{name} window must be ordered (start < end)")
        if self.discovery[1] >= self.confirmation[0]:
            raise ValueError("confirmation window must start after the discovery window ends")
        return self


class Schedule(BaseModel, frozen=True, extra="forbid"):
    formation_sessions: int = Field(ge=20)
    trading_sessions: int = Field(ge=5)


class FormationRule(BaseModel, frozen=True, extra="forbid"):
    coint_max_lag: int = Field(ge=0)
    p_value: float = Field(gt=0, lt=1)
    half_life_sessions: tuple[float, float]
    hedge_ratio_abs: tuple[float, float]
    top_pairs: int = Field(ge=1)
    min_eligible_names: int = Field(ge=2)

    @model_validator(mode="after")
    def _ranges(self) -> FormationRule:
        if not 0 < self.half_life_sessions[0] < self.half_life_sessions[1]:
            raise ValueError("half_life_sessions must be an ordered positive range")
        if not 0 < self.hedge_ratio_abs[0] < self.hedge_ratio_abs[1]:
            raise ValueError("hedge_ratio_abs must be an ordered positive range")
        return self


class TradingRule(BaseModel, frozen=True, extra="forbid"):
    z_entry: float = Field(gt=0)
    z_exit: float = Field(ge=0)
    z_stop: float = Field(gt=0)
    fill: Literal["next_open"]

    @model_validator(mode="after")
    def _bands(self) -> TradingRule:
        if not self.z_exit < self.z_entry < self.z_stop:
            raise ValueError("bands must satisfy z_exit < z_entry < z_stop")
        return self


class PassRules(BaseModel, frozen=True, extra="forbid"):
    min_closed_trades: int = Field(ge=1)
    min_windows: int = Field(ge=1)
    min_positive_window_fraction: float = Field(gt=0, le=1)
    max_abs_market_beta: float = Field(gt=0)
    trim_fraction: float = Field(ge=0, lt=0.5)


class PowerSpec(BaseModel, frozen=True, extra="forbid"):
    seeds: int = Field(ge=1)
    min_pass: int = Field(ge=1)
    planted_pairs: int = Field(ge=1)
    half_life_sessions: float = Field(gt=0)
    innovation_std: float = Field(gt=0)
    hedge_ratio: tuple[float, float]
    market_vol: float = Field(gt=0)
    sector_vol: float = Field(gt=0)
    idiosyncratic_vol: float = Field(gt=0)
    overnight_vol: float = Field(gt=0)

    @model_validator(mode="after")
    def _bounds(self) -> PowerSpec:
        if self.min_pass > self.seeds:
            raise ValueError("min_pass cannot exceed seeds")
        if not 0 < self.hedge_ratio[0] <= self.hedge_ratio[1]:
            raise ValueError("hedge_ratio must be an ordered positive range")
        return self


class NullSpec(BaseModel, frozen=True, extra="forbid"):
    seeds: int = Field(ge=1)
    max_pass: int = Field(ge=0)
    shift_block_sessions: int = Field(ge=1)

    @model_validator(mode="after")
    def _bounds(self) -> NullSpec:
        if self.max_pass >= self.seeds:
            raise ValueError("max_pass must be below seeds")
        return self


class SpreadProtocol(BaseModel, frozen=True, extra="forbid"):
    """The frozen pairs protocol (lane ``spread``, entry ``spread-pairs``)."""

    id: Literal["spread-pairs"]
    version: int = Field(ge=1)
    title: str = Field(min_length=1)
    references: tuple[str, ...]
    hypotheses: str = Field(min_length=1)
    decision_rule: str = Field(min_length=1)
    cohort: str = Field(min_length=1)
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    bars: BarRange
    windows: StageWindows
    schedule: Schedule
    formation: FormationRule
    trading: TradingRule
    costs_bps_per_side: tuple[float, ...] = Field(min_length=1)
    decision_cost_bps: float = Field(ge=0)
    bootstrap: BootstrapSpec
    pass_rules: PassRules
    confirmation_rules: PassRules
    power: PowerSpec
    null_check: NullSpec

    @model_validator(mode="after")
    def _consistent(self) -> SpreadProtocol:
        if any(c < 0 for c in self.costs_bps_per_side):
            raise ValueError("costs_bps_per_side must be non-negative")
        if self.decision_cost_bps not in self.costs_bps_per_side:
            raise ValueError("decision_cost_bps must be one of costs_bps_per_side")
        if not self.bars.start <= self.windows.discovery[0]:
            raise ValueError("discovery window must start inside the bars range")
        if not self.windows.confirmation[1] <= self.bars.through:
            raise ValueError("confirmation window must end inside the bars range")
        return self

    def stage(self, name: Literal["discovery", "confirmation"]) -> tuple[date, date]:
        return self.windows.discovery if name == "discovery" else self.windows.confirmation

    def rules(self, name: Literal["discovery", "confirmation"]) -> PassRules:
        return self.pass_rules if name == "discovery" else self.confirmation_rules


@dataclass(frozen=True)
class LoadedSpreadProtocol:
    protocol: SpreadProtocol
    sha256: str
    path: Path


def load_spread_protocol(path: Path) -> LoadedSpreadProtocol:
    raw = path.read_bytes()
    return LoadedSpreadProtocol(SpreadProtocol.model_validate_json(raw), hashlib.sha256(raw).hexdigest(), path)
