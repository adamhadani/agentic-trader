# Broker funding, borrowing and aggregate entry capacity

Entry admission shares one pure policy across the preflight worker and final
transaction. CLI, Telegram and the daemon continue to use `EntryExecutionService`
and the durable FIFO. No second queue, journal or schema is introduced.

## Evidence and capital

Alpaca acquires typed account, asset, trade, bid/ask, inventory and exact order-group
evidence. Account and complete book observations bracket the read interval. Cash,
identity, restrictions, inventory and order changes refuse the attempt without
retrying it. Market marks and buying power may change: both account observations
are retained and the lower capacity is used. Reads run off the event loop with
existing HTTP deadlines. Final admission ages evidence from the **first request**,
not the final response (`execution.entry_evidence_max_age_seconds`, default 30).
Quote/trade age and market-session limits also apply after lock waits.

Risk capital is the lesser of the configured mandate and observed equity. Scans
use ledger equity; final admission also includes both fresh account observations.
There is no artificial $1,000 capital floor. The account-risk fingerprint, claim
lease, current approved signal and current reservations are checked under the
existing trading → ledger lock order. Fill/status and quarantine writers participate
in the trading lock, so a concurrent capacity change cannot bypass the final check.
Queue and signal age are rechecked after lock waits, independently of claim lifetime.

## Funding and instrument eligibility

The account must be active, USD, and unrestricted for trading; the asset must be
active and tradable. The supported contract is a whole-share equity limit bracket
with DAY/GTC lifetime. Direction and broker side must agree.

For brackets that can remain overnight, this application conservatively uses the
lesser of `buying_power` and `regt_buying_power`, plus
`non_marginable_buying_power` for a nonmarginable long. This is an explicit local
policy, not a reconstruction of the broker's intraday margin engine. Long entry
funding uses quantity × max(limit, observed ask). Short funding uses quantity ×
max(limit, 1.03 × observed ask). Broker buying power already reflects broker-held
entry orders; those commitments are not deducted a second time.

The FIFO head receives funding priority. Later queued approvals reserve configured
notional/stop-risk capacity, but funding remains provisional until their own turn.
They can be refused when conditions change. Closing orders do not supply projected
proceeds or release risk before confirmed execution/reconciliation.

Short entries also require enabled shorting, margin eligibility, at least $2,000
observed equity and current `borrow_status="easy_to_borrow"`. Hard-to-borrow locates
are unsupported. Missing or malformed evidence refuses admission. The adapter uses
the SDK's public raw account/asset GET methods and strict models: the installed SDK
asset model requires the deprecated `easy_to_borrow` field and cannot preserve the
replacement field. There is no legacy flag fallback or old PDT/$25,000 gate.

The configured quote feed is retained explicitly. IEX quotes are venue observations,
not consolidated NBBO; no feed fallback or relabeling occurs. These are conservative
local budget checks, not a promise that Alpaca will accept the order. Broker prices,
funding and stock availability can change after observation. POST remains authoritative
and is never automatically retried after an ambiguous response.

## Aggregate exposure and protection

`portfolio.max_stop_risk_pct` defaults to **2%** of effective risk capital, reduced
by the existing drawdown multiplier. The sum includes current positions, queued
commitments and the proposed order exactly once. For an exact protected holding,
budgeted risk retains the larger of its original reserved risk and current
mark-to-stop giveback. Stop prices can gap; this is a planned stop-loss budget,
not a guaranteed maximum loss, hard volatility constraint or portfolio CVaR limit.
Existing name/class/gross/count/correlation and per-trade caps still apply.
Proposed notional uses the larger of the approved limit, observed ask and last trade;
an older approved price cannot understate observed exposure. Existing holdings retain
the larger recorded/broker-mark exposure. These observations are snapshots, not a
bound on subsequent price moves.

An order is a protective exit only through exact parent/replacement identity.
Symbol and opposite side are insufficient. Pending entries must retain their
exact approved bracket. Filled entries require matching inventory and confirmed
entry cost basis, plus one exact
`new` stop of the correct side and full quantity. Held, accepted, pending or
partially filled exits do not establish verified active protection. Any exit fill,
partial ownership, broker-only holding, unrelated order or missing evidence blocks
new risk until reconciliation/review. Admission does not cancel or repair those orders.

## Audit, tests and operations

Successful `entry_submitting` events retain account/asset/quote/book evidence and
calculated funding, capital and exposure totals. Rejections retain the reason and
available context in the existing work result/outbox. Account IDs and order evidence
remain private database data; never copy raw records into Git or shared reports.

Regression coverage includes capital floors, direction mismatch, boundary budgets,
partial/external/changed protection, malformed and changing HTTP evidence, and final
transaction revalidation. Real SDK loopback HTTP tests exercise both successful POST
and zero-POST refusal. Disposable PostgreSQL tests verify cross-client writer locks
and final capacity checks. These source tests are separate from deployed checks.

After merge/restart, run `doctor --readiness` and `scripts/verify_runtime.py`; verify
current-run broker reconciliation, polling and revision. A closed market legitimately
prevents live entry preflight; do not submit a synthetic order to test a connection.
Closes, protection monitoring and the emergency halt remain separate workflows.

## Primary broker contracts

- [Order funding and lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca).
- [Margin and short selling](https://docs.alpaca.markets/us/docs/margin-and-short-selling).
- [Current intraday margin framework](https://docs.alpaca.markets/us/docs/the-intraday-margin-rule).
- [PDT API migration](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/).
- [Borrow-status migration; legacy flag removal September 22, 2026](https://docs.alpaca.markets/us/changelog/2026-06-05-borrow-status-6b96a5a).
