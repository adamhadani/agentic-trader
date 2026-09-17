# Observed-session alpha replay

This is the first engineering increment of **A2b** in the [alpha roadmap](alpha-roadmap.md).
It provides a reproducible session/minute replay and data-coverage diagnostic.
**Intraday qualification remains blocked.** Live signal-clock migration and broker
execution observations are still required before that gate can change.

## Why a separate execution clock

Alpaca minute bars are labelled by their interval start; larger intraday bars
aggregate those minutes, including eligible extended-hours prices. Brackets cannot
execute in extended hours. Filtering a mixed-session hourly bar cannot recover
its regular-hours OHLC. Minute and native daily bars also use different trade-condition
rules, so a minute-derived session bar is a distinct data contract.
See [Alpaca bar construction](https://docs.alpaca.markets/us/docs/market-data-faq)
and [bracket semantics](https://docs.alpaca.markets/us/docs/orders-at-alpaca).

`market/bars.py` owns pure observed sessions and aggregation. The shared
`data/sessions.py` SDK adapter supplies
calendar open/close times, interpreted in exchange time and normalized to UTC.
It never falls back to a weekday/holiday approximation. The
[calendar endpoint](https://alpaca.markets/sdks/python/api_reference/trading/calendar.html)
provides early-close boundaries as well as trading dates.

- Execution uses completed raw one-minute bars inside `[session_open, session_close)`.
  DST changes each session's UTC offset; holidays contribute no fake observations.
- Signals aggregate those minutes from **session open**, with layout `rth_open_v1`.
  The last bucket is clipped at the observed close, including early closes.
  Supported signal intervals are 15m, 1h, 4h and a full regular session (`1d`).
- A signal bar is unavailable until its complete interval ends. Forming/future
  values do not affect an earlier replay prefix. Timezones, ordering, duplicates,
  unknown timestamps, invalid OHLC and adjustment/feed mismatches fail explicitly.
- Every expected regular-session minute must be observed. No interpolation, flat
  price substitution or silent dropping of missing market observations. Coverage
  failures retain expected/observed/missing counts and example missing timestamps.
  An absent bar can reflect no eligible trades or a data gap; absence alone cannot
  establish that a held position had a known flat return.
- Extended-hours bars are retained in the input artifact but excluded from signal
  aggregation and execution. No entry or bracket exit can be attributed to them.

## One strategy/execution mechanism

### Clock isolation before live migration

Existing alpha versions use the `fixed_duration_v1` clock: a start-labelled bar
closes after its declared duration, with the established UTC convention for naive
inputs. Research manifests still require timezone-aware observations. Daily closure
on this clock is start plus 24 hours, not an inferred exchange-session close.
`market/bars.py` now owns `fixed_bar_closes` and `completed_fixed_bars` for research,
live screening and shadow observations; there is no research-module import shim.

Explicit `rth_open_v1` or unknown layouts cannot pass through that clock. Unknown,
duplicate or unordered timestamps and conflicting timeframe metadata also fail.
Research validation rejects incompatible input; live screening emits no candidate;
shadow records the rejection and earns no observation credit. New research manifests
record their clock, while immutable definitions and valid fixed-clock calculations
retain their existing identity/meaning. Resampling must explicitly update timeframe
metadata at the transformation boundary.

The [prospective observer](alpha-forward-observations.md) now uses this same session
adapter/window clock to retain actual REST receipt and revision evidence. It is
independent of strategy scoring and does not qualify or reinterpret any definition.

These guards prevent accidental reinterpretation, including session-derived daily
bars. New version-3 definitions now accept explicit receipt-stamped session snapshots
through [shared screening/shadow decision selection](alpha-session-decisions.md).
A dedicated diagnostic worker now acquires those snapshots and schedules durable
session decisions; normal trading scans retain native data. Activation remains blocked
until forward execution and qualification evidence are complete.

### Shared execution state

Both the existing coarse simulator and session replay use the same immutable
`BracketIntent` construction and `simulate_execution` state machine. The new clock
maps completed observations to execution eligibility; it does not duplicate fills,
fees, pending-order lifecycle or bracket calculations in a second simulator.

`SessionClockPolicy.decision_delay_seconds` defaults to 60 and explicitly represents
an assumed publication/decision delay. Eligibility is the first regular-session
minute starting at or after signal close plus that delay. Zero models idealized
instant availability, not measured operational latency. Several delayed observations
mapping to the same minute use the latest decision, including a latest no-signal.
An observation is evaluated once; a stale coarse score cannot re-enter every minute.
New v2 replay artifacts also expire unsubmitted decisions outside their versioned
window (default 120 seconds after eligibility). Already-submitted GTC orders persist unless a new immutable [timed execution policy](alpha-trade-lifetimes.md) declares an entry lifetime.
See [versioned decisions](alpha-session-decisions.md) for historical v1 differences.

Untimed accepted GTC limits remain pending across closed sessions, independently of later
feature availability. Folds can start flat without inheriting an old order or replaying
an earlier signal. Existing conservative stop/target ordering, gap handling, both
fill costs and marked equity apply on the finer clock. A trailing change based on
a minute's close becomes effective only on a subsequent execution bar.

Results retain minute equity returns, completed signal bars, availability/eligibility
and supersession decisions, plus simulated order-created, entry-filled, stop-updated
and exit-filled events. Timestamps identify **OHLC bar starts and phases**, not known
exchange fill times. These events are diagnostics, not broker executions or P&L.

The coarse daily contract remains unchanged: eight retained A2a development runs
(random/genetic × four scenarios) reproduced every scientific field exactly after
extracting the shared engine. This is parity evidence, not fresh calibration.

## Command and durable evidence

```bash
uv run copilot alpha replay 'delta(close,3)' \
  --symbol SPY --interval 15m --start 2024-11-27 --end 2024-11-29 \
  --decision-delay-seconds 60 --output /private/path/new-run
```

Dates are inclusive exchange dates, bounded to 31 calendar days. `--feed iex|sip`
defaults to the configured feed. An explicit feed comparison does not change the
daemon's feed. Calendar and bars use injected, bounded SDK clients and GET requests.
No daemon, broker connection/stream, order submission or Telegram notifier starts.

Unlike synthetic calibration, this command uses the configured research journal:

1. Publish an exclusive private manifest with the frozen formula/strategy policy,
   replay policy/layout/version, source/dependency identity and as-of boundary.
2. Reserve **one lifetime research attempt**, then durably exclude the inspected
   interval from fresh holdouts, before any provider access. Failures and crashes
   do not refund the attempt or release the interval.
3. Capture raw minute data (NPZ), the observed calendar and input hashes. Compute
   aggregation and replay off the asyncio loop; preserve unavailable results.
4. Publish `result.json` atomically, with input/manifest hashes. Retain its location,
   hash, plan, coverage/status and reserved count in an immutable diagnostic projection
   using the existing `domain_events` journal. Rebuild restores it without I/O.

No new table, order queue or notification mechanism is introduced. Diagnostic
results are separate from qualification runs and do not enter the current coarse
trial-variance sample. Lifetime attempt counts still include them. A failed diagnostic
exits nonzero; inspect retained evidence rather than rerunning until it passes.
Existing output directories are never overwritten. Artifacts are private (0600)
and must remain outside Git. A hard interruption can leave a reserved running job;
durable campaign resumption remains A5, with no automatic replay/refund.

## Predeclared deployment-feed smoke test

[The frozen plan](../config/research/a2b-replay-v1.json) declares four attempts before
inspection: SPY, `delta(close,3)`, 15m, 60-second assumed delay, IEX and SIP, over:

| Case | Exchange dates | Expected RTH minutes | Expected completed signal bars |
| --- | --- | ---: | ---: |
| DST transition | 2024-03-08 through 2024-03-11 | 780 | 52 |
| Thanksgiving / early close | 2024-11-27 through 2024-11-29 | 600 | 40 |

Retain missing coverage or denied-feed results. No feed fallback, date replacement,
formula selection or threshold tuning follows inspection. This checks software/data
contracts, not alpha profitability, statistical power or superiority of a feed.
The [September 16 results](alpha-session-replay-2026-09-16.md) retain both SIP
completions and both IEX coverage failures, with no simulated entries or promotions.

## Remaining promotion gates

This increment is **A2b groundwork**, not authorization to activate intraday alphas:

1. Verify the now-implemented durable worker against actual forward sessions for the
   versioned session-derived signal and freshness contract. Native provider bars remain different;
   new replay results cannot be silently substituted for existing qualifications.
2. Quantify publication/revision and operator/broker acknowledgment delays with actual
   forward observations. Historical corrected bars cannot establish point-in-time
   availability. See [Alpaca updated bars](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).
3. Reconcile partial fills, buy-stop conversion/working protection, spread/slippage,
   corporate actions, dividends, borrow and funding. Full-size OHLC fills and next-bar
   stop updates are assumptions; bracket DNR/DNC semantics do not make corporate
   actions economically disappear. Existing raw-price friction is not complete cost accounting.
4. Exercise prospective paper-fill comparisons through the existing entry/close and
   journal boundaries, with qualified versions and preserved position ownership.
   Only then replace the unconditional intraday gate with evidence-based eligibility.

Continue A2b before A3 forecast/trading-policy alignment and broader mining. Synthetic
or historical replay earns no shadow dates/decisions and cannot promote an alpha.
