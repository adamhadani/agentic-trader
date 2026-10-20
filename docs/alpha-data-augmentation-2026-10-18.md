# Data augmentation for alpha mining — assessment, October 18, 2026

**Question from the operator.** Would additional, non-OHLCV data sources help alpha mining,
backtesting and factor analysis, and what are the next targets for better alpha?

**Short answer.** Yes, but the order matters. Mining today can see only OHLCV-derived fields
plus four earnings/dividend event fields, on current-membership cohorts, with no cross-sectional
operators. Every lane that has run on that ceiling has found nothing (48 pooled formula–cohort
pairs, two spread protocols, the ex-dividend leg, the recurrence test). The highest-value
additions are **free, point-in-time, event-shaped** sources that fit the existing events lane and
the existing frozen-protocol discipline: FINRA short-sale volume, SEC EDGAR insider filings,
EDGAR filing timestamps for earnings, and EDGAR XBRL fundamentals. A paid vendor is justified
only after a free source shows a signal, or to fix survivorship if the free route fails.

## 1. What the system sees today

From the inventory of `agentic_trader/data`, `research/` and the docs
(`research-data-sources.md`, `market-data-evidence.md`, `alpha-pooled-mining.md`):

| Layer | Available | Not available |
| --- | --- | --- |
| Bars | Alpaca SIP history (15-min delayed, daily from 2016, hourly cached per cohort), IEX live, yfinance fallback/futures | real-time SIP (403, $99/mo), minute caches, quotes/trades history |
| Events | Nasdaq earnings calendar as served **today** (EPS, consensus, BMO/AMC), Alpaca corporate actions from 2016 (process-date filter), Benzinga headlines to 2015 (cached, never studied) | announcement timestamps, consensus history/revisions, declaration dates |
| Macro | VIX/yields/DXY (latest only), FRED latest observation, ForexFactory this week | vintages (ALFRED), history in the journal |
| Reference | current universe YAML with hand sector labels, Nasdaq SymDir today | **point-in-time membership, delistings**, authoritative sectors/subtypes |
| DSL | ts_* operators on open/high/low/close/volume (+ returns, gaps, 4 event fields) | **cross-sectional rank/zscore/group-neutralize**, fundamentals, short interest, insider, options, sentiment |

Two consequences dominate every result so far. First, long base rates are **upper bounds**
(current-universe survivorship), and the cohort-v3 band showed the bracket's base rate halving
when the names get smaller. Second, the formula space cannot express most of the literature:
classic cross-sectional alphas are ranks and neutralisations, which the pooled lane can only
approximate through quantile filters.

## 2. Candidate sources, ranked

Ranked by expected information per hour of work, for the desk's actual label (a 20-session long
bracket on US equities, 5–15 bp cost) and its constraints (free or near-free, cacheable,
digest-pinned, point-in-time by publication date).

| # | Source | Cost | PIT | Fits | Literature prior | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **FINRA Reg SHO daily short-sale volume** (daily files since 2009, per symbol: short volume / total volume) | free | yes (published same/next day) | events-lane field `short_volume_ratio`, `short_volume_z` | daily short-flow predicts negative near-term returns (Boehmer–Jones–Zhang; Diether–Lee–Werner) | **first** |
| 2 | **SEC EDGAR Form 4 insider transactions** (XML, filed within 2 business days) | free | yes (filing date) | events-lane fields `insider_net_buy_30d`, cluster-buy flag | insider purchases, especially clustered, predict 1–6 month returns (Lakonishok–Lee; Cohen–Malloy–Pomorski) | **second** |
| 3 | **EDGAR 8-K / earnings press-release timestamps** | free | yes | fixes PEAD timing (today the report time is unknown) and lets PEAD v2 use a true announcement instant | PEAD is the one event anomaly with a positive probe on this desk | **with PEAD v2** |
| 4 | **EDGAR XBRL companyfacts** (quarterly fundamentals with `filed` dates) | free, messy tags | yes | conditioning tests on native setups (profitability, accruals, leverage), not mining | profitability and accruals are robust monthly anomalies; horizon longer than ours | **third, as a conditioning test** |
| 5 | **Alpaca inactive assets + bars for delisted symbols** | free | partial | survivorship repair for cohorts v2/v3 if Alpaca serves bars for inactive symbols | — | **spike first** (one afternoon) |
| 6 | Benzinga news counts/novelty as events v2 (acquisition exists) | free; LLM scoring of history is not | yes (`created_at`) | events-lane fields `news_count_5d`, `news_novelty` | attention/news momentum (Tetlock) is weak at our cost | after 1–3 |
| 7 | FINRA bi-monthly short interest | free | yes, 1-week lag | slow field | short interest ratio predicts negative returns, monthly horizon | fold into 1 |
| 8 | 13F holdings | free | 45-day lag, quarterly | — | weak at 20 sessions | skip |
| 9 | Historical option chains / IV skew / put-call | paid (CBOE DataShop; Polygon options tier) | yes | new lane | skew and volume ratios predict returns (Xing–Zhang–Zhao; Cremers–Weinbaum) | defer until a free lane pays |
| 10 | Real-time SIP (Algo Trader Plus, $99/mo) | paid | n/a | live intraday parity only | intraday fixed set found nothing on historical SIP | defer |
| 11 | Paid PIT universe/fundamentals (Sharadar, Norgate, EODHD) | paid | yes | survivorship + fundamentals properly | — | only if #5 fails and #4 proves too messy |

Sources 1–4 share one shape: a per-symbol, per-date table with a publication date, acquired
once into a SHA-256-pinned private cache by a research-only `alpha <source>` command, then
exposed to the pooled events lane as `NaN`-before-visible dimensionless fields, exactly as the
earnings fields were. The acquisition module, the field adapter and the fixed-set protocol are
each about the size of the events-v1 work (one SDD day each).

## 3. Method gaps that matter as much as data

- **Cross-sectional operators.** Add `cs_rank`, `cs_zscore` and `group_demean(sector)` to the
  pooled scoring path (the roadmap's A4 asks for the panel contract first; the pooled cube is
  that contract). Without them no new field can be used the way the literature uses it. Small PR;
  no new data.
- **Survivorship.** Until a cohort carries point-in-time membership, every long base rate is an
  upper bound and every pass is a weak pass. Source #5 is the cheap route; #11 the paid one.
- **Point-in-time earnings.** The Nasdaq page gives today's consensus for past quarters;
  surprise fields are therefore contaminated by later revisions. EDGAR timestamps (#3) fix the
  *when*; the *consensus history* stays unavailable without a paid estimates vendor.
- **Power, not search.** 48 fixed-set tests found nothing partly because the label's base rate
  is low and the arms are correlated. The bracket counterfactual (ran 2026-10-18: `no_change`, [result](bracket-counterfactual-2026-10-18.md)) settled the exit
  side; new fields should be tested as fixed sets of ≤ 8 formulas on one cohort, as before.

## 4. Recommended order

1. **Bracket counterfactual v1** (ran 2026-10-18: `no_change`, [result](bracket-counterfactual-2026-10-18.md)): exits, on the replayed native setups.
2. **Survivorship spike** (#5): does Alpaca return SIP bars for inactive symbols? If yes, build a
   dated inactive-asset list and re-screen cohort v2 with point-in-time eligibility; if no,
   record it and price option #11.
   **Ran 2026-10-20** ([spike](survivorship-spike-2026-10-20.md)): SIP serves full bars for every
   delisted name, but Alpaca's asset master drops recent delistings (its inactive list stops being a
   record around 2022), so point-in-time membership needs EDGAR Form 25/15 dates; deferred until a
   cohort is next re-frozen.
3. **Free PIT data pack, one lane at a time, each a frozen fixed set on cohort v2's cube:**
   FINRA short volume (#1) → EDGAR insider (#2) → EDGAR timestamps + PEAD v2 (#3). Each lane:
   research-only acquisition, digest-pinned cache, `NaN`-before-visible fields, checks A and C,
   campaign on operator approval. Stop the pack at the first finalist and run the probe contract.
   *Built 2026-10-20:* the FINRA lane ([pooled mining](alpha-pooled-mining.md#short-volume-lane-short_fields_v1)) has its acquisition (`alpha short-volume`), four `short_fields_v1` fields and two frozen fixed sets (`short-v1`, `cohort3-short-v1`); checks A and C are pending, campaigns on operator approval.
4. **Fundamentals conditioning** (#4): a predeclared test, recurrence-style, of whether a
   profitability or accruals filter changes the native setups' base rate.
5. **Cross-sectional operators** alongside step 3, so each new field can also be ranked and
   sector-neutralised.
6. **Paid data** (#9–#11) only on evidence from steps 2–4.

Nothing above changes the production desk. Every item is research-only until its own frozen
protocol passes, and the north star (one or two honest cards per session with risk controls and
book-aware sizing) is served by step 1 and by the card evidence already in place.
