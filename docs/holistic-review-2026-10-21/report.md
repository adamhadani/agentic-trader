# Holistic review: signals, mining, risk, sizing and roadmap

**Revision 2, 2026-10-21.** Code reviewed read-only at main `db82817` in the worktree
`agentic-trader-intraday`, which is identical to production. Paths are relative to the repo root.

**Conventions**
- **Now** means what the code does today, cited as `file:line`.
- **Recommend** means a proposal.
- Past results cite `docs/<file>.md` and the section.
- Live production figures come from a read-only query of the production journal (scope
  `production/alpaca:paper`). They are cited as `maps/live.md` with the query number Q1–Q16. All
  `maps/*` files are in `maps/`.
- **Measured in review** marks a figure computed read-only from frozen research frames, caches or
  the journal. The script for each is listed in Appendix B.
- **[added in revision]** marks material from the gap review. **[revised]** marks a statement that
  a critic correction changed. Appendix A lists every correction and how it was checked.

The north star, in the operator's words: *"get great signals, alpha mining etc. so we're getting
steady stream of high quality suggested cards in telegram, complete with appropriate risk controls
and portfolio allocation/optimization decisions based on existing positions"*, and *"expand our
alpha mining operation by enhancing DSL, universe of symbols, making sure it runs on a reasonable
cadence"*.

---

## 0. Executive summary

1. **The plumbing is strong, but the problems are upstream of it.** Execution, broker evidence,
   the durable FIFO, the outbox and the pure `risk/` rules are well built (§6). What limits card
   quality is what gets selected and how it is measured, plus a few risk inputs that fail open
   silently.
2. **The cards that are sent are not the cards that are measured.**
   - From 2026-09-24 to 2026-10-21 the desk sent 33 native cards. 14 of them (42%) did not come
     from the 10:35 or 14:35 slots, and 12 were never journaled. Since PR A.1 the figure is 2 of 9.
   - A daemon start fires an immediate full-budget swing scan (`agentic_trader/cli/commands/service.py:581-593`),
     and there were 61 daemon starts in 33 days (maps/live.md Q16b).
   - Three of the last nine native cards were broad-index beta: SPY, VTI and VOO (§3.1).
     **[added in revision]**
3. **The evidence on native edge is weaker, and depends more on the yardstick, than the docs say.**
   **[revised]**
   - The "excess over SPY" control scales SPY's return by entry/stop, which is a beta-one control
     (`agentic_trader/research/setups/outcomes.py:187-221`).
   - With per-stop-width betas the primary-window gap shrinks from −0.109R to −0.062R.
   - In the robustness window, dropping the 4% of setups on bond ETFs moves the headline −0.103R to
     −0.006R.
   - The live `card_stats` record swung from −0.376R to +0.535R in eight days. Censoring and
     repeated rows drove that swing (§2.2).
   - So the live record is uninformative so far, and the replay suggests a small negative excess
     return.
4. **There is no tested replacement for selection.**
   - `setup_quality` carries no information (`docs/setup-outcomes-2026-09-23.md` "Hypotheses").
   - The three replacement keys proposed in the maps point in opposite directions.
   - Most of `vol_20`'s measured edge is the cost-in-R arithmetic of wide stops.
   - A meta-label ranker on these features has already failed its holdout: −0.293R against −0.126R
     ("Selection (H3)"). **[revised]**
5. **Allocation is nominal because the book is empty.**
   - Entry happens only on a tap (`agentic_trader/agent/copilot.py:466`; `cli/commands/trade.py:94`).
     There were 9 taps on 42 cards and 3 accepted entries in about 24 sessions (maps/live.md Q10).
   - Every `book_sizing` factor so far has been 1.00 (`docs/card-evidence.md:232`).
   - The $7,500 notional cap binds on 83% of replayed 4h setups, so the drawdown and macro haircuts
     do nothing (§3.6).
6. **Risk inputs fail open silently.** **[revised, added in revision]**
   - If the ForexFactory fetch fails, *every* macro-lockout gate (scan, evaluator, tap and admission)
     passes for 30 minutes (`agentic_trader/agent/calendar.py:107-125,176-221`). The next FOMC is
     2026-10-28.
   - If any one of ten macro inputs is missing, the risk multiplier resets to 1.0×
     (`agentic_trader/agent/regime.py:146-151,185-189`).
   - If VIX is unavailable, the scan aborts with no journal entry and no digest
     (`copilot.py:780`; `service.py:440-461`).
7. **Mining has been rigorous and powerless.**
   - 64 formula–cohort pairs produced no finalist. Their t-statistics are statistically
     indistinguishable from the calibrated null: mean −0.06, sd 1.25 (maps/pooled.md §4).
   - The lane detects about 0.065R, while plausible liquid-name effects are about 0.02R.
   - Its control is not matched for risk, its cohorts carry survivorship, and every formula uses one
     label horizon.
   - The single lane-wide confirmation window is the desk's most valuable research asset. It has no
     spending rule, and the insider campaigns are about to be approved (§3.4).
8. **Some data, once missed, cannot be bought back.** **[added in revision]**
   - Options IV and skew are not captured anywhere, and free quote history cannot be rebuilt later.
   - Macro inputs are not recorded, and FRED's HY OAS series is reportedly moving to a rolling
     three-year history.
   - Point-in-time membership and fundamentals cost about $499/yr from a vendor. Building them free
     would take about 4–6 SDD-days and still leave a gap that no free source covers (§3.3).
9. **On the five-stage architecture, risk and execution are clean seams. Signal and portfolio are
   fused** inside `run_scan` (`copilot.py:682-1677`) and `evaluate_candidate`
   (`agentic_trader/agent/evaluator.py:362-917`). No typed route exists for a cross-sectional
   research result to reach a card.
10. **Roadmap.** §5 replaces the competing orderings with one sequence. It has a one-week block
    dated to the 2026-10-28 FOMC and PEAD-probe expiry, and nine dated operator decisions.

### Top 10 recommendations, in priority order

| # | Recommendation | Effort | Section |
| --- | --- | --- | --- |
| 1 | Make the population that is sent the population that is measured: add a journal-only budget for swing scans, stop sending a card on daemon start, journal empty scans and per-rule rejections, and refuse index-beta clones | S | §3.1 |
| 2 | Before the 2026-10-28 FOMC, fail safe the risk inputs: a static, digest-pinned tier-1 calendar; a typed fetch status; an UNKNOWN macro state instead of 1.0×; and a scan abort that is journaled and still sends the digest | S–M | §3.6 |
| 3 | Decide the PEAD probe by 2026-10-28: renew it for 180 days at unchanged risk and judge it on the counterfactual label, not on taps | S | §3.4 |
| 4 | Fix the yardsticks before any gate reads them: `card_stats` v2 (time-matured, de-duplicated, beta-adjusted with per-name cost), a beta-matched market control, results by asset class, and an erratum for the bracket-counterfactual descriptives. Only then put `card_policy` in preview | M | §3.5 |
| 5 | Paper auto-entry for scheduled cards through the unchanged `EntryExecutionService`. Prerequisites: entries bounded to one session, an operator cancel and a notice digest. Measure the operator tap as its own gate | M | §3.1, §3.8 |
| 6 | Native-v2 bar semantics (delayed SIP, regular-session bars anchored to the session, RSI fix) under a new identity with a fresh frozen replay, then replay the regime gates on it | M | §3.2, §3.5 |
| 7 | An L3 `CardSelector` keyed on expected net R per unit of risk, with a per-name cost. Test any residual effect as one predeclared comparison on native-v2 bars | M | §3.2 |
| 8 | Start capturing data that cannot be backfilled: nightly options chain summaries, raw macro inputs and HY OAS, and the clock regime. Pre-register the options legs before the first capture | M | §3.3 |
| 9 | Point-in-time foundation: price Sharadar against the free route; add `SecurityId`, `asof` requests and a `DELISTED` label; fix the cube's 45-day label slice before any horizon-matched cube | S–M | §3.3, §3.4 |
| 10 | Research governance, then new hypotheses. Fix the confirmation-window rule before approving the insider campaigns, and build one scheduled `pooled-forward` re-score with a predeclared read rule. Then run TSMOM-v1 on the ETF32, the fundamentals junk-tail veto and the options legs. Allow LLM proposers only as one-shot fixed-set authors behind a knowledge-cutoff guard | M | §3.4, §3.5 |

---

## 1. The system as it runs today

### 1.1 The five stages and their seams

| Stage | Where it lives (Now) | Contract | Assessment |
| --- | --- | --- | --- |
| Mining | `research/alpha`, `research/pooled`, `research/apriori`, `research/spread`, `research/setups`; CLI only | A frozen JSON protocol, pinned by SHA-256, produces journal artifacts. The registry admits immutable `AlphaDefinition` versions under CAS (`agentic_trader/storage/alpha.py:308-378`) | Rigorous. No route reaches cards except a catalog probe (§1.3) |
| Signal | `screeners/` (`StrategyEngine.scan_contract`, `agentic_trader/screeners/strategies.py:391-426`), `FormulaicAlphaStrategy`, `EarningsDriftService` | One contract's `ContractMarketData` produces `ScreenerCandidate`s (`agentic_trader/screeners/base.py:23-52`). Candidates carry indicators and a heuristic `setup_quality`, but no forecast, horizon or probability | Per symbol only; nothing sees the scan's cross-section |
| Portfolio | Inline in `run_scan`: the sort (`copilot.py:1172-1177`), budgets (1260-1284), group caps (1286-1322), `card_policy` (1337-1360) and `BookSizer` (1396-1425) | `list[tuple[Any, Any, Any]]` (`copilot.py:869`) produces a signal row | Untyped. The live book is close to empty (§3.1) |
| Execution | `execution/` and `broker/` | `OrderRequest` (`agentic_trader/broker/base.py:83`) becomes a durable work item, then an `OrderResult` | The cleanest stage |
| Risk | `risk/`: pure rules over `Book`, `EntryIntent`, `RiskBudget` and `RiskLimits` (`agentic_trader/risk/rules.py:62-82,470-513`) | Each rule returns `Rejection \| None` | Clean for entry rules. Trailing stops and macro inputs live elsewhere and fail open (§3.6) |

### 1.2 The suggestion scan, step by step (10:35 and 14:35 New York)

1. The cron fires (`service.py:437-460`; `config/config.yaml:331`) and gates on the equity session.
   `run_scan` takes the scan lock, installs the registry snapshot (`copilot.py:749-760`) and checks
   the halt.
2. It reads the regime with an **unguarded** call (`copilot.py:780`), reads the book from EXECUTED
   rows (`copilot.py:785`), and applies the session gate and the whole-scan macro lockout
   (`copilot.py:795-831`).
3. It selects the universe: 159 static names (`config/config.yaml:95-258`) plus up to 20 dynamic
   names (`agentic_trader/screeners/dynamic_universe.py:113-176`). At 10:35 only, it also adds PEAD
   events (`copilot.py:897-948`).
4. **Fetch** (`agentic_trader/data/market_data.py:197-239`). Every symbol gets 1y of daily bars and
   60d of hourly bars. **[revised]** Every symbol also gets 10d of 15m bars whenever *any* active,
   shadow or probe registry definition has timeframe `15m` (`copilot.py:833-837`;
   `market_data.py:204,222-227`). Shadow definitions count; 16 are registered (maps/live.md Q13).
   That fits the 28% share of 15m captures measured on 2026-10-20 (maps/data.md §8). The bars come
   from IEX with raw adjustment (`agentic_trader/config.py:1043-1050`). 4h bars are a UTC-anchored
   `resample("4h")` of the hourly bars (`market_data.py:119-138`).
5. **Collect** (`copilot.py:1043-1131`). The coverage gate runs, then `scan_contract`, then
   `_admit_candidate`: the 12-hour duplicate rule (`copilot.py:1694-1723`; `config/config.yaml:276`),
   account risk, and a deterministic `evaluate_candidate`. Rejections are only logged
   (`copilot.py:1754-1762`).
   - **Bracket [revised].** The stop distance is `max(entry − (swing_low − 2 ticks), 1.5 × ATR14)`
     and `stop = entry − distance` (`evaluator.py:208-216`). For trend-pullback, ATR14 is the
     **4h** ATR (`strategies.py:133,163`). The target is `ratio × actual stop distance`, rounded up
     to the tick (`evaluator.py:217-222`). The ratio has been 2.0 in every journaled regime
     (maps/live.md Q12).
   - **Formula alphas [revised].** `FormulaicAlphaStrategy` fires on a level condition, not a
     crossing. `entry_directions` returns ±1 whenever `z ≥ threshold` or `z ≤ −threshold`
     (`agentic_trader/research/alpha/strategy.py:155-161`), and it is evaluated on the latest bar
     (`agentic_trader/screeners/formulaic.py:102-104`). The alpha therefore fires again on every
     bar beyond the threshold. Only the 12-hour duplicate rule suppresses the repeats.
6. **Rank** by `-setup_quality` (`copilot.py:1174-1177`). The journal records
   `"ranking_key": "setup_quality"` (`copilot.py:1907`). This step also attaches shadow features,
   the book context, the `card_stats` evidence, the `card_policy` decision (computed in every mode,
   `copilot.py:1245-1250`) and the news blocks.
7. **Send loop.** Candidates go through the budgets (1 per scan, 2 per session, 1 per group;
   `config/config.yaml:443-445`), then the LLM evaluation (`copilot.py:1364`), then
   `BookSizer.decide` (1412), then `record_signal`. The signal row and the outbox intent are written
   in one transaction (1459).
8. The scan writes `scan_candidates_ranked` only when `budget != NONE`, `native_count > 0`, no symbol
   filter is set and there is no timeframe (`copilot.py:1591`). It then runs `monitor_positions`
   (1676).
9. **Entry happens only on an operator tap.** The tap calls `execute_signal_by_id`
   (`copilot.py:2912`), whose only callers are the Telegram handler (`copilot.py:466`) and the CLI
   (`cli/commands/trade.py:94`). After freshness and re-gating, `EntryExecutionService` reserves
   risk, runs FIFO admission against broker evidence, and POSTs a single limit bracket. No
   time-in-force is set, so the GTC default applies (`broker/base.py:98`; `copilot.py:2967-2978`).

Other producers spend the same card budget: the swing scan (every 4 h from daemon start, first run
immediate, `ScanBudget.FULL`; `service.py:581-593`), the 15m intraday job (`service.py:596-626`),
`/scan`, the chat tool `trigger_market_scan` (`agentic_trader/agent/copilot_tools.py:61-70`) and
Re-evaluate.

### 1.3 Routes from research to production

| Route | State Now |
| --- | --- |
| (a) DSL alpha → `active` | Unreachable in practice (§3.4 MI12). Zero active alphas (maps/live.md Q13) |
| (b) DSL alpha → `probe` | Possible in principle; no qualified candidate exists |
| (c) Catalog leg → `probe` | Live for PEAD only. `CATALOG_ENTRY_IDS = ("pead",)` (`agentic_trader/research/apriori/probe.py:20`). The term expires 2026-10-28 (maps/live.md Q13) |
| (d) Pooled formula → card | **No route.** It needs an unbuilt "Part 2" spec (`docs/alpha-pooled-mining.md:1016,1368`) |
| (e) Config edit and restart | How `book_sizing` and `corporate_actions` reached `enforce` (#125) |
| (f) Measured evidence on the card (`card_stats`) | Daily snapshot. `card_policy` ships `off` (`config/config.yaml:454`) |

### 1.4 Live record, 2026-09-14 → 2026-10-21 (maps/live.md §3–§4)

| Quantity | Value |
| --- | --- |
| Cards sent (alpaca:paper) | 42 over 28 sessions. Since 09-18: 41 over 24 sessions, 1.7 per session. 37 TREND_PULLBACK, 1 SQUEEZE_BREAKOUT, 4 `pead_long` (Q2, Q3) |
| Raw setups | 105 over 19 digest days, 1.2% of name-scans (Q8) |
| Journaled candidates | 87 rows covering 41 distinct setups (Q5, Q6) |
| Operator taps | 9 cards. 3 missed on re-price, 1 timed out. **3 accepted entries**, 2 rejected for system reasons. No tap since BLK on 10-16 (Q10; gap review) |
| Closed trades | SPY −0.43R, XOM +2.37R (after 29 trailing-stop notices), VRNS −0.95R. BLK (`pead_long`) still open (Q4, Q11) |
| Account | +$240 (+0.23%) since 09-18. Drawdown from the high-water mark 0.50%. SPY rose 1.2% over the same span. The book was mostly cash, so the two returns are not comparable (Q11, Q15) |
| LLM gate | 14 of 14 journaled evaluations approved. Two large bracket tightenings (SPY, CEG) (Q7, Q16) |
| Shadow alpha lane | 0 active, 16 shadow, 1 probe. All 10,359 shadow forecasts invalid (6,915 data-contract mismatch, 3,444 stale bar). 0 session decisions scored (Q13) |
| Telegram traffic | 659 incident notices, 33 expiry notices and 62 trailing-stop notices, against 41 cards (Q9) |

---

## 2. The research record, and how to read it

### 2.1 Timeline (condensed from maps/history.md §4)

| Date | Lane | Outcome | Source |
| --- | --- | --- | --- |
| 09-16 → 09-19 | Per-symbol ETF32 and equity mining (about 8,900 charged trials), calibration, forecasts, sector panel, persistent book | 0 finalists. Planted-effect acceptance 0/64 | `docs/alpha-research-2026-09-16.md`; `docs/alpha-power-diagnosis-2026-09-18.md`; `docs/alpha-persistent-book-2026-09-17.md` |
| 09-23 | Setup-outcome study (15,644 setups) | `setup_quality` uninformative. Longs +0.03R. The Ridge ranker did *worse* in the holdout. `vol_20` and `sector_rel_mom_60` survive Holm | `docs/setup-outcomes-2026-09-23.md` "Hypotheses", "Selection (H3)" |
| 09-23 | Native shorts on a fresh 2017–21 window | −0.33R. Shorts disabled | `docs/setup-baserates-short-2026-09-23.md` |
| 09-26 | **PEAD v2, a priori** | **Long leg passes**: +0.149R, paired +0.055R over control | `docs/apriori-pead-2026-09-26.md` "Criteria" |
| 09-30 → 10-07 | Pooled literature entries, genetic v2/v3, fixed set v4 | Paired edges ≈ 0. Check B failed, so the genetic lane is parked. v4 found no finalists | `docs/alpha-pooled-literature-2026-09-30.md`; `docs/alpha-pooled-checks-v3-2026-10-05.md`; `docs/alpha-pooled-checks-v4-2026-10-07.md` |
| 10-10 / 10-11 | Spread-reversion v1 and v2 | Both failed discovery | `docs/alpha-spread-results-2026-10-10.md`; `docs/alpha-spread-results-2026-10-11.md` |
| 10-12 | Ex-dividend run-up | Failed P2 against the same-name placebo | `docs/apriori-exdiv-2026-10-12.md` |
| 10-14 | Setup recurrence | `no_change`: repeats −0.067R | `docs/setup-recurrence-2026-10-14.md` "Hypotheses" |
| 10-14 → 10-20 | Pooled intraday, events, cohort v3 ×3, short volume ×2 | 64 pairs in total, no finalist. `shorts_retreat` passed discovery at t +3.40 and failed selection | `docs/alpha-pooled-intraday-v1-2026-10-14.md`; `docs/alpha-pooled-events-v1-2026-10-15.md`; `docs/alpha-pooled-cohort3-2026-10-16.md`; `docs/alpha-pooled-short-v1-2026-10-20.md` "Cohort v3" |
| 10-18 | Bracket counterfactual | `no_change`. The live ratchet is worse by 0.013R. "Every arm trails SPY by 0.10–0.15R" (but see §2.2) | `docs/bracket-counterfactual-2026-10-18.md` "Rules", "Descriptives" |
| 10-20 | Survivorship spike | Bars for delisted names exist; a dated membership record does not | `docs/survivorship-spike-2026-10-20.md` "Answer" |
| 10-21 | EDGAR insider ×2 | Built. Check A at replicate 18/100. Campaigns await approval | `~/agentic-trader-research/insider-v1-checks-20261021.log` |

### 2.2 How to read it correctly **[revised]**

1. **Live `card_stats` figures to date are censoring artefacts, not evidence of edge in either
   direction.**
   - The TREND_PULLBACK LONG snapshot went −0.376R (10-08, n 34) → −0.102R (10-13) → +0.467R
     (10-16) → **+0.535R (10-21, n 67)** (maps/live.md Q1).
   - On a 2R bracket, stops resolve quickly and targets slowly. Dropping immature rows
     (`agentic_trader/research/setups/card_stats.py:91-92`) therefore over-weights stops early.
   - The earlier headlines came from these censored early snapshots, on raw R. That covers
     "runners-up −0.40R" (`docs/alpha-roadmap.md:634-637`); "−0.17R (n 33)", "27% target / 73%
     stop" and "sent −0.47R (n 5)" (`docs/alpha-roadmap.md:507-513,540-543`); and "TREND_PULLBACK
     LONG −0.376R on 34 mature" (memory `honest-cards-pr-a-workstream`).
   - The current +0.535R is no better evidence. It is raw R, 87 rows cover only 41 setups
     (`card_stats.py:112` does not de-duplicate), and every candidate saw one regime (SPY above its
     200-day average, VIX 15.8–18.2; Q12).
   - Enforcing `card_policy` at threshold 0 would withhold **nothing** today. The maps' claim that it
     "would withhold most native cards" (maps/history.md §6.1) is out of date.
   - **Only the replay is defensible evidence.**
2. **The "trails SPY by 0.10–0.15R" headline overstates the deficit.**
   - `market_r = sign × (SPY close/open − 1) × entry / risk_unit` (`outcomes.py:210-221`) assumes a
     beta of one, in the setup's own R units. `excess_r = r_cost − market_r` (`outcomes.py:382-383`),
     and the bracket study recomputes it the same way (`bracket_counterfactual.py:175-177`).
   - (a) **Beta mismatch.** In the primary frame, return-space betas by stop-width quintile run
     0.39–1.21. With those betas the A0_live excess is **−0.062R**, against −0.109R beta-one
     (session mean −0.069R; naive session SE 0.024). Measured in review with
     `scripts/pm_beta_adj_R.py`. The betas are in-sample, so this is descriptive.
   - (b) **Bond-ETF leverage of the control.** In the primary window, 416 of 8,768 A0_live setups
     are on rates/credit ETFs, with median entry/risk of 233. Their unclipped mean `market_r` is
     +2.15R (+0.82R after the ±10R clip), against r_cost +0.17R, so their excess is −0.64R; 44 of
     them hit the clip. Removing them moves the primary excess from −0.109R to −0.083R.
   - In the robustness window, 343 such rows (4.2%) carry unclipped `market_r` +7.66R and excess
     −2.32R. Without them the headline −0.103R becomes **−0.006R** (−0.008R if metals are also
     removed). Measured in review with `scripts/rates_excess_check.py`.
   - The gap review's approximate bootstrap puts the "reliably worse" live ratchet at −0.0051R
     (90% CI [−0.011, −0.0001]) without rates/credit, against −0.0126R with them.
   - **Conclusion.** The frozen `no_change` decision stands, because no arm passes either way. But
     two descriptive claims are largely artefacts of the yardstick in the robustness window: the
     doc's and CLAUDE.md's "every arm trails SPY by 0.10–0.15R in both windows" and "the ratchet is
     worse by 0.013R". In the primary window, a negative excess of about half the quoted size
     survives.
3. **None of the replays applies the regime gates. [added in revision]**
   - Replay builds `RiskEvaluator` with a calendar that does no I/O and never calls the regime
     detector (`agentic_trader/research/setups/replay.py:126-133,287-291`).
   - Replay sizes with `calculate_levels_deterministic(candidate)` (`replay.py:313`), whose defaults
     are `macro_risk_multiplier=1.0` and `min_reward_risk=None` (`evaluator.py:166-174`).
   - Official closes (Cboe VIX history and the Treasury par curve) were run through the live
     thresholds (`agentic_trader/agent/regime.py:172-189`; `agentic_trader/agent/macro.py:149-156,250-259`).
     Over the counterfactual's primary window, 2021-06-01 → 2026-01-29:
     - the curve was inverted on 60.0% of sessions;
     - squeeze breakouts would have been suppressed on at least 63.4% of sessions;
     - targets of at least 2.2R would have been required on at least 71.5%.

     The 2017–2021 figures are 18.8% and 34.9%. All are lower bounds that omit HY OAS
     (`macro-sourcing-probes/`).
   - The "live" arm (A0_live) of the bracket counterfactual, including its 1,311 squeeze setups
     (`docs/bracket-counterfactual-2026-10-18.md` "Descriptives"), is therefore an *ungated*
     population.
4. **The pooled record is a power statement, not a "no".**
   - The implied discovery standard errors run from 0.006R to 0.042R, median 0.017R. At the median,
     the 80%-power detectable edge at t ≥ 3 is about 0.065R.
   - The 64 t-statistics have mean −0.06 and sd 1.25. Check C's null runs give a seed-t sd of
     1.11–1.27 (`docs/alpha-pooled-cohort3-2026-10-16.md` "Check C"; maps/pooled.md §4).
   - The lane's own design doc expected "few discoveries"
     (`docs/alpha-pooled-mining-research-2026-09-28.md` "Conclusion").
5. **Learned rankers have already failed out of sample.**
   - In the holdout, random selection earned −0.118R, `setup_quality` −0.126R and the frozen Ridge
     −0.293R. The paired CI of Ridge against `setup_quality` is [−0.325, −0.016]
     (`docs/setup-outcomes-2026-09-23.md` "Selection (H3)").
   - Any new selector must say in advance why it would differ.

### 2.3 Patterns that recur

- **A positive leg with zero paired edge.** Long brackets earn about +0.15R on cohort v2 whatever
  the formula. That is drift plus survivorship (`docs/alpha-pooled-literature-2026-09-30.md`
  "Criteria").
- **Discovery winners fail out of sample**, and the gates caught every one: the Ridge ranker,
  `shorts_retreat`, `quiet_momentum` and recurrence.
- **Construction defects are found late**: the pead-v1 calendar gap, spread v1 entries already
  beyond the stop, GTC suppression, the uncapped GPOR +1,546R label, and the `corporate_actions`
  config loss. This review adds three: the cube's label span (§3.4 MI4), the bond-ETF yardstick
  (§2.2) and the ungated replays (§2.2).
- **Measurement lanes are started and never read**: the IEX shadow lane and the daily panel's
  missing read rule (maps/live.md §4).
- **Power is set below the plausible effect** (§2.2 item 4).

---

## 3. Findings by area

Each subsection lists what the code does **Now** (cited), then numbered gaps, then **Recommend**
items. The IDs (CS1, SG-R1, …) are used again in the roadmap (§5).

### 3.1 The card stream and the operator loop

**Now**

- **Who sends cards.** There are five producers, and all of them spend one durable budget derived
  from today's `signals` rows (`copilot.py:1268-1283`; 1 card per scan, 2 per session, 1 per group,
  `config/config.yaml:443-445`):
  - the suggestion scans;
  - the swing scan, which runs once at daemon start and then every 4 h (`service.py:581-593`). The
    `config.yaml:330` comment "aligned with 4h candle closes" is false;
  - the intraday job;
  - `/scan` and the chat tool;
  - Re-evaluate.
- **What is journaled.** Only unrestricted, budgeted scans with at least one admitted native
  candidate (`copilot.py:1591`). Collect-phase risk rejections are only logged
  (`copilot.py:1754-1762`).
- **Card policy.** `card_policy.decide` runs in every mode (`copilot.py:1245-1250`). The journal
  block records `measured_ev`, `n_mature` and `n_fetch_failed` in every mode
  (`agentic_trader/execution/card_policy.py:71-84`). Preview only adds `would_withhold` and a log
  line (`card_policy.py:47-55`; `copilot.py:1337-1350`).
- **Entry.** Entry requires a tap (§1.2 step 9; `docs/production.md:43`: "Scans stage suggestions;
  an operator approves entry orders").

**Gaps**

- **CS1 — The cards that are sent are not the population that is measured. [added in revision]**
  The journal was joined to `signals` by trigger (gap review, read-only psql). From 09-24 to 10-21
  the desk sent 37 cards: 4 `pead_long` and 33 native. Of the 33 native cards:
  - 19 came from the suggestion slots, including late 10:35 runs;
  - 2 came from journaled swing scans (AUGO 10-15 12:10, SPY 10-19 11:46);
  - 12 were never journaled. These came from swing, operator or Re-evaluate scans before PR A.1
    (#111, live on 10-09).

  So 42% did not come from the slots; since A.1 the share is 2 of 9. The docs' "18 of 22 unjournaled"
  (`docs/card-evidence.md:378-380`; `docs/alpha-roadmap.md:533`) overstates the problem. 11 were
  unjournaled. Another 7 were journaled, but before #106 added `signal_id`, so they match only by
  contract and time.

  Replay and `card_stats` model only the 10:35 and 14:35 instants (`replay.py:3-6`). The base rate a
  card prints therefore describes a different population whenever the card came from elsewhere.
- **CS2 — The 14:35 starvation was a pre-A.1 effect. [revised]**
  - In 7 of the 13 journaled 14:35 scans, every candidate was refused with "per-session budget
    spent". All 7 fell between 09-25 and 10-08, each time after a card from outside the slots had
    taken a slot. This has not happened since 10-09.
  - maps/live.md §7.3 says 14:35 supply "roughly doubles"; that estimate rests on the pre-A.1 data.
  - The harm that remains: since A.1, swing scans spent 2 of the 9 native slots, and a restart
    during the session can still spend a slot on a bar that is mostly pre-market.
- **CS3 — Index-beta clones.**
  - Correlation groups are configured sector labels. A dynamic name gets only the `dynamic` group
    (`copilot.py:612-617`).
  - The result: SPY (swing, 10-19), VTI (10-20 10:39) and VOO (a dynamic name, 10-20 14:38) were
    three of the last nine native cards, all broad-index beta. Their excess return is about zero by
    construction (maps/live.md §6 gap 4).
- **CS4 — Two denominators are invisible.**
  - Zero-candidate scans write no event. 12 of the 39 scheduled slots from 09-24 to 10-21 have none.
  - Per-rule rejections exist only in logs (maps/live.md §6 gap 2).
  - Together these hide candidate supply and the cost of each risk rule.
- **CS5 — Tap-only entry keeps the book empty, so the portfolio and risk layers never act. [added
  in revision]**
  - There were 9 taps on 42 cards and 3 accepted entries (Q10). Every `book_sizing` factor was 1.00
    (`docs/card-evidence.md:232`).
  - Measured in review (`autoentry/book_sim.py`): the 41 cards #14–#54 were entered at their default
    tier, labelled with the repo's `label_bracket` on 1h bars, and run against the configured caps
    (`config/config.yaml:3-7`). The book would have held a median of 4 open positions and a maximum
    of 8.
  - 10 of the 41 would have been refused by book rules: same_symbol 5, concurrent_positions 4,
    portfolio_notional 1.
  - Planned stop risk would have peaked at $1,626 (1.6% against the 2% cap), with about 1.3 fills
    per session.
  - Caveat: Yahoo 1h bars were used, and their buckets start at :30, not :00.
- **CS6 — The operator's tap is an unmeasured fifth gate. [added in revision]**
  - `cards outcomes` attributes the selection scorer, the LLM, exposure, policy, sizing, news, regime,
    recurrence and the counterfactual arms (`outcomes.py:857-1000`), but has no operator block.
    `collect_execution` keeps only tap age and R consumed
    (`agentic_trader/research/setups/execution_evidence.py:126-136,245-246`).
  - Measured in review (`autoentry/label_taps.py`): 29 sent cards have resolved, with 9 targets, a
    mean of −0.21R raw and −0.23R excess.
  - Collapsed to 24 setups, the 4 tapped ones average −0.51R and the 20 untapped +0.14R raw
    (+0.17R excess). The difference has a permutation p of 0.31.
  - Four of the nine taps were one CRM setup. Tap latency ran from 4 s to 17,473 s.
  - The tap's value is **unknown**, not shown to be negative.
- **CS7 — Cards are buried in operational traffic.** Telegram carried 754 notices against 41 cards
  (Q9), including 62 trailing-stop notices, 29 of them for XOM. CLAUDE.md contract 3 requires a
  notice for every confirmed ratchet.
- **CS8 — Putting `card_policy` in preview today adds no information. [added in revision]** Because
  of the mechanics under **Now**, `would_withhold` can be recomputed afterwards for any threshold.
  At threshold 0 it would flag nothing on 10-21. The proposals to "turn preview on now"
  (maps/history.md §7.1; maps/signals.md §7.1) cost nothing and inform nothing. The step that
  matters is the estimator (RM1).
- **CS9 — `ScanBudget.NONE` is not "send nothing". [revised]** maps/architecture.md §7.1 suggests
  running swing scans with `NONE`. In fact `NONE` means no limits and still sends cards
  (`agentic_trader/config.py:187-190`; `docs/card-evidence.md:376-377`), and it is excluded from the
  journal (`copilot.py:1591`). That is the opposite of the intent.

**Recommend**

- **CS-R1 [S].** Add a `JOURNAL_ONLY` scan budget for the swing (and intraday) scans: they journal
  with their trigger and send nothing. Remove the immediate run at daemon start
  (`service.py:588-592`). *Effect:* the cards that are sent become the measured population, and
  restarts stop spending slots. *Operator decision D2.*
- **CS-R2 [S].** Stop index clones in one of two ways:
  - exclude broad-index ETFs from native cards (decision D4); or
  - refuse a second card whose return correlation with an earlier card that session is above 0.8,
    using the Ledoit–Wolf covariance the book sizer already builds
    (`agentic_trader/execution/book_sizing.py:188-281`).
- **CS-R3 [S].** Journal zero-candidate scans, and per-rule rejection counts from collect.
- **CS-R4 [M; decision D9; amends CLAUDE.md contract 1 and `docs/production.md:43` for paper only].** Add a
  paper-only auto-entry mode.
  - **Config.** Add `execution.auto_entry: off|paper`, validated in `load_config`. `paper` is
    accepted only when `execution_mode == alpaca` and `alpaca_paper` is true. Re-check
    `is_paper_scope` (`agentic_trader/research/alpha/probe.py:53-55`) inside `enqueue_entry`.
  - **Trigger.** Only `RankedOutcome.SENT` cards from `ScanTrigger.SUGGESTION_SCAN`
    (`agentic_trader/execution/durable.py:75`) qualify. Each calls the existing
    `execute_signal_by_id(sig_id, quantity=None)`. Tag the journal with `entry_trigger: auto|tap`.
  - **Prerequisites in the same change:** session-bounded entries (EX-R1), an operator cancel that
    reuses `entry_cancel`, and the notice digest (CS-R6).
  - **Pass criteria after 20 sessions:**
    - at least one `book_sizing` factor below 1;
    - per-rule admission refusals visible;
    - at least 20 entries with entry and exit slippage;
    - labelled R reconciled with realized R, card by card.
  - Label paper fills as optimistic: Alpaca's paper fill model ignores queue position and impact.
- **CS-R5 [S].** Add an `operator` block to `summarize()`:
  - tap rate by slot, trigger and strategy;
  - tap latency;
  - the mix of tap outcomes;
  - tapped against untapped excess R, de-duplicated per setup, with a block-bootstrap CI.

  Predeclare a forecast-value-added rule (Gilliland; Fildes et al. 2009). Once 30 distinct setups
  have resolved, keep human selection only if tapped minus untapped has a CI above zero. Under
  auto-entry, attribute Dismiss and cancel vetoes the way the LLM gate is attributed.
- **CS-R6 [S].** Send cards and operational notices through separate channels or a digest. Keep a
  durable notice for every ratchet, but batch ratchet and expiry notices into the digest.

### 3.2 Signals, selection and the LLM gate

**Now**

- **Candidate contract.** A candidate holds indicator fields and `setup_quality`
  (`screeners/base.py:23-52`); it carries no forecast.
- **Trend pullback.** It requires all of:
  - daily close > EMA50 > EMA200, on the bar still forming;
  - the 4h close within 0.5×ATR of the 4h EMA20 (`strategies.py:136-137`);
  - an RSI rule of "min(previous two) < 45 and current ≥ 40" (`strategies.py:146-149`;
    `config/config.yaml:311-312`).
- **Squeeze breakout** is defined at `strategies.py:223-349`. Shorts are disabled
  (`config/config.yaml:305,320`).
- **Bracket and formula-alpha firing**: see §1.2 step 5, as corrected.
- **Conflict resolution.** Ownership is chosen lexically by `min((probe, strategy, timeframe,
  alpha_version))` (`agentic_trader/screeners/registry.py:96-104`).
- **LLM gate** (`evaluator.py:720-884`):
  - It sees a subset of what the gates already enforce.
  - Its `acompletion` call has no deadline (`evaluator.py:751-760`).
  - Any exception approves the card, sets `macro_clearance=True` and puts the raw exception text in
    the thesis (`evaluator.py:886-912`).
  - `llm_verdict` records only approved, rejection_reason, stop, target and applied
    (`evaluator.py:859-865`).
- **Dynamic names** come from most-actives (ranked by trade count) and movers, filtered to $10 and
  above, then checked against a percentile liquidity gate (`dynamic_universe.py:113-176,219-265`).

**Gaps**

- **SG1 — The ranking key carries no information, and ranking matters.**
  - `setup_quality` has development ρ +0.016 and holdout −0.012 (`docs/setup-outcomes-2026-09-23.md`
    "Hypotheses", "Selection (H3)").
  - In 13 of the 21 journaled scans that sent a card, ranking actually chose between candidates; in
    12 of those a runner-up was refused for the per-scan budget (gap review).
- **SG2 — The three proposed replacement keys point in opposite directions. [added in revision;
  replaces maps/architecture.md §7.3]**
  - The proposals are:
    - a meta-label P(target before stop) (maps/architecture.md §7.3);
    - a `vol_20` + `sector_rel_mom_60` composite (maps/signals.md §7.2; maps/history.md §7.1;
      maps/alpha_pipeline.md I1);
    - a −Δvol tie-break (maps/portfolio.md §7.6).
  - Measured in review on the counterfactual primary frame (A0_live, 4h, n 7,579, R clipped at
    ±10). By ATR% tercile, low/mid/high:

    | ATR% tercile | Target rate | Stop rate | Cost in R | Beta-one `market_r` | Net R | Excess R |
    | --- | --- | --- | --- | --- | --- | --- |
    | low | 33.5% | 63.4% | 0.122 | 0.232 | −0.092 | −0.325 |
    | mid | 35.0% | 61.6% | 0.055 | 0.144 | +0.073 | −0.071 |
    | high | 34.6% | 58.0% | 0.032 | 0.089 | +0.117 | +0.028 |

  - Target rates are flat. Most of the excess-R spread comes from two terms that scale with
    1/stop%: the fixed basis-point cost and the beta-one control. The rest comes from fewer stops.
  - `vol_20`'s ρ of +0.049 against R_cost ("Hypotheses", H2d) is therefore mostly a cost-efficiency
    effect, and its size depends on the assumed 5 bp per side.
  - On a near-empty book, −Δvol picks the *lowest*-volatility candidate. The $7,500 cap binds on
    82.9% of 4h setups, so a card's dollar volatility is about $7,500 × σ. That is the worst
    tercile.
  - A meta-label repeats the Ridge failure (−0.293R against −0.126R in the holdout), and P(target)
    is blind to an effect that does not move the target rate.
  - The 10-21 → 11-04 prospective read has about 45 candidates, so SE(ρ) ≈ 0.15 against an effect of
    0.05.
  - (The gap review's figures used a different winsorisation; the direction and conclusion are the
    same. See Appendix A.)
- **SG3 — The pullback trigger is semantically off.**
  - The dip threshold (45) is above the recovery threshold (40), so "dipped below 45, now ≥ 40" also
    fires while RSI is still falling, for example 44 → 41 (`strategies.py:146-149`).
  - The recovery term uses `abs(rsi_now − rsi_extreme)` (`strategies.py:42`).
  - `docs/strategies.md:31-34` describes a dip-and-recover rule that this configuration does not
    enforce.
- **SG4 — Bar semantics differ between live and replay.**
  - Live uses UTC-anchored 4h resampling (`market_data.py:119-138`) over IEX hourly bars. Every live
    card's candle is stamped 12:00 or 16:00 UTC (Q14).
  - The replay cache holds SIP hourly bars from 04:00 to 19:00 New York: 3 to 4.3 UTC 4h buckets per
    session, including extended hours. This was measured on the XOM, KO, SEIC and ABBV caches.
  - Live acts on the bar still forming; replay does not (`replay.py:16-24`).
  - **[revised] DST on 2026-11-01 is not a deadline.** In the replay, EDT and EST 4h setups are
    indistinguishable: r_cost +0.040R against +0.020R (session-clustered SE 0.038 and 0.046), target
    rate 34.4% against 34.2% (gap review).
  - Live is different. IEX names without extended-hours prints go from 2 to 3 regular-session
    buckets per session, so the trigger's 4h EMA20 and RSI14 shrink from about 10 and 7 sessions to
    about 6.7 and 4.7.
- **SG5 — The LLM gate selects nothing, fails open and is not identified.**
  - It approved 14 of 14 (Q7).
  - Its fallback approves on any exception, including a malformed veto reply.
  - **[added in revision]** `llm_verdict` has no model or prompt identity. The model can be switched
    by environment variable (`agentic_trader/config.py:989`), so `cards outcomes`' `llm_gate` block
    pools every model and prompt that ever ran (`outcomes.py:936-956`). The news block, by contrast,
    pins both (`agentic_trader/agent/news_context.py:49,166-169`).
  - *Correction to maps/live.md §7.5:* `cards outcomes` already labels LLM-edited brackets
    separately (`llm_r_cost`, `bracket_edit_mean_delta_r`; `outcomes.py:939-956`). Only `card_stats`
    labels the deterministic bracket (`docs/card-evidence.md` "Limits / Proxy bracket").
- **SG6 — Formula alphas fire on a level. [revised]** A registry formula that stays above its
  threshold produces a candidate on every scan once 12 h have passed since its last card. Unless a
  position in the same symbol is held, that means a fresh card each session. The research simulator
  holds one position at a time (`agentic_trader/research/alpha/simulation.py:233`). So live repeats
  have no research counterpart once a card goes untapped.
- **SG7 — The 15m fetch is universe-wide. [revised]** It is fetched for every symbol whenever any
  registry definition, shadow included, is 15m (§1.2 step 4). That is about 540 paced requests per
  scan instead of about 360. Separately, the 15m intraday job can produce nothing:
  - native strategies emit only 4h and 1h (`strategies.py:341-349`);
  - the job keeps only 15m candidates (`copilot.py:1092-1094`);
  - no 15m alpha is active.

  It still takes the scan lock every 15 minutes.
- **SG8 — Dynamic names are unvalidated.**
  - No replay or base rate exists for them; the setup study used the static 159
    (`docs/setup-outcomes-2026-09-23.md` "Protocol and run").
  - The shadow cross-section excludes them (`agentic_trader/research/setups/ranker.py:245-249`).
  - They come from attention screens, where the literature predicts adverse selection for longs
    (Barber & Odean 2008; Da, Engelberg & Gao 2011).
- **SG9 — Research has no landing zone.**
  - The live executor is per-symbol z-thresholding.
  - Pooled formulas pick cross-sectional deciles.
  - Each new card family has been added as a branch inside `run_scan`: PEAD at `copilot.py:897-948`
    and 1133-1170; dynamic names at 876-896.

**Recommend**

- **SG-R1 [M, after RM-R1 and SG-R2] — L3 `CardSelector` with one key: expected net R per unit of
  risk.**
  - The key is a shrunk gross prior per (strategy, timeframe, direction) minus `2·c_i / stop%_i`.
  - Estimate c_i per name from daily bars already held, using Abdi–Ranaldo (2017) or Corwin–Schultz
    (2012), instead of a flat 5 bp.
  - Journal the key as `ranking_key`.
  - Use −Δvol only as a correlation-aware tie-break, in R units, and only when the book holds
    positions.
  - Test the remaining gross effect (about +0.12R from low to high tercile, through fewer stops), and
    `sector_rel_mom_60`, as **one** predeclared comparison:
    - on native-v2 bars;
    - over a window no study has read (after 2026-08, or a declared second use of 2017–21);
    - against `setup_quality` and random.
  - A meta-label enters that comparison only if its spec states why it would differ from the failed
    Ridge, for example an excess-R target, de-duplication or purged cross-validation.
- **SG-R2 [M] — Native-v2 bar semantics** (shared with DA-R1):
  - delayed SIP (`end = now − 16 min`, as the PEAD path already does at `copilot.py:397-401`);
  - regular-session 1h and 4h bars anchored at the open, following `build_session_bars`
    (`agentic_trader/market/bars.py:276-346`);
  - no forming bar;
  - indicators on regular-session bars only;
  - the RSI trigger fixed (dip threshold below the recovery threshold, signed recovery term).

  Ship it as a new identity with a fresh frozen replay. Everything replay-based waits for it: the
  ratchet-off test, the native-versus-universe control, a second look at `vol_20` and any
  meta-label. Journal `clock_regime` on each candidate, and treat 11-02 onward as its own stratum.
  *Decision D5.*
- **SG-R3 [S] — LLM hygiene.**
  - Add an `asyncio.wait_for` deadline.
  - Journal a fallback as `llm_verdict = {"error": TypeName}` with its own outcome. Make a malformed
    veto fail closed. Never set `macro_clearance=True` on a fallback.
  - Add `model` (the dated snapshot the provider returned) and `prompt_sha256` to `llm_verdict`, and
    group `llm_gate` by them.
  - *Decision D7:* whether the LLM is commentary-only for native cards, as it already is for
    catalog probes (`evaluator.py:858-870`).
- **SG-R4 [S].** Scope the 15m fetch to the symbols that a 15m definition covers. Turn the intraday
  job off (`scheduler.intraday_scan_enabled: false`) until a 15m alpha is active.
- **SG-R5 [M, before a second catalog leg].** Add a `CatalogCandidateSource` protocol (decision time,
  frozen bracket, own budget) to replace the PEAD special cases (`copilot.py:626,904,1273-1279`).
- **SG-R6 [M, only after a pooled finalist or the composite exists].** A cross-sectional
  `RankerDefinition` or `PanelScoreStrategy` that ranks the scan's cross-section, deployed through
  the `off → preview → enforce` pattern.

### 3.3 Data acquisition and parity

**Now**

- **Live bars.**
  - Requests are one symbol at a time with `adjustment="raw"` (`agentic_trader/data/providers.py:178-277`).
  - The feed is IEX because of the `load_config` default (`config.py:1043-1050`), although
    `MarketDataConfig` defaults to `sip` (`config.py:689`). All 3,432 captures on 2026-10-20 were
    `alpaca:iex` (maps/data.md §2.1).
  - yfinance is a silent fallback (`providers.py:426-437,463-562`).
  - `fetch_latest_price` has no feed pin and no age check (`providers.py:348-379`). It drives taps
    (`copilot.py:3151`) and ratchets (`copilot.py:2554`).
- **Research bars.** Setup replays use SIP with adjustment `all`
  (`agentic_trader/research/setups/baserates.py:57-58`). The cubes use SIP adjusted bars plus raw
  bars for the eligibility gates (`agentic_trader/research/pooled/runner.py:220-245`).
- **Cohorts.** Cohorts use current membership: "not point-in-time" (`config/research/pooled/cohort-v2.json:4`;
  `cohort-v3.json:4`).
- **Event data.**
  - FINRA (`agentic_trader/research/short_volume.py:42`).
  - EDGAR Form 3/4/5, mapped through today's `company_tickers.json`
    (`agentic_trader/research/insider_filings.py:61,574-596`).
  - The Nasdaq calendar, corporate actions and news.
- **Options and fundamentals.**
  - Options are display-only: Yahoo, the first 3 expirations, a 60 s in-memory cache, nothing
    persisted (`agentic_trader/options/fetcher.py:35-41,62-65,122-127`).
  - The normalizer replaces a missing or zero IV with 0.20 and a missing DTE with 14 days
    (`agentic_trader/options/gex.py:24-48`; `agentic_trader/constants.py:259,263`).
  - No module imports Alpaca's `OptionHistoricalDataClient`.
  - No accounting data exists: `xbrl`, `companyfacts` and `fundamental` have zero hits under
    `agentic_trader/` and `config/`.
- **Macro inputs.**
  - yfinance supplies VIX, ^TNX, ^IRX, ^FVX, ^TYX and DXY (`macro.py:283-293,383-390`;
    `regime.py:103-113`).
  - The 2y yield, HY OAS and breakevens are scraped from the last row of FRED's graph CSV
    (`macro.py:296-351`; `constants.py:161-173`).
  - The economic calendar comes from ForexFactory (`constants.py:126`).

**Gaps**

- **DA1 — Live and research see different bars** (maps/data.md G1). Hourly bars per session differ:

  | Symbol | Live (IEX) | Research (SIP) |
  | --- | --- | --- |
  | SPY | 9 hourly bars, 08:00–16:00 ET | 16 bars, 04:00–19:00 ET |
  | Mid-caps (AMKR, AFL, AEM, ATKR) | 7 bars | AMKR: a 04:00 bar on 27% of days; 17:00–19:00 bars on about 30% |

  The native trigger is therefore computed on different series live and in replay. `card_stats`
  labels outcomes on IEX (`agentic_trader/research/setups/sources.py:26-34`).
- **DA2 — Raw indicators break across splits.** Daily EMAs, hourly history and the book-sizing
  covariance all use raw closes. A reverse split reads as a months-long uptrend, and a forward split
  suppresses the name. The corporate-action block covers only −5 to +7 days
  (`docs/corporate-actions.md:104-110`) and never covers dynamic, PEAD or operator names.
- **DA3 — A silent cross-vendor fallback** feeds the gates with auto-adjusted, consolidated-volume
  frames. No live consumer checks `attrs["feed"]` (maps/data.md G3).
- **DA4 — Survivorship and security identity. [revised; added in revision]**
  - Every cohort is current membership, so discovery (2016-08 → 2021-12) is conditioned on survival
    to 2026.
  - Identity is keyed on today's ticker:
    - bar requests never set `asof` (`providers.py:243-253`; it appears only in the evidence
      allowlist at `agentic_trader/data/evidence.py:24-27`);
    - the research cache key is `{symbol}_{timeframe}` (`agentic_trader/research/setups/runner.py:172`);
    - the event lane uses today's symbol (`agentic_trader/research/pooled/events.py:8-10`).
  - The insider map is current-only. Measured in review on the cached `company_tickers.json`
    (10,443 rows): none of the 21 dead or renamed tickers the spike probed is present, FB is absent,
    FISV maps to Fiserv, and Q maps to Qnity.
  - Dead names would get NaN insider fields, so they would silently leave the deciles even in a
    point-in-time cohort.
  - **Delisting returns are dropped.** `label_bracket` returns IMMATURE when the bars end
    (`agentic_trader/research/setups/labels.py:318-327`), and the cube drops IMMATURE cells
    (`agentic_trader/research/pooled/cube.py:292-298`). This is the CRSP delisting bias (Shumway
    1997; Shumway & Warther 1999).
  - maps/data.md I5 splits the repair at 2022 ("Alpaca inactive list for 2016–2021, EDGAR from
    2022"). That is the wrong axis: names alive in discovery that died after 2022 need a source in
    every discovery year.
- **DA5 — No shared incremental store.**
  - A 1,000-name cube took about a day of sequential requests at about 2.3 s each, roughly
    26 requests/min against a Basic-plan limit of 200/min (`docs/alpha-pooled-cohort3-2026-10-16.md`
    "Building the cube").
  - The evidence store grew to 9.2 GB in 33 days with no retention policy (maps/data.md G5).
- **DA6 — Options state is lost every session. [added in revision]**
  - Alpaca's free option history starts in February 2024 and is trade-based. Yahoo serves current
    chains only (Alpaca docs, "Historical Option Data").
  - The data assessment calls options "paid … defer"
    (`docs/alpha-data-augmentation-2026-10-18.md` §2 row 9). The Basic plan includes the indicative
    options feed and historical calls (Alpaca docs, "About Market Data API").
  - The existing normalizer would fabricate skew if reused for research (see **Now**).
- **DA7 — Accounting data is absent from the code, the maps and the roadmap. [added in revision]**
  - The desk ranks EDGAR XBRL as source #4 (`docs/alpha-data-augmentation-2026-10-18.md:45,91-92`).
    It has no spec.
  - The SEC Financial Statement Data Sets are as-filed, carry `accepted` timestamps and the
    `prevrpt` amendment flag, and start in 2009. They meet the prerequisites that earlier docs
    imposed (`docs/alpha-factor-research-plan.md:41-47`; `docs/alpha-stack-survey-2026-09-18.md:206-207`).
  - The trap is the XBRL `frames` API: it returns the *last-filed* value, which is lookahead.
  - The insider acquisition already holds nearly all the plumbing: the quarterly zips, the paced
    User-Agent client, the digest store and `symbol_ciks` (`insider_filings.py:59-61,134-280,354,574-596`).
- **DA8 — Macro inputs come from unofficial sources and are never recorded.**
  - The macro layer keeps only the latest values (`docs/alpha-data-augmentation-2026-10-18.md` §1,
    Macro row).
  - The scan's regime block records no sources, observation dates or raw inputs
    (`regime.py:58-100`).
  - FRED's ICE BofA notes reportedly limit those series to a rolling 3 years from April 2026. This
    was seen in search snippets only; FRED was in maintenance during the review. See §3.6 for the
    failure modes.
- **DA9 — Research-only event data has no live path.** FINRA and EDGAR fields exist only behind
  research CLIs (maps/data.md G6).
- **DA10 — Reference data is missing.**
  - Sectors exist only as hand labels for the 159 live names.
  - There are no shares outstanding, so no size or turnover.
  - There is no per-name cost: the cube uses a flat 5 or 15 bp (`cube.py:86`).

**Recommend**

- **DA-R1 [M].** Native-v2 delayed SIP on a session-anchored clock (= SG-R2). Keep IEX only for
  latest-trade checks.
- **DA-R2 [S].** Use `adjustment="split"` for indicators, ATR and covariance, and keep raw prices
  only for the price floors (the pattern at `agentic_trader/research/pooled/cohort.py:205-211`).
  Capture structural corporate actions market-wide; that is about 40 per 10 days
  (`docs/corporate-actions.md:39-41`, "Data").
- **DA-R3 [S].** Fail closed on the data contract live:
  - exclude yfinance frames from the gates, or disable the fallback for equities;
  - replace `fetch_latest_price` with an Alpaca snapshot that has a maximum age.
- **DA-R4 [M; start now] — An options capture worker. [added in revision]**
  - Model it on `CorporateActionsWorker` (`agentic_trader/data/corporate_actions_worker.py:30-68`):
    once per New York session, post-close (16:20–20:00 ET). A missed session stays missing.
  - Symbols: cohort v2, the universe and held equities.
  - For each underlying, make one paginated `GET /v1beta1/options/snapshots/{underlying}`, filtered
    to 7–70 DTE and strikes 0.75–1.25 × spot. Take open interest from `/v2/options/contracts`.
  - Pace at 100 requests/min or less. That is about 500–1,000 requests, or 5–10 minutes, outside
    every scan window.
  - Store raw gzipped pages with SHA-256s under `artifact_directory()`, plus a per-session digest in
    the `ShortVolumeStore` style (`short_volume.py:132,194`).
  - Compute our own summaries, with NaN on missing, crossed or stale quotes (never 0.20):
    - XZZ skew;
    - the Cremers–Weinbaum IV spread;
    - 30-day ATM call and put IV;
    - put/call volume and option/stock volume;
    - open interest.
  - Run a one-week agreement check of the skew ranks against yfinance on about 30 names. Freeze the
    feed (`indicative`) into the series identity.
- **DA-R5 [S, decision D8] — Price the point-in-time pack before building it. [added in revision]**
  - The free route is Form 25/15, heuristic CIK-to-ticker history, SIC from EDGAR submissions and
    XBRL shares. That is about 4–6 SDD-days at the doc's one day per module
    (`docs/alpha-data-augmentation-2026-10-18.md:54-58`). It still has no authoritative source for
    the ticker a dead CIK traded under.
  - **Sharadar.** Vendor pages checked 2026-10-21 give $499/yr for full history: 21,000 active and
    delisted tickers from 1997 keyed by permaticker; SIC; first and last price dates; delist and
    ticker-change actions; point-in-time fundamentals; insiders from 2005.
    - Evaluate it for one $69 month: verify the personal-use and retention terms, and run a
      fail-closed coverage audit (all 22 spike names, ≥ 95% of the 2,147 Alpaca inactive names with
      bars).
    - If it passes, drop the free Form 25/15, SIC and XBRL builds. Then run one descriptive read of
      the discovery leg mean on point-in-time v2 against current v2.
    - The 10-year tier starts around October 2016, after the discovery start, so buy full history.
  - **Norgate** costs $630–787.50/yr and needs Windows, which does not fit the macOS desk.
  - **EODHD** has only end-of-day prices for names delisted before 2018.
- **DA-R6 [M; prerequisite for any point-in-time cohort, whatever the source] [added in revision].**
  - A `SecurityId` (permaticker, or CIK plus listing interval) for cohorts, cube columns, field
    frames and cache keys.
  - Bars fetched per (ticker in use, `asof` = end of interval), with the cache keyed
    `{security_id}_{ticker}_{asof}`.
  - Each series checked session by session against an independent unadjusted close, failing closed
    above a tolerance.
  - Regression fixtures for Q, FISV → FI → FISV, SBNY, SQ → XYZ and FB → META.
  - Event and insider sources mapped by security id.
  - An explicit `DELISTED` outcome in `label_bracket`, using the last close or deal price for cash
    mergers and a declared proxy otherwise. Shumway & Warther's −55% applies to Nasdaq; verify any
    NYSE/AMEX figure against Shumway 1997 before freezing it.
  - IMMATURE and DELISTED counts reported by year.
- **DA-R7 [M, only if Sharadar is declined] [added in revision].** A research-only
  `alpha xbrl-statements` acquisition of the FSDS quarterly zips, cloned from the insider store.
  - Usable value: the minimum `accepted` row per (cik, tag, ddate, qtrs), visible after
    `accepted < D−1 close`. Amendments become visible only at their own `accepted` time.
  - Never use `frames`.
  - A frozen tag-priority list for asset growth, ROE, gross profitability and net issuance. ASC 606
    switched the revenue tags in 2018.
  - `sub.txt`'s `sic` column also provides point-in-time SIC.
  - A live path would need the nightly `companyfacts.zip` plus a parity test against FSDS.
- **DA-R8 [S → M] — Record and backfill macro history.** Journal the raw decision inputs, sources and
  observation dates on every scan (RM-R3), so the desk starts owning its HY OAS history. Build the
  research-only `alpha macro-history` acquisition described in RM-R3.
- **DA-R9 [M].** Before a warehouse, parallelise research bar fetches up to the 200/min limit, and
  extend the cohort v2 and v3 cubes incrementally past 2026-07-31 for the forward job (MI-R4).
  Compress evidence pages after N days and keep the manifests forever.
- **DA-R10 [S; rank last] — Alpaca Algo Trader Plus ($1,188/yr). [added in revision]** It fixes
  neither survivorship nor parity.
  - Replay uses only bars that ended at 10:00 or 14:00 (`replay.py:17-24`), and scans run at 10:35
    and 14:35, so delayed SIP already serves every bar replay uses.
  - The one consumer the plan would improve is admission's IEX quote and trade:
    - a 60 s freshness limit (`agentic_trader/execution/capacity.py:233-234`; `config.py:573`);
    - the IEX ask as the capacity price (`capacity.py:267-292`).
  - Tabulate two weeks of persisted `broker_context` (`agentic_trader/storage/workflow.py:606-612`):
    quote and trade age, spread, fill minus ask, and "stale" refusals.
  - Predeclare the purchase rule, for example staleness refusals above 5% of taps, or a median fill
    more than 10 bp above the IEX ask.

### 3.4 Alpha mining: pooled lane, per-symbol DSL lane and a priori catalog

**Now**

- **Pooled lane.**
  - `CampaignProtocol` (`agentic_trader/research/pooled/campaign.py:246-401`) has **four [revised]**
    data-source subclasses, each declared "fixed_set in v1":
    - `IntradayCampaignProtocol` (:403);
    - `EventsCampaignProtocol` (:451);
    - `ShortVolumeCampaignProtocol` (:494);
    - `InsiderCampaignProtocol` (:555).
  - Windows are identical across every protocol: discovery 2016-08-01 → 2021-12-31, selection
    2022-01-03 → 2023-12-29, confirmation 2024-01-02 → 2026-07-31 (`campaign.py:87-99`;
    `config/research/pooled/campaign-v2.json:10-22`).
  - Every cell is labelled with one long bracket: decision at 10:35, a 2×ATR14 stop, a 3R target and
    20 sessions (`cube.py:57-71,288`; `config/research/pooled/insider-v1.json:16`).
  - The decisive statistic is the paired edge of the top-decile picks against every eligible name
    (`agentic_trader/research/pooled/stats.py:54-105,142`).
  - Charges are written before scores (`agentic_trader/research/pooled/ledger.py:82-87`). The
    confirmation window is consumed **lane-wide**, once (`agentic_trader/storage/alpha.py:1141-1155`;
    `docs/alpha-pooled-mining.md:616-622`).
- **Per-symbol DSL lane.**
  - Scores are z-scored over a trailing 30-bar window (`strategy.py:26,144-147`).
  - Qualification goes through the lifetime `family/all` DSR (`agentic_trader/research/alpha/promotion.py:351-388`;
    `storage/alpha.py:185-251`).
  - The weekly miner is retired (`scripts/launchd.sh:21`).
- **A priori catalog.** PEAD long passed and runs as a $100 probe with a 30-day term
  (`config.py:829-831`). Exdiv failed. The spread lane is closed.
- **Insider lane.** Check A was at replicate 18/100 on 2026-10-21. The campaigns need approval.

**Gaps**

- **MI1 — Power is below plausible effects** (§2.2 item 4).
  - The lane's own translation of AlphaAgent's S&P 500 IC (0.0056) is about 0.02R, against a
    detection floor of about 0.08R (`docs/alpha-pooled-mining-research-2026-09-28.md:51,94`).
  - The 64 nulls rule out edges above about 0.06R and nothing smaller. Result docs report no
    per-formula interval or MDE.
- **MI2 — Check A's curve is not each formula's power.** The planted score is AR(1) with φ = 0.95
  and a constant shift (`agentic_trader/research/pooled/power.py:89-107,206-233`). Real formulas'
  standard errors span 7× (maps/pooled.md §6.2).
- **MI3 — The control is not matched for risk.**
  - The cube stores no ATR%, beta, sector or size array (`_ARRAYS`, `cube.py:52`).
  - Decile tails of almost any characteristic are high-volatility names. In the R label, the cost and
    beta-one terms penalise or favour them mechanically (SG2).
  - A low-volatility effect (Ang–Hodrick–Xing–Zhang 2006; Frazzini–Pedersen 2014) is a plausible,
    untested explanation for v2's mean t of −0.59. The v4 label diagnostic says the same: deciles
    "hold high-volatility names" (`docs/alpha-pooled-checks-v4-2026-10-07.md` "Label diagnostic").
- **MI4 — A horizon-matched cube is not a config change; the label slice silently drops long
  timeouts. [added in revision; defect]**
  - The cube hands the labeller only bars up to the decision plus 45 calendar days
    (`_LABEL_SPAN`, `cube.py:50-51`; `_Hourly.around`, `cube.py:231-234`).
  - `BracketSpec` accepts any `max_hold_sessions ≥ 1` and checks it against nothing (`cube.py:56-61`).
  - When the bars run out, `label_bracket` returns IMMATURE (`labels.py:322-327`), and the build
    skips the cell (`cube.py:296-298`).
  - `check_coverage` counts only `no_hourly_bars` and `no_decision_price` as gaps (`cube.py:349-350`).
  - The D..D+45 window holds 29–34 NYSE sessions (median 32, measured over 2016–2026). So any
    `max_hold_sessions` above about 28 keeps only cells that hit stop or target early, which selects
    on the outcome.
  - maps/pooled.md §7.3 ("a configuration change, not a redesign") is wrong.
  - The setup protocols hard-code a 30-day maturation (`agentic_trader/research/setups/recurrence.py:50`)
    and drop immature rows (`agentic_trader/research/setups/study.py:468-471`), with the same bias
    for any longer hold.
- **MI5 — Pooled PEAD is a weaker encoding than the design that passed** (maps/pooled.md §6.5). The
  pooled lane has no event-entry rule and no event-matched control.
- **MI6 — The overlap rule misses the a priori reads.** `LITERATURE_ENTRIES` lists only two pooled
  entries (`agentic_trader/research/pooled/campaign_run.py:107`). PEAD and exdiv both read the
  confirmation window.
- **MI7 — The single confirmation window has no spending rule, and the next consumer is about to be
  approved. [revised]**
  - Null campaigns reached confirmation in 1 of 40 replicates for the intraday fixed set and 4–5 of 40
    for genetic search (`docs/alpha-pooled-intraday-v1-2026-10-14.md`; `docs/alpha-pooled-checks-v3-2026-10-05.md`).
  - Any insider finalist would consume 2024-01 → 2026-07 for every future lane. That would happen
    before the composite (maps/history.md §7.3) or a prospective interval (maps/pooled.md §7.6)
    could use it.
- **MI8 — Fixed sets reuse a gate built for search**: t ≥ 3, 3 of 4 blocks, a 0.5× haircut, then
  Holm. The leg-ci90 confirmation rule mixes 2024–26 drift into an alpha gate (`campaign.py:732-739,789-794,833-842`).
- **MI9 — Long-only, single-formula, with no combination stage.** Negative edges that replicate in
  sign across cohorts are discarded: `quiet_momentum` t −3.34 / −1.69, `intraday_high_proximity`
  −0.59 / −2.33, `overnight_persistence` −1.93 / −1.18 (maps/pooled.md §6.9).
- **MI10 — A new source costs about 10 touch points, and the cube takes one source at a time**
  (`agentic_trader/research/pooled/runner.py:317-318`).
- **MI11 — No cross-sectional operators or sectors in the grammar.** The operators are listed at
  `agentic_trader/research/alpha/operators.py:108-170`; the fields at
  `agentic_trader/research/alpha/dsl.py:28-48`.
- **MI12 — Per-symbol qualification is practically unreachable** (maps/alpha_pipeline.md G2–G3).
  - The family DSR deflates the holdout to a per-bar Sharpe of about 0.31, roughly 4.9 annualized.
  - Holdout consumption is keyed by symbol only (`storage/alpha.py:221`).
  - Promotion requires the active set to equal the incumbent snapshot taken at mining time
    (`storage/alpha.py:322-326`).
  - Qualification expires after 45 days (`config.py:827`), while 10 decisions take about 75–150
    sessions.
  - Daily bars are stale on Mondays and after holidays (`agentic_trader/research/alpha/clock.py:47-50`).
  - A GTC probe is not refused (`storage/alpha.py:427-428`).
- **MI13 — The shadow lane is blind.** All 10,359 forecasts are invalid and no session decision has
  been scored (Q13). The promotion path cannot be reached on the IEX deployment feed.
- **MI14 — Cross-asset time-series momentum has never been tested, and no lane can express it.
  [added in revision]**
  - **Per-symbol DSL.** Its z-normalisation uses `std.where(std > 0)` (`strategy.py:144-147`), so a
    persistent `sign(roc(close, 252))` scores NaN.
  - **Pooled lane.** It is cross-sectional top-k on 452 single stocks with no ETFs
    (`campaign-v2.json:24-30`; `cohort-v2.json`).
  - **Persistent book.** It requires zero-net targets (`agentic_trader/research/alpha/persistent_book.py:104-107`)
    and pays zero on cash (:73-76).
  - **A priori catalog.** It is event-shaped.
  - The native trend filter (daily close > EMA50 > EMA200, `strategies.py:99`) is harvested through
    pullback brackets with a mean hold of 5.6 sessions. The literature places the trend premium at
    1–12 months.
  - TSMOM on the ETF32 is **not** beta-free. 22 of 32 members are equity (`config/config.yaml:98-129`),
    and Goyal & Jegadeesh (2018) attribute most of TSMOM's edge over cross-sectional momentum to its
    time-varying net-long exposure.
- **MI15 — LLM formula proposers need guards the protocol cannot express. [added in revision]**
  - Today the only proposer is `TypedGeneticSearch` (`agentic_trader/research/pooled/genetic.py:147`).
  - Each campaign dedups only against itself (`genetic.py:239`), and resume assumes a deterministic
    proposer (`storage/alpha.py:1047-1052`).
  - `CampaignProtocol` (`campaign.py:246-289`) and the immutable reservation fields
    (`storage/alpha.py:40`) carry no proposer identity.
  - Every window predates the knowledge cutoff of any usable LLM.
  - The roadmap plans RD-Agent(Q) "behind our existing DSL, trial ledger and validator"
    (`docs/alpha-roadmap.md:865-867`) with no cutoff constraint.
  - An iterative LLM loop is adaptive search, which is the parked `genetic` mode.
- **MI16 — Mining has no cadence, and four overlapping "prospective scorer" proposals exist.
  [revised]** The proposals are maps/architecture.md §7.8, maps/history.md §7.2,
  maps/alpha_pipeline.md I5 and maps/data.md I4. Power at a familywise one-sided 5% over about
  80 pairs (z ≈ 3.2), with 80% power:

  | Effect | Per-session SD | Sessions needed |
  | --- | --- | --- |
  | 0.05R | 0.17R | about 190 |
  | 0.02R (the largest discovery edge seen) | 0.17R | about 1,190 (4.7 years) |
  | 0.10R | 0.74R | about 900 (3.6 years) |

  The SDs come from `docs/alpha-pooled-short-v1-2026-10-20.md:94` and
  `docs/alpha-pooled-mining-research-2026-09-28.md:85-94`. maps/history.md §7.2's "about 0.10R in
  two years" holds only for a single a priori formula.
- **MI17 — Performance.** Python-lambda rolling operators are about 100× slower than vectorised ones:
  `ts_rank` takes 7.0 ms against 0.07 ms for `ts_mean` (`operators.py:15-19,138-152`).

**Recommend**

- **MI-R1 [decision D6, before approving the insider campaigns].** Fix the confirmation-window rule.
  There are three options:
  - (a) the first finalist spends it (status quo);
  - (b) reserve it for one predeclared composite with literature-derived signs (MI-R5);
  - (c) confirm prospectively from 2026-08-01 through MI-R4.

  Extend the overlap rule to every journaled study whose inspected interval overlaps, including
  PEAD and exdiv.
- **MI-R2 [S] — Fix the label span before any horizon-matched cube. [added in revision]**
  - Derive the slice from the spec: `max_hold_sessions + 1` sessions on the observed calendar, or
    `ceil(max_hold × 7/5) + 10` days.
  - Add a `BracketSpec` validator.
  - Count as a coverage gap any immature cell more than `max_hold_sessions` before `bars_through`.
  - Add a regression test: a flat series with `max_hold_sessions = 40` must label TIMEOUT.
  - Derive `_MIN_MATURATION_DAYS` from the hold.
  - Existing 20-session cubes keep their identity.
  - Pair this with forward-return and residual arrays (maps/data.md I7), so monthly characteristics
    can be tested without a bracket.
- **MI-R3 [M] — A risk-matched control, and per-formula power.**
  - Add `atr_pct`, `beta_60`, log dollar volume and SIC sector arrays to the cube. Rebuilding from
    cached bars is enough.
  - Make the decisive statistic the pick residual after a per-session regression on those exposures,
    or a DGTW-style matched cell (Daniel–Grinblatt–Titman–Wermers 1997).
  - Plant δ on each real formula's picks within check C to report a per-formula MDE and an
    equivalence bound. A null should then read "edge < X R at 90%".
- **MI-R4 [M] — One `alpha pooled-forward` spec replaces the four scorer proposals. [added in
  revision]**
  - Run it as a launchd research job outside the daemon, monthly or quarterly. The daemon already
    shares executor capacity with research (`docs/architecture-review.md:96`).
  - Extend the cohort v2 and v3 cubes incrementally past 2026-07-31.
  - Re-score all frozen pairs and their every-eligible controls, and journal picks and label
    maturity.
  - Fix the read rule *before* the first run. Either:
    - one read at about 2027-08 with Holm over the family; or
    - per-formula always-valid confidence sequences (Howard et al. 2021) with Lan–DeMets alpha
      spending.
  - The daemon captures receipt-time evidence only for inputs whose historical fetch may differ
    from what was visible on the day (the Nasdaq calendar).
  - State plainly that this accrues evidence over years.
- **MI-R5 [M, only if D6 reserves the window] — Combination as one trial.**
  - Add `cs_rank`, `cs_zscore` and `group_demean`.
  - Freeze a single equal-weight composite whose field signs come from the literature (reversal,
    52-week high, PEAD, short ratio, insider buying), never from observed t.
  - Use Chen–Zimmermann OSAP definitions and Jensen–Kelly–Pedersen themes as priors.
- **MI-R6 [M].** An event-entry selection rule: enter on the first session the condition turns
  true, skip held names, and use an event-matched control. Pair it with a `FieldSource` plugin
  registry that supports multi-source frames (MI10).
- **MI-R7 [S; decision D1 by 2026-10-28] — PEAD probe.**
  - Renew it for 180 days (`MAX_PROBE_TERM_DAYS = 180`, `agentic_trader/research/alpha/probe.py:20`;
    `alpha apriori-probe --renew`, `agentic_trader/cli/commands/alpha.py:540-560`) at unchanged risk.
  - Judge it on the counterfactual label over every eligible event
    (`agentic_trader/research/apriori/probe_outcomes.py:96-129`), not on taps. The 10-trade floor
    (`docs/apriori-alphas.md` "Part 2") cannot be reached at one fill per four cards.
  - Raising risk to parity with native cards, or giving PEAD both slots, needs a separate spec.
- **MI-R8 [M] — TSMOM-v1 a priori entry. [added in revision]**
  - **Freeze first.** Freeze `config/research/apriori/tsmom-v1.json` before reading any bars, and do
    not look at the cached SIP ETF bars first.
  - **Universe.** ETF32 minus BIL, which is the T-bill reference. Declare SHY as cash or risky
    before freezing.
  - **Signal.** The sign of the trailing 252-session total return in excess of BIL, observed at the
    month-end close and traded at the next open (Moskowitz–Ooi–Pedersen 2012).
  - **Weights.** Inverse 60-session volatility with equal risk per sleeve (US equity, sectors,
    international, rates/credit, metals). Long/flat, reset monthly, at 1 and 5 bp per side.
  - **Window.** 2017-01 → 2026-08. A listing becomes eligible after 252 of its own bars.
  - **Controls:**
    - C1: a volatility-scaled always-long book, because Kim–Tse–Wald (2016) attribute most of
      TSMOM's alpha to volatility scaling;
    - C2: a historical-mean sign control (Huang et al. 2020);
    - C3: SPY and a 60/40 portfolio, descriptive only.
  - **Pass rules:**
    - P1: the paired monthly difference from C1, by stationary block bootstrap, one-sided;
    - P2: full-sample beta to SPY ≤ 0.3;
    - P3: mean return in SPY's worst-quintile months > 0.
  - **Power.** About 116 months at a paired Sharpe of 0.4 gives t ≈ 1.25, roughly one-in-three
    power. Run a planted-effect check first.
  - **Implementation.** Add a net-long mode and a cash yield to `simulate_book` rather than a new
    engine. Use pysystemtrade (GPL-3.0) only as an external audit reference.
  - Measure the result at book level, never with per-card SPY excess (RM2).
- **MI-R9 [M] — LLM proposers only as one-shot fixed-set authors. [added in revision]**
  - Run the proposer outside the daemon. Pin a model snapshot (not the `openai/gpt-4o` alias,
    `config.py:871,989`) at temperature 0, with no tools and no data.
  - Pin a `proposer` block {model_snapshot, knowledge_cutoff, prompt_sha256, response_sha256} inside
    the protocol, so `protocol_sha256` covers it.
  - In `CampaignProtocol._consistent` (`campaign.py:291-321`):
    - refuse any `proposer` unless `windows.confirmation[0] ≥ cutoff + 180 days`;
    - refuse `genetic` mode.
  - A 2025–26 frontier model fails that guard, so it needs the forward scorer (MI-R4).
  - Vet originality against every expression charged lane-wide, using AlphaAgent's largest common
    AST subtree, before any charge.
  - Keep expectations low: about 0.02R. The likely value is in deciding which new fields to acquire.
- **MI-R10 [M] — Per-symbol lane: either repair it or retire it explicitly.**
  - Repair means a validation policy v2: family-corrected selection on validation, then a one-sided
    block-bootstrap holdout test of the frozen finalist (the A1b comparator recovered 12–28 of 64
    planted cases against 0 of 64; `docs/alpha-stack-survey-2026-09-18.md:97-101`). It also means
    (symbol, family) holdout keys, per-symbol incumbents, calendar-aware daily freshness and
    `semantics_version == 5` for probes.
  - Otherwise, retire the lane by tag (maps/alpha_pipeline.md I2, I6, I7).
- **MI-R11 [S].** Key check A on (cube SHA, selection, gates, bootstrap, stage-code digest), which
  saves 2–3.5 h of 6-core compute per protocol. Vectorise the rolling operators (Qlib's expression
  engine is the template).

### 3.5 Research methodology and measurement

**Now**

- **`card_stats`.**
  - It drops immature rows (`card_stats.py:91-92`).
  - It does not de-duplicate (`card_stats.py:112`).
  - It reports raw `mean_r_cost`, keyed by (strategy, direction), not timeframe
    (`card_stats.py:50-62`).
  - `card_policy` withholds when `mean_r_cost < min_measured_ev` (`card_policy.py:47-55`).
- **Market control.** It is beta-one (`outcomes.py:187-221`).
- **Labels.** Entry is at the first regular hourly open at or after the decision, with a 20-session
  timeout and 5 bp per side (`labels.py:180-187,252-261`). Live entries are GTC limits with no
  timeout and a one-minute ratchet (maps/execution.md G2).
- **R caps.** R is winsorised at ±10 only in the counterfactual (`bracket_counterfactual.py:172-177`).
  The pooled and a priori statistics are uncapped; the GPOR +1,546R cell shows why that matters
  (`docs/setup-recurrence-2026-10-14.md` "Robustness window").

**Gaps**

- **RM1 — The live estimator behind cards and `card_policy` is biased three ways and swings sign**
  (§2.2 item 1).
- **RM2 — The beta-one SPY control distorts the main yardstick** (§2.2 item 2).
  - It penalises low-beta, tight-stop and long-hold cards.
  - It has already put two misleading descriptive sentences into the record: the
    bracket-counterfactual doc's "Descriptives" and the CLAUDE.md summary of v1.
  - Any cross-asset card would be mis-scored the same way. Examples are TLT, IEF and GLD, or the
    TSMOM sleeves of MI-R8.
  - **[added in revision]**
- **RM3 — The regime gates change most replayed sessions and have never been replayed** (§2.2
  item 3).
  - The scan's regime block (`regime.py:58-100`) records no input sources, observation dates,
    `macro_unavailable_reason` or calendar status.
  - So the `by_macro_stress` bucket in `cards outcomes` cannot tell a feed failure from a calm tape.
    `/macro` does audit these (`copilot.py:4432-4446`); scans do not.
  - **[added in revision]**
- **RM4 — Text signals have no scorer-vintage contract. [added in revision]**
  - Benzinga history is cached back to 2015 (`docs/news-context.md:154`).
  - The next frozen entry named in that doc is "post-news drift conditioned on tone"
    (`docs/news-context.md:179`).
  - The only scorer is the live LLM on a moving alias (`news_context.py:290`; `config.py:871,989`).
  - Replaying it over 2016–2023 headlines measures memorised outcomes (Glasserman & Lin 2023;
    Lopez-Lira, Tang & Zhu 2025).
  - The live news blocks do pin model and prompt (`news_context.py:166-169`). Since 2026-10-13 they
    form a sample that falls after the cutoff.
- **RM5 — `llm_verdict` has no identity** (SG5).
- **RM6 — The operator tap is unmeasured** (CS6).
- **RM7 — A fundamentals conditioning test is underpowered for main effects. [added in revision]**
  - Recurrence R1's CI implies an SE of about 0.044R (`docs/setup-recurrence-2026-10-14.md`
    "Hypotheses"). A tercile split of about 8.8k setups therefore has an MDE of about 0.11R.
  - Ken French's 2×3 five-factor data for 2016-01 → 2026-08 (`ff5_power.py`): RMW +0.21%/month
    (t 0.92), CMA −0.01%/month (t −0.05). Over 1963–2026: RMW t 3.03, CMA t 3.23.
  - Half the RMW spread is about 0.025R per 20-session label, a quarter of the MDE.
  - The setup tests resample sessions only (`recurrence.py:215-249`; `study.py:143-167`). A
    fundamental tercile is a nearly fixed set of names for years, so the CIs would be too narrow.
- **RM8 — Labels describe a policy the desk does not trade** (§3.8 EX2).
- **RM9 — Prospective reads have no power plan.** The `vol_20` window gives SE(ρ) ≈ 0.15 (SG2). The
  PEAD 10-trade floor is unreachable by tap (MI-R7).

**Recommend**

- **RM-R1 [M; decision D3] — `card_stats` v2. [added in revision; supersedes maps/live.md §7.1]**
  - Use time-matured cohorts: only decisions at least `max_hold_sessions` (20) old. This removes the
    censoring swing exactly, with no survival model. Aalen–Johansen is needed only if timeliness
    matters later.
  - Keep one row per (contract, strategy, timeframe, direction, New York session), from suggestion
    triggers only.
  - Use a beta-adjusted excess R with an explicit per-name cost, shrunk toward the replay prior.
  - Only then run `card_policy` in preview, with its threshold on that unit.
  - If enforcement ever reads daily peeks, use always-valid confidence sequences (Howard et al.
    2021; the `confseq` package).
- **RM-R2 [S] — Fix the yardstick. [added in revision]**
  - Add a beta-matched control beside the raw column: a past-only trailing 120-session daily OLS
    beta, frozen at the decision.
  - Add an own-asset buy-and-hold control in R to measure timing value.
  - Report excess by asset class in `cards outcomes` and every future setup study.
  - Publish a **descriptive erratum** to `docs/bracket-counterfactual-2026-10-18.md` and the CLAUDE.md
    summary, giving the beta-matched and ex-rates figures. The frozen `no_change` remains the
    decision of record.
  - Remove BIL, and arguably SHY, from the native scan universe. A 1.5× 4h-ATR stop on a T-bill fund
    implies 650–2,000× notional per unit of risk.
- **RM-R3 [M] — Macro history and a regime-gate replay. [added in revision]**
  - Build a research-only, digest-pinned `alpha macro-history` acquisition:
    - Cboe `VIX_History.csv` (1990 onward);
    - Treasury par yield curve CSVs;
    - FOMC, BLS and BEA dates;
    - ALFRED vintages for FRED series;
    - HY OAS for whatever window FRED still serves.
  - Add a pure `regime_at(session, scan_time)` that reuses `classify_vix_level` and
    `compute_compound_macro_stress` on inputs visible strictly before the decision.
  - Predeclare a replay paired by setup, with three arms: ungated, full live gates and VIX-only.
    Score it on R per session net of a beta-matched control. Run it on native-v2 bars.
  - Until then, label the replays' "live" arm as **ungated**.
  - Extend `regime_journal_block` with sources, observation dates, raw inputs and a calendar
    sub-block now, at small effort, so the desk owns its own history from today.
- **RM-R4 [M] — A text-scorer contract. [added in revision]**
  - Every frozen text-field spec must declare {scorer_model_snapshot, knowledge_cutoff,
    prompt_sha256}.
  - A validator refuses any window that starts before cutoff + margin, unless the scorer is
    chronologically consistent: ChronoBERT/ChronoGPT vintages (He et al. 2025) or a vintage-pinned
    dictionary such as Loughran–McDonald.
  - Build the first news study prospectively on journaled live blocks. If wanted, add a separate
    historical replay with a point-in-time scorer.
  - For filings text, copy Kim, Muhn & Nikolaev (2024): anonymised inputs, with the decisive test
    after the cutoff.
- **RM-R5 [M] — Fundamentals as a junk-tail veto, not as another pooled lane. [added in revision]**
  - Freeze `setup-fundamentals-v1` before reading data.
  - Tag each replayed native setup with as-filed terciles on its D−1 date (ROE, gross
    profitability, asset growth, net issuance) through a new features version on the existing
    `CrossSection` hook (`agentic_trader/research/setups/features.py:221-248`). That hook is shared
    with the live shadow ranker (`copilot.py:1820-1831`).
  - One directional hypothesis: junk-tail setups have lower R_cost.
  - Use a joint name-cluster and session-block bootstrap, with a check-C-style placebo that permutes
    characteristic paths across names.
  - Print the MDE (about 0.1R).
  - Confirm on the unread one-shot holdout, 2026-03-02 → 2026-08-14
    (`docs/setup-recurrence-2026-10-14.md:20`).
  - A pass makes only a card-construction rule eligible, through `card_policy`'s preview → enforce
    path.
  - Do **not** open a pooled fundamentals lane on the 20-session 3R label.
- **RM-R6 [M] — One labeller kernel (L5).** Either give native positions the label's 20-session
  timeout through `SessionLifetimePolicy`, or drop the timeout from the labeller. Label the entry
  type the desk actually uses (EX-R2).
- **RM-R7 [S].** Floor the risk unit (for example ≥ 0.5% of price) and winsorise R in the pooled
  and a priori statistics. Move the bootstrap, CI, Holm and trimmed-mean helpers into one public
  `research/stats.py`; private helpers are imported across packages today
  (`agentic_trader/research/pooled/stats.py:25-26`).
- **RM-R8 [S].** Require a written power statement for every prospective read before it starts.

### 3.6 Risk policy, regime inputs and admission

**Now**

- **Pure rule core.** `RiskLimits` (`agentic_trader/risk/limits.py:50-120`), 18 rule ids
  (`rules.py:62-82`), and the fixed-order composites `book_gates` (:470-488) and `admission_gates`
  (:491-513).
- **Per-trade budget.** `min(cash, equity) × max_risk_pct_cap × drawdown_factor × clamp(macro)`
  (`agentic_trader/risk/capital.py:69-93`).
- **Tiers.** Built in `calculate_dynamic_sizing` (`agentic_trader/agent/position_sizing.py:40-277`).
- **Regime.** VIX buckets set breakout permission and the minimum R:R (`regime.py:172-183`). The
  macro report sets the multiplier (`regime.py:185-189`).
- **Compound stress.** `compute_compound_macro_stress` scores VIX, the curve and HY OAS. An inverted
  curve alone gives HIGH stress: ×0.5 and no breakouts (`macro.py:207-280`).
- **Macro calendar.** ForexFactory's "this week" JSON (`constants.py:126`).

**Gaps**

- **RK1 — The $7,500 notional cap is the binding constraint, so the de-risking levers do nothing.**
  - A base tier is notional-capped whenever the stop is under 3.33% of price
    (`config/config.yaml:337,343`). That is 82.9% of replayed 4h setups (measured in review).
  - A probe of `calculate_dynamic_sizing` at a 1.5% stop: a drawdown factor of 0.5 alone, or macro
    ×0.5 alone, leaves every tier at 75 shares / $7,500 (maps/risk.md G1; `scripts/sizing_probe.py`).
  - The haircuts in `capital.py:45-66` are therefore mostly inert for ETFs and low-volatility names.
- **RK2 — The macro lockout fails open silently. [revised; added in revision]**
  - On a timeout, a non-200 response or a non-JSON body, `fetch_events` caches an empty list as a
    successful fetch for 30 minutes (`agentic_trader/agent/calendar.py:176-221`).
  - `is_in_lockout_window` reads that same list (`calendar.py:107-125`). So *every* deterministic
    lockout passes:
    - the evaluator's `macro_lockout` rule (`evaluator.py:526-541`);
    - the whole-scan lockout (`copilot.py:809-831`);
    - the tap and admission preflight (`copilot.py:3962-3987`).
  - The LLM is told "Lockout verified: CLEAR" (`calendar.py:156-160`). No readiness component covers
    the calendar (`agentic_trader/diagnostics/readiness.py:17-28`). The earnings path, by contrast,
    says "Unverified" (`docs/risk-policy.md:119-120`).
  - Only keyword titles gate. ForexFactory's "High" flag is collected (`calendar.py:203`) but never
    gates (`calendar.py:121`).
  - The window is 60 minutes before and 30 after (`config/config.yaml:277-278`). 08:30 releases
    therefore never reach a regular-hours scan.
  - Only FOMC afternoons matter: the 14:00 statement and the 14:30 press conference, whose window
    contains the 14:35 scan. That is about 8 sessions a year. The next two are **2026-10-28** and
    2026-12-09.
- **RK3 — Regime inputs fail open, or abort the scan. [added in revision]**
  - All ten macro inputs are mandatory (`macro.py:411-413`). If any one is missing, `get_regime`
    continues with `risk_multiplier = 1.0` and only the VIX rules (`regime.py:146-151,185-189`).
  - Five of the ten (5y, 30y, DXY, T10YIE, T5YIE) never affect the stress level. The `inflation`
    argument is accepted and never used (`macro.py:207-280`).
  - The toggles `credit_oas_enabled` and `inflation_breakeven_enabled` (`config.py:488-489`) are read
    nowhere.
  - **Live, 2026-10-21 17:04 UTC:** FRED served HTML "Site Maintenance" with HTTP 200, so all four
    FRED inputs parse as None. While that lasts, a book that should be at 0.25× or 0.50× trades at
    1.0×. `docs/risk-policy.md:59-61` says a multiplier is "never read as 1.0".
  - If VIX is unavailable, `get_regime` raises (`regime.py:168-169`) at an unguarded call
    (`copilot.py:780`), and the scan aborts with no cards. It also leaves:
    - no SCAN readiness observation (the component goes stale only after 18,000 s;
      `config.py:619`);
    - no `pead_decision` event, although `copilot.py:735` promises one for every decision;
    - no 14:35 digest, because `publish_scan_digest` follows `run_scan` without `try/finally`
      (`service.py:440-461`).
  - The curve slopes subtract FRED's prior-day DGS2 from Yahoo's live ^TNX (`macro.py:384-394,139-156`).
- **RK4 — The regime rules are uncalibrated hard gates.** Curve inversion, a multi-quarter recession
  indicator, halves risk and suppresses squeeze breakouts (`macro.py:149-150,255-259`). The 1h
  squeeze has the best measured base rate (`docs/setup-outcomes-2026-09-23.md` "Base rates"). §2.2
  item 3 shows these gates would bind on most sessions of the windows every lane was judged on.
- **RK5 — No market-beta or net-exposure concept.** The caps are on gross notional
  (`rules.py:303-329`). The beta and sector bound specified for L3 was not built
  (`docs/alpha-roadmap.md:521-523`).
- **RK6 — Dynamic names escape the correlation rule.** Groups are configured sectors and skip
  "unknown" (`config.py:163-169`). The statistical-correlation fallback is off (`config.py:397`).
- **RK7 — Gap and tail risk are unbounded.**
  - A −6% index day on a full $40k equity class at β ≈ 1.2 costs about $2.9k, roughly 2.9% of
    capital (maps/risk.md G4).
  - No scenario, gap or CVaR rule exists.
  - Held positions ride into earnings: the blackout runs only at entry.
- **RK8 — Capacity refuses instead of shrinking** (`position_sizing.py:95-96` against
  `rules.py:270-277,315-329`).
- **RK9 — Smaller issues** (maps/risk.md G8):
  - no joint `equity × dd × macro` check at admission (`agentic_trader/execution/admission.py:87`);
  - the probe risk cap is not an admission invariant (`workflow.py:478-526`;
    `docs/alpha-roadmap.md:160-163`);
  - `volatility_targeted` is mislabelled;
  - original `risk_dollars` persist after a stop ratchet (`agentic_trader/risk/book.py:71`).

**Recommend**

- **RK-R1 [S; ship before 2026-10-28] — A static, digest-pinned tier-1 calendar. [added in
  revision]**
  - Create `config/calendar/us-tier1-v1.json` holding:
    - FOMC statement and press-conference times for 2026–27, from the Fed's calendar page;
    - 08:30 CPI, PPI and Employment Situation dates, from BLS.
  - Load it through `StaticTier1Calendar(BaseEconomicCalendar)` and stamp its digest into every
    scan journal.
  - Give it a `valid_through` date:
    - with fewer than 30 days left, degrade a `macro_calendar` readiness component;
    - past that date, say "unverified" and apply a validated operator setting,
      `risk.macro_calendar_unknown: open|closed`.
  - Keep ForexFactory or the BLS iCal feed as a cross-check: lock out on the union and journal any
    disagreement.
  - Make `fetch_events` return a typed status, and never cache a failure as a success.
- **RK-R2 [S → M] — Regime inputs. [added in revision]**
  - Use Cboe `VIX_History.csv` as the primary source for the prior close (it needs
    `follow_redirects=True`), and Cboe's delayed quote intraday. Keep yfinance as the secondary.
  - Take every tenor from one source on one date: the Treasury par-curve CSV, or keyed FRED.
  - Make only the decision inputs mandatory: VIX, 3m, 2y, 10y and HY OAS.
  - When a decision input is missing, use an explicit `UNKNOWN` stress state, with a multiplier and
    breakout policy set in validated config (for example 0.5× and breakouts off), never 1.0.
  - When VIX is missing, journal `regime_unavailable`, call `_journal_skipped_drift`, observe
    SCAN = false and queue one outbox notice. Run the digest in `finally`.
  - Add a `macro_inputs` readiness component, and a test that pins "a non-decision input failure
    leaves the multiplier unchanged".
- **RK-R3 [S].** Make risk the binding constraint: apply `dd × macro` to the notional ceiling as
  well, or size to a constant ex-ante dollar volatility (Grossman & Zhou 1993; Harvey et al. 2018).
  Add a test that a 0.5 drawdown factor shrinks every tier.
- **RK-R4 [M].** A beta-dollar book view on each card ("book β$ +$X → +$Y"), and a `net_beta_dollars`
  rule run in preview. This becomes material once auto-entry fills the book (CS-R4).
- **RK-R5 [M].** Classify dynamic names by SIC (Sharadar, or EDGAR submissions) or by clustering the
  scan covariance, and retire the separate per-pair yfinance correlation fetches.
- **RK-R6 [M].** A scenario-loss rule in `risk/` (β-shock plus a per-name 99th-percentile overnight
  gap), rolled out preview → enforce. Add a held-position earnings notice through the planned
  advisory-exit card.
- **RK-R7 [M, after RM-R3].** Run the breakout suppression and the regime R:R in **preview** until
  the regime-gate replay passes. A momentum-crash conditioner (Daniel & Moskowitz 2016; Barroso &
  Santa-Clara 2015) is the evidence-based candidate.
- **RK-R8 [S].** Hygiene: size to the remaining headroom, add a joint per-trade check at preflight,
  make the probe cap an admission invariant, and rename `volatility_targeted`.

### 3.7 Sizing, book-aware sizing and portfolio optimisation

**Now**

- **Book volatility factor.** `book_vol_factor` is the closed-form largest `f ∈ [0,1]` that keeps
  book volatility within budget (`agentic_trader/risk/book_vol.py:72-111`).
- **`BookSizer`.**
  - It builds one Ledoit–Wolf covariance per scan from **raw** daily bars
    (`book_sizing.py:188-281`; `agentic_trader/research/setups/covariance.py:20-26,87`).
  - It prices the default tier (`book_sizing.py:339`).
  - It runs in `enforce` since 2026-10-20 (`config/config.yaml:466`).
- **Shadow optimiser.** It pins every weight (`book_sizing.py:434-445`), so it only re-verifies
  `w'Σw`.
- **Research allocator.** `ConvexAlphaPortfolioOptimizer` (`agentic_trader/research/alpha/optimizer.py:45-282`)
  is never reached from live.

**Gaps**

- **SZ1 — The volatility budget cannot bind under the current caps** (maps/portfolio.md G1). The
  equity class cap is $40k (`config.py:393`) against a budget of $800/day.
- **SZ2 — The caps are inconsistent.** 8 positions × $7,500 = $60k, but the equity class cap is
  $40k. The sixth full card is refused rather than shrunk.
- **SZ3 — R is not a constant dollar unit.** An ETF with a 1.8% stop risks about $135 against
  $250 nominal. Every R-space statistic weights setups equally; the desk's dollar P&L does not.
- **SZ4 — Size carries no conviction.** No live expected-return input exists.
- **SZ5 — The shadow optimiser carries no allocation information.**
- **SZ6 — Beta and sector exposure are not measured** (RK5).
- **SZ7 — The raw-price covariance is vulnerable to splits.** One split can push `vol_before` over
  budget and block every non-hedging card for up to 120 sessions (`book_vol.py:91-95`).
- **SZ8 — Tiers above the default are not checked against the budget**, and admission never
  re-checks `book_vol` (`docs/risk-policy.md:113-115`).
- **SZ9 — The book is valued at entry notional, not at marks** (`book_sizing.py:126-141`).
- **SZ10 — Selection ignores the book, and sizing depends on order.** **[revised]**
  maps/portfolio.md §7.6 proposes −Δvol as a tie-break. As a primary key on a near-empty book it
  selects the replay's worst tercile (SG2). Use it only per unit of R, and only when the book holds
  positions.
- **SZ11 — All of the above is moot while the book is empty** (CS5). **[added in revision]**
- **SZ12 — A trend leg cannot run under today's limits. [added in revision]**
  - `CATALOG_ENTRY_IDS = ("pead",)` (`apriori/probe.py:20`), with a PEAD-specific drift path
    (`copilot.py:623,1271`).
  - Limits: 1 drift card per session and 4 open drift positions (`config.py:178,181`); 8 positions
    and 2 per group (`config/config.yaml:6-7`); a 60-session holding maximum
    (`agentic_trader/execution/lifetime_policy.py:75`).
  - The $7,500 cap turns inverse-volatility sizing into equal-notional sizing for any stop under 3.3%
    of price. The rates sleeve would carry well under half its intended risk.
  - Holding expiry closes and reopens the position, so a persistent signal pays a full round trip
    every month. Netting cut turnover from 51.86× to 18.90× in the persistent book
    (`docs/alpha-persistent-book-2026-09-17.md` "What improved and what failed").

**Recommend**

- **SZ-R1 [S].** Choose one sizing primitive and make the caps consistent with it:
  - equal stop risk, with the notional cap raised to `R / min_stop_pct`; or
  - equal volatility contribution.

  Set `max_equity_exposure` explicitly, and add a gating reason "R capped by notional ($X)".
- **SZ-R2 [M].** Add SPY and the sector ETFs to the scan covariance and journal per-card β and
  sector dollars (= RK-R4).
- **SZ-R3 [S].** Split-adjusted bars for the risk model. Make every offered tier fit the budget.
  Value the book at the latest completed close.
- **SZ-R4 [M, after CS-R4].** A free shadow allocator per scan:
  - bounds [0, max tier];
  - μ = the shrunk `card_stats` v2 excess R × stop%, with a standard error from `n_mature`;
  - Σ from SZ-R3;
  - the existing caps as linear constraints.

  Journal the suggested notional and the binding constraints. This is the evidence that E2E stage B
  (allocator-sized signals) needs.
- **SZ-R5 [M].** A desk replay: push the 8,768 replayed setups through live sizing, caps, the
  2-per-session budget and `book_vol` into a dollar equity curve against SPY, reusing the
  `persistent_book` ledger identity. Under auto-entry, the live paper account becomes the same curve.
- **SZ-R6 [later].** Fractional Kelly with parameter-uncertainty shrinkage (Baker & McHale 2013),
  only once μ > 0 is established.
- **SZ-R7 [M, only after MI-R8 passes] [added in revision].**
  - Freeze TSMOM in its live-implementable form: one instrument per sleeve and at most 5 (for
    example VTI, EFA, IEF, LQD or TIP, GLD), `SessionLifetimePolicy(holding_sessions=21)`, a
    k × daily-ATR stop, and a far target of at least 2R.
  - Declare a leg-specific risk and notional budget so the bond sleeve keeps risk parity.
  - Generalise `probe.py` and the drift path into a catalog-leg interface (SG-R5) rather than add a
    second PEAD-style branch.

### 3.8 Execution and accounting

**Now**

- **Entry.** `EntryExecutionService` claims the FIFO head, runs preflight, commits SUBMITTING and
  POSTs once (`agentic_trader/execution/entries.py:112-164`). Recovery is lookup-only.
- **Admission** rebuilds exposure from exact order lineage (`capacity.py:79-217`).
- **Signal state.** A signal is EXECUTED at acceptance, not at fill (`workflow.py:645-649`).
- **Closes.** `PositionCloseService` handles them (`agentic_trader/execution/closing.py:20`).
- **Timed exits.** Only alpha and probe signals with a `lifetime` are timed
  (`agentic_trader/execution/lifetimes.py:209-213`).
- **Ledger.** It accepts **nine [revised]** activity types (`agentic_trader/accounting/ledger.py:20-23`):
  FILL, CSD, CSW, JNLC, FEE, CFEE, DIVFEE, DIV and INT.

**Gaps**

- **EX1 — Native entries are GTC, with no expiry and no cancel. [revised]**
  - The tap path sets no time-in-force (`copilot.py:2967-2978`), so GTC applies (`broker/base.py:98`).
  - `/close` answers "No open broker position" for a resting entry (`closing.py:155-156`).
  - Research measured GTC limits as adverse-selecting (`docs/alpha-roadmap.md:150-152`;
    `docs/alpha-lifetime-attribution-2026-09-21.md` "Result and decision").
  - **Little's law, corrected.** The maps' "about 1.5 fills per day" was cards per session. Tap-only
    entry produced 3 accepted entries in about 24 sessions, about 0.13 a day. At the 5.6-session mean hold that
    gives a steady state of about **0.7 positions**, not 8. Capacity becomes the constraint only if
    most cards are entered: the auto-entry replay gives a median of 4 positions and a maximum of 8
    (CS5).
- **EX2 — Labels do not describe the traded policy.**
  - Labels assume a market-like entry at the next hourly open, a 20-session timeout and 5 bp per
    side.
  - Live uses a conditional limit fill, has no timeout, and ratchets from one-minute prices
    (`labels.py:180-187,252-261` against `copilot.py:2528-2630`).
- **EX3 — Execution evidence is thin.**
  - Not measured: fill rate, time to fill, filled-versus-unfilled label R, shortfall against the
    journaled arrival quote, exit slippage, per-signal fees (`agentic_trader/broker/alpaca.py:1279`).
  - Admission refusals are free-text strings (maps/execution.md G3).
- **EX4 — One unmodelled account event stops the whole desk. [revised]**
  - Any of the following becomes an issue:
    - a CSD, CSW or JNLC carrying a nonzero share quantity ("Unsupported share adjustment",
      `ledger.py:129-130`);
    - any activity type outside the nine (`ledger.py:137-138`);
    - any cash or quantity mismatch, for example after a split (`ledger.py:146-150`).
  - Any issue sets `gross_realized = None` (`ledger.py:156`). `require_risk_checkpoint` then refuses
    (`agentic_trader/accounting/risk.py:92-93`):
    - every scan candidate drops as "account risk unavailable" (`copilot.py:1726-1740`);
    - every entry is refused (`workflow.py:372-382`).
  - `/resume` does not lift it, and the full-history import re-reads the same activity on every
    refresh (`agentic_trader/accounting/service.py:54-86`).
- **EX5 — The ratchet is mislabelled, and measured as harmful.**
  - It trails at `current − max(risk, 1.5 × risk)`, where `risk` is the initial stop distance, not
    ATR (`copilot.py:2569-2572`). `trailing_stop.mode: chandelier_atr` (`config/config.yaml:420`) is
    never read.
  - Its measured cost shrinks to about −0.005R once the bond-ETF yardstick artefact is removed (§2.2
    item 2). That is still not positive.
- **EX6 — The tradeable envelope is narrow.** Whole-share equity limit brackets only, one position
  per symbol, full closes only as market DAY orders (`capacity.py:237-244`; `alpaca.py:1053-1061`).
- **EX7 — Legacy components contradict the contracts.** `RedundantBroker` fails over on "timeout"
  text (`agentic_trader/broker/redundant.py:121-161`). `PaperBroker` ignores the limit price
  (`agentic_trader/broker/paper.py:62-64`).
- **EX8 — Scaling fuses.** Every entry preflight runs a full activity import, which fails closed
  above 100 pages (`entries.py:120-124`; `alpaca.py:316-336`).
- **EX9 — Admission prices on IEX quotes with a 60 s freshness limit, and nothing measures the
  cost. [added in revision]** See DA-R10.

**Recommend**

- **EX-R1 [S–M; prerequisite for CS-R4].** Attach `daily_entry_lifetime()` (57,600 s;
  `lifetime_policy.py:24,65-71`) or DAY time-in-force to native cards, and set `time_in_force`
  explicitly. Add an operator "cancel resting entry" command that reuses `entry_cancel`.
- **EX-R2 [M].** Measure policy parity:
  - filled against unfilled label R in `cards outcomes`;
  - a deliberate entry type (a marketable limit matches the labels);
  - a 20-session native timeout, or no labeller timeout (RM-R6).
- **EX-R3 [M].** Decompose implementation shortfall from the journal (Perold 1988): delay, admission
  latency, execution, exit slippage and opportunity, each in R. Feed the measured distributions into
  research cost parameters. Treat paper fills as optimistic.
- **EX-R4 [M, on native-v2 with the beta-matched yardstick].** Run the predeclared ratchet-off test
  (`docs/bracket-counterfactual-2026-10-18.md` "What this says" §2). If the ratchet stays, rebuild it
  on ATR with parity to hourly closes, and delete the dead `mode` key.
- **EX-R5 [M].**
  - Classify the full Alpaca dividend family.
  - Quarantine the *symbol* on structural actions.
  - Decouple the drawdown gate (equity and flows) from realized-P&L readiness.
  - Treat an unknown cash-bearing activity conservatively, as an external inflow.
  - For a partial fill, reserve worst-case risk and block only that symbol.
- **EX-R6 [S].** Typed refusal codes for capacity and admission, with a refusal histogram.
- **EX-R7 [S].** Delete or quarantine `RedundantBroker`, the slicer engine and the paper fill model.
- **EX-R8 [M].** An incremental ledger import (a cursor plus a periodic full audit), and batched
  order-observation dedup.

### 3.9 Architecture, contracts and operations

**Now.** §1.1 summarises the five stages.
- `TradingCopilot` is 4,527 lines with 103 methods (`copilot.py:244`). It is at once the composition
  root, the scan orchestrator, the tap front end, the reporting layer and the chat backend.
- Imports run both ways between research and live:
  - 17 live modules import `agentic_trader.research`, for example `config.py:59`,
    `evaluator.py:35` and `workflow.py:40-41`;
  - `research/` imports `storage`, `execution`, `screeners` and `agent` about 70 times.

**Gaps**

- **AR1 — The fusion points.**
  - `evaluate_candidate` (`evaluator.py:362-917`) builds the bracket, sizes, runs eight book rules,
    does correlation I/O, runs the macro, earnings, corporate-action, regime and session rules, calls
    the LLM, clamps, and falls back to approval.
  - `run_scan` (`copilot.py:682-1677`) covers signal, selection, sizing, journaling, notification and
    monitoring.
  - The deterministic pass runs twice per winner (`copilot.py:1744,1364`). Book sizing runs after the
    LLM (`copilot.py:1396-1425`), so LLM budget is spent on cards that sizing then blocks.
- **AR2 — The seams are untyped, and policy is baked into the DTOs.**
  - The scan works on `tuple[Any, Any, Any]` (`copilot.py:869`), and `decision_provenance` is an
    untyped dict of about 20 keys (`copilot.py:1462-1491`).
  - Journal payloads grew under a constant `schema_version=1` (`workflow.py:155`).
  - `LLMTradeEvaluation.risk_reward_ratio` has `ge=2.0` (`evaluator.py:106`), while
    `risk.min_risk_reward_ratio` has no floor (`limits.py:115`). So a predeclared sub-2R bracket
    would raise twice: at card construction and again at outbox delivery
    (`agentic_trader/notifier/outbox.py:78`).
- **AR3 — The signal contract cannot carry what mining produces** (SG9).
- **AR4 — No import boundary between research and live.** Shared kernels (bracket policy, labels,
  covariance) live under `research/`, so a research refactor can change the daemon.
- **AR5 — Configuration loses keys silently.**
  - `session:` (`config/config.yaml:414`) is never passed to `AppConfig` (`config.py:1109-1179`). It
    is latent today because the values equal the defaults, but it is the same class of bug as the
    `corporate_actions` loss before 2026-10-13 (`docs/alpha-roadmap.md:591`).
  - 25 of 30 sub-models ignore unknown keys.
  - Dead knobs remain: `strategy_allocations`, the indicator periods, `trailing_stop.mode` and
    `credit_oas_enabled`.
  - `StrategyEngine` silently calls `load_config()` when given no config (`strategies.py:75,246,364`).
- **AR6 — Observability does not cover the north star.**
  - There are no funnel metrics.
  - `/scan` counts new cards with `len(get_recent_signals(limit=100))` before and after, so it is
    wrong once 100 rows exist, and it reports a gated run as complete (`copilot.py:4366-4378`).
  - The chat tool `trigger_market_scan` ignores its arguments and spends the budget
    (`copilot_tools.py:61-70`).
  - The chat LLM can call `promote_alpha` (`copilot_tools.py:184-192`), which is still CAS-gated.
  - The chat memory is unbounded (`agentic_trader/agent/copilot_graph.py:83`).
  - `watchdog.log` is never rotated (`scripts/launchd.sh:172`).
- **AR7 — One feed failure silences a whole session** (RK3). The digest is coupled to scan success.

**Recommend**

- **AR-R1 [M].** Build L3 as a typed `Selection` (SG-R1). Build L4 as `BracketBuilder` → `Sizer` →
  `risk.book_gates` → `LLMReviewer`, so the LLM runs last and only annotates or vetoes. Replace
  `LLMTradeEvaluation` with a `Card` model that carries no policy (no `ge=2.0`).
- **AR-R2 [M].** A typed `Signal`/`Insight` contract (direction, horizon, expected R, confidence,
  source), on the QuantConnect LEAN Algorithm Framework model. Cross-sectional `SignalSource`s see
  the whole scan, as Zipline Pipeline and Qlib's `DataHandler` do. Native strategies, PEAD and
  registry alphas become implementations.
- **AR-R3 [S].** Harden config:
  - build with one `AppConfig.model_validate(merged)`;
  - set `extra="forbid"` everywhere;
  - add a test that every YAML key round-trips;
  - delete the dead knobs.
- **AR-R4 [M].** Move the kernels (bracket policy, labels, covariance, DSL evaluation, probe
  constants) into a `kernels` package, and enforce `research → kernels ← live` with import-linter in
  pre-commit.
- **AR-R5 [S].**
  - Per-scan Prometheus counters: candidates, approved, sent, withheld, LLM approve/veto/fallback, and
    book-sizing blocks.
  - An incident for "N suggestion slots with zero cards and no reason".
  - Fix the `/scan` reply.
  - Rotate the logs.
  - Version the journal payloads with pydantic models.

---

## 4. Outside work worth integrating

These are references and tools, not new dependencies, unless the table says otherwise. Each is tied
to a specific finding.

| Area | Work | What to take | Where it applies |
| --- | --- | --- | --- |
| Sequential and family inference | Howard, Ramdas, McAuliffe & Sekhon (2021), *Ann. Stat.* 49(2), time-uniform confidence sequences (`confseq`); Lan & DeMets (1983), *Biometrika* 70(3) | Always-valid reads and alpha spending across interim looks and competing lanes | MI-R4, RM-R1 |
| Selection-correct testing | Romano–Wolf / Hansen SPA (the `arch` package, already used); Bailey et al. PBO/CPCV | Family control at selection, then an honest holdout test | MI-R10 |
| Anomaly priors | Chen & Zimmermann, Open Source Asset Pricing; Jensen, Kelly & Pedersen (2023, *JF*); Hou, Xue & Zhang (2020, *RFS* 33(5)); McLean & Pontiff (2016, *JF* 71(1)); Chordia, Subrahmanyam & Tong (2014) | Literature signs and definitions for fixed sets and the composite; realistic effect sizes (expect small, decayed, large-cap-weak effects) | MI-R5, MI1 |
| Costs | Abdi & Ranaldo (2017, *RFS* 30(12)); Corwin & Schultz (2012); Novy-Marx & Velikov (2016, *RFS* 29(1)); Frazzini, Israel & Moskowitz (2018) | Per-name spread from daily bars, so the selector key and labels stop assuming a flat 5 bp | SG-R1, DA10 |
| Delisting bias | Shumway (1997, *JF* 52(1)); Shumway & Warther (1999, *JF* 54(6)) | A `DELISTED` label outcome and a declared performance-delisting proxy | DA-R6 |
| Fundamentals | Novy-Marx (2013, *JFE* 108(1)); Cooper, Gulen & Schill (2008, *JF* 63(4)); Pontiff & Woodgate (2008, *JF* 63(2)); Hou, Xue & Zhang (2015, *RFS* 28(3)); Ball et al. (2016, *JFE* 121(1)); Green, Hand & Soliman (2011); Fama & French (2008); Avramov et al. (2007, *JF* 62(5)) | Field definitions (GP/A, asset growth, ROE, net issuance; not Sloan accruals) and a tail/interaction prior rather than a main-effect prior | DA-R7, RM-R5 |
| Options | Xing, Zhang & Zhao (2010, *JFQA* 45(3)); An, Ang, Bali & Cakici (2014, *JF* 69(5)); Cremers & Weinbaum (2010); Johnson & So (2012, *JFE* 106(2)); Pan & Poteshman (2006) | Skew and ΔIV legs whose horizon fits the 20-session bracket. Weekly-horizon signals as context only | DA-R4, §5 item 2.11 |
| Trend | Moskowitz, Ooi & Pedersen (2012, *JFE* 104(2)); Hurst, Ooi & Pedersen (2017); Kim, Tse & Wald (2016); Huang et al. (2020, *JFE* 135(3)); Goyal & Jegadeesh (2018, *RFS* 31(5)); Levine & Pedersen (2016); Faber (2007); Fung & Hsieh (2001) | The TSMOM-v1 spec and its controls (C1 volatility-scaled long, C2 historical mean); a book-level yardstick | MI-R8, RM2 |
| LLM research | AlphaAgent (arXiv 2502.16789); R&D-Agent-Quant (arXiv 2505.15155, whose NASDAQ-100 test design respects model cutoffs); Alpha-GPT; Lopez-Lira, Tang & Zhu (2025, arXiv 2504.14765); Sarkar & Vafa (2024); Glasserman & Lin (2023); ChronoBERT/ChronoGPT (He et al. 2025); Kim, Muhn & Nikolaev (2024); Look-Ahead-Bench (2026) | One-shot proposers, AST originality vetting, a knowledge-cutoff guard and point-in-time text scorers | MI-R9, RM-R4, SG-R3 |
| Human-in-the-loop | Fildes, Goodwin, Lawrence & Nikolopoulos (2009, *IJF* 25(1)); Gilliland, Forecast Value Added; Harvey et al. (2017), "Man vs. Machine" | Attribute the tap like any other gate | CS-R5 |
| Risk and sizing | Grossman & Zhou (1993); Harvey et al. (2018), volatility targeting; Moreira & Muir (2017); Cederburg et al. (2020); Maillard, Roncalli & Teïletche (2010); López de Prado (2016), HRP; Kaminski & Lo (2014); Daniel & Moskowitz (2016); Barroso & Santa-Clara (2015); Baker & McHale (2013) | Risk-binding sizing, ERC tie-breaks, a regime conditioner with evidence behind it, a stop-width study, Kelly shrinkage | RK-R3, RK-R7, SZ-R1, SZ-R6 |
| Execution | Perold (1988); Handa & Schwartz (1996); Harris & Hasbrouck (1996); Lo, MacKinlay & Zhang (2002) | Shortfall decomposition and limit-order adverse selection | EX-R2, EX-R3 |
| Attention and events | Barber & Odean (2008); Da, Engelberg & Gao (2011); Frazzini & Lamont (2007); Barber et al. (2013); Martineau (2022) | Priors for dynamic names, event controls and PEAD decay in large caps | SG8, MI-R6, MI-R7 |
| Selection by meta-labelling | López de Prado, *AFML* ch. 3; Joubert (2022) | Use **only** inside SG-R1's predeclared comparison, with a stated differentiator from the failed Ridge | SG-R1 |
| Open-source software | Qlib (Alpha158, expression engine, point-in-time provider); alphalens-reloaded; QuantConnect LEAN Algorithm Framework; Zipline Pipeline; cvxportfolio, skfolio, Riskfolio-Lib (validators); MAPIE; import-linter; exchange_calendars; pysystemtrade (GPL-3.0: an audit reference only, never vendored); NautilusTrader (a parity-design reference) | Feature library, vectorised operators, typed stage contracts, import boundaries | AR-R2, AR-R4, MI-R11 |
| Data | Sharadar (full history, $499/yr); SEC Financial Statement Data Sets; SEC Form 25/15; Cboe `VIX_History.csv`; Treasury par yield curve; ALFRED; Ken French data library; Alpaca indicative options feed (Basic plan) | Point-in-time membership, fundamentals and SIC; official macro sources; factor controls; options capture | DA-R4, DA-R5, DA-R7, RK-R2, RM-R3 |

---

## 5. Roadmap: one ordered queue

**Principles.**
- Measure the card that is actually sent, on a yardstick that does not invent alpha or deficit.
- Fix the inputs that fail open before adding new sources.
- Start any clock that cannot be bought back.
- Spend the last historical confirmation window on purpose.

Effort: S ≈ 1 SDD-day or less; M ≈ 2–5 SDD-days; L ≈ more than 5. "Dep" lists dependencies.

### Phase 0 — by 2026-10-28 (the FOMC, and the PEAD probe expiry)

| # | Item | Effort | Dep | Effect on cards |
| --- | --- | --- | --- | --- |
| 0.1 | Static tier-1 calendar and a typed fetch status (RK-R1) **[added in revision]** | S | — | The FOMC 14:35 batch and taps are locked out deterministically |
| 0.2 | Regime minimum fail-safes: journal `regime_unavailable`, digest in `finally`, an `UNKNOWN` multiplier in place of 1.0 (part of RK-R2) **[added in revision]** | S | — | No silent sessions; no 4× risk while a feed is down |
| 0.3 | **Decision D1:** renew the PEAD probe for 180 days at unchanged risk, judged on the counterfactual label (MI-R7) | S | — | Keeps the only positive card source alive |
| 0.4 | `JOURNAL_ONLY` swing and intraday scans; no send on daemon start (CS-R1, decision D2) **[added in revision]** | S | — | Cards sent = cards measured |
| 0.5 | Index-clone rule (CS-R2, decision D4) | S | — | Two distinct bets per session |
| 0.6 | Journal zero-candidate scans and per-rule rejection counts (CS-R3) | S | — | Candidate supply becomes measurable |
| 0.7 | Journal the raw regime inputs, sources, observation dates and `clock_regime` (part of RM-R3) **[added in revision]** | S | — | The desk starts owning its macro history; the DST stratum is identifiable |
| 0.8 | Scope the 15m fetch; turn off the idle intraday job (SG-R4) | S | — | About 180 fewer requests per scan; frees the scan lock |
| 0.9 | **Decision D6:** the confirmation-window rule, *before* approving the insider campaigns (MI-R1) **[added in revision]** | — | — | Protects the most valuable research asset |

### Phase 1 — weeks 2–5 (honest measurement, and a book that exists)

| # | Item | Effort | Dep | Effect |
| --- | --- | --- | --- | --- |
| 1.1 | Beta-matched control, results by asset class, the bracket-counterfactual erratum, BIL removed from native cards (RM-R2) **[added in revision]** | S | — | A yardstick that does not penalise low beta |
| 1.2 | `card_stats` v2 (RM-R1), then `card_policy` **preview** on that unit (decision D3) | M | 1.1, 0.4 | An honest base rate on every card; a safe withhold path |
| 1.3 | Session-bounded native entries and an operator cancel (EX-R1) | S–M | — | No zombie entries; prerequisite for 1.4 |
| 1.4 | Paper auto-entry for `SUGGESTION_SCAN` cards (CS-R4; operator decision); notice digest (CS-R6); operator block in `cards outcomes` (CS-R5) **[added in revision]** | M | 1.3, 0.4 | The book fills and allocation decisions become live; execution evidence arrives about 10× faster |
| 1.5 | LLM hygiene: deadline, fallback outcome, fail-closed veto, model and prompt identity (SG-R3; decision D7) | S | — | Honest attribution; no fallback approval with `macro_clearance=True` |
| 1.6 | Options capture worker (DA-R4) and the volume-backfill spike (§5 item 2.11) **[added in revision]** | M | — | Starts the irreplaceable IV/skew clock |
| 1.7 | Cube label-span fix and its regression test (MI-R2) **[added in revision]** | S | — | Prevents a biased first horizon-matched lane |
| 1.8 | The rest of the regime inputs: Cboe VIX, a single-source curve, `macro_inputs` readiness (RK-R2) | M | 0.2 | Official sources on one clock |
| 1.9 | **Decision D8:** a Sharadar evaluation month and coverage audit (DA-R5) **[added in revision]** | S | — | Decides whether the free point-in-time builds are needed |

### Phase 2 — months 1–3 (new evidence on the right bars and yardstick)

| # | Item | Effort | Dep | Effect |
| --- | --- | --- | --- | --- |
| 2.1 | Native-v2 bar semantics and a fresh frozen replay (SG-R2, DA-R1; decision D5) | M | 1.1 | Live, replay and `card_stats` see one series |
| 2.2 | `alpha macro-history` and the regime-gate replay on native-v2 (RM-R3; then RK-R7) **[added in revision]** | M | 2.1 | Recovers suppressed breakout cards, or justifies the gates |
| 2.3 | L3 `CardSelector` on expected net R with a per-name cost; one predeclared comparison of residual effects (SG-R1) **[added in revision]** | M | 1.2, 2.1 | The 1–2 cards go to the best net expectancy, not to an uninformative key |
| 2.4 | Ratchet-off test on native-v2 with the beta-matched yardstick (EX-R4) | S–M | 2.1, 1.1 | Settles a measured but yardstick-sensitive cost |
| 2.5 | Risk-binding sizing, beta-dollar view, split-safe covariance, consistent caps (RK-R3, RK-R4, SZ-R1, SZ-R3) | M | 1.4 | Haircuts actually bite; cards become portfolio decisions |
| 2.6 | `SecurityId`, `asof`, `DELISTED`; point-in-time cohort v4 (DA-R6) | M | 1.9 | Honest long base rates for every lane |
| 2.7 | Risk arrays in the cube and per-formula MDE (MI-R3) | M | 2.6 preferred | Makes the 64 nulls readable; better controls |
| 2.8 | `alpha pooled-forward` job with a predeclared read rule (MI-R4) **[added in revision]** | M | 0.9 | Mining gets a cadence at zero trial cost |
| 2.9 | TSMOM-v1 a priori study (MI-R8), and only after a pass, the live form (SZ-R7) **[added in revision]** | M | 1.1 | The only untested, literature-backed source on instruments already traded |
| 2.10 | Fundamentals junk-tail veto (RM-R5), using Sharadar SF1 or FSDS as-filed (DA-R7) **[added in revision]** | M | 1.9, 2.1 | A cheap, honest conditioning test on native cards |
| 2.11 | Options: one-afternoon spike on Alpaca's Feb-2024 option bars. If expired contracts enumerate, a research-only `alpha option-volume` fixed set (≤ 4 formulas, own protocol and approval). Before the first capture, commit a priori entries `skew_low` and `civ_rise`, with one read at 300 sessions (about 2027-12) and a synthetic power check first. Options context block on candidates (descriptive, never a gate) once the capture exists **[added in revision]** | S + M | 1.6 | Turns "options are paid, defer" into dated, pre-registered tests |

### Phase 3 — quarter and beyond (architecture and long horizon)

| # | Item | Effort |
| --- | --- | --- |
| 3.1 | L4 evaluator split; a policy-free `Card`; typed `Signal`/`Insight`; `kernels` package and import-linter; config hardening (AR-R1 to AR-R4) | L |
| 3.2 | Text-scorer contract; a prospective news-tone study; optional ChronoBERT replay (RM-R4) **[added in revision]** | M |
| 3.3 | LLM proposer as a one-shot fixed-set author behind the cutoff guard (MI-R9) **[added in revision]** | M |
| 3.4 | Event-entry selection, `FieldSource` plugins; the composite, if D6 reserves the window (MI-R5, MI-R6) | M–L |
| 3.5 | Per-symbol lane: policy v2 or explicit retirement; prune the frozen harnesses by tag (MI-R10) | M |
| 3.6 | Narrow the ledger's blast radius; incremental import; delete legacy brokers (EX-R5, EX-R7, EX-R8) | M |
| 3.7 | Shortfall decomposition, typed refusals, envelope widening only where research needs it (EX-R3, EX-R6) | M |
| 3.8 | Algo Trader Plus, decided on measured admission evidence only (DA-R10) **[added in revision]** | S |
| 3.9 | Scenario-loss rule; held-position earnings notice; dynamic-name classification (RK-R5, RK-R6) | M |

### Operator decisions, in date order

| ID | Decision | By |
| --- | --- | --- |
| D1 | Renew the PEAD probe (recommended: 180 days, unchanged risk, judged on the counterfactual label) or let it retire | 2026-10-28 |
| D2 | The swing scan's role: journal-only (recommended) or sending | Phase 0 |
| D3 | The `card_policy` evidence unit (raw or beta-adjusted excess R; this depends on the desk mandate) and the threshold | Before 1.2 preview |
| D4 | Exclude broad-index ETFs from native cards, or use a correlation-dedup rule | Phase 0 |
| D5 | Spend a replay window on native-v2 | Phase 2 |
| D6 | Confirmation-window rule: first finalist spends it, reserved for one composite, or prospective from 2026-08-01 | Before approving the insider campaigns |
| D7 | The LLM stays decisive for native cards, or becomes commentary-only | Phase 1 |
| D8 | Buy Sharadar full history (after the evaluation month), or build the free route | Phase 1 |
| D9 | Paper auto-entry (amends contract 1 and `docs/production.md:43` for paper only) | Phase 1 |

### What to stop, or not to start

- More pooled fixed sets on the same label, the same current-membership cohorts and an
  unmatched control (MI1–MI3).
- `card_policy` preview or enforce on raw, censored, repeat-weighted R (CS8, RM1).
- A meta-label or learned ranker without a predeclared reason it would beat the failed Ridge (SG2).
- −Δvol as a primary key (SG2, SZ10).
- A pooled fundamentals campaign on the 20-session 3R label (RM7).
- The XBRL `frames` API for anything historical (DA7).
- Treating DST on 2026-11-01 as a deadline (SG4).
- Buying Algo Trader Plus before measuring the admission evidence (DA-R10).
- Replaying a modern LLM over text dated before its cutoff (RM4).
- Iterative LLM search loops; they are the parked `genetic` mode (MI15).

---

## 6. Strengths to preserve

- **Pre-registration with byte-hash identity.** Charge-before-read and consume-before-compute
  (`agentic_trader/research/pooled/ledger.py:35-101`; `storage/alpha.py:203-258`). After 64 tests the
  confirmation window is still unused, and no fluke was promoted.
- **The session-paired control.** It removed a +0.15R drift-and-survivorship illusion from the
  literature entries and from exdiv (`docs/alpha-pooled-literature-2026-09-30.md` "Criteria";
  `docs/apriori-exdiv-2026-10-12.md`).
- **`risk/` as a model seam.** Pure rules, one implementation each, a fixed order, and seeded
  consistency tests between scan and admission (`rules.py:470-513`; `docs/risk-policy.md` tests).
  Every rule this review proposes belongs there.
- **Execution discipline.**
  - Single submission and lookup-only recovery (`entries.py:166-175`).
  - Two-phase admission with a pinned risk fingerprint (`workflow.py:527-616`).
  - Ownership by exact order lineage (`capacity.py:69-76`).
  - A transactional outbox.
- **Evidence-first records.**
  - Raw pages are kept before parsing (`providers.py:227-276`).
  - Provenance is complete on every card (maps/live.md §5).
  - Every ranked candidate is journaled with its outcome reason.
  - Most of this review's measurements were possible only because of that.
- **Live/replay reuse.** Replay runs the unchanged `StrategyEngine` and `calculate_levels_deterministic`
  (`replay.py:1-8,287-313`), and PEAD's live path calls the study's own `build_events`.
- **`off → preview → enforce` modes.** Policy is journaled before it acts.
- **Honest negative write-ups.** They are rarer, and more valuable, than any single alpha.

---

## 7. Open questions

- What did the risk engine reject, rule by rule? Only logs hold this (`copilot.py:1754-1762`), and
  the logs were not read.
- Does the 16-definition shadow registry hold a 15m definition today? The 28% share of 15m captures
  implies it does (§1.2 step 4). Retiring or re-declaring the 16 shadows also stops the blind shadow
  lane (MI13).
- Do Alpaca paper fills reference the SIP NBBO? That decides which feed the labels should use.
- Has an unsupported activity type, such as a capital-gain distribution on an ETF, ever appeared in
  the paper account (EX4)?
- Does `asof` map a reused ticker back to the delisted entity, for example SBNY on 2023-03-01?
- Is the desk's mandate long-biased, or should native cards be judged net of beta? This decides D3
  and whether RK-R4 is a display or a constraint.
- Does Alpaca's Basic plan enumerate *expired* option contracts and return their daily bars (§5 item
  2.11)?
- Why did the daily panel's first outcome give `ic_pairs 0`? One missing label voids the
  cross-section (`agentic_trader/research/alpha/daily_forecasts.py:551`). Will it recur?

---

## Appendix A. Revision notes

**A.0 The file itself.** `report.md` did not exist when this revision started, at 20:08–20:18 IDT
on 2026-10-21. The critic saw the same from 19:31 to 20:00. This revision therefore *builds* the
report from the ten maps in `maps/`, applies every correction, and integrates every gap finding. The
structure above is new, because there was no earlier structure to keep.

**A.1 Critic corrections. Each was checked against the code or data before it was applied.**

| # | Correction | Verdict | Verification |
| --- | --- | --- | --- |
| 1 | maps/pooled.md §2 "five subclasses" should be four | **Accepted** | `campaign.py:403,451,494,555` |
| 2 | maps/execution.md G4 "eight supported" should be nine; share adjustments and mismatches are also issues | **Accepted, with corrected line numbers** | The constants are at `ledger.py:20-23` (the critic gave 19-22). "Unsupported share adjustment" is at `ledger.py:129-130` (the critic gave 127-128). Cash and quantity mismatch are at `ledger.py:146-150` (the critic gave 142-148) |
| 3 | Little's law used cards for fills; the steady state is about 0.7 positions | **Accepted** | 3 accepted entries in about 24 sessions (maps/live.md Q10). The review's auto-entry replay independently gives a median of 4 positions when every card is entered |
| 4 | `FormulaicAlphaStrategy` fires on a level, not a crossing | **Accepted** | `strategy.py:155-161`; `formulaic.py:102-104`; duplicate rule at `copilot.py:1694-1723` |
| 5 | The 15m fetch is universe-wide whenever any active, shadow or probe definition is 15m | **Accepted** | `copilot.py:833-837`; `market_data.py:204,222-227` |
| 6 | Early `card_stats` figures are censoring artefacts; +0.535R now; enforcing at 0 withholds nothing | **Accepted** | maps/live.md Q1; `card_stats.py:91-92,112` |
| 7 | Beta-adjusted excess −0.062R against −0.109R | **Accepted, reproduced** | Re-ran `scripts/pm_beta_adj_R.py` on the frozen primary frame: −0.109 → −0.062 (session mean −0.069, SE 0.024; betas 0.39–1.21) |
| 8 | The meta-label claim is unsupported; the Ridge failed the holdout | **Accepted** | `docs/setup-outcomes-2026-09-23.md:86-107` ("Selection (H3)") |
| 9 | A calendar failure makes every deterministic lockout fail open, not only the LLM line | **Accepted** | `calendar.py:107-125,176-221`; `evaluator.py:526-541`; `copilot.py:809-831,3962-3987` |
| 10 | The bracket stop formula mixed a price with a distance; ATR14 is the 4h ATR | **Accepted, with corrected line** | `evaluator.py:208-216`. `atr_4h` is assigned at `strategies.py:133` (the critic gave 131) and passed at 163 |

**A.2 Corrections found during this revision** (not in the critic's list):

- maps/live.md §7.5 proposes labelling the LLM-edited bracket in `cards outcomes`. That already
  exists (`outcomes.py:939-956`). Only `card_stats` labels the deterministic bracket. See SG5.
- maps/architecture.md §7.1 proposes `ScanBudget.NONE` for swing scans. `NONE` sends cards and is
  not journaled (`config.py:187-190`; `copilot.py:1591`). Replaced by `JOURNAL_ONLY` (CS9, CS-R1).
  The gap review raised this point; it is verified here.
- maps/pooled.md and maps/data.md cite `_ARRAYS` at `cube.py:53`. It is at `cube.py:52`.
- **Gap-review figures that needed reconciling:**
  - **Bond-ETF `market_r` means** (+2.15R primary, +7.66R robustness) are *unclipped*. After the
    ±10R clip they are +0.82R and +2.33R. The excess figures (−0.64R; −2.32R; robustness ex-rates
    −0.006R; ex-rates and metals −0.008R) reproduce with `scripts/rates_excess_check.py`. This review
    adds the primary ex-rates figure, −0.083R.
  - **ATR-tercile split.** Re-run with a ±10R clip, it gives target rates of 33.5/35.0/34.6% and cost
    in R of 0.122/0.055/0.032, as the gap review reported. `market_r` came out 0.232/0.144/0.089
    (the review had 0.169/0.146/0.088) and excess −0.325/−0.071/+0.028 (the review had
    −0.285/−0.079/+0.019). The difference comes from winsorisation; the conclusion is unchanged. The
    82.9% notional-cap share is confirmed.
- The `ranking_key` line is `copilot.py:1907`; the gap review gave 1902.
- **maps/data.md I5's split of the survivorship repair at 2022 is on the wrong axis** (DA4). The
  gap review raised this; it is verified against the window definitions in `campaign-v2.json:10-22`.
- **maps/signals.md §7.3 treats DST on 11-01 as a deadline.** The gap review's replay comparison says
  it is not; journal the clock regime instead (SG4).

**A.3 Where each gap finding was integrated.**

| Gap finding | Integrated as |
| --- | --- |
| One ordered card-stream plan; preview adds nothing | CS1, CS2, CS8, CS9, CS-R1–R3; Phase 0–1; decisions D2–D7 |
| The three L3 keys conflict; `vol_20` is cost-in-R | SG2, SG-R1; SZ10; §5 item 2.3 |
| Four "prospective scorer" proposals become one batch job; window rule | MI16, MI-R1, MI-R4, MI-R7; items 0.3, 0.9 and 2.8 |
| Accounting data absent; as-filed SEC route | DA7, DA-R7; item 2.10 |
| Cube label span silently drops timeouts | MI4, MI-R2; item 1.7 |
| Fundamentals as a junk-tail veto | RM7, RM-R5; item 2.10 |
| Options state not captured | DA6, DA-R4; item 1.6 |
| Pre-register options legs; Feb-2024 volume backfill | Item 2.11; §4 Options row |
| Options context block on candidates | Item 2.11 (descriptive, never a gate) |
| LLM proposer fixed-set route and cutoff guard | MI15, MI-R9; item 3.3 |
| Text-scorer vintage contract | RM4, RM-R4; item 3.2 |
| `llm_verdict` model and prompt identity | SG5, SG-R3; item 1.5 |
| Price the point-in-time pack (Sharadar) | DA-R5; item 1.9; decision D8 |
| Identity, `asof` and delisting returns | DA4, DA-R6; item 2.6 |
| Algo Trader Plus only on measured evidence | DA-R10, EX9; item 3.8 |
| Paper auto-entry | CS5, CS-R4; EX1 (Little's law); item 1.4; decision D9 |
| The operator tap as an unmeasured gate | CS6, CS-R5; item 1.4 |
| Macro lockout fails open on ForexFactory | RK2, RK-R1; item 0.1 |
| Regime inputs fail open or abort the scan | RK3, RK-R2; items 0.2 and 1.8 |
| Regime gates never replayed; no macro history | §2.2 item 3; RM3, RM-R3; items 0.7 and 2.2 |
| Cross-asset TSMOM untested | MI14, MI-R8; item 2.9 |
| The beta-one control distorted by bond ETFs | §2.2 item 2; RM2, RM-R2; item 1.1 |
| Trend leg vs the book limits | SZ12, SZ-R7; item 2.9 |

---

## Appendix B. Evidence index

All paths are under `~/agentic-trader-research/holistic-review-20261021/`.

| Evidence | Path | Used in |
| --- | --- | --- |
| Subsystem maps (architecture, signals, live, history, pooled, data, execution, risk, portfolio, alpha_pipeline) | `maps/*.md` | Throughout |
| Beta-adjusted excess by stop-width quintile | `scripts/pm_beta_adj_R.py` on `~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz` | §2.2 item 2 |
| Bond-ETF effect on beta-one excess | `scripts/rates_excess_check.py` on the primary and robustness frames | §2.2 item 2; RM2 |
| Sizing-tier probe | `scripts/sizing_probe.py` | RK1 |
| Auto-entry book replay | `autoentry/book_sim.py`, `autoentry/cards.csv` (read-only `signals` export) | CS5 |
| Tap labelling | `autoentry/label_taps.py`, `autoentry/labels.csv` | CS6 |
| FF5 power arithmetic | `ff5_power.py`, `ff5-download/` | RM7 |
| Macro-source probes (ForexFactory, BLS, Treasury, Cboe VIX) | `macro-sourcing-probes/` | RK2, RK3, §2.2 item 3 |
| FSDS documentation probe | `xbrl-fsds/` | DA7 |
| Insider check A progress | `~/agentic-trader-research/insider-v1-checks-20261021.log` | MI7 |
