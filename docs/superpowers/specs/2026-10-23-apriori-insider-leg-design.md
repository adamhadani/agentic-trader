# A priori alpha catalog, entry 3: opportunistic insider cluster purchases

Status: design, 2026-10-23. Research only. Operator decisions 2026-10-21 (holistic review C01/D6:
the pooled insider campaigns stay unrun; insider is recast as **one a priori event leg**,
PEAD-shaped, on cohort v3's band, entry once on the session after filing, event-matched control,
P1–P4, the entry frozen before any insider outcome is read) and 2026-10-23 ("start on insider
study"). A live probe, if the leg passes, is a separate spec, as PEAD's was.

## 1. Why this leg

Insider purchases are the best-documented long-only anomaly the desk can build from free
point-in-time data (Seyhun 1986; Lakonishok and Lee 2001; Jeng, Metrick and Zeckhauser 2003).
The two refinements with the strongest out-of-sample record are:

- **opportunistic, not routine, trades** (Cohen, Malloy and Pomorski 2012, *Decoding Inside
  Information*, JF): routine insiders trade in the same calendar month year after year and carry
  no information; the rest predict returns;
- **clusters**: several insiders buying the same firm within a few weeks (Jaffe 1974's
  "intensive trading"; Alldredge and Blank 2019, *Do Insiders Cluster Trades with Colleagues?*,
  JFQA) earn more than single-insider purchases.

The leg combines both: at least two distinct officers or directors with opportunistic open-market
purchases filed within 21 sessions. The effect is known to be strongest outside the largest names,
which is cohort v3's band (ranks 401–1,400 by liquidity).

**Sample sizing (event counts only, no prices or labels read, 2026-10-23).** These counts come
from the lane's loader over cohort v3, decisions 2016-08-01 → 2026-07-31, with a 63-session
cooldown per name:

| Rule | Events | From 2023 | Names |
| --- | --- | --- | --- |
| any opportunistic purchase | 4,198 | 1,466 | 820 |
| opportunistic dollars ≥ $100k in 21 sessions | 2,998 | 1,083 | 746 |
| **≥ 2 distinct opportunistic buyers in 21 sessions (chosen)** | **1,394** | **483** | **521** |

The chosen rule is the most selective literature-backed one. It still clears P4 about 4.6×
before the price and liquidity gates.

## 2. Scope

In:
- one frozen catalog entry, `insider` version 1;
- the study code (events, labels from the cached label cube, matched controls, statistics,
  executor, runner) and `alpha apriori-study` dispatch by entry id;
- tests and docs;
- the study run and its results doc, in the same PR.

Out:
- any live probe, card or EDGAR live feed (a part 2 spec);
- a sell-side leg;
- 10b5-1 filtering (the flag exists only from April 2023);
- any change to the pooled lane's protocols, fields or cube;
- any registry, shadow, trial or promotion credit;
- any database, broker or Telegram access.

**The pooled insider fixed sets stay retired.** This leg reads insider-conditioned outcomes over
the whole 2016–2026 window, including the pooled lane's unused confirmation slice
(2024-01-02 → 2026-07-31). After the run, `insider-v1` and `cohort3-insider-v1` can no longer claim
an unseen confirmation window on insider information. That is consistent with the 2026-10-21
decision not to run them. The lane-wide confirmation window stays unused for every other
information source.

## 3. Catalog entry

`config/research/apriori/insider-v1.json` is committed **before any label is read**. Its SHA-256
goes into the run manifest, and a change is a new version, never an edit. It has these fields:

- `id: "insider"`, `version: 1`, `title`, `references`, `hypotheses` (prose, the verbatim
  record), `decision_rule`.
- `window`: `decisions: ["2016-08-01", "2026-07-31"]`, `bars_through: "2026-09-01"`,
  `recent_from: "2023-01-01"`. These are PEAD v2's decision window and also the cohort-v3 cube's
  decision span.
- `pooled_protocol: "config/research/pooled/cohort3-insider-v1.json"`, with `pooled_protocol_sha256`
  equal to that file's SHA-256 (`969fc5df…a38d`). The protocol supplies:
  - the cube spec (cohort v3 and its digest, 10:35 decision, 2×ATR14 stop, 3R target, 20-session
    time exit, $5M 20-session median dollar-volume floor, $10 minimum price, 15 bp decisive
    cost);
  - the insider inputs (archive digest `67a262c0…`, quarters 2015q1–2026q3, `owner_roles`
    Officer/Director, `max_trade_rel` 10, `exclude_10b5_1` false);
  - the corporate-actions digest (`8a547b06…`).

  The leg reuses all of these and declares none of them twice.
- `cube_sha256`: the content hash (`LabelCube.sha256`) of the cached cohort-v3 cube. The runner
  fails closed when the loaded cube differs.
- `event`:
  - `cluster_window_sessions: 21`;
  - `min_buyers: 2`;
  - `cooldown_sessions: 63`;
  - `routine_rule: "same_owner_same_month_prior_year"`, the pooled lane's rule, a literal.
- `control`:
  - `matched_controls: 5`;
  - `match_return_sessions: 20`;
  - `liquidity_buckets: 3`;
  - `exclusion_sessions: 126`.
- `descriptive_hold_sessions: 60`.
- `bootstrap`: `block_mean: 10`, `draws: 2000`, `seed: 20261023`.
- `pass_rule`: `min_events: 300`, `min_recent_events: 100`, `trim_fraction: 0.01`.

The bracket and the cost are not fields of the entry. They are the cube's and are recorded from
the protocol in the manifest. The model refuses `min_buyers < 2` and `cooldown_sessions ≤` the
cube's `max_hold_sessions`, so one name's trades never overlap. It also refuses a window outside
the cube spec's decisions.

## 4. Data (all offline except the trading calendar)

- **Label cube.** `build_cube_inputs(..., require_cached=True)` loads the cached cube for cohort v3
  and the protocol's cube spec from the pooled cache (`~/agentic-trader-research/pooled-cache-v1`).
  It also loads the cohort's adjusted and raw daily bars from the same cache. The cube's labels
  are the leg's labels: `r_cost` at 15 bp per side, decisive; `r_gross` at 0 bp, reported
  alongside.
- **Insider transactions.** `load_insider_sources` reads the cached archives and the corporate
  actions and verifies both digests against the protocol. The implausible-size rule (a trade above
  `max_trade_rel` × the 20-session median raw dollar volume at its filing session) is applied first,
  exactly as the lane does. Task 1 makes it a public function that the lane then calls, with no
  behaviour change.
- **Calendar.** The Alpaca trading calendar, as `build_cube_inputs` reads it. This is the only
  provider access.

A missing cube, a cube or input whose digest differs, or a failed bar cache read fails the run
(`status: failed`) after the manifest is written.

## 5. Event and trade rule

Sessions are the observed trading days. For each cohort symbol `s`, take its qualifying
officer/director open-market purchases (code P). Keep those filed on or before the last session.
Drop the implausible ones, then drop the routine ones. A purchase is routine when the same owner
filed a purchase of the same issuer in the same calendar month of the previous year; the rest are
opportunistic. A filing date `F` has position `pos(F)`, the first session on or after `F`.

1. **Trigger.** On each filing date `F` with at least one opportunistic purchase of `s`, count
   `buyers(F)`: the distinct owners of the opportunistic purchases of `s` with
   `pos(F) − 20 ≤ pos(F') ≤ pos(F)`. `F` triggers when `buyers(F) ≥ min_buyers` and no earlier
   trigger of `s` lies within `cooldown_sessions` positions (`suppressed_by_cooldown`, an
   informational count). The cooldown runs from the trigger, whatever the later gates decide, so
   one episode counts once.
2. **Decision session.** `D` is the first session strictly after `F`. A Form 4 can be filed until
   22:00 New York on `F`, so it is public before `D`'s open. `decision_at = D 10:35` New York.
   `D` must lie inside `window.decisions` (else `outside_window`).
3. **Identity.** An event with `F` before the symbol's latest identity break is excluded
   (`identity_break`). This is the pooled events lane's rule: the CIK-to-ticker map is current, so
   history before a ticker change is unreliable.
4. **Share classes.** When one issuer's purchases fan out to several cohort symbols on the same
   `D`, keep the class with the larger cube `dollar_volume` at `D` and exclude the others
   (`share_class_sibling`).
5. **Label.** The event takes the cube cell `(D, s)`:
   - `s` absent from the cube is `not_in_cube`;
   - an ineligible cell is `not_eligible` (price, dollar-volume floor or ATR, as of `D − 1`);
   - an eligible but unlabelled cell is `unlabelled`;
   - otherwise the event takes the cell's `r_cost`, `r_gross`, `hit` and `holding`.
6. **Matching inputs.**
   - `ret20` is the adjusted close at `D − 1` divided by the adjusted close 20 sessions earlier,
     minus 1. A non-finite value excludes the event (`no_return`).
   - `bucket` is the event's dollar-volume tercile among the labelled cells at `D`.
7. **Matched control.** The candidates at `D` are the cube cells that meet all of these:
   - labelled;
   - another symbol;
   - not an event at `D`;
   - in the same `bucket`;
   - finite `ret20`;
   - no qualifying purchase (routine or opportunistic, after the implausible rule) filed in
     `[date(D − 126 sessions), D)`.

   Take the 5 candidates with the smallest `|ret20 − ret20_event|`, breaking ties by the cube's
   `tiebreak` key. The control row is their mean `r_cost` (and mean `r_gross`), with
   `session = D` and `pair_id` = the event id. With fewer than 5 candidates, the event is
   **unpaired**: it keeps its P1/P3/P4 row, is absent from P2, and is counted.

Every excluded trigger is counted by reason and year.

## 6. Statistics and pass rule

The unit is the cube `r_cost` per event, at 15 bp per side. Confidence intervals come from a
stationary session-block bootstrap over decision sessions (block mean 10, 2,000 draws, seed
20261023), using `research/apriori/statistics.py` (`mean_test`, `paired_test`, `trimmed_mean`)
unchanged. `ci90` is the [5th, 95th] percentile range, and a criterion holds when its lower bound
is above zero.

The LONG leg **passes** only if all four hold:

- **P1 edge.** Mean `r_cost` > 0 and the ci90 lower bound > 0.
- **P2 adds value over the matched control.** Mean `r_cost(leg) − r_cost(control)` > 0, with one
  session resample for both means and the ci90 lower bound > 0.
- **P3 robustness.** The 1%-trimmed mean > 0, and the mean over decisions from 2023-01-01 > 0.
- **P4 sample.** At least 300 labelled events, at least 100 of them from 2023-01-01.

The decision rule is frozen in the entry:
- pass → `eligible_for_probe` (a part 2 spec is needed);
- otherwise → `failed`: it is written up and the catalog row is marked failed.

No threshold, window, cooldown, buyer count or control parameter is revisited. A change is a new
version with a new window.

### Descriptive diagnostics (never decisive)

- per year;
- hits (stop, target, timeout);
- by buyers (2, 3+);
- by trailing opportunistic dollars over `DV` (terciles);
- by `ret20` tercile;
- events per session (median, p90, max);
- excluding 2020 (the March 2020 purchase wave);
- leg minus the mean of all labelled cells at `D` (PEAD's style of control);
- the 60-session close-to-close adjusted return from `D` to `D + 60`, minus the matched controls'
  (insider effects are documented over months; immature events are counted);
- `r_gross`;
- the unpaired count.

## 7. Code layout

| File | Responsibility |
| --- | --- |
| `config/research/apriori/insider-v1.json` | The frozen entry. |
| `research/apriori/catalog.py` | `InsiderEvent`, `InsiderControl`, `InsiderEntry`, `LoadedInsiderEntry`, `load_insider_entry`. |
| `research/pooled/insider_fields.py` | Extract the visibility plus implausible-size filter as a public `plausible_transactions(...)`; `_symbol_fields` calls it (no behaviour change). |
| `research/apriori/insider_events.py` | `opportunistic_flags(...)`, `build_insider_events(...)`, which returns the events frame plus counts by reason and year, and `purchase_dates(...)` for the control exclusion. |
| `research/apriori/insider_study.py` | `InsiderLegInputs`, `label_insider_events` (cube cells plus matched controls), `evaluate_insider` (P1–P4 plus diagnostics) and `execute_insider_study`. The executor writes protocol and manifest before `build`; any exception gives `status: failed`; `authorizes_promotion: false` everywhere. |
| `research/apriori/insider_runner.py` | `build_insider_leg_inputs(...)`: verifies the protocol digest, then calls `build_cube_inputs(require_cached=True)` and `load_insider_sources`. |
| `cli/commands/alpha.py` | `apriori-study` dispatches on `insider`, with `--insider-filings DIR` and `--corporate-actions FILE` (required for `insider`, refused otherwise) and `--cache` as the pooled cache. |

There is no new config, no database, no migration and no daemon change.

## 8. Failure handling

| Condition | Behaviour |
| --- | --- |
| Entry invalid (min_buyers < 2, cooldown ≤ hold, window outside the cube's decisions) | `load_insider_entry` raises before any I/O. |
| Output directory exists | The CLI refuses. |
| Protocol file digest ≠ `pooled_protocol_sha256` | `status: failed` after the manifest, before the calendar call. |
| No cached cube, or cube sha ≠ `cube_sha256` | `status: failed`. |
| Insider or actions digest ≠ protocol | `status: failed` (`verify_insider_sources`). |
| Fewer than `min_events` labelled events | P4 fails; the result is still complete and written. |

## 9. Testing

Synthetic fixtures only: no network, and the suite's socket guard applies.
- **Entry validation:** each refusal above, and dispatch by id.
- **`plausible_transactions`:** parity with the lane, so the existing `insider_fields` tests stay
  green unchanged.
- **Events:**
  - routine vs opportunistic;
  - two buyers within 21 sessions fires, while 22 sessions apart does not;
  - one buyer filing twice does not fire;
  - the cooldown suppresses within 63 sessions and from the trigger even when the event is
    ineligible;
  - `D` is the first session strictly after `F`, including a non-session `F`;
  - `outside_window`, `identity_break` and `share_class_sibling`;
  - counts by reason and year;
  - determinism.
- **Labels and controls:**
  - the cube cell is read at `(D, s)`, with `not_in_cube`, `not_eligible` and `unlabelled`;
  - `ret20` arithmetic;
  - bucket terciles;
  - exclusion of names with a purchase in the 126 sessions before `D` and of same-session
    events;
  - 5 nearest, with the tie broken by tiebreak;
  - unpaired below 5;
  - the control mean.
- **Statistics:** P1–P4 on a planted positive and a planted null; the paired test keyed by `D`.
- **Executor:** protocol and manifest written before `build`; the failure path writes
  `status: failed`; digests are recorded.
- **Runner:** fake cube and sources, plus each digest mismatch.
- **CLI:** dispatch, the required flags, and refusal of an existing output directory.

## 10. Run plan

- Run from the clean branch revision after the final review, outside 10:20–10:50 and 14:20–14:50
  New York.
- Command: `alpha apriori-study config/research/apriori/insider-v1.json --output
  ~/agentic-trader-research/apriori-insider-v1-<date> --cache ~/agentic-trader-research/pooled-cache-v1
  --insider-filings ~/agentic-trader-research/insider-filings-cache --corporate-actions
  ~/agentic-trader-research/corporate-actions-cohort-v3-20261015/actions.json`.
- Expect minutes: everything is cached except the calendar.
- The results doc (`docs/apriori-insider-<date>.md`), the catalog row and the CLAUDE.md sentence go
  into this PR.

## 11. Caveats (recorded in the results doc)

- **Survivorship.** Cohort v3 is a current-membership cohort (the 2026-10-15 snapshot). The
  matched control is drawn from the same survivors, so P2 differences out part of the bias. A
  passing result is a reason for a capped probe, not a claim about the historical cross-section.
- **Live parity.** The study knows each Form 4 by its EDGAR filing date. Live, a part 2 probe
  needs a nightly EDGAR ingestion that sees the same filings before `D`'s 10:35 decision, and it
  must measure its own lead-time coverage.
- **Earnings proximity.** Insiders buy in open windows, which often fall just after an earnings
  report. Matching on `ret20` controls for the post-report price move, not for the report itself.
  PEAD contamination is possible and is not separated out in v1.
- **Routine definition.** The rule uses one prior year (the data start in 2015), not Cohen–Malloy–
  Pomorski's three years. Some routine insiders are therefore classed as opportunistic, which
  dilutes the signal and does not inflate it.
- **One hypothesis, one test.** No parameter is tuned on this data. The diagnostics may suggest a
  v2 with a new window; they cannot pass or fail anything.
