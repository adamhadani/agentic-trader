# PEAD Concurrent Bars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PEAD live preparation stays inside its 60 s budget on large earnings pages, and one catalog source's overrun no longer starves another.

**Architecture:**
- In `pead_live.live_events`, fetch both bar requests as 100-symbol chunks. All chunks run concurrently under one shared semaphore of 8, each chunk is retried once on its own, and any final failure still fails the session closed.
- In `TradingCopilot.run_scan`, catalog sources that prepare now do so concurrently under the existing single deadline, and post-processing keeps source order.

**Tech Stack:** Python 3.14 asyncio (`asyncio.Semaphore`, `asyncio.TaskGroup` or gather with cancellation, `asyncio.to_thread`), pandas, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-11-10-pead-concurrent-bars-design.md`

## Global Constraints

- Leave the frozen PEAD entry, `build_events`, `LOOKBACK_CALENDAR_DAYS = 120`, `DAILY_BATCH_SYMBOLS = 100`, `DRIFT_PREPARE_TIMEOUT_SECONDS = 60.0` and the 10 s provider read timeout unchanged.
- `PEAD_FETCH_CONCURRENCY = 8` is a module constant in `pead_live.py`. Add no config key.
- **Fail closed:** unavailable, never partial. Any chunk failing after its single retry fails the session.
- **Journal and log text:** type names only. The reason is `bars_unavailable: {TypeName} (after 1 retry)`.
- **Executor:** do not use the shared `provider_reads` `BoundedReadExecutor`.
- **Schema:** no migrations, head stays `008`.
- **Tests:** offline only, with fakes; network is blocked in tests.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **Cancellation on failure.** When one chunk fails twice, the session returns `unavailable` promptly. The other in-flight chunk awaits are cancelled, nothing is merged, and no exception escapes `live_events`. This includes `ExceptionGroup` from a `TaskGroup`.
2. **A timeout while chunks are in flight.** `asyncio.timeout_at` in `_prepare_catalog` cancels `live_events`. The cancellation must propagate, and no "Task exception was never retrieved" warnings may leak.
3. **Order and identity.** Merged frames per symbol equal the sequential result. Duplicate symbols across the adjusted and raw requests are fine because they are separate mappings.
4. **Fewer than 100 symbols.** A single chunk behaves exactly like today, including SPY with a tiny reporter list.
5. **Sources racing.** With concurrent sources, `catalog_only` and `dynamic_contracts` are built in `catalog_runs` order, not completion order.

---

### Task 1: Concurrent chunked PEAD bar fetch with per-chunk retry

**Files:**
- Modify: `agentic_trader/research/apriori/pead_live.py` (the `_fetch_daily` region, about lines 117–176).
- Test: `tests/research/apriori/test_pead_live.py`.
- Docs: `docs/apriori-alphas.md` (PEAD live probe paragraph), `docs/production.md` (incident note).

**Interfaces:**
- Consumes: `DAILY_BATCH_SYMBOLS` from `agentic_trader.data.providers` (the chunk size), and `DailyBars.fetch_daily_many(symbols, start, end, *, adjustment)`.
- Produces: `PEAD_FETCH_CONCURRENCY = 8`. `live_events(...)` has the same signature as today. `_fetch_daily` is replaced by a chunked helper; it is private, so name it as you like.

- [ ] **Step 1: Write the failing tests** in `tests/research/apriori/test_pead_live.py`, using a fake `DailyBars` that records each call's symbols and adjustment and tracks peak in-flight calls:
  - `test_large_fetch_is_chunked_and_bounded`: 250 reporters plus the static names. Every call has at most `DAILY_BATCH_SYMBOLS` symbols. The union of symbols per adjustment equals the requested set. Peak in-flight equals `PEAD_FETCH_CONCURRENCY` when the fake blocks until 8 calls are in flight and then releases.
  - `test_failed_chunk_is_retried_alone`: one specific chunk raises `ReadTimeout` once. The result is `ok`. That chunk was called twice and every other chunk exactly once.
  - `test_chunk_failing_twice_fails_the_session_with_type_name_only`: one chunk always raises `ReadTimeout("HTTPSConnectionPool(host='x') read timed out")`. The result status is `unavailable` and the reason is exactly `bars_unavailable: ReadTimeout (after 1 retry)`.
  - `test_chunked_result_matches_the_existing_fixture_events`: the existing ok fixtures through the chunked path give the same `events` and `counts` as the current expectation.
  - Update the existing assertions at about lines 333, 338–339 and 372–373 to the type-name-only format. The long-message truncation test becomes a check that the message never appears.

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run: `uv run pytest tests/research/apriori/test_pead_live.py -q`
  Expected: FAIL. There is no chunking, `PEAD_FETCH_CONCURRENCY` is missing, and the reason has the old format.

- [ ] **Step 3: Implement** the chunked concurrent fetch in `pead_live.py`.
  - Use one `asyncio.Semaphore(PEAD_FETCH_CONCURRENCY)` per `live_events` call, shared by the adjusted and raw requests.
  - Each chunk is one `asyncio.to_thread(bars.fetch_daily_many, chunk, start, end, adjustment=adj)`.
  - On an exception, log the `pead_bars_retry` warning (`reason`: type name), `await sleep(BARS_RETRY_DELAY_SECONDS)` without holding the semaphore, then retry once.
  - On a final failure, cancel the siblings and surface the first failure's type name.
  - Merge the per-adjustment dicts.
  - The reason is `f"bars_unavailable: {type(exc).__name__}{_RETRY_SUFFIX}"`.

- [ ] **Step 4: Run the tests and confirm they pass.**
  Run: `uv run pytest tests/research/apriori tests/screeners/test_earnings_drift.py tests/agent/test_scan_drift.py -q`
  Expected: PASS, with no "never retrieved" warnings in the output.

- [ ] **Step 5: Update the docs.**
  - `docs/apriori-alphas.md`: one or two sentences on concurrent chunked fetching (8 concurrent, per-chunk retry, still unavailable on any final failure, reason type name only).
  - `docs/production.md`: an incident note on PEAD unavailable 11-05, 11-06 and 11-09 and the cause.

- [ ] **Step 6: Stage** the files. Do not commit.

### Task 2: Catalog sources prepare concurrently under the shared deadline

**Files:**
- Modify: `agentic_trader/agent/copilot.py:1232-1286` (the catalog-runs loop and its comment).
- Test: `tests/agent/test_scan_drift.py`, or the module that already builds a desk with both the PEAD and insider-watch sources (find it with `grep -rln insider_watch_source tests/agent`).

**Interfaces:**
- Consumes: `TradingCopilot._prepare_catalog(source, alpha_snapshot, active_positions, *, deadline)` (unchanged), and `CatalogRun` with fields `source`, `preparation` and `capped`.
- Produces: no new public names.

- [ ] **Step 1: Write the failing test** `test_a_slow_catalog_source_does_not_starve_the_next`:
  - Set up a desk with two catalog sources due at 10:35. Monkeypatch `copilot_module.DRIFT_PREPARE_TIMEOUT_SECONDS` to 0.2.
  - The first source's prepare sleeps 5 s. The second returns an ok preparation immediately.
  - Assert that the first source's decision is journaled `unavailable` / `timeout` and the second's is `ok`.
  - Assert that `run_scan`'s catalog preparation took under 0.2 s plus a margin of 0.5 s, not 0.4 s or more of sequential deadlines. Measure with `time.monotonic()` around `suggestion_scan`, or use the journaled `elapsed_seconds`.

- [ ] **Step 2: Run the test and confirm it fails.**
  Run: `uv run pytest <that test> -q`
  Expected: FAIL. The second source is starved and shows `timeout` with elapsed 0.0.

- [ ] **Step 3: Implement it.**
  - First pass over `catalog_runs`: handle late/skipped and unbuilt runs as today, and collect the runs that will prepare now.
  - Set `prepare_deadline` once, then `await asyncio.gather(*(self._prepare_catalog(...) for run in due))`.
  - Assign the preparations, then run the existing catalog-only and `dynamic_contracts` post-processing over `due` in `catalog_runs` order.
  - Update the comment to say sources prepare concurrently under one bound.

- [ ] **Step 4: Run the tests and confirm they pass.**
  Run: `uv run pytest tests/agent -q -p no:cacheprovider`
  Expected: PASS, including every existing catalog and drift test, unmodified.

- [ ] **Step 5: Stage** the files. Do not commit.
