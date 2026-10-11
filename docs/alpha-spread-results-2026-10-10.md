# Spread-reversion lane (pairs v1): checks A and C, and the study (2026-10-10)

Protocol `config/research/spread/pairs-v1.json` (SHA-256 `88dc4945…aa352`) on cohort
`cohort-v1.json` (SHA-256 `6f3a4a32…dbea`), run from the installed checkout at the clean merged
revision `6d2acd8` (PR #113) on Saturday 2026-10-10, outside every scan window. Contract and
definitions: [spread lane](alpha-spread-lane.md). Everything below carries
`authorizes_promotion: false`.

## Check A — statistical power (passed, 10 of 10 seeds, about 2 minutes)

Synthetic factor worlds with 30 planted Ornstein–Uhlenbeck pairs (half-life 10 sessions,
innovation 0.008) on the weekday calendar, 12 discovery tiles each. Every seed completed and every
seed held all four rules. Planted recall per seed 0.53–0.62; lane mean 1.5–1.9 bp per day at the
5 bp decision cost; 609–901 closed trades per seed. The protocol detects what it is designed to
detect.

## Check C — null on shifted real bars (passed, 0 of 10 seeds, about 2 minutes)

Real cached bars truncated to discovery, each symbol's log returns and overnight gaps circularly
shifted by its own multiple of 63 sessions, offsets distinct within a sector (zero forced collisions
in all ten seeds). Every seed completed; 1,414–1,676 closed trades per seed; lane means −0.3 to
−1.1 bp per day; S1, S2 and S3 failed in every seed, S4 held. The protocol does not pass on
alignment-destroyed data.

## The study — discovery 2018-01-02 → 2023-12-29 (failed_discovery)

Eleven non-overlapping tiles (504 formation / 126 trading sessions). Formation found 108–114
eligible names, 676–740 same-sector pairs tested, 27–74 passing per tile, and the top-20 cap
bound in every tile (median half-life 9–16 sessions, median |β| 0.4–1.0; financial services the
largest sector in nine tiles). Trading closed 2,309 trades over 1,386 lane sessions.

| Rule | Value | Holds |
| --- | --- | --- |
| S1 lane mean (5 bp) and bootstrap CI90 | −0.30 bp/day, CI90 [−2.16, +1.36], one-sided p 0.62 | no |
| S2 trade mean / 1%-trimmed mean (net) | −3.6 bp / −5.5 bp | no |
| S3 counts | 2,309 trades, 11 of 11 windows traded, 64% positive windows | yes |
| S4 SPY beta | 0.161 (HAC(5) se 0.044) | yes |

Descriptives: hit rate 46%, annualised lane Sharpe −0.11 at 5 bp (+0.20 at 0 bp, −0.43 at 10 bp),
mean holding 4.9 sessions, median 1. Exits: 2,084 stops, 117 reversions, 108 window-end closes.
Per-window lane means ranged from −6.6 bp/day (window 4, formation 2018–2019, trading H1 2020)
to +2.9 bp/day. SPY beta is below the 0.2 bound but more than three standard errors above zero,
so the lane as traded was not market-neutral.

Decision as pre-registered: **failed_discovery**. The confirmation window (2024-01-02 →
2026-07-31) was not journaled, read or consumed; `confirmation_record` is null. Nothing promotes.

## What this says

- On this cohort, cost and window, a frozen Engle-Granger spread with z-entry 2 / exit 0.5 /
  stop 4 did not earn its 10 bp round trip. The point estimate at zero cost is slightly positive
  and at 5 bp slightly negative; neither is distinguishable from zero.
- The protocol's machinery worked end to end: both gates bound to the same protocol, cohort and
  revision; the study fetched every bar without failure (`bar_failures` empty, digests recorded);
  the one-use window stayed untouched because discovery failed.
- **A construction defect in protocol v1 dominates the trade count.** The entry rule fires when
  |z| ≥ 2 with no upper band, and formation σ is estimated in-sample on pairs selected for low
  residual variance, so the frozen spread is routinely far outside its formation band by the time
  a tile starts trading. 1,932 of 2,309 entries (84%) occurred at |z| ≥ 4, already beyond the
  stop; 1,861 of them were stopped on the next mark with mean gross return −1.4 bp, each paying
  the full round trip. The median entry |z| over all trades was 5.4.

## Post hoc read of the in-band trades (descriptive only; not a result)

This split was made after reading discovery and cannot pass or fail anything. Among the 377
trades entered in the band 2 ≤ |z| < 4: mean gross +17.5 bp, mean net +7.5 bp at the 10 bp round
trip, hit rate 44%, mean holding 22.8 sessions; 111 reversions averaged +6.4%, 197 stops −3.7%,
69 window-end closes +1.2%; 7 of 11 windows positive, window 4 (−2.4% per trade) the worst.
Dispersion is large relative to the mean, and a protocol with an entry band would also change
selection, capital use and the bootstrap, so this number is a reason to predeclare a v2, not
evidence that v2 would pass.

## Decision

The pairs v1 protocol is **closed on the discovery window**. The confirmation window is still
unused and remains available to a successor protocol under a new frozen version, its own checks
A and C, and operator approval. If the desk wants a v2, the one change this evidence motivates is
an entry band (`z_entry ≤ |z| < z_stop` at the signal close) with σ estimated on a holdout slice
of formation rather than the fitting slice; everything else should stay as in v1 so the comparison
is clean. Reading discovery a second time with a changed protocol is a second look at the same
data and must be declared as such in the v2 spec.

Artifacts: `~/agentic-trader-research/spread-power-v1-20261010`, `spread-null-v1-20261010`,
`spread-study-v1-20261010` (`result.json`, `manifest.json`, `protocol.json`,
`discovery-trades.csv.gz`, `discovery-lane.csv.gz`). SDD ledger archived under
`~/agentic-trader-research/spread-lane-sdd-20261010/`.
