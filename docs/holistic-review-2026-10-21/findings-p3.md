# Holistic review findings register — P3 (12 findings)

Each canonical finding as consolidated from the seven expert lenses, followed by its independent
fact check (a skeptic told to refute it against the code) and value review (priority and horizon).
Where the fact check corrected a claim, the corrected claim governs. See [README](README.md).

<a id="c14"></a>

## C14 — Card selection is an inline sort on a key measured as uninformative; build L3 as an injected selector with shadow challengers

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | signals | gap | high | M | partially_confirmed |

_lenses: architecture, product_

### Claim (as consolidated)

TradingCopilot.run_scan is about 1,000 lines inside a 4,527-line class. It sorts a list[tuple[Any, Any, Any]] by −setup_quality, then applies inline budget and group closures and PEAD carve-outs (copilot.py:869, 1174-1177, 1268-1300).

setup_quality carries no information: ρ is +0.016 in development and −0.012 in holdout. The frozen ridge ranker did worse.

The L3 card-selector seam is still Planned (alpha-roadmap.md:494).

Selection within one source has small upside (ρ about 0.03–0.05). The seam's main value is that each research result becomes a config swap measured on the live stream.

### Evidence

- agentic_trader/agent/copilot.py:869, 1174-1177, 1268-1300
- docs/setup-outcomes-2026-09-23.md 'Hypotheses' H1 and 'Selection (H3)'
- docs/alpha-roadmap.md:494 (L3 Planned)
- agentic_trader/research/setups/card_stats.py:43-62 (no excess field for a selector objective)

### Recommendation

Define a CardSelector protocol.
- Input: Sequence[Forecast/CardCandidate] (source, horizon, expected excess R ± se, p_target), a typed BookSnapshot, and budgets derived from signals rows.
- Output: a typed Selection with ordered picks, a RiskRule refusal code per candidate, and the book after the picks.
- Journal ranking_key as the selector id.

Make the budget pools (native, catalog/probe, group) config keyed by source family, which removes the PEAD branches from run_scan.

Run challengers in shadow and score them on beta-adjusted excess with the existing selection block:
- setup_quality;
- random;
- a vol_20 + sector_rel_mom_60 rank composite;
- a −Δbook-vol diversification tie-break;
- later, a meta-label (C34).

Fold card_policy in as the withholding rule, applied to the shrunk excess posterior (C03).

### North-star impact

Choosing the 1-2 daily cards becomes an A/B experiment in config with a measurable excess-R objective, not a code change.

### References

- López de Prado (2018), Advances in Financial Machine Learning, ch. 3 (meta-labeling) — A secondary classifier filters or sizes a primary signal
- Joubert (2022), Meta-Labeling: Theory and Framework, J. Financial Data Science — Meta-labels improve precision on top of weak primary models

### Fact check — partially_confirmed

**Corrected claim**

Most of the finding is correct. run_scan (copilot.py:682-1678, about 1,000 lines) collects approvals into a list[tuple[Any, Any, Any]] (copilot.py:869). It sorts them by (-setup_quality, contract, strategy) (copilot.py:1174-1177) and appends PEAD drift candidates after the native ranking (copilot.py:1200). Budgets are derived inline from signals rows, with a PEAD native/drift split and the card_groups/capped_groups closures (copilot.py:1268-1300). The send loop then branches on is_drift.

Three details need correcting:
1. **Class size.** The 4,527 lines are the file. TradingCopilot starts at line 244, so the class is about 4,280 lines.
2. **ranking_key.** It is already journaled on every scan_candidates_ranked event, but hard-coded as the constant "setup_quality" (copilot.py:1907). The recommendation should say "make ranking_key the selector id", not add it.
3. **Shadow challengers.** Part of this already exists:
   - There is one shadow-ranker slot (config.py:203, scan.shadow_ranker_artifact). It is unconfigured, because the ridge artifact was deliberately not installed (setup-outcomes-2026-09-23.md, 'Interpretation' item 1).
   - Every ranked candidate in a scheduled scan journals a setup_features_v2 vector that includes vol_20 and sector_rel_mom_60 (copilot.py:1816-1849; ranker.py:296-312).
   - `cards outcomes` already has a selection block comparing setup_quality, shadow_score and random top-1/top-2 (outcomes.py:925-935). It scores mean r_cost, not excess. The per-row excess_r is r_cost minus a same-bracket, beta-one SPY control (outcomes.py:187-201, 383), not a beta-adjusted excess.

So a vol_20 + sector_rel_mom_60 composite can already be scored counterfactually from the journal without the seam. What is genuinely missing is narrower:
- an injected selector with a typed selection result, so a challenger can be swapped in live and PEAD branches leave run_scan (roadmap L3, still Planned at alpha-roadmap.md:494);
- more than one shadow scorer per scan;
- an excess-R objective in the selection block and in CardKeyStats (card_stats.py:43-62 has only mean_r_cost).

The doc evidence is accurate:
- setup_quality is uninformative: rho +0.016 in development, -0.012 in holdout.
- The frozen ridge ranker was significantly worse in holdout (-0.293 vs -0.126 top-2), and random (-0.118) slightly beat setup_quality.
- The surviving features have rho of about 0.03-0.05.

**Checked**

- agentic_trader/agent/copilot.py:244 (class TradingCopilot starts; no top-level code after it, so the class runs to line 4527)
- agentic_trader/agent/copilot.py:682-1678 (run_scan is about 997 lines)
- agentic_trader/agent/copilot.py:635-637 (_setup_quality helper)
- agentic_trader/agent/copilot.py:869 (approved_candidates: list[tuple[Any, Any, Any]])
- agentic_trader/agent/copilot.py:1174-1177 (sorted by -setup_quality, then contract and strategy)
- agentic_trader/agent/copilot.py:1200-1201 (PEAD drift candidates appended after the native ranking)
- agentic_trader/agent/copilot.py:1268-1300 (signals_since budget derivation, PEAD_STRATEGY_ID native/drift split, card_groups/capped_groups closures)
- agentic_trader/agent/copilot.py:1302-1340 (send loop with is_drift branches)
- agentic_trader/agent/copilot.py:1787-1849 (_shadow_blocks/_compute_shadow_blocks: per-candidate setup feature vectors plus an optional ranker score)
- agentic_trader/agent/copilot.py:1907 (ranking_key already journaled, hard-coded to 'setup_quality')
- agentic_trader/config.py:203 (scan.shadow_ranker_artifact slot); config/ has no shadow_ranker setting (unconfigured)
- agentic_trader/research/setups/ranker.py:296-312 (shadow_blocks journals features_version, features, score and ranker_sha)
- agentic_trader/research/setups/features.py:42-55 (setup_features_v2 includes vol_20 and sector_rel_mom_60)
- agentic_trader/research/setups/outcomes.py:645-674, 925-935 (selection block compares setup_quality, shadow_score and random top-1/top-2 on r_cost)
- agentic_trader/research/setups/outcomes.py:187-201, 383 (excess_r = r_cost - market_r, a same-bracket beta-one SPY control)
- agentic_trader/research/setups/card_stats.py:43-62 (CardKeyStats has mean_r_cost and no excess field)
- docs/setup-outcomes-2026-09-23.md 'Hypotheses' (H1 rho +0.016, holdout -0.012; vol_20 +0.049 and sector_rel_mom_60 +0.028 survive Holm; 'rho about 0.03-0.05')
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)' (holdout ridge -0.293 vs setup_quality -0.126 vs random -0.118; paired CI [-0.325,-0.016]); 'Interpretation' item 1 (artifact not configured as shadow_ranker_artifact)
- docs/alpha-roadmap.md:494 (L3 card-selector seam: Planned; acceptance wording matches the finding)
- docs/alpha-roadmap.md:522 (also calls book-aware sizing 'the deferred L3 seam'; book sizing has shipped as the injected self.book_sizer)
- docs/risk-policy.md:466 (unifying correlation_groups_of belongs to the L3 card-selector seam)
- Web: Joubert 2022 JFDS 4(3):31-44 at pm-research.com; AFML ch. 3 covers meta-labeling

**Bad references**

_none_

**Notes**

All cited file:line locations exist and support their use:
- copilot.py:869 is the untyped tuple list.
- copilot.py:1174-1177 is the setup_quality sort.
- copilot.py:1268-1300 is the PEAD budget split and the group closures.
- card_stats.py:43-62 has no excess field.
- alpha-roadmap.md:494 is L3 Planned.

The setup-outcomes doc figures match exactly. The Joubert citation is real: JFDS Summer 2022, 4(3):31-44, DOI 10.3905/jfds.2022.1.098 (https://www.pm-research.com/content/iijjfds/4/3/31; SSRN https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4032018). López de Prado's AFML ch. 3 (Labeling) contains the meta-labeling section.

The main overstatement is that the finding presents shadow challengers and ranking_key journaling as wholly new. The journal and the outcomes report already provide a one-slot shadow scorer, per-candidate feature vectors and a setup_quality/shadow/random selection comparison. The real gaps are the swappable live seam, multiple challengers, and an excess-R objective.

A small roadmap inconsistency also exists. alpha-roadmap.md:522 calls book-aware sizing "the deferred L3 seam", and copilot.py:1205 comments "BOOK SIZING (L3)", while line 494 keeps L3 as the card selector. The selector half is the part still unbuilt.

### Value review — P3, later (months)

**Reasoning**

The diagnosis is accurate, but the claim that this is high-leverage does not hold up once you check what the system already measures.

**What the code does now (confirmed):**
- run_scan sorts `approved_candidates` by `-setup_quality` (agentic_trader/agent/copilot.py:1174-1177).
- The ranked journal hard-codes `"ranking_key": "setup_quality"` (copilot.py:1907).
- PEAD and native budgets are split inline on `PEAD_STRATEGY_ID` (copilot.py:1273-1279).
- setup_quality is uninformative: development ρ is +0.016 and holdout ρ is −0.012 (docs/setup-outcomes-2026-09-23.md, 'Hypotheses', H1).
- L3 is still Planned (docs/alpha-roadmap.md:494).

**The live shadow-measurement loop the finding asks for already mostly exists:**
- Every scheduled universe scan computes a setup-feature vector per ranked candidate (copilot.py:1816-1849). The vector includes `vol_20` and `sector_rel_mom_60` (agentic_trader/research/setups/features.py:48-52, 139, 237).
- An optional `scan.shadow_ranker_artifact` scores the candidates in shadow (agentic_trader/config.py:203; copilot.py:1832-1834).
- The full ranked population, including runners-up, LLM vetoes and card_policy withholds, is journaled.
- `cards outcomes` labels every row and already compares selectors offline. `_selection_by_score` covers `setup_quality` and `shadow_score`, and `_selection_random` covers random (agentic_trader/research/setups/outcomes.py:645-675, 932-933).

So adding the proposed challengers (random, a vol_20+sector_rel_mom_60 rank composite, a −Δbook-vol tie-break) needs only new score columns in the outcomes report. It needs no live seam, it is retroactive over every journaled scan, and it carries no card-flow risk. The one real measurement gap is that selection is scored on `r_cost`, not `excess_r` (outcomes.py:657-662). card_stats also has no excess field (agentic_trader/research/setups/card_stats.py:43-62). That is a small change.

**The decisive point is the north star.** In the one-shot holdout, every scorer's top-2 lost money after costs: random −0.118R, setup_quality −0.126R, and ridge −0.293R, which is significantly worse (docs/setup-outcomes-2026-09-23.md, 'Selection (H3)' and 'Interpretation' item 2). Even the best surviving features have ρ of only about 0.03–0.05. Reordering a pool whose expected value is negative relative to SPY cannot produce high-quality cards. The binding constraint is the candidate source, not the ordering.

**Live A/B testing is also underpowered.** At 1–2 cards a day, telling apart selectors that differ by ρ≈0.03 takes many hundreds of sessions. The offline runner-up comparison, and the historical setup replay, are far more powerful.

**Meta-labelling (López de Prado ch. 3; Joubert 2022) is the wrong tool here.** It filters a primary model that already has edge. The ridge result is direct local evidence that a learned secondary ranker on this pool overfits.

**When it becomes worth funding.** The seam pays off once there are at least two candidate sources and at least one has measured positive excess R to arbitrate against native cards, for example a probe alpha or catalog leg that passes. At that point, budget pools keyed by source family and a typed Selection carrying RiskRule refusals are the right design. Removing the PEAD branches from run_scan is also legitimate cleanup, best done together with L4.

Until then, the refactor is roughly 1,000 lines of change to the most safety-critical scan path, with no expected gain in card quality.

**Risks**

**Card-flow regressions.** Refactoring run_scan's budget logic can break the steady stream of cards. That logic includes:
- the durable per-session spend read back from `signals` rows, which must stay restart-safe (copilot.py:1268-1283);
- the operator-dynamic group cap (copilot.py:1262-1267, 1291-1297);
- drift-versus-native budget separation;
- card_policy withholding and book-sizing interplay.

**Selector-shopping on a tiny live stream.** Running several shadow challengers creates garden-of-forking-paths risk: one of them will look good by chance unless a protocol and a single read are predeclared.

**Overfitting from learned rankers.** A meta-labeller trained on a negative-EV pool will overfit, as ridge already did (holdout −0.293R versus −0.126R).

**Unbuilt dependency.** Folding card_policy into a selector that depends on C03's shrunk excess posterior ties this item to something that does not exist yet.

**Misplaced effort.** Engineering time diverts from the entry-side source search, which is where the null results point.

**Prerequisites**

The full seam needs the following first:
1. At least one candidate source with measured positive beta-adjusted excess R on live or forward evidence. Otherwise there is nothing worth arbitrating.
2. An `excess_r` selection objective in `cards outcomes` and an excess field in card_stats.
3. A predeclared challenger protocol: a fixed list of challengers, a power calculation on journaled runner-up counts, and a single read.
4. C03 (shrunk excess posterior), if card_policy is folded into the selector.
5. Sequencing with L4 (evaluator split), so run_scan is cut once rather than twice.

**Better alternative**

Skip the live seam for now and extend the offline comparison that already exists. This is about a day of work, with no runtime change and no card-flow risk.

1. In agentic_trader/research/setups/outcomes.py, have `_selection_by_score` also report top-1 and top-2 mean `excess_r` alongside `r_cost`.
2. Add predeclared challenger score columns computed from the journaled shadow feature vectors: a vol_20 + sector_rel_mom_60 rank composite, a direction-filtered variant (no shorts, per setup-outcomes follow-up 1), and random as the baseline.
3. Score all of them retroactively over every journaled `scan_candidates_ranked` scan, with session-block bootstrap CIs.

Only if a challenger beats random on excess R over a predeclared sample should you build the L3 CardSelector seam to switch it live. Build the seam then, or when a second positive-excess source arrives. In the meantime, put the funding into the entry-side source search.

---

<a id="c34"></a>

## C34 — Meta-labelling native setups already lost in its holdout: any L3 meta-label needs a different target, features and selection metric, should withhold rather than rank, and should be expected to help only modestly

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | signals | research_direction | medium | M | partially_confirmed |

_lenses: ml, literature_

### Claim (as consolidated)

The setup-outcome study fitted logistic, ridge and HGB models on setup_features_v2 over 14,017 setups. It used purged walk-forward validation (5 folds, 20-session embargo) and selected on the median fold's top-2 mean R (study.py:607-614).

Ridge's development gain came from a single fold. In the 116-session holdout it did significantly worse than setup_quality: top-2 −0.293R against −0.126R, CI90 [−0.325, −0.016].

The target was raw R_cost, while the native population's excess is about −0.11R. A meta-label can therefore at best withhold cards.

label_bracket is already a triple-barrier label (labels.py:159-189).

Public evidence for meta-labelling gains is thin: precision improved from 0.17 to 0.20 and from 0.48 to 0.54 on just two strategies.

Journaled rows repeat setups: 87 rows cover 41 setups.

### Evidence

- agentic_trader/research/setups/study.py:58-59, 289-313, 402, 607-614
- agentic_trader/research/setups/features.py:44-59; research/setups/labels.py:159-189
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)'; docs/setup-baserates-short-2026-09-23.md (2017–2021: 11,480 setups)
- docs/card-evidence.md 'Limits / Repeated setups'

### Recommendation

Redo it only as a new frozen protocol:
- Target: beta-adjusted excess (alpha_R > 0), or P(target before stop) weighted by excess.
- Features: the library's D-1 cross-sectional ranks, setup geometry, the v3 veto features, industry momentum and the composite score.
- Data: both replays, 14,017 + 11,480 setups.
- Validation: uniqueness weights and combinatorial purged CV with an embargo of at least 20 sessions.
- Selection: per-session rank IC across all candidates; take the simplest model within 1 SE. Report PBO across the model choices.
- Calibration: isotonic, before any card shows a probability.

Use it as a card_policy withholding rule in preview, A/B-tested against setup_quality and against uniform-random selection.

Apply the same machinery to PEAD events (positive base rate, about 4,726 events) for sizing.

### North-star impact

Could remove the worst native cards and size PEAD cards without repeating a design that already failed. Expect a modest effect.

### References

- López de Prado (2018), Advances in Financial Machine Learning, ch. 3, 4, 7 — Triple-barrier labels, meta-labelling, uniqueness weights, purged CV
- Singh & Joubert, Does Meta-Labeling Add to Signal Efficacy? (Hudson & Thames) — Only modest out-of-sample precision gains (https://hudsonthames.org/wp-content/uploads/2022/04/Does-Meta-Labeling-Add-to-Signal-Efficacy.pdf)
- skfolio CombinatorialPurgedCV — Maintained CPCV implementation (https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)

### Fact check — partially_confirmed

**Corrected claim**

The setup-outcome study (protocol config/research/setup-outcomes-v1.json, setup_features_v2) fitted three models on 14,017 development setups (study.py:361-408):
- logistic, trained on the binary target-hit label (study.py:370);
- ridge and HGB, both regressing raw R_cost (study.py:387, 401).

Validation used purged expanding walk-forward folds (5 folds, 20-session embargo; study.py:289-314). The winner was the model with the highest median fold top-2 mean raw R_cost (study.py:557-588, 607-614).

Ridge won. Its development gain was concentrated in fold 3 (docs/setup-outcomes-2026-09-23.md 'Interpretation'). In the 116-session one-shot holdout it was significantly worse than setup_quality: top-2 -0.293R against -0.126R, CI90 [-0.325, -0.016]. No target, and no selection metric, was beta-adjusted.

The native long population's excess over SPY is about -0.11R: -0.110R in the primary window and -0.103R in the robustness window (docs/bracket-counterfactual-2026-10-18.md 'Descriptives'). A meta-label can therefore at best withhold cards.

label_bracket is already a triple-barrier label: stop, target and a max_hold_sessions timeout (labels.py:159-327).

The public meta-labelling evidence cited (Singh & Joubert, Hudson & Thames) is two strategies on S&P 500 E-mini. Out-of-sample precision went from 0.17 to 0.20 and from 0.48 to 0.54.

Repeated journaling inflates live counts (docs/card-evidence.md 'Repeated setups'). The specific '87 rows cover 41 setups' comes from a live journal query, not from that doc.

**Checked**

- agentic_trader/research/setups/study.py:58-59: SCORERS = setup_quality, logistic, ridge, hgb; _FITTED_CANDIDATES = logistic, ridge, hgb. Confirmed.
- agentic_trader/research/setups/study.py:289-314: purged_walk_forward is an expanding window that drops the last `embargo` train sessions before each test chunk. Confirmed.
- agentic_trader/research/setups/study.py:361-408: the logistic target is hit=='target' (line 370); ridge (line 387) and HGB (line 401) regress raw r_cost; HGB is constructed at line 402.
- agentic_trader/research/setups/study.py:557-588 and 607-614: each fold's metric is the top_k mean of r_cost, and the winner is the fitted candidate with the highest median fold metric. Confirmed.
- agentic_trader/research/setups/study.py:320-325 and features.py:44-59: the model features are CROSS_SECTIONAL (8) + MARKET (2) + SETUP (3) + strategy/timeframe one-hots, setup_features_v2. Confirmed.
- config/research/setup-outcomes-v1.json: cv_folds 5, embargo_sessions 20, acceptance.top_k 2, ci 0.9, min_sessions 60, max_hold_sessions 20, cost 0/5 bp. Confirmed.
- agentic_trader/research/setups/labels.py:159-327: label_bracket has a stop barrier, a target barrier and a timeout at max_hold_sessions (lines 252-266). It is a triple-barrier label. Confirmed.
- docs/setup-outcomes-2026-09-23.md 'Protocol and run', 'Selection (H3)', 'Interpretation': 14,017 development setups over 1,163 sessions; 5 folds with a 20-session embargo; ridge folds -0.06/+0.03/+0.19/+0.06/+0.03 and the doc says its gain 'came from one fold (fold 3)'; holdout top-2 ridge -0.293 vs setup_quality -0.126 (random -0.118); CI90 [-0.325, -0.016]; 116 sessions; acceptance failed. All confirmed.
- docs/setup-baserates-short-2026-09-23.md:30: 11,480 labelled setups in the 2017-2021 window. Confirmed.
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' lines 70 and 77: A0_live excess vs SPY is -0.110R (primary window) and -0.103R (robustness window), on long native setups only. This supports the '-0.11R' claim, but the finding does not cite this doc.
- docs/card-evidence.md:381-384 'Repeated setups': the caveat is qualitative only and contains no 87/41 figure. The figure appears only in the review's own scratchpad maps/live.md:33,52 (live DB query Q6).
- docs/apriori-pead-2026-09-26.md:7,76 and docs/alpha-pooled-mining-research-2026-09-28.md:67: PEAD has 4,726 trades with a long leg of +0.149R. The repo's research doc already recommends meta-labelling only on a pooled event set such as PEAD.
- docs/alpha-roadmap.md:494: L3 is the planned card-selector seam; docs/alpha-expansion-survey-2026-09-23.md:86-88 names meta-labelling as the follow-on to shadow ranking. No meta-label, CPCV, uniqueness-weight, PBO or isotonic code exists in agentic_trader (grep).
- Hudson & Thames PDF (Singh & Joubert, 'Does Meta-Labeling Add to Signal Efficacy?'), fetched and extracted: two strategies on S&P 500 E-mini. Bollinger mean-reversion out-of-sample precision went from 0.17 to 0.20 (accuracy 17% to 63%); SMA trend-following went from 0.48 to 0.54. Confirmed.
- skfolio CombinatorialPurgedCV page: exists, with n_folds, n_test_folds, purged_size and embargo_size. Confirmed.

**Bad references**

- docs/card-evidence.md 'Limits / Repeated setups': cited for '87 rows cover 41 setups', but the doc has only the qualitative caveat (lines 381-384) and no 87/41 numbers. The figure comes from a live DB query in the review's own scratchpad (maps/live.md:33,52, Q6).
- The '-0.11R native population excess' claim has no citation in the evidence list. Its actual source is docs/bracket-counterfactual-2026-10-18.md 'Descriptives' (line 70, long setups only).

**Notes**

The core of the finding holds. The cited code, the protocol parameters and the holdout numbers all check out, and nothing in the codebase already implements the recommended meta-label machinery: no CPCV, no uniqueness weights, no PBO, no isotonic calibration. The repo's own docs/alpha-pooled-mining-research-2026-09-28.md:67 already makes the 'precision filter, not alpha, and only on a pooled event set like PEAD' argument, so the recommendation partly restates existing internal guidance.

Minor inaccuracies:
- 'The target was raw R_cost' holds for ridge and HGB; logistic used the binary target-hit label. The selection metric was raw R_cost.
- Evidence line study.py:402 points at the HGB constructor; the r_cost target is on line 401.
- The '-0.11R' figure is for long setups (8,768) in the bracket counterfactual, not the full native population including shorts.
- 'Ridge's gain came from a single fold' paraphrases the doc. The median-based selection metric itself (+0.03) does not depend on fold 3 alone, but fold 3 drove the apparent gain.

References:
- AFML ch. 3, 4 and 7 map correctly to triple-barrier/meta-labelling, sample-uniqueness weights and purged/embargoed CV.
- The recommended CPCV is AFML ch. 12, and PBO is Bailey et al. Neither is cited, which leaves the references incomplete, though not wrong.
- The H&T paper is a capstone-style report whose authors call the gains confirmed. 'Thin evidence' is the reviewer's own, reasonable, judgment.

The PDF text extract was saved to ~/agentic-trader-research/holistic-review-20261021/extras/c34-ht/ht.txt (precision figures at lines 581-603 and 670-689).

### Value review — P3, later (months)

**Reasoning**

The diagnosis is correct and worth keeping as a design guardrail, but it does not justify an M-sized project now.

What checks out:
- Selection takes the median fold's top-2 mean R. Ties break in SCORERS order (agentic_trader/research/setups/study.py:58-59, 607-614).
- Ridge's development lead came from fold 3 (+0.19). In the holdout it lost to setup_quality, CI90 [-0.325, -0.016] (docs/setup-outcomes-2026-09-23.md, 'Selection (H3)').
- Natives lose to SPY in every bracket arm, by -0.10 to -0.15R (docs/bracket-counterfactual-2026-10-18.md, A0_live excess -0.110R).
- label_bracket is already a target/stop/time triple barrier (agentic_trader/research/setups/labels.py:159-189).

Why it is not worth funding as specified:
1. The primary signal loses to the market everywhere. A withhold-only meta-label at best keeps a subset near zero excess, which still does not beat SPY. Most of the gain from withholding bad natives is already available at S effort through card_policy. That policy is currently off (config/config.yaml:454) and thresholds raw mean_r_cost (agentic_trader/execution/card_policy.py:44,54), with no excess term in card_stats.
2. The only features that passed Holm, vol_20 (rho +0.049) and sector_rel_mom_60 (+0.028), were tested against raw R_cost over a rising 2021-2026 tape (docs/setup-outcomes-2026-09-23.md, 'Hypotheses'). Higher-volatility names carry higher beta, so vol_20 may be beta, not alpha. If neither survives an excess target, a meta-label has nothing to learn.
3. The proposed feature set (D-1 cross-sectional ranks, industry momentum, composite score) overlaps the pooled-lane families. That lane has returned 64 null formula-cohort pairs. A meta-label over those features is more mining under another name, and its trials must be charged.
4. There is no clean confirmation window. 2021-2026 development and holdout have both been read. 2017-2021 was read for the short-suppression test, and 3,365 of its 11,480 setups are shorts that natives no longer trade (docs/setup-baserates-short-2026-09-23.md). Both replays use the current 159-name universe, which flatters longs. Confirmation would have to be prospective, at about 1-2 cards a day, which means many months.
5. The primary itself is about to change. Known bar-semantics artifacts (UTC 4h buckets over extended hours, a falling-RSI 'recovery' trigger, the 2026-11-01 DST shift) mean a native-v2 replay is due. A meta-label fitted to the current primary would be stale on arrival.
6. PEAD sizing: 4,726 events carry roughly 600-900 independent observations, giving a +0.055R edge over control (docs/alpha-pooled-mining-research-2026-09-28.md). That is too little to estimate conditional heterogeneity with an ML model. The first-order PEAD levers are budget and risk parity at the probe's renewal, not model-based sizing.

Fund only the cheap diagnostics now (see better_alternative). Reopen a frozen meta-label protocol only if they show heterogeneity in excess R across setups.

**Risks**

- **Burns trials on a dead primary.** It would be the fifth test of native setups, after H1, H3, the recurrence test and the bracket counterfactual. Feature overlap with the pooled lane makes it disguised mining, and trial charging would be ambiguous.
- **Beta mistaken for edge.** Even on an excess target, beta estimates drift. vol_20's signal can survive as residual market exposure.
- **No unread window.** Any 'holdout' carved from 2017-2026 has already been inspected. A pass would be weak evidence, and PBO across logistic, ridge and HGB with CPCV does not fix a reused window.
- **Small, dependent samples.**
  - Survivorship in the 159-name universe.
  - Dependent setups: sessions cap the effective sample (design effect about 5-8), and journaled rows repeat setups (87 rows for 41 setups).
  - Per-session rank IC over about 2 cards a session gives a very noisy selection metric.
- **Replay-to-live mismatch.** Replay setups differ from live ones: live acts on forming bars, bars carry extended-hours contamination, and dynamic names are absent from the replay. A filter tuned on replay can withhold live cards wrongly.
- **Misleading card display.** Showing a calibrated probability on cards invites the operator to read precision as edge.
- **PEAD sizing overfits.** With about 700 effective observations, sizing by meta-label could add variance to the one positive source.

**Prerequisites**

- **Excess-R evidence in card_policy.**
  - Add mean_excess_r to card_stats, using the market_r that outcomes.py already computes.
  - Key it by (strategy, timeframe, direction) and de-duplicate per setup-session.
  - Run card_policy in preview on that excess threshold.
- **Freeze the native primary.** Fix bar semantics and the RSI trigger, then run a predeclared native-v2 replay before the DST shift. Without this, any meta-label targets a moving primary.
- **A declared prospective confirmation window**, journaled before it is read, since every historical window has been inspected.
- **A point-in-time universe** (EDGAR Form 25/15 delistings, per docs/survivorship-spike-2026-10-20.md), or an explicit survivorship bound on any result.
- **The L3 card-selector seam** (alpha-roadmap.md 'Planned'), so that a withholding rule plugs in without editing run_scan.
- **Trial accounting** that charges the meta-label's model and feature choices against the same pooled ledger.

**Better alternative**

Three cheap steps, total effort S:

1. **Re-score the existing artifacts.** Rerun the H1 and H2a-h rank tests on the 14,017 setups already labelled in the setup-outcome replay, against excess_r (r_cost minus SPY R over the same window) instead of raw R_cost. This is descriptive only, with no new protocol and no new data. If vol_20 and sector_rel_mom_60 lose significance on excess, close meta-labelling of natives permanently.
2. **Ship card_policy on excess R.** Use the deduped excess-R threshold in preview, then enforce. It withholds whole (strategy, timeframe, direction) keys with negative excess, which captures most of the achievable benefit with no model. If a feature does survive step 1, test it as a single frozen two-feature rank filter inside card_policy preview, A/B against setup_quality and random, rather than as a logistic/ridge/HGB model search.
3. **Spend the native budget on PEAD.** At the probe's 2026-10-28 renewal, move budget toward PEAD (risk parity with natives, and both scan slots). For PEAD sizing, predeclare one conditioning split (for example reaction-z or surprise terciles) as a frozen second look instead of an ML sizer.

---

<a id="c35"></a>

## C35 — Use LLMs as label-blind feature curators, not as formula search engines; do not build an Alpha-GPT, AlphaAgent or RD-Agent-style mining loop on this desk's data

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | next (weeks) | alpha_mining | stop_doing | medium | M | partially_confirmed |

_lenses: ml, literature_

### Claim (as consolidated)

No LLM miner exists today. Genetic search is parked and the ETF32 miner is retired.

Under the pooled ledger, every LLM-proposed formula scored on labels is a charged trial, so an LLM loop inherits the same problem as GP.

Published evidence for US equities is weak:
- Alpha-GPT reports a competition ranking.
- AlphaAgent's out-of-sample IC is 0.0056 on the S&P 500 against 0.0212 on CSI 500, the best of 20 trials. The desk maps that to about 0.02R, far below its MDE.
- RD-Agent(Q)'s main test is on CSI 300.

LLM backtests also inherit look-ahead from the model's training data.

ScoreBook is already label-blind (scoring.py:99-110).

### Evidence

- docs/alpha-pooled-checks-2026-10-02.md; docs/alpha-pooled-checks-v3-2026-10-05.md (genetic parked); scripts/launchd.sh:21
- docs/alpha-pooled-mining-research-2026-09-28.md:51 (AlphaAgent IC mapped to ~0.02R)
- agentic_trader/research/pooled/scoring.py:99-110; research/pooled/formula.py:238-246 (jaccard_codes)
- Live map Q7 (LLM gate 14/14 approvals)

### Recommendation

Limit LLM use to two jobs:
(1) Drafting library entries and fixed-set hypotheses in the DSL from abstracts and OSAP documentation. Each draft carries a literature sign, a citation and a fidelity table. The LLM never sees labels or scores. A human reviews each draft and freezes it as a library version, checking originality via canonical_expression and pick-Jaccard.
(2) The prospective news feature (C32).

Never feed an LLM scored campaign results.

Revisit search only if the lane's MDE falls below about 0.03R and the agent passes a search-power test in the style of check B.

### North-star impact

Keeps the trial budget, the confirmation window and operator attention on work that can reach cards, and gives the library breadth cheaply.

### References

- Wang et al., Alpha-GPT (arXiv 2308.00016) — Evidence is a self-reported competition ranking (https://arxiv.org/abs/2308.00016)
- AlphaAgent (arXiv 2502.16789) — S&P 500 out-of-sample IC 0.0056, best of 20 trials (https://arxiv.org/abs/2502.16789)
- Li et al., R&D-Agent-Quant (arXiv 2505.15155) — Main evidence on CSI 300 (https://arxiv.org/html/2505.15155v2)
- Glasserman & Lin (2023), arXiv 2309.17322 — Training-data look-ahead in LLM backtests

### Fact check — partially_confirmed

**Corrected claim**

The core holds. No LLM-driven miner exists. A word-boundary grep for "llm" over agentic_trader/research hits only LLM-gate attribution in research/setups, and copilot_tools.py:172-217 exposes only catalog reads and promote/demote. The pooled genetic campaign is parked (docs/alpha-pooled-checks-v3-2026-10-05.md:6,97), and the ETF32 miner is retired (scripts/launchd.sh:21). The pooled ledger commits a charge before each formula is scored (ledger.py:3-6; campaign_run.py step 5), so any LLM-proposed formula scored on labels would be a charged trial. ScoreBook builds score panels from bars only (scoring.py:2-7, 103, 112-133).

The finding needs these corrections:

(1) This "stop doing" is already the desk's stated policy. docs/alpha-roadmap.md:455-456 says "full LLM-agent miners are deliberately not planned." The finding's new content is only the positive proposal: a label-blind LLM drafts fixed-set or library hypotheses for human review.

(2) jaccard_codes is at research/pooled/formula.py:218-223, not :238-246. A pick-Jaccard originality check against literature entries already runs in campaign_run.py:226-235 (already_tested, dedupe_jaccard). The proposed originality check reuses existing machinery; it is not new work.

(3) AlphaAgent: the paper reports 20 independent trials and says "In each trial, we insert the optimal factors into their respective alpha zoos, yielding the optimal combination as the final result." It does not explicitly say "best of 20." Reported S&P 500 results for 2021-2024 are IC 0.0056, ICIR 0.055, IR 1.05. CSI 500 is IC 0.0212. The desk's mapping of about 0.02R (docs/alpha-pooled-mining-research-2026-09-28.md:51) is far below the lane MDE of about 0.08-0.09R (same doc :94).

(4) RD-Agent(Q): the main test is on CSI 300 (2017 to 2020-08), but main-text Table 2 also reports a US NASDAQ 100 test (2024 to June 2025). There IC is 0.016-0.017 and IR 1.33-1.77, single values with no variance over about 1.5 years, so the US evidence is short and thin rather than absent.

(5) Alpha-GPT: the abstract gives no metric. The full text cites competition results: WorldQuant IQC 2024 "top 10 worldwide and top 3 regionally," and roughly the top 5-10% in a JoinQuant HFT competition. It also gives an unspecified-market IC table (0.58% to 2.23%) and GPT-4-judged quality scores. None is a US-equity out-of-sample backtest with uncertainty.

(6) Glasserman & Lin found that, in-sample, a "distraction effect" outweighs look-ahead bias, and that out-of-sample look-ahead is less of a concern. They support "LLM backtests can be biased by training-data knowledge," not a measured large look-ahead.

**Checked**

- agentic_trader/research/pooled/scoring.py:2-7 (module docstring: label-blind, causal; vet_expression runs before anything is charged), :99-110 (_scored_formula panel closure, comment at :103 'Label-blind'), :112-133 (ScoreBook.__init__ takes only adjusted bars, trading days, sessions and symbols; it takes no labels)
- agentic_trader/research/pooled/formula.py:218-223 (jaccard_codes actually lives here); :225-246 is prepare_hourly/_decision_instants, not jaccard_codes
- agentic_trader/research/pooled/campaign_run.py:1-45 (charges each formula before it is scored; literature overlap rule), :226-235 (already_tested: pick-Jaccard dedupe against literature entries)
- agentic_trader/research/pooled/ledger.py:1-6, :82 (a charge is committed before its formula is evaluated)
- scripts/launchd.sh:21 (MINER_RETIRED_NOTICE, ETF32 miner retired 2026-10-14)
- docs/alpha-pooled-checks-2026-10-02.md:3,73 (check B failed 1/10); docs/alpha-pooled-checks-v3-2026-10-05.md:6,97 (genetic campaign parked)
- docs/alpha-pooled-mining-research-2026-09-28.md:51 (AlphaAgent S&P 500 IC 0.0056 vs CSI 500 0.0212; mapped to about 0.02R), :94 (MDE 0.080R at t=3; confirmation 0.092R)
- docs/alpha-roadmap.md:455-456 ('Foundation models for return direction and full LLM-agent miners are deliberately not planned.')
- grep -w llm over agentic_trader/research: hits only setups/outcomes.py and execution_evidence.py (LLM-gate attribution); no LLM call in research/pooled or research/alpha
- agentic_trader/agent/copilot_tools.py:172-217 (chat can read the alpha catalog and promote/demote; it cannot propose or mine formulas)
- scratchpad holistic-review/maps/live.md:55,77,126 (Q7: 14/14 LLM approvals, 0 llm_vetoed)
- arXiv 2502.16789v2 Table 2 (AlphaAgent)
- arXiv 2308.00016 abstract and HTML (Alpha-GPT)
- arXiv 2505.15155v2 Section 3 and Table 2 (RD-Agent(Q))
- arXiv 2309.17322 abstract (Glasserman & Lin)

**Bad references**

- agentic_trader/research/pooled/formula.py:238-246 cited for jaccard_codes. Those lines are prepare_hourly/_decision_instants; jaccard_codes is at formula.py:218-223.
- AlphaAgent (arXiv 2502.16789) 'best of 20 trials': the paper says 20 independent trials with the optimal factors combined into the alpha zoo. It does not state that the reported S&P 500 IC is the best of 20, so this detail overstates the source.
- R&D-Agent-Quant (arXiv 2505.15155) 'Main evidence on CSI 300': correct as far as it goes, but it omits that main-text Table 2 includes a US NASDAQ 100 test (IC about 0.017, 2024 to June 2025). That weakens the implied absence of US evidence.

**Notes**

I could not refute the substance. No code path lets an LLM propose or score formulas. Genetic search is gated and parked, and campaigns charge before scoring, so the trial-budget argument follows from the code.

The main misdescription is framing. The finding presents "do not build an LLM miner" as a stop-doing item, but the roadmap already excludes full LLM-agent miners (docs/alpha-roadmap.md:455-456). The finding also does not mention that its proposed originality check (canonical_expression plus pick-Jaccard against literature) already exists in campaign_run.already_tested and research/alpha/search.py:55.

The "Live map Q7" evidence exists in the review's own scratchpad map (maps/live.md:55,126): 14/14 approvals and 0 llm_vetoed events. It is internal review evidence, not a repo doc. It bears on the LLM gate's selectivity, not on mining.

The recommendation's "about 0.03R" MDE threshold for revisiting search is the reviewer's own number; no repo doc sources it. The docs give lane MDEs of 0.080R at t=3 and 0.092R for confirmation.

### Value review — P3, next (weeks)

**Reasoning**

Most of this finding is already policy, so it adds little new value for the north star. The "stop doing" half is correct but restates an existing decision. docs/alpha-roadmap.md:455-456 already says "Foundation models for return direction and full LLM-agent miners are deliberately not planned". docs/alpha-expansion-survey-2026-09-23.md:105-106 says the same ("Not now: ... a full LLM-agent miner"). Nothing in agentic_trader/research calls an LLM.

There is one real gap. docs/alpha-roadmap.md:864-866 (item 5) still invites us to "evaluate bounded proposal generation" with RD-Agent(Q) behind the DSL and the trial ledger. It does not say that the proposer must never see labels or scores. Closing that loophole is worth one paragraph.

The quantitative case for that paragraph is sound and grounded in our own docs. docs/alpha-pooled-mining-research-2026-09-28.md:51 maps AlphaAgent's S&P 500 out-of-sample IC of 0.0056 to about 0.02R. The same doc (line 94) puts the lane's minimum detectable edge at 0.080R at t = 3, and at 0.092R over the confirmation window. A search engine that sees scores also makes every proposal a charged trial, which raises the bar for every survivor.

The "build" half, an LLM that drafts DSL library entries from abstracts and OSAP, is low leverage now. Our bottleneck is not drafting. Each fixed set is eight hand-written literature formulas: v4, the intraday set, the events set, and the short-volume and insider sets in config/research/pooled/. The limits are statistical power and orthogonal information. Two facts weaken the curator further:
- OSAP is mostly accounting and fundamentals signals. We have no fundamentals data, and the DSL has no cross-sectional operators (research/alpha/operators.py:109-167, per maps/pooled.md §2). Most drafts would therefore be price/volume proxies that sit next to formulas already tested null.
- The human review step spends operator attention, which is our scarcest resource.

So: make the roadmap edit, with no new code. Bundle it into the next workstream PR, since the operator does not want docs-only PRs. Defer or skip the curator until a new data source opens a hypothesis space that hand drafting cannot cover.

**Risks**

1. Process overhead: we would formalise a curator workflow (fidelity tables, a human freeze step, pick-Jaccard originality checks) that produces formulas close to ones already tested null. That spends operator review time with no change in finalist odds.
2. Literature look-ahead does not go away. Even when label-blind, an LLM trained after 2021 knows which anomalies survived our discovery window (2016-08 to 2021-12), and it brings publication bias with it (McLean-Pontiff decay). It is the same contamination that hand-picked literature sets carry, only less visible.
3. Fidelity drift: drafts will quietly replace a paper's fundamentals with price proxies. A fidelity table helps only if someone reads it.
4. The "revisit when MDE < 0.03R" trigger is probably unreachable on this lane (the doc puts the realistic reach at 0.08-0.10R). Read that way, it is a permanent ban. That is fine, but it should say so honestly.
5. A blanket "stop" could be misread to cover legitimate label-blind LLM uses, such as news context (C32) or the existing card commentary.

**Prerequisites**

None for the guardrail.

For any curator build:
- a new data source with a hypothesis space we have not tested;
- a written spec saying the LLM sees only abstracts and the DSL grammar, never journal, cube, ScoreBook or campaign artifacts;
- the existing canonical_expression and jaccard_codes dedup (research/pooled/formula.py) applied to drafts before freezing;
- operator approval, as for every fixed-set protocol.

**Better alternative**

Make a one-paragraph change to docs/alpha-roadmap.md item 5 (lines 860-866), bundled into the next workstream PR. It should say that any LLM proposal path must be label-blind, must never receive campaign results, and runs only under the existing fixed-set checks A and C. That captures the whole "stop doing" value at near-zero cost.

For library breadth, encode new hypotheses from a new data source by hand, eight formulas per protocol as today, rather than building an LLM curator. The rate-limiting step is data and power, not authoring.

If we want to spend effort on LLMs, the live gate is the higher-leverage target. maps/live.md item 5 (Q7, Q16) records 14 of 14 approvals, 15-26 s per call, and two material bracket edits that card_stats does not measure. Re-scoping or measuring that gate affects every card sent today, which a research-side curator would not.

---

<a id="c39"></a>

## C39 — Replace fixed t≥3 gates and single-shot windows with power-aware thresholds, an alpha-spending plan and anytime-valid prospective tests

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | next (weeks) | research_methodology | improvement | medium | M | partially_confirmed |

_lenses: literature_

### Claim (as consolidated)

A predeclared fixed set of 8 literature priors currently faces all of these:
- t≥3 with 3 of 4 positive blocks;
- a 0.5x selection haircut;
- Holm;
- a leg ci90 above 0, which depends on market drift in 2024–26.
(campaign.py:732-739, 789-794, 833-842)

Result docs give no per-formula MDE or equivalence bound.

The first freeze consumes the whole lane-wide window. Null campaigns reached confirmation in 1 of 40 fixed-set replicates and 4–5 of 40 genetic replicates.

The prospective lanes (PEAD probe, daily panel, vol_20 read) have no stopping rule.

What the literature says:
- Harvey-Liu-Zhu's t>3 is calibrated for searched factors.
- Harvey-Liu 2020 show that flat hurdles lack power, and calibrate cutoffs by double bootstrap to a target FDR and miss ratio.
- A. Chen bounds at least 75% (tightest: at least 91%) of published findings as true. For literature-signed formulas the open question is magnitude and decay, not sign.

### Evidence

- agentic_trader/research/pooled/campaign.py:732-739, 789-794, 833-842
- agentic_trader/research/alpha/promotion.py:351-366, 370, 388
- docs/alpha-pooled-mining.md:615-621
- docs/alpha-pooled-intraday-v1-2026-10-14.md (null reached confirmation 1/40); docs/alpha-pooled-checks-v3-2026-10-05.md (genetic 4–5/40)
- docs/alpha-power-diagnosis-2026-09-18.md

### Recommendation

1. Every result table reports, per formula, the edge ci90, the MDE (from planting δ on that formula's picks in check C) and an equivalence statement.

2. Protocol v5, prospective only: set discovery cutoffs by Harvey-Liu double bootstrap on check C panels, at a declared FDR (for example 10%) and miss ratio.

3. Scope DSR to selection only. Test frozen finalists' holdouts with a one-sided block bootstrap (C29).

4. Write an O'Brien-Fleming / Lan-DeMets alpha-spending plan for the lane-wide window across the expected campaigns.

5. Open a prospective confirmation interval from 2026-08, scored nightly with confidence sequences. Use the same machinery for the PEAD renewal and for the vol_20 / sector_rel_mom_60 read.

### North-star impact

Turns 'no finalist' into a bounded statement and lets small real effects accumulate prospectively without burning historical data.

### References

- Harvey & Liu (2020), False (and Missed) Discoveries in Financial Economics, JF 75(5) — Double-bootstrap cutoffs balancing FDR against misses (https://arxiv.org/abs/2006.04269v1)
- Chen, A. Y., Most claimed statistical findings in cross-sectional return predictability are likely true (arXiv 2206.15365) — Literature priors deserve less than maximal scepticism about sign (https://arxiv.org/pdf/2206.15365)
- Lan & DeMets (1983), Discrete sequential boundaries for clinical trials, Biometrika 70 — Alpha-spending across campaigns (https://doi.org/10.2307/2336502)
- Howard, Ramdas, McAuliffe & Sekhon (2021), Annals of Statistics 49(2) — Anytime-valid intervals for nightly scoring

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding is correct. Several details need correcting.

**Gates.** The pooled fixed sets face these gates:
- Discovery: t >= 3, 3 of 4 blocks positive, leg mean > 0 as a point estimate, and >= 400 sessions (campaign.py:732-739).
- Selection: edge > 0 and >= 0.5x the discovery edge (campaign.py:789-794).
- Confirmation: Holm at one-sided alpha 0.05, plus a leg ci90 lower bound > 0, a 1%-trimmed leg mean > 0, a last-12-month edge > 0, and >= 300 sessions (campaign.py:832-842).

The leg ci90 condition applies only at confirmation. Because the leg is a long-bracket mean, it carries the window's base rate (market drift).

**Window.** The first freeze consumes the lane-wide 2024-01-02 to 2026-07-31 window (campaign.py:878-883; docs/alpha-pooled-mining.md:616-622). No alpha-spending plan exists. The research memo names alpha-spending as a reasonable idea but it was never adopted (docs/alpha-pooled-mining-research-2026-09-28.md:61). No confidence sequences exist anywhere.

**Null replicates reaching confirmation.** The counts are 4/40 for genetic v3 (alpha-pooled-checks-v3-2026-10-05.md:40) and 5/40 for genetic v2 (alpha-pooled-checks-2026-10-02.md:64). Among the fixed sets, intraday v1 had 1/40; v4, events and short had 0/40.

**Power and intervals.** The claim that "no finalist" is unbounded overstates the gap:
- Every result doc reports check A's end-to-end power curve: detection 0.40 at 0.05R, 0.84 at 0.08R, 0.95 at 0.10R.
- The research memo states a discovery MDE of 0.080R at t = 3 (alpha-pooled-mining-research-2026-09-28.md:94).
- So a lane-level bound exists. What is missing is a per-formula MDE or equivalence statement.
- Per-formula edge ci90 is already computed and stored in every discovery row (stats.py:127-147). Result docs tabulate only mean and t, except the ci90 given for shorts_retreat in the short-v1 doc.

**Prospective lanes.** These do not lack stopping rules entirely:
- The PEAD probe has a -4R cumulative kill and a 30-day renewable term (probe.py:40, 115; probe_state.py:57-63; config.py:831). It lacks a statistically calibrated efficacy or futility boundary.
- The vol_20 / sector_rel_mom_60 read has a fixed Oct 21 - Nov 4 window but no predeclared decision rule.
- The daily panel has no decision rule.

**Recommendation 3.** The per-symbol promotion holdout already gates on a block-bootstrap mean interval with lower bound > 0 (promotion.py:249-252, 391), alongside a family-deflated holdout DSR (promotion.py:370, 388). The real change would be dropping the holdout DSR, not adding a bootstrap. The pooled lane uses no DSR.

**Checked**

- agentic_trader/research/pooled/campaign.py:732-739 (discovery passes: t >= min_t, positive blocks >= min_positive_blocks, leg mean > 0 as a point estimate, sessions >= min_sessions)
- agentic_trader/research/pooled/campaign.py:789-794 (selection keeps a candidate if edge > 0, edge >= min_fraction_of_discovery x discovery edge, and sessions >= min_sessions)
- agentic_trader/research/pooled/campaign.py:832-842 (Holm, leg ci90 lower > 0, trimmed mean > 0, recent 12-month edge > 0, min sessions)
- agentic_trader/research/pooled/campaign.py:878-883 (on_frozen, then windows.confirmation(ledger, ...) consumes the window)
- config/research/pooled/campaign-v4.json, intraday-v1.json, events-v1.json (min_t 3.0, blocks 4, min_positive_blocks 3, min_fraction_of_discovery 0.5, alpha 0.05)
- agentic_trader/research/pooled/stats.py:127-147 (every paired_edge_test / leg_mean_test row already carries ci90, se, t, p_one_sided)
- agentic_trader/research/pooled/power.py:1-11 (check A plants delta on one AR(1) formula among 200 nulls and runs every stage)
- agentic_trader/research/alpha/promotion.py:351-366, 370, 388 (family-deflated DSR on validation and holdout)
- agentic_trader/research/alpha/promotion.py:249-252, 391 (the holdout already has a block_bootstrap_mean interval gate with lower bound > 0)
- agentic_trader/research/alpha/probe.py:40, 115; agentic_trader/storage/probe_state.py:57-63; agentic_trader/config.py:831 (PEAD probe: kill at -4R cumulative, 30-day renewable term)
- docs/alpha-pooled-mining.md:600-622 (gates; lane-wide single-use window, lines 616-622)
- docs/alpha-pooled-checks-v4-2026-10-07.md Check A/Check C/campaign table (detection 0.40 at 0.05R, 0.84 at 0.08R, 0.95 at 0.10R; C: 0/40 confirmed, 3 past discovery; per-formula mean and t, no ci90)
- docs/alpha-pooled-intraday-v1-2026-10-14.md Check C (1 replicate reached confirmation, none_confirmed)
- docs/alpha-pooled-checks-2026-10-02.md:64 (genetic v2: 5 none_confirmed) and docs/alpha-pooled-checks-v3-2026-10-05.md:40 (genetic v3: 4 reached confirmation)
- docs/alpha-pooled-events-v1-2026-10-15.md, docs/alpha-pooled-short-v1-2026-10-20.md:56-59, docs/alpha-pooled-cohort3-2026-10-16.md (fixed-set nulls: 0 reached confirmation; short-v1 doc gives ci90 for shorts_retreat at :101-104)
- docs/alpha-pooled-mining-research-2026-09-28.md:61, 94 (alpha-spending named as a reasonable idea but unused; MDE 0.080R at t=3 for discovery, 0.092R for confirmation)
- docs/alpha-power-diagnosis-2026-09-18.md Result and decision (planted effect 0/64 detected under the joint gates in the per-symbol funnel)
- docs/alpha-roadmap.md:291-302, 630 (vol_20/sector_rel_mom_60 read in the Oct 21 - Nov 4 window, no decision rule declared); docs/alpha-daily-panel.md (no decision or stopping rule)
- docs/apriori-alphas.md:166-200 (probe enrolment, term, -4R kill, 10-closed-trade comparability floor)
- Web: arXiv 2006.04269 (Harvey & Liu, False (and Missed) Discoveries; double bootstrap for FDR and miss-to-false-discovery ratio)
- Web: arXiv 2206.15365 (Chen; at least 75% true under the simplest bound, at least 91% under the refined one)
- Web: Lan & DeMets 1983, Biometrika 70(3):659-663, doi 10.2307/2336502
- Web: Howard, Ramdas, McAuliffe & Sekhon, Ann. Statist. 49(2):1055-1080 (2021), doi 10.1214/20-AOS1991

**Bad references**

_none_

**Notes**

**Code citations.** All the campaign.py and promotion.py citations exist and support the gate description. The doc line range alpha-pooled-mining.md:615-621 is off by one; the content is at 616-622. The "4–5 of 40 genetic" figure combines two docs, but only the v3 doc is cited; the 5 comes from alpha-pooled-checks-2026-10-02.md.

**What the finding gets wrong.**
1. It implies the results are unbounded. Check A's power curve already bounds them at lane level: an edge of 0.08R or more would have been found with about 84% probability.
2. It says the prospective lanes have no stopping rule. The PEAD probe has a -4R kill and a term expiry.
3. Recommendation 3 does not acknowledge the block bootstrap that already exists on the per-symbol holdout.

**Literature references.** All four are verified:
- Harvey & Liu, JF 75(5), arXiv 2006.04269: the double bootstrap sets hurdles to a target FDR and to a miss-to-false-discovery ratio. "Flat hurdles lack power" is a mild paraphrase; the abstract says current methods lack power to detect outperforming managers.
- Chen, arXiv 2206.15365: at least 75% of findings are true under the simplest bound and at least 91% under the tightest.
- Lan & DeMets, Biometrika 70(3), 1983, doi 10.2307/2336502.
- Howard, Ramdas, McAuliffe & Sekhon, Ann. Statist. 49(2), 2021, doi 10.1214/20-AOS1991.

**Judgement.** The recommendations themselves are new to the codebase and reasonable. The DSR-scoping point targets the per-symbol promotion path, not the pooled lane.

### Value review — P3, next (weeks)

**Reasoning**

The diagnosis is partly right, but this work does not address what is holding back the north star. The limit is effect size, not where the threshold sits.

1. **Looser gates would not have changed the outcome.**
   - Intraday v1: seven of eight discovery edges were negative (docs/alpha-pooled-intraday-v1-2026-10-14.md, "Campaign: no finalists").
   - Cohort v3: the best t across all three protocols was +1.85 (docs/alpha-pooled-cohort3-2026-10-16.md).
   - Only one formula cleared t≥3: `shorts_retreat`, at +0.020R and t +3.40. It then fell to +0.006R in selection (docs/alpha-pooled-short-v1-2026-10-20.md).
   - A Harvey-Liu FDR cutoff around t≈2–2.5 would still produce essentially nothing. Even a rescued `shorts_retreat` at about 0.006R per pick is economically irrelevant to cards that trail SPY by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md).

2. **The finding understates what already exists.**
   - Each formula's edge ci90 and one-sided p are already computed and stored (agentic_trader/research/pooled/stats.py:127-147). They are just not printed in the result tables.
   - Check A already publishes a detection-vs-planted-edge curve at protocol level: 0.40 at 0.05R, 0.84 at 0.08R, 0.99 at 0.15R (docs/alpha-pooled-intraday-v1-2026-10-14.md, "Check A").
   - So item 1 is largely a reporting change. That part is worth doing because it is nearly free. "No finalist" becomes "true edge below about +X R at 90%", which justifies closing lanes for good. It also makes plain that the lane has no power for post-publication-decayed effects of 0.02–0.05R.

3. **The heavier items are premature or poorly identified.**
   - Alpha-spending over the lane-wide window (item 4) has nothing to spend yet. The window has never been opened (docs/alpha-pooled-mining.md:615-621).
   - The Harvey-Liu double bootstrap (item 2) needs a large population of tested strategies to estimate the share of true nulls. Each protocol has m=8, and the 64 formula-cohort pairs are strongly correlated across cohorts.
   - The leg ci90>0 rule (campaign.py:833-842) should not be dropped. Cards are long-only bracket trades with no two-leg execution contract. A relative edge that loses money in a falling tape is not a good card.

4. **The prospective point is the most valid, but it is only half true.** Catalog probes already have a kill at −4R and term expiry with renewal (docs/apriori-alphas.md:166-183). What is missing is a predeclared success or renewal criterion. At about one drift card per session, a confidence sequence would take a very long time to reach a decision.

**Verdict:** worth doing as a cheap reporting change. It does not justify a methodology programme.

**Risks**

- **Forking paths:** Redesigning thresholds right after 64 null results looks like moving the goalposts. CLAUDE.md forbids relaxing gates or discounting trials retroactively. Any v5 cutoff tuned with knowledge of the near-misses (`shorts_retreat`, `quiet_momentum`) is contaminated.
- **Unstable calibration:** FDR-calibrated cutoffs estimated from 8-formula sets would be unstable and could raise the false-acceptance rate. The current rate is 0 of 40 in check C.
- **Time cost:** Building alpha-spending and confidence-sequence infrastructure takes researcher time away from finding larger effect sources, which is the real constraint.
- **Over-reading equivalence bounds:** Bounds computed on survivorship-biased cohorts (current membership) could be read as more conclusive than they are.
- **False comfort:** A confidence sequence on a sparse probe could end up as a never-ending "inconclusive" lane.

**Prerequisites**

- An unconsumed confirmation window and at least one frozen finalist before any spending plan matters.
- Enough accumulated prospective data after 2026-07-31 for the sequential tests to have any power.
- Operator approval of a new protocol version, declared before anyone looks at results.
- For the reporting item, nothing is needed beyond the artifacts that already exist: per-formula edge ci90 and the check A detection curves.

**Better alternative**

Take only the cheap part now:
- In every pooled result doc, add a per-formula column with the edge ci90, which is already in the campaign artifact JSON.
- Add one line giving the protocol's MDE at 80% detection, read from the existing check A curve.
- Add an explicit equivalence sentence, for example: "We can exclude true edges above +X R/pick."

Do this in the next results doc instead of building a framework. For each prospective lane (catalog probe renewal, daily panel), predeclare a fixed decision date and a single fixed-n test, or a small normal-mixture confidence sequence, when the lane opens or renews. Then put the saved effort into sources that could move per-card expectancy by 0.1R or more: new entry-side data and the card-level selection that the runners-up evidence shows is worse than random. Lowering thresholds on lanes whose best edges are about 0.02R will not do that.

---

<a id="c43"></a>

## C43 — Gap, scenario and earnings risk on held positions is unbounded, while the stop-risk budget is read as a loss limit

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | risk | gap | medium | M | partially_confirmed |

_lenses: portfolio_

### Claim (as consolidated)

The stop-risk budget is not a loss limit:
- Capacity bounds planned stop risk and snapshot exposure, 'not gap loss or portfolio CVaR' (capacity.py:3).
- Aggregate stop risk is 2% of capital, measured at original risk.
- The earnings blackout runs only at scan and tap time, so held names carry into their reports.
- Native positions have no holding deadline.

At a card beta of about 0.93, a full $40k equity class would lose:
- about $2.2k on a −6% index day;
- about $4.5k on a −12% day.
Both exceed the 2% stop-risk figure.

A single name is worse: a 10–20% earnings gap on a $7.5k card costs $750–1,500, which is 5–10x the median planned risk of $135.

### Evidence

- agentic_trader/execution/capacity.py:3; docs/entry-capacity.md:70
- config/config.yaml:3; agentic_trader/risk/rules.py:270-277
- docs/risk-policy.md:106; agentic_trader/execution/freshness.py:21; execution/lifetimes.py:210-212
- ~/agentic-trader-research/holistic-review-20261021/scripts/pm_frames_w.py, pm_frames.py

### Recommendation

1. Add a pure scenario_loss rule in risk/: Σ notional·β·shock + Σ notional·g_i, where g_i is the name's 250-session p99 overnight gap. Budget it at, for example, 4% of capital. Run it in preview, then enforce, and journal it per card.

2. Add a daily earnings check on held positions that emits an advisory card: hold, halve or exit through PositionCloseService on an operator tap. Test any exit rule against the earnings-announcement premium first (C25).

3. Add a 20-session holding deadline to native cards via SessionLifetimePolicy.

4. Later, historical-scenario CVaR that includes 2020-03 and 2022.

### North-star impact

Extends 'appropriate risk controls' to the losses that dominate a long-beta book: index gaps and earnings gaps.

### References

- Rockafellar & Uryasev (2000), Optimization of conditional value-at-risk, J. Risk 2(3) — CVaR as the tail-loss budget

### Fact check — partially_confirmed

**Corrected claim**

No rule budgets gap, scenario or earnings loss on held positions, and the system says so itself. Capacity bounds only planned stop risk and snapshot exposure (capacity.py:3). docs/entry-capacity.md:70-72 and config.yaml:3 explicitly say the 2% aggregate stop-risk budget is not a maximum-loss, volatility or CVaR limit. Nothing in the code treats it as a loss limit. The closest thing is the per-card Telegram 'Risk: -$X' and 'Capital Risk' lines (telegram_bot.py:338, 383, 503), which show stop-distance risk with no gap caveat.

How the stop-risk budget is measured:
- At scan time, held risk is the signal's recorded risk_dollars (book.py:71).
- At admission, held risk is the larger of the original reserved risk and the current mark-to-stop giveback (capacity.py:209). It is not 'original risk' alone.

The earnings blackout (7 days) runs only at scan and tap (docs/risk-policy.md:106; evaluator.py:554; copilot.py:3212-3258). Nothing re-checks held names before their reports. Native strategy positions carry no alpha_policy, so they have no holding deadline (lifetimes.py:210-212). Only formulaic and PEAD-drift cards get a lifetime.

Loss is not literally unbounded. It is capped by notional limits:
- $7.5k per trade (config.yaml:343);
- $40k for the equity class (config.py:393 default);
- $60k for the portfolio (config.yaml:4).

Since 2026-10-20, book_vol in enforce mode also applies a 1-sigma daily dollar-vol budget of 0.8% of capital, at card construction only (config.yaml:465-467; risk/book_vol.py). That is not a tail or gap bound, and it likely does not bind at normal volatility. The drawdown haircut and halt (3%/6%) act only after losses.

With a replayed-setup beta of about 0.92-0.93 (verified):
- a full $40k equity class loses about $2.2k on a -6% index day and about $4.5k on a -12% day, against a $2k stop budget;
- a 10-20% earnings gap on a $7.5k card costs $750-1,500, roughly 5.6-11x the median replayed base-tier risk of $135 (not 5-10x).

The gap is already on the roadmap as future work (docs/alpha-roadmap.md:41, :135; docs/architecture-review.md:370-382).

**Checked**

- agentic_trader/execution/capacity.py:1-7: the docstring says it 'bounds planned stop risk and snapshot exposure, not gap loss or portfolio CVaR'
- agentic_trader/execution/capacity.py:194-209: at admission, held risk_dollars = max(original, quantity x (mark - stop))
- docs/entry-capacity.md:66-72: the 2% budget 'is a planned stop-loss budget, not a guaranteed maximum loss, hard volatility constraint or portfolio CVaR limit'; it 'retains the larger of its original reserved risk and current mark-to-stop giveback'
- config/config.yaml:3 (max_stop_risk_pct 0.02, comment 'not a gap-loss guarantee'), :4 ($60k portfolio notional), :343 ($7.5k per-trade notional cap), :279 (earnings_blackout_days 7), :465-467 (book_sizing enforce, max_portfolio_daily_vol_pct 0.008)
- agentic_trader/config.py:387-393: max_equity_exposure defaults to 40000 and config.yaml does not override it
- agentic_trader/risk/rules.py:270-277: aggregate_stop_risk = book.planned_risk() + intent.risk_dollars <= capital x pct x drawdown_factor
- agentic_trader/risk/book.py:71, 95-96: the scan's book uses the signal row's risk_dollars
- docs/risk-policy.md:106: the earnings_blackout row is evaluator gate and tap gate only, with no admission step
- earnings lookups exist only at agentic_trader/agent/evaluator.py:554 and agentic_trader/agent/copilot.py:3212-3258 (tap); there is no held-position earnings check and no presentation/ module mentions earnings
- agentic_trader/execution/lifetimes.py:208-213: reconcile skips signals with no alpha_policy or no lifetime
- alpha_policy is attached only by screeners/formulaic.py:156 and screeners/earnings_drift.py:212, so native strategy cards get no lifetime; grep finds no other time exit or max-hold for native positions
- agentic_trader/risk/book_vol.py:1-30: the daily dollar-vol budget is applied at card construction only
- agentic_trader/notifier/telegram_bot.py:338, 383, 503: cards show the stop-distance figure as 'Risk: -$X' and 'Capital Risk', with no gap caveat
- docs/alpha-roadmap.md:41 and :135, docs/architecture-review.md:370-382: tail and scenario CVaR constraints are already listed as future work
- Ran scratchpad scripts/pm_frames.py and pm_frames_w.py read-only on ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz: winsorised beta 0.930 (unwinsorised 0.920), median base-tier risk $135, median notional $7,500
- WebSearch: Rockafellar and Uryasev (2000), J. Risk 2(3):21-41, exists and supports the CVaR use

**Bad references**

_none_

**Notes**

The core gap holds. There is no scenario or gap budget, no earnings check on held positions, and no holding deadline for native positions. The figures check out: 40k x 0.93 x 0.06 = $2,232, 40k x 0.93 x 0.12 = $4,464, beta 0.930 and median risk $135, all reproduced from the frozen bracket-counterfactual frame. These are replayed setups, not live cards.

Corrections:
1. The title says the stop-risk budget 'is read as a loss limit', but the code, docs and config all explicitly disclaim that. The finding's own first bullet even quotes the disclaimer.
2. 'Measured at original risk' is wrong for admission, which uses max(original, current mark-to-stop giveback) (capacity.py:209).
3. 'Unbounded' leaves out the notional caps and the book_vol daily-vol budget (enforce since 2026-10-20). That budget is a portfolio volatility constraint the finding never mentions, though it is 1-sigma and not tail-aware.
4. The ratio is 5.6-11x, not 5-10x.
5. The gap is already roadmapped (alpha-roadmap.md:135, 'Add scenario CVaR/hard volatility constraints against measured needs'), so this finding reiterates a known gap rather than discovering a new one.

The evidence path 'execution/lifetimes.py' lacks the agentic_trader/ prefix but resolves to agentic_trader/execution/lifetimes.py. The freshness.py:21 citation only shows that earnings is one of the tap-time reads, which is weak but not wrong. The Rockafellar & Uryasev reference is valid.

### Value review — P3, later (months)

**Reasoning**

The facts behind the finding are right, but the headline overstates it, and the package does almost nothing for the north star.

1. The gap is already disclosed. The code, config and docs all say the budget is not a loss limit:
   - "not gap loss or portfolio CVaR" (agentic_trader/execution/capacity.py:3);
   - "not a gap-loss guarantee" (config/config.yaml:3);
   - "not a guaranteed maximum loss ... or portfolio CVaR limit" (docs/entry-capacity.md "Aggregate exposure and protection").
   The weakness is real, but it is documented.

2. The index-shock leg mostly duplicates the existing caps. Index scenario loss is already bounded linearly by the notional caps:
   - $60k portfolio (config.yaml:4);
   - $40k equity class (agentic_trader/config.py:393);
   - $7.5k per trade (config.yaml:343).
   A 4% scenario budget at a −12% shock and β 0.93 allows about $35.8k of equity notional. So rule 1 is roughly a 10% tighter class cap with extra machinery.

   The per-name p99 overnight gap from 250 sessions rests on 2–3 tail observations, so it is noisy. Overnight gaps also already sit inside the daily returns that the book_vol sizer's covariance uses.

3. The earnings-through and holding-deadline exposure is small in frequency, from docs/bracket-counterfactual-2026-10-18.md, "Primary window" table:
   - A0_live holds 5.6 sessions on average and reaches the 20-session timeout in only 4.3% of setups.
   - The entry blackout covers 7 calendar days, about 5 sessions (config.yaml:279).
   - Many native cards are broad ETFs with no earnings.
   Only a small minority of positions can cross a report. On a paper account, a $750–1,500 single-name gap is evidence, not real loss.

4. The binding constraint is elsewhere. Cards trail SPY by 0.10–0.15R, and 64 formula-cohort pairs have produced no finalist. A new gate also lowers card supply, which is already about 1–2 a day. Item 4 (CVaR) contradicts the survey's own order of work: "CVaR/MPC ... should address a measured shortcoming" (docs/alpha-stack-survey-2026-09-18.md "Optimization priorities"). Its legacy CVaR also still has the S4 sign defect.

The best argument in the finding is item 3, as measurement parity rather than risk. The outcome labeller uses DEFAULT_MAX_HOLD_SESSIONS = 20 (agentic_trader/research/setups/outcomes.py:64). Live native cards have no deadline: TradeLifetimeService skips signals without alpha_policy (agentic_trader/execution/lifetimes.py:210-212). The few runners therefore diverge between labelled and broker P&L.

Verdict: do a cheap annotation-and-measurement slice now and defer the rules. Build a scenario or CVaR rule only once measured gap-through losses justify it.

**Risks**

- **Lower mean return.** Advisory exits before earnings give up the earnings-announcement premium (Savor & Wilson 2016; Frazzini & Lamont 2007; see C25). That would cut the mean of a card stream that already trails SPY.
- **Telegram noise.** Advisory hold/halve/exit cards compete with suggestion cards for the operator's attention, which live.md gap 9 already flags.
- **Fewer cards.** Another refusal rule (scenario_loss) lowers card supply when supply is already the bottleneck. A gap estimate from a 250-session p99 and a per-name β is noisy and unstable, so the rule could flap between scans.
- **Change to safety-critical code.** A holding deadline for native cards needs a new lifecycle path for signals without alpha_policy. That touches closure contract 2 and durable execution. It must not be confused with the semantics-v5 alpha contract, which forbids a holding deadline.
- **False assurance.** A scenario budget can be read as a loss limit, the same mistake this finding warns about.

**Prerequisites**

- **Measure before building rules.**
  - Realised loss versus planned R on stopped live trades: the gap-through frequency and size (open question in maps/risk.md).
  - How many held positions actually crossed an earnings date.
- **A per-name β and a book β-dollar view.** These come from maps/risk.md item 2 and maps/portfolio.md G6, and they feed any index-shock term.
- **The C25 test of the earnings premium** on the card stream, before any exit advice.
- **For CVaR:** split-adjusted joint history covering 2020-03 and 2022, and a fix for the S4 sign and units defect in backtest/monte_carlo.py.
- **For a native holding deadline:** fold it into the "live trailing-policy parity" item that is already listed as unfinished, so it is justified as parity with the labeller's 20-session timeout.

**Better alternative**

A days-long annotate-and-measure slice:

(a) A held-position earnings line on /positions and the daily digest, built from the existing earnings provider and modelled on the corporate-action line (agentic_trader/notifier/telegram_bot.py:159). Add a held_through_earnings flag and realised-versus-planned R to `cards outcomes`. There is no rule and no exit.

(b) Build the book β-dollar line (maps/risk.md item 2). β$ × shock is the index scenario at no extra cost, and it also separates beta from selection, which is the desk's real problem.

(c) If a hard index bound is wanted now, lower max_equity_exposure (agentic_trader/config.py:393). That is a config change equivalent to rule 1's index leg.

(d) Align the native-card 20-session timeout with outcomes.py:64 as part of the trailing-policy parity work.

Build a scenario_loss or CVaR rule only if (a) shows gap-through losses that matter.

---

<a id="c44"></a>

## C44 — The 'shadow optimiser' pins every weight and produces no allocation evidence; replace it with a free per-scan shadow allocator

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | sizing_portfolio | improvement | medium | M | partially_confirmed |

_lenses: portfolio_

### Claim (as consolidated)

_shadow_check passes ConvexAlphaPortfolioOptimizer lower bounds equal to upper bounds equal to the pinned weights (book_sizing.py:434-445). The solve can therefore only re-derive w'Σw.

The research allocator supports factor, group, turnover and gross constraints and an uncertainty penalty (optimizer.py:45-82). It runs only from `alpha portfolio SNAPSHOT.json`, and it has three further limits:
- build_shadow_portfolio refuses pending reservations (portfolio.py:105-106).
- It uses a separate risk model, a fixed 25% diagonal shrink (portfolio.py:167-170).
- Calibrated forecasts exist only for qualified registry versions, and there are none.

### Evidence

- agentic_trader/execution/book_sizing.py:434-445
- agentic_trader/research/alpha/optimizer.py:45-82; research/alpha/portfolio.py:105-106, 167-170
- agentic_trader/cli/commands/alpha.py:678-731; research/alpha/shadow.py:96-120

### Recommendation

Make it journal-only; shadow weights never reach orders.
- Stop the pinned solve.
- Each scan, solve over the book pinned at marks plus the approved candidates, each bounded to [0, max-tier notional].
- Inputs: μ = posterior alpha_R × risk$ (alpha only). Σ comes from the factor model, scaled to the roughly 5.6-session hold.
- Constraints: the existing caps as linear constraints, plus a beta band, sector caps and κ·se.
- Journal the suggested notional, the binding constraints and the duals.
- Add an 'allocator' arm to cards outcomes, compared with the actual choice.

Expect 'size ≈ 0 unless diversifying' today. Use one risk model for live and research, with skfolio and Riskfolio only as test oracles.

### North-star impact

Starts accruing prospective evidence on allocator-set sizes (roadmap stage B) without touching orders, and gives the selector a principled diversification tie-break.

### References

- skfolio and Riskfolio-Lib — Independent validators for risk-budgeting and CVaR formulations (https://skfolio.org)
- Grinold & Kahn (1999), Active Portfolio Management — Bridge from a posterior score to μ in return units

### Fact check — partially_confirmed

**Corrected claim**

The core finding is right.

**The book-sizing shadow optimiser cannot allocate.**
- `_shadow_check` pins every weight with lower bound = upper bound = (book notional + scaled card) / risk_capital (book_sizing.py:434-445). The solve can only reproduce w'Σw.
- This is the design intent, not a defect. It is documented as an arithmetic cross-check of the closed form that never reaches size or orders (book_sizing.py:1-7; config.py:270-273; docs/card-evidence.md:271).

**The full research allocator runs only from the CLI.** `build_shadow_portfolio` / `ConvexAlphaPortfolioOptimizer` with real bounds runs only from `copilot alpha portfolio SNAPSHOT --output DIR` (cli/commands/alpha.py:678-729). It refuses any nonzero pending reservation (portfolio.py:105-106).

**Corrections:**
1. **Shrinkage is a configurable default, not fixed.** The research covariance uses 25% diagonal shrinkage as the PortfolioPolicy default (portfolio.py:29, 167-170). A snapshot payload can override it (alpha.py:717). It is a different estimator from live book sizing's Ledoit-Wolf (research/setups/covariance.py:80-89), so "separate risk model" holds.
2. **Line range.** optimizer.py:45-82 is the constructor. Group constraints are in `optimize()` (lines 91-93 and 179-189), and duals and margins are already emitted (236-260).
3. **Calibrated forecasts are not limited to qualified versions.** shadow.py:93-119 takes the calibration from `qualification/{version_id}` without checking `passed`. storage/alpha.py:259-277 writes that record for every holdout-consumed finalist, pass or fail, and promotion.py:458 always includes the calibration. Calibrated shadow forecasts can therefore exist for failed finalists in the active or shadow registry. Even when they exist, they are only journaled as `combined_shadow_forecast` (shadow.py:130-153) and never feed an allocator. The docs record 0 qualifications (docs/alpha-roadmap.md:1000, 1020). Whether any calibrated record exists in the live DB could not be checked read-only.
4. **The recommended inputs do not exist yet.** The code has no posterior alpha_R and no factor risk model (grep finds neither). The proposal depends on building both first. The 5.6-session hold matches A0_live in docs/bracket-counterfactual-2026-10-18.md (Descriptives).

**Checked**

- agentic_trader/execution/book_sizing.py:395-420 (attach_shadow is gated by shadow_optimizer and runs only on rule-derived decisions)
- agentic_trader/execution/book_sizing.py:423-445 (pinned = book weights plus the scaled card divided by risk_capital; optimize(..., lower_bounds=pinned, upper_bounds=pinned) with the docstring 'every weight pinned'; alpha is a one-hot vector on the card)
- agentic_trader/execution/book_sizing.py:1-7, 476-489 (module doc calls it 'a journaled cross-check of the closed-form arithmetic', never read by the decision; the shadow block is absent from the scan_candidates_ranked journal block)
- agentic_trader/agent/copilot.py:1419, 1427, 1476 (attach_shadow call; the full model_dump including shadow goes to card provenance)
- agentic_trader/config.py:268-282 (BookSizingConfig docstring: the shadow optimiser cross-check never reaches size or orders; shadow_optimizer defaults to True)
- docs/card-evidence.md:271-272 (shadow optimiser documented as a cross-check of the closed form)
- agentic_trader/research/alpha/optimizer.py:45-82 (constructor: gross, factor, turnover, uncertainty_aversion), 84-94 and 160-189 (group_matrix/group_caps, factor, turnover and gross constraints), 236-260 (constraint margins and duals already emitted)
- agentic_trader/research/alpha/portfolio.py:19-33 (PortfolioPolicy defaults, covariance_shrinkage=0.25), 105-106 (nonzero reservations refused), 167-170 (fixed-intensity diagonal shrink)
- agentic_trader/research/setups/covariance.py:80-89 (live book sizing uses sklearn LedoitWolf, a different estimator)
- grep for build_shadow_portfolio/ConvexAlphaPortfolioOptimizer: callers are only cli/commands/alpha.py:711 and book_sizing.py:440
- agentic_trader/cli/commands/alpha.py:678-729 (alpha portfolio SNAPSHOT --output; policy can be overridden from the payload at line 717)
- agentic_trader/research/alpha/shadow.py:84-153 (calibration read from qualification/{version_id} without checking passed; combined forecasts are journaled as valid:false 'combined_shadow_forecast' and never passed to an allocator)
- agentic_trader/storage/alpha.py:259-277 (qualification/{version_id} is written for every holdout-consumed finalist, pass or fail), 310-312 (only active promotion checks decision.qualified)
- agentic_trader/research/alpha/promotion.py:448-458 (the qualification record carries calibration whatever 'passed' is)
- docs/alpha-roadmap.md:1000, 1020 (0 qualifications/promotions; registry 0 active/10 shadow), :1037 (convex allocator shadow-only)
- docs/forecast-to-fill-review.md:20-45 (research allocator shadow-only, separate from production)
- docs/bracket-counterfactual-2026-10-18.md Descriptives table (A0_live hold 5.6 sessions; supports the recommendation's hold length)
- grep for posterior/alpha_R/factor_model/FactorModel/allocator in agentic_trader: no matches
- https://skfolio.org (exists; risk budgeting plus CVaR; credits Riskfolio-Lib)
- Grinold & Kahn, Active Portfolio Management, 2nd ed. (McGraw-Hill; cited as 1999 or 2000; alpha = IC x sigma x score bridge)

**Bad references**

_none_

**Notes**

I found nothing that refutes the main points:
- The live "shadow optimiser" is a fully pinned cross-check.
- The free allocator has no per-scan caller.
- Reservations block it.
- Its covariance estimator differs from live (fixed 0.25 diagonal shrink vs Ledoit-Wolf).

The finding still overstates or misdescribes three things:
- **"Fixed" shrink:** 0.25 is an overridable policy default, not a hard-coded value.
- **The calibration gate:** the code reads calibration from any qualification record (pass or fail), not only qualified ones.
- **The recommendation's inputs:** it treats "posterior alpha_R" and "the factor model" as available. Neither exists in the code today.

The pinned solve is a deliberate design (it is documented as a D4 arithmetic cross-check), so the finding is a scope or roadmap proposal, not a bug.

The shadow block does reach card provenance through `model_dump` (copilot.py:1427, 1476) but is excluded from the `scan_candidates_ranked` `book_sizing` block (book_sizing.py:476-489).

Both references check out. skfolio exists and supports risk budgeting and CVaR, and it credits Riskfolio-Lib. Grinold & Kahn's second edition is variously dated 1999 or 2000; that is not a bad reference.

### Value review — P3, later (months)

**Reasoning**

The diagnosis is right. The prescription is premature, and its output would be almost all zeros today.

What the code does now:
- `_shadow_check` pins lower bound = upper bound = current weights (agentic_trader/execution/book_sizing.py:434-445). The CVXPY solve therefore only recomputes w'Σw a second way. It runs on every card because config/config.yaml:470 sets `shadow_optimizer: true`, it yields no allocation evidence, and nothing reads it (docstring at book_sizing.py:396-400).
- The real allocator, `build_shadow_portfolio`, has no daemon producer. It refuses any pending reservation (research/alpha/portfolio.py:105-106) and uses a separate fixed 25% diagonal shrink (portfolio.py:167-170). Live sizing uses Ledoit-Wolf instead.
- All of these facts check out.

Why I would not fund it now:
1. **With an alpha-only μ, the solve is degenerate.** The only live μ source is card_stats `mean_r_cost` per (strategy, direction) (research/setups/card_stats.py:43-62).
   - The measured base rate is negative: the withheld-card example is −0.39R over 30 (docs/card-evidence.md:126-133).
   - Every replayed bracket arm trails SPY by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md:6, 77, 100).
   - With μ ≤ 0 and bounds [0, max tier], the QP optimum is w = 0. The derivative at zero is μ_i − λ(Σw_book)_i, and that is ≤ 0 for a long equity card against a long equity book. So the finding's own "size ≈ 0 unless diversifying" overstates even that: a diversifying name still gets zero unless its covariance with the book is negative.
   - The journal would record "abstain" on every scan. card_stats and `card_policy` (docs/card-evidence.md "card_policy") already say that.
   - The proposed "allocator arm" in `cards outcomes` would just compare "trade" with "don't trade", which is the EV-versus-zero question already answered.
   - If raw μ were used instead (absolute R is positive in a rising tape), the allocator would size up levered SPY beta. That is the trap the bracket study exposed.
2. **There is little to allocate across.**
   - `max_cards_per_scan: 1` (config/config.yaml:443).
   - The book is near-empty: the preview week journaled six factors, all 1.00 "against a one-share book" (docs/card-evidence.md:232).
   - A joint solve over approved candidates adds almost nothing over a one-dimensional closed form when one card is sent per scan.
3. **The inputs would make the evidence untrustworthy** (gaps from the portfolio map):
   - R is not a consistent dollar unit, because the notional cap truncates risk (G3).
   - The covariance uses `adjustment=raw` returns, so splits pollute it (G7, book_sizing.py:222).
   - The book is valued at entry notional, not at marks (G9, book_sizing.py:126-141).
   - μ per (strategy, direction) is coarse, and repeated setups correlate it.
   - Months of prospective journal built on these would have to be discarded once they are fixed.
4. **Prospective accrual is the slowest route to the stage-B question.** At about 2 scans per session, a replay of the bracket study's 8,768 setups through live sizing, caps and book_vol (portfolio map item 9) answers "does allocator-set size beat the current choice in dollars versus SPY" in days.

Fund it once a (strategy, direction) key, or a probe, shows measured excess EV > 0 above a sample floor. Until then the allocator's correct and only output is zero.

Retiring the pinned solve is worth doing as housekeeping in the next book_sizing PR: move it to a unit-test oracle and delete it from the scan path, which saves a solver call per card. That is not north-star work.

**Risks**

- **Evidence that cannot discriminate.** It would accrue months of all-zero allocator rows under μ ≤ 0. Those rows cost scan latency and journal schema, and they invite a misleading "the allocator agrees" story.
- **Pressure to trade beta.** If someone switches μ to raw R to get non-zero sizes, the allocator sizes levered SPY beta (docs/bracket-counterfactual-2026-10-18.md:100).
- **Biased μ.** μ is taken from sent cards over a 90-day (strategy, direction) window that repeats correlate, which overstates n. The SE term then understates uncertainty, and κ·se is a free knob that can be tuned after the fact.
- **Time scaling.** Scaling Σ to about 5.6 sessions with sqrt-time assumes iid returns, which bracket path dependence breaks.
- **Contaminated inputs.** Building on the raw-split covariance (G7) and the entry-notional book (G9) contaminates the journal and forces it to be thrown away later.
- **A new failure surface in the scan path.** Solver timeouts and non-OPTIMAL statuses add to the scan path. Every failure mode must be evidence-only and never block a card.
- **Scope creep toward weights reaching orders.** The "shadow weights never reach orders" boundary in CLAUDE.md needs an explicit test guard.
- **A third risk model.** It would sit alongside Ledoit-Wolf (live) and the 25% diagonal shrink (research) unless G12 is resolved first.

**Prerequisites**

- **Positive measured edge.** At least one card key or probe needs a measured excess-over-SPY EV > 0 with n_mature above a predeclared floor. Without it, μ ≤ 0 and the output is identically zero.
- **R in dollars.** Make R a consistent dollar unit (map item 1, G2/G3), so that μ = EV_R × risk$ is linear in notional.
- **Split-safe covariance.** Use `adjustment=split`/`all`, or mask the action date using the captured corporate actions (G7). Unify the estimator in `research/setups/covariance.py` so live and research share one risk model (G12).
- **Mark the book.** Value it at broker marks or the latest close instead of `signals.notional_value` (G9).
- **Beta and sector in the book context (G6).** Without this, the "beta band, sector caps" constraints have no inputs.
- **A predeclared μ estimator.** Shrinkage toward 0, an SE that accounts for clustering of repeated setups, and a frozen κ, all declared before any outcome comparison.

**Better alternative**

Three cheaper steps get the same benefit sooner:

- **Answer the stage-B question by desk replay.** Run the 8,768 replayed bracket setups (docs/bracket-counterfactual-2026-10-18.md) through live `calculate_dynamic_sizing`, the caps, the 2-per-session budget and book_vol into a dollar equity curve against SPY (portfolio map item 9; the ledger identity in research/alpha/persistent_book.py can be reused). The answer comes in days, not months, and it can include an allocator arm offline.
- **Journal a closed form instead of running a QP in the scan path.** BookSizer already computes book vol before and after the card, so journal −Δvol and the marginal risk contribution per ranked candidate. With one card per scan and a pinned book, the free solve reduces to w* = clip((μ_i − λ(Σw_b)_i)/(λσ_i²), 0, max) anyway.
- **Use the EV threshold directly and retire the pinned solve.** The "principled EV threshold" the finding promises is simply card_policy in preview with μ_i > cost + κ·se, so no allocator is needed for it. Move the pinned `_shadow_check` to a unit-test oracle, with skfolio or Riskfolio as optional test oracles only.

---

<a id="c46"></a>

## C46 — Use the literature's negative short-horizon predictors as vetoes on native long cards, especially dynamic movers

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | signals | research_direction | medium | M | partially_confirmed |

_lenses: literature_

### Claim (as consolidated)

The native book is long-only. Up to 20 dynamic movers per scan have no replay base rate, and setup_features_v2 has no MAX, overnight-gap or news feature (features.py:44-53).

The literature documents long-side underperformance in liquid names:
- high MAX (Bali-Cakici-Whitelaw: decile spread above 1%/month);
- overnight gains that reverse intraday in high-attention names (Berkman et al.);
- extreme moves without news that reverse (Chan 2003);
- one-month reversal in low-turnover names (Medhat-Schmeling);
- momentum earned overnight rather than intraday (Lou-Polk-Skouras).

The desk's own pooled negatives agree (v2 / v3):
- quiet_momentum: −0.119R (t −3.34) / −0.044R;
- overnight_persistence: −0.035R / −0.016R;
- intraday_high_proximity: −0.013R / −0.045R (t −2.33).

### Evidence

- agentic_trader/screeners/dynamic_universe.py:315-339; agent/copilot.py:614-617
- agentic_trader/research/setups/features.py:44-53; research/setups/ranker.py:245-249
- docs/alpha-pooled-events-v1-2026-10-15.md:70; alpha-pooled-intraday-v1-2026-10-14.md:71, 77; alpha-pooled-cohort3-2026-10-16.md:83, 105, 111
- docs/bracket-counterfactual-2026-10-18.md:35-36 (2017–2021 robustness cache)

### Recommendation

1. From now on, journal setup_features_v3:
   - max_ret_21;
   - entry-day gap divided by ATR;
   - headline count over the prior 48 hours;
   - 20-day dollar-volume ratio;
   - a dynamic-name flag.

2. Freeze one veto set before reading any outcomes. Example: veto when MAX is in the top decile, or when the gap exceeds 1 ATR with no headline.

3. Test it as the paired change in alpha_R between kept and vetoed setups, on a declared window not yet read (for example the 2017-01→2021-04 cache), plus a dynamic-name proxy replay.

4. Ship it off → preview → enforce.

### North-star impact

The cheapest card-quality gain for a long-only desk is not buying what the literature says underperforms. This lifts the floor of the daily cards and targets the unvalidated dynamic slot.

### References

- Bali, Cakici & Whitelaw (2011), Maxing out, JFE 99 — High-MAX stocks underperform
- Berkman, Koch, Tuttle & Zhang (2012), Paying Attention, JFQA 47(4) — Overnight gains reverse in high-attention large stocks
- Chan (2003), Stock price reaction to news and no-news, JFE — No-news extreme moves reverse (https://doi.org/10.1016/S0304-405X(03)00146-6)
- Medhat & Schmeling (2022), Short-term Momentum, RFS 35(3); Lou, Polk & Skouras (2019), JFE 134(1) — Turnover conditioning and overnight/intraday decomposition

### Fact check — partially_confirmed

**Corrected claim**

The core gap is real and correctly cited.
- The native book is long-only (config/config.yaml:305,320).
- Up to 20 dynamic names per scan come from most_actives and movers (dynamic_universe.py:315-339, config.py:121), with at most one dynamic card per session (copilot.py:727,1263).
- No replay study covers dynamic names. Their cross-sectional setup features are NaN because the live cross-section excludes non-universe symbols (ranker.py:245-249).
- setup_features_v2 has no MAX, overnight-gap or news feature (features.py:42-59).
- No such veto exists in risk/ or screeners/.
- The pooled numbers quoted match the docs exactly.

Four details need correcting.

1. Two of the five proposed 'v3' journal fields already exist outside setup_features:
- The 48-hour headline count is journaled as news.n_items with window_hours=48 for every ranked native equity candidate (news_context.py:159-166, config.py:360, copilot.py:1889).
- The dynamic flag is journaled as dynamic/dynamic_source (copilot.py:629-632, 1486, 1893).
Only max_ret_21, gap/ATR and a dollar-volume ratio are new. dollar_volume_20 already exists as a level.

2. The example test window (2017-01 to 2021-04) is not unread:
- It was the robustness window of setup-recurrence (docs/setup-recurrence-2026-10-14.md:18-20) and the bracket counterfactual (docs/bracket-counterfactual-2026-10-18.md:35-36, 76).
- More importantly, it lies entirely inside the 2016-08-01 to 2021-12-31 pooled discovery window that produced the motivating negatives (docs/alpha-pooled-intraday-v1-2026-10-14.md:63).
- Under the desk's own rule (docs/alpha-pooled-events-v1-2026-10-15.md:80-84), that window is already used for this hypothesis.
- A clean test needs a window outside 2016-08 to 2021-12, or a prospective preview-mode read.

3. The literature framing 'long-side underperformance in liquid names' is partly wrong:
- Chan (2003) finds its no-news reversal mainly in smaller, illiquid stocks.
- Medhat & Schmeling (2022) find short-term momentum, not reversal, in high-turnover stocks, strongest among the largest and most liquid. That is the most-actives dynamic population, so it argues against a blanket one-month-reversal veto on dynamic names.
- Lou, Polk & Skouras is an overnight/intraday decomposition, not evidence of underperformance.

4. The desk's pooled 'agreement' is weak:
- The intraday doc calls all edges within noise (alpha-pooled-intraday-v1-2026-10-14.md:79-80).
- quiet_momentum's t of -3.34 is explicitly labelled a hypothesis only.
- intraday_high_proximity measures proximity to a 140-bar high, not the Bali MAX measure.

**Checked**

- config/config.yaml:305,320: allow_short: false on both active native strategies (trend_pullback, squeeze_breakout), so the native book is long-only. Confirmed.
- agentic_trader/config.py:107-126 and config/config.yaml:259-271: dynamic sources are most_actives (top 100 by trades) plus movers (top 50 gainers and losers), max_symbols 20. agentic_trader/screeners/dynamic_universe.py:315-339 fetches them, and :262-265 applies the cap. copilot.py:614-617 adds the 'dynamic' group, and copilot.py:727 and :1263 enforce a one-dynamic-card-per-session cap. Confirmed.
- agentic_trader/research/setups/features.py:42-59: setup_features_v2 has CROSS_SECTIONAL (mom_231_21, mom_60, rev_5, vol_20, dist_high_240, dollar_volume_20, resid_mom_60, sector_rel_mom_60), MARKET and SETUP. There is no MAX, gap or news feature. Confirmed.
- agentic_trader/research/setups/ranker.py:245-249: the live cross-section population is limited to universe.groups symbols, so dynamic names get no cross-sectional feature values. This supports the claim.
- agentic_trader/research/setups/card_stats.py:1-80 and docs/card-evidence.md:93-96: the measured record is keyed only by (strategy, direction) and pools dynamic and static journaled candidates. No dynamic-specific split exists. A grep of research/ and cli/commands finds no dynamic attribution in cards outcomes. The replay studies use the static 159-name universe (docs/setup-recurrence-2026-10-14.md:14). So 'no replay base rate' holds.
- agentic_trader/agent/copilot.py:1889,1893 and :1478,1486; agentic_trader/agent/news_context.py:53-66 and :159-166; agentic_trader/config.py:360; config/config.yaml:520: each ranked candidate's journal already records news.n_items over window_hours=48, plus dynamic/dynamic_source.
- grep of agentic_trader/risk and agentic_trader/screeners: no existing gap, extension or MAX veto. The gap is real.
- docs/alpha-pooled-events-v1-2026-10-15.md:70 (quiet_momentum -0.119R, t -3.34), :76-84 (only a hypothesis; 'the discovery window it would be read on is already used by this observation'). Numbers confirmed.
- docs/alpha-pooled-intraday-v1-2026-10-14.md:63 (discovery window 2016-08-01 to 2021-12-31), :71 (overnight_persistence -0.035), :77 (intraday_high_proximity -0.013), :79-80 ('all are within noise of zero'). Numbers confirmed.
- docs/alpha-pooled-cohort3-2026-10-16.md:83 (quiet_momentum -0.044), :105 (overnight_persistence -0.016), :111 (intraday_high_proximity -0.045, t -2.33), :117 ('near their 140-session high'). Numbers confirmed.
- docs/bracket-counterfactual-2026-10-18.md:35-36 and :76: the 2017-01-03 to 2021-04-30 robustness window was read with reported results. docs/setup-recurrence-2026-10-14.md:18-20 and :72 read the same window. config/config.yaml:305 cites a 'fresh 2017-2021 window' for the short base-rate test.
- docs/news-context.md:5 and :98-99: news is 'never a gate' and 'no number here is a gate until an operator-specified policy says so'. An off/preview/enforce veto is compatible with that contract.
- Web check of Bali, Cakici & Whitelaw (2011), JFE 99(2):427-446: spread over 1%/month, robust to controls. Confirmed.
- Web check of Berkman, Koch, Tuttle & Zhang (2012), JFQA 47(4):715-741: overnight gains reverse intraday, strongest in high-attention and hard-to-value stocks, sample of the 3,000 largest stocks. Confirmed.
- Web check of Chan (2003), JFE, DOI 10.1016/S0304-405X(03)00146-6: no-news extreme moves reverse, but the effects are mainly in smaller, more illiquid stocks.
- Web check of Medhat & Schmeling (2022), RFS 35(3):1480-1526: reversal in low-turnover stocks, short-term MOMENTUM in high-turnover stocks, strongest among the largest and most liquid.
- Web check of Lou, Polk & Skouras (2019), JFE 134(1):192-213: momentum profits accrue overnight. This is a return decomposition, not documented long-side underperformance.

**Bad references**

- Chan (2003), JFE, DOI 10.1016/S0304-405X(03)00146-6. The citation exists and supports 'no-news extreme moves reverse', but not the finding's framing of underperformance 'in liquid names': Chan's effects are mainly in smaller, illiquid stocks.
- Medhat & Schmeling (2022), RFS 35(3). The citation exists and correctly describes reversal in low-turnover names. But its headline result is short-term momentum in high-turnover, large and liquid stocks, which is the opposite of a reversal veto for most-actives dynamic names, so it only partly supports the use made of it.
- Lou, Polk & Skouras (2019), JFE 134(1). The citation exists and is accurate as a decomposition, but it is listed as evidence of 'long-side underperformance', which it does not document.

**Notes**

Every code and doc citation resolves and says what the finding claims. The pooled R and t values are exact. Bali, Cakici & Whitelaw and Berkman et al. are correctly cited.

Two caveats are not in the finding:
- A replay of 'gap > 1 ATR with no headline' needs historical news. The research-only `alpha news` acquisition exists for this.
- Gap and MAX could be derived after the fact from bars for candidates already journaled, the way `cards outcomes` derives the SPY tape and recurrence. Journaling 'from now on' is therefore not the only route.

Structurally, trend_pullback is already a dip-buying entry (config/config.yaml:303-316), so a MAX or gap veto would mostly bite squeeze_breakout and dynamic gainers. The finding does not acknowledge this, and it bears on expected power.

### Value review — P3, later (months)

**Reasoning**

The finding correctly identifies a real gap: dynamic movers have no measured base rate. The veto workstream it proposes to close that gap is not worth funding as written, for six reasons.

1. **Small upside.** The proposal vetoes the top decile of a feature among native setups. Even if the vetoed decile were 0.1R worse, the kept mean rises by about 0.01R. With at most 2 native cards per session, roughly 1 in 10 cards would be swapped for the next-ranked candidate. That barely touches the measured gap: natives trail SPY by −0.110R and every bracket arm trails it by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md, summary and "Descriptives").

2. **The desk's own native-setup evidence points the other way.** On native long setups:
   - `vol_20` is the strongest surviving feature, and its sign is positive: ρ +0.049, Holm p 0.004 (docs/setup-outcomes-2026-09-23.md:74). MAX is largely a realized-volatility proxy, so a top-decile MAX veto would preferentially remove the setups this result says do better in R.
   - `mom_60` is +0.019 and `rev_5` is −0.000 (same doc, :72-73). Neither supports momentum or reversal vetoes.

3. **The supporting "pooled negatives" are cherry-picked and replicate weakly.** They are the most negative of 64 formula-cohort tests, and they measure cohort-decile picks under the cube's bracket, not native trend-pullback or squeeze setups:
   - `quiet_momentum`: t −3.34 on v2, −1.69 on v3 (docs/alpha-pooled-events-v1-2026-10-15.md table; docs/alpha-pooled-cohort3-2026-10-16.md events table).
   - `overnight_persistence`: t −1.93 on v2, −1.18 on v3.
   - `intraday_high_proximity`: t −0.59 on v2, −2.33 on v3 (docs/alpha-pooled-intraday-v1-2026-10-14.md; docs/alpha-pooled-cohort3-2026-10-16.md intraday table).

   With 64 tests, a minimum t near −3 is roughly what noise produces.

4. **MAX conditioning has already been tried.**
   - `reversal-lowmax` failed (docs/apriori-alphas.md:82).
   - The v4 literature fixed set had 7 of 8 negative edges (docs/alpha-pooled-checks-v4-2026-10-07.md, "What this says").

   The MAX premium in the literature sits in small, illiquid stocks. This universe is mega-caps, ETFs and a liquid cohort (config/config.yaml:95-258).

5. **The proposed test window is not unread.** 2017-01→2021-04 has already served as the robustness window for:
   - the short base-rate study (docs/setup-baserates-short-2026-09-23.md:5,16);
   - setup recurrence (docs/setup-recurrence-2026-10-14.md:19-20, 72);
   - the bracket counterfactual, on these same native setups (docs/bracket-counterfactual-2026-10-18.md, "Windows").

   It is fresh for this hypothesis, but its outcomes have been inspected. Power is also marginal: about 800 vetoed setups, clustered by session, against an expected large-cap effect of about 0.05–0.1R.

6. **Two of the five proposed v3 features already exist prospectively.** Every ranked candidate is journaled with its dynamic tag and a 48-hour `news.n_items` count (agentic_trader/agent/copilot.py:1889, 1893; agentic_trader/agent/news_context.py:159-163). The other two price features, `max_ret_21` and gap/ATR, can be derived causally from daily bars at labelling time, the same way `cards outcomes` already derives regime and recurrence. No new feature version is needed to measure them.

**What is worth keeping is the dynamic-slot question.** Movers are extreme |%chg| names by construction (agentic_trader/screeners/dynamic_universe.py:325-339). Any MAX or gap veto would therefore act mostly as an on/off switch for dynamic gainers. That is better decided directly with a base-rate replay than through a literature veto set. The slot is capped at one dynamic card per session through the shared `dynamic` group (agentic_trader/agent/copilot.py:614-617).

**Risks**

- **Wrong sign.** A MAX/volatility veto could remove the high-`vol_20` native setups, the only feature that survived Holm with a positive sign, and lower card quality.
- **Repeated looks.** Deriving a veto set from the most negative of 64 pooled tests, then testing it on a window three studies have already read, is the kind of second look the desk's protocol is meant to prevent.
- **Fewer cards.** Vetoes cut card count, when the problem is a negative-excess source, not a thin tail. If dynamic gainers are mostly vetoed, the one-per-session dynamic card disappears without a measurement saying it should.
- **Cost to run.** A replay of headline-conditioned vetoes needs the Benzinga history back to 2017 plus another frozen protocol. That is M effort for an expected effect of about 0.01R per card, and it adds yet another operator-gated mode (off/preview/enforce) on top of `card_policy`, `book_sizing` and `corporate_action`.

**Prerequisites**

The veto test is only worth doing if all of the following hold:

1. The dynamic-name base rate is measured: a proxy replay of daily top-N by |return| and by dollar volume, plus a static/dynamic split in `cards outcomes` (the open WS3 follow-up, docs/alpha-roadmap.md:322-323).
2. `card_policy` is on excess R rather than raw `mean_r_cost` (agentic_trader/execution/card_policy.py:44,54), so that any veto's effect is measured net of SPY.
3. A genuinely unread, predeclared window is reserved. The 2026-03-02→2026-08-14 setup holdout is already declared for the bracket confirmation read and must not be spent here.
4. The prospective `vol_20`/`sector_rel_mom_60` read (2026-10-21 → 11-04) has completed, so the volatility sign is known before freezing a MAX veto.

**Better alternative**

Address the measured problem directly, in three cheaper and higher-leverage steps:

1. **Measure the dynamic slot, S–M effort.** Run the proxy replay of top-N |return| and top-N dollar-volume names through the unchanged `StrategyEngine`, and add a dynamic/static split to `cards outcomes`. If dynamic longs show negative excess, switch the dynamic slot off or route it through `card_policy`. That is one config decision, not a veto framework.
2. **Add descriptive columns to `cards outcomes`.** Derive `max_ret_21` and gap/ATR causally from bars there, next to the existing `news.n_items` and dynamic tag. They then accumulate as prospective, descriptive evidence at near-zero cost, with no `setup_features_v3` and no gate.
3. **Spend the remaining effort on what moves the −0.11R gap.** Put `card_policy` in preview on excess R, keyed by strategy, timeframe and direction, and reallocate budget toward the PEAD leg. PEAD beat its control by +0.055R (docs/apriori-pead-2026-09-26.md), and its probe renewal falls on 2026-10-28.

---

<a id="c47"></a>

## C47 — Re-test reversal as the literature states it: one-month 'quiet' reversal, residual and VIX-conditioned, on horizon-matched labels

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | alpha_mining | research_direction | medium | M | partially_confirmed |

_lenses: literature_

### Claim (as consolidated)

events-v1 froze quiet_momentum = ts_sum(returns, 20), taken outside earnings windows, and cited Jegadeesh-Titman for it (events-v1.json:186-188). But the one-month return is the short-term reversal signal; the desk's own momentum_12_1 skips that month for this reason.

The formula produced the lane's largest |t|, with the reversal sign: −0.119R (t −3.34) on v2 and −0.044R on v3. It was recorded only as an unregistered hypothesis. 5-session reversals on v3 were small and positive (+0.027R, +0.022R).

All short-horizon priors were labelled with the 20-session bracket, which hits its target about 8.6% of the time, although BracketSpec parameterises the hold (cube.py:57-71).

The literature also identifies the conditions under which reversal is strongest:
- Chan: no-news reversal.
- Da-Liu-Schaumburg: residual reversal earns about 4x the standard strategy, risk-adjusted.
- Nagel: reversal returns can be forecast from VIX.
- Medhat-Schmeling: reversal concentrates in low-turnover names.

### Evidence

- config/research/pooled/events-v1.json:186-188; campaign-v4.json:82-85
- docs/alpha-pooled-events-v1-2026-10-15.md:70, 76-82; docs/alpha-pooled-cohort3-2026-10-16.md:83-84, 91
- agentic_trader/research/pooled/cube.py:57-71
- docs/alpha-pooled-literature-2026-09-30.md 'Diagnostics'

### Recommendation

Request one declared second-look fixed set, reversal-v1, with at most 4 formulas:
- minus the 21-session return, outside earnings windows;
- minus the 5-session industry-residual return (needs C16);
- the first, conditioned on VIX above its one-year median;
- the first, within the lowest turnover tercile.

Label it on a new 5-session time-exit cube and on the 20-session cube, each counted as its own trial. Score on the 2022–23 selection window and prospectively, not on the discovery window that has already been read.

Move the first-hour and overnight formulas to a 1-session cube in the same way.

### North-star impact

Gives the best-supported pattern in the lane's own data a fair, horizon-matched test, with a potential long-only 'buy the quiet dip' card source as the outcome.

### References

- Jegadeesh (1990), Evidence of Predictable Behavior of Security Returns, JF 45(3) — One-month cross-sectional reversal
- Da, Liu & Schaumburg (2014), A Closer Look at the Short-Term Return Reversal, Management Science 60 — Residual reversal (https://ideas.repec.org/a/inm/ormnsc/v60y2014i3p658-674.html)
- Nagel (2012), Evaporating Liquidity, RFS 25(7) — VIX-predictable reversal returns

### Fact check — partially_confirmed

**Corrected claim**

events-v1 froze quiet_momentum = ts_sum(returns, 20), taken outside earnings windows, under a Jegadeesh-Titman 1993 momentum rationale (events-v1.json:186-188). But the one-month return is the classic short-term reversal horizon, which is why the desk's own momentum_12_1 skips that month (campaign-v4.json:82-85).

quiet_momentum produced the largest |t| among the events fixed sets, with the reversal sign: −0.119R, t −3.34 on cohort v2 (events-v1 doc:70) and −0.044R, t −1.69 on cohort v3 (cohort3 doc:83). It was recorded only as an unregistered hypothesis (events-v1 doc:79-83). It is not the largest |t| in the pooled lane: cohort3-short-v1 shorts_retreat reached t +3.40 (short-v1 doc:94).

5-session reversal is mixed:
- Small and positive on v3: +0.027R and +0.022R.
- Negative or flat on v2: reversal_5 −0.021R, t −0.86 (v4 checks doc:46); quiet_reversal +0.011R.

Every pooled fixed set and literature entry was labelled with the same 20-session 2xATR/3R bracket, although BracketSpec parameterises the hold (cube.py:57-71). Picks under that bracket hit target about 8.4-8.6% of the time on the cohort-v1 literature cube (literature doc:108).

Two prior results the finding omits:
- A literature-stated one-month reversal was already tested. reversal-lowmax-v1 (-roc(close, 21), low-MAX filter, Jegadeesh 1990) ran over 2016-2026 on cohort v1 and failed P2 with a paired edge of +0.008R, t 0.52 (literature doc:10-13, 71). The literature overlap rule (alpha-pooled-mining.md:722-742) denies probe eligibility to close relatives of a failed entry. The selection and confirmation windows have therefore already been read for a one-month reversal formula.
- The 2026-10-07 label diagnostic measured reversal_5 on plain 5-session forward returns: +16 bps, t 1.30, per-ATR edge −0.048 (v4 checks doc:81-117). It concluded that the bracket label does not hide the edge for these formulas.

The untested variants remain open: quiet, residual, VIX-conditioned and turnover-conditioned reversal on a horizon-matched label. They need new inputs: industry membership (C16), a VIX history and shares outstanding for turnover. None of these exist in the pooled DSL.

**Checked**

- config/research/pooled/events-v1.json:185-200: quiet_momentum = ts_sum(returns, 20) / (earnings_age > 20), with a Jegadeesh-Titman 1993 rationale. quiet_reversal = -ts_sum(returns, 5), citing Jegadeesh 1990 and Lehmann 1990. Confirmed.
- config/research/pooled/campaign-v4.json:82-85: momentum_12_1 = delay(close, 21)/delay(close, 252) - 1, so it skips the most recent month. Confirmed.
- docs/alpha-pooled-events-v1-2026-10-15.md:70 (quiet_momentum −0.119R, t −3.34) and :71 (quiet_reversal +0.011, t +0.37). Lines 76-83 say the sign-flipped formula is only a hypothesis needing a new frozen protocol. Confirmed.
- docs/alpha-pooled-cohort3-2026-10-16.md:83 (quiet_momentum −0.044R, t −1.69), :84 (quiet_reversal +0.027R), :91 (reversal_5 +0.022R). Confirmed.
- docs/alpha-pooled-short-v1-2026-10-20.md:94: shorts_retreat t +3.40 on cohort v3. This is larger than |−3.34|, which contradicts 'the lane's largest |t|' at the pooled-lane level.
- docs/alpha-pooled-checks-v4-2026-10-07.md:46: reversal_5 on cohort v2 is −0.021R (t −0.86). So 5-session reversal is mixed across cohorts.
- docs/alpha-pooled-checks-v4-2026-10-07.md:81-117: label diagnostic of 2026-10-07. reversal_5 measured on plain 5- and 20-session forward returns gives +16 bps at 5 sessions (t 1.30) and a per-ATR edge of −0.048. Conclusion there: 'the bracket label is not what removes an edge'.
- agentic_trader/research/pooled/cube.py:57-71: BracketSpec.max_hold_sessions is a parameter (Field ge=1). Confirmed.
- grep of config/research/pooled/*.json: every fixed set, campaign and literature entry uses a 20-session, 2xATR14 stop, 3R target bracket. Confirmed.
- docs/alpha-pooled-literature-2026-09-30.md:108 (Diagnostics): the 8.6% target share is for the high52-v1 picks on the cohort-v1 cube (8.4% for reversal-lowmax). It is not a cohort-v2/v3 base rate.
- docs/alpha-pooled-literature-2026-09-30.md:10-13, 29, 71, 121-131 and config/research/pooled/reversal-lowmax-v1.json: a frozen one-month reversal entry, -roc(close, 21) with a low-MAX filter citing Jegadeesh 1990. It was evaluated over 2016-08 to 2026-07 and failed P2 (edge +0.008R, t 0.52). Its per-year edges for 2022 and 2023 were read.
- docs/alpha-pooled-mining.md:580-581: windows are discovery 2016-08 to 2021-12, selection 2022-01 to 2023-12, confirmation 2024-01 to 2026-07.
- docs/alpha-pooled-mining.md:722-742: literature overlap rule. A campaign finalist that overlaps a failed literature entry (including the reversal family) gains no probe eligibility.
- grep of agentic_trader/research/pooled and agentic_trader: no VIX series, sector/industry field or shares-outstanding (turnover) input exists in the pooled DSL.
- Web: Da, Liu & Schaumburg (2014), Management Science 60(3):658-674, via the RePEc URL. The 4x figure is real, but it belongs to an 'enhanced' strategy that removes cash-flow news measured by analyst forecast revisions.
- Web: Nagel (2012), RFS 25(7):2005-2039. Reversal returns are 'highly predictable with the VIX index'. Confirmed.
- Jegadeesh (1990), JF 45(3): bibliographic details correct.

**Bad references**

_none_

**Notes**

Verdict: partially confirmed. The core facts are right, but the claim overstates one statistic and leaves out evidence that weakens its premise.

**What checks out:**
- events-v1 attaches a momentum citation to a one-month return.
- The quiet_momentum numbers and the "hypothesis only" status are correct.
- Every fixed set uses the 20-session bracket.
- BracketSpec parameterises the hold.

**Detail errors:**
1. "The lane's largest |t|" is wrong at the pooled-lane level. shorts_retreat on cohort v3 reached t +3.40 (docs/alpha-pooled-short-v1-2026-10-20.md:94). It holds only within the events fixed sets, or as the largest negative t.
2. The 8.6% target rate comes from the high52-v1 picks on the cohort-v1 literature cube. It is not a measured base rate for the cohort v2/v3 cubes.
3. The 5-session reversal evidence is selective. It cites only v3's positive numbers. On v2, reversal_5 was −0.021R, t −0.86 (docs/alpha-pooled-checks-v4-2026-10-07.md:46).

**Material omissions (counter-evidence):**
- **One-month reversal was already tested.** reversal-lowmax-v1 is -roc(close, 21) with a low-MAX filter, citing Jegadeesh 1990. It was a frozen literature entry over 2016-08 to 2026-07 and failed: paired edge +0.008R, t 0.52, recent edge −0.021R (docs/alpha-pooled-literature-2026-09-30.md:10-13, 71). Its per-year edges for 2022 and 2023 were also read. So the 2022-23 window the recommendation proposes to score on is not clean for this hypothesis family. The literature overlap rule (docs/alpha-pooled-mining.md:722-742) also limits what a close relative could earn.
- **The label-horizon premise was already probed once.** The 2026-10-07 label diagnostic re-scored reversal_5 on plain 5- and 20-session forward returns, with no bracket. The 5-session edge was +16 bps (t 1.30), and per unit of ATR the edge was −0.048. Its conclusion was "the bracket label is not what removes an edge" (docs/alpha-pooled-checks-v4-2026-10-07.md:81-117). That check did not cover the 20-session quiet reversal, so a horizon-matched retest is not fully pre-empted, but it should be cited.

**Feasibility of the recommendation:**
- The VIX-median condition needs a VIX series, which the pooled DSL lacks.
- The turnover-tercile condition needs shares outstanding, which is absent anywhere in agentic_trader.
- So formulas 3 and 4 need new data inputs, not only C16.

**References:**
- Jegadeesh 1990 and Nagel 2012 are accurate. Nagel is VIX-predictable reversal, RFS 25(7):2005-2039.
- Da-Liu-Schaumburg 2014 exists and does report about 4x the standard reversal's risk-adjusted return. But that figure is for an 'enhanced' reversal that strips cash-flow news measured by analyst forecast revisions, at a one-month horizon.
- An industry-only 5-session residual, as recommended, would not replicate the 4x. That is a mild overreach in how the paper is used, not a non-existent citation, so it is not listed in bad_references.
- Chan (2003) and Medhat-Schmeling (2022) are cited only in the claim text. Their characterisations (no-news reversal; reversal concentrated in low-turnover names) match the published papers.

### Value review — P3, later (months)

**Reasoning**

The finding is right about the horizon mismatch but wrong about the history, and the reversal-specific set is not worth its own campaign.

1. The one-month reversal "as the literature states it" has already been tested. `reversal-lowmax-v1` froze `-1.0 * roc(close, 21)` with the Bali-Cakici-Whitelaw low-MAX filter (docs/alpha-pooled-literature-2026-09-30.md:10-13, :29). It was read on 2016-08 to 2026-07, and it failed P2 and P3:
   - paired edge +0.008R, t 0.52;
   - since 2023, -0.021R;
   - by year, 2022 -0.006 and 2023 +0.020 ("Paired edge by year").
   That test used a 20-session hold, which is the correct horizon for the Jegadeesh one-month effect. Formula 1 on the 20-session cube is therefore a near-duplicate of a null. The 2022-23 selection window the finding proposes has already been read for this signal on overlapping liquid names, so it is not a clean window.

2. The motivating statistic is a post-hoc sign flip. `quiet_momentum` (events-v1.json:186-190) showed the top decile of 20-session returns underperforming:
   - v2: -0.119R, t -3.34;
   - v3: -0.044R, t -1.69 (docs/alpha-pooled-events-v1-2026-10-15.md table, "Reading"; docs/alpha-pooled-cohort3-2026-10-16.md "Events").
   Weak winners do not imply strong losers. The lane has tested losers directly five times with no signal:
   - `quiet_reversal` +0.011 and +0.027;
   - `reversal_5` t 1.44;
   - `reversal_x_volume` t 1.45;
   - `reversal-lowmax` t 0.52.
   The asymmetric negative is also exposed to the untested low-volatility confound. The cube has no ATR or beta arrays, and the control removes only the equal-weighted mean (pooled map gap 3; cube.py:53).

3. Three of the four formulas rest on infrastructure that does not exist:
   - The pooled lane reads no VIX series: grep finds no `vix` in agentic_trader/research/pooled.
   - Industry residuals need sectors (C16), and cohort v3 has none.
   - Turnover needs shares outstanding, which is absent.
   Each conditioning split also halves or thirds an already marginal sample. On 498 selection sessions the SE is about 0.06R, so even an unshrunk -0.119R effect gives t of about 2.

4. Survivorship inflates exactly this signal. Both cohorts are current members, and reversal profits most from dead losers being absent (data map I5). A pass would not be credible without a prospective or point-in-time read.

5. The payoff to the north star is small even if the effect is real. The decile edges at stake are 0.02-0.05R, and decile evidence does not transfer to the top 1-2 names that cards trade (pooled map gap 17). That would not close the 0.10-0.15R gap to SPY.

The part worth funding is generic: a 5-session (and 1-session) time-exit cube. BracketSpec already parameterises the hold (cube.py:57-71), and session_count_v1 exits exist live. Fund it as its own item for all short-horizon priors (5-day reversal, short flow, overnight, first hour), not as a reversal-only second look.

**Risks**

- **Garden of forking paths.** This would be the sixth or seventh reversal variant on the same names. It would add VIX, turnover and residual splits chosen after seeing a sign-flipped discovery statistic.
- **No clean window.** reversal-lowmax-v1 already read 2016-2026, which covers both the 2022-23 selection window and the 2024-26 confirmation period. A "pass" would be a second look presented as independent evidence. The overlap rule in campaign_run.py:107 checks only the two pooled literature entries, so it would not catch this.
- **Survivorship bias favours exactly this long-loser signal.** It could manufacture a false finalist and burn the lane-wide confirmation window (pooled map gap 7).
- **Low power.** On 498 sessions, with conditioning splits, the likely outcome is another uninformative null that still costs operator approval and check A/C compute (about 3 h per protocol).
- **Mismatched evidence.** Decile results would not speak to top-1/2 card selection.

**Prerequisites**

- A risk-matched (sector × ATR-quintile, or ATR/beta regression) control on the existing cached cube, to see whether the quiet_momentum -0.119R survives volatility matching (pooled map item 1).
- A generic 5-session / 1-session time-exit cube built from cached bars.
- Overlap accounting that registers reversal-lowmax-v1's inspected 2016-2026 interval against any new reversal protocol.
- A VIX series in the cube inputs, sector labels (C16) and shares outstanding, before any conditioned variant.
- A prospective or point-in-time (survivorship-corrected) evaluation interval.
- Operator approval of a declared second look.

**Better alternative**

There are two cheaper, higher-leverage moves.

1. **Read the anti-momentum result as a statement about the native cards.** quiet_momentum, intraday_high_proximity and overnight_persistence are all negative, and the sign replicates across disjoint cohorts v2 and v3. Each describes buying recent strength, which is what the native breakout cards do. Run the risk-matched control on the existing cube; it takes hours on cached data. Add a descriptive extension column to `cards outcomes`: the 20-session return percentile and the distance from the 20-day high in ATR. If card R falls as extension rises after volatility matching, a "no-chase" construction rule can go through the existing preview-then-enforce path. That acts on today's card stream instead of creating a new source that has already tested null.

2. **Build the horizon-matched cube once.** Put a single predeclared short-horizon fixed set on it: market-residual 5-day reversal (Da-Liu-Schaumburg, using SPY beta only, so no C16 is needed), short flow, overnight and first hour. That replaces a reversal-only campaign. Leave the one-month 20-session reversal closed: reversal-lowmax-v1 already tested it.

---

<a id="c49"></a>

## C49 — The pooled genetic search never used fitness, so check B's failure measured random sampling rather than search power

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | next (weeks) | alpha_mining | defect | medium | S | partially_confirmed |

_lenses: ml_

### Claim (as consolidated)

Campaigns v2 and v3 split formula_budget 200 across 12 families, giving 16–17 formulas each (campaign.py:381-391). archive_size is 16 (campaign-v2.json:317; v3:320).

Fitness never entered the search:
- TypedGeneticSearch.tell evicts only when the archive exceeds archive_size (search.py:89-111).
- ask() picks parents uniformly from sorted(self.archive) (:175-180).
- With budget at or below archive size, nothing is evicted before the last ask.

Every v2/v3 proposal and every check B run (search_power.py:115-124 calls the same family_search) was therefore a uniformly random crossover or mutation.

The result docs instead attribute B's 1/10 and 3/10 to basket non-smoothness and the search's 'reach', and the lane was parked on that reading.

### Evidence

- agentic_trader/research/alpha/search.py:89-111, 164-184
- agentic_trader/research/pooled/campaign.py:381-391; research/pooled/genetic.py:147-153
- config/research/pooled/campaign-v2.json:42, 317; campaign-v3.json:45, 320
- agentic_trader/research/pooled/search_power.py:15-20, 107-124
- docs/alpha-pooled-checks-v3-2026-10-05.md 'Why it failed', 'Decision'

### Recommendation

1. Amend alpha-pooled-mining.md to say that checks B v2/v3 measured random neighbourhood sampling.

2. Do not reopen pooled GP: useful search budgets are unaffordable under per-formula charging. Any search should live inside one charged model spec, as nested purged CV over a small, declared grid.

3. If any GP is revived, including the per-symbol miner (archive 32, budget 512):
   - use tournament or fitness-proportional parent selection;
   - keep archive_size well below the budget;
   - add a unit test that the proposals change when the told fitness values change.

### North-star impact

Removes a misleading result from the design record and ends any plan to fix mining with a larger GP budget.

### References

- Tang et al. (2025), AlphaAgent, arXiv 2502.16789 — Competitive formula search runs at far larger budgets and still reports tiny US out-of-sample IC (https://arxiv.org/abs/2502.16789)

### Fact check — partially_confirmed

**Corrected claim**

The core defect holds and I verified it by experiment. In pooled protocols v2 and v3 (formula_budget 200 over 12 families, so 17 formulas each for the first eight families and 16 for the last four; archive_size 16), the archive never exceeds archive_size before a family's last ask. TypedGeneticSearch.ask picks parents uniformly from sorted(self.archive), which sorts alphabetically. Fitness reaches the search only through eviction, so the told fitness values had no effect on any proposal. Running the real v3 family parameters and vetting under two unrelated fitness functions gave identical proposal sequences for all 12 families.

Three details need correcting:

(1) Not every proposal was a crossover or mutation. Each family's first 1–2 proposals are its declared seeds (23 seeds in total). The remaining ~14–15 per family are fitness-independent random mutations or crossovers of earlier proposals.

(2) Check B was also inert to its plant. A family's proposals do not depend on fitness, so every check B run for the same family scored the same 16–17 formulas: reversal seeds 0, 3, 6 and 9 shared one set, as did range_location seeds 1, 4 and 7 and abnormal_volume seeds 2, 5 and 8. B therefore tested whether a fixed random draw happened to contain a near-copy of each hidden formula. It did not measure search power.

(3) The parking itself was the pre-registered consequence of B < 8/10 (v3 doc, 'Decision'). It was not a judgement made on the docs' reading. What that reading shaped was the v2→v3 redesign (decile selection, chosen to give the search 'a gradient to climb') and the 'larger budget' direction left open. Once the budget exceeds archive_size, a larger budget would in fact switch on elitist truncation selection.

The recommendation's '(archive 32, budget 512)' for the per-symbol miner is unsupported. The archive default is 32, but the budget is the catalog size plus --iterations, which defaults to 25 (cli/commands/alpha.py:294), and the docs use 9 or 25. No 512 exists in the repo, so the per-symbol miner probably also rarely or never evicted.

**Checked**

- agentic_trader/research/alpha/search.py:60-111 (tell: truncation to archive_size happens only when the archive exceeds it; the archive is kept in fitness and family order)
- agentic_trader/research/alpha/search.py:164-184 (ask: drains seed_queue first, then picks parents with sorted(self.archive), which is alphabetical by expression, and rng.sample/rng.choice; fitness values are never read)
- agentic_trader/research/pooled/campaign.py:381-391 (family_budgets: divmod(200,12) gives 17x8 + 16x4)
- agentic_trader/research/pooled/genetic.py:147-153 (archive_size=protocol.search.archive_size), :175-212 (rejected proposals are never told; errors are told -inf; evaluated proposals are told row['fitness'])
- config/research/pooled/campaign-v2.json:42 (formula_budget 200), :317 (archive_size 16); campaign-v3.json:45, :320 (same values); 12 families with seeds [2,2,2,2,1,2,2,2,2,2,2,2] = 23 seeds
- agentic_trader/research/pooled/search_power.py:15-20 (docstring blames 'reach'), :107-125 (check B calls the same family_search with the campaign seed and budget, and seen=set())
- Empirical read-only simulation (venv python -I -B): for all 12 v3 families with the real seed, operators, windows, archive_size and vet_expression, the proposal sequences were IDENTICAL under two unrelated fitness functions; the archive size at each ask peaked at 16 (17-budget families) or 15 (16-budget families), so nothing was evicted before the last ask
- docs/alpha-pooled-checks-2026-10-02.md 'Why it failed' (the search 'has no gradient to climb'; a non-smooth top-3 basket) and 'What happens next' item 3 ('fitness landscape stays flat')
- docs/alpha-pooled-checks-v3-2026-10-05.md 'Why it failed' ("the search's reach is the next one") and 'Decision' ('As pre-registered ... parked'; the open direction 'a larger search budget per family')
- docs/alpha-pooled-mining.md:633-642, 651-672 (same attribution; no mention that fitness is inert)
- tests/research/test_alpha_search_families.py, tests/research/test_alpha_discovery.py, tests/research/pooled/test_genetic.py (no test that proposals depend on told fitness)
- agentic_trader/research/alpha/miner.py:347, :434 (per-symbol miner: TypedGeneticSearch(self.seed), default archive_size 32; budget = catalog + iterations)
- agentic_trader/cli/commands/alpha.py:294 (--iterations default 25); docs/cli-reference.md:280 and docs/alpha-pipeline.md:307 (iterations 9); no budget of 512 found anywhere
- arXiv 2502.16789 abstract and HTML v2 Table 2 (AlphaAgent S&P 500 IC 0.0056, ICIR 0.0552; 20 trials x 5 rounds; factor counts per round not reported)

**Bad references**

_none_

**Notes**

Every file:line citation exists and supports its use: search.py:89-111 and 164-184; campaign.py:381-391; genetic.py:147-153; campaign-v2.json:42 and 317; campaign-v3.json:45 and 320; search_power.py:15-20 and 107-124; and the v3 doc sections.

Two further sources share the docs' misattribution:
- The v2 doc (docs/alpha-pooled-checks-2026-10-02.md, 'Why it failed') attributes the failure to a flat landscape ('no gradient to climb'), and its item 3 says 'A larger per-family budget helps only marginally while the fitness landscape stays flat'. A gradient could not have been climbed either way.
- docs/alpha-pooled-mining.md 'The genetic search' never states that parent selection is uniform.

No test asserts that proposals depend on told fitness, so recommendation 3 (a regression test) targets a real gap.

The AlphaAgent reference (Tang et al., arXiv 2502.16789, KDD 2025) exists and partly supports its use. US S&P 500 IC is tiny: 0.0056, ICIR 0.055 in Table 2. However, the paper does not quantify its search budget beyond 20 trials x 5 rounds, so 'far larger budgets' is plausible but not directly evidenced. It is not listed as a bad reference.

The finding's 'impact: medium' is reasonable. The lane is parked and the fixed-set lanes don't use the search, so the defect alters the design record, not any live behaviour.

### Value review — P3, next (weeks)

**Reasoning**

The defect is real. The configs set formula_budget 200 over 12 families (config/research/pooled/campaign-v2.json and campaign-v3.json, both with archive_size 16). family_budgets splits that as divmod(200, 12) (agentic_trader/research/pooled/campaign.py:381-391), so 8 families get 17 formulas and 4 get 16.

tell() is called only for charged formulas (agentic_trader/research/pooled/genetic.py, the search.tell calls after scoring). It prunes the archive only once it holds more than archive_size entries (agentic_trader/research/alpha/search.py:89-111). ask() draws parents uniformly from sorted(self.archive) (search.py:175-180). The archive therefore reaches 17 entries only after the last ask. Each family had 1-2 seeds, so the other ~15 proposals were random crossovers or mutations of earlier proposals, whatever their fitness. Expressions that errored (told -inf) stayed eligible as parents. Check B reuses the same family_search with the same budgets (agentic_trader/research/pooled/search_power.py:107-124), so its 1/10 and 3/10 measured random neighbourhood sampling.

This matters little for the north star:
- **The verdict stands.** Check B certifies the search as configured, and that search could not recover the planted edges. The v3 doc's "Why it failed" already says the misses' baskets differ from anything the 17-formula search proposed (docs/alpha-pooled-checks-v3-2026-10-05.md). It leaves out the cause (no selection pressure) but does not misstate the result. Parking the lane remains correct.
- **Fitness would not have rescued it.** With 2 seeds and about 15 proposals, a fitness-guided search gets roughly one generation of exploitation. That is not enough to change the conclusion. The finding's own recommendation is not to reopen pooled GP, and I agree.
- **No production or card impact.** The lane is parked and the weekly ETF32 miner is retired (CLAUDE.md). The per-symbol miner (archive 32, budget 512) does get truncation selection once 32 formulas are evaluated, so the finding's tournament-selection fix is unnecessary there.

The value is a cheap correction to the design record, plus a guard so a revived GP cannot silently become random search. Fund it only as a few lines folded into the next workstream PR, not as its own effort.

**Risks**

- **Reopening the lane.** Rewriting the explanation could be read as "check B was invalid, rerun GP with a bigger budget". That would spend trials and widen forking paths in a lane that 64 null formula-cohort pairs argue against. The amendment must say the fail verdict stands and only the explanation changes.
- **Overcorrecting.** Calling check B "meaningless" goes too far. It correctly certified that the configured search lacked power, and the parking decision is unaffected.
- **Rewriting history.** Editing frozen result docs breaks the record. Add a dated note to docs/alpha-pooled-mining.md (and at most a forward pointer from the v2/v3 check docs) instead of rewriting their findings.
- **Process.** The operator's rule is no docs-only PRs, so this has to ride on a feature branch.
- **Wasted effort.** Building tournament selection now is effort on a parked lane.

**Prerequisites**

None for the doc note beyond a feature PR to attach it to. Any GP revival, pooled or per-symbol, would need:
- a new frozen spec and operator approval;
- a CampaignProtocol validator that rejects a genetic protocol whose archive_size is not well below its smallest per-family budget;
- a unit test showing ask() proposals change when the fitness values passed to tell() are permuted.

All three belong in the revival spec itself, not in this change.

**Better alternative**

Make it two small changes in the next feature PR:
1. A dated sentence under the "Status (2026-10-05)" paragraph of docs/alpha-pooled-mining.md. It should say that with 16-17 formulas per family and archive_size 16, no eviction happened before the last ask (search.py:89-111, 175-180), so the v2/v3 searches and check B were fitness-blind random sampling around the seeds; that the fail verdict and the parking decision stand; and that a revived search needs selection pressure.
2. A five-line guard in CampaignProtocol validation for search_mode genetic, requiring archive_size < min(family_budgets()).

Skip tournament or fitness-proportional selection work unless and until a GP lane is formally reopened. The finding's point 2 (any search lives inside one charged model spec, as nested purged CV over a small declared grid) is the more useful direction. It belongs in whatever entry-side research spec comes next, not in this fix.

---

<a id="c55"></a>

## C55 — Research and live import each other with no enforced boundary, and the shared kernels live under research/

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | architecture | gap | medium | M | partially_confirmed |

_lenses: architecture_

### Claim (as consolidated)

Live depends on research: 17 live modules make 35 imports from agentic_trader.research. Examples:
- evaluator.py:35 (bracket_prices);
- workflow.py:40-41;
- config.py:59;
- book_sizing.py:27-28;
- formulaic.py:9-11;
- copilot.py:103-116.

Research depends on live: research/ makes 68 imports into storage, execution, screeners, agent, broker and notifier. No import contract exists.

The shared kernels sit under research/: bracket policy, label_bracket, trailing_price, the DSL, covariance and the card_stats schema. Private bootstrap helpers also cross package boundaries.

### Evidence

- grep at db82817: 35 'from agentic_trader.research' lines in 17 live modules; 68 research→live import lines
- agentic_trader/agent/evaluator.py:35; storage/workflow.py:40-41; config.py:59; execution/book_sizing.py:27-28; screeners/formulaic.py:9-11; agent/copilot.py:103-116

### Recommendation

Create a pure agentic_trader/kernels package containing:
- the bracket, label and trail kernel;
- the DSL and the session bar builder;
- covariance and the public stats helpers;
- the Forecast, DataContract and ExecutionPolicy types.

Enforce the boundary with import-linter in pre-commit:
- live packages may import kernels;
- research may import only kernels and storage;
- research must not import broker, notifier or execution.

Any kernel change must pass the parity regression test.

### North-star impact

Lets research iterate without silently changing live card construction, while kernel changes are mirrored in replay by construction.

### References

- import-linter (layers, forbidden, independence contracts) — CI-enforceable boundary between research, kernels and live (https://import-linter.readthedocs.io/en/stable/)

### Fact check — partially_confirmed

**Corrected claim**

Live code depends on research. 17 non-CLI live modules make 35 direct import lines from agentic_trader.research; every cited location is accurate (evaluator.py:35, workflow.py:40-41, config.py:59, book_sizing.py:27-28, formulaic.py:9-11, copilot.py:103-116). CLI commands add 92 more.

Research also depends on live. The 68 research import lines go into storage (44), execution (9), agent (8) and screeners (7). None go directly into broker or notifier:
- broker is reached only indirectly, because research imports storage.workflow and storage/workflow.py:25 imports broker.base;
- importing every research module loads no notifier module.

Research also imports market (58), data (28), config (9), transport (2: research/news.py:29 and research/corporate_actions.py:24) and runtime (1), for 170 non-research import lines in total. storage.workflow and storage.alpha form two-way cycles with research.

No import contract covers the research/live boundary. The only import-isolation check is tests/risk/test_rules.py:404-417, which applies to the risk package alone.

Live-used kernels sit under research/:
- bracket_prices, entry_limit, trailing_price and execution_policy_from_dict (research/alpha/strategy.py);
- the DSL (research/alpha/dsl.py:179);
- covariance (research/setups/covariance.py);
- the convex optimiser;
- the CardStatsSnapshot schema (research/setups/card_stats.py:65).

label_bracket (research/setups/labels.py:159) is not imported by live code directly. It reaches the live card_policy gate through the daemon's CardStatsWorker, which lives in research.

The private bootstrap helpers (_ci90, _stationary_index_draws) cross sub-package boundaries within research (pooled, spread and apriori import them from setups). They do not cross the live/research boundary.

The recommended contract would not pass as written. It says research may import only kernels and storage and must not import broker. import-linter checks indirect imports by default, and storage.workflow imports broker.base, so that rule fails at once. Research also depends heavily on market, data and config. Before the rule can hold, storage.workflow.encode and the broker DTOs have to move into the kernel or shared layer, and the market, data and config layers have to be allowed explicitly.

**Checked**

- grep at db82817 (agentic-trader-intraday worktree): 35 'from/import agentic_trader.research' lines in 17 non-CLI, non-research modules. Exact match; CLI commands add 92 more lines.
- agentic_trader/agent/evaluator.py:35 imports bracket_prices, entry_limit and execution_policy_from_dict from research.alpha.strategy; bracket_prices is used at evaluator.py:242
- agentic_trader/storage/workflow.py:40-41 imports research.alpha.validation and research.apriori.probe
- agentic_trader/config.py:59 imports MAX_PROBE_TERM_DAYS from research.alpha.probe
- agentic_trader/execution/book_sizing.py:27-28 imports ConvexAlphaPortfolioOptimizer and shrunk_covariance/daily_returns; they are used at :271 and :440
- agentic_trader/screeners/formulaic.py:9-11 imports clock, dsl and strategy from research.alpha
- agentic_trader/agent/copilot.py:103-116 makes 7 research imports, including trailing_price/policy_trails (used at :2541 and :2574)
- Kernel locations: research/alpha/strategy.py:117,175,200,225; research/setups/labels.py:159 (label_bracket); research/alpha/dsl.py:179; research/setups/covariance.py:50,80; research/setups/card_stats.py:38,65; research/alpha/optimizer.py:45
- card_stats consumers: execution/card_evidence.py:20 imports CardStatsSnapshot; the daemon runs CardStatsWorker from research (cli/commands/service.py:40,328)
- Research-to-other-package direct imports by target: market 58, storage 44, data 28, execution 9, config 9, agent 8, screeners 7, constants 4, transport 2, runtime 1 (170 in total); broker 0; notifier 0
- python -I import of every research module: agentic_trader.broker.* ends up loaded (transitively, via storage/workflow.py:25 and execution/agent); no notifier module is loaded
- No import-linter config in pyproject.toml or .pre-commit-config.yaml, and no .importlinter file
- tests/risk/test_rules.py:404-417 is the only import-isolation test; it covers the risk package only
- Private helper imports: research/pooled/stats.py:25-26, research/pooled/power.py:47, research/spread/evaluate.py:13-14 and research/apriori/statistics.py:16-17 import _ci90/_stationary_index_draws from research/setups/*
- Two-way cycles: storage/workflow.py:40-41 imports research while research/alpha/shadow.py:22, decisions.py:34, observation.py:30, news.py:28 and others import storage.workflow; storage/alpha.py:25-28 imports research.alpha while research imports storage.alpha 8 times
- import-linter docs: https://import-linter.readthedocs.io/en/stable/ lists layers, forbidden and independence contracts; contract_types/forbidden/ says 'Indirect imports will also be checked' by default

**Bad references**

_none_

**Notes**

The core of the finding holds. The live-to-research figure (35 imports in 17 modules) is exact, every cited file:line is correct, and no import contract covers research and live.

What is wrong:
1. The 68 research-to-live imports are said to go into "broker and notifier" among others. Research has zero direct imports of either. Broker is loaded only indirectly, through storage/workflow.py:25 and execution; notifier is not loaded at all. The 68 is exactly storage + execution + agent + screeners. The finding also leaves out the heavier dependencies on market (58), data (28) and config (9), and the 2 transport imports.
2. label_bracket is not a direct live import. It feeds live behaviour indirectly, through the CardStatsWorker that the daemon runs and the card_policy gate.
3. The private bootstrap helpers cross sub-package boundaries inside research, not the research/live boundary.
4. One narrow import-isolation test does exist: tests/risk/test_rules.py:404-417, covering the risk package only.

The recommendation is directionally sound but under-specified. import-linter forbidden contracts check indirect imports by default, and research imports storage.workflow.encode in at least 6 places, so the proposed rule "research may import storage but not broker" fails on day one.

The import-linter reference is valid and supports its use. A background import-scan job (ID b3uxylnwu) was still running when I finished; I did not need its result.

### Value review — P3, later (months)

**Reasoning**

The facts are right, but the remedy does nothing for the north star.

**The finding checks out at db82817.**
- Live code imports about 20 distinct research modules. Examples are `agent/evaluator.py:35`, `execution/book_sizing.py:27-28`, `screeners/formulaic.py:9-11`, `storage/workflow.py:40-41`, `config.py:59` and `agent/copilot.py:103-116`.
- `bracket_prices` and `trailing_price` sit in `research/alpha/strategy.py:175,200`.
- No import-linter, tach or deptry is configured.
- My raw count was 127 lines, because it includes the `cli/commands` alpha subcommands. Those are composition code, so leaving them out makes the finding's 35 roughly right.

**Why it is not worth funding now.**
1. **No alpha impact.** It adds no signal, no card and no better sizing. The binding constraint is that 64 formula-cohort pairs produced no finalist and sent cards trail SPY. The bracket is closed as an explanation (`docs/bracket-counterfactual-2026-10-18.md`), so the next work is on the entry side. Moving files does not change that.
2. **The risk is mostly theoretical.** It is a single operator on a paper account. I found no documented incident where a research refactor changed live card construction. The silent-loss bugs that actually happened were config ones: the `corporate_actions` YAML block was ignored before 2026-10-13, and the map's G8 notes `session:` is never passed to `AppConfig`.
3. **Much of the coupling is deliberate parity.** CLAUDE.md requires that "research/live share scores and versioned bracket policy".
   - `research/setups/replay.py:83` imports the live `RiskEvaluator` on purpose, so replay uses the production gate.
   - `research/alpha/strategy.py:19` imports `execution.lifetime_policy` so research and live share the semantics-v5 57,600 s session lifetime.
4. **The proposed contract would break that parity.** "Research may import only kernels and storage" forbids `replay.py` from importing `agent.evaluator`. Satisfying it means either copying the evaluator, which breaks the one-implementation rule, or moving the evaluator and risk into `kernels/`. Either way it becomes a large refactor of the most safety-critical modules.
5. **The move does not deliver the stated benefit.** Renaming `research/` to `kernels/` does not stop a kernel edit from changing live; it only relabels it. The real protections already exist:
   - immutable, versioned policy hashes;
   - existing tests on both sides (`tests/agent/test_evaluator_*`, `tests/research/test_alpha_execution.py`, `tests/research/setups/test_labels.py`).
6. **The research-to-broker/notifier rule is already met.** Research has zero imports of `broker` or `notifier`. The only `transport` imports are the two read-only data clients in `research/news.py:29` and `research/corporate_actions.py:24`. A contract there would document today's state, not fix anything.

**What is worth keeping.**
- The parity regression test. It belongs to the roadmap's planned L5 step, "one bracket-label kernel and a sizing protocol callable from replay" (`docs/alpha-roadmap.md:496`).
- Relocating the kernel is reasonable once L5 is funded, which it isn't yet.

**Risks**

- **Churn in the riskiest modules.** The move touches `evaluator.py`, `copilot.py`, `book_sizing.py`, `workflow.py`, the probe and registry storage, and 20+ CLI commands, all of them on the entry-authorization, sizing and registry path.
- **Merge conflicts and in-flight work.** A move this broad conflicts with in-flight research branches; the insider campaigns are pending approval.
- **Daemon redeploy.** It forces a redeploy of a running daemon for zero behaviour change.
- **The contract as written breaks parity.** The stated rule forbids research from importing `agent`, but `research/setups/replay.py:83-84` deliberately imports `RiskEvaluator` and `RegimeDetector`. Meeting the rule would mean duplicating them, which breaks the one-implementation rule and lets replay drift from live, or moving the risk and evaluator stack into `kernels/`.
- **Diluted kernel package.** Moving the probe constants, `ValidationPolicy` and the `card_stats` schema into `kernels/` would make it a dumping ground rather than a pure layer.
- **No compatibility aliases.** CLAUDE.md forbids them for internal callers, so every caller must change in one PR. That makes the diff large and hard to review.

**Prerequisites**

- Fund L5 first (`docs/alpha-roadmap.md:496`): one bracket, label and trail kernel callable from replay, and retire the legacy backtest's private trail logic. A parity test should prove that the live evaluator's bracket and trail on a fixed setup equal the replay labeller's prices and exits.
- Before you write any contract, run an import-graph audit. It should separate deliberate parity imports (replay to the evaluator, `strategy` to `lifetime_policy`) from accidental coupling (`config.py:59` reaching into `research.alpha.probe` for a constant, and `data/corporate_actions_worker.py:18` importing `in_scan_window` from `card_stats_worker`).
- An operator decision that architecture work is worth an engineering slot ahead of the next entry-side data source.

**Better alternative**

Skip the package move and spend about an hour on a narrow import-linter config with no file moves.

1. **Forbid research → broker and notifier.** Today there are zero violations, and `transport` is allowed for the read-only data clients. This keeps research from ever gaining order or Telegram side effects.
2. **Allowlist the research modules live code may import.** Live packages (`agent`, `execution`, `screeners`, `storage`, `config`) may import only:
   - `alpha.strategy`, `alpha.dsl`, `alpha.clock`, `alpha.models`, `alpha.probe`, `alpha.validation`, `alpha.optimizer`;
   - `setups.covariance`, `setups.card_stats`, `setups.ranker`, `setups.sources`;
   - the `apriori.probe`, `apriori.catalog` and `apriori.pead_live` entries already in use.

   Any new research module that reaches the daemon then needs an explicit, reviewed edit to the list.
3. **Fold the kernel relocation into L5** when that step is funded, together with the bracket, label and trail parity test.

If the slot is for architecture at all, two items do more for the north star:
- **Fail-closed config loading.** `config.py` should reject unknown keys and pass every YAML block. Silent config loss is the bug class that has actually bitten this desk (G8).
- **A generic live channel for cross-sectional finalists** (the map's G6). Without it, a pooled-lane winner needs bespoke `run_scan` code before it can produce a single card.

---

<a id="c56"></a>

## C56 — Stop carrying dormant one-shot harnesses and contract-violating legacy components on main

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | architecture | stop_doing | medium | M | partially_confirmed |

_lenses: architecture_

### Claim (as consolidated)

Dormant code:
- research/ is about 32k LOC. research/alpha has 66 modules, mostly one-shot frozen harnesses.
- cli/commands/alpha.py is 2,208 lines with 38 commands.

Duplicate bracket engines: besides labels.py there are two more, research/alpha/simulation.py and backtest/engine.py, and the live trail lives inside TradingCopilot.

Legacy components that contradict execution contracts:
- RedundantBroker fails a POST over to another broker on 'timeout' text (redundant.py:121-161).
- SlicedExecutionEngine infers a fill from the limit price (execution/engine.py:126-129).
- PaperBroker fills at the latest price and ignores the limit (paper.py:62-65).

The tag-and-retire pattern already exists: research/power-diagnosis-v1-20260918.

### Evidence

- find agentic_trader/research -name '*.py' | xargs wc -l (32,349); ls research/alpha (66 entries)
- agentic_trader/cli/commands/alpha.py (2,208 lines, 38 commands)
- agentic_trader/research/alpha/simulation.py:174; research/setups/labels.py:159; backtest/engine.py
- agentic_trader/broker/redundant.py:121-161; execution/engine.py:126-129; broker/paper.py:62-65
- git tag research/power-diagnosis-v1-20260918

### Recommendation

1. Tag each completed harness and delete it, together with its CLI command, keeping the result docs and journal artifacts.

2. Delete or quarantine RedundantBroker and the slicer.

3. Make PaperBroker use the label kernel's fill model.

4. Re-base simulation.py and backtest/engine.py onto the single label kernel (L5), or retire them.

### North-star impact

Lowers the cost of changing the shared kernels that the Forecast, selector and parity work must touch, and removes paths that could break the no-replay and no-inferred-fill guarantees.

### References

_none_

### Fact check — partially_confirmed

**Corrected claim**

Main still carries a lot of finished, CLI-only research code. By import reachability, 30 of the 64 research/alpha modules (6.8k of 14.4k LOC) are reachable only through the alpha CLI. Examples are calibration, power_study/power_trace/power_artifacts, lifetime_*, persistent_*, volume_study, study/study_artifacts, factor_controls*, retained_* and benchmark_workflow. Across research/ as a whole, about 19.5k of 32.3k LOC is CLI-only. That CLI-only set still includes active lanes (pooled insider campaigns pending approval, alpha mine/qualify/registry), so "mostly one-shot" overstates it: the true harness share is roughly a third to a half. The other 34 research/alpha modules are live runtime dependencies (daemon observation/decision/daily-panel workers, shadow, probe, forward evidence, trailing_price), as are research/setups card_stats/covariance/ranker and apriori probe/pead_live. cli/commands/alpha.py is 2,208 lines with about 40 commands (37 decorator sites plus 3 loop-generated), and nine sibling alpha_*.py files add another about 784 lines.

There are three independent bracket walkers with divergent fill semantics:
- labels.py:159 fills gaps at the open and counts a both-touch bar as the stop.
- simulation.py:174 is the canonical engine for alpha mining, qualification and replay.
- backtest/engine.py:222-232 fills at the level, with no gap handling.
The native live trail is inline in TradingCopilot (copilot.py:2569-2572). Alpha-policy trails reuse research.alpha.strategy.trailing_price (copilot.py:2573-2576), the same function simulation.py uses.

Of the three contract-inconsistent components, only RedundantBroker is genuinely legacy, and it is latent:
- It is off by default (config.py:503) and enabled only by YAML or BROKER_REDUNDANCY_ENABLED. When on, it would replay an entry POST to the fallback on 'timeout'-type errors once the circuit opens (redundant.py:121-161) and fail closes over to the fallback.
- In practice, entries through EntryExecutionService fail closed under it, because it lacks entry_market_context (base.py:303-309, entries.py:125-127). Its real hazard is that it hides authoritative_positions, which downgrades manual close and panic to the legacy path.
- SlicedExecutionEngine is not legacy: it is the live entry executor. Its limit-price fill inference (engine.py:127) is reachable only with simulated brokers (engine.py:55-59) and a non-immediate algorithm (config.yaml:355 ships 'immediate'). The fix is to delete the slicing branch and slicer.py, not the engine.
- PaperBroker is the supported EXECUTION_MODE=paper simulator. It does ignore the limit and fills at the latest price, or at request.entry_price or 100.0 (paper.py:62-65).

The research/* tags preserve source revisions for result docs. No tagged harness has been retired yet, so tag-and-delete would be a new step, not an existing pattern.

**Checked**

- find agentic_trader/research -name '*.py' | xargs wc -l -> 32,349 total (confirmed)
- ls agentic_trader/research/alpha -> 66 entries, but that count includes __pycache__; there are 65 .py files (64 modules plus __init__)
- AST import reachability over the worktree (script at ~/agentic-trader-research/holistic-review-20261021/reach_c56.py): with daemon/runtime roots (non-CLI packages plus cli/commands/service.py and cli/utils.py), 34 of 64 research/alpha modules are runtime-reachable. 30 modules (6,765 of 14,408 LOC) are reachable only through the CLI. Across all of research/, 19,523 of 32,349 LOC is CLI-only
- Live dependencies on research/: cli/commands/service.py:35-39 (daily_observations, daily_plan, decisions, observation workers); agent/copilot.py:103-106 (evidence, probe, shadow, strategy.trailing_price); execution/book_sizing.py:27 (optimizer) plus research.setups.covariance; storage/alpha_daily.py:21-23; screeners/earnings_drift.py (apriori pead_live/probe); research.setups.card_stats via copilot, service and execution/card_evidence.py
- cli/commands/alpha.py: 2,208 lines. 37 decorator sites (31 top-level, 6 pooled) plus a lifecycle loop at :469-489 that registers promote/shadow/demote, about 40 commands in all. Sibling files alpha_controls/forecast/liquidity/universe/spread/news/short_volume/insider_filings/corporate_actions add about 784 lines more
- research/alpha/simulation.py:174 simulate_execution, used by miner, promotion, study, replay, forecast_policy, power_trace and lifetime_attribution
- research/setups/labels.py:159 label_bracket (gap fills at open, a both-touch bar counts as the stop), reused by research/pooled/cube.py:289 and research/apriori/pead_study.py:148
- backtest/engine.py:222-232 fills at the stop/target level with no gap-to-open; used by copilot.py:4484, cli/commands/backtest.py:139 and backtest/stress.py:156
- agent/copilot.py:2528 manage_trailing_stops: the native trail is inline at :2569-2572, while alpha policies use research.alpha.strategy.trailing_price at :2573-2576, the same function simulation.py:291 uses
- broker/redundant.py:121-161: submit_entry_order string-matches 'timeout' etc. (:136-145) and resubmits to the fallback once the circuit is OPEN (:150-152, :154-159). close_position (:163-212) also falls over to the fallback on exception
- config.py:502-507 RedundancyConfig.enabled=False; config.py:1063-1067 enables it from the BROKER_REDUNDANCY_ENABLED env var; config/config.yaml has no redundancy block; cli/utils.py:55 forces it off for dry runs
- broker/base.py:303-309: entry_market_context raises NotImplementedError for non-simulated brokers, and RedundantBroker does not override it (method list at redundant.py:34-311). Via execution/entries.py:81 and :125-127, every entry through EntryExecutionService fails closed under RedundantBroker. authoritative_positions is not forwarded either (base.py:359-361), so manual close and panic (copilot.py:2812, :4096) would take the non-authoritative legacy path
- execution/engine.py:126-129: fill_px = res.fill_price or child_slice.limit_price (confirmed). But engine.py:55-59 refuses multi-slice for any non-simulated broker; config/config.yaml:355 ships algorithm 'immediate'; SlicedExecutionEngine is the live entry executor (copilot.py:434, entries.py:41 and :155, immediate branch at engine.py:46-53)
- broker/paper.py:62-65: fill_price = live latest price, else request.entry_price or 100.0, ignoring the limit (confirmed). PaperBroker is the supported EXECUTION_MODE=paper simulator (broker/__init__.py:41-47), not unused
- git tag: research/power-diagnosis-v1-20260918 exists, as do 3 other research/* tags. docs/alpha-power-diagnosis-2026-09-18.md:92-93 and docs/alpha-factor-controls-2026-09-18.md:20 use them to preserve the source revision, but power_study.py, power_trace.py and factor_controls*.py are all still on main

**Bad references**

- research/alpha '66 modules': the 66 is an ls count that includes __pycache__; there are 64 modules plus __init__.py
- agentic_trader/cli/commands/alpha.py '38 commands': grep counts 37 decorator sites plus the loop decorator; the actual total is about 40 commands
- execution/engine.py:126-129 as a 'legacy component that contradicts execution contracts': the engine is the live entry executor, and that branch is gated to simulated brokers at engine.py:55-59
- 'tag-and-retire pattern already exists' (research/power-diagnosis-v1-20260918): the tag exists, but docs/alpha-power-diagnosis-2026-09-18.md:92-93 uses it only to preserve the revision. power_study.py and power_trace.py remain on main, so nothing was retired

**Notes**

The core of the finding holds:
- A large body of CLI-only finished harness code is on main.
- Three divergent bracket walkers exist, and backtest/engine.py has no gap-to-open handling, unlike labels.py.
- RedundantBroker's text-matched POST/close failover contradicts contract 3 ("POST/PATCH/DELETE are never automatically retried").

The overstatements:
- "Dormant" and "mostly" ignore that about 40% of research/ LOC and 34 of the 64 research/alpha modules are live runtime dependencies.
- The slicer is mischaracterized as a legacy component; it is the production executor.
- The north_star_impact phrase "paths that could break no-replay and no-inferred-fill guarantees" is true only for RedundantBroker, and even then only if an operator enables it. Even enabled, entries fail closed on entry_market_context; the live risk is closes and the hidden authoritative_positions.

Additional supporting evidence the finding missed: RedundantBroker.close_position falls over to the fallback on any primary exception or unsuccessful close (redundant.py:182-212), and PaperBroker fabricates a 100.0 fill when no price or limit is available (paper.py:65).

Recommendation adjustments:
- Point 2 should read "remove the multi-slice branch and slicer.py and keep the immediate submit", not "delete the slicer".
- Point 4 should note that simulation.py is the canonical engine for alpha qualification and that registry hashes depend on it. Re-basing it onto labels.py changes alpha execution semantics, which per CLAUDE.md needs new version identities, not a silent swap.

No external references were cited, so no web verification was needed. Everything was read-only. The only file written was the reachability script under the scratchpad holistic-review folder.

### Value review — P3, later (months)

**Reasoning**

This finding mixes one cheap safety fix with a large cleanup that does little for the north star. The north star is better cards, and the binding constraint is an alpha and data source: 64 formula-cohort pairs have produced no finalist and the bracket is closed (CLAUDE.md, docs/alpha-pooled-short-v1-2026-10-20.md, docs/bracket-counterfactual-2026-10-18.md). Fewer lines of code changes no card.

I checked the legacy-component claims against the code. They are overstated for production:

(1) **RedundantBroker** is real but dormant. It is reachable only through config: `create_broker` wraps the primary broker only when `config.redundancy.enabled` is true (broker/__init__.py:63-83). `RedundancyConfig.enabled` defaults to False (config.py:502-503), and config/config.yaml has no `redundancy` block. If enabled, the defaults would be wrong. A primary Alpaca POST that fails with "timeout" text, or raises, would fail over and resubmit on the fallback broker (redundant.py:121-161). The default fallback is `ExecutionMode.PAPER`, the simulator (config.py:504). That violates contract 3 (no automatic POST retry) and the halt-on-unknown-outcome rule. It is a footgun that one config line can arm. It is worth deleting together with `RedundancyConfig`, which is a small change.

(2) **SlicedExecutionEngine** is on the live path: copilot.py:434 constructs it and entries.py:155 calls `execute_order`. But for any non-simulated broker it is a pass-through:
- A single-slice or immediate plan goes straight to `broker.submit_entry_order` (engine.py:45-53).
- A multi-slice plan is refused when `simulated_execution` is false (engine.py:55-59).

The inferred fill at engine.py:126-129 can only run against PaperBroker. So it does not break the Alpaca contract today. It is needless indirection, worth replacing with a direct submit when entries.py is next touched.

(3) **PaperBroker** filling at the latest price (paper.py:62-65) affects only `EXECUTION_MODE=paper`, the simulator. Production runs Alpaca paper. Fixing its fill model has close to zero value. Drop that item.

(4) **The duplicate bracket engines are not dormant.**
- `research/alpha/simulation.py` is used by `promotion.py`, `miner.py`, `study.py` and `replay.py`, which form the registry qualification path.
- `setups/labels.py` feeds `card_stats` and `outcomes`, which drive `card_policy` and `cards outcomes`.
- `backtest/` is used by `TradingCopilot` (copilot.py:32) and two CLI commands.

Re-basing `simulation.py` onto the label kernel changes how promotion is scored. That needs a new validation-policy version and breaks comparability with retained evidence. It belongs in the L5 parity work, and only once a promotion is actually close. Not as cleanup.

(5) **Deleting the research harnesses** (about 32k LOC; 38 commands in cli/commands/alpha.py) mainly cuts agent context and review load. That is real in an AI-maintained repo. But it carries reproducibility and shared-kernel risk, and it would take sprint time away from finding a new alpha source. Do it piece by piece, only for closed lanes whose code nothing live imports, using the existing tag-and-retire pattern (e.g. the `research/power-diagnosis-v1-20260918` tag). Not as a funded workstream.

Overall: P3. The RedundantBroker deletion is the one piece worth doing soon, folded into the next PR.

**Risks**

- **Broken reproducibility.** Result docs and journal manifests are tied to the code revision and to frozen `config/research/*.json` files. Deleting a harness without a tag and a pointer in each result doc leaves past results impossible to re-run or audit.
- **Lost test coverage.** Harness tests also exercise shared kernels (`labels.py`, `cube.py`, `simulation.py`, the daily study service). Deleting them can quietly drop coverage of code that is still live.
- **Changed promotion scoring.** Re-basing `simulation.py` changes how promotion is scored, and old evidence may no longer be comparable. It needs a new validation-policy version.
- **Stale guidance.** CLAUDE.md and the docs cite many retired commands, and dangling references would mislead future agents.
- **Opportunity cost.** An M-sized cleanup takes the sprint away from the binding constraint, which is a new alpha or data source.
- **Removal mistaken for a fix.** Deleting the slicer still leaves the `entries.py` executor seam to rewire. Removing `RedundantBroker` must also remove the `redundancy` config block, so stale YAML fails validation instead of being silently ignored. Earlier, a whole `corporate_actions:` block was silently ignored by `load_config` (CLAUDE.md).

**Prerequisites**

- **Dependency map before any deletion.** For each harness, list the modules it imports and the modules that import it, and confirm nothing reachable from the daemon or scan path depends on it (start from `TradingCopilot`, `cli/commands`, `entries.py`).
- **Tag-and-retire convention.** Add a `research/<lane>-<version>-<date>` tag and record it in the result doc, as `research/power-diagnosis-v1-20260918` already does.
- **Bracket-kernel parity (L5).** Kernel consolidation should wait until L5 defines one bracket/fill contract, and until a promotion candidate actually exists to justify a new validation-policy version.
- **The usual test gate.** The full test suite and pre-commit must pass. Any `TradingCopilot` change (removing the slicer) also needs the deployed restart-and-verify step.

**Better alternative**

1. **One small PR (S), riding on the next feature branch.** Delete `RedundantBroker` and `RedundancyConfig`, so a `redundancy:` YAML key is rejected rather than silently arming POST failover to the simulator. Replace `SlicedExecutionEngine` in `entries.py`/`copilot.py` with a direct `broker.submit_entry_order`, plus a test that live entry submission never infers a fill price. That removes every contract footgun the finding names, at a fraction of the cost.

2. **Drop the PaperBroker fill-model work.** The simulator is not the production path.

3. **Fold the bracket-kernel re-basing into L5 parity.** Defer it until a promotion candidate exists.

4. **Retire harnesses only when a lane closes.** Make tagging and deleting each closed lane's harness and CLI command part of that lane's results PR (e.g. spread v1/v2, the open-gap and sector-panel studies), keeping the docs and manifests. Do not run it as a separate cleanup campaign.

---

<a id="c57"></a>

## C57 — Borrow definitions and test oracles from open-source quant tooling; do not adopt frameworks, and avoid mlfinlab

| Priority | Horizon | Area | Kind | Impact | Effort | Fact check |
| --- | --- | --- | --- | --- | --- | --- |
| P3 | later (months) | architecture | integration | low | S | partially_confirmed |

_lenses: literature_

### Claim (as consolidated)

Current gaps:
- Rolling DSL operators are Python lambdas, about 100x slower than vectorised code.
- Risk uses a single Ledoit-Wolf model.
- The CVXPY allocator has no external oracle.
- Journaled setup features have no factor tear sheets.

Verified tools and licences:
- Qlib (MIT): Alpha158/360, a point-in-time database, LightGBM.
- OSAP: Python signal code.
- skfolio: CombinatorialPurgedCV, WalkForward, RiskBudgeting, HRP.
- Riskfolio-Lib (BSD-3): CVaR and risk contributions.
- alphalens-reloaded (Apache-2.0): fixes only; its successor is ML4T Diagnostic.
- vectorbt: Apache-2.0 plus Commons Clause.
- mlfinlab: all rights reserved and commercial (QuantConnect lists it at GBP 100/month).

### Evidence

- agentic_trader/research/alpha/operators.py:15-19, 138-152
- agentic_trader/research/setups/covariance.py:87; research/alpha/optimizer.py:45-282; risk/book_vol.py:72-111

### Recommendation

- Use Alpha158 and OSAP definitions as reference specs for DSL v2 and for the fidelity tables. Implement vectorised kernels in-house rather than adopting Qlib's data layer, which would bypass the digest-pinned stores.
- Use skfolio's CPCV and WalkForward for fitted rankers.
- Use skfolio and Riskfolio only as test oracles for book_vol extensions.
- Generate IC-by-horizon tear sheets with alphalens-reloaded or ML4T Diagnostic, in a pinned research extra checked on Python 3.14.
- Avoid mlfinlab, and keep vectorbt out of kernels shared with live.

### North-star impact

Speeds up research, keeps definitions matched to published ones and gives the allocation math independent validation, without adding production dependencies.

### References

- Microsoft Qlib (MIT) — Feature definitions, point-in-time design, LightGBM baseline (https://github.com/microsoft/qlib)
- Riskfolio-Lib (BSD-3-Clause) — Risk-contribution test oracles (https://github.com/dcajasn/Riskfolio-Lib)
- alphalens-reloaded (Apache-2.0) — Factor IC and quantile tear sheets (https://github.com/stefan-jansen/alphalens-reloaded)
- QuantConnect: MlFinLab offering — mlfinlab is commercial, not open source (https://www.quantconnect.com/announcements/15575/new-machine-learning-package-mlfinlab/)

### Fact check — partially_confirmed

**Corrected claim**

The core of the finding holds: use outside tooling for reference definitions and test oracles only, and avoid mlfinlab. Four of its gap statements need correcting.

(1) Only six DSL window operators run a Python callable per window through rolling.apply: ts_rank, ts_argmax, ts_argmin, decay_linear, ts_slope and ts_mad (operators.py:15-19, 49-54, 65-67, 138-152). ts_residual inherits the cost through ts_slope. ts_mean, ts_std, ts_sum, ts_max, ts_min, ts_corr and ema are native pandas kernels. The slowdown depends on the operator. ts_slope is about 50-265x slower than a convolution. ts_rank is about 10-14x slower than pandas rolling rank. ts_mad is about 31 ms per 2,520-row series. "About 100x" is therefore order-of-magnitude for some operators and overstated for ts_rank.

(2) Correct: live book-aware sizing uses a single sklearn LedoitWolf covariance (research/setups/covariance.py:80-89, called at execution/book_sizing.py:271). The other entry-risk rules do not use a covariance model.

(3) Correct in substance: the CVXPY allocator has no independent oracle for its optimum. Its tests are property assertions only. The one cross-check in the code (book_sizing.py:423-457) pins every weight and compares the result with the in-house closed form. That verifies the variance arithmetic, not the optimisation.

(4) Partly wrong: journaled and replayed setup features already have rank-IC analysis. research/setups/study.py computes per-feature Spearman IC with a session-block bootstrap and Holm correction (lines 188-219 and 499-506). Its fitted rankers already use purged, embargoed walk-forward folds (lines 289-312 and 567). What is missing is quantile-return, IC-by-horizon and turnover tear sheets, plus CPCV/PBO. Recommending skfolio's WalkForward therefore adds nothing; only its CombinatorialPurgedCV would.

Two recommendations are not new. Alpha158 as the reference operator set, and an Alpha158-lite library, were already recommended in docs/alpha-expansion-survey-2026-09-23.md (lines 37 and 77). vectorbt is already out of the codebase: the optional VectorBT engine was retired (docs/roadmap.md:1153; docs/forecast-contract-hardening.md:54).

The licence facts check out: Qlib is MIT, Riskfolio-Lib BSD-3, alphalens-reloaded Apache-2.0 in fixes-only maintenance with ML4T Diagnostic as successor, and vectorbt Apache-2.0 with the Commons Clause. mlfinlab is commercial; its early 0.0.x releases were BSD-3. The GBP 100 (+VAT) per month figure comes from QuantConnect's docs page, not from the cited announcement.

**Checked**

- agentic_trader/research/alpha/operators.py:15-19 (ts_rank uses rolling.apply with a Python function), :49-54 (ts_argmax/argmin lambdas), :65-67 (decay_linear lambda), :138-145 (ts_slope lambda), :151-152 (ts_mad lambda); :22-41 (ts_corr/std/mean/ema, ts_max/min at :40-46, ts_sum :155-156 are native pandas)
- Local micro-benchmark with pandas 3.0.5 on 2,520 rows (python -I, repo code not imported): ts_slope via rolling.apply is 49-265x slower than np.convolve (d=60..10), ts_rank via apply is 10-14x slower than pandas rolling().rank(), ts_mad takes about 31 ms per series
- agentic_trader/research/setups/covariance.py:80-89 (sklearn LedoitWolf), consumed by agentic_trader/execution/book_sizing.py:28,271; research variants with fixed diagonal shrinkage at research/alpha/portfolio.py:168 and persistent_book.py:265
- agentic_trader/risk/book_vol.py:72-111 (closed-form book_vol_factor)
- agentic_trader/research/alpha/optimizer.py:45-282 (CVXPY + Clarabel ConvexAlphaPortfolioOptimizer)
- tests/research/test_convex_portfolio_optimizer.py:19-135 and tests/research/test_alpha_optimizer_safety.py (property assertions only, with no reference solution)
- agentic_trader/execution/book_sizing.py:395-472 and tests/execution/test_book_sizing.py:290-298 (shadow optimiser with every weight pinned, compared against the closed form)
- agentic_trader/research/setups/study.py:188-219 (spearman_by_session_bootstrap), :289-312 (purged_walk_forward with embargo), :499-506 (H1/H2 per-feature IC with Holm), :567 (fitted rankers use purged walk-forward)
- agentic_trader/research/alpha/information.py (cross-sectional Rank IC with HAC for the panel lane)
- pyproject.toml:32-33 (cvxpy and scikit-learn are dependencies; skfolio, riskfolio, alphalens, qlib and vectorbt are not)
- docs/alpha-expansion-survey-2026-09-23.md:37,77 (Alpha158 already named as the operator reference set; an 'Alpha158-lite feature library' is already recommended)
- docs/alpha-information-coefficient.md:13-14 (the IC convention already follows alphalens-reloaded)
- docs/roadmap.md:1153 and docs/forecast-contract-hardening.md:54 (the optional VectorBT engine is already retired)
- https://github.com/microsoft/qlib (MIT; Alpha158/Alpha360; PIT database; LightGBM)
- https://github.com/dcajasn/Riskfolio-Lib (BSD-3-Clause; CVaR; risk contributions)
- https://github.com/stefan-jansen/alphalens-reloaded (Apache-2.0; bug, security and compatibility fixes only; new development is in ML4T Diagnostic)
- https://www.quantconnect.com/announcements/15575/new-machine-learning-package-mlfinlab/ (says 'first commercial library'; gives no price)
- quantconnect.com/docs/v2/drafts/mlfinlab via search (GBP 100 + VAT per month per user)
- https://github.com/polakowo/vectorbt (Apache 2.0 with Commons Clause)
- OSAP CrossSection repo via search (Signals/pyCode Python pipeline exists)

**Bad references**

- QuantConnect: MlFinLab offering (https://www.quantconnect.com/announcements/15575/new-machine-learning-package-mlfinlab/). It supports 'commercial' (the page calls it 'the first commercial library added by QuantConnect') but gives no price. The 'GBP 100/month' figure appears only on quantconnect.com/docs/v2/drafts/mlfinlab (GBP 100 + VAT per month per user).
- Evidence 'agentic_trader/research/alpha/operators.py:15-19, 138-152' supports 'some operators use Python callables', not 'rolling DSL operators are Python lambdas'. The same file has seven native vectorised window operators (lines 22-46 and 155-156).

**Notes**

The overall direction is sound and low-risk: borrow definitions and oracles, keep the digest-pinned stores, add no production dependencies, avoid mlfinlab. The weaknesses are precision and novelty.

- Rolling operators: the "Python lambdas, about 100x" claim overgeneralises. A quick local benchmark (pandas 3.0.5, 2,520 rows) measured roughly 50-265x for ts_slope against a convolution and only 10-14x for ts_rank against pandas native rolling rank. An in-house fix is cheap: convolution for decay_linear and ts_slope, rolling().rank() for ts_rank.
- Setup features: the "no factor tear sheets" gap ignores the existing per-feature Spearman IC with session-block bootstrap, Holm correction and purged walk-forward folds in research/setups/study.py.
- CVXPY allocator: "no external oracle" is fair. The pinned-weight shadow check in book_sizing only confirms w'Σw against the closed form.
- Already on record:
  - The Alpha158 reference set is in the 2026-09-23 expansion survey.
  - Alphalens is already the IC convention in docs/alpha-information-coefficient.md.
  - vectorbt was already retired.
- Licences:
  - Qlib is MIT; Riskfolio-Lib is BSD-3.
  - alphalens-reloaded is Apache-2.0, maintained for fixes only, and points to ML4T Diagnostic as its successor.
  - vectorbt is Apache 2.0 with the Commons Clause.
  - OSAP has a Python signal pipeline (Signals/pyCode).
  - mlfinlab: early PyPI releases were BSD-3 and current access is commercial. I could not find Hudson & Thames' own current licence text; "all rights reserved" appears only in a fork's description.
- skfolio's CombinatorialPurgedCV, WalkForward, RiskBudgeting and HierarchicalRiskParity are real classes. Its licence is not stated in the finding.
- Python 3.14 compatibility of these research extras was not checked. The finding rightly says it must be.

### Value review — P3, later (months)

**Reasoning**

Most of this is hygiene, and none of it addresses why the system has no edge. Taking the parts in turn:

1. **Operator speed is real but does not limit anything.** The kernels are slow: `ts_rank`, `ts_slope` and `ts_mad` run as `rolling().apply` lambdas (agentic_trader/research/alpha/operators.py:15-19, 138-152). But each pooled lane is a fixed set of eight formulas, and the genetic search is parked. The time goes elsewhere: the cohort-v3 build spent about 11 hours fetching hourly bars sequentially and about 2 hours building the cube, against about 3 hours of replicates per protocol (docs/alpha-pooled-cohort3-2026-10-16.md, "Building the cube" and "Operational notes"). The limit is a lack of new information, not compute. The 64 null pairs did not fail because the lanes were slow.

2. **"Use Alpha158/OSAP as reference specs" is mostly done already.** docs/alpha-expansion-survey-2026-09-23.md:37 names Alpha101 and Alpha158 as the reference operator sets, and docs/alpha-information-coefficient.md:13-14 already takes its IC definitions from alphalens source. More price/volume features from Alpha158 are exactly the kind of formula that has failed 64 times on this cube.

3. **A Riskfolio/skfolio oracle for book_vol is overkill.** `book_vol_factor` solves a scalar quadratic in closed form, for the largest f with vol(f) <= budget (agentic_trader/risk/book_vol.py:72-111). A hand-computed numpy test is a complete oracle for it. The CVXPY allocator (research/alpha/optimizer.py) is shadow-only by contract and never reaches orders. Validating it changes no card.

4. **skfolio CPCV/WalkForward has nothing to cross-validate yet.** No fitted ranker exists in production: `setup_quality` is explicitly not validated alpha. It only becomes relevant if a LightGBM/Ridge card ranker is funded, and then it belongs inside that workstream.

5. **The licence warnings are correct but nearly free to act on.** vectorbt is already retired (docs/development-notes.md:280), and mlfinlab is not a dependency. This amounts to one line in docs/research-data-sources.md, not a project.

Net effect on the north star (steady, high-quality cards): close to zero. Worth doing only as small fold-ins to workstreams that justify themselves on other grounds.

**Risks**

- **Shared kernels touch live scores.** Research and live share scores through dsl.py. Vectorised `ts_rank` (tie-averaging), `ts_mad` or `ts_slope` that differ even at float precision could change live formulaic or probe scores. They would also break bit-exact reproduction of journaled campaign results and digest-pinned evidence. Any rewrite needs exact-equality (or a declared-tolerance) tests against the current lambdas, NaN/min_periods behaviour included.
- **Dependency risk on Python 3.14.** alphalens-reloaded, skfolio and Riskfolio pull in heavy dependency trees (empyrical, matplotlib/plotly, cvxpy pins), and wheels for 3.14 may lag. A research extra could drift and break `uv sync` or pre-commit for the whole repo.
- **Alpha158 as a "spec" invites more of the same.** Turning it into a new DSL v2 feature library spends confirmation windows and trial budget on price/volume transforms. This cube has already rejected that family repeatedly.
- **External oracles can give false comfort.** Agreement between two convex solvers does not validate the covariance inputs (a single Ledoit-Wolf model, research/setups/covariance.py:87), and that is where the real model risk sits.

**Prerequisites**

- **Vectorising:** a funded workstream that actually evaluates many formulas per campaign (a reopened genetic search with a new spec and operator approval, or a large DSL v2), plus golden-value equality tests that freeze the current operator outputs first.
- **CPCV:** a fitted card ranker approved for development.
- **Tear sheets:** a research extra verified to resolve on Python 3.14, isolated from the runtime dependency set.
- **Riskfolio/skfolio oracles:** a decision to move the shadow allocator toward sizing authority. No such decision exists, and none should be made until there is something to allocate (docs/alpha-persistent-book-2026-09-17.md: the allocation mechanics work, but every arm's IC is about zero).

**Better alternative**

- Do not fund this as its own item. Add one paragraph to docs/research-data-sources.md recording the licence verdicts: mlfinlab is proprietary, vectorbt carries a Commons Clause, Qlib/alphalens-reloaded/Riskfolio are permissive.
- If replicate time ever blocks a campaign, rewrite only the three slow kernels in plain numpy, with bit-equality tests against the existing lambdas: `ts_slope` from rolling sums of x and t·x (exact and O(n)), `ts_mad`/`ts_rank` over `sliding_window_view`. That costs about a day and adds no dependency.
- Spend the actual effort on the binding constraint, which is new information on the entry side: a non-price data source, or point-in-time fundamentals, which is the useful part of OSAP's signal list. Do not spend it on more price-feature definitions or allocator validation.
