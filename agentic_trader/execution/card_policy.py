"""The operator's measured-evidence send policy for native suggestion cards (docs/card-evidence.md).

Pure: ``decide`` never reads the journal, the book or the broker. It is a switch on measured
evidence, not a risk rule: insufficient, stale, unavailable or partial (any fetch failure in the
key) evidence never withholds. ``run_scan`` does not call it for a policy-locked candidate (a
versioned alpha or a paper probe), whose ``card_policy`` block is all null.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agentic_trader.config import CardPolicyConfig
from agentic_trader.execution.card_evidence import CardEvidence


__all__ = ["CardPolicyDecision", "decide", "journal_block"]


class CardPolicyDecision(BaseModel, frozen=True):
    withhold: bool
    would_withhold: bool
    reason: str | None
    measured_ev: float | None
    n_mature: int | None
    n_fetch_failed: int


def _reason(measured_ev: float, n_mature: int, threshold: float) -> str:
    """Two decimals, or three when two would print the EV and the threshold as equal (-0.004 vs 0)."""
    places = 3 if round(measured_ev, 2) == round(threshold, 2) else 2
    return f"card policy: measured EV {measured_ev:+.{places}f}R over {n_mature} < {threshold:+.{places}f}R"


def decide(policy: CardPolicyConfig, evidence: CardEvidence) -> CardPolicyDecision:
    """``would_withhold`` on complete measured evidence over the sample floor below the threshold.

    ``withhold`` only under ``enforce``. A key with any fetch failure is a partial record: it is
    recorded (``n_fetch_failed``) but never withheld.
    """
    measured = evidence.status == "measured"
    measured_ev = evidence.mean_r_cost if measured else None
    n_mature = evidence.n_mature if measured else None
    threshold = policy.min_measured_ev
    would = (
        policy.mode != "off"
        and threshold is not None
        and measured_ev is not None
        and n_mature is not None
        and n_mature >= policy.min_mature_cards
        and evidence.n_fetch_failed == 0
        and measured_ev < threshold
    )
    reason = (
        _reason(measured_ev, n_mature, threshold)
        if would and measured_ev is not None and n_mature is not None and threshold is not None
        else None
    )
    return CardPolicyDecision(
        withhold=would and policy.mode == "enforce",
        would_withhold=would,
        reason=reason,
        measured_ev=measured_ev,
        n_mature=n_mature,
        n_fetch_failed=evidence.n_fetch_failed,
    )


def journal_block(decision: CardPolicyDecision | None, mode: str) -> dict[str, Any]:
    """The per-candidate ``card_policy`` block (journal and provenance); ``would_withhold`` is None while off.

    Without a decision (a policy-locked candidate, which the policy does not apply to) every
    field is null.
    """
    if decision is None:
        return {"measured_ev": None, "n_mature": None, "would_withhold": None, "n_fetch_failed": None}
    return {
        "measured_ev": decision.measured_ev,
        "n_mature": decision.n_mature,
        "would_withhold": None if mode == "off" else decision.would_withhold,
        "n_fetch_failed": decision.n_fetch_failed,
    }
