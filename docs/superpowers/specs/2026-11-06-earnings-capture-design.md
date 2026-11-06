# Dated earnings-calendar capture and the PEAD bars retry

Date: 2026-11-06. Operator request (2026-11-06): pursue pre-event bets with free data; "Go ahead" on the
recommended first steps: (1) a dated capture of the forward earnings calendar, with the PEAD bars retry in the
same PR, and (2) a development read of the uncertainty-conditioned pre-earnings run-up (its own spec and PR).
Design calls are mine under the operator's standing design trust.

## 1. Why

Every pre-earnings rule needs to know, at decision time, which names were *scheduled* to report and when.
The desk's 2016–2026 Nasdaq page archive was fetched after the fact: its dates are the dates reports actually
happened, and its consensus fields are today's values, not the values a trader saw. No free source sells a
dated history of the forward calendar. The only clean asset is one the desk captures itself, dated as taken,
and every earnings season not captured is lost for good. The clean prospective sample for the run-up rule
starts with the Q4 2026 season (late January to March 2027), so capture must be running well before then.

Separately, on 2026-11-05 a single 10-second Alpaca read timeout skipped that session's PEAD decision:
`research/apriori/pead_live.py` fetches daily bars once and fails the whole session closed on any exception.
The fetch is a GET, which the desk may retry; it should retry once.

## 2. Goals

1. Once per New York weekday, after 06:00 ET and outside the suggestion-scan windows, fetch the Nasdaq
   earnings calendar page of every weekday from today through today + `horizon_days` (28 calendar days),
   and journal one dated capture: every row as served, parsed into a fixed column list, with each page's
   fetch time and body digest. Keep the raw bodies privately on disk.
2. A capture is all-or-nothing: it is journaled only when every target page was accepted. A failed pass
   journals nothing and the next poll (30 minutes later) retries the whole date.
3. Readiness component `earnings_capture`: ready while the latest capture is at most 4 days old.
4. The live PEAD decision retries each of its two daily-bar fetches once before failing the session.

## 3. Non-goals and anti-goals

- No consumer yet. The earnings blackout keeps its own cached lookups; no card, gate, risk rule or scan reads
  the capture in this PR. The run-up study and any later lane read it separately.
- No backfill: past forward calendars cannot be fetched, and the archive is not a capture.
- No 8-K Item 2.02 matching. EDGAR acceptance times are permanent and can be fetched at read time.
- No Telegram output, no migration (schema head stays `008_alpha_pipeline`), no change to the blackout.
- Anti-goal: a transient Nasdaq failure must not page the operator. Only a stale capture is not ready.
- Anti-goal: never record a partial capture as if complete.

## 4. Data source and acceptance

Endpoint: the existing `NASDAQ_EARNINGS_CALENDAR_URL` with `params={"date": YYYY-MM-DD}` and the existing
`NASDAQ_REQUEST_HEADERS` (generic browser user agent; nothing about the operator is sent). A page is accepted
exactly as `NasdaqEarningsCalendar.reported_rows` accepts one: HTTP 200, a JSON object body and
`status.rCode == 200`. An accepted page with `data: null` or no rows is an accepted empty day (the forward
calendar is empty a few weeks out; 2027-01-14 served zero rows on 2026-11-06).

Forward rows (sampled 2026-11-06) carry `symbol`, `name`, `marketCap`, `fiscalQuarterEnding`, `epsForecast`,
`noOfEsts`, `lastYearRptDt` (`MM/DD/YYYY` or `N/A`), `lastYearEPS` and `time`. A peak-season page is about
180 rows and 40 KB.

A new parser `parse_forward_rows(payload, day) -> list[ForwardRow]` lives beside `parse_calendar_payload` in
`agent/earnings.py` and reuses its `_number`, `_count` and `_parse_timing` rules. `ForwardRow` is a frozen
dataclass: `symbol`, `timing` (`EarningsTiming`), `fiscal_quarter` (`str | None`), `eps_forecast`,
`n_estimates`, `last_year_report_date` (`date | None`; `N/A` or unparseable is `None`), `last_year_eps`,
`market_cap` (`float | None`, from `"$126,374,027,000"`). Rows without a symbol are skipped and counted.
Duplicate symbols on a page are kept as served.

## 5. The capture

Module `agentic_trader/data/earnings_capture.py`, modelled on `data/insider_capture.py`.

**Schedule (`EarningsCaptureWorker.run_once(now) -> status`).** In order:

| Condition (New York time) | Status | Effect |
| --- | --- | --- |
| Saturday or Sunday | `weekend` | nothing |
| inside a suggestion-scan window (`in_scan_window`, as the insider capture uses) | `scan_window` | nothing |
| before 06:00 | `waiting` | nothing |
| today's capture already journaled (in memory, or by the journal after a restart) | `present` | nothing |
| otherwise | `captured` | one capture of today |

NYSE holidays that fall on weekdays are captured like any weekday. The capture date is fixed when the pass
starts. Targets are the weekdays in `[capture_date, capture_date + horizon_days]`, requested in date order,
one request per second. Each target gets at most one retry, after 5 seconds, on a transport error, HTTP 429
or HTTP 5xx; any other unaccepted response fails at once. If a scan window opens between two requests the
pass stops and journals nothing (`scan_window`). If any target is still unaccepted the pass raises
`CaptureIncomplete` naming the target date and the reason, and journals nothing.

**Journal.** Stream `earnings/calendar`, event key `earnings/calendar/<capture_date>`, new
`EventKind.EARNINGS_CALENDAR_CAPTURE` (`earnings_calendar_capture`). Recorded once per capture date under the
store's lock; a second record of the same key is a no-op that returns `False` (the insider store's pattern).
Payload:

```json
{
  "capture_date": "2026-11-06",
  "started_at": "2026-11-06T11:02:00+00:00",
  "completed_at": "2026-11-06T11:02:31+00:00",
  "source": "nasdaq-calendar",
  "horizon_days": 28,
  "columns": ["symbol", "timing", "fiscal_quarter", "eps_forecast", "n_estimates",
              "last_year_report_date", "last_year_eps", "market_cap"],
  "pages": [{"date": "2026-11-06", "fetched_at": "...", "as_of": "Fri, Nov 6, 2026",
             "rows": 114, "skipped": 0, "body_sha256": "..."}],
  "rows": {"2026-11-06": [["PBR", "after_hours", "Sep/2026", 1.38, 2, "2025-11-06", 0.82, 126374027000.0]]}
}
```

`as_of` is the page's `data.asOf` string or `null`. Rows are arrays in `columns` order (dates ISO, missing
values `null`) to keep the journal compact: about 270,000 row-captures and 20–25 MB a year, against a 737 MB
journal today.

**Raw bodies.** Before journaling, each accepted body is written gzip-compressed and owner-only to
`<state>/data/earnings_calendar/<capture_date>/<target_date>.json.gz` (`state_directory()`, outside the
repository). `body_sha256` is the digest of the uncompressed bytes as received. A crash between the files and
the journal leaves files without an event; the next pass overwrites them and journals.

**Daemon wiring.** `config.earnings_capture` (`EarningsCaptureConfig`: `enabled: bool = False` in code,
`horizon_days: int = 28` with `7 <= horizon_days <= 42`, `extra="forbid"`); `config/config.yaml` ships
`enabled: true`; `load_config` passes the YAML block. `cli/commands/service.py` starts
`run_earnings_capture(copilot, config)` when enabled and cancels it on shutdown like the insider capture.
The loop polls every 1,800 seconds until shutdown and never raises into the daemon.

## 6. Readiness

`HealthComponent.EARNINGS_CAPTURE` (`earnings_capture`), registered like `insider_capture`: an
`earnings_capture_enabled` flag on `ReadinessService`, an observation age limit of 4 days, and the name added to
`READINESS_CHECK_NAMES`. Each poll observes:

- ready, `"<status>; latest capture <date> (<pages> pages, <rows> rows)"`, while the latest capture date is at
  most 4 days before today (New York; Friday's capture is 3 days old on Monday, 4 on a Tuesday after a
  Monday holiday);
- not ready, `"<status>; stale: latest capture <date> (<age> days)"`, after that;
- with no capture yet: ready `"not_due; no capture yet"` while the status is `waiting`, `scan_window` or
  `weekend` and no pass has run in this process; otherwise not ready `"no capture yet"`.

A failed pass (`CaptureIncomplete` or any exception) logs the exception type name and observes the same
freshness rule with `"failed: <TypeName>"` as the status, so a transient failure stays ready while a recent
capture exists and goes not ready only once the capture is stale. This differs on purpose from the insider
capture, which observes every failed poll as not ready. Exception text is never journaled or observed.

`earnings_capture` is an ordinary component under quiet hours: overnight incidents are held for the morning
summary.

## 7. PEAD bars retry

In `pead_live.live_events`, each of the two `fetch_daily_many` calls (adjusted, then raw) goes through one
helper that retries once after `BARS_RETRY_DELAY_SECONDS` (2.0; the sleep is injectable for tests) on any
exception, logging the first failure's type name (`pead_bars_retry`). If the retry also fails, the session is
unavailable as today, with the reason `bars_unavailable: <Type>: <message> (after 1 retry)` bounded to
`REASON_MAX_CHARS`. A success on the retry continues normally. No other behaviour changes.

## 8. Tests

- Parser: a recorded forward row parses every column; `N/A`, blanks, `$` and comma amounts, `(…)` negatives
  and a bad `lastYearRptDt` behave as section 4 says; a row without a symbol is skipped and counted.
- Worker: each status of the table in section 5 with an injected clock; the target list (weekdays only,
  inclusive of both ends); pacing; a retry on 503 then 200 succeeds; 429 twice fails; a 404 or a non-JSON
  body fails at once; a scan window opening mid-pass journals nothing; one failed target journals nothing and
  writes the payload only after every page is accepted; `data: null` is an accepted empty day.
- Store (PostgreSQL integration, `test_*` database): the payload round-trips, a second record of the same key
  returns `False` and adds no row, and the raw files are written owner-only and decompress to bytes whose
  SHA-256 is the journaled digest.
- Readiness: the freshness rule at ages 3, 4 and 5 days, `not_due` before the first pass, a failed pass while
  fresh stays ready, and the age-limit and check-name registration.
- PEAD: a first `ReadTimeout` then success yields `status="ok"` with both fetches called twice at most; two
  failures give the reason with `(after 1 retry)`; no retry when the first call succeeds.
- Config: the YAML block is loaded (`load_config` passes it), unknown keys are refused, bounds hold.

## 9. Docs

`docs/production.md`: a section on the capture (schedule, files, readiness rule and its difference from the
insider capture, the runbook line "a stale capture means lost calendar days; nothing trades on it"), the
readiness table row and the PEAD retry. `CLAUDE.md` contract 8: one sentence on the capture and its readiness.
`docs/alpha-roadmap.md`: a short "pre-event program (2026-11-06)" section recording the research verdict and
order (capture first; run-up development read; the EDGAR veto layer and a release exit in preview; analyst
days; drop list), without local paths.

## 10. Rollout and rollback

Merge on operator approval, then the controlled restart. Verify: `earnings_capture` is ready (`not_due` or
`captured`), the first `earnings/calendar/<date>` event exists after 06:00 ET on the next weekday with about
21 pages, and the raw files are present. Rollback to `106b20b` leaves the `earnings/calendar` events in the
journal; the plan checks whether any reader parses every journal row's `kind` into `EventKind`, and the PR
states the result (a hazard if one does, none otherwise).
