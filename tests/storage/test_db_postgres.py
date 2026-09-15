from agentic_trader.config import AppConfig, DatabaseConfig, load_config


def test_resolved_db_url_priority(tmp_path, monkeypatch):
    path = tmp_path / "explicit.db"
    cfg = AppConfig(database=DatabaseConfig(url="postgresql://production/agentic_trader"), db_path=str(path))
    assert cfg.resolved_db_url == f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", "postgresql://remote/trader_prod")
    assert cfg.resolved_db_url == f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DB_PATH", "")
    assert load_config().resolved_db_url == "postgresql+asyncpg://remote/trader_prod"
    monkeypatch.setenv("DB_PATH", str(path))
    assert load_config().resolved_db_url == f"sqlite+aiosqlite:///{path}"
