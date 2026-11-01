"""The frozen ``composite-ic-v1`` file and its validated model (research only).

The file holds the members, signs, citations, source digests, construction constants, target, gate, check
settings and the pins for the sector file and the two cohort protocols. A change is a new version. The
window may not reach the reserve: it ends on or before 2023-12-29, the end of the pooled selection window.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_trader.research.alpha.dsl import compile_expression


__all__ = ["WINDOW_END_LIMIT", "CompositeProtocol", "LoadedComposite", "load_composite"]

WINDOW_END_LIMIT = date(2023, 12, 29)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(_Frozen):
    repository: str
    cluster_labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    factor_details_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Member(_Frozen):
    id: str
    expression: str
    sign: Literal[1, -1]
    citation: str

    @field_validator("expression")
    @classmethod
    def _compiles(cls, value: str) -> str:
        compile_expression(value)  # raises a ValueError subclass the model reports
        return value


class Theme(_Frozen):
    theme: str
    members: tuple[Member, ...] = Field(min_length=1)


class Skipped(_Frozen):
    id: str
    citation: str
    reason: str


class Construction(_Frozen):
    winsorize_z: float = Field(gt=0)
    min_sector_names: int = Field(ge=1)
    min_regression_rows: int = Field(ge=1)
    min_member_fraction: float = Field(gt=0, le=1)


class SectorPin(_Frozen):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    min_mapped_fraction: float = Field(gt=0, le=1)


class ProtocolPin(_Frozen):
    protocol: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Bootstrap(_Frozen):
    draws: int = Field(ge=1)
    seed: int


class Gate(_Frozen):
    min_t: float
    blocks: int = Field(ge=1)
    min_positive_blocks: int = Field(ge=1)

    @model_validator(mode="after")
    def _blocks(self) -> Gate:
        if self.min_positive_blocks > self.blocks:
            raise ValueError("min_positive_blocks exceeds blocks")
        return self


class CIc(_Frozen):
    replicates: int = Field(ge=1)
    max_acceptance: float = Field(ge=0, le=1)
    seed: int
    session_shuffle_replicates: int = Field(ge=1)


class AIc(_Frozen):
    replicates: int = Field(ge=1)
    decisive_lambda: float = Field(gt=0, lt=1)
    min_power: float = Field(gt=0, le=1)
    reported_lambdas: tuple[float, ...]
    ar1_phi: float = Field(gt=0, lt=1)
    seed: int


class Checks(_Frozen):
    c_ic: CIc
    a_ic: AIc


class CompositeProtocol(_Frozen):
    id: Literal["composite-ic"]
    version: int
    rationale: str
    source: Source
    themes: tuple[Theme, ...] = Field(min_length=1)
    skipped: tuple[Skipped, ...]
    construction: Construction
    sectors: SectorPin
    decisive: ProtocolPin
    secondary: ProtocolPin
    window: tuple[date, date]
    horizon: int = Field(ge=1)
    reported_horizons: tuple[int, ...]
    min_names: int = Field(ge=1)
    bootstrap: Bootstrap
    gate: Gate
    checks: Checks

    @model_validator(mode="after")
    def _validate(self) -> CompositeProtocol:
        start, end = self.window
        if start >= end:
            raise ValueError("window start is not before its end")
        if end > WINDOW_END_LIMIT:
            raise ValueError(f"window ends {end.isoformat()}, after the {WINDOW_END_LIMIT.isoformat()} limit")
        if self.horizon not in self.reported_horizons:
            raise ValueError("the decisive horizon is not among the reported horizons")
        ids = [member.id for theme in self.themes for member in theme.members]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate member id")
        return self

    @property
    def members(self) -> tuple[Member, ...]:
        return tuple(member for theme in self.themes for member in theme.members)


@dataclass(frozen=True)
class LoadedComposite:
    protocol: CompositeProtocol
    sha256: str
    path: Path


def load_composite(path: Path) -> LoadedComposite:
    raw = path.read_bytes()
    return LoadedComposite(
        protocol=CompositeProtocol.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path
    )
