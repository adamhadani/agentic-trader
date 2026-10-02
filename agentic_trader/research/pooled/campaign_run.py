"""The budgeted pooled campaign, run once per protocol against the journal ledger.

**Gate.** It refuses to start unless checks A, B and C all passed for this cohort and
protocol, on one cube, at the clean code revision that is running.

**Sequence.**

1. Write ``protocol.json`` and ``manifest.json``.
2. Reserve the campaign in the ledger before reading anything.
3. Build or load the cube.
4. Run the per-family search, charging each formula before it is scored.
5. Run the pooled discovery finish and selection.
6. Write the frozen documents.
7. Consume the lane-wide confirmation interval, then read it.

**Overlap rule.** A finalist whose discovery picks overlap a literature entry's at Jaccard
>= ``dedupe_jaccard`` is ``already_tested_by`` that entry. It keeps its Holm slot and is
never probe-eligible: both literature entries failed.

**Failure.** A failure is recorded as ``status: failed``; charges remain. A confirmed,
probe-eligible formula earns eligibility for a separately specified paper probe, nothing more.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentic_trader.research.pooled.campaign import CampaignProtocol, CampaignWindows, LoadedProtocol, _jaccard
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, check_coverage
from agentic_trader.research.pooled.formula import Formula, allowed_mask, select_picks
from agentic_trader.research.pooled.genetic import SearchOutcome, run_search_stages
from agentic_trader.research.pooled.ledger import JournalLedger
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "GATES",
    "LITERATURE_ENTRIES",
    "already_tested",
    "campaign_id_for",
    "check_gate",
    "execute_campaign",
    "literature_cells",
    "require_clean_revision",
]

LITERATURE_ENTRIES = ("config/research/pooled/high52-v1.json", "config/research/pooled/reversal-lowmax-v1.json")
# (gate name, the ``check`` its manifest must record)
GATES = (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))


def campaign_id_for(loaded: LoadedProtocol) -> str:
    return f"pooled-campaign-v{loaded.protocol.version}-{loaded.sha256[:16]}"


def require_clean_revision(environment: Mapping) -> str:
    revision = str(environment.get("runtime", {}).get("revision", "unavailable"))
    if revision == "unavailable" or revision.endswith("-dirty"):
        raise ValueError(f"refusing to run from a dirty or unknown code revision ({revision}); commit first")
    return revision


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


def literature_cells(entries: Iterable, view: CubeView, book: ScoreBook) -> dict[str, frozenset]:
    """Each literature entry's discovery picks on this cube (scores, filters and hold lengths)."""
    stop = view.offset + len(view.sessions)
    cells: dict[str, frozenset] = {}
    for loaded in entries:
        formula: Formula = loaded.entry.formula
        scores = book.panel(formula.score)[view.offset : stop]
        filters = [book.panel(spec.expression)[view.offset : stop] for spec in formula.filters]
        allowed = allowed_mask(formula, filters, view.eligible)
        cells[loaded.entry.id] = frozenset(select_picks(scores, allowed, view, formula.k).cells())
    return cells


def already_tested(
    carried: Sequence[str], cells: Mapping[str, frozenset], literature: Mapping[str, frozenset], threshold: float
) -> dict[str, str]:
    """Finalist id -> the literature entry whose discovery picks it overlaps at Jaccard >= threshold."""
    marked: dict[str, str] = {}
    for fid in carried:
        for entry_id, entry_cells in literature.items():
            if _jaccard(set(cells[fid]), set(entry_cells)) >= threshold:
                marked[fid] = entry_id
                break
    return marked


def _write_frozen(
    directory: Path, frozen: Sequence[str], search: SearchOutcome, protocol: CampaignProtocol
) -> list[dict]:
    documents = []
    for fid in frozen:
        formula = Formula(score=search.expressions[fid], k=protocol.k)
        save_json_report(
            {
                "formula_id": fid,
                "family": search.families[fid],
                "expression": search.expressions[fid],
                "formula": formula.model_dump(mode="json"),
                "identity": formula.identity,
            },
            directory / f"{fid}.json",
        )
        documents.append({"formula_id": fid, "identity": formula.identity})
    return documents


def _write_records(path: Path, records: Iterable[Mapping]) -> None:
    text = "".join(json.dumps(_finite_json(dict(record)), sort_keys=True) + "\n" for record in records)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(text)


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
            "literature_entries": [entry.entry.id for entry in entries],
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
        "authorizes_promotion": False,
    }
    reserved = False
    try:
        protocol.require_campaign_ready()
        revision = require_clean_revision(environment)
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        if set(gates) != {name for name, _ in GATES}:
            raise ValueError("checks A, B and C must all be given")
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
            },
        )
        reserved = True
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        if built.cube.sha256 != cube_sha256:
            raise ValueError("the cached cube is not the cube checks A, B and C ran on")
        book = ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
        windows = CampaignWindows(built.cube, protocol.windows, cohort_sha256=cohort.sha256)
        ledger = JournalLedger(repository, asyncio.get_running_loop(), campaign_id)
        frozen_documents: list[dict] = []

        def on_frozen(frozen: list[str], search: SearchOutcome) -> None:
            frozen_documents.extend(_write_frozen(directory / "frozen", frozen, search, protocol))
            ledger.advance("frozen", {"candidates": frozen_documents})

        outcome, search = await asyncio.to_thread(
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
        literature = await asyncio.to_thread(literature_cells, entries, windows.discovery(), book)
        marked = already_tested(outcome["carried"], search.cells, literature, protocol.dedupe_jaccard)
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
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if reserved:
            try:
                await repository.advance_pooled_campaign(campaign_id, "failed", {"error": result["error"]})
            except Exception as ledger_exc:  # e.g. the campaign was already completed
                result["ledger_error"] = f"{type(ledger_exc).__name__}: {ledger_exc}"
    save_json_report(_finite_json(result), directory / "result.json")
    return result
