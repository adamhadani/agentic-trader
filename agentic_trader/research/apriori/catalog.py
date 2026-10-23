"""Catalog entries (``pead`` and ``exdiv``): one frozen JSON file per a priori alpha under ``config/research/apriori/``.

An entry is committed before any data is read. Its identity is the SHA-256 of the file's
bytes, recorded in every run manifest; a changed file is a new version (``pead-v2``) with a
new, non-overlapping window, never an edit. Entries grant no registry, trial, shadow or
promotion credit. ``hypotheses`` and ``decision_rule`` are free text kept verbatim for the
human record; the computation lives in ``pead_study``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.execution.lifetime_policy import DAILY_ENTRY_LIFETIME_SECONDS, SessionLifetimePolicy
from agentic_trader.research.alpha.strategy import AprioriBracketPolicy


if TYPE_CHECKING:
    # cube imports apriori modules that import this one; only the annotation needs it.
    from agentic_trader.research.pooled.cube import CubeSpec


__all__ = [
    "ExdivEntry",
    "ExdivEvent",
    "ExdivTrade",
    "InsiderControl",
    "InsiderEntry",
    "InsiderEvent",
    "LoadedEntry",
    "LoadedExdivEntry",
    "LoadedInsiderEntry",
    "PeadEntry",
    "catalog_entry_id",
    "load_exdiv_entry",
    "load_insider_entry",
    "load_pead_entry",
    "pead_execution_policy",
]

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
    # The gate reads unadjusted bars, as a live scan saw prices at the time; adjusted
    # history divides by later splits, which would make the price floor lookahead.
    liquidity_adjustment: Literal["raw"]


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
    # Accepted pages with zero rows on observed trading sessions, per calendar year.
    max_empty_session_fraction_per_year: float = Field(ge=0, lt=1)
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


def pead_execution_policy(entry: PeadEntry) -> AprioriBracketPolicy:
    """The live bracket of a PEAD leg: the study's trade block, one-session entry, 15:45 time exit."""
    return AprioriBracketPolicy(
        stop_atr=entry.trade.stop_atr_multiple,
        atr_window=entry.trade.atr_window,
        reward_risk=entry.trade.target_r,
        lifetime=SessionLifetimePolicy(
            resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS,
            holding_sessions=entry.trade.max_hold_sessions,
            close_time_et="15:45",
        ),
    )


class ExdivEvent(BaseModel, frozen=True, extra="forbid"):
    min_gap_days: int = Field(ge=1)
    max_gap_days: int = Field(ge=1)
    exclude_special: Literal[True]
    structural_exclusion_days: int = Field(ge=0)

    @model_validator(mode="after")
    def _gap_ordered(self) -> ExdivEvent:
        if self.min_gap_days > self.max_gap_days:
            raise ValueError("gap band must be ordered (min_gap_days <= max_gap_days)")
        return self


class ExdivTrade(BaseModel, frozen=True, extra="forbid"):
    decision_time_et: str
    entry_lead_sessions: int = Field(ge=1)
    control_offset_sessions: int = Field(ge=1)
    stop_atr_multiple: float = Field(gt=0)
    atr_window: int = Field(ge=2)
    target_r: float = Field(gt=0)
    secondary_hold_sessions: int = Field(ge=1)

    @field_validator("decision_time_et")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("decision_time_et must be HH:MM")
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _holds_ordered(self) -> ExdivTrade:
        if self.secondary_hold_sessions <= self.entry_lead_sessions:
            raise ValueError("secondary_hold_sessions must exceed the hold (entry_lead_sessions)")
        if self.control_offset_sessions <= self.entry_lead_sessions + 1:
            raise ValueError("control_offset_sessions must start after the ex-date (entry_lead_sessions + 1)")
        return self

    @property
    def max_hold_sessions(self) -> int:
        """The hold is the lead: the fill session is session 1, the exit is session ``entry_lead_sessions``."""
        return self.entry_lead_sessions

    def decision_time(self) -> time:
        return time.fromisoformat(self.decision_time_et)


class ExdivEntry(BaseModel, frozen=True, extra="forbid"):
    """The frozen pre-ex-dividend run-up protocol (catalog entry ``exdiv``)."""

    id: Literal["exdiv"]
    version: int = Field(ge=1)
    title: str
    references: tuple[str, ...]
    hypotheses: str
    decision_rule: str
    window: PeadWindow
    actions_window: tuple[date, date]
    cohort: str = Field(min_length=1)
    # The cohort is pinned by content: a run whose cohort file has another digest fails closed.
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    event: ExdivEvent
    universe: PeadUniverse
    trade: ExdivTrade
    costs_bps_per_side: tuple[float, ...] = Field(min_length=1)
    decision_cost_bps: float
    bootstrap: BootstrapSpec
    pass_rule: PassRule

    @model_validator(mode="after")
    def _consistent(self) -> ExdivEntry:
        if self.decision_cost_bps not in self.costs_bps_per_side:
            raise ValueError("decision_cost_bps must be one of costs_bps_per_side")
        start, end = self.actions_window
        if start >= self.window.decisions[0]:
            raise ValueError("actions_window must start before the first decision (the previous ex-date is needed)")
        if end < self.window.bars_through:
            raise ValueError("actions_window must end at or after bars_through")
        return self


@dataclass(frozen=True)
class LoadedExdivEntry:
    entry: ExdivEntry
    sha256: str
    path: Path


def load_exdiv_entry(path: Path) -> LoadedExdivEntry:
    raw = path.read_bytes()
    return LoadedExdivEntry(
        entry=ExdivEntry.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path
    )


class InsiderEvent(BaseModel, frozen=True, extra="forbid"):
    cluster_window_sessions: int = Field(ge=1)
    min_buyers: int = Field(ge=2)
    cooldown_sessions: int = Field(ge=1)
    routine_rule: Literal["same_owner_same_month_prior_year"]


class InsiderControl(BaseModel, frozen=True, extra="forbid"):
    matched_controls: int = Field(ge=1)
    match_return_sessions: int = Field(ge=1)
    liquidity_buckets: int = Field(ge=2)
    exclusion_sessions: int = Field(ge=1)


class InsiderEntry(BaseModel, frozen=True, extra="forbid"):
    """The frozen opportunistic insider-cluster purchase protocol (catalog entry ``insider``)."""

    id: Literal["insider"]
    version: int = Field(ge=1)
    title: str
    references: tuple[str, ...]
    hypotheses: str
    decision_rule: str
    window: PeadWindow
    pooled_protocol: str = Field(min_length=1)
    pooled_protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cube_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event: InsiderEvent
    control: InsiderControl
    descriptive_hold_sessions: int = Field(ge=1)
    bootstrap: BootstrapSpec
    pass_rule: PassRule

    def check_against(self, spec: CubeSpec) -> None:
        """Refuse a cube whose bracket outlives the cooldown or whose decisions do not cover the window."""
        if self.event.cooldown_sessions <= spec.bracket.max_hold_sessions:
            raise ValueError("cooldown_sessions must exceed the cube bracket's max_hold_sessions")
        start, end = self.window.decisions
        if start < spec.decisions[0] or end > spec.decisions[1]:
            raise ValueError("window.decisions must lie inside the cube spec's decisions")


@dataclass(frozen=True)
class LoadedInsiderEntry:
    entry: InsiderEntry
    sha256: str
    path: Path


def load_insider_entry(path: Path) -> LoadedInsiderEntry:
    raw = path.read_bytes()
    return LoadedInsiderEntry(
        entry=InsiderEntry.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path
    )


def catalog_entry_id(path: Path) -> str:
    """The ``id`` of a catalog entry file, read before choosing which model validates it."""
    document = json.loads(path.read_bytes())
    value = document.get("id") if isinstance(document, Mapping) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Catalog entry {path} has no string 'id'")
    return value
