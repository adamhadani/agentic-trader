from __future__ import annotations

import click

from agentic_trader.config import load_config
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.migrations import (
    downgrade_migrations,
    get_alembic_config,
    get_current_revision,
    get_history,
    run_migrations_head,
)
from alembic import command as alembic_command


@click.group("db", help="Database migration and schema management commands")
def db_group() -> None:
    """Database migration and schema management commands."""


@db_group.command("upgrade", help="Upgrade database schema")
@click.argument("revision", default="head", required=False)
def upgrade(revision: str) -> None:
    """Upgrade database schema to specified revision (default: 'head')."""
    config = load_config()
    db_url = SignalDatabase(config.db_path).db_url
    if revision == "head":
        run_migrations_head(db_url)
    else:
        cfg = get_alembic_config(db_url)
        alembic_command.upgrade(cfg, revision)
    curr = get_current_revision(db_url)
    click.echo(f"✅ Database upgraded successfully to revision: {curr}")


@db_group.command("downgrade", help="Downgrade database schema")
@click.argument("revision", default="base", required=False)
def downgrade(revision: str) -> None:
    """Downgrade database schema to specified revision (default: 'base')."""
    config = load_config()
    db_url = SignalDatabase(config.db_path).db_url
    downgrade_migrations(revision, db_url)
    curr = get_current_revision(db_url)
    click.echo(f"✅ Database downgraded to revision: {curr or '<base>'}")


@db_group.command("current", help="Show current database migration revision")
def current() -> None:
    """Show current database migration revision."""
    config = load_config()
    db_url = SignalDatabase(config.db_path).db_url
    curr = get_current_revision(db_url)
    click.echo(f"Current database revision: {curr or '<unversioned/empty>'}")


@db_group.command("history", help="Show database migration history")
def history() -> None:
    """Show database migration history."""
    config = load_config()
    db_url = SignalDatabase(config.db_path).db_url
    hist = get_history(db_url)
    click.echo("Migration History:")
    for h in hist:
        click.echo(f"  • {h['revision']} (down: {h['down_revision']}) - {h['doc']}")
