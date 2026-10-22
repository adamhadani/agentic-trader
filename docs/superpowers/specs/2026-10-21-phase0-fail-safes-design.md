# Phase 0 fail-safes: tier-1 calendar, UNKNOWN regime, journal-only evidence scans, index exclusion, complete scan journal

Date: 2026-10-21. Status: design, written under the operator's standing instruction ("go with your
recommended approaches") after the operator approved the holistic review's Phase 0 on 2026-10-21
([review README](../../holistic-review-2026-10-21/README.md), "Decisions and status"). It covers report
items RK-R1, RK-R2 (minimum part), CS-R1 (decision D2), CS-R2 (decision D4), CS-R3 and roadmap item 0.7
([report §3.1, §3.6, §5](../../holistic-review-2026-10-21/report.md)). Branch `feature/phase0-fail-safes`.
It must merge and deploy before the **2026-10-28 FOMC** (statement 14:00 ET, press conference 14:30 ET).
With the configured window of 60 minutes before and 30 after (`config/config.yaml:277-278`), the press
conference's window [13:30, 15:00] ET contains the 14:35 suggestion scan (`config/config.yaml:331`).
Every current-behaviour statement below was checked at `224fe00`.

## 1. Intent and why

Two problems share one week.

**Risk inputs fail open, silently.** The macro lockout has one source, ForexFactory's unofficial
"this week" JSON (`agentic_trader/constants.py:126`). A timeout, a non-200 response or a non-JSON body
leaves `events = []`, and that empty list is cached as a successful fetch for 30 minutes
(`agentic_trader/agent/calendar.py:187-221`). A non-200 is not even an exception: it is cached with no
log line (`calendar.py:194`, `:219-220`). Every lockout reader then sees "no event": the whole-scan check
(`agentic_trader/agent/copilot.py:809-831`), the evaluator gate (`agentic_trader/agent/evaluator.py:526-541`)
and the tap and admission preflight (`copilot.py:3962-3987`). The LLM is told "Lockout verified: CLEAR"
(`calendar.py:156-160`), and the card prints "Macro: Cleared" (`agentic_trader/notifier/telegram_bot.py:256,422`).

The regime fails the same way. All ten macro inputs are mandatory (`agentic_trader/agent/macro.py:411-413`).
When any one is missing, `get_regime` falls back to VIX-only rules at `risk_multiplier = 1.0`
(`agentic_trader/agent/regime.py:146-151,185-189`). That happens even when the missing input never
affects the stress level. When VIX itself is missing, `get_regime` raises (`regime.py:168-169`) at an
unguarded call (`copilot.py:780`). The scan then aborts with:
- no journal and no SCAN readiness observation;
- no `pead_decision`, although the docstring promises one for every decision (`copilot.py:730-735`);
- no 14:35 digest, because `publish_scan_digest` follows `run_scan` without `try/finally`
  (`agentic_trader/cli/commands/service.py:440-462`).

**The cards that are sent are not the population that is measured.** Three things cause this:
- The swing scan runs with `ScanBudget.FULL` every 4 hours from daemon start, including an immediate
  run (`service.py:581-593`). Its cards spend the same derived session budget as the 10:35 and 14:35
  slots (`copilot.py:1268-1283`).
- Broad-index ETFs (SPY, VTI and VOO) took 3 of the last 9 native card slots.
- Zero-candidate scans and collect-phase rejections leave no journal at all (`copilot.py:1591`,
  `:1754-1762`).

Phase 0 makes the risk inputs fail safe and visible. It makes card-sending scans the only scans that
send cards. It journals every scan that could have produced a card.

## 2. Goals, non-goals, anti-goals

Goals:

1. A digest-pinned static tier-1 calendar is authoritative for FOMC, CPI, PPI and Employment Situation
   instants. Lockouts use its union with the live feed. The live feed reports a typed status. Every
   scan journals the calendar's identity and status. The prompt and card never claim a verification
   that did not happen.
2. A missing decision input yields an explicit `UNKNOWN` stress state at a configured multiplier
   below 1.0, never 1.0×. A missing context input changes nothing. A missing VIX skips the scan and
   leaves a record: a journal event, the skipped PEAD decision, SCAN readiness and one notice per day.
   The digest still runs.
3. The scan journal's `regime` block carries the raw inputs, their sources and observation dates,
   which inputs were missing, and the New York clock regime (EDT or EST).
4. Swing and intraday scans send nothing (`ScanBudget.JOURNAL_ONLY`). Only the suggestion slots,
   operator `/scan`, the chat tool and Re-evaluate send cards.
5. Unrestricted scans never send a native card on a broad US equity index ETF. Such candidates are
   still collected, ranked and journaled.
6. Every unrestricted, non-dry, budgeted scan that had something to evaluate journals exactly one
   `scan_candidates_ranked` event. That includes zero-candidate, lockout-gated and regime-unavailable
   scans. Each event carries a collect funnel with per-rule rejection counts.

Non-goals (rulings, recorded for the PR):

- **No new regime sources.** Cboe VIX, a single-source Treasury curve and keyed FRED are roadmap item
  1.8. Phase 0 journals the mixed clocks; it does not fix them. Today the 2y comes from FRED's
  prior-day DGS2 and the 10y from Yahoo's live ^TNX (`macro.py:383-398`).
- **No `macro_inputs` readiness component.** See §5.2: it does not fit the readiness design cleanly
  today.
- **No change to the regime rules' calibration** (RK4, RK-R7), to what a tier-1 event is, or to the
  lockout window minutes. ForexFactory's "High" impact flag still never gates (`calendar.py:121`).
- **No change to `card_stats` or `card_policy`'s population.** Journal-only swing candidates are
  labelled exactly as today's swing candidates are, because `card_stats` reads every universe
  candidate (`agentic_trader/research/setups/outcomes.py:148-187`; `card_stats.py:139-143`). De-duplication is `card_stats`
  v2 (roadmap 1.2).
- **No correlation-dedup rule** (the D4 alternative) and no change to correlation groups.
- **No change to suggestion-scan ranking, budgets, the PEAD drift budget, admission or broker paths.**
- **No new schema.** No migration and no new `EventKind`: event `kind` is a free string column
  (`alembic/versions/005_execution_workflows.py:26`), so new payload fields need none either.

Anti-goals (what the builder must not do):

- Fabricate or carry forward a VIX value; read a missing decision input as benign; or let any default
  produce 1.0× without a measured `LOW` stress. The one exception is the explicit
  `regime.yield_curve_enabled: false` policy (§5.2).
- Cache a failed fetch as a success, or let readiness, incidents or journals carry a raw exception
  message (type names only).
- Page the operator for a condition that is already handled conservatively. 659 incident notices in
  33 days is already the operator-loop problem (report §3.1 CS7).
- Reuse `ScanBudget.NONE`. It means "no limits" and still sends cards (`agentic_trader/config.py:187-190`).
- Widen the intraday job beyond `non_universe_contracts`, or give it the universe.
- Add compatibility aliases. `is_in_lockout_window`, `get_macro_report` and the dead toggles go, and
  their callers are updated in the same change.
- Let a readiness degradation change the trading halt (CLAUDE.md contract 8).

## 3. Constraints

- CLAUDE.md contracts 1, 6, 7 and 8:
  - every entry-risk rule keeps one implementation in `agentic_trader/risk`;
  - a journal event and its notice intent share a transaction;
  - blocking provider reads stay off the event loop;
  - readiness is passive and computed per run.
- Operator policy lives in validated config read by `load_config` (`config.py:1109-1180`). All new keys
  sit in existing blocks (`risk`, `regime`, `scan`), so the loader needs no new block.
- Tests use the explicit fixture config (`tests/fixtures/config.yaml`), temporary SQLite and no network.
  The real v1 calendar file is read only by the tests that pin its data (§7.1).
- POST/PATCH/DELETE are never retried. Nothing here touches the broker.

## 4. Review claims checked at HEAD

Confirmed:
- the calendar fail-open, including the non-200 path, and the "Lockout verified: CLEAR" prompt (§1);
- every lockout reader;
- all ten macro inputs are mandatory, the 1.0× fallback, the unguarded regime call, the missing digest
  and the missing `pead_decision`;
- the five context inputs never affect stress: `compute_compound_macro_stress` reads VIX, the curve
  regime and the credit regime only, and its `inflation` argument is unused (`macro.py:207-280`);
- the curve regime reads the 10y−2y and 10y−3m slopes (`macro.py:139-156`), so its inputs are the 3m,
  2y and 10y;
- `credit_oas_enabled` and `inflation_breakeven_enabled` are read nowhere (`config.py:488-489`; grep);
- `ScanBudget` has only FULL, SESSION and NONE, and NONE still sends;
- the swing job's FULL budget and run at start;
- the journal condition at `copilot.py:1591`, and collect-phase rejections that are only logged.

The review is wrong or imprecise in these places:

1. **Index clones (report §3.1 CS3).** The report says "a dynamic name gets only the `dynamic` group
   (`copilot.py:612-617`)". In fact `_scan_groups` returns the configured groups *plus* `dynamic`
   (`copilot.py:614-617`). VOO is in the code-default `us_broad_market` group with SPY and IVV
   (`config.py:374`); `config/config.yaml` does not override `correlation_groups`. The real defect is
   two overlapping label sets: `us_broad_market` and `sector_etf_broad_equity`. The second is built
   from the universe's sectors (`config.py:1104-1107`) and holds SPY, QQQ, IWM, DIA and VTI
   (`config.yaml:98-102`). VTI and VOO share no group, so both could be carded on 10-20. Finding C08's
   fact check already said this; the report kept the old mechanism. The D4 exclusion makes the point
   moot for cards.
2. **"Never read as 1.0" (RK3).** `docs/risk-policy.md:61` is about a *non-finite* multiplier raising
   `ValueError`, so the 1.0× fallback does not literally contradict it. A different line is wrong:
   `docs/risk-policy.md:331` says the preflight's regime half "fails closed when VIX or macro data is
   unavailable". Only a missing VIX fails closed; missing macro data yields 1.0×. §8 fixes both lines
   to match the code.
3. **"Session gated" swing and intraday scans.** Both run with `asset_class="all"`
   (`copilot.py:686`; `service.py:584-589,602-606`). That session gate is open whenever futures trade
   (`agentic_trader/market/session.py:1127-1130`), about 23 hours a day. Under Alpaca execution such a
   scan then selects nothing: equities are skipped for the closed session and futures as not
   executable (`copilot.py:961-967`). `docs/production.md:22-24` ("when the equity session is open")
   is therefore imprecise. This matters for CS-R3: naive zero-candidate journaling would write
   overnight noise every 4 hours (§5.6).
4. **The intraday job is idle by construction, not merely low-yield.** Native strategies emit only 4h
   and 1h setups (`agentic_trader/screeners/strategies.py:337-349`), and the 15m filter removes them
   (`copilot.py:1092-1094`). Registry alphas come only from active and probe definitions
   (`agentic_trader/screeners/registry.py:119-129`), and the live map counts 0 active and only the PEAD
   catalog probe. The job also runs under the default `trigger=OPERATOR_SCAN` (`copilot.py:700`), which
   is harmless only because restricted scans never journal.
5. **Run at start (CS-R1).** The report recommends removing the swing job's immediate run. That run
   exists so the `scan` readiness component has an observation in each run (`service.py:589-591`;
   `docs/production.md:23-25`). Under `JOURNAL_ONLY` it can no longer spend a slot, so this spec keeps
   it (§5.4).
6. **VIX-only fallback detail.** The fallback re-fetches VIX through a second `_fetch_ticker_sync`
   (`regime.py:103-113,160-170`). It rounds to 2 decimals where `macro.py:283-293` rounds to 3. When
   the macro report failed because VIX was missing, the retry usually fails too and raises.

## 5. Design

### 5.1 Static tier-1 calendar and typed live status (RK-R1)

**File.** `config/calendar/us-tier1-v1.json`. It is immutable once merged: a moved or cancelled
release (as in the 2025 shutdown) or a new year becomes `us-tier1-v2.json`. The schema:

```json
{
  "schema_version": 1,
  "calendar_id": "us-tier1-v1",
  "timezone": "America/New_York",
  "valid_from": "YYYY-MM-DD",
  "valid_through": "YYYY-MM-DD",
  "retrieved_on": "YYYY-MM-DD",
  "events": [
    {"kind": "fomc_statement", "at": "YYYY-MM-DDT14:00:00-04:00", "source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
    {"kind": "fomc_press_conference", "at": "YYYY-MM-DDT14:30:00-04:00", "source_url": "…"},
    {"kind": "cpi", "at": "YYYY-MM-DDT08:30:00-05:00", "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm"}
  ]
}
```

- `kind` is a `Tier1Kind(StrEnum)` (new, `agent/calendar.py`): `fomc_statement`,
  `fomc_press_conference`, `cpi`, `ppi`, `employment_situation`. An SEP release shares the statement's
  instant; it is not a separate kind.
- `at` is an ISO-8601 instant with an explicit offset.
- `valid_through` is the last New York date through which **every** included series' published
  schedule is complete. It is set at data entry from the series with the shortest published horizon.
- Dates are not invented here. The plan's data-entry task fills `events` from the separately collected,
  verified scratch JSON. Each `source_url` must be the official schedule page the date came from.

**Loader.** `StaticTier1Calendar(BaseEconomicCalendar)` is built by
`StaticTier1Calendar.load(path, sha256)`. The loader never raises. A failure leaves the calendar in
status `invalid` with an error type name, and the desk falls back to the unknown policy below. The
loader validates, failing closed to `invalid`:
- the SHA-256 of the file bytes equals `risk.macro_calendar_sha256` when that key is set
  (`DigestMismatch`);
- the schema parses (pydantic, `extra="forbid"`; `ValidationError`);
- `calendar_id` equals the file stem, and `valid_from ≤ valid_through`;
- every `at` carries the UTC offset New York actually had at that instant (this catches DST typos), and
  falls within `[valid_from, valid_through]`;
- events are sorted and `(kind, at)` is unique;
- every `source_url` is `https` on `www.federalreserve.gov` or `www.bls.gov`.

A missing file is `FileNotFoundError`. A load failure is logged once, at ERROR, with the type name.

**Statuses.** `CalendarStatus(StrEnum)` (new): `ok`, `failed` (live feed only), `expired` and `invalid`
(static only). The static status is `expired` when today's New York date is outside
`[valid_from, valid_through]`. The static calendar is **verified** iff its status is `ok`.

**Typed live fetch.** `BaseEconomicCalendar.fetch_events()` returns a frozen `CalendarFetch` instead
of `list[MacroEvent]`. Its fields:
- `status`, `events`, `fetched_at`;
- `error`: a type name such as `ReadTimeout`, `UnexpectedStatus`, `JSONDecodeError` or
  `UnexpectedPayload`;
- `http_status`;
- `coverage`: the first and last item dates in the payload, any country;
- `unparsed_items`: the count of item parse failures, which today are silently dropped
  (`calendar.py:214-215`).

`ForexFactoryCalendar` caches a success for 30 minutes, as today. It never caches a failure as a
success: a failure is remembered as `failed` for a 5-minute retry backoff. The feed is reportedly capped
at 2 downloads per 5 minutes per IP (gap findings, "Macro lockout fails open"). A failure yields no live
events. The log line carries the type name, not `str(e)` (`calendar.py:217`). `MacroEvent` gains
`source` (`"static:us-tier1-v1"` or `"forexfactory"`) and `kind: Tier1Kind | None`. Live titles map to
a kind through the existing keywords (`calendar.py:19-37`). "press conference" is checked before
"statement".

**Union.** `UnionTier1Calendar(BaseEconomicCalendar)` (new) holds the static calendar, the live feed
and the unknown policy. It is the one calendar `TradingCopilot` builds, in place of
`ForexFactoryCalendar()` at `copilot.py:343`, and injects into `RiskEvaluator` (`copilot.py:426-433`).
`RiskEvaluator`'s fallback default (`evaluator.py:138`) and `backtest/engine.py:64` build it from
config. Replay's `_NoIoCalendar` (`agentic_trader/research/setups/replay.py:128-133`) becomes a
no-I/O union.

Its single read is `async check(now, pre_minutes, post_minutes) -> CalendarCheck`, a frozen result
with:
- `verified` (whether the static calendar is ok);
- `lockout`: the first tier-1 event whose window contains `now` (`risk.in_lockout_window`). Static
  events come first. Live tier-1 events with no static match are added, so a live-only event, such as
  an unscheduled FOMC action, still locks out;
- `upcoming`: the next 24 hours, merged;
- the static and live states, and `disagreements`.

A live and a static event **match** when they have the same kind within ±5 minutes. Within the live
`coverage`, a static event with no live match is `static_only`; a live tier-1 event with no static match
is `live_only`. Disagreements are evidence only. The union locks out on both kinds either way.
`is_in_lockout_window` and `get_upcoming_tier1_events` are removed and their callers moved to `check`;
the unused `EconomicCalendarProtocol` (`calendar.py:56-80`) is deleted.

**The rule stays in `risk/`.** `risk.macro_lockout` gains a required keyword:
`macro_lockout(event_title, event_at, now, limits, *, calendar_verified: bool)`.
`RiskLimits` gains `macro_calendar_unknown: Literal["open", "closed"]`, read in `from_config`
(`agentic_trader/risk/limits.py:68-69,116-117`). When `calendar_verified` is false and the policy is
`closed`, the rule refuses before the window test with one text: "Macro calendar unverified: the static
tier-1 calendar is {expired|invalid}; new entries refused (risk.macro_calendar_unknown: closed)."
Otherwise it behaves as today. Three callers apply it the same way, from one `check`:
- the whole-scan lockout (`copilot.py:809-831`), which today calls only the window predicate;
- the evaluator gate (`evaluator.py:523-541`);
- `_macro_lockout_reason` (`copilot.py:3973-3987`).

The evaluator runs one `check` per evaluation and renders the prompt from it. This keeps the one-clock
property its comment requires (`evaluator.py:385-387`).

**Unknown policy: default `open`.**
- Rationale: the desk's other calendar evidence fails open with an explicit label. The earnings blackout
  says "Unverified" (`docs/risk-policy.md:119-120`); stale or uncovered corporate-action evidence never
  refuses (CLAUDE.md, corporate actions).
- Expiry cannot arrive silently: `macro_calendar` readiness has been not ready for 30 days by then, and
  the monitor turns that into an incident with reminders (§5.1 readiness).
- `closed` turns a calendar-maintenance lapse into a de facto stop of all entries. Readiness may not do
  that (contract 8: degraded readiness "does not … automatically change the trading halt").
- The lockout binds on about 8 afternoons a year.
- Under `open` with an unverified static calendar, the union still locks out on live events when the
  feed is ok.

**Prompt and card.** The prompt is rendered from the `CalendarCheck`:
- Verified: it names the source, for example "Lockout CLEAR per static tier-1 calendar us-tier1-v1
  (sha256 1a2b3c4d), valid through YYYY-MM-DD; live cross-check (ForexFactory): ok|failed (Type)",
  then the 24-hour list.
- Unverified: "Lockout UNVERIFIED: static tier-1 calendar {expired|invalid}; live feed {ok|failed};
  treat scheduled-release timing as unknown". The string "verified" never appears without the
  `UNVERIFIED` prefix in this branch.
- `LLMTradeEvaluation` gains `macro_note: str | None`. The evaluator sets it when `check.verified` is
  false. The card then prints "Macro: Unverified — {note}" instead of "Cleared" (`telegram_bot.py:256,422`).
- `macro_clearance` (part of the LLM's JSON contract, `agent/prompts.py:73`) is unchanged.

**Journal.** Every scan journal event (§5.6) carries a top-level `calendar` block, written by
`CalendarCheck.journal_block()`. It always has every key:

```
calendar: {verified, unknown_policy,
           static: {calendar_id, sha256, pinned, status, valid_through, days_remaining, error},
           live:   {source, status, fetched_at, error, http_status, tier1_events, coverage},
           lockout: null | {title, at, kind, sources: ["static", "live"]},
           disagreements: [{side: "static_only"|"live_only", kind, at, title}]}   # at most 10
```

**Readiness.** `macro_calendar` is a computed check in `ReadinessService.report`, like
`trading_halt` and `entry_cancellation` (`agentic_trader/diagnostics/readiness.py:139-162`); it is not
an observed `HealthComponent`. The copilot passes a callable that returns the static state.
- Ready iff the static status is `ok` and `days_remaining ≥ risk.macro_calendar_renewal_days`
  (default 30).
- The detail is stable text, for example "us-tier1-v1 valid through YYYY-MM-DD (N days)",
  "us-tier1-v1 expires YYYY-MM-DD (N days); publish v2", "us-tier1-v1 expired YYYY-MM-DD; lockout
  unverified (policy open)" or "invalid: DigestMismatch".
- The live feed status is deliberately not part of readiness. A ForexFactory blip is handled, since the
  static calendar is authoritative, and is journaled per scan. Making it a check would add incident
  traffic and stop the monitor's retention from compacting the detail.
- The monitor already turns any not-ready check into a debounced incident
  (`agentic_trader/diagnostics/monitor.py:30-50`), so no monitor change is needed.

**Config** (`RiskConfig`, `config.py:401-407`):
- `macro_calendar_path: Path = "config/calendar/us-tier1-v1.json"`, resolved against `WORKSPACE_ROOT`
  like `shadow_ranker_artifact` (`config.py:207-215`);
- `macro_calendar_sha256: str | None = None`: 64 lowercase hex characters. `config/config.yaml` pins it;
  `None` loads unpinned and journals `pinned: false`;
- `macro_calendar_unknown: Literal["open", "closed"] = "open"`;
- `macro_calendar_renewal_days: int = Field(30, ge=1)`.

`tests/fixtures/config.yaml` points `macro_calendar_path` at a synthetic test calendar, so no
production-data date leaks into unrelated tests.

### 5.2 Regime fail-safes (RK-R2, minimum)

**Decision inputs are VIX, the 3m (^IRX), the 2y (FRED DGS2), the 10y (^TNX) and HY OAS (FRED
BAMLH0A0HYM2).** These are exactly what `compute_compound_macro_stress` and `calculate_yield_curve_spreads`'s
regime read (`macro.py:148-156,222-269`). **Context inputs are the 5y (^FVX), 30y (^TYX), DXY and both
breakevens (T10YIE, T5YIE).** They feed only the butterfly curvature, the inflation regime and display
(`macro.py:145-146,188-204`; `agentic_trader/presentation/formatters.py:396-434`;
`agentic_trader/agent/macro_explainer.py:61-65,188,209`; `regime.py:239-255`). The two tuples are
`DECISION_INPUTS` and `CONTEXT_INPUTS` in `macro.py`. A property test pins that the context inputs never
move the stress assessment (§7.2).

**Typed inputs.** `MacroInput` is a frozen dataclass with `name`, `value: float | None`, `source`
(`"yfinance:^VIX"`, `"fred:DGS2"`), `observed_on: date | None`, `decision: bool` and
`error: str | None`. The error is a type name, `no_observation` or `unexpected_payload`. Both
fetchers return it:
- the Yahoo helper's observation date is the last non-null Close bar's date. One helper replaces the
  duplicate in `regime.py:103-113`;
- `FredDataClient` records the observation date it already parses (`macro.py:333-341`). It returns
  `unexpected_payload` when the body is not a CSV, for example the HTML "Site Maintenance" page with
  HTTP 200 that the review observed. That page cannot be re-verified offline; on such a body the parser
  returns None today (`macro.py:328-351`).

**Evaluation.** `MacroIntelligenceEngine.evaluate()` replaces `get_macro_report()`. Its callers move
with it: `regime.py:148`, the `explain-macro` CLI (`agentic_trader/cli/commands/trade.py:176`) and
`/explain_macro` (`copilot.py:4451`). It never raises for missing data. It returns a `MacroEvaluation`
with `inputs`, `missing`, `missing_decision`, `report: MacroIntelligenceReport | None` and `stress`.

- **All decision inputs present:** the report is built as today. The 5y, 30y, DXY and breakevens
  become optional (`TreasuryYields.yield_5y/yield_30y`, `curvature_butterfly_bps`, `report.dxy` and
  `report.inflation` may be None). Displays print "N/A". The stress score is unchanged.
  `compute_compound_macro_stress` drops its unused `inflation` parameter.
- **A decision input missing:** `report` is None and `stress = unknown_macro_stress(config, missing)`.
  It carries `MacroStressLevel.UNKNOWN` (a new member of the existing enum, `macro.py:58-64`),
  `risk_multiplier = regime.unknown_risk_multiplier` and
  `squeeze_breakout_allowed = regime.unknown_breakout_allowed`. Its `min_rr_threshold` is the default
  2.0, so the VIX-derived ratio governs (`regime.py:177-183`). The drivers line is "Missing decision
  inputs: 2y, hy_oas".

**`RegimeDetector.get_regime`.**
- VIX comes from the evaluation's inputs. When the engine is disabled, VIX, the 10y and DXY come from
  the same typed helper.
- If VIX is missing, it raises `RegimeUnavailable` (new, `regime.py`). The exception carries the
  `inputs` and has a fixed message naming the missing input names only.
- Otherwise the VIX rules apply (`regime.py:172-183`). The multiplier, breakout flag and ratio then
  come from `stress`, measured or `UNKNOWN`, exactly as `regime.py:185-189` applies a report today.
- `RegimeSnapshot` gains `macro_stress: MacroStressAssessment | None` and
  `macro_inputs: Mapping[str, MacroInput]`. Its `risk_multiplier` loses the 1.0 default
  (`regime.py:41`), so every construction must state it.
- The summary line (`regime.py:194`), which cards print as "Regime:", appends "Macro: UNKNOWN (missing
  2y, hy_oas; ×0.50, breakouts suppressed)" when unknown. `/macro`'s "uses volatility only" branch
  (`formatters.py:383-387`) instead names the UNKNOWN policy, and the stress badge map gains `UNKNOWN`
  (`formatters.py:398-403`).
- An `UNKNOWN` snapshot is cached for `cache_ttl_seconds`, like any other. Each evaluator call reads
  the regime (`evaluator.py:389`), and refetching ten inputs per candidate would hammer a failing feed.
- Admission's `force_refresh=True` (`copilot.py:3966`) still refetches. Under `UNKNOWN` its per-trade
  budget is halved, so a max-tier tap made under a measured `LOW` is refused with the existing "request
  a fresh scan for smaller sizing" text; the base tier ($250) fits the halved $500 budget.

**`regime.yield_curve_enabled: false`** remains an explicit, documented VIX-only policy at 1.0×. It is
journaled with `macro_enabled: false` and `macro_stress: null`. It is a configuration, not a failure,
and existing VIX-classification tests rely on it (`tests/agent/test_regime.py:43-152`). The dead
`regime.credit_oas_enabled` and `regime.inflation_breakeven_enabled` are removed from `RegimeConfig`
and `config.yaml:287-288`. The second one misdescribes behaviour once the breakevens are context-only.

**UNKNOWN defaults: `unknown_risk_multiplier: 0.5` and `unknown_breakout_allowed: false`.** They are
validated `0.10 ≤ m < 1.0`, so 1.0 is refused and the value sits inside the per-trade clamp
(`docs/risk-policy.md:56-61`). This is the `HIGH` stress policy (`macro.py:255-259`), the second most
severe known state:
- a missing feed is evidence of nothing, so `EXTREME` (0.25×) would over-react;
- VIX is always present on this path, so a VIX above 30 still yields EXTREME's VIX rules;
- breakouts off follows the observation that squeeze entries are the setup most sensitive to the macro
  gates.

Honest effect: because the $7,500 notional cap binds on about 83% of setups (report RK1), the
multiplier mostly changes the max tier and the tap and admission budget. The breakout suppression is
the larger effect.

**VIX unavailable in `run_scan`.** The new order in `run_scan`:

> lock → registry snapshot → halt → session gate → **idle check** → lockout → **regime (guarded)** →
> dynamic universe → drift preparation → selection → fetch → collect → rank → send or journal.

- The regime read moves from `copilot.py:780` to just after the lockout check. A locked-out or idle
  scan therefore makes no macro reads.
- The **idle check** ends the scan when no instrument would be selected: the existing executable and
  closed-session rules (`copilot.py:951-968`) applied to the configured contracts, with dynamic and
  drift names, which are equities, counted only when the equity session is open. It observes SCAN ready
  with "Idle: nothing eligible (…)", journals nothing and returns None. Overnight swing scans end here
  (§4 item 3).
- On `RegimeUnavailable`, or any exception from `get_regime` (classified by type name), a non-dry scan:
  1. journals the scan event with `status: regime_unavailable` (§5.6) when it qualifies for the
     journal;
  2. in the same transaction, adds at most one notice per New York date. It uses
     `workflows.add_notification(session, f"regime-unavailable/{et_date}", NotificationKind.MESSAGE, …)`,
     the pattern of `pead-unavailable` (`copilot.py:2049-2068`). The text: "⚠️ Market regime
     unavailable (vix: ReadTimeout) at HH:MM NY: the {trigger} scan was skipped — no cards[, no PEAD
     decision]. Later scans retry; this notice is sent once per New York date.";
  3. journals the skipped PEAD decision with `_journal_skipped_drift(reason="regime unavailable")`
     when `drift_scan` (`copilot.py:1974-1981`);
  4. observes SCAN **not ready** with "Regime unavailable (vix: ReadTimeout); scan skipped". A lost scan
     is a scan failure, and the monitor's SCAN incident is the health signal;
  5. records a gated summary for the digest (§5.6) and returns None.

  A restricted scan gets only the readiness observation and the operator reply. The None text in
  `_scan_result_text` gains "market regime unavailable" (`copilot.py:3934-3935`).
  `run_scan_summary_html` (`copilot.py:4352-4376`) says the scan did not run, with the gated summary's
  reason, instead of "Scan Complete … no setups". A dry scan logs and prints the reason, then returns
  None.
- **Digest in `finally`.** `run_suggestion_scan` (`service.py:440-462`) wraps the scan in
  `try/finally`. The digest runs in the `finally`, inside its own `try/except`, which logs the
  failure; a digest failure never masks the scan's own exception.

**Why no `macro_inputs` readiness component.** Readiness components are either per-run worker
observations with a maximum age (`readiness.py:76-114`) or checks computed from durable state
(`:115-162`). Macro inputs are read only inside scans, taps and `/macro`, and after this change only
when a scan has something to evaluate. An observed component would go stale every night and every
weekend, unless idle scans fed it fake "ready" observations tied to the swing cadence. More decisively,
a missing decision input is now handled conservatively (`UNKNOWN`) and journaled with names, sources and
dates on every scan. Paging on it would add incident traffic for a handled condition, for example a
FRED maintenance window. The case that must page, a missing VIX, already does so through SCAN readiness
and the daily notice. A `macro_inputs` component with per-input age contracts belongs with the source
upgrade (roadmap 1.8), once the observation dates have an agreed staleness rule.

### 5.3 Raw regime inputs in the journal (item 0.7)

`regime_journal_block` (`regime.py:58-100`) keeps its seven keys, adds the keys below and still
never raises. `REGIME_BLOCK_KEYS` grows to match.

```
macro_enabled: bool
macro_stress: "LOW"|"MODERATE"|"HIGH"|"EXTREME"|"UNKNOWN"|null     # null = disabled (or pre-change events)
inputs: {name: {value, source, observed_on, decision, error}}     # all ten names; context inputs have decision: false
missing: [names]           # every missing input
missing_decision: [names]  # the subset that forced UNKNOWN
clock_regime: "EDT"|"EST"  # New York at the snapshot's timestamp
```

The block reaches both the scan journal and every card's `decision_provenance["regime"]`
(`copilot.py:1479`). For a `regime_unavailable` event it carries the inputs from the exception with
null snapshot fields. It is descriptive only. `cards outcomes` already buckets `macro_stress` without a
fixed list (`agentic_trader/research/setups/outcomes.py:798-815`), so `UNKNOWN` needs no reader change.
`/macro`'s `MACRO_REPORT` audit (`copilot.py:4432-4446`) may adopt the same block; that is the
builder's call.

### 5.4 Journal-only swing and intraday scans (CS-R1, D2)

`ScanBudget.JOURNAL_ONLY = "journal_only"` (`config.py:187-190`). In `run_scan`, a `JOURNAL_ONLY` scan:
- collects exactly as today: dedup, account risk and the deterministic `evaluate_candidate` with
  `use_llm=False`;
- ranks;
- then skips the shadow ranker, book preparation, the `card_stats` read, card-policy decisions, the
  news fetch and scoring, the budget read and the whole send loop. It calls no LLM, no `record_signal`
  and no outbox, and spends no card budget;
- gives every native candidate `outcome = RankedOutcome.JOURNAL_ONLY` (`"journal_only"`);
- journals per §5.6, observes SCAN as today (`copilot.py:1673`) and runs `monitor_positions`
  (`:1676`). `drift_scan` additionally requires `budget != JOURNAL_ONLY` (defensive; the swing job
  never sets `shadow_evidence`).

**Swing job.** `register_swing_scan` passes `budget=ScanBudget.JOURNAL_ONLY` with the existing
`trigger=SWING_SCAN` (`service.py:588`). It **keeps `next_run_time=now`**:
- the start run is what gives the `scan` component an observation in each run
  (`docs/production.md:23-25`), which avoids a session-aware rewrite of `scan_max_age_seconds`
  (`config.py:619`);
- a journal-only run cannot spend a slot, which removes the defect behind 61 restarts in 33 days;
- the cost that remains is repeated journal rows after in-session restarts. Those exist today, are
  counted by `recurrence.same_session_repeats`, and are `card_stats` v2's to de-duplicate.

The false config comment "aligned with 4h candle closes" (`config.yaml:330`) becomes "every 4 h from
daemon start; journal-only".

**Intraday job.** `register_intraday_scan` passes `budget=ScanBudget.JOURNAL_ONLY` (`service.py:602-609`).
It stays restricted to `non_universe_contracts` (`service.py:613-618`). It still never journals,
because restricted scans are outside the universe journal contract (`copilot.py:1591`;
`outcomes.py:155-160`), so it can never send a card again. The job is idle by construction (§4 item 4).
It costs about three bar requests for each of nine equities every 15 minutes, and it holds the scan
lock. `config/config.yaml` therefore ships `scheduler.intraday_scan_enabled: false`; the flag exists
(`config.py:463`; `service.py:724`). This is the config-only half of roadmap item 0.8, flagged in §10.

**Still sending.** Operator `/scan` and the chat tool `trigger_market_scan` call
`run_scan(use_llm=True, dry_run=False)` with the default SESSION budget (`copilot.py:4367`;
`agentic_trader/agent/copilot_tools.py:61-70`). `copilot scan` uses SESSION, or NONE with `--no-budget`
(`agentic_trader/cli/commands/scan.py:92`). Re-evaluate and `/scan SYMBOL` use NONE with `symbols`
(`copilot.py:3898-3902`). The 10:35 and 14:35 suggestion scans use FULL (`service.py:443-455`). None of
these change.

**Digest.** `publish_scan_digest` aggregates scans whose summary has
`trigger == ScanTrigger.SUGGESTION_SCAN`. Today it uses scope (`copilot.py:4288-4291,4311`), which lets
swing and unrestricted operator scans in. Gated suggestion scans add a "Skipped: 14:35 (macro lockout:
FOMC Press Conference at 18:30 UTC)" or "(market regime unavailable)" clause. Summaries therefore carry
`trigger`, `budget`, `status` and `scheduled_time_et`.

### 5.5 Broad-index ETF exclusion (CS-R2, D4)

**Rule.** A *broad US equity index ETF* is an unlevered, long-only ETF that tracks a headline US equity
benchmark: a total-market, large-, mid- or small-cap segment index, in cap or equal weight, the Dow 30
or the Nasdaq-100. Sector and industry ETFs stay eligible, as do style, factor, dividend and thematic
ETFs, international ETFs and bond or commodity ETFs.

Why this line:
- a native card on a segment benchmark is a market-timing bet;
- the desk's yardstick is SPY at β=1 (`outcomes.py:187-221`), so for these names excess return is
  zero by construction (S&P 500 and total market) or a known size or growth spread, never a
  setup-specific edge;
- the bracket study found every native exit arm trails SPY by 0.10–0.15R
  (`docs/bracket-counterfactual-2026-10-18.md`);
- these names are mutually correlated at 0.8–1.0, yet the label groups let two of them be carded in
  one session (§4 item 1).

Style and factor tilts are left eligible until the β-matched yardstick (roadmap 1.1) can judge them.

**Config.** `scan.broad_index_etfs: tuple[str, ...]` (`ScanConfig`, `config.py:193-205`). Each entry
must match `^[A-Z][A-Z.]{0,5}$` and be unique. The default and `config.yaml` carry the same 23 names:

| Segment | Symbols |
| --- | --- |
| S&P 500 (cap and equal weight) | SPY, VOO, IVV, SPLG, RSP |
| Total market | VTI, ITOT, SCHB, IWV |
| Large cap / S&P 100 | IWB, VV, SCHX, OEF |
| Nasdaq-100, Dow 30 | QQQ, QQQM, DIA |
| Mid cap | MDY, IJH, IWR, VO |
| Small cap | IWM, IJR, VB |

An empty list turns the rule off. Before data entry closes, the builder verifies each ticker is an
active Alpaca `us_equity` asset with a one-off read-only check, not in tests. SPLG may have been renamed
by its issuer; list the current ticker. A ticker that never matches is harmless.

**Both paths.**
- Static universe: SPY, QQQ, IWM, DIA and VTI (`config.yaml:98-102`). SPY, QQQ and IWM are also
  explicit `contracts` (`config.yaml:38-55`).
- Every other listed name can arrive only as a dynamic name, as VOO did on 10-20. The dynamic filter
  keeps unlevered ETFs (`agentic_trader/screeners/dynamic_universe.py:113-176`).

The rule matches `candidate.contract` (normalised) in the send loop, so both paths are covered. It does
not filter the dynamic selection: an index name still takes a dynamic slot and is still journaled.

**Placement.** In the send loop (`copilot.py:1304-1350`), **before** the budget refusals, for an
unrestricted scan (`not symbols`, any budget, including dry runs), a native candidate that is neither
drift nor policy-locked (`_policy_locked(candidate, probe=True)`, `copilot.py:3083-3086`):
- `outcome = RankedOutcome.BROAD_INDEX_EXCLUDED` (`"broad_index_excluded"`);
- a runner-up reason "broad index ETF: excluded from native cards (scan.broad_index_etfs)";
- no LLM, card or group budget spent.

Placing it first makes the outcome deterministic rather than "per-scan budget spent". Excluded names
are also dropped from `book_candidates` and from news scoring, which otherwise spend fetches and the
`max_scored_per_scan` LLM budget on names that cannot be carded (`copilot.py:1210-1256`).

The rule is unaffected by catalog, PEAD, alpha and probe cards. A restricted operator scan
(`/scan SPY`, `copilot scan --symbols SPY`) and Re-evaluate still card these names, so operator trades
remain possible. **This is a card-selection rule, not an entry-risk rule:** it lives in `run_scan`, not
`agentic_trader/risk`, and admission never sees it.

### 5.6 A complete scan journal (CS-R3)

**Which scans journal.** A scan journals exactly one `scan_candidates_ranked` event when all of these
hold:
- it is unrestricted (no symbols, no timeframe);
- it is not a dry run;
- `budget != NONE` (FULL, SESSION or JOURNAL_ONLY);
- it passed the idle check (§5.2).

A zero-candidate scan journals. Lockout-gated and regime-unavailable scans journal too. Halted,
closed-session and idle scans do not: they never evaluated a slot, and halt and lockout keep their own
PEAD skip journal. The `native_count` condition at `copilot.py:1591` is removed. The key stays
`scan_candidates_ranked/{scan_id}` and the transaction stays its own.

**Payload changes.** All are additive. Readers treat a missing `status` as `completed`.

```
status: "completed"|"macro_lockout"|"regime_unavailable"     # ScanStatus(StrEnum), new, execution/durable.py
gate: null | {rule: "macro_lockout", reason: <rule text>}
           | {rule: "regime_unavailable", missing: [...], errors: {name: Type}}
scheduled_time_et: "10:35"|"14:35"|null
calendar: {...}            # §5.1, every status
regime: {...}|null         # §5.3; null when the lockout gated before the read
funnel: null | {
  selected, scanned, fetch_failed, insufficient, coverage_excluded, dynamic_excluded,
  raw_setups, raw_setups_by_strategy: {strategy: n},
  duplicates, account_risk_unavailable,
  rejected_by_rule: {RiskRule value | "unclassified": n},   # det_res.rejection_rule; None -> "unclassified"
  errors: {TypeName: n}, setups_lost_to_errors,
  admitted                                                  # == native_count
}
candidates: [...]          # [] when gated or empty; outcome may be "journal_only" or "broad_index_excluded"
```

The funnel counts native setups only. Drift keeps its own `pead_decision`. Native setups satisfy
`raw_setups = duplicates + account_risk_unavailable + Σ rejected_by_rule + admitted +
setups_lost_to_errors`. To feed `rejected_by_rule`, `_admit_candidate` (`copilot.py:1679-1767`)
returns a typed refusal: `duplicate`, `account_risk_unavailable` or `rejected` with the rule id, in place
of the parsed reason strings at `copilot.py:1121-1127`. The per-contract exception guard
(`copilot.py:1129-1131`) counts the type name. Rule ids and type names only; no reason text.

**Consumers, checked.**
- `_candidate_entries` already skips an empty `candidates` list and non-universe scopes
  (`outcomes.py:159-164`), so `label_journaled` and `card_stats` see the same rows as before.
- `card_stats`' `events_considered` (`card_stats.py:154`) now counts zero-candidate and gated events.
  It is documented as "scan events considered", not "scans with candidates".
- `summarize` (`outcomes.py:858-1000`) today treats every row that is not `sent` as a runner-up
  (`:339`, `:904-919`). It gains a `journal_only` class beside `sent` and `runner_up` in `counts`,
  `base_rates` and `market_exposure`, so that `runner_up` keeps meaning "competed for a card and lost".
  `broad_index_excluded` rows stay runner-ups, refused by a selection rule like a budget refusal.
- A new pure `scan_supply(events)` beside `summarize` reports, per trigger: scans, scans by status,
  zero-candidate scans, summed funnel counts and sent cards. It reads the events, not the labelled frame,
  so zero-candidate scans are counted. Today's `triggers` block counts scans from rows only
  (`outcomes.py:977-982`). Events before this change count as `funnel: unknown`. `cards outcomes` prints
  it as `supply`, and its "No scan_candidates_ranked events" line (`agentic_trader/cli/commands/cards.py:84-85`)
  becomes "No journaled candidates in N day(s) (M scan events)".
- Execution evidence (`agentic_trader/research/setups/execution_evidence.py`) reads only sent rows.
  Replay never reads the journal. The operator liquidity reference reads `dynamic_universe_built` only
  (`copilot.py:3805-3835`). None needs a change.

## 6. Names at a glance

| Kind | Name |
| --- | --- |
| Enums (new) | `ScanBudget.JOURNAL_ONLY`; `RankedOutcome.JOURNAL_ONLY`, `RankedOutcome.BROAD_INDEX_EXCLUDED`; `ScanStatus` (`completed`, `macro_lockout`, `regime_unavailable`); `MacroStressLevel.UNKNOWN`; `CalendarStatus` (`ok`, `failed`, `expired`, `invalid`); `Tier1Kind` |
| Types (new) | `StaticTier1Calendar`, `UnionTier1Calendar`, `CalendarFetch`, `CalendarCheck`, `MacroInput`, `MacroEvaluation`, `RegimeUnavailable` |
| Changed signatures | `BaseEconomicCalendar.fetch_events() -> CalendarFetch`; `macro_lockout(..., *, calendar_verified)`; `MacroIntelligenceEngine.evaluate()` replaces `get_macro_report()`; `compute_compound_macro_stress` drops `inflation` |
| Removed | `is_in_lockout_window`, `get_upcoming_tier1_events`, `EconomicCalendarProtocol`, `regime.credit_oas_enabled`, `regime.inflation_breakeven_enabled`, the duplicate `regime._fetch_ticker_sync` |
| Config | `risk.macro_calendar_path`, `risk.macro_calendar_sha256`, `risk.macro_calendar_unknown` (`open`), `risk.macro_calendar_renewal_days` (30); `regime.unknown_risk_multiplier` (0.5), `regime.unknown_breakout_allowed` (false); `scan.broad_index_etfs`; `config.yaml`: `scheduler.intraday_scan_enabled: false` |
| Journal | `scan_candidates_ranked`: `status`, `gate`, `scheduled_time_et`, `calendar`, `funnel`, the new `regime` keys, new outcome values |
| Readiness | computed check `macro_calendar` |
| Outbox key | `regime-unavailable/{et_date}` |
| Model field | `LLMTradeEvaluation.macro_note` |
| Data | `config/calendar/us-tier1-v1.json`, plus a synthetic test calendar under `tests/fixtures/` |

## 7. Tests (plan must cover)

### 7.1 Calendar

**Live fetch status.** Each of these yields `failed` with its error type and is never cached as
`ok`: a timeout, a 403 or 429, a 200 HTML body, a JSON object instead of a list. It is retried after
the backoff. Zero USD events is `ok`. A malformed item is counted in `unparsed_items`.

**Loader.** Each of these yields `invalid` with its type name, never an exception at construction: a
digest mismatch, a missing file, an unknown kind, an offset wrong for New York at that instant, an `at`
outside the validity range, unsorted or duplicate events, a non-official host.

**Union and statuses.**
- A static event locks out while the live feed is failed.
- A live-only tier-1 event locks out.
- Matching within ±5 minutes; `static_only` and `live_only` disagreements; the 10-entry bound.
- Expired plus `open`: no static lockout, `verified: false`, prompt "UNVERIFIED", card "Macro:
  Unverified".
- Expired plus `closed`: one refusal text from `macro_lockout` at the whole-scan check, the evaluator,
  the tap gate and the preflight.
- The prompt contains "verified" only when `verified` is true (string test over all branches).

**Readiness.** `macro_calendar` is ready at 30 days and not ready at 29, after expiry and when
invalid, with the detail strings pinned.

**Shipped data** (the only tests that read the real file):
- `config.yaml`'s `macro_calendar_sha256` equals the file's SHA-256.
- Every FOMC statement is 14:00 New York and has a press conference at 14:30 the same date. Every BLS
  kind is 08:30.
- `valid_through` is no later than the shortest series' last listed date.
- With `now` injected: 2026-10-28 14:35 ET is locked out by the press conference, 10:35 ET is clear,
  and a 14:50 ET tap is refused.

No test asserts days remaining against the real clock. That would age out (memory: date-dependent
tests); readiness covers it.

### 7.2 Regime

- **Property test:** for random context-input values, including None, `MacroEvaluation.stress` is
  identical. This pins "a non-decision input failure leaves the multiplier unchanged" for each of the
  five.
- Each decision input missing in turn gives `UNKNOWN`, 0.5×, breakouts off, a VIX-derived ratio and
  `missing_decision` naming it.
- Config validation refuses `unknown_risk_multiplier` of 1.0 and 0.05.
- A FRED HTML-200 body gives `unexpected_payload` and None.
- `yield_curve_enabled: false` stays VIX-only at 1.0× with `macro_stress: null`.
- The formatters and the prompt render UNKNOWN and None context fields.
- The removed toggles in an old YAML are ignored.

**VIX missing in `run_scan`:**
- one `scan_candidates_ranked` event with `status: regime_unavailable` and the inputs;
- one `pead_decision` skipped at 10:35;
- SCAN observed false with type names only;
- one notice. A second failure on the same New York date adds none (dedup key); the next date adds one.
- The notice and the event are in one transaction.
- No regime read on an idle or locked-out scan.
- The digest is published when `run_scan` raises, and a digest failure does not mask it.
- A dry run journals and notifies nothing.

### 7.3 Journal-only scans

- **A daemon start cannot record a signal.** Run the registered swing job's function, args and kwargs
  against `scan_desk` with a candidate that a FULL scan would send. Assert:
  - no `signals` row and no notification work item;
  - the evaluator was called only with `use_llm=False`;
  - no news fetch and no book preparation;
  - the event has `budget: journal_only`, `trigger: swing_scan` and every outcome `journal_only`.
- The intraday job is registered with `JOURNAL_ONLY`, restricted to `non_universe_contracts`, and never
  journals.
- `ScanBudget.NONE` still sends (dry run, `--no-budget`, Re-evaluate).
- The digest counts only `SUGGESTION_SCAN` summaries and lists skipped slots.

### 7.4 Broad-index exclusion

- Static SPY and dynamic VOO ranked first get `broad_index_excluded`. The next native candidate is
  sent. No LLM evaluation, news score or budget is spent on the excluded two.
- `/scan SPY` (restricted) still produces a card.
- A policy-locked alpha or probe card on SPY is unaffected.
- Config validation refuses lower case, a bad shape and duplicates. An empty list turns the rule off.

### 7.5 Complete journal

- An eligible scan with zero raw setups journals once with `candidates: []` and a full funnel.
- Overnight swing scans under Alpaca execution are idle and journal nothing.
- The funnel identity holds across duplicate, account-risk, rule-rejected, admitted and errored setups.
  `rejected_by_rule` uses `RiskRule` values and `unclassified`.
- A lockout-gated suggestion scan journals `status: macro_lockout`, the rule text and `regime: null`.
- **Consumers on mixed old and new events:**
  - `label_journaled` and `compute_card_stats` produce the same rows as on the non-empty events alone;
  - `summarize` separates `journal_only`;
  - `scan_supply` counts zero-candidate and gated scans per trigger;
  - an event without `status` counts as `completed`.

## 8. Docs to update (same PR)

- **CLAUDE.md.**
  - Contract 1: `macro_lockout` reads a verified union calendar, with `risk.macro_calendar_unknown`.
  - Contract 8: the journal rule (§5.6, statuses, funnel); swing and intraday `JOURNAL_ONLY`; the
    broad-index rule; "every journaled universe scan carries a `regime` block" becomes "except a
    lockout-gated one"; the `macro_calendar` readiness check and the static calendar's renewal duty.
  - The code map gains `config/calendar/`.
- **docs/risk-policy.md.**
  - The per-trade budget: `UNKNOWN` multiplier, and the line at :61 made true for missing inputs too.
  - The `macro_lockout` row: union calendar, `calendar_verified`, the closed-policy text.
  - Scan order: the lockout's source.
  - "What the operator sees": collect-phase refusals are now counted in the journal (they were "logged
    only").
  - Known divergences: correct :331-333.
  - `RiskLimits` gains `macro_calendar_unknown`.
- **docs/card-evidence.md.**
  - Coverage, under Limits (:372-380): swing is journal-only; zero-candidate and gated events; the
    funnel; the `journal_only` and `broad_index_excluded` outcomes. Correct "18 of the 22", which the
    review recounted as 11 unjournaled plus 7 journaled before `signal_id` existed (report §3.1 CS1).
  - "Journaled regime" (:293-299): the new keys and `null` on lockout-gated events.
  - The `cards outcomes` `supply` block.
- **docs/operational-monitoring.md:** the `macro_calendar` check, and the `regime-unavailable`
  notice beside the existing pead-unavailable and missed-slot notices.
- **docs/production.md.**
  - Ownership and scheduling (:22-34): swing is journal-only and kept at start; the "session gated"
    wording made exact; intraday disabled.
  - A short **calendar renewal runbook**: publish `us-tier1-vN+1.json` from the official pages, pin its
    SHA-256 in `config.yaml`, deploy before `macro_calendar` degrades.
- **config/config.yaml:** new keys with comments; the swing comment; removal of the two dead toggles;
  `intraday_scan_enabled: false`.
- **docs/apriori-alphas.md** (Part 2, Enrolment/Measurement, :166-202). Record the operator's
  2026-10-21 decision:
  - the PEAD probe is renewed for 180 days at unchanged risk;
  - it is judged on the counterfactual label over every eligible event (`probe_outcomes`), not on taps;
  - the renewal is a separate operator CLI action, `copilot alpha apriori-probe … --renew --days 180`
    (`--days` allows up to `MAX_PROBE_TERM_DAYS = 180`,
    `agentic_trader/research/alpha/probe.py:20`; `agentic_trader/cli/commands/alpha.py:534-540`).
- **docs/holistic-review-2026-10-21/README.md:** the Phase 0 row's status (built, then merged #N and
  deployed date).
- **docs/alpha-roadmap.md:** the holistic-review queue (:27), Phase 0 status.

## 9. Rollout

1. **Data first (day 1).** Commit `us-tier1-v1.json` from the verified scratch JSON. The operator
   reviews the event list against the Fed and BLS pages. Then pin the digest.
2. **Build and merge by Monday 2026-10-26.** Run:
   - the full test suite;
   - disposable-PostgreSQL integration for the journal and outbox transaction, `--run-postgres`;
   - `pre-commit run --all-files`.

   No migration. **Nothing to backfill**: old events lack the new fields and readers default them.
3. **Deploy outside the scan windows**, by Tuesday 2026-10-27 at the latest: pause the watchdog, then
   the daemon; update the installed checkout; restore the daemon, then the watchdog
   (`docs/operational-monitoring.md`, Deployment). Run the PEAD renewal CLI separately, before
   2026-10-28.
4. **Verify after deploy**, recorded as deployment evidence apart from tests:
   - `doctor --readiness` shows `macro_calendar` ready with the v1 detail;
   - the first in-session swing scan journals `budget: journal_only` and no `signals` row;
   - the next suggestion scan journals `calendar` (static `ok`, live status), `regime.inputs`,
     `clock_regime` and `funnel`;
   - `cards outcomes` prints `supply`;
   - no card on a listed index ETF.
5. **2026-10-28 acceptance:**
   - the 10:35 scan runs;
   - the 14:35 scan journals `status: macro_lockout` (FOMC Press Conference);
   - taps between 13:00 and 15:00 ET are refused with the lockout text;
   - the digest lists the skipped 14:35 slot.

   Record the evidence in the review README.

## 10. Open questions and builder's calls

Recommended here; the operator may overrule:
- `risk.macro_calendar_unknown` defaults to **open** (§5.1).
- The broad-index list includes the size benchmarks, QQQ and DIA, so five static universe names leave
  native cards (§5.5).
- `scheduler.intraday_scan_enabled: false` ships in `config.yaml`. This is item 0.8's config-only half,
  not in the approved list, and justified by §4 item 4.
- `UNKNOWN` is 0.5× with breakouts off (§5.2).
- Journal-only swing candidates stay in `card_stats` until v2 (§2).

Builder's calls (contract fixed, mechanics free):
- the ±5-minute match tolerance and the 5-minute failure backoff (keep both constants, journal the
  tolerance);
- the name `evaluate` versus a different name for the engine's entry point (no alias either way);
- how the idle check shares selection logic with `copilot.py:951-968`;
- whether `/macro`'s audit adopts `regime_journal_block`;
- how the union stub is built for replay and backtest without I/O;
- whether `CalendarCheck` offers a convenience that calls `risk.macro_lockout`, as long as the rule
  itself stays in `risk/`.

Known residuals, not in scope:
- the evaluator still re-reads the regime per candidate, so a cache expiry inside a long scan can mix
  two snapshots;
- `/status` still calls `get_regime` unguarded (`copilot.py:4256`);
- the daemon and a CLI dry scan share ForexFactory's per-IP cap.
