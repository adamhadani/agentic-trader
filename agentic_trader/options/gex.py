from __future__ import annotations

import math
from typing import Any

import pandas as pd

from agentic_trader.options.models import GammaExposureProfile, GammaRegime, StrikeGEX


def black_scholes_gamma(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float = 0.045,
    implied_volatility: float = 0.20,
) -> float:
    """Calculates analytical Black-Scholes Gamma (second derivative of option price w.r.t spot).

    Gamma is identical for both call and put options with matching strikes and expirations.
    """
    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 1e-6 or implied_volatility <= 1e-4:
        return 0.0

    try:
        t_sqrt = math.sqrt(time_to_expiry_years)
        sigma_t = implied_volatility * t_sqrt
        d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * (implied_volatility**2)) * time_to_expiry_years) / (
            sigma_t
        )

        pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
        gamma = pdf_d1 / (spot * sigma_t)
        return float(gamma) if not math.isnan(gamma) and not math.isinf(gamma) else 0.0
    except ValueError, OverflowError, ZeroDivisionError:
        return 0.0


class GEXCalculator:
    """Computes aggregate market maker Gamma Exposure (GEX), Call/Put Walls,

    Gamma Flip levels, and Put/Call positioning metrics from option chains.
    """

    def __init__(self, risk_free_rate: float = 0.045):
        self.risk_free_rate = risk_free_rate

    def calculate_gex(
        self,
        symbol: str,
        underlying_price: float,
        calls_df: pd.DataFrame,
        puts_df: pd.DataFrame,
        expirations: list[str] | None = None,
    ) -> GammaExposureProfile:
        """Processes normalized calls and puts DataFrames into a comprehensive GammaExposureProfile.

        Calls and puts DataFrames are expected to have:
        - 'strike': float
        - 'openInterest': int/float
        - 'volume': int/float (optional)
        - 'impliedVolatility': float (optional, default 0.20)
        - 'dte_years' or 'time_to_expiry': float (optional, default 14/365)
        """
        if underlying_price <= 0:
            raise ValueError(f"Underlying price must be positive, got {underlying_price}")

        strike_data: dict[float, dict[str, Any]] = {}

        # 1. Process Calls (Dealer is assumed short customer calls -> Long Gamma for market stability)
        if not calls_df.empty:
            for _, row in calls_df.iterrows():
                k = float(row.get("strike", 0.0))
                if k <= 0:
                    continue

                oi = int(row.get("openInterest", 0) or 0)
                vol = int(row.get("volume", 0) or 0)
                iv = float(row.get("impliedVolatility", 0.20) or 0.20)
                dte = float(row.get("dte_years", row.get("time_to_expiry", 14.0 / 365.0)) or 14.0 / 365.0)

                gamma = black_scholes_gamma(underlying_price, k, dte, self.risk_free_rate, iv)
                # Call GEX: Gamma * OI * 100 shares * spot^2 * 0.01 / 1,000,000 ($ Millions / 1% move)
                call_gex = gamma * oi * 100.0 * (underlying_price**2) * 0.01 / 1_000_000.0

                if k not in strike_data:
                    strike_data[k] = {
                        "call_gex": 0.0,
                        "put_gex": 0.0,
                        "call_oi": 0,
                        "put_oi": 0,
                        "call_volume": 0,
                        "put_volume": 0,
                    }
                strike_data[k]["call_gex"] += call_gex
                strike_data[k]["call_oi"] += oi
                strike_data[k]["call_volume"] += vol

        # 2. Process Puts (Dealer is assumed short customer puts -> Short Gamma / Volatility Accelerator)
        if not puts_df.empty:
            for _, row in puts_df.iterrows():
                k = float(row.get("strike", 0.0))
                if k <= 0:
                    continue

                oi = int(row.get("openInterest", 0) or 0)
                vol = int(row.get("volume", 0) or 0)
                iv = float(row.get("impliedVolatility", 0.20) or 0.20)
                dte = float(row.get("dte_years", row.get("time_to_expiry", 14.0 / 365.0)) or 14.0 / 365.0)

                gamma = black_scholes_gamma(underlying_price, k, dte, self.risk_free_rate, iv)
                # Put GEX is negative dealer gamma
                put_gex = -(gamma * oi * 100.0 * (underlying_price**2) * 0.01 / 1_000_000.0)

                if k not in strike_data:
                    strike_data[k] = {
                        "call_gex": 0.0,
                        "put_gex": 0.0,
                        "call_oi": 0,
                        "put_oi": 0,
                        "call_volume": 0,
                        "put_volume": 0,
                    }
                strike_data[k]["put_gex"] += put_gex
                strike_data[k]["put_oi"] += oi
                strike_data[k]["put_volume"] += vol

        # Build sorted list of StrikeGEX
        sorted_strikes = sorted(strike_data.keys())
        strike_models: list[StrikeGEX] = []
        total_call_gex = 0.0
        total_put_gex = 0.0
        total_call_oi = 0
        total_put_oi = 0
        total_call_vol = 0
        total_put_vol = 0

        max_call_oi = -1
        call_wall_strike = underlying_price
        max_put_oi = -1
        put_wall_strike = underlying_price

        for k in sorted_strikes:
            item = strike_data[k]
            c_gex = round(item["call_gex"], 4)
            p_gex = round(item["put_gex"], 4)
            net = round(c_gex + p_gex, 4)
            c_oi = item["call_oi"]
            p_oi = item["put_oi"]
            c_vol = item["call_volume"]
            p_vol = item["put_volume"]

            total_call_gex += c_gex
            total_put_gex += p_gex
            total_call_oi += c_oi
            total_put_oi += p_oi
            total_call_vol += c_vol
            total_put_vol += p_vol

            if c_oi > max_call_oi:
                max_call_oi = c_oi
                call_wall_strike = k

            if p_oi > max_put_oi:
                max_put_oi = p_oi
                put_wall_strike = k

            strike_models.append(
                StrikeGEX(
                    strike=k,
                    call_gex=c_gex,
                    put_gex=p_gex,
                    net_gex=net,
                    call_oi=c_oi,
                    put_oi=p_oi,
                    call_volume=c_vol,
                    put_volume=p_vol,
                )
            )

        total_net_gex = round(total_call_gex + total_put_gex, 2)
        total_call_gex = round(total_call_gex, 2)
        total_put_gex = round(total_put_gex, 2)

        # Gamma Regime determination
        if total_net_gex > 5.0:
            regime = GammaRegime.POSITIVE_GAMMA
        elif total_net_gex < -5.0:
            regime = GammaRegime.NEGATIVE_GAMMA
        else:
            regime = GammaRegime.NEUTRAL

        # Gamma Flip Level (zero-crossing strike of cumulative or strike net GEX)
        gamma_flip = self._find_gamma_flip(strike_models, underlying_price)

        # Put/Call Ratios
        pcr_oi = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 0.0
        pcr_vol = round(total_put_vol / total_call_vol, 3) if total_call_vol > 0 else 0.0

        return GammaExposureProfile(
            symbol=symbol,
            underlying_price=round(underlying_price, 2),
            total_net_gex=total_net_gex,
            total_call_gex=total_call_gex,
            total_put_gex=total_put_gex,
            gamma_regime=regime,
            gamma_flip_strike=round(gamma_flip, 2) if gamma_flip is not None else None,
            call_wall_strike=call_wall_strike,
            put_wall_strike=put_wall_strike,
            call_wall_oi=max(0, max_call_oi),
            put_wall_oi=max(0, max_put_oi),
            pcr_open_interest=pcr_oi,
            pcr_volume=pcr_vol,
            strikes=strike_models,
            expirations_analyzed=expirations or [],
        )

    def _find_gamma_flip(self, strike_models: list[StrikeGEX], spot: float) -> float | None:
        """Finds the price level where net gamma transitions from negative to positive.

        Uses linear interpolation between adjacent strikes.
        """
        if len(strike_models) < 2:
            return None

        # Look for sign changes in net_gex
        for i in range(len(strike_models) - 1):
            s1 = strike_models[i]
            s2 = strike_models[i + 1]

            if s1.net_gex <= 0 < s2.net_gex:
                # Upward crossing (negative to positive)
                denom = s2.net_gex - s1.net_gex
                if denom > 0:
                    fraction = (0.0 - s1.net_gex) / denom
                    return s1.strike + fraction * (s2.strike - s1.strike)
            elif s1.net_gex >= 0 > s2.net_gex:
                # Downward crossing
                denom = s1.net_gex - s2.net_gex
                if denom > 0:
                    fraction = (s1.net_gex - 0.0) / denom
                    return s1.strike + fraction * (s2.strike - s1.strike)

        # If no single strike crossed zero, check cumulative gamma zero-crossing
        cum_gex = 0.0
        prev_cum = 0.0
        prev_strike = strike_models[0].strike

        for s in strike_models:
            cum_gex += s.net_gex
            if (prev_cum < 0 <= cum_gex) or (prev_cum > 0 >= cum_gex):
                denom = abs(cum_gex - prev_cum)
                if denom > 0:
                    fraction = abs(prev_cum) / denom
                    return prev_strike + fraction * (s.strike - prev_strike)
            prev_cum = cum_gex
            prev_strike = s.strike

        return None
