# Regime and recurrence as measured context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Journal the regime each universe scan observed, derive the SPY tape at every decision in the outcomes report, count each setup's recurrence, and attribute all three in `cards outcomes` as descriptive blocks — never a gate.

**Architecture:** `agent/regime.py` gains a pure `regime_journal_block`; `run_scan` computes it once and writes it at the top level of `scan_candidates_ranked` and into every card's `decision_provenance`. A new pure module `research/setups/tape.py` turns SPY daily closes before a session into `TapeState` and bucket names. `research/setups/outcomes.py` reads the journaled block, fetches SPY daily once, computes the tape per session, adds recurrence columns over the report's own rows (`with_recurrence`, no lookahead) and two summary blocks (`regime_context`, `recurrence`). The CLI is unchanged.

**Tech Stack:** Python 3.14, `uv`, pandas, pytest (network blocked, fakes only), ruff strict, mypy.

**Spec:** `docs/superpowers/specs/2026-10-14-regime-recurrence-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-regime`; never touch `/Users/adamhadani/Development/agentic-trader`.
- ruff strict: no `dict()`/`list()` calls where a literal works (C408; `dict.fromkeys(...)` is fine), imports at module top (PLC0415), no unused unpacked variables (RUF059), no builtin shadowing (A002), `int | float` not `(int, float)` in `isinstance` (UP038). mypy must pass. `env -u VIRTUAL_ENV uv run pre-commit run --all-files` must pass before each commit; the ruff-format hook also reformats fenced code in Markdown — re-add and recommit when it changes a doc.
- No new schema, migration or `EventKind`; nothing in `agentic_trader/risk`, `execution/card_policy.py`, `research/setups/card_stats.py`, admission or `run_scan`'s ordering reads regime, tape or recurrence. `research/setups/features.py` does not change.
- Every journaled value is JSON-safe (`allow_nan=False`): floats through `finite_or_none`, enums as their `.value`, datetimes as ISO strings.
- Existing summary blocks of `summarize` and the existing columns of `_COLUMNS` keep their names, order and semantics; new columns go after `news_alignment` and before `market_r`.
- Implementers STAGE their files and report; they never commit and never dispatch subagents.

## Review Focus

1. An event whose `regime` is not a dict (a string, a list) → regime columns `None`, no crash (Task 3 `test_non_dict_regime_block_is_absent`).
2. A SPY daily frame with a naive index, or a bar stamped on the decision's own New York date → the naive index is read as UTC, the same-day bar is excluded (Task 2 `test_tape_state_excludes_the_decision_session_and_reads_naive_index_as_utc`).
3. Two journaled rows of one setup with identical `decided_at` (one scan journaled twice) → neither is the other's prior (Task 4 `test_recurrence_same_session_repeats_and_ties_on_decided_at_are_not_prior`).
4. A prior row of the same setup still open (immature, or resolved after this decision) → `prior_outcome == "none"` (Task 4 `test_recurrence_prior_outcome_ignores_brackets_open_at_the_decision`).
5. The regime detector stub without fields (`SimpleNamespace(summary_text="test")`) → an all-`None` block, scan and cards unchanged (Task 1 `test_block_from_a_stub_is_all_none`, `test_journal_and_provenance_carry_the_regime_block`).

---

### Task 1: `regime_journal_block` and the scan wiring

**Files:**
- Modify: `agentic_trader/agent/regime.py` (add after `classify_vix_level`)
- Modify: `agentic_trader/agent/copilot.py` (`run_scan` ~line 780 after `regime = await self.regime_detector.get_regime()`; the `decision_provenance` dict ~line 1460; the `_journal_scan_ranking` call ~line 1590; `_journal_scan_ranking` signature and payload ~lines 1848–1905; the import at line 31)
- Test: `tests/agent/test_regime_journal_block.py` (new), `tests/agent/test_scan_shadow_ranker.py` (one new test)

**Interfaces:**
- Produces: `agentic_trader.agent.regime.REGIME_BLOCK_KEYS: tuple[str, ...]` and `regime_journal_block(snapshot: Any) -> dict[str, Any]`; `scan_candidates_ranked` payload key `regime` (top level); `decision_provenance["regime"]` on every card recorded by `run_scan`.
- Consumes: `agentic_trader.research.setups.ranker.finite_or_none` (import-cycle free: `ranker` imports only `features`, which imports `research.alpha.panel`; nothing under `research/setups` except `replay.py` imports `agent`, and `regime.py` does not import `replay`).

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/agent/test_regime_journal_block.py
from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace

from agentic_trader.agent.macro import MacroStressLevel
from agentic_trader.agent.regime import REGIME_BLOCK_KEYS, RegimeSnapshot, regime_journal_block
from agentic_trader.constants import VolatilityRegime


def _snapshot(**overrides):
    fields = {
        "vix": 17.25,
        "vix_regime": VolatilityRegime.NORMAL,
        "tnx": 4.1,
        "dxy": 102.3,
        "breakout_allowed": True,
        "min_rr_threshold": 1.5,
        "timestamp": datetime(2026, 10, 14, 14, 35, tzinfo=UTC),
        "summary_text": "VIX: 17.25 (NORMAL)",
        "macro_report": None,
        "risk_multiplier": 0.9,
        "macro_unavailable_reason": None,
    }
    return RegimeSnapshot(**{**fields, **overrides})


def test_block_has_exactly_the_declared_keys_and_json_safe_values():
    report = SimpleNamespace(stress=SimpleNamespace(level=MacroStressLevel.MODERATE))
    block = regime_journal_block(_snapshot(macro_report=report))
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert block == {
        "vix": 17.25,
        "vix_regime": "NORMAL",
        "breakout_allowed": True,
        "risk_multiplier": 0.9,
        "min_rr_threshold": 1.5,
        "macro_stress": "MODERATE",
        "observed_at": "2026-10-14T14:35:00+00:00",
    }


def test_block_without_a_macro_report_has_no_stress():
    block = regime_journal_block(_snapshot())
    assert block["macro_stress"] is None
    assert block["vix_regime"] == "NORMAL" and block["breakout_allowed"] is True


def test_block_from_a_stub_is_all_none():
    """The scan fixture's detector returns a namespace with only summary_text; nothing may raise."""
    block = regime_journal_block(SimpleNamespace(summary_text="test"))
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert all(value is None for value in block.values())


def test_non_finite_and_non_numeric_values_are_none():
    block = regime_journal_block(_snapshot(vix=math.nan, risk_multiplier="x", min_rr_threshold=math.inf))
    assert block["vix"] is None and block["risk_multiplier"] is None and block["min_rr_threshold"] is None


def test_a_raising_attribute_yields_an_all_none_block():
    class Broken:
        @property
        def vix(self):
            raise RuntimeError("boom")

    block = regime_journal_block(Broken())
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert all(value is None for value in block.values())
```

Append to `tests/agent/test_scan_shadow_ranker.py` (after `test_sent_card_provenance_has_shadow_block`; it uses the module's existing `shadow_desk`, `temp_db`, `_ranked_events`, `ScanBudget`, `ScanTrigger` names and needs `from agentic_trader.agent.regime import REGIME_BLOCK_KEYS` added to the imports):

```python
async def test_journal_and_provenance_carry_the_regime_block(shadow_desk, temp_db):
    """The scan-level regime is journaled once at the top level and copied into each card's provenance."""
    await shadow_desk.run_scan(
        use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True, trigger=ScanTrigger.SUGGESTION_SCAN
    )
    [event] = await _ranked_events(temp_db)
    payload = event["payload"]
    block = payload["regime"]
    # The fixture's detector is a bare namespace: every key present, every value None.
    assert tuple(block) == REGIME_BLOCK_KEYS
    assert all(value is None for value in block.values())
    assert all("regime" not in candidate for candidate in payload["candidates"])
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["regime"] == block
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_regime_journal_block.py tests/agent/test_scan_shadow_ranker.py::test_journal_and_provenance_carry_the_regime_block -q`
Expected: FAIL with `ImportError: cannot import name 'REGIME_BLOCK_KEYS'`.

- [ ] **Step 3: Implement `regime_journal_block`**

In `agentic_trader/agent/regime.py`, add `from typing import Any` to the imports, `from agentic_trader.research.setups.ranker import finite_or_none`, and after `classify_vix_level`:

```python
REGIME_BLOCK_KEYS: tuple[str, ...] = (
    "vix",
    "vix_regime",
    "breakout_allowed",
    "risk_multiplier",
    "min_rr_threshold",
    "macro_stress",
    "observed_at",
)


def _number(snapshot: Any, name: str) -> float | None:
    value = getattr(snapshot, name, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return finite_or_none(float(value))


def regime_journal_block(snapshot: Any) -> dict[str, Any]:
    """JSON-safe record of the regime a scan observed; every key present, missing values None.

    Scan-level evidence for ``scan_candidates_ranked`` and ``decision_provenance``: what the desk
    saw and gated on (the VIX bucket, breakout flag, risk multiplier, required reward/risk, macro
    stress) cannot be rebuilt from bars later, so it is journaled; the tape itself is derived by
    the outcomes report. Never raises: a detector stub or a partial snapshot yields None fields.
    """
    try:
        regime = getattr(snapshot, "vix_regime", None)
        level = getattr(getattr(getattr(snapshot, "macro_report", None), "stress", None), "level", None)
        allowed = getattr(snapshot, "breakout_allowed", None)
        timestamp = getattr(snapshot, "timestamp", None)
        return {
            "vix": _number(snapshot, "vix"),
            "vix_regime": str(getattr(regime, "value", regime)) if regime is not None else None,
            "breakout_allowed": bool(allowed) if allowed is not None else None,
            "risk_multiplier": _number(snapshot, "risk_multiplier"),
            "min_rr_threshold": _number(snapshot, "min_rr_threshold"),
            "macro_stress": str(getattr(level, "value", level)) if level is not None else None,
            "observed_at": timestamp.isoformat() if isinstance(timestamp, datetime) else None,
        }
    except Exception:
        return dict.fromkeys(REGIME_BLOCK_KEYS)
```

- [ ] **Step 4: Wire the scan**

In `agentic_trader/agent/copilot.py`:

1. Change the import at line 31 to `from agentic_trader.agent.regime import RegimeDetector, regime_journal_block`.
2. After `regime = await self.regime_detector.get_regime()` and its log line (~line 780–781), add:

```python
            regime_block = regime_journal_block(regime)
```

3. In the `decision_provenance` dict of `record_signal` (~line 1460, the dict that has `"news": self._news_block(eval_res),`), add directly after the `"news"` entry:

```python
                            "regime": regime_block,
```

4. In the `_journal_scan_ranking` call (~line 1590) add the keyword `regime_block=regime_block,` after `trigger=trigger,`.
5. In `_journal_scan_ranking`'s signature add `regime_block: dict[str, Any],` after `trigger: ScanTrigger,`, and in `payload` add, after the `"trigger"` entry:

```python
                # The regime the whole scan observed (scan-level, like trigger and budget); the
                # outcomes report copies it onto each candidate and buckets by it. Never a gate.
                "regime": regime_block,
```

- [ ] **Step 5: Run the tests and the touched suites**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_regime_journal_block.py tests/agent/test_scan_shadow_ranker.py tests/agent/test_card_stats_worker.py -q`
Expected: PASS (every existing test in those files still green).

- [ ] **Step 6: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader/agent/regime.py agentic_trader/agent/copilot.py tests/agent/test_regime_journal_block.py tests/agent/test_scan_shadow_ranker.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/agent/regime.py agentic_trader/agent/copilot.py tests/agent/test_regime_journal_block.py tests/agent/test_scan_shadow_ranker.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/agent/regime.py agentic_trader/agent/copilot.py`
Expected: clean. Then `git add agentic_trader/agent/regime.py agentic_trader/agent/copilot.py tests/agent/test_regime_journal_block.py tests/agent/test_scan_shadow_ranker.py` and report. The controller commits as `Journal the scan's regime block in scan_candidates_ranked and card provenance`.

---

### Task 2: `research/setups/tape.py` — the tape at a decision

**Files:**
- Create: `agentic_trader/research/setups/tape.py`
- Test: `tests/research/setups/test_tape.py`

**Interfaces:**
- Produces: `TAPE_HISTORY_DAYS = 400`, `RET_SESSIONS = 20`, `MEAN_SESSIONS = 200`, `TAPE_BUCKETS = ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")`, `TapeState(ret_20: float | None, above_200: bool | None, sessions: int)` (frozen dataclass), `tape_state(daily: pd.DataFrame | None, session: date) -> TapeState`, `tape_buckets(state: TapeState) -> tuple[str, ...]`.
- Consumes: `agentic_trader.market.session.ET_TZ`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/setups/test_tape.py
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.tape import (
    MEAN_SESSIONS,
    RET_SESSIONS,
    TAPE_BUCKETS,
    TAPE_HISTORY_DAYS,
    TapeState,
    tape_buckets,
    tape_state,
)


def _daily(closes: list[float], *, end: str = "2026-10-13", tz: str | None = "UTC") -> pd.DataFrame:
    """One bar per weekday ending on ``end`` (a session date), stamped 20:00 UTC = 16:00 New York."""
    index = pd.bdate_range(end=end, periods=len(closes), freq="B") + pd.Timedelta(hours=20)
    index = pd.DatetimeIndex(index, tz=tz) if tz else pd.DatetimeIndex(index)
    return pd.DataFrame({"Close": closes}, index=index)


def test_constants_are_the_spec_values():
    assert (TAPE_HISTORY_DAYS, RET_SESSIONS, MEAN_SESSIONS) == (400, 20, 200)
    assert TAPE_BUCKETS == ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")


def test_tape_state_computes_the_20_session_return_and_the_200_session_mean():
    closes = [100.0] * 199 + [110.0] * 21  # 220 sessions; last 21 at 110 → ret_20 == 0; mean(last 200) < 110
    state = tape_state(_daily(closes), date(2026, 10, 14))
    assert state.sessions == 220
    assert state.ret_20 == pytest.approx(0.0)
    assert state.above_200 is True


def test_tape_state_return_uses_exactly_21_closes():
    closes = [100.0] * 20 + [105.0]  # 21 closes: ret_20 == 105 / 100 - 1
    state = tape_state(_daily(closes), date(2026, 10, 14))
    assert state.ret_20 == pytest.approx(0.05)
    assert state.above_200 is None  # fewer than 200 sessions
    assert state.sessions == 21


def test_tape_state_short_history_is_none():
    state = tape_state(_daily([100.0] * 20), date(2026, 10, 14))
    assert state == TapeState(ret_20=None, above_200=None, sessions=20)


def test_tape_state_excludes_the_decision_session_and_reads_naive_index_as_utc():
    """A bar stamped on the decision's own New York date is never used; a naive index is UTC."""
    closes = [100.0] * 20 + [105.0, 999.0]  # the 999 bar is on 2026-10-13, the decision's session
    state = tape_state(_daily(closes, tz=None), date(2026, 10, 13))
    assert state.sessions == 21
    assert state.ret_20 == pytest.approx(0.05)


def test_tape_state_uses_new_york_dates_for_sessions():
    """A bar stamped 03:00 UTC on the 14th belongs to the New York session of the 13th."""
    frame = _daily([100.0] * 21, end="2026-10-13")
    late = pd.DataFrame({"Close": [200.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-10-14T03:00:00Z")]))
    state = tape_state(pd.concat([frame, late]), date(2026, 10, 14))
    assert state.sessions == 22
    assert state.ret_20 == pytest.approx(200.0 / 100.0 - 1.0)


def test_tape_state_empty_none_and_nan_frames():
    assert tape_state(None, date(2026, 10, 14)) == TapeState(None, None, 0)
    assert tape_state(pd.DataFrame(), date(2026, 10, 14)) == TapeState(None, None, 0)
    assert tape_state(_daily([math.nan] * 30), date(2026, 10, 14)) == TapeState(None, None, 0)
    mixed = _daily([100.0] * 20 + [math.nan] + [105.0])
    assert tape_state(mixed, date(2026, 10, 14)) == TapeState(ret_20=pytest.approx(0.05), above_200=None, sessions=21)


def test_tape_state_without_a_close_column_is_empty():
    frame = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-10-01T20:00:00Z")]))
    assert tape_state(frame, date(2026, 10, 14)) == TapeState(None, None, 0)


def test_tape_state_zero_base_close_gives_no_return():
    closes = [0.0] + [100.0] * 20
    assert tape_state(_daily(closes), date(2026, 10, 14)).ret_20 is None


def test_tape_state_unsorted_index_is_sorted_first():
    frame = _daily([100.0] * 20 + [105.0]).iloc[::-1]
    assert tape_state(frame, date(2026, 10, 14)).ret_20 == pytest.approx(0.05)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (TapeState(0.02, True, 250), ("ret20_up", "above_200")),
        (TapeState(-0.01, False, 250), ("ret20_down", "below_200")),
        (TapeState(0.0, True, 250), ("ret20_down", "above_200")),
        (TapeState(0.03, None, 30), ("ret20_up",)),
        (TapeState(None, False, 250), ("below_200",)),
        (TapeState(None, None, 0), ("unknown",)),
    ],
)
def test_tape_buckets(state, expected):
    assert tape_buckets(state) == expected
    assert set(expected) <= set(TAPE_BUCKETS)


def test_tape_buckets_never_mix_unknown_with_a_measure():
    assert "unknown" not in tape_buckets(TapeState(np.float64(0.1), None, 21))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_tape.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.setups.tape'`.

- [ ] **Step 3: Implement the module**

```python
# agentic_trader/research/setups/tape.py
"""The index tape at a decision: SPY's 20-session return and 200-session mean from completed sessions.

Pure and I/O-free. The outcomes report (``research/setups/outcomes.py``) fetches SPY daily bars
once and calls ``tape_state`` per decision session so every journaled candidate, including the
history before the regime block existed, gets the same past-only measures. Bucket names are
threshold-free (the sign of the return, the side of the mean), so nothing here can be fitted
to the sample. Descriptive only: never a gate, rule or ranking input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ


__all__ = [
    "MEAN_SESSIONS",
    "RET_SESSIONS",
    "TAPE_BUCKETS",
    "TAPE_HISTORY_DAYS",
    "TapeState",
    "tape_buckets",
    "tape_state",
]

# Calendar days of daily bars fetched before the earliest decision: about 275 sessions, enough
# for the 200-session mean with margin.
TAPE_HISTORY_DAYS = 400
RET_SESSIONS = 20
MEAN_SESSIONS = 200
TAPE_BUCKETS = ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")


@dataclass(frozen=True)
class TapeState:
    """SPY measures over the completed sessions strictly before a decision's session."""

    ret_20: float | None  # close[-1] / close[-21] - 1
    above_200: bool | None  # close[-1] > mean(close[-200:])
    sessions: int  # completed sessions with a finite close available before the session


def _closes_before(daily: pd.DataFrame, session: date) -> np.ndarray:
    index = pd.DatetimeIndex(daily.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame = daily.set_axis(index).sort_index()
    ny_dates = np.array(frame.index.tz_convert(ET_TZ).date, dtype=object)
    closes = pd.to_numeric(frame.loc[ny_dates < session, "Close"], errors="coerce")
    values = closes.to_numpy(dtype=float)
    return values[np.isfinite(values)]


def tape_state(daily: pd.DataFrame | None, session: date) -> TapeState:
    """The tape as of ``session``'s decision: only sessions whose New York date precedes it count.

    Today's bar, partial or complete, is never used (the decision is intraday). Rows without a
    finite close are dropped before counting. Fewer than 21 closes give no return; fewer than
    200 give no mean comparison. A missing, empty or close-less frame is ``(None, None, 0)``.
    """
    if daily is None or daily.empty or "Close" not in daily.columns:
        return TapeState(ret_20=None, above_200=None, sessions=0)
    values = _closes_before(daily, session)
    n = len(values)
    ret_20: float | None = None
    if n >= RET_SESSIONS + 1 and values[-RET_SESSIONS - 1]:
        candidate = float(values[-1] / values[-RET_SESSIONS - 1] - 1.0)
        ret_20 = candidate if math.isfinite(candidate) else None
    above_200 = bool(values[-1] > float(np.mean(values[-MEAN_SESSIONS:]))) if n >= MEAN_SESSIONS else None
    return TapeState(ret_20=ret_20, above_200=above_200, sessions=n)


def tape_buckets(state: TapeState) -> tuple[str, ...]:
    """The bucket names a state belongs to; ``("unknown",)`` only when both measures are absent."""
    names: list[str] = []
    if state.ret_20 is not None:
        names.append("ret20_up" if state.ret_20 > 0 else "ret20_down")
    if state.above_200 is not None:
        names.append("above_200" if state.above_200 else "below_200")
    return tuple(names) if names else ("unknown",)
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_tape.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader/research/setups/tape.py tests/research/setups/test_tape.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/research/setups/tape.py tests/research/setups/test_tape.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/setups/tape.py`
Expected: clean. `git add agentic_trader/research/setups/tape.py tests/research/setups/test_tape.py` and report. Commit message: `Add the pure SPY tape state and buckets for the outcomes report`.

---

### Task 3: Outcomes — regime columns, the SPY daily fetch, tape columns and `regime_context`

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py` (`_COLUMNS`, `_candidate_entries`, `_row`, `label_journaled`, `summarize`; new `_bucket`, `_tape_members`, `_regime_summary`)
- Test: `tests/research/setups/test_outcomes.py` (new tests), `tests/research/setups/test_card_stats.py` (one new test)

**Interfaces:**
- Consumes: Task 2's `TAPE_BUCKETS`, `TAPE_HISTORY_DAYS`, `TapeState`, `tape_buckets`, `tape_state`; the journal's top-level `regime` block (Task 1).
- Produces: columns `regime_vix`, `regime_vix_regime`, `regime_risk_multiplier`, `regime_macro_stress`, `spy_ret_20`, `spy_above_200`, `tape_reason` (and the placeholders `prior_sessions`, `prior_same_session`, `prior_outcome`, filled by Task 4) after `news_alignment`; `label_journaled(..., tape_symbol: str | None = MARKET_PROXY_SYMBOL)`; `_bucket(rows) -> {"n", "mean_r_cost", "mean_excess_r", "hit_rate"}`; `summarize(...)["regime_context"]`. Task 4 reuses `_bucket`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/research/setups/test_outcomes.py` (the module already defines `_bars`, `FakeBarSource`, `_candidate`, `_event`, `DECIDED_AT`, `NOW`; import `TAPE_HISTORY_DAYS` from `agentic_trader.research.setups.tape` and `timedelta` is already imported):

```python
def _regime_block(**overrides):
    block = {
        "vix": 17.25,
        "vix_regime": "NORMAL",
        "breakout_allowed": True,
        "risk_multiplier": 0.9,
        "min_rr_threshold": 1.5,
        "macro_stress": "LOW",
        "observed_at": "2026-03-02T14:00:00+00:00",
    }
    return {**block, **overrides}


def _event_with_regime(scan_id, decided_at, candidates, regime):
    event = _event(scan_id, decided_at, candidates)
    if regime is not None:
        event["payload"]["regime"] = regime
    return event


def _spy_daily(closes: list[float], *, end: str) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=len(closes), freq="B") + pd.Timedelta(hours=20)
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(index, tz="UTC"))


_RESOLVING_BARS = _bars(
    [
        ("2026-03-02T14:30:00Z", 100.0, 100.5, 99.5, 100.2),
        ("2026-03-02T15:30:00Z", 100.2, 102.5, 100.0, 102.2),
    ]
)
_SPY_HOURLY = _bars(
    [
        ("2026-03-02T14:30:00Z", 500.0, 501.0, 499.0, 500.5),
        ("2026-03-02T15:30:00Z", 500.5, 502.0, 500.0, 501.0),
    ]
)
# One bar that hits neither level and no later bars: the bracket is still open (IMMATURE).
_OPEN_BARS = _bars([("2026-03-02T14:30:00Z", 100.0, 100.5, 99.5, 100.2)])


class DailyAwareBarSource(FakeBarSource):
    """Serves ``frames[symbol]`` for hourly calls and ``daily[symbol]`` for ``1d`` calls."""

    def __init__(self, frames, daily, *, raise_for=None, raise_daily_for=None):
        super().__init__(frames, raise_for=raise_for)
        self.daily = daily
        self.raise_daily_for = raise_daily_for or set()

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        if timeframe == "1d":
            self.calls.append((symbol, timeframe, start, end, adjustment))
            if symbol in self.raise_daily_for:
                raise RuntimeError(f"no daily for {symbol}")
            return self.daily[symbol]
        return super().fetch_bars(symbol, timeframe, start, end, adjustment=adjustment)


def test_regime_columns_come_from_the_event_block_and_are_none_without_one():
    events = [
        _event_with_regime("s1", DECIDED_AT, [_candidate(contract="AAPL")], _regime_block()),
        _event_with_regime("s2", DECIDED_AT + timedelta(hours=1), [_candidate(contract="MSFT")], None),
    ]
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "MSFT": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()}
    )
    frame = label_journaled(events, source, now=NOW).set_index("contract")
    assert frame.loc[
        "AAPL", ["regime_vix", "regime_vix_regime", "regime_risk_multiplier", "regime_macro_stress"]
    ].tolist() == [
        17.25,
        "NORMAL",
        0.9,
        "LOW",
    ]
    assert frame.loc[
        "MSFT", ["regime_vix", "regime_vix_regime", "regime_risk_multiplier", "regime_macro_stress"]
    ].tolist() == [
        None,
        None,
        None,
        None,
    ]


def test_non_dict_regime_block_is_absent():
    events = [_event_with_regime("s1", DECIDED_AT, [_candidate()], "NORMAL")]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW)
    assert frame["regime_vix_regime"].tolist() == [None]
    assert summarize(frame)["regime_context"]["blocks"] == 0


def test_tape_fetch_is_one_raw_daily_request_after_the_hourly_ones():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    daily = _spy_daily([100.0] * 20 + [105.0], end="2026-02-27")  # last completed session before 2026-03-02
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": daily})
    frame = label_journaled(events, source, now=NOW)
    daily_calls = [call for call in source.calls if call[1] == "1d"]
    assert len(daily_calls) == 1
    symbol, _, start, end, adjustment = daily_calls[0]
    assert (symbol, adjustment, end) == ("SPY", "raw", NOW)
    assert start == DECIDED_AT - timedelta(days=TAPE_HISTORY_DAYS)
    assert source.calls[-1] == daily_calls[0]
    assert frame["spy_ret_20"].tolist() == [pytest.approx(0.05)]
    assert frame["spy_above_200"].tolist() == [None]
    assert frame["tape_reason"].tolist() == [None]


def test_tape_symbol_none_skips_the_daily_fetch():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW, tape_symbol=None)
    assert not [call for call in source.calls if call[1] == "1d"]
    assert frame["spy_ret_20"].tolist() == [None] and frame["tape_reason"].tolist() == [None]


def test_tape_fetch_failure_sets_the_reason_and_leaves_the_rest_intact():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()}, raise_daily_for={"SPY"}
    )
    frame = label_journaled(events, source, now=NOW)
    assert frame["tape_reason"].tolist() == ["RuntimeError: no daily for SPY"]
    assert frame["spy_ret_20"].tolist() == [None] and frame["spy_above_200"].tolist() == [None]
    assert frame["hit"].tolist() == [BracketHit.TARGET.value]
    assert frame["market_r"].notna().all()
    summary = summarize(frame)
    assert summary["regime_context"]["tape_reason"] == "RuntimeError: no daily for SPY"
    assert summary["regime_context"]["by_tape"]["unknown"]["n"] == 1


def test_tape_is_computed_per_session_from_closes_before_it():
    later = DECIDED_AT + timedelta(days=1)  # 2026-03-03
    events = [
        _event("s1", DECIDED_AT, [_candidate(contract="AAPL")]),
        _event("s2", later, [_candidate(contract="MSFT")]),
    ]
    # 2026-02-27 is the last session before 03-02; 03-02's own close (300) counts only for 03-03.
    daily = _spy_daily([100.0] * 20 + [105.0, 300.0], end="2026-03-02")
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "MSFT": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": daily})
    frame = label_journaled(events, source, now=NOW).set_index("contract")
    assert frame.loc["AAPL", "spy_ret_20"] == pytest.approx(0.05)
    assert frame.loc["MSFT", "spy_ret_20"] == pytest.approx(2.0)  # 300 / close[-21] (100) - 1


def test_regime_context_buckets_mature_rows_only():
    stop_bars = _bars(
        [
            ("2026-03-02T14:30:00Z", 100.0, 100.2, 98.5, 98.8),
        ]
    )
    events = [
        _event_with_regime("s1", DECIDED_AT, [_candidate(contract="AAPL")], _regime_block()),
        _event_with_regime(
            "s2",
            DECIDED_AT + timedelta(hours=1),
            [_candidate(contract="XOM")],
            _regime_block(vix_regime="ELEVATED", macro_stress=None),
        ),
        _event_with_regime("s3", DECIDED_AT + timedelta(hours=2), [_candidate(contract="MSFT")], _regime_block()),
    ]
    daily = _spy_daily([100.0] * 20 + [105.0], end="2026-02-27")
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "XOM": stop_bars, "MSFT": _OPEN_BARS, "SPY": _SPY_HOURLY}, {"SPY": daily}
    )
    frame = label_journaled(events, source, now=NOW)
    block = summarize(frame)["regime_context"]
    assert block["blocks"] == 3  # MSFT is immature but has a block
    assert set(block["by_vix_regime"]) == {"NORMAL", "ELEVATED"}
    normal = block["by_vix_regime"]["NORMAL"]
    assert normal["n"] == 1 and normal["hit_rate"] == 1.0 and normal["mean_r_cost"] > 0
    assert normal["mean_excess_r"] == pytest.approx(
        normal["mean_r_cost"] - float(frame.set_index("contract").loc["AAPL", "market_r"])
    )
    assert block["by_vix_regime"]["ELEVATED"]["n"] == 1 and block["by_vix_regime"]["ELEVATED"]["hit_rate"] == 0.0
    assert set(block["by_macro_stress"]) == {"LOW"} and block["by_macro_stress"]["LOW"]["n"] == 1
    assert set(block["by_tape"]) == set(TAPE_BUCKETS)
    assert block["by_tape"]["ret20_up"]["n"] == 2  # both mature rows share the session's tape
    assert block["by_tape"]["ret20_down"]["n"] == 0 and block["by_tape"]["unknown"]["n"] == 0
    assert block["by_tape"]["above_200"]["n"] == 0  # fewer than 200 sessions: no mean comparison
    assert block["tape_reason"] is None


def test_new_columns_follow_news_alignment_and_precede_market_r():
    cols = list(_COLUMNS)
    expected = [
        "regime_vix",
        "regime_vix_regime",
        "regime_risk_multiplier",
        "regime_macro_stress",
        "spy_ret_20",
        "spy_above_200",
        "tape_reason",
        "prior_sessions",
        "prior_same_session",
        "prior_outcome",
    ]
    start = cols.index("news_alignment") + 1
    assert cols[start : start + len(expected)] == expected
    assert cols[start + len(expected)] == "market_r"


def test_empty_summary_has_the_regime_and_recurrence_blocks():
    summary = summarize(pd.DataFrame(columns=list(_COLUMNS)))
    assert summary["regime_context"] == {
        "blocks": 0,
        "by_vix_regime": {},
        "by_macro_stress": {},
        "by_tape": {
            name: {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None} for name in TAPE_BUCKETS
        },
        "tape_reason": None,
    }
    assert set(summary["recurrence"]) == {
        "setups",
        "repeated_setups",
        "same_session_repeats",
        "by_prior",
        "by_prior_outcome",
    }
```

Note for the implementer: `test_empty_summary_has_the_regime_and_recurrence_blocks` asserts the `recurrence` key set only; Task 4 fills its values. Until Task 4 lands, `summarize` must still return a `recurrence` block with those five keys (zero counts, empty buckets) — write it in this task with the placeholder columns, and Task 4 replaces the computation.

Add to `tests/research/setups/test_card_stats.py` (uses its existing `_labelled`, `_compute`, `TP`, `SQ`, `outcomes_module`):

```python
def test_regime_tape_and_recurrence_columns_are_inert_in_the_snapshot():
    """card_stats reads hit, r_cost, holding_sessions, strategy, direction and decided_at only."""
    frame = _labelled(TP + SQ)
    payload = _compute(frame).model_dump(mode="json")
    extra = (
        "regime_vix",
        "regime_vix_regime",
        "regime_risk_multiplier",
        "regime_macro_stress",
        "spy_ret_20",
        "spy_above_200",
        "tape_reason",
        "prior_sessions",
        "prior_same_session",
        "prior_outcome",
    )
    assert set(extra) <= set(outcomes_module._COLUMNS)
    stripped = _compute(frame.drop(columns=list(extra))).model_dump(mode="json")
    assert payload == stripped
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py -q`
Expected: the new tests FAIL (`KeyError: 'regime_vix'`, `TypeError: ... unexpected keyword argument 'tape_symbol'`, missing `regime_context`); every existing test still passes.

- [ ] **Step 3: Implement**

In `agentic_trader/research/setups/outcomes.py`:

1. Imports: `from datetime import UTC, date, datetime, timedelta` and
   `from agentic_trader.research.setups.tape import TAPE_BUCKETS, TAPE_HISTORY_DAYS, TapeState, tape_buckets, tape_state`.
   Add `"PRIOR_OUTCOMES"`, `"RESOLVED_HITS"`, `"with_recurrence"` to `__all__` only in Task 4; add nothing to `__all__` here.

2. `_COLUMNS`: insert after `"news_alignment",`:

```python
("regime_vix",)
("regime_vix_regime",)
("regime_risk_multiplier",)
("regime_macro_stress",)
("spy_ret_20",)
("spy_above_200",)
("tape_reason",)
("prior_sessions",)
("prior_same_session",)
("prior_outcome",)
```

3. `_candidate_entries`: after `scan_id = payload.get("scan_id")` add

```python
        raw_regime = payload.get("regime")
        regime = raw_regime if isinstance(raw_regime, dict) else None
```

   and extend the appended dict with `"regime": regime`.

4. `_row`: after the `score` line add `raw_regime = entry.get("regime")` / `regime: dict[str, Any] = raw_regime if isinstance(raw_regime, dict) else {}`, and after the `"news_alignment"` entry add

```python
        "regime_vix": regime.get("vix"),
        "regime_vix_regime": regime.get("vix_regime"),
        "regime_risk_multiplier": regime.get("risk_multiplier"),
        "regime_macro_stress": regime.get("macro_stress"),
        # The tape is filled per session by label_journaled once the SPY daily fetch is known.
        "spy_ret_20": None,
        "spy_above_200": None,
        "tape_reason": None,
        # Recurrence is filled over the whole frame by with_recurrence (Task 4); these are the
        # "first occurrence" defaults.
        "prior_sessions": 0,
        "prior_same_session": 0,
        "prior_outcome": "none",
```

5. `label_journaled`: add the keyword `tape_symbol: str | None = MARKET_PROXY_SYMBOL` after `market_symbol`; extend the docstring with one sentence: "The tape (SPY daily, `TAPE_HISTORY_DAYS` calendar days before the earliest decision, raw, one request after the hourly ones) is computed per decision session with `tape_state`; a failed fetch records `tape_reason` on every row." Change the inner `fetch` to take the timeframe:

```python
    def fetch(symbol: str, start: datetime, timeframe: str = _BAR_TIMEFRAME) -> pd.DataFrame | str:
        if pacer is not None:
            pacer.acquire()
        try:
            return bars.fetch_bars(symbol, timeframe, start, now, adjustment="raw")
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
```

   After the market-proxy block and before `rows: list[...] = []`, add:

```python
    daily: pd.DataFrame | None = None
    tape_reason: str | None = None
    if tape_symbol is not None and market_start is not None:
        tape = fetch(tape_symbol, market_start - timedelta(days=TAPE_HISTORY_DAYS), "1d")
        if isinstance(tape, str):
            tape_reason = tape
        else:
            daily = tape
```

   After the `rows` loop and before `frame = pd.DataFrame(...)`, add:

```python
    states: dict[str, TapeState] = {}
    for row in rows:
        session = row["session"]
        state = states.get(session)
        if state is None:
            state = states[session] = tape_state(daily, date.fromisoformat(session))
        row["spy_ret_20"], row["spy_above_200"], row["tape_reason"] = state.ret_20, state.above_200, tape_reason
```

   Extend the trailing dtype clean-up loop's tuple with `"regime_vix", "regime_vix_regime", "regime_risk_multiplier", "regime_macro_stress", "spy_ret_20", "spy_above_200", "tape_reason"` so absent values stay `None` (not NaN).

6. Summary helpers, after `_news_summary`:

```python
def _bucket(rows: pd.DataFrame) -> dict[str, Any]:
    """n, mean R after cost, mean excess over the market control (rows with one) and target rate."""
    if rows.empty:
        return {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None}
    excess = pd.to_numeric(rows["excess_r"], errors="coerce").dropna()
    return {
        "n": len(rows),
        "mean_r_cost": _mean_r_cost(rows),
        "mean_excess_r": float(excess.mean()) if len(excess) else None,
        "hit_rate": float((rows["hit"] == BracketHit.TARGET.value).mean()),
    }


def _tape_members(rows: pd.DataFrame, name: str) -> pd.Series:
    """Rows whose tape state belongs to bucket ``name`` -- the one membership rule, ``tape_buckets``."""
    if rows.empty:
        return pd.Series(dtype=bool, index=rows.index)

    def member(row: pd.Series) -> bool:
        ret = row["spy_ret_20"]
        above = row["spy_above_200"]
        state = TapeState(
            ret_20=float(ret) if ret is not None and not pd.isna(ret) else None,
            above_200=bool(above) if above is not None and not pd.isna(above) else None,
            sessions=0,
        )
        return name in tape_buckets(state)

    return rows.apply(member, axis=1).astype(bool)


def _regime_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """The descriptive ``regime_context`` block: the journaled regime and the derived tape, over mature rows."""
    present = frame.loc[frame["regime_vix_regime"].notna()]
    measured = mature.loc[mature["regime_vix_regime"].notna()]
    regimes = sorted(str(value) for value in measured["regime_vix_regime"].dropna().unique())
    stresses = sorted(str(value) for value in mature["regime_macro_stress"].dropna().unique())
    reasons = frame["tape_reason"].dropna()
    return {
        "blocks": len(present),
        "by_vix_regime": {name: _bucket(measured.loc[measured["regime_vix_regime"] == name]) for name in regimes},
        "by_macro_stress": {name: _bucket(mature.loc[mature["regime_macro_stress"] == name]) for name in stresses},
        "by_tape": {name: _bucket(mature.loc[_tape_members(mature, name)]) for name in TAPE_BUCKETS},
        "tape_reason": str(reasons.iloc[0]) if len(reasons) else None,
    }


def _recurrence_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """Placeholder shape until Task 4 computes recurrence; keys are the contract."""
    return {
        "setups": 0,
        "repeated_setups": 0,
        "same_session_repeats": 0,
        "by_prior": {"first": _bucket(mature.iloc[0:0]), "repeat": _bucket(mature.iloc[0:0])},
        "by_prior_outcome": {},
    }
```

7. `summarize`: add `"regime_context": _regime_summary(frame, frame)` and `"recurrence": _recurrence_summary(frame, frame)` to the empty-frame dict, and `"regime_context": _regime_summary(frame, mature)` and `"recurrence": _recurrence_summary(frame, mature)` to the returned dict, both after `"news_context"`. Update the `summarize` docstring: "…, card-policy counts, book sizing, news, regime/tape and recurrence."

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py tests/research/setups/test_execution_evidence.py tests/cli/test_cli_smoke.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader/research/setups/outcomes.py tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/research/setups/outcomes.py tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/setups/outcomes.py`
Expected: clean. Stage the three files and report. Commit message: `cards outcomes: regime columns from the journal, SPY tape per decision, regime_context block`.

---

### Task 4: Outcomes — recurrence columns and the `recurrence` block

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py` (`__all__`, new `RESOLVED_HITS`, `PRIOR_OUTCOMES`, `with_recurrence`; `label_journaled` calls it; `_recurrence_summary` computed)
- Test: `tests/research/setups/test_outcomes.py`

**Interfaces:**
- Consumes: Task 3's columns, `_bucket`, `_recurrence_summary` placeholder.
- Produces: `RESOLVED_HITS = ("target", "stop", "timeout")`, `PRIOR_OUTCOMES = ("target", "stop", "timeout", "none")`, `with_recurrence(frame: pd.DataFrame) -> pd.DataFrame`, `summarize(...)["recurrence"]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/research/setups/test_outcomes.py` (import `PRIOR_OUTCOMES`, `RESOLVED_HITS`, `with_recurrence` from the outcomes module):

```python
def _labelled_row(
    contract,
    session,
    decided_at,
    hit,
    *,
    exit_time=None,
    strategy="TREND_PULLBACK",
    direction="LONG",
    r_cost=None,
    excess_r=None,
    scan_id=None,
):
    row = dict.fromkeys(_COLUMNS)
    row.update(
        {
            "scan_id": scan_id or f"{contract}-{decided_at}",
            "session": session,
            "contract": contract,
            "strategy": strategy,
            "direction": direction,
            "hit": hit,
            "r_cost": r_cost,
            "excess_r": excess_r,
            "exit_time": pd.Timestamp(exit_time) if exit_time else None,
            "decided_at": pd.Timestamp(decided_at),
            "sent": False,
            "prior_sessions": 0,
            "prior_same_session": 0,
            "prior_outcome": "none",
        }
    )
    return row


def _frame(rows):
    return pd.DataFrame(rows, columns=list(_COLUMNS))


def test_recurrence_constants():
    assert RESOLVED_HITS == ("target", "stop", "timeout")
    assert PRIOR_OUTCOMES == ("target", "stop", "timeout", "none")


def test_recurrence_counts_prior_sessions_and_uses_the_last_resolved_prior_outcome():
    rows = [
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-02T19:30:00Z", r_cost=1.9
        ),
        _labelled_row(
            "PFE", "2026-03-03", "2026-03-03T14:35:00Z", "stop", exit_time="2026-03-03T16:30:00Z", r_cost=-1.1
        ),
        _labelled_row("PFE", "2026-03-04", "2026-03-04T14:35:00Z", "timeout", r_cost=0.2),
        _labelled_row(
            "XOM", "2026-03-04", "2026-03-04T14:35:00Z", "target", exit_time="2026-03-05T15:30:00Z", r_cost=1.9
        ),
    ]
    out = with_recurrence(_frame(rows)).set_index(["contract", "session"])
    assert out.loc[("PFE", "2026-03-02"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        0,
        0,
        "none",
    ]
    assert out.loc[("PFE", "2026-03-03"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        1,
        0,
        "target",
    ]
    assert out.loc[("PFE", "2026-03-04"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        2,
        0,
        "stop",
    ]
    assert out.loc[("XOM", "2026-03-04"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        0,
        0,
        "none",
    ]


def test_recurrence_prior_outcome_ignores_brackets_open_at_the_decision():
    rows = [
        # Resolved, but only after the second decision: the desk did not know it yet.
        _labelled_row("PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-03T18:30:00Z"),
        _labelled_row("PFE", "2026-03-03", "2026-03-03T14:35:00Z", "stop", exit_time="2026-03-03T16:30:00Z"),
        # Still immature at the third decision; the second row has resolved.
        _labelled_row("PFE", "2026-03-04", "2026-03-04T14:35:00Z", "immature"),
        _labelled_row("PFE", "2026-03-05", "2026-03-05T14:35:00Z", "fetch_failed"),
        _labelled_row("PFE", "2026-03-06", "2026-03-06T14:35:00Z", "timeout"),
    ]
    out = with_recurrence(_frame(rows)).set_index("session")
    # At the 03-03 decision the 03-02 bracket was still open (it exits at 18:30 that day).
    assert out.loc["2026-03-03", "prior_outcome"] == "none"
    # By 03-04 both earlier brackets had resolved; the most recent by decided_at is the 03-03 stop.
    assert out.loc["2026-03-04", "prior_outcome"] == "stop"
    # Immature and fetch-failed rows never count; the last resolved prior is still the 03-03 stop.
    assert out.loc["2026-03-06", "prior_outcome"] == "stop"
    assert out.loc["2026-03-06", "prior_sessions"] == 4
```

```python
def test_recurrence_same_session_repeats_and_ties_on_decided_at_are_not_prior():
    rows = [
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-02T16:30:00Z", scan_id="a"
        ),
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T18:35:00Z", "stop", exit_time="2026-03-02T19:30:00Z", scan_id="b"
        ),
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T18:35:00Z", "stop", exit_time="2026-03-02T19:30:00Z", scan_id="c"
        ),
    ]
    out = with_recurrence(_frame(rows)).set_index("scan_id")
    assert out.loc["a", ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [0, 0, "none"]
    assert out.loc["b", ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [0, 1, "target"]
    assert out.loc["c", ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [0, 1, "target"]


def test_recurrence_is_per_setup_not_per_symbol():
    rows = [
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-02T16:30:00Z", direction="LONG"
        ),
        _labelled_row("PFE", "2026-03-03", "2026-03-03T14:35:00Z", "stop", direction="SHORT"),
        _labelled_row("PFE", "2026-03-04", "2026-03-04T14:35:00Z", "stop", strategy="SQUEEZE_BREAKOUT"),
        _labelled_row("PFE", "2026-03-05", "2026-03-05T14:35:00Z", "stop", strategy=None),
    ]
    out = with_recurrence(_frame(rows))
    assert out["prior_sessions"].tolist() == [0, 0, 0, 0]
    assert out["prior_outcome"].tolist() == ["none"] * 4


def test_recurrence_leaves_other_columns_and_order_unchanged():
    frame = _frame([_labelled_row("PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target")])
    out = with_recurrence(frame)
    assert list(out.columns) == list(_COLUMNS)
    assert out.drop(columns=["prior_sessions", "prior_same_session", "prior_outcome"]).equals(
        frame.drop(columns=["prior_sessions", "prior_same_session", "prior_outcome"])
    )
    assert with_recurrence(_frame([])).empty


def test_recurrence_summary_block():
    rows = [
        _labelled_row(
            "PFE",
            "2026-03-02",
            "2026-03-02T14:35:00Z",
            "target",
            exit_time="2026-03-02T16:30:00Z",
            r_cost=1.9,
            excess_r=1.5,
        ),
        _labelled_row(
            "PFE",
            "2026-03-03",
            "2026-03-03T14:35:00Z",
            "stop",
            exit_time="2026-03-03T16:30:00Z",
            r_cost=-1.1,
            excess_r=-1.3,
        ),
        _labelled_row("PFE", "2026-03-03", "2026-03-03T18:35:00Z", "immature", scan_id="late"),
        _labelled_row("XOM", "2026-03-03", "2026-03-03T14:35:00Z", "timeout", r_cost=0.3),
    ]
    frame = with_recurrence(_frame(rows))
    block = summarize(frame)["recurrence"]
    assert block["setups"] == 2
    assert block["repeated_setups"] == 1
    assert block["same_session_repeats"] == 1
    assert block["by_prior"]["first"] == {
        "n": 2,
        "mean_r_cost": pytest.approx(1.1),
        "mean_excess_r": pytest.approx(1.5),
        "hit_rate": 0.5,
    }
    assert block["by_prior"]["repeat"] == {
        "n": 1,
        "mean_r_cost": pytest.approx(-1.1),
        "mean_excess_r": pytest.approx(-1.3),
        "hit_rate": 0.0,
    }
    assert set(block["by_prior_outcome"]) == set(PRIOR_OUTCOMES)
    assert block["by_prior_outcome"]["target"]["n"] == 1 and block["by_prior_outcome"]["none"]["n"] == 2
    assert block["by_prior_outcome"]["stop"]["n"] == 0


def test_label_journaled_applies_recurrence():
    events = [
        _event("s1", DECIDED_AT, [_candidate()]),
        _event("s2", DECIDED_AT + timedelta(days=1), [_candidate()]),
    ]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW + timedelta(days=1)).sort_values("decided_at")
    assert frame["prior_sessions"].tolist() == [0, 1]
    assert frame["prior_outcome"].tolist() == ["none", "target"]
```

Also update `test_empty_summary_has_the_regime_and_recurrence_blocks` (Task 3) to assert the full empty shape:

```python
assert summary["recurrence"] == {
    "setups": 0,
    "repeated_setups": 0,
    "same_session_repeats": 0,
    "by_prior": {
        "first": {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None},
        "repeat": {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None},
    },
    "by_prior_outcome": {
        name: {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None} for name in PRIOR_OUTCOMES
    },
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_outcomes.py -q -k "recurrence or empty_summary"`
Expected: FAIL (`ImportError: cannot import name 'with_recurrence'`).

- [ ] **Step 3: Implement**

In `agentic_trader/research/setups/outcomes.py`:

1. `__all__`: add `"PRIOR_OUTCOMES"`, `"RESOLVED_HITS"`, `"with_recurrence"` (keep it sorted).
2. Constants, after `NEWS_ALIGNMENTS`:

```python
RESOLVED_HITS = (BracketHit.TARGET.value, BracketHit.STOP.value, BracketHit.TIMEOUT.value)
PRIOR_OUTCOMES = (*RESOLVED_HITS, "none")
```

3. The function, after `_llm_relabel`:

```python
def _is_timestamp(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def with_recurrence(frame: pd.DataFrame) -> pd.DataFrame:
    """Adds prior_sessions, prior_same_session and prior_outcome per ``(contract, strategy, direction)``.

    Over the other rows of the same setup in this frame: distinct earlier sessions; same-session rows
    decided strictly earlier (a tie on ``decided_at`` is not earlier); and the hit of the most recent
    earlier row (by ``decided_at``) whose bracket had resolved -- ``hit`` in ``RESOLVED_HITS`` with an
    ``exit_time`` at or before this row's ``decided_at`` -- else ``"none"``. A prior card still open at
    the decision was not information the desk had, so it never counts; immature and fetch-failed rows
    never count. Rows without a strategy or direction keep the first-occurrence defaults. Relative
    to the frame's window: a setup first journaled before it reads as first.
    """
    out = frame.copy()
    prior_sessions = pd.Series(0, index=out.index, dtype=int)
    prior_same = pd.Series(0, index=out.index, dtype=int)
    prior_outcome = pd.Series("none", index=out.index, dtype=object)
    keyed = out.dropna(subset=["contract", "strategy", "direction"])
    for _, group in keyed.groupby(["contract", "strategy", "direction"], sort=False):
        records = list(group[["session", "decided_at", "hit", "exit_time"]].itertuples())
        for row in records:
            decided = pd.Timestamp(row.decided_at)
            earlier = [other for other in records if pd.Timestamp(other.decided_at) < decided]
            prior_sessions.loc[row.Index] = len({other.session for other in records if other.session < row.session})
            prior_same.loc[row.Index] = sum(1 for other in earlier if other.session == row.session)
            resolved = [
                other
                for other in earlier
                if other.hit in RESOLVED_HITS
                and _is_timestamp(other.exit_time)
                and pd.Timestamp(other.exit_time) <= decided
            ]
            if resolved:
                prior_outcome.loc[row.Index] = max(resolved, key=lambda other: pd.Timestamp(other.decided_at)).hit
    out["prior_sessions"] = prior_sessions
    out["prior_same_session"] = prior_same
    out["prior_outcome"] = prior_outcome
    return out
```

4. In `label_journaled`, replace the final `return frame` with `return with_recurrence(frame)` (after the dtype clean-up).
5. Replace the placeholder `_recurrence_summary`:

```python
def _recurrence_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """The descriptive ``recurrence`` block: setup counts over every row, buckets over mature rows."""
    key = ["contract", "strategy", "direction"]
    keyed = frame.dropna(subset=key)
    sessions_per_setup = keyed.groupby(key, sort=False)["session"].nunique() if len(keyed) else pd.Series(dtype=int)
    first = mature["prior_sessions"].fillna(0).astype(int) == 0
    return {
        "setups": int(len(sessions_per_setup)),
        "repeated_setups": int((sessions_per_setup > 1).sum()),
        "same_session_repeats": int((frame["prior_same_session"].fillna(0).astype(int) > 0).sum()),
        "by_prior": {"first": _bucket(mature.loc[first]), "repeat": _bucket(mature.loc[~first])},
        "by_prior_outcome": {name: _bucket(mature.loc[mature["prior_outcome"] == name]) for name in PRIOR_OUTCOMES},
    }
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups -q && env -u VIRTUAL_ENV uv run pytest tests/cli/test_cli_smoke.py tests/agent/test_card_stats_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader/research/setups/outcomes.py tests/research/setups/test_outcomes.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/research/setups/outcomes.py tests/research/setups/test_outcomes.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/setups/outcomes.py`
Expected: clean. Stage both files and report. Commit message: `cards outcomes: setup recurrence without lookahead and the recurrence block`.

---

### Task 5: Documentation

**Files:**
- Modify: `docs/card-evidence.md` (new section after "News context"; the "Repeated setups" limit)
- Modify: `docs/production.md` (the `cards outcomes` paragraph ~line 569)
- Modify: `docs/alpha-roadmap.md` (item 1 of the desk direction, a dated status)
- Modify: `CLAUDE.md` (contract 8, the `cards outcomes` sentence; the news paragraph's last sentence)

**Interfaces:** none; the text below is binding and copied verbatim, then checked against the code by the reviewer.

- [ ] **Step 1: `docs/card-evidence.md`** — insert after the "News context" section and before "## Limits":

```markdown
## Regime and recurrence context

Spec: [regime and recurrence](superpowers/specs/2026-10-14-regime-recurrence-design.md). Two more
descriptive blocks in `cards outcomes`, measuring the two hypotheses from the 2026-10-13 review of the
live sandbox: that the funnel's edge appears only in a trending tape, and that repeated setups are
informative. Nothing here is read by `card_policy`, `card_stats`, any risk rule, admission or the
ranking; there is no card line.

**Journaled regime.** Every `scan_candidates_ranked` event carries one top-level `regime` block
(`agent/regime.py:regime_journal_block`; keys `REGIME_BLOCK_KEYS`): `vix`, `vix_regime`
(`COMPRESSED`/`NORMAL`/`ELEVATED`/`EXTREME`), `breakout_allowed`, `risk_multiplier`,
`min_rr_threshold`, `macro_stress` (`LOW`/`MODERATE`/`HIGH`/`EXTREME`, null without a macro report) and
`observed_at` — the snapshot the scan gated on, which cannot be rebuilt from bars later. Every card's
`decision_provenance["regime"]` carries the same block. Events before 2026-10-14 have none.

**Derived tape.** The report fetches SPY daily bars once (raw, 400 calendar days before the earliest
decision, after the hourly fetches) and computes, per decision session, from completed sessions
strictly before it (`research/setups/tape.py:tape_state`): `spy_ret_20` (the 20-session close return)
and `spy_above_200` (last close above the 200-session mean). Columns `regime_vix`,
`regime_vix_regime`, `regime_risk_multiplier`, `regime_macro_stress`, `spy_ret_20`, `spy_above_200`,
`tape_reason` sit after `news_alignment`. A failed daily fetch sets `tape_reason` on every row and leaves
the rest of the report intact.

**Recurrence.** Over the rows of the report (`with_recurrence`), per `(contract, strategy,
direction)`: `prior_sessions` (distinct earlier sessions in the window), `prior_same_session`
(same-session rows decided strictly earlier) and `prior_outcome` (the hit — `target`/`stop`/`timeout` —
of the most recent earlier row whose bracket had resolved by this decision, else `none`; a card still
open at the decision never counts, so there is no lookahead). The counts are relative to `--days`: a
setup first journaled before the window reads as first.

**Summary blocks.** Buckets are `{n, mean_r_cost, mean_excess_r, hit_rate}` over mature rows
(`mean_excess_r` over the bucket's rows with a market control):

- `regime_context`: `blocks` (rows with a journaled `vix_regime`), `by_vix_regime`, `by_macro_stress`,
  `by_tape` (`ret20_up`/`ret20_down` by the sign of `spy_ret_20`, `above_200`/`below_200`, `unknown`
  when neither measure exists; a row counts in each bucket it belongs to), `tape_reason`.
- `recurrence`: `setups` (distinct setups in the window), `repeated_setups` (journaled in more than
  one session), `same_session_repeats` (rows with `prior_same_session > 0`), `by_prior`
  (`first`/`repeat`), `by_prior_outcome` (`target`/`stop`/`timeout`/`none`).

Reading: a bucket that differs from the others is a hypothesis for a predeclared study (the bracket
counterfactual is next), not a rule. The buckets are threshold-free by design so they cannot be fitted
to the sample; the 20- and 200-session horizons are the setup feature vector's own. On 2026-10-14 the
journal held 39 distinct setups since 2026-09-24, 20 of them journaled in more than one session, and the
feature vector's `spy_above_200` was `1.0` on every candidate that had it.
```

Then change the "Repeated setups" limit's last sentence from "Counting distinct setups per session is a precondition before `card_policy` moves to `enforce`." to "`cards outcomes` now counts them (`recurrence.same_session_repeats`, `repeated_setups`); de-duplicating the `card_stats` denominator is a separate operator decision before `card_policy` moves to `enforce`."

- [ ] **Step 2: `docs/production.md`** — in the `cards outcomes` paragraph (~line 569 onwards), after the sentence that ends "…each group's base rates and mean" (find the sentence listing the summary blocks; append after the block list) add one sentence: "The `regime_context` and `recurrence` blocks ([card evidence](card-evidence.md#regime-and-recurrence-context)) bucket mature rows by the journaled VIX regime and macro stress, by the SPY tape derived at each decision (one extra SPY daily request per run), and by each setup's recurrence in the window; the next universe scan after the 2026-10-14 deploy is the first event with a `regime` block."

- [ ] **Step 3: `docs/alpha-roadmap.md`** — under desk-direction item 1 (**Native funnel**), after its existing status paragraphs, add:

```markdown
   *Status (October 14):* the live-sandbox review of 2026-10-13 (four broker-confirmed closed trades,
   +4.8R, two from retired mined alphas; sent cards +0.24R against a +0.21R SPY move) left two
   hypotheses the journal could not measure: edge only in a trending tape, and informative repeats.
   The scan now journals its regime block and `cards outcomes` derives the SPY tape per decision and
   each setup's recurrence without lookahead ([card evidence](card-evidence.md#regime-and-recurrence-context),
   [spec](superpowers/specs/2026-10-14-regime-recurrence-design.md)). Measurement only; the next step is
   one predeclared bracket counterfactual (2R vs 3R target, ratchet on/off, index control) as a frozen
   entry.
```

- [ ] **Step 4: `CLAUDE.md`** — in contract 8, change "`cards outcomes` decomposes selection, LLM gate, market exposure and fill slippage; these are descriptive, never gates." to "`cards outcomes` decomposes selection, LLM gate, market exposure, fill slippage, news, the journaled regime with the derived SPY tape, and setup recurrence (`regime_context`, `recurrence`; [card evidence](docs/card-evidence.md#regime-and-recurrence-context)); these are descriptive, never gates. Every universe scan journals a top-level `regime` block (`regime_journal_block`) and copies it into card provenance."

- [ ] **Step 5: Verify and stage**

Run: `env -u VIRTUAL_ENV uv run pre-commit run --files docs/card-evidence.md docs/production.md docs/alpha-roadmap.md CLAUDE.md` and `grep -n "regime-and-recurrence-context" docs/card-evidence.md docs/production.md docs/alpha-roadmap.md CLAUDE.md` (the anchor must match the heading `## Regime and recurrence context`).
Expected: hooks pass (re-add if ruff-format reflows a fenced block); four files reference the anchor. Stage the four files and report. Commit message: `Docs: regime and recurrence context in cards outcomes`.
