from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

import click
import httpx
from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentic_trader.cli.utils import artifact_directory, coro, get_copilot_and_config, session_source
from agentic_trader.config import AppConfig, ScanBudget, load_config
from agentic_trader.constants import MAX_DAILY_COMPARISONS, AuditEventType
from agentic_trader.diagnostics.doctor import format_doctor_cli_output, run_diagnostics
from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.diagnostics.probe import probe_readiness
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.market.bars import ObservationStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.research.alpha.daily_observations import DailyPanelService
from agentic_trader.research.alpha.daily_plan import DailyComparisonPlan, DailyPanelPlan
from agentic_trader.research.alpha.decisions import SessionDecisionService
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.research.alpha.observation import SessionObservationService
from agentic_trader.research.setups.card_stats_worker import CardStatsUnavailable, CardStatsWorker
from agentic_trader.runtime import runtime_identity
from agentic_trader.screeners.earnings_drift import PEAD_DECISION_TIME_ET
from agentic_trader.storage.alpha_daily import DailyCampaignRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.maintenance import RetentionService
from agentic_trader.storage.operations import OperationsStore
from agentic_trader.telemetry.event_loop import monitor_event_loop


logger = logging.getLogger("copilot")
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent.parent


@click.command("eval", help="Run promptfoo evaluations on risk evaluator")
@coro
async def eval_command() -> None:
    """Run promptfoo evaluations on risk evaluator."""
    _copilot, config = get_copilot_and_config()
    logger.info("Running Promptfoo evaluation benchmark on risk prompts...")
    config_file = WORKSPACE_ROOT / "evals" / "promptfooconfig.yaml"
    provider = config.llm_model.replace("/", ":") if "/" in config.llm_model else f"openai:{config.llm_model}"
    logger.info("Evaluating production model: %s (Promptfoo provider: %s)", config.llm_model, provider)
    proc = await asyncio.create_subprocess_exec(
        "npx",
        "-y",
        "promptfoo",
        "eval",
        "--providers",
        provider,
        "-c",
        str(config_file),
        "--no-cache",
        env=os.environ.copy(),
    )
    rc = await proc.wait()
    sys.exit(rc)


@click.command("listen", help="Listen for interactive Telegram bot commands")
@coro
async def listen() -> None:
    """Listen for interactive Telegram bot commands."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    if not copilot.notifier.is_configured():
        logger.error("Telegram is not configured in .envrc")
        return
    logger.info("Starting Telegram Bot listener... (press Ctrl+C to stop)")
    await copilot.notifier.start_polling()
    workflow_task = asyncio.create_task(copilot.workflow_worker())
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Stopping listener...")
        copilot._shutdown_event.set()
        workflow_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await workflow_task
        await copilot.notifier.stop_polling()


MAX_DAILY_PROTOCOL_BYTES = 1_000_000
DAILY_TERMINAL_LABELS = {
    **{
        f"decision:{status}": {"kind": "decision", "status": status}
        for status in (DecisionStatus.SCORED, DecisionStatus.UNAVAILABLE, DecisionStatus.INTERRUPTED)
    },
    **{
        f"outcome:{status}": {"kind": "outcome", "status": status}
        for status in (ObservationStatus.COMPLETE, ObservationStatus.UNAVAILABLE)
    },
}


def _load_daily_protocol_document(path: Path | None):
    """Bound protocol reads before parsing or constructing any provider clients."""
    if path is None:
        raise ValueError("Explicit daily-panel protocol path required")
    with path.expanduser().open("rb") as source:
        payload = source.read(MAX_DAILY_PROTOCOL_BYTES + 1)
    if len(payload) > MAX_DAILY_PROTOCOL_BYTES:
        raise ValueError("Daily-panel protocol exceeds the bounded document size")
    return json.loads(payload)


def load_daily_panel_plan(path: Path | None) -> DailyPanelPlan:
    """Read exactly one explicit parent protocol; never infer it from trading configuration."""
    return DailyPanelPlan.from_document(_load_daily_protocol_document(path))


def load_daily_comparison_plans(paths: tuple[Path, ...], parent: DailyPanelPlan) -> tuple[DailyComparisonPlan, ...]:
    if len(paths) > MAX_DAILY_COMPARISONS:
        raise ValueError("Daily comparison enrollment exceeds its bounded protocol count")
    comparisons = tuple(DailyComparisonPlan.from_document(_load_daily_protocol_document(path)) for path in paths)
    if len({comparison.comparison_id for comparison in comparisons}) != len(comparisons):
        raise ValueError("Daily comparison identities must be unique")
    for comparison in comparisons:
        comparison.validate_parent(parent)
    return comparisons


def _record_research_results(results, metrics, component):
    if component == HealthComponent.ALPHA_DAILY_PANEL:
        if results["status"] not in ("idle", "enrolled", "recorded", "unavailable"):
            raise ValueError("Unknown daily-panel worker status")
        terminal_counts = results["terminal_counts"]
        comparison_counts = results["comparison_counts"]
        for counts in (terminal_counts, comparison_counts):
            if not isinstance(counts, dict) or any(
                key not in DAILY_TERMINAL_LABELS or type(count) is not int or count < 0 for key, count in counts.items()
            ):
                raise ValueError("Invalid daily-panel terminal counts")
        metrics.inc_counter("alpha_daily_panel_polls_total", labels={"status": results["status"]})
        for key in ("decisions", "outcomes"):
            metrics.inc_counter(f"alpha_daily_panel_{key}_total", value=results[key])
        for key, count in terminal_counts.items():
            metrics.inc_counter("alpha_daily_panel_terminals_total", value=count, labels=DAILY_TERMINAL_LABELS[key])
        for key, count in comparison_counts.items():
            metrics.inc_counter("alpha_daily_panel_comparisons_total", value=count, labels=DAILY_TERMINAL_LABELS[key])
        if results["status"] != "idle":
            logger.info(
                "Daily panel progress: %s %s",
                results["campaign_id"],
                results["status"],
                extra={
                    "event": "alpha_daily_panel",
                    "campaign_id": results["campaign_id"],
                    "status": results["status"],
                    "decisions": results["decisions"],
                    "outcomes": results["outcomes"],
                    "terminal_counts": terminal_counts,
                    "comparison_counts": comparison_counts,
                },
            )
        return
    for result in results:
        labels = {"symbol": result["symbol"], "timeframe": result["timeframe"], "feed": result["feed"]}
        if component == HealthComponent.ALPHA_DECISIONS:
            metrics.inc_counter("alpha_session_decisions_total", labels={**labels, "status": result["status"]})
            logger.info(
                "Session decision retained: %s %s %s",
                result["symbol"],
                result["closed_at"],
                result["status"],
                extra={
                    "event": "alpha_session_decision",
                    "decision_id": result["decision_id"],
                    "status": result["status"],
                    "artifact_hash": result.get("artifact_hash"),
                },
            )
            continue
        complete = result["status"] == ObservationStatus.COMPLETE
        metrics.set_gauge("alpha_observation_complete", float(complete), labels=labels)
        metrics.set_gauge("alpha_observation_timestamp_seconds", datetime.now(UTC).timestamp(), labels=labels)
        if complete:
            metrics.set_gauge(
                "alpha_observation_availability_upper_bound_seconds",
                result["availability_upper_bound_seconds"],
                labels=labels,
            )
        logger.info(
            "Session observation retained: %s %s %s",
            result["symbol"],
            result["closed_at"],
            result["status"],
            extra={
                "event": "alpha_session_observation",
                "observation_id": result["observation_id"],
                "status": result["status"],
                "artifact_hash": result["artifact_hash"],
            },
        )


async def _poll_research_worker(worker, readiness, metrics, shutdown, *, component, poll_seconds, offset=0):
    """Shared wall-clock polling; shutdown drains current work before the caller closes readers."""
    while not shutdown.is_set():
        try:
            results = await worker.run_once()
            _record_research_results(results, metrics, component)
            # Durable worker progress is separate from usable data or forecast outcomes.
            await readiness.observe(component, True)
        except Exception as exc:
            logger.exception("Research diagnostic worker failed: %s", component)
            await readiness.observe(component, False, type(exc).__name__)
        delay = poll_seconds - ((datetime.now(UTC).timestamp() - offset) % poll_seconds)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=delay)


async def run_session_worker(config, repository, readiness, metrics, shutdown, *, component):
    """Compose one session observer using its own source policy and owned readers."""
    factory, policy, folder = {
        HealthComponent.ALPHA_OBSERVER: (
            SessionObservationService,
            config.alpha_pipeline.observations,
            "forward-observations",
        ),
        HealthComponent.ALPHA_DECISIONS: (SessionDecisionService, config.alpha_pipeline.decisions, "forward-decisions"),
    }[component]
    with session_source(config, policy.feed.removeprefix("alpaca:")) as source:
        worker = factory(
            repository,
            source,
            policy,
            directory=artifact_directory() / folder,
            runtime=await asyncio.to_thread(runtime_identity),
        )
        await _poll_research_worker(
            worker,
            readiness,
            metrics,
            shutdown,
            component=component,
            poll_seconds=policy.poll_seconds,
            offset=policy.poll_offset_seconds,
        )


async def run_daily_panel_worker(config, repository, readiness, metrics, shutdown):
    """Load one frozen daily protocol; no hot reload, notifier, broker writes or second daemon."""
    policy = config.alpha_pipeline.daily_panel
    if not policy.enabled:
        return
    component = HealthComponent.ALPHA_DAILY_PANEL
    try:
        plan = await asyncio.to_thread(load_daily_panel_plan, policy.protocol_path)
        comparisons = await asyncio.to_thread(load_daily_comparison_plans, policy.comparison_protocol_paths, plan)
        daily_repository = DailyCampaignRepository(repository.store, policy=config.alpha_pipeline)
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            worker = DailyPanelService(
                daily_repository,
                source,
                plan,
                policy,
                acquisition=config.alpha_pipeline.daily_research,
                directory=artifact_directory() / "daily-panel",
                runtime=await asyncio.to_thread(runtime_identity),
                on_progress=lambda: readiness.observe(component, True),
                comparisons=comparisons,
            )
            await _poll_research_worker(
                worker, readiness, metrics, shutdown, component=component, poll_seconds=policy.poll_seconds
            )
    except Exception as exc:
        logger.exception("Daily-panel worker initialization failed")
        await readiness.observe(component, False, type(exc).__name__)
        # Invalid configuration stays visibly failed until a controlled restart.
        await shutdown.wait()


def _et_today() -> date:
    """Today's New York date (the card-statistics worker's failure memory is per date)."""
    return datetime.now(ET_TZ).date()


async def run_card_stats_worker(copilot, config, readiness) -> None:
    """Persist one ``card_stats`` snapshot per New York date; readiness is worker progress.

    Polls every ``card_policy.stats_poll_seconds``. An idle poll (not yet due, inside a
    suggestion-scan window, today's snapshot present, or a scan holding the scan lock), the
    start of a labelling run and a recorded snapshot observe ``card_stats`` ready; a failure (for
    example every bar fetch failing) writes nothing and observes it failed (``CardStatsUnavailable``
    with its message, any other error by type only), so the previous snapshot stays authoritative
    until it ages out. A failure is remembered for the rest of its New York date: until a snapshot
    is ``present`` or ``recorded``, later idle polls and a retry's start re-observe it failed with
    the same detail, so readiness does not flap around the scan slots. Only shutdown or
    cancellation ends the loop: a failed readiness write (the database is down) is logged and the
    next poll runs. Labelling runs in a worker thread. The daemon's shutdown cancels this
    coroutine, so nothing is recorded, but it cannot stop that thread: process exit waits for
    its provider reads.
    """
    component = HealthComponent.CARD_STATS
    shutdown = copilot._shutdown_event
    revision = str((await asyncio.to_thread(runtime_identity))["revision"])
    # (New York date, detail) of the last failed run, until a snapshot is present or recorded.
    failure: tuple[date, str] | None = None

    async def observe_progress(detail: str) -> None:
        """Ready with ``detail``, unless today's run already failed and no snapshot exists since."""
        held = failure[1] if failure is not None and failure[0] == _et_today() else None
        await readiness.observe(component, held is None, held or detail)

    worker = CardStatsWorker(
        copilot.db,
        copilot.card_stats,
        config,
        revision=revision,
        on_progress=observe_progress,
        scan_busy=lambda: copilot.scan_running,
    )
    while not shutdown.is_set():
        try:
            status = await worker.run_once()
            if status in ("present", "recorded"):
                failure = None
            await observe_progress(status)
        except Exception as exc:
            logger.exception("Card statistics worker failed", extra={"event": "card_stats_failed"})
            # The detail reaches /readyz and Telegram incident notices: only our own outage text
            # carries a message (exception type names only); any other error's (a host, a DSN)
            # stays in the log above.
            detail = (
                f"{type(exc).__name__}: {exc}"[:200] if isinstance(exc, CardStatsUnavailable) else type(exc).__name__
            )
            failure = (_et_today(), detail)
            try:
                await readiness.observe(component, False, detail)
            except Exception:
                logger.exception(
                    "Card statistics readiness could not be recorded", extra={"event": "card_stats_readiness_failed"}
                )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=config.card_policy.stats_poll_seconds)


def _slot_due_at(slot_et: str) -> datetime:
    """The instant a New York ``HH:MM`` suggestion-scan slot was due today."""
    return datetime.combine(datetime.now(ET_TZ).date(), dt_time.fromisoformat(slot_et), tzinfo=ET_TZ)


def make_suggestion_scan(copilot: Any, *, use_llm: bool):
    """The session-aligned suggestion scan: a full-budget equity scan, then an optional digest."""

    async def run_suggestion_scan(digest: bool = False, scheduled_time_et: str | None = None) -> None:
        active, reason = await copilot.session_provider.is_session_active(instrument_type="equity")
        if active:
            await copilot.run_scan(
                use_llm=use_llm,
                dry_run=False,
                asset_class="equity",
                budget=ScanBudget.FULL,
                shadow_evidence=True,
                # The configured New York time of this job: only the PEAD decision time
                # (10:35) may produce a drift card.
                scheduled_time_et=scheduled_time_et,
                # When that slot was due: a drift decision reached too late is skipped.
                scheduled_at=_slot_due_at(scheduled_time_et) if scheduled_time_et else None,
            )
        else:
            logger.info(
                "Suggestion scan skipped: %s", reason, extra={"event": "suggestion_scan_skipped", "reason": reason}
            )
        # A closed session is still a reportable session: the digest always runs.
        if digest:
            await copilot.publish_scan_digest()

    return run_suggestion_scan


def register_suggestion_scans(scheduler: Any, copilot: Any, config: AppConfig, *, use_llm: bool) -> None:
    """Register one cron job per configured New York suggestion-scan time (weekdays only).

    An empty ``suggestion_scan_times_et`` is the operator off switch: no cron job, and
    therefore no automatic suggestion scan and no end-of-session digest.
    """
    times = config.scheduler.suggestion_scan_times_et
    if not times:
        logger.info("Suggestion scans disabled (no times configured).")
        return
    job = make_suggestion_scan(copilot, use_llm=use_llm)
    slots: dict[str, tuple[str, bool]] = {}
    for index, item in enumerate(times):
        hour, minute = (int(part) for part in item.split(":"))
        job_id, digest = f"suggestion_scan_{index}", index == len(times) - 1
        scheduler.add_job(
            job,
            "cron",
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            timezone=ET_TZ,
            id=job_id,
            kwargs={"digest": digest, "scheduled_time_et": item},
            # A scan still running at the next trigger is skipped and logged rather than
            # overlapped. A late start within the (wide) grace still runs exactly once: a
            # sleeping host's maintenance wake can be ~15 minutes late (September 28). The
            # session check, card freshness/expiry and the PEAD lateness cap bound it.
            coalesce=True,
            max_instances=1,
            misfire_grace_time=config.scheduler.suggestion_scan_misfire_grace_seconds,
        )
        slots[job_id] = (item, digest)
    # A run dropped past its grace is otherwise only an APScheduler WARNING line.
    scheduler.add_listener(MissedSuggestionScanNotice(copilot.outbox, slots), EVENT_JOB_MISSED)
    logger.info("Scheduled suggestion scans at %s New York on weekdays.", ", ".join(times))


def missed_scan_text(slot_et: str, late: timedelta, *, digest: bool) -> str:
    """The operator notice for a suggestion-scan slot APScheduler dropped past its grace.

    Conditional on the session: the listener never reads the market calendar, and on an
    exchange holiday the job would have skipped its scan anyway.
    """
    lost = "If the market was open, this slot produced no cards" + (
        " (and no PEAD decision)." if slot_et == PEAD_DECISION_TIME_ET else "."
    )
    if digest:
        lost += " The end-of-session digest was not published."
    minutes = max(0, int(late.total_seconds() // 60))
    return (
        f"⚠️ {slot_et} NY suggestion scan was missed (the scheduler woke {minutes} min late — "
        f"host asleep or event loop blocked). {lost}"
    )


class MissedSuggestionScanNotice:
    """``EVENT_JOB_MISSED`` listener: one durable notice per dropped suggestion-scan slot.

    APScheduler calls listeners synchronously, here from the asyncio executor's done
    callback on the event loop. The listener only logs ``suggestion_scan_missed`` and
    schedules the outbox write as a task, referenced in ``pending`` until it finishes; it
    never blocks and never raises. The outbox dedup key
    ``suggestion-scan-missed/<ET date>/<HH:MM>`` keeps it to one notice per slot and date.
    """

    def __init__(self, outbox: NotificationDispatcher, slots: Mapping[str, tuple[str, bool]]):
        self.outbox = outbox
        # Job id -> (configured New York slot, whether that slot publishes the digest).
        self.slots = dict(slots)
        self.pending: set[asyncio.Task[None]] = set()

    def __call__(self, event: JobExecutionEvent) -> None:
        try:
            slot = self.slots.get(event.job_id)
            if slot is None:
                return
            slot_et, digest = slot
            late = datetime.now(UTC) - event.scheduled_run_time
            key = f"suggestion-scan-missed/{event.scheduled_run_time.astimezone(ET_TZ).date().isoformat()}/{slot_et}"
            logger.warning(
                "Suggestion scan %s NY was missed: the scheduler woke %s late",
                slot_et,
                late,
                extra={
                    "event": "suggestion_scan_missed",
                    "job_id": event.job_id,
                    "slot_et": slot_et,
                    "scheduled_run_time": event.scheduled_run_time.isoformat(),
                    "late_seconds": round(late.total_seconds(), 1),
                    "dedup_key": key,
                },
            )
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._publish(missed_scan_text(slot_et, late, digest=digest), key))
        except Exception:
            logger.exception(
                "Missed suggestion-scan notice could not be scheduled",
                extra={"event": "suggestion_scan_missed_notice_failed", "job_id": getattr(event, "job_id", None)},
            )
            return
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _publish(self, text: str, key: str) -> None:
        try:
            await self.outbox.publish_message(text, key=key)
        except Exception:
            logger.exception(
                "Missed suggestion-scan notice could not be queued",
                extra={"event": "suggestion_scan_missed_notice_failed", "dedup_key": key},
            )


def register_intraday_scan(scheduler: Any, copilot: Any, config: AppConfig, *, use_llm: bool) -> None:
    """Register the 15-minute intraday scan, scoped to the explicitly configured contracts."""

    async def run_intraday_scan(*, symbols: list[str]) -> None:
        session_active, reason = await copilot.session_provider.is_session_active(instrument_type="all")
        if session_active:
            await copilot.run_scan(
                use_llm=use_llm,
                dry_run=False,
                asset_class="all",
                timeframe="15m",
                symbols=symbols,
                budget=ScanBudget.FULL,
            )
        else:
            logger.debug("Intraday scan skipped outside market session: %s", reason)

    symbols = config.non_universe_contracts
    if not symbols:
        # run_scan(symbols=[]) selects the whole universe, so an empty scope must
        # register no job at all rather than a 15-minute universe scan.
        logger.info("Intraday scanner has no explicitly configured contracts; the universe is scanned on the cron.")
        return
    scheduler.add_job(
        functools.partial(run_intraday_scan, symbols=symbols),
        "interval",
        minutes=config.scheduler.intraday_interval_minutes,
        id="intraday_scan",
        next_run_time=datetime.now(UTC),
    )


def register_card_expiry_sweep(scheduler: Any, copilot: Any) -> None:
    """Register the untapped-card sweep: every 5 minutes, and once at startup.

    Expiring a card releases no risk and adds none, so this job runs regardless of halt
    state -- unlike the scan jobs above, it is never session- or halt-gated.
    """
    scheduler.add_job(
        copilot.expire_stale_cards,
        "interval",
        minutes=5,
        id="card_expiry_sweep",
        next_run_time=datetime.now(UTC),
    )


@click.command("daemon", help="Run continuous daemon scanner and trade manager")
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Bypass LLM evaluation in background scheduled scans",
)
@coro
async def daemon(no_llm: bool) -> None:
    """Run continuous daemon scanner and trade manager."""
    copilot, config = get_copilot_and_config()
    if not await copilot.broker.connect():
        raise click.ClickException("Broker connection failed; daemon startup aborted.")
    identity = {**runtime_identity(), "broker": type(copilot.broker).__name__, "alpaca_paper": config.alpaca_paper}
    await copilot.db.record_audit(AuditEventType.RUNTIME_STARTED, identity)
    logger.info("Runtime started: %s", identity)

    if copilot.notifier.is_configured():
        logger.info("Starting Telegram Bot listener for interactive callbacks...")
        await copilot.notifier.start_polling()

    stream_task: asyncio.Task[None] | None = None
    if copilot.broker.supports_trade_stream:
        stream_task = asyncio.create_task(copilot.start_trade_stream())

    if copilot.metrics_server:
        await copilot.metrics_server.start()

    copilot.readiness.started = True
    workflow_task = asyncio.create_task(copilot.workflow_worker())
    lag_task = asyncio.create_task(monitor_event_loop(config.telemetry, copilot.metrics, copilot.db.record_audit))

    session_tasks = [
        asyncio.create_task(
            run_session_worker(
                config,
                copilot.alpha_repository,
                copilot.readiness,
                copilot.metrics,
                copilot._shutdown_event,
                component=component,
            )
        )
        for component, policy in (
            (HealthComponent.ALPHA_OBSERVER, config.alpha_pipeline.observations),
            (HealthComponent.ALPHA_DECISIONS, config.alpha_pipeline.decisions),
        )
        if policy.enabled
    ]

    if config.alpha_pipeline.daily_panel.enabled:
        session_tasks.append(
            asyncio.create_task(
                run_daily_panel_worker(
                    config, copilot.alpha_repository, copilot.readiness, copilot.metrics, copilot._shutdown_event
                )
            )
        )

    # One card-statistics snapshot per New York date for the cards' evidence block and the card
    # policy (docs/card-evidence.md). Shutdown cancels it; an in-flight labelling thread finishes
    # on its own and records nothing.
    card_stats_task = asyncio.create_task(run_card_stats_worker(copilot, config, copilot.readiness))

    scheduler = AsyncIOScheduler(
        job_defaults={
            "misfire_grace_time": config.scheduler.misfire_grace_seconds,
            "coalesce": True,
            "max_instances": 1,
        }
    )
    interval = config.scheduler.cron_hour_interval
    # Schedule regular swing scans
    scheduler.add_job(
        copilot.run_scan,
        "interval",
        hours=interval,
        args=[not no_llm, False],
        kwargs={"budget": ScanBudget.FULL},
        id="swing_scan",
        next_run_time=datetime.now(UTC),
    )
    # Schedule intraday 15-minute scans during active market sessions
    if config.scheduler.intraday_scan_enabled:
        register_intraday_scan(scheduler, copilot, config, use_llm=not no_llm)
        logger.info(
            "Scheduled intraday scanner every %d minutes (session-gated).",
            config.scheduler.intraday_interval_minutes,
        )
    # Session-aligned suggestion scans on the New York clock; the last one publishes the digest.
    register_suggestion_scans(scheduler, copilot, config, use_llm=not no_llm)
    # Schedule automated position monitoring & broker reconciliation every 1 minute
    scheduler.add_job(
        copilot.monitor_positions,
        "interval",
        minutes=1,
        id="position_monitor",
        next_run_time=datetime.now(UTC),
    )
    # Strike an untapped card's buttons once its session ends; runs regardless of halt.
    register_card_expiry_sweep(scheduler, copilot)
    # Schedule daily morning macro briefing (Monday - Friday)
    if config.scheduler.macro_briefing_enabled:
        scheduler.add_job(
            copilot.broadcast_macro_briefing,
            "cron",
            day_of_week="mon-fri",
            hour=config.scheduler.macro_briefing_hour,
            minute=config.scheduler.macro_briefing_minute,
            id="macro_briefing",
        )
        logger.info(
            "Scheduled morning macro briefing for mon-fri at %02d:%02d UTC.",
            config.scheduler.macro_briefing_hour,
            config.scheduler.macro_briefing_minute,
        )
    scheduler.start()
    logger.info("Scheduler started: scanning every %dh, reconciling positions every 1m.", interval)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Shutting down daemon...")
        copilot._shutdown_event.set()
        # Background operator scans (re-evaluations, /scan SYMBOL) stop before the stream, Telegram and SDK clients close.
        await copilot.cancel_background_scans()
        copilot.readiness.started = False
        for task in session_tasks:
            await task
        card_stats_task.cancel()
        try:
            await card_stats_task
        except asyncio.CancelledError:
            pass
        except Exception:
            # A worker that died must not cut the rest of the shutdown short.
            logger.exception("Card statistics worker ended with an error", extra={"event": "card_stats_task_failed"})
        workflow_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await workflow_task
        lag_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lag_task
        if copilot.metrics_server:
            await copilot.metrics_server.stop()
        if stream_task and not stream_task.done():
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
        await copilot.broker.stop_trade_stream()
        scheduler.shutdown()

        await copilot.notifier.stop_polling()


@click.command("doctor", help="Run active diagnostics or inspect/supervise daemon readiness")
@click.option("--readiness", is_flag=True, help="Read daemon freshness without active probes or state changes")
@click.option(
    "--monitor", is_flag=True, help="Persist readiness incidents, enqueue alerts and compact old healthy observations"
)
@coro
async def doctor(readiness: bool, monitor: bool) -> None:
    if readiness and monitor:
        raise click.UsageError("Choose --readiness or --monitor")
    config = load_config()
    if readiness or monitor:
        async with httpx.AsyncClient() as client:
            report = await probe_readiness(config, client)
        if monitor:
            await monitor_once(config, report)
        click.echo(json.dumps(report, indent=2))
        if not report["ready"]:
            raise click.ClickException("Daemon is not ready; inspect component freshness above.")
        return
    diagnostic_report = await run_diagnostics(config)
    click.echo(format_doctor_cli_output(diagnostic_report))


async def monitor_once(config: AppConfig, report: dict[str, Any]) -> None:
    """Composition boundary for an external supervisor, never a second poller."""
    db = await asyncio.to_thread(SignalDatabase, config=config)
    try:
        monitor = OperationsMonitor(OperationsStore(db.workflows), config.operations)
        await monitor.observe(report)
        await RetentionService(db.workflows, config.operations).sweep(apply=True, only_if_due=True)
        # Let the normal consumer deliver. If its loop/process is unavailable,
        # this separate process may deliver one existing outbox item with the same
        # fenced claim/retry protocol. It has no broker or execution consumer.
        checks = report["checks"]
        if checks.get("endpoint", {}).get("ready") is False or checks.get("delivery", {}).get("ready") is False:
            notifier = TelegramNotifier(
                config.telegram_bot_token,
                config.telegram_chat_id,
                db=db,
                settings=config.telegram,
                environment=config.environment,
                execution_mode=config.execution_mode,
            )
            if notifier.app and notifier.is_configured():
                async with asyncio.timeout(config.execution.notification_delivery_timeout_seconds):
                    async with notifier.app.bot:
                        await NotificationDispatcher(db.workflows, notifier, config.execution).dispatch_one()
    finally:
        await db.engine.dispose()
