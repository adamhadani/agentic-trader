# Card-stats frozen replay prior (2026-10-28)

The shrinkage prior for `card_stats` v2 (`net_excess_r_v1`): per (strategy, timeframe, direction) key, the
mean and spread of the replayed `A0_live` setups in the net-excess unit. It is estimated once, committed as
`config/research/card-stats-prior-v1.json`, pinned by `card_policy.prior_sha256` and loaded with
`load_prior` (digest, unit and constant checks; any failure is `PriorUnavailable`). **It never includes a
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
  digest). Code revision recorded in the file: `research/prospective-equity-panel-iex-v1-20260918-68-g310b8d1-dirty`.
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
