from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentic_trader.constants import ConflictResolutionMode, Direction, StrategyMode
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.screeners.base import BaseStrategy, ScreenerCandidate
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig

logger = logging.getLogger(__name__)


class ConflictResolver:
    """Resolves trade setup conflicts across multiple concurrent strategies."""

    @staticmethod
    def resolve(
        candidates: list[ScreenerCandidate],
        mode: str = ConflictResolutionMode.NETTING,
    ) -> list[ScreenerCandidate]:
        """Filter and resolve conflicting signals on the same underlying instrument.

        Modes:
        - netting: Opposing directions (LONG and SHORT) on the same symbol cancel out.
        - highest_conviction: Prioritizes setup with higher R:R / risk reward or primary trend.
        - first: Preserves the first signal generated chronologically.
        """
        if not candidates:
            return []

        # 1. Deduplicate identical setups (same symbol, direction, and strategy)
        seen_keys: set[tuple[str, str, str, str, str | None]] = set()
        deduped: list[ScreenerCandidate] = []
        for c in candidates:
            sym = c.symbol or c.contract
            key = (sym.upper(), c.direction.upper(), c.strategy.upper(), c.timeframe, c.alpha_version)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(c)

        if len(deduped) <= 1:
            return deduped

        # 2. Group by symbol
        by_symbol: dict[str, list[ScreenerCandidate]] = {}
        for c in deduped:
            sym = (c.symbol or c.contract).upper()
            by_symbol.setdefault(sym, []).append(c)

        resolved: list[ScreenerCandidate] = []
        for sym, sym_candidates in by_symbol.items():
            if len(sym_candidates) == 1:
                resolved.append(sym_candidates[0])
                continue

            # Check for conflicting directions on the same symbol
            has_long = any(str(c.direction).upper() in ("LONG", str(Direction.LONG)) for c in sym_candidates)
            has_short = any(str(c.direction).upper() in ("SHORT", str(Direction.SHORT)) for c in sym_candidates)

            if has_long and has_short:
                if mode == ConflictResolutionMode.NETTING:
                    logger.warning(
                        "Signal Conflict Detected on %s: Opposing LONG and SHORT signals across strategies. "
                        "Netting policy active -> Canceling conflicting setups.",
                        sym,
                        extra={"symbol": sym, "candidate_count": len(sym_candidates), "action": "netted_out"},
                    )
                    continue
                elif mode == ConflictResolutionMode.HIGHEST_CONVICTION:
                    # Prefer Trend-Pullback momentum over breakout or higher price-EMA distance
                    logger.info(
                        "Signal Conflict on %s resolved via highest_conviction policy.",
                        sym,
                    )
                    # Pick candidate with greatest swing range or primary strategy
                    winner = max(
                        sym_candidates,
                        key=lambda x: x.recent_swing_high - x.recent_swing_low,
                    )
                    resolved.append(winner)
                    continue
                elif mode == ConflictResolutionMode.FIRST:
                    resolved.append(sym_candidates[0])
                    continue
                else:
                    # Default fallback: netting
                    continue

            # One owner per instrument. This deterministic arbitration is not an
            # alpha combination model; calibrated combinations remain shadow-only.
            winner = min(
                sym_candidates,
                key=lambda candidate: (candidate.strategy, candidate.timeframe, candidate.alpha_version or ""),
            )
            contributors = tuple(
                sorted({f"{c.strategy}/{c.timeframe}/{c.alpha_version or 'builtin'}" for c in sym_candidates})
            )
            resolved.append(winner.model_copy(update={"contributors": contributors}))

        return resolved


class StrategyRegistry:
    """Central registry and lifecycle manager for all quantitative trading strategies."""

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    def install_alphas(self, definitions: tuple[AlphaDefinition, ...]) -> int:
        """Build then atomically swap one complete registry snapshot between scans."""
        strategies = {sid: s for sid, s in self._strategies.items() if not isinstance(s, FormulaicAlphaStrategy)}
        for definition in definitions:
            strategy = FormulaicAlphaStrategy(definition)
            strategies[strategy.strategy_id] = strategy
        self._strategies = strategies
        return len(definitions)

    def register(self, strategy: BaseStrategy) -> None:
        """Register a strategy instance into the registry."""
        sid = strategy.strategy_id.lower()
        if sid in self._strategies:
            logger.debug("Overwriting strategy registration for '%s'", sid)
        self._strategies[sid] = strategy

    def unregister(self, strategy_id: str) -> bool:
        """Unregister a strategy by ID."""
        sid = strategy_id.lower()
        if sid in self._strategies:
            del self._strategies[sid]
            return True
        return False

    def get(self, strategy_id: str) -> BaseStrategy | None:
        """Retrieve a registered strategy by ID (case-insensitive)."""
        return self._strategies.get(strategy_id.lower())

    def list_strategies(self) -> list[str]:
        """Return list of all registered strategy IDs."""
        return sorted(self._strategies.keys())

    def list_registered_strategies(self) -> list[str]:
        """Alias for list_strategies."""
        return self.list_strategies()

    def get_active_strategies(
        self,
        config: AppConfig,
        override_strategy: str | None = None,
        override_mode: str | None = None,
    ) -> list[BaseStrategy]:
        """Resolve and return active strategies based on configuration and runtime overrides."""
        # Explicit single strategy override (e.g. from CLI --strategy)
        if override_strategy:
            strat = self.get(override_strategy)
            if strat:
                return [strat]
            logger.warning("Requested strategy '%s' not found in registry.", override_strategy)
            return []

        raw_mode = override_mode or getattr(config.strategies, "mode", StrategyMode.PARALLEL) or StrategyMode.PARALLEL
        mode = str(raw_mode).lower()

        if mode == StrategyMode.SINGLE:
            raw_single = getattr(config.strategies, "active_strategy", "trend_pullback") or "trend_pullback"
            single_id = str(raw_single).lower()
            strat = self.get(single_id)
            if strat and strat.is_enabled(config):
                return [strat]
            logger.warning(
                "Single strategy '%s' is either not registered or not enabled in config.",
                single_id,
            )
            return []

        # Parallel mode: evaluate all configured active strategies
        configured_active = getattr(
            config.strategies,
            "active_strategies",
            ["trend_pullback", "squeeze_breakout"],
        )
        active_strats: list[BaseStrategy] = []
        for sid in configured_active:
            strat = self.get(sid.lower())
            if strat and strat.is_enabled(config) and strat not in active_strats:
                active_strats.append(strat)

        # Include any active promoted formulaic alphas in parallel screening
        for sid, strat in list(self._strategies.items()):
            if sid.startswith("alpha_") and strat.is_enabled(config) and strat not in active_strats:
                active_strats.append(strat)

        return active_strats
