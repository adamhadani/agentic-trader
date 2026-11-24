# Entry Risk Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A periodic ledger checkpoint landing between entry preflight and final submission no longer refuses the entry. The final submission re-assesses capacity on the newer observation, as it already does, and records both fingerprints.

**Architecture:** One change in `WorkflowStore.begin_submission` (`agentic_trader/storage/workflow.py`):
- Drop the exact-fingerprint refusal; the existing lease, expiry and `assess_entry_capacity` checks on the snapshot read under the lock stay binding.
- Keep a fail-closed guard for a missing preflight fingerprint.
- Rename the parameter `risk_fingerprint` to `preflight_risk_fingerprint` and update the caller in `execution/entries.py`.

**Tech Stack:** Python 3.14, SQLAlchemy async, pytest (SQLite plus `--run-postgres` on `test_*` databases), and the real Alpaca SDK against the local venue fixtures.

**Spec:** `docs/superpowers/specs/2026-11-10-entry-risk-refresh-design.md`

## Global Constraints

- **No migrations:** schema head stays `008`. No new config keys.
- **No auto-retry:** POST/PATCH/DELETE are never retried. Nothing is resized or replayed.
- **Lock order:** trading → ledger, unchanged.
- **Log and journal text:** type names only, no values.
- **No aliases:** no compatibility alias for the renamed parameter. Update every caller and test in the same change.
- **Postgres tests:** only on a database named `test_*`: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **Material worsening during the preflight.** A loss is recorded and refreshed while the preflight runs. It must still refuse, and the reason must come from the capacity re-check ("drawdown"), not from missing broker evidence.
2. **Lock-wait race.** Admission waits on the ledger lock while a worse checkpoint publishes. The refusal comes from the re-check on the published snapshot.
3. **Evidence consistency.** On a refresh, `risk_fingerprint` matches the digest of `account_risk` in `ENTRY_SUBMITTING`, and `preflight_risk_fingerprint` is the old one.
4. **Store reads risk but the preflight passed `None`.** Refuse, with no mutation.
5. **Simulator path** (no risk at either stage). Unchanged.

---

### Task 1: Re-assess on a refreshed observation instead of refusing

**Files:**
- Modify: `agentic_trader/storage/workflow.py` (`begin_submission`, about lines 936–1022).
- Modify: `agentic_trader/execution/entries.py:141-146` (the caller).
- Modify tests:
  - `tests/integration/test_account_risk_admission.py`
  - `tests/integration/test_account_risk_race.py`
  - `tests/integration/test_entry_capacity_http.py`
  - `tests/integration/test_entry_capacity_race.py`
- Docs:
  - `CLAUDE.md:64`
  - `AGENTS.md:189`
  - `docs/account-ledger.md:140-146`
  - `docs/durable-execution.md:70`
  - `docs/entry-capacity.md:26-28`

**Interfaces:**
- Produces: `WorkflowStore.begin_submission(item, config, *, preflight_risk_fingerprint: str | None = None, broker_context: BrokerEntryContext | None = None) -> str | None`.
- `ENTRY_SUBMITTING` payload keys:
  - `account_risk`: the snapshot read under the lock (unchanged);
  - `risk_fingerprint`: `account_risk`'s fingerprint, or `None` without risk;
  - `preflight_risk_fingerprint`: the argument;
  - plus the existing keys.

- [ ] **Step 1: Write the failing tests.**
  - In `tests/integration/test_account_risk_admission.py`, add:
    - `test_benign_refresh_during_preflight_submits_once`. Run `enqueue_entry`, then set `entry.macro_check.side_effect` to an async function that awaits `copilot.ledger.refresh()` with no state change. Then `assert await entry.dispatch_one()`. Assert:
      - the item status is `WorkStatus.ACCEPTED`;
      - exactly one POST in `mutations(venue)`;
      - exactly one `ENTRY_SUBMITTING` event, whose payload has `preflight_risk_fingerprint != risk_fingerprint`, `risk_fingerprint == (await copilot.ledger.current_risk()).fingerprint`, and `risk_fingerprint == AccountRiskSnapshot.model_validate(payload["account_risk"]).fingerprint`.
    - `test_store_level_benign_refresh_is_not_refused`. Run `enqueue_entry`, `claim_entry(lease_seconds=60)`, `checked = await copilot.ledger.current_risk()`, `await copilot.ledger.refresh()`, and `context = await copilot.broker.entry_market_context(order)`. Then `reason = await store.begin_submission(claim, copilot.config, preflight_risk_fingerprint=checked.fingerprint, broker_context=context)`. Assert `reason is None`, `(await copilot.ledger.current_risk()).fingerprint != checked.fingerprint`, and that the work status is `WorkStatus.SUBMITTING`.
    - `test_risk_without_preflight_fingerprint_is_refused`. Same setup, but pass `preflight_risk_fingerprint=None` with the context. Assert the reason contains "not observed at preflight", the status is `WorkStatus.CHECKING`, and there are no mutations.
  - Rewrite `test_new_checkpoint_fences_an_already_checked_claim` as `test_worse_checkpoint_after_preflight_is_refused_by_capacity_recheck`. Record a loss of 700, refresh, and pass `broker_context=await copilot.broker.entry_market_context(order)`. Assert "drawdown" is in `reason.lower()`, the status is `CHECKING`, and there are no mutations.
  - In `test_worsening_or_stale_risk_blocks_real_sdk_post`, for `stage == "preflight"`, assert "drawdown" is in `reason.lower()`.
  - In `tests/integration/test_account_risk_race.py`, pass a broker context: `context = await ledger.broker.entry_market_context(OrderRequest.model_validate(claim.payload))`, obtained before the writer publishes. Call `begin_submission(claim, app_config, preflight_risk_fingerprint=checked.fingerprint, broker_context=context)`. Change the refusal assertion to `"drawdown" in refusal.lower()`. Keep every other assertion (lock wait, `CHECKING`, no `ENTRY_SUBMITTING`, no mutations).
  - Rename `risk_fingerprint=` to `preflight_risk_fingerprint=` at every other `begin_submission` call in tests.

- [ ] **Step 2: Run the new and changed tests and confirm they fail.**
  Run: `uv run pytest tests/integration/test_account_risk_admission.py tests/integration/test_account_risk_race.py -q`
  Expected: FAIL. The new tests either fail with `TypeError` for the unexpected keyword `preflight_risk_fingerprint`, or are refused with "risk changed after preflight".

- [ ] **Step 3: Implement it in `begin_submission`.**
  - Rename the keyword.
  - Replace the `risk.fingerprint != risk_fingerprint` refusal with two parts:
    - `if risk and preflight_risk_fingerprint is None: return "Account risk was not observed at preflight. No order submitted."`
    - When `risk` exists and its fingerprint differs, emit `logger.info(...)` with no values, saying the observation was refreshed between preflight and submission and is being re-assessed. Use `extra={"event": "entry_risk_refreshed"}`.
  - Leave every following check as it is. They already use the snapshot read under the lock.
  - In the evidence dict, set `"risk_fingerprint": risk.fingerprint if risk else None` and add `"preflight_risk_fingerprint": preflight_risk_fingerprint`.
  - Update the docstring to name the rule.
  - In `entries.py`, pass `preflight_risk_fingerprint=risk.fingerprint if risk else None`.

- [ ] **Step 4: Run the affected suites and confirm they pass.**
  Run: `uv run pytest tests/integration/test_account_risk_admission.py tests/integration/test_account_risk_race.py tests/integration/test_entry_capacity_http.py tests/integration/test_entry_capacity_race.py tests/execution -q`
  Then: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration/test_account_risk_admission.py tests/integration/test_account_risk_race.py tests/integration/test_entry_capacity_race.py --run-postgres -q`
  Expected: PASS.

- [ ] **Step 5: Update the docs.**
  - **The rule:** replace "pins the (exact) risk fingerprint" wherever it states the rule (the five files above) with this meaning: final submission re-reads the fresh risk observation under the trading → ledger lock and re-assesses capacity on it; a refreshed observation is recorded (`preflight_risk_fingerprint` beside `risk_fingerprint`), not refused; a changed observation refuses only through those checks.
  - **`docs/account-ledger.md`:** keep "no order is resized or replayed". Reword "A changed/expired observation rejects the original request" to "An expired observation, or one that fails the re-assessment, rejects the original request".
  - **`docs/durable-execution.md`:** add one sentence on the 2026-11-09 incident. A periodic checkpoint between preflight and submission refused the TXN auto-entry, even though the re-assessment would have passed.

- [ ] **Step 6: Run the whole suite and pre-commit.**
  Run: `uv run pre-commit run --all-files`. Its pytest hook runs the full suite.
  Expected: all pass.

- [ ] **Step 7: Stage.** Stage only, with `git add` on the files above. The controller commits.
