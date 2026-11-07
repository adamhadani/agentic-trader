"""Scan bars: the effective bar feed and end delay (`market_data.scan_bars`, `sip_delay_minutes`)."""

import textwrap
from datetime import timedelta

import pytest
from pydantic import ValidationError

from agentic_trader.config import AppConfig, MarketDataConfig, load_config


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


@pytest.mark.parametrize("feed", ["sip", "iex"])
def test_configured_is_the_default_and_reads_the_configured_feed_undelayed(feed):
    market_data = MarketDataConfig(alpaca_feed=feed)

    assert (market_data.scan_bars, market_data.sip_delay_minutes) == ("configured", 16)
    assert market_data.bars_feed == feed
    assert market_data.bars_end_delay is None
    assert AppConfig().market_data.scan_bars == "configured"


@pytest.mark.parametrize("feed", ["sip", "iex"])
def test_sip_delayed_reads_sip_ending_the_delay_back_whatever_the_configured_feed(feed):
    market_data = MarketDataConfig(alpaca_feed=feed, scan_bars="sip_delayed")

    assert market_data.bars_feed == "sip"
    assert market_data.bars_end_delay == timedelta(minutes=16)
    assert MarketDataConfig(scan_bars="sip_delayed", sip_delay_minutes=30).bars_end_delay == timedelta(minutes=30)


@pytest.mark.parametrize("minutes", [15, 60])
def test_the_delay_accepts_its_bounds(minutes):
    assert MarketDataConfig(sip_delay_minutes=minutes).sip_delay_minutes == minutes


@pytest.mark.parametrize("bad", [{"sip_delay_minutes": 14}, {"sip_delay_minutes": 61}, {"scan_bars": "sip"}])
def test_out_of_range_values_are_refused(bad):
    with pytest.raises(ValidationError):
        MarketDataConfig(**bad)


@pytest.mark.parametrize("variable", ["ALPACA_DATA_FEED", "APCA_DATA_FEED"])
def test_the_data_feed_variable_moves_quotes_and_alpaca_feed_but_never_sip_delayed_scan_bars(tmp_path, variable):
    path = write(
        tmp_path,
        """
        market_data: {scan_bars: sip_delayed, sip_delay_minutes: 20}
        database: {path: test.db}
        """,
    )

    config = load_config(path, environ={**PRODUCTION, variable: "iex"})

    assert config.alpaca_data_feed == "iex"  # broker latest trade and quote
    assert config.market_data.alpaca_feed == "iex"
    assert config.market_data.bars_feed == "sip"
    assert config.market_data.bars_end_delay == timedelta(minutes=20)


def test_under_configured_the_data_feed_variable_still_selects_the_bar_feed(tmp_path):
    path = write(tmp_path, "database: {path: test.db}\n")

    config = load_config(path, environ={**PRODUCTION, "ALPACA_DATA_FEED": "iex"})

    assert config.market_data.scan_bars == "configured"
    assert (config.market_data.bars_feed, config.market_data.bars_end_delay) == ("iex", None)
