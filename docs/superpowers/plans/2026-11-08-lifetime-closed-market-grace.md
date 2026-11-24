# Lifetime closed-market grace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed lifetime evidence read on a filled timed position does not count toward the review halt while the regular session is certainly closed.

**Architecture:**
- A pure static-calendar predicate in `market/session.py`.
- An injectable `market_closed` hook on `TradeLifetimeService`.
- A grace check in `reconcile()`'s `EvidenceReadFailed` branch that keeps the prior count instead of incrementing it.

**Tech Stack:** Python 3.14, pytest (asyncio), SQLAlchemy async (SQLite and PostgreSQL integration).

**Spec:** `docs/superpowers/specs/2026-11-08-lifetime-closed-market-grace-design.md`

## Global Constraints

- **No new config key and no migration.** Schema head stays `008_alpha_pipeline`. The review reason string stays `lifecycle_evidence_unavailable:{TypeName}`.
- **Logs carry exception type names only**, never messages. The existing `PLANTED` secret-text assertions must keep passing.
- **The grace never closes, cancels, halts or clears a halt.** It only decides whether a failed pass increments the count.
- **The failed type name is still appended** to `reconcile()`'s return value on a non-counted pass.
- **`resume_blockers` is unchanged.**
- **Test run time:** `uv run pytest` must not depend on the weekday or time it runs.
- **Before committing, run:**
  - `uv run pre-commit run --all-files`;
  - the full unit suite;
  - the PostgreSQL integration file with `--run-postgres` and `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`.
- **Implementers stage only.** The controller commits.

## Review Focus

1. **An entry fully filled at the broker but never journaled as filled:** it must still review at 5 while closed. The grace reads the journal, not the venue.
2. **A partially filled entry** (projection `partially_filled`, or `filled_quantity < quantity`): it is not "fully filled", so the grace must not apply.
3. **The boundaries:** 09:30:00 ET is open and 16:00:00 ET is closed. A UTC-aware `now` is converted to New York time before the date and time tests, and DST must not shift the result.
4. **A non-counted pass with prior 0:** `_evidence_failures` is left without the key, so the existing `== {}` assertions hold.
5. **The grace lookup raising** (projection read error): the pass counts, and the log shows the type name only.

---

### Task 1: Closed-market grace for filled timed positions

**Files:**
- Modify `agentic_trader/market/session.py`: add `regular_session_certainly_closed` after `DeterministicCalendarProvider`.
- Modify `agentic_trader/execution/lifetimes.py`:
  - the constructor;
  - a new `_closed_market_grace`;
  - `reconcile()`'s `except EvidenceReadFailed` branch and its docstring.
- Test `tests/market/test_regular_session_closed.py` (new).
- Test `tests/integration/test_trade_lifetimes.py`: the fixture, plus new tests after `test_threshold_one_reviews_on_first_failure`.
- Docs:
  - `docs/production.md`: the "A failed evidence read of a timed position…" paragraph in "Timed alpha lifecycle operations";
  - `docs/alpha-trade-lifetimes.md`: the paragraph ending "A `REVIEW` decision still halts immediately.";
  - `CLAUDE.md`: the "Timed alpha execution" paragraph, the sentence beginning "A failed evidence read is retried…";
  - `config/config.yaml`: extend the `lifetime_evidence_review_after_failures` comment by one clause.

**Interfaces:**
- **Produces** `def regular_session_certainly_closed(now: datetime) -> bool` in `agentic_trader.market.session`:
  - a naive `now` returns `False`;
  - otherwise, with `et = now.astimezone(ET_TZ)`, it returns `True` iff `et.weekday() >= 5`, or `MarketHolidayCalendar.is_equity_holiday(et.date())[0]`, or `not (EQUITY_RTH_OPEN <= et.time() < EQUITY_RTH_CLOSE)`;
  - early closes are ignored on purpose.
- **Produces** a `TradeLifetimeService.__init__` keyword `market_closed: Callable[[datetime], bool] = regular_session_certainly_closed`, stored as `self.market_closed`.
- **Produces** `async def _closed_market_grace(self, signal: dict[str, Any]) -> bool` on `TradeLifetimeService`. It returns `True` iff all of these hold:
  - `signal.get("broker_order_id")` is truthy;
  - `self.market_closed(self.clock())` is `True`;
  - `parent = await self.repository.order(signal["broker_order_id"])` is not `None`;
  - `parent.status == "filled"`;
  - `Decimal(parent.quantity) > 0`;
  - `Decimal(parent.filled_quantity) == Decimal(parent.quantity)`.

  Any exception returns `False`, logging a warning with `type(exc).__name__` only.
- **Changes** `reconcile()`'s `except EvidenceReadFailed as exc:` branch:
  - It still appends `name` to `failed`.
  - If `await self._closed_market_grace(signal)`, it puts `prior` back (only when `prior > 0`), and logs a warning: `"Lifetime evidence read failed for signal %s (%s) while the regular session is closed; the entry is filled, pass not counted (%d of %d)"` with `signal["id"], name, prior, threshold`. It does not review.
  - Otherwise it runs today's increment/review code unchanged.

- [ ] **Step 1: Write the failing predicate tests** in `tests/market/test_regular_session_closed.py`. Use `ZoneInfo("America/New_York")`. One parametrized test, `test_regular_session_certainly_closed(when, expected)`:

```python
NY = ZoneInfo("America/New_York")
CASES = [
    (datetime(2026, 11, 7, 12, 0, tzinfo=NY), True),  # Saturday
    (datetime(2026, 11, 8, 12, 0, tzinfo=NY), True),  # Sunday
    (datetime(2026, 11, 9, 9, 29, 59, tzinfo=NY), True),  # Monday before the open
    (datetime(2026, 11, 9, 9, 30, tzinfo=NY), False),  # the open
    (datetime(2026, 11, 9, 15, 59, 59, tzinfo=NY), False),
    (datetime(2026, 11, 9, 16, 0, tzinfo=NY), True),  # the close
    (datetime(2026, 11, 26, 12, 0, tzinfo=NY), True),  # Thanksgiving
    (datetime(2026, 11, 27, 14, 0, tzinfo=NY), False),  # Black Friday early close: ignored, reads open
    (datetime(2026, 10, 12, 12, 0, tzinfo=NY), False),  # Columbus Day: NYSE open
    (datetime(2026, 11, 9, 14, 30, tzinfo=UTC), False),  # 09:30 EST from UTC
    (datetime(2026, 11, 9, 14, 29, tzinfo=UTC), True),  # 09:29 EST from UTC
    (datetime(2026, 7, 6, 13, 30, tzinfo=UTC), False),  # 09:30 EDT from UTC
    (datetime(2026, 11, 9, 12, 0), False),  # naive: never "certainly closed"
]
```

- [ ] **Step 2: Run** `uv run pytest tests/market/test_regular_session_closed.py -q`. It is expected to fail with an ImportError.

- [ ] **Step 3: Implement `regular_session_certainly_closed`.** Write a docstring that says why early closes are ignored and why a naive input is `False`.

- [ ] **Step 4: Run Step 2 again.** It is expected to pass.

- [ ] **Step 5: Pin the existing integration fixture to an open market.** In `lifecycle_case`, construct each `TradeLifetimeService` with `market_closed=lambda now: False`. Add a comment saying the closed-market grace has its own tests, and that this keeps every other test independent of the weekday.

- [ ] **Step 6: Write the failing service tests** in `tests/integration/test_trade_lifetimes.py`. Reuse the existing helpers: `use_session_policy`, `filled_position`, `group_read_fails`, `lifetime_reviews`, `PLANTED`. Set `service.market_closed = lambda now: True/False` per phase.
  - `test_filled_position_read_failures_while_closed_never_review(caplog)`:
    - Setup: `use_session_policy(c, fill_days_ago=18)` (filled, waiting), then one successful `services[0].reconcile()` so the filled projection is journaled.
    - Closed phase: `group_read_fails(c, 100)` with the market closed. Ten sweeps each return `("ConnectionError",)`; there is no review, `TRADING_HALTED != "true"`, `c.signal_id not in service._evidence_failures`, and there is no non-GET call.
    - Open phase: four more sweeps give no review, and the fifth reviews once with `lifecycle_evidence_unavailable:ConnectionError` and a halt.
    - `PLANTED not in caplog.text`.
  - `test_the_count_carries_across_a_closed_window`:
    - Same setup. Three open failing sweeps, then `_evidence_failures[c.signal_id] == 3`.
    - Ten closed sweeps leave it at 3, with no review.
    - Open again: the first open sweep makes 4 (no review), and the second reviews.
  - `test_a_resting_entry_still_reviews_while_closed`: the fixture's resting entry with the market closed. `assert_review_only_at_threshold`-style: four sweeps with no review, then the fifth reviews.
  - `test_the_grace_needs_a_journaled_fill`:
    - `filled_position(c)` with no successful sweep first, so nothing is journaled.
    - Market closed, `group_read_fails(c, 100)`.
    - Five sweeps, then the review.
  - `test_a_failed_clock_read_at_a_passed_deadline_while_closed_is_not_counted`:
    - `filled_position(c)`, the market closed, and `c.broker.regular_session_open` raising `ConnectionError(PLANTED)`.
    - Ten sweeps give no review, no halt, and no non-GET call.
  - `test_a_failed_grace_lookup_counts_the_pass(caplog, monkeypatch)`:
    - Setup as in the first test (journaled fill), then the market closed and `group_read_fails(c, 100)`.
    - Monkeypatch `service.repository.order` to raise `RuntimeError(PLANTED)`.
    - The fifth sweep reviews.
    - `PLANTED not in caplog.text`, and `"RuntimeError"` appears in the log.
  - `test_default_market_closed_is_the_static_calendar`: a `TradeLifetimeService(...)` built without `market_closed` has `service.market_closed is regular_session_certainly_closed`.

- [ ] **Step 7: Run** `uv run pytest tests/integration/test_trade_lifetimes.py -q`. The new tests are expected to fail and the existing ones to pass.

- [ ] **Step 8: Implement** the constructor keyword, `_closed_market_grace` and the `reconcile()` branch, per Interfaces. Update the `reconcile()` docstring with one sentence on the closed-market grace.

- [ ] **Step 9: Run** the integration file (SQLite), the same file with `--run-postgres`, then the full `uv run pytest -q`. All are expected to pass.

- [ ] **Step 10: Docs.**
  - `docs/production.md`: one sentence in the failed-evidence paragraph stating the rule (filled entry per the journal, regular session certainly closed per the static exchange calendar, early closes read as open; the count holds and readiness still degrades), and the incident note "Incident 2026-11-07: an Alpaca `ReadTimeout` outage on a Saturday morning halted trading through the BLK probe".
  - `docs/alpha-trade-lifetimes.md` and `CLAUDE.md`: one clause each with the same rule.
  - `config/config.yaml`: extend the comment with "; passes while the regular session is closed do not count for a filled entry".

- [ ] **Step 11:** Run `uv run pre-commit run --all-files`, stage every changed file (`git add` with explicit paths), and report. Do not commit.
