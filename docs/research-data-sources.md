# Research data access — September 17, 2026

## Verified account behavior

Read-only requests at 15:46 UTC used the installed credentials without printing
keys, headers, account IDs or raw runtime logs. Private responses and an allowlisted
summary live under `~/.local/state/agentic-trader/reviews/feed-access-20260917/`.

| Probe | Result |
| --- | --- |
| SPY minute SIP, end=now | HTTP 403; exact subscription restriction message; 199/200 requests remaining |
| Same SIP endpoint, end=20 minutes ago | HTTP 200, one requested observation returned |
| SPY minute IEX, end=now | HTTP 200, one requested observation returned |
| Finnhub SPY daily candles, existing key | HTTP 403, access to resource denied |
| yfinance SPY daily, five days | Five rows, aware New York clock, action/adjusted-close columns, no missing rows |

This SIP response is an **entitlement restriction**, not an exhausted rate limit.
Alpaca's [FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) requires historical
SIP `end` at least 15 minutes old without the subscription; latest SIP endpoints
also require it. Its [bar API](https://docs.alpaca.markets/us/reference/stockbars)
documents HTTP 429 for throttling and rate-limit response headers. A generic 403
alone could also mean credentials or permissions; the exact message and successful
same-key delayed/IEX controls establish this diagnosis.

The [current plan table](https://docs.alpaca.markets/us/docs/about-market-data-api)
lists Basic as free with real-time IEX and historical data excluding the latest
15 minutes (200 historical calls/minute); Algo Trader Plus lists full real-time
US-exchange coverage at $99/month. Verify dashboard billing before any purchase.
No subscription purchase or automatic runtime feed switch was performed.

Historical 2020–2023 SIP experiments are unblocked. A delayed SIP observation must
retain its real receipt time and cannot satisfy a live strategy's shorter decision
window. Changing an alpha from SIP to IEX changes its data contract and requires
new version/evidence; single-exchange volume/coverage is not interchangeable with SIP.
A successful request also does not prove complete/fresh price coverage.

## Free-source assessment

| Source | Use for this pipeline | Constraints |
| --- | --- | --- |
| Alpaca historical SIP | Primary for the frozen ETF study; existing SDK, calendar, evidence capture and broker identity | Explicit past end bound; no permission for recent SIP; daily adjusted prices are research proxies |
| yfinance / Yahoo | Independent daily price/action checks and exploratory hypotheses; installed and connectivity verified | Unofficial interface; personal-use framing; no SLA or point-in-time action vintage; retain unmodified rows and settings |
| Finnhub | Existing calendar/fundamental uses may remain useful | Our key cannot access historical candles; do not assume a free quote/calendar key includes OHLCV history |
| Twelve Data Basic | Candidate keyed daily-data cross-check if coverage/action access is verified | [Published free quota](https://twelvedata.com/pricing): 8 API credits/minute, 800/day; endpoint and exchange entitlements need a separate probe |
| Alpha Vantage | Occasional independent checks | [Free quota](https://www.alphavantage.co/support/): 25/day; [daily adjusted history/action endpoint](https://www.alphavantage.co/documentation/#dailyadj) is premium, limiting usefulness for this total-return experiment |

No new keys/accounts were created. Finnhub's [candle documentation](https://finnhub.io/docs/api/stock-candles)
is client-rendered; the empirical account rejection is stronger evidence of our
current access than assuming a pricing entitlement from generic “free API” language.

For Yahoo checks, explicitly use `auto_adjust=False`, `back_adjust=False`,
`actions=True`, `repair=False`, `keepna=True`, bounded timeout and declared timezone.
Its [download contract](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
defaults to automatic OHLC adjustment and omitting missing rows; intraday history is
limited and cannot replace our multi-year minute replay. Even with `auto_adjust=False`,
Yahoo history is not guaranteed to be original as-traded unadjusted prices; split
semantics and separate adjusted-close ratios must be checked. The project's
[repair documentation](https://ranaroussi.github.io/yfinance/advanced/price_repair.html)
describes missing/incorrect split/dividend adjustments and heuristic repairs.
Do not silently repair a frozen experiment. Its [README](https://github.com/ranaroussi/yfinance)
describes the unofficial research/personal-use scope.

**Recommendation:** keep historical SIP as primary; use yfinance as a separately
frozen daily/action cross-check when a candidate merits investigation. A source
comparison must reserve inspected periods/trials before analyzing outcomes, preserve
both source artifacts and diagnose discrepancies. Never fill primary missing bars
with another vendor or use vendor choice to rescue a failed economic screen.

### Historical IEX coverage finding

The first frozen ETF acquisition returned all ten series only from **2020-07-27**
(864 bars each through 2023), despite requesting January 2020. The matching raw
pages retain this boundary and no normalization losses. This observed response
limit is narrower than the generic plan table's history description; do not infer
IEX coverage from SIP availability. The original run remains failed. The separately
charged [follow-up protocol](alpha-persistent-book.md#iex-coverage-follow-up-separate-protocol)
changes only warmup start; research comparison and live feed validation stay separate.
