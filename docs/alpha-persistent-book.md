# Persistent ETF book experiment

The frozen [persistent-etf-v1 protocol](../config/research/persistent-etf-v1.json)
tests whether slower signals and retained inventory survive realistic cost stress.
It is a new development experiment, not a rerun or rescue of the failed
[sector-panel study](alpha-sector-panel-2026-09-17.md). No promotion gate changes.

## Frozen comparison

- Same nine legacy sector ETFs; SPY supplies the beta reference. This curated
  surviving-fund cohort is not point-in-time equity membership or historical borrow
  eligibility. Acquire 2020–2023; 2020–2021 warmup, separate 2022/2023 evaluation
  folds. These are development periods, not untouched confirmation.
- Slow momentum: `roc(close,126) - roc(close,21)`. Residual momentum subtracts
  trailing 126-session beta times that SPY score. Monthly reversal: `-roc(close,21)`.
  None is selected or sign-flipped after looking at results.
- A fourth score combines all three using trailing 126-session cross-sectional
  rank-score covariance, 20% diagonal shrinkage and nonnegative minimum-variance
  weights summing to one. Exact duplicate histories are deduplicated before fitting.
  Only observed scores enter estimation; no future labels. This models redundancy,
  **not expected return**. Covariance observations are dependent day×asset samples,
  not a statistical sample-size claim. Fitted weights/covariances are retained.
- All scores use the same top/bottom-three tie-aware half-long/half-short targets.
  Rank IC uses the next open to twentieth subsequent close, restricted to each fold;
  the final 20 labels remain explicitly immature. Existing cross-sectional Spearman,
  HAC (20 lags) and declared 252-observation/year diagnostics are reused.
- Rebalance every 20 observed sessions. Compare (a) full round trips at the same
  opening price, (b) net trades retaining inventory, (c) net trades with a 10% L1
  portfolio-weight buffer. The buffer skips the whole small rebalance; it does not
  round individual positions independently. No interval/buffer sweep.
- Scenarios pair per-side turnover costs / annual short borrow / negative-cash
  funding: **0/0/0**, **1/100/300**, **5/300/500 bp**. These are assumptions, not
  measured spread, impact or broker borrow rates. No positive-cash interest or
  short-proceeds rebate. No discounting the stress scenario after results.
- **80 charged comparisons:** four scores × two folds × (one IC + three book modes
  × three cost/carry scenarios). The complete budget and exclusions for every member,
  SPY and warmup are journaled before any calendar/bar acquisition.

The reset control liquidates/reopens at the *same* scheduled open. It isolates
unnecessary round-trip turnover without giving either mode a different overnight
holding window. It is not the earlier study's five-session disjoint basket policy.

## Accounting and information clock

`persistent_book.py` is a pure target-book diagnostic, not a second live execution
engine. It reuses panel scores and basket allocation, while the bracket simulator
continues to own bracket-policy research. There is no new order queue, schema,
registry, event bus, broker adapter or execution authority.

At close *t*, observed equity and closing prices fix the desired quantities. Those
quantities trade at the next observed session's opening proxy. Opening gaps cannot
resize the order retroactively. Costs apply to absolute **traded notional**;
retained inventory is not recharged. Cash and signed inventory reconcile every day:

`change in equity = price P&L − trading fees − short borrow − cash funding`.

Carry uses ACT/365 elapsed UTC time across the observed Alpaca calendar, including
weekends, holidays, DST and early closes: previous-close marked exposure overnight,
post-trade opening exposure during the session. Each fold starts flat, includes cash
dates, marks daily and explicitly liquidates terminal inventory at the final close
with costs. Reports retain daily quantities/cash, all cost components, net/gross
exposure, drawdown, pre-liquidation inventory and final zero inventory. No surviving
terminal position is silently discarded. Insolvency or missing evidence fails.

Targets have zero net and gross at most one **at the decision mark**. Gaps, costs
and subsequent returns can change these ratios. Maximum observed exposure is reported;
this research book does not implement live intermediate-exposure risk admission,
rounding, liquidity limits, partial fills or protective-order ownership.

## Corporate actions and source contract

Use native daily **Alpaca SIP, adjustment=all**. Alpaca documents split, dividend
and spin-off adjustments in its [bar API](https://docs.alpaca.markets/us/reference/stockbars).
The adjusted-price unit is a synthetic total-return proxy, not a physical share.
Dividends are represented by the provider's adjusted price ratios and are **not**
credited again as cash. Short exposure owes that adjusted return and the assumed
borrow charge. Exact payment-date cash, withholding, lending availability, historical
eligibility and corporate-action delivery are not modeled. All-in side costs remain
proxies for spread/slippage/fees, not a complete market-impact model.

Current-vintage back-adjustments are not point-in-time event records. Dimensionless
ROC/beta/ranks and monetary accounting are invariant to later constant per-symbol
rescaling of the historical prefix; tests cover that invariant. Provider corrections
and exact action-time reinvestment still require independent evidence before any
execution claim. Historical publication is assumed complete by next local midnight;
native daily open/close values are not verified auction fills.

The shared provider retains raw JSON pages **of the requested adjusted series**,
request bounds/adjustment, normalization and immutable hashes. “Raw response” does
not mean “unadjusted price.” Missing/invalid members fail; no fill, intersection,
fallback provider or repair algorithm quietly supplies observations. Original failed
raw-price studies remain unchanged. See [feed access](research-data-sources.md).

## Screen, tests and operation

Every score/mode must have complete coverage, at least 200 matured IC dates per year,
positive IC each year and mean IC ≥0.03, at least 20 actual rebalances overall,
positive net return each year in both primary and stress scenarios, stress drawdown
≤20%, and no 20-session block contributing more than half the primary net log gain.
Undefined metrics fail. These are **further-research screens**, not multiple-testing
adjusted significance, qualification, shadow credit or paper profits.

The simulator has explicit null/planted-spread accounting controls, duplicate and
complementary-score controls, future-perturbation tests, fixed/net/buffer comparisons,
exact cash/fee/weekend carry checks, rescaling invariance and missing-data rejection.
Those synthetic controls test mechanics; they are not a calibrated false-discovery
study. Real SDK HTTP pagination/capture and SQLite/disposable PostgreSQL tests exercise
the shared journal, acquisition failure, exclusions-before-access and replay.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  nice -n 10 uv run copilot alpha book-study config/research/persistent-etf-v1.json \
  --output /private/path/new-directory
```

Commit code/protocol before acquisition. The existing `AlphaPanelService` owns
reservation/acquisition/artifacts/journal and receives the pure study computation
by injection; work runs off the async loop. CLI starts no daemon/poller, submits no
orders and sends no Telegram messages. All outcomes, including failures, remain
charged. Review full matrix, IC/fold stability, costs, concentration and turnover;
no best-of-failures promotion or in-place protocol retuning.

## Separately frozen IEX comparison

The operator selected evaluation of IEX as a new feed. Before either acquisition,
freeze [persistent-etf-iex-v1](../config/research/persistent-etf-iex-v1.json) with the
same universe, periods, scores, costs and screens; only campaign identity/feed differ.
It charges another **80 comparisons** (160 total across the two independent journals).
Compare complete coverage, IC, returns, turnover and failure reasons, retaining all
results. IEX's single-exchange bars are never merged with SIP bars. Neither successful
research nor current IEX request access changes production feed, creates an active
alpha or authorizes delayed-SIP/session substitution. Prospective IEX receipt timing,
coverage, independently qualified versions and execution evidence remain necessary.
