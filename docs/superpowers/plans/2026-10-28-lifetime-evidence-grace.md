# Lifetime Evidence Grace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed broker/calendar evidence read in the trade-lifetime sweep is retried on the next 60 s sweep and only becomes a lifetime review (trading halt) after N consecutive failed sweeps for that signal; every failed sweep degrades `reconciliation` readiness.

**Architecture:** `TradeLifetimeService.reconcile()` keeps an in-memory per-signal consecutive-failure count, reviews at the configured threshold with today's reason string, and returns the failed sweeps' exception type names; `TradingCopilot._monitor_positions` folds them into the reconciliation readiness detail it already builds.

**Tech Stack:** Python 3.14, uv, pydantic, SQLAlchemy async, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-lifetime-evidence-grace-design.md` (binding).

## Global Constraints

- New field `ExecutionConfig.lifetime_evidence_review_after_failures: int = Field(default=5, ge=1, le=60)`; shipped in `config/config.yaml` under `execution:` as `lifetime_evidence_review_after_failures: 5` with a short comment; reaches the service through the existing `config: ExecutionConfig`.
- Review reason string unchanged: `f"lifecycle_evidence_unavailable:{type(exc).__name__}"`; review via the existing `self.repository.review(signal["id"], reason, {})`.
- Count unit is sweeps (calls of `reconcile()`), never wall-clock time.
- `reconcile()` returns `tuple[str, ...]` (type names of this sweep's failed signals, in loop order); `_monitor_positions` extends `self._reconciliation_errors` with `f"trade_lifetimes:{name}"` for each. The existing `except Exception` around `reconcile()` stays.
- Type names only in logs' `extra`, readiness detail and journal; `logger.warning` may carry the type name and the count, never `str(exc)`.
- Unchanged: `resume_blockers`, every deterministic `review(...)` call (REVIEW decisions, `missing_durable_entry_identity`, bracket refusals, partial exit, failed holding close), `recover()`, `_missed_native_bound` (it already swallows), the entry FIFO halts.
- No migration. Imports at module top; no `dict()` calls. Tests: temp SQLite, blocked network, `test_*` PostgreSQL only.

## Review Focus

1. **Sleep between failures:** two failed sweeps separated by hours plus a success must not review (the count is per sweep). Covered by the 4-fail-then-success test, which uses no clock.
2. **Two signals in one sweep:** a failing signal must not stop the healthy one from being reconciled in the same sweep (the loop continues), and only the failing one counts.
3. **Restart:** a new `TradeLifetimeService` starts at zero (in-memory); documented, no test needed beyond construction.
4. **Existing calendar-failure integration tests** must keep "never closes" and the `resume_blockers` block while moving the review to the Nth sweep.
5. **Readiness detail** carries `trade_lifetimes:ConnectionError` and never the planted exception message.

---

### Task 1: Retry evidence reads before a review halt

**Files:**
- Modify: `agentic_trader/config.py` (`ExecutionConfig`, ~L673-697), `config/config.yaml` (`execution:` ~L361), `agentic_trader/execution/lifetimes.py` (`TradeLifetimeService.__init__` ~L262-278, `reconcile` ~L385-404), `agentic_trader/agent/copilot.py` (`_monitor_positions` ~L3020-3031)
- Docs: `CLAUDE.md` ("Timed alpha execution" paragraph), `docs/alpha-trade-lifetimes.md` (session-calendar bullet ~L86-96), `docs/production.md` ("Timed alpha lifecycle operations" ~L1350)
- Test: `tests/integration/test_trade_lifetimes.py` (update the two `..._calendar..._failure_reviews_and_never_closes` tests; add new tests beside them using the `lifecycle_case` fixture), the copilot readiness test file that already covers `trade_lifetimes:` errors if one exists (else a new `tests/agent/test_lifetime_readiness.py`), the shipped-config test file (`tests/config/test_shipped_config.py`)

**Interfaces:**
- Produces: `ExecutionConfig.lifetime_evidence_review_after_failures`; `TradeLifetimeService._evidence_failures: dict[int, int]`; `async def reconcile(self) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**
  - `test_failed_evidence_reads_retry_before_review`: the broker's group read (or the calendar) raises `ConnectionError("planted secret text")` for 4 sweeps (`lifetime_evidence_review_after_failures=5`), then succeeds: after each failing sweep `reconcile()` returns `("ConnectionError",)`, no `LIFETIME_REVIEW` event, halt not `"true"`; after the success it returns `()` and a further 4 failures still do not review (count reset).
  - `test_persistent_evidence_failure_reviews_at_threshold`: 5 consecutive failing sweeps → exactly one `LIFETIME_REVIEW` with reason `lifecycle_evidence_unavailable:ConnectionError` and `TRADING_HALTED == "true"`; none after sweep 4.
  - `test_threshold_one_reviews_on_first_failure`: `=1` reviews on the first failed sweep (today's behaviour).
  - `test_failing_signal_does_not_block_healthy_signal`: two timed signals, one failing; the healthy one's deadline action still happens in the same sweep and only the failing id is counted (return value has one name).
  - `test_deterministic_review_still_halts_first_sweep`: a `missing_durable_entry_identity` (or REVIEW decision) case halts on sweep 1 regardless of the threshold.
  - `test_monitor_records_lifetime_failures_in_readiness`: `_monitor_positions` with a lifetime service whose `reconcile` returns `("ConnectionError",)` observes `reconciliation` not ready with detail containing `trade_lifetimes:ConnectionError`; with `()` the detail has no `trade_lifetimes:` entry.
  - Planted text `"planted secret text"` absent from the review event payload, readiness detail and captured log records' `extra`.
  - Shipped config: `load_config(...).execution.lifetime_evidence_review_after_failures == 5`; `ExecutionConfig()` default 5; `0` and `61` rejected.
  - Update the two existing calendar-failure tests: sweeps 1..4 → no review (still `resume_blockers` blocks, still no non-GET call); sweep 5 → the review with `lifecycle_evidence_unavailable:RuntimeError`.
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/integration/test_trade_lifetimes.py tests/config -q` (new tests fail).
- [ ] **Step 3: Implement** per the spec §3; drop ids no longer in the active set from `_evidence_failures` each sweep; reset on a sweep with no exception for that signal. Then the three doc edits per spec §6.
- [ ] **Step 4: Run** `uv run pytest tests/integration/test_trade_lifetimes.py tests/agent tests/config tests/execution -q`, then the full `uv run pytest`, `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration --run-postgres -q`, and `uv run pre-commit run --all-files`.
- [ ] **Step 5: Stage** (`git add` the changed files; do not commit).
