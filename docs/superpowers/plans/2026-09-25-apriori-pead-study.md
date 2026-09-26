# A Priori Alpha Catalog: PEAD Study (Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A research-only `copilot alpha apriori-study` command that runs the frozen post-earnings-drift (PEAD) study for the long and short legs and writes a decision per leg.

**Architecture:** A new `agentic_trader/research/apriori/` package: a validated, hash-identified catalog entry (`catalog.py`), paced historical Nasdaq calendar acquisition with a hash-checked page store (`earnings_history.py`), daily-bar event construction reusing the live liquidity code (`pead_events.py`), bracket labelling plus P1–P4 statistics (`pead_study.py`) and bar acquisition (`pead_runner.py`). The Nasdaq row parser moves into `agent/earnings.py` so the live earnings blackout and the study share it. The study writes its manifest before any provider access, as the setup studies do.

**Tech Stack:** Python 3.14, `uv`, pandas/numpy, httpx (Nasdaq), alpaca-py via existing `AlpacaDataProvider`, click, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-09-25-apriori-pead-study-design.md`

## Global Constraints

- Run everything from the worktree `/Users/adamhadani/Development/agentic-trader-pead` as `env -u VIRTUAL_ENV uv run ...`. Never read-write or run anything in `/Users/adamhadani/Development/agentic-trader` (the live installed checkout).
- Research only: no database writes, no alpha registry/trial/shadow/promotion credit, no notifications, no broker access. Every result carries `"authorizes_promotion": false`.
- No schema migration; schema head stays `008_alpha_pipeline`.
- Only GET requests are retried (bounded). Tests use no network (the suite blocks Python/libcurl network I/O).
- Frozen values (from the spec): decisions `2016-03-01`..`2026-07-31`, bars through `2026-09-01`, recent from `2023-01-01`; feed `alpaca:sip`, adjustment `all`; surprise ±5%, reaction ±1σ, `vol_window` 20, benchmark `SPY`, `min_abs_forecast` 0.05, `min_estimates` 1; `min_price` 10.0, 20-session dollar volume, static percentile 0.25; decision `10:35` ET on D+2; stop 2.0×ATR14, target 3R, hold 20 sessions (60 secondary); costs `[0.0, 5.0]` bps per side, 5.0 decides; bootstrap block mean 10, 2000 draws, seed 20260925; pass rule `min_events` 300, `min_recent_events` 100, `trim_fraction` 0.01; `max_failed_calendar_fraction` 0.02; calendar interval 1.0 s.
- `ci90` = [5th, 95th] percentile of the bootstrap; a criterion holds when the lower bound is > 0 (the short-suppression precedent).
- Private artifacts: directories `0o700`, files `0o600`; never overwrite an existing output directory.
- Match surrounding style: module docstrings explaining contracts, comment density of `research/setups/`. Update callers to canonical names; no compatibility aliases.
- Before each commit: affected tests green; before the final task: full `env -u VIRTUAL_ENV uv run pytest -q` and `env -u VIRTUAL_ENV uv run pre-commit run --all-files` green (stage first).

## Review Focus

1. **A calendar page whose saved bytes no longer match its recorded SHA-256** (disk corruption, a hand edit) — the run must refuse rather than silently re-parse different data. Pinned in Task 3.
2. **Nasdaq value formats beyond the happy path** — `($0.95)` negatives, `$1,234.50` commas, blank or `N/A` forecasts, `N/A` estimate counts — must parse to the right sign or to `None`, never raise or mis-sign a surprise. Pinned in Task 1.
3. **Reaction windows that straddle weekends and holidays** — D−1 and D+1 must be adjacent *sessions*, and σ must use only sessions strictly before D (no lookahead into the report day). Pinned in Task 4.
4. **A symbol that has daily bars but no hourly bar ending before 10:35 on D+2** (halted, thin, or late listing) — the event must be counted as missing, not labelled with a later price. Pinned in Task 5.
5. **A study run killed or failing mid-way** — `protocol.json`/`manifest.json` already exist and `result.json` records `status: failed` with the error; a rerun into a fresh directory with `--cache` reuses saved pages and bars. Pinned in Tasks 5 and 6.

---

### Task 1: Shared Nasdaq calendar row parser

**Files:**
- Modify: `agentic_trader/agent/earnings.py`
- Test: `tests/agent/test_earnings.py` (existing; add tests)

**Interfaces:**
- Produces:
  - `NASDAQ_REQUEST_HEADERS: dict[str, str]` (module constant, `{"User-Agent": "Mozilla/5.0", "Accept": "application/json"}`)
  - `@dataclass(frozen=True) class CalendarRow: symbol: str; date: date; timing: EarningsTiming; eps: float | None; eps_forecast: float | None; surprise_pct_reported: float | None; n_estimates: int | None; fiscal_quarter: str | None`
  - `def parse_calendar_payload(payload: object, day: date) -> list[CalendarRow]`
- `NasdaqEarningsCalendar._request_date` uses `parse_calendar_payload`; its observable behaviour (dict `symbol → EarningsEvent`, `None` on failure) is unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/agent/test_earnings.py`; reuse its existing imports and add these)

```python
from agentic_trader.agent.earnings import CalendarRow, parse_calendar_payload


def _payload(*rows):
    return {"data": {"asOf": "Thu, Apr 29, 2021", "rows": list(rows)}}


def test_parse_calendar_payload_reads_eps_fields():
    row = {
        "symbol": "amzn",
        "time": "time-not-supplied",
        "eps": "$15.79",
        "epsForecast": "$9.75",
        "surprise": "61.95",
        "noOfEsts": "15",
        "fiscalQuarterEnding": "Mar/2021",
        "marketCap": "$2,750,294,260,000",
    }
    [parsed] = parse_calendar_payload(_payload(row), date(2021, 4, 29))
    assert parsed == CalendarRow(
        symbol="AMZN",
        date=date(2021, 4, 29),
        timing=EarningsTiming.UNSPECIFIED,
        eps=15.79,
        eps_forecast=9.75,
        surprise_pct_reported=61.95,
        n_estimates=15,
        fiscal_quarter="Mar/2021",
    )


def test_parse_calendar_payload_value_formats():
    rows = [
        {"symbol": "NEG", "eps": "($0.95)", "epsForecast": "($1.07)", "surprise": "-16", "noOfEsts": "3"},
        {"symbol": "BIG", "eps": "$1,234.50", "epsForecast": "$2", "surprise": "N/A", "noOfEsts": "N/A"},
        {"symbol": "BLANK", "eps": "", "epsForecast": "", "surprise": "", "noOfEsts": ""},
        {"symbol": "JUNK", "eps": "abc", "epsForecast": None, "surprise": "nan", "noOfEsts": "2.5"},
    ]
    parsed = {row.symbol: row for row in parse_calendar_payload(_payload(*rows), date(2021, 4, 29))}
    assert (parsed["NEG"].eps, parsed["NEG"].eps_forecast, parsed["NEG"].surprise_pct_reported) == (-0.95, -1.07, -16.0)
    assert parsed["NEG"].n_estimates == 3
    assert (parsed["BIG"].eps, parsed["BIG"].eps_forecast, parsed["BIG"].surprise_pct_reported) == (1234.5, 2.0, None)
    assert parsed["BIG"].n_estimates is None
    for symbol in ("BLANK", "JUNK"):
        row = parsed[symbol]
        assert (row.eps, row.eps_forecast, row.surprise_pct_reported, row.n_estimates) == (None, None, None, None)


def test_parse_calendar_payload_empty_and_malformed_days():
    day = date(2021, 5, 1)
    assert parse_calendar_payload({"data": None}, day) == []
    assert parse_calendar_payload({"data": {"rows": None}}, day) == []
    assert parse_calendar_payload([], day) == []
    assert parse_calendar_payload(_payload("not a row", {"symbol": ""}, {"eps": "$1"}), day) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_earnings.py -q`
Expected: FAIL with `ImportError: cannot import name 'CalendarRow'`.

- [ ] **Step 3: Implement** — in `agentic_trader/agent/earnings.py` add `import math`, then after `EarningsEvent`:

```python
NASDAQ_REQUEST_HEADERS: dict[str, str] = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


@dataclass(frozen=True)
class CalendarRow:
    """One Nasdaq calendar row: the report date plus the EPS fields the calendar serves today.

    Shared by the live earnings blackout (which reads only ``symbol``/``date``/``timing``)
    and the a priori PEAD study. The EPS values are as the endpoint serves them now, not
    guaranteed point-in-time.
    """

    symbol: str
    date: date
    timing: EarningsTiming
    eps: float | None
    eps_forecast: float | None
    surprise_pct_reported: float | None
    n_estimates: int | None
    fiscal_quarter: str | None


def _number(raw: object) -> float | None:
    """``'$1.07'`` -> 1.07, ``'($0.95)'`` -> -0.95, ``'$1,234.50'`` -> 1234.5; blank, ``N/A`` or junk -> None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.upper() == "N/A":
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace("$", "").replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return -abs(value) if negative else value


def _count(raw: object) -> int | None:
    value = _number(raw)
    if value is None or value < 0 or not value.is_integer():
        return None
    return int(value)


def parse_calendar_payload(payload: object, day: date) -> list[CalendarRow]:
    """Rows of one Nasdaq calendar response for ``day``.

    A missing or null ``data``/``rows`` is an empty day (the endpoint serves
    ``data: null`` on some non-trading dates); rows without a symbol are skipped.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    raw_rows = data.get("rows") if isinstance(data, dict) else None
    rows: list[CalendarRow] = []
    for row in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        quarter = str(row.get("fiscalQuarterEnding") or "").strip()
        rows.append(
            CalendarRow(
                symbol=symbol,
                date=day,
                timing=_parse_timing(row.get("time")),
                eps=_number(row.get("eps")),
                eps_forecast=_number(row.get("epsForecast")),
                surprise_pct_reported=_number(row.get("surprise")),
                n_estimates=_count(row.get("noOfEsts")),
                fiscal_quarter=quarter or None,
            )
        )
    return rows
```

Replace the header literal in `_request_date` with `headers=NASDAQ_REQUEST_HEADERS`, and replace its parse block (the second `try:` through `return rows_map`) with:

```python
try:
    rows_map = {
        row.symbol: EarningsEvent(symbol=row.symbol, date=day, timing=row.timing)
        for row in parse_calendar_payload(payload, day)
    }
except Exception as e:
    logger.warning("Failed to parse Nasdaq earnings calendar payload", extra={"date": day.isoformat(), "error": str(e)})
    return None
return rows_map
```

- [ ] **Step 4: Run the earnings tests (new and existing blackout tests)**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_earnings.py tests/agent -q -k earnings`
Expected: PASS.

- [ ] **Step 5: Stage** (`git add agentic_trader/agent/earnings.py tests/agent/test_earnings.py`); the controller commits: `Earnings: share the Nasdaq calendar row parser, with EPS fields`.

---

### Task 2: Catalog entry model and the frozen PEAD entry

**Files:**
- Create: `agentic_trader/research/apriori/__init__.py`
- Create: `agentic_trader/research/apriori/catalog.py`
- Create: `config/research/apriori/pead-v1.json`
- Test: `tests/research/apriori/__init__.py` (empty), `tests/research/apriori/test_catalog.py`

**Interfaces:**
- Produces:
  - `class PeadEntry(BaseModel, frozen=True, extra="forbid")` with nested `window: PeadWindow` (`decisions: tuple[date, date]`, `bars_through: date`, `recent_from: date`), `event: PeadEvent` (`min_abs_forecast: float`, `min_estimates: int`, `surprise_pct: float`, `reaction_sigma: float`, `vol_window: int`, `benchmark: str`), `universe: PeadUniverse` (`min_price: float`, `dollar_volume_window: Literal[20]`, `static_percentile: float`), `trade: PeadTrade` (`decision_time_et: str`, `entry_session_offset: Literal[2]`, `stop_atr_multiple: float`, `atr_window: int`, `target_r: float`, `max_hold_sessions: int`, `secondary_hold_sessions: int`), `bootstrap: BootstrapSpec` (`block_mean: int`, `draws: int`, `seed: int`), `pass_rule: PassRule` (`min_events: int`, `min_recent_events: int`, `trim_fraction: float`), plus `id: Literal["pead"]`, `version: int`, `title: str`, `references: tuple[str, ...]`, `hypotheses: str`, `decision_rule: str`, `feed: Literal["alpaca:sip"]`, `adjustment: Literal["all"]`, `costs_bps_per_side: tuple[float, ...]`, `decision_cost_bps: float`, `max_failed_calendar_fraction: float`, `calendar_request_interval_seconds: float`.
  - `PeadTrade.decision_time() -> time`
  - `@dataclass(frozen=True) class LoadedEntry: entry: PeadEntry; sha256: str; path: Path`
  - `def load_pead_entry(path: Path) -> LoadedEntry` — SHA-256 of the file's bytes.

- [ ] **Step 1: Write the failing tests** — `tests/research/apriori/test_catalog.py`:

```python
import hashlib
import json
from datetime import date, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.apriori.catalog import PeadEntry, load_pead_entry


ENTRY = Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json"


def test_frozen_entry_loads_with_the_spec_values():
    loaded = load_pead_entry(ENTRY)
    entry = loaded.entry
    assert loaded.sha256 == hashlib.sha256(ENTRY.read_bytes()).hexdigest()
    assert (entry.id, entry.version) == ("pead", 1)
    assert entry.window.decisions == (date(2016, 3, 1), date(2026, 7, 31))
    assert entry.window.bars_through == date(2026, 9, 1) and entry.window.recent_from == date(2023, 1, 1)
    assert (entry.event.surprise_pct, entry.event.reaction_sigma, entry.event.vol_window) == (5.0, 1.0, 20)
    assert (entry.event.min_abs_forecast, entry.event.min_estimates, entry.event.benchmark) == (0.05, 1, "SPY")
    assert (entry.universe.min_price, entry.universe.static_percentile) == (10.0, 0.25)
    assert entry.trade.decision_time() == time(10, 35)
    assert (entry.trade.stop_atr_multiple, entry.trade.target_r, entry.trade.atr_window) == (2.0, 3.0, 14)
    assert (entry.trade.max_hold_sessions, entry.trade.secondary_hold_sessions) == (20, 60)
    assert entry.costs_bps_per_side == (0.0, 5.0) and entry.decision_cost_bps == 5.0
    assert (entry.bootstrap.block_mean, entry.bootstrap.draws, entry.bootstrap.seed) == (10, 2000, 20260925)
    assert (entry.pass_rule.min_events, entry.pass_rule.min_recent_events, entry.pass_rule.trim_fraction) == (
        300,
        100,
        0.01,
    )
    assert entry.max_failed_calendar_fraction == 0.02 and entry.calendar_request_interval_seconds == 1.0


def _document() -> dict:
    return json.loads(ENTRY.read_text())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unexpected=1),
        lambda d: d["window"].update(decisions=["2026-07-31", "2016-03-01"]),
        lambda d: d["window"].update(recent_from="2015-01-01"),
        lambda d: d["window"].update(bars_through="2026-08-15"),
        lambda d: d.update(decision_cost_bps=7.0),
        lambda d: d["trade"].update(decision_time_et="25:00"),
        lambda d: d["trade"].update(secondary_hold_sessions=10),
        lambda d: d["universe"].update(dollar_volume_window=30),
        lambda d: d["pass_rule"].update(trim_fraction=0.6),
    ],
)
def test_invalid_entries_are_rejected(mutate):
    document = _document()
    mutate(document)
    with pytest.raises(ValidationError):
        PeadEntry.model_validate(document)
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_catalog.py -q`
Expected: FAIL (`ModuleNotFoundError: agentic_trader.research.apriori`).

- [ ] **Step 3: Implement** — `agentic_trader/research/apriori/__init__.py`:

```python
"""A priori alpha catalog: literature-documented hypotheses tested by frozen studies.

Research only. See docs/apriori-alphas.md.
"""
```

`agentic_trader/research/apriori/catalog.py`:

```python
"""Catalog entries: one frozen JSON file per a priori alpha under ``config/research/apriori/``.

An entry is committed before any data is read. Its identity is the SHA-256 of the file's
bytes, recorded in every run manifest; a changed file is a new version (``pead-v2``) with a
new, non-overlapping window, never an edit. Entries grant no registry, trial, shadow or
promotion credit. ``hypotheses`` and ``decision_rule`` are free text kept verbatim for the
human record; the computation lives in ``pead_study``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


__all__ = ["LoadedEntry", "PeadEntry", "load_pead_entry"]

# Every labelled decision needs its full holding window of bars before the data cutoff.
_MIN_MATURATION_DAYS = 30


class PeadWindow(BaseModel, frozen=True, extra="forbid"):
    decisions: tuple[date, date]
    bars_through: date
    recent_from: date

    @model_validator(mode="after")
    def _ordered(self) -> PeadWindow:
        start, end = self.decisions
        if start >= end:
            raise ValueError("decisions must be ordered (start < end)")
        if not start < self.recent_from <= end:
            raise ValueError("recent_from must fall inside the decision window")
        if self.bars_through < end + timedelta(days=_MIN_MATURATION_DAYS):
            raise ValueError(f"bars_through must be at least {_MIN_MATURATION_DAYS} days after the last decision")
        return self


class PeadEvent(BaseModel, frozen=True, extra="forbid"):
    min_abs_forecast: float = Field(gt=0)
    min_estimates: int = Field(ge=1)
    surprise_pct: float = Field(gt=0)
    reaction_sigma: float = Field(gt=0)
    vol_window: int = Field(ge=2)
    benchmark: str = Field(min_length=1)


class PeadUniverse(BaseModel, frozen=True, extra="forbid"):
    min_price: float = Field(gt=0)
    # The live liquidity rule (`median_dollar_volume`) is fixed at 20 sessions; the study reuses it.
    dollar_volume_window: Literal[20]
    static_percentile: float = Field(gt=0, lt=1)


class PeadTrade(BaseModel, frozen=True, extra="forbid"):
    decision_time_et: str
    entry_session_offset: Literal[2]
    stop_atr_multiple: float = Field(gt=0)
    atr_window: int = Field(ge=2)
    target_r: float = Field(gt=0)
    max_hold_sessions: int = Field(ge=1)
    secondary_hold_sessions: int = Field(ge=1)

    @field_validator("decision_time_et")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("decision_time_et must be HH:MM")
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _secondary_longer(self) -> PeadTrade:
        if self.secondary_hold_sessions <= self.max_hold_sessions:
            raise ValueError("secondary_hold_sessions must exceed max_hold_sessions")
        return self

    def decision_time(self) -> time:
        return time.fromisoformat(self.decision_time_et)


class BootstrapSpec(BaseModel, frozen=True, extra="forbid"):
    block_mean: int = Field(ge=1)
    draws: int = Field(ge=100)
    seed: int


class PassRule(BaseModel, frozen=True, extra="forbid"):
    min_events: int = Field(ge=1)
    min_recent_events: int = Field(ge=1)
    trim_fraction: float = Field(ge=0, lt=0.5)


class PeadEntry(BaseModel, frozen=True, extra="forbid"):
    """The frozen PEAD protocol (catalog entry ``pead``)."""

    id: Literal["pead"]
    version: int = Field(ge=1)
    title: str
    references: tuple[str, ...]
    hypotheses: str
    decision_rule: str
    window: PeadWindow
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    event: PeadEvent
    universe: PeadUniverse
    trade: PeadTrade
    costs_bps_per_side: tuple[float, ...] = Field(min_length=1)
    decision_cost_bps: float
    bootstrap: BootstrapSpec
    pass_rule: PassRule
    max_failed_calendar_fraction: float = Field(ge=0, lt=1)
    calendar_request_interval_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def _decision_cost_listed(self) -> PeadEntry:
        if self.decision_cost_bps not in self.costs_bps_per_side:
            raise ValueError("decision_cost_bps must be one of costs_bps_per_side")
        return self


@dataclass(frozen=True)
class LoadedEntry:
    entry: PeadEntry
    sha256: str
    path: Path


def load_pead_entry(path: Path) -> LoadedEntry:
    raw = path.read_bytes()
    return LoadedEntry(entry=PeadEntry.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
```

`config/research/apriori/pead-v1.json` (exact content):

```json
{
  "id": "pead",
  "version": 1,
  "title": "Post-earnings-announcement drift: surprise and price reaction agree",
  "references": [
    "Ball and Brown (1968), An Empirical Evaluation of Accounting Income Numbers",
    "Bernard and Thomas (1989), Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?",
    "Chan, Jegadeesh and Lakonishok (1996), Momentum Strategies",
    "Brandt, Kishore, Santa-Clara and Venkatachalam (2008), Earnings Announcements are Full of Surprises",
    "Martineau (2022), Rest in Peace Post-Earnings Announcement Drift"
  ],
  "hypotheses": "For each leg separately, over decisions 2016-03-01..2026-07-31 (entry at 10:35 New York on the second session after the Nasdaq report date D; 2 x ATR14 stop, 3R target, 20-session time exit; 5 bps per side): LONG = surprise >= +5% and SPY-relative D-1..D+1 reaction >= +1 sigma; SHORT = surprise <= -5% and reaction <= -1 sigma. P1: mean R_cost > 0 with the bootstrap ci90 lower bound > 0. P2: mean R_cost(leg) - mean R_cost(control, same direction) > 0 with the paired ci90 lower bound > 0; control = every liquid reporter's same bracket. P3: the 1%-trimmed mean > 0 and the mean over decisions from 2023-01-01 > 0. P4: at least 300 labelled events, at least 100 from 2023-01-01. Stationary session-block bootstrap, block mean 10, 2000 draws, seed 20260925; ci90 = [5th, 95th] percentiles. See docs/superpowers/specs/2026-09-25-apriori-pead-study-design.md.",
  "decision_rule": "A leg for which P1, P2, P3 and P4 all hold becomes eligible for Part 2 (a capped live paper probe with its own spec). Otherwise the leg stays off and is recorded as failed. No threshold is revisited after the run; any change is a new version with a new, non-overlapping window.",
  "window": {
    "decisions": ["2016-03-01", "2026-07-31"],
    "bars_through": "2026-09-01",
    "recent_from": "2023-01-01"
  },
  "feed": "alpaca:sip",
  "adjustment": "all",
  "event": {
    "min_abs_forecast": 0.05,
    "min_estimates": 1,
    "surprise_pct": 5.0,
    "reaction_sigma": 1.0,
    "vol_window": 20,
    "benchmark": "SPY"
  },
  "universe": {
    "min_price": 10.0,
    "dollar_volume_window": 20,
    "static_percentile": 0.25
  },
  "trade": {
    "decision_time_et": "10:35",
    "entry_session_offset": 2,
    "stop_atr_multiple": 2.0,
    "atr_window": 14,
    "target_r": 3.0,
    "max_hold_sessions": 20,
    "secondary_hold_sessions": 60
  },
  "costs_bps_per_side": [0.0, 5.0],
  "decision_cost_bps": 5.0,
  "bootstrap": {
    "block_mean": 10,
    "draws": 2000,
    "seed": 20260925
  },
  "pass_rule": {
    "min_events": 300,
    "min_recent_events": 100,
    "trim_fraction": 0.01
  },
  "max_failed_calendar_fraction": 0.02,
  "calendar_request_interval_seconds": 1.0
}
```

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_catalog.py -q`
Expected: PASS.

- [ ] **Step 5: Stage** the four files; controller commits `A priori catalog: PEAD entry model and frozen pead-v1 protocol`.

---

### Task 3: Historical calendar acquisition with a hash-checked page store

**Files:**
- Create: `agentic_trader/research/apriori/earnings_history.py`
- Test: `tests/research/apriori/test_earnings_history.py`

**Interfaces:**
- Consumes: `parse_calendar_payload`, `CalendarRow`, `NASDAQ_REQUEST_HEADERS` (Task 1); `NASDAQ_EARNINGS_CALENDAR_URL` (`agentic_trader.constants`).
- Produces:
  - `class CalendarPageStore: __init__(self, directory: Path); load(self, day: date) -> bytes | None; save(self, day: date, body: bytes, received_at: datetime) -> None`
  - `@dataclass(frozen=True) class CalendarAcquisition: rows: tuple[CalendarRow, ...]; requested_dates: int; fetched_dates: int; reused_dates: int; failed_dates: tuple[date, ...]` with property `failed_fraction -> float`
  - `async def acquire_calendar(days: Sequence[date], store: CalendarPageStore, *, interval_seconds: float, transport: httpx.AsyncBaseTransport | None = None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, retry_delays: Sequence[float] = RETRY_DELAYS) -> CalendarAcquisition`
  - `def weekdays(start: date, end: date) -> list[date]`
  - `def dedupe_rows(rows: Iterable[CalendarRow]) -> tuple[list[CalendarRow], int]` — first row per `(symbol, date)`, plus the duplicate count.

- [ ] **Step 1: Write the failing tests** — `tests/research/apriori/test_earnings_history.py`:

```python
import json
from datetime import UTC, date, datetime

import httpx
import pytest

from agentic_trader.research.apriori.earnings_history import (
    CalendarPageStore,
    acquire_calendar,
    dedupe_rows,
    weekdays,
)


def _body(*symbols: str) -> bytes:
    rows = [{"symbol": s, "eps": "$1.10", "epsForecast": "$1.00", "noOfEsts": "4"} for s in symbols]
    return json.dumps({"data": {"rows": rows}}).encode()


class _Recorder:
    def __init__(self, responses):
        self.responses = responses  # date iso -> list of (status, body) served in order
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        day = request.url.params["date"]
        self.calls.append((request.method, day))
        status, body = self.responses[day].pop(0)
        return httpx.Response(status, content=body)


async def _no_sleep(_seconds: float) -> None:
    return None


def test_weekdays_skips_weekends():
    assert weekdays(date(2021, 4, 29), date(2021, 5, 4)) == [
        date(2021, 4, 29),
        date(2021, 4, 30),
        date(2021, 5, 3),
        date(2021, 5, 4),
    ]


async def test_acquires_parses_saves_and_reuses_pages(tmp_path):
    store = CalendarPageStore(tmp_path / "nasdaq")
    recorder = _Recorder({"2021-04-29": [(200, _body("AAA", "BBB"))], "2021-04-30": [(200, _body("CCC"))]})
    days = [date(2021, 4, 29), date(2021, 4, 30)]
    first = await acquire_calendar(
        days, store, interval_seconds=1.0, transport=httpx.MockTransport(recorder), sleep=_no_sleep
    )
    assert [row.symbol for row in first.rows] == ["AAA", "BBB", "CCC"]
    assert (first.requested_dates, first.fetched_dates, first.reused_dates, first.failed_dates) == (2, 2, 0, ())
    assert all(method == "GET" for method, _ in recorder.calls)
    for path in (tmp_path / "nasdaq").iterdir():
        assert path.stat().st_mode & 0o777 == 0o600

    again = await acquire_calendar(
        days, store, interval_seconds=1.0, transport=httpx.MockTransport(recorder), sleep=_no_sleep
    )
    assert len(recorder.calls) == 2  # nothing re-fetched
    assert (again.fetched_dates, again.reused_dates) == (0, 2)
    assert [row.symbol for row in again.rows] == ["AAA", "BBB", "CCC"]


async def test_retries_then_records_a_failed_date_without_saving_it(tmp_path):
    store = CalendarPageStore(tmp_path / "nasdaq")
    recorder = _Recorder({"2021-04-29": [(503, b""), (200, b"not json"), (500, b"")]})
    result = await acquire_calendar(
        [date(2021, 4, 29)],
        store,
        interval_seconds=1.0,
        transport=httpx.MockTransport(recorder),
        sleep=_no_sleep,
        retry_delays=(0.0, 0.0),
    )
    assert len(recorder.calls) == 3
    assert result.failed_dates == (date(2021, 4, 29),) and result.failed_fraction == 1.0
    assert store.load(date(2021, 4, 29)) is None


async def test_a_page_that_no_longer_matches_its_hash_is_refused(tmp_path):
    store = CalendarPageStore(tmp_path / "nasdaq")
    day = date(2021, 4, 29)
    store.save(day, _body("AAA"), datetime(2026, 9, 25, tzinfo=UTC))
    page = next(p for p in (tmp_path / "nasdaq").iterdir() if p.name == "2021-04-29.json")
    page.chmod(0o600)
    page.write_bytes(_body("TAMPERED"))
    with pytest.raises(ValueError, match="SHA-256"):
        store.load(day)


async def test_paces_every_request(tmp_path):
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    recorder = _Recorder({"2021-04-29": [(200, _body("AAA"))], "2021-04-30": [(200, _body("BBB"))]})
    await acquire_calendar(
        [date(2021, 4, 29), date(2021, 4, 30)],
        CalendarPageStore(tmp_path / "nasdaq"),
        interval_seconds=1.0,
        transport=httpx.MockTransport(recorder),
        sleep=sleep,
    )
    assert slept == [1.0, 1.0]


def test_dedupe_keeps_the_first_row_per_symbol_and_date():
    from agentic_trader.agent.earnings import CalendarRow, EarningsTiming

    def row(symbol, eps):
        return CalendarRow(symbol, date(2021, 4, 29), EarningsTiming.UNSPECIFIED, eps, 1.0, None, 3, None)

    rows, duplicates = dedupe_rows([row("AAA", 1.0), row("AAA", 2.0), row("BBB", 1.0)])
    assert [(r.symbol, r.eps) for r in rows] == [("AAA", 1.0), ("BBB", 1.0)] and duplicates == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_earnings_history.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** — `agentic_trader/research/apriori/earnings_history.py`:

```python
"""Paced historical acquisition of the Nasdaq earnings calendar for a priori studies.

One GET per weekday, at most one request per ``interval_seconds``, with bounded retries
(GET only). Each accepted page is saved verbatim with its SHA-256 and receipt time; a
rerun reuses saved pages after re-checking their hashes and never re-fetches a saved
date. A date that still fails is recorded, not saved, so the caller can fail closed on
a gappy sample. Parsing uses the live blackout's parser (``agent.earnings``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from agentic_trader.agent.earnings import NASDAQ_REQUEST_HEADERS, CalendarRow, parse_calendar_payload
from agentic_trader.constants import NASDAQ_EARNINGS_CALENDAR_URL


__all__ = [
    "RETRY_DELAYS",
    "CalendarAcquisition",
    "CalendarPageStore",
    "acquire_calendar",
    "dedupe_rows",
    "weekdays",
]

logger = logging.getLogger(__name__)

RETRY_DELAYS: tuple[float, ...] = (2.0, 10.0)
_TIMEOUT_SECONDS = 15.0


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


class CalendarPageStore:
    """``<directory>/<YYYY-MM-DD>.json`` (raw bytes) plus ``<YYYY-MM-DD>.meta.json`` (hash, receipt)."""

    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory

    def _paths(self, day: date) -> tuple[Path, Path]:
        stem = day.isoformat()
        return self.directory / f"{stem}.json", self.directory / f"{stem}.meta.json"

    def load(self, day: date) -> bytes | None:
        page, meta = self._paths(day)
        if not page.exists() or not meta.exists():
            return None
        body = page.read_bytes()
        recorded = json.loads(meta.read_text())["sha256"]
        if hashlib.sha256(body).hexdigest() != recorded:
            raise ValueError(f"Calendar page {page} does not match its recorded SHA-256; refusing a corrupt cache")
        return body

    def save(self, day: date, body: bytes, received_at: datetime) -> None:
        page, meta = self._paths(day)
        _write_private(page, body)
        record = {"sha256": hashlib.sha256(body).hexdigest(), "received_at": received_at.isoformat()}
        _write_private(meta, json.dumps(record, sort_keys=True).encode())


@dataclass(frozen=True)
class CalendarAcquisition:
    rows: tuple[CalendarRow, ...]
    requested_dates: int
    fetched_dates: int
    reused_dates: int
    failed_dates: tuple[date, ...]

    @property
    def failed_fraction(self) -> float:
        return len(self.failed_dates) / self.requested_dates if self.requested_dates else 0.0


def weekdays(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def dedupe_rows(rows: Iterable[CalendarRow]) -> tuple[list[CalendarRow], int]:
    seen: set[tuple[str, date]] = set()
    kept: list[CalendarRow] = []
    duplicates = 0
    for row in rows:
        key = (row.symbol, row.date)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, duplicates


async def _get_page(
    client: httpx.AsyncClient,
    day: date,
    interval_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
    retry_delays: Sequence[float],
) -> bytes | None:
    """The page body for ``day`` when the endpoint returns HTTP 200 and a JSON object, else None."""
    for delay in (*retry_delays, None):
        await sleep(interval_seconds)
        try:
            response = await client.get(
                NASDAQ_EARNINGS_CALENDAR_URL, params={"date": day.isoformat()}, headers=NASDAQ_REQUEST_HEADERS
            )
            if response.status_code == 200 and isinstance(response.json(), dict):
                return response.content
            logger.warning("Nasdaq calendar HTTP %s for %s", response.status_code, day.isoformat())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Nasdaq calendar request failed for %s: %s", day.isoformat(), exc)
        if delay is None:
            return None
        await sleep(delay)
    return None


async def acquire_calendar(
    days: Sequence[date],
    store: CalendarPageStore,
    *,
    interval_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> CalendarAcquisition:
    rows: list[CalendarRow] = []
    fetched = reused = 0
    failed: list[date] = []
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
        for day in days:
            body = store.load(day)
            if body is not None:
                reused += 1
            else:
                body = await _get_page(client, day, interval_seconds, sleep, retry_delays)
                if body is None:
                    failed.append(day)
                    continue
                store.save(day, body, datetime.now(UTC))
                fetched += 1
            rows.extend(parse_calendar_payload(json.loads(body), day))
    return CalendarAcquisition(
        rows=tuple(rows),
        requested_dates=len(days),
        fetched_dates=fetched,
        reused_dates=reused,
        failed_dates=tuple(failed),
    )
```

Note: `test_paces_every_request` expects exactly one `sleep(interval)` per successful request and no retry sleeps; `_get_page` sleeps the interval before each attempt and the retry delay only after a failed attempt.

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_earnings_history.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**; controller commits `A priori: paced historical Nasdaq calendar acquisition with hash-checked pages`.

---

### Task 4: PEAD event construction (daily bars only)

**Files:**
- Create: `agentic_trader/research/apriori/pead_events.py`
- Test: `tests/research/apriori/test_pead_events.py`

**Interfaces:**
- Consumes: `PeadEntry` (Task 2), `CalendarRow` (Task 1), `median_dollar_volume`, `static_reference` (`agentic_trader.screeners.dynamic_universe`), `ET_TZ` (`agentic_trader.market.session`).
- Produces:
  - `SUPPORTED_SYMBOL = re.compile(r"^[A-Z]{1,5}$")`
  - `@dataclass(frozen=True) class MarketData: trading_days: tuple[date, ...]; daily: Mapping[str, pd.DataFrame]; static_symbols: tuple[str, ...]`
  - `def surprise_pct(row: CalendarRow, event: PeadEvent) -> float | None`
  - `def decision_at(day: date, clock: time) -> datetime` (UTC)
  - `def build_events(rows: Sequence[CalendarRow], market: MarketData, entry: PeadEntry) -> tuple[pd.DataFrame, dict]` — events frame with columns `EVENT_COLUMNS` and a counts dict `{"reasons": {reason: n}, "reasons_by_year": {reason: {year: n}}, "rows": n, "events": n}`.
  - `EVENT_COLUMNS = ("symbol", "report_date", "session", "decision_at", "z", "surprise_pct", "surprise_pct_reported", "atr", "median_dollar_volume", "reference", "leg", "surprise_long", "surprise_short", "reaction_long", "reaction_short")` — `session` is the D+2 decision date; `leg` is `"LONG"`, `"SHORT"` or `None`.
  - Skip/missing reasons (exact strings): `"unsupported_symbol"`, `"no_daily_bars"`, `"non_session_date"`, `"outside_calendar"`, `"outside_window"`, `"no_reference"`, `"short_history"`, `"no_reaction_bars"`, `"illiquid_price"`, `"illiquid_volume"`, `"no_atr"`.

- [ ] **Step 1: Write the failing tests** — `tests/research/apriori/test_pead_events.py`:

```python
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import MarketData, build_events, decision_at, surprise_pct


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json").entry


def sessions(start: str, count: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, periods=count)]


def daily_frame(days, closes, volume=1_000_000.0, spread=1.0):
    # 05:00 UTC is the New York date's midnight or 01:00 in both EST and EDT (as Alpaca stamps daily bars).
    index = pd.DatetimeIndex([datetime.combine(d, time(5, 0), tzinfo=UTC) for d in days])
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + spread / 2,
            "Low": closes - spread / 2,
            "Close": closes,
            "Volume": np.full(len(closes), volume),
        },
        index=index,
    )


def report(symbol, day, eps=1.10, forecast=1.00, n=5):
    return CalendarRow(symbol, day, EarningsTiming.UNSPECIFIED, eps, forecast, 10.0, n, "Mar/2021")


def wiggle(n, base=50.0, step=0.2):
    # Alternating returns give a finite, known volatility.
    return base + np.array([step if i % 2 else 0.0 for i in range(n)])


def market_with(event_closes_after, *, days=None, static=25, static_volume=1_000_000.0, volume=2_000_000.0):
    """60 sessions; the event symbol XYZ jumps on the report day index 40."""
    days = days or sessions("2021-03-01", 60)
    base = wiggle(len(days))
    xyz = base.copy()
    xyz[40:] = xyz[40:] * event_closes_after
    daily = {"XYZ": daily_frame(days, xyz, volume=volume), "SPY": daily_frame(days, wiggle(len(days), 400.0))}
    for i in range(static):
        daily[f"S{i:02d}"] = daily_frame(days, wiggle(len(days)), volume=static_volume)
    return MarketData(tuple(days), daily, tuple(f"S{i:02d}" for i in range(static))), days


def entry_with_window(start, end):
    window = ENTRY.window.model_copy(update={"decisions": (start, end), "recent_from": end})
    return ENTRY.model_copy(update={"window": window})


def test_surprise_requires_forecast_estimates_and_size():
    assert surprise_pct(report("A", date(2021, 4, 1), 1.10, 1.00), ENTRY.event) == pytest.approx(10.0)
    assert surprise_pct(report("A", date(2021, 4, 1), -0.90, -1.00), ENTRY.event) == pytest.approx(10.0)
    assert surprise_pct(report("A", date(2021, 4, 1), 0.10, 0.04), ENTRY.event) is None  # |forecast| < 0.05
    assert surprise_pct(report("A", date(2021, 4, 1), 1.0, 1.0, n=None), ENTRY.event) is None
    assert surprise_pct(report("A", date(2021, 4, 1), None, 1.0), ENTRY.event) is None


def test_decision_at_is_new_york_clock_in_utc():
    assert decision_at(date(2021, 4, 5), time(10, 35)) == datetime(2021, 4, 5, 14, 35, tzinfo=UTC)
    assert decision_at(date(2021, 1, 5), time(10, 35)) == datetime(2021, 1, 5, 15, 35, tzinfo=UTC)


def test_positive_surprise_and_jump_is_a_long_leg_event():
    market, days = market_with(1.10)
    entry = entry_with_window(days[0], days[-1])
    events, counts = build_events([report("XYZ", days[40])], market, entry)
    [event] = events.to_dict("records")
    assert event["leg"] == "LONG" and event["surprise_long"] and event["reaction_long"]
    assert event["session"] == days[42] and event["report_date"] == days[40]
    assert event["decision_at"] == decision_at(days[42], time(10, 35))
    assert event["z"] > 1.0 and event["surprise_pct"] == pytest.approx(10.0)
    assert counts["events"] == 1


def test_reaction_uses_adjacent_sessions_across_a_holiday_and_no_report_day_volatility():
    days = sessions("2021-03-01", 60)
    days.pop(41)  # D+1 is a later calendar day after a "holiday"
    days.append(days[-1] + pd.Timedelta(days=1))
    market, _ = market_with(1.0, days=days)
    xyz = market.daily["XYZ"].copy()
    xyz.loc[xyz.index[40], "Close"] *= 5.0  # a huge move ON the report day must not enter sigma
    daily = dict(market.daily, XYZ=xyz)
    market = MarketData(market.trading_days, daily, market.static_symbols)
    events, _ = build_events([report("XYZ", days[40])], market, entry_with_window(days[0], days[-1]))
    [event] = events.to_dict("records")
    closes = xyz["Close"].to_numpy()
    sigma = np.std(np.diff(np.log(closes[19:40])), ddof=1)
    spy = market.daily["SPY"]["Close"].to_numpy()
    expected = ((closes[41] / closes[39] - 1) - (spy[41] / spy[39] - 1)) / (sigma * np.sqrt(2))
    assert event["z"] == pytest.approx(expected)


def test_illiquid_and_cheap_names_are_counted_not_emitted():
    market, days = market_with(1.10, volume=1.0)
    entry = entry_with_window(days[0], days[-1])
    events, counts = build_events([report("XYZ", days[40])], market, entry)
    assert events.empty and counts["reasons"] == {"illiquid_volume": 1}
    cheap, _ = market_with(0.1)  # close falls to ~5
    events, counts = build_events([report("XYZ", days[40])], cheap, entry)
    assert events.empty and counts["reasons"] == {"illiquid_price": 1}


def test_skip_reasons():
    market, days = market_with(1.10)
    entry = entry_with_window(days[30], days[50])
    rows = [
        report("BRK/B", days[40]),
        report("NOPE", days[40]),
        report("XYZ", date(2021, 3, 6)),  # a Saturday
        report("XYZ", days[58]),  # D+2 beyond the calendar
        report("XYZ", days[5]),  # too early for sigma
        report("XYZ", days[52]),  # decision after the window
    ]
    events, counts = build_events(rows, market, entry)
    assert events.empty
    assert counts["reasons"] == {
        "unsupported_symbol": 1,
        "no_daily_bars": 1,
        "non_session_date": 1,
        "outside_calendar": 2,
        "outside_window": 1,
    }
    assert counts["reasons_by_year"]["non_session_date"] == {2021: 1}


def test_negative_surprise_and_drop_is_a_short_leg_event_and_mixed_signals_are_control_only():
    market, days = market_with(0.9)
    entry = entry_with_window(days[0], days[-1])
    events, _ = build_events([report("XYZ", days[40], eps=0.80, forecast=1.00)], market, entry)
    assert events.iloc[0]["leg"] == "SHORT"
    events, _ = build_events([report("XYZ", days[40], eps=1.20, forecast=1.00)], market, entry)
    assert events.iloc[0]["leg"] is None and events.iloc[0]["reaction_short"] and events.iloc[0]["surprise_long"]
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_pead_events.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** — `agentic_trader/research/apriori/pead_events.py`:

```python
"""PEAD event construction from daily bars: reaction, liquidity, legs and the control set.

For a report dated on session D (observed NYSE sessions only):

- reaction ``z = (r_i - r_SPY) / (sigma_i * sqrt(2))`` with ``r`` from close(D-1) to
  close(D+1) and ``sigma_i`` the sample standard deviation of the ``vol_window`` daily log
  returns ending D-1 -- never the report day itself;
- liquidity exactly as the live dynamic-universe gate computes it at the D+2 decision:
  ``median_dollar_volume(daily, as_of=D+1)`` against
  ``static_reference(static equities, percentile, as_of=D+1)`` and the ``as_of`` close
  against ``min_price`` (the live functions are reused, not reimplemented);
- ``ATR`` = the simple mean of the last ``atr_window`` true ranges through D+1.

Every liquid event with the needed bars is a control event; it is additionally a leg
event when its surprise and reaction agree. Anything else is counted by reason and year,
never silently dropped.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import PeadEntry, PeadEvent
from agentic_trader.screeners.dynamic_universe import median_dollar_volume, static_reference


__all__ = ["EVENT_COLUMNS", "SUPPORTED_SYMBOL", "MarketData", "build_events", "decision_at", "surprise_pct"]

SUPPORTED_SYMBOL = re.compile(r"^[A-Z]{1,5}$")

EVENT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "report_date",
    "session",
    "decision_at",
    "z",
    "surprise_pct",
    "surprise_pct_reported",
    "atr",
    "median_dollar_volume",
    "reference",
    "leg",
    "surprise_long",
    "surprise_short",
    "reaction_long",
    "reaction_short",
)


@dataclass(frozen=True)
class MarketData:
    trading_days: tuple[date, ...]  # sorted observed sessions
    daily: Mapping[str, pd.DataFrame]  # by symbol; tz-aware or UTC-naive index, OHLCV
    static_symbols: tuple[str, ...]  # static-universe equities for the liquidity reference


def surprise_pct(row: CalendarRow, event: PeadEvent) -> float | None:
    if row.eps is None or row.eps_forecast is None or row.n_estimates is None:
        return None
    if row.n_estimates < event.min_estimates or abs(row.eps_forecast) < event.min_abs_forecast:
        return None
    return 100.0 * (row.eps - row.eps_forecast) / abs(row.eps_forecast)


def decision_at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=ET_TZ).astimezone(UTC)


def _by_session(frame: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    keyed = frame.set_axis(index.tz_convert(ET_TZ).date)
    return keyed[~keyed.index.duplicated(keep="last")]


def _reaction_z(sym: pd.DataFrame, bench: pd.DataFrame, days: Sequence[date], i: int, vol_window: int) -> float | None:
    history = days[i - 1 - vol_window : i]  # vol_window + 1 closes ending D-1
    needed = [*history, days[i + 1]]
    if any(day not in sym.index for day in needed) or any(day not in bench.index for day in (days[i - 1], days[i + 1])):
        return None
    closes = sym.loc[list(history), "Close"].to_numpy(float)
    if np.any(~np.isfinite(closes)) or np.any(closes <= 0):
        return None
    sigma = float(np.std(np.diff(np.log(closes)), ddof=1))
    if not math.isfinite(sigma) or sigma <= 0:
        return None
    r_i = float(sym.at[days[i + 1], "Close"] / sym.at[days[i - 1], "Close"] - 1.0)
    r_m = float(bench.at[days[i + 1], "Close"] / bench.at[days[i - 1], "Close"] - 1.0)
    return (r_i - r_m) / (sigma * math.sqrt(2.0))


def _atr(sym: pd.DataFrame, days: Sequence[date], j: int, window: int) -> float | None:
    if j - window < 0:
        return None
    ranges = []
    for k in range(j - window + 1, j + 1):
        day, prev = days[k], days[k - 1]
        if day not in sym.index or prev not in sym.index:
            return None
        high, low = float(sym.at[day, "High"]), float(sym.at[day, "Low"])
        prev_close = float(sym.at[prev, "Close"])
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    value = float(np.mean(ranges))
    return value if math.isfinite(value) and value > 0 else None


def build_events(rows: Sequence[CalendarRow], market: MarketData, entry: PeadEntry) -> tuple[pd.DataFrame, dict]:
    days = market.trading_days
    position = {day: i for i, day in enumerate(days)}
    start, end = entry.window.decisions
    vol_window, atr_window = entry.event.vol_window, entry.trade.atr_window
    clock = entry.trade.decision_time()
    keyed: dict[str, pd.DataFrame] = {}
    references: dict[date, float | None] = {}
    static_daily = {s: market.daily[s] for s in market.static_symbols if s in market.daily}
    benchmark = entry.event.benchmark
    bench = _by_session(market.daily[benchmark])

    reasons: Counter[str] = Counter()
    by_year: dict[str, Counter[int]] = defaultdict(Counter)
    events: list[dict] = []

    def skip(reason: str, day: date) -> None:
        reasons[reason] += 1
        by_year[reason][day.year] += 1

    for row in rows:
        if not SUPPORTED_SYMBOL.fullmatch(row.symbol):
            skip("unsupported_symbol", row.date)
            continue
        raw = market.daily.get(row.symbol)
        if raw is None or raw.empty:
            skip("no_daily_bars", row.date)
            continue
        i = position.get(row.date)
        if i is None:
            skip("non_session_date", row.date)
            continue
        if i + 2 >= len(days) or i - 1 - vol_window < 0 or i + 1 - atr_window < 1:
            skip("outside_calendar", row.date)
            continue
        session, as_of = days[i + 2], days[i + 1]
        if not start <= session <= end:
            skip("outside_window", row.date)
            continue
        if as_of not in references:
            references[as_of] = static_reference(static_daily, entry.universe.static_percentile, as_of=as_of).value
        reference = references[as_of]
        if reference is None:
            skip("no_reference", row.date)
            continue
        dollar_volume = median_dollar_volume(raw, as_of)
        if dollar_volume is None:
            skip("short_history", row.date)
            continue
        sym = keyed.setdefault(row.symbol, _by_session(raw))
        if as_of not in sym.index:
            skip("no_reaction_bars", row.date)
            continue
        if float(sym.at[as_of, "Close"]) < entry.universe.min_price:
            skip("illiquid_price", row.date)
            continue
        if dollar_volume < reference:
            skip("illiquid_volume", row.date)
            continue
        z = _reaction_z(sym, bench, days, i, vol_window)
        if z is None:
            skip("no_reaction_bars", row.date)
            continue
        atr = _atr(sym, days, i + 1, atr_window)
        if atr is None:
            skip("no_atr", row.date)
            continue
        surprise = surprise_pct(row, entry.event)
        threshold, sigma = entry.event.surprise_pct, entry.event.reaction_sigma
        surprise_long = surprise is not None and surprise >= threshold
        surprise_short = surprise is not None and surprise <= -threshold
        reaction_long, reaction_short = z >= sigma, z <= -sigma
        leg = "LONG" if surprise_long and reaction_long else "SHORT" if surprise_short and reaction_short else None
        events.append(
            {
                "symbol": row.symbol,
                "report_date": row.date,
                "session": session,
                "decision_at": decision_at(session, clock),
                "z": z,
                "surprise_pct": surprise,
                "surprise_pct_reported": row.surprise_pct_reported,
                "atr": atr,
                "median_dollar_volume": dollar_volume,
                "reference": reference,
                "leg": leg,
                "surprise_long": surprise_long,
                "surprise_short": surprise_short,
                "reaction_long": reaction_long,
                "reaction_short": reaction_short,
            }
        )

    frame = pd.DataFrame(events, columns=list(EVENT_COLUMNS))
    frame["leg"] = frame["leg"].astype(object).where(frame["leg"].notna(), None)
    counts = {
        "rows": len(rows),
        "events": len(frame),
        "reasons": dict(reasons),
        "reasons_by_year": {reason: dict(years) for reason, years in by_year.items()},
    }
    return frame, counts
```

If a test expectation disagrees with the implementation, re-derive the fixture index arithmetic (report at index 40; D−1 = 39, D+1 = 41, D+2 = 42; σ uses closes 19..39) before changing either; the test encodes the spec.

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_pead_events.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**; controller commits `A priori: PEAD event construction with the live liquidity rule`.

---

### Task 5: Labelling, P1–P4 statistics and the study executor

**Files:**
- Create: `agentic_trader/research/apriori/pead_study.py`
- Test: `tests/research/apriori/test_pead_study.py`

**Interfaces:**
- Consumes: `build_events`, `MarketData`, `EVENT_COLUMNS` (Task 4); `LoadedEntry`, `PeadEntry` (Task 2); `CalendarAcquisition` (Task 3); `SetupLevels`, `label_bracket` (`research/setups/labels.py`); `REGULAR_SESSION_HOURS_NY` (`screeners/coverage.py`); `_stationary_index_draws`, `_finite_json` (`research/setups/study.py`); `save_json_report` (`storage/artifacts.py`).
- Produces:
  - `def decision_price(hourly: pd.DataFrame, when: datetime) -> float | None`
  - `def levels_for(direction: str, price: float, atr: float, entry: PeadEntry) -> SetupLevels | None`
  - `def label_events(events: pd.DataFrame, hourly: Mapping[str, pd.DataFrame], entry: PeadEntry) -> tuple[pd.DataFrame, dict]` — one row per (event, direction) with columns: all `EVENT_COLUMNS`, `direction`, `is_leg`, `hit`, `r`, `r_cost` (decision cost), `r_cost_{c:g}bps` for every configured cost, `hold_secondary_r_cost`, `holding_sessions`; plus counts `{"no_hourly_bars", "no_decision_price", "degenerate_levels", "immature"}`.
  - `def evaluate_leg(labels: pd.DataFrame, direction: str, entry: PeadEntry) -> dict` with keys `p1`, `p2`, `p3`, `p4`, `passes`.
  - `@dataclass(frozen=True) class PeadInputs: acquisition: CalendarAcquisition; rows: tuple[CalendarRow, ...]; duplicates: int; market: MarketData; hourly: Mapping[str, pd.DataFrame]; bar_failures: Mapping[str, str]`
  - `async def execute_pead_study(loaded: LoadedEntry, directory: Path, *, build: Callable[[], Awaitable[PeadInputs]], environment: dict) -> dict`

- [ ] **Step 1: Write the failing tests** — `tests/research/apriori/test_pead_study.py`:

```python
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.earnings_history import CalendarAcquisition
from agentic_trader.research.apriori.pead_events import MarketData, decision_at
from agentic_trader.research.apriori.pead_study import (
    PeadInputs,
    decision_price,
    evaluate_leg,
    execute_pead_study,
    label_events,
    levels_for,
)


ENTRY_PATH = Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json"
LOADED = load_pead_entry(ENTRY_PATH)
ENTRY = LOADED.entry


def hourly_bars(day: date, prices: list[float]) -> pd.DataFrame:
    """Regular-session hourly bars 09:00..15:00 New York for one day, one price each."""
    from agentic_trader.market.session import ET_TZ

    index = [datetime.combine(day, time(9 + k), tzinfo=ET_TZ).astimezone(UTC) for k in range(len(prices))]
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p, "Volume": 1.0}, index=pd.DatetimeIndex(index))


def test_decision_price_is_the_close_of_the_last_bar_ending_by_the_decision():
    day = date(2021, 4, 5)
    bars = hourly_bars(day, [100.0, 101.0, 102.0])
    assert decision_price(bars, decision_at(day, time(10, 35))) == 100.0  # the 09:00 bar ends 10:00
    assert decision_price(bars.iloc[1:], decision_at(day, time(10, 35))) is None  # no bar ending by 10:35
    earlier = hourly_bars(day - timedelta(days=1), [99.0] * 7)
    assert decision_price(earlier, decision_at(day, time(10, 35))) is None  # never a prior day's price


def test_levels_follow_atr_multiples_and_reject_degenerate_brackets():
    long = levels_for("LONG", 100.0, 2.0, ENTRY)
    assert (long.stop, long.target) == (96.0, 112.0)
    short = levels_for("SHORT", 100.0, 2.0, ENTRY)
    assert (short.stop, short.target) == (104.0, 88.0)
    assert levels_for("LONG", 3.0, 2.0, ENTRY) is None  # stop at or below zero


def _labels(values_by_session: dict[date, list[tuple[str, bool, float]]]) -> pd.DataFrame:
    rows = []
    for session, items in values_by_session.items():
        for direction, is_leg, r_cost in items:
            rows.append({"session": session, "direction": direction, "is_leg": is_leg, "r_cost": r_cost})
    return pd.DataFrame(rows)


def _sessions(n: int, start=date(2020, 1, 1)) -> list[date]:
    return [start + timedelta(days=k) for k in range(n)]


def test_evaluate_leg_passes_a_clear_positive_edge_over_control():
    rng = np.random.default_rng(0)
    data = {}
    for session in _sessions(400, date(2022, 6, 1)):
        data[session] = [("LONG", True, 0.4 + 0.1 * rng.standard_normal())] + [
            ("LONG", False, -0.1 + 0.1 * rng.standard_normal()) for _ in range(3)
        ]
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert result["p1"]["holds"] and result["p2"]["holds"] and result["p3"]["holds"] and result["p4"]["holds"]
    assert result["passes"] and result["p1"]["n"] == 400 and result["p4"]["n_recent"] >= 100


def test_evaluate_leg_fails_p3_when_recent_decisions_lose():
    data = {}
    for session in _sessions(700, date(2021, 6, 1)):
        edge = 0.5 if session < date(2023, 1, 1) else -0.2
        data[session] = [("LONG", True, edge), ("LONG", False, -0.3)]
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert result["p1"]["holds"] and not result["p3"]["holds"] and not result["passes"]


def test_evaluate_leg_fails_p3_on_an_outlier_driven_mean():
    data = {s: [("SHORT", True, -0.2), ("SHORT", False, -0.3)] for s in _sessions(500, date(2021, 1, 1))}
    first = next(iter(data))
    data[first] = [("SHORT", True, 5000.0), ("SHORT", False, -0.3)]
    result = evaluate_leg(_labels(data), "SHORT", ENTRY)
    assert result["p3"]["trimmed_mean"] < 0 and not result["p3"]["holds"] and not result["passes"]


def test_evaluate_leg_fails_p4_on_too_few_events():
    data = {s: [("LONG", True, 1.0), ("LONG", False, 0.0)] for s in _sessions(50, date(2023, 2, 1))}
    result = evaluate_leg(_labels(data), "LONG", ENTRY)
    assert not result["p4"]["holds"] and not result["passes"]


def _market_and_hourly():
    day = date(2021, 4, 5)
    # 30 flat sessions: neither bracket level is touched, so both directions time out at 20 sessions.
    frames = [hourly_bars(d.date(), [100.0] * 7) for d in pd.bdate_range(day, periods=30)]
    return day, {"XYZ": pd.concat(frames)}


def test_label_events_labels_each_event_both_ways_and_flags_leg_rows():
    day, hourly = _market_and_hourly()
    events = pd.DataFrame(
        [
            {
                "symbol": "XYZ",
                "report_date": day - timedelta(days=2),
                "session": day,
                "decision_at": decision_at(day, time(10, 35)),
                "z": 2.0,
                "surprise_pct": 10.0,
                "surprise_pct_reported": 10.0,
                "atr": 1.0,
                "median_dollar_volume": 1e8,
                "reference": 1e7,
                "leg": "LONG",
                "surprise_long": True,
                "surprise_short": False,
                "reaction_long": True,
                "reaction_short": False,
            },
            {
                "symbol": "GONE",
                "report_date": day - timedelta(days=2),
                "session": day,
                "decision_at": decision_at(day, time(10, 35)),
                "z": 0.1,
                "surprise_pct": None,
                "surprise_pct_reported": None,
                "atr": 1.0,
                "median_dollar_volume": 1e8,
                "reference": 1e7,
                "leg": None,
                "surprise_long": False,
                "surprise_short": False,
                "reaction_long": False,
                "reaction_short": False,
            },
        ]
    )
    labels, counts = label_events(events, hourly, ENTRY)
    assert counts["no_hourly_bars"] == 1
    assert sorted(zip(labels["direction"], labels["is_leg"])) == [("LONG", True), ("SHORT", False)]
    assert {"r_cost", "r_cost_0bps", "r_cost_5bps", "hold_secondary_r_cost", "hit"} <= set(labels.columns)
    assert set(labels["hit"]) == {"timeout"} and labels["r"].abs().max() < 0.51  # entry 100 vs decision price 100


async def test_execute_writes_manifest_before_building_and_records_failure(tmp_path):
    output = tmp_path / "run"

    async def build():
        assert (output / "manifest.json").exists() and (output / "protocol.json").exists()
        raise RuntimeError("provider down")

    result = await execute_pead_study(LOADED, output, build=build, environment={"revision": "abc"})
    assert result == {"status": "failed", "error": "RuntimeError: provider down"}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["sha256"] == LOADED.sha256 and manifest["authorizes_promotion"] is False
    assert json.loads((output / "result.json").read_text())["status"] == "failed"
    with pytest.raises(FileExistsError):
        await execute_pead_study(LOADED, output, build=build, environment={})


async def test_execute_fails_closed_on_a_gappy_calendar(tmp_path):
    acquisition = CalendarAcquisition(
        rows=(), requested_dates=100, fetched_dates=97, reused_dates=0, failed_dates=tuple(_sessions(3))
    )

    async def build():
        return PeadInputs(
            acquisition=acquisition,
            rows=(),
            duplicates=0,
            market=MarketData((), {}, ()),
            hourly={},
            bar_failures={},
        )

    result = await execute_pead_study(LOADED, tmp_path / "run", build=build, environment={})
    assert result["status"] == "failed" and "calendar" in result["error"]
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_pead_study.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** — `agentic_trader/research/apriori/pead_study.py`:

```python
"""PEAD labelling, the frozen P1-P4 pass rule and the study executor.

Each event is labelled twice (LONG and SHORT brackets) with the setup study's
``label_bracket``: the control set for a direction is every labelled event in that
direction, and leg rows (``is_leg``) are the subset whose surprise and reaction agree.
The decision price is the close of the last regular-session hourly bar that *ends* at or
before the 10:35 decision on the same New York date -- never a later or earlier-day price.

Statistics follow the short-suppression precedent (``research/setups/baserates.py``):
event-weighted means, a stationary bootstrap over whole decision sessions, ``ci90`` =
[5th, 95th] percentiles, a criterion holding when its lower bound is above zero, and
P2's paired draw resampling each session once for both means.

``execute_pead_study`` writes ``protocol.json`` and ``manifest.json`` before calling
``build`` (the only provider access), and records any failure as ``status: failed`` in
``result.json`` instead of raising.
"""

from __future__ import annotations

import asyncio
import gzip
import math
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import LoadedEntry, PeadEntry
from agentic_trader.research.apriori.earnings_history import CalendarAcquisition
from agentic_trader.research.apriori.pead_events import MarketData, build_events
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket
from agentic_trader.research.setups.study import _finite_json, _stationary_index_draws
from agentic_trader.screeners.coverage import REGULAR_SESSION_HOURS_NY
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "PeadInputs",
    "decision_price",
    "evaluate_leg",
    "execute_pead_study",
    "label_events",
    "levels_for",
]

_DIRECTIONS = ("LONG", "SHORT")
# Hourly bars are trimmed to this span around each decision before labelling; the
# secondary hold (60 sessions) fits well inside it.
_LABEL_SPAN = timedelta(days=130)


@dataclass(frozen=True)
class PeadInputs:
    acquisition: CalendarAcquisition
    rows: tuple[CalendarRow, ...]
    duplicates: int
    market: MarketData
    hourly: Mapping[str, pd.DataFrame]
    bar_failures: Mapping[str, str]  # symbol -> reason


def _utc_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(frame.index)
    return index.tz_localize("UTC") if index.tz is None else index


def decision_price(hourly: pd.DataFrame, when: datetime) -> float | None:
    if hourly is None or hourly.empty:
        return None
    index = _utc_index(hourly)
    local = index.tz_convert(ET_TZ)
    day = when.astimezone(ET_TZ).date()
    eligible = (
        (local.hour.isin(REGULAR_SESSION_HOURS_NY)) & (local.date == day) & (index + pd.Timedelta(hours=1) <= when)
    )
    if not eligible.any():
        return None
    value = float(hourly["Close"].to_numpy(float)[np.flatnonzero(eligible)[-1]])
    return value if math.isfinite(value) and value > 0 else None


def levels_for(direction: str, price: float, atr: float, entry: PeadEntry) -> SetupLevels | None:
    risk = entry.trade.stop_atr_multiple * atr
    reward = entry.trade.target_r * risk
    if direction == "LONG":
        stop, target = price - risk, price + reward
    else:
        stop, target = price + risk, price - reward
    if min(stop, target) <= 0:
        return None
    return SetupLevels(direction=direction, entry=price, stop=stop, target=target)


def _cost_column(cost: float) -> str:
    return f"r_cost_{cost:g}bps"


def label_events(
    events: pd.DataFrame, hourly: Mapping[str, pd.DataFrame], entry: PeadEntry
) -> tuple[pd.DataFrame, dict]:
    counts: Counter[str] = Counter()
    rows: list[dict] = []
    for event in events.to_dict("records"):
        bars = hourly.get(event["symbol"])
        if bars is None or bars.empty:
            counts["no_hourly_bars"] += 1
            continue
        when = event["decision_at"]
        index = _utc_index(bars)
        window = bars.loc[(index >= when - timedelta(days=1)) & (index <= when + _LABEL_SPAN)]
        price = decision_price(window, when)
        if price is None:
            counts["no_decision_price"] += 1
            continue
        for direction in _DIRECTIONS:
            levels = levels_for(direction, price, float(event["atr"]), entry)
            if levels is None:
                counts["degenerate_levels"] += 1
                continue
            costs = {
                _cost_column(cost): label_bracket(
                    levels, when, window, max_hold_sessions=entry.trade.max_hold_sessions, cost_bps_per_side=cost
                )
                for cost in entry.costs_bps_per_side
            }
            decisive = costs[_cost_column(entry.decision_cost_bps)]
            if decisive.hit == BracketHit.IMMATURE:
                counts["immature"] += 1
                continue
            secondary = label_bracket(
                levels,
                when,
                window,
                max_hold_sessions=entry.trade.secondary_hold_sessions,
                cost_bps_per_side=entry.decision_cost_bps,
            )
            rows.append(
                {
                    **event,
                    "direction": direction,
                    "is_leg": event["leg"] == direction,
                    "hit": str(decisive.hit),
                    "r": decisive.r,
                    "r_cost": decisive.r_cost,
                    **{column: outcome.r_cost for column, outcome in costs.items()},
                    "hold_secondary_r_cost": secondary.r_cost,
                    "holding_sessions": decisive.holding_sessions,
                }
            )
    return pd.DataFrame(rows), dict(counts)


# --- Statistics ----------------------------------------------------------------------------


def _ci90(boot: np.ndarray) -> list[float]:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return [float("nan"), float("nan")]
    return [float(np.percentile(finite, 5)), float(np.percentile(finite, 95))]


def _p_positive(boot: np.ndarray) -> float:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return float("nan")
    return (int(np.sum(finite <= 0.0)) + 1) / (finite.size + 1)


def _sums(frame: pd.DataFrame, sessions: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    grouped = frame.groupby("session")["r_cost"].agg(["sum", "count"]).reindex(sessions, fill_value=0)
    return grouped["sum"].to_numpy(float), grouped["count"].to_numpy(float)


def _weighted(sums: np.ndarray, counts: np.ndarray, rows: np.ndarray) -> float:
    n = counts[rows].sum()
    return float(sums[rows].sum() / n) if n > 0 else float("nan")


def _draws(n: int, entry: PeadEntry) -> np.ndarray:
    spec = entry.bootstrap
    return _stationary_index_draws(n, spec.block_mean, spec.draws, spec.seed)


def _mean_test(leg: pd.DataFrame, entry: PeadEntry) -> dict:
    sessions = pd.Index(sorted(leg["session"].unique()))
    sums, counts = _sums(leg, sessions)
    boot = np.array([_weighted(sums, counts, rows) for rows in _draws(len(sessions), entry)], dtype=float)
    ci90 = _ci90(boot)
    return {
        "n": int(counts.sum()),
        "n_sessions": len(sessions),
        "mean_r_cost": _weighted(sums, counts, np.arange(len(sessions))) if len(sessions) else float("nan"),
        "ci90": ci90,
        "p_one_sided": _p_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _paired_test(leg: pd.DataFrame, control: pd.DataFrame, entry: PeadEntry) -> dict:
    sessions = pd.Index(sorted(control["session"].unique()))
    leg_sums, leg_n = _sums(leg, sessions)
    ctl_sums, ctl_n = _sums(control, sessions)

    def diff(rows: np.ndarray) -> float:
        return _weighted(leg_sums, leg_n, rows) - _weighted(ctl_sums, ctl_n, rows)

    boot = np.array([diff(rows) for rows in _draws(len(sessions), entry)], dtype=float)
    ci90 = _ci90(boot)
    return {
        "n_leg": int(leg_n.sum()),
        "n_control": int(ctl_n.sum()),
        "n_sessions": len(sessions),
        "control_mean_r_cost": _weighted(ctl_sums, ctl_n, np.arange(len(sessions))) if len(sessions) else float("nan"),
        "mean_diff": diff(np.arange(len(sessions))) if len(sessions) else float("nan"),
        "ci90": ci90,
        "p_one_sided": _p_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _trimmed_mean(values: np.ndarray, fraction: float) -> float:
    ordered = np.sort(values[np.isfinite(values)])
    cut = int(math.floor(fraction * len(ordered)))
    kept = ordered[cut : len(ordered) - cut]
    return float(kept.mean()) if kept.size else float("nan")


def evaluate_leg(labels: pd.DataFrame, direction: str, entry: PeadEntry) -> dict:
    control = labels[labels["direction"] == direction]
    leg = control[control["is_leg"].astype(bool)]
    recent = leg[leg["session"] >= entry.window.recent_from]
    trimmed = _trimmed_mean(leg["r_cost"].to_numpy(float), entry.pass_rule.trim_fraction)
    recent_mean = float(recent["r_cost"].mean()) if len(recent) else float("nan")
    p1 = _mean_test(leg, entry) if len(leg) else {"n": 0, "holds": False}
    p2 = _paired_test(leg, control, entry) if len(leg) else {"n_leg": 0, "holds": False}
    p3 = {
        "trimmed_mean": trimmed,
        "recent_mean": recent_mean,
        "holds": bool(np.isfinite(trimmed) and trimmed > 0 and np.isfinite(recent_mean) and recent_mean > 0),
    }
    p4 = {
        "n": len(leg),
        "n_recent": len(recent),
        "holds": len(leg) >= entry.pass_rule.min_events and len(recent) >= entry.pass_rule.min_recent_events,
    }
    return {"p1": p1, "p2": p2, "p3": p3, "p4": p4, "passes": all(p["holds"] for p in (p1, p2, p3, p4))}


# --- Diagnostics (descriptive; never decisive) ---------------------------------------------


def _mean_n(frame: pd.DataFrame, column: str = "r_cost") -> dict:
    values = frame[column].dropna()
    return {"n": len(values), "mean_r_cost": float(values.mean()) if len(values) else float("nan")}


def _diagnostics(labels: pd.DataFrame, events: pd.DataFrame, entry: PeadEntry) -> dict:
    out: dict = {}
    for direction in _DIRECTIONS:
        side = labels[labels["direction"] == direction]
        leg = side[side["is_leg"].astype(bool)]
        flag = "long" if direction == "LONG" else "short"
        by_year = {int(year): _mean_n(group) for year, group in leg.groupby(pd.to_datetime(leg["session"]).dt.year)}
        terciles = {}
        if len(leg) >= 3:
            # Rank first so repeated values cannot produce duplicate bin edges.
            buckets = pd.qcut(leg["median_dollar_volume"].rank(method="first"), 3, labels=["low", "mid", "high"])
            terciles = {str(name): _mean_n(group) for name, group in leg.groupby(buckets, observed=True)}
        out[direction] = {
            "costs": {column: _mean_n(leg, column) for column in labels.columns if column.startswith("r_cost_")},
            "surprise_only": _mean_n(side[side[f"surprise_{flag}"].astype(bool)]),
            "reaction_only": _mean_n(side[side[f"reaction_{flag}"].astype(bool)]),
            "secondary_hold": _mean_n(leg, "hold_secondary_r_cost"),
            "by_year": by_year,
            "liquidity_terciles": terciles,
            "hits": {str(k): int(v) for k, v in leg["hit"].value_counts().items()},
            "events_per_session": {
                "max": int(leg.groupby("session").size().max()) if len(leg) else 0,
                "median": float(leg.groupby("session").size().median()) if len(leg) else 0.0,
                "p90": float(leg.groupby("session").size().quantile(0.9)) if len(leg) else 0.0,
            },
        }
    both = events.dropna(subset=["surprise_pct", "surprise_pct_reported"])
    both = both[(both["surprise_pct"] != 0) & (both["surprise_pct_reported"] != 0)]
    out["surprise_sign_disagreements"] = int(
        (np.sign(both["surprise_pct"]) != np.sign(both["surprise_pct_reported"])).sum()
    )
    return out


def _save_frame(frame: pd.DataFrame, path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as target:
        target.write(frame.to_csv(index=False).encode())


async def execute_pead_study(
    loaded: LoadedEntry,
    directory: Path,
    *,
    build: Callable[[], Awaitable[PeadInputs]],
    environment: dict,
) -> dict:
    entry = loaded.entry
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**entry.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "entry_id": entry.id,
            "version": entry.version,
            "sha256": loaded.sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        inputs = await build()
        acquisition = inputs.acquisition
        if acquisition.failed_fraction > entry.max_failed_calendar_fraction:
            raise ValueError(
                f"calendar acquisition failed for {len(acquisition.failed_dates)}/{acquisition.requested_dates} dates, "
                f"above the frozen {entry.max_failed_calendar_fraction:.0%} limit"
            )
        events, event_counts = await asyncio.to_thread(build_events, inputs.rows, inputs.market, entry)
        labels, label_counts = await asyncio.to_thread(label_events, events, inputs.hourly, entry)
        await asyncio.to_thread(_save_frame, events, directory / "events.csv.gz")
        await asyncio.to_thread(_save_frame, labels, directory / "labels.csv.gz")
        legs = {direction: evaluate_leg(labels, direction, entry) for direction in _DIRECTIONS}
        result = {
            "status": "completed",
            "legs": legs,
            "decisions": {d: ("eligible_for_probe" if legs[d]["passes"] else "failed") for d in _DIRECTIONS},
            "diagnostics": _diagnostics(labels, events, entry),
            "calendar": {
                "requested_dates": acquisition.requested_dates,
                "fetched_dates": acquisition.fetched_dates,
                "reused_dates": acquisition.reused_dates,
                "failed_dates": [d.isoformat() for d in acquisition.failed_dates],
                "rows": len(inputs.rows),
                "duplicates": inputs.duplicates,
            },
            "events": event_counts,
            "labels": label_counts,
            "bar_failures": dict(inputs.bar_failures),
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_pead_study.py -q`
Expected: PASS. If `_finite_json` rejects a value type (e.g. `numpy.bool_`), convert at the source with `bool(...)`/`float(...)` rather than weakening `_finite_json`.

- [ ] **Step 5: Stage**; controller commits `A priori: PEAD labelling, frozen P1-P4 pass rule and study executor`.

---

### Task 6: Bar acquisition runner and the `alpha apriori-study` command

**Files:**
- Modify: `agentic_trader/research/setups/runner.py` (`_fetch_cached` gains `chunk: timedelta = _FETCH_CHUNK`)
- Create: `agentic_trader/research/apriori/pead_runner.py`
- Modify: `agentic_trader/cli/commands/alpha.py` (new command)
- Test: `tests/research/apriori/test_pead_runner.py`, `tests/cli/test_cli_apriori.py`, and one added test in `tests/research/setups/test_runner.py`

**Interfaces:**
- Consumes: `acquire_calendar`, `CalendarPageStore`, `dedupe_rows`, `weekdays` (Task 3); `build_events`, `MarketData`, `SUPPORTED_SYMBOL` (Task 4); `PeadInputs`, `execute_pead_study` (Task 5); `load_pead_entry` (Task 2); `_fetch_cached`, `_claim_cache_range`, `_build_pacer`, `BarSource`, `CalendarSource` (`research/setups/runner.py`).
- Produces:
  - `async def build_pead_inputs(entry: PeadEntry, *, bars: BarSource, calendar: CalendarSource, cache_dir: Path, static_symbols: Sequence[str], pace: Callable[[], Awaitable[None]], calendar_transport: httpx.AsyncBaseTransport | None = None, calendar_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> PeadInputs`
  - CLI: `copilot alpha apriori-study PROTOCOL_PATH --output NEW_DIR [--cache DIR]`

Behaviour of `build_pead_inputs` (in order):
1. `trading_days` = `calendar.get_calendar_range(decisions[0] − 60 days, bars_through)` filtered to `is_trading_day`.
2. Calendar pages for `weekdays(decisions[0] − 7 days, decisions[1])` into `CalendarPageStore(cache_dir / "nasdaq")`, interval from the entry; rows de-duplicated with `dedupe_rows`.
3. `bars_start = decisions[0] − 60 days` (00:00 UTC), `bars_end = bars_through` (end of day UTC); `_claim_cache_range(cache_dir / "bars", bars_start, bars_end)`.
4. Daily bars, one chunk (`chunk=bars_end - bars_start + timedelta(days=1)`), for `SPY` (benchmark), every static symbol and every supported report symbol (sorted, unique); an exception or empty frame records `bar_failures[symbol] = "daily: ..."` and the symbol is left out of `daily`. Missing benchmark daily bars raise `ValueError` (the study cannot run).
5. `events, _ = build_events(rows, market, entry)`; hourly bars (one-year chunks via the default) only for symbols with at least one event, over `[min(decision session) − 7 days, min(max(decision session) + 130 days, bars_through)]` per symbol; failures recorded as `"hourly: ..."`.
6. Return `PeadInputs(acquisition, rows, duplicates, market, hourly, bar_failures)`.

Hourly bars go into a per-symbol cache under `cache_dir / "bars"` with the timeframe in the file key, as `_fetch_cached` already does (`{symbol}_{timeframe}`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/research/setups/test_runner.py` (reuse its fixtures/fakes; adapt names to what the file defines — it has a fake `BarSource` that records calls):

```python
async def test_fetch_cached_honours_a_single_chunk(tmp_path):
    calls = []

    class Bars:
        def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
            calls.append((start, end))
            index = pd.DatetimeIndex([start], tz="UTC")
            return pd.DataFrame(
                {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0]}, index=index
            )

    async def pace():
        return None

    start = datetime(2016, 1, 1, tzinfo=UTC)
    end = datetime(2026, 9, 1, tzinfo=UTC)
    await runner._fetch_cached("AAA", "1d", Bars(), tmp_path, start, end, "all", pace, chunk=end - start)
    assert calls == [(start, end)]
```

`tests/research/apriori/test_pead_runner.py`:

```python
import json
from datetime import UTC, date, datetime, time, timedelta

import httpx
import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_runner import build_pead_inputs
from pathlib import Path


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json").entry


def tiny_entry():
    window = ENTRY.window.model_copy(
        update={
            "decisions": (date(2021, 3, 15), date(2021, 4, 30)),
            "bars_through": date(2021, 6, 30),
            "recent_from": date(2021, 4, 1),
        }
    )
    return ENTRY.model_copy(update={"window": window})


class Calendar:
    async def get_calendar_range(self, start, end):
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(date=day, is_trading_day=day.weekday() < 5, is_early_close=False))
            day += timedelta(days=1)
        return days


class Bars:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, start, end, adjustment))
        if symbol in self.missing:
            raise RuntimeError("unknown symbol")
        if timeframe == "1d":
            days = pd.bdate_range(start.date(), end.date())
            index = pd.DatetimeIndex([datetime.combine(d.date(), time(5), tzinfo=UTC) for d in days])
        else:
            stamps = [
                datetime.combine(d.date(), time(h), tzinfo=ET_TZ).astimezone(UTC)
                for d in pd.bdate_range(start.date(), end.date())
                for h in range(9, 16)
            ]
            index = pd.DatetimeIndex(stamps)
        n = len(index)
        close = 50.0 + np.arange(n) % 2 * 0.2
        return pd.DataFrame(
            {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": np.full(n, 1e6)},
            index=index,
        )


def calendar_transport(symbols_by_day):
    def handler(request):
        day = request.url.params["date"]
        rows = [
            {"symbol": s, "eps": "$1.10", "epsForecast": "$1.00", "noOfEsts": "4"} for s in symbols_by_day.get(day, [])
        ]
        return httpx.Response(200, content=json.dumps({"data": {"rows": rows}}).encode())

    return httpx.MockTransport(handler)


async def _no_sleep(_s):
    return None


async def _pace():
    return None


async def test_builds_inputs_and_records_bar_failures(tmp_path):
    bars = Bars(missing={"GONE"})
    inputs = await build_pead_inputs(
        tiny_entry(),
        bars=bars,
        calendar=Calendar(),
        cache_dir=tmp_path,
        static_symbols=[f"S{i:02d}" for i in range(25)],
        pace=_pace,
        calendar_transport=calendar_transport({"2021-04-05": ["XYZ", "GONE", "BRK/B"]}),
        calendar_sleep=_no_sleep,
    )
    assert inputs.acquisition.failed_dates == ()
    assert {row.symbol for row in inputs.rows} == {"XYZ", "GONE", "BRK/B"}
    assert "SPY" in inputs.market.daily and "XYZ" in inputs.market.daily
    assert inputs.bar_failures["GONE"].startswith("daily:")
    assert not any(call[0] == "BRK/B" for call in bars.calls)  # unsupported symbols are never fetched
    daily_calls = [c for c in bars.calls if c[1] == "1d" and c[0] == "XYZ"]
    assert len(daily_calls) == 1  # one chunk for daily bars
    assert all(call[4] == "all" for call in bars.calls)


async def test_rerun_reuses_pages_and_bars(tmp_path):
    kwargs = dict(
        calendar=Calendar(),
        cache_dir=tmp_path,
        static_symbols=[f"S{i:02d}" for i in range(25)],
        pace=_pace,
        calendar_sleep=_no_sleep,
    )
    first = Bars()
    await build_pead_inputs(
        tiny_entry(), bars=first, calendar_transport=calendar_transport({"2021-04-05": ["XYZ"]}), **kwargs
    )

    def refuse(request):
        raise AssertionError("calendar page re-fetched")

    second = Bars()
    inputs = await build_pead_inputs(
        tiny_entry(), bars=second, calendar_transport=httpx.MockTransport(refuse), **kwargs
    )
    assert second.calls == [] and inputs.acquisition.fetched_dates == 0
```

`tests/cli/test_cli_apriori.py`:

```python
import json
from pathlib import Path

from click.testing import CliRunner

from agentic_trader.cli.main import cli


ENTRY = Path(__file__).resolve().parents[2] / "config/research/apriori/pead-v1.json"


def test_apriori_study_refuses_an_existing_output_directory(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must refuse before constructing services")

    for name in ("load_config", "AlpacaDataProvider"):
        monkeypatch.setattr(f"agentic_trader.cli.commands.alpha.{name}", forbidden)
    output = tmp_path / "run"
    output.mkdir()
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(ENTRY), "--output", str(output)])
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def test_apriori_study_writes_the_manifest_before_provider_access(tmp_path, monkeypatch):
    output = tmp_path / "run"
    seen = {}

    async def fake_build(entry, **kwargs):
        seen["manifest"] = (output / "manifest.json").exists()
        raise RuntimeError("stop after the manifest")

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.build_pead_inputs", fake_build)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha._apriori_clients", _fake_clients)
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(ENTRY), "--output", str(output)])
    assert seen == {"manifest": True}
    assert result.exit_code != 0
    assert json.loads((output / "result.json").read_text())["status"] == "failed"


class _FakeClients:
    def __init__(self):
        self.bars = object()
        self.calendar = object()
        self.static_symbols = ["S00"]
        self.pace = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_clients():
    return _FakeClients()
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_pead_runner.py tests/cli/test_cli_apriori.py tests/research/setups/test_runner.py -q`
Expected: FAIL (missing module, missing `chunk` parameter, unknown command).

- [ ] **Step 3: Implement**

`runner.py`: change the signature to `async def _fetch_cached(symbol, timeframe, bars, cache_dir, start, end, adjustment, pace, *, chunk: timedelta = _FETCH_CHUNK) -> pd.DataFrame:` and use `chunk` in place of `_FETCH_CHUNK` inside the loop. No other change; existing callers keep one-year chunks.

`agentic_trader/research/apriori/pead_runner.py`:

```python
"""Provider access for the PEAD study: trading calendar, Nasdaq pages and SIP bars.

Called only from inside ``execute_pead_study``'s ``build`` callable, after the manifest
exists. Daily bars are fetched once per symbol over the whole window in a single request
(well under one page); hourly bars only for symbols with at least one liquid event, over
that symbol's event span, in the setup study's one-year chunks. Both reuse the setup
study's immutable ``.npz`` cache with its range claim, so a rerun with ``--cache`` never
re-fetches. A symbol whose bars fail is recorded in ``bar_failures`` and its events are
counted as missing, never silently dropped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import httpx
import pandas as pd

from agentic_trader.research.apriori.catalog import PeadEntry
from agentic_trader.research.apriori.earnings_history import CalendarPageStore, acquire_calendar, dedupe_rows, weekdays
from agentic_trader.research.apriori.pead_events import SUPPORTED_SYMBOL, MarketData, build_events
from agentic_trader.research.apriori.pead_study import PeadInputs
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range, _fetch_cached


__all__ = ["build_pead_inputs"]

_CALENDAR_PAD = timedelta(days=60)
_REPORT_PAD = timedelta(days=7)
_HOURLY_TAIL = timedelta(days=130)


async def _bars_or_failure(
    symbol: str,
    timeframe: str,
    bars: BarSource,
    cache_dir: Path,
    start: datetime,
    end: datetime,
    pace: Callable[[], Awaitable[None]],
    **kwargs,
) -> tuple[pd.DataFrame | None, str | None]:
    try:
        frame = await _fetch_cached(symbol, timeframe, bars, cache_dir, start, end, "all", pace, **kwargs)
    except Exception as exc:
        return None, f"{'daily' if timeframe == '1d' else 'hourly'}: {type(exc).__name__}: {exc}"
    if frame.empty:
        return None, f"{'daily' if timeframe == '1d' else 'hourly'}: empty"
    return frame, None


async def build_pead_inputs(
    entry: PeadEntry,
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    static_symbols: Sequence[str],
    pace: Callable[[], Awaitable[None]],
    calendar_transport: httpx.AsyncBaseTransport | None = None,
    calendar_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> PeadInputs:
    first, last = entry.window.decisions
    bars_through = entry.window.bars_through
    days = await calendar.get_calendar_range(first - _CALENDAR_PAD, bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))

    acquisition = await acquire_calendar(
        weekdays(first - _REPORT_PAD, last),
        CalendarPageStore(cache_dir / "nasdaq"),
        interval_seconds=entry.calendar_request_interval_seconds,
        transport=calendar_transport,
        sleep=calendar_sleep,
    )
    rows, duplicates = dedupe_rows(acquisition.rows)

    bar_dir = cache_dir / "bars"
    start = datetime.combine(first - _CALENDAR_PAD, time.min, tzinfo=UTC)
    end = datetime.combine(bars_through, time.max, tzinfo=UTC)
    _claim_cache_range(bar_dir, start, end)

    benchmark = entry.event.benchmark
    symbols = sorted({benchmark, *static_symbols, *(r.symbol for r in rows if SUPPORTED_SYMBOL.fullmatch(r.symbol))})
    daily: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        frame, failure = await _bars_or_failure(symbol, "1d", bars, bar_dir, start, end, pace, chunk=end - start)
        if frame is None:
            failures[symbol] = failure or "daily: unavailable"
        else:
            daily[symbol] = frame
    if benchmark not in daily:
        raise ValueError(f"benchmark {benchmark} has no daily bars: {failures.get(benchmark)}")

    market = MarketData(trading_days, daily, tuple(s for s in static_symbols if s in daily))
    events, _ = await asyncio.to_thread(build_events, rows, market, entry)
    hourly: dict[str, pd.DataFrame] = {}
    for symbol, group in events.groupby("symbol"):
        span_start = datetime.combine(min(group["session"]) - _REPORT_PAD, time.min, tzinfo=UTC)
        span_end = min(datetime.combine(max(group["session"]), time.max, tzinfo=UTC) + _HOURLY_TAIL, end)
        frame, failure = await _bars_or_failure(str(symbol), "1h", bars, bar_dir, span_start, span_end, pace)
        if frame is None:
            failures[str(symbol)] = failure or "hourly: unavailable"
        else:
            hourly[str(symbol)] = frame
    return PeadInputs(
        acquisition=acquisition,
        rows=tuple(rows),
        duplicates=duplicates,
        market=market,
        hourly=hourly,
        bar_failures=failures,
    )
```

Note: `_fetch_cached` keys the cache by `{symbol}_{timeframe}` only, so a symbol's hourly span is fixed by its first fetch; a rerun with the same entry and cache produces the same spans.

`cli/commands/alpha.py` — add imports:

```python
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_runner import build_pead_inputs
from agentic_trader.research.apriori.pead_study import execute_pead_study
from agentic_trader.research.setups.runner import _build_pacer
```

and the command (after `setup-baserates`):

```python
class _AprioriClients:
    """Alpaca calendar and SIP data clients for the a priori study; sessions closed on exit."""

    def __init__(self):
        self._stack = ExitStack()

    def __enter__(self) -> _AprioriClients:
        config = load_config()
        calendar_client = BoundedTradingClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
            paper=config.alpaca_paper,
            request_timeout=config.market_data.timeout_seconds,
        )
        self._stack.callback(calendar_client._session.close)
        data_client = BoundedStockDataClient(
            config.alpaca_api_key, config.alpaca_api_secret, request_timeout=config.market_data.timeout_seconds
        )
        self._stack.callback(data_client._session.close)
        # No bar evidence store: the study's cache files and digests are its bar artifact
        # (raw evidence for thousands of symbols would be many gigabytes).
        self.bars = AlpacaDataProvider(stock_client=data_client, feed="sip")
        self.calendar = AlpacaCalendarProvider(trading_client=calendar_client)
        self.static_symbols = sorted(
            contract
            for contract, info in config.contracts.items()
            if normalize_asset_class(str(info.asset_class)) == normalize_asset_class(AssetClass.EQUITY)
        )
        self.pace = _build_pacer(config)
        return self

    def __exit__(self, *exc) -> bool:
        self._stack.close()
        return False


def _apriori_clients() -> _AprioriClients:
    return _AprioriClients()


@alpha_group.command("apriori-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option(
    "--cache", type=click.Path(path_type=Path), default=None, help="Page/bar cache directory; defaults to OUTPUT/raw"
)
@coro
async def alpha_apriori_study_cmd(protocol_path, output, cache):
    """Run a frozen a priori catalog study (research only; grants no credit)."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded = await asyncio.to_thread(load_pead_entry, protocol_path)
    cache_dir = cache if cache is not None else output / "raw"
    environment = await asyncio.to_thread(research_environment)

    with _apriori_clients() as clients:

        async def build():
            return await build_pead_inputs(
                loaded.entry,
                bars=clients.bars,
                calendar=clients.calendar,
                cache_dir=cache_dir,
                static_symbols=clients.static_symbols,
                pace=clients.pace,
            )

        result = await execute_pead_study(loaded, output, build=build, environment=environment)
    click.echo(json.dumps({k: result.get(k) for k in ("status", "decisions", "error")}, indent=2, default=str))
    if result.get("status") == "failed":
        raise click.ClickException("A priori study failed; see result.json for the reason")
```

Check the imports this needs already exist in `alpha.py` (`ExitStack`, `AlpacaCalendarProvider`, `AlpacaDataProvider`, `BoundedTradingClient`, `BoundedStockDataClient`, `load_config`, `coro`, `research_environment`); add `normalize_asset_class` and `AssetClass` from where `copilot.py` imports them if they are not already imported. The CLI test patches `_apriori_clients` so it never reads configuration; with the default cache `output/raw`, `execute_pead_study` creates `output` itself (`exist_ok=False`), and the cache directories are created inside it afterwards by `build`.

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori tests/cli/test_cli_apriori.py tests/research/setups -q`
Expected: PASS.

- [ ] **Step 5: Stage**; controller commits `A priori: SIP bar acquisition and the alpha apriori-study command`.

---

### Task 7: Documentation and full verification

**Files:**
- Create: `docs/apriori-alphas.md`
- Modify: `docs/alpha-roadmap.md`, `CLAUDE.md`

- [ ] **Step 1: Write `docs/apriori-alphas.md`** — the catalog index:

```markdown
# A priori alpha catalog

An a priori alpha is a literature-documented anomaly tested as **one frozen hypothesis
per leg**, pooled over many names, before it may produce any card. It sidesteps the
mining funnel's multiple-testing problem (thousands of candidate formulas, few trades per
symbol) by spending one confirmatory test per leg.

Rules:

- Each entry is a JSON file under `config/research/apriori/`, committed before any data
  is read. Its SHA-256 goes into every run manifest. A changed file is a new version
  with a new, non-overlapping window — never an edit.
- Studies are research only: no registry, trial, shadow or promotion credit; no database,
  broker or Telegram access.
- A leg that passes becomes eligible for a live, capped paper probe, which needs its own
  spec (Part 2). A failed leg stays off.

Run: `copilot alpha apriori-study config/research/apriori/<entry>.json --output NEW_DIR [--cache DIR]`.

| Entry | Version | Hypothesis | Long | Short | Result |
| --- | --- | --- | --- | --- | --- |
| `pead` | 1 | Post-earnings drift when EPS surprise and price reaction agree | pending | pending | [spec](superpowers/specs/2026-09-25-apriori-pead-study-design.md) |
```

- [ ] **Step 2: Update `docs/alpha-roadmap.md`** — in the current-priorities section, add a short "A priori alpha catalog" item (PEAD Part 1 study, link to `docs/apriori-alphas.md`; Part 2 live lane only for a passing leg) and a later-work item:

```markdown
- **Later: overcome sparse per-symbol trade histories in alpha mining** (operator,
  2026-09-25). The funnel qualifies per symbol, so each hypothesis sees few trades and
  planted-signal power is 0/64. Research how quant practice handles this — pooled or
  cross-sectional panel estimation, hierarchical/shrinkage estimators, meta-labeling,
  event pooling, cross-asset transfer, synthetic or bootstrapped paths — then design ways
  to unblock the mining funnel. Research online first; no build until reviewed.
```

- [ ] **Step 3: Update `CLAUDE.md`** — after the matched-panel paragraph, add:

```markdown
The [a priori alpha catalog](docs/apriori-alphas.md) tests literature anomalies as one
frozen hypothesis per leg (`config/research/apriori/*.json`, SHA-256 in each manifest;
a change is a new version). `alpha apriori-study` is research only: manifest before any
provider access, no DB/registry/broker/Telegram, no promotion credit. A passing leg only
becomes eligible for a separately specified capped paper probe.
```

- [ ] **Step 4: Full verification**

Run: `env -u VIRTUAL_ENV uv run pytest -q` — Expected: all pass.
Stage all changed files, then run `env -u VIRTUAL_ENV uv run pre-commit run --all-files` — Expected: all hooks pass (re-stage after formatter fixes and re-run).

- [ ] **Step 5: Stage**; controller commits `Docs: a priori alpha catalog index, roadmap and handoff notes`.

---

### Task 8 (controller): Run the study, record the result, open the PR

Not dispatched to an implementer; the controller runs it from the worktree after Tasks 1–7 are committed and reviewed.

- [ ] **Step 1: Run** (≈4–5 hours; background, tracked):
  `env -u VIRTUAL_ENV uv run copilot alpha apriori-study config/research/apriori/pead-v1.json --output ~/agentic-trader-research/apriori-pead-v1-<YYYYMMDD> --cache ~/agentic-trader-research/apriori-pead-cache`
  Credentials come from the worktree's environment (`load_config()`); the run makes read-only market-data and Nasdaq GETs only. If it fails, rerun into a new `--output` with the same `--cache`.
- [ ] **Step 2: Write `docs/apriori-pead-<run date>.md`** in the style of `docs/setup-baserates-short-2026-09-23.md`: result line per leg; protocol (entry SHA-256, window, method); scale (calendar dates fetched/failed, rows, duplicates, events, reasons by kind, bar failures); a P1–P4 table per leg with estimates and ci90; control means; diagnostics (costs, surprise-only, reaction-only, 60-session hold, by year, liquidity terciles, hits, events per session, surprise sign disagreements); caveats (survivorship, D+2 entry, non-point-in-time EPS, ticker changes, no sector-relative reaction); decision per leg; artifact path.
- [ ] **Step 3: Update the catalog row** in `docs/apriori-alphas.md` (Long/Short status: `eligible` or `failed`; link the result doc) and the roadmap item's outcome line.
- [ ] **Step 4:** Commit, push `research/apriori-pead`, open the PR (study, code and docs together), wait for CI, merge. No daemon restart is needed (no runtime path changed); run `scripts/verify_runtime.py` against the installed daemon only if the installed checkout is updated.
