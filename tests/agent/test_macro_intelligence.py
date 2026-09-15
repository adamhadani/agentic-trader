from unittest.mock import AsyncMock, patch

import pytest

from agentic_trader.agent.macro import (
    CreditSpreads,
    CreditStressRegime,
    FredDataClient,
    InflationExpectations,
    InflationRegime,
    MacroIntelligenceEngine,
    MacroIntelligenceReport,
    MacroStressLevel,
    TreasuryYields,
    YieldCurveRegime,
    YieldCurveSpreads,
    calculate_yield_curve_spreads,
    classify_credit_stress,
    classify_inflation_expectations,
    compute_compound_macro_stress,
)
from agentic_trader.config import RegimeConfig


def test_yield_curve_spreads_normal():
    yields = TreasuryYields(
        yield_3m=3.90,
        yield_2y=4.30,
        yield_5y=4.60,
        yield_10y=4.90,
        yield_30y=5.20,
    )
    spreads = calculate_yield_curve_spreads(yields)

    # 10Y - 2Y = 4.90 - 4.30 = 0.60% (60 bps)
    assert pytest.approx(spreads.slope_10y_2y_pct, abs=0.01) == 0.60
    assert pytest.approx(spreads.slope_10y_2y_bps, abs=0.1) == 60.0

    # 10Y - 3M = 4.90 - 3.90 = 1.00% (100 bps)
    assert pytest.approx(spreads.slope_10y_3m_bps, abs=0.1) == 100.0

    # Curvature butterfly: 2 * 5Y - (2Y + 10Y) = 2 * 4.60 - (4.30 + 4.90) = 9.20 - 9.20 = 0.0 bps
    assert pytest.approx(spreads.curvature_butterfly_bps, abs=0.1) == 0.0

    assert spreads.regime == YieldCurveRegime.NORMAL_STEEP


def test_yield_curve_spreads_inverted():
    # Inverted 10Y-2Y: 2Y at 4.80%, 10Y at 4.20% (-60 bps)
    yields = TreasuryYields(
        yield_3m=5.00,
        yield_2y=4.80,
        yield_5y=4.40,
        yield_10y=4.20,
        yield_30y=4.40,
    )
    spreads = calculate_yield_curve_spreads(yields)

    assert spreads.slope_10y_2y_bps < 0.0
    assert spreads.regime == YieldCurveRegime.INVERTED


def test_yield_curve_spreads_flat():
    # Flat 10Y-2Y: 10 bps
    yields = TreasuryYields(
        yield_3m=4.20,
        yield_2y=4.30,
        yield_5y=4.35,
        yield_10y=4.40,
        yield_30y=4.60,
    )
    spreads = calculate_yield_curve_spreads(yields)

    assert pytest.approx(spreads.slope_10y_2y_bps, abs=0.1) == 10.0
    assert spreads.regime == YieldCurveRegime.FLAT


def test_yield_curve_spreads_steep():
    # Steep 10Y-2Y: 180 bps
    yields = TreasuryYields(
        yield_3m=3.00,
        yield_2y=3.20,
        yield_5y=4.20,
        yield_10y=5.00,
        yield_30y=5.40,
    )
    spreads = calculate_yield_curve_spreads(yields)

    assert pytest.approx(spreads.slope_10y_2y_bps, abs=0.1) == 180.0
    assert spreads.regime == YieldCurveRegime.STEEP


def test_classify_credit_stress():
    # Benign: < 350 bps
    credit_low = classify_credit_stress(2.80)
    assert credit_low.regime == CreditStressRegime.BENIGN
    assert pytest.approx(credit_low.high_yield_oas_bps, abs=0.1) == 280.0

    # Elevated: 350 to 500 bps
    credit_mid = classify_credit_stress(4.20)
    assert credit_mid.regime == CreditStressRegime.ELEVATED
    assert pytest.approx(credit_mid.high_yield_oas_bps, abs=0.1) == 420.0

    # Critical: > 500 bps
    credit_high = classify_credit_stress(5.60)
    assert credit_high.regime == CreditStressRegime.CRITICAL
    assert pytest.approx(credit_high.high_yield_oas_bps, abs=0.1) == 560.0


def test_classify_inflation_expectations():
    # Anchored: 2.0% - 2.6%
    inf_anchored = classify_inflation_expectations(2.35, 2.38)
    assert inf_anchored.regime == InflationRegime.ANCHORED

    # Low deflection: < 2.0%
    inf_low = classify_inflation_expectations(1.80, 1.85)
    assert inf_low.regime == InflationRegime.DEFLECTION_LOW

    # Elevated: > 2.6%
    inf_elevated = classify_inflation_expectations(2.90, 2.95)
    assert inf_elevated.regime == InflationRegime.ELEVATED


def test_compound_macro_stress_scoring():
    # Low stress conditions
    assessment_low = compute_compound_macro_stress(
        vix=16.0,
        yield_spreads=YieldCurveSpreads(
            slope_10y_2y_pct=0.60,
            slope_10y_2y_bps=60.0,
            slope_10y_3m_bps=80.0,
            curvature_butterfly_bps=5.0,
            regime=YieldCurveRegime.NORMAL_STEEP,
        ),
        credit=CreditSpreads(high_yield_oas_pct=2.70, high_yield_oas_bps=270.0, regime=CreditStressRegime.BENIGN),
        inflation=InflationExpectations(breakeven_10y=2.30, breakeven_5y=2.35, regime=InflationRegime.ANCHORED),
    )
    assert assessment_low.level == MacroStressLevel.LOW
    assert assessment_low.risk_multiplier == 1.0
    assert assessment_low.squeeze_breakout_allowed is True
    assert assessment_low.min_rr_threshold == 2.0

    # Moderate stress conditions (VIX 21, flat curve, benign credit)
    assessment_mod = compute_compound_macro_stress(
        vix=21.0,
        yield_spreads=YieldCurveSpreads(
            slope_10y_2y_pct=0.10,
            slope_10y_2y_bps=10.0,
            slope_10y_3m_bps=15.0,
            curvature_butterfly_bps=-10.0,
            regime=YieldCurveRegime.FLAT,
        ),
        credit=CreditSpreads(high_yield_oas_pct=3.10, high_yield_oas_bps=310.0, regime=CreditStressRegime.BENIGN),
        inflation=InflationExpectations(breakeven_10y=2.40, breakeven_5y=2.45, regime=InflationRegime.ANCHORED),
    )
    assert assessment_mod.level == MacroStressLevel.MODERATE
    assert assessment_mod.risk_multiplier == 0.8
    assert assessment_mod.squeeze_breakout_allowed is True

    # High stress conditions (Inverted curve, elevated credit)
    assessment_high = compute_compound_macro_stress(
        vix=26.0,
        yield_spreads=YieldCurveSpreads(
            slope_10y_2y_pct=-0.35,
            slope_10y_2y_bps=-35.0,
            slope_10y_3m_bps=-50.0,
            curvature_butterfly_bps=20.0,
            regime=YieldCurveRegime.INVERTED,
        ),
        credit=CreditSpreads(high_yield_oas_pct=4.80, high_yield_oas_bps=480.0, regime=CreditStressRegime.ELEVATED),
        inflation=InflationExpectations(breakeven_10y=2.75, breakeven_5y=2.80, regime=InflationRegime.ELEVATED),
    )
    assert assessment_high.level == MacroStressLevel.HIGH
    assert assessment_high.risk_multiplier == 0.5
    assert assessment_high.squeeze_breakout_allowed is False
    assert assessment_high.min_rr_threshold >= 2.2

    # Extreme stress conditions (VIX > 30, Critical Credit)
    assessment_extreme = compute_compound_macro_stress(
        vix=35.0,
        yield_spreads=YieldCurveSpreads(
            slope_10y_2y_pct=-0.50,
            slope_10y_2y_bps=-50.0,
            slope_10y_3m_bps=-80.0,
            curvature_butterfly_bps=30.0,
            regime=YieldCurveRegime.INVERTED,
        ),
        credit=CreditSpreads(high_yield_oas_pct=6.20, high_yield_oas_bps=620.0, regime=CreditStressRegime.CRITICAL),
        inflation=InflationExpectations(breakeven_10y=2.90, breakeven_5y=2.95, regime=InflationRegime.ELEVATED),
    )
    assert assessment_extreme.level == MacroStressLevel.EXTREME
    assert assessment_extreme.risk_multiplier == 0.25
    assert assessment_extreme.squeeze_breakout_allowed is False
    assert assessment_extreme.min_rr_threshold >= 2.5


@pytest.mark.asyncio
async def test_fred_data_client_csv_parsing():
    client = FredDataClient()

    sample_csv = (
        "DATE,BAMLH0A0HYM2\n"
        "2026-09-08,2.75\n"
        "2026-09-09,2.71\n"
        "2026-09-10,2.70\n"
        "2026-09-11,2.65\n"
        "2026-09-12,.\n"  # Holiday or weekend dot
    )

    with patch.object(client, "_fetch_csv_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = sample_csv

        val = await client.fetch_latest_value("BAMLH0A0HYM2")
        assert val == 2.65

        # Verify caching
        val_cached = await client.fetch_latest_value("BAMLH0A0HYM2")
        assert val_cached == 2.65
        assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_fred_data_client_fallback_on_error():
    client = FredDataClient()

    with patch.object(client, "_fetch_csv_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = Exception("Connection timeout")

        val = await client.fetch_latest_value("BAMLH0A0HYM2", fallback_default=3.20)
        assert val == 3.20


@pytest.mark.asyncio
async def test_macro_intelligence_engine_end_to_end():
    config = RegimeConfig(cache_ttl_seconds=300)
    engine = MacroIntelligenceEngine(config=config)

    # Mock yfinance ticker fetch
    mock_yields = {
        "^IRX": 3.95,
        "2YY=F": 4.35,
        "^FVX": 4.65,
        "^TNX": 4.95,
        "^TYX": 5.25,
        "^VIX": 17.2,
        "DX-Y.NYB": 103.4,
    }

    def mock_fetch_ticker(t):
        return mock_yields.get(t, 4.0)

    # Mock FRED client
    mock_fred_data = {
        "BAMLH0A0HYM2": 2.65,
        "T10YIE": 2.37,
        "T5YIE": 2.40,
    }

    async def mock_fetch_fred(series_id, fallback_default=None):
        return mock_fred_data.get(series_id, fallback_default)

    with (
        patch("agentic_trader.agent.macro._fetch_ticker_sync", side_effect=mock_fetch_ticker),
        patch.object(engine.fred_client, "fetch_latest_value", side_effect=mock_fetch_fred),
    ):
        report: MacroIntelligenceReport = await engine.get_macro_report(force_refresh=True)

        assert isinstance(report, MacroIntelligenceReport)
        assert report.yields.yield_10y == 4.95
        assert report.yields.yield_2y == 4.35
        assert pytest.approx(report.spreads.slope_10y_2y_bps, abs=0.1) == 60.0
        assert report.spreads.regime == YieldCurveRegime.NORMAL_STEEP
        assert report.credit.high_yield_oas_pct == 2.65
        assert report.credit.regime == CreditStressRegime.BENIGN
        assert report.inflation.breakeven_10y == 2.37
        assert report.inflation.regime == InflationRegime.ANCHORED
        assert report.vix == 17.2
        assert report.dxy == 103.4
        assert report.stress.level == MacroStressLevel.LOW
        assert report.stress.risk_multiplier == 1.0
        assert report.stress.squeeze_breakout_allowed is True
        assert "LOW" in report.summary_text
        assert "NORMAL_STEEP" in report.summary_text
