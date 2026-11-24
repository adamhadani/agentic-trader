# Map: live signal generation and card construction

Reviewed at `db82817` (worktree `agentic-trader-intraday`, identical to production). Paths are relative to the repo root. "Now" means what the code does today; "Recommend" marks proposals.

## 1. Purpose and scope

This subsystem turns bars into Telegram "SETUP" cards. A scheduled scan runs over a 159-name static universe plus up to 20 screener names. It evaluates two hand-written technical strategies, any registry formula alphas, and (at 10:35 only) the PEAD catalog probe. Candidates go through deterministic risk gates and are ranked by a heuristic. A per-scan and per-session budget is spent on the top candidates after an LLM review, and each card records its full provenance. This is the only place research alpha can reach the operator, so its seams decide whether a mining success could ever become a card.

## 2. Components and contracts

**Candidate contract.** `ScreenerCandidate` (`agentic_trader/screeners/base.py:23-52`) is a pydantic model carrying:
- contract, symbol, asset class, timeframe, and strategy and direction as plain `str`;
- a price snapshot (EMA20/50/200, RSI14, ATR14, swing high and low from the last 10 bars), `candle_timestamp` (a string) and `trigger_detail`;
- optional `alpha_version`, `alpha_score` and `alpha_policy` (a versioned bracket), `probe`, `catalog_event` (PEAD), and `setup_quality ∈ [0,1]`.

It has no forecast, probability, horizon or feature-vector field. `StrategyProtocol`/`BaseStrategy` (`base.py:55-106`) define `evaluate(data, asset_class) -> list[ScreenerCandidate]`.

**Native strategies** (`agentic_trader/screeners/strategies.py`):
- `TrendPullbackStrategy` (`:53-220`) requires daily close > EMA50 > EMA200, using the in-progress daily bar. It requires the in-progress 4h close to be within 0.5×ATR of 4h EMA20 (`:136-137`). The RSI rule is "min(prev two RSI) < `rsi_oversold_dip` and current ≥ `rsi_oversold`" (`:146-149`), with config values 45 and 40 (`config/config.yaml:311-312`).
- `SqueezeBreakoutStrategy` (`:223-349`) runs on 4h and 1h. It requires ≥5 prior squeeze bars, Volume > 1.3×Volume_SMA_20, and a close crossing the Bollinger band (`:269-307`).
- Shorts are disabled by config (`config/config.yaml:305,320`).
- `setup_quality` heuristics are at `:29-50`. The formula-alpha version is `clamp(|z|/3)` (`screeners/formulaic.py:25-27`), and PEAD sets it to 0.0 (`screeners/earnings_drift.py:214`).

**Engine and registry.**
- `StrategyEngine.scan_contract` (`strategies.py:391-426`) evaluates the active strategies, optionally `native_only` (`:411-412`, used for dynamic names), filters by timeframe and calls `ConflictResolver.resolve`.
- The resolver (`screeners/registry.py:18-110`) nets opposing directions. When several setups agree on a direction, it picks one owner by `min((probe, strategy, timeframe, alpha_version))` (`:96-104`): a lexical, not quality-based, choice.
- `StrategyRegistry.install_alphas` (`registry.py:119-129`) atomically swaps in `FormulaicAlphaStrategy` instances for registry `active` and `probe` definitions before every scan (`agent/copilot.py:757-758`).
- `FormulaicAlphaStrategy` (`formulaic.py:30-161`) is per-symbol and time-series based. It closes bars on the alpha clock, z-normalises the DSL score and fires when the latest z crosses `entry_threshold` (`:102-108`). It has no cross-sectional mode.
- `StrategyEngine` is constructed inline (`copilot.py:327`), not injected.

**Event source.** `EarningsDriftService` (`screeners/earnings_drift.py:82-216`) builds PEAD LONG candidates from `live_events` only at the entry's decision time, 10:35 (`:108-109`). Its candidates are `probe=True`, use the frozen catalog bracket and carry `catalog_event`.
- Budgets: its own derived budget of `max_drift_cards_per_session=1` and at most `max_open_drift_positions=4` (`agentic_trader/config.py:178-181`).
- Risk: `probe_risk_dollars=$100` per trade, with a 30-day term (`config.py:829-831`).

**Universe.**
- Static: 159 symbols in three groups (etf32 / mega_caps 65 / research_cohort 62) in `config/config.yaml:95-258`.
- Dynamic: `select_dynamic` (`screeners/dynamic_universe.py:113-176`) combines Alpaca most-actives (by trades) and movers (by |%chg|), ordered at `:91-98`. It applies symbol-shape, asset, exchange, leveraged and instrument filters and a $10 minimum price. `liquidity_gate` (`:219-265`) then requires a 20-session median dollar volume of at least the 25th percentile of the static equities, measured on the same feed.
- The cap is 20 names (`config.yaml:259-271`), and all dynamic names share the `dynamic` correlation group, so there is at most one dynamic card per session (`copilot.py:614-617`).
- The coverage gate (`screeners/coverage.py:31-48`) excludes equities with fewer than 80% of SPY's regular-session hourly bars.

**Deterministic evaluation.** `RiskEvaluator.evaluate_candidate` (`agent/evaluator.py:362-917`) does the following:
1. Builds the bracket (`calculate_levels_deterministic`, `:166-285`): the stop sits behind the swing extreme and at least 1.5×ATR away, and the target is `required_rr`×stop (2R). A versioned alpha uses its own bracket.
2. Sizes the position.
3. Applies, first refusal wins: the shared `risk.book_gates` (`:477-499`), the optional statistical correlation check (`:501-521`), macro lockout (`:523-541`), earnings blackout (`:543-572`, 7 days, fails open), corporate actions (`:574-635`, `enforce`), regime breakout and reward/risk (`:637-662`) and the session check (`:664-680`).
4. Only then calls the LLM.

**LLM gate** (`evaluator.py:720-884`; prompts in `agent/prompts.py:10-75`):
- The model sees only the candidate's indicator snapshot, `trigger_detail`, exposure, the regime summary and the macro calendar status. The system prompt tells it to "Reject a candidate that violates a configured gate" (`prompts.py:25-26`).
- Code overwrites entry, quantity, direction and macro clearance (`:767-776`). It clamps an LLM stop narrower than 1.5×ATR and a target below the required ratio (`:778-794`). Alpha brackets are fixed (`:796-799`), and a wider stop re-sizes the position (`_final_bracket`, `:287-360`).
- For a catalog probe the verdict is commentary only (`:858-870`).
- If no API key is configured, the candidate is approved deterministically (`:689-718`). **Any exception approves it deterministically** (`:886-917`).

**Scan orchestration.** `TradingCopilot.run_scan` (`agent/copilot.py:682-1677`, about 1,000 lines) holds universe selection, fetching, coverage, dynamic gating, drift, collect, rank, shadow, book sizing, evidence, policy, news, budgets, LLM, recording and journaling. `_admit_candidate` (`:1679-1768`) applies the 12-hour duplicate rule (shortened for 1h/15m), the account-risk read and the deterministic evaluation.

**Evidence and policy.**
- `card_evidence.lookup` (`execution/card_evidence.py:76-126`) reads the daily `card_stats` snapshot, keyed by `(strategy, direction)` (`research/setups/card_stats.py:50-62`).
- `card_policy.decide` (`execution/card_policy.py:37-68`) withholds on `mean_r_cost < min_measured_ev`. It ships `mode: "off"` (`config.yaml:453-455`).
- Shadow features `setup_features_v2` (`research/setups/features.py:44-59`) are journaled per ranked candidate. No ranker artifact is configured.

**News.** `NewsContextService` (`agent/news_context.py:191-270`) does one Benzinga fetch per scan and a direction-free LLM tone score for the first 6 ranked symbols. Its block has `applied: false` and is never seen by the gate (`docs/news-context.md`, "What it is").

**Rendering.** `format_alert_card` (`notifier/telegram_bot.py:233-401`) renders:
- levels;
- the evidence block: measured record, implied EV and caveat (`card_evidence.py:157-198`);
- book, corporate-action and news lines (`telegram_bot.py:365-370`);
- capital risk, macro, earnings and regime;
- tiers, the thesis and execution instructions.

**Seams consumed and produced.**
- Consumed: bars (`data/market_data.py:197-238`); regime (`agent/regime.py:133-227`); calendar, earnings and corporate-action captures; the ledger's `current_risk`; `BookSizer`.
- Produced: a `signals` row with `decision_provenance` and an outbox notification (`copilot.py:1459-1523`); one `scan_candidates_ranked` event per unrestricted budgeted scan (`:1591-1609`, `:1851-1931`); and `dynamic_universe_built` and `pead_decision` events.

## 3. Data flow

1. **Schedule.**
   - Suggestion scans at 10:35 and 14:35 ET (`config.yaml:331`; `cli/commands/service.py:437-460`) run equity-only, `budget=FULL`, `shadow_evidence=True`.
   - A **swing scan** runs every 4h from daemon start, and once at start, also `budget=FULL` (`service.py:581-593`).
   - A 15-minute intraday job scans `non_universe_contracts` at `timeframe="15m"` (`service.py:596-625`). The natives emit no 15m setups and there are no 15m alphas, and Alpaca mode skips futures (`copilot.py:961-964`), so this job is effectively idle.
2. **Pre-scan.** The scan sweeps probes, snapshots and installs alphas, and checks halt. It then reads the regime; this read is unguarded and raises if Yahoo VIX is unavailable (`regime.py:168-169`, `copilot.py:780`). Next it reads the book, then the session gate (`:795-807`), then the whole-scan macro lockout. A lockout returns before any journaling (`:809-831`).
3. **Universe.** The scan builds the dynamic selection (`:876-895`), PEAD events and drift-only symbols (`:897-948`), and applies executability and session filters (`:950-968`).
4. **Fetch.** Per symbol it fetches 1y daily and 60d hourly bars, resamples 4h from the hourly bars, and fetches 15m bars only when a 15m alpha exists. Requests are paced at 150 per minute (`config.yaml` `market_data.max_requests_per_minute`). The hourly bars include the still-forming bar and extended-hours bars. The 4h bars are `resample("4h")` on a UTC index (`market_data.py:119-138`). Indicator parameters are hard-coded (`:140-195`).
5. **Gates.** The coverage gate (`copilot.py:980-1004`) runs, then the dynamic liquidity gate (`:1010-1041`).
6. **Collect.** Per contract (`:1043-1131`) the scan calls `alpha_shadow.observe` (static names only), `scan_contract` and `_admit_candidate`. Drift candidates go through the same admission (`:1133-1170`).
7. **Rank.** Candidates are sorted by `-setup_quality`, then contract, then strategy (`:1174-1177`), and the journal records `"ranking_key": "setup_quality"` (`:1907`). Drift candidates are appended after all native ones (`:1200`).
8. **Context.** Shadow features (`:1193-1201`), book context (`:1209-1224`), evidence and policy (`:1228-1250`) and news (`:1254-1256`) are attached.
9. **Spend.** The budget is derived from today's `signals` rows (`:1268-1283`). Each candidate passes, in order:
   - the scan budget (1), the session budget (2), the group cap (1 per sector or `dynamic`) and the LLM budget (4) (`:1311-1326`);
   - the live-card guard, then the card policy (a no-op while off);
   - the LLM evaluation, where a veto falls through to the next rank (`:1362-1397`);
   - book sizing (`:1399-1427`);
   - `record_signal` and the outbox.
   Later candidates see earlier cards in the book (`:1565-1582`).
10. **Journal and outcomes.** The ranked journal is written (`:1591-1609`). The 08:30 `card_stats` worker labels the journal with the setup-outcome labeller (IEX, 5 bp, 20 sessions), and `copilot cards outcomes` attributes outcomes.

## 4. Evidence of what has been tried

- **Native edge is thin and negative after the market.** In the setup-outcome study, development longs earned about +0.03R after cost. The holdout was negative for every scorer, including random (−0.118R). `setup_quality` has ρ +0.016 in development and −0.012 in the holdout, and the frozen ridge ranker was significantly *worse* (top-2 −0.293R vs −0.126R) (`docs/setup-outcomes-2026-09-23.md`, "Base rates", "Hypotheses", "Selection (H3)").
  - In the bracket counterfactual, live A0 earns +0.039R after cost, but SPY over the same windows earns +0.211R, for **excess −0.110R**. Every arm trails SPY by 0.10–0.15R in both windows, so "the entries lag the index" (`docs/bracket-counterfactual-2026-10-18.md`, summary and "Descriptives").
- **Shorts suppressed.** Native shorts lost −0.33R per setup after cost (ci90 [−0.43, −0.21]) on a fresh 2017–2021 window (`docs/setup-baserates-short-2026-09-23.md`, "Hypotheses").
- **Live funnel.**
  - Runners-up: 22 mature at −0.40R on 2026-10-07 (`docs/alpha-roadmap.md:634-637`); 30% target / 70% stop at −0.17R (n 33); sent cards −0.47R (n 5) (`alpha-roadmap.md:540-543`).
  - `setup_quality` ranks within noise of random (same section).
  - On 2026-10-13 sent cards earned +0.24R against a +0.21R SPY move (`:546-548`).
- **Recurrence.** Repeats are not informative: −0.067R vs firsts (`docs/card-evidence.md`, "Regime and recurrence context").
- **Feature hints.** Only `vol_20` (ρ +0.049) and `sector_rel_mom_60` (+0.028) survive Holm. They are prospective-only, evaluated 2026-10-21 → 11-04 (`docs/alpha-roadmap.md:291-302,630`).
- **LLM gate.** Under the old prompt, 10/10 vetoes came from missing macro timing. Under the new prompt, 12/12 approvals were recorded on two candidates, at $0.018–0.035 and 9–26 s per call. "No further funnel gate was surfaced by the model" (`docs/llm-prompt-eval-2026-09-22.md`, Parts 1-2).
- **Research-positive signal.** The PEAD LONG leg earned +0.149R after cost and beat its control by +0.055R (`docs/apriori-pead-2026-09-26.md`, header). It is live only as a $100-capped probe, which expires 2026-10-28 (`alpha-roadmap.md:574-575`).
- **Mining.** 64 formula-cohort pairs have produced no finalist (CLAUDE.md; `alpha-pooled-*` results).

## 5. Strengths worth preserving

- Typed, explicit records:
  - every ranked candidate, sent or not, is journaled with its outcome reason, LLM verdict, news, book and policy blocks (`copilot.py:1851-1931`);
  - selection, LLM, exposure, slippage, news, regime and recurrence are attributable (`docs/production.md`, "cards outcomes" text);
  - research replay reuses the unchanged live `StrategyEngine` (`research/setups/replay.py:1-24`).
- Deterministic gates come before the LLM, through one shared risk module (`evaluator.py:473-499`). The LLM cannot widen size or loosen the reward/risk ratio (`:778-840`), and catalog probes stay unfiltered by the LLM (`:858-870`).
- The budget is durable and derived from `signals` rows, not a counter (`copilot.py:1268-1280`).
- The card carries an honest measured record and a not-validated caveat (`card_evidence.py:157-198`). The predeclared, one-shot-holdout discipline produced clean negative results instead of overfit rankers.
- Failure isolation: news, shadow, book and journal failures never block or alter a card (`copilot.py:1787-1815`, `:1927-1931`, `news_context.py:204-229`).

## 6. Gaps, weaknesses, risks and inconsistencies

1. **The card budget is spent on a source with measured negative alpha, while the one positive signal is starved.**
   - Now: natives fill the 2-card native budget (`config.yaml:443-444`) at roughly $250 risk each, with an excess of −0.11R versus SPY (bracket counterfactual).
   - Now: PEAD has +0.055R over control but gets 1 card per session, only at 10:35, at $100 risk (`config.py:178,829`).
   - Now: `card_policy` is `off` (`config.yaml:454`).
2. **Ranking on a known-uninformative key, with no swap seam.** The sort key is hard-wired (`copilot.py:1174-1177`) even though `setup_quality` is shown to carry no information (setup-outcomes, H1). The L3 card-selector seam is still "Planned" (`alpha-roadmap.md:494`). `StrategyEngine` is built inline (`copilot.py:327`).
3. **`setup_quality` and the pullback trigger are semantically off.**
   - Because the dip threshold (45) exceeds the recovery threshold (40), the rule "RSI dipped below 45, now ≥ 40" (`strategies.py:146-149`, `config.yaml:311-312`) also fires while RSI is still *falling*, for example 44 → 41.
   - The "recovery" term uses `abs(rsi_now - rsi_extreme)` (`strategies.py:42`), so a falling RSI scores as recovery.
   - `docs/strategies.md:31-34` describes a dip-and-recover rule that this configuration does not enforce.
4. **Bar semantics.**
   - 4h bars come from a UTC-anchored `resample("4h")` (`market_data.py:123-124`) over hourly bars that include extended-hours buckets. `screeners/coverage.py:12-14` notes that SPY prints 08:00 and 16:00 buckets that most names do not.
   - In EDT the buckets are 08–12 and 12–16 ET plus a pure after-hours 16–20 bucket. After DST ends on **2026-11-01** they become 07–11, 11–15 and 15–19, so trend-pullback's EMA20, RSI and ATR change meaning mid-way through the `vol_20`/`sector_rel_mom_60` window (10-21 → 11-04).
   - `Volume_SMA_20` on hourly bars (`market_data.py:193`) averages in thin extended-hours bars where they exist, which loosens the 1.3× volume surge (`strategies.py:273`) for liquid names only.
   - Live acts on the still-forming bar (`replay.py:16-24`).
5. **Dead strategy configuration.** `bb_length`, `bb_std`, `kc_length`, `kc_atr_mult`, `volume_sma_period`, `rsi_period`, `atr_period`, `atr_multiplier` and `strategy_allocations` (`config.py:410-449`; `config.yaml:292-327`) are read nowhere. Indicators are hard-coded at `market_data.py:140-195`. Separately, the swing-scan comment "aligned with 4h candle closes" (`config.yaml:330`) is false: the job runs on an interval counted from daemon start (`service.py:586-591`).
6. **The swing scan shares the native budget.** A `budget=FULL` swing scan at an arbitrary time, including each daemon restart (`service.py:581-593`), can spend the per-scan and per-session budget before 10:35 or 14:35. It does so without the dynamic names, news scoring or shadow evidence of the designed slots, and it inflates repeated journal rows (`docs/card-evidence.md`, "Limits: Repeated setups").
7. **The LLM gate adds no information and fails open.**
   - Its inputs are a subset of what the deterministic gates already enforce (`prompts.py:25-26`, `evaluator.py:457-680`).
   - The `acompletion` call has **no deadline** (`evaluator.py:751-760`), unlike the news scorer (`news_context.py:116-129`), and it runs serially under the scan lock.
   - Any exception, including a malformed veto reply missing `approved` or `thesis_summary` (`evaluator.py:93,112`), becomes an approval (`:886-917`). It is journaled with `llm: None` (`copilot.py:662-664`), so `cards outcomes` cannot tell a fallback approval from a scan that ran without the LLM. The fallback also puts raw exception text on the Telegram card (`evaluator.py:908`), against the project's type-name-only convention.
8. **Evidence measures beta as edge.**
   - `card_stats` and `card_policy` use raw `mean_r_cost` with no SPY control (`card_stats.py:50-62`; `card_policy.py:44,54`). In a rising tape the natives show +0.03 to +0.04R and would not be withheld at a threshold of 0.
   - The card's "Implied EV" mixes gross target/stop R with net timeout R (`card_evidence.py:69-73`; `docs/card-evidence.md` "Evidence block").
   - The key ignores timeframe: squeeze 1h and 4h differ (setup-outcomes "Base rates").
   - Repeated setups are not de-duplicated (`card-evidence.md` "Limits").
9. **Dynamic names are unvalidated and partly unmeasured.**
   - No replay covers them: the setup study used the static 159 (`setup-outcomes-2026-09-23.md:26`).
   - The shadow cross-section excludes them (`ranker.py:245-249`), so their `vol_20` and `sector_rel_mom_60` are NaN (`features.py:262`).
   - `cards outcomes` has no static/dynamic split (`outcomes.py:857-900`), which the open WS3 follow-up asks for (`alpha-roadmap.md:322-323`).
10. **No landing zone for cross-sectional research.** Pooled-lane formulas pick deciles per session. The live executor is per-symbol z-thresholding (`formulaic.py:102-108`), the scan has no cross-sectional stage, and a "Part 2 probe" needs its own spec (`docs/alpha-pooled-mining.md:1016,1368`). The daily-panel forecasts "cannot … send trade recommendations" (`docs/alpha-daily-panel.md:1-6`). A mining success would have no tested route to Telegram.
11. **PEAD is hard-coded into the orchestration**, with `PEAD_STRATEGY_ID` used in budgets, groups and position caps (`copilot.py:626,904,1273-1279`). A second catalog leg would mean more special cases in `run_scan`.
12. **Single points of failure and silent sessions.**
   - A Yahoo VIX failure aborts the whole scan with no journal (`regime.py:168-169`, `copilot.py:780`).
   - A macro-lockout scan journals nothing (`copilot.py:809-831`), so FOMC-day 14:35 sessions vanish from the evidence.
13. **Throughput limits universe growth.** Every scan refetches 1y daily and 60d hourly bars per symbol (`market_data.py:207-215`, `copilot.py:2228-2254`) at 150 requests/min: about 360 requests for 180 names. A cohort-v2/v3-scale universe of 450–1,000 names would take roughly 6–14 minutes of pacing per scan.
14. **Hygiene.** Strategies fall back to an implicit `load_config()` (`strategies.py:75,246`; `StrategyEngine` `:364`), contrary to the explicit-config rule. The resolver chooses the owner by strategy name (`registry.py:96-104`).

## 7. Improvement opportunities

1. **Re-point the budget to evidence.**
   - Set `card_policy: preview` now, with the threshold on **excess R**. Add `mean_excess_r` to `CardKeyStats`; `outcomes.py` already computes `market_r`. Key by `(strategy, timeframe, direction)` and de-duplicate per setup-session.
   - Once preview shows natives below 0 excess, move to `enforce`. Natives then only fill sessions that have no better source.
   - At PEAD probe renewal (expiry 2026-10-28), consider risk parity with native cards and both scan slots for events seen after 10:35. Each change needs its own spec.
   - Expected effect: fewer cards, but the card stream shifts from measured −0.11R excess toward +0.05R-over-control setups.
2. **Build the L3 selector seam with a forecast field.**
   - Add `forecast: {model_id, horizon, expected_r, p_target}` to `ScreenerCandidate`, and inject a `CardSelector` returning a typed selection. The journal's `ranking_key` becomes the selector's id.
   - Pre-register a prospective comparison: `setup_quality`, a `vol_20` + `sector_rel_mom_60` rank composite, and random, scored with the existing `selection` block.
   - Expected effect: small (ρ ≈ 0.03–0.05), but every later research score can plug in without editing `run_scan`.
3. **Fix bar semantics in replay and live together.**
   - Use regular-session-only hourly bars for indicators, session-anchored 4h buckets (for example 09:30–13:30 and 13:30–16:00, via `exchange_calendars` / `pandas_market_calendars`) and an extended-hours-free `Volume_SMA_20`.
   - Correct the RSI trigger (a dip threshold below the recovery threshold, and a signed recovery term).
   - Ship as a predeclared native-v2 replay on the setup-outcome development window with the 2017–2021 sign check, before the 11-01 DST shift contaminates the prospective window.
   - Expected effect: removes known artifacts. It is unlikely, alone, to make the natives positive after the market.
4. **Demote or re-task the LLM.**
   - Add an `asyncio.wait_for` deadline. Record fallback approvals as `llm_verdict = {"error": TypeName}` with their own outcome, and make malformed veto replies fail closed (L4 in `alpha-roadmap.md:495`).
   - If the LLM keeps a decisive role, give it information the gates lack, such as the measured record, bars, peers, the earnings note and news, under a frozen evaluation that uses the L1 `llm_gate` attribution.
   - Expected effect: lower latency and LLM spend with no measured loss, plus honest attribution.
5. **Keep the swing scan out of the native budget.** Make it journal-only (no cards) or align it to session bar closes. Expected effect: the 2 cards come from the full-context slots, and the evidence population is stationary.
6. **Measure dynamic names before they compete.**
   - Compute cross-sectional features over static ∪ dynamic names, with sectors from the asset master or Nasdaq SymDir.
   - Add a `dynamic` split to `cards outcomes`.
   - Run a replay proxy (daily top-N by |return| and by dollar volume) to estimate the base rate. The attention literature predicts adverse selection for longs on extreme movers (Barber & Odean 2008, "All That Glitters"; Da, Engelberg & Gao 2011, "In Search of Attention").
7. **Cross-sectional executor before more mining.** Add a `PanelScoreStrategy` that ranks the scan's cross-section at a frozen decision time, emits the top decile with the cube's own long bracket, and runs as a probe. This gives the pooled lane, and the "panel-driven card source" (`alpha-roadmap.md:450-453`), a parity-tested route to cards.
8. **Generalise catalog candidate sources.** A `CatalogCandidateSource` protocol (decision time, frozen bracket, own budget) would remove the PEAD special cases. Candidate entries: PEAD v2 with EDGAR 8-K timestamps (`alpha-data-augmentation-2026-10-18.md` #3), and a predeclared earnings-announcement-premium leg (Frazzini & Lamont 2007; Barber et al. 2013). The 7-day blackout (`config.yaml:279`) currently excludes that pre-announcement window by design; any change there is an operator risk decision.
9. **Incremental bar cache and multi-symbol requests** (`providers.py:311` already batches daily bars). Persist completed bars and refetch only the forming bar. Expected effect: universe growth toward cohort-v2/v3 breadth at the same cadence, which the operator asked for.
10. **Outside work.**
    - Use alphalens-reloaded tear sheets on journaled `setup_features_v2`.
    - Use Qlib's Alpha158 as a vetted cross-sectional feature library for item 7.
    - Treat natives as a primary signal and run a calibrated secondary classifier (meta-labeling; López de Prado, *AFML*, ch. 3), with conformal intervals (MAPIE) for the card's win-probability line.
    - Test headline tone against next-days excess return on the cached Benzinga history (Lopez-Lira & Tang 2023) as a frozen entry. This is where an LLM could plausibly add signal, unlike the indicator-review gate.

## 8. Open questions

- Live LLM veto rate, `vetoed_mean_r_cost` vs `approved_mean_r_cost`, and the frequency of fallback approvals in logs. No doc records them.
- The split of sent cards by `trigger` (swing vs suggestion vs operator), and how many daemon-start swing scans issued cards.
- Whether Alpaca IEX hourly bars carry extended-hours buckets for most universe names (how much `Volume_SMA_20` and the 4h bars are contaminated), and what share of trend-pullback triggers had a falling RSI.
- Dynamic-name candidate and card counts and outcomes since 2026-09-23.
- The PEAD probe's forward record to date: events, drift cards, fills, and whether it will be renewed at 2026-10-28.
- Whether operator taps are selective, which would make the realised card R differ from the labelled proxy.
