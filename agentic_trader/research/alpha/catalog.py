from __future__ import annotations

from agentic_trader.research.alpha.models import AlphaDefinition, AlphaOrigin


INSTITUTIONAL_ALPHA_CATALOG: list[AlphaDefinition] = [
    AlphaDefinition(
        alpha_id="alpha_wq_001",
        name="Adapted Price-Volume Momentum Alignment (legacy ID 001)",
        expression="ts_argmax(ts_corr(ts_rank(volume, 5), ts_rank(close, 5), 10), 5)",
        description="Measures the recency of peak positive correlation between rolling price and volume ranks.",
        direction="bi_directional",
        entry_threshold=0.8,
        timeframe="4h",
        origin=AlphaOrigin.FACTOR_LIBRARY,
    ),
    AlphaDefinition(
        alpha_id="alpha_wq_006",
        name="WQ-006 Open-Volume Absorption Divergence",
        expression="-1.0 * ts_corr(open, volume, 10)",
        description="Identifies institutional volume absorption when heavy volume fails to sustain opening momentum.",
        direction="bi_directional",
        entry_threshold=0.6,
        timeframe="4h",
        origin=AlphaOrigin.WORLDQUANT_101,
    ),
    AlphaDefinition(
        alpha_id="alpha_wq_012",
        name="WQ-012 Volume Flow Delta Reversal",
        expression="sign(delta(volume, 1)) * (-1.0 * delta(close, 1))",
        description="Fades short-term price spikes that occur on surging volume, capturing mean-reversion liquidity.",
        direction="bi_directional",
        entry_threshold=1.0,
        timeframe="4h",
        origin=AlphaOrigin.WORLDQUANT_101,
    ),
    AlphaDefinition(
        alpha_id="alpha_wq_028",
        name="Adapted Smoothed Trend-Volume Alignment (legacy ID 028)",
        expression="ts_corr(sma(close, 20), sma(volume, 20), 5)",
        description="Correlates intermediate moving averages of price and volume to identify institutional accumulation.",
        direction="long",
        entry_threshold=0.5,
        timeframe="4h",
        origin=AlphaOrigin.FACTOR_LIBRARY,
    ),
    AlphaDefinition(
        alpha_id="alpha_wq_053",
        name="Adapted Volume Exhaustion Counter-Trend (legacy ID 053)",
        expression="-1.0 * delta(ts_rank(volume, 10), 5)",
        description="Detects exhaustion in volume rank acceleration as an early warning of trend exhaustion.",
        direction="bi_directional",
        entry_threshold=0.7,
        timeframe="4h",
        origin=AlphaOrigin.FACTOR_LIBRARY,
    ),
    AlphaDefinition(
        alpha_id="alpha_vol_reversal",
        name="Quant-VR Volume-Weighted Mean Reversion",
        expression="-1.0 * delta(close, 3) * ts_rank(volume, 10)",
        description="Combines 3-bar price displacement with rolling volume rank to enter high-conviction reversions.",
        direction="bi_directional",
        entry_threshold=1.2,
        timeframe="4h",
        origin=AlphaOrigin.FACTOR_LIBRARY,
    ),
    AlphaDefinition(
        alpha_id="alpha_trend_expansion",
        name="Quant-TE Trend-Volume Range Expansion",
        expression="ts_rank(close - sma(close, 20), 10) * ts_rank(volume, 10)",
        description="Quantifies joint price expansion away from moving average confirmed by volume surge.",
        direction="long",
        entry_threshold=0.6,
        timeframe="4h",
        origin=AlphaOrigin.FACTOR_LIBRARY,
    ),
]


class AlphaCatalog:
    """Repository of pre-cataloged institutional formulaic alphas."""

    def __init__(self, catalog: list[AlphaDefinition] | None = None) -> None:
        self._catalog: dict[str, AlphaDefinition] = {
            a.alpha_id.lower(): a for a in (catalog or INSTITUTIONAL_ALPHA_CATALOG)
        }

    def list_alphas(self) -> list[AlphaDefinition]:
        """Return all cataloged alphas."""
        return list(self._catalog.values())

    def get(self, alpha_id: str) -> AlphaDefinition | None:
        """Retrieve alpha definition by ID (case-insensitive)."""
        return self._catalog.get(alpha_id.lower())

    def list_alpha_ids(self) -> list[str]:
        """Return list of alpha IDs."""
        return sorted(self._catalog.keys())
