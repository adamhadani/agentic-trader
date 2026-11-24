# Final report: an 8-week alpha program for agentic-trader (2026-11-02 → 2026-12-24)

*Final editor's revision of `06-synthesis.md`, after the statistics critique (`07`) and the fit critique (`08`). The strands 01–08 are archived outside Git (see the [README](README.md)). Repo facts were checked read-only against the installed checkout at `183e33b`. Figures marked "my arithmetic" are planning estimates; the power check named beside each one must confirm it before any read.*

## Recommendation in one paragraph

Make the card stream honest this week, and do it for free: withhold or stop auto-entering TREND_PULLBACK 4h, shrink every card prior, and put a real base rate on PEAD cards. Then buy Sharadar and spend its untouched 2007–2016 era **once**, on a single frozen manifest. That manifest holds the desk's two event near-misses (insider clusters, and a PEAD that needs no consensus data) and one mechanically chosen multi-theme composite judged by rank IC. Every one-shot read is rehearsed first on a window that has already been read. A pass reaches auto-entry only if it also clears an **economic gate**: non-negative net excess against a beta-matched SPY, after cost, on the most recent window. Cut the reversal, TSMOM, intraday and veto studies. Defer options to the next program. **Expect about a 1-in-4 chance of a source that is good enough to auto-enter in time for the January earnings season.** The rest of the value is decisive answers on the two near-misses, survivorship-free history, and an IC yardstick for judging every future data source.

**Corrections and verifications (to the strands and the draft):**

- **The native setup holdout 2026-03-02 → 2026-08-14 is spent.** The 09-23 ranker study read it (`docs/setup-outcomes-2026-09-23.md`).
- **D6 still reads "the first finalist spends the window"** (`docs/holistic-review-2026-10-21/README.md:72`).
- **PEAD v2's EPS and consensus are "as served today"**, so they are not guaranteed point-in-time (`docs/apriori-pead-2026-09-26.md`, Caveats).
- **PEAD's informative statistic is P2, at t ≈ 2.66** (+0.055R, ci90 [+0.021, +0.089]), not the absolute-leg t ≈ 3.0. By year, the absolute long mean was +0.41R in 2024, +0.05R in 2025 and +0.01R in 2026 to July, while the all-reporter control averaged +0.094R. **Recent excess is probably ≤ 0.**
- **Insider v1 saw about 106 events a year** (1,062 over 2016-08 → 2026-07). The SEC Form 3/4/5 data sets start in January 2006.
- **The PEAD throughput cap is not the per-session budget.** The binding caps are `apriori.max_open_drift_positions = 4` with 20-session holds (`agentic_trader/config.py:204`) and the desk cap of `max_concurrent_positions: 8` (`config/config.yaml:6`). Insider watch cards may also hold 4 open positions, so the two catalog/watch sources together can already fill all 8 slots.
- **`card_policy` withholds only keys with at least 20 matured live cards** (`min_mature_cards: 20`). In practice that means TREND_PULLBACK 4h LONG. SQUEEZE_BREAKOUT 1h keeps sending cards until it has 20 matured.
- **No Benzinga history cache exists**, and `tsmom-v1.json` has not been frozen (only exdiv, insider, pead-v1 and pead-v2 are in `config/research/apriori/`).
- **The 2017–21 native frames have already been read twice.** Another read would be the third.
- **The pooled confirmation slice 2024–26 is partly read for large-cap momentum.** The 09-18 factor-controls study reported 2024 and 2025 IC for skipped-month and residual momentum.
- **Alpaca's indicative option prices are calculated, not OPRA quotes.**
- **`gex.py` defaults implied volatility to 0.20** (`agentic_trader/options/gex.py:102`). That fabricated value violates contract 9.
- **Unverified, to check on day 1:**
  - Sharadar's price, and whether a cancelled subscriber may keep the data. My 10-30 searches found no vendor statement on either.
  - The meaning of SF1 `datekey`, generally understood to be the filing date.
  - Whether the EVENTS table carries 8-K Item 2.02.
  - How SEP treats delisting.
  - ORATS pricing: $49/month on one page, $99–299/month quoted by third parties.

---

## 1. Executive summary

1. **Why nothing has worked.** The desk tested single price/volume signals one at a time, on the most heavily mined data there is, with a statistic whose minimum detectable effect is 0.065–0.08R. The plausible single-signal effect is 0.02–0.05R. The 64 pooled t-statistics are distributed like the null: mean −0.06, sd 1.25.
2. **Only information events judged against controls built for the event ever looked positive.** PEAD gave +0.055R over all reporters (t ≈ 2.66). Insider clusters gave +0.070R over matched controls. Both are near-misses, and both are measured on survivorship-biased, current-membership cohorts.
3. **The market leg is now the hurdle, not the anomaly.** The north star scores cards against a beta-matched SPY. A long-only mid-cap card can beat its matched controls and still lose to that yardstick. Every pass therefore needs a second, economic gate before it is auto-entered.
4. **Paper trading cannot validate anything in 8 weeks.**
   - A card-level effect of 0.1R takes about 28 months of cards to see.
   - IC 0.02 at h = 5 takes about 2–4 years of live sessions (my arithmetic).
   - Validation must come from point-in-time history. Forward runs are for capture and parity.
5. **Bet 1 (week 1, about 4 days, $0): an honest stream.**
   - Decide `card_policy` (enforce, or stop auto-entering negative-prior cards).
   - Use empirical-Bayes priors across keys.
   - Put a net-excess base rate on PEAD cards.
   - Keep a window-and-read ledger.
   - Fix the fabricated `gex.py` IV default.
6. **Bet 2 (weeks 1–3, about $499 a year, to be verified): a fresh era for the event lane.**
   - `insider-oos-v1` with P2 as primary, and `pead-sue-v3`, a consensus-free SUE PEAD, both on Sharadar point-in-time cohorts for 2007–2016.
   - A rehearsal on known windows, a placebo, controls for earnings proximity and beta, and a delisting rule.
   - At least one leg passes: about 40–50%. One leg is good enough to auto-enter: about 15–20%.
7. **Bet 3 (weeks 2–7, $0 beyond Sharadar): IC machinery and one composite.**
   - Members are picked mechanically from the Jensen–Kelly–Pedersen themes, including fundamental themes from SF1 if the audit passes.
   - It is frozen in week 2 and confirmed on the **era**. The 2024–26 slice is used only as a decay check.
   - The composite becomes the yardstick that every future data source is judged against.
8. **Cut:** residual reversal, TSMOM, intraday noise-band momentum and `veto-v1`. **Defer** options to the next program, apart from a spec freeze and a one-hour feed check.

---

## 2. Diagnosis, ranked

### 2.1 The information set has little left at this resolution (primary)

- **Price/volume.** The 64 fixed-set pairs give t with mean −0.06 and sd 1.25, against a check-C null sd of 1.11–1.27 (holistic review §2.2).
- **Return space.** The v4 label diagnostic used raw 20-session returns with no bracket. Nothing reached |t| ≥ 3. `illiquidity` (+147 bp, t 2.40) is a high-low range, i.e. a volatility measure, not Amihud. `momentum_12_1` gave +96 bp, t 2.16 (`alpha-pooled-checks-v4-2026-10-07.md`).
- **Events.** Every positive hint carried non-price information and was judged against a control built for the event (PEAD, insider).
- **Literature.**
  - Published anomalies fall by about 58% after publication (McLean–Pontiff 2016).
  - Anomalies have decayed in liquid stocks (Chordia–Subrahmanyam–Tong 2014).
  - Martineau (2022) finds large-cap PEAD absent since 2006. Subrahmanyam's 2025 rebuttal blames design choices.
  - Simple rules on efficient indices fail White's Reality Check (Aronson ch. 9).

### 2.2 The statistic is too weak, and top-3 scoring wastes breadth (primary)

- **Power.** Check A detects 0.08R at 84% power and 0.05R at 40%. The 64 nulls rule out single-signal edges above about 0.06R and say nothing below that.
- **Breadth, corrected.**
  - The transfer coefficient falls from 0.42 at top-3 of 85 names to 0.19 at top-3 of 800. Information still scales as IC·TC·√N, so widening at constant IC *raises* information per session, by about 1.35×, and raises the expected score of each pick (my arithmetic).
  - Top-3 scoring captures only a fraction of what a full-cross-section IC test would.
  - The case against a wide cube today is survivorship and the absence of point-in-time membership, not TC.
- **Calendar time binds.** Overlapping 20-session labels give about 125 independent periods in ten years.

### 2.3 Nothing combines signals (primary, methodological)

- **Every practitioner book treats a single signal as a weak input to a combination:** Isichenko ch. 3, Grinold–Kahn Insight 6, Ilmanen ch. 24.
- **The repo has 64 pass/fail verdicts on single formulas and no combination stage.**
- **The batch rule says the fixed sets failed as a whole.** *Finding Alphas* ch. 15 judges an automated batch by its mean out-of-sample result. When 7 of 8 discovery edges are negative, the batch's best-looking member fails with it; `shorts_retreat` passed discovery at t +3.40, then gave +0.006R in selection.

### 2.4 The card stream runs on a negative-prior generator (primary for the north star)

| Key | Replay prior (net excess) | n | Comment |
|---|---|---|---|
| TREND_PULLBACK 4h LONG | −0.078R | 7,049 | se_session ≈ 0.025, t ≈ −3.1; live posterior −0.115R ± 0.156 on 10-29 |
| SQUEEZE_BREAKOUT 1h LONG | −0.021R | 1,087 | |
| SQUEEZE_BREAKOUT 4h LONG | +0.132R | 114 (93 sessions) | se_session 0.138. Shrunk across keys, it is about +0.05R (my arithmetic). Under `enforce` it is the winner's-curse survivor |

- **No selector has added value:**
  - `setup_quality` has ρ +0.016;
  - the frozen ridge ranker earned −0.293R against −0.118R for random picks;
  - runners-up beat the sent cards over the last 30 days;
  - the LLM gate has never been shown to add value.
- **PEAD is throttled by open-position caps,** not by its per-session budget (see the corrections box). The cap is deliberate: it stops the probe crowding native cards out of the 8 desk slots.

### 2.5 Clean data is nearly spent (structural)

| Window | Status | Use in this program |
|---|---|---|
| Pooled cube 2016-08 → 2023-12 | Read by 64 pairs and every lane | Development and rehearsal only |
| Pooled confirmation 2024-01-02 → 2026-07-31 | Unused for pooled formulas, but PEAD, exdiv and insider event outcomes and large-cap momentum IC were read there | Held in reserve. A decay check only if the composite passes the era (needs D6 revised then) |
| Native setups 2021-06 → 2026-01 | Read at least 5 times | Descriptive |
| Native setups 2026-03 → 2026-08 | Read once (ranker holdout) | Spent |
| Native setups 2017-01 → 2021-04 | Read twice | Not used (a third read) |
| **2007-01 → 2016-07: Sharadar daily, Form 4 from 2006** | **Fresh** | **One frozen manifest, two declared lanes** |
| Prospective, from 2026-08-01 | Fresh | Probe monitors; a fixed 2027-08 insider read |

Only inputs that cannot be rebuilt need a live capture now: borrow snapshots, option quotes (once the feed is shown to be real), and the Nasdaq consensus as served on the day. Anything derived from SIP bars can be re-scored from history later.

### 2.6 A statistical pass is not a card (new, primary for the north star)

There are three ways a pass can be "true but economically empty":

1. **An era pass shows the effect existed then, not that it persists into 2027.** For insider clusters, 2007–2016 straddles publication (Cohen–Malloy–Pomorski 2012).
2. **A matched-control pass is not EV ≥ 0 against a beta-matched SPY.** Insider v1's P2 was +0.070R, but its beta-adjusted card EV was never computed.
3. **An IC pass at h = 5 is not a positive 20-session card.** A composite at IC 0.03 with a 20-session hold nets about +0.02R per pick after cost (§3, direction 3; my arithmetic).

Hence the economic gate (program rule R5).

### 2.7 Yardstick and label handicaps (secondary)

These are real, but fixing them would not have changed the record (the return-space diagnostic bounds them):

- the long-only label with one horizon;
- R-normalisation, which penalises high-volatility picks;
- the absolute-leg ci90 rule, which conflicts with D3 and cost insider v1 its P1;
- the cube label slice dropping timeouts above about 28 sessions (MI4).

Three things are **not** causes:

- **Protocol conservatism.** The t-statistics already look like the null.
- **Compute speed.**
- **Exits.** The bracket counterfactual returned `no_change`.

---

## 3. Ranked directions

### Program rules (apply to every read)

| # | Rule |
|---|---|
| R1 | **Freeze and hash before reading.** A window × read ledger records every window, every read, its lane and its cohort. Each lane's α is declared in the ledger before its read: one-sided 5% per lane, Holm within a lane. The program-level false-pass bound under a global null is then about 10%. |
| R2 | **Rehearse before every one-shot read.** Run the exact code path on an already-read window and reproduce the known result and event counts within a tolerance declared in the manifest. Code fixed after a rehearsal must not change the hypothesis, and each fix is logged. |
| R3 | **State power at the *shrunk* effect,** in both the statistical unit and `net_excess_r_v1` per card. A planted-effect check runs before the read. |
| R4 | **Every read has a negative control.** Event legs use pseudo-event placebos: random dates for the same names and months, put through the identical matching. IC reads use C-IC (names permuted within session and sector). |
| R5 | **Two gates before auto-entry.** The *statistical gate* is a pass on the fresh window. The *economic gate* is the source's `net_excess_r_v1` on its most recent measured window, empirically shrunk toward 0 by the a priori family's spread, and it must be ≥ 0. A source that passes only the statistical gate runs as a tap-only probe that states its base rate. |
| R6 | **Every source enters as a capped, killable probe** ($100 risk, −4R kill) inside a declared slot allocation across the 8 desk positions. |

| Rank | Direction | Build (SDD days) | Cash | P(statistical pass) | P(passes both gates) | First Telegram effect |
|---|---|---|---|---|---|---|
| 1 | Honest card stream now | ≈ 4 | $0 | n/a (ships ≈ 95%) | n/a | Week 1–2 |
| 2 | Sharadar era, event lane (H2a, H2b) | ≈ 12, plus ≈ 3 path-to-cards | ≈ $499/yr (verify) | at least one ≈ 40–50% | ≈ 15–20% | January season (from about 01-13) |
| 3 | IC machinery and `composite-ic-v1` on the era | ≈ 11 | $0 beyond Sharadar | era ≈ 35–45% | ≈ 8–12% | 2027 |
| 4 | Options: spec and feed check only | ≈ 1 | $0–49 | n/a | n/a | Next program |
| — | Cut: residual reversal, TSMOM, intraday, `veto-v1` | — | — | — | — | — |

**Combined: about 1 in 4 that some source passes both gates by January** (my arithmetic: 1 − 0.82 × 0.90).

### Direction 1: an honest card stream, now

**Goal.** Every card that reaches Telegram, or is auto-entered, carries an honest base rate, and nothing with a well-measured negative prior is auto-traded.

**What changes (≈ 4 days, $0, one PR plus a deploy-and-verify cycle):**

| Item | Detail | Days |
|---|---|---|
| a. `card_policy` decision at DP0 | Two options, chosen knowingly (table below) | config edit |
| b. `card_stats` v2.1: empirical-Bayes prior across keys | Shrink each key's replay mean toward the cross-key mean with the pooled τ before it seeds the posterior. Publish a new prior version and digest, and record the old one | 1 |
| c. PEAD net-excess base rate | Recompute v2's 2016–26 events in `net_excess_r_v1` (beta-matched SPY, per-name cost), by year, with P2 by year. Shrink it and render the same evidence block native cards carry. This uses already-read data. It is the economic gate for the live consensus PEAD probe | 1.5 |
| d. Raw EPS and consensus pair in `pead_decision` | The derived surprise is already journaled (`pead_events.py:56-72`); this adds only the raw pair | 0.5 |
| e. `gex.py` IV default | NaN plus a quality note instead of 0.20 (contract 9) | 0.5 |
| f. Window × read ledger | The §2.5 table, plus lanes and α (R1) | 0.5 |

**The `card_policy` choice (DP0, Friday 11-06, after at least 5 preview sessions):**

| Option | Cards per session until January | What it means |
|---|---|---|
| **Enforce at 0.0** (recommended) | ≈ 0.5 | TREND_PULLBACK 4h LONG is withheld. SQUEEZE 1h sends until it has 20 matured cards; SQUEEZE 4h is rare. Insider watch is about 0.4 a session, and PEAD is out of season. Every sent card meets the desk's own definition of reasonable |
| Preview, auto-entering only cards with `would_withhold: false` (a small contract-1 eligibility change) | ≈ 2.5 | TREND_PULLBACK cards still arrive with "−0.12R ± 0.16" printed on them, and are not auto-entered |

Withheld cards keep their counterfactual labels, so the evidence is the same either way. Enforce is not costless: it fails the "steady 1–2 cards" half of the north star until a new source passes.

**Monitor (the PEAD probe, unchanged).**
- The always-valid confidence sequence on all eligible events is near-powerless: at about 250 events by late February, its upper bound falls below 0 only if the true EV is about −0.2R or lower. The **−4R probe kill is the binding control.**
- Report fill-conditional outcomes beside the all-eligible counterfactual. Session-bounded limit fills can be adversely selected: 4 cards and 1 fill by 10-21.
- First look: 2027-02-26.

**Dropped from the draft:**
- **PEAD throughput in week 1.** The Q3 season is over by 11-09, and the change would run ahead of the evidence. It moves to direction 2's path to cards.
- **p\* as a deliverable.** It re-expresses the posterior, and the target rate is non-stationary (0.285 → 0.421 by year).

**Probability.** P(PEAD's true live net excess ≥ 0) ≈ 35–45%, given its 2025–26 by-year figures.

### Direction 2: Sharadar era, event lane

**Why it ranks first among the research bets.** Insider v1's own result doc says a second look "needs a new window", and the prospective route needs about 3 years at about 9 events a month. A disjoint, survivorship-free historical era is that new window, available now. The two event sources the desk already runs, insider watch and PEAD, are also the only path to an EV ≥ 0 card before February.

**Hypotheses.** Both are frozen in one manifest, charged before any outcome is read: event lane, Holm over 2, one-sided 5%.

- **H2a `insider-oos-v1`.**
  - **Rule.** The frozen v1 rule: at least 2 opportunistic officer/director open-market purchases within 21 sessions, entry once on the session after filing, the cohort-v3 bracket, and 15 bp.
  - **Era.** 2007-01 → 2016-07. 2006 is burn-in for the "routine" classification.
  - **Cohort.** The 401–1,400 dollar-volume band, rebuilt point-in-time each January from SEP.
  - **Primary: P2**, the excess over 5 same-session controls. Choosing P2 after v1 is legitimate: v1 was the discovery and this is its confirmation, and it is consistent with D3.
  - **Changes from v1:**
    - controls are also matched on `atr_pct` or `beta_60` (2009's junk rally favoured exactly the beaten-down, high-beta names insiders buy);
    - the cohort is point-in-time;
    - the label is the daily-bar variant.
  - **Required secondaries:**
    - **S1.** P2 excluding events within 10 sessions after an earnings release, plus a control matched on earnings proximity. Insiders buy in post-earnings windows, so an H2a pass could be PEAD.
    - **S2.** The sign of P2 excluding 2008-03 → 2009-12.
    - **S3.** The effective number of independent sessions.
- **H2b `pead-sue-v3`.**
  - **Surprise.** Time-series SUE on as-reported quarterly EPS (SF1 `ARQ`): a seasonal random walk scaled by the standard deviation of the prior 8 seasonal differences. It needs no consensus, which removes v2's contamination.
  - **Announcement date.** From Sharadar EVENTS if it carries Item 2.02, otherwise the free EDGAR full-index.
  - **Look-ahead fix.** **Entry is at the later of D+2 and `datekey` + 1 session**, so EPS is never used before the date it became available, and the same rule is reproducible live. The D+2 entry with EPS dated at the 8-K is a declared sensitivity.
  - **Filter.** SUE in the top quintile of the trailing four quarters' reporters, plus v2's reaction filter (≥ +1σ), on v2's bracket.
  - **Primary.** Paired excess over all reporters.
  - **P5 (secondary).** The top 2 by SUE per session beat all events; this is the zero-fit slot ranking.
  - **What it validates.** A pass validates **SUE-PEAD only**. It does not re-certify the live consensus-based v2 cards.
- **Both legs:**
  - **Delisting rule.** Exit at the last close, with −30% for performance delistings (Shumway 1997). Sensitivity at 0% and −55% (Nasdaq, Shumway–Warther 1999).
  - **Placebo.** Pseudo-event dates (R4); it should come out near zero.

**Rehearsal (R2, 1 day, week 3).** Run the exact code paths on the already-read window 2016-08 → 2026-07 on the Sharadar point-in-time cohort.
- Reproduce insider v1's P2 (+0.070R) and its count (1,062), and PEAD v2's paired +0.055R, within the declared tolerance (the labels differ).
- The rehearsal also gives the **first survivorship-free re-estimate of both near-misses**, which is descriptive only.
- It gives the economic-gate numbers too: each leg's `net_excess_r_v1` on the most recent window.

**Power** (my arithmetic, Holm first step at one-sided 2.5%; the planted-effect check must confirm before the read):

| | H2a | H2b |
|---|---|---|
| Expected events | ≈ 1,000–1,500 | ≈ 1,500–2,500 (fewer under the `datekey` rule) |
| SE | ≈ 0.031–0.038 | ≈ 0.029–0.037 |
| Power at the v1/v2 point estimate | ≈ 50% at +0.07R | ≈ 30–55% at +0.055R |
| Power at the shrunk effect | ≈ 20–30% at +0.035–0.045R | ≈ 20–30% at +0.035R |
| P(pass) | ≈ 30% | ≈ 25–30% |

At least one passes: about 40–50%, since the two legs share a pipeline and an era.

**What it needs.**
- **Sharadar Core US Equities Bundle.** Budget the annual, about $499 (verify). Directions 3 and 4 need it after day 30, and retention after cancellation is unconfirmed.
- **Free inputs:**
  - Form 345 2006q1 → 2016q3, through the existing quarterly-zip path in `research/insider_filings.py` (a parameter change);
  - delisted-CIK mapping through Sharadar TICKERS;
  - EDGAR 8-K data only if EVENTS lacks Item 2.02.

| Task | Days |
|---|---|
| `alpha sharadar`: digest-pinned acquisition with a manifest | 2 |
| Stage-1 audit: the 22 survivorship-spike names, ≥ 95% of the 2,147 Alpaca-inactive names, as-reported vs restated, `datekey` semantics, SIP agreement on the overlap | 1 |
| Point-in-time cohort builder | 1.5 |
| Daily-bar label variant, with its own cube identity | 2 |
| Form 345 history and CIK mapping; announcement dates (EVENTS, or the EDGAR fallback) | 1.5 (up to 3 if the fallback is needed) |
| Harness wiring, secondaries, placebo, planted-effect power | 2.5 |
| Rehearsal | 1 |
| **Total** | **≈ 11.5–13** |

**Kill rules.**
- **H2a fails.** No further historical insider looks. The watch cards stay "unvalidated". Their prospective P2 at a **fixed 2027-08 read** is pooled with the era P2 by a fixed-effect meta-analysis declared now, so that the two do not become two separate chances to pass.
- **H2b fails.** No SUE PEAD. The consensus PEAD probe stays at its current caps on its direction-1 monitor.

**Path to cards (≈ 3 days, weeks 5–6, conditional, live for 01-13):**
- **H2a passes.** A spec moving insider watch cards to a registry paper probe; this is a contract-1 change, since watch cards are "never enrolled". Auto-entry follows only if the economic gate (R5) is ≥ 0; otherwise the probe is tap-only and states its base rate.
- **H2b passes.** A live `pead-v3`:
  - SUE from Sharadar's daily SF1 or EDGAR XBRL `companyfacts`;
  - entry under the same `datekey` rule;
  - P5 ranks the slots;
  - the economic gate decides auto-entry.
- **Either passes.** A **book-capacity rule** (decision O3). Today PEAD (4 open) and insider watch (4 open) can already fill all 8 desk slots. A source that fills the book silently starves the rest, and its base rate is then measured on a self-selected subset.

### Direction 3: IC machinery, then one composite confirmed on the era

**Hypothesis.** An equal-weight composite of mechanically chosen characteristics, each carrying its published sign and neutralised for sector, beta and size, has a positive cross-sectional rank IC against **20-session** residual returns. The horizon matches the card hold. The test runs on the point-in-time mid-cap band in 2007–2016.

**Evidence.**
- Combining beats single signals: Isichenko ch. 3; Grinold–Kahn; Gu–Kelly–Xiu (RFS 2020); DeMiguel et al. (RFS 2020), whose combination lowers marginal trading cost.
- Most factors replicate but are small: Jensen–Kelly–Pedersen (JF 2023).
- Internal: no composite has ever been computed. The learned ridge that failed was fitted to bracket R on native setups; this composite fits nothing.

**Members, frozen and hashed in week 2, before any IC is computed on the new machinery.**
- **The rule is mechanical, not a hand-picked list.** For each JKP theme computable from the purchased data, take the factor from the earliest-published paper in JKP's appendix, with JKP's sign:
  - **Price themes:** momentum, short-term reversal (JKP's one-month definition, which fits the 20-session horizon), low risk.
  - **Fundamental themes** (value, profitability, investment, accruals), from SF1 by `datekey`. These are included **only if** the Stage-1 audit confirms that `datekey` is a filing date. The declared fallback is the price themes only, and the choice is fixed at the audit, before any outcome is read.
- **Excluded:**
  - **Amihud illiquidity.** After ADV neutralisation it is mostly a volatility tilt, its sign conflicts with low risk, and Ben-Rephael–Kadan–Wohl (2015) find the premium is about zero.
  - **`short_ratio_z`.** It is double-dipped from `shorts_retreat`, and Reg SHO files start mid-2009.
  - **PEAD, insider and seasonality.**
- **Construction:**
  1. winsorise each member at ±3;
  2. z-score it within SIC sector;
  3. regress out `beta_60` and log ADV;
  4. take the equal-weight mean within each theme, then across themes, requiring at least 60% of the members.
- The fundamental themes are the one genuinely new information class the desk gets this program (an editor's addition; see §7).

**Infrastructure (≈ 11 days):**

| Task | Days |
|---|---|
| Cube v2 arrays from Sharadar daily bars: forward returns from the **next open** at 1, 5, 20 and 60 sessions; residuals on `beta_60` and sector; `atr_pct`; log ADV; SIC. Plus the MI-R2 label-span fix with its regression test | 3 |
| Grammar: `cs_rank`, `cs_zscore`, `group_neutralize`, `cs_regress_out` (kernels exist in `research/alpha/panel.py:19-29`), `ts_beta`, `ts_cov`, `ts_skew`, and vectorised rolling operators | 2 |
| IC statistic with a session-block bootstrap as primary and Newey–West at more than h − 1 lags as a cross-check; a tear sheet; checks A-IC (a planted AR(1) score at IC 0.02) and C-IC | 3 |
| Zero-trial re-score of the 64 pairs on the known windows: a second rehearsal and the batch-mean stop rule | 1 |
| Fundamental-theme members from SF1 (conditional) | 1.5 |
| Runs | 0.5 |

**Reads.**
1. **Development sanity gate,** 2016-08 → 2023-12. This is go/no-go, not evidence: the designers have seen these members there.
   - Rule: h = 20 IC > 0 with t ≥ 2, and IC > 0 in at least 3 of 4 blocks.
   - If it fails, close the composite, and the era is never read for it.
2. **Era read,** 2007-01 → 2016-07, price lane, one-sided 5%. h = 20 is decisive; h = 5 is reported.
3. **Slice decay check,** 2024-01 → 2026-07, only if the era passes, and only after D6 is revised.
   - Cohort: v3 *minus* the 300-name snapshot and the 64-name panel cohort, with the residual overlap declared.
   - Rule: IC point estimate ≥ 0, plus the economic gate on new top-decile entrants in `net_excess_r_v1` (3×ATR stop, 20-session time exit).
   - At about 645 sessions and h = 20 this is only a sign check, not a test.

**Power and card units** (my arithmetic; A-IC must confirm):

| | Era, h = 20, ≈ 2,400 sessions | Slice, h = 20, ≈ 645 sessions |
|---|---|---|
| SE (daily IC sd 0.10–0.15) | ≈ 0.009–0.014 | ≈ 0.018–0.026 |
| Power at IC 0.02 | ≈ 40–70% | sign check only |
| Power at IC 0.03 | ≈ 70–95% | sign check only |

- **In card units:** IC 0.03 × 20-session residual volatility 9% × a top-decile z of 1.75 gives about 0.47% gross per pick. After about 30 bp round trip that is about 0.17% net, or **about +0.02R** at a 3×ATR stop.
- **Even a clean pass is near break-even as a card.** Its main value is as a yardstick and a selector.

**Probability.**
- Era passes: about 35–45%.
- Slice decay check and economic gate pass, given an era pass: about 25–30%.
- A card at EV ≥ 0: about 8–12%.
- The infrastructure pays regardless.

**Path to cards, on a pass.**
- A `composite` catalog probe for **new** top-decile entrants (absent for the previous 20 sessions), inside the slot allocation, with a $100 cap and the −4R kill.
- The composite becomes the L3 `CardSelector` key, measured prospectively.
- It also becomes the yardstick for every new source: incremental IC over the frozen composite, against a 252-session time-shifted placebo of the new field.

**Cut: `veto-v1`.** It would be a third read of the 2017–21 frames, its MDE is about 0.15R against a plausible effect of 0.03–0.06R, and its fundamentals arm needs new joins.

### Direction 4: options-implied information (deferred to the next program)

- **Evidence.** It is real but regime-dependent:
  - Cremers–Weinbaum (JFQA 2010) find about 50 bp a week long-short, 1996–2005;
  - Xing–Zhang–Zhao (JFQA 2010);
  - An–Ang–Bali–Cakici (JF 2014);
  - Li & Wang (2026 preprint, arXiv 2608.26115) find the 2015–2026 predictability depends on regime, and that the post-2023 period differs sharply.
- **For this desk.** The long leg on mid caps after 15 bp, inside a 20-session hold, is plausibly 0–0.03R. P(a formula confirms) ≈ 15%.
- **This program (≈ 1 day, week 7):**
  1. Freeze `options_fields_v1`: four formulas plus a smirk negative control.
     - **Always judged on IC incremental to frozen price controls** (momentum, MAX, 5-day reversal, volatility), because option measures correlate with price features that were read in 2016–2023.
     - An option-liquidity filter on volume and open interest.
     - Discovery 2007–2015 on Sharadar labels.
  2. An ORATS demo token and a **one-hour comparison of Alpaca indicative IV ranks against ORATS.** Build the nightly capture worker (2.5 days) only if rank agreement is ≥ 0.8; otherwise the clock would record Alpaca's model, not the market.
- **Next program:** one ORATS month ($49–199, verify the price and that the quota covers about 1,400 tickers), under a licence that permits retention.

### Cut from the program

| Direction | Why |
|---|---|
| Residual no-news reversal | Its window is a re-read of the cohort-v3 reversal tests, and its family already failed (`reversal-lowmax-v1`, P2 +0.008R). Gross ≈ 0.02R against ≈ 0.033R round-trip cost means it fails the economic gate ex ante. No Benzinga history exists (about 6,000+ requests to build one). The JKP one-month reversal is already a composite member |
| TSMOM-v1 on the ETF32 | Its own spec declares roughly one-in-three power; 22 of the 32 ETFs are equity; the ETF32 has been read by four lanes; there is no execution path (a monthly 32-sleeve book against single bracketed cards, `max_concurrent_positions: 8` and the broad-index exclusion). Use it as `/macro` context with no test |
| Intraday SPY/QQQ noise band | About 25% power after a McLean–Pontiff halving; results from a single author group with a commercial affiliate; heavy stop-notice volume under contract 3; it would spend the last fresh intraday slice |
| `veto-v1` | See direction 3 |

---

## 4. Direct answers to your seven thoughts

1. **A more expressive DSL. Yes for cross-sectional and risk operators and new fields, no for more search.**
   - **Add:**
     - `cs_rank`, `cs_zscore`, `group_neutralize` and `cs_regress_out` (the kernels exist in `panel.py`; the pooled scorer just does not use them);
     - `ts_beta` against SPY or the sector, plus `ts_cov` and `ts_skew`;
     - residual and forward-return arrays from the entry price;
     - rank IC as the decisive statistic.
   - **The expansion that matters most is new fields** (SF1 fundamentals, later options), not new grammar over the same OHLCV. Search failed check B twice (1/10 and 3/10).
2. **A larger universe. Not yet, and for a different reason than the draft gave.**
   - Widening at constant IC does raise information, about 1.35× from 85 to 800 names even under top-3 scoring, and full-cross-section IC tests capture more of it still.
   - The blockers are survivorship and the absence of point-in-time membership; cohort v3 halved the long base rate.
   - Sharadar fixes both. After it, point-in-time bands are legitimate for the composite, and event legs benefit most, because their event count scales with names.
   - Longer history (2007–2016) is worth more than more names today.
3. **vectorbt PRO. No.** The pooled studies finish in seconds, and check A takes 1.5–3.5 hours. The constraint is power and clean data. Vectorising the DSL rolling operators (inside direction 3) gets the practical speed-up for free.
4. **GPU deep learning on aws-g4dn. Not in this program.**
   - At IC around 0.02 and about 1,000 independent periods, the constraint is signal and clean history, not model capacity. Learned rankers already failed out of sample.
   - **Later, gated uses:**
     - a Jiang–Kelly–Xiu price-image CNN, as a challenger that must beat the frozen composite on incremental IC (tens of dollars of T4 time);
     - point-in-time headline scoring with chronologically consistent language models. Strand 04 reports the ChronoBERT/ChronoGPT models were withdrawn from Hugging Face, so verify availability.
5. **de Prado. Take three cheap things.**
   - **Take:**
     - A per-window read ledger. That is the useful core of his multiple-testing chapters; ONC, deflated Sharpe and PBO fit a best-of-N selection among return series on a common window, which this program is not.
     - CPCV and PBO (skfolio) whenever something is *fitted*.
     - Abdi–Ranaldo or Corwin–Schultz spread estimates (AFML ch. 19) as per-name cost inputs for the 2007–2016 era, which has no quotes.
   - **Meta-labeling only in its zero-fit form:** P5's SUE slot ranking.
   - **Skip:** information bars, fractional differentiation, entropy features, VPIN, HRP/NCO, synthetic trading rules, bet sizing from p, and p\*.
   - **The library's larger lesson is Grinold–Kahn and Isichenko's:** IC first, then combine.
6. **Data sources, and how to judge them before paying.**
   - **Buy:** Sharadar now, budgeted as the annual (≈ $499, verify). Its value is history, survivorship and point-in-time fundamentals. One ORATS month next program.
   - **Free:**
     - nightly IBKR borrow snapshots (a cheap clock for veto use, read at 6 months or later);
     - FINRA bi-monthly short interest, never tested and distinct from Reg SHO daily volume, as a future incremental-IC candidate;
     - the Alpaca options capture, only after the feed check.
   - **Skip:** Reddit/StockTwits, congress and lobbying data, estimate feeds, transcripts, Google Trends, 13F, COT and real-time SIP.
   - **Protocol DSE-v1, before paying:**
     1. A desk-fit prior: the published effect × 0.5 for decay, converted to R over our hold. Kill the source if it is below half the route's MDE, unless it adds history or removes a bias.
     2. A free audit of coverage, restatements and date semantics.
     3. Pre-register the hypothesis hash and name the window it will consume.
     4. A one-month bulk pull under a licence that permits retention.
     5. One frozen route, judged by **incremental IC over the frozen composite**, or by excess over an event-matched control **and** the economic gate.
   - **Forward paper trading cannot judge a source in 1–2 months:**
     - a 0.1R card effect needs about 28 months;
     - IC 0.02 at h = 5 needs about 2–4 years;
     - use forward runs for capture and parity only.
7. **Other ideas.**
   - **The cheapest lever is honesty:** stop auto-trading negative-prior cards, shrink the priors, and print PEAD's real base rate.
   - **Write a book-capacity rule before any second catalog source auto-enters.**
   - **Rehearse every one-shot read.** The insider a priori run read the pooled confirmation slice and retired the pooled insider fixed sets, which is the precedent.
   - **Borrow tools, not frameworks:** alphalens-reloaded for IC tear sheets, skfolio for CPCV and PBO, and Qlib's Alpha158 definitions as a reference feature spec.
   - **Avoid** Qlib (A-share defaults), mlfinlab (now commercial), FinRL, TradingAgents and ai-hedge-fund. They offer no evidence value.

---

## 5. What not to do, and why

- **No more 8-formula single-signal fixed sets on the bracket label.** Future fields enter as composite members (judged incrementally), event legs, or vetoes.
- **Do not reopen genetic, agentic or LLM-iterative formula search.** Check B failed at 1/10 and 3/10, and the published agentic results come from CSI300 or from windows inside the models' training data.
- **Do not spend the 2024–26 slice before the era read**, and never on anything but the composite's decay check.
- **Do not run Sharadar-era tests one after another.** Every era hypothesis is frozen in the week-2 manifest.
- **Do not let a statistical pass auto-enter without the economic gate.** Do not present a matched-control or IC pass as card EV.
- **Do not widen catalog throughput before DP1,** and never without a slot allocation. PEAD and insider watch can already fill all 8 slots.
- **Do not tune exits, brackets or the LLM gate prompt.** The bracket counterfactual returned `no_change`. Do not train another learned ranker or meta-label on native setup features with a bracket-R target.
- **Do not backtest LLM scores of historical headlines.** Pre-cutoff contamination is established.
- **Do not judge any source or card type on 1–2 months of paper P&L.** Do not peek at `card_policy` evidence without the always-valid sequence.
- **Do not build the options capture worker before the indicative feed is shown to track market IV.**
- **Do not buy vectorbt PRO, social or congress data, estimate feeds or real-time SIP.** Do not start GPU or foundation-model work before the composite sets the IC bar.
- **Do not re-enable native shorts** (−0.33R per setup), and do not add two-leg pairs (both spread protocols failed).

---

## 6. Decisions and plan

### 6.1 Operator decisions needed up front (by Tuesday 2026-11-03)

| # | Decision | Cost | Recommendation |
|---|---|---|---|
| O1 | Subscribe to the Sharadar Core US Equities Bundle (D8). Day-1 due diligence: price, licence retention after cancellation, non-professional research use, `datekey` semantics, EVENTS item codes, delisting fields | ≈ $499/yr budgeted (≈ $69/month if retention is allowed; verify both) | Yes, now |
| O2 | Spend cap through 12-31: Sharadar plus at most $49 for an ORATS check. Nothing else is planned | ≈ $550 | Yes |
| O3 | Book-capacity principle: non-native sources (PEAD, insider watch, future probes) share at most 5 of the 8 desk slots, and native keeps at least 3. Every crowded-out card keeps its counterfactual label. The config change ships with the first path-to-cards spec | $0 | Approve the principle now; set the numbers at DP2 |

**Later decision points** (none needs action now):

| When | Decision |
|---|---|
| DP0, Fri 11-06 | `card_policy`: enforce (recommended, ≈ 0.5 cards/session) or preview with the auto-entry restriction (≈ 2.5 cards/session) |
| Week 2 | Approve the era manifest (event lane H2a/H2b, price lane `composite-ic-v1`, α per lane) before any era outcome is read |
| DP1, Fri 11-20 | H2a and H2b verdicts; for each pass, a tap-only or auto-entry probe spec under the economic gate |
| DP2, Fri 12-04 | Composite era verdict; if it passes, revise D6 so the slice can be used for the decay check; set the slot numbers |
| DP3, Fri 12-18 | Options: go or no-go for the next program |

### 6.2 Week-by-week plan, 2026-11-02 → 2026-12-24

| Week | Build and run | Gate |
|---|---|---|
| **1** (11-02) | Sharadar subscription, due diligence and Stage-1 audit.<br>"Honest stream" PR: `card_stats` v2.1 empirical-Bayes prior, the PEAD net-excess base rate by year with its card block, the raw EPS/consensus pair, the `gex.py` NaN fix. Deploy, restart, verify Telegram.<br>Window × read ledger.<br>Draft the era manifest. | **DP0** |
| **2** (11-09) | `alpha sharadar` digest-pinned pull; point-in-time cohort builder; daily-bar label; Form 345 2006q1 → 2016q3 and CIK mapping; announcement dates.<br>**Freeze and hash the era manifest, with `composite-ic-v1` members fixed by the mechanical rule.**<br>Start the cube v2 arrays. | Manifest approved |
| **3** (11-16) | **Rehearsal** on 2016-08 → 2026-07 (reproduce v1/v2; economic-gate numbers).<br>Planted-effect power at the shrunk effects; pseudo-event placebo.<br>**Then the single event-lane era read.**<br>Cube v2 arrays continue. | **DP1** |
| **4** (11-23, short) | Operators; IC statistic; checks A-IC and C-IC.<br>Re-score the 64 pairs (second rehearsal; batch-mean stop rule).<br>Fundamental-theme members, if the audit passed. | Batch-mean IC rule |
| **5** (11-30) | Composite development sanity gate; if it passes, the **era IC read at h = 20**.<br>Specs for whatever passed DP1 (insider probe, `pead-v3`, PEAD caps) with the slot allocation. | **DP2** |
| **6** (12-07) | Build and deploy the approved specs. Catalog auto-entry (a contract-1 change) only for sources past both gates. Verify. | Deploy and verify |
| **7** (12-14) | If the composite passed the era: the slice decay check, plus a shadow journal of would-be composite cards.<br>`options_fields_v1` freeze; ORATS demo; one-hour indicative comparison. | **DP3** |
| **8** (12-21, holiday) | Write-ups bundled into the feature PRs.<br>January-season readiness: event calendar, caps, probes.<br>Plan the next program. First PEAD monitor look on 2027-02-26. | **DP4** |

**Load.** About 31 SDD days against about 33 working days. Recent velocity (about 21 PRs in 17 days) makes the engineering plausible. Operator gates, one-shot reads and incidents do not compress. **What slips first:** the options check, then the fundamental-theme members (fall back to the price themes, as declared), then the 64-pair re-score.

### 6.3 What to expect by 12-24

| Outcome | Probability |
|---|---|
| An honest stream: negative-prior cards withheld or not auto-entered, shrunk priors, PEAD base rate | ≈ 95% |
| Infrastructure built: ledger, Sharadar point-in-time cohorts, survivorship-free re-estimates of both near-misses, IC machinery | ≈ 95% |
| Decisive verdicts on insider clusters and SUE-PEAD | ≈ 95% |
| At least one event leg passes the statistical gate | ≈ 40–50% |
| A source passes **both** gates, auto-entry-grade for the January season | ≈ 20–25% |
| Composite passes the era | ≈ 35–45% |
| Composite card at EV ≥ 0 | ≈ 8–12% |

**The most likely single outcome (about 3 in 4) is no auto-entry-grade source by January.** Under enforce, that means a sparse but honest stream. The next program then starts from a better position: options history judged incrementally over a working composite, FINRA short interest and borrow judged the same way, and point-in-time cohorts wide enough for event legs.

---

## 7. Changes after critique

**Accepted from the statistics critique (`07`):**

- **Breadth claim corrected.** Widening raises information under top-3 scoring; the real blocker is survivorship (§2.2, thought 2).
- **Forward IC timeline corrected** to 2–4 years at h = 5.
- **PEAD's informative t is 2.66,** and recent years are weak. P(live ≥ 0) lowered to 35–45%. The PEAD net-excess prior now comes **before** any throughput change.
- **The 2024–26 slice is not fresh for momentum.** It is now a decay check only, minus the overlapping names, and is read only after an era pass.
- **Option fields are always judged incrementally** over price controls.
- **Era power lowered** to about 30% (H2a), 25–30% (H2b), and 40–50% that at least one passes. Power is stated at the shrunk effect, in card units too.
- **H2a design:** earnings-proximity secondary, beta/ATR-matched controls, an ex-crisis sign secondary, and a delisting rule.
- **H2b design:** the `datekey` look-ahead is fixed by the entry rule, and a pass validates SUE-PEAD only.
- **Pseudo-event placebos** for every event leg.
- **Composite:** Amihud and `short_ratio_z` dropped; members chosen mechanically; frozen before any IC; decided at h = 20, with forward returns from the entry price; session-block bootstrap.
- **Cuts:** the `veto-v1` confirmation, TSMOM and intraday.
- **Empirical-Bayes priors across keys** before `card_policy` decides which keys survive. SQUEEZE 4h shrinks from +0.13R to about +0.05R.
- **A fixed-effect meta-analysis** of era and prospective insider P2, with a fixed read date.
- **"A pass is not a card"** is now a diagnosis item (§2.6) and a program rule (R5).

**Accepted from the fit critique (`08`):**

- **The PEAD throughput claim was false.** The binding caps are 4 open drift positions and the desk's 8. A book-capacity rule (O3) is added; insider watch's 4 open positions already make 8 non-native slots possible.
- **PEAD throughput moved after DP1.** The season is over by 11-09.
- **A reproduction rehearsal before every one-shot read** (R2), the program's most important addition.
- **The composite is confirmed on the Sharadar era,** not the slice. D6 is revised only after an era pass.
- **The PEAD provenance item is mostly done;** only the raw EPS/consensus pair remains.
- **`card_policy` floor:** enforce withholds only keys with at least 20 matured cards. Both options are stated with their cards per session.
- **No Benzinga history; TSMOM not frozen; 2017–21 would be a third read.**
- **Alpaca indicative options are calculated.** Feed check before any capture worker.
- **The `gex.py` fix** moved to week 1 as a contract-9 honesty fix.
- **EVENTS before any 8-K build;** reuse the Form 345 path.
- **Budget the Sharadar annual,** with licence diligence on day 1.
- **Options deferred.** The census was reduced to the ledger, and p\* was dropped as a deliverable.
- **Calendar re-planned** to about 31 SDD days, with a stated slip order.

**Rejected or modified:**

- **Online FDR (LORD++) as the program's error control: modified.**
  - Kept: the ledger, and α declared per read.
  - Rejected: default LORD++ spending. Its default sequence gives the first test an α well under 0.01, which would erase the power of the few planned high-prior tests.
  - Instead, each lane gets α 0.05 one-sided (Holm within a lane), recorded before the read. The program-level false-pass bound is about 10%, and the probes and the economic gate catch the rest.
- **"Card prior = estimate × 0.5 × era discount": modified into the economic gate (R5).**
  - A multiplicative haircut shrinks only the anomaly. Long-only mid-cap cards lose to beta-matched SPY through the *market leg*.
  - The gate measures the source's `net_excess_r_v1` directly on its most recent window, which already embeds post-publication decay.
- **Folding residual reversal into the era manifest (`07`): rejected.** It fails the economic gate ex ante (≈ 0.02R gross against ≈ 0.033R cost), and an extra Holm member would dilute the event lane.
- **Holm over 3 in one era manifest (`08`): modified.** One manifest and one hash, but two declared lanes, so that the composite does not dilute the event lane's power. Both lanes are frozen before any era outcome is read.
- **The composite with price members only (`06`/`07`): extended (editor's addition).** Moving confirmation onto Sharadar makes point-in-time SF1 available at no extra cost, and fundamental themes are the one information class the desk has never tested. They are included under the same mechanical JKP rule, conditional on the `datekey` audit, with a declared fallback to price themes.
- **Keeping enforce as a neutral operator choice (`08`): partly.** Both options are stated with their card counts, but I still recommend enforce, because the desk's own definition of a reasonable card (10-08) excludes a −0.12R card.

---

## Sources

**Repo** (read-only, `183e33b`):
- `CLAUDE.md`
- `config/config.yaml:1-8, 495-508`
- `agentic_trader/config.py:195-204`
- `agentic_trader/options/gex.py:98-106`
- `agentic_trader/research/apriori/pead_events.py:56-72`
- `agentic_trader/research/alpha/panel.py:19-29`
- `docs/apriori-pead-2026-09-26.md` (by-year table, caveats)
- `docs/apriori-insider-2026-10-23.md:126-127`
- `docs/apriori-alphas.md:131-148, 272`
- `docs/card-evidence.md:19, 194`
- `docs/card-stats-prior-2026-10-28.md:8, 32-35`
- `docs/alpha-factor-controls-2026-09-18.md:58-60`
- `docs/alpha-pooled-checks-v4-2026-10-07.md`
- `docs/alpha-pooled-literature-2026-09-30.md`
- `docs/setup-outcomes-2026-09-23.md`
- `docs/holistic-review-2026-10-21/README.md:72` and `gap-findings.md:228-262`

**Strand reports** (archived outside Git, see the README): `01-internal-audit.md` to `05-data-sources.md`, `06-synthesis.md`, `07-critique-statistics.md`, `08-critique-fit.md`.

**Web, checked for this revision:**
- [QuantRocket: Sharadar pricing](https://www.quantrocket.com/pricing/data/sharadar) and [Sharadar data](https://www.quantrocket.com/sharadar/): delisted tickers included; no public bundle price found.
- [Nasdaq Data Link: pricing help](https://help.data.nasdaq.com/article/568-how-much-does-nasdaq-data-link-data-cost-how-do-i-find-pricing): some products licensed "only for select use-cases".
- [QuantRocket: Sharadar fundamentals usage](https://www.quantrocket.com/codeload/fundamental-factors/fundamental_factors/Lesson03-Basic-Usage.ipynb.html): ARQ is as-reported; no `datekey` definition found.
- [QuantRocket: a primer on survivorship bias](https://quantrocket.com/blog/survivorship-bias)

**Web, from the strands and critiques:**
- [SEC Form 345 data sets](https://www.sec.gov/dera/data/form-345)
- [ORATS, Tradier-linked plan](https://orats.com/tradier)
- [Alpaca historical option data](https://docs.alpaca.markets/docs/historical-option-data); [Alpaca forum on indicative prices](https://forum.alpaca.markets/t/how-to-get-the-current-market-price-of-an-option-shown-in-alpaca-ui/14769)
- [Martineau, CFR 2022](https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf); [UCLA Anderson on the 2025 rebuttal](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/)
- [Ben-Rephael, Kadan & Wohl, JFQA 2015](https://ideas.repec.org/a/cup/jfinqa/v50y2015i1-2p197-229_00.html)
- [Li & Wang, arXiv 2608.26115](https://arxiv.org/abs/2608.26115)
- [Javanmard & Montanari, online FDR](https://arxiv.org/abs/1502.06197)
- [8-K item codes](https://app.edgar.tools/tools/8-k-items)

**Key literature:**
- Events: Cohen, Malloy & Pomorski (JF 2012); Lakonishok & Lee (RFS 2001); Bernard & Thomas (1989).
- Decay: McLean & Pontiff (JF 2016); Chordia, Subrahmanyam & Tong (2014).
- Combination: Jensen, Kelly & Pedersen (JF 2023); Gu, Kelly & Xiu (RFS 2020); DeMiguel et al. (RFS 2020).
- Delisting: Shumway (JF 1997); Shumway & Warther (JF 1999).
- Inference: Howard, Ramdas, McAuliffe & Sekhon (Ann. Stat. 2021).
- Books: Grinold & Kahn; Isichenko; López de Prado (AFML 2018, MLAM 2020); Tulchinsky et al.; Aronson.
