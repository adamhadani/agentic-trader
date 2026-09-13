"""Statistical Pairs Trading screener and candidate evaluator."""

from __future__ import annotations

import itertools
import logging
from datetime import UTC, datetime

import pandas as pd

from agentic_trader.config import PairsConfig
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.pairs.cointegration import (
    calculate_rolling_spread_zscore,
    generate_spread_signal,
    test_engle_granger,
)
from agentic_trader.pairs.models import PairEvaluation, SignalType


logger = logging.getLogger(__name__)

# Common futures to ETF proxy mappings
SYMBOL_PROXIES: dict[str, str] = {
    "/MES": "SPY",
    "MES=F": "SPY",
    "ES=F": "SPY",
    "/MNQ": "QQQ",
    "MNQ=F": "QQQ",
    "NQ=F": "QQQ",
    "/M2K": "IWM",
    "RTY=F": "IWM",
    "/MGC": "GLD",
    "GC=F": "GLD",
    "/MCL": "USO",
    "CL=F": "USO",
}


class PairsScreener:
    """Evaluates cross-asset pairs for cointegration, mean-reversion half-life, and trading signals."""

    def __init__(
        self,
        data_fetcher: MarketDataFetcher | None = None,
        config: PairsConfig | None = None,
    ):
        self.data_fetcher = data_fetcher or MarketDataFetcher()
        self.config = config or PairsConfig()

    @classmethod
    def resolve_symbol(cls, symbol: str) -> str:
        """Resolve futures contract to liquid proxy ticker if needed."""
        cleaned = symbol.strip().upper()
        return SYMBOL_PROXIES.get(cleaned, cleaned)

    def evaluate_pair(
        self,
        asset_y: str,
        asset_x: str,
        lookback_days: int | None = None,
    ) -> PairEvaluation | None:
        """Evaluate cointegration and spread state between asset_y and asset_x."""
        lookback = lookback_days or self.config.lookback_days
        ticker_y = self.resolve_symbol(asset_y)
        ticker_x = self.resolve_symbol(asset_x)
        pair_name = f"{asset_y}/{asset_x}"

        try:
            data_y = self.data_fetcher.fetch_data(ticker_y, ticker_y, daily_period="2y")
            data_x = self.data_fetcher.fetch_data(ticker_x, ticker_x, daily_period="2y")

            if data_y.daily.empty or data_x.daily.empty:
                logger.warning("Empty price data for pair %s", pair_name)
                return None

            close_y = data_y.daily["Close"].tail(lookback).dropna()
            close_x = data_x.daily["Close"].tail(lookback).dropna()

            aligned = pd.concat([close_y, close_x], axis=1, join="inner").dropna()
            if len(aligned) < 30:
                logger.warning("Insufficient aligned history for pair %s (%d bars)", pair_name, len(aligned))
                return None

            series_y = aligned.iloc[:, 0]
            series_x = aligned.iloc[:, 1]
            series_y.name = asset_y
            series_x.name = asset_x

            # Run Engle-Granger test
            coint_result = test_engle_granger(
                series_y=series_y,
                series_x=series_x,
                asset_y_name=asset_y,
                asset_x_name=asset_x,
                p_value_threshold=self.config.p_value_threshold,
            )

            # Compute rolling Z-score
            spread_df = calculate_rolling_spread_zscore(
                series_y=series_y,
                series_x=series_x,
                beta=coint_result.hedge_ratio_beta,
                alpha=coint_result.intercept_alpha,
                lookback=self.config.z_score_lookback,
            )

            latest_row = spread_df.iloc[-1]
            latest_idx = spread_df.index[-1]
            ts = latest_idx.to_pydatetime() if isinstance(latest_idx, pd.Timestamp) else datetime.now(UTC)

            current_spread = float(latest_row["spread"])
            spread_mean = float(latest_row["spread_mean"])
            spread_std = float(latest_row["spread_std"])
            z_score = float(latest_row["z_score"])

            signal = generate_spread_signal(
                pair_name=pair_name,
                timestamp=ts,
                current_spread=current_spread,
                spread_mean=spread_mean,
                spread_std=spread_std,
                z_score=z_score,
                z_entry=self.config.z_entry_threshold,
                z_exit=self.config.z_exit_threshold,
            )

            half_life_ok = (
                self.config.min_half_life_bars <= coint_result.half_life_bars <= self.config.max_half_life_bars
            )
            is_actionable = coint_result.is_cointegrated and half_life_ok and signal.signal != SignalType.NEUTRAL

            return PairEvaluation(
                pair_name=pair_name,
                asset_y=asset_y,
                asset_x=asset_x,
                coint_result=coint_result,
                signal=signal,
                lookback_bars=len(aligned),
                is_actionable=is_actionable,
            )

        except Exception as e:
            logger.error("Error evaluating pair %s: %s", pair_name, e)
            return None

    def scan_pairs(
        self,
        pairs: list[tuple[str, str]] | None = None,
        lookback_days: int | None = None,
    ) -> list[PairEvaluation]:
        """Scan a list of asset pairs and rank by statistical arbitrage opportunity."""
        candidates = pairs or self.config.default_pairs
        results: list[PairEvaluation] = []

        for asset_y, asset_x in candidates:
            eval_res = self.evaluate_pair(asset_y, asset_x, lookback_days=lookback_days)
            if eval_res is not None:
                results.append(eval_res)

        # Sort: actionable pairs first, then descending by absolute Z-score, then ascending by p-value
        results.sort(
            key=lambda item: (
                1 if item.is_actionable else 0,
                abs(item.signal.z_score),
                -item.coint_result.p_value,
            ),
            reverse=True,
        )
        return results

    @staticmethod
    def generate_pairwise_combinations(symbols: list[str]) -> list[tuple[str, str]]:
        """Generate unique ordered pairs from a list of symbols."""
        clean_symbols = [s.strip().upper() for s in symbols if s.strip()]
        return list(itertools.combinations(clean_symbols, 2))
