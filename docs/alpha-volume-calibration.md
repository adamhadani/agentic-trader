# Source-specific volume calibration

`copilot alpha volume-study PROTOCOL.json --output NEW_PRIVATE_DIRECTORY` fits
per-symbol daily relative-volume profiles on declared training intervals and measures
their behavior on later intervals. It uses the existing panel application service,
trial journal, inspected-period exclusions, raw SDK receipts and immutable artifacts.
It does not change a screener, switch the production feed, qualify an alpha or trade.

## Definition and boundaries

`VolumeContract` binds feed, timeframe, adjustment and bar layout. A
`VolumeCalibration` additionally binds symbol, policy, training interval, full training
volume/availability hash and its empirical reference distribution. Serialized profiles
round-trip with an exact version and content ID. `apply` rejects a different feed,
symbol, price adjustment or clock, future/incomplete bars and insufficient warmup.

- Relative volume is observed volume divided by the **preceding** N-bar median;
  the current bar is excluded from its denominator. Default N = 20.
- The training reference uses only observations complete by the declared cutoff.
  A default 90th-percentile threshold uses linear quantile interpolation; an event
  requires a strict exceedance. At least 200 training ratios are required by default.
- Forward percentiles use midranks against that frozen training distribution. Ties
  remain ties: a constant series does not become an extreme-volume event.
- Scaling an entire series by a positive constant preserves relative volume. Its
  source data hash still changes. No estimated multiplier converts IEX to SIP volume.
- Missing/negative/nonfinite volume fails. Observed zero is zero; a zero reference
  median is unavailable and fails the complete study. Missing dates never disappear
  through an intersection or fill. The shared observed-calendar panel checks coverage.
- Initial support is native **daily** bars. Intraday/session profiles are rejected
  until time-of-day, early-close and session-seasonality semantics are implemented.
  Historical native bars are assumed available at the next local midnight. This is
  not measured publication latency or point-in-time corporate-action evidence.
- Full profile documents and forward date-level ratios, ranks, events and summaries
  are retained in `result.json`, bound by the journal's artifact hash. No profile is
  installed automatically. Reuse requires the exact contract and causal cutoff.

Alpaca documents IEX as one exchange and SIP as all US exchanges; its split adjustment
changes both prices and volume. See [bar contracts](https://docs.alpaca.markets/us/reference/stockbars).
An IEX percentile describes relative activity on that feed, not consolidated market
capacity, borrow availability or predictive return. Current-vintage adjusted history
still requires care around corporate actions.

## Existing feature audit

The catalog/search predominantly uses volume ranks, sign changes and correlations;
a constant positive volume rescaling already leaves those operators unchanged.
This does **not** make their signals identical across feeds: participation changes
through time. The squeeze screener uses volume/current-inclusive 20-bar SMA and its
configured `volume_factor` (default 1.3). This study's preceding-median ratio and
1.5 reference threshold are a new diagnostic definition, not a retuned squeeze policy.
Absolute volume/dollar-volume capacity must continue to specify its source. Production
strategy identity and thresholds are unchanged.

## Frozen development experiment

Freeze and commit `config/research/volume-sip-v1.json` and `volume-iex-v1.json`
before acquisition. Both request adjusted native daily bars for SPY plus the nine
sector ETFs, 2020-07-27 through 2023-12-31. This established cohort isolates the feed
calibration question; the discovery roadmap now explicitly includes individual stocks.

Two chronological folds: train on 2021, evaluate 2022; train on 2022, evaluate 2023.
Pre-training observations supply the 20-bar warmup. For each symbol/fold compare the
frozen 90th-percentile threshold with a fixed 1.5 relative-volume reference: **40
charged comparisons per feed, 80 total**, including failures. Report train/forward
event rates, threshold, median forward percentile, coverage and exact profile IDs.
No threshold search, return labels, IC/P&L screen, automatic drift pass or promotion.

These years have already been inspected. “Forward” here means chronological
out-of-training evaluation on development history, not a fresh qualification holdout
or prospective live observation. SIP/IEX see the same markets, so are not independent
replications. Later event rates need not equal 10%; dependence, ties and distribution
changes prevent an IID guarantee. Preserve results even if calibration is unstable.

## Validation

TDD covers independent numeric quantile/midrank references, positive rescaling,
future perturbations, mismatched identities, invalid/late observations, zero
baselines and frozen serialization. CLI tests verify retained failures/trial counts
and unchanged registry. Parameterized actual SDK TCP + SQLite/disposable PostgreSQL
integration covers both feeds, pagination, exact adjustments, missing data, provider
rejection, exclusions before reads and journal replay. Source tests and deployed
health verification are recorded separately in the delivery PR.
