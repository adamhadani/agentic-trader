from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from alpaca.data.models.screener import ActiveStock, MostActives, Mover, Movers
from alpaca.trading.enums import AssetClass as AlpacaAssetClass, AssetExchange, AssetStatus

from agentic_trader.config import ContractConfig, DynamicUniverseConfig, load_config
from agentic_trader.constants import AssetClass
from agentic_trader.screeners.dynamic_universe import (
    MIN_REFERENCE_NAMES,
    AssetInfo,
    DynamicUniverseSource,
    ScreenerEntry,
    StaticReference,
    dollar_volume_threshold,
    liquidity_gate,
    median_dollar_volume,
    select_dynamic,
    static_reference,
    synthetic_contract,
)
from agentic_trader.transport.alpaca import BoundedScreenerClient


# A zero static reference: these tests exercise the optional absolute floor alone.
FLOOR_ONLY = StaticReference(percentile=0.25, names=MIN_REFERENCE_NAMES, value=0.0)


# --------------------------------------------------------------------------
# Fixtures and small builders
# --------------------------------------------------------------------------


def cfg(**overrides) -> DynamicUniverseConfig:
    base = {
        "enabled": True,
        "sources": ["most_actives", "movers"],
        "most_actives_top": 100,
        "movers_top": 50,
        "max_candidates": 40,
        "max_symbols": 20,
        "min_price": 10.0,
        "min_median_dollar_volume": 50_000_000,
    }
    base.update(overrides)
    return DynamicUniverseConfig(**base)


def entry(symbol, source="most_actives", rank=0, price=None, percent_change=None) -> ScreenerEntry:
    return ScreenerEntry(symbol=symbol, source=source, rank=rank, price=price, percent_change=percent_change)


def asset(
    symbol, name="Example Corp", asset_class="us_equity", exchange="NASDAQ", status="active", tradable=True
) -> AssetInfo:
    return AssetInfo(
        symbol=symbol, name=name, asset_class=asset_class, exchange=exchange, status=status, tradable=tradable
    )


@pytest.fixture
def app_config():
    return load_config()


# --------------------------------------------------------------------------
# select_dynamic: filter reasons
# --------------------------------------------------------------------------


def test_shape_rejects_dotted_symbol():
    """SLND.WS: a warrant expressed with a dot fails the ^[A-Z]{1,5}$ shape."""
    entries = [entry("SLND.WS")]
    selection = select_dynamic(entries, assets={}, static_symbols=(), cfg=cfg())
    assert selection.members == ()
    assert selection.reasons == {"shape": 1}


def test_shape_rejects_lowercase_and_long_symbols():
    entries = [entry("abcde"), entry("TOOLONG")]
    selection = select_dynamic(entries, assets={}, static_symbols=(), cfg=cfg())
    assert selection.members == ()
    assert selection.reasons == {"shape": 2}


def test_asset_missing_rejects_symbols_absent_from_the_active_equity_list():
    """NIVFW/HVIIU (warrant/unit) shapes pass the regex but are not in the clean
    active/tradable us_equity asset snapshot the source fetches."""
    entries = [entry("NIVFW"), entry("HVIIU")]
    selection = select_dynamic(entries, assets={}, static_symbols=(), cfg=cfg())
    assert selection.members == ()
    assert selection.reasons == {"asset_missing": 2}


def test_static_symbol_excluded():
    entries = [entry("AAPL")]
    assets = {"AAPL": asset("AAPL")}
    selection = select_dynamic(entries, assets=assets, static_symbols={"AAPL"}, cfg=cfg())
    assert selection.members == ()
    assert selection.reasons == {"static": 1}


def test_asset_class_rejects_non_equity():
    entries = [entry("XYZ")]
    assets = {"XYZ": asset("XYZ", asset_class="us_option")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"asset_class": 1}


def test_inactive_status_rejected():
    entries = [entry("XYZ")]
    assets = {"XYZ": asset("XYZ", status="inactive")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"inactive": 1}


def test_untradable_rejected():
    entries = [entry("XYZ")]
    assets = {"XYZ": asset("XYZ", tradable=False)}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"untradable": 1}


def test_exchange_outside_allowlist_rejected():
    entries = [entry("XYZ")]
    assets = {"XYZ": asset("XYZ", exchange="OTC")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"exchange": 1}


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("SOXS", "Direxion Daily Semiconductor Bear 3X Shares"),
        ("MSTZ", "ProShares UltraPro QQQ"),
    ],
)
def test_leveraged_name_markers_rejected(symbol, name):
    entries = [entry(symbol)]
    assets = {symbol: asset(symbol, name=name)}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"leveraged": 1}


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("INVLW", "Innventure, Inc. Warrant   10/02/2029"),
        ("IMAQR", "INTERNATIONAL MEDIA ACQUISITION CORP Rights"),
        ("ABCDU", "Example Acquisition Corp Units"),
    ],
)
def test_warrants_rights_and_units_rejected_by_name(symbol, name):
    # Alpaca lists ~1,000 exchange-traded warrants/rights/units with plain 5-letter symbols.
    entries = [entry(symbol)]
    assets = {symbol: asset(symbol, name=name)}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"instrument": 1}


def test_ordinary_names_containing_unit_letters_are_kept():
    entries = [entry("UNH")]
    assets = {"UNH": asset("UNH", name="UnitedHealth Group Incorporated Common Stock")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert [member.symbol for member in selection.members] == ["UNH"]


def test_price_below_minimum_rejected_when_screener_reports_price():
    entries = [entry("XYZ", source="movers", price=1.23, percent_change=5.0)]
    assets = {"XYZ": asset("XYZ")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert selection.reasons == {"price": 1}


def test_price_none_from_most_actives_is_not_rejected_here():
    """A most-actives entry has no screener price; the price check defers to liquidity_gate."""
    entries = [entry("XYZ", source="most_actives", price=None)]
    assets = {"XYZ": asset("XYZ")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert [m.symbol for m in selection.members] == ["XYZ"]
    assert selection.reasons == {}


def test_cap_counts_and_truncates_survivors():
    letters = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"]
    entries = [entry(sym, rank=i) for i, sym in enumerate(letters)]
    assets = {sym: asset(sym) for sym in letters}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg(max_candidates=3))
    assert [m.symbol for m in selection.members] == letters[:3]
    assert selection.reasons == {"cap": 2}


# --------------------------------------------------------------------------
# select_dynamic: ordering and dedup
# --------------------------------------------------------------------------


def test_most_actives_order_preserved_then_movers_by_absolute_percent_change():
    entries = [
        entry("AAAA", source="most_actives", rank=1),
        entry("BBBB", source="most_actives", rank=0),
        entry("CCCC", source="movers", percent_change=-2.0),
        entry("DDDD", source="movers", percent_change=5.0),
        entry("EEEE", source="movers", percent_change=-9.0),
    ]
    assets = {sym: asset(sym) for sym in ("AAAA", "BBBB", "CCCC", "DDDD", "EEEE")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    # most_actives ordered by rank (BBBB rank 0 before AAAA rank 1), then movers by |pct| desc.
    assert [m.symbol for m in selection.members] == ["BBBB", "AAAA", "EEEE", "DDDD", "CCCC"]


def test_duplicate_across_sources_keeps_first_most_actives_entry():
    entries = [
        entry("AAAA", source="most_actives", rank=0, price=None, percent_change=None),
        entry("AAAA", source="movers", rank=0, price=42.0, percent_change=8.0),
    ]
    assets = {"AAAA": asset("AAAA")}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert len(selection.members) == 1
    kept = selection.members[0]
    assert kept.source == "most_actives"
    assert kept.price is None
    assert selection.raw_counts == {"most_actives": 1, "movers": 1}


def test_raw_counts_reflect_entries_received_per_source_regardless_of_filtering():
    entries = [
        entry("aaaaa", source="most_actives"),  # rejected by shape
        entry("BBBB", source="movers", percent_change=1.0),
    ]
    selection = select_dynamic(entries, assets={"BBBB": asset("BBBB")}, static_symbols=(), cfg=cfg())
    assert selection.raw_counts == {"most_actives": 1, "movers": 1}


# --------------------------------------------------------------------------
# liquidity_gate
# --------------------------------------------------------------------------


def _daily_frame(closes, volumes):
    return pd.DataFrame({"Close": closes, "Volume": volumes})


def test_liquidity_gate_insufficient_bars():
    members = [entry("AAAA", price=50.0)]
    daily = {"AAAA": _daily_frame([50.0] * 19, [1_000_000.0] * 19)}
    kept, excluded = liquidity_gate(daily, members, cfg(), reference=FLOOR_ONLY)
    assert kept == []
    assert excluded == {"AAAA": "insufficient_bars"}


def test_liquidity_gate_missing_symbol_is_insufficient_bars():
    members = [entry("AAAA", price=50.0)]
    kept, excluded = liquidity_gate({}, members, cfg(), reference=FLOOR_ONLY)
    assert kept == []
    assert excluded == {"AAAA": "insufficient_bars"}


def test_liquidity_gate_exact_minimum_median_passes():
    # median(Close * Volume) over 20 rows exactly equal to the configured minimum.
    dollar_volume = 50_000_000.0
    closes = [100.0] * 20
    volumes = [dollar_volume / 100.0] * 20
    members = [entry("AAAA", price=100.0)]
    daily = {"AAAA": _daily_frame(closes, volumes)}
    kept, excluded = liquidity_gate(daily, members, cfg(min_median_dollar_volume=dollar_volume), reference=FLOOR_ONLY)
    assert kept == members
    assert excluded == {}


def test_liquidity_gate_below_minimum_median_excluded():
    closes = [100.0] * 20
    volumes = [1.0] * 20  # dollar volume 100, far below any reasonable minimum
    members = [entry("AAAA", price=100.0)]
    daily = {"AAAA": _daily_frame(closes, volumes)}
    kept, excluded = liquidity_gate(daily, members, cfg(min_median_dollar_volume=50_000_000), reference=FLOOR_ONLY)
    assert kept == []
    assert excluded == {"AAAA": "dollar_volume"}


def test_liquidity_gate_only_uses_last_20_rows():
    # 25 rows; the oldest 5 rows have near-zero dollar volume and must be ignored.
    closes = [1.0] * 5 + [100.0] * 20
    volumes = [1.0] * 5 + [1_000_000.0] * 20
    members = [entry("AAAA", price=100.0)]
    daily = {"AAAA": _daily_frame(closes, volumes)}
    kept, excluded = liquidity_gate(daily, members, cfg(min_median_dollar_volume=50_000_000), reference=FLOOR_ONLY)
    assert kept == members
    assert excluded == {}


def test_liquidity_gate_defers_price_check_for_most_actives_entries():
    """A most-actives entry (no screener price) whose last daily Close < min_price fails here."""
    members = [entry("AAAA", source="most_actives", price=None)]
    closes = [5.0] * 20  # below the default min_price of 10.0
    volumes = [1_000_000_000.0] * 20  # dollar volume alone would pass
    daily = {"AAAA": _daily_frame(closes, volumes)}
    kept, excluded = liquidity_gate(daily, members, cfg(), reference=FLOOR_ONLY)
    assert kept == []
    assert excluded == {"AAAA": "price"}


def test_liquidity_gate_caps_at_max_symbols_in_order():
    symbols = ["AAAA", "BBBB", "CCCC"]
    members = [entry(sym, price=100.0) for sym in symbols]
    daily = {sym: _daily_frame([100.0] * 20, [1_000_000.0] * 20) for sym in symbols}
    kept, excluded = liquidity_gate(daily, members, cfg(max_symbols=2), reference=FLOOR_ONLY)
    assert [m.symbol for m in kept] == ["AAAA", "BBBB"]
    assert excluded == {"CCCC": "cap"}


# --------------------------------------------------------------------------
# synthetic_contract
# --------------------------------------------------------------------------


def test_synthetic_contract_fields():
    contract = synthetic_contract("XYZ", "Example Corp")
    assert isinstance(contract, ContractConfig)
    assert contract.ticker == "XYZ"
    assert contract.name == "Example Corp"
    assert contract.asset_class == AssetClass.EQUITY
    assert contract.multiplier == 1.0
    assert contract.tick_size == 0.01


# --------------------------------------------------------------------------
# DynamicUniverseSource
# --------------------------------------------------------------------------


@dataclass
class _FakeAsset:
    symbol: str
    name: str
    asset_class: object
    exchange: object
    status: object
    tradable: bool


@dataclass
class _FakeTradingClient:
    pages: list[list[_FakeAsset]]
    calls: int = 0
    requests: list[object] = field(default_factory=list)

    def get_all_assets(self, request):
        self.requests.append(request)
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return page


@dataclass
class _FakeScreenerClient:
    most_actives_response: object = None
    movers_response: object = None
    most_actives_calls: int = 0
    movers_calls: int = 0
    fail_most_actives: bool = False

    def get_most_actives(self, request):
        self.most_actives_calls += 1
        if self.fail_most_actives:
            raise RuntimeError("screener unavailable")
        return self.most_actives_response

    def get_market_movers(self, request):
        self.movers_calls += 1
        return self.movers_response


class _FakeClock:
    def __init__(self, dt: datetime):
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt


def _most_actives_response(symbols):
    return MostActives(
        most_actives=[ActiveStock(symbol=s, volume=1_000.0, trade_count=100.0) for s in symbols],
        last_updated=datetime(2026, 9, 23, tzinfo=UTC),
    )


def _movers_response(gainers, losers):
    return Movers(
        gainers=[Mover(symbol=s, percent_change=p, change=p, price=50.0) for s, p in gainers],
        losers=[Mover(symbol=s, percent_change=p, change=p, price=50.0) for s, p in losers],
        market_type="stocks",
        last_updated=datetime(2026, 9, 23, tzinfo=UTC),
    )


async def test_entries_combines_configured_sources(app_config):
    screener = _FakeScreenerClient(
        most_actives_response=_most_actives_response(["AAAA", "BBBB"]),
        movers_response=_movers_response([("CCCC", 5.0)], [("DDDD", -3.0)]),
    )
    source = DynamicUniverseSource(app_config, screener_client=screener, trading_client=_FakeTradingClient(pages=[[]]))
    entries = await source.entries()
    assert [e.symbol for e in entries] == ["AAAA", "BBBB", "CCCC", "DDDD"]
    assert [e.source for e in entries] == ["most_actives", "most_actives", "movers", "movers"]
    ccc = next(e for e in entries if e.symbol == "CCCC")
    assert ccc.price == 50.0
    assert ccc.percent_change == 5.0
    aaa = next(e for e in entries if e.symbol == "AAAA")
    assert aaa.price is None


async def test_entries_raises_on_source_failure(app_config):
    screener = _FakeScreenerClient(fail_most_actives=True, movers_response=_movers_response([], []))
    source = DynamicUniverseSource(app_config, screener_client=screener, trading_client=_FakeTradingClient(pages=[[]]))
    with pytest.raises(RuntimeError, match="screener unavailable"):
        await source.entries()


async def test_assets_cached_for_the_same_ny_date_and_refetched_on_a_new_date(app_config):
    first_page = [
        _FakeAsset("AAAA", "First Corp", AlpacaAssetClass.US_EQUITY, AssetExchange.NASDAQ, AssetStatus.ACTIVE, True)
    ]
    second_page = [
        _FakeAsset("BBBB", "Second Corp", AlpacaAssetClass.US_EQUITY, AssetExchange.NYSE, AssetStatus.ACTIVE, True)
    ]
    trading = _FakeTradingClient(pages=[first_page, second_page])
    clock = _FakeClock(datetime(2026, 9, 23, 14, 0, tzinfo=UTC))  # ~10:00 ET
    source = DynamicUniverseSource(
        app_config, screener_client=_FakeScreenerClient(), trading_client=trading, clock=clock
    )

    first = await source.assets()
    second = await source.assets()
    assert trading.calls == 1
    assert first == second
    assert set(first) == {"AAAA"}

    clock.dt = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)
    third = await source.assets()
    assert trading.calls == 2
    assert set(third) == {"BBBB"}


async def test_assets_maps_fields_from_the_alpaca_asset(app_config):
    page = [
        _FakeAsset("AAAA", "Example Corp", AlpacaAssetClass.US_EQUITY, AssetExchange.NASDAQ, AssetStatus.ACTIVE, True)
    ]
    trading = _FakeTradingClient(pages=[page])
    clock = _FakeClock(datetime(2026, 9, 23, 14, 0, tzinfo=UTC))
    source = DynamicUniverseSource(
        app_config, screener_client=_FakeScreenerClient(), trading_client=trading, clock=clock
    )
    assets = await source.assets()
    info = assets["AAAA"]
    assert info == AssetInfo(
        symbol="AAAA", name="Example Corp", asset_class="us_equity", exchange="NASDAQ", status="active", tradable=True
    )


def _dated_daily(closes, volumes, end="2026-09-23"):
    # Alpaca stamps daily bars at New York midnight.
    index = pd.date_range(end=pd.Timestamp(end, tz="America/New_York"), periods=len(closes), freq="B").tz_convert("UTC")
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=index)


def test_liquidity_gate_ignores_todays_partial_bar():
    members = [entry("AAAA", price=50.0)]
    # 20 completed sessions at $50M, then today's partial bar with tiny volume.
    frame = _dated_daily([50.0] * 21, [1_000_000.0] * 20 + [10.0])
    kept, excluded = liquidity_gate({"AAAA": frame}, members, cfg(), as_of=date(2026, 9, 22), reference=FLOOR_ONLY)
    assert [m.symbol for m in kept] == ["AAAA"] and excluded == {}


def test_liquidity_gate_non_finite_rows_do_not_count():
    members = [entry("AAAA", price=50.0)]
    frame = _dated_daily([50.0] * 19 + [float("nan")], [1_000_000.0] * 20, end="2026-09-22")
    kept, excluded = liquidity_gate({"AAAA": frame}, members, cfg(), as_of=date(2026, 9, 22), reference=FLOOR_ONLY)
    assert kept == [] and excluded == {"AAAA": "insufficient_bars"}


@pytest.mark.parametrize(
    "name",
    ["Ultragenyx Pharmaceutical Inc. Common Stock", "Bullfrog Gold Corp", "Bear Creek Mining Corp"],
)
def test_ordinary_names_containing_leveraged_substrings_are_kept(name):
    entries = [entry("ABCD")]
    assets = {"ABCD": asset("ABCD", name=name)}
    selection = select_dynamic(entries, assets=assets, static_symbols=(), cfg=cfg())
    assert [member.symbol for member in selection.members] == ["ABCD"]


# --------------------------------------------------------------------------
# Final review: crypto prefixes, volatility/option-income products
# --------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["BTCS", "ETHE", "SOLV", "DOGEX"])
def test_crypto_prefixed_symbols_are_excluded_like_the_static_validator(symbol):
    """The session router would treat these as crypto (CRYPTO_SYMBOL_PREFIXES)."""
    selection = select_dynamic([entry(symbol)], assets={symbol: asset(symbol)}, static_symbols=(), cfg=cfg())
    assert selection.members == ()
    assert selection.reasons == {"crypto_prefix": 1}


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("VXX", "iPath Series B S&P 500 VIX Short-Term Futures ETN"),
        ("SVIX", "-1x Short VIX Futures ETF"),
        ("SVOL", "Simplify Volatility Premium ETF"),
        ("TSLY", "YieldMax TSLA Option Income Strategy ETF"),
        ("JEPQ", "JPMorgan Nasdaq Equity Premium Income ETF Option Income Shares"),
        ("QYLD", "Global X NASDAQ 100 Covered Call ETF"),
    ],
)
def test_volatility_and_option_income_products_are_instruments(symbol, name):
    selection = select_dynamic([entry(symbol)], assets={symbol: asset(symbol, name=name)}, static_symbols=(), cfg=cfg())
    assert selection.members == ()
    assert selection.reasons == {"instrument": 1}


@pytest.mark.parametrize(
    "name",
    [
        "Vixen Biosciences Inc. Common Stock",  # "vix" is not a whole word
        "Volatility Analytics Inc. Common Stock",  # no fund context
        "Covered Call Software Corp",  # no fund context
        "Apple Inc. Common Stock",
    ],
)
def test_ordinary_companies_are_not_volatility_products(name):
    selection = select_dynamic([entry("ABCD")], assets={"ABCD": asset("ABCD", name=name)}, static_symbols=(), cfg=cfg())
    assert [member.symbol for member in selection.members] == ["ABCD"]


# --------------------------------------------------------------------------
# Final review: relative, self-calibrating liquidity threshold
# --------------------------------------------------------------------------


def _flat(dollar_volume: float, rows: int = 20):
    return _daily_frame([100.0] * rows, [dollar_volume / 100.0] * rows)


def test_median_dollar_volume_needs_a_full_window():
    assert median_dollar_volume(_flat(1e6, rows=19)) is None
    assert median_dollar_volume(_flat(1e6)) == pytest.approx(1e6)


def test_static_reference_is_a_linear_percentile_of_the_static_medians():
    static = {f"S{i:02d}": _flat(1e6 * (i + 1)) for i in range(20)}  # 1M..20M
    reference = static_reference(static, 0.25)
    assert reference.names == 20
    assert reference.value == pytest.approx(5.75e6)  # numpy linear quantile of 1..20 at 0.25 = 5.75


def test_static_reference_fails_closed_below_the_minimum_names():
    static = {f"S{i:02d}": _flat(1e6) for i in range(MIN_REFERENCE_NAMES - 1)}
    static["THIN"] = _flat(1e6, rows=19)  # not a full window, so it does not count
    reference = static_reference(static, 0.25)
    assert reference.value is None and reference.names == MIN_REFERENCE_NAMES - 1


def test_static_reference_counts_completed_sessions_only():
    """The same as_of rule as the dynamic side: today's partial bar is ignored."""
    static = {f"S{i:02d}": _dated_daily([100.0] * 21, [1e4] * 20 + [1e9]) for i in range(20)}
    reference = static_reference(static, 0.5, as_of=date(2026, 9, 22))
    assert reference.value == pytest.approx(1e6)


def test_the_threshold_is_the_larger_of_the_reference_and_the_floor():
    reference = StaticReference(percentile=0.25, names=20, value=2e6)
    assert dollar_volume_threshold(reference, cfg(min_median_dollar_volume=0)) == 2e6
    assert dollar_volume_threshold(reference, cfg(min_median_dollar_volume=5e6)) == 5e6
    assert dollar_volume_threshold(StaticReference(0.25, 3, None), cfg()) is None


def test_liquidity_gate_passes_at_the_reference_and_excludes_below_it():
    reference = StaticReference(percentile=0.25, names=20, value=2e6)
    members = [entry("PASS", price=100.0), entry("THIN", price=100.0)]
    daily = {"PASS": _flat(2e6), "THIN": _flat(1.99e6)}
    kept, excluded = liquidity_gate(daily, members, cfg(min_median_dollar_volume=0), reference=reference)
    assert [m.symbol for m in kept] == ["PASS"]
    assert excluded == {"THIN": "dollar_volume"}


def test_liquidity_gate_without_a_reference_excludes_everything():
    members = [entry("AAAA", price=100.0), entry("BBBB", price=100.0)]
    daily = {"AAAA": _flat(1e12), "BBBB": _flat(1e12)}
    kept, excluded = liquidity_gate(daily, members, cfg(), reference=StaticReference(0.25, 5, None))
    assert kept == []
    assert excluded == {"AAAA": "no_reference", "BBBB": "no_reference"}


def test_config_defaults_make_the_reference_govern():
    defaults = DynamicUniverseConfig()
    assert defaults.min_dollar_volume_static_percentile == 0.25
    assert defaults.min_median_dollar_volume == 0


# --------------------------------------------------------------------------
# Final review: the screener client is bounded
# --------------------------------------------------------------------------


def test_source_uses_a_bounded_screener_client_with_the_market_data_timeout(app_config, monkeypatch):
    app_config.alpaca_api_key = "fake-key"
    app_config.alpaca_api_secret = "fake-secret"
    app_config.market_data.timeout_seconds = 7.5
    source = DynamicUniverseSource(app_config, trading_client=_FakeTradingClient(pages=[[]]))
    assert isinstance(source.screener_client, BoundedScreenerClient)
    assert source.screener_client.request_timeout == 7.5

    seen: list[dict] = []

    def fake_parent(self, method, url, opts, retry):
        seen.append(opts)
        return {"most_actives": [], "last_updated": "2026-09-23T00:00:00Z"}

    monkeypatch.setattr("agentic_trader.transport.alpaca.ScreenerClient._one_request", fake_parent)
    source.screener_client._one_request("GET", "https://example.invalid/v1beta1/screener/stocks/most-actives", {}, 3)
    assert seen == [{"timeout": 7.5}]
