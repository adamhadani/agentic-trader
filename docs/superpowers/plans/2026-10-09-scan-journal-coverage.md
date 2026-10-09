# Scan journal coverage (PR A.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every full-universe scan that can send cards (suggestion, swing, unrestricted operator) journals its `scan_candidates_ranked` event, named by trigger, so card statistics and outcomes cover the cards actually sent.

**Architecture:** Decouple the ranked-candidate journal in `TradingCopilot.run_scan` from the `shadow_evidence` flag (which keeps gating the shadow ranker alone), add a `ScanTrigger` enum and `trigger` keyword that the two scheduled jobs set, and surface the trigger in the outcomes report. No schema, budget, policy or scheduling change.

**Tech Stack:** Python 3.14, `env -u VIRTUAL_ENV uv run pytest`, pydantic v2, async SQLAlchemy journal (`db.workflows.append`), existing desk fixtures in `tests/agent/`.

**Spec:** `docs/superpowers/specs/2026-10-09-scan-journal-coverage-design.md`

## Global Constraints

- Shadow evidence (`live_cross_section`, `_shadow_blocks`) runs only when `shadow_evidence=True` and the scan is unrestricted — unchanged.
- Journal condition: `not dry_run and budget != ScanBudget.NONE and native_count and not symbols and timeframe is None`.
- `trigger` must never influence ranking, budget, sending, LLM, shadow or policy; only the journal payload.
- No new migration (head `008_alpha_pipeline`); no new config keys.
- Tests use the existing `shadow_desk`/`temp_db` fixtures; no network, no PostgreSQL required.
- `uv run pre-commit run --all-files` clean before each commit; commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A swing scan that ranks candidates but sends none (budget spent) must still journal its runners-up — the journal condition uses `native_count`, not `sent`.
2. A swing scan that sends a card must journal `signal_id` for it exactly as the suggestion scan does.
3. A dry run (`dry_run=True`, budget forced to NONE) journals nothing even with `trigger=SWING_SCAN`.
4. A pre-A.1 event without `"trigger"` must still load into the outcomes frame (`trigger` None) and into `compute_card_stats`.
5. The suggestion job must keep passing `shadow_evidence=True` *and* `trigger=SUGGESTION_SCAN`; if a test only checks one, a regression could silently drop shadow blocks.

---

### Task 1: ScanTrigger, decoupled journal, scheduler wiring, outcomes trigger column

**Files:**
- Modify: `agentic_trader/execution/durable.py` (add `ScanTrigger` after `EventKind`)
- Modify: `agentic_trader/agent/copilot.py` (`run_scan` signature/docstring ~617-700, shadow comment ~1106-1117, journal call ~1442, `_journal_scan_ranking` ~1700-1745)
- Modify: `agentic_trader/cli/commands/service.py` (`make_suggestion_scan` ~367, swing job ~622-633)
- Modify: `agentic_trader/research/setups/outcomes.py` (`_COLUMNS`, row builder ~241, `summarize`)
- Test: `tests/agent/test_scan_shadow_ranker.py`, `tests/cli/test_service_scheduling.py`, `tests/research/setups/test_outcomes.py`

**Interfaces:**
- Produces: `ScanTrigger(StrEnum)` with `SUGGESTION_SCAN="suggestion_scan"`, `SWING_SCAN="swing_scan"`, `OPERATOR_SCAN="operator_scan"`; `run_scan(..., trigger: ScanTrigger = ScanTrigger.OPERATOR_SCAN)`; `_journal_scan_ranking(..., trigger: ScanTrigger, ...)`; outcomes frame column `trigger`; `summarize(...)["triggers"]`.

- [ ] **Step 1: Write the failing tests (copilot journal)** in `tests/agent/test_scan_shadow_ranker.py`. Replace the two tests `test_swing_scan_shaped_call_neither_computes_nor_journals_shadow` and `test_unrestricted_manual_scan_neither_computes_nor_journals_shadow` with:

```python
async def test_swing_scan_journals_its_ranking_without_shadow(shadow_desk, temp_db, app_config, artifact, monkeypatch):
    """The daemon's swing scan (``run_scan(use_llm, dry_run, budget=FULL, trigger=SWING_SCAN)``)
    computes no shadow evidence but journals the same ranked-candidate event the suggestion
    scan does, so card statistics cover the cards it sends."""
    app_config.scan.shadow_ranker_artifact = artifact[0]

    def unexpected(*_args, **_kwargs):
        raise AssertionError("shadow evidence is scoped to the suggestion-scan job alone")

    monkeypatch.setattr(copilot_module, "live_cross_section", unexpected)
    await shadow_desk.run_scan(True, False, budget=ScanBudget.FULL, trigger=ScanTrigger.SWING_SCAN)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["shadow_ranker"] is None
    [event] = await _ranked_events(temp_db)
    payload = event["payload"]
    assert payload["trigger"] == "swing_scan"
    assert payload["scope"] == "universe"
    assert [c["shadow"] for c in payload["candidates"]] == [None] * len(payload["candidates"])
    sent = [c for c in payload["candidates"] if c["outcome"] == "sent"]
    assert [c["signal_id"] for c in sent] == [signal["id"]]
    assert "shadow_ranker_error" not in shadow_desk.last_scan_summary


async def test_unrestricted_operator_scan_journals_as_operator_scan(
    shadow_desk, temp_db, app_config, artifact, monkeypatch
):
    """``copilot scan``/Telegram ``/scan`` with no symbols and no timeframe never sets
    ``shadow_evidence`` and passes no trigger: it journals as ``operator_scan``."""
    app_config.scan.shadow_ranker_artifact = artifact[0]

    def unexpected(*_args, **_kwargs):
        raise AssertionError("shadow evidence is scoped to the suggestion-scan job alone")

    monkeypatch.setattr(copilot_module, "live_cross_section", unexpected)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)

    [event] = await _ranked_events(temp_db)
    assert event["payload"]["trigger"] == "operator_scan"
    assert all(c["shadow"] is None for c in event["payload"]["candidates"])


async def test_suggestion_scan_journals_as_suggestion_scan_with_shadow(shadow_desk, temp_db, app_config, artifact):
    app_config.scan.shadow_ranker_artifact = artifact[0]
    await shadow_desk.run_scan(
        use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True, trigger=ScanTrigger.SUGGESTION_SCAN
    )
    [event] = await _ranked_events(temp_db)
    assert event["payload"]["trigger"] == "suggestion_scan"
    assert all(c["shadow"] is not None for c in event["payload"]["candidates"])


async def test_dry_run_journals_nothing_even_as_swing_scan(shadow_desk, temp_db):
    await shadow_desk.run_scan(True, True, budget=ScanBudget.FULL, trigger=ScanTrigger.SWING_SCAN)
    assert await _ranked_events(temp_db) == []


async def test_swing_scan_with_budget_spent_still_journals_runners_up(shadow_desk, temp_db, app_config):
    """Journaling keys on ranked candidates, not on cards sent."""
    app_config.scan.cards_per_scan = 0  # see note below if the fixture spells the budget differently
    await shadow_desk.run_scan(True, False, budget=ScanBudget.FULL, trigger=ScanTrigger.SWING_SCAN)
    assert await temp_db.get_recent_signals(limit=10) == []
    [event] = await _ranked_events(temp_db)
    assert all(c["outcome"] != "sent" for c in event["payload"]["candidates"])
```

Add `from agentic_trader.execution.durable import ScanTrigger` to the test imports. For the last test, read the `shadow_desk` fixture and the existing budget tests in `tests/agent/` to find how a zero per-scan card budget is configured (the fixture or `app_config.scan.*`); if no config spells it, instead monkeypatch the budget to be spent (e.g. set `shadow_desk.config.scan.max_cards_per_scan = 0` or whatever the real attribute is) — the assertion is what matters, the setup must use the real knob.

- [ ] **Step 2: Write the failing scheduler tests** in `tests/cli/test_service_scheduling.py`. Read the file's existing fixtures first (how `register_suggestion_scans`/the scheduler are exercised). Add:

```python
async def test_suggestion_job_requests_shadow_evidence_and_names_its_trigger(config, temp_db):
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    job = make_suggestion_scan(copilot, use_llm=True)
    await job(digest=False, scheduled_time_et="10:35")
    kwargs = copilot.run_scan.await_args.kwargs
    assert kwargs["shadow_evidence"] is True
    assert kwargs["trigger"] is ScanTrigger.SUGGESTION_SCAN
    assert kwargs["budget"] is ScanBudget.FULL
```

and a test that the `swing_scan` job registered by `serve` carries `trigger=ScanTrigger.SWING_SCAN` and `budget=ScanBudget.FULL` in its kwargs. Read how `register_*` / the scheduler is built in `service.py` (~610-640); if the swing job is added inline in the serve coroutine rather than a helper, extract a small `register_swing_scan(scheduler, copilot, config, *, use_llm)` helper (same shape as `register_intraday_scan`) so it is testable, and assert on `scheduler.get_job("swing_scan").kwargs`. Keep `next_run_time=datetime.now(UTC)` (spec §3) and assert it is set (not None).

- [ ] **Step 3: Write the failing outcomes tests** in `tests/research/setups/test_outcomes.py` (use the file's `_event` helper, extending it with an optional `trigger=None` kwarg that is added to the payload only when not None):

```python
def test_trigger_column_comes_from_the_payload_and_is_none_for_older_events(...):
    # one event with trigger="swing_scan", one without → frame["trigger"] == ["swing_scan", None] (order by rows)

def test_summarize_reports_scans_and_sent_cards_per_trigger(...):
    # events: suggestion (2 candidates, 1 sent), swing (1 candidate, 1 sent), one without trigger
    # summary["triggers"] == {"suggestion_scan": {"scans": 1, "sent": 1}, "swing_scan": {"scans": 1, "sent": 1}, None/"unknown": {"scans": 1, "sent": 0}}
```

Rule: events without a trigger are reported under the key `"unknown"` (JSON cannot key by None). Also add to `tests/research/setups/test_card_stats.py` one test that `compute_card_stats` over a suggestion event and a swing event counts both (n = sum).

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_scan_shadow_ranker.py tests/cli/test_service_scheduling.py tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py -q -x`
Expected: FAIL (ImportError on `ScanTrigger`, then TypeError on `trigger` kwarg / missing journal).

- [ ] **Step 5: Implement**

`agentic_trader/execution/durable.py`, after `EventKind`:

```python
class ScanTrigger(StrEnum):
    """Which caller ran a full-universe scan; journaled in ``scan_candidates_ranked``.

    It names the path a card came from and nothing else: ranking, budgets, sending, the
    shadow ranker (``shadow_evidence``) and the card policy never read it.
    """

    SUGGESTION_SCAN = "suggestion_scan"  # the scheduled New York-time job (``make_suggestion_scan``)
    SWING_SCAN = "swing_scan"  # the daemon's interval job, including its run at daemon start
    OPERATOR_SCAN = "operator_scan"  # ``copilot scan`` / Telegram ``/scan`` with no symbols or timeframe
```

`agentic_trader/agent/copilot.py`:
- import `ScanTrigger` alongside the existing `EventKind`/`RankedOutcome` import;
- `run_scan(..., scheduled_at: datetime | None = None, trigger: ScanTrigger = ScanTrigger.OPERATOR_SCAN)`; docstring: rewrite the `shadow_evidence` paragraph to say it gates the shadow ranker block only, and add a `trigger` paragraph: "names the caller in the ranked-candidate journal; every unrestricted non-dry scan with a budget journals `scan_candidates_ranked` (suggestion, swing and operator scans alike), only the shadow block is suggestion-only";
- the comment block above `compute_shadow_evidence` (~1106): keep the shadow rationale, drop "nor journaling" wording, add one line that the ranked journal below is independent of this flag;
- the journal call (~1442): condition becomes `if not dry_run and budget != ScanBudget.NONE and native_count and not symbols and timeframe is None:` and passes `trigger=trigger`;
- `_journal_scan_ranking(self, *, scan_id, decided_at, budget, trigger: ScanTrigger, ranked, ...)`: payload `"trigger": trigger.value`; update its docstring/comment (remove "only ever called when run_scan computed shadow evidence").

`agentic_trader/cli/commands/service.py`:
- import `ScanTrigger`;
- `make_suggestion_scan`: add `trigger=ScanTrigger.SUGGESTION_SCAN` to the `run_scan` call;
- swing job: `kwargs={"budget": ScanBudget.FULL, "trigger": ScanTrigger.SWING_SCAN}` (extract `register_swing_scan` if Step 2 needed it; keep `next_run_time=datetime.now(UTC)` with a comment: "runs once at start so the `scan` readiness component observes this run; its cards are journaled like any other").

`agentic_trader/research/setups/outcomes.py`:
- `_COLUMNS`: insert `"trigger"` after `"session"`;
- the event walk (~114-119): `trigger = payload.get("trigger")` and include `"trigger": trigger` in each appended entry;
- row builder: `"trigger": entry.get("trigger")`;
- `summarize`: add `"triggers"`: group the frame by `trigger` (fill None with `"unknown"`), `scans` = unique `scan_id` count, `sent` = `sent` sum, as plain ints; empty frame → `{}`.

- [ ] **Step 6: Run the targeted tests, then the impacted suites**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent tests/cli tests/research/setups tests/execution tests/workflows -q`
Expected: PASS. Fix any test that asserted the old "swing/manual scans journal nothing" or the hard-coded `"trigger": "suggestion_scan"` (search `tests/` for `suggestion_scan` and `_ranked_events`).

- [ ] **Step 7: Pre-commit and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --all-files
git add -A agentic_trader tests
git commit -m "Scan journal coverage: every full-universe scan journals scan_candidates_ranked with its ScanTrigger; shadow evidence stays suggestion-only; outcomes report per-trigger counts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: Docs and full suite

**Files:**
- Modify: `docs/card-evidence.md` (~136, ~217), `docs/production.md` (~525-545 and the swing-scan sentence ~112), `CLAUDE.md` (contract 8), `docs/alpha-roadmap.md` (item 1 status under "Desk direction after the fixed-set lane (October 8)")
- Spec/plan already in `docs/superpowers/`.

**Interfaces:** Consumes Task 1's `ScanTrigger` names and the `triggers` summary block.

- [ ] **Step 1: Update `docs/card-evidence.md`**: replace "only scheduled suggestion scans are labelled" with "every full-universe scan (suggestion, swing including the daemon-start run, unrestricted `/scan`) is journaled and labelled; symbol- or timeframe-scoped scans (intraday job, `/scan SYMBOL`, Re-evaluate) show evidence and obey the policy but are not journaled". Rewrite the **Coverage** limitation accordingly and add: "Cards sent by swing or operator scans before 2026-10-09 (PR A.1) were never journaled and stay unlabelled; the record under-covers sent cards before that date." Mention the `trigger` field and the `triggers` block of `cards outcomes`.
- [ ] **Step 2: Update `docs/production.md`**: in the shadow-evidence paragraph, keep the shadow gating but state that the ranked-candidate journal is independent of the flag and names its `trigger` (`suggestion_scan` | `swing_scan` | `operator_scan`); update "one `scan_candidates_ranked` domain event per suggestion scan" to "per full-universe scan"; in the swing-scan description add that it runs once at daemon start (readiness) and journals its ranking.
- [ ] **Step 3: Update `CLAUDE.md` contract 8**: after "`setup_quality` orders cards and is stored in provenance — it is not validated alpha." add: "Every unrestricted non-dry scan with a budget journals `scan_candidates_ranked` with its `ScanTrigger`; shadow evidence stays suggestion-scan only."
- [ ] **Step 4: Update `docs/alpha-roadmap.md`** item 1 status: PR A merged 2026-10-08 (#110) and deployed; A.1 closes the journal coverage gap (18/22 sent cards unjournaled before it).
- [ ] **Step 5: Full suite and pre-commit**

Run: `env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider` (expect all pass; note counts) and `env -u VIRTUAL_ENV uv run pre-commit run --all-files`.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs
git commit -m "Docs: scan journal coverage — every full-universe scan is journaled and labelled; shadow evidence suggestion-only; roadmap item 1 status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
