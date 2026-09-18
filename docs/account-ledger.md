---
layout: default
title: Broker account activity ledger
---

# Account activity ledger

Schema `006_account_ledger` adds individual broker activity evidence to the shared
append-only `domain_events` journal. `activity_projections` and `ledger_checkpoints`
are replayable read models. This is account-level reconciliation, not tax-lot or
per-strategy allocation. Broker-backed sliced orders remain disabled.

## Accounting authority

Alpaca uses weighted-average cost intraday and compressed FIFO overnight. A local
FIFO guess can therefore disagree with its positions. For USD equities we compute:

```
gross realized = all execution cash flows + signed broker open cost basis
net realized + income = gross realized + signed fees + signed dividends/interest
```

Buy executions reduce cash; sells and `sell_short` increase it. Individual partial
executions retain their exact activity and order IDs, quantities, prices and times.
Open cost basis and unrealized P&L come directly from broker wire decimal strings.
All arithmetic uses Decimal. External orders are included without inventing signal
ownership. Existing tracked full-close wins/losses remain a separate subset and
are never added to account totals.

Before publishing account realized P&L, all available activities must reconcile to
broker cash (at most one cent rounding tolerance) and signed position quantities
(exactly). Account identity, cash, quantities and cost basis must remain unchanged
across the import. Market-price movement alone does not invalidate it. A race,
missing history, unknown activity, unsupported asset/currency or failed request
withholds account realized totals and exposes the reason. Zero is not substituted.
The response displays its observation time; this is not account-day return.

Supported non-trade activities: cash transfers/journals `CSD`, `CSW`, `JNLC`, fees
`FEE`, `CFEE`, `DIVFEE`, dividends `DIV`, and interest `INT`. Deposits/withdrawals and
cash journals are excluded from profit. Other corporate actions, stock transfers,
options/crypto accounting and tax treatment require explicit support. Reconciliation
is evidence that the available history balances; it cannot independently certify
broker history completeness or classify an undocumented cash journal as profit.

References: [account activities](https://docs.alpaca.markets/us/docs/account-activities),
[pagination contract](https://docs.alpaca.markets/us/reference/getaccountactivities-2),
[cost-basis methods](https://docs.alpaca.markets/us/docs/position-average-entry-price-calculation).
The installed TradingClient has no typed activities helper; its public GET method
uses the existing bounded SDK transport and preserves exact decimal strings.

## Import, concurrency and recovery

An independent daemon worker refreshes every 60 seconds. It reads full available
history in ascending activity-ID pages (100/page), bounded by `accounting.max_pages`
(default 100). Saturated or duplicate/nonadvancing pages fail visibly. Full scans
catch late fees and revisions whose transaction date predates a timestamp cursor.
This intentionally favors correctness for a small account; large histories need a
reviewed incremental strategy. Do not simply raise limits indefinitely.

The first import permanently binds the environment/account-mode scope to the
broker account ID. A different account is rejected. Each importer receives a
fencing token before remote reads; a superseded importer cannot replace newer
results. A separate ledger lock keeps remote acquisition outside trading transactions.
Final admission briefly acquires it after the trading lock to pin risk evidence. No DB transaction spans broker I/O. A successful full read commits changed
activities, removals and its broker snapshot/report together. Repeated observations
are deduplicated; revisions and reversions append events, and removed activities
append retractions. Failed imports preserve prior evidence and record an error.
Rebuild replays events and invalidates outstanding importer tokens.

`/readyz` includes current-run accounting freshness. Import errors, reconciliation
failures or missing/stale observations degrade readiness; they do not automatically
set the emergency halt. New Alpaca entries require valid risk evidence;
exits and protection remain available. Default freshness is
180 seconds. `/perf` uses the latest cached reconciliation with an explicit timestamp;
`copilot perf` refreshes the ledger read-only first. Neither submits orders or sends
synthetic bot messages. Interactive replies retain normal Telegram behavior.

## Operator commands

```bash
uv run copilot perf                 # account and tracked performance, same renderer
uv run copilot db ledger            # cached checkpoint without broker account identifier
uv run copilot db ledger --sync     # refresh read-only broker evidence
uv run copilot db activities        # private raw evidence with exact activity/order IDs
uv run copilot db ledger --rebuild  # DB replay only; no broker requests
uv run copilot db events --stream account/ledger
uv run copilot db events --stream activity/ACTIVITY_ID
uv run copilot doctor --readiness
```

Configuration under `accounting`: `refresh_seconds: 60`, `max_age_seconds: 180`,
`max_pages: 100`, `cash_tolerance: 0.01` (may be tightened, never greater than a cent).
Refresh must be shorter than freshness. Keep raw activities/account identities in
private storage, out of Git and shared diagnostics. Never delete events needed for
replay. Back up PostgreSQL before migration 006; deploy only after integration
verification, then restart the single registered daemon and verify readiness,
current revision, Telegram command descriptions and broker reconciliation.

## Cash-flow-adjusted drawdown and entry admission

The same fenced ledger checkpoint now persists versioned account risk evidence;
there is no second importer, risk queue or schema. Equity is explicitly **derived**
from broker cash + signed open cost basis + unrealized P&L, using Decimal. It is not
an independently requested broker equity field or a portfolio-volatility estimate.
For the first reconciled positive-equity observation, retain baseline equity `E0`,
cumulative deposits/withdrawals `F0`, and its observation time. Subsequently:

```text
adjusted_equity = equity - (cumulative_external_flows - F0)
high_water_mark = max(previous_high_water_mark, adjusted_equity)
drawdown = max(0, (high_water_mark - adjusted_equity) / high_water_mark)
```

Deposits and withdrawals do not manufacture profit or erase drawdown. Fees, dividends
and unrealized losses affect performance. This is a **fixed-baseline dollar-equity**
series, not time-weighted return. Monitoring begins at the recorded first baseline;
we do not invent earlier peaks. Sixty-second observations can miss intervening peaks.
Restart and journal replay preserve the baseline and sampled high-water mark.

Alpaca `CSD`/`CSW` provide supported signed external flows. `JNLC` only establishes
that a cash journal occurred. Journals already present at the first reconciled baseline
are frozen as starting history, without being classified as profit or external flows.
New, revised or removed journals in a subsequent reconciled observation invalidate
risk history persistently, as do revisions or retractions of accepted transfers.
An unreconciled import blocks risk temporarily; it can recover if the source reverts
before a valid observation establishes permanent invalidation. Existing P&L reporting
is unchanged. Fixing invalidated source history requires a
reviewed recovery; `/resume`, restart or replay cannot reset the baseline or waive
an invalidation. [Alpaca activity definitions](https://docs.alpaca.markets/us/docs/account-activities).

Alpaca scans require fresh reconciled risk before evaluating candidates. The drawdown
multiplier applies to **every sizing tier and the hard per-trade cap**, including
explicit operator quantities. Configured `portfolio.cash` remains a separate capital
mandate. The current policy starts reducing size above 3% drawdown, retains the
configured 10% minimum multiplier until the 6% threshold, then blocks new entries.
These values come from `sizing.drawdown_*` and `max_drawdown_stop_pct`; they are not
broker-side orders or a guarantee against gap losses. Nonpositive observed equity
also blocks new entries even if a withdrawal left adjusted drawdown unchanged.

Authorization checks risk under the trading → ledger lock order. Preflight refreshes
through the injected ledger service. The final `SUBMITTING` transaction rechecks
freshness, the exact risk fingerprint, the cap and the lease after acquiring locks.
A changed/expired observation rejects the original request with a reason; no order
is resized or replayed. Queue/submission events retain the admitted risk observation;
signal provenance retains its fingerprint. Missing, stale, failed or unsupported
risk evidence blocks new entries rather than becoming zero drawdown.

This is a derived entry gate. It does not alter the emergency halt or prevent closes,
protective-order maintenance or reconciliation. Empty local dry scans read no broker
risk; their zero drawdown describes that simulated portfolio. Other local simulations
do not claim this broker-account drawdown protection.

The existing accounting readiness check now includes risk availability for Alpaca.
`db ledger` displays risk without account/transfer identifiers; `db events` retains
private evidence. The passive runtime verifier includes current risk and separately
reports whether the drawdown gate permits new entries. Funding/borrowability and
aggregate portfolio risk remain separate open admission work (survey S3).
