"""Cross-sectional setup feature vector.

Pure and I/O-free so it can be imported verbatim by both the offline setup
study (replaying history) and the live suggestion scan. Every feature only
looks at sessions on or before ``as_of``: appending future rows, or reading
this module at a later date with more history collected, must never change
a value already computed for an earlier ``as_of``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.panel import group_neutralize


__all__ = [
    "CROSS_SECTIONAL",
    "DIRECTIONAL",
    "FEATURES_VERSION",
    "MARKET",
    "SECTOR_ETF",
    "SETUP",
    "CrossSection",
    "cross_section",
    "setup_vector",
]

FEATURES_VERSION = "setup_features_v1"

CROSS_SECTIONAL = (
    "mom_252_21",
    "mom_60",
    "rev_5",
    "vol_20",
    "dist_52w_high",
    "dollar_volume_20",
    "resid_mom_60",
    "sector_rel_mom_60",
)

DIRECTIONAL = frozenset({"mom_252_21", "mom_60", "rev_5", "dist_52w_high", "resid_mom_60", "sector_rel_mom_60"})

MARKET = ("spy_above_200", "spy_vol20_pct")

SETUP = ("setup_quality", "stop_atr", "reward_risk")

_SPY = "SPY"

# Frozen sector -> ETF map used for the resid_mom_60 regression. Keys are the
# `sector:` tags used under `universe.groups` in config/config.yaml. Every
# equity sector maps to its SPDR sector ETF (all present in the `etf32`
# group's membership); every `etf_*` sector -- the broad-equity, international,
# rates/credit and metals proxy groups -- maps to SPY, since those symbols
# have no single dedicated sector ETF of their own.
SECTOR_ETF: Mapping[str, str] = {
    "basic_materials": "XLB",
    "communication_services": "XLC",
    "consumer_cyclical": "XLY",
    "consumer_defensive": "XLP",
    "energy": "XLE",
    "financial_services": "XLF",
    "healthcare": "XLV",
    "industrials": "XLI",
    "real_estate": "XLRE",
    "technology": "XLK",
    "utilities": "XLU",
    "etf_broad_equity": _SPY,
    "etf_international": _SPY,
    "etf_metals": _SPY,
    "etf_rates_credit": _SPY,
}

# resid_mom_60: fit on the 126 returns ending 60 returns before the last one,
# then sum the (out-of-sample) residual over the most recent 60 returns.
_RESID_TRAIN_WINDOW = 126
_RESID_GAP = 60
_RESID_MIN_RETURNS = 187

_VOL20_WINDOW = 20
_VOL20_HISTORY = 252
_SPY_ABOVE_200_WINDOW = 200


@dataclass(frozen=True)
class CrossSection:
    as_of: date
    ranks: pd.DataFrame
    market: dict[str, float]


def _closed_frame(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Rows whose session date is on or before ``as_of``, in original order."""
    session_dates = pd.DatetimeIndex(frame.index).date
    return frame.loc[session_dates <= as_of]


def _basic_features(frame: pd.DataFrame) -> dict[str, float]:
    close = frame["Close"].to_numpy(dtype=float)
    high = frame["High"].to_numpy(dtype=float)
    volume = frame["Volume"].to_numpy(dtype=float)
    n = len(close)

    mom_252_21 = close[-22] / close[-253] - 1.0 if n >= 253 else np.nan
    mom_60 = close[-1] / close[-61] - 1.0 if n >= 61 else np.nan
    rev_5 = -(close[-1] / close[-6] - 1.0) if n >= 6 else np.nan

    if n >= _VOL20_WINDOW + 1:
        recent_close = close[-(_VOL20_WINDOW + 1) :]
        pct = recent_close[1:] / recent_close[:-1] - 1.0
        vol_20 = float(np.std(pct, ddof=1))
    else:
        vol_20 = np.nan

    dist_52w_high = close[-1] / np.max(high[-252:]) - 1.0 if n >= 252 else np.nan

    dollar_volume_20 = float(np.mean(close[-20:] * volume[-20:])) if n >= 20 else np.nan

    return {
        "mom_252_21": float(mom_252_21),
        "mom_60": float(mom_60),
        "rev_5": float(rev_5),
        "vol_20": vol_20,
        "dist_52w_high": float(dist_52w_high),
        "dollar_volume_20": dollar_volume_20,
    }


def _resid_mom_60(symbol: str, sectors: Mapping[str, str], closed: Mapping[str, pd.DataFrame]) -> float:
    sector = sectors.get(symbol)
    if sector is None:
        return np.nan
    etf_symbol = SECTOR_ETF.get(sector)
    if etf_symbol is None or etf_symbol == symbol:
        return np.nan

    sym_frame = closed.get(symbol)
    etf_frame = closed.get(etf_symbol)
    if sym_frame is None or etf_frame is None:
        return np.nan

    r_sym = sym_frame["Close"].astype(float).pct_change().dropna()
    r_etf = etf_frame["Close"].astype(float).pct_change().dropna()
    aligned = pd.concat({"sym": r_sym, "etf": r_etf}, axis=1, join="inner").sort_index()
    if len(aligned) < _RESID_MIN_RETURNS:
        return np.nan

    train = aligned.iloc[-(_RESID_TRAIN_WINDOW + _RESID_GAP) : -_RESID_GAP]
    tail = aligned.iloc[-_RESID_GAP:]

    x = train["etf"].to_numpy(dtype=float)
    y = train["sym"].to_numpy(dtype=float)
    x_var = float(np.var(x, ddof=1))
    b = 0.0 if x_var == 0.0 else float(np.cov(x, y, ddof=1)[0, 1] / x_var)
    a = float(y.mean() - b * x.mean())

    residual = tail["sym"].to_numpy(dtype=float) - a - b * tail["etf"].to_numpy(dtype=float)
    return float(np.sum(residual))


def _sector_rel_mom(mom_60: Mapping[str, float], sectors: Mapping[str, str]) -> pd.Series:
    usable = {symbol: value for symbol, value in mom_60.items() if np.isfinite(value) and symbol in sectors}
    if not usable:
        return pd.Series(dtype=float)

    columns = list(usable.keys())
    panel = pd.DataFrame(
        [[usable[symbol] for symbol in columns]],
        columns=columns,
        index=pd.DatetimeIndex([pd.Timestamp("2000-01-01")]),
    )
    groups = pd.Series({symbol: sectors[symbol] for symbol in columns}).reindex(columns)
    neutralized = group_neutralize(panel, groups)
    return neutralized.iloc[0]


def _rolling_vol20(close: pd.Series) -> pd.Series:
    returns = close.astype(float).pct_change()
    return returns.rolling(window=_VOL20_WINDOW, min_periods=_VOL20_WINDOW).std(ddof=1)


def _market_features(spy_closed: pd.DataFrame | None) -> dict[str, float]:
    if spy_closed is None or spy_closed.empty:
        return {"spy_above_200": np.nan, "spy_vol20_pct": np.nan}

    close = spy_closed["Close"].astype(float)
    values = close.to_numpy()
    n = len(values)

    if n >= _SPY_ABOVE_200_WINDOW:
        spy_above_200 = float(values[-1] > np.mean(values[-_SPY_ABOVE_200_WINDOW:]))
    else:
        spy_above_200 = np.nan

    vol20_series = _rolling_vol20(close).dropna()
    if len(vol20_series) < _VOL20_HISTORY:
        spy_vol20_pct = np.nan
    else:
        window = vol20_series.iloc[-_VOL20_HISTORY:]
        spy_vol20_pct = float(window.rank(pct=True).iloc[-1])

    return {"spy_above_200": spy_above_200, "spy_vol20_pct": spy_vol20_pct}


def cross_section(
    daily: Mapping[str, pd.DataFrame],
    sectors: Mapping[str, str],
    as_of: date,
) -> CrossSection:
    symbols = list(daily.keys())
    closed = {symbol: _closed_frame(frame, as_of) for symbol, frame in daily.items()}

    basic = {symbol: _basic_features(closed[symbol]) for symbol in symbols}
    mom_60_values = {symbol: basic[symbol]["mom_60"] for symbol in symbols}
    sector_rel = _sector_rel_mom(mom_60_values, sectors)

    raw: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        row = dict(basic[symbol])
        row["resid_mom_60"] = _resid_mom_60(symbol, sectors, closed)
        row["sector_rel_mom_60"] = float(sector_rel.get(symbol, np.nan))
        raw[symbol] = row

    raw_frame = pd.DataFrame({symbol: raw[symbol] for symbol in symbols}).T
    raw_frame = raw_frame.reindex(index=symbols, columns=list(CROSS_SECTIONAL))
    raw_frame = raw_frame.replace([np.inf, -np.inf], np.nan).astype(float)

    ranks = raw_frame.rank(axis=0, pct=True, na_option="keep")

    market = _market_features(closed.get(_SPY))

    return CrossSection(as_of=as_of, ranks=ranks, market=market)


def setup_vector(
    cs: CrossSection,
    *,
    symbol: str,
    direction: str,
    strategy: str,
    timeframe: str,
    setup_quality: float,
    stop_atr: float,
    reward_risk: float,
) -> dict[str, float]:
    row = cs.ranks.loc[symbol] if symbol in cs.ranks.index else pd.Series(np.nan, index=list(CROSS_SECTIONAL))

    vector: dict[str, float] = {}
    for feature in CROSS_SECTIONAL:
        value = float(row[feature])
        if direction == "SHORT" and feature in DIRECTIONAL:
            value = 1.0 - value
        vector[feature] = value

    for feature in MARKET:
        vector[feature] = float(cs.market.get(feature, np.nan))

    vector["setup_quality"] = float(setup_quality)
    vector["stop_atr"] = float(stop_atr)
    vector["reward_risk"] = float(reward_risk)

    vector[f"strategy={strategy}"] = 1.0
    vector[f"timeframe={timeframe}"] = 1.0

    return vector
