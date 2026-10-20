# Pooled Short-Volume Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add FINRA Reg SHO daily short-sale volume as a digest-pinned research acquisition and four `short_fields_v1` score fields on the cached pooled cubes, with a fixed-set protocol `pooled-short-volume-campaign` frozen for cohorts v2 and v3.

**Architecture:** A new offline acquisition (`research/short_volume.py`, CLI `alpha short-volume`) caches one FINRA file per NYSE session with per-file SHA-256 and a files digest. A new field builder (`research/pooled/short_fields.py`) turns the cache plus the cohort's corporate actions and raw bars into per-symbol frames; the pooled runner builds them beside the cube exactly as it builds event frames, the score book merges them into the daily prepared frames through a generalised `with_fields`, and a new protocol subclass pins the digests so a short-volume formula never shares an identity with a bar-only one.

**Tech Stack:** Python 3.14, pydantic v2 (frozen, `extra="forbid"`), httpx, pandas/numpy, click, pytest; ruff strict (no `dict()` literal calls, no function-level imports), mypy.

**Spec:** `docs/superpowers/specs/2026-10-20-pooled-short-volume-design.md`

## Global Constraints

- Research only: no database, registry, broker, Telegram or promotion credit anywhere in this plan.
- FINRA URL pattern `https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt`; file header `Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`; volumes are non-negative decimals (fractional shares appear in 2026 files).
- Acquisition requests NYSE sessions only, at most 4 requests per second, 20 s timeout, 8 MiB per-file byte budget, one retry on a transport error, idempotent GETs only.
- Symbol mapping: FINRA `/` → Alpaca `.`; rows with `p` (preferred), `/WS`, `/U` never match a cohort name.
- Field names: `short_ratio`, `short_ratio_z`, `short_volume_rel`, `offex_share`; `fields_version` literal `"short_fields_v1"`; protocol id `"pooled-short-volume-campaign"`; `short_inputs.short_volume.source` literal `"finra-regsho-cnms"`.
- Protocol defaults pinned in the frozen files: `z_window` 20, `min_total_volume` 10000, `max_offex_share` 1.5, `files_from` 2018-08-01, `files_through` 2026-09-01.
- Trailing statistics exclude T (`T-20 … T-1`), require `z_window` finite prior values, `std = 0` ⇒ `NaN`; fields are never carried forward across missing rows.
- Identity breaks reuse the events lane's rule: `identity_breaks` (public rename of `events._identity_breaks`), fields `NaN` on `T < break_on`, pre-break rows discarded.
- `search_mode` must be `fixed_set`; base-class, intraday and events protocol hashes and identities are unchanged.
- Existing test fixtures stay valid: `tests/research/pooled/mini_world.py` gains a short world beside the events world; no fixture is removed.
- Pre-commit runs ruff, mypy and the impacted pytest; commit a task's tests with its implementation.

## Review Focus

1. A FINRA file whose rows carry a `Date` other than the file's date (a re-posted or mis-named file) must fail acquisition and be kept on disk, never silently merged into another session. → Task 1 test `test_parse_file_rejects_rows_dated_another_day`.
2. A session whose file is absent inside the window (a FINRA outage) must fail closed unless explicitly allowed, and an allowed absence must appear in the manifest, so a missing day cannot quietly become `NaN` fields. → Task 1 test `test_absent_session_fails_unless_allowed_and_is_recorded`.
3. A symbol whose row is missing on one day must have `NaN` fields that day and a baseline on the next day that skips the gap (no forward fill, no shortened window), so a thinly reported name cannot look like a steady one. → Task 3 test `test_missing_row_is_nan_and_the_baseline_skips_it`.
4. A corrected FINRA file against a stale bar can put `TotalVolume` above the raw bar volume; `offex_share` above `max_offex_share` must become `NaN` and be counted, not scored. → Task 3 test `test_offex_share_above_the_cap_is_nan_and_counted`.
5. A cached cube built without raw bars (an old cache) must still carry raw frames when a short protocol asks for them, otherwise `offex_share` is silently all-`NaN`. → Task 4 test `test_short_build_loads_raw_bars_on_a_cache_hit`.

---

### Task 1: FINRA acquisition module and `alpha short-volume` command

**Files:**
- Create: `agentic_trader/research/short_volume.py`
- Create: `agentic_trader/cli/commands/alpha_short_volume.py`
- Modify: `agentic_trader/cli/main.py` (import and `alpha_group.add_command` beside `corporate_actions_cmd`/`news_cmd`, lines ~10-13 and the alpha registrations)
- Test: `tests/research/test_short_volume.py`, `tests/cli/test_alpha_short_volume_cli.py`

**Interfaces:**
- Consumes: `agentic_trader.market.session.DeterministicCalendarProvider().get_calendar_range(start, end)` (async, offline, zero-dependency: NYSE statutory holidays via `MarketHolidayCalendar`); the sessions are the days with `is_trading_day`. `acquire_short_volume` takes the resulting `sessions: Sequence[date]`; the CLI derives them.
- Produces:
  - `ShortVolumeRow(date: date, symbol: str, short_volume: float, short_exempt_volume: float, total_volume: float, market: str)` (frozen dataclass; `symbol` already mapped `/`→`.`)
  - `parse_file(text: str, expected_date: date) -> tuple[ShortVolumeRow, ...]` — raises `ValueError` on a bad header, a row dated another day, a negative or non-numeric volume, or fewer than 1,000 rows.
  - `class ShortVolumeStore(directory: Path)`: `paths(day) -> tuple[Path, Path]` (`{YYYY-MM-DD}.txt`, `{YYYY-MM-DD}.meta.json`), `load(day) -> str | None` (verifies sha256 against the meta, raises on mismatch), `meta(day) -> dict | None`, `save(day, body: bytes, *, requested_at, received_at, status: Literal["present","absent"], rows: int)`.
  - `FINRA_DAILY_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{stamp}.txt"`, `MAX_FILE_BYTES = 8 * 1024 * 1024`, `REQUESTS_PER_SECOND = 4`.
  - `async def acquire_short_volume(client: httpx.AsyncClient, sessions: Sequence[date], store: ShortVolumeStore, *, allow_absent: Sequence[date] = (), pace: Callable[[], Awaitable[None]]) -> dict` — the manifest: `window`, `sessions_requested`, `sessions_present`, `sessions_absent` (list), `sessions_reused`, `files_sha256`, `bytes_total`, `files` (sorted `"<date>:<sha256>"` lines).
  - `files_sha256(store: ShortVolumeStore, sessions: Sequence[date]) -> str` — SHA-256 of the newline-joined sorted `"<date>:<sha256>"` lines over the present sessions; raises `ValueError` on a session with no meta.
  - CLI `copilot alpha short-volume --from --through --cache DIR --output DIR [--allow-absent YYYY-MM-DD ...]`; writes `output/manifest.json` with `environment` from `research_environment()`; refuses an existing output directory.

- [ ] **Step 1: Write the failing parser and store tests**

```python
HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"


def _file(day="20260916", rows=1000, extra=""):
    body = HEADER + "".join(f"{day}|S{i:04d}|{i}|0|{2 * i + 1}|B,Q,N\n" for i in range(rows)) + extra
    return body


def test_parse_file_maps_share_classes_and_keeps_fractional_volumes():
    rows = parse_file(
        _file(extra="20260916|BF/B|593898.756091|2493|918845.996289|B,Q,N\n20260916|BFHpA|1|0|2|Q\n"), date(2026, 9, 16)
    )
    by = {r.symbol: r for r in rows}
    assert by["BF.B"].short_volume == pytest.approx(593898.756091) and by["BF.B"].total_volume == pytest.approx(
        918845.996289
    )
    assert "BFHpA" in by  # kept as served; the field builder never matches it to a cohort name


def test_parse_file_rejects_rows_dated_another_day():
    with pytest.raises(ValueError, match="dated"):
        parse_file(_file(extra="20260915|AAPL|1|0|2|Q\n"), date(2026, 9, 16))


@pytest.mark.parametrize(
    "bad",
    [
        "Date|Symbol|ShortVolume\n",
        _file(rows=999),
        _file(extra="20260916|X|-1|0|2|Q\n"),
        _file(extra="20260916|X|abc|0|2|Q\n"),
    ],
)
def test_parse_file_rejects_bad_header_short_files_and_bad_volumes(bad):
    with pytest.raises(ValueError):
        parse_file(bad, date(2026, 9, 16))


def test_store_round_trips_and_refuses_a_corrupt_file(tmp_path):
    store = ShortVolumeStore(tmp_path)
    store.save(date(2026, 9, 16), b"body", requested_at=NOW, received_at=NOW, status="present", rows=3)
    assert store.load(date(2026, 9, 16)) == "body" and store.meta(date(2026, 9, 16))["rows"] == 3
    store.paths(date(2026, 9, 16))[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        store.load(date(2026, 9, 16))
```

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/research/test_short_volume.py -q`; expected: ImportError.

- [ ] **Step 3: Implement `ShortVolumeRow`, `parse_file`, `ShortVolumeStore` in `agentic_trader/research/short_volume.py`** (private files via `os.open(..., 0o600)` as `alpha_news.py` does; meta JSON keys `sha256, requested_at, received_at, status, bytes, rows`).

- [ ] **Step 4: Run the tests to verify they pass.**

- [ ] **Step 5: Write the failing acquisition tests** (httpx `MockTransport`; the pattern is in `tests/research/apriori/test_earnings_history.py`)

```python
def _transport(files: dict[str, bytes | int]):
    def handler(request):
        stamp = request.url.path.rsplit("CNMSshvol", 1)[1].removesuffix(".txt")
        body = files.get(stamp, 403)
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


async def test_acquisition_requests_sessions_only_reuses_the_cache_and_digests(tmp_path):
    sessions = (date(2026, 9, 14), date(2026, 9, 15))  # Mon, Tue; no weekend stamp is ever requested
    seen = []
    ...  # handler records request paths
    manifest = await acquire_short_volume(client, sessions, store, pace=_noop)
    assert manifest["sessions_present"] == 2 and manifest["sessions_absent"] == []
    assert manifest["files_sha256"] == files_sha256(store, sessions)
    again = await acquire_short_volume(client, sessions, store, pace=_noop)
    assert again["sessions_reused"] == 2 and len(seen) == 2


async def test_absent_session_fails_unless_allowed_and_is_recorded(tmp_path):
    with pytest.raises(ValueError, match="absent"):
        await acquire_short_volume(client, sessions, store, pace=_noop)  # 2026-09-15 → 403
    manifest = await acquire_short_volume(client, sessions, store, allow_absent=[date(2026, 9, 15)], pace=_noop)
    assert manifest["sessions_absent"] == ["2026-09-15"] and store.meta(date(2026, 9, 15))["status"] == "absent"


async def test_invalid_file_is_kept_on_disk_and_fails(tmp_path):
    # body with rows dated another day → parse_file raises; the .txt exists; no meta "present"
    ...


async def test_byte_budget_fails_closed(tmp_path):
    # a 9 MiB body → ValueError("byte budget")
    ...
```

- [ ] **Step 6: Run them to verify they fail.**

- [ ] **Step 7: Implement `acquire_short_volume` and `files_sha256`** — stream the response (`client.stream("GET", url)`), count bytes against `MAX_FILE_BYTES`, `await pace()` before each request, retry once on `httpx.TransportError`, 403/404 ⇒ absent, any other status ⇒ `raise_for_status()`; parse before saving as `present`; an invalid file is written to the `.txt` path with status `invalid` meta then `ValueError` raised.

- [ ] **Step 8: Run the acquisition tests to verify they pass.**

- [ ] **Step 9: Write the failing CLI test** in `tests/cli/test_alpha_short_volume_cli.py` (CliRunner; monkeypatch `httpx.AsyncClient` to the mock transport; monkeypatch `research_environment`): the command writes `manifest.json` with `files_sha256`, refuses an existing `--output`, and derives weekday sessions excluding a known holiday (2026-09-07, Labor Day: no request for stamp `20260907`).

- [ ] **Step 10: Implement `alpha_short_volume.py`** (`@click.command("short-volume")`, `@coro`, `--from "start"`, `--through "end"` as `click.DateTime`, `--allow-absent` multiple) and register it in `cli/main.py` next to `news_cmd`; sessions come from `DeterministicCalendarProvider().get_calendar_range(start, end)` filtered on `is_trading_day`.

- [ ] **Step 11: Run `uv run pytest tests/research/test_short_volume.py tests/cli/test_alpha_short_volume_cli.py -q` and `uv run ruff check` + `uv run mypy agentic_trader/research/short_volume.py agentic_trader/cli/commands/alpha_short_volume.py`.** Expected: all pass, clean.

- [ ] **Step 12: Stage** `agentic_trader/research/short_volume.py agentic_trader/cli/commands/alpha_short_volume.py agentic_trader/cli/main.py tests/research/test_short_volume.py tests/cli/test_alpha_short_volume_cli.py` and report (the controller commits).

---

### Task 2: Protocol subclass `ShortVolumeCampaignProtocol` and its documents

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py` (after `EventInputs`, line ~442; `_PROTOCOL_CLASSES` line ~468; `__all__`)
- Modify: `agentic_trader/research/pooled/scoring.py` (beside `event_inputs_document`, line ~284)
- Modify: `tests/research/pooled/mini_world.py` (add `mini_short_protocol(**update)` beside `mini_events_protocol`)
- Test: `tests/research/pooled/test_campaign_protocol.py`

**Interfaces:**
- Produces:
  - `class ShortVolumeInputs(BaseModel, frozen=True, extra="forbid")`: `source: Literal["finra-regsho-cnms"]`, `files_sha256: str` (`^[0-9a-f]{64}$`), `files_from: date`, `files_through: date`, `z_window: int = Field(ge=2)`, `min_total_volume: float = Field(ge=0)`, `max_offex_share: float = Field(gt=0)`.
  - `class ShortInputs(BaseModel, frozen=True, extra="forbid")`: `fields_version: Literal["short_fields_v1"]`, `short_volume: ShortVolumeInputs`, `corporate_actions: CorporateActionInputs`.
  - `class ShortVolumeCampaignProtocol(CampaignProtocol, frozen=True, extra="forbid")`: `id: Literal["pooled-short-volume-campaign"]`, `score_inputs: Literal["daily"] = Field(default="daily", exclude=False)`, `short_inputs: ShortInputs`, validator refusing `search_mode != "fixed_set"` with the message `"a short-volume protocol is fixed_set in v1: a genetic search on short-volume fields needs check B and its own spec"`.
  - `_PROTOCOL_CLASSES["pooled-short-volume-campaign"] = ShortVolumeCampaignProtocol`.
  - `short_inputs_document(protocol: CampaignProtocol) -> dict | None` in `scoring.py` (`model_dump(mode="json")` for the subclass, else `None`).
  - `mini_short_protocol(**update)` in `mini_world.py`: the mini fixed-set document with `id`, `score_inputs="daily"`, `short_inputs={"fields_version": "short_fields_v1", "short_volume": {"source": "finra-regsho-cnms", "files_sha256": "d" * 64, "files_from": "2018-08-01", "files_through": "2026-09-01", "z_window": 20, "min_total_volume": 10000, "max_offex_share": 1.5}, "corporate_actions": {...same block as the events mini...}}`.

- [ ] **Step 1: Write the failing tests** in `test_campaign_protocol.py`, mirroring the events tests near line 328:

```python
def test_short_volume_protocol_loads_dumps_its_inputs_and_refuses_genetic(tmp_path):
    protocol = mini_short_protocol()
    assert isinstance(protocol, ShortVolumeCampaignProtocol) and protocol.score_inputs == "daily"
    dumped = protocol.model_dump(mode="json")
    assert dumped["short_inputs"]["fields_version"] == "short_fields_v1" and dumped["score_inputs"] == "daily"
    assert short_inputs_document(protocol) == dumped["short_inputs"]
    assert short_inputs_document(mini_fixed_set_protocol()) is None
    with pytest.raises(ValueError, match="fixed_set"):
        mini_short_protocol(search_mode="genetic", power_search={...the mini genetic block...})

def test_short_volume_protocol_leaves_other_protocol_hashes_unchanged():
    # load events-v1.json, intraday-v1.json, campaign-v4.json; their sha256 prefixes equal the pinned ones in test_events_v1/test_intraday_v1/test_campaign_v4
    # and "short_inputs" not in any of their dumps
```

- [ ] **Step 2: Run to verify failure** (`ImportError: ShortVolumeCampaignProtocol`).
- [ ] **Step 3: Implement the models, dispatch entry, `short_inputs_document` and the mini protocol.**
- [ ] **Step 4: Run `uv run pytest tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_events_v1.py tests/research/pooled/test_intraday_v1.py -q`** — pass; mypy on `campaign.py`, `scoring.py` clean.
- [ ] **Step 5: Stage and report.**

---

### Task 3: `short_fields_v1` builder and DSL registration

**Files:**
- Create: `agentic_trader/research/pooled/short_fields.py`
- Modify: `agentic_trader/research/pooled/events.py` (rename `_identity_breaks` → `identity_breaks`, add to `__all__`; update the two internal call sites)
- Modify: `agentic_trader/research/alpha/dsl.py:33` (`FIELD_UNITS` gains the four names as `(0.0, 0.0)` under a comment naming `short_fields_v1`)
- Modify: `tests/research/pooled/test_events.py` (the five identity-break tests import the public name)
- Test: `tests/research/pooled/test_short_fields.py`

**Interfaces:**
- Consumes: `ShortInputs` (Task 2); `ShortVolumeStore`, `parse_file`, `files_sha256` (Task 1); `CorporateAction`, `identity_breaks(actions, symbols) -> dict[str, date]`; `by_session` from `research/apriori/pead_events.py`.
- Produces:
  - `SHORT_FIELDS = ("short_ratio", "short_ratio_z", "short_volume_rel", "offex_share")`
  - `@dataclass(frozen=True) class LoadedShortVolume`: `by_symbol: Mapping[str, Mapping[date, ShortVolumeRow]]`, `files_sha256: str`, `sessions: tuple[date, ...]` (present sessions), `absent: tuple[date, ...]`, `rows: int`.
  - `load_short_volume(directory: Path, inputs: ShortVolumeInputs, sessions: Sequence[date]) -> LoadedShortVolume` — disk only; `sessions` is the trading-day calendar restricted to `[files_from, files_through]`; a session with no meta raises `ValueError("short-volume file for <date> is missing")`; `absent` metas are skipped and listed; computes `files_sha256` over the present sessions.
  - `verify_short_sources(inputs: ShortInputs, loaded: LoadedShortVolume, actions_sha256: str) -> None` — messages name `short_inputs.short_volume.files_sha256` / `short_inputs.corporate_actions.content_sha256`.
  - `@dataclass(frozen=True) class ShortCoverage`: `symbols_without_rows: tuple[str, ...]`, `unmatched_rows: int` (file rows matching no cohort symbol, summed), `thin_days: int`, `offex_clipped: int`, `identity_breaks: Mapping[str, str]`, `first_finite: Mapping[str, dict[str, str | None]]`.
  - `@dataclass(frozen=True) class ShortFrames`: `frames: Mapping[str, pd.DataFrame]` (naive-midnight `DatetimeIndex` over `trading_days`, the four columns), `coverage: ShortCoverage`.
  - `build_short_frames(inputs: ShortInputs, loaded: LoadedShortVolume, actions: Sequence[CorporateAction], raw: Mapping[str, pd.DataFrame], trading_days: Sequence[date], symbols: Sequence[str]) -> ShortFrames`.

- [ ] **Step 1: Write the failing builder tests** (build a 60-trading-day world with `tests.research.pooled.test_formula.weekdays`; helper `_rows(symbol, by_day: dict[date, tuple[short, total]])`):

```python
def test_short_ratio_is_the_file_ratio_and_nan_on_thin_days():
    # total 50,000 / short 20,000 → 0.4; total 9,999 → NaN and thin_days == 1

def test_short_ratio_z_uses_only_prior_sessions_and_needs_twenty():
    # constant 0.4 for 20 days then 0.6 on day 21: z on day 21 is NaN (std 0); then a varying baseline: z on day 22 computed from days 2..21 only; days 1..20 NaN

def test_short_volume_rel_is_short_over_the_prior_mean():
    # prior 20 shorts all 1000, day 21 short 1500 → 1.5; zero prior mean → NaN

def test_missing_row_is_nan_and_the_baseline_skips_it():
    # drop day 10's row: fields NaN on day 10; on day 22 the window is days 2..21 minus day 10 → only 19 finite → NaN; on day 23 it is 20 finite → finite

def test_offex_share_above_the_cap_is_nan_and_counted():
    # total 150,000 vs raw Volume 100,000 → 1.5 kept; 160,000 → NaN, offex_clipped == 1; raw Volume 0 → NaN; no raw frame → all NaN

def test_identity_break_blanks_fields_before_the_rename_and_drops_pre_break_rows():
    # name_change FB→META effective day 30: META's fields NaN before day 30; rows keyed "META" dated before day 30 are not used for the baseline

def test_symbol_without_rows_is_all_nan_and_reported_and_unmatched_rows_are_counted():
    # a file row for "ZZZZ" (not in symbols) → unmatched_rows == n_days; "BFHpA" never matches

def test_frames_align_with_prepare_daily_index():
    # frame.index equals pd.DatetimeIndex of trading_days (naive midnight), like EVENT_FRAMES

def test_load_short_volume_is_offline_skips_absent_and_digests(tmp_path):
    # store with 3 present + 1 absent session → sessions/absent as expected; files_sha256 == files_sha256(store, present)

def test_load_short_volume_fails_closed_on_a_missing_session(tmp_path)
def test_verify_short_sources_names_the_mismatching_digest()
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement `short_fields.py`**; the rolling prior-window statistics may use `pd.Series.shift(1).rolling(z_window, min_periods=z_window)`; `identity_breaks` imported from `events.py` after the rename; `offex_share = total / raw_volume` with the raw frame's `Volume` column reindexed to `trading_days` via `by_session`.
- [ ] **Step 4: Add the four names to `FIELD_UNITS` in `dsl.py`** and a test in `test_short_fields.py` that `compile_expression("-short_ratio_z / (ts_sum(returns, 5) > 0)")` compiles under `require_dimensionless` and `evaluate_prepared` on a frame without the columns fails closed with `Missing required fields`.
- [ ] **Step 5: Rename `_identity_breaks` → `identity_breaks`** in `events.py` and its tests; run `uv run pytest tests/research/pooled/test_events.py tests/research/pooled/test_short_fields.py tests/research/alpha -q`; mypy clean on the three modules.
- [ ] **Step 6: Stage and report.**

---

### Task 4: Score book `with_fields`, runner short sources and `CubeBuild.short`

**Files:**
- Modify: `agentic_trader/research/pooled/scoring.py` (`with_events` → `with_fields`, lines ~178-205; `score_book` dispatch, lines ~300-330)
- Modify: `agentic_trader/research/pooled/runner.py` (`EventSources` neighbourhood lines ~60-90; `build_cube_inputs` signature and the events block, lines ~181-370)
- Modify: `tests/research/pooled/mini_world.py` (`short_frames(seed) -> dict[str, pd.DataFrame]`, `SHORT_FRAMES`, `short_cube_build(cube)`)
- Modify: `tests/research/pooled/test_scoring.py`, `tests/research/pooled/test_events_v1.py` (call sites of `with_events`)
- Test: `tests/research/pooled/test_scoring.py`, `tests/research/pooled/test_runner.py`

**Interfaces:**
- Consumes: `SHORT_FIELDS`, `load_short_volume`, `verify_short_sources`, `build_short_frames`, `ShortFrames` (Task 3); `ShortVolumeCampaignProtocol`, `ShortInputs` (Task 2); `ShortVolumeStore`, `files_sha256` (Task 1).
- Produces:
  - `ScoreBook.with_fields(cls, adjusted, frames: Mapping[str, pd.DataFrame], field_names: Sequence[str], trading_days, sessions, symbols, *, capacity=DEFAULT_CAPACITY) -> ScoreBook` — replaces `with_events` (no alias; the events call site in `score_book` passes `EVENT_FIELDS`).
  - `@dataclass(frozen=True) class ShortSources: short_volume_dir: Path; actions_file: Path`
  - `load_short_sources(inputs: ShortInputs, sources: ShortSources, trading_days: Sequence[date]) -> tuple[LoadedShortVolume, tuple[CorporateAction, ...]]` — loads, verifies both digests, returns. Note the calendar dependency: unlike the events loader it needs `trading_days`; in `build_cube_inputs` the digest check therefore runs **right after** the calendar is read and **before** any bar fetch (the first provider access is the calendar itself, which is unavoidable; record this ruling in the report).
  - `build_cube_inputs(..., events=None, short: tuple[ShortInputs, ShortSources] | None = None)`; passing both `events` and `short` raises `ValueError`; raw bars are loaded when either is given; `CubeBuild.short: ShortFrames | None = None`, built for eligible symbols only, after the bars (cache-hit path included).
  - `score_book` dispatches `ShortVolumeCampaignProtocol` → `ScoreBook.with_fields(built.adjusted, built.short.frames, SHORT_FIELDS, ...)`, raising `ValueError("this protocol scores short-volume fields but the build carries no short frames; build with short=...")` when `built.short is None`.
  - `short_coverage_document(built: CubeBuild) -> dict | None` in `scoring.py` beside `event_coverage_document`.

- [ ] **Step 1: Write the failing scoring tests** — rename the three `with_events` tests to `with_fields` and add `test_with_fields_reads_short_columns_at_d_minus_one` using `SHORT_FRAMES` and the expression `-short_ratio_z / (ts_sum(returns, 5) > 0)` (NaN out of window, finite in window).
- [ ] **Step 2: Write the failing runner tests** in `test_runner.py`, mirroring `_event_world` (lines ~440-520): `_short_world(tmp_path, pinned: bool)` writes a `ShortVolumeStore` with files for every trading day in a short window plus an `actions.json`; tests: an unpinned digest fails before any bar fetch (`bars.calls == 0` after the calendar read); a pinned build has `built.short.frames` for exactly the eligible symbols and `built.raw` populated; **`test_short_build_loads_raw_bars_on_a_cache_hit`**: build once without `short`, then again with `short` on the cached cube → `built.raw` non-empty and `built.short` present; `events` and `short` together raise.
- [ ] **Step 3: Run to verify failure.**
- [ ] **Step 4: Implement** `with_fields`, `ShortSources`, `load_short_sources`, the `short=` branch of `build_cube_inputs` (mirror the events block at lines ~320-355 without the benchmark requirement), `CubeBuild.short`, `score_book` dispatch, `short_coverage_document`; add `short_frames`/`SHORT_FRAMES`/`short_cube_build` to `mini_world.py`.
- [ ] **Step 5: Run `uv run pytest tests/research/pooled/test_scoring.py tests/research/pooled/test_runner.py tests/research/pooled/test_events_v1.py tests/research/pooled/test_events.py -q`** — pass; mypy clean.
- [ ] **Step 6: Stage and report.**

---

### Task 5: Manifests, frozen documents and results carry `short_inputs` and `short_coverage`

**Files:**
- Modify: `agentic_trader/research/pooled/campaign_run.py` (frozen document lines ~236-246; manifest ~370-376; result ~585-593)
- Modify: `agentic_trader/research/pooled/power.py` (~335, ~366), `agentic_trader/research/pooled/null_check.py` (~267, ~305)
- Test: `tests/research/pooled/test_campaign_run.py`, `tests/research/pooled/test_power.py`, `tests/research/pooled/test_null_check.py`

**Interfaces:**
- Consumes: `short_inputs_document`, `short_coverage_document` (Tasks 2, 4); `short_cube_build`, `mini_short_protocol` (mini world).
- Produces: every manifest and `result.json` of checks A and C and the campaign carries `short_inputs` (`null` for other protocols) next to `event_inputs`, and `short_coverage` next to `event_coverage`; the frozen formula body gains `{"score_inputs": ..., "short_inputs": ...}` for a short protocol.

- [ ] **Step 1: Write the failing tests**: `test_frozen_document_binds_short_inputs` (same text under a short protocol and under the plain mini protocol gives different identities; `short_inputs` absent from the plain body); `test_a_short_campaign_result_carries_its_short_inputs_and_coverage` (mirror of the events test at line ~789 using `short_cube_build`); in `test_power.py` and `test_null_check.py`, the manifest of a short-protocol run has `short_inputs == protocol.short_inputs.model_dump(mode="json")` and `short_coverage == asdict(built.short.coverage)`, and an events run has `short_inputs is None`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the insertions at the listed lines (import both documents from `scoring.py`).
- [ ] **Step 4: Run the three test files** — pass.
- [ ] **Step 5: Stage and report.**

---

### Task 6: CLI field sources: `--short-volume` on power, null-check and campaign

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (`_pooled_event_sources` lines ~1641-1670 → `_pooled_field_sources`; `_pooled_build` gains `short=`; the three commands' options and calls at ~1696, ~1887, ~1967; the campaign's pre-ledger verification at the `if events is not None` block)
- Test: `tests/cli/test_alpha_pooled_cli.py`

**Interfaces:**
- Consumes: `ShortSources`, `load_short_sources` (Task 4); `ShortVolumeCampaignProtocol` (Task 2).
- Produces:
  - `_SHORT_VOLUME_OPTION = click.option("--short-volume", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None, help="The alpha short-volume acquisition's cache directory (short-volume protocols only).")`
  - `_pooled_field_sources(protocol, earnings_pages, corporate_actions, short_volume) -> tuple[tuple[EventInputs, EventSources] | None, tuple[ShortInputs, ShortSources] | None]` with refusals: an events protocol needs pages + actions and refuses `--short-volume`; a short protocol needs `--short-volume` + `--corporate-actions` and refuses `--earnings-pages`; any other protocol refuses all three. Messages: `"--short-volume and --corporate-actions are required for a short-volume protocol"`, `"--short-volume applies only to a short-volume protocol"`, `"--earnings-pages applies only to an events protocol"`.
  - The campaign command verifies the short sources before the ledger opens: `await asyncio.to_thread(load_short_sources, inputs, sources, trading_days)` needs the calendar; the command obtains `trading_days` the way `build_cube_inputs` does (one `calendar.get_calendar_range` call inside `_apriori_clients()`), or, ruling if that is awkward: it verifies only `files_sha256` over the sessions present in the store plus the actions digest via a lighter `verify_short_digests(inputs, sources) -> None` added to `runner.py` for exactly this use; the full load still runs in the build. Record which in the report.

- [ ] **Step 1: Write the failing CLI tests** (CliRunner, monkeypatched `execute_power_check`/`execute_null_check`/campaign entry as the existing tests do): short protocol without `--short-volume` → exit 1 with the message; short protocol with `--earnings-pages` → refused; events protocol with `--short-volume` → refused; plain protocol with `--short-volume` → refused; a short protocol with both options passes `short=(inputs, ShortSources(...))` into the build (assert on the captured kwargs).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the option, the helper and the three call sites; the campaign's pre-ledger verification per the ruling above.
- [ ] **Step 4: Run `uv run pytest tests/cli/test_alpha_pooled_cli.py tests/cli/test_cli_alpha.py -q`** — pass; mypy clean on `alpha.py`.
- [ ] **Step 5: Stage and report.**

---

### Task 7: Frozen protocols `short-v1` and `cohort3-short-v1`, pin test and docs

Precondition (controller): `alpha short-volume --from 2018-08-01 --through 2026-09-01` has run outside the scan windows and its manifest `files_sha256` is in the task brief, together with the two corporate-actions digests (`776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344` for cohort v2; the cohort-v3 digest from `config/research/pooled/cohort3-events-v1.json`).

**Files:**
- Create: `config/research/pooled/short-v1.json`, `config/research/pooled/cohort3-short-v1.json`
- Create: `tests/research/pooled/test_short_v1.py`
- Modify: `docs/alpha-pooled-mining.md` (new section "Short-volume lane (`short_fields_v1`)"), `docs/alpha-data-augmentation-2026-10-18.md` (§4 item 3: lane built, protocols frozen), `docs/alpha-roadmap.md` (Status paragraph), `CLAUDE.md` (one sentence after the cohort-v3 paragraph: the lane, the command, the two frozen files, checks A and C, campaigns on approval)

**Interfaces:**
- Consumes: everything above.
- Produces: the two frozen files; `test_short_v1.py` pins each file's SHA-256 prefix (first 8 hex, filled in after freezing), asserts both are `ShortVolumeCampaignProtocol`, `fixed_set`, eight families with these exact seeds in this order:

```python
EXPECTED_SCORES = {
    "light_shorting": "-short_ratio",
    "light_shorting_week": "-ts_mean(short_ratio, 5)",
    "shorts_retreat": "-short_ratio_z",
    "shorts_retreat_week": "-ts_mean(short_ratio_z, 5)",
    "shorts_retreat_volume": "-short_volume_rel",
    "unopposed_strength": "-short_ratio_z / (ts_sum(returns, 5) > 0)",
    "squeeze": "short_ratio_z / (ts_sum(returns, 5) < 0)",
    "quiet_tape": "-offex_share",
}
```

  plus: every seed passes `require_dimensionless`; `short-v1` shares cohort, cube identity (`f1c835a3d3364d2b`), windows, selection, bootstrap, gates, power, null_check, search, coverage, universe, bars window with `events-v1.json`; `cohort3-short-v1` likewise with `cohort3-events-v1.json`; `short_inputs.short_volume` equals the pinned block; `excluded_families` ⊇ `cohort3-events-v1`'s list ∪ the eight events-v1 family ids; the frozen identity of `"-short_ratio"` under `short-v1` differs from the identity of the same text under `events-v1` (`_frozen_document` or its public equivalent from `campaign_run.py`); the windowed seeds evaluate to `NaN` out of window on `SHORT_FRAMES` through `evaluate_prepared`.

- [ ] **Step 1: Write the two protocol files** by copying `events-v1.json` / `cohort3-events-v1.json`, replacing `id`, `title`, `rationale` (one paragraph: the eight hypotheses, same-day 18:00 ET publication, off-exchange-only caveat, trailing stats exclude T, no carry-forward, identity breaks, digests pinned), `event_inputs` → `short_inputs` (the pinned block with the digest from the brief), `families` (the eight above, each with `mutation_operators: []` and a one-sentence `rationale` from the spec table), and `excluded_families` extended with the eight events-v1 ids (`reason`: `"events fixed set, scored in events-v1 / cohort3-events-v1 (no finalists)"`).
- [ ] **Step 2: Write the failing pin test** with placeholder prefixes `"00000000"`; run; read the two real prefixes from the failure, fill them in, re-run to pass.
- [ ] **Step 3: Docs**: the four files above; `docs/alpha-pooled-mining.md` gets the field table (spec §3), the acquisition command, the protocol block and the pre-registered closure rule; CLAUDE.md gets one sentence.
- [ ] **Step 4: Run `uv run pytest tests/research/pooled -q`** — pass.
- [ ] **Step 5: Stage and report.**
