# Map: roadmap and research history (as of 2026-10-21, main `db82817`)

## 1. Purpose and scope

This part of the system is the desk's research governance: the dated roadmap docs, the frozen
protocol files, the trial and confirmation ledgers, and the operator decision gates. Together they
decide which hypotheses get tested, how a result can reach a Telegram card, and what is deferred.
This map rebuilds the dated record of every lane tried (2026-09-16 to 2026-10-21), finds the
failure patterns that recur, and compares what the roadmap prioritises now with the operator's
north star. The north star, restated 2026-10-14: a steady stream of high-quality cards, with risk
controls, and allocation decisions that account for existing positions.

## 2. Components and contracts

**Roadmap documents (the queue itself)**
- `docs/alpha-roadmap.md` is the "canonical alpha work queue" (line 3). Its header still says
  "updated 2026-09-19", but entries run to 2026-10-21 (line 627). It holds at least eight
  ordering sections: "Current priorities after the whole-stack survey" (27–220), "Alpha expansion
  workstreams" (269–456), "Stage layering" L1–L5 (458–500), "Desk direction" (502–630),
  "Alpha-discovery sprint" (632–669), the A1–A5 queue (671–828), the "Funnel expansion" ranking
  (830–888) and the "Source calibration … current ordered priorities" (1105–1157). This is despite
  its own rule to "record dependencies here instead of creating a second competing roadmap" (684).
  `CLAUDE.md` still points readers to the 9/18 section as the ordered plan.
- `docs/roadmap.md` is historical (Phases 1–45). It explicitly hands the alpha queue to the alpha
  roadmap (lines 7–26, 1140–1156).
- Dated surveys that changed direction: `alpha-stack-survey-2026-09-18.md` (S1–S4),
  `alpha-expansion-survey-2026-09-23.md` (WS1–3),
  `alpha-pooled-mining-research-2026-09-28.md` (pooled design and power arithmetic),
  `alpha-data-augmentation-2026-10-18.md` (free point-in-time data pack).
- The operator memory notes (`MEMORY.md` and per-workstream files) are the de facto decision log,
  holding approvals, rulings and live state.

**Frozen-protocol contract (research → evidence)**
- Every lane is a JSON file under `config/research/**`, pinned by SHA-256 and committed before any
  data is read. The pooled lane's typed form is `CampaignProtocol`
  (`agentic_trader/research/pooled/campaign.py:246`), with the subclasses `Intraday` (:403),
  `Events` (:451), `ShortVolume` (:494) and `Insider` (:555). `gates` (:375) requires check A
  (power) and check C (null) for `fixed_set`, plus check B (search power) for `genetic`.
  `require_campaign_ready` is at :393.
- One label for every pooled lane: `BracketSpec` (`research/pooled/cube.py:57-62`), instantiated as
  a 10:35 entry, 2×ATR14 stop, 3R target and 20-session hold
  (`config/research/pooled/insider-v1.json:16`). Selection is top decile (`:316`;
  `select_top_fraction`, `research/pooled/formula.py:195-215`). Discovery needs t ≥ 3 (`:38`) on
  the paired edge against every eligible name (`paired_edge_test`, `research/pooled/stats.py:142`).
- The ledgers are separate. The per-symbol lifetime family is `family/all`
  (`storage/alpha.py:185-192`), with per-symbol holdouts (`begin_holdout`, `storage/alpha.py:203`).
  The pooled lane keeps its own ledger and never touches `family/all` (`storage/alpha.py:37`); its
  calls are `reserve_pooled_family` :1079, `charge_pooled_formula` :1096 and
  `consume_pooled_confirmation` :1141. Through `JournalLedger` (`research/pooled/ledger.py:35`),
  charges are committed before scoring, and the one-use, lane-wide confirmation window
  (2024-01-02 → 2026-07-31) is consumed before it is read (`docs/alpha-pooled-mining.md:617-622`).
  The spread lane uses `consume_lane_confirmation` (`storage/alpha.py:1173`).
- Each lane excludes the families tried before it (`insider-v1.json:40`: 36 ids). Realized-vol
  operators are forbidden (`:307`), so the `vol_20` lead cannot be mined on the 2024–2026 window.

**Seams into production (what research can change)**
- *Card source and ranking*: `TradingCopilot.run_scan` (`agent/copilot.py:682`) ranks approved
  candidates by `setup_quality` (:1172-1177). Budgets are 1 card per scan, 2 per session and 1 per
  group (`config/config.yaml:443-445`; logic `copilot.py:1271-1280`), plus one PEAD drift card per
  session (`config.py:178`).
- *Measured-evidence loop*: every full-universe scan journals `scan_candidates_ranked`
  (`_journal_scan_ranking`, `copilot.py:1851-1925`). The daily `compute_card_stats` (`research/setups/card_stats.py:120`)
  feeds the card evidence lines and `card_policy.decide` (`execution/card_policy.py:37`). `cards
  outcomes` `summarize` (`research/setups/outcomes.py:857`) adds blocks for LLM gate, exposure,
  slippage, news, regime, recurrence and bracket counterfactual.
- *Operator switches* (off → preview → enforce): `card_policy` ships `off` (`config.py:223-236`)
  and is still `off` (`config/config.yaml:454`). `book_sizing` (:466) and `corporate_actions` (:512)
  moved to `enforce` on 2026-10-20.
- *Probe lane*: `probe_block_reason` (`storage/probe_state.py:45`) and catalog checks in
  `_alpha_entry_rejection` (`storage/workflow.py:478-524`). The probe settings are
  `probe_risk_dollars` 100 and `probe_term_days` 30 (`config.py:829-831`). The only
  enrolment is PEAD long, which expires 2026-10-28 (`docs/alpha-roadmap.md:574-575`).

## 3. Data flow

**Research loop.** A hypothesis (survey or live journal) goes to a spec, a frozen JSON with its
SHA, a build reviewed by subagent-driven development, a merge and a deploy. Checks A and C then run
detached, outside scan windows. The operator approves a campaign or study; it charges, consumes and
writes a results doc (bundled into the next PR, per memory `bundle-docs-into-workstream-prs`). The
lane is then closed, with status lines added to `alpha-roadmap.md`, `CLAUDE.md` and memory. A pass
does not reach cards: it only makes a *separate* spec eligible, such as the pooled "Part 2 probe
spec" (`alpha-pooled-mining.md:1016`), and that path is unbuilt.

**Live loop.** Scans write to the journal. `card_stats` is computed at 08:30 ET. The card shows its
measured record. `cards outcomes` attributes outcomes, a pattern found there such as recurrence is
tested in a frozen replay, and that test either yields a construction rule or closes. Only
the native strategies, with `setup_quality` ranking, and the PEAD probe produce cards today.

## 4. Evidence: dated timeline of hypotheses and outcomes

| Date | Lane / hypothesis | Outcome | Source |
|---|---|---|---|
| 09-16 | Per-symbol ETF32 random+genetic, 3,648 trials; nested walk-forward 1,368 | 0 finalists (max DSR 0.86); sparse profitable controls rejected 5/5 by cumulative DSR | `alpha-research-2026-09-16.md` |
| 09-16 | A1b/A2a calibration, 1,952 jobs each | Null criteria pass; planted power insufficient | `alpha-roadmap.md:676,697-700` |
| 09-17 | Forecast comparison (78), open-gap policy (20), ETF session (81), continuous timed (48), sector panel (32), persistent book (80+80), volume calibration (80) | 0 passes; QQQ momentum fails 5 bp stress; sector vol-momentum +3.6% at 1 bp, −4.3% at 5 bp | `alpha-roadmap.md:832-845,928-948,1015-1020,1090-1095,1146-1151` |
| 09-17/18 | 64-equity Ridge panel (96), matched controls (90), factor controls (54) | Ridge is a "lead"; vol style has higher IC every year; no incremental winner | `alpha-roadmap.md:1183-1191,1213-1219` |
| 09-18 | Power ablation, 800 searches | Planted 0/64; GTC entries suppress ~90% of pulses | `alpha-power-diagnosis-2026-09-18.md` |
| 09-19 | Equity mining 1,024 + 2,048 + 512 trials; IEX smoke | 0 gate-passing leaders | `alpha-roadmap.md:117-127,138-140` |
| 09-21 | Lifetime attribution (P1 session entry) | Conversion restored (exploratory); confirmatory run never executed | `alpha-lifetime-attribution-2026-09-21.md` |
| 09-23 | Setup-outcome study (15,644 setups) | `setup_quality` uninformative; longs +0.03R; Ridge ranker *worse* in holdout; `vol_20` (ρ 0.049) and `sector_rel_mom_60` survive Holm | `setup-outcomes-2026-09-23.md:74-114` |
| 09-23 | Short suppression (fresh 2017–21) | Fired: −0.33R, so `allow_short: false` | `setup-baserates-short-2026-09-23.md` |
| 09-26 | **PEAD v2** a priori | **Long passes** (+0.149R; paired +0.055R); short fails | `apriori-pead-2026-09-26.md` |
| 09-30 | Pooled literature: `high52`, `reversal-lowmax` | Fail P2/P3: paired +0.012R/+0.008R; picks ≈ control ≈ +0.15R | `alpha-pooled-literature-2026-09-30.md:163-176` |
| 10-02 / 10-05 | Pooled genetic v2 (top-3), v3 (decile) | Check B fails 1/10, then 3/10; genetic lane parked | `alpha-roadmap.md:412-437` |
| 10-07 | Pooled fixed set v4 (8 literature formulas) + label diagnostic | No finalists (best t 0.76); no return-space signal | `alpha-roadmap.md:658-665`; memory `alpha-discovery-sprint` |
| 10-08 | Card funnel diagnostic | 23% target / 77% stop, −0.39R; `setup_quality` ≈ random; 9/10 cards expired untapped | memory `desk-direction-2026-10-08` |
| 10-10 / 10-11 | Spread pairs v1, v2 | Both fail discovery (−0.30, then +0.02 bp/day) | `alpha-spread-results-2026-10-1{0,1}.md` |
| 10-12 | Ex-dividend run-up | Fails P2: leg +0.069R vs placebo +0.046R | `apriori-exdiv-2026-10-12.md` |
| 10-14 | Setup recurrence (live split +0.96R) | `no_change`: repeats −0.067R | `setup-recurrence-2026-10-14.md` |
| 10-14 | Pooled intraday fixed set | No finalists (7/8 negative) | `alpha-roadmap.md:597-606` |
| 10-15 | Pooled events fixed set | No finalists (`quiet_momentum` t −3.34, negative) | `alpha-roadmap.md:619` |
| 10-16 | Cohort v3 × 3 fixed sets | No finalists; 48 formula–cohort pairs, 0 finalists | `alpha-pooled-cohort3-2026-10-16.md` |
| 10-18 | Bracket counterfactual (5 arms) | `no_change`; live ratchet −0.013R; every arm trails SPY by 0.10–0.15R | `bracket-counterfactual-2026-10-18.md:3-9,100` |
| 10-20 | Survivorship spike | Delisted bars available; membership history is not | `survivorship-spike-2026-10-20.md` |
| 10-20 | Short-volume × 2 cohorts | No finalist; `shorts_retreat` t +3.40 in discovery, +0.006R in selection | `alpha-pooled-short-v1-2026-10-20.md` |
| 10-21 | EDGAR insider × 2 cohorts | Built; checks A/C running; campaigns need approval | `alpha-roadmap.md:627` |

**Tally.** About 8,900 per-symbol trials by 2026-09-28 (`alpha-roadmap.md:361-364`), 64 pooled
formula–cohort pairs, and about 25 frozen studies. The result is **zero promotions**. One positive
a priori leg (PEAD long, paired +0.055R) is live as a probe; one rule fired that restricts trading
(no native shorts); and two weak live features (`vol_20`, `sector_rel_mom_60`) are parked as
prospective-only (`alpha-roadmap.md:299-303`). From 10-07 to 10-20 there were 14 consecutive
negative outcomes.

**Recurring failure patterns**
1. **Positive leg, zero paired edge.** Long brackets earned about +0.15R on cohort v2 whatever the
   formula. The control earned the same, through market drift and current-membership survivorship
   (`alpha-pooled-literature-2026-09-30.md:163-169`; PEAD's control +0.094R; exdiv placebo +0.046R).
2. **Discovery winners fail out of sample.** Ridge ranker fold 3, `shorts_retreat`
   (+3.40 → +0.88), `quiet_momentum` (−3.34 → −1.69 on v3), recurrence (+0.96R live → −0.067R).
   The gates did their job each time.
3. **Construction defects found late.** pead-v1 calendar gap; spread v1 with 84% of entries beyond
   the stop; GTC suppression; the GPOR +1,546R label (`alpha-roadmap.md:332-333`, still open);
   `load_config` ignoring `corporate_actions` before 10-13; Juneteenth before 2022; the
   unevaluable VWAP seed.
4. **Measurement lanes started but never read.** The IEX shadow-forward lane has never scored
   since 09-17 (`capture_error`; sparse layout deferred, `alpha-roadmap.md:667-668`). The daily
   panel has no predeclared read rule (`alpha-daily-panel.md`). `card_policy` preview was never
   turned on.
5. **Power below the plausible effect.** By the lane's own arithmetic it detects 0.08–0.10R,
   while US-large-cap formula ICs map to about 0.02R (`alpha-pooled-mining-research-2026-09-28.md:5,51,119`:
   "expected to produce few discoveries").

## 5. Strengths worth preserving

- **Pre-registration with SHA pinning, charge-before-read and one-use windows.** Every negative
  above is trustworthy, and the desk never promoted a fluke such as `shorts_retreat`.
- **Session-paired edge against every eligible name, with a session-block bootstrap.** This test
  is what exposed that leg means are drift (PEAD's paired SE was 2.4× smaller,
  `alpha-pooled-mining-research-2026-09-28.md:39`).
- **Checks A and C before any campaign, and parking a lane when check B fails.**
- **Fast, cheap lanes.** One lane is about one SDD day plus 3–10 h of compute, reusing cached
  cubes. Each new field set (events, short, insider) is a small `CampaignProtocol` subclass.
- **Honest live measurement.** Cards state their measured record; `cards outcomes` decomposes
  selection, LLM, exposure, slippage, news, regime and counterfactuals.
- **Negative results fed back into trading safety.** Shorts were disabled, the GTC→v5 entry
  lifetime was fixed, and earnings blackout and corporate-actions enforce went live.

## 6. Gaps, weaknesses, risks and inconsistencies

1. **The live card stream is built from a selector known to underperform, ranked by a heuristic
   known to be uninformative.** Ranking is still `setup_quality` (`copilot.py:1172-1177`), although
   it was measured uninformative (`setup-outcomes-2026-09-23.md:80-81`) and no better than random
   live (memory 10-08). Native setups trail SPY by 0.10–0.15R in every bracket arm
   (`bracket-counterfactual-2026-10-18.md:100`). `card_policy` is still `off`
   (`config/config.yaml:454`). The agreed definition of a reasonable card, "EV ≥ 0 after cost"
   (`alpha-roadmap.md:507-514`), therefore has no enforcement path. Turning enforcement on would
   withhold most native cards (TREND_PULLBACK LONG −0.376R on 34 mature, memory
   `honest-cards-pr-a-workstream`). This quantity-versus-quality conflict is unresolved and
   recorded only as "operator decision".
2. **Roadmap drift since the 10-14 restatement.** Of the 10 PRs since then (#118–#128), five are
   pooled lanes (#120, #121, #122, #126, #128), none of which can affect a card without an unbuilt
   Part 2 path (`alpha-pooled-mining.md:1016`). None changed card selection or the card source.
   Of the operator's ask ("enhance DSL, universe, cadence"), cadence went backwards: the weekly
   miner was retired (`alpha-roadmap.md:594-595`), and no scheduled mining exists.
3. **Allocation is nominal.** `book_sizing` is in `enforce`, but all six preview factors were 1.00
   against a one-share book (`config/config.yaml:466`). The convex optimiser is shadow-only. The
   book is nearly empty because most cards expire untapped (9/10 per memory 10-08). North-star part
   (3) has no live effect.
4. **The confirmation resource is nearly spent and partly contaminated.** The 2024–26 window is the
   lane-wide last historical confirmation (`alpha-pooled-mining.md:617-622`). The literature
   entries and PEAD already read it (`alpha-pooled-literature-2026-09-30.md:193`). The next single
   formula that survives selection consumes it for all future lanes.
5. **Prospective evaluations have no power plan.** The `vol_20`/`sector_rel_mom_60` read
   (2026-10-21 → 11-04, `alpha-roadmap.md:299-303,630`) runs on about 150–200 mature candidates
   (about 3.3 per scan, memory 10-08). SE(ρ) ≈ 0.08, so ρ ≈ 0.05 is undetectable; about 2,600
   would be needed. The PEAD probe needs about 440 trades to confirm +0.149R. At most one drift card
   per session, each needing a tap, means it reaches neither that nor the 10-trade comparability
   floor (`apriori-alphas.md:199-201`) before its 10-28 expiry.
6. **Items deferred repeatedly.**
   - Cross-sectional DSL operators: recommended 09-18, 09-23 and 10-18
     (`alpha-data-augmentation-2026-10-18.md:62,93`); still absent
     (`research/alpha/operators.py:108-167`).
   - Point-in-time membership: A4 09-17, then 09-23, then deferred again 10-20.
   - L3 selector seam, L4 evaluator split and L5 single label kernel (`alpha-roadmap.md:494-496,669`).
     Three bracket implementations remain: live 2R plus ratchet
     (`config/config.yaml:274,419-425`), pooled 3R, and the apriori bracket.
   - A2 advisory exit cards (`alpha-roadmap.md:164-172`).
   - Probe risk cap as an admission invariant (`alpha-roadmap.md:160-163`; still absent at
     `workflow.py:478-524`).
   - Confirmatory session power run (`alpha-roadmap.md:61-64`, now moot).
   - Funnel stage scoreboard (`alpha-stack-survey-2026-09-18.md:237-241`).
   - Delayed-entry/tap-latency study (`alpha-roadmap.md:357-358`).
   - Ratchet-off construction test (10-18). The ratchet is measured harmful but stays enabled
     (`config/config.yaml:420`).
7. **Roadmap legibility.** The header is stale; the doc has eight competing orderings; and the "Next"
   items of the 9/18–9/22 section never ran. A successor reading the CLAUDE.md link would pick the
   wrong queue.
8. **Survivorship stays unpriced.** Every leg, including PEAD's (the Nasdaq calendar drops delisted
   names; memory 09-24), is an upper bound.

## 7. Improvement opportunities

1. **Card stream first (days, no new research).**
   - Turn `card_policy` to `preview` now. It is measurement only.
   - Replace the `setup_quality` sort with a predeclared alternative through a minimal L3 selector
     seam. A shadow A/B of `vol_20` against a neutral tie-break is the obvious candidate.
   - Run the declared "ratchet off" replay; the bracket counterfactual cache already exists.
   - Run one frozen replay test of native setups against the same-session eligible-universe control
     under the identical bracket. A negative paired edge would justify retiring or replacing the
     native selector rather than tuning it.
   - Expected effect: card quality moves from "known negative versus SPY" toward "at least neutral",
     with honest withholding visible in the journal.
2. **Give mining a cadence that creates new data: a prospective pooled scorer.** A daemon worker,
   modelled on the daily-panel worker, would each session score every frozen fixed set (about 72
   formulas) and their every-eligible control at D−1, and journal the decile picks. Mature labels
   would come from the existing cube labeller. This turns every "closed" lane into forward
   evidence, avoids the lane-wide window entirely, and supplies a ready card source for anything
   that confirms. It detects about 0.10R in two years (`alpha-pooled-mining-research-2026-09-28.md:85-94`).
3. **Spend the last historical confirmation on the most plausible single trial: a predeclared
   composite.** Add `cs_rank`/`cs_zscore`/`group_demean` and freeze one equal-weight composite of
   literature-signed fields (reversal, 52-week high, PEAD, short ratio, insider buying). This
   follows Gu–Kelly–Xiu (RFS 2020) and Jensen–Kelly–Pedersen (JF 2023): US predictability lives in
   combinations of weak signals. As cheap baselines on the same cube with a time-interval holdout,
   use Chen–Zimmermann's Open Source Asset Pricing signal definitions and post-publication returns
   (as ex-ante priors), and Qlib's Alpha158 + LightGBM.
4. **Add an ex-ante yield gate before building a lane.** Expected δ comes from the literature
   effect, haircut 58% for post-publication decay (McLean–Pontiff), mapped by δ ≈ 3·IC. Compare it
   with the lane's detection floor. Lanes below the floor become prospective-scorer entries only.
5. **Close measurement loops with dates and rules.**
   - PEAD renewal on 10-28: decide on workflow and kill criteria, not significance.
   - `vol_20`: instead of the underpowered live window, a declared second use of the 2017–21 replay
     (11,480 setups, `setup-baserates-short-2026-09-23.md:30`) has SE ≈ 0.01.
   - IEX forward lane: retire it or write its sparse-layout spec.
   - Daily panel: write a read rule.
6. **Consolidate the roadmap.** Keep one dated queue and archive superseded sections. Build the
   stage scoreboard; it doubles as the north-star dashboard.
7. **Price survivorship before the next confirmation spend.** Build a dated delisting list from
   EDGAR Form 25/15 (`survivorship-spike-2026-10-20.md`), and re-check PEAD's control on it.

## 8. Open questions

- Live state that could not be read without the CLI: cards per session and tap rate since 10-08;
  PEAD drift cards sent or executed since 10-14; daily-panel matured outcomes; how many
  `vol_20` candidates are mature.
- Does `excluded_families` (`insider-v1.json:40`) forbid a composite built from already-tested
  families, or only re-testing them singly?
- Does the operator consider native cards a workflow exercise (keep the stream) or a claim of edge
  (enforce `card_policy`)? The 10-08 definition implies the latter.
- Should the 2024–26 window count as fresh for the pooled lane, given the literature/PEAD reads and
  the human inspection of results there?
- Does the insider campaign get approved, and does any pass change the plan given gap 4?
