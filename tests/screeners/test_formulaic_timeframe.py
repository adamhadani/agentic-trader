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


# Scan bars on delayed SIP: every request ends 16 minutes back, so at 10:35 New York the 10:15 15m bar holds
# data only to 10:19. It must not count as complete; the 10:00 bar (closed 10:15) is the latest complete one.
DECISION = datetime(2026, 11, 9, 15, 35, tzinfo=UTC)  # 10:35 New York
LAST_BAR = pd.Timestamp("2026-11-09 15:15", tz="UTC")  # the 10:15 New York bar, truncated by the delay
LAST_COMPLETE = LAST_BAR - pd.Timedelta(minutes=15)  # the 10:00 New York bar


def _fifteen_minute_through_ten_fifteen() -> ContractMarketData:
    index = pd.date_range(end=LAST_BAR, periods=50, freq="15min")
    closes = 100.0 + np.arange(50) * 0.01
    closes[-2:] += 25.0  # both candidate last bars spike, so either one triggers
    frame = pd.DataFrame(
        {"Open": closes, "High": closes + 1.0, "Low": closes - 1.0, "Close": closes, "Volume": 10_000.0}, index=index
    )
    return ContractMarketData(contract="SPY", ticker="SPY", fifteen_minute=frame)


def _fifteen_minute_alpha() -> AlphaDefinition:
    return AlphaDefinition(
        alpha_id="alpha_test_15m_clock",
        name="Test 15m clock",
        expression="close - ts_min(close, 5)",
        timeframe="15m",
        direction="long",
        entry_threshold=0.5,
    )


def test_under_a_bar_delay_the_truncated_15m_bar_is_not_complete():
    strategy = FormulaicAlphaStrategy(
        _fifteen_minute_alpha(), clock=lambda: DECISION, bars_end_delay=timedelta(minutes=16)
    )

    [candidate] = strategy.evaluate(_fifteen_minute_through_ten_fifteen(), asset_class=AssetClass.EQUITY)

    assert pd.Timestamp(candidate.candle_timestamp) == LAST_COMPLETE


def test_without_a_bar_delay_completeness_is_unchanged():
    strategy = FormulaicAlphaStrategy(_fifteen_minute_alpha(), clock=lambda: DECISION)

    [candidate] = strategy.evaluate(_fifteen_minute_through_ten_fifteen(), asset_class=AssetClass.EQUITY)

    assert pd.Timestamp(candidate.candle_timestamp) == LAST_BAR


def test_under_a_bar_delay_the_cap_uses_the_receipt_not_a_later_evaluation_time():
    """Evaluation runs after the whole universe is fetched, possibly minutes later: the bars hold nothing after
    their receipt - delay, so a later evaluation clock must not admit the truncated 10:15 bar."""
    data = _fifteen_minute_through_ten_fifteen()
    data.received_at = DECISION
    strategy = FormulaicAlphaStrategy(
        _fifteen_minute_alpha(), clock=lambda: DECISION + timedelta(minutes=12), bars_end_delay=timedelta(minutes=16)
    )

    [candidate] = strategy.evaluate(data, asset_class=AssetClass.EQUITY)

    assert pd.Timestamp(candidate.candle_timestamp) == LAST_COMPLETE
