# Research window × read ledger

Program rule R1 of the [alpha directions review](alpha-directions-2026-10-30/report.md#program-rules-apply-to-every-read):
a record of which historical windows each study has read, so a new study can tell what is still fresh.
It starts from the review's [§2.5 table](alpha-directions-2026-10-30/report.md#25-clean-data-is-nearly-spent-structural).
Every row below was taken from the result doc it links, read for its exact window. Where a doc does not
state something, the row says "not stated in the doc". Nothing is inferred from memory or from the review.

The ledger does not change any gate, and a row here grants no promotion, shadow or probe credit.
It holds reads L01–L42; L40–L42 (the [IC lane](alpha-ic-lane.md)) were added before their first read and
have no result yet.

## Rules

1. **A study adds its row before it reads.** The row names the window, lane, cohort, what will be read
   (discovery, selection, confirmation, outcomes) and the lane's α. It is committed with the frozen
   manifest, before any provider or artifact read of outcomes.
2. **Freeze and hash first** (R1). A read without a ledger row is not a clean read, and a window it
   touched is marked `spent` for that hypothesis family until the row is added.
3. **Fresh means unread by anyone.** A window read by a different lane is not fresh for the lane
   that reads it next. A lane may reuse its own window only for a rehearsal (R2), which reproduces a
   known result and does not count as a new test.
4. **Calendar overlap is recorded.** Two studies on different cohorts can read the same dates. The
   market path is shared, so the window table lists the dates each read touched.

## α per lane

Declared here before the lane's read (R1). One-sided 5% per lane, Holm within a lane. The review bounds
the program-level false-pass rate at about 10% under a global null; the economic gate (R5) and capped probes
catch the rest.

| Lane | Hypotheses | α | Declared before | Status |
| --- | --- | --- | --- | --- |
| Event lane, Sharadar era 2007-01 → 2016-07 | `insider-oos-v1` (H2a), `pead-sue-v3` (H2b), Holm over 2 | 0.05 one-sided | The era manifest, frozen at week 2 of the review's plan | Not declared yet; no read has happened |
| Price lane, Sharadar era | `composite-ic-v1` | 0.05 one-sided | The same manifest | Not declared yet; no read has happened |
| IC lane, development window 2016-08-01 → 2023-12-29 | `composite-ic-v1` gate on cohort v3 (v2 reported); checks A-IC and C-IC; the 64-pair re-score | 0.05 one-sided | The frozen `composite-ic-v1.json`, `rescore-v1.json`, `sectors-v1.json` and `ff12-sic-v1.json`, committed with rows L40–L42 | No α: development only; `t ≥ 2` at h = 20 behaves as about 5% one-sided (calibration in [the contract](alpha-ic-lane.md#6-the-ic-statistic)) |
| Prospective insider read from 2026-08-01 | The frozen `insider` v1 rule | To be declared with the 2027-08 read | Its own row | Not declared yet |
| Any new lane | Per its spec | 0.05 one-sided, Holm within the lane | Its row, before the read | — |

The review defines the two era lanes (event and price) and their α in its sections on direction 2 and the
week-2 manifest. This ledger records the α when the manifest is frozen, not before.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `fresh` | No study has read the window. |
| `development only` | Read; usable to develop and rehearse, never as a pass. |
| `reserve (decay check only)` | Held back; read only to check that a passing result persists. |
| `spent` | Read for the same family; a further read is a second look and must be declared as such. |

## Windows

Statuses follow the review's §2.5. The "Reads" column lists ledger ids from the table further down.

| # | Window | Status | Use in this program | Reads |
| --- | --- | --- | --- | --- |
| W1 | Pooled cube, discovery 2016-08-01 → 2021-12-31 and selection 2022-01-03 → 2023-12-29 | development only | Development and rehearsal | L01–L11 (discovery; L11 also read selection); L12–L17 read the whole decision window, including these dates; L40–L42 (IC lane) read the whole window 2016-08-01 → 2023-12-29, selection included |
| W2 | Pooled confirmation 2024-01-02 → 2026-07-31 | reserve (decay check only) | Held in reserve; unused for pooled formulas | Unused by the pooled campaigns; read by L12–L20 and L39 for other lanes (see the note below the table) |
| W3 | Native setups 2017-01-03 → 2021-04-30 | spent (third read) | Not used | L21–L24 |
| W4 | Native setups 2021-06-01 → 2026-01-29 | development only | Descriptive | L25–L30 |
| W5 | Native setups holdout 2026-03-02 → 2026-08-14 | spent | None | L25 |
| W6 | Event decisions 2016-08-01 → 2026-07-31 (PEAD, exdiv, insider) | spent for these three hypotheses | Rehearsal only | L12–L14, L17 |
| W7 | Spread lane discovery 2018-01-02 → 2023-12-29 | spent for pairs v1 and v2 on cohort v1 | None | L31–L32 |
| W8 | Spread lane confirmation 2024-01-02 → 2026-07-31 | reserve (decay check only) | None; the spread lane is closed | The `spread/confirmation` journal key is unconsumed and no spread study read these dates; other lanes read the same dates (L12–L20, L39; see W2) |
| W9 | ETF, sector-panel and screened-equity windows (2020–2025) | development only | None | L33–L39, L18–L20 |
| W10 | Sharadar era 2007-01 → 2016-07 (Form 4 from 2006) | fresh | One frozen manifest, two declared lanes | None |
| W11 | Prospective from 2026-08-15 (2026-08-01 → 08-14 overlaps the native holdout, L25) | fresh | Probe monitors; a fixed 2027-08 insider read | Monitors only (see below) |

## Reads

Dates are the run date the result doc gives. "Not stated" means the doc does not say.

### Pooled lane (cube 2016-08 → 2026-07)

| Id | Date | Lane / study | Cohort | What was read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| L01 | 2026-09-30 | Pooled check A (power), cohort v1 | cohort v1 (about 85 names per session) | Discovery-window cells only; built the cube over decisions 2016-08-01 → 2026-07-31 | [power check A](alpha-pooled-power-2026-09-30.md) | Passed (98 of 100 at +0.15R) |
| L02 | 2026-10-02 | Checks A, B, C, cohort v2, protocol v2 | cohort v2 | Planted on discovery picks (B); the window each check read beyond that is not stated in the doc | [v2 checks](alpha-pooled-checks-2026-10-02.md) | A and C passed, B failed (1 of 10); no campaign |
| L03 | 2026-10-05 | Checks A, B, C, protocol v3 | cohort v2 | As L02 | [v3 checks](alpha-pooled-checks-v3-2026-10-05.md) | A and C passed, B failed (3 of 10); genetic lane parked |
| L04 | 2026-10-07 | Fixed-set v4: checks A and C, then the campaign | cohort v2 | Discovery 2016-08-01 → 2021-12-31; all eight formulas charged | [v4 results](alpha-pooled-checks-v4-2026-10-07.md) | `no_finalists`; confirmation unused |
| L05 | 2026-10-07 | v4 label diagnostic (read-only) | cohort v2 | Discovery window only (1,366 sessions, 452 names); nothing after 2021-12-31 | [v4 results](alpha-pooled-checks-v4-2026-10-07.md) | No return-space signal |
| L06 | 2026-10-14 | Intraday fixed set v1 (hourly bars), checks A and C, campaign | cohort v2 | Discovery 2016-08-01 → 2021-12-31 | [intraday v1](alpha-pooled-intraday-v1-2026-10-14.md) | `no_finalists` (7 of 8 edges negative) |
| L07 | 2026-10-15 | Events fixed set v1 (four event fields), checks and campaign | cohort v2 | Discovery 2016-08-01 → 2021-12-31; event inputs Nasdaq pages 2016-02-23 → 2026-07-31 and corporate actions 2016-01-01 → 2027-03-31 (inputs, not outcomes) | [events v1](alpha-pooled-events-v1-2026-10-15.md) | `no_finalists` |
| L08 | 2026-10-16 | Cohort v3: events, daily and intraday fixed sets | cohort v3 | Discovery 2016-08-01 → 2021-12-31, three campaigns | [cohort v3](alpha-pooled-cohort3-2026-10-16.md) | `no_finalists` for all three |
| L09 | 2026-10-20 | Short-volume fixed set, cohort v2 | cohort v2 | Discovery 2016-08-01 → 2021-12-31; files 2018-08-01 → 2026-09-01 (inputs) | [short volume v1](alpha-pooled-short-v1-2026-10-20.md) | `no_finalists` |
| L10 | 2026-10-20 | Short-volume fixed set, cohort v3 | cohort v3 | Discovery 2016-08-01 → 2021-12-31 | [short volume v1](alpha-pooled-short-v1-2026-10-20.md) | `shorts_retreat` passed discovery (+0.020R, t +3.40) |
| L11 | 2026-10-20 | Short-volume fixed set, cohort v3, selection | cohort v3 | Selection 2022-01-03 → 2023-12-29 (498 sessions) | [short volume v1](alpha-pooled-short-v1-2026-10-20.md) | Failed selection (+0.006R); `no_confirmation_candidates`; confirmation not read |

The review's "64 pairs" is the formula-cohort count that CLAUDE.md gives (48 through cohort v3, 64 after the
short-volume lane). This ledger lists reads by study, not by pair, and does not recount them.

### Event lanes and the pooled literature entries (decisions 2016-08-01 → 2026-07-31)

These read the whole decision window, including the confirmation dates 2024-01-02 → 2026-07-31.

| Id | Date | Lane / study | Cohort | What was read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| L12 | 2026-09-26 | PEAD v2 (a priori entry 1; v1 failed closed on 2026-09-25 before any outcome) | Liquid reporters from the Nasdaq calendar, live dynamic-universe liquidity rule; 23,983 liquid events | Outcomes, decisions 2016-08-01 → 2026-07-31; "recent" = from 2023-01-01 | [PEAD result](apriori-pead-2026-09-26.md) | LONG passed (+0.149R, +0.055R over control); SHORT failed |
| L13 | 2026-10-12 | Exdiv v1 | `config/research/spread/cohort-v1.json` (127 names) | Outcomes, decisions 2016-08-01 → 2026-07-31 | [exdiv result](apriori-exdiv-2026-10-12.md) | Failed P2 (placebo +0.046R); a second look must be declared |
| L14 | 2026-10-23 | Insider cluster v1 | Cohort v3 (1,000 names, ranks 401–1,400), cached cohort-v3 cube | Outcomes, decisions 2016-08-01 → 2026-07-31; the doc says the run read the pooled confirmation slice 2024-01-02 → 2026-07-31 | [insider result](apriori-insider-2026-10-23.md) | Failed P1 (+0.087R); P2 held (+0.070R); no re-test on 2016–2026 |
| L15 | 2026-09-30 | Literature entry `high52-v1` | Pooled cohort v1 cube | Outcomes, decisions 2016-08-01 → 2026-07-31 ("recent" = from 2023-01-01); per-year table includes 2024 and 2026 | [literature entries](alpha-pooled-literature-2026-09-30.md) | Failed P2 and P3 |
| L16 | 2026-09-30 | Literature entry `reversal-lowmax-v1` | Pooled cohort v1 cube | As L15 | [literature entries](alpha-pooled-literature-2026-09-30.md) | Failed P2 and P3 |
| L17 | 2026-10-30 | PEAD v2 LONG events in `net_excess_r_v1` (descriptive) | The L12 events, 23,868 reporters, 4,712 leg events | Re-read of the L12 artifacts and bars; windows 2016-08-01 → 2026-07-31, recent from 2025-01-01 | [PEAD base rate](pead-base-rate-2026-10-30.md) | Descriptive; no gate, no credit |

### Pooled confirmation dates read by other lanes

The pooled campaigns never read 2024-01-02 → 2026-07-31. These lanes read calendar dates inside it.

| Id | Date | Lane / study | Cohort | What was read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| L18 | 2026-09-17 | ETF session campaign | SPY, QQQ, XLF | Selected blocks June 2024, November 2024, March 2025 (minute bars, SIP) | [session campaign](alpha-session-campaign-2026-09-17.md) | 81 of 81 complete, none passed |
| L19 | 2026-09-18 | Retained factor controls | 64 equities and nine sector ETFs (IEX) | 1,255 sessions in 2021–2025, folds 2023, 2024, 2025 | [factor controls](alpha-factor-controls-2026-09-18.md) | 54 of 54 complete; no consistent improvement |
| L20 | 2026-09-17 | Screened equity/ETF forecasts | 64 equities and nine sector ETFs | Evaluation years 2023, 2024, 2025 | [panel forecasts](alpha-panel-forecasts-2026-09-17.md) | 96 of 96 complete; zero promoted |

L18–L20 are read from the docs above. The review's §2.5 names "large-cap momentum IC" for this window; I
could not tell from the docs which of L19 or L20 it means, so both are listed.

### Native setups (159-name current universe, SIP, all-adjusted)

| Id | Date | Lane / study | Window read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- |
| L21 | 2026-09-23 | Short-suppression test | Decisions 2017-01-03 → 2021-04-30, bars through 2021-06-15 | [short suppression](setup-baserates-short-2026-09-23.md) | Rule fired: native shorts −0.33R |
| L22 | 2026-10-14 | Setup recurrence, robustness | 2017-01-03 → 2021-04-30 | [recurrence](setup-recurrence-2026-10-14.md) | `no_change` |
| L23 | 2026-10-18 | Bracket counterfactual, robustness | 2017-01-03 → 2021-04-30 (8,119 setups) | [bracket counterfactual](bracket-counterfactual-2026-10-18.md) | `no_change` |
| L24 | 2026-10-23 | Bracket counterfactual erratum (beta-matched control) | 2017-01-03 → 2021-04-30, robustness block | [bracket counterfactual](bracket-counterfactual-2026-10-18.md#erratum-beta-matched-control-2026-10-23) | Descriptive correction |
| L25 | 2026-09-23 | Setup-outcome study and shadow ranker | Development 2021-06-01 → 2026-01-29 (14,017 setups); one-shot holdout 2026-03-02 → 2026-08-14 (1,627 setups, 20-session embargo) | [setup outcomes](setup-outcomes-2026-09-23.md) | Ranker failed its holdout and was worse than random |
| L26 | 2026-10-14 | Setup recurrence, primary | 2021-06-01 → 2026-01-29; holdout not read | [recurrence](setup-recurrence-2026-10-14.md) | `no_change` |
| L27 | 2026-10-18 | Bracket counterfactual, primary | 2021-06-01 → 2026-01-29 (8,768 setups); holdout "declared second use", not read | [bracket counterfactual](bracket-counterfactual-2026-10-18.md) | `no_change` |
| L28 | 2026-10-23 | Bracket counterfactual erratum, primary block | 2021-06-01 → 2026-01-29 | [bracket counterfactual](bracket-counterfactual-2026-10-18.md#erratum-beta-matched-control-2026-10-23) | Descriptive correction |
| L29 | 2026-10-28 | `card_stats` prior v1 (derived from the L27 primary frame) | Decisions 2021-06-01 → 2026-01-29, `A0_live` | [card-stats prior](card-stats-prior-2026-10-28.md) | Prior pinned; no window newly read |
| L30 | 2026-10-30 | `card_stats` prior v2 (empirical-Bayes shrink) | The same frame | [card-stats prior](card-stats-prior-2026-10-28.md) | Mean shrunk across keys |

L29 and L30 re-estimate from the L27 frame and add no new setups. They are listed because they read the
same data again.

### Spread lane (127-name cohort v1)

| Id | Date | Lane / study | Window read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- |
| L31 | 2026-10-10 | Pairs v1: checks A and C, study | Discovery 2018-01-02 → 2023-12-29; confirmation 2024-01-02 → 2026-07-31 not journaled, read or consumed | [spread v1](alpha-spread-results-2026-10-10.md) | `failed_discovery` |
| L32 | 2026-10-11 | Pairs v2 (second look, `second_look: true`): checks and study | The same discovery window; confirmation unread | [spread v2](alpha-spread-results-2026-10-11.md) | `failed_discovery` |

### ETF, sector-panel and screened-equity windows

All are development history. None grants qualification credit. Exact first and last dates are given only
where the doc states them.

| Id | Date | Lane / study | Cohort | Window read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| L33 | 2026-09-17 | Sector panel | Nine legacy sector ETFs plus SPY | 2021–2023 (753 bars each), 2021 warmup, folds 2022 and 2023; exact dates not stated in the doc | [sector panel](alpha-sector-panel-2026-09-17.md) | 32 of 32 complete, zero passed |
| L34 | 2026-09-17 | Persistent ETF book | Ten symbols | 2020–2023 (SIP 1,006 bars each), folds 2022 and 2023; IEX warmup from 2020-07-27 | [persistent book](alpha-persistent-book-2026-09-17.md) | 160 comparisons complete, zero passed |
| L35 | 2026-09-17 | Continuous timed ETF campaign | SPY, QQQ | Years 2022 and 2023; exact dates not stated in the doc | [continuous campaign](alpha-continuous-campaign-2026-09-17.md) | 36 complete, 12 coverage failures, zero passed |
| L36 | 2026-09-17 | Open-gap timing and cost | SPY, QQQ (1,252 daily bars each) | Discovery prefix 1,001 bars; the holdout beginning 2025-09-16 was not evaluated; first and last bar dates not stated in the doc | [open gap](alpha-open-gap-policy-2026-09-17.md) | Failed the advance-to-replay criterion |
| L37 | 2026-09-17 | Volume calibration | SPY plus nine sector ETFs | Train 2021, evaluate 2022; train 2022, evaluate 2023; no return labels read | [volume calibration](alpha-volume-calibration-2026-09-17.md) | Calibration only |
| L38 | 2026-09-19 | IEX-native mining smoke test | Eight symbols (`AAPL`, `AMD`, `IWM`, `MSFT`, `NVDA`, `QQQ`, `SPY`, `TLT`) | Five-year lookback, 1,254 observations each; dates not stated in the doc | [IEX mining](alpha-iex-mining-2026-09-19.md) | Funnel test with relaxed gates |
| L39 | 2026-09-19 | Matched screened-equity DSL panel | Screened cohort | 2021–2025 attempt failed closed on `PanelCoverageError` before scoring; recent diagnostic 2025-07-16 → 2025-12-31 | [matched panel](alpha-matched-panel-2026-09-19.md) | Descriptive only |

L18–L20 (above) are also ETF and equity-panel reads.

### IC lane (cube 2016-08 → 2023-12, development window W1)

Declared before the reads. Window `2016-08-01 → 2023-12-29` for every row; nothing after 2023-12-29 is read.
Frozen files and digests: `composite-ic-v1.json` `63269ca1ba2a2bf7437a8c979646948007f5a671c680efaa9b71b301077d63b4`,
`rescore-v1.json` `961f60f9fcebbe716a482d3a545e73e693f6ef80a204aa989b430a538776c8ec`, `sectors-v1.json`
`56bc70fb52ce541aa2e4292673e81ebbc42df18bcf53484e6907cb52d4689574`, `ff12-sic-v1.json`
`4fac501b2baffd0792188f514e0b93fe41bd195f6c09c66b288e23befe83bcb5`. The lane's α is in the table above.

| Id | Date | Lane / study | Cohort | What will be read | Result doc | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| L40 | 2026-10-31 | IC checks A-IC and C-IC, plus the alignment spot check of real cells | Cohort v3 (`cohort3-daily-v1`), target `e_20`, window 2016-08-01 → 2023-12-29 | The composite's permuted and planted replicates over the whole window; the spot check compares 200 seeded cells of the forward-return arrays with raw cached bars and **computes no IC** | [results](alpha-ic-lane-results-2026-10-31.md) | Passed: C-IC accepted 3.0% (limit 10%), `t` sd 1.13; A-IC power 100%, a plumbing check; spot check no mismatch. No α claim; C-IC validates h = 20 on the composite only |
| L41 | 2026-10-31 | Zero-trial re-score of the 64 tested pairs, by rank IC | Cohorts v2 and v3 (eight fixed sets of eight formulas) | Discovery 2016-08-01 → 2021-12-31 and selection 2022-01-03 → 2023-12-29 for each pair, with a placebo per pair and window; batch-mean verdicts, no `t ≥ 2` rule. **Selection is new for 63 of the 64 pairs** (only `shorts_retreat` on cohort v3 reached it, L11); allowed because W1 is development only, and any batch that carries then has no unread selection window left | [results](alpha-ic-lane-results-2026-10-31.md) | All eight sets reproduced; 2 of 8 batches carry, the chance rate (`short-v1`, `cohort3-events-v1`); no pair reaches `t ≥ 2` in both windows. No α claim; carrying is not a pass |
| L42 | 2026-10-31 | `composite-ic-v1` gate | Cohort v3 decisive (`cohort3-daily-v1`); cohort v2 (`campaign-v4`) reported and not deciding | `e_20` rank IC over 2016-08-01 → 2023-12-29, other horizons and raw returns reported; the decomposition (themes, composite excluding low risk, 252-session lag) and `min_detectable_ic` | [results](alpha-ic-lane-results-2026-10-31.md) | **Fail, composite closed:** v3 mean IC +0.0116, `t` 1.53, 3 of 4 blocks, power at IC 0.03 97.5%; v2 reported at +0.0148, `t` 1.50. No α claim |

A re-run after L42 is a declared second look. The checks and the gate run at one clean code revision; a code fix
after L40 means a new revision and a re-run of the checks.

### Prospective from 2026-08-01

No study has read outcomes from 2026-08-15 on. The native-setup holdout (L25, W5) ran through 2026-08-14,
so 2026-08-01 → 2026-08-14 is not fresh for a native-setup read. The rest run as monitors.

| Monitor | What it records | Doc | Start date |
| --- | --- | --- | --- |
| Native-daily panel | Frozen diagnostic controls over 64 equities plus nine ETF proxies, with terminal first outcomes | [daily panel](alpha-daily-panel.md) | Not stated in the doc |
| SPY/QQQ 15m forward observations | Receipts and revisions on the configured feed | [forward observations](alpha-forward-observations.md) | Not stated in the doc |
| IEX forward lane | IEX collection and a separately identified control cohort | [IEX forward](alpha-iex-forward.md) | Not stated in the doc |
| Insider watch cards and PEAD probe | Live cards, described in `cards outcomes` | [watch cards](apriori-alphas.md#watch-cards) | Not stated in the doc |

A fixed insider read from 2026-08-01 is planned for 2027-08 ([insider result](apriori-insider-2026-10-23.md),
route (a)). It keeps its declared start of 2026-08-01, so its row must record the two overlapping weeks
(2026-08-01 → 2026-08-14, read by L25): a different cohort and hypothesis, but rule 3 applies. It needs
its own row and α first.

## Discrepancies with the review's §2.5

1. **Native setups 2017-01-03 → 2021-04-30: three reads, not two.** The review says "read twice". The docs
   show the short-suppression test (L21), the recurrence robustness block (L22) and the bracket robustness
   block (L23), plus the 2026-10-23 erratum recomputation (L24). The window is `spent` either way.
2. **Pooled cube: the 2022-01-03 → 2023-12-29 half was read once by a campaign.** The review treats the
   cube as 2016-08 → 2023-12 read by every lane. The docs say the campaign discovery window is
   2016-08-01 → 2021-12-31 and the selection window is 2022-01-03 → 2023-12-29. Only the cohort-v3
   short-volume campaign (L11) reached selection. Every other campaign stopped at discovery. The decision
   windows of L12–L17 cover 2016-08-01 → 2026-07-31 and so include the 2022–2023 half.
3. **Pooled confirmation 2024-01-02 → 2026-07-31: more reads than the review lists.** The review names
   PEAD, exdiv, insider and large-cap momentum IC. The docs also show the pooled literature entries
   (L15, L16: per-year results through 2026), the ETF session campaign (L18: June 2024, November 2024,
   March 2025), the factor controls and panel forecasts (L19, L20: folds to 2025) and the matched-panel
   recent diagnostic (L39: 2025-07-16 → 2025-12-31). These use other cohorts and signals, but they read the
   same calendar dates.
4. **Native setups 2021-06-01 → 2026-01-29: the review says "at least 5".** The docs source three independent
   studies (L25–L27), the erratum (L28) and two prior derivations from the same frame (L29, L30). The count is
   consistent with the review, but the first three are the only independent studies.
5. **The spread lane is absent from §2.5.** Its discovery window 2018-01-02 → 2023-12-29 was read twice
   (L31, L32). Its confirmation key is separate from the pooled one and unconsumed, but other lanes read the same dates, so the window is not fresh (W8).
6. **PEAD's "recent" window differs between docs.** The study uses "recent" from 2023-01-01 (L12) and the
   base-rate doc from 2025-01-01 (L17). Both are as stated in their docs.
7. **The prospective window is not fresh from 2026-08-01.** The review treats it as unread from 2026-08-01.
   The native holdout (W5, L25) ran through 2026-08-14, so the unread window starts 2026-08-15 (W11).

## Not yet catalogued

These result docs exist but were not read for this ledger, so their windows are not recorded here: the A1b,
A2a and A2b studies, the forecast-benchmark, calibration and power studies, the panel-forecast and
factor-control companions, the volume and lifetime studies, the economic-factor plans, and the
survivorship spike. The survivorship spike checks bar availability and read no returns. Add rows
before any of these windows is used as fresh.

## Not stated in any doc

- The first and last bar dates of L33, L35, L36 and L38.
- The start dates of the prospective monitors.
- Which of L19 and L20 the review means by "large-cap momentum IC" (§2.5).
- Which window checks B and C of the pooled lane read beyond planting on discovery picks.
