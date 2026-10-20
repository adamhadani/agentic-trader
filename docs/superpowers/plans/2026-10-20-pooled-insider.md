# Pooled Insider Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the SEC's structured Form 3/4/5 insider-transactions data sets as a digest-pinned research acquisition and six `insider_fields_v1` score fields on the cached pooled cubes, with a fixed-set protocol `pooled-insider-campaign` frozen for cohorts v2 and v3.

**Architecture:** A new offline acquisition (`research/insider_filings.py`, CLI `alpha insider-filings`) caches one quarterly archive per quarter plus the SEC ticker map, each with a SHA-256, and a files digest over all of them. A reader extracts qualifying open-market trades per issuer CIK; a new field builder (`research/pooled/insider_fields.py`) turns them, the cohort's corporate actions and raw bars into per-symbol frames; the pooled runner builds them beside the cube exactly as it builds short-volume frames, the score book merges them through `with_fields`, and a new protocol subclass pins the digests so an insider formula never shares an identity with another lane's.

**Tech Stack:** Python 3.14, pydantic v2 (frozen, `extra="forbid"`), httpx, pandas/numpy, click, pytest, stdlib `zipfile`/`csv`; ruff strict (no `dict()` literal calls, no function-level imports), mypy.

**Spec:** `docs/superpowers/specs/2026-10-20-pooled-insider-design.md`

## Global Constraints

- Research only: no database, registry, broker, Telegram or promotion credit anywhere in this plan.
- Only host `www.sec.gov`; every request carries the operator's `User-Agent` from `EDGAR_USER_AGENT` (refuse without it); the manifest records `user_agent_set: true` and never the value; at most 2 requests per second, 60 s timeout, 64 MiB per-archive byte budget, one retry on a transport error, idempotent GETs only.
- Index URL `https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets`; archive links match `href="(/files/[^"]*?(\d{4}q[1-4])_form345\.zip)"`; ticker map `https://www.sec.gov/files/company_tickers.json` (a JSON object whose values have `cik_str`, `ticker`, `title`).
- Archive tables read: `SUBMISSION.tsv` (`ACCESSION_NUMBER`, `FILING_DATE` as `DD-MON-YYYY`, `DOCUMENT_TYPE`, `ISSUERCIK`, `AFF10B5ONE`), `REPORTINGOWNER.tsv` (`ACCESSION_NUMBER`, `RPTOWNERCIK`, `RPTOWNER_RELATIONSHIP`), `NONDERIV_TRANS.tsv` (`ACCESSION_NUMBER`, `TRANS_DATE`, `TRANS_CODE`, `TRANS_SHARES`, `TRANS_PRICEPERSHARE`, `TRANS_ACQUIRED_DISP_CD`); `FOOTNOTES.tsv` is never read; `csv.field_size_limit` is raised before reading.
- Transaction rules (spec §3): `DOCUMENT_TYPE == "4"`; `TRANS_CODE` `P` with `TRANS_ACQUIRED_DISP_CD` `A` or `S` with `D`; `TRANS_SHARES > 0` and `TRANS_PRICEPERSHARE > 0`, `dollars = shares × price`; an accession counts when a reporting owner's `RPTOWNER_RELATIONSHIP` contains one of `owner_roles`; `AFF10B5ONE == "1"` rows dropped when `exclude_10b5_1`; `trans_date <= filing_date`; `owner_cik` is the first qualifying owner's CIK.
- Symbol mapping by issuer CIK through the ticker map: ticker upper-cased with `-` → `.`; a CIK shared by several cohort symbols fans out to each; a cohort symbol with no CIK has no rows and is reported.
- Field names: `insider_net_ratio`, `insider_buy_rel`, `insider_sell_rel`, `insider_buyers`, `insider_buy_age`, `insider_opp_buy_rel`; `fields_version` literal `"insider_fields_v1"`; protocol id `"pooled-insider-campaign"`; `insider_inputs.insider_filings.source` literal `"sec-form345-datasets"`.
- Protocol values pinned in the frozen files: `quarters_from` `2015q1`, `quarters_through` `2026q3`, `buy_window` 63, `net_window` 126, `owner_roles` `["Officer", "Director"]`, `exclude_10b5_1` true.
- Visibility: a filing dated `F` counts on every trading day `T >= F`; a filing dated on a non-session counts from the next session; windows `(T − w, T]` in sessions, inclusive of `T`.
- `DV_T` = median of raw `Close × Volume` over the last 20 finite sessions through `T` (`dropna` then `rolling(20, min_periods=20).median()`, reindexed to `trading_days`).
- Coverage start: the first session with any qualifying transaction filed for the issuer; `insider_buy_rel`, `insider_sell_rel`, `insider_buyers`, `insider_opp_buy_rel` are `0` once covered with no activity and `NaN` before coverage; `insider_net_ratio` is `NaN` with a zero denominator; `insider_buy_age` is `NaN` before the first purchase.
- Routine purchase: the same `owner_cik` filed a purchase in the same issuer in the same calendar month of the previous year (filing months on both sides); routine purchases leave `insider_opp_buy_rel`.
- Identity breaks reuse `identity_breaks` from `events.py`: fields `NaN` on `T < break_on`; no rows are discarded (the CIK survives a rename).
- `search_mode` must be `fixed_set`; base-class, intraday, events and short-volume protocol hashes and identities are unchanged.
- Existing test fixtures stay valid: `tests/research/pooled/mini_world.py` gains an insider world beside the short world; no fixture is removed.
- Pre-commit runs ruff, mypy and the impacted pytest; commit a task's tests with its implementation.

## Review Focus

1. A filing accepted after 17:30 ET carries the next day's filing date, so a field must never count a filing on a session before its filing date, or the lane leaks tomorrow's filing into today's read. → Task 3 test `test_a_filing_counts_on_its_filing_session_and_not_before`.
2. An archive whose `SUBMISSION.tsv` lacks `AFF10B5ONE` (the column appears in 2023) must still read: the 10b5-1 rule applies only where the column exists, and the drop count says how many rows it removed. → Task 3 test `test_archives_without_the_10b5_1_column_still_read`.
3. A joint filing by two officers, or two filings by one officer on one day, must not inflate `insider_buyers`: distinct owners are counted, trades are summed. → Task 3 test `test_buyers_counts_distinct_owners_across_filings`.
4. A cohort symbol whose issuer never files a Form 4 (a foreign private issuer) must stay `NaN` in every buy field, never `0`, so it is in no decile. → Task 3 test `test_never_filing_issuer_is_nan_not_zero`.
5. A quarter missing from the index page (a renamed path) must fail the acquisition closed with the quarter named, never skip it, or the digest would silently cover fewer quarters than the protocol says. → Task 1 test `test_missing_quarter_on_the_index_fails_closed`.

---

### Task 1: SEC acquisition module and `alpha insider-filings` command

**Files:**
- Create: `agentic_trader/research/insider_filings.py`
- Create: `agentic_trader/cli/commands/alpha_insider_filings.py`
- Modify: `agentic_trader/cli/main.py` (import and `alpha_group.add_command` beside `short_volume_cmd`)
- Test: `tests/research/test_insider_filings.py`, `tests/cli/test_alpha_insider_filings_cli.py`

**Interfaces:**
- Consumes: `write_private` from `agentic_trader/research/short_volume.py` (import, do not copy); `research_environment` from `cli/commands/alpha.py`; `coro` from `cli/utils.py`.
- Produces:
  - `@dataclass(frozen=True, order=True) class Quarter(year: int, index: int)`: `parse_quarter(text: str) -> Quarter` (`^\d{4}q[1-4]$`, else `ValueError("quarter must look like 2015q1")`), `str(quarter) == "2015q1"`, `quarters_between(start: Quarter, end: Quarter) -> tuple[Quarter, ...]` (inclusive; `ValueError` when `end < start`).
  - `INDEX_URL`, `TICKERS_URL`, `SEC_HOST = "www.sec.gov"`, `MAX_ARCHIVE_BYTES = 64 * 1024 * 1024`, `REQUESTS_PER_SECOND = 2`, `_TIMEOUT_SECONDS = 60.0`, `REQUIRED_TABLES = {"SUBMISSION.tsv": (...columns...), "REPORTINGOWNER.tsv": (...), "NONDERIV_TRANS.tsv": (...)}` (the columns from Global Constraints, `AFF10B5ONE` excluded from the required set because old archives lack it).
  - `resolve_archive_urls(index_html: str) -> dict[str, str]` — quarter string → absolute URL on `https://www.sec.gov`.
  - `validate_archive(data: bytes) -> dict[str, int]` — row counts per required table; `ValueError` naming the missing table or column; a non-zip raises `ValueError("not a zip archive")`.
  - `class InsiderFilingStore(directory: Path)`: `paths(quarter) -> tuple[Path, Path]` (`{quarter}_form345.zip`, `{quarter}_form345.meta.json`), `tickers_paths() -> tuple[Path, Path]` (`company_tickers.json`, `company_tickers.meta.json`), `load(quarter) -> Path | None` (verifies the file's sha256 against its meta, raises `ValueError` on mismatch, returns the archive path), `load_tickers() -> dict | None` (same verification), `meta(quarter) -> dict | None`, `save(quarter, body: bytes, *, url, requested_at, received_at)`, `save_tickers(body, *, url, requested_at, received_at)`; meta keys `sha256, url, requested_at, received_at, status ("present"), bytes`; directories created only in `save*`; files written with `write_private`.
  - `files_sha256(store: InsiderFilingStore, quarters: Sequence[Quarter]) -> str` — SHA-256 of the newline-joined sorted lines `"<quarter>:<sha256>"` plus `"tickers:<sha256>"`; `ValueError` on a quarter or ticker map with no meta.
  - `async def acquire_insider_filings(client: httpx.AsyncClient, quarters: Sequence[Quarter], store: InsiderFilingStore, *, pace: Callable[[], Awaitable[None]]) -> dict` — the manifest: `window` (`[str(first), str(last)]`), `quarters` (strings), `quarters_fetched`, `quarters_reused`, `tickers_reused: bool`, `files_sha256`, `bytes_total`, `files` (the digest lines). Fetches the index once (only when some quarter is not cached), resolves every requested quarter (a missing one raises `ValueError("no archive on the SEC index for <quarter>")`), fetches the ticker map once when not cached, streams each archive under the byte budget, validates before saving, keeps an invalid archive on disk at the `.zip` path with `status: "invalid"` meta and raises.
  - CLI `copilot alpha insider-filings --from 2015q1 --through 2026q3 --cache DIR --output DIR`: reads `EDGAR_USER_AGENT` from the environment (`ClickException("EDGAR_USER_AGENT is required: the SEC's fair-access policy needs a declared contact")` when unset or blank), builds `httpx.AsyncClient(headers={"User-Agent": ua}, follow_redirects=False, timeout=_TIMEOUT_SECONDS)`, paces at `REQUESTS_PER_SECOND` (the `_pacer` pattern from `alpha_short_volume.py`), writes `output/manifest.json` with `user_agent_set: true`, `command` (argv), `environment` (`research_environment()`); refuses an existing output directory.

- [ ] **Step 1: Write the failing quarter, index, validation and store tests**

```python
def test_quarter_parsing_ordering_and_ranges():
    assert str(parse_quarter("2015q1")) == "2015q1" and parse_quarter("2015q4") < parse_quarter("2016q1")
    assert len(quarters_between(parse_quarter("2015q1"), parse_quarter("2026q3"))) == 47
    with pytest.raises(ValueError, match="2015q1"):
        parse_quarter("2015q5")
    with pytest.raises(ValueError):
        quarters_between(parse_quarter("2016q1"), parse_quarter("2015q4"))


def test_resolve_archive_urls_handles_both_path_prefixes():
    html = ('<a href="/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip">x</a>'
            '<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip">y</a>')
    urls = resolve_archive_urls(html)
    assert urls["2026q1"].startswith("https://www.sec.gov/files/structureddata/") and "2026q2" in urls


def test_validate_archive_requires_the_three_tables_and_their_columns():
    assert validate_archive(_archive())["NONDERIV_TRANS.tsv"] == 1
    with pytest.raises(ValueError, match="REPORTINGOWNER"):
        validate_archive(_archive(drop="REPORTINGOWNER.tsv"))
    with pytest.raises(ValueError, match="TRANS_CODE"):
        validate_archive(_archive(nonderiv_header="ACCESSION_NUMBER\tTRANS_DATE\n"))
    with pytest.raises(ValueError, match="zip"):
        validate_archive(b"not a zip")


def test_store_round_trips_and_refuses_a_corrupt_archive(tmp_path):
    store = InsiderFilingStore(tmp_path)
    store.save(parse_quarter("2025q1"), b"body", url="u", requested_at=NOW, received_at=NOW)
    assert store.load(parse_quarter("2025q1")).read_bytes() == b"body"
    store.paths(parse_quarter("2025q1"))[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        store.load(parse_quarter("2025q1"))
```

`_archive(...)` builds a zip in memory (`zipfile.ZipFile(io.BytesIO(), "w")`) with the three TSVs and one row each; keep it in a shared helper module `tests/research/insider_fixtures.py` so Task 3's tests reuse it.

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/research/test_insider_filings.py -q`; expected: ImportError.
- [ ] **Step 3: Implement `Quarter`, `parse_quarter`, `quarters_between`, `resolve_archive_urls`, `validate_archive`, `InsiderFilingStore` in `agentic_trader/research/insider_filings.py`.**
- [ ] **Step 4: Run the tests to verify they pass.**
- [ ] **Step 5: Write the failing acquisition tests** (httpx `MockTransport`, as in `tests/research/test_short_volume.py`)

```python
async def test_acquisition_resolves_the_index_fetches_tickers_and_archives_and_digests(tmp_path):
    # index html lists 2025q1 and 2025q2; handler serves company_tickers.json and both zips; records request paths and headers
    manifest = await acquire_insider_filings(client, quarters, store, pace=_noop)
    assert manifest["quarters_fetched"] == 2 and manifest["tickers_reused"] is False
    assert manifest["files_sha256"] == files_sha256(store, quarters)
    assert all(h["user-agent"] == "Test Desk test@example.com" for h in headers_seen)
    again = await acquire_insider_filings(client, quarters, store, pace=_noop)
    assert again["quarters_reused"] == 2 and again["tickers_reused"] is True and len(index_requests) == 1


async def test_missing_quarter_on_the_index_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="2025q3"):
        await acquire_insider_filings(client, quarters_between(q("2025q1"), q("2025q3")), store, pace=_noop)


async def test_invalid_archive_is_kept_on_disk_and_fails(tmp_path):
    # the 2025q2 body lacks REPORTINGOWNER.tsv → ValueError; the .zip exists; meta status "invalid"; store.load raises


async def test_byte_budget_and_foreign_hosts_fail_closed(tmp_path):
    # a body above MAX_ARCHIVE_BYTES (patch the constant to 1024 for the test) → ValueError("byte budget");
    # an index link to another host → ValueError("host")


def test_files_digest_changes_with_the_ticker_map(tmp_path):
    # same archives, different company_tickers.json bytes → different digest
```

- [ ] **Step 6: Run them to verify they fail.**
- [ ] **Step 7: Implement `acquire_insider_filings` and `files_sha256`** — `client.stream("GET", url)`, bytes counted against `MAX_ARCHIVE_BYTES`, `await pace()` before each request, one retry on `httpx.TransportError`, `raise_for_status()` on any non-200; every resolved URL's host must equal `SEC_HOST`.
- [ ] **Step 8: Run the acquisition tests to verify they pass.**
- [ ] **Step 9: Write the failing CLI tests** in `tests/cli/test_alpha_insider_filings_cli.py` (CliRunner; monkeypatch `httpx.AsyncClient` to the mock transport; monkeypatch `research_environment`): without `EDGAR_USER_AGENT` → exit 1 with the message and no request; with it → `manifest.json` has `files_sha256`, `user_agent_set` is `True`, the string `test@example.com` is absent from the manifest bytes; an existing `--output` is refused; `--through` before `--from` is refused.
- [ ] **Step 10: Implement `alpha_insider_filings.py`** (`@click.command("insider-filings")`, `@coro`, `--from "start"`/`--through "end"` as strings parsed with `parse_quarter`) and register it in `cli/main.py` next to `short_volume_cmd`.
- [ ] **Step 11: Run `uv run pytest tests/research/test_insider_filings.py tests/cli/test_alpha_insider_filings_cli.py -q`, `uv run ruff check`, `uv run mypy agentic_trader/research/insider_filings.py agentic_trader/cli/commands/alpha_insider_filings.py`.** Expected: all pass, clean.
- [ ] **Step 12: Stage** the new and modified files and report (the controller commits).

---

### Task 2: Protocol subclass `InsiderCampaignProtocol` and its documents

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py` (after `ShortVolumeCampaignProtocol`, line ~508; `_PROTOCOL_CLASSES` line ~514; `__all__`)
- Modify: `agentic_trader/research/pooled/scoring.py` (beside `short_inputs_document`, line ~297)
- Modify: `tests/research/pooled/mini_world.py` (`mini_insider_protocol(**update)` beside `mini_short_protocol`, line ~295)
- Test: `tests/research/pooled/test_campaign_protocol.py`

**Interfaces:**
- Produces:
  - `class InsiderFilingInputs(BaseModel, frozen=True, extra="forbid")`: `source: Literal["sec-form345-datasets"]`, `files_sha256: str` (`^[0-9a-f]{64}$`), `quarters_from: str`, `quarters_through: str` (both `^\d{4}q[1-4]$`; validator: `quarters_from <= quarters_through` by `parse_quarter`), `buy_window: int = Field(ge=1)`, `net_window: int = Field(ge=1)`, `owner_roles: tuple[str, ...] = Field(min_length=1)`, `exclude_10b5_1: bool`.
  - `class InsiderInputs(BaseModel, frozen=True, extra="forbid")`: `fields_version: Literal["insider_fields_v1"]`, `insider_filings: InsiderFilingInputs`, `corporate_actions: CorporateActionInputs`.
  - `class InsiderCampaignProtocol(CampaignProtocol, frozen=True, extra="forbid")`: `id: Literal["pooled-insider-campaign"]`, `score_inputs: Literal["daily"] = Field(default="daily", exclude=False)`, `insider_inputs: InsiderInputs`, validator refusing `search_mode != "fixed_set"` with `"an insider protocol is fixed_set in v1: a genetic search on insider fields needs check B and its own spec"`.
  - `_PROTOCOL_CLASSES["pooled-insider-campaign"] = InsiderCampaignProtocol`.
  - `insider_inputs_document(protocol: CampaignProtocol) -> dict | None` in `scoring.py`.
  - `mini_insider_protocol(**update)`: the mini fixed-set document with `id`, `score_inputs="daily"`, `insider_inputs={"fields_version": "insider_fields_v1", "insider_filings": {"source": "sec-form345-datasets", "files_sha256": "e" * 64, "quarters_from": "2015q1", "quarters_through": "2026q3", "buy_window": 63, "net_window": 126, "owner_roles": ["Officer", "Director"], "exclude_10b5_1": True}, "corporate_actions": {...the same block as the short mini...}}`.

- [ ] **Step 1: Write the failing tests** mirroring the short-volume tests in `test_campaign_protocol.py`: `test_insider_protocol_loads_dumps_its_inputs_and_refuses_genetic` (also: `quarters_from` after `quarters_through` raises `ValueError`), `test_insider_protocol_leaves_other_protocol_hashes_unchanged` (events-v1, intraday-v1, campaign-v4, short-v1 prefixes equal their pinned values; `insider_inputs` absent from their dumps).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement the models, dispatch entry, `insider_inputs_document` and the mini protocol.**
- [ ] **Step 4: Run `uv run pytest tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_short_v1.py tests/research/pooled/test_events_v1.py -q`** — pass; mypy clean on `campaign.py`, `scoring.py`.
- [ ] **Step 5: Stage and report.**

---

### Task 3: Transaction reader, `insider_fields_v1` builder and DSL registration

**Files:**
- Modify: `agentic_trader/research/insider_filings.py` (add the reader)
- Create: `agentic_trader/research/pooled/insider_fields.py`
- Modify: `agentic_trader/research/alpha/dsl.py:34-35` (`FIELD_UNITS` gains the six names as `(0.0, 0.0)` under a comment naming `insider_fields_v1`)
- Test: `tests/research/test_insider_filings.py` (reader), `tests/research/pooled/test_insider_fields.py`

**Interfaces:**
- Consumes: `InsiderInputs`, `InsiderFilingInputs` (Task 2); `InsiderFilingStore`, `files_sha256`, `quarters_between`, `parse_quarter` (Task 1); `CorporateAction`, `identity_breaks(actions, symbols) -> dict[str, date]` from `events.py`; `by_session` from `research/apriori/pead_events.py`.
- Produces:
  - `@dataclass(frozen=True) class InsiderTransaction(symbol: str, filing_date: date, trans_date: date, code: Literal["P", "S"], dollars: float, owner_cik: int)`.
  - `read_transactions(archive: Path, *, cik_to_symbols: Mapping[int, tuple[str, ...]], owner_roles: Sequence[str], exclude_10b5_1: bool) -> tuple[tuple[InsiderTransaction, ...], dict[str, int], int]` in `insider_filings.py` — the transactions for the cohort's CIKs (one per symbol the CIK fans out to), the drop counts keyed `document_type, code, acquired_disposed, owner_role, plan_10b5_1, non_positive, trade_after_filing`, and `unscored_rows` (qualifying rows whose issuer CIK is not in the map). Dates parsed from `DD-MON-YYYY`.
  - `symbol_ciks(tickers: Mapping, symbols: Sequence[str]) -> tuple[dict[int, tuple[str, ...]], tuple[str, ...]]` — the CIK map restricted to the cohort and the symbols without a CIK.
  - `INSIDER_FIELDS = ("insider_net_ratio", "insider_buy_rel", "insider_sell_rel", "insider_buyers", "insider_buy_age", "insider_opp_buy_rel")`.
  - `@dataclass(frozen=True) class LoadedInsiderFilings`: `by_symbol: Mapping[str, tuple[InsiderTransaction, ...]]`, `files_sha256: str`, `quarters: tuple[str, ...]`, `rows: int`, `unscored_rows: int`, `drops: Mapping[str, int]`, `symbols_without_cik: tuple[str, ...]`.
  - `load_insider_filings(directory: Path, inputs: InsiderFilingInputs, symbols: Sequence[str]) -> LoadedInsiderFilings` — disk only; every quarter in `[quarters_from, quarters_through]` must be present (`ValueError("insider archive for <quarter> is missing")`), the ticker map too; computes `files_sha256`.
  - `verify_insider_sources(inputs: InsiderInputs, loaded: LoadedInsiderFilings, actions_sha256: str) -> None` — messages name `insider_inputs.insider_filings.files_sha256` / `insider_inputs.corporate_actions.content_sha256`.
  - `@dataclass(frozen=True) class InsiderCoverage`: `symbols_without_cik`, `symbols_without_rows`, `unscored_rows`, `drops`, `identity_breaks: Mapping[str, str]`, `coverage_start: Mapping[str, str | None]`, `first_finite: Mapping[str, dict[str, str | None]]`.
  - `@dataclass(frozen=True) class InsiderFrames`: `frames: Mapping[str, pd.DataFrame]` (naive-midnight `DatetimeIndex` over `trading_days`, the six columns), `coverage: InsiderCoverage`.
  - `build_insider_frames(inputs: InsiderInputs, loaded: LoadedInsiderFilings, actions: Sequence[CorporateAction], raw: Mapping[str, pd.DataFrame], trading_days: Sequence[date], symbols: Sequence[str]) -> InsiderFrames`.

- [ ] **Step 1: Write the failing reader tests** in `tests/research/test_insider_filings.py` using `tests/research/insider_fixtures.py` (`_archive(rows=[...])` builds the three TSVs from row dicts; a helper `_row(code="P", ad="A", shares=100, price=10.0, role="Officer", doc="4", plan="0", filing="05-MAR-2025", trans="03-MAR-2025", cik=1000, owner=7)`):

```python
def test_reader_keeps_open_market_officer_and_director_trades_only(tmp_path):
    # rows: P/A officer (kept), S/D director (kept), P/D (dropped acquired_disposed), M (dropped code), 4/A (dropped document_type),
    # TenPercentOwner-only (dropped owner_role), "Director,Officer" (kept), price 0 (dropped non_positive), trans after filing (dropped)
    txs, drops, unscored = read_transactions(path, cik_to_symbols={1000: ("AAA",)}, owner_roles=("Officer", "Director"), exclude_10b5_1=True)
    assert [t.code for t in txs] == ["P", "S", "P"] and txs[0].dollars == pytest.approx(1000.0)
    assert drops == {"document_type": 1, "code": 1, "acquired_disposed": 1, "owner_role": 1, "plan_10b5_1": 0, "non_positive": 1, "trade_after_filing": 1}

def test_reader_drops_plan_trades_only_when_asked_and_reads_archives_without_the_column(tmp_path):
    # plan="1" row dropped with exclude_10b5_1=True, kept with False; an archive whose SUBMISSION header lacks AFF10B5ONE reads and drops["plan_10b5_1"] == 0

def test_reader_fans_a_shared_cik_out_and_counts_unmapped_issuers(tmp_path):
    # cik 2000 → ("GOOG", "GOOGL"): one P row yields two transactions; cik 3000 not in the map → unscored == 1

def test_symbol_ciks_maps_tickers_and_reports_missing(tmp_path):
    # tickers {"0": {"cik_str": 1, "ticker": "BF-B", ...}} → {1: ("BF.B",)}; "ZZZZ" without a CIK reported
```

- [ ] **Step 2: Write the failing builder tests** in `tests/research/pooled/test_insider_fields.py` (a 300-trading-day world with `tests.research.pooled.test_formula.weekdays`; helper `_tx(symbol, filing, code, dollars, owner, trans=None)`; raw bars with constant `Close × Volume = 1,000,000` so `DV_T` is 1e6 from day 20):

```python
def test_a_filing_counts_on_its_filing_session_and_not_before():
    # a P of $50,000 filed on day 30: insider_buy_rel is NaN on day 29 (before coverage), 0.05 on day 30 and day 92 (63 sessions), 0 on day 93

def test_a_filing_dated_on_a_holiday_counts_from_the_next_session():
    # filing_date a Saturday → counted on the following Monday

def test_net_ratio_is_nan_without_activity_and_plus_or_minus_one_with_one_side():
    # only purchases → +1; only sales → -1; $30k P and $10k S → 0.5; nothing in 126 sessions → NaN

def test_sell_rel_and_buy_rel_are_zero_once_covered_and_nan_before():
    # first filing is an S on day 40: insider_buy_rel NaN before day 40, 0 on day 40; insider_sell_rel 0 after day 102

def test_buyers_counts_distinct_owners_across_filings():
    # owner 7 buys twice, owner 8 once within 63 sessions → 2; a joint accession gives one owner_cik (reader rule)

def test_buy_age_counts_sessions_since_the_last_purchase_filing():
    # P filed day 10 → 0 on day 10, 5 on day 15; another P day 20 → 0; NaN before day 10; sales do not reset it

def test_routine_purchases_leave_the_opportunistic_field():
    # owner 7 buys in March 2017 and March 2018 → the 2018 purchase is routine: insider_opp_buy_rel 0, insider_buy_rel > 0; a purchase in April 2018 is opportunistic

def test_never_filing_issuer_is_nan_not_zero():
    # a symbol with no transactions: all six columns NaN everywhere; symbols_without_rows lists it

def test_dollar_volume_denominator_is_the_20_session_median_and_nan_without_bars():
    # Close×Volume doubles from day 100: rel halves from day 120; a symbol without a raw frame → buy_rel NaN even when covered

def test_identity_break_blanks_fields_before_the_rename():
    # name_change FB→META effective day 150: META's six fields NaN before day 150, finite after (rows kept)

def test_frames_align_with_prepare_daily_index()
def test_load_insider_filings_is_offline_and_digests(tmp_path)
def test_load_insider_filings_fails_closed_on_a_missing_quarter_or_ticker_map(tmp_path)
def test_verify_insider_sources_names_the_mismatching_digest()
```

- [ ] **Step 3: Run both files to verify failure.**
- [ ] **Step 4: Implement the reader and `insider_fields.py`.** Per symbol: map each transaction's `filing_date` to a session index with `np.searchsorted(trading_days, filing_date, side="left")` (a non-session rolls forward); build per-session arrays of purchase dollars, opportunistic purchase dollars and sale dollars; window sums as `cumsum` differences over `buy_window`/`net_window`; `insider_buyers` by a sliding window over the sorted `(session, owner)` purchase events with an owner-count dict; `insider_buy_age` as `T − last purchase session` via forward fill; the routine set is `{(owner_cik, filing_year, filing_month)}` over the symbol's purchases; `DV_T` per Global Constraints from the raw frame reindexed through `by_session`; coverage start per symbol is the first session with any transaction; identity breaks blank rows before `break_on`.
- [ ] **Step 5: Add the six names to `FIELD_UNITS`** and a test that `compile_expression("insider_buy_rel / (insider_buyers > 1)")` compiles under `require_dimensionless` and `evaluate_prepared` on a frame without the columns fails closed with `Missing required fields`.
- [ ] **Step 6: Run `uv run pytest tests/research/test_insider_filings.py tests/research/pooled/test_insider_fields.py tests/research/alpha -q`; mypy clean on the three modules.**
- [ ] **Step 7: Stage and report.**

---

### Task 4: Runner insider sources, `CubeBuild.insider` and score-book dispatch

**Files:**
- Modify: `agentic_trader/research/pooled/runner.py` (`ShortSources` neighbourhood lines ~95-140; `CubeBuild` ~140-152; `build_cube_inputs` ~243-310)
- Modify: `agentic_trader/research/pooled/scoring.py` (`score_book` dispatch ~312-350; `insider_coverage_document` beside `short_coverage_document` ~307)
- Modify: `tests/research/pooled/mini_world.py` (`insider_frames(seed=5)`, `INSIDER_FRAMES`, `insider_cube_build(cube)` beside the short equivalents)
- Test: `tests/research/pooled/test_runner.py`, `tests/research/pooled/test_scoring.py`

**Interfaces:**
- Consumes: `INSIDER_FIELDS`, `load_insider_filings`, `verify_insider_sources`, `build_insider_frames`, `InsiderFrames` (Task 3); `InsiderCampaignProtocol`, `InsiderInputs` (Task 2); `InsiderFilingStore`, `files_sha256`, `quarters_between`, `parse_quarter` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class InsiderSources: insider_filings_dir: Path; actions_file: Path`
  - `load_insider_sources(inputs: InsiderInputs, sources: InsiderSources, symbols: Sequence[str]) -> tuple[LoadedInsiderFilings, tuple[CorporateAction, ...]]` — loads, verifies both digests, returns (no calendar needed).
  - `verify_insider_digests(inputs: InsiderInputs, sources: InsiderSources) -> None` — reads every archive and the ticker map through the store (sha verified) and compares the files digest and the actions digest; messages as `verify_short_digests`.
  - `build_cube_inputs(..., events=None, short=None, insider: tuple[InsiderInputs, InsiderSources] | None = None)`; more than one of the three raises `ValueError("a build carries one field source, not several: pass one of events=, short= or insider=")` (update the existing events+short test's match); raw bars are loaded when any is given; `CubeBuild.insider: InsiderFrames | None = None`, built for eligible symbols only, after the bars (cache-hit path included); the digest check runs before any bar fetch.
  - `score_book` dispatches `InsiderCampaignProtocol` → `ScoreBook.with_fields(built.adjusted, built.insider.frames, INSIDER_FIELDS, ...)`, raising `ValueError("this protocol scores insider fields but the build carries no insider frames; build with insider=...")` when `built.insider is None`.
  - `insider_coverage_document(built: CubeBuild) -> dict | None` in `scoring.py`.

- [ ] **Step 1: Write the failing scoring test** `test_with_fields_reads_insider_columns_at_d_minus_one` using `INSIDER_FRAMES` and `insider_buy_rel / (insider_buyers > 1)` (NaN out of condition, finite in it), and `test_score_book_refuses_an_insider_protocol_without_insider_frames`.
- [ ] **Step 2: Write the failing runner tests** mirroring `_short_world`: `_insider_world(tmp_path, pinned: bool)` writes an `InsiderFilingStore` with one small archive per quarter of a two-quarter window plus a ticker map and an `actions.json`; tests: an unpinned digest fails before any bar fetch (`bars.calls == 0`); a pinned build has `built.insider.frames` for exactly the eligible symbols and `built.raw` populated; `test_insider_build_loads_raw_bars_on_a_cache_hit`; `events` and `insider` together raise; `verify_insider_digests` refuses a tampered archive body.
- [ ] **Step 3: Run to verify failure.**
- [ ] **Step 4: Implement** the sources, loader, verifier, the `insider=` branch of `build_cube_inputs`, `CubeBuild.insider`, the `score_book` dispatch, `insider_coverage_document`, and the mini-world helpers (`insider_frames` has purchases on a few names so the conditional seeds are finite somewhere in the discovery window).
- [ ] **Step 5: Run `uv run pytest tests/research/pooled/test_scoring.py tests/research/pooled/test_runner.py tests/research/pooled/test_short_v1.py -q`** — pass; mypy clean.
- [ ] **Step 6: Stage and report.**

---

### Task 5: Manifests, frozen documents and results carry `insider_inputs` and `insider_coverage`

**Files:**
- Modify: `agentic_trader/research/pooled/campaign_run.py` (~246-248, ~381, ~597-600), `power.py` (~338, ~370), `null_check.py` (~270, ~309)
- Test: `tests/research/pooled/test_campaign_run.py`, `tests/research/pooled/test_power.py`, `tests/research/pooled/test_null_check.py`

**Interfaces:**
- Consumes: `insider_inputs_document`, `insider_coverage_document` (Tasks 2, 4); `insider_cube_build`, `mini_insider_protocol`.
- Produces: every manifest and `result.json` of checks A and C and the campaign carries `insider_inputs` (`null` for other protocols) next to `short_inputs`, and `insider_coverage` next to `short_coverage`; the frozen formula body gains `{"score_inputs": ..., "insider_inputs": ...}` for an insider protocol.

- [ ] **Step 1: Write the failing tests** mirroring the short-volume ones: `test_frozen_document_binds_insider_inputs` (the same text under an insider protocol, a short protocol and the plain mini protocol gives three identities); `test_an_insider_campaign_result_carries_its_insider_inputs_and_coverage`; in `test_power.py` and `test_null_check.py` the manifest of an insider run has `insider_inputs == protocol.insider_inputs.model_dump(mode="json")` and `insider_coverage == asdict(built.insider.coverage)`, and a short run has `insider_inputs is None`.
- [ ] **Step 2: Run to verify failure.** **Step 3: Implement the insertions.** **Step 4: Run the three test files** — pass. **Step 5: Stage and report.**

---

### Task 6: CLI field sources: `--insider-filings` on power, null-check and campaign

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (`_pooled_field_sources` lines ~1652-1680; `_SHORT_VOLUME_OPTION` ~1695; `_pooled_build` ~1706; the three commands' options and calls ~1751-1770, ~1947-1964, ~2041-2103; the campaign's pre-ledger verification at ~2090)
- Test: `tests/cli/test_alpha_pooled_cli.py`

**Interfaces:**
- Consumes: `InsiderSources`, `load_insider_sources`, `verify_insider_digests` (Task 4); `InsiderCampaignProtocol` (Task 2).
- Produces:
  - `_INSIDER_FILINGS_OPTION = click.option("--insider-filings", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None, help="The alpha insider-filings acquisition's cache directory (insider protocols only).")`
  - `_pooled_field_sources(protocol, earnings_pages, corporate_actions, short_volume, insider_filings) -> tuple[events | None, short | None, insider | None]`; new refusals `"--insider-filings and --corporate-actions are required for an insider protocol"`, `"--insider-filings applies only to an insider protocol"`; an insider protocol refuses `--earnings-pages` and `--short-volume` with the existing messages; `_pooled_build` gains `insider=`; the campaign calls `verify_insider_digests` before the ledger opens, as it does `verify_short_digests`.

- [ ] **Step 1: Write the failing CLI tests** mirroring lines ~733-830: insider protocol without `--insider-filings` → refused; with `--short-volume` → refused; with `--earnings-pages` → refused; a short protocol with `--insider-filings` → refused; a plain protocol with `--insider-filings` → refused; an insider protocol with both options passes `insider=(inputs, InsiderSources(...))` into the build; the campaign verifies the insider digests before opening the ledger (a tampered archive refuses with the files-digest message and the ledger has no events).
- [ ] **Step 2: Run to verify failure.** **Step 3: Implement.** **Step 4: Run `uv run pytest tests/cli/test_alpha_pooled_cli.py tests/cli/test_cli_alpha.py -q`** — pass; mypy clean. **Step 5: Stage and report.**

---

### Task 7: Frozen protocols `insider-v1` and `cohort3-insider-v1`, pin test and docs

Precondition (controller): `alpha insider-filings --from 2015q1 --through 2026q3` has run with the operator's `EDGAR_USER_AGENT`, outside the scan windows, and its manifest `files_sha256` is in the task brief, with the two corporate-actions digests (`776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344` for cohort v2; `8a547b0694d9f171fa1fa7e39f492749f6468393426cf1a69de8a0bde0386c8e` for cohort v3).

**Files:**
- Create: `config/research/pooled/insider-v1.json`, `config/research/pooled/cohort3-insider-v1.json`
- Create: `tests/research/pooled/test_insider_v1.py`
- Modify: `docs/alpha-pooled-mining.md` (new section "Insider lane (`insider_fields_v1`)" after the short-volume section, plus the Commands block), `docs/alpha-data-augmentation-2026-10-18.md` (§4 item 3: lane #2 built), `docs/alpha-roadmap.md` (October 20 status), `CLAUDE.md` (one sentence after the short-volume sentence)

**Interfaces:**
- Produces: the two frozen files; `test_insider_v1.py` mirrors `test_short_v1.py`: pins each file's SHA-256 prefix (filled in after freezing), both are `InsiderCampaignProtocol`, `fixed_set`, eight families with these exact seeds in this order:

```python
EXPECTED_SCORES = {
    "net_buying": "insider_net_ratio",
    "buy_pressure": "insider_buy_rel / (insider_buy_rel > 0)",
    "opportunistic_buying": "insider_opp_buy_rel / (insider_opp_buy_rel > 0)",
    "cluster_buying": "insider_buy_rel / (insider_buyers > 1)",
    "fresh_purchase": "-insider_buy_age",
    "recent_buy_pressure": "insider_buy_rel / (insider_buy_age <= 10)",
    "buying_into_weakness": "insider_buy_rel / (ts_sum(returns, 20) < 0)",
    "light_selling": "-insider_sell_rel / (insider_sell_rel > 0)",
}
```

  plus: every seed passes `require_dimensionless`; `insider-v1` shares the `SHARED` attributes of `test_short_v1.py` with `short-v1.json`, `cohort3-insider-v1` with `cohort3-short-v1.json`; `insider_inputs.insider_filings` equals the pinned block; `excluded_families` ⊇ `cohort3-short-v1`'s list ∪ the eight short-v1 family ids; the frozen identity of `"insider_net_ratio"` under `insider-v1` differs from the same text under `short-v1`; the conditional seeds evaluate to `NaN` out of condition on `INSIDER_FRAMES` through `evaluate_prepared`.

- [ ] **Step 1: Write the two protocol files** by copying `short-v1.json` / `cohort3-short-v1.json`, replacing `id`, `title`, `rationale` (one paragraph: the eight hypotheses, filing-date visibility, Form 4 open-market officer/director trades only, 10b5-1 exclusion, CIK mapping, zeros once covered, digests pinned, and that every discovery block carries a score because the filings start in 2015), `short_inputs` → `insider_inputs`, `families` (the eight above, `mutation_operators: []`, one-sentence `rationale` each from the spec table), and `excluded_families` extended with the eight short-v1 ids (`reason`: `"short-volume fixed set, scored in short-v1 / cohort3-short-v1 (no finalist)"`).
- [ ] **Step 2: Write the failing pin test** with placeholder prefixes; run; fill in the two real prefixes; re-run to pass.
- [ ] **Step 3: Docs**: the four files above; `docs/alpha-pooled-mining.md` gets the source and caveats, the acquisition command (with `EDGAR_USER_AGENT`), the field table (spec §3), the formula table (spec §5), the protocol block and the pre-registered closure rule (spec §7); CLAUDE.md gets one sentence.
- [ ] **Step 4: Run `uv run pytest tests/research/pooled -q`** — pass.
- [ ] **Step 5: Stage and report.**
