# Pooled events lane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the pooled lane score formulas that read four event-conditioned fields (`earnings_age`, `earnings_surprise`, `earnings_reaction`, `dividend_yield`) next to the daily bars, on the cached cohort-v2 label cube, via a frozen fixed-set protocol `events-v1` (checks A and C).

**Architecture:** `EventsCampaignProtocol` subclasses `CampaignProtocol` (`id: pooled-events-campaign`, `score_inputs: "daily"` included in the dump, `event_inputs: EventInputs`); the loader dispatches on `id`. `research/pooled/events.py` loads the Nasdaq calendar pages and the corporate-actions acquisition offline, verifies both digests against the protocol, and builds one dimensionless event frame per symbol on the full trading-day calendar (state known at each day's close). The DSL learns the four names as dimensionless fields. `ScoreBook.with_events(...)` left-joins the event columns into `prepare_daily`'s frames so `evaluate_prepared` reads them at D-1 unchanged. `CubeBuild.events` is populated when the build is given `EventSources`; `score_book` dispatches on the subclass; `event_inputs` joins `score_inputs`/`score_read` in the frozen document and every manifest. The frozen protocol copies `intraday-v1`'s cube values so the cached cube is a hit.

**Tech Stack:** Python 3.14, `uv`, pandas, numpy, pydantic v2 (frozen), click, pytest (network blocked).

**Spec:** `docs/superpowers/specs/2026-10-14-pooled-events-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-events`; never touch `/Users/adamhadani/Development/agentic-trader` or `/Users/adamhadani/Development/agentic-trader-intraday` (a research run is using it).
- ruff strict (C408, PLC0415, RUF059, A002, UP038, RUF046, TRY004; sorted `__all__`); mypy clean on touched modules; pristine tests. `env -u VIRTUAL_ENV uv run pre-commit run --all-files` before each commit (ruff-format reflows fenced Markdown code; re-add).
- **v1–v4 and intraday-v1 identities unchanged:** `CampaignProtocol`'s fields and `model_dump` are untouched; `tests/research/pooled/test_campaign_protocol.py` already pins v4 (`a72f2e65`); add the same pin for `config/research/pooled/intraday-v1.json` (`5b4c3fdf`). The cube spec identity for the events protocol must equal `f1c835a3d3364d2b…`.
- Research only: no registry, broker, Telegram; no change to labels, the cube, bracket, cohort, windows, gates, bootstrap or stage logic; no runtime change; no network in the event loaders (`acquire_calendar` is never called — pages are read from disk only).
- No lookahead: an event field's value at trading day T uses nothing stamped after T's regular close; a report dated D is first visible at row D+1 (its reaction needs close(D+1)); daily reads stay at D-1.
- Exact values from the spec: `min_estimates = 1`, `min_abs_forecast = 0.05`, `vol_window = 20`, benchmark `SPY`, `trailing_sessions = 252`, calendar window `2016-02-23 → 2026-07-31`, corporate-actions window `2016-01-01 → 2027-03-31`, `pages_sha256 = 9273e40a7aaed0d925003502a621ecfedf40d0aa889aeaaca992d84f187aa3da`, actions `content_sha256 = 776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344`.
- Implementers STAGE and report; never commit; never dispatch subagents.

## Review Focus

1. A report on D with a missing close at D+1 (halt, delisting gap) → `earnings_reaction` NaN but `earnings_age`/`earnings_surprise` still advance from D+1; never an exception (Task 2 `test_reaction_is_nan_without_reaction_bars_but_age_advances`).
2. Two reports whose visibility windows overlap at the boundary (next report's D'+1 equals a day inside the previous segment) → the later report wins from D'+1 exactly (Task 2 `test_next_report_replaces_the_previous_from_its_visibility_day`).
3. A `cash_dividend` row with `rate: null`, or `special: true`, or an ex-date before the window → ignored and counted, never a NaN poisoning the trailing sum (Task 2 `test_dividend_yield_ignores_special_and_unpriced_rows`).
4. Protocol digests that do not match the files on disk → `ValueError` before any bars or panels are touched (Task 3 `test_events_build_fails_closed_on_digest_mismatch`).
5. A daily or intraday protocol run with `--earnings-pages`/`--corporate-actions`, or an events protocol run without them → a `ClickException` naming the option, never a silent daily run (Task 3 `test_pooled_cli_refuses_event_options_for_non_event_protocols`).

---

### Task 1: `EventsCampaignProtocol`, `event_inputs`, loader dispatch, identity plumbing

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py` (after `ScoreRead` ~line 226; after `IntradayCampaignProtocol` ~line 392; `_PROTOCOL_CLASSES` ~line 417; `__all__`)
- Modify: `agentic_trader/research/pooled/scoring.py` (`score_read_document` ~line 236; `__all__`)
- Modify: `agentic_trader/research/pooled/campaign_run.py` (`frozen_document` line 228; manifest line 363; result line 577)
- Modify: `agentic_trader/research/pooled/power.py` (manifest line 329), `agentic_trader/research/pooled/null_check.py` (manifest line 258)
- Modify: `agentic_trader/research/apriori/pead_events.py` (rename `_reaction_z` → `reaction_z`; update its callers in that module and any test)
- Test: `tests/research/pooled/test_campaign_protocol.py` (append), `tests/research/pooled/test_campaign_run.py` (append one test)

**Interfaces:**
- Produces:
  ```python
  class EarningsInputs(BaseModel, frozen=True, extra="forbid"):
      source: Literal["nasdaq-calendar"]
      pages_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
      calendar_from: date
      calendar_through: date
      min_estimates: int = Field(ge=1)
      min_abs_forecast: float = Field(gt=0)
      vol_window: int = Field(ge=2)
      benchmark: str = Field(min_length=1)


  class CorporateActionInputs(BaseModel, frozen=True, extra="forbid"):
      source: Literal["alpaca"]
      content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
      window: tuple[date, date]
      trailing_sessions: int = Field(ge=1)


  class EventInputs(BaseModel, frozen=True, extra="forbid"):
      fields_version: Literal["events_fields_v1"]
      earnings: EarningsInputs
      corporate_actions: CorporateActionInputs


  class EventsCampaignProtocol(CampaignProtocol, frozen=True, extra="forbid"):
      id: Literal["pooled-events-campaign"]
      score_inputs: Literal["daily"] = Field(default="daily", exclude=False)
      event_inputs: EventInputs
      # validator: search_mode must be "fixed_set"
  ```
  `load_campaign_protocol` returns `EventsCampaignProtocol` for id `pooled-events-campaign`.
  `scoring.event_inputs_document(protocol) -> dict | None` (the `event_inputs` dump for an events protocol, else `None`).
  `frozen_document` adds `"event_inputs"` to the body when non-None (alongside the existing `score_inputs`/`score_read` branch, which is unchanged for intraday). Every manifest (`power_a`, `null_check`, `campaign`) and the campaign `result` carry `"event_inputs": event_inputs_document(protocol)`.
  `pead_events.reaction_z(sym, bench, days, i, vol_window) -> float | None` (public name; same body).

- [ ] **Step 1: Write the failing tests** (append to `tests/research/pooled/test_campaign_protocol.py`; the module already has `V4_PATH`/`V4_SHA256` and loads v4 — reuse them):

```python
INTRADAY_PATH = Path("config/research/pooled/intraday-v1.json")
INTRADAY_SHA256 = "5b4c3fdf"  # docs/alpha-pooled-mining.md, intraday score inputs


def _events_document() -> dict:
    document = json.loads(INTRADAY_PATH.read_text())
    document.pop("score_read")
    document.update(
        id="pooled-events-campaign",
        score_inputs="daily",
        event_inputs={
            "fields_version": "events_fields_v1",
            "earnings": {
                "source": "nasdaq-calendar",
                "pages_sha256": "a" * 64,
                "calendar_from": "2016-02-23",
                "calendar_through": "2026-07-31",
                "min_estimates": 1,
                "min_abs_forecast": 0.05,
                "vol_window": 20,
                "benchmark": "SPY",
            },
            "corporate_actions": {
                "source": "alpaca",
                "content_sha256": "b" * 64,
                "window": ["2016-01-01", "2027-03-31"],
                "trailing_sessions": 252,
            },
        },
    )
    return document


def test_intraday_v1_identity_is_unchanged_by_the_events_subclass():
    loaded = load_campaign_protocol(INTRADAY_PATH)
    assert loaded.sha256.startswith(INTRADAY_SHA256)
    assert "event_inputs" not in loaded.protocol.model_dump(mode="json")
    assert "event_inputs" not in CampaignProtocol(**json.loads(V4_PATH.read_text())).model_dump(mode="json")


def test_events_protocol_loads_by_id_and_dumps_its_inputs(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(_events_document()))
    loaded = load_campaign_protocol(path)
    protocol = loaded.protocol
    assert isinstance(protocol, EventsCampaignProtocol)
    assert protocol.score_inputs == "daily" and protocol.search_mode == "fixed_set"
    dumped = protocol.model_dump(mode="json")
    assert dumped["score_inputs"] == "daily"
    assert dumped["event_inputs"]["earnings"]["vol_window"] == 20
    assert dumped["event_inputs"]["corporate_actions"]["trailing_sessions"] == 252
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")


def test_events_protocol_refuses_genetic_search_and_unknown_fields_version():
    genetic = _events_document()
    genetic.update(search_mode="genetic", null_check=None)
    with pytest.raises(ValidationError, match="fixed_set"):
        EventsCampaignProtocol.model_validate(genetic)
    other = _events_document()
    other["event_inputs"]["fields_version"] = "events_fields_v2"
    with pytest.raises(ValidationError):
        EventsCampaignProtocol.model_validate(other)


def test_event_inputs_document_is_none_for_daily_and_intraday_protocols():
    from agentic_trader.research.pooled.scoring import event_inputs_document

    assert event_inputs_document(load_campaign_protocol(V4_PATH).protocol) is None
    assert event_inputs_document(load_campaign_protocol(INTRADAY_PATH).protocol) is None
    events = EventsCampaignProtocol.model_validate(_events_document())
    assert event_inputs_document(events) == events.event_inputs.model_dump(mode="json")
```

Append to `tests/research/pooled/test_campaign_run.py` (it already imports `frozen_document` or the module; add the import if not):

```python
def test_frozen_document_binds_event_inputs(tmp_path):
    from tests.research.pooled.test_campaign_protocol import _events_document

    events = EventsCampaignProtocol.model_validate(_events_document())
    daily = CampaignProtocol(**json.loads(Path("config/research/pooled/campaign-v4.json").read_text()))
    with_events = frozen_document("close / ts_mean(close, 20)", events)
    without = frozen_document("close / ts_mean(close, 20)", daily)
    assert with_events["formula"]["event_inputs"] == events.event_inputs.model_dump(mode="json")
    assert with_events["formula"]["score_inputs"] == "daily"
    assert "event_inputs" not in without["formula"] and "score_inputs" not in without["formula"]
    assert with_events["identity"] != without["identity"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_campaign_run.py -q -k "events or event_inputs or intraday_v1_identity"`
Expected: FAIL with `ImportError`/`cannot import name 'EventsCampaignProtocol'`.

- [ ] **Step 3: Implement the models and dispatch** in `campaign.py` (after `IntradayCampaignProtocol`):

```python
class EarningsInputs(BaseModel, frozen=True, extra="forbid"):
    """The Nasdaq calendar pages an events protocol reads, pinned by a digest over every weekday page."""

    source: Literal["nasdaq-calendar"]
    pages_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calendar_from: date
    calendar_through: date
    min_estimates: int = Field(ge=1)
    min_abs_forecast: float = Field(gt=0)
    vol_window: int = Field(ge=2)
    benchmark: str = Field(min_length=1)


class CorporateActionInputs(BaseModel, frozen=True, extra="forbid"):
    """The corporate-actions acquisition (``actions.json``) an events protocol reads, pinned by its content digest."""

    source: Literal["alpaca"]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    window: tuple[date, date]
    trailing_sessions: int = Field(ge=1)


class EventInputs(BaseModel, frozen=True, extra="forbid"):
    fields_version: Literal["events_fields_v1"]
    earnings: EarningsInputs
    corporate_actions: CorporateActionInputs


class EventsCampaignProtocol(CampaignProtocol, frozen=True, extra="forbid"):
    """A pooled protocol whose formulas read daily bars at D-1 plus the event fields of
    ``events_fields_v1`` (spec 2026-10-14, events): everything else is inherited."""

    id: Literal["pooled-events-campaign"]  # type: ignore[assignment]
    score_inputs: Literal["daily"] = Field(default="daily", exclude=False)
    event_inputs: EventInputs

    @model_validator(mode="after")
    def _events(self) -> EventsCampaignProtocol:
        if self.search_mode != "fixed_set":
            raise ValueError(
                "an events protocol is fixed_set in v1: a genetic search on event fields needs check B and its own spec"
            )
        return self
```

Add `"pooled-events-campaign": EventsCampaignProtocol` to `_PROTOCOL_CLASSES`; export the four new names in `__all__` (sorted).

In `scoring.py` add (and export):

```python
def event_inputs_document(protocol: CampaignProtocol) -> dict | None:
    """The protocol's event inputs as recorded in manifests; None for a protocol without event fields."""
    return protocol.event_inputs.model_dump(mode="json") if isinstance(protocol, EventsCampaignProtocol) else None
```

In `campaign_run.frozen_document`, after the existing `read` branch:

```python
    events = event_inputs_document(protocol)
    if events is not None:  # an event-conditioned score is a different formula from the same text on bars alone
        body = {**body, "score_inputs": protocol.score_inputs, "event_inputs": events}
```

Add `"event_inputs": event_inputs_document(protocol),` beside every `"score_read": score_read_document(protocol),` (campaign manifest, campaign result, power manifest, null-check manifest). In `pead_events.py` rename `_reaction_z` to `reaction_z` and update the two call sites in `build_events` (grep the module and `tests/research/apriori` for `_reaction_z`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_campaign_run.py tests/research/apriori -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/pooled agentic_trader/research/apriori/pead_events.py`
Then `git add` the touched files and report.

---

### Task 2: `research/pooled/events.py` — offline loaders, digests, event fields; DSL field names

**Files:**
- Create: `agentic_trader/research/pooled/events.py`
- Modify: `agentic_trader/research/alpha/dsl.py` (`FIELD_UNITS` line 28)
- Test: `tests/research/pooled/test_events.py` (new), `tests/research/alpha/test_dsl.py` (append one test; create the file next to the existing DSL tests if the name differs — find them with `grep -rl "FIELD_UNITS\|prepare_data_fields" tests`)

**Interfaces:**
- Consumes: `CalendarPageStore`, `parse_calendar_payload`, `CalendarRow`, `dedupe_rows` (`research/apriori/earnings_history.py`, `agent/earnings.py`), `reaction_z`, `by_session` (`research/apriori/pead_events.py`), `CorporateAction` (`data/corporate_actions.py`), `encode` (`storage/workflow.py`), `EventInputs` (Task 1).
- Produces:
  ```python
  EVENT_FIELDS = ("earnings_age", "earnings_surprise", "earnings_reaction", "dividend_yield")

  @dataclass(frozen=True)
  class LoadedCalendar:
      rows_by_symbol: Mapping[str, tuple[CalendarRow, ...]]  # deduped by (symbol, date), sorted by date
      pages_sha256: str
      pages: int
      duplicates: int

  def pages_sha256(directory: Path, calendar_from: date, calendar_through: date) -> str
  def load_calendar_pages(directory: Path, calendar_from: date, calendar_through: date) -> LoadedCalendar
      # weekdays only; CalendarPageStore(directory).load(day) per day; a None (missing page) -> ValueError naming the date

  def load_actions(path: Path) -> tuple[tuple[CorporateAction, ...], str]
      # json list -> CorporateAction rows; returns (rows, sha256(encode(raw_rows))) -- the acquisition's content_sha256

  def verify_event_sources(inputs: EventInputs, calendar: LoadedCalendar, actions_sha256: str) -> None
      # ValueError "earnings pages digest ... does not match the protocol" / "corporate-actions digest ..."

  @dataclass(frozen=True)
  class EventCoverage:
      symbols_without_reports: tuple[str, ...]
      symbols_without_actions: tuple[str, ...]
      non_session_reports: int
      unpriced_dividends: int
      first_finite: Mapping[str, dict[str, str | None]]  # symbol -> field -> first trading day iso with a finite value

  @dataclass(frozen=True)
  class EventFrames:
      frames: Mapping[str, pd.DataFrame]  # symbol -> DataFrame indexed by DatetimeIndex(trading_days, UTC midnight), columns EVENT_FIELDS, float64
      coverage: EventCoverage

  def build_event_frames(
      inputs: EventInputs,
      calendar: LoadedCalendar,
      actions: Sequence[CorporateAction],
      adjusted: Mapping[str, pd.DataFrame],   # symbol -> adjusted daily bars (UTC index, Close column) incl. the benchmark
      trading_days: Sequence[date],
      symbols: Sequence[str],
  ) -> EventFrames
  ```
  The frame index must equal `prepare_daily(adjusted)[symbol].index`'s dtype/tz convention so a left join aligns: use `pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days])` **matching `by_session`'s keyed index** (read `by_session` in `pead_events.py`: it sets the axis to `.date` values and `prepare_daily` wraps that in `pd.DatetimeIndex`, i.e. naive midnight stamps). Build the event index the same way: `pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days])` (naive).

- [ ] **Step 1: Write the failing tests** in `tests/research/pooled/test_events.py`:

```python
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.pooled.campaign import EventInputs
from agentic_trader.research.pooled.events import (
    EVENT_FIELDS,
    LoadedCalendar,
    build_event_frames,
    load_actions,
    load_calendar_pages,
    pages_sha256,
    verify_event_sources,
)
from tests.research.pooled.test_formula import daily, weekdays


DAYS = weekdays(date(2021, 1, 4), 300)  # 300 trading days
INPUTS = EventInputs.model_validate(
    {
        "fields_version": "events_fields_v1",
        "earnings": {
            "source": "nasdaq-calendar",
            "pages_sha256": "a" * 64,
            "calendar_from": "2021-01-04",
            "calendar_through": "2022-03-31",
            "min_estimates": 1,
            "min_abs_forecast": 0.05,
            "vol_window": 20,
            "benchmark": "SPY",
        },
        "corporate_actions": {
            "source": "alpaca",
            "content_sha256": "b" * 64,
            "window": ["2021-01-01", "2022-12-31"],
            "trailing_sessions": 252,
        },
    }
)


def _row(
    symbol: str, day: date, eps: float | None = 1.1, forecast: float | None = 1.0, n: int | None = 3
) -> CalendarRow:
    return CalendarRow(
        symbol=symbol,
        date=day,
        timing=EarningsTiming.AFTER_HOURS,
        eps=eps,
        eps_forecast=forecast,
        surprise_pct_reported=None,
        n_estimates=n,
        fiscal_quarter=None,
    )


def _calendar(rows_by_symbol: dict) -> LoadedCalendar:
    return LoadedCalendar(
        rows_by_symbol={k: tuple(v) for k, v in rows_by_symbol.items()}, pages_sha256="a" * 64, pages=1, duplicates=0
    )


def _dividend(
    symbol: str, ex: date, rate: float | None = 0.5, special: bool = False, kind: str = "cash_dividend"
) -> CorporateAction:
    return CorporateAction(
        id=f"{symbol}-{ex.isoformat()}",
        kind=kind,
        symbol=symbol,
        effective_on=ex,
        process_date=ex,
        rate=rate,
        special=special,
    )


def _world(prices_aaa: list[float] | None = None):
    closes = prices_aaa or list(np.linspace(50, 80, len(DAYS)))
    return {
        "AAA": daily(DAYS, closes),
        "SPY": daily(DAYS, list(np.linspace(300, 330, len(DAYS)))),
    }


def test_report_is_visible_from_the_day_after_with_age_zero_and_carried_forward():
    d = DAYS[100]
    frames = build_event_frames(INPUTS, _calendar({"AAA": [_row("AAA", d)]}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    assert list(frames.columns) == list(EVENT_FIELDS)
    assert np.isnan(frames["earnings_age"].iloc[99]) and np.isnan(frames["earnings_age"].iloc[100])
    assert frames["earnings_age"].iloc[101] == 0.0 and frames["earnings_age"].iloc[150] == 49.0
    assert frames["earnings_surprise"].iloc[101] == pytest.approx(10.0)  # 100 * (1.1 - 1.0) / 1.0
    assert frames["earnings_surprise"].iloc[299] == pytest.approx(10.0)
    assert np.isfinite(frames["earnings_reaction"].iloc[101])
    assert frames["earnings_reaction"].iloc[101] == frames["earnings_reaction"].iloc[299]


def test_pre_market_and_after_hours_reports_on_the_same_day_give_identical_fields():
    d = DAYS[100]
    pre = _row("AAA", d)
    pre = CalendarRow(**{**pre.__dict__, "timing": EarningsTiming.PRE_MARKET})
    a = build_event_frames(INPUTS, _calendar({"AAA": [pre]}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    b = build_event_frames(INPUTS, _calendar({"AAA": [_row("AAA", d)]}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    pd.testing.assert_frame_equal(a, b)


def test_next_report_replaces_the_previous_from_its_visibility_day():
    rows = [_row("AAA", DAYS[100], eps=1.1), _row("AAA", DAYS[160], eps=0.9)]
    f = build_event_frames(INPUTS, _calendar({"AAA": rows}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    assert f["earnings_age"].iloc[160] == 59.0 and f["earnings_surprise"].iloc[160] == pytest.approx(10.0)
    assert f["earnings_age"].iloc[161] == 0.0 and f["earnings_surprise"].iloc[161] == pytest.approx(-10.0)


def test_surprise_is_nan_without_estimates_or_with_a_tiny_forecast_but_age_advances():
    rows = [_row("AAA", DAYS[100], n=0), _row("AAA", DAYS[160], forecast=0.01)]
    f = build_event_frames(INPUTS, _calendar({"AAA": rows}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    assert np.isnan(f["earnings_surprise"].iloc[120]) and f["earnings_age"].iloc[120] == 19.0
    assert np.isnan(f["earnings_surprise"].iloc[170]) and f["earnings_age"].iloc[170] == 9.0


def test_reaction_is_nan_without_reaction_bars_but_age_advances():
    closes = list(np.linspace(50, 80, len(DAYS)))
    closes[101] = float("nan")  # no close at D+1
    f = build_event_frames(
        INPUTS, _calendar({"AAA": [_row("AAA", DAYS[100])]}), [], _world(closes), DAYS, ["AAA"]
    ).frames["AAA"]
    assert np.isnan(f["earnings_reaction"].iloc[101]) and f["earnings_age"].iloc[101] == 0.0


def test_reaction_matches_the_pead_definition():
    from agentic_trader.research.apriori.pead_events import by_session, reaction_z

    world = _world()
    f = build_event_frames(INPUTS, _calendar({"AAA": [_row("AAA", DAYS[100])]}), [], world, DAYS, ["AAA"]).frames["AAA"]
    expected = reaction_z(by_session(world["AAA"]), by_session(world["SPY"]), DAYS, 100, 20)
    assert f["earnings_reaction"].iloc[101] == pytest.approx(expected)


def test_non_session_reports_are_skipped_and_counted():
    saturday = DAYS[100] + timedelta(days=(5 - DAYS[100].weekday()) % 7 or 7)
    assert saturday.weekday() == 5
    built = build_event_frames(INPUTS, _calendar({"AAA": [_row("AAA", saturday)]}), [], _world(), DAYS, ["AAA"])
    assert built.coverage.non_session_reports == 1
    assert np.isnan(built.frames["AAA"]["earnings_age"]).all()


def test_dividend_yield_trailing_window_and_coverage_rules():
    actions = [_dividend("AAA", DAYS[260]), _dividend("AAA", DAYS[10])]
    f = build_event_frames(INPUTS, _calendar({}), actions, _world(), DAYS, ["AAA"]).frames["AAA"]
    assert np.isnan(f["dividend_yield"].iloc[250])  # first 251 days of the window are NaN
    close = _world()["AAA"]["Close"].to_numpy(float)
    assert f["dividend_yield"].iloc[251] == pytest.approx(0.5 / close[251])  # DAYS[10] is inside [0, 251]
    assert f["dividend_yield"].iloc[262] == pytest.approx(0.5 / close[262])  # DAYS[10] fell out, DAYS[260] is in
    assert f["dividend_yield"].iloc[259] == pytest.approx(0.5 / close[259])  # DAYS[10] still in (259 - 251 = 8 <= 10)


def test_dividend_yield_ignores_special_and_unpriced_rows():
    actions = [
        _dividend("AAA", DAYS[260], special=True),
        _dividend("AAA", DAYS[261], rate=None),
        _dividend("AAA", DAYS[262], kind="forward_split", rate=2.0),
        _dividend("AAA", date(2020, 6, 1)),  # before the first trading day: ignored
    ]
    built = build_event_frames(INPUTS, _calendar({}), actions, _world(), DAYS, ["AAA"])
    assert built.frames["AAA"]["dividend_yield"].iloc[270] == 0.0
    assert built.coverage.unpriced_dividends == 1


def test_dividend_on_a_non_session_counts_from_the_next_session():
    ex = DAYS[260] + timedelta(days=(5 - DAYS[260].weekday()) % 7 or 7)  # a Saturday
    f = build_event_frames(INPUTS, _calendar({}), [_dividend("AAA", ex)], _world(), DAYS, ["AAA"]).frames["AAA"]
    k = next(i for i, d in enumerate(DAYS) if d >= ex)
    assert f["dividend_yield"].iloc[k - 1] == 0.0 and f["dividend_yield"].iloc[k] > 0.0


def test_symbol_without_reports_or_actions_is_all_nan_or_zero_and_reported():
    built = build_event_frames(INPUTS, _calendar({}), [], _world(), DAYS, ["AAA"])
    assert built.coverage.symbols_without_reports == ("AAA",) and built.coverage.symbols_without_actions == ("AAA",)
    f = built.frames["AAA"]
    assert np.isnan(f["earnings_age"]).all() and f["dividend_yield"].iloc[-1] == 0.0
    assert built.coverage.first_finite["AAA"]["earnings_age"] is None
    assert built.coverage.first_finite["AAA"]["dividend_yield"] == DAYS[251].isoformat()


def test_frames_align_with_prepare_daily_index():
    from agentic_trader.research.pooled.formula import prepare_daily

    f = build_event_frames(INPUTS, _calendar({}), [], _world(), DAYS, ["AAA"]).frames["AAA"]
    keyed = prepare_daily(_world())["AAA"]
    assert f.index.equals(keyed.index)


def _write_pages(directory: Path, days: list[date], symbol_rows: dict[date, list[dict]]) -> None:
    from agentic_trader.research.apriori.earnings_history import CalendarPageStore
    from datetime import UTC, datetime

    store = CalendarPageStore(directory)
    for day in days:
        body = json.dumps({"data": {"rows": symbol_rows.get(day, [])}}).encode()
        store.save(day, body, datetime.now(UTC))


def test_load_calendar_pages_is_offline_dedupes_and_digests(tmp_path):
    days = weekdays(date(2021, 1, 4), 5)
    row = {"symbol": "aaa", "time": "time-after-hours", "eps": "1.1", "epsForecast": "1.0", "noOfEsts": "3"}
    _write_pages(tmp_path, list(days), {days[1]: [row, row]})
    loaded = load_calendar_pages(tmp_path, days[0], days[-1])
    assert loaded.pages == 5 and loaded.duplicates == 1
    assert [r.date for r in loaded.rows_by_symbol["AAA"]] == [days[1]]
    assert loaded.pages_sha256 == pages_sha256(tmp_path, days[0], days[-1])
    metas = sorted(
        f"{d.isoformat()}:{json.loads((tmp_path / f'{d.isoformat()}.meta.json').read_text())['sha256']}" for d in days
    )
    assert loaded.pages_sha256 == hashlib.sha256("\n".join(metas).encode()).hexdigest()


def test_load_calendar_pages_fails_closed_on_a_missing_page(tmp_path):
    days = weekdays(date(2021, 1, 4), 5)
    _write_pages(tmp_path, list(days[:-1]), {})
    with pytest.raises(ValueError, match=days[-1].isoformat()):
        load_calendar_pages(tmp_path, days[0], days[-1])


def test_load_actions_returns_rows_and_the_acquisitions_content_digest(tmp_path):
    from agentic_trader.storage.workflow import encode

    rows = [_dividend("AAA", DAYS[10]).model_dump(mode="json")]
    path = tmp_path / "actions.json"
    path.write_text(json.dumps(rows))
    actions, digest = load_actions(path)
    assert actions[0].symbol == "AAA" and digest == hashlib.sha256(encode(rows).encode()).hexdigest()


def test_verify_event_sources_names_the_mismatching_digest():
    calendar = _calendar({})
    verify_event_sources(INPUTS, calendar, "b" * 64)
    with pytest.raises(ValueError, match="corporate-actions"):
        verify_event_sources(INPUTS, calendar, "c" * 64)
    with pytest.raises(ValueError, match="earnings pages"):
        verify_event_sources(INPUTS, LoadedCalendar({}, "d" * 64, 1, 0), "b" * 64)
```

DSL test (append to the existing DSL test module):

```python
def test_event_fields_are_dimensionless_and_only_present_when_supplied():
    from agentic_trader.research.alpha.dsl import FIELD_UNITS, AlphaExpressionEvaluator, compile_expression

    for name in ("earnings_age", "earnings_surprise", "earnings_reaction", "dividend_yield"):
        assert FIELD_UNITS[name] == (0.0, 0.0)
    assert tuple(float(u) for u in compile_expression("earnings_surprise / (1 + earnings_age)").units) == (0.0, 0.0)
    index = pd.date_range("2021-01-04", periods=5, freq="B")
    frame = pd.DataFrame({"Close": np.linspace(10, 11, 5)}, index=index)
    assert "earnings_age" not in AlphaExpressionEvaluator.prepare_data_fields(frame)
    with pytest.raises(ValueError, match="Missing required fields"):
        AlphaExpressionEvaluator().evaluate("earnings_age", frame)
    frame["earnings_age"] = np.arange(5, dtype=float)
    assert AlphaExpressionEvaluator().evaluate("earnings_age + 1", frame).iloc[-1] == 5.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError: agentic_trader.research.pooled.events`.

- [ ] **Step 3: Implement** `agentic_trader/research/pooled/events.py`:

```python
# agentic_trader/research/pooled/events.py
"""Event-conditioned score fields for the pooled lane (``events_fields_v1``), built offline from
the Nasdaq calendar page cache and the corporate-actions acquisition.

Every field is per symbol, indexed by NYSE trading day T and holds the state known at T's
regular close; ``evaluate_prepared`` then reads it at D-1 like any daily field. A report dated
on session D is complete when D+1 closes (its reaction is close(D-1) -> close(D+1), the PEAD
convention), so it is first visible at row D+1 with ``earnings_age`` 0.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow, parse_calendar_payload
from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.apriori.earnings_history import CalendarPageStore, dedupe_rows, weekdays
from agentic_trader.research.apriori.pead_events import by_session, reaction_z
from agentic_trader.research.pooled.campaign import EventInputs
from agentic_trader.storage.workflow import encode


__all__ = [
    "EVENT_FIELDS",
    "EventCoverage",
    "EventFrames",
    "LoadedCalendar",
    "build_event_frames",
    "load_actions",
    "load_calendar_pages",
    "pages_sha256",
    "verify_event_sources",
]

EVENT_FIELDS = ("earnings_age", "earnings_surprise", "earnings_reaction", "dividend_yield")
_SURPRISE_DECIMALS = 9


@dataclass(frozen=True)
class LoadedCalendar:
    rows_by_symbol: Mapping[str, tuple[CalendarRow, ...]]
    pages_sha256: str
    pages: int
    duplicates: int


@dataclass(frozen=True)
class EventCoverage:
    symbols_without_reports: tuple[str, ...]
    symbols_without_actions: tuple[str, ...]
    non_session_reports: int
    unpriced_dividends: int
    first_finite: Mapping[str, dict[str, str | None]]


@dataclass(frozen=True)
class EventFrames:
    frames: Mapping[str, pd.DataFrame]
    coverage: EventCoverage


def _page_lines(directory: Path, calendar_from: date, calendar_through: date) -> list[str]:
    lines = []
    for day in weekdays(calendar_from, calendar_through):
        meta = directory / f"{day.isoformat()}.meta.json"
        if not meta.exists():
            raise ValueError(f"earnings calendar page for {day.isoformat()} is missing from {directory}")
        lines.append(f"{day.isoformat()}:{json.loads(meta.read_text())['sha256']}")
    return sorted(lines)


def pages_sha256(directory: Path, calendar_from: date, calendar_through: date) -> str:
    """SHA-256 of the sorted ``<date>:<page sha256>`` lines of every weekday page in the window."""
    return hashlib.sha256("\n".join(_page_lines(directory, calendar_from, calendar_through)).encode()).hexdigest()


def load_calendar_pages(directory: Path, calendar_from: date, calendar_through: date) -> LoadedCalendar:
    """Every weekday page in the window, read from disk only (never fetched); a missing page fails closed."""
    digest = pages_sha256(directory, calendar_from, calendar_through)
    store = CalendarPageStore(directory)
    rows: list[CalendarRow] = []
    days = weekdays(calendar_from, calendar_through)
    for day in days:
        body = store.load(day)
        if body is None:
            raise ValueError(f"earnings calendar page for {day.isoformat()} is missing from {directory}")
        rows.extend(parse_calendar_payload(json.loads(body), day))
    kept, duplicates = dedupe_rows(rows)
    by_symbol: dict[str, list[CalendarRow]] = defaultdict(list)
    for row in kept:
        by_symbol[row.symbol].append(row)
    return LoadedCalendar(
        rows_by_symbol={s: tuple(sorted(v, key=lambda r: r.date)) for s, v in by_symbol.items()},
        pages_sha256=digest,
        pages=len(days),
        duplicates=duplicates,
    )


def load_actions(path: Path) -> tuple[tuple[CorporateAction, ...], str]:
    """The acquisition's ``actions.json`` rows and their content digest (the manifest's ``content_sha256``)."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path}: actions.json is a JSON list of corporate actions")  # noqa: TRY004
    digest = hashlib.sha256(encode(raw).encode()).hexdigest()
    return tuple(CorporateAction.model_validate(row) for row in raw), digest


def verify_event_sources(inputs: EventInputs, calendar: LoadedCalendar, actions_sha256: str) -> None:
    if calendar.pages_sha256 != inputs.earnings.pages_sha256:
        raise ValueError("earnings pages digest does not match the protocol's event_inputs.earnings.pages_sha256")
    if actions_sha256 != inputs.corporate_actions.content_sha256:
        raise ValueError(
            "corporate-actions digest does not match the protocol's event_inputs.corporate_actions.content_sha256"
        )


def _surprise(row: CalendarRow, inputs: EventInputs) -> float:
    earnings = inputs.earnings
    if row.eps is None or row.eps_forecast is None or row.n_estimates is None:
        return math.nan
    if row.n_estimates < earnings.min_estimates or abs(row.eps_forecast) < earnings.min_abs_forecast:
        return math.nan
    return round(100.0 * (row.eps - row.eps_forecast) / abs(row.eps_forecast), _SURPRISE_DECIMALS)


def _earnings_columns(
    rows: Sequence[CalendarRow],
    keyed: pd.DataFrame | None,
    bench: pd.DataFrame,
    days: Sequence[date],
    position: Mapping[date, int],
    inputs: EventInputs,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    n = len(days)
    age = np.full(n, np.nan)
    surprise = np.full(n, np.nan)
    reaction = np.full(n, np.nan)
    skipped = 0
    visible: list[tuple[int, float, float]] = []
    for row in rows:
        i = position.get(row.date)
        if i is None:
            skipped += 1
            continue
        if i + 1 >= n:
            continue  # completes after the calendar ends: never visible here
        z = reaction_z(keyed, bench, days, i, inputs.earnings.vol_window) if keyed is not None else None
        visible.append((i + 1, _surprise(row, inputs), math.nan if z is None else z))
    for k, (start, s, z) in enumerate(visible):
        stop = visible[k + 1][0] if k + 1 < len(visible) else n
        if stop <= start:
            continue
        age[start:stop] = np.arange(stop - start, dtype=float)
        surprise[start:stop] = s
        reaction[start:stop] = z
    return age, surprise, reaction, skipped


def _dividend_yield(
    actions: Sequence[CorporateAction],
    close: np.ndarray,
    days: Sequence[date],
    inputs: EventInputs,
) -> tuple[np.ndarray, int]:
    n = len(days)
    paid = np.zeros(n)
    unpriced = 0
    for action in actions:
        if action.kind != "cash_dividend" or action.special:
            continue
        if action.rate is None:
            unpriced += 1
            continue
        k = bisect_left(days, action.effective_on)
        if k < n:
            paid[k] += action.rate
    window = inputs.corporate_actions.trailing_sessions
    trailing = pd.Series(paid).rolling(window, min_periods=window).sum().to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = trailing / close
    out[~np.isfinite(out)] = np.nan
    return out, unpriced


def build_event_frames(
    inputs: EventInputs,
    calendar: LoadedCalendar,
    actions: Sequence[CorporateAction],
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> EventFrames:
    """One frame per symbol on the full trading-day calendar: the four ``events_fields_v1`` columns."""
    days = tuple(trading_days)
    position = {day: i for i, day in enumerate(days)}
    benchmark = adjusted.get(inputs.earnings.benchmark)
    if benchmark is None or benchmark.empty:
        raise ValueError(f"benchmark {inputs.earnings.benchmark} has no adjusted daily bars for the event fields")
    bench = by_session(benchmark)
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days])
    actions_by_symbol: dict[str, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        actions_by_symbol[action.symbol].append(action)
    frames: dict[str, pd.DataFrame] = {}
    first_finite: dict[str, dict[str, str | None]] = {}
    no_reports: list[str] = []
    no_actions: list[str] = []
    skipped_total = unpriced_total = 0
    for symbol in symbols:
        frame = adjusted.get(symbol)
        keyed = by_session(frame) if frame is not None and not frame.empty else None
        close = np.full(len(days), np.nan)
        if keyed is not None:
            close = keyed["Close"].reindex(list(days)).to_numpy(float)
        rows = calendar.rows_by_symbol.get(symbol, ())
        if not rows:
            no_reports.append(symbol)
        if symbol not in actions_by_symbol:
            no_actions.append(symbol)
        age, surprise, reaction, skipped = _earnings_columns(rows, keyed, bench, days, position, inputs)
        dividend_yield, unpriced = _dividend_yield(actions_by_symbol.get(symbol, ()), close, days, inputs)
        skipped_total += skipped
        unpriced_total += unpriced
        built = pd.DataFrame(
            {
                "earnings_age": age,
                "earnings_surprise": surprise,
                "earnings_reaction": reaction,
                "dividend_yield": dividend_yield,
            },
            index=index,
        )
        frames[symbol] = built
        first_finite[symbol] = {
            name: (
                days[int(np.argmax(np.isfinite(built[name].to_numpy())))].isoformat()
                if np.isfinite(built[name].to_numpy()).any()
                else None
            )
            for name in EVENT_FIELDS
        }
    return EventFrames(
        frames=frames,
        coverage=EventCoverage(
            symbols_without_reports=tuple(no_reports),
            symbols_without_actions=tuple(no_actions),
            non_session_reports=skipped_total,
            unpriced_dividends=unpriced_total,
            first_finite=first_finite,
        ),
    )
```

Note `bisect_left(days, action.effective_on)` needs `days` as a sequence of `date`; `weekdays` from `earnings_history` returns a list (fine). `by_session(...)` frames use `Close` (capitalised) — confirm against `pead_events.by_session` and the bars' column names in `tests/research/pooled/test_formula.daily`. If `keyed["Close"]` is wrong, use the column the fixtures produce (check `daily()`), never a lower-casing shim.

DSL: in `dsl.py` extend `FIELD_UNITS`:

```python
FIELD_UNITS = {
    **dict.fromkeys(("open", "high", "low", "close", "vwap"), (1.0, 0.0)),
    "volume": (0.0, 1.0),
    **dict.fromkeys(("returns", "hl_spread", "oc_spread", "open_gap"), (0.0, 0.0)),
    # Event-conditioned fields (pooled events lane, events_fields_v1): present only when a frame carries them.
    **dict.fromkeys(("earnings_age", "earnings_surprise", "earnings_reaction", "dividend_yield"), (0.0, 0.0)),
}
```

`prepare_data_fields` already keeps any column whose lower-cased name is in `FIELD_UNITS`, so no further change; verify no other code enumerates `FIELD_UNITS` as "bar columns" (grep `FIELD_UNITS` across `agentic_trader` and fix any such listing to exclude the event names only if it would otherwise require them).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_events.py tests/research/alpha -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/pooled agentic_trader/research/alpha/dsl.py`
Stage and report.

---

### Task 3: `ScoreBook.with_events`, `CubeBuild.events`, `score_book` dispatch, CLI options

**Files:**
- Modify: `agentic_trader/research/pooled/scoring.py` (`ScoreBook` ~line 99; `score_book` ~line 241)
- Modify: `agentic_trader/research/pooled/runner.py` (`CubeBuild` line 51; `build_cube_inputs` line 150)
- Modify: `agentic_trader/cli/commands/alpha.py` (`_pooled_build` line 1497; the `pooled power` (~1526), `pooled null-check` (~1712) and `pooled campaign` (~1786) commands)
- Modify: `agentic_trader/research/pooled/campaign_run.py` (result extras ~line 577: add `event_coverage`)
- Test: `tests/research/pooled/test_scoring.py` (append), `tests/research/pooled/test_runner.py` (append), `tests/research/pooled/mini_world.py` (add `events_cube_build`, `mini_events_protocol`), `tests/cli/test_cli_alpha.py` (append)

**Interfaces:**
- Consumes: Task 1 models, Task 2 `EventFrames`, `build_event_frames`, `load_calendar_pages`, `load_actions`, `verify_event_sources`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class EventSources:          # runner.py
      earnings_pages: Path     # the Nasdaq page directory
      actions_file: Path       # the acquisition's actions.json

  class CubeBuild:  events: EventFrames | None = None   # new field (default None)

  async def build_cube_inputs(..., load_hourly: bool = False, events: tuple[EventInputs, EventSources] | None = None) -> CubeBuild
  ScoreBook.with_events(adjusted, events: Mapping[str, pd.DataFrame], trading_days, sessions, symbols, *, capacity=64) -> ScoreBook   # score_inputs "daily", attribute event_inputs_bound=True
  score_book(protocol, built)  # EventsCampaignProtocol -> with_events(built.adjusted, built.events.frames, ...), raises when built.events is None
  ```
  CLI: options `--earnings-pages DIR` and `--corporate-actions FILE` (both `click.Path(exists=True, path_type=Path)`, default `None`) on `pooled power`, `pooled null-check`, `pooled campaign`; `_pooled_build(..., events=None)`; a helper `_pooled_event_sources(protocol, earnings_pages, corporate_actions) -> tuple[EventInputs, EventSources] | None` that raises `click.ClickException` when an events protocol lacks either option ("--earnings-pages and --corporate-actions are required for an events protocol") or a non-events protocol was given either ("--earnings-pages/--corporate-actions apply only to an events protocol").

- [ ] **Step 1: Write the failing tests**

`tests/research/pooled/mini_world.py` additions (keep style of the existing helpers):

```python
def event_frames(seed: int = 3) -> dict[str, pd.DataFrame]:
    """Synthetic events_fields_v1 frames on BAR_DAYS: a report every ~63 sessions per name."""
    rng = np.random.default_rng(seed)
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in BAR_DAYS])
    n = len(BAR_DAYS)
    frames = {}
    for j, name in enumerate(NAMES):
        age = np.full(n, np.nan)
        surprise = np.full(n, np.nan)
        reaction = np.full(n, np.nan)
        starts = list(range(30 + j, n, 63))
        for k, start in enumerate(starts):
            stop = starts[k + 1] if k + 1 < len(starts) else n
            age[start:stop] = np.arange(stop - start, dtype=float)
            surprise[start:stop] = rng.normal(0.0, 10.0)
            reaction[start:stop] = rng.normal(0.0, 1.0)
        dividend_yield = np.full(n, np.nan)
        dividend_yield[251:] = 0.01 * (j % 4)
        frames[name] = pd.DataFrame(
            {
                "earnings_age": age,
                "earnings_surprise": surprise,
                "earnings_reaction": reaction,
                "dividend_yield": dividend_yield,
            },
            index=index,
        )
    return frames


EVENT_FRAMES = event_frames()


def mini_events_protocol(**update):
    """The mini fixed-set protocol reading daily bars plus the events_fields_v1 columns."""
    document = mini_fixed_set_protocol(**update).model_dump(mode="json")
    document.update(
        id="pooled-events-campaign",
        score_inputs="daily",
        event_inputs={
            "fields_version": "events_fields_v1",
            "earnings": {
                "source": "nasdaq-calendar",
                "pages_sha256": "a" * 64,
                "calendar_from": "2016-02-23",
                "calendar_through": "2026-07-31",
                "min_estimates": 1,
                "min_abs_forecast": 0.05,
                "vol_window": 20,
                "benchmark": "SPY",
            },
            "corporate_actions": {
                "source": "alpaca",
                "content_sha256": "b" * 64,
                "window": ["2016-01-01", "2027-03-31"],
                "trailing_sessions": 252,
            },
        },
    )
    return EventsCampaignProtocol.model_validate(document)


def events_cube_build(cube: LabelCube) -> CubeBuild:
    coverage = EventCoverage(
        symbols_without_reports=(),
        symbols_without_actions=(),
        non_session_reports=0,
        unpriced_dividends=0,
        first_finite={name: dict.fromkeys(EVENT_FIELDS, BAR_DAYS[251].isoformat()) for name in NAMES},
    )
    return CubeBuild(
        cube=cube,
        trading_days=BAR_DAYS,
        adjusted=ADJUSTED,
        bar_failures={},
        static_used=(),
        events=EventFrames(frames=EVENT_FRAMES, coverage=coverage),
    )
```

(`mini_fixed_set_protocol`'s families use daily seeds; `mini_events_protocol` should pass `families=` with event seeds in the tests that score — see below.)

Append to `tests/research/pooled/test_scoring.py`:

```python
from agentic_trader.research.pooled.events import EventFrames
from tests.research.pooled.mini_world import (
    BAR_DAYS,
    ADJUSTED,
    EVENT_FRAMES,
    NAMES,
    SESSIONS,
    events_cube_build,
    mini_events_protocol,
    label_cube,
)


def test_with_events_reads_event_columns_at_d_minus_one_like_daily_fields():
    book = ScoreBook.with_events(ADJUSTED, EVENT_FRAMES, BAR_DAYS, SESSIONS, NAMES)
    assert book.score_inputs == "daily"
    panel = book.panel("earnings_age")
    position = {d: i for i, d in enumerate(BAR_DAYS)}
    for row, session in ((0, SESSIONS[0]), (500, SESSIONS[500])):
        previous = BAR_DAYS[position[session] - 1]
        for col, name in enumerate(NAMES):
            expected = EVENT_FRAMES[name].loc[pd.Timestamp(previous), "earnings_age"]
            assert (np.isnan(panel[row, col]) and np.isnan(expected)) or panel[row, col] == expected
    # bar fields still work on the same book
    assert np.isfinite(book.panel("close / ts_mean(close, 20)")[-1]).all()


def test_with_events_mixes_bar_and_event_fields_in_one_expression():
    book = ScoreBook.with_events(ADJUSTED, EVENT_FRAMES, BAR_DAYS, SESSIONS, NAMES)
    mixed = book.panel("if_else(earnings_age > 20, ts_sum(returns, 20), 0)")
    plain = book.panel("ts_sum(returns, 20)")
    age = book.panel("earnings_age")
    quiet = np.isfinite(age) & (age > 20)
    assert np.allclose(mixed[quiet], plain[quiet], equal_nan=True)
    assert (mixed[np.isfinite(age) & (age <= 20)] == 0).all()


def test_with_events_without_a_symbols_frame_leaves_bar_fields_and_nans_event_fields():
    frames = {k: v for k, v in EVENT_FRAMES.items() if k != NAMES[0]}
    book = ScoreBook.with_events(ADJUSTED, frames, BAR_DAYS, SESSIONS, NAMES)
    assert np.isnan(book.panel("earnings_age")[:, 0]).all()
    assert np.isfinite(book.panel("close")[-1, 0])


def test_score_book_dispatches_events_protocol_and_refuses_a_build_without_events():
    cube = label_cube()
    protocol = mini_events_protocol()
    book = score_book(protocol, events_cube_build(cube))
    assert book.score_inputs == "daily" and np.isfinite(book.panel("dividend_yield")[-1]).any()
    with pytest.raises(ValueError, match="event"):
        score_book(protocol, cube_build(cube))


def test_frozen_panels_from_an_events_book_serve_event_expressions():
    book = ScoreBook.with_events(ADJUSTED, EVENT_FRAMES, BAR_DAYS, SESSIONS, NAMES)
    frozen = book.freeze(["earnings_surprise / (1 + earnings_age)"])
    assert frozen.score_inputs == "daily"
    assert frozen.panel("earnings_surprise / (1 + earnings_age)").shape == (len(SESSIONS), len(NAMES))
```

Append to `tests/research/pooled/test_runner.py` (the module has a fake `BarSource`/`CalendarSource` and a cached-cube fixture for the hourly cache-hit test from the intraday work — reuse that fixture pattern):

```python
def test_events_build_fails_closed_on_digest_mismatch(tmp_path, cached_cube_world):
    """Digest mismatch -> ValueError before any bar is read (the fake bar source records calls)."""
    from agentic_trader.research.pooled.runner import EventSources, build_cube_inputs
    from tests.research.pooled.mini_world import mini_events_protocol
    from tests.research.pooled.test_events import _write_pages, _dividend

    pages = tmp_path / "nasdaq"
    _write_pages(pages, list(weekdays(date(2016, 2, 23), 3)), {})
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([_dividend("N00", date(2017, 3, 1)).model_dump(mode="json")]))
    protocol = mini_events_protocol()
    inputs = protocol.event_inputs.model_copy(
        update={
            "earnings": protocol.event_inputs.earnings.model_copy(
                update={"calendar_from": date(2016, 2, 23), "calendar_through": date(2016, 2, 25)}
            )
        }
    )
    with pytest.raises(ValueError, match="digest"):
        await_(
            build_cube_inputs(
                cached_cube_world.cohort,
                cached_cube_world.spec,
                bars=cached_cube_world.bars,
                calendar=cached_cube_world.calendar,
                cache_dir=cached_cube_world.cache_dir,
                static_symbols=("SPY",),
                pace=cached_cube_world.pace,
                events=(inputs, EventSources(earnings_pages=pages, actions_file=actions)),
            )
        )
    assert cached_cube_world.bars.calls == []


def test_events_build_on_a_cache_hit_fetches_the_benchmark_and_builds_frames(tmp_path, cached_cube_world):
    """Pinned digests match -> CubeBuild.events has one frame per cohort symbol and SPY came from the bar cache."""
    ...  # mirror the intraday cache-hit test: write pages + actions, compute their digests with pages_sha256/load_actions,
    # build inputs with those digests, call build_cube_inputs(events=...), assert set(built.events.frames) == set(cohort symbols),
    # every frame has EVENT_FIELDS columns on built.trading_days, and "SPY" appears in the fake bar source's calls exactly once
    # with timeframe "1d" (or not at all if the fixture pre-populates the SPY cache file).
```

(Adapt names to the fixture the intraday hourly cache-hit test uses; read `tests/research/pooled/test_runner.py` first. If no such fixture exists, build the smallest one: a fake bar source returning `daily()` frames for cohort names and `SPY`, a calendar source over `BAR_DAYS`, and a pre-built cube saved with `save_cube` into `cache_dir`.)

Append to `tests/cli/test_cli_alpha.py` (next to the pooled command tests; find them with `grep -n "pooled" tests/cli/test_cli_alpha.py`):

```python
def test_pooled_cli_refuses_event_options_for_non_event_protocols(tmp_path, cli_runner):
    result = cli_runner.invoke(
        main,
        [
            "alpha",
            "pooled",
            "power",
            "config/research/pooled/intraday-v1.json",
            "--output",
            str(tmp_path / "o"),
            "--cache",
            str(tmp_path / "c"),
            "--earnings-pages",
            str(tmp_path),
            "--corporate-actions",
            "config/research/pooled/intraday-v1.json",
        ],
    )
    assert result.exit_code != 0 and "apply only to an events protocol" in result.output


def test_pooled_cli_requires_event_options_for_an_events_protocol(tmp_path, cli_runner):
    from tests.research.pooled.test_campaign_protocol import _events_document

    path = tmp_path / "events.json"
    path.write_text(json.dumps(_events_document()))
    result = cli_runner.invoke(
        main, ["alpha", "pooled", "power", str(path), "--output", str(tmp_path / "o"), "--cache", str(tmp_path / "c")]
    )
    assert result.exit_code != 0 and "--earnings-pages and --corporate-actions are required" in result.output
```

(Use the runner fixture/`main` import the module already uses; the option check must run **before** any client or cohort I/O so these tests need no network or cache.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_scoring.py tests/research/pooled/test_runner.py tests/cli/test_cli_alpha.py -q -k "events or event_options"`
Expected: FAIL (`with_events` missing; `EventSources` import error; CLI options unknown).

- [ ] **Step 3: Implement**

`scoring.py` — add to `ScoreBook`:

```python
@classmethod
def with_events(
    cls,
    adjusted: Mapping[str, pd.DataFrame],
    events: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    sessions: Sequence[date],
    symbols: Sequence[str],
    *,
    capacity: int = DEFAULT_CAPACITY,
) -> ScoreBook:
    """A daily book whose frames also carry the ``events_fields_v1`` columns (spec 2026-10-14, events):
    the same D-1 read for bar and event fields; a symbol without an event frame keeps its bars and
    gets NaN event fields."""
    book = cls.__new__(cls)
    prepared = prepare_daily(adjusted)
    for symbol, keyed in prepared.items():
        frame = events.get(symbol)
        columns = (
            frame.reindex(keyed.index)
            if frame is not None
            else pd.DataFrame(index=keyed.index, columns=list(EVENT_FIELDS), dtype=float)
        )
        prepared[symbol] = keyed.join(columns, how="left")
    trading = tuple(trading_days)
    book._setup(
        sessions,
        symbols,
        capacity,
        lambda expression: evaluate_prepared(expression, prepared, trading, book._sessions, book._symbols),
        "daily",
    )
    return book
```

(`EVENT_FIELDS` is imported from `events.py`; import it at module top — check there is no import cycle: `events.py` imports `campaign.py` only, never `scoring.py`.) `score_book`:

```python
if isinstance(protocol, EventsCampaignProtocol):
    if built.events is None:
        raise ValueError(
            "this protocol scores event fields but the build carries no event frames; build with events=..."
        )
    return ScoreBook.with_events(
        built.adjusted, built.events.frames, built.trading_days, built.cube.sessions, built.cube.symbols
    )
```

`runner.py` — `EventSources` dataclass; `CubeBuild.events: EventFrames | None = None`; in `build_cube_inputs`, after bars are loaded on **both** paths (cache hit and build), before returning:

```python
event_frames = None
if events is not None:
    inputs, sources = events
    calendar_pages = await asyncio.to_thread(
        load_calendar_pages, sources.earnings_pages, inputs.earnings.calendar_from, inputs.earnings.calendar_through
    )
    actions, actions_digest = await asyncio.to_thread(load_actions, sources.actions_file)
    verify_event_sources(inputs, calendar_pages, actions_digest)
    if inputs.earnings.benchmark not in adjusted:
        frame = await acquisition.fetch(inputs.earnings.benchmark, "1d", bar_dir, start, end)
        if frame is not None:
            adjusted[inputs.earnings.benchmark] = frame
        acquisition.raise_on_errors()
    say(f"event fields: {calendar_pages.pages} calendar pages, {len(actions)} corporate actions")
    event_frames = await asyncio.to_thread(
        build_event_frames, inputs, calendar_pages, actions, adjusted, trading_days, symbols
    )
    say(
        f"event fields: {len(event_frames.frames)} symbols; {len(event_frames.coverage.symbols_without_reports)} without reports"
    )
```

**Order matters for Review Focus 4:** load and verify the digests **before** the bar loop (move the three loader lines to the top of the function, right after the cube-path checks, so a digest mismatch raises before any `acquisition.fetch`); only the benchmark fetch and `build_event_frames` run after the bars. Return `events=event_frames` in `CubeBuild` (keep `adjusted` restricted to cohort symbols as today; the benchmark frame is only used inside `build_event_frames`).

`alpha.py` — options and helper:

```python
def _pooled_event_sources(protocol, earnings_pages: Path | None, corporate_actions: Path | None):
    given = earnings_pages is not None or corporate_actions is not None
    if isinstance(protocol, EventsCampaignProtocol):
        if earnings_pages is None or corporate_actions is None:
            raise click.ClickException("--earnings-pages and --corporate-actions are required for an events protocol")
        return protocol.event_inputs, EventSources(earnings_pages=earnings_pages, actions_file=corporate_actions)
    if given:
        raise click.ClickException("--earnings-pages/--corporate-actions apply only to an events protocol")
    return None
```

Add the two options to the three commands, call `_pooled_event_sources` right after `load_campaign_protocol` (before cohort/clients), pass `events=` through `_pooled_build(...)` to `build_cube_inputs`. In `campaign_run.py`'s result add `"event_coverage": (asdict(built.events.coverage) if built.events is not None else None)` next to `symbols_without_hourly` (use `dataclasses.asdict`; `first_finite` is already JSON-safe).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled tests/cli/test_cli_alpha.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/pooled agentic_trader/cli/commands/alpha.py`
Stage and report.

---

### Task 4: Frozen `events-v1.json`, pin tests, docs

**Files:**
- Create: `config/research/pooled/events-v1.json`
- Test: `tests/research/pooled/test_events_v1.py` (new)
- Modify: `docs/alpha-pooled-mining.md` (new section "Event score inputs (protocol `pooled-events-campaign`)" after the intraday section ending ~line 308; "Commands" section ~line 864: the two new options), `docs/alpha-roadmap.md` (item 5 status, after the October 14 intraday status), `CLAUDE.md` (one sentence in the pooled-lane paragraph after the intraday sentence), `docs/apriori-alphas.md` only if it lists where the PEAD calendar cache is reused (one cross-reference line)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: the frozen file (its SHA-256 is recorded in the results doc after the runs, not in code).

- [ ] **Step 1: Write the failing tests** `tests/research/pooled/test_events_v1.py`:

```python
import json
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.pooled.campaign import EventsCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.formula import require_dimensionless
from tests.research.pooled.mini_world import ADJUSTED, EVENT_FRAMES, NAMES
from tests.research.pooled.test_intraday_v1 import V1 as INTRADAY


REPO = Path(__file__).resolve().parents[3]
V1 = REPO / "config/research/pooled/events-v1.json"
EXPECTED_SCORES = {
    "pead_surprise_decay": "earnings_surprise / (1 + earnings_age)",
    "pead_reaction_decay": "earnings_reaction / (1 + earnings_age)",
    "pead_reaction_window": "if_else(earnings_age <= 20, earnings_reaction, 0)",
    "pead_agreement": "if_else(earnings_age <= 20, sign(earnings_surprise) * earnings_reaction, 0)",
    "surprise_x_reaction": "earnings_surprise * earnings_reaction / (1 + earnings_age)",
    "quiet_momentum": "if_else(earnings_age > 20, ts_sum(returns, 20), 0)",
    "quiet_reversal": "if_else(earnings_age > 20, -ts_sum(returns, 5), 0)",
    "dividend_yield": "dividend_yield",
}


def test_events_v1_is_a_fixed_set_on_the_intraday_cube_with_pinned_sources():
    loaded = load_campaign_protocol(V1)
    protocol = loaded.protocol
    assert isinstance(protocol, EventsCampaignProtocol)
    assert protocol.version == 1 and protocol.search_mode == "fixed_set" and protocol.score_inputs == "daily"
    intraday = load_campaign_protocol(INTRADAY).protocol
    assert protocol.cube_spec().identity == intraday.cube_spec().identity
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")
    for name in (
        "cohort_sha256",
        "windows",
        "selection",
        "bootstrap",
        "discovery_gate",
        "selection_gate",
        "confirmation_gate",
        "power",
        "null_check",
        "search",
        "coverage",
        "universe",
        "bars_from",
        "bars_through",
    ):
        assert getattr(protocol, name) == getattr(intraday, name), name
    assert {f.id: f.seeds[0] for f in protocol.families} == EXPECTED_SCORES
    assert [f.id for f in protocol.families] == list(EXPECTED_SCORES)
    assert protocol.formula_budget == 8 and all(not f.mutation_operators for f in protocol.families)
    for seed in protocol.seed_expressions:
        require_dimensionless(seed)
    e = protocol.event_inputs
    assert e.fields_version == "events_fields_v1"
    assert e.earnings.pages_sha256 == "9273e40a7aaed0d925003502a621ecfedf40d0aa889aeaaca992d84f187aa3da"
    assert (e.earnings.calendar_from.isoformat(), e.earnings.calendar_through.isoformat()) == (
        "2016-02-23",
        "2026-07-31",
    )
    assert (e.earnings.min_estimates, e.earnings.min_abs_forecast, e.earnings.vol_window, e.earnings.benchmark) == (
        1,
        0.05,
        20,
        "SPY",
    )
    assert e.corporate_actions.content_sha256 == "776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344"
    assert tuple(d.isoformat() for d in e.corporate_actions.window) == ("2016-01-01", "2027-03-31")
    assert e.corporate_actions.trailing_sessions == 252
    assert protocol.power_search is None


def test_events_v1_excludes_every_intraday_and_v4_family():
    raw = json.loads(V1.read_text())
    intraday = json.loads(INTRADAY.read_text())
    excluded = {entry["id"] for entry in raw["excluded_families"]}
    assert {entry["id"] for entry in intraday["excluded_families"]} <= excluded
    assert {family["id"] for family in intraday["families"]} <= excluded
    assert not excluded & set(EXPECTED_SCORES)


def test_every_events_v1_seed_evaluates_on_a_frame_with_event_columns():
    from agentic_trader.research.pooled.formula import prepare_daily

    keyed = prepare_daily(ADJUSTED)[NAMES[0]].join(EVENT_FRAMES[NAMES[0]], how="left")
    evaluator = AlphaExpressionEvaluator()
    for expression in EXPECTED_SCORES.values():
        series = evaluator.evaluate(expression, keyed)
        assert isinstance(series, pd.Series) and series.index.equals(keyed.index)
        assert np.isfinite(series.to_numpy(float)).any(), expression
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_events_v1.py -q`
Expected: FAIL (`FileNotFoundError` for `events-v1.json`).

- [ ] **Step 3: Write the frozen protocol** `config/research/pooled/events-v1.json`: start from `config/research/pooled/intraday-v1.json`; remove `score_read`; set `id` to `pooled-events-campaign`, `score_inputs` to `daily`, `version` 1; add the `event_inputs` block with exactly the Global Constraints values (`source` `nasdaq-calendar`/`alpaca`, `fields_version` `events_fields_v1`); replace `families` with the eight rows of `EXPECTED_SCORES` (each `{"id", "seeds": [expr], "mutation_operators": [], "rationale": one sentence from the spec's hypothesis column}`); set `excluded_families` to intraday-v1's list plus one entry per intraday-v1 family (`reason`: "Tested as the intraday fixed set (intraday-v1, 2026-10-14)."); rewrite `title` and `rationale` for the events lane (mention: daily bars at D-1 plus `events_fields_v1`, PEAD visibility convention D+1, no forward-looking event fields, checks A and C only, same cube/cohort/windows/gates as v4 and intraday-v1). Format with two-space indentation and sorted keys like the other files (`python -c "import json,sys; ..."` or match the existing style exactly). If `cond`/`if_else` returns an `ndarray` that breaks `evaluate` on a mixed expression (Step 1's third test), fix the operator to return a `pd.Series` aligned to the test's index in `operators.py` (a correctness fix; keep `np.where` semantics) — never adjust the frozen text to dodge it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q`
Expected: PASS.

- [ ] **Step 5: Docs** — `docs/alpha-pooled-mining.md`: new section after the intraday one covering: the four fields with their exact definitions and the visibility convention; the two inputs, their digests and how they are verified; the CLI options; what `result.json` records (`event_coverage`); non-goals (no news fields, no forward-looking fields, no live contract); the frozen file and its eight formulas; checks A and C only. Add the two options to the "Commands" section. `docs/alpha-roadmap.md`: under item 5's October 14 status add one paragraph "the events lane is built (… spec link …); `events-v1.json` is frozen; checks pending". `CLAUDE.md`: one sentence after the intraday sentence in the pooled paragraph: "Protocol `pooled-events-campaign` (`events-v1.json`) adds four event-conditioned, dimensionless fields (`earnings_age`, `earnings_surprise`, `earnings_reaction`, `dividend_yield`; state known at the prior close, PEAD visibility D+1) read at D-1 beside the daily bars from the offline Nasdaq page cache and the cohort-v2 corporate-actions acquisition, both digest-pinned; fixed set, checks A and C; no news or forward-looking fields." Keep docs factual: no results.

- [ ] **Step 6: Lint, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests && env -u VIRTUAL_ENV uv run pre-commit run --files $(git diff --name-only --cached) 2>&1 | tail -20`
Stage and report.
