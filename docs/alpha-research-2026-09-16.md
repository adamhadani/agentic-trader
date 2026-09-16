# Alpha campaign evidence — September 16, 2026

This is redacted research evidence, not broker performance or promotion permission.
Source revision: `527df80`. Private protocols, data, complete trial exports and
checksums are retained in the operator's research artifact directory under
`campaign-20260916-walkforward`; they must remain outside Git.

## Frozen campaign

- Alpaca SIP/raw, daily, five-year lookback, current ETF32 membership.
- Random and genetic methods: 50 generated plus seven catalog trials per symbol
  and method, seed 20260917; **3,648 attempted / 3,646 evaluated**, two behavioral
  duplicates rejected, 3,422 distinct versions. All 64 runs completed.
- Three purged expanding validation folds in the first 80%; final 20% not evaluated.
  Five symbols had excluded terminal periods from earlier reviews; 27 retain
  unexamined terminal holdouts. No qualification or registry mutation was attempted.
- **Zero discovery finalists.** Maximum within-run DSR 0.860 and family DSR 0.783,
  below 0.95. Selected validation maxima are not fresh out-of-sample evidence.
- A separate predeclared 1,368-trial diagnostic used four symbols, two search methods
  and three prefix-only search/replay folds (24 folds). It used previously examined
  discovery history, never the final holdout. Lifetime recorded attempts became 7,049.

## Nested walk-forward diagnostic

The best inner-validation candidate was frozen before each following outer block;
these challengers were unqualified and never deployed. Replay spans approximately
November 2023–September 2025. Default costs are 5 bps per side, doubled for stress.
Daily OHLC execution omits some liquidity, borrowing, corporate-action and live
intrabar effects. Positions reset/are censored at fold boundaries.

| Symbol | Search | Outer SR | SR with doubled cost | Completed trades |
| --- | --- | ---: | ---: | ---: |
| SPY | random | -0.91 | -1.01 | 20 |
| SPY | genetic | 0.26 | 0.21 | 13 |
| QQQ | random | 0.23 | 0.15 | 21 |
| QQQ | genetic | 0.28 | 0.21 | 17 |
| GLD | random | 2.00 | 1.98 | 4 |
| GLD | genetic | 1.68 | 1.65 | 6 |
| TLT | random | -0.12 | -0.18 | 10 |
| TLT | genetic | -0.44 | -0.49 | 10 |

Gold's stronger returns have few completed trades and include marked/censored
exposure. This diagnostic does not identify a qualified winner.

## Correctness and controls

214 targeted regression tests passed. On one selected diagnostic candidate per ETF,
128 prefix, 128 future-perturbation and 128 entry-direction checks passed. All 32
replayed at 1×/2×/3× costs; selected validation profitability did not override gates.
The live service retained all 14 readiness checks and broker/report parity during
research. Research sent no orders or Telegram messages; no source change/restart.

Exploratory pure synthetic controls, five seeds each, used the real scorer/simulator
and qualification computation with the existing family penalty:

- Dense deliberately strong causal pulse: 5/5 discovery and qualification passes.
- Sparse profitable pulse: 5/5 discovery passes, 5/5 cumulative DSR rejections.
- Zero-edge controls: 15/15 rejections across three constructions.
- An initial persistent-regime predictor did not create a profitable strategy under
  rolling normalization and the fixed bracket policy. Forecast and strategy differ.

All controls, including failed constructions, were retained privately and never
recorded as market/shadow evidence. These small exploratory samples are not calibrated
error rates. They motivate A1 in the [active roadmap](alpha-roadmap.md), not a reduction
of thresholds to admit this batch. There remain zero active mined alphas; the two
built-in strategies are unaffected. This last statement is dated deployment evidence,
not a permanent claim about registry state.
