"""Public commands use journal evidence, never direct unverified YAML promotion."""

import json

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.models import AlphaDefinition


@pytest.mark.parametrize("command", ["catalog", "list", "export"])
def test_alpha_read_commands(command):
    result = CliRunner().invoke(cli, ["alpha", command])
    assert result.exit_code == 0, result.output


def test_catalog_inspect_is_read_only():
    result = CliRunner().invoke(cli, ["alpha", "inspect", "alpha_wq_006"])
    assert result.exit_code == 0, result.output
    assert "ts_corr" in result.output


def test_import_shadow_promote_rejected_demote(tmp_path):
    path = tmp_path / "alphas.yaml"
    path.write_text(
        "promoted_alphas:\n- definition:\n    alpha_id: alpha_test\n    name: Test\n    expression: close\n"
    )
    runner = CliRunner()
    imported = runner.invoke(cli, ["alpha", "import", str(path)])
    assert imported.exit_code == 0, imported.output
    exported = runner.invoke(cli, ["alpha", "export"])
    snapshot = json.loads(exported.output)
    assert len(snapshot["shadow"]) == 1

    version = AlphaDefinition.from_dict(snapshot["shadow"][0]).version_id
    promoted = runner.invoke(cli, ["alpha", "promote", version, "--generation", "1"])
    assert promoted.exit_code != 0
    assert "qualification" in promoted.output
    demoted = runner.invoke(cli, ["alpha", "demote", version, "--generation", "1"])
    assert demoted.exit_code == 0, demoted.output
    assert "existing positions retain protection" in demoted.output


def test_unattended_auto_promotion_removed():
    result = CliRunner().invoke(cli, ["alpha", "mine", "--auto-promote"])
    assert result.exit_code != 0
    assert "No such option" in result.output
