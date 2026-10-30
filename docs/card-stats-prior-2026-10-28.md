# Card-stats frozen replay prior (2026-10-28)

The shrinkage prior for `card_stats` v2 (`net_excess_r_v1`): per (strategy, timeframe, direction) key, the
mean and spread of the replayed `A0_live` setups in the net-excess unit. It is estimated once, first committed as
`config/research/card-stats-prior-v1.json`; v2 (`card-stats-prior-v2.json`, below) is what
`card_policy.prior_sha256` pins now. It is pinned by `card_policy.prior_sha256` and loaded with
`load_prior` (digest, model constraints, version, unit and constant checks; any failure is `PriorUnavailable`
with one fixed message). **It never includes a
live row**: live cards only move the posterior `(n*xbar + k*mean)/(n + k)`, `k = sd^2/(tau^2 + se_session^2)`.

## Provenance

- Frame: the 2026-10-18 bracket-counterfactual primary window, `frames/primary.csv.gz` (sha256
  `15a1225cd26fab78e7a6209e96fe6999c171fce7a2d74f5047c734204cbfd469`), protocol sha256 `fc6496aa6f9cbec182e0096aef5b7226290e02c367d2dcd084acd6371e75d725`; arm
  `A0_live`, feed `alpaca:sip`, decisions 2021-06-01 to 2026-01-29.
- Excluded symbols (25): BIL, SHY and `scan.broad_index_etfs`.
- Unit: `r` (uncosted) less the per-name cost `c` (floor 5 bp plus square-root impact from the 20 sessions
  before the decision, cap 200 bp), clipped at +/-10, less the trailing 120-session beta times `market_r`,
  clipped again. Daily bars are the study's adjusted SIP caches; beta and cost inputs use only sessions
  before the decision date.
- Regenerate: `uv run python scripts/card_stats_prior.py STUDY_DIR CACHE OUT_JSON` (offline; prints the
  digest; the recorded revision is the script's own checkout's, whatever the working directory). The
  estimator refuses (`ValueError`) a frame with no finite row or with fewer than two pooled quarters of 20 or
  more rows, rather than writing a prior whose pooled tau is only the floor. Code revision recorded in the
  file: `research/prospective-equity-panel-iex-v1-20260918-69-g72438f9`.
- Tau is the method-of-moments between-quarter standard deviation (quarters with at least 20 rows, floor
  0.05); a key with fewer than 8 such quarters takes the pooled tau (0.1643). Pooled sd
  1.395 over 8,250 rows. A key the replay never saw gets a skeptical entry:
  mean 0, pooled sd and tau.

## Keys

| strategy | timeframe | direction | n_rows | n_sessions | mean (R) | sd | se_session | tau | k |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SQUEEZE_BREAKOUT | 1h | LONG | 1,087 | 592 | -0.0210 | 1.404 | 0.0488 | 0.0985 (key) | 163.2 |
| SQUEEZE_BREAKOUT | 4h | LONG | 114 | 93 | +0.1315 | 1.391 | 0.1382 | 0.1643 (pooled) | 42.0 |
| TREND_PULLBACK | 4h | LONG | 7,049 | 1,056 | -0.0784 | 1.393 | 0.0251 | 0.1732 (key) | 63.4 |

## Cost and coverage

Per-side cost over all rows: p50 6.19 bp, p90 11.68 bp, p99 21.26 bp, max
136.44 bp. Rows without beta (`n_no_beta`): 0.

## v2 (2026-10-30)

v1 used each key's raw replay mean as the prior mean. With three keys of very different sample sizes the
smallest (SQUEEZE_BREAKOUT 4h, 114 setups, +0.13R) carried the largest unshrunk prior mean. v2 keeps every
v1 moment and changes only the mean: an empirical-Bayes shrink of each key's `replay_mean` toward the
cross-key mean. With `A = between_key_tau²` (the pooled between-quarter tau, 0.1643):

- `w_k = 1/(A + se_session_k²)` and `μ = Σ w_k·replay_mean_k / Σ w_k` (`cross_key_mean`);
- `B_k = A/(A + se_session_k²)` (`shrinkage`) and `mean_k = μ + B_k·(replay_mean_k − μ)`.

`sd`, `se_session`, `tau`, `n_rows`, `n_sessions` and `tau_source` are copied, so the weight `k` and the posterior se
are unchanged. The posterior mean is not: it moves because the prior mean did, and so do `measured_ev`
and `would_withhold`. For SQUEEZE_BREAKOUT 4h the posterior goes negative at a live mean below -0.155R at
n=20 (v1: -0.276R) and below -0.062R at n=50 (v1: -0.110R), so `enforce` withholds that key sooner. The
other two keys shift by about 0.001R at n=20. A key the replay never saw keeps the skeptical zero-mean entry. `load_prior` refuses a file
whose `mean` does not reproduce from `replay_mean`, `cross_key_mean` and `shrinkage` (`prior shrinkage
inconsistent`).

**Sensitivity to `A`.** SQUEEZE_BREAKOUT 4h's positive prior mean depends on the choice of `A`: its mean
crosses zero at tau = 0.069. The DerSimonian-Laird estimate of tau from the three keys is 0.038, which
gives -0.034R, and `A` is 18.5 times that estimate squared. The data cannot settle it: Q = 3.09 on 2 df,
and the 90% Q-profile interval for tau is 0 to 0.47. This is the key still sending under `enforce`.

The card prints `(shrunk from +0.13R)` when the two means differ by 0.005R or more.

Regenerate (reads v1 as digest-checked plain JSON; no frame, cache or network):

```
uv run python scripts/card_stats_prior.py shrink config/research/card-stats-prior-v1.json \
  6bab4e8d76a53ca099f9e5c82cd249e6cee2b9d26c4fc7e1613bd1e8469e5432 config/research/card-stats-prior-v2.json
```

v2 SHA-256 (`card_policy.prior_sha256`): `39056a50f30caa1bbf44d5beecb271eb7a381ac21d4e97b1907ca256b086d713`.
Cross-key mean `μ = -0.0076R`.

| Key | `replay_mean` | `shrinkage` | `mean` |
| --- | --- | --- | --- |
| SQUEEZE_BREAKOUT 1h LONG | -0.0210 | 0.919 | -0.0199 |
| SQUEEZE_BREAKOUT 4h LONG | +0.1315 | 0.586 | +0.0739 |
| TREND_PULLBACK 4h LONG | -0.0784 | 0.977 | -0.0768 |
