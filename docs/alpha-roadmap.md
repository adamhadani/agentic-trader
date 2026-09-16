# Alpha research roadmap

**Active long-horizon plan — updated 2026-09-16.** This is the canonical alpha work
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
| A2 | Planned; intraday promotion blocked | Session-correct fine-bar execution replay and research/live parity. |
| A3 | Planned; combined execution shadow-only | Distinguish forecast components from a fully specified tradable strategy; validate combinations causally. |
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
**Next: A2**, starting with the explicit execution return timeline below. Revisit
calibration with a versioned policy and fresh validation after changing those semantics.

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

First fix the return-timeline contract exposed by A1b: missing feature scores while
flat currently remove known cash observations from the return series. Use TDD for
warmup/intermittent undefined features, pending entries, held positions and genuinely
missing prices. Preserve the full known execution clock, explicit feature coverage
and causal signals; verify annualization, resampling spacing and statistical sample
length. The old A1b failures stay unavailable; do not reinterpret them after a fix.

Use observed exchange sessions and a finer execution timeline for 15m/1h/4h signals.
Cover timezone/DST, holidays/early closes, extended-hours aggregation, closed-bar
availability, GTC pending entries, conservative same-bar paths, gap/partial fills,
trailing updates and broker-held protection. Keep signal and execution clocks separate.

Acceptance: deterministic event replay and actual-SDK fixtures agree on order eligibility
and working protection; causal prefix tests pass; transaction costs/corporate actions
have explicit contracts; real paper-fill observations quantify the remaining mismatch.
Only then replace the unconditional intraday gate with evidence-based eligibility.

### A3 — Forecast components and executable combinations

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
