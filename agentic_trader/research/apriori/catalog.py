"""Catalog entries: one frozen JSON file per a priori alpha under ``config/research/apriori/``.

An entry is committed before any data is read. Its identity is the SHA-256 of the file's
bytes, recorded in every run manifest; a changed file is a new version (``pead-v2``) with a
new, non-overlapping window, never an edit. Entries grant no registry, trial, shadow or
promotion credit. ``hypotheses`` and ``decision_rule`` are free text kept verbatim for the
human record; the computation lives in ``pead_study``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


__all__ = ["LoadedEntry", "PeadEntry", "load_pead_entry"]

# Every labelled decision needs its full holding window of bars before the data cutoff.
_MIN_MATURATION_DAYS = 30


class PeadWindow(BaseModel, frozen=True, extra="forbid"):
    decisions: tuple[date, date]
    bars_through: date
    recent_from: date

    @model_validator(mode="after")
    def _ordered(self) -> PeadWindow:
        start, end = self.decisions
        if start >= end:
            raise ValueError("decisions must be ordered (start < end)")
        if not start < self.recent_from <= end:
            raise ValueError("recent_from must fall inside the decision window")
        if self.bars_through < end + timedelta(days=_MIN_MATURATION_DAYS):
            raise ValueError(f"bars_through must be at least {_MIN_MATURATION_DAYS} days after the last decision")
        return self


class PeadEvent(BaseModel, frozen=True, extra="forbid"):
    min_abs_forecast: float = Field(gt=0)
    min_estimates: int = Field(ge=1)
    surprise_pct: float = Field(gt=0)
    reaction_sigma: float = Field(gt=0)
    vol_window: int = Field(ge=2)
    benchmark: str = Field(min_length=1)


class PeadUniverse(BaseModel, frozen=True, extra="forbid"):
    min_price: float = Field(gt=0)
    # The live liquidity rule (`median_dollar_volume`) is fixed at 20 sessions; the study reuses it.
    dollar_volume_window: Literal[20]
    static_percentile: float = Field(gt=0, lt=1)


class PeadTrade(BaseModel, frozen=True, extra="forbid"):
    decision_time_et: str
    entry_session_offset: Literal[2]
    stop_atr_multiple: float = Field(gt=0)
    atr_window: int = Field(ge=2)
    target_r: float = Field(gt=0)
    max_hold_sessions: int = Field(ge=1)
    secondary_hold_sessions: int = Field(ge=1)

    @field_validator("decision_time_et")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("decision_time_et must be HH:MM")
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _secondary_longer(self) -> PeadTrade:
        if self.secondary_hold_sessions <= self.max_hold_sessions:
            raise ValueError("secondary_hold_sessions must exceed max_hold_sessions")
        return self

    def decision_time(self) -> time:
        return time.fromisoformat(self.decision_time_et)


class BootstrapSpec(BaseModel, frozen=True, extra="forbid"):
    block_mean: int = Field(ge=1)
    draws: int = Field(ge=100)
    seed: int


class PassRule(BaseModel, frozen=True, extra="forbid"):
    min_events: int = Field(ge=1)
    min_recent_events: int = Field(ge=1)
    trim_fraction: float = Field(ge=0, lt=0.5)


class PeadEntry(BaseModel, frozen=True, extra="forbid"):
    """The frozen PEAD protocol (catalog entry ``pead``)."""

    id: Literal["pead"]
    version: int = Field(ge=1)
    title: str
    references: tuple[str, ...]
    hypotheses: str
    decision_rule: str
    window: PeadWindow
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    event: PeadEvent
    universe: PeadUniverse
    trade: PeadTrade
    costs_bps_per_side: tuple[float, ...] = Field(min_length=1)
    decision_cost_bps: float
    bootstrap: BootstrapSpec
    pass_rule: PassRule
    max_failed_calendar_fraction: float = Field(ge=0, lt=1)
    calendar_request_interval_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def _decision_cost_listed(self) -> PeadEntry:
        if self.decision_cost_bps not in self.costs_bps_per_side:
            raise ValueError("decision_cost_bps must be one of costs_bps_per_side")
        return self


@dataclass(frozen=True)
class LoadedEntry:
    entry: PeadEntry
    sha256: str
    path: Path


def load_pead_entry(path: Path) -> LoadedEntry:
    raw = path.read_bytes()
    return LoadedEntry(entry=PeadEntry.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
