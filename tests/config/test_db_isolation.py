from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest
from click.testing import CliRunner
from pytest_socket import SocketBlockedError

from agentic_trader.cli.main import cli
from agentic_trader.config import AppConfig, load_config, load_envrc
from agentic_trader.storage.db import SignalDatabase


def test_plain_model_does_not_consult_runtime_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/agentic_trader")
    with pytest.raises(ValueError, match="not configured"):
        _ = AppConfig().resolved_db_url


def test_explicit_settings_mapping_never_reads_dotenv(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("database: {url: 'sqlite+aiosqlite:///:memory:'}\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "runtime-secret")
    monkeypatch.setattr("agentic_trader.config.load_envrc", lambda *a: pytest.fail("Local secrets were read"))
    cfg = load_config(str(path), environ={})
    assert cfg.telegram_bot_token is None
    assert cfg.resolved_db_url == "sqlite+aiosqlite:///:memory:"


def test_dotenv_loading_does_not_mutate_process_environment(tmp_path):
    dotenv = tmp_path / "test.env"
    dotenv.write_text("TELEGRAM_BOT_TOKEN=test-sentinel\n")
    yaml = tmp_path / "config.yaml"
    yaml.write_text("{}")
    before = dict(os.environ)
    cfg = load_config(str(yaml), environ={"TELEGRAM_BOT_TOKEN": ""}, env_file=dotenv)
    assert cfg.telegram_bot_token is None
    assert dict(os.environ) == before


def test_import_is_side_effect_free():
    env = {key: value for key, value in os.environ.items() if key not in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; before=dict(os.environ); import agentic_trader.config; assert dict(os.environ)==before",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_explicit_path_overrides_loaded_database(tmp_path):
    cfg = load_config()
    cfg.db_path = str(tmp_path / "separate.db")
    assert cfg.resolved_db_url == f"sqlite+aiosqlite:///{tmp_path / 'separate.db'}"


def test_cli_db_path_option(tmp_path: Path):
    result = CliRunner().invoke(cli, ["--db-path", str(tmp_path / "cli.db"), "db", "current"])
    assert result.exit_code == 0, result.output


def test_production_database_rejected_before_migrations():
    with pytest.raises(ValueError, match="Refusing test database"):
        SignalDatabase(db_url="postgresql+asyncpg://localhost:5432/agentic_trader")


def test_native_postgres_connection_is_also_blocked():
    with pytest.raises(ValueError, match="Refusing test database"):
        psycopg2.connect("postgresql://localhost:5432/agentic_trader")


def test_production_sqlite_path_is_rejected():
    with pytest.raises(ValueError, match="Refusing test database"):
        SignalDatabase(db_path="data/signals.db")


def test_test_configuration_has_no_runtime_credentials():
    cfg = load_config()
    assert cfg.environment == "test"
    assert cfg.execution_mode == "paper"
    assert cfg.telegram_bot_token is None
    assert cfg.alpaca_api_key is None
    assert cfg.openai_api_key is None
    assert Path(cfg.db_path).is_relative_to(Path(os.environ["COPILOT_TEST_ROOT"]))


def test_external_sockets_are_disabled():
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_envrc_source_directives_are_not_executed(tmp_path):

    env_file = tmp_path / "sample.envrc"
    marker = tmp_path / "must-not-exist"
    env_file.write_text(
        f"source /nonexistent/venv/activate\n. /another/activate\nexport DB_NAME=test\n# touch {marker}\n"
    )
    assert load_envrc(env_file) == {"DB_NAME": "test"}
    assert not marker.exists()
