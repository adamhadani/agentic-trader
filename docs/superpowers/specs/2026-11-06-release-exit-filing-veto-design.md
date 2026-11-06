# Pre-event risk controls: release exit and filing veto (preview)

Date: 2026-11-06. Operator: "start next workstream" (step 3 of the
[pre-event program](../../alpha-roadmap.md#pre-event-program-2026-11-06)), under the standing design trust
("go with your recommended approaches"). Both controls ship in `preview`; `enforce` is a later operator call
on journaled evidence, as with `corporate_action`, `book_sizing` and `card_policy`.

## 1. Why

The pre-event research (2026-11-06) found that the documented pre-event effects a free-data desk can use are
mostly **negative**. They are reasons not to hold a long, not reasons to open one. The run-up rule, the one
positive bet, failed its development read the same day (L43).

The two exposures that remain are:

1. **Holding a native position through an earnings release.** The `earnings_blackout` rule counts 0–7
   calendar days and gates entries only. A native position opened eight or more days ahead can still be held
   through a report, and a bracket stop cannot cap an opening gap. In 2026, 13.3% of S&P 500 reports moved
   the stock 10% or more.
2. **Opening a long right after a negative filing.** These filings have documented negative drift:
   - NT 10-K/10-Q late-filing notices: −2% to −3% over 5 days;
   - 8-K Items 2.04, 2.05, 2.06, 4.02 and 5.01: −1% to −3.7% over 30–90 days;
   - offering prospectuses: −4.6% for convertibles at announcement.

   All of them are free and timestamped on EDGAR.

A veto at one to two cards a session cannot be powered (about 100 vetoed cards are needed to detect −2%,
which takes years). So these controls are adopted on prior evidence plus preview counts, the same way the
`corporate_action` rule was.

## 2. Goals, non-goals, anti-goals

**Goals**
- **Release exit.** A once-per-session pass, shortly before the close, finds every open native equity
  position whose next earnings release falls before the next session's open. In `preview` it journals
  that and sends the operator one notice per position and report. In `enforce` it closes the position
  through `PositionCloseService`.
- **Filing veto.** A new entry-risk rule, `filing_veto`, built like `corporate_action`. A daemon capture
  journals each covered symbol's recent negative EDGAR filings. In `preview`, a long equity card whose
  issuer has one in its window carries a "would block" block in its provenance and a card line. In
  `enforce`, the rule refuses the card.
- **Measurement.**
  - The rate at which the veto would block across all ranked equity candidates, from the scan journal.
  - The release exit's would-close count, from its journal.

  `cards outcomes` reports both descriptively.

**Non-goals**
- No new card source, no signal or strategy change, and no historical backtest. Vetoes are adopted on prior
  evidence.
- Not in v1:
  - lock-up expiries (they need prospectus text parsing);
  - PDUFA dates;
  - the scheduling-delayer veto (it needs several seasons of captures);
  - 8-K Item 2.02 acceptance-time matching.
- No change to the `earnings_blackout` rule or its data source.

**Anti-goals**
- **A feed outage never reads as clearance.**
  - Missing, stale or uncovered filing evidence never refuses an entry.
  - An unverified earnings lookup never closes a position, but it is never reported as "no report" either.
- **A veto never blocks a short entry.**
- **No halt.** Neither control changes the trading halt, and neither adds a page outside the alert window.
- **No new migration.** Schema head stays `008`.
- **No automatic retry** of a POST, PATCH or DELETE.

## 3. Release exit

### 3.1 Scope

- **In scope:** tracked `EXECUTED` positions (`db.get_active_positions()`) that are equity and come from
  native cards.
- **Native** here means the position is none of:
  - a catalog card (strategy in `copilot._catalog_strategy_ids`, PEAD or insider watch);
  - a probe (`decision_provenance["paper_probe"]`);
  - an alpha-versioned card (`alpha_policy` or `alpha_version` set).
- **Both directions:** a stop cannot cap an opening gap either way.
- **Other tracked equity positions are journaled as `exempt`,** with their kind, so the exposure is counted,
  but they are never closed. Their frozen research exits must not change.
- **Non-equity positions are skipped silently.**

### 3.2 When

- **The pass runs once per New York trading day.** It runs on the first tick of a five-minute interval job
  at or after `close − release_exit.minutes_before_close`.
- **Early closes are honored:** the close comes from the trading calendar, as `publish_auto_entry_digest`
  reads it.
- **Default timing.** `minutes_before_close` defaults to 30, valid from 10 to 120. With the default the pass
  runs at 15:30 ET, or 12:30 on an early close.
- **One pass per date:** the journal key `release-exit/{et_date}` makes it idempotent across restarts.
- **Restart after the close:** if the daemon restarts after that day's close, it skips the date and
  journals nothing for it.

### 3.3 Which reports trigger an exit today

Let T be today and N the next trading day. A position exits today if its symbol has a report with either of
these:

- date T, timing after hours or unspecified (a pre-market report on T has already happened);
- date after T and before N (a weekend or holiday date), or date N with timing pre-market or unspecified.

A report on N after hours is handled by N's own pass.

### 3.4 Evidence: the union of two sources

1. **Live:** `NasdaqEarningsCalendar.next_earnings(symbol, now, horizon_days=(N − T).days)`. This is the
   source the blackout uses, so the same per-date cache serves both.
2. **Captured:** the latest `earnings/calendar` capture, if its `capture_date` is within
   `EARNINGS_CAPTURE_MAX_AGE_DAYS` (4) of T and its `rows` map holds the dates in [T, N].
   - A new reader, `EarningsCaptureStore.reports_between(start, end) -> CapturedReports | None`, returns
     the capture date and that capture's rows for each date in [start, end].
   - It returns `None` when there is no capture, the capture is too old, or any date in the range is
     missing from the capture.

**How the sources combine:**
- A report found by either source counts. If the two disagree on timing, the more conservative one wins:
  any reading that exits today does.
- The position's status is one of:
  - `would_close` (preview) or `close` (enforce);
  - `none`: both sources usable and neither shows a qualifying report;
  - `unknown`: no qualifying report, the live lookup unverified, and the capture unusable.
- An `unknown` position is never closed.

### 3.5 Preview

**The journal.** One event per date, journaled once:
- stream `release-exit`, key `release-exit/{et_date}`, new `EventKind.RELEASE_EXIT_PASS`;
- payload: `mode`, `trading_day`, `next_trading_day`, `close_at`, the capture date used (or null) and one
  entry per in-scope or exempt equity position;
- each entry has `signal_id`, `symbol`, `direction`, `scope` (`native` or the exempt kind), `status`,
  `report_date`, `timing`, `sources` (`live`, `capture` or both) and `live_verified`.

**The notice.** For each `would_close` native position, one `MESSAGE` notice in the same transaction, with
key `release-exit/{signal_id}/{report_date}`:

> Release exit (preview): {SYMBOL} reports {date} {before open|after close|timing unspecified}. In enforce the
> desk would close this {long|short} at about {HH:MM} ET today. Nothing was sent to the broker; /flatten
> closes it manually.

**Quiet hours.** The notice is an ordinary outbox message, and the pass runs inside market hours.

### 3.6 Enforce

**The close.** For each `close` position the pass calls
`PositionCloseService.close_signal(signal_id, request_id=…, context=…)`:
- `request_id` is `"release-" + sha256(f"{scope}/{signal_id}/{report_date}")[:32]`, stable across ticks and
  restarts;
- `context` is `{"reason": "release_exit", "report_date", "timing", "sources"}`.

`close_signal` keeps its own durable intent, recovery and refusals (contract 2). The returned text is
recorded in the pass entry, as `close_result`.

**A refusal or failure.**
- Any non-submitted outcome queues a notice:

  > Release exit could not close {SYMBOL}: {reason}. The position keeps its stop; /flatten closes it
  > manually.

- No halt and no review. The holding-expiry review path is not reused, because a release exit is a
  precaution and the position is still protected.

**The pass event** is appended before any close call. Each close result is then appended as its own event
(key `release-exit/{et_date}/{signal_id}`, kind `RELEASE_EXIT_CLOSE`), so a crash between the two leaves a
journaled intent. On restart the pass is already recorded. The close request ID makes a repeated
`close_signal` call a no-op ("Close already submitted…").

### 3.7 Failure of the pass itself

If the pass raises before journaling, it is retried on the next tick until the close. A New York date whose
pass never completes queues one `release-exit-unavailable/{et_date}` notice: the type name only, sent once,
at the close. Readiness is unchanged.

### 3.8 Config

```yaml
release_exit:
  mode: preview          # off | preview | enforce; code default off
  minutes_before_close: 30
```

`ReleaseExitConfig` uses `extra="forbid"` and the YAML-`off` validator from `CorporateActionsConfig`.

## 4. Filing veto

### 4.1 Evidence capture

- **The worker.** `agentic_trader/data/filings_capture.py`, started by the daemon when
  `filing_veto.capture` is true. The code default is false; `config.yaml` ships it true.
- **Symbols.** The same list the corporate-actions worker builds: universe equities, held equity positions
  and `corporate_actions.extra_symbols`. Reuse the existing builder; do not copy it.
- **Passes.**
  - The first poll after 06:00 ET on each New York weekday.
  - A refresh `filing_veto.pre_scan_minutes` (default 30) before each configured suggestion scan time.
  - Never inside a suggestion-scan window.

  A pass runs once per slot. The slots are kept in memory, and the journal is used for idempotency on
  restart.
- **Sources.**
  - The ticker map: `https://www.sec.gov/files/company_tickers.json`, one request per pass. Map with the
    semantics of `research.insider_filings.symbol_ciks` (`BF-B` → `BF.B`). A symbol under two CIKs is
    unmapped; never guess.
  - Per mapped CIK: `https://data.sec.gov/submissions/CIK{cik:010d}.json`, reading only `filings.recent`.
- **HTTP behaviour.**
  - Reuse the insider capture's EDGAR HTTP helpers. Widen the host allow-list to exactly `www.sec.gov` and
    `data.sec.gov`.
  - `EDGAR_USER_AGENT` is required. Without it, the worker observes `missing_user_agent` and sends nothing.
  - Two requests per second.
  - One retry after 5 s, only on a transport error, HTTP 429 or 5xx.
  - The existing timeouts and body cap.
- **Per-symbol outcome:** `covered`, `unmapped` or `failed`. Failures carry the type name or status only.
- **The journal.** Each completed pass is journaled once:
  - stream `filings/capture`, key `filings/capture/{et_date}/{slot}`, new `EventKind.FILINGS_CAPTURE`;
  - payload: `requested`, `covered`, `unmapped`, `failed`, `ticker_map_sha256`, `fetched_at`,
    `lookback_days`, and `filings`;
  - `filings` maps each covered symbol to its matched filings: `form`, `items`, `filing_date`,
    `acceptance`, `accession`.
- **Matching.** Only filings in a veto category and dated within `max(category windows)` (default 60 days)
  of the pass date are kept. A covered symbol with none has an empty list: that is evidence of "no filing",
  and it is what lets the rule clear a symbol.

### 4.2 Categories

| Category | Forms | Window before the decision date (calendar days, `filing_date` inclusive) |
| --- | --- | --- |
| `late_filing` | `NT 10-K`, `NT 10-Q`, `NT 20-F` and their `/A` | `late_filing_days`: 30 |
| `adverse_8k` | `8-K` or `8-K/A` whose `items` include 2.04, 2.05, 2.06, 4.02 or 5.01 | `adverse_8k_days`: 60 |
| `offering` | `424B4`, `424B5` | `offering_days`: 7 |

- **Excluded forms.** `424B2` and `424B3` are left out: structured-note and resale or merger prospectuses
  would mark large banks and acquirers permanently.
- **Dates.** `filing_date` is the window's date, which avoids EDGAR's acceptance-time zone ambiguity.
  `acceptance` is recorded but not used.
- **Point in time by construction.** A pass records only what EDGAR serves at fetch time, so nothing in it
  can be from the future.

### 4.3 The rule

- **Where.** In `agentic_trader/risk/rules.py`:
  - `RiskRule.FILING_VETO`;
  - a `FilingLike` protocol;
  - `blocking_filing(filings, symbol, today, *, windows) -> FilingLike | None`, the most recent qualifying
    filing;
  - `filing_veto_block(...) -> Rejection | None`, with the reason "Filing veto: {SYM} filed {form}{ Items
    …} on {date} ({relative days}) — within the {N}-day {category} window."
- **Long entries only.** A short never evaluates it.
- **Where it applies.** The same places `corporate_action` applies: the evaluator gate (after
  `corporate_action`), the tap gate and re-evaluate. Admission does not apply it.
- **Evidence status.** It comes from the latest pass: `ok`, `stale` (older than `max_age_seconds`, default
  4 days), `unavailable` (no pass) or `uncovered` (the symbol was not covered). Only `ok` with a blocking
  filing and `entry_block: enforce` refuses.
- **Provenance.** `decision_provenance["filings"]` is one of:
  - `{status, mode}` when the lookup is not ok;
  - `{status: "uncovered", mode, as_of}`;
  - `{status: "ok", mode, as_of, would_block, category, form, items, filing_date, accession, reason}`.
- **Card line.**
  - Preview with `would_block`: "Filing veto (preview): {reason} — card unchanged".
  - Ok with a matched filing outside every window: no line.
- **Re-pricing** copies provenance unchanged, as it does for corporate actions.

### 4.4 Measurement

- **The scan journal.** Each ranked equity candidate in `scan_candidates_ranked` gains
  `filings: {status, would_block, category}`, for longs only (a short has `null`). It is computed from the
  same in-memory lookup, so no I/O is added to the scan.
- **`cards outcomes` gains a `filing_veto` block.** Descriptive only, never a gate:
  - the ranked candidates' would-block rate by category and status counts;
  - the cards with `would_block`, with their matured `net_excess_r_v1` mean and n.
- **`cards outcomes` gains a `release_exit` block:** would-close and exempt counts by date, from the
  `release-exit` journal.

### 4.5 Readiness and config

**Readiness.**
- A new component, `filings_capture`, is ready while the latest pass that covered at least 90% of its mapped
  symbols is at most `max_age_seconds` old.
- Its detail names the covered, unmapped and failed counts, with type names only.
- It registers like `insider_capture` (flag, age limit, `READINESS_CHECK_NAMES`), and is an ordinary
  component under quiet hours.

**Config.**

```yaml
filing_veto:
  capture: true          # code default false
  entry_block: preview   # off | preview | enforce; code default off
  late_filing_days: 30
  adverse_8k_days: 60
  offering_days: 7
  pre_scan_minutes: 30
  max_age_seconds: 345600
```

`FilingVetoConfig` uses `extra="forbid"`, the YAML-`off` validator, windows from 1 to 120 days and
`pre_scan_minutes` from 10 to 120.

## 5. Docs

- `docs/risk-policy.md`: the rule table row, evaluator order and tap-gate order.
- `docs/production.md`: filings capture and release exit sections with runbooks.
- `CLAUDE.md`: contract 1 (the `filing_veto` sentence beside `corporate_action`) and contract 2 or 8 (the
  release exit).
- `docs/card-evidence.md`: the two `cards outcomes` blocks.
- `docs/alpha-roadmap.md`: the pre-event program bullet now says "built, in preview".

## 6. Left to the builder

Helper names inside modules, test layout, and how the five-minute job reaches the trading calendar, as long
as it reuses the lifetime service's calendar.
