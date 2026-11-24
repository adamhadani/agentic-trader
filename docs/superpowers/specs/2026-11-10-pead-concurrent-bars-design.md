# PEAD live preparation: concurrent bar chunks, and catalog sources that don't starve each other

## Why

The PEAD catalog source produced no decision on 2026-11-05, 11-06 and 11-09. Full diagnosis (not in Git): `scratchpad/pead-diagnosis.md`.

- **What the preparation does.** `pead_live.live_events` makes two sequential `_fetch_daily` calls: adjusted bars for `[SPY, *reporters]` and raw bars for `reporters ∪ 157 static names`. Each goes through `AlpacaDataProvider.fetch_daily_many`, a **sequential** loop over 100-symbol chunks (`DAILY_BATCH_SYMBOLS`). One chunk is one HTTP request, about 8,400 bars (120 calendar days), under Alpaca's 10,000-bar page.
- **Why it grows.** The number of requests tracks the Nasdaq page for the report date: 7 requests at 254 rows (11-05), 11 at 457 (11-06), 14 at 567 (11-09).
- **How slow it gets.** At 10:35 ET a request takes about 4–8 s. 10-30 already spent 54 of its 60 s on 7 requests.
- **11-09:** 14 sequential requests overran the shared `DRIFT_PREPARE_TIMEOUT_SECONDS` (60 s) with no error. No `pead_bars_retry` warning appeared anywhere in the log.
- **11-05 and 11-06:** these ran before #141. One chunk hit the 10 s read timeout and failed the whole session. The transport retries only `ConnectionError`; `ReadTimeout` isn't one.
  - #141's retry now repeats the **whole** multi-chunk call, so on a late failure it doubles the time.
- **Collateral:** catalog sources prepare one after another under one deadline (`copilot.py:1238-1267`). On 11-09 PEAD's overrun left the insider-watch source 0 s, so its decision was lost too (`insider_watch_decision` `unavailable`/`timeout`, elapsed 0.0).
- **Measured:** the 11-09 inputs (567 rows, 14 requests) took 22.9 s sequential before the open and 3.4 s with the 14 chunks concurrent.
- **Not the cause:** #144 (delayed SIP). The PEAD provider has its own `feed="sip"` client, and its request end is D+1 23:59 UTC.

Separately, `bars_unavailable` reasons embed the exception message, for example `HTTPSConnectionPool(host=…)`. Journal text should carry type names only.

## What changes

### 1. Concurrent, bounded chunk fetching with per-chunk retry (`research/apriori/pead_live.py`)

- `live_events` fetches bars as 100-symbol chunks: the same chunking as `DAILY_BATCH_SYMBOLS`, imported, not duplicated.
  - **Concurrency:** every chunk of both the adjusted and the raw request runs concurrently, bounded by one shared semaphore of `PEAD_FETCH_CONCURRENCY = 8`. Each chunk is one `asyncio.to_thread(bars.fetch_daily_many, chunk, start, end, adjustment=…)`, which is still one GET per chunk because a chunk never exceeds `DAILY_BATCH_SYMBOLS`.
  - **Retry:** a failed chunk is retried **once**, alone, after `BARS_RETRY_DELAY_SECONDS`, with a `pead_bars_retry` warning that carries type names only. Chunks that succeeded are never re-fetched.
  - **Merging:** results merge into one mapping per adjustment, the same shape `_fetch_daily` returns today.
- **Fail closed, unchanged:** if any chunk still fails after its retry, the session is `unavailable`, never partial. The remaining chunk tasks are cancelled and nothing is merged. The reason becomes `bars_unavailable: {TypeName} (after 1 retry)`: a type name only, no message, still within `REASON_MAX_CHARS`.
- The benchmark check, the static-reference check, the page checks and `build_events` are untouched.
- Why 8: 14 chunks become 2 rounds. The worst single chunk is 10 s + 2 s + 10 s, so even a retried round fits the 60 s budget. The native universe fetch already runs 8 concurrent requests (`scan_concurrency`). Do **not** route these requests through the shared `provider_reads` executor: it has 4 slots and fails fast, and leftover threads after a timeout would starve the native fetch.

### 2. Catalog sources prepare concurrently under the one deadline (`agent/copilot.py`)

- In the scan, the sources that will prepare now (on time and `source.applies(...)`) prepare **concurrently** with `asyncio.gather` over `_prepare_catalog`. They share the same single deadline, `DRIFT_PREPARE_TIMEOUT_SECONDS` from when preparation starts.
- Late, skipped and unbuilt runs are handled as today.
- After all preparations finish, the existing post-processing (catalog-only symbols, `dynamic_contracts`) runs **in `catalog_runs` order**, so the result is identical to the sequential version whenever every source finishes.
- `_prepare_catalog` already turns a timeout or an exception into that source's own `unavailable` preparation. One source overrunning or failing therefore no longer takes another's time.
- The total scan-lock hold stays bounded by one 60 s window.
- Update the comment at `copilot.py:1238-1239`.

**Unchanged:**
- The frozen PEAD entry, its identity (`entry_sha256`) and `build_events`.
- Lookback, the 10 s read timeout, `DRIFT_PREPARE_TIMEOUT_SECONDS` and `DAILY_BATCH_SYMBOLS`.
- No new config. Schema head stays `008`.
- The Nasdaq client and the insider watch rules.

## Tests

Part 1:
- More than 100 symbols per adjustment fetch in chunks. The fake records calls and their concurrency, and no call exceeds `DAILY_BATCH_SYMBOLS`.
- The maximum concurrent calls equals `PEAD_FETCH_CONCURRENCY` when there are more chunks than that, measured with a fake that blocks on an event.
- One failing chunk is retried alone: the call log shows the other chunks fetched exactly once.
- A chunk failing twice gives `unavailable` with reason `bars_unavailable: ReadTimeout (after 1 retry)` and no message text.
- The merged result is identical to the sequential baseline on the existing fixtures: the same events and counts.

Part 2:
- Two catalog sources, the first slower than the deadline. The second still prepares `ok`, the first is `unavailable`/`timeout`, and the total is about one deadline, not two.
- With both fast, post-processing order and outputs are unchanged. The existing catalog tests pass unmodified.

## Docs

- `docs/apriori-alphas.md`, the PEAD live probe section: describe concurrent chunked fetching with per-chunk retry and the type-name reason.
- `docs/production.md`: one incident note (11-05/06/09 PEAD unavailable; 11-09 also cost the insider-watch decision).
- The `CLAUDE.md` line describing the catalog probe ("fail-closed on Nasdaq/SIP gaps") stays accurate. Add nothing unless a statement there becomes false.

## Rulings (controller)

- **Concurrency, not a bigger budget.** Raising the timeout hides the scaling with page size and holds the scan lock longer.
- **8 workers as a module constant.** It is an implementation bound like `DAILY_BATCH_SYMBOLS`, not operator policy.
- **Concurrent sources rather than per-source deadlines.** This keeps the scan-lock bound at one window.
- **Deferred:** preparing before the open, a larger design. Also deferred: the intermittent Nasdaq 403 seen from this host, which already fails closed.
