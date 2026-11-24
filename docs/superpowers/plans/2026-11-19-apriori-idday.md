# Investor-day run-up (`idday` v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the research-only SEC investor-day acquisition, the frozen `idday-parser-v1` and its hand-check
tooling, and the `idday` v1 development study (legs, earnings-matched controls, same-firm placebo, planted
check, P1–P5), dispatched by `alpha apriori-study`.

**Architecture:** This mirrors the built `runup` v1 study (`research/apriori/runup_{events,study,runner}.py`,
`RunupEntry`).
- A new acquisition module and its `alpha investor-days` CLI group (`fetch`, `events`, `sample`) feed a pure
  parser.
- The study runs on cohort v3 ∪ cohort xu through one merged `LabelCube`.
- Every price-free gate is one function (`candidate_legs`), shared by the parser-check sampler and the study, so
  the hand check measures exactly the population that can become legs.

**Tech Stack:** Python 3.14, `uv`, httpx (`MockTransport` in tests), pandas/numpy, pydantic v2, click, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-apriori-idday-design.md`. Read it; section numbers below refer to
it. The run-up spec `docs/superpowers/specs/2026-11-06-apriori-runup-design.md` and its code are the reference
implementation for every mechanic marked "as runup".

## Global Constraints

- Research only: no database, registry, broker, Telegram, card or promotion credit; `authorizes_promotion` is
  always `false`.
- `EDGAR_USER_AGENT` is read from the environment. Its value is never logged, printed or written to any file;
  manifests record only `"user_agent_set": true`.
- No POST/PATCH/DELETE anywhere. GETs go through `data/edgar_http.edgar_fetch` with `edgar_pacer()`
  (2 requests per second).
- No new migration (head stays `008_alpha_pipeline`). No new third-party dependency.
- Tests block network I/O. Acquisition tests use `httpx.MockTransport`, and every fixture is synthetic, with no
  real filing text copied from SEC.
- The repo is public: no local absolute paths in committed code, configs or docs. Run output goes under
  `~/agentic-trader-research`.
- Readiness, journal and log text carries exception type names only, never secrets.
- Seeds: parser check 20261119; session bootstrap 20261120 (block mean 10, 2,000 draws); issuer-cluster
  bootstrap 20261121 (2,000 draws); placebo 20261122.
- Frozen numbers (spec):
  - entry offset 2 sessions after `F`; `1 ≤ H ≤ 63`; overlap 63 sessions; earnings band 3 sessions ending at
    `F`'s session; identity lookback 60 sessions; structural exclusion 5 calendar days;
  - stop 2 × ATR(14), no target, no trail; costs 5/15/25 bp, 15 decides;
  - 5 controls; match return 20 sessions; 3 dollar-volume terciles; coverage 130 sessions; quiet-before 30
    sessions; event-free span `[D − 63, X + 10]`;
  - placebo spans 252 (with earnings) and 126 (standalone) sessions; report-side event-free `[r′ − 5, X′ + 10]`;
    planted 0.0185;
  - P4 ≥ 250 legs and ≥ 60 from 2023-01-01; trim 1%;
  - decisions 2016-08-01 → 2026-06-30; last exit 2026-07-31; filings 2016-01-01 → 2026-07-31;
  - parser check: 60 events, ≤ 9 errors.
- Implementers stage changes and report; the controller commits. Run `uv run pytest <scoped tests>` and
  `uv run ruff check` / `uv run ruff format --check` on touched files; ruff C408 and PLC0415 are strict.

## Review Focus

1. A `visible`, clean issuer with zero search hits has no events but is a valid control. A non-visible issuer
   (6-K filer) is never a control. Pinned in Task 4.
2. A year-less date near the year end resolves forward: filed 2024-12-15, "January 10" → 2025-01-10. A past
   reference read as a future date ("outlined at our December 7th Investor Day", filed in March) never becomes an
   announcement. Pinned in Task 2.
3. An event date `E` on a non-session (a weekend or an NYSE holiday) gets `X` = the last session strictly before
   `E`, and `D` from a non-session `F` (Good Friday) is the second session after `F`. Pinned in Task 4.
4. A share-class issuer (GOOG/GOOGL) yields one leg trading the eligible class with the higher dollar volume at
   `D`. A same-issuer symbol is never its control, and the cluster bootstrap resamples issuers. Pinned in Tasks 3
   and 4.
5. A long hold (`H` = 60 sessions) labels to its time exit instead of `immature`: the cube's 45-day `_LABEL_SPAN`
   is not used. Two rows of one symbol on one `D` with different `H` are labelled separately. Pinned in Task 5.

---

### Task 1: SEC investor-day acquisition (`alpha investor-days fetch`)

**Files:**
- Create: `agentic_trader/research/investor_days.py`
- Create: `agentic_trader/cli/commands/alpha_investor_days.py` (click group `investor-days`, subcommand `fetch`)
- Modify: `agentic_trader/data/edgar_http.py`: add `SEC_SEARCH_HOST = "efts.sec.gov"` to `EDGAR_HOSTS`
- Modify: `agentic_trader/cli/main.py`: `alpha_group.add_command(investor_days_group)`
- Test: `tests/research/test_investor_days.py`, `tests/cli/test_alpha_investor_days.py` (or the repo's CLI test
  location)

**Interfaces:**
- Produces:
  - `SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"`;
    `QUERY = '"investor day" OR "analyst day" OR "capital markets day" OR "investor meeting" OR "analyst meeting"'`;
    `SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"`;
    `ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{file}"`;
    `MAX_HITS_PER_CIK = 1000`.
  - `@dataclass(frozen=True) class SearchHit: accession: str; file: str; filed: date; items: tuple[str, ...];
    file_type: str; description: str`.
  - `@dataclass(frozen=True) class IssuerRecord: cik: int; symbols: tuple[str, ...]; visible: bool; capped: bool;
    hits: tuple[SearchHit, ...]; doc_failures: Mapping[str, int]`. `clean` is a property: `not capped and not
    doc_failures`.
  - `class InvestorDayStore(root: Path)`, which owns the layout: `tickers.json`, `submissions/CIK{cik:010d}.json`,
    `hits.json` and `docs/{accession}_{safe_file}.gz`. Methods: `document(accession, file) -> bytes | None`,
    `issuers() -> tuple[IssuerRecord, ...]` and `unmapped() -> tuple[str, ...]`, both read from `hits.json`.
  - `def visible_from_submissions(document: Mapping) -> bool`: true iff `filings.recent.form` has a form starting
    with `10-K` or `10-Q`.
  - `async def acquire_investor_days(client, symbols: Sequence[str], store, *, user_agent: str, filings_from:
    date, filings_through: date, pace) -> dict`. It returns the manifest dict: counts, failures by reason,
    `capped_ciks`, `unmapped`, the ticker-file digest and `content_sha256`.
  - `def content_sha256(store) -> str`: the SHA-256 over the canonical `hits.json` bytes plus the sorted lines
    `"{accession}_{file} {sha256(document bytes)}"`.
  - Click: `investor_days_group` (`alpha investor-days`) with `fetch --cohort PATH (repeatable, order kept)
    --cache DIR --output DIR [--from 2016-01-01] [--through 2026-07-31]`.

Acquisition behaviour (spec section 4):
- Symbols are the union of the cohorts in order (`pooled/cohort.load_cohort`).
- CIKs come from `insider_filings.symbol_ciks`, with share classes grouped and unmapped symbols listed.
- Tickers are fetched from `edgar_http.TICKERS_URL` only if absent from the store.
- Submissions are fetched once per CIK. Search pages step `from` by page length until the total is reached,
  capped at `MAX_HITS_PER_CIK` (the capped flag is set).
- Documents are fetched only for `.htm`, `.html` and `.txt` (others are counted as `not_html`), and cached.
- A 404 (`missing_ok=True` returns `None`) is counted per issuer in `doc_failures["http_404"]`.
- `httpx.HTTPStatusError` 403 on any host raises `ValueError("SEC refused the request (403); stopping")`.
- The CLI refuses an existing `--output`. On success it writes `manifest.json` with `"user_agent_set": true`,
  the command and `research_environment()`, via `short_volume.write_private`, as `alpha insider-filings` does.

- [ ] **Step 1: Write the failing tests** in `tests/research/test_investor_days.py`, using a `MockTransport`
  router over `TICKERS_URL`, `SUBMISSIONS_URL`, `SEARCH_URL` and `ARCHIVE_URL`, all synthetic:
  - `test_visible_from_submissions`: `{"filings":{"recent":{"form":["4","10-Q"]}}}` → True;
    `["6-K","20-F"]` → False.
  - `test_share_classes_search_once`: GOOG and GOOGL under one CIK produce one search and one `IssuerRecord`
    with `symbols == ("GOOG","GOOGL")`.
  - `test_pagination_and_cap`: totals of 150 give two pages, 150 hits and `capped is False`. A total of 1,200
    stops at 1,000 with `capped is True`, and the CIK is in the manifest's `capped_ciks`.
  - `test_documents_cached_and_reused`: a second run makes no archive request (count the router hits).
  - `test_404_counted_403_stops`: a 404 document gives `doc_failures == {"http_404": 1}`; a 403 search raises
    `ValueError` matching `"403"`.
  - `test_non_html_counted`: a `.pdf` hit is counted as `not_html` and not fetched.
  - `test_content_sha256_stable_and_sensitive`: the same store gives the same digest; changing one document byte
    changes it.
  - `test_user_agent_never_written`: after `fetch` through the CLI runner, with `EDGAR_USER_AGENT="Test Co
    test@example.com"` and the transport patched, no file under `--cache` or `--output` contains
    `test@example.com`.
- [ ] **Step 2: Run them to verify they fail.**
  Run `uv run pytest tests/research/test_investor_days.py -q`. Expected: ImportError / FAIL.
- [ ] **Step 3: Implement `investor_days.py`, the CLI `fetch`, and the `EDGAR_HOSTS` addition.**
- [ ] **Step 4: Run the new tests, plus `uv run pytest tests -q -k "edgar_http or insider_filings or filings"`.**
  Expected: PASS. The existing EDGAR host tests stay green.
- [ ] **Step 5: Stage** (`git add` the files above). The controller commits.

### Task 2: The frozen parser `idday-parser-v1` (`alpha investor-days events`)

**Files:**
- Create: `agentic_trader/research/apriori/idday_parser.py`. This is a plan ruling: the spec's
  `idday_events.py` is split in two, so the parser lives apart from the leg gates.
- Modify: `agentic_trader/cli/commands/alpha_investor_days.py` (subcommand `events`)
- Test: `tests/research/apriori/test_idday_parser.py`

**Interfaces:**
- Consumes: Task 1 `InvestorDayStore`, `IssuerRecord`, `SearchHit`, `content_sha256`.
- Produces:
  - `PARSER_VERSION = "idday-parser-v1"`.
  - `def text_of(raw: bytes) -> str`.
  - `def dates_in(text: str, filed: date) -> list[tuple[int, date]]`.
  - `@dataclass(frozen=True) class FilingParse: filed: date; announced: tuple[tuple[date, str, str], ...]` (event
    date, sentence, keyword) `; event_day: bool; day_sentence: str`.
  - `def parse_document(hit: SearchHit, raw: bytes) -> FilingParse`.
  - `@dataclass(frozen=True) class IddayEvent: event_id: str; cik: int; symbols: tuple[str, ...]; status:
    Literal["announced", "event_only"]; filed: date | None; event_date: date; n_announcing: int;
    event_day_material: bool; keyword: str; sentence: str; accession: str; url: str; item_2_02: bool`.
    `event_id = f"{cik}:{filed or 'none'}:{event_date}"`.
  - `@dataclass(frozen=True) class EventTable: parser_version: str; content_sha256: str; events: tuple[IddayEvent,
    ...]` with property `events_sha256`: the SHA-256 of the canonical JSON (`sort_keys=True`, `separators=(",",
    ":")`, dates ISO) of the event rows in `(cik, event_date)` order.
  - `def parse_filings(store: InvestorDayStore) -> EventTable`.
  - `def save_event_table(table, path) / load_event_table(path) -> EventTable`. The loader recomputes and checks
    `events_sha256`.
  - CLI: `events --cache DIR --output FILE` refuses an existing file and prints `events_sha256` and counts by
    status.

The rules are exactly spec section 5, steps 1–8. Keyword, date, announcement-phrase and past-marker lists are
module constants copied verbatim from the spec. The feasibility prototype
(`~/agentic-trader-research/investor-day-feasibility-20261119/idday_edgar_text.py` and `strict_check.py`) shows
the sentence and date mechanics; the spec's rules win where they differ.

- [ ] **Step 1: Write the failing tests** with synthetic HTML snippets, each wrapped in `<p>…</p>` and filed on
  the stated date:
  - `test_announcement_nearest_date`: filed 2024-02-20, "Acme Inc. today announced it will host its Investor Day
    on Tuesday, March 5, 2024, following its earnings call on February 27." → `announced[0][0] ==
    date(2024,3,5)`.
  - `test_heading_then_sentence`: "Annual Investor Day. Intuit will host its annual Investor Day at its
    headquarters on Sept. 21 at 8 a.m." filed 2016-08-23 → 2016-09-21.
  - `test_past_markers_reject`: these produce no announcement:
    - "outlined at our December 7th Investor Day" filed 2023-03-14;
    - "thank those who attended our investor day on May 24" filed 2022-08-08;
    - "guidance that it reiterated at Investor Day on September 29, 2022" filed 2022-11-01.
  - `test_date_in_next_sentence_rejected`: "We look forward to our annual Investor Day. The quarter ends Oct.
    31." filed 2016-08-23 → no announcement.
  - `test_no_verb_rejected`: "CFA Society Minnesota Intellisight Investor Day. August 12, 2020." filed 2020-08-07,
    and a slide table "INVESTOR DAY 2022 Fiscal Year Oct 20-Sep 21" → none.
  - `test_phrases_accept`: each of these is announced on the right date:
    - "Save the Date June 24, 2025 CF Industries Investor Day";
    - "intend to make presentations in Houston, Texas on January 26, 2022 at the Kinder Morgan 2022 Investor Day";
    - "with more to come at our long-planned Investor Day on September 26";
    - "Announced our upcoming investor day on May 21";
    - "Additional detail to be provided at Vontier's Investor Day on March 23, 2023".
  - `test_year_rollover`: filed 2024-12-15, "will host its Investor Day on January 10" → 2025-01-10.
  - `test_may_verb_not_a_date`: "It may 5 times" yields no date; "MAY 5, 2025" (upper case) does.
  - `test_numeric_dates_need_year`: "1/2" gives no date; "3/5/2024" gives 2024-03-05.
  - `test_event_day_material`: "On March 5, 2024, Acme is hosting an Investor Day." filed 2024-03-05 → `event_day`
    True. A hit description "INVESTOR DAY PRESENTATION" with no announcement → `event_day` True.
  - `test_events_merge_and_first_announcement`, for one issuer:
    - an announcement filed 2024-02-01 for 2024-03-05;
    - a reminder filed 2024-02-20 for 2024-03-06;
    - event-day material filed 2024-03-05.

    Expect one event with `filed == 2024-02-01`, `event_date == 2024-03-05`, `n_announcing == 2` and
    `event_day_material`. An issuer with only event-day material gives `status == "event_only"`.
  - `test_item_2_02_flag`: an announcing hit with `items == ("2.02","9.01")` gives `item_2_02 True`.
  - `test_events_sha256_roundtrip`: `save_event_table` then `load_event_table` keeps the digest; a tampered file
    raises `ValueError`.
- [ ] **Step 2: Run them to verify they fail.**
  Run `uv run pytest tests/research/apriori/test_idday_parser.py -q`.
- [ ] **Step 3: Implement `idday_parser.py` and the `events` subcommand.**
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Stage.**

### Task 3: Foundations: `IddayEntry`, `merge_cubes`, `cluster_paired_test`

**Files:**
- Modify: `agentic_trader/research/apriori/catalog.py`
- Modify: `agentic_trader/research/pooled/cube.py`
- Modify: `agentic_trader/research/apriori/statistics.py`
- Create: `tests/research/apriori/fixtures/idday-v1-test.json`, a valid entry with dummy 64-hex digests
- Test: `tests/research/apriori/test_idday_catalog.py`, `tests/research/apriori/test_statistics.py`,
  `tests/research/pooled/test_cube.py` (or the existing cube test file)

**Interfaces:**
- Produces:
  - `class IddayCohort(BaseModel, frozen, extra="forbid"): name: Literal["v3","xu"]; pooled_protocol: str;
    pooled_protocol_sha256: str; cube_sha256: str` (hex patterns as `RunupEntry`).
  - `class IddayWindow(PeadWindow): last_exit: date`.
  - `class IddaySources(...): content_sha256: str; events_sha256: str; parser_version: Literal["idday-parser-v1"];
    filings_from: date; filings_through: date`.
  - `class IddayParserCheck(...): sha256: str; seed: int; sample_size: int = 60; max_errors: int = 9;
    excluded_symbols: tuple[str, ...]`. The excluded symbols are the 50 feasibility names.
  - `class IddayEvent(...)`: the catalog model, distinct from the parser's dataclass, named `IddayEventRules` to
    avoid the clash. Fields `entry_offset_sessions=2, min_hold_sessions=1, max_hold_sessions=63,
    overlap_sessions=63, earnings_band_sessions=3, identity_lookback_sessions=60, structural_exclusion_days=5`.
  - `class IddayControl(...)`: fields `matched_controls=5, match_return_sessions=20, liquidity_buckets=3,
    coverage_sessions=130, quiet_before_sessions=30, event_free_before_sessions=63, event_free_after_sessions=10`.
  - `class IddayPlacebo(...)`: fields `earnings_span_sessions=252, standalone_span_sessions=126,
    report_event_free_before_sessions=5, seed=20261122, planted_return=0.0185`.
  - `class ClusterBootstrapSpec(...): draws: int; seed: int`.
  - `class IddayEntry(BaseModel)`: fields `id: Literal["idday"]`, `version`, `title`, `references`, `hypotheses`,
    `decision_rule`, `window: IddayWindow`, `cohorts: tuple[IddayCohort, IddayCohort]` (names v3 then xu),
    `investor_days: IddaySources`, `parser_check: IddayParserCheck`, `event: IddayEventRules`, `trade:
    RunupTrade`, `costs_bps_per_side`, `decision_cost_bps`, `control: IddayControl`, `placebo: IddayPlacebo`,
    `bootstrap: BootstrapSpec`, `cluster_bootstrap: ClusterBootstrapSpec` and `pass_rule: PassRule`.
    Validators:
    - the decisive cost is in the costs;
    - the cohort names are `("v3","xu")`;
    - `window.last_exit ≥ window.decisions[1]`;
    - `investor_days.filings_through ≥ window.last_exit`.

    `check_against(spec)` behaves as in `RunupEntry`.
  - `@dataclass(frozen=True) class LoadedIddayEntry`; `def load_idday_entry(path) -> LoadedIddayEntry`.
  - `def merge_cubes(first: LabelCube, second: LabelCube) -> LabelCube` in `cube.py`. It raises `ValueError`
    unless the spec identities and sessions are equal and the symbols disjoint. Symbols are `first` then
    `second`, arrays are concatenated on axis 1, `cohort_sha256 = sha256(f"{first.cohort_sha256}|{second.
    cohort_sha256}")`, and coverage is `{"merged": [first.coverage, second.coverage]}`.
  - `def cluster_paired_test(leg: pd.DataFrame, control: pd.DataFrame, clusters: pd.Series, spec:
    ClusterBootstrapSpec) -> dict` in `statistics.py`. `leg` and `control` are aligned rows with `r_cost`;
    `clusters` is the issuer per row. Clusters are resampled with replacement and the paired mean difference
    taken over the resampled rows. It returns `{"n","n_clusters","mean_difference","ci90","finite_draws",
    "holds"}`, where `holds` is mean > 0 and the ci90 lower bound > 0. Add it to `__all__`.

- [ ] **Step 1: Write the failing tests:**
  - `test_idday_entry_loads_fixture` (sha256 recorded);
  - `test_idday_entry_rejects_wrong_cohort_order`;
  - `test_idday_entry_rejects_last_exit_before_decisions_end`;
  - `test_check_against_rejects_window_outside_cube`;
  - `test_merge_cubes_concatenates_and_hashes`: two tiny cubes give `symbols == a+b`, `window(...)` arrays equal
    to the hstack, and a `sha256` that differs from either input;
  - `test_merge_cubes_refuses_overlap_or_mismatch`: shared symbol, different sessions, different spec identity;
  - `test_cluster_paired_test_one_cluster_dominates`: 10 rows of one issuer at +1 and 10 single-row issuers at
    −0.1. The row mean is > 0, but `cluster_paired_test(...)["holds"] is False`. A uniform +0.5 over 30 issuers
    holds.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/research/apriori/test_idday_catalog.py tests/research/apriori/test_statistics.py tests/research/pooled -q`.
  Expected: PASS, with existing tests green.
- [ ] **Step 5: Stage.**

### Task 4: Events, controls, placebos and the parser check (`idday_events.py`, `alpha investor-days sample`)

**Files:**
- Create: `agentic_trader/research/apriori/idday_events.py`
- Modify: `agentic_trader/cli/commands/alpha_investor_days.py` (subcommand `sample`)
- Test: `tests/research/apriori/test_idday_events.py`

**Interfaces:**
- Consumes: Task 2 `EventTable` and `IddayEvent`; Task 3 `IddayEntry`; Task 1 `IssuerRecord`; the cube and
  helpers as runup (`CubeView`, `_buckets` from `insider_study`, `identity_breaks`, `CalendarRow`,
  `CorporateAction`).
- Produces:
  - `@dataclass(frozen=True) class IddayInputs`: runup's `RunupInputs` fields (`cube` is the merged cube,
    `trading_days`, `adjusted`, `hourly`, `reports`, `actions`, `identity_breaks`, `market_daily`,
    `market_hourly`, `decision_time`, `provenance`), plus `events: EventTable`, `issuers: Mapping[int,
    IssuerRecord]`, `issuer_of: Mapping[str, int]` and `band_of: Mapping[str, str]`. `band_of` values are `"v3"`,
    `"xu_large"`, `"xu_small"`, or `"xu"` if the cohort file gives no bands.
  - `def issuer_reports(reports, symbols) -> tuple[date, ...]`: the union of report dates, deduplicated and sorted.
  - `def candidate_legs(table: EventTable, trading_days: Sequence[date], reports_by_issuer: Mapping[int,
    tuple[date, ...]], entry: IddayEntry) -> tuple[pd.DataFrame, dict]`. These are spec section 7 steps 1–4,
    price-free. Columns: `event_id, cik, F, E, D, X, H, klass ("with_earnings"|"standalone")`. Counts are by
    reason (`lead_too_short`, `lead_too_long`, `outside_window`, `overlapping_event`, `earnings_in_hold`).
  - `def sample_parser_check(candidates: pd.DataFrame, table: EventTable, entry: IddayEntry) -> dict`. It returns
    `{"events_sha256", "seed", "sample_size", "sample": [{"event_id","symbols","filed","event_date","keyword",
    "sentence","url"}]}`, drawn with `numpy.random.default_rng(entry.parser_check.seed)` without replacement
    from candidates whose issuer has no symbol in `excluded_symbols`, in `event_id` order before drawing.
  - `def validate_parser_check(document: Mapping, sample: Mapping, entry: IddayEntry) -> dict`. It raises
    `ValueError` unless all of these hold:
    - `document["events_sha256"] == sample["events_sha256"]`;
    - the labelled `event_id`s equal the sample's;
    - every label has a bool `correct` and a non-empty `reason`;
    - errors ≤ `max_errors`.

    It returns `{"errors","sample_size","precision"}`.
  - `def build_idday_events(inputs: IddayInputs, entry: IddayEntry) -> tuple[pd.DataFrame, dict]`. It covers
    candidates plus steps 5–7, issuer symbol choice, the `leg` rows and one placebo per leg (spec section 9's
    draws, `role == "placebo"`, the same `H` and `klass`, `duplicate_session` dropped). Columns are runup's
    `RUNUP_EVENT_COLUMNS` adapted: `event_id, pair_id, role, symbol, cik, klass, band, session (D), decision_at,
    F, E, X, hold_sessions (H), atr, ret_20, bucket, keyword, item_2_02`.
  - `def match_controls(events, inputs, entry) -> tuple[pd.DataFrame, dict]`: spec section 8 control rules,
    `CONTROL_CANDIDATE_COLUMNS` as runup.
  - CLI `sample --cache DIR --events FILE --repo-root . --earnings-pages DIR --pooled-cache DIR --output FILE`.
    It loads the v3 events protocol offline (pages via `load_calendar_pages` through `load_event_sources`, the
    cached v3 cube's sessions via `cube_cache_path`; no provider call), runs `candidate_legs`, and writes
    `parser_sample.json`.

- [ ] **Step 1: Write the failing tests** with a tiny synthetic calendar, reports, issuers and a cube built in
  the test, as the runup event tests do:
  - `test_timing_D_X_H`:
    - `F` Monday 2024-03-04 and `E` Thursday 2024-03-21 give `D` 2024-03-06, `X` 2024-03-20 and `H` 11;
    - `F` on a non-session gives `D` = the second session after;
    - `E` on a holiday gives `X` = the last session before;
    - `H` 0 counts as `lead_too_short` and `H` 64 as `lead_too_long`.
  - `test_overlap_per_issuer`: two events of one issuer 40 sessions apart → the second is `overlapping_event`.
  - `test_earnings_class_and_hold`: a report on `F`'s session gives `with_earnings`; a report 2 sessions before
    `F`'s session gives `with_earnings`; a report 3 before gives `standalone`; a report at `F + 1` gives
    `earnings_in_hold`.
  - `test_share_class_one_leg_most_liquid`: GOOG/GOOGL eligible, GOOGL with the higher dollar volume at `D` → one
    leg on GOOGL.
  - `test_control_rules`:
    - a non-visible issuer, a capped issuer, an issuer with doc failures, the leg's own issuer, a name with a raw
      hit inside `[D − 63, X + 10]`, and a standalone-class name with a report in `[D − 30, X]` are all excluded;
    - a visible, clean issuer with zero hits is accepted;
    - for a `with_earnings` leg, only reporting peers are accepted.
  - `test_placebo_with_earnings_uses_other_report`: the placebo `D′` is the second session after another report
    of the issuer, with the same `H`; a second identical draw is dropped as `duplicate_session`.
  - `test_parser_check_sample_and_validate`:
    - sampling is deterministic for the seed and excludes `excluded_symbols` issuers;
    - validation passes with 9 errors and fails with 10;
    - validation fails on a digest mismatch, on a missing reason, and on an `event_id` set differing from the
      sample.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement `idday_events.py` and the `sample` subcommand.**
- [ ] **Step 4: Run** `uv run pytest tests/research/apriori/test_idday_events.py -q`. Expected: PASS.
- [ ] **Step 5: Stage.**

### Task 5: The study: labels, statistics, planted check, diagnostics, executor (`idday_study.py`)

**Files:**
- Create: `agentic_trader/research/apriori/idday_study.py`
- Test: `tests/research/apriori/test_idday_study.py`

**Interfaces:**
- Consumes: Task 4 `IddayInputs`, `build_idday_events`, `match_controls`; Task 3 `IddayEntry`,
  `cluster_paired_test`; runup's helpers by import where they are already public (`save_frame`,
  `save_json_report`, `_finite_json`, `mean_test`, `paired_test`, `difference_in_paired_test`, `trimmed_mean`,
  `label_bracket`, `BracketArm`).
- Produces:
  - `IDDAY_ARM = BracketArm(name="idday_time_exit", target_r=None, trail=None)`.
  - `def label_idday(events, candidates, inputs, entry) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]`.
    It works as runup's `label_runup`, with two differences:
    - the hold is per row (`hold_sessions`);
    - the hourly slice runs from `decision_at − 1 day` through the close of the session `D + H − 1` plus 3
      calendar days, and cells are cached by `(symbol, D, H)`.
  - `class IddayDecision(StrEnum)`: `survives_development`, `failed`, `inconclusive_underpowered`.
  - `def planted_check(labels, controls, entry) -> dict`, with planted return 0.0185.
  - `def evaluate_idday(labels, controls, entry, *, planted=None) -> dict`. It covers P1–P5 and P3's issuer
    cluster test (clusters are the legs' `cik`), the placebo, the decision and the section 10 diagnostics.
  - `async def execute_idday_study(loaded: LoadedIddayEntry, directory: Path, *, build: Callable[[],
    Awaitable[IddayInputs]], environment: dict) -> dict`. It mirrors `execute_runup_study`, with the manifest's
    `pinned` listing both cohorts, `investor_days` and `parser_check`, and it checks the merged cube's sha256
    against `sha256(f"{v3_cube_sha}|{xu_cube_sha}")`. That digest is computed in the runner and stored in
    provenance; the executor compares `inputs.cube.sha256` with `inputs.provenance["merged_cube_sha256"]` and
    each part's pinned digest with `provenance["cube_sha256_v3"]` and `provenance["cube_sha256_xu"]`.

- [ ] **Step 1: Write the failing tests** with synthetic hourly bars:
  - `test_long_hold_not_immature`: a leg with `H = 60` on flat bars exits at the time exit of session
    `D + 59`, with `hit` the time-exit code, not `immature`.
  - `test_cells_keyed_by_hold`: one symbol on one `D` as a control for legs with `H = 5` and `H = 20` produces two
    labelled cells with different exit sessions.
  - `test_hold_outside_window_excluded`: a row whose exit lands after the close of `X` is excluded by that reason.
  - `test_planted_detected`: synthetic paired placebos with noise sd 0.5R and n = 400 detect the planted
    0.0185.
  - `test_decision_rules`:
    - the planted check fails → `inconclusive_underpowered`;
    - P3's cluster test fails while P1/P2 hold → `failed`;
    - the placebo P2 holds → `failed`;
    - all hold → `survives_development`.
  - `test_executor_writes_protocol_and_manifest_first`: a `build` that raises gives `status == "failed"`, and
    `protocol.json` and `manifest.json` exist. A successful synthetic build writes every artifact named in spec
    section 11, with `authorizes_promotion False`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/research/apriori/test_idday_study.py -q`. Expected: PASS.
- [ ] **Step 5: Stage.**

### Task 6: Runner and `alpha apriori-study` dispatch (`idday_runner.py`)

**Files:**
- Create: `agentic_trader/research/apriori/idday_runner.py`
- Modify: `agentic_trader/cli/commands/alpha.py`: options `--investor-days DIR`, `--parser-check FILE`,
  `--corporate-actions-xu FILE`; validation; the `idday` branch
- Test: `tests/research/apriori/test_idday_runner.py`; extend the existing `alpha apriori-study` CLI validation
  tests

**Interfaces:**
- Consumes: everything above; `build_cube_inputs`, `load_event_sources`, `load_campaign_protocol`,
  `EventsCampaignProtocol`, the insider protocol class, `load_cohort`, `identity_breaks`, runup's `_market_frame`
  pattern (SPY).
- Produces: `async def build_idday_inputs(entry: IddayEntry, *, repo_root, bars, calendar, cache_dir,
  earnings_pages, actions_v3, actions_xu, investor_days_dir, parser_check, market_cache_dir, static_symbols=(),
  pace, progress=None) -> IddayInputs`. Steps:
  1. For each cohort, check the protocol bytes against the pinned SHA-256, load the protocol, and check that its
     cube spec identity equals the other's. Call `entry.check_against(spec)` and check the cohort SHA-256.
  2. For v3, check pages and actions with `load_event_sources`. For xu, load the actions file and check its
     content digest against `insider_inputs.corporate_actions.content_sha256`. Find the loader used by the
     insider runner or `load_event_sources`; do not re-implement it.
  3. `build_cube_inputs` for each cohort (`require_cached=True`, `load_hourly=True`; xu without field sources).
     Check each cube's sha256 against the pinned value, then `merge_cubes(v3, xu)`. Merge `adjusted`, `hourly`
     and actions, and compute `identity_breaks` over the union.
  4. Check `content_sha256(InvestorDayStore(investor_days_dir))` against the pin. Run `parse_filings`, then check
     `events_sha256` against the pin.
  5. Check `sha256(parser_check bytes) == entry.parser_check.sha256`. Rebuild `candidate_legs` and
     `sample_parser_check` from the inputs and call `validate_parser_check`. Any failure raises before any SPY
     read.
  6. SPY daily and hourly bars, as runup. Provenance includes every digest, `merged_cube_sha256`, the
     `cube_sha256_v3`/`_xu` values, the parser-check precision, the issuer counts (`visible`, `capped`, failures)
     and `unmapped`.

  CLI: for `entry_id == "idday"`, require `--cache`, `--earnings-pages`, `--corporate-actions`,
  `--corporate-actions-xu`, `--investor-days` and `--parser-check`, and reject `--insider-filings`. Reject the
  three new options for every other entry. Extend the existing messages as runup's branch does.

- [ ] **Step 1: Write the failing tests:**
  - `test_runner_refuses_wrong_protocol_digest`;
  - `test_runner_refuses_events_digest_mismatch`;
  - `test_runner_refuses_failing_parser_check` (10 errors);
  - `test_runner_refuses_parser_check_file_digest_mismatch`. These use monkeypatched loaders and small fixtures,
    as `test_runup_runner.py` does.
  - CLI: `test_apriori_study_idday_requires_its_options` and `test_new_options_rejected_for_other_entries`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/research/apriori -q -k "idday or runup or catalog"` and the CLI tests.
  Expected: PASS.
- [ ] **Step 5: Stage.**

## After the tasks (controller, not an SDD task)

These follow the final whole-branch review, in spec section 12's order:
1. `alpha investor-days fetch` after the close, outside the busy windows.
2. `events`, then `sample`, then the controller's labels into `parser_check.json`.
3. Write `config/research/apriori/idday-v1.json` with every digest.
4. Ledger row L45, then commit.
5. `alpha apriori-study`.
6. The results doc and the doc updates the spec lists.
7. The PR.
