import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from alembic import command


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def to_sync_url(url: str) -> str:
    """Convert an async database URL (e.g. sqlite+aiosqlite, postgresql+asyncpg) to its synchronous equivalent."""
    if url.startswith("sqlite+aiosqlite:///"):
        return url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


def get_alembic_config(db_url: str | None = None) -> Config:
    """Construct an Alembic Config object pointing to the project's alembic.ini and script location."""
    ini_path = PROJECT_ROOT / "alembic.ini"
    if not ini_path.exists():
        raise FileNotFoundError(f"Alembic configuration file not found at {ini_path}")

    config = Config(str(ini_path))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    if db_url:
        config.set_main_option("sqlalchemy.url", db_url)

    return config


def run_migrations_head(db_url: str | None = None) -> None:
    """Apply all pending Alembic migrations up to head."""
    config = get_alembic_config(db_url)
    target_url = config.get_main_option("sqlalchemy.url")
    logger.info("Applying Alembic migrations to head", extra={"db_url": target_url})
    command.upgrade(config, "head")


def downgrade_migrations(target: str = "base", db_url: str | None = None) -> None:
    """Downgrade database schema to target (e.g. 'base' or a specific revision hash)."""
    config = get_alembic_config(db_url)
    logger.info("Downgrading Alembic migrations", extra={"target": target})
    command.downgrade(config, target)


def get_current_revision(db_url: str | None = None) -> str | None:
    """Inspect and return the current Alembic revision hash for the target database."""
    config = get_alembic_config(db_url)
    target_url = config.get_main_option("sqlalchemy.url") or "sqlite+aiosqlite:///data/signals.db"
    sync_url = to_sync_url(target_url)

    # For sqlite databases, if the file doesn't exist yet, there is no revision
    if sync_url.startswith("sqlite:///"):
        path_part = sync_url.replace("sqlite:///", "", 1)
        if not Path(path_part).exists():
            return None

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    except Exception as e:
        logger.warning(f"Could not retrieve current revision: {e}", extra={"sync_url": sync_url})
        return None
    finally:
        engine.dispose()


def get_history(db_url: str | None = None) -> list[dict[str, Any]]:
    """Return a chronological or topological list of migration revisions."""
    config = get_alembic_config(db_url)
    script = ScriptDirectory.from_config(config)
    history: list[dict[str, Any]] = []
    for rev in script.walk_revisions():
        down_rev: str | Sequence[str] | None = rev.down_revision
        history.append(
            {
                "revision": rev.revision,
                "down_revision": down_rev,
                "doc": rev.doc,
            }
        )
    return history
