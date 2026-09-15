from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AssetClass, ConflictResolutionMode, Direction, StrategyType
from agentic_trader.screeners.base import BaseStrategy, ScreenerCandidate
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.screeners.indicators import calculate_ema
from agentic_trader.screeners.registry import ConflictResolver, StrategyRegistry


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.data.market_data import ContractMarketData

logger = logging.getLogger(__name__)


# Re-export ScreenerCandidate for backward compatibility
__all__ = [
    "FormulaicAlphaStrategy",
    "ScreenerCandidate",
    "SqueezeBreakoutStrategy",
    "StrategyEngine",
    "TrendPullbackStrategy",
]


class TrendPullbackStrategy(BaseStrategy):
    """Strategy A: Trend-Pullback momentum on Daily + 4-Hour data."""

    strategy_id: str = "trend_pullback"
    display_name: str = "Trend-Pullback Momentum"
    supported_asset_classes: tuple[AssetClass, ...] = (AssetClass.FUTURES, AssetClass.EQUITY)
    default_timeframe: str = "4h"

    def is_enabled(self, config: AppConfig | None = None) -> bool:
        cfg = config or self.config
        if not cfg:
            return True
        return bool(cfg.strategies.trend_pullback.enabled)

    def evaluate(
        self,
        data: ContractMarketData,
        asset_class: AssetClass = AssetClass.FUTURES,
    ) -> list[ScreenerCandidate]:
        if not self.is_enabled():
            return []

        cfg = (self.config or load_config()).strategies.trend_pullback
        df_daily = data.daily
        df_4h = data.four_hour

        if len(df_daily) < 10 or len(df_4h) < 10:
            return []

        # Determine daily EMA fast and slow
        fast_col = f"EMA_{cfg.daily_ema_fast}"
        slow_col = f"EMA_{cfg.daily_ema_slow}"

        daily_ema_fast_s = (
            df_daily[fast_col] if fast_col in df_daily else calculate_ema(df_daily["Close"], span=cfg.daily_ema_fast)
        )
        daily_ema_slow_s = (
            df_daily[slow_col] if slow_col in df_daily else calculate_ema(df_daily["Close"], span=cfg.daily_ema_slow)
        )

        daily_latest = df_daily.iloc[-1]
        daily_close = float(daily_latest["Close"])
        daily_fast = float(daily_ema_fast_s.iloc[-1])
        daily_slow = float(daily_ema_slow_s.iloc[-1])

        # Determine Daily Trend Invariant
        is_bullish_trend = daily_close > daily_fast > daily_slow
        is_bearish_trend = daily_close < daily_fast < daily_slow

        if not (is_bullish_trend or is_bearish_trend):
            return []

        # Look at recent 4h candles (current and previous 2 candles)
        latest_4h = df_4h.iloc[-1]
        prev_4h = df_4h.iloc[-2]
        prev2_4h = df_4h.iloc[-3]

        close_4h = float(latest_4h["Close"])
        trigger_ema_col = f"EMA_{cfg.trigger_ema_span}"
        trigger_ema_s = (
            df_4h[trigger_ema_col]
            if trigger_ema_col in df_4h
            else calculate_ema(df_4h["Close"], span=cfg.trigger_ema_span)
        )
        trigger_ema_val = float(trigger_ema_s.iloc[-1])

        ema20_4h = float(latest_4h["EMA_20"]) if "EMA_20" in latest_4h else trigger_ema_val
        ema50_4h = (
            float(latest_4h["EMA_50"])
            if "EMA_50" in latest_4h
            else float(calculate_ema(df_4h["Close"], span=50).iloc[-1])
        )
        ema200_4h = (
            float(latest_4h["EMA_200"])
            if "EMA_200" in latest_4h
            else float(calculate_ema(df_4h["Close"], span=200).iloc[-1])
        )
        rsi_current = float(latest_4h["RSI_14"])
        rsi_prev = float(prev_4h["RSI_14"])
        rsi_prev2 = float(prev2_4h["RSI_14"])
        atr_4h = float(latest_4h["ATR_14"])

        # Distance to trigger EMA must be within trigger_atr_distance_mult * ATR(14)
        dist_to_trigger = abs(close_4h - trigger_ema_val)
        within_tolerance = dist_to_trigger <= (cfg.trigger_atr_distance_mult * atr_4h)

        # Recent swing high and low over last 10 4h candles
        recent_window = df_4h.iloc[-10:]
        recent_swing_low = float(recent_window["Low"].min())
        recent_swing_high = float(recent_window["High"].max())

        timestamp_str = latest_4h.name.isoformat() if hasattr(latest_4h.name, "isoformat") else str(latest_4h.name)

        if is_bullish_trend and within_tolerance:
            # Long Trigger: RSI crossed below rsi_oversold_dip and recovered above rsi_oversold
            min_recent_rsi = min(rsi_prev, rsi_prev2)
            if min_recent_rsi < cfg.rsi_oversold_dip and rsi_current >= cfg.rsi_oversold:
                return [
                    ScreenerCandidate(
                        contract=data.contract,
                        symbol=data.contract,
                        asset_class=asset_class,
                        timeframe="4h",
                        strategy=StrategyType.TREND_PULLBACK,
                        direction=Direction.LONG,
                        current_price=round(close_4h, 2),
                        ema_20=round(ema20_4h, 2),
                        ema_50=round(ema50_4h, 2),
                        ema_200=round(ema200_4h, 2),
                        rsi_14=round(rsi_current, 2),
                        atr_14=round(atr_4h, 2),
                        candle_timestamp=timestamp_str,
                        recent_swing_low=round(recent_swing_low, 2),
                        recent_swing_high=round(recent_swing_high, 2),
                        trigger_detail=(
                            f"Daily Close > {cfg.daily_ema_fast} > {cfg.daily_ema_slow} EMA. "
                            f"4h RSI dipped to {min_recent_rsi:.1f} and recovered to {rsi_current:.1f} near {cfg.trigger_ema_span} EMA."
                        ),
                    )
                ]

        elif is_bearish_trend and within_tolerance:
            # Short Trigger: RSI crossed above rsi_overbought_surge and fell back below rsi_overbought
            max_recent_rsi = max(rsi_prev, rsi_prev2)
            if max_recent_rsi > cfg.rsi_overbought_surge and rsi_current <= cfg.rsi_overbought:
                return [
                    ScreenerCandidate(
                        contract=data.contract,
                        symbol=data.contract,
                        asset_class=asset_class,
                        timeframe="4h",
                        strategy=StrategyType.TREND_PULLBACK,
                        direction=Direction.SHORT,
                        current_price=round(close_4h, 2),
                        ema_20=round(ema20_4h, 2),
                        ema_50=round(ema50_4h, 2),
                        ema_200=round(ema200_4h, 2),
                        rsi_14=round(rsi_current, 2),
                        atr_14=round(atr_4h, 2),
                        candle_timestamp=timestamp_str,
                        recent_swing_low=round(recent_swing_low, 2),
                        recent_swing_high=round(recent_swing_high, 2),
                        trigger_detail=(
                            f"Daily Close < {cfg.daily_ema_fast} < {cfg.daily_ema_slow} EMA. "
                            f"4h RSI surged to {max_recent_rsi:.1f} and fell to {rsi_current:.1f} near {cfg.trigger_ema_span} EMA."
                        ),
                    )
                ]

        return []


class SqueezeBreakoutStrategy(BaseStrategy):
    """Strategy B: Volatility Squeeze Breakout on 4-Hour and 1-Hour data."""

    strategy_id: str = "squeeze_breakout"
    display_name: str = "Volatility Squeeze Breakout"
    supported_asset_classes: tuple[AssetClass, ...] = (AssetClass.FUTURES, AssetClass.EQUITY)
    default_timeframe: str = "4h"

    def is_enabled(self, config: AppConfig | None = None) -> bool:
        cfg = config or self.config
        if not cfg:
            return True
        return bool(cfg.strategies.squeeze_breakout.enabled)

    def evaluate_timeframe(
        self,
        data: ContractMarketData,
        timeframe: str = "4h",
        asset_class: AssetClass = AssetClass.FUTURES,
    ) -> ScreenerCandidate | None:
        if not self.is_enabled():
            return None

        cfg = (self.config or load_config()).strategies.squeeze_breakout
        df = data.four_hour if timeframe == "4h" else data.hourly
        if len(df) < 25 or "BB_Upper" not in df.columns or "BB_Lower" not in df.columns:
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(latest["Close"])
        prev_close = float(prev["Close"])
        bb_upper = float(latest["BB_Upper"])
        bb_lower = float(latest["BB_Lower"])
        prev_bb_upper = float(prev["BB_Upper"])
        prev_bb_lower = float(prev["BB_Lower"])
        volume = float(latest["Volume"])
        volume_sma = float(latest["Volume_SMA_20"]) if "Volume_SMA_20" in latest else 0.0
        atr = float(latest["ATR_14"])
        ema20 = float(latest["EMA_20"])
        ema50 = float(latest["EMA_50"])
        ema200 = float(latest["EMA_200"])
        rsi = float(latest["RSI_14"])

        # Prior squeeze requirement: Squeeze count prior to breakout >= min_squeeze_bars
        prior_squeeze_count = int(prev["Squeeze_Count"]) if "Squeeze_Count" in prev else 0
        had_squeeze = prior_squeeze_count >= cfg.min_squeeze_bars

        # Volume condition: Volume > volume_factor * SMA(Volume, 20)
        volume_surge = (volume_sma > 0) and (volume > (cfg.volume_factor * volume_sma))

        if not (had_squeeze and volume_surge):
            return None

        recent_window = df.iloc[-10:]
        recent_swing_low = float(recent_window["Low"].min())
        recent_swing_high = float(recent_window["High"].max())
        timestamp_str = latest.name.isoformat() if hasattr(latest.name, "isoformat") else str(latest.name)

        # Bullish Breakout: Closed above BB Upper while previous close was below/at BB Upper
        if close > bb_upper and prev_close <= prev_bb_upper:
            return ScreenerCandidate(
                contract=data.contract,
                symbol=data.contract,
                asset_class=asset_class,
                timeframe=timeframe,
                strategy=StrategyType.SQUEEZE_BREAKOUT,
                direction=Direction.LONG,
                current_price=round(close, 2),
                ema_20=round(ema20, 2),
                ema_50=round(ema50, 2),
                ema_200=round(ema200, 2),
                rsi_14=round(rsi, 2),
                atr_14=round(atr, 2),
                candle_timestamp=timestamp_str,
                recent_swing_low=round(recent_swing_low, 2),
                recent_swing_high=round(recent_swing_high, 2),
                trigger_detail=f"Squeeze fired after {prior_squeeze_count} bars. Candle closed above BB Upper with {volume / volume_sma:.1f}x volume surge.",
            )

        # Bearish Breakdown: Closed below BB Lower while previous close was above/at BB Lower
        if close < bb_lower and prev_close >= prev_bb_lower:
            return ScreenerCandidate(
                contract=data.contract,
                symbol=data.contract,
                asset_class=asset_class,
                timeframe=timeframe,
                strategy=StrategyType.SQUEEZE_BREAKOUT,
                direction=Direction.SHORT,
                current_price=round(close, 2),
                ema_20=round(ema20, 2),
                ema_50=round(ema50, 2),
                ema_200=round(ema200, 2),
                rsi_14=round(rsi, 2),
                atr_14=round(atr, 2),
                candle_timestamp=timestamp_str,
                recent_swing_low=round(recent_swing_low, 2),
                recent_swing_high=round(recent_swing_high, 2),
                trigger_detail=f"Squeeze fired after {prior_squeeze_count} bars. Candle closed below BB Lower with {volume / volume_sma:.1f}x volume surge.",
            )

        return None

    def evaluate(
        self,
        data: ContractMarketData,
        asset_class: AssetClass = AssetClass.FUTURES,
    ) -> list[ScreenerCandidate]:
        candidates: list[ScreenerCandidate] = []
        c_4h = self.evaluate_timeframe(data, timeframe="4h", asset_class=asset_class)
        if c_4h:
            candidates.append(c_4h)
        c_1h = self.evaluate_timeframe(data, timeframe="1h", asset_class=asset_class)
        if c_1h:
            candidates.append(c_1h)
        return candidates


class StrategyEngine:
    """Multi-strategy orchestrator managing strategy discovery, execution modes,

    and signal conflict resolution.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        registry: StrategyRegistry | None = None,
        conflict_resolver: ConflictResolver | None = None,
    ):
        self.config = config or load_config()
        self.registry = registry or StrategyRegistry(auto_load_promoted=True)
        self.conflict_resolver = conflict_resolver or ConflictResolver()

        # Instantiate and register default strategies
        self.trend_pullback_strat = TrendPullbackStrategy(self.config)
        self.squeeze_breakout_strat = SqueezeBreakoutStrategy(self.config)

        self.registry.register(self.trend_pullback_strat)
        self.registry.register(self.squeeze_breakout_strat)

    def check_trend_pullback(
        self, data: ContractMarketData, asset_class: AssetClass = AssetClass.FUTURES
    ) -> ScreenerCandidate | None:
        """Backward-compatible helper evaluating Strategy A: Trend-Pullback."""
        candidates = self.trend_pullback_strat.evaluate(data, asset_class=asset_class)
        return candidates[0] if candidates else None

    def check_squeeze_breakout(
        self,
        data: ContractMarketData,
        timeframe: str = "4h",
        asset_class: AssetClass = AssetClass.FUTURES,
    ) -> ScreenerCandidate | None:
        """Backward-compatible helper evaluating Strategy B: Squeeze Breakout."""
        return self.squeeze_breakout_strat.evaluate_timeframe(data, timeframe=timeframe, asset_class=asset_class)

    def scan_contract(
        self,
        data: ContractMarketData,
        asset_class: AssetClass = AssetClass.FUTURES,
        override_strategy: str | None = None,
        override_mode: str | None = None,
    ) -> list[ScreenerCandidate]:
        """Scan contract data across active strategies according to configured mode

        (single vs parallel) and apply conflict resolution.
        """
        active_strategies = self.registry.get_active_strategies(
            config=self.config,
            override_strategy=override_strategy,
            override_mode=override_mode,
        )

        raw_candidates: list[ScreenerCandidate] = []
        for strat in active_strategies:
            if not strat.can_handle(asset_class):
                continue
            candidates = strat.evaluate(data, asset_class=asset_class)
            raw_candidates.extend(candidates)

        conflict_mode = getattr(
            self.config.strategies,
            "conflict_resolution",
            ConflictResolutionMode.NETTING,
        )
        return self.conflict_resolver.resolve(raw_candidates, mode=conflict_mode)

    def scan_instrument(
        self,
        data: ContractMarketData,
        asset_class: AssetClass = AssetClass.FUTURES,
        override_strategy: str | None = None,
        override_mode: str | None = None,
    ) -> list[ScreenerCandidate]:
        """Alias for scan_contract supporting generalized multi-asset instruments."""
        return self.scan_contract(
            data,
            asset_class=asset_class,
            override_strategy=override_strategy,
            override_mode=override_mode,
        )
