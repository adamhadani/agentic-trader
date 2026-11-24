# Session-bounded native entries and operator cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New native entries expire after one regular session through the existing lifetime service.
The operator can cancel any resting entry through the same durable cancel path.

**Architecture:**
- A config switch and a `decision_provenance["entry_lifetime"]` marker written when the signal is recorded.
- One resolver, `entry_lifetime_of`, replaces every direct read of `alpha_policy["lifetime"]` in the
  lifetime service and repository.
- `begin_cancel` gains `reason: Literal["deadline", "operator"]`.
- A copilot method plus a Telegram command and a CLI command drive the operator path.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy async (PostgreSQL and SQLite), python-telegram-bot, click, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-session-bounded-entries-design.md`

## Global Constraints

- Contract 1 and Contract 3 hold:
  - POST/PATCH/DELETE are never automatically retried;
  - recovery is by exact lookup;
  - partial or uncertain evidence halts new risk;
  - `/resume` cannot waive it.
- No migration (head `008`). No new queue or work kind: reuse `WorkKind.ENTRY_CANCEL`.
- Historical signals (recorded without the marker) keep their behaviour exactly. The existing
  `tests/integration/test_trade_lifetimes.py` "historical" case stays green, unchanged.
- Alpha and catalog-probe entries keep `alpha_policy.lifetime` and behave exactly as before.
- Telegram text and notices carry type names only, never `str(exc)`.
- Tests use the suite's isolation guards. PostgreSQL tests use only a `test_*` database
  (`TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`).
- ruff strict (no C408, no PLC0415), mypy clean, pre-commit. Implementers stage only.

## Review Focus

1. A card recorded before deploy but tapped after it. It has no marker, so it stays GTC and untimed;
   the marker is written at record time, never at tap time. Test in Task 1.
2. A marked entry that fills partially just before its deadline. This halts as for alpha entries,
   and no DELETE goes out with any fill. Test in Task 2, on the integration suite.
3. An operator cancel racing a deadline cancel on the same signal. One work item only
   (`dedup_key`); the second path gets a clean refusal or no-op, and one DELETE at most. Test in
   Task 3.
4. `/cancel` on a signal whose position is filled. It is refused with "not resting" and no DELETE,
   and `/close` stays the way to exit. Test in Task 3.
5. A signal under the paper simulator. It is never marked, and an operator cancel there is refused
   with a clear reason rather than a `NotImplementedError` halt. Test in Task 3.

---

### Task 1: Config switch, signal marker and explicit GTC

**Files:**
- Modify: `agentic_trader/config.py` (`ExecutionConfig.native_entry_lifetime`, and `load_config`
  passthrough if the block is copied field by field)
- Modify: `config/config.yaml` (`execution.native_entry_lifetime: session`)
- Modify: `agentic_trader/agent/copilot.py`
  - the native `record_signal` call site (~1641): write the marker into `decision_provenance`;
  - the native `OrderRequest` (~3354): `time_in_force=TimeInForce.GTC`;
  - the card text line (the presentation module that renders native cards).
- Test: `tests/config/test_shipped_config.py` and the existing signal-recording and entry tests
  (find them with `grep -rn "record_signal\|execute_signal_by_id" tests/agent | head`)

**Interfaces:**
- Produces:
  - `ExecutionConfig.native_entry_lifetime: Literal["off", "session"] = "off"`;
  - `NATIVE_ENTRY_LIFETIME_KEY = "entry_lifetime"`, a constant in `execution/lifetime_policy.py`;
  - `native_entry_lifetime_marker(config: AppConfig) -> dict | None` in `execution/lifetime_policy.py`.
    It returns `asdict(daily_entry_lifetime())` iff the switch is `session` and
    `config.execution_mode == "alpaca"`, else `None`.

- [ ] **Step 1: Write the failing tests:**
  - `test_shipped_config_bounds_native_entries` (the YAML gives `session`);
  - `test_native_entry_lifetime_defaults_off`;
  - `test_invalid_native_entry_lifetime_refused`;
  - `test_native_card_records_entry_lifetime_marker` (alpaca, session);
  - `test_no_marker_when_off_or_paper_mode`;
  - `test_alpha_and_catalog_cards_get_no_provenance_marker`;
  - `test_native_entry_order_is_explicit_gtc`;
  - `test_card_text_says_unfilled_entry_cancels`, only when marked.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and the affected suites, then stage.**

### Task 2: Resolver, and the lifetime service on marked native signals

**Files:**
- Modify: `agentic_trader/execution/lifetime_policy.py` (`entry_lifetime_of`)
- Modify: `agentic_trader/execution/lifetimes.py` (`reconcile`, `resume_blockers`)
- Modify: `agentic_trader/storage/lifetimes.py` (`begin_cancel`'s policy fence reads through the resolver)
- Test: `tests/execution/test_lifetimes.py`, `tests/integration/test_trade_lifetimes.py`

**Interfaces:**
- Consumes: Task 1's marker key.
- Produces: `entry_lifetime_of(signal: Mapping[str, Any]) -> LifetimePolicy | None`.
  - It reads `alpha_policy["lifetime"]` first, then `decision_provenance[NATIVE_ENTRY_LIFETIME_KEY]`.
  - It parses through `lifetime_from_dict`, and raises `ValueError` on a malformed marker.
  - `decision_provenance` may be a JSON string or a dict, the same as other provenance readers.

- [ ] **Step 1: Write the failing tests.**
  - Unit:
    - `test_resolver_prefers_alpha_policy`;
    - `test_resolver_reads_native_marker`;
    - `test_resolver_none_without_either`;
    - `test_resolver_rejects_malformed_marker`.
  - Integration, PostgreSQL and SQLite as the existing file does:
    - `test_marked_native_entry_cancelled_once_after_deadline`: signal FAILED, notice present, one
      DELETE;
    - `test_marked_native_entry_untouched_before_deadline`;
    - `test_partial_fill_on_marked_native_entry_halts`;
    - `test_resume_blockers_include_marked_native_signals`;
    - the existing historical case stays as it is.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Replace each `signal["alpha_policy"]["lifetime"]` read with
  `entry_lifetime_of(signal)`. Keep the exception-to-review path.
- [ ] **Step 4: Run `tests/execution`, then `tests/integration --run-postgres -k lifetime` on the
  `test_*` database. Stage.**

### Task 3: Operator cancel (service path, Telegram, CLI)

**Files:**
- Modify: `agentic_trader/storage/lifetimes.py`
  (`begin_cancel(..., reason: Literal["deadline", "operator"] = "deadline")`)
- Modify: `agentic_trader/execution/lifetimes.py`
  (`TradeLifetimeService.cancel_resting_entry(signal_id: int, *, confirm: bool) -> RestingCancelResult`)
- Modify: `agentic_trader/agent/copilot.py` (a thin `cancel_resting_entry` delegating to the service; wiring)
- Modify: `agentic_trader/notifier/telegram_bot.py`:
  - `/cancel` handler;
  - `cancelentry_confirm:<id>` and `cancelentry_keep` callbacks;
  - the BotCommand list and help text.
- Modify: `agentic_trader/cli/commands/trade.py` (`copilot cancel <signal_id> [--confirm]`)
- Test: `tests/execution/test_lifetimes.py`, `tests/integration/test_trade_lifetimes.py`, the Telegram
  handler tests, the CLI trade tests

**Interfaces:**
- Produces `RestingCancelResult`, a frozen dataclass with:
  - `status: Literal["preview", "submitted", "refused"]`;
  - `reason: str | None`, a stable token such as `unknown_signal`, `not_resting`,
    `cancel_in_progress`, `close_active`, `incomplete_or_changed_bracket_group`, `broker_cannot_cancel`;
  - `signal_id: int`;
  - `symbol: str | None`;
  - `work_status: str | None`.
- `begin_cancel(reason="operator")`:
  - skips the `CANCEL_ENTRY` assessment check and the policy-equality fence;
  - keeps every other fence;
  - writes the notice "Operator cancel requested for entry <symbol> (signal <id>)".
- The result notice names "operator" or "deadline".

- [ ] **Step 1: Write the failing tests:**
  - `test_preview_makes_no_delete`;
  - `test_confirm_cancels_once_with_notice`;
  - `test_refused_when_filled`;
  - `test_refused_when_close_active`;
  - `test_refused_unknown_signal`;
  - `test_second_confirm_is_refused_cancel_in_progress`;
  - `test_operator_cancel_of_untimed_historical_entry`;
  - `test_operator_and_deadline_cancel_race_single_work_item`, with one DELETE at most;
  - `test_paper_mode_refused_broker_cannot_cancel`;
  - `test_telegram_cancel_preview_and_buttons`;
  - `test_telegram_confirm_callback_runs_cancel`;
  - `test_cli_cancel_previews_without_confirm`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Reuse `_reconcile_signal`'s group checks, extracted into one helper used
  by both paths. Do not duplicate them.
- [ ] **Step 4: Run the affected suites and the integration lifetime tests. Stage.**

### Task 4: Docs

**Files:**
- `docs/alpha-trade-lifetimes.md`
- `docs/production.md`
- `docs/durable-execution.md`
- `CLAUDE.md` (contract 1, one sentence)

- [ ] **Step 1: Make the edits.** Follow spec §5. Note that native entries are bounded from deploy;
  signals recorded earlier are not.
- [ ] **Step 2: Run the full suite, the PostgreSQL integration suite, and
  `pre-commit run --all-files`. Stage.**
