from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class GammaRegime(str, Enum):
    POSITIVE_GAMMA = "POSITIVE_GAMMA"  # Volatility suppressed, dealer dip-buying / mean-reversion
    NEGATIVE_GAMMA = "NEGATIVE_GAMMA"  # Volatility amplified, dealer momentum selling / breakout acceleration
    NEUTRAL = "NEUTRAL"


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class OptionContract(BaseModel):
    strike: float
    expiration: str
    option_type: OptionType
    bid: float = 0.0
    ask: float = 0.0
    last_price: float = 0.0
    implied_volatility: float = 0.0
    open_interest: int = 0
    volume: int = 0
    gamma: float = 0.0


class StrikeGEX(BaseModel):
    strike: float
    call_gex: float = 0.0  # in $ Millions per 1% move (+ value)
    put_gex: float = 0.0  # in $ Millions per 1% move (- value)
    net_gex: float = 0.0
    call_oi: int = 0
    put_oi: int = 0
    call_volume: int = 0
    put_volume: int = 0


class GammaExposureProfile(BaseModel):
    symbol: str
    underlying_price: float
    total_net_gex: float  # $ Millions per 1% move
    total_call_gex: float
    total_put_gex: float
    gamma_regime: GammaRegime
    gamma_flip_strike: float | None = None
    call_wall_strike: float
    put_wall_strike: float
    call_wall_oi: int = 0
    put_wall_oi: int = 0
    pcr_open_interest: float = 0.0
    pcr_volume: float = 0.0
    strikes: list[StrikeGEX] = Field(default_factory=list)
    expirations_analyzed: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
