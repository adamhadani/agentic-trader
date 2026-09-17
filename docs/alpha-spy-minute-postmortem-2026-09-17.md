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

## Automatic capture follow-up

[Routine Alpaca bar capture](market-data-evidence.md) now retains decoded pages before
SDK parsing and cleaning, including earlier pages when pagination fails. It does not
retroactively reconstruct the original campaign response.

The frozen `raw_evidence_verification_v1` check exercised the production composition
at source `816c04d` against actual historical SIP. The narrow window retained five
rows in one page; the original chunk retained **16,654 rows in two pages**. Both had
zero null/cleaner losses, zero changed/added/removed timestamps or OHLCV rows against
the frozen original, and the same four target minutes absent from raw pages.
No coverage rule, failed study or promotion threshold changed.

Two conservatively charged engineering comparisons increased lifetime attempts
**7,315 → 7,317**. They test acquisition provenance, not economic hypotheses, and
provide no qualification or shadow credit. Private protocol, script, pages, receipts
and results live under `research/raw-evidence-verification-20260917` and `market-data`.

| Verification artifact | SHA-256 |
| --- | --- |
| Frozen protocol | `225c62fe6f348e92a90c9543573514b916b7c91b547665d86f4bdd8a8d5504aa` |
| Narrow result | `ce3d4c45c9bbbb6ebcc43a7e17760c2025662a957aad6c648ed3b6f90e501bf6` |
| Original-chunk result | `d8c2285dd755cf6cc7c3a892e89584b917ba27e83275bdb8736dc04a2be8a8ea` |

Continue toward the frozen slower-turnover ETF study. Preserve strict minute
completeness until a separately tested missing-data execution contract exists.
