# Pooled events lane: event-conditioned score fields on the cached cube

Date: 2026-10-14. Status: design, written under the operator's standing design trust
(2026-10-09) after the operator chose "intraday first, events second" (2026-10-14).
Research only. Nothing here touches the registry, broker, Telegram or promotion.

## 1. Goal and non-goals

Test whether event-conditioned cross-sectional formulas carry a pooled edge on the
existing long-bracket label cube, using the same frozen cohort, cube, windows, gates
and checks as protocol v4 and the intraday fixed set. The lane changes only what a
formula may read: four dimensionless per-symbol, per-session **event fields** derived
from two cached research acquisitions that already exist or are cheap to run:

- the Nasdaq earnings-calendar page cache (`apriori-pead-v1-cache/nasdaq/`, 2016-02-23
  → 2026-07-31, all listed symbols; `CalendarPageStore`, `parse_calendar_payload`);
- the Alpaca corporate-actions acquisition (`alpha corporate-actions`), re-run for
  cohort v2 (the existing artifact covers only the 127-name spread cohort).

Non-goals (rulings, recorded for the PR):

- **No news fields in v1.** No `alpha news` acquisition has ever been run; the
  cohort-v2 history (452 symbols × ~128 months, chunks of 20) is thousands of Alpaca
  requests and must be scheduled outside scan windows. Count-only news fields are a
  declared v2 once that acquisition exists; LLM tone over ten years is a cost decision.
- **No forward-looking event fields.** `sessions_to_earnings` and `exdiv_countdown` are
  excluded: the calendar pages and corporate-actions rows are as served now, not
  point-in-time, so "next scheduled date" would be lookahead on reschedules. The
  pre-ex-dividend run-up was already tested and failed on P2 (exdiv v1); it is not
  re-tested here.
- **No label, bracket, cohort, window, gate, bootstrap or stage change.** The cube is
  the cached cohort-v2 cube (spec identity `f1c835a3d3364d2b…`, 10:35 decision, 2 ATR
  stop, 3R target, 20-session hold); it must be a cache hit.
- **No live contract.** Live scans have no event frames; a finalist (if any) would need
  its own, separately specified feed contract before any registry step.

## 2. Event fields (`events_fields_v1`)

All fields are per symbol, indexed by NYSE trading day `T` (the full `trading_days`
calendar of the cube build, not the cube's eligibility sessions), and hold the state
**known at T's regular close**. They are merged into the daily prepared frames and read
exactly like OHLCV daily fields: `evaluate_prepared` reads row `D-1` for decision
session `D`. Nothing in the read path changes.

Earnings convention, verbatim from the PEAD leg: a report dated on session `D`
(`CalendarRow.date`, any `timing`) is complete when `D+1` closes, because the reaction
return is close(`D-1`) → close(`D+1`). Hence the first decision session that can see a
report is `D+2`, as in the PEAD leg.

| Field | Definition at trading day T | Unit |
| --- | --- | --- |
| `earnings_age` | Sessions since the last completed report: `index(T) − index(D+1)` for the latest report with `D+1 ≤ T`; `NaN` before the symbol's first report in the calendar | dimensionless |
| `earnings_surprise` | That report's surprise `100 × (eps − eps_forecast) / abs(eps_forecast)`, rounded as in `pead_events`, carried forward until the next completed report; `NaN` when `n_estimates < min_estimates` or `abs(eps_forecast) < min_abs_forecast` or `eps` missing | dimensionless |
| `earnings_reaction` | That report's reaction `z = (r_i − r_SPY) / (σ_i √2)`, `r` from close(D−1) to close(D+1), `σ_i` the sample std of the `vol_window` daily log returns ending D−1 (`pead_events._reaction_z`, adjusted bars, SPY from the same cache), carried forward; `NaN` when the bars are missing | dimensionless |
| `dividend_yield` | Sum of `rate` (the per-share cash amount of a `cash_dividend` row; `cash_rate` is a merger field) of regular (`special = false`) `cash_dividend` actions whose `effective_on` (ex-date) maps to a trading day in the last 252 trading days through T (an ex-date on a non-session counts from the next session), divided by the adjusted close at T; `NaN` for the first 251 trading days of the acquisition window and where the close is missing; `0.0` for a covered non-payer | dimensionless |

Rulings: carried-forward fields make a sparse event continuous across the cross-section
(every name has a last report), so decile selection is well defined; `earnings_age`
lets a formula window the event. Protocol parameters pin `min_estimates` = 1,
`min_abs_forecast` = 0.05 and `vol_window` = 20, the PEAD v1/v2 values. A report dated on a
non-session date is skipped and counted (`non_session_reports`), as the PEAD leg does; duplicate
(symbol, date) rows keep the first. A symbol with no
calendar row at all (never reports, or ticker mismatch) has `NaN` fields; `NaN` scores
make a cell ineligible for that formula, as today, and the per-symbol count is reported.

## 3. Protocol: `pooled-events-campaign`

`EventsCampaignProtocol(CampaignProtocol)` in `research/pooled/campaign.py`, dispatched
by `load_campaign_protocol` on `id`, mirroring `IntradayCampaignProtocol`:

```json
"id": "pooled-events-campaign",
"score_inputs": "daily",
"event_inputs": {
  "fields_version": "events_fields_v1",
  "earnings": {
    "source": "nasdaq-calendar",
    "pages_sha256": "<digest over sorted (date, page sha256) of every page in the cache window>",
    "calendar_from": "2016-02-23", "calendar_through": "2026-07-31",
    "min_estimates": 1, "min_abs_forecast": 0.05, "vol_window": 20,
    "benchmark": "SPY"
  },
  "corporate_actions": {
    "source": "alpaca", "content_sha256": "<the cohort-v2 acquisition's content_sha256>",
    "window": ["2016-01-01", "2027-03-31"], "trailing_sessions": 252
  }
}
```

The corporate-actions input is the acquisition's `actions.json` (a JSON list of
`CorporateAction` dumps); its digest is `sha256(encode(rows))`, the acquisition's
`content_sha256`. The earnings input is the Nasdaq page directory; its digest is the SHA-256 of
the newline-joined, sorted `"<date>:<page sha256>"` lines for every weekday page in
`[calendar_from, calendar_through]`, each page re-verified against its `.meta.json` on load; a
missing page fails closed. `excluded_families` is `intraday-v1`'s list plus `intraday-v1`'s
eight families (every earlier fixed set stays excluded).

Everything else is verbatim `intraday-v1`/v4: `search_mode: fixed_set`, cohort v2,
cube spec, windows (discovery 2016-08-01 → 2021-12-31, selection 2022 → 2023,
confirmation 2024-01-02 → 2026-07-31), decile selection, gates, bootstrap, power and
null seeds. `score_inputs` is `"daily"` and included in the dump for this subclass
(the base class still excludes it, so v1–v4 and intraday-v1 hashes are unchanged).

Identity: the frozen formula document and every manifest gain `event_inputs` (the
block above) next to `score_inputs`, so an events formula never shares an identity
with a daily or hourly one. The runner fails closed when the digests do not match the
caches on disk, before any evaluation.

CLI: `alpha pooled power|null-check|campaign PROTOCOL --cache DIR --earnings-cache DIR
--corporate-actions ACTIONS_JSON` (the two new options are required for an events
protocol and refused for any other). Manifests record both paths and digests.

## 4. Frozen fixed set `events-v1` (eight formulas, dimensionless)

| Family | Expression | Hypothesis (literature) |
| --- | --- | --- |
| `pead_surprise_decay` | `earnings_surprise / (1 + earnings_age)` | post-earnings-announcement drift in the surprise direction, fading with age |
| `pead_reaction_decay` | `earnings_reaction / (1 + earnings_age)` | drift in the direction of the announcement reaction |
| `pead_reaction_window` | `if_else(earnings_age <= 20, earnings_reaction, 0)` | reaction drift confined to one holding horizon |
| `pead_agreement` | `if_else(earnings_age <= 20, sign(earnings_surprise) * earnings_reaction, 0)` | the PEAD leg rule: surprise and reaction agree |
| `surprise_x_reaction` | `earnings_surprise * earnings_reaction / (1 + earnings_age)` | joint magnitude |
| `quiet_momentum` | `if_else(earnings_age > 20, ts_sum(returns, 20), 0)` | momentum measured outside the announcement window |
| `quiet_reversal` | `if_else(earnings_age > 20, -ts_sum(returns, 5), 0)` | short-term reversal outside the announcement window |
| `dividend_yield` | `dividend_yield` | yield as a cross-sectional factor |

Comparisons evaluate to float 0/1 series and `if_else(cond, a, b)` requires both branches
to share units (`operators.py`), so every row above is dimensionless; the plan verifies each
expression under `require_dimensionless` (else
the frozen text is adjusted before the file is hashed, never after). The frozen file is
`config/research/pooled/events-v1.json`; its SHA-256 is recorded in the results doc.

## 5. Implementation shape

- `research/pooled/events.py` (new): `load_calendar_pages(directory, calendar_from, calendar_through)
  → LoadedCalendar(rows_by_symbol, pages_sha256)` (offline: `CalendarPageStore.load` +
  `parse_calendar_payload`, never `acquire_calendar`), `load_actions(path) → (rows, content_sha256)`,
  `build_event_fields(symbol, rows, actions, adjusted, spy, trading_days, params) →
  pd.DataFrame[earnings_age, earnings_surprise, earnings_reaction, dividend_yield]`,
  and `EventFields.coverage` (per-symbol first finite session, symbols without rows).
- `research/alpha/dsl.py`: the four names join `FIELD_UNITS` as dimensionless and
  `prepare_data_fields` keeps them when present (a frame without them, as every live
  frame, is unchanged; a formula naming them on such a frame raises the existing
  unknown-field error — a pooled finalist cannot reach the registry without a separate
  feed contract, as today).
- `research/pooled/scoring.py`: `ScoreBook.with_events(adjusted, events, trading_days,
  sessions, symbols)` merges the event columns into `prepare_daily`'s frames (left join
  on the session index; `score_inputs="daily"`, `event_inputs` carried on the book);
  `score_book` dispatches on `EventsCampaignProtocol`; `FrozenPanels` carries
  `event_inputs`; the literature-overlap check still runs on the plain daily book.
- `research/pooled/runner.py`: `CubeBuild.events` built after the bars (cache hit path
  included), only for the protocol's symbols with an eligible cell.
- `campaign_run.py`, `power.py`, `null_check.py`: `event_inputs` in the frozen document
  and manifests; `result.json` records `symbols_without_earnings`,
  `symbols_without_actions`, `first_finite_session` per formula.
- Acquisition first: `alpha corporate-actions --cohort config/research/pooled/cohort-v2.json
  --start 2016-01-01 --end 2027-03-31` into `~/agentic-trader-research/corporate-actions-cohort-v2-<date>/`
  (research only; minutes). Its `content_sha256` is pinned in the protocol before freezing.

## 6. Checks, campaign and closure (pre-registered)

Fixed set ⇒ checks **A** (power) and **C** (real-formula false acceptance) only, on the
cached cube from a clean worktree at the merged revision, outside scan windows. A is
formula-independent and must replay v4's result; C must show `errors_total: 0`. The
campaign runs only with the operator's explicit approval
(`--journal-scope production/alpaca:paper`), charges each formula to the pooled ledger
before scoring, and consumes the lane-wide confirmation window only for finalists.
`no_finalists` closes the events fixed set on this window; nothing promotes.

## 7. Tests (plan must cover)

Field builder: age/surprise/reaction carried forward and `NaN` before the first report;
a report dated D is invisible at row `D` and first visible at row `D+1` (age 0), so the
first decision that reads it is `D+2`; pre-market and after-hours
reports on the same D give identical fields; dividend window arithmetic at 252 sessions,
special dividends excluded, non-payer `0.0`, pre-coverage `NaN`; SPY missing ⇒ `NaN`
reaction, not an exception. Digest mismatch fails closed before evaluation. Base-class
and intraday hashes unchanged (pin test). `events-v1.json` parses, is dimensionless,
and freezes to a distinct identity from the same text under `intraday-v1`/v4.
