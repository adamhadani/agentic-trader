# Catalog auto-entry and weekly scorecard: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Alpaca paper account, auto-enter PEAD-probe and insider-watch cards, cap non-native positions at
5 of 8, and send a weekly per-lane scorecard to Telegram.

**Architecture:**
- The existing auto-entry marker gains a per-source rule for enabled catalog sources. The sweep, admission and
  digest are reused unchanged.
- One non-native predicate feeds a new admission refusal and the scan-time ceiling.
- The `cards outcomes` blocks are extracted into `research/setups/scorecard.py`, used by the CLI, a new
  `cards scorecard` command and a once-per-ISO-week daemon job.

**Tech stack:** Python 3.14, uv, pydantic config, APScheduler, SQLAlchemy async (SQLite tests, PostgreSQL
integration), pandas.

**Spec:** `docs/superpowers/specs/2026-11-14-catalog-auto-entry-scorecard-design.md`

## Global constraints

- Paper only.
  - `execution.auto_entry_catalog` non-empty acts only with `execution.auto_entry: paper`; otherwise it is
    inert and `load_config` logs one `auto_entry_catalog_inert` warning (final-review ruling I1, replacing the
    original refusal).
  - The live-account refusal is inherited.
  - The sweep's `is_paper_scope` check stays.
- Code defaults change nothing:
  - `auto_entry_catalog: ()`;
  - `portfolio.max_non_native_positions: None`;
  - `scorecard.enabled: True`, but the job registers only in the daemon.
- Shipped values (T5): `auto_entry_catalog: ["pead", "insider_watch"]`, `max_non_native_positions: 5`.
- No new outbox kind (the scorecard is `NotificationKind.MESSAGE`), no migration (head 008), no compatibility
  aliases. POST/PATCH/DELETE are never retried.
- Refusal reason literal: `non_native_capacity`. Scorecard notification key:
  `weekly-scorecard/{iso_year}-W{iso_week:02d}`. Due Saturday 09:00 America/New_York.
- Watch card line when its source is enabled: `auto-entered on paper; unvalidated; earns no promotion credit`.
  Otherwise unchanged: `tap-only, never auto-entered; earns no promotion credit`.
- Telegram text carries type names and fixed text only, never raw exception messages, chat IDs or secrets.
- Tests:
  - offline fakes and temporary SQLite;
  - PostgreSQL only for the admission count query and the checkpoint read, via
    `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 … --run-postgres`;
  - never any non-`test_*` database.
- The repo is public: no local absolute paths, emails or secrets in committed files.
- Implementers STAGE ONLY; the controller commits. Ruff C408/PLC0415 are strict. Commit a task's new tests
  together with its implementation, because pre-commit stashes unstaged tracked changes.

## Review focus

1. **A catalog card on a live scope or with `auto_entry: off`** never gets a marker. Test both.
2. **A PEAD card whose probe is killed (−4R) or expired** is marked, but admission refuses it through
   `probe_block_reason`. The sweep journals `auto_refused` and re-cards, with no halt.
3. **The 6th non-native entry while 5 are open, or 4 open plus 1 submitting,** is refused with
   `non_native_capacity`. A native entry at the same count is unaffected.
4. **The scorecard when a lane has zero cards, zero closed trades or a bar fetch failure** renders "none" or
   "n/a" and still sends. A failure logs the type name and retries next tick, never twice per week.
5. **A daemon restart after Saturday 09:00** publishes once on the first tick. A restart mid-week publishes
   nothing.

---

### Task 1: Catalog auto-entry marker, watch text, watch measurement

**Files:**
- `agentic_trader/config.py`: `ExecutionConfig.auto_entry_catalog`, plus validation in `load_config`.
- `agentic_trader/execution/auto_entry.py`
- `agentic_trader/agent/copilot.py`: the marker call site (about lines 1919–1935).
- `agentic_trader/notifier/telegram_bot.py`: the `_watch_block` line.
- `agentic_trader/research/apriori/watch_outcomes.py`: `summarize_watch`.
- Tests:
  - `tests/execution/test_auto_entry_gate.py`
  - `tests/agent/test_auto_entry_sweep.py`
  - `tests/notifier/test_auto_entry_card.py`
  - `tests/research/apriori/` (watch summary)
  - `tests/config/`

**Interfaces:**
- **Produces:** `auto_entry_marker(config, *, trigger, is_catalog, alpha, lifetime_marker, llm_verdict,
  catalog_source: str | None = None) -> dict | None`.
  - For `catalog_source in config.execution.auto_entry_catalog`, `trigger == SUGGESTION_SCAN`, `not alpha` and
    `auto_entry_active(config)`, it returns the marker WITHOUT `llm_approved` and WITHOUT `lifetime_marker`.
  - The call site passes the source id and asserts that the card's `alpha_policy` resolves a lifetime through
    `entry_lifetime_of`.
  - Native behaviour is unchanged.
- **Produces:** `summarize_watch(...)["auto_entries"]`. `taps` counts only signals whose entry work item's
  `entry_trigger` is `tap`, or that left the card without an auto-entry event. Read the trigger the way
  `execution_evidence.collect_execution` does.

- [ ] Write the failing tests:
  - Marker matrix:
    - enabled `pead` gets the marker;
    - enabled `insider_watch` gets the marker;
    - a catalog source not in the list gets None;
    - `auto_entry: off` gets None;
    - a live config is refused at load;
    - alpha, swing and operator scans get None;
    - a native card without LLM approval still gets None.
  - The sweep test's watch and drift fixtures, with sources enabled, produce `provenance["auto_entry"] ==
    {"mode": "paper"}` and the card `auto_entry: "placing"`. With sources disabled, the existing assertions
    hold.
  - The watch line for enabled vs disabled.
  - `summarize_watch` separates `taps` from `auto_entries`.
- [ ] Run them red: `uv run pytest tests/execution/test_auto_entry_gate.py tests/agent/test_auto_entry_sweep.py
  tests/notifier/test_auto_entry_card.py -q`.
- [ ] Implement it.
- [ ] Run the affected test files green, then `uv run pre-commit run --files <changed>`, then stage.

### Task 2: Non-native capacity (O3)

**Files:**
- `agentic_trader/config.py`: `PortfolioConfig.max_non_native_positions`, validated to 1..`max_concurrent_positions`.
- A new pure predicate `agentic_trader/execution/scope.py`: `is_non_native(*, strategy: str | None,
  alpha_version: str | None, alpha_policy: Any, provenance: Mapping | None) -> bool`.
  - True for a catalog or watch strategy id, the paper-probe provenance tag, or a set `alpha_policy` or
    `alpha_version`.
  - It imports no `agent` module.
- `agentic_trader/storage/workflow.py`: admission for non-native entries, under the alpha lock.
- `agentic_trader/agent/catalog_sources.py` and `agentic_trader/agent/insider_watch.py`: the scan-time ceiling.
- `agentic_trader/agent/copilot.py`: `_release_exit_scope` stays, plus a parity test.
- Tests: `tests/storage/` or `tests/execution/` for admission, `tests/agent/` for the ceiling and parity, and
  `tests/integration/test_postgres.py` (one count case).

**Interfaces:**
- **Produces:** the refusal reason `non_native_capacity`. It is a business refusal, mapped and journaled like
  the existing per-source/probe refusals: no halt, capacity released.
- **Count:** open non-native positions are signals in the scope with status `EXECUTED` (the same "open" notion
  the per-source `max_open_*` counts use). In-flight submissions are non-native entry work items not yet
  terminal. The count includes the entry being admitted.

- [ ] Write the failing tests:
  - At cap 5, the 6th non-native entry is refused. At 4 open plus 1 in flight, the next is refused.
  - A native entry at 5 non-native open is admitted.
  - `None` means no check.
  - Validation bounds.
  - Parity: `is_non_native(...) == (_release_exit_scope(...) != "native")` over native, pead, watch, probe and
    alpha rows.
  - The scan ceiling: with 5 non-native open, neither catalog source cards, and their decision events are still
    journaled.
- [ ] Run them red, implement, run green (including the PostgreSQL case), pre-commit, stage.

### Task 3: Scorecard module and CLI

**Files:**
- Create `agentic_trader/research/setups/scorecard.py` and `agentic_trader/presentation/scorecard.py`
  (the formatter).
- Modify `agentic_trader/cli/commands/cards.py`: `outcomes` calls the extracted functions, with unchanged
  output. Add a new `scorecard` command.
- Tests: `tests/research/setups/test_scorecard.py`, `tests/presentation/test_scorecard.py`, and the existing
  `cards outcomes` tests, which must stay green unchanged.

**Interfaces:**
- **Produces:** the async `build_scorecard(db, config, *, now: datetime, activity_days: int = 7,
  record_days: int = 90, bars=…, sip=…) -> LaneScorecard`. Bar sources are injected so tests use fakes.
- **Produces:** pydantic `LaneScorecard{window_start, window_end, lanes: tuple[LaneRow, ...], account:
  AccountRow | None}` with:
  - `LaneRow{lane: Literal["native","pead","insider_watch"], sent, entered_auto, entered_tap, filled, closed,
    realized_r_sum, record_closed, record_mean_r, open_positions, cf_mature, cf_mean_r, cf_mean_excess_r: float
    | None, note: str | None}`;
  - `AccountRow{start_equity, end_equity}`, read from ledger checkpoints.
- **Produces:** `format_scorecard(card: LaneScorecard) -> str`, Telegram HTML in the style of
  `presentation/auto_entry_digest.py`.
  - None and empty values render as "n/a" or "none".
  - The footer reads: "Counterfactual labels assume study-price fills; no lane has validated edge."
- **Produces:** `copilot cards scorecard [--days 7]`, which prints `format_scorecard` output and sends nothing.

- [ ] Write the failing tests:
  - A fake DB and bars with known signals produce exact lane numbers. Realized R = `realized_pnl /
    risk_dollars` over closed rows in the window.
  - Zero-card lanes are handled.
  - A bar failure gives `cf_* = None` with a note naming the type.
  - Formatter snapshot assertions.
  - `cards outcomes` output is unchanged (existing tests).
- [ ] Run them red, implement by moving the inline CLI blocks rather than copying them, run green, pre-commit,
  stage.

### Task 4: Weekly daemon job

**Files:**
- `agentic_trader/config.py`: `ScorecardConfig{enabled: bool = True, due_weekday: int = 5, due_time_et: str =
  "09:00", activity_days: int = 7, record_days: int = 90}`, with `AppConfig.scorecard`.
- `agentic_trader/agent/copilot.py`: `publish_weekly_scorecard(now) -> bool`.
- `agentic_trader/storage/workflow.py`: queue with an idempotent key.
- `agentic_trader/cli/commands/service.py`: an interval job every 15 minutes, `id="weekly_scorecard"`,
  `max_instances=1`, `coalesce=True`.
- `load_config`: passes the `scorecard:` YAML block.
- Tests: `tests/agent/test_weekly_scorecard.py` and `tests/cli/` (job registration).

**Interfaces:**
- **Consumes:** `build_scorecard` and `format_scorecard` (Task 3).
- **Produces:** due logic. A tick at or after this ISO week's Saturday 09:00 ET, with no
  `weekly-scorecard/{iso_year}-W{iso_week:02d}` notification yet, builds and publishes. Otherwise it is a
  no-op.
- **Runs:**
  - the build in `asyncio.to_thread`, or the worker pattern of `CardStatsWorker`;
  - skipped while any scan runs (`scan_busy`);
  - paced by `config.market_data.max_requests_per_minute`.
- **Writes:** one `NotificationKind.MESSAGE` (`{"text", "formatted": True}`) and one journal event
  (`weekly_scorecard`) in the same transaction.
- **Failures:** log `weekly_scorecard_failed` with `error_type` only, return False, never raise.

- [ ] Write the failing tests:
  - Due vs not due: Friday 23:00 → no; Saturday 09:00 → yes; Saturday 09:15 again → no (key exists); next
    Monday after a missed Saturday → yes, once.
  - A build failure leads to a retry next tick.
  - Disabled → no job registered.
  - Quiet hours are irrelevant.
- [ ] Run them red, implement, run green, pre-commit, stage.

### Task 5: Shipped config, docs, shipped-config test

**Files:**
- `config/config.yaml`:
  - `execution.auto_entry_catalog: ["pead", "insider_watch"]`;
  - `portfolio.max_non_native_positions: 5`;
  - a `scorecard:` block with the defaults;
  - updated comments, removing "Never auto-entered" on insider_watch.
- `tests/config/test_shipped_config.py`
- `CLAUDE.md`: contract 1's sweep sentence, the Paper probes and Insider watch cards paragraphs; contract 8's
  scorecard.
- `docs/paper-auto-entry.md`
- `docs/production.md`: the auto-entry section, plus a "Weekly scorecard" runbook.
- `docs/apriori-alphas.md`: PEAD "never automatically" and the watch section.
- `docs/card-evidence.md`: watch `taps`/`auto_entries`, and a scorecard section.
- `docs/pead-base-rate-2026-10-30.md:98`
- `docs/alpha-roadmap.md` and `docs/alpha-directions-2026-10-30/README.md`: O3 now implemented.
- Config and module comments named in the spec.

**Interfaces:**
- **Consumes:** every name from T1–T4.

- [ ] Write the failing shipped-config test, check it with a mutation against the old config, update config
  and docs, run the full affected tests, pre-commit, stage.
