# A priori entry `idday` v1: the investor-day run-up, development read

Date: 2026-11-19. Operator: "go ahead" (2026-11-19) on the frozen design after the feasibility check, including
the full SEC acquisition with the configured contact address (`EDGAR_USER_AGENT`) and one read of the 2016–2026
window. Design calls are mine under the operator's standing design trust.

## 1. Why, and what this read can decide

Cabrera, Kolokolova and Zhang (2025) study about 3,061 analyst and investor days in 2011–2022 and report a
buy-and-hold abnormal return of +3.70% from the announcement to the eve of the event. The desk has never tested
a pre-event drift that is not an earnings release, and an investor day is a scheduled event with a date known
weeks ahead, which suits a swing card.

The 2026-11-19 feasibility check (`~/agentic-trader-research/investor-day-feasibility-20261119`) found:

- **Benzinga history is no longer available.** Alpaca now returns no headlines before 2026-04-29, so SEC 8-K
  filings are the only free point-in-time source.
- **Rate and lead.** On 50 names, the 8-K text dates 34 of about 58 EDGAR-visible events at least two weekdays
  ahead. These are the hand-audited true events; the raw log counts 46 candidates, with leads up to 261. The
  median lead is 21 weekdays (p25 11, p75 30), and 4 of the 34 have leads above 63.
- **Precision.** Raw precision was 74%. A stricter wording rule, written after reading the cases and therefore
  in-sample, kept 36 of 37 true events and dropped 11 of 12 false ones.
- **Repeat hosts.** Annual hosts dominate: Intuit and Kinder Morgan supply 18 of the 34.
- **The earnings confound.** Many announcements sit inside earnings releases, so post-earnings drift
  contaminates the run-up.

The read runs on 2016–2026, which is development only for any new hypothesis (`docs/research-window-ledger.md`),
on today's survivors, with an event parser checked by hand. Every one of those biases favours the rule, so
**this read can kill the rule but cannot pass it.** The outcomes are:

- `failed`: the rule is closed on this source. A different parser, universe or window is a declared second
  look.
- `survives_development`: a separately specified prospective step becomes eligible. That would be either a live
  8-K capture feeding unvalidated paper cards, or a prospective read; the operator chooses. Nothing trades and
  no card is sent.
- `inconclusive_underpowered`: the planted-effect check (section 9) failed, so the read decides nothing.

## 2. Goals, non-goals, anti-goals

**Goals:**
- a research-only SEC acquisition, pinned by digest;
- a frozen event parser, validated by hand on names outside the feasibility sample before any price is read;
- a frozen catalog entry with its SHA-256, and the ledger row before the read;
- a study that is offline except for the trading calendar and the SPY bar read;
- the pass rule, with an earnings-matched control, a same-firm negative control and a planted-effect check;
- a results doc.

**Non-goals:**
- no card, probe, registry entry, shadow or promotion credit;
- no live 8-K capture;
- no Benzinga;
- no change to any live path.

**Anti-goals:**
- changing the parser, a threshold or a control rule after any outcome is read;
- reading outcomes after 2026-07-31;
- treating `survives_development` as permission to trade.

## 3. Population and pinned inputs

**Population.** The population is cohort v3 ∪ cohort xu: 2,156 names, ranks 1–2,156 of the screen-v3 ranking on
the 2026-10-15 snapshot.
- The two cohorts are disjoint and share one cube spec identity: 10:35 New York decision clock, ATR14, $10
  price, absolute $5M 20-session median dollar-volume floor, 15 bp decisive cost, bars 2016-01-01 → 2026-09-01.
- Investor days cluster in large caps, which sit in xu's ranks 1–400. Cohort v3 alone would carry roughly a
  third of the events.
- **Survivorship:** membership is today's. This is the same caveat as `runup` v1 and `insider` v2, and it is
  stronger for xu's small band.

**Pinned inputs.** The entry pins:
- `config/research/pooled/cohort3-events-v1.json` by SHA-256. It fixes cohort v3, the Nasdaq calendar page
  archive (2016-02-23 → 2026-07-31, `pages_sha256` 9273e40a…, market-wide, so it covers xu's names too) and the
  cohort-v3 corporate actions (`content_sha256` 8a547b06…).
- `config/research/pooled/cohort-xu-insider-v1.json` by SHA-256. It fixes cohort xu and its corporate actions
  (`content_sha256` 9f38dffd…). It is used only for its cohort, cube spec and actions; its insider fields are
  not read.
- Both cached cubes by content hash (`cube_sha256_v3`, `cube_sha256_xu`), checked before use.
- The investor-day acquisition (section 4) by `content_sha256`, and the event table (section 5) by
  `events_sha256`.
- The parser check (section 6) by `parser_check_sha256`.

**Assembly.** The runner loads cohort v3's cube as the run-up runner does (`build_cube_inputs` with
`require_cached`, `load_hourly` and the events protocol's sources). Cohort xu's protocol is an insider protocol
with no `event_inputs`, so its cube is loaded with `build_cube_inputs` without a field source, and its
corporate-actions file is checked directly against `insider_inputs.corporate_actions.content_sha256`. The Form
3/4/5 archive is not an input. It then merges them with a new `merge_cubes`, which requires an equal spec identity, equal
sessions and disjoint symbols. Symbols are concatenated, v3 first, and the merged identity is the SHA-256 of the
two cohort hashes joined by `|`.

**Market and calendar.** SPY daily bars come from the pooled cache. SPY hourly bars are fetched once after the
manifest exists, into a run-specific directory under `~/agentic-trader-research`, as in `runup` v1. The trading
calendar read is the only other provider access.

## 4. The investor-day acquisition (`alpha investor-days fetch`)

This is a research-only cached acquisition of SEC data. It requires `EDGAR_USER_AGENT`, which the operator
approved for this acquisition on 2026-11-19. The value is never logged or written; the manifest records only
that a contact was declared.

1. **Tickers and issuers.** SEC's `company_tickers.json` is fetched once and its digest recorded.
   - Unmapped cohort symbols are listed in the manifest and get no events.
   - Symbols that share a CIK (GOOG/GOOGL, FOX/FOXA, NWS/NWSA and others) form one issuer, searched once.
2. **Visibility.** For each CIK, SEC's submissions record (`data.sec.gov/submissions/CIK##########.json`) is
   fetched once. The issuer is `visible` if its recent filings include at least one 10-K or 10-Q: a domestic
   periodic filer, which must file 8-Ks. Foreign private issuers (6-K, 20-F, 40-F) are not visible, because their
   investor days would never appear as an 8-K.
3. **Search.** For each CIK, EDGAR full-text search (`efts.sec.gov/LATEST/search-index`) runs with:
   - the query `"investor day" OR "analyst day" OR "capital markets day" OR "investor meeting" OR "analyst meeting"`;
   - `forms=8-K` and a custom date range of 2016-01-01 → 2026-07-31;
   - every page fetched, capped at 1,000 hits per CIK, with capped CIKs counted.
4. **Documents.** For each hit whose file is `.htm`, `.html` or `.txt`, the document is fetched from
   `www.sec.gov/Archives` and cached gzip-compressed, keyed by accession and file name.
   - A 404 is counted and skipped.
   - A 403 stops the acquisition.
   - Other failures go through the shared EDGAR helpers (`data/edgar_http.edgar_fetch` with `TRANSIENT_RETRY`:
     one paced retry on a transport error, a 429 or a 5xx).
   - A failure that persists is counted in the issuer's `doc_failures`, which makes the issuer unclean, and the run
     continues. Malformed search or submissions payloads are handled the same way.
   - A progress line goes to stderr every 50 issuers. It never contains the user agent.
   - Requests are paced by `edgar_pacer()` at the repo's fair-access rate (`REQUESTS_PER_SECOND`, 2 per second).
     `efts.sec.gov` joins `EDGAR_HOSTS`.
5. **Output.** Output goes to `~/agentic-trader-research/investor-day-filings-<date>/`:
   - `hits.json`: per issuer, its CIK, its symbols, `visible`, whether the search was capped, and each hit's
     accession, file, filing date, items, file type and description;
   - `docs/`;
   - `manifest.json`: query, date range, counts, the ticker-file digest and failures by reason;
   - `content_sha256`, computed over `hits.json` and the sorted per-document digests.
6. **Cache reuse.** Re-running reuses cached documents and submissions records.

The acquisition reads no prices and spends no window. It runs outside 10:20–10:50 and 14:20–14:50 New York.

## 5. The frozen parser (`idday-parser-v1`)

The parser is `research/apriori/idday_events.parse_filings(acquisition_dir) -> EventTable`. It is deterministic
and offline. Its version string is `idday-parser-v1`, and any change to its rules is a new version.

1. **Text.** Script and style blocks are removed. The closing tags of `p`, `div`, `tr`, `td`, `th`, `li`, `h1`–`h6`,
   `table`, `ul`, `ol` and `blockquote`, and every `<br>`, become hard sentence breaks. A table cell therefore never
   pairs a keyword with a date in another cell. Remaining tags are stripped, entities unescaped, non-breaking
   spaces made plain, whitespace collapsed and the result stripped.
2. **Keyword.**
   `(investor|analyst)s?(\s*(and|&|/)\s*(investor|analyst)s?)?\s+day | capital\s+markets?\s+day | analyst\s+meeting | investor\s+meeting`,
   case-insensitive, with word boundaries. "Investor/Analyst Day" is one keyword.
3. **Sentences.** A boundary is `[.!?]` followed by whitespace and then a capital letter, a quote or `(`. A bullet
   character (`•`, `▪`, `●`, `◦`) is also a boundary, because earnings releases list highlights as bullets after a
   heading such as "Recent Business Highlights". A hard break from step 1 is always a boundary. Sentence-starting
   quotes are `"` and `“`. Each keyword occurrence is judged within its own sentence.
4. **Dates.**
   - Month names must be capitalised: the full name or its abbreviation (Jan…Dec, plus Sept), in title case or
     upper case, optionally followed by a dot.
   - The month name is followed by a day number, an optional ordinal suffix and an optional year. Numeric dates
     count only as `m/d/yyyy`.
   - A year-less date resolves to its next occurrence on or after the filing date minus 7 days.
5. **Announcement.** A sentence announces an event when all three hold:
   - (a) it contains an announcement phrase: `will|to|plans to|intends to|scheduled to` followed by
     `host|hold|be held|be hosted|be webcast|webcast|present|be presented|make presentations|provide|be provided|discuss`;
     or `save the date`, `join us`, `invite`, `schedule(s)`, `upcoming`, `more to come`, `to be held`,
     `in the coming`, `will be attending`, `previously disclosed` or `previously announced`. These match as whole
     words, except that `invite` also matches `invites`, as `schedule(s)` does;
   - (b) it contains none of these whole words or phrases: `attended`, `outlined`, `reiterated`, `articulated`,
     `unveiled`, `recent`, `last year`, `discussed at` or `presented at our`. Whole words means "recently" does
     not match `recent`;
   - (c) it contains a date within 200 characters of the keyword with `filed < date ≤ filed + 400 days`. The
     distance is measured from the keyword's start to the date's start.

   The date nearest the keyword is the announced event date.
6. **Event-day material.** A filing is event-day material when either holds:
   - a keyword sentence has no future date in the announcement sense (`filed < date ≤ filed + 400 days`) but
     contains one of:
     - "is/are hosting" or "is/are holding";
     - "hosted/held/hosting/holding" followed within 60 characters, with no period between, by "today";
     - "today's investor/analyst/capital markets", where the possessive is required;
     - a date in `[filed − 1, filed]`;
   - the hit's file description names the event and contains "presentation" or "slides".
7. **Events.** Per issuer (CIK), so a share-class pair yields one event:
   - The candidate dates are the issuer's announced dates plus the filing dates of event-day material. Candidates within
     3 calendar days of the previous kept one are merged into it.
   - Each candidate belongs to exactly the one event it was kept as or merged into. An event's announcing filings
     are the filings whose announced date belongs to it and that were filed before the event's kept date. Event
     ids are unique, and the parser asserts this.
   - `F` is the earliest announcing filing date, and `E` is the date that filing announced. That is the first
     announcement, point-in-time; a later reschedule is not used.
   - Events with no announcing filing are `event_only` and are counted but never become legs.
   - Each event records whether its first announcing filing lists Item 2.02 (descriptive only), its keyword, the
     sentence, the accession and the URL.
8. **Output.** `events.json` holds the event rows and `events_sha256`, the SHA-256 of the canonical JSON of the
   rows.

## 6. Parser check (before any price is read)

The check samples 60 events with seed 20261119, drawn from **candidate legs**. A candidate leg is an announced
event that passes the gates in section 7 that need no prices (steps 1–4: timing, window, overlap, earnings),
whose issuer has no symbol in the feasibility sample (`investor-day-feasibility-20261119/sample.json`). The bar
therefore measures the population that can become legs.
- **Labelling.** The controller (Claude) labels each event from its sentence and, where needed, the document. The
  operator is not in the loop. An event is **correct** if the
  sentence announces this registrant's own investor day, analyst day, capital markets day or analyst/investor
  meeting on the parsed date.
- **Saved.** The labels, with a one-line reason each, go to `parser_check.json` together with `events_sha256`
  and the sample's seed and size.
- **Gate.** The read proceeds only if errors ≤ 9 (precision ≥ 85%).
- **Failure.** If the check fails, the lane stops before any price read and the window stays unspent. A new
  parser is `idday-parser-v2`.
- **Enforcement.** The executor refuses to run without a passing `parser_check.json` for the pinned
  `events_sha256`.

A false event is a date with no news, so it dilutes the effect without biasing it against the controls. The
precision bar protects power, not validity.

## 7. Legs

For each announced event `(symbol, F, E)`:

1. **Timing.**
   - `D` is the second session strictly after `F`.
   - `X` is the last session strictly before `E`.
   - `H` is the number of sessions in `[D, X]`, which must satisfy `1 ≤ H ≤ 63` (`lead_too_short`,
     `lead_too_long`).

   Entering on the second session puts the earnings reaction (pre-market on `F`, or after the close on `F`)
   inside the close of `D − 1`, so the match return in section 8 contains it. The first session after the
   filing is given up for that.
2. **Window.** `D` must lie in 2016-08-01 → 2026-06-30 and `X ≤ 2026-07-31`, the end of the calendar archive
   (`outside_window`).
3. **Overlap and issuer.** One leg per issuer per 63 sessions: a later event whose `D` is within 63 sessions of
   an earlier candidate leg's `D` is excluded (`overlapping_event`).
   - A candidate leg is an event that passes steps 1, 2 and 4.
   - The rule is applied last among the price-free gates, in `D` order, keeping the first.
   - An earlier event that later fails a price gate (steps 5–7) still blocks, because the price-free gates
     cannot know it will fail.
   - Every price-free gate runs on one calendar: the merged cube's sessions. The parser-check sample and the
     study therefore see the identical candidates. An event is `outside_window` when any session it needs lies
     outside that calendar: `F`'s session, the start of its 3-session earnings band (so `F` on either of the
     first two sessions), `D`, or `X` (so `E` after the last session).
   - The leg trades the issuer's cohort symbol with the higher dollar volume at `D` among those eligible at `D`,
     ties broken by `tiebreak`. If none is eligible, the event is excluded (`not_eligible`).
4. **Earnings**, judged on the issuer's archive rows (the union over its cohort symbols, deduplicated by date).
   - An archive report date in `[F + 1, X]` excludes the event (`earnings_in_hold`).
   - The event's class is `with_earnings` when the symbol has an archive report date in the 3-session band
     ending at `F`'s session (the last session ≤ `F`), and `standalone` otherwise.
5. **Corporate actions.**
   - An identity break on or after the session 60 sessions before `D` excludes the event (`identity_break`).
   - A structural corporate action within 5 calendar days of `[D, E]` excludes it (`structural_action_nearby`).
6. **Eligibility.** The cube's eligibility at `D` must hold (`not_eligible`).
7. **Inputs.**
   - ATR(14) on adjusted bars as of `D − 1` (`no_atr`).
   - The 20-session return to `D − 1` (`no_return`).
   - The dollar-volume tercile at `D` among the merged universe's eligible names (`_buckets`).

## 8. Trade, labels and controls

**Trade.** LONG only.
- Decision at 10:35 New York on `D`.
- `label_bracket` on the cached regular-session hourly bars, with a `BracketArm` whose `target_r` is `None` and no
  trail.
- The labeller slices hourly bars from the decision through the close of the row's last allowed session, plus a
  margin. It does not use the cube's 45-calendar-day `_LABEL_SPAN`, which would make holds beyond about 30
  sessions `immature`. Its cache is keyed by `(symbol, D, H)`.
- Stop 2 × ATR(14) below the decision price.
- Time exit at the close of the `H`-th session, `X`.
- Costs 5, 15 and 25 bp per side; 15 bp decides.

**Exclusions** are the run-up's, by reason, year and role: `no_hourly_bars`, `missing_session_bars`,
`no_decision_price`, `degenerate_levels`, `immature` and `hold_outside_window`. The last means a fill not on `D`,
or an exit after the close of `X` for a leg, or after the close of the `H`-th session for a control or placebo.

**Excess return.** Each labelled row gets `net_excess_r_v1` through the PEAD base-rate helpers: `trailing_beta`,
`name_cost_bps`, `market_r` on SPY hourly bars over the row's own hold, and `net_excess_r`. A missing input gives
`NaN`.

**Controls.** Each leg gets 5 controls on `D`. Each control is a merged-universe name that is:
- eligible at `D`, in the leg's dollar-volume tercile, not a leg on `D`, and not of the leg's issuer;
- of a `visible` issuer whose search was not capped and whose documents fetched without failure;
- free of any raw full-text search hit of its own (any matched 8-K, parsed or not) filed in `[D − 63, X + 10]`
  sessions, and of any parsed event date in that span;
- clear of earnings according to the leg's class:
  - `standalone`: no archive report date in `[D − 30, X]`, and archive coverage of at least one report in the
    130 sessions before `D` (the control issuer's archive rows, as in section 7);
  - `with_earnings`: an archive report date in the same 3-session band ending at `F`'s session (a reporting
    peer), and none in `[F + 1, X]`.

Controls are chosen nearest in 20-session return to `D − 1` (a return that contains the earnings reaction),
with ties broken by the cube's `tiebreak`. Each is labelled with the leg's bracket and hold `H`.
- Fewer than 5 candidates leaves the leg unpaired, counted by year and class.
- The control row is the mean of the 5, and the per-cell outcomes are saved.

## 9. Pass rule, negative control and planted effect

**Bootstrap.** All tests use a stationary session-block bootstrap over decision sessions (`statistics.mean_test`,
`paired_test`, `difference_in_paired_test`): block mean 10, 2,000 draws, seed 20261120. ci90 is the
[5th, 95th] percentile pair.

**Rules.**
- **P1:** the legs' mean `net_excess_r_v1` > 0, with ci90 lower bound > 0.
- **P2:** the mean `r_cost` (15 bp) of paired legs minus the mean of their controls > 0, with the paired ci90
  lower bound > 0. Pairs are keyed by `D`.
- **P3:** all three must hold:
  - the 1%-trimmed mean of `net_excess_r_v1` > 0;
  - its mean over decisions from 2023-01-01 > 0;
  - P2's statistic has a ci90 lower bound > 0 under an issuer-cluster bootstrap (new
    `statistics.cluster_paired_test`: issuers resampled with replacement, 2,000 draws, seed 20261121). An annual
    host can contribute ten legs, and the result must not rest on a few hosts.
- **P4:** at least 250 legs with a finite `net_excess_r_v1`, at least 60 of them from 2023-01-01.
- **P5:** the legs' paired difference minus the placebos' paired difference > 0, with the ci90 lower bound > 0,
  from one joint session-block bootstrap over the union of their decision sessions (same seed).

**Negative control.** Every leg gets one pseudo-event of the same symbol with the same hold `H` and the same
class, drawn with seed 20261122.
- **`with_earnings`:** one of the symbol's other archive reports `r′` within 252 sessions of `F`, outside the
  leg's band. The draw requires:
  - no raw search hit or parsed event of the issuer with a filing or event date in `[r′ − 5, X′ + 10]` sessions;
  - no report in `[r′ + 1, X′]`.

  `F′` is `r′`'s session, `D′` the second session after it and `X′` the `H`-th session from `D′`. This is the
  same firm's ordinary earnings release with no investor day.
- **`standalone`:** a session `D′` within 126 sessions of `D`, with no archive report in `[D′ − 30, X′]` and no
  raw search hit or parsed event of the issuer in `[D′ − 63, X′ + 10]`.

Draws must fall in the decision window, with `X′ ≤ 2026-07-31`. A second draw of an existing `(symbol, D′)` is
dropped (`duplicate_session`). Pseudo-events pass through the identical gates (section 7, steps 5–7), labels,
controls and statistics. The placebo's P2 must **not** hold, and P5 must hold.

**Planted effect.** Before the legs' statistics are computed, every paired placebo leg gets a planted return of
half the published effect: 0.0185 of the decision price, converted to R per row
(`0.0185 × decision_price / risk_unit`). It is added to both `r_cost` and `net_excess_r_v1`, and P1 and P2 run on
that frame.
- Both must hold, or the decision is `inconclusive_underpowered`.
- `planted.json` records the result and the placebo P2's minimum detectable effect,
  (1.645 + 0.84) × its bootstrap standard deviation. It is written before `result.json`.

**Decision.**
- `inconclusive_underpowered` if the planted check fails;
- otherwise `failed` if any of P1–P5 fails or the placebo's P2 holds;
- otherwise `survives_development`.

No parser rule, threshold, lead, stop, cost, window or control parameter is revisited after the run; any change
is a new version.

## 10. Descriptive diagnostics (decide nothing)

- **Splits:** legs by class (`with_earnings` vs `standalone`, the key split), by year, by cohort band (v3, xu
  large, xu small), by keyword, by hold-length tercile and by dollar-volume tercile.
- **Concentration:** legs excluding the five symbols with the most legs, and the first leg per symbol only.
- **Without the stop:** the buy-and-hold excess from the decision price to the close of `X` minus beta × SPY over
  the same span.
- **Trade mechanics:** the stop/time-exit split and the mean hold; costs 5 and 25 bp; the placebo's P1.
- **For a later power plan:** the per-leg standard deviation of `net_excess_r_v1` and the session design effect.
- **Pipeline counts:** events, `event_only` events, exclusions by reason, symbols with at least one leg, the Item
  2.02 share and acquisition coverage (unmapped and capped CIKs, failures).

## 11. Code and artifacts

- `research/investor_days.py` and `cli/commands/alpha_investor_days.py`: the `fetch` acquisition (section 4) and
  `events`, which parses into `events.json` and writes the seeded parser-check sample, `parser_sample.json`.
- `research/apriori/idday_events.py`: the parser (section 5), the parser-check sampler and validator, the leg
  gates, control candidates and placebo draws.
- `research/apriori/idday_study.py`: labels, controls, net excess, P1–P5, placebo, planted check, diagnostics and
  the executor.
- `research/apriori/idday_runner.py`: input assembly. It checks both protocols, both cohorts, both cubes, pages,
  both action sets, the acquisition, events and parser-check digests; the merged cube is built with
  `research/pooled/cube.merge_cubes`. It also checks two things:
  - the acquisition covers exactly the merged cube's symbols (every issuer's symbols plus the unmapped ones);
  - every hit's filing date lies in the entry's filing range.
  It runs the cheap digest checks before loading any price bars, and it refuses a `last_exit` after the archive
  end.
- `research/apriori/statistics.cluster_paired_test`.
- `research/apriori/catalog.py`: `IddayEntry` (`id: "idday"`), `load_idday_entry`, and validation (decision window
  inside the cube spec's decisions; `X` bound ≤ the archive end).
- `config/research/apriori/idday-v1.json`, frozen after the acquisition and the parser check, before any price
  read.
- `alpha apriori-study` dispatches on `id: idday`, with `--investor-days DIR`, `--parser-check FILE`, the
  existing `--corporate-actions` (cohort v3's file) and a new `--corporate-actions-xu`. Validation requires both
  for `idday`, and the existing per-entry option checks are extended.
- **Executor outputs.** The executor writes `protocol.json` and `manifest.json` before any provider call, then
  `events.csv.gz`, `labels.csv.gz`, `controls.csv.gz`, `control_cells.csv.gz`, `placebo.csv.gz`, `planted.json`
  and `result.json`.
- **Failures.** Every failure is `status: failed` with the exception, and `authorizes_promotion` is always false.
- Run output goes under `~/agentic-trader-research/apriori-idday-v1-<date>`, never into the repository.

## 12. Order of work, ledger and docs

1. Code on `feature/investor-day-leg`.
2. The acquisition, after hours.
3. `alpha investor-days events` and the parser check.
4. Freeze `idday-v1.json` with every digest.
5. Add ledger row L45 to `docs/research-window-ledger.md`: investor-day run-up `idday` v1, cohorts v3 ∪ xu,
   decisions 2016-08-01 → 2026-06-30, outcomes read, development only, cannot pass. Also add an α-table line and
   update the windows' read lists.
6. Commit at a clean revision, then run.
7. After the run, write:
   - `docs/apriori-idday-<date>.md`;
   - the `docs/apriori-alphas.md` catalog section;
   - a `CLAUDE.md` paragraph;
   - the ledger verdict;
   - the roadmap's pre-event section;
   - a correction to `docs/news-context.md`'s history depth: Alpaca returns nothing before 2026-04-29 as of
     2026-11-19.

The read and the acquisition avoid 10:20–10:50 and 14:20–14:50 New York.
