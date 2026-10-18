"""Predeclared bracket counterfactual (B1-B5) over the setup-outcome replay frame.

See ``docs/superpowers/specs/2026-10-18-bracket-counterfactual-design.md``. Five exit arms
(the live bracket plus four counterfactuals) are compared on the same replayed native setups,
paired by setup. This module holds the frozen protocol model; statistics and the executor
follow in later tasks. Research only: no promotion, shadow or registry credit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, model_validator

from agentic_trader.research.setups.labels import LIVE_ARM, BracketArm, TrailSpec


__all__ = [
    "HYPOTHESES",
    "LIVE_ARM_NAME",
    "ArmSpec",
    "BracketCounterfactualProtocol",
]

HYPOTHESES: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5")
LIVE_ARM_NAME = "A0_live"

_MIN_MATURATION_DAYS = 30
_MIN_EMBARGO_DAYS = 28
_TRAIL_KEYS = {"trigger_r", "distance_r"}


class ArmSpec(BaseModel, frozen=True, extra="forbid"):
    """One exit arm as declared in the protocol file."""

    name: str
    target_r: float | Literal["planned"] | None
    trail: dict | None

    def to_arm(self) -> BracketArm:
        if self.target_r == "planned":
            return LIVE_ARM
        trail = TrailSpec(**self.trail) if self.trail is not None else None
        return BracketArm(self.name, self.target_r, trail)


class BracketCounterfactualProtocol(BaseModel, frozen=True, extra="forbid"):
    """A predeclared, hash-identified bracket-counterfactual protocol (spec section 7).

    ``hypotheses`` and ``decision_rule`` are recorded verbatim for the human record."""

    version: Literal["bracket_counterfactual_v1"]
    window: tuple[date, date]
    data_cutoff: date
    robustness_window: tuple[date, date]
    robustness_data_cutoff: date
    confirmation_window: tuple[date, date]
    confirmation_data_cutoff: date
    confirmation_prior_use: str
    embargo_sessions: int
    scan_times_et: tuple[str, ...]
    max_hold_sessions: int
    cost_bps_per_side: tuple[float, ...]
    arms: tuple[ArmSpec, ...]
    r_cap: float
    bootstrap: dict
    holm_alpha: float
    min_setups: int
    min_sessions: int
    min_mature_fraction: float
    min_divergent: int
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    strategy_config: dict
    sector_etf: dict[str, str]
    hypotheses: str
    decision_rule: str

    @model_validator(mode="after")
    def _check(self) -> BracketCounterfactualProtocol:
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be at least 1")
        if not self.cost_bps_per_side:
            raise ValueError("cost_bps_per_side must be non-empty")
        if self.r_cap <= 0:
            raise ValueError("r_cap must be positive")
        if not 0 < self.holm_alpha < 0.5:
            raise ValueError("holm_alpha must be in (0, 0.5)")
        for name, (start, end), cutoff in (
            ("window", self.window, self.data_cutoff),
            ("robustness_window", self.robustness_window, self.robustness_data_cutoff),
            ("confirmation_window", self.confirmation_window, self.confirmation_data_cutoff),
        ):
            if start >= end:
                raise ValueError(f"{name} must be non-empty and ordered (start < end)")
            required = end + timedelta(days=_MIN_MATURATION_DAYS)
            if cutoff < required:
                raise ValueError(
                    f"data_cutoff for {name} must be at least {_MIN_MATURATION_DAYS} days after its end "
                    f"(need >= {required.isoformat()})"
                )
        if self.robustness_window[1] + timedelta(days=_MIN_MATURATION_DAYS) > self.window[0]:
            raise ValueError("robustness_window must end more than one label horizon (30 days) before window starts")
        if self.confirmation_window[0] < self.window[1] + timedelta(days=_MIN_EMBARGO_DAYS):
            raise ValueError(
                f"confirmation_window must start at least {_MIN_EMBARGO_DAYS} calendar days (the embargo) after the window ends"
            )
        names = [arm.name for arm in self.arms]
        if len(set(names)) != len(names):
            raise ValueError("arm names must be unique")
        first = self.arms[0] if self.arms else None
        if (
            first is None
            or first.name != LIVE_ARM_NAME
            or first.target_r != "planned"
            or first.trail is not None
            or names.count(LIVE_ARM_NAME) != 1
        ):
            raise ValueError("arms must start with exactly one A0_live arm with target_r 'planned' and no trail")
        for arm in self.arms[1:]:
            if arm.target_r == "planned":
                raise ValueError("'planned' target_r is valid only on A0_live")
        for arm in self.arms:
            if arm.trail is None:
                continue
            if set(arm.trail) != _TRAIL_KEYS or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in arm.trail.values()
            ):
                raise ValueError(f"arm {arm.name}: trail needs exactly positive trigger_r and distance_r")
        return self

    def document(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def bracket_arms(self) -> tuple[BracketArm, ...]:
        return tuple(arm.to_arm() for arm in self.arms)

    def counterfactual_arm_names(self) -> tuple[str, ...]:
        return tuple(arm.name for arm in self.arms[1:])
