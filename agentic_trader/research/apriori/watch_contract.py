"""The insider watch card's frozen contract: the ``insider`` v1 entry, its pooled protocol's cube bracket and
the live execution policy built from it.

Spec: docs/superpowers/specs/2026-10-23-insider-watch-cards-design.md, section 3. The watch card source
(``agent/insider_watch.py``) builds every card's ``alpha_policy`` from this contract, and entry admission
(``storage/workflow.py``) refuses a watch signal whose policy differs from it. Nothing here is re-tuned: the
bracket is the study's (``cube_spec().bracket``, 10:35 decision, 2 x ATR14 stop, 3R target, 20-session time
exit) through the same session lifetime machinery as PEAD (``pead_execution_policy``).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentic_trader.execution.lifetime_policy import DAILY_ENTRY_LIFETIME_SECONDS, SessionLifetimePolicy
from agentic_trader.research.alpha.strategy import AprioriBracketPolicy
from agentic_trader.research.apriori.catalog import InsiderEntry, load_insider_entry
from agentic_trader.research.pooled.campaign import InsiderCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.cube import BracketSpec, CubeSpec
from agentic_trader.research.pooled.entry import REPO_ROOT


__all__ = [
    "INSIDER_WATCH_STRATEGY_ID",
    "WATCH_DECISION_TIME_ET",
    "WATCH_ENTRY",
    "WatchContract",
    "frozen_watch_contract",
    "load_watch_contract",
    "watch_execution_policy",
]

WATCH_ENTRY = Path("config/research/apriori/insider-v1.json")
# Never a catalog alpha id (``<entry>_<leg>``) and never ``alpha_``-prefixed: a watch card is not a
# registry alpha or a probe. Budgets, the open-position cap and admission key on it.
INSIDER_WATCH_STRATEGY_ID = "insider_watch_long"
# The cube bracket's decision time. The source reads its own from the contract; this copy only places
# the "service unavailable" decision when the contract cannot be loaded. A test pins it to the file.
WATCH_DECISION_TIME_ET = "10:35"


@dataclass(frozen=True)
class WatchContract:
    entry: InsiderEntry
    entry_sha256: str
    protocol_sha256: str
    spec: CubeSpec
    policy: dict[str, Any]  # AprioriBracketPolicy.to_dict(): the card's alpha_policy, byte for byte

    @property
    def decision_time_et(self) -> str:
        return self.spec.bracket.decision_time_et

    @property
    def holding_sessions(self) -> int:
        return self.spec.bracket.max_hold_sessions


def watch_execution_policy(bracket: BracketSpec) -> AprioriBracketPolicy:
    """The cube bracket as a live policy: one-session resting entry, a 15:45 New York time exit."""
    return AprioriBracketPolicy(
        stop_atr=bracket.stop_atr_multiple,
        atr_window=bracket.atr_window,
        reward_risk=bracket.target_r,
        lifetime=SessionLifetimePolicy(
            resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS,
            holding_sessions=bracket.max_hold_sessions,
            close_time_et="15:45",
        ),
    )


def load_watch_contract(repo_root: Path) -> WatchContract:
    """Load the frozen entry and its pooled protocol (digest-checked) and build the watch policy."""
    loaded = load_insider_entry(repo_root / WATCH_ENTRY)
    entry = loaded.entry
    protocol_file = load_campaign_protocol(repo_root / entry.pooled_protocol)
    if protocol_file.sha256 != entry.pooled_protocol_sha256:
        raise ValueError(f"{entry.pooled_protocol} does not match the insider entry's pooled_protocol_sha256")
    protocol = protocol_file.protocol
    if not isinstance(protocol, InsiderCampaignProtocol):
        raise ValueError(f"{entry.pooled_protocol} is a {protocol.id} protocol, not an insider protocol")  # noqa: TRY004
    spec = protocol.cube_spec()
    entry.check_against(spec)
    return WatchContract(
        entry=entry,
        entry_sha256=loaded.sha256,
        protocol_sha256=protocol_file.sha256,
        spec=spec,
        policy=watch_execution_policy(spec.bracket).to_dict(),
    )


@lru_cache(maxsize=1)
def frozen_watch_contract() -> WatchContract:
    """The repository's contract, read once per process (admission compares every watch signal to it)."""
    return load_watch_contract(REPO_ROOT)
