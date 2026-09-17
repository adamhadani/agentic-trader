# Continuous timed ETF discovery campaign

Frozen protocol: [etf-continuous-timed-v1.json](../config/research/etf-continuous-timed-v1.json).
This is a new development experiment after the [short-block campaign](alpha-session-campaign-2026-09-17.md),
not a retry or reinterpretation of its failures. Commit the protocol and tested
runner before acquiring its market observations. Results belong in a separate
report with the protocol/source hashes and every attempted comparison.

Completed: [September 17 results](alpha-continuous-campaign-2026-09-17.md), including
all 48 attempts and independent accounting checks.

## Question and fixed design

Do the existing momentum leads, or a new causal trend/pullback hypothesis, retain
positive cost-stressed returns with enough completed trades when stale entries and
holding duration have explicit limits? Retain the rejected one-bar reversal as a
control so selection is not restricted to prior winners.

- **48 attempts:** four hypotheses × SPY/QQQ × 2022/2023 × 0/1/5 bp per fill side.
- Every calendar year is one continuous regular-session minute execution path.
  January warmup happens once. Pending orders, held inventory, trailing protection,
  rolling features and cash returns do not reset at acquisition chunk boundaries.
- The two annual runs start flat independently. Compounded annual-run returns are
  selected-run diagnostic wealth, not an uninterrupted two-year broker account.
- Keep the prior 15m features, 50-bar normalization, 1.5 entry threshold, structural/
  ATR brackets, trailing policy and 60-second decision delay/120-second window.
- Declare **300 seconds resting / 86,400 seconds holding**, elapsed UTC, through
  the [shared timed policy](alpha-trade-lifetimes.md). These are frozen research
  choices, not fitted values or changes to existing positions. No duration sweep.
- New hypothesis: `roc(close,20) - roc(close,4)` contrasts a longer trailing move
  with the recent four bars. It is a testable trend/pullback hypothesis, not a claim
  of independence, literature replication or established profitability.
- These years are development history, potentially already inspected in daily
  research. They cannot be sold as fresh qualification/holdout evidence.

## Predeclared screen and decision

Advancement requires complete execution coverage, at least 80% feature coverage,
**100 aggregate completed trades**, positive primary-cost returns in both annual
runs, positive primary return above the compounded passive-long comparator,
positive aggregate return at 5 bp/side, and a largest positive day's contribution
no greater than 50% of total signed net log gain. Undefined metrics fail explicitly.
The zero-cost scenario diagnoses cost erosion and cannot rescue failed cost tests.

A pass earns **further research only**. Report statistical qualification, forward
shadow evidence and execution eligibility separately; session promotion is still
hard-gated. No automatic enrollment, promotion or order follows this campaign.
If everything fails, retain the denominator and explain economic failure versus
missing coverage, implementation defects or insufficient evidence. No retuning,
extra symbols or post-result reruns of these windows in this protocol.

After a promising result: freeze a finalist, assess calendar/subperiod stability,
trade attribution and correlation, then obtain untouched confirmation and actual
forward execution evidence through the existing pipeline. If the simple family
fails with adequate trade density, advance the causal ETF-panel/relative-value
work instead of repeatedly tweaking these formulas on these years.

## Acquisition and reproducibility

Replay accepts up to 366 inclusive exchange dates. `AlpacaSessionSource` obtains
at most 12 disjoint chunks of at most 31 elapsed days. The
[Alpaca endpoint uses inclusive bounds](https://docs.alpaca.markets/us/reference/stockbars);
the adapter subtracts one microsecond from each exclusive end at SDK datetime
precision. No boundary deduplication or missing-price imputation is allowed.
Each chunk must match feed/raw/minute metadata and have unique, ordered, aware
observations inside its exact requested bounds. SDK pagination remains unchanged.

Successful observations retain per-chunk request/receipt times, ranges and counts
in artifact metadata. Failures retain those receipts and the failing error; partial
chunks do not become a successful dataset. No whole-acquisition retry or feed
fallback. The campaign freezes one snapshot per symbol/year for all comparisons.
The complete range is excluded from future fresh holdouts before the first request;
every trial is charged and retained through the existing journal.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  nice -n 10 uv run python scripts/run_session_campaign.py \
  config/research/etf-continuous-timed-v1.json --output /private/path/new-directory
```

Use an explicit authorized research configuration when invoking from a worktree.
Tests always use isolated fixtures/DBs. This actual-data run writes intentional
research evidence to the configured journal; it sends no Telegram messages or orders.
Keep arrays/receipts private, retain failures, and audit arithmetic independently.
The bounded year is assembled in memory and simulated once; this is not a resumable
streaming research engine. Worker checkpoints/resource isolation remain on the roadmap.
