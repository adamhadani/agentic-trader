# Insider cluster leg: breadth replication on disjoint names (`insider` v2)

## Why

The `insider` a priori v1 leg ([result](../../apriori-insider-2026-10-23.md)) failed its absolute test, P1:
+0.087R, ci90 [−0.008, +0.189]. It passed its relative test, P2: cluster names beat same-session matched
controls by **+0.070R, ci90 [+0.009, +0.134]**. That is the strongest relative evidence on the desk, and the
operator's 2026-11-14 stock-take named insider buying as the lead to pursue ("approved all, proceed").

v1 read one population: cohort v3, the 1,000-name liquidity band. A single passing P2 on a single band can be
luck. Its own result doc says "any second look needs a new window". Two routes are named there: a prospective
read (about three years to 300 events) and a market-neutral variant. Neither answers soon.

A **breadth replication** answers in days. It runs the same frozen event rule, unchanged, on names v1 never
read, over the same decision window. If the relative effect is real it should appear on new names. If it was
a draw from one band it should not.

## Declared exception (operator approval 2026-11-14)

This read uses the v1 window (W6, 2016-08-01 → 2026-07-31) on a disjoint population. v1's decision rule says a
changed entry needs "a new, non-overlapping window". This spec amends that clause for one out-of-universe
replication only:

- **The names are new.** No cohort-xu symbol is in cohort v3, so no event in this read was in v1's.
- **The market path is not new.** The two populations share a regime path; that is the stated cost of the
  exception. P2, a same-session paired difference, removes most of the shared market component, and P1 does
  not, so P1 is descriptive here.
- **Ledger.** A ledger row (L44) is written before any label is read.

## Population: cohort xu

The source is screen-v3's ranking: snapshot `00a9a363…`, observed 2026-10-15, with the same window (60
sessions ending 2023-12-29), feed, exclusions and $10 close. It is the union of two disjoint bands, both outside
v3's ranks 401–1,400:

| Source | Ranks | Why |
| --- | --- | --- |
| Large | 1–400 | The names above v3's band |
| Small/mid | 1,401–2,156 | The names below v3's band whose 60-session median dollar volume on the screen window is at least $2M (rank 2,156 is the last name at or above $2M; 1,331 names rank beyond 1,400) |

About 1,156 names in total.

- **Survivorship:** the names were listed and active on 2026-10-15, so the cohort is not point-in-time. That
  is the same bias as v1, stronger for the small band. The matched controls share it.
- **The band cut at $2M is a ranking cut, not a per-session filter.** Per-session eligibility is v1's, below,
  so a small-band name trades only on sessions where it clears v1's floor.

## What is frozen (copied from v1 verbatim)

- **Bracket:** 10:35 ET decision, 2 × ATR14 stop, 3R target, 20-session time exit.
- **Eligibility:** an absolute $5M 20-session median raw dollar-volume floor, $10 minimum price and at least
  30 eligible names, with 15 bp decisive cost per side.
- **Event:** at least 2 distinct officers or directors with opportunistic open-market purchases filed within
  21 sessions, using the routine rule `same_owner_same_month_prior_year` and dropping trades above
  10 × the 20-session median dollar volume. One trigger per name per 63 sessions. The trade is on D, the
  first session after the filing date.
- **Control:** 5 same-session cells in the event's dollar-volume tercile, with insider coverage, no
  qualifying purchase in the prior 126 sessions and not themselves events, nearest in 20-session return.
  Dollar-volume terciles are computed per session within cohort xu, so large caps match large caps.
- **Bootstrap:** stationary session blocks, block mean 10, 2,000 draws, seed 20261023.
- **Inputs:**
  - the insider archive digest `67a262c0…` (SEC-wide, 2015q1–2026q3), unchanged;
  - a new corporate-actions acquisition for cohort xu, pinned by hash in the protocol;
  - a new label cube for cohort xu under v1's cube spec (15 bp, absolute $5M floor), built by
    `alpha pooled power`, which downloads bars for the names without cached bars.

## Rules

| Rule | Role |
| --- | --- |
| **P2:** mean R_cost(leg) − mean R_cost(control) > 0, paired by D, with the ci90 lower bound > 0 | **Primary** |
| **P4:** at least 300 labelled events, at least 100 from 2023-01-01 | Required |
| P1: mean R_cost > 0, ci90 lower bound > 0 | Descriptive (shared market path) |
| P3: 1%-trimmed mean and mean since 2023 | Descriptive |
| By band (large / small-mid), by buyers (2 / 3+), by year, 60-session hold | Descriptive |

**Decision.** The replication **holds** iff P2 and P4 hold.
- **If it holds:** the relative effect is replicated on unread names, which makes two things eligible, each
  needing its own spec and operator approval:
  - a market-neutral insider paper probe;
  - the Sharadar era read (W10, `insider-oos-v1`).

  A pass starts nothing by itself.
- **If P2 fails** (with P4 held): the v1 P2 result is not replicated. The insider lane closes as an alpha
  source, and the watch cards' "unvalidated" label stays.
- **If P4 fails:** the read is underpowered and recorded as such. No threshold is revisited either way.

**Sizing, before any label is read.** A count-only step applies the event rule to the archive and cohort
membership, reading no prices and no labels. It reports raw trigger counts by band and year. If fewer than 600
triggers are counted, the result doc says so in advance and the read goes ahead anyway; the counts change no
rule.

**Sizing result (2026-11-14, count-only).**
- 1,724 accepted triggers: 432 large and 1,292 small/mid, 618 of them from 2023-01-01. 1,075 of the 1,156
  names have qualifying filings.
- v1 labelled 82% of its 1,289 accepted triggers, so about 1,100–1,400 labelled events are expected. That is
  comparable to or above v1's 1,062 and clears the 600 floor.
- The implausible-trade filter and cube eligibility are not applied at this step.

## Non-goals

- No change to v1, the watch cards, the live contract or any production path.
- No combined v3 + xu cube, which would re-read v1's events.
- No new threshold, buyer count, cooldown, control parameter or cost.

## Outputs

- `config/research/pooled/screen-xu-large-v1.json`, `screen-xu-small-v1.json`, and the cohort file
  `cohort-xu.json`: two `liquidity_screen` sources with screen provenance, the union with its identity.
- `config/research/pooled/cohort-xu-insider-v1.json` (the protocol) and `config/research/apriori/insider-v2.json`
  (the entry, version 2, whose decision rule states this spec's exception and decision).
- Ledger row L44 (written before the read), a catalog row in `docs/apriori-alphas.md`, a result doc
  `docs/apriori-insider-breadth-<date>.md`, and the CLAUDE.md insider sentence.
- Run artifacts stay in `~/agentic-trader-research/`, out of Git.

## Constraints

- The repo is public: no local absolute paths, emails or secrets in committed files.
- Research downloads run outside the 10:20–10:50 and 14:20–14:50 New York windows and suspend across scan
  windows. They start on a weekend.
- The installed `.envrc` is sourced read-only for provider keys, and nothing runs in the installed checkout.
- No database, broker or Telegram.
