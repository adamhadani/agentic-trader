# Alpha research roadmap

**Active long-horizon plan — updated 2026-09-17.** This is the canonical alpha work
queue. [Pipeline contracts](alpha-pipeline.md) describe current behavior; the
[architecture review](architecture-review.md) retains broader operational priorities.
The [original alpha review](alpha-stack-review.md) is historical defect evidence.

## Objective and constraints

Discover reproducible, complementary forecasts and qualify their actual execution
policy before admitting paper risk. Three or four deployed alphas is an aspiration,
not a quota. Rejecting everything does not prove calibration; a good pipeline must
also recognize controlled positive cases and quantify its statistical power.

- Keep immutable strategy/data/policy identity, all attempts and failed experiments.
- Preserve causal features, purged temporal validation and one-use final holdouts.
- Never select gate parameters to make already-seen candidates pass. Predeclare
  calibration scenarios, thresholds and acceptance criteria before running them.
- Synthetic evidence is diagnostic only: no production journal/shadow credit,
  broker orders or notifier access. Statistical evidence alone cannot activate.
- Reuse the journal, artifact adapters, entry FIFO and close/protection services.
  No parallel execution queues, YAML ownership or statistical fallback promotion.
- Keep active paper service and test configuration isolated. Develop in a worktree.
- Each change starts with behavioral tests; retain RED → GREEN evidence, then review,
  relevant integrations, pre-commit/CI and a controlled deployment if code changes.

## Ordered work queue

| ID | Status | Milestone / acceptance boundary |
| --- | --- | --- |
| A1a | Implemented — [PR #35](https://github.com/adamhadani/agentic-trader/pull/35) | Calibration instruments and permanent control/reference tests; diagnostic outputs cannot authorize promotion. [Pilot evidence](alpha-calibration-2026-09-16.md). |
| A1b | Implemented — [PR #36](https://github.com/adamhadani/agentic-trader/pull/36), [study results](alpha-study-2026-09-16.md) | All 1,952 jobs retained; scientific status incomplete (22 unavailable comparisons). No replacement gate accepted. |
| A2 | A2a implemented — [PR #37](https://github.com/adamhadani/agentic-trader/pull/37); A2b replay groundwork — [PR #38](https://github.com/adamhadani/agentic-trader/pull/38) | [Replay contract](alpha-session-replay.md) and [four-run evidence](alpha-session-replay-2026-09-16.md): SIP coverage complete, IEX incomplete in both windows. Live signal-clock migration and broker execution observations remain; intraday promotion stays blocked. |
| A3 | Forecast benchmark prerequisite — [PR #43](https://github.com/adamhadani/agentic-trader/pull/43); [PR #44](https://github.com/adamhadani/agentic-trader/pull/44) timing/cost screen complete; standalone open-gap policy paused; combined execution shadow-only | Distinguish forecast components from a fully specified tradable strategy; validate combinations causally. |
| A4 | Planned | Broader economic hypotheses and point-in-time universe/data coverage. |
| A5 | Planned | Bounded research campaigns and a shadow observation universe independent of trading permissions. |

A1a makes A1b reproducible. A2 can follow those foundations while any longer A1b
forward experiment accumulates evidence. A3–A5 may need small prerequisite adapters;
record dependencies here instead of creating a second competing roadmap. Update each
row with the implementing PR, reproducible evidence and any remaining activation gate.

### A1 — Calibration before more search

A1a now provides `alpha calibrate`, shared pure statistical assessment, independent
references and fail-closed invalid-evidence checks. The 32-seed pilot detected dense
positive controls, rejected null strategy controls and exposed limited sparse-control
power. PR review required separating data and resampling random streams; retain both
the initial and corrected study evidence. Neither is approval of a 5% error-control
claim. A1b adds predeclared scenarios, block-length sensitivity, ARCH comparisons
and adaptive-search replay. Its [results](alpha-study-2026-09-16.md) expose return
coverage and execution/objective limitations; no gate replacement is justified.
**Current: A2b**, session/fine-bar execution replay. A2a corrected the return clock
and repeated calibration with a versioned policy and fresh validation: all 1,952
jobs completed, null-search criteria passed, but planted-signal power remains
insufficient. No replacement gate is justified by the [fresh results](alpha-timeline-study-2026-09-16.md).

The September 16 campaign found zero qualifying candidates among 3,648 trials.
Another 1,368 trials exercised 24 nested walk-forward folds. Artificial dense edges
passed, sparse profitable controls failed cumulative DSR, and zero-edge controls
failed. These small diagnostics do not measure reliable error rates. See
[campaign evidence](alpha-research-2026-09-16.md).

A1a acceptance:

- Independent numeric DSR references, realistic per-observation units, valid moments,
  finite/discrete budget checks, and deterministic edge cases.
- Reusable causal synthetic generators with null, sparse and dense positive cases;
  shared scoring/simulation/statistical assessment, explicitly synthetic provenance.
- Reproducible reports of per-gate rejection, detection/false-positive rates and
  uncertainty intervals, with bounded compute and frozen protocol identity.
- Joint block-resampling diagnostics preserve serial/cross-strategy dependence;
  duplicate/permuted/scaled columns have tested semantics. They cover supplied fixed
  candidates only; they cannot claim to replay adaptive search or replace lifetime
  trial accounting. No diagnostic output is a promotion credential.
- CLI integration exercises the real calculation and private artifact output with
  config/DB/provider construction forbidden; no synthetic production state.

A1b acceptance:

- Freeze unseen seeds, sample sizes, effect sizes, trade frequencies, autocorrelation,
  cross-candidate dependence and block-length sensitivity before evaluation.
- Measure confidence intervals for false acceptance and power, including a replay of
  the adaptive search process. Keep a validation set of calibration scenarios.
- Distinguish raw attempt count from independent trials; preserve the entire ledger.
  Any dependence estimate uses discovery/training data, never the final holdout.
- Compare the existing DSR/holdout contract with justified alternatives. Report
  assumptions and failures. Do not present a fixed-panel bootstrap as adaptive-search
  family-wise error control, or DSR as a calibrated probability of live profit.
- Change a runtime gate only after the agreed statistical acceptance criteria pass;
  version the policy and require fresh evidence. No automatic reinterpretation of
  past rejections or consumed holdouts.

### A2 — Session-correct execution replay

A2a is implemented: missing feature scores no longer remove known cash observations
or same-bar pending-fill/exit losses from the return series. TDD covers warmup,
intermittent features, pending/held exposure, missing prices and causal prefixes.
Full execution clocks, coverage and compatible variance samples are versioned;
lifetime trials and original A1b failures remain intact. The [contract](alpha-return-timeline.md)
and [fresh study](alpha-timeline-study-2026-09-16.md) record evidence and remaining limits.

A2b's [first increment](alpha-session-replay.md) implements observed exchange
sessions, 15m/1h/4h/session aggregation, a shared minute execution state machine,
explicit decision latency and durable diagnostic artifacts. It preserves the
existing daily validation semantics. The predeclared four-run deployment-feed
smoke test checks DST/early-close coverage without tuning a formula.
Its [results](alpha-session-replay-2026-09-16.md) retain two SIP completions and two
IEX coverage failures; neither successful replay generated an entry. No live-feed
change or alpha qualification follows from this mechanics/data check.
Clock-boundary hardening now rejects session-derived/unknown layouts and ambiguous
timestamps in fixed-duration research, live screening and shadow paths. Shared
closure logic lives in `market/bars.py`; new manifests record the fixed clock.
This preserves existing version identities and does not complete live migration.

The [prospective observer](alpha-forward-observations.md) now samples the configured
feed around actual session-bar closes, retains immutable raw inputs/receipts/revisions
through the existing journal, and excludes inspected dates before price access.
It measures the REST data boundary without scoring, trading or promotion credit.
The desk enables SPY/15m; actual forward evidence requires subsequent live sessions.

The [versioned decision contract](alpha-session-decisions.md) now preserves old
identities, gives new session versions shared receipt/delay/expiry screening and
shadow semantics, and prevents expired replay proposals from crossing closed sessions.
New versions remain diagnostic-only, including session-derived daily definitions.

Remaining A2b: install session acquisition and a durable, session-aligned decision
worker (including restart/revision/missed-window handling); collect actual publication/acknowledgment/fill evidence.
Native provider bars cannot be silently treated as session-derived bars.
Cover timezone/DST, holidays/early closes, extended-hours aggregation, closed-bar
availability, GTC pending entries, conservative same-bar paths, gap/partial fills,
trailing updates and broker-held protection. Keep signal and execution clocks separate.

Acceptance: deterministic event replay and actual-SDK fixtures agree on order eligibility
and working protection; causal prefix tests pass; transaction costs/corporate actions
have explicit contracts; real paper-fill observations quantify the remaining mismatch.
Only then replace the unconditional intraday gate with evidence-based eligibility.

### A3 — Forecast components and executable combinations

The [forecast benchmark](alpha-forecast-benchmarks.md) now separates explicit
close-return horizons from bracket-policy P&L, compares single-feature/Ridge/boosted
predictions on purged walk-forward folds, and retains all attempts/artifacts in the
existing journal. This is a bounded A3 prerequisite while A2b forward data accumulates;
it does not supersede the unfinished A2b acquisition/decision worker or enable trading.


A useful next-day predictor need not be profitable under an unrelated bracket/holding
policy. Define forecast horizon/label/uncertainty separately from trade execution.
Train calibration and combination weights only on preceding observations; retain
component attribution and evaluate incremental evidence against a frozen incumbent set.

Acceptance: nested walk-forward tests cover complementary, duplicate and opposing
forecasts, missing/stale data and changing incumbents. A combined tradable definition
versions contributors, weights and execution policy together. It passes the same
qualification/registry/risk boundaries. Keep portfolio targets shadow-only until
post-rounding risk, partial fills and protective-order ownership are supported.

### A4 — Widen information and hypotheses

Retain ETF32 as a benchmark. Add hypotheses with an economic rationale: sector-relative
momentum/reversal, residual signals, overnight versus session behavior, and liquidity/
volatility conditioning. Extend typed/panel operators only with alignment, dimensional,
missing-data and future-perturbation tests.

Expand toward 100–200 liquid equities only with point-in-time membership, delistings,
corporate actions, historical eligibility and feed coverage. Do not use today's list
as survivorship-safe history or silently replace raw execution prices with adjusted
feature prices. Require cohort-transfer tests and measured cost/borrow/capacity.

### A5 — Operate the research lifecycle

Separate a shadow observation universe from permission to trade an instrument. Run
bounded jobs with durable status/cancellation, immutable protocols and private artifacts.
Use daily forward outcome/decay/cost observations, bounded weekly discovery, and
periodic matched-budget experiments. Collect shadow observations before spending a
qualification holdout when that avoids evidence expiring during the waiting period.

Acceptance: no missing/unconfigured instrument silently earns shadow credit; changing
registry generations, duplicate candles, restart/recovery and worker contention are
covered. Observe actual paper fills before scaling. GP/Ridge/boosted methods remain
benchmarks; RL/AlphaGen/AlphaForge follow only after the evaluation/ensemble objectives
and simpler baselines are established.

## Funnel expansion: ranked experiments (September 17 review)

1. **Forecast objective and simple baselines (completed bounded follow-up).** The frozen
   78-trial ETF comparison is [complete](alpha-forecast-comparison-2026-09-17.md).
   Three one-bar single-feature leads improved every discovery fold; none is qualified.
   The [open-gap timing/cost protocol](alpha-forecast-policy.md#predeclared-open-gap-follow-up)
   froze next-open targets, QQQ transfer, four cost scenarios and triage criteria.
   [All 20 attempts completed](alpha-open-gap-policy-2026-09-17.md); SPY failed the
   every-fold net/excess-return criterion. Pause this standalone policy; no promotion
   or gate change. Forecast skill survives, but this trading rule is unstable/cost-sensitive.
   The [failure postmortem](alpha-open-gap-postmortem-2026-09-17.md) found no numeric
   discrepancy, but unstable selection, concentrated error gains and a loss/action
   mismatch. Before selecting another lead, extend the existing report with fold
   stability, influence, entered/skipped returns, turnover/cost and fitted-model
   evidence. Predeclare the economic use of each forecast; do not retune this result.
2. **Complete A2b live acquisition and durable decisions (next implementation priority).** Measure publication delay
   before choosing production timing; collect prospective forecasts and actual paper
   acknowledgments/fills. More historic search cannot substitute for this evidence.
3. **Aligned panel/relative hypotheses (A3/A4).** Add sector-relative residual momentum,
   reversal, overnight/session decomposition and liquidity conditioning. Implement a
   panel dataset contract and causal cross-sectional alignment before DSL `rank` or
   peer regressions. Compare incremental forecasts against frozen incumbent factors.
4. **Grammar-guided exploration (A4/A5).** Existing search already has typed AST
   mutation/crossover, a structural diversity archive and score deduplication. First
   benchmark bounded grammar productions and subtree novelty against existing random
   and GP search at identical trial/compute budgets. Only then evaluate MCTS/RL.
   [AlphaCFG (2026)](https://arxiv.org/abs/2601.22119) combines bounded grammar with
   syntax-aware policy/value learning and MCTS; [RiskMiner](https://arxiv.org/abs/2402.07080)
   is another MCTS comparator. Their published gains are hypotheses for our data,
   not transferable validation or permission to expand trial budgets indefinitely.
5. **Combination, then assisted generation.**
   [AlphaForge](https://arxiv.org/abs/2406.18394) studies generation plus changing
   factor combinations; [AlphaGen](https://arxiv.org/abs/2306.12964) targets collections.
   Compare these only after simple causal Ridge/linear combinations and attribution.
   [Microsoft RD-Agent(Q)](https://github.com/microsoft/RD-Agent) offers automated
   factor/model research workflows; evaluate bounded proposal generation behind our
   existing DSL, trial ledger and validator, without a second execution authority.
6. **Broader assets only with data/execution contracts.** ETF32 remains the initial
   benchmark, followed by point-in-time liquid equities with delistings and corporate
   actions. Options require historical contract chains/quotes, spread/slippage/Greeks,
   expiration/exercise/assignment accounting and protection ownership; Alpaca's
   [documented option lifecycle](https://docs.alpaca.markets/us/docs/options-trading-overview)
   includes these events. Futures require contract rolls, multipliers, margin/session
   data and a separately verified broker adapter. Neither is just adding ticker strings.
   Reuse equity ETFs for broad economic exposures while those capabilities are absent.

**Cadence:** keep bounded weekly discovery; collect daily forward outcomes. Do not
increase frequency while power/objective and deployment-clock gaps remain. Later
campaigns should include null/positive controls, parameter-budget matching, failure
rates, fold coverage, turnover/cost and cohort transfer, with a fresh untouched final
period. Current-list equities must never be described as survivorship-free history.

**Fallout retained:** benchmark failures keep reservations but lack automatic job
recovery/per-trial checkpoints (A5). The broader discovery/exposure audit should ensure
all inspected prefixes, not only explicit diagnostics, are fenced against later
holdout reuse. Forecast pooling still identifies horizon by bar timeframe; versioned
multi-bar target/uncertainty compatibility must precede deploying combined forecasts
(A3). Research-worker isolation and operational dependencies remain below.

## Operational dependencies retained

Corporate-action/partial-fill account semantics, macro/calendar freshness admission,
stop-intent recovery, composition/bootstrap and independent operations monitoring
remain in [architecture-review.md](architecture-review.md#remaining-findings-ranked).
Portfolio execution cannot bypass those ownership/protection constraints.

## References

- [DSR, including independent-trial treatment in Appendix 3](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
- [ARCH multiple-comparison studies and established SPA/StepM procedures](https://bashtage.github.io/arch/multiple-comparison/multiple-comparison_examples.html).
- [AlphaGen: optimizing complementary collections](https://arxiv.org/abs/2306.12964).
- [Alpaca historical bar adjustments](https://docs.alpaca.markets/us/reference/stockbars).
