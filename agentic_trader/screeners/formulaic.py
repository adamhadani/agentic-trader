from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentic_trader.constants import AssetClass, Direction
from agentic_trader.research.alpha.clock import closed_alpha_bars
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.strategy import alpha_scores, entry_directions, strategy_atr
from agentic_trader.screeners.base import BaseStrategy, ScreenerCandidate
from agentic_trader.screeners.indicators import calculate_ema, calculate_rsi


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.data.market_data import ContractMarketData
    from agentic_trader.research.alpha.models import AlphaDefinition

logger = logging.getLogger(__name__)
FIXED_CLOCK_MINIMUM_BARS = 15  # Preserve version-2 screening's existing warmup.


class FormulaicAlphaStrategy(BaseStrategy):
    """
    Production screener strategy executing a formulaic alpha DSL expression.
    Dynamically instantiated from promoted alpha records and registered in StrategyRegistry.
    Stamps every generated ScreenerCandidate with its unique, immutable alpha_id.
    """

    def __init__(
        self,
        definition: AlphaDefinition,
        config: AppConfig | None = None,
        evaluator: AlphaExpressionEvaluator | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        probe: bool = False,
    ) -> None:
        super().__init__(config=config)
        self.definition = definition
        self.strategy_id = definition.alpha_id.lower()
        self.display_name = definition.name
        self.default_timeframe = definition.timeframe or "4h"
        self.supported_asset_classes = (AssetClass.FUTURES, AssetClass.EQUITY, AssetClass.CRYPTO)
        self.evaluator = evaluator or AlphaExpressionEvaluator()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.probe = probe

    def is_enabled(self, config: AppConfig | None = None) -> bool:
        """Determines whether this alpha is enabled."""
        return True

    def evaluate(
        self,
        data: ContractMarketData,
        asset_class: AssetClass = AssetClass.FUTURES,
    ) -> list[ScreenerCandidate]:
        """
        Evaluate market data against the formulaic alpha expression.
        Returns a qualified ScreenerCandidate stamped with self.strategy_id when triggered.
        """
        # Check eligible_symbols universe routing if defined
        if self.definition.eligible_symbols:
            target_syms = {s.upper().strip("/").strip() for s in self.definition.eligible_symbols}
            contract_clean = getattr(data, "contract", "").strip("/").strip().upper()
            ticker_clean = getattr(data, "ticker", "").strip("/").strip().upper()
            symbol_clean = getattr(data, "symbol", "").strip("/").strip().upper()
            if not any(s and s in target_syms for s in (contract_clean, ticker_clean, symbol_clean)):
                return []

        try:
            df, _ = closed_alpha_bars(self.definition, data, as_of=self.clock())
        except (TypeError, ValueError) as exc:
            logger.warning("Alpha %s rejected invalid bar clock/data: %s", self.strategy_id, exc)
            return []
        minimum_bars = (
            max(self.definition.execution.atr_window, self.definition.execution.swing_window)
            if self.definition.clock is not None
            else FIXED_CLOCK_MINIMUM_BARS
        )
        if len(df) < minimum_bars:
            return []

        try:
            z_scores = alpha_scores(self.definition, df, self.evaluator)
        except Exception as e:
            logger.warning(
                "Failed evaluating formulaic alpha '%s' on %s: %s",
                self.strategy_id,
                getattr(data, "contract", "unknown"),
                e,
            )
            return []

        latest_z = float(z_scores.iloc[-1])
        entry_thresh = self.definition.entry_threshold
        direction = int(entry_directions(z_scores, self.definition).iloc[-1])
        signal_direction = Direction.LONG if direction == 1 else Direction.SHORT if direction == -1 else None

        if signal_direction is None:
            return []

        # Calculate standard technical telemetry for candidate card
        close_series = df["Close"] if "Close" in df else df["close"]
        high_series = df["High"] if "High" in df else df["high"]
        low_series = df["Low"] if "Low" in df else df["low"]

        curr_price = float(close_series.iloc[-1])
        ema_20_s = df["EMA_20"] if "EMA_20" in df else calculate_ema(close_series, 20)
        ema_50_s = df["EMA_50"] if "EMA_50" in df else calculate_ema(close_series, 50)
        if "EMA_200" in df:
            ema_200_s = df["EMA_200"]
        elif hasattr(data, "daily") and "EMA_200" in data.daily and not data.daily.empty:
            ema_200_s = data.daily["EMA_200"]
        else:
            ema_200_s = calculate_ema(close_series, 200)

        rsi_14_s = df["RSI_14"] if "RSI_14" in df else calculate_rsi(close_series, 14)
        atr_14_s = strategy_atr(df, self.definition.execution)

        recent_swing_low = float(low_series.tail(self.definition.execution.swing_window).min())
        recent_swing_high = float(high_series.tail(self.definition.execution.swing_window).max())
        candle_ts = str(df.index[-1])

        detail = (
            f"Alpha {self.strategy_id} triggered: Score Z={latest_z:+.2f} "
            f"(Threshold: {entry_thresh:.2f}) | {self.definition.expression}"
        )

        candidate = ScreenerCandidate(
            contract=data.contract,
            symbol=getattr(data, "symbol", data.contract) or data.contract,
            asset_class=asset_class,
            timeframe=self.default_timeframe,
            strategy=self.strategy_id,  # Stamped with the unique alpha strategy ID
            direction=str(signal_direction.value),
            current_price=curr_price,
            ema_20=float(ema_20_s.iloc[-1]),
            ema_50=float(ema_50_s.iloc[-1]),
            ema_200=float(ema_200_s.iloc[-1]),
            rsi_14=float(rsi_14_s.iloc[-1]),
            atr_14=float(atr_14_s.iloc[-1]),
            candle_timestamp=candle_ts,
            recent_swing_low=recent_swing_low,
            recent_swing_high=recent_swing_high,
            trigger_detail=detail,
            alpha_version=self.definition.version_id,
            alpha_score=latest_z,
            alpha_policy=self.definition.execution.to_dict(),
            probe=self.probe,
        )

        return [candidate]
