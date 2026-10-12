"""The daemon's corporate-action symbol union and the live flag."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.cli.commands.service import corporate_action_symbols
from agentic_trader.config import UniverseConfig, UniverseEntry, corporate_actions_live
from agentic_trader.constants import AssetClass


EQUITY, CRYPTO = AssetClass.EQUITY, AssetClass.CRYPTO


def _copilot(tracked, broker_positions=(), *, authoritative=True):
    db = SimpleNamespace(get_active_positions=AsyncMock(return_value=[{"contract": c} for c in tracked]))
    broker = SimpleNamespace(
        authoritative_positions=authoritative,
        get_positions=AsyncMock(
            return_value=[SimpleNamespace(symbol=symbol, asset_class=kind) for symbol, kind in broker_positions]
        ),
    )
    return SimpleNamespace(db=db, broker=broker)


def _config(app_config, universe=("SPY", "QQQ"), extra=()):
    return app_config.model_copy(
        update={
            "universe": UniverseConfig(groups={"test": [UniverseEntry(symbol=symbol) for symbol in universe]}),
            "corporate_actions": app_config.corporate_actions.model_copy(update={"extra_symbols": list(extra)}),
        }
    )


async def test_symbol_union_covers_universe_tracked_and_broker_held_equities_without_derivatives(app_config):
    config = _config(app_config, extra=("BRK.B",))
    copilot = _copilot(["xom", "/MES", "BTC/USD"], [("NVDA", EQUITY), ("BTCUSD", CRYPTO), ("xom", EQUITY)])
    symbols = await corporate_action_symbols(copilot, config)
    assert symbols == ["BRK.B", "NVDA", "QQQ", "SPY", "XOM"]  # Alpaca's crypto position has no slash


async def test_simulator_broker_positions_are_not_read(app_config):
    copilot = _copilot(["AAPL"], [("NVDA", EQUITY)], authoritative=False)
    symbols = await corporate_action_symbols(copilot, _config(app_config))
    assert symbols == ["AAPL", "QQQ", "SPY"]
    copilot.broker.get_positions.assert_not_awaited()


async def test_a_failed_broker_read_keeps_the_tracked_and_universe_names(app_config, caplog):
    copilot = _copilot(["AAPL"])
    copilot.broker.get_positions = AsyncMock(side_effect=ConnectionError("broker down at 10.0.0.1"))
    with caplog.at_level(logging.WARNING, logger="copilot"):
        symbols = await corporate_action_symbols(copilot, _config(app_config))
    assert symbols == ["AAPL", "QQQ", "SPY"]
    [record] = [
        r for r in caplog.records if getattr(r, "event", None) == "corporate_actions_broker_positions_unavailable"
    ]
    assert record.reason == "ConnectionError" and "broker down" not in record.getMessage()


def test_live_flag_needs_the_feature_and_both_credentials(app_config):
    on = app_config.model_copy(update={"alpaca_api_key": "k", "alpaca_api_secret": "s"})
    assert corporate_actions_live(on) is True
    assert corporate_actions_live(on.model_copy(update={"alpaca_api_secret": None})) is False
    off = on.model_copy(update={"corporate_actions": on.corporate_actions.model_copy(update={"enabled": False})})
    assert corporate_actions_live(off) is False
