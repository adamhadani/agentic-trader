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
| Runs | check A 10:47→12:08 ET, check C 12:08→12:15 ET, six workers, outside the 10:35/14:35 scan windows |
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

## Campaign

The campaign (`alpha pooled campaign … --journal-scope production/alpaca:paper`) runs once per
protocol and only with the operator's explicit approval. It charges each formula to the pooled
ledger before scoring it and consumes the lane-wide confirmation window only when a formula
freezes. `no_finalists` closes the intraday fixed set on this window; a finalist would only make
the formula eligible for a separately specified contract, since live scans have no hourly feed
parity yet. Its result is recorded here when it runs.
