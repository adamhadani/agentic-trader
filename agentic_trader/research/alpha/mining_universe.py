"""Deterministic symbol selection for bounded alpha-mining campaigns.

The scheduled ETF benchmark and a prospective equity snapshot are distinct
research cohorts.  This module keeps their selection and provenance rules out
of the CLI so every mining entry point records the same universe identity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic_trader.research.alpha.equity_universe import MAX_CANDIDATES, candidate_symbols
from agentic_trader.research.alpha.universe import ETF_RESEARCH_UNIVERSE


DEFAULT_SYMBOL = "SPY"
MAX_MINING_SYMBOLS = MAX_CANDIDATES


@dataclass(frozen=True)
class MiningUniverse:
    """A bounded, ordered cohort with immutable source identity."""

    name: str
    symbols: tuple[str, ...]
    version: str

    def __post_init__(self):
        if not self.name.strip() or not self.version.strip():
            raise ValueError("Mining universe requires a name and version")
        if not 1 <= len(self.symbols) <= MAX_MINING_SYMBOLS:
            raise ValueError("Mining universe must contain one to 500 symbols")
        if any(not symbol or symbol != symbol.upper() for symbol in self.symbols):
            raise ValueError("Mining symbols must be nonempty uppercase identifiers")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Mining universe symbols must be unique")


def _explicit_symbols(symbol: str, symbols: str) -> tuple[str, ...]:
    values = symbols or symbol
    result = tuple(sorted({part.strip().upper() for part in values.split(",") if part.strip()}))
    if not result:
        raise ValueError("At least one research symbol is required")
    return result


def _load_snapshot(path: Path, *, observed_at: datetime) -> tuple[tuple[str, ...], str]:
    try:
        snapshot = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unable to read universe snapshot: {path}") from exc
    try:
        symbols = candidate_symbols(snapshot, at=observed_at)
        snapshot_id = snapshot["snapshot_id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Universe snapshot failed its immutable identity/prospective checks") from exc
    return symbols, str(snapshot_id)


def resolve_mining_universe(
    *,
    universe: str,
    symbol: str = DEFAULT_SYMBOL,
    symbols: str = "",
    snapshot_path: Path | None = None,
    max_symbols: int | None = None,
    observed_at: datetime | None = None,
) -> MiningUniverse:
    """Resolve one explicit, ETF, or prospective-snapshot mining cohort.

    A snapshot is a current/prospective membership observation.  Its symbols
    may be used to widen discovery, but the snapshot identity is retained and
    does not make historical bars survivorship-free evidence.
    """

    if universe not in {"explicit", "etf32", "snapshot"}:
        raise ValueError("Unknown mining universe")
    if max_symbols is not None and not 1 <= max_symbols <= MAX_MINING_SYMBOLS:
        raise ValueError("max_symbols must be between one and 500")
    if universe == "snapshot":
        if snapshot_path is None:
            raise ValueError("--universe snapshot requires --universe-file")
        if symbols or symbol != DEFAULT_SYMBOL:
            raise ValueError("Snapshot mining cannot be combined with --symbol or --symbols")
        selected, snapshot_id = _load_snapshot(snapshot_path, observed_at=observed_at or datetime.now(UTC))
        selected = selected if max_symbols is None else selected[:max_symbols]
        return MiningUniverse("prospective_equity_snapshot", selected, f"snapshot:{snapshot_id}:cap:{len(selected)}")
    if snapshot_path is not None:
        raise ValueError("--universe-file is only valid with --universe snapshot")
    if max_symbols is not None and universe == "etf32" and max_symbols > len(ETF_RESEARCH_UNIVERSE.symbols):
        # A larger cap cannot add symbols to a fixed benchmark and usually
        # indicates that the operator meant to select a snapshot instead.
        raise ValueError("max_symbols exceeds the fixed etf32 cohort")
    selected = ETF_RESEARCH_UNIVERSE.symbols if universe == "etf32" else _explicit_symbols(symbol, symbols)
    selected = selected if max_symbols is None else selected[:max_symbols]
    version = ETF_RESEARCH_UNIVERSE.version_id if universe == "etf32" else "explicit:" + ",".join(selected)
    return MiningUniverse(universe, selected, version)
