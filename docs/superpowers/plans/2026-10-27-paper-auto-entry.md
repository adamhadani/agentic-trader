# Paper Auto-Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Alpaca paper account, every native card sent by a scheduled suggestion scan is entered automatically through the same `execute_signal_by_id` path a tap uses, with operator vetoes, quieter notices and a daily digest, and `cards outcomes` measures it.

**Architecture:** A provenance marker (`auto_entry`) on eligible cards, written at `record_signal`; an idempotent database sweep (`auto_enter_pending`) that calls `execute_signal_by_id(..., trigger=EntryTrigger.AUTO)` and journals one `auto_entry_attempted` event per card; card rendering by status with a button swap (`CARD_AUTO_ENTERED`); auto-triggered entries skip four routine notices, gain a fill notice and a daily digest; `cards outcomes` reads the trigger tag.

**Tech Stack:** Python 3.14, uv, pydantic, SQLAlchemy async (PostgreSQL in production, SQLite in tests), APScheduler, python-telegram-bot, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-paper-auto-entry-design.md` (binding; read it with this plan).

## Global Constraints

- No migration: schema head stays `008_alpha_pipeline`. New state lives in existing JSON payload columns, journal events and outbox rows.
- POST/PATCH/DELETE are never retried automatically. Auto-entry makes **one** attempt per card; no retry of any kind.
- There is exactly one entry path: `execute_signal_by_id` → `EntryExecutionService.authorize` → `enqueue_entry`. No risk rule, admission check, FIFO or preflight token changes.
- Telegram text, readiness details and journal reasons never carry exception text: exception **type names** only. Desk-authored refusal sentences may be journaled (stripped of markup, ≤300 chars).
- Contract 3: every confirmed trailing-stop change keeps its own notice. Do not batch ratchets.
- Contract 6: a business transition and its notification intent share one transaction.
- Tests: explicit fixture config, temporary SQLite, stripped credentials, blocked network I/O; PostgreSQL only via `--run-postgres` on `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`. Never relax these guards.
- `uv run pre-commit run --all-files` must pass (ruff C408/PLC0415 strict, mypy). Imports at module top.
- Config keys go through `load_config` with a shipped-config test.
- Exact values: `execution.auto_entry: Literal["off", "paper"]`, code default `"off"`, shipped `paper`; marker key `"auto_entry"`, marker value `{"mode": "paper"}`; payload `auto_entry` values `"placing" | "refused" | "abandoned"`; `EntryTrigger.TAP = "tap"`, `EntryTrigger.AUTO = "auto"`; `EventKind.AUTO_ENTRY_ATTEMPTED = "auto_entry_attempted"`; `NotificationKind.CARD_AUTO_ENTERED = "card_auto_entered"`; attempt outcomes `entered | refused | skipped_not_pending | abandoned`; keys `auto-entry/{signal_id}`, `signal/{id}/auto-refused`, `signal/{id}/auto-entered`, `signal/{id}/auto-filled`, `auto-entry-digest/{et_date}`; callback `cancelentry_ask:{signal_id}`; digest at session close + 15 min; abandon after `execution.card_freshness.fresh_seconds`.
- Copy (verbatim): auto line "🤖 Paper auto-entry: entered for you on the paper account; no tap needed. Unfilled entry is cancelled at the session close."; refused line "🤖 Auto-entry refused: {reason}. Tap Execute to enter it yourself."; abandoned line "🤖 Auto-entry was not placed in time. Tap Execute to enter it yourself."; fill notice "🤖 Auto-entry #{id} {symbol} filled {qty} @ ${price}; stop ${stop}, target ${target}."; digest footer "Paper fills are optimistic: no queue position or market impact."; button label "Cancel entry…".

## Review Focus

1. **A card delivered after its entry was already rejected** (`FAILED`) or expired: it must render with the "any other status" rule (no Dismiss, no Cancel entry…), never a stale "no tap needed" Dismiss. Test in Task 2.
2. **Dismiss tapped while the sweep is mid-call:** exactly one of `dismissed` / `entered` wins; never an entry on a dismissed card, never two entries. Test in Task 3 (dismiss between selection and the call → `skipped_not_pending`, no work item).
3. **The button swap reaches a card that was never delivered** (no `telegram_message_id`): acknowledged without calling Telegram; the late delivery renders Cancel entry… from the status. Test in Task 2.
4. **An early-close session** (13:00 close): the digest is due at 13:15 New York, not 16:15. Test in Task 4 with a calendar fixture.
5. **After a re-card, the stored `telegram_message_id` points at the re-card**, so the card-expiry strike hits the re-card's Execute/Dismiss; the original card's Dismiss still dismisses a `PENDING` card correctly and is harmless otherwise. Test in Task 3 (message id after re-card delivery; strike targets it).

---

### Task 1: Switch, gate and trigger plumbing

**Files:**
- Modify: `agentic_trader/config.py` (`ExecutionConfig`, `load_config`)
- Modify: `config/config.yaml` (execution block: `auto_entry: paper`)
- Modify: `agentic_trader/execution/durable.py` (`EntryTrigger`, `EventKind.AUTO_ENTRY_ATTEMPTED`, `NotificationKind.CARD_AUTO_ENTERED`)
- Create: `agentic_trader/execution/auto_entry.py`
- Modify: `agentic_trader/execution/entries.py` (`authorize`)
- Modify: `agentic_trader/storage/workflow.py` (`enqueue_entry`, new `entry_trigger_of`)
- Modify: `agentic_trader/agent/copilot.py` (`execute_signal_by_id`, `_journal_card_tap` payload)
- Test: `tests/config/test_shipped_config.py`, `tests/execution/test_auto_entry_gate.py`, `tests/agent/test_entry_trigger.py`

**Interfaces:**
- Produces:
  - `EntryTrigger(StrEnum)`: `TAP = "tap"`, `AUTO = "auto"` in `execution/durable.py`.
  - `AUTO_ENTRY_KEY = "auto_entry"`, `AUTO_ENTRY_MARKER = {"mode": "paper"}` in `execution/auto_entry.py`.
  - `auto_entry_active(config: AppConfig) -> bool` (true iff `auto_entry == "paper"`, `execution_mode == "alpaca"`, `alpaca_paper`, `execution.native_entry_lifetime == "session"`).
  - `auto_entry_marker(config: AppConfig, *, trigger: ScanTrigger, is_catalog: bool, alpha: bool, lifetime_marker: dict | None) -> dict | None` returns `dict(AUTO_ENTRY_MARKER)` iff active, `trigger == SUGGESTION_SCAN`, not catalog, not alpha, lifetime marker present.
  - `EntryExecutionService.authorize(request, *, trigger: EntryTrigger = EntryTrigger.TAP)`.
  - `WorkflowStore.enqueue_entry(request, config, *, trigger: EntryTrigger = EntryTrigger.TAP)` stores `"entry_trigger": str(trigger)` in the work item payload and in the `entry_queued` event payload.
  - `WorkflowStore.entry_trigger_of(session: AsyncSession, signal_id: int) -> EntryTrigger | None` reads the latest entry work item (`kind == ENTRY`, `dedup_key == str(signal_id)`) payload; a payload without the key reads as `TAP`.
  - `TradingCopilot.execute_signal_by_id(signal_id, quantity=None, *, trigger: EntryTrigger = EntryTrigger.TAP) -> ExecutionReply`; the `card_tap_assessed` payload gains `entry_trigger`.

- [ ] **Step 1: Write the failing tests**
  - `test_shipped_config_enables_paper_auto_entry`: `load_config(str(SHIPPED), environ=PRODUCTION).execution.auto_entry == "paper"` and `"auto_entry" in ...model_fields_set`.
  - `test_auto_entry_defaults_off`: `ExecutionConfig().auto_entry == "off"`; `ExecutionConfig(auto_entry="live")` raises `ValidationError`.
  - `test_load_config_refuses_auto_entry_on_live_alpaca` and `test_load_config_refuses_auto_entry_without_session_lifetime`: `load_config` with `auto_entry: paper` raises (`ValueError` or `ValidationError`) for `EXECUTION_MODE=alpaca`, `ALPACA_PAPER=false`; and for `native_entry_lifetime: off`.
  - `test_auto_entry_active_truth_table` (parametrized): simulator → False; alpaca paper + session → True; alpaca paper + lifetime off (constructed directly, bypassing load_config) → False; `auto_entry: off` → False.
  - `test_auto_entry_marker_only_for_native_suggestion_cards` (parametrized over swing/operator trigger, catalog, alpha, missing lifetime marker → `None`; native suggestion → `{"mode": "paper"}`).
  - `test_tap_journals_tap_trigger` / `test_auto_trigger_journals_auto`: after `execute_signal_by_id(id)` the `entry_queued` payload and the work item payload have `entry_trigger == "tap"`; with `trigger=EntryTrigger.AUTO` both are `"auto"`, and the `card_tap_assessed` payload carries the same value.
  - `test_entry_trigger_of_reads_latest_entry_and_defaults_tap`.
- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/config/test_shipped_config.py tests/execution/test_auto_entry_gate.py tests/agent/test_entry_trigger.py -q` → FAIL (missing names).
- [ ] **Step 3: Implement** the interfaces above. The `load_config` refusal goes in `load_config` after `AppConfig` is built (it needs `execution_mode`/`alpaca_paper`); keep `ExecutionConfig` validation to the `Literal`. Thread `trigger` through `execute_signal_by_id` → `authorize` → `enqueue_entry` without changing any other argument or check.
- [ ] **Step 4: Run the tests to verify they pass**, plus `uv run pytest tests/agent tests/execution tests/storage tests/config -q`.
- [ ] **Step 5: Stage** (the controller commits).

---

### Task 2: Card rendering, the button swap and `cancelentry_ask`

**Files:**
- Modify: `agentic_trader/notifier/telegram_bot.py` (`format_alert_card`, `format_terminal_card`, `send_signal_alert`, callback router, new `mark_auto_entered_card`)
- Modify: `agentic_trader/notifier/outbox.py` (`_deliver` for `CARD_AUTO_ENTERED`)
- Test: `tests/notifier/test_auto_entry_card.py`

**Interfaces:**
- Consumes: `NotificationKind.CARD_AUTO_ENTERED` (Task 1).
- Produces:
  - `send_signal_alert(..., auto_entry: str | None = None, auto_refusal: str | None = None)` and the same two keywords on `format_alert_card` / `format_terminal_card`. Every payload key Task 3 writes must bind to `send_signal_alert`.
  - `TelegramNotifier.mark_auto_entered_card(signal_id: int, contract: str) -> str | bool`, the `CARD_AUTO_ENTERED` delivery; payload `{"signal_id", "contract"}`.
  - Callback `cancelentry_ask:{signal_id}` → `cancel_handler(signal_id, False)` reply with the 1.3 preview buttons.
  - Constants `AUTO_ENTRY_LINE`, `AUTO_REFUSED_LINE` (format string with `{reason}`), `AUTO_ABANDONED_LINE`, `CANCEL_ENTRY_ASK_LABEL = "Cancel entry…"`, copy verbatim from Global Constraints.

- [ ] **Step 1: Write the failing tests** (one per row of the spec's 2.3 table, plus the swap):
  - `test_placing_card_pending_shows_auto_line_and_only_dismiss`: card text contains `AUTO_ENTRY_LINE`; keyboard is exactly `[["❌ Dismiss Signal" → dism_{id}]]`; no `exec_` callback.
  - `test_placing_card_delivered_after_entry_shows_cancel_entry` (status `SUBMITTING` and `EXECUTED`): keyboard is exactly one `Cancel entry…` → `cancelentry_ask:{id}`.
  - `test_refused_recard_shows_reason_and_normal_buttons`: text contains `AUTO_REFUSED_LINE.format(reason=...)` with the reason HTML-escaped; keyboard equals the normal Execute/Dismiss keyboard for the same card.
  - `test_abandoned_recard_line`.
  - `test_auto_card_failed_or_expired_renders_like_today` (Review Focus 1): status `FAILED` → no buttons; `EXPIRED` → Re-evaluate as today; neither shows Dismiss.
  - `test_mark_auto_entered_swaps_keyboard`: `edit_message_reply_markup` called with the stored message id and the single `Cancel entry…` button.
  - `test_mark_auto_entered_without_message_id_is_acknowledged` (Review Focus 3): returns a truthy string, Telegram not called; and the `BadRequest` "message is not modified" / "message to edit not found" cases acknowledged; any other exception propagates.
  - `test_cancelentry_ask_runs_the_preview`: callback calls `cancel_handler(id, False)` and replies with the 1.3 `Cancel entry` / `Keep` buttons; unauthorized users are refused as for other callbacks.
  - `test_outbox_delivers_card_auto_entered`.
  - `test_watch_card_still_says_tap_only` (pin the existing "tap-only, never auto-entered" text).
- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/notifier/test_auto_entry_card.py -q`.
- [ ] **Step 3: Implement.** Keyboard selection in `send_signal_alert` reads the status it already reads; add the `auto_entry` branches before the existing PENDING branch. `mark_auto_entered_card` mirrors `strike_expired_card` (same message-id lookup and acknowledgement rules).
- [ ] **Step 4: Run** the new file plus `tests/notifier -q`.
- [ ] **Step 5: Stage.**

---

### Task 3: Marker at record time, the sweep, the journal and the jobs

**Files:**
- Modify: `agentic_trader/agent/copilot.py` (`run_scan` record site ~L1739-1815; new `auto_enter_pending`, `_auto_entry_lock`)
- Modify: `agentic_trader/storage/workflow.py` (new `pending_auto_entries`, `record_auto_entry_attempt`)
- Modify: `agentic_trader/cli/commands/service.py` (`make_suggestion_scan` `finally`; new `register_auto_entry_jobs`; call it in `daemon`)
- Test: `tests/agent/test_auto_entry_sweep.py`, `tests/cli/test_service_auto_entry.py`, `tests/integration/test_auto_entry.py` (PostgreSQL)

**Interfaces:**
- Consumes: Task 1 (`auto_entry_marker`, `EntryTrigger`, `EventKind.AUTO_ENTRY_ATTEMPTED`, `NotificationKind.CARD_AUTO_ENTERED`), Task 2 (payload keywords).
- Produces:
  - At `record_signal`: `decision_provenance["auto_entry"] = auto_entry_marker(...)` when not None, and the card payload gains `auto_entry="placing"`.
  - `@dataclass(frozen=True) AutoEntryAttempt`: `signal_id: int`, `scan_id: str | None`, `outcome: Literal["entered","refused","skipped_not_pending","abandoned"]`, `reason: str | None`, `work_status: str | None`, `signal_status: str`, `attempted_at: datetime` (in `execution/auto_entry.py`).
  - `WorkflowStore.pending_auto_entries(et_date: date) -> list[dict]`: today's (New York) signals with the marker, status `PENDING`, and no `auto-entry/{id}` event, ordered by `created_at`, then `decision_provenance["rank"]`.
  - `WorkflowStore.record_auto_entry_attempt(attempt: AutoEntryAttempt, *, recard: dict | None, entered: bool) -> None`: one transaction appending the event (key `auto-entry/{signal_id}`, idempotent) plus either the `signal/{id}/auto-refused` SIGNAL notification (`recard` = original card payload + `auto_entry` + `auto_refusal`) or the `signal/{id}/auto-entered` `CARD_AUTO_ENTERED` notification. The original card payload is read from the `signal/{id}/created` outbox row.
  - `TradingCopilot.auto_enter_pending(now: datetime | None = None) -> list[AutoEntryAttempt]`, never raises; serialized by `self._auto_entry_lock = asyncio.Lock()`; re-checks `is_paper_scope(self.db.workflows.scope)` per card (refused, reason `not_paper_scope`).
  - `register_auto_entry_jobs(scheduler, copilot, config) -> None`: registers `auto_entry_sweep` (interval 1 min, `next_run_time=now`) only when `auto_entry_active(config)` (Task 4 adds the digest job here).

- [ ] **Step 1: Write the failing tests**
  - Marker: `test_suggestion_scan_marks_native_card` (provenance has `auto_entry == {"mode": "paper"}`, payload `auto_entry == "placing"`, and the payload binds to `send_signal_alert`); `test_no_marker_for_operator_scan_swing_drift_watch_alpha_futures_or_inactive` (parametrized); `test_repriced_card_drops_the_auto_marker` (the re-priced card built at `copilot.py` ~L3870 from the old provenance has no `auto_entry` key and its payload no `auto_entry`, so it renders as a tap card).
  - Sweep outcomes, one test per row of the spec's 2.2 table, each asserting the event payload and the follow-up (or its absence):
    - entered → `CARD_AUTO_ENTERED` queued, no re-card;
    - queue-time refusal leaving `PENDING` → `refused`, `auto-refused` SIGNAL queued with `auto_entry == "refused"` and the reason;
    - synchronous rejection → `FAILED`, `refused`, no re-card;
    - `REPRICE` / `MISSED` → `EXPIRED`, `refused` with reason `repriced` / `expired`, no re-card;
    - dismissed before the call → `skipped_not_pending`, no work item, no notification (Review Focus 2: dismiss after selection, before the call).
  - `test_sweep_calls_execute_once_per_card_with_auto_trigger_in_rank_order`; `test_sweep_is_idempotent` (second call finds nothing; a `SUBMITTING` signal without an event is not selected); `test_concurrent_sweeps_serialize`.
  - `test_card_older_than_fresh_seconds_is_abandoned_and_recarded` (`fresh_seconds` from config).
  - `test_exception_in_one_card_is_refused_with_type_name_and_next_card_runs` (reason equals the exception class name; no exception text).
  - `test_non_paper_scope_refuses_without_entry`.
  - `test_reason_is_stripped_and_capped_at_300`.
  - `test_recard_moves_message_id_and_expiry_strikes_it` (Review Focus 5).
  - Service: `test_suggestion_job_sweeps_in_finally_even_when_run_scan_raises`; `test_auto_entry_jobs_registered_only_when_active`.
  - Integration (PostgreSQL): `test_attempt_event_and_notification_share_a_transaction` and the idempotent event key under `--run-postgres`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** The outcome is derived from the signal's status re-read after `execute_signal_by_id` returns (spec table), never from the reply text. The sweep runs outside the scan lock.
- [ ] **Step 4: Run** the new tests, `tests/agent tests/cli tests/storage -q`, and the integration file with `--run-postgres`.
- [ ] **Step 5: Stage.**

---

### Task 4: Fill notice, folded notices and the daily digest

**Files:**
- Modify: `agentic_trader/storage/db.py` (`update_signal_execution`)
- Modify: `agentic_trader/storage/workflow.py` (resolution notice ~L698-709; `entry-terminal` notice ~L871-877)
- Modify: `agentic_trader/storage/lifetimes.py` (cancel intent ~L210-217; cancel result ~L315-328)
- Modify: `agentic_trader/agent/copilot.py` (new `publish_auto_entry_digest`)
- Create: `agentic_trader/presentation/auto_entry_digest.py` (`format_auto_entry_digest`)
- Modify: `agentic_trader/cli/commands/service.py` (`register_auto_entry_jobs` adds `auto_entry_digest`, interval 5 min)
- Test: `tests/storage/test_auto_entry_notices.py`, `tests/agent/test_auto_entry_digest.py`, `tests/integration/test_auto_entry.py` (extend)

**Interfaces:**
- Consumes: `WorkflowStore.entry_trigger_of` (Task 1); attempt events (Task 3).
- Produces:
  - In `update_signal_execution`: when `before.executed_at is None`, the new `executed_at` is set and `entry_trigger_of(...) == AUTO`, queue `signal/{id}/auto-filled` (`MESSAGE`, fill notice copy) in the same session/transaction.
  - The four folded notices are skipped when `entry_trigger_of(...) == AUTO` and, for the two lifetime notices, the cause is `deadline`; resolution is skipped only for status `accepted`. Journal events are unchanged.
  - `format_auto_entry_digest(rows: list[AutoEntryDigestRow], *, et_date: date) -> str` (one line per card in rank order, outcome wording from spec 2.4, footer verbatim). `AutoEntryDigestRow` is a frozen dataclass: `signal_id`, `symbol`, `outcome` (`filled | cancelled_unfilled | still_resting | refused | dismissed | cancelled_by_operator | abandoned`), `detail: str | None`.
  - `TradingCopilot.publish_auto_entry_digest(now: datetime | None = None) -> bool`: due when `now >= session_close(et_date) + 15 min` on a trading day (calendar the lifetime service uses); builds rows from the signals table, attempt events and entry/cancel work items; queues `auto-entry-digest/{et_date}` once; returns False (and journals nothing) when nothing to report.

- [ ] **Step 1: Write the failing tests**
  - `test_auto_fill_notice_queued_once_in_the_fill_transaction`; `test_tap_fill_has_no_fill_notice`.
  - `test_folded_notices_skipped_for_auto_and_kept_for_tap` (parametrized over the four notices); `test_operator_cancel_of_auto_entry_keeps_its_notices`; `test_rejected_resolution_of_auto_entry_still_notifies`; `test_ratchet_exit_unknown_partial_notices_unchanged_for_auto`.
  - Digest: `test_digest_lines_for_each_outcome` (exact strings); `test_digest_due_at_close_plus_15` (15:59 → not due, 16:15 → sent once, 16:20 → no second); `test_digest_early_close_due_at_1315` (Review Focus 4); `test_no_digest_without_auto_cards`; `test_digest_footer_verbatim`.
  - Integration (PostgreSQL): fill notice and signal update in one transaction; folded notices absent for an auto entry.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** new tests, `tests/storage tests/agent tests/execution -q`, integration with `--run-postgres`.
- [ ] **Step 5: Stage.**

---

### Task 5: `cards outcomes` measurement

**Files:**
- Modify: `agentic_trader/research/setups/execution_evidence.py` (`entry_trigger` column; `summarize_execution` split)
- Modify: `agentic_trader/research/setups/outcomes.py` (`operator_action` column; new `operator_block`; wire into `summarize`)
- Modify: `agentic_trader/cli/commands/cards.py` (print the block)
- Test: `tests/research/setups/test_operator_block.py`, `tests/research/setups/test_execution_evidence.py` (extend)

**Interfaces:**
- Consumes: `entry_queued.entry_trigger`, `card_tap_assessed.entry_trigger`, `auto_entry_attempted` events, the `auto_entry` marker.
- Produces:
  - Row columns `entry_trigger ∈ {"auto","tap","none"}` and `operator_action ∈ {"tapped","auto_entered","dismissed","cancelled","expired","auto_refused","abandoned","none"}`.
  - `summarize_execution(...)` returns `taps` and `auto_entries` counts separately; `mean_tap_age_seconds` over taps only.
  - `operator_block(frame: pd.DataFrame, *, seed: int = 0) -> dict` with keys `by_slot`, `by_trigger`, `by_strategy` (counts of sent/tapped/auto_entered/dismissed/cancelled/expired), `tap_latency` (`median_seconds`, `p90_seconds`, `n`), `kept_vs_vetoed` (`kept`, `vetoed`: `{n, mean_excess_r}`, `difference`, `ci90`, `excluded`: counts of `auto_refused` and `abandoned`, `status ∈ {"ok","insufficient"}`), `tap_era` (descriptive tapped vs untapped for unmarked cards). Excess R is the existing beta-matched column, de-duplicated per setup; CI is a session-block bootstrap (reuse the module's existing bootstrap helper).
  - `summarize(...)["operator"] = operator_block(...)`.

- [ ] **Step 1: Write the failing tests**
  - `test_entry_trigger_and_operator_action_per_row` (one fixture row per action).
  - `test_summarize_execution_keeps_taps_and_auto_apart`.
  - `test_kept_vs_vetoed_only_auto_marked_cards`; `test_refused_and_abandoned_excluded_and_counted`; `test_insufficient_below_30_setups_or_10_per_side`; `test_ci_is_session_block_bootstrap_and_seeded` (same seed → same CI).
  - `test_tap_latency_taps_only`; `test_tap_era_line_unmarked_cards_only`.
  - `test_cards_outcomes_prints_operator_block` (CLI).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** new tests and `tests/research tests/cli -q`.
- [ ] **Step 5: Stage.**

---

### Task 6: Visibility, bundled follow-ups and docs

**Files:**
- Modify: `scripts/verify_runtime.py` (503 handling; `auto_entry` field)
- Modify: `agentic_trader/data/insider_capture.py` (`freshness` signature/branch; caller passes status)
- Modify: `agentic_trader/execution/lifetimes.py` and `agentic_trader/presentation/formatters.py` (`not_resting` variants; missed-bound cache)
- Modify: `agentic_trader/presentation/formatters.py` and the status report builder (`/status` auto-entry line)
- Docs: `CLAUDE.md` (contract 1 sentence, verbatim intent from spec §4), `docs/production.md` (amend line ~43; runbook), `docs/paper-auto-entry.md` (new), `docs/card-evidence.md`, `docs/durable-execution.md`
- Test: `tests/scripts/test_verify_runtime_readiness.py`, `tests/data/test_insider_capture.py` (extend), `tests/execution/test_lifetimes.py` (extend), `tests/presentation/` or the existing status tests

**Interfaces:**
- Produces:
  - `verify_runtime`: `fetch_readiness(health_url: str, *, attempts: int = 3, delay_seconds: float = 2.0) -> dict` returns the parsed body; on persistent 503 raises `RuntimeError("Readiness is not ready: <check>: <detail>; ...")`; output JSON gains `"auto_entry": {"setting": "off|paper", "active": bool}`.
  - `InsiderCaptureWorker.freshness(now: datetime, status: WorkerStatus) -> tuple[bool, str]`: no capture and `status in ("waiting", "scan_window")` → `(True, "not_due; no capture yet")`; no capture otherwise → `(False, "no capture yet")`; other branches unchanged.
  - `not_resting` replies: dismissed/expired → "Signal #{id} was never entered ({status}); there is nothing to cancel."; still submitting → "Signal #{id} is still being submitted; try again in a minute."; the existing filled/closed/failed text otherwise.
  - Lifetime service: per-process set of signal ids whose missed-bound verdict is final (both verdicts).
  - `/status` line: "Auto-entry: paper (on)" / "Auto-entry: off" / "Auto-entry: inactive (not an Alpaca paper account)".

- [ ] **Step 1: Write the failing tests**
  - `test_verify_runtime_retries_503_then_succeeds` (fake server: 503, 503, 200); `test_verify_runtime_persistent_503_names_checks` (message contains each not-ready check and detail; no traceback text); `test_verify_runtime_reports_auto_entry`.
  - `test_insider_freshness_not_due_before_first_pass` (parametrized truth table: waiting/scan_window/captured/pending × capture present or not).
  - `test_not_resting_variants` (dismissed, expired, submitting, filled).
  - `test_missed_bound_verdict_cached_both_ways` (the order read happens once per signal per process, missed or not).
  - `test_status_shows_auto_entry_line` (three states).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement**, then write the docs: `docs/paper-auto-entry.md` holds the spec's contract (2.1–2.4), the measurement rules (2.5, including the predeclared kept-vs-vetoed rule) and the 20-session pass criteria verbatim; `docs/production.md` gains the runbook (turning it off: `execution.auto_entry: off` + controlled restart; card states; digest; vetoes); CLAUDE.md contract 1 gains one sentence per spec §4.
- [ ] **Step 4: Run** the full suite `uv run pytest -q`, integration `--run-postgres`, and `uv run pre-commit run --all-files`.
- [ ] **Step 5: Stage.**
