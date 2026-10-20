# Survivorship spike — October 20, 2026

**Question.** Can the free route fix survivorship in the research cohorts: does Alpaca list
delisted US equities as `inactive` assets, and does its SIP history still serve their bars?
(Source #5 in the [data-augmentation assessment](alpha-data-augmentation-2026-10-18.md).)

**Answer.** Half yes. **Bars: yes.** Alpaca's SIP daily history serves the complete life of every
delisted name probed, including names that no longer exist anywhere in its asset master (SIVB to
2023-03-09, FRC to 2023-04-28, TWTR to 2022-10-27, ATVI, SPLK, VMW, PXD, SGEN, HES, JNPR, ANSS, MRO,
CTLT, DFS, SQ, SWN, AJRD, NLSN, CTXS, SBNY, WBA: 22 of 22, each from 2016-01-04 to its last session).
**Membership: no.** The `inactive` asset list is not a delisting record: it holds 19,163 rows, 16,284 of
them OTC, and its non-OTC delistings stop around 2022 (last-bar year of inactive names with bars:
2018: 132, 2019: 335, 2020: 497, 2021: 703, 2022: 395, 2023: 19, 2024: 16, 2025: 27, 2026: 23). None of
the 22 recent delistings above is in the master at all, active or inactive; `FISV` and `SBNY` are
present only because the tickers were reused. A point-in-time cohort therefore needs a dated
delisting and ticker-change list from outside Alpaca; the free candidate is SEC EDGAR (Form 25 and
Form 15 filings, with the SEC's CIK-to-ticker map), the paid ones are source #11.

## What was run

Throwaway scripts (not in the repo), 03:20–03:45 ET, from the research worktree with the desk's
Alpaca keys, outside the scan windows. Artifacts in `~/agentic-trader-research/survivorship-spike-20261020/`
(`assets.json`, `bars_per_symbol.json`, `bars_nonotc.json`, `bad_nonotc.json`,
`recent_delistings_probe.json`, logs).

1. `GET /v2/assets` for `us_equity`, `active` and `inactive`: 14,404 active, 19,163 inactive
   (inactive by exchange: OTC 16,284; NASDAQ 1,329; NYSE 844; ARCA 471; BATS 137; AMEX 98). 236
   inactive symbols are also active (reused tickers). 68 inactive rows carry a `_DELISTED` suffix.
2. SIP daily bars (`adjustment=all`, 2016-01-01 → 2026-09-22) for every inactive-only, non-OTC
   symbol: 2,633 symbols; 424 are not tickers (CUSIP-like placeholders, `_DELISTED` suffixes,
   one preferred); **2,147 of the remaining 2,209 (97%) have bars**. Among them 412 had a calendar
   year with a 60-session-plus median dollar volume ≥ $5M (219 ≥ $20M, 124 ≥ $50M). By year, the
   number of now-inactive names above the cohort-v3 $5M floor: 2016: 234, 2017: 267, 2018: 310,
   2019: 262, 2020: 201, 2021: 152, 2022: 59, 2023: 9, 2024: 9, 2025: 6.
3. The 22 recent large delistings above, by direct symbol: all 22 served in full; none present in
   the asset master.

## What it means for the desk

- **Survivorship in the pooled cohorts is real and mostly post-2022-invisible to Alpaca.** Cohort
  v2/v3 discovery runs 2021-06 → 2026-01. Alpaca's inactive list would restore ~150 liquid dead
  names for 2021 and ~60 for 2022, and almost none after; the 2023–2026 delistings (banks in 2023,
  the 2023–2025 merger wave) are exactly the names a long-only base rate most needs.
- **The bars are not the problem.** Once a symbol and its delisting date are known, SIP history is
  complete through the last session, so a point-in-time label cube is buildable with the existing
  cache machinery.
- **The missing piece is a dated membership list,** and it is free: EDGAR Form 25 (exchange
  delisting notice) and Form 15 (deregistration) filings carry filing dates, and the SEC ticker map
  resolves CIKs. Nasdaq's daily symbol directory snapshots (already acquired for `alpha
  universe-snapshot`) give the forward record from the day the desk started taking them.
- **Side finding, fixed the same day.** Five active names appeared on the new TXSE exchange; the
  SDK's typed asset parse rejects the whole page on an exchange its enum lacks, which would have
  dropped the dynamic universe from the 10:35 scan and broken `alpha universe-snapshot`. The
  listing now reads raw rows (`BoundedTradingClient.list_us_equity_assets`).

## Recommendation

Do not build the survivorship repair before the next alpha lane. It lowers long base rates and
sharpens B2-type levels, but no fixed-set result since October 7 was anywhere near a pass, so a
bias correction cannot turn one into a finalist; and the native funnel's live base rate is measured
on the live flow, not on a cohort. Build it when a cohort is next re-frozen, as one bounded
workstream: EDGAR Form 25/15 acquisition (SHA-256-pinned, research-only `alpha delistings`), merged
with the Alpaca inactive list for 2016–2022, feeding a point-in-time eligibility column in the
cohort resolver. The next alpha lane stays the FINRA short-sale volume events leg.
