from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import click

from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.broker import create_broker
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.execution.durable import WorkKind
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.ledger import LedgerStore
from agentic_trader.storage.maintenance import RetentionService
from agentic_trader.storage.migrations import (
    downgrade_migrations,
    get_alembic_config,
    get_current_revision,
    get_history,
    run_migrations_head,
)
from agentic_trader.storage.operations import OperationsStore
from alembic import command as alembic_command


@click.group("db", help="Database migration and schema management commands")
def db_group() -> None:
    """Database migration and schema management commands."""


@db_group.command("upgrade", help="Upgrade database schema")
@click.argument("revision", default="head", required=False)
def upgrade(revision: str) -> None:
    """Upgrade database schema to specified revision (default: 'head')."""
    config = load_config()
    db_url = config.resolved_db_url
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
    db_url = config.resolved_db_url
    downgrade_migrations(revision, db_url)
    curr = get_current_revision(db_url)
    click.echo(f"✅ Database downgraded to revision: {curr or '<base>'}")


@db_group.command("current", help="Show current database migration revision")
def current() -> None:
    """Show current database migration revision."""
    config = load_config()
    db_url = config.resolved_db_url
    curr = get_current_revision(db_url)
    click.echo(f"Current database revision: {curr or '<unversioned/empty>'}")


@db_group.command("history", help="Show database migration history")
def history() -> None:
    """Show database migration history."""
    config = load_config()
    db_url = config.resolved_db_url
    hist = get_history(db_url)
    click.echo("Migration History:")
    for h in hist:
        click.echo(f"  • {h['revision']} (down: {h['down_revision']}) - {h['doc']}")


@db_group.command("clear", help="Clear all historical signals and positions from database")
@click.option("--yes", "-y", is_flag=True, default=False, help="Confirm clearing database without interactive prompt")
@coro
async def clear(yes: bool) -> None:
    """Clear all historical signals and positions from database."""
    if not yes and not click.confirm("Are you sure you want to clear all signal and position records?"):
        click.echo("Aborted.")
        return
    config = load_config()
    db = SignalDatabase(db_url=config.resolved_db_url)
    count = await db.clear_all_signals()
    click.echo(f"✅ Successfully cleared {count} signal/position record(s). Database is fresh.")


@db_group.command("audit", help="Read recent operational evidence as JSON")
@click.option("--signal-id", type=int, default=None)
@click.option("--limit", type=click.IntRange(1, 1000), default=100)
@coro
async def audit(signal_id: int | None, limit: int) -> None:
    config = load_config()
    db = SignalDatabase(config=config)
    try:
        click.echo(json.dumps(await db.get_audit_events(signal_id, limit), indent=2))
    finally:
        await db.engine.dispose()


@db_group.command("events", help="Read the append-only execution journal (newest first)")
@click.option("--stream", default=None, help="Exact stream, e.g. order/<broker-id> or entry/<client-id>")
@click.option("--limit", type=click.IntRange(1, 1000), default=100)
@coro
async def events(stream: str | None, limit: int) -> None:
    db = SignalDatabase(config=load_config())
    try:
        click.echo(json.dumps(await db.workflows.events(stream, limit=limit), indent=2))
    finally:
        await db.engine.dispose()


@db_group.command("queue", help="Inspect durable entry authorizations and outcomes")
@coro
async def queue() -> None:
    db = SignalDatabase(config=load_config())
    try:
        for item in await db.workflows.list_work(WorkKind.ENTRY):
            click.echo(json.dumps(asdict(item), default=str))
    finally:
        await db.engine.dispose()


@db_group.command("outbox", help="Inspect notifications; explicitly requeue a dead letter")
@click.option(
    "--retry", "retry_id", default=None, help="Dead-letter ID to requeue; may duplicate a previously delivered message"
)
@coro
async def outbox(retry_id: str | None) -> None:
    db = SignalDatabase(config=load_config())
    try:
        if retry_id and not await db.workflows.requeue_notification(retry_id):
            raise click.ClickException("No matching dead letter in this environment/account.")
        for item in await db.workflows.list_work(WorkKind.NOTIFICATION):
            click.echo(json.dumps(asdict(item), default=str))
    finally:
        await db.engine.dispose()


@db_group.command("orders", help="Inspect broker order projections or rebuild them from journal events")
@click.option("--rebuild", is_flag=True, help="Rebuild this account's read model; never submits an order")
@coro
async def orders(rebuild: bool) -> None:
    db = SignalDatabase(config=load_config())
    try:
        if rebuild:
            count = await db.workflows.rebuild_order_views()
            click.echo(f"Replayed {count} observations.")
        for order in await db.workflows.order_views():
            click.echo(order.model_dump_json())
    finally:
        await db.engine.dispose()


@db_group.command("ledger", help="Inspect account reconciliation; optionally refresh read-only broker evidence")
@click.option("--sync", "sync_broker", is_flag=True, help="Import account activities; no orders or messages")
@click.option("--rebuild", is_flag=True, help="Replay activity/checkpoint projections from the journal")
@coro
async def ledger(sync_broker: bool, rebuild: bool) -> None:

    config = load_config()
    db = SignalDatabase(config=config)
    try:
        store = LedgerStore(db.workflows)
        if rebuild:
            click.echo(f"Replayed {await store.rebuild()} ledger events.")
        if sync_broker:
            broker = create_broker(config)
            if not broker.supports_activity_ledger:
                raise click.ClickException("Broker does not support an activity ledger")
            await AccountLedgerService(broker, store, config.accounting).refresh()
        status = await store.status()
        # Account identity and raw broker evidence remain in private DB inspection.
        status.pop("snapshot", None)
        click.echo(json.dumps(status, indent=2))
        if sync_broker and (status.get("error") or status.get("report", {}).get("issues")):
            raise click.ClickException("Account reconciliation failed; inspect the reported issues")
    finally:
        await db.engine.dispose()


@db_group.command("activities", help="Inspect individual broker activities with exact execution/order IDs")
@coro
async def activities() -> None:

    db = SignalDatabase(config=load_config())
    try:
        for activity in await LedgerStore(db.workflows).activities():
            click.echo(json.dumps(activity))
    finally:
        await db.engine.dispose()


@db_group.command("incidents", help="Inspect operational incidents or replay their lifecycle state")
@click.option("--rebuild", is_flag=True, help="Replay state only; never resend notifications")
@coro
async def incidents(rebuild: bool) -> None:
    db = await asyncio.to_thread(SignalDatabase, config=load_config())
    try:
        store = OperationsStore(db.workflows)
        if rebuild:
            click.echo(f"Replayed {await store.rebuild()} incident changes.")
        click.echo(json.dumps(await store.incidents(), indent=2))
    finally:
        await db.engine.dispose()


@db_group.command("retention", help="Preview redundant healthy-observation compaction")
@click.option(
    "--apply", is_flag=True, help="Compact one bounded batch; preserve failures, transitions and financial history"
)
@coro
async def retention(apply: bool) -> None:
    config = load_config()
    db = await asyncio.to_thread(SignalDatabase, config=config)
    try:
        click.echo(json.dumps(await RetentionService(db.workflows, config.operations).sweep(apply=apply), indent=2))
    finally:
        await db.engine.dispose()
