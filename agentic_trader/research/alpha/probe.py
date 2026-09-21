"""Paper-probe (incubation) policy: pure assessment over stored qualification evidence.

A probe is a time-boxed, risk-capped paper-account trial. It is not qualification:
it reads already-recorded criterion observations and never touches price data, so
enrolment consumes no holdout and charges no research trial.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from numbers import Real
from typing import Any, TypeGuard


PROBE_POLICY_VERSION = "probe_policy_v1"
PAPER_PROBE_TAG = "paper_probe"
PAPER_SCOPE_SUFFIX = "/alpaca:paper"

# Structural contracts shared with promotion; each must have status "pass".
STRUCTURAL_CRITERIA = (
    "holdout_available",
    "deployment_data_contract",
    "intraday_session_execution_unverified",
    "recursive_feature_requires_shared_initialization",
)


@dataclass(frozen=True)
class ProbePolicy:
    version: str = PROBE_POLICY_VERSION
    min_holdout_sharpe: float = 0.0
    min_cost_stressed_return_pct: float = 0.0
    min_holdout_trades: int = 5
    max_term_days: int = 180
    kill_r: float = -4.0


@dataclass(frozen=True)
class ProbeAssessment:
    eligible: bool
    reasons: tuple[str, ...]
    observations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "reasons": list(self.reasons), "observations": dict(self.observations)}


def is_paper_scope(scope: str) -> bool:
    """Only the brokerage paper account; the local simulator scope is ``…/paper``."""
    return scope.endswith(PAPER_SCOPE_SUFFIX)


def _finite(value: Any) -> TypeGuard[float]:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def assess_probe(qualification: dict | None, policy: ProbePolicy | None = None) -> ProbeAssessment:
    policy = policy or ProbePolicy()
    if not qualification:
        return ProbeAssessment(False, ("qualification_missing",))
    criteria = qualification.get("criteria") or {}
    reasons: list[str] = []
    observations: dict[str, Any] = {}
    for code in STRUCTURAL_CRITERIA:
        criterion = criteria.get(code)
        if criterion is None:
            reasons.append(f"{code}_missing")
        elif criterion.get("status") != "pass":
            reasons.append(f"{code}_failed")
    floors = (
        ("holdout_sharpe", policy.min_holdout_sharpe, True),
        ("cost_stress", policy.min_cost_stressed_return_pct, True),
        ("holdout_trade_count", policy.min_holdout_trades, False),
    )
    for code, floor, strict in floors:
        criterion = criteria.get(code)
        if criterion is None:
            reasons.append(f"{code}_missing")
            continue
        if criterion.get("status") == "unavailable":
            reasons.append(f"{code}_unavailable")
            continue
        value = criterion.get("value")
        if not _finite(value):
            reasons.append(f"{code}_invalid")
            continue
        observations[code] = value
        if value <= floor if strict else value < floor:
            reasons.append(f"{code}_below_probe_floor")
    return ProbeAssessment(not reasons, tuple(reasons), observations)


def forward_record(
    rows: Iterable[tuple[float | None, float | None]], policy: ProbePolicy | None = None
) -> dict[str, Any]:
    """Cumulative realized R from reconciled closes; unknown outcomes never kill or protect."""
    policy = policy or ProbePolicy()
    trades, unknown, cumulative = 0, 0, 0.0
    for realized_pnl, risk_dollars in rows:
        if not _finite(realized_pnl) or not _finite(risk_dollars) or risk_dollars <= 0:
            unknown += 1
            continue
        trades += 1
        cumulative += realized_pnl / risk_dollars
    return {
        "trades": trades,
        "unknown": unknown,
        "cumulative_r": cumulative,
        "kill_r": policy.kill_r,
        "killed": trades > 0 and cumulative <= policy.kill_r,
    }


def policy_document(policy: ProbePolicy | None = None) -> dict[str, Any]:
    return asdict(policy or ProbePolicy())
