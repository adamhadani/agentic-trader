# Phase 0 Fail-Safes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the macro lockout and the regime fail safe and visible, make card-sending scans the only scans that send cards, keep broad-index ETFs off native cards, and journal every scan that could have produced a card. All of it must merge by Monday 2026-10-26 and deploy before the 2026-10-28 FOMC.

**Architecture:**
- A digest-pinned static tier-1 calendar (`config/calendar/us-tier1-v1.json`) is authoritative. A union with the ForexFactory feed, which now reports a typed status, answers one `check()`. That check feeds the single `risk.macro_lockout` rule at the scan, the evaluator, the tap gate and the preflight.
- The macro engine returns typed inputs. A missing decision input yields an explicit `UNKNOWN` stress state at 0.5×. A missing VIX raises `RegimeUnavailable`, which `run_scan` turns into a journal event, a skipped PEAD decision, SCAN readiness and one notice per New York date.
- `run_scan` gains an idle check and a guarded regime read. It now journals every eligible scan, with a status, a gate, the calendar block and a collect funnel.
- Swing and intraday scans run `ScanBudget.JOURNAL_ONLY`. A broad-index exclusion sits in the send loop.

**Tech Stack:** Python 3.14, pydantic v2, httpx (MockTransport in tests), SQLAlchemy async (SQLite in unit tests, PostgreSQL integration), APScheduler, click, pytest (asyncio auto mode), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-21-phase0-fail-safes-design.md`. It is binding, and the §10 defaults are accepted. Rulings that refine it are listed under "Rulings" below. Every task's implementer and reviewer reads the spec section the task names.

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-phase0` (branch `feature/phase0-fail-safes`). Never touch `/Users/adamhadani/Development/agentic-trader`. Tasks run no `copilot` CLI against real config, no daemon, no real database and no network.
- Run every command as `env -u VIRTUAL_ENV uv run …`. The code is Python 3.14 (`except A, B:` without parentheses is valid syntax here).
- Ruff is strict: no `dict()` literal calls (C408) and no function-level imports (PLC0415). mypy must be clean: `env -u VIRTUAL_ENV uv run mypy agentic_trader`.
- Existing config blocks (`RiskConfig`, `RegimeConfig`, `ScanConfig`) stay plain `BaseModel`, so unknown keys in an old YAML are ignored. The new calendar file model is `ConfigDict(frozen=True, extra="forbid")`.
- Tests use:
  - the fixture config `tests/fixtures/config.yaml` (loaded through `COPILOT_CONFIG`);
  - temporary SQLite, stripped credentials and blocked Python and libcurl network.
  - The shipped `config/calendar/us-tier1-v1.json` is read only by `tests/agent/test_shipped_calendar.py`.
  - Any other test that needs a calendar uses the fixture calendar or a `tmp_path` file. A test never asserts against the real clock: inject `now`.
- PostgreSQL integration runs only via `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest tests/integration --run-postgres`. The database name must start with `test_`.
- No Alembic migration (head stays `008_alpha_pipeline`) and no new `EventKind`. Every `scan_candidates_ranked` payload change is additive. Readers treat a missing `status` as `completed` and a missing `funnel` as unknown.
- No compatibility aliases. These go, and their callers change in the same task:
  - `BaseEconomicCalendar.is_in_lockout_window`, `get_upcoming_tier1_events`, `get_macro_summary_for_prompt` and `is_tier_1`;
  - `TIER_1_KEYWORDS`, `EconomicCalendarProtocol` and replay's `_NoIoCalendar`;
  - `MacroIntelligenceEngine.get_macro_report`, `FredDataClient.fetch_latest_value` and `FredDataClient.observation_dates`;
  - both `_fetch_ticker_sync` copies;
  - `RegimeConfig.credit_oas_enabled` and `RegimeConfig.inflation_breakeven_enabled`.
- Readiness details, Telegram notices, journal `gate`/`errors` and log `error` fields carry exception **type names** only, never `str(exc)`.
- A journal event and its notice intent are written in one transaction, through `append_scan_journal` (Task 4). POST, PATCH and DELETE are never retried. Nothing here touches the broker or admission sizing.
- Blocking provider reads (yfinance) run through `asyncio.to_thread`. A readiness degradation never changes the trading halt.
- Every entry-risk rule stays in `agentic_trader/risk`. The broad-index exclusion is a card-selection rule in `run_scan`, not a risk rule.
- Values pinned by the spec and the rulings:
  - lockout windows unchanged (60 min before, 30 after);
  - live success cache 30 min, failure backoff 5 min, live/static match tolerance ±5 min;
  - `risk.macro_calendar_renewal_days` 30 and `risk.macro_calendar_unknown` `open`;
  - `regime.unknown_risk_multiplier` 0.5, validated `0.10 ≤ m < 1.0`, and `regime.unknown_breakout_allowed` false;
  - outbox key `regime-unavailable/{et_date}`;
  - enum values: `ScanBudget.JOURNAL_ONLY = "journal_only"`, `RankedOutcome.JOURNAL_ONLY = "journal_only"`, `RankedOutcome.BROAD_INDEX_EXCLUDED = "broad_index_excluded"` and `ScanStatus` `completed`/`macro_lockout`/`regime_unavailable`.
- Commits:
  - Implementers stage their changes (`git add`, including every new test file) and report. **The controller commits.**
  - Pre-commit stashes unstaged tracked changes but not untracked files, so stage everything before running it.
  - Before reporting, run the task's tests, the affected test directories and `env -u VIRTUAL_ENV uv run pre-commit run --all-files`.
- **Merge gate (controller, not a task step):** Tasks 2 and 3 change LLM prompt text (the system prompt's lockout line, the Economic Calendar Status block and the regime context). Per the operator's standing rule, verify them with LangSmith traces and the replay harness before merging.

## Rulings (spec refinements the reviewer checks against)

1. **Calendar data window.** `valid_through` is 2027-12-03, the shortest series' last listed date (Employment Situation; `coverage.employment_situation.through`). The loader requires every event to fall within `[valid_from, valid_through]`. So v1 ships the **58** source events dated ≤ 2027-12-03 (9 FOMC statements, 9 press conferences, 13 CPI, 13 PPI, 14 Employment Situation). It omits the four later ones: the 2027-12-08 FOMC statement and press conference, CPI 2027-12-10 and PPI 2027-12-15. They belong in v2. The calendar is expired by then, so it would never use them.
2. **`series_last_listed`.** The schema gains a required `series_last_listed: {Tier1Kind: date}` for all five kinds, holding each series' last listed date in the source. The loader enforces `valid_through ≤ min(series_last_listed)` (`ValidityBeyondSeries`). This makes the spec's shortest-horizon rule a checked invariant. It is also the only way the spec's "`valid_through` is no later than the shortest series' last listed date" test can be written without reading the out-of-repo source file.
3. **`tentative`.** Events gain an optional `tentative: bool = False`, true on exactly the 14 2027 FOMC events (the Fed: "each meeting date is tentative until confirmed at the meeting immediately preceding it"). It is evidence for the renewal runbook only, and a tentative event locks out like any other.
4. **SPLG → SPYM.** The operator's Alpaca asset snapshot of 2026-10-15 (`~/agentic-trader-research/equity-snapshot-20261015/alpaca-assets.json`) has no SPLG. It lists SPYM, "State Street SPDR Portfolio S&P 500 ETF", as an active, tradable `us_equity`. All 22 other listed tickers are active there too. The list therefore carries SPYM in place of SPLG and keeps 23 names. This read stands in for the spec's one-off Alpaca check, so no task needs the network.
5. **`macro_lockout` signature.** The closed-policy text names `{expired|invalid}`, which a bare `calendar_verified: bool` cannot express. The rule therefore takes `*, calendar_verified: bool, static_status: CalendarStatus`. `static_status` is used only for that text. `CalendarStatus` lives in `agentic_trader/constants.py`, because `risk/` may not import `agent/calendar.py`, which itself imports `risk`.
6. **Class hierarchy.** `BaseEconomicCalendar` stays the live-feed ABC (`fetch_events() -> CalendarFetch`). `StaticTier1Calendar` and `UnionTier1Calendar` do not subclass it. The union's one read is `check()` (spec §5.1), so an inherited `fetch_events` would be a second read the spec rules out, and no behaviour depends on the inheritance.
7. **No-I/O union.** Replay uses `UnionTier1Calendar.offline()`, which reads no file, makes no request and is never `check`ed. Backtest keeps `RiskEvaluator(self.config)`, whose fallback is `UnionTier1Calendar.from_config(config)`.
8. **Fixture config.** `tests/fixtures/config.yaml` sets `scan.broad_index_etfs: []`, because unrelated scan tests card SPY. Exclusion tests opt in. It also points `risk.macro_calendar_path` at the synthetic `tests/fixtures/calendar/us-tier1-test-v1.json` (unpinned).
9. **Digest moves with the scan lifecycle.** The trigger filter and the "Skipped:" clause land in Task 4, which creates the gated summaries, so the digest never aggregates a gated summary as a zero-count scan.
10. **Engine cache.** `MacroIntelligenceEngine.evaluate()` caches only a complete evaluation (all ten inputs). `RegimeDetector` caches every snapshot, `UNKNOWN` included, for `cache_ttl_seconds`, as spec §5.2 says.
11. **Builder's calls taken:**
    - `/macro`'s `MACRO_REPORT` audit adds `"regime": regime_journal_block(regime)` and keeps its other keys;
    - `CalendarCheck.lockout_rejection(limits)` is the convenience; the rule stays in `risk/`;
    - the idle check and selection share one helper;
    - the calendar journal block carries `match_tolerance_minutes: 5`;
    - `macro_enabled` is derived as `macro_stress is not None`, which holds by construction.

## Review Focus

1. **A daemon running past New York midnight into an expired date.** The static status must flip on the first `check()` after midnight without a restart. Status is computed per call from `now`, never at load. Pinned by Task 1, `test_status_flips_at_new_york_midnight_without_reload`.
2. **The live cache expiring during a ForexFactory outage.** The refetch fails, and the union must see `failed` with no events, not the stale cached events. Pinned by Task 2, `test_an_expired_success_is_not_served_when_the_refetch_fails`.
3. **A FRED maintenance window ending mid-session.** The first regime read after the detector's TTL must measure again, not stay `UNKNOWN`, so the engine must not cache a partial evaluation. Pinned by Task 3, `test_a_partial_evaluation_is_not_cached_and_recovery_is_measured`.
4. **A session where both suggestion slots are gated** (10:35 regime unavailable, 14:35 FOMC lockout). The digest must still publish once and name both skips. Pinned by Task 4, `test_digest_lists_both_skipped_slots_when_no_suggestion_scan_completed`.
5. **A contract whose `scan_contract` raises, beside one whose admission raises mid-list.** The funnel identity must hold, errors must be counted by type, and the other contracts must still be journaled. Pinned by Task 5, `test_funnel_identity_holds_across_every_refusal_kind`, case `GGG`.

## File map

| File | Responsibility |
| --- | --- |
| `config/calendar/us-tier1-v1.json` (new) | Immutable v1 tier-1 schedule (58 events) |
| `tests/fixtures/calendar/us-tier1-test-v1.json` (new) | Synthetic calendar for unrelated tests |
| `agentic_trader/constants.py` | `CalendarStatus` |
| `agentic_trader/agent/calendar.py` | `Tier1Kind`, the file schema, `StaticTier1Calendar`, `CalendarFetch`, `ForexFactoryCalendar`, `CalendarCheck`, `UnionTier1Calendar`, `macro_calendar_readiness` |
| `agentic_trader/risk/rules.py`, `risk/limits.py` | `macro_lockout(..., calendar_verified, static_status)` and `RiskLimits.macro_calendar_unknown` |
| `agentic_trader/agent/macro.py` | `MacroInput`, input tuples, typed fetchers, `MacroEvaluation`, `evaluate()`, `UNKNOWN` |
| `agentic_trader/agent/regime.py` | `RegimeUnavailable`, the new snapshot fields, journal blocks |
| `agentic_trader/agent/scan_journal.py` (new) | `ScanNotice`, `append_scan_journal`, notice text, `RefusalKind`, `AdmissionRefusal`, `ScanFunnel` |
| `agentic_trader/agent/copilot.py` | `run_scan` order, idle check, gates, journal, `JOURNAL_ONLY`, broad-index rule, digest |
| `agentic_trader/execution/durable.py` | `ScanStatus` and the new `RankedOutcome` members |
| `agentic_trader/research/setups/outcomes.py` | `journal_only` class and `scan_supply` |
| `tests/calendar_doubles.py` (new) | `CalendarCheck` builders and the union double for tests |

---

### Task 1: Tier-1 calendar file, loader, config and readiness check

Spec §5.1 (File, Loader, Statuses, Readiness, Config), §7.1 (Loader, Readiness, Shipped data minus lockout acceptance) and §9 step 1.

**Files:**
- Create: `config/calendar/us-tier1-v1.json`, `tests/fixtures/calendar/us-tier1-test-v1.json`, `tests/agent/test_static_calendar.py`, `tests/agent/test_shipped_calendar.py`, `tests/config/test_macro_calendar_config.py`
- Modify: `agentic_trader/constants.py` (add `CalendarStatus`), `agentic_trader/agent/calendar.py` (additive: the static calendar types; `MacroEvent` gains two defaulted fields), `agentic_trader/config.py` (`RiskConfig` keys), `agentic_trader/diagnostics/readiness.py`, `agentic_trader/agent/copilot.py` (`self.static_calendar`, readiness wiring), `tests/fixtures/config.yaml`, `config/config.yaml` (`risk:` keys), `tests/workflows/test_journal_readiness.py`, `docs/production.md` (renewal runbook), `docs/operational-monitoring.md` (`macro_calendar` check)

**Interfaces:**
- Produces:
  - `constants.CalendarStatus(StrEnum)`: `OK = "ok"`, `FAILED = "failed"`, `EXPIRED = "expired"`, `INVALID = "invalid"`.
  - In `agent/calendar.py`:
    - `Tier1Kind(StrEnum)`: `FOMC_STATEMENT = "fomc_statement"`, `FOMC_PRESS_CONFERENCE = "fomc_press_conference"`, `CPI = "cpi"`, `PPI = "ppi"`, `EMPLOYMENT_SITUATION = "employment_situation"`.
    - `TIER1_TITLES: Mapping[Tier1Kind, str]`: "FOMC Statement", "FOMC Press Conference", "CPI", "PPI", "Employment Situation".
    - `OFFICIAL_HOSTS = frozenset({"www.federalreserve.gov", "www.bls.gov"})` and `FOREXFACTORY_SOURCE = "forexfactory"`.
    - `MacroEvent` gains `source: str = FOREXFACTORY_SOURCE` and `kind: Tier1Kind | None = None`.
  - Pydantic models (`frozen=True, extra="forbid"`):
    - `Tier1EventRecord(kind: Tier1Kind, at: AwareDatetime, source_url: str, tentative: bool = False)`;
    - `Tier1CalendarFile(schema_version: Literal[1], calendar_id: str` (pattern `^[a-z0-9]+(?:-[a-z0-9]+)*-v\d+$`)`, timezone: Literal["America/New_York"], valid_from: date, valid_through: date, retrieved_on: date, series_last_listed: dict[Tier1Kind, date]` (all five keys required)`, events: tuple[Tier1EventRecord, ...])`.
  - `CalendarFileError(ValueError)` and its subclasses, whose names are the `error` values: `DigestMismatch`, `CalendarIdMismatch`, `InvalidValidityRange`, `ValidityBeyondSeries`, `WrongNewYorkOffset`, `EventOutsideValidity`, `UnsortedEvents`, `DuplicateEvent`, `UnofficialSource`.
  - `@dataclass(frozen=True) StaticCalendarState(calendar_id: str, sha256: str | None, pinned: bool, status: CalendarStatus, valid_from: date | None, valid_through: date | None, days_remaining: int | None, error: str | None)`.
  - `StaticTier1Calendar`:
    - `classmethod load(path: Path, sha256: str | None) -> StaticTier1Calendar`. It never raises. It logs one ERROR with the type name on failure.
    - Attributes: `calendar_id` (the file stem until parsed), `sha256` (of the bytes read; None if unreadable), `pinned`, `error`, `valid_from`, `valid_through` and `events: tuple[MacroEvent, ...]`.
      - `events` is empty when the file is invalid.
      - Each event has `country="USD"`, `impact="High"`, `timestamp` in UTC, `source=f"static:{calendar_id}"` and `kind`.
    - `state(now: datetime) -> StaticCalendarState`:
      - status `INVALID` when `error` is set;
      - `EXPIRED` when New York's date at `now` is outside `[valid_from, valid_through]`;
      - otherwise `OK`.
      - `days_remaining = (valid_through − NY date).days`.
  - `macro_calendar_readiness(state: StaticCalendarState, *, renewal_days: int, unknown_policy: str) -> dict[str, Any]` returns `{"ready", "detail", "status", "days_remaining"}`. Ready iff status `OK` and `days_remaining ≥ renewal_days`. The detail strings are pinned in Step 1.
  - `RiskConfig`:
    - `macro_calendar_path: Path = Path("config/calendar/us-tier1-v1.json")`, resolved against `WORKSPACE_ROOT` by a validator shaped like `_resolve_shadow_ranker_artifact`;
    - `macro_calendar_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")`;
    - `macro_calendar_unknown: Literal["open", "closed"] = "open"`;
    - `macro_calendar_renewal_days: int = Field(30, ge=1)`.
  - `ReadinessService(..., macro_calendar: Callable[[datetime], Mapping[str, Any]] | None = None)`:
    - when set, `report()` adds `checks["macro_calendar"] = dict(macro_calendar(now))`;
    - an exception from the callable yields `{"ready": False, "detail": type name}`.
  - `TradingCopilot.static_calendar: StaticTier1Calendar`, loaded in `__init__` from `config.risk`. It is passed to readiness as `lambda now: macro_calendar_readiness(self.static_calendar.state(now), renewal_days=…, unknown_policy=…)`.

- [ ] **Step 1: Write the failing loader, status and readiness tests** (`tests/agent/test_static_calendar.py`).

  A local helper `write_calendar(tmp_path, events, *, calendar_id="us-tier1-v1", valid_from="2026-10-21", valid_through="2027-12-03", series_last_listed=None, **overrides) -> Path` writes `{calendar_id}.json`. `series_last_listed` defaults to all five kinds at `valid_through`.

```python
NOW = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
CPI = {
    "kind": "cpi",
    "at": "2026-11-10T08:30:00-05:00",
    "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
}


def test_a_valid_file_loads_ok_with_static_events(tmp_path):
    path = write_calendar(tmp_path, [CPI])
    cal = StaticTier1Calendar.load(path, hashlib.sha256(path.read_bytes()).hexdigest())
    state = cal.state(NOW)
    assert (state.status, state.pinned, state.error) == (CalendarStatus.OK, True, None)
    [event] = cal.events
    assert (event.title, event.kind, event.source) == ("CPI", Tier1Kind.CPI, "static:us-tier1-v1")
    assert event.timestamp == datetime(2026, 11, 10, 13, 30, tzinfo=UTC)


def test_an_unpinned_load_records_the_digest(tmp_path):
    path = write_calendar(tmp_path, [CPI])
    state = StaticTier1Calendar.load(path, None).state(NOW)
    assert state.pinned is False and state.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("digest", "DigestMismatch"),
        ("missing", "FileNotFoundError"),
        ("unknown_kind", "ValidationError"),
        ("extra_key", "ValidationError"),
        ("edt_offset_on_est_date", "WrongNewYorkOffset"),
        ("utc_offset", "WrongNewYorkOffset"),
        ("outside_validity", "EventOutsideValidity"),
        ("unsorted", "UnsortedEvents"),
        ("duplicate", "DuplicateEvent"),
        ("http_scheme", "UnofficialSource"),
        ("other_host", "UnofficialSource"),
        ("id_not_stem", "CalendarIdMismatch"),
        ("from_after_through", "InvalidValidityRange"),
        ("through_beyond_series", "ValidityBeyondSeries"),
    ],
)
def test_an_invalid_file_loads_as_invalid_with_its_type_name(tmp_path, caplog, case, error):
    cal = StaticTier1Calendar.load(*_broken(tmp_path, case))  # never raises
    state = cal.state(NOW)
    assert (state.status, state.error, cal.events) == (CalendarStatus.INVALID, error, ())
    assert [r.levelno for r in caplog.records if r.name == "agentic_trader.agent.calendar"] == [logging.ERROR]


def test_status_flips_at_new_york_midnight_without_reload(tmp_path):  # Review Focus 1
    cal = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None)
    assert cal.state(datetime(2027, 12, 4, 4, 59, tzinfo=UTC)).status is CalendarStatus.OK  # 23:59 NY, 12-03
    assert cal.state(datetime(2027, 12, 4, 5, 1, tzinfo=UTC)).status is CalendarStatus.EXPIRED  # 00:01 NY, 12-04
    assert cal.state(datetime(2026, 10, 20, 16, 0, tzinfo=UTC)).status is CalendarStatus.EXPIRED  # before valid_from


@pytest.mark.parametrize(
    ("now", "ready", "detail"),
    [
        (datetime(2027, 11, 3, 16, tzinfo=UTC), True, "us-tier1-v1 valid through 2027-12-03 (30 days)"),
        (datetime(2027, 11, 4, 16, tzinfo=UTC), False, "us-tier1-v1 expires 2027-12-03 (29 days); publish v2"),
        (
            datetime(2027, 12, 5, 16, tzinfo=UTC),
            False,
            "us-tier1-v1 expired 2027-12-03; lockout unverified (policy open)",
        ),
        (
            datetime(2026, 10, 20, 16, tzinfo=UTC),
            False,
            "us-tier1-v1 not valid until 2026-10-21; lockout unverified (policy open)",
        ),
    ],
)
def test_macro_calendar_readiness_detail(tmp_path, now, ready, detail):
    state = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None).state(now)
    check = macro_calendar_readiness(state, renewal_days=30, unknown_policy="open")
    assert (check["ready"], check["detail"]) == (ready, detail)


def test_an_invalid_calendar_is_not_ready(tmp_path):
    path = write_calendar(tmp_path, [CPI])
    state = StaticTier1Calendar.load(path, "0" * 64).state(NOW)
    assert (
        macro_calendar_readiness(state, renewal_days=30, unknown_policy="open")["detail"] == "invalid: DigestMismatch"
    )
```

  Also add:
  - in `tests/config/test_macro_calendar_config.py`:
    - `test_risk_config_defaults_and_validation`: the default path resolves to `WORKSPACE_ROOT / "config/calendar/us-tier1-v1.json"`, the policy is `"open"` and renewal is 30; `macro_calendar_sha256="ABC"` and `macro_calendar_unknown="maybe"` raise `ValidationError`;
    - `test_fixture_config_uses_the_synthetic_calendar`: `load_config().risk.macro_calendar_path.name == "us-tier1-test-v1.json"`;
  - in `tests/workflows/test_journal_readiness.py`: `test_macro_calendar_check_is_computed_and_type_named_on_failure`. A callable returning `{"ready": False, "detail": "invalid: DigestMismatch"}` appears verbatim under `checks["macro_calendar"]` and makes `report["ready"]` false. A raising callable yields `{"ready": False, "detail": "RuntimeError"}`.

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_static_calendar.py tests/config/test_macro_calendar_config.py tests/workflows/test_journal_readiness.py -q`. Expected: ImportError or failures on the new names.

- [ ] **Step 3: Implement** `CalendarStatus`, the schema, the errors, `StaticTier1Calendar`, `macro_calendar_readiness`, the `RiskConfig` keys, the readiness parameter and the copilot wiring.
  - Loader order: read bytes, then digest, then `Tier1CalendarFile.model_validate_json`, then the semantic checks in the error list's order.
  - The offset check is `at.utcoffset() == at.astimezone(ET_TZ).utcoffset()` (`ET_TZ` from `agentic_trader.market.session`).
  - Sorting is by `(at, kind)`, and uniqueness is on `(kind, at)`.
  - The host check is `urlsplit(url).scheme == "https" and urlsplit(url).hostname in OFFICIAL_HOSTS`.

- [ ] **Step 4: Run the Step 2 command.** Expected: all pass.

- [ ] **Step 5: Write the synthetic fixture calendar and point the fixture config at it.** The file `tests/fixtures/calendar/us-tier1-test-v1.json` has:
  - `calendar_id` `us-tier1-test-v1`, `valid_from` 2020-01-01 and `valid_through` 2099-12-31, with every `series_last_listed` at 2099-12-31;
  - five events in March 2021, one per kind, which exercise both offsets:
    - Employment Situation 2021-03-05T08:30:00-05:00;
    - CPI 2021-03-10T08:30:00-05:00;
    - PPI 2021-03-12T08:30:00-05:00;
    - FOMC statement 2021-03-17T14:00:00-04:00;
    - press conference 2021-03-17T14:30:00-04:00.

  In `tests/fixtures/config.yaml` add `risk: {macro_calendar_path: tests/fixtures/calendar/us-tier1-test-v1.json}` (unpinned).

- [ ] **Step 6: Data entry, writing `config/calendar/us-tier1-v1.json` from the verified source.** Use a scratch script in the session scratchpad. Do not commit it, and run it with `python3 -I`. It reads `/Users/adamhadani/agentic-trader-research/tier1-calendar-20261021/tier1-calendar-sources.json` and writes:
  - `schema_version` 1, `calendar_id` `"us-tier1-v1"`, `timezone` `"America/New_York"`;
  - `valid_from` `"2026-10-21"`, `valid_through` `"2027-12-03"`, `retrieved_on` `"2026-10-21"`;
  - `series_last_listed` `{"fomc_statement": "2027-12-08", "fomc_press_conference": "2027-12-08", "cpi": "2027-12-10", "ppi": "2027-12-15", "employment_situation": "2027-12-03"}` (the source's `coverage.*.through`);
  - `events`: each source event with `date_et ≤ 2027-12-03`, in source order (already sorted by instant):
    - `kind` is `series`;
    - `at` is `date_et` + `time_et` with New York's offset at that instant, e.g. `"2026-10-28T14:00:00-04:00"` and `"2026-11-06T08:30:00-05:00"`;
    - `source_url` and `tentative` are copied from the event.

  The output uses `json.dumps(indent=2)` and a trailing newline. Then:
  1. Run `env -u VIRTUAL_ENV uv run pre-commit run --files config/calendar/us-tier1-v1.json` so `end-of-file-fixer` and `check-json` settle the bytes **before** hashing.
  2. Compute `shasum -a 256 config/calendar/us-tier1-v1.json`.
  3. Pin the digest in `config/config.yaml`'s `risk:` block. Also add `macro_calendar_path: config/calendar/us-tier1-v1.json`, `macro_calendar_unknown: open` and `macro_calendar_renewal_days: 30`, each with a one-line comment (immutable file; renew as v2 before readiness degrades; open = unverified labels, never refuses).
  4. Report the digest.

- [ ] **Step 7: Write the shipped-data tests** (`tests/agent/test_shipped_calendar.py`, the only reader of the real file).

```python
SHIPPED = WORKSPACE_ROOT / "config/calendar/us-tier1-v1.json"


def test_config_pins_the_shipped_calendar():
    risk = yaml.safe_load((WORKSPACE_ROOT / "config/config.yaml").read_text())["risk"]
    assert risk["macro_calendar_path"] == "config/calendar/us-tier1-v1.json"
    assert risk["macro_calendar_sha256"] == hashlib.sha256(SHIPPED.read_bytes()).hexdigest()


def test_shipped_calendar_loads_ok_on_its_first_day_and_counts_per_kind():
    cal = StaticTier1Calendar.load(SHIPPED, hashlib.sha256(SHIPPED.read_bytes()).hexdigest())
    assert cal.state(datetime(2026, 10, 21, 16, tzinfo=UTC)).status is CalendarStatus.OK
    assert Counter(e.kind for e in cal.events) == {
        Tier1Kind.FOMC_STATEMENT: 9,
        Tier1Kind.FOMC_PRESS_CONFERENCE: 9,
        Tier1Kind.CPI: 13,
        Tier1Kind.PPI: 13,
        Tier1Kind.EMPLOYMENT_SITUATION: 14,
    }


def test_valid_through_is_the_shortest_series_horizon():
    doc = Tier1CalendarFile.model_validate_json(SHIPPED.read_bytes())
    assert doc.valid_from == date(2026, 10, 21) and doc.valid_through == date(2027, 12, 3)
    assert doc.valid_through == min(doc.series_last_listed.values())


FOMC_DATES = [
    date(2026, 10, 28),
    date(2026, 12, 9),
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 4, 28),
    date(2027, 6, 9),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 10, 27),
]


def test_every_fomc_statement_is_1400_ny_with_a_1430_press_conference_the_same_date(): ...  # statements' NY (date, time) == [(d, time(14, 0)) for d in FOMC_DATES]; press conferences == [(d, time(14, 30)) ...]
def test_every_bls_release_is_0830_ny(): ...
def test_tentative_marks_exactly_the_2027_fomc_events(): ...  # {(r.kind, r.at.date()) for r in doc.events if r.tentative} == both FOMC kinds x FOMC_DATES[2:] (14 events)
```

- [ ] **Step 8: Docs.**
  - `docs/production.md`: add a short "Tier-1 calendar renewal" runbook.
    - It ships as `config/calendar/us-tier1-vN.json` and is immutable.
    - Before `macro_calendar` readiness degrades (30 days before `valid_through`), build `us-tier1-vN+1.json` from the Fed FOMC calendar and the BLS CPI, PPI and Employment Situation schedule pages:
      - each `source_url` is the page the date came from;
      - re-check every `tentative` FOMC date;
      - `valid_through` is the shortest series' last listed date.
    - Run pre-commit on the file, then pin its SHA-256 and path in `config.yaml`.
    - Deploy (watchdog, then daemon, pause order) and confirm `doctor --readiness`.
    - A moved or cancelled release also means a new version.
  - `docs/operational-monitoring.md`: list the computed `macro_calendar` check and its detail strings. It is ready iff the static calendar is ok with at least `risk.macro_calendar_renewal_days` left. Live-feed status is journaled per scan, not part of readiness. The monitor's existing debounced incident covers it.

- [ ] **Step 9: Verify.**
  - Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_static_calendar.py tests/agent/test_shipped_calendar.py tests/config tests/workflows/test_journal_readiness.py tests/agent -q`. Expected: all pass.
  - Run `env -u VIRTUAL_ENV uv run mypy agentic_trader`. Expected: clean.
  - Run `env -u VIRTUAL_ENV uv run pre-commit run --all-files`. Expected: all hooks pass.

- [ ] **Step 10: Stage and report.** Stage `git add` on every file above, including the new tests and both JSON files. Suggested message: `Tier-1 calendar v1: digest-pinned static FOMC/CPI/PPI/Employment Situation schedule, loader that fails closed to invalid, macro_calendar readiness`.

---

### Task 2: Typed live feed, union check and one lockout rule at every site

Spec §5.1 (Typed live fetch, Union, The rule stays in `risk/`, Prompt and card) and §7.1 (Live fetch status, Union and statuses, and the shipped-data lockout acceptance).

**Files:**
- Create: `tests/calendar_doubles.py`, `tests/agent/test_macro_lockout_sites.py`, `tests/notifier/test_macro_line.py`
- Modify:
  - `agentic_trader/agent/calendar.py`: rewrite the live feed and add the union, deleting `EconomicCalendarProtocol`, the window and summary helpers, `is_tier_1` and `TIER_1_KEYWORDS`;
  - `agentic_trader/risk/rules.py`, `agentic_trader/risk/limits.py`;
  - `agentic_trader/agent/evaluator.py`, `agentic_trader/agent/prompts.py` (system-prompt line 22), `agentic_trader/agent/copilot.py`;
  - `agentic_trader/notifier/telegram_bot.py` (both card formatters), `agentic_trader/research/setups/replay.py`;
  - the test doubles in `tests/conftest.py`, `tests/agent/conftest.py`, `tests/agent/test_calendar.py` (rewrite), `tests/agent/test_calendar_prompt.py` (rewrite), `tests/agent/test_evaluator.py`, `tests/agent/test_evaluator_risk_policy.py`, `tests/agent/test_risk_policy_integration.py`, `tests/agent/test_tap_risk_policy.py`, `tests/agent/test_scan_drift.py`, `tests/agent/test_card_freshness_tap.py`, `tests/agent/test_async_responsiveness.py`, `tests/agent/test_timeframe_dedup.py`, `tests/agent/test_account_risk.py`, `tests/execution/test_risk_budgeting.py`, `tests/risk/test_rules.py`, `tests/agent/test_shipped_calendar.py`;
  - `docs/risk-policy.md`.

**Interfaces:**
- Consumes (Task 1): `Tier1Kind`, `TIER1_TITLES`, `MacroEvent.source/kind`, `StaticTier1Calendar`, `StaticCalendarState`, `CalendarStatus`, `TradingCopilot.static_calendar`, `RiskConfig.macro_calendar_*`.
- Produces:
  - `TIER1_KEYWORDS: tuple[tuple[str, Tier1Kind], ...]`, the existing keywords each mapped to a kind, with `"fomc press conference"` listed before every statement keyword. `tier1_kind(title: str, country: str) -> Tier1Kind | None` returns None outside USD/US.
  - `@dataclass(frozen=True) CalendarFetch(status: CalendarStatus, events: tuple[MacroEvent, ...] = (), fetched_at: datetime | None = None, error: str | None = None, http_status: int | None = None, coverage: tuple[date, date] | None = None, unparsed_items: int = 0)`.
  - `BaseEconomicCalendar` (ABC): `source: ClassVar[str]`, `label: ClassVar[str]` and abstract `async fetch_events(self, force_refresh: bool = False) -> CalendarFetch`.
  - `ForexFactoryCalendar(cache_ttl_minutes: int = 30, failure_backoff_minutes: int = 5, *, transport: httpx.AsyncBaseTransport | None = None, clock: Callable[[], datetime] | None = None)`, with `source = "forexfactory"` and `label = "ForexFactory"`.
    - Error type names: `UnexpectedStatus` for a non-200 (with `http_status`), the exception's type name for a raised error (`ReadTimeout`, `JSONDecodeError`), and `UnexpectedPayload` for a non-list body.
    - Coverage is the first and last New York date of all parseable items, any country. A non-dict item or an unparseable date counts in `unparsed_items`.
    - Events are USD items with `impact == "High"` or a tier-1 kind, carrying `kind=tier1_kind(...)`.
    - A failure is remembered for the backoff and never served as cached success.
  - `MATCH_TOLERANCE = timedelta(minutes=5)`.
  - `@dataclass(frozen=True) CalendarLockout(title: str, at: datetime, kind: Tier1Kind | None, sources: tuple[str, ...])`. `sources` is `("static",)`, `("static", "live")` or `("live",)`.
  - `@dataclass(frozen=True) CalendarDisagreement(side: Literal["static_only", "live_only"], kind: Tier1Kind, at: datetime, title: str)`.
  - `@dataclass(frozen=True) CalendarCheck(now, pre_minutes, post_minutes, unknown_policy, static: StaticCalendarState, live: CalendarFetch, live_source: str, live_label: str, lockout: CalendarLockout | None, upcoming: tuple[MacroEvent, ...], disagreements: tuple[CalendarDisagreement, ...])` with:
    - `verified` (property): `static.status is OK`;
    - `lockout_rejection(limits: RiskLimits) -> Rejection | None`, which calls `risk.macro_lockout(lockout.title|None, lockout.at|None, self.now, limits, calendar_verified=self.verified, static_status=self.static.status)`;
    - `prompt_text() -> str`;
    - `macro_note() -> str | None` (None when verified, else `f"static tier-1 calendar {static.status}; live feed {live_state}"`);
    - `journal_block() -> dict[str, Any]`, with exactly the spec's keys plus `match_tolerance_minutes`. `live_state` is `"ok"` or `f"failed ({error})"`.
  - `UnionTier1Calendar(static: StaticTier1Calendar, live: BaseEconomicCalendar, *, unknown_policy: Literal["open", "closed"])`:
    - `classmethod from_config(config: AppConfig, *, live: BaseEconomicCalendar | None = None)`, whose live default is `ForexFactoryCalendar()`;
    - `classmethod offline()`, with no file read, no request, an `INVALID`/`"Offline"` static and a `FAILED`/`"Offline"` live feed;
    - `async check(now: datetime, pre_minutes: int, post_minutes: int) -> CalendarCheck`;
    - attribute `static`.
  - `risk.macro_lockout(event_title, event_at, now, limits, *, calendar_verified: bool, static_status: CalendarStatus) -> Rejection | None`. When `not calendar_verified and limits.macro_calendar_unknown == "closed"`, it returns, before the window test, `Rejection(MACRO_LOCKOUT, f"Macro calendar unverified: the static tier-1 calendar is {static_status}; new entries refused (risk.macro_calendar_unknown: closed).")`.
  - `RiskLimits.macro_calendar_unknown: Literal["open", "closed"]`, read in `from_config`.
  - `LLMTradeEvaluation.macro_note: str | None = None`.
  - Copilot:
    - `self.calendar: UnionTier1Calendar = UnionTier1Calendar(self.static_calendar, ForexFactoryCalendar(), unknown_policy=config.risk.macro_calendar_unknown)`;
    - `_macro_lockout_reason(self, *, now: datetime) -> str | None` and `_macro_gate(self, request, signal, regime, *, now: datetime)`. The tap passes `tapped_at`; `_entry_macro_check` passes `datetime.now(UTC)`.
  - `RiskEvaluator(config, calendar: UnionTier1Calendar | None = None, …)`, whose fallback is `UnionTier1Calendar.from_config(config)`.
  - `tests/calendar_doubles.py`:
    - `clear_check(now=…) -> CalendarCheck`;
    - `lockout_check(title, at, *, kind=Tier1Kind.CPI, now=None) -> CalendarCheck` (`now` defaults to `at`);
    - `unverified_check(status=CalendarStatus.EXPIRED, *, now=…) -> CalendarCheck`;
    - `union_double(check) -> AsyncMock` (spec `UnionTier1Calendar`, `check.return_value = check`).

The union check, the one algorithm the signatures leave open:

```text
state = static.state(now); live = await self.live.fetch_events()
static_events = static.events if state.status is OK else ()
live_tier1 = [e for e in live.events if e.kind is not None] if live.status is OK else []
match(s, l) := s.kind == l.kind and |s.timestamp - l.timestamp| <= MATCH_TOLERANCE
candidates = [(s, ("static", "live") if any(match(s, l) for l in live_tier1) else ("static",)) for s in static_events]
           + [(l, ("live",)) for l in sorted(live_tier1 by timestamp) if not any(match(s, l) for s in static_events)]
lockout  = first candidate with in_lockout_window(now, e.timestamp, pre, post)        # static first
upcoming = candidates' events with now <= timestamp <= now + 24h, sorted by timestamp
disagreements (only if state OK and live OK and live.coverage):
    static_only = unmatched static events whose NY date is within live.coverage
    live_only   = unmatched live tier-1 events
    sorted by at, first 10
```

- [ ] **Step 1: Write the failing live-feed and union tests** (`tests/agent/test_calendar.py`, rewritten). Use `httpx.MockTransport` and an injected clock.

```python
@pytest.mark.parametrize(
    ("handler", "error", "http_status"),
    [
        (raise_(httpx.ReadTimeout("t")), "ReadTimeout", None),
        (respond(403, text="denied"), "UnexpectedStatus", 403),
        (respond(429, text="slow"), "UnexpectedStatus", 429),
        (respond(200, text="<html>maintenance</html>"), "JSONDecodeError", None),
        (respond(200, json={"events": []}), "UnexpectedPayload", None),
    ],
)
async def test_a_failed_fetch_is_typed_never_cached_as_ok_and_retried_after_backoff(handler, error, http_status):
    clock = Clock(datetime(2026, 10, 21, 16, tzinfo=UTC))
    calls = Counter(handler)
    feed = ForexFactoryCalendar(transport=httpx.MockTransport(calls), clock=clock)
    first = await feed.fetch_events()
    assert (first.status, first.error, first.http_status, first.events) == (
        CalendarStatus.FAILED,
        error,
        http_status,
        (),
    )
    assert (await feed.fetch_events()).status is CalendarStatus.FAILED and calls.n == 1  # inside the backoff
    clock.advance(minutes=5, seconds=1)
    await feed.fetch_events()
    assert calls.n == 2


async def test_zero_usd_events_is_ok_with_coverage(): ...  # EUR-only payload: OK, events (), coverage set
async def test_a_malformed_item_is_counted_not_dropped(): ...  # [USD CPI, {"date": "garbage"}, "x"] -> unparsed_items 2, 1 event
async def test_an_expired_success_is_not_served_when_the_refetch_fails():  # Review Focus 2
    ...  # success at t0; clock +31 min; transport now raises -> FAILED, events ()


def test_live_titles_map_to_kinds_press_conference_first():
    assert tier1_kind("FOMC Press Conference", "USD") is Tier1Kind.FOMC_PRESS_CONFERENCE
    assert tier1_kind("FOMC Statement", "USD") is Tier1Kind.FOMC_STATEMENT
    assert tier1_kind("Federal Funds Rate", "USD") is Tier1Kind.FOMC_STATEMENT
    assert tier1_kind("Non-Farm Employment Change", "USD") is Tier1Kind.EMPLOYMENT_SITUATION
    assert tier1_kind("Core CPI m/m", "USD") is Tier1Kind.CPI and tier1_kind("PPI m/m", "US") is Tier1Kind.PPI
    assert tier1_kind("FOMC Meeting Minutes", "USD") is None and tier1_kind("CPI m/m", "EUR") is None


async def test_a_static_event_locks_out_while_the_live_feed_is_failed(): ...  # sources ("static",)
async def test_a_live_only_tier1_event_locks_out(): ...  # sources ("live",)
async def test_matching_is_same_kind_within_five_minutes(): ...  # live CPI +4 min: sources ("static","live"), no disagreements; +6 min: one static_only + one live_only
async def test_disagreements_are_bounded_to_ten(): ...
async def test_a_tentative_static_event_locks_out_like_any_other(): ...
async def test_expired_static_under_open_policy_is_unverified_with_no_static_lockout():
    ...  # now = the static CPI instant, live OK and empty: verified False, lockout None, lockout_rejection(open limits) None,
    # "UNVERIFIED" in prompt_text(), macro_note() == "static tier-1 calendar expired; live feed ok"


async def test_journal_block_always_has_every_key():
    block = (await union.check(...)).journal_block()
    assert set(block) == {
        "verified",
        "unknown_policy",
        "match_tolerance_minutes",
        "static",
        "live",
        "lockout",
        "disagreements",
    }
    assert set(block["static"]) == {
        "calendar_id",
        "sha256",
        "pinned",
        "status",
        "valid_through",
        "days_remaining",
        "error",
    }
    assert set(block["live"]) == {"source", "status", "fetched_at", "error", "http_status", "tier1_events", "coverage"}
```

- [ ] **Step 2: Write the failing prompt tests** (`tests/agent/test_calendar_prompt.py`, rewritten). Pin these exact texts:
  - verified clear, no events:
    > `"Evaluation time: 2026-09-22 19:18 UTC. Lockout CLEAR per static tier-1 calendar us-tier1-test-v1 (sha256 {sha[:8]}), valid through 2099-12-31; live cross-check (ForexFactory): ok. No tier-1 US releases (FOMC, CPI, PPI, Employment Situation) in the next 24 hours."`
  - verified with upcoming events: `" Next tier-1 release in 2h 5m. Upcoming tier-1 releases in the next 24 hours:\n- CPI at 2026-09-22 21:23 UTC (in 2h 5m)"` (sorted, one line each);
  - lockout: today's `LOCKOUT ACTIVE: Event '…' scheduled at … (lockout window: 60 minutes before and 30 minutes after the event). Trading lockout in effect.`;
  - unverified: `"… Lockout UNVERIFIED: static tier-1 calendar expired; live feed failed (ReadTimeout); treat scheduled-release timing as unknown."`

  Then:

```python
@pytest.mark.parametrize(
    "check", ALL_BRANCHES
)  # verified clear/upcoming/lockout; unverified expired/invalid, live ok/failed, with live upcoming
def test_the_prompt_says_verified_only_when_the_static_calendar_is_ok(check):
    text = check.prompt_text().lower()
    if check.verified:
        assert "unverified" not in text
    else:
        assert text.count("verified") == text.count("unverified") >= 1


def test_system_prompt_claims_no_verification():
    prompt = build_system_prompt(AppConfig())
    assert "verified" not in prompt.lower()
    assert "Do not reject a candidate on macro timing unless that status says LOCKOUT ACTIVE." in prompt
```

- [ ] **Step 3: Write the failing rule, site, evaluator and card tests.**
  - `tests/risk/test_rules.py`:
    - `test_closed_policy_refuses_unverified_before_the_window_test`: an exact text for `EXPIRED` and for `INVALID`, with `event_at=None`.
    - `test_open_policy_or_verified_behaves_as_before`: the existing `macro_lockout` assertions gain `calendar_verified=True, static_status=CalendarStatus.OK`.
  - `tests/agent/test_macro_lockout_sites.py`:
    - `test_closed_policy_refuses_with_one_text_at_all_four_sites`. Set `app_config.risk.macro_calendar_unknown = "closed"` and the calendar to `union_double(unverified_check())`. All four sites must equal the closed text:
      - `run_scan` returns None and observes SCAN `"Macro gate checked: <text>; entry alerts paused"`;
      - `RiskEvaluator.evaluate_candidate` returns `rejection_reason == <text>` and `rejection_rule == "macro_lockout"`;
      - `_macro_gate(..., now=NOW)`;
      - `_entry_macro_check(...)`.
  - `tests/agent/test_evaluator.py`:
    - `test_one_calendar_check_per_evaluation_feeds_the_gate_and_the_prompt`: `calendar.check.await_count == 1`, and the LLM prompt contains `check.prompt_text()`.
    - `test_an_unverified_calendar_sets_macro_note_and_never_says_cleared`: deterministic approval, `macro_note == "static tier-1 calendar expired; live feed ok"`, and `"Macro cleared"` not in `thesis_summary` (the thesis reads `"Macro lockout unverified (Regime: …)"`).
  - `tests/notifier/test_macro_line.py`: `format_alert_card` renders `"Macro Check:</b> Unverified — static tier-1 calendar expired; live feed ok"`, and `format_terminal_card` renders `"Macro Check:      Unverified — …"`. Neither contains `"Cleared"`. A `None` note keeps `"Cleared"`.
  - `tests/agent/test_shipped_calendar.py`: `test_fomc_2026_10_28_acceptance`. Build `UnionTier1Calendar(StaticTier1Calendar.load(SHIPPED, sha), OkEmptyFeed(), unknown_policy="open")` with `RiskLimits.from_config(load_config())`:
    - 14:35 ET (`datetime(2026, 10, 28, 18, 35, tzinfo=UTC)`) has `lockout.title == "FOMC Press Conference"`;
    - 10:35 ET is clear;
    - 14:50 ET has `check.lockout_rejection(limits).reason == "Macro event lockout: FOMC Press Conference at 18:30 UTC."`.

- [ ] **Step 4: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_calendar.py tests/agent/test_calendar_prompt.py tests/risk/test_rules.py tests/agent/test_macro_lockout_sites.py tests/agent/test_evaluator.py tests/notifier/test_macro_line.py tests/agent/test_shipped_calendar.py -q`. Expected: failures and ImportErrors on the new names.

- [ ] **Step 5: Implement** the live feed, the union, the rule change and `RiskLimits` field, then the callers. The feed's failure log is one WARNING with `extra={"event": "macro_calendar_fetch_failed", "error": <type name>, "http_status": …}`, never `str(e)`. `macro_clearance` and the LLM's JSON contract (`prompts.py:73`) stay unchanged. The callers:
  - `run_scan`'s whole-scan check: `check = await self.calendar.check(datetime.now(UTC), …)`, then `rejection = check.lockout_rejection(RiskLimits.from_config(self.config))`. The SCAN detail becomes `f"Macro gate checked: {rejection.reason}; entry alerts paused"`. The PEAD skip reason stays `f"macro lockout: {check.lockout.title}"`, or `"macro calendar unverified"` with no event.
  - `_macro_lockout_reason`.
  - The evaluator: one `check` per evaluation for the gate, `prompt_text()` and `macro_note` on both approved paths. The lockout thesis is `"Rejected: macro lockout active for '…'."` or `"Rejected: macro calendar unverified (policy closed)."`.
  - `get_status_report` uses `prompt_text()`.
  - The card's `macro_status` is `f"Unverified — {html.escape(note)}"` when `macro_note` is set.
  - The system-prompt line becomes `"Macro lockout is checked deterministically before you are consulted; the Economic Calendar Status below states the result, its source and the evaluation time. Do not reject a candidate on macro timing unless that status says LOCKOUT ACTIVE."`
  - Replay uses `UnionTier1Calendar.offline()`, and `_NoIoCalendar` is deleted.
  - Update every test double listed under Files to `union_double(clear_check())`, `lockout_check(...)` or `check.side_effect = …`, keeping each test's intent.

- [ ] **Step 6: Run the Step 4 command, then the doubles' files.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent tests/execution tests/risk tests/workflows tests/notifier tests/research/setups/test_replay.py tests/backtest -q`. Expected: all pass.

- [ ] **Step 7: Docs** (`docs/risk-policy.md`).
  - The `macro_lockout` row: the union calendar, `calendar_verified`, `risk.macro_calendar_unknown` and the closed text.
  - The scan order's lockout line: the source is the static calendar's union with the live feed.
  - `RiskLimits` gains `macro_calendar_unknown`.
  - Known divergences (:331-333), lockout half only: the static calendar is authoritative; an unverified static calendar is labelled under `open` and refuses under `closed`.

- [ ] **Step 8: Verify.** Run `env -u VIRTUAL_ENV uv run mypy agentic_trader` and `env -u VIRTUAL_ENV uv run pre-commit run --all-files`. Expected: clean. Also run `grep -rn "is_in_lockout_window\|get_macro_summary_for_prompt\|get_upcoming_tier1_events\|EconomicCalendarProtocol\|TIER_1_KEYWORDS\|is_tier_1" agentic_trader tests`. Expected: no output.

- [ ] **Step 9: Stage and report.** Suggested message: `Union tier-1 calendar: typed ForexFactory status, static-authoritative lockout through one risk rule at scan, evaluator, tap and preflight; prompt and card never claim an unverified clearance`.

---

### Task 3: Typed macro inputs, the UNKNOWN stress state and RegimeUnavailable

Spec §5.2 (from "Decision inputs" through "UNKNOWN defaults") and §5.3, plus §7.2's non-scan items.

**Files:**
- Modify:
  - `agentic_trader/agent/macro.py`, `agentic_trader/agent/regime.py`, `agentic_trader/config.py` (`RegimeConfig`), `config/config.yaml` (`regime:`);
  - `agentic_trader/agent/evaluator.py` (breakout thesis reads `regime.macro_stress`), `agentic_trader/presentation/formatters.py`, `agentic_trader/agent/macro_explainer.py`;
  - `agentic_trader/agent/copilot.py` (`get_explain_macro_html`, the `MACRO_REPORT` audit), `agentic_trader/cli/commands/trade.py` (`explain-macro`);
  - tests: `tests/agent/test_macro_intelligence.py`, `tests/agent/test_regime.py`, `tests/agent/test_regime_journal_block.py`, `tests/notifier/test_telegram_macro.py`, `tests/cli/test_cli_smoke.py`, and every `RegimeSnapshot(...)` construction lacking `risk_multiplier=` (in `tests/notifier/test_telegram_interactive.py`, `tests/agent/test_regime.py`, `tests/agent/test_evaluator.py`, `tests/presentation/test_presentation.py`);
  - `docs/risk-policy.md`, `docs/card-evidence.md`.
- Create: `tests/agent/test_macro_unknown.py`, `tests/config/test_regime_config.py`

**Interfaces:**
- Produces:
  - `MacroStressLevel.UNKNOWN = "UNKNOWN"`.
  - `@dataclass(frozen=True) MacroInput(name: str, value: float | None, source: str, observed_on: date | None, decision: bool, error: str | None)`. `error` is a type name, `"no_observation"` or `"unexpected_payload"`.
  - `DECISION_INPUTS = ("vix", "3m", "2y", "10y", "hy_oas")` and `CONTEXT_INPUTS = ("5y", "30y", "dxy", "t10yie", "t5yie")`.
  - `MACRO_INPUT_SOURCES: Mapping[str, str]`:
    - `vix` → `"yfinance:^VIX"`, `3m` → `"yfinance:^IRX"`, `2y` → `"fred:DGS2"`, `10y` → `"yfinance:^TNX"`, `hy_oas` → `"fred:BAMLH0A0HYM2"`;
    - `5y` → `"yfinance:^FVX"`, `30y` → `"yfinance:^TYX"`, `dxy` → `"yfinance:DX-Y.NYB"`, `t10yie` → `"fred:T10YIE"`, `t5yie` → `"fred:T5YIE"`.
  - `fetch_yahoo_input(name: str, ticker: str, *, decision: bool) -> MacroInput` (sync). The value is rounded to 3 dp, and `observed_on` is the date of the last non-null Close bar.
  - `FredDataClient.fetch_input(name: str, series_id: str, *, decision: bool, force_refresh: bool = False) -> MacroInput`. A body whose first non-empty line is not a two-field header with first field `date` or `observation_date` (case-insensitive) is `unexpected_payload`.
  - `@dataclass(frozen=True) MacroEvaluation(inputs: Mapping[str, MacroInput], missing: tuple[str, ...], missing_decision: tuple[str, ...], report: MacroIntelligenceReport | None, stress: MacroStressAssessment)`.
  - `unknown_macro_stress(config: RegimeConfig, missing: Sequence[str]) -> MacroStressAssessment`: level `UNKNOWN`, `risk_multiplier=config.unknown_risk_multiplier`, `squeeze_breakout_allowed=config.unknown_breakout_allowed`, `min_rr_threshold=2.0`, `key_drivers=[f"Missing decision inputs: {', '.join(missing)}"]`.
  - `MacroIntelligenceEngine(config=None, fred_client=None, yahoo_fetch: Callable[..., MacroInput] = fetch_yahoo_input)` with:
    - `async read_inputs(names: Collection[str], *, force_refresh: bool = False) -> dict[str, MacroInput]` (Yahoo via `asyncio.to_thread`, all concurrent);
    - `async evaluate(force_refresh: bool = False) -> MacroEvaluation`, which never raises for missing data and caches only a complete evaluation.
  - `compute_compound_macro_stress(vix, yield_spreads, credit, config=None)` (no `inflation`).
  - Optional report fields: `TreasuryYields.yield_5y` and `yield_30y`, `YieldCurveSpreads.curvature_butterfly_bps`, `MacroIntelligenceReport.dxy` and `MacroIntelligenceReport.inflation` (None when either breakeven is missing). `observation_dates` keeps its FRED-series keys, derived from the inputs.
  - `RegimeConfig`:
    - adds `unknown_risk_multiplier: float = Field(0.5, ge=0.10, lt=1.0)` and `unknown_breakout_allowed: bool = False`;
    - removes `credit_oas_enabled` and `inflation_breakeven_enabled`.
  - `regime.RegimeUnavailable(Exception)`, constructed as `RegimeUnavailable(inputs: Mapping[str, MacroInput])`:
    - attributes `inputs` and `missing: tuple[str, ...]` (the missing names in canonical order, decision inputs first);
    - message `f"Market regime unavailable: missing {', '.join(missing)}"`.
  - `RegimeSnapshot`:
    - `risk_multiplier: float` loses its default;
    - adds `macro_stress: MacroStressAssessment | None = None` and `macro_inputs: Mapping[str, MacroInput] = field(default_factory=dict)`;
    - keeps `macro_report` and `macro_unavailable_reason`. The reason is `"Yield-curve enrichment is disabled in configuration"` or `"Missing decision inputs: …"`, never `str(exc)`.
  - `REGIME_BLOCK_KEYS` is the seven existing keys plus `macro_enabled`, `inputs`, `missing`, `missing_decision` and `clock_regime`.
  - `regime_journal_block(snapshot)`:
    - `macro_enabled = snapshot.macro_stress is not None`;
    - `inputs` maps each name to `{value, source, observed_on, decision, error}`;
    - `clock_regime` is `timestamp.astimezone(ET_TZ).tzname()`.
  - `unavailable_regime_block(exc: BaseException, *, at: datetime, macro_enabled: bool) -> dict[str, Any]`: every key present, snapshot fields None, and `inputs`/`missing` from a `RegimeUnavailable` (empty otherwise).

- [ ] **Step 1: Write the failing tests** (`tests/agent/test_macro_unknown.py`). They use fake Yahoo and FRED sources built from a `{name: value | None}` map, passed as `yahoo_fetch` and `fred_client`. A `None` value yields `MacroInput(value=None, error="no_observation")`, and an exception value yields its type name.

```python
DECISION_OK = {"vix": 17.2, "3m": 3.95, "2y": 4.35, "10y": 4.95, "hy_oas": 3.70}  # MODERATE: credit ELEVATED


async def test_context_inputs_never_move_the_stress():
    baseline = (await engine_with({**DECISION_OK, **CONTEXT_OK}).evaluate()).stress
    rng = random.Random(20261021)
    for _ in range(200):
        context = {n: rng.choice([None, rng.uniform(-1.0, 8.0)]) for n in CONTEXT_INPUTS}
        context["dxy"] = rng.choice([None, rng.uniform(80.0, 120.0)])
        assert (await engine_with({**DECISION_OK, **context}).evaluate(force_refresh=True)).stress == baseline


@pytest.mark.parametrize("name", CONTEXT_INPUTS)
async def test_one_missing_context_input_keeps_the_report_and_stress(name): ...  # report not None; missing == (name,)


@pytest.mark.parametrize("name", ["3m", "2y", "10y", "hy_oas"])
@pytest.mark.parametrize(("vix", "min_rr"), [(17.2, 2.0), (25.0, 2.2)])
async def test_each_missing_decision_input_gives_unknown_at_half_with_breakouts_off(name, vix, min_rr):
    snapshot = await detector_with({**DECISION_OK, **CONTEXT_OK, "vix": vix, name: None}).get_regime(force_refresh=True)
    assert snapshot.macro_stress.level is MacroStressLevel.UNKNOWN and snapshot.macro_report is None
    assert (snapshot.risk_multiplier, snapshot.breakout_allowed, snapshot.min_rr_threshold) == (0.5, False, min_rr)
    assert f"Macro: UNKNOWN (missing {name}; ×0.50, breakouts suppressed)" in snapshot.summary_text


async def test_missing_vix_raises_regime_unavailable_with_its_inputs():
    with pytest.raises(RegimeUnavailable, match=r"^Market regime unavailable: missing vix$") as caught:
        await detector_with({**DECISION_OK, **CONTEXT_OK, "vix": None}).get_regime(force_refresh=True)
    assert caught.value.inputs["vix"].error == "no_observation" and caught.value.missing == ("vix",)


async def test_disabled_yield_curve_is_vix_only_at_full_size():
    snapshot = await detector_with(..., RegimeConfig(yield_curve_enabled=False)).get_regime(force_refresh=True)
    block = regime_journal_block(snapshot)
    assert (snapshot.risk_multiplier, block["macro_enabled"], block["macro_stress"]) == (1.0, False, None)
    assert set(snapshot.macro_inputs) == {"vix", "10y", "dxy"}


async def test_unknown_snapshot_is_cached_and_force_refresh_refetches(): ...
async def test_a_partial_evaluation_is_not_cached_and_recovery_is_measured():  # Review Focus 3
    ...  # engine: 2y None -> UNKNOWN; source recovers; next evaluate() (no force) -> report not None, stress LOW/MODERATE


async def test_fred_html_200_is_unexpected_payload():
    client = FredDataClient()
    with patch.object(
        client, "_fetch_csv_text", new=AsyncMock(return_value="<html><title>Site Maintenance</title></html>")
    ):
        result = await client.fetch_input("2y", "DGS2", decision=True)
    assert (result.value, result.error, result.source) == (None, "unexpected_payload", "fred:DGS2")


def test_regime_block_carries_inputs_missing_and_clock_regime(): ...  # 13 keys; EDT on 2026-10-21, EST on 2026-11-03
def test_unavailable_regime_block_has_null_snapshot_fields(): ...
```

  Also add:
  - in `tests/config/test_regime_config.py`: `RegimeConfig(unknown_risk_multiplier=1.0)` and `RegimeConfig(unknown_risk_multiplier=0.05)` raise. `RegimeConfig(credit_oas_enabled=True, inflation_breakeven_enabled=False)` constructs and has neither attribute.
  - in `tests/notifier/test_telegram_macro.py`:
    - the `/macro` dashboard for an `UNKNOWN` snapshot contains `"UNKNOWN"`, `"missing 2y, hy_oas"` and `"×0.50"` but not `"uses volatility only"`;
    - the indicators block and `MacroExplainer.explain_deterministic` render `"N/A"` for None 5y, 30y, DXY and inflation;
    - `RegimeDetector.get_prompt_context` on `UNKNOWN` contains `"Compound Macro Stress: UNKNOWN (missing 2y, hy_oas; Risk Multiplier: 0.50x)"`.

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_macro_unknown.py tests/config/test_regime_config.py tests/notifier/test_telegram_macro.py -q`. Expected: ImportError or failures.

- [ ] **Step 3: Implement** macro.py, regime.py and the config change, then move the callers:
  - `explain-macro` and `/explain_macro` call `evaluate()`. With `report is None` they print `"Macro report unavailable: missing decision inputs: …"`.
  - The `/macro` audit adds `"regime": regime_journal_block(regime)`.
  - The evaluator's breakout thesis reads `regime.macro_stress`.
  - The formatter badge map gains `UNKNOWN`.
  - Delete both `_fetch_ticker_sync` copies.
  - In `config/config.yaml`, drop `credit_oas_enabled` and `inflation_breakeven_enabled`. Add `unknown_risk_multiplier: 0.5` and `unknown_breakout_allowed: false`, each with a comment (applies when a decision input is missing; never 1.0).
  - Update the listed tests to the new interfaces. Patch `MacroIntelligenceEngine` via its `yahoo_fetch` and `fred_client` parameters, not module globals.

- [ ] **Step 4: Run the Step 2 command, then the regime surface.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent tests/notifier tests/presentation tests/cli/test_cli_smoke.py tests/config -q`. Expected: all pass.

- [ ] **Step 5: Docs.**
  - `docs/risk-policy.md`: the per-trade budget section names `UNKNOWN` (`regime.unknown_risk_multiplier`, breakouts per `regime.unknown_breakout_allowed`). Line :61 adds "a missing decision input is UNKNOWN at 0.5×, never 1.0". :331-333's regime half becomes "fails closed only when VIX is unavailable".
  - `docs/card-evidence.md` "Journaled regime" (:293-299): the new keys and `UNKNOWN`.

- [ ] **Step 6: Verify.** Run mypy and `pre-commit run --all-files`. Expected: clean. `grep -rn "get_macro_report\|fetch_latest_value\|_fetch_ticker_sync\|credit_oas_enabled\|inflation_breakeven_enabled" agentic_trader tests config` returns nothing.

- [ ] **Step 7: Stage and report.** Suggested message: `Regime fail-safes: typed macro inputs with sources and observation dates, UNKNOWN stress at 0.5x with breakouts off for a missing decision input, RegimeUnavailable for a missing VIX`.

---

### Task 4: run_scan order, idle check, guarded regime and the digest

Spec §5.2 ("VIX unavailable in `run_scan`", "Digest in `finally`"), §5.4 "Digest", §7.2's `run_scan` items and §7.5's overnight-idle item.

**Files:**
- Create: `agentic_trader/agent/scan_journal.py`, `tests/agent/test_scan_gates.py`
- Modify: `agentic_trader/execution/durable.py` (`ScanStatus`), `agentic_trader/agent/copilot.py`, `agentic_trader/cli/commands/service.py` (`make_suggestion_scan`), `tests/agent/test_scan_drift.py`, `tests/agent/test_scan_budget.py` (digest tests pass `trigger=ScanTrigger.SUGGESTION_SCAN`), `tests/cli/test_service_scheduling.py`, `tests/integration/test_postgres.py`, `docs/operational-monitoring.md`

**Interfaces:**
- Consumes: Task 2's `CalendarCheck.journal_block()` and `lockout_rejection`. Task 3's `RegimeUnavailable`, `unavailable_regime_block` and `regime_journal_block`.
- Produces:
  - `durable.ScanStatus(StrEnum)`: `COMPLETED = "completed"`, `MACRO_LOCKOUT = "macro_lockout"`, `REGIME_UNAVAILABLE = "regime_unavailable"`.
  - In `scan_journal.py`:
    - `@dataclass(frozen=True) ScanNotice(key: str, text: str)`.
    - `async append_scan_journal(db: SignalDatabase, *, et_date: str, scan_id: str, payload: Mapping[str, Any] | None, notice: ScanNotice | None) -> None`. In one transaction it takes the `workflows.lock`, appends the `scan_candidates_ranked` event (stream `scan/{et_date}`, key `scan_candidates_ranked/{scan_id}`) when `payload` is not None, and calls `add_notification(key, NotificationKind.MESSAGE, {"text": …, "formatted": False})` when `notice` is set. It raises on failure; the copilot catches it and records `summary["scan_journal_error"] = type name`.
    - `regime_unavailable_notice(errors: Mapping[str, str], *, at: datetime, trigger: ScanTrigger, pead_skipped: bool) -> ScanNotice`, with key `f"regime-unavailable/{NY date of at}"` and text `f"⚠️ Market regime unavailable ({errors_text}) at {HH:MM NY} NY: the {trigger.removesuffix('_scan')} scan was skipped — no cards{', no PEAD decision' if pead_skipped else ''}. Later scans retry; this notice is sent once per New York date."`. `errors_text` is `"vix: ReadTimeout"`, or `", "`-joined pairs in `DECISION_INPUTS` order.
  - The copilot's journal writer, `_journal_scan_ranking`, gains keywords `status: ScanStatus`, `gate: Mapping[str, Any] | None`, `scheduled_time_et: str | None`, `calendar_block: Mapping[str, Any]`, `regime_block: Mapping[str, Any] | None` and `notice: ScanNotice | None = None`. It writes through `append_scan_journal`. Every payload gains `status`, `gate`, `scheduled_time_et` and `calendar`.
  - The gate shape for regime is `{"rule": "regime_unavailable", "missing": [...], "errors": {name: type}}`. For a non-`RegimeUnavailable` exception it is `missing: []` and `errors: {"regime": TypeName}`.
  - `_journal_skipped_drift(...) -> bool` (whether a decision was journaled).
  - Every non-dry run's summary carries `trigger`, `budget`, `status` and `scheduled_time_et`. A gated non-dry scan (lockout or regime) records a gated summary, `{"scope", "trigger", "budget", "status", "scheduled_time_et", "gate_reason"}`, as both `last_scan_summary` and a `_session_scan_stats` entry.
    - `gate_reason` is `f"macro lockout: {title} at {HH:MM} UTC"`, `"macro calendar unverified"` or `"market regime unavailable"`.

- [ ] **Step 1: Write the failing tests** (`tests/agent/test_scan_gates.py`, on `budget_desk`/`scan_desk`; the PEAD case goes in `tests/agent/test_scan_drift.py`'s `drift_desk`).

```python
VIX_DOWN = RegimeUnavailable({"vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout")})


async def test_vix_missing_suggestion_scan_journals_regime_unavailable_and_one_notice(budget_desk, temp_db):
    budget_desk.regime_detector.get_regime.side_effect = VIX_DOWN
    assert await suggestion(budget_desk) is None
    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["candidates"], p["trigger"]) == ("regime_unavailable", [], "suggestion_scan")
    assert p["gate"] == {"rule": "regime_unavailable", "missing": ["vix"], "errors": {"vix": "ReadTimeout"}}
    assert p["regime"]["inputs"]["vix"]["error"] == "ReadTimeout" and p["regime"]["vix"] is None
    assert p["calendar"]["verified"] is True
    [notice] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notice.dedup_key == f"regime-unavailable/{today_ny()}"
    assert "the suggestion scan was skipped — no cards." in notice.payload["arguments"]["text"]
    budget_desk.readiness.observe.assert_any_await(
        HealthComponent.SCAN, False, "Regime unavailable (vix: ReadTimeout); scan skipped"
    )


async def test_a_second_failure_the_same_new_york_date_adds_no_notice(
    budget_desk, temp_db
): ...  # two events, one work item
async def test_the_notice_dedups_per_new_york_date(
    temp_db,
):  # writer level, two dates -> two items, same date twice -> one
    for day in ("2026-10-28", "2026-10-28", "2026-10-29"):
        await append_scan_journal(
            temp_db,
            et_date=day,
            scan_id=uuid4().hex,
            payload=None,
            notice=regime_unavailable_notice(
                {"vix": "ReadTimeout"}, at=noon_ny(day), trigger=ScanTrigger.SWING_SCAN, pead_skipped=False
            ),
        )
    assert sorted(w.dedup_key for w in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)) == [
        "regime-unavailable/2026-10-28",
        "regime-unavailable/2026-10-29",
    ]


async def test_event_and_notice_share_one_transaction(
    temp_db, monkeypatch
): ...  # add_notification raises -> no event persisted
async def test_any_regime_exception_is_classified_by_type_name(
    budget_desk, temp_db
): ...  # RuntimeError -> errors {"regime": "RuntimeError"}
async def test_no_regime_read_on_an_idle_or_locked_out_scan(
    budget_desk,
): ...  # get_regime.assert_not_awaited() for both
async def test_overnight_swing_scan_under_alpaca_execution_is_idle_and_journals_nothing(scan_desk, temp_db, app_config):
    ...  # contracts /MES, /MNQ (futures) + SPY, QQQ (equity); execution_mode "alpaca"; is_session_active: "all" open,
    # "equity" closed; FULL + SWING_SCAN -> None, no events, get_regime not awaited,
    # SCAN observed (True, "Idle: nothing eligible (2 not executable, 2 equity session closed)")


async def test_a_dry_run_with_vix_missing_journals_and_notifies_nothing(budget_desk, temp_db, capsys): ...
async def test_a_restricted_scan_with_vix_missing_gets_readiness_and_reply_only(budget_desk, temp_db): ...
def test_the_scan_result_text_names_a_regime_outage():
    assert TradingCopilot._scan_result_text("SPY", None) == (
        "No fresh card for SPY: the scan did not run (trading halt, closed session, macro lockout or market regime unavailable)."
    )


async def test_telegram_scan_says_the_scan_did_not_run_with_its_gate_reason(budget_desk):
    ...
    # "⏸️ <b>Scan Did Not Run:</b> market regime unavailable." ; no gated summary -> "… no instrument is eligible in the current session."


async def test_digest_counts_suggestion_scans_only_and_lists_skipped_slots(budget_desk): ...
async def test_digest_lists_both_skipped_slots_when_no_suggestion_scan_completed(budget_desk):  # Review Focus 4
    ...  # 10:35 regime unavailable, 14:35 lockout (lockout_check("FOMC Press Conference", 18:30Z))
    assert text == (
        f"📋 Scan digest {d}: no suggestion scans completed this session. Skipped: 10:35 (market regime "
        "unavailable), 14:35 (macro lockout: FOMC Press Conference at 18:30 UTC)."
    )
```

  Also add:
  - in `tests/agent/test_scan_drift.py`, `test_a_regime_unavailable_decision_scan_journals_a_skipped_pead_decision`: `pead_decision` with status `skipped` and reason `"regime unavailable"`, and the notice text ends `"— no cards, no PEAD decision. Later scans retry; …"`;
  - in `tests/cli/test_service_scheduling.py`, `test_the_digest_runs_when_the_scan_raises` and `test_a_digest_failure_does_not_mask_the_scan_exception` (the scan raises `RuntimeError("scan")` and the digest raises `ValueError`; `pytest.raises(RuntimeError)`, and the digest failure is logged);
  - in `tests/integration/test_postgres.py`, `test_postgres_scan_journal_event_and_notice_commit_together`: `append_scan_journal` with a payload and a notice writes both. A second call whose notice add raises (monkeypatch) leaves neither row.

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_scan_gates.py tests/agent/test_scan_drift.py tests/cli/test_service_scheduling.py -q`. Expected: failures.

- [ ] **Step 3: Implement.**
  - New order: lock → registry snapshot → halt → session gate → **idle check** → lockout → **guarded regime** → dynamic universe → drift → selection → … (spec §5.2).
  - The idle check and selection share one helper, `_select_contracts(items, *, asset_class: str, target_syms: list[str] | None, equity_open: bool) -> tuple[list[tuple[str, Any]], list[str], list[str]]` (selected, not executable, closed session). `equity_open` is computed before the idle check.
  - A scan is idle iff the configured contracts select nothing and not `equity_open and (attempt_dynamic or drift_scan or operator_dynamic is not None)`. The detail is `f"Idle: nothing eligible ({n} not executable, {m} equity session closed)"`.
  - On a regime failure, first compute `pead_skipped = self._skipped_drift(alpha_snapshot, scheduled_time_et, reason="regime unavailable") is not None` (only when `drift_scan`), because the notice text needs it. Then run spec steps 1–5 in order:
    - the event and notice together when the scan qualifies (unrestricted, non-dry, `budget != NONE`);
    - the notice alone for a non-dry unrestricted NONE-budget scan;
    - nothing but readiness and the reply for a restricted scan;
    - a dry run logs and prints `"[DRY RUN] Market regime unavailable (vix: ReadTimeout); scan skipped."`.
  - `publish_scan_digest` keeps the `trigger == SUGGESTION_SCAN` summaries and splits completed from skipped:
    - with none at all: `"… no suggestion scans ran this session."`;
    - with only skipped: `"… no suggestion scans completed this session. Skipped: …."`;
    - otherwise today's text plus `" Skipped: …."`;
    - each skip renders as `f"{scheduled_time_et or 'unscheduled'} ({gate_reason})"`.
  - `make_suggestion_scan`: `try: …scan… finally: if digest: try: await copilot.publish_scan_digest() except Exception: logger.exception(…, extra={"event": "scan_digest_failed"})`.

- [ ] **Step 4: Run the Step 2 command, then `tests/agent tests/cli`.** Expected: all pass.

- [ ] **Step 5: Postgres.** If a local server is available, run `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest tests/integration/test_postgres.py -k scan_journal --run-postgres -q`. Expected: pass. Otherwise report "not run" (Task 9 runs it).

- [ ] **Step 6: Docs.** In `docs/operational-monitoring.md`, add the `regime-unavailable/{et_date}` notice beside pead-unavailable and missed-slot. It is sent once per New York date. SCAN readiness is the health signal, so no new readiness component exists, and the spec gives the reason.

- [ ] **Step 7: Verify and stage.** Run mypy and `pre-commit run --all-files`. Expected: clean. Suggested message: `run_scan: idle check before any read, regime read after the lockout and guarded: a missing VIX journals regime_unavailable, skips PEAD and sends one notice per NY date; digest always runs and lists skipped slots`.

---

### Task 5: A complete scan journal: lockout events, zero-candidate scans and the collect funnel

Spec §5.6 ("Which scans journal", "Payload changes") and §7.5's journal items.

**Files:**
- Modify: `agentic_trader/agent/scan_journal.py`, `agentic_trader/agent/copilot.py` (`_admit_candidate`, the collect loop, the journal condition at :1591, the lockout gate), `tests/agent/test_scan_shadow_ranker.py` (the empty-ranking expectation, if any), `docs/card-evidence.md`, `docs/risk-policy.md`
- Create: `tests/agent/test_scan_journal_funnel.py`

**Interfaces:**
- Consumes: Task 4's `append_scan_journal`, `ScanStatus` and `_journal_scan_ranking` keywords.
- Produces (in `scan_journal.py`):
  - `RefusalKind(StrEnum)`: `DUPLICATE = "duplicate"`, `ACCOUNT_RISK_UNAVAILABLE = "account_risk_unavailable"`, `REJECTED = "rejected"`.
  - `@dataclass(frozen=True) AdmissionRefusal(kind: RefusalKind, rule: str | None, reason: str)`, where `__str__` returns `reason`. `reason` keeps today's text exactly: `"duplicate"`, `"account risk unavailable: …"` or `"rejected: …"`. `rule` is `det_res.rejection_rule`.
  - `@dataclass ScanFunnel`:
    - fields `selected, scanned, fetch_failed, insufficient, coverage_excluded, dynamic_excluded, raw_setups, duplicates, account_risk_unavailable, setups_lost_to_errors, admitted: int = 0` and `raw_setups_by_strategy, rejected_by_rule, errors: Counter[str]`;
    - `record_refusal(refusal: AdmissionRefusal) -> None`, where a rejection counts under `rule or "unclassified"`;
    - `journal_block() -> dict[str, Any]` (plain dicts with sorted keys).
  - `TradingCopilot._admit_candidate(...) -> tuple[tuple[Any, Any, Any] | None, AdmissionRefusal | None]`. Drift outcomes use `str(refusal)`, so `pead_decision` text is unchanged.
  - The journal condition becomes `not dry_run and budget != NONE and not symbols and timeframe is None` (after the idle check), with no `native_count`. Every payload gains `funnel`: the block for `completed`, null when gated.
  - A lockout-gated qualifying scan journals:
    - `status: macro_lockout`;
    - `gate: {"rule": "macro_lockout", "reason": rejection.reason}`;
    - `regime: null`, `funnel: null`, `candidates: []` and the calendar block.

  Funnel counting, by the loop branch that increments it:
  - `selected = len(scan_selected)`;
  - `dynamic_excluded`: the loop-top skip;
  - `fetch_failed`, `insufficient`, `coverage_excluded` (the static coverage `continue`) and `scanned`: their branches;
  - `raw_setups`: each native candidate iterated;
  - `errors[type(exc).__name__]`: the per-contract guard;
  - `setups_lost_to_errors`: setups that contract counted minus those it classified, when the guard fires;
  - `admitted = native_count`.

- [ ] **Step 1: Write the failing tests** (`tests/agent/test_scan_journal_funnel.py`).

```python
async def test_a_zero_candidate_eligible_scan_journals_once_with_a_full_funnel(budget_desk, temp_db):
    budget_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: []
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, trigger=ScanTrigger.SUGGESTION_SCAN)
    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["candidates"], p["gate"]) == ("completed", [], None)
    assert p["funnel"]["selected"] == p["funnel"]["scanned"] == 5 and p["funnel"]["raw_setups"] == 0

async def test_funnel_identity_holds_across_every_refusal_kind(budget_desk, temp_db, monkeypatch):   # Review Focus 5
    # AAA duplicate; BBB account risk unavailable (ledger.current_risk raises ValueError on its call);
    # CCC rejected rule "per_trade_notional"; DDD rejected with rejection_rule None; EEE admitted;
    # FFF two setups, is_duplicate_recent raises RuntimeError on the first; GGG scan_contract raises RuntimeError.
    ...
    f = payload["funnel"]
    assert f["rejected_by_rule"] == {"per_trade_notional": 1, "unclassified": 1}
    assert f["errors"] == {"RuntimeError": 2} and f["setups_lost_to_errors"] == 1 and f["admitted"] == 1
    assert f["raw_setups"] == (f["duplicates"] + f["account_risk_unavailable"] + sum(f["rejected_by_rule"].values())
                               + f["admitted"] + f["setups_lost_to_errors"]) == 6
    assert [c["contract"] for c in payload["candidates"]] == ["EEE"]

async def test_a_lockout_gated_suggestion_scan_journals_macro_lockout_with_null_regime(budget_desk, temp_db):
    budget_desk.calendar = union_double(lockout_check("FOMC Press Conference", datetime(2026, 10, 28, 18, 30, tzinfo=UTC),
                                                      kind=Tier1Kind.FOMC_PRESS_CONFERENCE))
    ...
    assert p["gate"] == {"rule": "macro_lockout", "reason": "Macro event lockout: FOMC Press Conference at 18:30 UTC."}
    assert (p["status"], p["regime"], p["funnel"], p["candidates"]) == ("macro_lockout", None, None, [])
    assert p["calendar"]["lockout"]["title"] == "FOMC Press Conference"
    budget_desk.regime_detector.get_regime.assert_not_awaited()

async def test_halted_closed_session_idle_dry_and_restricted_scans_journal_nothing(...): ...
async def test_drift_refusals_keep_their_text(drift_desk, temp_db): ...   # existing pead outcomes unchanged
```

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_scan_journal_funnel.py -q`. Expected: failures.

- [ ] **Step 3: Implement** the refusal type, the funnel and the journal condition, and wire both gates through `_journal_scan_ranking`.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/agent tests/research/setups -q`. Expected: all pass. The existing `tests/agent/test_scan_shadow_ranker.py` and `tests/agent/test_scan_drift.py` journal assertions still hold, apart from deliberate updates where a test asserted "no event" for a zero-candidate eligible scan.

- [ ] **Step 5: Docs.**
  - `docs/card-evidence.md` Coverage (:372-380): every eligible scan journals once, including zero-candidate, lockout-gated and regime-unavailable scans. Halted, closed-session, idle, dry and restricted scans never do. Describe `status`/`gate`/`funnel`, readers defaulting `status` to `completed`, and that `events_considered` counts scan events, not scans with candidates. Correct "18 of the 22" to the review's recount: 11 unjournaled plus 7 journaled before `signal_id` existed (report §3.1 CS1).
  - `docs/risk-policy.md` "What the operator sees": collect-phase refusals are now counted per rule in the journal funnel, not only logged.

- [ ] **Step 6: Verify and stage.** Run mypy and `pre-commit run --all-files`. Expected: clean. Suggested message: `Complete scan journal: every eligible scan journals once with status, gate, calendar and a per-rule collect funnel; lockout-gated and zero-candidate scans included; typed admission refusals`.

---

### Task 6: Journal-only swing and intraday scans

Spec §5.4 (except Digest) and §7.3.

**Files:**
- Modify: `agentic_trader/config.py` (`ScanBudget`), `agentic_trader/execution/durable.py` (`RankedOutcome.JOURNAL_ONLY`), `agentic_trader/agent/copilot.py`, `agentic_trader/cli/commands/service.py`, `config/config.yaml` (`scheduler:`), `tests/cli/test_service_scheduling.py`, `docs/production.md`, `docs/card-evidence.md`
- Create: `tests/agent/test_journal_only_scans.py`

**Interfaces:**
- Consumes: Task 5's journal condition and funnel.
- Produces:
  - `ScanBudget.JOURNAL_ONLY = "journal_only"` and `RankedOutcome.JOURNAL_ONLY = "journal_only"`.
  - Under `JOURNAL_ONLY`, `run_scan` collects, ranks, journals, observes SCAN and runs `monitor_positions`. It calls no shadow, `book_sizer.prepare`, `card_stats.latest`, card-policy `decide`, `_news_blocks`, budget read, send loop, LLM or `record_signal`, and every native outcome is `journal_only`.
  - `drift_scan` additionally requires `budget not in (NONE, JOURNAL_ONLY)`.
  - `register_swing_scan` passes `kwargs={"budget": ScanBudget.JOURNAL_ONLY, "trigger": ScanTrigger.SWING_SCAN}` and keeps `next_run_time=datetime.now(UTC)`.
  - `register_intraday_scan`'s job passes `budget=ScanBudget.JOURNAL_ONLY` and stays restricted to `non_universe_contracts`.

- [ ] **Step 1: Write the failing tests** (`tests/agent/test_journal_only_scans.py`, plus the updated scheduling assertions).

```python
async def test_a_daemon_start_cannot_record_a_signal(budget_desk, temp_db, app_config):
    scheduler = MagicMock()
    register_swing_scan(scheduler, budget_desk, app_config, use_llm=True)
    job = scheduler.add_job.call_args
    budget_desk.news_context = AsyncMock()
    budget_desk.book_sizer.prepare = AsyncMock()
    await job.args[0](*job.kwargs["args"], **job.kwargs["kwargs"])
    assert await temp_db.get_recent_signals(limit=10) == []
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []
    assert {c.kwargs["use_llm"] for c in budget_desk.evaluator.evaluate_candidate.await_args_list} == {False}
    budget_desk.news_context.for_symbols.assert_not_awaited()
    budget_desk.book_sizer.prepare.assert_not_awaited()
    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["budget"], p["trigger"]) == ("journal_only", "swing_scan")
    assert {c["outcome"] for c in p["candidates"]} == {"journal_only"} and len(p["candidates"]) == 5


async def test_the_intraday_job_is_journal_only_restricted_and_never_journals(scan_desk, temp_db, app_config): ...
def test_shipped_config_disables_the_intraday_job():
    assert (
        yaml.safe_load((WORKSPACE_ROOT / "config/config.yaml").read_text())["scheduler"]["intraday_scan_enabled"]
        is False
    )
```

  In `tests/cli/test_service_scheduling.py`, `test_swing_scan_job_names_its_trigger_and_still_runs_at_startup` now expects `ScanBudget.JOURNAL_ONLY`, and the intraday registration tests expect `budget=ScanBudget.JOURNAL_ONLY`.

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_journal_only_scans.py tests/cli/test_service_scheduling.py -q`. Expected: failures.

- [ ] **Step 3: Implement.** In `config/config.yaml`:
  - the swing comment becomes `# every 4 h from daemon start; journal-only (sends no cards)`;
  - add `intraday_scan_enabled: false  # idle by construction (spec 2026-10-21 §4 item 4); JOURNAL_ONLY when enabled`.

- [ ] **Step 4: Run the Step 2 command and the NONE-budget regressions.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_scan_budget.py::test_no_budget_records_every_approved_candidate tests/agent/test_scan_budget.py::test_a_dry_run_never_spends_the_budget_and_records_nothing tests/agent/test_symbol_scan.py tests/agent -q`. Expected: all pass. NONE still sends: dry run, `--no-budget`, Re-evaluate.

- [ ] **Step 5: Docs.**
  - `docs/production.md` ownership and scheduling (:22-34):
    - swing scans are journal-only and keep their run at start (why: the `scan` readiness observation);
    - "session gated" becomes exact (`asset_class="all"` is open whenever futures trade; under Alpaca execution such a scan is idle overnight);
    - intraday ships disabled and would be journal-only.
  - `docs/card-evidence.md`: add the `journal_only` outcome. Swing candidates stay in `card_stats` until v2.

- [ ] **Step 6: Verify and stage.** Run mypy and `pre-commit run --all-files`. Expected: clean. Suggested message: `Swing and intraday scans are journal-only: no LLM, news, book, budget or card; the run at daemon start can no longer spend a slot; intraday disabled in config`.

---

### Task 7: Broad-index ETF exclusion from native cards

Spec §5.5 and §7.4.

**Files:**
- Modify: `agentic_trader/config.py` (`ScanConfig`, `DEFAULT_BROAD_INDEX_ETFS`), `agentic_trader/execution/durable.py` (`RankedOutcome.BROAD_INDEX_EXCLUDED`), `agentic_trader/agent/copilot.py` (exclusion mask, `book_candidates`, `_news_blocks`, send loop), `config/config.yaml` (`scan:`), `tests/fixtures/config.yaml` (`scan: {broad_index_etfs: []}`), `tests/config/test_scan_config.py`, `docs/card-evidence.md`
- Create: `tests/agent/test_broad_index_exclusion.py`

**Interfaces:**
- Produces:
  - `DEFAULT_BROAD_INDEX_ETFS: tuple[str, ...] = ("SPY", "VOO", "IVV", "SPYM", "RSP", "VTI", "ITOT", "SCHB", "IWV", "IWB", "VV", "SCHX", "OEF", "QQQ", "QQQM", "DIA", "MDY", "IJH", "IWR", "VO", "IWM", "IJR", "VB")` (Ruling 4).
  - `ScanConfig.broad_index_etfs: tuple[str, ...] = DEFAULT_BROAD_INDEX_ETFS`, validated so each entry matches `^[A-Z][A-Z.]{0,5}$` and entries are unique. An empty tuple turns the rule off.
  - `RankedOutcome.BROAD_INDEX_EXCLUDED = "broad_index_excluded"` and `BROAD_INDEX_REASON = "broad index ETF: excluded from native cards (scan.broad_index_etfs)"`.
  - `_news_blocks(ranked, *, use_llm, excluded_ranks: frozenset[int] = frozenset())`. An excluded rank gets None and is not in the fetch.
  - The exclusion mask holds for an unrestricted scan (`not symbols`, any budget, dry runs included) and a native candidate that is neither drift nor `_policy_locked(candidate, probe=True)`, matched on `normalize_symbol(candidate.contract)`. It is checked first in the send loop, before every budget refusal.

- [ ] **Step 1: Write the failing tests** (`tests/agent/test_broad_index_exclusion.py`, opting in with `app_config.scan.broad_index_etfs = ("SPY", "VOO")`; dynamic VOO uses the `tests/agent/test_dynamic_universe_scan.py` pattern).

```python
async def test_static_spy_and_dynamic_voo_ranked_first_are_excluded_and_the_next_native_is_sent(...):
    ...  # qualities SPY 0.95, VOO 0.9, AAA 0.8; FULL budget, max_cards_per_scan 1
    assert outcomes == {"SPY": "broad_index_excluded", "VOO": "broad_index_excluded", "AAA": "sent"}
    assert [c.args[0].contract for c in send_phase_llm_calls] == ["AAA"]           # no LLM on the excluded two
    assert "SPY" not in news_wanted and "VOO" not in news_wanted
    assert runners_up_reasons["SPY"] == BROAD_INDEX_REASON

async def test_a_restricted_scan_still_cards_a_listed_index(...): ...        # /scan SPY -> sent
async def test_a_policy_locked_alpha_or_probe_card_on_spy_is_unaffected(...): ...
async def test_an_empty_list_turns_the_rule_off(...): ...
```

  Add to `tests/config/test_scan_config.py`:
  - `ScanConfig(broad_index_etfs=("spy",))`, `("SP Y",)` and `("SPY", "SPY")` raise;
  - `ScanConfig().broad_index_etfs == DEFAULT_BROAD_INDEX_ETFS` and `len(DEFAULT_BROAD_INDEX_ETFS) == 23`;
  - `tuple(yaml.safe_load(config/config.yaml)["scan"]["broad_index_etfs"]) == DEFAULT_BROAD_INDEX_ETFS`.

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/agent/test_broad_index_exclusion.py tests/config/test_scan_config.py -q`. Expected: failures.

- [ ] **Step 3: Implement.** In `config/config.yaml`'s `scan:` block, list the 23 names grouped by the spec's segments, with a comment: card-selection rule, not an entry-risk rule; restricted scans and Re-evaluate still card them; SPYM replaced SPLG.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/agent tests/config -q`. Expected: all pass.

- [ ] **Step 5: Docs.** `docs/card-evidence.md`: add the `broad_index_excluded` outcome. It is a runner-up refused by a selection rule; such names are still collected, ranked and journaled.

- [ ] **Step 6: Verify and stage.** Run mypy and `pre-commit run --all-files`. Expected: clean. Suggested message: `Unrestricted scans never send a native card on a broad US equity index ETF (23 names, scan.broad_index_etfs); candidates still ranked and journaled`.

---

### Task 8: Journal consumers: the journal_only class, scan_supply and cards outcomes

Spec §5.6 "Consumers, checked" and §7.5's consumer items.

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py`, `agentic_trader/cli/commands/cards.py`, `tests/research/setups/test_outcomes.py`, `tests/research/setups/test_card_stats.py`, `docs/card-evidence.md`
- Create: `tests/research/setups/test_scan_supply.py`

**Interfaces:**
- Consumes: event payload fields from Tasks 4–7.
- Produces:
  - `summarize(frame)`: `counts`, `base_rates` and `market_exposure` each gain a `"journal_only"` entry. `runner_up` becomes `~sent & outcome != "journal_only"`. `broad_index_excluded` rows stay runner-ups. The empty-frame shape gains the same keys.
  - `scan_supply(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]`, keyed by trigger (missing → `"unknown"`). Each entry has:
    - `scans`;
    - `by_status` (a missing `status` counts as `completed`);
    - `zero_candidate`: completed scans with `candidates == []`;
    - `sent`: candidates with outcome `sent`;
    - `funnel`: the numeric fields summed, and the dict fields summed per key;
    - `funnel_unknown`: completed events whose `funnel` is missing or null.
  - `cards outcomes` adds `summary["supply"] = scan_supply(events)`. Its empty line becomes `f"No journaled candidates in {days} day(s) ({len(events)} scan events)."`.

- [ ] **Step 1: Write the failing tests.** These use `tests/research/setups/test_outcomes.py`'s `_event`, `_candidate` and `FakeBarSource`. `MIXED` is five `scan_candidates_ranked` events:
  1. an old event: no `status`, `trigger`, `funnel` or `calendar`; one candidate;
  2. a `suggestion_scan` completed with `candidates: []` and a funnel;
  3. a `suggestion_scan` with `status: macro_lockout`, `candidates: []` and `funnel: null`;
  4. a `suggestion_scan` completed with one `sent`, one `broad_index_excluded` and one `"per-scan budget spent"` candidate;
  5. a `swing_scan` completed with two `journal_only` candidates.

```python
def test_label_journaled_and_card_stats_see_the_same_rows_on_mixed_events():
    mixed, nonempty = MIXED, [e for e in MIXED if e["payload"]["candidates"]]
    a = label_journaled(mixed, FakeBarSource(...), **KW)
    b = label_journaled(nonempty, FakeBarSource(...), **KW)
    pd.testing.assert_frame_equal(a, b)
    assert (
        compute_card_stats(a, **STATS_KW, events_considered=4)["keys"]
        == compute_card_stats(b, **STATS_KW, events_considered=2)["keys"]
    )


def test_summarize_separates_journal_only_from_runners_up():
    s = summarize(
        frame_with(outcomes=["sent", "journal_only", "journal_only", "broad_index_excluded", "per-scan budget spent"])
    )
    assert (
        s["counts"]["sent"]["mature"],
        s["counts"]["journal_only"]["mature"],
        s["counts"]["runner_up"]["mature"],
    ) == (1, 2, 2)
    assert set(s["base_rates"]) == set(s["market_exposure"]) - {"reason"} == {"sent", "runner_up", "journal_only"}


def test_scan_supply_counts_zero_candidate_and_gated_scans_per_trigger():
    supply = scan_supply(MIXED)
    assert supply["suggestion_scan"]["by_status"] == {"completed": 2, "macro_lockout": 1}
    assert supply["suggestion_scan"]["zero_candidate"] == 1 and supply["unknown"]["funnel_unknown"] == 1
    assert supply["swing_scan"]["sent"] == 0


def test_an_event_without_status_counts_as_completed(): ...
```

  CLI: in `tests/cli/test_cli_smoke.py` (or the existing cards test, if there is one), add `test_cards_outcomes_prints_supply_and_the_empty_line` with a monkeypatched `scan_ranked_events` returning one zero-candidate event. The output contains `"supply"` and `"No journaled candidates in 30 day(s) (1 scan events)."`.

- [ ] **Step 2: Run them to verify they fail.** Run `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_scan_supply.py tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py -q`. Expected: failures.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/research tests/cli -q`. Expected: all pass.

- [ ] **Step 5: Docs.** In `docs/card-evidence.md`, document the `cards outcomes` `supply` block and the `journal_only` class in `counts`, `base_rates` and `market_exposure`.

- [ ] **Step 6: Verify and stage.** Run mypy and `pre-commit run --all-files`. Expected: clean. Suggested message: `cards outcomes: journal_only class beside sent and runner_up; scan_supply per trigger counts zero-candidate and gated scans with summed funnels`.

---

### Task 9: Cross-cutting docs, consistency sweep and full verification

Spec §8 (the remaining items), §9 steps 2 and 4 (what the PR records), and §10.

**Files:**
- Modify: `CLAUDE.md`, `docs/apriori-alphas.md` (Part 2, Enrolment/Measurement, :166-202), `docs/holistic-review-2026-10-21/README.md` (the Phase 0 row), `docs/alpha-roadmap.md` (the holistic-review queue, :27), plus any doc the sweep in Step 2 flags

**Interfaces:** Consumes the names from Tasks 1–8. Produces documentation only.

- [ ] **Step 1: Write the docs.**
  - `CLAUDE.md`:
    - **Contract 1:** `macro_lockout` reads the verified union calendar (static `config/calendar/us-tier1-vN.json`, digest-pinned, plus the live ForexFactory feed), with `risk.macro_calendar_unknown` (default `open`; `closed` refuses all entries while unverified).
    - **Contract 8:**
      - every eligible scan journals exactly one `scan_candidates_ranked` with `status`/`gate`/`calendar`/`funnel`, including zero-candidate, lockout-gated and regime-unavailable scans; halted, closed-session, idle, dry and restricted scans never do;
      - swing and intraday are `JOURNAL_ONLY`;
      - the broad-index rule;
      - "every journaled universe scan carries a top-level `regime` block" gains "except a lockout-gated one (`regime: null`)";
      - the `macro_calendar` readiness check and the renewal duty;
      - `UNKNOWN` at 0.5× and `RegimeUnavailable` → one `regime-unavailable/{et_date}` notice.
    - **Code map:** `config/calendar/` beside the calendar row.
  - `docs/apriori-alphas.md`, the operator's 2026-10-21 decision:
    - the PEAD probe is renewed for 180 days at unchanged risk;
    - it is judged on the counterfactual label over every eligible event (`probe_outcomes`), not on taps;
    - the renewal is a separate operator CLI action, `copilot alpha apriori-probe … --renew --days 180` (`--days` up to `MAX_PROBE_TERM_DAYS = 180`).
  - README Phase 0 row: status "Built on `feature/phase0-fail-safes`; merge # and deploy date recorded after merge".
  - Roadmap: Phase 0 status beside the queue item.

- [ ] **Step 2: Consistency sweep.** Run `grep -rn "is_in_lockout_window\|get_macro_report\|credit_oas_enabled\|inflation_breakeven_enabled\|Lockout verified\|aligned with 4h candle closes\|ScanBudget.FULL, \"trigger\": ScanTrigger.SWING_SCAN\|SPLG" agentic_trader tests config docs CLAUDE.md AGENTS.md`. Expected: hits only in historical documents (dated result docs, the holistic review and the spec). Fix any live doc that still describes the old behaviour.

- [ ] **Step 3: Full verification.**
  - Run `env -u VIRTUAL_ENV uv run pytest -q`. Expected: all pass, 0 failures.
  - Run `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest tests/integration --run-postgres -q`. Expected: all pass, including `test_postgres_scan_journal_event_and_notice_commit_together`.
  - Run `env -u VIRTUAL_ENV uv run mypy agentic_trader`. Expected: clean.
  - Run `env -u VIRTUAL_ENV uv run pre-commit run --all-files`. Expected: all hooks pass.
  - Run `git diff --stat main...HEAD -- alembic/`. Expected: empty (no migration).

- [ ] **Step 4: Stage and report.** Report the full-suite counts, the postgres result and the calendar digest from Task 1. Note that the LLM-prompt trace verification (Global Constraints, merge gate) and the §9 deploy steps are the controller's. Suggested message: `Phase 0 docs: CLAUDE.md contracts 1 and 8, PEAD renewal decision, review and roadmap status`.
