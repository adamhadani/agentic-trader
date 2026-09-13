from pydantic import BaseModel

from agentic_trader.config import AppConfig
from agentic_trader.constants import Direction, StrategyType
from agentic_trader.data.market_data import ContractMarketData


class ScreenerCandidate(BaseModel):
    contract: str
    timeframe: str
    strategy: str
    direction: str
    current_price: float
    ema_20: float
    ema_50: float
    ema_200: float
    rsi_14: float
    atr_14: float
    candle_timestamp: str
    recent_swing_low: float
    recent_swing_high: float
    trigger_detail: str


class StrategyEngine:
    def __init__(self, config: AppConfig):
        self.config = config

    def check_trend_pullback(self, data: ContractMarketData) -> ScreenerCandidate | None:
        """
        Evaluate Strategy A: Trend-Pullback on Daily + 4-Hour data.
        """
        cfg = self.config.strategies.trend_pullback
        if not cfg.enabled:
            return None

        df_daily = data.daily
        df_4h = data.four_hour

        if len(df_daily) < 10 or len(df_4h) < 10:
            return None

        daily_latest = df_daily.iloc[-1]
        daily_close = float(daily_latest["Close"])
        daily_ema50 = float(daily_latest["EMA_50"])
        daily_ema200 = float(daily_latest["EMA_200"])

        # Determine Daily Trend Invariant
        is_bullish_trend = daily_close > daily_ema50 > daily_ema200
        is_bearish_trend = daily_close < daily_ema50 < daily_ema200

        if not (is_bullish_trend or is_bearish_trend):
            return None

        # Look at recent 4h candles (current and previous 3 candles)
        latest_4h = df_4h.iloc[-1]
        prev_4h = df_4h.iloc[-2]
        prev2_4h = df_4h.iloc[-3]

        close_4h = float(latest_4h["Close"])
        ema20_4h = float(latest_4h["EMA_20"])
        ema50_4h = float(latest_4h["EMA_50"])
        ema200_4h = float(latest_4h["EMA_200"])
        rsi_current = float(latest_4h["RSI_14"])
        rsi_prev = float(prev_4h["RSI_14"])
        rsi_prev2 = float(prev2_4h["RSI_14"])
        atr_4h = float(latest_4h["ATR_14"])

        # Distance to EMA20 must be within 0.5 * ATR(14)
        dist_to_ema20 = abs(close_4h - ema20_4h)
        within_tolerance = dist_to_ema20 <= (cfg.trigger_atr_distance_mult * atr_4h)

        # Recent swing high and low over last 10 4h candles
        recent_window = df_4h.iloc[-10:]
        recent_swing_low = float(recent_window["Low"].min())
        recent_swing_high = float(recent_window["High"].max())

        timestamp_str = latest_4h.name.isoformat() if hasattr(latest_4h.name, "isoformat") else str(latest_4h.name)

        if is_bullish_trend and within_tolerance:
            # Long Trigger: RSI crossed below 45 and recovered above 40
            min_recent_rsi = min(rsi_prev, rsi_prev2)
            if min_recent_rsi < 45.0 and rsi_current >= 40.0:
                return ScreenerCandidate(
                    contract=data.contract,
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
                    trigger_detail=f"Daily Close > 50 > 200 EMA. 4h RSI dipped to {min_recent_rsi:.1f} and recovered to {rsi_current:.1f} near 20 EMA.",
                )

        elif is_bearish_trend and within_tolerance:
            # Short Trigger: RSI crossed above 55 and fell back below 60
            max_recent_rsi = max(rsi_prev, rsi_prev2)
            if max_recent_rsi > 55.0 and rsi_current <= 60.0:
                return ScreenerCandidate(
                    contract=data.contract,
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
                    trigger_detail=f"Daily Close < 50 < 200 EMA. 4h RSI surged to {max_recent_rsi:.1f} and fell to {rsi_current:.1f} near 20 EMA.",
                )

        return None

    def check_squeeze_breakout(self, data: ContractMarketData, timeframe: str = "4h") -> ScreenerCandidate | None:
        """
        Evaluate Strategy B: Volatility Squeeze Breakout on 4h or 1h data.
        """
        cfg = self.config.strategies.squeeze_breakout
        if not cfg.enabled:
            return None

        df = data.four_hour if timeframe == "4h" else data.hourly
        if len(df) < 25:
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

        # Prior squeeze requirement: Squeeze count prior to breakout >= 5
        prior_squeeze_count = int(prev["Squeeze_Count"])
        had_squeeze = prior_squeeze_count >= cfg.min_squeeze_bars

        # Volume condition: Volume > 1.3 * SMA(Volume, 20)
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

    def scan_contract(self, data: ContractMarketData) -> list[ScreenerCandidate]:
        candidates: list[ScreenerCandidate] = []
        pullback = self.check_trend_pullback(data)
        if pullback:
            candidates.append(pullback)

        squeeze_4h = self.check_squeeze_breakout(data, timeframe="4h")
        if squeeze_4h:
            candidates.append(squeeze_4h)

        squeeze_1h = self.check_squeeze_breakout(data, timeframe="1h")
        if squeeze_1h:
            candidates.append(squeeze_1h)

        return candidates
