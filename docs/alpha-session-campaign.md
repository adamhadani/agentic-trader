# Predeclared ETF session campaign — v1

The [frozen protocol](../config/research/etf-session-v1.json) widens the economic
hypothesis funnel after the failed daily open-gap policy. It is a bounded exploratory
experiment, not a new qualification route. Freeze this protocol and runner before
reading results; do not change thresholds or retry unavailable cohorts afterward.

## Hypotheses and action

| Hypothesis | Information at the completed 15-minute bar | Intended action |
| --- | --- | --- |
| Four-bar momentum | Four-bar close return relative to its trailing 50-bar distribution | Continue an unusually strong move |
| One-bar reversal | Negative close return relative to its trailing 50-bar distribution | Fade an unusually large move |
| Volume-conditioned momentum | Four-bar return multiplied by current/20-bar mean volume, then trailing normalization | Continue unusually strong moves with higher local volume |

These are single-symbol hypotheses on **SPY, QQQ and XLF**. Four bars may cross an
overnight gap; this is not always one wall-clock hour. Volume normalization does not
remove intraday seasonality. The [published intraday-momentum study](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866)
motivates testing information timing, but its first-half-hour/last-half-hour setup is
different; this campaign is not a replication or a claim that its results transfer.
Cross-sectional residual/sector-relative features remain pending a causal panel contract.

Each immutable version uses the existing bidirectional z-score trigger at ±1.5,
normalization 50, raw SIP 15-minute session bars and a decision window
[close + 60s, close + 180s). The existing bracket engine owns resting GTC limits,
ATR/structural protection, 2R targets and initial-risk trailing. Full minute-clock
net equity payoff is the target; no squared-error predictor is mapped into an
unrelated sign-only trading policy. Exact policy parameters are in the protocol.

## Budget, chronology and comparison

Three chronological blocks: June 2024, November 2024 and March 2025. These are
historical discovery/stress blocks, **not untouched qualification holdouts**.
November includes a DST transition and Thanksgiving early close. Each block starts
flat and retains warmup/cash minutes; there is no fitting or parameter selection
between blocks. This is chronological causal replay, not a claim of trained
walk-forward cross-validation. Future observations cannot change an earlier signal.

The budget is **81 strategy attempts**: 3 hypotheses × 3 symbols × 3 blocks ×
0/1/5 basis points per side. Cash and passive long are frozen comparators, not searched
alternatives. Passive long enters at the first observed RTH open with the same entry
friction and marks at the last close. Model and comparator terminal inventory is
marked, not forcibly liquidated; pending orders and open positions remain visible.
Compounding across separated blocks is diagnostic wealth, not a continuous backtest.

All comparisons within a cohort share one captured calendar/raw-minute snapshot.
Provider failures are retained and reused across the declared comparisons; there is
no automatic refetch to improve coverage. Each attempt uses `AlphaReplayService`,
which reserves its trial and excludes inspected dates before price access, retaining
its private inputs/result and journal record. Unstarted attempts after a crash remain
incomplete against the frozen 81 denominator; they are not silently replaced/refunded.
No orders, messages, activation or qualification are possible through this runner.

## Frozen triage

A symbol/hypothesis advances only to further research if it has:

- All primary/stress blocks available with complete execution coverage and ≥80% scored signal bars.
- At least 30 closed trades across primary blocks and positive net return in at least 2 of 3 blocks.
- Positive aggregate selected-block net return and excess over passive long at 1 bp/side.
- Positive aggregate net return at 5 bp/side.
- Largest positive daily log-equity contribution ≤50% of the signed total log gain.

These are descriptive, conservative triage rules, not calibrated statistical tests or
promotion credentials. A new economically specified experiment has its own preregistered
rules; it does not retroactively relax the failed open-gap experiment's criterion.
No required number of winners, parameter retuning or fresh final-holdout use follows.

## Prospective diagnostic cohort

Independently of historical winners, the protocol freezes all three hypotheses on
SPY/QQQ at the 1 bp policy for subsequent receipt-aware forward diagnostics. This
six-version cohort includes rejected hypotheses as controls and avoids choosing only
historical winners. Registering them in shadow does not grant qualified shadow credit,
activate trading, or attest that they passed triage. XLF remains research-only here.
Actual future market sessions must elapse; an idle healthy worker is not a sample.

## Reproduction and limits

```bash
uv run python scripts/run_session_campaign.py config/research/etf-session-v1.json \
  --output /private/path/new-campaign-directory
```

This spends another declared research campaign; do not run it merely to inspect the
saved results. The output directory must be new. The manifest binds protocol/runner
hashes, all immutable replay plans and environment. Each trial has a result, summary,
input hashes and journal run ID; the final result retains every outcome and actual
source receipts. The runner uses injected data/storage orchestration beneath its CLI
composition and the existing replay service, with no second execution or storage system.

Raw bars and full OHLC fills do not measure partial fills, spread/queue priority,
borrow/funding, distributions, corporate actions, or broker stop acknowledgments.
No historical timestamp is treated as a live receipt. Session activation/entry gates
remain unchanged. Tests cover frozen budgets/identities, lookahead rejection, shared
snapshots/failures, independent cost/compounding arithmetic, concentration and missing
outcomes, plus actual SDK HTTP and PostgreSQL artifact/journal replay. Real run results
and deployed checks are recorded separately after execution.
