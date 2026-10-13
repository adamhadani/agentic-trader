"""`alpha corporate-actions`: symbols from --symbol and --cohort, private output files, no database."""

import json
from datetime import UTC, datetime

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_corporate_actions as cmd
from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from tests.data.test_corporate_actions import RAW


class FakeSource:
    def __init__(self):
        self.requests = []

    def fetch(self, symbols, start, end):
        self.requests.append((tuple(symbols), start, end))
        now = datetime.now(UTC)
        return CorporateActionsPage(normalize_actions(RAW), now, now, len(symbols), (start, end), 1, ("p",))


def test_command_writes_actions_and_manifest(tmp_path, monkeypatch):
    source = FakeSource()
    monkeypatch.setattr(cmd, "build_corporate_actions_source", lambda config: source)
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"sectors": {"energy": ["XOM"], "technology": ["NVDA", "AAPL"]}}))
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cmd.corporate_actions_cmd,
        [
            "--symbol",
            "pxd",
            "--cohort",
            str(cohort),
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert source.requests[0][0] == ("AAPL", "NVDA", "PXD", "XOM")
    actions = json.loads((output / "actions.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(actions) == 4 and manifest["count"] == 4 and manifest["symbols"] == ["AAPL", "NVDA", "PXD", "XOM"]
    assert manifest["request_sha256"] and manifest["content_sha256"] and "environment" in manifest
    assert "4 corporate actions" in result.output
    assert oct(output.stat().st_mode & 0o777) == "0o700"
    assert oct((tmp_path / "cache" / "corporate-actions").stat().st_mode & 0o777) == "0o700"


def test_command_refuses_an_existing_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cmd, "build_corporate_actions_source", lambda config: FakeSource())
    output = tmp_path / "out"
    output.mkdir()
    result = CliRunner().invoke(
        cmd.corporate_actions_cmd,
        [
            "--symbol",
            "AAPL",
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0 and "exists" in result.output
