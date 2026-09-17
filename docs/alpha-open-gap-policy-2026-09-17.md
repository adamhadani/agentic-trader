# Open-gap timing and cost experiment — September 17, 2026

**Decision: pause the standalone daily long/cash open-gap policy.** The SPY
delayed-target model fails the frozen advance-to-replay criterion. Prediction error
improves modestly, but trading results are unstable across folds and cost-sensitive.
No alpha was qualified or promoted. This does not reject every possible use of
open-gap information.

Implementation: [PR #44](https://github.com/adamhadani/agentic-trader/pull/44).
The [protocol and payoff contract](alpha-forecast-policy.md) were committed at
`c3a5880d5508e0af323c1d2cc9d296127e7898ab` before evaluating these payoffs.

## Frozen scope and completeness

- SPY and QQQ, existing raw SIP daily snapshots: 1,252 bars each; discovery prefix
  1,001 bars. The holdout begins September 16, 2025 and was not evaluated.
- One OLS `open_gap` feature, horizon one, seed 20260917; original close-to-close
  and next-open-to-close labels. Three purged expanding walk-forward folds.
- Four runs completed; 20 charged attempts (one forecast and four cost variants
  per run). Lifetime attempts increased from 7,131 to 7,151.
- Every model retained 498 forecasts, 166 per fold. Each payoff clock has 501
  observations, including three first-fold-bar cash observations; no missing forecasts.
- Registry generation 4 remained unchanged: zero active alphas, four historical
  unqualified shadow hypotheses. No provider downloads, broker orders or Telegram
  messages were made by this experiment.

These are reused discovery periods, not fresh final tests. Daily endpoints are
price proxies, not auction fills. Results are hypothetical compounded returns over
the validation periods, not annual returns or account performance. Costs below
are assumed basis points **per side**; 1 basis point is 0.01%.

## Forecast accuracy and turnover

MSE skill is relative squared-error reduction against each fold’s training-mean
forecast. It is not profitability, statistical significance or a promotion score.

| Symbol | Training target | MSE skill | Hypothetical round trips |
| --- | --- | ---: | ---: |
| SPY | `close_to_close` | 1.618% | 423 |
| SPY | `next_open_to_close` | 1.862% | 384 |
| QQQ | `close_to_close` | 0.273% | 494 |
| QQQ | `next_open_to_close` | 0.498% | 402 |

## All cost scenarios

The fixed comparator holds a fully funded long from the same observed open to
close every eligible day. Both policies start each fold in cash. Cost scenarios
use identical forecast decisions; no threshold was selected after seeing results.

| Symbol | Training target | Cost/side (bps) | Model return | Comparator return | Model fold returns (1 / 2 / 3) |
| --- | --- | ---: | ---: | ---: | --- |
| SPY | `close_to_close` | 0 | +13.08% | +15.58% | +6.10% / -4.25% / +11.31% |
| SPY | `close_to_close` | 1 | +3.91% | +4.62% | +3.36% / -7.12% / +8.23% |
| SPY | `close_to_close` | 5 | -25.92% | -29.76% | -6.92% / -17.75% / -3.23% |
| SPY | `close_to_close` | 10 | -51.47% | -57.31% | -18.35% / -29.35% / -15.88% |
| SPY | `next_open_to_close` | 0 | +10.67% | +15.58% | +6.10% / -4.58% / +9.32% |
| SPY | `next_open_to_close` | 1 | +2.49% | +4.62% | +3.36% / -7.14% / +6.79% |
| SPY | `next_open_to_close` | 5 | -24.62% | -29.76% | -6.92% / -16.71% / -2.75% |
| SPY | `next_open_to_close` | 10 | -48.65% | -57.31% | -18.35% / -27.30% / -13.49% |
| QQQ | `close_to_close` | 0 | +14.42% | +10.06% | +5.23% / -7.30% / +17.29% |
| QQQ | `close_to_close` | 1 | +3.66% | -0.37% | +1.80% / -10.33% / +13.55% |
| QQQ | `close_to_close` | 5 | -30.18% | -33.11% | -10.86% / -21.48% / -0.25% |
| QQQ | `close_to_close` | 10 | -57.40% | -59.35% | -24.50% / -33.49% / -15.17% |
| QQQ | `next_open_to_close` | 0 | -2.90% | +10.06% | +4.78% / -7.83% / +0.53% |
| QQQ | `next_open_to_close` | 1 | -10.40% | -0.37% | +2.03% / -10.42% / -1.97% |
| QQQ | `next_open_to_close` | 5 | -35.04% | -33.11% | -8.27% / -20.11% / -11.37% |
| QQQ | `next_open_to_close` | 10 | -56.54% | -59.35% | -19.69% / -30.75% / -21.86% |

## Primary advance criterion

At one basis point per side, the **SPY next-open-to-close** model had complete
coverage, but its fold returns were **+3.36%, −7.14%, +6.79%**. Its mean excess
return over the comparator was **+1.084, −0.381, −2.289 bps per observation**.
It therefore fails both the every-fold-positive net return and every-fold-positive
excess return requirements. QQQ transfer also fails and cannot rescue SPY.

The delayed target preserves a small forecast-accuracy advantage. The evidence
does not support blaming the entire original lead on inaccessible overnight returns.
Instead, this positive-sign, fully funded daily policy fails to convert the forecast
into stable excess returns. Frequent round trips produce substantial cost drag;
even SPY’s positive aggregate result at one basis point trails the comparator.
Relative outperformance at higher costs can simply reflect fewer losing round trips,
not a viable policy. No retrospective sign/threshold/cost tuning is authorized by
this result.

## Failure investigation

The [independent postmortem](alpha-open-gap-postmortem-2026-09-17.md) reproduced
forecasts/cash flows and verified influential vendor data. It found concentrated
pooled error improvements, unstable day selection and a prediction-loss/action
mismatch. Positive pooled skill does not establish a robust predictive edge.

## Decision and next work

1. Retain the failed hypothesis and all scenarios; do not spend a qualification
   holdout or expensive execution replay on this standalone policy now.
2. Resume **A2b session acquisition and durable decision scheduling**, including
   restart/revision/missed-window handling and measured publication availability.
3. Then add causally aligned panel/relative hypotheses and test incremental
   forecasts against frozen references. Keep current gates, weekly mining cadence
   and shadow-only combination boundaries.

Actual auction/quote execution, corporate-action/total-return semantics, point-in-time
universe membership, job recovery and target-aware deployable combinations remain
in the [canonical roadmap](alpha-roadmap.md). No rejected result is silently dropped.

## Reproducibility and verification

Private artifacts live under
`~/.local/state/agentic-trader/research/open-gap-policy-20260917/`: the frozen
protocol, completion report, per-run manifests, predictions and full payoff arrays.
The shared journal retains all attempt charges, discovery exclusions and hashes.
Raw arrays, runtime state and logs remain out of Git.

- `protocol.json` SHA-256: `a4d08ca03acef206e8a22a62d4f48ed861c9259f97d6776dee3069bc24d06160`.
- `completion.json` SHA-256: `95123d1eccbfa8a9909d63b1b0343991ea1d36f69ee960bef705eaf1f3a4460a`.

TDD covered independent cash-flow arithmetic, both target endpoints, complete cash
clocks, fold boundaries, missing prices, future/holdout perturbation and overnight/
daytime planted controls. Review corrected CLI enum values and added explicit
native-daily clock checks. Full suite: **1,166 passed, 22 opt-in skips**. Real
HTTP/WebSocket/PostgreSQL integration: **80 passed**, with the two affected SDK/PG
cases repeated after clock hardening. All-file pre-commit passed.

These checks establish implementation behavior, not a calibrated discovery rate
or profitable trading strategy. Deployment freshness is verified separately after
merge/restart and recorded on the PR.
