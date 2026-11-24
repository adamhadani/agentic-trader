# Completeness critic

## Missing topics

### No synthesis yet: report.md does not exist, so nothing ranks or reconciles the ten maps

The operator asked for one holistic review. I checked ~/agentic-trader-research/holistic-review-20261021/report.md between 19:31 and 20:00 and it was missing. The maps overlap and disagree, and only a synthesis can settle that. (a) Four maps each propose a prospective scorer for the frozen fixed sets: architecture §7.8, history §7.2, alpha_pipeline I5 and data I4. Three propose an L3 selector, each with a different key: a meta-label P(target), a vol_20 composite, and a −Δvol tie-break. (b) history §7.1 and signals §7.1 say "turn card_policy to preview now"; live §7.1 says to rebuild card_stats first, because it is censored and counts repeats. (c) Some operator decisions are dated and spread across maps. The PEAD probe expires 2026-10-28 (live gap 7). US DST ends 2026-11-01, which shifts the UTC-anchored 4h buckets (agentic_trader/data/market_data.py:119-138) in the middle of the vol_20 / sector_rel_mom_60 prospective window of 10-21 → 11-04 (signals gap 4). The insider campaigns still need approval. (d) Figures conflict on which scans send cards: card-evidence.md:378-380 says 18 of 22, live.md Q3 says 14 of 37 outside the slots. These need reconciling from the journal's `trigger` field. The report needs one ordered plan by expected effect on card quality, with dependencies (for example, bar semantics and the swing-scan budget must be fixed before any new base-rate replay), effort, and an explicit list of operator decisions.

**Where to look:** maps/*.md sections 7-8; docs/alpha-roadmap.md:458-500 (L1-L5) and :502-630; docs/apriori-alphas.md "Part 2"; docs/card-evidence.md:378-380; agentic_trader/data/market_data.py:119-138

### Fundamental (EDGAR XBRL) signals: the desk's own next-ranked data source is absent from every map

No pooled pair, a priori entry or map proposal uses accounting data. Yet docs/alpha-data-augmentation-2026-10-18.md:45 and :91-92 rank XBRL companyfacts #4, "third, as a conditioning test" of native setups. The pooled map explains the 64 nulls partly by "most anomalies fail once microcaps are excluded" (Hou-Xue-Zhang). It leaves out that the q-factor families are the documented exceptions that survive in liquid names: investment and asset growth (Cooper-Gulen-Schill 2008), profitability (Novy-Marx 2013; Hou-Xue-Zhang 2015 and 2020), net issuance (Pontiff-Woodgate 2008) and accruals (Sloan 1996). data.md I6 mentions XBRL only as a source of shares outstanding. These signals are monthly and slow. They should be tested as a predeclared conditioning filter on the replayed native setups, or on a horizon-matched cube, not on the 20-session 3R long label.

**Where to look:** docs/alpha-data-augmentation-2026-10-18.md §2 rows 4 and 11, §4 item 4; agentic_trader/research/insider_filings.py:574-596 (ticker→CIK map, reusable for companyfacts); agentic_trader/research/pooled/cube.py:53 (no fundamental arrays)

### Options-implied information (IV skew, IV spread, put/call volume) and capturing its history from now on

Options-implied skew, IV spread and put/call volume are among the few cross-sectional predictors documented in optionable, meaning large and liquid, names at weekly horizons: Xing-Zhang-Zhao 2010; Cremers-Weinbaum 2010; Pan-Poteshman 2006; An-Ang-Bali-Cakici 2014. Large liquid names are exactly the constraint the pooled lane is fighting. The repo's options module is a yfinance chain snapshot used only for display GEX (agentic_trader/options/fetcher.py:1-40), and the data assessment defers options because historical data costs money (row 9). No map proposes the cheap route: start a digest-pinned daily capture of chain and IV summaries for the universe now, through Alpaca option snapshots or yfinance, using the daily-panel worker pattern. A point-in-time history would then build up for a later frozen test. data.md I4's warehouse list leaves options out.

**Where to look:** agentic_trader/options/fetcher.py, gex.py; docs/alpha-data-augmentation-2026-10-18.md §2 row 9; docs/alpha-daily-panel.md; maps/data.md §7 I4

### LLM-driven alpha research (agentic factor mining) and LLM look-ahead leakage

The operator asks for outside work to integrate and for a richer DSL and a steady cadence. The maps treat LLMs only as a card gate (measured to add nothing) and a news scorer, and cite only Lopez-Lira-Tang. Directly relevant open work:
- Microsoft RD-Agent(Q) (2025, built on Qlib), AlphaAgent (2025, regularises originality and hypothesis-formula alignment against decay) and Alpha-GPT (2023).
- Text signals from filings (Kim-Muhn-Nikolaev 2024).

One constraint is binding: models trained on post-2016 text "know" outcomes in the 2016-08→2021-12 discovery window (Glasserman-Lin 2023; Sarkar-Vafa 2024). Agent-proposed formulas therefore have to be charged through the existing ledger and judged only on data after the model's training cutoff, which ties this to the prospective-scorer proposals. The charge-before-read ledger and vet_expression are already the guard rails that would keep agent output honest.

**Where to look:** agentic_trader/research/pooled/ledger.py:35-101; agentic_trader/storage/alpha.py:1079-1141; agentic_trader/research/pooled/scoring.py:82-96 (vet_expression); agentic_trader/research/alpha/search.py:60-184; agentic_trader/agent/copilot_tools.py:184-192

### Build versus buy for point-in-time data

The maps propose several multi-day free builds:
- a dated delisting list from EDGAR Form 25/15 (pooled I10, data I5, history I7);
- SIC/industry mapping (pooled I1, data I6, risk I3);
- an incremental SIP warehouse (data I4);
- XBRL parsing.

None compares this with paid point-in-time datasets that fix survivorship, delistings, sectors and fundamentals at once: Norgate, Sharadar via Nasdaq Data Link, EODHD (data-augmentation row 11). Nor does any price Alpaca Algo Trader Plus (about $99/month real-time SIP, row 10). That plan would also close the IEX-versus-SIP gap between live and research (data G1) and the IEX quote-freshness refusals for mid-caps (execution G6). About 8,900 per-symbol trials and 64 pooled pairs have already been spent on survivorship-biased cohorts, so a priced comparison belongs in the plan: dollars per year against SDD-days, plus the point-in-time risk that remains, such as reused tickers (FISV, SBNY).

**Where to look:** docs/alpha-data-augmentation-2026-10-18.md §2 rows 10-11, §4 item 6; docs/survivorship-spike-2026-10-20.md; docs/research-data-sources.md:9-43

### The book is empty because entry depends on operator taps, so the portfolio and execution layers have no live effect

The north star includes "allocation decisions based on existing positions". live.md Q10 records 9 taps on 42 cards and 3 accepted entries in about 24 sessions, and the preview book was "one share" (card-evidence.md:232). With no positions, book_vol, correlation caps, aggregate stop risk and every portfolio or risk recommendation do nothing live. Fill rate, shortfall and exit slippage (execution G3) cannot accumulate either. Only live.md §7.7 raises auto-entry, and only for PEAD probes. The review should weigh a paper-only mode that takes every sent card at its default tier through EntryExecutionService, with operator authorization kept for any real-money mode. It should also measure how selective the operator's taps are. This changes the entry-authorization contract (CLAUDE.md contract 1), so it is an operator decision.

**Where to look:** agentic_trader/execution/entries.py:50-57; agentic_trader/agent/copilot.py:2912-2988; agentic_trader/storage/workflow.py:334-446; agentic_trader/research/apriori/probe_outcomes.py; maps/live.md gaps 7 and 9

### Where the macro and regime data comes from: load-bearing gates rely on unofficial or scraped endpoints

The risk map covers some of the failure behaviour, but no map treats sourcing as a data-gathering gap. Three gates depend on these feeds:
- **VIX/TNX/DXY via yfinance.** When FRED enrichment is also missing, a failure raises and aborts the whole scan (agentic_trader/agent/regime.py:160-170).
- **The ForexFactory "this week" JSON**, fetched with a browser User-Agent (agentic_trader/agent/calendar.py:186-191). A failed fetch caches an empty list for 30 minutes (:216-221), so lockout checks fail open.
- **FRED fredgraph.csv**, scraped for the latest observation only (agentic_trader/agent/macro.py:296-349).

Recommended replacements: the Fed's published FOMC calendar and the BLS/BEA release schedules, both published a year ahead, kept as a static digest-pinned table; FRED VIXCLS or Cboe data for VIX; and journaled provenance for each input. The risk map's proposed replay of the regime gates also needs historical macro inputs, which the desk does not store (data-augmentation §1, Macro row).

**Where to look:** agentic_trader/agent/regime.py:133-227; agentic_trader/agent/calendar.py:112-125, 173-221; agentic_trader/agent/macro.py:207-349; agentic_trader/constants.py:165-173

### Cross-asset time-series momentum (trend) on the ETF32 the desk already scans

The live ETF32 already holds:
- bonds: SHY, IEF, TLT, TIP, LQD, HYG, AGG, BIL;
- international: EFA, EEM, EWJ, EWG, EWU, EWC;
- metals: GLD, SLV (config/config.yaml:96-130).

Trend-following is one of the best-replicated effects across asset classes (Moskowitz-Ooi-Pedersen 2012; Hurst-Ooi-Pedersen 2017; Baltas-Kosowski 2013), and its returns are only loosely tied to the long-equity-beta native stream that trails SPY. No doc tests it; a grep finds no TSMOM or Moskowitz reference. It cannot be expressed in the per-symbol DSL, because every score is z-normalised over its own trailing 30 bars (research/alpha/strategy.py:144-152), which destroys 12-month level signals. The persistent ETF book tested only cross-sectional sector momentum. A predeclared a priori leg (long/flat, volatility-scaled, monthly) would add a card source whose excess over SPY is not beta by construction, and it answers portfolio G6 (beta concentration). It needs rebalance-style orders (execution G6).

**Where to look:** config/config.yaml:96-130; agentic_trader/research/alpha/strategy.py:144-161; docs/alpha-persistent-book-2026-09-17.md; agentic_trader/research/apriori/

## Errors found

- report.md was missing at ~/agentic-trader-research/holistic-review-20261021/report.md every time I checked (19:31 to 20:00 IDT). The corrections below are against the maps the report will be built from.
- pooled.md §2 says "Five subclasses add one data source each". There are four: IntradayCampaignProtocol (agentic_trader/research/pooled/campaign.py:403), EventsCampaignProtocol (:451), ShortVolumeCampaignProtocol (:494) and InsiderCampaignProtocol (:555).
- execution.md G4 says "outside the eight supported ones". Nine activity types are accepted: FILL, CSD, CSW, JNLC, FEE, CFEE, DIVFEE, DIV and INT (agentic_trader/accounting/ledger.py:19-22). Also, a CSD/CSW/JNLC carrying a nonzero share quantity is rejected as "Unsupported share adjustment" (ledger.py:127-128), and any quantity or cash mismatch, for example after a split, is also an issue (ledger.py:142-148).
- execution.md G1 (Little's law) says "At about 1.5 fills per day ... steady state is already about 8 positions". 1.5 is cards per session, not fills. live.md Q10 records 9 taps and 3 accepted entries in about 24 sessions (about 0.13 fills/day), so the steady state is about 0.7 positions. The capacity risk is real only if most cards are entered, for example under auto-execution.
- signals.md §2 says FormulaicAlphaStrategy "fires when the latest z crosses entry_threshold". It is a level condition: entry_directions sets ±1 whenever z ≥ threshold or z ≤ −threshold (agentic_trader/research/alpha/strategy.py:155-161; screeners/formulaic.py:102-104). It therefore fires again on every bar beyond the threshold, and only the 12-hour duplicate rule suppresses the repeats.
- signals.md §3 step 4 and §6.13 say 15m bars are fetched "only when a 15m alpha exists" and estimate "about 360 requests for 180 names". data.md §3 says "each contract gets three paced requests ... 10d 15m" ("roughly 540"). Both are partly wrong. The code fetches 15m bars for every symbol in the scan whenever any active, shadow or probe registry definition has timeframe 15m (agentic_trader/agent/copilot.py:834-837; data/market_data.py:204,223). Shadow definitions count, so with 15m shadows registered (live.md Q13 shows 16 shadows), suggestion scans fetch 15m universe-wide. That fits data.md's measured 28% share of 15m captures. The fix is to scope the 15m fetch to the symbols a 15m definition covers.
- architecture.md §4, signals.md §4 and history.md §4 and §6.1 cite live card_stats figures as evidence of negative card EV: runner-ups −0.40R on 10-07, −0.17R on n=33, 27% target / 73% stop, and TREND_PULLBACK LONG −0.376R on 34 mature. history.md adds that enforcing card_policy "would withhold most native cards". live.md Q1 shows those early figures were censoring artefacts: on a 2R bracket, stops resolve before targets. The 2026-10-21 snapshot is +0.535R on 67 mature, so enforcing at threshold 0 would withhold nothing today. Both figures are raw R, inflated by repeats (87 rows cover 41 setups) and drawn from one regime. Only the replay is defensible evidence.
- signals §4 and §6.1, architecture §4, history §6.1 and risk §4 say native entries trail SPY by −0.11R excess. That number uses the beta-one SPY control (research/setups/outcomes.py:219). I re-ran it read-only on the same frozen frames (~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz, arm A0_live, R clipped to ±10) with ~/agentic-trader-research/holistic-review-20261021/scripts/pm_beta_adj_R.py. Return-space betas per stop-width quintile are 0.39–1.21, and the beta-adjusted excess is −0.062R (session mean −0.069R, naive session SE 0.024). About 40% of the quoted gap is therefore beta mismatch. The negative-excess conclusion holds at about half the size. pooled.md gap 14 has the direction right but gives no number. The betas are in-sample, so this is descriptive.
- architecture.md §7.3 says a meta-label ranker "is the only lever on G1 that needs no new alpha; even a modest AUC beats random at 2 picks per session". This is unsupported. The predeclared setup-outcome study trained a ranker on these same features, and in the holdout it did significantly worse than setup_quality: top-2 −0.293R against −0.126R. Every scorer, including random, lost money in that holdout (docs/setup-outcomes-2026-09-23.md "Selection (H3)"). Any new selector has to explain why it would differ (for example an excess-R label, de-duplication, purged CV) and be predeclared.
- risk.md G5 says a calendar failure only "tells the LLM 'Lockout verified: CLEAR'". That understates it. The same cached empty list feeds is_in_lockout_window (agentic_trader/agent/calendar.py:118-125). So the deterministic macro-lockout entry rule (evaluator.py:526-541), the whole-scan lockout (copilot.py:809-831) and admission's preflight check (copilot.py:3962-3967) all fail open for 30 minutes after a ForexFactory exception or non-200 response (calendar.py:186-221).
- live.md §2 (Bracket row) says "The stop is max(swing low − 2 ticks, 1.5 ATR)", which mixes a price with a distance. The code takes the wider distance: stop = entry − max(entry − (swing_low − 2 ticks), 1.5 × ATR14) (agentic_trader/agent/evaluator.py:208-216). For trend-pullback, ATR14 is the 4h ATR (screeners/strategies.py:131, 163), not a daily ATR.
