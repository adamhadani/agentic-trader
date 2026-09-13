"""Unit tests for Cointegration & Statistical Pairs Trading Screener (Phase 22)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.config import PairsConfig
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.pairs import (
    CointegrationResult,
    PairEvaluation,
    PairsScreener,
    SignalType,
    SpreadSignal,
    calculate_rolling_spread_zscore,
    compute_half_life,
    format_pairs_report,
    format_pairs_telegram,
    generate_spread_signal,
    run_engle_granger_test,
)


def _generate_synthetic_cointegrated_pair(n: int = 300, beta: float = 1.8, alpha: float = 25.0):
    """Generate synthetic cointegrated series Y and X."""
    np.random.seed(42)
    # X is a random walk
    innovations_x = np.random.normal(0, 1, n)
    x = np.cumsum(innovations_x) + 100.0

    # Residuals follow stationary AR(1) process with mean reversion (rho = 0.6)
    residuals = np.zeros(n)
    for t in range(1, n):
        residuals[t] = 0.6 * residuals[t - 1] + np.random.normal(0, 0.8)

    y = beta * x + alpha + residuals

    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    series_x = pd.Series(x, index=dates, name="Asset_X")
    series_y = pd.Series(y, index=dates, name="Asset_Y")
    return series_y, series_x, beta, alpha


def _generate_synthetic_independent_walks(n: int = 300):
    """Generate two independent random walks."""
    np.random.seed(123)
    x = np.cumsum(np.random.normal(0, 1, n)) + 100.0
    y = np.cumsum(np.random.normal(0, 1, n)) + 150.0
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(y, index=dates, name="Walk_Y"), pd.Series(x, index=dates, name="Walk_X")


def test_engle_granger_detects_cointegration():
    """Verify Engle-Granger test identifies cointegrated series and estimates beta/alpha."""
    series_y, series_x, true_beta, true_alpha = _generate_synthetic_cointegrated_pair(n=350, beta=1.8, alpha=25.0)

    res = run_engle_granger_test(series_y, series_x, "Y_SYM", "X_SYM", p_value_threshold=0.05)

    assert res.is_cointegrated is True
    assert res.p_value < 0.05
    assert res.adf_statistic < -2.8  # Strong negative ADF statistic
    assert abs(res.hedge_ratio_beta - true_beta) < 0.1
    assert abs(res.intercept_alpha - true_alpha) < 2.0
    assert res.half_life_bars < 10.0


def test_engle_granger_rejects_independent_walks():
    """Verify Engle-Granger test rejects cointegration for independent random walks."""
    series_y, series_x = _generate_synthetic_independent_walks(n=300)

    res = run_engle_granger_test(series_y, series_x, "Walk_Y", "Walk_X", p_value_threshold=0.05)

    # Independent random walks should have non-stationary residuals (high p-value)
    assert res.p_value > 0.05
    assert res.is_cointegrated is False


def test_compute_half_life_math():
    """Verify Ornstein-Uhlenbeck / AR(1) half-life math."""
    np.random.seed(42)
    # Generate AR(1) with rho = 0.75 -> theta = -0.25 -> Theoretical half life = -ln(2) / -0.25 ≈ 2.77
    n = 1000
    res = np.zeros(n)
    for t in range(1, n):
        res[t] = 0.75 * res[t - 1] + np.random.normal(0, 0.5)

    half_life = compute_half_life(pd.Series(res))
    assert 2.0 < half_life < 4.0

    # Non-mean-reverting explosive/trending series (rho > 1.0 or theta >= 0) -> infinite half-life
    trending = pd.Series([100.0 * (1.02**t) for t in range(50)])
    trend_hl = compute_half_life(trending)
    assert np.isinf(trend_hl)


def test_calculate_rolling_spread_zscore():
    """Verify rolling spread and Z-score calculations."""
    series_y, series_x, beta, alpha = _generate_synthetic_cointegrated_pair(n=100, beta=1.5, alpha=10.0)

    df = calculate_rolling_spread_zscore(series_y, series_x, beta=beta, alpha=alpha, lookback=20)

    assert "spread" in df.columns
    assert "spread_mean" in df.columns
    assert "spread_std" in df.columns
    assert "z_score" in df.columns
    assert len(df) == 100

    # Z-scores should have mean near 0 and std near 1
    valid_z = df["z_score"].dropna()
    assert abs(valid_z.mean()) < 0.5
    assert 0.5 < valid_z.std() < 1.5


def test_generate_spread_signals():
    """Verify signal triggers across undervaluation, overvaluation, and mean reversion."""
    now = datetime.now(UTC)

    # 1. Undervalued spread Z <= -2.0 -> BUY_SPREAD
    sig_buy = generate_spread_signal("SPY/QQQ", now, current_spread=-5.0, spread_mean=0.0, spread_std=2.0, z_score=-2.5)
    assert sig_buy.signal == SignalType.BUY_SPREAD
    assert sig_buy.asset_y_action == "BUY"
    assert sig_buy.asset_x_action == "SELL"

    # 2. Overvalued spread Z >= +2.0 -> SELL_SPREAD
    sig_sell = generate_spread_signal("SPY/QQQ", now, current_spread=6.0, spread_mean=0.0, spread_std=2.0, z_score=3.0)
    assert sig_sell.signal == SignalType.SELL_SPREAD
    assert sig_sell.asset_y_action == "SELL"
    assert sig_sell.asset_x_action == "BUY"

    # 3. Mean-reverted |Z| <= 0.5 -> EXIT_SPREAD
    sig_exit = generate_spread_signal("SPY/QQQ", now, current_spread=0.4, spread_mean=0.0, spread_std=2.0, z_score=0.2)
    assert sig_exit.signal == SignalType.EXIT_SPREAD
    assert sig_exit.asset_y_action == "EXIT"
    assert sig_exit.asset_x_action == "EXIT"

    # 4. In-band 0.5 < |Z| < 2.0 -> NEUTRAL
    sig_neutral = generate_spread_signal(
        "SPY/QQQ", now, current_spread=2.4, spread_mean=0.0, spread_std=2.0, z_score=1.2
    )
    assert sig_neutral.signal == SignalType.NEUTRAL
    assert sig_neutral.asset_y_action == "HOLD"
    assert sig_neutral.asset_x_action == "HOLD"


def test_pairs_screener_evaluate_and_scan():
    """Verify PairsScreener end-to-end evaluation and candidate scanning."""
    series_y, series_x, _, _ = _generate_synthetic_cointegrated_pair(n=252, beta=1.5, alpha=10.0)

    mock_df_y = pd.DataFrame({"Open": series_y, "High": series_y, "Low": series_y, "Close": series_y, "Volume": 1000})
    mock_df_x = pd.DataFrame({"Open": series_x, "High": series_x, "Low": series_x, "Close": series_x, "Volume": 1000})

    mock_fetcher = MagicMock()
    mock_fetcher.fetch_data.side_effect = lambda contract, ticker, daily_period="2y": (
        ContractMarketData("SPY", "SPY", mock_df_y, pd.DataFrame(), pd.DataFrame())
        if "SPY" in contract
        else ContractMarketData("QQQ", "QQQ", mock_df_x, pd.DataFrame(), pd.DataFrame())
    )

    config = PairsConfig(p_value_threshold=0.05, min_half_life_bars=0.5, max_half_life_bars=60.0)
    screener = PairsScreener(data_fetcher=mock_fetcher, config=config)

    eval_res = screener.evaluate_pair("SPY", "QQQ", lookback_days=252)
    assert eval_res is not None
    assert eval_res.pair_name == "SPY/QQQ"
    assert eval_res.coint_result.is_cointegrated is True
    assert eval_res.lookback_bars == 252

    # Test pairwise combinations helper
    combs = screener.generate_pairwise_combinations(["SPY", "QQQ", "IWM"])
    assert len(combs) == 3
    assert ("SPY", "QQQ") in combs
    assert ("SPY", "IWM") in combs
    assert ("QQQ", "IWM") in combs


def test_pairs_report_formatters():
    """Verify ASCII and Telegram report formatting."""
    coint = CointegrationResult(
        asset_y="SPY",
        asset_x="QQQ",
        hedge_ratio_beta=1.42,
        intercept_alpha=12.5,
        adf_statistic=-3.85,
        p_value=0.0024,
        critical_values={"1%": -3.45, "5%": -2.87},
        half_life_bars=4.8,
        is_cointegrated=True,
    )
    sig = SpreadSignal(
        pair_name="SPY/QQQ",
        timestamp=datetime.now(UTC),
        current_spread=-4.2,
        spread_mean=0.5,
        spread_std=2.0,
        z_score=-2.35,
        signal=SignalType.BUY_SPREAD,
        asset_y_action="BUY",
        asset_x_action="SELL",
        summary="Undervalued spread: Long SPY / Short QQQ",
    )
    eval_item = PairEvaluation(
        pair_name="SPY/QQQ",
        asset_y="SPY",
        asset_x="QQQ",
        coint_result=coint,
        signal=sig,
        lookback_bars=252,
        is_actionable=True,
    )

    ascii_rep = format_pairs_report([eval_item])
    assert "STATISTICAL PAIRS TRADING" in ascii_rep
    assert "SPY/QQQ" in ascii_rep
    assert "BUY_SPREAD" in ascii_rep
    assert "1.4200" in ascii_rep

    tg_rep = format_pairs_telegram([eval_item])
    assert "Statistical Pairs Arbitrage Screener" in tg_rep
    assert "SPY/QQQ" in tg_rep
    assert "BUY_SPREAD" in tg_rep
    assert "✅ Cointegrated" in tg_rep


def test_cli_pairs_command():
    """Verify Click CLI copilot pairs subcommand execution."""
    runner = CliRunner()
    series_y, series_x, _, _ = _generate_synthetic_cointegrated_pair(n=252)

    mock_df_y = pd.DataFrame({"Open": series_y, "High": series_y, "Low": series_y, "Close": series_y, "Volume": 1000})
    mock_df_x = pd.DataFrame({"Open": series_x, "High": series_x, "Low": series_x, "Close": series_x, "Volume": 1000})

    with patch("agentic_trader.pairs.screener.MarketDataFetcher") as mock_fetcher_cls:
        instance = mock_fetcher_cls.return_value
        instance.fetch_data.side_effect = lambda contract, ticker, daily_period="2y": (
            ContractMarketData("SPY", "SPY", mock_df_y, pd.DataFrame(), pd.DataFrame())
            if "SPY" in contract
            else ContractMarketData("QQQ", "QQQ", mock_df_x, pd.DataFrame(), pd.DataFrame())
        )

        result = runner.invoke(cli, ["pairs", "--pair", "SPY/QQQ", "--json"])
        assert result.exit_code == 0
        assert "SPY/QQQ" in result.output
        assert "hedge_ratio_beta" in result.output


@pytest.mark.asyncio
async def test_telegram_pairs_command_dispatch():
    """Verify Telegram /pairs command handler executes and responds."""
    mock_pairs_provider = AsyncMock(return_value="📊 <b>Pairs Screener</b>\n• SPY/QQQ: BUY_SPREAD")
    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="123456",
        db=MagicMock(),
        pairs_provider=mock_pairs_provider,
    )

    update = MagicMock()
    update.effective_chat.id = 123456
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    await notifier.handle_pairs_command(update, context)

    assert mock_pairs_provider.called
    assert update.message.reply_text.call_count >= 2
    # Verify content delivered
    called_text = update.message.reply_text.call_args[0][0]
    assert "SPY/QQQ" in called_text
