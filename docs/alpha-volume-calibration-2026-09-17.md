# SIP/IEX volume calibration — September 17, 2026

## Result

Both frozen protocols completed: **80/80 comparisons**, 40 separately identified
profiles (ten symbols × two chronological folds × two feeds), and complete
**864/864 daily bars per symbol/feed**. Independent reconstruction agrees with every
threshold, forward ratio, percentile, event flag and event-rate summary; maximum
threshold error is **0.0**. Source artifact and profile hashes also agree.

Trial accounting: **7,557 → 7,637**. Each 40-comparison reservation and all ten member
exclusions precede acquisition. Registry remains **generation 10, 0 active / 10 shadow**.
No strategies were promoted or live thresholds/feed settings changed.

## Frozen protocol and interpretation

See the [contract and predeclared design](alpha-volume-calibration.md). Both protocols
were committed at `ecbffb8244ae2747d02e541eb0965f9889354421` before provider access.
Train 2021/evaluate 2022 and train 2022/evaluate 2023; preceding 20-bar median,
90th-percentile threshold, fixed 1.5 reference. Adjusted native daily SIP/IEX,
SPY plus nine sector ETFs. Previously inspected development history; no untouched
qualification or actual live-forward claim. No return labels or P&L were evaluated.

## Out-of-training event rates

The nominal training upper tail is 10%; ties/interpolation give approximately that
fraction. Forward frequencies describe distribution transfer, not an IID test or
a guarantee that future events will occur at the target rate. Rates below pool ten
symbols within a fold; names and the two feeds are dependent.

| Feed | Evaluation year | Training threshold range | Calibrated event rate | Fixed 1.5 event rate | Per-symbol calibrated range |
| --- | --- | --- | --- | --- | --- |
| SIP | 2022 | 1.539–2.030 | 6.06% | 12.07% | 2.39%–11.16% |
| SIP | 2023 | 1.445–1.693 | 6.92% | 8.00% | 4.00%–11.60% |
| IEX | 2022 | 1.610–1.961 | 6.45% | 14.34% | 4.78%–8.37% |
| IEX | 2023 | 1.412–1.851 | 10.16% | 13.64% | 4.80%–18.00% |

## Per-symbol evidence

| Symbol | Year | SIP threshold | IEX threshold | SIP event rate | IEX event rate | Relative-volume Spearman correlation |
| --- | --- | --- | --- | --- | --- | --- |
| SPY | 2022 | 1.816 | 1.770 | 2.39% | 5.58% | 0.821 |
| SPY | 2023 | 1.525 | 1.588 | 4.80% | 4.80% | 0.739 |
| XLB | 2022 | 1.675 | 1.761 | 11.16% | 4.78% | 0.743 |
| XLB | 2023 | 1.693 | 1.598 | 8.80% | 14.00% | 0.685 |
| XLE | 2022 | 1.564 | 1.729 | 8.76% | 7.97% | 0.787 |
| XLE | 2023 | 1.518 | 1.610 | 6.40% | 6.80% | 0.785 |
| XLF | 2022 | 1.707 | 1.879 | 4.38% | 5.98% | 0.785 |
| XLF | 2023 | 1.531 | 1.586 | 8.40% | 13.20% | 0.779 |
| XLI | 2022 | 1.717 | 1.677 | 5.58% | 8.37% | 0.730 |
| XLI | 2023 | 1.549 | 1.573 | 6.40% | 11.20% | 0.795 |
| XLK | 2022 | 2.030 | 1.951 | 2.79% | 7.97% | 0.700 |
| XLK | 2023 | 1.562 | 1.851 | 6.40% | 6.40% | 0.682 |
| XLP | 2022 | 1.716 | 1.739 | 7.57% | 6.37% | 0.690 |
| XLP | 2023 | 1.637 | 1.639 | 4.00% | 8.80% | 0.633 |
| XLU | 2022 | 1.539 | 1.856 | 9.96% | 5.18% | 0.751 |
| XLU | 2023 | 1.538 | 1.646 | 8.40% | 11.60% | 0.775 |
| XLV | 2022 | 1.803 | 1.610 | 2.79% | 6.37% | 0.595 |
| XLV | 2023 | 1.445 | 1.412 | 11.60% | 18.00% | 0.611 |
| XLY | 2022 | 1.830 | 1.961 | 5.18% | 5.98% | 0.565 |
| XLY | 2023 | 1.578 | 1.773 | 4.00% | 6.80% | 0.523 |

Within-feed normalization does not make the feeds identical: paired relative-volume
rank correlations range **0.523–0.821**. Thresholds and later
event frequencies differ. A common SIP-derived constant cannot establish an IEX
profile; future distribution drift still needs monitoring. Profiles are usable
research transforms with explicit source/training identity, not validated alpha signals.

## Audit and retained evidence

Private source arrays, raw SDK receipts, full calibration distributions, date-level
outputs, independent audit and journal checks are retained under the private
`research/feed-volume-20260917` state directory. Every failed test/review reproduction
remains separate from the final successful evidence. The numeric auditor imports
NumPy/pandas but no application modules and rebuilds ratios/quantiles/midranks directly.

| Artifact | SHA-256 |
| --- | --- |
| SIP result | `9e018a494bf2955cffab0ed7660e57ba1be86d4557adca33d7c7442f8837cf5f` |
| IEX result | `d9e87fcf67d87a903216117e4fbd3d0b2328584de12c85e3ac339ded0302c5da` |
| Independent audit script | `c88bfd82df77d041a47318bdc2afd7e20d741b8cf9c8c652f0f6f404937e5e30` |

## Operational observation, separate from historical calibration

A read-only preview of the compact `/alphas` response was 700 characters on the
actual registry (zero Telegram messages sent). The subsequent forward report had
78 recorded evaluations, all unavailable. Each has retained, hash-verified raw
HTTP 403 evidence. This agrees with the separately confirmed recent-SIP entitlement
refusal; it does not reflect a failure of the historical IEX calibration run. Fresh
worker readiness must not be presented as successful prospective alpha scoring.

## Next work

The [ordered roadmap](alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities)
prioritizes successful separately bound prospective IEX acquisition alongside a
dated 200–500 individual-equity universe and bounded economically
distinct stock/ETF forecast comparisons, with point-in-time membership/delisting
coverage, simple purged baselines and costs. Prospective IEX timing, intraday volume
seasonality and exact calibration/plan execution identity remain open. ETF calibration
here isolates the source question; it does not limit the next discovery universe.

200 actual SDK/SQLite/PostgreSQL integration checks and 39 final affected tests passed
locally. Full CI/source verification and deployed runtime evidence are recorded
separately in [PR #58](https://github.com/adamhadani/agentic-trader/pull/58).
