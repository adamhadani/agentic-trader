# First synthesis draft (superseded)

The synthesis agent's draft, returned as text because its file write was refused. The completeness
critic and the revision pass then built [report.md](report.md) from the maps; that report governs.
This draft is kept because its framing differs: it named C01, C09 and C18 as the P0 actions.

---

**Note:** the harness refused the file write ("Subagents should return findings as text, not write report files"), so `~/agentic-trader-research/holistic-review-20261021/report.md` was not created. The full report follows.

**Summary:** The program is not converging on the north star.
- **Cards:** they still come from native setups with about zero alpha against SPY. They are ranked by a key that carries no information. The one research-positive source (PEAD) gets one card a session and its probe expires on 2026-10-28.
- **Mining:** five of the last ten merges built pooled mining lanes that cannot produce a card.
- **P0 actions:** the swing scan spends the card budget (C09); renew PEAD and measure it against SPY (C18); stop single-formula pooled lanes (C01).
- **Next:** fix how card evidence is measured and the cheap control-plane defects. Then redirect mining to one composite and to event-driven legs.

---

# agentic-trader: holistic review of system and roadmap

Code reviewed: main `db82817` (read-only worktree, identical to production), 2026-10-21. Paths are relative to the repo root. Live figures come from read-only production queries in the live map (cited as "live map Qn"). Every finding id (C01–C57) was fact-checked and priority-rated by independent reviewers. Where a reviewer corrected a finding, this report uses the corrected version. Points I checked in the code myself are marked *(lead reviewer)*. "Now" means what the code does today; "Recommend" means a proposal.

## 1. Executive summary

**Is the program converging on the north star? No.** Research rigour is excellent and still improving, but the card stream has not moved:

- **Card source.** Cards still come from the same native trend-pullback longs as a month ago. On 8,768 replayed setups these earn +0.039R after cost but −0.110R against SPY over their own holding windows (docs/bracket-counterfactual-2026-10-18.md "Descriptives").
- **Ranking.** Cards are ranked by `setup_quality`, which is measured as uninformative: ρ +0.016 in development, −0.012 in the holdout (docs/setup-outcomes-2026-09-23.md "Hypotheses").
- **The one positive source is starved.** PEAD long (+0.055R over its control) gets one card a session at $100 risk, and its probe expires on 2026-10-28 (C18).
- **Research effort went elsewhere.** Five of the last ten merges built pooled mining lanes (15e6939, d6fd22e, fe10fc6, 08a98ba, db82817). None of them can produce a card, because the "Part 2" path is unbuilt (docs/alpha-pooled-mining.md:1014-1017).

Most of the gap is self-inflicted, and much of it is cheap to close.

1. **Stop single-formula pooled fixed sets, and aim mining at what can pass (C01, C06, C21, C25, C37).**
   - The 64 formula-cohort pairs have t mean −0.06 and sd 1.25. That is indistinguishable from check C's null (seed-t sd 1.11–1.27).
   - The lane's contract puts its reach at about 0.08–0.10R. Plausible anomaly edges in liquid US names after publication sit well below that.
   - Only one design has ever passed: the a priori event design (one entry per event, an event-matched control, P1–P4). It also owns the only working route to a card.
   - Redirect the capacity to one literature-signed composite (C06, C24) and to event-time legs: CMP-faithful insider buying (C19, C21) and the earnings-announcement premium (C25).
2. **PEAD is starved and has never been measured against the index (C18).**
   - Renew it before 10-28 under a fidelity rule.
   - Within a week, answer offline whether drift cards beat holding SPY. The control alone earns +0.094R, so the raw +0.149R may be mostly beta.
3. **The way cards are measured is broken. Fix it before any outcome-derived gate acts (C03, C04, C10, C11, C27).**
   - The card's measured number swung from −0.376R to +0.535R in 13 days. It is censored, repeat-counted (87 rows cover 41 setups), raw R, and labelled on IEX.
   - Its implied EV mixes gross and net R. Cost is about 0.077R, roughly twice the whole net edge.
   - Research base rates use SIP all-adjusted bars. The live UTC 4h buckets have different composition, so research and live describe different triggers.
   - The SPY control is beta-one and scaled into R units.
   - The cheapest honest fix: print the frozen replay prior (+0.04R net, −0.11R vs SPY, n 8,768) as the headline; make implied EV net; de-duplicate; label on SIP.
4. **Cheap operational defects cost cards directly.**
   - Swing scans fire at every daemon start (61 in 33 days). They spent the session budget in 7 of 13 journaled 14:35 scans (C09).
   - `load_config` silently drops the `session:` and `apriori:` blocks *(lead reviewer verified, config.py:1109-1179)* (C54).
   - The LLM gate has no deadline and approves on any exception (C30).
   - With raw bars under `book_sizing: enforce`, one reverse split anywhere in a scan can block every card (C36, C38).
   - One unmodelled ledger activity stops every scan (C53).
   - Telegram sent 659 incident notices against 41 cards (C52).
5. **"Allocation based on existing positions" exists in name only (C08, C12, C38).**
   - The $7,500 notional cap binds on about 84% of setups, so drawdown and macro de-risking barely change size.
   - `book_vol` has never bound.
   - There is no beta or sector view: VOO was carded after VTI.
   - Fix the cheap slices. Defer the factor model and allocator: with EV ≤ 0, an honest allocator says "size zero".
6. **Keep the core, and stop building platforms ahead of the evidence.**
   - Keep: pre-registration with byte-hash identity, charge-before-read, one-use windows, session-paired controls, the pure `risk/` module, and durable execution. These are why no false positive ever reached the operator.
   - Do not build the Forecast/SignalSource framework, the L3 seam, the warehouse, the factor model or the scorer daemon before a second positive-excess source exists (C13, C14, C17, C44, C02).

**Success in 8 weeks:**
- Every card is slot-timed, honestly labelled against a beta-matched benchmark, and a distinct bet.
- PEAD is renewed and measured against SPY.
- Two event legs and one composite have each had one powered, pre-registered test.
- The channel carries cards, not noise.

Realistically, 0–1 new sources will pass.

## 2. System map

```
 RESEARCH (CLI only; frozen JSON; journal; never orders)   LIVE DAEMON (one launchd process, one APScheduler)
 [1 MINING]                                                 triggers: 10:35 / 14:35 suggestion scans (FULL)
  pooled/  cohort->cube (long 2xATR/3R/20s)->fixed sets     swing every 4h AND at daemon start (FULL)   <- C09
           ->checks A,C->campaign ─────────── X no route    15m intraday (idle)
  apriori/ PEAD v2, exdiv ── enrol `probe` ─────────┐                │
  alpha/   per-symbol DSL->registry (0 active,      │                v
           16 shadow, every forecast invalid)       │       [DATA] IEX raw 1d/1h; resample("4h") on UTC;
  setups/  replay on SIP 'all'->base rates;         │              yfinance silent fallback          <- C10,C36
           card_stats worker ───────────────────┐   │                v
                                                │   └─────> [2 SIGNAL] StrategyEngine.scan_contract per contract
                                                │              -> ScreenerCandidate (indicators, setup_quality)
                                                │              + FormulaicAlphaStrategy + EarningsDriftService
                                                │       ╔══ RiskEvaluator.evaluate_candidate (evaluator.py:362-917) ═╗ FUSED:
                                                │       ║ bracket+sizing | risk.book_gates | macro/earnings/corp/     ║ signal+
                                                │       ║ regime/session | LLM (no deadline; approve on error)       ║ sizing+risk
                                                │       ╚═════════════════════════════════════════════════════════════╝
                                                │       ╔══ run_scan (copilot.py:682-1677) ═════════════════════════╗ [3 PORTFOLIO]
                                                └─────> ║ sort -setup_quality | budgets+group caps (inline)          ║ untyped:
                                     card evidence line ║ card_policy (off) | BookSizer book_vol (enforce)           ║ tuple[Any,Any,Any]
                                                        ║ record_signal+outbox in one txn | journal (if native_count)║
                                                        ╚═════════════════════════════════════════════════════════════╝
                                                        Telegram card -> tap (freshness gate)
                                                        [5 RISK] risk/ pure rules at scan, tap, admission (typed)
                                                        [4 EXECUTION] FIFO -> preflight -> broker-marked admission -> one POST
                                                                      (GTC limit bracket) -> ratchet (in copilot) -> close service
                                                        [FEEDBACK] card_stats (IEX, 5 bp, 90 d) -> card line, card_policy
```

| Stage | Where | Contract | Typed? | State |
| --- | --- | --- | --- | --- |
| 1 Mining | `research/pooled`, `apriori`, `alpha`, `setups`; CLI only | Frozen JSON → journal; registry CAS (`storage/alpha.py:280-369`) | Registry yes; lane outputs have no consumer | Rigorous; disconnected from cards except PEAD |
| 2 Signal | `screeners/strategies.py:391-426`, `formulaic.py`, `earnings_drift.py` | One contract → `ScreenerCandidate` (`screeners/base.py:23-52`) | Partly; no μ, horizon or probability | No edge vs SPY; PEAD hard-coded in `run_scan` |
| 3 Portfolio | Inline in `run_scan` (`copilot.py:1174-1177, 1268-1300, 1337-1360, 1396-1425`) | `tuple[Any,Any,Any]` → signal row | No | Near-random selection; inconsistent caps |
| 4 Execution | `execution/entries.py`, `capacity.py`, `closing.py`, `lifetimes.py`, `broker/alpaca.py` | `OrderRequest` → work item → `OrderResult` | Yes | Safe; native policy differs from research labels |
| 5 Risk | `risk/` | Typed → `Rejection \| None` | Yes (entry rules) | Model seam; bounds stop risk and gross notional only |

**Fusion points**
- `evaluate_candidate` and `run_scan` are where stages fuse.
- L3, L4 and L5 are still Planned/Later (docs/alpha-roadmap.md:494-496).

**Research → production channels**
- Registry: zero active alphas.
- PEAD-only catalog probe (`research/apriori/probe.py:20`).
- Config edits.
- The `card_stats` line.
- Shadow artifacts.
- A pooled finalist has no channel.

## 3. Scorecard (engineering rigour / contribution to the north star today)

**Data — strong / weak.**
- *Keep:*
  - lossless raw-page evidence;
  - digest pinning;
  - raw bars for gates and adjusted bars for returns (`research/pooled/cohort.py:205-211`);
  - fail-closed corporate actions;
  - the session builder (`market/bars.py:276-346`).
- *Gaps:*
  - live/research parity (C10);
  - raw-price splits (C36);
  - survivorship and look-ahead membership (C07);
  - no reference data (C16, C22);
  - perishable point-in-time inputs discarded (C20);
  - flat costs (C48);
  - no evidence retention, and a silent yfinance fallback when the disk fills (C17);
  - short history (C50).

**Alpha mining and methodology — excellent / very weak.**
- *Keep:* pre-registration, charge-before-read, session-paired controls, checks A/B/C, honest write-ups.
- *Gaps:*
  - power below plausible effects, and nulls not reported as bounds (C01, C39);
  - no composite (C06, C24);
  - a risk-, beta- and cost-sensitive statistic (C05, C23);
  - one horizon for every hypothesis (C37);
  - fidelity errors against the cited literature (C19);
  - an ungoverned window and no cadence (C02);
  - an unreachable per-symbol path (C29);
  - a fitness-blind genetic search (C49).

**Signals and cards — adequate / weak.**
- *Keep:* full provenance on every card and runner-up; deterministic gates before the LLM; durable budgets; honest caveats.
- *Gaps:*
  - off-slot budget spend (C09);
  - dishonest headline (C03);
  - native research exhausted (C41, C40, C11);
  - PEAD starved (C18);
  - LLM gate fails open (C30);
  - dynamic names unmeasured (C45);
  - no typed selection seam (C13, C14; deferred).

**Risk — excellent / adequate.**
- *Keep:* everything in `risk/`; fail-closed exposure; rules that only ever shrink size.
- *Gaps:*
  - inert de-risking under the notional cap (C12);
  - no beta, sector or clone awareness (C08);
  - uncalibrated macro gates that fail open on calendar failure (C42);
  - tail risk disclosed but unbounded (C43).

**Sizing and portfolio — adequate / weak.**
- *Keep:* closed-form `book_vol`; no forward fill; optimiser discipline; shadow weights never reach orders.
- *Gaps:*
  - R is not a dollar unit (C12, C27);
  - split-unsafe covariance; the tapped tier is not vol-checked (C36, C38);
  - no allocation view on the card (C26);
  - the pinned optimiser yields no information (C44).

**Execution — excellent / adequate.**
- *Keep:* all of it.
- *Gaps:*
  - the native GTC, ratchet and no-time-exit policy is unlabelled by research (C28);
  - blast radius of a single ledger activity or partial fill (C53);
  - legacy footguns (C56).

**Architecture and operations — adequate / adequate.**
- *Keep:* passive `/readyz` versus active `doctor`; debounced incidents; off/preview/enforce modes.
- *Gaps:*
  - silent config loss (C54);
  - unjournaled empty or gated scans (C31);
  - alert fatigue (C52);
  - research/live boundary (C55);
  - dormant harnesses (C56).

## 4. Verified findings ranked by priority

### P0

**C09 — Swing scans spend the session budget off-slot and starve 14:35.** Now; effort S.

*Evidence*
- `register_swing_scan` uses `ScanBudget.FULL` and `next_run_time=datetime.now(UTC)` (`cli/commands/service.py:581-593`, *verified*).
- The "aligned with 4h candle closes" comment is false (`config/config.yaml:330`).
- The session budget counts every signal row, whatever triggered it (`copilot.py:1268-1281`).
- There is no journal-only budget mode (`config.py:186-190`).
- Since 09-18: 15 off-slot cards (about 12 of them swing), 20 at 10:35, 6 at 14:35.
- In 7 of 13 journaled 14:35 scans, every candidate was refused with "per-session budget spent".
- 61 starts in 33 days (live map Q3, Q5, Q7, Q16b).
- `card_stats` pools all triggers, and repeats inflate n.

*Recommend*
- Add `ScanBudget.JOURNAL` (rank and journal; no signal, no outbox) for swing. This keeps scan readiness fresh (5 h max, `config.py:619`).
- Drop run-at-start.
- Set `scheduler.intraday_scan_enabled: false` (`config.py:463`; `service.py:724`).
- Fix the comment.
- Add a daemon-start test.
- Exclude journal-only rows from `card_stats` by `ScanTrigger` (`execution/durable.py:68-77`).
- Investigate the restarts.

*Effect:* predictable timing, honest base rates, and up to about 2× the 14:35 supply (an upper bound).

*Risks:* readiness coupling.

**C18 — PEAD is starved and expires 2026-10-28 with no continue rule.** Now; renewal is one command, the excess job about a week.

*Evidence*
- +0.149R after 5 bp; +0.055R over control (ci90 +0.021/+0.089); the control itself earns +0.094R (docs/apriori-pead-2026-09-26.md "Criteria", "Reading the result").
- Limits:
  - 1 card a session (`config.py:178`);
  - 10:35 only (`screeners/earnings_drift.py:108-109`);
  - $100 risk (`config.py:829`);
  - 30-day term (`config.py:831`; docs/alpha-roadmap.md:574-575).
- So far: 4 cards, 1 executed, 0 closed (live map Q13).
- An expired probe leaves the snapshot, and `pead_decision` journaling stops (`storage/alpha.py:682`; `storage/probe_state.py:57-58`).
- A −4R kill exists (`research/alpha/probe.py:40`); a continue rule does not.
- No excess-vs-SPY measure exists. 2025 gave +0.05R, 2026 +0.01R.

*Recommend*
1. Renew before 10-28 (`--renew`, up to 180 d, `storage/alpha.py:458-471`) under a written fidelity rule:
   - event yield;
   - liquidity rejects (23 of 30 on 10-21);
   - fill versus decision price;
   - Nasdaq gaps;
   - forward counterfactual within the study's distribution.

   Do not use "ci90 < 0 over 40 events"; at sd ≈ 1R it never fires.
2. Offline:
   - SPY- and beta-matched excess on all 4,726 LONG events and on the control;
   - the frozen labeller on 2026-08 → now.
3. Add the every-reporter control to `research/apriori/probe_outcomes.py:89-132`.
4. Scale to 2 cards a session with a per-probe $250 override only if excess ci90 > 0 and excess ≥ 0 over 2024–26.
5. Drop the reaction-only second look.

*Effect:* keeps the only research-to-Telegram route alive. Drift cards are additive (`copilot.py:1270-1279`).

*Risks:* decay (Martineau 2022); survivorship; correlated seasonal exposure (`config.py:181`).

**C01 — Stop single-source, single-formula pooled lanes; don't run insider as designed.** Now; effort S.

*Evidence*
- Gates (all t-tests one-sided):
  - discovery: t ≥ 3, ≥ 3 of 4 positive blocks, leg > 0, ≥ 400 sessions (`research/pooled/campaign.py:732-739`);
  - selection: ≥ 0.5× the discovery edge (789-794);
  - confirmation: Holm plus leg ci90 > 0, trimmed mean > 0 and recent 12 months > 0 (830-842).
- The 64 t-stats fall inside the null.
- Reach is 0.08–0.10R (docs/alpha-pooled-mining.md "Purpose", "Caveats"); the design doc anticipated "few discoveries" (docs/alpha-pooled-mining-research-2026-09-28.md:119).
- No Part 2 spec exists.
- Corrections:
  - the null-confirmation rate is about 1 in 200 overall (1 in 40 for intraday-v1 only);
  - check A gives 0.40–0.51 at 0.05R (an upper bound);
  - CMP maps to about 0.035–0.05R.

*Recommend*
- Freeze the pattern.
- Report per-formula ci90 and MDE80 by planting δ on each formula's own picks in check C (42 s on v4), and backfill the 64.
- Run a one-page ex-ante yield check without double haircuts.
- Handle insider via C19/C21.
- Name the replacement agenda in the same ruling.

*Risks:* this reads as abandoning mining unless framed as a redirect; decide before checks A/C report.

### P1

**C03 — The card's measured record is censored, repeat-counted, raw-R and IEX-labelled.** Now; effort S.

*Evidence*
- `CardKeyStats`: raw mature mean per (strategy, direction); no excess, de-duplication or timeframe (`research/setups/card_stats.py:43-62, 88-117`, *verified*).
- `implied_ev` adds gross target/stop R to net timeout R (`execution/card_evidence.py:69-73`, *verified*). Replay cost is about 0.077R (+0.116R gross vs +0.039R net), so the line overstates EV by about the whole edge.
- Labels are on IEX (`research/setups/sources.py:26-34`).
- 10-08 → 10-21: n 34 at −0.376 became n 67 at +0.535 (live map Q1, Q6).
- `stats_window_days: 90` (`config/config.yaml:457`) caps n near 150, against the roughly 540–680 needed.

*Recommend*
- Headline: replay prior per (strategy, direction, timeframe) with n and hash.
- Net implied EV.
- The live line de-duplicated per (contract, strategy, timeframe, direction, ET session), complete cohorts only.
- SIP labeller under a new version.
- Defer Aalen–Johansen, shrinkage, confidence sequences and Kelly until n distinct > ~200.

*Risks:* the honest headline is negative for every native card (§9 Q1).

**C04 — Cards are judged on raw R or a beta-one control; natives are about 0.9-beta with near-zero alpha.** Now; effort S.

*Evidence*
- `card_policy.decide` uses raw `mean_r_cost` (`execution/card_policy.py:43-54`, *verified*); it is off, with a null threshold (`config/config.yaml:453-455`).
- `market_r` is a beta-one SPY control scaled into R (`research/setups/outcomes.py:187-219`, *verified*).
- Replay: in 2021–26, beta 0.93 and alpha −8.0 bp. In 2017–21, beta 1.08 (0.82 session-weighted) and alpha +1.7 bp.
- Beta-adjusted excess: −0.062R; −0.034R / −0.094R.
- The stop-quintile "beta" is a truncated-payoff slope mixed with non-equity ETFs.

*Recommend*
- Operator restates test (2) as excess over a beta-matched index position.
- Pre-decision beta computed post hoc by asset class.
- Add `mean_alpha_r` to `CardKeyStats`.
- Keep `card_policy` off until C03 lands.
- Restate the roadmap conclusion.
- Label beta cards.
- Drop the B1 second look; defer FF5.

*Risks:* in-sample betas; rationalising a non-edge.

**C10 — Live and research compute the trigger on different bars.** Now (S) / weeks (M).

*Evidence*
- Live feed is IEX (`config.py:1043-1050`, *verified*); all 3,432 captures on 10-20.
- `resample("4h")` runs on a UTC index including extended hours and the forming bar (`data/market_data.py:119-138`, *verified*).
- Research uses SIP `all` (`research/setups/baserates.py:57-58`).
- Per session: SIP has 16 hourly bars / 4 buckets; IEX SPY 9 / 3; typical IEX names 8 / 2. A 4h EMA20 therefore spans about 5 versus about 10 sessions.
- `fetch_latest_price` has no pin or age check and can fall back to yfinance (`data/providers.py:348-379, 510-529`).
- Replay uses the same UTC resample (`research/setups/replay.py:237`), so the DST shift is shared. **Do not ship a live-only ET freeze before Nov 1.**

*Recommend*
- (a) No yfinance equity fallback in Alpaca mode; a timestamped latest price with a maximum age; provenance stamps.
- (b) Measure trigger agreement from the retained IEX pages against SIP replay.
- (c) If agreement is low:
  - a delayed-SIP 30Min RTH open-anchored builder (PEAD already uses delayed SIP, `copilot.py:397-401`);
  - scans at 10:47 and 14:47;
  - SIP labels;
  - a new base-rate version;
  - a parity test.

*Risks:* the strict minute builder drops thin names; trigger frequency changes.

**C36 — Raw prices across splits; under enforce one reverse split can block every card.** Now; effort S.

*Evidence*
- Raw fetch (`data/providers.py:186`).
- Book sizing accepts only raw frames (`research/setups/covariance.py:20-26`) and builds Ledoit-Wolf over book **plus all ranked candidates** (`execution/book_sizing.py:201`, *verified*).
- Shrinkage toward trace/p·I spreads one outlier to every name.
- An over-budget book gives factor 0 (`risk/book_vol.py:91-95`, *verified*) → `BOOK_SIZING_BLOCKED` (`copilot.py:1421-1427`).
- Size of the effect: a 2:1 split gives $300–510 of book vol against $800; a 1:10 reverse split gives $3.9–5.7k and blocks everything while the book is non-empty (BLK is open).
- Incidence is about 1.3% per name-year.
- Raw EMAs also read a reverse split as a trend (`strategies.py:96-97`).

*Recommend*
- Thread `adjustment` through `CompositeMarketDataProvider.fetch_bars` (`providers.py:488-505`).
- Use the research basis (`all`) or `split`.
- Rename and widen `RAW_ADJUSTMENTS`.
- Add split tests.
- *(Lead reviewer)* Set `book_sizing: preview` until this ships; the rule has never bound (docs/card-evidence.md:232).

**C12 — R is not the live sizing unit; de-risking barely bites.** Now; effort S.

*Evidence*
- `dd × macro` scales the risk budgets (`risk/capital.py:80-93`; `agent/position_sizing.py:153-166`) but not the notional ceiling (`position_sizing.py:95-96`, *verified*).
- The cap binds on 84% of base tiers and 99.9% of max tiers. A 0.5 factor cuts base notional by about 17% and max by about 0%.
- Caps conflict: 8 × $7,500 = $60k against the $40k class default (`config.py:393`). The sixth card is refused (`risk/rules.py:315-329`), not shrunk.
- docs/account-ledger.md:131-133 promises the opposite.

*Recommend*
- Scaled `notional_ceiling` in `risk/`, read by sizing, admission (`rules.py:212-217`) and replacement (`copilot.py:3440`).
- A halving test.
- Consistent caps.
- Reject equal-R.
- Defer k/σ sizing.

**C54 — Hand-wired config silently drops blocks.** Now; effort S. Confirmed.

*Evidence*
- No `session=` or `apriori=` (`config.py:1109-1179`; fields at 842 and 863, *verified*). `enforce_rth` feeds `risk/limits.py:119`.
- Only 5 models forbid extra keys (`config.py:232, 276, 309, 357, 779`).
- `trailing_stop.mode` is unread (`config/config.yaml:421`). The trail is `current − max(R, 1.5R)` (`copilot.py:2569`, *verified*).
- The same bug class dropped `corporate_actions` before 10-13 (docs/alpha-roadmap.md:591).

*Recommend*
- Pass both blocks.
- Fail on unknown top-level keys.
- Forbid extra keys on policy blocks, with a shipped-YAML test.
- Delete or rename the trailing keys.

*Risks:* watchdog restart-loops; inventory the keys first.

**C20 — Perishable point-in-time inputs are discarded.** Now, during Q3 season; effort S.

*Evidence*
- Nasdaq consensus and timing are parsed (`agent/earnings.py:138-148`), held only in a TTL cache (185-240), and only the SHA is journaled (243+).
- The asset master is held in memory (`screeners/dynamic_universe.py:345-366`). `alpha universe-snapshot` (`data/equity_metadata.py:30-63`) exists but has run once.
- `NewsItem` lacks `updated_at` (`data/news.py:33-48`).

*Recommend*
- A nightly research acquisition outside the daemon: Nasdaq pages for D−5..D+30 plus the asset master, digest-pinned.
- Keep the raw body that `reported_rows` hashes.
- Add `updated_at`.
- Drop VIX and FRED here.
- Name the first read: vintage versus revised surprise after Q3.

**C19 — The insider campaigns don't test CMP as cited; short-v1 measured a different variable.** Now, before approval; effort S.

*Evidence*
- `_routine` (`research/pooled/insider_fields.py:230-233`): no history requirement, no exclusion of unclassifiable insiders, purchases only, rolling per trade.
- The decile-under-bracket test (`config/research/pooled/insider-v1.json:11-17, 266-278, 315-318`) cites CMP (204-206).
- FINRA CNMS is off-exchange data (`research/short_volume.py:42`), while the cited paper (BJZ) uses NYSE orders.
- `quiet_momentum` is a 20-session sum attributed to Jegadeesh-Titman (`events-v1.json:186-188`); that is the reversal horizon.

*Recommend*
- Annotate v1: "not CMP; a null does not close CMP".
- Require a literature-fidelity table before any SHA is pinned.
- Run CMP faithfully as an a priori entry (C21).
- Add doc caveats.

**C21 — Generalise the a priori event design.** Now; effort S–M.

*Evidence*
- The pooled lane uses daily baskets with no hold-skipping (`research/pooled/formula.py:195-215`) measured against every eligible name (`stats.py:54-65`), so the pooled `pead_*` nulls do not test the event design.
- The catalog route is PEAD-only (`research/apriori/probe.py:20`, *verified*; `copilot.py:897-948, 1138-1170, 1273-1279`).
- The Form 4 cache covers the whole market (`research/insider_filings.py:496-596`).

*Recommend*
- Freeze `insider-cmp` v1 as follows, after a label-blind power count:
  - CMP classification with 3-year history; unclassifiable insiders excluded;
  - open-market purchases by officers and directors;
  - entry at 10:35 on F+1;
  - 20-session primary hold;
  - non-opportunistic purchasers as the control;
  - P1–P4.
- Make the pooled cluster/opportunistic formulas second looks.
- Build `CatalogCandidateSource` and a live Form 4 feed only after a pass.
- Drop exdiv v2.
- Note: insider buys share PEAD's season and drift caps (`config.py:178, 181`).

**C41 — Stop native construction research.** Now; decision.

*Evidence*
- Four studies close it:
  - ranker −0.293R vs −0.126R, CI [−0.325, −0.016];
  - shorts −0.325R;
  - recurrence: `no_change`;
  - A0 −0.110R / −0.103R vs SPY.
- Defects are real but already embedded in the replay prior:
  - the RSI trigger fires on a falling RSI (`screeners/strategies.py:146-149`; `config/config.yaml:311-312`, *verified*);
  - `abs()` scores a decline as recovery (`strategies.py:42`, *verified*).

*Recommend*
- Freeze native research (exceptions: C40, C11); no native-v2.
- Put the replay excess on the cards.
- Let `card_policy` on excess decide, once another source can fill sessions.

**C11 — Tight 4h-ATR stops make R ill-conditioned; the vol_20 lead looks like a cost artefact.** Today (hours) / weeks (M).

*Evidence*
- Stop is 1.5 × 4h ATR (`agent/evaluator.py:207-214`).
- Cost-in-R has no floor (`research/setups/labels.py:144`, *verified*).
- Median stop is 1.80%; mean cost 0.077R (0.12R on squeeze-1h).
- SPY is uncosted and unclipped (`research/setups/bracket_counterfactual.py:172-177`): +0.211 becomes +0.148 once clipped.
- vol_20 correlates +0.069 with r_cost but +0.010 with gross r (CI [−0.009, +0.028]) and +0.66 with stop%. That puts H2d's result (`research/setups/study.py:502-504`) in doubt.

*Recommend*
- **Today, before reading the 10-21→11-04 window:** declare both reads and re-run H2d/H2h on gross r (`research/setups/runner.py:337-354`).
- Run a zero-cost screen.
- If warranted, run stop width as bracket counterfactual v2 (1–3× daily ATR, vol-normalised, beta-adjusted alpha per dollar) and fold in the SPY fix.
- Realistic upside is alpha near zero, not quality.

**C08 — Allocation is nominal; clones carded twice.** Now (slice); effort S.

*Evidence*
- Gross caps (`risk/rules.py:303-329`).
- Label groups (`rules.py:332-348`).
- 10-20: VTI blocked SPY, then VOO was sent at 14:38 (live map Q3, Q7).
- Statistical correlation is off and fetched per pair (`config.py:397`; `agent/evaluator.py:503-523`).
- PENDING cards from earlier scans are invisible to the book (`storage/db.py:859-870`).
- The L3 beta/sector bounds never shipped (docs/alpha-roadmap.md:521-524).
- A factor model would not forecast better; its value is reporting.

*Recommend*
- Group or exclude broad-index clones.
- ρ > 0.8 de-duplication in preview, using BookSizer's matrix plus SPY.
- Retire the yfinance check.
- A β$ card line (after C36).
- Defer the factor model and caps.

**C52 — Cards compete with ops traffic; judgment isn't captured.** Now; effort S.

*Evidence*
- About 33 of 42 cards expired untapped; 0 of 6 at 14:35 were tapped; 3 fills.
- One audible chat (`config.py:870, 988`).
- 659 incidents versus 41 cards, but only 28 in weekday regular hours. This is alert fatigue, not burial.
- Research lanes sit in the same incident machinery (`diagnostics/readiness.py:24-28, 88-95`) with hourly reminders (`config.py:539`).
- The Dismiss button exists (`notifier/telegram_bot.py:1455, 1461`) and has never been used.

*Recommend*
- Severity tier: research lanes send open/recovered only, plus the digest.
- Turn off the decisions worker.
- Add Dismiss reasons.
- Ask the operator.
- Ratchet notices go away with C28.
- Never mute trading-critical incidents.

**C31 — No scorecard; funnel denominators unjournaled.** Now; effort S.

*Evidence*
- The ranked journal is written only when `native_count > 0` (`copilot.py:1591`, *verified*). That is narrower than contract 8 in CLAUDE.md.
- Collect-phase refusals are log-only (`copilot.py:1754-1766`).
- 12 of 39 slots lack the event.
- `schema_version` is hard-coded to 1 (`storage/workflow.py:155`).
- Calibration is never measured.

*Recommend*
- One event per non-dry scan (slot, trigger, raw count, refusal counts per `RiskRule`, outcomes), validated by a pydantic model.
- Build the digest from it.
- Add to `cards outcomes`:
  - slots journaled / scheduled;
  - cards per slot by source;
  - refusal share;
  - tap rate;
  - notices per card;
  - calibration table.

**C06 — Spend the last window on one pre-registered composite.** Weeks; effort M.

*Evidence*
- One source per build (`research/pooled/runner.py:317-318`, *verified*); time-series DSL only.
- Earlier 2–3 component blends were weak, but on a different label.
- Components score 0.013–0.034 Rank IC (docs/alpha-pooled-checks-v4-2026-10-07.md "Label diagnostic").
- Selection-window SE is about 0.02–0.03R.

*Recommend*
- M0: a bars-only composite with mechanical OSAP signs.
- Declare the MDE (≤ 0.04R, or don't run).
- Use the risk-matched control.
- Read the selection window, then confirmation only on a pass.
- Choose the label before freezing.
- Record the composite percentile on native candidates.
- Defer M1–M3, PBO and Qlib.

**C24 — Cross-sectional DSL operators and a frozen feature library.** Weeks; effort S–M.

*Evidence*
- Per-symbol DSL (`research/alpha/dsl.py:1-5`); global unit-only fields (28-48); helpers outside the grammar (`research/alpha/panel.py:19-37`).
- Boolean-division masks.
- `log(close)` passes as dimensionless (`operators.py:125`). Fix by requiring a dimensionless argument.

*Recommend*
- cs ops on [S, N] scores, masked to eligibility.
- A `composite` protocol type.
- library-v1 (about 12 OSAP signals).
- Fix `log`.
- Defer vectorisation.

**C23 — Test information across the whole cross-section before tradability.** Weeks; S diagnostic first.

*Evidence*
- One fused top-decile test (`formula.py:195-215`; `stats.py:140-145`).
- A Fama-MacBeth slope gives about 1.1–1.7× the t.
- Most of the 31% → 65% power gain comes from Holm on pooled windows.

*Recommend*
- A charged, discovery-only diagnostic (`research/alpha/information.py:168`).
- Build v5 only if slope t ≳ 2.5 persists.
- Adopt pooled Holm prospectively.

**C25 — Test the earnings-announcement premium the blackout excludes.** Weeks; effort M.

*Evidence*
- Blackout runs at scan and tap (`config/config.yaml:279`; `agent/evaluator.py:543-571`; `copilot.py:3256-3259`). It is untested, yet positions can still ride through earnings.
- The tradable window is about 34–37 bp, roughly 0.035–0.04R.
- Recent US decay (Heitz et al.).

*Recommend*
- `ea-premium-v1` as an exdiv clone with two Holm arms: pre-only (E−5→E−1) and through (exit E+1).
- Predicted dates.
- Non-announcer and placebo controls.
- Declared overlap.
- A live exemption only after a P2 pass.

**C37 — One 20-session bracket for every hypothesis; "bracket closed" over-generalised.** Weeks; effort M.

*Evidence*
- One bracket (`research/pooled/cube.py:57-71, 284-295`).
- The counterfactual tested native exits only.
- The v4 diagnostic tested 5/20-session returns on the daily set only.
- `_LABEL_SPAN = 45 d` (`cube.py:52`) blocks 60-session labels.
- `MAX_HOLDING_SESSIONS = 60` (`execution/lifetime_policy.py:75`).

*Recommend*
- Hold insider for the a priori route.
- Record a 60-session hedged shadow outcome for each PEAD decision.
- Add multi-horizon arrays in the C05 rebuild.

### P2

- **C38 — Book vol rarely binds and sizes the wrong book** (merge with C36).
  - *Evidence:*
    - binds about 32% at 8 × $7,500;
    - weights are recorded notional (`execution/book_sizing.py:126-141`);
    - the factor is computed on the default tier (339) and never rechecked at admission (docs/risk-policy.md:113-114).
  - *Recommend:* split-safe bars; factor on the max tier; daily block count. Defer ERC.
- **C30 — LLM gate.**
  - *Evidence:*
    - no timeout (`agent/evaluator.py:751-760`, *verified*);
    - fallback approves and shows `{e}` (886-917, *verified*); journaled `llm: None` (`copilot.py:656-664`);
    - 14/14 approvals, with SPY and CEG edits unmeasured.
  - *Recommend:*
    - `wait_for`;
    - a `{'error': TypeName}` verdict plus a counter;
    - fixed fallback text;
    - native commentary-only switch (reuse 858-870);
    - prompt changes need traces.
- **C53 — Ledger and partial-fill blast radius.**
  - *Evidence:*
    - 8 activity types (`accounting/ledger.py:18-23`);
    - unready → raise (`accounting/risk.py:92-93`) → scans drop everything (`copilot.py:1724-1740`);
    - no recovery path (`risk.py:184-187, 214-215`);
    - partial fills block all admission (`execution/capacity.py:103-112, 213-216`).
  - *Recommend:*
    - query the journal first;
    - classify Alpaca variants;
    - ack and re-baseline commands;
    - an incident on an all-drop scan;
    - sign-aware handling if decoupled.
- **C02 — No cadence; ungoverned window.**
  - *Evidence:*
    - shadows all invalid;
    - daily panel NaNs the whole cross-section on one missing label (`research/alpha/daily_forecasts.py:550-551`, *verified*);
    - the overlap rule (`research/pooled/campaign_run.py:107`, *verified*) misses the PEAD and exdiv reads.
  - *Recommend:*
    - window policy and inventory before insider;
    - family-aware overlap;
    - decisions worker off (`config/config.yaml:500-507`);
    - k-missing-label rule;
    - frozen forward declaration 2026-08→2027-07;
    - scorer daemon later.
- **C05 — Pooled statistic not risk-, beta- or cost-neutral.**
  - *Evidence:* pool control (`research/pooled/stats.py:54-105`); cost in R = bps/ATR%; ATR computed (`research/pooled/cohort.py:270-275`) but not stored (`cube.py:53`).
  - *Recommend:*
    - step 1: discovery-only gross/bps re-score plus regression on the control mean;
    - persist `atr_pct` and `beta_60`;
    - Fama-MacBeth residual as a descriptive column.
- **C07 — Membership look-ahead.**
  - *Evidence:*
    - 2026 names ranked on 2023 volume (`config/research/pooled/cohort-v2.json:4`);
    - spike doc line 41 misdates discovery;
    - `asof` never set (`data/providers.py:244-252`).
  - *Recommend:*
    - fix line 41;
    - binding rule: re-score survivors on a point-in-time cube before confirmation, and no model trains on v2/v3;
    - later, add the 2,147 inactive symbols plus a terminal-bar rule.
- **C13 — No live forecast field.**
  - *Evidence:* `AlphaForecast` exists (`research/alpha/forecasts.py:26-196`), but `ScreenerCandidate` has no μ.
  - *Recommend:* reuse it inside L3 later.
- **C15 — About ten touch points per source.**
  - *Evidence:* `campaign.py:403-584`; `scoring.py:324-377`; `runner.py:317-318`.
  - *Recommend:* allow a tuple of sources in the composite PR, with golden-hash tests.
- **C16 — No reference data.**
  - *Evidence:* hand-labelled sectors (`config.py:101-104`); dynamic names escape `correlation_group` (`risk/rules.py:332-348`).
  - *Recommend:* a thin `alpha sic` → French FF12/49 mapping, giving dynamic groups and a cube sector array; FF5+UMD once.
- **C17 — Store and retention.**
  - *Evidence:*
    - evidence grows about 320 MB/day and is never deleted;
    - below 1 GiB free, `check_capacity` raises (`data/evidence.py:93-104`), which triggers a silent yfinance fallback (`providers.py:505-529`).
  - *Recommend (lead reviewer: retention slice now):* gzip after 7 days; disk alert; fail visibly; defer the store.
- **C22 — PEAD inputs.**
  - *Evidence:*
    - no timing; D+2 entry (`research/apriori/pead_live.py:122`);
    - EDGAR `acceptanceDateTime` JSON is not a true UTC instant (Apple 16:30:35 ET shows `00:30:35Z`);
    - XBRL EPS lags.
  - *Recommend:* C20 capture plus the control arm; v3 is a demote-only sensitivity check.
- **C26 — No allocation view on the card.**
  - *Evidence:* the card shows the prior and the max-tier binding (`execution/card_evidence.py:157-195`; `position_sizing.py:139-147, 219-221`), but not why base risk is about $135.
  - *Recommend:* a per-tier `binding` field.
- **C27 — No desk dollar replay.**
  - *Evidence:* equal-R −$27.29, live −$7.45, equal-notional −$7.32; legacy `backtest/engine.py` doesn't match the desk.
  - *Recommend:* bp and dollar columns now; L5 replay for a finalist.
- **C28 — Native execution policy.**
  - *Evidence:*
    - GTC default (`copilot.py:2967-2978`; `broker/base.py:98`, *verified*);
    - lifetimes apply to alpha only (`execution/lifetimes.py:209-213`, *verified*);
    - ratchet −0.013R overall (about −0.24R on affected setups), +0.005R in 2017–21.
  - *Recommend:*
    - ratchet off as a simplification;
    - filled vs unfilled R;
    - a lifetime via a dedicated field if resting entries exist;
    - never DAY TIF (docs/alpha-trade-lifetimes.md:24-26).
- **C29 — Per-symbol path is unreachable.**
  - *Evidence:* holdout DSR is deflated by the lifetime family (`research/alpha/promotion.py:291-302, 370, 388`), though 0/64 has joint causes; the shadows are invalid.
  - *Recommend:* `alpha demote` the 16 (`cli/commands/alpha.py:477-488`); one roadmap line.
- **C32 — News.**
  - *Evidence:* the prompt hash covers the system prompt only (`agent/news_context.py:49`); a model alias (`config.py:871`); distraction effect.
  - *Recommend:* text contract; `updated_at`; a frozen forward read.
- **C33 — Options.**
  - *Evidence:* yfinance only (`options/fetcher.py:9, 67, 88`); Alpaca indicative has no OI and history only from 2024-02; history is purchasable.
  - *Recommend:* a retrospective veto test; thin capture.
- **C40 — Trigger decomposition.**
  - *Evidence:* the only control is β = 1 (`outcomes.py:187-219`).
  - *Recommend:* T/P/U as a one-day add-on, gating native-v2.
- **C42 — Macro and regime gates.**
  - *Evidence:*
    - inversion forces HIGH stress (`agent/macro.py:149-150, 255-259`);
    - VIX fallback multiplier is 1.0 (`agent/regime.py:185-189`);
    - FF failure → "CLEAR" (`agent/calendar.py:156-160, 210-221`, *verified*).
  - *Recommend:* static FOMC/BLS primary; UNVERIFIED label; inversion to preview; map the fallback.
- **C45 — Dynamic names.**
  - *Evidence:* up to 20 per scan; no sector or corporate-action coverage; labelled but pooled.
  - *Recommend:* static/dynamic split; a "not measured" line; risk groups.
- **C48 — Flat costs.**
  - *Evidence:* `cube.py:86`; `outcomes.py:65`; arrival quote is IEX.
  - *Recommend:* a half-spread query; a finalist spread stress (EDGE).
- **C50 — Calendar time binds.**
  - *Evidence:* about 1,357 sessions (836 short); no factor data (`research/alpha/factor_features.py:335`).
  - *Recommend:* FF5 attribution; OSAP/JKP yield gate; free survivorship repair; Sharadar only for a candidate.
- **C51 — Source ranking and macro history.**
  - *Evidence:* latest-only gates (`agent/macro.py:283-349`); the FRED client already fetches full history (308-345).
  - *Recommend:* a pinned FRED acquisition (descriptive); record a skip list (13F, Trends, ETF flows, index inclusion).

### P3

- **C14 L3 seam.** `ranking_key` is constant (`copilot.py:1907`). Extend `cards outcomes` (`outcomes.py:645-675`) with `excess_r` challengers first.
- **C34 Meta-labelling.** Ridge already lost; re-score on excess first.
- **C35 LLM mining.**
  - Already policy (docs/alpha-roadmap.md:455-456). Close item 5 (860-866): label-blind only.
  - AlphaAgent's IC of 0.0056 maps to about 0.02R.
- **C39 Thresholds.** Report ci90 (`research/pooled/stats.py:127-147`), MDE and equivalence; never re-tune after the fact.
- **C43 Gap risk.** Disclaimed (`execution/capacity.py:3`). Add held-earnings and β$ lines first.
- **C44 Pinned optimiser** (`execution/book_sizing.py:434-445`). Make it a test oracle; μ ≤ 0 gives zero.
- **C46 Vetoes.** Wrong sign versus vol_20 and a window already read; add descriptive columns.
- **C47 Reversal.** `reversal-lowmax-v1` already tested it (+0.008R); use a generic short-horizon cube.
- **C49 Fitness-blind GP.**
  - `ask` samples parents uniformly from `sorted(archive)` (`research/alpha/search.py:175-180`, *verified*), and `tell` evicts only above `archive_size` (89-111).
  - Add a note and the validator `archive_size < min(family_budgets)`.
- **C55 Imports.** A narrow import-linter contract; move kernels in L5.
- **C56 Legacy.** Delete `RedundantBroker`: one config line arms POST failover to the paper simulator (`broker/redundant.py:121-161`; `config.py:502-507`). Use direct submit instead of the slicer.
- **C57 Tooling.** Use outside tools as test oracles and definitions only; avoid mlfinlab (commercial) and keep vectorbt (Commons Clause) out of shared kernels.

## 5. Recommended roadmap

### Now (days, mostly before 2026-10-28)

**Operator decisions**
- **D1 Insider:** hold approvals until `insider-cmp` is frozen, or annotate them (C19, C21, C37). Decide before checks A/C report.
- **D2 PEAD:** renew before 10-28 under a fidelity rule (C18).
- **D3 Card timing:** cards only at 10:35, at 14:35 and from operator scans (C09).
- **D4 Card headline:** replay prior on the card; test (2) restated as beta-matched excess (C03, C04).
- **D5 Freeze:** single-source fixed sets and native research (C01, C41).
- **D6 Book sizing:** `book_sizing: preview` until bars are split-safe (C36).

**Work**

| # | Work | Findings |
| --- | --- | --- |
| N1 | Journal-only swing; no run-at-start; intraday off; restarts investigated | C09 |
| N2 | Today: declare the gross/net vol_20 reads; re-run H2d on gross r | C11 |
| N3 | PEAD renewal; SPY excess offline; forward read; control arm | C18, C22 |
| N4 | `insider-cmp` freeze (power count first); fidelity table; doc annotations | C19, C21 |
| N5 | Card evidence: replay headline, net EV, de-duplicated live line, `alpha_r`, bp/$ columns | C03, C04, C27 |
| N6 | Data integrity: no yfinance equities; timestamped latest price; provenance; split-safe frames; evidence retention and disk alert | C10a, C36, C17 |
| N7 | Config wiring; scaled ceiling and caps; LLM deadline and honest fallback | C54, C12, C30 |
| N8 | Clone group/exclusion; ρ dedup preview; tier binding labels | C08, C26 |
| N9 | Incident tiers; decisions worker off; demote 16 shadows; k-missing rule | C52, C02, C29 |
| N10 | One scan event per scan, with refusal counts; digest from journal | C31 |
| N11 | Nasdaq and asset-master nightly capture; news `updated_at` | C20 |
| N12 | ci90/MDE for the 64; spike line 41; point-in-time rule | C01, C07, C39 |

**Metrics by 11-07**
- 100% of slots journaled.
- Zero off-slot cards.
- PEAD renewed, with its SPY-excess published.
- Replay prior and net EV on every card.
- Zero unknown config keys.
- LLM fallbacks counted.
- Incidents per card down by about 10×.

### Next (weeks 2–8)

| # | Work | Findings | Decision / metric |
| --- | --- | --- | --- |
| X1 | Run `insider-cmp`; `ea-premium-v1` (two arms); PEAD 60-session shadow | C21, C25, C37 | A pass leads to a probe PR with the `CatalogCandidateSource` extraction and a parity test |
| X2 | Composite M0: cs ops, library, composite protocol, declared MDE, risk control; window policy; family-aware overlap; C05 step 1; C23 diagnostic | C06, C24, C05, C23, C02, C07 | Operator approves spending selection, then confirmation; MDE ≤ 0.04R |
| X3 | Bar-parity measurement; if low, the delayed-SIP RTH builder, :47 scans, SIP labels, new base rate, parity test | C10 | Gates the `card_policy` preview |
| X4 | `card_policy` preview on beta-adjusted alpha | C03, C04 | Enforce vs labelled stream (§9 Q1) |
| X5 | T/P/U decomposition; stop-width v2 if warranted | C40, C11 | T−P ≤ 0 → natives workflow-only |
| X6 | SIC groups and cube sector; FF5; FRED history | C16, C50, C51 | Dynamic names inside `correlation_group` |
| X7 | Ledger variants and ack commands; ratchet off; native LLM commentary-only; dynamic split | C53, C28, C30, C45 | Rulings on contracts 2 and 3 |
| X8 | News contract; spread query; GP note; delete `RedundantBroker` | C32, C48, C49, C56 | — |

**Metrics by early December**
- At least two powered event tests and one composite verdict, each with a stated MDE.
- Cards judged on beta-adjusted alpha, with a published would-withhold rate.
- Live/replay agreement ≥ 0.9, or a new base rate.
- Zero clone pairs.
- Research effort visibly reaches card sources (versus 0 of 5 recently).

### Later (months, each gated)

| Item | Findings | Gate |
| --- | --- | --- |
| L3 seam and forecast field | C14, C13 | A second positive-excess source |
| L5 kernel and desk replay (replacing `backtest/engine.py`) | C27, C55, C56 | An entry-side finalist |
| Factor model / ERC / allocator | C08, C38, C44 | Book ≥ 5 positions or positive EV |
| Scorer daemon | C02 | Composite with plausible ≥ 0.05R |
| FieldSource plugins, store, live Form 4/FINRA | C15, C17 | A field survives selection |
| Paid history and options | C50, C33 | A specific candidate needs it |
| Scenario rules / CVaR | C43 | Measured gap-through losses |

## 6. Outside work to integrate (verified links)

**Inference**

| Reference | Where it applies |
| --- | --- |
| Harvey & Liu 2020 — https://arxiv.org/abs/2006.04269v1 | MDE, yield (C01, C39) |
| Harvey, Liu & Zhu 2016 — https://doi.org/10.1093/rfs/hhv059 | Fixed-set gates |
| Howard et al. confidence sequences — https://arxiv.org/abs/1810.08240 | Daily-read policies (C03, C02) |
| Lan & DeMets 1983 — https://doi.org/10.2307/2336502 | Window spending |
| lifelines (Aalen–Johansen) — https://lifelines.readthedocs.io | `card_stats` v2, deferred |
| A. Y. Chen — https://arxiv.org/pdf/2206.15365 | Literature sign priors |

**Anomaly priors**

| Reference | Where it applies |
| --- | --- |
| OSAP — https://www.openassetpricing.com | library-v1, signs, yield gate (C24, C06, C50) |
| JKP — https://jkpfactors.com | Themes |
| French Data Library — https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html and 49 industries — https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_49_ind_port.html | FF5 attribution, SIC→FF49 (C04, C16) |
| McLean & Pontiff — https://doi.org/10.1111/jofi.12365 | Realistic MDE targets |
| Hou, Xue & Zhang — https://doi.org/10.1093/rfs/hhy131 | Realistic MDE targets |
| Gu, Kelly & Xiu — https://doi.org/10.1093/rfs/hhaa009 | Composite rationale |

**Event legs**

| Reference | Where it applies |
| --- | --- |
| Cohen-Malloy-Pomorski — https://www.nber.org/papers/w16454 | `insider-cmp` |
| Lamont & Frazzini — https://www.nber.org/papers/w13090 | EA premium (C25) |
| Savor & Wilson — https://doi.org/10.1111/jofi.12361 | EA premium (C25) |
| Martineau — https://ideas.repec.org/a/now/jnlcfr/104.00000122.html | PEAD decay (C18) |
| Da-Liu-Schaumburg — https://ideas.repec.org/a/inm/ormnsc/v60y2014i3p658-674.html | Residual reversal (C47) |
| Chan 2003 — https://doi.org/10.1016/S0304-405X(03)00146-6 | Small-cap caveat (C46) |

**Costs, text, data**

| Reference | Where it applies |
| --- | --- |
| EDGE spread estimator — https://doi.org/10.1016/j.jfineco.2024.103916 | Finalist cost stress (C48) |
| Glasserman & Lin — https://arxiv.org/abs/2309.17322 | News contract (C32) |
| Lopez-Lira & Tang — https://arxiv.org/html/2304.07619v6 | News contract (C32) |
| SEC EDGAR APIs — https://www.sec.gov/search-filings/edgar-application-programming-interfaces | C16, C22, C07 |
| FINRA short volume — https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files and short interest — https://www.finra.org/finra-data/browse-catalog/equity-short-interest | Short-flow data |
| Alpaca bars — https://docs.alpaca.markets/reference/stockbars, Market Data FAQ (delayed SIP) — https://docs.alpaca.markets/us/docs/market-data-faq, option chain — https://docs.alpaca.markets/reference/optionchain | C10, C36, C07, C33 |

**Tooling (reference designs and oracles only)**

| Tool | Use |
| --- | --- |
| Qlib — https://github.com/microsoft/qlib, data layer — https://qlib.readthedocs.io/en/latest/component/data.html | Alpha158 spec; store design |
| skfolio — https://skfolio.org, CPCV — https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html | Fitted-ranker validation |
| Riskfolio-Lib — https://github.com/dcajasn/Riskfolio-Lib | `book_vol` test oracle |
| alphalens-reloaded — https://github.com/stefan-jansen/alphalens-reloaded | IC tear sheets, research extra |
| cvxportfolio — https://www.cvxportfolio.com | L5 policy simulator |
| NautilusTrader — https://nautilustrader.io/docs/latest/concepts/overview; LEAN Insight — https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/alpha/key-concepts | Kernel and signal-object precedents |
| import-linter — https://import-linter.readthedocs.io/en/stable/ | C55 |
| Hudson & Thames meta-labeling — https://hudsonthames.org/wp-content/uploads/2022/04/Does-Meta-Labeling-Add-to-Signal-Efficacy.pdf | Expect small gains; avoid mlfinlab |
| AlphaAgent — https://arxiv.org/abs/2502.16789, Alpha-GPT — https://arxiv.org/abs/2308.00016, RD-Agent(Q) — https://arxiv.org/html/2505.15155v2 | Evidence against LLM mining loops (C35) |

## 7. What to stop or pause

| Stop / pause | Why | Findings |
| --- | --- | --- |
| Single-source fixed sets on 2016–21 with the 20-session bracket | Results look like the null; no route to cards | C01 |
| Insider campaigns as designed | Loose CMP proxy, weak encoding | C19, C21, C37 |
| Native construction research | Four closing studies; negative excess | C41, C14, C34 |
| A rushed native-v2 before 11-01 | The DST shift is shared with replay | C10, C41 |
| Trusting the +0.54R card mean | Censored, repeated, raw | C03 |
| Card-sending swing and start-up scans; the 15m job | Off-slot spend; idle job | C09 |
| IEX decisions worker; the 16 invalid shadows | 0 valid records out of about 14k; noise | C02, C29, C52 |
| The ratchet | Neutral to slightly negative; 62 notices | C28 |
| Forecast/L3/warehouse/factor model/optimiser/scorer builds | Nothing positive to carry yet | C13, C14, C17, C44, C02 |
| LLM mining; GP revival | About 0.02R ICs; fitness-blind | C35, C49 |
| exdiv v2, reaction-only PEAD, reversal re-test, veto sets | Weak or tested priors; windows already read | C21, C18, C47, C46 |
| Validation policy v2 | Other blockers remain | C29 |
| CVaR/scenario rules; full allocation block | Premature on an empty paper book | C43, C26, C44 |

## 8. Refuted findings and corrected sub-claims

No finding was refuted outright. C54 was confirmed and 56 were partially confirmed. The corrected sub-claims:

- C01: 1-in-40 null confirmation applies to intraday-v1 only (about 1 in 200 overall). Power figures were approximate (check A gives 0.40–0.51 at 0.05R). Insider is 1.3–2× short, not 3–13×.
- C02: the daily panel already refits Ridge per session (`research/alpha/daily_forecasts.py:247-297`). Setup reads are covered by `excluded_families`; the real gap is the a priori PEAD/exdiv reads.
- C03: the swing was censoring plus a favourable regime, not censoring alone.
- C04: ~0.9 beta holds for 2021–26 only.
- C05: the cube stores `dollar_volume`, and outcomes has `excess_r`.
- C06: blends were tested (weakly, on another label).
- C07: the spike deferred for other reasons; the misdating distorted framing.
- C08: VOO/VTI came from overlapping labels, not the dynamic group.
- C09: about 12 of the 15 off-slot cards came from swing scans; `card_stats` pools triggers.
- C10: parity gap partly acknowledged; DST shift shared.
- C11: stop width tested once, on 30 live candidates.
- C12: risk budgets do scale; only the notional ceiling doesn't.
- C13: forecast types exist.
- C14: `ranking_key` is journaled, as a constant.
- C15: the Nasdaq parser is live.
- C17: the store is about 0.5% of disk, and would exhaust the remaining space in 9–12 months.
- C20: other point-in-time records accrue; a snapshot tool exists.
- C22: 2,724 pages, not 16,755; Apple accepted 16:30 ET, not 20:30.
- C25: Frazzini-Lamont table is event-time; the window is about 34–37 bp; positions can ride through earnings.
- C29: 0/64 has joint causes, not the double count.
- C30: the LLM runs after the gates.
- C33: options history is purchasable.
- C35: RD-Agent has a NASDAQ-100 test; "best of 20" is not stated.
- C36: blocking needs a reverse split, but in any candidate.
- C38: binds about 32% at full deployment.
- C41: four studies, not five.
- C45: dynamic names are labelled and pooled.
- C46: the window was read three times.
- C47: `reversal-lowmax-v1` exists.
- C49: "512 budget" is unsupported.
- C52: only 28 of 659 incidents fell in weekday RTH.
- C53: partial-fill blocks hit admission only.
- C56: the slicer is the live executor pass-through.
- C57: six slow operators; `ts_rank` about 10–14×.
- References corrected:
  - Barber-Odean does not test subsequent underperformance.
  - Muravyev-Pearson-Pollet is JFE 2025.
  - DGTW matches size, B/M and momentum, not sector.
  - Qlib's PIT layer covers fundamentals only.
  - Lakonishok-Lee is about purchases, not clustering.

## 9. Open questions for the operator

1. Is a native card a claim of edge or a workflow exercise? If its honest headline is negative (about −0.11R vs SPY, about −0.06R beta-adjusted), keep sending labelled native cards, or accept empty sessions?
2. Mandate: is a long-biased book acceptable, or should cards be judged, and possibly hedged, net of beta? Should the desk be framed as an index sleeve plus a card overlay?
3. Insider: hold the pooled campaigns for `insider-cmp`, or approve them annotated as a separate trial?
4. PEAD: renew for 90 days under a fidelity rule? Approve auto-entry of probe cards on paper?
5. Keep broad-index ETFs in native card generation?
6. Reserve 2024–26 for one composite, and accept a forward window read once in August 2027 for the rest?
7. Was IEX for scans deliberate? Would you accept scans at about 10:47 and 14:47 on delayed SIP?
8. Turn off the ratchet, and make the native LLM commentary-only?
9. Was the $40k equity cap intended alongside 8 × $7,500?
10. Tier research-lane incidents, or use a second send-only chat? Why are cards untapped: timing, trust or noise?
11. Why does the daemon restart about twice a day?
12. Is there budget for paid data (Sharadar, ORATS) if a pre-registered candidate needs it?

**Answerable from the journal or logs; not checked in this read-only review:**
- the LLM fallback frequency;
- resting unfilled native entries;
- unsupported ledger activities or partial fills seen so far;
- the stop% distribution of sent cards;
- the current sign of the yield curve (whether the inversion gate is live).
