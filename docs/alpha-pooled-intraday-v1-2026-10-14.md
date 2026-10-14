# Pooled intraday fixed set v1: checks A and C (2026-10-14)

Research only. Nothing here promotes, journals shadow credit, or touches the registry, broker or
Telegram. The lane and its contract are described in
[pooled mining](alpha-pooled-mining.md#intraday-score-inputs-protocol-pooled-intraday-campaign)
and the [design](superpowers/specs/2026-10-14-pooled-intraday-design.md).

## What was run

| Item | Value |
| --- | --- |
| Protocol | `config/research/pooled/intraday-v1.json`, SHA-256 `5b4c3fdf23aa1800…` (`pooled-intraday-campaign`, fixed set, `score_inputs: hourly`, `bars_before_decision: 1`) |
| Cohort | v2, `c2c7941014932075…` (452 names, 437 ever eligible) |
| Cube | spec identity `f1c835a3d3364d2b…`, cube SHA-256 `9bedbf9b4b8e7541…` — the cached cube campaign v4 was checked on (cache hit; no provider access) |
| Code revision | main `15e6939` (PR #120), clean detached worktree |
| Runs | check A 10:47→12:14 ET, check C 12:14→12:15 ET, six workers, outside the 10:35/14:35 scan windows |
| Artifacts | `~/agentic-trader-research/pooled-power-a-intraday-v1-20261014/`, `~/agentic-trader-research/pooled-null-check-intraday-v1-20261014/` (private) |

Check A is formula-independent (it plants synthetic edges on the cube), so on the same cube it must
replay campaign v4's curve. Check C scores the eight real intraday formulas on per-name demeaned
labels; a replicate that freezes a formula is a false acceptance. Both are pre-registered in the
design (§4): A and C only, because a fixed set has no search.

## Check A: power — passed

Identical to the v4 run on this cube, as required.

| Planted edge (R per pick) | Detection | False acceptance |
| --- | --- | --- |
| 0.00 | 0.00 | 0.00 |
| 0.05 | 0.40 | 0.00 |
| 0.08 | 0.84 | 0.00 |
| 0.10 | 0.95 | 0.00 |
| 0.12 | 0.98 | 0.00 |
| 0.15 (gate) | 0.99 | 0.00 |

100 replicates, 200 null formulas; eligible names per session 265–414 (mean 367).

## Check C: real-formula false acceptance — passed

| Statistic | Value |
| --- | --- |
| Replicates | 40 |
| False acceptances | 0 (limit 2) |
| False-acceptance rate, upper 95% | 0.072 |
| Replicates reaching confirmation | 1 (`none_confirmed`); 39 `no_finalists` |
| Seed-formula discovery t (320 formula-replicates) | mean +0.04, sd 1.17 |
| Evaluation errors | 0 (`errors_total`; the check fails closed on any) |

Every one of the eight intraday formulas evaluated on every replicate: the cached hourly bars
cover all 437 eligible names, and the read rule (the 09:00 New York bucket closing 10:00, the
cube's own decision-reference bar) produced a finite score wherever a bar existed.

## Campaign: no finalists

The operator approved the campaign and it ran at 13:00 ET the same day from the same clean
worktree (`alpha pooled campaign … --journal-scope production/alpaca:paper`; campaign id
`pooled-intraday-campaign-v1-5b4c3fdf23aa1800`, PostgreSQL journal scope `production/alpaca:paper`,
artifact `~/agentic-trader-research/pooled-campaign-intraday-v1-20261014/`). Each of the eight
formulas was charged to the pooled ledger before scoring (8 charged, 8 evaluated, 0 errors,
0 rejected). None passed the discovery gate, so nothing was carried, selected or frozen and the
lane-wide confirmation window is **still unused**.

Discovery window 2016-08-01 → 2021-12-31, top-decile picks per session, 5 bp decisive cost;
`edge` is the session-paired pick-minus-pool mean R, `t` its session-block statistic, `leg` the
picks' own mean R:

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| first_hour_momentum | `oc_spread` | 1,359 | −0.011 | −0.72 | +0.183 |
| overnight_persistence | `open_gap` | 1,358 | −0.035 | −1.93 | +0.158 |
| overnight_vs_intraday | `open_gap - oc_spread` | 1,357 | −0.030 | −1.91 | +0.162 |
| relative_volume | `volume / ts_mean(volume, 140)` | 1,344 | −0.012 | −0.79 | +0.183 |
| range_expansion | `hl_spread / ts_mean(hl_spread, 140)` | 1,344 | −0.001 | −0.06 | +0.195 |
| range_location | `(close - low) / (high - low)` | 1,358 | −0.004 | −0.40 | +0.189 |
| first_bucket_efficiency | `(close - open) / (high - low)` | 1,358 | +0.001 | +0.06 | +0.194 |
| intraday_high_proximity | `close / ts_max(high, 140)` | 1,343 | −0.013 | −0.59 | +0.186 |

Seven of eight edges are negative and all are within noise of zero (the gate needs t ≥ 3 with
three of four positive blocks); the two overnight-gap formulas are the most negative, i.e. the
names with the largest overnight gaps did slightly worse than the pool over the 20-session
bracket. The positive leg means are the long-bracket base rate on this cohort and window, not
alpha. Fifteen cohort names had no cached hourly bars (never eligible, so never picked).

**Conclusion.** The intraday fixed set is closed on this window: the first completed regular
bucket carries no cross-sectional edge for the long bracket that the eight predeclared formulas
could find. Reopening intraday mining needs a new frozen protocol (different read, e.g. more
buckets or a later decision, or a search protocol with check B) and operator approval; the
confirmation window remains available to the lane.
