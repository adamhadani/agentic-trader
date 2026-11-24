# Raw lens findings (91)

The seven expert lenses' original findings, before consolidation into the canonical register.
Kept for provenance; the canonical register supersedes them.

# Lens: architecture (15 findings)

## There is no typed Forecast contract, so mining output cannot reach the signal, portfolio or execution stages

_area: architecture · kind: gap · impact: high · effort: L · confidence: high_

**Claim**

Today the only signal contract is ScreenerCandidate, and it describes indicators: EMA, RSI, ATR, swing levels and a heuristic setup_quality. strategy and direction are bare strings and asset_class defaults to FUTURES. It carries no expected return, horizon, probability, uncertainty, data contract or model identity.

The only typed route from research to live is the registry, and it accepts only:
- single-symbol definitions;
- raw-adjustment deployment feeds;
- 30-bar time-series z-thresholding;
- per-symbol daily probes.

The pooled lane, where all current mining happens, produces dimensionless cross-sectional decile scores on adjusted SIP bars read at D-1. A pass there only makes an unwritten 'Part 2 probe spec' eligible. Cross-sectional operators exist in research/alpha/panel.py, but outside the DSL grammar and with no live consumer.

Two consequences follow. Selection, sizing and the shadow allocator have no μ input, and every new signal family is hand-wired into run_scan: copilot.py has about 150 PEAD/drift lines.

**Evidence**

- agentic_trader/screeners/base.py:23-47 (ScreenerCandidate fields; strategy/direction: str; asset_class default FUTURES)
- agentic_trader/research/alpha/promotion.py:88 (eligible_symbols must equal the run symbol)
- agentic_trader/research/alpha/promotion.py:474-480 (deployment contract requires adjustment == raw)
- agentic_trader/research/alpha/strategy.py:144-161 (30-bar rolling z, threshold entry)
- agentic_trader/storage/alpha.py:427-432 (probe enrolment: unclocked 1d, explicit symbol universe)
- agentic_trader/research/pooled/formula.py:1-7,100-132,195-215 (adjusted bars at D-1, top-fraction basket)
- agentic_trader/research/alpha/panel.py:19-37,132 (cross_sectional_rank/zscore/group_neutralize/beta_adjust_scores outside the DSL)
- agentic_trader/research/alpha/dsl.py:1-5 ('Cross-sectional rank/scale ... deliberately absent')
- docs/alpha-pooled-mining.md:1014-1017 ('Decision rule': a pass leads to a Part 2 probe spec) and 1362-1366 ('What it never does')
- agentic_trader/agent/copilot.py:897-948,1268-1280 (PEAD special cases; grep counts 150 PEAD/drift lines)

**Recommendation**

Define one frozen pydantic Forecast with these fields:
- source_id, the content hash of a deployable artifact;
- symbol, decided_at and horizon_sessions;
- direction, score and cross-sectional rank;
- expected_r and its standard error, in the label's R units, plus a cost-model id;
- p_target;
- execution_policy_id;
- DataContract {feed, adjustment, bar_builder_version, clock};
- a provenance digest.

Add a SignalSource protocol, produce(panel: ScanPanel, as_of) -> Sequence[Forecast], that receives the whole scan cross-section rather than one contract.

Port in this order:
1. Native strategies, wrapping StrategyEngine. Take expected_r from the replay prior, which is honestly about zero.
2. PEAD. Its live source already calls the study's own build_events (research/apriori/pead_live.py:1,159), which makes it the parity-by-construction template.
3. A new content-addressed RankerDefinition: a DSL score, plus a cross-sectional selection rule, plus the label bracket. Enrol it in the existing registry 'probe' list under probe_block_reason, the risk cap and the −4R kill.

Then write the pooled 'Part 2' once, generically: a confirmed pooled formula becomes a RankerDefinition enrolment. Model the design on the QuantConnect LEAN Insight (direction, period, magnitude, confidence, source model), which is handed to a separate portfolio-construction model.

**North-star impact**

A research pass becomes a capped paper-probe card through enrolment, not through a new spec plus code each time. The selector and the allocator get μ and σ(μ) to act on, so 'allocation based on existing positions' consumes forecasts instead of sorting a heuristic.

**References**

- QuantConnect LEAN Algorithm Framework, Alpha key concepts (Insight: Symbol, Period, Direction, Magnitude, Confidence, SourceModel, Weight; insights feed the Portfolio Construction model) — An open-source precedent for a typed signal object between the alpha and portfolio stages, matching the operator's five-stage lens. (https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/alpha/key-concepts)
- Grinold & Kahn, Active Portfolio Management (2nd ed., 2000): alpha = IC × volatility × score — A standard mapping from a cross-sectional score to an expected return in return units, which is what Forecast.expected_r needs.

---

## Portfolio construction has no stage: the session's two cards come from an inline sort on a key measured as uninformative

_area: sizing_portfolio · kind: gap · impact: high · effort: M · confidence: high_

**Claim**

Card selection today happens inside TradingCopilot.run_scan, a method of about 1,000 lines in a 4,527-line class. It is a sort on -setup_quality over a list[tuple[Any, Any, Any]], followed by inline budget and group-cap closures and PEAD carve-outs.

setup_quality carries no information: holdout ρ is −0.012, and the frozen ridge ranker did worse. Native longs trail SPY by 0.10–0.15R in every bracket arm.

The L3 card-selector seam is still 'Planned'. The agreed card definition (EV ≥ 0 after cost) has no enforcement path: card_policy ships off, and when it is on it gates on raw mean_r_cost with no market control, which rewards beta.

**Evidence**

- agentic_trader/agent/copilot.py:1174-1177 (sort key -setup_quality)
- agentic_trader/agent/copilot.py:869 (approved_candidates: list[tuple[Any, Any, Any]])
- agentic_trader/agent/copilot.py:1268-1300 (inline budgets, card_groups/capped_groups closures, PEAD exclusions)
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)' and 'Hypotheses'
- docs/bracket-counterfactual-2026-10-18.md 'What this says' §3
- docs/alpha-roadmap.md:494 (L3 Planned) and 507-514 (reasonable-suggestion definition)
- config/config.yaml:454 (card_policy mode off)
- agentic_trader/execution/card_policy.py:44-54 (withholds on mean_r_cost)
- agentic_trader/research/setups/card_stats.py:43-62 (CardKeyStats: no excess-return field)

**Recommendation**

Build L3 as an injected CardSelector protocol.
- Input: Sequence[Forecast], one typed BookSnapshot and the budgets derived from signals rows.
- Output: a typed Selection holding the ordered picks, a typed refusal code per candidate (reusing risk.RiskRule/Rejection) and the book after the picks.

Make the budget pools (native, catalog/probe, group) config data keyed by source family, so the PEAD branches leave run_scan.

Run challenger selectors in shadow and journal each one's picks per scan. Every ranked candidate is already labelled by cards outcomes, so counterfactual selection is scored at no cost.

Set the objective to benchmark-relative R, a β-adjusted excess over SPY that outcomes.py can already compute from market_r. First challengers:
- (a) a meta-label P(target before stop) trained on the 8,768 replayed setups with setup_features_v2;
- (b) a −Δbook-vol tie-break among statistically tied candidates;
- (c) random, as the control.

Fold card_policy into the selector as its withholding rule, on excess R shrunk toward the replay prior (−0.11R).

**North-star impact**

This changes which one or two cards go out each session. Selection becomes an A/B experiment set in config rather than a code change, and card quality gets a measurable target (excess R) instead of raw R that pays for market beta.

**References**

- López de Prado, Advances in Financial Machine Learning (2018), ch. 3 'Meta-Labeling' — A secondary classifier sizes or filters a primary signal. That is exactly the selector role over native setups, and needs no new alpha.
- Joubert, 'Meta-Labeling: Theory and Framework', Journal of Financial Data Science (2022) — Evidence that meta-labels improve precision and position sizing on top of weak primary models.

---

## Live/research parity is asserted rather than typed: evidence describes different bars and a different trade from the card

_area: architecture · kind: defect · impact: high · effort: M · confidence: high_

**Claim**

One strategy name runs under four different input and execution contracts today.

**Bars and data:**
- Live scans use IEX: load_config defaults the feed to IEX. Indicators run on raw bars, and the 4h bar is a UTC df.resample('4h') over hourly bars that include extended-hours buckets. That bucketing shifts at the 2026-11-01 DST change.
- Replay base rates and the bracket counterfactual use SIP with adjustment 'all'.
- Pooled scores use SIP adjusted daily bars at D-1, yet the formula docstring says 'the live scan sees the same bars'.
- card_stats and card_policy label on the configured feed, which is IEX.

**Execution:**
- Every label enters at the first regular hourly open after the decision, so it always fills, and it has a fixed timeout.
- Native cards submit a GTC limit at the scan price with no time-in-force set and no lifetime.
- Native cards trail on fetch_latest_price at current − max(1, 1.5)×initial R. The config field 'mode: chandelier_atr' that should describe this is never read.
- That trail measured −0.013R (ci90 [−0.020, −0.006]) against the fixed bracket.
- GTC limits were measured to fill mainly when the forecast is wrong.

replay.py acknowledges only the forming bar as a parity gap.

**Evidence**

- agentic_trader/config.py:1043-1050 (alpaca_data_feed defaults to 'iex')
- agentic_trader/data/market_data.py:119-138 (resample_to_4h on a UTC index)
- agentic_trader/research/setups/baserates.py:57-58 (feed alpaca:sip, adjustment all)
- agentic_trader/research/pooled/formula.py:2-4 (docstring parity claim)
- agentic_trader/research/setups/sources.py:26-34 (label source: configured feed, raw)
- agentic_trader/research/setups/labels.py:176-189 (entry at first regular bar open at/after decision)
- agentic_trader/agent/copilot.py:2967-2978 (OrderRequest with no time_in_force)
- agentic_trader/execution/lifetimes.py:209-213 (only signals whose alpha_policy has a lifetime are timed)
- agentic_trader/agent/copilot.py:2553-2571 (fetch_latest_price; trail = current − max(risk, 1.5×risk))
- config/config.yaml:419-425 (trailing enabled, mode chandelier_atr)
- docs/bracket-counterfactual-2026-10-18.md 'Rules' (live ratchet −0.013R)
- docs/alpha-roadmap.md:148-152 (GTC fills when the forecast is wrong)
- docs/alpha-lifetime-attribution-2026-09-21.md 'Result and decision'
- agentic_trader/research/setups/replay.py:1-24

**Recommendation**

Make DataContract (feed, adjustment, bar-builder version, clock) and ExecutionPolicy (entry type, time-in-force, resting lifetime, exit arm, timeout, cost model) frozen, hashed values. Every label, every Forecast and every signal row should carry both.

The card's measured-record line, and card_policy, should refuse to speak for a card whose contracts differ from the evidence's.

Then converge, in this order:
1. Use one session-anchored RTH bar builder for live native scans, replay and labels. market/bars.py:276-346 already exists. Run it on delayed SIP, as the PEAD path already does in production. Ship it as a new frozen replay before the DST shift.
2. Give native cards an explicit ExecutionPolicy: an explicit time-in-force, a one-session resting entry (daily_entry_lifetime, execution/lifetime_policy.py:24,65-71) and the label's timeout through SessionLifetimePolicy.
3. Move trailing out of TradingCopilot into execution/, behind the label kernel's BracketArm/TrailSpec. Live and research arms then match by construction, and the counterfactual's fixed bracket becomes an arm-id change made as a declared construction test.

Add a parity regression test that replays a sample of journaled live scans through the replay path and asserts that the candidate sets are equal under the same DataContract.

**North-star impact**

The 'honest base rate' on every card and the only outcome-derived gate become statements about the trade the operator actually takes. It also removes known artefacts: the UTC and extended-hours 4h bar, the DST shift in the middle of the vol_20/sector_rel_mom_60 window, adverse GTC fills, and a measured-harmful trail.

**References**

- NautilusTrader concepts overview: 'The same strategy and execution-algorithm code can run across backtest and live systems, reducing deployment divergence.' — A design to borrow, not a framework to adopt: one execution and label kernel shared by research and live. (https://nautilustrader.io/docs/latest/concepts/overview)
- Handa & Schwartz, 'Limit Order Trading', Journal of Finance 51(5), 1996 — Shows that passive limit fills are adversely selected, which explains why labels that always fill overstate GTC-limit cards.

---

## Scheduling fuses evidence collection with card issuance: restart-timed swing scans spend the card budget

_area: operations · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

Five producers spend one session card budget first-come: the suggestion cron, the swing interval scan, the 15m intraday scan, operator /scan and the chat tool.

The swing scan is registered with next_run_time=datetime.now(UTC) and ScanBudget.FULL, so every daemon start can issue cards. The intraday job runs at FULL every 15 minutes, but native strategies emit only 4h and 1h setups and that scan keeps only 15m candidates.

In production:
- 14 of 37 native cards went out off-slot, between 09:37 and 13:43 ET.
- There were 61 daemon starts in 33 days.
- In 7 of 13 journaled 14:35 scans, every candidate was refused 'per-session budget spent' after a swing card had taken the slot.

Replay and card_stats model only the 10:35 and 14:35 instants. Swing scans also lack dynamic names, news and shadow evidence. The card population is therefore not the one the evidence measures.

**Evidence**

- agentic_trader/cli/commands/service.py:581-593 (swing: interval, FULL budget, next_run_time=now)
- agentic_trader/cli/commands/service.py:596-626 (intraday 15m, FULL budget)
- agentic_trader/screeners/strategies.py:343-348 (native emits only 4h and 1h)
- agentic_trader/agent/copilot.py:1093-1095 (timeframe filter)
- agentic_trader/research/setups/replay.py:3-6
- docs/card-evidence.md:376-380 ('Limits': swing and operator cards and journal coverage)
- query: production scan_candidates_ranked and signals by NY slot, trigger and outcome (maps/live.md Q3, Q5, Q7, Q16b)

**Recommendation**

Add an explicit ScanPurpose {CARD_SLOT, EVIDENCE} to the scheduler contract.
- Only the two suggestion slots are CARD_SLOT.
- Swing, intraday and the chat tool run with ScanBudget.NONE as journal-only evidence.
- Operator /scan previews by default.
- Remove run-at-start for any job that can send cards.
- Disable the 15m job until a 15m Forecast producer exists.

This is a small change in service.py and the scan entry points.

**North-star impact**

Cards come only from the designed full-context slots, and the card population becomes the one replay and card_stats measure. 14:35 supply recovers, and deploys and restarts stop sending cards.

**References**

_none_

---

## Stop adding single-source pooled lanes on the current plumbing until a deployment unit and a forward scorer exist

_area: alpha_mining · kind: stop_doing · impact: high · effort: S · confidence: medium_

**Claim**

Five of the ten PRs since the 2026-10-14 north-star restatement (#120, #121, #122, #126, #128) were pooled mining lanes. Each added a protocol subclass, an Inputs/Sources/verify trio, isinstance branches and a CLI module. None of them can change a card: a pass only makes an unbuilt Part 2 spec eligible.

The lane's detection floor, about 0.065–0.10R of paired edge, sits above the edges its own design doc expected: 'expected to produce few discoveries'. The record so far is 64 formula–cohort pairs and no finalist. One lane-wide historical confirmation window remains, and it is single-use.

**Evidence**

- docs/alpha-roadmap.md:597-627 (status lines for #120-#128)
- agentic_trader/cli/commands/alpha_short_volume.py and alpha_insider_filings.py (one CLI module per lane)
- agentic_trader/research/pooled/campaign.py:403-584 (one subclass per lane)
- docs/alpha-pooled-mining.md:1014-1017 ('Decision rule': a pass leads only to a Part 2 spec)
- docs/alpha-pooled-mining-research-2026-09-28.md 'A pooled lane sized to detect a 0.09R paired edge'
- docs/alpha-pooled-short-v1-2026-10-20.md (64 pairs, no finalist)
- docs/alpha-pooled-mining.md:615-622 (lane-wide, single-use confirmation window)

**Recommendation**

Run the already-built insider campaign if the operator approves it. Then freeze any new single-source fixed-set lane until three things exist:
- (a) FieldSource plugins, so a new source is one module and one JSON file;
- (b) the RankerDefinition deployment unit, so a pass is an enrolment;
- (c) the forward scorer, so frozen sets accrue out-of-sample evidence.

Redirect that capacity to the selector and parity work. If the last historical window is to be spent, spend it on one predeclared composite of literature-signed signals rather than on an eleventh source.

**North-star impact**

Stops spending most engineering time on research that structurally cannot change a card, and makes the next data source cheaper to test and deployable when it passes.

**References**

- McLean & Pontiff, 'Does Academic Research Destroy Stock Return Predictability?', Journal of Finance 71(1), 2016 — Post-publication decay pushes plausible single-anomaly edges below the lane's detection floor.
- Jensen, Kelly & Pedersen, 'Is There a Replication Crisis in Finance?', Journal of Finance 78(5), 2023 — Supports combining many weak, theme-grouped signals over testing standalone single-source formulas.

---

## Each new data source needs about ten touch points, and the design forbids combining sources

_area: alpha_mining · kind: improvement · impact: high · effort: M · confidence: high_

**Claim**

Today each pooled field source adds the following:
- a CampaignProtocol subclass with a duplicated 'fixed_set in v1' validator;
- an Inputs model that repeats CorporateActionInputs;
- a Sources/load/verify trio in the runner;
- a branch in score_book's isinstance ladder (8 isinstance sites in scoring.py);
- a branch in frozen_document's ladder;
- a row and column in an N×N CLI mutual-exclusion matrix;
- its fields hard-coded into the DSL's global FIELD_UNITS, with no visibility metadata.

build_cube_inputs refuses more than one source per build, so interaction formulas such as earnings reaction × short flow, or insider buying × reversal, cannot be written. The parsers exist only behind research CLIs, so a finalist from FINRA or EDGAR fields would have no live D-1 value.

**Evidence**

- agentic_trader/research/pooled/campaign.py:403-418,451-465,494-508,555-568 (four subclasses, duplicated validators)
- agentic_trader/research/pooled/campaign.py:434-449,486-492,547-553 (repeated inputs models)
- agentic_trader/research/pooled/runner.py:90-195 (EventSources/ShortSources/InsiderSources trios)
- agentic_trader/research/pooled/runner.py:317-318 ('a build carries one field source, not several')
- agentic_trader/research/pooled/scoring.py:324-377 (isinstance dispatch)
- agentic_trader/research/pooled/campaign_run.py:239-256 (frozen_document ladder)
- agentic_trader/cli/commands/alpha.py:1668-1714 (CLI exclusion matrix)
- agentic_trader/research/alpha/dsl.py:28-47 (per-lane fields in a global dict)

**Recommendation**

Introduce one FieldSource protocol and registry. Each source declares:
- an id and version;
- field specs with name, units and visibility rule (for example 'EDGAR filing date' or 'D+1');
- a pinned-inputs pydantic model;
- load(), verify_digests(), coverage() and frames(symbols, sessions).

CampaignProtocol then carries field_sources: tuple[FieldSourceRef, ...]. ScoreBook.with_fields joins the union of frames. The DSL field table is derived from the protocol's sources, so a field the protocol does not declare fails to compile.

Keep frozen identities byte-hashed. Parse the old protocol shapes in one loader and update callers in the same change, with no runtime aliases.

Reuse the same plugin in a daily daemon ingestion worker. Its parity test is that the live D-1 value equals the cube value, so any lane that yields a finalist already has a live feed.

**North-star impact**

A new data idea costs one module and one frozen JSON instead of a workstream. Multi-source hypotheses, where the literature puts more of the signal, become expressible, and event-lane passes become deployable.

**References**

- Qlib point-in-time database: every published value stored with its publication date, so backtests see only what was known — A template for the visibility metadata each FieldSource field should declare. (https://qlib.readthedocs.io/en/latest/advanced/PIT.html)
- Microsoft Qlib (Alpha158/Alpha360 handlers in a modular, loosely coupled data layer) — A precedent for pluggable feature handlers feeding one cross-sectional pipeline. (https://github.com/microsoft/qlib)

---

## Mining has no cadence: frozen formulas die after one campaign and nothing accrues forward evidence

_area: alpha_mining · kind: research_direction · impact: high · effort: M · confidence: medium_

**Claim**

The weekly miner is retired. In the daemon only the SPY/QQQ session workers and the daily panel run forward, and no worker re-scores frozen fixed-set formulas once their one-shot campaign is over. The 2024-01-02 → 2026-07-31 confirmation window is lane-wide and single-use. Every new cohort means a fresh per-lane cube download; the 1,000-name cube took about a day.

**Evidence**

- scripts/launchd.sh:21 (miner retired)
- agentic_trader/cli/commands/service.py:675-701 (only session and daily-panel workers run forward)
- docs/alpha-pooled-mining.md:615-622 (lane-wide single-use confirmation)
- docs/alpha-pooled-cohort3-2026-10-16.md 'Building the cube' and 'Operational notes'

**Recommendation**

Build a generic ForwardScorer daemon worker on the run_daily_panel_worker pattern, over a registry of frozen RankerDefinitions and fixed-set formulas. At each decision session it should:
1. Read D-1 fields from the shared store.
2. Journal the picks and the every-eligible control before any outcome exists.
3. Label at maturity with the shared label kernel.
4. Maintain an always-valid confidence sequence on the paired edge, per formula and for one predeclared composite.

A pre-registered sequential rule moves a formula to probe enrolment, in Forecast form. These formulas were already charged, and prospective sessions are fresh data, so this neither inflates the trial family nor consumes the historical window.

**North-star impact**

Gives the operator the 'reasonable cadence' they asked for: about 72 already-frozen formulas and every future lane accrue out-of-sample evidence daily. A near-miss such as shorts_retreat gets genuine confirmation, and a confirmation arrives ready to card.

**References**

- Howard, Ramdas, McAuliffe & Sekhon, 'Time-uniform, nonparametric, nonasymptotic confidence sequences', Annals of Statistics 49(2), 2021 — Allows daily peeking at forward evidence without inflating error, which a continuous scorer needs. (https://arxiv.org/abs/1810.08240)

---

## The evaluator fuses bracket, sizing, gates, I/O and the LLM, approves on any LLM error, and bakes policy into the card DTO

_area: signals · kind: defect · impact: medium · effort: M · confidence: high_

**Claim**

RiskEvaluator.evaluate_candidate does all of the following in one method: builds the bracket, sizes the position, runs the book gates, makes an I/O correlation check, applies the macro, earnings, corporate-action, regime and session rules, and calls the LLM.

It runs twice per winner: deterministically in _admit_candidate, then again with the LLM in the send loop. Book sizing runs after the LLM, so LLM spend goes to cards that sizing then blocks.

litellm.acompletion has no deadline. Any exception returns approved=True and writes the raw exception text into the card's thesis.

LLMTradeEvaluation, the de facto card object in six modules including the outbox, hard-codes risk_reward_ratio: Field(ge=2.0). A predeclared sub-2R bracket would therefore raise during construction and again at delivery.

Live, 14 of 14 LLM evaluations approved and there were 0 llm_vetoed events. L4 is still Planned.

**Evidence**

- agentic_trader/agent/evaluator.py:362-917
- agentic_trader/agent/evaluator.py:751-760 (acompletion, no timeout)
- agentic_trader/agent/evaluator.py:886-917 (approve-on-exception fallback; exception text in thesis_summary)
- agentic_trader/agent/evaluator.py:106 (risk_reward_ratio ge=2.0)
- agentic_trader/agent/copilot.py:1741-1760 (deterministic collect pass)
- agentic_trader/agent/copilot.py:1364-1427 (send loop: LLM evaluate, then book_sizer.decide)
- notifier/outbox.py, telegram_bot.py, book_sizing.py, position_sizing.py import LLMTradeEvaluation (grep)
- docs/alpha-roadmap.md:495 (L4 Planned)
- query: LLM outcomes in scan_candidates_ranked (maps/live.md Q7)

**Recommendation**

Build L4 as BracketBuilder → Sizer (including the book factor) → risk.book_gates → Reviewer, with typed results between them.

Replace LLMTradeEvaluation with a policy-free Card DTO whose reward/risk is validated against RiskLimits rather than a literal.

Run the Reviewer last, under asyncio.wait_for. Have it return a typed ReviewOutcome {approved, vetoed, commentary, error}. An error should fail closed for native cards and increment a metric; alternatively make native review commentary-only, as catalog probes already are (evaluator.py:858-870). Record the LLM-edited bracket as its own arm in cards outcomes.

**North-star impact**

No silently approved cards during LLM outages. LLM latency and cost are spent only on sendable cards, and bracket-construction experiments are no longer blocked by a DTO literal.

**References**

_none_

---

## There is no single book view, so the allocation stage cannot see beta, sector or marked exposure

_area: sizing_portfolio · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

Today there are three different book representations:
- risk.Book, built from signals rows;
- BookSizer weights, equity-only and valued at recorded entry notional rather than marks;
- admission's broker-marked book.

None carries beta or sector factors, and the BookSizer covariance uses raw bars, which splits distort. The L3 spec's beta and sector contribution bounds were never built.

book_vol is in enforce, yet all six preview factors were 1.00. The 'shadow optimiser' pins lower bound = upper bound = current weights, so it only recomputes w'Σw and accumulates no allocation evidence.

**Evidence**

- agentic_trader/risk/book.py:33-104
- agentic_trader/execution/book_sizing.py:126-141 (_book_weights from notional_value, equity only)
- agentic_trader/execution/capacity.py:194-209 (broker-marked revaluation)
- agentic_trader/execution/book_sizing.py:222 and agentic_trader/research/setups/covariance.py:20-26 (raw bars)
- agentic_trader/execution/book_sizing.py:434-445 (pinned optimiser bounds)
- docs/alpha-roadmap.md:521-524 (marginal variance, beta and sector contribution specified)
- config/config.yaml:466 and docs/card-evidence.md 'Book-aware sizing' (6 factors, all 1.00)

**Recommendation**

Build one BookSnapshot provider for the selector, sizer, book_gates and admission. It should combine:
- broker-marked positions, reservations and this scan's cards;
- a split-adjusted Ledoit-Wolf covariance that includes SPY and the sector ETFs;
- per-name β and risk group, with sectors from EDGAR SIC so dynamic names get groups too.

Journal β-dollars and marginal risk contribution per card, and add a net-beta rule to risk/ in preview mode.

Replace the pinned D4 solve with a free shadow solve over the approved candidates, using Forecast μ and σ(μ). Today that solve should honestly come out at about zero, and it builds the prospective evidence the E2E 'allocator-sized signals' stage needs.

**North-star impact**

Makes 'allocation decisions based on existing positions' real and consistent from scan to admission. Cards show their beta, which the bracket study identified as the source of the native longs' raw R.

**References**

- Maillard, Roncalli & Teïletche, 'The Properties of Equally Weighted Risk Contribution Portfolios', Journal of Portfolio Management 36(4), 2010 — Gives marginal risk contribution as an order-independent per-card budget, in place of greedy first-card-wins sizing.

---

## There is no shared point-in-time bar and field store: every lane and every scan refetches

_area: data · kind: gap · impact: medium · effort: L · confidence: medium_

**Claim**

Today each source fetches on its own:
- Research lanes fetch one symbol at a time into per-lane caches.
- Live scans refetch 1y daily, 60d hourly and optionally 10d 15m bars per symbol on every scan, and the TTL cache is dead code.
- Book sizing does its own batch fetch.

The 1,000-name cube took about a day. Cohort membership is current-only.

**Evidence**

- agentic_trader/research/setups/runner.py:158-189 (per-symbol cached fetch)
- agentic_trader/data/market_data.py:66-72,197-239 (unused cache_ttl; per-scan refetch)
- agentic_trader/execution/book_sizing.py:215-237
- docs/alpha-pooled-cohort3-2026-10-16.md 'Building the cube'
- docs/survivorship-spike-2026-10-20.md (membership history not available from current listings)

**Recommendation**

Put one BarStore/FieldStore behind the existing BarSource protocol, keyed by DataContract. Append to it nightly, outside scan windows, with a digest per day, and include asset-master snapshots so point-in-time membership starts accruing, plus the FieldSource daily files.

Live scans read completed bars from the store and fetch only the forming window. Protocols pin a slice digest instead of re-downloading.

**North-star impact**

This is the physical enabler of parity, the forward scorer and universe growth beyond 159+20 names at the same scan cadence. Cube builds drop from about a day to minutes.

**References**

- Qlib PIT data design — A publication-date-keyed storage layout that keeps online trading and historical backtests consistent. (https://qlib.readthedocs.io/en/latest/advanced/PIT.html)

---

## Research and live import each other with no enforced boundary; shared kernels live under research/

_area: architecture · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

Imports run both ways today:
- 17 live modules make 35 imports from agentic_trader.research. Examples: evaluator.py:35 (bracket_prices), storage/workflow.py:40-41 (ValidationPolicy, catalog ids), config.py:59, book_sizing.py:27-28 (optimizer, covariance), screeners/formulaic.py:9-11 (DSL, clock), copilot.py:103-116.
- research/ makes 68 imports into storage, execution, screeners, agent, broker and notifier.

No import contract is configured. The kernels both sides depend on live under research/: bracket policy, label_bracket, trailing_price, the DSL, covariance and the card_stats schema. Private bootstrap helpers are also imported across packages.

**Evidence**

- grep at db82817: 17 non-CLI live modules, 35 'from agentic_trader.research' lines; 68 research→live import lines
- agentic_trader/agent/evaluator.py:35
- agentic_trader/storage/workflow.py:40-41
- agentic_trader/config.py:59
- agentic_trader/execution/book_sizing.py:27-28
- agentic_trader/screeners/formulaic.py:9-11
- agentic_trader/agent/copilot.py:103-116

**Recommendation**

Create agentic_trader/kernels as a pure package holding:
- the bracket, label and trail kernel;
- the DSL and the session bar builder;
- covariance and the public stats helpers;
- the Forecast, DataContract and ExecutionPolicy types.

Enforce the boundary with import-linter contracts in pre-commit:
- Layers: live (agent, execution, broker, notifier) may import kernels.
- Research may import only kernels and storage.
- Forbidden: research must not import broker, notifier or execution.

Any kernel change must pass the parity regression test.

**North-star impact**

Research can iterate quickly without silently changing live card construction, and any kernel change is mirrored in replay by construction.

**References**

- import-linter (layers, forbidden, independence and protected contracts) — A low-cost, CI-enforceable boundary between research, kernels and live. (https://import-linter.readthedocs.io/en/stable/)

---

## The evidence substrate is untyped and unversioned, and the funnel's denominators are not journaled

_area: architecture · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

Today decision_provenance is a dict of about 20 keys, and the scan_candidates_ranked payload is untyped. WorkflowStore.append stamps schema_version=1 on every event, even though payloads gained llm, news, card_policy, book_sizing, regime and trigger blocks between 2026-10-05 and 10-14.

The ranked journal is written only when native_count > 0, and collect-phase risk rejections only go to the log. In production, 12 of 39 scheduled 10:35 slots have no journal event.

**Evidence**

- agentic_trader/agent/copilot.py:1462-1491 (decision_provenance dict)
- agentic_trader/agent/copilot.py:1851-1931 (ranked journal writer)
- agentic_trader/storage/workflow.py:155 (schema_version=1)
- agentic_trader/agent/copilot.py:1591 (journal only if native_count)
- agentic_trader/agent/copilot.py:1753-1760 (collect-phase rejection logged only)
- query: scan_candidates_ranked per scheduled slot (maps/live.md Q5)

**Recommendation**

Model each event kind as a pydantic discriminated union with a schema_version that is bumped whenever its payload changes. Validate at write time, and have readers dispatch on (kind, version).

Journal every scheduled scan, including empty ones, and every collect-phase rejection with its RiskRule code; risk.Rejection is already typed.

Add Prometheus funnel counters: raw setups, admitted, refused per rule, LLM outcome, withheld and sent.

**North-star impact**

Makes candidate supply and the cost of each gate measurable, which is needed before widening the universe or the DSL. Selectors and cards outcomes then read typed evidence that cannot drift silently.

**References**

- Pydantic discriminated unions — A standard way to version and validate heterogeneous journal payloads by kind. (https://pydantic.dev/docs/validation/latest/concepts/unions/)

---

## Config loading is hand-wired, drops blocks silently and ships dead knobs that misdescribe behaviour

_area: architecture · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

load_config hand-wires about 30 blocks and omits session= and apriori=, although AppConfig declares both. config/config.yaml ships a session: block that is therefore silently dropped. This is harmless today only because its values equal the defaults.

Only 5 sub-models forbid unknown keys, so a typo elsewhere vanishes. trailing_stop.mode: chandelier_atr is never read.

The same class of bug silently ignored corporate_actions, including entry_block: enforce, until 2026-10-13.

**Evidence**

- agentic_trader/config.py:1109-1179 (AppConfig(...) without session= or apriori=)
- agentic_trader/config.py:842,863 (apriori and session fields)
- config/config.yaml:414-417 (session block)
- agentic_trader/config.py:232,276,309,357,779 (the only extra=forbid models)
- config/config.yaml:421 vs agentic_trader/agent/copilot.py:2553-2571 (mode unused)
- docs/alpha-roadmap.md:591 (loader fix for corporate_actions/news, 2026-10-13)

**Recommendation**

Build AppConfig with a single model_validate over the merged YAML and environment dict, and set extra='forbid' on every sub-model.

Add a test that every top-level YAML key maps to a field and round-trips.

Delete or wire the dead knobs: trailing_stop.mode, strategies.strategy_allocations and the unused indicator parameters.

Move experiment policy into validated config: budgets per source family, the selector id and the execution-policy id. Experiments then become reviewed config changes.

**North-star impact**

Prevents an operator decision such as an enforce flip from silently not taking effect, and makes stage experiments configuration rather than code.

**References**

_none_

---

## A single unmodelled ledger event or partial fill blocks every card and entry

_area: execution · kind: defect · impact: medium · effort: M · confidence: medium_

**Claim**

The ledger recognises only FILL, CSD/CSW/JNLC, FEE/CFEE/DIVFEE and DIV/INT. Any other activity type becomes an issue, which makes the report not ready, which makes current_risk raise. Every scan candidate is then dropped as 'account risk unavailable' and every entry is refused. Because the full-history import re-reads the activity on each refresh, recovery requires a code change.

Separately, a partial fill, an untracked position or an unrelated order blocks entries in all symbols. The drawdown gate is coupled to complete realized-P&L classification. Whether such activity types occur in the paper account is unverified.

**Evidence**

- agentic_trader/accounting/ledger.py:18-23,134-140
- agentic_trader/accounting/risk.py:88-95
- agentic_trader/agent/copilot.py:1724-1740
- agentic_trader/execution/capacity.py:103-112,213-216

**Recommendation**

Separate equity/drawdown readiness, which needs only equity and external flows, from realized-P&L readiness, which needs every activity classified. Treat an unknown cash-bearing activity conservatively, as an external inflow that overstates drawdown, rather than blocking.

Scope partial-fill and untracked-position blocks to the affected symbol, with worst-case risk reserved. Keep fail-closed behaviour inside that scope.

**North-star impact**

Keeps the card stream alive through routine corporate actions and partial fills without weakening protection.

**References**

_none_

---

## Stop carrying dormant one-shot harnesses and contract-violating legacy components on main

_area: architecture · kind: stop_doing · impact: medium · effort: M · confidence: medium_

**Claim**

research/ is about 32k LOC. research/alpha has 66 modules, most of them one-shot frozen harnesses: study, power_*, lifetime_*, persistent_*, volume*, factor_*, forecast_controls*, retained_*, panel_forecast*. cli/commands/alpha.py is 2,208 lines with 38 commands.

Alongside the shared research/setups/labels.py kernel there are two more bracket engines, research/alpha/simulation.py and backtest/engine.py, and the live trail lives in TradingCopilot.

Legacy components contradict the execution contracts:
- RedundantBroker fails a POST over to another broker on 'timeout' text.
- SlicedExecutionEngine infers a fill from the limit price.
- PaperBroker fills at the latest price and ignores the limit.

The tag pattern for retiring harnesses already exists, for example research/power-diagnosis-v1-20260918.

**Evidence**

- find agentic_trader/research -name '*.py' | xargs wc -l (32,349 total); ls research/alpha (66 entries)
- agentic_trader/cli/commands/alpha.py (2,208 lines, 38 @command)
- agentic_trader/research/alpha/simulation.py:174; agentic_trader/research/setups/labels.py:159; agentic_trader/backtest/engine.py
- agentic_trader/broker/redundant.py:121-161
- agentic_trader/execution/engine.py:126-129
- agentic_trader/broker/paper.py:62-65
- git tag -l: research/power-diagnosis-v1-20260918

**Recommendation**

Tag each completed frozen harness and delete it from main together with its CLI command, keeping the results docs and journal artifacts. Delete or quarantine RedundantBroker and the slicer. Make PaperBroker use the label kernel's fill model. Re-base or retire simulation.py and backtest/engine.py onto the single label kernel (roadmap L5).

**North-star impact**

Lowers the cost of changing the shared kernels (DSL, labels, journal) that the Forecast, selector and parity work must touch, and removes paths that could break the no-replay and no-inferred-fill guarantees.

**References**

_none_


# Lens: ml (11 findings)

## Mining tests one standalone formula at a time and has no stage that combines signals, which is the wrong unit for alpha in liquid US equities

_area: alpha_mining · kind: gap · impact: high · effort: L · confidence: medium_

**Claim**

NOW: The pooled lane charges and judges each dimensionless DSL formula on its own (`Formula`, research/pooled/formula.py:79-97; `DiscoveryEvaluator.score`, research/pooled/campaign.py:718-749). Each formula is a long-only daily top decile (formula.py:195-215), measured against the equal-weighted mean of eligible names (research/pooled/stats.py:54-105), and must clear t >= 3 alone (campaign-v3.json discovery_gate).

The repo contains only one piece of combination code. `combine_forecasts` (research/alpha/forecasts.py:219) pools per-symbol calibrated forecasts with a fixed 0.5 shrinkage (forecasts.py:63), only for qualified registry versions, of which there are none, and nothing reads what it writes (shadow.py:132-149).

The cross-sectional learners run so far were small:
- a 3-feature Ridge on 64 equities: mean daily Rank IC 0.042, but every annual HAC CI includes zero and 74% of the gain came from five names (docs/alpha-panel-forecasts-2026-09-17.md);
- the 13-feature setup ranker, which failed its holdout (docs/setup-outcomes-2026-09-23.md H3).

The outcome:
- 64 formula-cohort pairs, 0 finalists.
- The v4 return-space diagnostic: no formula reaches |t| >= 3 (docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic').
- The lane's own design doc expected 'few discoveries', because its detectable edge (~0.08R at t=3) is larger than single-signal US effects (docs/alpha-pooled-mining-research-2026-09-28.md 'Conclusion').

**Evidence**

- agentic_trader/research/pooled/formula.py:79-97,195-215
- agentic_trader/research/pooled/campaign.py:619-623 (ScoredFormula.panel is just Callable[[CubeView], (scores, allowed)])
- agentic_trader/research/pooled/campaign.py:718-749
- agentic_trader/research/pooled/stats.py:54-105
- agentic_trader/research/alpha/forecasts.py:63,219; agentic_trader/research/alpha/shadow.py:132-149
- agentic_trader/research/setups/study.py:289-313 (purged_walk_forward already exists), :402 (sklearn HistGradientBoosting already a dependency, pyproject.toml:33)
- docs/alpha-panel-forecasts-2026-09-17.md 'Forecast and economic results', 'Attribution'
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic'
- docs/alpha-pooled-mining-research-2026-09-28.md 'The arithmetic sets a 0.09R target', 'Conclusion'

**Recommendation**

RECOMMEND: Build a pooled model lane as a new `search_mode: model_set` of CampaignProtocol, on the contracts that already exist.

1. **Features.** A frozen, SHA-pinned feature library (see the feature-library finding) with K of about 40-80 expressions and fields. Evaluate it once via ScoreBook/FrozenPanels (scoring.py:114-151, 253) into X[S,N,K]. Rank-gauss each feature per session with `panel.cross_sectional_rank` (panel.py:19-21). Missing values become 0 plus a missingness indicator.
2. **Specs.** At most four predeclared specs, each charged as one trial through `JournalLedger.charge`:
   - M0: an equal-weight composite of literature-signed features, with no fitting;
   - M1: ridge on the ranks;
   - M2: sklearn HistGradientBoosting with depth <= 3, `monotonic_cst` set to the literature signs, and early stopping on a purged inner fold;
   - M3: the rank average of M0-M2.
3. **Out-of-sample scores.** Produce them with expanding `purged_walk_forward` (setups/study.py:289), with the purge equal to the label horizon. Wrap each prediction panel as a `ScoredFormula`, so `DiscoveryEvaluator`, `finish_discovery`, `later_stages` and the stationary bootstrap run unchanged.
4. **Decisive test.** Use Holm over the specs on the selection window, not on discovery. All 64 formulas' discovery picks have already been read, so discovery results are descriptive only. Confirm prospectively (see the cadence finding). Before declaring the window, check the journal's inspected intervals.
5. **Reporting.** Report daily Rank IC through `information.cross_sectional_ic` with HAC, the decile paired edge, the top-2 edge (the depth cards trade at) and PBO by CSCV across the specs.
6. **Cohort and expectations.** Start on cohort v3 (ranks 401-1,400; its 32 literature-signed formulas averaged t +0.46, recounted from docs/alpha-pooled-cohort3-2026-10-16.md and alpha-pooled-short-v1-2026-10-20.md). Take signs from the literature, never from these observed t-statistics. Expect an honest IC of 0.01-0.03.

**North-star impact**

Mining stops hunting for one formula that clears t>=3 alone, which is impossible at plausible effect sizes, and starts combining many weak signals. Its single output, a calibrated cross-sectional score, can both rank and generate the 1-2 daily cards. In the literature, this is the only way liquid-name signals with IC around 0.01-0.02 become usable.

**References**

- Gu, Kelly, Xiu (2020), Empirical Asset Pricing via Machine Learning, Review of Financial Studies 33(5) — Cross-sectional ML on many characteristics; trees and NNs beat single factors through interactions and combination (https://academic.oup.com/rfs/article/33/5/2223/5758276)
- Jensen, Kelly, Pedersen (2023), Is There a Replication Crisis in Finance?, Journal of Finance; 153 factors in 13 themes — Theme-level shrinkage and combination instead of flat per-factor gates; factor definitions and code (https://jkpfactors.com/)
- Avramov, Cheng, Metzker (2023), Machine Learning vs. Economic Restrictions, Management Science 69(5) — Honest prior: ML payoffs fall about 62% once microcaps are excluded, so expect small ICs in a $5M-floor universe (https://papers.ssrn.com/abstract=3450322)
- Bailey, Borwein, Lopez de Prado, Zhu (2017), The Probability of Backtest Overfitting, J. Computational Finance — CSCV/PBO as the selection-overfit diagnostic across the declared model specs (https://papers.ssrn.com/abstract=2326253)
- Green, Hand, Zhang (2017), The Characteristics that Provide Independent Information about Average U.S. Monthly Stock Returns, RFS — Few characteristics are independently significant after 2003, so breadth must come from combination with shrinkage

---

## Model scores have no deployment contract; the first use should be ranking and withholding the scan's own candidates, then a capped probe source

_area: signals · kind: integration · impact: high · effort: L · confidence: medium_

**Claim**

NOW: The registry and probe lanes accept only per-symbol time-series definitions:
- `eligible_symbols == (symbol,)` (research/alpha/promotion.py:88);
- raw adjustment only (promotion.py:474-480);
- an entry fires when the score's z against its own trailing 30 bars crosses a threshold (research/alpha/strategy.py:144-161; screeners/formulaic.py:102-108);
- catalog probes are hard-coded to PEAD (research/apriori/probe.py: `CATALOG_ENTRY_IDS = ("pead",)`).

Cards are ranked by the heuristic `setup_quality` (agent/copilot.py:1174-1177; journal `ranking_key` at :1907), which was measured as uninformative (holdout rho -0.012, docs/setup-outcomes-2026-09-23.md H1). `ScreenerCandidate` has no forecast field (screeners/base.py:23-52). The live cross-section excludes dynamic names (research/setups/ranker.py:245-249), so their features are NaN. A pooled finalist would need an unbuilt 'Part 2' spec (docs/alpha-pooled-mining.md:1016).

Even a successful model lane therefore has no route to Telegram.

**Evidence**

- agentic_trader/research/alpha/promotion.py:88,474-480
- agentic_trader/research/alpha/strategy.py:144-161; agentic_trader/screeners/formulaic.py:102-108
- agentic_trader/research/apriori/probe.py (CATALOG_ENTRY_IDS = ("pead",); identity = entry SHA + study manifest/result)
- agentic_trader/agent/copilot.py:1174-1177,1907
- agentic_trader/screeners/base.py:23-52
- agentic_trader/research/setups/ranker.py:235-253
- docs/setup-outcomes-2026-09-23.md 'Hypotheses' H1
- docs/alpha-pooled-mining.md:1016

**Recommendation**

RECOMMEND:

1. **Identity.** A content-addressed `ModelArtifact`: sha256 over the spec SHA, the refit index, the training-window end and the weight bytes. Its evidence is the campaign manifest/result plus the prospective report. This mirrors how catalog probes are already identified (entry SHA + study manifest/result). Generalise `CATALOG_ENTRY_IDS` into a registry of artifact kinds, so new kinds need no new code path.
2. **Selector first.** This needs the least power, because the control is the same scan's candidate set.
   - Add `forecast {model_id, horizon, expected_excess_r, rank_pct}` to `ScreenerCandidate`.
   - Score every ranked native and PEAD candidate at D-1, computing features over static and dynamic names together (fixing the dynamic-name NaN gap).
   - Roll it out in `off -> preview -> enforce` like `card_policy`. Preview journals the model's ordering beside `setup_quality`. Enforce re-ranks and withholds a candidate when its predicted residual edge is below zero. Score the preview prospectively with the existing selection block of `cards outcomes`.
   - Until a model spec confirms, preview can run on the predeclared M0 composite.
3. **Probe source.** A `CrossSectionalSource` emits the top 1-2 names at 10:35 with the cube's own bracket and a `session_count_v1` time exit, under the existing probe cap, kill rule and `probe_block_reason`.
4. **Parity test.** The live D-1 feature vector must equal the warehouse/cube value for the same name and session, as PEAD already guarantees by reusing the study's `build_events`.

**North-star impact**

This directly changes which 1-2 cards are sent each session. It replaces a ranking key measured as random with a forecast of excess return, and it gives mining its first tested channel to Telegram. It also works before any spec 'passes', because preview generates the evidence that enforce later needs.

**References**

- Lopez de Prado (2018), Advances in Financial Machine Learning, Wiley, ch. 3 — A primary signal plus a secondary model that decides whether and how much to act; here the cross-sectional score plays that role over the scan's candidates
- Grinold and Kahn, Active Portfolio Management (alpha = IC x sigma x z) — Converts a rank score into an expected-return field the book sizer can trade off against marginal risk

---

## The label and control teach market beta: train on residualised, volatility-scaled shorter-horizon returns and store risk characteristics in the cube

_area: research_methodology · kind: gap · impact: high · effort: M · confidence: medium_

**Claim**

NOW: The cube stores only eight arrays (eligible, labelled, r_gross, r_cost, holding, hit, tiebreak, dollar_volume; research/pooled/cube.py:53). Each cell carries one long 2xATR14 / 3R / 20-session bracket label. The control is the equal-weighted mean of labelled eligible cells (stats.py:54-105), with no adjustment for beta, volatility, size or sector.

The campaigns forbid `realized_vol`, `ts_std` and `ts_mad` (campaign-v4.json:135-139; insider-v1.json:307-311), so a formula cannot risk-normalise itself.

The v4 label diagnostic found the only return-space positives were illiquidity (+147 bp/20d, t 2.40) and momentum_12_1 (+96 bp, t 2.16). It concluded they 'look good in raw returns only because their deciles hold high-volatility names in a rising 2016-2021 market; per unit of risk the edge is gone'. It also found that naive IC t-statistics exceed 3 for about half of 200 persistent random scores (docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic').

With a 20-session horizon, the 1,360-session discovery window holds only about 68 non-overlapping cross-sections. The native cards show what an unresidualised target produces: +0.039R raw but -0.110R versus SPY (docs/bracket-counterfactual-2026-10-18.md 'Descriptives').

**Evidence**

- agentic_trader/research/pooled/cube.py:53,57-62
- agentic_trader/research/pooled/stats.py:54-105
- config/research/pooled/campaign-v4.json:135-139; config/research/pooled/insider-v1.json:307-311
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic' (table and bullets)
- docs/alpha-pooled-cohort3-2026-10-16.md (1,360 discovery sessions per formula)
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives'

**Recommendation**

RECOMMEND:

1. **Extend the cube.** Adding these arrays is a new CubeSpec identity, and the v2/v3 rebuild needs only cached bars. Per cell, store:
   - atr_pct;
   - beta_60 against SPY;
   - log 20-session median dollar volume;
   - an SIC-based Fama-French 12 sector from EDGAR `submissions`;
   - forward volatility-scaled returns at 1, 5 and 20 sessions, entering at the D+1 open.
2. **Training target for the model lane.** Use the cross-sectionally residualised 5-session volatility-scaled return: per session, regress on beta, atr_pct, log dollar volume and sector dummies, and keep the residual. That gives about 272 non-overlapping discovery cross-sections instead of 68, and it stops the learner from loading on market and volatility exposure.
3. **Evaluation.** Still score on the deployed bracket R, but against a risk-matched control: the per-session regression residual, or a DGTW-style sector x ATR-quintile cell. Report raw, residual and bracket figures side by side. Declare the decisive statistic in the protocol. Keep the session-block bootstrap, and never use naive IC t-statistics.
4. **Feature contamination.** Use the risk characteristics as neutralisers, not as alpha features. Families such as vol_20, which earlier studies already read on 2021-2026, then do not contaminate the confirmation interval.

**North-star impact**

This stops a model from rediscovering 'buy high-beta names in a bull market', which is why native cards trail SPY. Any score that reaches a card becomes an excess-return forecast, so a 'good' card means selection skill, not tape.

**References**

- Daniel, Grinblatt, Titman, Wermers (1997), Measuring Mutual Fund Performance with Characteristic-Based Benchmarks, Journal of Finance — Characteristic-matched control cells for picks
- Lopez de Prado (2018), Advances in Financial Machine Learning, ch. 4 and 7 — Overlapping labels reduce sample uniqueness; purged and embargoed CV
- Frazzini and Pedersen (2014), Betting Against Beta, JFE; Ang, Hodrick, Xing, Zhang (2006), JF — Beta and volatility tilts dominate R-scaled outcomes unless they are neutralised

---

## Mining has no cadence: add a daily prospective scorer and scheduled walk-forward refits, with the frozen procedure as the trial identity

_area: operations · kind: gap · impact: high · effort: M · confidence: medium_

**Claim**

NOW:
- The weekly miner is retired (scripts/launchd.sh:21, 202-204), and pooled campaigns are manual one-shots run on operator approval.
- The daemon's research workers are the session observer and decisions, the daily panel, card_stats and corporate actions (cli/commands/service.py:24-40). None of them scores the 72 already-charged frozen formulas forward.
- Identity is immutable and content-addressed (research/alpha/models.py:127-135). There is no notion of a model whose weights are refit on a schedule under a fixed spec.
- Confirmation is one lane-wide historical window (2024-01 to 2026-07), and `LITERATURE_ENTRIES` is hard-coded to two files (research/pooled/campaign_run.py:107). The PEAD and exdiv reads of that window are therefore not counted.
- The existing prospective worker blanks a whole day's IC when one of 64 labels is missing (research/alpha/daily_forecasts.py:551). Its first outcome came back unavailable for that reason (live map Q13b).

**Evidence**

- scripts/launchd.sh:21,202-204
- agentic_trader/cli/commands/service.py:24-40,274-296
- agentic_trader/research/alpha/daily_observations.py:56 (DailyPanelService pattern)
- agentic_trader/research/alpha/daily_forecasts.py:551
- agentic_trader/research/pooled/campaign_run.py:107
- agentic_trader/research/alpha/models.py:127-135
- docs/alpha-pooled-mining.md:617-622 (window consumption is irreversible)

**Recommendation**

RECOMMEND:

1. **Prospective scorer.** A `ProspectiveScorer` daemon worker, following the `DailyPanelService` pattern, runs at 08:30 ET after card_stats. For every frozen formula and every frozen model spec it:
   - computes D-1 scores on warehouse bars;
   - journals the top-decile and top-2 picks together with the eligible control. Nothing is charged, because the spec was charged when it was frozen.
   - labels matured picks with `label_bracket` (research/setups/labels.py:159);
   - keeps an always-valid confidence sequence on the paired edge;
   - drops at most k missing names per session with reason codes, instead of NaN-ing the whole session.
2. **Schedule.**
   - Nightly: incremental data refresh.
   - Daily: scoring.
   - Quarterly: walk-forward refit of each spec on matured labels only. The trial identity is the spec SHA (feature-library SHA + model class + hyperparameter grid + refit rule + training-window rule). Each refit's weights are a pinned artifact under that SHA, and a refit is not a new trial.
   - Quarterly or semi-annually: operator-approved releases of new spec versions.
3. **Promotion.** When the confidence-sequence lower bound on the residual paired edge stays above 0 after at least 120 sessions, the spec becomes eligible for the selector or probe (see the deployment finding).
4. **Window governance.** Use the historical windows for discovery and selection, and the prospective interval for confirmation. Replace `LITERATURE_ENTRIES` with a check against every journaled inspected interval.

**North-star impact**

This delivers the 'reasonable cadence' the operator asked for. Evidence accrues every session at no trial cost and without spending the last historical window, and any spec that confirms is already wired to produce cards.

**References**

- Howard, Ramdas, McAuliffe, Sekhon (2021), Time-uniform, nonparametric, nonasymptotic confidence sequences, Annals of Statistics 49(2) — Valid daily peeking for a prospective scorer and its promotion rule (https://projecteuclid.org/journals/annals-of-statistics/volume-49/issue-2/Time-uniform-nonparametric-nonasymptotic-confidence-sequences/10.1214/20-AOS1991.full)
- McLean and Pontiff (2016), Does Academic Research Destroy Stock Return Predictability?, Journal of Finance — Post-publication decay makes continuous forward monitoring and scheduled refits necessary

---

## Flexible learners amplify survivorship bias, so point-in-time membership must come before any model trained on 2016-2021

_area: data · kind: gap · impact: high · effort: M · confidence: medium_

**Claim**

NOW:
- The cohorts are names 'listed and active on 2026-09-17, ranked on SIP dollar volume ending 2023-12-29' (config/research/pooled/cohort-v2.json:4), yet every protocol discovers on 2016-08 to 2021-12 (campaign-v3.json windows).
- The survivorship spike found 152-310 liquid now-dead names per year in 2016-2021 that can be recovered for free from the Alpaca inactive list plus SIP bars (docs/survivorship-spike-2026-10-20.md). It misstated the discovery window (data map G4).
- Bar requests never set `asof` (data/evidence.py:24-27 lists the field, and nothing sets it), so reused tickers can contaminate a rebuild.

The bias of a single formula has a known sign. A learner fits the bias through interactions: for example, 'buy deep losers with high volatility' looks safe when the losers that died are missing. Ranking on 2023 liquidity also selects names that grew.

**Evidence**

- config/research/pooled/cohort-v2.json:4; config/research/pooled/campaign-v3.json 'windows'
- docs/survivorship-spike-2026-10-20.md (dead names per year 2016-2021)
- agentic_trader/data/evidence.py:24-27
- agentic_trader/research/pooled/cohort.py:193-292 (point-in-time eligibility exists, but membership is current)

**Recommendation**

RECOMMEND: Build cohort v4 before training any model-lane spec.

- **Membership.** Use a dated asset master: the Alpaca inactive list for 2016-2021, then EDGAR Form 25 and Form 15 dates afterwards.
- **Eligibility.** Rank each session by D-1 trailing dollar volume, reusing `point_in_time_eligibility` (research/pooled/cohort.py:193), instead of by 2023 liquidity.
- **Delisting returns.** Use the last price to cash, or a CRSP-style -30% for performance delistings.
- **Bars.** Pin `asof` on bar requests for reused tickers.

Train and evaluate the model lane only on v4. Keep the v2/v3 cubes for descriptive comparison, and report the change in the base rate as the measured cost of survivorship.

**North-star impact**

Prevents the first ML spec from 'confirming' a survivorship artefact that would send systematically losing cards. It also makes long base rates on cards honest rather than upper bounds.

**References**

- Shumway (1997), The Delisting Bias in CRSP Data, Journal of Finance — Missing delisting returns bias anomaly returns, most strongly for distressed and reversal-type signals

---

## Replace hand-picked 8-formula sets with a frozen, literature-signed feature library and add the long-requested cross-sectional DSL operators

_area: alpha_mining · kind: improvement · impact: high · effort: M · confidence: medium_

**Claim**

NOW:
- Fields are a global dict edited per lane (research/alpha/dsl.py:28-48), with no source, visibility-lag or sign metadata.
- Operators are single-series only (research/alpha/operators.py:108-169). dsl.py:1-5 says cross-sectional operators are 'deliberately absent'.
- `cross_sectional_rank`, `cross_sectional_zscore` and `group_neutralize` exist (research/alpha/panel.py:19-37), but only research/setups/features.py:27 uses them.
- Formulas are evaluated per symbol in a loop (research/pooled/formula.py:125-130), so a panel operator cannot be expressed.
- Each fixed set is 8 hand-picked expressions (campaign-v4.json and insider-v1.json families). 'Only when' is written as division by a boolean, for example `insider_buy_rel / (insider_buy_rel > 0)`.
- `log` is declared dimensionless (operators.py:125), so price-level ranks pass the unit check.
- Rolling operators run Python lambdas: ts_rank, decay_linear, ts_slope and ts_mad (operators.py:15-19, 65-67, 138-152).
- Cross-sectional operators were recommended three times (docs/alpha-data-augmentation-2026-10-18.md §3) and are still absent.

**Evidence**

- agentic_trader/research/alpha/dsl.py:1-5,28-48
- agentic_trader/research/alpha/operators.py:15-19,65-67,108-169,125
- agentic_trader/research/alpha/panel.py:19-37
- agentic_trader/research/pooled/formula.py:125-130
- config/research/pooled/campaign-v4.json and insider-v1.json families
- docs/alpha-data-augmentation-2026-10-18.md §3 'Method gaps'

**Recommendation**

RECOMMEND:

1. **Library file.** `config/research/features/library-v1.json`, SHA-pinned. Each entry holds: id, expression or field source, units, visibility (D-1 / D+1 / filing date), literature sign, citation and OSAP/JKP acronym.
2. **Seed signals computable from current data.** Use the Open Source Asset Pricing and JKP signals that today's inputs (OHLCV, corporate actions, Nasdaq earnings pages, FINRA, EDGAR Form 4) can compute:
   - momentum: Mom12m, Mom6m, IndMom, MomSeason;
   - reversal and lottery: STreversal, MaxRet;
   - risk: IdioVol, Beta, BetaFP;
   - liquidity and price: DolVol, High52, PriceDelay;
   - events: SUE, AnnouncementReturn, EarningsStreak, short-flow proxies, insider net purchases.
   Then add XBRL fundamentals from SEC companyfacts using `filed` dates: shares outstanding for size and turnover, book equity, accruals, gross profitability, asset growth.
3. **DSL v2.** Add `cs_rank`, `cs_zscore`, `group_demean(x, sector)`, `where(cond, x)`, `ts_beta(x, mkt, d)` and `ts_skew`, plus `mkt_ret` and `sector_ret` fields. Evaluate these at panel level in ScoreBook.
4. **Performance and units.** Vectorise the rolling operators (bottleneck/numba, with the existing prefix tests as the oracle). Make `log` keep its units.
5. **New data.** A new source becomes library columns, not a new lane.

**North-star impact**

This supplies the breadth that combination draws its power from. It lets sector-neutral and beta-adjusted features rank cards, and turns each new data source from a 1-2-week lane into a set of columns.

**References**

- Chen and Zimmermann (2022), Open Source Cross-Sectional Asset Pricing, Critical Finance Review; openassetpricing.com (Python port of the signals, v1.4.0) — Replicated signal definitions and code, with published t-stats as literature signs and priors (https://www.openassetpricing.com/)
- Jensen, Kelly, Pedersen (2023), Global Factor Data: 153 characteristics, 13 themes — Characteristic definitions and theme clustering for hierarchical priors (https://jkpfactors.com/)
- Microsoft Qlib: Alpha158/Alpha360 handlers, LightGBM baselines, point-in-time database — A vetted vectorised expression engine and feature set to benchmark against; design template, not a dependency (https://github.com/microsoft/qlib)
- Heston and Sadka (2008), Seasonality in the Cross-Section of Stock Returns, JFE; Moskowitz and Grinblatt (1999), Do Industries Explain Momentum?, JF — Robust price-only signals that need cross-sectional and sector operators

---

## Stop building a standalone 8-formula pooled lane for each new data source, and re-encode the pending insider campaign before approving it

_area: alpha_mining · kind: stop_doing · impact: high · effort: S · confidence: high_

**Claim**

NOW:
- Five of the 10 PRs since the north-star restatement were pooled lanes: #120 intraday, #121 events, #122 cohort v3, #126 short volume, #128 insider (git log 9cfacc4..db82817). None of them can reach a card.
- Each data source needs its own protocol subclass (research/pooled/campaign.py:403-584), a Sources trio (runner.py:93-195), isinstance branches and an N x N CLI exclusion matrix. `build_cube_inputs` refuses more than one source (runner.py:317-318), so no formula can mix sources.
- Every fixed set is 8 standalone trials under a gate whose minimum detectable edge (about 0.065-0.08R; docs/alpha-pooled-cohort3-2026-10-16.md check A table) is above plausible single-signal effects.
- Check A, which does not depend on formula content, is re-run for 1.5-3.7 h per protocol.
- The pending insider-v1 set tests Lakonishok-Lee and Cohen-Malloy-Pomorski priors, whose horizon is about 6-12 months, on a 20-session bracket. It encodes them as a daily decile among in-condition names (docs/alpha-pooled-mining.md:1223-1235; insider-v1.json bracket). A null would be uninformative, and a lucky survivor would consume the lane-wide confirmation window.

**Evidence**

- git log 9cfacc4..db82817 (PRs #118-#128)
- agentic_trader/research/pooled/campaign.py:403-584
- agentic_trader/research/pooled/runner.py:93-195,317-318
- config/research/pooled/insider-v1.json (bracket max_hold_sessions 20; families)
- docs/alpha-pooled-mining.md:1223-1235
- docs/alpha-pooled-cohort3-2026-10-16.md (check A detection 0.76 at 0.08R)

**Recommendation**

RECOMMEND:

1. **Freeze the pattern.** Add no new per-source CampaignProtocol subclasses.
2. **Insider.** Run its checks only as data-quality evidence. Do not approve the insider-v1 / cohort3-insider-v1 campaigns as specified. Add the six insider fields (and the short-volume and event fields) to library-v1 as columns. If an insider-specific test is still wanted, declare it with an event-entry rule and a horizon-matched hold.
3. **Plumbing.** Replace the subclasses with a single `FieldSource` plugin (pins, load, verify, coverage, names) that can join several sources.
4. **Check A.** Cache it by cube SHA, selection rule, gates and stage-code digest instead of by protocol hash.
5. **Yield check.** Before building any new acquisition, write down the expected effect after the McLean-Pontiff haircut and compare it with the detection floor.

**North-star impact**

Frees roughly half of recent engineering effort for the combination and deployment path, which is the only route from mining to cards. It also protects the last historical confirmation window from being spent on a mis-specified test.

**References**

- Cohen, Malloy, Pomorski (2012), Decoding Inside Information, Journal of Finance; Lakonishok and Lee (2001), Are Insider Trades Informative?, RFS — Insider-purchase effects are measured over 6-12 month horizons, concentrated in small caps
- McLean and Pontiff (2016), Journal of Finance — Haircut for the ex-ante yield gate

---

## The pooled genetic search never used fitness, so check B's failure measured random sampling rather than search power

_area: alpha_mining · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

NOW:
- Campaigns v2 and v3 split `formula_budget` 200 across 12 families, giving 16-17 formulas each (`family_budgets`, research/pooled/campaign.py:381-391). Both set `archive_size` to 16 (campaign-v2.json:317; campaign-v3.json:320).
- `TypedGeneticSearch.tell` evicts only above `archive_size` (research/alpha/search.py:89-111).
- `ask()` picks parents uniformly from `sorted(self.archive)` (search.py:175-180), so fitness never influences parent choice. When the budget is at or below the archive size, nothing is evicted before the last ask either.

Every proposal in the v2/v3 searches, and in check B (research/pooled/search_power.py:115-124 runs the same `family_search` with the campaign budget), was therefore a uniformly random crossover or mutation of a formula proposed earlier.

The result docs instead attribute B's failure (1/10, then 3/10) to basket non-smoothness and the search's 'reach', and the lane was parked on that reading (docs/alpha-pooled-checks-2026-10-02.md 'Why it failed'; docs/alpha-pooled-checks-v3-2026-10-05.md 'Why it failed').

**Evidence**

- agentic_trader/research/alpha/search.py:89-111,164-184 (parents = sorted(self.archive); rng.sample/rng.choice)
- agentic_trader/research/pooled/campaign.py:381-391
- config/research/pooled/campaign-v2.json:42,317; config/research/pooled/campaign-v3.json:45,320
- agentic_trader/research/pooled/genetic.py:147-153 (archive_size taken from protocol)
- agentic_trader/research/pooled/search_power.py:15-20,107-124
- docs/alpha-pooled-checks-v3-2026-10-05.md 'Why it failed', 'Decision'

**Recommendation**

RECOMMEND:

1. **Correct the record.** Amend docs/alpha-pooled-mining.md so it says that checks B v2/v3 measured random neighbourhood sampling, not search power.
2. **Do not reopen GP formula search in the pooled lane.** Under per-formula charging and t >= 3 / Holm gates, useful GP or LLM search budgets (thousands to tens of thousands of evaluations) are unaffordable. Do any search inside one charged model spec, as nested purged CV over a small, declared hyperparameter grid.
3. **If any GP is revived**, including the per-symbol miner (archive 32, budget 512):
   - use tournament or fitness-proportional parent selection;
   - set `archive_size` well below the budget;
   - add a unit test that the proposal sequence changes when the told fitness values change.

**North-star impact**

Removes a misleading result from the design record, and ends any plan to fix mining with a 'larger GP budget', which could not reach card-grade evidence under the trial accounting.

**References**

- Tang et al. (2025), AlphaAgent: LLM-Driven Alpha Mining with Regularized Exploration to Counteract Alpha Decay, arXiv 2502.16789 — Competitive formula search runs at far larger budgets and reports S&P 500 out-of-sample IC of only 0.0056 (as cited in docs/alpha-pooled-mining-research-2026-09-28.md) (https://arxiv.org/abs/2502.16789)

---

## Retire the per-symbol time-series DSL lane as a deployment unit, together with its 16 permanently invalid shadow registrations

_area: alpha_mining · kind: stop_doing · impact: medium · effort: S · confidence: high_

**Claim**

NOW:
- Qualification requires a single-symbol, raw-bar definition that fires on a 30-bar time-series z (promotion.py:88, 474-480; strategy.py:144-161).
- The holdout and validation DSR are deflated by the lifetime `family/all` trial count (promotion.py:351-366, 370-388; storage/alpha.py:239-251). Per the alpha-pipeline map's arithmetic, that needs an annualised Sharpe of about 4.9 on a 252-bar holdout. Planted-effect acceptance was 0/64 (docs/alpha-power-diagnosis-2026-09-18.md).
- Live, the registry holds 16 shadow alphas. All 10,359 shadow forecasts are invalid: 6,915 deployment_data_contract_mismatch and 3,444 stale_closed_bar (live map Q13; research/alpha/clock.py:47-50).
- `AlphaShadowService.observe` still runs per static contract in every scan (agent/copilot.py:1062-1065), and `combine_forecasts` output is never read.

**Evidence**

- agentic_trader/research/alpha/promotion.py:88,351-366,370-388,474-480
- agentic_trader/storage/alpha.py:236-251
- agentic_trader/research/alpha/clock.py:47-50
- agentic_trader/agent/copilot.py:1062-1065
- docs/alpha-power-diagnosis-2026-09-18.md (0/64 planted acceptance)
- live map Q13: alpha_projections forecast/% grouped by valid, reason

**Recommendation**

RECOMMEND (operator decision):

- Declare in docs/alpha-roadmap.md that per-symbol z-threshold definitions are no longer a promotion path.
- Demote the 16 shadow registrations and stop `observe` in scans.
- Tag and remove the per-symbol miner, simulation and forecast-combination harnesses from main, keeping reproducibility by git tag.
- Keep the parts the model lane reuses: the DSL compiler, registry CAS, probe lane and admission fencing.
- Freeze `family/all` as historical. It should not deflate the model lane, which charges its own ledger.

**North-star impact**

Removes a path that cannot produce cards but still costs scan time, journal volume and design attention. It also lowers the cost of changing the shared kernels the model lane needs.

**References**

- Bailey and Lopez de Prado (2014), The Deflated Sharpe Ratio, J. Portfolio Management — The DSR is meant to penalise selection within the search that produced a candidate, not to tax a frozen finalist with every trial ever run

---

## Meta-labelling native setups was already tried and lost in the holdout; a redo needs a different target, data and selection metric, and should withhold rather than rank

_area: signals · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

NOW: The setup-outcome study fitted logistic, ridge and HGB models on `setup_features_v2` (13 features) over 14,017 development setups, using purged walk-forward folds (5 folds, 20-session embargo).
- The winner was picked by the median fold top-2 mean R (research/setups/study.py:607-614).
- Ridge's development gain came from one fold. In the one-shot 116-session holdout it did significantly worse than `setup_quality`: top-2 -0.293R against -0.126R, difference CI90 [-0.325, -0.016] (docs/setup-outcomes-2026-09-23.md 'Selection (H3)').
- The target was raw R_cost.
- The native population's excess is -0.11R (docs/bracket-counterfactual-2026-10-18.md), so a meta-label can at best withhold cards, not create edge.

The architecture map's proposal to rank cards by a meta-label probability trained on the replayed setups does not account for this result.

**Evidence**

- agentic_trader/research/setups/study.py:58-59,289-313,402,607-614
- agentic_trader/research/setups/features.py:44-59
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)', 'Interpretation and decisions'
- docs/setup-baserates-short-2026-09-23.md (fresh 2017-2021 replay: 11,480 setups, 8,115 long)
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives'

**Recommendation**

RECOMMEND: Redo it only as a new frozen protocol that changes all of the following.

- **Target.** Beta-adjusted excess R, or P(target before stop) weighted by excess R.
- **Features.** The library's D-1 cross-sectional ranks for the setup's name, plus the setup geometry.
- **Data.** Train on both replays (2021-26: 14,017 setups; 2017-21: 11,480), with session-grouped purged CV.
- **Selection metric.** Choose the model on per-session rank IC across all candidates, which has far lower variance than top-2 R, and take the simplest model within one standard error.
- **Calibration.** Isotonic.
- **Use.** A withholding rule inside `card_policy` preview: withhold when the calibrated expected excess is below zero. Score it prospectively.
- **PEAD.** Apply the same machinery to PEAD events (positive base rate, about 4,726 events) for sizing, which is where meta-labelling has an edge to modulate.

**North-star impact**

This could remove the worst native cards and size PEAD cards by calibrated probability, without repeating a design that already failed. Expect a modest effect.

**References**

- Lopez de Prado (2018), Advances in Financial Machine Learning, ch. 3 (meta-labeling) — A secondary model on a primary signal; useful when the primary signal has positive base recall, as PEAD does and native setups do not

---

## Use LLMs as label-blind feature curators and as a debiased news feature, not as formula search engines

_area: alpha_mining · kind: research_direction · impact: medium · effort: M · confidence: low_

**Claim**

NOW:
- LLMs play no role in mining. Live, the LLM gate approved 14 of 14 journaled evaluations (live map Q7).
- News tone is direction-free card context and is never a gate. Benzinga history back to 2015-01-02 is cached, and no study uses it (docs/news-context.md:151-155 and 'No study exists yet').
- AlphaAgent-style LLM mining reports S&P 500 out-of-sample IC of 0.0056, against 0.0212 on CSI 500 (docs/alpha-pooled-mining-research-2026-09-28.md line 51).
- Under the pooled ledger, every LLM-proposed formula scored on labels is a charged trial, so LLM search in formula space inherits the GP problem.
- The ScoreBook design is already label-blind (scoring.py:99-110).

**Evidence**

- docs/news-context.md:151-155, 'Research acquisition'
- docs/alpha-pooled-mining-research-2026-09-28.md:51
- agentic_trader/research/pooled/scoring.py:99-110 (label-blind panels)
- agentic_trader/research/pooled/formula.py:238-246 (jaccard_codes), research/alpha/search.py canonical_expression
- live map Q7 (14/14 LLM approvals)

**Recommendation**

RECOMMEND:

1. **LLM as curator.** The LLM drafts library entries in the DSL from paper abstracts and OSAP documentation, each with a literature sign and citation. It never sees labels or scores. A human reviews the batch and it is frozen as a library version, so its cost is charged once at the model-spec level. AlphaAgent's originality check maps onto `canonical_expression` and pick-Jaccard against the existing library.
2. **News feature.** Predeclare a news library column: LLM tone and relevance of headlines published before 09:30, using point-in-time `created_at` (and keep `updated_at`), with a frozen prompt and model id. Score with entity anonymisation, removing company names and tickers, to limit the model's memorised knowledge.
3. **Evaluation window.** Weight evaluation toward periods after the model's training cutoff, or treat it as prospective only. In-sample historical scores from an LLM are contaminated by what it learned in training.

**North-star impact**

Gives the library breadth cheaply, and tests the one place where an LLM plausibly adds information (text) without spending the trial budget.

**References**

- Wang et al. (2023), Alpha-GPT: Human-AI Interactive Alpha Mining for Quantitative Investment, arXiv 2308.00016 — Human-in-the-loop LLM hypothesis drafting (https://arxiv.org/abs/2308.00016)
- Lopez-Lira and Tang (2023), Can ChatGPT Forecast Stock Price Movements?, arXiv 2304.07619 — Headline-level LLM tone predicts returns, mainly for small stocks and negative news (https://arxiv.org/abs/2304.07619)
- Glasserman and Lin (2023), Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis, arXiv 2309.17322 — Anonymisation as a de-biasing tool for historical LLM sentiment backtests (https://arxiv.org/abs/2309.17322)


# Lens: product (14 findings)

## The card stream is a market-beta product judged by a raw-R quality bar, so the gate meant to stop it would certify it

_area: product · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

Now:
- 37 of the 41 cards sent since 2026-09-18 are native longs (36 trend-pullback, 1 squeeze). The other 4 are PEAD probe cards.
- On 8,768 replayed native setups the live bracket earns +0.039R after cost. SPY over the same holding windows earns +0.211R, so the excess is -0.110R. The 2017-2021 window gives -0.103R.
- The agreed test (2) of a 'reasonable suggestion' is non-negative EV after cost in raw R.
- card_policy compares the raw mean_r_cost with min_measured_ev. It ships mode "off" with min_measured_ev null.
- CardKeyStats has no excess-return field, although every labelled row already carries excess_r = r_cost - market_r.

At threshold 0 in raw R the gate passes the native source in any rising tape: replay +0.039R, today's live snapshot +0.535R. It would certify beta as quality.

**Evidence**

- query (read-only psql, signals since 2026-09-18 by strategy/slot): 36 TREND_PULLBACK, 1 SQUEEZE_BREAKOUT, 4 pead_long
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' (A0_live r_cost +0.039, SPY own window +0.211, excess -0.110; robustness -0.103) and 'What this says' item 3
- docs/alpha-roadmap.md:507-510 (definition test (2) is raw EV after cost)
- agentic_trader/execution/card_policy.py:44,54 (measured_ev = evidence.mean_r_cost; withhold when < threshold)
- config/config.yaml:454-455 (card_policy mode off, min_measured_ev null)
- agentic_trader/research/setups/card_stats.py:43-62 (CardKeyStats fields: no excess)
- agentic_trader/research/setups/outcomes.py:382-383 (excess_r already computed per row)
- agentic_trader/research/setups/outcomes.py:198-199 (market_r docstring: beta-one exposure control)

**Recommendation**

This week, the operator should restate test (2) as: non-negative expected excess over a beta-matched index position, over the card's own holding window, after cost.

Code (small):
- Add mean_excess_r, with its count and standard error, to CardKeyStats from the existing excess_r column.
- Key it by (strategy, timeframe, direction).
- Move card_policy to preview on excess R at threshold 0 now.

Before enforce:
- Replace market_r's beta-one control with a 60-session pre-entry beta.
- Land the honest-estimator fixes (separate finding).

When natives are withheld, add one line to the scan digest, e.g. 'N native setups withheld: measured excess -0.11R vs SPY', so an empty slot reads as a decision rather than an outage.

If the operator still wants market exposure, offer it as an explicitly labelled 'beta-timing' card that shows its SPY-equivalent dollars, never as a selection card.

**North-star impact**

Defines 'high quality' as a falsifiable, market-relative number, so the desk stops sending a measured -0.11R-vs-SPY product presented as stock selection. Honest supply then comes from the positive-over-control sources (PEAD and other event entries), which the following findings expand.

**References**

- Grinold & Kahn, Active Portfolio Management (2nd ed., 2000), ch. 2-4 — Skill is residual (benchmark-relative) return; a long-only card stream must be judged net of the index exposure the operator could hold for free.
- Jensen, M. (1968), 'The Performance of Mutual Funds in the Period 1945-1964', Journal of Finance 23(2) — Classic case for measuring performance net of beta, the correction the card gate lacks.

---

## The only research-positive source is starved, and its probe expires 2026-10-28 with no decision rule

_area: signals · kind: improvement · impact: high · effort: M · confidence: medium_

**Claim**

PEAD long passed all four predeclared criteria:
- +0.149R per trade after cost;
- +0.055R over the every-reporter control (ci90 +0.021 to +0.089);
- a median of 2 and a p90 of 9 qualifying events per decision session.

Live, the probe is held well below that supply:
- at most 1 drift card per session, decided only at the 10:35 slot;
- $100 risk per trade under a 30-day term that ends 2026-10-28;
- so far 4 cards, 1 fill, 0 closed trades.

The forward record depends on manual taps, yet probe_outcomes already labels every eligible event counterfactually.

Two caveats:
- The study never reports the leg net of SPY; the control's +0.094R is mostly drift and survivorship.
- Recent years are weak (2025 +0.05R, 2026 +0.01R).

**Evidence**

- docs/apriori-pead-2026-09-26.md 'Criteria' (P1 +0.149, P2 +0.055, control +0.094) and 'Diagnostics' (events per session median 2 / p90 9; LONG by year)
- agentic_trader/config.py:178 (max_drift_cards_per_session=1), :181 (max_open_drift_positions=4)
- agentic_trader/screeners/earnings_drift.py:108-109 (applies only at the entry's decision_time_et)
- agentic_trader/agent/copilot.py:1273-1279 (separate drift budget)
- agentic_trader/config.py:829-831 (probe_risk_dollars 100, probe_term_days 30; comment: review checkpoint, not a risk bound)
- docs/alpha-roadmap.md:574-575 (probe expires 2026-10-28)
- docs/apriori-alphas.md 'Part 2' Measurement (counterfactual label of every eligible event; not comparable until 10 closed trades)
- maps/live.md Q13 (4 cards, 1 executed, 0 closed)

**Recommendation**

1. Before 10-28, the operator renews the probe with --renew, attaching a predeclared, workflow-based continue/kill rule. The rule reads the counterfactual label of every eligible event, not taps, because significance is out of reach at this rate. Example: continue unless the counterfactual mean r_cost over at least 40 mature events has a ci90 upper bound below 0, or the -4R kill fires.
2. Compute the leg's excess over SPY, and over a beta-matched benchmark, descriptively from the study's own labels. Print that market-relative base rate on drift cards.
3. Write a probe v2 spec for operator approval:
   - raise max_drift_cards_per_session to 2 (the study median);
   - set per-card risk to native parity ($250) once card_policy withholds natives;
   - keep the frozen D+2 10:35 entry; a 14:35 entry would need its own frozen entry.
   max_open_drift_positions (4) and the 8-slot book bound the total exposure.

**North-star impact**

Moves the scarce card slots from a source measured at -0.11R vs SPY to the only source with a measured edge over its control. In earnings season it can supply 1-2 cards per session on its own, and renewing keeps the only working research-to-Telegram route alive.

**References**

- Bernard, V. & Thomas, J. (1989), 'Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?', Journal of Accounting Research 27 — Canonical evidence for the drift the probe trades; drift runs for about a quarter, consistent with the study's +0.25R 60-session diagnostic.
- Martineau, C. (2022), 'Rest in Peace Post-Earnings Announcement Drift', Critical Finance Review 11 — Finds PEAD largely gone in large caps recently, consistent with the weak 2025/2026 years; a reason for a forward kill rule rather than open-ended scaling.

---

## The research program is not converging on the north star: stop building single-source pooled fixed-set lanes

_area: alpha_mining · kind: stop_doing · impact: high · effort: S · confidence: high_

**Claim**

Now:
- Since the north star was restated on 2026-10-14, 5 of 10 merged PRs were pooled lanes (#120, #121, #122, #126, #128). None changed card selection or a card source; ranking is still setup_quality.
- 64 formula-cohort pairs produced no finalist.
- The implied discovery standard errors (|edge/t| from the published tables; for example momentum_12_1 +0.033 at t 1.04, first_bucket_efficiency +0.002 at t 0.34) have a median of about 0.017R. At t >= 3 and 80% power, the minimum detectable edge is about 0.065R.
- The 64 t-statistics (mean -0.06, sd 1.25) sit on check C's null spread.
- The lane's own design study predicted this: edges of PEAD's size 'can only be found as a priori hypotheses', and 'the a priori catalog is the higher-yield source of daily suggestions'.
- Even a pass cannot reach Telegram, because the Part 2 probe spec is unbuilt.

Result: zero promotions in five weeks, while the one positive source is capped.

**Evidence**

- git log db82817..5c886b6 (read-only): #120 intraday, #121 events, #122 cohort v3, #126 short-volume, #128 insider
- agentic_trader/agent/copilot.py:1174-1177 (ranking unchanged: -setup_quality)
- docs/alpha-pooled-cohort3-2026-10-16.md:95,110 (edge/t rows)
- docs/alpha-pooled-checks-v4-2026-10-07.md:50-52
- maps/pooled.md section 4 (re-derived se distribution; t mean -0.06, sd 1.25 vs check C seed-t sd 1.11-1.27)
- docs/alpha-pooled-mining-research-2026-09-28.md:5 and :119-121
- docs/alpha-pooled-mining.md:1014-1017 (Part 2 probe spec only after a confirmed formula)
- CLAUDE.md alpha pipeline section (64 formula-cohort pairs, no finalist)

**Recommendation**

Declare insider-v1 and cohort3-insider-v1 the last campaigns of this design. Run them only after two things:
- (a) the a priori insider event entry (separate finding) is frozen first, so the higher-yield design gets the clean read;
- (b) every result row reports a per-formula minimum detectable edge and a 90% equivalence bound. Plant delta on each real formula's picks inside check C's null panels; this takes minutes.

Open no new single-source decile lane unless it passes an ex-ante yield gate:
- take the literature effect;
- haircut it about 50% for post-publication decay;
- map it with delta ~ 3*IC (alpha-pooled-mining-research-2026-09-28.md:51);
- require the result to exceed the lane's measured minimum detectable edge;
- require a built route to a card.

Rebalance effort toward card-stream fixes, a priori event entries and prospective scoring. Track the share of research PRs that change a card source or selection, with a target of at least half.

**North-star impact**

Frees roughly half the engineering bandwidth, now spent on lanes that cannot detect plausible effects or reach Telegram, for the sources and selection that decide which 1-2 cards go out each session.

**References**

- McLean, R.D. & Pontiff, J. (2016), 'Does Academic Research Destroy Stock Return Predictability?', Journal of Finance 71(1) — About 58% post-publication decay; basis for the ex-ante yield haircut.
- Hou, K., Xue, C. & Zhang, L. (2020), 'Replicating Anomalies', Review of Financial Studies 33(5) — Most anomalies fail in liquid, value-weighted samples, exactly the current-member large-cap cohorts these lanes test.
- Harvey, C., Liu, Y. & Zhu, H. (2016), '...and the Cross-Section of Expected Returns', Review of Financial Studies 29(1) — The t>3 hurdle the lane uses; at that hurdle, power is the binding constraint for small effects.

---

## Restart-driven swing scans spend the session's card budget off-slot and starve the 14:35 suggestion scan

_area: operations · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

Scheduling now:
- The swing scan is registered with ScanBudget.FULL every 4 h from daemon start, and also runs once immediately at each start.
- The config comment 'aligned with 4h candle closes' is false.
- ScanBudget has no journal-only mode; NONE still sends cards.

Since 2026-09-18:
- 15 of 41 cards were sent off-slot, between 09:37 and 13:43 ET.
- Only 6 came from the 14:35 slot.
- The daemon started 61 times in 33 days.
- In 7 of 13 journaled 14:35 scans, every candidate was refused as 'per-session budget spent' after a swing card.

Population mismatch: swing scans have no dynamic names, news or shadow evidence, while replay and card_stats model only the 10:35/14:35 instants. The base rate printed on those cards therefore describes a different population.

The 15-minute intraday job cannot produce a native candidate, yet it takes the scan lock every 15 minutes.

**Evidence**

- agentic_trader/cli/commands/service.py:581-593 (budget FULL, next_run_time=datetime.now(UTC))
- config/config.yaml:330 (false alignment comment)
- agentic_trader/config.py:187-190 (ScanBudget FULL/SESSION/NONE only)
- query (read-only psql, signals since 2026-09-18 by NY time): offslot 15 (09:37-13:43), slot 10:35 20, slot 14:35 6
- maps/live.md Q5/Q7/Q16b (7 of 13 14:35 scans budget-spent; 61 daemon starts)
- agentic_trader/research/setups/replay.py:3-6 (replay at suggestion instants only)
- agentic_trader/screeners/strategies.py:343-348 vs agentic_trader/agent/copilot.py:1093-1094 (15m job cannot emit native candidates)
- agentic_trader/config.py:463 (intraday_scan_enabled default True)

**Recommendation**

- Add ScanBudget.JOURNAL: the scan journals its ranking but never records a signal or an outbox intent. Register the swing scan with it, or remove the swing scan.
- Drop next_run_time=now. Readiness can key on the suggestion scans.
- Set scheduler.intraday_scan_enabled to false until a 15m registry alpha exists.
- Cards then come only from the 10:35/14:35 slots and from explicit operator /scan.
- Add a test that a daemon start cannot record a signal.

**North-star impact**

Makes card timing predictable for the operator, roughly doubles 14:35 supply, and makes each card's printed base rate describe the population it came from. The honest-card and card_policy work depends on this.

**References**

_none_

---

## The record printed on cards is a censored, repeat-counted, raw-R, wrong-feed estimate that swung from -0.38R to +0.54R in 13 days

_area: product · kind: defect · impact: high · effort: M · confidence: high_

**Claim**

The daily card_stats snapshot for TREND_PULLBACK LONG went from -0.376R (n_mature 34, 2026-10-08) to +0.535R (n_mature 67, 2026-10-21). Causes in the code:
1. Only mature rows are counted. On a 2R bracket stops resolve faster than targets, so early windows over-weight stops.
2. Each repeat of a setup counts once. The 87 journaled rows are 41 distinct setups.
3. R is raw, not market-excess, and the key ignores timeframe.
4. Outcomes are labelled on the configured IEX feed, while the replay prior uses SIP 'all'.
5. 'Implied EV' mixes gross target/stop R with net timeout R.

Arithmetic: on this bracket (about 35% at +2R and 61% at -1R) the per-setup sd is about 1.4R. With 41 distinct setups the standard error is about 0.22R. Telling +0.04R from -0.11R at 2 standard errors needs about 650 distinct setups, roughly 1.5 years at the live rate.

**Evidence**

- query (read-only psql, card_stats_snapshot keys TREND_PULLBACK LONG): 10-08 n34 -0.376 ... 10-14 n45 +0.126 ... 10-21 n67 +0.535
- agentic_trader/research/setups/card_stats.py:92-94 (mature-only rows)
- docs/card-evidence.md 'Limits' (Censoring bias; Repeated setups)
- maps/live.md Q6 (87 rows = 41 distinct setups)
- agentic_trader/research/setups/card_stats_worker.py:116 (feed = configured feed)
- agentic_trader/config.py:1043 (load_config defaults the feed to iex)
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (SIP all-adjusted caches)
- agentic_trader/execution/card_evidence.py:69-73 (implied_ev mixes gross rates and net timeout R)

**Recommendation**

Rebuild CardKeyStats as an honest estimator before card_policy acts on it:
(a) Use complete cohorts: count only candidates decided at least 20 regular sessions plus a buffer before window_end, so every label has resolved. Alternatively, use Aalen-Johansen cumulative incidence over the open window.
(b) De-duplicate to one row per (contract, strategy, timeframe, direction, session).
(c) Report excess R, shrunk toward the replay prior with a normal-normal posterior:
   - prior mean -0.11R;
   - prior sd from the replay's per-session dispersion;
   - likelihood se about 1.4/sqrt(n).
   Print the posterior mean and its 90% interval on the card.
(d) Compute implied EV from net r_cost consistently.
(e) Label on delayed SIP (end <= now - 16 min), as the PEAD path already does, so live and replay share a feed.

card_policy enforce should read the posterior, never the raw window mean.

**North-star impact**

The operator sees a stable base rate that agrees with 8,768 replayed setups, instead of one that changes sign within two weeks. That makes test (1) of the agreed definition ('states its base rate honestly') true and makes card_policy safe to enforce.

**References**

- Aalen, O. & Johansen, S. (1978), 'An Empirical Transition Matrix for Non-homogeneous Markov Chains Based on Censored Observations', Scandinavian Journal of Statistics 5 — Cumulative-incidence estimator for competing stop/target/timeout outcomes under censoring.
- lifelines (Python survival analysis library), AalenJohansenFitter — Off-the-shelf implementation of the competing-risks estimator. (https://lifelines.readthedocs.io)
- Efron, B. & Morris, C. (1975), 'Data Analysis Using Stein's Estimator and its Generalizations', JASA 70 — Shrinking small live samples toward a well-measured prior: the replay base rate.

---

## The a priori event design is the only one that has produced a deployable result: generalise its route to cards and feed it data already on disk

_area: alpha_mining · kind: research_direction · impact: high · effort: M · confidence: medium_

**Claim**

Now:
- The only pass among roughly 25 frozen studies and 64 pooled pairs is the PEAD a priori entry: one hypothesis, entered once per event, against an event-matched control.
- exdiv used the same design and failed only on its placebo, leaving a yield gradient.
- The pooled lane encodes events as daily top-decile baskets with no hold-skipping, compared against all eligible names. Its pead_* nulls therefore neither replicate nor contradict PEAD.
- The live route exists only for PEAD, hard-coded into run_scan through PEAD_STRATEGY_ID.
- The SEC Form 4 acquisition is already digest-pinned on disk. The data assessment ranked insider trades and 8-K earnings timestamps as the next sources, but they were built as pooled decile fields instead of event entries.

**Evidence**

- docs/apriori-pead-2026-09-26.md 'Criteria' (one trial per leg, every-reporter control)
- docs/apriori-exdiv-2026-10-12.md (P2 placebo failure; yield gradient)
- agentic_trader/research/pooled/formula.py:195-215 (daily decile, no hold-skipping)
- maps/pooled.md gap 5 (pooled PEAD encoding weaker than the a priori design)
- agentic_trader/agent/copilot.py:897-948, 1138-1170, 1273-1279 (PEAD special cases)
- docs/alpha-roadmap.md:494 (L3: 'PEAD special cases leave run_scan', still Planned)
- docs/alpha-data-augmentation-2026-10-18.md:43-44 (insider second, 8-K timestamps third)
- docs/alpha-pooled-mining-research-2026-09-28.md:121 ('the a priori catalog is the higher-yield source of daily suggestions')

**Recommendation**

(1) Extract a CatalogCandidateSource protocol: entry file and SHA, an event loader at a decision time, the frozen bracket plus a session_count exit, its own per-session budget, and the probe cap. Port PEAD onto it with a parity test showing no behaviour change.

(2) Freeze these one-trial a priori entries, in order, each with an event-matched control:
(a) Opportunistic insider cluster purchases on the existing Form 4 cache: non-routine officer/director open-market buys, at least 2 insiders within 30 days, entry at the next 10:35 after the filing date, 20- and 60-session holds declared. Freeze it before any pooled insider campaign reads the data.
(b) PEAD v3 on EDGAR 8-K item 2.02 acceptance timestamps, entering D+1 instead of D+2. This removes the 'report time unknown' caveat.
(c) A yield-conditioned exdiv v2, declared as a second look.

A pass then reaches Telegram through the probe lane in days, not through a bespoke Part 2 spec.

**North-star impact**

Creates a pipeline of discrete, explainable cards (for example, 'CFO and two directors bought on Tuesday'), each with a measured edge over an event-matched control. This is what keeps 1-2 good cards per session coming when natives are withheld and PEAD is out of season.

**References**

- Cohen, L., Malloy, C. & Pomorski, L. (2012), 'Decoding Inside Information', Journal of Finance 67(3) — Opportunistic (non-routine) insider trades predict returns and routine ones do not: a natural one-trial event definition.
- Lakonishok, J. & Lee, I. (2001), 'Are Insider Trades Informative?', Review of Financial Studies 14(1) — Insider purchases, especially clustered ones, predict 6-12 month returns; sets the holding horizon to declare.
- Chen, A. & Zimmermann, T., Open Source Asset Pricing — Published signal definitions and t-stats, usable as priors for the ex-ante yield gate and for event definitions. (https://www.openassetpricing.com)

---

## Native cards trade an execution policy research measured as adverse, and not the policy their base rate labels

_area: execution · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

Now:
- The native tap builds an OrderRequest with no time_in_force, so the GTC default applies.
- Lifetimes act only on signals whose alpha_policy carries a lifetime. Unfilled native entries therefore rest indefinitely and hold reservations in an 8-slot, $2,000 stop-risk book.
- GTC limit entries were measured to fill mainly when the forecast is wrong.
- The trailing ratchet is enabled and trails at current - max(R, 1.5R), not by ATR. The configured mode chandelier_atr is never read.
- The bracket counterfactual measured that ratchet as worse than the fixed 2R bracket by 0.013R per setup, with the 90% CI entirely below zero.
- The card's base rate assumes an always-filling next-hour-open entry with a 20-session timeout. Live trades have no timeout.
- Telegram carried 62 ratchet notices, 29 of them for XOM.

**Evidence**

- agentic_trader/agent/copilot.py:2967-2978 (OrderRequest without time_in_force)
- agentic_trader/broker/base.py:98 (time_in_force default GTC)
- agentic_trader/execution/lifetimes.py:209-213 (only alpha_policy.lifetime signals)
- docs/alpha-roadmap.md:150-152 (GTC fills mainly when the forecast is wrong)
- config/config.yaml:419-425 (trailing_stop enabled, mode chandelier_atr)
- agentic_trader/agent/copilot.py:2569-2570 (trail = current - max(risk, risk*1.5))
- docs/bracket-counterfactual-2026-10-18.md 'What this says' item 2 (A2 -0.013R, CI below zero)
- agentic_trader/research/setups/labels.py:180-187 (label entry at the first regular bar at or after the decision)
- maps/live.md Q9 (62 trailing-stop notices, 29 for XOM)

**Recommendation**

Make one operator-approved construction change that cites the existing counterfactual:
1. Set time_in_force=DAY on native cards, or attach daily_entry_lifetime() (57,600 s), so unfilled entries cancel after one session.
2. Set trailing_stop.enabled to false for native cards, and delete the misdescribing mode and ATR keys.
3. Attach a SessionLifetimePolicy 20-session time exit, so the live trade matches the label printed on the card.
4. Add an operator 'cancel resting entry' command that reuses the existing entry_cancel work item.

Then add the label R of filled versus unfilled sent cards to cards outcomes, to measure the adverse-selection gap directly.

**North-star impact**

Gains about +0.013R on affected setups, avoids adverse fills and frees capacity for the next good card. The base rate on the card then describes the trade the operator actually gets, and dozens of ratchet notices per month leave Telegram.

**References**

- Handa, P. & Schwartz, R. (1996), 'Limit Order Trading', Journal of Finance 51(5) — Resting limit orders fill disproportionately when the price moves against the forecast (adverse selection).
- Kaminski, K. & Lo, A. (2014), 'When Do Stop-Loss Rules Stop Losses?', Journal of Financial Markets 18 — Stop and trail rules add value only under return persistence; consistent with the measured ratchet cost.

---

## There is no north-star scorecard, and two denominators of the funnel are invisible

_area: product · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

Now:
- Prometheus covers process and transport health only, with no funnel counts.
- Scans with zero admitted candidates write no journal event (the journal requires native_count).
- Collect-phase risk rejections exist only in logs.
- 12 of the 39 scheduled 10:35 slots from 2026-09-24 to 2026-10-21 have no scan_candidates_ranked event.
- Execution feedback is limited to mean fill_slip_r and tap age.
- Nothing tracks the calibration of the printed base rate, the selection edge against a random same-session choice, the book's beta or the operator's tap rate.

The program therefore has no weekly signal of whether it is moving toward 'steady, high-quality cards'.

**Evidence**

- maps/architecture.md section 2.5 (no funnel metrics)
- agentic_trader/agent/copilot.py:1591 (journal condition requires native_count)
- agentic_trader/agent/copilot.py:1754-1766 (collect rejections only logged)
- maps/live.md Q5 (12 of 39 slots without an event)
- agentic_trader/research/setups/execution_evidence.py:264-280 (summarize_execution: counts, fill_slip_r, tap age)
- agentic_trader/storage/workflow.py:604-614 (arrival evidence journaled in entry_submitting but unused)

**Recommendation**

Journal every scheduled scan, including empty and gated ones (lockout, VIX failure), with per-rule rejection counts. Then publish a weekly scorecard from the journal, both as a Telegram digest and in cards outcomes:
- Supply: % of scheduled slots journaled (target 100%); cards per slot by source; % of sessions with at least one card.
- Quality: de-duplicated, beta-adjusted excess R per card by source, shrunk, with a 90% interval; selection edge versus a random eligible candidate in the same scan.
- Honesty: reliability of the printed target rate (Brier score and binned calibration); printed versus realised EV.
- Allocation: book beta-dollars; maximum rho of a new card to the book; share of cards changed by book sizing or capacity.
- Operator loop: tap rate; time to tap; fill rate; label R of filled versus unfilled cards; implementation shortfall against the journaled arrival quote.
- Noise: ops notices per card.
- Research: share of PRs that changed a card source or selection.

**North-star impact**

Turns 'steady stream of high-quality cards' into a measured objective with denominators. Each research, policy or cap decision is then judged by whether it moved these numbers, the convergence mechanism the program lacks today.

**References**

- Brier, G. (1950), 'Verification of Forecasts Expressed in Terms of Probability', Monthly Weather Review 78 — Scoring rule and reliability check for the target-rate probability printed on cards.
- Perold, A. (1988), 'The Implementation Shortfall: Paper versus Reality', Journal of Portfolio Management 14(3) — Decomposes card-to-fill cost (delay, execution, opportunity) from evidence the journal already holds.

---

## The operator loop is broken: cards are buried under ops traffic, and the operator's judgment is never captured

_area: product · kind: gap · impact: medium · effort: S · confidence: medium_

**Claim**

Since 2026-09-14:
- 42 cards were sent. 34 expired, 3 closed (2 losses, 1 win), 1 is open and 2 failed for system reasons.
- None of the 6 cards from the 14:35 slot was tapped.
- Telegram carried 659 incident notifications, against 140 notifications in the signal family (cards, expiry and ratchet notices).

A skipped card leaves no reason, and the tap is binary. cards outcomes therefore cannot tell 'the operator disagreed' from 'the operator never saw it', even though operator action is the definition of a useful card.

**Evidence**

- query (read-only psql, signals status since 2026-09-14): EXPIRED 34, CLOSED_LOSS 2, CLOSED_WIN 1, EXECUTED 1, FAILED 2, PENDING 2
- query (read-only psql, work_items kind notification by dedup prefix): incident 659, signal 140, scan-digest 19
- maps/live.md Q3/Q10 (9 taps; 0 of 6 cards from the 14:35 slot tapped)
- maps/live.md section 6 item 9

**Recommendation**

1. With the operator's approval, route incident and ops notices to a separate bot or chat; the outbox and dedup are unchanged. The cards chat carries only cards, the daily digest and the weekly scorecard.
2. Add a 'Pass' button with 3-4 reason codes (late, already exposed, don't like the name or setup, size), journaled as card_passed.
3. Report time-to-tap and tap rate by slot and source, and compare the label R of tapped versus untapped cards. That comparison is the operator's own selection edge.
4. Ask the operator when they can actually act, and align the slot times to it.

**North-star impact**

The north star is cards the operator acts on. This makes the human stage measurable, shows why cards are skipped (a direct test of 'reasonable'), and stops good cards from being lost among hundreds of incident messages.

**References**

_none_

---

## Allocation 'based on existing positions' is nominal: sector-label groups let index clones through, and a fixed notional cap dominates every de-risking lever

_area: sizing_portfolio · kind: gap · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- The per-group card cap uses configured sector labels; dynamic names get only a 'dynamic' group. On 2026-10-20 SPY was blocked twice by the group cap, yet VOO, a dynamic name, was carded at 14:38 after VTI at 10:39.
- The trade notional ceiling is min($7,500, headroom) and is not multiplied by the drawdown and macro factors. For any stop tighter than 3.33% of price, a 0.5 drawdown or macro factor leaves size unchanged.
- No rule and no card line measures beta-dollars.
- Book sizing in enforce has produced only factors of 1.00. The 0.8%/day budget is barely reachable under the $40k equity class cap.
- At query time the book held one open position.

**Evidence**

- agentic_trader/agent/copilot.py:604-617 (group cap = sector labels plus 'dynamic')
- maps/live.md Q3/Q7 (SPY blocked, VOO carded after VTI on 2026-10-20)
- agentic_trader/agent/position_sizing.py:92-96 (ceiling not scaled by effective_adjustment)
- config/config.yaml:337,343 ($250 base risk, $7,500 cap)
- maps/risk.md G1 (read-only sizing probe: 0.5 factor leaves 75 shares / $7,500)
- agentic_trader/risk/rules.py:303-348 (gross notional and label counts only)
- docs/card-evidence.md:232 (six factors, all 1.00)
- agentic_trader/config.py:279,393 (0.8% budget, $40k class cap)

**Recommendation**

Do the cheap items, in this order:
1. De-duplicate cards by return correlation, using the Ledoit-Wolf covariance BookSizer already builds each scan. Refuse a card with rho > 0.8 to any card sent this session or to any open position, and treat broad-index ETFs (SPY, VOO, VTI, IVV) as one group.
2. Add a card line showing the book's SPY-equivalent dollars before and after the card, using per-name beta over 120 split-adjusted sessions. Add a net-beta cap in preview.
3. Apply drawdown x macro to the notional ceiling (one line at position_sizing.py:96), with a test that a 0.5 drawdown factor shrinks every tier.
4. Estimate the covariance on split-adjusted bars.

Defer optimiser work until some source has a positive measured mu.

**North-star impact**

The two cards per session become two distinct bets instead of SPY twice. Each card states what it does to the existing book, which is the allocation decision the north star asks for, and drawdown de-risking actually starts to bind.

**References**

- Ledoit, O. & Wolf, M. (2004), 'A Well-Conditioned Estimator for Large-Dimensional Covariance Matrices', Journal of Multivariate Analysis 88 — The estimator already in use; reuse it for correlation de-duplication and beta.
- Maillard, S., Roncalli, T. & Teiletche, J. (2010), 'The Properties of Equally Weighted Risk Contribution Portfolios', Journal of Portfolio Management 36(4) — Marginal risk-contribution logic for an order-independent per-card cap once the book is non-trivial.

---

## No typed selector or forecast seam exists, so no research score can rank or size a card without editing run_scan

_area: architecture · kind: gap · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- Ranking is hard-wired to setup_quality, measured as uninformative (development rho +0.016, holdout rho -0.012).
- ScreenerCandidate has no forecast, horizon or probability field.
- The L3 card-selector seam is still Planned.
- The registry accepts only single-symbol, raw-bar, time-series-z definitions.
- The shadow optimiser pins every weight, so it carries no allocation information.

A pooled ranker, the vol_20/sector_rel_mom_60 composite or a meta-label model therefore has nowhere to plug in.

**Evidence**

- agentic_trader/agent/copilot.py:1174-1177 (sort by -setup_quality)
- docs/setup-outcomes-2026-09-23.md 'Hypotheses' H1 and 'Selection (H3)'
- agentic_trader/screeners/base.py:23-52 (no forecast fields)
- docs/alpha-roadmap.md:494 (L3 Planned)
- maps/alpha_pipeline.md G1 (promotion.py:88; strategy.py:144-161)
- agentic_trader/execution/book_sizing.py:434-445 (lower bounds = upper bounds = pinned)

**Recommendation**

Add a typed CardCandidate with:
- source id and horizon;
- expected excess R and its se, taken from the shrunk evidence in the base-rate finding;
- p_target.

Inject a Selector protocol that returns a typed Selection with a refusal reason for each candidate. The journal's ranking_key becomes the selector id.

Ship four selectors in shadow, scored by the existing selection block on excess R:
- setup_quality;
- random;
- a rank composite of vol_20 and sector_rel_mom_60;
- a tie-break on diversification (-delta vol).

Later, add a meta-label P(target before stop) trained on the replayed setups. A versioned cross-sectional RankerDefinition then becomes the deployment unit for pooled-lane formulas, enrolled through the probe lane.

**North-star impact**

Selection within one source has small upside (rho about 0.03-0.05). The seam's value is that every future research result becomes a config swap measured on the live card stream, instead of a new Part 2 project per lane.

**References**

- Lopez de Prado, M. (2018), Advances in Financial Machine Learning, ch. 3 (meta-labeling) — A secondary classifier sizes or filters a primary signal: the right framing for ranking native and event candidates.
- QuantConnect LEAN Algorithm Framework (Insight: direction, period, magnitude, confidence) — Open-source template for a typed signal contract between alpha, portfolio construction, execution and risk stages. (https://github.com/QuantConnect/Lean)

---

## The LLM gate selects nothing, adds latency and fails open with raw exception text on the card

_area: signals · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

Now:
- The system prompt tells the model to reject candidates that violate configured gates, but the deterministic code enforces those gates before the model is consulted.
- The acompletion call has no deadline.
- Any exception approves the card deterministically and writes the raw exception text into the thesis on the Telegram card.

Live and eval evidence:
- 14 of 14 journaled LLM evaluations approved, with 0 llm_vetoed events.
- 12 of 14 bracket edits moved by 0.012R or less. The two large tightenings (SPY, CEG) are ignored by card_stats.
- The September eval measured 9-26 s and $0.018-0.035 per call.

**Evidence**

- agentic_trader/agent/prompts.py:24-26 ('Reject a candidate that violates a configured gate')
- agentic_trader/agent/evaluator.py:751-760 (no timeout)
- agentic_trader/agent/evaluator.py:886-917 (except: approved=True)
- agentic_trader/agent/evaluator.py:908 (exception text in thesis_summary)
- maps/live.md Q7/Q16 (14/14 approved; bracket edits)
- docs/llm-prompt-eval-2026-09-22.md 'Part 2 - replay harness'
- docs/card-evidence.md 'Limits' (Proxy bracket)

**Recommendation**

- Make the LLM commentary-only for native cards, as it already is for catalog probes (evaluator.py:858-870).
- Wrap the call in asyncio.wait_for with a deadline like the news scorer's.
- Record a fallback as llm_verdict={'error': TypeName} with its own outcome and a readiness counter, and never put exception text on a card.
- Point the LLM where it could add information: a frozen a priori study of Benzinga headline tone against next-days excess return, on the already cached news history.

**North-star impact**

Cards arrive faster and are never silently approved on an LLM failure. The commentary stays as card colour, and the LLM's one plausible information source (news tone) gets a proper test instead of a review of indicators the gates already checked.

**References**

- Lopez-Lira, A. & Tang, Y. (2023), 'Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models' — LLM headline scoring predicts next-day returns; the right frozen hypothesis for the desk's cached Benzinga data. (https://arxiv.org/abs/2304.07619)

---

## Stop investing research in the native technical setups beyond honesty and parity fixes

_area: signals · kind: stop_doing · impact: medium · effort: S · confidence: medium_

**Claim**

Five predeclared studies have closed the native source:
- setup_quality is uninformative, and the frozen ranker did worse in the holdout.
- Native shorts lose -0.33R per setup.
- Recurrence: no_change.
- No exit arm rescues the entries.
- Excess over SPY is about -0.10R in both 2017-2021 and 2021-2026.

The live signal also has unfixed semantic defects:
- The pullback trigger fires on a falling RSI: 'min(prev two) < 45 and current >= 40' with dip 45 above recovery 40.
- The quality heuristic scores a falling RSI as recovery via abs().
- 4h bars are a UTC-anchored resample of IEX hourly bars that include extended-hours buckets, while the replay prior uses SIP bars spanning 04:00-19:00.
- The buckets shift when DST ends on 2026-11-01.

**Evidence**

- docs/setup-outcomes-2026-09-23.md (summary; H1; Selection H3)
- docs/setup-baserates-short-2026-09-23.md
- docs/setup-recurrence-2026-10-14.md
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' and 'What this says'
- agentic_trader/screeners/strategies.py:146-149 and config/config.yaml:311-312 (dip 45 > recovery 40)
- agentic_trader/screeners/strategies.py:42 (abs recovery term)
- agentic_trader/data/market_data.py:119-138 (df.resample('4h') on a UTC index)
- maps/data.md G1 (measured IEX 9 vs SIP 16 hourly bars for SPY)

**Recommendation**

Freeze native strategy research: no new construction, ranking or recurrence studies on the native setups.

While natives stay in preview, do only the work that keeps their printed base rate honest: label card_stats on the same feed and bars as the replay prior.

If, after card_policy preview, the operator keeps natives as a card source, ship one native-v2 with a fresh frozen replay base rate before relying on post-11-01 data. It would use session-anchored RTH 1h/4h bars on delayed SIP and an RSI rule that requires recovery. Otherwise, stop sending TREND_PULLBACK cards and keep it journal-only as a control population.

**North-star impact**

Stops spending research cycles on a source with a robust negative excess and redirects them to event sources. Any native card still shown loses its known artifacts.

**References**

- Barber, B. & Odean, T. (2008), 'All That Glitters: The Effect of Attention and News on the Buying Behavior of Individual and Institutional Investors', Review of Financial Studies 21(2) — Attention-driven buying of movers predicts poor subsequent returns, relevant to the unmeasured dynamic-name natives.

---

## Mining cadence went to zero while daemon research workers produce only invalid records: replace them with a prospective scorer

_area: alpha_mining · kind: improvement · impact: medium · effort: M · confidence: medium_

**Claim**

Mining cadence now:
- The weekly miner was retired and nothing scheduled replaced it.
- Frozen fixed sets are scored once and never tracked forward.

The daemon's research workers produce no usable evidence:
- All 10,359 shadow forecasts are invalid: 6,915 deployment_data_contract_mismatch and 3,444 stale_closed_bar.
- 3,559 session decisions are unavailable because of IEX minute gaps, and none has ever scored.
- The first daily-panel outcome was voided because one of 64 labels was missing, which NaNs every arm's IC.

The prospective vol_20/sector_rel_mom_60 read covers about 150-200 candidates, against the roughly 2,600 needed to detect rho of about 0.05.

**Evidence**

- scripts/launchd.sh:21 (MINER_RETIRED_NOTICE)
- agentic_trader/research/alpha/clock.py:47-50,76-79 (stale_closed_bar; deployment_data_contract_mismatch)
- maps/live.md Q13 (10,359 invalid forecasts; 3,559 unavailable session decisions)
- agentic_trader/research/alpha/daily_forecasts.py:550-551 (any missing label NaNs the scores)
- docs/alpha-roadmap.md:296-303 (prospective-only window 10-21 to 11-04)
- maps/history.md gap 5 (power arithmetic)
- docs/setup-baserates-short-2026-09-23.md 'Scale' (11,480 setups on 2017-2021)
- agentic_trader/cli/commands/service.py:693-700 (daily panel worker pattern)

**Recommendation**

1. Retire the sinks:
   - demote, or re-declare, the 16 shadow alphas whose data contract cannot match the desk feed;
   - disable the IEX session-decision worker, whose version-3 controls can never qualify;
   - give the daily panel a predeclared missing-label rule: score when at most k names are missing, with reason codes.
2. Add a nightly ProspectiveFormulaWorker, modelled on run_daily_panel_worker and run outside scan windows:
   - score every frozen fixed-set formula at D-1, plus one predeclared composite with literature-given signs once cs_rank/cs_zscore exist;
   - journal the decile and top-2 picks with the every-eligible control, labelled by label_bracket;
   - evaluate the paired edge with an always-valid confidence sequence under a predeclared spending rule, so daily peeks are legitimate.
3. Replace the underpowered live vol_20 read with a declared second use of the 2017-2021 replay.

**North-star impact**

Gives mining the 'reasonable cadence' the operator asked for without spending the single historical confirmation window. The daemon stops writing tens of thousands of unusable records, and near-misses like shorts_retreat get a genuine out-of-sample path to the probe lane. Candidly, it detects only about 0.10R within two years, so it is a slow source, not a near-term card source.

**References**

- Howard, S., Ramdas, A., McAuliffe, J. & Sekhon, J. (2021), 'Time-uniform, nonparametric, nonasymptotic confidence sequences', Annals of Statistics 49(2) — Anytime-valid inference for a worker that checks the same prospective edge every night. (https://arxiv.org/abs/1810.08240)
- Microsoft Qlib (point-in-time data provider and expression engine) — Template for a nightly incremental store and vectorised formula scoring at D-1. (https://github.com/microsoft/qlib)


# Lens: portfolio (12 findings)

## Card evidence judges cards against a beta-one SPY control; the native stream is ~0.9-beta index exposure with near-zero alpha, and much of the '-0.11R vs SPY' gap is beta below 1

_area: research_methodology · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

NOW: `market_r` scales SPY's same-window return by entry/risk with an implicit beta of 1 (research/setups/outcomes.py:187-219, docstring 'a beta-one exposure control'), and `excess_r = r_cost - market` (outcomes.py:383). The bracket counterfactual's verdict 'every arm trails SPY by 0.10-0.15R ... that is an entry problem' rests on this (docs/bracket-counterfactual-2026-10-18.md 'What this says' item 3). `card_stats` has no excess field at all (research/setups/card_stats.py:43-62), and `card_policy` gates on raw `mean_r_cost` (execution/card_policy.py:43-54).

I re-analysed the frozen A0_live frames read-only: 8,768 setups for 2021-06 to 2026-01 and 8,119 for 2017-01 to 2021-04, winsorised at +-10R as the doc does.
- Realised beta to SPY rises monotonically with stop width. By stop-width quintile it is 0.39 / 0.65 / 0.77 / 0.92 / 1.21 in 2021-26 and 0.43 / 0.78 / 0.94 / 0.93 / 1.36 in 2017-21. The tightest quintile is 21-24% rates, credit and metals ETFs (SHY, BIL, AGG, TLT, GLD and so on).
- Beta-adjusted excess in R is -0.062R against the reported -0.109R for 2021-26. For 2017-21 it is -0.034R setup-weighted or -0.094R session-weighted, against -0.111R.
- In equal-notional return terms, which is how the desk actually sizes (see the sizing finding), pooled beta is 0.93 and alpha is -8.0 bp per setup for 2021-26. For 2017-21 alpha is +1.7 bp, and beta-one excess is +5.2 bp, not negative.
- The native source therefore still shows no positive alpha. But roughly 15-70% of the advertised deficit, depending on window and weighting, is beta below 1 on low-volatility, tight-stop names, amplified by R-weighting.

**Evidence**

- agentic_trader/research/setups/outcomes.py:187-219
- agentic_trader/research/setups/outcomes.py:383
- agentic_trader/research/setups/card_stats.py:43-62
- agentic_trader/execution/card_policy.py:43-54
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' and 'What this says' item 3
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_frames_w.py, pm_quintile_beta.py, pm_beta_adj_R.py over ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/{primary,robustness}.csv.gz (A0_live arm)

**Recommendation**

Ship soon (S):
- Journal a pre-entry beta on every ranked candidate: 120 completed sessions on split-adjusted daily bars, Vasicek-shrunk toward the cross-sectional mean.
- Compute `alpha_r = r_cost - beta*market_r` and `alpha_bp` in `cards outcomes`, and add both to `CardKeyStats`.
- Make `alpha_r` the statistic `card_policy` uses when it moves to preview. Do NOT adopt a beta-one 'excess R' for the policy: it would withhold low-beta cards (bonds, gold, staples) in rising tapes simply for being low-beta.
- Restate the roadmap conclusion as 'native setups ~ 0.9-beta exposure with alpha ~ 0 (-8 to +2 bp per trade)'. That frames the real portfolio question: whether the desk wants its market beta delivered through cards at all.

Research: re-run the bracket counterfactual's B1 on beta-adjusted alpha as a declared second look at the frames already read.

**North-star impact**

The only outcome-derived gate, and the measured line on every card, currently judge cards partly by their beta. Separating beta from alpha is a prerequisite for a beta budget, conviction sizing and an honest allocation line on the card.

**References**

- Vasicek, O. (1973). A note on using cross-sectional information in Bayesian estimation of security betas. Journal of Finance 28(5). — Standard shrinkage for per-name beta estimated on short windows.
- Frazzini, A. & Pedersen, L. H. (2014). Betting against beta. Journal of Financial Economics 111(1). — Low-beta assets lag a beta-one benchmark in rising markets but not on a risk-adjusted basis, which is the artefact seen in the tight-stop quintile.
- Kenneth R. French Data Library (daily market and industry factors). — Free daily factor returns for multi-factor attribution beyond SPY. (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

---

## R is not the live sizing unit: the $7,500 notional cap binds on 84% of setups, making drawdown and macro de-risking largely inert, and the proposed 'equal-R' fix would make dollar results worse

_area: sizing_portfolio · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

NOW: `calculate_dynamic_sizing` caps every tier at min($7,500, remaining portfolio notional) (agent/position_sizing.py:95-117). It applies drawdown x macro only to the base risk budget (:92, :161), never to that notional ceiling.

On the replayed stop distribution (median stop 1.80% of price; 10th-90th percentile 0.88-4.06%):
- 84% of setups have a notional-capped base tier (stop below 250/7,500 = 3.33%), and 99.9% a notional-capped max tier.
- Median live risk per card is $135, not the configured $250 (config/config.yaml:337, :343).
- For those cards the tiers collapse to Half plus 'Standard / Max' (position_sizing.py:219-221).
- At factor 0.5 (HIGH macro stress, or about 4.5% drawdown) base notional falls 17% and the max tier 0%. At 0.25, the max tier falls 4%. This contradicts 'applies to every sizing tier' in docs/account-ledger.md:132.

The caps also disagree with each other. 8 positions x $7.5k = $60k (config.yaml:4-6), but the equity class cap is the $40k code default (config.py:393, not set in config.yaml). The sixth full-size card is therefore refused by `asset_class_notional` (risk/rules.py:315-329), not shrunk.

The 'equal stop-risk, raise the notional cap' option in the portfolio map would lever the tight-stop, low-beta quintile. Mean notional would rise to $19.2k. Dollar excess against same-notional SPY per setup would be -$27.3 under equal-R, against -$7.5 under the live caps and -$7.3 under pure equal-notional (2021-26). For 2017-21 the figures are -$27.8, +$0.6 and +$3.9.

**Evidence**

- agentic_trader/agent/position_sizing.py:92-117
- agentic_trader/agent/position_sizing.py:153-166
- agentic_trader/agent/position_sizing.py:219-221
- agentic_trader/risk/capital.py:45-58
- config/config.yaml:3-8
- config/config.yaml:337-343
- agentic_trader/config.py:393
- agentic_trader/risk/rules.py:315-329
- docs/account-ledger.md:132
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_frames.py, pm_stop_quintile.py, pm_haircut.py on the frozen bracket-counterfactual frames

**Recommendation**

Ship soon (S, behind a mode flag with preview):
1. Make volatility-normalised notional the primitive: `notional_i = min(k / sigma_i, cap)`. Take sigma_i from the blended EWMA / 120-session daily vol on split-adjusted bars. Choose k (for example about $120 of daily vol per card) so median notional stays near today's $7k, which keeps the empirically less-bad near-equal-notional profile while equalising risk contributions.
2. Keep the stop as bracket geometry and as a per-trade risk ceiling ($250 x dd x macro), so tight stops can never lever a card up.
3. Apply dd x macro to the vol target and the notional ceiling as well.
4. Set `max_equity_exposure` explicitly, consistent with `max_concurrent_positions` x typical notional, or cut positions to 5.
5. Size to the remaining class, stop-risk and vol headroom instead of refusing, and journal the binding constraint.
6. Add tests pinning that a 0.5 drawdown factor halves every tier's notional.

Reject the 'raise the cap to R/min-stop' option.

**North-star impact**

Card sizes become consistent and comparable across names. The drawdown and stress levers actually reduce exposure. Research results become mappable to desk dollars, so 'appropriate risk controls' stops being nominal.

**References**

- Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M. & Van Hemert, O. (2018). The impact of volatility targeting. Journal of Portfolio Management 45(1). — Volatility scaling stabilises risk per position and the tails, at little cost to returns.
- Grossman, S. J. & Zhou, Z. (1993). Optimal investment strategies for controlling drawdowns. Mathematical Finance 3(3). — Drawdown control should scale exposure itself, not only a risk budget that a notional cap overrides.
- Cederburg, S., O'Doherty, M. S., Wang, F. & Yan, X. (2020). On the performance of volatility-managed portfolios. Journal of Financial Economics 138(1). — Treat vol targeting as risk control, not as an alpha claim.

---

## No market or sector exposure model: the book's beta-dollars, sector dollars and correlation to holdings are unmeasured, and label-based groups let the same bet be carded twice

_area: risk · kind: gap · impact: high · effort: M · confidence: high_

**Claim**

NOW:
- No live rule or card line measures beta, sector dollars or idiosyncratic risk. Caps are gross notional (risk/rules.py:303-329).
- The only correlation control counts same-direction names per hand-labelled sector group (risk/rules.py:332-348; config/config.yaml:7). Dynamic names get only the `dynamic` pseudo-group (agent/copilot.py:614-617). On 2026-10-20 VOO was carded after VTI while SPY was blocked (maps/live.md section 6 gap 4, production queries Q3/Q7).
- The statistical correlation check is off and does its own per-pair fetches (agent/evaluator.py:503-523; config.py:397).
- The L3 plan promised bounding 'marginal variance, beta and sector contribution' (docs/alpha-roadmap.md:521-524). Only total vol shipped.
- SPY and all 11 sector SPDRs are already in the static universe (config/config.yaml:96-112), so their daily bars are in every scan's `datasets`.

My out-of-sample check used 1,400 random month-end five-name long books, 2020-2026 SIP daily data and 120-session estimation, with the estimator fitted jointly over 25 names as BookSizer does. The live sklearn Ledoit-Wolf (identity target, research/setups/covariance.py:80-89) is roughly unbiased: geometric-mean realised/predicted variance is 0.92, against 0.87 for the sample covariance and 0.92 for a single-index model. It shrinks mean pairwise correlation from 0.256 to 0.209 against 0.244 realised. But the 90th-percentile ratio is 1.96 for every estimator, so a slow equal-weight window misses regime shifts. That matters more than the estimator family.

**Evidence**

- agentic_trader/risk/rules.py:303-348
- agentic_trader/agent/copilot.py:614-617
- agentic_trader/agent/evaluator.py:503-523
- agentic_trader/config.py:397
- config/config.yaml:7
- config/config.yaml:96-112
- agentic_trader/research/setups/covariance.py:80-89
- agentic_trader/execution/book_sizing.py:201
- docs/alpha-roadmap.md:521-524
- maps/live.md section 6 gap 4 (production DB Q3, Q7)
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_cov_bias.py over ~/agentic-trader-research/setup-cache-v1 (*_1d)

**Recommendation**

Ship soon (M, preview first):
- Add a two-level factor risk model in `risk/` with no new data: `r_i = beta_i*r_SPY + gamma_i*r_sectorETF_resid + e_i` on split-adjusted daily returns, with Vasicek-shrunk betas and `Sigma = B F B' + D`. Estimate F by EWMA (lambda about 0.97) blended with the 120-session estimate and floored at long-run vol.
- Per card and per book, output beta, beta-dollars, sector dollars, idiosyncratic vol, correlation to the book and marginal contribution to risk.
- Add rules `net_beta_dollars` (for example <= 35% of capital) and `sector_dollars` (for example <= 15%) with the house off/preview/enforce rollout.
- Replace label groups for unlabelled or dynamic names with statistical clusters from the same Sigma (rho > 0.8, or hierarchical clustering), so index clones and same-theme movers collide.
- Retire the per-pair yfinance correlation check.

Research: a predeclared beta-hedged card variant, only if the operator wants relative-return cards.

**North-star impact**

Turns 'allocation based on existing positions' into concrete numbers: how much SPY-equivalent and sector risk a card adds. It also stops two session slots going to the same index exposure.

**References**

- Menchero, J., Orr, D. J. & Wang, J. (2011). The Barra US Equity Model (USE4): Methodology Notes. MSCI. — Template for a parsimonious market-plus-industry factor covariance with specific risk.
- Ledoit, O. & Wolf, M. (2003). Improved estimation of the covariance matrix of stock returns with an application to portfolio selection. Journal of Empirical Finance 10(5). — Single-index shrinkage target, appropriate for long-only equity books dominated by the market factor.
- J.P. Morgan/Reuters (1996). RiskMetrics Technical Document, 4th ed. — EWMA volatility for responsiveness to regime shifts (the observed 2x tail misprediction).

---

## No calibrated expected return reaches size or selection, and the card's measured EV is censored, raw and prior-free, so it swung from -0.38R to +0.54R in eight sessions

_area: sizing_portfolio · kind: gap · impact: high · effort: M · confidence: medium_

**Claim**

NOW:
- Cards are ranked by the heuristic `setup_quality` (agent/copilot.py:1174-1177), which is uninformative (holdout rho -0.012; docs/setup-outcomes-2026-09-23.md 'Selection').
- Every card risks the same nominal budget whatever its evidence.
- `card_stats` averages raw `r_cost` over mature rows only (research/setups/card_stats.py:91-92, :112). It has no prior, no de-duplication and no excess.
- `card_policy` thresholds that raw mean and ships off (execution/card_policy.py:43-54; config/config.yaml:454).
- Production snapshots show TREND_PULLBACK long at -0.376R (n 34) on 10-08 and +0.535R (n 67) on 10-21 as slow-resolving targets matured (maps/live.md section 4, production Q1). That is informative censoring on a 2R bracket.
- Against that, the replay gives a stable prior. Beta-adjusted alpha is -0.062R per setup (2021-26), about -10.5 bp for trend pullback 4h and +1.3 bp for squeeze 1h in return space (pm_frames_w.py).

**Evidence**

- agentic_trader/agent/copilot.py:1174-1177
- agentic_trader/research/setups/card_stats.py:91-92
- agentic_trader/research/setups/card_stats.py:112
- agentic_trader/execution/card_policy.py:43-54
- config/config.yaml:454
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)'
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives'
- maps/live.md section 4 table (production DB Q1)

**Recommendation**

Ship soon (M):
- Build a `CardPrior` per (strategy, timeframe, direction, stop-width tercile) from the frozen replay frames: a normal prior on beta-adjusted alpha_R, with its session-block bootstrap SE.
- Update it with de-duplicated live rows (one per setup-session). Use Aalen-Johansen cumulative incidence for target, stop and timeout, so immature rows stop biasing early snapshots.
- Put `card_policy` in preview on `posterior_mean(alpha_R) - cost_R < 0`.
- Show 'expected excess -0.06R +- 0.02 (prior 8.8k replayed, live n 41)' on the card.
- Use the posterior as mu for the shadow allocator.

Later (research): fractional Kelly with parameter-uncertainty shrinkage, `f proportional to max(0, mu - z*se) / E[R^2]`, capped by the per-trade budget. It correctly gives about zero today.

**North-star impact**

Gives 'high quality' an operational meaning. Size and send decisions follow a calibrated, stable estimate instead of a censored live mean that flips sign within a week.

**References**

- Efron, B. & Morris, C. (1975). Data analysis using Stein's estimator and its generalizations. JASA 70(350). — Empirical-Bayes shrinkage of small live samples toward a large replay prior.
- Aalen, O. O. & Johansen, S. (1978). An empirical transition matrix for non-homogeneous Markov chains based on censored observations. Scandinavian Journal of Statistics 5(3). — Competing-risks estimator removing the stop-resolves-first censoring bias in card_stats.
- Baker, R. D. & McHale, I. G. (2013). Optimal betting under parameter uncertainty: improving the Kelly criterion. Decision Analysis 10(3). — Conviction sizing that shrinks to zero when the edge's SE is large.
- Howard, S. R., Ramdas, A., McAuliffe, J. & Sekhon, J. (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. Annals of Statistics 49(2). — Valid daily peeking if card_policy ever enforces on a rolling posterior.

---

## The card does not present an allocation decision: it shows tiers that collapse at the cap and a vol line that always reads x1.00

_area: product · kind: improvement · impact: high · effort: S · confidence: medium_

**Claim**

NOW: The card renders a tier list (notifier/telegram_bot.py:322-345), 'Capital Risk: x% of cash' (:383) and one book line, 'portfolio vol a -> b/day (budget c; size xf)' (`_book_line`, :131-153).

It shows:
- no binding constraint;
- no beta-dollars or sector exposure;
- no correlation to holdings;
- no stress loss;
- no cost in R;
- no prior.

For about 84% of setups the tiers collapse to Half plus 'Standard / Max' at the $7.5k cap (agent/position_sizing.py:219-221), and the default risk shows about $135 instead of the configured $250. In preview, all six book factors were 1.00 (docs/card-evidence.md:232). Operator engagement is low: 9 of 42 cards tapped (maps/live.md section 3 item 5, production Q10).

**Evidence**

- agentic_trader/notifier/telegram_bot.py:131-153
- agentic_trader/notifier/telegram_bot.py:322-345
- agentic_trader/notifier/telegram_bot.py:383
- agentic_trader/agent/position_sizing.py:219-221
- docs/card-evidence.md:232
- maps/live.md section 3 item 5 (production Q10)

**Recommendation**

Ship soon (S, render-only once the sizing and factor-model findings land). Replace the sizing and book lines with one 'Allocation' block, for example:
- 'Size 41 sh = $7,480 (7.5% of capital); risk to stop $135; ex-ante vol $105/day; bound by: per-trade notional cap ($250 risk budget not reached)'
- 'Book after card: 2 -> 3 names; beta$ +$13.9k -> +$20.8k (card beta 0.93); Tech $7.5k -> $15k (cap $15k)'
- 'Adds +$48/day to book vol (0.42% -> 0.58%, target 0.50%); rho to book 0.62 (highest AAPL 0.81)'
- 'Stress SPX -6%: book -$1.3k; stop risk in use $410 / $2,000'
- 'Edge prior: alpha -0.06R +- 0.02 (8.8k replayed; live 41) -> index-like; round-trip cost 0.06R'

Label tiers by what binds, and drop 'Max' when it equals Standard. Journal the same fields so `cards outcomes` can attribute them.

**North-star impact**

This is literally the north star's 'allocation decisions based on existing positions' made visible. Each card says what it adds to the book and why it has the size it has.

**References**

- Grinold, R. C. & Kahn, R. N. (1999). Active Portfolio Management, 2nd ed., chapters on risk decomposition and marginal contribution to risk. — Standard marginal-contribution-to-risk and beta decomposition for presenting a position's portfolio effect.

---

## The book-vol rule is a no-op by construction and sees the wrong book: raw prices, entry notional, unfilled GTC entries and ratcheted winners at full risk

_area: sizing_portfolio · kind: defect · impact: medium · effort: M · confidence: high_

**Claim**

NOW: The budget is 0.8% per day of risk capital, $800 (config/config.yaml:466-467; execution/book_sizing.py:338). My check found that a random full-size five-name ($37.5k) long book has Ledoit-Wolf-predicted daily vol of median $466 and 95th percentile $767. Only 3.7% of 1,400 draws exceed $800 (6.1% realised). The preview journaled six factors, all 1.00 (docs/card-evidence.md:232).

The book it sizes against is also misstated:
- (a) Raw bars (book_sizing.py:222; covariance.py:20-26). A split inside 120 sessions enters as a -50% or -90% return, and an over-budget book gives factor 0 to every non-hedge card (risk/book_vol.py:91-95).
- (b) Weights are recorded notional, not marks (book_sizing.py:126-141).
- (c) Accepted-but-unfilled native entries count as full positions. They are GTC by default (broker/base.py:98; agent/copilot.py:2967-2977 sets no time in force), only alpha-policy lifetimes cancel entries (execution/lifetimes.py:210-212), and reservations include EXECUTED rows (storage/workflow.py:262-269).
- (d) Ratcheted winners keep their original `risk_dollars` (agent/copilot.py:2622 updates only the stop; risk/book.py:95), so `aggregate_stop_risk` (risk/rules.py:270-277) still charges them in full.
- (e) The factor is computed on the default tier only (book_sizing.py:339) and is never rechecked at admission (docs/risk-policy.md:113-114).
- (f) One inner-joined sample covers the book plus every candidate (book_sizing.py:201; covariance.py:43-47).

**Evidence**

- config/config.yaml:466-467
- agentic_trader/execution/book_sizing.py:126-141
- agentic_trader/execution/book_sizing.py:201
- agentic_trader/execution/book_sizing.py:222
- agentic_trader/execution/book_sizing.py:338-339
- agentic_trader/risk/book_vol.py:88-95
- agentic_trader/research/setups/covariance.py:20-26
- agentic_trader/research/setups/covariance.py:43-47
- agentic_trader/broker/base.py:98
- agentic_trader/agent/copilot.py:2967-2977
- agentic_trader/execution/lifetimes.py:210-212
- agentic_trader/storage/workflow.py:262-269
- agentic_trader/agent/copilot.py:2622
- agentic_trader/risk/book.py:95
- docs/card-evidence.md:232
- docs/risk-policy.md:113-114
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_cov_bias.py

**Recommendation**

Ship soon (M). Re-purpose the rule rather than tune it:
- Replace 'total budget only' with a per-card cap on marginal contribution to risk (ERC style: `x_i*(Sigma x)_i / sigma_p <= target / N_target`), plus a book ex-ante vol target that can bind (for example 0.5% per day at full deployment).
- Compute it on split-adjusted bars, and mark weights at the last completed close (or the shared broker snapshot).
- Attach `daily_entry_lifetime()` (DAILY_ENTRY_LIFETIME_SECONDS, execution/lifetime_policy.py:24) to native signals so unfilled entries release capacity after one session.
- Re-price planned risk to `max(0, entry - stop)` after a confirmed ratchet.
- Evaluate the max tier, so every offered tier fits.
- Estimate book-name risk from each name's own history via the factor model, not an inner join.

Until then, label the card line 'informational'.

**North-star impact**

Today, 'book-aware' sizing cannot change a size, and the book it would size against is wrong in several directions. Fixing that makes position-aware allocation real before the book fills.

**References**

- Maillard, S., Roncalli, T. & Teiletche, J. (2010). The properties of equally weighted risk contribution portfolios. Journal of Portfolio Management 36(4). — Risk-contribution budgeting that binds per position and is order-independent.
- Lopez de Prado, M. (2016). Building diversified portfolios that outperform out of sample. Journal of Portfolio Management 42(4). — HRP as a no-forecast diversification alternative for cluster-aware sizing.

---

## No desk-level dollar replay exists, and every research verdict is setup-weighted in R while the desk sizes at near-equal notional

_area: research_methodology · kind: research_direction · impact: high · effort: M · confidence: high_

**Claim**

NOW: Research verdicts are setup-weighted means in R (docs/bracket-counterfactual-2026-10-18.md 'Statistics'; research/setups/card_stats.py:112). R-weighting levers tight stops: R per 1% SPY move spans 0.25-1.13R across the 10th-90th percentile of setups. The verdict depends on the unit. For 2017-21, beta-one excess is -0.103R in R but +5.2 bp per setup in equal-notional return space.

No replay runs the card stream through live sizing, the 1-per-scan and 2-per-session budgets, group caps, book rules and the 8-slot or $40k capacity. L5 'sizing protocol callable from replay' is 'Later' (docs/alpha-roadmap.md:496). My first-order dollar check is per setup, against same-notional SPY over the same window:
- 2021-26: equal-R -$27.3, live caps -$7.5, equal-notional -$7.3.
- 2017-21: -$27.8, +$0.6, +$3.9.

The replay has a median of 6 setups per session competing for 2 cards (pm_frames.py).

**Evidence**

- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (Statistics)
- agentic_trader/research/setups/card_stats.py:112
- docs/alpha-roadmap.md:496
- agentic_trader/research/alpha/persistent_book.py:172-182
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_stop_quintile.py, pm_frames.py

**Recommendation**

Research (M, predeclared, on both frozen frame sets, the already-read windows; descriptive second use):
- Replay the 10:35 and 14:35 instants under the live budgets and book rules, using the ledger identity from persistent_book.py:172-182.
- Arms:
  - sizing: {live caps, equal-R uncapped, vol-normalised k/sigma, vol-normalised plus beta cap};
  - entry lifetime: {GTC, one session};
  - ratchet: {on, off}.
- Outputs: dollar equity curve, Sharpe, max drawdown, net beta, capacity utilisation and refusal counts, against SPY and against a beta-matched SPY sleeve.
- Include the 'core beta sleeve plus card overlay' design: hold the target beta in SPY and judge cards on alpha.
- Pick the sizing primitive from this, not from R statistics.

**North-star impact**

Answers the operator's real question: does the card stream at desk caps beat simply holding the index, in dollars? It also chooses sizing, capacity and lifetime with evidence.

**References**

- Boyd, S. et al. (2017). Multi-period trading via convex optimization. Foundations and Trends in Optimization 3(1); cvxportfolio. — Reference design for a policy simulator that applies the live constraints and costs to a signal stream. (https://www.cvxportfolio.com)

---

## The 'shadow optimiser' pins every weight and yields no allocation evidence; replace it with a free per-scan shadow allocator

_area: sizing_portfolio · kind: improvement · impact: medium · effort: M · confidence: high_

**Claim**

NOW: `_shadow_check` calls `ConvexAlphaPortfolioOptimizer` with lower_bounds == upper_bounds == the pinned weights (execution/book_sizing.py:434-445). It can only re-derive w'Sigma w, adding one solver call per card with no decision content.

The research allocator supports factor-exposure, group, turnover and gross constraints and a forecast-uncertainty penalty (research/alpha/optimizer.py:45-82). But its only entry point is `alpha portfolio SNAPSHOT.json` (cli/commands/alpha.py:678-731). `build_shadow_portfolio` refuses any pending reservation (research/alpha/portfolio.py:105-106) and uses a separate risk model, a fixed 25% diagonal shrink (portfolio.py:167-170). Calibrated forecasts exist only for qualified registry versions, of which none are active (research/alpha/shadow.py:96-120; docs/alpha-roadmap.md:637).

**Evidence**

- agentic_trader/execution/book_sizing.py:434-445
- agentic_trader/research/alpha/optimizer.py:45-82
- agentic_trader/research/alpha/portfolio.py:105-106
- agentic_trader/research/alpha/portfolio.py:167-170
- agentic_trader/cli/commands/alpha.py:678-731
- agentic_trader/research/alpha/shadow.py:96-120

**Recommendation**

Ship soon (M, journal-only; shadow weights never reach orders):
- Stop the pinned solve.
- Per scan, solve over {book pinned at marks} plus {approved candidates in [0, max-tier notional]}.
- Set mu_i = posterior alpha_R x risk$_i from the conviction finding, alpha only, with beta handled as a constraint. Use Sigma from the factor model, scaled to the median 5.6-session hold (state the iid assumption).
- Constraints: the existing caps as linear constraints, plus a beta band and sector caps, plus kappa x se for uncertainty.
- Journal the suggested notional, the binding constraints and the duals. Add an 'allocator' arm to `cards outcomes` against the actual `setup_quality` choice.
- With alpha ~ 0 the honest output is 'size ~ 0 unless diversifying'. That is informative and agrees with `card_policy`.
- Use one risk model for live and research.

**North-star impact**

Starts accruing prospective evidence for allocator-set sizes, which is roadmap stage B, without touching orders. It also gives the card selector a principled diversification tie-break.

**References**

- skfolio (scikit-learn-compatible portfolio optimisation) and Riskfolio-Lib. — Independent validators for risk-budgeting and CVaR formulations in tests, not new runtime dependencies. (https://skfolio.org)
- Grinold, R. C. & Kahn, R. N. (1999). Active Portfolio Management, 2nd ed. (alpha = IC x sigma x z). — Bridge from a calibrated score or posterior to mu in return units for the allocator.

---

## Gap, scenario and earnings risk on held positions is unbounded, while the desk's stop-risk budget is read as a loss limit

_area: risk · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

NOW:
- Capacity 'bounds planned stop risk and snapshot exposure, not gap loss or portfolio CVaR' (execution/capacity.py:3; docs/entry-capacity.md:70).
- Aggregate stop risk is 2% of capital (config/config.yaml:3; risk/rules.py:270-277), summed at original risk.
- The earnings blackout runs at the scan and tap only (docs/risk-policy.md:106). The only earnings reference in execution/ is tap freshness (execution/freshness.py:21). Held names ride into reports.
- Native positions have no holding deadline (execution/lifetimes.py:210-212).

With the measured card beta of about 0.93:
- A full $40k equity class loses about $2.2k (2.2% of capital) on a -6% index day, and about $4.5k on a 2020-03-16-style -12% day. Both exceed the 2% stop-risk figure.
- A 10-20% single-name earnings gap on a $7.5k card is $750-1,500, 5-10x the median $135 planned risk.

**Evidence**

- agentic_trader/execution/capacity.py:3
- docs/entry-capacity.md:70
- config/config.yaml:3
- agentic_trader/risk/rules.py:270-277
- docs/risk-policy.md:106
- agentic_trader/execution/freshness.py:21
- agentic_trader/execution/lifetimes.py:210-212
- Read-only analysis (beta 0.93, median risk $135): ~/agentic-trader-research/holistic-review-20261021/scripts/pm_frames_w.py, pm_frames.py

**Recommendation**

Ship soon (S-M, preview then enforce):
- Add a pure `scenario_loss` rule in `risk/`: `sum(notional_i*beta_i*shock) + sum(notional_i*g_i)`, where g_i is a name's 99th-percentile overnight gap over 250 sessions and the shock is a declared worst day. Budget it at, for example, 4% of capital, and journal it per card.
- Add a daily held-position earnings check against the Nasdaq calendar that emits an advisory card ('XYZ reports in 2 sessions: hold, halve or exit'). It acts only on an operator tap, through `PositionCloseService`.
- Give native cards the label's 20-session holding deadline via `SessionLifetimePolicy`, for parity with every outcome label.

Later (research): historical-scenario CVaR on split-adjusted history including 2020-03 and 2022.

**North-star impact**

Makes 'appropriate risk controls' cover the losses that actually dominate a long-beta book (index gaps and earnings), not just planned stops.

**References**

- Rockafellar, R. T. & Uryasev, S. (2000). Optimization of conditional value-at-risk. Journal of Risk 2(3). — CVaR as the tail-loss budget once joint scenarios exist.

---

## Stop running the live R-ratchet on native positions: it measurably costs EV, gives no risk-budget relief and floods Telegram

_area: risk · kind: stop_doing · impact: medium · effort: S · confidence: high_

**Claim**

NOW:
- `trailing_stop.enabled: true` (config/config.yaml:419-425). The code trails at current - 1.5 x initial R once +1.5R is reached (agent/copilot.py:2570), priced from `fetch_latest_price` (:2554), which has no feed pin and no age check (maps/data.md G3). `mode: chandelier_atr` is never read.
- The predeclared counterfactual measured the ratchet arm at -0.013R per setup, 90% CI [-0.020, -0.006], on 8,768 setups (docs/bracket-counterfactual-2026-10-18.md 'Rules' table and 'What this says' item 2).
- Its intended risk benefit is not credited. A confirmed ratchet updates only the stop (agent/copilot.py:2622), so `risk_dollars` and `aggregate_stop_risk` keep charging the original risk (risk/book.py:95; risk/rules.py:270-277).
- Production carried 62 trailing-stop notices (29 for XOM) against 41 signal cards (maps/live.md section 6 gap 9, production Q9).
- Every research label assumes no ratchet.

**Evidence**

- config/config.yaml:419-425
- agentic_trader/agent/copilot.py:2554
- agentic_trader/agent/copilot.py:2570
- agentic_trader/agent/copilot.py:2622
- agentic_trader/risk/book.py:95
- agentic_trader/risk/rules.py:270-277
- docs/bracket-counterfactual-2026-10-18.md 'Rules' and 'What this says' item 2
- maps/live.md section 6 gap 9 (production Q9)

**Recommendation**

Operator decision now (S):
- Disable the ratchet for native (non-alpha) cards. This makes the live policy match the A0 bracket every label assumes. Record it as a declared construction change whose evidence is the existing paired estimate, which already has a tight CI; no new study is needed.
- Keep the 'notice for every confirmed stop change' contract for any remaining trails.
- If a trail is retained for risk reasons, rebuild it as a policy object in `execution/` shared with replay (L5). It should re-price planned risk after a confirmed move and use a fresh, feed-pinned quote.
- Delete the unused `mode` key.

**North-star impact**

Removes a measured EV drag and most stop-change noise from the operator's Telegram, and aligns the traded policy with the measured one.

**References**

- Kaminski, K. M. & Lo, A. W. (2014). When do stop-loss rules stop losses? Journal of Financial Markets 18. — Stop and trail rules add value only under return persistence, which the replay does not show for these setups.

---

## Macro and regime gates are uncalibrated and fail open: curve inversion suppresses the setup that did best during the only inversion in the replay, and a calendar fetch failure renders 'CLEAR'

_area: risk · kind: defect · impact: medium · effort: S · confidence: medium_

**Claim**

NOW:
- An inverted 10Y-2Y or 10Y-3M curve alone forces HIGH stress: risk x0.5, squeeze breakouts suppressed, minimum R:R 2.2 (agent/macro.py:149-150, :255; config.py:483).
- Descriptively, on the 2021-26 frames (an already-read window), squeeze-breakout 1h longs inside the 2022-07-06 to 2024-12-12 inversion span earned +0.224R (+11.6 bp vs SPY, n 579). Outside it they earned -0.057R (-7.4 bp, n 610). Session-mean SEs are 0.081 and 0.072.
- Halving risk shrinks base notional only 17% (see the sizing finding).
- When FRED fails, the VIX-only fallback keeps `risk_multiplier = 1.0` even with VIX above 30 (agent/regime.py:185-189), but the preflight fails closed if VIX itself is missing (:169).
- A ForexFactory failure caches an empty list (agent/calendar.py:216-219), which renders as 'Lockout verified: CLEAR' (:156-160). Earnings says 'Unverified' in the same situation.
- No document tests any regime gate.

**Evidence**

- agentic_trader/agent/macro.py:149-150
- agentic_trader/agent/macro.py:255
- agentic_trader/config.py:483
- agentic_trader/agent/regime.py:169
- agentic_trader/agent/regime.py:185-189
- agentic_trader/agent/calendar.py:156-160
- agentic_trader/agent/calendar.py:216-219
- docs/setup-outcomes-2026-09-23.md 'Base rates' (squeeze 1h long +0.08R)
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_inversion.py

**Recommendation**

Ship soon (S):
- Move the inversion branch to preview: journal `would_suppress` and `would_scale` instead of acting.
- In the VIX-only fallback, map VIX buckets through the compound-stress VIX branch instead of 1.0.
- Render 'UNVERIFIED (calendar fetch failed)' and add the published FOMC schedule as a static fallback.

Research (M): a predeclared replay of the VIX, curve and HY-OAS gates on the frozen setup population with the session-block bootstrap. If any conditioner is kept, test the momentum-crash state (negative trailing market return plus high realised vol) instead of curve inversion.

**North-star impact**

Stops an untested macro heuristic from silently removing the best-measured native setup, and stops the LLM and operator being told 'CLEAR' when the calendar is unknown.

**References**

- Estrella, A. & Mishkin, F. S. (1998). Predicting U.S. recessions: financial variables as leading indicators. Review of Economics and Statistics 80(1). — Curve inversion forecasts recessions 2-6 quarters ahead, not 1-week trade outcomes.
- Daniel, K. & Moskowitz, T. J. (2016). Momentum crashes. Journal of Financial Economics 122(2); Barroso, P. & Santa-Clara, P. (2015). Momentum has its moments. JFE 116(1). — Evidence-based state conditioners (market drawdown plus high volatility) for trend-type entries.

---

## The stop is set on 4h ATR against a week-long hold: tight-stop setups carry the worst alpha, the lowest beta and the highest cost in R, and stop width has never been tested

_area: signals · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

NOW: The stop is max(swing extreme, 1.5 x ATR14) (agent/evaluator.py:208), and for trend pullback the ATR is the 4h ATR (screeners/strategies.py:163). The replayed median stop is 1.80% of price (10th percentile 0.88%) against a 5.6-session mean hold and a 60.8% stop rate (docs/bracket-counterfactual-2026-10-18.md 'Descriptives' A0_live). The bracket study held the stop fixed across arms ('Arms').

Beta-adjusted return-space alpha by stop-width quintile, tight to wide:
- 2021-26: -9.3, -19.7, -4.8, -0.8, 0.0 bp (equities only: -14.9, -19.1, -4.7, -1.8, -1.4).
- 2017-21: -3.7, +3.5, -5.0, +9.9, +22.9 bp.

Round-trip cost in R is 10 bp / stop%, which is 0.11R at the 10th percentile. The tight quintile is 21-24% rates, credit and metals ETFs, whose equity-trend card has no natural SPY benchmark.

**Evidence**

- agentic_trader/agent/evaluator.py:208
- agentic_trader/screeners/strategies.py:163
- config/config.yaml:274-275
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (Arms) and 'Descriptives'
- Read-only analysis: ~/agentic-trader-research/holistic-review-20261021/scripts/pm_quintile_beta.py, pm_stop_quintile.py

**Recommendation**

Research (M, a predeclared construction test on an unread window, or a declared second use of the setup-outcome holdout):
- Stop-floor arms {1.5 x 4h ATR (live), 1 x daily ATR, 1.5 x daily ATR}, risk-equalised with vol-normalised notional, 2R target.
- Decisive statistic: beta-adjusted alpha per dollar, net of cost.
- Separately, predeclare excluding rates, credit and metals ETFs and broad-index clones from the native equity-trend source, or route them to a cross-asset trend card benchmarked to cash rather than SPY.

**North-star impact**

Targets the one untested native construction parameter with a consistent descriptive gradient, and removes cards whose expected alpha against SPY is zero by construction.

**References**

- Kaminski, K. M. & Lo, A. W. (2014). When do stop-loss rules stop losses? Journal of Financial Markets 18. — Stops tighter than the return horizon's noise mostly convert drift into stop-outs.
- Moskowitz, T. J., Ooi, Y. H. & Pedersen, L. H. (2012). Time series momentum. Journal of Financial Economics 104(2). — Cross-asset trend positions (bonds, gold) should be judged against their own asset or cash, not the equity index.


# Lens: data (13 findings)

## Live native scans and every research base rate compute the trigger on different bars, and the live 4h clock changes meaning on 2026-11-01

_area: data · kind: defect · impact: high · effort: M · confidence: high_

**Claim**

Live scans read Alpaca IEX raw bars: load_config defaults the feed to iex unless ALPACA_DATA_FEED is set, and all 3,432 captures on 2026-10-20 are alpaca:iex. They build 4h bars with a plain resample("4h") on a UTC index. The replay behind the setup base rates, the bracket counterfactual and the native −0.110R excess vs SPY uses SIP with adjustment 'all', through the same UTC resample. I measured this read-only on 2026-10-01→20 live evidence against the SIP caches for 2025–2026 sessions. Live IEX gives 9 hourly bars per session for SPY (08:00–16:00 ET) and 7 for XOM, AFL and AMKR (09:00–15:00). The SIP cache gives SPY 16 (04:00–19:00). XOM carries a 04:00 bar on 91% of days and 17:00–19:00 bars on 94–98%; AMKR has pre-market bars on 58–89% of days. For liquid names, replay therefore has about four 4h buckets per session against two live. The 4h EMA20, RSI14 and ATR14 that define TREND_PULLBACK span about 5 sessions in research and about 10 live. replay.py claims identical shapes and admits only the forming-bar gap. card_stats and card_policy label outcomes on the configured IEX feed. When EDT ends on 2026-11-01, the UTC buckets move from 08–12/12–16 ET to 07–11/11–15/15–19 ET. A mid-cap's live 4h series then goes from 2 to 3 bars per session overnight, the last being a single 15:00 bar. Separately, the composite provider silently falls back to yfinance auto-adjusted frames when Alpaca fails, and no live consumer checks attrs['feed']. fetch_latest_price, which sets tap prices and trailing ratchets, has no feed pin and no age check.

**Evidence**

- agentic_trader/config.py:1043-1050 (alpaca_data_feed defaults to 'iex'); config.py:689 (MarketDataConfig default 'sip')
- agentic_trader/data/market_data.py:119-138 (resample_to_4h on UTC index); :197-239 (fetch_data)
- agentic_trader/research/setups/baserates.py:57-58 (feed alpaca:sip, adjustment all); research/setups/replay.py:1-24, :237 (same resample_to_4h on the replay slice)
- agentic_trader/research/setups/sources.py:26-34 and card_stats_worker.py:116 (labels on configured feed, raw)
- agentic_trader/data/providers.py:348-379 (fetch_latest_price: no feed pin, no timestamp); :426-437,:439-460 (yfinance auto_adjust and fast_info fallback); :510-529 (silent fallback chain)
- Only research checks the feed tag: cli/commands/alpha.py:188, data/sessions.py:86,141
- Measured: ~/.local/state/agentic-trader/market-data/2026-10-20 manifests (3,432 captures, all alpaca:iex) vs ~/agentic-trader-research/setup-cache-v1 and pooled-cache-v1 hourly npz (per-hour bar presence 2025–2026); probe script ~/agentic-trader-research/holistic-review-20261021/scripts/hours_probe.py
- Free delayed SIP already in production for PEAD: agent/copilot.py:397-401 (feed="sip"); docs/research-data-sources.md 'Verified account behavior' (end ≥15 min old allowed on Basic)
- agentic_trader/market/bars.py:282 build_session_bars (RTH, open-anchored, never imputes) exists but only research and the forward observers use it

**Recommendation**

Use one regular-trading-hours, open-anchored bar builder for live and replay alike, by wrapping market/bars.py build_session_bars or an hourly equivalent. Hourly bars run 09:30–10:30 … 15:30–16:00 New York time, 4h bars run 09:30–13:30 and 13:30–16:00, with no extended hours, and the forming bar is dropped. Native scans fetch SIP with end = now − 16 min, which is free and already done for PEAD. Move the suggestion scans to at or after 10:47 and 14:47 so the 09:30–10:30 and 13:30–14:30 bars are complete, or use a 30-minute grid. Label card_stats and cards outcomes on SIP. Before 2026-11-01, either freeze the 4h bucket edges in ET as a stopgap that keeps today's definition through DST, or ship the new builder. Either way the native strategy needs a new frozen replay base rate, because it has a new identity, with its sign checked on a window no study has read. Fail closed on provenance: remove yfinance from equity scan fallbacks, or journal the provider per frame and exclude non-Alpaca frames from gates. Replace fetch_latest_price for taps and ratchets with a timestamped Alpaca snapshot and a maximum age.

**North-star impact**

The honest base rate printed on cards, card_policy's evidence, and the −0.11R verdict on native setups all describe a different trigger from the one that sends cards. No replay result can be applied to the live stream until the series match, and on 2026-11-02 the live trigger changes meaning again with no code change.

**References**

- Alpaca Market Data FAQ (historical SIP requires end ≥ 15 minutes old on the free plan) — Delayed SIP is free and already used for PEAD; it makes live and research one feed (https://docs.alpaca.markets/us/docs/market-data-faq)
- Alpaca Stock Bars API reference (adjustment raw/split/dividend/spin-off/all) — Shows the feed and adjustment parameters the live path should pin (https://docs.alpaca.markets/reference/stockbars)

---

## The one research-positive card source (PEAD) runs on events with no timing, survivors only and today's consensus; free EDGAR data fixes all three

_area: data · kind: gap · impact: high · effort: M · confidence: medium_

**Claim**

PEAD long passed at +0.149R, +0.055R over control, and is the only research-positive live source. Its documented caveats are all data caveats: report time is unknown, so entry waits until D+2; Nasdaq history lacks delisted names; EPS and consensus are as served today. I tabulated the 16,755 cached Nasdaq pages read-only. The `time` field is 'time-not-supplied' on 100% of rows in every year 2016–2025, and on 95% in 2026. Rows grow from 9,673 in 2016 to 17,693 in 2025, which is consistent with dead reporters missing. epsForecast is present on 77–83% of rows. The live blackout fetches the same pages but caches them only in memory, so no point-in-time consensus record accrues. EDGAR submissions JSON carries per-filing acceptanceDateTime, form and 8-K items. I verified this on CIK 320193: an 8-K accepted 2026-07-31T00:30Z, which is 20:30 ET, after the close.

**Evidence**

- docs/apriori-pead-2026-09-26.md 'Reading the result' and 'Caveats' (lines 111-136)
- Measured: ~/agentic-trader-research/apriori-pead-v1-cache/nasdaq/*.json (time field and row counts by year; epsForecast coverage)
- agentic_trader/agent/earnings.py:185-240 (in-memory cache only; parses time, epsForecast, noOfEsts at :138-148)
- docs/alpha-data-augmentation-2026-10-18.md §2 row 3 and §3 'Point-in-time earnings'
- https://data.sec.gov/submissions/CIK0000320193.json (sic, tickers, formerNames, filings.recent.acceptanceDateTime/form/items verified)
- Probe expiry 2026-10-28: docs/alpha-roadmap.md:574-575

**Recommendation**

Build PEAD v3 inputs from the EDGAR bulk acquisition in the next finding. (1) The event instant is the 8-K Item 2.02 acceptanceDateTime, mapped to the first regular session at or after it, with acceptance after 16:00 rolling to the next session. Pre-open reports can then be entered at D+1 instead of D+2. (2) The event set comes from every EDGAR filer that had a ticker on that date, with dead names from the Alpaca inactive list and Form 25, not from Nasdaq's surviving rows. (3) The surprise must be point-in-time and need no consensus vintage. Use a seasonal-random-walk SUE from XBRL EPS with `filed` dates, or the announcement return (EAR), which needs only the timestamp and bars. (4) Persist the live Nasdaq pages daily so analyst-based SUE accrues forward. Declare v3 as a second look on an already-read window. It can demote the leg or support renewal, but never counts as independent confirmation; forward probe accrual is the confirmation.

**North-star impact**

PEAD is the only source with a positive paired edge and the only alternative to the measured-negative native stream. The decision to renew, widen to both slots, or kill it at expiry should rest on survivorship-complete, correctly timed events, and earlier entry may capture drift the D+2 rule forgoes.

**References**

- Livnat & Mendenhall (2006), 'Comparing the post-earnings announcement drift for surprises calculated from analyst and time series forecasts', Journal of Accounting Research — A time-series SUE from filed EPS is a point-in-time surprise without consensus history; analyst SUE drifts more, which motivates capturing consensus forward
- Brandt, Kishore, Santa-Clara & Venkatachalam (2008), 'Earnings announcements are full of surprises', working paper — Return-based surprise (EAR) drift needs only the announcement timestamp and bars
- SEC EDGAR APIs (submissions, companyfacts, nightly bulk ZIPs) — Free source of acceptance timestamps and 8-K items (https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

---

## There is no reference data (industry, shares outstanding, filing-dated events); two nightly SEC bulk files supply all of it

_area: data · kind: integration · impact: high · effort: M · confidence: high_

**Claim**

Sectors exist only as hand-written labels on the 159 live names; UniverseEntry defaults to 'unknown'. None of the 452- or 1,000-name cohort members has a sector. No module reads SIC codes, XBRL company facts or shares outstanding: grep finds no sic, companyfacts or EntityCommonStockSharesOutstanding in agentic_trader. The EDGAR insider lane already has the SEC User-Agent plumbing and a CIK map, but only for today's tickers (company_tickers.json). The consequences appear across the system:
- the pooled control cannot be risk-matched, because the cube stores no sector or size array;
- cross-sectional neutralisation is impossible on cohorts;
- dynamic names get no correlation group, which is how VOO was carded after VTI on 2026-10-20.
SEC republishes submissions.zip and companyfacts.zip nightly at about 03:00 ET. The submissions JSON includes sic, sicDescription, tickers, exchanges, formerNames and per-filing acceptanceDateTime and items.

**Evidence**

- agentic_trader/config.py:101-104 (UniverseEntry.sector default 'unknown')
- agentic_trader/research/pooled/cube.py:53 (_ARRAYS: no sector/size/beta/ATR%)
- agentic_trader/research/insider_filings.py:58-62 (SEC host, current company_tickers.json)
- agentic_trader/risk/rules.py:332-348 and agent/copilot.py:614-617 (dynamic names only in 'dynamic' pseudo-group)
- docs/alpha-data-augmentation-2026-10-18.md §1 table 'Reference' row and §3 'Cross-sectional operators'
- SEC EDGAR APIs page (bulk ZIPs republished nightly ~03:00 ET); submissions JSON for CIK 320193 (fields verified)
- Ken French Data Library: daily FF5 (2x3) and momentum files and industry 'Details' pages (verified, updated through August 2026)

**Recommendation**

Build one EDGAR reference acquisition that serves both research and live. Download submissions.zip and companyfacts.zip nightly, with a SHA-256 per file per date. Parse them into point-in-time tables keyed by CIK:
(a) CIK ↔ ticker by date, joined through the daily Alpaca asset snapshot (see the 'start the clock' finding) and formerNames;
(b) SIC → Fama-French 12 and 49 industries, declaring that SIC is current rather than point-in-time, with low drift;
(c) shares outstanding from dei:EntityCommonStockSharesOutstanding, visible from the session after `filed`, giving market cap and turnover;
(d) filing events with acceptanceDateTime: 8-K items 2.02, 1.01 and 5.02, SC 13D, Form 25 and Form 15, 10-Q and 10-K.
Add Ken French daily FF5 and momentum factors for residual labels in research, and SPY and sector-SPDR betas, already in the universe, for live. Store per-cell sector, log size, ATR% and 60-session beta in the cube, so the decisive pooled statistic can be a within-(sector × size) residual, DGTW style. Use Qlib's date/period/value PIT layout for the tables.

**North-star impact**

One acquisition feeds four consumers:
- group-neutral ranks and risk-matched controls for mining, which shrinks standard errors and makes the 64 nulls readable;
- sector groups for dynamic names in the risk rules, so two cards are not the same bet;
- size and turnover fields;
- event timestamps for the catalog lane (PEAD v3, 13D, Form 25).

**References**

- SEC EDGAR application programming interfaces — submissions.zip and companyfacts.zip bulk files, free, nightly (https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- Kenneth R. French Data Library (daily FF5, momentum, SIC industry definitions) — Free residualisation factors and the SIC→industry map (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)
- Daniel, Grinblatt, Titman & Wermers (1997), 'Measuring mutual fund performance with characteristic-based benchmarks', Journal of Finance — Characteristic-matched (DGTW) control for the pooled paired edge
- Microsoft Qlib, Point-in-Time database — Template for date/period/value PIT tables that avoid look-ahead (https://qlib.readthedocs.io/en/latest/advanced/PIT.html)

---

## The survivorship repair was deferred on a misread window; the discovery years are exactly the ones the free Alpaca inactive list covers

_area: data · kind: defect · impact: high · effort: M · confidence: high_

**Claim**

The survivorship spike says 'Cohort v2/v3 discovery runs 2021-06 → 2026-01'. On that basis it concludes the repair restores 'almost none after' 2022 and should wait until a cohort is next re-frozen. In fact every pooled protocol discovers on 2016-08-01 → 2021-12-31, selects on 2022–2023 and confirms on 2024-01 → 2026-07. Cohorts are 'listed and active on 2026-09-17' (v2) or '2026-10-15' (v3), ranked on 'SIP raw dollar volume of the 60 sessions ending 2023-12-29', and 'not point-in-time'. The spike itself counts 234, 267, 310, 262, 201 and 152 now-inactive names per year for 2016–2021 above the v3 $5M floor, with SIP bars for 97% of non-OTC inactive symbols. Discovery therefore excludes about 150–310 liquid names per year, and eligibility is ranked on liquidity at the end of the selection window. On the asof question, Alpaca's default (asof = today) follows a current symbol back through renames, which is correct for current members. Dead names and reused tickers (the spike found FISV and SBNY) need an explicit asof, which the provider never sets.

**Evidence**

- docs/survivorship-spike-2026-10-20.md:41 (wrong window) and 'What was run' item 2 (per-year counts), 'Recommendation'
- config/research/pooled/campaign-v2.json:10-22 (windows); same windows in cohort3-*, short-v1, insider-v1, events-v1
- config/research/pooled/cohort-v2.json:4 and cohort-v3.json 'survivorship' field
- agentic_trader/data/evidence.py:24-27 (asof listed) vs data/providers.py:244-252 (StockBarsRequest without asof)
- Alpaca stock bars reference: asof default current day, renames mapped (verified)

**Recommendation**

Build cohort v4 point-in-time before the next confirmation spend. Membership comes from the Alpaca inactive list for 2016–2021, EDGAR Form 25 and Form 15 for 2022 onward, and the daily asset snapshot from now on. Eligibility is computed per session from trailing raw dollar volume, which point_in_time_eligibility already does at D-1, never from a 2023 ranking. Request dead names with asof set to their last active date. Declare a delisting-return rule for cells whose bracket spans the last session: the last close for cash mergers, and a Shumway-style haircut for performance delistings identified from Form 25 or the absence of a merger action. Re-report the pooled leg means and PEAD's every-reporter control on v4 as declared descriptive diagnostics, which charge no trials. Correct line 41 of the spike doc.

**North-star impact**

Every absolute gate, such as the pooled discovery leg mean being above 0 or the confirmation leg's ci90 being above 0, and every base rate shown on a card is an upper bound until this lands. Long-only and reversal-type signals are flattered most, and spending the last confirmation window on a survivor-only cohort cannot be undone.

**References**

- Shumway (1997), 'The delisting bias in CRSP data', Journal of Finance — Delisting returns are systematically negative for performance delistings, so they need a declared rule
- Brown, Goetzmann, Ibbotson & Ross (1992), 'Survivorship bias in performance studies', Review of Financial Studies — Survivor-only samples bias both levels and apparent predictability
- Alpaca Stock Bars API, asof parameter — The correct way to request dead or reused tickers (https://docs.alpaca.markets/reference/stockbars)

---

## No shared incremental bar store: live refetches a year of history every scan, research re-downloads per lane, and the only retained copy is 340 MB/day of raw pages on a 96%-full volume

_area: data · kind: improvement · impact: high · effort: L · confidence: high_

**Claim**

fetch_data pulls 1y of daily bars, 60d of hourly bars and optionally 10d of 15m bars per symbol per scan, paced at 150 requests per minute. About 180 names take 180–250 s live. At cohort-v3 breadth (1,000 names, 2–3 requests each), pacing alone is 13–20 minutes per scan. cache_ttl_seconds is stored but never read. Research fetches bars one symbol at a time per lane; cohort v3's hourly download took about eleven hours. I measured the stores read-only. The live evidence store is 9.2 GB over 33 days, and 2026-10-20 alone holds 340 MB in 3,432 captures. The entire 10-year research bar corpus (pooled-cache-v1, 3,075 symbol-timeframe files) is 796 MB. /System/Volumes/Data is 96% used, with 87 GiB free. Normalisation keeps only OHLCV and drops Alpaca's per-bar vwap and trade_count, so the DSL's `vwap` field has no data in the cube ('the cache has no VWAP').

**Evidence**

- agentic_trader/data/market_data.py:66,72 (cache_ttl stored, unused); :197-239 (per-scan full fetch)
- agentic_trader/agent/copilot.py:2228-2253 (_fetch_universe per contract); config.py:698 (150 req/min)
- agentic_trader/research/setups/runner.py:158-189 (per-symbol sequential cache)
- docs/alpha-pooled-cohort3-2026-10-16.md lines 31-36 ('about eleven hours'; 'A 1,000-name cube is a day's download')
- agentic_trader/market/bars.py:29 (OHLCV only); data/providers.py:286-300 (normalisation renames OHLCV only); docs/alpha-pooled-mining.md:349
- Measured: du of ~/.local/state/agentic-trader/market-data (9.2G), its 2026-10-20 directory (340M, 3,432 captures), ~/agentic-trader-research/pooled-cache-v1 (796M); df /System/Volumes/Data 96%
- Multi-symbol batching already exists: data/providers.py:310-346 (fetch_daily_many, 100 symbols per request)

**Recommendation**

Build one warehouse of completed bars. It is written nightly, and again at :47 for the day's completed regular-hours bars, from delayed SIP:
- daily bars, raw plus split factors;
- regular-hours hourly bars;
- coverage of all listed common stocks and ETFs above a low dollar-volume floor;
- multi-symbol requests;
- o/h/l/c/v/vw/n kept;
- partitions by session date, each with a digest.
Protocols pin a slice digest instead of keeping per-lane caches. Live scans read history from the warehouse and request only today's bars, one multi-symbol call per 100 names. Evidence retention: keep manifests, results and hashes forever, gzip pages after 7 days, and stop storing page bodies of full-history refetches once the warehouse holds those bars.

**North-star impact**

Universe expansion, which the operator asked for, becomes a config change: 1,000+ names at the same scan cadence. New cohorts and cubes take minutes instead of a day. Prospective nightly scoring of every frozen formula gets its daily input, which is the 'reasonable cadence' for mining. It also removes a disk-exhaustion risk to the daemon.

**References**

- Microsoft Qlib data layer and Point-in-Time database — Reference design for a shared, incremental, point-in-time research and live store (https://qlib.readthedocs.io/en/latest/advanced/PIT.html)
- Alpaca Stock Bars API (multi-symbol requests, vw and n fields) — Batch requests and the columns the cache currently discards (https://docs.alpaca.markets/reference/stockbars)

---

## Point-in-time data the desk already downloads every day is thrown away: the security master, analyst consensus, headline revisions

_area: data · kind: gap · impact: high · effort: S · confidence: high_

**Claim**

The dynamic-universe builder reads Alpaca's full active US-equity asset list once per New York date (14,404 active rows in the 2026-10-20 spike) and keeps it only in memory. The Nasdaq earnings pages, with epsForecast, noOfEsts and BMO/AMC timing, are fetched for the blackout and are also memory-only. NewsItem drops `updated_at`. These are exactly the pieces the desk's own assessments name as missing:
- point-in-time membership after 2022 (spike: 'Membership: no');
- consensus history, 'unavailable without a paid estimates vendor';
- announcement timing, which is 'time-not-supplied' on 100% of historical Nasdaq rows (measured).
The only point-in-time record that accrues today is the per-scan dynamic_universe_built journal.

**Evidence**

- agentic_trader/screeners/dynamic_universe.py:345-366 (assets cached in memory per NY date)
- agentic_trader/agent/earnings.py:185-240 (memory cache; no persistence)
- agentic_trader/data/news.py:33-48 (NewsItem without updated_at)
- docs/survivorship-spike-2026-10-20.md 'Answer' (inactive list stops being a record ~2022); docs/alpha-data-augmentation-2026-10-18.md:66-70
- agentic_trader/agent/copilot.py:2184-2222 (dynamic_universe_built is journaled)
- Pattern to reuse: agentic_trader/data/corporate_actions_worker.py:44-68 (once per NY date, outside scan windows)

**Recommendation**

Add a daily pit-snapshots worker modelled on the corporate-actions worker. It runs outside scan windows and writes, per source and per New York date, one gzip file with its SHA-256 and receipt time. Sources:
1. Alpaca asset master, all statuses, with asset-id UUIDs, so renames and delistings appear as day-over-day diffs.
2. Nasdaq earnings pages for D−5 … D+30: consensus vintages, plus BMO/AMC as first announced.
3. Market-wide structural corporate actions.
4. Options-chain summaries (see the options finding).
5. The VIX, FRED and yield observations the regime gates use.
The total is a few MB per day. Research reads these with the same digest pinning as other caches.

**North-star impact**

These are the only inputs whose value cannot be bought later; every uncaptured trading day is lost for good. Within months they give a free forward membership record that closes survivorship for live-era cohorts, a point-in-time analyst SUE for PEAD, and a history of the attention names that compete for cards.

**References**

- Livnat & Mendenhall (2006), Journal of Accounting Research — Analyst-forecast SUE drifts more than time-series SUE, which is why capturing consensus vintages forward is worth doing

---

## Stop building one-shot, research-only acquisitions per mining lane

_area: data · kind: stop_doing · impact: medium · effort: M · confidence: high_

**Claim**

Each new data source has arrived as a bespoke unit: an acquisition, a fields module, a protocol subclass and a CLI module. Examples are FINRA short volume, EDGAR insider, the Nasdaq pages, the corporate-actions research cache and news. Each costs about ten touch points (protocol subclasses, a Sources trio, isinstance ladders and an N×N CLI exclusion matrix), and build_cube_inputs refuses more than one source. None has a daily live adapter, so a finalist from those lanes could not reach a card without new ingestion and a parity test. The FINRA lane used only the CNMS file, which FINRA says begins 2018-08-01, leaving about 836 scored discovery sessions against about 1,360 for the daily set. Across these lanes, 64 formula–cohort pairs produced no finalist.

**Evidence**

- agentic_trader/research/pooled/campaign.py:403-584 (one subclass per source); research/pooled/runner.py:93-195, :317-318 (single source only)
- agentic_trader/cli/commands/alpha.py:1656-1715 (mutual-exclusion matrix)
- agentic_trader/research/short_volume.py:42 (CNMS only); docs/alpha-pooled-short-v1-2026-10-20.md:31-34 (~836 vs ~1,360 sessions)
- No agent/ or data/ consumer of research/short_volume.py or research/insider_filings.py (grep)
- FINRA daily short-sale volume files page: 'The earliest date for the Consolidated NMS files is August 1, 2018'

**Recommendation**

Admit no new single-source lane until a source comes in through one FieldSource contract, which has four parts:
(a) a historical adapter that writes point-in-time event tables into the warehouse, keyed on visibility time, not effective date;
(b) a daily incremental adapter run by the pit-snapshots worker;
(c) a parity test that the live D−1 value equals the warehouse value for the same session;
(d) coverage and absent-session binding in the digest, as short_volume.py already does.
Formulas can then read any union of sources, which lifts the single-source limit and allows interaction hypotheses. Order candidate sources by ex-ante yield against the lane's detection floor and by fit to the traded horizon.

**North-star impact**

Mining stops being 'one lane, one shot, nothing reaches Telegram' and becomes a reusable data layer in which a passing field already has a tested daily path to a card.

**References**

- FINRA daily short sale volume files — Documents the CNMS start date that limited the lane's discovery window (https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files)

---

## Live indicators and the book-vol covariance use raw prices across splits; under enforce, one split in a held name can block every card for months

_area: data · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

fetch_bars defaults to adjustment='raw'. TrendPullback's daily close > EMA50 > EMA200 test and its 4h ATR read those frames. Book sizing accepts only raw frames: is_raw_frame passes only None or 'raw', and missing names are fetched with adjustment='raw'. A 2:1 split inside the 120-session window therefore enters as a −50% return, and Ledoit-Wolf shrinkage spreads it across the matrix. If the book's volatility before the card then exceeds the budget, book_vol returns factor 0 for every non-hedging card. The corporate_action entry rule only covers −5 … +7 days, and the capture excludes dynamic, PEAD and operator names. Measured incidence:
- cohort v3's cached actions hold 135 splits (91 forward, 44 reverse) across 964 names over 2016–2026, about 1.4% of names per year, with 7 names inside a trailing-year split on 2026-10-15;
- market-wide there were 6 forward and 11 reverse splits in 10 days, concentrated in the small caps the movers screen surfaces.
A 1:10 reverse split lifts a $2 stock past the $10 dynamic price floor and makes close > EMA200 true for up to 200 sessions.

**Evidence**

- agentic_trader/data/providers.py:186 (adjustment='raw' default)
- agentic_trader/research/setups/covariance.py:20-26 (RAW_ADJUSTMENTS, is_raw_frame)
- agentic_trader/execution/book_sizing.py:206-232 (raw-only frames; fetch_daily_many adjustment='raw')
- agentic_trader/risk/book_vol.py:91-95 (factor 0 when the book is already over budget)
- docs/corporate-actions.md:39-41 (market-wide split counts), 'Entry rule', 'Known limits'
- Measured: ~/agentic-trader-research/corporate-actions-cohort-v3-20261015/actions.json (split counts by year; trailing-year names)

**Recommendation**

Fetch adjustment='split' for indicator, ATR and covariance frames. Keep raw prices only for price floors and order prices, following the raw/adjusted split cohort.py already uses. Accept 'split' explicitly in RAW_ADJUSTMENTS and keep rejecting yfinance auto-adjust. Capture structural corporate actions market-wide, about 40 per 10 days, which is far under max_symbols, so dynamic, PEAD and operator names are covered. Journal a per-scan count of names with a structural action inside any live lookback. The corporate-actions doc lists this measurement as open.

**North-star impact**

Removes a rare but silent failure that can stop the card stream under book_sizing enforce. It also removes false 'bullish trend' cards on reverse-split movers.

**References**

- Alpaca Stock Bars API, adjustment=split — Split-adjusted frames are a request parameter, not new data (https://docs.alpaca.markets/reference/stockbars)

---

## Options-implied data must be captured going forward, or it will never exist; the desk's options code uses yfinance and stores nothing

_area: data · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

options/fetcher.py builds GEX from yfinance option chains, an unofficial and unpinned source, for display only. The installed alpaca-py 0.44 already exposes OptionHistoricalDataClient.get_option_chain and snapshot models with implied_volatility and greeks. Alpaca's historical option data starts in February 2024. Its free feed is 'indicative', a derivative of OPRA; real OPRA requires a subscription. The data-augmentation assessment deferred options as paid. The result is that no free options history covers the 2016–2021 discovery window, but a free forward capture is available today.

**Evidence**

- agentic_trader/options/fetcher.py:9,36,67 (yfinance chains)
- alpaca-py 0.44 site-packages: alpaca/data/historical/option.py:24,204 (OptionHistoricalDataClient.get_option_chain); alpaca/data/models/snapshots.py:97-104 (implied_volatility, greeks)
- Alpaca historical option data docs: 'only offer historical option data since February 2024'; indicative feed free, OPRA subscription
- docs/alpha-data-augmentation-2026-10-18.md:50 (#9 options deferred as paid)

**Recommendation**

Add daily per-underlying summaries to the pit-snapshots worker at 15:45 ET, covering the static universe plus that day's dynamic and PEAD names. Fields:
- 30-day ATM implied volatility, interpolated;
- implied minus 20-day realized volatility;
- 25-delta put skew;
- put-call IV spread at matched strikes;
- option-to-stock volume;
- the next earnings implied move from the straddle.
Tag every value with feed 'alpaca:indicative' and its receipt time. Filter requests to expiries of 60 days or less and strikes within ±20%, which keeps it to about 1–3 pages per name. Use the data in two stages. Right away, it is live risk context: the implied earnings move for held names, and forward volatility next to the 120-session realized covariance. After about 12 months, it becomes a prospective research field set on a 5-session label, since the literature horizon is about a week. Replace the yfinance GEX source with the same feed. Declare two caveats: indicative quotes are not the OPRA NBBO, and much of the put-call-parity signal reflects borrow cost, so a long-only desk can use it only as a veto or ranking input.

**North-star impact**

It gives cards forward-looking risk lines now, and it is the only alternative-data family with strong weekly cross-sectional evidence in liquid names that the desk can get for free, but only if capture starts now.

**References**

- Cremers & Weinbaum (2010), 'Deviations from put-call parity and stock return predictability', JFQA — Put-call implied-volatility spread predicts weekly returns
- Xing, Zhang & Zhao (2010), 'What does the individual option volatility smirk tell us about future equity returns?', JFQA — Skew as a negative predictor; usable as a long-side veto
- An, Ang, Bali & Cakici (2014), 'The joint cross section of stocks and options', Journal of Finance — Changes in implied volatility predict stock returns
- Johnson & So (2012), 'The option to stock volume ratio and future returns', JFE — Option-to-stock volume as an informed-trading signal
- Muravyev, Pearson & Pollet (2022), 'Is there a risk premium in the stock lending market? Evidence from equity options', Journal of Finance — Shows put-call-parity signals largely reflect borrow fees, which tempers expectations
- Alpaca historical option data documentation — History starts February 2024; indicative feed is free (https://docs.alpaca.markets/docs/historical-option-data)

---

## Dynamic in-play names compete for the same two cards but cannot be measured; the dropped per-bar trade count would make their screen replayable

_area: data · kind: gap · impact: medium · effort: M · confidence: medium_

**Claim**

Each scheduled scan adds up to 20 names from Alpaca most-actives ranked by trade count and from the movers lists. There is no historical screener record, so the setup replay and card base rates cover only the static 159 names. Dynamic names also get no sector and no corporate-action coverage: in the preview week, 2 of the 9 blocked cards were uncovered most_actives names. Their cross-sectional features are NaN. Alpaca bars carry trade_count and vwap, but normalisation keeps only OHLCV, so neither live frames nor research caches can reconstruct 'most active by trades'. The per-scan dynamic_universe_built journal has been a prospective record of the screen since 2026-09-23.

**Evidence**

- agentic_trader/screeners/dynamic_universe.py:311-341 (most_actives by TRADES, movers); config/config.yaml:259-269
- agentic_trader/market/bars.py:29; agentic_trader/data/providers.py:286-300 (OHLCV-only normalisation)
- docs/corporate-actions.md 'Entry rule' (three uncovered in preview week) and 'Known limits' (dynamic names always uncovered)
- agentic_trader/research/setups/ranker.py:245-249 (dynamic excluded from live cross-section)
- agentic_trader/agent/copilot.py:2184-2222 (dynamic_universe_built payload)

**Recommendation**

Keep vw and n in the warehouse. Reconstruct a daily historical proxy of both screens over the full warehouse universe: top-N by prior-session trade count, and top-N by absolute return at 10:35 from regular-hours bars. Apply the same pure filters, select_dynamic and liquidity_gate. Run the native strategies through the existing replay on that proxy universe, as a predeclared comparison of dynamic against static base rate and excess R. Until that exists, give dynamic-name cards a 'not measured' evidence line and assign them SIC sectors from the EDGAR reference tables, so correlation groups apply.

**North-star impact**

About one in nine names in each scan is unmeasured and can take a card slot. The attention literature predicts adverse selection for long entries on extreme movers, and the desk cannot currently check whether that is happening.

**References**

- Barber & Odean (2008), 'All that glitters: the effect of attention and news on the buying behavior of individual and institutional investors', Review of Financial Studies — Attention-grabbing stocks see net retail buying and subsequent underperformance
- Da, Engelberg & Gao (2011), 'In search of attention', Journal of Finance — Attention predicts short-run price pressure and later reversal

---

## Costs are a flat 5/15 bp everywhere, although per-name spread estimates and journaled arrival quotes cost nothing

_area: data · kind: improvement · impact: medium · effort: S · confidence: medium_

**Claim**

The pooled cube charges one flat decision_cost_bps (15 bp decisive on cohort v3). The setup labeller and cards outcomes charge a fixed 5 bp per side. Execution journals the broker quote at submission, but nothing reads it back. Cohort v3's long-bracket base rate roughly halved against v2 (about +0.18R to +0.08R), and spread costs bite hardest in exactly the mid-caps and short horizons that other reviews propose to explore next.

**Evidence**

- agentic_trader/research/pooled/cube.py:86 (CubeSpec.decision_cost_bps flat)
- agentic_trader/research/setups/outcomes.py:64-65 (DEFAULT_COST_BPS_PER_SIDE = 5.0)
- agentic_trader/storage/workflow.py:606-614 (arrival quote journaled in entry_submitting, never read)
- docs/alpha-pooled-cohort3-2026-10-16.md (base-rate tables, ~+0.08R per pick on v3)

**Recommendation**

Add a per-(symbol, session) spread array to the warehouse and cube, computed from daily OHLC with the EDGE estimator (the 'bidask' package implements it). Calibrate it monthly against a sample of Alpaca historical SIP quotes and against live arrival-quote spreads and fill slippage. Charge r_cost as half-spread plus an impact term in research labels and in the card's EV line. Use the estimate as an eligibility input for cohorts and dynamic names, instead of IEX volume.

**North-star impact**

The EV-after-cost shown on cards and the decisive cost in mining become specific to each name. This prevents a short-horizon cube from manufacturing edge in wide-spread names, and filters dynamic names that are cheap to card but expensive to trade.

**References**

- Ardia, Guidotti & Kroencke (2024), 'Efficient estimation of bid–ask spreads from open, high, low, and close prices', JFE 161:103916 — The EDGE estimator works on the daily OHLC bars the desk already holds (https://doi.org/10.1016/j.jfineco.2024.103916)
- Corwin & Schultz (2012), 'A simple way to estimate bid-ask spreads from daily high and low prices', Journal of Finance — Baseline estimator for cross-checking
- Novy-Marx & Velikov (2016), 'A taxonomy of anomalies and their trading costs', Review of Financial Studies — Costs erase many higher-turnover anomalies, so cost realism changes conclusions

---

## A historical LLM study of the cached Benzinga or 8-K text would be look-ahead contaminated; only the live journaled scores are clean

_area: research_methodology · kind: research_direction · impact: medium · effort: S · confidence: high_

**Claim**

Benzinga headlines are cached back to 2015-01-02 and 'no study exists yet'. The signals review proposes testing headline tone against next-day excess returns on that history. Live news scores come from the desk's current LLM and journal its model id and prompt_sha256, so the prospective sample since 2026-10-13 is identifiable and clean. A model whose training data postdates 2016–2025 headlines can recognise firms and outcomes. Glasserman and Lin (2023) found the in-sample advantage of GPT-sentiment backtests is mostly look-ahead bias, and recommend anonymising firm identifiers.

**Evidence**

- docs/news-context.md 'Research acquisition' ('No study exists yet'; history reaches 2015-01-02)
- agentic_trader/agent/news_context.py:62-63,166-169 (model, prompt_sha256 journaled); :274-292 (desk LLM model)
- Signals map §7.10 proposal (Lopez-Lira & Tang frozen entry on cached Benzinga history)

**Recommendation**

Adopt a contract for text sources with three rules. (1) Historical studies use time-invariant, non-LLM measures, such as the Loughran-McDonald negative-word share, headline counts and novelty against the prior 5 days. LLM prompts are allowed historically only with entities anonymised and only for dates after the model's training cutoff. (2) LLM tone accrues prospectively, keyed by model id and prompt hash as already journaled, and is judged by a predeclared forward read. (3) The highest-value text is the EDGAR 8-K Item 2.02 Exhibit 99.1 earnings release, which carries guidance and tone and is timestamped by acceptance. Use it as a PEAD conditioner, scored live at acceptance and studied historically only with dictionary measures. Also keep `updated_at` on news items.

**North-star impact**

It avoids spending the last confirmation window, or the operator's trust, on a contaminated news result. It also points the LLM at the one place it can plausibly add information to cards: reading earnings releases, not re-checking gates.

**References**

- Glasserman & Lin (2023), 'Assessing look-ahead bias in stock return predictions generated by GPT sentiment analysis', arXiv:2309.17322 — Direct evidence and a remedy (anonymisation) for LLM look-ahead in backtests (https://arxiv.org/abs/2309.17322)
- Loughran & McDonald (2011), 'When is a liability not a liability? Textual analysis, dictionaries, and 10-Ks', Journal of Finance — Time-invariant financial dictionary for historical text features
- Tetlock (2007), 'Giving content to investor sentiment: the role of media in the stock market', Journal of Finance — Lexicon-based media tone baseline
- Lopez-Lira & Tang (2023), 'Can ChatGPT forecast stock price movements? Return predictability and large language models' — The proposed design, valid only on post-cutoff or prospective data

---

## Rank the remaining free sources by fit to the traded horizon, persist macro history for gate replay, and skip 13F, Google Trends and ETF flows

_area: data · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

With FINRA short volume closed on 2026-10-20 and EDGAR insider pending, the assessment's remaining list ranks sources by information per hour but not by fit to the 20-session label. FINRA publishes short interest twice a month for all exchange-listed stocks, on the 7th business day after settlement. Its API covers only 5 rolling years, so it cannot cover 2016–2021. The regime and macro gates read latest-only values (yfinance ^VIX and ^TNX, FRED latest observation), so replaying those gates needs history from somewhere. FRED's ICE BofA HY OAS series is reported to keep only 3 years of observations from April 2026. I could not confirm that on FRED directly because the site was under maintenance.

**Evidence**

- docs/alpha-data-augmentation-2026-10-18.md §2 (ranked table) and §4
- FINRA equity short interest page (twice monthly; published 7th business day; API up to five rolling years)
- agentic_trader/constants.py:161-173 (^VIX, ^TNX, DX-Y.NYB via yfinance; FRED DGS2, BAMLH0A0HYM2, T10YIE, T5YIE); agent/macro.py:298-349 (latest observation only)
- Risk map §7.6 (predeclared replay of regime gates proposed)

**Recommendation**

Run the remaining queue in this order, each source admitted through the FieldSource contract:
1. Short interest, as days-to-cover and low-short-interest 'good news'. It is a slow field that matches the 20-session label and is visible at publication date: API history from 2021, then prospective.
2. EDGAR events from the same submissions acquisition, with an event-entry design: SC 13D filings (activist stakes), 8-K 1.01 and 5.02.
3. Wikipedia pageviews, daily since 2015-07 and reproducible, as an attention veto for dynamic names. Do not use Google Trends, whose re-sampled values break digest pinning.
4. Macro history for gate replay: the CBOE VIX history CSV; FRED DGS2, DGS10 and T10Y3M; and BAA10Y as the full-history substitute for HY OAS. Persist the observed values daily; the regime block is already journaled per scan.
Skip three sources: 13F (quarterly with a 45-day lag, weak at 20 sessions), ETF flows (no free point-in-time source), and index-inclusion history (paid). Russell reconstitution can be approximated later from XBRL shares outstanding.

**North-star impact**

Keeps data work aligned with the horizon and long-only constraint the desk actually trades. It also makes the uncalibrated macro gates testable, since they currently halve risk and suppress the best-measured strategy.

**References**

- FINRA Equity Short Interest — Free, twice-monthly short interest for all exchange-listed securities; API limited to five rolling years (https://www.finra.org/finra-data/browse-catalog/equity-short-interest)
- Hong, Li, Ni, Scheinkman & Yan (2015), 'Days to cover and stock returns', NBER working paper — Days-to-cover is a cleaner short-interest predictor at monthly horizons
- Boehmer, Huszar & Jordan (2010), 'The good news in short interest', JFE — Low short interest predicts positive returns; usable long-only
- Brav, Jiang, Partnoy & Thomas (2008), 'Hedge fund activism, corporate governance, and firm performance', Journal of Finance — 13D filings carry abnormal returns around a timestamped event
- Da, Engelberg & Gao (2011), 'In search of attention', Journal of Finance — Attention measures for a dynamic-name veto
- FRED series BAMLH0A0HYM2 (ICE BofA US High Yield OAS) — Reported three-year history limit; verify before using it in a 2016–2021 replay (https://fred.stlouisfed.org/series/BAMLH0A0HYM2)


# Lens: quant (12 findings)

## Single-formula fixed sets on the frozen bracket cannot detect literature-sized edges. Stop launching them, including the pending insider campaigns as designed

_area: research_methodology · kind: stop_doing · impact: high · effort: S · confidence: high_

**Claim**

Now: each new data source becomes a fixed set of eight single formulas. Each formula is gated alone on the 2016-08-01→2021-12-31 discovery window, then must survive a conjunctive gauntlet (campaign.py:732-739 discovery t≥3, 3/4 blocks, leg>0, ≥400 sessions; :789-794 selection ≥0.5× the discovery edge on ~501 sessions; :830-842 Holm one-sided 0.05, leg ci90>0, trimmed mean>0, last-12-month edge>0 on ~646 sessions; values in config/research/pooled/cohort3-daily-v1.json).

The 64 published results:
- I recomputed them from the result tables: t mean −0.06, sd 1.26, 29/64 positive. The implied standard errors (|edge/t|) run from 0.006 to 0.042R, median 0.017R.
- Within each cohort, the dispersion of t is no larger than check C's null (seed-formula t sd 1.11–1.27). On v2 the observed variance is 1.08 against a null of about 1.39; on v3 it is 1.57 against about 1.41. Empirical-Bayes true-effect dispersion is therefore about 0 on v2 and 0.39 t-units on v3.
- The record is statistically exchangeable with noise. It rules out large standalone edges: the one-sided 95% upper bound is about edge + 0.028R at the median standard error. It does not rule out small ones.

End-to-end power of the gauntlet for one true formula at the median standard error, from my normal-approximation simulation (which ignores the block, leg-CI and multi-candidate Holm conditions, all of which make passing harder):
- 1% at 0.03R
- 11% at 0.044R (a decile with 20-session IC 0.02, using δ≈IC×1.25R×1.755)
- 31% at PEAD's 0.055R
- 54% at 0.065R

Clearing t≥3 over 5.4 years needs an annualised information ratio of about 1.3 on the long-decile-minus-pool series. That is far above what published anomalies retain in liquid US names after publication.

The lane's own contract says discovery power is 0.08–0.10R and that PEAD-sized edges "belong to the a priori catalog". Events-v1 nonetheless put the pead_* formulas into this lane.

The bootstrap is also over-dispersed: check C's seed-t sd is 1.11–1.27, so the nominal t≥3 is about 2.4–2.7 when calibrated. Check A's planted AR(1) curve is an upper bound, not each formula's power.

**Evidence**

- agentic_trader/research/pooled/campaign.py:732-739, 789-794, 830-842 (stage gates)
- config/research/pooled/cohort3-daily-v1.json (windows, discovery_gate min_t 3.0, selection 0.5x, confirmation alpha 0.05, bracket 2xATR14/3R/20 sessions)
- Result tables: docs/alpha-pooled-checks-v4-2026-10-07.md 'The campaign'; docs/alpha-pooled-intraday-v1-2026-10-14.md; docs/alpha-pooled-events-v1-2026-10-15.md; docs/alpha-pooled-cohort3-2026-10-16.md 'Campaigns'; docs/alpha-pooled-short-v1-2026-10-20.md (Cohort v2 / Cohort v3 tables)
- Check C null t sd: docs/alpha-pooled-cohort3-2026-10-16.md 'Check C'; docs/alpha-pooled-short-v1-2026-10-20.md check C table; docs/alpha-pooled-intraday-v1-2026-10-14.md; docs/alpha-pooled-events-v1-2026-10-15.md
- docs/alpha-pooled-mining.md 'Purpose', 'What power check A does and does not show', 'Caveats' (Power: 'PEAD-sized edges (about 0.055R) belong to the a priori catalog')
- docs/alpha-pooled-mining-research-2026-09-28.md lines 51 (δ≈3×IC mapping), 85-94 (MDE table), 119 ('expected to produce few discoveries')
- Recomputation scripts: ~/agentic-trader-research/holistic-review-20261021/pooled64.py and ~/agentic-trader-research/holistic-review-20261021/endtoend_power.py

**Recommendation**

1. Stop the lane-per-data-source pattern: no new single-formula fixed set on the 2016–2021 discovery window under the frozen 20-session long bracket.

2. Pending insider-v1 and cohort3-insider-v1 campaigns: do not run them as designed, or run them only in a discovery-only diagnostic mode that cannot freeze candidates or consume the lane-wide 2024-01→2026-07 window.
   - Null fixed-set replicates carried a candidate in up to 3 of 40 and reached confirmation in 1 of 40 (intraday). Each campaign is therefore a few-percent lottery ticket against the last historical window.
   - Plausible large-cap insider effects sit far below the 0.065R MDE.

3. Add an ex-ante yield gate to every lane spec:
   - expected δ = literature IC × about 0.4 (post-publication decay) × a liquid-universe haircut × 1.25 × 1.755 (decile)
   - compare it with the MDE at that family's own standard error, obtained by planting δ on the real formula's picks inside check C (seconds to minutes).
   - Hypotheses below the MDE go into the composite (see the composite finding) or the forward scorer, not a campaign.

4. Every result table reports per-formula edge ci90, calibrated t (t divided by check C's null sd) and MDE80, so a null reads "edge < X R at 90%".

**North-star impact**

This stops spending about one development day plus 3–10 compute hours per lane on tests that, by their own power curve, cannot produce a card. Effort moves to the designs below that can, and the last clean historical confirmation window is protected.

**References**

- Harvey, C., Liu, Y. & Zhu, H. (2016). ...and the Cross-Section of Expected Returns. Review of Financial Studies 29(1) — The t>3 hurdle is calibrated for discovering new factors out of hundreds of searched ones, not for testing a handful of predeclared literature priors (https://doi.org/10.1093/rfs/hhv059)
- Harvey, C. & Liu, Y. (2020). False (and Missed) Discoveries in Financial Economics. Journal of Finance 75(5) — Shows that strict hurdles trade false discoveries for a large missed-discovery (Type II) rate, the failure mode here
- McLean, R.D. & Pontiff, J. (2016). Does Academic Research Destroy Stock Return Predictability? Journal of Finance 71(1) — About 58% post-publication decay; the haircut for the ex-ante yield gate (https://doi.org/10.1111/jofi.12365)
- Hou, K., Xue, C. & Zhang, L. (2020). Replicating Anomalies. Review of Financial Studies 33(5) — Most anomalies are insignificant once microcaps are excluded, the regime of cohorts v2/v3 (https://doi.org/10.1093/rfs/hhy131)

---

## Native replay statistics are ill-conditioned in R units: cost on ~1.3% stops is about 0.08R per setup, the SPY control is uncosted, and the vol_20 lead is plausibly a cost artefact

_area: research_methodology · kind: defect · impact: high · effort: S · confidence: medium_

**Claim**

How native R is measured now:
- The stop is max(swing low, 1.5×ATR14 of the 4h trigger bars) (evaluator.py:208-214; strategies.py:163; config.yaml:275).
- r_cost = r − 2·bps·entry/risk_unit (labels.py:144), and the R statistics have no floor on the risk unit.

Cost per setup is large in R:
- Bracket counterfactual A0: mean R +0.116, so the cost is 0.077R per setup, which implies a harmonic-mean stop of about 1.3% of price.
- Setup outcomes: trend pullback +0.10→+0.03 (0.07R); squeeze 1h +0.20→+0.08 (0.12R).

The SPY control is uncosted. market_r is documented as 'Uncosted: a beta-one exposure control' (outcomes.py:186-219). About 0.08R of cost is charged to the setup and none to the benchmark, so most of the headline −0.110R 'excess' would disappear gross of cost.

The published columns do not reconcile:
- excess_r is clipped at ±10R but market_r is not (bracket_counterfactual.py:172-177).
- Primary window: 0.039 − 0.211 = −0.172, against −0.110 reported. Robustness window: 0.178 − 0.514 = −0.336, against −0.103 reported.
- So many rows have |excess| > 10R: the means are dominated by setups with tiny risk units.

Every arm shared the planned stop, so stop width, the lever that sets cost in R, was never varied. Yet 'entries lag the index' redirected the roadmap to the entry-side data lanes.

The setup study correlates features with r_cost (study.py:502-504). Stop % tracks volatility, so cost in R falls mechanically as vol_20 rises.
- My simulation assumes gross R is independent of volatility, uses the doc's outcome mix and sweeps entry slippage and stop/vol dispersion. It gives a purely mechanical Spearman ρ(vol, r_cost) of +0.02 to +0.13.
- That range brackets the observed +0.049 (Holm p 0.004).
- vol_20 is the lead now being read prospectively (2026-10-21→11-04) on roughly 150–200 candidates, where SE(ρ)≈0.07, so that read cannot discriminate 0.05 from 0 either.

**Evidence**

- agentic_trader/agent/evaluator.py:208-214 (stop = max(structural, 1.5×ATR))
- agentic_trader/screeners/strategies.py:163 (atr_14 = 4h ATR)
- agentic_trader/research/setups/labels.py:144, 187-188 (r_cost in R; entry at next bar open; risk_unit from levels)
- agentic_trader/research/setups/outcomes.py:186-219 (market_r 'Uncosted: a beta-one exposure control')
- agentic_trader/research/setups/bracket_counterfactual.py:172-177 (r, r_cost, excess clipped; market_r not)
- agentic_trader/research/setups/study.py:502-504 (H1/H2 Spearman vs r_cost)
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (arms share the planned stop), 'Descriptives' (A0 +0.116 / +0.039 / SPY +0.211 / excess −0.110; robustness +0.178 / +0.514 / −0.103)
- docs/setup-outcomes-2026-09-23.md 'Base rates' and 'Hypotheses' (vol_20 ρ +0.049 vs R_cost)
- Simulation: ~/agentic-trader-research/holistic-review-20261021/vol20_cost_confound.py

**Recommendation**

1. Before anyone reads the 10-21→11-04 prospective window, re-run H2d and H2h on gross r, and on r with cost charged in bps of price rather than R, using the retained development frames. This takes minutes and reads no new window. If ρ against gross r is about 0, retire vol_20 as a lead.

2. For a powered vol_20 read, make a declared second use of the 2017–2021 replay (8,119 setups, SE≈0.011) instead of the roughly 200 live candidates.

3. Floor the risk unit in every R statistic (for example ≥0.5× daily ATR or ≥0.75% of price). Report a %-return, β-adjusted and cost-symmetric excess (charge SPY about 1 bp per side) next to R.

4. Predeclare a stop-width arm protocol on the existing caches: 1×, 2× and 3× daily ATR on the same entries, targets in R, paired and β-adjusted. This is the untested explanation of the excess gap.

5. Replace the flat 5 bp per side with measured shortfall from the journaled entry_submitting quotes and order fills.

**North-star impact**

The diagnosis that native cards 'lag the index' and the only feature lead both rest on this unit. A wider, ATR-daily stop could lift native card EV by several hundredths of R with no alpha, and it would avoid a ranking decision built on a mechanical correlation.

**References**

- Kaminski, K. & Lo, A. (2014). When do stop-loss rules stop losses? Journal of Financial Markets 18 — Stop width and stop rules change expected return and cost drag. Width is the untested arm here
- Novy-Marx, R. & Velikov, M. (2016). A Taxonomy of Anomalies and Their Trading Costs. Review of Financial Studies 29(1) — Costs must be modelled per name and in return units; flat costs scaled by a volatility-dependent unit bias cross-sectional comparisons

---

## The decisive pooled statistic is not market- or cost-neutral in R units; two artefacts as large as every measured edge fit the sign flip between cohorts

_area: research_methodology · kind: defect · impact: high · effort: M · confidence: medium_

**Claim**

How the statistic is built now:
- session_table subtracts the equal-weighted mean r_cost of all eligible names from the picks' mean (stats.py:54-105).
- R = return/(2×ATR14) and cost is charged in R (cube.py:284-295; labels.py:144).
- The cube stores no ATR, beta or sector array (cube.py:53).

Two consequences, derived from the code:
(a) Market drift enters a name's R in proportion to β/σ = ρ/σ_m, its correlation to the market. Decile picks of extreme-value formulas (names in idiosyncratic states) carry less market-R than the pool, so in a rising tape they earn a negative edge.
(b) Cost in R is 2c/(2·ATR%), so higher-ATR picks pay less cost in R. At cohort v3's 15 bp per side, a pick with ATR 3.5% against a pool at 2.5% gains about +0.017R mechanically.

Predicted signs and what was observed:
- Artefact (a) should dominate v2 (pool leg about +0.17R at 5 bp) and push edges negative. Artefact (b) should dominate v3 (leg about +0.08R at 15 bp) and push them positive.
- Observed: v2 mean t −0.59 (9/32 positive); v3 mean t +0.46 (20/32). The typical |edge| is about 0.015R, the same order as the artefacts.
- The 32 shared formulas correlate only 0.44 across cohorts.

Check C's null demeans each name's mean R over the window (null_check.py:70-80). That removes exactly these persistent name-level components, which is consistent with its null t mean of about 0 while the real cohorts shift by about ±0.5.

The v4 'label diagnostic' read as 'no return-space signal' is contaminated the other way. Its own words: 'illiquidity and momentum_12_1 look good in raw returns only because their deciles hold high-volatility names in a rising 2016–2021 market.' Neither view removes β, volatility or sector exposure.

**Evidence**

- agentic_trader/research/pooled/stats.py:54-105 (equal-weighted pool control, default column r_cost)
- agentic_trader/research/pooled/cube.py:53 (_ARRAYS: no ATR/beta/sector), 284-295 (risk = 2×ATR; cost_bps_per_side = decision_cost_bps)
- agentic_trader/research/setups/labels.py:144 (cost in R)
- agentic_trader/research/pooled/null_check.py:70-80 (demeaned: each name's mean replaced by the overall mean)
- config/research/pooled/cohort3-daily-v1.json decision_cost_bps 15.0; docs/alpha-pooled-cohort3-2026-10-16.md 'Reading' (leg ~+0.08R v3 vs ~+0.18R v2)
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic' (return-space view; random AR(1) control edge per ATR −0.032)
- Cohort-level t means: ~/agentic-trader-research/holistic-review-20261021/pooled64.py

**Recommendation**

1. Run a diagnostic now. It needs no new window, because the discovery reads are already charged. For the 64 formulas:
   - re-score on r_gross and on cost in bps instead of R;
   - regress each formula's session edge on that session's pool mean (control_mean) and report the intercept (a market-neutral edge) and the slope.
   The prediction: on v2 the slopes are negative for extreme-value formulas, and the cohort-level shift shrinks once costs are in bps.

2. Pooled protocol v5:
   - Store in the cube an ex-ante β60 (from D−1 returns against SPY), ATR%, log dollar volume and an EDGAR SIC→Fama-French-12 sector.
   - Make the decisive statistic the per-session cross-sectional regression residual of a forward return that is β-hedged, scaled by ex-ante volatility and charged cost in bps. A DGTW-style matched cell (sector × ATR quintile × β tercile) is the alternative.
   - Re-calibrate check C without name-demeaning, using these controls instead (demeaning with full-window means is itself look-ahead).

**North-star impact**

Without this, any future 'finalist' may be a volatility or cost tilt that turns into negative excess once it is a card. With it, the existing 64 nulls become readable, and the standard errors of persistent formulas (0.03–0.04R) shrink toward those of the fast formulas (about 0.006–0.010R).

**References**

- Daniel, K., Grinblatt, M., Titman, S. & Wermers, R. (1997). Measuring Mutual Fund Performance with Characteristic-Based Benchmarks. Journal of Finance 52(3) — Characteristic-matched controls, the template for a risk-matched pool
- Fama, E. & MacBeth, J. (1973). Risk, Return, and Equilibrium: Empirical Tests. Journal of Political Economy 81(3) — Per-session cross-sectional regressions with controls
- Frazzini, A. & Pedersen, L.H. (2014). Betting Against Beta. Journal of Financial Economics 111(1) — β/volatility structure of the cross-section; low-β and low-vol names earn more per unit of risk, which R-scaling interacts with

---

## Detect with the whole cross-section and both tails, then test the long-only card implementation separately; the long-only top-decile statistic wastes up to 40% of t

_area: research_methodology · kind: improvement · impact: high · effort: M · confidence: medium_

**Claim**

How discovery works now: it uses only the long top decile minus the equal-weighted pool (formula.py:195-215; stats.py:140-145). One statistic is asked to both detect information and prove long-only tradability.

My simulation (370 names, 1,500 independent sessions, a weak linear signal):
- With idiosyncratic noise, a Fama–MacBeth slope on the score's rank has 1.70× the t of top-decile-minus-pool, worth about 2.9× the data. Top-minus-bottom decile has 1.36×.
- When style-factor noise loading on the score dominates, the gain falls to 1.15×. That is why the previous finding's controls come first.

In-house evidence:
- PEAD's short leg beat its every-reporter control by +0.100R (ci90 [+0.048, +0.157]), more than the long leg's +0.055R.
- Long-only detection discarded it. A two-leg detection statistic would have had t of about 4 instead of 2.66.

The DSL has no cross-sectional operators (dsl.py:1-5; operators.py:108-167). cross_sectional_rank, cross_sectional_zscore and group_neutralize exist only outside the grammar (panel.py:19-37).

Decile evidence never reaches the depth at which cards trade (1–2 names): top3_edge is computed only at confirmation (campaign.py:826-829), which no campaign has reached.

**Evidence**

- agentic_trader/research/pooled/formula.py:195-215 (select_top_fraction)
- agentic_trader/research/pooled/stats.py:140-145 (paired_edge_test)
- agentic_trader/research/alpha/dsl.py:1-5; agentic_trader/research/alpha/operators.py:108-167; agentic_trader/research/alpha/panel.py:19-37
- agentic_trader/research/pooled/campaign.py:826-829 (top3_edge descriptive at confirmation only)
- docs/apriori-pead-2026-09-26.md 'Criteria' (SHORT P2 +0.100 vs control; LONG P2 +0.055)
- Simulation: ~/agentic-trader-research/holistic-review-20261021/stat_efficiency.py; end-to-end: ~/agentic-trader-research/holistic-review-20261021/endtoend_power.py

**Recommendation**

Pooled protocol v5 with a two-stage claim.

(A) Information test:
- Statistic: the mean Fama–MacBeth slope of the risk-adjusted forward return (with the previous finding's controls) on the sector-demeaned cs_rank of the score, at the hypothesis' predeclared horizon.
- Errors: HAC with lag at least the horizon, or a session-block bootstrap, with t calibrated against check C.
- Multiplicity: Holm across the declared set on discovery and selection pooled (about 1,858 sessions). In my simulation this lifts end-to-end power at 0.055R from 31% to 65%.

(B) Implementation test, for A-passers only and as a single one-sided 5% test: top-k long-only (k = 1–2, the card depth), labelled with the live card policy's bracket and β-adjusted.

Add cs_rank, cs_zscore, group_demean(sector) and an explicit where(cond, x) to the pooled scoring path, which replaces the divide-by-boolean masks.

**North-star impact**

Detection power rises 1.2–1.7× at no data cost, and the long-only tradability claim is then tested at the depth cards actually trade.

**References**

- Grinold, R. & Kahn, R. (2000). Active Portfolio Management, 2nd ed. — Information is measured across the full cross-section (IC); implementation constraints such as long-only are applied afterwards
- Lewellen, J. (2015). The Cross-section of Expected Stock Returns. Critical Finance Review 4(1) — Fama–MacBeth composite forecasts have real out-of-sample power, including among larger stocks
- Stambaugh, R., Yu, J. & Yuan, Y. (2012). The Short of It. Journal of Financial Economics 104(2) — Anomaly profits concentrate in the short leg, which is why a separate long-only implementation test is mandatory after two-tail detection

---

## Spend the last historical confirmation window on one pre-registered, literature-signed composite with hierarchical shrinkage, not on single formulas

_area: alpha_mining · kind: research_direction · impact: high · effort: M · confidence: medium_

**Claim**

Now: 'one formula = one trial' (docs/alpha-pooled-mining.md 'Purpose') and the long-only single-formula gates leave no combination stage, so every weak signal is judged alone. Field sources cannot be mixed: build_cube_inputs refuses more than one (runner.py:317-318).

PEAD, the only pass, is itself a combination of two weak signals:
- surprise-only +0.115R;
- reaction-only +0.120R;
- both together +0.149R, against a control of +0.094R.

Empirical Bayes on the published t's already reproduces what the selection window showed:
- With within-cohort dispersion τ≈0.39 t-units on v3 (shrink factor 0.10), shorts_retreat's discovery estimate (+0.020R, t 3.40) shrinks to a posterior of about +0.004R.
- The selection window then measured +0.006R.
- A hierarchical model would have predicted that failure without a window read.

The ridge ranker's development gain came from one fold and reversed in the holdout (docs/setup-outcomes-2026-09-23.md 'Selection (H3)'). That is the textbook case that backtest-overfitting diagnostics catch.

**Evidence**

- docs/alpha-pooled-mining.md 'Purpose' (one formula counts as one trial)
- agentic_trader/research/pooled/runner.py:317-318 (one field source per cube build, per pooled map; spot-checked map claim)
- docs/apriori-pead-2026-09-26.md 'Diagnostics' (surprise-only +0.115, reaction-only +0.120) and 'Criteria' (+0.149 vs control +0.094)
- docs/alpha-pooled-short-v1-2026-10-20.md 'Cohort v3' (shorts_retreat +0.020R, t 3.40; failed selection at +0.006R per CLAUDE.md summary)
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)' (ridge fold 3 then holdout −0.293R)
- Shrinkage arithmetic: ~/agentic-trader-research/holistic-review-20261021/pooled64.py

**Recommendation**

1. Freeze one composite before reading anything new.
   - Inputs: the equal-weight (or OSAP-t-weighted) mean of sector-demeaned cs_zscores of 8–15 characteristics computable from current data. Candidates: short-term reversal, 12-1 momentum, 52-week-high proximity, PEAD reaction, Amihud illiquidity, low volatility/BAB, short ratio, insider net buying.
   - Signs and weights come only from published sources (Chen–Zimmermann OSAP, JKP), never from the 64 observed t's.
   - Test it as one hypothesis on discovery and selection pooled, using the information-test statistics above. Then spend 2024-01→2026-07 on it alone.
   - Arithmetic: 8 signals of IC 0.015 with mean pairwise ρ 0.2 give a composite IC of about 0.027, 1.8× a single signal.

2. Report every formula and theme as a hierarchical posterior (themes as clusters, in JKP's style). Use the posterior P(edge > cost) for probe eligibility.

3. Require PBO/CSCV for any searched or learned model.

4. Benchmarks on the same cube and time-interval holdout: Qlib Alpha158 + LightGBM, and a Gu–Kelly–Xiu-style ridge/NN on the same characteristics.

5. Lower the merge cost: allow multi-source formulas through a single field-source registry.

**North-star impact**

This is the most plausible route from research to a cross-sectional card source with real, if small, information: one trial instead of eight, a 1.8× higher IC than a single signal, and shrunk estimates that do not overstate EV on cards.

**References**

- Gu, S., Kelly, B. & Xiu, D. (2020). Empirical Asset Pricing via Machine Learning. Review of Financial Studies 33(5) — Predictability lives in combinations of many weak characteristics (https://doi.org/10.1093/rfs/hhaa009)
- Jensen, T., Kelly, B. & Pedersen, L.H. (2023). Is There a Replication Crisis in Finance? Journal of Finance 78(5) — Hierarchical Bayesian shrinkage of factors within themes; global factor data (https://jkpfactors.com)
- Chen, A. & Zimmermann, T. (2022). Open Source Cross-Sectional Asset Pricing. Critical Finance Review 11(2) — Signal code, published t-stats and long-short returns for about 200 predictors, used as ex-ante signs and weights (https://www.openassetpricing.com)
- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. (2017). The Probability of Backtest Overfitting. Journal of Computational Finance 20(4) — CSCV/PBO for any searched model (the ridge ranker case)

---

## One 20-session long bracket labels every hypothesis: the horizon and event-encoding mismatches are measured in-house, and 'the bracket is closed' was generalised from a test of native exits

_area: research_methodology · kind: defect · impact: medium · effort: M · confidence: high_

**Claim**

Now:
- Every pooled protocol labels the same trade: a 10:35 entry, a 2×ATR14 stop, a 3R target and 20 sessions (cube.py:284-295; identical 'bracket' blocks in cohort3-daily-v1.json, short-v1.json, events-v1.json, intraday-v1.json and insider-v1.json).
- Events are encoded as daily top-decile baskets with no hold-skipping, compared with all eligible names (formula.py:195-215). PEAD's passing design instead entered once at D+2 against an every-reporter control.

In-house evidence of horizon mismatch:
- PEAD's own diagnostics: a 60-session hold earns +0.252R against +0.149R at 20 sessions.
- insider-v1's net_buying cites Lakonishok–Lee's six-month effect.
- reversal_5, the short-ratio fields and first-hour momentum are 1–5-session effects.

How the bracket question was closed:
- The cohort-v3 doc concluded 'the constant across every negative is the bracket' and named it the next test.
- The bracket counterfactual then varied only exit arms on native setups, with the same entry and stop.
- 'Bracket closed as an explanation' was carried back to the lane, although the lane's horizon, stop width and event encoding were never tested.

**Evidence**

- agentic_trader/research/pooled/cube.py:284-295 (one long bracket per cell)
- config/research/pooled/{cohort3-daily-v1,short-v1,events-v1,intraday-v1,insider-v1}.json 'bracket' (identical)
- agentic_trader/research/pooled/formula.py:195-215 (daily basket, no hold-skipping)
- docs/apriori-pead-2026-09-26.md 'Diagnostics' (60-session hold +0.252 vs +0.149)
- docs/alpha-pooled-mining.md insider section frozen set table (net_buying: 'outperform over six months')
- docs/alpha-pooled-cohort3-2026-10-16.md 'Reading' (bracket named as the next test)
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' (arms share entry and planned stop; native setups only)

**Recommendation**

1. Cube v2, rebuilt from the cached bars: store forward returns at 1, 5, 20 and 60 sessions, with cost in bps and β-hedged, plus the risk arrays.

2. Each protocol predeclares its horizon from its source paper: reversal and flow at 1–5 sessions, PEAD at 60, insider at 60–120.

3. Add an event-entry selection rule to CampaignProtocol.select: enter on the first session the condition becomes true, skip names already held, and compare against a control of names with the same event type (for example every Form-4 purchaser, every reporter).

4. Use bracket labels only in the implementation stage, with the live card policy.

**North-star impact**

Hypotheses get tested at the horizon where the literature says their information is. PEAD-type and insider-type signals could then pass a fair test and arrive as cards with a matching time exit (session_count_v1 already exists live).

**References**

- Bernard, V. & Thomas, J. (1989). Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium? Journal of Accounting Research 27 — PEAD accrues over about 60 trading days, matching the in-house +0.252R at a 60-session hold
- Lakonishok, J. & Lee, I. (2001). Are Insider Trades Informative? Review of Financial Studies 14(1) — Insider signals predict over 6–12 months, not 20 sessions
- Cohen, L., Malloy, C. & Pomorski, L. (2012). Decoding Inside Information. Journal of Finance 67(3) — Opportunistic-trade signal and its monthly horizon

---

## The live card record behind card_policy is a censored, repeat-weighted, raw-beta estimator; fix the estimator before any policy acts on it

_area: research_methodology · kind: defect · impact: high · effort: M · confidence: high_

**Claim**

Now: _key_stats keeps mature rows only. For each (strategy, direction) it averages raw r_cost, with:
- no market-excess column;
- no de-duplication of repeated setups;
- no floor on the risk unit or winsorisation;
- no timeframe in the key (card_stats.py:43-62, 88-117).

card_policy withholds a card when that mean falls below a threshold (execution/card_policy.py:37-55).

On a 2R bracket stops resolve faster than targets, so early snapshots over-weight stops. The live map measured the card line for TREND_PULLBACK long:
- −0.376R on 34 mature rows (2026-10-08);
- +0.535R on 67 mature rows (2026-10-21);
- the 34 rows that matured in between resolved 28 targets and 6 stops.

The replay prior on 8,768 setups is +0.039R after cost and −0.110R against SPY. With an enforce threshold of 0, the policy would have flipped from withhold to send on censoring alone.

**Evidence**

- agentic_trader/research/setups/card_stats.py:43-62 (CardKeyStats fields), 88-117 (_key_stats: mature only, raw r_cost mean)
- agentic_trader/execution/card_policy.py:37-55
- Live map Q1 snapshots (production card_stats_snapshot events), maps/live.md §4
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' (replay prior)
- docs/card-evidence.md 'Limits' (repeated setups, proxy bracket)

**Recommendation**

Make card_stats v2 an estimator, and let card_policy read only its posterior.

1. Outcome probabilities: a competing-risks Aalen–Johansen estimate of the cumulative incidence of target, stop and timeout, treating immature rows as censored rather than dropped. EV = Σ_k CIF_k × mean R_k.
2. One row per (contract, strategy, timeframe, direction, session).
3. Outcome: β-adjusted, cost-symmetric excess with a floor on the risk unit.
4. Shrinkage toward the replay prior: a beta-binomial on the target rate and a normal prior on excess, weighted by effective n.
5. If the policy reads daily, use anytime-valid confidence sequences.

Move card_policy to preview on the posterior P(excess > 0), not on the raw mean.

**North-star impact**

The one outcome-derived gate on cards stops swinging between −0.38R and +0.54R in eight days and becomes safe to run in preview and then enforce. Withholding then reflects evidence, not censoring.

**References**

- Aalen, O. & Johansen, S. (1978). An Empirical Transition Matrix for Non-homogeneous Markov Chains Based on Censored Observations. Scandinavian Journal of Statistics 5 — Competing-risks cumulative incidence under censoring (lifelines implements it)
- Howard, S., Ramdas, A., McAuliffe, J. & Sekhon, J. (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. Annals of Statistics 49(2) — Valid inference when a policy peeks at the estimate every day

---

## Decompose the native trigger against a same-name random-time placebo and a same-session pool, not only against SPY at β=1

_area: research_methodology · kind: gap · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- The bracket counterfactual's only control is SPY over each trade's own window, scaled to R at β=1 (outcomes.py:186-219).
- The setup study compares scorers within the set of setups.

Neither separates three things:
(i) timing: does the EMA/RSI trigger pick better sessions than random sessions in the same name with the same geometry?
(ii) selection: do the 159 static names plus the dynamic names beat the eligible pool?
(iii) β exposure.

The desk has already used a same-name placebo once: exdiv, +0.069R against a placebo of +0.046R. It also has the cached SIP replays: 8,768 setups for 2021–26 and 8,119 for 2017–21.

**Evidence**

- agentic_trader/research/setups/outcomes.py:186-219 (beta-one SPY control)
- docs/bracket-counterfactual-2026-10-18.md 'Rules' and 'Descriptives'
- docs/apriori-exdiv-2026-10-12.md (same-name mid-quarter placebo design)
- docs/setup-outcomes-2026-09-23.md 'Selection (H3)' (scorer comparisons within setups only)

**Recommendation**

Predeclare a native decomposition protocol on the existing caches, as a declared second use, with three paired arms:
- T: the live triggers.
- P: the same symbol and geometry (the trigger's stop as a % of price, 2R target), entered at a random 10:35 or 14:35 instant within ±20 sessions on which the daily trend filter holds.
- U: a random eligible name in the same session with the same geometry rule.

Measurement: excess return with an ex-ante β (60 sessions before entry), cost in bps, a floor on the risk unit, and a session-block bootstrap.

Readings: the timing edge is T−P and the selection edge is P−U. If T−P ≤ 0, stop tuning the trigger and use the natives only as a tradeable-candidate set for a cross-sectional ranker.

**North-star impact**

This identifies which part of the native card stream, if any, adds value. It decides whether to keep spending card slots on trigger-based setups or to move them to ranked names.

**References**

- Brock, W., Lakonishok, J. & LeBaron, B. (1992). Simple Technical Trading Rules and the Stochastic Properties of Stock Returns. Journal of Finance 47(5) — Bootstrap and placebo nulls for technical entry rules
- Sullivan, R., Timmermann, A. & White, H. (1999). Data-Snooping, Technical Trading Rule Performance, and the Bootstrap. Journal of Finance 54(5) — Shows technical-rule gains vanish under correct nulls; the reality-check framework

---

## Cohort membership is look-ahead for the discovery window, the survivorship spike misread that window, and the one passing leg (PEAD) carries an unpriced consensus-vintage contamination

_area: data · kind: defect · impact: medium · effort: L · confidence: medium_

**Claim**

Now: cohort v2 membership is 'listed and active on 2026-09-17; ranked on SIP raw dollar volume of the 60 sessions ending 2023-12-29; not point-in-time' (cohort-v2.json:4). Cohort v3 ranks the same 2023 snapshot. Discovery is 2016-08-01→2021-12-31.

So discovery membership is conditioned on surviving to 2026 and being liquid in 2023. Pairing cancels a uniform survivor premium but not formula-specific selection:
- 2016–21 losers that later died are absent, which matters for reversal.
- Names that became large are over-represented, which matters for momentum and illiquidity.

The survivorship spike says 'Cohort v2/v3 discovery runs 2021-06 → 2026-01' and recommends not building the repair. Under the actual window:
- 152–310 now-dead names a year above the $5M floor (2016–2021) are in Alpaca's inactive list;
- 97% of non-OTC inactive names have SIP bars.
The free repair therefore covers exactly the discovery years.

Separately, the Nasdaq pages give today's consensus for past quarters, so surprise fields are contaminated by later revisions. PEAD's 'surprise ≥ 5%' filter is therefore not point-in-time. Its point-in-time-clean reaction-only rule earned +0.120R against a control of +0.094R, about half the passing edge.

**Evidence**

- config/research/pooled/cohort-v2.json:4 ('survivorship' field)
- config/research/pooled/cohort3-daily-v1.json 'windows' (discovery 2016-08-01..2021-12-31)
- docs/survivorship-spike-2026-10-20.md 'What it means for the desk' (window misstated) and 'What was run' item 2 (per-year counts; 2,147 of 2,209 with bars)
- docs/alpha-data-augmentation-2026-10-18.md §3 'Point-in-time earnings'
- docs/apriori-pead-2026-09-26.md 'Diagnostics' (reaction-only +0.120; control +0.094)

**Recommendation**

1. Cohort v4:
   - Eligibility is liquidity at D−1, point in time, over active and inactive Alpaca assets, with no frozen 2023 ranking.
   - Make it safe against ticker reuse: asof-bound bar requests and CIK-keyed identity.
   - Use EDGAR Form 25 and Form 15 for 2022 onward.
   - Rebuild the cube from cache plus the dead names.
   - Re-read the composite's discovery on it, not the old formulas.

2. Before the PEAD probe term is renewed (it expires 2026-10-28):
   - journal a declared second look at the reaction-only (point-in-time-clean) variant;
   - judge the probe on the counterfactual label of every eligible event (research/apriori/probe_outcomes.py), not on taps.

**North-star impact**

Any composite or PEAD-type card source would be judged on a population that could actually have been traded at the time, so its card EV is not inflated by survivorship or revised consensus.

**References**

- Shumway, T. (1997). The Delisting Bias in CRSP Data. Journal of Finance 52(1) — Missing delisting returns bias anomaly estimates, especially for losers
- Hou, K., Xue, C. & Zhang, L. (2020). Replicating Anomalies. Review of Financial Studies 33(5) — Sample construction (microcaps, breakpoints, weighting) changes most anomaly conclusions (https://doi.org/10.1093/rfs/hhy131)

---

## Govern the last historical confirmation window and add forward scoring, with honest power: forward data can confirm about 0.05R in about 4 years but cannot discover 0.03R

_area: research_methodology · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

Now:
- Consumption of the confirmation window is lane-wide and irreversible. It happens whenever selection freezes any candidate, whether or not confirmation passes ('Window consumption is the irreversible step').
- Null fixed-set replicates carried candidates in up to 3 of 40 and reached confirmation in 1 of 40 (intraday).
- The overlap rule lists only the two pooled literature entries (campaign_run.py:107), although PEAD v2 and exdiv v1 already read 2024–2026.
- No frozen formula is scored forward after its campaign.

My arithmetic at the median standard error (0.017R over 1,357 sessions, about 0.04R per year): forward-only detection at one-sided 5% and 80% power takes about
- 10.7 years for 0.03R;
- 3.8 years for 0.05R;
- 1.5 years for 0.08R.

So a forward scorer is a confirmation and decay monitor, not a discovery engine. Claims elsewhere that it fixes cadence and power should be read with this in mind.

**Evidence**

- docs/alpha-pooled-mining.md 'Window consumption is the irreversible step' and 'Campaign protocol and stages' (lane-wide consumption)
- agentic_trader/research/pooled/campaign_run.py:107 (LITERATURE_ENTRIES = high52-v1, reversal-lowmax-v1)
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Check C' (3 carried); docs/alpha-pooled-intraday-v1-2026-10-14.md check C table (1 reached confirmation)
- docs/apriori-pead-2026-09-26.md 'Protocol' (window through 2026-07-31)
- Arithmetic: ~/agentic-trader-research/holistic-review-20261021/pooled64.py (prospective block)

**Recommendation**

1. Spending rule: reserve the 2024-01→2026-07 window for the predeclared composite. Give every other campaign a discovery-only mode that cannot consume it.

2. Generalise the overlap rule to any journaled study whose inspected interval overlaps: PEAD, exdiv and setup outcomes.

3. Add a daemon worker on the daily-panel pattern:
   - each session, score every frozen formula and the composite at D−1;
   - journal the picks and label them with the existing labeller;
   - report e-value or confidence-sequence status and decay per formula.

4. Use that worker to confirm composites (δ ≥ 0.05R) and to retire decayed signals, never to promote single small-edge formulas.

**North-star impact**

The one clean historical test is not lost to noise, and mining gets a visible forward cadence whose limits are clear in advance.

**References**

- Ramdas, A., Grünwald, P., Vovk, V. & Shafer, G. (2023). Game-Theoretic Statistics and Safe Anytime-Valid Inference. Statistical Science 38(4) — E-values for continuously monitored forward evidence
- Howard, S., Ramdas, A., McAuliffe, J. & Sekhon, J. (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. Annals of Statistics 49(2) — Confidence sequences for the forward scorer

---

## Per-symbol qualification deflates the frozen finalist's holdout by the lifetime trial family, a double count that makes promotion near-impossible; fix it in validation policy v2 or retire the lane explicitly

_area: research_methodology · kind: defect · impact: medium · effort: M · confidence: high_

**Claim**

Now: _family_dsr takes the global family/all trial_count and the per-timeframe Sharpe variance (promotion.py:291-302; storage/alpha.py:239-251). It is applied to validation (promotion.py:353) and also to the one-shot holdout of a single pre-frozen candidate (promotion.py:370, gate holdout_dsr).

The deflated Sharpe ratio corrects the maximum of N in-sample Sharpe ratios. A holdout of one frozen strategy involves no selection, so deflating it counts selection twice.

Measured consequences:
- 0/64 planted signals accepted (docs/alpha-power-diagnosis-2026-09-18.md).
- About 4.9 annualised Sharpe needed on a 252-bar holdout with the frozen family (7,065 trials).
- Every new research charge raises the bar for all future candidates.
- The research-only holdout comparator recovered 12–28 of 64 planted signals.

**Evidence**

- agentic_trader/research/alpha/promotion.py:291-302 (_family_dsr), 353 (validation), 370 (holdout)
- agentic_trader/storage/alpha.py:239-251 (global trial_count into consumption)
- docs/alpha-power-diagnosis-2026-09-18.md (0/64 planted accepted)
- docs/alpha-stack-survey-2026-09-18.md (A1b comparator 12–28/64), per alpha_pipeline map
- docs/alpha-study-protocol.md (frozen family 7,065 trials, variance 0.002876), per alpha_pipeline map

**Recommendation**

Validation policy v2, as a new version id that leaves old evidence untouched:
- Control selection family-wise on validation only, using DSR at an effective trial count (from clustering trials by return correlation) or Hansen SPA/Romano–Wolf through the existing arch dependency.
- Test the holdout with a one-sided block-bootstrap or HAC test of the frozen finalist, in R per trade, with Holm across the holdouts consumed in a declared campaign.
- Report PBO from CSCV as a diagnostic.

Make the confirmatory planted-power run the acceptance test for v2.

If the per-symbol lane will not be revived (the miner is retired), record its retirement so that 0/64 is not read as evidence about alpha.

**North-star impact**

The only registry route from a DSL formula to a card becomes reachable under honest error control, or is closed explicitly so effort goes to the cross-sectional route.

**References**

- Bailey, D. & López de Prado, M. (2014). The Deflated Sharpe Ratio. Journal of Portfolio Management 40(5) — DSR deflates the selected maximum of N trials, not an out-of-sample test of one frozen strategy
- Romano, J. & Wolf, M. (2005). Stepwise Multiple Testing as Formalized Data Snooping. Econometrica 73(4) — Family-wise selection control on validation
- Hansen, P.R. (2005). A Test for Superior Predictive Ability. Journal of Business & Economic Statistics 23(4) — SPA test, an alternative selection-stage control

---

## The binding research resource is calendar time (5.4 discovery years on Alpaca SIP); buy long survivorship-free history or borrow it through published factor returns

_area: data · kind: integration · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- Alpaca SIP daily history starts in 2016, so every pooled discovery window is 2016-08→2021-12 (about 1,357 sessions).
- The lane's own research doc says 'The resource that limits this lane is calendar time and trials, not names', and that widening the universe adds same-day correlation faster than information.
- Paid point-in-time data (Sharadar, Norgate, EODHD) is considered only for survivorship, 'only if #5 fails', not for power.

t scales with √years. 1998–2021 instead of 2016–2021 gives about 2.1× t for daily-bar formulas and the composite. Separately, published long-run factor returns already contain decades of evidence the desk is trying to rediscover on 5 years.

**Evidence**

- docs/alpha-data-augmentation-2026-10-18.md table 'Bars' (daily from 2016) and row 11 (paid PIT vendors, 'only if #5 fails')
- docs/alpha-pooled-mining-research-2026-09-28.md line 119 (calendar time is the limiting resource)
- config/research/pooled/cohort3-daily-v1.json 'windows'

**Recommendation**

1. Price a survivorship-free daily dataset with delisted names and point-in-time membership: Sharadar SEP/SF1 via Nasdaq Data Link or Norgate, or CRSP if academic WRDS access exists.
   - Use it for daily-feature discovery of the composite and its cross-sectional statistics from about 1998.
   - Keep hourly bracket labels on Alpaca for the implementation stage.

2. Use free published factor-return libraries as priors and decay estimates instead of rediscovering:
   - OSAP's about 200 predictors (monthly long-short returns plus code);
   - JKP's 153 global factors;
   - Ken French's daily FF5 and momentum, for β/style hedging of labels.

3. Set the composite's signs and weights and the ex-ante yield gate from these sources.

**North-star impact**

Roughly doubles detection power for the composite without any new search. Card-source decisions then rest on decades of evidence rather than one 5-year window in a rising market.

**References**

- Chen, A. & Zimmermann, T. Open Source Asset Pricing (data and code) — About 200 predictors with long-short returns and post-publication periods, used as priors (https://www.openassetpricing.com)
- Jensen, Kelly & Pedersen global factor data — 153 factors with themes for hierarchical shrinkage (https://jkpfactors.com)
- Kenneth R. French Data Library — Daily FF5 and momentum factors for residual labels and card attribution beyond SPY (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)


# Lens: literature (14 findings)

## Stop opening one-anomaly-per-lane pooled campaigns without an ex-ante yield check: the replication literature predicts the 64 nulls

_area: research_methodology · kind: stop_doing · impact: high · effort: S · confidence: high_

**Claim**

Now: each new data source becomes its own fixed set of about 8 single literature formulas. Each is scored long-only as a top-decile daily basket against the equal-weighted eligible pool on one 20-session 2xATR14/3R bracket, on liquid cohorts, with a discovery gate of t >= 3 (insider-v1.json bracket/selection; research/pooled/formula.py:195-215; research/pooled/stats.py:54-105). 64 formula-cohort pairs produced no finalist. The two literature entries' paired edges were +0.012R and +0.008R. The literature predicts magnitudes of this size:
- Hou-Xue-Zhang: 65% of 452 anomalies fail |t| >= 1.96 once microcaps are mitigated (NYSE breakpoints, value weights), and 82% fail at 2.78.
- McLean-Pontiff: returns are 58% lower after publication.
- Chen-Velikov (JFQA 2023): the average anomaly nets 4 bp/month after spreads, publication and the post-2000s trading regime; the strongest net about 10 bp, and combinations about 20 bp.
- Stambaugh-Yu-Yuan: mispricing profits sit in short legs.
- Avramov-Cheng-Metzker: ML profits attenuate sharply once microcaps and distressed stocks are excluded.

My arithmetic: the median risk unit is 4.2% of price (docs/apriori-exdiv-2026-10-12.md:53-54), so 100 bp is about 0.24R. The lane detects 0.08R about 84% of the time (docs/alpha-pooled-checks-v4-2026-10-07.md:25-27); the pooled map's re-derived MDE is about 0.065R, roughly 27 bp per trade versus the control.
- Chen-Velikov's 4-20 bp/month long-short, with at most half on the long leg, is about 0.005-0.024R: 3-13x below the MDE.
- Pre-publication single-paper magnitudes, after a 58% haircut and halved for the long leg, land near 0.03-0.05R: CMP opportunistic insiders (82 bp/month value-weighted long-short), Xing-Zhang-Zhao smirk (10.9%/yr), Frazzini-Lamont premium (at least 7%/yr).

A null from this design therefore says almost nothing about the literature's effects. The next queued lane (XBRL fundamentals conditioning, docs/alpha-data-augmentation-2026-10-18.md section 4, item 4) is predicted to repeat the pattern. Five of the last ten PRs (#120, #121, #122, #126, #128) were such lanes.

**Evidence**

- config/research/pooled/insider-v1.json (bracket: 20 sessions, 2xATR14, 3R; selection top_fraction 0.1)
- agentic_trader/research/pooled/formula.py:195-215
- agentic_trader/research/pooled/stats.py:54-105
- docs/alpha-pooled-literature-2026-09-30.md 'Criteria' (paired +0.012R / +0.008R)
- docs/alpha-pooled-short-v1-2026-10-20.md (64 formula-cohort pairs, no finalist)
- docs/apriori-exdiv-2026-10-12.md:53-54 (median risk unit 4.2% of price)
- docs/alpha-pooled-checks-v4-2026-10-07.md:25-27 (detection 0.40 at 0.05R, 0.84 at 0.08R)
- docs/alpha-pooled-mining-research-2026-09-28.md:51,119 ('expected to produce few discoveries')
- docs/alpha-data-augmentation-2026-10-18.md section 4 (next lane: fundamentals)
- git log: 15e6939 (#120), d6fd22e (#121), fe10fc6 (#122), 08a98ba (#126), db82817 (#128) are pooled lanes

**Recommendation**

Recommend:
1. Do not freeze a new pooled field-source lane, and do not approve its campaign, unless a one-page ex-ante yield check written before freezing shows E[edge_R] >= the protocol's per-formula MDE. Define E[edge_R] = published long-short monthly spread x (hold sessions / 21) x (0.42 if the paper's sample predates publication) x long-leg share (default 0.5; lower when the paper reports short-leg concentration) / median risk-unit %.
2. Send sources that fail the check to a prospective scorer, which spends no window, or drop them.
3. Redirect lane effort to:
   - designs that lower the MDE (matched controls, finding 4);
   - event effects with large per-event magnitudes (finding 3);
   - one combined trial (finding 7).
4. Apply the check to the pending insider campaigns first (finding 2).

**North-star impact**

Each lane costs about one subagent-driven development (SDD) day, 3-10 h of compute and an operator approval, and none has reached a card. Stopping low-prior lanes frees that capacity for selection, events and evidence work that changes the 1-2 daily cards. It also protects the single-use 2024-26 confirmation window from a null survivor.

**References**

- Hou, Xue & Zhang (2020), Replicating Anomalies, Review of Financial Studies 33(5):2019-2133 — 65% of 452 anomalies fail |t|>=1.96 with microcaps mitigated; 82% at the 2.78 multiple-test hurdle; replicated magnitudes much smaller (https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html)
- Chen & Velikov (2023), Zeroing in on the Expected Returns of Anomalies, JFQA 58(3):968-1004 — Average anomaly nets 4 bp/month after spreads, publication and modern trading; strongest ~10 bp; combinations ~20 bp, which sets the ex-ante prior in R (https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/zeroing-in-on-the-expected-returns-of-anomalies/945133D5A3ECEEAF466AEE91551FD225)
- McLean & Pontiff (2016), Does Academic Research Destroy Stock Return Predictability?, Journal of Finance 71(1) — 26% out-of-sample and 58% post-publication decline across 97 predictors; the haircut in the yield formula (https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365)
- Stambaugh, Yu & Yuan (2012), The short of it: Investor sentiment and anomalies, JFE 104(2):288-302 — Anomaly mispricing sits in short legs; long-only desks capture less than half of long-short spreads (https://www.nber.org/papers/w16898)
- Avramov, Cheng & Metzker (2023), Machine Learning vs. Economic Restrictions: Evidence from Stock Return Predictability, Management Science (doi 10.1287/mnsc.2022.4449) — Excluding microcaps, distressed stocks or high-volatility episodes considerably attenuates ML profitability; trading costs erode it further (https://papers.ssrn.com/abstract=3450322)

---

## The pending insider campaigns do not test Cohen-Malloy-Pomorski as cited, and the short-volume lane measured a different quantity from its cited papers

_area: research_methodology · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

Now, insider lane:
- insider_opp_buy_rel marks a purchase as routine when the same owner filed a purchase in the same issuer in the same calendar month of the previous year (agentic_trader/research/pooled/insider_fields.py:26-27, 230-233). An owner with no history is therefore 'opportunistic' by default.
- insider-v1.json:204-206 cites Cohen, Malloy & Pomorski (2012) for opportunistic_buying.
- CMP actually classify per insider at the start of each calendar year. They require at least one trade in each of the three preceding years, and call an insider routine if they traded in the same calendar month for at least three consecutive years. Insiders without that history fall into neither bucket (NBER w16454, p.11; verified in the text).
- CMP's opportunistic long-short earns 82 bp/month value-weighted (t 2.15), against -20 bp for routine traders. Opportunistic trades predict returns in both the top and bottom half of market cap (Table IX). Lakonishok-Lee find the information is in purchases and concentrated in smaller firms.
- So a null on insider-v1 or cohort3-insider-v1, whose campaigns await approval (docs/alpha-roadmap.md:627), is not evidence against CMP, and a pass would not replicate CMP.

Now, short-volume lane:
- short-v1 cites Boehmer-Jones-Zhang 2008 (NYSE proprietary order data: heavily shorted stocks underperform by 1.16% over 20 days) and Diether-Lee-Werner (short-v1.json:156-166).
- It reads FINRA's CNMS daily file instead (research/short_volume.py:42). FINRA's 5/10/19 notice says that file covers only off-exchange prints and is not consolidated with exchange volume. It also counts a dealer's facilitating short sale against a customer's long sale, so the ratio overstates shorting: 20% off-exchange-only versus 10.7% consolidated in FINRA's example.
- The prior did not transfer, and the lane closed with no finalist (docs/alpha-pooled-short-v1-2026-10-20.md).

Now, events lane: events-v1's quiet_momentum cites Jegadeesh-Titman for a 20-session return (events-v1.json:186-188). See finding 6.

**Evidence**

- agentic_trader/research/pooled/insider_fields.py:26-27,230-233 (one-year, per-purchase routine rule)
- config/research/pooled/insider-v1.json:204-206 (cites CMP)
- config/research/pooled/short-v1.json:156-166 (cites BJZ 2008, DLW 2009)
- agentic_trader/research/short_volume.py:42 (CNMSshvol off-exchange file)
- docs/alpha-pooled-mining.md:1040-1041 (acknowledges off-exchange only, but priors unchanged)
- config/research/pooled/events-v1.json:186-188
- docs/alpha-roadmap.md:627 (insider campaigns pending approval)

**Recommendation**

Recommend:
1. Before approving either insider campaign, choose one of two paths:
   - freeze insider-v2 with CMP's rule: owner-level annual classification, a three-year history requirement, unclassifiable insiders excluded, open-market P purchases only;
   - or state in the v1 record that it uses 'a one-year routine filter, not CMP' and lower its prior.
2. Treat cohort v3 as the primary insider cohort, given Lakonishok-Lee's small-firm concentration. Use an event-entry rule (first session after a fresh opportunistic purchase is visible, skip names already held, control = names with a non-opportunistic purchase that session) rather than a daily decile.
3. Make a 'literature fidelity table' mandatory in every frozen protocol, reviewed before the SHA is pinned. It lists, for paper and implementation side by side: variable definition, data source, population, horizon, weighting and sign, and each deviation.
4. If short flow is revisited, BJZ/DLW-like data are exchange-reported short volume (FINRA notes the exchanges publish theirs, some for a fee) or bi-monthly short interest, not the TRF file.

**North-star impact**

The next operator approvals, and possibly the lane-wide confirmation window, would otherwise go to mis-specified tests. A CMP-faithful insider leg is one of the few literature effects documented in large caps at a one-month horizon that fits a long-only card.

**References**

- Cohen, Malloy & Pomorski (2012), Decoding Inside Information, Journal of Finance 67:1009-1043 (NBER w16454) — Routine = same calendar month for >=3 consecutive years with >=1 trade in each of the prior 3 years, classified per insider each year; opportunistic long-short 82 bp/month value-weighted (t 2.15); works in both size halves (https://www.nber.org/papers/w16454)
- Lakonishok & Lee (2001), Are Insider Trades Informative?, Review of Financial Studies 14(1):79-111 — Information is in purchases, not sales, and mainly in smaller firms (https://ideas.repec.org/a/oup/rfinst/v14y2001i1p79-111.html)
- Boehmer, Jones & Zhang (2008), Which Shorts Are Informed?, Journal of Finance 63(2):491-527 — Built on proprietary NYSE system order data: heavily shorted stocks underperform by 1.16% over 20 days, a different variable from FINRA TRF short volume (https://ideas.repec.org/a/bla/jfinan/v63y2008i2p491-527.html)
- FINRA Information Notice 5/10/19, Understanding Short Sale Volume Data — The Daily File is off-exchange only, not consolidated, and includes facilitating short sales that offset customer long sales (example: 20% vs 10.7%) (https://www.finra.org/sites/default/files/2019-07/information-notice-051019.pdf)

---

## Earnings-announcement premium: a long-only, days-long effect, strong in large caps, that the 7-day earnings blackout structurally excludes

_area: signals · kind: research_direction · impact: high · effort: M · confidence: medium_

**Claim**

Now:
- Every equity candidate is refused when its next report falls within [0, 7] days (config/config.yaml:279; risk/rules.py:376-384). The evaluator applies this with no catalog or probe exemption (agent/evaluator.py:543-571), so the desk can never hold a long into a scheduled announcement.
- Its only earnings source is post-event PEAD, entered on the second session after the report (docs/apriori-pead-2026-09-26.md header). PEAD is hard-coded into run_scan (agent/copilot.py:897-948).

Literature:
- Frazzini & Lamont (NBER w13090): prices rise around scheduled announcements, with monthly strategies earning 7-18%/yr. The premium is 'strong in large capitalization stocks'. The abnormal return is about 25 bp over the 10 trading days before the announcement, 21 bp in the 3-day window and 30 bp after, each with t > 7. Announcement dates are predicted from the prior year's announcements, so there is no look-ahead.
- Barber et al. (2013): the premium exists globally, significant in 9 of 20 countries.
- Savor & Wilson (2016): announcers earn 9.9% annualized abnormal returns.
- Martineau (2022): PEAD has been absent in large stocks since 2006. That points to the pre- and at-announcement legs, rather than post-drift, as the better large-cap bet.

My arithmetic: 46 bp (pre-announcement plus window) is about 0.11R gross at the 4.2% risk unit. After a 58% post-publication haircut it is about 0.05R, comparable to PEAD's measured paired edge (+0.055R, SE about 0.021R).

**Evidence**

- config/config.yaml:279 (earnings_blackout_days: 7)
- agentic_trader/risk/rules.py:376-384 (earnings_days_out within [0, blackout_days])
- agentic_trader/agent/evaluator.py:543-571 (no exemption for catalog probes)
- docs/apriori-pead-2026-09-26.md header (PEAD long +0.149R; +0.055R vs control)
- agentic_trader/agent/copilot.py:897-948 (PEAD branch hard-coded)
- agentic_trader/research/apriori/earnings_history.py:1-10 (digest-pinned Nasdaq pages)
- docs/apriori-exdiv-2026-10-12.md:53-54 (risk unit 4.2%)

**Recommendation**

Recommend: freeze an a priori catalog entry `ea-premium-v1` that reuses the PEAD/exdiv machinery.
- **Timing:** predict announcement session E as the same fiscal quarter's report date one year earlier, from the pinned Nasdaq pages (Frazzini-Lamont's method).
- **Trade:** go long at 10:35 on E-5 and exit through a session_count exit at the close of E (before-open reports) or E+1 (after-close reports). Use a 2xATR stop with the labeller's gap-at-open fills, so gap risk is priced into R.
- **Controls:** pair against every eligible non-announcer that session, plus a same-name placebo 30 sessions earlier, as exdiv did.
- **Reads:** declare the overlap with the PEAD and exdiv reads of 2016-2026.

If it passes, two things are needed:
- an operator risk decision exempting EA catalog probes from earnings_blackout;
- a generic catalog-source seam instead of a second PEAD-style branch in run_scan.

Also test any proposed 'exit held positions before earnings' rule (risk map) against this premium before adopting it, since the literature says longs are paid on average to hold through announcements.

**North-star impact**

This would add a second evidence-based, long-only card source whose timing is known days ahead from the calendar. It fits the 10:35 slot and the 1-2 card budget, and uses data the desk already pins.

**References**

- Lamont & Frazzini (2007), The Earnings Announcement Premium and Trading Volume, NBER WP 13090 — 7-18%/yr; strong in large caps; ~25 bp pre-announcement run-up over 10 days, 21 bp in the 3-day window, 30 bp after; dates predicted from prior-year announcements (https://www.nber.org/papers/w13090)
- Barber, De George, Lehavy & Trueman (2013), The earnings announcement premium around the globe, JFE 108(1):118-138 — Out-of-sample, cross-country support: significant in 9 of 20 countries (https://lbsresearch.london.edu/id/eprint/391)
- Savor & Wilson (2016), Earnings Announcements and Systematic Risk, Journal of Finance 71:83-138 — Announcers earn ~9.9% annualized abnormal returns; risk-based explanation, so the premium is less likely to be arbitraged away (https://doi.org/10.1111/jofi.12361)
- Martineau (2022), Rest in Peace Post-Earnings Announcement Drift, Critical Finance Review 11(3-4):613-646 — PEAD non-existent in large stocks since 2006; favours pre- and at-announcement legs over post-drift for liquid names and tempers PEAD probe expectations (https://ideas.repec.org/a/now/jnlcfr/104.00000122.html)

---

## Measure edges against characteristic-matched controls and published factor returns, not an equal-weighted pool or beta-one SPY

_area: research_methodology · kind: improvement · impact: high · effort: M · confidence: high_

**Claim**

Now:
- The pooled paired edge subtracts the equal-weighted mean R of all eligible names each session (research/pooled/stats.py:54-105).
- The cube stores no volatility, beta, size or industry arrays (research/pooled/cube.py:53).
- The setups' 'excess' is SPY's return scaled into the setup's R with beta one (research/setups/outcomes.py:219).
- card_stats and card_policy have no excess field at all (research/setups/card_stats.py:43-62).

Literature:
- Daniel-Grinblatt-Titman-Wermers (DGTW) characteristic-matched benchmarks are the standard way to separate selection from style.
- Ang-Hodrick-Xing-Zhang: the high-idiosyncratic-volatility quintile trails the low quintile by 1.06%/month. Bali-Cakici contest this. Novy-Marx & Velikov 2022 show betting-against-beta returns come mostly from micro caps. Either way, volatility and beta tilts carry return differences that any decile pick inherits.

Unit effect (my arithmetic): in R units, fixed bp costs and market drift both scale with 1/stop%. A 10 bp round trip is 0.067R at a 1.5% stop but 0.017R at a 6% stop. So any R-space feature correlated with volatility partly measures the unit. That could explain part of vol_20's Holm-surviving rho of +0.049, which is measured on R_cost (docs/setup-outcomes-2026-09-23.md:74). It could also explain part of cohort v2's negative mean t (pooled map).

Native longs trail SPY by 0.10-0.15R (docs/bracket-counterfactual-2026-10-18.md:6-7), but nothing says whether that is beta above one, momentum, size or industry.

**Evidence**

- agentic_trader/research/pooled/stats.py:54-105
- agentic_trader/research/pooled/cube.py:53 (_ARRAYS: no risk arrays)
- agentic_trader/research/setups/outcomes.py:219 (beta-one SPY R)
- agentic_trader/research/setups/card_stats.py:43-62 (no excess field)
- docs/setup-outcomes-2026-09-23.md:74 (vol_20 rho +0.049 vs R_cost)
- docs/bracket-counterfactual-2026-10-18.md:6-7
- docs/alpha-pooled-checks-v4-2026-10-07.md 'Label diagnostic' (high-volatility deciles)

**Recommendation**

Recommend:
1. **Matched control in the pooled lane.** Add atr_pct, 60-session beta to SPY, log dollar volume and an FF49 industry (finding 10) to the cube, rebuilt from cached bars. Make the decisive statistic the pick minus a DGTW-style matched cell (industry group x ATR% tercile x dollar-volume tercile) each session, and keep the equal-weighted pool as a reported cross-check. Re-score the 64 frozen formulas descriptively, without spending a window, to see whether the sign pattern changes.
2. **Factor attribution for cards and the setup replay.** Load Ken French's daily FF5 and momentum files and estimate each name's loadings over 250 pre-entry sessions. Report alpha_R = realized R minus the sum of loading x factor return over the hold, in R. Add mean_alpha_r to card_stats and point a card_policy preview at it.
3. **Check vol_20 before it is used.** Recompute vol_20's rho on r_gross and on alpha_R before it enters any selector.

**North-star impact**

Card evidence lines and card_policy would measure selection rather than beta, which the bracket study says is the whole native raw edge. Matched controls also cut the SE of persistent formulas, which makes the composite (finding 7) detectable.

**References**

- Daniel, Grinblatt, Titman & Wermers (1997), Measuring Mutual Fund Performance with Characteristic-Based Benchmarks, Journal of Finance 52(3):1035-1058 — Characteristic-matched benchmark design for the pooled control (https://ideas.repec.org/a/bla/jfinan/v52y1997i3p1035-58.html)
- Ang, Hodrick, Xing & Zhang (2006), The Cross-Section of Volatility and Expected Returns, Journal of Finance 61(1) — High-IVOL quintile -1.06%/month vs low; volatility tilts confound decile picks (https://www.nber.org/system/files/working_papers/w10852/w10852.pdf)
- Novy-Marx & Velikov (2022), Betting against betting against beta, JFE 143:80-106 — Low-beta premia largely from micro caps and construction choices; beta tilts must be controlled, not assumed to be alpha (https://mysimon.rochester.edu/novy-marx/research/BABAB.pdf)
- Kenneth R. French Data Library (daily FF5 2x3 and daily momentum factor files) — Free daily factor returns for alpha_R attribution of cards and replayed setups (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

---

## Use the literature's negative short-horizon predictors as vetoes on native long cards, especially dynamic movers

_area: signals · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- The native book is long-only (config/config.yaml:305,320).
- Up to 20 dynamic names per scan come from Alpaca most-actives ranked by trade count plus top movers (screeners/dynamic_universe.py:315-339). They share one 'dynamic' group (agent/copilot.py:614-617).
- Dynamic names have no replay base rate, because the setup study used the static 159 (docs/setup-outcomes-2026-09-23.md:38). They also get no shadow cross-sectional features (research/setups/ranker.py:245-249).
- setup_features_v2 has no MAX, overnight-gap or news feature (research/setups/features.py:44-53).

Literature, all about long-side underperformers in liquid names:
- Bali-Cakici-Whitelaw: a high maximum daily return over the past month predicts lower returns, with a decile spread above 1%/month after size, momentum, reversal and liquidity controls.
- Berkman et al.: among the 3,000 largest US stocks, overnight gains (+10 bp/day) reverse intraday (-7 bp/day), most strongly in high-attention stocks.
- Chan (2003): extreme moves without news reverse while news-driven moves drift, mainly in smaller stocks.
- Medhat-Schmeling: one-month returns reverse in low-turnover stocks and continue in high-turnover ones.
- Lou-Polk-Skouras: momentum profits accrue overnight, while other strategies earn intraday.

The desk's own pooled negatives point the same way:
- quiet_momentum: -0.119R (t -3.34) on v2, -0.044R on v3;
- overnight_persistence: -0.035R (t -1.93) and -0.016R;
- intraday_high_proximity: -0.013R and -0.045R (t -2.33).

**Evidence**

- agentic_trader/screeners/dynamic_universe.py:315-339 (MostActivesBy.TRADES, movers)
- agentic_trader/agent/copilot.py:614-617
- agentic_trader/research/setups/features.py:44-53 (CROSS_SECTIONAL list)
- agentic_trader/research/setups/ranker.py:245-249
- docs/alpha-pooled-events-v1-2026-10-15.md:70
- docs/alpha-pooled-intraday-v1-2026-10-14.md:71,77
- docs/alpha-pooled-cohort3-2026-10-16.md:83,105,111
- docs/bracket-counterfactual-2026-10-18.md:35-36 (2017-2021 robustness cache exists)

**Recommendation**

Recommend:
1. Add setup_features_v3 and journal it from now on:
   - max_ret_21;
   - entry-day open gap divided by ATR;
   - headline count in the prior 48 h (already fetched each scan);
   - 20-day dollar-volume ratio as a turnover proxy;
   - a dynamic flag.
2. Freeze one predeclared veto set before reading outcomes, for example: veto when MAX is in the cohort's top decile, or when the entry-day gap exceeds 1 ATR with no headline.
3. Test it as the paired change in alpha_R (finding 4) of kept versus vetoed setups, on replayed native setups over a declared window not yet read for these features (for example, the 2017-01 to 2021-04 robustness cache). Add a dynamic-name proxy replay: daily top-N by trade count and by |return|.
4. Ship the veto off, then preview, then enforce, like card_policy.

**North-star impact**

For a long-only desk, the cheapest card-quality gain is not buying names the literature says underperform. It lifts the floor of the 1-2 daily cards without needing new alpha, and it targets the unvalidated dynamic slot directly.

**References**

- Bali, Cakici & Whitelaw (2011), Maxing out: Stocks as lotteries and the cross-section of expected returns, JFE 99:427-446 — High-MAX stocks underperform, decile spread >1%/month, robust to standard controls; a direct long veto (https://ideas.repec.org/a/eee/jfinec/v99y2011i2p427-446.html)
- Berkman, Koch, Tuttle & Zhang (2012), Paying Attention: Overnight Returns and the Hidden Cost of Buying at the Open, JFQA 47(4):715-741 — Overnight gains reverse intraday in high-attention stocks among the 3,000 largest; relevant to gap-up and most-active entries (https://ideas.repec.org/a/cup/jfinqa/v47y2012i04p715-741_00.html)
- Chan (2003), Stock price reaction to news and no-news: drift and reversal after headlines, JFE (doi 10.1016/S0304-405X(03)00146-6) — No-news extreme moves reverse; news moves drift; motivates a news-conditioned veto (https://doi.org/10.1016/S0304-405X(03)00146-6)
- Medhat & Schmeling (2022), Short-term Momentum, Review of Financial Studies 35(3):1480-1526 — One-month reversal in low-turnover stocks, continuation in high-turnover, strongest among the largest stocks; turnover conditioning (https://openaccess.city.ac.uk/id/eprint/31278/)
- Lou, Polk & Skouras (2019), A tug of war: Overnight versus intraday expected returns, JFE 134(1):192-213 — Momentum earns overnight, other strategies intraday; informs gap and entry-time features (https://eprints.lse.ac.uk/87481/)

---

## Re-test reversal as the literature states it: one-month 'quiet' reversal, residual and VIX-conditioned, on horizon-matched labels

_area: alpha_mining · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

Now: events-v1 froze quiet_momentum = ts_sum(returns, 20) outside the earnings window and cited Jegadeesh-Titman (events-v1.json:186-188). In the literature, the one-month return is the short-term reversal signal (Jegadeesh 1990). That is why the desk's own momentum_12_1 skips the last month (campaign-v4.json:82-85).
- **Result:** the formula produced the lane's largest |t|, with the reversal sign: -0.119R (t -3.34) on v2 and -0.044R (t -1.69) on v3 (docs/alpha-pooled-events-v1-2026-10-15.md:70; docs/alpha-pooled-cohort3-2026-10-16.md:83). The results doc records the sign flip only as an unregistered hypothesis (events-v1:76-82).
- **Five-session reversals** were small and positive: quiet_reversal +0.027R (t +1.49) and reversal_5 +0.022R (t +1.44) on v3 (cohort3:84,91).
- **Label horizon:** every short-horizon prior (weekly reversal, overnight, first hour, daily short flow) was labelled with the 20-session 2xATR/3R bracket, which hits its target about 8.6% of the time (docs/alpha-pooled-literature-2026-09-30.md 'Diagnostics'). BracketSpec already parameterises the hold (research/pooled/cube.py:57-71).

Literature:
- Chan 2003: no-news moves reverse.
- Da-Liu-Schaumburg 2014: reversal of the component unexplained by cash-flow news earns about 4x the standard strategy's risk-adjusted return.
- Nagel 2012: reversal returns are liquidity-provision returns, forecastable from VIX and spiking in turmoil.
- Medhat-Schmeling 2022: reversal concentrates in low-turnover stocks.

**Evidence**

- config/research/pooled/events-v1.json:186-188
- config/research/pooled/campaign-v4.json:82-85
- docs/alpha-pooled-events-v1-2026-10-15.md:70,76-82
- docs/alpha-pooled-cohort3-2026-10-16.md:83-84,91
- docs/alpha-pooled-literature-2026-09-30.md 'Diagnostics' (target 8.6%)
- agentic_trader/research/pooled/cube.py:57-71
- docs/apriori-alphas.md:90 ('Part 2: live paper probe', session_count exits)

**Recommendation**

Recommend: ask the operator for one declared second-look fixed set, `reversal-v1`, with at most four literature-signed formulas:
- (a) minus the 21-session return, outside earnings windows;
- (b) minus the 5-session industry-residual return. This is the feasible proxy for Da-Liu-Schaumburg, because analyst revisions are unavailable; it needs finding 10's industries;
- (c) formula (a) only when VIX is above its trailing one-year median (Nagel);
- (d) formula (a) within the lowest dollar-volume-turnover tercile (Medhat-Schmeling).

Label on a new 5-session time-exit cube and on the 20-session cube, declaring each cube as its own trial. Score on the 2022-2023 selection window (touched only by shorts_retreat) and on the prospective scorer, not on the discovery window already read.

Move the first-hour and overnight formulas to a 1-session cube in the same way. Live execution already exists through session_count exits.

**North-star impact**

The best-supported pattern in the lane's own data would get a fair, horizon-matched test. A pass yields a long-only 'buy the quiet dip' card source that complements the trend cards.

**References**

- Jegadeesh (1990), Evidence of Predictable Behavior of Security Returns, Journal of Finance 45(3):881-898 (bibliographic details verified via RePEc listing) — Seminal one-month cross-sectional reversal; the literature sign for a 20-session return
- Da, Liu & Schaumburg (2014), A Closer Look at the Short-Term Return Reversal, Management Science 60:658-674 — Reversal of returns unexplained by cash-flow news earns ~4x the standard strategy; motivates residual reversal (https://ideas.repec.org/a/inm/ormnsc/v60y2014i3p658-674.html)
- Nagel (2012), Evaporating Liquidity, Review of Financial Studies 25(7):2005-2039 — Reversal returns proxy liquidity-provision returns and are predictable with VIX; a predeclared conditioning variable (https://ideas.repec.org/a/oup/rfinst/v25y2012i7p2005-2039.html)
- Chan (2003), Stock price reaction to news and no-news, JFE — No-news extreme moves reverse, which is exactly the 'quiet' condition (https://doi.org/10.1016/S0304-405X(03)00146-6)
- Medhat & Schmeling (2022), Short-term Momentum, Review of Financial Studies 35(3) — Reversal in low-turnover stocks, momentum in high-turnover; predeclared interaction (https://openaccess.city.ac.uk/id/eprint/31278/)

---

## Spend the one historical confirmation window on a pre-registered composite built from Open Source Asset Pricing and JKP definitions

_area: alpha_mining · kind: research_direction · impact: medium · effort: L · confidence: medium_

**Claim**

Now:
- The pooled DSL is single-instrument (research/alpha/operators.py:108-167; per-symbol scoring in research/pooled/formula.py). cross_sectional_rank, cross_sectional_zscore and group_neutralize already exist, but outside the grammar (research/alpha/panel.py:19,24,29,132).
- Every formula is a standalone trial. The 2024-01 to 2026-07 confirmation window is lane-wide and single-use (docs/alpha-pooled-mining.md:615-621).
- setup_features_v2 already computes eight cross-sectional characteristics (research/setups/features.py:44-53).

Literature:
- Chen & Zimmermann's Open Source Asset Pricing (OSAP) reproduces 319 characteristics with open code and data; 98% of originally clearly-significant predictors reach t > 1.96. Signal code has been in Python since the October 2025 update.
- Jensen-Kelly-Pedersen: most factors replicate and cluster into 13 themes, and related factors strengthen the evidence under Bayesian shrinkage (data CC BY-NC).
- Gu-Kelly-Xiu: every method agrees the dominant predictors are momentum, liquidity and volatility variants, all computable from OHLCV. A value-weighted neural-net decile long-short earned an out-of-sample Sharpe of 1.35.
- Chen-Velikov: combinations net about 20 bp/month, against 4 bp for the average single anomaly.
- Kelly-Malamud-Zhou: complexity helps only with appropriate shrinkage.
- Avramov et al.: much ML profit sits in microcaps and distressed stocks.

My arithmetic from Chen-Velikov: a composite on liquid names should earn about 0.02-0.05R versus the control, at or below today's 0.065R MDE.

**Evidence**

- agentic_trader/research/alpha/operators.py:108-167
- agentic_trader/research/alpha/panel.py:19-37,132
- agentic_trader/research/pooled/formula.py:195-215
- docs/alpha-pooled-mining.md:615-621 (lane-wide single-use window)
- agentic_trader/research/setups/features.py:44-53
- docs/setup-outcomes-2026-09-23.md:80-81 (setup_quality uninformative)
- docs/alpha-data-augmentation-2026-10-18.md section 3 (cross-sectional operators requested again)

**Recommendation**

Recommend:
1. Wire panel.py's cross_sectional_zscore and group_neutralize into pooled scoring as typed operators (cs_z, cs_rank, group_demean).
2. Freeze `composite-v1`: the equal-weighted mean of winsorised, industry-demeaned cross-sectional z-scores of about 10 characteristics, each signed and specified by OSAP's published direction and reference code:
   - momentum 12-1;
   - one-month reversal;
   - MAX;
   - volatility;
   - 52-week high;
   - Amihud illiquidity;
   - PEAD reaction;
   - opportunistic insider buying;
   - turnover change, once XBRL shares outstanding exist.
3. Give a second Holm slot to one LightGBM ranker with frozen hyperparameters, trained walk-forward on discovery data only, with Qlib's Alpha158/LightGBM defaults as the reference.
4. Estimate PBO by CSCV for the ML slot on discovery and require finding 4's matched control. Spend confirmation only if selection passes.
5. Sequence it after finding 4's SE reduction. Either way, use its continuous score as the L3 selector's and the shadow allocator's input.

**North-star impact**

The selector and the allocator lack a single continuous, literature-grounded score; setup_quality carries no information. This is the only mining output with a plausible route to ranking the daily cards, and it spends the irreplaceable window on the most plausible trial.

**References**

- Chen & Zimmermann (2022), Open Source Cross-Sectional Asset Pricing, Critical Finance Review 11(2):207-264; openassetpricing.com data and Python package — 319 replicated characteristics with code; reference spec and sign source for the composite and the fidelity tables (https://www.openassetpricing.com/)
- Jensen, Kelly & Pedersen (2023), Is There a Replication Crisis in Finance?, Journal of Finance 78(5):2465-2518 — Most factors replicate; 13 themes; hierarchical shrinkage instead of flat per-formula gates; public factor data (CC BY-NC) (https://www.nber.org/papers/w28432)
- Gu, Kelly & Xiu (2020), Empirical Asset Pricing via Machine Learning, Review of Financial Studies 33(5):2223-2274 — Dominant predictors are momentum, liquidity and volatility, computable from OHLCV; nonlinear interactions matter; panel ML baseline (https://www.nber.org/papers/w25398)
- Kelly, Malamud & Zhou (2024), The Virtue of Complexity in Return Prediction, Journal of Finance 79(1):459-503 — Large models help only with appropriate shrinkage; supports a regularised ML slot, not unpenalised search (https://www.sfi.ch/en/publications/the-virtue-of-complexity-in-return-prediction)
- Chen & Velikov (2023), JFQA — Combinations net ~20 bp/month vs 4 bp for the average single anomaly; realistic size of the composite (https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/zeroing-in-on-the-expected-returns-of-anomalies/945133D5A3ECEEAF466AEE91551FD225)

---

## Replace fixed t>=3 gates and single-shot windows with power-aware thresholds, alpha-spending and anytime-valid prospective tests

_area: research_methodology · kind: improvement · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- A predeclared fixed set of 8 faces all of these gates: t >= 3 with 3 of 4 positive blocks, a 0.5x selection haircut, Holm, and a positive leg ci90 that depends on 2024-26 market drift (research/pooled/campaign.py:732-739, 789-794, 833-842).
- Result docs give no per-formula MDE or equivalence bound.
- The per-symbol holdout DSR is deflated by the lifetime family/all trial count (research/alpha/promotion.py:351-366,370,388), although a frozen finalist is tested only once.
- The first freeze consumes the whole lane-wide window. Null campaigns reached confirmation in 1/40 fixed-set and 4-5/40 genetic replicates (docs/alpha-pooled-intraday-v1-2026-10-14.md; docs/alpha-pooled-checks-v3-2026-10-05.md).
- The prospective lanes (PEAD probe, daily panel, vol_20 read) have no stopping rule.

Literature:
- Harvey-Liu-Zhu: new factors need t > 3 under data mining.
- Harvey-Liu 2020: calibrate cutoffs by double bootstrap to a target false discovery rate and a target missed-to-false ratio, because flat hurdles lack power.
- A. Chen: false-discovery bounds imply at least 75% of published predictability findings are true (tightest bound at least 91%). For literature-signed formulas the problem is magnitude and decay, not truth.
- The deflated Sharpe ratio corrects selection among trials, so it belongs where search touches the data. PBO/CSCV estimates selection overfitting.
- Howard et al.: confidence sequences stay valid under continuous monitoring.
- Lan-DeMets: alpha-spending allows unplanned interim looks.

**Evidence**

- agentic_trader/research/pooled/campaign.py:732-739,789-794,833-842
- agentic_trader/research/alpha/promotion.py:351-366,370,388
- docs/alpha-power-diagnosis-2026-09-18.md (planted acceptance 0/64)
- docs/alpha-pooled-mining.md:615-621
- docs/alpha-pooled-intraday-v1-2026-10-14.md (check C: null reached confirmation 1/40)
- docs/alpha-pooled-checks-v3-2026-10-05.md (genetic null 4-5/40)

**Recommendation**

Recommend:
1. Every result table reports, per formula, the edge ci90, an MDE (plant delta on that formula's own picks inside check C's null panels) and an equivalence statement such as 'edge < X R at 90%'.
2. For literature fixed sets, protocol v5 (prospective only, never retroactive) sets discovery cutoffs with Harvey-Liu's double bootstrap on check C panels, at a declared FDR (for example 10%) and miss ratio.
3. Scope DSR to selection only. Test a frozen finalist's holdout with a one-sided block bootstrap; this is the A1b comparator, which recovered 12-28/64 planted cases.
4. Write an alpha-spending plan (O'Brien-Fleming-type) across the expected campaigns for the lane-wide window. Open a prospective confirmation interval from 2026-08 onward, scored nightly with confidence sequences. Use the same machinery for the PEAD probe renewal and the vol_20/sector_rel_mom_60 read.

**North-star impact**

This turns 'no finalist' into a bounded statement, lets small real effects accumulate prospectively, and gives mining a daily cadence without burning historical data. That is the cadence the operator asked for.

**References**

- Harvey & Liu (2020), False (and Missed) Discoveries in Financial Economics, Journal of Finance 75(5):2503-2553 — Double-bootstrap cutoffs that balance FDR against missed discoveries; flat hurdles lack power (https://arxiv.org/abs/2006.04269v1)
- Harvey, Liu & Zhu (2016), ...and the Cross-Section of Expected Returns, Review of Financial Studies 29(1):5-68 — Origin of the t>3 hurdle under data mining; appropriate for search, conservative for predeclared priors (https://www.nber.org/papers/w20592)
- Chen, A. Y., Most claimed statistical findings in cross-sectional return predictability are likely true (arXiv 2206.15365) — FDR bounds: >=75% (tightest >=91%) of findings true, so literature priors deserve less-than-maximal skepticism about sign (https://arxiv.org/pdf/2206.15365)
- Bailey & Lopez de Prado (2014), The Deflated Sharpe Ratio, Journal of Portfolio Management 40(5):94-107 — Corrects selection among N trials; should be applied where search touches data, not to a frozen finalist's one-shot holdout (https://papers.ssrn.com/abstract=2460551)
- Bailey, Borwein, Lopez de Prado & Zhu (2017), The Probability of Backtest Overfitting, Journal of Computational Finance 20(4) — PBO via CSCV for the composite's ML slot and any fitted ranker (https://papers.ssrn.com/abstract=2326253)
- Howard, Ramdas, McAuliffe & Sekhon (2021), Time-uniform, nonparametric, nonasymptotic confidence sequences, Annals of Statistics 49(2):1055-1080 — Anytime-valid intervals for nightly prospective scoring and probe renewal decisions (https://projecteuclid.org/journals/annals-of-statistics/volume-49/issue-2/Time-uniform-nonparametric-nonasymptotic-confidence-sequences/10.1214/20-AOS1991.full)
- Lan & DeMets (1983), Discrete sequential boundaries for clinical trials, Biometrika 70:659-663 — Alpha-spending function for governing the lane-wide confirmation window across campaigns (https://doi.org/10.2307/2336502)

---

## Start capturing daily option-implied volatility now: the one literature family documented at weekly horizons in optionable (liquid) names

_area: data · kind: integration · impact: medium · effort: M · confidence: medium_

**Claim**

Now: options data only feeds a GEX research estimate from yfinance chains (agentic_trader/options/fetcher.py:9,36,88), which is not point-in-time. The data assessment deferred options as 'paid' (docs/alpha-data-augmentation-2026-10-18.md section 2, row 9).

Alpaca's option chain snapshot returns impliedVolatility and greeks (Black-Scholes) on a free 'indicative' feed, where trades are delayed and quotes are modified. Historical option data starts only in February 2024. History cannot be bought back cheaply, but prospective capture can start today.

Literature:
- Cremers-Weinbaum: stocks with relatively expensive calls beat those with expensive puts by 50 bp/week. Predictability declined over their sample and is larger when option liquidity is high.
- Xing-Zhang-Zhao: the steepest smirks underperform the flattest by 10.9%/yr risk-adjusted. The effect persists at least six months and precedes bad earnings shocks.
- An-Ang-Bali-Cakici: rising call implied volatility predicts higher returns and rising put implied volatility lower returns, about 1%/month across deciles, persisting up to six months.

Optionable names are exactly the desk's liquid universe, and the 1-week to 1-month horizon fits a 5-20 session label.

**Evidence**

- agentic_trader/options/fetcher.py:9,36,88 (yfinance chains for GEX)
- docs/alpha-data-augmentation-2026-10-18.md section 2 row 9 (options deferred as paid)
- agentic_trader/data/corporate_actions_worker.py:44-68 (once-per-date daemon worker pattern)

**Recommendation**

Recommend:
1. Add a daemon worker following the corporate-actions worker's once-per-NY-date pattern, running about 15:45 ET outside scan windows. For each static and cohort-v2 name it stores, from the indicative feed with receipt times and a digest:
   - ATM call and put IV at the nearest monthly expiry 20-50 days out;
   - 25-delta put IV;
   - call and put volume and open interest.
2. Validate first. Compare indicative IV with an independent source on a sample, because the quotes are modified. Check whether matched-strike call/put IV is clean enough for Cremers-Weinbaum's volatility spread.
3. After about 250 sessions, run a predeclared prospective a priori test of smirk, IV spread and change in call/put IV, with a 5-session label and the matched control.
4. Until then, journal the fields as descriptive card context, as news is.

**North-star impact**

This builds the only plausibly orthogonal, short-horizon signal family for the desk's liquid names. The cost is one small worker and disk, and every month of delay is a month of irreplaceable point-in-time data.

**References**

- Cremers & Weinbaum (2010), Deviations from Put-Call Parity and Stock Return Predictability, JFQA 45(2):335-367 — Expensive calls vs puts: +50 bp/week; decays over sample; larger with liquid options (https://ideas.repec.org/a/cup/jfinqa/v45y2010i02p335-367_00.html)
- Xing, Zhang & Zhao (2010), What Does the Individual Option Volatility Smirk Tell Us About Future Equity Returns?, JFQA 45(3):641-662 — Steep smirk underperforms by 10.9%/yr; persists >=6 months; precedes earnings shocks (https://ideas.repec.org/a/cup/jfinqa/v45y2010i03p641-662_00.html)
- An, Ang, Bali & Cakici (2014), The Joint Cross Section of Stocks and Options, Journal of Finance 69(5):2279-2337 — Call (put) IV increases predict higher (lower) returns, ~1%/month decile spread (https://www.nber.org/papers/w19590)
- Alpaca docs: Historical option data; Option chain snapshot reference — Free indicative feed; snapshots include impliedVolatility and greeks; history only since February 2024 (https://docs.alpaca.markets/reference/optionchain)

---

## Free SEC SIC codes and Ken French 49-industry definitions unlock industry momentum, industry-neutral scores and risk groups for dynamic names

_area: data · kind: integration · impact: medium · effort: S · confidence: high_

**Claim**

Now:
- Sectors are hand labels for the static names (agentic_trader/config.py:101-104). The 452- and 1,000-name cohorts have none (docs/alpha-pooled-literature-2026-09-30.md 'Picks by sector').
- Dynamic names get only a 'dynamic' group (agent/copilot.py:614-617), so the correlation_group rule never binds for them (risk/rules.py:332-348).
- sector_rel_mom_60 is one of two features that survived Holm (rho +0.028; docs/setup-outcomes-2026-09-23.md:78), but it is NaN for dynamic names (research/setups/ranker.py:245-249).
- group_neutralize exists (research/alpha/panel.py:29) but pooled scoring does not use it.

Available for free:
- EDGAR's submissions JSON carries `sic` and `sicDescription` per CIK (verified on data.sec.gov), and the insider lane already maps tickers to CIKs (research/insider_filings.py:574).
- Ken French publishes the four-digit-SIC to 49-industry definitions (Siccodes49) and daily 49-industry portfolio returns.

Literature: Moskowitz-Grinblatt find a strong industry momentum effect that accounts for much of individual-stock momentum and survives size, book-to-market and individual-momentum controls.

**Evidence**

- agentic_trader/config.py:101-104
- agentic_trader/agent/copilot.py:614-617
- agentic_trader/risk/rules.py:332-348
- agentic_trader/research/setups/ranker.py:245-249
- docs/setup-outcomes-2026-09-23.md:78
- agentic_trader/research/alpha/panel.py:29
- agentic_trader/research/insider_filings.py:574 (symbol_ciks)

**Recommendation**

Recommend: capture SIC per symbol as a dated snapshot, at universe build and nightly for cohorts. It becomes point-in-time going forward, and current SIC stands in historically, a limitation to state. Map it to FF49 and FF12, and use it for:
- (a) industry groups for dynamic names in correlation_group and in card de-duplication;
- (b) group_demean in pooled scoring and finding 4's matched control;
- (c) a 6-month industry-momentum feature in setup_features_v3 for the L3 selector shadow, from French's daily 49-industry returns or cohort-built industry returns;
- (d) the industry residual in finding 6.

**North-star impact**

The two daily cards become genuinely different bets: no VOO after VTI, no stacked same-industry movers. A literature-backed, already-validated sector-relative feature becomes usable for ranking.

**References**

- Moskowitz & Grinblatt (1999), Do Industries Explain Momentum?, Journal of Finance 54(4):1249-1290 — Industry momentum explains much of stock momentum and survives standard controls; selector feature (https://ideas.repec.org/a/bla/jfinan/v54y1999i4p1249-1290.html)
- Ken French Data Library, 49 Industry Portfolios details (Siccodes49 definitions; daily returns) — Free SIC-to-industry mapping and daily industry returns (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_49_ind_port.html)
- SEC EDGAR APIs (data.sec.gov submissions JSON includes sic and sicDescription) — Free, keyless industry codes per CIK; the desk already holds the ticker-to-CIK map (https://www.sec.gov/edgar/sec-api-documentation)

---

## Test news tone prospectively on the journal the desk already writes; any historical LLM study must anonymise entities and respect model cutoffs

_area: signals · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

Now: each universe scan fetches Benzinga headlines and has an LLM score tone, relevance and catalyst for the first 6 ranked symbols. The result is journaled on every candidate and never acts as a gate (docs/news-context.md:9-29). The doc states 'No study yet', although cached history reaches 2015 (docs/news-context.md:179-185, 201).

Literature:
- Lopez-Lira & Tang (v6): GPT-4 headline scores predict next-day returns and subsequent drift, especially for small stocks and negative news, and strategy returns decline as LLM adoption rises.
- Glasserman & Lin: in-sample LLM backtests mix look-ahead bias with a 'distraction effect' from company names. Anonymised headlines separate the two, and out of sample most of the apparent edge came from knowledge of the future.
- Ke-Kelly-Xiu: a supervised, return-tailored text score that carries no LLM training-data prior.

Implication: for a long-only, large-cap desk entering at 10:35, positive-tone drift is weakest where the desk trades. The most plausible use is a negative-news or no-news veto (finding 5).

**Evidence**

- docs/news-context.md:9-29 (live scoring, journaled, applied false)
- docs/news-context.md:179-185,201 (no study; history to 2015)
- agentic_trader/agent/news_context.py:97-136 (LLM scorer)

**Recommendation**

Recommend:
1. Predeclare a read rule now on the scan_candidates_ranked news blocks: tone from headlines published before the decision only, against next 1- and 5-session alpha_R (finding 4). Include a minimum n and a confidence-sequence stop (finding 8). This sample is free of look-ahead because the scoring happened live.
2. If a historical study is wanted, either score anonymised headlines (company names and tickers masked) with a model whose training cutoff precedes the test window, or fit a Ke-Kelly-Xiu-style supervised score on the discovery window only. Label at 1 and 5 sessions.
3. Prefer negative-news and no-news flags as vetoes over a positive-tone long signal.

**North-star impact**

The LLM spend moves from an approver that approved 14 of 14 journaled evaluations (live map) to a measured input with a plausible card use, without look-ahead contaminating the evidence.

**References**

- Lopez-Lira & Tang, Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models (arXiv 2304.07619, v6 Oct 2025) — LLM headline scores predict next-day returns and drift, strongest in small stocks and negative news; returns decline with LLM adoption (https://arxiv.org/html/2304.07619v6)
- Glasserman & Lin (2023), Assessing Look-Ahead Bias in Stock Return Predictions Generated By GPT Sentiment Analysis (arXiv 2309.17322) — Look-ahead bias and a name-driven distraction effect in LLM backtests; anonymisation as the remedy (https://arxiv.org/abs/2309.17322)
- Ke, Kelly & Xiu (2019), Predicting Returns with Text Data, NBER WP 26186 — Supervised, return-tailored text sentiment; an LLM-free historical alternative (https://www.nber.org/papers/w26186)

---

## If L3 uses meta-labeling, build it the AFML way and expect small gains: the public evidence is thin

_area: signals · kind: improvement · impact: medium · effort: M · confidence: medium_

**Claim**

Now:
- Cards are ranked by setup_quality (agent/copilot.py:1172-1177), which carries no information (docs/setup-outcomes-2026-09-23.md:80-81).
- The frozen ridge ranker failed its holdout and was significantly worse than setup_quality (docs/setup-outcomes-2026-09-23.md:13).
- Journaled rows repeat setups: 87 rows cover 41 setups (live map Q6; docs/card-evidence.md 'Repeated setups').
- The desk's label_bracket is already a triple-barrier label: stop, target and time (research/setups/labels.py:159-189).
- The architecture map proposes a meta-label P(target before stop) selector.

Literature:
- AFML gives triple-barrier labels and meta-labeling (chapter 3), uniqueness weights for overlapping labels (chapter 4), and purged k-fold CV with embargo (chapter 7).
- The public evidence for meta-labeling gains is a practitioner capstone on two strategies. Out-of-sample precision rose from 0.17 to 0.20 and from 0.48 to 0.54 (Singh & Joubert, Hudson & Thames). That is weak support, and the desk's own ridge result is the more relevant prior.

**Evidence**

- agentic_trader/agent/copilot.py:1172-1177
- docs/setup-outcomes-2026-09-23.md:13,80-81
- agentic_trader/research/setups/labels.py:159-189
- docs/card-evidence.md 'Limits / Repeated setups'

**Recommendation**

Recommend:
1. **Target:** alpha_R > 0 over the hold (finding 4), not raw target-first, which rewards beta in rising tapes.
2. **Features:** setup_features_v2 plus v3 (finding 5), industry momentum (finding 10) and the composite score (finding 7).
3. **Fitting:** one setup = one sample (uniqueness weights); skfolio CombinatorialPurgedCV with an embargo of at least 20 sessions; PBO across the few model choices; one frozen model.
4. **Evaluation:** a shadow A/B against setup_quality and against uniform-random among approved candidates. Ship only through card_policy preview.
5. **Card display:** calibrate probabilities (isotonic) before any card line shows them.

**North-star impact**

L3 is the only lever on which of the day's candidates becomes one of the 1-2 cards. Building it this way avoids repeating the ridge failure and keeps 'random among approved' as the honest baseline.

**References**

- Lopez de Prado (2018), Advances in Financial Machine Learning, Wiley (ch. 3 triple-barrier and meta-labeling; ch. 4 sample uniqueness; ch. 7 purged k-fold) — Methods for overlapping labels, leakage-free CV and meta-labels (https://oreilly.com/library/view/advances-in-financial/9781119482086)
- Singh & Joubert, Does Meta-Labeling Add to Signal Efficacy? (Hudson & Thames report) — Only modest out-of-sample precision gains (0.17 to 0.20; 0.48 to 0.54) on two strategies; thin evidence base (https://hudsonthames.org/wp-content/uploads/2022/04/Does-Meta-Labeling-Add-to-Signal-Efficacy.pdf)
- skfolio CombinatorialPurgedCV — Maintained open-source CPCV with purge and embargo for the selector fit (https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)

---

## Borrow definitions and test oracles from open-source quant tooling; don't adopt frameworks, and avoid mlfinlab

_area: architecture · kind: integration · impact: low · effort: S · confidence: high_

**Claim**

Now:
- Rolling DSL operators are Python lambdas, about 100x slower than vectorised ones (research/alpha/operators.py:15-19,138-152, per the alpha-pipeline map's measurement).
- Risk uses one Ledoit-Wolf model (research/setups/covariance.py:87), and the CVXPY allocator (research/alpha/optimizer.py) has no external oracle.
- There is no factor tear-sheet tooling for the journaled setup features.

Tools, all verified:
- **Qlib** (MIT): Alpha158/Alpha360 handlers, a point-in-time database, LightGBM in its model zoo, and RD-Agent integration.
- **OSAP:** Python signal code via the `openassetpricing` package.
- **skfolio:** CombinatorialPurgedCV, WalkForward, RiskBudgeting and HierarchicalRiskParity.
- **Riskfolio-Lib** (BSD-3): 26 convex risk measures including CVaR, risk parity, HRP/HERC, and per-asset and per-factor risk contributions.
- **alphalens-reloaded** (Apache-2.0): maintained for fixes only, with ML4T Diagnostic as the successor; the next release supports Python 3.11-3.15.
- **vectorbt:** Apache-2.0 with a Commons Clause; the PRO edition is commercial.
- **mlfinlab:** all rights reserved, commercial use needs a licence; QuantConnect lists it at GBP 100/month.

**Evidence**

- agentic_trader/research/alpha/operators.py:15-19,138-152
- agentic_trader/research/setups/covariance.py:87
- agentic_trader/research/alpha/optimizer.py:45-282
- agentic_trader/risk/book_vol.py:72-111

**Recommendation**

Recommend:
1. Use Alpha158's and OSAP's definitions as the reference spec for DSL v2 operators and fields and for the fidelity tables. Implement vectorised kernels in-house (numpy or bottleneck) rather than importing Qlib's data layer, which would bypass the digest-pinned stores.
2. Use skfolio's CombinatorialPurgedCV and WalkForward for any fitted ranker.
3. Use skfolio RiskBudgeting and Riskfolio's risk-contribution functions only as test oracles for book_vol extensions (marginal risk contribution, risk budgets), never as live dependencies.
4. Run alphalens-reloaded or ML4T Diagnostic IC-by-horizon tear sheets on the journaled features, in a pinned research extra checked on Python 3.14.
5. Do not take mlfinlab, because of its licence, and keep vectorbt out of kernels shared with live.

**North-star impact**

Searches and studies run 10-100x faster, research definitions match published ones, and the allocation math gets independent validation, with no new production dependencies.

**References**

- Microsoft Qlib (MIT) — Alpha158/Alpha360 feature definitions, PIT database design, LightGBM baseline, RD-Agent (https://github.com/microsoft/qlib)
- Riskfolio-Lib (BSD-3-Clause) — CVaR, risk parity and risk-contribution primitives as test oracles (https://github.com/dcajasn/Riskfolio-Lib)
- alphalens-reloaded (Apache-2.0; fixes-only, successor ML4T Diagnostic) — Factor IC, quantile return and turnover tear sheets for journaled features (https://github.com/stefan-jansen/alphalens-reloaded)
- vectorbt (Apache-2.0 with Commons Clause; PRO commercial) — Fast vectorised backtests for research sweeps; licence limits reuse (https://github.com/polakowo/vectorbt)
- QuantConnect: MlFinLab offering (commercial, GBP 100/month) — mlfinlab is not open source; avoid as a dependency (https://www.quantconnect.com/announcements/15575/new-machine-learning-package-mlfinlab/)

---

## Don't build an LLM alpha-mining loop (Alpha-GPT, AlphaAgent or RD-Agent style) on this desk's data

_area: alpha_mining · kind: stop_doing · impact: low · effort: S · confidence: medium_

**Claim**

Now: no LLM miner exists. Genetic search was parked after check B failed 1/10 and then 3/10 (docs/alpha-pooled-checks-2026-10-02.md; docs/alpha-pooled-checks-v3-2026-10-05.md), and the weekly ETF32 miner was retired (scripts/launchd.sh:21).

Literature:
- Alpha-GPT's evidence is the authors' claim of a top-10 competition ranking.
- AlphaAgent's out-of-sample IC is 0.0056 on the S&P 500 against 0.0212 on CSI 500, reported as the best of 20 trials.
- RD-Agent(Q)'s main test is CSI 300 (2017-2020) on Qlib, with CSI 500 and NASDAQ 100 as robustness checks.
- LLM-driven backtests inherit look-ahead from training data (Glasserman-Lin).

The desk's own research doc maps an AlphaAgent-like IC to about 0.02R (docs/alpha-pooled-mining-research-2026-09-28.md:51), far below its MDE. Every LLM-proposed formula would also be a charged trial that raises the bar for real candidates.

**Evidence**

- docs/alpha-pooled-checks-2026-10-02.md (check B 1/10)
- docs/alpha-pooled-checks-v3-2026-10-05.md (check B 3/10; genetic parked)
- scripts/launchd.sh:21 (miner retired)
- docs/alpha-pooled-mining-research-2026-09-28.md:51

**Recommendation**

Recommend:
- Limit LLM use to two jobs: drafting fixed-set hypothesis cards with literature-fidelity tables before any data is read, each one charged; and the news study (finding 11).
- Never feed an LLM scored campaign results.
- Revisit only if the lane's MDE falls below about 0.03R (findings 4 and 8) and the agent itself passes a check-B-style search-power test.

**North-star impact**

Trial budget, the confirmation window and operator attention stay on work that can reach cards, and an attractive-looking but power-starved search does not reopen.

**References**

- Wang et al., Alpha-GPT: Human-AI Interactive Alpha Mining for Quantitative Investment (arXiv 2308.00016; EMNLP 2025 demo) — Evidence is a self-reported competition ranking, not out-of-sample US equity results (https://arxiv.org/html/2308.00016v2)
- AlphaAgent (arXiv 2502.16789) — Out-of-sample IC 0.0056 on S&P 500 vs 0.0212 on CSI 500; best of 20 trials (https://arxiv.org/html/2502.16789v2)
- Li et al., R&D-Agent-Quant (arXiv 2505.15155, NeurIPS 2025) — Main evidence on CSI 300 via Qlib; US results are robustness checks only (https://arxiv.org/html/2505.15155v2)
- Glasserman & Lin (2023), arXiv 2309.17322 — LLM training data leaks future outcomes into backtests (https://arxiv.org/abs/2309.17322)
