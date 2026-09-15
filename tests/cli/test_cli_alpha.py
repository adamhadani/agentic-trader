from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
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


def test_cli_alpha_inspect_catalog_item():
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
    # Promote alpha_wq_006
    res_prom = runner.invoke(
        cli,
        ["alpha", "promote", "alpha_wq_006", "--allocation", "0.20", "--notes", "CLI Promotion Test"],
    )
    assert res_prom.exit_code == 0
    assert "SUCCESS: Promoted 'alpha_wq_006'" in res_prom.output

    # Demote alpha_wq_006
    res_dem = runner.invoke(
        cli,
        ["alpha", "demote", "alpha_wq_006", "--reason", "Testing retirement"],
    )
    assert res_dem.exit_code == 0
    assert "SUCCESS: Demoted 'alpha_wq_006'" in res_dem.output
