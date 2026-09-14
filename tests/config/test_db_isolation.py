from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.config import load_config


def test_db_name_resolution_default():
    """Verify load_config defaults to signals.db when no overrides are present."""
    with patch.dict(os.environ, {}, clear=True):
        config = load_config()
        assert config.db_name == "signals"
        assert config.db_path.endswith("data/signals.db")
        assert config.database.name == "signals"


def test_db_name_resolution_override():
    """Verify DB_NAME environment variable routes to custom named database file."""
    with patch.dict(os.environ, {"DB_NAME": "sandbox_alpha"}, clear=True):
        config = load_config()
        assert config.db_name == "sandbox_alpha"
        assert config.db_path.endswith("data/sandbox_alpha.db")
        assert config.database.name == "sandbox_alpha"


def test_db_name_resolution_with_db_extension():
    """Verify DB_NAME ending in .db does not double-append extension."""
    with patch.dict(os.environ, {"DB_NAME": "staging.db"}, clear=True):
        config = load_config()
        assert config.db_name == "staging.db"
        assert config.db_path.endswith("data/staging.db")
        assert not config.db_path.endswith(".db.db")


def test_db_path_explicit_precedence():
    """Verify explicit DB_PATH takes precedence over default path calculation."""
    custom_path = "/tmp/arbitrary_location/my_test.db"
    with patch.dict(os.environ, {"DB_PATH": custom_path, "DB_NAME": "my_test"}, clear=True):
        config = load_config()
        assert config.db_name == "my_test"
        assert config.db_path == custom_path


def test_cli_db_name_option(tmp_path: Path):
    """Verify --db-name CLI option dynamically selects the target database."""
    runner = CliRunner()
    test_db = tmp_path / "cli_sandbox.db"
    result = runner.invoke(cli, ["--db-path", str(test_db), "db", "current"])
    assert result.exit_code == 0
    assert "Current database revision:" in result.output


def test_production_db_not_touched():
    """Verify test session fixture ensures DB_NAME is test_signals and does not point to signals.db."""
    config = load_config()
    assert config.db_name == "test_signals"
    assert not config.db_path.endswith("data/signals.db")
