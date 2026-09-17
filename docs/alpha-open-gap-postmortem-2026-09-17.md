# Why the open-gap experiment failed

**September 17, 2026.** Follow-up to the [frozen 20-attempt experiment](alpha-open-gap-policy-2026-09-17.md).
This is a post-hoc audit of its existing hypotheses, not another search or a fresh
qualification test. No thresholds, model specifications or holdouts were changed.

## Conclusion

**No arithmetic, label-alignment or model-fitting defect was found in the audited
experiment.** Independent calculations reproduce its predictions and daily returns.
The rejection is explained by weak, unstable day selection and substantial turnover
costs. There are also research-design and reporting weaknesses: a small pooled
squared-error improvement was too easily described as a useful prediction lead,
without showing its concentration or how it translated into the chosen trading rule.
Passing tests and rejecting a candidate do not establish that the pipeline is fully
calibrated or that no undiscovered bugs remain.

| Category | Finding | What it establishes |
| --- | --- | --- |
| Implementation | Independent OLS, labels, folds and cash flows reproduce saved results. | No discrepancy found in this experiment's numerical path. |
| Data | All 1,001 discovery-session dates present for both ETFs; influential April prices exactly match a fresh Alpaca read. | No missing-session or current-vendor mismatch found. It does not establish historical publication timing or independent exchange verification. |
| Model | Delayed-target SPY forecast skill is negative in the middle fold and highly concentrated in one observation. | The pooled 1.86% improvement is fragile, not broad evidence of predictable returns. |
| Trading rule | Forecast magnitude is discarded; positive forecasts buy every day regardless of costs. | Better squared error need not improve this sign-based policy. |
| Economics | Losing-period SPY entries underperform the days it skips, even before costs. | Costs amplify an existing selection problem; they are not the sole cause. |
| Acceptance rule | Every-fold positive net and excess returns were a strict, frozen triage rule. | Failure rejects this experiment's advancement, not every possible use of the feature. |

## 1. Independent implementation audit

The private audit reads the saved discovery prefix and reconstructs the calculation
without importing the production DSL, label builder, fold generator, scikit-learn
estimator, payoff evaluator or return-statistics helper:

- Feature: `open[t] / close[t-1] - 1`.
- Labels: `close[t+1] / close[t] - 1` or `close[t+1] / open[t+1] - 1`.
- Three expanding folds, independent arithmetic for training/validation boundaries;
  all training outcomes mature before their validation fold.
- OLS slope from centered covariance/variance and intercept from training means.
  Maximum absolute prediction discrepancy across all four saved runs: **3.47e-18**.
- Cash accounting from shares purchased with one unit of wealth, including entry
  cost, then shares sold at the closing proxy net of exit cost. Predictions determine
  only the next bar's position; fold-first bars stay in cash. All saved one-basis-point
  model/comparator returns and positions match to numerical tolerance.
- All 16 saved cost scenarios' hashes, complete clocks and compounded reported returns
  were already checked during deployment. Cost drag has a separate exact check:
  terminal gross wealth times `((1-c)/(1+c)) ** number_of_round_trips` equals net wealth.
- No duplicate timestamps, nonfinite/nonpositive OHLC values or inconsistent OHLC
  ranges were found in either discovery dataset. The final holdout was not fitted
  or scored. Loading an immutable file for integrity/boundary verification is distinct
  from using its holdout values in a model.

Permanent [independent reference tests](../tests/research/test_alpha_forecast_reference.py)
cover both endpoints at horizons one/five, purged OLS forecasts, scalar cash flows
and all three tested cost assumptions. A small constructed example also demonstrates
that positive squared-error skill can coexist with poor directional accuracy and
exactly the same trades as the constant comparator.

A GET-only Alpaca calendar audit found exactly **1,001 expected / 1,001 observed**
discovery dates per symbol, with no missing or unexpected sessions. Fresh SIP/raw
daily bars for April 7–11, 2025 matched the saved OHLCV values exactly for SPY/QQQ.
This refetch corroborates the vendor data, not contemporaneous availability or a
realizable auction fill. Alpaca documents [trade-condition-based bar aggregation](https://docs.alpaca.markets/us/docs/market-data-faq)
and [raw versus corporate-action adjustments](https://docs.alpaca.markets/us/reference/stockbars).

## 2. The headline forecast improvement is concentrated

For the **next-open-to-close** target:

| Symbol | Pooled MSE skill | Fold 1 | Fold 2 | Fold 3 | Largest observation's share of net squared-error improvement |
| --- | ---: | ---: | ---: | ---: | ---: |
| SPY | +1.862% | +0.448% | −1.348% | +3.051% | 100.34% |
| QQQ | +0.498% | −0.186% | −1.262% | +1.476% | 169.90% |

The largest contribution is the **April 8 decision predicting the April 9, 2025
open-to-close return**. SPY's realized target was +11.18%, its forecast +0.349%,
and its fold training-mean forecast about +0.0205%. Predicting a slightly larger
positive number reduces a large squared error substantially, even though both
forecasts produce the same long trade. Other observations offset that improvement,
which is why the contribution can exceed 100% of the net gain.

As an influence diagnostic only, omitting that one contribution from the already-fixed
error sums turns SPY's remaining skill to **−0.0094%** and QQQ's to **−0.4623%**.
No data was removed from the original results, no model was refitted on a reduced
sample, and this is not permission to discard a real extreme observation. It shows
fragility. Squared error gives large misses disproportionate weight; this is expected
behavior of the [OLS objective](https://scikit-learn.org/stable/modules/linear_model.html#ordinary-least-squares),
not a discovered numerical bug.

The original close-to-close lead was also concentrated: the same decision supplied
58.56% of SPY's and 62.27% of QQQ's net error improvement. Their tiny improvements
in each original fold were descriptive screening results, not independent statistical
confirmation after searching 78 trials. Our earlier phrase “predictive value survives”
should be read narrowly as **positive pooled error reduction on inspected discovery**.
It does not establish a stable or statistically significant edge.

## 3. The model chose poorly in the losing period

SPY delayed-target policy, with the original fixed decisions:

| Validation execution period | Days entered / eligible | Mean gross return on entered days | Mean gross return on skipped days | Model return at 1 bp/side |
| --- | ---: | ---: | ---: | ---: |
| September 18, 2023–May 14, 2024 | 131 / 166 | +4.70 bps | −3.17 bps | +3.36% |
| May 16, 2024–January 14, 2025 | 136 / 166 | −3.26 bps | +4.12 bps | −7.14% |
| January 16–September 15, 2025 | 117 / 166 | +8.31 bps | +9.80 bps | +6.79% |

The middle fold is not a marginal cost failure: entered days lose on average before
costs, while skipped days gain. In the third fold, both sets gain, but skipped days
gain more. At one basis point per side, skipping those days explains the negative
mean excess returns of **−0.381 and −2.289 bps per observation** in folds two/three.
The identity is exact: model-minus-comparator return equals the negative comparator
return on skipped days. The original criterion also fails at zero costs.

The fitted SPY slope stays positive across folds (approximately 0.121, 0.116, 0.095);
there is no slope-sign inversion or scaler conversion defect. But training on past
average relationships is not sufficient: in fold two, mean forecast is +3.67 bps,
actual mean return is −1.93 bps, and forecast/target Pearson correlation is about
−0.005. This is consistent with an unstable/noisy relationship; the audit does not
identify a proven economic cause or establish a particular market-regime model.

## 4. Trading-policy and hypothesis limitations

**Loss function versus action.** OLS predicts return magnitudes by minimizing squared
error. The screen takes only their sign and uses the same fully funded exposure for
+0.01% and +1%. That deliberate simple baseline throws away information and does not
optimize net utility, risk-adjusted exposure or incremental benefit over the incumbent.
A magnitude-aware or cost-aware policy is a new hypothesis requiring a frozen protocol
and fresh evidence, not an automatic fix to this rejected one.

**Cost hurdle.** For this cash-flow formula, a positive net trade requires the gross
price return to exceed `2*c/(1-c)`, about **2.0002 bps** at one basis point per side.
The policy's threshold is zero and has no such hurdle. It makes **384 SPY round trips**,
so even 1 bp/side multiplies gross terminal wealth by **0.926075**: +10.67% gross becomes
+2.49% net. At 5 bps/side it becomes −24.62%. These are assumed costs, not measured
spreads or fees. The fixed comparator's 498 round trips incur the same per-trade costs.

**Feature age.** The tested hypothesis is yesterday's completed-day `open_gap`
predicting the *next day's* return. It is not a test of fading or following this
morning's gap during the same session. The one-bar lag is intentional and causal,
not an indexing accident. A same-session hypothesis must specify the first actual
available gap observation and an achievable later entry; it cannot observe the opening
price and also assume an order filled at that same price without timing evidence.

**Target meaning.** Raw close-to-close targets include unadjusted distribution gaps;
raw `open_gap` can encode ex-dividend effects. The delayed intraday payoff holds no
overnight position and receives no dividend in this proxy. These are not total-return
or corporate-action-aware features. There is no evidence here that this limitation
caused the rejection, but it prevents a broad economic interpretation of the feature.

**Acceptance/power.** “Win in every fold” is a conservative triage requirement, not
a calibrated hypothesis test. With 166 eligible observations per fold, it can reject
weak useful effects. We preserved it because it was frozen, not because failure proves
zero alpha. Dependence-aware uncertainty and positive-control power remain unfinished.
We must not weaken the rule on these results or treat failure as proof of robustness.

## 5. What changes next

1. **Improve diagnosis before selecting another lead.** For every proposed lead,
   surface fold-level skill, observation-level error concentration, forecast versus
   realized distributions, entered/skipped returns, exposure/turnover and cost drag.
   Retain fitted model parameters or a reproducible reconstruction alongside arrays.
   These should extend the existing benchmark report/artifacts, not create a second
   research journal or trading authority. Existing arrays made this audit possible,
   but the headline report did not expose these weaknesses clearly enough.
2. **Write the economic hypothesis before choosing the label and action.** State
   information availability, horizon, comparator, net-return objective and the proposed
   use of forecast magnitude. Any same-session, cost-threshold or sizing comparison
   must be charged/predeclared and keep its final evaluation untouched.
3. **Continue A2b durable forward decisions**, collecting actual availability and
   missed-window/revision evidence. This closes a deployment gap; it is not expected
   to manufacture profitability for the rejected open-gap model.
4. **Then widen toward aligned relative/panel hypotheses**, using matched budgets
   and the existing gates. More genetic/ML search alone would not address the present
   objective mismatch or unstable evidence.

The standalone policy stays paused. No new candidate search, strategy tuning,
qualification, promotion, broker mutation or Telegram message was performed for
this postmortem. Reconstructing the exact frozen fit and attributing its existing
errors added no new candidate specification or trial charge. This post-hoc evidence
cannot be used as independent validation for a subsequently redesigned strategy.

## Evidence

Private evidence: `~/.local/state/agentic-trader/research/open-gap-postmortem-20260917/`.
It retains both audit scripts, numeric summaries, the GET-only vendor response and
SHA-256 manifest. No raw data, credentials or account details enter Git.

- Independent audit SHA-256: `7566d25b32d4137a502006dd695f48659835475013fddbbc1f51f31447163690`.
- Data verification SHA-256: `0152b33a2f47b7b990759dfad8039674d21d1a9cf7fe0fea739ea5bf8641da3d`.

Tests added here are independent regression references for existing behavior, not a
claim of a new feature's RED→GREEN cycle or of a runtime bug fix. Runtime/model
behavior and the frozen experiment remain unchanged.
