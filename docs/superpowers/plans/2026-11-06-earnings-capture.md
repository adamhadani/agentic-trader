# Earnings Calendar Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Journal one dated capture of the Nasdaq forward earnings calendar per New York weekday, and make the live PEAD decision retry its daily-bar fetch once.

**Architecture:** A daemon worker (`data/earnings_capture.py`, modelled on `data/insider_capture.py`) fetches every weekday page from today to today + 28 days after 06:00 ET, outside scan windows, and journals them all-or-nothing as `earnings/calendar/<date>` with gzip raw bodies under the state directory. A readiness component goes not-ready only when the capture is stale. `pead_live.live_events` wraps its two bar fetches in a one-retry helper.

**Tech Stack:** Python 3.14, httpx (`MockTransport` in tests), pydantic config, SQLAlchemy journal (`WorkflowStore`), pytest (+ `--run-postgres` on a `test_*` database).

**Spec:** `docs/superpowers/specs/2026-11-06-earnings-capture-design.md`

## Global Constraints

- GETs only; at most one retry per request; never retry POST/PATCH/DELETE.
- No migration: schema head stays `008_alpha_pipeline`. `EventKind` values are stored as text.
- Nothing journaled, observed or logged carries exception text from the Nasdaq request: type names only.
- The request headers are the existing `NASDAQ_REQUEST_HEADERS`; nothing about the operator is sent.
- No local absolute paths in committed code, config or docs; raw files live under `state_directory()`.
- Tests: explicit fixture config, temporary SQLite, no network (use `httpx.MockTransport`), PostgreSQL only via `--run-postgres` on a `test_*` database (`test_agentic_trader_codex_20260915` locally).
- `uv run pre-commit run --all-files` passes (ruff C408/PLC0415 are strict).
- Implementers stage changes and report; the controller commits.

## Review Focus

1. A pass interrupted half-way (one page fails after its retry, or a scan window opens) journals nothing and leaves no event; a later pass records the date once. Test in Task 2.
2. A restart after today's capture does not capture again (`present` from the journal). Test in Task 2.
3. A Nasdaq outage over a weekend: Friday's capture keeps readiness ready through Tuesday (4 days) and not past it. Test in Task 3.
4. A first `ReadTimeout` on the raw-bar fetch (the second call) is retried, not only the first call. Test in Task 4.
5. `load_config` actually passes the `earnings_capture:` YAML block (a past bug silently dropped `corporate_actions:`). Test in Task 1.

---

### Task 1: Forward-row parser and the config block

**Files:**
- Modify: `agentic_trader/agent/earnings.py` (add `ForwardRow`, `parse_forward_rows`, `_report_date`)
- Modify: `agentic_trader/config.py` (add `EarningsCaptureConfig`, `AppConfig.earnings_capture`, `load_config` wiring)
- Modify: `config/config.yaml` (add the block)
- Test: `tests/agent/test_earnings.py`, `tests/config/test_earnings_capture_config.py`

**Interfaces:**
- Produces: `ForwardRow` (frozen dataclass: `symbol: str`, `timing: EarningsTiming`, `fiscal_quarter: str | None`, `eps_forecast: float | None`, `n_estimates: int | None`, `last_year_report_date: date | None`, `last_year_eps: float | None`, `market_cap: float | None`) and `parse_forward_rows(payload: object, day: date) -> tuple[list[ForwardRow], int]` (rows, rows skipped for a missing symbol). `EarningsCaptureConfig(enabled: bool = False, horizon_days: int = 28)` with `ge=7, le=42` and `extra="forbid"`; `AppConfig.earnings_capture`.

- [ ] **Step 1: Write the failing tests.** In `tests/agent/test_earnings.py`: `test_parse_forward_rows_reads_every_column` with the PBR row from the spec (`"lastYearRptDt": "11/06/2025"`, `"marketCap": "$126,374,027,000"`, `"epsForecast": "$1.38"`, `"noOfEsts": "2"`, `"time": "time-after-hours"`) asserting `ForwardRow("PBR", EarningsTiming.AFTER_HOURS, "Sep/2026", 1.38, 2, date(2025, 11, 6), 0.82, 126374027000.0)`; `test_parse_forward_rows_missing_values` (`"N/A"`, `""`, `"($0.95)"` → -0.95, `lastYearRptDt` `"13/45/2025"` → `None`); `test_parse_forward_rows_skips_rows_without_symbol` (returns `(rows, 1)`); `test_parse_forward_rows_null_data_is_empty` (`{"data": None}` → `([], 0)`). In `tests/config/test_earnings_capture_config.py`: defaults (`enabled` False, 28), bounds (6 and 43 refused), unknown key refused, and `load_config` on a YAML file containing `earnings_capture: {enabled: true, horizon_days: 21}` yields those values (follow how existing tests call `load_config` with a temporary YAML).
- [ ] **Step 2: Run them; expect import/attribute failures.** `uv run pytest tests/agent/test_earnings.py tests/config/test_earnings_capture_config.py -q`
- [ ] **Step 3: Implement.** `parse_forward_rows` mirrors `parse_calendar_payload` (same `_number`, `_count`, `_parse_timing`); `last_year_report_date` parses `%m/%d/%Y`, anything else `None`; `market_cap` uses `_number`. `EarningsCaptureConfig` docstring cites the spec; `load_config` passes `cfg_dict.get("earnings_capture")` as `insider_watch` is passed. `config/config.yaml` gets `earnings_capture:` with `enabled: true` and `horizon_days: 28`.
- [ ] **Step 4: Run the tests; expect PASS**, then `uv run pytest tests/agent tests/config -q`.
- [ ] **Step 5: Stage** the files and report.

### Task 2: The capture module and its store

**Files:**
- Create: `agentic_trader/data/earnings_capture.py`
- Modify: `agentic_trader/execution/durable.py` (add `EventKind.EARNINGS_CALENDAR_CAPTURE = "earnings_calendar_capture"`)
- Test: `tests/data/test_earnings_capture.py`, `tests/integration/test_earnings_capture_postgres.py`

**Interfaces:**
- Consumes: Task 1's `ForwardRow`, `parse_forward_rows`, `EarningsCaptureConfig`; `NASDAQ_EARNINGS_CALENDAR_URL`, `NASDAQ_REQUEST_HEADERS`, `EarningsTiming`; `in_scan_window` (`research/setups/card_stats_worker.py`); `write_private` (`research/short_volume.py`); `state_directory()`; the insider store's lock-and-key pattern (`data/insider_capture.py:InsiderCaptureStore.record`).
- Produces (all in `agentic_trader/data/earnings_capture.py`):
  - constants `CAPTURE_STREAM = "earnings/calendar"`, `CAPTURE_AFTER_ET = time(6, 0)`, `POLL_SECONDS = 1800`, `REQUEST_INTERVAL_SECONDS = 1.0`, `RETRY_DELAY_SECONDS = 5.0`, `CAPTURE_MAX_AGE_DAYS = 4`, `CAPTURE_MAX_AGE_SECONDS = 4 * 86400`, `COLUMNS` (the spec's eight names, in order);
  - `class CaptureIncomplete(RuntimeError)` carrying `target: date` and `reason: str` (reason is an HTTP status, `body is not JSON`, `body is not a JSON object`, `status.rCode <x>` or an exception type name — never exception text);
  - `def default_capture_directory() -> Path` (`state_directory() / "data" / "earnings_calendar"`);
  - `def capture_targets(capture_date: date, horizon_days: int) -> list[date]` (weekdays in `[capture_date, capture_date + horizon_days]`);
  - `class EarningsCaptureStore(workflows: WorkflowStore, directory: Path)` with `async def recorded(self, capture_date: date) -> bool` (by event key), `async def latest(self) -> dict[str, Any] | None` (the newest event's `capture_date`, `pages` count and total `rows` count; uses `workflows.events(CAPTURE_STREAM, limit=1)`), `async def record(self, payload: Mapping[str, Any], bodies: Mapping[date, bytes]) -> bool` (writes `<directory>/<capture_date>/<target>.json.gz` with `write_private(gzip.compress(body))` in a thread, then appends once under a lock; `False` when the key exists);
  - `async def capture_calendar(client: httpx.AsyncClient, capture_date: date, targets: Sequence[date], *, clock: Callable[[], datetime], pace: Callable[[], Awaitable[None]], sleep: Callable[[float], Awaitable[None]], stop: Callable[[], bool]) -> tuple[dict[str, Any] | None, dict[date, bytes]]` — `(None, {})` when `stop()` turned true between requests; raises `CaptureIncomplete` otherwise on a failed target;
  - `class EarningsCaptureWorker(client, store, config, *, clock=None, pace=None, sleep=None)` with `async def run_once(self, now: datetime | None = None) -> str` (statuses `weekend`, `scan_window`, `waiting`, `present`, `captured`) and `async def freshness(self, now: datetime, status: str) -> tuple[bool, str]` (spec section 6);
  - `async def run_earnings_capture_worker(config: AppConfig, store: EarningsCaptureStore, observe: Callable[[bool, str], Awaitable[None]], shutdown: asyncio.Event, *, transport: httpx.AsyncBaseTransport | None = None, clock=None, pace=None, sleep=None) -> None`.

- [ ] **Step 1: Write the failing unit tests** in `tests/data/test_earnings_capture.py`, with an `httpx.MockTransport` keyed on the `date` query parameter, a fake store (or the SQLite workflow store fixture the insider tests use), an injected clock and no-op `pace`/`sleep`:
  - `test_targets_are_weekdays_inclusive` — `capture_targets(date(2026, 11, 6), 28)` starts at 2026-11-06, ends at 2026-12-04, has 21 dates, none on a weekend.
  - `test_run_once_statuses` — Saturday → `weekend`; 10:31 ET on a weekday with scan time `10:35` → `scan_window`; 05:59 ET → `waiting`; 06:01 ET → `captured`; a second call the same day → `present`; a new worker over the same store the same day → `present` with no request made.
  - `test_payload_shape` — after a capture the journaled payload has `capture_date`, `started_at`, `completed_at`, `source == "nasdaq-calendar"`, `horizon_days`, `columns == list(COLUMNS)`, one `pages` entry per target with `date`, `fetched_at`, `as_of`, `rows`, `skipped`, `body_sha256` (the SHA-256 of the served bytes), and `rows[target]` as arrays in `COLUMNS` order with ISO dates.
  - `test_null_data_is_accepted_empty_day` — `{"data": null, "status": {"rCode": 200}}` gives `rows == 0` and an empty list.
  - `test_retry_once_on_503_then_success`, `test_429_twice_fails_and_journals_nothing`, `test_404_fails_without_retry`, `test_non_json_body_fails` — the failing cases raise `CaptureIncomplete` with the target and reason and leave no event and no files.
  - `test_scan_window_mid_pass_journals_nothing` — `stop()` turns true after the third request; `run_once` returns `scan_window`; no event.
  - `test_failed_pass_then_success_records_once` — first pass fails on one target, second pass (same day) succeeds; exactly one event.
  - `test_freshness` — no capture and status `waiting` before any pass → `(True, "not_due; no capture yet")`; after a pass ran with none → `(False, "no capture yet")`; latest capture 4 days old → ready; 5 days → `(False, "...stale: latest capture <date> (5 days)")`.
  - `test_failed_poll_observes_freshness` — `run_earnings_capture_worker` with a transport that always returns 503 and a store holding a 1-day-old capture observes `ready=True` with a detail starting `"failed: CaptureIncomplete"`; with no capture it observes `False`.
- [ ] **Step 2: Write the failing PostgreSQL test** `tests/integration/test_earnings_capture_postgres.py` (copy the setup of `tests/integration/test_insider_capture_postgres.py`): record a payload with two bodies → `True`; record it again → `False` and one row; `latest()` returns the date and counts; each file is mode 0600 and `gzip.decompress(file) ` hashes to the payload's `body_sha256`.
- [ ] **Step 3: Run; expect failures.** `uv run pytest tests/data/test_earnings_capture.py -q` and `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration/test_earnings_capture_postgres.py --run-postgres -q`
- [ ] **Step 4: Implement** per the spec sections 4–6. Acceptance mirrors `NasdaqEarningsCalendar.reported_rows`. Retry only on `httpx.TransportError`, 429 and 5xx, after `sleep(RETRY_DELAY_SECONDS)`. `pace()` defaults to a 1-second spacer. The worker caches `_done` (capture date) in memory and checks `store.recorded(today)` once per new date. A failed poll in the run loop logs `earnings_capture_failed` with the type name only and observes `freshness(now, f"failed: {type(exc).__name__}")`.
- [ ] **Step 5: Rollback check.** Search the code for any reader that converts every journal row's `kind` into `EventKind` (for example `EventKind(row.kind)` or `EventKind(payload["kind"])` outside a known stream). Put the result (file:line, or "none") in the task report.
- [ ] **Step 6: Run the tests; expect PASS** (both commands above).
- [ ] **Step 7: Stage** and report.

### Task 3: Readiness, daemon wiring and the docs

**Files:**
- Modify: `agentic_trader/diagnostics/readiness.py` (`HealthComponent.EARNINGS_CAPTURE = "earnings_capture"`, `EARNINGS_CAPTURE_MAX_AGE_SECONDS = 4 * 86400`, `earnings_capture_enabled` flag and limit)
- Modify: `agentic_trader/config.py` (`READINESS_CHECK_NAMES` gains `"earnings_capture"`)
- Modify: `agentic_trader/agent/copilot.py` (pass `earnings_capture_enabled=config.earnings_capture.enabled`)
- Modify: `agentic_trader/cli/commands/service.py` (`run_earnings_capture(copilot, config)`, task start when enabled, cancel on shutdown like `insider_capture_task`)
- Modify: `docs/production.md`, `CLAUDE.md`, `docs/alpha-roadmap.md`
- Test: `tests/data/test_earnings_capture.py` (constant pin), `tests/config/test_quiet_hours_config.py` (registration), `tests/cli/test_service_earnings_capture.py` (new, modelled on `tests/cli/test_service_insider_capture.py`), `tests/workflows/test_journal_readiness.py` if it enumerates enabled components

**Interfaces:**
- Consumes: Task 2's `EarningsCaptureStore`, `default_capture_directory`, `run_earnings_capture_worker`, `CAPTURE_MAX_AGE_SECONDS`.

- [ ] **Step 1: Write the failing tests.** `test_max_age_constants_agree` (`EARNINGS_CAPTURE_MAX_AGE_SECONDS == earnings_capture.CAPTURE_MAX_AGE_SECONDS == 4 * 86400`); the quiet-hours registration test's `ReadinessService(...)` gains `earnings_capture_enabled=True` and still equals `READINESS_CHECK_NAMES`; a readiness test that an `earnings_capture` observation 4 days old is not counted stale by the age limit and one older is; `tests/cli/test_service_earnings_capture.py`: `run_earnings_capture` observes `HealthComponent.EARNINGS_CAPTURE` through `copilot.readiness.observe` and uses `default_capture_directory()`; the daemon starts no capture task when `earnings_capture.enabled` is false.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** the registration and wiring. `run_earnings_capture` builds `EarningsCaptureStore(copilot.db.workflows, default_capture_directory())` and an `observe` that calls `copilot.readiness.observe(HealthComponent.EARNINGS_CAPTURE, ok, detail)`.
- [ ] **Step 4: Docs.** `docs/production.md`: an "Earnings calendar capture" section beside the insider capture section (schedule, targets, all-or-nothing, raw files, readiness rule and why a failed poll stays ready while fresh, runbook line), the readiness component row, and a sentence on the PEAD bars retry next to the PEAD live decision text. `CLAUDE.md` contract 8: one sentence (`earnings/calendar/<date>` once per New York weekday after 06:00 ET outside scan windows, all-or-nothing, `earnings_capture` readiness stale after 4 days, nothing reads it yet). `docs/alpha-roadmap.md`: a short "Pre-event program (2026-11-06)" section: capture first; then the run-up development read on 2016–2026 that can only fail, prospective confirmation from the Q4 2026 season; the EDGAR negative-filing veto layer and a release exit for open native longs in preview; analyst days after an EDGAR count; dropped: the unconditional announcement premium, hold-through surprise bets, LLM backtests on historical text, index-inclusion, buyback/SEO/split/pre-FOMC drifts. No local paths.
- [ ] **Step 5: Run** `uv run pytest tests/diagnostics tests/config tests/cli tests/workflows tests/data -q`; expect PASS.
- [ ] **Step 6: Stage** and report.

### Task 4: PEAD bars retry

**Files:**
- Modify: `agentic_trader/research/apriori/pead_live.py`
- Test: `tests/research/apriori/test_pead_live.py`

**Interfaces:**
- Produces: `BARS_RETRY_DELAY_SECONDS = 2.0`; `live_events(..., sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)` (new keyword, default unchanged behaviour for callers); a private `_fetch_daily(bars, symbols, start, end, adjustment, sleep)`.

- [ ] **Step 1: Write the failing tests** using the module's existing fakes: `test_bars_retry_after_first_timeout` (the adjusted fetch raises `httpx.ReadTimeout` once, then succeeds → `status == "ok"`; the fake recorded two adjusted calls and one raw call; `sleep` awaited once with 2.0); `test_raw_fetch_retry` (the raw fetch fails once → `ok`); `test_bars_unavailable_after_retry` (both adjusted attempts fail → `status == "unavailable"`, reason starts `bars_unavailable: ReadTimeout` and ends `(after 1 retry)`, length ≤ `REASON_MAX_CHARS`); `test_no_retry_on_success` (`sleep` never awaited).
- [ ] **Step 2: Run; expect failures.** `uv run pytest tests/research/apriori/test_pead_live.py -q`
- [ ] **Step 3: Implement** `_fetch_daily` (one retry on any `Exception`, `logger.warning("PEAD daily bars fetch failed; retrying once", extra={"event": "pead_bars_retry", "reason": type(exc).__name__})`) and use it for both fetches; the final reason keeps the existing format plus ` (after 1 retry)` before truncation is applied to the whole string. Bound the suffix so it survives: build `f"bars_unavailable: {type(exc).__name__}: {exc}"`, truncate it to `REASON_MAX_CHARS - len(" (after 1 retry)")`, then append the suffix.
- [ ] **Step 4: Run** `uv run pytest tests/research/apriori/test_pead_live.py tests/screeners/test_earnings_drift.py -q`; expect PASS.
- [ ] **Step 5: Stage** and report.
