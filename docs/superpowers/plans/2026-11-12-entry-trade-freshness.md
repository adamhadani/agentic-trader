# Entry Trade Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A liquid name whose last IEX print is one to five minutes old, with a fresh quote, is no longer refused at entry admission. A fresh quote midpoint now also guards against price drift.

**Architecture:**
- Add one config field, `execution.entry_trade_max_age_seconds`, read by the latest-trade freshness check in `assess_entry_capacity`.
- Add one extra drift comparison, against the quote midpoint, in `EntryExecutionService._preflight`.
- Everything else in admission is unchanged.

**Tech Stack:** Python 3.14, pydantic v2, pytest (SQLite, plus `--run-postgres` on `test_*` databases), and the real Alpaca SDK against the local venue fixtures (`tests/integration/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-12-entry-trade-freshness-design.md`

## Global Constraints

- `entry_trade_max_age_seconds: float = Field(default=300, gt=0, le=900, allow_inf_nan=False)` on `ExecutionConfig`. `config/config.yaml` ships `entry_trade_max_age_seconds: 300.0`.
- These stay unchanged: `entry_quote_max_age_seconds` (60) for the quote and the broker clock, `entry_evidence_max_age_seconds` (30), `entry_max_price_drift_pct` (0.01) and `BROKER_CLOCK_SKEW_TOLERANCE_SECONDS` (1.0).
- Existing refusal texts are unchanged. The one new text is exactly `Market quote midpoint moved to {mid:g}; original approved limit {limit:g} retained.`
- No retry or re-read of market evidence after a refusal. POST/PATCH/DELETE are never retried.
- No migrations: schema head stays `008`. No compatibility aliases.
- Postgres tests run only against `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **Both stages.** A trade older than 300 s is still refused at preflight and in `begin_submission`, since both call `assess_entry_capacity`. A trade between 60 and 300 s old passes both.
2. **Clock skew.** A trade timestamp more than 1 s in the future is still refused.
3. **Quote limit.** The quote stays at 60 s even when the trade is fresh.
4. **Drift is symmetric.** The midpoint check uses an absolute difference, so it behaves the same for long and short requests and for a move in either direction.
5. **Simulator path.** `SimulatedEntryContext` never reaches either drift check, and its behaviour is unchanged.

---

### Task 1: Separate trade age limit and quote-midpoint drift check

**Files:**
- Modify: `agentic_trader/config.py` (`ExecutionConfig`, about lines 859–868), `config/config.yaml` (execution block, about line 365).
- Modify: `agentic_trader/execution/capacity.py:248`.
- Modify: `agentic_trader/execution/entries.py` (`_preflight` drift block, about lines 87–95).
- Tests:
  - `tests/execution/test_broker_capacity.py` (the parametrized clock test around line 143, plus new cases);
  - `tests/workflows/test_entries.py` (preflight drift);
  - one end-to-end test under `tests/integration/` with the real SDK. The local venue currently stamps the trade and the quote with the same `venue.quote_time`; add a separate trade time that defaults to it.
  - a config test wherever `ExecutionConfig` defaults are tested (`grep -rn "entry_quote_max_age_seconds\|ExecutionConfig(" tests`).
- Docs: `docs/durable-execution.md` (the config table near line 265, plus an incident note) and `docs/entry-capacity.md` (the evidence paragraph near line 58).

**Interfaces:**
- Produces: `ExecutionConfig.entry_trade_max_age_seconds: float`.
- `assess_entry_capacity(...)` keeps its signature; its trade check reads the new field.
- `_preflight` returns `(reason, context)` exactly as today.

- [ ] **Step 1: Write the failing tests.**
  - `tests/execution/test_broker_capacity.py`:
    - `test_trade_older_than_quote_limit_but_within_trade_limit_is_admitted`: trade 120 s old, quote 1 s old. No exception is raised.
    - `test_trade_older_than_trade_limit_is_refused`: trade 301 s old. Raises `ValueError` matching `Market trade is stale`.
    - `test_quote_limit_is_unchanged_with_a_fresh_trade`: quote 61 s old, trade 1 s old. Raises, matching `Market quote is stale`.
    - `test_future_trade_beyond_skew_is_refused`: trade 2 s in the future. Raises, matching `Market trade is stale`.
    - Update the parametrized clock test: its `trade_timestamp` case must use an age above 300 s, or above the config value it sets.
  - `tests/workflows/test_entries.py`:
    - `test_quote_midpoint_drift_refuses_before_macro`: trade price equal to the approved limit, and quote bid and ask both 1.5% above it. The reason equals `Market quote midpoint moved to {mid:g}; original approved limit {limit:g} retained.`, the macro check is not awaited, and nothing is POSTed.
    - `test_quote_midpoint_within_drift_proceeds`: the midpoint is 0.5% away. Preflight proceeds to the macro check.
  - **Config:**
    - `ExecutionConfig().entry_trade_max_age_seconds == 300`;
    - `0` and `901` raise a validation error;
    - the shipped `config/config.yaml` loads `300.0`.
  - **Integration** (real SDK, local venue): `test_entry_with_a_two_minute_old_trade_submits_once`. Set the venue trade time to now − 120 s and keep the quote fresh. `dispatch_one()` returns `True`, the work ends `ACCEPTED`, there is exactly one POST, and there is one `ENTRY_SUBMITTING` event. Run it on SQLite and, if the module is parametrized, on Postgres.

- [ ] **Step 2: Run the new tests and confirm they fail.**
  Run: `uv run pytest tests/execution/test_broker_capacity.py tests/workflows/test_entries.py -q`, plus the new integration test.
  Expected: FAIL. The new field is missing, a 120 s trade is refused, and there is no midpoint check.

- [ ] **Step 3: Implement it.**
  - Add the config field and the yaml line, with a comment saying IEX prints are sparse on mid caps while the quote keeps 60 s.
  - In `capacity.py:248`, pass `policy.entry_trade_max_age_seconds`.
  - In `_preflight`, right after the existing trade-price drift return, compute `mid = (context.quote.bid_price + context.quote.ask_price) / 2`. If `abs(float(mid) / request.entry_price - 1) > policy.entry_max_price_drift_pct`, return the midpoint message with `context`.
  - Update the comment above the drift block to name both checks.

- [ ] **Step 4: Run the affected suites and confirm they pass.**
  Run: `uv run pytest tests/execution tests/workflows tests/integration/test_entry_evidence.py tests/integration/test_entry_capacity_http.py tests/integration/test_account_risk_admission.py -q`, plus the new integration test.
  Then: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration --run-postgres -q -k "entry or capacity or admission"`
  Expected: PASS.

- [ ] **Step 5: Update the docs.**
  - `docs/durable-execution.md`: add the `entry_trade_max_age_seconds | 300` row next to `entry_quote_max_age_seconds | 60`. Add one or two sentences on the incident: COLB, NSC and GEHC were refused on 11-10/11/12 with fresh quotes, and the IEX trade-age probe found 30.5% of names older than 60 s.
  - `docs/entry-capacity.md`: in the IEX paragraph, add that the last trade has its own 300 s limit, the quote 60 s, and that the preflight drift check compares both the trade and the quote midpoint with the approved limit.

- [ ] **Step 6: Run the hooks except pytest.**
  Run: `SKIP=pytest uv run pre-commit run --all-files`
  Expected: all pass.

- [ ] **Step 7: Stage** the files with `git add`. Do not commit.
