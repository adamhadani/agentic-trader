# Pooled events fixed set v1: checks A and C, campaign (2026-10-14/15)

Research only. Nothing here promotes, journals shadow credit, or touches the registry, broker or
Telegram. The lane and its contract are described in
[pooled mining](alpha-pooled-mining.md#event-score-inputs-protocol-pooled-events-campaign) and
the [design](superpowers/specs/2026-10-14-pooled-events-design.md).

## What was run

| Item | Value |
| --- | --- |
| Protocol | `config/research/pooled/events-v1.json`, SHA-256 `4e156fd8dcb45064…` (`pooled-events-campaign`, fixed set, `score_inputs: daily` plus `events_fields_v1`) |
| Event inputs | Nasdaq calendar pages 2016-02-23 → 2026-07-31 (2,724 weekday pages, digest `9273e40a…`); cohort-v2 corporate actions 2016-01-01 → 2027-03-31 (13,037 actions, `content_sha256` `776d4df7…`); both verified before any bar was read |
| Cohort | v2, `c2c7941014932075…` (452 names, 437 ever eligible) |
| Cube | spec identity `f1c835a3d3364d2b…`, cube SHA-256 `9bedbf9b4b8e7541…` (cache hit; the cube v4 and intraday-v1 ran on) |
| Code revision | main `d6fd22e` (PR #121), clean detached worktree |
| Runs | check A 14:46→16:12 ET and check C 16:12→16:13 ET on 2026-10-14; campaign 05:02 ET on 2026-10-15 (manifest `started_at` 09:02:03 UTC, last artifact 09:02:17 UTC) (operator-approved); all outside scan windows |
| Artifacts | `~/agentic-trader-research/pooled-power-a-events-v1-20261014/`, `pooled-null-check-events-v1-20261014/`, `pooled-campaign-events-v1-20261015/` (private) |

Event coverage on the real data (recorded in every result): 437 eligible symbols, 2 with no
earnings report in the calendar, 78 with no corporate action (non-payers, yield 0.0), 29 identity
breaks (a `name_change` moving a ticker onto or off the symbol; fields NaN before the break), 8
reports dated on non-sessions skipped, 0 symbols without raw bars, 0 unpriced or unscaled
dividends. `dividend_yield` is first finite on 2017-01-03 (its 252-session warm-up); every other
formula from 2016-08-01.

## Check A: power — passed

Identical to the v4 and intraday runs on this cube, as the formula-independent check must be.

| Planted edge (R per pick) | Detection | False acceptance |
| --- | --- | --- |
| 0.00 | 0.00 | 0.00 |
| 0.05 | 0.40 | 0.00 |
| 0.08 | 0.84 | 0.00 |
| 0.10 | 0.95 | 0.00 |
| 0.12 | 0.98 | 0.00 |
| 0.15 (gate) | 0.99 | 0.00 |

## Check C: real-formula false acceptance — passed

| Statistic | Value |
| --- | --- |
| Replicates | 40 |
| False acceptances | 0 (limit 2) |
| False-acceptance rate, upper 95% | 0.072 |
| Replicate outcomes | 37 `no_finalists`, 3 `no_confirmation_candidates` (carried, none survived selection) |
| Seed-formula discovery t (320 formula-replicates) | mean +0.07, sd 1.23 |
| Evaluation errors | 0 |

## Campaign: no finalists

Campaign id `pooled-events-campaign-v1-4e156fd8dcb45064`, PostgreSQL journal scope
`production/alpaca:paper`. Eight formulas charged to the pooled ledger before scoring (8 charged,
8 evaluated, 0 errors, 0 rejected). None passed the discovery gate; nothing was carried,
selected or frozen; the lane-wide confirmation window is **still unused**.

Discovery window 2016-08-01 → 2021-12-31, top-decile picks per session among names with a finite
score (out-of-window cells are NaN for the windowed formulas), 5 bp decisive cost; `edge` is the
session-paired pick-minus-pool mean R, `t` its session-block statistic, `leg` the picks' own
mean R:

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| pead_surprise_decay | `earnings_surprise / (1 + earnings_age)` | 1,358 | −0.015 | −0.78 | +0.180 |
| pead_reaction_decay | `earnings_reaction / (1 + earnings_age)` | 1,359 | −0.003 | −0.15 | +0.188 |
| pead_reaction_window | `earnings_reaction / (earnings_age <= 20)` | 1,357 | +0.031 | +0.79 | +0.192 |
| pead_agreement | `sign(earnings_surprise) * earnings_reaction / (earnings_age <= 20)` | 1,357 | +0.011 | +0.32 | +0.172 |
| surprise_x_reaction | `earnings_surprise * earnings_reaction / (1 + earnings_age)` | 1,361 | −0.032 | −1.60 | +0.160 |
| quiet_momentum | `ts_sum(returns, 20) / (earnings_age > 20)` | 1,358 | −0.119 | −3.34 | +0.073 |
| quiet_reversal | `-ts_sum(returns, 5) / (earnings_age > 20)` | 1,357 | +0.011 | +0.37 | +0.207 |
| dividend_yield | `dividend_yield` | 1,248 | −0.049 | −1.17 | +0.158 |

Reading. The post-earnings-announcement-drift formulas carry no edge for the long bracket on this
cohort: the two reaction-window formulas are the only positive ones and both are well inside
noise. `quiet_momentum` is the one large statistic, and it is **negative**: outside the
announcement window, the names with the strongest 20-session momentum did about 0.12R worse
than the pool over the 20-session bracket (the gate needs t ≥ +3 with three of four positive
blocks; a negative t cannot pass a long-only lane). The sign-flipped formula
(`-ts_sum(returns, 20) / (earnings_age > 20)`, 20-session reversal outside announcements) was
not predeclared, was read on the same discovery window, and is therefore a hypothesis, not a
result: it would need its own frozen protocol, a declared second look, and operator approval,
and the discovery window it would be read on is already used by this observation. The positive
leg means are the long-bracket base rate on this cohort and window, not alpha.

**Conclusion.** The events fixed set is closed on this window. Cohort v2 has now returned no
finalist for daily-bar literature formulas (v4), first-bucket intraday formulas (intraday-v1)
and event-conditioned formulas (events-v1). The next source is a different population (cohort v3,
a lower liquidity band) rather than another formula family on this cohort; news-count fields
remain a declared events v2 once an `alpha news` acquisition exists.
