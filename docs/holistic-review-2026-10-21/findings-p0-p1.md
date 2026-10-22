# Holistic review findings register — P0, P1 (22 findings)

Each canonical finding as consolidated from the seven expert lenses, followed by its independent
fact check (a skeptic told to refute it against the code) and value review (priority and horizon).
Where the fact check corrected a claim, the corrected claim governs. See [README](README.md).

<a id="c01"></a>

## C01 — Stop launching single-source, single-formula pooled fixed-set lanes: by their own power curve they cannot detect literature-sized edges, and the pending insider campaigns should not run as designed

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | now (days) | research_methodology | stop_doing | high | S | partially_confirmed |

_lenses: quant, ml, architecture, product, literature_

### Claim (as consolidated)

Each new data source becomes a fixed set of about 8 standalone, long-only, top-decile formulas. Each runs on the 2016-08→2021-12 discovery window with one 20-session 2xATR14/3R bracket and is gated by t≥3, 3 of 4 positive blocks, a 0.5x selection haircut, Holm and a leg ci90 above 0 (campaign.py:732-739, 789-794, 830-842).

What the 64 formula-cohort pairs show:
- No finalist.
- t mean −0.06, sd ~1.25, which is inside check C's null spread. Seed-t sd is 1.11–1.27, so the nominal t≥3 is about 2.4–2.7 once calibrated.
- Median implied SE is ≈0.017R, giving an MDE of ≈0.065R.
- Simulated end-to-end power: 1% at 0.03R, 11% at 0.044R, 31% at PEAD's 0.055R.

Literature priors after post-publication decay and the long-leg share sit 3–13x below that MDE. Chen-Velikov's 4–20 bp/month long-short is ≈0.005–0.024R; single-paper effects are ≈0.03–0.05R. The lane's own design doc expected 'few discoveries', and its contract says PEAD-sized edges belong to the a priori catalog.

Five of the last ten PRs (#120, #121, #122, #126, #128) were such lanes. None can reach a card, because the Part 2 probe spec is unbuilt.

The pending insider-v1 and cohort3-insider-v1 campaigns are therefore lottery tickets: null fixed-set replicates reached confirmation in 1 of 40. They are drawn against the single-use, lane-wide 2024-01→2026-07 confirmation window.

### Evidence

- agentic_trader/research/pooled/campaign.py:732-739, 789-794, 830-842 (stage gates); config/research/pooled/cohort3-daily-v1.json, insider-v1.json
- docs/alpha-pooled-checks-v4-2026-10-07.md, alpha-pooled-intraday-v1-2026-10-14.md, alpha-pooled-events-v1-2026-10-15.md, alpha-pooled-cohort3-2026-10-16.md, alpha-pooled-short-v1-2026-10-20.md (result tables; check C null t sd)
- docs/alpha-pooled-mining.md 'Purpose', 'Caveats' (PEAD-sized edges belong to the a priori catalog), :1014-1017 (Part 2 spec)
- docs/alpha-pooled-mining-research-2026-09-28.md:51, 85-94, 119 ('expected to produce few discoveries')
- docs/alpha-pooled-literature-2026-09-30.md (literature entries paired +0.012R / +0.008R)
- git log 9cfacc4..db82817 (#120, #121, #122, #126, #128 are pooled lanes)
- ~/agentic-trader-research/holistic-review-20261021/pooled64.py and endtoend_power.py

### Recommendation

1. Freeze the pattern. Add no new single-source fixed set on the 2016–21 window under the 20-session bracket, and no new per-source CampaignProtocol subclass.

2. Do not run the insider campaigns as designed. At most, run discovery-only diagnostics that cannot freeze candidates or consume the confirmation window. See also C19 (CMP fidelity).

3. Add an ex-ante yield gate before freezing any lane, written as a one-page check:
   - E[edge_R] = published long-short spread × (hold/21) × 0.42 post-publication × long-leg share / median risk-unit %.
   - Compare it with the MDE obtained by planting δ on the formula's own picks inside check C.
   - Hypotheses that fail go to the composite or the forward scorer.

4. Every result table reports edge ci90, calibrated t (t divided by check C's null sd) and MDE80, so that a null reads 'edge < X R at 90%'.

5. Redirect the capacity to card selection, event entries and evidence work. Track the share of research PRs that change a card source or selection, with a target of at least half.

### North-star impact

Frees about half of recent engineering effort (about one development day plus 3–10 compute hours per lane) from tests that structurally cannot produce a card. It also protects the last clean historical confirmation window.

### References

- Harvey, Liu & Zhu (2016), ...and the Cross-Section of Expected Returns, RFS 29(1) — t>3 is calibrated for searched factors, not a handful of predeclared priors (https://doi.org/10.1093/rfs/hhv059)
- Harvey & Liu (2020), False (and Missed) Discoveries in Financial Economics, JF 75(5) — Strict hurdles trade false discoveries for a large missed-discovery rate
- McLean & Pontiff (2016), Does Academic Research Destroy Stock Return Predictability?, JF 71(1) — About 58% post-publication decay; the haircut in the yield gate (https://doi.org/10.1111/jofi.12365)
- Hou, Xue & Zhang (2020), Replicating Anomalies, RFS 33(5) — 65% of 452 anomalies fail once microcaps are mitigated, which is the regime of cohorts v2/v3 (https://doi.org/10.1093/rfs/hhy131)
- Chen & Velikov (2023), Zeroing in on the Expected Returns of Anomalies, JFQA 58(3) — Average anomaly nets 4 bp/month after costs and publication; combinations about 20 bp

### Fact check — partially_confirmed

**Corrected claim**

The core holds. Every fixed set since v4 is 8 standalone, long-only, top-decile formulas on the 2016-08 to 2021-12 discovery window. Each uses one 20-session 2xATR14/3R bracket and the gates at campaign.py:732-739, 789-794 and 830-842. The 64 formula-cohort pairs look like noise: t mean -0.06, sd 1.26, against check C seed-t sd of 1.11-1.27. The lane's own contract (alpha-pooled-mining.md:43-45, 1387-1388) puts its reach at about 0.08-0.10R and sends PEAD-sized (about 0.055R) edges to the a priori catalog. Average post-publication anomaly edges (Chen-Velikov 4-20 bp/month, about 0.005-0.024R) are far below that reach. No pooled Part 2 probe spec exists, so a pooled confirmation cannot reach a card. Five of the ten PRs #118-#128 were pooled lanes. No ex-ante yield or MDE gate exists, and the insider campaigns are pending.

Corrections:
(1) The 1-in-40 null confirmation rate comes from one protocol, intraday-v1. The other fixed-set check C runs that report it are 0/40: v4, events-v1, short-v1 and cohort3-short-v1. Across reported checks the rate is about 1 in 200 replicates. A null campaign therefore rarely burns the window. The real cost is low expected yield and engineering time, not window risk.
(2) The 1%/11%/31% power figures come from the reviewer's own normal-approximation simulation, not the lane's power curve. The lane's check A shows 0.40 (v2) and 0.51 (v3) detection at 0.05R, though the docs call check A an upper bound.
(3) MDE ≈0.065R is a discovery-only figure (t>=3, 80% power, median SE). The documented contract and check A put 80% end-to-end detection at about 0.08-0.10R. The gap is larger than stated.
(4) The '3-13x below MDE' prior is the generic Chen-Velikov average, not an insider prior. Applying the finding's own formula to Cohen-Malloy-Pomorski's 82 bp/month opportunistic-insider return gives roughly 0.035-0.05R, which is 1.3-2x below MDE and about 10-35% power. The insider campaigns are underpowered, especially the sparse conditional buy-side formulas on large caps, but they are not 3-13x short.
(5) 'Expected to produce few discoveries' comes from the literature research report (alpha-pooled-mining-research-2026-09-28.md:119), not the design doc.
(6) The git range 9cfacc4..db82817 contains 9 PRs; 'last ten' needs #118.

**Checked**

- agentic_trader/research/pooled/campaign.py:732-739 (discovery gate: t >= min_t, positive blocks, leg mean > 0, min sessions), :789-794 (selection: edge > 0 and >= min_fraction_of_discovery x discovery edge), :830-842 (Holm, leg ci90 > 0, trimmed mean, recent edge), :868-884 (window consumed only after something freezes)
- agentic_trader/research/pooled/null_check.py:121-150 (false acceptance = confirmed; 'reached_confirmation' = replicates that froze a candidate)
- config/research/pooled/{campaign-v4,intraday-v1,events-v1,short-v1,insider-v1,cohort3-daily-v1,cohort3-insider-v1}.json: all fixed_set, formula_budget 8, top_fraction 0.1, discovery 2016-08-01..2021-12-31, selection 2022..2023, confirmation 2024-01-02..2026-07-31, bracket 2xATR14 / 3R / 20 sessions, min_t 3.0, 3 of 4 blocks, min_fraction_of_discovery 0.5, Holm alpha 0.05
- docs/alpha-pooled-checks-v4-2026-10-07.md (campaign table; check C 0/40, three carried past discovery, none reached confirmation; check A 0.40 at 0.05R, 0.84 at 0.08R)
- docs/alpha-pooled-intraday-v1-2026-10-14.md (check C 1 replicate reached confirmation, seed-t sd 1.17; campaign table)
- docs/alpha-pooled-events-v1-2026-10-15.md (check C 0 reached confirmation, 3 no_confirmation_candidates, seed-t sd 1.23)
- docs/alpha-pooled-cohort3-2026-10-16.md (seed-t sd 1.27/1.19/1.11; check A v3 0.51 at 0.05R, 0.76 at 0.08R; three campaign tables)
- docs/alpha-pooled-short-v1-2026-10-20.md (seed-t sd 1.13/1.18; 0 reached confirmation on both; shorts_retreat t +3.40 failed selection)
- Re-ran ~/agentic-trader-research/holistic-review-20261021/pooled64.py: 64 rows match the docs; t mean -0.063, sd 1.257; median implied SE 0.0169R; MDE80 at t>=3 = 0.065R
- Re-ran ~/agentic-trader-research/holistic-review-20261021/endtoend_power.py: frozen pipeline at se 0.017 gives 0.01 / 0.11 / 0.31 confirmation at 0.03 / 0.044 / 0.055R
- docs/alpha-pooled-mining.md:27-45 (Purpose: lane detects about 0.08-0.10R; smaller edges are the a priori catalog's job), :1370-1390 (Caveats: PEAD-sized edges belong to the a priori catalog; check A detection is an upper bound), :1014-1018 (Part 2 probe spec only after confirmation), :1143-1255 (insider lane: fixed set, conditional buy-side formulas, campaigns pending, finalist would also need a live feed contract)
- docs/alpha-pooled-mining-research-2026-09-28.md:51 (AlphaAgent IC to about 0.02R), :85-94 (MDE table: 0.080R at t=3 over discovery), :119 ('expected to produce few discoveries')
- docs/alpha-pooled-literature-2026-09-30.md:7,11,70-71 (paired +0.012R / +0.008R)
- docs/superpowers/specs/ (no pooled Part 2 probe spec); grep for MDE / yield gate / calibrated t in research/pooled and roadmap: none, so the recommendation is new
- docs/superpowers/specs/2026-10-20-pooled-insider-design.md and docs/alpha-data-augmentation-2026-10-18.md:43 (no ex-ante effect-size or power estimate for insider)
- git log: #118-#128; #120, #121, #122, #126 and #128 are pooled lanes
- Web: Chen & Velikov JFQA 58(3) published abstract (4 bp/month average, at best 10 bp for the strongest, about 20 bp for combinations); McLean-Pontiff, HLZ and HXZ DOIs resolve; Cohen-Malloy-Pomorski 2012 (opportunistic insider trades 82 bp/month value-weighted)

**Bad references**

- 'null fixed-set replicates reached confirmation in 1 of 40' applies only to docs/alpha-pooled-intraday-v1-2026-10-14.md (check C). docs/alpha-pooled-checks-v4-2026-10-07.md, alpha-pooled-events-v1-2026-10-15.md and alpha-pooled-short-v1-2026-10-20.md (both cohorts) report 0 replicates reaching confirmation, so it does not generalize.
- 'by their own power curve': the cited power numbers come from the reviewer's scratchpad endtoend_power.py. The lane's own check A curves (0.40/0.51 at 0.05R; 0.84/0.76 at 0.08R) are more favourable.
- The 'lane's own design doc' quote ('expected to produce few discoveries') is from the literature research report alpha-pooled-mining-research-2026-09-28.md:119, not a design spec.

**Notes**

The code citations and gate parameters are exact. The 64-row table in pooled64.py matches the five results docs line for line, and its summary statistics reproduce. The Part 2 probe spec is unbuilt. The insider lane adds a further barrier: alpha-pooled-mining.md says a finalist would also need a live feed contract.

Two things strengthen the finding. Check A plants its edge over the full window across all names. The insider buy-side formulas score only in-condition names (x / cond), and purchases are sparse on large caps, so their real SE is likely larger than the median 0.017R. Check A's power is therefore overstated for them, as the short-volume doc already disclosed for its own 60% coverage. The documented MDE (0.08-0.10R) is also higher than the finding's 0.065R.

One thing weakens it: the main empirical case against the window being 'drawn against' is weak, because window consumption under the null is rare (about 0.5-2.5% per campaign). The stop-doing argument should rest on expected yield and opportunity cost, not on protecting the window.

All five references check out:
- HLZ: RFS 29(1), DOI hhv059.
- Harvey & Liu: JF 75(5), 2020.
- McLean-Pontiff: JF 71(1); returns 26% lower out of sample and 58% lower after publication.
- HXZ: RFS 33(5); 65% of 452 anomalies fail with microcaps mitigated.
- Chen-Velikov: the published JFQA 58(3) abstract gives 4 bp/month average and about 20 bp for combinations. The 2020 working paper said 8 bp/month; the finding uses the published figure correctly.

The recommendations (yield gate, calibrated t, MDE80 in result tables, no per-source protocol subclasses) are not already implemented anywhere in research/pooled or the roadmap.

### Value review — P0, now (days)

**Reasoning**

Fund it as a stop-doing decision, but trim the scope. The core diagnosis holds up and is mostly the lane's own documented design limit, applied consistently.

What the code and docs show:
- The gates are as stated. Discovery needs t>=3, at least 3 of 4 positive blocks, leg>0 and 400 sessions (agentic_trader/research/pooled/campaign.py:732-739). Selection needs >=0.5x the discovery edge (789-794). Confirmation needs Holm, then leg ci90>0, a positive trimmed mean and a positive recent-12-month edge (830-842).
- Check A's acceptance target is detection >=0.80 at 0.15R (docs/alpha-pooled-checks-v4-2026-10-07.md, "Check A").
- The lane's own caveats say discovery power is for 0.08-0.10R edges and that PEAD-sized edges of about 0.055R "belong to the a priori catalog" (docs/alpha-pooled-mining.md "Purpose" and "Caveats").
- The design report predicted reach of "1.6-2 times PEAD's paired 0.055R ... expected to produce few discoveries" (docs/alpha-pooled-mining-research-2026-09-28.md:119).

I re-ran scratchpad endtoend_power.py at se 0.017R. End-to-end confirmation probability is 1% at 0.03R, 11% at 0.044R and 31% at 0.055R. That run ignores the 3-of-4, Holm>1 and leg-ci90 hurdles, so the real figures are lower.

The 64 t-statistics (mean about -0.06, sd about 1.25) sit inside check C's null spread (seed-t sd 1.17 in docs/alpha-pooled-intraday-v1-2026-10-14.md "Check C"). The record therefore rules out edges of 0.06R and up. It does not rule out small ones. The current result tables don't make that readable.

The decisive product fact is that nothing the pooled lane confirms can become a card. A confirmation only earns a Part 2 spec, which has not been written (docs/alpha-pooled-mining.md:1016). The a priori catalog already has a working route to cards (apriori.pead_live -> screeners/earnings_drift.py, through the registry probe list). Five of the last ten merges (15e6939, d6fd22e, fe10fc6, 08a98ba, db82817) went into a route that cannot produce a card. Stopping that pattern is the cheapest and highest-leverage reallocation available for the north star. It is a decision plus two small reporting changes, not new infrastructure.

Trims and corrections:
1. The insider "do not run" is low-stakes either way. The development cost is sunk and the inputs are digest-pinned (67a262c0). Checks A and C are already running. Two campaigns cost a few compute hours. The real cost is about a 2.5% null chance per campaign of freezing something and consuming the 2024-01 to 2026-07 window (1/40 in check C, docs/alpha-pooled-intraday-v1-2026-10-14.md:46).

   Insider purchases are also the one source whose literature effect size is plausibly near the MDE. Lakonishok-Lee, Jeng-Metrick-Zeckhauser and Cohen-Malloy-Pomorski report roughly 50-80 bp/month abnormal returns for purchases, concentrated in small and mid caps, which is cohort v3's band. Applying the generic 0.42 post-publication haircut to that source overstates the gap.

   The stronger objection is the encoding. A daily decile among in-condition names, sparse on large caps (docs/alpha-pooled-mining.md "Insider lane"), compared against all eligible names repeats the weak pooled-PEAD encoding, not the event design that passed.

2. The yield-gate formula double-counts. Chen-Velikov's 4-20 bp/month is already post-publication and net of costs, so a second 0.42 factor should not apply to it. Keep the gate as a one-page pre-registration check, not new code.

3. Report "calibrated t" as advisory only. Check C's null uses 20-session blocks and misses longer dependence.

4. The PR-share KPI is process overhead; drop it.

Highest-value pieces, in order:
- A roadmap decision that freezes new single-source fixed sets under the 20-session bracket and the 2016-21 discovery window.
- Per-formula MDE80 and edge ci90 in every result table, computed by planting delta on each real formula's picks inside check C (minutes per set; check C took 42 s in v4). This turns the 64 nulls into bounds.
- An operator decision on insider that favours the alternative below over either running or shelving the campaigns.

**Risks**

- **Conflicts with the operator's explicit ask.** The operator asked to "expand our alpha mining operation by enhancing DSL, universe of symbols ... reasonable cadence". A bare "stop" reads as abandoning mining. Present it as a redirect to a better instrument: a risk-matched control, horizon-matched cubes, an event-entry selection rule, and a single pre-registered composite with cross-sectional operators. Name that replacement work in the same decision, or the freed capacity goes unused.
- **Insider is the source most likely to justify running.** Literature purchase effects are much larger than generic anomalies and concentrated in mid caps (cohort v3). Shelving it outright could forgo the most plausible remaining lead.
- **Forking paths.** Running insider "discovery-only" now, then deciding later whether to run the full campaign after seeing discovery, contaminates the pre-registration. Whatever is chosen must be declared before any result is read, and the formulas charged.
- **The calibration may mislead.** Calibrated t and the yield gate rest on check C's 20-session block null and a normal approximation, so both can be anti-conservative or mis-scaled. If calibrated t is used to relax gates retroactively, CLAUDE.md is breached: it forbids using diagnostics to relax gates or discount trials retroactively.
- **A lower v5 Holm gate raises the false-discovery rate** on the single historical window. It needs a spending rule or a prospective confirmation interval first.
- **The power numbers are approximations.** Per-formula SEs span 0.006-0.042R. Sparse insider deciles likely sit at the high end, so the median-SE MDE understates their gap and overstates fast formulas' gap.

**Prerequisites**

- Campaigns are approval-gated (CLAUDE.md, docs/alpha-pooled-mining.md "Fixed-set mode"), so the operator must decide on the pending insider-v1 and cohort3-insider-v1 campaigns. Do this before checks A and C finish, so the decision cannot be conditioned on their output.
- Record the freeze and the insider decision in docs/alpha-roadmap.md, together with docs/alpha-pooled-mining.md "Insider lane" pre-registered closure. Per the memory rule this goes into the next workstream PR, not a docs-only PR.
- A small check C extension that plants delta on each real formula's own picks, to produce per-formula MDE80. Then backfill MDE and ci90 columns for the 64 existing results from the stored result.json artifacts. Read-only over the existing cubes; nothing new is charged.
- A named replacement agenda with owners, so the freed capacity has somewhere to go. Candidates: insider as an a priori event leg; risk arrays (atr_pct, beta, sector) in the cube for a risk-matched control; an event-entry selection rule; one literature-signed composite.

**Better alternative**

Don't choose between running the insider campaigns as designed and shelving them. Recast the strongest insider hypothesis as a single a priori catalog leg instead. Reuse the already acquired, digest-pinned Form 345 data (67a262c0).

The leg:
- **Universe and trigger.** Opportunistic or cluster open-market officer/director purchases on cohort v3's mid-cap band.
- **Entry.** Once, on the first session after the EDGAR filing date, with no daily re-buying.
- **Control.** Event-matched: same-session names with a routine purchase, or a same-size non-event placebo.
- **Hold and criteria.** A horizon-matched session_count exit, judged on PEAD's P1-P4 criteria under the apriori-study manifest-first contract.

This is the design shape that actually passed (docs/apriori-pead-2026-09-26.md). It uses one trial, with a much smaller SE than a sparse decile. It does not touch the pooled confirmation window. And it rides the one research route that already reaches a Telegram card: alpha apriori-probe through the registry probe list and probe_block_reason.

Pair it with the cheap reporting fix: per-formula MDE80 and ci90 in check C and the result tables. The 64 nulls become "edge < X R" bounds that tell the operator which hypotheses are dead and which were simply untestable at this power.

---

<a id="c09"></a>

## C09 — Restart-timed swing scans spend the session card budget off-slot and starve the 14:35 suggestion scan; separate evidence scans from card slots

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | now (days) | operations | defect | high | S | partially_confirmed |

_lenses: architecture, product_

### Claim (as consolidated)

How scans are scheduled now:
- The swing scan runs every 4 hours from daemon start, with ScanBudget.FULL and next_run_time=now (service.py:581-593).
- The 'aligned with 4h candle closes' comment is false (config.yaml:330).
- The 15-minute intraday job runs with FULL budget, although native strategies emit only 4h and 1h setups (strategies.py:343-348; copilot.py:1093-1095).
- ScanBudget has no journal-only mode.

What this did since 2026-09-18:
- 15 of 41 cards went out off-slot, between 09:37 and 13:43 ET; only 6 came from 14:35.
- The daemon started 61 times in 33 days.
- In 7 of 13 journaled 14:35 scans, every candidate was refused with 'per-session budget spent' after a swing card had already used the budget.

Swing scans lack dynamic names, news and shadow evidence, and replay and card_stats model only the 10:35 and 14:35 slots. The base rate printed on swing cards therefore describes a different population.

### Evidence

- agentic_trader/cli/commands/service.py:581-626 (swing and intraday jobs with FULL budget; run at start)
- config/config.yaml:330; agentic_trader/config.py:187-190, 463
- agentic_trader/screeners/strategies.py:343-348 vs agentic_trader/agent/copilot.py:1093-1095
- agentic_trader/research/setups/replay.py:3-6; docs/card-evidence.md:376-380
- Production signals by NY slot: off-slot 15, 10:35 20, 14:35 6; live map Q5/Q7/Q16b (7 of 13 14:35 scans budget-spent; 61 daemon starts)

### Recommendation

Add ScanPurpose {CARD_SLOT, EVIDENCE}, or a ScanBudget.JOURNAL mode that journals the ranking but never records a signal or an outbox intent.

- Only the two suggestion slots are CARD_SLOT.
- Swing, intraday and the chat tool become journal-only, or are removed.
- Operator /scan previews by default.
- Remove run-at-start from any job that can send cards.
- Disable the 15-minute job until a 15-minute producer exists.
- Add a test that a daemon start cannot record a signal.

### North-star impact

Card timing becomes predictable, 14:35 supply roughly doubles, and every card's base rate describes the population it came from. The honest-card work depends on this.

### References

_none_

### Fact check — partially_confirmed

**Corrected claim**

The core defect is real.

**Confirmed parts**
- The daemon's swing scan runs on an interval from daemon start: every 4 hours, first run immediately, ScanBudget.FULL (service.py:581-593). The 'aligned with 4h candle closes' comment is wrong (config.yaml:330).
- The per-session native card budget is derived from every signal recorded that session, whatever scan triggered it (copilot.py:1268-1281). Swing cards, and Re-evaluate cards, therefore spend the same 2-card allowance as the 10:35 and 14:35 suggestion slots.
- ScanBudget has no journal-only mode (config.py:186-190).
- The 15-minute job runs with FULL budget and a 15m filter (service.py:596-626). Native strategies emit only 4h and 1h setups (strategies.py:343-348), so today it produces no candidates and spends nothing. Disabling it is hygiene and does not change 14:35 supply.

**Production data, verified read-only**
- 41 alpaca:paper signals since 2026-09-18: 15 outside the slot windows (09:37-13:43 ET), 20 in the 10:35 window (4 of them PEAD), 6 in the 14:35 window.
- In 7 of the 13 journaled 14:35 scans, every candidate was refused with 'per-session budget spent'. On each of those days an off-slot card had been sent earlier.
- There were 61 daemon runs in 33 days.

**Corrections**
1. **Not all 15 off-slot cards are restart-timed swing cards.**
   - 2026-09-24 13:06 CRM is a Re-evaluate.
   - 2026-10-01 11:53 CVX is the late 10:35 suggestion scan (journaled trigger=suggestion_scan).
   - 2026-09-18 12:50 TLT predates the 10:35/14:35 schedule (a1d97ad, 2026-09-22).
   - That leaves about 12 swing cards. Their timing comes from both restarts and the steady 4h interval phase: run 218878cc sent at 13:15 on both 9-29 and 9-30, and run 5a576d38 sent at 12:24 on 10-07.
2. **Swing scans do fetch news.** copilot.py:1251-1256 fetches news for every scan with no timeframe, and the swing job passes none. They lack only the dynamic universe and shadow evidence (copilot.py:880, 1194).
3. **card_stats does not model only the two slots.** It labels journaled universe-scope candidates from any trigger (outcomes.py:155-160; docs/card-evidence.md:372-380). Only replay (replay.py:1-7) is slot-only.
   - The base rate printed on a card is a pooled population: 27 suggestion-scan events and 4 swing-scan events.
   - The population argument should therefore be weaker. The pool mixes slot and off-slot decision times, and correlated swing repeats inflate n_mature. A 09:37 swing card reads a mostly pre-market 4h bar. It is not true that swing cards are measured against a different, slot-only population.
4. **This is a documented design choice, not an unknown bug.** The 2026-09-21 budget design spec states that the swing scan 'can therefore spend the session budget ... its cards count the same' (spec lines 188-190). It is still a consequential gap.
5. **Constraint on the recommendation.** Removing the run-at-start must keep a readiness `scan` observation at start (service.py:589-590; production.md:23-25). A journal-only start run would satisfy this.

The estimate that '14:35 supply roughly doubles' (6 to about 13) is plausible from the 7 budget-starved scans.

**Checked**

- agentic_trader/cli/commands/service.py:581-593 (swing job: interval hours=cron_hour_interval, ScanBudget.FULL, trigger SWING_SCAN, next_run_time=now; comment says the start run exists so the `scan` readiness component sees a run)
- agentic_trader/cli/commands/service.py:596-626 (intraday job: timeframe='15m', ScanBudget.FULL, next_run_time=now; registered only when non_universe_contracts is non-empty)
- agentic_trader/cli/commands/service.py:722-725 (both jobs registered; intraday gated by intraday_scan_enabled)
- config/config.yaml:329-331 ('Scan every 4 hours aligned with 4h candle closes': an interval from start is not aligned)
- config/config.yaml:10-94 (explicit contracts: futures plus SPY/QQQ/IWM/GLD/TLT/AAPL/MSFT/NVDA/AMD, so the intraday job is registered)
- agentic_trader/config.py:186-190 (ScanBudget has only FULL/SESSION/NONE; no journal-only mode)
- agentic_trader/config.py:455-466 (SchedulerConfig; intraday_scan_enabled=True at :463)
- agentic_trader/config.py:899-908 (non_universe_contracts)
- agentic_trader/screeners/strategies.py:337-349 (squeeze emits 4h and 1h); :155,:192 (trend pullback 4h); :390-421 scan_contract filters by timeframe
- agentic_trader/screeners/formulaic.py:50 (a registry alpha's timeframe comes from its definition, default 4h)
- agentic_trader/agent/copilot.py:1093-1095 (timeframe filter)
- agentic_trader/agent/copilot.py:1251-1256 (news is fetched for every scan with timeframe None, so swing scans also fetch it)
- agentic_trader/agent/copilot.py:1268-1281 (the per-session budget comes from every native signal recorded since session start, whatever its trigger)
- agentic_trader/agent/copilot.py:704-711, 880, 1194 (dynamic universe and shadow evidence only on suggestion scans)
- agentic_trader/research/setups/replay.py:1-7 (replay rebuilds only the 10:35 and 14:35 instants)
- agentic_trader/research/setups/outcomes.py:148-160 and card_stats.py:1 (card_stats labels journaled universe-scope candidates from any trigger)
- docs/card-evidence.md:372-384 (coverage section says swing scans are journaled and counted; swing repeats inflate n_mature)
- docs/production.md:22-26, 112-119
- docs/superpowers/specs/2026-09-21-scan-universe-and-suggestion-budget-design.md:86-87, 188-190 (the spec explicitly accepted that swing cards spend the session budget)
- scratchpad holistic-review/maps/live.md lines 75 and 97 plus the Q appendix
- Read-only psql: production alpaca:paper signals since 2026-09-18 by NY slot gave off-slot 15 (TREND_PULLBACK 09:37-13:43), 10:35 slot 20 (4 of them pead_long), 14:35 slot 6, total 41
- Read-only psql: scan_candidates_ranked has 27 suggestion_scan and 4 swing_scan events; 13 journaled 14:35 scans; 7 had every candidate refused with 'per-session budget spent' (9-25, 9-30, 10-01, 10-02, 10-05, 10-07, 10-08); on each of those dates an off-slot card came before 14:35
- Read-only psql: 61 distinct health/<run>/worker runs since 2026-09-18 (60 runtime_started audits); card_reevaluate_requested on 2026-09-24 at 10:53 and 13:05
- git log -S: 10:35/14:35 slots were introduced in a1d97ad on 2026-09-22

**Bad references**

- docs/card-evidence.md:376-380 is cited for 'card_stats model only the 10:35 and 14:35 slots', but it says the opposite: every full-universe scan (suggestion, swing, unrestricted /scan) journals scan_candidates_ranked, and swing runs add correlated repeats to n_mature.

**Notes**

Code claims were checked at db82817 in the read-only worktree. Production numbers came from the read-only psql wrapper (default_transaction_read_only=on); no identifiers were printed.

**Off-slot breakdown**
- The finding's 15 off-slot cards include:
  - one Re-evaluate, which uses a NONE budget for its own scan, but its signal still counts toward the derived session budget;
  - one late suggestion-scan card;
  - one card from before the 10:35/14:35 schedule existed.
- The live map's own count (14 of 37, from 2026-09-22, late 10:35 run excluded) is closer to the attribution the finding implies.

**The 7 budget-spent days**
- 9-25: EWJ at 10:38, then NWG at 13:06.
- 9-30: FCX at 10:39, then AAPL at 13:15.
- 10-01: CRM at 10:02 (swing), then CVX at 11:53 (late suggestion scan).
- 10-02: SEIC at 09:37 (swing), then AUGO at 10:38.
- 10-05: STX at 10:39, then ABBV at 11:32 (new run).
- 10-07: MU at 10:38, then MRK at 12:24 (same run, interval phase).
- 10-08: KO at 10:39, then AMGN at 13:43 (new run).

**Supporting detail**
- The intraday job is registered because non_universe_contracts includes the futures plus 9 explicit equities/ETFs.
- Since journaling started on 2026-10-09 there are only 4 swing_scan journal events, because zero-candidate scans write no event.
- Treat the recommendation as sound. Add two caveats: Re-evaluate and operator /scan cards also draw on the same derived budget, and the start-up readiness observation must be preserved.

### Value review — P0, now (days)

**Reasoning**

Fund it now. I checked the defect in the code. It is the cheapest lever in the review that affects the north star directly: card supply, card timing and honest base rates.

What the code does now:
- `register_swing_scan` gives the swing scan `budget=ScanBudget.FULL` and `next_run_time=datetime.now(UTC)` (agentic_trader/cli/commands/service.py:581-593). Every daemon start therefore runs a full universe scan that can send cards and that spends the same 2-card session budget (agentic_trader/config.py:187-190, 455-456).
- The config comment "aligned with 4h candle closes" (config/config.yaml:330) is false. The interval is anchored to the process start time.
- `ScanBudget` has only FULL, SESSION and NONE (config.py:187-190). No mode journals candidates without sending.

The effect, from the live map (maps/live.md gap 3 and recommendation 3; Q3, Q5, Q7, Q16b):
- 61 daemon starts in 33 days, including one at 10:50 ET today.
- 14-15 of about 40 cards were sent off-slot, between 09:37 and 13:43 ET.
- The 14:35 slot produced 6 cards in 24 sessions.
- 'per-session budget spent' appears 26 times.
- In 7 of 13 journaled 14:35 scans, every candidate was refused because a swing card had already taken the slot.

Why it matters beyond timing:
- A 09:37 card evaluates a 4h bar that is mostly pre-market.
- The 11 swing rows carry no shadow features (Q16c).
- Live-clock replay models only the 10:35 and 14:35 instants (agentic_trader/research/setups/replay.py:1-6).
- The same setup is re-journaled on every in-session restart, which inflates n_mature (docs/card-evidence.md "Limits", repeated setups).
- So the base rate printed on a card describes a different population from the card. Every planned honest-card or card_policy step (card_policy is still `off`) rests on that population being right.

This is a small change with a clear test: a daemon start cannot record a signal or an outbox intent. No other item in this review moves 14:35 supply and evidence integrity at the same time for so little cost. With 64 null mining tests, nothing upstream is about to produce better candidates, so making the existing funnel deliver and measure honestly is the right spend.

The 15-minute intraday part is cleanup with near-zero impact today. Native strategies emit only 4h and 1h candidates (agentic_trader/screeners/strategies.py:343-348). The 15m filter (agentic_trader/agent/copilot.py:1093-1095) removes them, and no active registry alpha produces 15m. Do it, but don't count it as leverage.

**Risks**

1. Readiness coupling. The `scan` readiness component has a 5-hour max age (agentic_trader/config.py:619, diagnostics/readiness.py:79). In practice the 4-hourly swing scan keeps it fresh, and the code comment at service.py:589-590 says that is why the scan runs at start. Removing the swing job outright would degrade readiness and raise monitor incidents after 14:35 and overnight. A journal-only swing scan avoids this. Otherwise the scan max-age rule has to become session- and slot-aware.

2. Evidence contamination stays. Journal-only swing rows must not enter `card_stats` or the `card_policy` denominator, or the repeated-setup inflation in docs/card-evidence.md "Limits" persists. Filter on `trigger` or de-duplicate by setup per session.

3. Total supply may fall. Off-slot sends made up about a third of cards. The cap is 2 per session, so 14:35 should recover most of them, but not every off-slot setup is still valid at 14:35. Measure cards per session for two weeks after the change.

4. Operator UX. Making `/scan` preview by default changes something the operator chose to have. An operator-initiated scan is explicit intent. Keep it as a separate decision, not bundled in.

5. Root cause untouched. The 61 restarts in 33 days are a separate operational problem (deploys, watchdog). They also truncate the in-memory scan digest. This fix removes their effect on cards but not the restarts themselves.

6. Missing zero-candidate journal. The 7-of-13 figure covers only journaled 14:35 scans. Zero-candidate scans write no event (copilot.py:1591), so the true counterfactual uplift at 14:35 is uncertain. "Roughly doubles" is a plausible upper bound, not a measured effect.

**Prerequisites**

- Decide the scan-readiness contract first: keep a journal-only swing cadence, or make `scan_max_age_seconds` session-aware.
- Confirm with the operator that cards come only from the 10:35 and 14:35 slots, plus explicit operator scans.
- Make `card_stats` and `card_policy` key off `ScanTrigger` (agentic_trader/execution/durable.py:68-77) so evidence-only rows are excluded.
- Add the regression test that a daemon start or swing run records no signal row and no outbox intent.
- No schema change is needed. Deploy through the usual pause-watchdog, then pause-daemon path.

**Better alternative**

A smaller version gets about 90% of the value:

1. Add one budget mode, `ScanBudget.JOURNAL`: rank and journal `scan_candidates_ranked`, but never record a signal or an outbox intent. Use it for the swing job. This keeps the readiness freshness that the swing job provides today.
2. Drop `next_run_time=datetime.now(UTC)` from the swing scan, or keep it only under JOURNAL.
3. Set `scheduler.intraday_scan_enabled: false` in config/config.yaml. The flag already exists at agentic_trader/config.py:463 and is checked at service.py:724, so this needs no code change.
4. Fix the false comment at config.yaml:330.

Skip the new ScanPurpose taxonomy and the change to the `/scan` default for now. Separately and in parallel, find out why the daemon starts about twice a day. Fewer restarts also fix the truncated scan digest and the repeated-setup inflation at their source.

---

<a id="c18"></a>

## C18 — The only research-positive card source (PEAD) is starved, and its probe expires 2026-10-28 with no continue or kill rule

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | now (days) | signals | improvement | high | M | partially_confirmed |

_lenses: product_

### Claim (as consolidated)

PEAD long passed all four predeclared criteria:
- +0.149R after cost;
- +0.055R over the every-reporter control (ci90 +0.021 to +0.089);
- a median of 2 qualifying events per session, and a p90 of 9.

Live, it is tightly capped: 1 drift card per session, decided only at 10:35, with $100 risk under a 30-day term that ends 2026-10-28 (config.py:178-181, 829-831; earnings_drift.py:108-109). So far that has produced 4 cards, 1 fill and 0 closed trades.

The forward record depends on manual taps, even though probe_outcomes already labels every eligible event counterfactually.

Caveats:
- The leg is never reported net of SPY. The control's +0.094R is mostly market drift and survivorship.
- Recent years are weak: 2025 +0.05R, 2026 +0.01R.
- Large-cap PEAD has largely disappeared since 2006 (Martineau).

### Evidence

- docs/apriori-pead-2026-09-26.md 'Criteria' and 'Diagnostics' (events per session; LONG by year)
- agentic_trader/config.py:178, 181, 829-831; agentic_trader/screeners/earnings_drift.py:108-109; agent/copilot.py:1273-1279
- docs/alpha-roadmap.md:574-575 (expires 2026-10-28)
- docs/apriori-alphas.md 'Part 2' Measurement (counterfactual label of every eligible event)
- Live map Q13 (4 cards, 1 executed, 0 closed)

### Recommendation

1. Before 10-28, renew with --renew under a predeclared rule that reads the counterfactual label of every eligible event. Example: continue unless the mean r_cost over at least 40 mature events has a ci90 upper bound below 0, or the −4R kill fires.

2. Compute the leg's excess over SPY and over a beta-matched benchmark from the study's labels, and print it on drift cards.

3. Draft a probe v2 spec for operator approval:
   - max_drift_cards_per_session = 2;
   - per-card risk at native parity ($250) once natives are withheld;
   - the same frozen D+2 10:35 entry.
   max_open_drift_positions (4) and the 8-slot book still bound total exposure.

4. Optionally, declare a second look at the reaction-only variant (C07).

### North-star impact

Moves scarce card slots from a source measured at −0.11R against SPY to the only source with measured edge over its control, and keeps the only working research-to-Telegram route alive.

### References

- Bernard & Thomas (1989), Post-Earnings-Announcement Drift, J. Accounting Research 27 — Drift runs about a quarter, consistent with the in-house +0.25R at 60 sessions
- Martineau (2022), Rest in Peace Post-Earnings Announcement Drift, Critical Finance Review 11 — PEAD largely gone in large caps, so a forward kill rule beats open-ended scaling (https://ideas.repec.org/a/now/jnlcfr/104.00000122.html)

### Fact check — partially_confirmed

**Corrected claim**

The PEAD LONG leg passed all four predeclared criteria: +0.149R after 5 bp per side (ci90 +0.070 to +0.232), and +0.055R over the every-reporter control (ci90 +0.021 to +0.089). The control itself earns +0.094R, which the doc attributes to market drift and survivorship (docs/apriori-pead-2026-09-26.md 'Criteria', 'Reading the result').

Supply is narrower than "a median of 2 events per session" suggests. The median of 2 and p90 of 9 count only sessions with at least one leg event (pead_study.py:236-239 groups the leg by session). That is 1,275 of about 2,476 decision dates, so roughly half of all sessions have no event, and off-season weeks are thin.

Live caps:
- At most 1 drift card per session (config.py:178).
- Decided only by the scheduled 10:35 scan (earnings_drift.py:108-109; copilot.py:1260-1279).
- A $100 probe risk cap (config.py:829; evaluator.py:158-164).
- A 30-day term (config.py:831) that ends 2026-10-28 (docs/alpha-roadmap.md:574-575).
- A 4-open-position cap (config.py:181).
- No config.yaml override for any of these.

The live map reports 4 cards, 1 executed (BLK) and 0 closed so far. That count comes from production queries, not the repo.

The probe does have a kill rule: −4R cumulative from first enrolment, sticky per version (probe.py:40; probe_state.py:62-63). The term is explicitly a "review checkpoint, not a risk bound" (config.py:830).

What is missing is a predeclared continue/renew criterion at expiry. Renewal only requires the probe to be unkilled and in the registry (storage/alpha.py:466-471). The kill rule and forward record read only closed, executed probe SignalRecords (probe_state.py:28-42), so they depend on operator taps. The counterfactual r_cost over every mature eligible event exists only as a report (probe_outcomes.py:89-132); liveness does not use it.

Neither the study labels (pead_study.py:165-178) nor probe_outcomes compute excess over SPY. Recent years are weak: 2025 +0.05R and 2026 +0.01R. Martineau (2022) reports large-stock PEAD non-existent since 2006.

Raising max_drift_cards_per_session would add cards rather than move native slots. The drift budget sits outside the native budget (copilot.py:1270-1279); only the 8-position book and operator attention are shared. Also, the "−0.11R vs SPY" native figure is an excess measure, while PEAD's +0.149R is raw, so the impact comparison mixes measures.

**Checked**

- docs/apriori-pead-2026-09-26.md:3-12, 72-77 (Criteria), 79-107 (Diagnostics, LONG by year), 109-127 (Reading the result)
- agentic_trader/config.py:172-184 (AprioriConfig: max_drift_cards_per_session=1 at :178, max_open_drift_positions=4 at :181)
- agentic_trader/config.py:820-831 (probe_risk_dollars=100 at :829, probe_term_days=30 at :831; comment at :830 'Review checkpoint, not a risk bound: the -4R kill rule limits losses')
- config/config.yaml (no apriori: or probe overrides; max_concurrent_positions 8 at :6, default_equity_risk_dollars 250 at :8)
- config/research/apriori/pead-v2.json:39-44 (decision_time_et 10:35, 2xATR, 3R, 20 sessions)
- agentic_trader/screeners/earnings_drift.py:107-109 (applies() only when scheduled_time_et == decision_time_et), :213 (probe=True)
- agentic_trader/agent/evaluator.py:158-164 (probe risk cap applied to probe candidates)
- agentic_trader/agent/copilot.py:1260-1279 (drift budget separate from native budget; drift never runs without a budget)
- agentic_trader/research/alpha/probe.py:33-40 (ProbePolicy.kill_r = -4.0)
- agentic_trader/storage/probe_state.py:28-64 (forward record = closed SignalRecords only; expiry and -4R kill in probe_block_reason)
- agentic_trader/storage/alpha.py:455-500, 584-620 (enrol/renew: only requires unkilled; no evidence threshold; sweep retires expired probes)
- agentic_trader/cli/commands/alpha.py:540-560 (--renew flag)
- agentic_trader/research/apriori/pead_study.py:114-183 (label_events: r, r_cost only, no SPY excess column), :215-240 (events_per_session computed via leg.groupby('session') i.e. only sessions with >=1 leg event)
- agentic_trader/research/apriori/probe_outcomes.py:89-132 (counterfactual mean r_cost over every mature event; reporting only, not used by liveness)
- grep of agentic_trader/research/apriori for SPY/excess/benchmark: SPY used only for the reaction z, never for outcome excess
- docs/alpha-roadmap.md:574-575 ('probe expires 2026-10-28')
- docs/apriori-alphas.md:90-209 (Part 2: budget, open-position cap, enrolment/renewal, Measurement, Known limits)
- docs/superpowers/specs/2026-09-26-apriori-pead-probe-design.md:23 (30-day renewable term, -4R sticky kill), :355 (no automatic renewal)
- scratchpad holistic-review/maps/live.md:36, 57, 79 (Q13: enrolled 2026-09-28, expires 2026-10-28, 4 cards, 1 executed BLK, 0 closed)
- https://ideas.repec.org/a/now/jnlcfr/104.00000122.html (Martineau 2022, CFR 11(3-4):613-646; abstract: large-stock PEAD non-existent since 2006)
- Web search for Bernard & Thomas (1989), JAR 27 (supplement), approx. 60-trading-day drift

**Bad references**

_none_

**Notes**

The core finding holds:
- The study numbers match the doc (+0.149R, +0.055R [0.021, 0.089], control +0.094R, p90 9, 2025 +0.05R, 2026 +0.01R, 60-session +0.252R).
- All code citations are accurate: config.py:178/181/829-831, earnings_drift.py:108-109, copilot.py:1273-1279, alpha-roadmap.md:574-575.
- The SPY-excess gap is real: there is no excess column in label_events or probe_outcomes.
- The forward record really does depend on executed and closed trades.

Three details need correcting:

1. The title says there is "no continue or kill rule". A −4R cumulative sticky kill exists (research/alpha/probe.py:40, enforced at storage/probe_state.py:62-63), and the finding's own recommendation mentions it. Only a predeclared continue/renew criterion is missing; renewal requires nothing beyond being unkilled.

2. "A median of 2 qualifying events per session" is the median over sessions that have at least one leg event (pead_study.py:236-239). That is about 51% of decision dates (1,275 of 2,476), so the 1-card cap binds less often than implied.

3. The north-star impact says the change "moves scarce card slots" from natives. In fact drift cards have a budget separate from the native one, so a higher drift cap adds cards. Comparing PEAD's raw +0.149R with the natives' −0.11R excess over SPY mixes a raw measure with an excess one.

The live counts (4 cards, 1 fill, 0 closed; enrolled 2026-09-28) come from the workflow's production live map (maps/live.md:57, Q13) and cannot be verified from the repo.

Both references check out:
- Martineau (2022), Critical Finance Review 11(3-4): 613-646. The URL resolves, and the abstract states large-stock PEAD has been non-existent since 2006.
- Bernard & Thomas (1989), Journal of Accounting Research 27 supplement. This is the standard citation for drift lasting about 60 trading days.

### Value review — P0, now (days)

**Reasoning**

I'd fund this now, but only part of it. Items 1 and 2 (renew, and measure excess over SPY) are P0. Item 3 (scale up) waits for item 2's answer. Item 4 should be dropped.

The finding checks out against the code:
- Drift cards have their own budget, outside the native one: 1 per session, decided only at 10:35 (agentic_trader/config.py:176-181; agent/copilot.py:1273-1279; screeners/earnings_drift.py:108-109). Each trade risks $100 under a 30-day term (config.py:829-831).
- The deadline is real. `snapshot()` installs only probes that may take new risk (storage/alpha.py:682). Once expired, a probe is blocked by `probe_block_reason` (storage/probe_state.py:57-58) and drops out of `catalog_probes`. After 10-28 the 10:35 scan would therefore stop journaling `pead_decision` events, and the counterfactual record goes quiet along with the cards (copilot.py:1991, 1997-2010).
- Renewing is one operator command. It works only while the version is still on the probe list and the probe is not killed. It can extend the term by up to 180 days (storage/alpha.py:458-471; research/alpha/probe.py:20).

Why renewal and SPY-excess come first:
- PEAD is the only route from research to Telegram that has ever passed its predeclared criteria: P1 +0.149R, P2 +0.055R over the control (docs/apriori-pead-2026-09-26.md 'Criteria'). Letting it lapse through inattention costs almost nothing to prevent.
- But the premise "the only source with measured edge" is not established in the north-star sense of an honest base rate. The native bracket earns +0.039R raw but −0.110R against SPY over its own holding windows. So SPY drift is worth about +0.15R on a much shorter hold (docs/bracket-counterfactual-2026-10-18.md 'Descriptives', cited in maps/live.md:41).
- PEAD holds 20 sessions, and its every-reporter control already earns +0.094R (docs/apriori-pead-2026-09-26.md 'Reading the result'). The raw +0.149R could be mostly or entirely beta.
- P2 shows cross-sectional skill among reporters. A long-only card, however, delivers the beta too. Whether a drift card beats holding SPY is unmeasured. That is a few days' offline work on the existing study labels, and the answer decides item 3.

Item 3 should be gated:
- Don't spend more risk on a leg whose recent years are flat (2025 +0.05R, 2026 +0.01R; 'Diagnostics' LONG by year) until its excess over SPY is known to be positive.
- It is also cheaper than the finding implies. `max_drift_cards_per_session` is plain config, and `probe_risk_dollars` is pinned in the enrolment only as evidence ("limits", storage/alpha.py:484-485). Neither needs a new frozen entry version.
- But `probe_risk_dollars` applies to every probe in `alpha_pipeline`. A $250 per-card risk would need a per-probe override.
- The finding's impact line says PEAD would take slots from natives. It doesn't: drift cards are additive, outside the native budget.

The proposed continue/kill rule is lenient to the point of uselessness:
- With per-event R spread around 1R, 40 events give a standard error of about 0.16R. For the ci90 upper bound to fall below 0, the mean would have to be about −0.26R or worse, so the rule almost never fires.
- One earnings season (roughly 2 leg events per session) can never confirm a +0.055R incremental edge.
- Frame the renewal rule honestly as an implementation-fidelity check:
  - the live event yield and liquidity-reject rate (live, 23 of 30 rows on 10-21 failed the volume screen; maps/live.md:57);
  - fill price against the study's decision price;
  - Nasdaq feed failures;
  - the forward counterfactual mean staying within the study's distribution.

Item 4 is a third look at in-sample diagnostics. Reaction-only (+0.120R) is weaker than the joint rule. Drop it.

**Risks**

1. **Scaling a decaying anomaly.** Recent years are flat (2025 +0.05R, 2026 +0.01R), and large-cap PEAD has largely disappeared (Martineau 2022). The SPY-excess figure may well come out ≤ 0. That would remove the premise that this is the best card source; a negative answer is useful, not a failure.
2. **Survivorship.** Nasdaq's history lacks delisted names (docs/apriori-pead-2026-09-26.md 'Caveats'). This flatters the absolute leg, and any SPY-excess number computed on those labels, more than it flatters P2.
3. **Correlated exposure.** Raising per-card risk to $250 with up to 4 open drift positions puts about $1,000 into names that all report in the same season and share market beta. The 8-slot book does not cap that correlation.
4. **A kill rule that only looks like one.** The proposed ci90 rule almost never fires, so it would license open-ended running in place of a real decision.
5. **Forking paths.** Building v2 or a reaction-only variant out of in-sample diagnostics adds multiplicity to the one lane that passed.
6. **Taps still matter for realized evidence.** Only 1 of 4 cards was executed, so the realized-vs-counterfactual comparison stays years away (it needs 10 closed trades; docs/apriori-alphas.md 'Part 2' Measurement). Auto-entering on paper would be a contract change and needs explicit operator approval.
7. **Gap in the record if renewal is late.** An expired probe leaves the snapshot, so `pead_decision` events stop being journaled until the renewal.

**Prerequisites**

1. **Operator action before 2026-10-28.** Run `alpha apriori-probe ... --renew --days 90 --generation N` (the term can be up to 180 days). Write the fidelity-oriented continue rule into docs/apriori-alphas.md before reading any further forward data.
2. **SPY-excess analysis.** It needs the study's `label_events` with per-event entry and exit timestamps, from ~/agentic-trader-research/apriori-pead-v2-20260926, plus cached SPY daily bars and sector-ETF bars for a beta-matched benchmark. A precedent exists in research/setups/outcomes.py:382-383 (`excess_r`), so the method can be shared. Show the result on drift cards and in `cards outcomes` alongside the counterfactual (research/apriori/probe_outcomes.py:89-131).
3. **Before any scaling.**
   - A predeclared threshold, for example: scale only if the leg's mean excess over SPY has ci90 > 0 on the full sample and is ≥ 0 over 2024-2026.
   - A per-probe risk override, so raising PEAD's risk does not raise every probe's `probe_risk_dollars`.
   - Operator approval.

**Better alternative**

A cheaper, higher-leverage way to settle the edge question: do the renewal (one command), then run one offline job and skip the forward-tap evidence altogether. That job has two parts:

1. **Honest base rate on history.** Recompute all 4,726 historical LONG-leg events and the control as excess R over SPY, and over a beta-matched sector ETF, on the same holding windows. That answers whether drift cards beat holding the index.
2. **A clean forward read.** Run the frozen pead-v2 labeller, unchanged and identified by its SHA, over the window after the study (2026-08 to now, about 100+ events).

Both come from cached Nasdaq pages and bars. Neither needs taps, the daemon or a new version, and they give a better read on the edge in about a week than the live probe will by 2027. Decide on scaling from that result. Skip the reaction-only second look.

---

<a id="c03"></a>

## C03 — The measured record behind card lines and card_policy is a censored, repeat-counted, raw-R, wrong-feed mean that swung from −0.38R to +0.54R in 13 days; rebuild it as a shrunk estimator before any policy acts on it

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | research_methodology | defect | high | M | partially_confirmed |

_lenses: quant, portfolio, product_

### Claim (as consolidated)

How the record is computed: _key_stats averages raw r_cost over mature rows only, per (strategy, direction). It has no excess field, no de-duplication, no risk-unit floor and no timeframe in the key (card_stats.py:43-62, 88-117). card_policy withholds on that mean (card_policy.py:37-55).

Why it swings: on a 2R bracket, stops resolve faster than targets, so early windows over-weight stops. TREND_PULLBACK long went from −0.376R (n 34, 2026-10-08) to +0.535R (n 67, 2026-10-21) as 28 targets and 6 stops matured.

Other defects:
- The 87 journaled rows are only 41 distinct setups.
- Labels use the configured IEX feed, while the replay prior uses SIP 'all'.
- Implied EV mixes gross target/stop R with net timeout R (card_evidence.py:69-73).

Precision: per-setup sd is ≈1.4R, so SE is ≈0.22R at 41 setups. Separating +0.04R from −0.11R needs about 650 distinct setups, roughly 1.5 years.

Consequence: with a threshold of 0, the policy would have flipped from withhold to send on censoring alone. Meanwhile no calibrated expected return reaches size or selection, and the replay prior (8,768 setups: +0.039R raw, −0.110R vs SPY) is ignored.

### Evidence

- agentic_trader/research/setups/card_stats.py:43-62, 88-117 (mature-only, raw r_cost mean)
- agentic_trader/execution/card_policy.py:37-55; config/config.yaml:454-455 (mode off)
- agentic_trader/execution/card_evidence.py:69-73 (implied EV mixes gross and net)
- agentic_trader/research/setups/card_stats_worker.py:116; agentic_trader/config.py:1043 (labels on IEX)
- Production card_stats_snapshot events: 10-08 n34 −0.376 → 10-21 n67 +0.535; 87 rows = 41 setups (live map Q1, Q6)
- docs/card-evidence.md 'Limits' (censoring, repeated setups); docs/bracket-counterfactual-2026-10-18.md 'Descriptives'

### Recommendation

Build card_stats v2 as an estimator:
(a) Outcome probabilities from competing-risks Aalen–Johansen cumulative incidence of target, stop and timeout, with immature rows censored rather than dropped. Alternatively, use only complete cohorts. EV = Σ CIF_k × mean R_k, computed on net r_cost.
(b) One row per (contract, strategy, timeframe, direction, session).
(c) Outcome: β-adjusted, cost-symmetric excess with a risk-unit floor.
(d) Shrinkage toward a CardPrior built from the frozen replay frames, per (strategy, timeframe, direction, stop-width tercile): a normal prior on alpha_R with bootstrap SE, and a beta-binomial on the target rate.
(e) Labels on delayed SIP.
(f) Anytime-valid confidence sequences if it is read daily.

On the card, print a line such as 'expected excess −0.06R ± 0.02 (prior 8.8k replayed; live n 41)'.

card_policy reads only the posterior P(excess > 0), in both preview and enforce.

Feed the posterior to the shadow allocator. Later, consider fractional Kelly with parameter-uncertainty shrinkage, which gives ≈0 today.

### North-star impact

The base rate on each card becomes stable and agrees with 8,768 replayed setups, which makes 'states its base rate honestly' true. card_policy, the only outcome-derived gate, then becomes safe to enforce.

### References

- Aalen & Johansen (1978), An Empirical Transition Matrix for Non-homogeneous Markov Chains Based on Censored Observations, Scand. J. Statistics 5 — Competing-risks cumulative incidence under censoring (https://lifelines.readthedocs.io)
- Efron & Morris (1975), Data Analysis Using Stein's Estimator and its Generalizations, JASA 70 — Shrinking small live samples toward a well-measured replay prior
- Howard, Ramdas, McAuliffe & Sekhon (2021), Annals of Statistics 49(2) — Valid inference when a policy reads the estimate daily
- Baker & McHale (2013), Optimal betting under parameter uncertainty, Decision Analysis 10(3) — Conviction sizing that shrinks to zero when the edge SE is large

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding is right. CardKeyStats/_key_stats average raw r_cost over mature rows only, grouped by (strategy, direction) (card_stats.py:43-62, 88-117, 139-142). card_stats does not use the labeller's excess_r (computed at outcomes.py:378-383), does not de-duplicate, has no risk-unit floor (labels.py:144) and has no timeframe in the key, even though squeeze emits 4h and 1h (strategies.py:343-346). card_policy.decide compares that mean against min_measured_ev (card_policy.py:37-55); the policy is off and its threshold is null (config.yaml:454-455). implied_ev mixes gross target/stop R with net timeout R (card_evidence.py:69-73). Labels use the configured feed (IEX by default via config.py:1043, fetched through sources.py:31) with raw adjustment (outcomes.py:570), while the replay used SIP all-adjusted caches.

The details need these corrections:
(1) For the TREND_PULLBACK long key, 10-08 → 10-21 added about +28 targets and +5 stops (34×23.5% ≈ 8T/26S → 67×53.7% ≈ 36T/31S). The "+28 targets and +6 stops" figure is the aggregate's 35 → 69 maturation.
(2) "Flipped on censoring alone" overstates it. Censoring explains the early stop-heavy reading, but the 10-21 value (53.7% target vs 34.9% in 8,768 replayed setups) also reflects a favourable single-regime window. Both snapshots are biased, in opposite directions.
(3) The precision arithmetic holds: sd ≈ 1.4R on this bracket, SE ≈ 0.22R at n 41, and about 540–680 setups for 80% power on a 0.15R difference. But the binding limit is the 90-day rolling window (stats_window_days: 90). At about 2 distinct setups per session it caps n near 150, so the live record alone can never reach that precision, however long it runs. "Roughly 1.5 years" is a loose time estimate.
(4) These defects are not undiscovered. docs/card-evidence.md "Evidence block" and "Limits" already document the mixed gross/net implied EV, IEX labels, censoring bias and repeat counting, and they defer de-duplication to an operator decision before enforce. They are unfixed known limits, not hidden bugs.
(5) card_stats_worker.py:116 only records the feed name in provenance. The fetch feed is set in sources.py:31.

**Checked**

- agentic_trader/research/setups/card_stats.py:43-62 (CardKeyStats has no excess/de-dup/timeframe fields), :88-117 (_key_stats drops immature and fetch_failed rows and averages raw r_cost), :139-142 (grouped by strategy and direction only)
- agentic_trader/execution/card_policy.py:37-55 (would_withhold = mean_r_cost < min_measured_ev, n_mature >= floor, n_fetch_failed == 0; withhold only under enforce)
- config/config.yaml:453-459 (card_policy mode "off", min_measured_ev null, min_mature_cards 20, stats_window_days 90)
- agentic_trader/execution/card_evidence.py:69-73 (implied_ev = target_rate*rr - stop_rate + timeout_rate*mean_timeout_r, which mixes gross and net R)
- agentic_trader/research/setups/card_stats_worker.py:97-121 (label_journaled called with the default SPY market proxy, so the frame has excess_r but card_stats ignores it; line 116 only records the feed name)
- agentic_trader/research/setups/sources.py:26-34 (bar source uses config.market_data.alpaca_feed); outcomes.py:570 (adjustment='raw')
- agentic_trader/config.py:689 (field default sip) and :1043-1050 (load_config defaults to iex when no env override; config.yaml sets no alpaca_feed)
- agentic_trader/research/setups/outcomes.py:378-383 (excess_r computed per row), :436-527 (with_recurrence counts repeats but nothing de-duplicates), :529-650 (label_journaled)
- agentic_trader/research/setups/labels.py:43-63, :144 (SetupLevels only checks ordering; r_cost = r - 2*cost*entry/risk_unit, no risk-unit floor)
- agentic_trader/screeners/strategies.py:343-346 (squeeze emits 4h and 1h in one universe scan, so pooling timeframes is real); agent/copilot.py:1878 (timeframe journaled but unused by card_stats)
- agentic_trader/research/setups/ranker.py:1-18 (shadow score never changes ranking); copilot.py:635-637 (setup_quality ordering); grep shows no runtime use of the replay base rates in card_stats or card_evidence
- docs/card-evidence.md 'Evidence block' (implied EV documented as mixed gross/net), 'Limits' (IEX in production, censoring bias, repeated setups with de-dup left as an operator decision before enforce)
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (SIP all-adjusted caches, winsorised ±10R) and 'Descriptives' (A0_live 34.9%/60.8%/4.3%, +0.039 r_cost, -0.110 excess, n 8,768)
- scratchpad holistic-review/maps/live.md section 4 (Q1 snapshot table: 10-08 n34 23.5%/76.5% -0.376; 10-21 n67 53.7%/46.3% +0.535) and line 33 (87 rows, 41 distinct setups, Q6)
- Web: all four references verified (Aalen & Johansen 1978 SJS 5:141-150; Efron & Morris 1975 JASA 70(350):311-319; Howard et al. 2021 AoS 49(2):1055-1080; Baker & McHale 2013 Decision Analysis 10(3):189-199); lifelines has AalenJohansenFitter

**Bad references**

_none_

**Notes**

I could not verify the production numbers (card_stats_snapshot values, 87 rows = 41 setups) directly, because the ground rules forbid DB/CLI access. They come from the review's own live map (maps/live.md Q1/Q6), and its table is internally consistent with the cited rates. All code citations check out; only line 116 is slightly off target, as noted in the corrected claim. The replay prior figures match docs/bracket-counterfactual-2026-10-18.md 'Descriptives' exactly. Note that the replay prior is winsorised at ±10R and the live record is not, which is another comparability gap. The live labeller's lack of an R cap is also noted in project memory.

Nothing in the runtime reads the replay base rates (no CardPrior or prior in card_evidence/card_stats), and no calibrated expected return reaches sizing or selection. The ranker is shadow-only (ranker.py:1-18), and selection uses setup_quality (copilot.py:635-637).

The recommendations are reasonable. Aalen–Johansen with a fixed 20-session horizon is apt, since every label resolves by 20 sessions and complete-cohort reads are a simpler alternative. The finding did not mention that the 90-day window cap makes shrinkage toward the replay prior close to mandatory rather than optional. All four literature references exist with correct venue, volume and year. The lifelines URL is a library homepage, not the paper, but lifelines does provide AalenJohansenFitter.

### Value review — P1, now (days)

**Reasoning**

The diagnosis holds against the code. I'd fund a cut-down version now and drop most of the proposed estimator machinery.

What the code does now:
- The record averages raw `r_cost` over mature rows only, per (strategy, direction). It has no excess field, no de-duplication and no timeframe in the key (agentic_trader/research/setups/card_stats.py:43-62, 88-117).
- docs/card-evidence.md "Limits" admits both the censoring and the repeated-setup problems.
- The card prints "mean {mean_r_cost:+.2f}R after cost" next to an implied EV (agentic_trader/execution/card_evidence.py:166-175). That EV is `target_rate·rr − stop_rate + timeout_rate·mean_timeout_r` (card_evidence.py:69-73): target and stop are counted gross, and only timeouts are net.
- The mixing is not trivial. In replay, mean R is +0.116 and mean `r_cost` is +0.039 (docs/bracket-counterfactual-2026-10-18.md "Descriptives", A0_live). Cost is therefore about 0.077R per setup, roughly twice the whole net edge. The implied-EV line overstates by about that much.
- Labels use the desk feed, `alpaca_feed` (card_stats_worker.py:116), which is IEX in production.

Why the record is misleading:
- The live mean went from −0.376R (n 34) to +0.535R (n 67) in 13 days. That is censoring plus repeat-counting (87 rows, 41 setups; maps/live.md Q1, Q6), not information.
- So today every native card shows the operator a +0.54R "measured record". Meanwhile 8,768 replayed setups say +0.039R net and −0.110R vs SPY.
- That is a live honesty defect against the agreed definition of a reasonable suggestion: an honest base rate and bracket EV ≥ 0.

Why I would not build the full estimator:
- The replay prior's sampling error on `r_cost` is about 0.045R. The A2 arm's 90% CI is [−0.045, +0.108] under a session-block bootstrap (same doc, "Rules").
- The live SE at 41 distinct setups is about 0.22R (sd ≈ 1.4R).
- So any sensible shrinkage puts about 4% weight on live data. The posterior is the prior for at least the next year, as the finding's own 650-setup estimate implies.
- Aalen–Johansen, beta-binomial cells by stop-width tercile, confidence sequences and parameter-uncertainty Kelly are therefore an M-sized build whose output is "print the replay number". Kelly would give about 0, as the finding says.
- The honest number also leaves no ambiguity for card_policy. P(excess > 0) is about 0 for TREND_PULLBACK LONG. Enforcing would withhold nearly every native card.
- So the real blocker is the unresolved trade-off between card count and card quality (maps/history.md §6.1), and a native card source that trails SPY by 0.10–0.15R in every bracket arm. An estimator resolves neither. It only makes that conflict impossible to ignore, and the bracket counterfactual already does that for free.

What to fund, a few days of work:
- (a) Make the card's primary base rate the frozen replay figure per (strategy, direction, timeframe): +0.04R net, −0.11R vs SPY, n 8,768, with the protocol hash.
- (b) Demote the live record to a descriptive line: de-duplicated by (contract, strategy, timeframe, direction, ET session), complete cohorts only (decided more than 20 sessions plus a buffer ago), with the n of distinct setups shown.
- (c) Fix implied EV to net R.
- (d) Switch the labeller to historical SIP, which research already uses, under a new labeller protocol version.

Then hand the operator the explicit card_policy decision on the prior. The expensive estimator work waits until live distinct setups exceed about 200, or until a card source with positive replayed excess exists, so that live data can move the posterior.

**Risks**

- **Fewer cards.** An honest base rate shows −0.11R vs SPY on essentially every native card. Any outcome-derived gate then withholds the stream, which directly conflicts with "steady stream" unless a new source with positive excess arrives. The operator must make this call explicitly.
- **Prior and live populations differ.** The replay is on the configured universe, SIP data, deterministic brackets and historical regimes. Live uses dynamic names (SPY/VTI/VOO are pure beta, so excess ≈ 0 by construction), IEX 4h bars bucketed in UTC, LLM-edited brackets and a single regime (SPY above its 200-day average, VIX normal). A shrunk estimator with no between-population variance term would hide a real live deviation, and 41 setups cannot estimate that variance.
- **Comparability breaks.** Changing the snapshot schema and labeller mid-stream breaks comparison with journaled `card_policy` blocks and old snapshots. Both need a versioned protocol (setup-outcomes-v2) and a rollback-tolerant reader (CardStatsSnapshot ignores unknown keys, which helps).
- **Too-fine prior cells.** Stop-width-tercile × timeframe × direction cells could leave SQUEEZE_BREAKOUT or 1h cells thin, which reintroduces noise.
- **Dependency and complexity.** Adding `lifelines` to the daemon for a mostly decorative estimator adds dependency weight and review load.
- **Repeated looks.** If the policy reads a live estimate daily without anytime-valid inference, it can flip on noise. The scoped version avoids this because policy reads the frozen prior.

**Prerequisites**

- **Yardstick.** The operator decides what the card's headline number is: raw net R, excess vs SPY, or both. My recommendation is both, with excess as the selection-skill test. The operator also decides what happens when the honest base rate is negative: keep sending labelled cards, or withhold and accept a thinner stream.
- **Frozen prior artifact.** The replayed frames are hash-pinned at ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz and the setup-outcomes frames. Each CardPrior cell needs its protocol hash, the session-block bootstrap SE and the cell n.
- **Row-level labels.** card_stats stores only aggregates (card_stats.py:120 onward). A de-duplicated, complete-cohort live line needs a stable setup key and labels kept per row, either in the snapshot or as a journal artifact.
- **New labeller version.** A new labeller protocol version for the SIP feed and net implied EV, so old snapshots stay readable and are reported separately.
- **Accepted limits.** No change to risk/, admission or the broker. `card_policy` keeps its fail-open rules for partial or stale evidence.

**Better alternative**

Skip the competing-risks, hierarchical-shrinkage, confidence-sequence and Kelly build for now. Ship a small change and a decision instead:

1. Print the frozen replay base rate per (strategy, direction, timeframe) as the card's headline, for example "replayed 8,768 setups: +0.04R net, −0.11R vs SPY". Fix implied EV to use net R.
2. Show the live record only as a de-duplicated, complete-cohort descriptive count, labelled on SIP.
3. Put the card_policy decision to the operator using the prior directly. Run `preview` against the replay prior's excess to quantify how many cards would be withheld.

Spend the saved effort on the binding constraint, a card source with positive replayed excess, because no estimator can change a −0.11R prior. Revisit Aalen–Johansen and shrinkage only when live distinct setups pass about 200, or when a new source makes live evidence able to move the posterior.

---

<a id="c04"></a>

## C04 — Cards are judged by raw R or a beta-one SPY control: the native stream is ~0.9-beta exposure with alpha near zero, and a raw-R card_policy would certify beta as quality

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | product | defect | high | S | partially_confirmed |

_lenses: portfolio, product, literature_

### Claim (as consolidated)

The card stream and how it is judged:
- 37 of the 41 cards since 2026-09-18 are native longs.
- The agreed test (2) of a reasonable suggestion is raw EV after cost (alpha-roadmap.md:507-510).
- card_policy compares the raw mean_r_cost with a threshold. It ships with mode off and the threshold null.
- CardKeyStats has no excess field, although every row carries excess_r.
- market_r scales SPY into R at β=1 (outcomes.py:187-219).

In replay the stream earns +0.039R raw against SPY's +0.211R, an excess of −0.110R. A raw-R threshold of 0 therefore passes natives in any rising tape.

The beta-one control overstates the deficit:
- Realised beta rises with stop width: by quintile 0.39 → 1.21 in 2021–26. The tightest quintile is 21–24% rates, credit and metals ETFs.
- Beta-adjusted excess is −0.062R in 2021–26, and −0.034R / −0.094R in 2017–21 (setup-weighted / session-weighted).
- In equal-notional return terms, beta is 0.93 and alpha −8.0 bp (2021–26) and +1.7 bp (2017–21).

So natives show no positive alpha. But 15–70% of the advertised deficit is beta below 1, amplified by R-weighting, and a beta-one policy would withhold low-beta cards simply for being low-beta.

### Evidence

- Production signals since 2026-09-18: 36 TREND_PULLBACK, 1 SQUEEZE_BREAKOUT, 4 pead_long
- docs/alpha-roadmap.md:507-510; agentic_trader/execution/card_policy.py:43-54; config/config.yaml:454-455
- agentic_trader/research/setups/card_stats.py:43-62 (no excess); research/setups/outcomes.py:187-219, 382-383 (beta-one market_r; excess_r per row)
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' and 'What this says' item 3
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_frames_w.py, pm_quintile_beta.py, pm_beta_adj_R.py (A0_live frames)

### Recommendation

1. The operator restates test (2) as: non-negative expected excess over a beta-matched index position, over the card's holding window, after cost.

2. Per ranked candidate, journal a pre-entry beta: 120 sessions, split-adjusted, Vasicek-shrunk.

3. Compute alpha_r = r_cost − β·market_r, and alpha_bp, in cards outcomes and CardKeyStats. Optionally add FF5 plus momentum attribution.

4. Put card_policy in preview on alpha_r, through the shrunk estimator (C03), not on beta-one excess.

5. Restate the roadmap conclusion as 'native ≈ 0.9-beta exposure, alpha −8 to +2 bp/trade'.

6. When natives are withheld, say so in the scan digest. Offer market exposure only as an explicitly labelled beta card that shows its SPY-equivalent dollars.

7. Re-run the bracket counterfactual's B1 on beta-adjusted alpha, as a declared second look.

### North-star impact

Defines 'high quality' as a falsifiable, market-relative number. The desk then stops presenting index exposure as stock selection, and the gate stops penalising diversifying low-beta cards.

### References

- Jensen (1968), The Performance of Mutual Funds in the Period 1945-1964, JF 23(2) — Performance must be measured net of beta
- Vasicek (1973), A note on using cross-sectional information in Bayesian estimation of security betas, JF 28(5) — Shrinkage for per-name beta on short windows
- Frazzini & Pedersen (2014), Betting Against Beta, JFE 111(1) — Low-beta assets lag a beta-one benchmark in rising markets without lagging on a risk-adjusted basis
- Kenneth R. French Data Library — Daily FF5 and momentum factors for card attribution beyond SPY (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

### Fact check — partially_confirmed

**Corrected claim**

The core is right. Test (2) and card_policy judge native cards on raw mean_r_cost. card_policy ships with mode off and threshold null. CardKeyStats has no excess field. The only market control anywhere (cards outcomes market_exposure, bracket counterfactual B1) is a beta-one SPY move scaled into the card's R. Nothing in the live path computes a per-name beta.

Corrections to the details:
(a) Since 2026-09-18 there are 37 native cards, but 36 are longs. One TREND_PULLBACK card was a SHORT (TLT, 2026-09-18). The mix is 35 TP long, 1 TP short, 1 squeeze long and 4 pead_long.
(b) The '~0.9 beta' holds only in 2021-26 (return-space beta 0.93, alpha -8.0 bp). In 2017-21 the beta is 1.08 setup-weighted (0.82 session-weighted), with alpha +1.7 bp. Beta-one equal-notional excess there is +5.2 bp, so that window's R-space deficit of -0.11R comes from R-weighting low-beta, tight-stop setups, not from an overall beta below 1.
(c) The 0.39 -> 1.21 'realised beta' by stop quintile is the slope of a bracket-truncated payoff over each setup's own window. It mixes rates, credit and metals ETFs (24% of Q1) with truncation (equities-only Q1 is 0.63). The recommended 120-session Vasicek name beta would not reproduce it. Beta-adjusting with a name beta would over-hedge tight-stop cards unless the bracket's payoff beta is modelled.
(d) 'A raw-R threshold of 0 passes natives in any rising tape' is too strong. It holds for the long-run replay means (+0.039R / +0.178R). But the live 90-day card_stats for TP long read -0.376R on 10-08 and -0.102R on 10-13 in a rising tape, through informative censoring, before reaching +0.535R on 10-21. The raw gate is noisy as well as beta-contaminated.
(e) excess_r is None on immature, fetch-failed and no-proxy rows, so not every row carries it.

The beta-adjusted excess figures check out: -0.062R (2021-26) and -0.034R / -0.094R (2017-21). The '15-70% of the deficit' range is roughly right: 43% in 2021-26, 69% setup-weighted and about 15% session-weighted in 2017-21.

**Checked**

- docs/alpha-roadmap.md:507-510: test (2) is 'non-negative expected value at that base rate after cost', which is raw R with no market control. Confirmed.
- agentic_trader/execution/card_policy.py:37-54: measured_ev = evidence.mean_r_cost (line 44), compared with policy.min_measured_ev. Confirmed raw R.
- agentic_trader/execution/card_evidence.py:59,114,123: CardEvidence carries the CardKeyStats mean_r_cost. It has no excess field.
- config/config.yaml:454-455: card_policy mode "off", min_measured_ev null. Confirmed.
- agentic_trader/research/setups/card_stats.py:43-62 (CardKeyStats) and :89-117 (_key_stats): no excess or market field. Confirmed.
- agentic_trader/research/setups/outcomes.py:187-219: market_r = sign*(SPY close/open-1)*entry/|entry-stop|. Its docstring at :201 says 'a beta-one exposure control'. Lines :382-383 hold market_r and excess_r per labelled row, which is None for immature, fetch-failed and no-proxy rows. Lines :769-776 and :957-963 are the cards outcomes market_exposure, also beta-one. Confirmed.
- grep for 'beta' in agentic_trader/risk, agentic_trader/execution and agentic_trader/cli/commands: no hits. No pre-entry per-name beta is computed or journaled anywhere in the live path, although docs/alpha-roadmap.md:523 planned that book sizing would bound beta.
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives': A0_live +0.039 r_cost, SPY +0.211, excess -0.110 (2021-26); robustness excess -0.103. 'What this says' item 3 matches.
- Re-ran the reviewer's read-only scripts on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/{primary,robustness}.csv.gz. Primary: beta 0.930, alpha -8.0bp, beta-one -9.8bp; quintile betas 0.39/0.65/0.77/0.92/1.21; Q1 non-equity share 24%; beta-adjusted excess R -0.062 vs beta-one -0.109. Robustness: beta 1.084 setup-weighted / 0.818 session-weighted, alpha +1.7bp, beta-one notional excess +5.2bp; Q1 non-equity share 21%; beta-adjusted R -0.034 setup / -0.094 session vs beta-one -0.111; equities-only primary Q1 beta 0.63.
- Production PostgreSQL, read-only query on signals (alpaca:paper, not quarantined, date >= 2026-09-18): TREND_PULLBACK LONG 35, TREND_PULLBACK SHORT 1 (TLT 2026-09-18), SQUEEZE_BREAKOUT LONG 1, pead_long 4. Total 41.
- Reviewer live map (scratchpad maps/live.md section 4): TP-long card_stats mean_r_cost was -0.376 (10-08) and -0.102 (10-13), then +0.535 (10-21), all in a rising tape.
- References: Jensen 1968 JF 23(2) 389-416, Vasicek 1973 JF 28(5) 1233-39, Frazzini & Pedersen 2014 JFE 111(1) and the French Data Library URL (daily FF5 2x3 and daily Mom available) all exist and support their stated use.

**Bad references**

_none_

**Notes**

All file:line citations exist and support their use. The numbers reproduce exactly from the reviewer's scripts on the frozen bracket-counterfactual frames.

The finding is not refuted. The code never measures cards net of a non-unit beta, and the policy gate is raw R.

Two nuances for whoever acts on the recommendations:
(1) The low realised beta in the tight-stop quintile is partly mechanical bracket truncation, not the names' beta. A pre-entry name beta (rec. 2-3) should be checked against the payoff beta before it replaces the beta-one control.
(2) Rec. 7 (re-run B1 beta-adjusted) mostly matters for the longer-hold arms (A4's SPY R is +0.283 vs +0.211 for A0). The paired contrast otherwise largely cancels beta.

The DB check used a read-only session (default_transaction_read_only=on). No secrets were read.

### Value review — P1, now (days)

**Reasoning**

The core defect is real and cheap to fix, but it changes the scoreboard, not the signal. It earns P1, not P0.

What the code does now (verified):
- `card_policy.decide` compares raw `evidence.mean_r_cost` with `min_measured_ev` (agentic_trader/execution/card_policy.py:43-54).
- Config ships `mode: "off"` and `min_measured_ev: null` (config/config.yaml:454-455), so nothing gates on raw R today. The "would certify beta as quality" harm is latent and only fires once the operator picks a threshold.
- `CardKeyStats` has no excess or alpha field (agentic_trader/research/setups/card_stats.py:43-62), although every outcome row carries `excess_r` (outcomes.py:382-383).
- `market_r` is explicitly "a beta-one exposure control" that converts SPY's return into the card's R units through `entry/risk_unit` (outcomes.py:187-219).

That last point is a genuine measurement bug, not just a framing issue. The tight-stop quintile (21-24% rates, credit and metals ETFs) gets a SPY benchmark scaled by a huge 1/stop factor, although those names barely co-move with SPY. Beta-one excess is therefore noisy and biased against exactly the diversifying low-beta cards the book should want.

Why it matters for the north star:
- Test (2) of a reasonable suggestion (docs/alpha-roadmap.md "Desk direction after the fixed-set lane") is raw EV after cost, and it is the only falsifiable definition of "high quality" the desk has.
- The bracket counterfactual's item 3 (docs/bracket-counterfactual-2026-10-18.md "What this says") reads the −0.10 to −0.15R excess as all entry failure. The finding's own replay numbers (beta 0.93, alpha −8 bp in 2021-26 and +1.7 bp in 2017-21) leave the substantive verdict unchanged: natives have no stock-selection alpha. The beta correction moves the deficit from "badly negative" to "about zero".
- So the decision value is in (a) not enabling `card_policy` on a raw or beta-one number, and (b) labelling what the operator is actually being sent (about 0.9-beta index exposure with stops). It does not unblock new edge.

That is worth a few days before anyone turns `card_policy` to preview. It is not worth more than the entry-side signal work that actually feeds the north star.

What I would fund vs cut:
- Fund items 1, 3 and 5: restate test (2) as non-negative alpha over a beta-matched index position after cost; add `mean_excess_r` and `alpha_r`/`alpha_bp` to `CardKeyStats` and cards outcomes; correct the roadmap conclusion.
- Fund item 6: label withheld natives in the scan digest and show any market-exposure card's SPY-equivalent dollars. This directly improves card honesty.
- Fund item 4 (`card_policy` preview on `alpha_r`) only together with the shrunk estimator (C03).
- Defer FF5 plus momentum attribution. On about 41 live cards it is noise, and the replay population is survivorship-flattered.
- Drop item 7 (B1 second look). B1 is a paired arm-vs-live contrast on identical entries, so beta mostly cancels: the contrast is r_diff − β·(m_arm − m_live). No arm is close to winning (ratchet −0.013R, CI ±0.007R). A declared second look spends a degree of freedom on a question that cannot change a decision.

**Risks**

1. **The beta numbers are in-sample.** The supporting scripts (pm_beta_adj_R.py, pm_quintile_beta.py) fit per-quintile betas ex post on the same frames they adjust, so the "15-70% of the deficit is beta" range is optimistic. A pre-entry 120-session beta will recover less of the gap.

2. **The reframing can rationalise a non-edge.** "It's just low beta" must not become a reason to keep sending native cards. Alpha is still near zero in both windows, and the restated test must still fail them.

3. **Live samples are too small to discriminate.** With 20-40 mature cards per key, the standard error of mean alpha_r is far larger than the 0.05R beta correction. A `card_policy` threshold on `alpha_r` stays noise-dominated without shrinkage (C03) and an honest sample floor.

4. **Shrinkage behaves badly on non-equity ETFs.** Vasicek shrinkage toward an equity cross-sectional prior pulls the betas of TLT, GLD and similar names toward 1, reintroducing the very bias being fixed. The prior needs to be per asset class, or the non-equity names need their own benchmark.

5. **Journaling beta at decision time touches the live scan path.** That adds a provider dependency and readiness surface for a quantity that can be computed after the fact.

6. **The restatement conflicts with the existing operator definition.** It needs an explicit operator decision, not a silent metric swap, or prior evidence becomes incomparable.

**Prerequisites**

1. The operator explicitly agrees to restate test (2) of a reasonable suggestion, from raw EV after cost to non-negative alpha after cost over the holding window versus a beta-matched index position. This is recorded in docs/alpha-roadmap.md and docs/card-evidence.md.

2. A beta definition is frozen before any numbers are read:
   - window: 120 sessions, ending at the prior close, split-adjusted;
   - benchmark per asset class: SPY for equities, a separate benchmark or none for rates, credit and metals ETFs;
   - shrinkage prior.

3. The C03 shrunk-estimator work must land before `card_policy` moves to preview on `alpha_r`.

4. cards outcomes already fetches SPY bars for `market_r`, so a post-hoc pre-entry beta needs no new data source.

**Better alternative**

Split the work and do the cheap, decision-relevant half now, with no live-path change:

1. Compute a pre-decision beta after the fact in cards outcomes and the setup runner from bars dated strictly before the decision. There is no lookahead, because those bars were knowable at entry. Add `alpha_r` and `mean_excess_r`/`mean_alpha_r` to `CardKeyStats`. Do not journal beta in the live scan.
2. Keep `card_policy` at `off` until its threshold is defined on `alpha_r` through the C03 estimator.
3. Make the doc and definition change.
4. Drop the B1 second look and defer FF5.

The higher-leverage action is the conclusion itself. Natives are about 0.9-beta exposure with no alpha, so stop spending card budget on them as stock picks: cut them from the digest or send them only as explicitly labelled beta cards. Put the freed attention into the entry-side lanes (insider-v1 campaigns, the `shorts_retreat` hypothesis), which are the only path to actually raising card quality.

---

<a id="c06"></a>

## C06 — Spend the single historical confirmation window on one pre-registered, literature-signed composite (plus at most a few declared model specs), not on single formulas

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | next (weeks) | alpha_mining | research_direction | high | L | partially_confirmed |

_lenses: quant, ml, literature_

### Claim (as consolidated)

Each formula is judged alone ('one formula = one trial'), and there is no combination stage:
- The only combination code, combine_forecasts (forecasts.py:219), serves qualified registry versions, of which there are none, and its output is never read (shadow.py:132-149).
- build_cube_inputs refuses more than one field source (runner.py:317-318).
- Earlier learners were small. A 3-feature Ridge reached IC 0.042, but its HAC CIs span 0 and 74% of the gain came from five names. A 13-feature ranker reversed in holdout (−0.293R).

PEAD, the only pass, is itself a combination: surprise-only +0.115R, reaction-only +0.120R, both +0.149R.

Empirical Bayes on the published t's would have predicted shorts_retreat's failure. On v3 (τ≈0.39 t-units, shrink 0.10) its +0.020R (t 3.40) shrinks to about +0.004R, and the selection window then measured +0.006R.

The literature locates predictability in combinations of many weak characteristics. Chen-Velikov find combinations net about 20 bp/month against 4 bp for single anomalies. Arithmetic: 8 signals of IC 0.015 with pairwise ρ 0.2 give a composite IC of about 0.027 (1.8x). The honest expectation in a liquid universe is IC 0.01–0.03, or about 0.02–0.05R.

### Evidence

- docs/alpha-pooled-mining.md 'Purpose' (one formula = one trial); agentic_trader/research/pooled/runner.py:317-318
- agentic_trader/research/alpha/forecasts.py:63, 219; research/alpha/shadow.py:132-149
- agentic_trader/research/setups/study.py:289-313 (purged_walk_forward), :402 (HistGradientBoosting dependency)
- docs/apriori-pead-2026-09-26.md 'Diagnostics' and 'Criteria'
- docs/alpha-panel-forecasts-2026-09-17.md; docs/setup-outcomes-2026-09-23.md 'Selection (H3)'
- docs/alpha-pooled-short-v1-2026-10-20.md (shorts_retreat discovery and selection)

### Recommendation

1. Freeze composite-v1 before reading anything new.
   - Construction: the equal-weight (or OSAP-t-weighted) mean of winsorised, sector-demeaned cs_zscores of 8–15 characteristics. Candidates: 12-1 momentum, 1-month reversal, 52-week high, MAX, volatility/BAB, Amihud, PEAD reaction, short ratio, opportunistic insider buying, turnover.
   - Signs and specifications come only from OSAP/JKP, never from the 64 observed t's.

2. Optional model_set protocol, at most four specs, each one charged trial:
   - M0: the composite.
   - M1: ridge on ranks.
   - M2: monotone-constrained shallow HistGradientBoosting.
   - M3: the rank average of the three.
   Produce out-of-sample scores via purged_walk_forward, wrapped as ScoredFormula so the existing evaluators run unchanged. Estimate PBO/CSCV across the specs and benchmark against Qlib Alpha158 + LightGBM.

3. Run the decisive test on the selection window, since discovery has already been read. Use Holm, matched controls (C05) and the information-test statistic (C23). Spend 2024-01→2026-07 only if selection passes, then confirm prospectively.

4. Report formulas as hierarchical theme posteriors, and use P(edge > cost) for probe eligibility.

5. Use the continuous score as the selector and allocator input.

### North-star impact

This is the most plausible route from mining to a cross-sectional card source and to a ranking score for the 1-2 daily cards: one trial instead of eight, at about 1.8x the IC of a single signal.

### References

- Gu, Kelly & Xiu (2020), Empirical Asset Pricing via Machine Learning, RFS 33(5) — Predictability lives in combinations of many weak characteristics (https://doi.org/10.1093/rfs/hhaa009)
- Jensen, Kelly & Pedersen (2023), Is There a Replication Crisis in Finance?, JF 78(5) — Hierarchical shrinkage within themes; public factor data (https://jkpfactors.com)
- Chen & Zimmermann (2022), Open Source Cross-Sectional Asset Pricing, Critical Finance Review 11(2) — Replicated signal code and published signs used as ex-ante weights (https://www.openassetpricing.com)
- Bailey, Borwein, López de Prado & Zhu (2017), The Probability of Backtest Overfitting, J. Computational Finance 20(4) — CSCV/PBO across the declared specs
- Avramov, Cheng & Metzker (2023), Machine Learning vs. Economic Restrictions, Management Science — ML payoffs shrink sharply without microcaps, so expect small ICs

### Fact check — partially_confirmed

**Corrected claim**

In the pooled long-bracket mining lane, each formula is judged alone ('one formula counts as one trial', docs/alpha-pooled-mining.md 'Purpose'). That lane has no multi-characteristic composite or model stage:
- build_cube_inputs accepts at most one extra field source (events, short or insider) per build (runner.py:317-318).
- The DSL has only time-series operators (operators.py:15-170), so a composite of cross-sectional z-scores needs new code. An out-of-sample model score could still be wrapped as a ScoredFormula panel callable (campaign.py:619-623).

combine_forecasts (forecasts.py:219) only blends calibrated active/shadow registry versions. Its rows are journaled with valid=False and reason combined_shadow_forecast (shadow.py:132-149), and nothing in agentic_trader reads them back. The shadow optimiser takes CombinedForecast only from a hand-supplied JSON file (cli/commands/alpha.py:679-718).

It is NOT true that combine_forecasts is the only combination code, or that combination has never been tested. Equal-weight percentile-rank composites already exist and were run:
- the reversal60 + volatility20 rank_blend and residual_rank_blend in forecast_controls.py:56, factor_controls.py:111-115 and daily_forecasts.py:336-341/446;
- the min-variance dependence_blend at persistent_book.py:228.

Their recorded results are weak:
- forecast controls (2026-09-18): blend IC 0.079 / −0.017 / 0.082;
- factor controls (2026-09-18): adding residual momentum gave no consistent improvement over the blend;
- persistent ETF book (2026-09-17): 'Combination is insufficient', +2.91% at primary cost and −0.36% under stress.

The rank_blend arm is also running prospectively in the daemon's daily panel (docs/alpha-daily-panel.md). Those blends had only 2–3 components, used a 20-session return/IC contract and never ran under the pooled bracket-R label. So a literature-signed composite of 8–15 characteristics under the bracket label is still new, but the finding should cite these prior null blend results as tempering the 1.8x expectation.

Other corrections:
- The empirical Bayes figures (τ≈0.39, shrinkage ≈0.10–0.15, about +0.004R) reproduce only when the prior comes from the 24 earlier cohort-v3 t values (daily, events, intraday). Including the short lane's own eight t values gives τ≈0.72–0.85 and about +0.009R. That is still below the +0.010R keep bar, so the qualitative conclusion holds, but the margin depends on the prior choice.
- The ridge ranker (13 numeric features plus one-hots) was significantly worse than both setup_quality and random in holdout (−0.293R vs −0.118R random), not merely 'reversed'.
- PEAD's +0.115 / +0.120 / +0.149R are absolute leg means that include +0.094R of control drift, and PEAD is an AND conjunction, not a weighted composite.
- The selection window (2022-01-03 → 2023-12-29) has already been read once, for shorts_retreat on cohort v3.

**Checked**

- agentic_trader/research/alpha/forecasts.py:63,188-260 (ForecastCalibration; combine_forecasts at :219, weighted blend)
- agentic_trader/research/alpha/shadow.py:69-150 (combine only for active/shadow versions with a qualification/{version_id} calibration; output recorded valid=False reason combined_shadow_forecast)
- grep combined_shadow_forecast/CombinedForecast across agentic_trader, tests, scripts: no production reader of the recorded rows (only tests/research/test_alpha_shadow.py:141-143); CombinedForecast reaches build_shadow_portfolio only via a hand-supplied JSON in cli/commands/alpha.py:679-718
- agentic_trader/storage/alpha.py:720-738 (record_forecast; invalid rows get no shadow summary)
- agentic_trader/research/pooled/runner.py:302-318 (build_cube_inputs raises if more than one of events/short/insider)
- agentic_trader/research/alpha/operators.py:15-170 (time-series operators only; no cross-sectional zscore, winsorise or sector demean)
- agentic_trader/research/pooled/campaign.py:619-623 (ScoredFormula = formula_id, nodes, panel callable; wrapping model scores is feasible)
- agentic_trader/research/setups/study.py:27,289-313,402; research/setups/features.py:44-59 (8+2+3 = 13 numeric features plus one-hots)
- COUNTER-EVIDENCE: research/alpha/forecast_controls.py:56, factor_controls.py:111-115, daily_forecasts.py:336-341,446 (rank_blend / residual_rank_blend), daily_plan.py:19,153-154; persistent_book.py:228-285 (dependence_blend, min-variance blend of rank scores); persistent_study.py:23,60-117
- docs/alpha-pooled-mining.md 'Purpose' (lines 27-45: 'one formula counts as one trial')
- docs/apriori-pead-2026-09-26.md 'Criteria' and 'Diagnostics' (+0.149R; surprise-only +0.115R; reaction-only +0.120R; control +0.094R)
- docs/apriori-alphas.md (PEAD long is the only eligible leg; exdiv, high52, reversal-lowmax failed)
- docs/alpha-panel-forecasts-2026-09-17.md (IC 0.04230; all annual HAC CIs include zero; top five names 74.23%)
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)' (ridge holdout top-2 -0.293R vs random -0.118R; CI of the paired difference [-0.325, -0.016])
- docs/alpha-pooled-short-v1-2026-10-20.md (shorts_retreat +0.020R, t +3.40; selection +0.006R vs +0.010R keep bar)
- docs/alpha-pooled-cohort3-2026-10-16.md (24 cohort-v3 t values, used to reproduce the EB calculation)
- docs/alpha-persistent-book-2026-09-17.md 'What improved and what failed' ('Combination is insufficient')
- docs/alpha-factor-controls-2026-09-18.md, docs/alpha-forecast-controls-2026-09-18.md, docs/alpha-daily-panel.md (rank-blend results and prospective arms)
- EB reproduction (python -I one-liner) on the published t values
- Composite IC arithmetic: 0.015*sqrt(8)/sqrt(1+7*0.2) = 0.0274
- Web: Gu-Kelly-Xiu DOI resolves to RFS 33(5):2223; jkpfactors.com = JKP Global Factor Data (JF 2023); openassetpricing.com = Chen-Zimmermann CFR 2022; Avramov-Cheng-Metzker MS 69(5) 2023 (payoffs drop sharply excluding microcaps); Chen-Velikov JFQA 2023 (single anomalies ~4 bp/month net, combinations ~20 bp)

**Bad references**

_none_

**Notes**

The core thesis holds: the pooled lane tests single formulas, has no composite or model stage under the bracket label, and the cited code locations are accurate:
- forecasts.py:219
- shadow.py:132-149
- runner.py:317-318
- study.py:289-313 and :402

Every cited doc figure matches its source exactly (PEAD, panel forecasts, setup outcomes, short-v1). The composite-IC arithmetic is correct. All five references exist and support their stated use, and so does the in-claim Chen-Velikov figure (the JFQA 2023 version: 4 bp single, about 20 bp combinations; the 2020 working paper says 8 bp).

The main misdescription is the first bullet. It says combine_forecasts is "the only combination code", but the repo already has rank-blend and dependence-blend composites with recorded, mostly null results (persistent book: "Combination is insufficient"). The finding omits this prior evidence, and it bears directly on the expected gain.

The empirical Bayes "would have predicted failure" calculation is the reviewer's own and depends on the prior choice: +0.004R with a prior from the 24 earlier cohort-v3 t values, +0.009R if all 32 are pooled. Both fall below the +0.010R keep bar.

Recommendation detail: running the decisive test on the selection window spends a window that shorts_retreat on cohort v3 has already read once, though no composite has used it. The cross-references to C05 and C23 could not be checked here.

### Value review — P1, next (weeks)

**Reasoning**

Fund the core idea (M0: one frozen, literature-signed composite judged as a single trial) but not the whole package. Of the mining bets left, it is the most principled one, and it is the one design the 64 nulls never tested.

The code facts in the finding check out:
- The DSL has no cross-sectional operators. The only cs/rank-related line in agentic_trader/research/pooled/formula.py is the dimensionless check at :52, and docs/alpha-data-augmentation-2026-10-18.md §3 lists cs_rank/cs_zscore/group_demean as missing.
- build_cube_inputs refuses more than one field source (agentic_trader/research/pooled/runner.py:317-318).
- combine_forecasts (research/alpha/forecasts.py:219) only reaches a shadow record written with valid=False (research/alpha/shadow.py:132-149).
- The cited numbers match their docs: PEAD +0.115/+0.120/+0.149R (docs/apriori-pead-2026-09-26.md, diagnostics table lines 76-85), Ridge IC 0.0423 with 74.23% from five names (docs/alpha-panel-forecasts-2026-09-17.md), ranker −0.293R in holdout (docs/setup-outcomes-2026-09-23.md 'Selection (H3)'), and shorts_retreat +0.020R t 3.40 → +0.006R in selection (docs/alpha-pooled-short-v1-2026-10-20.md).

Using the one clean historical confirmation window on a single high-prior trial is better than losing it to a 2.5–12% null survivor (pooled map gap 7).

It is not P0, for four reasons:

(1) The prior from our own data is weaker than the finding says. The v4 return-space diagnostic (docs/alpha-pooled-checks-v4-2026-10-07.md, label diagnostic table) already shows the likely components at about 0.013–0.034 Rank IC, none with bootstrap |t| ≥ 3. Under the long-only top-decile bracket label, 7 of 8 had negative edges, and edge-per-ATR was negative under the bracket for most. A 1.8x composite of IC about 0.015 signals is therefore plausible in return space, but on the bracketed long-decile label it may still sit at or below zero.

(2) Power on the proposed decisive window is poor:
- The selection window is 498 sessions.
- Discovery standard errors for slow formulas run up to 0.042R (median 0.017R over about 1,357 sessions), so the selection-window SE is roughly 0.02–0.03R for a slow composite.
- Against an honest expected edge of 0.02–0.05R, that gives t of about 1–2. The selection gate (campaign.py 774-796) is a haircut rule, not a significance test, so this would end in another uninformative null unless an MDE is declared first.

(3) Cards trade the top 1–2 names, while the lane measures deciles. top3_edge only exists at confirmation (campaign.py 826-829), so even a pass does not transfer directly to card quality.

(4) The ML half (M1–M3, PBO/CSCV over 4 specs, Qlib Alpha158+LightGBM, hierarchical theme posteriors) is Effort XL for little gain:
- PBO across 4 configurations is not meaningful.
- Alpha158 is tuned to A-shares.
- Our own 13-feature ranker already reversed in holdout.
Drop or defer that half to P3.

Fund the scoped version:
1. Add cs_zscore/cs_rank/group_demean.
2. Build composite-v1 from bar-derived characteristics only (12-1 momentum, 1-month reversal, 52-week-high proximity, MAX, idiosyncratic vol/beta, Amihud, dollar-volume turnover). This fits the single-source cube today. Pick the components mechanically from OSAP signs filtered by data availability, so nobody curates them against the known v4 results.
3. Declare the MDE before reading.
4. Use the risk-matched control as the decisive statistic.
5. Then spend selection, and confirmation only on a pass.
6. Add short/insider/PEAD components only as a v2 after the FieldSource refactor.

**Risks**

1. **Underpowered decisive test.** On the 498-session selection window (SE about 0.02–0.03R for slow characteristics), even a real 0.03R edge probably fails or passes at random. That would spend the selection window (already touched once by shorts_retreat) and could leave the composite question unresolved for good.
2. **The label kills a real return-space signal.** The v4 diagnostic shows the components' small positive return-space ICs turning into negative bracketed long-decile edges. A composite tested on the bracket label could be 'null' because of the label, not the signal. Pick between the bracket and a horizon-matched time-exit label before freezing, not after.
3. **The confirmation window is contaminated for PEAD and exdiv.** PEAD v2 and exdiv v1 already read 2016-08→2026-07 (pooled map gap 6). If the composite includes the PEAD reaction, its 2024–26 'confirmation' is partly a second look. The ledger checks only pooled/confirmation (storage/alpha.py:42), not overlapping inspected intervals.
4. **Forking paths in component choice.** The operator has seen the v4/cohort3/short/insider single-formula results. Unless component inclusion and signs are mechanical from OSAP/JKP, the 'pre-registered' composite is implicitly fitted to known nulls.
5. **Survivorship and exposure confounds.** Cohorts are current members (cohort-v3.json 'survivorship'). Illiquidity, MAX and volatility characteristics are the ones most distorted by missing delistings, and without a risk-matched control the composite's result can be a low-vol or beta tilt.
6. **Scope creep into the ML/Qlib/PBO stack** (effort L→XL) before the cheap M0 answer is in.
7. **Even a pass gives decile evidence, not top-2 card evidence.** The bridge to cards still has to be built, and a long-only liquid-universe edge of about 0.02–0.05R does not by itself close the cards' −0.10 to −0.15R gap to SPY.

**Prerequisites**

1. **Cross-sectional operators.** cs_zscore, cs_rank, winsorise and group_demean in the pooled DSL. Group demeaning needs a sector field; SIC per CIK comes from EDGAR submissions.
2. **Risk-matched control as the decisive statistic (C05).** Store atr_pct, 60-session beta, log dollar volume and sector in the cube, and use a per-session residual or a DGTW-style matched cell.
3. **A declared MDE before reading.** Plant δ on the composite's own picks in check C's null panels, and refuse the run if the MDE on the chosen window is above roughly 0.04R.
4. **Confirmation-window governance.** Treat any journaled study whose inspected interval overlaps as a read: PEAD, exdiv and the setup outcomes. Add a prospective confirmation interval (2026-08→), so a pass on contaminated history is still confirmed forward.
5. **A mechanical component rule.** OSAP/JKP signs and inclusion filtered only by data availability, frozen and SHA-pinned before any new read.
6. **For a multi-source composite v2 only:** the FieldSource plugin refactor that removes the one-source refusal in runner.py:317-318.
7. **An operator decision on the label:** the bracket, or a horizon-matched time-exit cube for the short-horizon components.

**Better alternative**

Do a cut-down version instead of the full proposal.

(a) Ship cs operators plus a bars-only composite M0 as one formula through the existing fixed-set machinery (checks A+C, risk-matched control, declared MDE). This needs no new data source and no multi-source refactor, takes about a week, and answers the main question.

(b) At the same time, at almost no cost, record the frozen composite's cross-sectional percentile for every ranked native candidate. Put it in scan_candidates_ranked and decision_provenance as context, never a gate, and have cards outcomes attribute it against runners-up. That builds prospective, window-free evidence that the score ranks the names the cards actually trade, which the decile test cannot show.

Drop M1–M3, PBO/CSCV, the Qlib Alpha158+LightGBM benchmark and the hierarchical theme posteriors until M0 passes.

---

<a id="c08"></a>

## C08 — Allocation 'based on existing positions' is nominal: there is no beta or sector exposure model and no single book view, and label-based groups let index clones be carded twice

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | risk | gap | high | M | partially_confirmed |

_lenses: portfolio, architecture, product_

### Claim (as consolidated)

No live rule or card line measures beta, sector dollars or idiosyncratic risk. The caps are gross notional (rules.py:303-329).

Correlation control is weak:
- It counts names per hand-labelled sector group (rules.py:332-348).
- Dynamic names share one 'dynamic' pseudo-group (copilot.py:614-617). On 2026-10-20 VOO was carded after VTI while SPY was blocked.
- The statistical correlation check is off, and it fetches data per pair (evaluator.py:503-523).

There are three book representations, and none carries factors:
- risk.Book, built from signals rows;
- BookSizer weights, at entry notional;
- admission's broker-marked book.

L3 promised bounds on marginal variance, beta and sector contribution (alpha-roadmap.md:521-524); only total volatility shipped. SPY and all 11 sector SPDRs are already in every scan's datasets.

An out-of-sample check on 1,400 random five-name books found Ledoit-Wolf roughly unbiased (realised/predicted 0.92). But every estimator's 90th-percentile ratio is about 1.96, so slow equal-weight windows miss regime shifts.

### Evidence

- agentic_trader/risk/rules.py:303-348; agentic_trader/agent/copilot.py:604-617
- agentic_trader/agent/evaluator.py:503-523; agentic_trader/config.py:397
- agentic_trader/risk/book.py:33-104; execution/book_sizing.py:126-141; execution/capacity.py:194-209 (three book views)
- config/config.yaml:7, 96-112 (labels; SPY and sector SPDRs in universe)
- docs/alpha-roadmap.md:521-524
- Live map Q3/Q7 (VOO carded after VTI, SPY blocked, 2026-10-20)
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_cov_bias.py

### Recommendation

1. One BookSnapshot provider for the selector, sizer, book gates and admission: broker-marked positions, plus reservations, plus this scan's cards.

2. A two-level factor model in risk/:
   - r_i = β_i r_SPY + γ_i r_sectorETF_resid + e_i, on split-adjusted daily returns;
   - Vasicek-shrunk betas;
   - Σ = BFB' + D, with an EWMA (λ≈0.97) blended with 120 sessions and floored.

3. Per card and per book, report beta-dollars, sector dollars, idiosyncratic volatility, correlation to the book and marginal risk contribution.

4. Add net_beta_dollars (for example ≤35% of capital) and sector_dollars (≤15%) rules, rolled out off → preview → enforce.

5. Groups for dynamic names:
   - assign them from EDGAR SIC and statistical clusters (ρ>0.8);
   - treat broad-index ETFs as one group;
   - refuse a card with ρ>0.8 to an open position or to a card already sent this session.

6. Retire the per-pair yfinance correlation check.

### North-star impact

The two daily cards become distinct bets, and each card can say how much SPY-equivalent and sector risk it adds to the existing book.

### References

- Menchero, Orr & Wang (2011), The Barra US Equity Model (USE4), MSCI — Template for a parsimonious market-plus-industry factor covariance
- Ledoit & Wolf (2003), Improved estimation of the covariance matrix of stock returns, J. Empirical Finance 10(5) — Single-index shrinkage target for long-only equity books
- J.P. Morgan/Reuters (1996), RiskMetrics Technical Document — EWMA responsiveness to regime shifts
- Vasicek (1973), JF 28(5) — Beta shrinkage

### Fact check — partially_confirmed

**Corrected claim**

No live rule or card line measures beta, sector dollars or idiosyncratic risk. The notional caps are gross (rules.py:303-329). The correlation_group rule counts same-direction positions per label group (rules.py:332-348). Those groups are the hand-written DEFAULT_CORRELATION_GROUPS plus universe sectors, merged by the loader (config.py:373-382, 1104-1107).

The VOO/VTI case does not come from the dynamic pseudo-group, as claimed. It comes from overlapping, inconsistent labels:
- VOO's groups are {us_broad_market (default label, with SPY and IVV), dynamic}.
- VTI is only in sector_etf_broad_equity.
- On 2026-10-20, VTI's 10:39 ET card (signal 51) used up sector_etf_broad_equity. That blocked SPY, which is in both groups.
- VOO (signal 52, 14:38 ET) passed because neither us_broad_market nor dynamic had a card that day.
- Both cards expired unfilled, so the book never held both.

The statistical correlation check is off by default (config.py:397) and fetches per pair (evaluator.py:503-523). It fetches through the composite provider, Alpaca first with yfinance as fallback, not through yfinance alone.

There are two book types built from three row sources, and none carries factors:
- risk.Book, at entry notional at the scan and broker-marked at admission (admission.py:90, capacity.py:205-208);
- BookSizer's signed weight dict, at entry notional (book_sizing.py:126-141).

The scan's book holds only EXECUTED positions plus this scan's cards (db.py:859-870). Unfilled PENDING cards from earlier scans the same day are invisible to the rules and to the sizer. Only the per-session group cap counts them.

The sizer does price correlation between a candidate and the book, through a Ledoit-Wolf (scaled-identity) covariance (book_vol.py:83-85). But it bounds only total daily volatility, at 0.8% (config.yaml:467), and that bound never bound in preview: 6 of 6 factors were 1.00. The L3 bounds on beta and sector contribution promised at docs/alpha-roadmap.md:521-524 did not ship.

SPY and the 11 sector SPDRs are in the universe scans' datasets, not the intraday job's.

The out-of-sample check reproduces: 1,400 windows, LW geometric-mean realised/predicted variance 0.924. The 90th-percentile variance ratio is 1.83 for sample, 1.96 for LW and 1.98 for single-index, so "every estimator about 1.96" should read 1.8–2.0. The script has no EWMA arm, so the claim that EWMA helps with regime shifts is untested. The single-index (market-factor) estimator is no better calibrated than LW (0.923). A factor model's value here is therefore exposure reporting and limits, not better volatility forecasts.

**Checked**

- agentic_trader/risk/rules.py:303-348: portfolio_notional and asset_class_notional sum unsigned notional, so the caps are gross. correlation_group counts same-direction positions per configured label group.
- agentic_trader/risk/book.py:33-104: BookPosition holds only symbol, direction, asset_class, notional and planned_risk. There is no factor or beta field.
- grep of agentic_trader outside research/ for beta, sector and idiosyncratic risk: no live rule or card line measures beta, sector dollars or idiosyncratic risk. The beta hits are pairs hedge ratios and a macro-explainer string.
- agentic_trader/agent/copilot.py:168, 604-617, 1250-1322: a dynamic name adds the 'dynamic' pseudo-group on top of its configured groups. The per-session group cap counts every card recorded today.
- agentic_trader/config.py:373-382 (DEFAULT_CORRELATION_GROUPS us_broad_market = /MES, /ES, SPY, VOO, IVV), 1104-1107 (the loader fills the defaults and merges in the universe sectors), 163-169 (sector_<name> groups)
- config/config.yaml portfolio block, parsed with yaml: it has no correlation_groups key, so the defaults apply. VOO is not in the universe; VTI is, under sector etf_broad_equity (line 102). max_correlated_positions is 2 (line 7) and max_cards_per_group_per_session is 1 (line 445).
- Postgres signals table (read-only): id 51 VTI LONG 2026-10-20 14:39 UTC (10:39 ET); id 52 VOO LONG 18:38 UTC (14:38 ET), dynamic=true. Both EXPIRED unfilled. id 49 SPY was carded 2026-10-19.
- agentic_trader/agent/evaluator.py:497-523; config.py:396-397: enable_dynamic_correlation defaults to False and config.yaml never sets it. The check calls calculate_correlation once per same-direction position.
- agentic_trader/data/market_data.py:79-96, 245-264; config/config.yaml:433: the correlation check uses the composite provider, which is Alpaca first with yfinance as fallback.
- agentic_trader/execution/book_sizing.py:126-141 (weights at signal-row notional), 237-281 (Ledoit-Wolf over raw daily closes); agentic_trader/risk/book_vol.py:75-111 (closed-form total-vol factor with a candidate-book cross term); research/setups/covariance.py:80-89 (sklearn LedoitWolf, scaled-identity target)
- agentic_trader/execution/admission.py:90; agentic_trader/execution/capacity.py:194-209: admission builds the same risk.Book type from broker-marked rows (max of entry notional and quantity × mark).
- agentic_trader/storage/db.py:859-870: the scan's active_positions are EXECUTED rows only.
- config/config.yaml:463-469: book_sizing is in enforce with a 0.008 daily-vol budget and a 120-session lookback. The preview week produced 6 factors, all 1.00.
- docs/alpha-roadmap.md:519-524 (L3 promises bounds on marginal variance, beta and sector contribution); 535-538 (the PR B status ships a total-vol factor only)
- docs/risk-policy.md:287-293 (the default groups and universe sectors are merged)
- agentic_trader/config.py:900-910: the intraday job scans non_universe_contracts only.
- Re-ran ~/agentic-trader-research/holistic-review-20261021/scripts/pm_cov_bias.py read-only on ~/agentic-trader-research/setup-cache-v1 (159 symbols, 2020-04-27 to 2026-09-22). Results: 1,400 windows. Geometric-mean realised/predicted variance is 0.924 for LW, 0.873 for sample and 0.923 for single-index. The p90 ratio is 1.96 for LW, 1.83 for sample and 1.98 for single-index.
- References checked by web search: Vasicek 1973 JF 28(5):1233-39; Ledoit & Wolf 2003 JEF 10:603-621 (single-index target); Menchero, Orr & Wang 2011 USE4 Methodology Notes; RiskMetrics Technical Document, 4th ed. 1996 (daily λ 0.94, monthly λ 0.97)

**Bad references**

_none_

**Notes**

The core gap is real:
- There is no beta, sector-dollar or idiosyncratic-risk measurement or limit.
- The caps are gross notional.
- The groups are labels.
- The L3 beta and sector bounds in the roadmap did not ship.
- No book view carries factors.
- Index clones can be carded on the same day.

The cited mechanism for the VOO example is wrong. scratchpad maps/live.md:76 says VOO had "only the dynamic group". The code says VOO is in the default us_broad_market group, which config.yaml does not override. The real defect is overlapping and inconsistent label sets: VTI is missing from us_broad_market, and VOO, as a non-universe name, is missing from sector_etf_broad_equity. That still supports the recommendation to treat broad-index ETFs as one group, and to use statistical or SIC grouping.

The finding understates the existing control. BookSizer, in enforce since 2026-10-20, already uses a covariance that includes candidate-book cross terms. Its weakness is that it uses a total-volatility budget that never binds, not that correlation is ignored.

A gap the finding does not stress: unfilled PENDING cards from earlier scans in the session are outside every book view, at db.py:859-870.

Two smaller corrections:
- Recommendation 6 calls the correlation check "yfinance". It actually runs Alpaca first with yfinance as fallback.
- RiskMetrics' daily decay factor is 0.94. The λ≈0.97 in the recommendation is RiskMetrics' monthly value, used here as a design choice.

All four references exist and support their stated use. One source gives Vasicek's paper as issue 28(4); most give 28(5), pp. 1233-39.

### Value review — P1, now (days)

**Reasoning**

The diagnosis is correct, but the recommendation bundles a cheap fix that matters with a factor-model build that would not change decisions today. Fund the first part now and defer the second.

What the code does now:
- Correlation control counts same-direction names per configured group (agentic_trader/risk/rules.py:332-348).
- Every dynamic name falls into the single 'dynamic' pseudo-group (agentic_trader/agent/copilot.py:614-617, 1266-1318).
- The only statistical check is off by default (agentic_trader/config.py:397). When enabled, it makes a yfinance fetch for each pair (agentic_trader/agent/evaluator.py:503-523; agentic_trader/data/market_data.py:245-257).
- VTI is labelled etf_broad_equity in config/config.yaml:96-101. VOO is not in the config, so on 2026-10-20 it went out as a 'dynamic' card after VTI while SPY was blocked (live map gap 4).
- Broad-index ETFs carry no excess over SPY by construction, and the bracket study shows the native stream's raw R is mostly market exposure. Every exit arm trails SPY by 0.10-0.15R (docs/bracket-counterfactual-2026-10-18.md "What this says" section 3).
- So one of the two daily slots is regularly spent on pure beta. Fixing that is a direct card-quality gain for the north star and takes hours to days.

Why the rest waits:
- The full model (BookSnapshot unification, Vasicek betas, Σ = BFB' + D with EWMA, net-beta and sector-dollar caps) would change almost no decisions at today's scale.
- The paper book is mostly cash: drawdown 0.50%, only a few positions, 1-2 cards a day (live map item 7).
- The variance budget it would extend has never bound. All six preview factors were 1.00, and with $7,500 per-trade and $40k equity caps it cannot bind (portfolio map G1).
- Allocating cards with measured EV at or below 0 does not create edge. An honest allocator's main output would be 'size zero', which card_policy already covers.
- Unifying the three book views means touching admission's broker-marked book, which sits inside contract 1 (FIFO/ledger, fingerprint pinning). That is high regression risk for little gain while the book is nearly empty.
- The 90th-percentile realised/predicted ratio of 1.96 argues for EWMA only once the volatility budget can actually bind.

Fund now (P1, days):
1. A static broad-index clone set in config (VOO/IVV/SPLG/ITOT/SCHB/QQQM and similar, mapped to etf_broad_equity), or simply exclude broad-index ETFs from native cards.
2. ρ > 0.8 dedup against open positions and against cards already sent this session, using the Ledoit-Wolf covariance BookSizer already builds each scan (execution/book_sizing.py:188-237). Add SPY to its symbol set.
3. Retire the per-pair yfinance correlation check.
4. Add one descriptive card and journal line: candidate β and the book's net beta-dollars.

Defer: the two-level factor model, the sector-dollar and net-beta caps and the BookSnapshot unification are P2-P3. Revisit them when the book regularly holds five or more concurrent positions or book_vol starts binding.

**Risks**

1. Fewer cards. A ρ > 0.8 refusal can empty a session when the ranked list is dominated by one tape, which works against the steady-stream goal. Measure the would-refuse rate in preview first, using the same off/preview/enforce pattern as card_policy.
2. Split contamination. The covariance uses raw prices (execution/book_sizing.py:222; research/setups/covariance.py:20-26), so a recent split turns into a -50% or -90% return. That corrupts β and ρ, and through Ledoit-Wolf's diagonal target it inflates every name's variance (portfolio map G7). No beta number should reach a card until returns are split-safe.
3. Noisy single-stock correlations. 120-session ρ for single stocks in the 0.6-0.85 range is noisy and will flap around the threshold. Clone ETFs (ρ about 0.99) are robust; the threshold is not.
4. Sparse data drops book names. The joint inner join over the book plus all candidates can drop a book name when one candidate has sparse IEX bars (portfolio map G11). That turns the dedup context 'unavailable', which must never refuse a card.
5. The full model could bind the wrong cards. Rolled out to admission, it changes the risk fingerprint and lease semantics (contract 1) and could silently refuse cards the operator would tap. Shared-rule parity tests in risk/ are mandatory for that step.
6. A new data dependency. EDGAR SIC mapping adds acquisition and staleness handling for little marginal value over return-based clustering.

**Prerequisites**

1. An operator decision on whether broad-index ETFs (and possibly the sector SPDRs) belong in native card generation at all.
2. Split-safe returns for the risk model, before any β or ρ is shown or enforced. Either fetch adjustment='split' for the covariance only, or mask action-date returns using the corporate-actions capture the daemon already runs.
3. cards outcomes reporting excess R against SPY alongside raw R, so the β line has a measured counterpart. The labeller already computes excess_r (research/setups/outcomes.py:382-383).
4. Before the deferred factor-model phase:
   - a book that actually holds several concurrent positions;
   - a fix for the cap inconsistency (portfolio map G1/G2), so that a book-level volatility or beta rule can bind at all.

**Better alternative**

Ship the cheap slice instead of the factor model. It takes hours to two days and fixes the observed failure (VOO carded after VTI) with almost no new machinery:
1. Config only: put the broad-index clones (VOO, IVV, SPLG, ITOT, SCHB, QQQM and similar) in the existing etf_broad_equity correlation group in config/config.yaml. Better still, exclude broad-index ETFs from native card generation, since their excess return is about zero by construction.
2. In preview, refuse a card with ρ > 0.8 to an open position or to a card already sent this session, using the per-scan Ledoit-Wolf matrix BookSizer already builds (with SPY added). Then retire the yfinance per-pair check (evaluator.py:503-523).
3. Add one descriptive line per card: 'β x.x, adds $Y SPY-equivalent; book net β$ $Z'.

Defer the Barra-lite model, EWMA blending, sector-dollar and net-beta caps and the BookSnapshot unification until the book is non-trivial and the existing book_vol rule has bound at least once. A higher-leverage use of the same effort is a predeclared β-hedged card variant (short β x notional of SPY), whose outcome is excess R directly; that measures whether the card stream has any edge beyond market beta.

---

<a id="c10"></a>

## C10 — Live native scans and every research base rate compute the trigger on different bars (IEX raw UTC-4h vs SIP all-adjusted), and the live 4h clock changes meaning on 2026-11-01

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | data | defect | high | M | partially_confirmed |

_lenses: data, architecture_

### Claim (as consolidated)

Live and research use different bars:
- load_config defaults the live feed to IEX (config.py:1043-1050); all 3,432 captures on 2026-10-20 are alpaca:iex.
- Live bars are raw, and 4h bars are a UTC resample('4h') that includes extended-hours buckets (market_data.py:119-138).
- Replay base rates, the bracket counterfactual and the −0.110R verdict use SIP with adjustment 'all' (baserates.py:57-58).
- card_stats and card_policy label outcomes on IEX.

Measured differences:
- SPY has 9 hourly bars per session on live IEX against 16 on SIP (04:00–19:00).
- Liquid names get about 4 buckets of 4h per session in replay against 2 live, so the 4h EMA20/RSI14/ATR14 spans about 5 sessions in research and about 10 live.

Parity is not acknowledged. replay.py admits only the forming-bar gap, and the pooled formula.py docstring claims the live scan sees the same bars.

When EDT ends on 2026-11-01, the UTC buckets shift to 07–11, 11–15 and 15–19 ET, so mid-caps go from 2 to 3 bars per session overnight.

The provider also falls back silently to yfinance auto-adjusted frames, with no feed check. fetch_latest_price, which prices taps and trailing ratchets, has no feed pin and no age check.

### Evidence

- agentic_trader/config.py:1043-1050 (feed defaults to iex)
- agentic_trader/data/market_data.py:119-138, 197-239 (UTC resample_to_4h)
- agentic_trader/research/setups/baserates.py:57-58; research/setups/replay.py:1-24; research/pooled/formula.py:2-4 (parity claim)
- agentic_trader/research/setups/sources.py:26-34; research/setups/card_stats_worker.py:116 (labels on configured feed)
- agentic_trader/data/providers.py:348-379, 426-460, 510-529 (fetch_latest_price; yfinance fallback chain)
- Measured hourly bar presence: ~/.local/state/agentic-trader/market-data/2026-10-20 manifests vs ~/agentic-trader-research/setup-cache-v1 and pooled-cache-v1
- agentic_trader/market/bars.py:276-346 (build_session_bars exists; used only by research); agent/copilot.py:397-401 (PEAD already on delayed SIP)

### Recommendation

1. Make DataContract (feed, adjustment, bar-builder version, clock) a frozen, hashed value on every label, forecast and signal row. Card evidence and card_policy refuse to speak for a card whose contract differs from the evidence's.

2. Use one RTH, open-anchored bar builder (market/bars.py build_session_bars) for live, replay and labels:
   - hourly bars 09:30–10:30 through 15:30–16:00;
   - 4h bars 09:30–13:30 and 13:30–16:00;
   - the forming bar dropped.
   Run native scans on delayed SIP (end ≤ now−16 min), as PEAD already does. Move the suggestion scans to 10:47 and 14:47 or later, or use a 30-minute grid. Label card_stats on SIP.

3. Before 2026-11-01, either freeze the 4h bucket edges in ET as a stopgap or ship the builder. Either way, the native strategy needs a new frozen replay base rate.

4. Fail closed on provenance:
   - remove yfinance from equity scan fallbacks, or journal the provider per frame and exclude non-Alpaca frames from gates;
   - for taps and ratchets, replace fetch_latest_price with a timestamped Alpaca snapshot and a maximum age.

5. Add a parity regression test that replays journaled live scans through the replay path.

### North-star impact

Until the series match, the honest base rate printed on cards, card_policy's evidence and the −0.11R native verdict all describe a different trigger from the one that sends cards. On 2026-11-02 the live trigger changes again with no code change.

### References

- Alpaca Market Data FAQ (historical SIP with end ≥15 minutes old on the free plan) — Free delayed SIP makes live and research one feed (https://docs.alpaca.markets/us/docs/market-data-faq)
- Alpaca Stock Bars API reference — Feed and adjustment parameters the live path should pin (https://docs.alpaca.markets/reference/stockbars)
- NautilusTrader concepts overview — One strategy and execution kernel shared by backtest and live (https://nautilustrader.io/docs/latest/concepts/overview)

### Fact check — partially_confirmed

**Corrected claim**

The core defect is real:
- Live native scans run on raw IEX bars (load_config's default; all 3,432 captures on 2026-10-20 are alpaca:iex).
- The live 4h bars are a UTC-anchored resample('4h') of the full hourly frame, with no RTH filter.
- The setup replay base rates and the bracket counterfactual (A0_live excess -0.110R; 7,457 of 8,768 setups are trend-pullback 4h) apply the same resample to SIP all-adjusted hourly bars covering 04:00-19:00 ET.
- Measured bar counts:
  - SIP: 16 hourly bars and 4 UTC 4h buckets per session.
  - Live IEX, SPY: 9 hourly bars (08:00-16:00 ET), 3 buckets.
  - Live IEX, AAPL and similar names: 8 hourly bars, 2 buckets.
  - So a 4h EMA20/RSI14/ATR14 spans about 5 sessions in research and about 10 sessions live for most names.
- When EDT ends on 2026-11-01, live UTC buckets move to 07-11, 11-15 and 15-19 ET, and those names go from 2 to 3 buckets per session.
- fetch_latest_price, used for tap pricing and trailing ratchets, has no feed pin and no age check, and can fall back to yfinance.
- The native scan path never checks frame provenance (attrs['feed']).

Corrections:
1. Parity is partly acknowledged, not ignored. The setup-outcomes design spec, "Accepted parity gaps" (docs/superpowers/specs/2026-09-23-setup-outcomes-and-shadow-ranker-design.md:147-157, cited from docs/setup-outcomes-2026-09-23.md:144-145), lists the feed (IEX vs SIP) and adjustment (raw vs all) differences. docs/card-evidence.md:362 notes IEX's hourly gaps. What is not acknowledged is the consequence for bar count and 4h-bucket composition, including the extended-hours buckets and the DST shift. The bracket-counterfactual result doc does not mention it at all.
2. card_stats and card_policy do not describe a different trigger. They label the live journaled candidates, on the live feed. The mismatch is between live and the SIP research verdicts (setup baserates, bracket counterfactual -0.110R, recurrence). After 2026-11-01, card_stats' rolling window will mix pre-DST and post-DST trigger behaviour.
3. The yfinance fallback is not silent. It logs a 'fallback_triggered' warning (resilience/fallback.py:158-169) and tags frames with attrs feed='yfinance' (providers.py:436). Nothing in the scan path checks that tag, journals it or gates on it.
4. formula.py:2-4 claims D-1 completed-session timing for a research-only lane that never runs live. It is not a parity claim for the native scan.
5. build_session_bars is also used by the daemon's diagnostic alpha decisions worker (research/alpha/decisions.py:207), but never by the native scan path.

**Checked**

- agentic_trader/config.py:1043-1050: load_config sets alpaca_feed to 'iex' when neither the env nor the YAML names a feed. MarketDataConfig's own default is 'sip' (config.py:689), but config/config.yaml:428-440 has no alpaca_feed, so production runs on IEX.
- ~/.local/state/agentic-trader/market-data/2026-10-20: all 3,432 capture manifests have request.feed='alpaca:iex' (3,359 raw, 73 'all').
- Live IEX hourly buckets on 2026-10-16 and 2026-10-19: SPY has 9 (08:00-16:00 ET), AAPL has 8 (08:00-15:00 ET). docs/production.md:101-105 and docs/superpowers/specs/2026-09-21-scan-universe-and-suggestion-budget-design.md:228-230 also note SPY's 08:00 and 16:00 IEX buckets.
- SIP cache (~/agentic-trader-research/setup-cache-v1, AAPL_1h and SPY_1h): median 16 hourly bars per session (04:00-19:00 ET) and a median of 4 UTC 4h buckets per ET date.
- agentic_trader/data/market_data.py:119-138: resample_to_4h is a plain df.resample('4h'). providers.py has no tz_convert, so the SDK's UTC index anchors the buckets at UTC midnight. market_data.py:216-217 builds live 4h bars from the full hourly frame with no RTH filter.
- agentic_trader/research/setups/replay.py:237: replay applies the same resample_to_4h to the full cached hourly slice, extended hours included. replay.py:16-23 lists only the forming-bar parity gap.
- agentic_trader/data/providers.py:186: Alpaca fetch_bars defaults to adjustment='raw'.
- agentic_trader/research/setups/baserates.py:57-58, bracket_counterfactual.py:94, recurrence.py:137 and study.py:75 all pin feed alpaca:sip; baserates.py:58 also pins adjustment 'all'.
- docs/bracket-counterfactual-2026-10-18.md:18 (SIP all-adjusted caches) and :70 (A0_live excess -0.110R). Its Caveats section (113ff) says nothing about feed or bar construction. Trend pullback 4h is 7,457 of 8,768 setups (:80).
- agentic_trader/research/setups/sources.py:26-34 and card_stats_worker.py:116: card_stats labels on the configured feed with raw adjustment.
- agentic_trader/agent/copilot.py:1227-1250: the card evidence block and card_policy read the card_stats snapshot, which labels the live journaled scan_candidates_ranked.
- docs/card-evidence.md:362: labels use the configured feed, 'IEX in production, with its hourly gaps'.
- docs/superpowers/specs/2026-09-23-setup-outcomes-and-shadow-ranker-design.md:147-157 ('Accepted parity gaps': adjustment and feed) and docs/setup-outcomes-2026-09-23.md:144-145: the feed and adjustment differences are acknowledged, but only as effects on volume levels, ranks and splits.
- agentic_trader/research/pooled/formula.py:3-4: the 'same bars' wording is about D-1 completed-session timing in a research-only lane.
- DST arithmetic for EST (UTC-5): IEX 08:00-15:00 ET maps to UTC 4h buckets covering 08-10, 11-14 and 15 ET, i.e. 3 buckets. US DST ends Sunday 2026-11-01.
- agentic_trader/data/providers.py:348-379: Alpaca fetch_latest_price builds StockLatestTradeRequest and StockLatestBarRequest with no feed argument and never checks a timestamp or age.
- providers.py:530-570: the composite latest-price path falls back to yfinance. providers.py:436 tags yfinance frames with attrs feed='yfinance'.
- agentic_trader/resilience/fallback.py:158-169: a cascade logs a 'fallback_triggered' warning.
- Native scan path (copilot.py, screeners/, agent/evaluator.py, risk/): no check of attrs['feed']. Feed checks exist only in research/alpha and data/sessions.py.
- agentic_trader/agent/copilot.py:2554 (manage_trailing_stops) and :3148-3164 (_tap_price) both use fetch_latest_price with no age check.
- agentic_trader/agent/copilot.py:397-401: the PEAD bars use feed='sip'.
- agentic_trader/market/bars.py:136-146 and 282-346: session-anchored builder with 1h and 4h support. Its callers are research/alpha observation.py, decisions.py and replay_workflow.py; the decisions worker is diagnostic and runs in the daemon.
- tests/: no test replays journaled live scans through the replay path. tests/research/setups/test_replay.py:194 only checks that resample_to_4h is applied identically.
- Alpaca Market Data FAQ (fetched): free plan is IEX only; 'For historical queries, the end parameter must be at least 15 minutes old to query SIP data without a subscription.'
- Alpaca stockbars reference (fetched): documents the feed and adjustment query parameters.
- NautilusTrader overview (fetched): 'The same strategy and execution-algorithm code can run across backtest and live systems...'

**Bad references**

_none_

**Notes**

All three external references support their use. The FAQ says the free tier can query historical SIP when end is at least 15 minutes old "without a subscription". The stockbars reference documents the feed and adjustment parameters. The NautilusTrader overview states that the same strategy code runs in backtest and live.

All cited file:line locations exist and match.

The impact is arguably understated: 4h trend pullback makes up 85% of the bracket-counterfactual sample, so the -0.110R verdict is mostly about the 4h construction that differs from live.

Every measurement script ran read-only from ~/agentic-trader-research/holistic-review-20261021/c10/. No repo files were changed and no secrets were read.

### Value review — P1, now (days)

**Reasoning**

The defect is real; I checked it in the code.
- **Live feed.** `load_config` defaults the live feed to IEX (`agentic_trader/config.py:1043-1050`), and `config/config.yaml` does not override it for scans.
- **Live 4h bars.** They are a plain `df_1h.resample("4h")` on a UTC index, built from hourly bars that include extended hours and the bar still forming (`agentic_trader/data/market_data.py:119-138, 206-213`).
- **Research bars.** Base rates are pinned to `feed: alpaca:sip`, `adjustment: all` (`agentic_trader/research/setups/baserates.py:57-58`).
- **Live labels.** `card_stats` labels on the configured feed, which is IEX (`agentic_trader/research/setups/card_stats_worker.py:116`; `agentic_trader/research/setups/sources.py:26-34`).
- **Parity claim.** `agentic_trader/research/setups/replay.py:1-24` claims identical bars and admits only the forming-bar gap. The pooled `agentic_trader/research/pooled/formula.py:2-4` docstring says "the live scan sees the same bars".

So the printed "honest base rate" and the measured outcomes describe a sibling of the trigger that actually sends cards. The measured outcomes are the +0.03R development rate (`docs/setup-outcomes-2026-09-23.md` "Base rates") and the −0.110R versus SPY (`docs/bracket-counterfactual-2026-10-18.md` "Descriptives"). IEX labels also under-count stop and target touches. That biases the only outcome evidence the operator sees.

**Why P1 and not P0.** Parity makes measurement honest; it does not create edge. The native trend-pullback signal has no edge on any feed:
- +0.03R in development, and every scorer loses in the one-shot holdout (`docs/setup-outcomes-2026-09-23.md` "Selection", "Interpretation and decisions").
- −0.110R versus SPY (`docs/bracket-counterfactual-2026-10-18.md` "Descriptives").

Nothing suggests the IEX variant is better. For card quality, the binding constraint stays the signal source. Even so, this is a must-do prerequisite for any research-born signal, such as a panel-score probe or a future finalist, to reach Telegram with evidence that applies to it. It is also a prerequisite for any `card_policy` enforce decision.

**Part of the recommendation is wrong.** Replay calls the same UTC `resample_to_4h` on its hourly slice (`agentic_trader/research/setups/replay.py:237`) across 2021–2026. The frozen base rate therefore already mixes the EDT and EST bucket regimes. The 2026-11-01 shift is seasonal non-stationarity shared by live and research, not a new parity break. A live-only stopgap that freezes 4h edges in ET before Nov 1 would widen the gap from the frozen base rate. Skip it. Change the bucketing only together with a new predeclared base-rate run.

**Second gap the finding misses.** Fourteen of 37 native cards were sent outside the 10:35/14:35 slots, by swing scans and restart scans that fire immediately at startup (`maps/live.md` gap 3, citing `service.py:581-593`). Replay models only the frozen slots, so bar parity is moot for about 38% of cards unless card emission is restricted to those slots.

**Provenance part: cheap, high integrity value, do it immediately.**
- `CompositeMarketDataProvider` falls back silently to yfinance auto-adjusted frames (`agentic_trader/data/providers.py:426-437, 510-529`), and no live consumer checks `attrs["feed"]`.
- `fetch_latest_price` has no feed pin and no age check, and can fall back to yfinance `fast_info` (`agentic_trader/data/providers.py:348-379, 439-460`). It prices taps and trailing-stop ratchets.

**Precedent.** PEAD already reads delayed SIP (`agentic_trader/agent/copilot.py:397-401`). `docs/research-data-sources.md` (lines 11-27) confirms that SIP with `end` at least 15 minutes old works on the current plan, so a single-feed design is available at no cost.

**Risks**

1. **The minute-level builder would empty the scan.** Using `build_session_bars` (`agentic_trader/market/bars.py:281-346`) live as written would make thin names disappear. It raises `SessionCoverageError` on any missing minute. That is the same all-or-nothing rule that left the IEX forward lane with 0 scored decisions (`maps/live.md` "Shadow alpha lane"; the deferred sparse-layout item in `docs/alpha-roadmap.md:667`). Mid-cap and dynamic names would be dropped and card flow would fall, which runs against the north star.
2. **Fetch cost.** Pulling 60 days of minute bars for about 150 symbols on every scan is also heavy against 150 requests per minute.
3. **Garden of forking paths.** Re-running the native base rate under several bar builders turns the replay into a second look. One builder must be predeclared under a new `setup_baserates` version.
4. **Card count.** Moving scans to 10:47/14:47, dropping the forming bar and using RTH-only buckets will change trigger frequency and the budget dynamics. Expect fewer or different cards.
5. **Old evidence.** Card evidence windows labelled on IEX become non-comparable. They need relabelling or must be marked as a different contract. Old journal evidence must not be silently reinterpreted.
6. **Scope creep.** A hashed DataContract column on every label, forecast and signal row means a schema migration past head 008 plus wide churn. Most of the value comes from putting it in the existing JSON provenance (the `decision_provenance` and `card_stats` snapshots).
7. **Fail-closed side effects.** If Alpaca is degraded, scans will produce fewer cards. The scan must count and surface skipped symbols so they are not silently absent.

**Prerequisites**

1. **Measure before building (1–2 days).** Use the raw IEX pages that `BarEvidenceStore` retains from live scans to recompute the native trigger at recent 10:35/14:35 instants. Compare it with SIP replay at the same instants, reporting trigger agreement (Jaccard) and the distributions of the 4h RSI, EMA distance and volume ratio. This sizes the gap and tells you whether the base rate on cards is meaningfully wrong today.
2. **Restrict card emission to the frozen slots.** Stop swing scans and restart scans from spending the native card budget, or replay them too. Otherwise parity covers only about 60% of cards.
3. **Confirm delayed SIP covers the universe.** Check that delayed SIP (`end` at least 16 minutes back) on the 30Min or hourly endpoint covers the static and dynamic universe within the current pacing.
4. **Write the new base-rate protocol.** Predeclare a frozen `setup_baserates` version that names exactly one bar builder (feed `sip`, a fixed adjustment, RTH-only open-anchored buckets, forming bar dropped, the scan times).
5. **Get operator approval** for the scan-time change and the trigger-semantics change.

**Better alternative**

Split the finding and fund the cheap parts first.

**(a) Days, no research needed: fail closed on provenance.**
- Drop yfinance from equity bar fallbacks in alpaca mode. Skip and count symbols that have no Alpaca frame.
- Replace `fetch_latest_price` for taps and ratchets with a timestamped Alpaca latest trade that has a maximum age.
- Stamp feed, adjustment and builder version into `decision_provenance`.

**(b) Days: run the agreement measurement in prerequisite 1.** If trigger agreement is high, stop at (a) and (c).

**(c) Weeks, if agreement is low.** Use one shared, pure bar function in both `MarketDataFetcher.fetch_data` and `replay.frames_at`:
- Fetch delayed-SIP **30Min** bars, not minutes. They align with 09:30, need no strict per-minute coverage and cost about the same number of requests as today.
- Filter to RTH and aggregate into open-anchored buckets: hourly 09:30–10:30 … 15:30–16:00, and 4h 09:30–13:30 and 13:30–16:00.
- Drop the forming bar.

Then:
- Move the scans to 10:47/14:47.
- Switch `card_stats` labels to SIP.
- Run one predeclared base-rate replay under a new version.
- Make card evidence and `card_policy` refuse to speak when the card's provenance contract differs from the evidence's.
- Add a parity regression test that replays journaled live evidence pages through `frames_at`.

**Do not ship the live-only ET freeze before 2026-11-01.** Replay already contains both DST regimes. Defer the full hashed DataContract on every row until a second signal source actually needs it.

---

<a id="c11"></a>

## C11 — Native R is ill-conditioned by tight 4h-ATR stops: cost is about 0.08R per setup, the SPY control is uncosted, the vol_20 lead is plausibly a cost artefact, and stop width has never been tested

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | research_methodology | defect | high | M | partially_confirmed |

_lenses: quant, portfolio_

### Claim (as consolidated)

The native stop is max(swing, 1.5×ATR14 on 4h bars) (evaluator.py:208-214; strategies.py:163), against a mean hold of 5.6 sessions. The median stop is 1.80% of price; the 10th percentile is 0.88%.

Cost is charged in R with no risk-unit floor (labels.py:144):
- A0 costs 0.077R per setup, implying a harmonic-mean stop of about 1.3%.
- Squeeze on 1h bars costs 0.12R.

The SPY control is mishandled:
- market_r is uncosted (outcomes.py:186-219).
- It is also unclipped, while excess is clipped at ±10R (bracket_counterfactual.py:172-177). The published columns therefore do not reconcile: 0.039 − 0.211 ≠ −0.110.

Every bracket arm shared the planned stop, so stop width was never varied.

The vol_20 lead may be mechanical. Stop % tracks volatility, so cost in R falls as vol_20 rises. A simulation gives a mechanical ρ of +0.02 to +0.13, which brackets the observed +0.049 (study.py:502-504). The prospective read (~200 candidates, SE ≈0.07) cannot discriminate between the two.

Beta-adjusted alpha by stop-width quintile:
- 2021–26: worst at the tight end (−9.3, −19.7, −4.8, −0.8, 0.0 bp).
- 2017–21: rises to +22.9 bp at the wide end.

### Evidence

- agentic_trader/agent/evaluator.py:208-214; agentic_trader/screeners/strategies.py:163; config/config.yaml:274-275
- agentic_trader/research/setups/labels.py:144, 187-188; research/setups/outcomes.py:186-219
- agentic_trader/research/setups/bracket_counterfactual.py:172-177; research/setups/study.py:502-504
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (arms share the stop), 'Descriptives'
- docs/setup-outcomes-2026-09-23.md 'Base rates', 'Hypotheses' (vol_20 ρ +0.049)
- ~/agentic-trader-research/holistic-review-20261021/vol20_cost_confound.py and scripts/pm_quintile_beta.py, pm_stop_quintile.py

### Recommendation

1. Before reading the 10-21→11-04 window, re-run H2d and H2h on gross r and on bps-charged r, using the retained development frames. If ρ against gross r is about 0, retire vol_20. For a powered read, use a declared second use of the 2017–21 replay (8,119 setups).

2. Floor the risk unit in every R statistic, at ≥0.5× daily ATR or ≥0.75% of price. Report a %-return, β-adjusted, cost-symmetric excess, charging SPY about 1 bp per side.

3. Predeclare a stop-width construction test on the existing caches:
   - arms: {1.5×4h ATR (live), 1× daily ATR, 1.5× daily ATR, 2× daily ATR, 3× daily ATR};
   - 2R target and volatility-normalised notional;
   - decisive statistic: β-adjusted alpha per dollar, net of cost.

4. Predeclare excluding rates, credit and metals ETFs and index clones from the equity-trend source, or benchmark them to cash.

5. Replace the flat 5 bp cost with measured shortfall from journaled arrival quotes.

### North-star impact

Both the diagnosis that native cards 'lag the index' and the only surviving feature lead rest on this unit. A wider, daily-ATR stop could lift native card EV by several hundredths of R without any new alpha.

### References

- Kaminski & Lo (2014), When do stop-loss rules stop losses?, J. Financial Markets 18 — Stop width changes expected return and cost drag; width is the untested arm
- Novy-Marx & Velikov (2016), A Taxonomy of Anomalies and Their Trading Costs, RFS 29(1) — Costs must be modelled per name in return units
- Moskowitz, Ooi & Pedersen (2012), Time series momentum, JFE 104(2) — Cross-asset trend positions should be judged against their own asset or cash

### Fact check — partially_confirmed

**Corrected claim**

The live and replay stop is max(entry - (swing low - 2 ticks), 1.5 x ATR14). The ATR is the 4h ATR for trend pullback and squeeze-4h setups, and the 1h ATR for squeeze-1h setups (evaluator.py:208-214; strategies.py:163 and the squeeze path at 223-297). Replay uses the same function (replay.py:313).

On the 8,768 replayed long A0 setups:
- The median stop is 1.80% of price and the 10th percentile is 0.88%.
- Cost is charged in R with no price-% floor (labels.py:144, 187). It averages 0.077R, which implies a 1.30% harmonic-mean stop. Squeeze-1h costs 0.12R.
- The worst cases are degenerate: BIL entries with 3-cent stops (0.03%) cost about 3R per trade.

The SPY control has two issues:
- It is uncosted by documented design (outcomes.py:198-199). Charging it at a realistic ~1 bp per side would shift excess by only ~0.015R.
- It is not clipped, while r_cost and excess are clipped at ±10R (bracket_counterfactual.py:172-177). The published SPY column (+0.211, docs/bracket-counterfactual-2026-10-18.md 'Descriptives') is inflated by 48 degenerate rows. The clipped mean is +0.148, which reconciles with the -0.110 excess.

The vol_20 lead is a cost artefact, and more clearly so than the finding states. On these long setups:
- vol_20 rank correlates +0.069 with r_cost but only +0.010 with gross r (90% CI [-0.009, +0.028]).
- vol_20 rank correlates +0.66 with stop%.

So the H2d result (+0.049 vs r_cost, study.py:502-504) is almost entirely the vol to stop% to cost-in-R channel.

Stop width was not "never tested". The October 8 funnel diagnostic tested k ∈ {1, 1.5, 2, 3} x 4h-ATR stops against 1-3R targets on 30 live candidates. No cell was credibly positive. The bracket spec excluded stop geometries on that basis (specs/2026-10-18-bracket-counterfactual-design.md:12-13, 48, 337). What is true is narrower: stop width has never been tested on a powered replay population, nor with daily-ATR units, and every v1 arm shared the planned stop.

The stop-width quintile alphas reproduce: 2021-26 -9.3/-19.7/-4.8/-0.8/0.0 bp and 2017-21 -3.7/+3.5/-5.0/+9.9/+22.9 bp. However:
- The worst quintile is Q2, not the tightest.
- 2017-21 is not monotone.
- The quintiles are cross-sectional selection: wide stops mean high-beta, high-vol names, with betas 0.39 to 1.21. They are not the effect of widening a given setup's stop.
- In return space the cost is a flat 10 bp, so this gradient is not the cost mechanism.

**Checked**

- agentic_trader/agent/evaluator.py:202-214: entry, min_stop_distance = min_stop_atr_multiple*candidate.atr_14, and stop_distance = max(entry - (swing_low - 2 ticks), min_stop_distance). Confirmed.
- agentic_trader/screeners/strategies.py:133,163: trend pullback atr_14 = 4h ATR. Squeeze (strategies.py:223-297, latest['ATR_14'] of the evaluated timeframe, called for 4h and 1h at lines 343-346) uses 1h ATR for its 1h setups.
- config/config.yaml:274-275: min_risk_reward_ratio 2.0, min_stop_atr_multiple 1.5. Confirmed.
- agentic_trader/research/setups/labels.py:144: r_cost = r - 2*cost*entry/risk_unit. labels.py:187: risk_unit = abs(entry - stop), with no price-% floor. Confirmed.
- agentic_trader/research/setups/outcomes.py:186-219: market_r is SPY open-to-close in setup R. Its docstring says 'Uncosted: a beta-one exposure control', so this is a documented design choice. Confirmed.
- agentic_trader/research/setups/bracket_counterfactual.py:172-177: r and r_cost are clipped and excess_r is recomputed and clipped, but market_r is not. bracket_counterfactual.py:377: the descriptive mean_market_r is taken from unclipped market_r. Confirmed.
- agentic_trader/research/setups/study.py:188-219 and 502-504: H1/H2 are pooled Spearman tests against r_cost. Confirmed.
- agentic_trader/research/setups/replay.py:313: replay uses evaluator.calculate_levels_deterministic, so research and live stops match.
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run': all arms share the planned stop. 'Descriptives': A0 hold 5.6, r_cost +0.039, SPY +0.211, excess -0.110. Confirmed.
- docs/setup-outcomes-2026-09-23.md 'Hypotheses': H2d vol_20 rho +0.049, Holm p 0.004. Confirmed.
- docs/alpha-roadmap.md:300-303,630: prospective vol_20 window 2026-10-21 -> 2026-11-04. Confirmed.
- Counter-evidence: docs/superpowers/specs/2026-10-18-bracket-counterfactual-design.md:12-13,48,337, docs/alpha-roadmap.md:540,555, and ~/agentic-trader-research/card-funnel-diagnostic-20261008/report.md section 3: the October 8 funnel diagnostic ran a k x ATR(4h) stop grid (k = 1, 1.5, 2, 3) against targets of 1, 1.5, 2 and 3R on 30 live candidates.
- Recomputed on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz (A0, n 8,768):
- stop% p10 0.885% and median 1.799%; mean cost 0.0768R; harmonic-mean stop 1.302%; squeeze 1h cost 0.122R; mean hold 5.64. All confirmed.
- Reconciliation: unclipped mean market_r is 0.211 and clipped is 0.148. 0.039 - 0.148 = -0.109, which matches excess -0.110, while 0.039 - 0.211 = -0.172. 48 rows have |market_r| > 10, with a maximum of +182.9R (BIL, 3-cent stops, stop% 0.034%).
- Direct vol_20 test: vol_20 was recomputed as a cross-sectional percentile rank at the previous close from the setup-cache-v1 daily bars and joined to the 8,768 long A0 setups.
- rho(vol_rank, r_cost) = +0.069; rho(vol_rank, gross r) = +0.010 (session-bootstrap 90% CI [-0.009, +0.028]); rho(vol_rank, stop%) = +0.66; rho(stop%, gross r) = +0.002.
- Ran the reviewer's pm_quintile_beta.py on both frames: 2021-26 alpha by quintile -9.3/-19.7/-4.8/-0.8/0.0 bp, and 2017-21 -3.7/+3.5/-5.0/+9.9/+22.9 bp. Reproduced. Betas rise from 0.39 to 1.21, and the tightest quintile is 24% rates/credit/metals ETFs.
- ~/agentic-trader-research/setup-outcomes-v1-20260923-run2/: holds only JSON summaries, with no per-setup development frames.
- Web-verified references: Kaminski & Lo, JFM 18 (2014) 234-254; Novy-Marx & Velikov, RFS 29(1) 104-147; Moskowitz, Ooi & Pedersen, JFE 2012, 228-250.

**Bad references**

_none_

**Notes**

All cited code lines and doc sections exist and say what the finding claims. The numbers reproduce from the frozen frames:
- stop% quantiles
- 0.077R and 0.12R cost
- 5.6-session hold
- 8,119 robustness setups
- the quintile alphas
- the column non-reconciliation

The finding errs in four places.

1. **"Stop width has never been tested".** The October 8 funnel diagnostic's 4x4 stop/target grid did vary stop width. It covered 30 live candidates, in-sample and maturity-biased. The bracket spec cites it explicitly as the reason to exclude stop geometries. The finding should say "never tested on a powered population or in daily-ATR units".

2. **"SPY control is mishandled / uncosted".** Leaving SPY uncosted is a documented design choice. The real defect is the unclipped `market_r` in the descriptive column and in nothing else: excess is computed from clipped values, so B1 and the "trails SPY by 0.10-0.15R" statement stand. A cost-symmetric SPY at about 1 bp/side moves excess by only ~0.015R.

3. **The quintile evidence is confounded.** The narrative "worst at the tight end" is loose: Q2 is the worst, and 2017-21 is noisy. The quintile split is also a confounded cross-sectional association. It does not support the impact claim that widening stops would lift EV by several hundredths of R, beyond the purely mechanical cost dilution in R units. Per dollar of notional, cost is unchanged.

4. **The recommended "retained development frames" do not exist.** The setup-outcomes run2 directory keeps only JSON. The bracket-counterfactual frames plus setup-cache-v1 are enough to run the H2d gross-vs-cost check; I ran it, and it confirms the artefact: gross rho is about 0.01 on the long setups.

Recommendation 4 (exclude or cash-benchmark rates, credit and metals ETFs) is strongly supported:
- BIL produces the degenerate 0.03% stops.
- Non-equity names make up 24% of the tightest stop quintile.

All three literature references exist with correct bibliographic details, and they are relevant as used.

My check scripts are at:
- ~/agentic-trader-research/holistic-review-20261021/extras/factcheck-c11/check1.py
- ~/agentic-trader-research/holistic-review-20261021/extras/factcheck-c11/vol20_check.py

### Value review — P1, now (days)

**Reasoning**

This finding mixes one cheap, time-critical check with one real but limited experiment and some low-value extras. Fund them separately.

What the code confirms:
- The native stop is max(swing, 1.5×ATR14 of 4h bars). See agentic_trader/agent/evaluator.py:207-214, which reads min_stop_atr_multiple from config/config.yaml:275, and agentic_trader/screeners/strategies.py:163.
- Cost is charged in R with no floor: r_cost = r − 2·bps·entry/risk_unit (agentic_trader/research/setups/labels.py:144).
- market_r carries no cost (its docstring, research/setups/outcomes.py:198, says "Uncosted") and is never clipped. Only r, r_cost and excess_r are clipped (research/setups/bracket_counterfactual.py:172-177).
- No arm varies the stop. BracketArm carries only target_r and trail (labels.py:89-97).
- The same tight unit is not used in the pooled cubes. Those place stops at 2× ATR14 from the eligibility panel (research/pooled/cube.py:284; stop_atr_multiple 2.0 in config/research/pooled/*.json). So this finding explains nothing about the 64 null pooled results. Its scope is the native setup funnel: the cards themselves, the setup study and card_policy.

1) Re-run H2d/H2h on gross r before reading the 10-21→11-04 window: P0, hours of work, worth doing today.
- The setup runner already labels every setup at zero cost as well as primary cost (research/setups/runner.py:337-354). Gross r is in the retained frames, so the re-run is essentially one more call through _h1_h2 (study.py:502-504) with 'r' in place of 'r_cost'.
- vol_20 is the only feature lead left (docs/setup-outcomes-2026-09-23.md, Hypotheses: ρ +0.049). Its prospective read opens today (docs/alpha-roadmap.md:630).
- If the lead is a 1/stop% cost artefact, the window read is already settled. If it survives on gross r, it gains credibility.
- The gross-r and bps-charged reads must be added to the declaration before 11-04, or this becomes outcome switching.

2) Stop-width construction test: P1, next few weeks, and the only route this finding offers to better native cards.
- The median cost is 0.077R per setup, against +0.039R net and +0.116R gross for A0 (docs/bracket-counterfactual-2026-10-18.md, Descriptives). Cost dominates the unit, and the October 18 'bracket closed' verdict never varied the stop.
- Be sober about the upside. Widening the stop recovers about 0.03-0.04R of cost, against a −0.110R gap to SPY. The submitter's own quintile table puts the widest-stop quintile at 0.0 bp beta-adjusted alpha in 2021-26.
- So the realistic result turns cards from negative alpha into roughly zero alpha. It does not make them high-quality cards.
- It is still the cheapest lever left on the only funnel that actually produces cards.

3) Smaller items:
- Costing the SPY control at about 1 bp per side moves excess by only about 0.015R. Fixing the clip mismatch is a one-line reporting change. Neither changes any conclusion, so fold both into the step-2 PR.
- The risk-unit floor should be applied in research reports, plus an added %-return/beta column in cards outcomes. Do not redefine R in card_policy or in historical evidence.
- Drop item 5. Alpaca paper fills are simulated, so 'measured shortfall' from paper arrival quotes would understate real cost and add nothing.
- Item 4 (benchmarking rates, credit and metals ETFs to cash) is correct but small. Declare it as a stratum of the step-2 test, not as a separate workstream.

**Risks**

- **Outcome switching:** redefining vol_20's outcome on the day its prospective window opens looks like moving the goalposts unless both reads are declared, with hashes, before the 11-04 read. The two reads can then disagree, and that has to be handled too.
- **Overused window:** the stop-width test would reuse the 2021-26 development window for a third time (setup study, bracket counterfactual, now stop width). Only the B5/holdout confirmation is untouched, and it is single-use. Spending it on a construction tweak with a capped upside of about zero alpha has a real opportunity cost.
- **Confounded quintile evidence:** the 2017-21 wide-stop result (+22.9 bp) is cross-sectional selection: wide stops come from swing-defined or high-vol names, which is the same confound as vol_20. It is not a counterfactual on width and must not motivate the test's expected effect.
- **Smaller trades:** wider stops at a fixed per-trade risk budget roughly halve notional. Card dollar EV, the operator's perceived quality and SPY-beta dollars all fall. This interacts with book_sizing in enforce mode, the notional and class caps, and min_risk_reward. A per-dollar win can still feel like a loss per card.
- **No-lookahead:** daily ATR must come only from completed prior sessions at the 10:35/14:35 decision times. The live scan computes 4h ATR intraday, so the research and live geometry must stay identical.
- **Likely outcome:** another honest null. Budget for it as hygiene, not alpha discovery.

**Prerequisites**

- **Before the 11-04 read:** a written addendum to the vol_20/sector_rel_mom_60 prospective evaluation, declaring both the gross-r and the r_cost (or %-return) Spearman reads with their pass direction. Also confirm that the retained setup-study development frames contain the zero-cost 'r' column; runner.py:337-354 suggests they do.
- **For the stop-width test, extend BracketArm** (labels.py:89-97) to carry stop geometry, via a stop multiple and an ATR source, and recompute SetupLevels per arm. Daily ATR comes from the cached daily bars, causal at decision time.
- **Freeze a v2 protocol** that keeps B1-B4 and the unread B5 confirmation. The decisive statistic is beta-adjusted alpha per dollar net of cost, with volatility-normalised notional. Strata: equities, and rates/credit/metals ETFs benchmarked to cash.
- **Operator approval** before spending the holdout, as with earlier frozen tests.

**Better alternative**

Don't build a new study. Run stop width as version 2 of the existing bracket-counterfactual protocol: reuse its runner, its pairing by setup, B1-B5 and `cards outcomes`' descriptive arms, and add four stop-geometry arms (1×, 1.5×, 2× and 3× daily ATR, each with a 2R target). Most of the infrastructure exists, so this is days rather than a new M-sized lane.

Do the H2d gross-r re-run separately and first. It costs hours and the window opens today.

Before either, a zero-cost screen: take the retained development frames and compute the share of A0's gross-versus-net gap that comes from setups below a 0.75%-of-price stop. If that share is small, skip the stop-width test entirely.

---

<a id="c12"></a>

## C12 — R is not the live sizing unit: the $7,500 notional cap binds on ~84% of setups, so drawdown and macro de-risking are largely inert; size by volatility, not by equal R

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | sizing_portfolio | defect | high | S | partially_confirmed |

_lenses: portfolio, product_

### Claim (as consolidated)

calculate_dynamic_sizing caps every tier at min($7,500, remaining notional). It applies the drawdown × macro factor only to the base risk budget (position_sizing.py:92-117, 153-166).

On the replayed stop distribution (median stop 1.80%):
- 84% of base tiers and 99.9% of max tiers are notional-capped.
- Median live risk is about $135, not $250.
- The tiers collapse to Half plus 'Standard / Max' (:219-221).

De-risking barely binds. A 0.5 factor (HIGH stress, or about 4.5% drawdown) cuts base notional only 17% and max notional 0%, contradicting account-ledger.md:132.

The caps also conflict. Eight positions × $7.5k = $60k, but the equity class cap is the $40k code default, so the sixth full-size card is refused rather than shrunk.

Raising the cap to equal-R would lever the tight-stop, low-beta quintile. Per setup in 2021–26, dollar excess against same-notional SPY is:
- −$27.3 with equal-R;
- −$7.5 with the live caps;
- −$7.3 with equal-notional.

### Evidence

- agentic_trader/agent/position_sizing.py:92-117, 153-166, 219-221; agentic_trader/risk/capital.py:45-58
- config/config.yaml:3-8, 337-343; agentic_trader/config.py:393; agentic_trader/risk/rules.py:315-329
- docs/account-ledger.md:132
- Live risk map G1 (0.5 factor leaves 75 shares / $7,500)
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_frames.py, pm_stop_quintile.py, pm_haircut.py

### Recommendation

Ship behind a mode flag, with preview first:
- Set notional_i = min(k/σ_i, cap). σ comes from blended EWMA and 120-session split-adjusted daily volatility. Choose k so median notional stays near $7k.
- Keep the stop as bracket geometry and as a per-trade risk ceiling of $250 × dd × macro.
- Apply dd × macro to the volatility target and to the notional ceiling (one line at position_sizing.py:96). Add a test that a 0.5 factor halves every tier.
- Set max_equity_exposure consistent with the maximum number of positions, or cut the maximum to 5.
- When headroom is short, size down to the remaining class, stop-risk or volatility headroom instead of refusing, and journal which constraint bound.

Reject the equal-R option.

### North-star impact

Card sizes become comparable across names, the drawdown and stress levers actually reduce exposure, and research results map onto desk dollars.

### References

- Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), The impact of volatility targeting, JPM 45(1) — Volatility scaling stabilises risk per position
- Grossman & Zhou (1993), Optimal investment strategies for controlling drawdowns, Mathematical Finance 3(3) — Drawdown control must scale exposure itself
- Cederburg, O'Doherty, Wang & Yan (2020), On the performance of volatility-managed portfolios, JFE 138(1) — Treat volatility targeting as risk control, not as an alpha claim

### Fact check — partially_confirmed

**Corrected claim**

calculate_dynamic_sizing applies dd x macro to both risk budgets: the base budget ($250 x dd x macro, position_sizing.py:161) and the max-tier budget (budget.dollars = min(cash, equity) x 1% x dd x macro, capital.py:92; position_sizing.py:104). It does not apply the factor to the per-trade notional ceiling, min($7,500, remaining portfolio notional) (position_sizing.py:95-96). The finding's "only to the base risk budget" is wrong on this point.

On the replayed A0_live stop distribution (median stop 1.80%), the notional ceiling binds on:
- 84% of base tiers;
- 99.9% of max tiers.

The 500-share cap and integer flooring are ignored here, which is immaterial. Median live risk is about $135, and for capped setups the tiers collapse to Half plus 'Standard / Max' (:219-221).

A 0.5 factor (macro HIGH, or a 4.5% drawdown) cuts aggregate base notional only about 17% and max-tier notional about 0%. So the sizing effect of de-risking is weak. Three qualifications:
- macro HIGH/EXTREME still raise min R:R and suppress breakouts (macro.py:249-268);
- the aggregate stop-risk budget does scale with the drawdown factor (rules.py:270-277), although at about $135 per card it rarely binds before the class cap;
- the book-vol factor (enforce) is not scaled by dd/macro and has not bound so far (config.yaml:466).

docs/account-ledger.md:131-133 is literally true, because the multiplier is applied to every tier's risk budget. But the size reduction it implies mostly does not happen.

The cap conflict is accurate. max_concurrent_positions is 8 (config.yaml:6), but the equity class cap is the $40k code default (config.py:393, not overridden). So, in Alpaca, a sixth full-size card at $7,500 is refused by asset_class_notional in book_gates and admission (rules.py:314-329, 470-488). It is not shrunk, because sizing reads only total notional.

No per-name volatility sizing exists; SizingMode.VOLATILITY_TARGETED still sizes by stop distance (evaluator.py:252). The per-setup dollar-excess figures reproduce exactly: equal-R −$27.29, live −$7.45, equal-notional −$7.32. However, they are measured against beta-one same-notional SPY. The tight-stop quintile has beta 0.39 to 0.65, so part of equal-R's −$27.3 is beta shortfall in a rising 2021–26 market rather than alpha. Its beta-adjusted alpha is still negative (−9 to −20 bp in Q1–Q2), so the rejection of equal-R stands.

**Checked**

- agentic_trader/agent/position_sizing.py:64-117 (shared budget at :65-71, effective_adjustment at :92, notional ceiling at :95-96 not scaled by dd/macro, max_qty = min(500 shares, budget.dollars/unit risk, ceiling/entry) at :104-117)
- agentic_trader/agent/position_sizing.py:153-166 (static mode: base budget = $250 x dd x macro, clamped to max_qty)
- agentic_trader/agent/position_sizing.py:210-223 (a notional-capped base equals max_qty, so the tiers collapse to Half plus 'Standard / Max' at :219-221)
- agentic_trader/risk/capital.py:45-58, 80-93 (drawdown factor, and budget.dollars = capital x 1% x dd x macro, which also scales the MAX tier's risk budget)
- agentic_trader/agent/macro.py:249-268 (HIGH stress = 0.50 multiplier, which also raises min_rr and suppresses breakouts; EXTREME = 0.25)
- config/config.yaml:1-8 (cash 100k, max_notional 60k, max_concurrent_positions 8; no max_equity_exposure), :333-346 (static mode, $250 base, 1% cap, $7,500 notional cap, 3%/6% drawdown thresholds), :465-470 (book_sizing enforce, preview factors all 1.00)
- agentic_trader/config.py:385-398 (max_equity_exposure default 40000 at :393), :520-524 (drawdown_min_risk_multiplier 0.10), :1057-1060 and :1103-1114 (no env/YAML override of the equity class cap)
- agentic_trader/risk/rules.py:270-277 (aggregate_stop_risk does scale with drawdown_factor), :314-329 (asset_class_notional refuses), :470-488 (book_gates at scan includes asset_class_notional, so a card is refused and never shrunk)
- agentic_trader/agent/evaluator.py:252-266 (comment 'Both supported modes use stop-distance sizing': SizingMode.VOLATILITY_TARGETED does no volatility targeting)
- agentic_trader/execution/book_sizing.py:336-338 and agentic_trader/risk/book_vol.py (the book-vol budget = 0.8% x risk capital and is not scaled by dd/macro)
- docs/account-ledger.md:131-139
- Reran the reviewer's pm_frames.py, pm_stop_quintile.py, pm_haircut.py and pm_quintile_beta.py on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz (A0_live, n=8,768). Results: median stop 1.80%; base capped 83.9%; max capped 99.9%; median live risk $135; f=0.5 cuts base notional 17% and max 0%; dollar excess equal-R -27.29, live -7.45, equal-notional -7.32. Q1 tight-stop beta is 0.39 (0.63 for equities only) and Q5 is 1.21.
- scratchpad maps/risk.md G1 and maps/portfolio.md G1-G3 (the 'Live risk map G1' citation exists and matches sizing_probe.py)
- Web-verified Harvey et al. 2018 JPM 45(1):14-33; Grossman & Zhou 1993 Math Finance 3(3):241-276; Cederburg et al. 2020 JFE 138(1):95-117

**Bad references**

_none_

**Notes**

The core of the finding is right, and its numbers reproduce exactly from the reviewer's scripts on the bracket-counterfactual primary frame.

Details that are wrong or need qualifying:
1. "Applies the drawdown x macro factor only to the base risk budget" misdescribes the code. The max tier's risk budget is also scaled (capital.py:92 via position_sizing.py:104). What is not scaled is the notional ceiling at :96.
2. "Largely inert" holds for per-card sizing only. Macro HIGH/EXTREME still raise min R:R and block breakouts. The aggregate stop-risk cap halves with the drawdown factor, but it rarely binds at about $135 per card under the 5-card class cap.
3. "Contradicting account-ledger.md:132" overstates it. The doc says the multiplier "applies to every sizing tier", which is mechanically true; the effect, not the application, is missing.
4. The finding omits book_sizing (enforce since 2026-10-20). It is a portfolio daily-vol factor ≤ 1, so it does not provide the per-name volatility sizing the finding says is missing. Its budget is not scaled by dd/macro, and in preview all six factors were 1.00 (config.yaml:466). The omission does not undercut the finding, but it is a counterpoint worth naming.
5. The −$27.3 / −$7.5 / −$7.3 comparison uses beta-one SPY at the same notional. It therefore mixes beta shortfall from low-beta tight-stop names with alpha.
6. The proposed min(k/σ, cap), with median about $7k and the cap kept, is structurally close to today's min($250/stop, $7,500), since stops are ATR-floored. Its main real changes are a different vol estimator decoupled from swing-low stops and scaling the ceiling by dd x macro. The replay does not show it improving dollar excess over the live caps (equal-notional −$7.32 vs live −$7.45).

A PORTFOLIO_CASH or MAX_NOTIONAL_EXPOSURE override set in the environment could change the arithmetic. Per the ground rules I did not read .envrc to check. The equity class cap has no env override.

All three references exist with the cited venue, volume and issue, and support their stated relevance.

### Value review — P1, now (days)

**Reasoning**

Fund the defect fix now. Defer the volatility-targeting redesign. The two halves are worth very different amounts.

The defect is real and the code confirms it:
- The drawdown × macro factor reaches only the risk budget (`risk/capital.py:80-93`) and the base budget (`agent/position_sizing.py:153-166`). The notional ceiling, `min(max_trade_notional_cap, remaining)` at `position_sizing.py:96`, is never scaled.
- With $250 base risk and a $7,500 cap (`config/config.yaml:337-343`), any stop tighter than 3.33% is notional-capped. Below about a 1.67% stop, a 0.5 factor changes nothing. The max tier ($1,000 × factor) stays capped in almost every case (maps/risk.md G1, sizing_probe).
- The docs promise the opposite. `docs/account-ledger.md` ("Alpaca scans require fresh reconciled risk…") says the drawdown multiplier applies to every sizing tier and the hard per-trade cap. CLAUDE.md contract 1 lists drawdown among the entry-risk rules.
- The caps also conflict. `max_concurrent_positions: 8` × $7,500 = $60k (`config.yaml:4-6`), but `max_equity_exposure` falls back to the $40k code default (`config.py:393`). `asset_class_notional` (`risk/rules.py:315-329`) therefore refuses the sixth full card instead of shrinking it.
- The north star explicitly asks for "appropriate risk controls … based on existing positions". A drawdown lever that does not cut size is a broken control, not a tuning matter. That is why this is P1.

Why not P0, and why defer volatility targeting:
- This is a paper account, so an inert lever has cost nothing yet.
- The finding's own numbers show sizing is not where the problem is. Dollar excess per setup against SPY is −$7.5 at the live caps and −$7.3 equal-notional. Re-weighting a stream with no edge moves variance, not expectancy. After 64 null formula-cohort pairs and the closed bracket (`docs/bracket-counterfactual-2026-10-18.md`), entry-side alpha is still what limits the north star.
- `k/σ` sizing adds a second volatility scaler on top of `book_sizing`, which has been in enforce since 2026-10-20 (`config.yaml:465-470`). It also needs split-adjusted σ, but BookSizer reads raw bars (`execution/book_sizing.py:222`, maps/portfolio.md G7). Both points need design work before this is really "S" effort.

Rejecting equal-R is correct. Equal-R levers the tight-stop, low-beta quintile and gives the worst dollar excess (−$27.3).

**Risks**

- **The "one-line" fix is not one line.** CLAUDE.md requires one implementation per entry-risk rule. If only `position_sizing.py:96` scales the notional ceiling:
  - admission's `per_trade_notional` (`risk/rules.py:212-217`) and the replacement-quantity cap (`agent/copilot.py:3440`) still allow a full $7,500;
  - a card would show a smaller size than an explicit operator quantity can reach;
  - tap-time and admission could disagree.

  The scaled ceiling belongs in `risk/` (for example on `RiskBudget`) and must be read by all three paths.
- **Stacking with book_sizing.** Per-name volatility targeting combined with the enforced portfolio-volatility factor can double-count and shrink cards twice. Keep one volatility estimator, in BookSizer's covariance or `covariance.py`.
- **Splits distort σ.** Raw-bar σ turns a split inside the 120-session window into a fake −50% return. That under-sizes the name, or makes the context unavailable.
- **The class-cap change is a real exposure increase.** Raising `max_equity_exposure` to $60k adds about 50% gross equity exposure. Cutting max positions to 5 reduces card throughput. Either way it is an operator policy decision, not a bug fix.
- **"Size down to headroom" changes behaviour.** Cards get smaller where they used to be refused, so more small positions get admitted. Every tier must be re-checked at admission (maps/portfolio.md G8), and the binding constraint journaled.
- **Measurement break.** Changing the sizing unit mid-stream breaks comparability of `cards outcomes` dollar statistics, so the switch date needs a provenance marker.

**Prerequisites**

- Decide where the scaled ceiling lives. `risk/capital.py` should expose `notional_ceiling = max_trade_notional_cap × dd × macro`, and three paths must read it:
  - `calculate_dynamic_sizing`;
  - `per_trade_notional` at admission;
  - `_capped_replacement_quantity`.
- Add a regression test that a 0.5 factor halves (±1 share) every tier at a 1.5% stop and a 4% stop.
- Get an operator decision on the cap conflict: set `portfolio.max_equity_exposure` in `config/config.yaml` to match 8 × $7,500, or lower `max_concurrent_positions`.
- For the deferred volatility-targeting half:
  - fix BookSizer to split-adjusted bars first (or mask action dates with the captured corporate actions);
  - read the real stop% distribution of sent cards from the journal, to confirm the replayed 84% capped share holds live;
  - run the desk-replay dollar curve (maps/portfolio.md rec 9), so any change in sizing unit is judged in desk dollars against SPY.

**Better alternative**

Split the finding. Ship the cheap part now:

1. Scale the notional ceiling by dd × macro in `risk/`, consumed by sizing, admission and replacement, with the halving test.
2. Make the caps consistent through config (`max_equity_exposure` or `max_concurrent_positions`).
3. Optionally, shrink to the remaining class headroom instead of refusing, journaling the binding rule.

That fixes the broken control and the documented contract in roughly a day, with no new estimator. Do not build standalone `k/σ` sizing. Fold per-name volatility normalisation into the existing BookSizer workstream: same covariance, split-adjusted, one scaler. Do it only after the desk replay shows the sizing unit matters in dollars, or once an entry-side signal exists whose edge depends on sizing. Until then, alpha discovery is the higher-leverage use of the effort.

---

<a id="c19"></a>

## C19 — The pending insider campaigns do not test Cohen-Malloy-Pomorski as cited, and the short-volume lane measured a different variable from the one its cited papers studied

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | research_methodology | defect | high | S | partially_confirmed |

_lenses: literature_

### Claim (as consolidated)

Insider lane:
- insider_opp_buy_rel marks a purchase as routine when the same owner bought in the same calendar month one year earlier (insider_fields.py:26-27, 230-233). Owners with no purchase history therefore default to 'opportunistic'.
- insider-v1.json:204-206 cites CMP.
- CMP actually classify each insider annually. They require at least one trade in each of the three prior years, call an insider routine after the same month in at least three consecutive years, and exclude unclassifiable insiders.
- CMP's opportunistic long-short earns 82 bp/month value-weighted (t 2.15) and works in both size halves.
- Lakonishok-Lee place the information in purchases, in small firms, over 6–12 months. insider-v1 instead uses a 20-session daily decile.
- A null on v1 therefore says nothing about CMP.

Short-volume lane:
- short-v1 cites BJZ 2008, which used NYSE proprietary order data, and DLW.
- It actually reads FINRA's CNMS off-exchange files (short_volume.py:42). FINRA says these are not consolidated and include dealers' facilitating short sales: 20% against 10.7% in FINRA's own example.

Events lane: quiet_momentum cites Jegadeesh-Titman for a 20-session return, but a 20-session return is the short-term reversal signal (see C47).

### Evidence

- agentic_trader/research/pooled/insider_fields.py:26-27, 230-233
- config/research/pooled/insider-v1.json:204-206 (cites CMP); bracket 20 sessions
- config/research/pooled/short-v1.json:156-166; agentic_trader/research/short_volume.py:42; docs/alpha-pooled-mining.md:1040-1041, 1223-1235
- config/research/pooled/events-v1.json:186-188
- docs/alpha-roadmap.md:627 (insider campaigns pending approval)

### Recommendation

Before approving either insider campaign, choose one of two paths:
- Freeze insider-v2 with CMP's rule: owner-level annual classification with a three-year history, unclassifiable insiders excluded, open-market P purchases only. Make cohort v3 primary. Use event-entry selection: enter on the first session after the purchase becomes visible, skip names already held, and use non-opportunistic purchasers that session as the control. Declare a horizon-matched hold.
- Or record v1 as a 'one-year routine filter, not CMP' and lower its prior.

Make a literature-fidelity table mandatory before any protocol SHA is pinned. It sets the paper against the implementation for: variable, source, population, horizon, weighting and sign, and lists each deviation.

Revisit short flow only with exchange-reported short volume or with twice-monthly short interest.

### North-star impact

Keeps the next operator approvals, and possibly the lane-wide confirmation window, from being spent on mis-specified tests. A CMP-faithful insider leg is one of the few one-month, long-only literature effects documented in large caps.

### References

- Cohen, Malloy & Pomorski (2012), Decoding Inside Information, JF 67 — Routine/opportunistic definition; opportunistic long-short 82 bp/month (https://www.nber.org/papers/w16454)
- Lakonishok & Lee (2001), Are Insider Trades Informative?, RFS 14(1) — Information is in purchases, concentrated in smaller firms, over 6–12 months
- Boehmer, Jones & Zhang (2008), Which Shorts Are Informed?, JF 63(2) — Built on NYSE order data, a different variable from FINRA TRF short volume
- FINRA Information Notice 5/10/19, Understanding Short Sale Volume Data — Daily file is off-exchange only and includes facilitating short sales (https://www.finra.org/sites/default/files/2019-07/information-notice-051019.pdf)

### Fact check — partially_confirmed

**Corrected claim**

The core is right: insider-v1 is only a loose proxy for CMP, and short-v1 cites papers built on different short-sale data. Four details need correcting or softening.

(1) The one-prior-year same-month rule is not, by itself, a departure from CMP. The design spec acknowledges the reduction (docs/superpowers/specs/2026-10-20-pooled-insider-design.md:155: "reduced to one prior year because the data start in 2015"). CMP's footnote 9 also reports similar magnitude and significance for one- to five-year same-month back-windows. The departures that matter are these:
- There is no history requirement and no exclusion of insiders who cannot be classified, so first-time and sparse buyers count as opportunistic (insider_fields.py:230-233, 276).
- The routine set is built from purchases only, by filing month. CMP uses any trade, by trade month.
- Classification is per trade on a rolling basis. CMP classifies each owner once a year.
- The test design differs: a daily top decile of 63-session opportunistic dollars over 20-session dollar volume, held under a 2-ATR stop / 3R target bracket for at most 20 sessions (insider-v1.json:11-17, 266-278, 315-318). CMP measures next-month returns on a long-short portfolio, and its 82 bp/month (t 2.15) is a value-weighted five-factor alpha.
So a v1 null is a weak, low-fidelity test of CMP, not one that "says nothing" about it.

(2) Short-volume lane: confirmed that it reads FINRA CNMS off-exchange files (short_volume.py:42) while citing BJZ 2008 (NYSE proprietary order data) and DLW 2009. The off-exchange-only limitation is already documented (docs/alpha-pooled-mining.md:1039-1043; short-volume design spec non-goal), but the docs never reconcile it with the cited papers and never mention the inflation from facilitation short sales. One correction: FINRA's 20% vs 10.7% example illustrates the lack of consolidation with exchange (NYSE) volume, not the facilitation issue.

(3) Events lane: confirmed. quiet_momentum is a 20-session return attributed to Jegadeesh-Titman (whose formation periods run 3-12 months). That horizon is the short-term reversal horizon, and the realised t of -3.34 (docs/alpha-pooled-events-v1-2026-10-15.md:70) is consistent with reversal.

(4) Roadmap: the roadmap line 627 confirms the insider campaigns are pending.

**Checked**

- agentic_trader/research/pooled/insider_fields.py:26-27 (docstring: a purchase is routine if the same owner filed a purchase in the same issuer and the same calendar month of the previous year)
- agentic_trader/research/pooled/insider_fields.py:230-233 (_routine builds the month set from the purchases passed in, keyed on filing month) and :276 (opportunistic = buy & ~routine, so an owner with no prior-year purchase counts as opportunistic)
- agentic_trader/research/pooled/insider_fields.py:280-290 (buy_window/net_window sums)
- config/research/pooled/insider-v1.json:11-17 (bracket: 2 ATR stop, 3R target, max_hold_sessions 20), :204-209 (opportunistic_buying cites CMP), :266-278 (buy_window 63, net_window 126), :315-318 (selection top_fraction 0.1)
- config/research/pooled/cohort3-insider-v1.json:206 (same CMP rationale)
- docs/superpowers/specs/2026-10-20-pooled-insider-design.md:155 (says the CMP rule was 'reduced to one prior year because the data start in 2015')
- config/research/pooled/short-v1.json:155-169 (light_shorting / light_shorting_week cite BJZ 2008 and DLW 2009)
- agentic_trader/research/short_volume.py:42 (FINRA_DAILY_URL = CNMSshvol)
- docs/alpha-pooled-mining.md:1039-1043 (off-exchange-only caveat) and :1223-1235 (insider-v1 table)
- docs/superpowers/specs/2026-10-20-pooled-short-volume-design.md:18-28 (cites BJZ/DLW as the prior; off-exchange-only non-goal; 'No exchange short data is sought')
- docs/alpha-pooled-short-v1-2026-10-20.md:110-113
- config/research/pooled/events-v1.json:186-191 (quiet_momentum = ts_sum(returns, 20), cites Jegadeesh and Titman 1993)
- docs/alpha-pooled-events-v1-2026-10-15.md:70 (quiet_momentum t -3.34, negative)
- docs/alpha-roadmap.md:627 (insider checks and campaigns pending, campaigns on operator approval)
- NBER w16454 abstract and full text (extracted PDF): base rule requires at least one trade in each of the 3 preceding years; routine = same calendar month for at least 3 consecutive years; classified at the start of each calendar year; Table I universe limited to classifiable insiders; VW opportunistic L/S five-factor alpha 82 bp/month, t=2.15; Table IX: opportunistic trades predict returns in both top and bottom size halves; footnote 9: one- to five-year same-month back-windows give similar magnitude and significance
- FINRA Information Notice 5/10/19 PDF pp. 2-3: data limited to publicly disseminated trades (facilitation short sales included, offsetting purchase not), not consolidated with exchange data, worked example 20% vs 10.7% when NYSE volume is added, not short interest
- Lakonishok & Lee 2001 RFS 14(1) abstract via RePEc/search: information in purchases, sales uninformative, predictive ability concentrated in smaller firms

**Bad references**

_none_

**Notes**

All four references exist and support their use:
- **CMP:** 82 bp VW five-factor alpha with t=2.15; the three-preceding-year and three-consecutive-same-month rule; annual classification; both size halves (Table IX).
- **FINRA notice:** off-exchange only, not consolidated, includes facilitation short sales, the 20%/10.7% example.
- **Lakonishok-Lee:** purchases informative, sales not, concentrated in smaller firms.
- **BJZ 2008:** NYSE proprietary order data; checked from my own knowledge, not fetched.

The finding understates two pieces of counter-evidence:
- The insider design spec already flags the one-year reduction of CMP's rule (docs/superpowers/specs/2026-10-20-pooled-insider-design.md:155).
- CMP's own footnote 9 says a one-year same-month back-window gives similar results. This weakens "says nothing about CMP" but leaves the missing exclusion of unclassifiable insiders and the horizon/portfolio mismatch intact.

Practical constraint: filings start 2015q1 and discovery starts 2016-08 (insider-v1.json:276, 335-337). A three-year history requirement would leave few classifiable owners before about 2018, which bears on the recommended v2.

The "20-session daily decile" description is approximately right: a top_fraction of 0.1 each session with a bracket held at most 20 sessions. The field windows themselves are 63 sessions (buy) and 126 sessions (net).

Minor: the north_star_impact calls CMP a "long-only" effect, but CMP's headline 82 bp is long-short (opportunistic buys minus opportunistic sells).

Extracted paper text is at ~/agentic-trader-research/holistic-review-20261021/cmp/cmp.txt.

### Value review — P1, now (days)

**Reasoning**

Fund this, in two parts: a cheap gate now and a re-scoped build later.

**The defect is real, and I checked it in the code.**
- `_routine` (agentic_trader/research/pooled/insider_fields.py:230-233) calls a purchase routine only if the same owner bought in the same calendar month exactly one year earlier. The module docstring (:26-27) and docs/alpha-pooled-mining.md:1197 say the same.
- So an insider with no purchase history counts as 'opportunistic', and nobody is ever excluded as unclassifiable.
- config/research/pooled/insider-v1.json:204-206 still credits this rule to Cohen, Malloy and Pomorski (CMP). CMP use a three-year history and drop insiders they cannot classify.
- `net_buying` cites Lakonishok-Lee's six-month net purchase ratio (insider-v1.json:186-192). It is scored as a daily decile with a 20-session ATR bracket (`max_hold_sessions: 20`, insider-v1.json:11-14), which does not match LL's horizon.
- The 20-session hold does roughly match CMP's one-month horizon. The problem with the CMP leg is the classification rule, not the hold.

**The timing makes the cheap part worth doing now.**
- docs/alpha-roadmap.md:627 says checks A and C and both insider campaigns are pending operator approval.
- The lane's habit, in every recent result doc, is to record a null as 'fixed set CLOSED on this window'.
- If v1 runs unannotated, a null on a mis-specified rule closes off one of the few literature effects that pays over about a month (CMP).
- A spurious v1 finalist would also use up the one-shot lane-wide confirmation window on the wrong test.
- The fix costs hours: record v1 as a 'one-year routine filter, not CMP' with a lower prior, and declare before approval that a v1 null does not close CMP.

**Make the literature-fidelity table mandatory before any protocol SHA is pinned.**
- After 64 null formula-cohort pairs, the lane's credibility depends on each null meaning what its citation says.
- This review has already found three mismatches between citation and implementation:
  - CMP in the insider lane.
  - Boehmer-Jones-Zhang, who used NYSE order data, while short_volume.py:42 reads FINRA's off-exchange CNMS files. docs/alpha-pooled-mining.md:1040-1041 discloses the source, but not that it differs from the cited papers.
  - `quiet_momentum`, which credits Jegadeesh-Titman for a 20-session return (events-v1.json:186-188). That horizon is the known one-month reversal. Its t of −3.34 on cohort v2, which did not replicate at about −1.7 on v3, is consistent with that known reversal.

**The build should be re-scoped, not adopted as written.**
- Building insider-v2 inside the pooled lane would need a new event-entry selection mode and a new control. That is effort M, not S.
- The pooled lane's daily in-condition decile is also a poor fit for sparse events.
- The a priori catalog already has event-driven runners with controls (agentic_trader/research/apriori/pead_events.py, exdiv_study.py).
- It is also the only path that has produced a live card source: pead v2 LONG became `pead_long` (docs/apriori-alphas.md table and 'Part 2: live paper probe').
- A CMP-faithful entry there gives one confirmatory test and a direct route to a capped probe, and so to Telegram cards.

**Keep expectations modest for the north star.**
- CMP opportunistic buys are sparse once insiders need three years of history to be classified.
- Even a pass would add a trickle of cards, not a steady stream, against a hurdle where the native entries already trail SPY by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md).

**The short-volume and events parts are doc corrections on lanes that are already closed (P3).**
- Add a caveat that the short-v1 null does not test BJZ or Diether-Lee-Werner.
- Relabel `quiet_momentum` as a short-term reversal measure.
- Revisit short flow only with exchange-reported short volume (for example Cboe's free daily files) or FINRA's twice-monthly short interest.

**Risks**

**Decay of the effect**
- CMP's sample ends around 2007. Published anomalies typically lose about half their effect after publication (McLean-Pontiff).
- In the large caps of cohort v2, opportunistic buys may now be weak.

**Long-only versus long-short**
- The 82 bp/month figure comes from a long-short portfolio. The system can only go long, under a bracket, measured against SPY, so the long leg alone may fall short of the hurdle.

**Filing lag**
- Form 4 is filed up to two business days after the trade, and visibility is read at T+1 10:35 ET (insider-v1.json:303).
- Much of the abnormal return around filing may already be gone. An event-entry design must measure from filing visibility, not from the trade date.

**Sparsity and power**
- Requiring a trade in each of the three prior years leaves most purchasers unclassifiable. Events per year in a 353- or 1,000-name cohort may be too few for the P1–P4 rules, so expect an underpowered null.

**Mid-sample regime change**
- The 2023 Rule 10b5-1 amendments, and the AFF10B5ONE flag that exists only from April 2023, change the mix of routine and opportunistic trades partway through the sample.

**Forking paths**
- If v1 runs before v2 is frozen, v2 is a second look and must be declared as one (the existing `second_look` convention), with its own window.

**Process**
- The fidelity table can turn into box-ticking unless a reviewer must sign off on each listed deviation before the SHA is pinned.

**Prerequisites**

**Already in place**
- The digest-pinned SEC Form 3/4/5 acquisition starts in 2015. That gives a three-year owner history from about 2018, which covers the bar window.
- `owner_cik` is already on each `InsiderTransaction`.
- The a priori event-study framework exists: pead_events, exdiv_study, the P1–P4 statistics and the probe path.

**Needed**
- An operator decision before approving the v1 campaigns: either annotate and lower the prior for v1, or hold it.
- A short written spec for an `insider-cmp` v1 a priori entry, covering:
  - CMP's owner-level annual classification, unclassifiable insiders excluded, open-market code-P purchases only.
  - Entry on the first session after filing visibility.
  - A same-session control of non-opportunistic purchasers, plus a same-name placebo.
  - A horizon-matched session-count exit and a fresh one-use window.
- A label-blind count of CMP-classifiable opportunistic purchase events per year in cohorts v2 and v3. It reads the cached filings only, no prices, and checks power before building.
- A fidelity-table section added to the spec template, listing variable, source, population, horizon, weighting, sign and deviations.

**Better alternative**

**Now, at about an hour's cost**
- Add a fidelity note to the insider-v1 spec and to docs/alpha-roadmap.md before the operator approves anything. State that `opportunistic_buying` is a one-year same-month filter, not CMP, and that `net_buying` runs on a 20-session bracket rather than LL's six to twelve months.
- Pre-register that a v1 null does not close the insider source.
- Then let v1 run cheaply as planned.

**Then**
- Run the label-blind event count.
- If power is adequate, implement CMP as an a priori catalog entry, reusing the pead_events/exdiv event-study machinery, instead of an insider-v2 that needs a new event-entry selection mode in the pooled lane.
- The a priori path is the only one that has produced a live probe (`pead_long`), so a pass reaches Telegram cards directly.

**Leave alone**
- Drop the short-volume follow-up beyond a caveat in the results doc.

---

<a id="c20"></a>

## C20 — Point-in-time data the desk already downloads every day is thrown away: the asset master, analyst consensus and headline revisions

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | data | gap | high | S | partially_confirmed |

_lenses: data_

### Claim (as consolidated)

Three point-in-time sources are fetched and then discarded:
- Alpaca's full active asset list (14,404 rows) is read once per New York date and kept only in memory (dynamic_universe.py:345-366).
- Nasdaq earnings pages, which carry epsForecast, noOfEsts and BMO/AMC timing, are memory-only (earnings.py:185-240).
- NewsItem drops updated_at (news.py:33-48).

These are exactly the gaps the desk's own assessments flag:
- point-in-time membership after 2022;
- consensus history, described as 'unavailable without a paid vendor';
- announcement timing, which is 'time-not-supplied' on 100% of historical Nasdaq rows (measured).

The only point-in-time record that accrues today is the per-scan dynamic_universe_built journal.

### Evidence

- agentic_trader/screeners/dynamic_universe.py:345-366
- agentic_trader/agent/earnings.py:185-240 (parses time, epsForecast, noOfEsts at :138-148; memory cache only)
- agentic_trader/data/news.py:33-48
- docs/survivorship-spike-2026-10-20.md 'Answer'; docs/alpha-data-augmentation-2026-10-18.md:66-70
- agentic_trader/data/corporate_actions_worker.py:44-68 (pattern to reuse)

### Recommendation

Add a daily pit-snapshots worker on the corporate-actions pattern: once per New York date, outside scan windows. For each source it writes one gzip file with a SHA-256 and receipt time:
1. The Alpaca asset master, all statuses, with asset UUIDs, so renames and delistings show up as diffs.
2. Nasdaq pages for D−5 through D+30: consensus vintages, and BMO/AMC as first announced.
3. Market-wide structural corporate actions.
4. Option-chain summaries (C33).
5. The VIX, FRED and yield observations the regime gates use.

This is a few MB per day. Research reads the files with digest pinning.

### North-star impact

These inputs cannot be bought later, and every day not captured is lost. Within months they yield a free forward membership record, an analyst SUE for PEAD and a history of attention names.

### References

- Livnat & Mendenhall (2006), J. Accounting Research — Analyst-based SUE drifts more than time-series SUE, so consensus vintages are worth capturing

### Fact check — partially_confirmed

**Corrected claim**

The core gap is real: no daily, persisted point-in-time capture exists for the asset master, the Nasdaq consensus vintages or news revisions. Several details are wrong or overstated.

1. **Asset master.** The daemon's dynamic universe reads only ACTIVE assets once per NY date, keeps them in memory and drops the UUID (dynamic_universe.py:345-366). However, a retention path already exists: the manual `alpha universe-snapshot` (data/equity_metadata.py:30-63). It saves the raw /v2/assets page for all statuses, with UUIDs, plus the Nasdaq symbol directories, each with SHA-256 and receipt times. It has simply never been scheduled; one run exists, from 2026-10-15. The recommendation should schedule or reuse EquityMetadataSource rather than build a new asset capture.
2. **Earnings.** The blackout path keeps only symbol, date and timing for today..today+blackout_days, in memory (earnings.py:205-240, 51-54). epsForecast and noOfEsts are discarded even in memory. The live PEAD drift path journals some of this data in its pead_decision events (earnings_drift.py:148, copilot.py:2026-2050): the page body hash and the derived surprise for LONG events of the D-2 report date. It does not journal raw consensus, estimate counts, timing, or any pre-announcement vintage.
3. **News.** NewsItem drops updated_at (news.py:33-48). Headlines themselves (id, created_at, source, text) are journaled per scanned candidate (news_context.py:144-174), so only revision timing and history are lost. Alpaca news remains re-fetchable historically, so 'cannot be bought later' holds only for the revision history.
4. **'The only PIT record that accrues today is dynamic_universe_built' is false.** Other PIT records already accrue: pead_decision, the daily corporate-actions capture, per-scan news blocks, and the regime block with VIX and macro_stress (regime.py:76-100). The last of these partly covers recommendation item 5.
5. **Timing.** 'time-not-supplied on 100% of historical rows (measured)' is supported only by the PEAD design spec's 'historically always time-not-supplied'. No measured result doc backs it.

**Checked**

- agentic_trader/screeners/dynamic_universe.py:345-366: assets() reads AssetStatus.ACTIVE raw rows once per NY date into _assets_cache, keeps only symbol/name/class/exchange/status/tradable and drops the asset id (UUID). Confirmed.
- agentic_trader/agent/earnings.py:138-148: parse_calendar_payload reads time, epsForecast and noOfEsts. Confirmed.
- agentic_trader/agent/earnings.py:185-240: the blackout path's _request_date keeps only EarningsEvent(symbol, date, timing) (earnings.py:51-54) in an in-memory TTL cache. eps_forecast and n_estimates are dropped even in memory. next_earnings fetches only today..today+horizon_days (earnings.py:270-289).
- agentic_trader/agent/earnings.py:243-268 and research/apriori/pead_live.py:123: reported_rows hashes the body (body_sha256) but does not store it.
- agentic_trader/screeners/earnings_drift.py:135-148 and agent/copilot.py:2012-2050: the pead_decision journal persists page_sha256, the report_date and LONG event documents (EVENT_COLUMNS at research/apriori/pead_events.py:56-72: surprise_pct and surprise_pct_reported, but not eps_forecast, n_estimates or timing). This is counter-evidence that partly contradicts 'the only PIT record'.
- agentic_trader/data/news.py:33-48: NewsItem has extra='ignore' and no updated_at field. Confirmed. research/news.py:150-158 also caches only the normalised items, so updated_at is dropped there too.
- agentic_trader/agent/news_context.py:144-174: each scan journals per-candidate headlines (id, created_at, source, headline) into decision_provenance / scan_candidates_ranked. Headlines are therefore not entirely discarded; only updated_at and revision history are.
- agentic_trader/data/equity_metadata.py:30-63 and cli/commands/alpha_universe.py: the manual `alpha universe-snapshot` already retains the raw /v2/assets page with no status filter (all statuses, UUIDs) plus the nasdaqlisted/otherlisted directories with SHA-256 and receipt times. Only one run exists (~/agentic-trader-research/equity-snapshot-20261015), and scripts/launchd.sh has no schedule for it.
- agentic_trader/agent/copilot.py:2184-2228: dynamic_universe_built journals screener members/scanned/excluded, not the asset master.
- agentic_trader/data/corporate_actions_worker.py:44-68: the daily capture pattern exists. It also journals a PIT record (corporate-actions/capture), but only for desk symbols.
- agentic_trader/agent/regime.py:76-100: the regime block already journals VIX, vix_regime and macro_stress per scan.
- docs/survivorship-spike-2026-10-20.md 'Answer': supports membership being missing after ~2022 and the 14,404 active rows. It also notes the universe-snapshot directory snapshots as a forward record.
- docs/alpha-data-augmentation-2026-10-18.md:66-70: supports 'consensus history stays unavailable without a paid estimates vendor'.
- docs/superpowers/specs/2026-09-25-apriori-pead-study-design.md:84-85 ('time is ignored (historically always time-not-supplied)'). This is the only doc support found for the timing claim. No result doc reports a measured 100%.
- WebSearch: Livnat & Mendenhall (2006), JAR 44(1):177-205. Confirms that analyst-based (I/B/E/S) SUE drift is significantly larger than time-series SUE drift.

**Bad references**

_none_

**Notes**

All cited file:line ranges exist and say what the finding attributes to them. The Livnat & Mendenhall (2006) reference is valid and relevant: it is JAR 44(1):177-205, and it finds analyst-based SUE drift larger than time-series SUE drift. Note that the desk's PEAD already uses analyst-based surprise from Nasdaq's epsForecast. The real gain from capturing vintages is removing the later-revision contamination flagged in alpha-data-augmentation-2026-10-18.md:68-70, not switching from time-series to analyst SUE.

The main misdescriptions are threefold. The finding omits the existing `alpha universe-snapshot` raw asset-master retention. It omits the pead_decision and news-headline journals. It overstates 'the only point-in-time record'.

The cited docs/alpha-data-augmentation-2026-10-18.md:66-70 supports consensus history being unavailable without a paid vendor. The survivorship doc supports post-2022 membership being absent from Alpaca. That doc also names EDGAR Form 25/15 as a free, retrospectively acquirable membership route, which weakens the claim that missed membership days 'cannot be bought later'.

No repo files were modified and no state-changing commands were run.

Sources: [SUFE academic newsletter summary of Livnat & Mendenhall 2006](https://academicnewsletter.sufe.edu.cn/info/413468)

### Value review — P1, now (days)

**Reasoning**

Fund a narrowed version now. Most of the five-source bundle is not perishable, so it should be cut.

What the code does today:
- The Alpaca asset master is cached in memory once per New York date and never persisted (agentic_trader/screeners/dynamic_universe.py:344-366).
- NasdaqEarningsCalendar parses epsForecast, noOfEsts and the timing field (agentic_trader/agent/earnings.py:138-148) but keeps them only in a TTL'd dict (earnings.py:185-227).
- reported_rows journals only body_sha256 and drops the raw bytes (earnings.py:157-169, 243+).
- NewsItem has no updated_at field (agentic_trader/data/news.py:33-48).

Why the Nasdaq consensus/timing capture is worth doing now:
- It is the one input that is both perishable and close to cards. The only alpha that made it to live is the catalog PEAD long probe (docs/apriori-alphas.md table row 'pead v2': eligible +0.149R). That study's surprise uses today's revised consensus for past quarters (docs/alpha-data-augmentation-2026-10-18.md section 3, 'Point-in-time earnings').
- The live probe reads the page at D+1 (agentic_trader/research/apriori/pead_live.py:123), so live and research surprise are not the same quantity.
- Capturing daily vintages for D-5..D+30 measures that research/live contract gap within a single earnings season. A season gives roughly 1,000+ events across cohort v2/v3. It also starts an analyst-SUE record (Livnat & Mendenhall 2006), which no free vendor sells retrospectively.
- Q3 reporting season is under way now (late October to mid-November), so each week of delay loses most of a season. That is the case for 'now' over 'next'.

The rest of the bundle is mostly recoverable later or already decided:
- Asset-master snapshots: the survivorship spike (docs/survivorship-spike-2026-10-20.md 'Answer', 'What it means for the desk') already found that dated delistings come free and retrospectively from EDGAR Form 25/15, and renames come from Alpaca name_change rows. The spike also recommended not building survivorship repair before the next lane ('Recommendation'). A daily gzip of the master is nearly free, so add it, but it does not justify the work.
- VIX, FRED and yields can be fetched retrospectively (ALFRED vintages, CBOE history). Drop them.
- Option-chain summaries are perishable but depend on the C33 decision. Keep them out of this item.
- news updated_at is a one-field change to the existing news journal.

Why P1 and not P0: it produces no card for at least one earnings season, and the north star is cards. Why not P2: the cost is one small PR, the data cannot be bought later, and it directly hardens the desk's only live alpha.

Unverified: I could not find a doc that measures 'time-not-supplied on 100% of historical Nasdaq rows'. The string exists only as an enum mapping (earnings.py:34) and in plan fixtures. Treat that sub-claim as unconfirmed until it is measured on the cached pages.

**Risks**

- Collect-and-forget: forward-only data with no predeclared consumer tends to rot. Without a named first read, for example 'after the Q3 season, compare vintage surprise with revised surprise on PEAD events', it is a sunk cost.
- Nasdaq's endpoint is unofficial and keyless. Pulling about 36 pages a day (D-5..D+30), on top of the live calendar and PEAD reads, raises the chance of throttling or blocking, which would also break the live earnings blackout and PEAD probe that share that endpoint. Pace the pulls and run them away from scan windows.
- Nasdaq's consensus is a single aggregator of unstated provenance with a coarse noOfEsts. It is not I/B/E/S, so any SUE built from it is noisy and can change silently.
- Putting a sixth component into the trading daemon adds production surface: readiness, shutdown and SDK clients. CLAUDE.md lists research executor separation as unfinished, and every failure path would need the corporate-actions worker's fail-closed discipline.
- Scope creep into the option-chain and FRED items turns an S into an M, and slows the perishable part.
- Raw page bytes and the asset master must stay in the private artifact store, out of Git, under the repo's secrets and data rules.

**Prerequisites**

- A one-page frozen capture spec: sources (Nasdaq earnings pages for D-5..D+30 with raw bytes, the Alpaca asset master for all statuses, news updated_at), file layout, SHA-256 and receipt time, pacing, and failure semantics. A failure must never touch scans or readiness gating.
- A decision on placement. Prefer a standalone scheduled research acquisition job outside the trading daemon, writing to the private research store as a digest-pinned cache like the other `alpha <source>` acquisitions. Fall back to the corporate-actions worker pattern (agentic_trader/data/corporate_actions_worker.py:44-68) only if the daemon is required.
- A named first consumer and read date, for example after the Q3 season: vintage surprise against the PEAD v2 study's revised surprise on the same events, plus BMO/AMC as first announced against the D/D+1 reaction convention in docs/alpha-pooled-mining.md 'Visibility convention'.
- A check that the extra Nasdaq load does not degrade the live earnings blackout or the PEAD probe.

**Better alternative**

Ship the perishable 80% in about a day without adding a daemon worker:
1. Add a scheduled research acquisition, outside the trading daemon and scan windows (for example around 18:00 ET). Once per New York date it gzips:
   - the raw Nasdaq earnings pages for D-5..D+30, with SHA-256 and receipt time;
   - the Alpaca asset master, all statuses, because it is nearly free.
2. Keep the raw body that reported_rows already hashes for the live PEAD decision (earnings.py:243+ journals body_sha256 only). The probe's own inputs then become point-in-time evidence at zero extra requests.
3. Add updated_at to NewsItem and to the research news cache.

Drop VIX, FRED and yields: ALFRED and CBOE give them retrospectively. Decide option chains separately under C33. For membership, a one-off EDGAR Form 25/15 acquisition gives dated delistings retrospectively, which is higher leverage than waiting years for a forward asset-master record.

---

<a id="c21"></a>

## C21 — The a priori event design is the only one that has produced a deployable result: generalise its route to cards and feed it data already on disk

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | alpha_mining | research_direction | high | M | partially_confirmed |

_lenses: product_

### Claim (as consolidated)

Among about 25 frozen studies and 64 pooled pairs, the only pass is the PEAD a priori entry. Its design is one hypothesis, entered once per event, against an event-matched control.

exdiv used the same design and failed only on its placebo test, leaving a yield gradient.

The pooled lane encodes events differently: daily top-decile baskets with no hold-skipping, compared against all eligible names (formula.py:195-215). Its pead_* nulls therefore neither replicate nor contradict PEAD.

The live route exists only for PEAD and is hard-coded into run_scan (copilot.py:897-948, 1138-1170, 1273-1279).

The digest-pinned Form 4 cache is already on disk, but it was built into pooled decile fields rather than event entries.

### Evidence

- docs/apriori-pead-2026-09-26.md 'Criteria'; docs/apriori-exdiv-2026-10-12.md (placebo failure; yield gradient)
- agentic_trader/research/pooled/formula.py:195-215
- agentic_trader/agent/copilot.py:897-948, 1138-1170, 1273-1279
- docs/alpha-data-augmentation-2026-10-18.md:43-44; docs/alpha-pooled-mining-research-2026-09-28.md:121 ('a priori catalog is the higher-yield source')

### Recommendation

1. Extract a CatalogCandidateSource protocol: entry file and SHA, an event loader at a decision time, a frozen bracket plus session_count exit, its own per-session budget, and the probe cap. Port PEAD onto it with a parity test showing no behaviour change.

2. Freeze one-trial a priori entries, in this order, each with an event-matched control:
   (a) Opportunistic insider cluster purchases: non-routine officer and director open-market buys, at least 2 insiders within 30 days, entry at the next 10:35 after the filing date, with 20- and 60-session holds declared. Freeze it before any pooled insider campaign reads the data.
   (b) PEAD v3 on EDGAR 8-K 2.02 timestamps (C22).
   (c) A yield-conditioned exdiv v2, declared as a second look.

A pass then reaches Telegram through the probe lane in days.

### North-star impact

Creates a pipeline of discrete, explainable cards, each with a measured edge over an event-matched control. These sources can supply cards when natives are withheld and PEAD is out of season.

### References

- Cohen, Malloy & Pomorski (2012), JF 67(3) — Opportunistic insider trades as a one-trial event definition
- Lakonishok & Lee (2001), RFS 14(1) — Clustered purchases predict 6–12-month returns, which sets the hold to declare
- Chen & Zimmermann, Open Source Asset Pricing — Published definitions and t-stats as priors (https://www.openassetpricing.com)

### Fact check — partially_confirmed

**Corrected claim**

The core structural claims hold.

- **PEAD is the only route.** The live catalog route exists only for PEAD. It is hard-coded through run_scan (copilot.py:897-948, 1138-1170, 1273-1279), probe enrolment (probe.py:19, CATALOG_ENTRY_IDS = ('pead',); probe.py:47, load_pead_entry) and config (config.py:177-184). A CatalogCandidateSource abstraction does not exist.
- **The pooled lane uses a different design.** It scores daily top-decile baskets with no hold-skipping (formula.py:195-215) against the mean of every labelled, eligible name in the session. That control is in stats.py:54-65, not in formula.py. So the pooled pead_* nulls (docs/alpha-pooled-events-v1-2026-10-15.md:65-81) do not test the event-matched PEAD design.
- **exdiv** failed only on P2 (the same-name placebo) and left a yield gradient.
- **The insider data** is built only into pooled decile fields (insider_fields.py). The pending insider-v1 fixed set already includes decile versions of opportunistic_buying and cluster_buying, and its campaigns are still pending.

Two corrections:
- **Not the only deployable result.** PEAD is the only positive-alpha pass among the frozen studies and the 64 pooled pairs, but not the only predeclared result that was deployed. The short-suppression test (docs/setup-baserates-short-2026-09-23.md) fired, and native strategies became long-only.
- **Lakonishok & Lee (2001)** supports 'insider purchases (not sales) predict returns, mainly in small firms, over months'. It does not support 'clustered purchases' specifically. Clustering is better cited to Alldredge & Blank, as the repo's own docs/alpha-pooled-mining.md:1231 does. Its 6-12-month horizon is also much longer than the 20- and 60-session holds the recommendation proposes.

**Checked**

- agentic_trader/research/pooled/formula.py:195-215: select_top_fraction keeps the top ceil(fraction*n) of each session with no hold-skipping. This supports the decile-basket description.
- agentic_trader/research/pooled/stats.py:54-65,91-94: control_mean is the mean over every labelled cell in the session, and edge = pick mean - control_mean. agentic_trader/research/pooled/cube.py:105 says labelled = 'eligible and resolved'. The 'all eligible names' control comes from here, not from formula.py.
- config/research/pooled/events-v1.json: selection is top_fraction 0.1. The pead_surprise_decay, pead_reaction_decay, pead_reaction_window and pead_agreement families are present. The windowed ones take the decile among in-window names, but they are still compared with all labelled names.
- docs/alpha-pooled-events-v1-2026-10-15.md:65-68,78-81: the pead_* t-stats lie between -0.78 and +1.85. No finalists.
- agentic_trader/agent/copilot.py:682 (run_scan starts here), 897-948, 1138-1170, 1273-1279: the drift prep, collect and budget code is PEAD-specific (PEAD_STRATEGY_ID, earnings_drift, max_drift_*). Confirmed.
- agentic_trader/research/apriori/probe.py:19 CATALOG_ENTRY_IDS = ('pead',); probe.py:47 calls load_pead_entry; config.py:172-184 AprioriConfig.pead_entry_path and max_drift_*; screeners/earnings_drift.py:44,82: the catalog and probe route is PEAD-only end to end.
- agentic_trader/research/apriori/catalog.py:223,266: ExdivEntry and load_exdiv_entry exist for research only. Nothing live uses them.
- docs/apriori-pead-2026-09-26.md:3,72-77 'Criteria': LONG passed P1-P4, and the control is the same bracket on every liquid reporter.
- docs/apriori-exdiv-2026-10-12.md 'Result' and 'Descriptives': P1, P3 and P4 hold, P2 fails against the same-name placebo. Yield terciles are +0.038/+0.051/+0.117R. Confirmed.
- docs/apriori-alphas.md:23-24,81-82: high52 and reversal-lowmax fail. No other catalog pass.
- docs/setup-baserates-short-2026-09-23.md:1-8: the predeclared short-suppression rule fired and was deployed (native strategies set to allow_short: false). This is counter-evidence to 'only one that has produced a deployable result'.
- docs/setup-outcomes-2026-09-23.md:1-16: the ranker failed, and setup_quality carries no information.
- agentic_trader/research/pooled/insider_fields.py:1-27,62-69 and config/research/pooled/insider-v1.json: the Form 4 data is built into six pooled fields. The fixed set already contains opportunistic_buying (a simplified CMP routine rule: same owner, same month of the previous year) and cluster_buying (insider_buyers > 1), both scored as top-decile formulas. docs/alpha-pooled-mining.md:1249 says 'campaigns pending'.
- docs/alpha-data-augmentation-2026-10-18.md:43-44: these lines are the Form 4 row (the proposed form is pooled fields plus a cluster-buy flag) and the 8-K / PEAD v2 row. The citation is valid.
- docs/alpha-pooled-mining-research-2026-09-28.md:121: contains 'the a priori catalog is the higher-yield source of daily suggestions'. The citation is valid.
- WebSearch: Cohen, Malloy & Pomorski (2012), JF 67(3):1009-1043, opportunistic vs routine trades. Verified.
- WebSearch: Lakonishok & Lee (2001), RFS 14(1):79-111. The abstract says purchases (not sales) predict returns, mainly in small firms. It does not frame the result as 'clustered' purchases.

**Bad references**

- Lakonishok & Lee (2001), RFS 14(1): the paper exists and supports 'insider purchases predict returns (mainly in small firms)'. It does not specifically support 'clustered purchases'. Its 6-12-month horizon also does not justify the declared 20- and 60-session holds.
- agentic_trader/research/pooled/formula.py:195-215 is cited for 'compared against all eligible names', but those lines show only the selection. The control (mean of all labelled cells per session) is at agentic_trader/research/pooled/stats.py:54-65.

**Notes**

I found nothing that refutes the main thesis. The live probe route is PEAD-specific throughout: run_scan, AprioriConfig, the CATALOG_ENTRY_IDS = ('pead',) tuple, and load_catalog_probe, which calls load_pead_entry. Nothing generic exists.

The pooled lane's control is every labelled name in the session, which is not event-matched. That holds even for the windowed pead_* formulas, which take their decile among in-window names only.

**What the finding overstates:**
- **The title.** The short-suppression test (2026-09-23) also fired under a predeclared rule and changed production. PEAD is the only positive alpha pass, but not the only deployed result.
- **The Form 4 claim omits something.** The pooled insider-v1 fixed set already encodes the same two hypotheses (opportunistic_buying, cluster_buying) as decile formulas, so recommendation (a) re-tests them in event form. That is still distinct, but the overlap is worth stating.
- **The repo's routine-trade rule is simpler than Cohen-Malloy-Pomorski's.** insider_fields.py:26-27 calls a purchase routine when the same owner bought in the same calendar month of the previous year only. CMP's rule needs three consecutive years. An a priori 'opportunistic' entry would need to freeze its own definition.

**The recommended ordering is valid.** The insider campaigns are still pending (docs/alpha-pooled-mining.md:1249), so an a priori entry frozen now avoids a second look at the data.

### Value review — P1, now (days)

**Reasoning**

The direction is right. The finding bundles four items with very different value, and its order is wrong.

What holds:
- The PEAD a priori long leg is the only pass in the history: +0.149R, +0.055R paired vs control (docs/apriori-pead-2026-09-26.md 'Criteria').
- The desk's own power analysis already ranked the a priori catalog as the higher-yield source of suggestions (docs/alpha-pooled-mining-research-2026-09-28.md 'Conclusion').
- The live route is PEAD-specific. copilot.py:897-948 (drift prep, lateness, caps), 1138-1170 (drift admission) and 1271-1280 (a separate drift budget) are hard-coded, and copilot.py contains 187 'pead'/'drift' references.
- The Form 4 cache holds whole-market quarterly archives. Symbols are mapped only at read time (insider_filings.py:496-596), so an event-level, whole-market study is feasible without new acquisition.

Why it is time-critical: the pooled insider fixed set already contains the same hypotheses.
- `opportunistic_buying` and `cluster_buying` are in config/research/pooled/insider-v1.json 'families'. They are scored as daily top-decile baskets with no hold-skipping (formula.py:195-215), on the v2/v3 cohorts, over discovery 2016-08..2021-12 and selection 2022-2023.
- Whichever look reads those outcomes first makes the other one a second look.
- The campaigns are awaiting operator approval (docs/alpha-roadmap.md:627), so the order is still the operator's choice. Freezing the a priori entry first costs hours and keeps it a first look.
- The event-level design (all liquid events market-wide, one entry each, explicit control, same P1-P4 bootstrap in research/apriori/statistics.py) is the shape that produced the only pass. It is the better first look at this hypothesis than an eighth and ninth pooled decile formula.

What I would not fund now:
1. **Step 1, the CatalogCandidateSource extraction before any second leg passes.** It is a refactor of the riskiest live path: lateness skip, caps, unavailable notices and budgets, guarding the only live probe. It produces no edge. Do it inside the probe PR of the first second leg that passes, with the PEAD parity test.
2. **Step 2(c), exdiv v2.** The yield tercile gradient is a post-hoc descriptive on a failed window, and the placebo already explains two-thirds of the leg (docs/apriori-exdiv-2026-10-12.md 'Reading', 'Decision'). Low prior. Treat it as P3 or drop.
3. **Step 2(b).** It belongs to C22.

Corrections to the north-star claim:
- **Season.** Insider open-market buys concentrate in post-earnings open trading windows, the same weeks as PEAD. They will not fill off-season gaps. They will compete for max_drift_cards_per_session=1 and max_open_drift_positions=4 (config.py:178,181).
- **Live path.** 'Reaches Telegram in days' is wrong. No live EDGAR source exists: EDGAR appears only in research/ and the acquisition CLI. The quarterly data sets lag by up to a quarter, so a live Form 4 daily-index/XML feed with research parity is a separate build of weeks.

Verdict: P1 on the narrowed scope, with the freeze and sequencing decision due this week:
- Freeze one insider a priori entry and run it on cached data.
- Generalise the live route only after it passes.

**Risks**

1. **Cross-lane double-dipping.** The pooled insider-v1/cohort3-insider-v1 campaigns test cluster and opportunistic buying on the same 2016-2026 Form 4 data. A full-window a priori read also touches the pooled lane-wide 2024-26 confirmation window, which is already a known tension (history map, open questions). Without an explicit declaration of which test is the first look, both results are compromised.

2. **Power.** PEAD's paired ci90 was about ±0.034R at n=4,726 (docs/apriori-pead-2026-09-26.md 'Criteria'). Clustered opportunistic buys in names passing the live liquidity gate ($10 close, dollar volume ≥ 25th percentile of static equities) are probably low thousands at most. The ci90 half-width then roughly doubles, so a true +0.05R paired edge would likely fail P2. That is a false negative that would close a good hypothesis.

3. **Horizon mismatch.**
   - The literature effect runs over 6-12 months (Lakonishok-Lee). The desk bracket is 20 sessions with a 2xATR stop and a 3R target.
   - Declaring both 20- and 60-session holds is two hypotheses unless 20 is primary and 60 is descriptive, as PEAD did.
   - A 60-session hold would also occupy drift position slots for three months.

4. **Supply overlap.** Post-earnings trading windows put insider cards in PEAD weeks. Under the shared drift caps (config.py:178,181) one source crowds out the other, and off-season supply does not improve.

5. **Live parity.** No live Form 4 feed exists. The quarterly SEC data sets (research) and the EDGAR daily index/XML feed (live) are different parsers, a classic parity bug source. Outage handling also needs a fail-closed rule like PEAD's Nasdaq gap rule.

6. **Survivorship and identity.** The company_tickers.json map is current-only (insider_filings.py:574-596). Delisted issuers' events vanish, which flatters the leg's absolute mean. Only a control drawn from the same mapped universe neutralises this.

7. **Refactor regression.** Extracting a protocol from run_scan's PEAD path before a second user exists risks breaking the only live probe's lateness, cap and unavailable-notice contracts for no new edge.

8. **Evidence maturity.** The PEAD probe has 4 cards, 1 executed and 0 closed, and expires 2026-10-28 (live map, 'PEAD catalog probe'). A second probe leg on manual taps would add more unmatured evidence unless measurement relies on counterfactual labels of every eligible event (research/apriori/probe_outcomes.py).

**Prerequisites**

1. **Operator sequencing decision now, before approving the pooled insider campaigns.**
   - Either freeze the a priori insider entry first and designate the pooled cluster_buying/opportunistic_buying formulas as second looks,
   - or explicitly drop one of the two looks.
   - Also declare which lane owns the 2024-26 insider window.

2. **A label-blind event-count pass on the on-disk Form 4 cache, before freezing.**
   - Count qualifying non-routine officer/director open-market buys with ≥2 insiders within 30 days that pass the live liquidity gate, per year, per session, and the share from 2023.
   - Use those counts for a power estimate against the P2 bar and the P4 sample floor (300 events, 100 recent).
   - If underpowered, widen the definition before freezing (for example, any opportunistic buy), not after.

3. **A frozen entry spec**, in the style of the PEAD design doc:
   - the CMP routine-trader rule;
   - visibility from filing date F, entering at 10:35 on F+1;
   - an explicit control (for example, all liquid names with a qualifying Form 4 trade in the same session window, or the session-paired eligible universe);
   - a 20-session primary hold on the existing apriori bracket, with 60 sessions descriptive only;
   - costs of 0/5/10 bp, with 5 bp deciding;
   - the survivorship caveat recorded.

4. **Daily bars for whole-market event names**, fetched as PEAD did for its event names.

5. **Only after a pass:**
   - a live Form 4 source spec with daily index plus XML, research-parity tests and outage fail-closed behaviour;
   - then the CatalogCandidateSource extraction with a PEAD behaviour-parity test, in the same PR as the new probe;
   - a decision on how the new leg shares or splits the drift card budget and position caps.

6. **A PEAD probe renewal and measurement decision before 2026-10-28**, so the probe lane still exists to receive a second leg.

**Better alternative**

Narrow the work to its cheap, time-critical core, and defer everything else until something passes:

1. Freeze the insider a priori entry this week and run it on the cached data. It is about exdiv-sized: events ~260 lines, study ~340, runner ~110 under research/apriori/. Let it stand in for, or come before, the cluster and opportunistic formulas in the pooled insider campaign. Hold the pooled campaign approval until this read is done.
2. Do not build the CatalogCandidateSource refactor or the live Form 4 feed until a second leg passes.
3. Drop exdiv v2.
4. Leave PEAD v3 to C22.

In parallel, the higher-leverage card-quality work is the existing PEAD leg:
- Renew it before 10-28.
- Judge it on counterfactual labels of every eligible event (probe_outcomes.py) rather than on 1-in-4 manual taps.
- A 60-session PEAD version (descriptively +0.25R) on a fresh window is a better-priored next a priori entry than exdiv v2.

---

<a id="c23"></a>

## C23 — Detect information with the whole cross-section and both tails, then test the long-only card implementation separately: the top-decile-minus-pool statistic wastes up to 40% of t

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | next (weeks) | research_methodology | improvement | high | M | partially_confirmed |

_lenses: quant_

### Claim (as consolidated)

Discovery uses only the long top decile minus the equal-weighted pool (formula.py:195-215; stats.py:140-145). One statistic is asked both to detect information and to prove long-only tradability.

A simulation (370 names, 1,500 sessions, weak linear signal) compared statistics:
- A Fama-MacBeth slope on the score's rank has 1.70x the t, worth about 2.9x the data.
- Top-minus-bottom decile has 1.36x.
- The gain falls to 1.15x when style-factor noise dominates, which is why controls come first (C05).

In-house evidence: PEAD's short leg beat its control by +0.100R (ci90 [+0.048, +0.157]), against +0.055R for the long leg. A two-leg detection statistic would have had t of about 4 instead of 2.66.

The DSL has no cross-sectional operators (dsl.py:1-5).

Evidence never reaches the depth cards trade at: top3_edge is computed only at confirmation (campaign.py:826-829), which no campaign has reached.

### Evidence

- agentic_trader/research/pooled/formula.py:195-215; research/pooled/stats.py:140-145
- agentic_trader/research/alpha/dsl.py:1-5; research/alpha/operators.py:108-167; research/alpha/panel.py:19-37
- agentic_trader/research/pooled/campaign.py:826-829
- docs/apriori-pead-2026-09-26.md 'Criteria' (SHORT P2 +0.100; LONG +0.055)
- ~/agentic-trader-research/holistic-review-20261021/stat_efficiency.py and endtoend_power.py

### Recommendation

Pooled protocol v5 makes a two-stage claim.

(A) Information test:
- Statistic: the mean Fama-MacBeth slope of the risk-adjusted forward return on the sector-demeaned cs_rank of the score, at the predeclared horizon.
- Errors: HAC with lag at least the horizon, or a session-block bootstrap, with t calibrated against check C.
- Multiplicity: Holm across the declared set, on discovery and selection pooled (about 1,858 sessions). In simulation this raises power at 0.055R from 31% to 65%.

(B) Implementation test, for A-passers only, as a single one-sided 5% test: top-k long-only (k = 1–2) on the live card bracket, β-adjusted.

Add cs_rank, cs_zscore, group_demean(sector) and where(cond, x) to pooled scoring, replacing the divide-by-boolean masks.

### North-star impact

Raises detection power 1.2–1.7x at no data cost, and tests long-only tradability at the depth cards actually trade.

### References

- Grinold & Kahn (2000), Active Portfolio Management, 2nd ed. — Information is measured across the full cross-section; constraints are applied afterwards
- Lewellen (2015), The Cross-section of Expected Stock Returns, Critical Finance Review 4(1) — Fama-MacBeth composite forecasts have out-of-sample power among larger stocks
- Stambaugh, Yu & Yuan (2012), The Short of It, JFE 104(2) — Anomaly profits concentrate in short legs, so a separate long-only test is needed

### Fact check — partially_confirmed

**Corrected claim**

For protocols v3 and later (every fixed-set campaign since 2026-10-05), pooled discovery ranks each formula's score, takes the long top decile (formula.py:198-215) and tests its edge over the equal-weighted mean of all eligible, labelled names (stats.py:54-105, 140-145). The gate is not one statistic: it also needs the absolute long-leg mean > 0 and 3 of 4 positive blocks (campaign.py:718-749). Information detection and long-only tradability are still fused into one long-tail test. No full-cross-section or two-tail information statistic exists in this lane.

Under a weak linear Gaussian signal, the simulation reproduces as claimed: a Fama-MacBeth rank slope gives about 1.7x the t (about 2.9x the data), and top-minus-bottom about 1.35x. The gain shrinks to about 1.1x when score-correlated style noise dominates. The figure is a model result. For tail-concentrated or conditional formulas it overstates the gain, and such formulas are common here: the divide-by-0/1 seeds in the events, short and insider sets leave most names unscored. In those formulas the decile is taken among scored names but compared with the whole eligible pool, so the edge mixes the condition with the ranking.

PEAD numbers: the cited values are correct (LONG P2 +0.055R, t ≈ 2.66; SHORT P2 +0.100R). The "t ≈ 4" figure is an approximation. It assumes the two legs are independent, and it combines short-bracket labels from the a priori catalog lane, while the pooled cube holds only long-bracket labels (cube.py:2). A bottom decile scored on the cube would therefore not reproduce the +0.100R.

The DSL has no cross-sectional operators (dsl.py:1-5), and campaign families carry only seed strings (campaign.py:100-107). Some of the proposed pieces already exist elsewhere and should be reused rather than written anew:
- cross_sectional_rank, cross_sectional_zscore and group_neutralize (alpha/panel.py:19-37)
- the Rank IC/HAC module cross_sectional_ic (alpha/information.py:168)
- quantile filters on pooled literature entries (formula.py:59-83, 146-161)

top3_edge is computed only at confirmation (campaign.py:826-829), which no campaign has reached. "Evidence never reaches the depth cards trade at" is still too broad. The pooled literature entries high52-v1 and reversal-lowmax-v1 were tested at long top 3 per session (docs/alpha-pooled-literature-2026-09-30.md), and protocols v1/v2 used k=3. Top-3 selection was dropped for the decile because check B failed at top 3 (docs/alpha-pooled-checks-2026-10-02.md:98-120). The gap therefore applies only to the decile campaigns.

The quoted Holm power gain (31% to 65% at 0.055R) reproduces from endtoend_power.py. Most of that gain comes from pooling discovery with selection and replacing t ≥ 3 with Holm, not from the change of statistic. The model also ignores the block, leg and trimmed-mean gates.

**Checked**

- agentic_trader/research/pooled/formula.py:177-215: select_picks (top-k with hold-skipping) and select_top_fraction (top ceil(0.1n), no hold-skipping)
- agentic_trader/research/pooled/stats.py:54-105: session_table. The control is the equal-weighted mean of all eligible, labelled cells, picks included; edge = pick mean - control
- agentic_trader/research/pooled/stats.py:140-145: paired_edge_test (stationary session-block bootstrap ratio)
- agentic_trader/research/pooled/campaign.py:718-749: the discovery gate needs edge t >= min_t, 3 of 4 positive blocks, leg mean > 0 and min_sessions
- agentic_trader/research/pooled/campaign.py:774-796 (selection) and 805-846 (confirmation, Holm on the edge p-value)
- agentic_trader/research/pooled/campaign.py:684 and 826-829: DESCRIPTIVE_TOP_K = 3; top3_edge exists only inside _confirmation
- agentic_trader/research/pooled/campaign.py:351-362: select dispatches top_fraction or top_k
- agentic_trader/research/pooled/campaign.py:100-107: a campaign Family holds seed strings only, with no filters
- agentic_trader/research/pooled/formula.py:59-83 and 146-161: pooled Formula supports cross-sectional quantile filters (literature entries only)
- agentic_trader/research/pooled/cube.py:2 and 104-107: the cube holds long-bracket labels only
- agentic_trader/research/alpha/dsl.py:1-5: no cross-sectional operators; operators.py:108-167 are time-series only
- agentic_trader/research/alpha/panel.py:19-37: cross_sectional_rank, cross_sectional_zscore and group_neutralize exist; used at research/setups/features.py:189
- agentic_trader/research/alpha/information.py:1-60 and 168: cross_sectional_ic (Rank IC with HAC), used by panel_study.py:380, daily_forecasts.py:552, panel_forecast.py:227 and persistent_study.py:160, but not by the pooled lane
- config/research/pooled/*.json: v3+, events, short, insider and cohort3 protocols use top_fraction 0.1; campaign-v1/v2 use k=3; discovery min_t 3.0, selection 0.5x of discovery, confirmation Holm at 0.05
- config/research/pooled/events-v1.json, short-v1.json, insider-v1.json: seeds that divide by 0/1 conditions confirmed
- docs/apriori-pead-2026-09-26.md 'Criteria' table: LONG P2 +0.055 (+0.021,+0.089), SHORT P2 +0.100 (+0.048,+0.157), control -0.164; the implied LONG t of about 2.66 is correct
- docs/alpha-pooled-literature-2026-09-30.md: high52-v1 and reversal-lowmax-v1 were tested long top 3 per session
- docs/alpha-pooled-checks-2026-10-02.md:98-120 and docs/alpha-pooled-checks-v3-2026-10-05.md:17-31: top-3 was abandoned for the decile because check B failed
- Ran scratchpad stat_efficiency.py (30 sims): FM rank slope 1.72x, top-minus-bottom 1.35x; with style noise c=0.05: 1.46x and 1.26x; c=0.15: 1.11x and 1.07x
- Ran scratchpad endtoend_power.py: at delta 0.055, se_d 0.017, frozen confirmation power 0.31 vs v5 0.65 (reproduced)
- Web: Lewellen 2015 CFR 4(1) 1-44 exists (FM composite forecasts, rolling real-time slopes); Stambaugh, Yu & Yuan 2012 JFE 104(2) 288-302 exists (short legs carry the sentiment-driven anomaly profit); Grinold & Kahn 2nd ed. 2000 exists

**Bad references**

_none_

**Notes**

The core of the finding holds:
- The pooled lane's only detection statistic is a long-tail-versus-pool edge.
- It has no cross-sectional or IC statistic.
- The DSL has no cross-sectional operators.
- top3_edge exists only at confirmation.
- The simulation ratios and the 31%→65% power figures reproduce from the scratchpad scripts.

Overstated or missing details:
1. Discovery is not a single statistic: it also requires leg mean > 0 and block consistency.
2. Top-3 depth evidence does exist, from the pooled literature entries and v1/v2. The decile was adopted deliberately after check B failed at top 3.
3. The cross-sectional operators and a Rank IC/HAC implementation already exist in research/alpha/panel.py and research/alpha/information.py. Recommendation (A) can reuse them.
4. The PEAD two-leg t ≈ 4 assumes independent legs and mixes short-bracket labels from another lane.
5. The 1.7x gain assumes a linear full-cross-section signal. Conditional formulas that divide by a boolean would gain less, and their pool-mismatch problem (the decile is taken among scored names, the control is all eligible names) is a related gap the finding does not name.

All three references exist and broadly support their stated use. The Lewellen abstract does not itself mention larger stocks; my recollection that the paper's large-stock subsample results support the claim is unchecked against the full paper, so it is not listed as a bad reference.

### Value review — P1, next (weeks)

**Reasoning**

The diagnosis holds up, and of the remaining options in the pooled lane it is the cheapest way to add power, because it needs no new data. I checked the code:
- Discovery is decided by one statistic: the top decile minus the pool. It comes from `select_top_fraction` (agentic_trader/research/pooled/formula.py:195-215) through `paired_edge_test` (agentic_trader/research/pooled/stats.py:142-146).
- The only depth-matched read is `top3_edge`, and it runs only inside `_confirmation` (agentic_trader/research/pooled/campaign.py:805, 826-829; `DESCRIPTIVE_TOP_K = 3` at :684). No campaign has ever reached that step.
- The DSL rules out cross-sectional operators on purpose (agentic_trader/research/alpha/dsl.py:1-4).

The history map lists "power below the plausible effect" as a recurring failure: the lane can detect about 0.08-0.10R, while plausible large-cap ICs map to about 0.02R (alpha-pooled-mining-research-2026-09-28.md). A statistic that uses the whole cross-section, plus Holm over discovery and selection pooled, targets that gap directly. Splitting the test in two is also the right idea: "is there information" is a different question from "does a 1-2 name long-only bracket make money".

Why it is not P0:
1. The record suggests the formula families lack information, not just power. Seven of eight edges were negative on the intraday and v4 fixed sets. `quiet_momentum` had t -3.34. Cohort v3's largest t was +1.85 (docs/alpha-pooled-cohort3-2026-10-16.md). More power cannot turn negative edges positive.
2. Much of the claimed gain comes from the short side, which the desk cannot trade. Shorts are disabled in config/config.yaml:305,320 after losing -0.33R (docs/setup-baserates-short-2026-09-23.md). By the finding's own Stambaugh citation, an information test that passes on short-leg information will often fail stage B. So the gain at the card is much smaller than 1.7x.
3. Stage B at k = 1-2 on the confirmation window is weakly powered in its own right. I estimate t around 1.5-2 for a 0.055R effect over about 646 sessions. That is a rough check, not a simulation.
4. The gain shrinks to 1.15x under style noise. That makes the factor-control work (C05) a real prerequisite, not a nice-to-have. Without it, Fama-MacBeth slopes will mostly pick up vol and size tilts the desk already knows about (the parked `vol_20` lead).
5. The simulation assumes a linear signal with independent sessions and Gaussian noise. Event and anomaly payoffs are often tail-shaped, where decile statistics lose less.

For a paper account with one operator, a few weeks of method work is justified only if a cheap diagnostic first shows that the closed sets hid information.

**Risks**

- **Forking paths.** Rescoring the 64 closed formula-cohort pairs with a new decisive statistic on the same discovery and selection windows is a second look. It must be predeclared and charged to the pooled ledger before any read. It must not be used to reopen sets that already failed under the old statistic.
- **The confirmation window.** The one-use, lane-wide window (2024-01-02 to 2026-07-31) is still unused, and it is the desk's most valuable research asset. A v5 that sends an A-passer into an underpowered stage-B test there spends it on a coin flip.
- **Short-leg findings.** Detected information may live mainly in the short leg (PEAD's short P2 was +0.100R against +0.055R long, docs/apriori-pead-2026-09-26.md "Criteria"). That invites pressure to re-enable shorts, which were measured negative.
- **Sector demeaning on poor labels.** `group_demean(sector)` without authoritative point-in-time sectors would rest on config-group labels. Cohort v3's 1,000 names have none (maps/pooled.md:177). The test would quietly become a test of the label source.
- **Unmatched checks.** Checks A and C, and the power arithmetic, are calibrated for the paired-edge bootstrap. A new statistic with HAC errors and overlapping 20-session labels needs its own null calibration. Otherwise the 1.7x claim is unverified in the real pipeline.
- **Scope creep.** Adding four DSL operators widens the search space and the multiplicity of a fixed set.

**Prerequisites**

1. Factor and risk controls (C05) must land first, so that "risk-adjusted forward return" is defined. Otherwise the gain falls to about 1.15x and the slopes load on known styles.
2. Point-in-time sector labels for cohorts v2 and v3, for example SIC codes from the EDGAR submissions data the insider lane already reaches. Without them, drop `group_demean(sector)` from v5.
3. Fresh versions of check A (planted signal) and check C (real-formula null) for the slope statistic: HAC lag at least 20 sessions, or the existing session-block bootstrap. They must pass at the same clean revision.
4. Before any data is read, a written v5 protocol that freezes:
   - the two-stage rule;
   - Holm across the declared set over discovery and selection pooled;
   - the stage-B window, k, and the live card bracket with beta adjustment;
   - the rule that stage B consumes the confirmation window only after an A-pass.
5. Operator approval, as for every new pooled protocol.

**Better alternative**

Before building v5, run a cheap, charged, descriptive-only diagnostic.
- **What it computes:** the Fama-MacBeth rank-slope t and the top-minus-bottom decile t, beside the existing paired edge, for the fixed-set formulas already scored.
- **Scope:** the discovery window only. No claim, no use of the confirmation window, journaled as a diagnostic.
- **Effort:** about S, since the cubes and panels are cached.

If no formula shows a same-sign slope t of about 2.5 or more that holds into selection, the closed families lacked information, and v5's main benefit is moot for them. Spend the effort on new information sources or on C05 instead.

If several do, build v5. Make stage B the existing `select_picks` top-k (k = 3) paired edge, which is already implemented as `top3_edge`, promoted from descriptive to decisive. That avoids a new execution path.

Independently, and cheaply, the Holm-on-pooled-windows change can be adopted on the current top-decile statistic in the next protocol. The finding's own simulation credits most of the power gain at 0.055R (31% to 65%) to that change, so it captures much of the gain with none of the short-leg mismatch.

---

<a id="c24"></a>

## C24 — Replace hand-picked 8-formula sets with a frozen, literature-signed feature library, and add the cross-sectional DSL operators that have been requested repeatedly

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | next (weeks) | alpha_mining | improvement | high | M | partially_confirmed |

_lenses: ml_

### Claim (as consolidated)

Fields are a global dict edited per lane, with no source, visibility-lag or sign metadata (dsl.py:28-48).

Operators are single-series only (operators.py:108-169); dsl.py:1-5 calls cross-sectional operators 'deliberately absent'. cross_sectional_rank, cross_sectional_zscore and group_neutralize exist only in panel.py:19-37, and only setups/features.py uses them. Formulas are evaluated per symbol in a loop (formula.py:125-130).

Quality problems:
- 'Only when' is written as division by a boolean.
- `log` is declared dimensionless (operators.py:125), so price-level ranks pass the unit check.
- Rolling operators are Python lambdas, about 100x slower than vectorised ones (operators.py:15-19, 138-152).

Cross-sectional operators have been recommended three times (alpha-data-augmentation §3).

### Evidence

- agentic_trader/research/alpha/dsl.py:1-5, 28-48
- agentic_trader/research/alpha/operators.py:15-19, 65-67, 108-169, 125, 138-152
- agentic_trader/research/alpha/panel.py:19-37; research/pooled/formula.py:125-130
- config/research/pooled/campaign-v4.json and insider-v1.json families (boolean-division masks)
- docs/alpha-data-augmentation-2026-10-18.md §3 'Method gaps'

### Recommendation

1. Create config/research/features/library-v1.json, SHA-pinned. Each entry records: id, expression or field source, units, visibility (D-1, D+1 or filing date), literature sign, citation, and OSAP/JKP acronym.

2. Seed it with signals computable from current data: Mom12m, Mom6m, IndMom, MomSeason, STreversal, MaxRet, IdioVol, Beta, DolVol, High52, PriceDelay, SUE, AnnouncementReturn, and the short-flow and insider fields. Add XBRL fundamentals with filed dates later.

3. DSL v2:
   - add cs_rank, cs_zscore, group_demean(x, sector), where(cond, x), ts_beta and ts_skew;
   - add mkt_ret and sector_ret fields;
   - evaluate at panel level in ScoreBook.

4. Vectorise the rolling operators with numpy or bottleneck, using the prefix tests as the oracle, and make log preserve units.

5. New sources become library columns, not new lanes. Use Qlib Alpha158 and OSAP definitions as the reference spec.

### North-star impact

Supplies the breadth that combination draws its power from. Sector-neutral and beta-adjusted features can then rank cards, and a new data source costs a set of columns rather than a 1–2-week lane.

### References

- Chen & Zimmermann (2022), Open Source Cross-Sectional Asset Pricing; openassetpricing.com Python package — Replicated signal definitions, code and literature signs (https://www.openassetpricing.com/)
- Jensen, Kelly & Pedersen (2023), Global Factor Data (153 characteristics, 13 themes) — Definitions and theme clustering for hierarchical priors (https://jkpfactors.com/)
- Microsoft Qlib (Alpha158/Alpha360) — A vetted vectorised expression set to benchmark against (https://github.com/microsoft/qlib)
- Heston & Sadka (2008), Seasonality in the Cross-Section of Stock Returns, JFE — Robust price-only signals that need cross-sectional operators

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding holds. The pooled DSL can only express single-series formulas. dsl.py:1-5 calls cross-sectional operators deliberately absent, and operators.py:108-169 provides only per-instrument operators. FIELD_UNITS (dsl.py:28-48) is one global, unit-only dict that each lane edits. Each new data source needs its own pinned protocol class (campaign.py:446-570). Scores are evaluated symbol by symbol (formula.py:125-130), and campaign-v4's rationale records that sector-relative momentum could not be expressed.

Four details are wrong or need qualifying:

1. **Cross-sectional helpers.** cross_sectional_rank and cross_sectional_zscore (panel.py:19-26) have no non-test caller. Only group_neutralize is used, at setups/features.py:189. Hand-written cross-sectional ranks exist in factor_controls.py:108-114, forecast_controls.py:55 and persistent_book.py:249, and an unused factor_neutralize in orthogonalization.py:165. None of these is reachable from the DSL. The pooled lane does have cross-sectional quantile filters (formula.py:59-76, 146-160) and a per-session top-k or top-decile selection. This is a partial substitute, but it cannot compose or neutralise features.

2. **Boolean-division masks.** These are in events-v1, short-v1 and insider-v1 and their cohort3 copies, not in campaign-v4. They are a documented convention. They exist because constants must be finite (dsl.py:111) and there is no where() operator that returns NaN.

3. **Slow rolling operators.** Only six operators use Python callbacks: ts_rank, ts_argmax, ts_argmin, decay_linear, ts_slope and ts_mad. ts_mean, ts_std, ts_sum, ts_max, ts_min, ts_corr and ema are vectorised. Measured against ts_mean, the callback operators are 20-360x slower (ts_slope 22x, ts_rank 77x, ts_mad 359x). For 8-formula fixed sets this barely matters; it matters only at the scale of a library or a search.

4. **log units.** log(close) and log(volume) do pass require_dimensionless, so price-level and size ranks get through the unit check. No frozen formula or mutation grammar uses log, so no past result was affected. The recommended fix, 'make log preserve units', is itself wrong: log(close) - log(delay(close,1)) would then carry price units and log returns would be rejected. The fix should require a dimensionless argument to log, or track log units that cancel when subtracted.

**Checked**

- agentic_trader/research/alpha/dsl.py:1-5: the docstring says cross-sectional rank and scale are 'deliberately absent from this series language'. Confirmed.
- agentic_trader/research/alpha/dsl.py:28-48: FIELD_UNITS is one global dict that carries only units. Comments show lane fields added per lane (events, short, insider). There is no source, visibility or sign metadata. Confirmed.
- agentic_trader/research/alpha/dsl.py:111: constants must be finite, so no operator can return NaN outside a condition.
- agentic_trader/research/alpha/operators.py:108-169: every operator works on one instrument's series; ts_corr takes two series of the same symbol. log is declared dimensionless at :125. Confirmed.
- Ran require_dimensionless (agentic_trader/research/pooled/formula.py:49-52) in the worktree venv: log(close), log(volume), sign(close) and ts_argmax(close,20) all pass as dimensionless. Confirmed.
- Searched for log( in config/research and in the mutation_operators of campaign-v3.json (decay_linear, delay, ema, ts_mean, ts_rank, ts_slope, ts_sum, zscore). No frozen formula or search grammar uses log, so no past result was affected.
- operators.py: these use Python callbacks via rolling.apply: ts_rank :15-19, ts_argmax/ts_argmin :49-54, decay_linear :65-67, ts_slope :138-144, ts_mad :151-152. These are vectorised pandas: ts_mean, ts_std, ts_sum, ts_max, ts_min, ts_corr, ema.
- Benchmark on 2,500 rows, window 20, relative to ts_mean: ts_slope 22x, ts_argmax 24x, decay_linear 26x, ts_rank 77x, ts_mad 359x. A vectorised rolling-cov slope ran about 6x faster than ts_slope and matched it to 1e-12.
- agentic_trader/research/alpha/panel.py:19-37 defines cross_sectional_rank, cross_sectional_zscore and group_neutralize. Confirmed.
- grep across agentic_trader/: group_neutralize is used only at agentic_trader/research/setups/features.py:189. cross_sectional_rank and cross_sectional_zscore are used only in tests/research/test_alpha_discovery.py.
- Hand-written cross-sectional ranks outside the DSL: agentic_trader/research/alpha/factor_controls.py:108-114, forecast_controls.py:55, persistent_book.py:249. orthogonalization.py:165 factor_neutralize handles market and sector, but nothing outside __init__ exports calls it.
- agentic_trader/research/pooled/formula.py:125-130: the per-symbol evaluation loop. Confirmed. formula.py:59-76 and :146-160 apply cross-sectional quantile filters, and selection is a per-session top-k or top-fraction (docstring :1-8).
- agentic_trader/research/pooled/scoring.py:114: ScoreBook exists and evaluates through evaluate_prepared.
- agentic_trader/research/pooled/campaign.py:446-570: each new source needs its own protocol class (EventInputs/EventsCampaignProtocol, ShortInputs/ShortVolumeCampaignProtocol, InsiderInputs/InsiderCampaignProtocol), each pinned with a fields_version Literal. This supports 'a new source costs a lane'.
- grep '/ (' config/research/pooled/*.json: boolean-division masks appear in events-v1.json:166,174,190,198, short-v1.json:200,208, insider-v1.json:200-248 and their cohort3 copies. campaign-v4.json has none. The insider-v1.json rationale documents the masks as a deliberate convention.
- config/research/pooled/campaign-v4.json rationale: 'sector-relative momentum has no group operator in the DSL'. Supports the gap.
- docs/alpha-data-augmentation-2026-10-18.md: §1 table (line 26), §3 'Method gaps' (line 62) and §4 step 5 (line 93) call for cs_rank, cs_zscore and group_demean. docs/alpha-stack-review.md:29,89,370 and docs/alpha-expansion-survey-2026-09-23.md:75 recommend cross-sectional operators. The claim of repeated requests holds.
- Web: openassetpricing.com confirms Chen-Zimmermann signals and code, and recommends the openassetpricing Python package, which is third-party (by Peng Li). jkpfactors.com confirms 153 characteristics in 13 themes. The github.com/microsoft/qlib README lists Alpha158 and Alpha360. Heston & Sadka (2008), JFE 87:418-445, confirmed.

**Bad references**

- config/research/pooled/campaign-v4.json is cited for boolean-division masks, but it contains none; its eight seeds are plain expressions. The masks are in events-v1.json, short-v1.json, insider-v1.json and their cohort3 copies.
- The Heston & Sadka (2008) citation is accurate, but the relevance claim ('price-only signals that need cross-sectional operators') overstates it. MomSeason is a per-stock same-calendar-month lagged return, and the pooled lane's existing top-decile selection already ranks it cross-sectionally. Its 20-annual-lag form also exceeds the cube's history from 2016 and MAX_LOOKBACK=2520 (operators.py:12).

**Notes**

The improvement is legitimate and well motivated: three separate docs ask for cs_rank, cs_zscore and group_demean, and each source currently costs a protocol class plus its own fixed set.

Three points the reviewer should weigh:
- **Seed list overlaps tested signals.** STreversal (-roc(close,5)) and Mom12m (delay(close,21)/delay(close,252)-1) are campaign-v4 families. High52 is the separate high52-v1 entry, which v4 deliberately excluded. A library that re-scores them on the same windows needs the existing trial/holdout accounting, so the closed results are not reopened silently.
- **Gap between the fields and their metadata.** The fields' visibility is documented as free text (protocol rationales; module docstrings of events.py, short_fields.py, insider_fields.py), and versioned through the fields_version Literal. It is not attached to FIELD_UNITS. The 'no metadata' point applies to the DSL field registry, not to the system as a whole.
- **Benchmark scope.** The speed measurements come from one short in-process benchmark on synthetic data in the worktree venv. No repo files were changed.

### Value review — P1, next (weeks)

**Reasoning**

Fund it, but only a thin slice. It is P1 because it is the prerequisite for the most plausible remaining mining trial: one pre-registered composite of literature-signed weak signals. The proposal as written, though, is overscoped, and some of its claims are cosmetic.

Why it matters now:
- The pooled record is 64 single-formula nulls. Their t-stats have mean −0.06 and sd 1.25, which is indistinguishable from check C's calibrated null (seed-t sd 1.11–1.27; docs/alpha-pooled-cohort3-2026-10-16.md "Check C").
- The lane's minimum detectable edge is about 0.065R at the median standard error. Single post-publication anomalies in liquid US names plausibly sit below that.
- The documented way out is combination (Gu-Kelly-Xiu 2020; Jensen-Kelly-Pedersen 2023), and this repo cannot express it. The DSL is per-symbol only (dsl.py:1-5; operators.py:108-169). Formulas are evaluated per symbol in a loop (research/pooled/formula.py:125-130). The cross-sectional helpers live only in alpha/panel.py:19-37. The operators have been recommended and deferred three times (docs/alpha-data-augmentation-2026-10-18.md §3 and §4 step 5).

Where the claimed value is overstated:
1. `cs_rank` or `cs_zscore` on a single formula changes nothing. `select_top_fraction` already ranks each session's eligible names (formula.py:195-215), so a monotone per-session transform picks the same decile. All of the value is in composites, sector demeaning and beta residualisation. Sector demeaning needs sector labels that cohort v3 does not have.
2. The boolean-division masks work as intended: `safe_div` turns a zero denominator into NaN (operators.py:65-67). `cond` already exists (operators.py:76-77, 125-127). This is readability, not a bug.
3. The `log` unit hole (operators.py:125) only lets price-level formulas through `vet_expression`. That matters for genetic search, which is parked. Fixed sets are written by hand.
4. Speed is not the binding constraint yet. Check C on a fixed set runs in 42 s (docs/alpha-pooled-checks-v4-2026-10-07.md), and check A does not depend on the formulas. Vectorising the rolling operators can wait until profiling shows a need.
5. "DSL v2 + panel-level ScoreBook + vectorisation + library + sources-as-columns" is effort L, not M. It overlaps the separate FieldSource plugin item.

Impact on the north star is indirect. No pooled formula has a live scoring path, so cards change only if:
- a composite passes and is wired to a live path (a probe or a native-card ranker); or
- its percentile is recorded on native card candidates as descriptive provenance.

The DSL being shared between research and live makes the second route cheap.

It is not P0 for two reasons:
- The measuring instrument has known confounds that would also hit a composite. The equal-weighted control is not risk-matched, the cube stores no ATR, beta or sector, and no per-formula detectable-effect size is reported.
- The card-stream items (the L3 selector and the native-versus-universe control) are closer to Telegram.

The composite will also likely spend the last clean historical confirmation window, so it has to be governed as exactly one trial.

**Risks**

1. **Refactor sink.** A DSL v2 grammar plus panel evaluation plus vectorisation could take 3–4 weeks with no card impact unless a composite passes.
2. **Menu effect.** A library of more than 15 columns invites more single-formula fixed sets, which burns multiple-testing budget and the single lane-wide confirmation window. The library has to come with a rule that the composite is one charged trial.
3. **Look-ahead and survivorship.** Cross-sectional statistics must use only names eligible at D−1 (`view.eligible`, formula.py:205). Today `ScoredFormula` builds panels with an all-ones mask (scoring.py:109). Z-scoring over the whole frozen cohort would let survivorship and not-yet-listed names into the cross-section. The prefix tests are a time-series oracle only, so cross-sectional prefix and eligibility tests are needed.
4. **Small effect.** A composite of decayed anomalies in liquid names may still land at an IC of about 0.01–0.02, or 0.02–0.04R. That is below the t≥3 detectable effect, so it could produce a 65th null unless the control and gate fixes come first (pooled map §7.1, 7.2 and 7.7).
5. **Low-volatility confound.** Literature signs tilt the composite toward low-volatility names (IdioVol, MaxRet, Beta). Against an equal-weighted control on a volatility-scaled R label, that interacts in a way nobody has tested.
6. **Sign leakage.** Signs must come from the papers and OSAP, never from this repo's observed t-stats.
7. **Contaminated families.** `excluded_families` (insider-v1.json:40-) lists `vol_20` and `sector_relative_momentum`, both read 2021–2026. A composite that includes them, or PEAD (read 2016–2026), contaminates the confirmation window.
8. **Sector coverage.** No authoritative sectors exist for cohort v3, so `group_demean` and IndMom depend on a new SIC mapping.

**Prerequisites**

1. Sector labels per symbol. Use SIC from EDGAR submissions through the insider lane's existing ticker-to-CIK map, frozen and digest-pinned.
2. A market-return field (SPY daily returns from the existing bar cache, read at D−1) for Beta, IdioVol and residual momentum.
3. The eligibility mask exposed to score construction, so cross-sectional operators normalise only over names eligible at D−1.
4. Ideally first: per-formula detectable-effect and confidence-interval reporting from check C plants, plus risk arrays (`atr_pct`, beta, sector) in the cube for a risk-matched control. Without them the composite's verdict is no more readable than the 64 nulls.
5. An operator ruling on whether `excluded_families` and the PEAD/exdiv reads bar a composite from those families. Then explicit operator approval to spend the confirmation window on this one trial, or a decision to confirm it prospectively.

**Better alternative**

A roughly one-week thin slice instead of DSL v2:

(a) Apply `cs_zscore`, `cs_rank` (winsorised) and `group_demean` to the [S,N] score arrays in pooled scoring, after `evaluate_prepared` and masked to D−1 eligibility. Do not change the series DSL grammar. Add a `composite` protocol type: a list of (library id, literature sign) pairs, z-scored per session and equal-weighted.

(b) Write `config/research/features/library-v1.json`, SHA-pinned in the protocol, with about 12 OSAP-defined price and volume signals that the current DSL can already express, plus `mkt_ret`. Candidates: Mom12m, Mom6m, MomSeason via `delay`, STreversal, MaxRet, High52, DolVol, Beta, IdioVol and PriceDelay. Give each one units, visibility, sign and citation. Defer XBRL, vectorisation and sources-as-columns; the last belongs to the FieldSource plugin item.

(c) Pre-register exactly one equal-weight composite and run it through checks A and C, with per-formula detectable-effect reporting. Also enroll it in a prospective pooled scorer, so a pass does not depend solely on the contaminated 2024–26 window.

(d) Record the composite's percentile for each native card candidate in `decision_provenance` as descriptive context. That is the cheapest route from this work to Telegram.

Fix `log` units in the same PR because it is one line. Skip the masking-syntax change.

---

<a id="c25"></a>

## C25 — Test the earnings-announcement premium: a long-only, days-long, large-cap effect that the 7-day earnings blackout structurally excludes

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | next (weeks) | signals | research_direction | high | M | partially_confirmed |

_lenses: literature_

### Claim (as consolidated)

Today, every equity candidate within 0–7 days of a report is refused (config.yaml:279; rules.py:376-384), and catalog probes get no exemption (evaluator.py:543-571). The desk therefore never holds a long into a scheduled announcement. Its only earnings source is post-event PEAD at D+2.

The literature points to the announcement window itself:
- Frazzini-Lamont: prices rise around scheduled announcements, and monthly strategies earn 7–18%/yr. The effect is strong in large caps: about 25 bp over the 10 days before, 21 bp in the 3-day window and 30 bp after (t > 7). Dates are predicted from the prior year.
- Barber et al.: significant in 9 of 20 countries.
- Savor-Wilson: 9.9% annualised.
- Martineau: large-cap PEAD has been gone since 2006, which favours the pre- and at-announcement legs over post-event drift.

Arithmetic: 46 bp is about 0.11R gross at a 4.2% risk unit, and about 0.05R after a 58% haircut. That is comparable to PEAD's measured edge.

### Evidence

- config/config.yaml:279; agentic_trader/risk/rules.py:376-384; agentic_trader/agent/evaluator.py:543-571
- agentic_trader/agent/copilot.py:897-948; docs/apriori-pead-2026-09-26.md header
- agentic_trader/research/apriori/earnings_history.py:1-10 (pinned Nasdaq pages)
- docs/apriori-exdiv-2026-10-12.md:53-54 (risk unit 4.2%)

### Recommendation

Freeze an a priori entry, ea-premium-v1, on the PEAD/exdiv machinery:
- Predict the announcement session E from the same fiscal quarter's report one year earlier, using the pinned Nasdaq pages.
- Go long at 10:35 on E−5.
- Exit with a session_count exit at the close of E for before-open reports, or E+1 for after-close reports.
- Use a 2xATR stop with gap-at-open fills.
- Controls: eligible non-announcers in the same session, plus a same-name placebo 30 sessions earlier.
- Declare the overlap with the PEAD and exdiv reads.

A pass would need two further things: an operator decision exempting EA catalog probes from earnings_blackout, and a generic catalog seam (C21).

Test any proposed 'exit held positions before earnings' rule against this premium before adopting it.

### North-star impact

Adds a second evidence-based, long-only card source whose timing is known days ahead and fits the 10:35 slot, using data the desk already pins.

### References

- Lamont & Frazzini (2007), The Earnings Announcement Premium and Trading Volume, NBER WP 13090 — 7–18%/yr, strong in large caps, with dates predicted from the prior year (https://www.nber.org/papers/w13090)
- Barber, De George, Lehavy & Trueman (2013), The earnings announcement premium around the globe, JFE 108(1) — Out-of-sample cross-country support
- Savor & Wilson (2016), Earnings Announcements and Systematic Risk, JF 71 — Risk-based premium that is less likely to be arbitraged away (https://doi.org/10.1111/jofi.12361)
- Martineau (2022), Critical Finance Review 11(3-4) — Large-cap PEAD gone since 2006; favours pre- and at-announcement legs

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding holds:
- **The blackout.** For every equity candidate, the scan refuses the entry when an upcoming report is 0–7 calendar days away (config.yaml:279; rules.py:376-384; evaluator.py:543-571). The tap gate refuses it again (copilot.py:3256-3259). Catalog probes get no exemption.
- **The desk's only earnings signal is post-event PEAD at D+2** (copilot.py:897-948; docs/apriori-pead-2026-09-26.md).
- **The window has never been tested.** No announcement-window or pre-announcement premium has been tested anywhere: events-v1 holds only post-report decay formulas and quiet-window formulas.

Corrections:
1. **"The desk never holds a long into a scheduled announcement" is wrong.**
   - The blackout only refuses new entries, at scan and tap time. Admission does not apply it (rules.py:430-434), and it fails open when the calendar is unavailable (evaluator.py:556-563; copilot.py:3218-3225).
   - There is no exit-before-earnings rule, and native positions have no configured time exit. A long entered more than 7 calendar days before a report can therefore be held through the announcement, incidentally and without measurement.
   - The accurate statement: the desk never deliberately enters within the 7 days before a report.
2. **The 25/21/30 bp figures (t > 7) come from a different test than the strategy.**
   - They are from Frazzini–Lamont Table VII, 1973–2004. That sample is stocks above the CRSP median market cap, which is not strictly "large caps".
   - They use actual Compustat dates, and the authors say this event-time view "does not reflect a tradable strategy". They are not from the predicted-date strategy.
3. **The proposed trade does not capture 46 bp.**
   - The 46 bp figure is days [-10,-2] plus [-1,1]. A trade from E−5 to the close of E/E+1 spans only about CAAR(-5..+1).
   - By Table VII that is ≈ 0.460% − 0.089% ≈ 37 bp, or ≈ 34 bp with an exit on day 0.
   - That is about 0.08–0.09R gross at a 4.2% risk unit, and about 0.035–0.04R after a 58% haircut. The finding's 0.11R and 0.05R are too high.
   - This is an abnormal return against non-announcers, so it is comparable to PEAD's +0.055R paired difference, not to PEAD's +0.149R absolute edge.
4. **The finding omits directly relevant counter-evidence.** Heitz, Narayanamoorthy & Zekhnini (2020 WP) report that the US earnings-announcement premium has disappeared in recent years, while it persists internationally. That is the same post-2004 regime shift the finding uses (via Martineau) to argue in favour of the EA legs. Martineau's own claim concerns PEAD only; it does not show that a pre-announcement premium survives.

Revised verdict: freezing ea-premium-v1 on the pinned Nasdaq pages is still a cheap, well-posed predeclared test. The expected effect is small, and recent US evidence says it may be zero, so impact should be rated medium or speculative rather than high.

**Checked**

- config/config.yaml:279: earnings_blackout_days: 7 is confirmed
- agentic_trader/risk/rules.py:376-384: earnings_days_out counts 0..blackout_days calendar days to the next report; confirmed
- agentic_trader/agent/evaluator.py:543-571: the equity blackout runs on every candidate, with no check of candidate.catalog_event or probe (catalog handling is only at evaluator.py:858-870, after the gate). Lookup failure fails open (evaluator.py:556-563)
- agentic_trader/agent/copilot.py:3212-3227,3256-3259: the same blackout is re-applied at tap time for every equity; it fails open (copilot.py:3218-3225)
- agentic_trader/risk/rules.py:430-434: admission applies neither the earnings blackout nor the corporate_action gate
- agentic_trader/agent/earnings.py:285: a report dated today counts as ahead unless it is pre-market
- Repo-wide grep of execution/, broker/, notifier/ and config: there is no rule that exits held positions before earnings, and config.yaml has no native max-hold or time exit
- agentic_trader/agent/copilot.py:897-948: the PEAD drift path at the 10:35 decision is confirmed
- agentic_trader/config.py:172-184: AprioriConfig enabled by default with pead-v2 entry
- docs/apriori-pead-2026-09-26.md header: PEAD long leg enters at 10:35 on D+2, +0.149R per trade, +0.055R over control; confirmed
- config/research/pooled/events-v1.json families: only post-report PEAD-decay, quiet-window momentum/reversal and dividend_yield. No pre-announcement or announcement-window formula
- grep of docs/config/agentic_trader for 'announcement premium', 'Frazzini' and 'Savor': no earlier EA-premium test exists
- agentic_trader/research/apriori/earnings_history.py:1-10: paced, SHA-256-pinned Nasdaq calendar pages; confirmed
- docs/apriori-exdiv-2026-10-12.md:53-54: median risk unit (2xATR14) is 4.2% of price; confirmed
- NBER w13090 PDF (extracted text): abstract; p.3 '7% and 18% per year', 'strong in large capitalization stocks'; p.6 dates predicted from the previous year; p.16-17 and Table VII
- Web: Savor & Wilson JF 2016, 71:83-138, 9.9% annualized; confirmed
- Web: Barber et al. JFE 2013, nine of 20 countries significant; confirmed
- Web: Martineau CFR 2022 11(3-4):613-646, PEAD non-existent for large caps since 2006; confirmed
- Web: Heitz, Narayanamoorthy & Zekhnini (2020 WP), 'The Disappearing Earnings Announcement Premium', reports the US EAP has disappeared in recent years

**Bad references**

- Lamont & Frazzini (2007) NBER WP 13090 (partly misused): the 25/21/30 bp, t > 7 numbers come from Table VII, 1973–2004, on above-median-cap stocks using actual Compustat dates, which the authors call not a tradable strategy. They are not results of the predicted-date strategy the finding describes. The 7–18%/yr figure, 'strong in large capitalization stocks' and the prior-year date prediction are accurately cited.
- Martineau (2022) CFR 11(3-4): the citation exists and supports 'large-cap PEAD gone since 2006'. It does not support the finding's inference that it favours pre- and at-announcement legs; the paper says nothing about an announcement premium.

**Notes**

All four references exist with the cited venues and headline numbers. Checked: Savor-Wilson 9.9% annualised (JF 71:83-138); Barber et al. nine of 20 countries significant (JFE 2013, 108(1):118-138); Frazzini-Lamont 7–18%/yr. The repo has no earlier EA-premium entry (config/research/apriori/ holds only pead-v1, pead-v2 and exdiv-v1).

The finding's main weaknesses:
- It overstates "never holds into announcements". The blackout gates entries only, and nothing forces an exit before a report.
- It mislabels the event-time evidence as coming from the predicted-date strategy.
- It sizes the trade on a window it does not hold.
- It omits the Heitz et al. evidence that the US premium has disappeared.

The design suggestion itself is sound and consistent with the existing machinery. That machinery comprises session_count exits, 10:35 decisions, the 2xATR conservative labeller and pinned Nasdaq pages with an EarningsTiming field. One practical caveat: predicting E from the prior year's report means decisions can start only about a year after the 2016-02-23 calendar start, which itself has a 2016-06/07 gap (docs/apriori-pead-2026-09-26.md, Protocol).

The Frazzini-Lamont PDF text used for checking is at ~/agentic-trader-research/holistic-review-20261021/fl/fl.txt (Table VII is at about lines 1783-1830).

Sources: https://www.nber.org/system/files/working_papers/w13090/w13090.pdf ; https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf ; https://www.iimb.ac.in/ARC2020/Papers/The_Disappearing_Earnings_Announcement_Premium.pdf ; https://www.london.edu/faculty-and-research/academic-research/t/the-earnings-announcement-premium-around-the-globe ; https://academicnewsletter.sufe.edu.cn/info/359814

### Value review — P1, next (weeks)

**Reasoning**

Fund the research study now and gate all live work on a pass. My reasons are below, with where each comes from.

**Why fund it**
- **The catalog lane is the desk's only route that has passed.** The 64 pooled formula-cohort pairs, both spread versions and the bracket counterfactual all came back null. In the a priori catalog, PEAD LONG passed at +0.149R and beat its control by +0.055R (docs/apriori-pead-2026-09-26.md, header). exdiv failed only on its paired test, P2 (docs/apriori-exdiv-2026-10-12.md, "Result").
- **The earnings-announcement premium has the strongest prior of any untested catalog candidate.** It replicates out of sample across countries (Barber et al. 2013), and Savor-Wilson give it a risk-based reason to persist.
- **The finding describes the structural exclusion correctly, and it is wider than stated:**
  - All equity entries are refused within 0–7 days of a report (config/config.yaml:279; agentic_trader/agent/evaluator.py:547-571). That check looks only at asset class, the configured days and the calendar, with no probe exemption.
  - The tap gate repeats the refusal (agentic_trader/agent/copilot.py:3226,3258).
  - So the desk can never hold into a scheduled announcement.
- **It is cheap and needs no new data.**
  - The study is close to a clone of exdiv: the same event-known-in-advance shape, long E−5 into the event, a same-name placebo, and a dispatch on the entry id (agentic_trader/research/apriori/exdiv_events.py, exdiv_runner.py, exdiv_study.py).
  - It uses the existing pinned Nasdaq page cache. The PEAD run left 2,615 pages and 23,983 liquid events (docs/apriori-pead-2026-09-26.md, "Scale").
  - Parsed rows already carry `fiscal_quarter` and BMO/AMC `timing` (agentic_trader/agent/earnings.py:29-34,138-148). That is what the prior-year date prediction and the exit rule need.
- **The event count gives real power on P2.** That is roughly 9× exdiv's 2,647 events, which failed on a paired interval about ±0.05R wide.

**Why P1 and not P0**
- **The expected edge is thinner than the finding's arithmetic.**
  - The proposed hold (E−5 to E or E+1) captures about half the 10-day pre-window plus the 3-day window: roughly 33 bp excess, not 46.
  - After the 58% decay haircut that is about 0.03R against non-announcers.
  - P1 (absolute mean) will pass on market drift, exactly as exdiv's did. P2 decides the result, and +0.03R is near its detection limit once announcement-day jumps widen the variance.
- **The live path is not ready.** A pass needs three things:
  - an operator decision to exempt catalog probes from the blackout, in both the evaluator and the tap gate;
  - a generic catalog seam, because PEAD is hard-coded in `run_scan` (agentic_trader/agent/copilot.py:626,904,1273-1279, from maps/signals.md item 11; this is the L3 selector seam in docs/alpha-roadmap.md:494);
  - the PEAD probe's first-term evidence. The probe expires 2026-10-28 (docs/alpha-roadmap.md, item 3).
- **The Martineau argument does not hold here.** The desk's own conditioned PEAD leg passed on 2016–2026 data. Fund this on its own prior, not as a replacement for PEAD.

**Design changes to freeze before any read**
1. Always exit at the close of E+1 (it covers both timings), and record `time-not-supplied` coverage as a descriptive. docs/alpha-data-augmentation-2026-10-18.md:44 says report time is often unknown.
2. Add a second arm, pre-announcement only (E−5 to E−1 close). It has no jump variance, so it has better power, and it would fit the existing blackout semantics better. Give both arms Holm slots.
3. Make the live rule identical to the research rule, which uses the predicted date. Never condition on whether the date was confirmed: late announcers carry bad news (Johnson & So, "Time Will Tell"), so selecting on-schedule announcers after the fact biases the result upward.
4. Report SPY beta, as Savor-Wilson imply. The desk's problem is that cards trail SPY.

Also do what the finding's last point says: test any future "exit before earnings" rule against this premium before adopting it.

**Risks**

- **P2 power.** The plausible paired edge is about 0.03R after decay. Announcement-day gaps (2×ATR stop with gap-at-open fills, so losses can run to −2R or −3R) widen the paired interval. A null result is the most likely outcome, and it would add one more closed lane.
- **Research and live populations differ.**
  - The historical pages are ex-post, so research must use dates predicted from the prior year, and mispredictions dilute the effect.
  - Live sees confirmed dates. Early confirmers skew to good news, so if the live rule drifts from the predicted-date rule, its performance is unvalidated.
- **The overlap with PEAD and exdiv must be declared,** or a pass can double-count the same large-cap drift. PEAD enters at D+2, so it runs in sequence on the same name rather than overlapping.
- **Card flow will be seasonal.** There are four earnings seasons a year and one probe card per session outside the native budget, so this adds bursts, not a steady stream.
- **A pass creates pressure on the risk side.** It means relaxing a deliberate operator risk control, the blackout, and that touches both the evaluator and the tap gate. The exemption must stay limited to catalog probes. It must never leak to native technical setups, which have no evidence for holding through a binary event.
- **The historical timing field is sparse.** If the protocol branches on BMO/AMC, unknown rows need a frozen fallback.

**Prerequisites**

- **Before any outcome read:**
  - Write a frozen spec and `config/research/apriori/ea-premium-v1.json`, with its SHA in the manifest, on the exdiv machinery.
  - Freeze the date-prediction rule: same fiscal quarter, one year earlier, from the pinned Nasdaq pages. Freeze the E+1 exit, both arms (through-announcement and pre-only), both controls (same-session non-announcers and a same-name placebo 30 sessions earlier), and the P1–P4 thresholds.
  - Declare the overlap with PEAD and exdiv, and give it a Holm slot in the confirmation family.
  - Use a clean revision.
- **Before any live work:**
  - The PEAD probe's first-term evidence and the operator's renewal decision (expiry 2026-10-28).
  - The generic catalog candidate-source seam: L3, or C21.
  - An explicit operator decision to exempt catalog probes only from `earnings_blackout`, in both the evaluator and the tap gate.

**Better alternative**

Run it as a research-only exdiv clone with two predeclared arms: pre-announcement only (E−5 to E−1 close) and through-announcement (exit at the E+1 close). Defer the blackout exemption and the catalog seam until one arm passes P2.

The pre-only arm is the cheaper, higher-power version. It avoids announcement-day jump variance, and if it passes, it needs only a narrow blackout exemption: it exits before the report. That is a much smaller operator risk change than holding through it.

In the meantime, put live engineering time into the PEAD probe renewal and the L3 selector seam. A pass from this study would need both anyway.

---

<a id="c31"></a>

## C31 — There is no north-star scorecard: the evidence journal is untyped and unversioned, and the funnel's denominators are not journaled

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | product | gap | medium | M | partially_confirmed |

_lenses: architecture, product_

### Claim (as consolidated)

The evidence journal is untyped and unversioned:
- decision_provenance is a dict of about 20 keys, and scan_candidates_ranked is untyped (copilot.py:1462-1491, 1851-1931).
- WorkflowStore.append stamps schema_version=1 on every event (workflow.py:155), although payloads have since gained llm, news, card_policy, book_sizing, regime and trigger blocks.

The funnel's denominators are missing:
- The ranked journal is written only when native_count > 0 (copilot.py:1591).
- Collect-phase rejections go only to logs (:1753-1766).
- 12 of 39 scheduled 10:35 slots have no event at all.

Monitoring and feedback are thin:
- Prometheus covers process health only.
- Execution feedback is limited to mean fill_slip_r and tap age (execution_evidence.py:264-280).
- Arrival quotes are journaled but never used (workflow.py:604-614).

Nothing tracks base-rate calibration, selection edge versus random, book beta or tap rate.

### Evidence

- agentic_trader/agent/copilot.py:1462-1491, 1591, 1753-1766, 1851-1931
- agentic_trader/storage/workflow.py:155, 604-614
- agentic_trader/research/setups/execution_evidence.py:264-280
- Live map Q5 (12 of 39 10:35 slots without an event)

### Recommendation

Type the journal:
- Model each event kind as a pydantic discriminated union whose schema_version is bumped whenever its payload changes.
- Validate at write time; readers dispatch on (kind, version).

Journal the whole funnel:
- Journal every scheduled scan, including empty and gated ones, with rejection counts per RiskRule.
- Add Prometheus funnel counters: raw, admitted, refused per rule, LLM outcome, withheld, sent.

Publish a weekly scorecard to the Telegram digest and to cards outcomes:
- Supply: % of slots journaled; cards per slot by source; % of sessions with at least one card.
- Quality: de-duplicated, beta-adjusted, shrunk excess R by source with a 90% interval; selection edge versus a random candidate from the same scan.
- Honesty: Brier score and binned calibration of the printed target rate; printed versus realised EV.
- Allocation: book beta-dollars; maximum ρ to the book; share of cards changed by sizing or capacity.
- Operator loop: tap rate; time to tap; fill rate; label R of filled versus unfilled cards; implementation shortfall against the arrival quote.
- Noise: ops notices per card.
- Research: share of PRs that change a card source or selection.

### North-star impact

Turns the 'steady stream of high-quality cards' into a measured objective with denominators, so every decision is judged by whether it moved these numbers.

### References

- Brier (1950), Verification of Forecasts Expressed in Terms of Probability, Monthly Weather Review 78 — Calibration of the printed target rate
- Perold (1988), The Implementation Shortfall, JPM 14(3) — Card-to-fill cost decomposition from journaled evidence
- Pydantic discriminated unions — Versioned, validated heterogeneous payloads (https://pydantic.dev/docs/validation/latest/concepts/unions/)

### Fact check — partially_confirmed

**Corrected claim**

The core gap is real, but several details are wrong.

What holds:
- The scan journal and decision_provenance are untyped dicts. See copilot.py:1459-1489 and 1871-1931, db.py:528 (dict[str, Any]) and models.py:49 (Text column).
- WorkflowStore.append hard-codes schema_version=1 (workflow.py:155). Readers only refuse anything other than 1 (ledger.py:157, operations.py:107, alpha.py:750). Payload growth (trigger, regime, llm, news, card_policy, book_sizing) is handled by key presence, not by a version bump.
- scan_candidates_ranked is written only when native_count > 0 (copilot.py:1591). This is narrower than the CLAUDE.md contract, which says "every unrestricted non-dry scan with a budget journals scan_candidates_ranked".
- Collect-phase risk rejections are logged only (copilot.py:1754-1766). The scan summary does not count them by rule (copilot.py:1113-1127).
- The arrival quote is journaled in ENTRY_SUBMITTING via broker_context (workflow.py:611-614). It is used only at submission, for the capacity check (capacity.py:233-234, 267, 292), and is never read back. fill_slip_r is measured against the card's planned entry (execution_evidence.py:254), not against the arrival quote.
- Nothing computes calibration (Brier or reliability) of the printed card_stats target rate against realised outcomes.
- Nothing computes portfolio or book beta.
- There are no Prometheus funnel counters.
- There is no scorecard.

Corrections:
1. Slot counts. The 39 slots are both scheduled times: 20 sessions × 2, minus 2026-10-21's 14:35, which was not yet due. They are not 39 slots at 10:35. Twelve lack a scan_candidates_ranked event: 6 of 20 at 10:35 and 6 of 19 at 14:35. Ten of those twelve do have a dynamic_universe_built event carrying the scan_id, and five of them also have a pead_decision event. So the slot ran and is identifiable; only its candidate and rejection counts are missing. Only two slots have no event at all: 09-24 at 14:35, and 09-28 at 10:35, which was lost to host sleep. The end-of-session digest (copilot.py:4293-4350) also keeps aggregate scanned/candidates/sent/runners-up counts durably, as outbox text, but not per scan or per rule.
2. Selection edge versus random is already tracked. cards outcomes reports top-1 and top-2 per-scan R for setup_quality and shadow_score against a random pick (outcomes.py:645-675, 937-942). Base rates (target, stop and timeout rate, mean R) are tracked for sent cards and runners-up (outcomes.py:677-687, 919-922). They are printed on cards through card_stats (card_stats.py:43-60). Only the calibration of the printed rate is missing.
3. Execution feedback covers more than mean fill_slip_r and tap age. It also reports cards, entered, filled, missing fill evidence, repriced and fill_source counts (execution_evidence.py:266-279), and tap_r_consumed per row (:256), so a fill rate exists. A taps-per-card rate and implementation shortfall against the arrival quote do not.
4. Prometheus is mostly operational, but it is not limited to process health. It also exports trader_orders_filled_total by exit_reason (copilot.py:2351-2355), trader_active_positions_count (:2495), trader_scan_duration_seconds (:1637) and alpha observer/panel counters (service.py:155-199).
5. Not every journal event is untyped. BrokerOrderObservation is a frozen pydantic model with schema_version Literal[1] (durable.py:120-140), and the card_stats snapshot is typed (card_stats.py:43). The untyped part is the scan and card evidence: scan_candidates_ranked, decision_provenance and the tap and submission evidence.

**Checked**

- agentic_trader/agent/copilot.py:1459-1489 (decision_provenance dict literal, ~20 keys incl. **evidence_keys, dynamic tag, validity)
- agentic_trader/storage/db.py:528 (record_signal decision_provenance: dict[str, Any]); agentic_trader/storage/models.py:49 (stored as Text)
- agentic_trader/agent/copilot.py:1851-1931 (_journal_scan_ranking builds an untyped dict payload with llm/news/card_policy/book_sizing/regime/trigger blocks)
- agentic_trader/storage/workflow.py:141-158 (append hard-codes schema_version=1); readers fail closed on !=1 at storage/ledger.py:157, storage/operations.py:107, storage/alpha.py:750
- agentic_trader/execution/durable.py:120-140 (BrokerOrderObservation is a typed pydantic model with schema_version Literal[1]); research/setups/card_stats.py:43-60 (CardKeyStats typed snapshot)
- agentic_trader/agent/copilot.py:1178-1181, 1591 (ranked journal requires native_count > 0) vs CLAUDE.md contract 8 'Every unrestricted non-dry scan with a budget journals scan_candidates_ranked'
- agentic_trader/agent/copilot.py:1113-1127 (collect loop counts only duplicates and account-risk errors) and :1754-1766 (risk-engine rejection logged only)
- agentic_trader/agent/copilot.py:2184-2220 (dynamic_universe_built payload: dynamic portion only, with scan_id)
- agentic_trader/agent/copilot.py:4293-4350 (end-of-session digest text with scanned/candidates/sent/runners-up, durable via outbox)
- Read-only DB query of domain_events (scan_candidates_ranked, dynamic_universe_built, pead_decision) 2026-09-24..2026-10-21
- agentic_trader/telemetry/collector.py; all inc_counter/set_gauge/observe_histogram sites (telegram_bot.py, readiness.py, event_loop.py, copilot.py:571,1637,2351,2490-2499,4170-4178, cli/commands/service.py:155-199)
- agentic_trader/research/setups/execution_evidence.py:230-280 (rows incl. tap_r_consumed; summary has cards/entered/filled/missing_fill_evidence/repriced/fill_source/mean_fill_slip_r/mean_tap_age_seconds)
- agentic_trader/storage/workflow.py:604-614 (ENTRY_SUBMITTING evidence includes broker_context with EntryQuoteEvidence); execution/capacity.py:233-234,267,292 (quote used at submission only); no reader of the journaled quote anywhere
- agentic_trader/research/setups/outcomes.py:645-700, 857-1010 (selection by setup_quality/shadow_score vs random; base rates for sent vs runner-up; market_r/excess_r exposure; book_sizing counts)
- grep for brier/calibration in card paths (none), 'beta' in risk/ (none), 'scorecard' in docs/code (none)
- Brier 1950 MWR 78(1):1-3 (web search)
- Perold 1988 JPM 14(3):4-9 (web search)
- https://pydantic.dev/docs/validation/latest/concepts/unions/ (fetched; has 'Discriminated unions' section)

**Bad references**

_none_

**Notes**

I ran one read-only psql query (default_transaction_read_only, statement_timeout) against the production DB. It looked only at domain_events kinds and timestamps, to check "Live map Q5".

Ranked-scan events, raw recorded_at in UTC (EDT = UTC−4), 2026-09-24 to 2026-10-21:
- 10:35 slot: 14 events.
- 14:35 slot: 13 suggestion events.
- Swing scans: 4 more events.

Ranked event missing for:
- 10:35: 09-28, 09-29, 10-12, 10-15, 10-16, 10-19.
- 14:35: 09-24, 10-09, 10-12, 10-14, 10-15, 10-16.

dynamic_universe_built exists for every slot except 09-24 at 14:35 and 09-28 at 10:35. pead_decision exists for every 10:35 slot from 09-29 onward.

All three references check out:
- Brier 1950, Monthly Weather Review 78(1):1-3.
- Perold 1988, Journal of Portfolio Management 14(3), starting p.4.
- The pydantic.dev unions page exists and has a "Discriminated unions" section.

Cited line ranges are accurate to within a line or two. copilot.py:1462-1491 sits inside the decision_provenance literal (1463-1489). The rejection log is at 1754-1766.

The recommendation is sound. Two parts are not new, though: selection-vs-random and base rates already exist in cards outcomes and should be extended rather than proposed as new. Also, the 'native_count' gate contradicts the CLAUDE.md contract wording, so journaling empty and gated scans is a contract fix, not just an enhancement.

### Value review — P1, now (days)

**Reasoning**

This finding bundles three items of very different value. I would fund one, thin down another and drop the third.

1. **Funnel denominators (fund now; it is the core of the finding).**
   - The ranked journal is written only when `native_count` is non-zero (agentic_trader/agent/copilot.py:1591).
   - Collect-phase risk refusals go only to a log line, although `rejection_rule` is already in hand (copilot.py:1753-1766; evaluator.py:437).
   - The live map counts 12 of 39 scheduled 10:35 slots with no event (maps/live.md gap 2, Q5). The per-day raw counts sit in an in-memory digest that a restart truncates (copilot.py:4293; docs/production.md "Suggestion scans").
   - `corporate_action` and `book_sizing` moved to enforce on 2026-10-20, and earnings blackout is 7 days, so the supply side of "steady stream" can't be measured right now. When a slot produces no card, nobody can tell whether there were no raw setups or whether blackout, a cap or a corporate action took them.
   - The fix is small: one event for every non-dry scan, with trigger, slot, raw count and a refusal count per RiskRule. The supply fixes in the live map (swing-scan budget, return-correlation dedup; maps/live.md recommendations 3-4) can only be verified with these denominators.

2. **Weekly scorecard (worth doing only as a thin rendering).** Most of the quality block already exists in `cards outcomes`:
   - selection versus a random pick and versus scorers (research/setups/outcomes.py:645-675, 930-934);
   - excess over the market control (outcomes.py:383, 690-697);
   - the LLM gate, book sizing, news, regime/recurrence and the bracket counterfactual (outcomes.py:706-855).

   The genuinely missing pieces are the operator loop and calibration:
   - tap rate and time to tap: 9 taps out of 42 cards, 33 untapped expiries;
   - notices per card: 659 incident notices against 41 cards (maps/live.md gap 9);
   - calibration of the printed target rate.

   These belong as extra fields in the existing summary, plus one digest message. With about 40 sent cards and 3 closed trades, Brier scores and implementation shortfall are noise. The 87 counterfactually labelled candidates are the only usable sample for quality.

3. **Typing the whole journal as a discriminated union with per-kind version bumps (drop as stated).**
   - About 27 event kinds share durable contracts.
   - Readers fail closed on `schema_version != 1` (storage/ledger.py:157, storage/operations.py:107, storage/alpha.py:750), so a version bump on a shared kind is a breaking change across the persistence boundaries of a live daemon.
   - It produces no card and no edge.
   - Prometheus funnel counters would duplicate the journal for a single operator.

Overall, measurement does not create edge. The evidence already says there is none: a holdout ρ of −0.012 for `setup_quality` and −0.110R versus SPY (docs/setup-outcomes-2026-09-23.md "Selection"; docs/bracket-counterfactual-2026-10-18.md "Descriptives"). That keeps this below a P0 alpha or supply fix. The denominator journal is still cheap, it is a prerequisite for judging those fixes, and the enforce flips make it timely. Hence P1, horizon days, but only for the narrow scope.

**Risks**

- **Scope creep into a journal-wide refactor.** It would touch durable event contracts that readers validate strictly: `schema_version` is checked in ledger.py:157, operations.py:107 and alpha.py:750, and maintenance compaction filters on `schema_version == 1` (maintenance.py:66). A careless bump could stall ledger or incident projection, or compaction, on the production daemon.
- **Goodhart and over-reaction.** The scorecard would show beta-adjusted R, Brier score and implementation shortfall on about 40 cards and 3 fills. That invites reactive tuning on noise, which is exactly what the predeclared-study discipline forbids. Each weekly number needs its n and interval, and must stay descriptive, never a gate.
- **Write volume and idempotency.** Empty and swing scans add one event per scan. These events need a stable slot key, so that restarts (61 daemon starts in 33 days) don't create duplicate or misleading denominators. Swing and start-up scans also need to be kept apart from scheduled slots.
- **Payload size.** Rejection payloads should hold counts per rule plus symbols. Full evaluation dicts would bloat the retained journal, and financial or work events are never compacted.
- **More Telegram traffic.** A weekly digest adds to an already noisy channel unless it replaces existing notices.

**Prerequisites**

- **Slot identity.** Define a canonical scheduled-slot key (New York date plus scheduled time) and record the trigger, so that missed, late and swing or start-up scans are distinguishable. The 9-28 slot was lost to host sleep (docs/production.md "Late and missed slots").
- **Rule identifiers.** Agree that the per-rule refusal counts use the existing RiskRule ids from `agentic_trader/risk`. Pre-risk exclusions (coverage, dynamic-universe unavailability) need their own reason codes rather than free text.
- **Readers stay untouched.** Additive payload fields need no bump; if a new kind is added, it starts at version 1.
- **Order of work.** Ideally this lands before or together with the live-map supply fixes (swing-scan budget, correlation dedup), so their effect is measured against a clean baseline week.
- **Testing.** Disposable PostgreSQL integration coverage for the new event, as required for persistence changes.

**Better alternative**

Do the effort-S version and skip the effort-M version.

1. Add one durable event for every non-dry scan, scheduled or not. Either always write `scan_candidates_ranked`, or add a single `scan_completed` kind beside it. It should carry:
   - slot key, trigger and timing;
   - raw setup count;
   - collect-phase refusal counts and symbols per RiskRule;
   - LLM, budget and policy outcomes.

   Validate it with one pydantic model that carries its own internal `payload_version`. Leave every other event kind and the row's `schema_version` alone.
2. Build the end-of-session digest from these events instead of in-memory state, so restarts no longer truncate it.
3. Add a few fields to the existing `cards outcomes` summary:
   - `slots_journaled / slots_scheduled`;
   - cards per slot by source;
   - refusal share by rule;
   - tap rate and median time to tap;
   - notices per card;
   - a binned calibration table of the printed target rate against labelled outcomes, with n and intervals.
4. Send one weekly message that renders that summary.

Drop the global discriminated union, the per-kind version bumps, the Prometheus funnel counters and the implementation-shortfall metric until there are dozens of fills.

---

<a id="c36"></a>

## C36 — Live indicators and the book-vol covariance use raw prices across splits; under enforce, one split in a held name can block every card

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | data | defect | medium | S | partially_confirmed |

_lenses: data, portfolio_

### Claim (as consolidated)

fetch_bars defaults to adjustment='raw' (providers.py:186). TrendPullback's close > EMA50 > EMA200 test and its 4h ATR both read raw frames.

Book sizing accepts only raw frames (covariance.py:20-26; book_sizing.py:206-232):
- A 2:1 split inside 120 sessions enters as a −50% return, and Ledoit-Wolf spreads that move across the matrix.
- If pre-card book volatility then exceeds the budget, book_vol returns a factor of 0 for every non-hedging card (book_vol.py:91-95).

The corporate_action rule only covers −5 to +7 days around an action, and its capture excludes dynamic, PEAD and operator names.

Incidence:
- 135 splits across 964 cohort-v3 names over 2016–26, about 1.4% of names per year. On 2026-10-15, 7 names had a split within the trailing year.
- Market-wide, there were 6 forward and 11 reverse splits in 10 days, concentrated in movers.

A 1:10 reverse split lifts a $2 stock past the $10 dynamic floor and makes close > EMA200 true for up to 200 sessions.

### Evidence

- agentic_trader/data/providers.py:186
- agentic_trader/research/setups/covariance.py:20-26; agentic_trader/execution/book_sizing.py:206-232
- agentic_trader/risk/book_vol.py:91-95
- docs/corporate-actions.md:39-41, 'Entry rule', 'Known limits'
- Measured: ~/agentic-trader-research/corporate-actions-cohort-v3-20261015/actions.json (split counts)

### Recommendation

Use split-adjusted data where prices feed indicators or risk:
- Fetch adjustment='split' for indicator, ATR and covariance frames.
- Keep raw prices only for price floors and order prices, as cohort.py already does.
- Accept 'split' explicitly in RAW_ADJUSTMENTS, while still rejecting yfinance auto-adjust.

Widen corporate-action coverage:
- Capture structural corporate actions market-wide (about 40 per 10 days), so that dynamic, PEAD and operator names are covered.
- Journal a per-scan count of names with a structural action inside any live lookback.

### North-star impact

Removes a rare but silent failure that can stop the card stream under book_sizing enforce, and removes false 'bullish trend' cards on reverse-split movers.

### References

- Alpaca Stock Bars API, adjustment=split — Split-adjusted frames are a request parameter (https://docs.alpaca.markets/reference/stockbars)

### Fact check — partially_confirmed

**Corrected claim**

The core finding is correct. Live scans read raw Alpaca bars: providers.py:186, plus market_data.py:209-219 (daily over 1y; 1h over 60d, resampled to 4h). The TrendPullback daily trend test (strategies.py:96-97) and the 4h ATR (strategies.py:131) are computed on these unadjusted frames.

Book sizing accepts only raw or untagged frames (covariance.py:20-26; book_sizing.py:206-232). It has no outlier handling, so a split enters the Ledoit-Wolf covariance as a one-day return of -50% (2:1) or +900% (1:10 reverse).

Under enforce (config.yaml:466), an over-budget book returns factor 0 (book_vol.py:91-95). scale_sizing then returns None and the card is BOOK_SIZING_BLOCKED (book_sizing.py:362-368; copilot.py:1421-1427).

Corrections to the details:

(1) **Magnitude.** A 2:1 forward split alone does not plausibly push the book over budget at the shipped settings: $7,500 per-trade cap, 0.8%/day of $100k. Ledoit-Wolf shrinkage jumps to about 0.8-1.0 and flattens correlations. In simulation the book vol stays at $300-510 against an $800 budget. The block-every-card failure needs a reverse split: at 1:10 the book vol reaches about $3.9k-5.7k, over budget every time.

(2) **Scope.** The split need not be in a held name. prepare() builds the covariance over the book plus every equity native candidate in the scan (copilot.py:1209-1223; book_sizing.py:203). Ledoit-Wolf shrinks toward trace/p × I. So a reverse split anywhere in one candidate's 120-session window raises every book name's variance and blocks every card in that scan whenever the book is non-empty. That includes an uncovered dynamic movers name. With an empty book, such a candidate is only scaled down, not blocked.

(3) **Incidence.** Cohort v3 has 1,000 symbols; 964 is the number with any action in the acquisition. 135 splits (91 forward, 44 reverse) over 2016 to Oct 2026 is about 1.25-1.4% per name-year, and about 0.9%/yr over 2021-2025. 7 names had a split in the trailing year to 2026-10-15, and 3 of them inside about 120 sessions (TRI, MLI, NFE).

(4) **Movers.** "Concentrated in movers" is not supported. The cited doc gives only market-wide counts.

(5) **"Silent."** The failure is not silent in the code sense. docs/card-evidence.md 'Limitations' documents both that raw bars distort variance and that an over-budget book blocks every non-hedging card. docs/corporate-actions.md 'Known limits' records 'live bars stay raw' as an accepted limit, and blocks are journaled with a vol reason. What is missing is any link from a block to the split that caused it.

(6) **EMA200 horizon.** The EMA200 effect has no 200-session cutoff. It decays with a half-life of about 69 sessions and is bounded by the 1y daily fetch window of about 250 sessions.

(7) **$10 floor.** The $10 floor passing a reverse-split name reflects the true current price, not an adjustment artifact.

**Checked**

- agentic_trader/data/providers.py:178-194 (AlpacaDataProvider.fetch_bars, adjustment='raw' default at :186); providers.py:488-507 (CompositeMarketDataProvider.fetch_bars passes no adjustment, so it uses raw); providers.py:436 (the yfinance fallback is tagged yfinance_auto_adjust)
- config/config.yaml:433-435 (alpaca is the primary provider, yfinance the fallback)
- agentic_trader/data/market_data.py:197-238 (live fetch_data: daily period=1y raw, 1h period=60d raw, 4h resampled from raw 1h); market_data.py:140-147 (EMA_50/EMA_200/ATR_14 on df.copy(), attrs preserved)
- agentic_trader/agent/copilot.py:2228-2252 (_fetch_universe calls data_fetcher.fetch_data for every scan)
- agentic_trader/screeners/strategies.py:84-99 (close > EMA50 > EMA200 on the daily frame), :131 (4h ATR_14), config/config.yaml:303-307 (50/200)
- agentic_trader/research/setups/covariance.py:20-26 (RAW_ADJUSTMENTS={None,'raw'}), :28-41 (only non-positive or non-finite closes are dropped, no outlier clipping), :80-89 (sklearn LedoitWolf)
- agentic_trader/execution/book_sizing.py:203-232 (book ∪ candidates, raw-only frames, raw refetch), :252-281, :283-374 (decide; factor<1 under enforce goes to scale_sizing, None means BLOCKED)
- agentic_trader/agent/position_sizing.py:280-320 (factor 0 floors every tier below min_units, so it returns None)
- agentic_trader/risk/book_vol.py:88-95 (factor 0 when a > budget^2 and the full card does not fix it)
- agentic_trader/agent/copilot.py:1209-1223 (the covariance covers the book plus every equity native candidate in ranked[:native_count]), :1412-1427 (BOOK_SIZING_BLOCKED outcome is journaled with its reason)
- config/config.yaml:465-470 (book_sizing enforce, 0.8%/day, 120 sessions), :342-343 ($7,500 per-trade notional cap), :259-266 (dynamic min_price 10)
- agentic_trader/cli/commands/service.py:382 (corporate-action symbols = universe | held | extra_symbols, no dynamic names)
- agentic_trader/config.py:312-314 (pre_days 7, post_days 5)
- docs/corporate-actions.md:39-41 (6 forward and 11 reverse splits on 2026-10-01..10, market-wide), 'Entry rule', 'Known limits' (live bars stay raw; dynamic/PEAD/operator names uncovered)
- docs/card-evidence.md:219-228 and 'Limitations' :262-268 (raw bars distort variance; an over-budget book blocks every non-hedging card)
- ~/agentic-trader-research/corporate-actions-cohort-v3-20261015/manifest.json + actions.json: 91 forward + 44 reverse = 135 splits; 1,000 cohort symbols, 964 with any action; 112 names with any split; 7 names with a split in the trailing year to 2026-10-15 (UL, TPL, SF, AGL, TRI, MLI, NFE), 3 within about 120 sessions
- Simulation (system python3, sklearn LedoitWolf, 120 sessions, 2% vol, rho 0.35, $7,500 per name, $800 budget, 2-6 book names, 1-3 candidates): with no split, book vol $241-588; a -50% (2:1) return in a book or candidate name gives LW shrinkage 0.8-1.0 and book vol $302-511, never over budget; a +900% (1:10 reverse) return in a book OR a candidate name gives book vol $3.9k-5.7k, over budget in 100% of trials
- https://docs.alpaca.markets/reference/stockbars (adjustment = raw|split|dividend|spin-off|all, default raw)

**Bad references**

- docs/corporate-actions.md:39-41 supports the 6 forward / 11 reverse market-wide counts but not the claim that splits are 'concentrated in movers'
- '135 splits across 964 cohort-v3 names': the cohort-v3 acquisition (manifest.json) covers 1,000 symbols; 964 is the count of symbols with any corporate action

**Notes**

Overall: the defect is real and acknowledged in the docs, but it is understated in one respect and overstated in another.

Understated: a reverse split in a candidate alone (for example a dynamic movers name, which the corporate_action capture never covers) is enough. Ledoit-Wolf shrinkage goes to about 1 under a single huge outlier, and its target is the mean variance times the identity. So every book name inherits the inflated variance and every card in that scan is blocked.

Overstated: the headline 2:1 forward-split example does not by itself breach the 0.8%/day budget at the $7,500 per-trade cap. Reverse splits are the trigger.

The recommendation is sound:
- Fetch 'split'-adjusted frames for indicators, ATR and covariance; Alpaca supports adjustment=split, and cohort.py already uses raw for floors and adjusted for ATR.
- A cheap independent guard is also worth adding: a covariance-input sanity check that drops or winsorizes single-day returns beyond, say, ±40%, or excludes names with a structural action in the lookback. That would also protect against unadjusted symbol changes and spin-offs.

The finding should drop the 'silent' wording, since the docs list both halves of the mechanism as limitations.

No repo files were modified. The simulation ran from the scratchpad with system python3 -I.

### Value review — P1, now (days)

**Reasoning**

The defect is real, and the code confirms it is worse than the finding says. It is also cheap to fix, and its failure lands directly on the north star: book_sizing went to enforce on 2026-10-20 (config/config.yaml:465-466).

What the code does now:
- The live scan fetches daily and 1h bars through the composite provider with no adjustment argument (agentic_trader/data/market_data.py:209,214). That provider's fetch_bars has no adjustment parameter at all (agentic_trader/data/providers.py:488-505), so Alpaca answers raw (providers.py:186). The yfinance fallback answers auto-adjusted (providers.py:436). Indicator frames therefore change basis depending on which provider answered.
- Book sizing accepts only raw frames (agentic_trader/research/setups/covariance.py:20-26) and fetches missing names raw (agentic_trader/execution/book_sizing.py:222).
- A forward split is the worse case for us, because TrendPullback picks recent winners, and recent winners are the names that split (NVDA, AVGO, CMG). A 10:1 split enters the 120-session lookback as a −90% day. That one day adds about 0.0068 to daily variance (about 8% daily volatility). On a $10k position that alone exceeds the 0.8% daily-vol budget on a roughly $100k book. Once the book is over budget, book_vol_factor returns 0 for every non-hedging card (agentic_trader/risk/book_vol.py:91-95), and the stream stays dead while the name is held.
- The same split makes close > EMA50 > EMA200 false for up to about 200 sessions. That silently drops exactly the momentum names the strategy wants. Reverse splits do the opposite and create false trend cards.

A parity gap the finding missed: the setups replay that produces honest base rates, the bracket counterfactual and the recurrence test all use adjustment 'all' (agentic_trader/research/setups/study.py:76, baserates.py:58, bracket_counterfactual.py:95). Card evidence is therefore measured on a different price basis than the one live cards fire on.

docs/corporate-actions.md 'Known limits' already lists 'live bars stay raw' as a pending, measured change, so this is acknowledged debt.

Why P1 and not P0: incidence is low (about 1.4% of names per year, higher conditional on momentum), and the fix does not create new alpha. It is still the cheapest way to remove a single-event outage of the card stream under a gate that is now enforced.

The second half of the recommendation is lower value and should be split out:
- Market-wide structural capture and the per-scan lookback-straddle count: once frames are adjusted, the lookback risk is gone. Blackout coverage for dynamic, PEAD and operator names is a separate P2/P3 question about the −5/+7 day window.

**Risks**

- **Changes which cards fire.** Switching the indicator basis changes TrendPullback eligibility and ATR stops. card_policy and card_stats evidence collected on raw-fed cards is then not strictly comparable. Note the switch date in provenance so before and after cohorts can be told apart.
- **Mixed bases.** Alpaca recomputes adjusted history at query time. Any cached or partially fetched frames, including 1h bars resampled to 4h, must not mix bases inside one scan. The adjustment must also reach the composite provider and its fallback chain, or frames will still change basis silently.
- **Wrong paths changed.** Crypto and futures must stay raw; providers.py:193 raises otherwise. Order prices, price floors and dollar-volume floors must keep raw or current-basis values. The adjusted latest bar equals the raw one, but asserting this in tests is prudent.
- **Strict pins elsewhere.** Several research and observer paths hard-pin raw (agentic_trader/data/sessions.py:87,142; agentic_trader/market/bars.py:290; agentic_trader/cli/commands/alpha.py:189). The change must be scoped to the scan and book-sizing paths, not to a global default.
- **'split' versus 'all'.** 'split' leaves a raw-versus-research gap on dividends. 'all' matches the research replay and the yfinance fallback, but shifts historical levels by cumulative dividends. Pick one and record it.

**Prerequisites**

- Add an `adjustment` keyword to CompositeProvider.fetch_bars (providers.py:488) and pass it to every provider, so that yfinance is either used only when it matches the requested basis or tagged honestly.
- Decide the live basis. Recommendation: 'all', to match the setups replay (study.py:76, baserates.py:58).
- Widen RAW_ADJUSTMENTS (rename it, since it will no longer be raw-only) to accept that basis while still rejecting yfinance auto-adjust frames for covariance, unless the bases are declared equal.
- Add a regression test with a synthetic 10:1 split in a held name: today it gives factor 0; after the fix it gives a sane factor. Add a TrendPullback test showing eligibility survives a split.
- Run a one-off read-only check of current held and universe names against the existing corporate-actions capture, to see whether any split already sits inside a live lookback.

**Better alternative**

A narrower, higher-leverage first step:
1. Thread `adjustment` through CompositeProvider.
2. Fetch the scan's daily and 1h frames, and book sizing's missing frames, with the same adjustment the research setups replay uses ('all'). Accept that basis in covariance.

This is about three small edits plus two regression tests. It removes the outage that blocks the whole card stream, the TrendPullback false negatives and positives, and the research/live basis mismatch in one PR. Ship it as its own small PR, or bundle it with the next workstream PR as the memory policy prefers.

If an even smaller stopgap is needed for book sizing alone: in covariance.daily_returns, drop or rescale returns on ex-dates that the existing corporate-actions capture shows as splits (price_factor). This is strictly worse than fetching adjusted bars.

Defer market-wide structural capture and the per-scan straddle counter. They only matter for the blackout window, not for lookback contamination, once frames are adjusted.

---

<a id="c37"></a>

## C37 — One 20-session long bracket labels every hypothesis, and 'the bracket is closed' was generalised from a test of native exits

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | next (weeks) | research_methodology | defect | medium | M | partially_confirmed |

_lenses: quant_

### Claim (as consolidated)

Every pooled protocol labels the same trade: a 10:35 entry, a 2×ATR14 stop, a 3R target and 20 sessions (cube.py:284-295, with identical bracket blocks across the protocols). Events are encoded as daily top-decile baskets measured against all eligible names.

In-house evidence shows the horizons do not match:
- PEAD earns +0.252R at 60 sessions against +0.149R at 20.
- Insider net_buying cites a six-month effect.
- reversal_5, the short-ratio fields and the first-hour fields describe 1–5-session effects.
- The bracket hits its target only about 8.6% of the time.

The cohort-v3 doc named the bracket as the next thing to test. The counterfactual that followed varied only exit arms on native setups, with the same entry and stop. 'Bracket closed' was then carried back to the pooled lane, whose horizon, stop width and event encoding were never tested.

### Evidence

- agentic_trader/research/pooled/cube.py:57-71, 284-295
- config/research/pooled/{cohort3-daily-v1,short-v1,events-v1,intraday-v1,insider-v1}.json 'bracket' (identical)
- agentic_trader/research/pooled/formula.py:195-215
- docs/apriori-pead-2026-09-26.md 'Diagnostics' (60-session +0.252R)
- docs/alpha-pooled-cohort3-2026-10-16.md 'Reading'; docs/bracket-counterfactual-2026-10-18.md 'Protocol and run'
- docs/alpha-pooled-literature-2026-09-30.md 'Diagnostics' (target 8.6%)

### Recommendation

1. Build cube v2 from cached bars, storing forward returns at 1, 5, 20 and 60 sessions (cost in bps, β-hedged) plus the risk arrays (C05).

2. Have each protocol predeclare its horizon from its source paper:
   - reversal and flow: 1–5 sessions;
   - PEAD: 60 sessions;
   - insider: 60–120 sessions.

3. Add an event-entry selection rule to CampaignProtocol.select:
   - enter on the first session the condition becomes true;
   - skip names already held;
   - use a control of the same event type.

4. Use bracket labels only in the implementation stage, with the live card policy. session_count_v1 already exists live.

### North-star impact

PEAD-type and insider-type hypotheses get a fair test at the horizon where their information lies, and would arrive as cards with a matching time exit.

### References

- Bernard & Thomas (1989), J. Accounting Research 27 — PEAD accrues over about 60 trading days
- Lakonishok & Lee (2001), RFS 14(1) — Insider signals predict over 6–12 months
- Cohen, Malloy & Pomorski (2012), JF 67(3) — Opportunistic-trade signal at monthly horizons

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding holds.
- Every pooled protocol labels one identical long bracket: a 10:35 decision, a 2xATR14 stop, a 3R target and a 20-session time exit (cube.py:57-71, 284-295; all protocol JSONs). The cube stores no forward returns.
- Event, insider, short-volume, intraday and daily fixed sets are all daily top-decile baskets (select_top_fraction, formula.py:195-215). They are scored against the mean of all labelled eligible names (stats.py:54-64). CampaignProtocol.select has no first-event-entry rule.
- The cohort-v3 doc named the bracket as the constant, and the follow-up counterfactual varied only exit arms on native setups (same entry, planned stop, 20-session timeout).
- The data-augmentation doc (lines 71-73) and roadmap line 625 then treated it as having 'settled the exit side'. Short-v1 and insider-v1 kept the identical bracket. Note that the counterfactual doc itself scopes its verdict to the native funnel, and the cohort-v3 doc already declared the test would run on native setups.

Corrections:
1. 'Horizon and stop width were never tested' is overstated. The 2026-10-07 v4 label diagnostic (docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic') re-scored the daily fixed set on cohort v2, reversal_5 included, using plain close-to-close returns at 5 and 20 sessions, per-session rank IC, a decile spread, and per-ATR edges with no stop or target. It concluded that 'the bracket label is not what removes an edge.' What remains untested in the pooled lane:
   - horizons of 60 sessions or more;
   - return-space or horizon checks for the events, short-volume, insider and intraday sets, and for cohort v3;
   - event-time (first-trigger) entry with a same-event control.
2. Event-time entry with a same-event-type control already exists, in the a priori lane rather than the pooled lane. The PEAD study enters on D+2 against a control of every liquid reporter, passed, and runs as a live catalog probe (earnings_drift.py, 20-session session_count_v1). The PEAD +0.252R at 60 sessions is from that a priori study, as a descriptive diagnostic. It is not a pooled-lane result.
3. The insider net_buying rationale's 'six months' is the net-purchase-ratio lookback window, not a forecast horizon. Lakonishok & Lee find predictability over roughly 12 months.
4. The 8.6% target rate is one literature leg (52-week high). PEAD long hit target 5% of the time.
5. Not all the cited fields are 1–5-session effects:
   - the first-hour fields are same-session (intraday) effects;
   - Boehmer, Jones & Zhang's short-ratio effect runs about 20 days.
6. On the recommendation: session_count_v1 exists live, but MAX_HOLDING_SESSIONS = 60 (lifetime_policy.py:75). A 60–120-session insider horizon therefore exceeds the current live time-exit cap.

**Checked**

- agentic_trader/research/pooled/cube.py:51-71 (BracketSpec: decision_time_et, stop_atr_multiple, atr_window, target_r, max_hold_sessions) and :264-295 (one LONG bracket per eligible cell, labelled by label_bracket)
- cube.py:54 _ARRAYS: the cube stores only the bracket outcome (r_gross, r_cost, holding, hit). It holds no forward-return arrays.
- config/research/pooled/*.json: the bracket block is identical in every protocol (10:35, 2.0 x ATR14, 3R, 20 sessions): campaign-v1..v4, high52, reversal-lowmax, events-v1, intraday-v1, short-v1, insider-v1 and all cohort3-* files
- events-v1, insider-v1, short-v1, intraday-v1 and cohort3-daily-v1 all use selection {rule: top_fraction, fraction: 0.1}
- agentic_trader/research/pooled/formula.py:195-215 select_top_fraction (daily basket, no hold-skipping, a name can be re-picked on consecutive sessions)
- agentic_trader/research/pooled/campaign.py:350-362 select dispatches to the two rules only; it has no event-entry rule
- agentic_trader/research/pooled/stats.py:1-10, 54-64: control = mean R of all labelled eligible cells in the session, not restricted to the formula's finite-score or event names
- events-v1 family rationales: pead_reaction_window and pead_agreement say 'the decile is taken among in-window names only'
- docs/apriori-pead-2026-09-26.md 'Diagnostics': 60-session hold +0.252 (n 4,679) vs P1 +0.149. Exits for the LONG leg: 40% stop / 5% target / 55% time
- docs/alpha-pooled-literature-2026-09-30.md:108 'Diagnostics': target 8.6% (one leg; the other leg 8.4%)
- docs/alpha-pooled-cohort3-2026-10-16.md 'Reading' (lines 133-141): the bracket is named as the constant and the counterfactual is explicitly scoped 'on the replayed native setups'
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run': all arms share the entry, the planned stop, R and a 20-session timeout. The verdict is scoped to 'the native funnel's base rate'
- docs/alpha-data-augmentation-2026-10-18.md:71-73: the counterfactual 'settled the exit side; new fields should be tested as fixed sets ... as before' (this is the carry-over to the pooled lane)
- docs/alpha-roadmap.md:621, 623, 625 ('the bracket is closed, so the next alpha work is on the entry side'); short-v1 and insider-v1 then kept the same bracket
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic' (lines 83-118): counter-evidence
- agentic_trader/research/pooled/insider_fields.py:248-252 (net_window is a lookback)
- agentic_trader/execution/lifetime_policy.py:74-75 (SESSION_LIFETIME_VERSION = session_count_v1, MAX_HOLDING_SESSIONS = 60)
- agentic_trader/screeners/earnings_drift.py:44-55 (live PEAD catalog probe, 20-session session_count_v1 hold)
- Web: Bernard & Thomas 1989 JAR 27 (about 60-trading-day SUE drift); Lakonishok & Lee 2001 RFS 14(1):79-111; Cohen, Malloy & Pomorski 2012 JF pp. 1009-1043 (opportunistic trades, about 82 bp per month VW)

**Bad references**

_none_

**Notes**

Every cited file:line location exists and supports its use. The bracket blocks are identical across all 15 protocol files that carry one, including the older v1-v4, high52 and reversal-lowmax protocols, not only the five named. The three literature references are real and correctly characterised: Bernard & Thomas report about 60-day SUE drift, Lakonishok & Lee is RFS 14(1):79-111, and Cohen, Malloy & Pomorski is JF 2012 pp. 1009-1043, with monthly-horizon opportunistic-trade returns.

The main thing the finding misses is the v4 label diagnostic. It already tested return space at 5 and 20 sessions, and no-stop per-ATR edges, for the daily formula family, and found no signal. That weakens the 'never tested' wording for daily and reversal formulas. It does not affect the event, insider, short or intraday sets, 60-session-plus horizons, or event-time encoding, none of which the pooled lane has tested.

The 'carried back' framing is supported by docs/alpha-data-augmentation-2026-10-18.md:71-73 and docs/alpha-roadmap.md:625, not by the counterfactual doc itself, which limits its claim to the native funnel.

### Value review — P1, next (weeks)

**Reasoning**

The defect is real and I checked it. The way it was generalised matters more than the recommendation's scope.

What the code does now:
- Every pooled protocol carries the identical bracket: 10:35, 2×ATR14 stop, 3R target, 20 sessions. That covers campaign-v1..v4, high52, reversal-lowmax, events, short, intraday and insider, plus the cohort3 variants (config/research/pooled/*.json 'bracket').
- Every cube label is `label_bracket` on that spec (agentic_trader/research/pooled/cube.py:284-295).
- The only selection rules are `top_k` and `top_fraction` (agentic_trader/research/pooled/campaign.py:222, 358-362). Event formulas are therefore daily decile baskets with no hold-skipping, measured against all eligible names (agentic_trader/research/pooled/formula.py:195-215).

How 'bracket closed' was generalised:
- docs/alpha-pooled-cohort3-2026-10-16.md 'Reading' names the bracket as "the constant across every negative".
- The counterfactual that followed varied only the target and the ratchet. Every arm shared the native entry, the planned stop and a 20-session timeout (docs/bracket-counterfactual-2026-10-18.md 'Protocol and run').
- It closed the bracket only "as an explanation for the native funnel's base rate". It then sent the entry-side work back to the pooled lane on the same frozen bracket ('Consequence for the desk').
- So horizon and stop width were never tested for the pooled lane. The 64 nulls cannot tell "no information" apart from "information at a different horizon".

Why P1 and not P0:
- Expected yield is uneven across the recommendation.
- Short-horizon part (reversal_5, first-hour, short flow at 1–5 sessions): I expect little. These effects are small and largely liquidity provision in liquid names. A 10:35 entry, a 5–15 bp per-side decisive cost and the post-publication decay (McLean-Pontiff 2016) would likely leave nothing card-worthy. A 1–5-session card also needs a fast feedback loop and creates turnover the single operator must act on.
- Long-horizon event part (insider opportunistic buys at 60–126 sessions per Cohen-Malloy-Pomorski 2012; PEAD at 60): this is the part with a real prior.
- PEAD is also the only in-house hypothesis that has passed (docs/apriori-pead-2026-09-26.md, LONG eligible, live as the `pead_long` probe).
- Caution on the PEAD number: the +0.252R at 60 sessions is unhedged R with no 60-session control in the 'Diagnostics' table. Part of it is surely long drift, the same beta that makes every native arm trail SPY.

Why it is time-sensitive:
- The insider-v1 and cohort3-insider-v1 campaigns are frozen on the 20-session bracket and waiting for operator approval.
- Running them now spends a look at insider formulas on a horizon the source papers say is wrong (Lakonishok-Lee 2001: 6–12 months). A later horizon-matched version would then be a second look on a window that has already been read.
- The cheapest high-value action is a decision rather than code: hold those approvals until a horizon-matched, event-entry insider protocol is frozen.

Corrections to the recommendation:
- A 60-session cube is not a config-only change. `_LABEL_SPAN = timedelta(days=45)` (cube.py:52, used at 231-234) trims the bar slice handed to the labeller to about 30 sessions. 60-session labels would come back IMMATURE (agentic_trader/research/setups/labels.py:177-182) and fail coverage. The fix is small, but the span must scale with `max_hold_sessions`.
- Event-entry selection plus a same-event control is already the a priori lane's design: D+2 entry, every-reporter control, P1–P4 bootstrap, `session_count_v1` live exit (agentic_trader/execution/lifetime_policy.py:74), and probe enrolment. Building the same thing a second time inside `CampaignProtocol.select` duplicates a working path.

What to do: fund this narrowed to the long-horizon event leg. Fold the multi-horizon forward-return arrays into the same cube-v2 rebuild as the risk arrays (C05), so there is one cache-only build. Do not fund a 1/5/20/60 sweep over all 64 formulas.

**Risks**

1. **Forking paths (largest risk).** Relabelling the same formulas on the same 2016-08→2021-12 discovery window at several horizons multiplies trials. A horizon chosen after seeing results, even implicitly, makes any finalist uninterpretable. Each (formula, horizon) pair must be charged to the pooled ledger. The horizon must be fixed per formula from its paper, by hash, before any label is computed. Never pick the best horizon per formula.

2. **Window overlap goes unrecorded.** PEAD-60 or exdiv variants are second looks at a window the a priori studies already read (2016-08→2026-07). The pooled overlap rule does not register those reads: `LITERATURE_ENTRIES` lists only pooled entries (agentic_trader/research/pooled/campaign_run.py:107), and the ledger checks only `pooled/confirmation`. A "confirmed" pooled PEAD-60 would therefore not be independent evidence.

3. **Longer horizons inflate long-drift beta.** In unhedged R units a long hold rewards beta. Without the β-hedged label and the risk-matched control (C05), a 60–126-session "edge" will mostly be market exposure, and that is already why cards trail SPY.

4. **Live practicality.**
   - Holds of 60–126 sessions tie up concurrent-position and aggregate-stop-risk capacity for months.
   - Each forward outcome takes 3–6 months, so the operator's evidence accrues slowly.
   - Probes would need a renewal cadence that matches the hold.

5. **Thin events at the tail.** Insider "fresh opportunistic purchase" events in a liquid cohort are sparse. P4-style count floors (at least 300 events, at least 100 recent) may fail at 126 sessions near the window end, where labels become immature.

6. **The selection window is nearly unread.** Burning it on a broad multi-horizon sweep would leave nothing clean for the combination stage.

**Prerequisites**

1. **Operator decision now:** hold approval of the frozen insider-v1 and cohort3-insider-v1 campaigns (20-session bracket) until a horizon-matched insider version is frozen, or record explicitly that the 20-session run is a separate, charged trial.

2. **A horizon table frozen by hash in each new protocol,** taken from the source papers. Every (formula, horizon) pair is charged before any read. Keep the confirmation window one-use, lane-wide.

3. **Cube changes:**
   - Make `_LABEL_SPAN` (cube.py:52) derive from `max_hold_sessions`, or store per-horizon forward-return arrays.
   - Add a β-hedged, cost-in-bps label.
   - Build this in the same cache-only rebuild as the C05 risk arrays (atr_pct, 60-session beta, size, sector), so the long-horizon test has a risk-matched control.

4. **Fix the overlap rule (campaign_run.py:107)** so the a priori PEAD and exdiv inspected intervals count as already-read data for any pooled relative.

5. **Live path:** confirm that `session_count_v1` and the probe's expiry/renewal and open-position cap support 60–126-session holds before anything is enrolled.

**Better alternative**

A narrower, cheaper path that reuses machinery already built:

1. **Insider on the a priori lane, not inside `CampaignProtocol.select`.** Write one predeclared a priori catalog entry for insider opportunistic open-market officer/director purchases, following Cohen-Malloy-Pomorski (2012).
   - Use the existing `insider_fields_v1` acquisition.
   - Enter on the first session after the EDGAR filing date.
   - Use a 60-session (or 126-session) `session_count_v1` time exit with a wide stop.
   - Control: all insider-purchase events, plus a β-hedged comparison.
   - Apply the same P1–P4 stationary-bootstrap rules as PEAD.
   This reuses the PEAD study harness, statistics and probe path. A pass becomes a capped `probe`, not a new campaign lane.

2. **PEAD-60 without a historical re-test.** The historical window is already read. Record a descriptive shadow 60-session, SPY-hedged outcome for each live `pead_long` probe decision alongside its 20-session one. A horizon change for the probe can then be judged on fresh forward evidence, with no second look at the historical window.

3. **Short horizons (1–5 sessions):** add the forward-return arrays to the C05 cube rebuild at near-zero marginal cost and report them descriptively. Fund a frozen short-horizon fixed set only if the cost-adjusted descriptive edge is plausibly above round-trip cost.

---

<a id="c41"></a>

## C41 — Stop investing research in native technical setups beyond honesty and parity fixes

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | signals | stop_doing | medium | S | partially_confirmed |

_lenses: product_

### Claim (as consolidated)

Five predeclared studies have closed the native source:
- setup_quality is uninformative, and the frozen ranker did worse in the holdout.
- Native shorts lose −0.33R per setup.
- The recurrence study returned no_change.
- No exit arm rescues the entries.
- Excess over SPY is about −0.10R in both 2017–21 and 2021–26.

The live signal also has unfixed semantic defects:
- The pullback trigger fires on a falling RSI. The rule is 'min(prev two) < 45 and current ≥ 40', with the dip threshold (45) above the recovery threshold (40) (strategies.py:146-149; config.yaml:311-312).
- The quality heuristic scores a falling RSI as recovery because it uses abs() (strategies.py:42).
- 4h bars are UTC resamples that shift when DST ends (C10).

### Evidence

- docs/setup-outcomes-2026-09-23.md; docs/setup-baserates-short-2026-09-23.md; docs/setup-recurrence-2026-10-14.md; docs/bracket-counterfactual-2026-10-18.md
- agentic_trader/screeners/strategies.py:42, 146-149; config/config.yaml:311-312
- agentic_trader/data/market_data.py:119-138

### Recommendation

Freeze native strategy research: no new construction, ranking or recurrence studies. The only exceptions are the decomposition test (C40) and the stop-width test (C11).

While natives remain a card source, keep their printed base rate honest by using the same feed and bars as the prior.

If the operator keeps natives after card_policy preview, ship one native-v2 before relying on data after 11-01:
- session-anchored RTH bars on delayed SIP;
- an RSI rule that requires recovery;
- a fresh frozen replay base rate.

Otherwise, stop sending TREND_PULLBACK cards and keep it as a journal-only control population.

### North-star impact

Redirects research from a source with a robust negative excess to event sources, and strips known artefacts from any native card still shown.

### References

- Barber & Odean (2008), All That Glitters, RFS 21(2) — Attention-driven buying of movers predicts poor returns

### Fact check — partially_confirmed

**Corrected claim**

The core holds, with these corrections. Four predeclared studies close the native source, not five; the SPY-excess bullet is a result inside the bracket-counterfactual study, not a separate study. The four studies are setup-outcomes, short base rates, recurrence and the bracket counterfactual. Their results:
- setup_quality carries no information (dev ρ +0.016, holdout −0.012), and the frozen ridge ranker was significantly worse than setup_quality in the holdout (−0.293 vs −0.126 top-2, paired CI [−0.325, −0.016]).
- Native shorts lost −0.325R per setup on 2017–21.
- Recurrence returned no_change.
- No exit arm beats the live bracket. The live A0 arm's excess over SPY is −0.110R on 2021–26 and −0.103R on 2017–21; across all arms it ranges from −0.10 to −0.15R.

The semantic defects are real but should be worded precisely:
- The long trigger (strategies.py:147-149; config.yaml:311-312) CAN fire on a falling or flat RSI, not always. Because dip=45 is above recovery=40, any current RSI in [40, 45) that sits below the previous two-bar minimum qualifies (e.g. 50→44→42). The card's trigger_detail then prints 'dipped to 44.0 and recovered to 42.0' (strategies.py:160-163).
- setup_quality's abs() (strategies.py:42) scores that decline as positive 'recovery'.
- The 4h frame is a UTC-midnight-anchored resample of Alpaca 1h bars (market_data.py:119-138, 216-219), so its bucket boundaries move one New York hour when DST ends. Replay inherits the same definition (replay.py:237).

card_policy is currently 'off' (config.yaml:454), not in preview. The live data feed defaults to IEX (config.py:925, 1043), while every replay prior is SIP all-adjusted.

**Checked**

- agentic_trader/screeners/strategies.py:29-43 (trend_pullback_setup_quality; line 42 recovery = clamp01(abs(rsi_now - rsi_extreme)/span))
- agentic_trader/screeners/strategies.py:146-149 (long trigger: min(rsi_prev, rsi_prev2) < rsi_oversold_dip and rsi_current >= rsi_oversold); 160-163 trigger_detail prints 'dipped to X and recovered to Y' even when Y < X; 175-180 passes rsi_extreme=min_recent_rsi
- config/config.yaml:311-312 (rsi_oversold 40.0, rsi_oversold_dip 45.0); agentic_trader/config.py:420-421 same code defaults; docs/strategies.md:31-34 documents the rule without a rising requirement
- agentic_trader/data/market_data.py:119-138 resample_to_4h uses df_1h.resample('4h') on the index as given; :216-219 fetch_data builds 4h from 1h this way; agentic_trader/data/providers.py:292 Alpaca frames are UTC-indexed, so bins anchor at UTC midnight (00/04/08/12/16/20 UTC), which move one ET hour when DST ends
- agentic_trader/research/setups/replay.py:12-15, 237 (replay uses the same resample_to_4h, so the studies inherit the same bar definition)
- docs/setup-outcomes-2026-09-23.md: setup_quality dev rho +0.016 (Holm p 0.54), holdout -0.012; ridge holdout top-2 -0.293 vs setup_quality -0.126, paired CI [-0.325, -0.016], acceptance failed
- docs/setup-baserates-short-2026-09-23.md: S1 short mean R_cost -0.325R [-0.430, -0.212] on 2017-2021
- docs/setup-recurrence-2026-10-14.md: decision no_change (R1 -0.067R)
- docs/bracket-counterfactual-2026-10-18.md: no arm passes B1; A0_live excess -0.110R (2021-26), -0.103R (2017-21); arms -0.10 to -0.15R
- config/config.yaml:453-455 card_policy mode 'off'; agentic_trader/config.py:925,1043 live alpaca_data_feed defaults to iex while all replay priors are SIP all-adjusted
- docs/alpha-roadmap.md:555, 623: the roadmap already sends new alpha work to the entry side but does not freeze native research or record the RSI/DST defects; grep of docs/ and agentic_trader/ found no earlier mention of either defect
- Barber & Odean (2008) RFS 21(2):785-818: full text checked (abstract, Section 1, conclusion pp. 812-814)

**Bad references**

- Barber & Odean (2008), 'All That Glitters', RFS 21(2):785-818. The paper exists and is cited correctly, but it does not support the use as stated. Its empirical result is that individual investors are net BUYERS of attention-grabbing stocks: the paper says its 'principal empirical focus is on ... the imbalance in the number of purchases and sales', and it does not test subsequent returns. Poor subsequent returns appear only as a model proposition and as speculation ('could temporarily inflate a stock's price, leading to disappointing subsequent returns'). It also does not bear on these setups: the native trend-pullback longs buy a pullback to the 20-EMA inside a daily uptrend, not attention-grabbing movers. The setup studies themselves are the evidence for the finding; this paper adds nothing.

**Notes**

I could not refute the finding. Every cited code location exists and supports its claim:
- **RSI rule.** The 45/40 thresholds let the trigger fire while RSI declines, and the trigger text then misstates a fall as a recovery.
- **setup_quality.** The abs() treats a decline as recovery.
- **4h bars.** resample_to_4h has no session anchor, and Alpaca frames are UTC-indexed.

The four result docs report exactly the cited figures. A grep of docs/ and code found no earlier note of the RSI or DST defects. The roadmap (docs/alpha-roadmap.md:623) already says the next alpha work is on the entry side, but it does not freeze native research or schedule a native-v2. So the recommendation is not already done.

Minor issues, none of which change the conclusion:
- The 'five studies' count is really four studies with five results.
- 'fires on a falling RSI' should read 'can fire'.
- Before relying on post-11-01 data, the operator should also weigh the IEX-live vs SIP-prior mismatch the recommendation mentions (config.py:925, 1043).

I did not verify C10, C11 or C40; they are other findings. The extracted PDF text is at ~/agentic-trader-research/holistic-review-20261021/c41-pdf/bo2008.txt

### Value review — P1, now (days)

**Reasoning**

Fund the freeze and drop the native-v2 build.

The evidence is the strongest the desk has, much stronger than anything live outcomes will add for months:
- The bracket counterfactual replayed 8,768 setups (2021–26) and 8,119 (2017–21) through the unchanged live StrategyEngine. Every arm, the live one included, trails SPY by 0.10–0.15R in both windows (docs/bracket-counterfactual-2026-10-18.md, header).
- The frozen ridge ranker was significantly worse than setup_quality in the holdout (90% CI [−0.325, −0.016]). Every scorer lost after cost, random included (docs/setup-outcomes-2026-09-23.md, "Selection"/"Interpretation" §2).

Writing down a no-more-native-studies rule costs nothing. It stops the obvious temptation to try "one more construction tweak" on a source that is negative after the market in two independent windows. Some of this is already de facto: CLAUDE.md §8 sends the next alpha work to the entry side. Codifying it still has value because the finding itself proposes a native-v2.

The code defects are real:
- The long trigger is `min(rsi_prev, rsi_prev2) < 45 and rsi_current >= 40` (agentic_trader/screeners/strategies.py:148-149, with thresholds at config/config.yaml:311-312). An RSI path of 44.9 → 44.5 → 42 fires while still falling.
- `recovery = abs(rsi_now - rsi_extreme)` with `rsi_extreme=min_recent_rsi` (strategies.py:42, 177) rewards a falling RSI.
- The 4h bars are plain UTC `resample("4h")` (agentic_trader/data/market_data.py:119-135).

Even so, I would not fund native-v2, for four reasons:
1. The replay base rate was produced by the same unchanged engine, so it already embeds these defects. It spans years of both DST regimes, so the "before 11-01" deadline is overstated. The printed prior is honest about the rule as it stands.
2. setup_quality has ρ ≈ +0.016 in development and −0.012 in the holdout, so fixing the abs() bug changes the ranking of an uninformative score. Its expected effect is about zero.
3. A recovery-requiring RSI rule plus RTH bars is a new construction draw on a source with robust −0.10R excess. The signals map itself says it is "unlikely, alone, to make the natives positive after the market" (maps/signals.md:175). It would also invalidate the replay prior and need a fresh frozen replay, which spends research budget on the source being frozen.
4. The finding is internally inconsistent: it freezes construction research and then proposes a construction change.

The consequential product decision is not to fix TREND_PULLBACK. The question is what to do with all native cards. The SPY-excess evidence covers both native strategies, not just pullback, and natives fill the 2-card session budget (config.yaml:443-444). Demoting them with nothing to replace them would take card flow to about zero, against the operator's 1–2 cards a day north star. So the right move now has three parts:
- Adopt the freeze.
- Label native cards honestly with their replay excess over SPY.
- Let measured excess R decide withholding, rather than a one-off strategy kill.

Research hours then go to the one research-positive source: PEAD, +0.149R and +0.055R over its control (docs/apriori-pead-2026-09-26.md). Its probe expires 2026-10-28.

**Risks**

- **Lost card flow.** Demoting natives, or just TREND_PULLBACK, before a replacement source exists collapses card flow. The operator loses the daily cards and the end-to-end workflow exercise that setup-outcomes §2 says natives still provide.
- **Native-v2 as a forking path.** If built anyway, native-v2 is a garden-of-forking-paths draw. A lucky replay could re-legitimise a source that is negative after the market in two windows. It also resets the printed base rate with no improvement in expected value.
- **Over-reading the freeze.** The freeze could be read as forbidding legitimate conditioning work on natives as a primary signal, such as meta-labeling or the C40 decomposition. Keep the exceptions explicit and predeclared.
- **Weak benchmark.** Raw mean_r_cost is +0.03 to +0.04R in a rising tape, so a card_policy left on raw R (card_stats.py:50-62, card_policy.py:44,54) would never withhold natives. Any demotion has to run on excess R, or it does nothing.
- **Overstated deadline.** The 11-01 DST deadline in the finding is overstated. Rushing a native-v2 to meet it would be motivated by a false urgency.

**Prerequisites**

- An operator decision recording the freeze in the alpha roadmap. The only exceptions are C40 (decomposition) and C11 (stop width), each predeclared.
- An excess-R metric in card_stats/card_policy. `mean_excess_r` comes from the `market_r` already computed in research/setups/outcomes.py, keyed by (strategy, timeframe, direction).
- card_policy moved from "off" (config.yaml:454) to preview on that metric.
- A replacement or additional card source live before any native is withheld in enforce, for example:
  - PEAD probe renewal or risk parity before its 2026-10-28 expiry;
  - a panel/cross-sectional executor.
- If C40 shows the entry trigger specifically drives the loss, that result, not this finding, is the only thing that could justify a native-v2 spec.

**Better alternative**

Make no code changes to the native strategies.

1. Add one honesty line to native cards: the replay excess over SPY (about −0.10R in both windows) next to the existing base rate.
2. Put card_policy in preview with `min_measured_ev` measured in excess R, so natives get withheld on evidence, and only once another source can fill the session.
3. Spend the saved effort on scaling PEAD, the only research-positive source, before its probe expires on 2026-10-28.

This gets the honesty and redirection benefits at roughly a third of the effort, keeps daily card flow, and avoids a new construction draw on a source with a robustly negative excess return.

---

<a id="c52"></a>

## C52 — The operator loop is broken: cards are buried under ops traffic and the operator's judgment is never captured

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | product | gap | medium | S | partially_confirmed |

_lenses: product_

### Claim (as consolidated)

Since 2026-09-14, 42 cards have been sent:
- 34 expired;
- 3 closed (2 losses, 1 win);
- 1 is open;
- 2 failed.

None of the 6 cards sent from the 14:35 slot was tapped.

Telegram carried 659 incident notifications against 140 signal-family messages.

A skipped card leaves no reason, and the tap is binary. cards outcomes therefore cannot tell 'the operator disagreed' from 'the operator never saw it', although operator action is what defines a useful card.

### Evidence

- Production signals status since 2026-09-14 (EXPIRED 34, CLOSED_LOSS 2, CLOSED_WIN 1, EXECUTED 1, FAILED 2)
- Production work_items by dedup prefix (incident 659, signal 140, scan-digest 19)
- Live map Q3/Q10 (9 taps; 0 of 6 cards from the 14:35 slot tapped)

### Recommendation

1. With operator approval, route incident and ops notices to a separate bot or chat, leaving the outbox and dedup unchanged.

2. Add a 'Pass' button with 3–4 reason codes (late, already exposed, dislike the name or setup, size), journaled as card_passed.

3. Report time-to-tap and tap rate by slot and source, and compare tapped with untapped label R.

4. Align the slot times with when the operator can act.

### North-star impact

Makes the human stage measurable, shows directly why cards are skipped, and stops good cards being lost among hundreds of incident messages.

### References

_none_

### Fact check — partially_confirmed

**Corrected claim**

Since 2026-09-14 the paper desk has sent 42 cards. 34 expired, 2 are still PENDING (today's DHT and CLF), 2 closed at a loss, 1 closed at a win, 1 is executed and open (BLK), and 2 failed. The 34 expired include tapped cards that missed on re-price or were unavailable (cards 19, 20, 22 and 44), so about 33 expired with no tap. None of the 6 cards sent in the 14:35-14:55 window (ids 15, 24, 35, 43, 50, 52) was tapped. Card 15 predates the move to the 14:35 slot on 2026-09-22.

Every notification goes to one audible Telegram chat (config.py:870,988; no disable_notification anywhere). Over the period that chat carried:
- 659 incident notices (262 opened, 262 recovered, 135 reminders);
- 140 signal-family messages, of which only 41 are cards; the rest are 62 stop ratchets, 33 expiry notices and 4 exits.

The incident traffic is mostly evenings, nights and weekends. Only 28 of 659 arrived in weekday regular hours, none in the 14:00 hour, and only 11 within an hour of any card. So cards are not literally buried when they arrive. The plausible effect is overall alert fatigue or a muted chat, which nothing measures.

The tap is not binary. Cards already have Execute (with size tiers), Dismiss and Re-evaluate buttons (telegram_bot.py:1283,1455,1461). Dismiss sets status DISMISSED (db.py:743-752), but it records no reason and journals no event, and the operator has never used it (0 DISMISSED rows). `cards outcomes` already prints each card's signal status and tap age (execution_evidence.py:206-280, cards.py:81-82). It could therefore separate 'declined' (DISMISSED) from 'no action' (EXPIRED) if Dismiss were used. What it cannot separate is 'saw and ignored' from 'never saw'. It also computes no tap rate by slot or source and no comparison of tapped vs untapped label R.

Corrected recommendations:
- Add reason codes to the existing Dismiss button and journal a card_dismissed event, instead of adding a new Pass button.
- Add tap rate by slot and source, plus tapped vs untapped R. Time-to-tap is already journaled (age_seconds) and summarised (mean_tap_age_seconds).
- With operator approval, move incident and ops notices to a second chat, or send them silently with disable_notification.

**Checked**

- Production signals (read-only psql via scratchpad q.sh), execution_mode='alpaca:paper', not quarantined: 42 rows from 2026-09-14 11:58 ET to 2026-10-21 10:39 ET. EXPIRED 34, PENDING 2 (ids 53 DHT and 54 CLF, sent 2026-10-21 10:39 ET), CLOSED_LOSS 2, FAILED 2, CLOSED_WIN 1, EXECUTED 1, DISMISSED 0
- Cards sent in the 14:35-14:55 ET window: ids 15 (2026-09-18 14:41), 24, 35, 43, 50, 52. None has a card_tap_assessed event and all are EXPIRED. The domain_events card_tap_assessed rows cover cards 19, 20, 21, 22, 42, 44 and 47
- work_items kind='notification': incident 659 (262 opened, 262 recovered, 135 reminder) and signal 140 (41 signal, 62 stop, 33 card_expired, 4 exit). All were delivered between 2026-09-16 and 2026-10-21
- Time of the incident notices (NY time): 28 of 659 fell in weekday regular hours (09:30-16:00), 161 on weekends, 0 in the 14:00 hour and 18 in the 10:00-11:59 hours. Only 11 fell within ±60 min of any card notice. Most fell between 17:00 and 02:59
- agentic_trader/config.py:870,988: a single telegram_chat_id. telegram_bot.py:1469,1591,1636: every send goes to self.chat_id. No disable_notification anywhere in agentic_trader/
- agentic_trader/notifier/telegram_bot.py:281-296,1283,1455,1461: cards carry a 'Dismiss Signal' button next to the Execute/size-tier buttons. :1299-1313 is the dismiss handler. :1315-1336 is Re-evaluate
- agentic_trader/storage/db.py:743-752: dismiss_signal moves PENDING to DISMISSED with no reason field and journals no domain event (only a log line)
- agentic_trader/agent/copilot.py:3260-3300: each tap journals card_tap_assessed with age_seconds, r_consumed and outcome
- agentic_trader/research/setups/execution_evidence.py:126-136,206-259,264-280: cards outcomes' execution frame carries a status column per sent card (DISMISSED vs EXPIRED vs entered), tap_age_seconds and mean_tap_age_seconds
- agentic_trader/cli/commands/cards.py:81-82,96-98: cards outcomes prints that execution summary and the per-card frame
- agentic_trader/research/setups/outcomes.py:85-135: the labelled candidate columns have sent/signal_id but no operator-action or tap-rate column
- docs/production.md:1244: the report does not compute a tap rate (PEAD probe section)
- scratchpad maps/live.md section 3 item 5 and section 7 item 9 (Q3, Q9, Q10): source of the 9-tap and 0-of-6 figures

**Bad references**

- Evidence item 'Production signals status since 2026-09-14 (EXPIRED 34, CLOSED_LOSS 2, CLOSED_WIN 1, EXECUTED 1, FAILED 2)' leaves out the 2 PENDING rows (ids 53 and 54). Those counts sum to 40, not the 42 the claim states.
- Evidence item 'Production work_items by dedup prefix (incident 659, signal 140, ...)': the counts are right, but they do not support 'buried'. Only 41 of the 140 are cards, and only 28 of the 659 incident notices fell in weekday regular hours (11 within ±60 min of a card).

**Notes**

The counts hold up; the mechanism and the 'missing feature' framing do not.

Confirmed:
- The 42-card total and the 34 EXPIRED, 2 CLOSED_LOSS, 1 CLOSED_WIN, 1 EXECUTED and 2 FAILED counts.
- 0 of 6 cards from the 14:35 slot tapped.
- 659 incident vs 140 signal-family notifications.
- One chat, with every message audible.

Refuted or misdescribed:
1. 'Buried': the incident notices peak at 17:00-02:59 ET and on weekends, and almost none overlap the 10:39 and 14:38 card sends. An untapped card's tap freshness window is 1800 s (live map, freshness.py), so the overnight traffic cannot hide a card while it is still actionable. Alert fatigue or muting remains possible but is not evidenced.
2. 'A skipped card leaves no reason and the tap is binary': a Dismiss button already exists and writes DISMISSED. It lacks only a reason and a journal event, and the operator never uses it.
3. 'cards outcomes cannot tell disagreed from never saw': it already reports per-card status and tap age. The real gap is that untapped cards all end EXPIRED, with no tap-rate or slot breakdown.

The recommendations are sound once restated:
- Route or silence the incident traffic. Telegram's disable_notification would reduce noise without needing a second bot.
- Add reasons to Dismiss.
- Add a tap-rate and tapped vs untapped R report by slot and source.
- Align the slots with when the operator can act.

All DB checks were read-only (default_transaction_read_only=on) aggregate queries. No payload text, chat IDs or secrets were printed.

### Value review — P1, now (days)

**Reasoning**

Verdict: split the finding. Fund the noise fix now (P1, small effort). Demote the judgment-capture pieces to P3 or drop them. This work does not produce edge, so it is not P0. Cards trail SPY by about 0.11R after cost over their holding windows (docs/bracket-counterfactual-2026-10-18.md "Descriptives"). Getting the operator to read edgeless cards faster does not make them high quality.

What the code does now:
- Every Telegram send goes to one chat_id (notifier/telegram_bot.py:1469, 1591, 1636). There is no per-kind routing, although notification kinds already exist, OPERATIONAL among them (execution/durable.py:93-99). Incident notices are written as OPERATIONAL (storage/operations.py:47-52).
- An open incident re-notifies every reminder_seconds, 3600 by default (config.py:539, diagnostics/incidents.py:72-78).
- Readiness mixes research-lane workers into the same incident machinery as trading components: ALPHA_OBSERVER, ALPHA_DECISIONS, ALPHA_DAILY_PANEL and CORPORATE_ACTIONS (diagnostics/readiness.py:24-28, 88-95).
- The live map shows those research lanes are chronically degraded: every shadow forecast is invalid, and the session decisions are all unavailable (maps/live.md section 4, Q13). They are the likely source of the roughly 18 incident notices a day (659 over about 37 days).

Why the noise fix is P1 and not P2: it is more than UX. On a desk whose safety contracts depend on the operator noticing things, a channel that is about 75% incident traffic causes alarm fatigue. Examples of notices that must not be missed: unknown submission outcomes halting new risk, the "protection may have been removed" warning, entry_cancellation readiness.

The judgment-capture parts are weak:
1. A Dismiss button already exists on every card (telegram_bot.py:1455, 1461; handler :1299-1313; db.py:743-752 sets DISMISSED). The finding's own status counts contain no DISMISSED, so the operator never uses it. The behaviour is "didn't look or didn't bother", not "disagreed". A new Pass button with reason codes would probably get the same near-zero uptake.
2. Card quality does not need taps. card_stats and cards outcomes label every sent card and every runner-up counterfactually, whether or not it was tapped (maps/live.md section 2 "Evidence/policy"). The agreed definition of a reasonable card is honest base rate, bracket EV ≥ 0 and book-consistent size (docs/alpha-roadmap.md "Operational definition of a reasonable suggestion"). Operator action is not part of it. So the premise that operator action defines a useful card is wrong for this system.
3. Comparing tapped and untapped label R with 9 taps and 3 fills has no statistical power.
4. Most of the 14:35 slot's emptiness comes from swing and restart scans spending the session budget first (maps/live.md gap 3: 14 of 37 native cards were sent outside the slots, and "per-session budget spent" appears 26 times). Slot timing is not the main cause, so realigning slots before fixing the swing scan treats the wrong cause.

Time-to-tap and tap rate by slot are a one-off read-only query on existing card_tap_assessed events and signal timestamps. They do not need new code.

**Risks**

- **Muted ops chat.** Routing all OPERATIONAL notices to a second chat the operator mutes would hide trading-critical incidents: broker or stream freshness, accounting staleness, entry_cancellation, unknown-outcome halts. Routing must go by component severity, not by kind alone.
- **Trailing-stop notices.** CLAUDE.md contract 3 requires a notice for every confirmed trailing-stop change, at the operator's request. The 62 trailing-stop notices cannot be batched or moved without the operator's explicit approval.
- **Second poller.** A second bot that also polls would break the "do not start a second poller" rule. A send-only second chat (or bot) avoids this, and its id must stay in env, out of git.
- **Pass button.** It adds another callback on the serialized Telegram handler path (contract 7) and gives n≈1 a day of one person's unvalidated opinion. It invites someone to treat it later as a label or a gate.
- **Slot changes.** Live 4h bars are UTC-anchored and shift at the 2026-11-01 DST change (data/market_data.py:119-138). Moving scan times without re-checking which bar is evaluated can make cards read partial bars that differ from the replay base rates.
- **Masking broken lanes.** Silencing research-lane incidents can hide that those lanes have been blind for weeks. Their degraded state should stay visible in the digest and in doctor.

**Prerequisites**

- A read-only breakdown of the 659 incident notices by component and notice kind (opened / reminder / recovered) from production work_items and incident/<component> events. This confirms that research-lane reminders dominate before anything is changed.
- The operator's approval of a severity tiering, and of a second chat if one is still wanted.
- The swing-scan budget fix (maps/live.md improvement 3) must land before the 14:35 slot is judged or moved.
- A short direct conversation with the operator on why cards go untapped, before any telemetry is built.

**Better alternative**

1. **Fix the source instead of re-routing the symptom.** Add a severity tier to HealthComponent:
   - Research-lane components (alpha_observer, alpha_decisions, alpha_daily_panel, and corporate_actions, whose stale evidence never refuses) send only open and recovered notices, with no hourly reminders. They roll up into the end-of-session scan digest.
   - Trading-critical components keep the current reminder policy in the main chat.

   This is a small policy change in diagnostics/incidents.py and storage/operations.py. It leaves the outbox and dedup unchanged, needs no second chat, and should remove most of the 659 notices.
2. **If reason capture is still wanted, extend the existing Dismiss button** with 3–4 reason sub-buttons that write the reason on the DISMISSED transition and a journal event. Do not add a new Pass button.
3. **Since there is one operator, just ask him** why the 14:35 cards and most cards go untapped.
4. **For evidence volume, the higher-leverage change is operator-approved auto-entry of cards on the paper account** (at least probe cards; see maps/live.md improvement 7). Taps are the bottleneck for probe and fill evidence, not for card-quality labels.
5. **Drop the tapped-vs-untapped R comparison.**

---

<a id="c54"></a>

## C54 — Config loading is hand-wired, silently drops blocks and ships dead knobs that misdescribe behaviour

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | now (days) | architecture | defect | medium | S | confirmed |

_lenses: architecture_

### Claim (as consolidated)

Blocks are dropped:
- load_config hand-wires about 30 blocks and omits session= and apriori=, although AppConfig declares both (config.py:842, 863, 1109-1179).
- config.yaml's session block is therefore silently dropped. This is harmless only because its values equal the defaults.

Typos and dead knobs pass unnoticed:
- Only 5 sub-models forbid unknown keys.
- trailing_stop.mode: chandelier_atr is never read.

The same class of bug silently ignored corporate_actions, including entry_block: enforce, until 2026-10-13.

### Evidence

- agentic_trader/config.py:842, 863, 1109-1179
- agentic_trader/config.py:232, 276, 309, 357, 779 (only extra=forbid models)
- config/config.yaml:414-417, 421; agentic_trader/agent/copilot.py:2553-2571
- docs/alpha-roadmap.md:591 (corporate_actions loader fix)

### Recommendation

1. Build AppConfig with a single model_validate over the merged YAML and environment dict, with extra='forbid' on every sub-model.

2. Add a test that every top-level YAML key maps to a field and round-trips.

3. Delete or wire up the dead knobs: trailing_stop.mode, strategies.strategy_allocations and the unused indicator parameters.

4. Move experiment policy into validated config: source-family budgets, selector id and execution-policy id.

### North-star impact

Prevents an operator decision, such as an enforce flip, from silently not taking effect, and makes stage experiments configuration rather than code.

### References

_none_

### Fact check — confirmed

**Corrected claim**

_none_

**Checked**

- agentic_trader/config.py:834-866: AppConfig declares apriori (842) and session (863), and has no model_config, so pydantic 2.13.5's default extra='ignore' applies
- agentic_trader/config.py:1109-1179: load_config builds AppConfig keyword by keyword for 28 blocks. Every declared block is passed except apriori= and session=. Nothing in load_config (955-1180) checks top-level YAML keys against AppConfig fields.
- agentic_trader/config.py:232, 276, 309, 357, 779: these are the only model_config extra=forbid declarations (CardPolicyConfig, BookSizingConfig, CorporateActionsConfig, NewsConfig, DailyPanelWorkerConfig). No ConfigDict or shared base model exists elsewhere in config.py.
- config/config.yaml:414-417: the session block (enforce_rth true, allow_extended_hours false, timezone America/New_York) equals the SessionConfig defaults at agentic_trader/config.py:660-666, so dropping it changes nothing today
- agentic_trader/risk/limits.py:119 and agentic_trader/diagnostics/doctor.py:360-361 read config.session, so the dropped block would matter if anyone changed it
- config/config.yaml has no apriori block. config.apriori is read at agentic_trader/agent/copilot.py:385, 394 and 905, so YAML can never change those values.
- config/config.yaml:421 sets trailing_stop.mode: chandelier_atr. A grep for trailing_stop.mode, policy.mode and ts_cfg.mode finds no reader. The live ratchet (agentic_trader/agent/copilot.py:2528-2571) and the backtest engine (agentic_trader/backtest/engine.py:292-338) branch only on enabled, breakeven_trigger_r and trail_* fields. The CLI (agentic_trader/cli/commands/backtest.py:120-135) sets mode but nothing reads it.
- agentic_trader/agent/copilot.py:2565: the live trail distance is max(risk, risk*trail_atr_multiple), a multiple of initial risk rather than ATR, so the 'chandelier_atr (quant ATR high-water mark)' comment at config.py:671 and config.yaml:421 misdescribes live behaviour
- strategies.strategy_allocations is declared at agentic_trader/config.py:449 and passed through at line 1094, but nothing in agentic_trader/ reads it
- docs/alpha-roadmap.md:591: says 'The loader now passes the corporate_actions: and news: YAML blocks (entry_block: enforce was previously ignored)'
- git show 4de9224 (#117) commit message confirms that load_config never passed corporate_actions=. git show 4de9224^:config/config.yaml has no corporate_actions block.
- tests/: no test checks that every top-level config.yaml key maps to an AppConfig field. tests/config/test_card_policy_config.py:111 checks one block only.

**Bad references**

_none_

**Notes**

Every cited location exists and supports the finding.

Exactly two AppConfig fields are missing from the load_config wiring: apriori and session. The apriori omission has no effect today because config.yaml has no apriori block, but an operator who added one would be silently ignored. The session block is dropped, and that is harmless only because its values equal the defaults.

The five extra=forbid models are exactly the ones cited. Every other sub-model, and AppConfig itself, silently ignores unknown keys under pydantic v2's default.

trailing_stop.mode is truly dead: neither the live ratchet nor the backtest engine reads it. Its 'chandelier_atr / ATR high-water mark' label also misdescribes the live trail, which uses max(risk, risk*trail_atr_multiple), a multiple of initial risk, at copilot.py:2565. strategy_allocations is also never read.

One nuance on the corporate_actions history: before #117 (2026-10-13), the shipped config.yaml had no corporate_actions block. So no shipped 'enforce' value was actually ignored. The bug was latent: the documented entry_block setting would have been silently ignored if an operator had set it. This matches the #117 commit text ('silently ignored and the defaults ran') and docs/alpha-roadmap.md:591, so the finding is supported as written.

Minor counting detail: load_config wires 28 blocks, which fits 'about 30'.

### Value review — P1, now (days)

**Reasoning**

The defect is real, I checked it against the code, and it affects live risk controls, not just tidiness.

1. load_config builds AppConfig by hand (agentic_trader/config.py:1109-1179). It never passes `session=` or `apriori=`, although both are declared fields (config.py:842, 863). Both are read in production:
   - `config.session.enforce_rth` feeds the entry risk limits (risk/limits.py:119).
   - `config.apriori.*` controls the PEAD drift-card switch, the per-session cap, the open-position cap and the lateness gate (agent/copilot.py:385, 905-907, 937, 1277, 1993).
   - If the operator sets `session.enforce_rth: false` or `apriori.enabled: false` in config/config.yaml (session block at lines 414-417), nothing happens and nothing warns. Today this is harmless only because the YAML values equal the defaults (SessionConfig, config.py:660-666).

2. Only five sub-models forbid unknown keys (config.py:232, 276, 309, 357, 779), and nothing checks unknown top-level keys. Typos therefore pass silently.

3. This failure has already happened once. The `corporate_actions:` block, including `entry_block: enforce`, was silently ignored until the 2026-10-13 loader fix (CLAUDE.md, news-context paragraph; news-context workstream). The desk's control mechanism is preview-then-enforce flips decided by the operator, such as book_sizing and corporate_action going to enforce on 2026-10-20. A loader that can silently discard such a flip undermines that mechanism.

4. The dead knob is worse than unused; it describes the wrong behaviour:
   - `trailing_stop.mode: "chandelier_atr"` (config/config.yaml:421; TrailingStopConfig.mode, config.py:671) is never read on the live path. It only appears as an unrelated backtest CLI argument (cli/commands/backtest.py:110-122).
   - The live ratchet computes `current - sign*max(risk, risk*trail_atr_multiple)` (agent/copilot.py:2569). That trails by a multiple of initial risk R, not ATR.
   - So both the YAML comment and the field name misdescribe live stop behaviour. That matters for anyone reading the bracket-counterfactual result, which modelled the "live 1.5R ratchet" (docs/bracket-counterfactual-2026-10-18.md).

Why P1 rather than P0: this fix creates no edge and will not raise card quality. Cards still trail SPY for reasons unrelated to config. But it is about an hour or two of work. It protects every future enforce or kill decision and every risk knob, and the failure mode has already occurred once in production. Cheap insurance for the control plane, worth doing now.

Item 4 of the recommendation (moving source-family budgets, selector id and execution-policy id into config) is a separate design change with its own blast radius. It belongs in the stage-seam work, not this fix. I would drop it from this item.

**Risks**

- **Daemon fails to start after deploy.** Turning on extra='forbid' across every sub-model in one step can do this. Any stale or legacy key in the shipped YAML would raise at startup, and the watchdog would restart-loop. Examples: the keys the strategy loader filters on purpose (config.py:1094), or contract/universe documents with extra metadata.
- **Breaking environment contracts.** Rewriting load_config as one model_validate over merged YAML and environment can break subtle contracts:
  - load_config must not mutate os.environ.
  - The TEST environment strips Telegram credentials (config.py:1146-1151).
  - Placeholder-token filtering ("your_") must keep working.
  - The database URL/path derivation must stay intact.
  - The CLAUDE.md test guards (stripped credentials, explicit fixture config) depend on these.
- **Changing derived values without anyone noticing.** The correlation groups merged from universe sector groups (config.py:1103-1107) and the explicit-contract precedence (config.py:1101-1102) are computed values. A generic validator would replace them with raw YAML unless they are kept as explicit post-validation steps.
- **Silently changing a stop parameter.** Renaming or deleting trailing_stop.mode and trail_atr_multiple touches a live stop-ratchet input. The rename must keep the R-based behaviour exactly, or it changes live stops. Contract 3 requires a notice for every confirmed trailing-stop change.

**Prerequisites**

- Do the work in an isolated worktree, as CLAUDE.md requires.
- Pause the watchdog and then the daemon before deploying.
- Write the failing tests first: a test that loads the shipped config/config.yaml and asserts each top-level key is an AppConfig field, and a test that a non-default `session.enforce_rth` or `apriori.enabled` round-trips through load_config.
- Inventory every key in the shipped YAML against its model before turning on forbid anywhere.
- Bundle the change with the next workstream PR, following the no-docs-only-PR memory. If kept standalone, keep it tiny and run the full affected test suite.

**Better alternative**

Do a minimal hardening patch now instead of the full single-model_validate rewrite. It gets most of the protection for much less blast radius:
1. Pass `session=SessionConfig(**cfg_dict.get("session") or {})` and `apriori=AprioriConfig(**cfg_dict.get("apriori") or {})` in load_config.
2. Fail closed at load when `set(cfg_dict) - set(AppConfig.model_fields)` is non-empty. Also report non-default sub-model fields that load_config does not consume.
3. Add `extra="forbid"` only to the operator-policy blocks whose silent drop has consequences (risk, sizing, book_sizing, card_policy, scan, corporate_actions, news, session, apriori, trailing_stop), each guarded by the shipped-YAML load test.
4. Delete trailing_stop.mode from the model and the YAML. Rename trail_atr_multiple to a name that says R, such as trail_risk_multiple, or document it in place as an R multiple, updating callers in the same change as CLAUDE.md requires.

Leave experiment-policy-in-config to the stage-seam workstream.
