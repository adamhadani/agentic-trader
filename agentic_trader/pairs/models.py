"""Data models for Cointegration & Statistical Pairs Trading."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SignalType(str, Enum):
    """Pairs trading spread signal type."""

    BUY_SPREAD = "BUY_SPREAD"  # Long Asset Y, Short Asset X
    SELL_SPREAD = "SELL_SPREAD"  # Short Asset Y, Long Asset X
    EXIT_SPREAD = "EXIT_SPREAD"  # Mean-reverted: Exit open position
    NEUTRAL = "NEUTRAL"  # In-band, no actionable statistical deviation


class CointegrationResult(BaseModel):
    """Result of Engle-Granger two-step cointegration test."""

    asset_y: str
    asset_x: str
    hedge_ratio_beta: float
    intercept_alpha: float
    adf_statistic: float
    p_value: float
    critical_values: dict[str, float] = Field(default_factory=dict)
    half_life_bars: float
    is_cointegrated: bool


class SpreadSignal(BaseModel):
    """Real-time spread measurement and trading signal."""

    pair_name: str
    timestamp: datetime
    current_spread: float
    spread_mean: float
    spread_std: float
    z_score: float
    signal: SignalType
    asset_y_action: str
    asset_x_action: str
    summary: str


class PairEvaluation(BaseModel):
    """Comprehensive statistical arbitrage evaluation of an asset pair."""

    pair_name: str
    asset_y: str
    asset_x: str
    coint_result: CointegrationResult
    signal: SpreadSignal
    lookback_bars: int
    is_actionable: bool = False
