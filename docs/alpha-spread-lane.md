# Spread-reversion lane (pairs v1)

Design: [spec](superpowers/specs/2026-10-10-spread-reversion-lane-design.md). Frozen files:
`config/research/spread/pairs-v1.json` (protocol) and `config/research/spread/cohort-v1.json`.
The protocol file is authoritative for every number below.

## 1. Purpose and status

Desk-direction item 2 ([roadmap](alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8)).
Every earlier lane tested a directional label; mean reversion of a cointegrated same-sector spread is
market-neutral by construction. The lane is research only: every result carries
`authorizes_promotion: false`. No probe, registry entry, card, order or two-leg execution contract
exists (a probe would need atomic legs, shared protection and borrow checks), and `/pairs` stays
display-only. A `confirmed` result only makes a capped probe specifiable.

**Result (2026-10-10):** checks A and C passed and the v1 study ended in `failed_discovery`; the
confirmation window is unused. See [the results](alpha-spread-results-2026-10-10.md), which also
record the v1 construction defect (entries with no upper z band against an in-sample σ) that a
successor protocol would fix.

## 2. Cohort

127 equities in 11 sectors, taken from the configured scan universe intersected with the pooled
cohort v2, with the config's sector tags; 927 same-sector candidate pairs. Cross-sector pairs are never
tested. Membership is today's (survivorship, not point-in-time): survivors are less likely to have
diverged for good, which flatters reversion; the 2024-2026 confirmation window is least exposed. The
cohort file's SHA-256 is pinned by the protocol (`cohort_sha256`) and recorded in every manifest; a
changed file is a new version.

## 3. Protocol

| field | value |
| --- | --- |
| feed / adjustment | `alpaca:sip` / `all`; bars 2016-01-04 through 2026-08-31 |
| discovery trading windows | 2018-01-02 to 2023-12-29 |
| confirmation trading windows | 2024-01-02 to 2026-07-31 (one use, journaled) |
| schedule | formation 504 sessions, trading 126 sessions, non-overlapping tiles, whole windows only |
| formation | Engle-Granger `coint` (trend `c`, maxlag 1, no autolag), p < 0.05, half-life 5-42 sessions, abs(beta) in [0.25, 4], top 20 by t-statistic, at least 60 eligible names |
| trading | z entry 2.0, exit 0.5, stop 4.0, fills at the next open |
| costs | 0 / 5 / 10 bp per side; 5 bp decides |
| bootstrap | stationary, block mean 10, 2000 draws, seed 20261010 |
| discovery pass rules | 100 closed trades, 8 windows, 60% of windows positive, abs(beta to SPY) at most 0.2, 1% trim |
| confirmation rules | 30 closed trades, 4 windows, otherwise identical |

Expect about 11 discovery and 5 confirmation windows. The confirmation tiling restarts at 2024-01-02
and its formation windows reach into 2023, which is estimation on the past and allowed. Fewer than 60
eligible names in any window fails the stage closed (`coverage_failed`).

## 4. Causality and accounting

Everything estimated (alpha, beta, residual std `sigma_f`) comes from the 504 formation sessions and
is frozen for the next 126. The regression direction is fixed (Y is the alphabetically earlier symbol),
never chosen by fit. Eligibility uses only past bars. Signals are read at the close and filled at the
next session's open; no entry on the window's last session; an open pair is closed at the last
session's close (`window_end`). A session where either leg lacks a bar is skipped for signals and the
open position is marked at the last available closes; a pair missing the last session's bar closes at
its last available close. Dollar weights at entry are `1/(1+abs(beta))` and `abs(beta)/(1+abs(beta))`
(gross 1) with fixed shares thereafter. Net trade return is gross minus `2c/10^4` for `c` bp per side
(two legs in, two out). The lane series is committed-capital (Gatev): each window gives 1/20 of capital
to each selected pair, flat or unfilled slots earn 0, and windows are concatenated (disjoint).

## 5. Pass rules and decisions

- S1: lane mean daily return > 0 with the bootstrap 90% CI lower bound > 0.
- S2: closed-trade mean and 1%-trimmed mean > 0.
- S3: closed-trade count, window count and positive-window fraction at the stage's thresholds; the
  fraction is over windows that had at least one closed trade.
- S4: abs(OLS beta of the lane return on SPY) at most 0.2.

Decisions: `failed_discovery` (any of S1-S4 fails; the confirmation window is neither consumed nor
read); `failed_confirmation` (discovery passed, the confirmation tiling failed the relaxed rule);
`confirmed` (both passed). None permits anything beyond specifying a capped probe.

## 6. Checks A and C

`spread-study` refuses without both, whose protocol hash, cohort hash and runtime revision match its
own, and refuses a dirty or unknown revision.

- **A, power** (`spread-power`): ten synthetic panels shaped like the cohort with 30 planted
  same-sector cointegrated pairs (OU residual, planted half-life 10, innovation std 0.8%). The full
  discovery pipeline runs; pass when discovery passes in at least 8 of 10 seeds. No provider access.
- **C, null** (`spread-null`): ten seeds; each symbol's returns and gaps are circularly shifted by its
  own multiple of 63 sessions, distinct within a sector, then rebuilt into prices. Pass when the
  pipeline's discovery passes in at most 1 of 10: the whole-pipeline false-acceptance rate, selection
  included. A seed that cannot complete fails the check.

## 7. Journal

The study calls `AlphaRepository.consume_lane_confirmation("spread", ...)` once, after discovery
passes and before reading any confirmation session. It records `spread/confirmation` under the alpha
lock and refuses any overlapping interval already consumed for the lane (lane-wide, never `family/all`;
the protocol is one frozen hypothesis, so no trial is charged). The journal scope must equal
`--journal-scope` (checked before any bar read, PostgreSQL). The consumption record survives later
failures. Output directories are new and private; the manifest is written before any I/O.

## 8. Running it

After merge and a controlled restart, from the installed checkout, outside the 10:35 and 14:35 New York
scan windows:

```bash
copilot alpha spread-power config/research/spread/pairs-v1.json --output ~/agentic-trader-research/spread-power-v1-YYYYMMDD
copilot alpha spread-null  config/research/spread/pairs-v1.json --cache ~/agentic-trader-research/pooled-cache-v1/bars --output ~/agentic-trader-research/spread-null-v1-YYYYMMDD
copilot alpha spread-study config/research/spread/pairs-v1.json --power DIR --null-check DIR --cache ~/agentic-trader-research/pooled-cache-v1/bars --journal-scope SCOPE --output ~/agentic-trader-research/spread-study-v1-YYYYMMDD
```

Output naming is `spread-{power,null,study}-v1-YYYYMMDD`. Estimated runtime (spec estimate, not yet measured): a stage in minutes, each
check under an hour. The pooled cache already holds the 127 names and SPY.

## 9. Known limits

Survivorship (section 2); today's adjustment factors applied to history; no borrow cost or short
availability; next-open fills with a flat 5 bp per side and no impact; daily bars only; beta frozen
for six months; multiple comparisons inside formation are controlled only by the out-of-sample
trading window and the top-20 cap; dependence across simultaneously open pairs is handled by the
lane-series block bootstrap, not trade-level tests; the market-neutrality gate uses one factor (SPY).
A signal on the penultimate session still fills at the last session's open and closes as `window_end`
the same day. Ticker reuse (a ticker's history may belong to an earlier listing, e.g. constant
zero-volume padding before a 2023 listing) is handled only by treating zero-volume rows as missing
bars; there is no listing-date check.

Detection band. A 252-session formation window was tried first and gave the Engle-Granger plus
half-life filter almost no detection band: planted half-life 20 went undetected, and half-life 6 was
detected but its AR(1) half-life estimate fell under the floor of 5. At 504 sessions the test reliably
detects half-lives up to about 12 and slower reversion rarely, so check A plants half-life 10, an edge
the protocol can detect; slower real spreads are largely invisible to this protocol. The AR(1)
half-life estimate is noisy near the floor. Check C's shifts: each name draws its own offset from its own
history, distinct from the offsets already taken in its sector; a forced collision fails the check and
is recorded (`offset_collisions`). Same-sector pairs share names, so formation-stage false
positives are dependent and heavy-tailed; the whole-pipeline false-acceptance rate is what check C
measures.

## 10. Relation to the display module

`agentic_trader/pairs` (`/pairs`, `copilot pairs`) fits and scores in-sample, uses the plain ADF
p-value on an estimated residual (liberal) and reads whichever provider answers first. It is unsuitable
as evidence. The lane uses `coint` with MacKinnon p-values, formation-only estimation and frozen
parameters, and reads one declared SIP cache.
