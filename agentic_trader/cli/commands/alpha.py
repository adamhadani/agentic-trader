"""Journal-backed alpha research, qualification and version lifecycle commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from uuid import uuid4

import click
import pandas as pd
import yfinance as yf

from agentic_trader.cli.utils import artifact_directory, coro, session_source
from agentic_trader.config import load_config
from agentic_trader.data.evidence import BarAcquisitionError, BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.lifetime_policy import MAX_TRADE_LIFETIME_SECONDS, TradeLifetimePolicy
from agentic_trader.market.bars import MAX_DECISION_SECONDS, SessionClockPolicy, completed_fixed_bars
from agentic_trader.research.alpha.baselines import (
    BENCHMARK_METHODS,
    ECONOMIC_FEATURES,
    MAX_BENCHMARK_TRIALS,
    ForecastBenchmarkPlan,
)
from agentic_trader.research.alpha.benchmark_workflow import AlphaBenchmarkService
from agentic_trader.research.alpha.calibration import CalibrationPlan, run_calibration
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.data import load_dataset, save_dataset
from agentic_trader.research.alpha.evidence import (
    DEFAULT_FORWARD_DAYS,
    DEFAULT_FORWARD_LIMIT,
    MAX_FORWARD_DAYS,
    MAX_FORWARD_LIMIT,
    load_daily_panel_evidence,
    load_forward_evidence,
)
from agentic_trader.research.alpha.forecast_policy import MAX_SIDE_COST_BPS, DailyLongFlatPolicy
from agentic_trader.research.alpha.forecasts import CombinedForecast, ForecastContract
from agentic_trader.research.alpha.lifetime_artifacts import execute_lifetime_study
from agentic_trader.research.alpha.lifetime_attribution import LifetimeAttributionProtocol
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.mining_universe import resolve_mining_universe
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.panel_study import (
    PanelStudyPlan,
    PanelStudyStatus,
    PanelTriagePolicy,
    assess_panel_hypothesis,
)
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.research.alpha.persistent_study import PersistentStudyPlan, compute_persistent_study
from agentic_trader.research.alpha.portfolio import PortfolioPolicy, PortfolioSnapshot, build_shadow_portfolio
from agentic_trader.research.alpha.power_artifacts import execute_power_study
from agentic_trader.research.alpha.power_study import FamilySnapshot, PowerProtocol
from agentic_trader.research.alpha.promotion import AlphaPromotionService, read_alpha_definitions
from agentic_trader.research.alpha.replay import (
    ReplayPlan,
    ReplayStatus,
)
from agentic_trader.research.alpha.replay_workflow import AlphaReplayService
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, TimedAlphaExecutionPolicy
from agentic_trader.research.alpha.study import StudyProtocol, StudyStatus
from agentic_trader.research.alpha.study_artifacts import execute_study
from agentic_trader.research.alpha.targets import MAX_FORECAST_HORIZON, ForecastLabel, ForecastTarget
from agentic_trader.research.alpha.validation import DatasetManifest
from agentic_trader.research.alpha.volume_study import VolumeStudyPlan, compute_volume_study
from agentic_trader.runtime import runtime_identity, state_directory
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.artifacts import save_json_report
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
            name: package_version(name)
            for name in ("numpy", "pandas", "scipy", "cvxpy", "scikit-learn", "alpaca-py", "arch", "statsmodels")
        },
    }


def download_bars(symbol, lookback, interval, *, feed="yfinance", config=None):
    requested = "1h" if interval == "4h" else interval
    if feed == "alpaca":
        if config is None:
            raise ValueError("Explicit Alpaca research configuration required")
        provider = AlpacaDataProvider(
            api_key=config.alpaca_api_key,
            api_secret=config.alpaca_api_secret,
            feed=config.market_data.alpaca_feed,
            request_timeout=config.market_data.timeout_seconds,
            evidence=BarEvidenceStore(state_directory() / "market-data", config.market_data.evidence),
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
    frame = completed_fixed_bars(frame, requested)
    if interval == "4h":
        frame = (
            frame.resample("4h")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna()
        )
        frame.attrs["timeframe"] = interval
    frame.attrs.update(
        feed="yfinance" if feed == "yfinance" else f"alpaca:{config.market_data.alpaca_feed}", adjustment="raw"
    )
    return completed_fixed_bars(frame, interval)


def _research_failure_code(exc: Exception) -> str:
    """Persist a useful, non-sensitive reason code in the research journal."""
    if isinstance(exc, BarAcquisitionError):
        return "bar_acquisition_failed"
    if isinstance(exc, ValueError) and str(exc) == "Insufficient data for predeclared purged validation folds":
        return "insufficient_validation_history"
    if isinstance(exc, ValueError):
        return "validation_or_discovery_failed"
    if isinstance(exc, OSError):
        return "research_io_failed"
    return "research_worker_failed"


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
@click.option("--universe", type=click.Choice(["explicit", "etf32", "snapshot", "screen"]), default="explicit")
@click.option(
    "--universe-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Immutable prospective snapshot or completed liquidity screen JSON (used with --universe snapshot/screen)",
)
@click.option(
    "--max-symbols",
    type=click.IntRange(min=1, max=500),
    help="Bound the selected cohort while retaining its deterministic order",
)
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
    symbol,
    symbols,
    lookback,
    interval,
    iterations,
    seed,
    min_sharpe,
    min_dsr,
    method,
    universe,
    universe_file,
    max_symbols,
    feed,
    max_seconds,
):
    """Persist every trial. Mining never consumes holdout or promotes an alpha."""
    config = load_config()
    try:
        mining_universe = await asyncio.to_thread(
            resolve_mining_universe,
            universe=universe,
            symbol=symbol,
            symbols=symbols,
            snapshot_path=universe_file,
            max_symbols=max_symbols,
            expected_feed=f"alpaca:{config.market_data.alpaca_feed}" if feed == "alpaca" else "yfinance",
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    symbol_universe = mining_universe.symbols
    failures = 0
    async with alpha_repository() as repository:
        for research_symbol in symbol_universe:
            snapshot = await repository.snapshot()
            run_id = uuid4().hex
            miner = AlphaMiner(seed=seed)
            try:
                # Charge the immutable attempt before provider I/O.  A timeout,
                # entitlement failure or process crash must remain visible in the
                # journal rather than becoming an uncharged missing experiment.
                await repository.reserve_run(
                    run_id,
                    symbol=research_symbol,
                    timeframe=interval,
                    trials=iterations + len(miner.catalog.list_alphas()),
                )
                frame = await asyncio.to_thread(
                    download_bars, research_symbol, lookback, interval, feed=feed, config=config
                )
                manifest = DatasetManifest.from_frame(
                    frame,
                    symbol=research_symbol,
                    timeframe=interval,
                    feed="yfinance" if feed == "yfinance" else f"alpaca:{config.market_data.alpaca_feed}",
                    adjustment="raw",
                    universe_version=mining_universe.version,
                )
                path = await asyncio.to_thread(save_dataset, frame, artifact_directory(), manifest.content_hash)
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
                    error=f"{type(exc).__name__}: {_research_failure_code(exc)}",
                    evidence=exc.evidence if isinstance(exc, BarAcquisitionError) else None,
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
@click.option("--method", type=click.Choice(BENCHMARK_METHODS), required=True)
@click.option("--budget", type=click.IntRange(1, MAX_BENCHMARK_TRIALS), default=5)
@click.option("--seed", type=click.IntRange(0, 2**32 - 1), default=20260917)
@click.option(
    "--horizon", type=click.IntRange(1, MAX_FORECAST_HORIZON), default=1, help="Forecast horizon in observed bars"
)
@click.option(
    "--label",
    type=click.Choice([label.value for label in ForecastLabel]),
    default=ForecastLabel.CLOSE_TO_CLOSE.value,
    help="Forecast return endpoints",
)
@click.option("--feature", multiple=True, help="Repeat for a frozen DSL feature set; defaults to the economic library")
@click.option(
    "--cost-bps",
    multiple=True,
    type=click.FloatRange(0, MAX_SIDE_COST_BPS),
    help="Per-side cost scenarios for a daily next-open/close payoff screen; repeat in increasing order",
)
@click.option("--output", type=click.Path(path_type=Path), help="New private artifact directory")
@coro
async def alpha_benchmark_cmd(run_id, method, budget, seed, horizon, label, feature, cost_bps, output):
    """Benchmark forecasts and optional cost scenarios; diagnostic evidence cannot promote."""
    async with alpha_repository() as repository:
        saved = await repository.get(f"run/{run_id}")
        if not saved:
            raise click.ClickException("Unknown source run")
        try:
            plan = ForecastBenchmarkPlan(
                ForecastTarget(saved["manifest"]["timeframe"], horizon, ForecastLabel(label)),
                method=method,
                budget=budget,
                seed=seed,
                features=feature or ECONOMIC_FEATURES,
                execution=DailyLongFlatPolicy(cost_bps) if cost_bps else None,
            )
            output = output or artifact_directory() / f"forecast-benchmark-{uuid4().hex}"
            result = await AlphaBenchmarkService(repository).run(
                run_id,
                plan,
                output,
                environment=await asyncio.to_thread(research_environment),
            )
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(json.dumps({**result, "artifact_directory": str(output)}, indent=2))
        if result["status"] != "completed":
            raise click.ClickException("Forecast benchmark failed; attempt and artifacts retained")


@alpha_group.command("portfolio")
@click.argument("snapshot_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output", required=True, type=click.Path(path_type=Path), help="Immutable private audit report destination."
)
@coro
async def alpha_portfolio_cmd(snapshot_path, output):
    """Validate/solve a shadow portfolio from an explicit JSON evidence snapshot.

    Input contains snapshot, forecasts, returns (pandas split orient), and optional
    policy. It must cover all broker holdings and pending reservations. No orders
    are available through this command.
    """
    payload = await asyncio.to_thread(lambda: json.loads(snapshot_path.read_text()))
    snapshot_data = dict(payload["snapshot"])
    snapshot_data["as_of"] = datetime.fromisoformat(snapshot_data["as_of"])
    for key in ("locked_symbols", "shortable", "tradable"):
        snapshot_data[key] = frozenset(snapshot_data[key])
    snapshot = PortfolioSnapshot(**snapshot_data)
    forecasts = tuple(
        CombinedForecast(
            **{
                **f,
                "observed_at": datetime.fromisoformat(f["observed_at"]),
                "contract": ForecastContract.from_document(f["contract"]),
                **{key: tuple(f[key]) for key in ("contributors", "families", "calibration_ids")},
            }
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
        risk_contract=ForecastContract.from_document(payload["risk_contract"]),
        policy=PortfolioPolicy(**payload.get("policy", {})),
    )
    await asyncio.to_thread(
        save_json_report,
        {
            "kind": "shadow_portfolio_v2",
            "input": payload,
            "output": result,
            "input_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        },
        output,
    )
    click.echo(json.dumps(result, indent=2))


@alpha_group.command("status")
@coro
async def alpha_status_cmd():
    """Show installed registry acknowledgment and latest research observation."""
    async with alpha_repository() as repository:
        report = await repository.status()
        report["daily_panel"] = await load_daily_panel_evidence(repository)
        click.echo(json.dumps(report, indent=2, allow_nan=False))


@alpha_group.command("forward")
@click.option("--days", default=DEFAULT_FORWARD_DAYS, type=click.IntRange(1, MAX_FORWARD_DAYS), show_default=True)
@click.option("--limit", default=DEFAULT_FORWARD_LIMIT, type=click.IntRange(1, MAX_FORWARD_LIMIT), show_default=True)
@coro
async def alpha_forward_cmd(days, limit):
    """Read recorded forward decisions, failures and measured receipt timing."""
    async with alpha_repository() as repository:
        _, report = await load_forward_evidence(repository, days=days, limit=limit)
        click.echo(json.dumps(report, indent=2, allow_nan=False))


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


@alpha_group.command("calibrate")
@click.option("--seeds", type=click.IntRange(1, 256), default=10)
@click.option("--observations", type=click.IntRange(600, 10000), default=2500)
@click.option("--seed", type=click.IntRange(0, 2**32 - 256), default=20260916)
@click.option("--family-trials", type=click.IntRange(1, 100_000_000), default=100)
@click.option(
    "--trial-variance", type=click.FloatRange(min=0), default=0.003, help="Per-observation trial Sharpe variance"
)
@click.option("--bootstrap-samples", type=click.IntRange(99, 4999), default=499)
@click.option("--block-length", type=click.IntRange(2, 40), default=10)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="New private JSON artifact; existing files are preserved",
)
@coro
async def alpha_calibrate_cmd(
    seeds, observations, seed, family_trials, trial_variance, bootstrap_samples, block_length, output
):
    """Synthetic calibration only; no runtime config, DB, market data or activation."""
    try:
        plan = CalibrationPlan(
            seeds=seeds,
            observations=observations,
            seed=seed,
            family_trials=family_trials,
            trial_variance=trial_variance,
            bootstrap_samples=bootstrap_samples,
            block_length=block_length,
        )
        path = output or artifact_directory() / f"calibration-{plan.identity[:16]}-{uuid4().hex}.json"
        if path.exists():
            raise click.ClickException("Output already exists; choose a new artifact path")
        click.echo(f"Starting synthetic protocol {plan.identity}: {json.dumps(plan.to_dict(), sort_keys=True)}")
        report = await asyncio.to_thread(run_calibration, plan)
        report["environment"] = await asyncio.to_thread(research_environment)
        await asyncio.to_thread(save_json_report, report, path)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Synthetic calibration {plan.identity}; {len(report['strategy_controls'])} strategy controls, {len(report['family_controls'])} fixed-panel controls"
    )
    click.echo(f"Report: {path}")
    click.echo("Diagnostic only; no promotion permission or runtime state changed.")


@alpha_group.command("study-plan")
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New frozen protocol JSON file")
@click.option("--seed", type=click.IntRange(0, 2**128 - 1), default=StudyProtocol.model_fields["seed"].default)
@coro
async def alpha_study_plan_cmd(output, seed):
    """Freeze a synthetic protocol under current contracts without evaluating observations."""
    protocol = StudyProtocol(seed=seed)
    try:
        await asyncio.to_thread(save_json_report, protocol.document(), output)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Protocol {protocol.identity}: {output}")


@alpha_group.command("study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="New private directory; existing runs are never overwritten",
)
@coro
async def alpha_study_cmd(protocol_path, output):
    """Run a frozen synthetic study, retaining every replicate and all failures."""
    try:
        document = json.loads(await asyncio.to_thread(protocol_path.read_text))
        protocol = StudyProtocol.from_document(document)
        environment = await asyncio.to_thread(research_environment)
        click.echo(f"Starting synthetic study {protocol.identity}; output: {output}")
        result = await asyncio.to_thread(execute_study, protocol, output, environment, progress=click.echo)
    except (TypeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Study {result['status']}; {result['recorded_jobs']}/{result['expected_jobs']} replicates retained.")
    click.echo("Diagnostic only; neither study completion nor passing criteria authorizes promotion.")
    if result["status"] == StudyStatus.INCOMPLETE:
        raise click.ClickException("Incomplete study; inspect retained failures. Validation may remain unexamined.")


@alpha_group.command("power-plan")
@click.option("--seed", type=click.IntRange(0, 2**128 - 1), required=True)
@click.option("--family-snapshot", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True)
@coro
async def alpha_power_plan_cmd(seed, family_snapshot, output):
    """Freeze paired control/winner diagnosis; no observations evaluated."""
    try:
        snapshot = (
            FamilySnapshot.model_validate_json(await asyncio.to_thread(family_snapshot.read_text))
            if family_snapshot
            else None
        )
        protocol = PowerProtocol(seed=seed, family_snapshot_hash=snapshot.identity if snapshot else None)
        await asyncio.to_thread(save_json_report, protocol.document(), output)
    except (ValueError, TypeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Power protocol {protocol.identity}: {output}")
    if snapshot is None:
        click.echo("Current-family comparisons will remain unavailable without a sourced snapshot.")


@alpha_group.command("power-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--family-snapshot", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True)
@coro
async def alpha_power_study_cmd(protocol_path, family_snapshot, output):
    """Diagnose search, execution and individual gates using synthetic paired controls."""
    try:
        protocol = PowerProtocol.from_document(json.loads(await asyncio.to_thread(protocol_path.read_text)))
        snapshot = (
            FamilySnapshot.model_validate_json(await asyncio.to_thread(family_snapshot.read_text))
            if family_snapshot
            else None
        )
        environment = await asyncio.to_thread(research_environment)
        result = await asyncio.to_thread(
            execute_power_study, protocol, output, environment, snapshot, progress=click.echo
        )
    except (TypeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Power study {result['status']}; {result['recorded_jobs']}/{result['expected_jobs']} searches retained."
    )
    click.echo("Synthetic diagnosis only; no promotion permission or runtime state changed.")
    if result["status"] == StudyStatus.INCOMPLETE:
        raise click.ClickException("Incomplete power diagnosis; inspect retained missing/failed comparisons.")


@alpha_group.command("lifetime-plan")
@click.option("--seed", type=click.IntRange(0, 2**128 - 1), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@coro
async def alpha_lifetime_plan_cmd(seed, output):
    """Freeze the paired P0/P1/P2 lifetime attribution protocol only."""
    try:
        protocol = LifetimeAttributionProtocol(seed=seed)
        await asyncio.to_thread(save_json_report, protocol.document(), output)
    except (TypeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Lifetime attribution protocol {protocol.identity}: {output}")
    click.echo("Synthetic diagnostic only; no promotion permission or runtime state changed.")


@alpha_group.command("lifetime-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True)
@coro
async def alpha_lifetime_study_cmd(protocol_path, output):
    """Run a frozen paired lifetime attribution protocol with durable artifacts."""
    try:
        protocol = LifetimeAttributionProtocol.from_document(
            json.loads(await asyncio.to_thread(protocol_path.read_text))
        )
        environment = await asyncio.to_thread(research_environment)
        click.echo(f"Starting lifetime attribution {protocol.identity}; output: {output}")
        result = await asyncio.to_thread(execute_lifetime_study, protocol, output, environment, progress=click.echo)
    except (TypeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Lifetime attribution {result['status']}; {result['recorded_jobs']}/{result['expected_jobs']} jobs retained."
    )
    click.echo("Synthetic diagnostic only; neither completion nor a favorable counterfactual authorizes promotion.")
    if result["status"] == StudyStatus.INCOMPLETE:
        raise click.ClickException("Incomplete lifetime attribution; inspect retained failures.")


@alpha_group.command("replay")
@click.argument("expression")
@click.option("--symbol", required=True)
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--interval", type=click.Choice(["15m", "1h", "4h", "1d"]), default="15m")
@click.option("--feed", type=click.Choice(["iex", "sip"]), help="Defaults to the configured Alpaca stock feed")
@click.option(
    "--decision-delay-seconds",
    type=click.IntRange(0, MAX_DECISION_SECONDS),
    default=SessionClockPolicy().decision_delay_seconds,
)
@click.option(
    "--max-lateness-seconds",
    type=click.IntRange(1, MAX_DECISION_SECONDS),
    default=SessionClockPolicy().max_lateness_seconds,
    help="Expire an unsubmitted decision; resting orders follow the separate entry lifetime, if declared",
)
@click.option("--entry-lifetime-seconds", type=click.IntRange(1, MAX_TRADE_LIFETIME_SECONDS))
@click.option("--holding-lifetime-seconds", type=click.IntRange(1, MAX_TRADE_LIFETIME_SECONDS))
@click.option("--output", type=click.Path(path_type=Path), help="New private directory; no overwrite")
@coro
async def alpha_replay_cmd(
    expression,
    symbol,
    start,
    end,
    interval,
    feed,
    decision_delay_seconds,
    max_lateness_seconds,
    entry_lifetime_seconds,
    holding_lifetime_seconds,
    output,
):
    """Diagnostic session/minute replay; charges one trial and consumes the inspected period."""
    config = load_config()
    feed = feed or config.market_data.alpaca_feed
    try:
        if (entry_lifetime_seconds is None) != (holding_lifetime_seconds is None):
            raise ValueError("Both entry and holding lifetimes must be declared together")
        execution = (
            TimedAlphaExecutionPolicy(lifetime=TradeLifetimePolicy(entry_lifetime_seconds, holding_lifetime_seconds))
            if entry_lifetime_seconds is not None
            else AlphaExecutionPolicy()
        )
        symbol = symbol.strip().upper()
        definition = AlphaDefinition(
            "session_replay",
            "Session replay",
            expression,
            execution=execution,
            semantics_version=3,
            clock=SessionClockPolicy(decision_delay_seconds, max_lateness_seconds),
            timeframe=interval,
            eligible_symbols=(symbol,),
            data_feed=f"alpaca:{feed}",
        )
        plan = ReplayPlan(symbol, start.date(), end.date(), definition)
        output = output or artifact_directory() / f"session-replay-{uuid4().hex}"
        environment = await asyncio.to_thread(research_environment)
        with session_source(config, feed) as source:
            async with alpha_repository() as repository:
                result = await AlphaReplayService(repository, source).run(plan, output, environment=environment)
    except (TypeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Session replay {result['run_id']}: {result['status']}; artifacts: {output}")
    click.echo(
        "One research attempt charged; inspected period excluded from fresh holdouts. No promotion or shadow credit."
    )
    if result["status"] != ReplayStatus.COMPLETED:
        raise click.ClickException(
            f"Replay unavailable ({result.get('error_type')}); inspect retained result/coverage."
        )
    click.echo(
        f"Observed minutes: {result['coverage']['observed_minutes']}; completed simulated trades: {result['total_trades']}"
    )


@alpha_group.command("volume-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@coro
async def alpha_volume_study_cmd(protocol_path, output):
    """Fit source-specific daily volume profiles and evaluate frozen forward intervals."""
    document = json.loads(await asyncio.to_thread(protocol_path.read_text))
    plan = VolumeStudyPlan.from_document(document)
    config = load_config()
    environment = await asyncio.to_thread(research_environment)
    async with alpha_repository() as repository:
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            result = await AlphaPanelService(
                repository, source, acquisition=config.alpha_pipeline.daily_research, compute=compute_volume_study
            ).run(plan, output, environment=environment)
    report = {
        k: result[k] for k in ("status", "plan_id", "charged_trials", "completed_comparisons", "authorizes_promotion")
    }
    report["result_hash"] = await asyncio.to_thread(
        lambda: hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    )
    await asyncio.to_thread(save_json_report, report, output / "screen.json")
    click.echo(json.dumps(report, indent=2))
    if result["status"] != PanelStudyStatus.COMPLETED:
        raise click.ClickException("Volume study failed; retained inputs/receipts explain unavailable comparisons")


@alpha_group.command("book-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@coro
async def alpha_book_study_cmd(protocol_path, output):
    """Run a frozen adjusted-price ETF book diagnostic; never submit portfolio orders."""
    document = json.loads(await asyncio.to_thread(protocol_path.read_text))
    plan = PersistentStudyPlan.from_document(document)
    config = load_config()
    environment = await asyncio.to_thread(research_environment)
    async with alpha_repository() as repository:
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            result = await AlphaPanelService(
                repository, source, acquisition=config.alpha_pipeline.daily_research, compute=compute_persistent_study
            ).run(plan, output, environment=environment)
    report = {
        k: result[k] for k in ("status", "plan_id", "charged_trials", "completed_comparisons", "authorizes_promotion")
    }
    report["decisions"] = result.get("decisions", [])
    report["result_hash"] = await asyncio.to_thread(
        lambda: hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    )
    await asyncio.to_thread(save_json_report, report, output / "screen.json")
    click.echo(json.dumps(report, indent=2))
    if result["status"] != PanelStudyStatus.COMPLETED:
        raise click.ClickException("Book study failed; retained inputs/receipts explain unavailable comparisons")


@alpha_group.command("panel-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@coro
async def alpha_panel_study_cmd(protocol_path, output):
    """Run a frozen native-daily panel diagnostic; charge trials and exclude inspected periods."""
    protocol = json.loads(await asyncio.to_thread(protocol_path.read_text))
    if set(protocol) not in (
        {"plan", "triage"},
        {"plan", "triage", "diagnostics"},
        {"plan", "triage", "diagnostics", "universe_source"},
        {"plan", "triage", "diagnostics", "universe_source", "study_scope"},
    ):
        raise click.ClickException("Explicit plan and triage protocol required")
    diagnostics = protocol.get("diagnostics")
    if diagnostics is not None and diagnostics != {
        "version": "matched_panel_diagnostics_v1",
        "market_exposure_window": 60,
        "turnover": "absolute_weight_change",
    }:
        raise click.ClickException("Matched-panel diagnostics must use the frozen causal exposure/turnover contract")
    plan = PanelStudyPlan.from_document(protocol["plan"])
    source = protocol.get("universe_source")
    if source is not None and (
        not isinstance(source, dict)
        or source.get("kind") != "equity_liquidity_screen_v1"
        or source.get("feed") != plan.feed
        or source.get("selected_count") != len(plan.symbols)
        or not isinstance(source.get("result_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", source["result_sha256"])
    ):
        raise click.ClickException("Matched panel universe source must identify the complete source-specific cohort")
    if "study_scope" in protocol and (
        not isinstance(protocol["study_scope"], str) or not protocol["study_scope"].strip()
    ):
        raise click.ClickException("Study scope must be a non-empty frozen statement")
    triage = PanelTriagePolicy(**protocol["triage"])
    if any(c not in plan.costs_bps for c in (triage.primary_cost_bps, triage.stress_cost_bps)):
        raise click.ClickException("Triage costs must appear in the frozen study")
    config = load_config()
    environment = await asyncio.to_thread(research_environment)
    environment["panel_protocol"] = protocol
    environment["panel_protocol_hash"] = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    async with alpha_repository() as repository:
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            result = await AlphaPanelService(repository, source, acquisition=config.alpha_pipeline.daily_research).run(
                plan, output, environment=environment
            )
    decisions = []
    if result["status"] == PanelStudyStatus.COMPLETED:
        for hypothesis in plan.hypotheses:
            trials = [t for t in result["trials"] if t["hypothesis"] == hypothesis.name]
            decision = assess_panel_hypothesis(trials, tuple(f.name for f in plan.folds), triage)
            decisions.append({"hypothesis": hypothesis.name, **decision})
    report = {
        "status": result["status"],
        "plan_id": plan.identity,
        "charged_trials": plan.trial_count,
        "completed_comparisons": result["completed_comparisons"],
        "decisions": decisions,
        "result_hash": hashlib.sha256((output / "result.json").read_bytes()).hexdigest(),
        "authorizes_promotion": False,
    }
    await asyncio.to_thread(save_json_report, report, output / "screen.json")
    click.echo(json.dumps(report, indent=2))
    if result["status"] != PanelStudyStatus.COMPLETED:
        raise click.ClickException("Panel study failed; retained inputs/receipts explain the unavailable comparisons")
