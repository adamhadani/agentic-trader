# A Priori PEAD Paper Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the passing pead v2 LONG leg into capped, expiring paper-probe Telegram cards that follow the study's frozen trade rule: 2×ATR14 stop, 3R target, no trailing, time exit at 15:45 New York on session 20.

**Architecture:** The plan adds:
- a new execution-policy kind (`apriori_bracket_v1`) with a session-counted lifetime (`session_count_v1`), consumed by the existing lifetime and close services;
- a catalog enrolment in the existing registry `probe` list, governed by the unchanged `probe_block_reason`;
- a live event source that reuses the study's `build_events`;
- an `EarningsDriftService` whose candidates join the 10:35 suggestion scan with their own budget.

Measurement reuses the study's labeller.

**Tech Stack:** Python 3.14, `uv`, async SQLAlchemy (SQLite and PostgreSQL), alpaca-py, httpx, pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-apriori-pead-probe-design.md`

## Global Constraints

- **Worktree:** `/Users/adamhadani/Development/agentic-trader-peadprobe`, branch `feat/apriori-pead-probe`. Never read-write or run anything in `/Users/adamhadani/Development/agentic-trader` (the live installed checkout).
- **Commands:** run them as `env -u VIRTUAL_ENV uv run …`.
- **Schema:** no new migration; the schema head stays `008_alpha_pipeline`.
- **Serialization:** existing `AlphaExecutionPolicy`, `TimedAlphaExecutionPolicy` and `TradeLifetimePolicy` serialization, and every existing version hash, must stay byte-identical.
- **Probe liveness:** `probe_block_reason` (`storage/probe_state.py`) stays the single probe-liveness rule. Do not change its body.
- **Broker writes:** POST, PATCH and DELETE are never retried automatically.
- **Budgets:** card budgets are derived from recorded signals, never stored in a counter.
- **Scan scope:** never widen the intraday job. The dynamic universe is unchanged.
- **Tests:** they use only guarded `test_*` PostgreSQL databases and blocked network I/O. `--run-postgres` uses `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`.
- **LLM prompt:** no prompt or template text changes (`USER_EVALUATION_TEMPLATE`, `build_system_prompt` stay untouched). The event facts reach the LLM only through `candidate.trigger_detail`, which the template already carries.
- **Frozen values (verbatim from the spec):**
  - stop 2×ATR14, target 3R, no structural anchor, no trailing;
  - `session_count_v1`: `resting_seconds` 57,600, `holding_sessions` 20, `close_time_et` "15:45"; the fill session counts as session 1; early close means 15 minutes before that session's close;
  - at most 1 drift card per session, outside the native budget; tie-break by largest reaction z, then symbol;
  - only the scheduled scan whose time equals the entry's `decision_time_et` (10:35) produces drift cards;
  - catalog version ID `apriori:pead:v2:long:<first 16 hex of the entry file SHA-256>`;
  - `alpha_id` `pead_long`.
- **Commits:** implementers stage their files with explicit paths (`git add <paths>`), run `env -u VIRTUAL_ENV uv run pre-commit run --all-files`, and stop. The controller commits.
- **Rulings on the spec, made in this plan:**
  - The live-source parity test uses a synthetic calendar and bar fixture rather than committed provider data. It is the same check without putting provider pages in Git.
  - The drift journal stores parsed calendar rows plus the page body's SHA-256, not the raw body.

## Review Focus

1. **Old readers meet a catalog version** (`alpha list`, `alpha inspect`, `AlphaRepository.versions()`, `snapshot()`, `_symbol_owner_conflict`, promotion). None may crash, and the catalog version never becomes a formulaic strategy. Tests are in Task 3.
2. **A drift event on a symbol the desk already holds or an alpha owns.** It is skipped with a journaled reason, never carded twice. Tests are in Task 5.
3. **A restart mid-session.** The drift budget is re-derived from recorded signals (`strategy == "pead_long"`, New York day), and native budget arithmetic excludes drift signals. Tests are in Task 6.
4. **Incomplete Nasdaq or SIP evidence:** an empty page on a session day, `rCode != 200`, SPY missing its D+1 bar, or no static reference. The session is unavailable with one debounced notice, not a partial event set. Tests are in Tasks 4 and 6.
5. **A holding window crossing Thanksgiving and an early close.** The deadline is exact, and a short or missing calendar gives REVIEW, never a guessed date. Tests are in Tasks 1 and 2.

---

### Task 1: `apriori_bracket_v1` execution policy and `session_count_v1` lifetime

**Files:**
- Modify: `agentic_trader/execution/lifetime_policy.py`
- Modify: `agentic_trader/research/alpha/strategy.py:31-145` (policy classes, `execution_policy_from_dict`, `bracket_prices`, `trailing_price`)
- Modify: `agentic_trader/research/apriori/catalog.py` (add `pead_execution_policy`)
- Test: `tests/execution/test_session_lifetime.py` (new)
- Test: `tests/research/test_apriori_bracket_policy.py` (new)

**Interfaces:**
- Produces, in `agentic_trader/execution/lifetime_policy.py`:
  - `SESSION_LIFETIME_VERSION = "session_count_v1"`
  - `MAX_HOLDING_SESSIONS = 60`
  - `SESSION_CLOSE_BUFFER = timedelta(minutes=15)`
  - `class SessionEvidenceError(ValueError)`
  - `@dataclass(frozen=True) class SessionLifetimePolicy(resting_seconds: int, holding_sessions: int, close_time_et: str = "15:45", version: str = SESSION_LIFETIME_VERSION)`, with:
    - `.entry_deadline(submitted_at) -> datetime`
    - `.holding_deadline(filled_at: datetime, sessions: Sequence[MarketCalendarDay]) -> datetime`, which raises `SessionEvidenceError`
  - `LifetimePolicy = TradeLifetimePolicy | SessionLifetimePolicy`
  - `lifetime_from_dict(document: Mapping[str, Any]) -> LifetimePolicy`
- Produces, in `agentic_trader/research/alpha/strategy.py`:
  - `APRIORI_BRACKET_KIND = "apriori_bracket_v1"`
  - `@dataclass(frozen=True, kw_only=True) class AprioriBracketPolicy(stop_atr: float, atr_window: int, reward_risk: float, lifetime: SessionLifetimePolicy, tick_size: float = 0.01, kind: str = APRIORI_BRACKET_KIND)`, with `.to_dict()`
  - `policy_trails(document: Mapping | None) -> bool`
  - `execution_policy_from_dict` now returns `AlphaExecutionPolicy | AprioriBracketPolicy`
- Produces, in `agentic_trader/research/apriori/catalog.py`: `pead_execution_policy(entry: PeadEntry) -> AprioriBracketPolicy`

- [ ] **Step 1: Write the failing lifetime tests** in `tests/execution/test_session_lifetime.py`:

```python
"""session_count_v1: the fill session is session 1; exit at 15:45 NY (or close - 15 min) on session N."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    SessionEvidenceError,
    SessionLifetimePolicy,
    TradeLifetimePolicy,
    lifetime_from_dict,
)
from agentic_trader.market.session import MarketCalendarDay

NY = ZoneInfo("America/New_York")
THANKSGIVING = date(2026, 11, 26)
EARLY = {date(2026, 11, 27): time(13, 0)}


def nyse_days(start: date, end: date) -> list[MarketCalendarDay]:
    days, day = [], start
    while day <= end:
        trading = day.weekday() < 5 and day != THANKSGIVING
        close = EARLY.get(day, time(16, 0))
        days.append(
            MarketCalendarDay(
                date=day,
                is_trading_day=trading,
                is_early_close=day in EARLY,
                open_time=time(9, 30) if trading else None,
                close_time=close if trading else None,
            )
        )
        day += timedelta(days=1)
    return days


POLICY = SessionLifetimePolicy(resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS, holding_sessions=20)


def et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=NY)


def test_session_twenty_across_thanksgiving_exits_at_1545():
    # Nov 2 (1) .. Nov 25 (18), Nov 27 early close (19), Nov 30 (20).
    deadline = POLICY.holding_deadline(et(date(2026, 11, 2), 10, 40), nyse_days(date(2026, 11, 2), date(2026, 12, 15)))
    assert deadline == et(date(2026, 11, 30), 15, 45).astimezone(UTC)


def test_early_close_session_twenty_exits_fifteen_minutes_before_close():
    # Oct 30 (1), Nov 2-6 (6), 9-13 (11), 16-20 (16), 23-25 (19), Nov 27 (20, closes 13:00).
    deadline = POLICY.holding_deadline(et(date(2026, 10, 30), 11, 0), nyse_days(date(2026, 10, 30), date(2026, 12, 15)))
    assert deadline == et(date(2026, 11, 27), 12, 45).astimezone(UTC)


def test_a_short_calendar_is_evidence_error_never_a_guess():
    with pytest.raises(SessionEvidenceError, match="session_calendar_short"):
        POLICY.holding_deadline(et(date(2026, 11, 2), 10, 40), nyse_days(date(2026, 11, 2), date(2026, 11, 20)))


@pytest.mark.parametrize("when", [et(date(2026, 11, 2), 8, 0), et(date(2026, 11, 2), 16, 30), et(THANKSGIVING, 11, 0)])
def test_a_fill_outside_a_regular_session_is_evidence_error(when):
    with pytest.raises(SessionEvidenceError, match="fill_outside_regular_session"):
        POLICY.holding_deadline(when, nyse_days(date(2026, 10, 26), date(2026, 12, 31)))


def test_entry_deadline_is_one_session_of_elapsed_seconds():
    submitted = et(date(2026, 11, 2), 10, 40)
    assert POLICY.entry_deadline(submitted) == submitted.astimezone(UTC) + timedelta(seconds=57_600)


@pytest.mark.parametrize(
    "fields",
    [
        {"resting_seconds": 0, "holding_sessions": 20},
        {"resting_seconds": 57_600, "holding_sessions": 0},
        {"resting_seconds": 57_600, "holding_sessions": 61},
        {"resting_seconds": 57_600, "holding_sessions": True},
        {"resting_seconds": 57_600, "holding_sessions": 20, "close_time_et": "25:00"},
        {"resting_seconds": 57_600, "holding_sessions": 20, "version": "elapsed_utc_v2"},
    ],
)
def test_invalid_session_policies_are_refused(fields):
    with pytest.raises(ValueError):
        SessionLifetimePolicy(**fields)


def test_lifetime_from_dict_dispatches_on_version_and_keeps_old_documents():
    old = {"resting_seconds": 120, "holding_seconds": 180, "version": "elapsed_utc_v1"}
    assert lifetime_from_dict(old) == TradeLifetimePolicy(**old)
    new = {"resting_seconds": 57_600, "holding_sessions": 20, "close_time_et": "15:45", "version": "session_count_v1"}
    assert lifetime_from_dict(new) == SessionLifetimePolicy(**new)
```

- [ ] **Step 2: Write the failing policy tests** in `tests/research/test_apriori_bracket_policy.py`:

```python
import json
import math
from pathlib import Path

import pytest

from agentic_trader.execution.lifetime_policy import SessionLifetimePolicy
from agentic_trader.research.alpha.strategy import (
    APRIORI_BRACKET_KIND,
    AlphaExecutionPolicy,
    AprioriBracketPolicy,
    TimedAlphaExecutionPolicy,
    bracket_prices,
    execution_policy_from_dict,
    policy_trails,
    session_entry_policy,
    trailing_price,
)
from agentic_trader.research.apriori.catalog import load_pead_entry, pead_execution_policy
from agentic_trader.research.apriori.pead_study import levels_for

ENTRY = load_pead_entry(Path("config/research/apriori/pead-v2.json")).entry
POLICY = pead_execution_policy(ENTRY)


def test_pead_policy_carries_the_frozen_trade_rule():
    assert (POLICY.stop_atr, POLICY.atr_window, POLICY.reward_risk, POLICY.tick_size) == (2.0, 14, 3.0, 0.01)
    assert POLICY.lifetime == SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20, close_time_et="15:45")
    assert POLICY.to_dict()["kind"] == APRIORI_BRACKET_KIND


@pytest.mark.parametrize("entry,atr", [(100.0, 1.2345), (37.21, 0.5), (412.07, 9.87), (10.0, 0.013)])
def test_levels_equal_the_study_rounded_outward_and_ignore_swings(entry, atr):
    stop, target = bracket_prices(entry, 1, atr, swing_low=1.0, swing_high=1e6, policy=POLICY)
    study = levels_for("LONG", entry, atr, ENTRY)
    assert stop <= study.stop and stop > study.stop - 0.01 - 1e-9
    risk = entry - stop
    assert math.isclose(target - entry, math.ceil(round(risk / 0.01) * 3.0 - 1e-10) * 0.01, abs_tol=1e-8)
    assert (target - entry) / risk >= 3.0 - 1e-9


def test_policy_round_trips_and_never_trails():
    document = json.loads(json.dumps(POLICY.to_dict()))
    assert execution_policy_from_dict(document) == POLICY
    assert trailing_price(100.0, 150.0, 95.0, 5.0, 1, POLICY) == 95.0
    assert policy_trails(document) is False
    assert policy_trails(AlphaExecutionPolicy().to_dict()) is True and policy_trails(None) is True


def test_existing_documents_are_byte_identical():
    plain, timed = AlphaExecutionPolicy(), session_entry_policy()
    assert "kind" not in plain.to_dict() and "kind" not in timed.to_dict()
    assert execution_policy_from_dict(plain.to_dict()) == plain
    assert isinstance(execution_policy_from_dict(timed.to_dict()), TimedAlphaExecutionPolicy)


def test_unknown_policy_kind_is_refused():
    with pytest.raises(ValueError, match="Unsupported execution policy kind"):
        execution_policy_from_dict({**POLICY.to_dict(), "kind": "apriori_bracket_v9"})


@pytest.mark.parametrize(
    "field,value", [("stop_atr", 0.0), ("reward_risk", float("nan")), ("atr_window", 1), ("kind", "x")]
)
def test_invalid_apriori_policies_are_refused(field, value):
    fields = {"stop_atr": 2.0, "atr_window": 14, "reward_risk": 3.0, "lifetime": POLICY.lifetime, field: value}
    with pytest.raises(ValueError):
        AprioriBracketPolicy(**fields)
```

- [ ] **Step 3: Run the tests to verify they fail.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/execution/test_session_lifetime.py tests/research/test_apriori_bracket_policy.py -q`
  Expected: collection errors (`SessionLifetimePolicy`, `AprioriBracketPolicy` undefined).

- [ ] **Step 4: Implement the lifetime policy.** Append to `agentic_trader/execution/lifetime_policy.py`, with `from collections.abc import Mapping, Sequence`, `from datetime import time`, `from typing import TYPE_CHECKING, Any`, `from zoneinfo import ZoneInfo`, and under `TYPE_CHECKING`: `from agentic_trader.market.session import MarketCalendarDay`:

```python
SESSION_LIFETIME_VERSION = "session_count_v1"
MAX_HOLDING_SESSIONS = 60
SESSION_CLOSE_BUFFER = timedelta(minutes=15)
_NEW_YORK = ZoneInfo("America/New_York")


class SessionEvidenceError(ValueError):
    """The broker calendar cannot establish a session-counted deadline; callers REVIEW, never guess."""


def _clock(value: str) -> time:
    try:
        hours, minutes = (int(part) for part in value.split(":"))
        return time(hours, minutes)
    except (ValueError, TypeError) as exc:
        raise ValueError("close_time_et requires HH:MM") from exc


@dataclass(frozen=True)
class SessionLifetimePolicy:
    """One-session resting entry, then a holding exit at ``close_time_et`` New York on session N.

    The fill's own regular session is session 1, exactly as the study's ``label_bracket``
    counts it. On an early-close session the exit moves to 15 minutes before that close.
    """

    resting_seconds: int
    holding_sessions: int
    close_time_et: str = "15:45"
    version: str = SESSION_LIFETIME_VERSION

    def __post_init__(self):
        if self.version != SESSION_LIFETIME_VERSION:
            raise ValueError("Unsupported session lifetime version")
        if type(self.resting_seconds) is not int or not 1 <= self.resting_seconds <= MAX_TRADE_LIFETIME_SECONDS:
            raise ValueError("Trade lifetimes require bounded positive integer seconds")
        if type(self.holding_sessions) is not int or not 1 <= self.holding_sessions <= MAX_HOLDING_SESSIONS:
            raise ValueError(f"Holding sessions must be an integer in 1..{MAX_HOLDING_SESSIONS}")
        _clock(self.close_time_et)

    def entry_deadline(self, submitted_at: datetime) -> datetime:
        return aware_utc(submitted_at) + timedelta(seconds=self.resting_seconds)

    def holding_deadline(self, filled_at: datetime, sessions: Sequence["MarketCalendarDay"]) -> datetime:
        fill = aware_utc(filled_at).astimezone(_NEW_YORK)
        regular = sorted(
            (d for d in sessions if d.is_trading_day and d.open_time is not None and d.close_time is not None),
            key=lambda d: d.date,
        )
        first = next((d for d in regular if d.date == fill.date()), None)
        if first is None or not first.open_time <= fill.time().replace(tzinfo=None) <= first.close_time:
            raise SessionEvidenceError("fill_outside_regular_session")
        held = [d for d in regular if d.date >= first.date]
        if len(held) < self.holding_sessions:
            raise SessionEvidenceError("session_calendar_short")
        last = held[self.holding_sessions - 1]
        target = datetime.combine(last.date, _clock(self.close_time_et), tzinfo=_NEW_YORK)
        close = datetime.combine(last.date, last.close_time, tzinfo=_NEW_YORK)
        return min(target, close - SESSION_CLOSE_BUFFER).astimezone(UTC)


LifetimePolicy = TradeLifetimePolicy | SessionLifetimePolicy


def lifetime_from_dict(document: Mapping[str, Any]) -> LifetimePolicy:
    """Deserialize a stored lifetime without rewriting historical documents."""
    if document.get("version") == SESSION_LIFETIME_VERSION:
        return SessionLifetimePolicy(**document)
    return TradeLifetimePolicy(**document)
```

- [ ] **Step 5: Implement the bracket policy** in `agentic_trader/research/alpha/strategy.py`:
  - Import `SessionLifetimePolicy` alongside `TradeLifetimePolicy`.
  - Add the constant and class after `TimedAlphaExecutionPolicy`:

```python
APRIORI_BRACKET_KIND = "apriori_bracket_v1"


@dataclass(frozen=True, kw_only=True)
class AprioriBracketPolicy:
    """A catalog study's fixed bracket: ``stop_atr`` x ATR, ``reward_risk`` R, never trailed.

    A separate kind rather than new ``AlphaExecutionPolicy`` fields, so every existing
    policy document, and every version hash derived from one, stays byte-identical.
    """

    stop_atr: float
    atr_window: int
    reward_risk: float
    lifetime: SessionLifetimePolicy
    tick_size: float = 0.01
    kind: str = APRIORI_BRACKET_KIND

    def __post_init__(self):
        if self.kind != APRIORI_BRACKET_KIND:
            raise ValueError("Unsupported execution policy kind")
        for name in ("stop_atr", "reward_risk", "tick_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid execution policy {name}")
        if type(self.atr_window) is not int or not 2 <= self.atr_window <= 252:
            raise ValueError("Invalid execution lookback/buffer")
        if not isinstance(self.lifetime, SessionLifetimePolicy):
            raise TypeError("A session-counted lifetime policy is required")

    def to_dict(self):
        return asdict(self)


def policy_trails(document: Mapping[str, Any] | None) -> bool:
    """False only for a catalog bracket: its protection is fixed for the whole hold."""
    return not (document and document.get("kind") == APRIORI_BRACKET_KIND)
```

  - Replace `execution_policy_from_dict`:

```python
def execution_policy_from_dict(document: dict) -> AlphaExecutionPolicy | AprioriBracketPolicy:
    """Deserialize versioned policy without rewriting historical financial identity."""
    if "kind" in document:
        if document["kind"] != APRIORI_BRACKET_KIND:
            raise ValueError("Unsupported execution policy kind")
        fields = dict(document)
        fields["lifetime"] = SessionLifetimePolicy(**fields["lifetime"])
        return AprioriBracketPolicy(**fields)
    if "lifetime" in document:
        fields = dict(document)
        fields["lifetime"] = TradeLifetimePolicy(**fields["lifetime"])
        return TimedAlphaExecutionPolicy(**fields)
    return AlphaExecutionPolicy(**document)
```

  - In `bracket_prices`, before the existing validation, add:

```python
    if isinstance(policy, AprioriBracketPolicy):
        if direction not in (-1, 1) or not all(math.isfinite(x) and x > 0 for x in (entry, atr)):
            raise ValueError("Bracket observations must be finite and positive")
        ticks = math.ceil(policy.stop_atr * atr / policy.tick_size - 1e-10)
        stop = round(entry - direction * ticks * policy.tick_size, 8)
        target = round(entry + direction * math.ceil(ticks * policy.reward_risk - 1e-10) * policy.tick_size, 8)
        if min(stop, target) <= 0:
            raise ValueError("Bracket would cross zero")
        return stop, target
```

  - In `trailing_price`, add as its first line: `if isinstance(policy, AprioriBracketPolicy): return stop`.
  - Widen the `policy` parameter annotations of `bracket_prices`, `trailing_price`, `strategy_atr` and `entry_limit` to `AlphaExecutionPolicy | AprioriBracketPolicy`. Those functions only read `tick_size`, `atr_window` and the fields above.
  - Add `from collections.abc import Mapping` and `from typing import Any` if they are absent.

- [ ] **Step 6: Implement `pead_execution_policy`** in `agentic_trader/research/apriori/catalog.py`:

```python
def pead_execution_policy(entry: PeadEntry) -> AprioriBracketPolicy:
    """The live bracket of a PEAD leg: the study's trade block, one-session entry, 15:45 time exit."""
    return AprioriBracketPolicy(
        stop_atr=entry.trade.stop_atr_multiple,
        atr_window=entry.trade.atr_window,
        reward_risk=entry.trade.target_r,
        lifetime=SessionLifetimePolicy(
            resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS,
            holding_sessions=entry.trade.max_hold_sessions,
            close_time_et="15:45",
        ),
    )
```

  Its imports:
  - `from agentic_trader.execution.lifetime_policy import DAILY_ENTRY_LIFETIME_SECONDS, SessionLifetimePolicy`
  - `from agentic_trader.research.alpha.strategy import AprioriBracketPolicy`

- [ ] **Step 7: Check every consumer of `execution_policy_from_dict`.** Run `grep -rn "execution_policy_from_dict(" agentic_trader`. Confirm each call site reads only `tick_size`, `atr_window`, `stop_atr`, `reward_risk`, `lifetime`, or passes the policy to `bracket_prices`, `trailing_price`, `strategy_atr` or `entry_limit`. If one reads `swing_window`, `structural_buffer_ticks`, `friction_per_side` or `trail_*`, guard it with `isinstance(policy, AlphaExecutionPolicy)` and record that in the report.

- [ ] **Step 8: Run the new and neighbouring tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/execution/test_session_lifetime.py tests/research/test_apriori_bracket_policy.py tests/execution/test_lifetimes.py tests/research/test_alpha_execution.py tests/research/apriori -q`
  Expected: PASS.

- [ ] **Step 9: Stage and report.**

```bash
git add agentic_trader/execution/lifetime_policy.py agentic_trader/research/alpha/strategy.py agentic_trader/research/apriori/catalog.py tests/execution/test_session_lifetime.py tests/research/test_apriori_bracket_policy.py
env -u VIRTUAL_ENV uv run pre-commit run --all-files
```

---

### Task 2: Session-counted time exit and fixed protection in the running services

**Files:**
- Modify: `agentic_trader/execution/lifetimes.py:67-102` (`assess_lifetime`), `:105-120` (`__init__`), `:157-195` (`reconcile`, `resume_blockers`), `:210-230` (`_reconcile_signal`)
- Modify: `agentic_trader/storage/lifetimes.py:98-104` (use `lifetime_from_dict`)
- Modify: `agentic_trader/agent/copilot.py:221-227` (construct the default lifetime service after `session_provider`, with its calendar) and `:1760-1762` (`manage_trailing_stops` skips non-trailing policies)
- Test: `tests/execution/test_lifetimes.py` (append)
- Test: `tests/integration/test_trade_lifetimes.py` (append)
- Test: `tests/broker/test_broker_stop_sync.py` (append)

**Interfaces:**
- Consumes from Task 1: `SessionLifetimePolicy`, `SessionEvidenceError`, `lifetime_from_dict`, `LifetimePolicy`, `policy_trails`, `AprioriBracketPolicy`, and `pead_execution_policy` in `research/apriori/catalog.py`.
- Produces:
  - `assess_lifetime(policy: LifetimePolicy, identity, order, now, sessions: Sequence[MarketCalendarDay] | None = None) -> LifetimeDecision`
  - `TradeLifetimeService(..., calendar: MarketCalendarProtocol | None = None)`
  - the module constant `HOLDING_CALENDAR_SPAN_DAYS = 40`

- [ ] **Step 1: Write the failing unit tests.** Append to `tests/execution/test_lifetimes.py`:

```python
from datetime import date, time
from zoneinfo import ZoneInfo

from agentic_trader.execution.lifetime_policy import SessionLifetimePolicy
from agentic_trader.market.session import MarketCalendarDay


def _weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(MarketCalendarDay(day, True, False, time(9, 30), time(16, 0)))
        day += timedelta(days=1)
    return days


def test_session_policy_closes_on_session_twenty_and_reviews_without_a_calendar(lifetime_order):
    _, identity, order, _ = lifetime_order
    ny = ZoneInfo("America/New_York")
    filled_at = datetime(2026, 11, 2, 10, 40, tzinfo=ny)
    filled = order.model_copy(update={"status": "filled", "filled_quantity": "10", "filled_at": filled_at})
    policy = SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20)
    sessions = _weekdays(date(2026, 11, 2), 25)  # no holidays: session 20 is Nov 27
    deadline = datetime(2026, 11, 27, 15, 45, tzinfo=ny)
    before = assess_lifetime(policy, identity, filled, deadline - timedelta(seconds=1), sessions)
    at = assess_lifetime(policy, identity, filled, deadline, sessions)
    assert (before.action, at.action) == (LifetimeAction.NONE, LifetimeAction.CLOSE_POSITION)
    assert at.deadline == deadline.astimezone(UTC)
    missing = assess_lifetime(policy, identity, filled, deadline, None)
    short = assess_lifetime(policy, identity, filled, deadline, sessions[:5])
    assert (missing.action, missing.reason) == (LifetimeAction.REVIEW, "session_calendar_unavailable")
    assert (short.action, short.reason) == (LifetimeAction.REVIEW, "session_calendar_short")
```

- [ ] **Step 2: Write the failing integration tests.** Append to `tests/integration/test_trade_lifetimes.py`. The fake calendar makes every day a 00:00–23:59 session, and the policy exits at 00:01, so "today's session" has always passed its deadline while "tomorrow's" has not:

```python
from agentic_trader.execution.lifetime_policy import SessionLifetimePolicy
from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.research.alpha.strategy import AprioriBracketPolicy


class AllDaysCalendar:
    def __init__(self):
        self.calls = 0

    async def get_calendar_range(self, start, end):
        self.calls += 1
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(day, True, False, dt_time(0, 0), dt_time(23, 59, 59)))
            day += timedelta(days=1)
        return days


async def use_session_policy(c, *, fill_days_ago):
    ny = ZoneInfo("America/New_York")
    policy = AprioriBracketPolicy(
        stop_atr=2.0,
        atr_window=14,
        reward_risk=3.0,
        lifetime=SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20, close_time_et="00:01"),
    )
    async with c.db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, c.signal_id)
        row.alpha_policy, row.timeframe = json.dumps(policy.to_dict(), allow_nan=False), "1d"
    filled_position(c)
    fill = datetime.now(ny).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=fill_days_ago)
    c.venue.entry.update(filled_at=fill.astimezone(UTC).isoformat())
    for service in c.services:
        service.calendar = AllDaysCalendar()


async def test_session_policy_closes_once_on_session_twenty(lifecycle_case):
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=19)  # the fill day is session 1, today is session 20
    await asyncio.gather(*(service.reconcile() for service in c.services))
    posts = [call for call in c.venue.calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][3]["client_order_id"].startswith("hold-")


async def test_session_policy_waits_before_session_twenty(lifecycle_case):
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=18)  # today is session 19
    await asyncio.gather(*(service.reconcile() for service in c.services))
    assert not any(call[0] != "GET" for call in c.venue.calls)


async def test_session_policy_calendar_failure_reviews_and_never_closes(lifecycle_case):
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=25)

    class Down:
        async def get_calendar_range(self, start, end):
            raise RuntimeError("calendar down")

    for service in c.services:
        service.calendar = Down()
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert c.signal_id in await c.services[0].resume_blockers()
```

  Add imports at the top of the file: `from datetime import time as dt_time` and `from zoneinfo import ZoneInfo`.

- [ ] **Step 3: Write the failing trailing test.** Append to `tests/broker/test_broker_stop_sync.py`. Copy the setup of `test_copilot_syncs_broker_stop_on_ratchet` (lines 59–87). In the position dict it builds, set `"alpha_policy": pead_execution_policy(load_pead_entry(Path("config/research/apriori/pead-v2.json")).entry).to_dict()`. Keep the price far enough above entry that the unmodified test would ratchet. Assert `updated == 0` and that `broker.modify_order_stop` was never awaited.

- [ ] **Step 4: Run to verify the failures.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/execution/test_lifetimes.py tests/integration/test_trade_lifetimes.py tests/broker/test_broker_stop_sync.py -q`
  Expected: the new tests FAIL (the `sessions` argument is unknown; no `calendar` attribute; the stop ratchets).

- [ ] **Step 5: Implement `assess_lifetime` with sessions.** In `agentic_trader/execution/lifetimes.py`:
  - Import `LifetimePolicy`, `SessionEvidenceError`, `SessionLifetimePolicy`, `lifetime_from_dict` and `from collections.abc import Sequence`.
  - Under `TYPE_CHECKING`, import `MarketCalendarDay, MarketCalendarProtocol`.
  - Change the signature to `def assess_lifetime(policy: LifetimePolicy, identity, order, now, sessions: Sequence[MarketCalendarDay] | None = None)`.
  - Replace `deadline = policy.holding_deadline(order.filled_at)` in the filled branch with:

```python
        if isinstance(policy, SessionLifetimePolicy):
            if sessions is None:
                return LifetimeDecision(LifetimeAction.REVIEW, "session_calendar_unavailable")
            try:
                deadline = policy.holding_deadline(order.filled_at, sessions)
            except SessionEvidenceError as exc:
                return LifetimeDecision(LifetimeAction.REVIEW, str(exc))
        else:
            deadline = policy.holding_deadline(order.filled_at)
```

- [ ] **Step 6: Give the service a calendar.**
  - Add `calendar: MarketCalendarProtocol | None = None` to `TradeLifetimeService.__init__` (keyword-only, after `config`) and store it as `self.calendar`.
  - Add `HOLDING_CALENDAR_SPAN_DAYS = 40` beside `BRACKET_EXIT_COUNT`.
  - Add the method:

```python
    async def _sessions(self, lifetime: LifetimePolicy, parent: OrderObservation) -> list | None:
        """Broker sessions from the fill date; ``None`` when this policy counts no sessions."""
        if not isinstance(lifetime, SessionLifetimePolicy) or parent.filled_at is None:
            return None
        if self.calendar is None:
            raise RuntimeError("Session-counted lifetime requires a market calendar")
        start = aware_utc(parent.filled_at).astimezone(NEW_YORK).date()
        days = await self.calendar.get_calendar_range(start, start + timedelta(days=HOLDING_CALENDAR_SPAN_DAYS))
        return [day for day in days if day.is_trading_day]
```

  Define `NEW_YORK = ZoneInfo("America/New_York")` in the module and import `timedelta`.

  - In `reconcile()` and `resume_blockers()`, replace `TradeLifetimePolicy(**signal["alpha_policy"]["lifetime"])` with `lifetime_from_dict(signal["alpha_policy"]["lifetime"])`.
  - In both `_reconcile_signal` and `resume_blockers`, change `assess_lifetime(lifetime, identity, parent, self.clock())` to `assess_lifetime(lifetime, identity, parent, self.clock(), await self._sessions(lifetime, parent))`.

  A raised calendar error flows into the existing `except` blocks: `lifecycle_evidence_unavailable:<Type>` review in `reconcile`, and "blocked" in `resume_blockers`.

- [ ] **Step 7: Update `storage/lifetimes.py`.** Replace `TradeLifetimePolicy(**lifetime)` with `lifetime_from_dict(lifetime)` and update the import. The entry-cancel branch needs no sessions.

- [ ] **Step 8: Wire the copilot.** In `agentic_trader/agent/copilot.py.__init__`:
  - Delete the `self.lifetime_service = (...)` block at lines 221–227.
  - Re-insert it immediately after `self.session_provider = CompositeMarketSessionProvider(...)`, as:

```python
        self.lifetime_service = (
            lifetime_service
            if lifetime_service is not None
            else TradeLifetimeService(
                LifetimeRepository(self.db.workflows),
                self.broker,
                self.close_service,
                config=config.execution,
                calendar=self.session_provider.calendar,
            )
        )
```

  Confirm with `grep -n "self.lifetime_service" agentic_trader/agent/copilot.py` that nothing between the old and new positions uses it.

  - In `manage_trailing_stops`, immediately after `if self.broker.authoritative_positions and not pos.get("executed_at"): continue`, add:

```python
            if not policy_trails(pos.get("alpha_policy")):
                continue  # A catalog bracket keeps its original protection for the whole hold.
```

  Import `policy_trails` from `agentic_trader.research.alpha.strategy`.

- [ ] **Step 9: Run the affected tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/execution tests/integration/test_trade_lifetimes.py tests/broker/test_broker_stop_sync.py tests/agent -q`
  Then: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest tests/integration/test_trade_lifetimes.py --run-postgres -q`
  Expected: PASS.

- [ ] **Step 10: Stage and report.**

```bash
git add agentic_trader/execution/lifetimes.py agentic_trader/storage/lifetimes.py agentic_trader/agent/copilot.py tests/execution/test_lifetimes.py tests/integration/test_trade_lifetimes.py tests/broker/test_broker_stop_sync.py
env -u VIRTUAL_ENV uv run pre-commit run --all-files
```

---

### Task 3: Catalog probe identity, enrolment, admission, CLI and `/alphas`

**Files:**
- Create: `agentic_trader/research/apriori/probe.py`
- Modify: `agentic_trader/research/alpha/models.py:244-248` (`RegistrySnapshot.catalog_probes`)
- Modify: `agentic_trader/storage/alpha.py` (`enrol_catalog_probe`; shared enrolment tail; `_change`, `snapshot`, `versions`, `_symbol_owner_conflict` and `probe_report` recognise catalog versions)
- Modify: `agentic_trader/storage/workflow.py:442-484` (`_alpha_entry_rejection` catalog branch)
- Modify: `agentic_trader/cli/commands/alpha.py` (`apriori-probe` command; `inspect` prints a catalog definition raw)
- Modify: `agentic_trader/presentation/formatters.py:561-586` (`format_alphas_dashboard_html`)
- Test: `tests/research/test_apriori_catalog_probe.py` (new)
- Test: `tests/storage/test_catalog_probe_admission.py` (new)
- Test: `tests/integration/test_alpha_probe_postgres.py` (append one catalog case)
- Test: `tests/agent/test_alphas_probe_summary.py` (append)

**Interfaces:**
- Consumes from Task 1: `pead_execution_policy`, `load_pead_entry`.
- Produces, in `agentic_trader/research/apriori/probe.py`:
  - `CATALOG_KIND = "apriori"`
  - `catalog_version_id(entry_id: str, version: int, leg: str, sha256: str) -> str`
  - `is_catalog_definition(definition: Mapping[str, Any] | None) -> bool`
  - `load_catalog_probe(entry_path: Path, study_dir: Path, leg: str) -> dict[str, Any]`, which returns the definition document including `"version_id"`
- Produces, in `AlphaRepository`: `enrol_catalog_probe(definition: dict, *, actor: str, expected_generation: int, days: int | None = None, renew: bool = False, now: datetime | None = None) -> int` (the new generation).
- Produces: `RegistrySnapshot.catalog_probes: tuple[dict[str, Any], ...] = ()`. These are the live catalog definitions, each with a `"version_id"` key.
- Produces: `probe_report()` rows gain `"kind"` (`"apriori"` or `"dsl"`) and `"study_mean_r"` (a float or None).
- The definition document's keys:
  - `kind`, `version_id`, `alpha_id`, `entry_id`, `entry_version`, `entry_sha256`, `leg`, `timeframe` (`"1d"`);
  - `execution` (the `AprioriBracketPolicy` document);
  - `study_result_sha256`, `study_manifest_sha256`, `study_mean_r`;
  - `eligible_symbols` (None), `clock` (None).

- [ ] **Step 1: Write the failing identity and enrolment tests.** In `tests/research/test_apriori_catalog_probe.py`, reuse `paper_database`, `make_definition` and `seed` from `tests/research/probe_fixtures.py`. Build a study directory in `tmp_path` with this helper:

```python
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_trader.research.apriori.probe import catalog_version_id, is_catalog_definition, load_catalog_probe
from agentic_trader.storage.alpha import AlphaRepository
from tests.research.probe_fixtures import make_definition, paper_database, seed

ENTRY = Path("config/research/apriori/pead-v2.json")
SHA = hashlib.sha256(ENTRY.read_bytes()).hexdigest()
NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


def study(tmp_path, *, sha=SHA, long="eligible_for_probe", status="completed", entry_id="pead", version=2):
    directory = tmp_path / "study"
    directory.mkdir(exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"entry_id": entry_id, "version": version, "sha256": sha, "authorizes_promotion": False})
    )
    (directory / "result.json").write_text(
        json.dumps(
            {
                "status": status,
                "authorizes_promotion": False,
                "decisions": {"LONG": long, "SHORT": "failed"},
                "legs": {"LONG": {"p1": {"mean_r_cost": 0.1489}}, "SHORT": {"p1": {"mean_r_cost": -0.064}}},
            }
        )
    )
    return directory


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()
```

  Tests to write, each asserting the exact error text fragment given:
  - `test_definition_pins_entry_study_and_policy`: checks
    - `definition["version_id"] == catalog_version_id("pead", 2, "LONG", SHA) == f"apriori:pead:v2:long:{SHA[:16]}"`;
    - `alpha_id == "pead_long"`;
    - `execution["kind"] == "apriori_bracket_v1"`;
    - `study_mean_r == 0.1489`;
    - `study_result_sha256` equals the SHA-256 of `result.json`'s bytes;
    - `is_catalog_definition(definition)`.
  - A parametrized refusal test:
    - leg `"SHORT"` gives `"did not pass its study"`;
    - leg `"sideways"` gives `"leg"`;
    - `sha="0"*64` gives `"manifest SHA-256"`;
    - `status="failed"` gives `"not completed"`;
    - `long="failed"` gives `"did not pass its study"`;
    - `entry_id="other"` gives `"manifest names"`.
  - `test_enrolment_lists_the_catalog_probe_and_snapshot_separates_it`:
    - `generation = await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)`;
    - `snapshot = await repository.snapshot(now=NOW)`;
    - `snapshot.probe == ()`;
    - `[d["version_id"] for d in snapshot.catalog_probes] == [definition["version_id"]]`;
    - the enrolment record `probe/<id>` has `term_days == 30`;
    - `await repository.versions()` returns only DSL definitions and does not raise.
  - `test_re_enrolment_with_changed_evidence_is_refused`: after enrolment, a definition with a different `study_result_sha256` under the same ID raises `"Catalog version evidence differs"`.
  - `test_catalog_probe_shares_slots_kill_and_renewal_rules`:
    - fill 3 slots (`AlphaPipelineConfig.max_probes` default 3) with DSL probes, then catalog enrolment raises `"probe slots are in use"`;
    - after a closed signal with `alpha_version=<catalog id>`, `realized_pnl=-450`, `risk_dollars=100` and `exit_timestamp=NOW`, re-enrolment raises `"kill rule"`;
    - `renew=True` for a non-listed version raises `"not a current probe"`.
  - `test_catalog_versions_cannot_be_promoted_or_shadowed_only_demoted`:
    - `promote` and `shadow` of the catalog ID raise `"Catalog alphas run only as paper probes"`;
    - `demote` removes it from `probe`.
  - `test_dsl_enrolment_and_promotion_ignore_catalog_owners`: with the catalog probe live, `enrol_probe` of a DSL definition on `SPY` succeeds, with no `AlphaDefinition.from_dict` crash.
  - `test_probe_report_marks_catalog_rows`: the row has `kind == "apriori"`, `alpha_id == "pead_long"`, `symbols == []` and `study_mean_r == 0.1489`.
  - `test_sweep_retires_an_expired_catalog_probe_with_one_notice`: `sweep_probes(now=NOW + timedelta(days=31))` returns `[catalog_id]`.

- [ ] **Step 2: Write the failing admission tests.** Create `tests/storage/test_catalog_probe_admission.py`. It enrols the catalog probe in a `paper_database`, records a signal via `db.record_signal(...)`, then opens a session and calls `await db.workflows._alpha_entry_rejection(session, row)` directly. The signal has:
  - `contract="NVDA"`, `strategy="pead_long"`, `direction="LONG"`, `timeframe="1d"`;
  - `alpha_version=<id>`, `alpha_policy=definition["execution"]`;
  - `decision_provenance={"pead_event": {"symbol": "NVDA", "session": <today New York ISO date>}}`.

  Cases:
  - the matching signal returns `None`;
  - each single mutation returns `"Catalog signal differs from its immutable strategy contract."`:
    - `direction="SHORT"`;
    - `strategy="pead_short"`;
    - `alpha_policy` with `stop_atr` 1.5;
    - provenance symbol `"AMD"`;
    - provenance session yesterday;
    - provenance missing;
  - after `demote`, the result starts with `"Alpha version is not active/qualified"`;
  - after the term expires (monkeypatch `datetime` via `now` or enrol with `days=1` and a signal checked at `NOW + 2 days`, following how the existing probe admission tests control time), the result starts with `"Paper probe blocked:"`.

- [ ] **Step 3: Run to verify the failures.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_apriori_catalog_probe.py tests/storage/test_catalog_probe_admission.py -q`
  Expected: FAIL (the module is missing).

- [ ] **Step 4: Implement `research/apriori/probe.py`:**

```python
"""Catalog probe identity: a passing catalog leg's frozen definition document.

A catalog alpha is not a DSL formula, so its identity is the entry file's SHA-256 plus
the leg, and its evidence is the study's own manifest and result. It can only ever be a
paper probe (see ``AlphaRepository.enrol_catalog_probe``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentic_trader.research.apriori.catalog import load_pead_entry, pead_execution_policy

CATALOG_KIND = "apriori"
LIVE_LEGS = ("LONG",)
ELIGIBLE = "eligible_for_probe"


def catalog_version_id(entry_id: str, version: int, leg: str, sha256: str) -> str:
    return f"apriori:{entry_id}:v{version}:{leg.lower()}:{sha256[:16]}"


def is_catalog_definition(definition: Mapping[str, Any] | None) -> bool:
    return bool(definition) and definition.get("kind") == CATALOG_KIND


def load_catalog_probe(entry_path: Path, study_dir: Path, leg: str) -> dict[str, Any]:
    leg = leg.upper()
    if leg not in ("LONG", "SHORT"):
        raise ValueError(f"Unknown leg {leg!r}; expected LONG or SHORT")
    loaded = load_pead_entry(entry_path)
    manifest_bytes = (study_dir / "manifest.json").read_bytes()
    result_bytes = (study_dir / "result.json").read_bytes()
    manifest, result = json.loads(manifest_bytes), json.loads(result_bytes)
    entry = loaded.entry
    if manifest.get("entry_id") != entry.id or manifest.get("version") != entry.version:
        raise ValueError(
            f"Study manifest names {manifest.get('entry_id')} v{manifest.get('version')}, not {entry.id} v{entry.version}"
        )
    if manifest.get("sha256") != loaded.sha256:
        raise ValueError("Study manifest SHA-256 differs from the entry file; the study tested another version")
    if result.get("status") != "completed":
        raise ValueError(f"Study is not completed (status {result.get('status')!r})")
    decision = (result.get("decisions") or {}).get(leg)
    if decision != ELIGIBLE:
        raise ValueError(f"Leg {leg} did not pass its study ({decision}); it cannot be enrolled")
    if leg not in LIVE_LEGS:
        raise ValueError(f"The live path trades only {', '.join(LIVE_LEGS)}")
    version_id = catalog_version_id(entry.id, entry.version, leg, loaded.sha256)
    return {
        "kind": CATALOG_KIND,
        "version_id": version_id,
        "alpha_id": f"{entry.id}_{leg.lower()}",
        "entry_id": entry.id,
        "entry_version": entry.version,
        "entry_sha256": loaded.sha256,
        "leg": leg,
        "timeframe": "1d",
        "execution": pead_execution_policy(entry).to_dict(),
        "study_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "study_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "study_mean_r": float(result["legs"][leg]["p1"]["mean_r_cost"]),
        "eligible_symbols": None,
        "clock": None,
    }
```

  Confirm that `PeadEntry` exposes `id` and `version` (the JSON keys `"id"` and `"version"`). If the model names them differently, use its names.

- [ ] **Step 5: Implement the repository changes** in `agentic_trader/storage/alpha.py`:
  1. **Shared enrolment tail.** Extract from `_authorize_probe` everything from `previous = await load_enrolment(...)` to the end into `async def _record_probe_enrolment(self, session, registry, version_id, *, label, owner_check, assessment, days, renew, actor, now)`:
     - term validation;
     - sticky kill;
     - renew membership;
     - the slot limit via `_live_probes`;
     - the owner check (call `await owner_check(others)` and raise the existing message when it returns a conflict);
     - the payload;
     - `_append(probe/<id>)`;
     - the notification. `label` replaces `', '.join(definition.eligible_symbols)` in the text.

     Keep the term check (`type(days) is not int …`) in the tail so both paths share it. `_authorize_probe` keeps its DSL-only checks, then calls the tail with:
     - `label=", ".join(definition.eligible_symbols)`;
     - `owner_check=lambda others: self._symbol_owner_conflict(session, definition, (*registry["active"], *others))`;
     - `assessment=assessment.to_dict()`.
  2. **`enrol_catalog_probe`:**

```python
async def enrol_catalog_probe(self, definition, *, actor, expected_generation, days=None, renew=False, now=None):
    now = _aware(now) if now else datetime.now(UTC)
    if not is_catalog_definition(definition):
        raise ValueError("Catalog enrolment requires a catalog definition")
    version_id = definition["version_id"]
    async with self.store.db.session_factory() as session, session.begin():
        await self.store.lock(session)
        await self.store.lock(session, resource="alpha")
        if not is_paper_scope(self.store.scope):
            raise ValueError("Paper probes run only on the Alpaca paper account scope")
        key = f"version/{version_id}"
        existing = await self._get(session, key)
        if existing is not None and existing["definition"] != definition:
            raise ValueError("Catalog version evidence differs; a changed entry or study is a new version")
        registry = _normalized(await self._get(session, REGISTRY_KEY))
        if registry["generation"] != expected_generation:
            raise ValueError("Registry changed; refresh generation before retrying")
        if version_id in registry["active"] or version_id in registry["shadow"]:
            raise ValueError("Catalog alphas run only as paper probes")
        if existing is None:
            await self._append(session, key, {"definition": definition}, EventKind.ALPHA_RESEARCH, actor)
        await self._record_probe_enrolment(
            session,
            registry,
            version_id,
            label=f"{definition['entry_id']} v{definition['entry_version']} {definition['leg']} (any liquid reporter)",
            owner_check=_no_owner_conflict,
            assessment={
                "study_result_sha256": definition["study_result_sha256"],
                "study_mean_r": definition["study_mean_r"],
            },
            days=days,
            renew=renew,
            actor=actor,
            now=now,
        )
        for field in ("active", "shadow", "probe"):
            registry[field] = [
                v
                for v in registry[field]
                if (await self._get(session, f"version/{v}"))["definition"]["alpha_id"] != definition["alpha_id"]
            ]
        registry["probe"] = sorted([*registry["probe"], version_id])
        registry["generation"] += 1
        await self._append(session, REGISTRY_KEY, registry, EventKind.ALPHA_REGISTRY, actor)
        return registry["generation"]
```

     Add `async def _no_owner_conflict(others): return None` at module level. Import `is_catalog_definition` from `agentic_trader.research.apriori.probe` and `is_paper_scope` if it is not already imported.
  3. **`_change`.** After loading `version`:

```python
            catalog = is_catalog_definition(version["definition"])
            if catalog and mode != "inactive":
                raise ValueError("Catalog alphas run only as paper probes; enrol them with `alpha apriori-probe`")
            alpha_id = version["definition"]["alpha_id"]
            definition = None if catalog else AlphaDefinition.from_dict(version["definition"])
```

     Change the supersession loop to compare `old["definition"]["alpha_id"] != alpha_id`. The `probe` and `active` branches only run when `definition` is not None, which is guaranteed by the raise above.
  4. **`snapshot()`.** Build `catalog = []`. For each live probe ID whose row definition is catalog, append `{**row["definition"], "version_id": version_id}` and skip `AlphaDefinition.from_dict`. Return `RegistrySnapshot(..., catalog_probes=tuple(catalog))`. Apply the same skip for catalog rows in the `active` and `shadow` groups (defensive; they cannot occur).
  5. **`versions()`.** Skip rows whose definition `is_catalog_definition`.
  6. **`_symbol_owner_conflict`.** `if is_catalog_definition(current["definition"]): continue` before `from_dict`.
  7. **`probe_report`.** Add `"kind": version["definition"].get("kind", "dsl")` and `"study_mean_r": version["definition"].get("study_mean_r")` to the initial row dict.

  In `models.py`, add `catalog_probes: tuple[dict[str, Any], ...] = ()` to `RegistrySnapshot`, with `from typing import Any` if it is absent.

- [ ] **Step 6: Implement admission** in `agentic_trader/storage/workflow.py` `_alpha_entry_rejection`. Right after `definition = json.loads(row.payload)["definition"]`:

```python
        if definition.get("kind") == CATALOG_KIND:
            if is_active:
                return "Catalog alphas run only as paper probes."
            return _catalog_signal_rejection(signal, definition)
```

  Add at module level:

```python
def _catalog_signal_rejection(signal: SignalRecord, definition: dict) -> str | None:
    provenance = json.loads(signal.decision_provenance or "{}")
    event = provenance.get("pead_event") if isinstance(provenance, dict) else None
    session = signal.timestamp.astimezone(ET_TZ).date().isoformat()
    if (
        signal.strategy != definition["alpha_id"]
        or signal.timeframe != definition["timeframe"]
        or str(signal.direction).upper() != definition["leg"]
        or not signal.alpha_policy
        or json.loads(signal.alpha_policy) != definition["execution"]
        or not isinstance(event, dict)
        or event.get("symbol") != signal.contract.upper()
        or event.get("session") != session
    ):
        return "Catalog signal differs from its immutable strategy contract."
    return None
```

  Import `CATALOG_KIND` from `agentic_trader.research.apriori.probe` and `ET_TZ` from `agentic_trader.market.session`. Check for an import cycle with `python -c "import agentic_trader.storage.workflow"`. If `research.apriori.probe` pulls in something that imports `storage.workflow`, define `CATALOG_KIND = "apriori"` in `storage/probe_state.py` and import it from there in both modules.

- [ ] **Step 7: Add the CLI command.** In `agentic_trader/cli/commands/alpha.py`, mirror the existing `probe` command at lines 439–468, including its actor, `--generation` and confirmation options:

```python
@alpha_group.command("apriori-probe")
@click.argument("entry_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--study", "study_dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--leg", type=click.Choice(["long", "short"]), default="long", show_default=True)
@click.option("--days", type=int, default=None)
@click.option("--renew", is_flag=True)
# … the same --generation / --actor / --yes options as `alpha probe`
@coro
async def apriori_probe_cmd(entry_path, study_dir, leg, days, renew, generation, actor, yes):
    """Enrol (or renew) a passing catalog leg as a capped paper probe. Never automatic."""
    definition = load_catalog_probe(entry_path, study_dir, leg)
    # Echo the version ID, entry SHA, study mean R and the probe limits, then confirm unless --yes,
    # exactly as `alpha probe` does; then:
    generation = await repository.enrol_catalog_probe(
        definition, actor=actor, expected_generation=generation, days=days, renew=renew
    )
```

  In `inspect` (lines 470–486): when `is_catalog_definition(row["definition"])`, print `json.dumps(row, indent=2)` and return, before `AlphaDefinition.from_dict`.

  **Test:** add to `tests/research/test_apriori_catalog_probe.py` a `CliRunner` test that invokes `apriori-probe` with the fixture study. Patch the repository factory the same way the existing `alpha probe` CLI test does; find it with `grep -rn '"probe"' tests/cli`. Assert the command enrols once and prints the version ID.

- [ ] **Step 8: Update `/alphas`.** In `format_alphas_dashboard_html`:
  - count `len(snapshot.probe) + len(snapshot.catalog_probes)`;
  - name the probes from `[d.alpha_id for d in snapshot.probe] + [d["alpha_id"] for d in snapshot.catalog_probes]`;
  - in the per-row line, render `symbols` as `"any liquid reporter"` when `row.get("kind") == "apriori"`, and append ``f" · study {row['study_mean_r']:+.3f}R/trade"`` when `study_mean_r` is not None.

  Append a test to `tests/agent/test_alphas_probe_summary.py`: a snapshot with one catalog probe and a probe-report row `{"kind": "apriori", "alpha_id": "pead_long", "study_mean_r": 0.1489, "live": True, "days_remaining": 29.0, "kill_distance_r": 4.0, "forward": {"trades": 0, "cumulative_r": 0.0}}` renders `pead_long (any liquid reporter)` and `study +0.149R/trade`.

- [ ] **Step 9: Add a PostgreSQL case.** Append to `tests/integration/test_alpha_probe_postgres.py`: enrol the catalog probe, then check `snapshot().catalog_probes` and admission acceptance of a matching signal, following that file's existing fixture pattern.

- [ ] **Step 10: Run the tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/research tests/storage tests/agent/test_alphas_probe_summary.py tests/cli -q`
  Then: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest tests/integration/test_alpha_probe_postgres.py --run-postgres -q`
  Expected: PASS, including every existing probe-registry test.

- [ ] **Step 11: Stage and report.** Stage the files above explicitly, then run pre-commit.

---

### Task 4: Live PEAD event source

**Files:**
- Modify: `agentic_trader/agent/earnings.py` (`ReportedPage`, `NasdaqEarningsCalendar.reported_rows`)
- Modify: `agentic_trader/data/providers.py` (`AlpacaDataProvider.fetch_daily_many`)
- Create: `agentic_trader/research/apriori/pead_live.py`
- Test: `tests/agent/test_earnings.py` (append)
- Test: `tests/data/test_fetch_daily_many.py` (new; locate the existing provider tests with `ls tests/data` and reuse their fake stock client)
- Test: `tests/research/apriori/test_pead_live.py` (new)

**Interfaces:**
- Consumes: `build_events`, `MarketData`, `SUPPORTED_SYMBOL` and `EVENT_COLUMNS` from `pead_events`; `dedupe_rows` from `earnings_history`; `PeadEntry`.
- Produces, in `agentic_trader/agent/earnings.py`:
  - `@dataclass(frozen=True) class ReportedPage(day: date, rows: tuple[CalendarRow, ...] | None, http_status: int | None, detail: str | None, body_sha256: str | None)`. `rows is None` means unavailable.
  - `async NasdaqEarningsCalendar.reported_rows(day: date) -> ReportedPage`
- Produces, in `agentic_trader/data/providers.py`: `AlpacaDataProvider.fetch_daily_many(symbols: Sequence[str], start: datetime, end: datetime, *, adjustment: str = "raw") -> dict[str, pd.DataFrame]`, plus `DAILY_BATCH_SYMBOLS = 100`.
- Produces, in `agentic_trader/research/apriori/pead_live.py`:
  - `class DailyBars(Protocol)` with `fetch_daily_many`
  - `@dataclass(frozen=True) class LiveEvents(status: Literal["ok", "unavailable"], session: date, report_date: date | None, events: tuple[dict[str, Any], ...], counts: dict[str, Any], reason: str | None, page: ReportedPage | None)`
  - `async def live_events(entry: PeadEntry, session: date, *, earnings, calendar, bars: DailyBars, static_symbols: Sequence[str]) -> LiveEvents`
  - `event_document(row: Mapping[str, Any]) -> dict[str, Any]`, a JSON-safe event row: dates and datetimes as ISO strings, numpy scalars as Python types
  - `LOOKBACK_CALENDAR_DAYS = 120`

  `events` holds only LONG-leg rows, ordered by `z` descending then `symbol` ascending. Each is an `event_document` of an `EVENT_COLUMNS` row.

- [ ] **Step 1: Write the failing `reported_rows` tests.** Append to `tests/agent/test_earnings.py`, using `httpx.MockTransport` as the file's existing tests do:
  - a 200 response with `{"status": {"rCode": 200}, "data": {"rows": [{"symbol": "NVDA", "eps": "$1.10", "epsForecast": "$1.00", "surprise": "10", "noOfEsts": "5", "time": "time-not-supplied", "fiscalQuarterEnding": "Oct/2026"}]}}` gives `rows` of length 1, `body_sha256` equal to the SHA-256 of the response bytes, and `rows[0].eps == 1.10`;
  - HTTP 503 gives `rows is None` and `http_status == 503`;
  - a 200 response with `"status": {"rCode": 400}` gives `rows is None` and `"rCode"` in the detail;
  - a body that is a list gives `rows is None`;
  - a transport exception gives `rows is None` and the detail names the exception type.

- [ ] **Step 2: Implement `reported_rows`:**

```python
@dataclass(frozen=True)
class ReportedPage:
    day: date
    rows: tuple[CalendarRow, ...] | None
    http_status: int | None
    detail: str | None
    body_sha256: str | None

    # method on NasdaqEarningsCalendar
    async def reported_rows(self, day: date) -> ReportedPage:
        """Every row Nasdaq lists for ``day``; unusable pages are ``rows=None``, never guessed.

        Acceptance matches the study's acquisition: HTTP 200, a JSON object and
        ``status.rCode == 200``. Uncached: a live PEAD decision reads the page once.
        """
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=6.0) as client:
                resp = await client.get(
                    NASDAQ_EARNINGS_CALENDAR_URL, params={"date": day.isoformat()}, headers=NASDAQ_REQUEST_HEADERS
                )
        except Exception as exc:
            return ReportedPage(day, None, None, f"request failed: {type(exc).__name__}", None)
        digest = hashlib.sha256(resp.content).hexdigest()
        if resp.status_code != 200:
            return ReportedPage(day, None, resp.status_code, f"HTTP {resp.status_code}", digest)
        try:
            payload = resp.json()
        except ValueError:
            return ReportedPage(day, None, 200, "body is not JSON", digest)
        if not isinstance(payload, dict):
            return ReportedPage(day, None, 200, "body is not a JSON object", digest)
        code = (payload.get("status") or {}).get("rCode") if isinstance(payload.get("status"), dict) else None
        if code != 200:
            return ReportedPage(day, None, 200, f"status.rCode {code!r}", digest)
        return ReportedPage(day, tuple(parse_calendar_payload(payload, day)), 200, None, digest)
```

  Add `import hashlib`.

- [ ] **Step 3: Write the failing `fetch_daily_many` test** in `tests/data/test_fetch_daily_many.py`. The fake `stock_client` records requests and returns an object whose `.df` is a `(symbol, timestamp)` MultiIndex frame with lowercase `open`/`high`/`low`/`close`/`volume` columns for the requested symbols minus one missing symbol. Assert:
  - 250 symbols produce 3 requests of at most 100 symbols;
  - the missing symbol is absent from the result;
  - each frame has title-case OHLCV columns;
  - `request.feed` and `request.adjustment` match the provider feed and the argument.

- [ ] **Step 4: Implement `fetch_daily_many`:**

```python
DAILY_BATCH_SYMBOLS = 100


    def fetch_daily_many(
        self, symbols: Sequence[str], start: datetime, end: datetime, *, adjustment: str = "raw"
    ) -> dict[str, pd.DataFrame]:
        """Daily bars for many equities in batched requests; symbols without bars are absent.

        No bar-evidence capture: a live PEAD decision journals its own event evidence,
        as the study kept its own cache.
        """
        if self.stock_client is None:
            raise UnsupportedSymbolError("No Alpaca stock client available")
        clean = sorted({symbol.strip().upper() for symbol in symbols if "/" not in symbol})
        frames: dict[str, pd.DataFrame] = {}
        for index in range(0, len(clean), DAILY_BATCH_SYMBOLS):
            chunk = clean[index : index + DAILY_BATCH_SYMBOLS]
            bars = self.stock_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=chunk,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end,
                    feed=self.feed,
                    adjustment=Adjustment(adjustment),
                )
            )
            present = set(bars.df.index.get_level_values("symbol")) if not bars.df.empty else set()
            for symbol in chunk:
                if symbol in present:
                    frame, _ = self._normalize_bars(bars, symbol)
                    if not frame.empty:
                        frames[symbol] = frame
        return frames
```

  Add `from collections.abc import Sequence` if it is absent.

- [ ] **Step 5: Write the failing `live_events` tests** in `tests/research/apriori/test_pead_live.py`. Build synthetic inputs in the style of `tests/research/apriori/test_pead_events.py`; reuse its bar builders if it has them.
  - **Calendar:** a fake with `get_calendar_range` returning weekday sessions (09:30–16:00) from 2026-06-01 to 2026-10-30.
  - **Session:** `S = 2026-10-28` (Wednesday), so `D = 2026-10-26` and `D+1 = 2026-10-27`.
  - **Bars:** a fake `DailyBars` returning adjusted and raw frames:
    - SPY flat;
    - `WINR`: price 50, a +8% move D-1→D+1, large volume;
    - `LOSR`: a +8% move but a price of 5 (fails `min_price`);
    - `FLAT`: no move;
    - 25 static symbols at price 20 with moderate volume.
  - **Earnings:** a fake `reported_rows` returning `ReportedPage` rows for `WINR` (eps 1.10 vs forecast 1.00), `LOSR` (the same) and `FLAT`.

  Tests:
  - `test_live_events_equal_the_study_rows_for_that_session` (the parity test). Call `build_events(rows, MarketData(...), entry_with_window)`, where the frozen entry is `model_copy`'d so that `decisions=(date(2026, 8, 3), date(2026, 10, 30))`, filter `session == S` and `leg == "LONG"`, and map through `event_document`. The result must equal `live_events(...).events`. The only expected event is `WINR`.
  - `test_ordering_is_by_reaction_z_then_symbol`: two long events with different z come out in descending z.
  - Parametrized unavailability, each with `status == "unavailable"`, `events == ()` and the reason fragment:
    - the page has `rows is None` gives `"calendar_unavailable"`;
    - an empty rows tuple gives `"calendar_empty"`;
    - SPY lacks the D+1 bar gives `"benchmark_bars_unavailable"`;
    - `bars.fetch_daily_many` raises gives `"bars_unavailable"`;
    - fewer than 20 static names have bars (`build_events` reports `no_reference`) gives `"static_reference_unavailable"`;
    - `S` is not a trading day gives `"not_a_trading_session"`.
  - `test_counts_carry_build_events_skip_reasons`: `counts["reasons"]["illiquid_price"] == 1` (from `LOSR`).

- [ ] **Step 6: Implement `research/apriori/pead_live.py`:**

```python
"""Live PEAD events for one decision session, computed by the study's own ``build_events``.

The frozen entry is the identity; a copy narrowed to ``decisions=(session, session)`` is
only a window, never validated or persisted. Any missing evidence (Nasdaq page, SPY or
the static liquidity reference) makes the whole session unavailable: no partial sets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import ReportedPage
from agentic_trader.research.apriori.catalog import PeadEntry
from agentic_trader.research.apriori.earnings_history import dedupe_rows
from agentic_trader.research.apriori.pead_events import SUPPORTED_SYMBOL, MarketData, build_events

LOOKBACK_CALENDAR_DAYS = 120


class DailyBars(Protocol):
    def fetch_daily_many(
        self, symbols: Sequence[str], start: datetime, end: datetime, *, adjustment: str
    ) -> dict[str, pd.DataFrame]: ...


@dataclass(frozen=True)
class LiveEvents:
    status: Literal["ok", "unavailable"]
    session: date
    report_date: date | None
    events: tuple[dict[str, Any], ...]
    counts: dict[str, Any]
    reason: str | None
    page: ReportedPage | None


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def event_document(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _plain(value) for key, value in row.items()}


def _unavailable(session, reason, *, report_date=None, page=None, counts=None) -> LiveEvents:
    return LiveEvents("unavailable", session, report_date, (), counts or {}, reason, page)


def _has_bar(frame: pd.DataFrame | None, day: date) -> bool:
    if frame is None or frame.empty:
        return False
    index = pd.DatetimeIndex(frame.index)
    index = index.tz_localize(UTC) if index.tz is None else index
    return day in set(index.tz_convert("America/New_York").date)


async def live_events(
    entry: PeadEntry, session: date, *, earnings, calendar, bars: DailyBars, static_symbols
) -> LiveEvents:
    days = await calendar.get_calendar_range(session - timedelta(days=LOOKBACK_CALENDAR_DAYS), session)
    trading = tuple(sorted(day.date for day in days if day.is_trading_day))
    if len(trading) < 3 or trading[-1] != session:
        return _unavailable(session, "not_a_trading_session")
    report_date, as_of = trading[-3], trading[-2]
    page = await earnings.reported_rows(report_date)
    if page.rows is None:
        return _unavailable(session, f"calendar_unavailable: {page.detail}", report_date=report_date, page=page)
    if not page.rows:
        return _unavailable(session, "calendar_empty", report_date=report_date, page=page)
    rows, _ = dedupe_rows(page.rows)
    reporters = sorted({row.symbol for row in rows if SUPPORTED_SYMBOL.fullmatch(row.symbol)})
    benchmark = entry.event.benchmark
    start = datetime.combine(trading[0], time.min, tzinfo=UTC)
    end = datetime.combine(as_of, time(23, 59), tzinfo=UTC)
    try:
        adjusted = await asyncio.to_thread(bars.fetch_daily_many, [benchmark, *reporters], start, end, adjustment="all")
        raw = await asyncio.to_thread(
            bars.fetch_daily_many, sorted({*reporters, *static_symbols}), start, end, adjustment="raw"
        )
    except Exception as exc:
        return _unavailable(session, f"bars_unavailable: {type(exc).__name__}", report_date=report_date, page=page)
    if not _has_bar(adjusted.get(benchmark), as_of):
        return _unavailable(session, "benchmark_bars_unavailable", report_date=report_date, page=page)
    market = MarketData(trading, adjusted, tuple(s for s in static_symbols if s in raw), liquidity_daily=raw)
    window = entry.window.model_copy(update={"decisions": (session, session)})
    frame, counts = await asyncio.to_thread(build_events, rows, market, entry.model_copy(update={"window": window}))
    if counts["reasons"].get("no_reference"):
        return _unavailable(session, "static_reference_unavailable", report_date=report_date, page=page, counts=counts)
    long = frame[frame["leg"] == "LONG"].sort_values(["z", "symbol"], ascending=[False, True])
    events = tuple(event_document(row) for row in long.to_dict("records"))
    return LiveEvents("ok", session, report_date, events, counts, None, page)
```

  Check `dedupe_rows`'s signature in `earnings_history.py`. If it takes a list, pass `list(page.rows)`.

- [ ] **Step 7: Run the tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_earnings.py tests/data tests/research/apriori -q`
  Expected: PASS.

- [ ] **Step 8: Stage and report.** Stage explicitly, then run pre-commit.

---

### Task 5: `EarningsDriftService` and configuration

**Files:**
- Create: `agentic_trader/screeners/earnings_drift.py`
- Modify: `agentic_trader/config.py` (`AprioriConfig`, and `AppConfig.apriori`)
- Modify: `agentic_trader/screeners/base.py:23-50` (`ScreenerCandidate.catalog_event`)
- Test: `tests/screeners/test_earnings_drift.py` (new; create `tests/screeners/__init__.py` only if sibling test packages use them)

**Interfaces:**
- Consumes:
  - `live_events`, `LiveEvents` and `event_document` (Task 4);
  - `load_pead_entry` and `pead_execution_policy` (Task 1);
  - `SessionLifetimePolicy.holding_deadline`;
  - `RegistrySnapshot.catalog_probes` (Task 3);
  - `calculate_ema` and `calculate_rsi` from `screeners/indicators.py`.
- Produces, in `agentic_trader/config.py`: `class AprioriConfig(BaseModel)` with `enabled: bool = True`, `pead_entry_path: str = "config/research/apriori/pead-v2.json"`, `max_drift_cards_per_session: int = Field(default=1, ge=0)`, and `AppConfig.apriori: AprioriConfig = Field(default_factory=AprioriConfig)`.
- Produces: `ScreenerCandidate.catalog_event: dict[str, Any] | None = None`.
- Produces, in `agentic_trader/screeners/earnings_drift.py`:
  - `PEAD_STRATEGY_ID = "pead_long"`
  - `@dataclass(frozen=True) class DriftPreparation(status: str, session: date, version_id: str | None = None, policy: dict | None = None, events: tuple[dict, ...] = (), skipped: dict[str, str] = field(default_factory=dict), counts: dict = field(default_factory=dict), reason: str | None = None, report_date: str | None = None, page_sha256: str | None = None, time_exit_at: str | None = None)`. Its `status` is one of `"inactive"`, `"unavailable"`, `"ok"`.
  - `class EarningsDriftService`:
    - `__init__(self, entry_path: Path, *, earnings, calendar, bars, static_symbols: Sequence[str])`
    - `.decision_time_et -> str`
    - `.applies(scheduled_time_et: str | None) -> bool`
    - `.live_version(snapshot: RegistrySnapshot) -> dict | None`
    - `async .prepare(*, now: datetime, snapshot: RegistrySnapshot, owned: Mapping[str, str]) -> DriftPreparation`
    - `.candidate(event: dict, data: Any, prep: DriftPreparation) -> ScreenerCandidate | None`
  - `drift_card_facts(event: dict, prep: DriftPreparation, holding_sessions: int) -> dict`, returning `{"surprise_pct", "z", "report_date", "holding_sessions", "time_exit_at"}`.

- [ ] **Step 1: Write the failing tests** in `tests/screeners/test_earnings_drift.py`. Use fakes: a `live_events` monkeypatch (`monkeypatch.setattr(earnings_drift, "live_events", fake)`), a weekday calendar fake, and a `RegistrySnapshot(0, (), (), catalog_probes=(definition,))` where `definition` comes from `load_catalog_probe` on a `tmp_path` study (copy the `study()` helper from Task 3's test).
  - `test_applies_only_at_the_entry_decision_time`: `applies("10:35")` is True; `applies("14:35")` and `applies(None)` are False.
  - `test_no_live_catalog_version_is_inactive_and_reads_nothing`: an empty `catalog_probes` gives `status == "inactive"`, and the `live_events` fake is never called.
  - `test_a_version_for_another_entry_sha_is_inactive`: a definition with `entry_sha256="0"*64` is inactive.
  - `test_ok_events_skip_owned_symbols_with_reasons`: with the fake returning events `[WINR z=2.1, ABCD z=1.5]` and `owned={"ABCD": "open position"}`:
    - `events` has symbols `["WINR"]` and `skipped == {"ABCD": "open position"}`;
    - `policy == definition["execution"]` and `version_id == definition["version_id"]`;
    - `time_exit_at` is the ISO time of 15:45 New York on the 20th weekday session counted from `now`'s date.
  - `test_unavailable_passes_the_reason_through`: `LiveEvents("unavailable", …, reason="calendar_empty")` gives `status == "unavailable"` and `reason == "calendar_empty"`.
  - `test_candidate_is_a_locked_probe_card`: `data = SimpleNamespace(daily=<60-row OHLC frame>, hourly=<7-row hourly frame whose last close is 123.45>)` gives:
    - `contract == "WINR"`, `strategy == "pead_long"`, `direction == "LONG"`, `timeframe == "1d"`;
    - `current_price == 123.45` and `atr_14 == event["atr"]`;
    - `probe is True`, `setup_quality == 0.0`;
    - `alpha_version == prep.version_id`, `alpha_policy == prep.policy`, `catalog_event == event`;
    - `trigger_detail` contains `"EPS beat +10.0%"` and `"+2.1σ"`.
  - `test_candidate_without_intraday_bars_is_none`: an empty `hourly` and `four_hour` give `None`.

- [ ] **Step 2: Run to verify the failures.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/screeners/test_earnings_drift.py -q`
  Expected: FAIL (the module is missing).

- [ ] **Step 3: Implement the configuration** in `config.py`, next to `DynamicUniverseConfig`:

```python
class AprioriConfig(BaseModel):
    """Catalog paper probes (see docs/apriori-alphas.md). Enabled means eligible to run;
    nothing trades unless an operator enrolled the catalog version and it is live."""

    enabled: bool = True
    pead_entry_path: str = "config/research/apriori/pead-v2.json"
    max_drift_cards_per_session: int = Field(default=1, ge=0)
```

  Add `apriori: AprioriConfig = Field(default_factory=AprioriConfig)` to `AppConfig`. Also add `catalog_event: dict[str, Any] | None = None` to `ScreenerCandidate`, after `probe`.

- [ ] **Step 4: Implement `screeners/earnings_drift.py`:**

```python
"""PEAD drift candidates for the 10:35 suggestion scan (catalog paper probe).

Events come from the study's own ``build_events`` via ``live_events``; the bracket is
the catalog policy (``apriori_bracket_v1``), never re-priced and never trailed. The
LLM writes commentary only (see ``RiskEvaluator``); ranking is by reaction z, never
``setup_quality``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentic_trader.constants import AssetClass
from agentic_trader.execution.lifetime_policy import SessionEvidenceError, SessionLifetimePolicy
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_live import live_events
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.indicators import calculate_ema, calculate_rsi

PEAD_STRATEGY_ID = "pead_long"
NEW_YORK = ZoneInfo("America/New_York")
_EXIT_CALENDAR_DAYS = 45


@dataclass(frozen=True)
class DriftPreparation:
    status: str
    session: date
    version_id: str | None = None
    policy: dict | None = None
    events: tuple[dict, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    reason: str | None = None
    report_date: str | None = None
    page_sha256: str | None = None
    time_exit_at: str | None = None


def _finite(value: Any, default: float) -> float:
    number = float(value)
    return number if math.isfinite(number) else default


class EarningsDriftService:
    def __init__(self, entry_path: Path, *, earnings, calendar, bars, static_symbols: Sequence[str]):
        self.loaded = load_pead_entry(Path(entry_path))
        self.earnings, self.calendar, self.bars = earnings, calendar, bars
        self.static_symbols = tuple(static_symbols)

    @property
    def decision_time_et(self) -> str:
        return self.loaded.entry.trade.decision_time_et

    def applies(self, scheduled_time_et: str | None) -> bool:
        return scheduled_time_et == self.decision_time_et

    def live_version(self, snapshot) -> dict | None:
        for definition in getattr(snapshot, "catalog_probes", ()):
            if definition.get("entry_sha256") == self.loaded.sha256 and definition.get("leg") == "LONG":
                return definition
        return None

    async def _time_exit(self, now: datetime, policy: dict) -> str | None:
        lifetime = SessionLifetimePolicy(**policy["lifetime"])
        start = now.astimezone(NEW_YORK).date()
        try:
            days = await self.calendar.get_calendar_range(start, start + timedelta(days=_EXIT_CALENDAR_DAYS))
            return lifetime.holding_deadline(now, [d for d in days if d.is_trading_day]).isoformat()
        except SessionEvidenceError, Exception:
            return None  # The card shows the hold length; the running service owns the real deadline.

    async def prepare(self, *, now: datetime, snapshot, owned: Mapping[str, str]) -> DriftPreparation:
        session = now.astimezone(NEW_YORK).date()
        version = self.live_version(snapshot)
        if version is None:
            return DriftPreparation("inactive", session, reason="no live catalog probe for this entry")
        result = await live_events(
            self.loaded.entry,
            session,
            earnings=self.earnings,
            calendar=self.calendar,
            bars=self.bars,
            static_symbols=self.static_symbols,
        )
        common = {
            "version_id": version["version_id"],
            "policy": version["execution"],
            "counts": result.counts,
            "report_date": result.report_date.isoformat() if result.report_date else None,
            "page_sha256": result.page.body_sha256 if result.page else None,
        }
        if result.status != "ok":
            return DriftPreparation("unavailable", session, reason=result.reason, **common)
        events, skipped = [], {}
        for event in result.events:
            reason = owned.get(event["symbol"])
            if reason:
                skipped[event["symbol"]] = reason
            else:
                events.append(event)
        return DriftPreparation(
            "ok",
            session,
            events=tuple(events),
            skipped=skipped,
            time_exit_at=await self._time_exit(now, version["execution"]),
            **common,
        )

    def candidate(self, event: dict, data: Any, prep: DriftPreparation) -> ScreenerCandidate | None:
        if data is None or isinstance(data, BaseException):
            return None
        intraday = getattr(data, "hourly", None)
        if intraday is None or intraday.empty:
            intraday = getattr(data, "four_hour", None)
        daily = getattr(data, "daily", None)
        if intraday is None or intraday.empty or daily is None or len(daily) < 20:
            return None
        price = float(intraday["Close"].iloc[-1])
        if not math.isfinite(price) or price <= 0:
            return None
        close = daily["Close"]
        exit_text = f"time exit {prep.time_exit_at[:10]} 15:45 NY" if prep.time_exit_at else "20-session time exit"
        return ScreenerCandidate(
            contract=event["symbol"],
            symbol=event["symbol"],
            asset_class=AssetClass.EQUITY,
            timeframe="1d",
            strategy=PEAD_STRATEGY_ID,
            direction="LONG",
            current_price=price,
            ema_20=_finite(calculate_ema(close, 20).iloc[-1], price),
            ema_50=_finite(calculate_ema(close, 50).iloc[-1], price),
            ema_200=_finite(calculate_ema(close, 200).iloc[-1], price),
            rsi_14=_finite(calculate_rsi(close, 14).iloc[-1], 50.0),
            atr_14=float(event["atr"]),
            candle_timestamp=str(intraday.index[-1]),
            recent_swing_low=float(daily["Low"].tail(10).min()),
            recent_swing_high=float(daily["High"].tail(10).max()),
            trigger_detail=(
                f"PEAD probe: EPS beat {event['surprise_pct']:+.1f}%, reaction {event['z']:+.1f}σ vs SPY "
                f"(report {event['report_date']}); 20-session hold, {exit_text}."
            ),
            alpha_version=prep.version_id,
            alpha_policy=prep.policy,
            probe=True,
            setup_quality=0.0,
            catalog_event=event,
        )


def drift_card_facts(event: dict, prep: DriftPreparation, holding_sessions: int) -> dict:
    return {
        "surprise_pct": event["surprise_pct"],
        "z": event["z"],
        "report_date": event["report_date"],
        "holding_sessions": holding_sessions,
        "time_exit_at": prep.time_exit_at,
    }
```

  In `_time_exit`, narrow `except (SessionEvidenceError, Exception)` to `except Exception`, and log a warning with `extra={"event": "pead_time_exit_unavailable"}`.

- [ ] **Step 5: Run the tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/screeners/test_earnings_drift.py tests/test_config*.py -q` (use whichever config test files exist).
  Expected: PASS.

- [ ] **Step 6: Stage and report.** Stage explicitly, then run pre-commit.

---

### Task 6: Scan integration, commentary-only LLM, card and journal

**Files:**
- Modify: `agentic_trader/agent/copilot.py`:
  - `__init__`: construct `EarningsDriftService`, injectable as `earnings_drift`;
  - `run_scan`: `scheduled_time_et`; the drift preparation, fetch, collect, budget, send, journal and notice;
  - new helpers `_prepare_drift`, `_admit_candidate`, `_journal_drift`.
- Modify: `agentic_trader/cli/commands/service.py:286-333` (pass `scheduled_time_et`)
- Modify: `agentic_trader/agent/evaluator.py:60-80` (`LLMTradeEvaluation.llm_verdict`) and `:725-727` (the commentary-only override)
- Modify: `agentic_trader/execution/durable.py:58-61` (`EventKind.PEAD_DECISION = "pead_decision"`)
- Modify: `agentic_trader/notifier/telegram_bot.py:93-225` (`format_alert_card(..., drift=None)`) and `:1172-1215` (`send_signal_alert(..., drift=None)`)
- Test: `tests/agent/test_scan_drift.py` (new)
- Test: `tests/agent/test_evaluator_catalog.py` (new; reuse the evaluator LLM test harness; find it with `grep -rln "litellm.acompletion" tests/agent`)
- Test: `tests/notifier/test_drift_card.py` (new; follow the existing `format_alert_card` tests)
- Test: `tests/cli/test_suggestion_scans.py` (append; find the existing `register_suggestion_scans` test with `grep -rln register_suggestion_scans tests`)

**Interfaces:**
- Consumes: Task 5's `EarningsDriftService`, `DriftPreparation`, `PEAD_STRATEGY_ID` and `drift_card_facts`; Task 3's `RegistrySnapshot.catalog_probes`; `synthetic_contract`.
- Produces:
  - `TradingCopilot.__init__(..., earnings_drift: EarningsDriftService | None = None)`
  - `run_scan(..., scheduled_time_et: str | None = None)`
  - the provenance key `"pead_event"` on drift signals, with `pead_event["session"]` as the New York date ISO string
  - the journal event `PEAD_DECISION` on stream `scan/<et_date>`, key `pead_decision/<scan_id>`
  - the outbox dedup key `pead-unavailable/<session ISO date>`

- [ ] **Step 1: Write the failing evaluator test** in `tests/agent/test_evaluator_catalog.py`. With the LLM mocked to return `{"approved": false, "rejection_reason": "earnings momentum fading", "thesis_summary": "Weak follow-through", ...}` for a candidate carrying `catalog_event={"symbol": "WINR"}` and an `apriori_bracket_v1` `alpha_policy`, assert:
  - `approved is True` and `rejection_reason is None`;
  - `llm_verdict == {"approved": False, "rejection_reason": "earnings momentum fading"}`;
  - `thesis_summary.startswith("LLM commentary (not a gate): Weak follow-through")`;
  - `stop_loss` and `take_profit` equal `bracket_prices(entry, 1, atr, …, policy)`.

  The same LLM response for a native candidate (`catalog_event=None`) stays `approved is False`.

- [ ] **Step 2: Implement the evaluator override.** Add `llm_verdict: dict[str, Any] | None = None` to `LLMTradeEvaluation`. Immediately before `return LLMTradeEvaluation(**data).model_copy(update={"earnings_note": earnings_note_value})`, insert:

```python
            if candidate.catalog_event is not None:
                # A catalog probe tests the unfiltered study rule: record the LLM's verdict,
                # show its text as commentary, never let it veto (deterministic gates already ran).
                data["llm_verdict"] = {
                    "approved": bool(data.get("approved")),
                    "rejection_reason": data.get("rejection_reason"),
                }
                data["approved"], data["rejection_reason"] = True, None
                data["thesis_summary"] = f"LLM commentary (not a gate): {data.get('thesis_summary') or ''}".strip()
```

  No prompt or template text changes.

- [ ] **Step 3: Write the failing card test** in `tests/notifier/test_drift_card.py`. Call `format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift={"surprise_pct": 12.34, "z": 1.83, "report_date": "2026-10-26", "holding_sessions": 20, "time_exit_at": "2026-11-24T20:45:00+00:00"})`. It must contain:
  - `"PAPER PROBE"`;
  - `"📈 <b>PEAD</b> — EPS beat +12.3%, reaction +1.8σ vs SPY (report 2026-10-26)"`;
  - `"⏱️ 20-session hold · time exit 15:45 NY on 2026-11-24 unless the stop or target fills first"`.

  With `time_exit_at=None`, the second line reads `"⏱️ 20-session hold · time exit 15:45 NY on session 20 unless the stop or target fills first"`. With `drift=None`, the output is byte-identical to today's output (call it both ways and compare).

- [ ] **Step 4: Implement the card.** Add `drift: dict[str, Any] | None = None` to `format_alert_card` and `send_signal_alert`, and pass it through. In `format_alert_card`, build:

```python
    drift_block = ""
    if drift:
        exit_day = _ny_date(drift.get("time_exit_at")) or f"session {drift['holding_sessions']}"
        drift_block = (
            f"📈 <b>PEAD</b> — EPS beat {drift['surprise_pct']:+.1f}%, reaction {drift['z']:+.1f}σ vs SPY "
            f"(report {html.escape(str(drift['report_date']))})\n"
            f"⏱️ {drift['holding_sessions']}-session hold · time exit 15:45 NY on {exit_day} "
            "unless the stop or target fills first\n\n"
        )
```

  `_ny_date(iso)` returns the New York date ISO string or None; add it beside `_ny_hhmm`. Prepend `drift_block` to `text`, before the probe header is applied, so the order is: probe header, drift block, card.

- [ ] **Step 5: Write the failing scan tests** in `tests/agent/test_scan_drift.py`. Build on the `scan_desk` fixture and the helpers in `tests/agent/test_scan_budget.py` (`frame`, `instrument`, `candidate`, `evaluation`, `_plant_signal`); import them. The fake drift service:

```python
class FakeDrift:
    decision_time_et = "10:35"

    def __init__(self, prep, candidates):
        self.prep, self.candidates, self.prepare_calls = prep, candidates, 0

    def applies(self, scheduled):
        return scheduled == "10:35"

    async def prepare(self, *, now, snapshot, owned):
        self.prepare_calls += 1
        self.owned = dict(owned)
        return self.prep

    def candidate(self, event, data, prep):
        return self.candidates.get(event["symbol"])
```

  A drift candidate is `candidate("WINR", 0.0, strategy="pead_long")`, updated with `timeframe="1d"`, `catalog_event={"symbol": "WINR", "session": <today NY>, "surprise_pct": 10.0, "z": 2.0, "report_date": "2026-10-26"}`, `alpha_version="apriori:pead:v2:long:abc"`, `alpha_policy={"kind": "apriori_bracket_v1"}` and `probe=True`. The prep is `DriftPreparation("ok", today, version_id=…, policy=…, events=({"symbol": "WINR", ...},))`. Set `copilot.earnings_drift = FakeDrift(...)`.

  Tests:
  - `test_drift_card_is_sent_beside_the_native_budget`:
    - `run_scan(use_llm=True, budget=ScanBudget.FULL, shadow_evidence=True, scheduled_time_et="10:35")` with the `budget_desk` universe records the native winner `DDD` and `WINR`;
    - the `WINR` provenance has `pead_event.symbol == "WINR"`;
    - `summary["drift"]["sent"] == 1`;
    - a `pead_decision` event exists on stream `scan/<today>` whose payload event `WINR` has `outcome == "sent"`.
  - `test_drift_runs_only_at_the_decision_time`: `scheduled_time_et="14:35"`, `None`, a `symbols=["AAA"]` scan, and `dry_run=True` each leave `prepare_calls == 0` with no `pead_long` signal.
  - `test_drift_budget_is_derived_after_a_restart`: `_plant_signal` a `pead_long` signal today (extend `_plant_signal` locally with a `strategy` argument), then run gives no new `WINR` signal and the WINR outcome `"drift budget spent"`. Native cards still send.
  - `test_native_budget_excludes_drift_signals`: plant two `pead_long` signals today with `max_drift_cards_per_session=3`. The native session budget (2) is unaffected: two FULL scans still send one native card each.
  - `test_owned_symbols_reach_prepare`: with an open position in `WINR` (`db.record_signal` plus `update_signal_execution` via the helpers the existing tests use), the fake records `owned == {"WINR": "open position", …}`.
  - `test_unavailable_session_sends_one_notice_and_native_cards_continue`:
    - the prep is `DriftPreparation("unavailable", today, reason="calendar_empty", report_date="2026-10-26")`;
    - two runs produce exactly one outbox notification with dedup key `pead-unavailable/<today>` whose text contains `"calendar_empty"`;
    - the native card is still sent.
  - `test_drift_candidate_rejected_by_a_deterministic_gate_is_journaled`: the evaluator returns `approved=False` with `"risk"` for `WINR` in the collect phase, giving journal outcome `"rejected: risk"` and no signal.
  - `test_drift_is_absent_from_the_shadow_ranking_journal`: the `scan_candidates_ranked` payload has no `WINR`.

- [ ] **Step 6: Implement the wiring in `copilot.py`.**
  1. **Construct the service in `__init__`.** Add the `earnings_drift: EarningsDriftService | None = None` parameter. After `self.session_provider` exists:

```python
self.earnings_drift = earnings_drift
self._earnings_drift_error: str | None = None
if earnings_drift is None and config.apriori.enabled:
    try:
        static_symbols = sorted(
            c
            for c, info in config.contracts.items()
            if normalize_asset_class(str(info.asset_class)) == normalize_asset_class(AssetClass.EQUITY)
        )
        self.earnings_drift = EarningsDriftService(
            Path(config.apriori.pead_entry_path),
            earnings=self.earnings_calendar,
            calendar=self.session_provider.calendar,
            bars=AlpacaDataProvider(
                api_key=config.alpaca_api_key,
                api_secret=config.alpaca_api_secret,
                feed="sip",
                request_timeout=config.market_data.timeout_seconds,
            ),
            static_symbols=static_symbols,
        )
    except Exception as exc:
        self._earnings_drift_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "PEAD drift source unavailable: %s",
            self._earnings_drift_error,
            extra={"event": "earnings_drift_unavailable"},
        )
```

     In `tests/agent/conftest.py::scan_desk`, add `copilot.earnings_drift = None` so existing scan tests stay drift-free.
  2. **`_admit_candidate` helper.** Extract the native collect loop's body from `# Deduplication check` through `approved_candidates.append(...)` into:

```python
    async def _admit_candidate(self, candidate, *, dedup_exempt_setups, current_exposure, active_positions, dry_run):
        """Dedup, account risk and the deterministic evaluation shared by native and drift candidates.

        Returns ``((candidate, det_res, account_risk), None)`` or ``(None, reason)``, where
        reason is ``"duplicate"``, ``"account risk unavailable: …"`` or ``"rejected: …"``.
        """
```

     Keep every log line. The native loop then does:
     - `admitted, reason = await self._admit_candidate(...)`;
     - on `"duplicate"`, append to `summary["duplicates"]` as before;
     - on `reason.startswith("account risk unavailable")`, `scan_errors += 1`;
     - otherwise `approved_candidates.append(admitted)` when `admitted`.

     Existing tests in `tests/agent/test_scan_*.py` pin this behaviour and must stay green unchanged.
  3. **Prepare before selection.** Before building `selected`:

```python
drift_prep = None
drift_only: set[str] = set()
if (
    not dry_run
    and shadow_evidence
    and not symbols
    and timeframe is None
    and self.earnings_drift is not None
    and self.earnings_drift.applies(scheduled_time_et)
):
    drift_prep = await self._prepare_drift(alpha_snapshot, active_positions)
    known = {c.strip("/").upper() for c in self.config.contracts} | {s for s, _ in dynamic_contracts}
    for event in drift_prep.events:
        if event["symbol"] not in known:
            drift_only.add(event["symbol"])
    dynamic_contracts = [*dynamic_contracts, *((s, synthetic_contract(s, s)) for s in sorted(drift_only))]
```

     `_prepare_drift` builds `owned`:
     - `{p["contract"].strip("/").upper(): "open position"}` for each active position;
     - plus `{symbol: f"owned by {d.alpha_id}"}` for every `d` in `(*alpha_snapshot.active, *alpha_snapshot.probe)` and each of its `eligible_symbols`.

     It calls `self.earnings_drift.prepare(now=datetime.now(UTC), snapshot=alpha_snapshot, owned=owned)`. Any exception becomes `DriftPreparation("unavailable", <NY date>, reason=f"error: {type(exc).__name__}")`, logged with `logger.exception`.
  4. **Collect.** In the per-contract collect loop, `if contract in drift_only: continue` goes first, before shadow observation and strategy scanning. These names are fetched only for their drift candidate. After the loop:

```python
drift_ranked: list[tuple[Any, Any, Any]] = []
drift_outcomes: dict[str, str] = {}
if drift_prep is not None and drift_prep.status == "ok":
    for event in drift_prep.events:
        symbol = event["symbol"]
        cand = self.earnings_drift.candidate(event, datasets.get(symbol), drift_prep)
        if cand is None:
            drift_outcomes[symbol] = "no intraday price"
            continue
        admitted, reason = await self._admit_candidate(
            cand,
            dedup_exempt_setups=dedup_exempt_setups,
            current_exposure=current_exposure,
            active_positions=active_positions,
            dry_run=dry_run,
        )
        if admitted is None:
            drift_outcomes[symbol] = reason
        else:
            drift_ranked.append(admitted)
```

  5. **Rank, budget and send.**
     - Keep `ranked` for the native entries sorted as today, and set `native_count = len(ranked)`. Compute `shadow_by_rank` over the native list only, then set `ranked = [*ranked, *drift_ranked]`, `shadow_by_rank += [None] * len(drift_ranked)` and `outcomes = [None] * len(ranked)`.
     - `summary["approved"]` stays the native count.
     - In the budgeted branch:

```python
native_today = [row for row in today if row["strategy"] != PEAD_STRATEGY_ID]
remaining_session = max(0, cfg.max_cards_per_session - len(native_today))
remaining_drift = max(
    0,
    self.config.apriori.max_drift_cards_per_session - sum(1 for row in today if row["strategy"] == PEAD_STRATEGY_ID),
)
```

       `groups_used` still counts every row in `today`. In the NONE branch set `remaining_drift = 0`; drift never runs there.
     - In the send loop, set `is_drift = candidate.catalog_event is not None` and replace the reason chain with:

```python
                    reason = None
                    if is_drift and remaining_drift <= 0:
                        reason = "drift budget spent"
                    elif not is_drift and remaining_scan <= 0:
                        reason = "per-scan budget spent"
                    elif not is_drift and remaining_session <= 0:
                        reason = "per-session budget spent"
                    elif any(groups_used.get(g, 0) >= cfg.max_cards_per_group_per_session for g in capped_groups(candidate.contract)):
                        reason = "correlation group already has a card this session"
                    elif use_llm and not is_drift and llm_budget <= 0:
                        reason = "LLM evaluation budget spent"
```

       Change `if use_llm: llm_budget -= 1` to `if use_llm and not is_drift:`.
     - In `record_signal`'s provenance, `"rank"` is `rank if not is_drift else rank - native_count`, and `"candidates_considered"` is `native_count if not is_drift else len(drift_ranked)`. Add `**({"pead_event": candidate.catalog_event} if is_drift else {})`. In the notification dict add:
       `**({"drift": drift_card_facts(candidate.catalog_event, drift_prep, self.earnings_drift.loaded.entry.trade.max_hold_sessions)} if is_drift else {})`.
     - Budget charge: `if is_drift: remaining_drift -= 1`, else the existing `remaining_scan -= 1; remaining_session -= 1`. The group update is unchanged.
     - `_journal_scan_ranking` receives `ranked=ranked[:native_count]`, `outcomes=outcomes[:native_count]` and `shadow_by_rank=shadow_by_rank[:native_count]`, guarded by `native_count` instead of `ranked`.
     - After the send loop:

```python
            if drift_prep is not None:
                for index, (cand, _det, _risk) in enumerate(drift_ranked, start=native_count):
                    drift_outcomes[cand.contract] = outcomes[index] or "not sent"
                summary["drift"] = {
                    "status": drift_prep.status,
                    "reason": drift_prep.reason,
                    "events": len(drift_prep.events),
                    "sent": sum(1 for v in drift_outcomes.values() if v == "sent"),
                }
                await self._journal_drift(scan_id=scan_id, prep=drift_prep, outcomes=drift_outcomes, summary=summary)
```

  6. **`_journal_drift`.** Mirror `_journal_dynamic_universe`: one transaction, never raising.

```python
payload = {
    "scan_id": scan_id,
    "session": prep.session.isoformat(),
    "status": prep.status,
    "reason": prep.reason,
    "version_id": prep.version_id,
    "report_date": prep.report_date,
    "page_sha256": prep.page_sha256,
    "counts": prep.counts,
    "skipped": prep.skipped,
    "time_exit_at": prep.time_exit_at,
    "events": [{**event, "outcome": outcomes.get(event["symbol"], "not considered")} for event in prep.events],
}
async with self.db.session_factory() as session, session.begin():
    await workflows.lock(session)
    await workflows.append(
        session,
        stream=f"scan/{prep.session.isoformat()}",
        kind=EventKind.PEAD_DECISION,
        payload=payload,
        key=f"pead_decision/{scan_id}",
    )
    if prep.status == "unavailable":
        await workflows.add_notification(
            session,
            f"pead-unavailable/{prep.session.isoformat()}",
            NotificationKind.MESSAGE,
            {
                "text": f"🧪 PEAD probe: no drift card this session — {prep.reason} "
                f"(report date {prep.report_date or 'unknown'}). Native cards are unaffected.",
                "formatted": False,
            },
        )
```

     Catch exceptions into `summary["drift_journal_error"]` and log `"pead_decision_journal_failed"`. `json` must be able to encode `counts`. `build_events` counts are plain dicts of ints and strings; if a test shows numpy values, wrap `counts` with `event_document`.
  7. **Scheduling.** In `service.py`:
     - `run_suggestion_scan(digest: bool = False, scheduled_time_et: str | None = None)` passes `scheduled_time_et=scheduled_time_et` to `run_scan`;
     - `register_suggestion_scans` uses `kwargs={"digest": index == len(times) - 1, "scheduled_time_et": item}`.

     Test (append to the existing `register_suggestion_scans` test file): the registered job kwargs include `scheduled_time_et` equal to each configured time.

- [ ] **Step 7: Run the tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/agent tests/notifier tests/cli -q`
  Then the full suite: `env -u VIRTUAL_ENV uv run pytest -q`
  Expected: PASS, including every existing `tests/agent/test_scan_*.py`.

- [ ] **Step 8: Stage and report.** Stage explicitly, then run pre-commit.

---

### Task 7: Probe measurement in `cards outcomes`, and documentation

**Files:**
- Create: `agentic_trader/research/apriori/probe_outcomes.py`
- Modify: `agentic_trader/cli/commands/cards.py` (the probe section in `outcomes_cmd`)
- Modify docs:
  - `docs/apriori-alphas.md` (Part 2: probe operation)
  - `docs/alpha-trade-lifetimes.md` (`session_count_v1`)
  - `docs/alpha-roadmap.md` (item 9 progress)
  - `docs/production.md` (enrol/renew/demote runbook lines)
  - `docs/entry-capacity.md` (not needed; leave it)
  - `CLAUDE.md` (the a priori paragraph)
- Test: `tests/research/apriori/test_probe_outcomes.py` (new)

**Interfaces:**
- Consumes:
  - `EventKind.PEAD_DECISION` payloads (Task 6);
  - `label_events` and `EVENT_COLUMNS`;
  - `load_pead_entry`;
  - `AlphaRepository.probe_report()` rows with `kind == "apriori"` (Task 3);
  - `SignalDatabase` scope helpers.
- Produces:
  - `decision_frame(payloads: list[dict]) -> pd.DataFrame`: one row per journaled LONG event, typed back (`session` and `report_date` as `date`, `decision_at` as aware `datetime`), with `outcome`
  - `summarize_probe(decisions: pd.DataFrame, labels: pd.DataFrame, signals: list[dict], forward: dict | None, study_mean_r: float | None) -> dict`
  - `async probe_signals(db, since: datetime) -> list[dict]`: `pead_long` signals in scope, with `status`, `executed_at`, `realized_pnl` and `risk_dollars`

- [ ] **Step 1: Write the failing tests** in `tests/research/apriori/test_probe_outcomes.py`:
  - `decision_frame` round-trips two payloads (one `ok` with events `sent` and `rejected: risk`, one `unavailable` with no events) into 2 rows with the right dtypes. A duplicate `scan_id` payload is counted once.
  - `summarize_probe` on 4 journaled events has:
    - `outcome` `sent`×2 and `drift budget spent`×2;
    - labels with `r_cost` 1.0, −1.0, 3.0 (mature) and one immature (absent from labels);
    - signals: one executed and closed with pnl −100 on risk 100, and one expired.

    It returns:
    - `{"events": 4, "carded": 2, "executed": 1, "closed": 1}`;
    - `realized_mean_r == -1.0`;
    - `counterfactual == {"mature": 3, "mean_r_cost": 1.0}`;
    - `forward` passed through;
    - `study_mean_r` passed through;
    - `"note"` saying that realized and counterfactual are not comparable until at least 10 closed trades.

- [ ] **Step 2: Implement `probe_outcomes.py`.**
  - **`decision_frame`:** dedupe by `scan_id`, explode `events`, and parse `session`, `report_date` and `decision_at` with `date.fromisoformat` and `datetime.fromisoformat`.
  - **Labels:** in the CLI, call `label_events(frame[EVENT_COLUMNS], hourly, entry)` and keep rows with `direction == "LONG"` and `is_leg`.
  - **`summarize_probe`:**
    - counts from `decisions["outcome"]`;
    - `executed` = signals with a non-null `executed_at`;
    - `closed` = statuses in `CLOSED_WIN`, `CLOSED_LOSS`, `CLOSED_MANUAL`;
    - `realized_mean_r` = the mean of `realized_pnl / risk_dollars` over closed signals, or None;
    - `counterfactual` = `{"mature": len(labels), "mean_r_cost": labels["r_cost"].mean()}`, or None when labels are empty.
  - **`probe_signals`:** a `select(SignalRecord…)` with `*db._scope()`, `SignalRecord.strategy == "pead_long"` and `SignalRecord.timestamp >= since`.

- [ ] **Step 3: Extend the CLI.** In `outcomes_cmd`, after the existing output:

```python
payloads = await _pead_decision_events(db, days, now=now)
if payloads:
    entry = load_pead_entry(Path(config.apriori.pead_entry_path)).entry
    decisions = decision_frame(payloads)
    sip = AlpacaDataProvider(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        feed="sip",
        request_timeout=config.market_data.timeout_seconds,
    )
    hourly = {}
    for symbol, group in decisions.groupby("symbol"):
        start = datetime.combine(min(group["session"]) - timedelta(days=7), time.min, tzinfo=UTC)
        hourly[symbol] = await asyncio.to_thread(sip.fetch_bars, symbol, "1h", start, now, adjustment="all")
    labels, _ = await asyncio.to_thread(label_events, decisions[list(EVENT_COLUMNS)], hourly, entry)
    labels = labels[(labels["direction"] == "LONG") & labels["is_leg"]] if not labels.empty else labels
    report = [r for r in await AlphaRepository(db.workflows).probe_report(now=now) if r.get("kind") == "apriori"]
    summary = summarize_probe(
        decisions,
        labels,
        await probe_signals(db, now - timedelta(days=days)),
        report[0]["forward"] if report else None,
        report[0]["study_mean_r"] if report else None,
    )
    click.echo("PEAD probe:")
    click.echo(json.dumps(summary, indent=2, default=str))
```

  Here `_pead_decision_events` mirrors `_scan_ranked_events` with `EventKind.PEAD_DECISION`. A bar fetch failure for one symbol puts it in `summary["bar_failures"]` (wrap each fetch) and never aborts the report. Pace fetches with `config.market_data.max_requests_per_minute`, as `label_journaled` does. Look at how `research/setups/outcomes.label_journaled` paces and reuse that helper.

- [ ] **Step 4: Write the docs.**
  - `docs/apriori-alphas.md`: add a "Part 2: live paper probe" section covering:
    - the policy kind, the time exit, the budget, commentary-only LLM, fail-closed behaviour, enrolment, measurement, and known limits (the entry fill, survivorship, the Nasdaq dependency);
    - the enrol command:
      `copilot alpha apriori-probe config/research/apriori/pead-v2.json --study ~/agentic-trader-research/apriori-pead-v2-20260926 --leg long --generation N --actor <operator>`.
  - `docs/alpha-trade-lifetimes.md`: a `session_count_v1` subsection (fill session = session 1; 15:45 New York or close − 15 min; the calendar is required; REVIEW on missing evidence; old policies unchanged).
  - `docs/alpha-roadmap.md` item 9: "Part 2 delivered (PR #…): catalog probe lane, awaiting operator enrolment and the first term's evidence."
  - `docs/production.md`: runbook lines for enrol, renew, demote and reading `/alphas` and `cards outcomes`.
  - `CLAUDE.md`: extend the a priori paragraph with one sentence:
    > "A passing leg runs only as a catalog paper probe (`alpha apriori-probe`): registry `probe` list, `probe_block_reason` liveness, `apriori_bracket_v1` fixed bracket with `session_count_v1` time exit, LLM commentary only, one drift card per session outside the native budget, fail-closed on Nasdaq/SIP gaps."

- [ ] **Step 5: Run the tests.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/research/apriori tests/cli -q`, then `env -u VIRTUAL_ENV uv run pytest -q`
  Expected: PASS.

- [ ] **Step 6: Stage and report.** Stage explicitly, then run pre-commit.

---

## After all tasks (controller)

1. Final whole-branch review on the most capable model. Then the full suite, the PostgreSQL integration tests (`tests/integration --run-postgres`) and pre-commit.
2. Open the PR `feat/apriori-pead-probe`, which carries the spec, the plan and the docs. Merge only on the operator's approval.
3. Controlled restart:
   1. `launchctl bootout` the watchdog, then the copilot.
   2. `git pull --ff-only` and `uv sync` in the installed checkout.
   3. Bootstrap the copilot, then the watchdog.
   4. Poll `/readyz` on port 9108.
   5. Run `scripts/verify_runtime.py`.
4. The operator enrols the probe with `alpha apriori-probe` (it requires confirmation). Verify that `/alphas` shows `pead_long (any liquid reporter)`.
5. Next 10:35 New York session: confirm a `pead_decision` journal event (ok, empty or unavailable) and, if an event qualified, the drift card in Telegram.
