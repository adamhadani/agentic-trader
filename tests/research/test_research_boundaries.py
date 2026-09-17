import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.simulation import BracketIntent, simulate_execution
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy


@pytest.mark.parametrize("command", ["optimize", "retune"])
def test_unjournaled_retuning_and_config_export_are_not_available(command):
    result = CliRunner().invoke(cli, [command, "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output
    assert CliRunner().invoke(cli, ["alpha", "--help"]).exit_code == 0


@pytest.mark.parametrize("last_close,expected", [(101.0, [1.0, 1.01, 1.01, 1.01]), (103.0, [1.0, 1.01, 1.01, 1.03])])
def test_shared_execution_marks_accumulated_profit_exactly_once(last_close, expected):
    close = np.array([100.0, 101.0, 101.0, last_close])
    bars = pd.DataFrame(
        {"open": close, "high": close + 0.01, "low": close - 0.01, "close": close},
        index=pd.date_range("2026-01-01", periods=4, tz="UTC"),
    )
    result = simulate_execution(
        bars,
        {0: BracketIntent(1, 100.0, 98.5, 103.0, "2025-12-31T00:00:00Z")},
        AlphaExecutionPolicy(friction_per_side=0, trail_trigger_r=10),
    )
    np.testing.assert_allclose((1 + result["net_returns"]).cumprod(), expected)
    assert result["total_trades"] == int(last_close == 103)
