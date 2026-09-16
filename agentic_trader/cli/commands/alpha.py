"""Journal-backed alpha research, qualification and version lifecycle commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from uuid import uuid4

import click
import pandas as pd
import yfinance as yf

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.research.alpha.baselines import benchmark_models
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.data import completed_bars, load_dataset, save_dataset
from agentic_trader.research.alpha.forecasts import CombinedForecast
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.portfolio import PortfolioPolicy, PortfolioSnapshot, build_shadow_portfolio
from agentic_trader.research.alpha.promotion import AlphaPromotionService, read_alpha_definitions
from agentic_trader.research.alpha.universe import ETF_RESEARCH_UNIVERSE
from agentic_trader.research.alpha.validation import DatasetManifest
from agentic_trader.runtime import runtime_identity
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


@asynccontextmanager
async def alpha_repository():
    config = load_config()
    db = SignalDatabase(db_url=config.resolved_db_url, config=config)
    try:
        await db.init_db()
        yield AlphaRepository(db.workflows, policy=config.alpha_pipeline)
    finally:
        await db.engine.dispose()


def research_environment():
    root = Path(__file__).resolve().parents[3]
    return {
        "runtime": runtime_identity(),
        "python": platform.python_version(),
        "lock_hash": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "packages": {
            name: package_version(name) for name in ("numpy", "pandas", "scipy", "cvxpy", "scikit-learn", "alpaca-py")
        },
    }


def artifact_directory() -> Path:
    root = os.environ.get("COPILOT_TEST_ROOT")
    return Path(root) / "research" if root else Path.home() / ".local/state/agentic-trader/research"


def download_bars(symbol, lookback, interval, *, feed="yfinance", config=None):
    requested = "1h" if interval == "4h" else interval
    if feed == "alpaca":
        if config is None:
            raise ValueError("Explicit Alpaca research configuration required")
        provider = AlpacaDataProvider(
            api_key=config.alpaca_api_key, api_secret=config.alpaca_api_secret, feed=config.market_data.alpaca_feed
        )
        frame = provider.fetch_bars(symbol, requested, period=lookback)
        if (
            frame.attrs.get("feed") != f"alpaca:{config.market_data.alpaca_feed}"
            or frame.attrs.get("adjustment") != "raw"
        ):
            raise ValueError("Research requires the declared raw Alpaca stock feed; observations cannot be relabeled")
    else:
        frame = yf.download(symbol, period=lookback, interval=requested, auto_adjust=False, progress=False)
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame[[c for c in frame.columns if c.lower() in ("open", "high", "low", "close", "volume")]].astype(float)
    if frame.empty:
        raise ValueError(f"No observations for {symbol}")
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame = completed_bars(frame, requested)
    if interval == "4h":
        frame = (
            frame.resample("4h")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna()
        )
    frame.attrs.update(
        feed="yfinance" if feed == "yfinance" else f"alpaca:{config.market_data.alpaca_feed}", adjustment="raw"
    )
    return completed_bars(frame, interval)


@click.group("alpha", help="Causal formula research and journal-backed promotion")
def alpha_group():
    pass


@alpha_group.command("catalog")
def alpha_catalog_cmd():
    click.echo("FORMULAIC ALPHA CATALOG (research hypotheses; validation required)")
    for definition in AlphaCatalog().list_alphas():
        click.echo(f"{definition.alpha_id}: {definition.name} | {definition.expression}")


@alpha_group.command("list")
@coro
async def alpha_list_cmd():
    async with alpha_repository() as repository:
        snapshot = await repository.snapshot()
        active = {d.version_id for d in snapshot.active}
        shadow = {d.version_id for d in snapshot.shadow}
        click.echo(f"ALPHA REGISTRY — generation {snapshot.generation}")
        for definition in await repository.versions():
            status = (
                "active"
                if definition.version_id in active
                else "shadow"
                if definition.version_id in shadow
                else "inactive"
            )
            click.echo(
                f"{definition.alpha_id} {definition.version_id} | {status} | {definition.timeframe} | {','.join(definition.eligible_symbols or ()) or 'unqualified universe'}"
            )


@alpha_group.command("mine")
@click.option("--symbol", default="SPY")
@click.option("--symbols", default="")
@click.option("--lookback", default="5y")
@click.option("--interval", type=click.Choice(["1d", "4h", "1h", "15m"]), default="1d")
@click.option("--iterations", type=click.IntRange(0, 10000), default=25)
@click.option("--seed", type=int, default=20260916)
@click.option("--method", type=click.Choice(["random", "genetic"]), default="random")
@click.option("--universe", type=click.Choice(["explicit", "etf32"]), default="explicit")
@click.option("--feed", type=click.Choice(["yfinance", "alpaca"]), default="yfinance")
@click.option(
    "--max-seconds",
    type=click.FloatRange(min=1),
    default=300,
    help="Per-symbol compute deadline; completed trials are checkpointed",
)
@click.option("--min-sharpe", type=float, default=1.0)
@click.option("--min-dsr", type=click.FloatRange(0, 1), default=0.95)
@coro
async def alpha_mine_cmd(
    symbol, symbols, lookback, interval, iterations, seed, min_sharpe, min_dsr, method, universe, feed, max_seconds
):
    """Persist every trial. Mining never consumes holdout or promotes an alpha."""
    symbol_universe = (
        ETF_RESEARCH_UNIVERSE.symbols
        if universe == "etf32"
        else tuple(sorted({s.strip().upper() for s in (symbols or symbol).split(",") if s.strip()}))
    )
    config = load_config()
    failures = 0
    async with alpha_repository() as repository:
        for research_symbol in symbol_universe:
            snapshot = await repository.snapshot()
            run_id = uuid4().hex
            try:
                frame = await asyncio.to_thread(
                    download_bars, research_symbol, lookback, interval, feed=feed, config=config
                )
                manifest = DatasetManifest.from_frame(
                    frame,
                    symbol=research_symbol,
                    timeframe=interval,
                    feed="yfinance" if feed == "yfinance" else f"alpaca:{config.market_data.alpaca_feed}",
                    adjustment="raw",
                    universe_version=ETF_RESEARCH_UNIVERSE.version_id
                    if universe == "etf32"
                    else "explicit:" + ",".join(symbol_universe),
                )
                path = await asyncio.to_thread(save_dataset, frame, artifact_directory(), manifest.content_hash)
                miner = AlphaMiner(seed=seed)
                await repository.reserve_run(
                    run_id,
                    symbol=research_symbol,
                    timeframe=interval,
                    trials=iterations + len(miner.catalog.list_alphas()),
                )
                candidates = await asyncio.to_thread(
                    miner.mine,
                    frame,
                    iterations=iterations,
                    timeframe=interval,
                    symbol=research_symbol,
                    min_sharpe=min_sharpe,
                    min_dsr=min_dsr,
                    method=method,
                    max_seconds=max_seconds,
                )
                await repository.record_run(
                    run_id,
                    {**miner.last_run, "environment": await asyncio.to_thread(research_environment)},
                    {
                        **manifest.to_dict(),
                        "artifact": str(path),
                        "holdout_start": str(frame.index[miner.last_run["holdout_start"]]),
                        "incumbents": [d.to_dict() for d in snapshot.active],
                    },
                )
                for trial in miner.last_run["trials"]:
                    await repository.register(AlphaDefinition.from_dict(trial["definition"]), actor="research_worker")
                click.echo(
                    f"{research_symbol}: run {run_id}; {miner.last_run['trial_count']} trials, {len(candidates)} discovery finalists; holdout untouched"
                )
                for candidate in candidates[:10]:
                    click.echo(
                        f"  {candidate.definition.version_id} {candidate.definition.expression} | validation SR {candidate.metrics.sharpe_oos:.3f} DSR {candidate.metrics.dsr:.4f}"
                    )
                click.echo("Discovery only; promotion requires frozen holdout qualification and shadow evidence.")
            except (ValueError, ArithmeticError, OSError) as exc:
                failures += 1
                await repository.record_failure(
                    run_id,
                    symbol=research_symbol,
                    timeframe=interval,
                    error=f"{type(exc).__name__}: data_or_discovery_failed",
                )
                click.echo(f"{research_symbol}: failed ({type(exc).__name__}); rejection persisted", err=True)
    if failures:
        raise click.ClickException(f"{failures} symbol runs failed; completed symbol checkpoints retained")


@alpha_group.command("qualify")
@click.argument("run_id")
@click.argument("version_id")
@coro
async def alpha_qualify_cmd(run_id, version_id):
    """Consume a frozen finalist's holdout once and persist pass/fail reasons."""
    async with alpha_repository() as repository:
        run = await repository.get(f"run/{run_id}")
        if not run:
            raise click.ClickException("Unknown run")
        bars = await asyncio.to_thread(load_dataset, Path(run["manifest"]["artifact"]))
        decision = await AlphaPromotionService(repository).qualify(run_id, version_id, bars)
        click.echo(json.dumps(decision, indent=2))


@alpha_group.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@coro
async def alpha_import_cmd(path):
    """Import historical YAML as unqualified shadow versions; never activate orders."""
    definitions = await asyncio.to_thread(read_alpha_definitions, path)
    async with alpha_repository() as repository:
        for definition in definitions:
            await repository.register(definition, actor="yaml_import")
            snapshot = await repository.snapshot()
            if definition in snapshot.active or definition in snapshot.shadow:
                continue
            await repository.set_shadow(
                definition.version_id, actor="yaml_import", expected_generation=snapshot.generation
            )
        click.echo(f"Imported {len(definitions)} unqualified shadow versions. Existing positions unchanged.")


@alpha_group.command("export")
@coro
async def alpha_export_cmd():
    async with alpha_repository() as repository:
        click.echo(json.dumps(asdict(await repository.snapshot()), indent=2, default=str))


def lifecycle_command(name):
    @alpha_group.command(name)
    @click.argument("version_id")
    @click.option(
        "--generation", type=int, required=True, help="Observed registry generation; stale changes are rejected"
    )
    @coro
    async def command(version_id, generation):
        async with alpha_repository() as repository:
            operation = {"promote": repository.promote, "shadow": repository.set_shadow, "demote": repository.demote}[
                name
            ]
            updated = await operation(version_id, actor="cli_operator", expected_generation=generation)
            click.echo(
                f"{name}: {version_id}; registry generation {updated}. Effective next scan; existing positions retain protection."
            )

    return command


for _name in ("promote", "shadow", "demote"):
    lifecycle_command(_name)


@alpha_group.command("inspect")
@click.argument("identity")
@coro
async def alpha_inspect_cmd(identity):
    """Inspect exact persisted evidence, or a catalog hypothesis without recomputing it."""
    async with alpha_repository() as repository:
        row = await repository.get(f"version/{identity}")
        if row:
            click.echo(
                json.dumps({**row, "qualification": await repository.get(f"qualification/{identity}")}, indent=2)
            )
            return
    definition = AlphaCatalog().get(identity)
    if not definition:
        raise click.ClickException("Unknown version or catalog ID")
    click.echo(json.dumps(definition.to_dict(), indent=2))


@alpha_group.command("test")
@click.argument("expression")
@click.option("--symbol", default="SPY")
@click.option("--lookback", default="5y")
@click.option("--interval", type=click.Choice(["1d", "4h", "1h", "15m"]), default="1d")
@coro
async def alpha_test_cmd(expression, symbol, lookback, interval):
    """Diagnostic expression test; cannot qualify or promote a version."""
    definition = AlphaDefinition("alpha_diagnostic", "Diagnostic", expression, timeframe=interval)
    frame = await asyncio.to_thread(download_bars, symbol, lookback, interval)
    async with alpha_repository() as repository:
        await repository.exclude_observed_interval(
            symbol=symbol,
            start=frame.index[0].isoformat(),
            end=frame.index[-1].isoformat(),
            trials=1,
            reason=f"CLI diagnostic {definition.version_id} / {DatasetManifest.from_frame(frame, symbol=symbol, timeframe=interval, feed='yfinance', adjustment='raw', universe_version='diagnostic').content_hash}",
            actor="cli_diagnostic",
        )
    result = await asyncio.to_thread(AlphaMiner().evaluate_alpha, definition, frame)
    click.echo(json.dumps(result.to_dict() if result else {"reason": "insufficient_data"}, indent=2))


@alpha_group.command("benchmark")
@click.argument("run_id")
@click.option("--method", type=click.Choice(["ridge", "boosted"]), required=True)
@click.option("--budget", type=click.IntRange(1, 100), default=5)
@click.option("--seed", type=int, default=20260916)
@coro
async def alpha_benchmark_cmd(run_id, method, budget, seed):
    """Compare ML baselines against a saved dataset without reading its holdout."""

    async with alpha_repository() as repository:
        saved = await repository.get(f"run/{run_id}")
        if not saved:
            raise click.ClickException("Unknown source run")
        bars = await asyncio.to_thread(load_dataset, Path(saved["manifest"]["artifact"]))
        identifier = uuid4().hex
        await repository.reserve_run(
            identifier, symbol=saved["manifest"]["symbol"], timeframe=saved["manifest"]["timeframe"], trials=budget
        )
        result = await asyncio.to_thread(
            benchmark_models, bars, timeframe=saved["manifest"]["timeframe"], method=method, budget=budget, seed=seed
        )
        await repository.record_run(
            identifier, {**result, "environment": await asyncio.to_thread(research_environment)}, saved["manifest"]
        )
        click.echo(json.dumps({"run_id": identifier, **result}, indent=2))


@alpha_group.command("portfolio")
@click.argument("snapshot_path", type=click.Path(exists=True, path_type=Path))
@coro
async def alpha_portfolio_cmd(snapshot_path):
    """Validate/solve a shadow portfolio from an explicit JSON evidence snapshot.

    Input contains snapshot, forecasts, returns (pandas split orient), and optional
    policy. It must cover all broker holdings and pending reservations. No orders
    are available through this command.
    """
    payload = await asyncio.to_thread(lambda: json.loads(snapshot_path.read_text()))
    snapshot_data = payload["snapshot"]
    snapshot_data["as_of"] = datetime.fromisoformat(snapshot_data["as_of"])
    for key in ("locked_symbols", "shortable", "tradable"):
        snapshot_data[key] = frozenset(snapshot_data[key])
    snapshot = PortfolioSnapshot(**snapshot_data)
    forecasts = tuple(
        CombinedForecast(
            **{**f, "observed_at": datetime.fromisoformat(f["observed_at"]), "contributors": tuple(f["contributors"])}
        )
        for f in payload["forecasts"]
    )
    returns = pd.DataFrame(**payload["returns"])
    returns.index = pd.to_datetime(returns.index, utc=True)
    result = await asyncio.to_thread(
        build_shadow_portfolio,
        forecasts,
        returns,
        snapshot,
        now=datetime.now(UTC),
        policy=PortfolioPolicy(**payload.get("policy", {})),
    )
    click.echo(json.dumps(result, indent=2))


@alpha_group.command("status")
@coro
async def alpha_status_cmd():
    """Show installed registry acknowledgment and latest research observation."""
    async with alpha_repository() as repository:
        click.echo(json.dumps(await repository.status(), indent=2))


@alpha_group.command("exclude-period")
@click.option("--symbol", required=True)
@click.option("--start", required=True, help="Timezone-aware timestamp")
@click.option("--end", required=True, help="Timezone-aware timestamp")
@click.option("--trials", type=click.IntRange(min=0), required=True)
@click.option("--reason", required=True)
@coro
async def alpha_exclude_period_cmd(symbol, start, end, trials, reason):
    """Record external diagnostic trials and prevent reusing their observed period."""
    async with alpha_repository() as repository:
        await repository.exclude_observed_interval(
            symbol=symbol, start=start, end=end, trials=trials, reason=reason, actor="cli_operator"
        )
        click.echo("Observed interval excluded from future holdout qualification; trials retained.")
