# Pooled insider lane: SEC Form 4 open-market insider trades on the cached cubes

Date: 2026-10-20. Status: design, written under the operator's standing design trust (2026-10-09)
after the operator chose this source on 2026-10-20 ("sure EDGAR sounds reasonable"); the
data-augmentation assessment of 2026-10-18 ranks it second, after the FINRA short-volume lane, which
closed the same day with no finalist ([results](../../alpha-pooled-short-v1-2026-10-20.md)). Research
only. Nothing here touches the registry, broker, Telegram or promotion.

## 1. Goal and non-goals

Test whether open-market insider buying and selling carries a pooled edge on the existing
long-bracket label cubes, using the same frozen cohorts, cubes, windows, gates and checks as the
events and short-volume lanes. The lane adds one new, free, point-in-time source and changes only
what a formula may read: six dimensionless per-symbol, per-session **insider fields** derived from the
SEC's structured *Insider Transactions Data Sets* (quarterly archives of every Form 3/4/5 filed on
EDGAR, `{YYYY}q{N}_form345.zip`, published since 2006q1), acquired once into a digest-pinned private
cache by a new research-only command.

Why this source (assessment §2, item 2): it is free, every filing carries EDGAR's own filing date (a
Form 4 is due within two business days of the trade; 61% of purchases in 2025q1 were filed within two
days, the rest are late filings that are news when filed), the 2025q1 archive alone covers 406 of
cohort v2's 452 names (the rest are foreign private issuers not subject to Section 16), every cohort
symbol maps to an issuer CIK through the SEC's `company_tickers.json`, and the literature prior is
direct and long-only: aggregate insider purchases predict positive abnormal returns over the following
months, purchases are informative where sales are not (Lakonishok and Lee 2001; Jeng, Metrick and
Zeckhauser 2003), *opportunistic* (non-routine) trades carry the information (Cohen, Malloy and
Pomorski 2012), and purchases by several insiders in a short span (cluster buys) are the strongest
(Alldredge and Blank 2019). The probe of 2025q1 found purchases rare on mega caps (79 of 452 cohort-v2
names had an open-market purchase in the quarter) and sales common, so the buy-side fields are sparse
by construction and the formulas condition on a purchase being present (§5).

Non-goals (rulings, recorded for the PR):

- **Structured data sets, not per-filing XML.** The quarterly archives (`SUBMISSION`,
  `REPORTINGOWNER`, `NONDERIV_TRANS` tables) are the SEC's own normalisation of the filings, hold the
  same fields the XML holds, and are 47 downloads instead of two million. Their lag (a quarter is posted
  a few weeks after it ends; 2026q3 is already posted) is irrelevant to a backtest whose confirmation
  window ends 2026-07-31. A live feed would read EDGAR's daily index instead; that is a finalist's own
  contract, not v1's.
- **Form 4 non-derivative open-market trades only.** Transaction codes `P` (open-market or private
  purchase) and `S` (open-market or private sale) from the non-derivative table of original Form 4
  filings. Amendments (`4/A`) restate an original and would double count; Form 3 reports holdings, not
  trades; Form 5 reports exempt and late trades once a year. Option exercises (`M`), awards (`A`), tax
  withholding (`F`), gifts (`G`) and the rest are not open-market decisions and are excluded.
- **Officers and directors.** A filing counts when at least one reporting owner's relationship contains
  `Officer` or `Director`; filings by ten-percent-owner-only or `Other` owners (funds, trusts) are
  dropped. Trades flagged as made under a Rule 10b5-1 plan (`AFF10B5ONE = 1`, a column populated from
  2023) are dropped: a pre-scheduled trade carries no decision. Both rules are protocol parameters.
- **Issuer-level, by CIK.** A cohort symbol is matched to filings by its issuer CIK from
  `company_tickers.json` (the archives' `ISSUERTRADINGSYMBOL` is free text: `NYSE:FLG`, `(SIRI)`,
  lower case, `NONE`). Share classes that share a CIK (GOOG/GOOGL, FOX/FOXA, NWS/NWSA, LBTYA/LBTYK,
  Z/ZG) each receive the issuer's insider activity; the security title is not parsed. The mapping is
  the current one; identity breaks use the events lane's rule (§3).
- **Windows by filing date, not trade date.** A trade becomes information when it is filed; a late
  filing is news on its filing date. The trade date is recorded and used only to drop rows whose trade
  date follows the filing date (data errors).
- **No label, bracket, cohort, window, gate, bootstrap or stage change.** Both cubes (cohort v2, spec
  identity `f1c835a3d3364d2b…`; cohort v3) must be cache hits.
- **No live contract.** Live scans have no insider frames; a finalist (if any) would need its own feed
  contract before any registry step.

## 2. Acquisition: `alpha insider-filings`

`agentic_trader/research/insider_filings.py` (new) and `cli/commands/alpha_insider_filings.py` (new):

```
copilot alpha insider-filings --from 2015q1 --through 2026q3 --cache DIR --output DIR
```

- **SEC fair-access policy.** The SEC refuses requests without a declared `User-Agent` of the form
  `"<name> <contact e-mail>"` (verified 2026-10-20: a UA without an e-mail address answers 403). The
  command reads the string from the environment variable `EDGAR_USER_AGENT` and refuses to start
  without it (`ClickException("EDGAR_USER_AGENT is required: the SEC's fair-access policy needs a
  declared contact")`). The manifest records `user_agent_set: true`, never the value (it holds an
  e-mail address). Requests are paced at most 2 per second (the policy allows 10), through a bounded
  `httpx.Client` (timeout 60 s, 64 MiB byte budget per archive, streamed), idempotent GETs only, one
  retry on a transport error, and only to `www.sec.gov`.
- **Index first.** One `GET https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets`
  resolves each quarter's archive URL (the path prefix changed in 2026: `/files/structureddata/data/…`
  through 2026q1, `/files/datastandardsinnovation/data/…` from 2026q2), so the command never guesses a
  path. A quarter in `[from, through]` with no link fails the acquisition closed. The `--from`/`--through`
  values are quarters (`YYYYqN`); the window must hold at least one quarter.
- **Ticker map.** One `GET https://www.sec.gov/files/company_tickers.json` saved as
  `DIR/company_tickers.json` (+ `.meta.json`); it is part of the digest (below) so the symbol-to-CIK
  mapping a protocol was frozen with is the one every later build uses.
- **Cache layout.** `DIR/{YYYYqN}_form345.zip` plus `DIR/{YYYYqN}_form345.meta.json` (`url`,
  `requested_at`, `received_at`, `status`, `bytes`, `sha256`), written directly into `--cache DIR`
  (the same `DIR` the pooled commands take as `--insider-filings`); a cached archive is never
  refetched. Each archive is validated on acquisition: a well-formed zip holding `SUBMISSION.tsv`,
  `REPORTINGOWNER.tsv` and `NONDERIV_TRANS.tsv` whose header rows carry the columns §3 reads; an
  archive failing validation is kept on disk for evidence and fails the acquisition. There is no
  `absent` state: every quarter since 2006q1 is posted.
- `output/manifest.json`: window, quarters, per-archive digest lines, the **files digest** = SHA-256 of
  the newline-joined sorted lines `"<quarter>:<sha256>"` for every archive plus
  `"tickers:<sha256>"` for `company_tickers.json`, byte totals, `environment`
  (`research_environment()`), `command` (the argv), `user_agent_set`. The loader and the campaign's
  pre-ledger check apply the same digest rule, and the pre-ledger check reads every archive body
  against its meta file. Research only: no database, broker or Telegram.

Expected scale: 47 archives (2015q1 → 2026q3), 8–20 MB each, about 600 MB on disk, under 5 minutes.
2015 is acquired so that the 126-session and one-year look-backs (§3) are complete on 2016-08-01, the
first discovery session: unlike the short-volume lane, every discovery block carries a score.

## 3. Fields (`insider_fields_v1`)

All fields are per symbol, indexed by NYSE trading day `T` (the cube build's full `trading_days`
calendar), and hold the state **known by the end of T**: a filing with EDGAR filing date `F` is
visible on every `T ≥ F`. EDGAR assigns the next business day's filing date to a filing accepted after
17:30 ET, so a filing dated `F` is public by 17:30 ET on `F` at the latest, and
`evaluate_prepared`'s read of row `D-1` for decision session `D` (10:35 ET) is causal with at least
17 hours of slack. A filing dated on a non-session (EDGAR accepts on weekdays that are exchange
holidays) is visible from the next session. The builder reads the cached archives from disk only.

**Transactions.** The loader (`load_insider_filings`) joins the three tables per quarter and keeps, for
the cohort's issuer CIKs only, one row per qualifying non-derivative transaction:
`(symbol, filing_date, trans_date, code ∈ {P, S}, dollars, owner_cik)` where
`dollars = TRANS_SHARES × TRANS_PRICEPERSHARE`, both finite and positive; `DOCUMENT_TYPE == "4"`;
`TRANS_ACQUIRED_DISP_CD` is `A` for `P` and `D` for `S` (the 28 contrary rows in 2025q1 are dropped);
the accession has a reporting owner whose `RPTOWNER_RELATIONSHIP` contains one of the protocol's
`owner_roles` (`Officer`, `Director`); `AFF10B5ONE != "1"` when the protocol's `exclude_10b5_1` is
set; `trans_date ≤ filing_date`. `owner_cik` is the `RPTOWNERCIK` of the first qualifying owner on the
accession (a joint filing by two officers counts once). Rows from accessions filed by the same owner
on the same day for the same issuer are kept separately (they are separate trades). Symbol mapping:
`company_tickers.json` ticker upper-cased with `-` → `.` equals the cohort symbol; a cohort symbol
without a CIK is listed in coverage and has no rows. Identity breaks are the events lane's rule,
verbatim: the protocol pins the cohort's corporate-actions acquisition, `identity_breaks` (imported from
`events.py`) gives each symbol's latest `name_change` break, and every field is `NaN` on
`T < break_on` (the CIK does not change on a rename, so no rows are discarded; the pre-break history is
hidden because the symbol did not carry it).

Let, per symbol, `P$(w, T)` be the sum of purchase dollars over filings with
`filing_date ∈ (T − w sessions, T]`, `S$(w, T)` the same for sales, `DV_T` the 20-session median raw
dollar volume ending at `T` inclusive (`median_dollar_volume` over the cohort's raw bars, the same statistic
the cube's eligibility rule reads at `D-1`), and `covered_T` true
once the issuer has **any** qualifying Form 4 transaction filed on or before `T` in the loaded window
(the symbol's coverage start). Protocol parameters: `buy_window` 63 sessions (one quarter),
`net_window` 126 (six months, Lakonishok and Lee's horizon).

| Field | Definition at T | Unit |
| --- | --- | --- |
| `insider_net_ratio` | `(P$(126) − S$(126)) / (P$(126) + S$(126))`; `NaN` when the denominator is 0 or before coverage | dimensionless, in [−1, 1] |
| `insider_buy_rel` | `P$(63) / DV_T`; `0` when covered with no purchase in the window; `NaN` before coverage or when `DV_T` is missing or 0 | dimensionless |
| `insider_sell_rel` | `S$(63) / DV_T`; the same rules | dimensionless |
| `insider_buyers` | distinct `owner_cik` with a purchase filed in `(T−63, T]`; `0` when covered with none; `NaN` before coverage | count |
| `insider_buy_age` | sessions since the most recent filing date with a purchase (`0` on that session); `NaN` when no purchase has been filed yet | sessions |
| `insider_opp_buy_rel` | `P$(63) / DV_T` over **opportunistic** purchases only: a purchase is routine when the same owner filed a purchase in the same issuer in the same calendar month of the previous year (Cohen, Malloy and Pomorski's routine rule, reduced to one prior year because the data start in 2015); `0`/`NaN` as `insider_buy_rel` | dimensionless |

Rulings: windows are in sessions and inclusive of `T`, so a filing dated `T` counts on `T` (visible by
17:30 ET, read at 10:35 ET the next session); the dollar-volume denominator is the eligibility rule's own statistic computed from the raw bars the build
already loads for the events and short-volume lanes, so it is comparable across names and lanes; zeros (covered, no activity) are real
values, not missing, so a formula may rank "no selling" or condition on `insider_buy_rel > 0`;
`NaN` before coverage keeps never-filing names (foreign private issuers) out of every decile, as the
events lane keeps names without an earnings calendar out; the opportunistic rule uses filing months on
both sides. Coverage (`InsiderCoverage`): symbols without a CIK, symbols without any qualifying
transaction, rows dropped by each rule (document type, code, owner role, 10b5-1, contrary
acquired/disposed code, non-positive price or shares, trade after filing), rows for CIKs outside the
cohort (`unscored_rows`), per-symbol coverage start and first finite session per field, identity breaks.

## 4. Protocol: `pooled-insider-campaign`

`InsiderCampaignProtocol(CampaignProtocol)` in `research/pooled/campaign.py`, dispatched by
`load_campaign_protocol` on `id`, mirroring `ShortVolumeCampaignProtocol`:

```json
"id": "pooled-insider-campaign",
"score_inputs": "daily",
"insider_inputs": {
  "fields_version": "insider_fields_v1",
  "insider_filings": {
    "source": "sec-form345-datasets",
    "files_sha256": "<the acquisition manifest's files digest>",
    "quarters_from": "2015q1", "quarters_through": "2026q3",
    "buy_window": 63, "net_window": 126,
    "owner_roles": ["Officer", "Director"], "exclude_10b5_1": true
  },
  "corporate_actions": { "source": "alpaca", "content_sha256": "<cohort acquisition>", "window": [...], "trailing_sessions": 252 }
}
```

`search_mode: fixed_set` is required. `score_inputs` is `"daily"` and included in the dump for this
subclass; base-class, intraday, events and short-volume hashes are unchanged. The frozen formula
document and every manifest gain `insider_inputs` next to `score_inputs`, so an insider formula never
shares an identity with a formula of another lane. The runner fails closed when either digest does
not match the cache on disk, before any bar read or evaluation; the campaign command verifies both
before the ledger reserves anything. `excluded_families` is `cohort3-short-v1`'s list plus the eight
`short-v1` families (every earlier fixed set stays excluded).

Two frozen files, one per cached cube, everything else copied from the matching short-volume protocol:

- `config/research/pooled/insider-v1.json`: cohort v2, 5 bp, static-percentile universe, `short-v1`'s
  windows, gates, bootstrap, power and null seeds.
- `config/research/pooled/cohort3-insider-v1.json`: cohort v3, 15 bp, the $5M floor,
  `cohort3-short-v1`'s values.

CLI: `alpha pooled power|null-check|campaign PROTOCOL --cache DIR --insider-filings DIR
--corporate-actions ACTIONS_JSON` (`--insider-filings` is the acquisition's cache directory; the two
options are required for an insider protocol and refused for any other; `--earnings-pages` and
`--short-volume` are refused for it). Manifests record the digests, not the paths.

## 5. Frozen fixed set `insider-v1` (eight formulas, dimensionless)

The lane buys the top decile of the score each session, so each expression is signed to select the
names the hypothesis says will **outperform**. Windowed formulas divide by their condition (`x / (cond)`
is `x` in the window and `NaN` out of it, the events-lane ruling), so an out-of-window cell is
ineligible and the decile is taken among in-window names only. Because purchases are sparse, every
buy-side formula conditions on a purchase being present: without the condition most names score `0`
and the decile would be filled by hash-key ties.

| Family | Expression | Hypothesis (literature) |
| --- | --- | --- |
| `net_buying` | `insider_net_ratio` | Lakonishok and Lee's net purchase ratio: issuers whose insiders are net buyers over six months outperform |
| `buy_pressure` | `insider_buy_rel / (insider_buy_rel > 0)` | among names with a purchase this quarter, the largest buying relative to the name's own turnover |
| `opportunistic_buying` | `insider_opp_buy_rel / (insider_opp_buy_rel > 0)` | Cohen, Malloy and Pomorski: non-routine purchases carry the information |
| `cluster_buying` | `insider_buy_rel / (insider_buyers > 1)` | Alldredge and Blank: two or more insiders buying in one quarter |
| `fresh_purchase` | `-insider_buy_age` | the most recently filed purchase first (the information is freshest) |
| `recent_buy_pressure` | `insider_buy_rel / (insider_buy_age <= 10)` | buying relative to turnover, only where a purchase was filed within two weeks |
| `buying_into_weakness` | `insider_buy_rel / (ts_sum(returns, 20) < 0)` | Jeng, Metrick and Zeckhauser: contrarian purchases after a decline are the profitable ones |
| `light_selling` | `-insider_sell_rel / (insider_sell_rel > 0)` | the declared weak prior: among names with insider sales, the lightest relative to turnover (sales are mostly liquidity-driven and uninformative) |

Every row is dimensionless (the six `insider_*` names join `FIELD_UNITS` as `(0.0, 0.0)`; `returns`
is dimensionless); the pin test verifies each under `require_dimensionless`. The two frozen files'
SHA-256 prefixes are pinned in `tests/research/pooled/test_insider_v1.py` and recorded in the results doc.

## 6. Implementation shape

- `research/insider_filings.py` (new): `InsiderFilingStore(directory)` (`fetch_quarter`,
  `load_quarter(quarter) → bytes | path`, `meta(quarter)`, `load_tickers()`), `resolve_archive_urls(index_html)
  → dict[quarter, url]`, `acquire_insider_filings(client, quarters, store, *, pace, user_agent) → manifest`,
  `files_sha256(store, quarters)`, `Quarter` parsing (`YYYYqN`, ordering, range), `read_transactions(zip_path,
  *, ciks, owner_roles, exclude_10b5_1) → tuple[InsiderTransaction, ...]` plus per-rule drop counts
  (csv module, `csv.field_size_limit` raised: `FOOTNOTES.tsv` is not read).
- `research/pooled/insider_fields.py` (new): `INSIDER_FIELDS = ("insider_net_ratio", "insider_buy_rel",
  "insider_sell_rel", "insider_buyers", "insider_buy_age", "insider_opp_buy_rel")`,
  `load_insider_filings(directory, inputs, symbols) → LoadedInsiderFilings (by_symbol, files_sha256,
  quarters, rows, unscored_rows, drops, symbols_without_cik)` (disk only, digest verified against the
  meta files), `build_insider_frames(inputs, loaded, actions, raw, trading_days, symbols) →
  InsiderFrames(frames, coverage)` (`raw` is the cohort's raw daily bars, as for the short-volume builder); imports `identity_breaks` from `events.py`. Window sums are
  computed per symbol from a per-session dollar series indexed by filing date (filings on non-sessions
  roll to the next session), with `np.cumsum` differences for the two windows.
- `research/alpha/dsl.py`: the six names join `FIELD_UNITS` as dimensionless.
- `research/pooled/scoring.py`: `score_book` dispatches on `InsiderCampaignProtocol` to
  `ScoreBook.with_fields(..., INSIDER_FIELDS, ...)`; `insider_inputs_document(protocol)` and
  `insider_coverage_document(built)` beside the short-volume ones.
- `research/pooled/runner.py`: `InsiderSources(insider_filings_dir, actions_file)`,
  `load_insider_sources`, `verify_insider_digests`, `build_cube_inputs(..., insider=None)` (exactly one
  of `events`, `short`, `insider`; the check generalised to count the given sources), `CubeBuild.insider`;
  raw bars are loaded for the insider frames as they are for the events and short-volume frames.
- `campaign_run.py`, `power.py`, `null_check.py`: `insider_inputs` in the frozen document and manifests;
  every `result.json` records `insider_coverage` (`null` for other protocols); the campaign result records
  `first_finite_session` per formula.
- `cli/commands/alpha.py`: `_pooled_field_sources` learns `insider_filings`; `--insider-filings DIR` on
  power, null-check and campaign.
- Acquisition first, outside scan windows, once for both protocols, with the operator's
  `EDGAR_USER_AGENT`; the cohort-v2 and cohort-v3 corporate-actions acquisitions are reused.

## 7. Checks, campaigns and closure (pre-registered)

Fixed set ⇒ checks **A** (power) and **C** (real-formula false acceptance) only, per protocol, on the
cached cube from a clean worktree at the merged revision, outside scan windows. A is formula-independent
and must replay the matching short-volume protocol's result on the same cube; C must show
`errors_total: 0`. Each campaign runs only with the operator's explicit approval (`--journal-scope
production/alpaca:paper`), charges each formula to the pooled ledger before scoring, and consumes the
lane-wide confirmation window only for finalists. A campaign ending `no_finalists` **or**
`no_confirmation_candidates` (a discovery survivor that fails selection, the short-volume lane's cohort-v3
outcome) on both cubes closes the insider fixed set on this window; nothing promotes. Results doc:
`docs/alpha-pooled-insider-v1-<date>.md`, with the coverage block, drop counts and first-finite sessions.

## 8. Tests (plan must cover)

Acquisition: refuses without `EDGAR_USER_AGENT`; the UA header is sent and never written to the
manifest; quarter parsing and range (`2015q1` → `2026q3` is 47 quarters; `2015q5` refused; `through`
before `from` refused); index resolution picks the right path per quarter and a missing quarter fails
closed; cache hit never refetches; a zip lacking a table or a header column is kept and fails; byte
budget; files digest covers the archives and the ticker map and changes when either changes; no network
outside `www.sec.gov`. Reader: only document type `4`; only `P`/`S` with the matching acquired/disposed
code; owner-role filter (ten-percent-owner-only dropped, `Director,Officer` kept); 10b5-1 rows dropped
when the flag is set and kept when the parameter is off; non-positive price or shares dropped; trade
after filing dropped; CIK-to-symbol mapping with a shared CIK fanning out to both classes; a cohort
symbol without a CIK reported. Fields: a filing dated `T` counts on `T` and not on `T−1`; a filing dated
on a holiday counts from the next session; `insider_net_ratio` `NaN` with no activity in 126 sessions and
`±1` with one side only; `insider_buy_rel` `0` once covered with no purchase, `NaN` before coverage and
with a missing dollar volume; `insider_buyers` counts distinct owners; `insider_buy_age` `0` on the filing
session, increments by session, `NaN` before the first purchase; the routine rule (same owner, same
issuer, same calendar month one year earlier) excludes a purchase from `insider_opp_buy_rel` and a
different month does not; identity break ⇒ `NaN` before the break. Conditional formulas `NaN` out of
condition through `evaluate_prepared`. Digest mismatch (archives, ticker map or actions) fails closed
before evaluation and before the campaign's ledger opens. Base-class, intraday, events and short-volume
hashes unchanged (pin test). Both frozen files parse, are dimensionless, freeze to identities distinct
from the same text under a short-volume protocol, and refuse `search_mode: genetic`. `--insider-filings`
refused for a non-insider protocol; `--short-volume` and `--earnings-pages` refused for an insider
protocol.
