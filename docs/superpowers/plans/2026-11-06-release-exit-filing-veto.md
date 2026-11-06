# Release Exit and Filing Veto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two preview-first, pre-event risk controls. A daily release exit journals (and, in `enforce`, closes)
native equity positions that would otherwise be held through an earnings report. A `filing_veto` entry rule
blocks long cards after negative EDGAR filings; it reads a new daemon filings capture.

**Architecture:**
- **Filing veto.** It has three parts:
  - a pure parser and classifier (`data/edgar_filings.py`);
  - a journaled capture with a lookup and a daemon worker (`data/filings_capture.py`), modelled on the
    insider and earnings captures;
  - a `risk/` rule wired exactly where `corporate_action` is.
- **Release exit.** It has two parts:
  - a pure decision function plus a service (`execution/release_exit.py`) that journals one pass per date
    and calls `PositionCloseService.close_signal` only in `enforce`;
  - a 5-minute daemon interval job.
- **Measurement.** Both feed descriptive `cards outcomes` blocks.

**Tech Stack:** Python 3.14, uv, pydantic config, SQLAlchemy async journal (`WorkflowStore`), httpx, APScheduler, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-release-exit-filing-veto-design.md`

## Global Constraints

- **Rollout:** both controls ship `preview` in `config/config.yaml`; the code defaults are `off` /
  `capture: false`.
- **No migration:** schema head stays `008_alpha_pipeline`. New `EventKind` string values only.
- **No POST/PATCH/DELETE retry.** EDGAR GETs retry once after 5 s, only on a transport error, 429 or 5xx.
- **Fail-safe evidence:**
  - missing, stale or uncovered filing evidence never refuses;
  - an `unknown` release status never closes;
  - a veto never applies to a short.
- **No halt or review:** nothing here changes the trading halt or calls a lifetime review.
- **Type names only:** readiness details, Telegram text and journal reasons carry type names (or HTTP status
  or fixed strings), never exception text.
- **EDGAR access:**
  - `EDGAR_USER_AGENT` is read from the environment at worker start. Its value never appears in logs,
    journal, tests or docs.
  - Hosts are exactly `www.sec.gov` and `data.sec.gov`.
  - 2 requests/second.
- **Tests:** temporary SQLite or the `test_*` PostgreSQL; network blocked (inject fake HTTP).
- **Quality:** `uv run pre-commit run --all-files` passes (ruff C408/PLC0415 strict, mypy).
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **The release-exit boundary on Friday.** With N = Monday, a Monday pre-market report exits Friday, and a
   Monday after-hours report does not exit Friday. Covered in Task 5.
2. **Unverified live lookup with a fresh capture.** The capture alone decides `would_close`. When both are
   unusable the status is `unknown`, never `none`. Covered in Task 5.
3. **Ticker collisions and `BF-B`/`BF.B`.** A symbol under two CIKs is unmapped, never guessed. Covered in
   Task 1.
4. **`8-K` with items `"2.02,9.01"`.** It is not adverse. Items are compared as exact tokens, so `"2.050"`
   does not match `2.05`. Covered in Task 1.
5. **A daemon restart after the pass journaled but before a close.** The pass is not re-journaled, and a
   repeated close is a no-op through the stable request ID. Covered in Task 5.

---

### Task 1: EDGAR filings parser and classifier (pure)

**Files:**
- Create: `agentic_trader/data/edgar_filings.py`
- Test: `tests/data/test_edgar_filings.py`

**Interfaces:**
- Produces:
  - `class FilingCategory(StrEnum)`: `LATE_FILING = "late_filing"`, `ADVERSE_8K = "adverse_8k"`,
    `OFFERING = "offering"`.
  - `ADVERSE_8K_ITEMS: frozenset[str] = frozenset({"2.04", "2.05", "2.06", "4.02", "5.01"})`
  - `LATE_FILING_FORMS = frozenset({"NT 10-K", "NT 10-Q", "NT 20-F", "NT 10-K/A", "NT 10-Q/A", "NT 20-F/A"})`
  - `OFFERING_FORMS = frozenset({"424B4", "424B5"})`
  - `classify(form: str, items: Sequence[str]) -> FilingCategory | None`
  - `@dataclass(frozen=True) class MatchedFiling`, with:
    - fields: `symbol: str`, `category: FilingCategory`, `form: str`, `items: tuple[str, ...]`,
      `filing_date: date`, `acceptance: str | None`, `accession: str`;
    - `describe() -> str`: `"8-K Items 2.05, 2.06"`, `"NT 10-K"` or `"424B5"`;
    - `to_payload() -> dict`, plus `@classmethod from_payload(d) -> MatchedFiling` (round trip).
  - `parse_recent(payload: Mapping[str, Any], symbol: str, since: date) -> list[MatchedFiling]`
    - Reads `payload["filings"]["recent"]` (columnar lists).
    - `items` is a comma-separated string: split it and strip each token.
    - Keeps classified filings with `filing_date >= since`, newest first.
    - Raises `ValueError` on missing keys or mismatched column lengths.
  - `ticker_ciks(payload: Mapping[str, Any], symbols: Sequence[str]) -> tuple[dict[str, int], list[str]]`
    - Returns the mapped symbols and the unmapped ones.
    - Normalize the same way as `research.insider_filings.symbol_ciks` (`BF-B` ↔ `BF.B`); reuse it if its
      signature fits, otherwise extract the shared normalizer.
    - A symbol listed under two CIKs is unmapped.

- [ ] **Step 1: Write failing tests.** They are listed below, with the spec's exact values.
  - `test_classify_late_filing_forms`: `classify("NT 10-K", ())` and `classify("NT 10-Q/A", ())` give
    `LATE_FILING`; `classify("10-K", ())` is None.
  - `test_classify_adverse_8k_exact_items`:
    - `("8-K", ("2.02", "9.01"))` is None;
    - `("8-K", ("2.05", "9.01"))` is `ADVERSE_8K`;
    - `("8-K/A", ("4.02",))` is `ADVERSE_8K`;
    - `("8-K", ("2.050",))` is None.
  - `test_classify_offering_excludes_424b2_424b3`: `"424B5"` and `"424B4"` give `OFFERING`; `"424B2"` and
    `"424B3"` are None.
  - `test_parse_recent_filters_window_and_sorts_newest_first`:
    - use a 4-row columnar fixture (`form`, `items`, `filingDate`, `acceptanceDateTime`,
      `accessionNumber`) with `since=date(2026,9,7)`;
    - only qualifying rows on or after `since` are kept, newest first;
    - `items` parse to tuples.
  - `test_parse_recent_rejects_mismatched_columns`: `ValueError`.
  - `test_ticker_ciks_maps_class_shares_and_drops_collisions`:
    - payload `{"0": {"cik_str": 1, "ticker": "BF-B"}, "1": {"cik_str": 2, "ticker": "XX"}, "2": {"cik_str": 3, "ticker": "XX"}}`;
    - for `symbols=["BF.B","XX","ZZ"]` the result is `({"BF.B": 1}, ["XX", "ZZ"])`.
  - `test_matched_filing_payload_round_trip` and `test_describe`.
- [ ] **Step 2:** `uv run pytest tests/data/test_edgar_filings.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `agentic_trader/data/edgar_filings.py`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Stage only. The controller commits.

### Task 2: Filings capture — client, store, lookup, config, readiness, daemon worker

**Files:**
- Create: `agentic_trader/data/filings_capture.py`
- Modify:
  - `agentic_trader/data/insider_capture.py`: widen `_fetch`'s host check to `{"www.sec.gov", "data.sec.gov"}`
    and keep everything else.
  - `agentic_trader/execution/durable.py`: `EventKind.FILINGS_CAPTURE = "filings_capture"`.
  - `agentic_trader/config.py`: `FilingVetoConfig`, the `AppConfig.filing_veto` field and its `load_config`
    wiring, and `"filings_capture"` in `READINESS_CHECK_NAMES`.
  - `agentic_trader/diagnostics/readiness.py`: `HealthComponent.FILINGS_CAPTURE`, a constructor flag
    `filings_capture_enabled`, and `FILINGS_CAPTURE_MAX_AGE_SECONDS = 4 * 86400`.
  - `agentic_trader/agent/copilot.py`: pass the readiness flag, as `earnings_capture_enabled` is passed.
  - `agentic_trader/cli/commands/service.py`: start or cancel the worker, as `start_earnings_capture` does.
    Reuse the corporate-actions symbol-list builder, unchanged.
  - `docs/production.md`: a "Filings capture" section and runbook, beside "Earnings calendar capture".
- Test:
  - `tests/data/test_filings_capture.py`
  - `tests/config/test_filing_veto_config.py`
  - `tests/integration/test_filings_capture_postgres.py`
  - the existing readiness and quiet-hours tests that pin component names

**Interfaces:**
- Consumes: Task 1's `MatchedFiling`, `parse_recent` and `ticker_ciks`, and the insider capture's
  `_fetch`/`edgar_pacer` helpers.
- Produces:
  - `FilingVetoConfig`:
    - `capture: bool = False`, `entry_block: EntryBlockMode = "off"` (with the YAML-`off` validator);
    - `late_filing_days: int = 30`, `adverse_8k_days: int = 60`, `offering_days: int = 7`, all
      `ge=1, le=120`;
    - `pre_scan_minutes: int = 30` (`ge=10, le=120`), `max_age_seconds: int = 345600` (`gt=0`);
    - `extra="forbid"`;
    - the method `windows() -> dict[FilingCategory, int]`.
  - `FILINGS_STREAM = "filings/capture"`, `TICKERS_URL`, `SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik:010d}.json"`,
    and `COVERAGE_READY_FRACTION = 0.9`.
  - `async capture_filings(fetch, symbols, *, today, lookback_days, pace) -> dict`
    - `fetch` is an injected `async (url) -> bytes`.
    - It returns the spec §4.1 payload: `requested`, `covered`, `unmapped`, `failed` (`{symbol: reason}`),
      `ticker_map_sha256`, `fetched_at`, `lookback_days` and `filings` (`{symbol: [payload…]}`).
    - A failed ticker map raises `FilingsCaptureFailed(reason)`.
    - A per-symbol failure lands in `failed`; the pass continues.
  - `FilingsCaptureStore(workflows)`, with:
    - `async recorded(key) -> bool`;
    - `async record(key, payload) -> bool`, which journals once under a lock;
    - `async latest() -> dict | None`.
  - `@dataclass(frozen=True) class FilingsLookup`:
    - fields: `status: Literal["ok", "stale", "unavailable"]`, `as_of: datetime | None`, `covered: frozenset[str]`,
      `filings: Mapping[str, tuple[MatchedFiling, ...]]`;
    - methods: `covers(symbol) -> bool` and `for_symbol(symbol) -> tuple[MatchedFiling, ...]`;
    - `@classmethod build(latest_payload, now, max_age_seconds)`.
  - `FilingsCaptureWorker.run_once(now) -> str`, returning one of the statuses `weekend`, `scan_window`,
    `waiting`, `present`, `captured` or `missing_user_agent`.
    - Slots are `"open"` (the first poll ≥ 06:00 ET) and `"pre-HHMM"` (at each suggestion scan time minus
      `pre_scan_minutes`).
    - The key is `filings/capture/{et_date}/{slot}`.
    - A slot whose time has passed runs once, at the first poll after it.
    - A missed earlier slot on the same date is skipped when a later slot is due.
    - It never runs inside a scan window.
  - `run_filings_capture_worker(...)`: polls every 300 s and calls `readiness.observe(HealthComponent.FILINGS_CAPTURE, ok, detail)`.
    - `ok` while the newest pass with `len(covered) >= 0.9 * (len(requested) - len(unmapped))` is within
      `max_age_seconds`.
    - `detail` gives `covered`, `unmapped` and `failed` counts.

- [ ] **Step 1: Write failing tests.**
  - Config: defaults; the YAML `off` spelling; bounds; `load_config` reads a `filing_veto:` block.
  - Capture with a fake `fetch`:
    - a ticker map plus 3 symbols: one with a qualifying 8-K, one with none, one returning 503 twice;
    - check `covered`, `failed["SYM"] == "http_503"` and the empty list for the clean symbol;
    - an unmapped symbol;
    - a ticker-map failure raises;
    - retry happens only on transport/429/5xx (a 404 is not retried);
    - pacing goes through the injected `pace`.
  - Host check: `_fetch` accepts `data.sec.gov` and still rejects others.
  - Lookup:
    - `ok`, `stale` (older than `max_age_seconds`), `unavailable` (no event);
    - `covers` is case-insensitive.
  - Worker slots:
    - 05:59 gives `waiting`;
    - 06:01 gives `captured` for `open`;
    - a second call is `present`;
    - at 10:06 with scans `["10:35", "14:35"]` and `pre_scan_minutes=30` it gives `captured` for
      `pre-1005`;
    - inside a scan window it gives `scan_window`;
    - Saturday gives `weekend`;
    - no user agent gives `missing_user_agent`, with zero fetches.
  - Readiness:
    - the 90% coverage rule;
    - the component is registered and in `READINESS_CHECK_NAMES`;
    - it is an ordinary quiet-hours component.
  - PostgreSQL: concurrent idempotent `record`.
- [ ] **Step 2:** Run the new tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Keep raw bodies out of the journal; only matched filings are journaled.
- [ ] **Step 4:** Run the new tests, the affected readiness and config tests, and
  `TEST_POSTGRES_URL=… uv run pytest tests/integration/test_filings_capture_postgres.py --run-postgres`.
  Expected: PASS.
- [ ] **Step 5:** Stage only.

### Task 3: `filing_veto` rule — risk/, evaluator, tap gate, provenance, card line

**Files:**
- Modify:
  - `agentic_trader/risk/rules.py`: `RiskRule.FILING_VETO`, the `FilingLike` protocol, `blocking_filing` and
    `filing_veto_block`.
  - `agentic_trader/risk/__init__.py`: the exports.
  - `agentic_trader/agent/evaluator.py`: gate step 2d after corporate actions, the
    `LLMTradeEvaluation.filings` field, and the `filings` block threaded through `_rejected` and the
    deterministic card, as `corporate_actions` is.
  - `agentic_trader/agent/copilot.py`, mirroring every `corporate_actions` site:
    - the per-scan `FilingsLookup` build (from `FilingsCaptureStore.latest()`);
    - `_admit_candidate` and the send-phase plumbing;
    - `decision_provenance["filings"]`;
    - a tap-gate reason `_filing_veto_reason` inside `_tap_gate_reason`.
  - `agentic_trader/notifier/telegram_bot.py`: `_filing_veto_line`, used where `_corporate_action_line` is.
  - Docs:
    - `docs/risk-policy.md`: the rule table row, evaluator order and tap-gate order.
    - `CLAUDE.md` contract 1: one sentence after the `corporate_action`/`macro_lockout` text.
- Test: `tests/risk/test_rules.py`, `tests/agent/test_evaluator.py`,
  `tests/agent/test_card_freshness_tap.py` and `tests/notifier/` card-line tests.

**Interfaces:**
- Consumes: Task 1's `MatchedFiling` (it satisfies `FilingLike`), and Task 2's `FilingsLookup` and
  `FilingVetoConfig.windows()`.
- Produces:
  - `blocking_filing(filings, symbol, today, *, windows: Mapping[str, int]) -> FilingLike | None`
    - Returns the newest filing whose `today - filing_date` is between 0 and `windows[category]`.
    - Symbols match case-insensitively.
  - `filing_veto_block(filings, symbol, today, *, windows) -> Rejection | None`
    - The reason is exactly `f"Filing veto: {SYM} filed {describe} on {date} ({relative_days(date, today)}) — within the {N}-day {category} window."`.
  - The provenance block shapes are exactly as in spec §4.3.
  - Card line: `"🗂 Filing veto (preview): {reason} — card unchanged"`. In enforce the card never
    exists, so there is no enforce line.

- [ ] **Step 1: Write failing tests.**
  - Rules:
    - windows inclusive at 30, 60 and 7, with the edge day in and the day after out;
    - the newest filing wins;
    - a different symbol does not match;
    - the reason text matches exactly.
  - Evaluator:
    - long + preview + blocking filing: the card passes with `filings.would_block == True`;
    - long + enforce: rejected with `RiskRule.FILING_VETO`;
    - short + enforce: no block, and `filings` is None;
    - `stale`, `unavailable` and `uncovered` give their blocks and never refuse;
    - `off` gives no block;
    - futures give no block.
  - Tap gate: enforce refuses a long tap with a blocking filing; preview does not.
  - Card line: present in preview with `would_block`; absent otherwise.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement, mirroring `corporate_action` site by site. Admission does not apply the rule.
- [ ] **Step 4:** Run the affected suites. Expected: PASS.
- [ ] **Step 5:** Stage only.

### Task 4: Filing-veto measurement — scan journal and `cards outcomes`

**Files:**
- Modify:
  - `agentic_trader/agent/copilot.py`: the `scan_candidates_ranked` candidate dict (near the `news` field)
    gains `filings`.
  - `agentic_trader/research/` outcomes module that builds `scan_supply`, or a new
    `filing_veto_summary(events, frame)` beside it; `agentic_trader/cli/commands/cards.py` adds it to the
    summary as `summary["filing_veto"]`.
  - `docs/card-evidence.md`: the `filing_veto` block.
- Test: the scan-journal tests and the outcomes tests beside `scan_supply`'s.

**Interfaces:**
- Consumes: Task 3's lookup plumbing (in-memory, no I/O).
- Produces:
  - Each ranked candidate's `filings` is `{status, would_block, category}` for long equity candidates,
    `None` otherwise.
  - `filing_veto_summary(events, frame) -> dict` returns:
    - `candidates`: `n` and `by_status`;
    - `would_block`: total, `by_category` and `rate`;
    - `cards`: `n_would_block`, `mean_net_excess_r` and `n_matured`.

- [ ] **Step 1: Write failing tests.**
  - The journal candidate block for a long candidate with a blocking filing, a short (`None`) and an
    uncovered symbol.
  - The summary over two synthetic scan events: rates, categories, and card means from a frame with
    `net_excess_r` and a `filings_would_block` column.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Stage only.

### Task 5: Release exit — decision, capture reader, service, daemon job, `cards outcomes`

**Files:**
- Create:
  - `agentic_trader/execution/release_exit.py`
  - `tests/execution/test_release_exit.py`
- Modify:
  - `agentic_trader/data/earnings_capture.py`: `EarningsCaptureStore.reports_between`.
  - `agentic_trader/execution/durable.py`: `EventKind.RELEASE_EXIT_PASS = "release_exit_pass"` and
    `EventKind.RELEASE_EXIT_CLOSE = "release_exit_close"`.
  - `agentic_trader/config.py`: `ReleaseExitConfig` and the `AppConfig.release_exit` wiring.
  - `agentic_trader/agent/copilot.py`: construct the service and add the `run_release_exit()` entry point.
    Reuse `_catalog_strategy_ids` for scope.
  - `agentic_trader/cli/commands/service.py`: a `scheduler.add_job(copilot.run_release_exit, "interval", minutes=5)`
    job, in the same style as the auto-entry digest.
  - `agentic_trader/cli/commands/cards.py` plus the outcomes module: `summary["release_exit"]`.
  - Docs:
    - `docs/production.md`: a "Release exit" section.
    - `CLAUDE.md` contract 2: one sentence.
    - `docs/card-evidence.md`: the `release_exit` block.
- Test: `tests/execution/test_release_exit.py`, `tests/data/test_earnings_capture.py` (the reader),
  `tests/config/` and `tests/integration/test_release_exit_postgres.py` (idempotent pass journaling).

**Interfaces:**
- Consumes:
  - `NasdaqEarningsCalendar.next_earnings(symbol, now, horizon_days) -> EarningsLookup` (`event`,
    `verified`);
  - `EarningsTiming`;
  - `PositionCloseService.close_signal(signal_id, *, request_id, context) -> str`;
  - the lifetime service's trading calendar.
- Produces:
  - `ReleaseExitConfig`: `mode: EntryBlockMode = "off"` (YAML-`off` validator), `minutes_before_close: int = 30`
    (`ge=10, le=120`) and `extra="forbid"`.
  - `EarningsCaptureStore.reports_between(start, end, *, today, max_age_days=EARNINGS_CAPTURE_MAX_AGE_DAYS) -> CapturedReports | None`
    - `CapturedReports` holds `capture_date` and `rows: Mapping[date, tuple[(symbol, timing), …]]`.
    - It returns `None` when there is no capture, the capture is too old, or a date in [start, end] is
      absent from the capture's `rows` keys.
  - `exits_today(report_date: date, timing: EarningsTiming, today: date, next_day: date) -> bool`
    implements spec §3.3 exactly.
  - `decide(symbol, live: EarningsLookup | None, captured: CapturedReports | None, today, next_day) -> ReleaseDecision`
    - `ReleaseDecision` carries `status` (one of `exit`, `none`, `unknown`), `report_date`, `timing`,
      `sources` and `live_verified`.
    - `exit` if any qualifying report exists in either source.
    - `none` only if the live lookup is verified, or the capture is usable for [today, next_day].
    - `unknown` otherwise.
  - `ReleaseExitService.run(now) -> str`, returning `off`, `closed_market`, `waiting`, `present`, `done` or
    `failed`.
    - It journals the pass (stream `release-exit`, key `release-exit/{date}`) together with the preview
      notices in one transaction.
    - In `enforce` it then calls `close_signal` and appends a `RELEASE_EXIT_CLOSE` event per position, plus
      a notice on any non-submitted result.
    - At or after the close, a date with no recorded pass queues one `release-exit-unavailable/{date}`
      notice (type name).

- [ ] **Step 1: Write failing tests.**
  - `exits_today`. Use Friday 2026-11-06 with N = Monday 2026-11-09:

    | Report | Exits Friday |
    | --- | --- |
    | Fri after hours | yes |
    | Fri unspecified | yes |
    | Fri pre-market | no |
    | Sat (any timing) | yes |
    | Mon pre-market | yes |
    | Mon unspecified | yes |
    | Mon after hours | no |
    | Tue pre-market | no |

  - `decide`:
    - live verified with no event, capture None: `none`;
    - live unverified with no event, a fresh capture without the symbol: `none` with sources `capture`;
    - live unverified, capture None: `unknown`;
    - live shows Mon after hours, capture shows Mon pre-market: `exit` (conservative), sources both.
  - `reports_between`: an old capture gives None; a missing date gives None; rows are grouped by date.
  - `run`:
    - preview journals one pass with native and exempt entries (a catalog PEAD position is `exempt`,
      scope `pead`) and one notice per `would_close`, and calls no close;
    - a second `run` the same day gives `present`;
    - before the close minus 30 minutes it gives `waiting`;
    - an early close (13:00) runs at 12:30;
    - enforce calls `close_signal` once with `request_id == "release-" + sha256(...)[:32]` and the spec's
      context, and appends `RELEASE_EXIT_CLOSE`;
    - a refusal string gives a notice with no halt;
    - a restart after the pass journaled re-calls nothing that is not idempotent;
    - an exception before the journal: the next tick retries; after the close, one unavailable notice.
  - The PostgreSQL pass record is idempotent under concurrency.
  - `cards outcomes` `release_exit`: counts by status, scope and date from synthetic events.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the new and affected suites, and the PostgreSQL test. Expected: PASS.
- [ ] **Step 5:** Stage only.

### Task 6: Ship config, roadmap, final docs

**Files:**
- Modify:
  - `config/config.yaml`: `filing_veto: {capture: true, entry_block: preview, …}` and
    `release_exit: {mode: preview, minutes_before_close: 30}`, each with a dated rollout comment like
    `corporate_actions`'.
  - `docs/alpha-roadmap.md`: the pre-event program bullet reads "built 2026-11-06, in preview".
  - `CLAUDE.md` contract 8: the `filings_capture` readiness sentence beside `earnings_capture`.
- Test: the config-loading test that reads the shipped `config/config.yaml`.

- [ ] **Step 1:** Write a failing test: the shipped config loads with `filing_veto.entry_block == "preview"`,
  `filing_veto.capture is True` and `release_exit.mode == "preview"`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Edit the config and docs.
- [ ] **Step 4:** Run the full unit suite and the PostgreSQL integration suite. Expected: PASS.
- [ ] **Step 5:** Stage only.
