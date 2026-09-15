from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy


def _create_candles_with_trend(n_bars: int = 50, spike_last: bool = False) -> pd.DataFrame:
    base = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)
    dates = [base + timedelta(minutes=15 * i) for i in range(n_bars)]
    np.random.seed(42)
    closes = 100.0 + np.cumsum(np.random.normal(0.05, 0.2, n_bars))
    if spike_last:
        closes[-1] += 25.0
    highs = closes + 1.0
    lows = closes - 1.0
    opens = (highs + lows) / 2.0
    vols = np.full(n_bars, 10000.0)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=pd.DatetimeIndex(dates),
    )


def test_formulaic_strategy_routes_to_15m():
    """Verify an alpha defined with timeframe='15m' evaluates against data.fifteen_minute."""
    defn_15m = AlphaDefinition(
        alpha_id="alpha_test_15m",
        name="Test 15m Momentum",
        expression="close - ts_min(close, 5)",
        timeframe="15m",
        direction="long",
        entry_threshold=0.5,
    )
    strategy = FormulaicAlphaStrategy(definition=defn_15m)

    # 15m has the spike; 4h and daily do not
    df_15m = _create_candles_with_trend(50, spike_last=True)
    df_4h = _create_candles_with_trend(50, spike_last=False)
    df_daily = _create_candles_with_trend(50, spike_last=False)

    cmd = ContractMarketData(
        contract="SPY",
        ticker="SPY",
        daily=df_daily,
        four_hour=df_4h,
        hourly=df_4h,
        fifteen_minute=df_15m,
    )

    candidates = strategy.evaluate(cmd, asset_class=AssetClass.EQUITY)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.timeframe == "15m"
    assert cand.strategy == "alpha_test_15m"
    assert cand.direction == "LONG"


def test_formulaic_strategy_routes_to_1h():
    """Verify an alpha defined with timeframe='1h' evaluates against data.hourly."""
    defn_1h = AlphaDefinition(
        alpha_id="alpha_test_1h",
        name="Test 1h Momentum",
        expression="close - ts_min(close, 5)",
        timeframe="1h",
        direction="long",
        entry_threshold=0.5,
    )
    strategy = FormulaicAlphaStrategy(definition=defn_1h)

    # 1h has the spike; 4h and daily do not
    df_1h = _create_candles_with_trend(50, spike_last=True)
    df_4h = _create_candles_with_trend(50, spike_last=False)
    df_daily = _create_candles_with_trend(50, spike_last=False)

    cmd = ContractMarketData(
        contract="QQQ",
        ticker="QQQ",
        daily=df_daily,
        four_hour=df_4h,
        hourly=df_1h,
        fifteen_minute=pd.DataFrame(),
    )

    candidates = strategy.evaluate(cmd, asset_class=AssetClass.EQUITY)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.timeframe == "1h"
    assert cand.strategy == "alpha_test_1h"
