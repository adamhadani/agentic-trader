# Nasdaq Soft-Error Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an HTTP 200 Nasdaq calendar page with `status.rCode != 200` or a non-object `data` is never read as a verified empty day, by the live blackout lookup, PEAD or the capture.

**Architecture:** one acceptance predicate in `agent/earnings.py`, used by `_request_date`, `reported_rows` and the capture. The parsers and the research acquisition are unchanged.

**Tech Stack:** Python 3.14, httpx (`MockTransport` in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-11-13-nasdaq-soft-error-design.md`

## Global Constraints

- The four existing reason strings are unchanged (`HTTP {code}`, `body is not JSON`, `body is not a JSON object`, `status.rCode {code!r}`). The one new reason is exactly `data is not an object`.
- `parse_calendar_payload`, `parse_forward_rows` and `research/apriori/earnings_history.py` behaviour stays unchanged; only their docstrings change.
- GET only, and no new retry. No config change, no migration (head `008`), no compatibility alias for `_rejection`: update its callers.
- Logs carry the date and reason only.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **Weekend page.** `data: {"asOf": "...", "rows": null}` with rCode 200 stays a verified empty day in all three paths.
2. **Live soft error.** A soft-error live page goes through the failure backoff and is not refetched within `failure_retry_minutes`.
3. **Fallback.** A soft-error live page with a capture holding the report gives a capture-sourced blackout, not a clear.
4. **Capture with `data: null`.** Such a page fails the pass immediately, with no retry, and nothing is journaled.
5. **Research untouched.** The research acquisition's acceptance and the parser behaviour are untouched (existing `tests/research/apriori/test_earnings_history.py` and the pooled events tests still pass).

---

### Task 1: Shared calendar page acceptance

**Files:**
- Modify: `agentic_trader/agent/earnings.py`:
  - add `calendar_page_rejection`;
  - `_request_date` and `reported_rows` call it;
  - parser docstrings change.
- Modify: `agentic_trader/data/earnings_capture.py`: remove `_rejection` and call `calendar_page_rejection`.
- Test: `tests/agent/test_earnings.py`, `tests/data/test_earnings_capture.py`.
- Docs: `docs/production.md` per the spec's Docs section.

**Interfaces:**
- Produces: `calendar_page_rejection(response: httpx.Response) -> tuple[dict[str, Any] | None, str | None]`, exported from `agentic_trader.agent.earnings`. It returns `(payload, None)` when accepted and `(None, reason)` otherwise.

- [ ] **Step 1: Write the failing tests** (spec "Tests" section):
  - `test_calendar_page_rejection_reasons`, parametrized over the five reasons plus one accepted page.
  - `test_null_rows_is_a_verified_empty_day`, updated: `_response(None)` builds `{"data": {"rows": None}, ...}`.
  - `test_null_data_page_is_unverified`.
  - `test_non_200_rcode_page_is_unverified`.
  - `test_soft_error_date_is_not_refetched_within_the_failure_backoff`.
  - `test_reported_rows_null_data_is_unavailable`: asserts `rows is None` and `detail == "data is not an object"`.
  - `test_soft_error_live_page_falls_back_to_capture`: a real `NasdaqEarningsCalendar` on `MockTransport` serving `data: null` for every date, plus the capture double already used by the `resolve_earnings` tests holding a report for the symbol inside the window. Asserts `source == "capture"` and that the event is found.
  - In `tests/data/test_earnings_capture.py`:
    - `test_null_data_is_accepted_empty_day` is replaced by `test_null_data_page_fails_the_capture`, with nothing journaled and the reason `data is not an object`, mirroring the existing rCode 400 test at about line 251;
    - `test_null_rows_object_is_accepted_empty_day` asserts `rows == 0` and `as_of` set.

- [ ] **Step 2: Run them and confirm they fail for the right reason.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_earnings.py tests/data/test_earnings_capture.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement the change** per the spec's Change section.

- [ ] **Step 4: Run the affected suites.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/agent tests/data tests/research/apriori tests/research/pooled tests/execution/test_release_exit.py -q -p no:cacheprovider`
  Expected: PASS.

- [ ] **Step 5: Update the docs.**

- [ ] **Step 6: Run the hooks except pytest.**
  Run: `SKIP=pytest env -u VIRTUAL_ENV uv run pre-commit run --all-files`
  Expected: PASS.

- [ ] **Step 7: Stage** the files. Do not commit.
