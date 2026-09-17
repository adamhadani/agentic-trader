# Forecast-to-fill contract hardening — September 17, 2026

This implements the safety fixes from the [tutorial review](forecast-to-fill-review.md).
It does not establish profitable alphas or authorize joint portfolio execution.

## Corrected boundaries

- **Participation measures trades:** `abs(target - filled_holding)` is bounded by
  observed trade capacity. Position limits separately constrain the final holding.
  Nonzero pending orders block shadow allocation until a reachable-risk envelope
  and executable plan model exist; an unfilled order is not inventory.
- **Explicit forecast units:** calibration, combination and covariance inputs carry
  `ForecastContract(target, feed, adjustment, bar_layout, currency)`. The target
  includes timeframe, holding horizon and return label. Covariance rows are labelled
  by outcome availability, not by the start of their return interval. The input
  producer must supply returns matching this contract; metadata cannot prove their
  economic correctness. Mismatches, future/stale risk data and nonfinite forecasts fail.
- **Causal calibration:** fitting requires aligned outcome-availability timestamps,
  all no later than the training cutoff. Miner calibration uses completed bar times
  and the existing purged training slice. The original caller was already purged;
  this makes that guarantee enforceable at the calibration boundary too.
- **Distinct uncertainties:** outcome return volatility remains descriptive.
  Forecast-mean standard error comes from OLS coefficient covariance with HAC errors,
  at least horizon-minus-one lags and configured minimum lag count. Scores are clipped
  consistently in fitting/prediction. This conditional estimation error does not
  cover search selection, regime change or model misspecification.
- **Dependency fencing:** exact expression/normalization families and identical
  calibration evidence cannot gain extra weight through renamed versions. Combination
  uses explicit positive weights and a worst-correlation standard-error bound.
  Distinct correlated expressions still require causal joint fitting and redundancy
  experiments; this is not a learned optimal blend.
- **Robust shadow objective:** expected return minus variance penalty, trading cost
  and `uncertainty_aversion * sum(standard_error * abs(weight))`. The last term is
  a box-uncertainty penalty, not a covariance estimate or a 95% confidence promise.
  Builder default aversion is 1; zero explicitly disables it. Instruments with no
  forecast have zero expected return and no forecast-error term, but retain portfolio
  variance, transaction costs and all position constraints.
- **Reviewable solves:** reports include components of the objective, solver/library
  versions, timing, constraint margins/duals, covariance eigenvalues and independent
  primal checks. Strict OPTIMAL is still required. These are diagnostics, not a full
  independently verified KKT certificate. Position-count selection remains an explicit
  deterministic heuristic, not globally optimal mixed-integer allocation.

`alpha portfolio SNAPSHOT.json --output REPORT.json` requires a new immutable report
path. It retains exact input, canonical JSON SHA-256 and output with private file
permissions. Input forecasts require `contract`, `standard_error`, `families` and
`calibration_ids`; the top-level `risk_contract` must match. Old untyped calibration
artifacts cannot be silently upgraded. Shadow observations journal their rejection;
existing historical research and qualification records remain intact.

## Retired divergent mechanisms

The legacy `optimize`/`retune` CLI commands, automatic Saturday retuner, its config
export/reporting path and optional VectorBT dependencies are removed. The NumPy
retuner repeatedly added accumulated unrealized P&L and used different execution
rules/trial accounting. Fixing just its arithmetic would leave two incompatible
research pipelines. Historical outputs are retained as untrusted historical evidence.
Use journal-backed `alpha mine`, `benchmark`, panel/replay and explicit qualification;
these did not use the faulty retuner. Common execution-ledger tests verify a marked
profit is counted once, both before and after closing.

The uncalibrated `fractional_kelly` sizing mode is removed. Supported modes are
validated `static` and `volatility_targeted`. No assumed win rate is presented as a
learned edge. A separate discovered sizing defect is fixed: minimum size cannot
resurrect a trade after exhausted notional/risk capacity or a drawdown halt. Such
results contain zero quantity and an explicit blocking reason.

## Operational read contracts

One bounded read-only executor serves fallback wrappers (four workers; injectable
for tests). Synchronous callables execute off the asyncio loop. A timeout returns
without waiting for executor shutdown, discards late results and does not replay an
unfinished request. Busy workers retain slots until completion; overload fails fast
instead of queueing unbounded work. Python cannot terminate a running thread: a
noncooperative provider can still occupy all slots, so real transport deadlines matter.

Broker, historical stock/crypto and calendar adapters share bounded official Alpaca
SDK transports. GET retains SDK retry behavior; POST/PATCH/DELETE never automatically
retry. A socket timeout applies per attempt; SDK GET retries/pagination can outlive
one caller deadline, but cannot publish their discarded result. This mechanism must
never wrap a trade handler. Other provider-specific transports still need their own
socket/cancellation contracts.

Active `doctor` now probes the exact configured feed in the most recent minute.
An HTTP rejection identifies access failure; a successful empty response proves
request permission, **not** fresh prices. No IEX substitution is inferred. Current-run
`/readyz`, actual bar receipts and execution-feed freshness remain separate checks.
See [Alpaca access semantics](https://docs.alpaca.markets/us/docs/market-data-faq) and
[Python executor shutdown semantics](https://docs.python.org/3/library/concurrent.futures.html).

## Remaining priorities

1. Automated lossless provider/normalization evidence. The [SPY postmortem](alpha-spy-minute-postmortem-2026-09-17.md)
   found the same four minutes absent from fresh raw responses; routine capture remains open.
2. Resolve current recent-SIP access without silently changing feed identity.
3. Frozen persistent-book, slower-turnover ETF research with full costs and causal
   dependence controls. Preserve the failed campaign, trial charges and promotion gates.
4. Executable portfolio plans, reachable pending risk, rounding, partial-fill ownership
   and protective orders, using the existing FIFO/close services/journal/outbox.

Tests and live deployment verification are recorded separately in development notes.
