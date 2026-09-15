from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.research.alpha import AlphaCatalog
from agentic_trader.research.alpha.promotion import AlphaPromotionManager


def test_cli_alpha_catalog():
    runner = CliRunner()
    res = runner.invoke(cli, ["alpha", "catalog"])
    assert res.exit_code == 0
    assert "INSTITUTIONAL FORMULAIC ALPHA CATALOG" in res.output
    assert "alpha_wq_006" in res.output
    assert "alpha_wq_053" in res.output


def test_cli_alpha_list():
    runner = CliRunner()
    res = runner.invoke(cli, ["alpha", "list"])
    assert res.exit_code == 0
    assert "PRODUCTION PROMOTED ALPHAS" in res.output


def test_cli_alpha_inspect_catalog_item(monkeypatch, sample_ohlcv_df):
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.yf.download", lambda *a, **kw: sample_ohlcv_df)
    runner = CliRunner()
    res = runner.invoke(cli, ["alpha", "inspect", "alpha_wq_006"])
    assert res.exit_code == 0
    assert "QUANTITATIVE TEARSHEET: ALPHA_WQ_006" in res.output
    assert "ts_corr" in res.output


def test_cli_alpha_promote_and_demote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    temp_yaml = tmp_path / "promoted_alphas.yaml"
    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.AlphaPromotionManager",
        lambda *args, **kwargs: AlphaPromotionManager(config_path=temp_yaml),
    )

    runner = CliRunner()
    # Promote alpha_wq_006 with symbols
    res_prom = runner.invoke(
        cli,
        [
            "alpha",
            "promote",
            "alpha_wq_006",
            "--allocation",
            "0.20",
            "--symbols",
            "AAPL,MSFT",
            "--notes",
            "CLI Promotion Test",
        ],
    )
    assert res_prom.exit_code == 0
    assert "SUCCESS: Promoted 'alpha_wq_006'" in res_prom.output
    assert "AAPL, MSFT" in res_prom.output

    # List promoted alphas and verify ELIGIBLE SYMBOLS column
    res_list = runner.invoke(cli, ["alpha", "list"])
    assert res_list.exit_code == 0
    assert "ELIGIBLE SYMBOLS" in res_list.output
    assert "AAPL, MSFT" in res_list.output

    # Demote alpha_wq_006
    res_dem = runner.invoke(
        cli,
        ["alpha", "demote", "alpha_wq_006", "--reason", "Testing retirement"],
    )
    assert res_dem.exit_code == 0
    assert "SUCCESS: Demoted 'alpha_wq_006'" in res_dem.output


def test_cli_alpha_demote_with_orphan_positions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    temp_yaml = tmp_path / "promoted_alphas.yaml"
    mgr = AlphaPromotionManager(config_path=temp_yaml)
    catalog = AlphaCatalog()
    mgr.promote(catalog.get("alpha_wq_006"), allocation_weight=0.10)

    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.AlphaPromotionManager",
        lambda *args, **kwargs: mgr,
    )

    mock_copilot = MagicMock()
    mock_copilot.db.get_active_positions = AsyncMock(
        return_value=[
            {
                "id": 42,
                "symbol": "AMD",
                "strategy": "alpha_wq_006",
                "direction": "LONG",
                "quantity": 10.0,
                "entry_price": 150.0,
            }
        ]
    )
    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.get_copilot_and_config",
        lambda: (mock_copilot, MagicMock()),
    )

    runner = CliRunner()
    res = runner.invoke(cli, ["alpha", "demote", "alpha_wq_006"])
    assert res.exit_code == 0
    assert "FOUND 1 ACTIVE POSITION(S) ATTRIBUTED TO 'alpha_wq_006'" in res.output
    assert "ACTION NOTICE: Positions remain open under orphan status" in res.output


def test_cli_alpha_demote_with_liquidate_positions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    temp_yaml = tmp_path / "promoted_alphas.yaml"
    mgr = AlphaPromotionManager(config_path=temp_yaml)
    catalog = AlphaCatalog()
    mgr.promote(catalog.get("alpha_wq_006"), allocation_weight=0.10)

    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.AlphaPromotionManager",
        lambda *args, **kwargs: mgr,
    )

    mock_copilot = MagicMock()
    mock_copilot.db.get_active_positions = AsyncMock(
        return_value=[
            {
                "id": 99,
                "symbol": "NVDA",
                "strategy": "alpha_wq_006",
                "direction": "LONG",
                "quantity": 5.0,
                "entry_price": 120.0,
            }
        ]
    )
    mock_copilot.broker.connect = AsyncMock()
    mock_copilot.close_position_manual = AsyncMock(return_value="Position #99 closed at $125.00")
    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.get_copilot_and_config",
        lambda: (mock_copilot, MagicMock()),
    )

    runner = CliRunner()
    res = runner.invoke(cli, ["alpha", "demote", "alpha_wq_006", "--liquidate-positions"])
    assert res.exit_code == 0
    assert "FOUND 1 ACTIVE POSITION(S) ATTRIBUTED TO 'alpha_wq_006'" in res.output
    assert "--liquidate-positions specified: Closing positions immediately..." in res.output
    assert "All attributed positions have been closed." in res.output
    mock_copilot.close_position_manual.assert_called_once_with(99)
