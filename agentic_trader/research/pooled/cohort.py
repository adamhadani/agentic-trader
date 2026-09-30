"""The pooled lane's cohort: a frozen symbol list under ``config/research/pooled/``.

The cohort is today's membership, not a point-in-time universe: the configured
scan-universe equity groups plus a prospective equity snapshot. Its identity is the
SHA-256 of the file's bytes, recorded in every manifest; a changed file is a new version.
Point-in-time eligibility per session is computed separately from bars
(``point_in_time_eligibility``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


__all__ = ["SUPPORTED_SYMBOL", "Cohort", "CohortSource", "LoadedCohort", "load_cohort"]

SUPPORTED_SYMBOL = re.compile(r"^[A-Z]{1,5}$")


class CohortSource(BaseModel, frozen=True, extra="forbid"):
    kind: Literal["config_groups", "equity_snapshot"]
    description: str
    # config groups: SHA-256 of the newline-joined sorted symbols; snapshot: its snapshot_id.
    identity: str = Field(min_length=1)
    symbols: tuple[str, ...]


class Cohort(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-cohort"]
    version: int = Field(ge=1)
    survivorship: str
    sources: tuple[CohortSource, ...] = Field(min_length=1)
    excluded: dict[str, str]  # symbol -> reason (e.g. unsupported symbol form)
    symbols: tuple[str, ...] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def _sorted_unique_supported(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if list(value) != sorted(set(value)):
            raise ValueError("symbols must be sorted and unique")
        unsupported = [s for s in value if not SUPPORTED_SYMBOL.fullmatch(s)]
        if unsupported:
            raise ValueError(f"unsupported symbols: {unsupported[:5]}")
        return value

    @model_validator(mode="after")
    def _union_of_sources(self) -> Cohort:
        union = sorted({s for source in self.sources for s in source.symbols if SUPPORTED_SYMBOL.fullmatch(s)})
        if list(self.symbols) != union:
            raise ValueError("symbols must be the sorted union of the sources' supported symbols")
        return self

    def source_of(self, symbol: str) -> tuple[str, ...]:
        return tuple(source.kind for source in self.sources if symbol in source.symbols)


@dataclass(frozen=True)
class LoadedCohort:
    cohort: Cohort
    sha256: str
    path: Path


def load_cohort(path: Path) -> LoadedCohort:
    raw = path.read_bytes()
    return LoadedCohort(cohort=Cohort.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
