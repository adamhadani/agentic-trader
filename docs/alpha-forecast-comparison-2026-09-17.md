# Forecast comparison — September 17, 2026

## Outcome

The predeclared comparison completed **18 jobs / 78 trials**, with no failures.
Three single-feature one-bar models reduced squared prediction error against the
training-mean comparator in all three discovery folds. This provides research leads,
**not qualified alphas, strategy profit or an independent significance result**.
Nothing was promoted; the registry remained at generation 4, zero active and four
historical shadow hypotheses. No orders or Telegram messages were sent.

| Symbol | One-bar feature | Pooled error reduction | Fold error reductions |
| --- | --- | --- | --- |
| SPY | `open_gap` | 1.618% | 0.334%, 0.537%, 2.365% |
| QQQ | `open_gap` | 0.273% | 0.0017%, 0.0269%, 0.501% |
| QQQ | `roc(close,20)` | 0.051% | 0.010%, 0.118%, 0.032% |

“Error reduction” is `1 - model_MSE / training_mean_MSE`. Each single-feature model
fits its slope/intercept on preceding training observations; this is not an assumption
that the raw feature's sign is the trade direction. All numbers are descriptive
and selected from the full family below. In particular, the tiny QQQ improvements
could reflect noise and do not establish an economically usable edge.

| Method | Trials | Positive pooled skill | Positive skill in every fold |
| --- | ---: | ---: | ---: |
| Single feature | 42 | 17 | 3 |
| Ridge combinations | 18 | 8 | 0 |
| Boosted trees | 18 | 4 | 0 |
| Total | 78 | 29 | 3 |

None of the five-bar trials improved the comparator in every fold. SPY's best pooled
Ridge score reduced error by 2.023%, but its first fold worsened error by 2.995%.
This is a useful example of why an aggregate winner alone is insufficient. These
results do not justify increasing model complexity or expanding automated search yet.

## Frozen scope and provenance

The [predeclared protocol](alpha-forecast-benchmarks.md#bounded-first-comparison-predeclared-before-reading-results)
used SPY/QQQ/IWM, horizons one and five observed bars, seven single features, three
Ridge settings and three boosted settings, seed 20260917. The implementation was
committed as `e6045783d40a3f994bd26f4c7bd462d5ba3668cf` before computation.

Each saved SIP/raw dataset has 1,252 daily bars from September 20, 2021 through
September 15, 2026. The discovery prefix has 1,001 bars; the original holdout begins
September 16, 2025. **No holdout values entered fitting or evaluation.** The original
source runs predate the current return-timeline policy; unchanged, hash-verified
raw data was explicitly registered under new dataset-only records with zero formula
trials. Original performance/qualification evidence was not migrated or reused.
These are already-inspected discovery periods, not fresh out-of-sample confirmation.

- Lifetime research attempts increased **7,053 → 7,131**, exactly 78.
- Forecast trials did not add strategy-Sharpe variance samples.
- Plans, full fold metrics and per-observation forecasts/targets are private artifacts
  linked by durable diagnostic events. Discovery intervals are excluded from later
  holdout reuse. Recomputing the campaign would be another charged experiment.
- Artifact directory: `~/.local/state/agentic-trader/research/forecast-comparison-20260917`.
- Protocol SHA-256: `84d2e056a87189315253e9b5c577a68b0ad12ac631b4f79647e9490589e73ab0`.
- Completion SHA-256: `974d299c4db8724dd8cf70976477665eafeadf0e1ca9177bcd844d63485ed3d1`.

## Next experiment and limits

Prioritize **SPY open-gap** as a lead for the next frozen study. Compare economic
forecast targets and explicit executable policies: a completed-bar close-to-close
predictor does not guarantee an edge available from the following open. Predeclare
entry timing, holding horizon, costs, gap treatment, turnover and controls before
examining fresh evidence. Check the QQQ counterpart for transfer; do not promote
three correlated/weak candidates just because the requested count is three or four.

The simple baselines now make that question testable without losing forecasts inside
an unsuitable bracket policy. Remaining work includes uncertainty/multiple-search
calibration, target-aware combination, an execution policy version, prospective
shadow evidence, and the existing qualification/registry/risk controls. Intraday
session admission remains blocked pending A2b acquisition/durable decisions and actual
execution observations. Wider DSL/panel hypotheses and grammar-guided search remain
in the [ordered roadmap](alpha-roadmap.md#funnel-expansion-ranked-experiments-september-17-review).

## Validation versus deployment

Source validation: 1,134 tests passed (22 explicit opt-in skips), 80 real
HTTP/WebSocket/PostgreSQL integrations passed, and all-file pre-commit/Ruff/mypy passed.
TDD and independent paired-error references cover the forecast contract; synthetic
positive controls are regression tests, not a calibrated false-discovery estimate.
Deployment revision, service freshness and reconciliation are verified separately
and recorded on the PR after merge/restart.
