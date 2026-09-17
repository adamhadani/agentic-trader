# Continuous timed ETF results — September 17, 2026

**48 attempts retained: 36 completed, 12 coverage failures, zero complete triage
passes.** Active/qualified alphas remain zero. This experiment improves failure
attribution: the QQQ momentum family has hundreds of completed trades and positive
low-cost returns, but its turnover erases that edge at the frozen stress cost.
SPY's missing second year is unavailable evidence, not an economic rejection.

The [protocol](alpha-continuous-campaign.md), implementation and tests were committed
as `a748e99` before provider access. Four hypotheses × two ETFs × two annual windows
× three per-side costs consumed exactly 48 attempts; lifetime attempts increased
from 7,232 to **7,280**. All comparisons within each symbol/year share one frozen
snapshot. No reruns, threshold changes, new registrations or promotions followed.

## What the completed comparisons show

Returns compound separate 2022 and 2023 annual simulations, each starting flat.
They are development-history model returns, not untouched walk-forward validation,
a continuous two-year account, real fills, or paper-account profits. Costs apply on
both fill sides; cash/warmup minutes count and final inventory is marked, not closed.

| QQQ hypothesis | Zero cost | 1 bp/side | 5 bp/side | Closed trades at 1 bp | Frozen rejection |
| --- | ---: | ---: | ---: | ---: | --- |
| Hour momentum | +37.18% | +26.24% | −9.50% | 415 | Stress cost |
| Bar reversal | +0.95% | −11.97% | −49.10% | 684 | Annual stability, net/excess, stress, concentration |
| Volume momentum | +22.51% | +13.31% | −17.10% | 390 | Stress cost |
| Trend/pullback (new) | +30.49% | +20.12% | −13.78% | 414 | Stress cost |

All three momentum variants pass coverage, the 100-trade minimum, positive primary
returns in both years, aggregate benchmark excess and the concentration screen.
QQQ's annual primary returns are:

| Hypothesis | 2022 | 2023 | Largest positive day's share of signed net log gain |
| --- | ---: | ---: | ---: |
| Hour momentum | +3.99% | +21.39% | 21.93% |
| Volume momentum | +5.00% | +7.92% | 40.89% |
| Trend/pullback | +14.59% | +4.83% | 23.89% |

The passive-long price comparator compounds to +1.70%, but is −33.27% in 2022 and
+52.40% in 2023. Aggregate excess does not mean beating passive long every year.
Dividends, borrow/funding, liquidity/partial fills and production timing are omitted;
the gross/primary results do not establish an exploitable net edge. Five basis points
is a declared stress scenario, not a measured estimate of our Alpaca execution cost.
It must not be lowered after seeing this failure; future realistic cost calibration
requires independent quote/fill evidence and a new prospective protocol.

The timed policy generated 415 QQQ momentum closes versus 11 in the earlier short-
block study, but windows and lifetime both changed: this is not a controlled estimate
of the lifetime change's effect. Median completed holding duration is 24 elapsed hours
in each year; 266 of 415 exits use the holding deadline. Eighty-six resting orders
expire. More turnover creates more cost: an additional 4 bp on each side across
hundreds of round trips materially compounds. Counts are not independent bets.

QQQ momentum/volume daily log returns correlate 0.797; momentum/pullback 0.484.
SPY/QQQ momentum correlate 0.700 over their shared 2022 sample. These descriptive
correlations are evidence against treating formula variants as unrelated risk.

## SPY coverage failure

All twelve 2023 comparisons fail before simulation because the frozen observations
lack **June 5, 2023, 13:52–13:55 UTC** (09:52–09:55 New York). The calendar expects
97,140 minutes; 97,136 are present. These gaps are inside an acquisition chunk,
not its boundaries. Arrays, observed calendar, exact missing times and receipts are
retained. No prices were imputed and no alternative snapshot was selected.

The retained artifact is after provider normalization. Because the existing provider
uses `dropna()`, this evidence cannot conclusively separate absent supplier bars from
rejected/null raw values. A lossless raw-response/normalization audit is a targeted
follow-up; do not claim a confirmed Alpaca outage or silently repair these results.

SPY's complete 2022 primary returns were −3.12% momentum, −11.29% reversal,
+4.97% volume momentum and +15.13% trend/pullback. All four were negative at 5 bp.
No two-year SPY return or qualified winner can be calculated from that one year.

## Independent verification and provenance

A separate accounting audit reconstructed **every minute's equity for all 36
completed runs** from entry/exit event prices, signed inventory, cash and fees.
Maximum discrepancy from saved compounded returns was **5.06e−13** in unit equity.
A second calculation compounded independently recomputed closed-trade returns and
remaining marked inventory. Both agreed with campaign summaries. It also checked:

- All 48 declared unique plans/results and saved manifest/observation/dataset hashes.
- Identical data/calendar hashes across all 12 comparisons in each of four cohorts.
- Identical entry/exit paths at all three costs for each completed hypothesis/year.
- Complete regular-session clocks, exact missing-minute failures and 12 disjoint
  acquisition chunks per cohort, including request/receipt ordering and row counts.
- Entry fills respect their limit and precede the 300-second deadline; expirations
  respect the declared entry/holding deadlines; orders follow completed signal bars.
- All closed-trade counts and terminal inventory reconcile; no forced end liquidation.

These checks found no arithmetic, fee or chunk-index discrepancy in the inspected
path. They do not prove realistic fills, independent observations or general absence
of bugs. No statistical significance or IC qualification is asserted by this campaign.

Private evidence: `~/.local/state/agentic-trader/research/etf-continuous-timed-v1-20260917`.
Raw arrays, provider receipts, audit scripts and traces stay outside Git. SHA-256:

| Artifact | Digest |
| --- | --- |
| Frozen protocol | `907a3d791e59ca2409991b87bf3f6cdde8f0d7d5e6b44689d6862a5888dd0a94` |
| Campaign manifest | `b87cbaed87b70226cc613e4227ff15f3ee43ce6cf6aaca2f0a935f99de1e0bbe` |
| Campaign result | `1f6c3071856d170f2af53063072ad95907b65224f551c6e483381b17496bca9f` |
| Independent audit | `dc7f11d0507060e38754d522b2779113ddc649950bbdc741e94487fb38a13553` |

## Decision and next experiment

Do not promote these candidates or tune their duration/cost gates on these years.
Continue the fixed forward controls; no new enrollment is authorized by this protocol.
Prioritize a causal ETF panel and relative/residual hypotheses with explicit forecast
horizons, ranks across assets and turnover/cost analysis. The [IC contract review](alpha-information-coefficient.md)
defines the measurement prerequisites. Start with a bounded predeclared comparison,
not a large search of variants on the same observations. Investigate raw coverage
provenance alongside it; retain research checkpoints/capacity as architecture work.

## Validation versus deployment

Source validation: TDD acquisition expectations first failed in six cases, then
passed; full suite **1,318 passed, 50 skipped**; actual TCP/WebSocket and disposable
PostgreSQL integration suite **136 passed**. All-file pre-commit checks passed.
SDK tests cover pending/held state across chunks and DST, half-open endpoint bounds,
failed acquisition receipts and unchanged trial accounting. Live research made only
market-data/calendar reads and intentional research journal writes; no bot messages
or broker orders. Deployed verification is recorded separately on the delivery PR.
