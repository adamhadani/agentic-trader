# Earnings Blackout Capture Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the live Nasdaq earnings lookup fails, the equity earnings blackout uses the fresh dated capture. When neither source can vouch for the window, the blackout refuses under `risk.earnings_calendar_unknown: closed`.

**Architecture:**
- One `resolve_earnings` function in `agent/earnings.py` reads the live lookup, then the capture. It is used by the scan evaluator and by the tap/auto-entry gate.
- `earnings_blackout_reason` gains an `unknown` policy keyword.
- One `EarningsCaptureStore` instance is shared by the release exit and the evaluator.

**Tech Stack:** Python 3.14 asyncio, pydantic v2 config, pytest (asyncio auto mode), with fakes for the live calendar and the capture source.

**Spec:** `docs/superpowers/specs/2026-11-12-earnings-blackout-capture-design.md`

## Global Constraints

- `risk.earnings_calendar_unknown: Literal["open", "closed"] = "open"` on the risk config. `config/config.yaml` ships `earnings_calendar_unknown: closed`, next to `macro_calendar_unknown`.
- The new refusal text is exactly `{symbol}: earnings calendar unavailable and no fresh capture covers the next {blackout_days} days (policy closed)`. Call sites prefix it with `Earnings Blackout: `, under `RiskRule.EARNINGS_BLACKOUT`.
- Capture note suffix, exactly: ` (calendar capture {capture_date ISO})`.
- `EarningsLookup` new fields: `source: Literal["live", "capture"] = "live"` and `capture_date: date | None = None`.
- **Log text:** type names only, never exception messages. The live-failure log uses `event: earnings_lookup_failed`.
- No new provider calls, no change to the Nasdaq client or the capture worker, schema head stays `008`, and no compatibility aliases.
- **Tests:** offline fakes only; network is blocked.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **A found report always blocks**, including one from an unverified live lookup. The capture is not consulted then.
2. **Same-day timing parity.** A capture row for today with `PRE_MARKET` timing is not ahead, exactly as in the live lookup. Today with any other timing is ahead.
3. **A stale or partial capture** (older than 4 days, or a weekday of the window missing) gives None from `reports_between`. The result is unverified, so `closed` refuses and `open` notes "Unverified".
4. **Disabled cases never refuse:** `earnings_blackout_days: 0`, a non-equity, or no live calendar (`earnings_calendar is None`), even under `closed`.
5. **Scan and tap give the same decision** for the same inputs. They share the resolver and the reason.

---

### Task 1: Resolver, lookup fields, reason, note and config

**Files:**
- Modify: `agentic_trader/agent/earnings.py` (`EarningsLookup`, `earnings_blackout_reason`, `earnings_note`, the new `resolve_earnings` and capture Protocol, `__all__`).
- Modify: `agentic_trader/config.py` (risk config next to `macro_calendar_unknown`, about line 601), `config/config.yaml` (about line 285), `agentic_trader/risk/limits.py` (add the policy beside `earnings_blackout_days`, about lines 72 and 121).
- Test: `tests/agent/test_earnings.py`, plus the config tests wherever `macro_calendar_unknown` defaults are tested (`grep -rn macro_calendar_unknown tests`).

**Interfaces:**
- Produces:
  - `resolve_earnings(symbol: str, now: datetime, horizon_days: int, *, live: EarningsCalendarProtocol | None, captures: EarningsCaptureSource | None) -> EarningsLookup`.
  - `EarningsCaptureSource` (Protocol): `async def reports_between(self, start: date, end: date, *, today: date) -> CapturedReports | None`.
  - `earnings_blackout_reason(lookup, symbol, now, blackout_days, *, unknown: Literal["open", "closed"] = "open") -> str | None`.
  - `RiskLimits.earnings_calendar_unknown`.
- Consumes: `CapturedReports` (`capture_date`, and `rows: Mapping[date, tuple[tuple[str, EarningsTiming], ...]]`) from `agentic_trader.data.earnings_capture`, imported under `TYPE_CHECKING` only.

- [ ] **Step 1: Write the failing tests** in `tests/agent/test_earnings.py`, with a fake live calendar (returns a given lookup or raises) and a fake capture source (returns given `CapturedReports` or None, or raises):
  - `test_verified_live_result_is_used_without_reading_the_capture`: the capture fake records calls; assert none.
  - `test_live_failure_falls_back_to_capture_report_in_window`: live raises `httpx.HTTPStatusError`, or any exception. The capture holds the symbol 3 days out. Assert `source == "capture"`, `verified`, the event date, `capture_date`, and that `earnings_blackout_reason(...)` blocks.
  - `test_live_failure_with_capture_absence_is_verified_clear`: no reason, and the note ends with ` (calendar capture 2026-11-12)`.
  - `test_unverified_live_without_event_falls_back_to_capture`.
  - `test_unverified_live_with_event_still_blocks_without_capture`: the capture is not called.
  - `test_capture_today_premarket_is_not_ahead_but_after_close_is`.
  - `test_no_capture_gives_unverified`: `reports_between` returns None. With `unknown="closed"` the reason equals the exact text. With `unknown="open"` there is no reason and the note is `Unverified — earnings calendar unavailable`.
  - `test_capture_read_error_is_treated_as_unavailable`: it raises. Unverified, and the log record carries the type name, not the message.
  - `test_closed_policy_with_zero_blackout_days_never_refuses`.
  - **Config:** default `open`; the shipped yaml loads `closed`; `RiskLimits` carries it.

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run: `uv run pytest tests/agent/test_earnings.py -q`
  Expected: FAIL. `resolve_earnings` and the `unknown` keyword are missing.

- [ ] **Step 3: Implement** per spec sections 1–3. `horizon_end = today + timedelta(days=horizon_days)`, with `today` the New York date of `now`. Scan the capture's rows in date order, from today to `horizon_end`.

- [ ] **Step 4: Run the tests and confirm they pass.**
  Run: `uv run pytest tests/agent/test_earnings.py tests/execution -q`
  Expected: PASS. The release-exit tests are unchanged.

- [ ] **Step 5: Stage** the files. Do not commit.

### Task 2: Use the resolver at both call sites, wire the shared capture store, and update the docs

**Files:**
- Modify: `agentic_trader/agent/evaluator.py` (the constructor, about line 139; step 2b, about lines 568–597).
- Modify: `agentic_trader/agent/copilot.py` (`EarningsCaptureStore` construction, about line 465; `RiskEvaluator(...)`, about line 558; `_earnings_blackout_reason`, about line 3964).
- Tests: `tests/agent/test_evaluator_risk_policy.py` or `tests/agent/test_evaluator.py` (scan), `tests/agent/test_tap_risk_policy.py` (tap), and a wiring test.
- Docs: `docs/risk-policy.md`, `docs/production.md`, `CLAUDE.md`, per the spec's Docs section.

**Interfaces:**
- Consumes: Task 1's `resolve_earnings`, `earnings_blackout_reason(..., unknown=...)` and `RiskLimits.earnings_calendar_unknown`.
- Produces: `RiskEvaluator(..., earnings_captures: EarningsCaptureSource | None = None)`.

- [ ] **Step 1: Write the failing tests:**
  - **Scan:** with a live calendar that raises and no capture, under `closed`: the candidate is rejected with rule `EARNINGS_BLACKOUT` and a reason containing `earnings calendar unavailable`. Under `open`: not rejected, and the note is `Unverified — earnings calendar unavailable`. With a capture holding a report 2 days out: rejected with the blackout reason.
  - **Tap:** the same three cases through `_earnings_blackout_reason` (or `_tap_gate_reason`).
  - **Parity:** the same fakes give the same refusal text from the scan and the tap.
  - **Wiring:** `TradingCopilot`'s evaluator holds an `earnings_captures` that is the same object as `release_exit.captures`.

- [ ] **Step 2: Run the tests and confirm they fail.**

- [ ] **Step 3: Implement it.**
  - Replace both try/except fail-open blocks with `resolve_earnings`. Keep the existing gate conditions: equity, `blackout_days > 0`, and the live calendar not None.
  - Build one `EarningsCaptureStore` and pass it to both consumers.

- [ ] **Step 4: Run the tests and confirm they pass.**
  Run: `uv run pytest tests/agent tests/execution -q -p no:cacheprovider`
  Expected: PASS, with the existing tests unmodified except for removed message-bearing log assertions, if any.

- [ ] **Step 5: Update the docs** per the spec.

- [ ] **Step 6: Run the hooks except pytest.**
  Run: `SKIP=pytest uv run pre-commit run --all-files`
  Expected: PASS.

- [ ] **Step 7: Stage** the files. Do not commit.
