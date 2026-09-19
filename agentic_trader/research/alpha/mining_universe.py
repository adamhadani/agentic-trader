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
from uuid import UUID

from agentic_trader.research.alpha.equity_universe import MAX_CANDIDATES, candidate_symbols, document_hash
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


def _load_liquidity_screen(path: Path) -> tuple[tuple[str, ...], str]:
    """Load only a completed, source-qualified liquidity selection.

    The screen result is a research cohort identity, not a new eligibility
    decision.  Requiring its immutable result fields keeps mining provenance
    tied to the full UUID-based screen instead of silently accepting a hand
    edited symbol list.
    """
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unable to read liquidity screen: {path}") from exc
    selected = result.get("selected")
    if (
        result.get("version") != "equity_liquidity_screen_v1"
        or result.get("status") != "completed"
        or result.get("selection_available") is not True
        or result.get("authorizes_promotion") is not False
        or result.get("point_in_time_historical_membership") is not False
        or result.get("common_stock_classification") is not False
        or result.get("market_capacity_estimate") is not False
        or not isinstance(selected, list)
        or not 1 <= len(selected) <= MAX_MINING_SYMBOLS
        or result.get("selected_count") != len(selected)
    ):
        raise ValueError("Liquidity screen is incomplete or violates its research-only contract")
    try:
        symbols = tuple(row["symbol"] for row in selected)
        asset_ids = tuple(row["asset_id"] for row in selected)
    except (KeyError, TypeError) as exc:
        raise ValueError("Liquidity screen selection is malformed") from exc
    try:
        valid_asset_ids = all(str(UUID(asset_id)) == asset_id for asset_id in asset_ids)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Liquidity screen selection must contain unique uppercase symbols and asset identities"
        ) from exc
    if (
        len(set(symbols)) != len(symbols)
        or len(set(asset_ids)) != len(asset_ids)
        or any(not symbol or symbol != symbol.upper() for symbol in symbols)
        or not valid_asset_ids
    ):
        raise ValueError("Liquidity screen selection must contain unique uppercase symbols and asset identities")
    # The screen itself is already deterministically ranked. Preserve that
    # order when a cap is requested; re-sorting would change the frozen cohort.
    return symbols, document_hash(result)


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

    if universe not in {"explicit", "etf32", "snapshot", "screen"}:
        raise ValueError("Unknown mining universe")
    if max_symbols is not None and not 1 <= max_symbols <= MAX_MINING_SYMBOLS:
        raise ValueError("max_symbols must be between one and 500")
    if universe in {"snapshot", "screen"}:
        if snapshot_path is None:
            raise ValueError(f"--universe {universe} requires --universe-file")
        if symbols or symbol != DEFAULT_SYMBOL:
            raise ValueError(f"{universe.title()} mining cannot be combined with --symbol or --symbols")
        if universe == "snapshot":
            selected, source_id = _load_snapshot(snapshot_path, observed_at=observed_at or datetime.now(UTC))
            version_prefix = "snapshot"
        else:
            selected, source_id = _load_liquidity_screen(snapshot_path)
            version_prefix = "screen"
        selected = selected if max_symbols is None else selected[:max_symbols]
        return MiningUniverse(
            "prospective_equity_snapshot" if universe == "snapshot" else "screened_equity_cohort",
            selected,
            f"{version_prefix}:{source_id}:cap:{len(selected)}",
        )
    if snapshot_path is not None:
        raise ValueError("--universe-file is only valid with --universe snapshot or screen")
    if max_symbols is not None and universe == "etf32" and max_symbols > len(ETF_RESEARCH_UNIVERSE.symbols):
        # A larger cap cannot add symbols to a fixed benchmark and usually
        # indicates that the operator meant to select a snapshot instead.
        raise ValueError("max_symbols exceeds the fixed etf32 cohort")
    selected = ETF_RESEARCH_UNIVERSE.symbols if universe == "etf32" else _explicit_symbols(symbol, symbols)
    selected = selected if max_symbols is None else selected[:max_symbols]
    version = ETF_RESEARCH_UNIVERSE.version_id if universe == "etf32" else "explicit:" + ",".join(selected)
    return MiningUniverse(universe, selected, version)
