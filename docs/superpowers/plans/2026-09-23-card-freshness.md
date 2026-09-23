# Card Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the operator taps a suggestion card, re-assess it with the current price, session and gates.
- A fresh card executes.
- A stale card that is still valid in the same session becomes a re-priced replacement card, which needs a fresh tap.
- A card whose setup has run or whose session is over is expired, with a one-tap re-evaluate.

**Architecture:**
- A pure `execution/freshness.py` decides the outcome.
- Storage gains a conditional expire and an atomic replace-with-outbox.
- `TradingCopilot.execute_signal_by_id` consults the decision before `EntryExecutionService.authorize`, which is itself unchanged.
- Telegram gains a `reval_` callback and card lines.

No migration.

**Tech Stack:** Python 3.14, pydantic, async SQLAlchemy, python-telegram-bot, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-card-freshness-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-freshness` (branch `feat/card-freshness`). Never read-write or run anything in `/Users/adamhadani/Development/agentic-trader`.
- Run everything as `env -u VIRTUAL_ENV uv run …`.
- There is no network in tests. Tests use temporary SQLite and the existing fixtures (`tests/conftest.py` `app_config`/`temp_db`, `tests/workflows/conftest.py`, `tests/notifier/test_telegram_interactive.py` patterns).
- No migration. Schema head stays 008. `SignalStatus.EXPIRED` already exists. `domain_events.kind` is a String column.
- Entry authorization contracts are unchanged:
  - an approved bracket is never mutated, and re-pricing always creates a new signal;
  - `EntryExecutionService`, admission, FIFO and fencing are untouched;
  - the freshness decision never authorizes an order by itself;
  - a trade handler is never replayed.
- Every card and every asynchronous message goes through the durable outbox, in the same transaction as the state change it reports.
- With `execution.card_freshness.enabled: false`, taps behave byte-for-byte as today.
- Use TDD: write the test, confirm RED, implement, confirm GREEN.
- Before reporting, run:
  - `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests`
  - `env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests`
  - the full `env -u VIRTUAL_ENV uv run pytest -q`
  - `env -u VIRTUAL_ENV uv run pre-commit run --all-files`

  Run all of them in the foreground.
- Implementers stage with `git add` and do NOT commit. No subagents.

---

### Task 1: Pure freshness assessment + config

**Files:**
- Create: `agentic_trader/execution/freshness.py`
- Modify: `agentic_trader/config.py`. Add `CardFreshnessConfig` and `ExecutionConfig.card_freshness: CardFreshnessConfig = Field(default_factory=CardFreshnessConfig)`.
- Modify: `config/config.yaml`. Add the block under `execution:`.
- Test: `tests/execution/test_card_freshness.py`. Also update any config-defaults test that enumerates execution settings; grep `signal_max_age_seconds` in `tests/`.

**Produces:**
```python
class CardFreshnessConfig(BaseModel):
    enabled: bool = True
    fresh_seconds: float = Field(default=1800, gt=0)
    fresh_max_r: float = Field(default=0.25, gt=0)
    reprice_min_risk_fraction: float = Field(default=0.5, gt=0, lt=1)

class CardOutcome(StrEnum):
    EXECUTE = "execute"; REPRICE = "reprice"; MISSED = "missed"; EXPIRED = "expired"

@dataclass(frozen=True)
class CardAssessment:
    outcome: CardOutcome
    reason: str                 # operator-facing, plain text
    r_consumed: float | None
    age_seconds: float
    price: float | None

def assess_card(
    *, direction: str, entry: float, stop: float, target: float,
    issued_at: datetime, valid_until: datetime | None, now: datetime,
    price: float, session_is_rth: bool, gate_reason: str | None,
    min_reward_risk: float, policy: CardFreshnessConfig,
) -> CardAssessment
```

**Rules, applied in this order:**
1. **Session over gives `EXPIRED`.** This is true when `valid_until` is set and `now >= valid_until`, OR `valid_until` is None and the NY date of `issued_at` ≠ the NY date of `now`, OR `not session_is_rth`. Reason: "Card expired: its session has ended."
2. `sign = +1 LONG / −1 SHORT`, `risk = abs(entry − stop)`, `r_consumed = sign*(price − entry)/risk`.
3. **Geometry breaks give `MISSED`,** with a specific reason for each:
   - at or beyond the stop: LONG `price <= stop`, SHORT `price >= stop`;
   - at or beyond the target;
   - remaining risk `sign*(price − stop)` < `reprice_min_risk_fraction * risk` ("too close to the stop");
   - `sign*(target − price) / (sign*(price − stop))` < `min_reward_risk` ("reward:risk at 161.28 is 1.6 < 2.0").
4. **A failing gate gives `MISSED`:** `gate_reason` is not None, and the reason is `gate_reason`.
5. **Fresh gives `EXECUTE`:** `age <= fresh_seconds` and `abs(r_consumed) <= fresh_max_r`.
6. **Otherwise `REPRICE`.** Reason: "Re-priced at {price}: {r_consumed:+.2f}R since the card."

Also produce:
```python
def reprice_quantity(*, original_quantity: float, entry: float, stop: float, new_entry: float,
                     multiplier: float, whole_units: bool) -> float
```
The new quantity keeps the original risk dollars: `original_quantity*abs(entry−stop) / abs(new_entry−stop)`. It is capped so that the new notional is at most the original notional (`original_quantity*entry`). It is floored to whole units when `whole_units`, and returns 0 when that gives less than 1 unit.

- [ ] **Step 1: Failing tests.** Table-driven:
  - each rule's boundary (exactly `fresh_seconds`, exactly `fresh_max_r`, exactly the minimum reward:risk passes, just below fails);
  - LONG and SHORT;
  - legacy `valid_until=None`, same NY date vs next date;
  - `session_is_rth=False`;
  - `gate_reason` beats `EXECUTE`;
  - `reprice_quantity`: same risk dollars, notional cap, flooring, zero.
- [ ] **Step 2:** RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** GREEN.
- [ ] **Step 5:** `git add`.

### Task 2: Storage — conditional expire, atomic replace, budget exclusion

**Files:**
- Modify: `agentic_trader/storage/db.py`
- Test: `tests/storage/test_signal_freshness.py`. Put it where the existing signal/db tests live; grep `dismiss_signal` in `tests/`.

**Produces** (async methods on the same class as `record_signal`/`dismiss_signal`):
- `expire_signal(signal_id: int) -> bool`: `UPDATE … SET status=EXPIRED WHERE id=? AND status=PENDING` within `_scope()`. Returns True iff one row changed. Mirror `dismiss_signal`.
- `replace_signal(old_signal_id: int, *, notification: dict, decision_provenance: dict, **new_signal_fields) -> int | None`. In **one transaction**:
  1. conditionally expire the old signal; if no row changed, return None and write nothing;
  2. insert the new signal row (same fields as `record_signal`, status PENDING);
  3. add its SIGNAL outbox notification exactly as `record_signal` does.

  Returns the new id. Refactor `record_signal` so the insert and notification logic is shared rather than duplicated (a private helper taking a session).
- `signals_since(cutoff)`: exclude rows whose `decision_provenance` JSON contains a non-null `reprices` key. Implement it portably for SQLite and PostgreSQL: select the column and filter in Python if the JSON is stored as text; check `SignalRecord.decision_provenance`'s type. Update the docstring: replacements replace a card rather than spending budget.

- [ ] **Step 1: Failing tests:**
  - expire PENDING → True and the status is EXPIRED;
  - expiring non-PENDING → False;
  - replace commits both rows and one outbox row;
  - replace when the old signal is not PENDING → None, no new row, no outbox row;
  - replace rolls back on an insert error (force it, e.g. with an invalid field), leaving the old signal PENDING;
  - `signals_since` excludes the replacement but includes the EXPIRED original.
- [ ] **Step 2:** RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** GREEN.
- [ ] **Step 5:** `git add`.

### Task 3: Copilot tap path, valid_until on cards, re-evaluate

**Files:**
- Modify: `agentic_trader/agent/copilot.py`
- Modify: `agentic_trader/cli/commands/trade.py`, the caller of `execute_signal_by_id`
- Test: `tests/agent/test_card_freshness_tap.py`, reusing fixtures from `tests/agent/test_scan_budget.py` and `tests/workflows/test_entries.py`

**Produces:**
```python
@dataclass(frozen=True)
class ExecutionReply:           # in agentic_trader/execution/freshness.py
    ok: bool
    text: str                   # HTML, as today
    offer_reevaluate: bool = False
```
`TradingCopilot.execute_signal_by_id(signal_id, quantity=None) -> ExecutionReply`. Update every caller: the Telegram `execute_handler` type and `cli/commands/trade.py`. No compatibility tuple.

**Behaviour in `execute_signal_by_id`,** after the existing halt, not-found and not-PENDING checks, and only when `config.execution.card_freshness.enabled`:
1. Fetch `price = await asyncio.to_thread(self.data_fetcher.fetch_latest_price, ticker)`. If it is None, non-finite or raises, return `ExecutionReply(False, "⚠️ Current price unavailable; try again shortly.")` and leave the signal PENDING.
2. Session: `info = await self.session_provider.get_session_info(contract)`. `session_is_rth = info.is_open and info.is_rth`.
3. `valid_until` comes from the signal's `decision_provenance["valid_until"]` (ISO), if present.
4. Gates, as one reason string or None, first failure wins:
   - `await self._entry_macro_check(request, sig)` (the existing macro, regime and reward:risk check; build the `OrderRequest` as today);
   - the earnings blackout for equities: `self.earnings_calendar.next_earnings` + `earnings_blackout_reason` with `config.risk.earnings_blackout_days`, failing open exactly like the evaluator.
5. Call `assess_card(...)` with `min_reward_risk = config.risk.min_risk_reward_ratio`.
6. Append one `card_tap_assessed` domain event (new `EventKind.CARD_TAP_ASSESSED = "card_tap_assessed"`):
   - `stream = f"card/{signal_id}"`, `key = f"card_tap_assessed/{signal_id}/{uuid4().hex}"`;
   - payload `{signal_id, outcome, reason, age_seconds, price, r_consumed, tapped_at}`;
   - use the same journal/lock pattern as `record_health`, contained so it never blocks.
7. Act on the outcome:
   - **`EXECUTE`:** continue into the existing path unchanged (`authorize`).
   - **`REPRICE`:**
     - `new_qty = reprice_quantity(...)`, where `whole_units` = equity. If it is 0, treat the card as MISSED ("re-priced size rounds to zero").
     - Else call `db.replace_signal(signal_id, …)`. It copies contract, strategy, timeframe, direction, stop, target, asset_class, alpha_version and alpha_policy from the old signal, with `entry_price=price`, `quantity=new_qty`, and risk, reward and notional recomputed.
     - `decision_provenance` = the old provenance plus `{reprices: signal_id, first_issued_at, tap_latency_seconds, r_consumed, valid_until}`, with the old `valid_until` carried over.
     - `notification` has the same shape as the scan's, with an `eval_res` rebuilt from the old `raw_response` with the new entry, quantity and numbers, plus `"reprices": signal_id` and `"first_issued_at"`.
     - Reply `ExecutionReply(False, "🔄 Card #N was X min old ({r:+.2f}R since). A re-priced card #M was sent; review it and tap again to trade.")`.
     - If `replace_signal` returns None, reply as for a not-PENDING signal.
   - **`MISSED` / `EXPIRED`:** `await self.db.expire_signal(signal_id)`, then `ExecutionReply(False, f"⌛ {reason}", offer_reevaluate=True)`. For `EXPIRED`, append the next regular open (from `info`) in UTC and NY time.

**`valid_until` on new cards:**
- In `run_scan`, when recording a signal, set `decision_provenance["valid_until"]` to the ISO of `(await self.session_provider.get_session_info(candidate.contract)).next_close` when it is available; get it once per contract, off the hot path, and leave it out when unavailable.
- Pass `valid_until` into the notification payload so the card can render it.

**Re-evaluate:**
- Add `run_scan(..., dedup_exempt_contracts: frozenset[str] = frozenset())`, which skips `is_duplicate_recent` for those contracts only.
- Add `async def reevaluate_signal(self, signal_id) -> ExecutionReply`. It looks up the old signal, whatever its status:
  - If not in RTH: `ExecutionReply(False, "Market closed; next regular open …")`.
  - Else `summary = await self.run_scan(symbols=[contract], budget=ScanBudget.NONE, dedup_exempt_contracts=frozenset({contract}))`. Reply "📨 Fresh card sent" if `summary["sent"] > 0`, else "No valid setup for {contract} right now" plus the first runner-up or rejection reason from the summary if present.

- [ ] **Step 1: Failing tests,** with a fake data fetcher price, a fake session provider and fake calendars:
  - `EXECUTE` calls `entry_service.authorize` with the ORIGINAL bracket;
  - `REPRICE` creates exactly one new PENDING signal and one outbox row, expires the old one, and does not call authorize;
  - `MISSED` and `EXPIRED` expire and set `offer_reevaluate`;
  - a price failure leaves the signal PENDING;
  - disabled gives the legacy path (authorize called, no assessment event);
  - a double tap on the re-priced original is refused (not PENDING);
  - an earnings gate turns an otherwise-`EXECUTE` into `MISSED`;
  - `valid_until` is recorded on scan cards;
  - `reevaluate_signal` passes the dedup exemption for only that contract, uses the NONE budget, and is refused outside RTH;
  - the CLI `trade` caller still works.
- [ ] **Step 2:** RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** GREEN.
- [ ] **Step 5:** `git add`.

### Task 4: Telegram UX + docs

**Files:**
- Modify: `agentic_trader/notifier/telegram_bot.py`
- Modify: docs `docs/production.md` (suggestion scans / execution section), `docs/durable-execution.md` (entry authorization: freshness precedes `authorize` and never authorizes by itself), `docs/alpha-roadmap.md` (a "Card freshness (September 23)" item under the alpha expansion workstreams, marked delivered, with its follow-ups)
- Test: extend `tests/notifier/test_telegram_interactive.py`

**Behaviour:**
- The `execute_handler` type is `Callable[..., Awaitable[ExecutionReply]]`. The `exec_` branch replies with `reply.text`. When `reply.offer_reevaluate`, attach `InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Re-evaluate", callback_data=f"reval_{signal_id}")]])` to that reply.
- New `reevaluate_handler: Callable[[int], Awaitable[ExecutionReply]] | None`, wired to `copilot.reevaluate_signal` where `execute_handler` is wired (copilot.py ~line 204). The `reval_` branch:
  1. `query.answer("Re-evaluating…")`;
  2. `_safe_clear_markup()`, so the button is single-use;
  3. call the handler;
  4. reply with its text.

  Parse with a prefix check so it can't collide with `exec_` or `dism_`.
- Card formatters (`format_alert_card`, `format_terminal_card`, and whatever `send_signal_alert` renders from the notification payload):
  - when the payload or eval has `valid_until`, add `• <b>Valid until:</b> HH:MM NY` (converted to America/New_York) after the Earnings line;
  - when the payload has `reprices`, prefix the title with `🔄 UPDATED CARD (re-priced from #N, first issued HH:MM NY)`.
  - Output is unchanged when these are absent.

- [ ] **Step 1: Failing tests:**
  - `reval_` parsing calls the handler and clears the markup;
  - the re-evaluate button is present only when `offer_reevaluate`;
  - the valid-until and updated-card lines are present or absent as appropriate;
  - existing `exec_` and `dism_` behaviour is unchanged.
- [ ] **Step 2:** RED.
- [ ] **Step 3:** Implement and write the docs.
- [ ] **Step 4:** GREEN.
- [ ] **Step 5:** `git add`.

After Task 4, the controller commits, runs the final review, opens the PR, merges, deploys and verifies.

Post-deploy verification: the next scheduled card shows "Valid until".
