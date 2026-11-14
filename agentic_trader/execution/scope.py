"""Which signals are non-native: everything but a native strategy card.

A non-native signal is a catalog or watch strategy (a PEAD drift card, an insider watch card), a paper probe or an
alpha-versioned or alpha-policed card. This is the predicate the release exit's exempt scopes name
(``TradingCopilot._release_exit_scope`` is its source of truth, with one branch per scope), kept here as a pure function
of a signal's stored fields so storage admission and the scan can share it without importing the agent. A test pins
the two together over every scope.

``portfolio.max_non_native_positions`` (operator decision O3) is a capacity/admission rule beside the per-source
``max_open_*`` caps, not an entry-risk rule: it never reads prices, stops or the book's exposure, so it does not belong
in ``agentic_trader/risk``.
"""

from collections.abc import Mapping
from typing import Any

from agentic_trader.research.alpha.probe import PAPER_PROBE_TAG
from agentic_trader.research.apriori.probe import CATALOG_STRATEGY_IDS
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID


__all__ = ["NON_NATIVE_STRATEGY_IDS", "is_non_native"]

# Every strategy id a catalog or watch source signs a card with.
NON_NATIVE_STRATEGY_IDS = CATALOG_STRATEGY_IDS | {INSIDER_WATCH_STRATEGY_ID}


def is_non_native(
    *, strategy: str | None, alpha_version: str | None, alpha_policy: Any, provenance: Mapping[str, Any] | None
) -> bool:
    """Whether a signal's stored fields make it a catalog, watch, probe or alpha card (never raises)."""
    if strategy in NON_NATIVE_STRATEGY_IDS:
        return True
    if isinstance(provenance, Mapping) and provenance.get(PAPER_PROBE_TAG):
        return True
    return bool(alpha_policy or alpha_version)
