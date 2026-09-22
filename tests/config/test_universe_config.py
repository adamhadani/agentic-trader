import textwrap

import pytest

from agentic_trader.config import (
    AppConfig,
    ContractConfig,
    MarketDataConfig,
    ScanBudget,
    ScanConfig,
    SchedulerConfig,
    UniverseConfig,
    UniverseEntry,
    load_config,
)
from agentic_trader.constants import AssetClass


def test_universe_expands_to_equity_contracts_and_sector_groups():
    universe = UniverseConfig(
        groups={
            "etfs": [UniverseEntry(symbol="XLK", sector="etf_sector"), UniverseEntry(symbol="SPY", sector="etf_broad")],
            "stocks": [UniverseEntry(symbol="AAPL", sector="technology"), UniverseEntry(symbol="ZZZ")],
        }
    )
    assert universe.symbols == ("AAPL", "SPY", "XLK", "ZZZ")
    assert universe.contract_documents()["AAPL"] == {"ticker": "AAPL", "name": "AAPL", "asset_class": "equity"}
    assert universe.sector_groups() == {
        "sector_etf_broad": ["SPY"],
        "sector_etf_sector": ["XLK"],
        "sector_technology": ["AAPL"],
    }  # "unknown" never forms a group


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ({"a": [{"symbol": "SPY"}], "b": [{"symbol": "SPY"}]}, "Duplicate"),
        ({"a": [{"symbol": "SOLV"}]}, "crypto"),
        ({"a": [{"symbol": "spy"}]}, "pattern"),
        ({"a": [{"symbol": "TOOLONGX"}]}, "pattern"),
        ({"a": [{"symbol": "SPY", "sector": "Not Snake"}]}, "pattern"),
    ],
)
def test_invalid_universes_are_rejected(groups, message):
    with pytest.raises(ValueError, match=message):
        UniverseConfig(groups=groups)


def test_universe_size_is_bounded():
    entries = [{"symbol": symbol} for symbol in ("AAA", "BBB", "CCC")]
    with pytest.raises(ValueError, match="max_symbols"):
        UniverseConfig(groups={"a": entries}, max_symbols=2)


def test_scan_config_defaults_match_the_spec():
    assert ScanConfig().model_dump() == {
        "max_cards_per_scan": 1,
        "max_cards_per_session": 2,
        "max_cards_per_group_per_session": 1,
        "max_llm_evaluations_per_scan": 4,
        "min_bar_coverage": 0.8,
        "coverage_sessions": 10,
        "coverage_reference_symbol": "SPY",
    }
    assert [b.value for b in ScanBudget] == ["full", "session", "none"]


def test_market_data_and_universe_defaults_match_the_spec():
    assert MarketDataConfig().scan_concurrency == 8
    assert MarketDataConfig().max_requests_per_minute == 150
    assert UniverseConfig().max_symbols == 250


def test_non_universe_contracts_default_treats_every_contract_as_explicit():
    config = AppConfig(
        contracts={
            "SPY": ContractConfig(ticker="SPY", name="SPY", asset_class="equity"),
            "/MES": ContractConfig(ticker="MES=F", name="Micro E-mini S&P 500", multiplier=5.0, tick_size=0.25),
        },
        universe=UniverseConfig(
            groups={"a": [UniverseEntry(symbol="SPY"), UniverseEntry(symbol="AAPL")]},
        ),
    )
    assert config.non_universe_contracts == ["/MES", "SPY"]


def test_non_universe_contracts_with_no_universe_returns_all_contracts():
    config = AppConfig(
        contracts={
            "SPY": ContractConfig(ticker="SPY", name="SPY", asset_class="equity"),
            "/MES": ContractConfig(ticker="MES=F", name="Micro E-mini S&P 500", multiplier=5.0, tick_size=0.25),
        }
    )
    assert config.non_universe_contracts == ["/MES", "SPY"]


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_load_config_merges_universe_contracts_with_explicit_contracts_winning(tmp_path):
    path = write(
        tmp_path,
        """
        contracts:
          "SPY":
            ticker: "SPY"
            name: "SPDR S&P 500 ETF Trust"
            asset_class: "equity"
            target_risk_dollars: 400.0
          "/MES":
            ticker: "MES=F"
            name: "Micro E-mini S&P 500"
            multiplier: 5.0
            tick_size: 0.25
        universe:
          groups:
            mega_caps:
              - {symbol: AAPL, sector: technology}
              - {symbol: SPY, sector: etf_broad}
        scheduler:
          suggestion_scan_times_et: ["10:35", "14:35"]
        """,
    )
    config = load_config(path, environ={"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""})
    assert set(config.contracts) == {"SPY", "/MES", "AAPL"}
    assert config.contracts["SPY"].name == "SPDR S&P 500 ETF Trust"  # explicit entry won
    assert config.contracts["SPY"].target_risk_dollars == 400.0
    assert config.contracts["AAPL"].asset_class == AssetClass.EQUITY
    assert config.contracts["AAPL"].tick_size == 0.01 and config.contracts["AAPL"].multiplier == 1.0
    assert config.portfolio.correlation_groups["sector_technology"] == ["AAPL"]
    assert "SPY" in config.portfolio.correlation_groups["us_broad_market"]  # defaults retained
    assert config.non_universe_contracts == ["/MES", "SPY"]
    assert config.scheduler.suggestion_scan_times_et == ["10:35", "14:35"]


def test_load_config_without_universe_is_unchanged(tmp_path):
    path = write(tmp_path, 'contracts:\n  "SPY":\n    ticker: "SPY"\n    name: "SPY"\n    asset_class: "equity"\n')
    config = load_config(path, environ={"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""})
    assert set(config.contracts) == {"SPY"}
    assert config.universe.symbols == ()
    assert config.non_universe_contracts == ["SPY"]


def test_load_config_tolerates_a_bare_universe_and_scan_key(tmp_path):
    path = write(
        tmp_path,
        """
        contracts:
          "SPY":
            ticker: "SPY"
            name: "SPY"
            asset_class: "equity"
        universe:
        scan:
        """,
    )
    config = load_config(path, environ={"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""})
    assert config.universe.symbols == ()
    assert config.scan == ScanConfig()


@pytest.mark.parametrize("times", [["25:00"], ["9:5"], ["10:35", "10:35"], []])
def test_invalid_suggestion_scan_times_are_rejected(times):
    with pytest.raises(ValueError):
        SchedulerConfig(suggestion_scan_times_et=times)
