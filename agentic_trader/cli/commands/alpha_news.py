"""Research-only news acquisition (headlines, no bodies) for a symbol list or a frozen cohort file."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.research.corporate_actions import cohort_symbols
from agentic_trader.research.news import build_news_source, fetch_news_cached


async def _noop_pace() -> None:
    return None


def _write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


@click.command("news")
@click.option("--symbol", "symbols", multiple=True, help="A symbol; repeatable.")
@click.option(
    "--cohort", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="A frozen cohort JSON file."
)
@click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--cache", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--chunk-symbols", default=20, show_default=True, type=click.IntRange(1, 200))
@coro
async def news_cmd(symbols, cohort: Path | None, start, end, cache: Path, output: Path, chunk_symbols: int):
    """Fetch and cache news headlines (created_at window) for research; no database, no trading."""
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    try:
        wanted = list(symbols) + (cohort_symbols(cohort) if cohort else [])
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not wanted:
        raise click.ClickException("Pass --symbol and/or --cohort")
    start_date, end_date = start.date(), end.date()
    if end_date < start_date:
        raise click.ClickException("--end must not precede --start")
    try:
        source = build_news_source(load_config())
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        items, manifest = await fetch_news_cached(
            source, wanted, start_date, end_date, cache, pace=_noop_pace, chunk_symbols=chunk_symbols
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    rows = [item.model_dump(mode="json") for item in items]
    _write_private(output / "news.json.gz", gzip.compress(json.dumps(rows, sort_keys=True).encode()))
    _write_private(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str).encode())
    click.echo(f"{len(items)} news items for {len(manifest['symbols'])} symbols; {output / 'manifest.json'}")
