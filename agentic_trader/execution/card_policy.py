"""The operator's measured-evidence send policy for native suggestion cards (docs/card-evidence.md).

Pure: ``decide`` never reads the journal, the book or the broker. It is a switch on measured
evidence, not a risk rule: insufficient, stale or unavailable evidence never withholds.
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


def decide(policy: CardPolicyConfig, evidence: CardEvidence) -> CardPolicyDecision:
    """``would_withhold`` on measured evidence over the sample floor below the threshold; ``withhold`` only under ``enforce``."""
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
        and measured_ev < threshold
    )
    reason = (
        f"card policy: measured EV {measured_ev:+.2f}R over {n_mature} < {threshold:+.2f}R"
        if would and measured_ev is not None and threshold is not None
        else None
    )
    return CardPolicyDecision(
        withhold=would and policy.mode == "enforce",
        would_withhold=would,
        reason=reason,
        measured_ev=measured_ev,
        n_mature=n_mature,
    )


def journal_block(decision: CardPolicyDecision | None, mode: str) -> dict[str, Any] | None:
    """The per-candidate ``card_policy`` block (journal and provenance); ``would_withhold`` is None while off."""
    if decision is None:
        return None
    return {
        "measured_ev": decision.measured_ev,
        "n_mature": decision.n_mature,
        "would_withhold": None if mode == "off" else decision.would_withhold,
    }
