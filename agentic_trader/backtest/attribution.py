import logging
from datetime import datetime

import pandas as pd

from agentic_trader.agent.regime import classify_vix_level
from agentic_trader.backtest.metrics import calculate_profit_factor, calculate_win_rate
from agentic_trader.backtest.models import (
    AssetClassAttribution,
    BacktestResult,
    BacktestTrade,
    FactorAttribution,
    PerformanceAttributionResult,
    RegimeAttribution,
)
from agentic_trader.constants import SECTOR_MAP, AssetClass, StrategyType, VolatilityRegime


logger = logging.getLogger(__name__)


def _get_regime_for_date(dt: datetime, vix_df: pd.DataFrame | None) -> str:
    """Classify macro volatility regime for a given trade entry date."""
    if vix_df is None or vix_df.empty:
        return str(VolatilityRegime.NORMAL)
    try:
        lookup_ts = pd.Timestamp(dt)
        clean_vix = vix_df.copy()
        if hasattr(clean_vix.index, "tz") and clean_vix.index.tz is not None:
            if lookup_ts.tzinfo is None:
                lookup_ts = lookup_ts.tz_localize(clean_vix.index.tz)
            else:
                lookup_ts = lookup_ts.tz_convert(clean_vix.index.tz)
        elif lookup_ts.tzinfo is not None:
            lookup_ts = lookup_ts.tz_localize(None)

        asof_loc = clean_vix.index.get_indexer([lookup_ts], method="pad")[0]
        val = float(clean_vix["Close"].iloc[0]) if asof_loc == -1 else float(clean_vix["Close"].iloc[asof_loc])
        return str(classify_vix_level(val))
    except Exception as e:
        logger.debug("Regime classification fallback on date %s: %s", dt, e)
        return str(VolatilityRegime.NORMAL)


def calculate_performance_attribution(
    result: BacktestResult,
    vix_df: pd.DataFrame | None = None,
) -> PerformanceAttributionResult:
    """
    Decomposes backtest returns and risk metrics across:
    1. Quantitative Strategy Factors (Trend-Pullback vs Squeeze-Breakout vs Cash Carry)
    2. Macro Volatility Regimes (Compressed, Normal, Elevated, Extreme)
    3. Tradable Asset Classes (Futures vs Equities vs Crypto)
    4. Economic Sectors / Correlation Clusters
    """
    trades = result.trades
    starting_cash = max(result.starting_cash, 1.0)
    total_alpha = max(result.strategy_pnl, 0.0)

    # 1. Factor Attribution
    factor_attributions: list[FactorAttribution] = []

    # Trend-Pullback factor
    trend_trades = [
        t for t in trades if str(t.strategy).upper() in ("TREND_PULLBACK", str(StrategyType.TREND_PULLBACK).upper())
    ]
    trend_pnl = sum(t.pnl_dollars or 0.0 for t in trend_trades)
    trend_wr = calculate_win_rate(trend_trades)
    trend_pf = calculate_profit_factor(trend_trades)
    trend_pct = (trend_pnl / starting_cash) * 100.0
    trend_share = (trend_pnl / total_alpha * 100.0) if total_alpha > 0 and trend_pnl > 0 else 0.0

    factor_attributions.append(
        FactorAttribution(
            factor_name="Trend-Pullback (Momentum)",
            pnl_dollars=round(trend_pnl, 2),
            return_contribution_pct=round(trend_pct, 2),
            trade_count=len(trend_trades),
            win_rate=round(trend_wr, 1),
            profit_factor=round(trend_pf, 2),
            pct_of_total_alpha=round(trend_share, 1),
        )
    )

    # Squeeze-Breakout factor
    squeeze_trades = [
        t for t in trades if str(t.strategy).upper() in ("SQUEEZE_BREAKOUT", str(StrategyType.SQUEEZE_BREAKOUT).upper())
    ]
    squeeze_pnl = sum(t.pnl_dollars or 0.0 for t in squeeze_trades)
    squeeze_wr = calculate_win_rate(squeeze_trades)
    squeeze_pf = calculate_profit_factor(squeeze_trades)
    squeeze_pct = (squeeze_pnl / starting_cash) * 100.0
    squeeze_share = (squeeze_pnl / total_alpha * 100.0) if total_alpha > 0 and squeeze_pnl > 0 else 0.0

    factor_attributions.append(
        FactorAttribution(
            factor_name="Squeeze Breakout (Volatility)",
            pnl_dollars=round(squeeze_pnl, 2),
            return_contribution_pct=round(squeeze_pct, 2),
            trade_count=len(squeeze_trades),
            win_rate=round(squeeze_wr, 1),
            profit_factor=round(squeeze_pf, 2),
            pct_of_total_alpha=round(squeeze_share, 1),
        )
    )

    # Cash Carry Yield factor
    carry_pnl = result.cash_yield_pnl
    carry_pct = (carry_pnl / starting_cash) * 100.0
    factor_attributions.append(
        FactorAttribution(
            factor_name="Cash Carry Yield (Risk-Free)",
            pnl_dollars=round(carry_pnl, 2),
            return_contribution_pct=round(carry_pct, 2),
            trade_count=0,
            win_rate=100.0,
            profit_factor=float("inf") if carry_pnl > 0 else 0.0,
            pct_of_total_alpha=0.0,
        )
    )

    # 2. Macro Volatility Regime Attribution
    regimes = [str(r.value) for r in VolatilityRegime]
    trade_regimes = {id(t): _get_regime_for_date(t.entry_timestamp, vix_df) for t in trades}

    regime_attributions: list[RegimeAttribution] = []
    for r in regimes:
        r_trades = [t for t in trades if trade_regimes.get(id(t)) == r]
        r_pnl = sum(t.pnl_dollars or 0.0 for t in r_trades)
        r_wr = calculate_win_rate(r_trades)
        r_pf = calculate_profit_factor(r_trades)
        r_avg = (r_pnl / len(r_trades)) if r_trades else 0.0

        regime_attributions.append(
            RegimeAttribution(
                regime=r,
                trade_count=len(r_trades),
                pnl_dollars=round(r_pnl, 2),
                win_rate=round(r_wr, 1),
                profit_factor=round(r_pf, 2),
                avg_trade_pnl=round(r_avg, 2),
            )
        )

    # 3. Asset Class Attribution
    asset_class_attributions: list[AssetClassAttribution] = []
    asset_classes = [AssetClass.FUTURES, AssetClass.EQUITY, AssetClass.CRYPTO]
    for ac in asset_classes:
        ac_trades = [t for t in trades if (t.asset_class == ac or str(t.asset_class).upper() == str(ac.value).upper())]
        if not ac_trades and ac == AssetClass.CRYPTO:
            continue
        ac_pnl = sum(t.pnl_dollars or 0.0 for t in ac_trades)
        ac_wr = calculate_win_rate(ac_trades)
        ac_pf = calculate_profit_factor(ac_trades)
        ac_share = (ac_pnl / total_alpha * 100.0) if total_alpha > 0 and ac_pnl > 0 else 0.0

        asset_class_attributions.append(
            AssetClassAttribution(
                name=ac.value.capitalize(),
                trade_count=len(ac_trades),
                pnl_dollars=round(ac_pnl, 2),
                win_rate=round(ac_wr, 1),
                profit_factor=round(ac_pf, 2),
                pct_of_total_pnl=round(ac_share, 1),
            )
        )

    # 4. Sector / Cluster Attribution
    sector_trades_map: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        sec = SECTOR_MAP.get(t.symbol, SECTOR_MAP.get(t.symbol.upper(), "Other / Diversified"))
        sector_trades_map.setdefault(sec, []).append(t)

    sector_attributions: list[AssetClassAttribution] = []
    for sec_name, s_trades in sector_trades_map.items():
        s_pnl = sum(t.pnl_dollars or 0.0 for t in s_trades)
        s_wr = calculate_win_rate(s_trades)
        s_pf = calculate_profit_factor(s_trades)
        s_share = (s_pnl / total_alpha * 100.0) if total_alpha > 0 and s_pnl > 0 else 0.0

        sector_attributions.append(
            AssetClassAttribution(
                name=sec_name,
                trade_count=len(s_trades),
                pnl_dollars=round(s_pnl, 2),
                win_rate=round(s_wr, 1),
                profit_factor=round(s_pf, 2),
                pct_of_total_pnl=round(s_share, 1),
            )
        )
    sector_attributions.sort(key=lambda s: s.pnl_dollars, reverse=True)

    return PerformanceAttributionResult(
        factors=factor_attributions,
        regimes=regime_attributions,
        asset_classes=asset_class_attributions,
        sectors=sector_attributions,
    )
