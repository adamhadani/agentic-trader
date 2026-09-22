import pytest

from agentic_trader.constants import ExecutionMode, executes_asset_class


@pytest.mark.parametrize(
    ("mode", "asset_class", "expected"),
    [
        (ExecutionMode.ALPACA, "EQUITY", True),
        ("alpaca", "equities", True),
        (ExecutionMode.ALPACA, "futures", False),
        (ExecutionMode.ALPACA, "CRYPTO", False),
        (ExecutionMode.PAPER, "futures", True),
        (ExecutionMode.TRADOVATE, "FUTURES", True),
        ("unknown", "futures", True),
    ],
)
def test_executes_asset_class(mode, asset_class, expected):
    assert executes_asset_class(mode, asset_class) is expected
