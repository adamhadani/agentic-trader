# Prospective IEX collection and scoring

The September 17 forward audit retained 78 SIP decision failures, all with raw
HTTP 403 receipts. They establish acquisition failure, not a failed predictive
score. The operator selected IEX for a separately validated feed evaluation.

## Configuration and identity

`alpha_pipeline.observations.feed` and `alpha_pipeline.decisions.feed` independently
select `alpaca:sip` or `alpaca:iex`. Desk YAML selects IEX for SPY/QQQ. Trading data
still uses `market_data.alpaca_feed`; there is no implicit inheritance or fallback.
The injected worker policy is the sole source of acquisition feed identity.

The existing daemon owns both bounded workers and their SDK clients. No second
daemon/poller, queue, event schema or execution path is introduced. Read/scoring
work stays off asyncio; shutdown drains in-flight work before closing clients.

The committed [IEX diagnostic cohort](../config/research/iex-forward-v1.json)
predeclares all three existing control formulas on SPY and QQQ, without selecting
on historical outcomes. Each has a new logical alpha ID and immutable IEX version.
Import using the existing explicit command after deployment:

```bash
uv run copilot alpha import config/research/iex-forward-v1.json
uv run copilot alpha forward --days 7
```

Original SIP versions, cursors, failures and artifacts remain retained. A worker
only enrolls exact-feed versions; other-feed candidates are reported with
`feed_not_configured`. The candidate budget applies to the configured feed. Registry
changes conservatively re-enroll cursors; no retroactive scoring or repeated failed
windows. Forward diagnostics consume observed periods but add zero formula-search
trials and grant no qualifying shadow credit. Every session entry/activation gate
remains in place.

## Acceptance and limits

Report actual complete observations, scored/unavailable decisions, missing minutes,
receipt lag, request duration and runtime identity separately from `/readyz`.
HTTP 200 alone is not complete coverage. Keep all failures and source hashes;
do not fill missing minutes or shorten warmup after observing failure.

The existing observation contract requires all RTH minutes from session open;
decision scoring requires complete configured history and enough causal warmup.
IEX is a separate feed: SIP evidence cannot establish IEX availability or score
parity. The daily volume profiles cannot calibrate intraday seasonality. The volume
control keeps its explicitly limited local relative-volume formula.

## Verification

TDD first reproduced missing feed validation, inherited trading feed and mixed-feed
registry rejection. Unit regressions cover policy identity, exact-feed filtering,
visible excluded-feed warnings, no future/backfilled decisions, loop responsiveness
and client draining. Real SDK HTTP and independent SQLite/PostgreSQL clients exercise
exact IEX/raw/one-minute requests, concurrent consume-once claims, source failures
and event replay without orders or qualification credit.

Actual deployed receipt/coverage evidence is recorded separately after restart.

## Deployed evidence — September 17, 2026

PR #59 passed 1,519 full-suite tests (70 skipped) and 200 SDK/network/PostgreSQL
integration tests before squash merge. Revision `620b5d8` was installed through a
controlled launchd restart, with the six committed IEX controls imported while the
single daemon was stopped. Original SIP definitions remain retained: generation 16,
active 0/shadow 16. No research orders or Telegram messages were sent.

The read-only runtime report verified all 17 readiness checks, matching broker/DB
positions (flat), Telegram polling age 9.61 seconds, and event-loop lag 0.0021 seconds.
Current-run logs had no warnings/errors; two bar-acquisition warnings in the wider
log slice belonged to the previous runtime. Private deployment evidence is under
`~/.local/state/agentic-trader/reviews/iex-forward-20260917/`.

### Acquisition works; the dense-minute decision contract still rejects history

At the **17:45 UTC** signal close, the first SPY and QQQ observations contained all
**255/255** expected RTH minutes. Receipt upper bounds were **5.65s** and **6.11s**.
By 17:46:40 UTC there were eight complete repeated observations, with zero unavailable
observations. These are a short observation window, not elapsed forward qualification.

All six candidate decisions for that candle were **unavailable**, with no score:

| Symbol | Expected historical RTH minutes | Missing | Raw rows including extended hours |
| --- | ---: | ---: | ---: |
| SPY | 3,375 | 2 | 3,425 |
| QQQ | 3,375 | 52 | 3,575 |

A separate calculation using the stored raw JSON and NPZ, without application imports
or new provider reads, reproduced all missing timestamps. Every raw/normalized timestamp
and OHLCV value agreed; both responses exhausted pagination and normalization dropped
zero rows. This is source absence under the existing complete-minute contract, not
local filtering loss or the earlier SIP entitlement refusal.

[Alpaca's aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq)
permit absent minute bars when trades do not establish eligible OHLC/volume fields.
That is a possible explanation, not proof of the cause of these particular omissions.
A separate trade-condition/source investigation and versioned sparse signal-bar versus
execution-price contract would be needed before changing treatment. Do not impute,
splice feeds, shorten warmup after seeing failures, or reinterpret these frozen results.
Native-daily broader-equity research can proceed independently of this intraday gate.
