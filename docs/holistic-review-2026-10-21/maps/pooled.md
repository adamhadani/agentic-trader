# Map: pooled mining lane, a priori catalog, spread lane, setup research

Reviewed at main `db82817`, read-only. Paths are relative to the repository root. **Now** = what the code or docs do today. **Rec** = my recommendation.

## 1. Purpose and scope

This is the desk's confirmatory research layer. It turns a hypothesis (a DSL formula, a literature anomaly, a pairs rule, a construction change to native setups) into a frozen, digest-pinned protocol. The protocol is tested on historical SIP bars under a session-block bootstrap with one-use holdouts. Research is told to stay away from the registry, broker and Telegram. Only two kinds of research output reach the trading daemon: a passing a priori leg, which can be enrolled as a capped paper probe (`pead_long` is the only one), and measurement artifacts (card stats, shadow ranker features, covariance for sizing).

## 2. Components and contracts

**Pooled protocol (`research/pooled/campaign.py`).**
- `CampaignProtocol` (246-401) is a frozen pydantic model. Its identity is the SHA-256 of the file bytes (`load_campaign_protocol` 603-615), so a refactor of the model does not change an identity.
- It holds three disjoint windows (`StageWindows` 87-99): discovery 2016-08→2021-12, selection 2022→2023, confirmation 2024-01→2026-07.
- Selection is either `k` (top-k with hold-skipping) or `Selection(top_fraction)` (219-232; dispatched in `select` 355-362).
- Gates: `DiscoveryGate` (166-171), `SelectionGate` (174-176), `ConfirmationGate` (179-183), `PowerSpec` (186-194) and `NullCheckSpec` (205-216).
- `search_mode` is `genetic` or `fixed_set`. A fixed set forbids check B and requires check C (323-337, `gates` 375-379).
- Five subclasses add one data source each: intraday 403-418, events 451-465, short volume 494-508, insider 555-584. Every one of them hard-codes "fixed_set in v1".
- `excluded_families` (278) is declarative only. Nothing compares it with the seeds.

**Stage logic (pure, ledger injected).**
- `CampaignWindows` (650-681) opens the confirmation window only after `ledger.consume_confirmation`.
- `DiscoveryEvaluator.score` (709-749): the formula passes when t ≥ 3, at least 3 of 4 equal blocks have a positive edge, the leg mean is above 0 and it has at least 400 sessions. Fitness is t − 0.02·nodes.
- `finish_discovery` (752-764) deduplicates survivors at Jaccard 0.5 and carries 5.
- `_selection` (774-796) requires an edge above 0, at least 0.5× the discovery edge, and 150 sessions.
- `_confirmation` (805-846) applies Holm (830) and then requires all of: leg ci90 low above 0, 1%-trimmed mean above 0, last-12-month edge above 0, and 300 sessions (833-842). `top3_edge` (826-829) is descriptive only.
- `later_stages` and `run_stages` are at 849-900.

**Label cube (`cube.py`).**
- Built once per (cohort, `CubeSpec`) (79-91). It stores only the eight arrays in `_ARRAYS` (53): eligible, labelled, r_gross, r_cost, holding, hit, tiebreak, dollar_volume.
- Every cell is labelled **long only** (`SetupLevels("LONG", …)` 288): stop 2×ATR14, target 3R, 20 sessions, decision at 10:35.
- Labels are read only through `window()` (195-204), which returns read-only views. `with_shift` (206-220) plants edges for the checks.
- Labelling uses `setups/labels.py:label_bracket` (159-328). Entry is the first regular hourly bar at or after 10:35 (180-187), so in practice the 11:00 open. A bar that touches both levels counts as the stop. There is no R cap and no floor on the risk unit.

**Formulas and selection (`formula.py`).**
- Scores are per-symbol time-series DSL on adjusted daily bars, read at D−1 (`evaluate_prepared` 111-132, D−1 at 122), or on hourly bars (`evaluate_prepared_hourly` 253-287).
- `select_top_fraction` (195-215) takes a daily decile with no hold-skipping. `select_picks` (177-192) is the hold-skipping top-k.
- The DSL has **no cross-sectional operators** (`research/alpha/operators.py` 109-167 lists only ts_*/elementwise).

**Statistics (`stats.py`).**
- `session_table` (54-105): per session, the picks' mean R minus the equal-weighted mean of all labelled eligible cells.
- `paired_edge_test` / `leg_mean_test` (142-153) use a stationary bootstrap (block 20, the same draws for every formula, 108-113).
- Cross-checks are two-way clustering, calendar-time Newey-West and the design effect (163-221).
- Its bootstrap helpers are *private* functions imported from `setups/baserates.py` and `setups/study.py` (25-26).

**Scoring (`scoring.py`).** `ScoreBook` (114-250) is an LRU of panels. `with_fields` (183-219) joins one extra field source. `score_book` (324-377) dispatches with an isinstance ladder. `vet_expression` (82-96) refuses anything not dimensionless or using a forbidden operator before it can be charged.

**Checks.**
- A (`power.py`): stationary-block resamples of discovery sessions (`synthetic_cube` 61-86). It runs 200 AR(1) φ=0.95 null scores and one planted score with a constant +δ on its picks (89-107, 206-233).
- C (`null_check.py`): name-demeaned resampled panels (`demeaned` 69-80) with the real formulas (`null_replicate` 84-).
- B (`search_power.py`) applies to genetic protocols only.

**Run binding (`campaign_run.py`).**
- `require_clean_revision` (115-120) refuses a dirty or unknown revision.
- `check_gate` (191-208) binds every gate result to the same cube, protocol and revision.
- `LITERATURE_ENTRIES` (107) is hard-coded to `high52-v1` and `reversal-lowmax-v1`. `literature_codes` and `already_tested` are at 211-237. `frozen_document` (239-256) has its own isinstance ladder.

**Ledger.** `JournalLedger` (`ledger.py` 35-101) writes charges, reservations and the lane-wide `pooled/confirmation` (`storage/alpha.py:42`). The spread lane uses `consume_lane_confirmation` (`storage/alpha.py:1173`).

**Field sources.** `events.py` (identity breaks 187, `build_event_frames` 245), `short_fields.py` (`build_short_frames` 215) and `insider_fields.py`. Each has its own Loaded/Coverage/Frames/verify quartet and a `runner.py` Sources trio (93-195). `CubeBuild` (197-209) has three optional field slots, and the build refuses more than one source (317-318).

**A priori catalog (`research/apriori/`).** The entries are PEAD and exdiv. `statistics.py` has `mean_test` (32-46) and `paired_test` (49-70; the leg and control are resampled per session together). It also has a second copy of `trimmed_mean` (73-77; the first is in `pooled/stats.py` 156-160). `pead_live.py` reuses the study's own `build_events` for the live probe.

**Spread lane (`research/spread/`).** Engle-Granger formation on 504 sessions (`formation.py`), 126-session trading tiles, pass rules S1–S4 (`evaluate.py`), one-use `spread/confirmation`.

**Setup research (`research/setups/`).** Replays the live `StrategyEngine` with `label_bracket`. `recurrence.py` and `bracket_counterfactual.py` are predeclared tests; the counterfactual winsorises at ±10R (`winsorized` 172-177). `outcomes.market_r` (187-219) is a **beta-one** SPY control in the setup's R units (219). `card_stats.py` feeds `card_policy`, `ranker.py` is shadow-only and `covariance.py` is Ledoit-Wolf.

**Seams.**
- *Consumes:* Alpaca SIP daily and hourly bars (the setup bar cache), Nasdaq calendar pages, Alpaca corporate actions, FINRA Reg SHO files, SEC Form 345 archives. Everything is digest-pinned.
- *Produces:* `ALPHA_RESEARCH` journal events and private artifact directories (`protocol/manifest/result/outcome.json`, `formulas.jsonl`, `frozen/`).
- *Production imports research modules directly* (a boundary inversion):
  - `execution/book_sizing.py:28` imports `setups.covariance`
  - `screeners/earnings_drift.py:24-26` imports `apriori.pead_live`
  - `agent/copilot.py:107-116` imports `setups.ranker` and `setups.card_stats`
  - `execution/card_evidence.py:20` imports `setups.card_stats`
  - `storage/alpha.py:28` and `storage/workflow.py:41` import `apriori.probe`

## 3. Data flow

1. **Freeze the cohort** by a liquidity screen (`screen-v2/v3.json`). Each session is made eligible from raw bars at D−1: price ≥ $10 and dollar volume above the static percentile or an absolute $5M (`cohort.py` 118-143, 193-).
2. **Build the cube once.** v2 has 922k eligible cells (about 367 names per session). v3 has 2.2M; its hourly download took about 11 h (docs/alpha-pooled-cohort3-2026-10-16.md "Building the cube").
3. **Freeze the protocol**, pinning the cohort SHA, cube spec and field-source digests.
4. **Run checks A and C** at the clean revision.
5. **Run the campaign on operator approval.** It reserves budgets in the journal, preflights panels and charges each formula before scoring it. Discovery dedupes and carries survivors; selection freezes candidates, whose frozen documents are written before the confirmation window is consumed; confirmation applies Holm. `outcome.json` and `result.json` are written on every exit.
6. **After a confirmation** a formula only earns a "Part 2" spec. No pooled formula has a live scoring path.
7. **Other lanes.** The a priori and spread lanes repeat the freeze → checks → one-use-window cycle. Setup research replays the native funnel and feeds `cards outcomes` (descriptive) and `card_stats` (the `card_policy` gate).

## 4. Evidence: what has been tried

| Test | Outcome | Source |
| --- | --- | --- |
| high52, reversal-lowmax (top-3, cohort v1) | Leg mean +0.16/+0.15R, but paired edge +0.012/+0.008R (t 0.81/0.52). Failed P2 and P3 | docs/alpha-pooled-literature-2026-09-30.md "Criteria" |
| Check A, cohort v1/v2/v3 | Detection at 0.15R is 0.98/0.96/0.99 (decile). At 0.05R it is 0.10–0.51 | docs/alpha-pooled-power-2026-09-30.md "Curve"; docs/alpha-pooled-checks-v3-2026-10-05.md; docs/alpha-pooled-cohort3-2026-10-16.md |
| Genetic v2/v3 | Check B failed (1/10, then 3/10). Lane parked | docs/alpha-pooled-checks-2026-10-02.md; docs/alpha-pooled-checks-v3-2026-10-05.md |
| Fixed sets: v4 daily, intraday-v1, events-v1, cohort3 ×3, short ×2 | 64 formula-cohort pairs, no finalist. One discovery survivor (`shorts_retreat`, v3, +0.020R, t 3.40) failed selection (+0.006R) | the per-run docs; docs/alpha-pooled-short-v1-2026-10-20.md "Cohort v3" |
| Return-space label diagnostic (v4) | No formula reaches \|t\| ≥ 3 in return space either. The bracket does not hide the signal | docs/alpha-pooled-checks-v4-2026-10-07.md "Label diagnostic" |
| PEAD v2 long | **Passed**: +0.149R; +0.055R vs every-reporter control (ci90 [+0.021, +0.089]). Short leg failed | docs/apriori-pead-2026-09-26.md "Criteria" |
| exdiv v1 | Failed P2: +0.069R vs same-name placebo +0.046R. A yield gradient remains as a hypothesis | docs/apriori-exdiv-2026-10-12.md |
| Spread v1/v2 | Both failed discovery. v2 lane mean +0.02 bp/day | docs/alpha-spread-results-2026-10-10.md; docs/alpha-spread-results-2026-10-11.md |
| Setup outcomes | `setup_quality` carries no information. The ranker did worse than it on the holdout | docs/setup-outcomes-2026-09-23.md |
| Recurrence v1 | `no_change` (repeats −0.067R vs first occurrences) | docs/setup-recurrence-2026-10-14.md |
| Bracket counterfactual v1 | `no_change`. Every arm trails SPY by 0.10–0.15R. The live ratchet costs −0.013R | docs/bracket-counterfactual-2026-10-18.md |

I re-derived the standard errors from the published edge and t tables of the 64 pooled tests (se = |edge/t|):
- The implied discovery standard errors run from **0.006R to 0.042R, median 0.017R**.
- The 64 t-statistics have **mean −0.06 and sd 1.25**, with 29 of 64 positive. That matches check C's null seed-t sd of 1.11–1.27 (docs/alpha-pooled-cohort3-2026-10-16.md "Check C"), so the whole record is statistically indistinguishable from the calibrated null.
- On cohort v2 the mean t is −0.59 (9/32 positive). On v3 it is +0.46 (20/32 positive).
- The t-statistics of the 32 formulas common to both cohorts correlate at 0.44. The populations are disjoint but the period is the same.

## 5. Strengths worth preserving

- **Pre-registration with byte-hash identity.** Every frozen file is pinned and every source carries a digest. The stage logic is written so a gate cannot be run out of order (`check_gate` 191-208). Formulas are charged before they are scored, and the confirmation window is consumed before it is read. After 64 tests the confirmation window is still unused, which is a real asset.
- **The session-paired control.** It removed a +0.15R survivorship-plus-drift illusion in the literature entries and in exdiv P2. It is the reason none of these false positives became cards.
- **Label-blind search.** Search code gets a `CampaignWindows` object and score panels, never the `LabelCube`. Combined with D−1 reads and NaN-not-wrap (`formula.py:122`), this gives a strong no-lookahead story.
- **Checks A, B and C** are honest instruments. Check B caught a real design flaw (top-3 sets sharing no picks). Check C measured the bootstrap's understated variance (seed-t sd 1.12–1.27).
- **Shared code between study and live.** The PEAD probe calls the study's own `build_events`, so the live candidates are computed exactly as in the study.

## 6. Gaps, weaknesses, risks and inconsistencies

1. **The lane is calibrated for effects larger than the plausible ones.**
   - Check A's target is a 0.08–0.10R paired edge (docs/alpha-pooled-mining.md "Purpose"). At the median se, the 80%-power minimum detectable edge at the t ≥ 3 gate is about (3+0.84)·0.017 ≈ 0.065R.
   - With 2×ATR14 at about 4.2% of price (docs/apriori-exdiv-2026-10-12.md "Descriptives"), that is about 27 bp per trade, or roughly 3%/yr of long-decile excess over an equal-weighted large-cap pool.
   - The literature puts that out of reach in liquid US names: Hou-Xue-Zhang (2020) find most anomalies fail once microcaps are excluded; Chordia-Subrahmanyam-Tong (2014) document attenuation; McLean-Pontiff (2016) find a 58% decline after publication.
   - The research doc already predicted "few discoveries" (docs/alpha-pooled-mining-research-2026-09-28.md "Conclusion"). So "64 nulls" rules out edges above about 0.06R, not small ones. The result docs report no per-formula interval or MDE, so readers cannot tell a true zero from missing power.

2. **Check A's power curve is not each formula's power.**
   - The planted score is AR(1) with φ=0.95 and the plant is a constant δ on every pick (`power.py` 89-107, 218-224).
   - Real formulas' standard errors span 7× (illiquidity 0.040 and momentum_12_1 0.039 against first_bucket_efficiency 0.006; docs/alpha-pooled-checks-v4-2026-10-07.md and docs/alpha-pooled-cohort3-2026-10-16.md tables). Persistent, factor-loaded scores are far less powered than the curve claims.
   - Check A's docs disclose the "upper bound" (docs/alpha-pooled-mining.md "What power check A does and does not show"), but the result docs keep quoting the curve as if it applied to each formula.

3. **The control removes only the equal-weighted mean, not style exposure.**
   - `session_table` (`stats.py` 54-105) compares the picks with every eligible name. The cube stores no ATR%, beta, sector or size array (`cube.py:53`), so a risk-matched control cannot be built.
   - Decile tails of almost any characteristic are higher-volatility names, and the R label is volatility-scaled. A low-volatility effect (Ang-Hodrick-Xing-Zhang 2006; Frazzini-Pedersen 2014) would then push tail-picking formulas negative in R terms. That is a plausible, **untested** explanation for v2's mean t of −0.59.
   - It is also consistent with the v4 label diagnostic: illiquidity and momentum "look good in raw returns only because their deciles hold high-volatility names" (docs/alpha-pooled-checks-v4-2026-10-07.md "Label diagnostic").
   - The design doc recommended sector-demeaned or residual ranks and stratum intercepts; they were never built (docs/alpha-pooled-mining-research-2026-09-28.md "One formula, one trial").

4. **One label horizon for every hypothesis.**
   - Every formula is labelled with the same 20-session long bracket. In the literature cube the target is hit only about 8.6% of the time (docs/alpha-pooled-literature-2026-09-30.md "Diagnostics").
   - Short-horizon priors were tested on this 20-session hold. Examples are one-week reversal, first-hour momentum (Gao-Han-Li-Zhou is a same-day, index-level effect) and daily short flow. A null on these is expected from the horizon mismatch alone.
   - `BracketSpec` (`cube.py` 57-71) already parameterises the hold, and `session_count_v1` exits exist live (docs/apriori-alphas.md "Part 2"). A horizon-matched cube is a configuration change, not a redesign.

5. **The PEAD encoding in the pooled lane is weaker than the a priori design that passed.**
   - The a priori test entered once at D+2 on a beat-and-reaction event and used an every-reporter control.
   - The pooled `pead_*` formulas take a daily top-decile basket with no hold-skipping (`formula.py` 195-215), re-buy each event for up to 20 sessions, and fill the decile with stale reports. They are compared with all eligible names.
   - The pooled results (t −1.6…+0.79 on v2; docs/alpha-pooled-events-v1-2026-10-15.md) therefore neither contradict nor replicate PEAD. The lane has no event-entry selection rule and no event-matched control.

6. **The overlap rule misses the a priori studies.**
   - `LITERATURE_ENTRIES` (`campaign_run.py:107`) lists only the two pooled entries.
   - PEAD v2 and exdiv v1 read 2016-08→2026-07, which includes the pooled confirmation window, and events-v1's `pead_*` and `dividend_yield` formulas are close relatives.
   - A confirmed pooled PEAD formula would therefore look like independent confirmation when it is a second look at data already read. The pooled ledger checks only `pooled/confirmation` (`storage/alpha.py:42`), not the journal's other inspected intervals.

7. **The single lane-wide confirmation window can be burned on noise.**
   - The first campaign that freezes anything consumes 2024-01→2026-07 for every future campaign (docs/alpha-pooled-mining.md "Window consumption is the irreversible step").
   - Under check C, null campaigns reached confirmation in 1/40 (intraday fixed set) and 4–5/40 (genetic) replicates (docs/alpha-pooled-intraday-v1-2026-10-14.md; docs/alpha-pooled-checks-v3-2026-10-05.md).
   - No spending rule exists across campaigns, and there is no prospective interval.

8. **A fixed set reuses a gate built for search.**
   - A predeclared set of 8 still faces t ≥ 3, 3 of 4 blocks, a 0.5× selection haircut, then Holm, a positive leg ci90, and a positive recent 12 months (`campaign.py` 732-739, 789-794, 833-842).
   - Discovery uses about 54% of the history. With no search, Holm over the set on discovery∪selection would control family-wise error by itself, which is the research doc's own argument (docs/alpha-pooled-mining-research-2026-09-28.md "Multiple-testing control…").
   - Also, the confirmation's leg-ci90 requirement (835) makes a pass depend on 2024–26 market drift, which mixes beta into an alpha gate.

9. **The design is long-only, single-formula, and has no combination stage.**
   - Negative edges are discarded as "hypotheses only". Some replicate in sign across disjoint cohorts: `quiet_momentum` (t −3.34 and −1.69), `intraday_high_proximity` (−0.59 and −2.33), `overnight_persistence` (−1.93 and −1.18).
   - They all describe buying recent strength, which is plausibly what the native entries do; those trail SPY by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md "What this says" 3).
   - No ensemble or composite exists. "One formula = one trial" structurally forbids the way industry gets usable IC from many 0.01-IC signals (Gu-Kelly-Xiu 2020; Jensen-Kelly-Pedersen 2023).

10. **Adding a data source costs about 10 touch points, and formulas cannot mix sources.**
    - Each source needs its own:
      - protocol subclass with a duplicated "fixed_set in v1" validator (`campaign.py` 414-418, 460-465, 503-508, 563-568)
      - Inputs model with a repeated `CorporateActionInputs`
      - Sources, load and verify trio (`runner.py` 93-195) and `CubeBuild` slot (207-209)
      - `*_document` functions (`scoring.py` 289-321)
      - branches in `score_book` (324-377) and `frozen_document` (`campaign_run.py` 239-256)
      - manifest fields (`power.py` 337-341, 372-374)
      - an N×N CLI mutual-exclusion matrix (`cli/commands/alpha.py` 1656-1715)
    - `build_cube_inputs` refuses more than one source (`runner.py` 317-318). So interaction hypotheses are inexpressible: earnings reaction × short flow, or insider buying × reversal (the Jeng-Metrick-Zeckhauser formula only gets `returns`).

11. **No cross-sectional operators and no authoritative sectors.** The DSL is per-symbol (`formula.py` 111-132); the operators are listed at `operators.py` 109-167. Sector labels exist only for config-group names (docs/alpha-pooled-literature-2026-09-30.md "Picks by sector"). Cohort v3's 1,000 names have none.

12. **Survivorship is still unresolved.**
    - Both cohorts are current members (`cohort-v3.json` "survivorship"). The leg-mean gates in discovery and confirmation read absolute R, which this flatters.
    - The spike found that SIP serves delisted names' bars but that no dated delisting list exists (docs/alpha-roadmap.md line 625).

13. **R is uncapped in the pooled and a priori statistics, but winsorised in the counterfactual.**
    - There is no risk-unit floor in `label_bracket`, and neither `stats.py` nor `apriori/statistics.py` winsorises. `bracket_counterfactual.winsorized` (172-177) does, at ±10R.
    - The GPOR cell (+1,546R) shows the failure mode (docs/setup-recurrence-2026-10-14.md "Robustness window").

14. **The setups' SPY excess is beta-one.** `market_r` scales SPY's return by entry/risk (`outcomes.py:219`). In a market that rose +0.21R per window (docs/bracket-counterfactual-2026-10-18.md "Descriptives"), a beta below 1 explains part of the −0.11R "excess" without any alpha deficit.

15. **Check A is recomputed when nothing changed.**
    - Check A is formula-independent, yet it is re-run per protocol hash and revision (`campaign_run.py` 191-208). That cost about 1.5–3.7 h on 6 cores per protocol (v4 1 h 40; cohort v3 about 3 h × 3; short-v3 3 h 40).
    - The operator noticed the heat (docs/alpha-pooled-cohort3-2026-10-16.md "Operational notes").

16. **Stats helpers are duplicated and private ones are imported across packages.** `trimmed_mean` exists twice (`pooled/stats.py` 156-160 and `apriori/statistics.py` 73-77). Private `_ci90`, `_p_one_sided_positive`, `_stationary_index_draws` and `_finite_json` are imported from `setups/baserates.py` and `setups/study.py` (`pooled/stats.py` 25-26, `apriori/statistics.py` 16-17, `power.py:47`). Production code also imports `research.*` (section 2, Seams).

17. **Decile evidence does not transfer to the cards.** Cards trade the top 1–2 names. `top3_edge` is computed only at confirmation (`campaign.py` 826-829), which no campaign has reached. The lane has produced no evidence at the selection depth the cards actually trade.

## 7. Improvement opportunities, by expected effect on cards and discovery

1. **Store risk arrays in the cube and test against a risk-matched control.**
   - Add `atr_pct`, a 60-session beta, log dollar volume and an SIC sector to the cube. The SIC code is free per CIK from EDGAR `submissions`, and the insider lane already maps tickers to CIKs.
   - Make the decisive statistic the pick residual after a per-session cross-sectional regression of R on those exposures, or a DGTW-style matched cell (sector × ATR quintile) (Daniel-Grinblatt-Titman-Wermers 1997).
   - Expected effect: the standard error of persistent formulas falls towards the fast formulas' 0.006–0.010R, worth several times more data. It also tests the low-volatility confound in gap 3, which makes the 64 existing nulls readable.
   - The cube can be rebuilt from cached bars (v2's build is cache-only).

2. **Report each formula's power and an equivalence bound.**
   - In check C's null panels, plant δ on *each real formula's* picks to get a per-formula MDE.
   - Publish edge ci90 and MDE in every result table. A null should read "edge < X R at 90%".
   - The cost is minutes (check C on a fixed set takes 42 s; docs/alpha-pooled-checks-v4-2026-10-07.md).

3. **Add a horizon-matched cube.**
   - Add a 5-session time-exit cube (or 1-session) for the short-horizon priors: reversal, short flow, overnight and intraday. Live cards can execute it via `session_count_v1`.
   - It is a declared new protocol per set. The discovery window is reused, but the label is new.

4. **Add an event-entry selection rule.**
   - Enter on the first session the condition turns true, skip names already held, and compare against a control of names with the same kind of event. Add it to `CampaignProtocol.select`.
   - This generalises PEAD's passing design to insider "fresh purchase", exdiv yield terciles and EDGAR-timestamped PEAD v3.
   - The literature favours event controls because of the announcement premium (Frazzini-Lamont 2007).

5. **Build a combination stage as one trial.**
   - Add `cs_zscore`, `cs_rank` and `group_demean` to scoring. The data-augmentation doc (docs/alpha-data-augmentation-2026-10-18.md §3) also asks for this.
   - Pre-register one composite: an equal-weighted average of the *literature-signed* z-scores. Take the signs from the papers, never from observed t, and add a walk-forward ridge variant.
   - Test it as one formula on the nearly unread selection window, which only `shorts_retreat` has touched, then confirm.
   - Use Chen-Zimmermann's Open Source Asset Pricing signal library (code plus published t-stats) as the prior library. Use JKP themes (Jensen-Kelly-Pedersen 2023) for hierarchical shrinkage instead of flat per-formula gates.

6. **Govern the confirmation window.**
   - Add a prospective confirmation interval (2026-08→; about 250 sessions a year) and a spending rule per campaign family.
   - Generalise `LITERATURE_ENTRIES` into "any journaled study whose inspected interval overlaps", with PEAD and exdiv included.
   - Expected effect: the one clean historical window is no longer lost to a 2.5–12% null survivor.

7. **Use a fixed-set protocol v5 for future sets only.**
   - Use Holm over the set on discovery∪selection (about 1,860 sessions) at one-sided 0.05, then confirm.
   - Rough gain: about 1.15× from the lower threshold (2.5 vs 3) times about 1.17× from √(1,860/1,357), so the minimum detectable effect falls by about 25%.
   - Move the leg-ci90 requirement from the gate to the probe.

8. **Bridge to cards before more mining.**
   - Record each native card candidate's percentile on the cross-cohort negative characteristics (20-day strength outside earnings, 140-bar high proximity, overnight gap) in `decision_provenance`.
   - Test a predeclared veto on the setup replay over a window no study has read. The native entries' −0.10…−0.15R gap to SPY is where card quality is lost; this is hypothesis-generating only.

9. **Make field sources plugins.**
   - One `FieldSource` registry (pins, load, verify, coverage, field names) replaces the subclasses and isinstance ladders, and `with_fields` should join the union of frames.
   - Identities stay byte-hash, so the old files still load. CLAUDE.md forbids compatibility aliases, so update the callers in the same change.
   - Expected effect: each new source drops to about two touch points, and multi-source formulas become possible.

10. **Fix survivorship point-in-time.** Build a dated delisting list from EDGAR Form 25 (and Form 15) filings and combine it with the SIP bars the spike showed exist. Rebuild the cohorts point-in-time. This is required before any leg-mean gate can be read as an absolute claim.

11. **Clean up the statistics.**
    - Floor the risk unit (for example ≥ 0.5% of price) and winsorise R in the pooled and a priori statistics, matching the counterfactual.
    - Use a beta-adjusted excess (β from 60 sessions before entry) in `market_r`.
    - Move the bootstrap, ci90, Holm and trimmed-mean helpers into one public `research/stats.py`.

12. **Cache check A.** Key it on (cube SHA, selection, gates, bootstrap, power spec, stage-code digest) instead of protocol and revision. That saves about 2–3.5 h of 6-core compute per new protocol. Separately, parallelise the hourly acquisition, which was 11 h sequential on v3.

## 8. Open questions

- What is each real formula's score autocorrelation? I could not tell how far from φ=0.95 each one sits, or how much check A overstates its power, without running code.
- Is v2's negative mean t a volatility or R-scaling artefact, or a real effect? It needs ATR in the cube.
- Do the journal's inspected-interval records already count the PEAD, exdiv and setup-outcome reads of 2024–2026 against the pooled lane? `consume_pooled_confirmation` checks only `pooled/confirmation`.
- What is `pead_long`'s live forward record (closed trades, realised R against the counterfactual)? No result doc exists. Martineau (2022) reports PEAD largely gone in large caps since the mid-2000s, and the study's own liquidity terciles are low +0.189 and high +0.132R.
- What is the status of the insider protocols' checks and campaigns after #128 merged? With buy-side conditions on sparse large-cap purchases, how many in-condition names per session does `insider-v1` have, and is a decile of them meaningful?
- Does the operator accept prospective confirmation (about a year of waiting) in exchange for keeping the historical window clean?
