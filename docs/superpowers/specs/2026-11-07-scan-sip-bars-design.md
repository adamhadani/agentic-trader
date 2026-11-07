# Scan bars on delayed SIP

Date: 2026-11-07. Operator, 2026-11-07: "yes go ahead" to the native-supply diagnosis, then "once ur happy with
it go ahead and push/merge". Design calls are mine, under the standing design trust.

## 1. Why

Native cards almost stopped:
- 2 native cards in the 9 sessions from Oct 26 to Nov 6;
- 7 ranked candidates in 14 days;
- the latest scan found 0 setups across 157 names.

The diagnosis is in [native supply 2026-11-07](../../native-supply-2026-11-07.md).

- **Not sleep.** Every slot missed since Oct 22 is the Oct 27–29 trading halt, and none has been missed since
  Oct 30. Earlier missed slots were a few host-sleep misses in September, plus swing and daemon-start scans
  spending session slots before Phase 0 (#129).
- **Not the live pipeline.** On the 17 scans since Oct 22, live `raw_setups` (16) matches the unchanged
  strategy engine replayed on IEX bars (17).
- **The feed.** The same engine on SIP bars finds 51 setups on those 17 scans, against 17 on IEX.
  - Over Sep 1 – Nov 6, SIP gives 3.50 setups a scan and IEX 2.34.
  - Since Oct 22, SIP gives 3.00 and IEX 1.00.

  IEX prints are a small share of consolidated trades, so 4h highs, lows, RSI and ATR built from them diverge.
  In this tape that divergence hides about two-thirds of the TREND_PULLBACK setups, and most squeeze breakouts
  (47 on SIP against 11 on IEX).
- **The evidence was measured on SIP.** The card-stats replay prior, the setup base rates and the bracket
  counterfactual all used SIP. So the live card stream has been a thinner, differently selected population
  than the evidence that judges it.

## 2. What changes

### Settings
- `market_data.scan_bars`: `configured` or `sip_delayed`.
  - **Code default `configured`:** today's behaviour, where bars use `market_data.alpaca_feed`, which
    `ALPACA_DATA_FEED` overrides.
  - **`config.yaml` ships `sip_delayed`.**
- `market_data.sip_delay_minutes`: default 16, from 15 to 60.
  - **Why delayed:** the free plan serves SIP only for request ends at least 15 minutes old. A recent SIP
    request returns HTTP 403, and the composite provider would then fall back to yfinance silently.
  - **Why it costs nothing:** setups use bars completed by the decision time. At 10:35 that is the 9:00 bar,
    which ended at 10:00, so a 16-minute delay hides no bar the setup logic needs. The in-progress bar now
    runs to 10:19 instead of 10:35.

### Effective feed
`MarketDataConfig` gains `bars_feed` (`sip` under `sip_delayed`, otherwise `alpaca_feed`) and `bars_end_delay`
(a `timedelta` under `sip_delayed`, otherwise `None`).

### The end clamp
`AlpacaDataProvider` gains an `end_delay: timedelta | None`.
- When set, `fetch_bars` and `fetch_daily_many` clamp the request end to `now − end_delay`. A missing end, or
  one later than that, takes the clamped value; an earlier explicit end is kept.
- Evidence capture records the clamped end.

### Consumers that move to the effective feed and delay

All of them use one definition, so their units stay consistent:
- **Scan bars:** the `MarketDataFetcher` provider (`data/market_data.py`).
- **`build_bar_source` default feed** (`research/setups/sources.py`): the dynamic universe's daily batch, and
  the `cards outcomes` and card-stats labellers. An explicit `feed=` argument (`build_daily_bar_source`'s
  `sip` with `DelayedBars`) is unchanged.
- **The dynamic-universe `feed` label** and the operator-reference feed match (`agent/copilot.py`). The
  liquidity threshold is in the feed's units, so a cached IEX reference is ignored and recomputed on SIP. This
  is the existing feed-match rule.
- **The card-stats `feed` label** (`research/setups/card_stats_worker.py`). It now equals the prior's `sip`.
- **The `doctor` market-data probe** (`diagnostics/doctor.py`). It probes the effective feed. Under
  `sip_delayed` its end is `now − delay`, and its message says it checked delayed SIP access.

### Unchanged
- Broker latest trade and quote (`alpaca_data_feed`, real-time IEX), and `fetch_latest_price` (the
  subscription's default feed).
- Research CLIs (`cli/commands/alpha.py`), which pin their own feed identity in manifests and caches.
- The prospective observation and decision workers (`alpha_pipeline.*.feed`).
- Strategies, thresholds, risk rules, sizing and the LLM gate.

### Effects to expect
- A native card's entry level is the latest 4h close, now as of about 16 minutes before the decision rather
  than the decision time itself.
- The tap gate still re-prices on the live IEX quote.
- An auto-entry's session-lifetime limit order may fill a little less often when the price has risen since.
  The research semantics this matches decided on the completed bar and filled at the next hour.

## 3. Anti-goals

- **No silent yfinance fallback for SIP.** The clamp exists so no SIP request is ever recent.
- **No change to quotes, positions, streams or broker identity.**
- **No migration.**
- **No new card source, threshold or gate.** The change only stops the live feed from hiding setups the
  strategies already define.

## 4. Verification after deploy

- The Monday 10:35 and 14:35 scans journal `raw_setups` in line with the SIP replay: about 3 a scan in this
  tape, against about 1 on IEX.
- The dynamic block's `feed` is `sip`.
- `doctor --readiness` market data is ready.
- No `yfinance` fallback appears in bar evidence.
