"""Deterministic dynamic-universe selection for scheduled suggestion scans.

See docs/superpowers/specs/2026-09-23-dynamic-universe-design.md (WS3). Adds up to
`max_symbols` in-play US equities from Alpaca's screener to a scan's static universe,
filtered by pure functions so every reason is independently testable. `DynamicUniverseSource`
is the only I/O boundary: blocking Alpaca SDK calls run off the event loop via
`asyncio.to_thread`, using injected clients in tests and the repo's bounded, credentialed
clients otherwise. A screener/asset failure is the caller's concern (Task 2): `entries()`
and `assets()` simply raise.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.models.screener import MostActives, Movers
from alpaca.data.requests import MarketMoversRequest, MostActivesBy, MostActivesRequest
from alpaca.trading.enums import AssetClass as AlpacaAssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from agentic_trader.config import ContractConfig
from agentic_trader.constants import AssetClass
from agentic_trader.market.session import ET_TZ
from agentic_trader.transport.alpaca import BoundedTradingClient


if TYPE_CHECKING:
    from alpaca.trading.client import TradingClient

    from agentic_trader.config import AppConfig, DynamicUniverseConfig


# Warrants, units, rights and multi-class share symbols all fall outside this shape.
_SYMBOL_SHAPE = re.compile(r"^[A-Z]{1,5}$")

_ALLOWED_EXCHANGES = frozenset({"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"})

# A leveraged/inverse fund ("Direxion Daily Semiconductor Bear 3X Shares", "ProShares
# UltraPro QQQ") names both a fund and a whole-word leverage marker; requiring the fund
# context keeps companies such as Ultragenyx, Bullfrog Gold or Bear Creek Mining.
_LEVERAGED_MARKERS = re.compile(
    r"(?<![\w-])-?[123]x\b|\b(?:ultra|ultrapro|ultrashort|bull|bear|leveraged|inverse)\b|daily target",
    re.IGNORECASE,
)
_FUND_CONTEXT = re.compile(r"\b(?:etf|etn|fund|trust|shares|proshares|direxion)\b", re.IGNORECASE)

# Warrants, rights and units trade under plain symbols too; their asset names say so.
_INSTRUMENT_MARKERS = re.compile(r"\bwarrants?\b|\brights?\b|\bunits?\b", re.IGNORECASE)

# The last 20 completed daily bars set the liquidity gate's median dollar-volume window.
_LIQUIDITY_WINDOW = 20


@dataclass(frozen=True)
class ScreenerEntry:
    symbol: str
    source: str
    rank: int
    price: float | None
    percent_change: float | None


@dataclass(frozen=True)
class AssetInfo:
    symbol: str
    name: str
    asset_class: str
    exchange: str
    status: str
    tradable: bool


@dataclass(frozen=True)
class DynamicSelection:
    members: tuple[ScreenerEntry, ...]  # ordered, <= max_candidates
    reasons: dict[str, int]  # filter reason -> count
    raw_counts: dict[str, int]  # source -> entries received


def _combined_order(entries: Sequence[ScreenerEntry]) -> list[ScreenerEntry]:
    """Most-actives entries first (source rank order), then movers by |percent_change| desc."""
    most_actives = sorted((e for e in entries if e.source == "most_actives"), key=lambda e: e.rank)
    movers = sorted(
        (e for e in entries if e.source != "most_actives"),
        key=lambda e: -abs(e.percent_change) if e.percent_change is not None else 0.0,
    )
    return most_actives + movers


def _deduplicated(entries: Sequence[ScreenerEntry]) -> list[ScreenerEntry]:
    """A symbol seen in both sources keeps its first (most-actives-first order) entry."""
    seen: set[str] = set()
    kept: list[ScreenerEntry] = []
    for entry in entries:
        if entry.symbol in seen:
            continue
        seen.add(entry.symbol)
        kept.append(entry)
    return kept


def select_dynamic(
    entries: Sequence[ScreenerEntry],
    assets: Mapping[str, AssetInfo],
    static_symbols: Collection[str],
    cfg: DynamicUniverseConfig,
) -> DynamicSelection:
    """Filter raw screener entries into an ordered, capped, reason-counted selection."""
    raw_counts: dict[str, int] = {}
    for entry in entries:
        raw_counts[entry.source] = raw_counts.get(entry.source, 0) + 1

    reasons: dict[str, int] = {}

    def bump(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    static = set(static_symbols)
    survivors: list[ScreenerEntry] = []
    for entry in _deduplicated(_combined_order(entries)):
        if not _SYMBOL_SHAPE.match(entry.symbol):
            bump("shape")
            continue
        if entry.symbol in static:
            bump("static")
            continue
        asset = assets.get(entry.symbol)
        if asset is None:
            bump("asset_missing")
            continue
        if asset.asset_class != "us_equity":
            bump("asset_class")
            continue
        if asset.status != "active":
            bump("inactive")
            continue
        if not asset.tradable:
            bump("untradable")
            continue
        if asset.exchange not in _ALLOWED_EXCHANGES:
            bump("exchange")
            continue
        if _INSTRUMENT_MARKERS.search(asset.name):
            bump("instrument")
            continue
        if _LEVERAGED_MARKERS.search(asset.name) and _FUND_CONTEXT.search(asset.name):
            bump("leveraged")
            continue
        if entry.price is not None and entry.price < cfg.min_price:
            bump("price")
            continue
        survivors.append(entry)

    members = survivors[: cfg.max_candidates]
    if len(survivors) > cfg.max_candidates:
        reasons["cap"] = len(survivors) - cfg.max_candidates

    return DynamicSelection(members=tuple(members), reasons=reasons, raw_counts=raw_counts)


def liquidity_gate(
    daily_by_symbol: Mapping[str, pd.DataFrame],
    members: Sequence[ScreenerEntry],
    cfg: DynamicUniverseConfig,
    *,
    as_of: date | None = None,
) -> tuple[list[ScreenerEntry], dict[str, str]]:
    """Apply the median dollar-volume liquidity gate, preserving source order.

    Only completed sessions count: with ``as_of`` (the last completed New York session),
    later rows such as today's in-progress bar are ignored, and rows with a non-finite
    Close or Volume never count toward the 20-session window.

    A most-actives entry carries no screener price; its price check is deferred here
    against the last daily Close (reason "price"). A movers entry already passed the
    `min_price` filter in `select_dynamic` and is not rechecked.
    """
    excluded: dict[str, str] = {}
    kept: list[ScreenerEntry] = []
    for entry in members:
        daily = _completed_rows(daily_by_symbol.get(entry.symbol), as_of)
        if daily is None or len(daily) < _LIQUIDITY_WINDOW:
            excluded[entry.symbol] = "insufficient_bars"
            continue
        window = daily.tail(_LIQUIDITY_WINDOW)
        if entry.price is None and float(window["Close"].iloc[-1]) < cfg.min_price:
            excluded[entry.symbol] = "price"
            continue
        dollar_volume = window["Close"] * window["Volume"]
        if float(dollar_volume.median()) < cfg.min_median_dollar_volume:
            excluded[entry.symbol] = "dollar_volume"
            continue
        kept.append(entry)

    capped = kept[: cfg.max_symbols]
    for entry in kept[cfg.max_symbols :]:
        excluded[entry.symbol] = "cap"
    return capped, excluded


def _completed_rows(daily: pd.DataFrame | None, as_of: date | None) -> pd.DataFrame | None:
    if daily is None or daily.empty:
        return daily
    finite = daily[np.isfinite(daily["Close"].to_numpy(float)) & np.isfinite(daily["Volume"].to_numpy(float))]
    if as_of is None or not isinstance(finite.index, pd.DatetimeIndex):
        return finite
    index = finite.index if finite.index.tz is not None else finite.index.tz_localize("UTC")
    return finite[index.tz_convert(ET_TZ).date <= as_of]


def synthetic_contract(symbol: str, name: str) -> ContractConfig:
    """One scan's in-memory contract entry for a dynamic name; `config.contracts` is never mutated."""
    return ContractConfig(ticker=symbol, name=name, multiplier=1.0, tick_size=0.01, asset_class=AssetClass.EQUITY)


class DynamicUniverseSource:
    """Read-only Alpaca screener/asset access: injected clients in tests, bounded credentialed
    clients otherwise. Every method is a raising boundary; the caller (Task 2) decides fallback."""

    def __init__(
        self,
        config: AppConfig,
        *,
        screener_client: ScreenerClient | None = None,
        trading_client: TradingClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.cfg = config.universe.dynamic
        self.screener_client = screener_client or ScreenerClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
        )
        self.trading_client = trading_client or BoundedTradingClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
            paper=config.alpaca_paper,
            request_timeout=config.market_data.timeout_seconds,
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self._assets_cache: dict[str, AssetInfo] | None = None
        self._assets_cache_date: date | None = None

    async def entries(self) -> list[ScreenerEntry]:
        """One list of raw screener entries per configured source, in source order."""
        collected: list[ScreenerEntry] = []
        if "most_actives" in self.cfg.sources:
            most_actives_response = await asyncio.to_thread(
                self.screener_client.get_most_actives,
                MostActivesRequest(top=self.cfg.most_actives_top, by=MostActivesBy.TRADES),
            )
            # Only raw_data mode (never used here) returns a dict instead of the typed model.
            assert isinstance(most_actives_response, MostActives)
            collected.extend(
                ScreenerEntry(symbol=item.symbol, source="most_actives", rank=rank, price=None, percent_change=None)
                for rank, item in enumerate(most_actives_response.most_actives)
            )
        if "movers" in self.cfg.sources:
            movers_response = await asyncio.to_thread(
                self.screener_client.get_market_movers,
                MarketMoversRequest(top=self.cfg.movers_top),
            )
            assert isinstance(movers_response, Movers)
            movers = list(movers_response.gainers) + list(movers_response.losers)
            collected.extend(
                ScreenerEntry(
                    symbol=item.symbol,
                    source="movers",
                    rank=rank,
                    price=item.price,
                    percent_change=item.percent_change,
                )
                for rank, item in enumerate(movers)
            )
        return collected

    async def assets(self) -> dict[str, AssetInfo]:
        """Active US-equity assets, fetched once per NY date and cached in memory."""
        today = self.clock().astimezone(ET_TZ).date()
        if self._assets_cache is not None and self._assets_cache_date == today:
            return self._assets_cache

        raw_assets = await asyncio.to_thread(
            self.trading_client.get_all_assets,
            GetAssetsRequest(asset_class=AlpacaAssetClass.US_EQUITY, status=AssetStatus.ACTIVE),
        )
        # Only raw_data mode (never used here) returns a dict instead of a list of assets.
        assert isinstance(raw_assets, list)
        assets = {
            asset.symbol: AssetInfo(
                symbol=asset.symbol,
                name=asset.name or "",
                asset_class=asset.asset_class.value,
                exchange=asset.exchange.value,
                status=asset.status.value,
                tradable=asset.tradable,
            )
            for asset in raw_assets
        }
        self._assets_cache = assets
        self._assets_cache_date = today
        return assets
