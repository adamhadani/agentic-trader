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
results. A separate ledger transaction lock avoids blocking entry admission during
large imports on PostgreSQL. No DB transaction spans broker I/O. A successful full read commits changed
activities, removals and its broker snapshot/report together. Repeated observations
are deduplicated; revisions and reversions append events, and removed activities
append retractions. Failed imports preserve prior evidence and record an error.
Rebuild replays events and invalidates outstanding importer tokens.

`/readyz` includes current-run accounting freshness. Import errors, reconciliation
failures or missing/stale observations degrade readiness; they do not automatically
halt existing trading. Entry risk checks remain independent. Default freshness is
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
