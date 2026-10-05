"""The budgeted pooled campaign, run once per protocol against the journal ledger.

**Gate.** It refuses to start unless checks A, B and C all passed for this cohort and
protocol, on one cube, at the clean code revision that is running.

**Sequence.**

1. Write ``protocol.json`` and ``manifest.json``, which records the journal written to.
2. Reserve the campaign in the ledger before reading anything. The reservation pins the
   journal scope: the confirmation window is single-use per scope.
3. Load the cached cube.
4. Preflight (label-blind, nothing charged): every family's seed panels and one wrapped
   expression per mutation operator must compute, so a systematic evaluation error fails
   the campaign before any budget is spent.
5. Run the per-family search, charging each formula before it is scored.
6. Run the pooled discovery finish and selection.
7. Write the frozen documents.
8. Consume the lane-wide confirmation interval, then read it.

**Overlap rule.** A finalist whose discovery picks overlap a literature entry's at Jaccard
>= ``dedupe_jaccard`` is ``already_tested_by`` that entry. Literature formulas are evaluated
under the campaign's own selection rule, as pick codes. It keeps its Holm slot and is
never probe-eligible: both literature entries failed.

**Every exit writes ``result.json``** with ``confirmation_consumed``, read back from the
ledger. A failure is ``status: failed``; charges remain.

**Cancellation** (Ctrl-C, or SIGTERM while the campaign runs). The search's worker thread
cannot be cancelled, so the journal ledger is aborted: no further journal write starts, and
the worker is waited for. Before consumption it stops at its next ledger call, nothing is
consumed and a rerun resumes. After consumption it only computes, so its outcome is kept in
``outcome.json``. The result is ``status: cancelled``, the ledger is advanced to ``failed``
and the cancellation is re-raised.

**Recovery.** A campaign that consumed its confirmation window but never completed (for
example, an error inside the confirmation computation) is recovered by
``execute_campaign_recovery``. It recomputes the confirmation for the journaled frozen
candidates, and charges and consumes nothing.

A confirmed, probe-eligible formula earns eligibility for a separately specified paper
probe, nothing more.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    LoadedProtocol,
    _confirmation,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, check_coverage
from agentic_trader.research.pooled.formula import Formula, allowed_mask, jaccard_codes
from agentic_trader.research.pooled.genetic import SearchOutcome, run_search_stages
from agentic_trader.research.pooled.ledger import CampaignAborted, JournalLedger
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook, formula_id
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.alpha import POOLED_CONFIRMATION_KEY
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "GATES",
    "LITERATURE_ENTRIES",
    "already_tested",
    "campaign_id_for",
    "check_gate",
    "execute_campaign",
    "execute_campaign_recovery",
    "frozen_document",
    "journal_identity",
    "literature_codes",
    "preflight",
    "require_clean_revision",
]

LITERATURE_ENTRIES = ("config/research/pooled/high52-v1.json", "config/research/pooled/reversal-lowmax-v1.json")
# (gate name, the ``check`` its manifest must record)
GATES = (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))
OUTCOME_KEYS = ("status", "carried", "selection", "frozen", "confirmation", "confirmed")


def campaign_id_for(loaded: LoadedProtocol) -> str:
    return f"pooled-campaign-v{loaded.protocol.version}-{loaded.sha256[:16]}"


def require_clean_revision(environment: Mapping) -> str:
    raw = environment.get("runtime", {}).get("revision")
    revision = str(raw) if raw else "unavailable"
    if revision == "unavailable" or revision.endswith("-dirty"):
        raise ValueError(f"refusing to run from a dirty or unknown code revision ({revision}); commit first")
    return revision


def journal_identity(repository) -> dict:
    """The journal a campaign writes to: its scope, dialect and database name (never credentials)."""
    engine = repository.store.db.engine
    return {"scope": repository.store.scope, "dialect": engine.dialect.name, "database": engine.url.database}


def _named(expression: str) -> str:
    try:
        return canonical_expression(expression)
    except Exception:  # an expression that does not compile is named as written
        return expression


def preflight(protocol: CampaignProtocol, book: ScoreBook, view: CubeView) -> int:
    """Compute what each family's search computes first; returns the number of expressions checked.

    For each family, every seed's panel on the discovery view (through ``ScoreBook.formula``,
    as the search scores it), and one wrapped expression per mutation operator,
    ``op(first seed, first constant window)``, through ``ScoreBook.panel``. Label-blind:
    nothing is selected, evaluated or charged. An exception, or a seed whose scores are
    entirely NaN over the discovery view, raises a ``ValueError`` naming the expression.
    """
    checked = 0
    for family in protocol.families:
        for seed in family.seeds:
            try:
                scores, _ = book.formula(seed).panel(view)
            except Exception as exc:
                raise ValueError(
                    f"preflight: family {family.id} seed {_named(seed)!r} failed: {type(exc).__name__}: {exc}"
                ) from exc
            if not np.isfinite(scores).any():
                raise ValueError(
                    f"preflight: family {family.id} seed {_named(seed)!r} scores are entirely NaN "
                    "over the discovery window"
                )
            checked += 1
        for operator in family.mutation_operators:
            expression = f"{operator}({family.seeds[0]}, {family.constant_windows[0]})"
            try:
                book.panel(canonical_expression(expression))
            except Exception as exc:
                raise ValueError(
                    f"preflight: family {family.id} mutation {_named(expression)!r} failed: {type(exc).__name__}: {exc}"
                ) from exc
            checked += 1
    return checked


def check_gate(directory: Path, *, check: str, cohort_sha256: str, protocol_sha256: str, revision: str) -> dict:
    """A gate's passed result, for this cohort and protocol, at this exact code revision."""
    manifest = json.loads((directory / "manifest.json").read_text())
    result = json.loads((directory / "result.json").read_text())
    if manifest.get("check") != check:
        raise ValueError(f"{directory} holds a {manifest.get('check')!r} result, not {check!r}")
    if result.get("status") != "passed":
        raise ValueError(f"{check} has not passed (status {result.get('status')!r})")
    if result.get("cohort_sha256") != cohort_sha256:
        raise ValueError(f"{check} was run on a different cohort")
    if result.get("campaign_protocol_sha256") != protocol_sha256:
        raise ValueError(f"{check} was run under a different campaign protocol")
    ran_at = manifest.get("environment", {}).get("runtime", {}).get("revision")
    if ran_at != revision:
        raise ValueError(f"{check} ran at code revision {ran_at!r}; this campaign runs at {revision!r}")
    if not result.get("cube_sha256"):
        raise ValueError(f"{check} records no cube")
    return result


def literature_codes(
    entries: Iterable, view: CubeView, book: ScoreBook, protocol: CampaignProtocol
) -> dict[str, np.ndarray]:
    """Each literature formula's discovery picks under the campaign's own selection rule, as pick codes."""
    stop = view.offset + len(view.sessions)
    codes: dict[str, np.ndarray] = {}
    for loaded in entries:
        formula: Formula = loaded.entry.formula
        scores = book.panel(formula.score)[view.offset : stop]
        filters = [book.panel(spec.expression)[view.offset : stop] for spec in formula.filters]
        allowed = allowed_mask(formula, filters, view.eligible)
        codes[loaded.entry.id] = protocol.select(scores, allowed, view).codes(len(view.symbols))
    return codes


def already_tested(
    carried: Sequence[str], codes: Mapping[str, np.ndarray], literature: Mapping[str, np.ndarray], threshold: float
) -> dict[str, str]:
    """Finalist id -> the literature entry whose discovery picks it overlaps at Jaccard >= threshold."""
    marked: dict[str, str] = {}
    for fid in carried:
        for entry_id, entry_codes in literature.items():
            if jaccard_codes(codes[fid], entry_codes) >= threshold:
                marked[fid] = entry_id
                break
    return marked


def frozen_document(expression: str, protocol: CampaignProtocol) -> dict:
    """A frozen candidate: its score and the selection rule it was tested under, with a stable identity."""
    body = {"score": expression, "filters": [], "selection": protocol.selection_rule.model_dump(mode="json")}
    identity = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"formula": body, "identity": identity}


def _write_frozen(
    directory: Path,
    frozen: Sequence[str],
    search: SearchOutcome,
    protocol: CampaignProtocol,
    marked: Mapping[str, str],
) -> list[dict]:
    documents = []
    for fid in frozen:
        frozen_formula = frozen_document(search.expressions[fid], protocol)
        save_json_report(
            {
                "formula_id": fid,
                "family": search.families[fid],
                "expression": search.expressions[fid],
                **frozen_formula,
                "already_tested_by": marked.get(fid),
                "authorizes_promotion": False,
            },
            directory / f"{fid}.json",
        )
        documents.append(
            {"formula_id": fid, "identity": frozen_formula["identity"], "already_tested_by": marked.get(fid)}
        )
    return documents


def _write_records(path: Path, records: Iterable[Mapping]) -> None:
    text = "".join(json.dumps(_finite_json(dict(record)), sort_keys=True) + "\n" for record in records)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(text)


def _say(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _save_outcome(directory: Path, outcome: Mapping) -> None:
    save_json_report(_finite_json({key: outcome[key] for key in OUTCOME_KEYS}), directory / "outcome.json")


async def _consumption(repository, campaign_id: str) -> bool | None:
    """Whether the ledger records this campaign's confirmation as consumed; None if it cannot be read."""
    try:
        record = await repository.get(f"pooled/campaign/{campaign_id}")
    except Exception:
        return None
    return record is not None and "confirmation_consumed" in record["stages"]


async def _best_effort(awaitable: Awaitable[Any]) -> tuple[Any, str | None]:
    """Clean-up after a cancellation: a failure or a further interrupt is reported, never raised."""
    try:
        return await awaitable, None
    except (Exception, asyncio.CancelledError, KeyboardInterrupt) as exc:
        return None, f"{type(exc).__name__}: {exc}"


async def _settle(worker: asyncio.Future) -> None:
    """Wait until the worker thread has stopped; a further interrupt does not cut the wait short.

    The aborted journal ledger makes this prompt: the worker stops at its next ledger call, or,
    once the confirmation was consumed, after it has only computed the confirmation.
    """
    while not worker.done():
        with suppress(asyncio.CancelledError, KeyboardInterrupt):
            await asyncio.wait({worker})


@contextmanager
def _sigterm_cancels(task: asyncio.Task | None) -> Iterator[None]:
    """While the campaign runs, SIGTERM cancels it as Ctrl-C does; the previous handler is restored."""
    loop = asyncio.get_running_loop()
    previous = signal.getsignal(signal.SIGTERM)
    installed = False
    if task is not None:
        # No signal support (not the main thread, or another platform): SIGTERM keeps its default.
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            installed = True
    try:
        yield
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)


@dataclass
class _RunState:
    """What the failure and cancellation paths need to know about how far the run got."""

    reserved: bool = False
    outcome: dict | None = None
    outcome_saved: bool = False
    worker_error: str | None = None


async def execute_campaign(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    gates: Mapping[str, Mapping],
    repository,
    entries: Sequence,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    protocol = loaded.protocol
    campaign_id = campaign_id_for(loaded)
    journal = journal_identity(repository)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "campaign",
            "campaign_id": campaign_id,
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "gates": {
                name: {"status": gate.get("status"), "cube_sha256": gate.get("cube_sha256")}
                for name, gate in gates.items()
            },
            "literature_entries": {entry.entry.id: getattr(entry, "sha256", None) for entry in entries},
            "journal": journal,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {
        "campaign_id": campaign_id,
        "cohort_sha256": cohort.sha256,
        "campaign_protocol_sha256": loaded.sha256,
        "journal": journal,
        "authorizes_promotion": False,
    }
    state = _RunState()
    with _sigterm_cancels(asyncio.current_task()):
        try:
            result = await _run_campaign(
                loaded,
                directory,
                state,
                base=base,
                cohort=cohort,
                build=build,
                gates=gates,
                repository=repository,
                entries=entries,
                environment=environment,
                progress=progress,
            )
        except asyncio.CancelledError, KeyboardInterrupt:
            await _record_cancellation(directory, state, base, repository, progress)
            raise
        except Exception as exc:
            result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            result["confirmation_consumed"] = None  # unknown until the ledger is read below
            if state.outcome is not None:
                result["outcome"] = {k: state.outcome[k] for k in ("status", "confirmed", "frozen")}
                if state.outcome_saved:
                    result["outcome_file"] = "outcome.json"
            try:
                if state.reserved:
                    try:
                        await repository.advance_pooled_campaign(campaign_id, "failed", {"error": result["error"]})
                    except Exception as ledger_exc:  # e.g. the campaign was already completed
                        result["ledger_error"] = f"{type(ledger_exc).__name__}: {ledger_exc}"
                result["confirmation_consumed"] = await _consumption(repository, campaign_id)
            finally:  # written even if a cancellation interrupts the journal calls above
                save_json_report(_finite_json(result), directory / "result.json")
            return result
    save_json_report(_finite_json(result), directory / "result.json")
    return result


async def _record_cancellation(
    directory: Path, state: _RunState, base: Mapping, repository, progress: Callable[[str], None] | None
) -> None:
    """The cancelled campaign's evidence: its outcome if it has one, result.json, then the ledger."""
    campaign_id = base["campaign_id"]
    if state.outcome is not None and not state.outcome_saved:
        try:
            _save_outcome(directory, state.outcome)
            state.outcome_saved = True
        except Exception as exc:
            _say(progress, f"cancelled: outcome.json could not be written ({type(exc).__name__}: {exc})")
    consumed, read_error = await _best_effort(_consumption(repository, campaign_id))
    result: dict[str, Any] = {**base, "status": "cancelled", "error": "cancelled", "confirmation_consumed": consumed}
    if read_error is not None:
        result["ledger_error"] = read_error
    if state.outcome is not None:
        result["outcome"] = {k: state.outcome[k] for k in ("status", "confirmed", "frozen")}
        if state.outcome_saved:
            result["outcome_file"] = "outcome.json"
    if state.worker_error is not None:
        result["worker_error"] = state.worker_error
    try:
        save_json_report(_finite_json(result), directory / "result.json")
    except Exception as exc:
        _say(progress, f"cancelled: result.json could not be written ({type(exc).__name__}: {exc})")
    if state.reserved:
        # Directly on the repository: the aborted journal ledger refuses every write.
        _, ledger_error = await _best_effort(
            repository.advance_pooled_campaign(campaign_id, "failed", {"error": "cancelled"})
        )
        if ledger_error is not None:
            _say(progress, f"cancelled: the ledger was not advanced to failed ({ledger_error})")
    note = "consumed; outcome kept" if consumed and state.outcome_saved else f"confirmation_consumed={consumed}"
    _say(progress, f"campaign {campaign_id} cancelled ({note}); see result.json")


async def _run_campaign(
    loaded: LoadedProtocol,
    directory: Path,
    state: _RunState,
    *,
    base: Mapping,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    gates: Mapping[str, Mapping],
    repository,
    entries: Sequence,
    environment: dict,
    progress: Callable[[str], None] | None,
) -> dict:
    protocol = loaded.protocol
    campaign_id = base["campaign_id"]
    protocol.require_campaign_ready()
    revision = require_clean_revision(environment)
    if cohort.sha256 != protocol.cohort_sha256:
        raise ValueError("cohort file does not match the protocol's cohort_sha256")
    if set(gates) != {name for name, _ in GATES}:
        raise ValueError("checks A, B and C must all be given")
    for name, gate in gates.items():
        if gate.get("status") != "passed":
            raise ValueError(f"check {name} has not passed (status {gate.get('status')!r})")
        if not gate.get("cube_sha256"):
            raise ValueError(f"check {name} records no cube")
    cubes = {gate.get("cube_sha256") for gate in gates.values()}
    if len(cubes) != 1:
        raise ValueError("checks A, B and C did not all run on one cube")
    (cube_sha256,) = cubes
    await repository.reserve_pooled_campaign(
        campaign_id,
        {
            "protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_sha256": cube_sha256,
            "code_revision": revision,
            "budget": protocol.formula_budget,
            "journal_scope": base["journal"]["scope"],
        },
    )
    state.reserved = True
    built = await build()
    check_coverage(built.cube, protocol.coverage)
    if built.cube.sha256 != cube_sha256:
        raise ValueError("the cached cube is not the cube checks A, B and C ran on")
    book = ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
    # The preflight and the overlap read discovery straight from the cube, so the windows'
    # opened trail stays discovery, selection, confirmation.
    discovery = built.cube.window(*protocol.windows.discovery)
    checked = await asyncio.to_thread(preflight, protocol, book, discovery)
    _say(progress, f"preflight: {checked} expressions computed on the discovery window, nothing charged")
    windows = CampaignWindows(built.cube, protocol.windows, cohort_sha256=cohort.sha256)
    ledger = JournalLedger(repository, asyncio.get_running_loop(), campaign_id)
    # Overlap is fixed from discovery picks before the search.
    literature = await asyncio.to_thread(literature_codes, entries, discovery, book, protocol)
    frozen_documents: list[dict] = []

    def on_frozen(frozen: list[str], search: SearchOutcome) -> None:
        marked_frozen = already_tested(frozen, search.codes, literature, protocol.dedupe_jaccard)
        frozen_documents.extend(_write_frozen(directory / "frozen", frozen, search, protocol, marked_frozen))
        ledger.advance("frozen", {"candidates": frozen_documents, "already_tested": marked_frozen})

    # A worker thread cannot be cancelled. Its task is awaited through asyncio.wait, which (like
    # asyncio.shield, without logging the expected CampaignAborted as an unhandled error) never
    # passes a cancellation on; on cancellation the ledger is aborted and the worker waited for.
    worker = asyncio.ensure_future(
        asyncio.to_thread(
            run_search_stages,
            protocol,
            windows,
            book,
            ledger,
            ledger,
            campaign_id=campaign_id,
            on_frozen=on_frozen,
            progress=progress,
        )
    )
    try:
        await asyncio.wait({worker})
    except asyncio.CancelledError, KeyboardInterrupt:
        ledger.abort()
        await _settle(worker)
        if not worker.cancelled():
            error = worker.exception()
            if error is None:
                state.outcome = worker.result()[0]
            elif not isinstance(error, CampaignAborted):
                state.worker_error = f"{type(error).__name__}: {error}"
        raise
    outcome, search = worker.result()
    state.outcome = outcome
    _save_outcome(directory, outcome)
    state.outcome_saved = True
    marked = already_tested(outcome["carried"], search.codes, literature, protocol.dedupe_jaccard)
    confirmed = list(outcome["confirmed"])
    probe_eligible = [fid for fid in confirmed if fid not in marked]
    await asyncio.to_thread(_write_records, directory / "formulas.jsonl", search.records)
    rejected: Counter[str] = Counter()
    for run in search.runs:
        rejected.update(run.rejected)
    rows = {row["formula_id"]: row for row in outcome["discovery"]}
    result = {
        **base,
        "status": outcome["status"],
        "cube_sha256": built.cube.sha256,
        "code_revision": revision,
        # The worker returned, so the window was consumed exactly when candidates were frozen.
        "confirmation_consumed": bool(outcome["frozen"]),
        "families": [run.summary() for run in search.runs],
        "proposals": {
            "evaluated": len(search.scores),
            "errors": sum(run.errors for run in search.runs),
            "rejected": dict(sorted(rejected.items())),
        },
        "survivors": sum(1 for row in outcome["discovery"] if row["passes"]),
        "carried": [
            {
                **rows[fid],
                "expression": search.expressions[fid],
                "family": search.families[fid],
                "already_tested_by": marked.get(fid),
            }
            for fid in outcome["carried"]
        ],
        "selection": outcome["selection"],
        "frozen": frozen_documents,
        "confirmation": outcome["confirmation"],
        "confirmed": confirmed,
        "probe_eligible": probe_eligible,
        "already_tested": marked,
        "expressions": {fid: search.expressions[fid] for fid in sorted({*outcome["carried"], *outcome["frozen"]})},
    }
    await repository.advance_pooled_campaign(
        campaign_id,
        "completed",
        {
            "status": outcome["status"],
            "confirmed": confirmed,
            "probe_eligible": probe_eligible,
            "already_tested": marked,
        },
    )
    return result


async def _consumed_candidates(repository, campaign_id: str, protocol: CampaignProtocol, cohort_sha256: str) -> list:
    """The frozen ids journaled with this campaign's lane-wide confirmation consumption."""
    consumed = await repository.get(POOLED_CONFIRMATION_KEY) or {"intervals": []}
    mine = [item for item in consumed["intervals"] if item["campaign_id"] == campaign_id]
    if len(mine) != 1:
        raise ValueError(f"the lane-wide confirmation record holds {len(mine)} intervals for {campaign_id}, not one")
    (item,) = mine
    interval = (date.fromisoformat(item["start"]), date.fromisoformat(item["end"]))
    if interval != tuple(protocol.windows.confirmation) or item["cohort_sha256"] != cohort_sha256:
        raise ValueError("the consumed interval is not this protocol's confirmation window and cohort")
    return list(item["candidates"])


async def execute_campaign_recovery(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    repository,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Recompute the outcome of this protocol's campaign after it consumed its window but never completed.

    It refuses unless the journal records the consumption and the campaign is not completed,
    and unless the reservation's code revision, cube, protocol, cohort and journal scope are the
    running ones. The frozen ids come from the lane-wide ``pooled/confirmation`` record, each
    expression from its charge, and the overlap marks from the freeze. The confirmation view is
    opened straight from the cube, because the journal already records its single use. Nothing
    is charged and nothing is consumed again; on success the campaign is ``completed``.
    """
    protocol = loaded.protocol
    campaign_id = campaign_id_for(loaded)
    journal = journal_identity(repository)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "campaign_recovery",
            "campaign_id": campaign_id,
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "reads": "the journaled frozen candidates on the consumed confirmation window; charges and consumes nothing",
            "journal": journal,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {
        "campaign_id": campaign_id,
        "cohort_sha256": cohort.sha256,
        "campaign_protocol_sha256": loaded.sha256,
        "journal": journal,
        "recovered": True,
        "authorizes_promotion": False,
    }
    try:
        protocol.require_campaign_ready()
        revision = require_clean_revision(environment)
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        record = await repository.get(f"pooled/campaign/{campaign_id}")
        if record is None:
            raise ValueError(
                f"no pooled campaign {campaign_id} in journal scope {journal['scope']}; nothing to recover"
            )
        if record["status"] == "completed":
            raise ValueError(f"pooled campaign {campaign_id} is already completed; nothing to recover")
        if "confirmation_consumed" not in record["stages"]:
            raise ValueError(
                f"pooled campaign {campaign_id} never consumed its confirmation window; "
                "rerun the campaign instead (it resumes)"
            )
        pins = {
            "protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "code_revision": revision,
            "journal_scope": journal["scope"],
        }
        for key, running in pins.items():
            if record.get(key) != running:
                raise ValueError(
                    f"the campaign was reserved with {key} {record.get(key)!r}; this recovery has {running!r}"
                )
        frozen = await _consumed_candidates(repository, campaign_id, protocol, cohort.sha256)
        freeze = record["details"].get("frozen")
        if freeze is None or [document["formula_id"] for document in freeze["candidates"]] != frozen:
            raise ValueError("the ledger's freeze does not match the candidates journaled with the consumption")
        expressions: dict[str, str] = {}
        for fid in frozen:
            charge = await repository.get(f"pooled/formula/{campaign_id}/{fid}")
            if charge is None or formula_id(charge["expression"]) != fid:
                raise ValueError(f"frozen candidate {fid} has no matching charge in the ledger")
            expressions[fid] = charge["expression"]
        marked = {fid: entry for fid, entry in freeze["already_tested"].items() if fid in expressions}
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        if built.cube.sha256 != record["cube_sha256"]:
            raise ValueError("the cached cube is not the cube the campaign ran on")
        _say(progress, f"recovery: recomputing the confirmation of {len(frozen)} frozen candidates (nothing charged)")
        book = ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
        formulas = [book.formula(expressions[fid]) for fid in frozen]
        view = built.cube.window(*protocol.windows.confirmation)
        rows, confirmed = await asyncio.to_thread(_confirmation, formulas, frozen, view, protocol)
        status = "confirmed" if confirmed else "none_confirmed"
        probe_eligible = [fid for fid in confirmed if fid not in marked]
        save_json_report(
            _finite_json(
                {"status": status, "frozen": frozen, "confirmation": rows, "confirmed": confirmed, "recovered": True}
            ),
            directory / "outcome.json",
        )
        result = {
            **base,
            "status": status,
            "cube_sha256": built.cube.sha256,
            "code_revision": revision,
            "confirmation_consumed": True,
            "frozen": freeze["candidates"],
            "confirmation": rows,
            "confirmed": confirmed,
            "probe_eligible": probe_eligible,
            "already_tested": marked,
            "expressions": expressions,
        }
        await repository.advance_pooled_campaign(
            campaign_id,
            "completed",
            {
                "status": status,
                "confirmed": confirmed,
                "probe_eligible": probe_eligible,
                "already_tested": marked,
                "recovered": True,
            },
        )
    except Exception as exc:
        result = {
            **base,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "confirmation_consumed": await _consumption(repository, campaign_id),
        }
    save_json_report(_finite_json(result), directory / "result.json")
    return result
