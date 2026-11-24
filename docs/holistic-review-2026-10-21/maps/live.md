# Live production evidence (Alpaca paper desk, 2026-09-14 → 2026-10-21)

Source: production PostgreSQL `agentic_trader`, read-only (`default_transaction_read_only=on`), scope `production/alpaca:paper`; code at worktree `db82817`. Queries are numbered **Q1–Q16** (appendix). Timestamps converted to New York unless marked UTC. No account or chat identifiers are reproduced.

## 1. Purpose and scope

This map shows what the running desk actually produces: how many cards it sends, which setups they come from, what the LLM gate and the risk layers do to them, what the operator takes, and whether anything measured live shows edge. It also covers the prospective evidence lanes that run inside the daemon (shadow alphas, the PEAD probe, the daily panel), because those are the only path by which a mined alpha could ever reach a card.

## 2. Components and contracts (live path)

| Stage | Code | Contract as it runs today |
| --- | --- | --- |
| Scheduling | `cli/commands/service.py:437` (`make_suggestion_scan`), `:581-592` (`register_swing_scan`) | Suggestion scans at 10:35 and 14:35 ET (`config/config.yaml:331`). A swing scan runs **at daemon start** (`next_run_time=datetime.now(UTC)`) and then every 4 h (`config.yaml:330`), with `budget=ScanBudget.FULL`, so it spends the same session card budget. |
| Bars | `data/market_data.py:119-138` (`resample_to_4h`), `config.py:925,1043` (feed default `iex`) | 4h bars come from `df_1h.resample("4h")` on a UTC index, so the buckets are 08:00–12:00 and 12:00–16:00 ET while EDT is in force. Every card's `candle_timestamp` is 12:00 or 16:00 UTC (Q14). The live feed is IEX, while the base rates were measured on SIP (`docs/setup-outcomes-2026-09-23.md` "Protocol and run"). |
| Setup detection | `screeners/strategies.py:53-215` | Trend pullback: daily close > EMA50 > EMA200, the 4h RSI dips below 45 and recovers to at least 40 within 0.5 ATR of the 4h EMA20. Shorts are disabled (`config.yaml:305,320`). `setup_quality` is a hand-written heuristic (`strategies.py:29-43`). |
| Admission (collect) | `agent/copilot.py:1679-1770` (`_admit_candidate`) | Applies the 12 h dedup against the signals table (`:1723`; `config.yaml:276`), account risk, and a deterministic `evaluate_candidate(use_llm=False)`. Rejections are **only logged** (`:1756`). |
| Bracket | `agent/evaluator.py:210-222` | The stop is max(swing low − 2 ticks, 1.5 ATR). The target is exactly `required_rr` × risk, which is 2.0 in every journaled regime (Q12). |
| Rank/send | `copilot.py:1174-1177`, `:1284`, `:1380` | Candidates are sorted by `-setup_quality`. Budgets are 1 card per scan, 2 per session and 1 per correlation group (`config.yaml:443-445`). There are 4 LLM evaluations per scan; a veto becomes `RankedOutcome.LLM_VETOED` (`execution/durable.py:80-91`). |
| Correlation cap | `copilot.py:604-617`, `:1286-1290` | Groups are the configured universe **sector labels**. A dynamic name gets only the `dynamic` group. |
| Journal | `copilot.py:1591` (condition), `:1851-1931` (writer) | One `scan_candidates_ranked` event per unrestricted, budgeted, non-dry scan, and only when `native_count > 0`. It holds admitted candidates with shadow features, `llm`, `news`, `card_policy` and `book_sizing`, plus a top-level `regime`. |
| Evidence/policy | `research/setups/card_stats.py:43-117`, `execution/card_evidence.py:76`, `execution/card_policy.py:37-55` | The daily snapshot holds per-(strategy, direction) `mean_r_cost`, rates and `n_mature`. It has **no market-excess field and no setup de-duplication**. `card_policy` withholds when `mean_r_cost < min_measured_ev`; it ships `off` (`config.yaml:454`). |
| Book sizing | `risk/book_vol.py:72`, `execution/book_sizing.py:144` | `enforce` since 2026-10-20 (`config.yaml:466`). |
| Tap | `execution/freshness.py:131-213` | A fresh tap (≤1800 s and ≤0.25R) submits the original bracket. A later tap re-prices only if RR ≥ 2 at the current price. |
| Digest | `copilot.py:4293-4340` | `scan-digest/{date}` message with raw candidate counts. It is held in memory, so a restart truncates it (`docs/production.md` "Suggestion scans"). |
| Probe/shadow lanes | `research/alpha/clock.py:47-50,76-79`, `research/alpha/probe.py:99-117`, `research/alpha/daily_forecasts.py:551,574-577` | These are fail-closed rules on data contract, bar staleness, minute coverage and label completeness. |

Seams. The live path consumes the universe config, the Alpaca IEX bars and the dynamic screener, the regime/macro detector, the ledger's account risk, the registry snapshot (active/probe) and the `card_stats` snapshot. It produces `signals` rows, outbox notifications, `scan_candidates_ranked`, `pead_decision` and `dynamic_universe_built` events, and the inputs that `cards outcomes` and `card_stats` label.

## 3. Data flow (observed)

1. A scan fetches about 159 static and about 20 dynamic names. The digest reports 517–676 name-scans per day, and each scan takes 180–250 s (Q8), so 10:35 cards land at about 10:39.
2. Raw setups: 105 over 19 digest days, about 5.5 per day, or 1.2% of 8,530 name-scans (Q8).
3. Admitted and journaled: 87 rows in 31 events over 18 days. These are 41 distinct setups in 41 symbols: 83 TREND_PULLBACK LONG 4h rows and 4 SQUEEZE_BREAKOUT LONG rows (Q5, Q6). Outcomes: 37 "per-scan budget spent", 26 "per-session budget spent", 21 sent and 3 correlation-group blocks (Q7).
4. Sent: 42 alpaca:paper cards over 28 sessions (1.5 per session); 41 cards over 24 sessions since 2026-09-18 (1.7 per session). The mix is 37 TREND_PULLBACK (1 short, TLT on 9-18, before shorts were disabled), 1 SQUEEZE_BREAKOUT and 4 `pead_long`. Only 3 sessions had no card (Q2, Q3).
5. The operator tapped 9 cards: 7 tap assessments since 2026-09-24, plus XOM and CRM on 9-23, which predate the assessment journal (Q10). Of those, 3 missed on re-price, 1 timed out ("Tap-time checks timed out after 15s", MRK 10-14), and five entry work items followed: 3 accepted and 2 rejected for system reasons, both in September. Thirty-three cards expired untapped (Q9).
6. Closed trades: SPY on 9-14 (−$86, about −0.43R on `risk_dollars`), XOM (+$513, +2.37R, target after 13 sessions and 29 trailing-stop notices) and VRNS (−$264, −0.95R, stop). BLK `pead_long` is still open at −$9 (Q4, Q11, Q13).
7. Account: adjusted equity went from 102,083.69 (baseline 2026-09-18) to 102,323.47 (2026-10-21), a gain of $240 (+0.23%). The high-water mark is 102,833.94 and drawdown is 0.50% (Q11). Over the same span SPY rose from 761.62 to 771.02 (+1.2%) on the observer's 15m IEX closes (Q15). The book was mostly cash, so the two percentages are not a like-for-like comparison.

## 4. Evidence of what was tried and how it went

- **Replay base rates.** Trend-pullback 4h long earns +0.03R after cost over 2021–2026 development (n 7,457). The one-shot holdout loses money for every scorer. `setup_quality` has ρ +0.016 in development and −0.012 in the holdout (`docs/setup-outcomes-2026-09-23.md` "Base rates", "Selection"). The live bracket earns +0.039R after cost but **−0.110R versus SPY** over its own holding windows. A rising tape doubles the raw mean and leaves the excess unchanged (`docs/bracket-counterfactual-2026-10-18.md` "Descriptives").
- **Live record as the cards show it** (`card_stats`, Q1):

  | snapshot | n_mature (TP long) | target / stop | mean R after cost |
  | --- | ---: | --- | ---: |
  | 2026-10-08 | 34 | 23.5% / 76.5% | −0.376 |
  | 2026-10-13 | 40 | 32.5% / 67.5% | −0.102 |
  | 2026-10-16 | 64 | 51.6% / 48.4% | +0.467 |
  | 2026-10-21 | 67 | 53.7% / 46.3% | **+0.535** |

  The population grew from 57 to 86 rows, while mature rows grew from 35 to 69. The 34 newly matured rows resolved +28 targets and +6 stops. This is informative censoring: on a 2R bracket, stops resolve fast and targets slowly, so early snapshots over-weight stops. The 2026-10-07 headline of "runners-up −0.40R" (`docs/alpha-roadmap.md:635-637`) was an artefact of the same bias. The +0.54R now shown is not evidence of edge either:
  - It counts repeats; 87 rows cover only 41 setups (Q6).
  - It is raw R. Every candidate saw SPY above its 200-day average and VIX NORMAL at 15.8–18.2 (Q12).
  - It cannot be reconciled with the 8,768-setup history (34.9% target rate) except as a favourable 4-week single-regime window. The October 13 live review recorded sent cards +0.24R against a +0.21R SPY move (`docs/alpha-roadmap.md` "Status (October 14)").
- **LLM gate.** Of 14 journaled LLM evaluations, 14 approved; 0 `llm_vetoed` events exist (Q7). Twelve of the fourteen bracket edits move stop and target by ≤0.012R. Two are large tightenings: SPY (stop +0.43R, target −0.86R) and CEG (+0.73R, −1.46R) (Q16). Earlier vetoes came from a prompt bug (`docs/llm-prompt-eval-2026-09-22.md` Part 1).
- **Shadow alpha lane.** It is fully blind. The registry has 0 active, 16 shadow and 1 probe alpha (Q13). All 10,359 shadow forecasts are invalid: 6,915 `deployment_data_contract_mismatch` and 3,444 `stale_closed_bar` (`clock.py:47-50,76-79`). Of the session decisions, 3,559 are `unavailable`, mostly `SessionCoverageError` with 2–5 missing IEX minutes, 222 are `missed`, and **none scored** (Q13). The roadmap already defers a sparse-layout contract (`docs/alpha-roadmap.md:667`).
- **PEAD catalog probe** (`apriori:pead:v2:long`). It was enrolled on 2026-09-28 and expires on 2026-10-28, with kill at −4R and $100 risk per trade (Q13). It has sent 4 cards (one per eligible session since 10-15), 1 has been executed (BLK) and 0 have closed. Liquidity filters reject most events, for example 23 of 30 rows on 10-21 failed the volume screen (Q9b). Realized results are "not comparable until … at least 10 closed trades" (`docs/apriori-alphas.md` "Part 2").
- **Prospective daily panel.** The first outcome is for decision 2026-09-22. It came back `unavailable` (`source_qualified_labels_unavailable`): 63 of 64 labels were observed, but one missing symbol (ATKR) sets every arm's IC to NaN (`daily_forecasts.py:551`), giving `ic_pairs 0` and `insufficient_breadth` (Q13b).

## 5. Strengths worth preserving

- **Complete provenance on every card.** Regime, news, book sizing, card evidence, LLM verdict, shadow features and the account-risk fingerprint are all recorded (Q4), and each journaled scan carries the full ranked list. The analysis in this map was possible only because of this.
- **Honest labelling of measured records.** The not-validated caveat is shown, and `card_policy` cannot fail closed on partial evidence (`card_policy.py:43-55`).
- **Pre-registered research that killed weak ideas fast.** Recurrence, the bracket test, the spread lane and the pooled lanes all followed this pattern. The live sample should be kept to the same standard.
- **A mature broker evidence chain.** Fills come from order projections. VRNS shows a stop at 45.92 filled at 46.01 (Q11), and nothing infers price from the request.

## 6. Gaps, weaknesses, risks, inconsistencies

1. **The measured record shown on cards is biased in three ways and swings sign.** `CardKeyStats` has no excess return or setup de-duplication (`card_stats.py:43-62,112`), and immature rows are dropped (`:91-92`). With `card_policy` at threshold 0 under `enforce`, TREND_PULLBACK would have been withheld 10-08 → 10-13 and sent from 10-14 on. That makes the policy a lagging switch on a censored, raw-beta sample (Q1). The roadmap names `card_policy` as "the construction-side control" (`docs/alpha-roadmap.md` "Status (October 10)"), yet it is still `off` (`config.yaml:454`).
2. **Two denominators are invisible.**
   - Zero-candidate scans write no journal event (`copilot.py:1591` requires `native_count`). Twelve of the 39 scheduled slots from 2026-09-24 to 2026-10-21 (10:35) have no event (Q5 versus the schedule). The digests show 0 raw candidates on 10-12 and 10-14, and on 10-16 only the PEAD card (Q8); the 9-28 10:35 slot was dropped by host sleep (`docs/production.md` "Late and missed slots").
   - Risk-engine rejections are not journaled (`copilot.py:1756`). Earnings blackout (7 days, `config.yaml:279`), corporate-action blocks and caps cannot be measured as funnel losses.

   About 105 raw candidates (Q8) against 87 journaled rows shows the gap.
3. **Swing scans spend the card budget at times set by restarts.** Fourteen of 37 native cards were sent outside the two scheduled slots, at 09:37–13:43 ET, by swing scans or Re-evaluate (Q3; the one late 10:35 run is excluded). Scan times have been 10:35 and 14:35 since `a1d97ad` (2026-09-22). There were 61 daemon starts in 33 days, many during the session; one was at 10:50 ET today (Q16b). A start fires an immediate full-budget scan (`service.py:588,592`). As a result the 14:35 slot produced only 6 cards in 24 sessions, and "per-session budget spent" appears 26 times (Q7). A 09:37 card evaluates a 4h bar that is mostly pre-market.
4. **Correlation caps are sector labels, so the same exposure gets carded twice.** On 2026-10-20 SPY was blocked twice by "correlation group already has a card", but **VOO**, a dynamic name with only the `dynamic` group, was sent at 14:38 after VTI at 10:39. SPY had also been carded on 10-19 (Q3, Q7; `copilot.py:614-617`). Broad-index ETFs (SPY ×6 rows, VTI ×4, VOO ×4; Q6) are pure beta, so their excess return over SPY is about zero by construction.
5. **The LLM gate selects nothing.** The approval rate is 14 of 14 (Q7), yet it adds 15–26 s per call (`docs/llm-prompt-eval-2026-09-22.md` Part 1). When it does make a material bracket change (SPY, CEG), `card_stats` labels the deterministic bracket instead (`docs/card-evidence.md` "Limits / Proxy bracket"). The sent card and the measured card differ.
6. **The promotion path cannot be reached on the current deployment feed.** No shadow forecast and no session decision has ever been valid (Q13). The registry's requirement of 20 shadow dates and 10 decisions (CLAUDE.md alpha pipeline) can never be met while the alphas' declared feed/adjustment differs from the desk's IEX raw frames (`clock.py:76-79`) and coverage is all-or-nothing.
7. **Probe evidence depends on manual taps and will not mature in time.** One fill in four probe cards (Q10), a 20-session hold, and a 30-day term ending 2026-10-28 (Q13) mean the probe expires with 0 closed trades. Reaching the 10-trade comparability floor would take months at this tap rate.
8. **All-or-nothing label completeness voids prospective IC.** One missing symbol out of 64 empties the cross-section (`daily_forecasts.py:551,574-577`). If this repeats, the daily panel will be as blind as the session lane.
9. **The operator is not acting on cards.** Nine cards tapped out of 42 and 3 fills; 0 of the 6 cards from the 14:35 slot were tapped (Q3, Q10). Telegram carried 659 incident notices, 33 expiry notices and 62 trailing-stop notices (29 for XOM alone) against 41 signal cards (Q9). The cards are buried in operational traffic.
10. **The bar clock is UTC-anchored and the feed differs from the research feed.** The 4h buckets shift when US DST ends on 2026-11-01 (12:00 UTC becomes 07:00 EST), which changes what "the 4h bar" means for every live card (`market_data.py:119-138`). The live feed is IEX while the replay base rates are SIP (`config.py:925`).
11. **The paper fill model is lenient.** The VRNS stop filled 9 cents better than the stop price (Q11). Paper P&L understates real implementation cost; the sample of one is anecdotal.
12. **The ranking key is known to be uninformative.** `setup_quality` decides which of several candidates gets the single per-scan card (`copilot.py:1176`). The shadow `score` is null on all rows because no ranker artifact is deployed. The shadow features exist on 61 of 76 suggestion rows and on none of the 11 swing rows; dynamic names lack `sector_rel_mom_60` (Q16c).

## 7. Improvement opportunities

1. **Rebuild `card_stats` as an honest estimator before any policy acts on it** (gap 1).
   - Report excess R against SPY, or against a beta-matched sector ETF; the labeller already computes `excess_r` (`research/setups/outcomes.py:382-383`).
   - De-duplicate to one row per (contract, strategy, direction, session).
   - Replace "mature rows only" with competing-risks cumulative incidence (Aalen–Johansen; the `lifelines` package), which removes the censoring swing seen in Q1.
   - Shrink toward the replay prior: +0.04R raw and −0.11R excess on 8,768 setups (`docs/bracket-counterfactual-2026-10-18.md`), for example a beta-binomial on the target rate.
   - If the policy ever enforces on daily peeks, use always-valid confidence sequences (Howard, Ramdas et al. 2021; the `confseq` package).

   Expected effect: the card line stops swinging from −0.38R to +0.54R in eight days and `card_policy` becomes safe to move to `preview`.
2. **Journal every scheduled scan, including empty ones, with rejection counts per rule** (gap 2). Expected effect: candidate supply and the cost of each risk rule become measurable, which is needed before widening the universe or the DSL to address "only 1 candidate".
3. **Make the swing scan evidence-only, with no card budget, or restrict sending to the two scheduled slots; remove the send on daemon start** (gap 3). Expected effect: 14:35 supply roughly doubles. In 7 of the 13 journaled 14:35 scans, every candidate was refused as "per-session budget spent", and each time a swing card had taken the second slot (Q3, Q5). Card timing also becomes predictable for the operator, and restarts stop sending cards.
4. **Replace sector-label groups with return-correlation dedup, and exclude broad-index ETFs from native cards** (gap 4). Use the book sizer's per-scan Ledoit–Wolf covariance, for example by refusing a second card with ρ > 0.8 to any card sent that session. Expected effect: the session's two cards carry two distinct bets, and no slots go to SPY clones whose excess return is zero.
5. **Make the LLM commentary-only for native cards, as PEAD already is, or label the LLM-edited bracket as a second arm in `cards outcomes`** (gap 5). Expected effect: lower latency and cost with no loss of selection, and the edits get measured instead of hidden.
6. **Unblock prospective evidence** (gaps 6 and 8).
   - Ship the deferred sparse-layout rule: an IEX minute with no prints is "no trade", not missing data, with a bounded maximum gap. Alternatively, price SIP real-time entitlement (`docs/research-data-sources.md`).
   - Retire or re-declare the 16 shadow alphas whose data contract can never match.
   - Pre-register a missing-label rule for the daily panel, for example delisting-return imputation in CRSP style, or a cap of k missing names with a reason code.

   Expected effect: the only route from mining to cards starts producing data.
7. **Decide the PEAD probe term now, before 2026-10-28** (gap 7). Rely on the counterfactual label of every eligible event (`research/apriori/probe_outcomes.py`) rather than on taps. Consider an operator-approved contract change that auto-enters probe cards on paper. Expected effect: evidence accrues on every eligible event instead of on the roughly one card in four that gets tapped.
8. **Separate the operational Telegram channel from cards, and batch trailing-stop and expiry notices into the digest** (gap 9). This keeps the "every confirmed ratchet" rule (CLAUDE.md contract 3) as a durable record while lowering its Telegram salience. Expected effect: cards get read, which tests whether they are "reasonable" to a human.
9. **Anchor 4h bars to the session (09:30/13:30 ET or 10:30-aligned), with the replay pinned to match** (gap 10). This needs a new frozen base-rate replay, because historical identities must not be reinterpreted.

## 8. Open questions

- Twelve of 39 scheduled slots in the journal window have no `scan_candidates_ranked` event. Without an empty-scan journal I cannot tell how many were "0 admitted" and how many were "not run" (sleep, restart or lock).
- What did the risk engine reject? Rejection reasons live only in logs, which I did not read.
- What are the per-row excess returns of the 87 journaled candidates? `card_stats` stores aggregates only, and `cards outcomes` (a CLI command) was out of bounds, so I could not test +0.54R against SPY or de-duplicate it.
- Why did the 2026-09-22 daily-panel outcome give `ic_pairs 0` when 63 labels were observed? The code (`daily_forecasts.py:551`) explains the NaNing; whether this is a one-off depends on how often a symbol is missing from IEX daily labels.
- Is the 15–26 s LLM latency recorded in September (gpt-5.6) still representative?

## Appendix: queries (psql, read-only)

- **Q1** `select payload::jsonb->>'window_end', k->>'strategy', k->>'direction', k->>'n_mature', k->>'n_immature', k->>'target_rate', k->>'stop_rate', k->>'mean_r_cost' from domain_events, jsonb_array_elements(payload::jsonb->'keys') k where kind='card_stats_snapshot' order by id;` plus `rows_labelled`/`events_considered` per snapshot.
- **Q2** `select strategy, status, count(*) from signals where execution_mode='alpaca:paper' and not is_quarantined and timestamp>='2026-09-09' group by 1,2;`
- **Q3** Per-session and per-slot card lists: `string_agg(contract||status)` grouped by the NY date, and slot classification by NY time (10:35–10:55, 14:35–14:55, other).
- **Q4** `decision_provenance::jsonb` for ids 16, 42, 47, 53 (keys only for the others).
- **Q5** `select id, recorded_at, payload->>'trigger', jsonb_array_length(payload->'candidates'), outcome counts from domain_events where kind='scan_candidates_ranked';`
- **Q6** Distinct `(contract, strategy, direction)` and setup-days over `jsonb_array_elements(payload->'candidates')`; per-symbol repeats.
- **Q7** Candidate `llm.applied/approved × outcome` counts; `count(*) where payload like '%llm_vetoed%'` = 0.
- **Q8** `select payload::jsonb->'arguments'->>'text' from work_items where dedup_key like 'scan-digest/%';`
- **Q9** Notification counts by `payload->>'kind'` and dedup-key prefix (`work_items`, kind `notification`); Q9b `pead_decision` counts/reasons.
- **Q10** `kind in ('card_tap_assessed','card_reevaluate_requested','entry_resolved')` payloads; `work_items` entry statuses.
- **Q11** Latest `account/ledger` checkpoint: `payload->'risk'` (equity, baseline, HWM, drawdown) and `payload->'report'` realized/unrealized; `order_projections` filled `average_fill_price`.
- **Q12** `regime` blocks across journaled scans; `shadow.features.spy_above_200` distribution.
- **Q13** `alpha_projections` key `registry` (array lengths), `probe/%`, `forecast/%` grouped by `valid, reason`, `session-decision/%` grouped by `status, error_type`; Q13b the daily-outcome artifact `outcome.json` (arm `ic_reason`, label statuses).
- **Q14** `decision_provenance->>'candle_timestamp'` distribution.
- **Q15** SPY first/last 15m close per NY date from `stream='alpha/observation/latest/SPY'`.
- **Q16** LLM versus deterministic stop/target shift in R per journaled candidate; Q16b `min(recorded_at)` per `health/<run>/worker` stream since 2026-09-18 (61 runs); Q16c shadow feature coverage by trigger.
