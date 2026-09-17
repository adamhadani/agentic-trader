# SPY missing-minute postmortem — September 17, 2026

**The fresh SIP raw responses omit the same four minutes before SDK bar parsing or
pandas cleaning.** Neither normalization loss nor chunk-size/pagination behavior
explains the gap in these new captures. This strengthens the upstream-data explanation
for the [continuous campaign failure](alpha-continuous-campaign-2026-09-17.md), but
does not reconstruct the original raw response or prove why Alpaca omitted the bars.

## Frozen comparison

Before provider access, a private immutable protocol fixed SPY, SIP, raw adjustment,
one-minute bars and two requests:

- June 5, 2023, **13:50–13:58 UTC**, including the four missing 13:52–13:55 minutes.
- The original acquisition chunk: June 5 05:00 UTC through July 6 04:59:59.999999 UTC.

The official SDK's `raw_data=True` retains all bar-row fields/null entries after
pagination and before `BarSet` parsing. This is a lossless bar-row capture, not an
HTTP packet/header archive. It was saved privately before normalization. The same
production provider normalization was then applied to the retained response, with
no second provider call, and compared with the original immutable dataset.

| Comparison | Raw rows | Parsed/normalized rows | Null / dropped rows | Changed common OHLCV rows | Target minutes absent from raw |
| --- | ---: | ---: | ---: | ---: | ---: |
| Narrow window | 5 | 5 | 0 / 0 | 0 | 4 |
| Original chunk | 16,654 | 16,654 | 0 / 0 | 0 | 4 |

There were no added/removed timestamps relative to the original dataset in either
comparison. The original dataset hash is unchanged. No prices were imputed and none
of the twelve failed SPY comparisons was rerun or relabeled successful.

Alpaca explains that bar availability depends on eligible trades. These captures
cannot distinguish trade-eligibility rules, supplier history problems or another
upstream cause. See [Alpaca's bar construction documentation](https://docs.alpaca.markets/us/docs/market-data-faq).
A support investigation can use this reproducible narrow request; no support message
has been sent automatically.

## Diagnostic failure retained

Both captures succeeded, but the first comparison script used the SDK request
model's UTC-naive `.start/.end` against a UTC-aware pandas index and failed with
`TypeError`. The failed result records remain intact. The corrected **offline**
comparison uses the frozen aware protocol bounds, reuses both saved raw responses,
and made no further market-data requests. This was a diagnostic-script defect,
not a newly discovered failure of the campaign's acquisition clock.

The two captures and one separately journaled offline comparison consumed three
conservative research attempts. There is no refund, new economic test, variance
sample, shadow credit or promotion. The registry stayed generation 10, with zero
active and ten shadow hypotheses. Research attempts increased 7,312 → 7,315.

## Retained evidence

Private directory: `~/.local/state/agentic-trader/research/spy-minute-postmortem-20260917`.
Raw prices, scripts, receipts and journal IDs stay outside Git.

| Artifact | SHA-256 |
| --- | --- |
| Frozen protocol | `5614f18263052b90774b11f777c9375b36bc888aba8326ecbc1a00f8db68d6ea` |
| Narrow raw rows | `5e776cfb4c7f3351b4ebb4ec711055a7ab18e20b33aadf3e9f9143487235ed08` |
| Original-chunk raw rows | `a2388c3397958e6b0c903d9f9641abc4a4e02bb54edc26226f0bc78fad0994cd` |
| Offline comparison | `bc2be475268bdcb2b1ef6d2f41c5e64cbfedd71d4daa5420853dae38d1386338` |

## Next work

Automate raw-row capture and normalization outcomes at the shared data boundary,
including failed parsing and interrupted pagination, with bounded storage/retention
and references in existing acquisition receipts. This forensic capture does **not**
mean that routine research/live acquisitions already retain every raw response.
Preserve strict minute completeness until a separately tested missing-data execution
contract exists. Continue toward the frozen slower-turnover ETF study; do not change
cost or promotion thresholds to rescue the earlier campaign.
