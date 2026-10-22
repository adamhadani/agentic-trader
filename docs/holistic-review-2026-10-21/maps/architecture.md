# Map: orchestration, configuration, operations and the five-stage architecture

Reviewed at `db82817` (worktree `agentic-trader-intraday`, identical to production). All `file:line`
references are relative to the repo root. "Today" means what the code does now; "Recommend" marks
proposals.

## 1. Purpose and scope

This layer turns market data into one or two Telegram suggestion cards per session and runs the
paper desk around them. It covers composition (`TradingCopilot`), the scan loop that runs signals,
selection, sizing and risk, scheduling (APScheduler inside one launchd daemon), configuration
(`load_config` → `AppConfig`), the Telegram/outbox delivery path, readiness and monitoring, and the
route by which research results reach production. The operator's lens (2026-10-05, arXiv 2608.31041,
`docs/alpha-roadmap.md:458-500`) asks for five compartments: mining, signal, portfolio, execution and
risk, with typed seams between them. This map checks the code against that lens.

## 2. Components and contracts

### 2.1 Composition and lifecycle

- **`TradingCopilot`** (`agentic_trader/agent/copilot.py:244`, 4,527 lines, 103 methods). It is the
  composition root, the scan orchestrator, the tap-time execution front end, the reporting layer and
  the chat backend all in one class. Line ranges by responsibility:
  - construction: `__init__`, 250-542, about 25 services;
  - scan orchestration and its helpers: 682-2256;
  - reconciliation, monitoring and trailing stops: 2256-2630;
  - positions and close: 2631-2912;
  - tap, execute, re-price and re-evaluate: 2912-3684;
  - operator scans: 3684-3962;
  - gates, workers and halt: 3962-4247;
  - HTML reports: 4247-4527.

  The constructor accepts injected services, but some are hard-wired: `ForexFactoryCalendar()` and
  `NasdaqEarningsCalendar()` at 343-344, `RegimeDetector` and the LangGraph chat graph at 446.
- **CLI**: `get_copilot_and_config` (`agentic_trader/cli/utils.py:41-62`) builds the whole copilot for
  `scan`, `trade` (10 call sites), `stress` and `service`. The research CLI is separate and large:
  `cli/commands/alpha.py` (2,208 lines) plus nine `alpha_*.py` modules, about 50 `alpha` subcommands.
- **Daemon** (`agentic_trader/cli/commands/service.py:651-810`). It runs on one APScheduler and
  starts these jobs and workers:
  - the swing scan: every 4 h from startup, `ScanBudget.FULL`, first run immediate
    (`service.py:581-593`);
  - the intraday scan: every 15 min with the `15m` filter over `non_universe_contracts`, also
    `FULL` (596-626);
  - the suggestion scans: cron 10:35 and 14:35 ET, `FULL`, `shadow_evidence=True`
    (437-502);
  - the position monitor (1 min), the card-expiry sweep (5 min) and the macro briefing;
  - async workers: the entry/outbox workflow loop, the SPY/QQQ session observer and decision
    workers, the daily-panel worker, the card-stats worker and the corporate-actions worker
    (672-710).
- **Supervision** (`scripts/launchd.sh`):
  - The daemon plist sets `KeepAlive` on crash (`launchd.sh:23-58`).
  - A 60 s watchdog checks the PID and runs `copilot doctor --monitor` (147-181).
  - A market-hours awake agent keeps the host awake (90-145).
  - The weekly miner is retired; `install-miner` prints a notice (21, 202-204).

### 2.2 The five stages today

| Stage | Where it lives | Input → output contract | Typed? |
| --- | --- | --- | --- |
| Mining | `research/alpha`, `research/pooled`, `research/apriori`, `research/spread`, `research/setups`; CLI only | Frozen JSON protocols (`config/research/**`) → journal artifacts. The live registry takes immutable `AlphaDefinition` versions under CAS (`storage/alpha.py:371-378`, gates at 308-340) | Yes (registry); the lane outputs are artifacts with no live consumer |
| Signal | `screeners/` (`StrategyEngine.scan_contract`, `strategies.py:391-426`), `FormulaicAlphaStrategy`, `EarningsDriftService` | `ContractMarketData` for **one contract** → `ScreenerCandidate` (`screeners/base.py:23-53`) | Partly. The candidate carries indicator fields (EMA, RSI, ATR, swings) and a heuristic `setup_quality ∈ [0,1]`; there is no forecast, horizon or probability |
| Portfolio | Inline in `run_scan`: sort (`copilot.py:1174-1176`), budgets (1260-1284), group caps (1286-1300, 1318-1322), `card_policy` (1337-1360), `BookSizer` (1396-1425, `execution/book_sizing.py:283`) | `list[tuple[Any, Any, Any]]` (869) → recorded signal row | No. `BookSizingDecision` is typed; the selection itself is not |
| Execution | `execution/` (`EntryExecutionService`, FIFO, `PositionCloseService`, `TradeLifetimeService`), `broker/`; tap assessment in `copilot.py:3452-3606` | `OrderRequest` (`broker/base.py:83`) → work item → `OrderResult` | Yes (cleanest stage) |
| Risk | `risk/` (pure rules: `RiskLimits`, `Book`, `EntryIntent`, `RiskBudget`, `Rejection`, about 1,000 lines); called from the evaluator, the tap gate (`copilot.py:3247`) and admission. Post-trade trailing lives in `copilot.py:2528-2630` | `EntryIntent`, `Book`, `RiskBudget` → `Rejection \| None` | Yes for entry rules; no for trailing and monitoring |

**Fusion points.** The first is `RiskEvaluator.evaluate_candidate` (`agent/evaluator.py:362-917`). In
one method it builds the bracket and sizes the position (396), runs eight book rules (498), a
statistical-correlation check that does I/O (503-525), the macro, earnings, corporate-action, regime
and session rules, the LLM call (751), clamping, and an approve-on-failure fallback (886-917). The
second is `run_scan` (`copilot.py:682-1677`). It runs signal, selection, sizing, risk re-entry,
journaling, notification and, at the end, position monitoring (1676).

### 2.3 Key contracts at the seams

- **`LLMTradeEvaluation`** (`evaluator.py:92-124`) is the de facto **card DTO**. Six modules use it,
  including `execution/book_sizing.py` and `notifier/outbox.py:77-79`, which re-validates it at
  delivery. It hard-codes policy: `risk_reward_ratio: float = Field(ge=2.0)` (106).
- **`decision_provenance`** (`copilot.py:1462-1491`) is an untyped dict with about 20 keys that has
  grown with each feature (shadow, LLM, book sizing, corporate actions, news, regime, card evidence).
- **`scan_candidates_ranked`** journal (`copilot.py:1851-1932`) is also an untyped payload.
  `WorkflowStore.append` stamps `schema_version=1` on every event (`storage/workflow.py:155`).
- **`run_scan` returns `dict[str, Any] | None`.** `None` means "gated"; the `/scan` reply ignores it
  (`copilot.py:4366-4378`).
- **`TelegramNotifier`** takes 20 callback parameters (`notifier/telegram_bot.py:585-615`). Most are
  `Callable[[], Awaitable[str]]` that return HTML built inside the copilot.
- **Cross-package imports run both ways.** 17 live modules import `agentic_trader.research`, including
  `config.py:59`, `evaluator.py:35` (`bracket_prices`), `storage/workflow.py:40-41` and
  `execution/book_sizing.py` (optimiser, covariance). In the other direction, `research/` imports
  `storage`, `execution`, `screeners` and `agent` about 70 times. No import-boundary linter is
  configured.

### 2.4 Configuration

- `AppConfig` (`config.py:834-932`) holds 30 sub-models. About 385 field lines sit beside a 521-line
  `config/config.yaml` and 40+ frozen research JSONs.
- `load_config` (`config.py:955-1180`) merges YAML, `.envrc` and the environment, then **hand-wires
  each block** into `AppConfig(...)` (1109-1179).
- Only 5 sub-models set `extra="forbid"` (`config.py:232, 276, 309, 357, 779`).
- Config is read once, at construction. Edits need a restart (`docs/production.md` "Ownership and
  scheduling").

### 2.5 Operations

- **Readiness** (`diagnostics/readiness.py`) checks per-component freshness:
  - process components: reconciliation, worker, scan, telegram, delivery and accounting;
  - research workers: the alpha observer, decisions and daily panel;
  - daily workers: card_stats and corporate_actions;
  - work queues: entry and cancel recovery, outbox dead letters and close recovery.
- **External watchdog**: debounced incidents go through the existing outbox
  (`docs/operational-monitoring.md`, "Health contract" and "Incident lifecycle").
- **Prometheus metrics** cover process and transport health only: `trader_scan_duration_seconds`,
  outbox gauges, Telegram poll metrics, loop lag and so on. There are **no funnel metrics**:
  candidates, approvals, sends, withholds, LLM vetoes and LLM fallbacks are not counted.

## 3. Data flow

**Suggestion scan (10:35 or 14:35 ET):**

1. The cron fires (`service.py:467-502`) and gates on the equity session.
2. `run_scan` takes the scan lock (`copilot.py:584`).
3. It sweeps probes, installs the registry snapshot and acknowledges it (`copilot.py:749-760`).
4. It checks the halt, reads the regime, builds a dict-based book of DB positions, and gates on the
   session and macro lockout (`copilot.py:765-831`).
5. It selects the dynamic universe (up to 20 screener names) and prepares the PEAD drift at 10:35
   (`copilot.py:876-948`).
6. **COLLECT.** Per contract: fetch daily, 4h, 1h and optionally 15m bars; apply the coverage gate;
   update alpha shadow observations; run `strategy_engine.scan_contract` in a thread; then call
   `_admit_candidate` (dedup, account risk, **deterministic** `evaluate_candidate`) (`copilot.py:1043-1131`,
   1679-1768).
7. Drift candidates pass through the same admit path (`copilot.py:1133-1170`).
8. **RANK.** Sort by `-setup_quality` (`copilot.py:1174`). Compute shadow-ranker blocks
   (suggestion scans only), the book-sizer context, the `card_stats` evidence, the `card_policy`
   decision and the news blocks.
9. **SEND loop.** Apply the budget, group cap, LLM budget, live-card and card_policy refusals. Then
   run `evaluate_candidate` **again, with the LLM** (`copilot.py:1364`), then `BookSizer.decide`
   (1412), then `record_signal` with the provenance and the notification intent in one transaction
   (1459). The card joins the in-scan book (1565-1582).
10. Journal `scan_candidates_ranked` (1592); journal the drift decision; record readiness; run
    `monitor_positions` (1676).
11. The outbox worker delivers the card. A tap goes to `execute_signal_by_id` (`copilot.py:2912`),
    then `_assess_card_tap` (re-price, regime/earnings/corporate re-gate), then
    `EntryExecutionService` (FIFO, admission with the broker-marked book).

**Other card producers spend the same budget.** The per-session budget comes from today's signal
rows (`copilot.py:1268-1282`; `max_cards_per_session: 2`, `max_cards_per_scan: 1`). The swing scan,
the intraday scan, Telegram `/scan`, the chat tool `trigger_market_scan` (`agent/copilot_tools.py:61-70`)
and Re-evaluate all draw on it. The budget is spent greedily, first come, first served.

**Research → production channels today:**

- (a) **Registry promotion by CAS.** It needs qualification and ≥20 shadow sessions and decisions
  (`storage/alpha.py:308-340`). There are zero active alphas (`docs/alpha-roadmap.md:637`).
- (b) **Catalog paper probe** (`alpha apriori-probe`). PEAD is live through a special branch inside
  `run_scan`.
- (c) **Config edit and restart.** Example: `book_sizing` and `corporate_actions` were flipped to
  `enforce` in #125.
- (d) **Daily `card_stats` evidence block** on each card; `card_policy` ships `off`
  (`config/config.yaml` `card_policy.mode`).
- (e) **Shadow only**: the ranker, with `scan.shadow_ranker_artifact` unset, so it records features
  only; and the optimiser cross-check.

A pooled-lane finalist has no channel. It needs a per-lane "Part 2" probe spec
(`docs/alpha-pooled-mining.md:1016, 1368`).

## 4. Evidence of what has been tried

- **Stage review (2026-10-05).** It found signal, portfolio and risk fused, and no live portfolio
  module. L1 (attribution, #106) and L2 (one risk module, #107) shipped. **L3 (card-selector seam),
  L4 (split evaluator) and L5 (one bracket kernel and sizing protocol) are still planned**
  (`docs/alpha-roadmap.md:494-496`).
- **Card quality.** The "reasonable suggestion" definition fails test (2): runner-ups hit target 27%
  and stop 73% at 2R (`docs/alpha-roadmap.md:507-513`). On 2026-10-10: runner-ups −0.17R (n 33),
  sent −0.47R (n 5), and `setup_quality` "within noise of random" (540-545). The 2026-10-13 sandbox
  review: sent cards +0.24R against SPY +0.21R (546).
- **Bracket counterfactual (2026-10-18).** No exit arm beats the live 2R bracket. Every arm trails
  SPY by 0.10–0.15R. The **live ratchet (A2) is reliably worse, −0.013R (ci90 [−0.020, −0.006])**
  (`docs/alpha-roadmap.md:623`; `docs/bracket-counterfactual-2026-10-18.md:4-5, 94-99`).
- **Who sends cards.** "18 of the 22 cards sent since 2026-09-24" came from swing or operator scans
  and were unjournaled until PR A.1 (`docs/card-evidence.md:378-380`).
- **Mining.** 64 formula–cohort pairs under the frozen bracket, no finalist (CLAUDE.md;
  `docs/alpha-pooled-short-v1-2026-10-20.md`). The ETF32 miner was retired after 62 runs
  (`scripts/launchd.sh:21`).
- **Config loss has happened before.** Before 2026-10-13, `load_config` silently ignored the
  `corporate_actions:` block, including `entry_block: enforce` (`docs/alpha-roadmap.md:591`).
- **Operations.** A sleeping host fired the 10:35 job 14m38s late (`docs/production.md`, "Late and
  missed slots (September 28)").
- **Still-open architecture-review findings.**
  - P2: copilot composition and transport strings (`docs/architecture-review.md:93`).
  - P2: `/scan` describes gated runs as complete (94).
  - P2: research shares executor capacity (96).
  - P2: monitoring shares the host, DB and Telegram (99).

## 5. Strengths worth preserving

- **`risk/` is a model stage seam.** It has pure functions, frozen typed inputs and one
  implementation per rule. Seeded consistency tests tie the scan to admission
  (`docs/alpha-roadmap.md:493`).
- **The execution stage is rigorous.** Durable FIFO, preflight tokens, exact client-ID recovery, an
  exclusive close intent, and a transactional outbox (`notifier/outbox.py`, at least once, retries
  delivery only).
- **Measurement-first culture with predeclared, one-shot protocols.** Charged trials and one-use
  confirmation windows protect against research self-deception at small sample sizes.
- **Live/replay parity is deliberate.** Replay runs the **unchanged live `StrategyEngine` and
  `calculate_levels_deterministic`** (`research/setups/replay.py:1-8, 287-313`) and documents its
  remaining gaps.
- **Passive `/readyz` is separate from active `doctor`; incidents are debounced.** No new
  infrastructure was added (no Redis or second queue), which suits a single-operator desk.
- **Modes stage behaviour change.** `off|preview|enforce` (`card_policy`, `book_sizing`,
  `corporate_actions`) lets a policy be journaled before it acts.

## 6. Gaps, weaknesses, risks and inconsistencies

**G1. There is no portfolio-construction stage; selection is close to random.** Ranking is a sort on
a heuristic (`copilot.py:1174-1176`; the heuristic is `strategies.py:29-50`). Measured, it is "within
noise of random" (`docs/alpha-roadmap.md:540-545`). The only gate that uses measured evidence,
`card_policy`, ships `off`. Budgets and group caps are inline closures (`copilot.py:1288-1300`). For
the north star, the two cards per session are a near-random draw from risk-approved candidates.

**G2. Card timing and population depend on the scheduler and restarts, not on the design.** The swing
scan sends cards with a `FULL` budget, at 4-hour offsets from daemon start, with an immediate first
run (`service.py:581-593`). It uses a different population: no dynamic names, no PEAD and no shadow
evidence (`docs/production.md:22-26`). It produced most cards historically
(`docs/card-evidence.md:378-380`). Replay and `card_stats` model only the 10:35 and 14:35 instants
(`research/setups/replay.py:3-6`). So the base rate printed on a card measures a different
population from the one the card came from. A deploy at 09:40 can spend the session's budget before
10:35.

**G3. The intraday 15-minute job is probably a no-op that still holds the scan lock.** Native
strategies emit only `4h` and `1h` (`screeners/strategies.py:343-348`), and the scan keeps only
`15m` candidates (`copilot.py:1093-1094`). With zero active alphas, nothing can pass. Yet every
15 minutes it takes `_scan_lock`, refetches 15m bars for nine equities and runs `monitor_positions`.
Two consequences: Telegram `/scan` returns "Scan Already Running", and the PEAD lateness clock
(measured after the lock wait, `copilot.py:910-911`) can be pushed later.

**G4. The evaluator fuses signal geometry, sizing, risk and the LLM.** This is
`evaluator.py:362-917`, and L4 is unbuilt. Three consequences:
- The deterministic pass runs twice per winner (`copilot.py:1744`, then 1364).
- Book sizing runs **after** the LLM (`copilot.py:1396-1425`), so LLM budget is spent on cards that
  sizing then blocks.
- Any exception in the LLM branch falls back to **approval** with deterministic levels
  (`evaluator.py:886-917`). That fallback has no metric or readiness signal.

**G5. The seams are untyped, and policy is baked into the DTOs.**
- The scan works on `tuple[Any, Any, Any]` (`copilot.py:869`), 52 `dict[str, Any]` annotations and
  23 duck-typed `getattr(candidate…)` calls.
- The journal payloads grew between 2026-10-05 and 10-14 (llm, news, card_policy, book_sizing,
  regime, trigger) under a constant `schema_version=1` (`storage/workflow.py:155`). Readers must infer
  which fields exist from the event date.
- `LLMTradeEvaluation.risk_reward_ratio` has `ge=2.0` (`evaluator.py:106`), but
  `risk.min_risk_reward_ratio` has no such floor (`risk/limits.py:115`). A predeclared sub-2R
  construction test, or a versioned alpha bracket below 2R, would raise in card construction and
  again at outbox delivery (`notifier/outbox.py:78`).

**G6. The signal contract cannot carry what mining produces.**
- `ScreenerCandidate` is indicator-shaped and evaluated one contract at a time
  (`screeners/base.py:23-48`; `strategies.py:391`). Pooled-lane formulas are cross-sectional
  decile rankers. No live `SignalSource` sees the scan's cross-section.
- Each new family has been added as a branch inside `run_scan`: PEAD at `copilot.py:897-948` and
  1133-1170, dynamic names at 876-896 and 1009-1040, `native_only` at 1090.
- A finalist from the insider or short-volume lane would therefore need bespoke live code, not
  registry enrolment.

**G7. There is no compile-time boundary between research and live.** Imports run both ways (§2.3),
including `config.py:59 → research.alpha.probe` and `storage/workflow.py:40 →
research.alpha.validation`. The shared kernels (bracket policy in `research/alpha/strategy.py`,
labels, covariance, ranker helpers) live under `research/`. A research refactor can change the
daemon's behaviour.

**G8. Config sprawl and silent loss.**
- **`session:` in `config/config.yaml:414` is never passed to `AppConfig`** (`config.py:1109-1179`
  omits `session=`). This is latent today, because the YAML values equal the code defaults, but it is
  the same class of bug as the `corporate_actions` loss.
- `apriori` cannot be set from YAML at all.
- 25 of 30 sub-models ignore unknown keys, so a typo under `risk:` or `scan:` vanishes.
- Dead knobs ship in the config:
  - `strategies.strategy_allocations` (0.6/0.4, `config.yaml:299`; `config.py:449`) is read nowhere;
  - so are `session.allow_extended_hours` (`config.py:662`) and `sizing.suggest_tiers_enabled` (525);
  - `portfolio.default_equity_risk_dollars` (391) duplicates `sizing.default_equity_risk_dollars`
    (514), and only the latter is read.
- **The trailing-stop config describes something the code does not do.** `mode: chandelier_atr` is
  never read. The code trails `max(risk, 1.5×risk)` behind the current price, where risk is the
  initial stop distance, not ATR (`copilot.py:2570`).
- `getattr` calls with literal defaults shadow config defaults (`evaluator.py:503-504`;
  `screeners/registry.py:173-177`).
- `StrategyEngine` silently calls `load_config()` when given no config (`strategies.py:75, 246, 364`).

**G9. A trailing policy measured as harmful runs on every position.** The ratchet costs about
0.013R per setup (§4). It lives in the copilot (`copilot.py:2528-2630`), not in `risk/` or
`execution/`, and research approximates it separately (L5).

**G10. Research cadence is effectively zero and lanes are expensive to add.**
- No mining is scheduled (`scripts/launchd.sh:202-204`).
- Each lane adds a bespoke acquisition, a fields module, a protocol and a CLI module
  (`alpha_short_volume.py`, `alpha_insider_filings.py`).
- Frozen fixed sets are not tracked prospectively after their campaign. Only the daily panel and the
  SPY/QQQ workers run forward (`service.py:675-703`).
- `docs/development-notes.md` is dated 2026-09-19. It still lists a Saturday miner (54) and omits the
  suggestion scans, card-stats and corporate-actions workers.

**G11. Operations and governance.**
- No funnel metrics or alerts (§2.5).
- `/scan` counts new cards as `len(get_recent_signals(limit=100))` before and after
  (`copilot.py:4366-4378`). Once 100 rows exist it always answers "No setups", and it reports a gated
  `None` run as "Evaluated all universe contracts".
- The chat tool `trigger_market_scan` ignores its `asset_class` and `strategy` arguments and spends
  the session budget (`copilot_tools.py:61-70`).
- The chat LLM can call `promote_alpha` (`copilot_tools.py:184-192`). It is still gated by
  qualification and CAS (`storage/alpha.py:308-340`). The chat system prompt reserves closes,
  `/panic` and order placement for operator commands (`copilot_graph.py`, `COPILOT_SYSTEM_PROMPT`),
  but promoting an alpha to live card generation is not on that list.
- `MemorySaver` keeps the chat history in memory, unbounded (`copilot_graph.py:83`).
- Single macOS host; `watchdog.log` gets a line every 60 s with no rotation (`launchd.sh:172`).

**G12. Live stage attribution starts after the risk gate.** Only candidates that passed COLLECT are
journaled (`copilot.py:1127`, 1592). Collect-phase rejections and strategy output exist only in logs. Live
evidence therefore cannot separate a signal-stage failure from a risk-stage veto; only replay can.

**G13. Parity gaps that remain.**
- Live reads the forming hourly bar; replay excludes it (`research/setups/replay.py:17-24`).
- Replay targets the configured R, not the regime R (`evaluator.py:180-186`).
- Live sizing is stop-distance; research sizing differs (L5).

## 7. Improvement opportunities

All of these are recommendations; none is implemented today.

1. **Issue cards only from the two suggestion slots** (`service.py:581-626`). Swing and intraday
   scans run with `ScanBudget.NONE`, or with a new journal-only budget, and send nothing. *Effect:*
   the card population becomes the one replay and `card_stats` measure, so the honest base rate
   becomes true. It also removes restart-dependent card spending. This is the cheapest card-quality
   win.
2. **Disable the 15m intraday job** (`scheduler.intraday_scan_enabled: false`) until a 15m registry
   alpha exists. *Effect:* frees the scan lock, cuts API reads and makes PEAD lateness safer.
3. **Build L3 now as a `Selector` protocol with a typed `Selection`.** Inputs: typed signals, the
   book and the budgets. Output: ranked picks, refusal reasons per candidate, and the book after the
   picks. Move ranking off `setup_quality` to a **meta-label probability**, P(target before stop),
   following López de Prado, *Advances in Financial Machine Learning* ch. 3, and Joubert (2022),
   "Meta-Labeling: Theory and Framework". Train it on the replayed native setups (8,768 in the
   bracket counterfactual) with the features the shadow ranker already computes. A/B it in shadow by
   swapping selectors. *Effect:* this is the only lever on G1 that needs no new alpha; even a
   modest AUC beats random at 2 picks per session.
4. **Adopt a typed `Signal`/`Insight` contract and a cross-sectional `SignalSource`.** The model is
   the QuantConnect LEAN Algorithm Framework: Universe → Alpha (`Insight`: direction, period,
   magnitude, confidence, source) → Portfolio Construction → Execution → Risk. That is the five-stage
   lens as open source. Give each source the whole scan cross-section, as the Zipline Pipeline and
   Qlib `DataHandler`/`Strategy` patterns do. Native strategies, PEAD, registry alphas and pooled
   finalists become implementations. Generalise the catalog-probe contract so a frozen formula that
   passes is enrolled in the registry rather than coded per lane. Carry forecasts in return units
   (Grinold–Kahn: alpha = IC·σ·z) so the book sizer can trade expected return against marginal risk.
   *Effect:* time from a passing lane to a capped live probe drops from a new spec plus code to an
   enrolment.
5. **Build L4.** Split into `BracketBuilder` → `Sizer` (book factor included) → `risk.book_gates` →
   `LLMReviewer`. The reviewer only annotates or vetoes through the recorded verdict. Replace
   `LLMTradeEvaluation` with a policy-free `Card` model. *Effect:* no wasted LLM budget, no `ge=2.0`
   trap, and stage-level A/B testing.
6. **Harden config.**
   - Build with one `AppConfig.model_validate(merged_dict)` and set `extra="forbid"` everywhere.
   - Add a test that every top-level YAML key maps to a field and round-trips.
   - Delete the dead knobs or wire them, and rename the trailing keys to match the code.
   - *Effect:* closes the class of bug behind the `corporate_actions` loss.
7. **Make layers a contract.** Move the shared kernels (bracket policy, labels, covariance, DSL
   evaluation, probe constants) to a `kernels` (or `domain`) package. Enforce `research → kernels ←
   live` with import-linter in pre-commit. *Effect:* research iterations cannot silently change live
   behaviour.
8. **Give research a cadence without new search: a forward-tracking worker for every frozen fixed
   set.** It extends the daily-panel worker pattern: score the 64 charged formula–cohort pairs
   nightly on fresh data and journal decile outcomes. Run heavy studies in a separate launchd
   research process with bounded CPU (`docs/architecture-review.md:96`). *Effect:* untouched
   out-of-sample evidence accumulates at zero extra trial cost. A `shorts_retreat`-style
   near-miss gets a genuine prospective confirmation within months, not another window.
9. **Put a predeclared "ratchet off" construction test** (`docs/alpha-roadmap.md:623`) in front of
   the operator. Move trailing into `execution/` behind the L5 kernel shared with replay.
10. **Add observability for the north star.**
    - Per-scan Prometheus counters: candidates, approved, sent, withheld, LLM approve/veto/fallback,
      book-sizing blocks.
    - Readiness or incident rules for LLM fallback rate and for "N suggestion slots with zero cards
      and no reason".
    - Fix `/scan` to render the returned summary (`docs/architecture-review.md:94`).
    - An external dead-man's switch and log rotation (99).
11. **Version the journal payloads and journal the pre-gate candidates** with their rejection rule.
    Use typed pydantic models for `scan_candidates_ranked` and `decision_provenance`. *Effect:*
    `cards outcomes` can attribute signal versus risk live (G12).

## 8. Open questions

- How many cards since PR A.1 (2026-10-09) came from each `trigger` (suggestion, swing, operator)?
  The journal has it; that would size G2.
- Does the production registry hold any active or probe **15m** alpha? This confirms or refutes G3.
- Does the production signal scope already exceed 100 rows? If so, the `/scan` reply bug is live
  today.
- What do `cards outcomes` report for `llm_gate` (vetoed versus approved mean R, bracket-edit deltas)?
  No figures appear in the docs, so the LLM gate's value is unmeasured in writing.
- How often does the LLM fallback fire? Only logs record it.
- Is the swing scan meant to issue cards, or is it a historical artefact of the pre-suggestion-scan
  design?
- How often does `book_sizing: enforce` give a factor below 1? If almost never, it is a no-op in
  practice.
- How much CPU do the in-daemon research workers (daily panel, session decisions) take during
  suggestion scans? Scan durations are metered, but contention is not.
