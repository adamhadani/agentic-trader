# Map: data acquisition and processing

Repo: `agentic-trader-intraday` at `db82817` (identical to production). All paths are relative to the repo root. "Measured" means a read-only check I ran on 2026-10-21 against the private evidence store (`~/.local/state/agentic-trader/market-data/`) or the research bar cache (`~/agentic-trader-research/setup-cache-v1/`). I did not change any state.

## 1. Purpose and scope

This layer turns vendor feeds into the bars, events and universes that both the live suggestion scan and the research lanes consume. Live, it fetches Alpaca bars, plus yfinance as a fallback, for the static and dynamic universe twice per session. It also captures corporate actions daily and fetches news on each scan. In research, it builds frozen, digest-pinned acquisitions: SIP bar caches, the pooled label cube, Nasdaq earnings pages, FINRA short volume, EDGAR insider filings, corporate actions and news. These sit behind cohorts frozen from current-membership snapshots. Whether research evidence says anything about live cards depends on how closely the live and research data contracts match.

## 2. Components and contracts

### 2.1 Live market-data path
- **`AlpacaDataProvider.fetch_bars`** (`agentic_trader/data/providers.py:178-277`) issues one-symbol requests on the configured feed, with `adjustment="raw"` by default (`:186`). Before the SDK parses anything, it validates each page envelope (`:57-70`) and observes the raw pages (`:227-237`). It records `source_quality` row counts (`:260-265`) and tags the frame with `feed`, `adjustment` and `timeframe` attrs (`:266-270`). An unknown timeframe silently maps to 1h (`:176`), and an unknown period maps to 365 days (`:104`).
- **`fetch_daily_many`** (`:310-346`) sends batched daily requests of 100 symbols. It has no evidence capture and no attrs, and is used by book sizing (`execution/book_sizing.py:222`) and the live PEAD drift path (`research/apriori/pead_live.py:135-137`).
- **`fetch_latest_price`** (`:348-379`) tries the latest trade, swallows that error and falls back to the latest bar. There is no feed pin and no timestamp or age check. It is used for the tap-time entry price (`agent/copilot.py:3151`) and for trailing-stop ratchets (`agent/copilot.py:2554`).
- **`YFinanceDataProvider`** (`providers.py:382-460`) uses `auto_adjust=True` (`:426,:431`) and tags its frames `adjustment="yfinance_auto_adjust"`.
- **`CompositeMarketDataProvider`** (`:463-562`) wraps primary and fallback providers in `RunnableWithFallbacks` (`resilience/fallback.py:54-210`). Reads go through `BoundedReadExecutor`, which has fail-fast capacity and never replays a read that missed its deadline (`resilience/reads.py:22-70`). When every provider fails it returns an empty frame (`providers.py:529`).
- **`MarketDataFetcher`** (`data/market_data.py:63-243`) builds Alpaca (with a `BarEvidenceStore`) and then yfinance (`:81-94`). Its `fetch_data` (`:197-239`) reads 1y of daily bars, 60d of hourly bars and optionally 10d of 15m bars, and computes indicators. `resample_to_4h` (`:119-138`) is a plain `df.resample("4h")` on a UTC index. `cache_ttl_seconds` is stored but never used (`:66,72`).
- **Feed configuration.** `MarketDataConfig.alpaca_feed` defaults to `"sip"` (`config.py:689`), but `load_config` defaults it to `"iex"` unless `ALPACA_DATA_FEED` is set (`config.py:1043-1050`). Measured: every bar capture on 2026-10-20 (3,432 captures) was `alpaca:iex` (1h, 15m and 1d raw; 1d `all`). The live PEAD drift source is hard-wired to SIP (`agent/copilot.py:397-401`) and reads only completed prior sessions (`pead_live.py:132-137`).
- **Pacing.** `RequestPacer` is a 60-second sliding window, default 150 requests/min (`data/pacing.py:14-41`; `config.py:698`).

### 2.2 Clocks, bars and calendars
- `market/bars.py` defines `TradingSession`, `SessionSchedule`, the `SessionClockPolicy` decision windows and `build_session_bars`. That builder aggregates only completed regular-hours (RTH) minutes, anchored at each observed open, and raises `SessionCoverageError` rather than imputing (`:276-346`). `fixed_bar_closes` and `completed_fixed_bars` cover the legacy fixed-duration clock (`:79-109`). Only the research and forward observers use this builder.
- `market/quality.py:11-69` defines `BarSourceQuality` (raw, parsed and normalised row counts, `require_lossless`).
- `data/sessions.py:32-146` defines `AlpacaSessionSource`: an observed Alpaca calendar, minute acquisition in half-open chunks of at most 31 days with receipts, and a frozen check on feed, adjustment and timeframe (`:86-90`, `:140-145`).
- `market/session.py:515-560` defines `CompositeMarketCalendar` (Alpaca → Finnhub → deterministic). The research lanes deliberately use Alpaca's calendar, because the offline calendar lacks ad-hoc closures (`docs/alpha-pooled-mining.md:1045-1047`).

### 2.3 Raw evidence
`data/evidence.py` defines `BarEvidenceStore` and `BarCapture`. Each capture writes a manifest before network access, then `page-NNNN.json` files, then `result.json`. Limits are 100 pages, 128 MiB, a 1 GiB free-space reserve, and no deletion (`docs/market-data-evidence.md:32-67`). `BAR_QUERY_FIELDS` lists `asof` (`evidence.py:24-27`), but nothing sets it.

### 2.4 Universes
- **Live static universe.** `config/config.yaml:95-272` holds 159 symbols in three groups: `etf32` (32), `mega_caps` (65) and `research_cohort` (62). Sector labels are hand-written (`config.py:101-104`; the default is `unknown`). There are also 13 explicit `contracts`, including futures served only by yfinance (`providers.py:153-156`).
- **Live dynamic universe.** `screeners/dynamic_universe.py` takes Alpaca most-actives ranked by **trades** (`:317`) and movers (`:311-341`). `select_dynamic` (`:113-176`) filters on symbol shape, exchange and name heuristics. The scan then applies `liquidity_gate` (`:219-262`): the median 20-session close×volume must reach a percentile of the static equities' medians on the same feed. The orchestration is in `agent/copilot.py:2077-2174`. Up to 20 names are added per scheduled scan.
- **Research universes.**
  - `ETF_RESEARCH_UNIVERSE` (`research/alpha/universe.py:24-34`).
  - Equity snapshot `prospective-equity-300-v2`: a SHA-256 sample of current non-ETF listings (`research/alpha/equity_universe.py:32-36,191-224`; `docs/alpha-equity-universe.md:21-32`).
  - The 64-name IEX liquidity screen (`research/alpha/liquidity.py:1-60`; `docs/alpha-equity-universe.md:118-126`).
  - `resolve_mining_universe` (`research/alpha/mining_universe.py:115-136`).
  - Pooled cohorts (`research/pooled/cohort.py:61-115`). v2 has 452 names: 127 config-group names plus the top 400 by SIP median dollar volume over the 60 sessions ending 2023-12-29. v3 has 1,000 names, ranks 401–1,400 (`config/research/pooled/cohort-v2.json:4`, `cohort-v3.json`; `research/pooled/screen.py:1-10`).
- **Point-in-time eligibility.** `point_in_time_eligibility` (`research/pooled/cohort.py:193-292`) evaluates each session D as of D-1. It uses **raw** bars for the $10 price floor and the dollar-volume floor, **adjusted** bars for ATR, and reuses the live gate functions (`:27`).

### 2.5 Event and reference data
- **Corporate actions.**
  - Model and normalisation: `data/corporate_actions.py`. It covers 14 kinds, with `effective_on` defined per kind (`:62-78`). Normalisation fails closed on an unknown type or a missing field and deduplicates by id (`:263-274`).
  - `AlpacaCorporateActionsSource` makes chunked requests with `limit=None` and fails the whole fetch if any chunk fails (`:318-367`). `CorporateActionsLookup.covers` separates `ok` from `uncovered` (`:370-396`).
  - The daemon worker runs once per New York date outside scan windows. It covers universe, held and extra symbols over `process_date` in [−45, +120] days (`data/corporate_actions_worker.py:44-68`).
  - The research cache is keyed by request hash and checked by content hash (`research/corporate_actions.py:95-144`).
- **News.**
  - `data/news.py` defines `NewsItem` with `extra="ignore"` (`:33-60`) and `AlpacaNewsSource`.
  - Live: one fetch per scan without a timeframe, and an LLM score for at most 6 symbols (`docs/news-context.md:9-29`).
  - Research: `fetch_news_cached` makes monthly chunks with binary splitting on truncation and stores a gzip cache keyed by request hash (`research/news.py:99-175`).
- **Earnings.** The Nasdaq calendar is fetched one page per weekday and saved verbatim with SHA-256 (`research/apriori/earnings_history.py:1-10`). The same parser serves the live blackout (`agent/earnings.py`). The pooled `events_fields_v1` fields make a report visible at D+1 and treat a name change as an identity break (`research/pooled/events.py:1-11`).
- **FINRA Reg SHO.** The acquisition reads the consolidated CNMS daily file (`research/short_volume.py:42`). It checks the trailer row count and a minimum of 1,000 rows, fails closed on a missing session unless `allow_absent` lists it, and computes a files digest that also binds the absent sessions (`:75-113,194-215,270-312`).
- **SEC EDGAR Form 3/4/5.** The acquisition reads the quarterly data-set zips plus the *current* `company_tickers.json` (`research/insider_filings.py:1-13,61`). `symbol_ciks` maps today's tickers to CIKs and fails closed on a ticker listed under two CIKs (`:574-596`). A filing is visible from its filing date (`research/pooled/insider_fields.py:1-30`).

### 2.6 Research bar caches and the label cube
- **Bar cache.** `_fetch_cached` fetches one symbol at a time in one-year chunks with retries. It writes one immutable `.npz` per symbol and timeframe and **strips attrs** (`research/setups/runner.py:158-189`). A range claim per directory protects reuse (`:481-496`). The pooled runner keeps adjusted bars in `bars/` and raw bars in `bars_raw/`, records a per-file digest list, and verifies the cube against it (`research/pooled/runner.py:220-245,372-403`).
- **`LabelCube`** (`research/pooled/cube.py:57-389`).
  - Labels: one long bracket per eligible (session, symbol) cell, decided at 10:35 with a 2×ATR14 stop, a 3R target and 20 sessions (`:1-9,57-86,237-336`).
  - Arrays: eligible, labelled, `r_gross`, `r_cost`, holding, hit, tiebreak and `dollar_volume`.
  - Integrity: content hash checked on load (`:375-389`). Labels can be read only through `window()` (`:195-204`).

### 2.7 Seams
| Consumes | Produces |
| --- | --- |
| Alpaca data API (bars, latest trade, screener, assets, corporate actions, news); yfinance; Nasdaq calendar and symbol directories; FINRA CDN; SEC www | `ContractMarketData` for strategies (`data/market_data.py:40-60`); `dynamic_universe_built` and `scan_candidates_ranked` journal events; `corporate-actions/capture` events; news blocks on cards; raw page evidence; research `.npz` caches; `LabelCube`; event, short and insider field frames; cohort and screen JSON |

## 3. Data flow

**Live, at 10:35 and 14:35 ET.**
1. The scan selects the static universe plus dynamic names (`copilot.py:877-886`).
2. `_fetch_universe` calls `fetch_data` per contract, eight at a time (`copilot.py:2228-2253`). Each contract gets three paced requests: 1y daily, 60d hourly and 10d 15m. All three use IEX raw bars, with a silent yfinance fallback.
3. The 4h frame is resampled from hourly bars on UTC boundaries.
4. The coverage gate compares hourly RTH bars against SPY (`screeners/coverage.py:9-48`).
5. The dynamic liquidity gate runs.
6. Strategies read `iloc[-1]`, which includes the bar still forming (`screeners/strategies.py:93-108`; acknowledged in `research/setups/replay.py:16-23`).
7. Corporate-action and news blocks are attached.
8. Book sizing refetches raw daily bars for names whose frames are missing or not raw (`execution/book_sizing.py:206-232`).
9. Card outcomes and `card_stats` (which feeds `card_policy`) label the journaled candidates on the **configured feed, raw** (`research/setups/sources.py:26-34`; `card_stats_worker.py:116`).

**Research.**
1. A cohort is frozen from a snapshot and screen.
2. A protocol pins the digests of every input (cohort, short and insider files, calendar pages, corporate actions).
3. `build_cube_inputs` fetches SIP daily bars (adjusted and raw) and SIP adjusted hourly bars into the cache, computes point-in-time eligibility and labels the cube.
4. Field frames (events, short, insider) are built offline from the cached acquisitions, read at D-1 and scored against cube windows.
5. Setup base rates and the bracket counterfactual replay the native strategies on **SIP, adjustment `all`** (`research/setups/baserates.py:57-58`).

## 4. What has been tried

- **Feed access** (`docs/research-data-sources.md:9-23`). Recent SIP returns 403 as an entitlement restriction, not throttling. SIP with an `end` at least 15 minutes old works. Historical IEX starts at 2020-07-27 (`:69-77`).
- **Pooled lane, SIP cubes.** 64 formula-cohort pairs across daily, intraday, events and short-volume protocols produced no finalist. On cohort v3 the long-bracket base rate roughly halved, from about +0.18R to +0.08R (`docs/alpha-pooled-cohort3-2026-10-16.md:113-139`; `docs/alpha-pooled-short-v1-2026-10-20.md:72-108`).
- **Building a 1,000-name cube took about a day.** Ten years of hourly bars were fetched sequentially at about 2.3 s per request (`docs/alpha-pooled-cohort3-2026-10-16.md:26-35`).
- **FINRA coverage** starts at 2018-08-01. That leaves about 836 scored discovery sessions, against about 1,360 for the daily set. Identity breaks number 29 on v2 and 98 on v3 (`docs/alpha-pooled-short-v1-2026-10-20.md:20-35`).
- **Survivorship spike** (`docs/survivorship-spike-2026-10-20.md:7-17,26-36`). SIP serves full history for 22 of 22 recent delistings. The Alpaca inactive list stops being a delisting record around 2022. Now-dead names above the $5M floor per year: 2016: 234, 2017: 267, 2018: 310, 2019: 262, 2020: 201, 2021: 152.
- **IEX forward lane.** It never scored, because IEX minute gaps fail the strict coverage rule (`docs/alpha-roadmap.md:633-638,667-669`). Session replay reached full coverage on SIP but not on IEX (`docs/alpha-roadmap.md:677`).
- **Corporate-actions preview week:** 9 blocks, 0 `would_block`, 3 `uncovered`. The uncovered share is about one third of the card flow (two most-actives cards and one PEAD card) (`docs/corporate-actions.md:216-219`).
- **News.** Benzinga history reaches 2015-01-02 and is cached, but no study has used it (`docs/news-context.md:151-155,179-180`).

## 5. Strengths worth preserving

- **Lossless evidence before parsing.** Raw pages are kept and row counts are reconciled, so silent row loss is detectable (`providers.py:227-276`; `market/quality.py:63-69`). Strict panels reject that loss (`docs/market-data-evidence.md:105-110`).
- **Digest pinning everywhere.** There are request hashes and content hashes, absences are bound into digests, a mismatch is refused on reuse, and invalid files are kept with status `invalid` (`short_volume.py:157-215`; `research/corporate_actions.py:118-144`; `research/news.py:80-93`; `pooled/runner.py:220-245`).
- **Point-in-time discipline in research.**
  - Raw bars feed the price and liquidity gates, adjusted bars feed returns and ATR, and the live gate functions are reused (`cohort.py:205-211`; `pead_events.py:9-13`).
  - Visibility rules are explicit: earnings visible at D+1, FINRA same day, EDGAR filing date.
  - Identity breaks are respected.
- **A correct session-anchored RTH bar builder** that never imputes (`market/bars.py:276-346`), and observed exchange calendars.
- **Fail-closed structure.** Corporate-action normalisation fails closed (`data/corporate_actions.py:263-274`). The lookup distinguishes `uncovered` from `ok`, so the entry rule never claims coverage it lacks. Research is charged before I/O.
- **Bounded, non-replaying reads** (`resilience/reads.py`). Book sizing refuses yfinance-adjusted frames (`research/setups/covariance.py:20-25`).

## 6. Gaps, weaknesses, risks and inconsistencies

**G1. Live and research see different bars for the same signal (measured).** The live scan reads **IEX** raw hourly bars (`config.py:1043`; all 2026-10-20 captures were `alpaca:iex`). Replay base rates and the bracket counterfactual use **SIP, adjustment `all`** (`baserates.py:57-58`). Hours per session measured:

| Symbol | Live (IEX) | Research cache (SIP) |
| --- | --- | --- |
| SPY | 9 hourly bars, 08:00–16:00 ET | 16 bars, 04:00–19:00 ET |
| AMKR, AFL, AEM, ATKR (mid-caps) | 7 bars, regular hours only | AMKR: 04:00 bar on 27% of days, 17:00–19:00 bars on about 30% |

`resample_to_4h` buckets on UTC boundaries (`market_data.py:119-138`). Live, AMKR therefore gets about two 4h bars per day. Replay gets two to four, irregularly. The bucket edges also shift by an hour at each daylight-saving change, and the 08:00–12:00 EDT bucket mixes pre-market prints into the trigger bar. The native trigger (4h EMA20 distance, RSI dip and recovery, ATR, at `strategies.py:106-137`) is therefore computed on different series live and in replay. `replay.py:1-23` claims identical shapes and windows. The only parity gap it acknowledges is the bar still forming.

`card_stats` and `card_policy` label outcomes on IEX (`card_stats_worker.py:116`; `sources.py:26-34`). IEX highs and lows sit inside the consolidated range, so they under-count stop and target touches compared with broker reality. Result: the "honest base rate" behind cards is measured on a different signal than the one that fires live.

**G2. Raw, unadjusted live indicator history across splits.** Daily EMA50/EMA200 over 1y (`strategies.py:85-99`), hourly bars over 60d, and the book-sizing covariance (`covariance.py:1-5`; `book_sizing.py:222`) all use raw closes (`providers.py:186`).
- After a reverse split, raw close sits far above the pre-split EMAs, so the trend test reads "bullish" for months.
- After a forward split, the name is silently suppressed.

The `corporate_action` block only covers −5 to +7 days (`docs/corporate-actions.md:104-110`). Capture covers only a 45-day `process_date` lookback (`corporate_actions_worker.py:63`). Dynamic, PEAD and operator names are never covered (`docs/corporate-actions.md:231-236`), and the movers list is where reverse-split names concentrate. The doc lists this as a known limit (`:223-224`). Nothing has measured how often it happens.

**G3. Silent cross-vendor fallback and an unpinned latest price.**
- If Alpaca fails, the scan uses yfinance frames: auto-adjusted, consolidated volume, a different clock (`providers.py:426-437`; `market_data.py:88-94`). No live consumer checks `attrs["feed"]`; only the research paths do (`data/sessions.py:86-90,141-145`; `cli/commands/alpha.py:188`).
- A single fallback frame among the static names shifts the dynamic liquidity reference percentile. Consolidated volume is roughly 30× IEX volume, so a dynamic name served by yfinance passes the gate trivially.
- `fetch_latest_price` has no feed and no age check, and can fall back to yfinance `fast_info` (`providers.py:348-379,439-460`). It still drives tap-time entry prices and trailing-stop ratchets (`copilot.py:2554,3151`).

**G4. Research cohorts carry survivorship and look-ahead in the discovery window.**
- Every pooled protocol uses discovery 2016-08-01 → 2021-12-31, selection 2022-01-03 → 2023-12-29, confirmation 2024-01-02 → 2026-07-31 (`campaign-v2.json:10-22`; same in `cohort3-*`, `short-v1`, `insider-v1`, `events-v1`).
- Membership is "listed and active on 2026-09-17, ranked on SIP dollar volume ending 2023-12-29" (`cohort-v2.json:4`; `screen-v2.json:16-17`). So discovery is conditioned on survival to 2026 and on liquidity at the end of the selection window.
- The spike doc says discovery "runs 2021-06 → 2026-01" (`survivorship-spike-2026-10-20.md:41`). That contradicts the protocols. Under the actual windows, the years where Alpaca's inactive list *is* informative (2016–2021: 152–310 liquid dead names per year) are exactly the discovery years. That makes the free repair worth more than the spike concluded.
- Bars are requested without `asof` (`evidence.py:24-27` lists it; `providers.py:244-252` never sets it). A rebuild that includes delisted names would hit ticker reuse (the spike found FISV and SBNY reused).

**G5. No shared, incremental store; collection is one-shot and slow.**
- Every lane re-acquires its own bars with one request per symbol (`setups/runner.py:158-189`). A 1,000-name cube took about a day.
- Each live scan refetches 1y daily, 60d hourly and 10d 15m bars for about 180 symbols, roughly 540 paced requests (`market_data.py:197-239`). The TTL cache is dead code (`:72`).
- The evidence store holds 9.2 GB from 33 days, about 320 MB per trading day. Nothing is ever deleted (`market-data-evidence.md:60-67`), and the host disk is 96% used with 87 GiB free (measured).
- The research `.npz` caches strip the evidence references (`setups/runner.py:186-188`), despite `market-data-evidence.md:26-28`.
- Native scan records do not pin their input frames (`market-data-evidence.md:95-98`).

**G6. Research-only event data has no live path.** FINRA, EDGAR insider and historical Nasdaq-page fields exist only behind research CLIs (`cli/commands/alpha_short_volume.py`, `alpha_insider_filings.py`; no `agent/` or `data/` consumer). If a field family ever produced a finalist, it could not be put on a card without new daily ingestion and a parity test. Earnings consensus is today's vintage, and the time of the report is unknown (`alpha-data-augmentation-2026-10-18.md:23,66-69`).

**G7. Missing reference data.**
- **Sectors:** hand labels for the 159 live names; none for the 1,000- or 452-name cohorts. Group demeaning and sector-relative features are therefore impossible (`alpha-data-augmentation-2026-10-18.md:26,62-65`).
- **Size:** there are no shares outstanding, so no market cap or turnover.
- **Costs:** there is no per-name spread or cost. The cube uses a flat 5 or 15 bp (`cube.py:86`).

**G8. Avoidable coverage losses.**
- FINRA history before 2018-08 is not read: only the CNMS file is fetched, never the earlier per-facility files (`short_volume.py:42`).
- Identity breaks blank out pre-rename history instead of mapping it through the `name_change` chain (`events.py:9-11`; 98 breaks on v3).
- News `updated_at` is dropped (`data/news.py:37`; `research/news.py:161-163`), so headline revisions are invisible to a future news study.

**G9. Dynamic names come from attention screens and are not replayable.** Most-actives by trade count and the top movers (`dynamic_universe.py:317-339`) select high-attention names. No historical screener record exists, so these names get no base rate, no cohort membership and no corporate-action coverage. They are the one third of the flow that `enforce` cannot see.

**G10. Config trap.** The code default `alpaca_feed="sip"` (`config.py:689`) combined with `end=None` (`providers.py:195-200`) would hit the recent-SIP 403 on the Basic plan, and the scan would then fall back silently to yfinance (G3). A bare `AppConfig()` gets that default; `load_config` overrides it with IEX.

## 7. Improvement opportunities

**I1. Move native scans to delayed SIP on a session-anchored clock (free).**
- Fetch SIP with `end = now − 16 min`. The Basic plan allows it (`research-data-sources.md:11-12,25-27`), and the PEAD path already does it in production (`copilot.py:397-401`).
- Build 1h and 4h bars from regular-hours data anchored at the open, using `build_session_bars` or an hourly equivalent, and drop the bar still forming. Synthesize today's daily bar from completed hours, exactly as `replay.py:206-266` does.
- Effects:
  - G1 closes: live, replay, base rates and `card_stats` all see one series.
  - The replay parity gap for the bar still forming disappears.
  - Liquidity gates can use absolute consolidated dollars, consistent with cohort v3's $5M floor.
- Keep IEX only for latest-trade checks. This is a semantics change, so it needs fresh replay base rates.

**I2. Use split-adjusted indicator frames live and in replay.** Use `adjustment="split"` for indicators, ATR and covariance, and keep raw prices only for price floors (the pattern in `cohort.py:205-211`). Capture structural corporate actions (splits, mergers and the like) **market-wide**: about 40 per 10 days (`corporate-actions.md:41`), well under `max_actions`. Keep dividends per universe. Effect: removes the split-driven false trends and volatility spikes, and covers dynamic, PEAD and operator names.

**I3. Fail closed on the data contract live.**
- Disable yfinance for equities in scans, or tag every frame's provider in the scan journal and exclude fallback frames from gates.
- Replace `fetch_latest_price` for ratchets and taps with an Alpaca snapshot (trade, quote and timestamps) and a maximum age.

**I4. A research data warehouse with a nightly incremental refresh** (outside scan windows, digest per day):
- delayed SIP daily and hourly bars for all listed common stocks above a low dollar-volume floor, using multi-symbol requests and pagination;
- Alpaca asset-master and Nasdaq-directory snapshots, so a point-in-time membership record starts accruing;
- the FINRA daily file;
- the EDGAR daily index (Form 4, 8-K item 2.02, Form 25 and Form 15);
- market-wide corporate actions.

Protocols would pin a slice digest. Effects:
- New cohorts, cubes and labels take minutes instead of a day.
- Live and research read the same store, so G5 closes.
- A finalist can be confirmed *prospectively* rather than only on the single historical confirmation window.
- The operator's "reasonable cadence" for mining becomes a data refresh plus scheduled fixed-set re-scoring.

Qlib's point-in-time provider design (`period` and `date` keyed) is a useful template.

**I5. Survivorship-corrected cohort v4.** Use Alpaca's inactive list for 2016–2021, EDGAR Form 25 and Form 15 for 2022 onward, and `asof`-bound bar requests. Effect: honest, lower long base rates, and less inflation of reversal-type edges, which benefit most from dead losers being absent.

**I6. Reference and cost data (all free).**
- SIC codes from the EDGAR submissions JSON, mapped to Fama-French 12 or 49 industries (Ken French's SIC map). This unlocks `cs_rank` and `group_demean` and industry momentum (Moskowitz–Grinblatt 1999).
- Shares outstanding from XBRL `dei:EntityCommonStockSharesOutstanding` with `filed` dates, giving size and turnover.
- Abdi–Ranaldo (2017) or Corwin–Schultz (2012) spread estimates from the daily bars already held, giving per-name `r_cost`.
- Ken French's daily FF5 and momentum factors for residual labels and for card attribution beyond SPY.

**I7. Cheap coverage repairs.**
- Read the pre-2018 FINRA per-facility files and sum them, recovering about 500 discovery sessions.
- Carry fields through `name_change` chains instead of blanking pre-rename history.
- Keep `updated_at` on news items.
- Store forward-return and benchmark-residual arrays in the cube, so new labels need no rebuild.

**I8. Shared adapters for live ingestion.** Run the research parsers (`short_volume.parse_file`, `read_transactions`) daily in a daemon worker. Add a parity test that the live D-1 value equals the cube value. That is a prerequisite for any probe from those lanes.

**I9. Evidence retention.** Gzip page files after N days, keep the manifest, result and hashes forever, and archive monthly.

## 8. Open questions

- Does Alpaca return a partial daily bar for today when the SIP `end` falls mid-session? (This decides whether I1 must synthesize it, as replay does.)
- Does `asof` actually map a reused ticker back to the delisted entity (for example SBNY with `asof=2023-03-01`)?
- Do Alpaca paper fills reference the SIP NBBO? That decides which feed card outcomes should be labelled on.
- How many scans had a universe or dynamic name with a split inside its 1y or 60d window? The magnitude of G2 is unmeasured.
- Does the Basic plan include historical SIP quotes, and indicative options history, for spread and options lanes?
- Is the 15m fetch on every scan needed? It accounted for about 28% of captures on 2026-10-20.
- Is production's `ALPACA_DATA_FEED` deliberately IEX? The roadmap shows the operator chose IEX evaluation without a "silent live feed switch" (`alpha-roadmap.md:1038-1043`), but not a decision to keep scans on IEX.
