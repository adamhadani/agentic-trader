import asyncio
import concurrent.futures
import logging
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from agentic_trader.runtime import validate_test_database
from agentic_trader.storage.models import Base
from alembic import context


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Model metadata for autogenerate support
target_metadata = Base.metadata


def get_url() -> str:
    """Resolve database URL prioritizing explicit config, env vars, or local SQLite."""
    cfg_url = config.get_main_option("sqlalchemy.url")
    if cfg_url and cfg_url not in (
        "driver://user:pass@localhost/dbname",
        "sqlite+aiosqlite:///data/signals.db",
    ):
        url = cfg_url
    elif os.getenv("DATABASE_URL"):
        url = os.environ["DATABASE_URL"]
    elif os.getenv("DB_PATH"):
        db_path = Path(os.environ["DB_PATH"]).resolve()
        url = f"sqlite+aiosqlite:///{db_path}"
    elif os.getenv("DB_NAME"):
        db_name = os.environ["DB_NAME"]
        db_filename = db_name if db_name.endswith(".db") else f"{db_name}.db"
        db_path = Path(f"data/{db_filename}").resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"
    else:
        db_path = Path("data/signals.db").resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"

    # For async engine, ensure appropriate async dialect driver
    if url.startswith("sqlite:///") and not url.startswith("sqlite+aiosqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif url.startswith("postgresql+psycopg2://"):
        url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    validate_test_database(url)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations online using an async engine."""
    url = get_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode, handling existing event loops if present."""
    provided_connection = config.attributes.get("connection", None)
    if provided_connection is not None:
        do_run_migrations(provided_connection)
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, run_async_migrations())
            future.result()
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
