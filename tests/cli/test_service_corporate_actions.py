"""The daemon's corporate-action symbol union and the live flag."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.cli.commands.service import corporate_action_symbols
from agentic_trader.config import corporate_actions_live


def _copilot(tracked, broker_positions=None, *, authoritative=True):
    db = SimpleNamespace(get_active_positions=AsyncMock(return_value=[{"contract": c} for c in tracked]))
    broker = SimpleNamespace(
        authoritative_positions=authoritative,
        get_positions=AsyncMock(return_value=[SimpleNamespace(symbol=s) for s in (broker_positions or [])]),
    )
    return SimpleNamespace(db=db, broker=broker)


def _config(app_config, universe=("SPY", "QQQ"), extra=()):
    config = app_config.model_copy(deep=True)
    config.universe.groups = {"test": [type(config.universe.groups.get("x", [None])[0] or object)() for _ in ()]}
    return config.model_copy(
        update={
            "universe": app_config.universe.model_copy(update={"groups": {}}),
            "corporate_actions": app_config.corporate_actions.model_copy(update={"extra_symbols": list(extra)}),
        }
    ), set(universe)


async def test_symbol_union_covers_tracked_and_broker_held_equities_without_derivatives(app_config):
    config, _ = _config(app_config, extra=("BRK.B",))
    copilot = _copilot(["xom", "/MES", "BTC/USD"], ["NVDA", "ETH/USD", "xom"])
    symbols = await corporate_action_symbols(copilot, config)
    assert symbols == sorted(set(config.universe.symbols) | {"XOM", "NVDA", "BRK.B"})
    assert all("/" not in s for s in symbols)


async def test_simulator_broker_positions_are_not_read(app_config):
    config, _ = _config(app_config)
    copilot = _copilot(["AAPL"], ["NVDA"], authoritative=False)
    symbols = await corporate_action_symbols(copilot, config)
    assert "AAPL" in symbols and "NVDA" not in symbols
    copilot.broker.get_positions.assert_not_awaited()


def test_live_flag_needs_the_feature_and_both_credentials(app_config):
    on = app_config.model_copy(update={"alpaca_api_key": "k", "alpaca_api_secret": "s"})
    assert corporate_actions_live(on) is True
    assert corporate_actions_live(on.model_copy(update={"alpaca_api_secret": None})) is False
    off = on.model_copy(update={"corporate_actions": on.corporate_actions.model_copy(update={"enabled": False})})
    assert corporate_actions_live(off) is False
