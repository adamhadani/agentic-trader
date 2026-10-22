# Holistic review findings register — P2 (23 findings)

Each canonical finding as consolidated from the seven expert lenses, followed by its independent
fact check (a skeptic told to refute it against the code) and value review (priority and horizon).
Where the fact check corrected a claim, the corrected claim governs. See [README](README.md).

<a id="c02"></a>

## C02 — Mining has no cadence and the last confirmation window is ungoverned: add a daily prospective scorer, scheduled refits and a window-spending rule, and be honest about forward power

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | alpha_mining | gap | high | M | partially_confirmed |

_lenses: quant, ml, architecture, product_

### Claim (as consolidated)

The weekly miner is retired (launchd.sh:21), campaigns are manual one-shots, and no worker scores the ~72 already-charged frozen formulas forward.

The daemon's research workers produce unusable records:
- All 10,359 shadow forecasts are invalid: 6,915 contract mismatch and 3,444 stale_closed_bar.
- 3,559 session decisions are unavailable because of IEX minute gaps.
- The daily panel NaNs a whole day's IC when 1 of 64 labels is missing (daily_forecasts.py:551).

Window governance:
- Consumption of the confirmation window is lane-wide and irreversible on any freeze.
- The overlap rule lists only two literature entries (campaign_run.py:107), although PEAD v2, exdiv and setup outcomes already read 2024–26.
- Identity is immutable (models.py:127-135), with no notion of a scheduled refit under a fixed spec.

Power: at the median SE (~0.04R per year), forward detection at 80% power takes about 10.7 years for 0.03R, 3.8 for 0.05R and 1.5 for 0.08R. A forward scorer is therefore a confirmation and decay monitor, not a discovery engine. The live vol_20 prospective read (~150–200 candidates) is similarly underpowered.

### Evidence

- scripts/launchd.sh:21, 202-204 (miner retired)
- agentic_trader/cli/commands/service.py:24-40, 675-701 (forward workers: session and daily panel only)
- agentic_trader/research/alpha/daily_forecasts.py:550-551 (one missing label NaNs the session)
- agentic_trader/research/pooled/campaign_run.py:107 (LITERATURE_ENTRIES)
- agentic_trader/research/alpha/models.py:127-135; research/alpha/clock.py:47-50, 76-79
- docs/alpha-pooled-mining.md:615-622 (window consumption is irreversible)
- Live production map Q13 (10,359 invalid shadow forecasts; 3,559 unavailable session decisions)

### Recommendation

1. ProspectiveScorer daemon worker on the DailyPanelService pattern, running ~08:30 ET. Each session it:
   - scores every frozen formula or spec, plus one predeclared composite, at D-1 from the shared store;
   - journals the top-decile and top-2 picks with the eligible control before outcomes exist;
   - labels them at maturity with label_bracket;
   - keeps an always-valid confidence sequence on the paired edge;
   - tolerates up to k missing names, with reason codes.
   Specs already charged incur no new trials.

2. Schedule:
   - nightly data refresh;
   - daily scoring;
   - quarterly walk-forward refit. The trial identity is the spec SHA; each refit is a pinned artifact, not a new trial.

3. Promotion: a confidence-sequence lower bound above 0 after at least 120 sessions makes a spec eligible for the selector or probe. Use this to confirm composites (δ≥0.05R) and retire decayed signals, never to promote single small-edge formulas.

4. Window governance:
   - reserve 2024-01→2026-07 for the predeclared composite (C06);
   - give every other campaign a discovery-only mode;
   - replace LITERATURE_ENTRIES with a check against every journaled inspected interval.

5. Retire the sinks:
   - demote the 16 contract-mismatched shadow alphas;
   - disable the IEX session-decision worker;
   - give the daily panel a missing-label rule;
   - replace the live vol_20 read with a declared second use of the 2017–21 replay.

### North-star impact

Gives mining the 'reasonable cadence' the operator asked for without spending the last historical window. Near-misses such as shorts_retreat get a genuine out-of-sample path, and a confirmation arrives already wired to cards. The limits on forward power are stated in advance.

### References

- Howard, Ramdas, McAuliffe & Sekhon (2021), Time-uniform, nonparametric, nonasymptotic confidence sequences, Annals of Statistics 49(2) — Valid daily peeking for the scorer and its promotion rule (https://arxiv.org/abs/1810.08240)
- Ramdas, Grünwald, Vovk & Shafer (2023), Game-Theoretic Statistics and Safe Anytime-Valid Inference, Statistical Science 38(4) — E-values for continuously monitored forward evidence
- McLean & Pontiff (2016), JF 71(1) — Post-publication decay makes forward monitoring and scheduled refits necessary

### Fact check — partially_confirmed

**Corrected claim**

Mining has no cadence. The per-symbol miner is retired (scripts/launchd.sh:21, 202-204). Pooled campaigns are manual, operator-approved one-shots. No daemon worker scores the 64 charged pooled fixed-set formula-cohort pairs forward: pooled/ledger shows formulas_charged 64, campaigns 8, confirmations 0. None of these formulas is "frozen", because nothing passed selection.

The forward lanes that do exist produce no usable evidence:
- Scan-time shadow evaluation (research/alpha/shadow.py:91,159) has written 10,397 forecasts as of 2026-10-21, all invalid: 6,953 deployment_data_contract_mismatch and 3,444 stale_closed_bar (clock.py:47-50,76-79). They come from only 4 of the 16 shadow versions.
- The other 12 shadow versions are session-clock alphas. Their decision worker has 3,583 unavailable decisions (3,487 SessionCoverageError, 96 SessionAcquisitionError) and 222 missed, with none scored.
- The daily panel voids a whole session's IC when any single label is missing (daily_forecasts.py:550-551).

The pooled confirmation window (2024-01-02 to 2026-07-31) is single-use. It is consumed lane-wide and irreversibly once any candidate freezes (storage/alpha.py:1141-1171; docs/alpha-pooled-mining.md:607-622).

Second-look governance has two mechanisms, not one:
- the pick-overlap rule against two literature entries (campaign_run.py:107, 211-236);
- each protocol's declarative `excluded_families`, which already excludes the families that read 2021-2026 with positive results: setup outcomes (vol_20, sector_relative_momentum), factor controls and the sector panel (config/research/pooled/*.json; docs/alpha-pooled-mining.md:593-597).

The real gap is narrower. Neither mechanism covers the a priori PEAD v2 and exdiv studies (both read 2016-08 to 2026-07), even though events-v1 includes PEAD-style formulas and dividend_yield. The exclusions are also hand-maintained, not checked against the journal.

Refits: registry and pooled identities are immutable hashes (models.py:127-135; campaign_run.py:239-255). The prospective daily panel already does a scheduled per-session rolling Ridge refit under a frozen plan (daily_forecasts.py:247-297). A refit-under-fixed-spec notion therefore exists, but only in that lane, and parameter-free pooled formulas need no refit.

The power arithmetic is correct. At SE ≈ 0.039R per year, forward detection at 80% power (one-sided 5%) takes about 10.7, 3.8 and 1.5 years for 0.03, 0.05 and 0.08R.

**Checked**

- scripts/launchd.sh:21 (MINER_RETIRED_NOTICE) and :202-204 (install-miner only prints the notice): confirmed
- agentic_trader/cli/commands/service.py:24-40 imports and :675-701: the daemon's research workers are the session observer/decision workers and the daily panel. The card-stats and corporate-actions workers also run, and no pooled formula is scored forward. grep finds no research.pooled import outside the research/CLI modules
- agentic_trader/research/alpha/shadow.py:91,159 and storage/alpha.py:720-725: shadow forecasts are written by scan-time shadow evaluation (actor 'shadow_scan'), not by a research worker
- Read-only production DB via scratchpad q.sh (alpha_projections): forecast/* = 10,397, all valid=false (6,953 deployment_data_contract_mismatch, 3,444 stale_closed_bar), from only 4 version_ids. session-decision/* = 3,583 unavailable (3,487 SessionCoverageError, 96 SessionAcquisitionError) and 222 missed, from 12 version_ids. registry: active 0, probe 1, shadow 16. pooled/ledger: formulas_charged 64, campaigns 8, confirmations 0
- agentic_trader/research/alpha/clock.py:47-50 (stale_closed_bar) and :76-79 (deployment_data_contract_mismatch): the reason codes exist as cited
- agentic_trader/research/alpha/daily_forecasts.py:549-551: `ic_scores = scores if not missing else scores * np.nan`. Confirmed that one missing label voids the whole session's IC. The DB holds 1 daily-outcome so far
- agentic_trader/research/pooled/campaign_run.py:107 LITERATURE_ENTRIES (high52-v1, reversal-lowmax-v1) and :211-236 (literature_codes and already_tested, a Jaccard pick-set overlap rule)
- config/research/pooled/*.json excluded_families and research/pooled/campaign.py:138-141,278 (declarative only, not enforced in code). Setup outcomes, factor controls and the sector panel are excluded as reading 2021-2026. The apriori PEAD and exdiv studies are not listed. events-v1 contains the PEAD-style formulas and dividend_yield
- agentic_trader/storage/alpha.py:1141-1171 consume_pooled_confirmation: lane-wide overlap refusal, append-only, no release path. Confirmed irreversible
- docs/alpha-pooled-mining.md:580 (windows), :590-622 (excluded families, single-use confirmation, lane-wide consumption), :722-760 (literature overlap rule)
- docs/apriori-pead-2026-09-26.md:26 and docs/apriori-exdiv-2026-10-12.md:13: both read 2016-08-01 to 2026-07-31, which covers 2024-26
- agentic_trader/research/alpha/models.py:127-135: version_id of the registry AlphaDefinition. The pooled frozen identity is a separate mechanism, campaign_run.py:239-255 frozen_document
- agentic_trader/research/alpha/daily_forecasts.py:247-297: the daily panel refits Ridge every session on a rolling, strictly mature training window under a frozen plan
- docs/alpha-roadmap.md:298-303, 630, 667 (vol_20 prospective-only read 2026-10-21 to 2026-11-04; sparse-layout contract deferred)
- Power arithmetic: re-ran the read-only scratch script holistic-review/pooled64.py. Median discovery SE is 0.0169 over 1,357 sessions, so about 0.039R per year. At one-sided 5% and 80% power, detection takes 10.7, 3.8 and 1.5 years for 0.03, 0.05 and 0.08R. Reproduced. Per-lane median SE ranges from 0.0099 to 0.032
- References: arXiv 1810.08240 = Howard, Ramdas, McAuliffe & Sekhon, Ann. Statist. 49(2):1055-1080 (2021). Ramdas, Grünwald, Vovk & Shafer, Statist. Sci. 38(4):576-601 (2023). McLean & Pontiff, JF 71(1):5-32 (2016). All exist and fit their stated use

**Bad references**

- agentic_trader/research/alpha/models.py:127-135: cited as the identity relevant to a 'scheduled refit' gap. It is the registry AlphaDefinition hash, not the pooled frozen identity (campaign_run.py:239-255). The daily panel's rolling Ridge refit (daily_forecasts.py:247-297) contradicts 'no notion of a scheduled refit under a fixed spec'
- agentic_trader/research/pooled/campaign_run.py:107: cited as the only overlap governance. It omits the protocols' `excluded_families`, which already cover the setup-outcome, factor-control and sector-panel reads of 2021-2026, so 'setup outcomes' is not an uncovered case

**Notes**

The core of the finding holds:
- Mining has no cadence and no forward scorer exists for the charged pooled formulas.
- The shadow and session lanes are blind, and the daily panel voids a session's IC when any one label is missing.
- The confirmation window is single-use, lane-wide and irreversible.
- The forward-power numbers are right, and all three references exist and fit their use.

Corrections:
1. Counts have grown since the map. As of 2026-10-21 there are 10,397 invalid forecasts and 3,583 unavailable session decisions, plus 222 missed.
2. "~72 frozen formulas" should read "64 charged formula-cohort pairs, none frozen". The 16 insider pairs are not yet charged.
3. Shadow forecasts come from scan-time shadow evaluation, not from a research worker.
4. Only 4 shadow versions are contract-mismatched. The other 12 fail on IEX session coverage. "Demote the 16 contract-mismatched shadow alphas" should be split into re-declaring or retiring the 4 fixed-duration alphas and dealing with the 12 session-clock alphas through the sparse-layout or retire decision.
5. The overlap gap is real but narrower than stated, as given in the corrected claim. The useful recommendation still stands: replace the hand-maintained `excluded_families` and literature lists with an automatic check against journaled inspected intervals.

The production counts came from read-only queries (default_transaction_read_only) through the workflow's existing scratchpad helper ~/agentic-trader-research/holistic-review-20261021/q.sh. I edited no repo files and ran no copilot command.

### Value review — P2, next (weeks)

**Reasoning**

I would fund a cut-down version and not the headline build. The bundle mixes cheap, urgent governance and cleanup work with a medium-effort daemon that has nothing worth scoring.

**What the code and docs confirm**
- The miner is retired (scripts/launchd.sh:21, 202-204).
- The daemon's only forward research workers are the session observer and decisions (IEX, SPY/QQQ, config/config.yaml:493-507) and the daily panel (agentic_trader/cli/commands/service.py:675-701).
- One missing label sets the whole cross-section to NaN: `ic_scores = scores if not missing else scores * np.nan` (agentic_trader/research/alpha/daily_forecasts.py:550-551).
- The literature overlap rule covers only two entries (agentic_trader/research/pooled/campaign_run.py:107). Those entries are used for pick-set Jaccard deduplication, not for interval checks (docs/alpha-pooled-mining.md "literature entries", ~L727-742).
- Window consumption is lane-wide and irreversible (docs/alpha-pooled-mining.md ~L615-622).
- The live map's 0 valid shadow forecasts and 0 scored session decisions (maps/live.md §4, gap 6) make the promotion path unreachable today.

**Why the ProspectiveScorer daemon is weak value now**
1. Almost every one of the ~72 charged formulas is a known null or negative. In the v4 campaign 7 of 8 discovery edges were negative (docs/alpha-pooled-checks-v4-2026-10-07.md "The campaign"). Scoring them forward mostly monitors nulls.
2. The power claim is right for cohort v2 but too pessimistic for cohort v3. Backing per-year SE out of the reported t-stats:
   - cohort v2: about 0.03-0.09R per year, median about 0.05, in the v4 table;
   - cohort v3: about 0.011R per year, from shorts_retreat's +0.020R at t 3.40 over 836 sessions (docs/alpha-pooled-short-v1-2026-10-20.md, discovery table).
   Even so, the one live hypothesis, shorts_retreat, had a selection edge of +0.006R (se 0.007). Forward detection of that needs decades, and longer still once the confidence-sequence penalty and multiplicity across 72 specs are counted. The recommendation leaves multiplicity out entirely.
3. A daemon is not needed for validity. Data after 2026-07-31 is already a fresh confirmation window, and the doc names this route (alpha-pooled-mining.md ~L620-622). A spec frozen and journaled now can be read once as a batch campaign from cached historical SIP bars in August 2027. That avoids the IEX/SIP contract mismatch that blinded all three existing workers.
4. Scheduled refits only apply to fitted models. Pooled formulas have no parameters, and the composite (C06) does not exist yet.

**The parts worth doing in the next few weeks**
- **Confirmation-window policy.** Insider-v1 and cohort3-insider-v1 campaigns are about to come up for approval (CLAUDE.md, insider lane). A selection pass there would consume the 2024-01→2026-07 window lane-wide under today's rules.
  - The 2024-26 window is already partly read by other studies: setup outcomes, the bracket counterfactual and recurrence over 2021-2026, and the PEAD/exdiv a priori legs.
  - So the window is clean only with respect to particular hypotheses. The overlap rule needs to be hypothesis-family-aware; a plain date-interval check would refuse almost everything.
- **Retire the sinks.**
  - Disable the IEX decisions worker.
  - Demote the 16 shadow alphas whose data contract does not match the deployment feed.
  - Add a k-missing-label rule with reason codes to the daily panel, so it does not go as blind as the session lane.
  This removes noise from Telegram, which carried 659 incident notices against 41 cards (maps/live.md gap 9), and it stops the system looking as if it has a working promotion path.

**Effect on the north star**
None of this produces a card within months. The cleanup protects the confirmation window and the operator's attention, and the scorer becomes useful only once a composite or probe candidate exists.

**Risks**

- **Another blind worker.** This would be a fourth daemon research worker, and the three existing ones have produced 0 valid records out of about 14k because of the feed-contract mismatch and all-or-nothing coverage. Nightly D-1 scoring of 1,000+ names, plus FINRA, EDGAR and Nasdaq refreshes, adds new failure surfaces and readiness components and more incident noise on Telegram.
- **Uncorrected multiplicity.** Confidence sequences across ~72 specs without a multiplicity correction would produce false 'eligible' flags. With a correction, power falls further.
- **A refit loophole.** "A refit is a pinned artifact, not a new trial" opens a degrees-of-freedom loophole unless the spec fixes every refit choice.
- **Coupling to an unbuilt item.** Reserving the window for C06 ties governance to a composite that does not exist.
- **Over-refusal.** An interval-only overlap check would refuse nearly every campaign, because setup, bracket and recurrence replays already cover 2021-2026.
- **Timing.** If the window policy is not settled before the insider campaigns are approved, one marginal single formula could spend the last historical window.
- **False comfort.** Forward CS monitoring can be read as an "alpha pipeline in progress" when its expected yield is about zero for the current frozen set.

**Prerequisites**

1. An operator decision on the confirmation-window policy, recorded in docs/alpha-pooled-mining.md before any insider-v1 or cohort3-insider-v1 campaign is approved. It should cover:
   - whether 2024-01→2026-07 is reserved;
   - whether other campaigns run discovery-only;
   - an inventory of which hypotheses have already read 2024-26.
2. A candidate actually worth scoring forward: a frozen C06 composite spec, or an explicitly declared second look such as cohort-v3 shorts_retreat.
3. A data contract for forward D-1 scoring: historical SIP daily bars through the existing cube acquisition, not the desk's IEX feed.
4. A predeclared multiplicity and stopping rule for monitoring several specs at once.
5. The sink cleanup (decisions worker off, mismatched shadow alphas demoted, the daily-panel missing-label rule) needs none of the above.

**Better alternative**

A cut-down version, about 2-3 days of work, instead of an M-effort daemon:

**(a) Governance first**
- Write the window policy and an inventory of which hypotheses have read 2024-26.
- Make the overlap rule hypothesis-family-aware: extend LITERATURE_ENTRIES (agentic_trader/research/pooled/campaign_run.py:107) to the a priori, setup and bracket studies, rather than checking date intervals only.
- Do this before approving the insider campaigns.

**(b) Retire the sinks**
- Set config/config.yaml:500-507 `decisions.enabled` to false.
- Demote the 16 mismatched shadow alphas.
- Replace the all-or-nothing NaN at daily_forecasts.py:550-551 with a rule that tolerates up to k missing labels, records a reason code for each, and requires min_assets observed.

**(c) A frozen forward declaration instead of a ProspectiveScorer**
- Journal the spec hashes and a forward window of 2026-08-01→2027-07-31 under the pooled ledger now.
- Read it once as a batch campaign from cached SIP bars in August 2027. This is the route the doc already names.
- For any candidate that needs live evidence sooner, use the existing capped `probe` registry path rather than a parallel CS promotion rule. It already gives forward evidence with real fills and wires to cards.
- Build a nightly scorer only after a composite exists that has a plausible edge of 0.05R or more on a cohort-v3-like cross-section, where the per-year SE is about 0.011R.

---

<a id="c05"></a>

## C05 — The decisive pooled statistic is not neutral to risk, beta or cost in R units: store risk characteristics in the cube, use matched or residualised controls and a shorter volatility-scaled training target

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | research_methodology | defect | high | M | partially_confirmed |

_lenses: quant, ml, literature_

### Claim (as consolidated)

How the statistic works now:
- session_table subtracts the equal-weighted mean r_cost of the eligible names (stats.py:54-105).
- R = return/(2×ATR14), with cost charged in R (cube.py:284-295; labels.py:144).
- The cube stores only eight arrays: no ATR%, beta, size or sector (cube.py:53).
- Campaigns forbid realized_vol and ts_std (campaign-v4.json:135-139).

Two artefacts follow:
1. Market drift enters R in proportion to each name's correlation with the market, so decile picks of names in idiosyncratic states earn a negative edge in rising tapes.
2. Cost in R falls as ATR% rises, so at 15 bp high-ATR picks gain about +0.017R mechanically.

The predicted signs match what was observed:
- v2 (high drift): mean t −0.59, 9 of 32 positive.
- v3 (high cost): mean t +0.46, 20 of 32 positive.
- The same formulas correlate only 0.44 across the two cohorts.
- The typical |edge| is about 0.015R, the same size as the artefacts.

Check C's name-demeaning (null_check.py:70-80) removes exactly these components, and it uses full-window means.

The v4 label diagnostic's two positives (illiquidity, momentum_12_1) were high-volatility deciles in a rising market, and naive IC t exceeds 3 for about half of 200 random persistent scores.

A 20-session horizon leaves only ~68 non-overlapping discovery cross-sections. Neither the R view nor the return view removes β, volatility or sector exposure.

### Evidence

- agentic_trader/research/pooled/stats.py:54-105 (equal-weighted pool control)
- agentic_trader/research/pooled/cube.py:53, 284-295 (no risk arrays; risk = 2×ATR; cost in R)
- agentic_trader/research/pooled/null_check.py:70-80 (name-demeaned null)
- config/research/pooled/campaign-v4.json:135-139; cohort3-daily-v1.json decision_cost_bps 15
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic'; docs/alpha-pooled-cohort3-2026-10-16.md 'Reading'
- agentic_trader/research/setups/outcomes.py:219; research/setups/card_stats.py:43-62 (card side has no excess)
- ~/agentic-trader-research/holistic-review-20261021/pooled64.py (cohort-level t means)

### Recommendation

1. Run a diagnostic now; it needs no new window. Re-score the 64 formulas on r_gross and with cost in bps, and regress each formula's session edge on control_mean, reporting the intercept and slope.

2. Build cube v2 from cached bars, as a new CubeSpec identity. Per cell, store:
   - atr_pct;
   - ex-ante beta_60 against SPY;
   - log dollar volume;
   - SIC → FF12/49 sector;
   - forward β-hedged, volatility-scaled returns at 1/5/20/60 sessions, with cost in bps.

3. Make the decisive statistic the per-session cross-sectional regression residual, or a DGTW cell (sector × ATR quintile × β tercile). Keep the equal-weighted pool as a cross-check, and report raw, residual and bracket results side by side.

4. Train the model lane on the cross-sectionally residualised 5-session volatility-scaled return, which gives about 272 non-overlapping discovery cross-sections.

5. Recalibrate check C with these controls instead of name-demeaning.

6. Use the risk characteristics as neutralisers, not as alpha features. Load Ken French's daily FF5 and momentum factors for attribution.

### North-star impact

Prevents a future 'finalist' from being a volatility, beta or cost tilt that turns into negative excess once it becomes a card. It makes the 64 nulls readable and shrinks the SEs of persistent formulas, which the composite (C06) needs.

### References

- Daniel, Grinblatt, Titman & Wermers (1997), Measuring Mutual Fund Performance with Characteristic-Based Benchmarks, JF 52(3) — Characteristic-matched control cells
- Fama & MacBeth (1973), Risk, Return, and Equilibrium, JPE 81(3) — Per-session cross-sectional regressions with controls
- Ang, Hodrick, Xing & Zhang (2006), The Cross-Section of Volatility and Expected Returns, JF 61(1); Novy-Marx & Velikov (2022), Betting against betting against beta, JFE 143 — Volatility and beta tilts carry return differences that decile picks inherit
- Kenneth R. French Data Library — Daily factor returns for residual labels (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

### Fact check — partially_confirmed

**Corrected claim**

The core is right. The pooled lane's decisive statistic is the pick-minus-pool mean of r_cost (stats.py:54-105). R is scaled by 2 x ATR14 with cost charged in R (cube.py:284-295; setups/labels.py:143-144), so cost in R = bps/ATR% and market drift in R scales with each name's correlation to the market. The lane has no beta, volatility or sector neutralisation anywhere.

The details need these corrections:

(1) The cube does store dollar_volume, a liquidity/size proxy (cube.py:53, 111, 333), and the single-entry study already stratifies residuals by liquidity tercile (study.py:72-83). The cube lacks ATR% (computed in Eligibility.atr, cohort.py:185, but not stored), beta, market cap and sector. 'Log dollar volume' therefore needs no new acquisition.

(2) Check C's demeaning (null_check.py:70-81, applied to the discovery view at :207) removes each name's discovery-window average R. That covers the persistent, name-level part of the drift and cost-in-R effects. It does not remove the state-dependent part, for example a formula picking a name in a transient high-volatility state. 'Removes exactly these components' is an overstatement. Consistent with the hypothesis, null seed t means are about -0.1 (short-v1 doc), against real cohort means of -0.59 (v2) and +0.46 (v3).

(3) Both cohorts use the same discovery tape (2016-08-01..2021-12-31). 'v2 high drift' means the cohort carries more long drift in R (leg about +0.18R vs about +0.08R, cohort3 doc 'Reading'), not a different market period. v2 runs at 5 bp, v3 at 15 bp. The sign pattern is a post-hoc consistency, not a test. The cleanest supporting case is `illiquidity` (a top-decile range-volatility score, ts_mean(hl_spread, 21)), which flips from -0.016R on v2 to +0.015R on v3. The +0.017R cost figure is an illustrative magnitude, not computed from cube data.

(4) The card side does compute an excess: excess_r = r_cost - market_r, where market_r is a beta-one SPY return in R units (outcomes.py:187-219, 382-383). Only the card_stats snapshot (card_stats.py:43-62) carries no excess. Neither side uses beta_i or volatility neutralisation.

(5) The single-entry pooled study already reports the gross edge (study.py:150-162). The campaign path scores r_cost only, so the 64 campaign results have no gross or bps view.

(6) Because live cards are sized in R, cost-per-R and drift-per-R differences are real P&L in card units. The defect is attribution (selection versus volatility, beta and cost tilt), not fictitious returns.

Residualisation machinery that could be reused exists in the per-symbol and panel lanes (alpha/panel.py:132, alpha/panel_study.py:228, alpha/factor_controls.py).

**Checked**

- agentic_trader/research/pooled/stats.py:54-105: session_table control_mean is the equal-weighted mean of r_cost over labelled cells (and, with purge=True, only cells whose exit falls in the window); edge = mean(picks) - control_mean. No beta, volatility or sector adjustment.
- agentic_trader/research/pooled/cube.py:53: eight arrays (eligible, labelled, r_gross, r_cost, holding, hit, tiebreak, dollar_volume). No ATR%, beta, market cap or sector, but dollar_volume IS stored (cube.py:111, 333).
- agentic_trader/research/pooled/cube.py:284-295: risk = 2 x ATR14 (eligibility.atr, through D-1); a long bracket with a 3R target; cost_bps_per_side passed to label_bracket.
- agentic_trader/research/setups/labels.py:143-144: r = (exit - entry)/risk_unit; r_cost = r - 2*bps/1e4*entry/risk_unit, which equals r - bps/ATR% when risk is 2 x ATR. So cost in R falls as ATR% rises (algebra confirmed).
- agentic_trader/research/pooled/cohort.py:181-190: Eligibility.atr is computed but not carried into the cube.
- agentic_trader/research/pooled/null_check.py:70-81, 207: demeaned() moves each name's labelled R (gross and cost) to R - name_mean + overall, using means over the whole discovery view.
- config/research/pooled/campaign-v4.json:135-139 (forbids realized_vol, ts_std, ts_mad; decision_cost_bps 5 at :42); cohort3-daily-v1.json:42 (15 bp). All protocols share the windows: discovery 2016-08-01..2021-12-31, selection 2022-2023, confirmation 2024-01..2026-07.
- grep over agentic_trader/research/pooled/*.py finds no beta, sector, market or neutralisation code in the pooled lane. The only stratification is residual_by_liquidity_tercile in the single-entry study (study.py:72-83).
- agentic_trader/research/pooled/study.py:150-162: the single-entry pooled study already reports the gross (r_gross) paired edge. The campaign path (campaign.py:722, 727, 787, 817, 828) scores r_cost only.
- agentic_trader/research/setups/outcomes.py:187-219, 382-383: market_r is a beta-one SPY return in the candidate's R units, and excess_r = r_cost - market_r is computed per card candidate.
- agentic_trader/research/setups/card_stats.py:43-62: CardKeyStats has mean_r_cost but no excess or market field.
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic': confirms that illiquidity and momentum_12_1 look good only because high-volatility deciles sat in a rising 2016-2021 market; per ATR the edge is gone. Naive IC t > 3 for about half of 200 persistent random scores.
- docs/alpha-pooled-cohort3-2026-10-16.md 'Reading': v2 leg means about +0.18R vs v3 about +0.08R; 15 bp vs 5 bp.
- Ran scratchpad holistic-review/pooled64.py. Spot-checked its rows against docs/alpha-pooled-checks-v4-2026-10-07.md, alpha-pooled-cohort3-2026-10-16.md, alpha-pooled-short-v1-2026-10-20.md, alpha-pooled-events-v1-2026-10-15.md and alpha-pooled-intraday-v1-2026-10-14.md; all matched. Reproduced: v2 mean t -0.59, 9/32 positive; v3 mean t +0.46, 20/32 positive; paired corr 0.44; median |edge| 0.015R.
- docs/alpha-pooled-short-v1-2026-10-20.md check C table: name-demeaned null seed-formula t means are -0.11 (v2) and -0.15 (v3), sd 1.13 and 1.18.
- Residualisation code exists outside the pooled lane: agentic_trader/research/alpha/panel.py:132 beta_adjust_scores, alpha/panel_study.py:228 _market_factor_betas, alpha/factor_controls.py, alpha/orthogonalization.py.
- References checked by web search: DGTW 1997 JF 52(3) 1035-1058; Novy-Marx & Velikov JFE 2022 pp. 80-106 (Jan 2022 issue, vol 143). Fama-MacBeth 1973 JPE 81(3), Ang-Hodrick-Xing-Zhang 2006 JF 61(1) and the Ken French Data Library URL (daily FF5 and momentum factors) are standard and correct.

**Bad references**

- Evidence parenthetical 'agentic_trader/research/setups/outcomes.py:219 ... (card side has no excess)' is wrong for outcomes.py: line 219 is the end of market_r, a beta-one SPY control in R units, and outcomes.py:382-383 computes excess_r = r_cost - market_r. Only card_stats.py:43-62 lacks an excess field.
- Claim 'cube stores ... no ... size (cube.py:53)' is inexact: cube.py:53 includes dollar_volume, a size/liquidity proxy (recommendation item 2 lists 'log dollar volume' as if it were new).

**Notes**

I could not refute the finding. The code facts behind it all hold:
- Pool-relative r_cost statistic.
- R measured against 2 x ATR, with cost in R (cost_R = bps/ATR%).
- No beta, sector or volatility neutralisation anywhere in research/pooled.
- realized_vol, ts_std and ts_mad forbidden. v4 still includes ts_mean(hl_spread, 21), which is effectively a volatility score, so forbidding the operators does not prevent volatility tilts.
- Check C uses discovery-window name demeaning.
- The 64-row aggregates are reproduced exactly from the docs, as is the label-diagnostic wording.

The 68 non-overlapping cross-sections figure (1,366 discovery sessions / 20) is correct for the full-window lanes. The short lane has only about 850 sessions, so about 42.

The mechanism argument holds approximately. With ATR proportional to sigma_i, beta_i*m/(2*ATR) is proportional to rho_i, so lower-correlation picks collect less market drift per R in rising tapes. The argument is a hypothesis consistent with the signs, not a demonstration. The recommended diagnostic (re-score on r_gross and in bps, regress the session edge on control_mean) is cheap and would test it directly.

Three corrections downgrade the verdict to partially confirmed:
- dollar_volume is stored in the cube.
- 'Exactly' overstates what check C's demeaning removes.
- The card side has a beta-one excess_r in outcomes.py; only card_stats lacks one.

The references are valid. DGTW's actual matching variables are size, B/M and momentum, so the recommended sector x ATR x beta cell is an adaptation, which is fine as cited.

### Value review — P2, next (weeks)

**Reasoning**

**The mechanism is real. What it means for the north star is limited, so fund the cheap part now and gate the rest.**

**What the code does now (confirmed)**
- `session_table` subtracts only the equal-weighted r_cost of the labelled eligible names (agentic_trader/research/pooled/stats.py:54-105).
- The risk unit is 2×ATR14 (agentic_trader/research/pooled/cube.py:284-288).
- Cost is charged in R as 2·bps·entry/risk (agentic_trader/research/setups/labels.py:144). At 15 bp per side (config/research/pooled/cohort3-daily-v1.json:42), cost in R is 15bp/ATR%. A high-ATR decile therefore gains about 0.02R mechanically against the pool, which matches the typical |edge| of 0.015R.
- The cube persists only eight arrays (cube.py:53). ATR is already computed per cell during eligibility (agentic_trader/research/pooled/cohort.py:185, 270-275) and then thrown away, so storing atr_pct is close to free.
- Check C removes full-window name means (agentic_trader/research/pooled/null_check.py:69-80). It does not remove time-varying beta or volatility exposure.

**Where the evidence is thin**
- The drift explanation for v2 (mean t −0.59) against v3 (+0.46) is post hoc. It rests on two cohort means of 32 correlated t-statistics (pooled64.py).
- The full record (t mean −0.06, sd 1.25) is indistinguishable from check C's calibrated null (docs/alpha-pooled-cohort3-2026-10-16.md "Check C"). The more likely reading of 64 nulls is still "no edge above about 0.06R in liquid names", not a hidden signal.
- One point supports the claim. docs/alpha-pooled-checks-v4-2026-10-07.md "Label diagnostic" already found that the raw-return positives for illiquidity and momentum_12_1 were high-volatility tilts in a rising tape.

**Why P2 and not P1**
- No pooled formula has a live scoring path, so a finalist would not produce a card without a further Part 2 spec.
- The three-stage gate (discovery, selection, Holm-adjusted one-use confirmation) already controls false positives. The artefact mainly costs power and makes nulls harder to read.
- The cost and drift artefacts are largest exactly where SEs are largest (persistent, factor-loaded scores, se 0.04R). Residualising would lower those SEs, but the median MDE of about 0.065R would probably only fall to about 0.045R. That is still above plausible large-cap anomaly sizes.

**What to fund**
- **Step 1, now, a few days of work.** r_gross is already in the cube, so this is a read-only, discovery-window-only re-score. Report:
  - r_gross and cost-in-bps edges;
  - the intercept and slope of each formula's session edge on control_mean.
- This should run before the insider campaign is read, because that frozen protocol inherits the same statistic. The step-1 output can sit beside it as a descriptive cross-check.
- **Steps 2–5 only if step 1 shows material slopes or cost shifts, and the operator keeps funding pooled mining after the insider campaigns.** These are the new CubeSpec with risk arrays, the residual decisive statistic, the 5-session volatility-scaled target and the recalibrated check C.
- **Step 6 needs changes:**
  - DGTW sector × ATR quintile × β tercile cells are too thin. At about 367 names per session (v2), that is about 2 names per cell. Use a per-session Fama-MacBeth residual on [atr_pct, beta_60, log dollar volume] instead.
  - Ken French factors are fine for attribution only.

**A bigger version of the same defect sits on the card side**
- The card side measures excess against a beta-one SPY control expressed in R (agentic_trader/research/setups/outcomes.py:187-219). The roadmap verdict "cards trail SPY by 0.10–0.15R" (docs/bracket-counterfactual-2026-10-18.md) and the card evidence behind `card_policy` both rest on that control.
- Fixing that yardstick directly changes how the operator judges card quality. Fixing the pooled statistic only changes how a research lane with no yield reads its nulls.

**Risks**

**Forking paths.** The new control would be chosen after 64 nulls have been seen.
- Re-scoring is a second look at a discovery window that has already been read. For `shorts_retreat` it is also a second look at the selection window.
- Any formula the new statistic "rescues" must be charged to the pooled ledger and stay descriptive. It needs a new frozen protocol, with checks A and C rerun at the new cube and protocol identity, before the unused confirmation window is touched.
- Risk of the wrong move: a post-hoc residual statistic that turns a null into a finalist.

**Over-neutralising for a long-only card book.**
- Low-volatility and beta tilts are part of what the operator actually earns. Residualising them in research while live cards are measured beta-one against SPY in R would leave research and live on different yardsticks. Both have to change together.

**Weak inputs.**
- SIC from EDGAR is current classification, not point-in-time.
- beta_60 is noisy for mid-caps.
- DGTW cells are too thin at the cohort sizes here.

**Horizon mismatch.**
- A 5-session volatility-scaled training target does not match the live 2R bracket or the 20-session cube bracket. A model trained on it can rank well and still fail as a card.

**Cost to the existing evidence.**
- A new CubeSpec identity invalidates every A/C gate binding, so all checks have to be rerun.
- The sign predictions (drift for v2, cost for v3) may not hold under the step-1 test, in which case steps 2–6 are wasted.

**Prerequisites**

**Before step 1**
- Insider checks A and C have finished, and no campaign has been read yet. Step 1's output can then be attached to the insider readout as a descriptive cross-check.
- Step 1 is journaled as a charged, discovery-window-only research read.

**Before steps 2–5**
- The operator has decided whether pooled mining continues after the insider campaigns.
- Step 1 shows a material control_mean slope or a material cost-in-R shift.

**Inputs for a cube v2**
- New CubeSpec version and a new protocol version, declared as a second look.
- SPY daily bars (already cached) for beta_60.
- ATR persisted from cohort.py, not recomputed.
- An EDGAR SIC pull (EDGAR access already exists) for sector.
- Checks A and C recalibrated with the new control before any campaign.
- A coordinated change to the card-side yardstick (outcomes.market_r), so research and live measure excess the same way.

**Better alternative**

**Do this instead of the full package**
1. **Step 1 only, now (days):**
   - re-score the 64 formulas on r_gross and on cost in bps;
   - regress each formula's session edge on control_mean.
2. **Persist two arrays, not a whole cube redesign:** atr_pct (already computed at agentic_trader/research/pooled/cohort.py:270) and beta_60.
3. **Add a per-session Fama-MacBeth residual** as a descriptive column in every future campaign readout. Promote it to the decisive statistic only in a new frozen protocol.
4. **Skip for now:**
   - DGTW cells;
   - multi-horizon targets;
   - the 5-session model-lane target, until a composite lane is actually funded.

**Higher leverage for the north star:** apply the same beta- and volatility-adjusted excess to card evidence. That means outcomes.market_r, cards outcomes and card_stats, which today use beta-one SPY in R units (agentic_trader/research/setups/outcomes.py:187-219). The verdict "cards trail SPY" and the `card_policy` gate depend on it directly.

---

<a id="c07"></a>

## C07 — Cohort membership is look-ahead for the discovery window, and the survivorship repair was deferred on a misread window: build a point-in-time cohort v4 before the next confirmation spend or any model fit

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | defect | high | M | partially_confirmed |

_lenses: quant, data, ml_

### Claim (as consolidated)

Cohort construction: names listed and active in 2026, ranked on SIP dollar volume over the 60 sessions ending 2023-12-29, explicitly 'not point-in-time' (cohort-v2.json:4; cohort-v3.json). Every protocol discovers on 2016-08-01→2021-12-31.

Pairing cancels a uniform survivor premium but not formula-specific selection:
- Missing dead losers affect reversal.
- Names that later grew large affect momentum and illiquidity.
- Flexible learners can fit the bias through interactions.

The survivorship spike stated 'discovery runs 2021-06 → 2026-01' (line 41) and deferred the repair on that basis. Yet it counted 152–310 now-inactive names per year above the $5M floor for 2016–21, with SIP bars for 97% of non-OTC inactive symbols. The free repair therefore covers exactly the discovery years.

Bar requests never set asof (providers.py:244-252), so reused tickers such as FISV and SBNY can contaminate rebuilds.

Separately, PEAD's surprise ≥5% filter uses today's consensus for past quarters. Its point-in-time-clean reaction-only rule earned +0.120R against a control of +0.094R.

### Evidence

- config/research/pooled/cohort-v2.json:4 and cohort-v3.json 'survivorship'; campaign-v2.json:10-22 and cohort3-* windows
- docs/survivorship-spike-2026-10-20.md:41 (wrong window), 'What was run' item 2 (per-year counts), 'Recommendation'
- agentic_trader/data/evidence.py:24-27 vs agentic_trader/data/providers.py:244-252 (asof never set)
- agentic_trader/research/pooled/cohort.py:193-292 (point-in-time eligibility exists; membership is current)
- docs/apriori-pead-2026-09-26.md 'Diagnostics'; docs/alpha-data-augmentation-2026-10-18.md §3

### Recommendation

Build cohort v4:
- Membership: the Alpaca inactive list for 2016–21, EDGAR Form 25/15 for 2022 onward, and the daily asset snapshot (C20) from now on.
- Eligibility: per session from trailing D-1 dollar volume via point_in_time_eligibility (cohort.py:193), never a 2023 ranking.
- Identity: asof-pinned bar requests and CIK-keyed identity.
- Delisting returns, by declared rule: last close for cash mergers; a Shumway-style haircut for performance delistings.

On v4, re-report the pooled leg means and PEAD's control as descriptive diagnostics. Train and evaluate any model only on v4. Correct line 41 of the spike doc.

Before the PEAD probe renewal, journal a declared second look at the reaction-only variant.

### North-star impact

Every absolute gate and every long base rate on a card stays an upper bound until this lands. It also prevents the first ML spec, or the irreversible confirmation spend, from 'confirming' a survivorship artefact.

### References

- Shumway (1997), The Delisting Bias in CRSP Data, JF 52(1) — Missing delisting returns bias anomaly estimates, especially for losers
- Brown, Goetzmann, Ibbotson & Ross (1992), Survivorship bias in performance studies, RFS — Survivor-only samples bias both return levels and apparent predictability
- Alpaca Stock Bars API, asof parameter — The correct way to request dead or reused tickers (https://docs.alpaca.markets/reference/stockbars)

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding is right. Cohorts v2 and v3 are built from names listed in 2026 (v2 also includes the current scan-universe config groups), ranked on raw SIP dollar volume over the 60 sessions ending 2023-12-29. Every pooled campaign protocol discovers on 2016-08-01 to 2021-12-31, so membership looks ahead for discovery. The 2023 ranking window also falls inside the selection window. Per-session D-1 eligibility already exists (cohort.py:193-292); only membership is not point-in-time. Line 41 of the survivorship spike misstates the discovery window as 2021-06 to 2026-01. The spike's own counts (152-310 now-inactive names above the $5M floor per year for 2016-21, with bars for 97% of the 2,209 ticker-like inactive non-OTC symbols) mean that the free Alpaca inactive list covers the discovery years. No bar request sets asof: fetch_bars at providers.py:244-252 and fetch_daily_many at providers.py:324-332. Alpaca defaults asof to the current day, so a dead or reused ticker can map to the wrong entity in a rebuild. That contamination is a plausible risk, not an observed one.

Several details need correcting:
(1) The spike did not defer the repair because of the wrong window. Its Recommendation (lines 58-61) defers because no fixed-set result was near a pass and because the live base rate is measured on the live flow. It also already proposed merging the Alpaca inactive list for 2016-2022 (line 63). The misread window only distorted its framing of where the coverage gap lies (lines 40-43).
(2) Formula-specific survivor selection is already acknowledged in docs/alpha-pooled-mining.md:1372-1380, so it is not a new insight.
(3) The PEAD doc says consensus is 'as served today, not guaranteed to be point-in-time'; the claim that today's consensus contaminates past surprises comes from augmentation §3. The reaction-only rule (+0.120R vs control +0.094R) avoids consensus revision, but it is not fully point-in-time clean: it still uses the Nasdaq calendar, which lacks delisted names.
(4) Card base rates come from journaled live scan candidates (card_stats.py:1-5), so they are not inflated by cohort survivorship. The bias applies to research cohort results and absolute gates (P1), not to 'every long base rate on a card'.
(5) high52-v1 and reversal-lowmax-v1 use a single 2016-08 to 2026-07 window rather than the discovery split.

**Checked**

- config/research/pooled/cohort-v2.json:4: the survivorship string reads 'listed and active on 2026-09-17; ranked on SIP raw dollar volume of the 60 sessions ending 2023-12-29; not point-in-time'. Cohort v2 also takes the current scan-universe config groups as a source (cohort-v2.json:5-10).
- config/research/pooled/cohort-v3.json:4 and screen-v2.json/screen-v3.json: the screen window ends 2023-12-29 (60 sessions) and membership is the names active on 2026-10-15.
- Discovery windows across all pooled protocols (campaign-v1..v4, intraday-v1, events-v1, short-v1, insider-v1, cohort3-*): every one is 2016-08-01 to 2021-12-31 (campaign-v2.json:10-22). high52-v1 and reversal-lowmax-v1 have no discovery split: they use one decision window from 2016-08-01 to 2026-07-31.
- docs/survivorship-spike-2026-10-20.md:41 says 'Cohort v2/v3 discovery runs 2021-06 → 2026-01', which is wrong. Lines 33-34 give per-year counts of 234/267/310/262/201/152 for 2016-2021 (152-310 is correct). Line 31 gives 2,147 of 2,209 (97%) with bars. Lines 56-64 are the Recommendation.
- agentic_trader/data/providers.py:244-252 (fetch_bars) and :324-332 (fetch_daily_many): StockBarsRequest is built without asof. agentic_trader/data/evidence.py:24-27 only lists asof as a field it records. A grep finds no asof= anywhere in agentic_trader.
- .venv alpaca/data/requests.py:101-107: the SDK supports asof. The Alpaca docs say asof defaults to the current day, maps renamed entities, and falls back to the symbol alone when the symbol is not found on that date.
- agentic_trader/research/pooled/runner.py:1-21, 248-270: the pooled cube's bars come through the shared BarSource/fetch_bars path, which has no asof.
- agentic_trader/research/pooled/cohort.py:193-292: per-session D-1 point-in-time eligibility already exists (20-session median raw dollar volume, $10 floor, ATR). Membership is the fixed symbol list.
- No delisting, Form 25 or inactive-membership code exists in agentic_trader (grep).
- docs/apriori-pead-2026-09-26.md Diagnostics: reaction-only rule +0.120 (n 7,869) and control +0.094 match. The Caveats say 'EPS and consensus values are as served today, not guaranteed to be point-in-time' and that survivorship affects Nasdaq history.
- docs/alpha-data-augmentation-2026-10-18.md §3: 'The Nasdaq page gives today's consensus for past quarters; surprise fields are therefore contaminated by later revisions.'
- docs/alpha-pooled-mining.md:1372-1380 already acknowledges that 'a formula that selects future survivors is still flattered' and that check C does not cover a tilt toward survivors.
- agentic_trader/research/setups/card_stats.py:1-5: card base rates come from journaled live full-universe-scan candidates, not from research cohorts.
- Web: Shumway (1997) JF 52(1):327-340 confirmed. Brown, Goetzmann, Ibbotson & Ross (1992) RFS 5(4):553-580 confirmed. Alpaca stockbars asof parameter confirmed.

**Bad references**

_none_

**Notes**

All four cited references check out:
- Shumway (1997) is JF 52(1):327-340. The -55% performance-delisting correction it implies comes from Shumway & Warther (1999).
- Brown, Goetzmann, Ibbotson & Ross (1992) is RFS 5(4):553-580. It is about fund performance studies, but its general survivorship-bias point applies here.
- The Alpaca stockbars docs confirm that asof defaults to the current day, maps renamed entities (the FB/META example), and falls back to the symbol alone when the symbol is not found on the asof date.

The finding's stated evidence that eligibility is point-in-time while membership is current is accurate.

The most valuable new item is that line 41 of the spike doc is factually wrong about the discovery window. The Alpaca inactive list (2016-2022) therefore lines up with discovery. Selection (2022-2023) and confirmation (2024-2026) would still need EDGAR Form 25/15 dates.

The asof gap applies to both bar request sites in providers.py: lines 244-252 and fetch_daily_many at 324-332.

### Value review — P2, next (weeks)

**Reasoning**

The defect is real and the evidence holds up. Every pooled protocol discovers on 2016-08-01 to 2021-12-31: campaign-v2.json lists that discovery window, and so do all cohort3-*, events-v1, short-v1 and insider-v1 configs. Yet the survivorship spike based its deferral on "discovery runs 2021-06 → 2026-01" (docs/survivorship-spike-2026-10-20.md:41). Under the real windows, the free Alpaca inactive list is most useful in exactly the discovery years: 152 to 310 now-dead names a year above the $5M floor (same doc, 'What was run' item 2). So the deferral rested on a factual error.

The cohorts are survivor-conditioned and ranked on 2023 liquidity (cohort-v2.json:4; the cohort-v3.json survivorship note). Bar requests never set asof (providers.py:244-252), although evidence.py:26 whitelists it.

The infrastructure is closer than "M" suggests. point_in_time_eligibility already drops a name once its bars stop: close_on returns None, giving "no_raw_close" (research/pooled/cohort.py:173-177, 260-263). Adding dead symbols to the discovery cube needs no new eligibility logic.

I still would not fund the whole package now:
1. **It produces no cards.** The live native funnel trails SPY because it lacks edge, not because of research survivorship. Card base rates for native setups are measured on live flow (spike 'Recommendation').
2. **It cannot explain the 64 nulls.** Survivor bias generally flatters long base rates and reversal-type edges. The nulls happened despite that tailwind, so they are robust to it.
3. **The value is defensive.** It stops a false finalist from consuming the one-use, lane-wide confirmation window, and stops a future ML fit from learning the bias. That protection can be bought much more cheaply than a full v4. The pending insider campaigns are the near-term exposure (CLAUDE.md, insider lane).
4. **Parts of the recommendation are overbuilt.**
   - **Shumway-style delisting haircut.** It is less consequential here than in buy-and-hold literature. A 2-ATR stop bracket usually exits a performance-delisting loser before its last bar. Note one related gap: a trade that runs out of bars is labelled IMMATURE and silently dropped (research/pooled/cube.py:296-298). The labeller needs a declared terminal rule, but it is a small one.
   - **"Re-report PEAD's control on v4".** This does not work as written. The PEAD events come from the Nasdaq calendar, which has no delisted names (docs/apriori-pead-2026-09-26.md 'Caveats'). A v4 membership list adds no events. That needs EDGAR 8-K timestamps (lane #3 in docs/alpha-data-augmentation-2026-10-18.md §4).
   - **A "declared second look" at reaction-only PEAD before the 10-28 renewal.** This spends a hypothesis on a weaker variant: +0.120R against a +0.094R control is roughly +0.026R paired, versus +0.055R measured. The consensus contamination it would fix is mild. The renewal should turn on workflow and kill criteria, and live probe outcomes are already point-in-time by construction.
   - **EDGAR Form 25/15 plus CIK identity for 2022 onward.** This is the costly, messy part. Free SEC data does not give a clean historical CIK-to-ticker map. It only matters for the selection and confirmation windows, so it should be built when a candidate actually reaches selection.

Net: correct finding, misdated deferral, worth fixing. Fund it staged and gated, not as a standalone cohort v4 workstream ahead of entry-side work.

**Risks**

- **Scope creep.** EDGAR Form 25/15 to historical-ticker resolution is not cleanly free. The SEC ticker map is current-only, so CIK identity across renames can grow from M to L.
- **Unverified asof behaviour.** Nobody has checked that Alpaca's asof maps reused tickers back to the dead entity (open question in maps/data.md:216). Alpaca's symbol mapping relies on the same asset master that dropped post-2022 delistings, so asof may not help after 2022.
- **Researcher degrees of freedom.** The delisting-return haircut and the cash-merger rule need fixing before any v4 read, or they become free tuning knobs.
- **Forking paths.** Re-reporting 64 closed formula-cohort pairs on v4 invites reopening lanes on new numbers. The re-reports must stay descriptive and must not be able to create a finalist or reopen a closed lane.
- **Invalidated hashes.** A new cube and cohort change the hashes and force checks A and C to rerun for every protocol. That costs compute and calendar time on a worktree that already hosts long jobs.
- **Opportunity cost.** It delays entry-side alpha work that could actually produce cards.
- **Rushed PEAD decision.** A hurried second look before 2026-10-28 could replace a measured +0.055R paired edge with a weaker, untested variant.

**Prerequisites**

- **Correct the spike doc's discovery window.** Line 41 of docs/survivorship-spike-2026-10-20.md should read 2016-08-01 to 2021-12-31, and the Recommendation should be re-argued on that basis.
- **Verify asof.** One short probe: does SBNY with asof=2023-03-01, and FISV across its reuse, return the dead or prior entity?
- **Declare the terminal-bar rule.** The labeller drops a trade whose bars end before stop, target or timeout as IMMATURE (research/pooled/cube.py:296-298). Declare a last-close rule for mergers and a stated haircut for performance delistings before any v4 read.
- **Reuse the spike artifacts.** The spike's private files (~/agentic-trader-research/survivorship-spike-20261020/assets.json, bars_nonotc.json) give the 2016-21 membership. They should be digest-pinned like the other research caches.
- **Check build capacity.** The cube builder (research/pooled/runner.py:301 build_cube_inputs) must handle about 2,000 extra symbols' SIP daily and hourly bars.
- **Get operator agreement on a binding rule.** No one-use confirmation spend and no model fit on a cohort without point-in-time membership.

**Better alternative**

Stage it lazily, in three steps.

**Step 0 — now, about an hour, no code:**
- Fix line 41 of the spike doc.
- Write a binding rule into docs/alpha-pooled-mining.md: any candidate that passes selection must be re-scored on a point-in-time-augmented discovery cube before the lane-wide confirmation window is consumed, and no ML or model spec may train on the v2 or v3 cubes. This protects the scarce resource (the confirmation window) at near-zero cost, which is most of this finding's value.

**Step 1 — S effort, fold into the next cohort freeze or the first insider finalist, whichever comes first:**
- Augment discovery only (2016-21) with the Alpaca inactive non-OTC symbols already fetched in the spike: 2,147 symbols with bars.
- Pass them through the existing point_in_time_eligibility, which already handles death via no_raw_close.
- Add the declared terminal-bar rule.
- Report each lane's control mean on old versus augmented cubes as one descriptive table: a cheap estimate of how big the bias is.

**Step 2 — defer:**
- EDGAR Form 25/15, CIK identity and asof pinning for 2022 onward until a candidate actually needs the selection or confirmation windows repaired.
- PEAD's survivorship check until EDGAR 8-K timestamps (lane #3) exist.
- Make the PEAD renewal on workflow and kill criteria, not on a rushed second look.

---

<a id="c13"></a>

## C13 — There is no typed Forecast or deployment contract, so no mining or model output can reach a card without bespoke code

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | architecture | gap | high | L | partially_confirmed |

_lenses: architecture, ml_

### Claim (as consolidated)

ScreenerCandidate carries indicators and a heuristic setup_quality. strategy and direction are bare strings, and asset_class defaults to FUTURES. It has no expected return, horizon, probability, uncertainty, data contract or model identity (screeners/base.py:23-52).

The deployment routes are narrow:
- The registry accepts only single-symbol, raw-adjustment definitions that trade on a 30-bar time-series z (promotion.py:88, 474-480; strategy.py:144-161).
- Catalog probes are hard-coded to PEAD (probe.py CATALOG_ENTRY_IDS).
- Pooled output is a dimensionless cross-sectional decile score on adjusted SIP at D-1. A pass only makes an unwritten 'Part 2' spec eligible (alpha-pooled-mining.md:1014-1017).
- Dynamic names get NaN cross-sectional features (ranker.py:245-249).

So selection, sizing and the shadow allocator have no μ input, and every new family is hand-wired into run_scan (about 150 PEAD/drift lines in copilot.py).

### Evidence

- agentic_trader/screeners/base.py:23-52
- agentic_trader/research/alpha/promotion.py:88, 474-480; research/alpha/strategy.py:144-161; screeners/formulaic.py:102-108
- agentic_trader/research/apriori/probe.py (CATALOG_ENTRY_IDS = ('pead',)); research/apriori/pead_live.py:1, 159 (reuses build_events)
- agentic_trader/research/alpha/panel.py:19-37, 132; research/alpha/dsl.py:1-5
- docs/alpha-pooled-mining.md:1014-1017, 1362-1366
- agentic_trader/research/setups/ranker.py:245-249; agent/copilot.py:897-948, 1268-1280

### Recommendation

1. Define a frozen pydantic Forecast with:
   - source_id (a content hash), symbol, decided_at, horizon_sessions;
   - direction, score and cross-sectional rank;
   - expected_r ± se in label R units, plus a cost-model id;
   - p_target and execution_policy_id;
   - a DataContract and a provenance digest.

2. Add a SignalSource protocol, produce(panel, as_of) → Sequence[Forecast], over the whole scan cross-section, static and dynamic names alike.

3. Port the native strategies first, taking expected_r from the replay prior (≈0). Port PEAD next; it is the parity template.

4. Add a content-addressed RankerDefinition/ModelArtifact: spec SHA, refit index, window end and weights.
   - Enrol it in the probe list under probe_block_reason, the risk cap and the −4R kill.
   - Generalise CATALOG_ENTRY_IDS into a registry of artifact kinds.
   - Write the pooled 'Part 2' once, generically.

5. Deploy in two steps:
   - First, preview re-ranking and withholding of the scan's own candidates, where the control is the same scan.
   - Then a CrossSectionalSource that emits the top 1–2 names with the cube's bracket and a session_count_v1 exit.

6. Add a parity test that live D-1 features equal the warehouse or cube values.

### North-star impact

A research pass becomes a capped paper-probe card through enrolment instead of a new project, and selection and sizing get μ and σ(μ) to act on.

### References

- QuantConnect LEAN Algorithm Framework, Alpha key concepts (Insight) — Precedent for a typed signal object between the alpha and portfolio stages (https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/alpha/key-concepts)
- Grinold & Kahn, Active Portfolio Management (2000) — alpha = IC × volatility × score maps a rank into expected return
- López de Prado (2018), Advances in Financial Machine Learning, ch. 3 — A secondary model deciding whether and how much to act on candidates

### Fact check — partially_confirmed

**Corrected claim**

A typed forecast contract already exists. research/alpha/forecasts.py:26-196 defines four frozen types:
- ForecastContract: target and horizon, feed, adjustment, bar layout, currency.
- ForecastCalibration: OLS-HAC slope and intercept, shrinkage, a covariance, and a content-hash calibration_id.
- AlphaForecast: expected_return, standard_error, version_id, family_id, calibration_id.
- CombinedForecast.

The live daemon builds these through AlphaShadowService (copilot.py:1065, shadow.py:84-135). It does so only for registry DSL definitions whose qualification carries a calibration. The convex shadow allocator does consume μ and its standard error: build_shadow_portfolio uses expected_return at portfolio.py:125-126 and standard_error at 188-190. That allocator runs only from the CLI (cli/commands/alpha.py:711).

What is actually missing is narrower:
- ScreenerCandidate (screeners/base.py:23-52) carries no per-candidate μ, horizon, uncertainty or data contract. It does carry alpha_version, alpha_score and alpha_policy for registry alphas.
- Live card ranking uses only setup_quality (copilot.py:1176).
- Book sizing has no μ. Its shadow optimiser uses a dummy unit alpha (book_sizing.py:439). Alpha and probe cards are exempt from book sizing (book_sizing.py:325).
- The only measured-EV input is bucket-level: card_stats mean_r_cost per (strategy, direction), read by card_policy, which ships off (config.yaml:453-455).
- The calibrated forecast lane covers only single-symbol, raw-adjustment registry DSL alphas (promotion.py:88, 474-480). These trade a rolling time-series z whose window defaults to 30 bars and can be set from 2 to 252 (models.py:62, 77-78).
- Such alphas, DSL probes included, reach cards generically, without bespoke code (registry.py:121-126, copilot.py:758). So the title's "no mining or model output can reach a card without bespoke code" holds only for non-DSL families.
- Catalog probes are hard-coded to PEAD (probe.py:20). PEAD is hand-wired into run_scan (115 drift/PEAD lines in copilot.py:682-1679, plus helpers at 1933-2077).
- A pooled pass only makes an unwritten Part 2 spec eligible (alpha-pooled-mining.md:1014-1017, 1362-1366).
- Dynamic names get NaN cross-sectional ranker features (ranker.py:245-249; features.py:262).
- A sha256-verified ranker artifact already exists and is scored in shadow (ranker.py LoadedRanker).

The recommendation should therefore generalise the existing AlphaForecast/ForecastContract/ForecastCalibration and the LoadedRanker artifact, rather than define a new pydantic Forecast and RankerDefinition from scratch.

**Checked**

- agentic_trader/screeners/base.py:23-52 (ScreenerCandidate: strategy/direction str, asset_class default FUTURES, setup_quality, but also alpha_version/alpha_score/alpha_policy at 42-44)
- agentic_trader/research/alpha/promotion.py:88 (eligible_symbols must equal the single manifest symbol), 474-480 (raw adjustment and iex/sip feed required for qualification)
- agentic_trader/research/alpha/strategy.py:26 (NORMALIZATION_WINDOW = 30), 144-161; research/alpha/models.py:62,77-78 (normalization_window can be set from 2 to 252)
- agentic_trader/screeners/formulaic.py:102-108 (trades entry_directions on the z-score)
- agentic_trader/research/apriori/probe.py:20 (CATALOG_ENTRY_IDS = ('pead',)), 32
- agentic_trader/research/apriori/pead_live.py:1-5, 150-165 (reuses build_events)
- agentic_trader/research/alpha/panel.py:1-60, 120-140 (sector-panel operators; research/pooled/* does not import it; pooled formula.py:25 imports dsl)
- agentic_trader/research/alpha/dsl.py:1-5
- docs/alpha-pooled-mining.md:1014-1017 (Part 2 probe spec on a confirmed formula), 1362-1366, 163-170, 269-274, 298 (adjusted daily bars, read at D-1, decile basket)
- agentic_trader/research/setups/ranker.py:236-250 (population limited to universe_sectors), features.py:262 (NaN row for a symbol outside the cross-section), ranker.py:1-120 (sha256-verified LoadedRanker scored in shadow only)
- agentic_trader/agent/copilot.py:682-1679 run_scan (115 drift/PEAD lines inside it plus helpers 1933-2077), 897-948, 1268-1280, 1176 (ranking by setup_quality), 758 (registry install_alphas for active and probe), 1065 (alpha_shadow.observe)
- agentic_trader/research/alpha/forecasts.py:26-196 (ForecastContract, ForecastCalibration, AlphaForecast with expected_return/standard_error/calibration_id, CombinedForecast)
- agentic_trader/research/alpha/shadow.py:69-150 (the live AlphaShadowService builds calibrated AlphaForecasts and combines them)
- agentic_trader/research/alpha/portfolio.py:76-190 (build_shadow_portfolio consumes CombinedForecast expected_return and standard_error)
- agentic_trader/execution/book_sizing.py:325, 423-445 (exempts alpha/probe candidates; the shadow optimiser uses a dummy unit alpha)
- agentic_trader/screeners/registry.py:121-126 (generic FormulaicAlphaStrategy install for active and probe DSL definitions)
- agentic_trader/research/setups/card_stats.py:43-63 (per-(strategy,direction) mean_r_cost); execution/card_policy.py:37-63; config/config.yaml:453-455 (card_policy off)
- QuantConnect LEAN Alpha key-concepts page (fetched; describes Insight with direction/magnitude/confidence/weight/period passed to portfolio construction)

**Bad references**

- agentic_trader/research/alpha/panel.py:19-37, 132 — these are sector-panel helpers (cross_sectional_rank, cross_sectional_zscore, group_neutralize, beta_adjust_scores). The pooled lane does not import them (research/pooled/formula.py:25 imports dsl), so they do not support the description of pooled output.
- agentic_trader/screeners/base.py:23-52 does not support 'no model identity': lines 42-44 carry alpha_version, alpha_score and alpha_policy.

**Notes**

Confirmed:
- PEAD is the only catalog entry.
- Pooled Part 2 is unwritten.
- Live selection ranks by setup_quality.
- Book sizing takes no μ.
- Dynamic names get NaN cross-sectional features.
- PEAD/drift is hand-wired into run_scan.

Refuted or overstated:
- "No typed Forecast contract": AlphaForecast and its siblings exist in research/alpha/forecasts.py and run live in shadow.
- "Shadow allocator has no μ input": build_shadow_portfolio consumes μ and its standard error. Only book sizing's optimiser cross-check uses a dummy alpha.
- "No model identity": alpha_version is present on the candidate.
- "30-bar z": 30 is the default; the window can be set from 2 to 252.
- Title: registry DSL alphas and DSL probes reach cards generically.

Recommendation item 4's content-addressed model artifact already exists in part, as the sha256-verified LoadedRanker scored in shadow.

The QuantConnect, Grinold & Kahn and López de Prado (AFML ch. 3, meta-labeling) references are valid and fit their stated use.

### Value review — P2, next (weeks)

**Reasoning**

The diagnosis is accurate, but most of the recommended build is plumbing with nothing yet to carry. Only a thin slice is worth funding now. The rest should wait for a pass.

What I verified in the code:
- `ScreenerCandidate` is shaped around indicators. `asset_class` defaults to FUTURES. It has no horizon or probability fields (`agentic_trader/screeners/base.py:23-48`).
- The registry deployment route only covers a single symbol trading on a 30-bar time-series z-score:
  - finalists must have `eligible_symbols == (manifest["symbol"],)` (`research/alpha/promotion.py:88`);
  - raw adjustment is required (`promotion.py:474-480`);
  - entries are a threshold on a rolling z (`research/alpha/strategy.py:144-161`, `screeners/formulaic.py:102-108`).
- Catalog probes are hard-coded as `CATALOG_ENTRY_IDS = ("pead",)` (`research/apriori/probe.py:20`).
- PEAD is wired directly into `run_scan` (`agent/copilot.py:897-948`, 1133-1170).
- A pooled pass only makes a separate Part 2 spec eligible (`docs/alpha-pooled-mining.md`, "Decision rule (pre-registered 2026-10-07)" and "What it never does").

So this is the architecture map's G6, and roadmap L3 is still marked "Planned" (`docs/alpha-roadmap.md`, L1–L5 table).

One part of the claim is wrong. A typed forecast already exists: `ForecastContract`, `ForecastCalibration`, `AlphaForecast` and `CombinedForecast` in `research/alpha/forecasts.py:27-198`. `AlphaShadowService` produces it, and the shadow optimiser consumes μ and its standard error. The real gap is that this type never reaches the live candidate. A new pydantic `Forecast` alongside it would give the codebase two forecast types.

Why this is not P0/P1 as written:
- The north star is limited by edge, not by deployment plumbing:
  - 64 formula–cohort pairs have produced zero finalists;
  - there are zero active alphas;
  - native runners-up are at −0.17R to −0.40R, and `setup_quality` is no better than random (`docs/alpha-roadmap.md`, "Desk direction… (October 8)", October 10 status);
  - every bracket arm trails SPY by 0.10–0.15R (`docs/bracket-counterfactual-2026-10-18.md`).
- A generic stack of `SignalSource`, `ModelArtifact`, a generic Part 2 and a `CrossSectionalSource` is effort L built around no real input.
- The recommendation itself fills `expected_r` from the replay prior, which is about 0 (honestly, slightly negative). Selection and sizing would get a μ that carries no information.
- The friction it removes is small. Hand-wiring PEAD took about 150 lines. The pre-registered rule needs an operator-approved Part 2 spec anyway, so enrolment cannot save the spec step, only the coding step. That saves days, and only on an event (a pass) that has not happened in 64 tries.
- Writing Part 2 generically now risks fitting the abstraction to PEAD plus an imagined decile ranker. The next real pass, perhaps insider- or event-driven, may not fit that shape.

What is worth funding (P2, next weeks):
- Fold a minimal forecast field (`model_id`, `horizon`, `p_target`/`expected_r`, `se`) into the L3 `CardSelector` seam, reusing the existing `AlphaForecast`/`ForecastContract`.
- This is what lets a meta-label selector be A/B tested by swapping selectors (signals map rec 2; architecture map rec 3).
- Step 5a (preview re-ranking and withholding of the scan's own candidates, with the same scan as the control) is the high-value part of this finding. It largely is L3 plus the existing shadow-ranker hook (`scan.shadow_ranker_artifact`, which is unset).

What to defer until a lane produces a finalist or a forward-confirmed near-miss such as `shorts_retreat`:
- `SignalSource` over the cross-section, `ModelArtifact`/`RankerDefinition` enrolment, the generalised `CATALOG_ENTRY_IDS`, `CrossSectionalSource` and the D-1 parity test.
- The parity test is cheap, but it only matters once a cross-sectional source goes live.

**Risks**

1. **Wrong abstraction, built speculatively.** With zero finalists, the interface gets shaped by PEAD and an imagined decile ranker. The first real pass may need a different seam: event timing, multi-day holds, or features at the filing date. Then it gets rewritten.
2. **False precision on cards and in sizing.**
   - An `expected_r` taken from a replay prior near 0 (setup-outcome holdout −0.118R, `docs/setup-outcomes-2026-09-23.md`) or from 90-day `(strategy, direction)` EV adds noise to selection and sizing, not information.
   - A "+0.05R ± 0.3" line on a card would undercut the "honest base rate" definition of a reasonable suggestion.
3. **Regression risk on the live path.**
   - Porting native strategies and PEAD means refactoring `run_scan` (`copilot.py:682-1677`, inside a 4,527-line class). That touches budget spending, the drift lateness clock and `card_policy`.
   - The PEAD probe is the only live prospective alpha evidence. A port in the middle of its evaluation could break its parity.
4. **Erosion of the pre-registration gate.**
   - Turning "pass → spec → probe" into "pass → enrolment" lowers the friction the protocol deliberately imposes.
   - The −4R kill and `probe_block_reason` cap the damage. But the confirmation window being unused across lanes reflects discipline that should not be automated away.
5. **A second forecast type.** This duplicates `research/alpha/forecasts.py:AlphaForecast`/`ForecastContract`, which already carry μ, SE, a data contract and calibration.
6. **Data-contract mismatch for any cross-sectional source.**
   - Pooled scores use adjusted SIP at D-1. Promotion requires raw (`promotion.py:474-480`). Recent-SIP entitlement is an open issue.
   - Dynamic names get NaN cross-sectional features (`ranker.py:245-249`).
   - A live parity failure would be silent unless the parity test exists first.

**Prerequisites**

1. **L3 `CardSelector` seam** with a typed `Selection` result, so a selector, rather than `run_scan`, owns ranking, budgets and group caps (`docs/alpha-roadmap.md` L3). Ideally also L4, so sizing runs before the LLM step (`evaluator.py:362-917`; `copilot.py:1364` then 1396-1425).
2. **Move the existing forecast types and bracket policy into a shared kernel package** (`research/alpha/forecasts.py`, `research/alpha/strategy.py`), enforced by import-linter (architecture map rec 7). Live code should not import more of `research/`.
3. **A defensible source for `p_target`/`expected_r` with an honest standard error.** Either `card_stats` EV with `n_mature`, or a meta-label classifier trained and calibrated on the roughly 8,768 replayed native setups from the bracket counterfactual, with a predeclared holdout.
4. **Before any `CrossSectionalSource` or generic Part 2:**
   - at least one lane finalist, or a forward-tracked near-miss that confirms prospectively;
   - verified live D-1 feature access for the full cohort on the deployment feed;
   - a parity test of live features against cube values.
5. **Operator approval of a Part 2 spec**, which the pre-registered decision rule requires in any case.

**Better alternative**

There is a cheaper route in two pieces.

**1. Do the forecast field inside L3, not as a framework.**
- Add one optional `forecast` field to the candidate, reusing `research/alpha/forecasts.py:AlphaForecast` after moving it to a kernel: `model_id`, `horizon`, `p_target`, `expected_r`, `se`.
- Add the L3 `CardSelector` seam.
- Fill the field first from a meta-label P(target before stop) model trained on the replayed native setups (López de Prado, AFML ch. 3; Joubert 2022).
- Run it in preview against the same scan's `setup_quality` ordering. The scan's own candidates are the control, which is step 5a of the finding.
- This touches only selection, which is the one lever on the north star that needs no new alpha. It also gives the book sizer a real μ/SE input later, without a deployment framework.

**2. Give lane outputs a prospective home without deployment.**
- Add a nightly forward-tracking worker that scores the 64 frozen fixed-set formula–cohort pairs and journals decile outcomes (architecture map rec 8, extending the daily-panel worker pattern).
- It accumulates out-of-sample evidence at zero trial cost. It also shows the actual shape of a cross-sectional signal before anyone designs `CrossSectionalSource`.

Build the generic `SignalSource`/`ModelArtifact` enrolment and the generic Part 2 only when a lane produces its first finalist or forward-confirmed near-miss. Design them around that real case, with PEAD as the parity template.

---

<a id="c15"></a>

## C15 — Each new data source costs about ten touch points, cannot be combined with other sources and has no live path: replace per-lane plumbing with one FieldSource contract

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | alpha_mining | improvement | high | M | partially_confirmed |

_lenses: data, architecture_

### Claim (as consolidated)

Adding one source currently touches all of the following:
- a CampaignProtocol subclass with duplicated validators (campaign.py:403-584);
- an Inputs model;
- a Sources/load/verify trio (runner.py:90-195);
- branches in score_book's isinstance ladder (scoring.py:324-377);
- a branch in frozen_document (campaign_run.py:239-256);
- a row and column in an N×N CLI exclusion matrix (alpha.py:1656-1715);
- fields in the global FIELD_UNITS, which has no visibility metadata (dsl.py:28-47).

build_cube_inputs refuses more than one source (runner.py:317-318), so interaction formulas cannot be expressed.

The FINRA, EDGAR and Nasdaq parsers exist only behind research CLIs, so a finalist would have no live D-1 value.

The FINRA lane used only CNMS files, which start on 2018-08-01. That gave about 836 discovery sessions instead of about 1,360.

### Evidence

- agentic_trader/research/pooled/campaign.py:403-584 (one subclass per source)
- agentic_trader/research/pooled/runner.py:90-195, 317-318
- agentic_trader/research/pooled/scoring.py:324-377; research/pooled/campaign_run.py:239-256
- agentic_trader/cli/commands/alpha.py:1656-1715; research/alpha/dsl.py:28-47
- agentic_trader/research/short_volume.py:42; docs/alpha-pooled-short-v1-2026-10-20.md:31-34
- No agent/ or data/ consumer of research/short_volume.py or research/insider_filings.py (grep)

### Recommendation

Create one FieldSource protocol and registry. Each source declares:
- an id and version;
- field specs: name, units, and a visibility rule keyed on visibility time (for example filing date or D+1);
- a pinned-inputs model;
- load, verify_digests, coverage and frames.

Wiring:
- CampaignProtocol carries field_sources as a tuple.
- ScoreBook joins their union.
- The DSL field table derives from the declared sources, so an undeclared field fails to compile.

Each source provides:
(a) a historical point-in-time adapter that writes into the warehouse;
(b) a daily incremental adapter run by the pit-snapshots worker;
(c) a parity test that the live D-1 value equals the warehouse value;
(d) coverage and absent-session binding in its digest.

Keep frozen identities byte-hashed. Parse old shapes in one loader, with no runtime aliases. Cache check A by cube SHA, selection rule, gates and stage-code digest.

### North-star impact

A new data idea costs one module and one JSON file. Multi-source hypotheses become expressible, and a passing field already has a tested daily path to a card.

### References

- Microsoft Qlib, Point-in-Time database — Template for the visibility metadata each field should declare (https://qlib.readthedocs.io/en/latest/advanced/PIT.html)
- FINRA daily short sale volume files — Documents the CNMS start date that limited the lane (https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files)

### Fact check — partially_confirmed

**Corrected claim**

Each new pooled field source does require roughly ten touch points:
- a CampaignProtocol subclass with a copy-pasted fixed_set validator (campaign.py:403-584) and a _PROTOCOL_CLASSES entry (campaign.py:594-600);
- an Inputs model;
- a Sources dataclass with load and verify functions (runner.py:90-195) and a dedicated CubeBuild slot (runner.py:197-211);
- an isinstance branch in score_book (scoring.py:324-377), plus its own inputs-document and coverage-document helpers (scoring.py:289-321);
- a branch in frozen_document (campaign_run.py:239-256);
- a row and column in the CLI exclusion matrix (alpha.py:1656-1715);
- entries in the global FIELD_UNITS table, which carries units but no visibility metadata (dsl.py:28-47).

build_cube_inputs refuses more than one extra source (runner.py:317-318). Each alt-data source can still be combined with daily bars through the generic ScoreBook.with_fields join (scoring.py:184-216). What cannot be expressed is a formula that crosses two alt-data sources, or hourly bars combined with events.

FINRA and EDGAR parsing exists only behind the research CLIs, so a short-volume or insider finalist would have no live D-1 value.

Nasdaq is different, and the finding is wrong about it:
- The Nasdaq calendar parser lives in agent/earnings.py (CalendarRow and parse_calendar_payload at 61 and 123, NasdaqEarningsCalendar at 171). The live earnings blackout uses it, and the live PEAD drift path uses it too (copilot.py:146-150, 385-411 → screeners/earnings_drift.py → research/apriori/pead_live.py, which reuses the study's build_events).
- The daemon already captures corporate actions every day (data/corporate_actions.py).
- So the events lane's raw inputs are live. Only the events_fields_v1 frame computation (research/pooled/events.py) has no live caller.
- pead_live is an existing in-repo precedent for research/live parity that a FieldSource contract could generalise.

The FINRA lane used only CNMS files. Those start on 2018-08-01, which left about 836 scored discovery sessions against about 1,360 (docs/alpha-pooled-short-v1-2026-10-20.md:31-34).

Check A is formula-independent but tied to one protocol's SHA (study.py:45-53), so it is recomputed for each protocol.

**Checked**

- agentic_trader/research/pooled/campaign.py:403-584: IntradayCampaignProtocol, EventsCampaignProtocol, ShortVolumeCampaignProtocol and InsiderCampaignProtocol, each with a near-identical 'fixed_set only in v1' model_validator; Event/Short/InsiderInputs models; the _PROTOCOL_CLASSES registry at 594-600
- agentic_trader/research/pooled/runner.py:90-195: EventSources/ShortSources/InsiderSources with their load_* and verify_* functions; CubeBuild has a separate events/short/insider slot for each source (197-211)
- agentic_trader/research/pooled/runner.py:317-318: raises 'a build carries one field source, not several'
- agentic_trader/research/pooled/scoring.py:324-377: score_book isinstance ladder, plus per-source *_inputs_document and *_coverage_document helpers at 289-321; ScoreBook.with_fields at 184-216 is already a generic column join of one frames mapping
- agentic_trader/research/pooled/campaign_run.py:239-256: frozen_document has one branch per source
- agentic_trader/cli/commands/alpha.py:1656-1715: _pooled_field_sources pairwise exclusion matrix, used by three commands (1805, 2005, 2114)
- agentic_trader/research/alpha/dsl.py:28-47: global FIELD_UNITS holds only (price, volume) unit exponents, with no visibility metadata
- grep for non-research importers: only cli/commands/alpha_short_volume.py and alpha_insider_filings.py import research/short_volume.py and research/insider_filings.py; research/pooled/* has no agent/ or data/ consumer
- agentic_trader/agent/earnings.py:13,61-80,123-151,171: the Nasdaq parser (CalendarRow, parse_calendar_payload, NasdaqEarningsCalendar) is live code; research/pooled/events.py:29 imports it
- agentic_trader/agent/copilot.py:146-150,385-411 and screeners/earnings_drift.py:25 -> research/apriori/pead_live.py: the live PEAD drift path reuses the study's own build_events
- agentic_trader/research/pooled/study.py:45-53 check_power_gate binds a check A result to campaign_protocol_sha256, so the formula-independent check A is rerun for each protocol
- agentic_trader/research/short_volume.py:42: CNMS-only URL
- docs/alpha-pooled-short-v1-2026-10-20.md:31-34 (Coverage): about 836 scored discovery sessions vs about 1,360
- docs/superpowers/specs/2026-10-20-pooled-short-volume-design.md:14-18,62: CNMS available from 2018-08-01
- grep for a pit-snapshots worker or warehouse in agentic_trader/: none exists
- https://qlib.readthedocs.io/en/latest/advanced/PIT.html
- https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files

**Bad references**

- Claim text 'The FINRA, EDGAR and Nasdaq parsers exist only behind research CLIs': wrong for Nasdaq. The parser is live code in agentic_trader/agent/earnings.py:61-151, used by the earnings blackout and by EarningsDriftService/pead_live. The finding's own grep evidence covers only short_volume.py and insider_filings.py.

**Notes**

The structural core holds and every code citation checks out:
- one subclass, Inputs model and Sources/load/verify trio per source;
- the isinstance ladders in scoring.py and campaign_run.py;
- the N×N CLI exclusion;
- FIELD_UNITS carries units only;
- build_cube_inputs refuses more than one source;
- the CNMS start date limits discovery history.

Nuances:
(1) The range campaign.py:403-584 includes IntradayCampaignProtocol. It is a score-input mode (hourly bars), not an alt-data source.
(2) ScoreBook.with_fields is already a generic join of one frames mapping. The barrier to multi-source formulas is build_cube_inputs and the one-subclass-per-source protocol design, not the scoring join. Alt-data fields already combine with daily bars.
(3) The recommendation names a 'pit-snapshots worker' and a 'warehouse'. Neither exists in agentic_trader/ today, so they are proposals (possibly from another finding), not current components.
(4) The Check A caching suggestion is well-founded. check_power_gate pins check A to campaign_protocol_sha256, and the short-volume doc reports check A curves 'identical to the daily, intraday and events curves on the same cubes'. Cohort v3's check A still took about 3.5 hours (09:12 to 12:52).

Both external references support their use:
- The Qlib PIT doc describes per-record publication date, period and revision chain.
- The FINRA page states 'The earliest date for the Consolidated NMS files is August 1, 2018' and lists individual TRF/ADF/ORF files, with a year filter back to 2009.

The finding does not say that pre-2018 per-facility files would extend the history. That is an implied improvement the FINRA page only hints at; it does not confirm per-facility start dates.

### Value review — P2, next (weeks)

**Reasoning**

The code facts are accurate. Each source has its own CampaignProtocol subclass with a copied "fixed_set in v1" validator (agentic_trader/research/pooled/campaign.py:403-584). Each also has a Sources/load/verify trio (runner.py:90-195). score_book (scoring.py:324-377), frozen_document (campaign_run.py:239-256) and the CLI exclusion matrix (cli/commands/alpha.py:1656-1715) each branch on the source with an isinstance ladder or per-source if-chain. FIELD_UNITS is one global table with no visibility metadata (research/alpha/dsl.py:28-47). build_cube_inputs refuses more than one source (runner.py:317-318). Only CLI and research modules import research/short_volume.py and research/insider_filings.py, so nothing in agent/ or data/ consumes them. The CNMS-only start is recorded in docs/alpha-pooled-short-v1-2026-10-20.md "Coverage" and in short-v1.json's rationale.

As written, though, this is the wrong bet for the north star.

1. Plumbing is not the bottleneck. The short-volume lane was built and merged on 2026-10-20 (#126), and the insider lane went from start to merge in about a day (#128). The binding constraints are elsewhere. Long brackets earn about +0.15R whatever the formula, and so does the control (maps/history.md "Recurring failure patterns" 1). Discovery winners fail out of sample. Power is 0.08-0.10R. There is a single lane-wide confirmation window. Making a 0-for-64 strategy cheaper to run raises the trial count against that same window without raising the hit rate.

2. The live half is speculative. The warehouse, the pit-snapshots worker, per-source daily adapters and parity tests have no consumer: no field has survived selection. A grep for warehouse or pit_snapshot in agentic_trader/ returns nothing, so this is new infrastructure and the effort is L, not M. Daily Nasdaq scraping and EDGAR acquisition in the daemon would add fragile dependencies to production for no card.

3. One piece is real. The single-source restriction blocks the most plausible remaining historical trial: a predeclared composite of weak signals with literature signs, in the spirit of Gu-Kelly-Xiu and Jensen-Kelly-Pedersen (maps/history.md recommendation 3). The narrow fix is small. ScoreBook.with_fields already takes a frames mapping and a list of field names (scoring.py:184-219), so joining a union per symbol is a contained change. That slice should ride inside the composite workstream (P1 if that is funded), not ship as a standalone refactor.

Verdict: P2 for the narrow slice, built as part of whatever first consumes it. Drop the warehouse, live-adapter and check-A-cache scope until a field passes discovery and selection, or until a prospective pooled scorer is funded and needs D-1 values.

**Risks**

1. Frozen-identity regression. frozen_document hashes source-specific keys: event_inputs, short_inputs, insider_inputs (campaign_run.py:239-256). A generic field_sources shape must reproduce every committed protocol and frozen-candidate hash byte for byte, or past evidence silently loses its identity. Parsing two shapes also strains the CLAUDE.md rule against compatibility aliases.

2. Joint-eligibility bias. The sources disagree on what "missing" means. Short fields are NaN and never carried forward. Insider fields read an unloaded month as a real zero (campaign.py:569-571 comment). Events are NaN before an identity break. A naive union join shrinks the eligible set to the intersection of coverage, which skews toward larger, better-covered names. That changes the every-eligible control and can manufacture a paired edge.

3. More lookahead surface. Each source's visibility rule (filing date, D+1, 18:00 ET publication) now composes with the others. One wrong declaration contaminates every multi-source formula.

4. Throughput without discipline. Cheaper lanes invite more trials against the one confirmation window (2024-01-02 to 2026-07-31). Unless an ex-ante yield gate exists, the refactor raises false-discovery exposure.

5. Scope creep into production. Daily Nasdaq, EDGAR and FINRA adapters in the daemon add failure modes (scraping breakage, the EDGAR user agent, provider pacing) and readiness components with zero card consumers.

**Prerequisites**

1. A funded consumer: either the predeclared multi-source composite or a prospective pooled scorer that needs D-1 values for these fields. Without one, do nothing.

2. Golden-hash tests over every committed config/research/pooled/*.json protocol and every saved frozen document, written before any refactor.

3. A joint-eligibility rule declared in the consuming spec: per-field NaN propagation versus coverage intersection, and how the every-eligible control is defined under it.

4. A visibility declaration per field, reviewed against each source's publication time. The rationale texts in short-v1.json and insider-v1.json already state these rules.

5. For any live adapter: a field that has passed discovery and selection. None has yet.

**Better alternative**

Do a narrow slice of about S effort inside the composite (or prospective-scorer) PR, not a platform refactor:

1. Remove runner.py:317-318 and let CubeBuild carry a tuple of loaded sources.
2. Merge each symbol's frames into ScoreBook.with_fields, which already takes frames plus field names (scoring.py:184-219).
3. Derive each protocol's allowed field set from its declared sources, so an undeclared field fails at vet_expression rather than at runtime. Add a visibility column next to the units in FIELD_UNITS.
4. Replace the isinstance ladders only where the composite touches them.

For the live path, do not build a separate warehouse with parity tests. Run the existing digest-pinned acquisitions incrementally each day (append that day's FINRA file, EDGAR filings or actions to the same store) and have the live reader call the same loader and frame builder. Parity then holds by construction. Build that only for the source of a field that survives selection, or for the sources the prospective scorer needs.

The FINRA 2018 start is a separate, cheap fix. To my knowledge FINRA's per-facility daily files (FNSQ, FNQC, FNYX, FNRA, FORF) predate the consolidated CNMS file, so summing them could cover 2016-08 to 2018-07; check FINRA's catalog first. Any re-run would be a new frozen second-look spec, for example shorts_retreat on the unscored 2016-2018 sessions. It is low power, so P3 at most.

---

<a id="c16"></a>

## C16 — There is no reference data (industry, shares outstanding, filing-dated events); two free nightly SEC bulk files plus Ken French's definitions supply it

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | integration | high | M | partially_confirmed |

_lenses: data, literature_

### Claim (as consolidated)

Sector data today:
- Sectors exist only as hand labels on the 159 static names (config.py:101-104 defaults to 'unknown').
- The 452- and 1,000-name cohorts have no sectors.
- No module reads SIC, XBRL companyfacts or shares outstanding.
- The insider lane maps CIKs only for today's tickers (insider_filings.py:58-62, 574).

Consequences:
- Pooled controls cannot be risk-matched (cube.py:53).
- group_neutralize (panel.py:29) goes unused.
- Dynamic names fall into one pseudo-group, so correlation_group never binds.
- sector_rel_mom_60, one of only two Holm-surviving features (ρ +0.028), is NaN for dynamic names.

The data is free. SEC republishes submissions.zip and companyfacts.zip nightly, around 03:00 ET, with sic, tickers, formerNames and per-filing acceptanceDateTime and items. Ken French publishes Siccodes49 and daily FF5, momentum and 49-industry returns.

Moskowitz-Grinblatt find that industry momentum explains much of stock momentum.

### Evidence

- agentic_trader/config.py:101-104; agentic_trader/research/pooled/cube.py:53
- agentic_trader/research/insider_filings.py:58-62, 574
- agentic_trader/risk/rules.py:332-348; agentic_trader/agent/copilot.py:614-617
- agentic_trader/research/setups/ranker.py:245-249; research/alpha/panel.py:29; docs/setup-outcomes-2026-09-23.md:78
- docs/alpha-data-augmentation-2026-10-18.md §1 'Reference', §3
- data.sec.gov submissions JSON for CIK 320193 (fields verified); Ken French Siccodes49 and daily factor files (verified)

### Recommendation

Build one EDGAR reference acquisition that serves both research and live. Download submissions.zip and companyfacts.zip nightly, with a SHA-256 per file per date, and parse them into CIK-keyed point-in-time tables:
(a) CIK↔ticker by date, via the asset snapshots and formerNames;
(b) SIC → FF12/FF49, declaring current SIC as the historical proxy;
(c) dei:EntityCommonStockSharesOutstanding, visible from the session after it was filed, giving size and turnover;
(d) filing events with acceptanceDateTime: 8-K 2.02, 1.01 and 5.02; SC 13D; Form 25 and 15; 10-Q and 10-K.

Uses:
- industry groups for dynamic names in correlation_group and card de-duplication;
- group_demean and matched controls in pooled scoring;
- a 6-month industry-momentum selector feature;
- industry-residual reversal (C47);
- French daily factors for residual labels.

Follow Qlib's date/period/value point-in-time layout.

### North-star impact

One acquisition feeds four consumers:
- risk-matched mining controls;
- real groups for dynamic names, so two cards are not the same bet;
- size and turnover fields;
- event timestamps for the catalog lane (PEAD v3, 13D, Form 25).

### References

- SEC EDGAR application programming interfaces — Free nightly submissions.zip and companyfacts.zip; sic and sicDescription per CIK (https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- Kenneth R. French Data Library, 49 Industry Portfolios (Siccodes49; daily returns) — Free SIC-to-industry map and daily factor and industry returns (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_49_ind_port.html)
- Moskowitz & Grinblatt (1999), Do Industries Explain Momentum?, JF 54(4) — Industry momentum as a selector feature
- Daniel, Grinblatt, Titman & Wermers (1997), JF 52(3) — Characteristic-matched controls that need sector and size

### Fact check — partially_confirmed

**Corrected claim**

The code has no machine-readable reference data. No module reads SIC codes, XBRL companyfacts or shares outstanding.

Sectors are hand labels in three places:
- the 159 static universe names (config.py:101-104, default 'unknown'; all 159 in config.yaml are labelled);
- the 127-name spread cohort (config/research/spread/cohort-v1.json);
- a small SECTOR_MAP used only by backtest attribution (constants.py:227).

The pooled cohorts v2 (452 names) and v3 (1,000 names) are plain symbol lists. The insider lane maps CIKs through today's company_tickers.json only (insider_filings.py:61, 574-596).

What follows from that:

1. **Pooled control.** The control is the equal-weight mean of every labelled eligible cell in the session (stats.py:55-64). The cube carries no sector array (cube.py:53), so sector-matched controls are impossible. Dollar volume is in the cube, so a liquidity or size-proxy match is already possible.

2. **group_neutralize.** It is not unused. It computes sector_rel_mom_60 at research/setups/features.py:189. It is absent only from the pooled lane and its DSL.

3. **Dynamic names in risk and caps.**
   - Dynamic names belong to no configured group, so the risk rule correlation_group (rules.py:332-348) never binds for their positions.
   - The 'dynamic' pseudo-group (copilot.py:614-617) feeds only the per-session card cap. That cap is 1 (config.yaml:445), so it allows at most one dynamic card per session, related or not.
   - A dynamic card and a static card in the same real industry are not de-duplicated, and dynamic positions can accumulate in one industry across sessions.
   - Book-aware sizing (risk/book_vol.py, enforce) partly offsets this: it shrinks size using the return covariance with the held book, but it never refuses.

4. **Shadow ranker.** Dynamic names are outside the shadow ranker's cross-section (ranker.py:245-249). Every cross-sectional feature is NaN for them (features.py:262), including sector_rel_mom_60. That feature is one of two Holm survivors in development (rho +0.028, docs/setup-outcomes-2026-09-23.md:78), but this affects shadow scoring only.

5. **External data.** SEC publishes submissions.zip nightly at about 03:00 ET and recompiles companyfacts.zip nightly, with no time stated. The submissions JSON fields named in the finding exist. Ken French publishes Siccodes49, daily 49-industry returns, daily FF5 (2x3) and daily momentum.

6. **Roadmap.** docs/alpha-data-augmentation-2026-10-18.md already lists:
   - companyfacts (#4, as a conditioning test);
   - EDGAR 8-K timestamps (#3);
   - Form 25/15 dates for point-in-time membership (section 4, step 2);
   - group_demean(sector) (section 3).

   The new parts of the recommendation are a single CIK-keyed point-in-time reference acquisition shared by research and live, and SIC-derived industries for dynamic names.

**Checked**

- agentic_trader/config.py:101-104 (UniverseEntry.sector defaults to 'unknown'); config.py:167-168 and 1104-1107 (sector labels become correlation groups)
- config/config.yaml universe.groups: 159 members across etf32/mega_caps/research_cohort, every one hand-labelled with a sector
- config/research/pooled/cohort-v2.json (452 symbols) and cohort-v3.json (1,000 symbols): plain symbol lists with no sectors
- grep of agentic_trader/ and scripts/ for sic|sicDescription|companyfacts|EntityCommonStockSharesOutstanding|shares_outstanding|formerNames|Siccodes|fama-french|data.sec.gov: no reader exists
- agentic_trader/research/insider_filings.py:61 (TICKERS_URL = company_tickers.json), :374-376 (fetched once), :574-596 (symbol_ciks maps the current ticker map only); research/pooled/insider_fields.py:141
- agentic_trader/research/pooled/cube.py:53 (_ARRAYS: no sector or size array, only dollar_volume); research/pooled/stats.py:1-10 and 55-64 (control = equal-weight mean of all labelled eligible cells in the session); grep of research/pooled for sector/industry/group: nothing
- agentic_trader/research/alpha/panel.py:29 group_neutralize; it is used at agentic_trader/research/setups/features.py:177-190 (_sector_rel_mom)
- agentic_trader/risk/rules.py:332-348 (correlation_group covers configured groups only); risk/limits.py:93-96,114; agent/copilot.py:168, 604-617, 1258-1300, 1318 (pseudo-group 'dynamic' applies only to the per-session card cap); config/config.yaml:443-445 (max_cards_per_group_per_session: 1)
- agentic_trader/risk/book_vol.py:1-30 (book-aware sizing uses the return covariance with the held book)
- agentic_trader/research/setups/ranker.py:235-253 (population limited to universe_sectors keys); research/setups/features.py:251-262 (an absent symbol gets NaN for every cross-sectional feature); agent/copilot.py:1815-1831 (shadow ranker only)
- docs/setup-outcomes-2026-09-23.md:66-83 (H2h sector_rel_mom_60 rho +0.028, Holm p 0.02; vol_20 is the only other Holm survivor)
- docs/alpha-data-augmentation-2026-10-18.md section 1 Reference row, section 2 #3/#4, section 3, section 4 step 2
- agentic_trader/constants.py:227-257 SECTOR_MAP; config/research/spread/cohort-v1.json (hand 'sectors' map); research/setups/features.py:69 SECTOR_ETF
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces (fetched)
- https://data.sec.gov/submissions/CIK0000320193.json (fetched: sic, sicDescription, tickers, formerNames, filings.recent.acceptanceDateTime, items all present)
- https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/det_49_ind_port.html and data_library.html (fetched: Siccodes49, 49 industries daily, FF5 2x3 daily, momentum daily)

**Bad references**

- Daniel, Grinblatt, Titman & Wermers (1997), JF 52(3): the citation is real (Measuring Mutual Fund Performance with Characteristic-Based Benchmarks). However, the DGTW benchmarks match on size, book-to-market and momentum, not sector. 'Controls that need sector and size' misstates it: they need size, B/M (book equity from companyfacts) and momentum.
- SEC EDGAR API page: 'about 03:00 ET' is stated for submissions.zip only. companyfacts.zip is described as recompiled nightly with no time given. The page itself does not list sic, sicDescription, acceptanceDateTime or items; those fields are verified only in the data.sec.gov JSON.

**Notes**

The core of the finding holds:
- there is no SIC, companyfacts or shares-outstanding reader;
- the pooled cohorts have no sectors and no sector-matched control;
- the insider CIK map uses the current ticker map only;
- correlation_group never binds for dynamic names;
- the shadow-ranker features are NaN for dynamic names.

Four details are wrong or overstated:
1. **group_neutralize is used.** It is called at research/setups/features.py:189. It is unused only in the pooled lane.
2. **Hand sectors also exist elsewhere,** in the spread cohort and in constants.SECTOR_MAP.
3. **The pseudo-group is a card cap only** (one dynamic card per session), separate from the risk rule.
4. **Book-aware sizing in enforce already accounts for return correlation** with held names, dynamic ones included. Sector data would add refusal and de-duplication, not the first correlation control.

The cited evidence lines exist:
- config.py:101-104;
- insider_filings.py:61 and 574;
- rules.py:332-348;
- copilot.py:614-617;
- ranker.py:245-249;
- panel.py:29;
- setup-outcomes:78.

cube.py:53 is the _ARRAYS tuple. It supports 'no sector array' only indirectly; the control definition is in stats.py.

Moskowitz-Grinblatt (JF 54(4), 1999) is cited and summarized accurately. The Ken French and data.sec.gov claims were verified live. Much of the recommendation (companyfacts, 8-K timestamps, Form 25/15, group_demean) is already on the 2026-10-18 data-augmentation roadmap. The finding is right that no authoritative sector or reference acquisition is planned, and that live sector use for dynamic names is missing.

### Value review — P2, next (weeks)

**Reasoning**

I'd fund a thin slice now and defer the rest. The bundle as written has four point-in-time (PIT) tables, nightly multi-GB bulk downloads and five consumers. None of those consumers is what limits card quality today. The limit is that there is no edge: in the setup-outcomes holdout, every scorer lost money after costs, random included (docs/setup-outcomes-2026-09-23.md "Holdout"). Reference data makes measurement sharper and risk handling cleaner, but it does not create an edge.

What I checked in the code:
- **Dynamic names have no sector.** `UniverseEntry.sector` defaults to "unknown" (agentic_trader/config.py:101-104), and `sector_groups` skips "unknown" (config.py:163-169, 1104-1107). So `correlation_group` (risk/rules.py:332-348) never applies to the up-to-20 dynamic names per scan. The only limit on them is a per-session card budget pseudo-group (agent/copilot.py:614-617), not a rule on the book. This is a real gap for the north star's "allocation based on existing positions", because a 20-session hold lets correlated movers pile up across days. Book-aware sizing in enforce limits size by marginal volatility, but not the count of overlapping bets.
- **The pooled cube has no exposure arrays.** `_ARRAYS` (research/pooled/cube.py:53) stores only `dollar_volume`: no sector, no ATR%. So the session-paired control removes only the equal-weighted mean, not style exposure (maps/pooled.md:130-131).

Corrections to the finding:
1. **`group_neutralize` is used.** It feeds `sector_rel_mom_60` (research/setups/features.py:27, 177-190). It is unused only in the pooled lane.
2. **`sector_rel_mom_60` is a development result, not a proven selector.** Its ρ +0.028 is development-only. The ridge ranker built on those features did significantly worse than `setup_quality` in the holdout: −0.293R vs −0.126R, paired CI [−0.325, −0.016] (setup-outcomes "Holdout"). The "6-month industry-momentum selector" is therefore a hypothesis.
3. **`formerNames` cannot give ticker history.** In the submissions JSON it holds former entity names, and `tickers` is current only. A CIK↔ticker-by-date table has to come from Alpaca corporate-actions `name_change` records (already acquired 2016+) plus the desk's own asset and SymDir snapshots.
4. **Shares outstanding adds little at this horizon.** `dei:EntityCommonStockSharesOutstanding` is a sparse cover-page value, and at a 20-session horizon size is already proxied by `dollar_volume` in the cube. Drop (c).
5. **The finding underplays the survivorship case for Form 25/15.** The 2026-10-20 survivorship spike found Alpaca serves bars for all 22 probed delistings. The missing piece is a dated membership list, and EDGAR Form 25/15 is the free source. The same doc explicitly recommends building it only when a cohort is next re-frozen, because no fixed-set result since Oct 7 was near a pass (docs/survivorship-spike-2026-10-20.md "Recommendation"). I agree.

**What to fund, in order:**
- **Now (P1, small effort):** industry labels from current SIC mapped to French Siccodes12/49 for the live universe, the dynamic names and cohorts v2/v3. Use them in correlation groups and card de-duplication, and as a cube `sector` array so the *next* lane can use `group_demean` and a sector × ATR-quintile matched control.
- **Separate tracks:**
  - 8-K 2.02 acceptance timestamps belong in a PEAD v3 spec. PEAD v2 long is the only passing leg (docs/apriori-pead-2026-09-26.md).
  - Form 25/15 goes with the next cohort re-freeze.
  - companyfacts: drop.

**Risks**

- **Scope creep in an M-sized bundle.** It really has four PIT tables, two nightly multi-GB SEC downloads and messy XBRL tags, and it would compete with the next alpha lane for the single operator's attention.
- **Look-ahead leakage.**
  - Current SIC and current tickers get applied to 2016–2026 history.
  - companyfacts values are amended after first filing.
  - An 8-K accepted after the close has to become visible at the next session. The finding's "session after filing" rule needs an explicit cutoff at 16:00 ET acceptance.
- **Coverage gaps.** ETFs, ADRs, SPACs, multi-class tickers that share a CIK, and names with no CIK are exactly where `symbol_ciks` (research/insider_filings.py:574) fails closed. Their groups need explicit handling (ETF as its own group, "unclassified" kept visible). It must not silently fall back to "unknown", which disables the rule.
- **New live dependency.** If the daemon fetches from the SEC, a fetch failure needs a defined behaviour: a cached file plus a readiness staleness check.
- **Coarse SIC buckets.** SIC can lump unlike businesses together (for example, software firms coded 7372 alongside unrelated issuers), so FF49 groups are a coarse risk proxy, not a factor model.
- **Forking paths.** Re-scoring the 64 closed fixed sets with risk-matched controls or `group_demean` would be second looks. Each needs a new frozen protocol and operator approval, and must not quietly reopen closed lanes.

**Prerequisites**

- **Existing plumbing to reuse:**
  - EDGAR user-agent and the 2 rps rate limiter (research/insider_filings.py:58-62).
  - CIK resolution via `symbol_ciks` (insider_filings.py:574).
  - The SHA-256-pinned research acquisition pattern.
- **A decided group contract for live risk.** Extend `UniverseEntry.sector` / `sector_groups` (config.py:101-104, 163-169, 1104-1107) so dynamic names get groups at scan time. Apply `max_correlated_positions` to them, and define the fallback for unclassified names.
- **For pooled use:** a new protocol version that declares the `sector` (and ATR%) cube arrays and the matched-control statistic before any data read, per the frozen-protocol discipline.
- **For Form 25/15:** wait for the next cohort re-freeze (docs/survivorship-spike-2026-10-20.md "Recommendation").
- **For 8-K timestamps:** a PEAD v3 spec.

**Better alternative**

A thin slice instead of the nightly bulk pipeline:
1. **SIC acquisition.** A research-only `alpha sic` acquisition reads SEC company_tickers.json (already fetched by the insider lane) plus the per-CIK data.sec.gov submissions JSON `sic` field. It covers about 1,500 symbols: live universe, cohorts v2/v3 and observed dynamic names. That is roughly 13 minutes once at the existing 2 rps, refreshed weekly, SHA-pinned. It maps through French Siccodes12/49 and declares current SIC as the historical proxy.
2. **Live use.** Use the result in the live universe so dynamic names join real `correlation_group`s, with the statistical-cluster option on the scan covariance (maps/risk.md:154) as a backstop for unclassified names.
3. **Cube use.** Store a `sector` array (and ATR%) in the next cube so the next lane can use `group_demean` and a DGTW-style sector × ATR-quintile control.
4. **French factors.** Download French daily FF5+UMD and 49-industry returns once, digest-pinned.

Skip companyfacts and shares outstanding. Fold Form 25/15 into the cohort re-freeze. Put 8-K item 2.02 acceptance timestamps in a PEAD v3 spec, since PEAD v2 long is the only passing leg and probably the most card-relevant use of EDGAR event data.

---

<a id="c17"></a>

## C17 — There is no shared incremental point-in-time bar and field store: live refetches a year of history per scan, research re-downloads per lane, and raw evidence pages fill a disk that is 96% full

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | later (months) | data | improvement | high | L | partially_confirmed |

_lenses: data, architecture_

### Claim (as consolidated)

Live fetching:
- fetch_data pulls 1 year of daily bars, 60 days of hourly and optionally 10 days of 15-minute bars per symbol per scan, at 150 requests per minute. That is about 180–250 s for roughly 180 names, and pacing alone would take 13–20 minutes at 1,000 names.
- cache_ttl_seconds is never read (market_data.py:66-72, 197-239).
- Multi-symbol batching (fetch_daily_many) exists but scans do not use it.

Research fetching:
- Research fetches per symbol per lane (runner.py:158-189). Cohort v3's hourly download took about 11 hours.
- Book sizing does its own fetch.

Storage:
- The live evidence store is 9.2 GB over 33 days, 340 MB on 2026-10-20 alone, against 796 MB for the entire 10-year research corpus.
- /System/Volumes/Data is 96% used.

Normalisation keeps only OHLCV and drops vwap and trade_count (bars.py:29; providers.py:286-300), so the DSL's vwap field has no data.

### Evidence

- agentic_trader/data/market_data.py:66-72, 197-239; agentic_trader/agent/copilot.py:2228-2253; config.py:698
- agentic_trader/research/setups/runner.py:158-189; agentic_trader/execution/book_sizing.py:215-237
- docs/alpha-pooled-cohort3-2026-10-16.md:31-36 (about eleven hours)
- agentic_trader/market/bars.py:29; agentic_trader/data/providers.py:286-300, 310-346
- Measured: du of ~/.local/state/agentic-trader/market-data (9.2G; 2026-10-20 340M), ~/agentic-trader-research/pooled-cache-v1 (796M); df /System/Volumes/Data 96%

### Recommendation

Put one BarStore/FieldStore behind BarSource, keyed by DataContract.

What it holds, appended nightly (and at :47 for completed RTH bars) from delayed SIP:
- daily raw bars plus split factors, and RTH hourly bars;
- all listed common stocks and ETFs above a low dollar-volume floor, fetched with multi-symbol requests;
- o/h/l/c/v/vw/n;
- per-session partitions, each with a digest;
- asset-master snapshots and the FieldSource daily files.

How it is used:
- Protocols pin slice digests instead of keeping per-lane caches.
- Live scans read history from the store and fetch only today's bars, one call per 100 names.

Retention:
- Keep manifests, results and hashes forever.
- Gzip pages after 7 days.
- Stop storing bodies of full-history refetches once the store holds those bars.

### North-star impact

Universe expansion becomes a config change at the same scan cadence, cube builds drop from about a day to minutes, the forward scorer gets its daily input, and the disk-exhaustion risk to the daemon goes away.

### References

- Microsoft Qlib data layer and Point-in-Time database — Reference design for a shared incremental research and live store (https://qlib.readthedocs.io/en/latest/advanced/PIT.html)
- Alpaca Stock Bars API (multi-symbol requests; vw and n fields) — Batch requests and the columns currently discarded (https://docs.alpaca.markets/reference/stockbars)

### Fact check — partially_confirmed

**Corrected claim**

The finding's core holds. No shared incremental point-in-time bar or field store exists: no BarStore, FieldStore or DataContract type is in the code.

**Live fetching**
- Every scan refetches the full history for each symbol through fetch_data: 1y daily, 60d hourly, and 10d of 15m bars only for a 15m timeframe or 15m alphas (market_data.py:197-239; copilot.py:834-837, 2228-2253).
- Requests are paced at 150 per minute (config.yaml:440). That is consistent with docs/production.md:734: two to three minutes per scan for about 160 names.
- MarketDataFetcher.cache_ttl_seconds is assigned (market_data.py:72) but never read.
- The main scan fetch path does not use fetch_daily_many (providers.py:310-346).
- Normalized frames keep only OHLCV (bars.py:29; providers.py:280-308). As a result the DSL's vwap field (dsl.py:29) is never present in any frame, and the miner silently drops it (miner.py:69).

**Corrections**
1. **Book sizing** does not do its own full fetch. It reuses the scan's daily frames and batch-fetches only missing names through fetch_daily_many (book_sizing.py:201-222). So the scan flow does use multi-symbol batching, but only for that residue.
2. **Research caching**
   - The pooled lanes (daily, intraday, events, short, insider) share one cohort-keyed cache, pooled-cache-v1, with bars/, bars_raw/ and a cube cache (pooled/runner.py:260-270, 324, 372). They do not re-download per lane.
   - Redundant downloads happen in two places:
     - per research family: separate caches such as setup-cache-v1, setup-cache-2017, apriori-pead-v1-cache and apriori-exdiv-cache;
     - per new cohort: cohort v3's hourly bars took about eleven hours, while its daily bars for 1,118 symbols took 12 minutes (docs/alpha-pooled-cohort3-2026-10-16.md:26-37).
3. **Research corpus size:** 796 MB is the pooled bar cache only. The research root is 2.9 GB in total.
4. **Disk**
   - The volume is 96% used, but it is 1.8 TiB with 87 GiB free.
   - The 9.2 GB evidence store is about 0.5% of used space. It is not what fills the disk.
   - The store grows by about 300-340 MB per trading day, and nothing deletes it; that is a deliberate, documented policy (docs/market-data-evidence.md:60; config.yaml:431).
   - At that rate it would use the remaining space in roughly 9-12 months. Then the 1 GiB reserve check (evidence.py:93-104) would reject every Alpaca bar capture, and the composite provider would fall back to yfinance (market_data.py:91-108).
5. **vw and n are not lost at acquisition.** The raw evidence pages keep every decoded field, including vw and n (docs/market-data-evidence.md:20-22). They are dropped only when frames are normalized.

**Checked**

- agentic_trader/data/market_data.py:63-108 (MarketDataFetcher: cache_ttl_seconds assigned at :72 and never read; Alpaca primary plus yfinance fallback composite at :80-108)
- agentic_trader/data/market_data.py:197-239 (fetch_data: 1y daily, 60d hourly, optional 10d 15m, one paced request per timeframe per symbol, no cache)
- grep of cache_ttl across agentic_trader: other modules read their own TTLs, MarketDataFetcher does not
- agentic_trader/agent/copilot.py:2228-2253 (_fetch_universe calls fetch_data per instrument under a semaphore); copilot.py:834-837 (15m only for a 15m timeframe or 15m alphas)
- agentic_trader/config.py:697 and config/config.yaml:439-440 (scan_concurrency 8, max_requests_per_minute 150)
- config/config.yaml universe: etf32 32 + mega_caps 65 + research_cohort 62 + up to 20 dynamic + 13 contracts, about 180 names
- docs/production.md:734 (expect two to three minutes per scan for ~160 names at two reads each)
- agentic_trader/data/providers.py:257-308 (_normalize_bars keeps only OHLCV), :310-346 (fetch_daily_many batches 100 symbols)
- agentic_trader/market/bars.py:29 (OHLCV tuple)
- agentic_trader/research/alpha/dsl.py:29, 186-199 (vwap declared, only present if the frame has the column); miner.py:38, 69 (vwap silently filtered out when unavailable)
- agentic_trader/execution/book_sizing.py:188-237 (reuses the scan's daily frames, batch-fetches only missing names through fetch_daily_many)
- agentic_trader/research/setups/runner.py:100-189 (per-(symbol, timeframe) npz cache inside a caller-supplied cache_dir)
- agentic_trader/research/pooled/runner.py:250-300, 324, 372 (pooled lanes share one cohort-keyed cache_dir with bars/ and bars_raw/ plus a cube cache)
- docs/alpha-pooled-cohort3-2026-10-16.md:26-37 (about eleven hours for hourly bars on 1,000 symbols; daily bars for 1,118 symbols took 12 min)
- docs/market-data-evidence.md:20-22, 33-40, 51-61 (raw pages keep every decoded field; 1 GiB reserve; 'There is no automatic deletion' at :60); config/config.yaml:431 ('never prune retained evidence')
- agentic_trader/data/evidence.py:93-104 (capacity check raises EvidenceCapacityError below the reserve)
- Measured: df /System/Volumes/Data shows 1.8Ti size, 1.7Ti used, 87Gi free, 96%; du market-data 9.2G across 33 date dirs; 2026-10-20 340M (3,432 captures), weekdays 317-340M, weekend 1-22M; pooled-cache-v1 796M; ~/agentic-trader-research total 2.9G (apriori-pead-v1-cache 766M, short-volume-cache 648M, insider-filings-cache 479M, setup-cache-v1 53M, setup-cache-2017 38M, apriori-exdiv-cache 50M)
- grep for BarStore/FieldStore/FieldSource/DataContract: none exist (only BarSource Protocol at research/setups/runner.py:100)
- https://docs.alpaca.markets/reference/stockbars (multi-symbol 'symbols' parameter; bars carry vw and n; the limit counts all symbols together)
- https://qlib.readthedocs.io/en/latest/advanced/PIT.html and https://qlib.readthedocs.io/en/latest/component/data.html

**Bad references**

- Microsoft Qlib data layer and Point-in-Time database (https://qlib.readthedocs.io/en/latest/advanced/PIT.html): the page exists but only partly supports its use. It covers a PIT store for quarterly and annual fundamentals, says outright that only those frequencies are supported, and does not describe price-bar storage or incremental updates. Qlib's bar data layer and daily incremental update (dump_bin, update_data_to_bin) are documented at https://qlib.readthedocs.io/en/latest/component/data.html, which would be the right citation for a shared incremental bar store.

**Notes**

The Alpaca Stock Bars reference checks out: one request takes multiple symbols, bars include vw and n, and the limit counts all symbols together, so multi-symbol requests need next_page_token pagination.

Two things the recommendation should account for:
- Never deleting evidence is an intentional contract (docs/market-data-evidence.md:60, 'There is no automatic deletion'; config.yaml:431). Gzipping pages, or dropping bodies of full-history refetches, changes that contract and should be framed as a policy change.
- Research replay deliberately mirrors the live fetch_data(daily_period="1y"/hourly "60d") windows (research/setups/replay.py:12-27, 104, 182-187). Moving live scans onto a store therefore needs replay parity preserved.

The scan-duration figure (180-250 s for about 180 names) is plausible. Pacing alone gives 144-216 s at 2-3 requests per name, and docs/production.md:734 says two to three minutes.

The north-star impact claim that 'the disk-exhaustion risk to the daemon goes away' overstates the cause. The disk is nearly full for reasons other than the evidence store, so the store mainly sets a slow countdown toward the 1 GiB reserve.

### Value review — P2, later (months)

**Reasoning**

The facts mostly check out, but the store as proposed does not address what is actually blocking the north star. One part of the finding is urgent, and it should be split out and done now.

**Verified in the code:**
- `fetch_data` makes three single-symbol paced requests per contract: 1y daily, 60d hourly and 10d 15m (agentic_trader/data/market_data.py:197-239). `_fetch_universe` calls it per instrument (agentic_trader/agent/copilot.py:2228-2253).
- `cache_ttl_seconds` is assigned but never read (market_data.py:66,72).
- `fetch_daily_many` batches 100 symbols per request but captures no evidence (data/providers.py:310-346). Only book sizing and PEAD use it (execution/book_sizing.py:222; research/apriori/pead_live.py:135-137).
- Normalisation keeps OHLCV only (market/bars.py:29; providers.py:291-293). `vwap` appears only in the retired per-symbol miner's `SEARCH_FIELDS` (research/alpha/miner.py:38).
- Measured: the evidence store holds 9.2G, about 320 MB per trading day (2026-10-12 to 10-20). The disk has 87 GiB free (96% used).

**Overstated claims:**
1. The store is not why the disk is full. 9.2 GB is about 0.5% of the 1.7 TiB in use, so a BarStore alone does not make "the disk-exhaustion risk go away".
2. "Research re-downloads per lane" is mostly not true for the pooled lanes. The events, short-volume and insider lanes all reused the cached v2/v3 cubes (CLAUDE.md; docs/alpha-pooled-short-v1-2026-10-20.md). The roughly 11-hour download (docs/alpha-pooled-cohort3-2026-10-16.md:31-32) is a one-off cost per new cohort, and the docs say a new cohort is not the next step.

**Why it does not move the north star:**
- 64 formula-cohort pairs produced no finalist.
- The bracket has been closed as an explanation (docs/bracket-counterfactual-2026-10-18.md).
- Mid-caps halved the base rate (docs/alpha-pooled-cohort3-2026-10-16.md).

So neither universe breadth nor research throughput is the binding constraint; hypothesis quality and live/research parity are. Widening the live universe for a strategy with no measured edge produces more cards that trail SPY, not better ones. Faster cube builds mainly make it cheaper to spend the multiple-testing budget. A scan of about 180 names taking 3-4 minutes twice a day does not hurt the operator.

**The part that matters now, and it is cheap:**
- The evidence store never deletes (docs/market-data-evidence.md:60-62) and grows about 1.6 GB a week. Other programs on the disk also eat into the headroom.
- When free space falls below `min_free_bytes` (1 GiB, config.py:684), `check_capacity` raises inside the Alpaca fetch (data/evidence.py:93-95; providers.py:234-235, 273-275).
- That raise becomes a `BarAcquisitionError`, and `CompositeMarketDataProvider` silently falls back to yfinance (providers.py:505-529; market-data-evidence.md:57-58).
- The result: on a full disk, every live scan quietly switches to auto-adjusted, consolidated-volume yfinance bars. The dynamic liquidity gate and the card statistics are then wrong, and nothing alerts.
- A retention policy plus a free-space guard is a P1 measured in hours. A full BarStore is not needed to fix it.

**When the full store would be worth funding:**
- A field family (FINRA, EDGAR) reaches a finalist and needs daily live ingestion with a parity test (map G6).
- A deliberate breadth hypothesis needs a wide live universe.
- The store is used to fix the IEX-vs-SIP mismatch between live and research bars (map G1). That is the strongest argument for it, but the finding does not make it, and a recent-SIP 403 means today's trigger bar would still come from IEX.

**Risks**

- **Effort and opportunity cost.** This is L effort for a single operator. Weeks of plumbing would displace entry-side alpha work and add no edge.
- **Point-in-time correctness.** A shared store mixing raw bars, split factors and adjusted bars could quietly break the careful raw-vs-adjusted discipline (research/pooled/cohort.py:205-211; book sizing refuses non-raw frames in research/setups/covariance.py:20-25). It would also inherit the `asof`/ticker-reuse gap: `asof` is listed (evidence.py:24-27) but never set (providers.py:244-252), and FISV and SBNY are reused tickers. Stored history would also have to be restated after every corporate action.
- **Live migration risk.** Replacing per-scan full-history fetches changes the evidence contract (native scan records do not pin input frames; market-data-evidence.md:95-98). It also changes the coverage gate's inputs and the semantics of the yfinance fallback, which touches the live path behind CLAUDE.md contracts 3, 4 and 8.
- **Research hygiene.** Cheap cube builds make it easy to burn the confirmation window and the trial budget on more cohort and field variants after 64 nulls.
- **Retention must stay within the contracts.** It must not delete evidence that journal entries or the card-outcome/`card_stats` labelling still reference. Gzip in place and keeping digests is safe. Deletion needs a reference check.

**Prerequisites**

- **Before retention:**
  - An inventory of which journal events, card provenance and research manifests point at `market-data/<date>/<uuid>` pages, so retention never orphans referenced evidence.
  - An operator decision on keeping the "no automatic deletion" contract (docs/market-data-evidence.md:60), with an amendment such as gzip after N days and archival off the boot volume.
- **Before a full store:**
  - A decided data contract for live vs research (feed, adjustment, session layout) that resolves the IEX-vs-SIP and UTC-4h parity gap first (map G1). Otherwise the store just caches two inconsistent series faster.
  - A concrete consumer that needs it: a finalist that needs daily FINRA/EDGAR fields live, or a predeclared breadth hypothesis.
  - Wiring `asof` into bar requests and a ticker-identity key (CIK or Alpaca asset UUID) for the asset master.

**Better alternative**

Split it.

**Now (P1, hours to a day):**
- Gzip evidence pages older than 7 days in place, keeping manifests, results and sha256s. Pages are JSON, so expect a 5-10x reduction.
- Add a free-space headroom item to readiness and `doctor --monitor`, alerting well above the 1 GiB reserve, for example at 50 GiB.
- Make `EvidenceCapacityError` a distinct failure rather than an ordinary Alpaca error that silently degrades the scan to yfinance (providers.py:273-275, 505-529). Either refuse the scan or mark it degraded.
- Optionally, move the evidence directory off the 96%-full system volume.

**Next, only if it is cheap:**
- Route the scan's 1y daily leg through `fetch_daily_many` (one call per 100 names), with evidence capture added to it.
- Either delete the dead `cache_ttl_seconds` or use it.
- This roughly halves paced requests, from 3 per name to about 2 per name.

**When a hypothesis needs them:**
- Add `vw` and `n` to normalisation, as a new versioned data contract, when a predeclared hypothesis needs VWAP or trade count.

**Defer the unified BarStore/FieldStore (Qlib-style point-in-time store)** until a finalist needs daily live field ingestion, or until it is built as the vehicle for live/research parity. Spend the saved effort on the entry-side and parity work, which bears directly on card quality.

---

<a id="c22"></a>

## C22 — PEAD runs on events with no timing, survivors only and today's consensus; free EDGAR data fixes all three

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | gap | high | M | partially_confirmed |

_lenses: data_

### Claim (as consolidated)

PEAD's documented caveats are all data caveats:
- The report time is unknown, so entry waits until D+2.
- Nasdaq history lacks delisted names.
- EPS and consensus are as served today.

Measured on 16,755 cached Nasdaq pages:
- `time` is 'time-not-supplied' on 100% of rows for 2016–2025, and on 95% in 2026.
- Row counts grow from 9,673 (2016) to 17,693 (2025), consistent with dead reporters missing.
- epsForecast is present on 77–83% of rows.

The live blackout fetches the same pages but caches them only in memory.

EDGAR submissions carry the per-filing acceptanceDateTime, form and 8-K items. Verified example: an 8-K for CIK 320193 was accepted at 20:30 ET, after the close.

The consensus vintage also contaminates the surprise filter. The point-in-time-clean reaction-only rule earned +0.120R against a control of +0.094R.

### Evidence

- docs/apriori-pead-2026-09-26.md 'Reading the result' and 'Caveats' (lines 111-136)
- Measured: ~/agentic-trader-research/apriori-pead-v1-cache/nasdaq/*.json (time field, rows by year, epsForecast coverage)
- agentic_trader/agent/earnings.py:185-240
- docs/alpha-data-augmentation-2026-10-18.md §2 row 3, §3 'Point-in-time earnings'
- https://data.sec.gov/submissions/CIK0000320193.json (acceptanceDateTime, form, items verified)

### Recommendation

Build PEAD v3 inputs from the EDGAR reference acquisition (C16):
- Event instant: the 8-K Item 2.02 acceptanceDateTime, mapped to the first regular session at or after it; acceptances after 16:00 roll to the next session. Pre-open reports can then be entered at D+1.
- Event set: all EDGAR filers that had a ticker on the date, plus dead names from the Alpaca inactive list and Form 25.
- Surprise: a point-in-time measure with no consensus vintage. Use a seasonal random-walk SUE from XBRL EPS with filed dates, or the announcement return (EAR).
- Persist live Nasdaq pages daily so analyst SUE accrues going forward.

Declare v3 as a second look on a window already read. It can demote the leg or support renewal, but it never counts as independent confirmation.

### North-star impact

Bases PEAD's renewal, widening or kill on survivorship-complete, correctly timed events, and may capture drift that the D+2 rule forgoes.

### References

- Livnat & Mendenhall (2006), Comparing the PEAD for surprises calculated from analyst and time series forecasts, J. Accounting Research — Time-series SUE from filed EPS is point-in-time without consensus history
- Brandt, Kishore, Santa-Clara & Venkatachalam (2008), Earnings announcements are full of surprises — Return-based surprise needs only the timestamp and bars
- SEC EDGAR APIs — Free acceptance timestamps and 8-K items (https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

### Fact check — partially_confirmed

**Corrected claim**

The core gap is real. No code acquires EDGAR 8-K timestamps or XBRL EPS. The PEAD study ignores the Nasdaq timing field and enters at D+2 (pead_live.py:122). The blackout cache is in memory only (earnings.py:189-198). The live PEAD probe reads pages through the uncached reported_rows (earnings.py:243) and journals only the page SHA-256 and leg events (earnings_drift.py:148), so no raw page history accrues.

Several details need correcting:

(1) Page count: the cache holds 2,724 page files, of which 2,612 are non-empty, with 151,271 rows. It does not hold 16,755 pages. The v2 study used 2,615 pages and 147,031 rows.

(2) Consensus coverage: epsForecast is present on 76.5-80.0% of rows, not 77-83%. The time-not-supplied shares (100% for 2016-2025, 94.6% for 2026) and the 9,673/17,693 row counts are correct.

(3) Row growth: 2016 is a partial year (cache starts 2016-02-23, 224 pages, with an empty June-July gap). The fair comparison is 2017 to 2025: 11,698 to 17,693 rows, or about 45 to 68 rows per page. That growth is consistent with survivorship, but also with wider Nasdaq coverage.

(4) The EDGAR example is wrong. Apple's 8-K 0000320193-25-000077 was accepted at 16:30:35 ET according to its EDGAR index page, not at 20:30 ET. The submissions JSON shows "2025-10-31T00:30:35.000Z". Read as UTC, that is 4 h late in EDT and 5 h late in EST (2026-01-29: 16:30:33 ET vs "2026-01-30T02:30:33Z"). Used at face value, the field would push pre-open 8-Ks (for example 07:00 ET) into the session and break the proposed D+1 rule. v3 must take the acceptance instant from the filing header or index (ET wall time), or validate it against filingDate and the 17:30 ET cutoff.

(5) "Free EDGAR fixes all three" overstates the case:
- Consensus vintage is not fixed. It is replaced by a different surprise (time-series SUE or EAR). The repo's own assessment (alpha-data-augmentation section 3) says consensus history needs a paid vendor, and Livnat & Mendenhall find time-series SUE gives weaker drift than analyst SUE.
- A survivorship-complete event set also needs a dated CIK-to-ticker history, which EDGAR does not publish as a list. Alpaca's inactive list stops around 2022.
- 8-K Item 2.02 misses foreign issuers that file 6-Ks, who appear in the Nasdaq rows.

(6) "Point-in-time-clean reaction-only" (+0.120R vs control +0.094R, doc line 85) is clean only with respect to consensus vintage. Its event set still comes from the survivor-only Nasdaq pages.

(7) The 8-K timing plus Form 25 direction is already the repo's sequenced plan (alpha-data-augmentation section 2 row 3 and section 4 item 3; survivorship-spike). The new parts of this finding are the time-series SUE/EAR substitute and daily persistence of raw Nasdaq pages.

**Checked**

- docs/apriori-pead-2026-09-26.md:85 (Diagnostics: reaction-only +0.120R, n 7,869), :109-127 (Reading the result: control +0.094R), :129-143 (Caveats: survivorship 9.7k vs 17.7k rows, report time unknown so D+2, EPS/consensus as served today)
- agentic_trader/agent/earnings.py:189-198 (blackout cache is an in-memory dict with TTL), :200-240 (_fetch_date/_request_date), :243-268 (reported_rows, 'Uncached')
- agentic_trader/research/apriori/pead_live.py:119-123 (report_date = trading[-3], decision at D+2; reads the page via reported_rows)
- agentic_trader/screeners/earnings_drift.py:135-148 (live PEAD probe journals only page_sha256 and leg events, not page bodies)
- agentic_trader/research/apriori/pead_events.py and earnings_history.py: the Nasdaq timing field is never read, so the study ignores report time
- grep agentic_trader/ and config/ for acceptanceDateTime, 8-K, 2.02, data.sec.gov, companyfacts, XBRL and SUE: no EDGAR 8-K or XBRL code exists; insider_filings.py uses Form 3/4/5 data sets only
- Measured ~/agentic-trader-research/apriori-pead-v1-cache/nasdaq: 2,724 page files (2,612 non-empty), 151,271 rows; time-not-supplied 100.0% for 2016-2025 and 94.6% for 2026; rows 9,673 (2016, 224 pages) and 17,693 (2025); epsForecast non-empty on 76.5-80.0% of rows
- docs/alpha-data-augmentation-2026-10-18.md:44 (section 2 row 3: EDGAR 8-K timestamps 'with PEAD v2'), :68-70 (section 3 Point-in-time earnings: consensus history needs a paid vendor), :86 (section 4: EDGAR timestamps + PEAD v2 already sequenced as lane #3)
- docs/survivorship-spike-2026-10-20.md:11-16, 47, 62-63 (Alpaca inactive list stops around 2022; Form 25/15 workstream planned)
- config/research/apriori/ holds pead-v1.json and pead-v2.json, so 'v3' is the right next number
- WebFetch data.sec.gov/submissions/CIK0000320193.json: 8-K 0000320193-25-000077 acceptanceDateTime '2025-10-31T00:30:35.000Z', 0000320193-26-000018 '2026-07-31T00:30:28.000Z', 0000320193-26-000005 '2026-01-30T02:30:33.000Z', items 2.02,9.01
- WebFetch EDGAR index pages for those three accessions: Accepted 2025-10-30 16:30:35, 2026-07-30 16:30:28 and 2026-01-29 16:30:33 (Eastern)
- WebFetch https://www.sec.gov/search-filings/edgar-application-programming-interfaces (exists; documents submissions and companyfacts; says nothing about the acceptanceDateTime time zone)

**Bad references**

- Measured claim '16,755 cached Nasdaq pages' is wrong: the cache has 2,724 page files (2,612 non-empty, 151,271 rows)
- Measured claim 'epsForecast present on 77-83% of rows' is wrong: the measured range is 76.5-80.0%
- data.sec.gov/submissions/CIK0000320193.json 'verified example ... accepted at 20:30 ET' is wrong: the EDGAR index page shows Accepted 2025-10-30 16:30:35 ET. The JSON acceptanceDateTime ('2025-10-31T00:30:35.000Z') is not a true UTC instant and was misread.

**Notes**

I verified the per-year shares and counts with a read-only python -I script over the cache (scratch scripts are in ~/agentic-trader-research/holistic-review-20261021/c22/).

Timestamp offset: the submissions-JSON values sit 8 h after the Eastern index time in EDT and 10 h after it in EST, which looks like the ET-to-UTC offset applied twice. I read the JSON through WebFetch. The seconds match across all three filings, which supports correct alignment, but the exact JSON strings should be re-checked directly before v3 relies on any offset correction. Either way, the finding's '20:30 ET' is wrong and the after-close conclusion still holds.

The PEAD doc citation 'lines 111-136' covers Reading the result and most of Caveats. The +0.120R reaction-only figure is at line 85, outside the cited range, but it does match the doc.

Literature references:
- Livnat & Mendenhall (2006) J. Accounting Research 44(1) exists and is relevant. Caveat: it finds analyst-based SUE produces larger drift than time-series SUE, and it used Compustat data, not EDGAR XBRL.
- Brandt, Kishore, Santa-Clara & Venkatachalam, 'Earnings announcements are full of surprises', exists as a 2008 SSRN working paper and supports return-based (EAR) surprise.
- The SEC EDGAR API page exists and documents the submissions and companyfacts APIs.

XBRL detail for the SUE plan: Q4 EPS usually has to be derived from 10-K annual minus the 9-month figure, and XBRL EPS is GAAP EPS including special items. Both complicate the seasonal random-walk SUE and should be specified in the frozen entry.

### Value review — P2, next (weeks)

**Reasoning**

The diagnosis is correct and matters, but the remedy as written costs too much for what it buys.

**What I verified**
- The data caveats are real. I sampled every fifth file of the 5,448-file `~/agentic-trader-research/apriori-pead-v1-cache/nasdaq` cache. Every row from 2016 to 2025 has `time-not-supplied`. In 2026 only 223 of 2,257 rows carry timing.
- The caveats are documented in docs/apriori-pead-2026-09-26.md 'Caveats'.
- `reported_rows` in agentic_trader/agent/earnings.py:243-247 is uncached, so no live page is kept.
- PEAD long matters a great deal. It is the only research result that reaches Telegram (maps/alpha_pipeline.md:187), and its paired edge, +0.055R, is the desk's only positive one.

**Why P2, not P0/P1**
1. **It cannot change the next decision.** The probe renewal is due 2026-10-28 (maps/history.md:241), which is days away. The finding itself says a second look on an already-read window can only demote the leg, never confirm it.
2. **Its biggest target is the least exposed number.** Survivorship mainly inflates the absolute +0.149R. The paired +0.055R, which carries the evidence, is affected less, because the leg and its control lose the same names ('Reading the result').
3. **One proposed source does not hold up.** Using the Alpaca inactive list for dead names conflicts with docs/survivorship-spike-2026-10-20.md:11-17, which found that list is not a delisting record after about 2022. Building the dead-name set needs dated Form 25/15 filings plus a mapping from each CIK to the tickers it used over time. That map does not exist for free (the SEC map lists current tickers only), and tickers get reused (236 at :28). That work is L, not M.
4. **The XBRL SUE is not point-in-time.** XBRL EPS becomes visible only when the 10-Q or 10-K is filed, weeks after the 8-K announcement. Financial data in an 8-K is not XBRL-tagged. Honouring the filed dates makes SUE useless for a D+1 or D+2 entry. Using the values as of the announcement date brings back the same look-ahead the change is meant to remove.
5. **Timing gains little.** The 8-K Item 2.02 timestamp helps only pre-market reporters, moving their entry from D+2 to D+1, which is about one session of a 20-session drift. A live change would also create a new probe identity and restart the forward record.

**What is worth doing**
- **Persist the live Nasdaq pages now.** It costs almost nothing, and analyst-consensus history starts accruing point-in-time.
- **Use EDGAR acceptance times for event timing,** both historically and live. This is the cleanest piece and also serves future event lanes.
- **Keep any v3 as a sensitivity check** of the paired number against EAR surprise and dated delistings, run once the Form 25/15 list (already queued at maps/history.md:249) exists. It should not be a new probe version.

Against 64 null pooled tests, the binding constraint on PEAD is not historical fidelity. It is forward evidence: one drift card per session, 440 trades needed to confirm (maps/history.md:187), and a forward counterfactual with no control (probe_outcomes.py:96-129 has no every-reporter arm).

**Risks**

- **Forking paths:** a second look on the 2016-26 window that was already read and inspected invites tuning. If v3 is ever used to "support renewal" rather than to demote only, it becomes confirmation bias.
- **Bad event joins:** CIK-to-ticker errors for dead and reused symbols would create events that were never real. These could move the paired number in either direction and be hard to audit.
- **Misclassified timing:** 8-K acceptance time is not press-release time. Some filers submit the 8-K during or after the session that follows a pre-market release, which pushes their entry a session too late.
- **The XBRL filed-date lag** (reasoning, point 4) quietly reintroduces look-ahead.
- **Effort creep:** the work grows from M to L (EDGAR reference build, delisting list, SUE pipeline) and competes with higher-leverage card-stream work: the selector A/B, `card_policy` preview and the prospective pooled scorer.
- **A live D+1 change** resets the probe's forward record under a new identity.

**Prerequisites**

- **Before any historical v3:**
  - The PEAD renewal decision on 2026-10-28, made independently on workflow and kill criteria.
  - A dated EDGAR delisting and ticker-change list (Form 25/15), per docs/survivorship-spike-2026-10-20.md.
  - The EDGAR reference acquisition (C16): a cached submissions index with acceptanceDateTime, form and items, plus a mapping from each CIK to the tickers it used over time. `EDGAR_USER_AGENT` already exists for the insider lane.
  - A frozen v3 entry that is explicitly labelled a demote-only second look, with operator approval.
  - SIP bars for dead symbols, which docs/survivorship-spike-2026-10-20.md confirms are served.
- **For the live timing change:** a new probe version and enrolment through `alpha apriori-probe`.

**Better alternative**

Two cheap steps give point-in-time, survivorship-free PEAD evidence going forward, without rebuilding history from EDGAR:

1. **Persist every live Nasdaq calendar page** that the 10:35 drift preparation reads, using the existing digest-pinned evidence and journal path. `reported_rows` is uncached today (agentic_trader/agent/earnings.py:243-247). Point-in-time consensus and timing history then accrue at no research cost.
2. **Add the study's every-liquid-reporter control to the probe's forward counterfactual.** probe_outcomes.py already labels every mature LONG-leg event the scan saw, but computes no control. `cards outcomes` would then report the paired leg-minus-control edge on live data.

Live calendars include future-dead names and today's consensus, so this forward record is survivorship-free and point-in-time by construction. It is the evidence that can actually support renewal or a kill of PEAD.

If a historical re-check is still wanted, cut v3 down to:
- EDGAR 8-K Item 2.02 acceptance timing;
- the announcement-return (EAR) surprise, with no XBRL SUE;
- dead names from the queued Form 25/15 list.

Run it once as a sensitivity check of the paired +0.055R, not as a new probe version.

---

<a id="c26"></a>

## C26 — The card does not present an allocation decision: tiers collapse at the cap and the book line always reads x1.00

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | product | improvement | high | S | partially_confirmed |

_lenses: portfolio_

### Claim (as consolidated)

The card shows a tier list (telegram_bot.py:322-345), 'Capital Risk: x% of cash' (:383), and one book-volatility line (:131-153).

It does not show:
- which constraint is binding;
- beta-dollars or sector exposure;
- correlation to current holdings;
- stress loss;
- cost in R;
- the prior.

For about 84% of setups the tiers collapse to Half plus 'Standard / Max' (position_sizing.py:219-221), and default risk shows about $135 rather than $250. All six preview book factors were 1.00 (card-evidence.md:232).

Engagement is low: 9 of 42 cards were tapped.

### Evidence

- agentic_trader/notifier/telegram_bot.py:131-153, 322-345, 383
- agentic_trader/agent/position_sizing.py:219-221
- docs/card-evidence.md:232
- Live map Q10 (9 of 42 cards tapped)

### Recommendation

Once the sizing (C12) and factor-model (C08) work lands, replace the sizing and book lines with one render-only 'Allocation' block:
- size and notional (% of capital), risk to stop, ex-ante volatility in $/day, and the constraint it is bound by;
- the book after the card: names, beta-dollars before and after, sector dollars against the cap;
- marginal contribution to book volatility, and ρ to the book with the highest pair named;
- stress loss (for example SPX −6%) and stop risk in use;
- the edge prior (alpha ± se, replay n, live n) and round-trip cost in R.

Label tiers by what binds, and drop 'Max' when it equals Standard. Journal the same fields for cards outcomes.

### North-star impact

Makes the north star's 'allocation decisions based on existing positions' visible on every card.

### References

- Grinold & Kahn (1999), Active Portfolio Management, 2nd ed. — Risk decomposition and marginal contribution to risk for presenting a position's portfolio effect

### Fact check — partially_confirmed

**Corrected claim**

The card does not present an allocation view of the book. It shows nothing on beta-dollars, sector exposure, correlation to current holdings or stress loss, and the book line carries only vol before and after plus a factor. That factor was 1.00 in all six journaled preview cases (docs/card-evidence.md:231-232). It is structurally rare below 1 at the current config, but it is not "always" 1.00.

The finding understates what the card already shows in three places:

1. **Binding constraint.** Sizing gating reasons are rendered under the tier list (position_sizing.py:139-144 → evaluator.py:808-849 → telegram_bot.py:342-344). On notional-capped cards this reads "Max size capped by remaining $7,500 notional limit", and because Standard equals Max there, it names the binding constraint for the default tier as well. Drawdown and macro haircuts are rendered the same way (position_sizing.py:76-87). Not shown: constraints that bind only at admission, such as the asset-class cap, aggregate stop risk and the correlation group.
2. **Prior and cost.** A measured prior is shown on every native card (card_evidence.py:157-195): n mature candidates, target/stop/timeout rates, mean R after cost, implied EV at the card's R:R, and the cost assumption in bp/side. What is missing is an uncertainty (se or CI), a replay-n versus live-n split, and cost stated in R.
3. **Collapsed tiers.** When base equals max, the code already merges the two into a single "Standard / Max" tier (position_sizing.py:219-221). The "drop Max when it equals Standard" recommendation is therefore cosmetic.

The quantitative claims hold on replayed setups, not live cards. Across 8,768 A0_live rows of the bracket-counterfactual frame, 83.9% have a stop tighter than 250/7,500 = 3.33%. Those setups collapse to Half plus "Standard / Max" with risk of 7,500 × stop%, which gives a median base-tier risk of $135 against the configured $250 (config.yaml:337,343).

The "9 of 42 tapped" figure comes from the review's own live-map query, Q10. That same map attributes the low engagement partly to 659 operational notices burying the cards (live.md:81), so card presentation is not established as the cause.

**Checked**

- agentic_trader/notifier/telegram_bot.py:131-153 (_book_line: renders vol before/after, budget and factor for applied/unchanged; adds a preview suffix)
- agentic_trader/notifier/telegram_bot.py:322-345 (tier list, which also renders eval_res.gating_reasons as 🛡️ lines at :342-344)
- agentic_trader/notifier/telegram_bot.py:255,383 (Capital Risk = risk_dollars / portfolio_cash)
- agentic_trader/notifier/telegram_bot.py:122-127,365-370 (evidence block rendered on every native card)
- grep of telegram_bot.py for correlat|beta|sector|stress|cost: no card fields for any of them
- agentic_trader/execution/card_evidence.py:157-195 (Measured record: n mature, target/stop/timeout %, mean R after cost, Implied EV at R:R, feed and bp/side caveat)
- agentic_trader/agent/position_sizing.py:94-96,109-113 (notional ceiling min(7500, remaining) bounds max_qty)
- agentic_trader/agent/position_sizing.py:139-144 (gating reason 'Max size capped by remaining $X notional limit' or '...risk ceiling')
- agentic_trader/agent/position_sizing.py:153-165 (static mode: base budget $250 clamped to max_qty)
- agentic_trader/agent/position_sizing.py:212-224 (tier branches; :219-221 Half + 'Standard / Max' when base == max)
- agentic_trader/agent/evaluator.py:272-284,808-849 (sizing gating_reasons reach the card's gating_reasons)
- config/config.yaml:333-347 (static mode, default_equity_risk_dollars 250, max_trade_notional_cap 7500, max_risk_pct_cap 0.01)
- docs/card-evidence.md:231-232 (preview 2026-10-09 to 2026-10-19: six journaled factors, all 1.00 against a one-share book; enforce since 2026-10-20)
- Read-only recompute on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz, A0_live arm: 8,768 rows; 83.9% have a stop under 3.33% of entry; base-tier risk min(250, 7500*stop%) has median $135, mean $147, p10 $66
- scratchpad holistic-review/maps/live.md:35,81 (Q10: 9 of 42 cards tapped; the map also blames 659 incident notices burying the cards)
- WebSearch: Grinold & Kahn, Active Portfolio Management 2nd ed. (McGraw-Hill, 1999/2000), Part I includes the 'Risk' chapter

**Bad references**

_none_

**Notes**

All cited file:line locations exist and contain what the finding says. The cap mechanism itself sits at position_sizing.py:94-113 and 153-165; :219-221 is only the resulting label branch.

The 84% and ~$135 figures reproduce exactly from the frozen bracket-counterfactual frames (median $135, 83.86% notional-capped). They describe historical replayed native setups, not the 42 live cards.

The Grinold & Kahn reference is real: McGraw-Hill, 2nd ed., 1999/2000, with a Risk chapter in Part I. It is appropriate for marginal contribution to risk.

The core of the gap is valid: there are no beta, sector, correlation-to-holdings or stress fields on the card, and the book line adds little at the current config. The finding misdescribes the card by omitting the measured-record and implied-EV block and the gating-reason line, which partly cover "the prior", "cost" and "which constraint is binding".

The search only confirmed indirectly that the book's Risk chapter treats marginal contribution to risk:
- https://www.risk.net/journal-of-computational-finance/2160384/portfolio-selection-with-marginal-risk-control
- https://publikationen.ub.uni-frankfurt.de/opus4/files/41685/SSRN-id2854194.pdf
- https://webpac.nkut.edu.tw/toread/opac/bibliographic_view/1000049614
- https://accueil-bibliotheque-nice.edhec.edu/KENTIKA-19141699124919698719-16-Active-portfolio-management-a-.htm

### Value review — P2, next (weeks)

**Reasoning**

This is a presentation change. It adds no edge, and the binding problem for the north star is that measured outcomes trail SPY: the live bracket is −0.110R versus SPY (docs/bracket-counterfactual-2026-10-18.md "Descriptives"), and 64 pooled pairs produced no finalist. A richer allocation block on an edgeless card does not make the card high quality. Parts of the finding are also overstated against the code.

What the card already shows:
- **The edge prior.** The card already renders the measured prior: n mature, target/stop/timeout rates, mean R after cost, implied EV at the card's R:R, and the cost in bp/side (agentic_trader/execution/card_evidence.py:166-197, called from telegram_bot.py:123-128). The finding says the card lacks "the prior" and "cost in R"; the prior is there, and only a separate cost-in-R figure is missing.
- **The binding constraint for the max tier.** The card prints it as a gating line, "Max size capped by remaining $X notional limit" or "... risk ceiling" (position_sizing.py:143-147, rendered at telegram_bot.py:340-342). What it does not explain is that the base tier is clamped to that cap (position_sizing.py:161-166). So a card labelled with a $250 default can risk about $135 with no explanation, and in that case the label "Standard / Max" (position_sizing.py:219-221) is misleading.

Why the full block shows little today:
- The book line reads ×1.00 because the paper book is nearly empty: six factors, "all 1.00 against a one-share book" (docs/card-evidence.md:232). This is not a rendering defect.
- With an empty book, every part of the proposed block is close to zero or trivially uninformative: beta-dollars before and after, sector dollars, MCTR, ρ to the book, and stress loss. The block only starts to say something once the account holds several positions.
- The beta, sector and stress parts depend on C08 (factor model) and C12 (sizing), and neither exists yet.

Engagement does not support "high impact":
- The live map attributes low tapping to cards buried in operational traffic: 659 incident notices, 62 trailing-stop notices and 33 expiry notices against 41 signal cards, and 0 of 6 cards from the 14:35 slot tapped (maps/live.md finding 9, Q9/Q10).
- Not tapping cards whose record shows no edge may simply be the operator acting rationally.

Verdict:
- Fund now, at under a day's effort: the honest-labelling slice. Label each tier by the constraint that binds it, explain notional-capped risk (for example "risk $135 — capped by $7,500 notional"), drop "Max" when it equals Standard, and journal the binding constraint.
- Defer the full Allocation block until C08/C12 land and the book holds at least three positions.

I could not verify the "84% of setups" collapse figure in the maps; treat it as unsourced.

**Risks**

- **False precision.** MCTR, ρ and stress loss would come from a 120-session Ledoit-Wolf covariance on raw, unadjusted bars (docs/card-evidence.md:220-226), and beta-dollars from a factor model that does not exist yet. Precise-looking portfolio numbers on an edgeless signal can raise the operator's confidence without any basis.
- **Card length.** The card already stacks evidence, book, corporate-action, news, earnings, regime, tier and PEAD blocks. Adding five or more lines pushes toward Telegram's 4,096-character chunking (telegram_bot.py:873) and buries the levels.
- **Display drifting from enforcement.** If the block shows sector caps or stress losses that no rule in risk/ enforces, it implies controls that do not exist. This conflicts with the one-implementation-per-rule contract and with G8: the max tier the operator can tap is never vol-checked (maps/portfolio.md G8; book_sizing.py:339).
- **Effort is understated.** Journaling new provenance fields for cards outcomes means changes to the schema or provenance, the outcomes report and tests, which is more than "S".

**Prerequisites**

The full block needs:
- C08 (factor/beta/sector model) and C12 (sizing rework) merged;
- a book with enough positions for the before/after and ρ lines to be non-trivial;
- every displayed constraint backed by a rule in agentic_trader/risk;
- the G8 fix (vol-check every offered tier), so that "bound by" is true for the tier the operator taps.

The labelling slice needs none of these. It needs only the tier builder (position_sizing.py:207-225) to record which cap clamped the base and max tiers, plus the renderer (telegram_bot.py:322-345).

**Better alternative**

Split it.

1. **Now, under a day.** Make the tier builder emit a `binding` field per tier (risk budget / notional cap / remaining book notional / book_vol / probe cap) and render it in the tier line, e.g. "Standard: 75 sh, risk $135 (capped by $7,500 notional)". Drop "Max" when it equals Standard, and journal `binding` in provenance so cards outcomes can count how often the notional cap, rather than risk, sets size. This fixes the misleading $250-vs-$135 display at almost no cost.
2. **Higher leverage on engagement.** Take operational traffic out of the card channel: 659 incident notices, 62 trailing-stop notices and 33 expiry notices against 41 cards (maps/live.md finding 9). Route that traffic to a separate chat or a daily digest, so a card is the only thing that pings.
3. **Later.** Add the beta, sector, ρ and stress lines only after C08/C12 land and the book is populated, and only for constraints that are enforced.

---

<a id="c27"></a>

## C27 — No desk-level dollar replay exists: research verdicts are setup-weighted R while the desk sizes at near-equal notional

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | later (months) | research_methodology | research_direction | high | M | partially_confirmed |

_lenses: portfolio_

### Claim (as consolidated)

Verdicts are setup-weighted means in R (bracket counterfactual 'Statistics'; card_stats.py:112). R-weighting levers tight stops: R per 1% SPY move spans 0.25–1.13R across the 10th–90th percentiles.

The verdict therefore depends on the unit. In 2017–21, beta-one excess is −0.103R in R terms but +5.2 bp per setup in equal-notional return.

No replay runs the card stream through the live constraints:
- live sizing;
- the 1-per-scan and 2-per-session budgets;
- group caps and book rules;
- the 8-slot and $40k capacity.
L5 ('sizing protocol callable from replay') is marked Later (alpha-roadmap.md:496).

A first-order dollar check against same-notional SPY, per setup:

| Window | Equal-R | Live caps | Equal-notional |
|---|---|---|---|
| 2021–26 | −$27.3 | −$7.5 | −$7.3 |
| 2017–21 | −$27.8 | +$0.6 | +$3.9 |

A median of 6 setups per session compete for 2 cards.

### Evidence

- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (Statistics)
- agentic_trader/research/setups/card_stats.py:112; docs/alpha-roadmap.md:496
- agentic_trader/research/alpha/persistent_book.py:172-182 (ledger identity)
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_stop_quintile.py, pm_frames.py

### Recommendation

Run a predeclared, descriptive second use of both frozen frame sets. Replay the 10:35 and 14:35 instants under the live budgets and book rules, using the ledger identity in persistent_book.py:172-182.

Arms:
- sizing: live caps; equal-R uncapped; volatility-normalised k/σ; volatility-normalised with a beta cap;
- entry lifetime: GTC or one session;
- ratchet: on or off.

Outputs: dollar equity curve, Sharpe, max drawdown, net beta, capacity utilisation and refusal counts, each against SPY and against a beta-matched SPY sleeve. Include a 'core beta sleeve plus card overlay' design.

Choose the sizing primitive from these results, not from R statistics.

### North-star impact

Answers the operator's real question: does the card stream at desk caps beat simply holding the index, in dollars?

### References

- Boyd et al. (2017), Multi-period trading via convex optimization, Foundations and Trends in Optimization 3(1); cvxportfolio — Reference design for a policy simulator applying live constraints and costs (https://www.cvxportfolio.com)

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding holds. Research verdicts on the native card stream are setup-weighted means in R (card_stats.py:112; bracket counterfactual Statistics). R-weighting levers tight stops: 0.25–1.13R per 1% SPY move across p10–p90. The sign of excess over SPY depends on the unit: in 2017–21 it is −0.103R but +5.2 bp at equal notional. The finding's dollar table reproduces exactly from the frozen frames, and so does the median of 6 setups per session.

The title 'No desk-level dollar replay exists' overstates the gap. A legacy dollar portfolio backtest exists: `copilot backtest` (agentic_trader/backtest/engine.py). It runs the live StrategyEngine with live default-tier sizing ($7,500 notional cap), a $60k exposure cap and 4 concurrent slots. It reports a dollar equity curve, Sharpe, Sortino, max drawdown and Monte Carlo.

That engine does not reproduce the desk:
- It uses yfinance daily bars, not the 10:35 and 14:35 New York instants or the SIP caches.
- It takes candidates in symbol order until capacity is full. It has no ranking and no budgets of 1 card per scan, 2 per session or 1 per group per session.
- It does not apply the correlation-group or risk/ book rules, the 8-slot limit, the $40k equity class cap or book_sizing.
- It uses its own chandelier trail and has no SPY or beta-matched benchmark.

The setup study's top_k_selection (study.py:266) applies a 2-per-session selection, but only in R.

The accurate gap is narrower than the title: no replay runs the card stream through the live card budgets, book rules and capacity with live sizing and compares the result to SPY in dollars. The recommended replay should replace or absorb the legacy BacktestEngine rather than add a third path. Roadmap L5 already plans to retire that engine's private trail logic.

**Checked**

- agentic_trader/research/setups/card_stats.py:112: `mean_r_cost` is a plain setup-weighted mean in R. Supports the claim.
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (Statistics bullet, line 40) says 'Setup-weighted means', winsorised at ±10R. The robustness window gives A0 excess of −0.103R. Supports the claim.
- docs/alpha-roadmap.md:496: L5 'Later', 'sizing protocol callable from replay ... Retire the legacy backtest's private trail logic'. Supports the claim, and it also shows that a legacy backtest exists.
- agentic_trader/research/alpha/persistent_book.py:171-182: the self-financing identity check `new_equity - equity == price_pnl - fee - borrow - funding`. Supports the claim.
- config/config.yaml:6 max_concurrent_positions 8; :7 max_correlated_positions 2; :343 max_trade_notional_cap 7500; :443-445 max_cards_per_scan 1, max_cards_per_session 2, max_cards_per_group_per_session 1.
- agentic_trader/config.py:393: max_equity_exposure 40000 is the equity class cap. It is used at agentic_trader/risk/limits.py:89 and is not overridden in config.yaml. The '$40k capacity' figure is correct.
- agentic_trader/agent/position_sizing.py:96: trade_notional_ceiling = min(max_trade_notional_cap, remaining_notional). Combined with the frames (base tier notional-capped on 83.9% of primary and 85.4% of robustness setups; median base notional $7,500), this supports 'near-equal notional'.
- I re-ran the reviewer's pm_stop_quintile.py, pm_frames_w.py and pm_frames.py read-only on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/{primary,robustness}.csv.gz. Primary per-setup excess is −27.29 / −7.45 / −7.32 dollars. Robustness is −27.81 / +0.61 / +3.91. Beta-one excess for 2017–21 is +5.2 bp. R per 1% SPY move at p10–p90 is 0.25–1.13 (primary; robustness 0.26–1.23). The median is 6 setups per session in both windows. Every number in the finding reproduces.
- agentic_trader/backtest/engine.py:37-562 (`copilot backtest`, agentic_trader/cli/commands/backtest.py:139, Telegram /backtest) is counter-evidence. It is a portfolio-level dollar replay. It runs the live StrategyEngine and sizes through RiskEvaluator.calculate_levels_deterministic → calculate_dynamic_sizing (evaluator.py:166, :253), so it uses the default tier with the $7,500 cap. It caps exposure at $60k (constants.py:142) and positions at 4 (engine.py:49). It reports a dollar equity curve, Sharpe, Sortino, max drawdown and Monte Carlo.
- That engine still differs from live. It uses yfinance daily bars (hourly=daily, engine.py:353-358), so it does not decide at the 10:35/14:35 instants. It enters candidates in symbol order until capacity runs out (engine.py:333-338), with no ranking, no per-scan, per-session or per-group card budget, and no risk/ package rules. It uses its own chandelier trail (engine.py:283-330) and has no SPY benchmark.
- agentic_trader/research/setups/study.py:266 top_k_selection applies a top-k-per-session selection, but only in R and with no capacity or dollar sizing.
- grep found no other research module (research/setups outcomes, replay, bracket_counterfactual) that computes dollars, notional or an equity curve for the card stream.
- Web check: Boyd, Busseti, Diamond, Kahn, Koh, Nystrup, Speth, 'Multi-Period Trading via Convex Optimization', Foundations and Trends in Optimization 3(1):1-76, 2017 (DOI 10.1561/2400000023). cvxportfolio.com cites it and provides a MarketSimulator with constraints and cost models. The reference is valid.

**Bad references**

_none_

**Notes**

I reproduced every quantitative claim from the frozen bracket-counterfactual frames using the reviewer's scripts, run read-only. The 'Live caps' column applies only the per-trade $250 risk and $7,500 notional cap of the base tier. It does not include the book_sizing factor, the drawdown haircut, the budgets or capacity, which is consistent with its 'first-order' label.

The main flaw is a misdescription by omission: the finding ignores the existing legacy `copilot backtest` dollar engine, which the roadmap line it cites (alpha-roadmap.md:496) mentions. The recommendation stays valid. The Boyd et al. 2017 / cvxportfolio reference checks out.

### Value review — P2, later (months)

**Reasoning**

The diagnosis is right. The full replay as specified is poor value right now.

What is true in the code and docs:
- Verdicts are setup-weighted R. This covers `mean_r_cost` (agentic_trader/research/setups/card_stats.py:112) and the bracket counterfactual (docs/bracket-counterfactual-2026-10-18.md, 'Protocol and run', Statistics).
- The desk does not size in R. With $250 risk and a $7,500 notional cap (config/config.yaml:337, :343), every stop tighter than 3.33% is notional-capped. That makes live sizing roughly equal-notional, and the probe confirms it: live caps give −$7.5 and equal-notional −$7.3 per setup in 2021–26.
- So R-unit research does not describe desk dollars, and L5 is still marked 'Later' (docs/alpha-roadmap.md:496).

Why the full 16-arm replay does not earn M effort now:
1. **The dollar check already answers the operator's question, and the answer is no.** In 2021–26 the shortfall is −$7.5 per setup. That is about −10 bp of a $7.5k notional per ~5.6-session hold (A0_live hold, bracket doc Descriptives). In 2017–21 it is roughly zero. The R unit exaggerates how big the shortfall is; it does not change its sign. Path dependence and caps will not flip it, for two reasons. First, the selection key is about as good as random (setup_quality, docs/alpha-roadmap.md:513), so the 1-per-scan and 2-per-session budgets (config/config.yaml:443-445) pick a near-random subsample. Second, the 8-slot and $40k caps change trade count and variance, not the expected excess.
2. **Choosing a sizing primitive for a stream with no alpha only reshapes beta and variance.** It cannot produce better cards, which is the north star.
3. **The desk-versus-SPY curve is an absolute comparison on a survivorship-flattered universe.** The bracket doc's Caveats say this bias cancels only in paired contrasts. Any 'cards ≈ index' result from this replay would therefore be optimistic.
4. **The arm grid repeats closed questions.**
   - The ratchet was already tested: A2 is worse by 0.013R with a CI entirely below zero (bracket doc, Rules table).
   - The entry-lifetime arm runs into the semantics-v5 fixed lifetime (DAILY_ENTRY_LIFETIME_SECONDS = 57,600, execution/lifetime_policy.py:24).
   - 4×2×2 arms on frames already used for a verdict, with the instruction to 'choose the sizing primitive from these results', is post-hoc selection. The only clean data left is the unused confirmation window (2026-03-02 → 2026-08-14), and this question should not spend it.
5. **The replay cannot model what matters live.** It leaves out the LLM veto, which cards the operator actually taps, and which tier they pick.

Where the finding is valuable:
- **The unit mismatch already reaches live-facing numbers.** card_stats EV is shown on cards, and `card_policy` withholds on measured R. Both should also be stated in dollars at live sizing, in bp of notional, and against a beta-matched SPY. That is a small change.
- **L5 should exist before any entry-side lane's finalist is deployed.** A deployment decision should be judged at desk level under live constraints, not in setup-weighted R. Build the replay then, with a predeclared rule, using the native frames as the regression input.

**Risks**

- **Fishing.** A 16-arm grid on frames already examined, with the primitive picked post hoc. Pressure would follow to 'confirm' it on the one remaining confirmation window.
- **Survivorship bias.** The 159-name current universe inflates the absolute dollar curve and Sharpe against SPY, so the replay could make the cards look close to the index.
- **Misleading risk-adjusted framing.** A ~$40k long book on $100k has a beta of roughly 0.4–0.5. It will show a smaller drawdown than 100% SPY and look 'better risk-adjusted' unless the beta-matched sleeve is treated as the main comparison.
- **Replay versus live divergence.** No LLM veto, no operator tap or tier choice, and hourly-close approximations of the stop and ratchet (bracket doc, Caveats).
- **Opportunity cost.** M effort and operator attention go to sizing a stream with no edge, while entry-side work is still unfunded (insider campaigns pending).

**Prerequisites**

Applies to the full replay, if or when it is funded:
- **A reason to run it:** either an entry-side lane (e.g. insider-v1 or cohort3-insider-v1) produces a finalist, or the operator asks whether the native card stream should be kept, reframed as a beta-sleeve overlay, or withheld.
- **One bracket-label kernel and a sizing protocol callable from replay** (L5, docs/alpha-roadmap.md:496), so replay uses live `calculate_dynamic_sizing` and the shared risk/ rules instead of a third reimplementation.
- **A predeclared protocol with a single primary comparison:** desk dollar excess against a beta-matched SPY sleeve, with sizing arms limited to live caps versus vol-normalised with a beta cap. Ratchet and lifetime arms should be dropped as already closed.
- **A survivorship-free universe,** or an explicit statement that the absolute curve is an upper bound.
- **The ledger identity** from research/alpha/persistent_book.py:172-182, reused rather than rebuilt.
- **Caps fixed first.** The $40k equity cap versus 8×$7.5k inconsistency should be resolved before measuring capacity utilisation, or the refusal counts only describe a config bug.

**Better alternative**

Fund the cheap, unit-honest part now (about a day, P1), then build L5 when there is a candidate worth deploying.

1. Add descriptive columns to `cards outcomes`, card_stats and the bracket-counterfactual report: excess in bp of notional, dollars at live sizing (`min($250, $7,500·stop%)`), and excess against a beta-matched SPY. Base them on the existing probe's arithmetic (scratchpad scripts/pm_stop_quintile.py). Then record in docs/card-evidence.md, with these dollar figures, that the R verdict holds in sign for 2021–26 and is about zero for 2017–21.
2. Make `card_policy` and the base rate shown on cards state their unit, or also compute in notional bp, so the R-weighting of tight stops cannot drive withholding.
3. Treat 'core beta sleeve plus card overlay' as an operator product decision supported by the existing evidence. Cards show −0.10R excess in both windows, and the dollar check is negative or flat. A replay is not needed to frame that choice.
4. Build the desk replay as L5 only when an entry-side finalist exists. Predeclare it, keep one primary comparison, and use the confirmation window only for that candidate.

---

<a id="c28"></a>

## C28 — Native cards trade an execution policy that research measured as adverse: resting GTC entries, a value-destroying R-ratchet and no time exit

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | execution | defect | medium | S | partially_confirmed |

_lenses: product, portfolio, architecture_

### Claim (as consolidated)

Entries:
- The native tap builds an OrderRequest with no time_in_force, so the GTC default applies (copilot.py:2967-2978; broker/base.py:98).
- Only alpha_policy lifetimes cancel entries (lifetimes.py:209-213). Unfilled native entries therefore rest indefinitely and hold reservations, including EXECUTED rows, in an 8-slot, $2,000 stop-risk book.
- GTC limit entries were measured to fill mainly when the forecast is wrong.

Trailing stop:
- trailing_stop is enabled. Once price reaches +1.5R it trails at current − max(R, 1.5R), priced from fetch_latest_price with no feed pin or age check (copilot.py:2553-2571).
- The configured mode 'chandelier_atr' is never read.
- The predeclared counterfactual measured the ratchet at −0.013R per setup (90% CI [−0.020, −0.006]) over 8,768 setups.
- Its risk benefit is never credited. A confirmed ratchet updates only the stop, so risk_dollars and aggregate_stop_risk keep charging the original risk (risk/book.py:95; rules.py:270-277).

Labels versus live trades: labels assume an entry that always fills at the next hour and a 20-session timeout. Live trades have neither.

Telegram carried 62 ratchet notices (29 for XOM) against 41 cards.

### Evidence

- agentic_trader/agent/copilot.py:2553-2571, 2622, 2967-2978; agentic_trader/broker/base.py:98
- agentic_trader/execution/lifetimes.py:209-213; storage/workflow.py:262-269
- config/config.yaml:419-425 (trailing enabled; mode unused)
- docs/bracket-counterfactual-2026-10-18.md 'Rules' and 'What this says' item 2 (−0.013R)
- docs/alpha-roadmap.md:148-152 (GTC fills when the forecast is wrong); docs/alpha-lifetime-attribution-2026-09-21.md
- agentic_trader/research/setups/labels.py:176-189; agentic_trader/risk/book.py:95; risk/rules.py:270-277
- Live map Q9 (62 trailing-stop notices, 29 for XOM)

### Recommendation

Make one operator-approved construction change that cites the existing evidence:
1. Set time_in_force=DAY, or daily_entry_lifetime() (57,600 s), on native cards.
2. Disable trailing_stop for native cards, and delete the misdescribing mode and ATR keys.
3. Add a 20-session time exit through SessionLifetimePolicy, matching the label.
4. Add an operator 'cancel resting entry' command that reuses entry_cancel.

If any trail is kept, rebuild it in execution/ as a policy shared with replay (BracketArm/TrailSpec). It should re-price planned risk after each confirmed move and use a fresh, feed-pinned quote. Keep the contract of one notice per confirmed change.

Add the label R of filled versus unfilled sent cards to cards outcomes.

### North-star impact

Recovers about +0.013R on affected setups, avoids adverse fills and frees capacity. The base rate on the card then describes the trade the operator actually gets, and dozens of ratchet notices leave Telegram.

### References

- Handa & Schwartz (1996), Limit Order Trading, JF 51(5) — Resting limit orders are adversely selected
- Kaminski & Lo (2014), When do stop-loss rules stop losses?, J. Financial Markets 18 — Trail rules add value only under return persistence

### Fact check — partially_confirmed

**Corrected claim**

The core code facts hold.

Native cards:
- They submit GTC limit brackets (OrderRequest default, broker/base.py:98).
- Only alpha_policy.lifetime signals are ever cancelled (lifetimes.py:209-213), and native screeners set no alpha_policy.
- There is no native time exit and no targeted operator cancel for a resting entry.
- A working, unfilled entry occupies a reservation slot and stop-risk capacity (workflow.py:262-269; capacity.py:131-161).

Trailing:
- The live trail triggers at +1.5R, trails at current - 1.5R, and is priced from an unpinned, unaged latest trade or bar with a possible yfinance fallback (providers.py:348-378, 531-546).
- trailing_stop.mode and the 'ATR' naming are unused or misdescriptive.
- A confirmed ratchet updates only stop_loss (db.py:958-988).

Labels: they fill at the next regular hourly open with a 20-session timeout (labels.py:176-189).

Corrections:
1. **Risk crediting is path-dependent.** The scan book (evaluator.py:497) charges the original risk_dollars. The broker-marked admission book for Alpaca charges max(original risk_dollars, qty x (mark - current broker stop)) (capacity.py:194-209). A ratchet therefore reduces the mark-to-stop charge relative to an un-ratcheted position, but the charge never falls below the original R.
2. **The -0.013R is a mean over all 8,768 setups.** The ratchet changes only 467 of them, so the cost per affected setup is about -0.24R, not '+0.013R on affected setups'. The doc frames this as a hypothesis: B1 tested for an advantage, not a cost, and a 'ratchet off' change would need its own protocol (docs/bracket-counterfactual-2026-10-18.md 'What this says' item 2).
3. **The adverse-selection evidence was not measured on native cards.** It comes from a synthetic study of mined alpha signals with the entry at signal-bar close (docs/alpha-lifetime-attribution-2026-09-21.md 'Mechanism'). Native entries use the observed price: a fresh tap is limited to 0.25R and 30 minutes, and a staler tap is re-priced to the current price (freshness.py:196-212). In the live record, the accepted native entries filled. docs/alpha-trade-lifetimes.md:165-167 already documents native GTC as a known limit.
4. **'Indefinitely' should be 'up to 90 days'.** Alpaca auto-cancels GTC orders after 90 days.
5. **Recommendation 1's time_in_force=DAY option conflicts with the repo's broker contract.** docs/alpha-trade-lifetimes.md:25-27 says to keep GTC protection and that a DAY bracket is not a substitute. Only the persisted entry_cancel lifetime route (daily_entry_lifetime) is consistent with that contract.

**Checked**

- agentic_trader/agent/copilot.py:2967-2978: the native tap builds an OrderRequest with no time_in_force. Confirmed.
- agentic_trader/broker/base.py:98: time_in_force defaults to GTC. No override in execution/, storage/ or screeners/. alpaca.py:197 passes request.time_in_force through. Confirmed.
- agentic_trader/execution/lifetimes.py:209-213: reconcile() skips any signal without alpha_policy.lifetime. Native screeners leave alpha_policy=None (screeners/base.py:44); only formulaic.py:156 and earnings_drift.py:212 set it. Confirmed.
- No targeted command cancels a resting entry. /close and /flatten act on broker positions, and closing.py:210-211 says 'unfilled entry orders in other symbols remain working'. /panic cancels everything and halts. Confirmed.
- storage/workflow.py:262-269: reservations() covers SUBMITTING and EXECUTED rows. capacity.py:131-161 charges working, unfilled entry roots as exposure. Confirmed.
- config/config.yaml:3-6: max_stop_risk_pct 0.02 and max_concurrent_positions 8, so about $2,000 on $100k. Confirmed.
- copilot.py:2528-2571: trailing applies to native cards because policy_trails(None) is True (research/alpha/strategy.py:112-114). With trail_trigger_r 1.5 and trail_atr_multiple 1.5, the stop goes to current - 1.5R. Confirmed.
- config/config.yaml:419-425 and config.py:669-678: `mode` is never read by the live code. The only 'chandelier_atr' uses are the backtest CLI (cli/commands/backtest.py:77-133) and constants. trail_atr_multiple is used as a multiple of R, not ATR. Confirmed.
- data/providers.py:348-378 and 531-546: StockLatestTradeRequest/StockLatestBarRequest are built without feed=self.feed, there is no timestamp or age check, and the chain can fall back to yfinance. Confirmed.
- storage/db.py:958-988: update_position_stop writes only stop_loss; risk_dollars is unchanged. Confirmed.
- risk/book.py:71,95-96 and rules.py:270-277: the generic book charges stored risk_dollars. The scan book is built from DB rows (evaluator.py:497). Confirmed for the scan path.
- execution/capacity.py:194-209: the admission book for Alpaca is broker-marked and charges max(original risk_dollars, qty x (mark - current broker stop)). This partly contradicts 'keep charging the original risk'.
- research/setups/labels.py:176-189: the entry is the open of the first regular hourly bar at or after the decision, so it always fills; max_hold_sessions is the timeout. Live native trades have no holding exit (grep for max_hold/holding_seconds outside research). Confirmed.
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run', 'Rules' (A2 -0.013, CI [-0.020, -0.006], 8,768 setups), 'What this says' item 2. Numbers confirmed; the doc also says A2 differs from A0 on only 467 setups and calls the result a hypothesis that needs its own protocol.
- docs/alpha-roadmap.md:148-152 quote confirmed. docs/alpha-lifetime-attribution-2026-09-21.md 'Mechanism': the GTC adverse-selection result comes from synthetic planted and null series for mined alpha signals with the entry at signal-bar close.
- docs/alpha-trade-lifetimes.md:25-27 (Keep GTC protection; 'A DAY bracket is not a substitute') and :165-167 (built-in strategies still submit GTC entries at the observed price, a documented known limit).
- execution/freshness.py:196-212 and config.yaml:364-365: a fresh tap (30 min, 0.25R) keeps the original limit; otherwise the order is re-priced at the current price.
- scratchpad holistic-review/maps/live.md gap 9 and item 6: 62 trailing-stop notices, 29 for XOM, 41 cards. XOM still closed at target, +2.37R.
- Alpaca orders docs: GTC orders are auto-cancelled 90 days after creation. Bracket TIF must be day or gtc.
- References checked: Handa & Schwartz, JF 51(5):1835-1861 (1996); Kaminski & Lo, J. Financial Markets 18:234-254 (2014).

**Bad references**

_none_

**Notes**

Every file:line citation exists and supports the mechanics described. The finding goes wrong in three places: how much of the ratchet's risk reduction is credited at admission, how strongly the doc's evidence is stated, and the impact arithmetic.

- **Admission credit.** capacity.py:209 takes the max of the original risk and mark-to-stop. This is never below the original R, but it does fall after a ratchet compared with no ratchet.
- **Ratchet cost.** The -0.013R averages over all setups (467 of 8,768 are affected), and the doc calls it a hypothesis.
- **GTC evidence.** It is synthetic, from mined-alpha signals, and is being extrapolated to native cards.

Two further points:
- The recommendation's DAY option contradicts docs/alpha-trade-lifetimes.md:25-27. Alpaca's docs do not confirm whether bracket legs inherit DAY, but the repo's own contract rejects it.
- The 62 and 29 notice counts come from the review's live map (DB queries), not from code. The XOM position with 29 notices still hit its target at +2.37R.

Both references exist with the cited venues: Handa & Schwartz, JF 51(5):1835-61, 1996; Kaminski & Lo, JFM 18:234-254, 2014. Both broadly support their uses. Kaminski & Lo treats 0/1 stop-loss rules rather than trailing stops specifically, which is a mild stretch but not a bad reference.

Sources: https://ideas.repec.org/a/bla/jfinan/v51y1996i5p1835-61.html, https://dspace.mit.edu/handle/1721.1/114876, https://docs.alpaca.markets/docs/orders-at-alpaca

### Value review — P2, next (weeks)

**Reasoning**

The diagnosis is accurate, but the claimed P&L benefit is overstated. A narrow slice is worth funding as cheap hygiene. It does not add edge.

**What the code does now (verified):**
- The native tap builds an `OrderRequest` with no time-in-force, so the GTC default applies (agentic_trader/agent/copilot.py:2967-2978; agentic_trader/broker/base.py:98).
- `TradeLifetimeService` reconciles only signals whose `alpha_policy` carries a `lifetime` (agentic_trader/execution/lifetimes.py:209-213).
- The ratchet trails at current − max(R, 1.5R) from an unpinned `fetch_latest_price` (copilot.py:2553-2571). `trailing_stop.mode: chandelier_atr` is never read (config/config.yaml:419-425).
- One global `enabled` flag gates the whole ratchet (copilot.py:2530-2532).
- `aggregate_stop_risk` sums `planned_risk` (agentic_trader/risk/book.py:95-96; agentic_trader/risk/rules.py:270-277).

**Why the leverage is small:**

1. **The ratchet effect is about 0.01R and changes sign across windows.**
   - The −0.013R per setup (90% CI [−0.020, −0.006]) comes from only 467 of 8,768 setups where the arm differs (docs/bracket-counterfactual-2026-10-18.md, Rules table and "What this says" item 2).
   - On the 2017–2021 robustness window the same arm is +0.005R (same table, B3 column).
   - The doc itself calls "ratchet off" a hypothesis: B1 tested for an advantage, not a cost.
   - The real problem is that every arm trails SPY by 0.10–0.15R ("What this says" item 3). That is an entry problem the ratchet cannot fix.
   - So "value-destroying" is too strong. This is neutral-to-slightly-negative complexity.

2. **The GTC adverse-selection evidence comes from a different population.**
   - It was measured on mined ETF32 alphas with multi-session GTC limits (docs/alpha-roadmap.md:148-152; docs/alpha-lifetime-attribution-2026-09-21.md).
   - Native cards pass the tap freshness gate, which classifies each tap as EXECUTE, REPRICE, MISSED or EXPIRED. They are submitted only when the price is still near the entry.
   - The live record has 9 taps, 3 accepted entries and closed trades of SPY, XOM and VRNS (maps/live.md items 5-6). There is no observed population of long-resting unfilled native entries, and capacity (8 slots) is not binding.

3. **The over-charged risk after a ratchet is conservative and does not bind** at roughly one fill a week.

**Why still fund a slice:**
- The best argument is product, not P&L. Telegram carried 62 ratchet notices (29 for XOM, which hit its target anyway) against 41 cards, while the operator tapped only 9 cards (maps/live.md item 9).
- Turning the ratchet off is a one-line, documented, operator-approved config change. It removes that noise at the source and stays compatible with contract 3 (a notice for every confirmed change, since there would be none).
- Bounding native entries to one session makes the card's measured base rate describe the trade the operator actually gets.

**Fund:**
- (a) `trailing_stop.enabled: false`, citing the counterfactual, and delete the unused `mode` key.
- (b) Attach `daily_entry_lifetime()` (57,600 s resting, no holding deadline) to native signals through a dedicated field, not a fake `alpha_policy`, so `TradeLifetimeService` cancels unfilled entries through the existing `entry_cancel` work item.
- (c) In `cards outcomes`, add label R for filled versus unfilled sent cards. This is the only part that measures whether conditional-fill bias exists on native cards.
- Bundle these into the next execution or card PR (no standalone PR).

**Drop:**
- **`time_in_force=DAY` as written.** The repo's broker contract says "Keep GTC protection … A DAY bracket is not a substitute for independently expiring the entry while preserving protection" (docs/alpha-trade-lifetimes.md:24-26). DAY bracket legs risk leaving a filled position unprotected overnight.
- **The 20-session holding exit.** Timeouts are rare under a 2R bracket (about 5% even on the 3R arm, docs/bracket-counterfactual-2026-10-18.md primary-window table). XOM resolved at its target in 13 sessions. It adds close-path surface for parity only.
- **Rebuilding the trail as a shared BracketArm/TrailSpec policy.** Do not invest in a component measured as neutral-to-negative.
- **The new cancel command.** It is low value at about one fill a week; `/panic` and expiry cover it for now.

**Risks**

1. **Unprotected positions.** Taking the DAY-TIF route literally would put an Alpaca bracket on DAY legs and could leave filled positions unprotected after the close. This violates the documented broker boundary (docs/alpha-trade-lifetimes.md:24-26). Use only the service-side lifetime, which keeps GTC protection.

2. **Side effects of reusing `alpha_policy`.** Attaching a lifetime to native cards by reusing `alpha_policy` would have side effects:
   - The trailing branch takes `trailing_price` whenever `alpha_policy` is set (copilot.py:2574-2578).
   - `policy_trails` and alpha/probe exemptions key off that document.
   - Native cards could silently change ratchet math or `card_policy` treatment.

   This needs a distinct native lifetime field and tests that `card_policy` and exemption behaviour is unchanged.

3. **Over-reading the evidence.** Calling ratchet-off "recovered +0.013R" claims a confirmed cost, which the frozen rules did not test. The 2017–2021 window shows +0.005R. Record it as a simplification or operator construction choice, not a measured gain.

4. **Global flag scope.** `trailing_stop.enabled` is global (copilot.py:2530-2532). It would also stop trailing for any future active alpha whose policy trails. That is harmless today with 0 active alphas, but needs a per-policy switch before any alpha is activated.

5. **Paper fills overstate the gain.** The one-session entry bound changes fill rate. The simulator and research fill rates are upper bounds (docs/alpha-trade-lifetimes.md "Known limits"), so paper evidence may overstate the improvement.

6. **No outcome data.** With roughly 3 fills a month, the filled-versus-unfilled split will take months to say anything.

**Prerequisites**

1. An operator decision recording ratchet-off as a construction change, citing docs/bracket-counterfactual-2026-10-18.md and stating that B1 tested advantage, not cost.
2. Check the actual live count and age of accepted-but-unfilled native entries (`db queue`/`db orders`). This was not run (maps/execution.md open question). If there are none, the lifetime part is preventive only.
3. A design note for the native lifetime field that keeps it apart from `alpha_policy`, with tests on the card_policy and exemption paths.
4. The `cards outcomes` filled/unfilled split should land in the same PR, so the change is measured.
5. Follow the deployment playbook: pause the watchdog and daemon before updating the installed checkout, then verify runtime.

**Better alternative**

**Cheapest version:** flip `trailing_stop.enabled: false` in config/config.yaml as a documented operator construction choice, delete the unused `mode`/ATR keys, and add filled-versus-unfilled label R to `cards outcomes`. This removes about 60 notices a month and measures whether native conditional-fill bias exists at all, before building native entry lifetimes. The ratchet is neutral-to-slightly-negative and its sign flips across windows, so there is nothing worth rebuilding. Add `daily_entry_lifetime()` through `TradeLifetimeService` only if the measurement or `db orders` shows resting unfilled native entries. Never use `time_in_force=DAY`.

**Higher-leverage use of the same effort:** the operator-attention problem. Cards are buried in operational traffic and only 9 of 42 were tapped, so separate or digest the operational Telegram stream (maps/live.md recommendation 8). The entry-side edge work (cards trail SPY by 0.10–0.15R) is what actually moves the north star.

---

<a id="c29"></a>

## C29 — The per-symbol DSL lane cannot promote anything: its holdout DSR double-counts the lifetime trial family, and its 16 shadow registrations are permanently invalid. Fix it in validation policy v2 or retire it explicitly

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | now (days) | research_methodology | defect | medium | M | partially_confirmed |

_lenses: quant, ml_

### Claim (as consolidated)

_family_dsr uses the global family/all trial_count (promotion.py:291-302; storage/alpha.py:239-251). It applies this both on validation and on the one-shot holdout of a single pre-frozen candidate (promotion.py:353, 370). DSR corrects for selection among N trials, so applying it to a single frozen candidate counts selection twice.

Results of the double count:
- 0 of 64 planted signals accepted.
- About 4.9 annualised Sharpe needed on a 252-bar holdout against 7,065 trials.
- Every new research charge raises the bar further.
- The research-only comparator recovered 12–28 of the 64.

Qualification also requires single-symbol, raw-bar, 30-bar z definitions (promotion.py:88, 474-480).

Live, all 10,359 shadow forecasts from the 16 registrations are invalid: 6,915 contract mismatch and 3,444 stale_closed_bar. Yet AlphaShadowService.observe still runs per contract in every scan (copilot.py:1062-1065), and the combine_forecasts output is never read.

### Evidence

- agentic_trader/research/alpha/promotion.py:88, 291-302, 351-366, 370-388, 474-480
- agentic_trader/storage/alpha.py:236-251; research/alpha/clock.py:47-50
- agentic_trader/agent/copilot.py:1062-1065
- docs/alpha-power-diagnosis-2026-09-18.md (0/64); docs/alpha-stack-survey-2026-09-18.md (comparator 12–28/64); docs/alpha-study-protocol.md (7,065 trials)
- Live map Q13 (alpha_projections validity)

### Recommendation

This needs an operator decision between two options.

(a) Validation policy v2, as a new version id:
- Control family-wise selection on validation only: DSR at an effective trial count from return-correlation clustering, or Hansen SPA / Romano-Wolf via arch.
- Test the frozen finalist's holdout with a one-sided block bootstrap or HAC, with Holm across the holdouts in a campaign.
- Report PBO as a diagnostic.
- Use the planted-power run as v2's acceptance test.

(b) Retirement:
- Declare that per-symbol z-threshold definitions are no longer a promotion path.
- Demote the 16 registrations and stop calling observe in scans.
- Tag and remove the miner, simulation and forecast-combination harnesses.
- Keep the DSL compiler, registry CAS, probe lane and admission fencing.
- Freeze family/all so it does not deflate new lanes.

Either way, record that 0/64 is not evidence about alpha.

### North-star impact

Either reopens a route to cards under honest error control, or closes it explicitly and removes scan-time and journal noise.

### References

- Bailey & López de Prado (2014), The Deflated Sharpe Ratio, JPM 40(5) — DSR deflates the selected maximum of N trials, not a one-shot test of one frozen strategy
- Romano & Wolf (2005), Stepwise Multiple Testing as Formalized Data Snooping, Econometrica 73(4) — Family-wise selection control on validation
- Hansen (2005), A Test for Superior Predictive Ability, JBES 23(4) — SPA test as a selection-stage alternative

### Fact check — partially_confirmed

**Corrected claim**

Confirmed core: the per-symbol DSL promotion path applies one deflation to both stages. Both the validation DSR and the one-shot holdout DSR of a single pre-frozen finalist use the lifetime family/all trial_count (promotion.py:291-302, 353, 370; storage/alpha.py:240, 250). On the holdout this deflates for N selections that already happened on validation. At the frozen 7,065-trial / 0.00288 variance reference, a 252-bar holdout needs about 4.9 annualised Sharpe to reach DSR 0.95 (reproduced with metrics.py:72-131 and validation.py:30). Three further points are confirmed:
- alpha_shadow.observe runs for every non-dry, non-dynamic contract in every scan (copilot.py:1062-1065).
- The 16 shadow registrations are documented (docs/alpha-panel-forecasts-2026-09-17.md:113).
- The code makes unverified-feed imports structurally invalid. data_feed defaults to "unverified" (models.py:65) and observe uses require_verified=True (shadow.py:43), so every observation raises deployment_data_contract_mismatch (clock.py:76-79).

Corrections:
(1) The 0/64 is not "a result of the double count". In the timeline study (docs/alpha-timeline-study-2026-09-16.md, Planted effects), the local 16-trial family also accepted only 0-4/64. The 12-28/64 comparator dropped the discovery/family DSR, the holdout DSR and the IC gate together, and it still failed its own power requirement. In the 800-search diagnosis (docs/alpha-power-diagnosis-2026-09-18.md, "Where the signal is lost" and "Family sensitivity"):
   - GTC entries stay pending about 90% of bars.
   - The ten-trade holdout minimum fails 64/64 dense known-route runs.
   - Dropping any single criterion yields no passes.
   - The diagnosis states that "removing lifetime history would not solve the observed execution bottleneck".
   docs/alpha-study-2026-09-16.md:120-131 says the rejection reasons overlap and are not causal attributions. The holdout DSR is one of several joint blockers, not the cause.
(2) Qualification does not require "30-bar z" definitions. normalization_window is configurable in 2-252 (models.py:62, 77-78) and only defaults to 30 (strategy.py:26). The actual requirements are:
   - a single eligible symbol (promotion.py:88);
   - no clock (85-86);
   - an alpaca:iex/sip raw feed that matches the definition (474-481).
(3) "Every new research charge raises the bar" is only partly true:
   - Only reserve_run, record_run and exclude_observed_interval with trials>0 charge family/all.
   - The prospective observers charge 0 trials.
   - The pooled and single-protocol lanes never touch family/all (storage/alpha.py:37, 1183-1186).
   So the recommendation to "freeze family/all so it does not deflate new lanes" is already effectively true for the active lanes.
(4) combine_forecasts is not merely unread; in practice it is unreachable. It runs only for valid observations that carry a qualification calibration (shadow.py:89-92), and no shadow registration has one. If it ran, its output would be journaled as valid=False "combined_shadow_forecast" (shadow.py:144-153), which nothing reads.
(5) The live counts (10,359 / 6,915 / 3,444) cannot be verified from code or docs.

There is also an additional blocker the finding omits. The daemon observers consume per-symbol holdout intervals (observation.py:162, daily_observations.py:220 into storage/alpha.py:977-1003), and begin_holdout refuses any overlap (storage/alpha.py:234). Fresh holdouts for observed symbols are therefore already consumed.

**Checked**

- agentic_trader/research/alpha/promotion.py:70-96 (qualify: one version_id; clock must be None at 85-86; single eligible symbol at 88; begin_holdout consumes before computation at 90-91)
- agentic_trader/research/alpha/promotion.py:291-302 (_family_dsr passes family trial_count/trial_variance to DSR)
- agentic_trader/research/alpha/promotion.py:351-366 (family_adjusted_validation_dsr) and 368-372 (holdout dsr = _family_dsr(holdout summary, family)); 388 holdout_dsr gate vs min_dsr
- agentic_trader/research/alpha/promotion.py:470-497 (assess_qualification: alpaca:iex/sip + raw adjustment matching the definition)
- agentic_trader/research/alpha/metrics.py:72-131 (Bailey/LdP DSR: expected max SR from N trials and variance)
- agentic_trader/research/alpha/validation.py:20-32 (min_dsr 0.95, min_sharpe 1.0, minimum_trades 10)
- Computed with the repo DSR formula: N=7,065, V=0.0028764548818829777, T=252, normal moments -> SR0 = 3.21 annualised, required holdout SR = 4.90 annualised. Confirms the ~4.9 claim
- agentic_trader/storage/alpha.py:158-201 (record_run charges family/all and the per-timeframe variance family), 203-256 (begin_holdout: per-symbol holdout key, overlap refusal at 234, trial_count from family/all at 240/250, variance None when <=1 sample)
- agentic_trader/storage/alpha.py:37 and 1183-1186 (pooled and single-protocol lanes never touch family/all); 977-1006 exclude_observed_interval and reserve_run charge family/all
- Callers of exclude_observed_interval: observation.py:162, daily_observations.py:220, decisions.py:182 all charge trials=0 but consume per-symbol holdout intervals
- agentic_trader/research/alpha/models.py:50-82 (data_feed default 'unverified'; normalization_window configurable 2-252); strategy.py:26 (NORMALIZATION_WINDOW = 30 default)
- agentic_trader/research/alpha/clock.py:36-80 (stale_closed_bar at 50; deployment_data_contract_mismatch at 76-79 when require_verified)
- agentic_trader/research/alpha/shadow.py:25-158 (observe_definition uses require_verified=True; combine only for valid observations with a qualification calibration; combined output journaled valid=False 'combined_shadow_forecast')
- grep: no reader of 'combined_shadow_forecast' anywhere; cli/commands/alpha.py:678-705 alpha portfolio builds CombinedForecast from an explicit JSON file
- agentic_trader/agent/copilot.py:757 and 1062-1065 (alpha_shadow.observe per non-dry, non-dynamic contract in every scan)
- docs/alpha-power-diagnosis-2026-09-18.md (Full-policy acceptance; Where the signal is lost; Family sensitivity and provenance)
- docs/alpha-timeline-study-2026-09-16.md:59-75 (Planted effects table: lifetime 0/64, local 0-4/64, alternative 12-28/64; the alternative drops discovery/family DSR and the IC gate)
- docs/alpha-stack-survey-2026-09-18.md:96-101
- docs/alpha-study-protocol.md:70-73 (7049 + 16 = 7,065 trials)
- docs/alpha-study-2026-09-16.md:120-131 (reasons overlap, not causal attributions; retain gates)
- docs/alpha-panel-forecasts-2026-09-17.md:113 and docs/alpha-forecast-controls-2026-09-18.md:18 (0 active / 16 shadow)
- WebSearch: Bailey & López de Prado 2014 JPM 40(5) 94-107; Romano & Wolf 2005 Econometrica 73(4) 1237-1282; Hansen 2005 JBES 23(4) 365-380. All three exist and are cited correctly

**Bad references**

_none_

**Notes**

The methodological core holds and is a fair expert critique, though the project's docs never frame it as double counting. Every code citation exists and supports its use: promotion.py:88, 291-302, 351-366, 370-388 and 474-480; storage/alpha.py:236-251; clock.py:47-50; copilot.py:1062-1065. The exception is the "30-bar z" claim attached to promotion.py:88/474-480.

The doc citations exist:
- 0/64: docs/alpha-power-diagnosis-2026-09-18.md.
- 12-28/64: docs/alpha-stack-survey-2026-09-18.md:96-101, whose primary source is docs/alpha-timeline-study-2026-09-16.md.
- 7,065: docs/alpha-study-protocol.md:72, written as "7049 + 16".

The main overreach is causal attribution. The finding presents the zero acceptances as consequences of the holdout double count, but the docs show that execution conversion (pending GTC entries, the ten-trade minimum), the selection/objective mismatch and the IC gate fail jointly. A local 16-trial family still gets 0-4/64.

On the recommendation:
- Option (a) is sound as a new policy version. Note that the timeline study already ran ARCH SPA and max-t as fixed-panel comparisons; their null upper bounds were 9.8-12.3%, inconclusive (docs/alpha-timeline-study-2026-09-16.md, Fixed candidate panels).
- Option (b): the "freeze family/all" piece is largely moot, because the active lanes already keep separate ledgers.
- Option (b) should also note that historical YAML imports have no research run. They can never be qualified through AlphaPromotionService.qualify, which needs a run_id/manifest, whatever the DSR policy.

The live shadow-forecast counts were not verified. This check did not read the database, per the ground rules.

### Value review — P2, now (days)

**Reasoning**

The defect is real, but its north-star leverage is low because the lane it affects is already dead.

What the code does now:
- `_family_dsr` (agentic_trader/research/alpha/promotion.py:291-302) deflates by the lifetime `family/all` trial_count (agentic_trader/storage/alpha.py:239-251).
- It is applied twice, once on validation (promotion.py:351-366) and again on the holdout of one finalist frozen before that holdout (promotion.py:370, 388). Charging the holdout of a single pre-selected candidate for N-way selection counts the selection twice.

The finding overstates the causal story, though:
- docs/alpha-power-diagnosis-2026-09-18.md ("Several criteria fail together") reports that the 10-trade holdout minimum fails in 64/64 dense runs and 63/64 sparse runs. It also says "dropping any single criterion produces no current-family full-policy passes".
- GTC execution conversion leaves an entry pending at about 453 of 498 bars ("Execution conversion is a concrete bottleneck").
- So fixing the DSR alone would most likely still give about 0/64.
- On top of that, the lane needs single-symbol definitions (promotion.py:88), a 30-bar time-series z, and raw IEX/SIP data (promotion.py:474-480). The 16 shadows fail a contract they can never meet (live map Q13: 6,915 contract mismatches, 3,444 stale_closed_bar).
- The weekly ETF32 miner was retired on 2026-10-14 (CLAUDE.md).
- All live research now runs in the pooled lane, which keeps its own ledger and never touches `family/all` (storage/alpha.py:37). The recommendation's "freeze family/all" is therefore already true for every lane that is still active.

Option (a), validation policy v2, is an M-effort statistics rebuild with Romano-Wolf, SPA and PBO. It would revive a sparse per-symbol lane whose other blockers (trade count, GTC, three disagreeing simulators, z-only expressiveness) stay in place. Pooled campaigns and the cards themselves are where the evidence is, so I would drop (a).

The part worth doing now is the cheap end of option (b):
- The operator demotes the 16 shadows with the existing `alpha demote` command (agentic_trader/cli/commands/alpha.py:477-488; storage/alpha.py:377). `AlphaShadowService.observe` only iterates active plus shadow definitions (research/alpha/shadow.py:76-81), so the per-contract call at agent/copilot.py:1062-1065 becomes a no-op and the invalid journal rows stop, with no code change.
- One doc line records that 0/64 is a joint-gate and execution-conversion result, not evidence about whether alpha exists.

Deleting code (miner, simulation, forecast combination) is hygiene, at P3 and later. I give P2 rather than P3 only because the demote and doc step costs minutes, and it removes noise from the live journal that otherwise misleads every future survey (the live map already flagged "retire or re-declare the 16 shadow alphas").

**Risks**

- Demoting changes the registry generation. The promotion CAS requires the global active set to equal each candidate's mining-time snapshot (storage/alpha.py:322-326), so any still-qualified per-symbol candidate gets stranded. That is acceptable if the lane is being retired, but it should be a conscious choice.
- Removing harness code wholesale risks breaking frozen-study reproducibility, and could hit shared pieces: the DSL compiler, the registry CAS, probe enrolment and admission fencing used by the PEAD probe. Because of that, only tag and remove after the probe lane's dependencies are mapped.
- If option (a) is ever built, writing a new validation policy on top of 7,826 charged attempts invites post-hoc relaxation. It needs a new policy id, a planted/null acceptance test frozen before any real-data use, and no retroactive re-reading of consumed holdouts (CLAUDE.md: "never use its output to discount cumulative trials or relax gates retroactively").
- Saying "0/64 was caused by DSR" would be its own error. The diagnosis attributes it to several co-failing criteria plus GTC conversion.

**Prerequisites**

- Operator decision to retire per-symbol z-threshold qualification as a promotion path.
- A list of the 16 shadow version_ids and the current registry generation (read-only `alpha list`), so each demote passes `expected_generation`.
- A check that the PEAD catalog probe (`apriori:pead:v2:long`) is neither shadow nor active, so demotion does not touch it. It sits on the probe list.
- Before any code deletion: a dependency map of `research/alpha` modules that the probe/admission path and the pooled lane import (the pooled lane imports only the DSL evaluator, per the alpha_pipeline map).

**Better alternative**

Skip validation policy v2 for the per-symbol lane entirely. Today:
- (1) Run `alpha demote` on the 16 invalid shadows. That empties the shadow set, so the scan-time observe loop does no work and the invalid forecast rows stop. No code change is needed.
- (2) Add one sentence to docs/alpha-roadmap.md saying the per-symbol promotion path is retired and that 0/64 reflects joint gates plus GTC conversion, not absence of alpha.

If a promotion route is wanted later, design it once for the pooled lane, whose finalist currently has no route to Telegram (alpha_pipeline map G1). There, selection is already controlled by the predeclared fixed set and the one-shot confirmation window. A frozen finalist then needs only a one-sided HAC or block-bootstrap test on its confirmation read, with Holm across finalists. That is the correct one-shot statistic the finding describes, built where candidates actually come from.

---

<a id="c30"></a>

## C30 — The LLM gate selects nothing, runs after nothing has been decided, has no deadline and approves on any error with raw exception text on the card

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | now (days) | signals | defect | medium | M | partially_confirmed |

_lenses: architecture, product_

### Claim (as consolidated)

One method does everything. RiskEvaluator.evaluate_candidate fuses the bracket, sizing, book gates, an I/O correlation check, the macro, earnings, corporate-action, regime and session rules, and the LLM call (evaluator.py:362-917). It runs twice per winner.

Ordering wastes LLM spend. Book sizing runs after the LLM, so the LLM evaluates cards that sizing then blocks. The prompt also asks the model to reject gate violations the code already enforces (prompts.py:24-26).

Failures approve silently. acompletion has no timeout (evaluator.py:751-760). Any exception returns approved=True and writes the exception text into the card's thesis (:886-917).

Live evidence:
- 14 of 14 evaluations approved; 0 llm_vetoed.
- 12 of 14 bracket edits moved by 0.012R or less.
- The eval measured 9–26 s and $0.018–0.035 per call.

The card DTO blocks bracket experiments. LLMTradeEvaluation is the de facto card DTO across six modules, including the outbox, and it hard-codes risk_reward_ratio ge=2.0 (:106). A predeclared sub-2R bracket would raise both at construction and at delivery.

L4 is still Planned.

### Evidence

- agentic_trader/agent/evaluator.py:106, 362-917, 751-760, 858-870, 886-917
- agentic_trader/agent/prompts.py:24-26
- agentic_trader/agent/copilot.py:1364-1427, 1741-1760 (deterministic pass, then LLM, then book_sizer)
- notifier/outbox.py, telegram_bot.py, execution/book_sizing.py, agent/position_sizing.py import LLMTradeEvaluation (grep)
- docs/alpha-roadmap.md:495 (L4 Planned); docs/llm-prompt-eval-2026-09-22.md
- Live map Q7/Q16 (14/14 approved; bracket edits)

### Recommendation

Build L4 as a pipeline with typed results between stages: BracketBuilder → Sizer (including the book factor) → risk book gates → Reviewer.

Replace LLMTradeEvaluation with a policy-free Card DTO validated against RiskLimits.

For the Reviewer:
- Make native review commentary-only, as it already is for catalog probes.
- Run it last, under asyncio.wait_for.
- Return a typed ReviewOutcome.
- Record failures as llm_verdict={'error': TypeName} with a readiness counter, and never put exception text on a card.

Record LLM-edited brackets as their own arm in cards outcomes. Point the LLM at text instead (C32).

### North-star impact

No silently approved cards during LLM outages, LLM latency and cost spent only on sendable cards, and bracket experiments no longer blocked by a DTO literal.

### References

- Lopez-Lira & Tang (2023), Can ChatGPT Forecast Stock Price Movements? — Headline text is where an LLM can plausibly add information (https://arxiv.org/abs/2304.07619)

### Fact check — partially_confirmed

**Corrected claim**

RiskEvaluator.evaluate_candidate (evaluator.py:362-917) fuses the deterministic bracket and sizing, the shared book gates, an optional awaited return-correlation check (off by default), macro, earnings, corporate-action, regime, reward/risk and session rules, and the LLM call. It runs twice per winner: a use_llm=False collect pass (copilot.py:1744) and a send pass with the LLM (copilot.py:1364).

The LLM does not run "after nothing has been decided". It runs after every deterministic entry gate and after card_policy, and only book sizing follows it (copilot.py:1394-1424). So a card that sizing blocks has already spent an LLM call, but under the shipped config this has been rare (the preview had 6 factors, all 1.00). The system prompt still tells the model to "reject a candidate that violates a configured gate" (prompts.py:25-26), which the code has already enforced.

The acompletion call has no timeout (evaluator.py:751-760), unlike macro_explainer.py:97 (timeout=35). Any exception, including a malformed or schema-invalid reply, returns approved=True with the deterministic bracket. It also puts the raw exception text into thesis_summary, which Telegram renders (telegram_bot.py:391, 535). It records no llm_verdict, and only a WARNING log marks it (evaluator.py:886-917). The failure is visible on the card, but silent to the journal, readiness and cards outcomes.

LLMTradeEvaluation is used in six modules: evaluator, copilot, position_sizing, book_sizing, outbox (model_validate at delivery, outbox.py:78) and telegram_bot. It hard-codes risk_reward_ratio ge=2.0 (:106). Under the shipped config (min_risk_reward_ratio 2.0), the reward_risk rule (evaluator step 3b) refuses a sub-2R alpha bracket before construction. The DTO literal only becomes the blocker, raising at construction and at outbox delivery, once the config minimum (no lower bound, config.py:402) is lowered below 2. A sub-2R bracket experiment therefore needs both the config change and the DTO fix.

Live figures come from the review's production queries (live map Q7/Q16) and were not re-verified: 14/14 approved, 0 llm_vetoed, 12/14 edits of 0.012R or less. Latency in docs/llm-prompt-eval-2026-09-22.md was 15.4 s and 26.2 s on the two production traces, with harness means of 8.3–16.5 s, at $0.018–0.035 per call. L4 is Planned (docs/alpha-roadmap.md:495).

**Checked**

- agentic_trader/agent/evaluator.py:106 - `risk_reward_ratio: float = Field(ge=2.0)` confirmed
- agentic_trader/agent/evaluator.py:362-917 - evaluate_candidate fuses deterministic bracket/sizing (calculate_levels_deterministic), book_gates (:476-478), awaited data_fetcher.calculate_correlation (:480-499, only when enable_dynamic_correlation, default False at config.py:397), macro lockout, earnings, corporate actions, regime breakout, reward_risk, session, then the LLM
- agentic_trader/agent/evaluator.py:751-760 - litellm.acompletion with no timeout argument; contrast macro_explainer.py:96-97 which passes timeout=35; no litellm.request_timeout set anywhere (grep)
- agentic_trader/agent/evaluator.py:886-917 - broad `except Exception` returns approved=True, deterministic bracket, thesis_summary=f"... (LLM fallback used: {e})", and no llm_verdict (so _llm_block at copilot.py:656-664 records None)
- agentic_trader/notifier/telegram_bot.py:391,535 - thesis_summary is rendered on the card, so exception text reaches Telegram
- grep 'LLM evaluation failed' - only a logger.warning at evaluator.py:888; no readiness counter or incident
- agentic_trader/agent/evaluator.py:858-870 - catalog probes (catalog_event set) get commentary-only verdicts; native verdicts are applied
- agentic_trader/agent/prompts.py:25-26 - 'Reject a candidate that violates a configured gate' (line 24 is blank)
- agentic_trader/agent/copilot.py:1741-1760 - deterministic pass with use_llm=False for every candidate; copilot.py:1364-1373 - second evaluate_candidate with use_llm for winners; copilot.py:1394-1424 - book sizing runs after the LLM, and the comment at :1395-1396 says a blocked card has spent LLM budget
- copilot.py:1333-1352 - card_policy runs before the LLM
- config/config.yaml:465-466 - book_sizing enforce since 2026-10-20; the preview had 6 factors, all 1.00, so wasted LLM calls on sizing-blocked cards are rare so far
- grep LLMTradeEvaluation: evaluator, copilot, position_sizing (:14, :281), execution/book_sizing (:23), notifier/outbox (:10, :78 model_validate at delivery), notifier/telegram_bot (:33) - six modules
- config.py:402 risk.min_risk_reward_ratio=2.0 (no lower bound); config.yaml:274; risk/rules.py:259-267 reward_risk; evaluator.py:658-666 step 3b refuses a sub-required_rr alpha bracket
- agentic_trader/research/alpha/strategy.py:81-104 AprioriBracketPolicy allows any reward_risk > 0
- docs/alpha-roadmap.md:495 - L4 'Planned' confirmed
- docs/llm-prompt-eval-2026-09-22.md:17-18 (production traces 15.44 s/$0.0178 and 26.17 s/$0.0348), :90 (harness means 8.3-16.5 s)
- docs/card-evidence.md:360-363 Proxy bracket - labels use the deterministic COLLECT bracket, not the LLM-edited one
- scratchpad holistic-review/maps/live.md:55,77,126,135 - source of 14/14 approved, 0 llm_vetoed, 12/14 edits <=0.012R (Q7/Q16); not independently re-queried because the DB URL lives in .envrc, which is off-limits
- https://arxiv.org/abs/2304.07619 - Lopez-Lira & Tang, 'Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models', 2023; the abstract supports headline-based predictability

**Bad references**

_none_

**Notes**

The core defects are confirmed in code:
- no timeout on the LLM call;
- approval on any exception, with the exception text placed on a Telegram-rendered card and no llm_verdict or readiness signal;
- the ge=2.0 literal on a DTO that is re-validated at outbox delivery;
- book sizing placed after the LLM;
- the double evaluation;
- a prompt instruction that duplicates the gates.

Corrections:
1. The title's "runs after nothing has been decided" misdescribes the order. Every deterministic gate and card_policy precede the LLM; only book sizing follows it.
2. "Failures approve silently" is half-right. The card visibly says "LLM fallback used: <exception>". It is silent only to the journal, readiness and outcomes.
3. The prompt citation should be prompts.py:25-26, not 24-26.
4. A sub-2R bracket is first refused by the reward_risk rule at the config minimum. The DTO literal is a second, redundant block that binds only after the config minimum is lowered.
5. The latency range "9–26 s" mixes harness means (8.3–16.5 s) with production traces (15.4 and 26.2 s).
6. Wasted LLM calls on sizing-blocked cards are mostly theoretical so far: all six preview factors were 1.00.

Additional supporting evidence:
- The code comment at evaluator.py:847-849 already acknowledges that a schema-invalid reply would "turn an LLM veto into the fallback approval".
- The sizing-block `_rejected` path (evaluator.py:459-472) passes risk_reward_ratio=target/stop or 0. That would itself fail the ge=2.0 validator if stop_distance were 0. This is a latent construction error caused by the same DTO literal.

The Lopez-Lira & Tang reference resolves and supports its stated use.

### Value review — P2, now (days)

**Reasoning**

The defects are real; I checked each one in the code. The full L4 rebuild is not worth funding now. A narrow hotfix is.

What the code does now:
- No deadline on the LLM call. `litellm.acompletion` runs with no timeout (agentic_trader/agent/evaluator.py:751-760). The repo already uses the pattern this needs, `asyncio.wait_for` around an LLM scorer (agentic_trader/agent/news_context.py:116, :208).
- Any exception approves the card. The `except Exception` branch returns `approved=True`, puts the raw exception into `thesis_summary` ("LLM fallback used: {e}"), and sets no `llm_verdict` (evaluator.py:886-917).
- A failure looks like "LLM not used". `_llm_block` returns None when there is no verdict (agentic_trader/agent/copilot.py:656-664). So in `cards outcomes`, an LLM failure is indistinguishable from a `use_llm=False` run, which is an attribution gap.
- The DTO literal is real: `risk_reward_ratio: float = Field(ge=2.0)` (evaluator.py:106). `LLMTradeEvaluation` is imported by the outbox, telegram_bot, book_sizing and position_sizing modules (grep).
- Book sizing runs after the LLM (copilot.py:1364-1427), as claimed.

How much this matters:
- The safety cost of approve-on-error is small. Every deterministic risk rule has already run in the deterministic pass (copilot.py:1741-1760) and again in the same `evaluate_candidate` call before the LLM. Admission rechecks risk, and the operator taps by hand. So "silently approved" in practice means the deterministic card was sent.
- The real costs are three. A hung provider call has no bound and can stall a scan; Telegram handlers serialize. LLM failures do not show in the outcome attribution. Exception text reaches the operator's card.
- LLM spend is not a real argument. `max_llm_evaluations_per_scan` is 4 (config.py:197), so the spend is cents per day.
- The sub-2R DTO blocker is moot. The bracket is closed: no arm beats the live 2R net of SPY (docs/bracket-counterfactual-2026-10-18.md), and no sub-2R experiment is planned.

The finding's strongest point is about honesty, not architecture:
- The gate approved 14 of 14 evaluations, with 0 `llm_vetoed`. Two large bracket tightenings, SPY and CEG, were applied to the cards (live map Q7/Q16, gap 5).
- `card_stats` labels the deterministic bracket, not the bracket that was sent (docs/card-evidence.md "Limits / Proxy bracket"). The card that was sent and the card that was measured differ.
- Making native review commentary-only fixes that today without a refactor. The catalog-probe path already does exactly this (evaluator.py:858-870).

Why the full L4 is P3:
- Splitting `evaluate_candidate` into a pipeline and replacing the DTO is architecture hygiene. It adds no edge, and the binding constraint on the north star is entry-side alpha, not the gate's structure.
- The roadmap orders L3, the selector seam, before L4 (docs/alpha-roadmap.md, L3/L4 rows). L3 has more leverage because it lets a shadow ranker be A/B tested.

Recommendation: fund the narrow fix now as P2, and keep L4 at P3.

**Risks**

- **Persisted outbox payloads.** Replacing `LLMTradeEvaluation` with a new Card DTO touches payloads the outbox stores and replays. Delivery is at least once (CLAUDE.md contract 6), so a rename or validator change can break redelivery of pending rows or rebuilds. This needs a migration and compatibility plan, which the finding's M effort estimate understates.
- **Risk parity tests.** Refactoring `evaluate_candidate`, which is about 550 lines and fuses the risk rules, risks breaking the L2 seeded tests that pin scan/admission agreement.
- **Behaviour changes on live cards.** Making native review commentary-only stops LLM bracket edits and vetoes. That should be logged as a dated behaviour change.
- **Prompt changes need trace evidence.** Any prompt change, for example removing the "reject gate violations" text at prompts.py:24-26, needs LangSmith trace and replay evidence before merge.
- **Timeout value.** Too short a timeout turns the observed 9–26 s calls into routine fallbacks.
- **Diverted effort.** Engineering time moves away from entry-side alpha work, which is the actual bottleneck.

**Prerequisites**

- **Measure fallback frequency first.** Query the scan journal and logs for "LLM evaluation failed" and for any thesis containing "LLM fallback used", to see whether fallbacks have already happened in production.
- **Builds on L1.** It needs L1's `llm_verdict` journaling, which is already merged.
- **L3 before L4.** For the full L4, the L3 card-selector seam should land first (roadmap order), and there should be an explicit plan for compatibility with persisted outbox and LLMTradeEvaluation payloads.
- **Operator decisions.** The operator must approve native review becoming commentary-only, which removes the native LLM veto and bracket edits. It is a contract change to the L4 wording "may only annotate or, for native cards, veto".

**Better alternative**

A hotfix of under a day instead of L4. It has four parts:

1. Wrap `acompletion` in `asyncio.wait_for` with a validated config timeout. Copy the `news_context.py:116` pattern.
2. In the fallback branch:
   - Set `llm_verdict={'error': type(e).__name__, 'applied': False}`, and count those errors in the scan journal or a readiness counter.
   - Write a fixed thesis string with no exception text.
3. Add one config switch that makes native verdicts commentary-only, reusing the catalog-probe branch at evaluator.py:858-870. With it on:
   - The LLM never edits brackets, so the sent card equals the measured card.
   - The LLM never vetoes, which costs nothing at 14 of 14 approvals.
4. Leave the `ge=2.0` DTO literal and the evaluator split for L4. Do them after L3, and only if a predeclared bracket experiment ever needs them.

This removes every operational hazard in the finding at a small fraction of the cost. The money saved should go to the L3 selector seam and to entry-side alpha.

---

<a id="c32"></a>

## C32 — Test news tone prospectively on the journal already being written; any historical LLM study of cached headlines is look-ahead contaminated unless entities are anonymised and the test window postdates the model's training cutoff

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | research_methodology | research_direction | medium | M | partially_confirmed |

_lenses: data, literature_

### Claim (as consolidated)

Each universe scan scores Benzinga headlines for the first 6 ranked symbols with an LLM, journaling the model id and prompt_sha256 (news_context.py:62-63, 166-169). The record since 2026-10-13 is therefore free of look-ahead.

History is cached back to 2015-01-02, and the doc says 'no study yet'. Other proposals, including C30's, suggest testing tone on that history.

Historical LLM scoring is contaminated:
- A model trained after 2016–2025 can recognise the firms and their outcomes.
- Glasserman-Lin find that most in-sample GPT-sentiment advantage is look-ahead plus a distraction effect driven by company names.

Where the effect lives: Lopez-Lira-Tang's predictability is strongest in small stocks and negative news, and declines as LLM adoption rises. Positive tone is therefore weakest where this long-only, large-cap desk trades.

NewsItem also drops updated_at.

### Evidence

- docs/news-context.md:9-29, 179-185, 201 (live scoring; no study; history to 2015)
- agentic_trader/agent/news_context.py:62-63, 97-136, 166-169, 274-292
- agentic_trader/data/news.py:33-48 (no updated_at)

### Recommendation

Adopt a text-source contract:

1. Historical studies use time-invariant, non-LLM measures: Loughran-McDonald negative share, headline counts, and novelty against the prior 5 days. Alternatively, fit a Ke-Kelly-Xiu supervised score on the discovery window only. Historical LLM prompts are allowed only with anonymised entities and only on data after the model's training cutoff.

2. LLM tone accrues prospectively, keyed by model id and prompt hash, and is judged by a predeclared forward read: headlines published before the decision against next 1- and 5-session alpha_R, with a minimum n and a confidence-sequence stop.

3. Prefer negative-news and no-news flags as vetoes.

4. Treat the 8-K Item 2.02 Exhibit 99.1 earnings release as the highest-value text, used as a PEAD conditioner: scored live at acceptance, and with dictionaries only in historical studies.

Keep updated_at on news items.

### North-star impact

Avoids spending a confirmation window, or the operator's trust, on a contaminated news result, and points the LLM at the one place it plausibly adds information.

### References

- Glasserman & Lin (2023), Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis — Look-ahead bias and the anonymisation remedy (https://arxiv.org/abs/2309.17322)
- Lopez-Lira & Tang, Can ChatGPT Forecast Stock Price Movements? (v6) — Predictability strongest in small stocks and negative news; declines with adoption (https://arxiv.org/html/2304.07619v6)
- Loughran & McDonald (2011), JF; Ke, Kelly & Xiu (2019), Predicting Returns with Text Data, NBER 26186 — LLM-free historical text measures

### Fact check — partially_confirmed

**Corrected claim**

Most of the finding holds. Each scan without a timeframe fetches Benzinga headlines (48 h window, items dated after the scan excluded, data/news.py:180-204). It LLM-scores the first 6 ranked symbols that have at least one company item (news_context.py:241). When a score succeeds it records model and prompt_sha256 (news_context.py:166-169). The journal row exists for universe scans only.

That makes the record since 2026-10-13 free of look-ahead. It is not free of distraction:
- Glasserman & Lin found that anonymised headlines OUTPERFORMED the originals in-sample. The distraction effect from company names outweighs look-ahead bias, is strongest for larger companies, and can persist out-of-sample.
- So the paper does not say that 'most in-sample GPT-sentiment advantage is look-ahead plus distraction'. Distraction lowers the measured performance.
- The live scorer sends the ticker and headlines that are not anonymised (news_context.py:106-112). The desk's mega_caps group is where this effect is largest.

On the history and the data:
- Research history is cached back to 2015-01-02 (docs/news-context.md:154), and no study exists (docs/news-context.md:179, 201).
- Historical LLM scoring of that cache would be look-ahead contaminated for any period before the model's training cutoff.
- NewsItem drops updated_at, both live and in the research cache (data/news.py:33-44, research/news.py:162).

Corrections to the rest:
- The desk is long-only (config/config.yaml:305, 320), but it is not purely large-cap. The 159-name universe has 32 ETFs, about 65 mega/large caps, a 62-name research_cohort that includes mid and small caps, and up to 20 dynamic movers (config/config.yaml:96-271).
- A descriptive prospective tone read already exists: cards outcomes reports by_tone and by_alignment with n, mean_r_cost and hit_rate (research/setups/outcomes.py:731-765). It is not predeclared, has no SPY-relative alpha_R at 1 or 5 sessions, and has no stopping rule. Recommendation 2 extends this read rather than building a new one.
- The recorded model id is the configured string, by default the alias 'openai/gpt-4o' (config.py:871), not a pinned snapshot.
- prompt_sha256 covers the system prompt only, not the user-message template (news_context.py:49).
- Keying by model id plus prompt hash is therefore not a fully immutable scorer identity.

**Checked**

- agentic_trader/agent/news_context.py:41-49 (system prompt; prompt_sha256 hashes the system prompt only)
- agentic_trader/agent/news_context.py:106-112 (user message sends 'Symbol: {symbol}' and raw headlines, so entities are not anonymised)
- agentic_trader/agent/news_context.py:159-173 (block carries model and prompt_sha256 only when scored)
- agentic_trader/agent/news_context.py:217-270 (fetch window ends at now; only the first score_first symbols with at least one item are scored)
- agentic_trader/agent/news_context.py:273-293 (model = news.model or AppConfig.llm_model)
- agentic_trader/data/news.py:33-59 (NewsItem has no updated_at; extra='ignore' drops it)
- agentic_trader/data/news.py:180-204 (select_items excludes items after now)
- agentic_trader/research/news.py:4,162 (research cache stores normalised NewsItem dumps, so updated_at is lost there too)
- agentic_trader/config.py:363 (max_scored_per_scan default 6); config/config.yaml:518-521 (6)
- agentic_trader/config.py:871 (llm_model default 'openai/gpt-4o', a provider alias rather than a dated snapshot)
- config/config.yaml:96-258 (159 names: etf32, mega_caps, a 62-name research_cohort with mid/small caps such as AUB, ATKR, AUGO); 259-271 (dynamic movers, up to 20)
- config/config.yaml:305,320 (allow_short: false for both native strategies, so the desk is long-only)
- docs/news-context.md:9-29 (live scoring), 154 (history reaches 2015-01-02), 179-180 and 201 (no study yet), 185 ('long history')
- docs/news-context.md:80-99 and agentic_trader/research/setups/outcomes.py:731-765 (cards outcomes already reports a descriptive by_tone/by_alignment read: n, mean_r_cost, hit_rate on mature rows)
- https://arxiv.org/abs/2309.17322 (Glasserman & Lin abstract)
- https://arxiv.org/html/2304.07619v6 (Lopez-Lira & Tang v6)

**Bad references**

- Glasserman & Lin (2023), arXiv:2309.17322: the source exists and supports the anonymisation remedy. It contradicts the finding's description, though. The paper reports that anonymised headlines outperform in-sample and that the distraction effect (a drag) outweighs look-ahead bias, not that most in-sample advantage is look-ahead plus distraction. It also says distraction can persist out-of-sample, so it applies to the live scorer too.
- docs/news-context.md:179-185, 201 as evidence for 'history to 2015': the 2015-01-02 date is at docs/news-context.md:154. Lines 179-185 and 201 support only 'no study' and 'long history'.

**Notes**

The core research-direction claim holds:
- Historical LLM tone on the 2015-2025 cache would be contaminated unless entities are anonymised and the window postdates the training cutoff.
- The live prospective record avoids look-ahead.
- updated_at is dropped.

Lopez-Lira & Tang v6 supports the claim. Its abstract says GPT-4 scores predict drift 'especially for small stocks and negative news' and that 'Strategy returns decline as LLM adoption rises', though the body presents the adoption decline as suggestive rather than a direct test.

Loughran & McDonald (2011, JF) and Ke, Kelly & Xiu (NBER w26186) are correctly identified as LLM-free text methods.

I could not verify the reference to C30's proposal, since that is another finding.

Material corrections:
1. Glasserman-Lin is misdescribed. Their result also undercuts the implication that the live record is uncontaminated: distraction remains, strongest for mega caps, and the live prompt includes the ticker.
2. The desk is long-only but not large-cap only.
3. A descriptive by_tone/by_alignment read already exists in cards outcomes.
4. Model identity is an alias, and the prompt hash covers the system prompt only.

The recommendation to prefer negative-news and no-news vetoes conflicts with the current contract that news is never a gate (docs/news-context.md:3-5, 39-40). Adopting it would need operator approval and the card_policy-style preview-then-enforce route.

Everything was checked read-only in /Users/adamhadani/Development/agentic-trader-intraday at db82817.

### Value review — P2, next (weeks)

**Reasoning**

This finding is really four separate items, and they are worth different amounts.

(1) Text-source contract: no historical LLM scoring of named, pre-cutoff text. This is correct and costs almost nothing, a paragraph in docs/news-context.md ("Research acquisition"). Its value is defensive. The live block already records `model` and `prompt_sha256` (agentic_trader/agent/news_context.py:62-63, 166-169), so the record since 2026-10-13 is clean. The `alpha news` acquisition exists (docs/news-context.md, "Research acquisition") and the doc says "No study exists yet". That is exactly the setup in which someone would next run an LLM over 2016-2026 headlines and get a Glasserman-Lin false positive. One sentence should prevent it. The same rule should cover any future historical replay of the LLM gate, which sees symbols and dates. I found no such replay in the repo.

(2) Predeclared prospective forward read. Most of the machinery already exists. `cards outcomes` already buckets mature rows by `news_tone`, `news_alignment` and catalyst (agentic_trader/research/setups/outcomes.py:285-294, 365-368, 743-765). What is missing is a frozen read: n floor, horizon, stopping rule. That is a small frozen JSON. Power is the binding constraint, though. At most 6 scored symbols per universe scan, 2 scans a session (config/config.yaml:520-521), heavily repeated symbols, and a large-cap universe where tone skews positive/neutral. The "opposed" bucket, negative tone on a LONG, will accrue a few rows a week. A useful read is months away, so writing it now is cheap but pays off late.

(3) Historical dictionary fields: LM negative share, headline count, 5-day novelty. This is the only part that could produce a signal, and it is a natural candidate for the next pooled fixed set, because the pooled lane machinery (cohorts, cube, checks A/C, journaled confirmation window) is reusable. The prior is low, though. CLAUDE.md records 64 null formula-cohort pairs, including the events lane on earnings surprise and reaction (docs/alpha-pooled-events-v1-2026-10-15.md). The literature effect lives in small caps and negative news, and the label is a long bracket on mid/large caps. It should compete with the other entry-side sources for the next operator decision. It does not jump the queue.

(4) 8-K Item 2.02 Exhibit 99.1 as a PEAD conditioner: drop for now. It needs a new EDGAR full-text acquisition plus a live acceptance-time scorer (a new worker with a readiness component). It conditions a PEAD/earnings effect that the events lane already found null on both cohorts.

Keeping `updated_at` (agentic_trader/data/news.py:33-48 drops it with extra="ignore") is a one-line fix. It only matters if a historical news study ever runs, so it rides along with any news-touching PR.

Net: P2. The guardrail, the predeclaration and `updated_at` are worth a few hours bundled into the next workstream PR (operator rule: no docs-only PRs). The dictionary fixed set is one candidate among several for the next data source. None of this moves cards off "trailing SPY" in the near term.

**Risks**

- **Power.** The prospective read may never reach a decisive n at 6 scored symbols per scan. Without a confidence-sequence stop and a hard expiry, it becomes a perpetual "watch this" item, or gets peeked at and turned into a veto on a small sample.
- **Veto drift.** Recommendation 3 (negative or no news as a veto) touches card selection. Under CLAUDE.md contract 8, only measured, sample-floored `card_policy` evidence may withhold cards. Any veto must go through that path after a frozen read, not straight from a bucket mean.
- **Dictionary lane look-ahead.** A historical dictionary lane has its own look-ahead traps:
  - Benzinga timestamps vs. revisions, which is why `updated_at` matters.
  - Market-wide list items tagging more than five symbols (docs/news-context.md, "Operations": 185 of 1,003 items).
  - Coverage that thins before about 2018.
  - The news cache is acquired per cohort. The documented example uses spread cohort-v1, and whether cohort v2/v3 history is already cached is unverified, so "data already cached" may overstate how cheap this is.
- **Ke-Kelly-Xiu supervised score.** It adds a fitted text model and a fresh multiple-testing surface to a lane that has so far run fixed sets only.
- **Opportunity cost.** Every week spent here is a week not spent on the entry-side items the roadmap ranks ahead.

**Prerequisites**

- **Model and cutoff.** Record the news scorer's model id and its training cutoff in the contract. The block stores `model`, but the cutoff must come from the provider.
- **Bundling.** Ship with the next feature PR, per the bundle-docs rule. Contents:
  - the contract paragraph in docs/news-context.md and docs/alpha-pipeline.md;
  - `updated_at` on `NewsItem` and in the research cache;
  - a frozen forward-read spec. It needs: the outcome (mature `r_cost` and SPY-excess from `cards outcomes`); the buckets (tone, and alignment opposed vs. none); a per-arm n floor; a clustered-by-symbol variance or confidence-sequence stop; and an expiry date.
- **Dictionary fixed set.** Before considering one:
  - verify which cohorts' Benzinga history is actually in the cache;
  - measure per-year coverage and the market-wide-tag share;
  - get operator approval as the next data source, competing with the post-insider options;
  - run it through the standard checks A and C on the same cube.

**Better alternative**

Split it.

Do now, at near-zero cost, inside the next PR:
- the one-paragraph contract against historical LLM text studies, extended to any historical replay of the LLM gate;
- `updated_at` on news items;
- a frozen forward-read JSON over the existing `cards outcomes` `news_context` buckets.

Skip the Ke-Kelly-Xiu model and the 8-K Exhibit 99.1 build.

If the operator wants a news alpha source, the cheapest higher-leverage version is to add 2-3 time-invariant fields (headline count rel. to 60-day mean, 5-day novelty, LM negative share) to the next pooled fixed set. That reuses the cube, checks A/C and the journaled confirmation window. Do it only after confirming the cohort-v2/v3 news cache exists and is consistent across years.

Treat negative or no news as a veto only through `card_policy` after the forward read clears its floor.

---

<a id="c33"></a>

## C33 — Start capturing daily option-implied data now: the literature's best weekly-horizon family for liquid names cannot be bought back later

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | integration | medium | M | partially_confirmed |

_lenses: data, literature_

### Claim (as consolidated)

The current options code (options/fetcher.py) builds GEX from unpinned yfinance chains, for display only. The data assessment deferred options as a paid item.

alpaca-py 0.44 already exposes OptionHistoricalDataClient.get_option_chain, which returns implied_volatility and greeks on a free 'indicative' feed (OPRA itself requires a subscription). History starts only in February 2024. No free history therefore covers 2016–21, but forward capture is free today.

What the literature finds:
- Cremers-Weinbaum: expensive calls versus puts predict +50 bp/week, more strongly when the options are liquid.
- Xing-Zhang-Zhao: a steep smirk predicts −10.9%/yr and precedes bad earnings.
- An-Ang-Bali-Cakici: changes in implied volatility predict about 1%/month.
- Johnson-So: option-to-stock volume is informative.

Muravyev-Pearson-Pollet show that parity signals largely reflect borrow costs, so a long-only desk can use them only as veto or ranking inputs.

### Evidence

- agentic_trader/options/fetcher.py:9, 36, 67, 88 (yfinance chains)
- alpaca-py 0.44: alpaca/data/historical/option.py:24, 204; alpaca/data/models/snapshots.py:97-104
- docs/alpha-data-augmentation-2026-10-18.md:50 (options deferred as paid)
- agentic_trader/data/corporate_actions_worker.py:44-68 (worker pattern)

### Recommendation

Add daily per-underlying summaries to the pit-snapshots worker (C20) at about 15:45 ET, covering the static, dynamic and PEAD names:
- 30-day ATM implied volatility;
- implied volatility minus 20-day realised volatility;
- 25-delta put skew;
- matched-strike put-call IV spread;
- option-to-stock volume;
- the implied earnings move.

Tag each value with feed 'alpaca:indicative' and the receipt time. Filter to expiries of 60 days or less and strikes within ±20%, which is about 1–3 pages per name.

Validate the indicative IV against an independent source first.

Use the data in two stages:
- Now, as card risk context: the implied earnings move for held names, and forward volatility next to the covariance.
- After about 250 sessions, in a predeclared prospective test on a 5-session label with a matched control.

Replace the yfinance GEX source with the same feed.

### North-star impact

Gives cards forward-looking risk lines now, and builds the only free, plausibly orthogonal short-horizon signal family for liquid names, but only if capture starts now.

### References

- Cremers & Weinbaum (2010), Deviations from Put-Call Parity and Stock Return Predictability, JFQA 45(2) — Weekly predictability from the call-put IV spread
- Xing, Zhang & Zhao (2010), JFQA 45(3); An, Ang, Bali & Cakici (2014), JF 69(5) — Smirk and IV-change predictors
- Muravyev, Pearson & Pollet (2022), JF — Parity signals largely reflect borrow fees
- Alpaca historical option data and option chain reference — Free indicative feed with IV and greeks; history from February 2024 (https://docs.alpaca.markets/reference/optionchain)

### Fact check — partially_confirmed

**Corrected claim**

Current code: per-name option data comes only from on-demand yfinance chains (options/fetcher.py:9,67,88). They are cached in memory for 60 s, never journaled or digest-pinned, and used only for the /gex Telegram command, the gex CLI and a chat tool. No scan, evaluator or risk path reads it. The data assessment deferred option chains as a paid item (docs/alpha-data-augmentation-2026-10-18.md:50) and did not consider Alpaca's free feed.

The installed alpaca-py 0.44.0 already provides OptionHistoricalDataClient.get_option_chain (alpaca/data/historical/option.py:24,204). On the free 'indicative' feed it returns IV and Black-Scholes greeks per contract. Indicative quotes are modified and trades are delayed 15 minutes; OPRA requires a subscription. The chain endpoint is latest-only and has no open interest.

Two history facts make forward capture time-sensitive:
- Alpaca serves no historical IV or greeks at all.
- Alpaca's historical option bars and trades start in February 2024 only.

Corrections to the finding:
- **Title overstates the urgency.** The history can be bought later from paid vendors (CBOE DataShop, OptionMetrics, ORATS, Polygon; the doc itself lists the first and last). What cannot be recovered later is free, Alpaca-indicative, as-of-receipt IV snapshots. Part of an IV history could be rebuilt from Alpaca option bars, but only back to February 2024.
- **GEX cannot move to the snapshot feed alone.** GEX needs open interest (options/gex.py:119), which snapshots lack. It must be joined from the Trading-API option contracts endpoint (alpaca/trading/models.py:686).
- **Page counts are too low for index ETFs.** '1–3 pages per name' holds for typical single names. SPY and QQQ have daily expiries, so a 60-day, ±20% window needs far more pages.
- **The borrow-fee citation is wrong.** The finding is in Muravyev, Pearson and Pollet, JFE 172 (2025), 'Why Does Options Market Information Predict Stock Returns?' (about two-thirds of the predictability is borrow fees). It is not the JF 2022 paper.

The literature magnitudes for Cremers-Weinbaum, Xing-Zhang-Zhao and An-Ang-Bali-Cakici are accurate. An-Ang-Bali-Cakici is a monthly horizon, not weekly.

**Checked**

- agentic_trader/options/fetcher.py:9 (import yfinance), :36 (class docstring 'via yfinance'), :67 (yf.Ticker), :88 (ticker.option_chain): all as cited
- agentic_trader/options/*.py: no journal, store, persist or digest, so chains are fetched on demand with a 60 s in-memory cache (fetcher.py:38-40) and never pinned
- GEX consumers are display only: notifier/telegram_bot.py:857,1028-1039 (/gex), cli/commands/options.py:16-49 (gex CLI), agent/copilot_tools.py:86-89 (chat tool), agent/copilot.py:435,4500-4506. Nothing in evaluator, risk or scan reads it
- grep over agentic_trader/scripts/config: no use of OptionHistoricalDataClient, OptionChainRequest, OptionsFeed or get_option_chain. The only per-name IV is the yfinance column consumed in options/gex.py:119-125. Market vol is VIX only (constants.py:152-178)
- docs/alpha-data-augmentation-2026-10-18.md:50: row 9 marks historical option chains as 'paid (CBOE DataShop; Polygon options tier)', verdict 'defer until a free lane pays', so the claim holds
- .venv alpaca_py-0.44.0: alpaca/data/historical/option.py:24 (OptionHistoricalDataClient), :204 (get_option_chain, GET /options/snapshots/{underlying}); alpaca/data/models/snapshots.py:97-105 (OptionsSnapshot with implied_volatility and greeks); alpaca/data/enums.py:103-114 ('opra requires subscription', INDICATIVE); alpaca/data/requests.py:443-469 (OptionChainRequest with strike and expiry filters)
- alpaca/trading/models.py:686-687: open_interest is on the Trading-API contract model, not on OptionsSnapshot
- agentic_trader/data/corporate_actions_worker.py:44-68: once-daily run_once worker pattern, as cited
- docs/alpha-roadmap.md:868-875 and docs/roadmap.md:74,576-582: the existing roadmap treats options only as an execution asset class or as a 'Completed' yfinance GEX phase, with no IV capture lane
- https://docs.alpaca.markets/reference/optionchain: latest trade, quote, IV and Black-Scholes greeks; no open interest; indicative is free with delayed trades and modified quotes; latest-only
- Alpaca historical option data docs: 'only offer historical option data since February 2024'
- Cremers-Weinbaum JFQA 45(2) 335-367: 50 bp/week; stronger with high option liquidity and low stock liquidity. Confirmed
- Xing-Zhang-Zhao JFQA 45(3) 641-662: 10.9%/yr; worst earnings shocks follow. Confirmed
- An-Ang-Bali-Cakici JF 69(5) 2279-2337: about 1%/month from IV changes. Confirmed
- Muravyev-Pearson-Pollet: JF 77(3) 2022 is 'Is There a Risk Premium in the Stock Lending Market?'. The borrow-fee explanation of options predictors is JFE 172 (2025), 'Why Does Options Market Information Predict Stock Returns?' (about two-thirds reduction)

**Bad references**

- Muravyev, Pearson & Pollet (2022), JF. JF 77(3):1787-1828 is 'Is There a Risk Premium in the Stock Lending Market? Evidence from Equity Options', whose headline is that borrow-fee risk carries little premium. The finding that options predictors (IV spread, put-call parity) largely reflect borrow fees is in Muravyev, Pearson & Pollet, 'Why Does Options Market Information Predict Stock Returns?', Journal of Financial Economics 172 (2025). Only an earlier working-paper version of the JF paper carried a related spread/skew result.

**Notes**

The finding's core holds up:
- options are yfinance-sourced, unpersisted and display-only;
- the data assessment deferred options as paid;
- alpaca-py 0.44 has a free indicative chain with IV and greeks;
- nothing in the repo or roadmap captures per-name IV.

All cited file:line evidence checks out. The snapshots.py range is 97-105 rather than 97-104, which doesn't matter.

Points that need correcting or caveating:
1. **'Cannot be bought back later' overstates it.** Paid historical chains exist. The time-sensitive part is the free indicative IV snapshot record, because the chain endpoint is latest-only and Alpaca history starts in February 2024.
2. **'History starts only in February 2024' conflates two things.** It is true of Alpaca historical option bars and trades. Alpaca keeps no history of IV or greeks at all.
3. **Replacing the GEX source needs a second endpoint.** Open interest is missing from snapshots, so the Trading-API contracts endpoint is needed too.
4. **The indicative feed is unproven for this use.** Quotes are 'modified' per Alpaca docs, so skew and the parity-spread inputs could be distorted. The finding's 'validate against an independent source first' is essential, not optional.
5. **One reference is wrong.** The Muravyev-Pearson-Pollet borrow-fee result is cited to the wrong paper and venue.

The 'best weekly-horizon family' framing is a judgment call. Cremers-Weinbaum found predictability shrinking over their sample, and the JFE 2025 paper attributes about two-thirds of options-signal predictability to borrow fees. That weakens the long-only edge and supports the finding's own 'veto or ranking only' caveat.

Sources:
- [Alpaca option chain reference](https://docs.alpaca.markets/reference/optionchain)
- [Alpaca historical option data](https://docs.alpaca.markets/docs/historical-option-data)
- [Cremers-Weinbaum (RePEc)](https://ideas.repec.org/a/cup/jfinqa/v45y2010i02p335-367_00.html)
- [Xing-Zhang-Zhao (RePEc)](https://ideas.repec.org/a/cup/jfinqa/v45y2010i03p641-662_00.html)
- [An-Ang-Bali-Cakici (NBER)](https://www.nber.org/papers/w19590.pdf)
- [Muravyev-Pearson-Pollet JF 2022 (RePEc)](https://ideas.repec.org/a/bla/jfinan/v77y2022i3p1787-1828.html)
- [Muravyev-Pearson-Pollet JFE 2025 (RePEc)](https://ideas.repec.org/a/eee/jfinec/v172y2025ics0304405x25001618.html)

### Value review — P2, next (weeks)

**Reasoning**

The facts check out, but the urgency does not. agentic_trader/options/fetcher.py:9,36,67,88 does build display-only GEX from unpinned yfinance chains. alpaca-py 0.44 does expose get_option_chain (alpaca/data/historical/option.py:204-229), and OptionChainRequest takes feed, expiry and strike filters (alpaca/data/requests.py:443-469), so a capture costs a few hundred paced requests a day. Options are deferred as paid source #9 in docs/alpha-data-augmentation-2026-10-18.md §2, row 9.

The headline premise, that this data "cannot be bought back later", is wrong. Daily IV/skew history for US equities can be bought (OptionMetrics, ORATS, Polygon options tiers, CBOE DataShop) and partly found free (DoltHub post-no-preference/options, scraped, from about 2019). Only receipt-timed snapshots on Alpaca's indicative feed are irreplaceable. That matters for live/research feed parity, but it does not justify spending a year waiting.

On the north star: a prospective test after about 250 sessions gives roughly 50 non-overlapping weekly cross-sections. At a realistic post-publication IC of 0.02-0.03, that is t ≈ 1-1.5, the same power trap docs/alpha-data-augmentation-2026-10-18.md §3 ("Power, not search") identifies behind 48+ null fixed-set tests. So the plan as written yields no decision before late 2027.

The proposed 5-session label also differs from the pooled cube's long-bracket label (max_hold_sessions 20, config/research/pooled/campaign-v2.json:30). The option effects cited are mostly monthly long-short (Xing-Zhang-Zhao, An-Ang-Bali-Cakici). The weekly one (Cremers-Weinbaum) decayed after publication and, per Muravyev-Pearson-Pollet, is largely borrow cost. For a long-only desk that leaves a veto or ranking role: skip long cards with steep put skew or expensive puts. That fits the native long cards and is a plausible way to lift the base rate, but it is unproven here.

The "card risk context now" half adds little: entries are already refused near earnings by the earnings blackout rule (agentic_trader/risk/rules.py:376). Swapping IV in for realised vol in book sizing is a sizing change that would need its own test. Replacing the GEX source is a display nicety.

Net: a thin raw capture is cheap insurance worth starting within weeks. The signal question should be answered retrospectively now, not prospectively in 2027. It is not P0/P1 while the insider lane's checks and campaigns are pending and the next alpha source is an operator decision.

**Risks**

1. The indicative feed is derived, not the OPRA NBBO, and quotes for illiquid strikes can be stale or wide. Alpaca's IV and greeks are vendor-computed with unknown dividend and rate assumptions, so the six derived summaries could carry feed artifacts. Validation against an independent source is mandatory, not optional.
2. Storing only the six summaries locks in today's definitions (25-delta skew interpolation, ATM choice, implied-move method). Raw filtered chain pages should be stored digest-pinned, as the other acquisitions are, so summaries can be recomputed.
3. Running the capture inside the production daemon adds load and a new readiness component. A capture failure must never degrade readiness or the scans, and it must stay out of the scan windows, as the corporate-actions worker does (agentic_trader/data/corporate_actions_worker.py:49-50).
4. Coverage is sparse. Many dynamic or PEAD names have thin chains, so the panel will be unbalanced and missingness will correlate with liquidity.
5. The prospective test is underpowered at 250 sessions and invites an extended second look, which raises the garden-of-forking-paths risk.
6. Alpaca could change indicative-feed terms or rate limits, which would break the series mid-stream.
7. Scope creep: the GEX swap and the IV-in-covariance sizing change pull this toward live risk changes without any evidence behind them.

**Prerequisites**

1. Confirm the free plan returns IV and greeks on the indicative feed for single-name equities, not only ETFs. Measure pages per name with the ≤60-day and ±20% filters, and check the rate-limit headroom against the existing bar and news reads.
2. Decide where the capture runs. C20's pit-snapshots worker if it lands; otherwise a research-only job modelled on the corporate-actions worker, with no readiness coupling.
3. Use the digest-pinned raw-page cache convention, recording feed 'alpaca:indicative' and the receipt time.
4. Before any summary is used on a card, run a one-off validation of indicative IV against an independent source (yfinance or Cboe delayed quotes) on about 20 liquid names over a few days.
5. Write a frozen protocol for whatever test eventually reads the data, with its label tied to the cube's bracket label rather than an ad hoc 5-session return, before anyone looks at the data.

**Better alternative**

Answer the signal question retrospectively now and treat forward capture as a cheap side project.

(a) Pull free daily IV and skew history (DoltHub post-no-preference/options volatility_history and option_chain, about 2019 onward; check coverage and point-in-time quality first), or make a one-off paid purchase of ORATS or Polygon historical summaries (30-day IV, skew slope, implied earnings move) for cohort v2's names.
(b) Expose 3-4 dimensionless fields (smirk, call-minus-put IV spread, IV minus realised vol, change in IV) on the existing cohort-v2 cube as a fixed set of ≤8 formulas under checks A and C, using the same lane machinery as the short-volume and insider lanes.
(c) Frame it explicitly as a long-only veto test: does excluding the top smirk or expensive-put decile from native long setups raise the bracket base rate, tested recurrence-style on the replayed setups?

That settles the question in weeks rather than about a year. If it passes, the forward Alpaca capture becomes the live-parity feed and earns priority. If it fails, the capture can stay a dormant raw archive or be dropped.

---

<a id="c38"></a>

## C38 — The book-vol rule is a no-op by construction and sizes against the wrong book

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | sizing_portfolio | defect | medium | M | partially_confirmed |

_lenses: portfolio_

### Claim (as consolidated)

The budget is 0.8% per day of risk capital, or $800 (config.yaml:466-467; book_sizing.py:338). A random full-size five-name long book of $37.5k has a Ledoit-Wolf-predicted daily volatility with median $466 and 95th percentile $767. Only 3.7% of 1,400 draws exceed $800 (6.1% realised), and all six preview factors were 1.00.

The book it measures is also misstated:
(a) It uses raw bars, so splits distort it (C36).
(b) Weights are taken at recorded entry notional, not at marks (book_sizing.py:126-141).
(c) Accepted-but-unfilled GTC entries count as full positions (workflow.py:262-269).
(d) Ratcheted winners keep their original risk_dollars.
(e) The factor is computed on the default tier only and is never rechecked at admission (book_sizing.py:339; risk-policy.md:113-114).
(f) One inner-joined sample covers the book plus every candidate (book_sizing.py:201; covariance.py:43-47).

### Evidence

- config/config.yaml:466-467; agentic_trader/execution/book_sizing.py:126-141, 201, 222, 338-339
- agentic_trader/risk/book_vol.py:88-95; research/setups/covariance.py:20-26, 43-47
- agentic_trader/storage/workflow.py:262-269; agentic_trader/risk/book.py:95; agent/copilot.py:2622
- docs/card-evidence.md:232; docs/risk-policy.md:113-114
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_cov_bias.py

### Recommendation

Re-purpose the rule rather than tune it.

New constraints:
- A per-card cap on marginal risk contribution, in ERC style: x_i(Σx)_i/σ_p ≤ target/N.
- A book ex-ante volatility target that can actually bind, for example 0.5% per day at full deployment.

Fix the book it measures:
- Use split-adjusted bars.
- Mark weights at the last completed close, or from the shared broker snapshot.
- Give native entries a one-session lifetime.
- After a confirmed ratchet, re-price planned risk to max(0, entry − stop).
- Evaluate the max tier, not only the default tier.
- Estimate book names from their own history through the factor model, not an inner join.

Until then, label the card line 'informational'.

### North-star impact

Makes book-aware sizing capable of changing a size, measured against the book that actually exists, before the book fills.

### References

- Maillard, Roncalli & Teïletche (2010), The Properties of Equally Weighted Risk Contribution Portfolios, JPM 36(4) — Order-independent per-position risk budgeting
- López de Prado (2016), Building diversified portfolios that outperform out of sample, JPM 42(4) — HRP as a no-forecast alternative

### Fact check — partially_confirmed

**Corrected claim**

The 0.8%/day budget ($800 on $100k of risk capital; config.yaml:467, book_sizing.py:338) seldom binds for books of five or fewer full-size names. On setup-cache-v1 (2020-04 to 2026-09), a five-name book of 5 x $7,500 has a Ledoit-Wolf-predicted daily volatility with median $466 and 95th percentile $767, and only 3.7% of draws exceed $800. The rule is still not a no-op by construction. The config allows 8 positions and $60k of notional (config.yaml:4, 6, 343), and at 8 x $7,500 about 32% of draws exceed $800, predicted and realised alike (34% for 2022 onward). So the rule binds as the book approaches full deployment. The six preview factors of 1.00 were measured "against a one-share book" (card-evidence.md:232) and say nothing about construction.

Sub-claims:
(a) Raw bars: confirmed (book_sizing.py:207, 222; covariance.py:20-26). It is already a documented limitation (card-evidence.md:262-263).
(b) Weights are recorded notional_value, never marked to market: confirmed (book_sizing.py:126-141). After a fill, sync_entry_executions rewrites notional_value to fill price x filled quantity (copilot.py:2381-2390).
(c) Accepted-but-unfilled entries count as full positions: confirmed in substance. The sizer's book comes from db.get_active_positions, EXECUTED only (copilot.py:785; db.py:859-870). EXECUTED is set on broker acceptance (workflow.py:646), and native entries default to GTC (broker/base.py:98). The cited workflow.py:262-269 is admission's reservation query, not the sizer's input.
(d) Ratcheted stops keep their original risk_dollars: true (db.py:958-976 updates only stop_loss). It does not affect book_vol, which never reads risk_dollars. It affects aggregate_stop_risk (risk/book.py:95; rules.py:270-275), and keeping the original risk is a documented contract.
(e) The factor uses the headline default-tier notional (book_sizing.py:339) and is applied unchanged to every tier (position_sizing.py:280-345). It is never rechecked at admission, by design (risk-policy.md:113-114; book_vol.py:4-5). A tap on the max tier can therefore exceed the budget: confirmed.
(f) One inner-joined sample covers the book plus all ranked native equity candidates: confirmed (book_sizing.py:201; covariance.py:43-47, 67-77).
Not mentioned in the finding: recorded but untapped cards from the same scan also join the book (copilot.py:1565-1582; book_sizing.py:93-107), which overstates it.

**Checked**

- config/config.yaml:2-7, 343, 464-470 (budget 0.008, enforce; cash $100k, max_concurrent_positions 8, max_notional_exposure $60k, max_trade_notional_cap $7,500)
- agentic_trader/execution/book_sizing.py:93-107, 126-141, 197-238, 283-393
- agentic_trader/risk/book_vol.py:1-111
- agentic_trader/research/setups/covariance.py:20-89
- agentic_trader/agent/copilot.py:785, 1204-1225, 1400-1426, 1561-1582, 2366-2390, 2622, 2955-2977
- agentic_trader/storage/db.py:767-800, 859-870, 958-988
- agentic_trader/storage/workflow.py:262-269, 632-648
- agentic_trader/broker/base.py:95-98 (OrderRequest time_in_force defaults to GTC)
- agentic_trader/agent/position_sizing.py:280-345 (scale_sizing scales every tier by one factor)
- agentic_trader/risk/book.py:95; agentic_trader/risk/rules.py:270-275
- docs/card-evidence.md:207-275; docs/risk-policy.md:111-115
- Re-ran the reviewer's pm_cov_bias.py logic on ~/agentic-trader-research/setup-cache-v1 (2020-04-27 to 2026-09-22, same seed): N=5 reproduces median $466, p95 $767, 3.7% predicted / 6.1% realised > $800; N=8 x $7,500 ($60k, the configured full deployment) gives median $689, p95 $1,093, 31.7% predicted / 31.1% realised > $800 (2022+: 34.3%). Script: ~/agentic-trader-research/holistic-review-20261021/c38-check/c38_nbook.py
- WebSearch: Maillard/Roncalli/Teiletche JPM 36(4) 2010 and Lopez de Prado JPM 42(4) 2016 both verified

**Bad references**

- agentic_trader/storage/workflow.py:262-269 cited for (c): this is admission's reservations() query (SUBMITTING+EXECUTED). The book sizer reads db.get_active_positions (copilot.py:785, db.py:859-870), and acceptance-to-EXECUTED happens at workflow.py:646
- agentic_trader/risk/book.py:95 cited for (d): planned_risk() feeds the aggregate_stop_risk rule, not book_vol. The book-vol rule never reads risk_dollars, so (d) is misattributed
- research/setups/covariance.py:20-26, 43-47: the path is agentic_trader/research/setups/covariance.py. The line ranges are correct

**Notes**

The headline "no-op by construction" is overstated. The reviewer's Monte Carlo assumes a five-name book, but the configured full deployment is 8 names and $60k. At that size the same estimator and data exceed the $800 budget in about a third of draws, so the rule is non-binding mainly because the live book is small.

Typical default-tier notional is often below $7,500: the base tier risks $250, so notional is $250 divided by the stop distance, capped at $7,500. Realistic books are therefore smaller than the reviewer's "full-size" books, which supports "rarely binds today", not "cannot bind".

Sub-claims (a), (b), (e) and (f) are accurate. (c) is right in substance but cites the wrong code. (d) is a true fact about the aggregate stop-risk rule, not about book_vol.

Both literature references are correct: Maillard, Roncalli & Teïletche, JPM 36(4):60-70, 2010; López de Prado, JPM 42(4):59-69, 2016 (DOI 10.3905/jpm.2016.42.4.059).

The recommendation's ERC formula, x_i(Σx)_i/σ_p, is the standard Euler risk contribution. Admission skipping the recheck and the raw-bar choice are both documented design decisions, not oversights.

Sources: https://eprints.nottingham.ac.uk/23189, https://papers.ssrn.com/abstract=2708678

### Value review — P2, next (weeks)

**Reasoning**

Split the finding in two. One part is a cheap fix for a real hazard. The other is a redesign that should not be funded yet.

**What the code does now.** `book_vol_factor` (agentic_trader/risk/book_vol.py:88-103) finds the largest f ≤ 1 that keeps the book within budget. The budget is 0.008 × risk capital (config/config.yaml:466-467; execution/book_sizing.py:338). Weights are recorded entry notional (book_sizing.py:126-141). Bars are fetched raw (book_sizing.py:222). The covariance is an inner-join Ledoit-Wolf fit (research/setups/covariance.py:43-47). Enforce has been live since 2026-10-20 (config.yaml:466). During the preview, all six factors were 1.00, but that was measured against a one-share book (docs/card-evidence.md "Book-aware sizing"), so the rule has never been tested on a realistic book.

**Where the finding overstates.**
1. "A no-op by construction" is too strong. A volatility cap that is slack most of the time is a tail guard. Binding 4–6% of the time on random five-name books is roughly what that design implies. The live book can reach 8 names and $60k (config.yaml:4-6), and real cards are momentum-correlated rather than random. Both make the rule bind more often than this probe shows.
2. Point (d), that ratcheted winners keep their original risk_dollars, does not affect this rule. book_vol reads `notional_value`, not risk_dollars (book_sizing.py:136).
3. Point (c), that unfilled GTC entries count as full positions, makes the estimate conservative. Its real fix is the entry-expiry funnel work that is already on the roadmap.
4. The ERC redesign does not deliver ERC here. Cards arrive one at a time and open positions are never resized (maps/portfolio.md G10), so a cap of the form x_i(Σx)_i/σ_p ≤ target/N is just another marginal-risk cap.
5. A binding 0.5%/day target would only shrink every card in a stream that trails SPY. That is the same as lowering per-trade risk, and it does nothing for card quality, which is the north-star bottleneck.

**What is consequential now.** Under enforce, raw bars mean a split inside the 120-session window enters as a −50% to −98% return. Two things follow:
- Ledoit-Wolf shrinks toward mean(diag)·I, so one outlier inflates every name's variance.
- A large forward split on a held name can push vol_before over budget. Then factor = 0 for every non-hedging card (book_vol.py:91-95). Default tiers fall below the minimum order size and the candidates are skipped as `book_sizing_blocked` (copilot.py:1422), for up to 120 sessions. Recent 10:1 and 50:1 large-cap splits are within this range.
- A candidate with its own recent split is shrunk or blocked the same way. The corporate_action rule covers only a few days around the action, not 120 sessions.

This failure removes cards from the north-star stream without any visible error to the operator.

Separately, the max tier is never vol-checked. The factor is computed on the default tier (book_sizing.py:339) and admission does not recheck it (docs/risk-policy.md:113-115).

**What to fund.** Both fixes are small (S effort):
- Make the risk model split-safe. Either fetch `adjustment="split"` for the risk model only, or mask action-date returns using the existing corporate-actions capture.
- Compute the factor on the largest tier the operator can tap.

Ship them with C36. Defer the ERC and vol-target redesign until a measured edge exists. That gives P2 overall, with the split fix done first.

**Risks**

- **Doing the full redesign now.** It adds sizing complexity and journal and card fields to a card stream with no demonstrated edge. A binding vol target mostly shrinks positions, which reduces how fast evidence accumulates without improving card quality.
- **Breaking existing tests.** Changing the estimator (factor model, adjusted bars) alters journaled factor semantics mid-evaluation. It needs a version tag in provenance so preview and enforce factors stay comparable.
- **Contradicting a design choice.** Re-checking at admission or at marks would shrink an offered tier after the fact. docs/risk-policy.md:113 explicitly rules that out ("never refuse or shrink an offered tier"), so it needs an operator decision.
- **Mixing price scales.** Split-adjusted bars for the risk model, alongside raw bars for signals, need care. The fix must not leak into the strategy frames or contradict the "live bars stay raw" contract in docs/corporate-actions.md.
- **Ignoring the hazard.** If nothing is done, a 10:1+ split on a held or candidate name can silently suppress equity cards for months under enforce. The only trace is `book_sizing_blocked` outcomes in the journal.

**Prerequisites**

- **C36 (raw bars and splits) decision.** Decide whether the risk model reads split-adjusted bars, or masks action-date returns from the daily corporate-actions capture. Note that the capture does not cover names it never requested.
- **Max-tier input.** Expose the max-tier notional to `BookSizer` at card construction (book_sizing.py:339).
- **Before any ERC or vol-target redesign:**
  - The desk dollar replay (maps/portfolio.md item 9 / G14): push the 8,768 replayed setups through live sizing, caps and book_vol to get a dollar curve versus SPY.
  - At least one (strategy, direction) cell with positive measured EV after cost.
- **Native entry lifetime (point c).** Belongs to the separate entry-expiry funnel work, not to this item.

**Better alternative**

Make the S-sized fixes now and bundle them with C36.

1. **Split-safe covariance.** Mask, or split-adjust, returns on captured split and reverse-split dates in covariance.py before the Ledoit-Wolf fit. This removes the only path by which enforce can wrongly zero out cards.
2. **Max-tier factor.** Compute the factor on the max tier's notional, so every tier the operator can tap is within budget.
3. **Monitoring.** Add `book_sizing_blocked` counts per day to the cards-outcomes or doctor output so false blocks are visible.

Until step 1 ships, set `book_sizing.mode` back to `preview`. That is a one-line config change, and it gives up nothing, since the rule has never bound. Skip the ERC cap and the binding vol target for now.

If book risk is the concern, two higher-leverage moves exist:
- Measure and journal the book's beta-dollars and sector exposure (maps/portfolio.md G6). The bracket study shows the card stream is long beta with negative excess.
- Run the desk dollar replay against SPY. It answers whether sizing changes anything that matters before money is spent on a better allocator.

---

<a id="c40"></a>

## C40 — Decompose the native trigger against a same-name random-time placebo and a same-session pool, not only against SPY at β=1

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | research_methodology | gap | medium | M | partially_confirmed |

_lenses: quant_

### Claim (as consolidated)

Today the bracket counterfactual's only control is SPY over each trade's own window, at β=1 (outcomes.py:186-219). The setup study compares scorers only within the set of setups.

Neither analysis separates three things:
(i) timing: whether the EMA/RSI trigger picks better sessions than random sessions in the same name with the same geometry;
(ii) selection: whether the chosen names beat the eligible pool;
(iii) β exposure.

The tools already exist. The desk ran a same-name placebo for exdiv (+0.069R against +0.046R), and it holds SIP replays of 8,768 setups (2021–26) and 8,119 setups (2017–21).

### Evidence

- agentic_trader/research/setups/outcomes.py:186-219
- docs/bracket-counterfactual-2026-10-18.md 'Rules' and 'Descriptives'
- docs/apriori-exdiv-2026-10-12.md (same-name placebo design)
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)'

### Recommendation

Run a predeclared decomposition on the existing caches, as a declared second use. It has three paired arms:
- T: the live triggers.
- P: the same symbol and geometry (the trigger's stop as a percentage of price, a 2R target), entered at a random 10:35 or 14:35 instant within ±20 sessions on which the daily trend filter holds.
- U: a random eligible name in the same session, with the same geometry.

Measure ex-ante-β excess, with costs in bps, a floor on the risk unit and a session-block bootstrap.

The timing edge is T−P; the selection edge is P−U. If T−P ≤ 0, stop tuning the trigger and use the native setups only as a candidate set for a cross-sectional ranker.

### North-star impact

Shows which part of the native card stream, if any, adds value, and so whether card slots should keep going to trigger setups.

### References

- Brock, Lakonishok & LeBaron (1992), Simple Technical Trading Rules, JF 47(5) — Bootstrap and placebo nulls for technical rules
- Sullivan, Timmermann & White (1999), Data-Snooping, Technical Trading Rule Performance, and the Bootstrap, JF 54(5) — Technical-rule gains vanish under correct nulls

### Fact check — partially_confirmed

**Corrected claim**

The core claim holds. The bracket counterfactual's only entry-side control is SPY over each arm's own holding window, at a fixed β=1 (outcomes.py:187-219 via runner.py:410-446; bracket_counterfactual.py:177). The setup study's and cards outcomes' 'random' baseline is the mean of the setups themselves (study.py:283-285; outcomes.py:667-674). So nothing separates trigger timing (same name, random time), name selection (the setup's name vs the eligible pool in the same session) and β exposure. The relevant hourly SIP caches exist: primary 8,768 setups (2021-06 to 2026-01) and robustness 8,119 setups (2017-01 to 2021-04) cover the 159-name universe. The labeller (label_bracket) and the session-block bootstrap can be reused.

Three corrections:
(1) The native trigger is not one 'EMA/RSI trigger'. Trend pullback 4h (7,457 of the 8,768 primary setups) uses a daily EMA trend filter plus a 4h EMA-distance and RSI dip/recovery trigger. Squeeze breakout (about 1,311 setups) is a squeeze plus a Bollinger-band break with a volume surge and has no daily trend filter. The P arm's 'daily trend filter holds' conditioning therefore needs a per-strategy definition.
(2) 'The tools already exist' is true of the caches, the labeller and the bootstrap. No placebo or pool-arm generator exists for native setups: the exdiv placebo is a separate apriori module.
(3) Sullivan, Timmermann & White (1999) did not find that technical-rule gains simply 'vanish under correct nulls'. The best rule stayed significant after White's Reality Check in-sample (DJIA 1897-1986) and was not significant out of sample (1987-1996).

**Checked**

- agentic_trader/research/setups/outcomes.py:187-219: market_r is SPY's open-to-close return over the trade's own window, scaled by entry/risk_unit. Its docstring calls it 'a beta-one exposure control, not a tradable return' (uncosted). The cited range is right; the def starts at 187.
- agentic_trader/research/setups/runner.py:403-473: _build_arm_frame uses market_symbol='SPY' only (line 410) and raises if SPY has no bars (421). excess_r = r_cost - market_r (446).
- agentic_trader/research/setups/bracket_counterfactual.py:177 (winsorized excess_r = r_cost - market_r), :182-198 (pair_arms) and :216-231 (paired_contrast): the only entry-side control is SPY. The protocol's sector_etf field (line 97) only validates features; no sector or β-estimated control is computed.
- agentic_trader/research/setups/study.py:283-285: random_selection is the within-session mean of the setups themselves. :569-586 and :715-718 compare scorers against that within-setup random baseline only.
- agentic_trader/research/setups/outcomes.py:667-674: _selection_random in cards outcomes is also the scan's own candidate mean, which is within the set.
- grep for placebo/random-entry/same-name across agentic_trader, docs and config: the only placebo is the exdiv leg (agentic_trader/research/apriori/exdiv_study.py, exdiv_events.py). No timing placebo or eligible-pool control exists for the native setups.
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (8,768 primary setups 2021-06 to 2026-01 and 8,119 robustness setups 2017-01 to 2021-04, from caches setup-cache-v1 and setup-cache-2017), 'Rules' (B1 = paired excess over SPY over the arm's own window) and 'Descriptives' (excess -0.10 to -0.15R): all match.
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)': top-2 per session against a random pick among the setups. Matches 'only within the set of setups'.
- docs/apriori-exdiv-2026-10-12.md 'Result' table (P1 +0.069R; P2 control +0.046R, difference +0.025R, ci90 [-0.026, +0.072]) and docs/apriori-alphas.md:24: match.
- agentic_trader/screeners/strategies.py:53-160 (TrendPullbackStrategy: daily EMA fast/slow filter plus 4h EMA distance and RSI dip/recovery trigger) and :223-330 (SqueezeBreakoutStrategy: squeeze count plus BB break with a volume surge, no EMA/RSI and no daily trend filter).
- docs/alpha-roadmap.md:514-560 and :621-623: no native timing or selection decomposition is planned. The bracket is 'closed'; next work is on the entry side through data sources.
- WebSearch: Brock, Lakonishok & LeBaron 1992, JF 47(5):1731-1764 (bootstrap model nulls: RW, AR(1), GARCH-M, EGARCH). Sullivan, Timmermann & White 1999, JF 54(5):1647-1691 (White's Reality Check over an expanded BLL rule universe).

**Bad references**

_none_

**Notes**

I found no counter-evidence that a timing placebo or an eligible-pool selection control exists for the native setups.
- The pooled mining lanes compute session-paired edges against the eligible cohort, but only for DSL formulas, never for the native StrategyEngine setups.
- The recurrence study compares first and repeat setups within the setup set; that is not a random-time control.

Both references exist and are cited correctly: BLL is JF 47(5):1731-1764, STW is JF 54(5):1647-1691. Their relevance notes are loosely worded:
- BLL's nulls are model-based bootstraps (random walk, AR(1), GARCH-M, EGARCH) rather than 'placebos'. Under those nulls BLL found the rules significant.
- STW's note overstates the result: the best rule survived the data-snooping-adjusted null in-sample (1897-1986) and only failed out of sample (1987-1996).

Neither citation fails to support its use as precedent for bootstrap or placebo nulls on technical rules, so none is listed as bad.

Line citation: outcomes.py:186 is a blank line; market_r spans 187-219.

### Value review — P2, next (weeks)

**Reasoning**

The gap is real. Today the native trigger's only market control is SPY at β=1 over each trade's own window (agentic_trader/research/setups/outcomes.py:186-219; the docstring says "a beta-one exposure control"). The setup study compared scorers only within the set of setups (docs/setup-outcomes-2026-09-23.md, 'Selection (H3)'). No native analysis yet separates timing from name selection from β.

The useful part of the proposal is the T−P contrast. P uses the same name and the same geometry at a random instant, so T−P cancels survivorship and most of β to first order. Every native result so far carries survivorship as a caveat (setup-outcomes 'Caveats: Membership'; bracket-counterfactual 'Caveats': "Current-universe replay flatters longs"). T−P would be the first native contrast free of it. The work is also cheap to build: label_bracket (labels.py:159), the paired session-block bootstrap (bracket_counterfactual.py:204-233) and both SIP caches (8,768 setups in 2021–26, 8,119 in 2017–21) already exist.

I am not funding it above P2, for three reasons.

(1) It changes few decisions. The native stream is already measured as negative after the market: excess −0.110R per setup for A0_live, and −0.10 to −0.15R in every arm and both windows (bracket-counterfactual-2026-10-18.md 'Descriptives' and 'What this says' item 3). Random selection lost in the one-shot holdout (−0.118R) and setup_quality was uninformative (ρ +0.016 in development, −0.012 in the holdout; setup-outcomes 'Selection (H3)' and 'Hypotheses'). The lever that acts on cards is card_policy keyed on excess R, and it is still off (maps/signals.md §6.8, §7.1).

(2) The prior strongly favours T−P ≈ 0 for EMA/RSI triggers on liquid large caps (Brock, Lakonishok & LeBaron 1992; Sullivan, Timmermann & White 1999). The likely result restates what is already known.

(3) The proposed fallback has already failed. "Use natives only as a candidate set for a cross-sectional ranker" was the H3 experiment: the frozen ridge ranker was significantly worse out of sample (top-2 −0.293R against −0.126R for setup_quality; paired ci90 [−0.325, −0.016]). A ranker over native setups is not an untested option.

On β: native picks tilt toward higher-volatility names (vol_20 survived Holm, ρ +0.049), and SPY rose in both windows. A β adjustment is therefore more likely to widen the shortfall than reverse it. It improves attribution but will not rescue the stream.

Where it does pay off: as a cheap gate before anyone funds the native-v2 trigger repair (the RSI dip/recover fix and session-anchored 4h bars; maps/signals.md §7.3). If T−P ≤ 0, that repair is not worth a replay. Native cards then become explicitly workflow-only and are withheld by card_policy, and the slots go to PEAD, catalog legs or a cross-sectional source. Arm U also overlaps with the same-session eligible-universe control already proposed in maps/history.md §7.1, so P is a small add-on to that test rather than a standalone protocol.

**Risks**

- **The placebo can contain the trigger.** Random instants within ±20 sessions overlap T's own hold (mean 5.6 sessions) and other native firings in the same name. P must exclude any instant where a native strategy fires, and any instant inside T's holding window. Otherwise P contains T and T−P is biased toward 0.
- **The geometry is mis-specified.** The trigger's stop sits at a structural, ATR-based level. A fixed stop percentage at a random instant changes what R means whenever volatility differs, so T−P becomes partly a volatility artefact. Match the trigger's stop/ATR14 ratio at the placebo instant, frozen, and floor the risk unit.
- **Researcher degrees of freedom.** "The daily trend filter holds" is undefined for trend-pullback, which also has 4h conditions. So are the number of placebo draws and the seed. All must be frozen in a protocol file committed before labelling.
- **Window reuse.** The 2021–26 development window is now on at least its third use. The 2026-03 → 08 confirmation window is reserved as the bracket counterfactual's declared second use and must not be read. Run this as a descriptive diagnostic, not a gate, so it consumes nothing.
- **U does not cancel survivorship.** U is drawn from the same current 159-name universe, so P−U still carries survivorship bias.
- **Opportunity cost.** It competes with work that moves the card stream directly: card_policy preview on excess R, the L3 selector seam, the PEAD renewal decision on 10-28, and the swing scan spending the native budget. An expected null displacing any of these is a net loss.

**Prerequisites**

- **A frozen protocol JSON, committed before any bar is labelled.** It fixes:
  - the arms T, P and U;
  - the placebo sampler (eligibility rule, exclusion of trigger and hold windows, K draws per trigger, seed);
  - the geometry rule (ATR-matched stop, 2R target, risk-unit floor);
  - the ex-ante β estimator (for example, a 60-session daily OLS against SPY ending at D−1);
  - 5 bp per side, winsorisation at ±10R, and a stationary session-block bootstrap (block mean 10), matching bracket-counterfactual-v1.
- **Reuse of the existing artefacts:** the caches setup-cache-v1 and setup-cache-2017, and the primary and robustness frames under ~/agentic-trader-research/bracket-counterfactual-v1-20261018/.
- **The daily trend condition as a pure function** that can be evaluated at any instant, outside StrategyEngine.
- **Operator agreement on what each outcome triggers, before the run:**
  - T−P ≤ 0 means no native-v2 trigger repair is funded, and native cards are labelled workflow-only and withheld under card_policy.
  - T−P > 0 with P−U < 0 means a spec for a β-hedged native or a native from a better candidate source.

**Better alternative**

Pull the lever directly first. Put card_policy in preview with its threshold on mean excess R per (strategy, timeframe, direction), de-duplicated per setup-session. outcomes.py already computes market_r, so this needs no new study, and it decides whether native cards keep their slots.

If the decomposition is still wanted, do not run it as a standalone protocol with Holm rules. Fold it into the already-proposed same-session eligible-universe replay (maps/history.md §7.1) as a roughly one-day descriptive add-on:
- arms T, P (K=5 ATR-matched placebos) and U;
- paired T−P and P−U with session-block CIs;
- β-excess shown alongside, not as a decision rule;
- no confirmation window read.

Schedule it only as the gate in front of any native-v2 trigger repair.

---

<a id="c42"></a>

## C42 — Macro and regime gates are uncalibrated and fail open: curve inversion suppresses the setup that did best during the only inversion in the replay, and a calendar fetch failure renders 'CLEAR'

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | risk | defect | medium | S | partially_confirmed |

_lenses: portfolio_

### Claim (as consolidated)

Inversion: an inverted 10Y-2Y or 10Y-3M curve alone forces HIGH stress. That means risk ×0.5, squeeze breakouts suppressed and a minimum R:R of 2.2 (macro.py:149-150, 255; config.py:483).

On the already-read 2021–26 frames, squeeze 1h longs inside the 2022-07-06 → 2024-12-12 inversion earned +0.224R (+11.6 bp vs SPY, n 579), against −0.057R outside it (n 610). Halving risk shrinks base notional by only 17% anyway (C12).

Fail-open behaviour:
- The VIX-only fallback keeps risk_multiplier at 1.0 even when VIX is above 30 (regime.py:185-189), while the preflight fails closed if VIX is missing (:169).
- A ForexFactory failure caches an empty list, which renders as 'Lockout verified: CLEAR' (calendar.py:156-160, 216-219). The earnings check says 'Unverified' in the same situation.

No document tests any regime gate, and macro history is latest-only.

### Evidence

- agentic_trader/agent/macro.py:149-150, 255, 298-349; agentic_trader/config.py:483
- agentic_trader/agent/regime.py:169, 185-189
- agentic_trader/agent/calendar.py:156-160, 216-219
- docs/setup-outcomes-2026-09-23.md 'Base rates'
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_inversion.py

### Recommendation

Code fixes:
- Move the inversion branch to preview: journal would_suppress and would_scale instead of acting.
- Map VIX buckets in the fallback through the compound-stress VIX branch.
- Render 'UNVERIFIED (calendar fetch failed)', and add the published FOMC schedule as a static fallback.
- Persist macro observations daily.

Research: run a predeclared replay of the VIX, curve and HY-OAS gates on the frozen setup population, with a session-block bootstrap. If a conditioner is kept, test the momentum-crash state (negative trailing market return plus high realised volatility) instead of inversion.

### North-star impact

Stops an untested heuristic from silently removing the best-measured native setup, and stops the operator and the LLM being told 'CLEAR' when the calendar is unknown.

### References

- Estrella & Mishkin (1998), Predicting U.S. recessions, REStat 80(1) — Inversion forecasts recessions quarters ahead, not one-week trades
- Daniel & Moskowitz (2016), Momentum crashes, JFE 122(2); Barroso & Santa-Clara (2015), JFE 116(1) — Evidence-based state conditioners for trend entries

### Fact check — partially_confirmed

**Corrected claim**

Code now:
- An inverted 10Y-2Y or 10Y-3M curve alone forces HIGH macro stress (macro.py:149-150, 255-259): risk factor 0.5, squeeze breakouts refused (risk/rules.py:458-462, evaluator.py:639), and a minimum R:R of 2.2 (config.py:484, not :483).
- The setup replay applies no regime gate (research/setups/replay.py:282-320).

The inside/outside split comes from the reviewer's script, not the cited doc. On the bracket-counterfactual primary frame (A0_live, long-only, 2021-06-01..2026-01-29), squeeze 1h longs inside the approximate calendar span 2022-07-06..2024-12-12 earned +0.224R (+11.6 bp vs SPY, n 579), against -0.057R outside (n 610). I reproduced these figures. Session-mean SEs are about 0.08 each.

Caveats on the split:
- The span is a hand-set calendar window, not the code's daily rule (^TNX vs ^IRX and FRED DGS2).
- It is not the only inversion in the window: 10Y-2Y inverted briefly in April and June 2022, and 10Y-3M (CMT) re-inverted in Feb–Mar 2025.
- It is one contiguous episode, confounded with the 2023-24 tape.

The 0.5x factor shrinks base-tier notional by about 17% pooled (12% for squeeze 1h), because the $7,500 notional cap binds.

Fail-open:
- get_macro_report raises if any of its 10 series is missing (macro.py:404-411). That includes DXY and the breakevens, which never enter the score. The VIX-only fallback then keeps risk_multiplier 1.0 even above VIX 30 (regime.py:185-189).
- That fallback still suppresses breakouts and lifts min R:R to 2.5 at EXTREME (regime.py:175-180). Only the size cut fails open. A missing VIX fails closed (regime.py:169).
- A ForexFactory exception or non-200 response caches an empty event list for 30 min (calendar.py:187-221). The prompt then reads 'Lockout verified: CLEAR' (calendar.py:156-160), and the card shows 'Cleared' (evaluator.py:708/776, telegram_bot.py:256). The earnings note instead says 'Unverified' (earnings.py:320).

No doc tests the efficacy of any regime gate. card-evidence's regime_context gives only descriptive buckets, and only on cards that already passed the gate.

Macro inputs (yields, OAS, curve regime, drivers) are fetched latest-only and never persisted. However, since 2026-10-14 every scan journals the derived regime block: vix, vix_regime, breakout_allowed, risk_multiplier, min_rr_threshold and macro_stress (regime.py:58-98). So 'persist macro observations daily' is partly done already for outputs.

**Checked**

- agentic_trader/agent/macro.py:149-150: `slope_10y_2y_bps < 0 or slope_10y_3m_bps < 0` gives INVERTED. Confirmed.
- agentic_trader/agent/macro.py:233-259: inversion adds 2 to the score and, on its own, takes the HIGH branch at :255 (multiplier 0.50 at :257, breakout_allowed False at :258, min_rr = cfg.elevated_min_rr at :259). Confirmed.
- agentic_trader/config.py:479-490: elevated_min_rr=2.2 is at :484. Line :483 is vix_watch_threshold. config/config.yaml:281-290 does not override it.
- agentic_trader/agent/macro.py:217-279: the `inflation` argument never enters the score.
- agentic_trader/agent/macro.py:404-411: if any of the 10 series is missing (including DXY and the breakevens, which are unscored), get_macro_report raises.
- agentic_trader/agent/macro.py:298-349: FredDataClient keeps only the latest observation. _fetch_ticker_sync (:281-291) returns only the last close.
- agentic_trader/agent/regime.py:146-189: the macro failure is caught (:148-150). In the VIX-only fallback, :169 raises if VIX is missing, and risk_multiplier stays 1.0 (:185). That fallback still suppresses breakouts at EXTREME (:175) and sets min_rr to extreme_min_rr (:178-179).
- agentic_trader/risk/rules.py:458-462 (regime_breakout) and :249-256 (required_reward_risk); agentic_trader/agent/evaluator.py:639: squeeze breakouts are refused when breakout_allowed is False.
- agentic_trader/risk/capital.py:80-93 and agentic_trader/agent/position_sizing.py:62-160: the macro factor scales both the budget and the base tier.
- Reran pm_inversion.py on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz (A0_live arm, all LONG, 2021-06-01..2026-01-29). Squeeze 1h, inside the span: n 579, +0.224R, +11.6 bp vs SPY. Outside: n 610, -0.057R. Session means +0.202 (se 0.081) vs -0.096 (se 0.072). Reproduced.
- Computed the notional shrink from 0.5x risk with the $7,500 cap and a $250 base tier: 16.6% pooled (squeeze 1h 11.9%, trend 4h 17.1%, squeeze 4h 32%). The ~17% figure matches.
- agentic_trader/research/setups/replay.py:282-320: the replay builds RiskEvaluator but never applies a regime gate, so suppressed setups stay in the population.
- agentic_trader/agent/calendar.py:156-160 ('Lockout verified: CLEAR' when there are no upcoming events) and :187-221 (an exception or non-200 response caches an empty list for 30 min). Confirmed.
- agentic_trader/agent/earnings.py:316-320 returns 'Unverified — earnings calendar unavailable'. Confirmed.
- agentic_trader/agent/evaluator.py:526-545, 682-708, 776 and agentic_trader/notifier/telegram_bot.py:256: macro_clearance=True is set without checking whether the fetch worked, so the card renders 'Cleared'.
- agentic_trader/agent/regime.py:58-98 (REGIME_BLOCK_KEYS / regime_journal_block) and docs/card-evidence.md 'Regime and recurrence context': the derived regime outputs are journaled per scan since 2026-10-14.
- docs/setup-outcomes-2026-09-23.md 'Base rates': squeeze 1h long n 1,185, +0.20R, +0.08 R_cost. Consistent with the frame's pooled (579*0.224+610*-0.057)/1189 = +0.080.
- docs/risk-policy.md:110 and docs/production.md:627: unit tests exist and outcomes are bucketed descriptively. No efficacy study of any regime gate was found in docs/.
- Web search: a 10Y-3M (CMT) re-inversion in Feb–Mar 2025, inside the replay window.
- Literature: Estrella & Mishkin 1998, REStat 80(1):45-61; Daniel & Moskowitz 2016, JFE 122(2):221-247; Barroso & Santa-Clara 2015, JFE 116(1):111-120. All correctly cited.

**Bad references**

- agentic_trader/config.py:483 is vix_watch_threshold (18.0). elevated_min_rr=2.2 is at config.py:484 (off by one).
- docs/setup-outcomes-2026-09-23.md 'Base rates' supports only the pooled squeeze 1h long base rate (+0.20R / +0.08 R_cost). It does not contain the inside/outside-inversion split, which comes only from the reviewer's pm_inversion.py over the bracket-counterfactual frame.

**Notes**

The core of the finding holds, so I could not refute it. The partial verdict comes from the corrections listed below.

Corrections:
1. "The only inversion in the replay" is overstated. The script uses a hand-set calendar window, not the code's daily inversion rule. There were brief 10Y-2Y inversions in April and June 2022, and a 10Y-3M (CMT) re-inversion in Feb–Mar 2025 within the 2021-06..2026-01 window. Whether the code's ^IRX discount-basis 3M would have flagged the 2025 episode is uncertain. The comparison is also one contiguous episode that overlaps the 2023-24 rally, so it shows the gate is untested, not that inversion helps squeezes.
2. The VIX fallback fails open only on size. Breakout suppression and the 2.5 R:R still apply at VIX above 30. The finding understates one aggravating factor: the full macro report is dropped when DXY or a breakeven fetch fails, even though neither enters the stress score. The `inflation` argument is unused in compute_compound_macro_stress. The config flags credit_oas_enabled and inflation_breakeven_enabled are never read; only yield_curve_enabled is used (regime.py:146).
3. "Macro history is latest-only" is true for the inputs but not for the outputs. The scan journal has carried the derived regime and stress block since 2026-10-14.
4. The calendar fail-open is fully confirmed and slightly worse than stated: a non-200 response also caches an empty list for 30 minutes, and the Telegram card shows 'Cleared'. macro_clearance is set to True whether or not the fetch worked.

Numbers I re-derived from ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz match the finding exactly, and the ~17% notional-shrink figure holds (16.6% pooled).

All three literature citations exist and are correctly cited. Daniel & Moskowitz and Barroso & Santa-Clara study cross-sectional WML momentum, so applying them to long-only trend or squeeze entries is a reasonable hypothesis, not established evidence.

The FRED CSV endpoint was under maintenance, so I could not check daily spread values. The 2025 re-inversion is supported by news coverage from the web search.

### Value review — P2, next (weeks)

**Reasoning**

Split verdict. The honesty and fail-open fixes are cheap, real defects and worth doing. The inversion evidence and the proposed regime-gate research are weak, and that part should not be funded.

What the code does now:
- An inverted curve alone forces HIGH stress, which sets ×0.5, turns breakouts off and raises min_rr to 2.2 (agentic_trader/agent/macro.py:149-150, 255-259).
- The VIX-only fallback leaves risk_multiplier at 1.0 (agentic_trader/agent/regime.py:185-189). EXTREME VIX still suppresses breakouts and sets min_rr 2.5 (:175-181), so only the multiplier fails open. The risk map's sizing analysis says the macro multiplier is mostly inert anyway, because the tiers are notional-capped. So this fail-open costs little in dollars.
- A calendar failure is worse. calendar.py:185-219 catches every exception and treats a non-200 response as zero events, with no warning on the non-200 path. It caches the empty list for 30 minutes. get_macro_summary_for_prompt then renders 'Lockout verified: CLEAR' (calendar.py:156-169).
- That text goes into the LLM gate prompt (agent/evaluator.py:682; copilot.py:4252), so the gate that is applied to native cards is told something false. docs/risk-policy.md:331-333 accepts the fail-open lockout but not the false claim. The earnings path already says 'Unverified' (docs/risk-policy.md:119-120), so this is also an inconsistency.
- The feed is an unofficial 'thisweek' scrape (constants.py:126). Fixing the label and the fallback is effort S and touches no risk semantics beyond being honest.

The inversion claim is overstated:
- Inside versus outside 2022-07 to 2024-12 is one episode. It is confounded with the 2023-24 rally, and survivorship flatters the longs (docs/setup-outcomes-2026-09-23.md 'Base rates' says to read them as upper bounds).
- One episode cannot calibrate or condemn a gate. The curve has likely not been inverted since about Dec 2024, so moving the branch to preview would change no card today. It is still the right hygiene, because an untested heuristic should not be a hard gate, but it is not leverage.

Drop the proposed predeclared replay of the VIX, curve and HY-OAS gates, and the momentum-crash conditioner:
- Five years hold about one inversion and a handful of VIX>30 sessions, so power is near zero. After 64 null pooled tests, another low-power protocol is the wrong use of research budget.
- Daniel-Moskowitz's crash state applies to cross-sectional momentum portfolios, not 1h squeeze breakouts.

None of this creates edge, which is the binding constraint on the north star. Bundle it into the next PR that touches risk/ or the evaluator; it does not justify its own workstream.

**Risks**

- **Lockout fallback:** a static FOMC/BLS schedule can drift from reality (rescheduled releases, the release delays of the Oct-Nov 2025 shutdown), so it must be dated and versioned, with a visible as-of.
- **Fail-closed lockout:** making the lockout fail closed on an unverified calendar, instead of only labelling it, would cut card flow during FF outages. The north star favours label-only, plus the static schedule.
- **Inversion to preview:** this removes a de-risking rule with no evidence that it is harmful. That is acceptable only because the curve is likely not inverted now (unconfirmed; check current FRED T10Y2Y and T10Y3M before shipping) and preview journals would_suppress.
- **VIX buckets in the fallback:** mapping them through the compound-stress branch changes min_rr and breakout behaviour in a FRED outage. That needs tests at both the tap and preflight layers, because the gate runs at the scan, the tap and admission (docs/risk-policy.md:105, 191, 225).
- **LLM prompt:** changing the CLEAR text changes the gate prompt. Per operator practice, verify with LangSmith traces and the replay harness before merging.

**Prerequisites**

- Confirm the current 10Y-2Y and 10Y-3M signs, to establish whether the inversion branch is live.
- Fix the per-trade budget so drawdown and macro factors reach the notional ceiling; otherwise the VIX-fallback multiplier fix has almost no dollar effect. The risk map's G4 notes that the macro factor is largely inert on notional-capped tiers, and its recommendation applies dd×macro to the notional ceiling.
- Have a source for the static schedule: the Fed's published FOMC dates and the BLS release calendar for CPI, PPI and Employment Situation, both published about a year ahead.
- Have the trace/replay harness ready for the prompt-text change.

**Better alternative**

- **Make the calendar trustworthy instead of only relabelling its failure.** Make a versioned static official schedule (FOMC plus BLS CPI/PPI/NFP) the primary source, with ForexFactory only as a supplement. The lockout then no longer depends on an unofficial scrape. Render UNVERIFIED only when both are unavailable, and log non-200 responses.
- **Measure the regime gates without a new predeclared protocol.** cards outcomes already decomposes the journaled regime per card (regime_context, CLAUDE.md contract 8), and setups/outcomes.py:126,371 already carries regime_risk_multiplier. Report would_suppress counts and the forgone measured R there descriptively, after inversion moves to preview.
- **Get real de-risking from the sizing fix instead.** Applying dd×macro to the notional ceiling gives macro de-risking actual dollar effect, which matters more than the VIX-fallback multiplier.

---

<a id="c45"></a>

## C45 — Dynamic in-play names compete for the two cards but are unmeasured; keeping the dropped per-bar trade count would make their screen replayable

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | gap | medium | M | partially_confirmed |

_lenses: data_

### Claim (as consolidated)

Each scan adds up to 20 names from Alpaca most-actives (ranked by trade count) and movers (dynamic_universe.py:311-341).

There is no historical screener record, so replay and card base rates cover only the static 159 names. Dynamic names also lack:
- a sector;
- corporate-action coverage (2 of the 9 preview-week blocks were uncovered most_actives names);
- cross-sectional features, which are NaN for them.

Bars do carry trade_count and vwap, but normalisation keeps only OHLCV.

The per-scan dynamic_universe_built journal has recorded the screen prospectively since 2026-09-23.

### Evidence

- agentic_trader/screeners/dynamic_universe.py:311-341; config/config.yaml:259-269
- agentic_trader/market/bars.py:29; agentic_trader/data/providers.py:286-300
- docs/corporate-actions.md 'Entry rule' and 'Known limits'
- agentic_trader/research/setups/ranker.py:245-249; agent/copilot.py:2184-2222

### Recommendation

1. Keep vw and n in the warehouse.

2. Reconstruct a daily historical proxy of both screens over the warehouse universe:
   - top-N by prior-session trade count;
   - top-N by |return| at 10:35, from RTH bars.
   Apply the same select_dynamic and liquidity_gate filters.

3. Run the native strategies through replay on that proxy, as a predeclared comparison of dynamic against static base rate and excess R.

4. Until then, give dynamic-name cards a 'not measured' evidence line and EDGAR SIC sectors.

### North-star impact

About one in nine names per scan is unmeasured yet can take a card slot. The attention literature predicts adverse selection there, and the desk cannot yet check whether that is happening.

### References

- Barber & Odean (2008), All that glitters, RFS — Attention-grabbing stocks underperform after retail buying
- Da, Engelberg & Gao (2011), In search of attention, JF — Attention predicts short-run price pressure and reversal

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding holds, but several details are wrong.

**What is right.**
- Each scheduled suggestion scan adds up to 20 dynamic names (dynamic_universe.py:311-341; config.yaml:264-265). The swing, intraday and manual scans add none (docs/production.md:113-118).
- Dynamic names have no sector (dynamic_universe.py:278-280; config.py:160-168).
- The corporate-actions capture does not cover them (service.py:382). In the preview week, three of the nine blocks were uncovered: two most_actives names and one pead_long drift name. That breakdown is in docs/corporate-actions.md 'Operations' step 4, not in 'Entry rule' or 'Known limits'.
- Their shadow cross-sectional features are NaN (ranker.py:245-253; features.py:262).
- Historical replay populations come from universe.groups only (alpha.py:1180,1248,1336,1452).
- Normalised bars keep only OHLCV (providers.py:289-292), and research caches store OHLCV only (runner.py:170-171).
- dynamic_universe_built has been journaled since 2026-09-23 (#95, 3fcd250).

**What is wrong.**
1. Dynamic names are not wholly unmeasured, and card base rates do not cover only the 159 static names.
   - Every scan_candidates_ranked candidate, dynamic ones included (tagged dynamic/dynamic_source at copilot.py:1893), is labelled by the card_stats worker and by cards outcomes. outcomes.py:148-178 has no dynamic filter.
   - So the card evidence line pools dynamic and static outcomes per (strategy, direction) (card_stats.py:139-141).
   - What is actually missing: a dynamic-vs-static split in card_stats and cards outcomes, and any historical replay of dynamic names.
2. Card slots are bounded. All dynamic names share the correlation group 'dynamic', so at most one dynamic card is issued per session (docs/production.md:176-180). The impact is therefore smaller than the claim that "one in nine names can take a card slot" implies.
3. Per-bar n and vw are not irrecoverably dropped.
   - Lossless raw Alpaca bar pages are retained and never pruned (data/evidence.py:1-5; config.yaml:429-431) for both daemon and research fetches.
   - They are missing from every normalised frame and cache.
   - Related: the DSL already declares a 'vwap' field (dsl.py:29; miner.py:38) that provider-normalised frames never supply.
4. The prospective screen record is incomplete. dynamic_universe_built stores rank, price and percent_change, but discards the most-actives response's cumulative volume and trade_count (dynamic_universe.py:321; SDK ActiveStock fields). The ranking variable itself is not journaled.
5. A daily "prior-session trade count" proxy differs from the live screen. The live screen ranks on current-day cumulative trades as of the scan, so a faithful proxy needs intraday bars with n.

**Checked**

- agentic_trader/screeners/dynamic_universe.py:311-341 (most_actives top=100 by MostActivesBy.TRADES, movers top=50; entries keep only symbol/rank, most_actives price/percent_change=None at :321)
- agentic_trader/screeners/dynamic_universe.py:278-280 (synthetic_contract has no sector)
- config/config.yaml:259-269 (dynamic enabled, max_candidates 40, max_symbols 20); static universe groups = 32+65+62 = 159
- agentic_trader/data/providers.py:280-300 (_normalize_bars keeps only OHLCV columns)
- agentic_trader/market/bars.py:29 (OHLCV tuple)
- .venv alpaca/data/models/bars.py:33-34 (Bar has trade_count, vwap); alpaca/data/models/screener.py:8-20 (ActiveStock has cumulative volume and trade_count)
- agentic_trader/data/evidence.py:1-5 and config/config.yaml:429-431 (lossless raw Alpaca bar pages retained, never pruned); data/market_data.py:86, research/setups/sources.py:33 (evidence store wired for daemon and research fetches)
- agentic_trader/research/setups/runner.py:170-171 (research .npz cache stores OHLCV only)
- agentic_trader/research/alpha/dsl.py:29,186-199 and miner.py:38 (DSL declares a vwap field that provider-normalized frames never supply)
- agentic_trader/research/setups/ranker.py:235-253 (live cross-section population = universe.groups only); research/setups/features.py:262 (non-member symbol -> NaN row)
- agentic_trader/agent/copilot.py:2176-2222 (dynamic_universe_built payload: members with source, rank, price, percent_change; scanned; excluded; threshold)
- agentic_trader/agent/copilot.py:1868-1893 and 1486 (scan_candidates_ranked candidates and card provenance carry dynamic/dynamic_source)
- agentic_trader/research/setups/outcomes.py:148-178 (labeller reads every universe-scope candidate, no dynamic filter); research/setups/card_stats.py:1,139-141 (keys pooled per (strategy, direction))
- agentic_trader/cli/commands/alpha.py:1180,1248,1336,1452 (replay/study populations from universe.groups)
- agentic_trader/cli/commands/service.py:382 (corporate-actions capture = universe.symbols | held | extra_symbols)
- agentic_trader/config.py:160-168 (sector correlation groups built from universe entries only)
- docs/production.md:113-187 (dynamic universe: suggestion scans only, shared 'dynamic' correlation group => at most one dynamic card per session, null cross-sectional features, audit event)
- docs/corporate-actions.md 'Entry rule' (lines 107-110: nine blocks, three uncovered), 'Operations' step 4 (lines 216-219: two most_actives + one pead_long uncovered), 'Known limits' (lines 233-238)
- docs/alpha-roadmap.md:304 and git commit 3fcd250 (2026-09-23, #95) for dynamic_universe_built start date
- Barber & Odean (2008) RFS 21(2) full text (conclusion pp. 812-813, appendix Proposition 1)
- Da, Engelberg & Gao (2011) JF 66(5) (known result: SVI predicts higher prices over the next 2 weeks and reversal within the year)

**Bad references**

- Barber & Odean (2008), All that glitters, RFS: the stated relevance overstates the paper. Its tested result is attention-driven net buying by individual investors on high-volume days, after extreme one-day returns and when a stock is in the news. Underperformance after that buying is only a model prediction (Appendix Proposition 1) and a discussion point ('could temporarily inflate a stock's price, leading to disappointing subsequent returns', p. 812; 'do not generate superior returns', p. 813). It is not an empirically tested result there. Da, Engelberg & Gao (2011) is accurate as cited.

**Notes**

Verdict: partially confirmed. The gap is real: no historical replay covers dynamic names, there is no static-vs-dynamic split in the measured evidence, and they have no sector, no corporate-action coverage and NaN cross-sectional features. The recommendation is sound once the corrections above are applied.

The overstatements:
- Dynamic candidates are already labelled prospectively by card_stats and cards outcomes, pooled per (strategy, direction) with static names. So the right fix is a dynamic split of card_stats and cards outcomes plus a replay proxy. A blanket 'not measured' card line would be wrong, because the pooled line already includes them.
- The one-dynamic-card-per-session cap (shared 'dynamic' correlation group) limits the impact on the north star.
- Raw evidence pages already keep n and vw, so step 1 of the recommendation is mostly about promoting them into normalised frames and caches. Those pages could also feed a historical proxy for the period they cover.
- The journal should also record the most-actives trade_count and volume fields that the code currently discards (dynamic_universe.py:321).

Minor citation issue: the 'two most_actives' figure is in docs/corporate-actions.md 'Operations' step 4, not in 'Entry rule' or 'Known limits'.

The Barber & Odean PDF text used for the reference check was extracted to ~/agentic-trader-research/holistic-review-20261021/c45pdf/bo.txt.

### Value review — P2, next (weeks)

**Reasoning**

The gap is real and I confirmed it in the code. Each scan adds up to 20 attention-screened names: most-actives ranked by trades, plus gainers and losers (agentic_trader/screeners/dynamic_universe.py:311-341; config/config.yaml:259-269). Several things then go missing for those names:
- Bar normalisation keeps only OHLCV and drops trade_count and vwap (agentic_trader/data/providers.py:286-300; market/bars.py:29).
- The shadow cross-section uses static universe symbols only (research/setups/ranker.py:245-249), so dynamic names get NaN features.
- `cards outcomes` has no static/dynamic split. agentic_trader/research/setups/outcomes.py never mentions "dynamic", and the split is still an open WS3 follow-up (docs/alpha-roadmap.md:322).
- In the preview week, 2 of the 3 `uncovered` corporate-action blocks were most_actives cards (docs/corporate-actions.md, preview-week notes around :110 and :218).

So a meaningful share of the card stream is unmeasured. The attention literature cited (Barber & Odean 2008; Da, Engelberg & Gao 2011) does predict adverse selection for longs on extreme movers.

I would still not fund the recommendation as written (warehouse vw/n, then a proxy screen, then a replay study) now. Three reasons:

1. **The decision it informs is small.** All dynamic names share one `dynamic` correlation group, so they are already capped at about one card per session (docs/production.md:176-179; copilot.py:614-617). The replay would measure native long strategies, which already trail SPY on the static names. Whether they trail it a bit more on movers decides at most "keep or cut one card slot". It produces no new edge, and the north star is edge plus flow.

2. **The proxy would answer a different question.** Alpaca's screens are market-wide, include ETFs (VOO was carded as a dynamic name on 2026-10-20 after VTI), and run at query time on IEX/SIP. The proposed proxy ranks a frozen current-membership cohort, and movers are exactly where survivorship bias is worst: the reverse-split and delisted losers are missing. That bias inflates the replayed base rate, and a null or positive replay would still not license the live screen.

3. **Exact-fidelity evidence already exists.** The `dynamic_universe_built` journal (since 2026-09-23) plus the `dynamic`/`dynamic_source` fields on cards and `scan_candidates_ranked` candidates (docs/production.md:181-182) record the real screen. Running the existing outcome labeller over those candidates, split static/dynamic, gives the comparison in days with no new data contract.

The cheap parts of the recommendation (a 'not measured' evidence line and sectors for dynamic names) are worth doing soon, ideally inside the next card-evidence PR. The SIC sectors matter mainly for the correlation-cap leak (VOO after VTI), which is a risk-grouping fix rather than a measurement one. The warehouse-plus-replay part should wait. Fund it only if the prospective split shows dynamic cards are a material and distinctly worse share of flow, and even then a hard rule is cheaper: no dynamic card without N measured outcomes.

**Risks**

- **Survivorship and population mismatch.** A proxy screen over frozen current-membership cohorts misses the delisted and reverse-split losers that dominate real movers lists. The replayed base rate would be biased upward and could wrongly bless dynamic cards.
- **Feed mismatch.** trade_count is feed-specific (IEX against SIP, roughly 30x volume differences per the data map), so a warehouse trade-count rank will not reproduce Alpaca's most-actives order.
- **Cache identity.** Adding vw/n to the normalisation changes the bar schema behind digest-pinned research caches and cube hashes. It must be a new versioned cache, never a mutation of the existing pins, or frozen evidence identities break.
- **Opportunity cost.** It is an M-effort study whose best outcome is cutting one card slot, while the native strategies themselves have no measured edge.
- **Thin sample.** With at most one dynamic card per session, the prospective split will be slow and noisy. Label all journaled dynamic candidates, not just the cards sent.

**Prerequisites**

- The outcome labeller (`cards outcomes`) must be able to read `dynamic` and `dynamic_source` from card provenance and from `scan_candidates_ranked` candidates (both already journaled per docs/production.md:181-182).
- A sector source for non-universe names: EDGAR SIC (the insider lane already has the user-agent plumbing) or the Alpaca asset master or Nasdaq SymDir.
- For any later replay: a new versioned bar cache that keeps vw/n, and a frozen predeclared protocol that addresses survivorship. The protocol could use a point-in-time listing (Nasdaq daily directory snapshots) or restrict the conclusion to "lower bound on adverse selection".
- Operator agreement on the decision rule up front, for example "dynamic cards withheld or preview-only if excess R over static < X after N candidates".

**Better alternative**

Do the prospective measurement first and skip the warehouse replay for now:

1. Add a static/dynamic (and most_actives/movers/operator) split to `cards outcomes`. This is the WS3 follow-up already listed at docs/alpha-roadmap.md:322.
2. Label every journaled `scan_candidates_ranked` candidate since 2026-09-23 with the existing labeller, split by `dynamic_source`, and report excess R against static candidates. That gives the real screen at exact fidelity in days, at S effort.
3. In the same PR, add a 'not measured' evidence line on dynamic cards.
4. Give dynamic names a risk group, so that a broad-index ETF like VOO cannot be carded after VTI. A cheap version maps known index ETFs to a `broad_index` group and uses SIC or the asset-master sector for equities.
5. If the operator wants a guard now, the cheapest option is to make dynamic cards preview-only (journaled, not sent) until the split reaches a predeclared sample floor. That fits the existing `card_policy` preview/enforce pattern.

Only if this shows dynamic cards are a material share that differs from static ones should the versioned vw/n cache and a survivorship-aware proxy replay be funded.

---

<a id="c48"></a>

## C48 — Costs are a flat 5 or 15 bp everywhere, although per-name spread estimates and journaled arrival quotes are free

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | improvement | medium | S | partially_confirmed |

_lenses: data_

### Claim (as consolidated)

Each part of the system applies one flat cost:
- The pooled cube charges a single decision_cost_bps, 15 bp as decisive on v3 (cube.py:86).
- The setup labeller and cards outcomes charge 5 bp per side (outcomes.py:64-65).

Execution already journals the broker quote at submission (workflow.py:606-614), but nothing reads it.

Cohort v3's base rate roughly halved compared with v2 (about +0.18R to +0.08R). Spread costs bite hardest in the mid-caps, short horizons and dynamic names proposed as next steps.

### Evidence

- agentic_trader/research/pooled/cube.py:86
- agentic_trader/research/setups/outcomes.py:64-65
- agentic_trader/storage/workflow.py:606-614
- docs/alpha-pooled-cohort3-2026-10-16.md (base rates)

### Recommendation

1. Add a per-(symbol, session) spread array to the warehouse and the cube, computed with the EDGE estimator on daily OHLC (the bidask package).

2. Calibrate it monthly against sampled Alpaca SIP quotes, live arrival-quote spreads and fill slippage.

3. Charge r_cost as half-spread plus impact, in research labels and on the card EV line.

4. Use the spread estimate, instead of IEX volume, for cohort and dynamic-name eligibility.

### North-star impact

Makes the EV-after-cost on each card and the decisive cost in mining specific to each name, so short-horizon cubes cannot manufacture edge in wide-spread names.

### References

- Ardia, Guidotti & Kroencke (2024), Efficient estimation of bid–ask spreads from OHLC prices, JFE 161 — EDGE works on the daily bars already held (https://doi.org/10.1016/j.jfineco.2024.103916)
- Corwin & Schultz (2012), JF; Novy-Marx & Velikov (2016), RFS — Baseline estimator; costs erase many higher-turnover anomalies

### Fact check — partially_confirmed

**Corrected claim**

Every cost model in the system is a flat, name-independent bps charge, and none is per name.
- The pooled cube carries a single CubeSpec.decision_cost_bps (cube.py:86). It is applied per side, so round trip is 2x (cube.py:294, labels.py:144): 15 bp per side on the cohort-v3 protocols and 5 bp on the v2 protocols.
- cards outcomes and the card-stats worker use DEFAULT_COST_BPS_PER_SIDE = 5.0 (outcomes.py:65).
- Some studies report a small grid of flat costs instead of a single cost: [0, 5] bp per side for setup-outcomes and bracket-counterfactual, 1/5 bp primary/stress for the ETF and panel protocols. The backtest engine uses a flat 2 bp slippage.
- No spread estimator exists anywhere in the code. hl_spread is only a DSL feature.

At final submission, the entry workflow journals the latest quote in the ENTRY_SUBMITTING event and the ENTRY_SUBMISSION audit row (workflow.py:606-615). This is a market-data quote on the configured data feed, which defaults to IEX top-of-book rather than the NBBO (alpaca.py:576-596, config.py:925/1043), and it exists only for cards the operator actually taps. capacity.py reads that quote live for freshness and buying power. Nothing reads the journaled copy back: execution_evidence.py measures fill slippage against the planned entry, not against the arrival quote.

Cohort v3 eligibility already uses SIP dollar volume. Only dynamic-universe eligibility uses the scan feed's (default IEX) dollar volume.

The cohort-v3 doc reports leg means of about +0.08R vs +0.18R at 15 vs 5 bp. It attributes the drop mainly to the band's names lacking the mega caps' drift, not to cost.

**Checked**

- agentic_trader/research/pooled/cube.py:80-91: CubeSpec.decision_cost_bps is a single float. cube.py:294 passes it as cost_bps_per_side to label_bracket. cube.py:104-107: the cube stores only r_gross and r_cost; there is no per-name cost array.
- agentic_trader/research/setups/labels.py:144: r_cost = r - 2*cost_bps_per_side/1e4*entry_price/risk_unit. It is a flat, symmetric per-side bps charge.
- config/research/pooled/cohort3-*.json: decision_cost_bps 15.0. The cohort-v2 protocols (campaign-v2..v4, events-v1, intraday-v1, short-v1, insider-v1) use 5.0.
- agentic_trader/research/setups/outcomes.py:64-65: DEFAULT_MAX_HOLD_SESSIONS = 20 and DEFAULT_COST_BPS_PER_SIDE = 5.0. The constant is used by cli/commands/cards.py:76 and card_stats_worker.py:101,117.
- config/research/setup-outcomes-v1.json and bracket-counterfactual-v1.json: cost_bps_per_side [0.0, 5.0]. These are flat grids, not per-name costs. The ETF/panel protocols use primary 1 bp and stress 5 bp. backtest/engine.py uses a flat equity_slippage_pct of 0.0002 (config.py:499).
- agentic_trader/storage/workflow.py:606-614: the ENTRY_SUBMITTING payload includes broker_context.model_dump(), which contains quote (bid/ask/timestamp/feed) per broker/base.py:69-81 and 213-222. The same evidence also goes to the ENTRY_SUBMISSION audit row at workflow.py:615.
- Readers: the only readers of the quote are execution/capacity.py:234,247,267,292. They use the live context at admission for freshness, identity and buying power, before it is journaled. Nothing reads ENTRY_SUBMITTING or ENTRY_SUBMISSION back; the grep found no other reader. research/setups/execution_evidence.py reads only order_observed and entry_resolved fills, and fill_slip_r is measured against the planned entry, not the quote.
- agentic_trader/broker/alpaca.py:576-596: the entry quote is get_stock_latest_quote on config.alpaca_data_feed. config.py:925 and 1043 default this to 'iex'.
- Grep for corwin/roll/abdi/edge/bidask/half_spread/effective_spread/spread_bps in agentic_trader: no per-name spread estimator found. hl_spread (research/alpha/dsl.py:198) is a raw high-low range used only as a DSL feature.
- screeners/dynamic_universe.py:197-213 and config/config.yaml:259-271: dynamic-name eligibility is a dollar-volume percentile measured on the scan's own feed (default IEX). cohort-v3.json survivorship text: the cohort is ranked on SIP raw dollar volume.
- docs/alpha-pooled-cohort3-2026-10-16.md:13 (cost 15 vs 5 bp) and lines 125-128 ('The long-bracket base rate fell by half. Leg means on v3 are about +0.08R against about +0.18R on v2, at 15 bp instead of 5 bp. The band's names do not carry the mega caps' long drift...').
- Web: Ardia, Guidotti & Kroencke (2024), JFE 161, 103916, doi 10.1016/j.jfineco.2024.103916, verified; the bidask package (PyPI/CRAN, by E. Guidotti) implements EDGE on OHLC. Novy-Marx & Velikov (2016), RFS 29(1):104-147, verified. Corwin & Schultz (2012), JF, is the standard high-low spread estimator and is already cited in the repo's configs.

**Bad references**

_none_

**Notes**

The core of the finding stands: costs are flat and not per name everywhere, the journaled arrival quote is never read back, and no OHLC spread estimator exists. All cited code locations exist and support the claim; the outcomes.py:64-65 range covers the constant at line 65. The paper references are real and correctly described.

Corrections and caveats:
1. Several studies use a grid of flat costs (0/5 or 1/5 bp), not literally one cost. They are still name-independent.
2. The journaled quote comes from the configured market-data feed, which defaults to IEX. An IEX-only top-of-book spread is not the NBBO and runs wider, so calibrating against it would be biased unless the feed is SIP. It also exists only for tapped entries (about 1-2 per day), so the sample is small. The live quote is read at admission (capacity.py), just not for cost.
3. The recommendation's "instead of IEX volume, for cohort" eligibility is wrong for cohorts v2/v3, which are SIP-ranked. It applies only to dynamic-universe eligibility.
4. The cohort-v3 doc explains the base-rate halving mainly by the band's lack of mega-cap drift. A rough estimate (an extra 20 bp round trip on an R of about 4-6% of price is about 0.03-0.05R) suggests cost explains only part of the roughly 0.10R drop. The finding juxtaposes the two without making a causal claim.
5. The "warehouse" in the recommendation does not exist in the code (no matches), so it would be new infrastructure.
6. Adding per-name cost arrays changes the cube's hash identity and the frozen protocol, so it would need new protocol versions. "Effort S" is optimistic for the research side but plausible for the cards-outcomes side.

### Value review — P2, next (weeks)

**Reasoning**

The diagnosis is right. Costs are flat everywhere. `CubeSpec.decision_cost_bps` is one scalar (agentic_trader/research/pooled/cube.py:86, applied at :294). Cards outcomes and the labeller use `DEFAULT_COST_BPS_PER_SIDE = 5.0` (agentic_trader/research/setups/outcomes.py:64-65). Neither is calibrated from execution evidence. For a long-short mid-cap or short-horizon lane, a flat cost flatters formulas that systematically pick wide-spread names, such as reversal_5, illiquidity = ts_mean(hl_spread, 21) and reversal_x_volume (docs/alpha-pooled-cohort3-2026-10-16.md, daily table). Novy-Marx and Velikov describe exactly this failure.

It is still not a north-star lever today, for three reasons.

(1) It can only lower measured edges, never create one. After 64 null formula-cohort pairs and no finalist, the false-positive guard has nothing to guard. The largest v3 t is +1.85 (cohort3 doc, line 116).

(2) The pooled test statistic is a session-paired pick-minus-pool R. A flat bps cost nearly cancels in that difference. A per-name cost moves it only when the formula's picks differ in spread from the pool. So it would not have changed any of the recorded nulls in a positive direction. The v2 to v3 base-rate halving (+0.18R to +0.08R, cohort3 doc lines 125-126) is attributed to the 15 bp cost and to the band's names lacking mega-cap drift. A per-name spread array does not address the second.

(3) On the live side, cards come from a liquid universe. There, quoted spreads are probably at or below 5 bp per side, so the card EV line is more likely conservative than flattering.

Several parts of the claim are inaccurate:
- The arrival quote is read. `EntryQuoteEvidence` holds bid and ask (agentic_trader/broker/base.py:69-80), and capacity uses `quote.ask_price` for exposure (agentic_trader/execution/capacity.py:267, 292). What is true is that nothing reads it for cost calibration.
- Fill slippage is already measured in R (agentic_trader/research/setups/execution_evidence.py:68-80, 250, 278).
- Cohort v3 eligibility is SIP dollar volume ($5M floor; `feed: Literal["alpaca:sip"]` at cube.py:81; cohort3 doc line 13), not IEX volume.
- Effort is M, not S. A new cube array changes `CubeSpec.identity` (cube.py:88-91). That forces new frozen protocol versions and rerun checks A and C, plus a card-EV wiring change and a calibration job.

This is worth doing once the next lane actually targets mid-caps, short horizons or dynamic names, or once any formula passes discovery. Before that, a cheap stress test is enough (see alternative).

**Risks**

- Daily-OHLC spread estimators (Corwin-Schultz, Abdi-Ranaldo, EDGE) are noisy and often zero or negative on liquid names with spreads of a few bp. A per-(symbol, session) value would be spurious precision. It needs at least monthly pooling and would be dominated by volatility noise in large caps.
- Changing the cube spec invalidates every frozen protocol hash and every check A/C result. Introducing a cost model right after a long run of nulls also looks like a forking path. It must be predeclared as a new version, never applied retroactively to closed lanes.
- Calibrating against Alpaca paper fills understates real cost: there is no queue position, and the VRNS stop filled better than its stop price (maps/live.md item 11). The live arrival-quote sample is tiny (about 1-2 cards a day, fewer taps).
- Recent-SIP quote entitlement on the current plan is unverified (CLAUDE.md lists recent-SIP entitlement as an open priority), so "sampled SIP quotes" may be unavailable.
- Replacing the dollar-volume floor with a spread-based eligibility rule changes cohort membership. That is a new cohort and needs its own freeze.
- Opportunity cost: it takes engineering time from the entry-side alpha source search, the one thing the north star actually needs.

**Prerequisites**

- A live lane or candidate where per-name cost is decision-relevant: a formula passing discovery, or a protocol that explicitly targets the 401-1,400 band, short horizons or the dynamic universe.
- Confirmation of SIP quote entitlement on the current Alpaca plan.
- Enough journaled entry quotes and fills (tens, not single digits) to say anything about live cost.
- A new protocol version that declares the cost model before any read, so the change is not applied to closed lanes.

**Better alternative**

Do two cheap things now and defer the infrastructure.

(a) One read-only query (an afternoon). Over all `entry_submitting` events, read the journaled `broker_context.quote` bid and ask (agentic_trader/storage/workflow.py:606-614). Compute the half-spread in bp and in card R (half-spread divided by |entry − stop|). Put it next to the existing `fill_slip_r` from cards outcomes (execution_evidence.py:68-80, 278). If live half-spread plus slippage is at or below 5 bp per side, the card EV line needs no change. Record the result in the next workstream PR.

(b) Add a predeclared finalist stress to the pooled and apriori protocols, using only data already in the cube: a monthly-pooled Abdi-Ranaldo or EDGE half-spread from the cube's own OHLC, with `hl_spread` already derived at agentic_trader/research/alpha/dsl.py:198. Any formula that passes discovery must keep a positive edge when charged its picks' per-name estimated half-spread rather than the flat cost. Report the picks-minus-pool difference in estimated spread as a descriptive column.

This catches the Novy-Marx/Velikov failure for the candidates that matter. It does not change the cube identity, add acquisition, or need monthly calibration machinery. Build the full per-name `r_cost` array only when a lane is designed around mid-caps or short horizons.

---

<a id="c50"></a>

## C50 — The binding research resource is calendar time (5.4 discovery years on Alpaca SIP): buy long survivorship-free history, or borrow it through published factor returns

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | integration | medium | M | partially_confirmed |

_lenses: quant_

### Claim (as consolidated)

Alpaca SIP daily history starts in 2016, so every pooled discovery window is 2016-08→2021-12, about 1,357 sessions. The lane's own research doc says calendar time limits it more than the number of names does.

Paid point-in-time data (Sharadar, Norgate, EODHD) was considered only for survivorship, and only 'if #5 fails'.

t scales with √years, so 1998–2021 would give about 2.1x the t for daily-bar formulas and for the composite.

Published factor returns already hold decades of the evidence the desk is trying to rediscover on 5 years.

### Evidence

- docs/alpha-data-augmentation-2026-10-18.md 'Bars' (daily from 2016) and row 11 (paid point-in-time vendors)
- docs/alpha-pooled-mining-research-2026-09-28.md:119
- config/research/pooled/cohort3-daily-v1.json 'windows'

### Recommendation

1. Price a survivorship-free daily dataset with delisted names and point-in-time membership: Sharadar SEP/SF1, Norgate, or CRSP via WRDS. Use it for daily-feature discovery of the composite from about 1998, and keep hourly bracket labels on Alpaca for the implementation stage.

2. Use published factor returns as priors, decay estimates and hedging factors:
   - OSAP (about 200 predictors with long-short returns);
   - JKP (153 factors);
   - Ken French's daily FF5 and momentum.

3. Set the composite's signs and weights, and the ex-ante yield gate, from these sources.

### North-star impact

Roughly doubles detection power for the composite without new search, so card-source decisions rest on decades of evidence rather than one 5-year rising-market window.

### References

- Chen & Zimmermann, Open Source Asset Pricing — Long-short returns and post-publication periods as priors (https://www.openassetpricing.com)
- Jensen, Kelly & Pedersen global factor data — 153 factors with themes for shrinkage (https://jkpfactors.com)
- Kenneth R. French Data Library — Daily FF5 and momentum for residual labels (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

### Fact check — partially_confirmed

**Corrected claim**

Every pooled protocol declares discovery as 2016-08-01 to 2021-12-31 on Alpaca SIP, with bars from 2016-01-01. That window holds 1,366 sessions (alpha-pooled-checks-v4-2026-10-07.md:87). Daily-formula scores cover about 1,357 of them, and the short-volume lane covered only about 836 (alpha-pooled-short-v1-2026-10-20.md:33-34). The research doc says the lane is limited by 'calendar time and trials, not names' (alpha-pooled-mining-research-2026-09-28.md:119).

Paid point-in-time vendors appear in two places:
- data-augmentation row 11, for survivorship and fundamentals, gated on '#5 fails and #4 proves too messy';
- alpha-expansion-survey-2026-09-23.md:96, for membership.

They have never been considered for extending history before 2016. The 2026-10-20 survivorship spike chose the free EDGAR Form 25/15 route instead of paid data.

No published factor data (OSAP, JKP, French) is integrated. Factor controls use ETF proxies (factor_features.py:335).

The √(24/5.4) ≈ 2.1x gain in t has two conditions:
- It holds only for a redefined daily-bar label. The live cube's bracket labels need Alpaca hourly bars at 10:35 (cube.py:80, 237-277), which Sharadar, Norgate and CRSP do not supply.
- It assumes the edge is stationary. Post-publication decay of anomalies over 1998-2021 makes the effective gain smaller.

'The composite' and an 'ex-ante yield gate' do not exist in the repo. They are proposals from elsewhere in the review.

**Checked**

- config/research/pooled/*.json: every pooled protocol (campaign-v1..v4, events-v1, intraday-v1, short-v1, insider-v1 and the cohort3-* variants) has bars_from 2016-01-01 and discovery window [2016-08-01, 2021-12-31]; cohort3-daily-v1.json 'windows' confirms it
- docs/alpha-data-augmentation-2026-10-18.md:22 (Bars: 'Alpaca SIP history ... daily from 2016') and :52 (row 11: 'Paid PIT universe/fundamentals (Sharadar, Norgate, EODHD) ... survivorship + fundamentals properly ... only if #5 fails and #4 proves too messy'); :66-67, :95 (step 6 'Paid data only on evidence from steps 2-4')
- docs/alpha-pooled-mining-research-2026-09-28.md:119 ('The resource that limits this lane is calendar time and trials, not names') and :94 (says a 5.4-year discovery window is about 1,260 sessions, which understates the real count)
- docs/alpha-pooled-checks-v4-2026-10-07.md:46-53 (1,357 scored sessions per formula) and :87 (the discovery window holds 1,366 sessions)
- docs/alpha-pooled-short-v1-2026-10-20.md:31-35 (short-volume lane scored only about 836 discovery sessions, Aug 2018 to Dec 2021)
- docs/survivorship-spike-2026-10-20.md (#5 ran: bars yes, membership no; recommendation is the free EDGAR Form 25/15 route, not paid vendors)
- docs/alpha-expansion-survey-2026-09-23.md:95-97 (Sharadar/Norgate named only for point-in-time membership)
- grep for pre-2016 or longer-history discussion across docs: none; grep for OSAP/JKP data/Ken French/FF5/CRSP/WRDS in agentic_trader, config, scripts: no integration (CRSP hits in cohort-v3.json are a ticker)
- agentic_trader/research/alpha/factor_features.py:335 and docs/alpha-factor-controls.md:27 (factor controls use ETF return proxies, explicitly not Fama-French factors)
- docs/alpha-pooled-mining-research-2026-09-28.md:57 (JKP is cited only as a shrinkage method, not as a data source)
- agentic_trader/research/pooled/cube.py:1-9, :80 (CubeSpec feed Literal['alpaca:sip']), :237-277, :350 (labels need hourly bars at the 10:35 decision price)
- Arithmetic: sqrt(24/5.42) is about 2.10
- Web: openassetpricing.com, jkpfactors.com (153 factors, 13 themes, 93 countries), Ken French Data Library (lists 'Fama/French 5 Factors (2x3) [Daily]' and 'Momentum Factor (Mom) [Daily]'), Chen-Zimmermann OSAP search (about 205-212 predictors with long-short returns)

**Bad references**

_none_

**Notes**

The core of the finding holds:
- All pooled discovery windows are fixed to the 2016-2021 Alpaca SIP span.
- The lane's own doc names calendar time as the binding constraint.
- Nothing in the code or docs proposes buying pre-2016 history.
- Published factor-return datasets are not used. The JKP paper is cited only for its shrinkage method, and factor controls deliberately use ETF proxies instead of FF factors.

Details it gets wrong:
1. Row 11 lists paid data for 'survivorship + fundamentals', gated on '#5 fails AND #4 proves too messy'. The finding says survivorship only, gated on #5 alone. The expansion survey also named Sharadar/Norgate.
2. The finding omits that #5 has since run (survivorship-spike-2026-10-20.md). It half-failed, and the desk chose the free EDGAR delisting route, so the paid option was never triggered.
3. The window has 1,366 sessions; 1,357 is the per-formula scored count. The research doc's own '≈1,260' (line 94) is low. The short-volume lane had about 836 sessions, not about 1,357.
4. The 2.1x t figure is right as arithmetic, but it does not carry over to the existing statistic. Pooled labels are hourly 10:35 bracket outcomes (cube.py), and none of the suggested vendors supply that pre-2016. The doubling would apply to a daily-label proxy whose link to the implemented label must itself be validated. Anomaly decay (e.g. McLean-Pontiff) also undercuts stationarity across 1998-2021.
5. 'The composite' and 'ex-ante yield gate' refer to other review proposals, not existing system components.

All three URL references resolve and support their stated use:
- OSAP: about 200+ predictors with long-short returns.
- JKP: 153 factors in 13 themes.
- French library: lists daily FF5 (2x3) and daily Mom.

No bad references.

### Value review — P2, next (weeks)

**Reasoning**

Verdict: fund the free half now and defer the paid half. The finding bundles two things of very different value.

What the code and docs show:
- Every pooled protocol uses the same windows: discovery 2016-08-01→2021-12-31, selection 2022-01-03→2023-12-29, confirmation 2024-01-02→2026-07-31 (config/research/pooled/cohort3-daily-v1.json "windows"; the same in campaign-v1..v4, events-v1, short-v1 and insider-v1).
- The desk's own research doc says calendar time and trials, not names, limit the lane (docs/alpha-pooled-mining-research-2026-09-28.md:119).
- Paid point-in-time vendors are row 11, gated behind "only if #5 fails" (docs/alpha-data-augmentation-2026-10-18.md:52, §4 step 6).
- No published factor data is used anywhere. The only factor features say so outright: "Observed ETF return proxies, not ... Fama-French factors" (agentic_trader/research/alpha/factor_features.py:335).
- The √years arithmetic is right: √(24/5.4) ≈ 2.1.

Why buying long history should not be funded now:
(1) The nulls are not near-misses that more power would turn into finalists.
- v4: seven of eight discovery edges were negative, "Nothing here is a near miss" (docs/alpha-pooled-checks-v4-2026-10-07.md:57-58).
- Cohort v3: the best result was t +1.85 among 48 tests, and the only |t|>2 was negative (docs/alpha-pooled-cohort3-2026-10-16.md:116-122). That is what the maximum of a null family looks like.
- The one real discovery pass, shorts_retreat at t +3.40, died in the 498-session selection window at +0.006R (docs/alpha-pooled-short-v1-2026-10-20.md:102-104).
- So the stage that actually decides is selection and confirmation, and those windows are calendar-limited by 2022–2026. Extending discovery back to 1998 does nothing for them unless every protocol is re-frozen with new windows.

(2) Pre-2003 evidence is not stationary.
- Anomalies decay after publication (McLean–Pontiff 2016).
- Few characteristics remain independently significant after 2003 (Green–Hand–Zhang 2017).
- So a 1998–2021 discovery t partly measures dead effects. For a desk choosing a live card source, that is false confidence, not power.

(3) The label would not match.
- The pooled labels are long-bracket outcomes resolved on hourly Alpaca bars. A vendor's daily OHLC cannot order a same-day stop and target, so discovery and selection would score different labels.
- That breaks the shared versioned-bracket contract and is the feed-convention pitfall the desk's own doc names (docs/alpha-pooled-mining-research-2026-09-28.md "Five pitfalls").
- Effort is therefore L, not M: a new vendor feed identity, point-in-time membership from 1998, a daily-label variant, and re-frozen windows.

(4) Survivorship for 2016–2021 is nearly free already.
- SIP serves full bars for delisted names (22/22). Alpaca's inactive list does hold 152–310 liquid dead names a year for 2016–2021, which are exactly the discovery years (docs/survivorship-spike-2026-10-20.md:7-17,26-36).
- An EDGAR Form 25/15 list fills the post-2022 gap.

Why the published-factor half is worth doing:
- It costs no money and takes days.
- Ken French daily FF5+UMD lets `cards outcomes` explain why cards trail SPY by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md) through beta, size or momentum tilt, which is a direct north-star diagnostic.
- OSAP and JKP returns, split by size bucket and pre/post publication, give an ex-ante yield gate. Before freezing another eight-formula set, check whether the literature long leg in liquid, mid-cap names is large enough for 0.1R per trade. Most anomaly premia sit in microcaps and short legs, which a long-only liquid-name card cannot harvest. That gate would have pruned several of the 64 null pairs for free.
- Literature-sourced signs and weights are the right prior for any pre-registered composite.

**Risks**

- Prior leakage. If composite signs and weights come from OSAP or JKP whose sample overlaps 2016–2026, the discovery window is no longer clean. Signs must come from pre-publication or pre-2016 samples, and the journal must record exactly which factor-file vintage was read.
- Unit mismatch. Monthly long-short decile returns, mostly from the short leg and microcaps, do not translate into per-trade R of a long-only bracket. A naive mapping overstates expected yield, so a gate built this way must use long-leg, size-bucketed returns.
- Decay and false confidence. A 1998–2021 pass driven by pre-2003 years would justify a card source that is dead in 2026. Long history should feed shrinkage priors discounted for post-publication decay, not pass/fail discovery.
- Label and feed drift if paid data is bought. A daily-OHLC bracket label is not the hourly SIP label. Adjustment conventions differ between Sharadar and Alpaca, and a different feed identity weakens the research-live parity contract.
- Vendor practicality. Norgate's updater is Windows-only, which does not fit this macOS desk. CRSP/WRDS needs an institutional licence. That leaves Sharadar via the Nasdaq Data Link API (prices from about 1998) or EODHD (cheaper, noisier delisting coverage). Price and licence terms need checking before anything is bought.
- Opportunity cost. A single operator spending weeks re-freezing protocols on new history delays entry-side work and the insider campaigns that are already built (CLAUDE.md, insider lane awaiting approval).

**Prerequisites**

For the free factor part:
- Pull Ken French daily FF5+UMD and the OSAP (Chen–Zimmermann) and JKP factor-return files into a digest-pinned research cache, using the same pattern as `alpha short-volume` and `alpha insider-filings`.
- Add factor attribution to the descriptive `cards outcomes` report, never as a gate.
- Write a one-page ex-ante yield gate: the literature long-leg return in the cohort's size and liquidity band after post-publication decay, converted to expected R per trade, with a minimum to clear before a fixed set may be frozen.

For any paid-history purchase, all of these are needed first:
(a) A pre-registered composite or formula whose discovery edge is positive and literature-signed but underpowered, roughly 1.5 < t < 2.5 on 2016–2021.
(b) A daily-label variant of the bracket whose agreement with the hourly SIP label has been measured on 2016–2021.
(c) Re-frozen window design, with discovery before 2016 and selection and confirmation after, approved by the operator before any data is read.
(d) A licence and price check for Sharadar SEP/SF1, the macOS-compatible option.

Also do the free survivorship repair first, using Alpaca's inactive list plus EDGAR Form 25/15 dates, so the 2016–2021 base rates are honest.

**Better alternative**

Do only the free slice now, which is roughly P1 at about 2–4 days:
(1) Ken French daily FF5+UMD in `cards outcomes`, so card underperformance against SPY is broken down into beta, size, value and momentum exposure rather than reported as one SPY residual.
(2) OSAP and JKP long-leg, size-bucketed, post-publication returns as an ex-ante yield gate and sign prior for any future fixed set or composite, to stop testing families whose best case in liquid names is below the lane's detection floor (about 0.1R paired).
(3) The free survivorship repair for the actual 2016–2021 discovery years: Alpaca inactive bars plus EDGAR Form 25/15 dates. The spike doc misdated discovery as 2021-06→2026-01 (docs/survivorship-spike-2026-10-20.md:41), which undervalued this repair.

Buy Sharadar history only when a literature-signed pre-registered candidate shows a positive, underpowered discovery edge, and use it to lengthen selection and confirmation through re-frozen windows, not only discovery.

---

<a id="c51"></a>

## C51 — Rank the remaining free sources by fit to the traded horizon, persist macro history for gate replay, and skip 13F, Google Trends and ETF flows

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | data | research_direction | medium | M | partially_confirmed |

_lenses: data_

### Claim (as consolidated)

With FINRA short volume closed and EDGAR insider pending, the assessment ranks the remaining sources by information per hour, not by fit to the 20-session label.

FINRA short interest:
- published twice a month for all exchange-listed stocks, on the 7th business day after settlement;
- its API covers only 5 rolling years, so it cannot reach 2016–21.

Macro history:
- Regime and macro gates read only the latest values: yfinance ^VIX and ^TNX, and the latest FRED observation (constants.py:161-173; macro.py:298-349). Replaying the gates needs history.
- FRED's HY OAS series reportedly keeps only 3 years of data since April 2026. This is unconfirmed because FRED was under maintenance.

### Evidence

- docs/alpha-data-augmentation-2026-10-18.md §2 and §4
- FINRA equity short interest page (twice monthly; 5 rolling years via API)
- agentic_trader/constants.py:161-173; agentic_trader/agent/macro.py:298-349

### Recommendation

Admit each source through the FieldSource contract, in this order:
1. Short interest, as days-to-cover and low-short-interest 'good news'. API history from 2021, then prospective.
2. EDGAR events with event-entry designs: SC 13D, 8-K 1.01 and 5.02.
3. Wikipedia pageviews, as an attention veto for dynamic names. Do not use Google Trends, whose re-sampling breaks digests.
4. Macro history for gate replay: the CBOE VIX CSV; FRED DGS2, DGS10 and T10Y3M; and BAA10Y as a full-history substitute for HY spreads. Persist these daily.

Skip:
- 13F: quarterly, with a 45-day lag.
- ETF flows: no free point-in-time source.
- Index-inclusion history: paid.

### North-star impact

Keeps data work aligned with the horizon and the long-only constraint, and makes the uncalibrated macro gates testable.

### References

- FINRA Equity Short Interest — Free twice-monthly short interest; API limited to five years (https://www.finra.org/finra-data/browse-catalog/equity-short-interest)
- Hong, Li, Ni, Scheinkman & Yan (2015), Days to cover and stock returns; Boehmer, Huszar & Jordan (2010), The good news in short interest, JFE — Long-only-usable short-interest predictors
- Brav, Jiang, Partnoy & Thomas (2008), Hedge fund activism, JF — 13D filings as a timestamped event
- FRED BAMLH0A0HYM2 — Reported three-year history limit; verify before use (https://fred.stlouisfed.org/series/BAMLH0A0HYM2)

### Fact check — partially_confirmed

**Corrected claim**

The code claims are correct. The regime gate and the macro gates use only the latest yfinance close and the latest FRED observation (constants.py:160-173; macro.py:283-349,384-397; regime.py:103-166). Nothing stores the input history: the journal keeps only VIX and the gate outputs per scan, starting 2026-10-14 (regime.py:58-98). Replay applies no regime gate (research/setups/replay.py:286-313), and engine.py:135 accepts vix_df but no caller passes it, so the VIX thresholds and the macro gates cannot be tested historically. Persisting daily macro history is a valid gap.

The FINRA facts are correct: twice a month, published on the 7th business day after settlement, API history of five rolling years. One correction: coverage includes OTC equities as well as exchange-listed ones. The HY OAS three-year limit is supported only by secondary reports, as the finding itself says.

Four details need correcting:
- The assessment does not rank 'not by fit to the 20-session label'. §2 ranks by information per hour 'for the desk's actual label (a 20-session long bracket)' and includes a horizon/literature-prior column.
- The doc already says to skip 13F (#8, 'weak at 20 sessions'), and index inclusion is already skipped as paid (corporate-actions.md:230; alpha-roadmap.md:576). The genuinely new skips are Google Trends and ETF flows.
- The doc already lists short interest as #7 ('fold into 1'), but it was never folded in: short_fields_v1 is Reg SHO volume only. Promoting it to its own days-to-cover lane is a real but incremental change.
- The 'FieldSource contract' does not exist in the codebase. Each lane has its own inputs document (null_check.py:270-273; campaign_run.py:242-251), so that contract would have to be built first.

**Checked**

- agentic_trader/constants.py:160-173: the VIX/TNX/DXY/IRX/FVX/TYX tickers and the FRED series DGS2, BAMLH0A0HYM2, T10YIE and T5YIE match the citation.
- agentic_trader/agent/macro.py:283-293: yfinance history(period="5d") returns only the last close. macro.py:296-349 is FredDataClient.fetch_latest_value, which keeps only the most recent valid observation in an in-memory TTL cache. macro.py:384-397 is the engine's latest-only gather.
- agentic_trader/agent/regime.py:103-113 and 159-166: the RegimeDetector also reads the latest VIX/TNX/DXY close. regime.py:58-98: regime_journal_block persists VIX, the regime bucket and the gate outputs per scan, but not the yield, OAS or DXY inputs.
- agentic_trader/agent/evaluator.py:393,400,495,639 and copilot.py:3444,3540,4001,4016,4018 are the live consumers of breakout_allowed, min_rr_threshold and risk_multiplier.
- agentic_trader/research/setups/replay.py:286-313: the replay builds a RegimeDetector but calls only calculate_levels_deterministic, so no gate is replayed.
- agentic_trader/backtest/engine.py:135,561: the backtest accepts vix_df, but no caller passes it.
- No FieldSource class, protocol or doc exists in agentic_trader/ or docs/. Each lane has its own inputs document (research/pooled/null_check.py:270-273; campaign_run.py:242-251).
- docs/alpha-data-augmentation-2026-10-18.md §1 line 24: macro is listed as 'latest only', and ALFRED vintages and journal history as not available.
- docs/alpha-data-augmentation-2026-10-18.md §2: sources are ranked 'by expected information per hour of work, for the desk's actual label (a 20-session long bracket ...)'. The table has a literature-prior/horizon column. #7 is short interest ('fold into 1'). #8 is 13F ('weak at 20 sessions', skip).
- docs/alpha-data-augmentation-2026-10-18.md §4: lane #1 is closed and lane #2 (insider) is pending, which matches the finding.
- config/research/pooled/campaign-v2.json:30 and events-v1.json:14: max_hold_sessions 20 confirms the 20-session label.
- grep for short interest and days-to-cover in agentic_trader: no hits. short_fields_v1 holds Reg SHO volume fields only, so short interest was never folded in.
- docs/corporate-actions.md:230 and docs/alpha-roadmap.md:576: index inclusion is already skipped as paid.
- FINRA equity short interest page (WebFetch): twice a month; published on the 7th business day after the reporting settlement date; covers exchange-listed AND OTC equities; the API reaches back up to five rolling years.
- FRED BAMLH0A0HYM2 page (WebFetch): maintenance notice only. A WebSearch found secondary reports (eco3min, a Reddit snapshot) of a rolling three-year window from late April 2026; there is no official notice.
- FRED BAA10Y (WebSearch): an active daily series, no discontinuation found.
- The cited papers exist as described: Hong, Li, Ni, Scheinkman & Yan 2015 (NBER WP 'Days to Cover and Stock Returns'); Boehmer, Huszar & Jordan 2010 JFE 'The good news in short interest'; Brav, Jiang, Partnoy & Thomas 2008 JF 'Hedge fund activism'.

**Bad references**

_none_

**Notes**

The core finding holds: the macro and regime gates are latest-only with no history, there is no historical gate replay, and the FINRA short-interest timing and API limits are as stated. The recommendation to persist macro history (CBOE VIX history; FRED DGS2/DGS10/T10Y3M/BAA10Y) addresses a real gap that docs/alpha-data-augmentation-2026-10-18.md §1 names but §4 never schedules.

The framing overstates novelty in three places:
- The 'information per hour, not horizon fit' contrast misreads §2, which explicitly ranks for the 20-session label.
- The 13F and index-inclusion skips are already recorded in the docs.
- Short interest was already ranked #7 ('fold into 1'), even though it was never implemented.

The 'FieldSource contract' it cites does not exist yet; it is probably a proposal from another finding.

On the HY OAS limit: if FRED truncated the series, pre-truncation ALFRED vintages may still hold the full history. That is worth checking before falling back to BAA10Y.

No citation is fabricated. The FRED HY OAS page was under maintenance, and the finding correctly marks that claim as unverified.

### Value review — P2, next (weeks)

**Reasoning**

I would fund only one part of this bundle, the macro-history gate replay. The rest is low-leverage source shopping, and it misstates the assessment it critiques.

1. The premise is wrong. The finding says the doc ranks sources "by information per hour, not by fit to the 20-session label". docs/alpha-data-augmentation-2026-10-18.md §2 ranks them "by expected information per hour of work, for the desk's actual label (a 20-session long bracket ...)". It already places FINRA short interest as row 7, "fold into 1", and 13F as row 8, "skip".

2. New source lanes are poor value now.
   - Short interest is the slow sibling of the FINRA short-volume lane. That lane closed on 2026-10-20 with no finalist (docs/alpha-pooled-short-v1-2026-10-20.md).
   - The 5-year API window starts in 2021, but the pooled cube reads from 2016-01-01 (docs/alpha-pooled-mining.md, cube section, ~line 195). The frozen discovery/selection/confirmation split cannot be reused, so a weaker protocol would follow.
   - The "good news in short interest" and days-to-cover effects are monthly-horizon results that concentrate in small caps. Cohorts v2 and v3 are liquid.
   - SC 13D filings are rare in a 400–1,400-name liquid band, and most of the effect lands at announcement. 8-K items 1.01 and 5.02 have weak priors.
   - The finding also drops the doc's actual EDGAR priority: 8-K item 2.02 timestamps for PEAD v2 (§2 row 3). PEAD is the one event anomaly with a positive probe here.
   - Wikipedia pageviews as an attention veto is a weak, untested idea.
   - After 64 null formula–cohort pairs, another weak-prior lane mostly spends operator approvals.

3. The macro part bears directly on the north star. The regime and macro gates are hard, uncalibrated rules that decide which cards appear and on what terms:
   - VIX buckets set breakout suppression and min_rr (agent/regime.py:172-189).
   - An inverted curve alone forces HIGH stress, halving risk and suppressing breakouts (agent/macro.py:255-259, per maps/risk.md G6).
   - No protocol has tested them.

   It is also nearly free:
   - FredDataClient already downloads the full fredgraph.csv history on every call and keeps only the last line (agent/macro.py:308-345).
   - The setup replay already runs the unchanged live engine over 2021–2026 (research/setups/replay.py:286-292), giving a population to condition on.
   - Prospective values are already journaled per scan in the `regime` block since 2026-10-14 (docs/card-evidence.md, 'Regime and recurrence context'). So daily persistence in the daemon is redundant; a one-shot, digest-pinned research acquisition is enough.

4. The skip list (13F, Google Trends, ETF flows, paid index-inclusion history) is correct and costs nothing. Record it as a decision; it needs no funding.

**Risks**

- **Little power in the gate replay.** 2021–2026 holds about 1–3 independent stress episodes: the 2022 VIX>30 spells, the long 2022–24 inversion and the April 2025 spike. A session-block bootstrap will probably return 'inconclusive'. The protocol must say beforehand what action follows each outcome, for example: remove the curve-inversion→HIGH rule as unsupported by evidence, or keep it as an operator risk preference. Otherwise the replay is theatre.
- **Loosening is a risk-policy change.** Relaxing a gate after the replay changes risk in exactly the stress case. It is an operator decision and should go through preview before enforce, as with book_sizing and corporate_action.
- **FRED data rights.** The HY OAS (BAMLH0A0HYM2) history limit is unverified. Using BAA10Y as a substitute changes the stress score's input, which needs a new frozen rule, not a silent swap. VIXCLS, DGS2, DGS10 and T10Y3M are full-history and unrevised, so there is no vintage issue.
- **Source lanes.**
  - The short-interest window (2021+) forces a new, weaker protocol.
  - 13D and 8-K events need event-entry plumbing that does not exist: there is no CatalogCandidateSource (maps/signals.md:186).
  - The FieldSource registry is only proposed (maps/pooled.md §9), so each new source today is another subclass and isinstance ladder.

**Prerequisites**

For the macro gate replay:
- A short frozen spec in the recurrence style, declaring the gates under test, the bucket rules taken unchanged from config, the population (the existing replayed native setups), the paired statistic and the actions for pass, fail and inconclusive. Declare it before reading any history.
- A research-only, SHA-256-pinned acquisition of FRED VIXCLS, DGS2, DGS10 and T10Y3M (plus BAA10Y or HY OAS after verifying availability).
- Confirmation that the setup replay applies no regime filtering, so gated and ungated sessions both appear in the population.

For any further field source:
- The FieldSource registry refactor (maps/pooled.md §9).
- The insider lane finishing first: checks A and C are running now, and the campaigns need approval.
- For any event-entry design, a CatalogCandidateSource protocol.

**Better alternative**

Split the finding and fund only the cheap, decisive part.

1. **Run a regime-gate replay now (about one SDD day).**
   - Pull full FRED history for VIXCLS, DGS2, DGS10 and T10Y3M once into a digest-pinned research cache. FRED's VIXCLS replaces the CBOE CSV, so one client covers everything; the existing fredgraph.csv path already returns the full history.
   - Join it by date to the replayed setup population.
   - Test VIX-bucket breakout suppression, the min_rr bump and the inversion→HIGH rule under a frozen protocol, merged with risk map item 6 (maps/risk.md:162-163).
   - Do not add daily persistence to the daemon: the scan journal's `regime` block already records the prospective values.

2. **Keep the assessment's EDGAR order.** Finish the insider lane, then take EDGAR 8-K item 2.02 timestamps and PEAD v2 (doc §2 row 3). 13D, 8-K 1.01/5.02 and Wikipedia come after that.

3. **Defer short interest.** Do it only if insider or PEAD v2 produces a finalist that a slow short field could condition, not as its own lane.

4. **Record the skip list** (13F, Google Trends, ETF flows, paid index-inclusion history) in docs/alpha-data-augmentation-2026-10-18.md §2, bundled with the next workstream PR.

---

<a id="c53"></a>

## C53 — A single unmodelled ledger activity or partial fill blocks every card and entry

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P2 | next (weeks) | execution | defect | medium | M | partially_confirmed |

_lenses: architecture_

### Claim (as consolidated)

Unknown activities block everything:
- The ledger recognises only FILL, CSD/CSW/JNLC, FEE/CFEE/DIVFEE and DIV/INT (ledger.py:18-23, 134-140).
- Any other activity becomes an issue, so the report is not ready and current_risk raises (accounting/risk.py:88-95).
- Every scan candidate is then dropped as 'account risk unavailable' and every entry is refused (copilot.py:1724-1740).
- The full-history import re-reads the activity on each refresh, so recovery needs a code change.

Partial ownership blocks everything too:
- A partial fill, an untracked position or an unrelated order blocks entries in all symbols (capacity.py:103-112, 213-216).
- The drawdown gate depends on complete realised-P&L classification.

Whether such activity occurs in the paper account is unverified.

### Evidence

- agentic_trader/accounting/ledger.py:18-23, 134-140
- agentic_trader/accounting/risk.py:88-95
- agentic_trader/agent/copilot.py:1724-1740
- agentic_trader/execution/capacity.py:103-112, 213-216

### Recommendation

Separate the two kinds of readiness:
- Equity and drawdown readiness needs only equity and external flows.
- Realised-P&L readiness needs every activity classified.

Treat an unknown cash-bearing activity conservatively, as an external inflow that overstates drawdown, instead of blocking.

Scope partial-fill and untracked-position blocks to the affected symbol, reserving worst-case risk for it and failing closed within that scope.

### North-star impact

Keeps the card stream alive through routine corporate actions and partial fills without weakening protection.

### References

_none_

### Fact check — partially_confirmed

**Corrected claim**

Fail-closed coupling of the ledger and the entry gate (documented, intentional policy, not an accidental bug):

- The ledger reconciles only FILL, CSD/CSW/JNLC, FEE/CFEE/DIVFEE and DIV/INT (ledger.py:20-23, 113-138).
- Any other activity type, a malformed row, a cash mismatch over one cent, a quantity mismatch, a non-us_equity position, or history longer than max_pages*100 activities (alpaca.py:336) makes the report not ready (ledger.py:60-61, 137-156).
- Account risk then fails on both paths: advance_risk_checkpoint sets risk_error (risk.py:170, 218-219), and require_risk_checkpoint raises (risk.py:228-230, 92-93).
- In Alpaca mode, every non-dry scan candidate is dropped as 'account risk unavailable' (copilot.py:1725-1740), so no card is sent. Every queued entry is refused at preflight (entries.py:120-128; the claim cites copilot.py for this, but that code is in entries.py).
- Every refresh re-imports the full activity history (alpaca.py:314-336), and no acknowledge or override path exists. An unsupported activity therefore blocks permanently until the code learns that activity type.
- A JNLC added or revised after the baseline is worse. It sets a persistent risk_invalidation (risk.py:184-187, 214-215). The docs require a 'reviewed recovery' for it (docs/account-ledger.md:126-129), but the code has no path for one.

Partial ownership has a narrower effect than the title implies:

- At admission, _exposure checks every reservation and every holding, whatever symbol is being entered (capacity.py:79-217). A partial entry fill (103-104), a partial exit fill (111-112), a broker-only holding (213-214) or a non-terminal unrelated order (215-216) therefore refuses entries in every symbol. This matches the documented contract (docs/entry-capacity.md:85-89).
- These checks run only at admission (entries.py:103). They do not stop card generation, except for one case: a partial entry on a signal with an alpha lifetime policy triggers a lifetime review that halts trading (lifetimes.py:121-122; storage/lifetimes.py:257-276). A halt blocks scans (copilot.py:766).

How likely each block is in the paper account:

- Dividend-type activities are unlikely, because Alpaca paper does not simulate dividends.
- Partial fills are a realistic trigger. Alpaca paper gives random-size partial fills on 10% of eligible orders, so the partial-ownership block is the more likely of the two.
- The drawdown equity itself needs only the snapshot (risk.py:115-118). The coupling to full realised-P&L classification sits entirely in _validated_checkpoint.

**Checked**

- agentic_trader/accounting/ledger.py:19-25 (FILL, CASH_TRANSFERS, FEES, INCOME sets)
- agentic_trader/accounting/ledger.py:60-61 (LedgerReport.ready requires no issues)
- agentic_trader/accounting/ledger.py:126-140 (anything else appends unsupported_activity_issue; malformed rows become 'Invalid activity')
- agentic_trader/accounting/ledger.py:146-156 (cash mismatch, quantity mismatch and non-us_equity positions also add issues and withhold gross)
- agentic_trader/accounting/ledger.py:71-89 (corporate-action text only explains the issue and does not resolve it)
- agentic_trader/accounting/risk.py:83-93 (_validated_checkpoint raises when the report is not ready)
- agentic_trader/accounting/risk.py:162-219 (advance_risk_checkpoint keeps the old risk but sets risk_error)
- agentic_trader/accounting/risk.py:184-192, 214-215 (a JNLC added or revised after the baseline sets risk_invalidation, which persists)
- agentic_trader/accounting/risk.py:222-244 (require_risk_checkpoint raises on risk_error or invalidation, then re-runs _validated_checkpoint)
- agentic_trader/accounting/service.py:54-98 (refresh commits the report even when it has issues; current_risk calls require_risk_checkpoint)
- agentic_trader/storage/ledger.py:99-104 (commit calls advance_risk_checkpoint)
- agentic_trader/broker/alpaca.py:309-336 (every refresh re-reads the full activity history in ascending order; more than max_pages*100 activities raises)
- agentic_trader/config.py:547-551 (AccountingConfig max_pages=100)
- grep for an acknowledge/waive/override/reset of risk_invalidation in agentic_trader: none outside accounting/risk.py
- agentic_trader/agent/copilot.py:1725-1740 (in Alpaca mode a current_risk ValueError drops the candidate as 'account risk unavailable')
- agentic_trader/risk/capital.py:29-31 (requires_account_risk is True only when execution_mode is ALPACA)
- agentic_trader/execution/entries.py:117-138 (preflight refresh plus current_risk; an exception refuses the entry)
- agentic_trader/agent/copilot.py:4041-4047 (the ACCOUNTING readiness worker fails when the report is not ready, so the block is visible)
- agentic_trader/execution/capacity.py:79-217 (_exposure iterates every reservation and holding regardless of the request symbol; lines 103-104 partial entry, 111-112 partial exit, 213-214 untracked holding, 215-216 unrelated order)
- grep for callers of assess_entry_capacity: only execution/entries.py:103 (admission), not the scan
- agentic_trader/execution/lifetimes.py:121-122, 203-218 and agentic_trader/storage/lifetimes.py:257-276 (a partially filled entry on a lifetime-policy signal triggers a review that halts trading)
- agentic_trader/agent/copilot.py:766-774 (a halt blocks scans)
- docs/account-ledger.md:30-43 (supported activity list; unknown activity withholds totals)
- docs/account-ledger.md:118-147 ('Missing, stale, failed or unsupported risk evidence blocks new entries')
- docs/entry-capacity.md:80-89 ('Any exit fill, partial ownership, broker-only holding, unrelated order or missing evidence blocks new risk until reconciliation/review')
- docs/corporate-actions.md:148-157 (the ledger explains and does not resolve)
- https://docs.alpaca.markets/docs/paper-trading (paper 'does NOT simulate dividends'; partial fills of random size occur '10% of the time')

**Bad references**

_none_

**Notes**

The code behaves as the finding describes. Three things are wrong or overstated.

1. **It is not a defect.** This is documented, deliberate fail-closed policy. docs/account-ledger.md:146-147 says unsupported risk evidence blocks new entries. docs/entry-capacity.md:85-89 says partial ownership, a broker-only holding or an unrelated order blocks new risk until review. CLAUDE.md contract 2 says partial/ambiguous ownership stays blocked. The finding is better framed as a robustness and availability trade-off than as a bug.

2. **The title overstates the partial-fill effect.** A partial fill blocks entries in every symbol, but card generation only through the lifetime-review halt, which applies to alpha-policy signals. The capacity check itself runs only at admission.

3. **One citation is misplaced.** The claim that every entry is refused cites copilot.py:1724-1740, but that code only drops scan candidates. The entry refusal is at entries.py:117-138. The ledger citation (18-23) includes a blank line and the Money alias, and the sets are at 20-23; that is fine.

**Additional evidence that strengthens the finding:**
- A JNLC change after the baseline permanently invalidates risk, and there is no recovery tool (grep found no reset path).
- More than 10,000 activities at the default max_pages also blocks everything (alpaca.py:336).
- The ACCOUNTING readiness component fails visibly (copilot.py:4041-4047), so the block would alert rather than stay silent. This mitigates the impact.

**Recommendation caveat:** treating an unknown cash amount as an external inflow is conservative only when the amount is positive. A negative unknown amount booked as an outflow would understate drawdown. The fix must be sign-aware: book positive amounts as external inflows and negative amounts as losses.

**Paper-account evidence (Alpaca docs):** dividends are not simulated, so DIV-family blocks are unlikely. Partial fills of random size happen on 10% of eligible orders. A partially filled entry that later expires or is cancelled keeps 0 < filled < quantity, which persistently trips capacity.py:103. The partial-fill path is therefore the more realistic trigger.

The finding listed no references, so none needed web verification.

### Value review — P2, next (weeks)

**Reasoning**

The defect is real, and it is a single point of failure that can stop the card stream for good. It is still a latent availability risk, not an edge problem, and the binding constraint on the north star is edge: 64 null formula-cohort pairs, and cards trailing SPY (docs/alpha-pooled-short-v1-2026-10-20.md; docs/bracket-counterfactual-2026-10-18.md). So it is worth funding, but it does not outrank the entry-side alpha work.

What I verified in the code:
- **Unknown activities.** `reconcile` classifies only FILL, CSD/CSW/JNLC, FEE/CFEE/DIVFEE and DIV/INT (agentic_trader/accounting/ledger.py:18-23). Any other type becomes an issue (:134-140), and the cash difference it causes adds a second issue (:146-148). `_validated_checkpoint` then raises on `not report.ready` (agentic_trader/accounting/risk.py:92-93). In Alpaca mode the scan drops every candidate as "account risk unavailable" (agentic_trader/agent/copilot.py:1724-1740; `requires_account_risk`, agentic_trader/risk/capital.py:29-31). Each refresh re-reads the full history, so one such activity keeps the stream dead until a code change.
- **No operator recovery path.** The sticky `risk_invalidation` (agentic_trader/accounting/risk.py:163-168, :215, :228-229) has no CLI to clear it or re-baseline. A revised JNLC is a second route to a permanent block.
- **Partial fills and untracked exposure.** These block admission in every symbol (agentic_trader/execution/capacity.py:103-104, :111-112, :213-216). A partial timed entry returns REVIEW (agentic_trader/execution/lifetimes.py:121-122). This path stops taps, not card generation, so it hurts the north star less than the finding suggests.

Corrections to the recommendation:
1. **"Unknown cash = external inflow" is conservative only for credits.** An unknown debit treated as an external outflow takes a real loss out of adjusted equity. That understates drawdown (risk.py:192-194 computes `adjusted_equity = equity - (cash_flows - baseline_flows)`). The rule must depend on the sign: credits count as external flows, debits count as P&L losses.
2. **Unknown share-bearing activities need a symbol quarantine, not a cash rule.** SPLIT, SPIN, MA and NC change quantities, so cash treatment does not fit them.
3. **Symbol-scoped partial-fill handling changes a written contract.** CLAUDE.md contract 2 says partial or ambiguous ownership stays blocked. Bracket exits activate only after a complete entry fill (docs/alpaca-integration-review.md:129), so a partial entry leaves unprotected shares. The global block is defensible on a one-operator paper desk that gets 1-2 cards a day.

How often any of this happens is unverified. Alpaca paper is reported to simulate partial fills, but whether it posts DIV variants or ROC/NRA dividends is unknown. It has run since September without, as far as the docs record, such a block. Do the cheap slice first and defer the M-sized restructure.

**Risks**

- **Weakened drawdown protection.** A sign-blind "unknown = external flow" rule hides losses and silently weakens the one admission gate fed by measured account evidence. The account-drawdown cap shared across tiers is a CLAUDE.md contract.
- **Unprotected shares.** Scoping partial fills to one symbol could admit new risk while a partially filled entry is still unprotected, and could conflict with contracts 1 and 2. It touches the FIFO, ledger and lifetime code paths, which are the riskiest code in the repo.
- **Contract drift.** Splitting readiness in two creates a mismatch to manage: `/perf` withholds realised P&L while admission proceeds. Docs and readiness must say which kind of readiness is degraded.
- **Opportunity cost.** It takes engineering time from entry-side alpha work, the actual bottleneck.
- **Unverified frequency.** If Alpaca paper never emits these activities, the restructure buys nothing.

**Prerequisites**

1. Query the production journal read-only, through the operator. Look at the `account/ledger` checkpoints and the `activity/*` streams for:
   - any "Unsupported activity type" or "Cash mismatch" issue ever recorded;
   - every distinct `activity_type` seen;
   - the count of partially filled entry roots and partial-entry REVIEW decisions since go-live.
2. Check Alpaca's documented activity types against what the paper environment actually posts: the DIV family (DIVCGL, DIVCGS, DIVNRA, DIVROC, DIVTW, DIVTXEX, DIVFT), PTC/PTR and the SPLIT/SPIN/MA/NC/CIL corporate-action types.
3. Confirm with the operator that changing the partial-ownership rule in CLAUDE.md contract 2 is acceptable before scoping blocks to one symbol.

**Better alternative**

A cheaper, higher-leverage slice, about one small PR, which could ride on the next workstream PR:

1. Add the documented Alpaca cash-only activity types to the classified sets:
   - dividend variants, as income;
   - DIVNRA and DIVTW withholding, as fees;
   - PTC/PTR pass-through charges and rebates, as fees.
   Pin each with a fixture test. This removes the most likely trigger without touching the drawdown model.
2. Add a journaled, per-activity-ID operator acknowledgement command, for example `ledger accept <activity_id> --as income|fee|external|pnl`. Also add an explicit re-baseline command for a sticky `risk_invalidation`. Together they turn a permanent outage that needs a code change into a one-command recovery the operator can audit.
3. In the meantime, make the outage loud: emit a debounced incident (`diagnostics/monitor`) when scans drop every candidate for "account risk unavailable".

Defer the general readiness split and symbol-scoped partial fills until the journal shows they actually happen. If it does, make the split sign-aware and put structural actions under a per-symbol quarantine.
