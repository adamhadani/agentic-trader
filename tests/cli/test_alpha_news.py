"""`alpha news`: symbols from --symbol and --cohort, private gzip output, no database."""

import gzip
import json

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_news as cmd
from agentic_trader.cli.main import alpha_group
from agentic_trader.data.news import NewsPage, normalize_news
from tests.data.test_news import row


class FakeSource:
    def __init__(self):
        self.requests = []

    def fetch(self, symbols, start, end):
        self.requests.append((tuple(symbols), start, end))
        rows = [
            row(1, "a", ["AAPL"], created_at="2024-03-02T12:00:00Z"),
            row(2, "b", ["XOM"], created_at="2024-03-03T12:00:00Z"),
        ]
        return NewsPage(normalize_news(rows), start, end, tuple(symbols), (start, end), 1, ("p",), False)


def test_command_writes_items_and_manifest(tmp_path, monkeypatch):
    source = FakeSource()
    monkeypatch.setattr(cmd, "build_news_source", lambda config: source)
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"sectors": {"energy": ["XOM"], "technology": ["NVDA", "AAPL"]}}))
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cmd.news_cmd,
        [
            "--symbol",
            "pxd",
            "--cohort",
            str(cohort),
            "--start",
            "2024-03-01",
            "--end",
            "2024-03-31",
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert source.requests[0][0] == ("AAPL", "NVDA", "PXD", "XOM")
    items = json.loads(gzip.decompress((output / "news.json.gz").read_bytes()))
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(items) == 2 and manifest["count"] == 2 and manifest["symbols"] == ["AAPL", "NVDA", "PXD", "XOM"]
    assert manifest["request_sha256"] and manifest["content_sha256"] and "environment" in manifest
    assert "2 news items" in result.output
    assert oct(output.stat().st_mode & 0o777) == "0o700"
    assert oct((tmp_path / "cache" / "news").stat().st_mode & 0o777) == "0o700"


def test_command_refuses_an_existing_output_directory_and_needs_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(cmd, "build_news_source", lambda config: FakeSource())
    output = tmp_path / "out"
    output.mkdir()
    args = [
        "--symbol",
        "AAPL",
        "--start",
        "2024-03-01",
        "--end",
        "2024-03-31",
        "--cache",
        str(tmp_path / "cache"),
        "--output",
        str(output),
    ]
    result = CliRunner().invoke(cmd.news_cmd, args)
    assert result.exit_code != 0 and "exists" in result.output
    result = CliRunner().invoke(
        cmd.news_cmd,
        [
            "--start",
            "2024-03-01",
            "--end",
            "2024-03-31",
            "--cache",
            str(tmp_path / "c"),
            "--output",
            str(tmp_path / "o"),
        ],
    )
    assert result.exit_code != 0 and "--symbol" in result.output


def test_command_is_registered():
    assert "news" in alpha_group.commands
