# A priori alpha catalog, entry 2: pre-ex-dividend run-up study (desk item 3, part 2)

Status: design, 2026-10-12. Research only. Part 1 (corporate-actions ingestion, PR #115,
main `83f8988`) supplies the event source; this spec freezes one hypothesis on it and runs
the study. A live probe, if the leg passes, is a separate spec (part 3), exactly as the PEAD
entry's probe was.

## 1. Why this leg

Part 1's acquisition (`alpha corporate-actions`, run 2026-10-12 on the frozen spread cohort
`config/research/spread/cohort-v1.json`, 127 names, process-date window 2016-01-01 →
2027-03-31) returned 4,043 actions:

| Kind | Count | Usable as a frozen test? |
| --- | --- | --- |
| cash dividend | 3,915 on 107 names (3,490 quarterly-regular, 3,298 inside the decision window, 1,370 from 2023) | yes |
| forward split | 22 (14 at 2:1 or larger) | no: far below any sample floor |
| reverse split | 6 | no |
| spin-off / mergers / name changes | 14 / 69 / 17 | no: cohort names are mostly the acquirer |

Only the dividend calendar carries a sample. Of the candidate dividend hypotheses in part 1
§8, the **pre-ex-date run-up** is chosen because it is the one that fits the desk's trade
contract without new accounting:

- the position is **closed at the last cum-dividend close**, so no dividend is received,
  no ex-date price drop is crossed, raw live bars need no adjustment, the ledger sees no
  `DIV` activity and part 1's `corporate_action` rule (cash dividends annotate, never
  block) lets the card through;
- the literature is specific about timing: Hartzmark and Solomon (2013, *The dividend
  month premium*, JFE) find abnormal returns for predicted dividend payers concentrated in
  the days before the ex-date, reversing afterwards; Lakonishok and Vermaelen (1986) and
  Kalay (1982) document abnormal volume and returns around ex-dates; Elton and Gruber
  (1970) is the background for the ex-day drop itself, which this leg deliberately avoids;
- the live event source already exists: the daemon capture of 2026-10-12 held 17 future
  ex-dates up to 60 days ahead (4 within a week, 2 in 8–14 days, 3 in 15–28 days, 7 in
  29–56 days), so a five-session lead is visible in production evidence for a quarterly
  payer.

An ex-day capture (hold over the ex-date) would need dividend accounting in the label, in
the paper ledger (part 1 only names `DIV` activities; it does not resolve them) and in the
tracked position, and its gross edge (a fraction of a median $0.48 dividend) is of the same
order as the 10 bp round trip. It is not tested here.

## 2. Scope

In: one frozen catalog entry `exdiv` version 1; the study code (events, labels, statistics,
executor, runner) and `alpha apriori-study` dispatch by entry id; tests; docs; the study run
and its results doc in the same PR.

Out: any live probe or card (part 3 spec); a short leg (the post-ex reversal is a different
hypothesis with its own timing); ex-day capture; changes to part 1's daemon worker or rule;
any registry, shadow, trial or promotion credit; any database, broker or Telegram access
from the study.

## 3. Catalog entry

`config/research/apriori/exdiv-v1.json`, committed before any data is read. Its SHA-256 goes
into the run manifest; a change is a new version with a new window, never an edit.

- `id: "exdiv"`, `version: 1`, `title`, `references`, `hypotheses` (prose, verbatim
  record), `decision_rule`;
- `window`: `decisions: ["2016-08-01", "2026-07-31"]`, `bars_through: "2026-10-01"`,
  `recent_from: "2023-01-01"` (PEAD v2's decision window, so the two entries are comparable;
  bars run a month longer so the last decisions' controls, 32 sessions later, are mature);
- `actions_window: ["2016-01-01", "2026-12-01"]`: the **process-date** window requested
  from Alpaca (part 1 measured that `start`/`end` filter the process date, which for a
  dividend is the payable date, typically two to six weeks after the ex-date). It starts
  seven months before the first decision so the first events have a previous ex-date, and
  ends three months after the last bar so every in-window ex-date's payable date is inside;
- `cohort: "config/research/spread/cohort-v1.json"` (the 127-name frozen research cohort;
  its SHA-256 and symbol list go into the manifest, like the pooled campaign pins its
  cohort; the file is read exactly as `alpha corporate-actions --cohort` reads it);
- `feed: "alpaca:sip"`, `adjustment: "all"`;
- `event`: `min_gap_days: 77`, `max_gap_days: 105` (quarterly regularity: the previous
  non-special ex-date of the same symbol lies 11–15 weeks earlier; 3,490 of 3,800 cohort
  dividends with a predecessor qualify, 3,316 of them within ±7 days of "previous + 91"),
  `exclude_special: true`, `structural_exclusion_days: 10` (no split, merger, spin-off,
  name change or other non-dividend action for the symbol within ten calendar days of the
  decision-to-ex span; 4 events in the cohort);
- `universe`: `min_price: 10.0`, `dollar_volume_window: 20`, `static_percentile: 0.25`,
  `liquidity_adjustment: "raw"` (PEAD's gate, unchanged: the live dynamic-universe
  functions on raw bars, as of the session before the decision);
- `trade`: `decision_time_et: "10:35"`, `entry_lead_sessions: 5`,
  `control_offset_sessions: 32`, `stop_atr_multiple: 2.0`, `atr_window: 14`,
  `target_r: 3.0`, `secondary_hold_sessions: 10`;
- `costs_bps_per_side: [0.0, 5.0, 10.0]`, `decision_cost_bps: 5.0`;
- `bootstrap`: `block_mean: 10`, `draws: 2000`, `seed: 20261012`;
- `pass_rule`: `min_events: 300`, `min_recent_events: 100`, `trim_fraction: 0.01`.

There is no `max_hold_sessions` field: the hold **is** `entry_lead_sessions` (the fill
session is session 1; the exit is the close of session `entry_lead_sessions`, which is the
last session before the ex-date). The model derives it and refuses a `secondary_hold_sessions`
that does not exceed it.

## 4. Data

**Corporate actions.** `fetch_corporate_actions_cached` (part 1, `research/corporate_actions.py`)
with the cohort's symbols over `actions_window`, cache under `CACHE/corporate-actions/`.
The study's manifest records the acquisition manifest (request and content digests,
counts by kind). A failed fetch fails the run (`status: failed`); nothing is partial.

**Sessions.** The Alpaca trading calendar from 60 calendar days before the first decision
through `bars_through`, as PEAD's runner reads it.

**Prices.** Adjusted (`all`) daily bars for every cohort symbol and SPY over the whole span
(ATR, and the diagnostics' yield); raw daily bars for cohort and static-universe names (the
liquidity gate); regular-session hourly bars for every symbol with at least one event, over
its event span in the setup study's one-year chunks. All through the setup study's immutable
`.npz` cache with its range claim (`bars` and `bars_raw` sibling directories), so a rerun
with `--cache` never re-fetches. Hourly bars are adjusted (`all`): inside a window that
contains no ex-date every bar carries the same factor, so bracket returns are unaffected;
across the ex-date (the secondary hold only) the dividend is folded into the price, which
is what a total-return diagnostic wants.

A symbol whose bars fail is recorded in `bar_failures` and its events are counted under a
reason, never silently dropped. SPY missing fails the run.

## 5. Event and trade rule

Sessions are indexed on the observed calendar. For a cash dividend of symbol `s` with
ex-date `E` (`effective_on`):

1. **Eligibility.** Not `special`; `s` matches the supported symbol pattern; `E` is an
   observed session (else `non_session_ex_date`); the previous non-special cash dividend of
   `s` in the acquisition has ex-date `P` with `min_gap_days ≤ E − P ≤ max_gap_days`
   calendar days (else `no_predecessor` / `irregular_gap`); no non-dividend action of `s`
   has `effective_on` within `structural_exclusion_days` of any date in `[D, E]`
   (else `structural_action_nearby`).
2. **Decision session.** `D = E − entry_lead_sessions` sessions; `D` must lie inside
   `window.decisions` (else `outside_window`) and have enough calendar before it (else
   `outside_calendar`). `decision_at = D 10:35 New York` (UTC), as PEAD.
3. **Liquidity and ATR as of `D − 1`.** Raw close ≥ `min_price`, `median_dollar_volume`
   (raw, 20 sessions) ≥ the static reference percentile as of `D − 1`; ATR14 (adjusted)
   through `D − 1`. Reasons `illiquid_price`, `illiquid_volume`, `short_history`,
   `no_reference`, `no_atr`, `no_daily_bars`, `no_raw_close`.
4. **Leg trade (LONG).** Decision price = close of the last regular hourly bar ending at or
   before 10:35 on `D`; stop `price − 2·ATR`, target `price + 3·(2·ATR)`; entry at the
   open of the first regular bar at or after `decision_at`; the bracket walks hourly bars
   (`label_bracket`, conservative touch rules) with `max_hold_sessions =
   entry_lead_sessions`, so the time exit is the last bar's close on session `E − 1`,
   **cum-dividend**. Labelled at every cost in `costs_bps_per_side`; the decision cost
   decides. `IMMATURE` labels are counted, not scored.
5. **Control (placebo) trade.** The same symbol, same bracket construction and hold, with
   decision session `D_c = D + control_offset_sessions` (32 sessions ≈ the middle of a
   63-session quarter), liquidity and ATR as of `D_c − 1`, and the same structural
   exclusion around `[D_c, D_c + entry_lead_sessions − 1]`; additionally no ex-date of `s`
   may fall in `[D_c − 1, D_c + entry_lead_sessions]` (else `control_overlaps_ex_date`).
   The control row carries the leg's `session` as its pairing key (`pair_id` = the leg
   event's dividend id) and its own `control_session`. A leg event whose control is missing
   keeps its P1/P3/P4 row and is absent from P2 only; the count of unpaired legs is reported.
6. **Secondary hold (diagnostic).** The leg bracket re-labelled with
   `secondary_hold_sessions = 10`, which crosses the ex-date on adjusted bars: the
   Hartzmark–Solomon post-ex reversal, descriptive only.

Every excluded dividend is counted by reason and year.

## 6. Statistics and pass rule

Unit: `R_cost` per leg event at 5 bp per side (0 and 10 bp reported alongside).
Confidence intervals: stationary session-block bootstrap over leg decision sessions (block
mean 10, 2,000 draws, seed 20261012), `ci90` = [5th, 95th] percentiles, a criterion holds
when its lower bound is above zero. The same functions as PEAD: `_mean_test`,
`_paired_test` and `_trimmed_mean` move from `pead_study.py` to
`research/apriori/statistics.py` (public names, `BootstrapSpec` parameter) and
`pead_study.py` imports them; PEAD's tests and v2 result are unchanged by construction
(same code, same seed handling).

The LONG leg **passes** only if all hold:

- **P1 edge.** Mean `R_cost` > 0 and ci90 lower bound > 0.
- **P2 adds value over the placebo.** Mean `R_cost(leg) − R_cost(control)` > 0 with the
  paired draw (one session resample for both means, keyed by the leg session) and ci90
  lower bound > 0.
- **P3 robustness.** 1%-trimmed mean > 0 and the mean over decisions from 2023-01-01 > 0.
- **P4 sample.** ≥ 300 leg events, ≥ 100 from 2023-01-01.

Decision rule, frozen in the entry: pass → `eligible_for_probe` (part 3 spec needed);
otherwise `failed`, written up, the catalog row marked failed. No threshold is revisited;
a change is a new version with a new window. The window has no reserved confirmation slice
(the a priori lane spends one confirmatory test per leg, as PEAD did).

### Descriptive diagnostics (never decisive)

Per year; liquidity terciles; hit counts (stop / target / timeout); events per session
(median, p90, max; the cohort's ex-dates carry a median of 2 and at most 10 events);
costs; the secondary hold (post-ex); by `foreign` flag; by dividend-yield tercile
(`rate / decision price`, the H–S effect is stronger for higher yields); the control's own
mean; unpaired leg count.

## 7. Code layout

| File | Responsibility |
| --- | --- |
| `config/research/apriori/exdiv-v1.json` | the frozen entry |
| `research/apriori/catalog.py` | `ExdivEntry` (+ `ExdivWindow` reusing `PeadWindow`, `ExdivEvent`, `ExdivTrade` with derived `max_hold_sessions`), `load_exdiv_entry`, `load_entry_id(path)` (peeks `id` for CLI dispatch) |
| `research/apriori/statistics.py` | `mean_test`, `paired_test`, `trimmed_mean` (moved; `pead_study` imports) |
| `research/apriori/exdiv_events.py` | `build_exdiv_events(actions, market, entry)` → events frame (`role` leg/control, `pair_id`, `session`, `control_session`, `ex_date`, `previous_ex_date`, `gap_days`, `rate`, `foreign`, `atr`, `median_dollar_volume`, `reference`, `decision_at`) + counts by reason/year; reuses `MarketData`, `_by_session`, `_atr`, `decision_at` from `pead_events` (the private helpers become public names) |
| `research/apriori/exdiv_study.py` | `ExdivInputs`, `label_exdiv_events` (one direction, both roles), `evaluate_exdiv` (P1–P4 + diagnostics), `execute_exdiv_study` (protocol + manifest before `build`, `status: failed` on any exception, `authorizes_promotion: false`) |
| `research/apriori/exdiv_runner.py` | `build_exdiv_inputs(entry, *, bars, calendar, actions_source, cache_dir, static_symbols, pace)` |
| `cli/commands/alpha.py` | `apriori-study` dispatches on the entry id (`pead` → existing path; `exdiv` → new path, building the `AlpacaCorporateActionsSource` the way `alpha corporate-actions` does) |

No new config, no database, no migration, no daemon change.

## 8. Failure handling

| Condition | Behaviour |
| --- | --- |
| Entry file invalid (unknown id, hold ≥ secondary hold, window order, cost not listed) | `load_*` raises before any I/O |
| Output directory exists | CLI refuses |
| Cohort file missing/unparseable | `status: failed` after manifest |
| Corporate-actions fetch fails or returns zero actions | `status: failed` |
| SPY bars missing | `status: failed` |
| A symbol's bars fail | `bar_failures[symbol]`, its events counted as `no_daily_bars` / `no_hourly_bars` |
| Malformed action row | part 1's `normalize_actions` already fails closed naming kind and field |
| Fewer than `min_events` leg events | P4 fails; the result is still complete and written |

## 9. Testing

Synthetic fixtures only (no network; the suite's socket guard applies):

- entry validation (derived hold, secondary > hold, windows, cost listing, id dispatch);
- events: regular vs irregular gap, special excluded, non-session ex-date, structural
  exclusion window (both sides), control placement and `control_overlaps_ex_date`, decision
  inside/outside window, liquidity/ATR reasons, counts by reason and year, determinism;
- labels: the time exit lands on the close of session `E − 1` (never the ex-date), gap
  and touch rules unchanged, every cost column present, secondary hold crosses `E`;
- statistics: parity test that `pead_study`'s results on its existing fixtures are
  byte-identical after the move; paired test keyed by the leg session with a missing
  control;
- executor: manifest and protocol written before `build`, failure path writes
  `status: failed`, `authorizes_promotion: false` everywhere, cohort digest recorded;
- runner: fake bar/calendar/actions sources, cache reuse, failure recording;
- CLI: dispatch by id, refuses an existing output directory.

Full suite and pre-commit before each commit, as always.

## 10. Runtime and run plan

Daily bars: 128 symbols × 2 adjustments, one request each. Hourly: ~127 symbols × 10
years in one-year chunks ≈ 1,300 paced requests. Expect under an hour. Run from the clean
merged revision, outside the 10:2x–10:4x and 14:2x–14:4x New York windows, output
`~/agentic-trader-research/apriori-exdiv-v1-<date>`, cache
`~/agentic-trader-research/apriori-cache` (shared with nothing that claims a conflicting
range). The results doc (`docs/apriori-exdiv-<date>.md`) and the catalog row go into this
PR.

## 11. Caveats (recorded in the results doc)

- The cohort is the spread lane's current-member cohort: survivorship-biased, not
  point-in-time. A passing result is a reason for a capped probe, not a claim about the
  historical cross-section.
- The study knows the ex-date at `D = E − 5` because the acquisition is historical; live,
  the ex-date is known only once declared. The 2026-10-12 capture shows declarations
  typically two to six weeks ahead for quarterly payers, and the regularity filter is the
  same predictability Hartzmark–Solomon use, but a part 3 probe must measure its own
  lead-time coverage from the daemon's captures before claiming parity.
- The decision price is the 10:30 hourly close, not a broker fill; slippage is not
  measured. Costs are the desk's standing 5 bp per side assumption.
- One hypothesis, one test: no parameter (lead, offset, gap band, bracket) is tuned on
  this data. The diagnostics may suggest a v2 with a new window; they cannot pass or fail
  anything.
