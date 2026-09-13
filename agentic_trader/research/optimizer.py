import logging
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from agentic_trader.backtest.metrics import (
    calculate_drawdown,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_win_rate,
)
from agentic_trader.backtest.models import BacktestTrade
from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType
from agentic_trader.data.market_data import ContractMarketData, MarketDataFetcher
from agentic_trader.research.models import OptimizationResult, ParameterCandidate


try:
    import vectorbt as vbt

    HAS_VECTORBT = True
except Exception:
    HAS_VECTORBT = False

logger = logging.getLogger(__name__)


class ParameterGridOptimizer:
    """
    High-throughput parameter grid search and sensitivity analysis engine.
    Leverages VectorBT JIT tensor compilation when available, with clean
    vectorized NumPy fallback when running in minimal environments.
    """

    def __init__(self, config: AppConfig | None = None):
        self.config = config or load_config()
        self.data_fetcher = MarketDataFetcher()

    def run(
        self,
        symbol: str,
        strategy: str = "trend_pullback",
        lookback: str = "2y",
        market_data: ContractMarketData | None = None,
        force_fallback: bool = False,
    ) -> OptimizationResult:
        """
        Execute hyperparameter grid search for the given asset and strategy.
        """
        data = market_data or self._load_market_data(symbol, lookback)
        use_vbt = HAS_VECTORBT and not force_fallback
        engine_name = "VectorBT (JIT Tensor)" if use_vbt else "Vectorized NumPy"

        logger.info(
            "Starting parameter optimization for %s via %s (Strategy: %s, Lookback: %s)...",
            symbol,
            engine_name,
            strategy,
            lookback,
        )

        if strategy == "trend_pullback":
            candidates = self._optimize_trend_pullback(data, use_vbt=use_vbt)
        elif strategy == "squeeze_breakout":
            candidates = self._optimize_squeeze_breakout(data, use_vbt=use_vbt)
        else:
            raise ValueError(f"Unsupported strategy for optimization: {strategy}")

        # Rank candidates: prioritize configurations with executed trades, then risk-adjusted return (Sharpe), then total return
        ranked = sorted(
            candidates,
            key=lambda c: (
                1 if c.total_trades > 0 else 0,
                c.sharpe_ratio if not (np.isnan(c.sharpe_ratio) or np.isinf(c.sharpe_ratio)) else -999.0,
                c.total_return_pct,
            ),
            reverse=True,
        )

        return OptimizationResult(
            symbol=symbol,
            strategy=strategy,
            lookback=lookback,
            engine_used=engine_name,
            total_combinations_tested=len(candidates),
            ranked_candidates=ranked,
        )

    def _load_market_data(self, symbol: str, lookback: str) -> ContractMarketData:
        ticker = symbol.strip("/").upper() + "=F" if symbol.startswith("/") else symbol
        yf_ticker = yf.Ticker(ticker)
        df_daily = yf_ticker.history(period=lookback, interval="1d")
        df_daily = self.data_fetcher._clean_yfinance_df(df_daily)
        df_daily = self.data_fetcher.compute_daily_indicators(df_daily)
        df_daily = self.data_fetcher.compute_intraday_indicators(df_daily)

        try:
            df_1h = yf_ticker.history(
                period=lookback if lookback in ("1mo", "3mo", "6mo", "1y", "2y") else "2y", interval="1h"
            )
            df_1h = self.data_fetcher._clean_yfinance_df(df_1h)
            df_1h = self.data_fetcher.compute_intraday_indicators(df_1h)
            df_4h = self.data_fetcher.resample_to_4h(df_1h)
            df_4h = self.data_fetcher.compute_intraday_indicators(df_4h)
        except Exception:
            df_1h = df_daily.copy()
            df_4h = df_daily.copy()

        return ContractMarketData(
            contract=symbol,
            ticker=ticker,
            daily=df_daily,
            four_hour=df_4h,
            hourly=df_1h,
        )

    def _optimize_trend_pullback(
        self,
        data: ContractMarketData,
        use_vbt: bool,
    ) -> list[ParameterCandidate]:
        """
        Sweep RSI pullback oversold limits and fast EMA periods for Trend Pullback strategy.
        """
        rsi_grid = [35.0, 38.0, 40.0, 42.0, 45.0, 48.0, 50.0]
        ema_grid = [15, 20, 25]

        candidates: list[ParameterCandidate] = []
        df = data.daily.copy()
        if len(df) < 15:
            return candidates

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        # Base trend condition: Price > 50 EMA > 200 EMA
        base_trend = (close > df["EMA_50"]) & (df["EMA_50"] > df["EMA_200"])
        rsi_series = df["RSI_14"]
        atr_series = df["ATR_14"]

        for ema_span in ema_grid:
            ema_series = close.ewm(span=ema_span, adjust=False).mean()
            # Proximity to EMA: within 1.0% of EMA
            ema_prox = (close - ema_series).abs() / ema_series <= 0.015

            for rsi_thresh in rsi_grid:
                # RSI dipped <= threshold in recent bars and bounced
                rsi_dipped = (rsi_series <= rsi_thresh) | (rsi_series.shift(1) <= rsi_thresh)
                rsi_bounced = rsi_series > rsi_series.shift(1)

                entries = base_trend & ema_prox & rsi_dipped & rsi_bounced

                params: dict[str, Any] = {
                    "rsi_threshold": rsi_thresh,
                    "ema_span": ema_span,
                }

                cand = self._simulate_signals(
                    close=close,
                    high=high,
                    low=low,
                    entries=entries,
                    atr=atr_series,
                    params=params,
                    use_vbt=use_vbt,
                )
                candidates.append(cand)

        return candidates

    def _optimize_squeeze_breakout(
        self,
        data: ContractMarketData,
        use_vbt: bool,
    ) -> list[ParameterCandidate]:
        """
        Sweep volume surge factors and minimum squeeze duration for Squeeze Breakout strategy.
        """
        volume_factor_grid = [1.1, 1.2, 1.3, 1.4, 1.5]
        min_squeeze_grid = [3, 4, 5, 6, 8]

        candidates: list[ParameterCandidate] = []
        df = data.daily.copy()
        if len(df) < 15:
            return candidates

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        vol_sma = volume.rolling(20).mean()

        squeeze_count = df["Squeeze_Count"] if "Squeeze_Count" in df else pd.Series(0, index=df.index)
        bb_upper = df["BB_Upper"] if "BB_Upper" in df else close * 1.02
        atr_series = df["ATR_14"]

        breakout = close > bb_upper

        for vf in volume_factor_grid:
            vol_surge = volume > (vf * vol_sma)
            for min_sq in min_squeeze_grid:
                had_sq = squeeze_count.shift(1) >= min_sq
                entries = breakout & vol_surge & had_sq

                params: dict[str, Any] = {
                    "volume_factor": vf,
                    "min_squeeze_bars": min_sq,
                }

                cand = self._simulate_signals(
                    close=close,
                    high=high,
                    low=low,
                    entries=entries,
                    atr=atr_series,
                    params=params,
                    use_vbt=use_vbt,
                )
                candidates.append(cand)

        return candidates

    def _simulate_signals(
        self,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        entries: pd.Series,
        atr: pd.Series,
        params: dict[str, Any],
        use_vbt: bool,
    ) -> ParameterCandidate:
        """
        Simulate entries with 1:2 R:R ATR bracket exits.
        If VectorBT is active, leverages VectorBT portfolio stats; otherwise runs vectorized NumPy simulation.
        """
        if use_vbt and HAS_VECTORBT:
            try:
                # Clean boolean mask
                clean_entries = entries.fillna(False)
                portfolio = vbt.Portfolio.from_signals(
                    close=close,
                    entries=clean_entries,
                    sl_stop=0.02,  # 2% stop approximation for fast tensor evaluation
                    tp_stop=0.04,  # 4% take profit (1:2 R:R)
                    init_cash=100000.0,
                    freq="1D",
                )

                ret_pct = round(float(portfolio.total_return() * 100.0), 2)
                trade_count = int(portfolio.trades.count())
                wr = round(float(portfolio.trades.win_rate() * 100.0), 2) if trade_count > 0 else 0.0
                sharpe_raw = float(portfolio.sharpe_ratio())
                sharpe = (
                    0.0 if (trade_count == 0 or np.isnan(sharpe_raw) or np.isinf(sharpe_raw)) else round(sharpe_raw, 2)
                )
                max_dd = round(abs(float(portfolio.max_drawdown() * 100.0)), 2)
                pf = round(float(portfolio.trades.profit_factor()), 2) if portfolio.trades.count() > 0 else 0.0

                return ParameterCandidate(
                    parameters=params,
                    total_return_pct=ret_pct,
                    win_rate=wr,
                    profit_factor=pf if not np.isnan(pf) else 0.0,
                    sharpe_ratio=sharpe,
                    max_drawdown_pct=max_dd if not np.isnan(max_dd) else 0.0,
                    total_trades=trade_count,
                )
            except Exception as e:
                logger.debug("VectorBT execution failed (%s); falling back to vectorized NumPy", e)

        # Fallback pure NumPy/Pandas simulation
        trades: list[BacktestTrade] = []
        equity = [100000.0]
        in_trade = False
        entry_p = 0.0
        stop_p = 0.0
        target_p = 0.0

        for i in range(len(close)):
            curr_c = float(close.iloc[i])
            curr_h = float(high.iloc[i])
            curr_l = float(low.iloc[i])
            curr_atr = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else (curr_c * 0.015)
            dt = close.index[i].to_pydatetime() if hasattr(close.index[i], "to_pydatetime") else close.index[i]

            if in_trade:
                # Check bracket exits
                closed = False
                exit_price = curr_c
                if curr_l <= stop_p:
                    closed = True
                    exit_price = stop_p
                elif curr_h >= target_p:
                    closed = True
                    exit_price = target_p

                if closed:
                    pnl = (exit_price - entry_p) * 50.0  # standard size
                    pnl_pct = ((exit_price - entry_p) / entry_p) * 100.0
                    trades.append(
                        BacktestTrade(
                            symbol="TEST",
                            asset_class=AssetClass.EQUITY,
                            strategy=StrategyType.TREND_PULLBACK,
                            direction=Direction.LONG,
                            entry_timestamp=dt,
                            entry_price=entry_p,
                            quantity=50.0,
                            stop_loss=stop_p,
                            take_profit=target_p,
                            risk_dollars=curr_atr * 50.0,
                            exit_timestamp=dt,
                            exit_price=exit_price,
                            exit_reason=ExitReason.TAKE_PROFIT if exit_price >= target_p else ExitReason.STOP_LOSS,
                            pnl_dollars=pnl,
                            pnl_pct=pnl_pct,
                        )
                    )
                    equity.append(equity[-1] + pnl)
                    in_trade = False
                else:
                    unrealized = (curr_c - entry_p) * 50.0
                    equity.append(equity[-1] + unrealized)
            else:
                equity.append(equity[-1])
                if bool(entries.iloc[i]):
                    in_trade = True
                    entry_p = curr_c
                    stop_p = curr_c - (curr_atr * 1.5)
                    target_p = curr_c + (curr_atr * 3.0)

        eq_series = pd.Series(equity)
        daily_ret = eq_series.pct_change().dropna()
        total_ret = round(((eq_series.iloc[-1] - 100000.0) / 100000.0) * 100.0, 2)
        wr = calculate_win_rate(trades)
        pf = calculate_profit_factor(trades)
        max_dd, _ = calculate_drawdown(eq_series)
        sharpe = calculate_sharpe_ratio(daily_ret)

        return ParameterCandidate(
            parameters=params,
            total_return_pct=total_ret,
            win_rate=wr,
            profit_factor=pf,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            total_trades=len(trades),
        )
