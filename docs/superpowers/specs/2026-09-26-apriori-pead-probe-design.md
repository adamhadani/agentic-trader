# A priori catalog, Part 2: capped paper probe of long PEAD cards

Status: design approved in conversation 2026-09-26; this document is the written spec.
Part 1 (the frozen study) is `2026-09-25-apriori-pead-study-design.md`; its result is
`docs/apriori-pead-2026-09-26.md`.

## Goal

Turn the one passing catalog leg, **pead v2 LONG**, into Telegram cards on the Alpaca
paper account. The cards run as a capped, expiring paper probe that follows the study's
frozen trade rule as closely as live execution allows. Each probe outcome is measured
against the study's expectation (+0.149R per trade after 5 bps) and against the −4R kill.

This serves the north star (one or two reasonable suggestions per session). In earnings
season the study found a median of 2 qualifying long events per decision session and a
90th percentile of 9. That is a source of cards that is independent of the two native
strategies.

## Decisions (from brainstorming)

| # | Decision |
| --- | --- |
| Q1 | Reuse the existing paper-probe rules: paper scope only, 30-day renewable term, −4R cumulative kill sticky per version, `probe_risk_dollars` cap, counts against `max_probes`, no shadow/holdout/promotion credit. |
| Q2 | Automatic time exit through the existing lifetime and close services, at 15:45 New York on the 20th session of the hold. |
| D1 | Probe identity: a catalog enrolment in the existing registry `probe` list, under the same single liveness rule (`probe_block_reason`). |
| D2 | Live events: 10:35 New York suggestion scan only; a backward Nasdaq calendar lookup for report date D; the study's own event functions on SIP daily bars; long leg only. |
| D3 | Cards: a new `EarningsDrift` strategy; a fixed, price-locked bracket (2×ATR14 stop, 3R target, no trailing); the LLM writes narrative only. |
| D4 | Budget: at most 1 drift card per session, outside the native card budget; tie-break by largest reaction z. |
| D5 | Failure: an unavailable or incomplete calendar or bar read yields no drift card and one operational notice; nothing is guessed. |
| D6 | Measurement: `cards outcomes` and `/alphas` show the probe's running R against −4R and +0.15R, plus every qualifying event's counterfactual label. |

## Fidelity to the study

The probe tests the frozen rule, so the live path reuses the study's code. It does not
reimplement it. Code checks found three places where the existing alpha card path would
not match the study. This spec fixes all three:

1. **Stop placement.** `bracket_prices` in `research/alpha/strategy.py` takes
   `max(structural swing stop, stop_atr × ATR)`, but the study stop is exactly
   `2 × ATR14` (`pead_study.levels_for`). The new policy kind has no structural anchor.
2. **Trailing.** Every position with an `alpha_policy` trails via `trailing_price`
   (trigger 1.5R) in `TradingCopilot`'s stop-adjustment loop, but the study never trails.
   The new policy kind declares no trailing, and the loop leaves its stop unchanged.
3. **LLM veto.** The evaluator's LLM can reject a candidate, but the study measured the
   unfiltered rule. For drift candidates the LLM output is recorded and used for the
   thesis text only. It is not an approval gate. The deterministic operational gates
   still apply (halt, macro lockout, regime, account drawdown, entry capacity, position
   and correlation caps, earnings blackout); a skip for any of them is journaled with its
   reason.

A fourth difference cannot be fixed; it is measured instead (see "Known limits").

## Components

### 1. Execution policy: `apriori_bracket_v1` and `session_count_v1`

**File:** `research/alpha/strategy.py` (policy), `execution/lifetime_policy.py` (lifetime).

- New frozen dataclass `AprioriBracketPolicy` with `kind = "apriori_bracket_v1"`:
  `stop_atr` (2.0), `atr_window` (14), `reward_risk` (3.0), `tick_size` (0.01),
  `lifetime` (a `SessionLifetimePolicy`). Its fields contain no trailing parameters.
- `execution_policy_from_dict` dispatches on `document.get("kind")`. Documents without
  `kind` deserialize exactly as today. `AlphaExecutionPolicy`, `TimedAlphaExecutionPolicy`
  and `TradeLifetimePolicy` serialization is unchanged, so every existing version hash,
  signal `alpha_policy` and position is byte-identical.
- `bracket_prices(entry, direction, atr, swing_low, swing_high, policy)` for this kind:
  `ticks = ceil(stop_atr × atr / tick_size)`, then stop = entry − direction × ticks × tick
  and target = entry + direction × ceil(ticks × reward_risk) × tick. This is the existing
  outward rounding without the structural term; swing inputs are ignored.
- `trailing_price(...)` for this kind returns `stop` unchanged. The copilot's
  stop-adjustment loop already defers to `trailing_price` whenever `alpha_policy` is
  present. It must also skip the native breakeven/trail proposals for this kind, so that
  no stop replacement is requested for it.
- New frozen dataclass `SessionLifetimePolicy` with `version = "session_count_v1"`:
  `resting_seconds` (57,600, the existing one-session entry lifetime),
  `holding_sessions` (20) and `close_time_et` ("15:45").
  `holding_deadline(filled_at, sessions)` takes the broker's regular sessions (a list of
  `MarketCalendarDay`) and works as follows:
  - The fill's New York session counts as session 1.
  - The deadline is `close_time_et` on session 20, or 15 minutes before that session's
    close when the broker calendar lists an early close.
  - If the sessions list does not reach session 20, the result is "unknown", which maps
    to `LifetimeAction.REVIEW`, never to a guessed date.
  - A fill outside a regular session is REVIEW.

  This matches `label_bracket`, which counts the entry session as session 1 and times
  out on the close of session 20.
- A single `lifetime_from_dict(document)` dispatches on `version`, and it replaces the
  direct `TradeLifetimePolicy(**lifetime)` calls in `execution/lifetimes.py` and
  `storage/lifetimes.py`.

### 2. Time exit

**File:** `execution/lifetimes.py`.

- `TradeLifetimeService` gains an injected `MarketCalendarProtocol` (the daemon's
  existing composite calendar).
- For a `session_count_v1` position, `assess_lifetime` receives the sessions from the
  fill date through fill date + 40 calendar days.
- Everything else is unchanged:
  - the 1-minute `position_monitor` job;
  - `PositionCloseService.close_signal` with the stable `hold-<sha256>` request ID;
  - the `regular_session_open()` check;
  - the partial-exit review;
  - the Telegram notice;
  - `resume_blockers`.
- A calendar read failure is `lifecycle_evidence_unavailable`, the existing review path.
  The bracket stop and target stay live throughout.

### 3. Catalog probe identity and enrolment

**Files:** `storage/alpha.py`, `storage/workflow.py`, `research/apriori/catalog.py`,
`cli/commands/alpha.py`.

- Version ID: `apriori:pead:v2:long:<first 16 hex of the entry file SHA-256>`. The
  registry `probe` list and the projection table already accept any string ID. No
  migration is needed.
- `version/<id>` projection payload:

  ```
  {"definition": {
      "kind": "apriori",
      "alpha_id": "pead_long",
      "entry_id": "pead",
      "entry_version": 2,
      "entry_sha256": "…",
      "leg": "LONG",
      "timeframe": "1d",
      "execution": <AprioriBracketPolicy document>,
      "study_result_sha256": "…"}}
  ```

  `clock` is absent and `eligible_symbols` is null: the universe is any liquid reporter.
- New `AlphaRepository.enrol_catalog_probe(entry_path, result_path, leg, *, actor,
  expected_generation, days=None, renew=False, now=None)`. It shares `_authorize_probe`'s
  checks:
  - paper scope;
  - term bounds;
  - the sticky kill;
  - renew only when listed;
  - `max_probes` slots.

  In place of the DSL-specific checks (qualification, native daily, explicit symbols) it
  requires all of the following:
  - The entry file loads through `load_pead_entry` and its SHA-256 matches.
  - The result file's manifest names the same SHA-256.
  - The result lists `decisions[leg] == "eligible_for_probe"`.
  - The leg is LONG. SHORT failed, and enrolment refuses it by name.

  The payload pins the entry SHA, the result SHA and the limits, as today. The symbol
  owner conflict check becomes a per-card check (component 5).
- `probe_block_reason` is unchanged: it reads enrolment, policy, expiry and the
  forward record by `alpha_version`, all of which already work for this ID. The sweep,
  snapshot and admission keep a single liveness rule.
- `_alpha_entry_rejection` in `storage/workflow.py`: when the version definition has
  `kind == "apriori"`, the DSL contract check is replaced by these checks:
  - strategy == `alpha_id`;
  - timeframe matches;
  - direction == leg;
  - `alpha_policy` == `definition.execution` exactly;
  - the signal's provenance carries a PEAD event whose symbol == contract and whose
    decision session == the signal's New York trading day.

  A catalog version can only be a probe. An `active` listing of a `kind == "apriori"`
  version is refused ("catalog alphas run only as paper probes").
- CLI: `copilot alpha apriori-probe ENTRY.json --result RESULT.json --leg long [--days N]
  [--renew]`, mirroring `alpha probe` (actor, generation and confirmation). Nothing is
  enrolled automatically.

### 4. Live event source

**Files:** `agent/earnings.py`, new `research/apriori/pead_live.py`.

- `NasdaqEarningsCalendar.reported_rows(day) -> list[CalendarRow] | None` fetches one
  date with the existing headers, timeout and pacing, and parses it with
  `parse_calendar_payload`. It accepts the page only on HTTP 200, a dict body and
  `status.rCode == 200`, like `acquire_calendar`. Any other outcome is `None`
  (unavailable). An empty row list on a session day is also treated as unavailable.
  That is the Part 1 gap signature. The raw page and outcome are journaled under the
  existing diagnostic journal.
- `pead_live.live_events(entry, session, calendar, market_reader, clock)`:
  1. Report date D = the regular session two sessions before `session`, from the broker
     calendar.
  2. Rows = `reported_rows(D)`. If `None`, return unavailable.
  3. Read SIP daily bars:
     - adjusted bars for the reporters and SPY;
     - raw bars for the reporters and the static liquidity universe.

     Each read covers enough sessions for `vol_window` and `atr_window`, through D+1.
     Bars are read via the existing `resilience/fallback` market-data path with feed
     `alpaca:sip`; the D+1 bar is complete by 10:35 on D+2, and SIP more than 15 minutes
     old is allowed on the account's plan.
  4. Call `pead_events.build_events(rows, MarketData(...), live_entry)`, where
     `live_entry` is a copy of the frozen entry with `window.decisions = (session, session)`.
     The frozen file and its SHA are the probe's identity; the copy only narrows the window.
  5. Return the LONG leg events (`leg == "LONG"`), plus `build_events`' skip counts.
- A symbol with incomplete bars is skipped by `build_events`' own reasons, as in the
  study. If SPY or the static reference is unavailable, the whole session is unavailable.

### 5. Scan integration and budget

**Files:** new `screeners/earnings_drift.py`, `agent/copilot.py`, `config.py`.

- New config `apriori: AprioriConfig` with:
  - `pead_entry_path` (default `config/research/apriori/pead-v2.json`);
  - `enabled` (default `true`; the probe still does nothing unless enrolled and live);
  - `max_drift_cards_per_session` (default 1).
- On the scheduled suggestion scan whose time equals the entry's `decision_time_et`
  (10:35), and only when the catalog version is live under `probe_block_reason`:
  1. Run `live_events`.
  2. Drop events whose symbol has an open position, a pending entry or another alpha
     owner. The reason is journaled.
  3. Order by reaction z (descending), then symbol.
  4. Build one `ScreenerCandidate` per event from the same scan's hourly data, fetched
     for these symbols the same way dynamic names are fetched:
     - `strategy = "pead_long"`, `alpha_version = <catalog id>`;
     - `alpha_policy = <AprioriBracketPolicy document>`, `probe = True`;
     - `atr_14` = the event's study ATR;
     - `setup_quality` = 0, and it is never used for ranking;
     - `trigger_detail` = "EPS beat X%, reaction +Yσ vs SPY, report D".
- Drift candidates have their own budget. They skip the native per-scan and per-session
  count and the `setup_quality` ranking. Today's drift spend is derived from recorded
  signals (`strategy == "pead_long"`, New York trading day) and is never stored as a
  counter. Native budget arithmetic excludes drift signals.
  - Correlation-group caps still apply.
  - The dynamic group is counted separately as today.
  - The first event that clears every deterministic gate is carded. The rest are
    runners-up with reasons.
- The 14:35 scan and ad hoc `/scan` never produce drift cards. `/scan SYMBOL` on a
  drift name runs only the native strategies.
- The evaluator applies the probe cap (`risk_dollars_cap = probe_risk_dollars` when
  `candidate.probe`) and the fixed bracket.

### 6. Card and LLM

- The card shows:
  - "🧪 PEAD probe";
  - the event line (beat %, reaction σ, report date);
  - "20-session hold, time exit 15:45 NY on <date>" (computed from the calendar at card
    time for a same-day fill);
  - the $ risk cap;
  - the usual valid-until.
- Taps follow the existing policy-locked path: a fresh card executes the original
  bracket, and one that has run past its stop or target, or has expired, is refused. It
  is never re-priced.
- The LLM receives the event facts as candidate context. Its approve/reject and text are
  stored in provenance. Only the text is shown, labelled as commentary. Any prompt change
  requires LangSmith trace review and the replay harness before merge.

### 7. Measurement

- Every qualifying live event is journaled at the decision, whether it was carded,
  skipped, tapped, filled or unfilled. This is the set that the study would have traded.
- `copilot cards outcomes` gains a probe section:
  - carded count, tap rate and fill rate;
  - realized R per closed trade and cumulative R against −4R;
  - the counterfactual label of every journaled event, labelled later by
    `pead_study.label_events` on SIP hourly bars once mature, compared against the
    realized trades.
- `/alphas` shows the probe line: term and expiry, cumulative R, closed-trade count, and
  the study's +0.149R reference.
- No shadow, holdout or promotion credit, as for every probe.

## Failure behaviour

| Condition | Result |
| --- | --- |
| Catalog version not enrolled, expired, killed, or policy changed | No drift event lookup at all; the reason appears in `/alphas`. |
| Nasdaq page unavailable, non-200, `rCode != 200`, or empty on a session day | No drift card this session; one `MESSAGE` notice ("PEAD probe: earnings calendar unavailable for D"); journaled. |
| SIP bars unavailable for SPY or the static reference | Same as above, with the bar reason. |
| One reporter's bars incomplete | That event is skipped with `build_events`' reason; the others continue. |
| Probe liveness changes between scan and tap | Admission refuses via `probe_block_reason` ("Paper probe blocked: …"). |
| Calendar read fails during the holding check | `lifecycle_evidence_unavailable` review; halts new risk as today; the bracket stays live. |

The notice is debounced to one per session. A calendar outage never affects native cards.

## Invariants kept

- No new migration; schema head stays `008_alpha_pipeline`.
- Existing policy and lifetime serialization, and version hashes, are unchanged.
- `probe_block_reason` stays the single liveness rule.
- POST, PATCH and DELETE are never retried automatically.
- Budgets are derived from signals, never stored.
- The intraday job is not widened.
- The dynamic universe is not changed.
- Tests use only guarded `test_*` PostgreSQL databases and blocked network I/O.

## Testing

- **Policy:**
  - `apriori_bracket_v1` levels equal `levels_for` rounded outward, for LONG, over
    sampled ATRs;
  - the old documents round-trip byte-identically (existing hash fixtures);
  - `trailing_price` is a no-op and the stop loop requests no replacement.
- **Session lifetime:**
  - a fill mid-session gives session 1;
  - a 20-session walk across a holiday and an early close;
  - a short calendar gives REVIEW;
  - a fill outside RTH gives REVIEW;
  - close at 15:45 through `TradeLifetimeService` with a fake broker and calendar, reusing
    the existing lifetime tests' fixtures;
  - idempotent request ID.
- **Enrolment:**
  - happy path;
  - wrong SHA, SHORT leg, failed result or tampered result refused;
  - slot limit;
  - sticky kill;
  - renewal;
  - active listing refused;
  - admission accepts a matching drift signal and refuses one with a mismatched policy,
    event or direction.
- **Live source:**
  - `reported_rows` accept/unavailable matrix using recorded fixture pages;
  - `live_events` on a synthetic calendar and bars reproduces the Part 1 events for a
    known past session from the v2 artifacts.

  The second item is a parity test: a small committed fixture of calendar rows plus
  bars for a handful of symbols on one 2025 session, whose live output must equal the
  study's events for that session.
- **Scan:**
  - the drift budget is independent of the native budget;
  - tie-break by z;
  - owner and position conflicts are skipped;
  - the 14:35 scan and `/scan` produce no drift card;
  - a calendar outage yields one notice and no card, with native cards unaffected;
  - the budget is re-derived after a restart.
- **Measurement:** the outcomes report on seeded journal rows.
- **PostgreSQL:** integration tests for enrolment and admission with `--run-postgres`.
- **Deploy:**
  1. Controlled restart and `verify_runtime`.
  2. Enrol the probe via the CLI; the operator confirms it.
  3. `/alphas` shows it.
  4. The next 10:35 scan journals a PEAD decision (an event, an empty set, or
     unavailable).

## Known limits

- **Entry fill.** The study entered at the next hourly bar's open. The live card is a
  limit order at the rounded 10:35 price, resting one session, and it is not re-priced on
  tap. Drift winners that run up before the tap may never fill, which selects against
  winners. The fill rate and the counterfactual labels (component 7) measure this
  directly. Changing the entry mechanics is a separate decision after the first term.
- **Survivorship.** The study's control earned +0.094R, so the signal-specific edge is
  about +0.055R. A 30-day term yields roughly 5–15 closed trades. That can trip the −4R
  kill, but it cannot confirm the edge. The term is a checkpoint, not a verdict.
- **Nasdaq dependency.** Nasdaq is an unofficial, unauthenticated source, so an outage
  or format change stops drift cards. Native cards are unaffected.

## Out of scope

- The SHORT leg (failed).
- The 60-session hold (descriptive only; it would be a new catalog version).
- Automatic enrolment or renewal.
- Promotion to `active`.
- Other catalog entries.
- Allocator sizing.
