# Pooled Alpha Mining — Part 1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pooled lane's research core — cohort, point-in-time eligibility, the long-bracket label cube, the formula selector, pooled statistics, the campaign's stage logic with an in-memory ledger, power check A — and run the two literature entries (52-week high; one-month reversal among low-MAX names) through it.

**Architecture:** One frozen cohort file and one cube spec produce a hash-identified label cube (one long bracket per eligible name and session). A formula is a dimensionless DSL score, optional quantile filters and k = 3; picks are the top-k eligible names per session with hold-skipping and a hash tie-break. The statistic is the session-paired edge (picks' mean R minus all eligible names' mean R that session) with a stationary session-block bootstrap. The campaign's discovery/selection/confirmation stages are pure functions over cube windows with an injected ledger; power check A drives them on block-resampled discovery-window cubes with synthetic AR(1) formulas and a planted edge.

**Tech Stack:** Python 3.14, numpy, pandas, pydantic v2, click (CLI), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-28-pooled-alpha-mining-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-pooled` (branch `research/pooled-mining`). Never read-write, run or restart anything in `/Users/adamhadani/Development/agentic-trader` (the live installed checkout).
- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree root.
- Research only: no database writes, no registry, broker or Telegram access, no trial or promotion credit in Part 1a. Every result document carries `"authorizes_promotion": false`.
- No new dependencies, no schema migrations.
- Tests: no network, synthetic fixtures only, follow `tests/research/` patterns (see `tests/research/apriori/test_pead_study.py::hourly_bars`).
- Reuse, never reimplement: `research/setups/labels.py:label_bracket`, `SetupLevels`, `BracketHit`; `research/apriori/pead_study.py:decision_price`; `research/apriori/pead_events.py:decision_at`, `_atr`, `_by_session`; `screeners/dynamic_universe.py:median_dollar_volume`, `static_reference`; `research/setups/study.py:_stationary_index_draws`, `holm`, `_finite_json`; `research/setups/baserates.py:_ci90`, `_p_one_sided_positive`; `research/setups/runner.py:_claim_cache_range`, `_fetch_cached`, `BarSource`, `CalendarSource`; `storage/artifacts.py:save_json_report`; `research/alpha/dsl.py:compile_expression`, `AlphaExpressionEvaluator`; `research/alpha/search.py:MUTATION_OPERATORS`.
- Exact values (verbatim from the spec): decisions 2016-08-01 → 2026-07-31; bars 2016-01-01 → 2026-09-01; discovery 2016-08-01 → 2021-12-31; selection 2022-01-03 → 2023-12-29; confirmation 2024-01-02 → 2026-07-31; `recent_from` 2023-01-01; decision 10:35 New York on D using daily bars through D−1; stop 2×ATR14, target 3R, 20-session hold; costs 0 and 5 bps per side, 5 decides; `k` = 3; eligibility close ≥ $10 and 20-session median dollar volume ≥ the static equities' 25th percentile, both on raw bars at `as_of` = D−1; ≥ 30 eligible names per session; bootstrap block mean 20; P4 ≥ 500 sessions with a pick and ≥ 150 from 2023-01-01; trim 1%; discovery t ≥ 3.0, ≥ 3 of 4 blocks positive, ≥ 400 sessions, carry 5; selection ≥ 0.5 × discovery edge, ≥ 150 sessions; confirmation Holm one-sided α 0.05, 10,000 draws, last 12 months positive, ≥ 300 sessions; power A 100 replicates, 200 null formulas, AR(1) φ 0.95, δ ∈ {0, 0.05, 0.08, 0.10, 0.12, 0.15}, detection ≥ 80% at δ = 0.15, false acceptance ≤ 5% at δ = 0; coverage fails closed above 2% sessions without a static reference or 5% eligible cells without hourly data in any year.
- Frozen files (`config/research/pooled/*.json`) may be corrected only before any real-data run; after the first real-data run a change is a new version file.
- Implementers stage their changes with `git add` and report; the controller commits (the pre-commit hook runs the full suite, ~5 minutes).
- Docs ship in this PR (no docs-only PR).

## Rulings recorded before execution

- **Calendar-time cross-check:** the cube stores each cell's final R, not a daily path, so the calendar-time portfolio spreads each pick's residual R evenly over its holding sessions. It is a reported cross-check, never decisive; Task 13 records this in the spec.
- **SIP history starts 2016-01:** formulas with a 252-session lookback have no score until early 2017; their names are simply not rankable before then (counted, not failed).
- **Immature picks:** a pick is chosen from point-in-time eligibility, never from label availability; a pick whose label is unresolved is dropped from statistics and counted, and it blocks its name for `max_hold_sessions`.
- **Campaign stage logic lives in 1a** (pure functions + in-memory ledger) because power check A must run it unchanged; Part 1b adds the journal ledger, the genetic search and the campaign CLI.

## Review Focus

1. A score evaluated with bar D (the decision day) instead of bars through D−1 — expected: changing bar D never changes the session-D score (Task 4 test `test_scores_use_bars_through_the_previous_session_only`).
2. The $10 floor or dollar-volume screen read from adjusted instead of raw bars — expected: a later split in adjusted history cannot change eligibility (Task 2 test `test_eligibility_reads_raw_bars_at_as_of`).
3. Hold-skipping off by one — expected: a name picked on session s is not picked again before session s + holding (Task 4 test `test_a_held_name_is_skipped_until_after_its_exit_session`).
4. Label leakage across stage windows — expected: with `purge=True` a pick whose hold ends after the window's last session is dropped from both picks and control (Task 5 test `test_purge_drops_cells_whose_hold_crosses_the_window_end`).
5. Sessions without a pick counted as zero edges — expected: the paired edge averages only sessions with a pick, and the bootstrap weights resampled sessions the same way (Task 5 test `test_sessions_without_picks_do_not_dilute_the_edge`).

---

### Task 1: Cohort file and loader

**Files:**
- Create: `agentic_trader/research/pooled/__init__.py`
- Create: `agentic_trader/research/pooled/cohort.py`
- Create: `config/research/pooled/cohort-v1.json`
- Test: `tests/research/pooled/__init__.py`, `tests/research/pooled/test_cohort.py`

**Interfaces:**
- Produces: `SUPPORTED_SYMBOL`, `CohortSource`, `Cohort`, `LoadedCohort(cohort, sha256, path)`, `load_cohort(path) -> LoadedCohort`, `Cohort.source_of(symbol) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_cohort.py
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.pooled.cohort import load_cohort


REPO = Path(__file__).resolve().parents[3]
COHORT = REPO / "config/research/pooled/cohort-v1.json"


def _write(tmp_path: Path, **overrides) -> Path:
    doc = {
        "id": "pooled-cohort",
        "version": 1,
        "survivorship": "current membership (2026-09); not point-in-time",
        "sources": [
            {
                "kind": "config_groups",
                "description": "mega_caps+research_cohort",
                "identity": "x",
                "symbols": ["AAA", "BBB"],
            },
            {"kind": "equity_snapshot", "description": "snapshot", "identity": "y", "symbols": ["BBB", "CCC"]},
        ],
        "excluded": {"BRK.B": "unsupported symbol form"},
        "symbols": ["AAA", "BBB", "CCC"],
    }
    doc.update(overrides)
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(doc))
    return path


def test_identity_is_the_sha256_of_the_file_bytes(tmp_path):
    path = _write(tmp_path)
    loaded = load_cohort(path)
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.cohort.symbols == ("AAA", "BBB", "CCC")
    assert loaded.cohort.source_of("BBB") == ("config_groups", "equity_snapshot")


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbols": ["BBB", "AAA", "CCC"]},  # unsorted
        {"symbols": ["AAA", "AAA", "CCC"]},  # duplicate
        {"symbols": ["AAA", "BRK.B"]},  # unsupported form
        {"unknown": 1},  # unknown field
        {"symbols": ["AAA", "BBB"]},  # a source symbol missing from the union
    ],
)
def test_invalid_cohorts_are_rejected(tmp_path, overrides):
    with pytest.raises(ValidationError):
        load_cohort(_write(tmp_path, **overrides))


def test_committed_cohort_is_valid_and_union_of_its_sources():
    loaded = load_cohort(COHORT)
    union = sorted({s for source in loaded.cohort.sources for s in source.symbols})
    assert list(loaded.cohort.symbols) == union
    kinds = {source.kind for source in loaded.cohort.sources}
    assert kinds == {"config_groups", "equity_snapshot"}
    assert len(loaded.cohort.symbols) >= 300
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_cohort.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled'`.

- [ ] **Step 3: Implement the package and loader**

```python
# agentic_trader/research/pooled/__init__.py
"""Pooled alpha mining: one formula evaluated across a frozen cohort (research only)."""
```

```python
# agentic_trader/research/pooled/cohort.py
"""The pooled lane's cohort: a frozen symbol list under ``config/research/pooled/``.

The cohort is today's membership, not a point-in-time universe: the configured
scan-universe equity groups plus a prospective equity snapshot. Its identity is the
SHA-256 of the file's bytes, recorded in every manifest; a changed file is a new version.
Point-in-time eligibility per session is computed separately from bars
(``point_in_time_eligibility``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


__all__ = ["SUPPORTED_SYMBOL", "Cohort", "CohortSource", "LoadedCohort", "load_cohort"]

SUPPORTED_SYMBOL = re.compile(r"^[A-Z]{1,5}$")


class CohortSource(BaseModel, frozen=True, extra="forbid"):
    kind: Literal["config_groups", "equity_snapshot"]
    description: str
    # config groups: SHA-256 of the newline-joined sorted symbols; snapshot: its snapshot_id.
    identity: str = Field(min_length=1)
    symbols: tuple[str, ...]


class Cohort(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-cohort"]
    version: int = Field(ge=1)
    survivorship: str
    sources: tuple[CohortSource, ...] = Field(min_length=1)
    excluded: dict[str, str]  # symbol -> reason (e.g. unsupported symbol form)
    symbols: tuple[str, ...] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def _sorted_unique_supported(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if list(value) != sorted(set(value)):
            raise ValueError("symbols must be sorted and unique")
        unsupported = [s for s in value if not SUPPORTED_SYMBOL.fullmatch(s)]
        if unsupported:
            raise ValueError(f"unsupported symbols: {unsupported[:5]}")
        return value

    @model_validator(mode="after")
    def _union_of_sources(self) -> Cohort:
        union = sorted({s for source in self.sources for s in source.symbols if SUPPORTED_SYMBOL.fullmatch(s)})
        if list(self.symbols) != union:
            raise ValueError("symbols must be the sorted union of the sources' supported symbols")
        return self

    def source_of(self, symbol: str) -> tuple[str, ...]:
        return tuple(source.kind for source in self.sources if symbol in source.symbols)


@dataclass(frozen=True)
class LoadedCohort:
    cohort: Cohort
    sha256: str
    path: Path


def load_cohort(path: Path) -> LoadedCohort:
    raw = path.read_bytes()
    return LoadedCohort(cohort=Cohort.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
```

- [ ] **Step 4: Generate the committed cohort file**

Run from the worktree root (reads the config and the private snapshot read-only; writes only the new file):

```bash
env -u VIRTUAL_ENV uv run python - <<'EOF'
import hashlib, json, re
from pathlib import Path
import yaml

supported = re.compile(r"^[A-Z]{1,5}$")
groups = yaml.safe_load(Path("config/config.yaml").read_text())["universe"]["groups"]
config_symbols = sorted({row["symbol"] for name in ("mega_caps", "research_cohort") for row in groups[name]})
snapshot_path = Path("~/.local/state/agentic-trader/research/equity-universe-20260917/v2/snapshot.json").expanduser()
snapshot = json.loads(snapshot_path.read_text())
snapshot_symbols = sorted({row["symbol"] for row in snapshot["selected"]})
everything = sorted({*config_symbols, *snapshot_symbols})
doc = {
    "id": "pooled-cohort",
    "version": 1,
    "survivorship": "current membership (2026-09); not point-in-time",
    "sources": [
        {
            "kind": "config_groups",
            "description": "config/config.yaml universe groups mega_caps + research_cohort (scan-universe equities)",
            "identity": hashlib.sha256("\n".join(config_symbols).encode()).hexdigest(),
            "symbols": config_symbols,
        },
        {
            "kind": "equity_snapshot",
            "description": "prospective_equity_candidates_v2 snapshot (300 selected, 2026-09-17)",
            "identity": snapshot["snapshot_id"],
            "symbols": snapshot_symbols,
        },
    ],
    "excluded": {s: "unsupported symbol form" for s in everything if not supported.fullmatch(s)},
    "symbols": [s for s in everything if supported.fullmatch(s)],
}
Path("config/research/pooled").mkdir(parents=True, exist_ok=True)
Path("config/research/pooled/cohort-v1.json").write_text(json.dumps(doc, indent=2) + "\n")
print(len(doc["symbols"]), "symbols;", len(doc["excluded"]), "excluded")
EOF
```

Expected: prints roughly `420 symbols; N excluded` (N small).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_cohort.py -q`
Expected: PASS (7 tests).

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/__init__.py agentic_trader/research/pooled/cohort.py config/research/pooled/cohort-v1.json tests/research/pooled/__init__.py tests/research/pooled/test_cohort.py
```

---

### Task 2: Point-in-time eligibility

**Files:**
- Modify: `agentic_trader/research/pooled/cohort.py` (append)
- Test: `tests/research/pooled/test_eligibility.py`

**Interfaces:**
- Consumes: `median_dollar_volume`, `static_reference` (`screeners/dynamic_universe.py`); `_atr`, `_by_session` (`research/apriori/pead_events.py`).
- Produces: `UniverseSpec(min_price, static_percentile, min_eligible_names)`; `Eligibility(sessions, symbols, eligible[S,N] bool, atr[S,N], dollar_volume[S,N], reference, reference_names, skipped_sessions, reasons)`; `point_in_time_eligibility(trading_days, decisions, symbols, adjusted, raw, static_raw, *, universe, atr_window) -> Eligibility`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_eligibility.py
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import UniverseSpec, _DailySlicer, point_in_time_eligibility
from agentic_trader.screeners.dynamic_universe import median_dollar_volume


UNIVERSE = UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=2)


def days(n: int, start: date = date(2021, 1, 4)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def daily(sessions: list[date], close: float | list[float], volume: float = 1e6) -> pd.DataFrame:
    closes = np.full(len(sessions), close, dtype=float) if np.isscalar(close) else np.asarray(close, dtype=float)
    index = pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=ET_TZ).astimezone(UTC) for d in sessions])
    return pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes, "Volume": volume}, index=index
    )


def test_slicer_matches_the_unsliced_live_function():
    sessions = days(120)
    rng = np.random.default_rng(1)
    frame = daily(sessions, list(50 + rng.normal(0, 1, 120)), volume=1e6)
    frame.iloc[[5, 40, 41, 90], frame.columns.get_loc("Close")] = np.nan
    slicer = _DailySlicer(frame)
    for as_of in sessions[18:]:
        assert slicer.median_dollar_volume(as_of) == median_dollar_volume(frame, as_of)


def reference(sessions: list[date]) -> dict[str, pd.DataFrame]:
    """The live gate needs MIN_REFERENCE_NAMES (20) static names: $20 x 1e6 = $2e7 dollar volume each."""
    return {f"R{i:02d}": daily(sessions, 20.0) for i in range(20)}


def test_eligibility_reads_raw_bars_at_as_of():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0), "BBB": daily(sessions, 20.0), "LOW": daily(sessions, 9.0)}
    # Adjusted history is divided by a later 4:1 split: LOW's adjusted price looks like 2.25 and AAA's 5.0.
    adjusted = {s: daily(sessions, float(f["Close"].iloc[0]) / 4) for s, f in raw.items()}
    result = point_in_time_eligibility(
        sessions,
        (decision, decision),
        ["AAA", "BBB", "LOW"],
        adjusted,
        raw,
        reference(sessions),
        universe=UNIVERSE,
        atr_window=14,
    )
    assert result.sessions == (decision,)
    assert result.eligible[0].tolist() == [True, True, False]  # $10 floor on raw bars, not adjusted
    assert result.reasons["illiquid_price"] == 1


def test_eligibility_never_uses_the_decision_day():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0), "BBB": daily(sessions, 20.0)}
    later = {s: f.copy() for s, f in raw.items()}
    later["AAA"].iloc[40:, later["AAA"].columns.get_loc("Close")] = 1.0  # collapses ON the decision day
    kwargs = dict(universe=UNIVERSE, atr_window=14)
    static = reference(sessions)
    before = point_in_time_eligibility(sessions, (decision, decision), ["AAA", "BBB"], raw, raw, static, **kwargs)
    after = point_in_time_eligibility(sessions, (decision, decision), ["AAA", "BBB"], later, later, static, **kwargs)
    assert before.eligible.tolist() == after.eligible.tolist() == [[True, True]]


def test_sessions_below_the_minimum_eligible_names_are_skipped():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0), "LOW": daily(sessions, 5.0)}
    result = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "LOW"], raw, raw, reference(sessions), universe=UNIVERSE, atr_window=14
    )
    assert not result.eligible.any()
    assert result.skipped_sessions == {"too_few_eligible": 1}


def test_a_session_without_a_static_reference_is_skipped():
    sessions = days(60)
    decision = sessions[10]  # fewer than 20 completed sessions of history: no reference
    raw = {"AAA": daily(sessions, 20.0), "BBB": daily(sessions, 20.0)}
    result = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "BBB"], raw, raw, reference(sessions), universe=UNIVERSE, atr_window=14
    )
    assert result.skipped_sessions == {"no_reference": 1}
    assert result.reference == (None,)


def test_atr_and_dollar_volume_are_recorded_for_eligible_cells():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0, volume=2e6), "BBB": daily(sessions, 20.0, volume=2e6)}
    result = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "BBB"], raw, raw, reference(sessions), universe=UNIVERSE, atr_window=14
    )
    assert result.atr[0, 0] == pytest.approx(0.4)  # high-low = 20*0.02 every day
    assert result.dollar_volume[0, 0] == pytest.approx(4e7)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_eligibility.py -q`
Expected: FAIL with `ImportError: cannot import name 'UniverseSpec'`.

- [ ] **Step 3: Implement eligibility (append to `cohort.py`)**

Add these imports at the top of `cohort.py` (merge with the existing ones):

```python
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.pead_events import _atr, _by_session
from agentic_trader.screeners.dynamic_universe import median_dollar_volume, static_reference
```

Extend `__all__` with `"Eligibility"`, `"UniverseSpec"`, `"point_in_time_eligibility"`, then append:

```python
class UniverseSpec(BaseModel, frozen=True, extra="forbid"):
    min_price: float = Field(gt=0)
    static_percentile: float = Field(gt=0, lt=1)
    min_eligible_names: int = Field(ge=1)


class _DailySlicer:
    """Fast point-in-time access to one symbol's raw daily bars by New York session date.

    ``median_dollar_volume`` keeps the finite rows through ``as_of`` and takes the last 20.
    Handing it only the last ``_TAIL`` finite rows through ``as_of`` returns the identical
    value without scanning the whole history on every call (the live function is reused,
    not reimplemented).
    """

    _TAIL = 40

    def __init__(self, frame: pd.DataFrame):
        keep = np.isfinite(frame["Close"].to_numpy(float)) & np.isfinite(frame["Volume"].to_numpy(float))
        finite = frame[keep]
        index = pd.DatetimeIndex(finite.index)
        index = index if index.tz is not None else index.tz_localize("UTC")
        self._frame = finite
        self._days = index.tz_convert(ET_TZ).tz_localize(None).normalize().values.astype("datetime64[D]")
        self._closes = finite["Close"].to_numpy(float)

    def upto(self, as_of: date) -> pd.DataFrame:
        pos = int(np.searchsorted(self._days, np.datetime64(as_of), side="right"))
        return self._frame.iloc[max(0, pos - self._TAIL) : pos]

    def median_dollar_volume(self, as_of: date) -> float | None:
        return median_dollar_volume(self.upto(as_of), as_of)

    def close_on(self, as_of: date) -> float | None:
        pos = int(np.searchsorted(self._days, np.datetime64(as_of), side="left"))
        if pos < len(self._days) and self._days[pos] == np.datetime64(as_of):
            return float(self._closes[pos])
        return None


@dataclass(frozen=True)
class Eligibility:
    sessions: tuple[date, ...]  # decision sessions in the window
    symbols: tuple[str, ...]
    eligible: np.ndarray  # bool [S, N]
    atr: np.ndarray  # float [S, N], ATR through D-1 on adjusted bars; NaN where ineligible
    dollar_volume: np.ndarray  # float [S, N], raw 20-session median at D-1; NaN where ineligible
    reference: tuple[float | None, ...]  # static reference per session
    reference_names: tuple[int, ...]
    skipped_sessions: dict[str, int]
    reasons: dict[str, int]  # per-cell ineligibility reasons


def point_in_time_eligibility(
    trading_days: Sequence[date],
    decisions: tuple[date, date],
    symbols: Sequence[str],
    adjusted: Mapping[str, pd.DataFrame],
    raw: Mapping[str, pd.DataFrame],
    static_raw: Mapping[str, pd.DataFrame],
    *,
    universe: UniverseSpec,
    atr_window: int,
) -> Eligibility:
    """Eligibility at each decision session D exactly as the live dynamic-universe gate sees it.

    ``as_of`` is D-1, the last completed session. The $10 floor and the dollar-volume screen
    read **raw** bars (adjusted history depends on later corporate actions); ATR reads the
    adjusted bars. Eligibility never depends on a formula, so every formula shares one control.
    """
    days = tuple(trading_days)
    start, end = decisions
    rows = [i for i, day in enumerate(days) if start <= day <= end and i >= 1]
    sessions = tuple(days[i] for i in rows)
    names = tuple(symbols)
    shape = (len(sessions), len(names))
    eligible = np.zeros(shape, dtype=bool)
    atr = np.full(shape, np.nan)
    dollar_volume = np.full(shape, np.nan)
    reference: list[float | None] = []
    reference_names: list[int] = []
    skipped: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    raw_slicers = {s: _DailySlicer(f) for s, f in raw.items() if f is not None and not f.empty}
    static_slicers = {s: _DailySlicer(f) for s, f in static_raw.items() if f is not None and not f.empty}
    keyed = {s: _by_session(f) for s, f in adjusted.items() if f is not None and not f.empty}

    for row, i in enumerate(rows):
        as_of = days[i - 1]
        ref = static_reference(
            {s: sl.upto(as_of) for s, sl in static_slicers.items()}, universe.static_percentile, as_of=as_of
        )
        reference.append(ref.value)
        reference_names.append(ref.names)
        if ref.value is None:
            skipped["no_reference"] += 1
            continue
        for col, symbol in enumerate(names):
            slicer, adj = raw_slicers.get(symbol), keyed.get(symbol)
            if slicer is None or adj is None:
                reasons["no_daily_bars"] += 1
                continue
            volume = slicer.median_dollar_volume(as_of)
            if volume is None:
                reasons["short_history"] += 1
                continue
            close = slicer.close_on(as_of)
            if close is None:
                reasons["no_raw_close"] += 1
                continue
            if close < universe.min_price:
                reasons["illiquid_price"] += 1
                continue
            if volume < ref.value:
                reasons["illiquid_volume"] += 1
                continue
            value = _atr(adj, days, i - 1, atr_window)
            if value is None:
                reasons["no_atr"] += 1
                continue
            eligible[row, col] = True
            atr[row, col] = value
            dollar_volume[row, col] = volume
        if eligible[row].sum() < universe.min_eligible_names:
            skipped["too_few_eligible"] += 1
            eligible[row] = False
            atr[row] = np.nan
            dollar_volume[row] = np.nan
    return Eligibility(
        sessions=sessions,
        symbols=names,
        eligible=eligible,
        atr=atr,
        dollar_volume=dollar_volume,
        reference=tuple(reference),
        reference_names=tuple(reference_names),
        skipped_sessions=dict(skipped),
        reasons=dict(reasons),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/cohort.py tests/research/pooled/test_eligibility.py
```

---

### Task 3: Label cube, window guard and artifact

**Files:**
- Create: `agentic_trader/research/pooled/cube.py`
- Test: `tests/research/pooled/test_cube.py`

**Interfaces:**
- Consumes: `Eligibility`, `UniverseSpec` (Task 2); `decision_at` (`pead_events`); `decision_price` (`pead_study`); `label_bracket`, `SetupLevels`, `BracketHit` (`setups/labels`).
- Produces: `BracketSpec`, `CoverageSpec`, `CubeSpec` (`.identity`), `HIT_CODES`, `tiebreak_key(symbol, day) -> int`, `CubeView(sessions, symbols, offset, eligible, labelled, r_gross, r_cost, holding, hit, tiebreak, dollar_volume)` with `.sub(lo, hi) -> CubeView`, `LabelCube` (`.window(start, end) -> CubeView`, `.sessions`, `.symbols`, `.spec_identity`, `.cohort_sha256`, `.coverage`, `.sha256`, `.with_shift(session_idx, symbol_idx, delta) -> LabelCube`; constructed with keyword arguments `spec_identity, cohort_sha256, sessions, symbols, arrays, coverage`), `build_cube(eligibility, hourly, spec, *, cohort_sha256) -> LabelCube`, `save_cube(cube, path)`, `load_cube(path) -> LabelCube`, `check_coverage(cube, coverage)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_cube.py
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import Eligibility, UniverseSpec
from agentic_trader.research.pooled.cube import (
    BracketSpec,
    CoverageSpec,
    CubeSpec,
    LabelCube,
    build_cube,
    check_coverage,
    load_cube,
    save_cube,
    tiebreak_key,
)


SPEC = CubeSpec(
    feed="alpaca:sip",
    bars_from=date(2021, 1, 1),
    decisions=(date(2021, 3, 1), date(2021, 3, 31)),
    bars_through=date(2021, 5, 1),
    bracket=BracketSpec(
        decision_time_et="10:35", stop_atr_multiple=2.0, atr_window=14, target_r=3.0, max_hold_sessions=3
    ),
    universe=UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=1),
    decision_cost_bps=5.0,
)


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def hourly(days: list[date], path: list[float]) -> pd.DataFrame:
    """Seven regular-session hourly bars (09:00..15:00 NY) per day; ``path`` gives one price per bar."""
    index, prices = [], []
    k = 0
    for day in days:
        for h in range(9, 16):
            index.append(datetime.combine(day, time(h), tzinfo=ET_TZ).astimezone(UTC))
            prices.append(path[min(k, len(path) - 1)])
            k += 1
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p, "Volume": 1.0}, index=pd.DatetimeIndex(index))


def eligibility(sessions, symbols, eligible, atr=1.0) -> Eligibility:
    grid = np.asarray(eligible, dtype=bool)
    return Eligibility(
        sessions=tuple(sessions),
        symbols=tuple(symbols),
        eligible=grid,
        atr=np.where(grid, atr, np.nan),
        dollar_volume=np.where(grid, 1e7, np.nan),
        reference=tuple(1e6 for _ in sessions),
        reference_names=tuple(30 for _ in sessions),
        skipped_sessions={},
        reasons={},
    )


def test_cells_are_labelled_with_the_long_bracket_from_the_decision_price():
    days = weekdays(date(2021, 3, 1), 10)
    # 09:00 bar closes at 100 (the decision price); entry is the 11:00 bar's open (100);
    # the 12:00 bar reaches the 106 target (100 + 3 * 2 * ATR 1).
    bars = {"AAA": hourly(days, [100.0, 100.0, 100.0, 106.0])}
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), bars, SPEC, cohort_sha256="c" * 64)
    view = cube.window(days[0], days[0])
    assert view.labelled.tolist() == [[True]]
    assert view.r_gross[0, 0] == pytest.approx(3.0)
    assert view.r_cost[0, 0] == pytest.approx(3.0 - 2 * 5 / 1e4 * 100 / 2)
    assert view.hit[0, 0] == 1  # target


def test_missing_hourly_bars_are_counted_not_labelled():
    days = weekdays(date(2021, 3, 1), 5)
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), {}, SPEC, cohort_sha256="c" * 64)
    view = cube.window(days[0], days[0])
    assert not view.labelled.any()
    assert view.holding[0, 0] == SPEC.bracket.max_hold_sessions
    assert cube.coverage["unlabelled_by_year"]["no_hourly_bars"] == {"2021": 1}


def test_the_cube_exposes_labels_only_through_windows():
    days = weekdays(date(2021, 3, 1), 5)
    cube = build_cube(eligibility(days[:2], ["AAA"], [[True], [True]]), {}, SPEC, cohort_sha256="c" * 64)
    for name in ("r_cost", "r_gross", "labelled", "hit", "holding"):
        assert not hasattr(cube, name)
    view = cube.window(days[1], days[1])
    assert view.sessions == (days[1],) and view.offset == 1
    with pytest.raises(ValueError):
        cube.window(date(2020, 1, 1), date(2020, 1, 31))  # no session inside


def test_tiebreak_is_a_hash_not_the_alphabet():
    day = date(2021, 3, 1)
    keys = {s: tiebreak_key(s, day) for s in ("AAA", "BBB", "CCC", "DDD")}
    assert sorted(keys, key=keys.get) != ["AAA", "BBB", "CCC", "DDD"]
    assert tiebreak_key("AAA", day) != tiebreak_key("AAA", day + timedelta(days=1))


def test_save_and_load_round_trip_verifies_the_hash(tmp_path):
    days = weekdays(date(2021, 3, 1), 10)
    bars = {"AAA": hourly(days, [100.0, 100.0, 100.0, 106.0])}
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), bars, SPEC, cohort_sha256="c" * 64)
    path = tmp_path / "cube.npz"
    save_cube(cube, path)
    loaded = load_cube(path)
    assert loaded.sha256 == cube.sha256
    assert loaded.window(days[0], days[0]).r_gross[0, 0] == pytest.approx(3.0)
    # Tampering with the stored arrays is detected.
    data = dict(np.load(path, allow_pickle=False))
    data["r_gross"] = data["r_gross"] + 1
    np.savez_compressed(path, **data)
    with pytest.raises(ValueError, match="hash"):
        load_cube(path)


def test_with_shift_moves_only_the_given_labelled_cells():
    days = weekdays(date(2021, 3, 1), 10)
    bars = {"AAA": hourly(days, [100.0] * 80), "BBB": hourly(days, [100.0] * 80)}
    cube = build_cube(
        eligibility(days[:2], ["AAA", "BBB"], [[True, True], [True, True]]), bars, SPEC, cohort_sha256="c" * 64
    )
    shifted = cube.with_shift(np.array([1]), np.array([0]), 0.5)
    before, after = cube.window(days[0], days[1]), shifted.window(days[0], days[1])
    assert after.r_cost[1, 0] == pytest.approx(before.r_cost[1, 0] + 0.5)
    assert np.array_equal(np.delete(after.r_cost.ravel(), 2), np.delete(before.r_cost.ravel(), 2))


def test_coverage_fails_closed():
    days = weekdays(date(2021, 3, 1), 5)
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), {}, SPEC, cohort_sha256="c" * 64)
    with pytest.raises(ValueError, match="hourly"):
        check_coverage(cube, CoverageSpec(max_no_reference_fraction=0.02, max_unlabelled_fraction_per_year=0.05))


def test_spec_identity_is_stable_and_sensitive():
    assert SPEC.identity == CubeSpec.model_validate(SPEC.model_dump()).identity
    changed = SPEC.model_copy(update={"decision_cost_bps": 0.0})
    assert changed.identity != SPEC.identity
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_cube.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.cube'`.

- [ ] **Step 3: Implement `cube.py`**

```python
# agentic_trader/research/pooled/cube.py
"""The label cube: one long bracket outcome per eligible (session, cohort symbol).

Built once per cohort and spec, it is shared by every formula and every run: a formula
only *selects* cells. Labels use the setup study's ``label_bracket`` (conservative
same-bar tie, gap fills at the open) from ``decision_price`` at 10:35 New York, with
stop = price - 2*ATR14 (through D-1) and target = price + 3R. Labels are readable only
through ``LabelCube.window`` so a campaign can open its stage windows in order; the
cube's own coverage report holds counts, never a return statistic.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.apriori.pead_study import decision_price
from agentic_trader.research.pooled.cohort import Eligibility, UniverseSpec
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket


__all__ = [
    "HIT_CODES",
    "BracketSpec",
    "CoverageSpec",
    "CubeSpec",
    "CubeView",
    "LabelCube",
    "build_cube",
    "check_coverage",
    "load_cube",
    "save_cube",
    "tiebreak_key",
]

HIT_CODES = {BracketHit.IMMATURE: 0, BracketHit.TARGET: 1, BracketHit.STOP: 2, BracketHit.TIMEOUT: 3}
# 20 sessions span at most ~30 calendar days; the slice handed to the labeller is trimmed to this.
_LABEL_SPAN = timedelta(days=45)
_ARRAYS = ("eligible", "labelled", "r_gross", "r_cost", "holding", "hit", "tiebreak", "dollar_volume")


class BracketSpec(BaseModel, frozen=True, extra="forbid"):
    decision_time_et: str
    stop_atr_multiple: float = Field(gt=0)
    atr_window: int = Field(ge=2)
    target_r: float = Field(gt=0)
    max_hold_sessions: int = Field(ge=1)

    @field_validator("decision_time_et")
    @classmethod
    def _clock(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    def decision_clock(self) -> time:
        return time.fromisoformat(self.decision_time_et)


class CoverageSpec(BaseModel, frozen=True, extra="forbid"):
    max_no_reference_fraction: float = Field(ge=0, lt=1)
    max_unlabelled_fraction_per_year: float = Field(ge=0, lt=1)


class CubeSpec(BaseModel, frozen=True, extra="forbid"):
    feed: Literal["alpaca:sip"]
    bars_from: date
    decisions: tuple[date, date]
    bars_through: date
    bracket: BracketSpec
    universe: UniverseSpec
    decision_cost_bps: float = Field(ge=0)

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def tiebreak_key(symbol: str, day: date) -> int:
    """Deterministic, non-alphabetical tie-break: the first 8 bytes of SHA-256(symbol|date)."""
    return int.from_bytes(hashlib.sha256(f"{symbol}|{day.isoformat()}".encode()).digest()[:8], "big")


@dataclass(frozen=True)
class CubeView:
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    offset: int  # index of the first session in the parent cube
    eligible: np.ndarray  # bool [S, N]
    labelled: np.ndarray  # bool [S, N]: eligible and resolved
    r_gross: np.ndarray  # float [S, N]: R at 0 bps, NaN where unlabelled
    r_cost: np.ndarray  # float [S, N]: R at the decisive cost, NaN where unlabelled
    holding: np.ndarray  # int [S, N]: sessions held (max hold where unlabelled)
    hit: np.ndarray  # int8 [S, N]: HIT_CODES
    tiebreak: np.ndarray  # uint64 [S, N]
    dollar_volume: np.ndarray  # float [S, N]

    def sub(self, lo: int, hi: int) -> CubeView:
        """Sessions ``lo:hi`` of this view (relative indices)."""
        return CubeView(
            sessions=self.sessions[lo:hi],
            symbols=self.symbols,
            offset=self.offset + lo,
            **{name: getattr(self, name)[lo:hi] for name in _ARRAYS},
        )


class LabelCube:
    """Holds the labels privately; ``window`` is the only way to read them."""

    def __init__(
        self,
        *,
        spec_identity: str,
        cohort_sha256: str,
        sessions: tuple[date, ...],
        symbols: tuple[str, ...],
        arrays: Mapping[str, np.ndarray],
        coverage: Mapping,
    ):
        missing = set(_ARRAYS) - set(arrays)
        if missing:
            raise ValueError(f"missing cube arrays: {sorted(missing)}")
        self._spec_identity = spec_identity
        self._cohort_sha256 = cohort_sha256
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        self._arrays = {name: np.asarray(arrays[name]) for name in _ARRAYS}
        self._coverage = dict(coverage)
        self._sha256: str | None = None

    @property
    def sessions(self) -> tuple[date, ...]:
        return self._sessions

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def spec_identity(self) -> str:
        return self._spec_identity

    @property
    def cohort_sha256(self) -> str:
        return self._cohort_sha256

    @property
    def coverage(self) -> dict:
        return dict(self._coverage)

    def _meta(self) -> dict:
        return {
            "spec_identity": self._spec_identity,
            "cohort_sha256": self._cohort_sha256,
            "sessions": [d.isoformat() for d in self._sessions],
            "symbols": list(self._symbols),
        }

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            digest = hashlib.sha256(json.dumps(self._meta(), sort_keys=True, separators=(",", ":")).encode())
            for name in _ARRAYS:
                array = np.ascontiguousarray(self._arrays[name])
                digest.update(name.encode())
                digest.update(str(array.dtype).encode())
                digest.update(str(array.shape).encode())
                digest.update(array.tobytes())
            self._sha256 = digest.hexdigest()
        return self._sha256

    def window(self, start: date, end: date) -> CubeView:
        lo, hi = bisect_left(self._sessions, start), bisect_right(self._sessions, end)
        if start > end or lo >= hi:
            raise ValueError(f"no cube session in {start.isoformat()}..{end.isoformat()}")
        return CubeView(
            sessions=self._sessions[lo:hi],
            symbols=self._symbols,
            offset=lo,
            **{name: self._arrays[name][lo:hi] for name in _ARRAYS},
        )

    def with_shift(self, session_idx: np.ndarray, symbol_idx: np.ndarray, delta: float) -> LabelCube:
        """A copy whose labelled cells at the given absolute indices gain ``delta`` R (power checks)."""
        arrays = {name: array.copy() for name, array in self._arrays.items()}
        rows, cols = np.asarray(session_idx, dtype=int), np.asarray(symbol_idx, dtype=int)
        keep = arrays["labelled"][rows, cols]
        for name in ("r_gross", "r_cost"):
            arrays[name][rows[keep], cols[keep]] += delta
        return LabelCube(
            spec_identity=self._spec_identity,
            cohort_sha256=self._cohort_sha256,
            sessions=self._sessions,
            symbols=self._symbols,
            arrays=arrays,
            coverage=self._coverage,
        )


class _Hourly:
    def __init__(self, frame: pd.DataFrame):
        index = pd.DatetimeIndex(frame.index)
        index = index if index.tz is not None else index.tz_localize("UTC")
        ordered = frame.set_axis(index.tz_convert("UTC")).sort_index()
        self._frame = ordered[~ordered.index.duplicated(keep="first")]
        self._index = self._frame.index

    def around(self, when) -> pd.DataFrame:
        lo = self._index.searchsorted(when - timedelta(days=1), side="left")
        hi = self._index.searchsorted(when + _LABEL_SPAN, side="right")
        return self._frame.iloc[lo:hi]


def build_cube(
    eligibility: Eligibility, hourly: Mapping[str, pd.DataFrame], spec: CubeSpec, *, cohort_sha256: str
) -> LabelCube:
    shape = eligibility.eligible.shape
    labelled = np.zeros(shape, dtype=bool)
    r_gross = np.full(shape, np.nan)
    r_cost = np.full(shape, np.nan)
    holding = np.full(shape, spec.bracket.max_hold_sessions, dtype=np.int16)
    hit = np.zeros(shape, dtype=np.int8)
    tiebreak = np.zeros(shape, dtype=np.uint64)
    unlabelled: dict[str, Counter[str]] = defaultdict(Counter)
    eligible_by_year: Counter[str] = Counter()
    labelled_by_year: Counter[str] = Counter()
    prepared = {s: _Hourly(f) for s, f in hourly.items() if f is not None and not f.empty}
    clock = spec.bracket.decision_clock()
    for row, day in enumerate(eligibility.sessions):
        year = str(day.year)
        when = decision_at(day, clock)
        for col, symbol in enumerate(eligibility.symbols):
            tiebreak[row, col] = tiebreak_key(symbol, day)
            if not eligibility.eligible[row, col]:
                continue
            eligible_by_year[year] += 1
            bars = prepared.get(symbol)
            if bars is None:
                unlabelled["no_hourly_bars"][year] += 1
                continue
            window = bars.around(when)
            price = decision_price(window, when)
            if price is None:
                unlabelled["no_decision_price"][year] += 1
                continue
            risk = spec.bracket.stop_atr_multiple * float(eligibility.atr[row, col])
            if not (math.isfinite(risk) and risk > 0 and price - risk > 0):
                unlabelled["degenerate_levels"][year] += 1
                continue
            levels = SetupLevels("LONG", price, price - risk, price + spec.bracket.target_r * risk)
            outcome = label_bracket(
                levels,
                when,
                window,
                max_hold_sessions=spec.bracket.max_hold_sessions,
                cost_bps_per_side=spec.decision_cost_bps,
            )
            if outcome.hit == BracketHit.IMMATURE:
                unlabelled["immature"][year] += 1
                continue
            labelled[row, col] = True
            labelled_by_year[year] += 1
            r_gross[row, col] = outcome.r
            r_cost[row, col] = outcome.r_cost
            holding[row, col] = outcome.holding_sessions
            hit[row, col] = HIT_CODES[outcome.hit]
    coverage = {
        "sessions": len(eligibility.sessions),
        "skipped_sessions": dict(eligibility.skipped_sessions),
        "eligibility_reasons": dict(eligibility.reasons),
        "eligible_by_year": dict(eligible_by_year),
        "labelled_by_year": dict(labelled_by_year),
        "unlabelled_by_year": {reason: dict(years) for reason, years in unlabelled.items()},
        "reference_names": {
            "min": min(eligibility.reference_names, default=0),
            "max": max(eligibility.reference_names, default=0),
        },
    }
    return LabelCube(
        spec_identity=spec.identity,
        cohort_sha256=cohort_sha256,
        sessions=eligibility.sessions,
        symbols=eligibility.symbols,
        arrays={
            "eligible": eligibility.eligible.copy(),
            "labelled": labelled,
            "r_gross": r_gross,
            "r_cost": r_cost,
            "holding": holding,
            "hit": hit,
            "tiebreak": tiebreak,
            "dollar_volume": eligibility.dollar_volume.copy(),
        },
        coverage=coverage,
    )


def check_coverage(cube: LabelCube, coverage: CoverageSpec) -> None:
    """Fail closed on a gappy sample (the study never tests on it)."""
    report = cube.coverage
    sessions = max(report["sessions"], 1)
    no_reference = report["skipped_sessions"].get("no_reference", 0)
    if no_reference / sessions > coverage.max_no_reference_fraction:
        raise ValueError(
            f"static reference unavailable on {no_reference}/{sessions} sessions, above the frozen "
            f"{coverage.max_no_reference_fraction:.0%} limit"
        )
    gaps: Counter[str] = Counter()
    for reason in ("no_hourly_bars", "no_decision_price"):
        gaps.update(report["unlabelled_by_year"].get(reason, {}))
    for year, eligible in report["eligible_by_year"].items():
        if eligible and gaps[year] / eligible > coverage.max_unlabelled_fraction_per_year:
            raise ValueError(
                f"{gaps[year]}/{eligible} eligible cells in {year} have no hourly decision data, above the frozen "
                f"{coverage.max_unlabelled_fraction_per_year:.0%} limit"
            )


def save_cube(cube: LabelCube, path: Path) -> None:
    meta = {**cube._meta(), "coverage": cube.coverage, "sha256": cube.sha256}
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, meta=np.array(json.dumps(meta, sort_keys=True)), **cube._arrays)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_cube(path: Path) -> LabelCube:
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(str(data["meta"]))
        arrays = {name: data[name] for name in _ARRAYS}
    cube = LabelCube(
        spec_identity=meta["spec_identity"],
        cohort_sha256=meta["cohort_sha256"],
        sessions=tuple(date.fromisoformat(d) for d in meta["sessions"]),
        symbols=tuple(meta["symbols"]),
        arrays=arrays,
        coverage=meta["coverage"],
    )
    if cube.sha256 != meta["sha256"]:
        raise ValueError(f"cube hash mismatch for {path}: stored {meta['sha256']}, computed {cube.sha256}")
    return cube
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/cube.py tests/research/pooled/test_cube.py
```

---

### Task 4: Formula document, score panels and pick selection

**Files:**
- Create: `agentic_trader/research/pooled/formula.py`
- Test: `tests/research/pooled/test_formula.py`

**Interfaces:**
- Consumes: `CubeView` (Task 3); `compile_expression`, `AlphaExpressionEvaluator` (`research/alpha/dsl.py`); `_by_session` (`pead_events`).
- Produces: `require_dimensionless(expression) -> None`, `expression_nodes(expression) -> int`, `FormulaFilter`, `Formula` (`.lookback`, `.identity`), `evaluate_panel(expression, adjusted, trading_days, sessions, symbols) -> np.ndarray[S,N]`, `allowed_mask(formula, filter_values, eligible) -> np.ndarray[S,N] bool`, `Picks(session_idx, symbol_idx)` (`.cells() -> set[tuple[int,int]]`), `select_picks(scores, allowed, view, k) -> Picks`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_formula.py
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import (
    Formula,
    FormulaFilter,
    Picks,
    allowed_mask,
    evaluate_panel,
    expression_nodes,
    select_picks,
)


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def daily(days: list[date], closes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=ET_TZ).astimezone(UTC) for d in days])
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6}, index=index)


def view(scores_shape, *, holding=1, tiebreak=None) -> CubeView:
    s, n = scores_shape
    sessions = tuple(weekdays(date(2021, 3, 1), s))
    tb = np.arange(n, dtype=np.uint64)[None, :].repeat(s, 0) if tiebreak is None else tiebreak
    return CubeView(
        sessions=sessions,
        symbols=tuple(f"S{j}" for j in range(n)),
        offset=0,
        eligible=np.ones((s, n), bool),
        labelled=np.ones((s, n), bool),
        r_gross=np.zeros((s, n)),
        r_cost=np.zeros((s, n)),
        holding=np.full((s, n), holding, dtype=np.int16),
        hit=np.ones((s, n), np.int8),
        tiebreak=tb,
        dollar_volume=np.ones((s, n)),
    )


def test_scores_must_be_dimensionless():
    Formula(score="close / ts_max(high, 252)", k=3)
    with pytest.raises(ValidationError, match="dimensionless"):
        Formula(score="ts_slope(close, 20)", k=3)
    with pytest.raises(ValidationError, match="dimensionless"):
        Formula(score="roc(close, 5)", filters=[FormulaFilter(expression="volume", max_quantile=0.5)], k=3)


def test_formula_identity_and_lookback():
    f = Formula(
        score="-1.0 * roc(close, 21)", filters=[FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5)], k=3
    )
    assert f.lookback >= 21
    assert f.identity == Formula.model_validate(f.model_dump()).identity
    assert expression_nodes("roc(close, 5)") > expression_nodes("close")


def test_scores_use_bars_through_the_previous_session_only():
    days = weekdays(date(2021, 1, 4), 40)
    closes = list(np.linspace(50, 60, 40))
    base = {"AAA": daily(days, closes)}
    shocked = {"AAA": daily(days, closes[:30] + [1.0] * 10)}  # collapses from the decision day on
    sessions = [days[30]]
    a = evaluate_panel("roc(close, 5)", base, days, sessions, ["AAA"])
    b = evaluate_panel("roc(close, 5)", shocked, days, sessions, ["AAA"])
    assert a[0, 0] == pytest.approx(closes[29] / closes[24] - 1)
    assert a[0, 0] == b[0, 0]


def test_quantile_filters_are_cross_sectional_among_eligible_finite_names():
    values = np.array([[1.0, 2.0, 3.0, 4.0, np.nan]])
    eligible = np.array([[True, True, True, True, True]])
    f = Formula(score="roc(close, 5)", filters=[FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5)], k=1)
    mask = allowed_mask(f, [values], eligible)
    assert mask.tolist() == [[True, True, False, False, False]]


def test_top_k_by_score_with_hash_tiebreak():
    scores = np.array([[0.5, 0.9, 0.9, 0.1]])
    tb = np.array([[4, 7, 3, 1]], dtype=np.uint64)
    picks = select_picks(scores, np.ones_like(scores, bool), view(scores.shape, tiebreak=tb), k=2)
    assert picks.cells() == {(0, 2), (0, 1)}  # both 0.9s; the lower hash first
    assert list(picks.symbol_idx) == [2, 1]


def test_a_held_name_is_skipped_until_after_its_exit_session():
    scores = np.tile(np.array([[0.9, 0.5, 0.1]]), (4, 1))
    picks = select_picks(scores, np.ones_like(scores, bool), view(scores.shape, holding=2), k=1)
    # Picked on 0, held through 1 (2 sessions), free again on 2.
    assert list(zip(picks.session_idx, picks.symbol_idx, strict=True)) == [(0, 0), (1, 1), (2, 0), (3, 1)]


def test_ineligible_or_nan_scores_are_never_picked():
    scores = np.array([[np.nan, 0.2, 0.9]])
    v = view(scores.shape)
    v.eligible[0, 2] = False
    picks = select_picks(scores, np.ones_like(scores, bool), v, k=3)
    assert picks.cells() == {(0, 1)}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_formula.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `formula.py`**

```python
# agentic_trader/research/pooled/formula.py
"""A pooled formula: a dimensionless DSL score, optional cross-sectional filters and k.

Scores are evaluated per symbol on adjusted daily bars and read at D-1, so a decision at
10:35 on D uses only completed sessions -- the live scan sees the same bars. Picks are the
top-k eligible names per session by score, skipping names the formula still holds and
breaking ties by a hash (never the alphabet).
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator, compile_expression
from agentic_trader.research.apriori.pead_events import _by_session
from agentic_trader.research.pooled.cube import CubeView


__all__ = [
    "Formula",
    "FormulaFilter",
    "Picks",
    "allowed_mask",
    "evaluate_panel",
    "expression_nodes",
    "require_dimensionless",
    "select_picks",
]


def require_dimensionless(expression: str) -> None:
    units = compile_expression(expression).units
    if tuple(float(u) for u in units) != (0.0, 0.0):
        raise ValueError(f"expression must be dimensionless to rank across names: {expression!r} has units {units}")


def expression_nodes(expression: str) -> int:
    return sum(1 for _ in ast.walk(ast.parse(expression, mode="eval")))


class FormulaFilter(BaseModel, frozen=True, extra="forbid"):
    expression: str
    min_quantile: float | None = Field(default=None, ge=0, le=1)
    max_quantile: float | None = Field(default=None, ge=0, le=1)

    @field_validator("expression")
    @classmethod
    def _dimensionless(cls, value: str) -> str:
        require_dimensionless(value)
        return value

    @model_validator(mode="after")
    def _bounds(self) -> FormulaFilter:
        if self.min_quantile is None and self.max_quantile is None:
            raise ValueError("a filter needs min_quantile or max_quantile")
        if self.min_quantile is not None and self.max_quantile is not None and self.min_quantile >= self.max_quantile:
            raise ValueError("min_quantile must be below max_quantile")
        return self


class Formula(BaseModel, frozen=True, extra="forbid"):
    score: str
    filters: tuple[FormulaFilter, ...] = ()
    k: int = Field(ge=1)

    @field_validator("score")
    @classmethod
    def _dimensionless(cls, value: str) -> str:
        require_dimensionless(value)
        return value

    @property
    def lookback(self) -> int:
        return max(compile_expression(e).lookback for e in (self.score, *(f.expression for f in self.filters)))

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def evaluate_panel(
    expression: str,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    sessions: Sequence[date],
    symbols: Sequence[str],
) -> np.ndarray:
    """[S, N] values known at each decision: the expression on adjusted daily bars, read at D-1."""
    position = {day: i for i, day in enumerate(trading_days)}
    previous = pd.DatetimeIndex([trading_days[position[day] - 1] for day in sessions])
    out = np.full((len(sessions), len(symbols)), np.nan)
    evaluator = AlphaExpressionEvaluator()
    for col, symbol in enumerate(symbols):
        frame = adjusted.get(symbol)
        if frame is None or frame.empty:
            continue
        keyed = _by_session(frame)
        keyed = keyed.set_axis(pd.DatetimeIndex(keyed.index))
        series = evaluator.evaluate(expression, keyed)
        out[:, col] = series.reindex(previous).to_numpy(float)
    out[~np.isfinite(out)] = np.nan
    return out


def allowed_mask(formula: Formula, filter_values: Sequence[np.ndarray], eligible: np.ndarray) -> np.ndarray:
    """Eligible names passing every cross-sectional quantile filter, session by session."""
    mask = eligible.copy()
    for spec, values in zip(formula.filters, filter_values, strict=True):
        for row in range(values.shape[0]):
            finite = eligible[row] & np.isfinite(values[row])
            if not finite.any():
                mask[row] = False
                continue
            keep = finite.copy()
            if spec.max_quantile is not None:
                keep &= values[row] <= np.quantile(values[row][finite], spec.max_quantile)
            if spec.min_quantile is not None:
                keep &= values[row] >= np.quantile(values[row][finite], spec.min_quantile)
            mask[row] &= keep
    return mask


@dataclass(frozen=True)
class Picks:
    session_idx: np.ndarray  # int, relative to the view
    symbol_idx: np.ndarray  # int

    def cells(self) -> set[tuple[int, int]]:
        return set(zip(self.session_idx.tolist(), self.symbol_idx.tolist(), strict=True))


def select_picks(scores: np.ndarray, allowed: np.ndarray, view: CubeView, k: int) -> Picks:
    """Top-k eligible names per session by score; a picked name stays held through its exit session."""
    sessions, names = scores.shape
    held_through = np.full(names, -1, dtype=np.int64)
    rows: list[int] = []
    cols: list[int] = []
    for row in range(sessions):
        candidates = np.flatnonzero(allowed[row] & view.eligible[row] & np.isfinite(scores[row]) & (held_through < row))
        if candidates.size == 0:
            continue
        order = np.lexsort((view.tiebreak[row, candidates], -scores[row, candidates]))
        for col in candidates[order[:k]]:
            held_through[col] = row + int(view.holding[row, col]) - 1
            rows.append(row)
            cols.append(int(col))
    return Picks(np.asarray(rows, dtype=np.int64), np.asarray(cols, dtype=np.int64))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/formula.py tests/research/pooled/test_formula.py
```

---

### Task 5: Pooled statistics

**Files:**
- Create: `agentic_trader/research/pooled/stats.py`
- Modify: `docs/superpowers/specs/2026-09-28-pooled-alpha-mining-design.md` (calendar-time ruling, one sentence in "Statistics")
- Test: `tests/research/pooled/test_stats.py`

**Interfaces:**
- Consumes: `CubeView` (Task 3); `Picks` (Task 4); `_stationary_index_draws` (`setups/study`); `_ci90`, `_p_one_sided_positive` (`setups/baserates`).
- Produces: `SessionTable(sessions, pick_sum, pick_n, control_mean, edge, pick_rows, dropped_unlabelled, dropped_purged)`, `session_table(picks, view, *, purge, column="r_cost") -> SessionTable`, `bootstrap_draws(n, block_mean, draws, seed) -> np.ndarray`, `paired_edge_test(table, draws) -> dict` (keys `mean, se, t, ci90, p_one_sided, n_sessions, holds`), `leg_mean_test(table, draws) -> dict` (keys `mean, ci90, p_one_sided, n, holds`), `trimmed_mean(values, fraction) -> float`, `two_way_clustered(rows) -> dict`, `calendar_time_newey_west(rows, n_sessions, lag) -> dict`, `design_effect(rows) -> dict`.

`pick_rows` is a DataFrame with columns `session_idx, symbol_idx, r_gross, r_cost, hit, holding, control_mean, residual` (only labelled, non-purged picks).

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_stats.py
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import Picks
from agentic_trader.research.pooled.stats import (
    bootstrap_draws,
    calendar_time_newey_west,
    design_effect,
    leg_mean_test,
    paired_edge_test,
    session_table,
    trimmed_mean,
    two_way_clustered,
)


def cube_view(r: np.ndarray, *, holding=1, labelled=None) -> CubeView:
    s, n = r.shape
    sessions = tuple(date(2021, 3, 1) + timedelta(days=i) for i in range(s))
    lab = np.isfinite(r) if labelled is None else labelled
    return CubeView(
        sessions=sessions,
        symbols=tuple(f"S{j}" for j in range(n)),
        offset=0,
        eligible=np.ones((s, n), bool),
        labelled=lab,
        r_gross=r.copy(),
        r_cost=r.copy(),
        holding=np.full((s, n), holding, dtype=np.int16) if np.isscalar(holding) else holding,
        hit=np.ones((s, n), np.int8),
        tiebreak=np.zeros((s, n), np.uint64),
        dollar_volume=np.ones((s, n)),
    )


def picks(cells) -> Picks:
    rows, cols = zip(*cells, strict=True)
    return Picks(np.array(rows), np.array(cols))


def test_paired_edge_is_pick_mean_minus_all_eligible_mean_per_session():
    r = np.array([[1.0, 0.0, -1.0], [2.0, 0.0, 1.0]])
    table = session_table(picks([(0, 0), (1, 2)]), cube_view(r), purge=False)
    assert table.control_mean.tolist() == [0.0, 1.0]
    assert table.edge.tolist() == [1.0, 0.0]
    result = paired_edge_test(table, bootstrap_draws(2, 20, 50, 1))
    assert result["mean"] == pytest.approx(0.5)
    assert result["n_sessions"] == 2


def test_sessions_without_picks_do_not_dilute_the_edge():
    r = np.array([[1.0, 0.0], [5.0, 5.0], [1.0, 0.0]])
    table = session_table(picks([(0, 0), (2, 0)]), cube_view(r), purge=False)
    assert np.isnan(table.edge[1])
    result = paired_edge_test(table, bootstrap_draws(3, 20, 200, 3))
    assert result["mean"] == pytest.approx(0.5)
    finite = result["ci90"]
    assert finite[0] == pytest.approx(0.5) and finite[1] == pytest.approx(0.5)  # every draw averages 0.5s only


def test_purge_drops_cells_whose_hold_crosses_the_window_end():
    r = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
    holding = np.array([[1, 1], [3, 1], [1, 2]], dtype=np.int16)
    table = session_table(picks([(0, 0), (1, 0), (2, 0)]), cube_view(r, holding=holding), purge=True)
    assert table.dropped_purged == 1  # session 1's pick exits at index 3 > last (2)
    assert table.pick_n.tolist() == [1, 0, 1]
    assert table.control_mean[2] == pytest.approx(1.0)  # (2,1) exits at 3: purged from the control too


def test_unlabelled_picks_are_dropped_and_counted():
    r = np.array([[np.nan, 0.0, 1.0]])
    table = session_table(picks([(0, 0), (0, 2)]), cube_view(r), purge=False)
    assert table.dropped_unlabelled == 1
    assert table.pick_n.tolist() == [1]


def test_leg_mean_weights_each_pick():
    r = np.array([[3.0, 1.0], [0.0, 0.0]])
    table = session_table(picks([(0, 0), (0, 1), (1, 0)]), cube_view(r), purge=False)
    assert leg_mean_test(table, bootstrap_draws(2, 20, 50, 1))["mean"] == pytest.approx(4.0 / 3.0)


def test_holds_requires_a_positive_lower_bound():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0, 1.0, (400, 20))
    r[:, 0] += 0.8
    table = session_table(picks([(i, 0) for i in range(400)]), cube_view(r), purge=False)
    good = paired_edge_test(table, bootstrap_draws(400, 20, 500, 7))
    assert good["holds"] and good["t"] > 3
    flat = np.zeros((400, 20))
    none = session_table(picks([(i, 1) for i in range(400)]), cube_view(flat), purge=False)
    result = paired_edge_test(none, bootstrap_draws(400, 20, 500, 7))
    assert result["mean"] == 0.0 and not result["holds"]  # a zero edge never holds


def test_trimmed_mean_matches_pead_semantics():
    assert trimmed_mean(np.array([-100.0, 1.0, 2.0, 3.0, 100.0]), 0.2) == pytest.approx(2.0)


def test_two_way_clustering_and_design_effect_on_a_reference_case():
    rows = pd.DataFrame(
        {
            "session_idx": [0, 0, 1, 1, 2, 2],
            "symbol_idx": [0, 1, 0, 1, 0, 1],
            "residual": [1.0, 1.0, -1.0, -1.0, 0.5, 0.5],
        }
    )
    clustered = two_way_clustered(rows)
    e = rows["residual"] - rows["residual"].mean()
    by_date = sum(g.sum() ** 2 for _, g in e.groupby(rows["session_idx"]))
    by_symbol = sum(g.sum() ** 2 for _, g in e.groupby(rows["symbol_idx"]))
    white = float((e**2).sum())
    variance = by_date + by_symbol - white
    variance = variance if variance > 0 else max(by_date, by_symbol)  # Thompson; fall back when non-positive
    assert clustered["se"] == pytest.approx(np.sqrt(variance) / len(rows))
    deff = design_effect(rows)
    assert deff["icc"] == pytest.approx(1.0)  # identical residuals within each session
    assert deff["deff"] == pytest.approx(2.0)


def test_calendar_time_series_is_finite_and_reports_t():
    rows = pd.DataFrame({"session_idx": [0, 1, 2, 3], "holding": [2, 2, 1, 1], "residual": [1.0, 0.5, 0.2, 0.1]})
    result = calendar_time_newey_west(rows, n_sessions=5, lag=2)
    assert np.isfinite(result["mean"]) and np.isfinite(result["t"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_stats.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `stats.py`**

```python
# agentic_trader/research/pooled/stats.py
"""Session-paired edge statistics for pooled picks.

The decisive statistic is the mean over sessions with a pick of
``mean R(picks) - mean R(all labelled eligible cells)`` that session, with a stationary
bootstrap over whole sessions (``_stationary_index_draws``, block mean 20). Sessions
without a pick carry zero weight in the point estimate and in every bootstrap draw.
Two-way clustered errors, a calendar-time Newey-West series and the design effect are
cross-checks, reported and never decisive. The calendar-time series spreads each pick's
residual evenly over its holding sessions (the cube stores final R, not a daily path).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import Picks
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive
from agentic_trader.research.setups.study import _stationary_index_draws


__all__ = [
    "SessionTable",
    "bootstrap_draws",
    "calendar_time_newey_west",
    "design_effect",
    "leg_mean_test",
    "paired_edge_test",
    "session_table",
    "trimmed_mean",
    "two_way_clustered",
]


@dataclass(frozen=True)
class SessionTable:
    sessions: tuple[date, ...]
    pick_sum: np.ndarray  # float [S]
    pick_n: np.ndarray  # float [S]
    control_mean: np.ndarray  # float [S], NaN without a labelled cell
    edge: np.ndarray  # float [S], NaN without a pick
    pick_rows: pd.DataFrame
    dropped_unlabelled: int
    dropped_purged: int


def session_table(picks: Picks, view: CubeView, *, purge: bool, column: str = "r_cost") -> SessionTable:
    values = getattr(view, column)
    usable = view.labelled.copy()
    if purge:
        last = len(view.sessions) - 1
        exits = np.arange(len(view.sessions))[:, None] + view.holding.astype(np.int64) - 1
        usable &= exits <= last
    counts = usable.sum(axis=1)
    sums = np.where(usable, values, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        control_mean = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    pick_sum = np.zeros(len(view.sessions))
    pick_n = np.zeros(len(view.sessions))
    rows = []
    dropped_unlabelled = dropped_purged = 0
    for row, col in zip(picks.session_idx.tolist(), picks.symbol_idx.tolist(), strict=True):
        if not view.labelled[row, col]:
            dropped_unlabelled += 1
            continue
        if not usable[row, col]:
            dropped_purged += 1
            continue
        value = float(values[row, col])
        pick_sum[row] += value
        pick_n[row] += 1
        rows.append(
            {
                "session_idx": row,
                "symbol_idx": col,
                "r_gross": float(view.r_gross[row, col]),
                "r_cost": float(view.r_cost[row, col]),
                "hit": int(view.hit[row, col]),
                "holding": int(view.holding[row, col]),
                "control_mean": float(control_mean[row]),
                "residual": value - float(control_mean[row]),
            }
        )
    with np.errstate(invalid="ignore", divide="ignore"):
        edge = np.where(
            (pick_n > 0) & np.isfinite(control_mean), pick_sum / np.maximum(pick_n, 1) - control_mean, np.nan
        )
    columns = ["session_idx", "symbol_idx", "r_gross", "r_cost", "hit", "holding", "control_mean", "residual"]
    return SessionTable(
        sessions=view.sessions,
        pick_sum=pick_sum,
        pick_n=pick_n,
        control_mean=control_mean,
        edge=edge,
        pick_rows=pd.DataFrame(rows, columns=columns),
        dropped_unlabelled=dropped_unlabelled,
        dropped_purged=dropped_purged,
    )


@lru_cache(maxsize=16)
def bootstrap_draws(n: int, block_mean: float, draws: int, seed: int) -> np.ndarray:
    return _stationary_index_draws(n, block_mean, draws, seed)


def _ratio_boot(numerator: np.ndarray, weight: np.ndarray, draws: np.ndarray) -> np.ndarray:
    if draws.shape[1] == 0:
        return np.full(draws.shape[0], np.nan)
    num = numerator[draws].sum(axis=1)
    den = weight[draws].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.maximum(den, 1e-300), np.nan)


def _summary(point: float, boot: np.ndarray) -> dict:
    finite = boot[np.isfinite(boot)]
    se = float(np.std(finite, ddof=1)) if finite.size > 1 else float("nan")
    ci90 = _ci90(boot)
    return {
        "mean": point,
        "se": se,
        "t": point / se if se and math.isfinite(se) and se > 0 else float("nan"),
        "ci90": ci90,
        "p_one_sided": _p_one_sided_positive(boot),
        "finite_draws": int(finite.size),
        "holds": bool(math.isfinite(point) and point > 0 and math.isfinite(ci90[0]) and ci90[0] > 0),
    }


def paired_edge_test(table: SessionTable, draws: np.ndarray) -> dict:
    weight = np.isfinite(table.edge).astype(float)
    numerator = np.where(weight > 0, table.edge, 0.0)
    total = weight.sum()
    point = float(numerator.sum() / total) if total else float("nan")
    return {**_summary(point, _ratio_boot(numerator, weight, draws)), "n_sessions": int(total)}


def leg_mean_test(table: SessionTable, draws: np.ndarray) -> dict:
    total = table.pick_n.sum()
    point = float(table.pick_sum.sum() / total) if total else float("nan")
    return {**_summary(point, _ratio_boot(table.pick_sum, table.pick_n, draws)), "n": int(total)}


def trimmed_mean(values: np.ndarray, fraction: float) -> float:
    ordered = np.sort(values[np.isfinite(values)])
    cut = math.floor(fraction * len(ordered))
    kept = ordered[cut : len(ordered) - cut]
    return float(kept.mean()) if kept.size else float("nan")


def two_way_clustered(rows: pd.DataFrame) -> dict:
    """Standard error of the mean residual, clustered by session and by symbol (Thompson 2011)."""
    if rows.empty:
        return {"mean": float("nan"), "se": float("nan"), "t": float("nan")}
    residual = rows["residual"].to_numpy(float)
    mean = float(residual.mean())
    e = pd.Series(residual - mean)
    n = len(e)
    by_date = float(sum(g.sum() ** 2 for _, g in e.groupby(rows["session_idx"].to_numpy())))
    by_symbol = float(sum(g.sum() ** 2 for _, g in e.groupby(rows["symbol_idx"].to_numpy())))
    white = float((e**2).sum())
    variance = by_date + by_symbol - white
    if variance <= 0:
        variance = max(by_date, by_symbol)
    se = math.sqrt(variance) / n
    return {"mean": mean, "se": se, "t": mean / se if se > 0 else float("nan")}


def calendar_time_newey_west(rows: pd.DataFrame, n_sessions: int, lag: int) -> dict:
    """Mean per-session residual of open picks (each spread over its hold), Newey-West t."""
    if rows.empty or n_sessions == 0:
        return {"mean": float("nan"), "se": float("nan"), "t": float("nan"), "sessions": 0}
    totals = np.zeros(n_sessions)
    counts = np.zeros(n_sessions)
    for start, hold, residual in rows[["session_idx", "holding", "residual"]].itertuples(index=False):
        end = min(int(start) + int(hold), n_sessions)
        totals[int(start) : end] += float(residual) / max(int(hold), 1)
        counts[int(start) : end] += 1
    series = totals[counts > 0] / counts[counts > 0]
    t_len = len(series)
    mean = float(series.mean())
    x = series - mean
    variance = float(x @ x) / t_len
    for k in range(1, min(lag, t_len - 1) + 1):
        variance += 2 * (1 - k / (lag + 1)) * float(x[k:] @ x[:-k]) / t_len
    se = math.sqrt(max(variance, 0.0) / t_len)
    return {"mean": mean, "se": se, "t": mean / se if se > 0 else float("nan"), "sessions": t_len}


def design_effect(rows: pd.DataFrame) -> dict:
    """Mean picks per session, intra-session correlation (one-way ANOVA) and DEFF = 1 + (m-1)rho."""
    if rows.empty:
        return {"picks_per_session": float("nan"), "icc": float("nan"), "deff": float("nan"), "effective_n": 0.0}
    groups = [g.to_numpy(float) for _, g in rows.groupby("session_idx")["residual"]]
    n = sum(len(g) for g in groups)
    k = len(groups)
    m_bar = n / k
    grand = float(np.concatenate(groups).mean())
    if k < 2 or n <= k:
        return {"picks_per_session": m_bar, "icc": float("nan"), "deff": float("nan"), "effective_n": float(n)}
    ssb = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    ssw = sum(((g - g.mean()) ** 2).sum() for g in groups)
    msb, msw = ssb / (k - 1), ssw / (n - k)
    n0 = (n - sum(len(g) ** 2 for g in groups) / n) / (k - 1)
    denominator = msb + (n0 - 1) * msw
    icc = float((msb - msw) / denominator) if denominator > 0 else 1.0
    icc = min(max(icc, 0.0), 1.0)
    deff = 1 + (m_bar - 1) * icc
    return {"picks_per_session": m_bar, "icc": icc, "deff": deff, "effective_n": n / deff}
```

- [ ] **Step 4: Record the calendar-time ruling in the spec**

In `docs/superpowers/specs/2026-09-28-pooled-alpha-mining-design.md`, section "Statistics", replace the calendar-time bullet's first sentence with:

```markdown
- **Cross-checks, reported, never decisive.** A calendar-time portfolio (each pick's
  residual R spread evenly over its holding sessions — the cube stores final R, not a
  daily path — averaged over the picks open each session) with Newey-West lag 19; two-way
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/stats.py tests/research/pooled/test_stats.py docs/superpowers/specs/2026-09-28-pooled-alpha-mining-design.md
```

---

### Task 6: Campaign protocol model and the frozen `campaign-v1.json`

**Files:**
- Create: `agentic_trader/research/pooled/campaign.py` (protocol models only in this task)
- Create: `config/research/pooled/campaign-v1.json`
- Test: `tests/research/pooled/test_campaign_protocol.py`

**Interfaces:**
- Consumes: `CubeSpec`, `BracketSpec`, `UniverseSpec`, `CoverageSpec` (Tasks 2–3); `require_dimensionless` (Task 4); `MUTATION_OPERATORS` (`research/alpha/search.py`); `compile_expression`.
- Produces: `CampaignProtocol` (`.cube_spec() -> CubeSpec`), `LoadedProtocol(protocol, sha256, path)`, `load_campaign_protocol(path) -> LoadedProtocol`, and the sub-models `StageWindows`, `Family`, `ExcludedFamily`, `SearchSpec`, `CampaignBootstrap`, `DiscoveryGate`, `SelectionGate`, `ConfirmationGate`, `PowerSpec`, `PowerSearchSpec`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_campaign_protocol.py
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = REPO / "config/research/pooled/campaign-v1.json"


def _mutated(tmp_path, mutate) -> Path:
    doc = json.loads(PROTOCOL.read_text())
    mutate(doc)
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(doc))
    return path


def test_committed_protocol_is_valid_and_pins_the_committed_cohort():
    loaded = load_campaign_protocol(PROTOCOL)
    protocol = loaded.protocol
    assert protocol.cohort_sha256 == load_cohort(REPO / protocol.cohort).sha256
    assert len(protocol.families) == 12
    assert protocol.formula_budget == 200
    assert protocol.cube_spec().decisions == (protocol.windows.discovery[0], protocol.windows.confirmation[1])
    assert {f.id for f in protocol.excluded_families} >= {"vol_20", "sector_relative_momentum"}


def test_windows_must_be_ordered_and_disjoint(tmp_path):
    def overlap(doc):
        doc["windows"]["selection"][0] = doc["windows"]["discovery"][1]

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, overlap))


def test_seeds_must_be_dimensionless_and_avoid_forbidden_operators(tmp_path):
    def price_seed(doc):
        doc["families"][0]["seeds"].append("ts_slope(close, 20)")

    def forbidden(doc):
        doc["families"][0]["seeds"].append("-ts_std(returns, 20)")

    for mutate in (price_seed, forbidden):
        with pytest.raises(ValidationError):
            load_campaign_protocol(_mutated(tmp_path, mutate))


def test_mutation_operators_must_be_known_and_allowed(tmp_path):
    def unknown(doc):
        doc["families"][0]["mutation_operators"].append("realized_vol")

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, unknown))


def test_power_blocks_reference_declared_values(tmp_path):
    def bad_delta(doc):
        doc["power"]["detection_delta"] = 0.2

    def bad_family(doc):
        doc["power_search"]["families"] = ["nope"]

    for mutate in (bad_delta, bad_family):
        with pytest.raises(ValidationError):
            load_campaign_protocol(_mutated(tmp_path, mutate))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_protocol.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the protocol models (`campaign.py`)**

```python
# agentic_trader/research/pooled/campaign.py
"""The budgeted pooled campaign: frozen protocol, and (below) its pure stage logic.

The protocol is committed before any real-data run of the pooled lane; its SHA-256 is
recorded by every Part 1 manifest. Discovery, selection and confirmation are pure
functions over cube windows with an injected ledger, so power check A runs them
unchanged on synthetic cubes with an in-memory ledger.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.alpha.search import MUTATION_OPERATORS
from agentic_trader.research.pooled.cohort import UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CoverageSpec, CubeSpec
from agentic_trader.research.pooled.formula import require_dimensionless


__all__ = ["CampaignProtocol", "LoadedProtocol", "load_campaign_protocol"]


def _calls(expression: str) -> set[str]:
    return {
        node.func.id.lower()
        for node in ast.walk(ast.parse(expression, mode="eval"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


class StageWindows(BaseModel, frozen=True, extra="forbid"):
    discovery: tuple[date, date]
    selection: tuple[date, date]
    confirmation: tuple[date, date]

    @model_validator(mode="after")
    def _ordered(self) -> StageWindows:
        spans = (self.discovery, self.selection, self.confirmation)
        if any(start > end for start, end in spans):
            raise ValueError("each window must be ordered")
        if not (self.discovery[1] < self.selection[0] and self.selection[1] < self.confirmation[0]):
            raise ValueError("windows must be disjoint and in stage order")
        return self


class Family(BaseModel, frozen=True, extra="forbid"):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    rationale: str
    seeds: tuple[str, ...] = Field(min_length=1)
    mutation_operators: tuple[str, ...]

    @field_validator("seeds")
    @classmethod
    def _dimensionless(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for seed in value:
            require_dimensionless(seed)
        return value

    @field_validator("mutation_operators")
    @classmethod
    def _known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - set(MUTATION_OPERATORS)
        if unknown:
            raise ValueError(f"unknown mutation operators: {sorted(unknown)}")
        return value


class ExcludedFamily(BaseModel, frozen=True, extra="forbid"):
    id: str
    reason: str


class SearchSpec(BaseModel, frozen=True, extra="forbid"):
    seed: int
    archive_size: int = Field(ge=1)
    forbidden_operators: tuple[str, ...]


class CampaignBootstrap(BaseModel, frozen=True, extra="forbid"):
    block_mean: float = Field(gt=1)
    discovery_draws: int = Field(ge=100)
    selection_draws: int = Field(ge=100)
    confirmation_draws: int = Field(ge=100)
    seed: int


class DiscoveryGate(BaseModel, frozen=True, extra="forbid"):
    min_t: float = Field(gt=0)
    blocks: int = Field(ge=2)
    min_positive_blocks: int = Field(ge=1)
    min_sessions: int = Field(ge=1)
    carry: int = Field(ge=1)


class SelectionGate(BaseModel, frozen=True, extra="forbid"):
    min_fraction_of_discovery: float = Field(gt=0, le=1)
    min_sessions: int = Field(ge=1)


class ConfirmationGate(BaseModel, frozen=True, extra="forbid"):
    alpha: float = Field(gt=0, lt=1)
    trim_fraction: float = Field(ge=0, lt=0.5)
    recent_months: int = Field(ge=1)
    min_sessions: int = Field(ge=1)


class PowerSpec(BaseModel, frozen=True, extra="forbid"):
    replicates: int = Field(ge=1)
    null_formulas: int = Field(ge=1)
    ar_phi: float = Field(gt=0, lt=1)
    deltas: tuple[float, ...] = Field(min_length=1)
    detection_delta: float
    min_detection: float = Field(gt=0, le=1)
    max_false_acceptance: float = Field(ge=0, lt=1)
    seed: int


class PowerSearchSpec(BaseModel, frozen=True, extra="forbid"):
    families: tuple[str, ...] = Field(min_length=1)
    seeds: int = Field(ge=1)
    delta: float = Field(gt=0)
    min_recovered: int = Field(ge=1)


class CampaignProtocol(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-campaign"]
    version: int = Field(ge=1)
    title: str
    cohort: str
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    bars_from: date
    bars_through: date
    windows: StageWindows
    k: int = Field(ge=1)
    bracket: BracketSpec
    universe: UniverseSpec
    decision_cost_bps: float = Field(ge=0)
    coverage: CoverageSpec
    formula_budget: int = Field(ge=1)
    families: tuple[Family, ...] = Field(min_length=1)
    excluded_families: tuple[ExcludedFamily, ...]
    search: SearchSpec
    complexity_penalty_per_node: float = Field(ge=0)
    dedupe_jaccard: float = Field(gt=0, le=1)
    bootstrap: CampaignBootstrap
    discovery_gate: DiscoveryGate
    selection_gate: SelectionGate
    confirmation_gate: ConfirmationGate
    power: PowerSpec
    power_search: PowerSearchSpec

    @model_validator(mode="after")
    def _consistent(self) -> CampaignProtocol:
        forbidden = set(self.search.forbidden_operators)
        ids = [family.id for family in self.families]
        if len(ids) != len(set(ids)):
            raise ValueError("family ids must be unique")
        for family in self.families:
            if forbidden & set(family.mutation_operators):
                raise ValueError(f"family {family.id} mutates with a forbidden operator")
            for seed in family.seeds:
                if forbidden & _calls(seed):
                    raise ValueError(f"family {family.id} seed {seed!r} uses a forbidden operator")
        if self.formula_budget < len(self.families):
            raise ValueError("formula_budget must cover at least one formula per family")
        if self.discovery_gate.min_positive_blocks > self.discovery_gate.blocks:
            raise ValueError("min_positive_blocks cannot exceed blocks")
        if self.power.detection_delta not in self.power.deltas or 0.0 not in self.power.deltas:
            raise ValueError("power deltas must include 0 and the detection delta")
        if set(self.power_search.families) - set(ids):
            raise ValueError("power_search families must be declared families")
        if self.power_search.min_recovered > self.power_search.seeds:
            raise ValueError("min_recovered cannot exceed seeds")
        if not self.bars_from < self.windows.discovery[0] < self.windows.confirmation[1] < self.bars_through:
            raise ValueError("bars must span every window")
        return self

    def cube_spec(self) -> CubeSpec:
        return CubeSpec(
            feed=self.feed,
            bars_from=self.bars_from,
            decisions=(self.windows.discovery[0], self.windows.confirmation[1]),
            bars_through=self.bars_through,
            bracket=self.bracket,
            universe=self.universe,
            decision_cost_bps=self.decision_cost_bps,
        )


@dataclass(frozen=True)
class LoadedProtocol:
    protocol: CampaignProtocol
    sha256: str
    path: Path


def load_campaign_protocol(path: Path) -> LoadedProtocol:
    raw = path.read_bytes()
    return LoadedProtocol(
        protocol=CampaignProtocol.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path
    )
```

- [ ] **Step 4: Write `config/research/pooled/campaign-v1.json`**

First compute the cohort SHA-256: `shasum -a 256 config/research/pooled/cohort-v1.json`. Put that value in `cohort_sha256`. Then write exactly this document (replacing `<COHORT_SHA256>`):

```json
{
  "id": "pooled-campaign",
  "version": 1,
  "title": "Pooled campaign v1: budgeted genetic search over 12 OHLCV families, one-shot confirmation",
  "cohort": "config/research/pooled/cohort-v1.json",
  "cohort_sha256": "<COHORT_SHA256>",
  "feed": "alpaca:sip",
  "bars_from": "2016-01-01",
  "bars_through": "2026-09-01",
  "windows": {
    "discovery": ["2016-08-01", "2021-12-31"],
    "selection": ["2022-01-03", "2023-12-29"],
    "confirmation": ["2024-01-02", "2026-07-31"]
  },
  "k": 3,
  "bracket": {"decision_time_et": "10:35", "stop_atr_multiple": 2.0, "atr_window": 14, "target_r": 3.0, "max_hold_sessions": 20},
  "universe": {"min_price": 10.0, "static_percentile": 0.25, "min_eligible_names": 30},
  "decision_cost_bps": 5.0,
  "coverage": {"max_no_reference_fraction": 0.02, "max_unlabelled_fraction_per_year": 0.05},
  "formula_budget": 200,
  "families": [
    {"id": "high52", "rationale": "Proximity to the 52-week high (George & Hwang, 2004).", "seeds": ["close / ts_max(high, 252)", "close / ts_max(high, 126)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "ema"]},
    {"id": "reversal", "rationale": "Short-term reversal at one week and one month (Jegadeesh, 1990; Lehmann, 1990).", "seeds": ["-1.0 * roc(close, 5)", "-1.0 * roc(close, 21)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "decay_linear"]},
    {"id": "max_lottery", "rationale": "Low maximum daily return beats lottery-like names (Bali, Cakici & Whitelaw, 2011).", "seeds": ["-1.0 * ts_max(returns, 21)", "-1.0 * ts_max(returns, 10)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay"]},
    {"id": "momentum_12_1", "rationale": "Twelve-month momentum skipping the last month (Jegadeesh & Titman, 1993).", "seeds": ["delay(close, 21) / delay(close, 252) - 1.0", "roc(delay(close, 21), 105)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "ema"]},
    {"id": "momentum_12_7", "rationale": "Intermediate-horizon momentum (Novy-Marx, 2012).", "seeds": ["roc(delay(close, 147), 105)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay"]},
    {"id": "range_location", "rationale": "Location within the recent high-low range.", "seeds": ["(close - ts_min(low, 20)) / (ts_max(high, 20) - ts_min(low, 20) + 1e-6)", "(close - ts_min(low, 60)) / (ts_max(high, 60) - ts_min(low, 60) + 1e-6)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "decay_linear"]},
    {"id": "trend_slope", "rationale": "Standardized trend slope.", "seeds": ["zscore(ts_slope(close, 20), 10)", "ts_slope(close, 60) / ts_mean(close, 60)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "ts_slope"]},
    {"id": "abnormal_volume", "rationale": "High-volume return premium (Gervais, Kaniel & Mingelgrin, 2001).", "seeds": ["volume / ts_mean(volume, 50)", "zscore(volume, 20)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "ts_sum"]},
    {"id": "overnight_intraday", "rationale": "Overnight versus intraday return components (Lou, Polk & Skouras, 2019).", "seeds": ["ts_sum(open_gap, 21)", "-1.0 * ts_sum(oc_spread, 5)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "ts_sum"]},
    {"id": "price_volume_corr", "rationale": "Price-volume co-movement.", "seeds": ["ts_corr(close, volume, 10)", "-1.0 * ts_corr(returns, volume, 21)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay"]},
    {"id": "signed_volume", "rationale": "Signed volume pressure.", "seeds": ["sign(returns) * zscore(volume, 20)", "ts_sum(sign(returns) * volume, 10) / ts_sum(volume, 10)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay", "ts_sum"]},
    {"id": "hl_spread", "rationale": "High-low spread as an illiquidity proxy (Corwin & Schultz, 2012).", "seeds": ["ts_mean(hl_spread, 21)", "-1.0 * zscore(hl_spread, 20)"], "mutation_operators": ["ts_mean", "ts_rank", "zscore", "delay"]}
  ],
  "excluded_families": [
    {"id": "vol_20", "reason": "Survived Holm in the setup-outcome study, which read 2021-2026."},
    {"id": "sector_relative_momentum", "reason": "Survived Holm in the setup-outcome study, which read 2021-2026."},
    {"id": "residual_momentum", "reason": "Factor-controls study read 2021-2026 and reported partly positive results."},
    {"id": "volatility_scaled_momentum", "reason": "Sector-panel study read 2021-2026 and reported a positive low-cost result."}
  ],
  "search": {"seed": 20260930, "archive_size": 16, "forbidden_operators": ["realized_vol", "ts_std", "ts_mad"]},
  "complexity_penalty_per_node": 0.02,
  "dedupe_jaccard": 0.5,
  "bootstrap": {"block_mean": 20, "discovery_draws": 1000, "selection_draws": 1000, "confirmation_draws": 10000, "seed": 20260931},
  "discovery_gate": {"min_t": 3.0, "blocks": 4, "min_positive_blocks": 3, "min_sessions": 400, "carry": 5},
  "selection_gate": {"min_fraction_of_discovery": 0.5, "min_sessions": 150},
  "confirmation_gate": {"alpha": 0.05, "trim_fraction": 0.01, "recent_months": 12, "min_sessions": 300},
  "power": {"replicates": 100, "null_formulas": 200, "ar_phi": 0.95, "deltas": [0.0, 0.05, 0.08, 0.1, 0.12, 0.15], "detection_delta": 0.15, "min_detection": 0.8, "max_false_acceptance": 0.05, "seed": 20260932},
  "power_search": {"families": ["reversal", "range_location", "abnormal_volume"], "seeds": 10, "delta": 0.15, "min_recovered": 8}
}
```

If a seed fails `require_dimensionless` (the DSL decides units), replace only that seed with the nearest dimensionless form (e.g. wrap in `zscore(…, 20)`) and note it in the report; do not drop a family.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/campaign.py config/research/pooled/campaign-v1.json tests/research/pooled/test_campaign_protocol.py
```

---

### Task 7: Campaign stage logic and the in-memory ledger

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py` (append)
- Test: `tests/research/pooled/test_campaign_stages.py`

**Interfaces:**
- Consumes: `CampaignProtocol` (Task 6); `LabelCube`, `CubeView` (Task 3); `Picks`, `select_picks` (Task 4); `session_table`, `bootstrap_draws`, `paired_edge_test`, `leg_mean_test`, `trimmed_mean` (Task 5); `holm` (`setups/study`).
- Produces: `ScoredFormula(formula_id, nodes, panel: Callable[[CubeView], tuple[np.ndarray, np.ndarray]])`, `Ledger` (Protocol with `consume_confirmation(*, cohort_sha256, interval, campaign_id, candidates)`), `InMemoryLedger` (`.consumed`), `CampaignWindows(cube, windows, cohort_sha256)` (`.discovery()`, `.selection()`, `.confirmation(ledger, *, campaign_id, candidates)`, `.opened`), `run_stages(formulas, windows, ledger, protocol, *, campaign_id) -> dict` with keys `status` (`no_finalists` | `no_confirmation_candidates` | `confirmed` | `none_confirmed`), `discovery`, `carried`, `selection`, `frozen`, `confirmation`, `confirmed`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_campaign_stages.py
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from agentic_trader.research.pooled.campaign import (
    CampaignWindows,
    InMemoryLedger,
    ScoredFormula,
    load_campaign_protocol,
    run_stages,
)
from agentic_trader.research.pooled.cube import LabelCube


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol


def sessions_between(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_cube(n_names=40, seed=0, planted=None, delta=0.0) -> LabelCube:
    days = tuple(sessions_between(PROTOCOL.windows.discovery[0], PROTOCOL.windows.confirmation[1]))
    rng = np.random.default_rng(seed)
    s = len(days)
    r = rng.normal(0.0, 1.2, (s, n_names))
    if planted is not None:
        r[:, planted] += delta
    arrays = {
        "eligible": np.ones((s, n_names), bool),
        "labelled": np.ones((s, n_names), bool),
        "r_gross": r,
        "r_cost": r,
        "holding": np.ones((s, n_names), np.int16),
        "hit": np.ones((s, n_names), np.int8),
        "tiebreak": rng.integers(0, 2**62, (s, n_names)).astype(np.uint64),
        "dollar_volume": np.ones((s, n_names)),
    }
    return LabelCube(
        spec_identity="spec",
        cohort_sha256="c" * 64,
        sessions=days,
        symbols=tuple(f"S{j}" for j in range(n_names)),
        arrays=arrays,
        coverage={},
    )


def constant_formula(name: str, favourite: int) -> ScoredFormula:
    def panel(view):
        scores = np.zeros(view.eligible.shape)
        scores[:, favourite] = 1.0
        return scores, np.ones_like(view.eligible)

    return ScoredFormula(formula_id=name, nodes=3, panel=panel)


def test_a_strong_planted_edge_is_confirmed_and_the_window_consumed_once():
    cube = synthetic_cube(planted=0, delta=1.0)
    windows = CampaignWindows(cube, PROTOCOL.windows, cohort_sha256="c" * 64)
    ledger = InMemoryLedger()
    outcome = run_stages(
        [constant_formula("planted", 0), constant_formula("null", 5)], windows, ledger, PROTOCOL, campaign_id="t1"
    )
    assert outcome["status"] == "confirmed"
    assert outcome["confirmed"] == ["planted"]
    assert windows.opened == ("discovery", "selection", "confirmation")
    assert len(ledger.consumed) == 1 and ledger.consumed[0]["candidates"] == ("planted",)


def test_no_discovery_survivor_leaves_later_windows_unread():
    windows = CampaignWindows(synthetic_cube(), PROTOCOL.windows, cohort_sha256="c" * 64)
    ledger = InMemoryLedger()
    outcome = run_stages([constant_formula("null", 5)], windows, ledger, PROTOCOL, campaign_id="t2")
    assert outcome["status"] == "no_finalists"
    assert windows.opened == ("discovery",)
    assert ledger.consumed == []


def test_confirmation_is_consumed_before_it_is_read_and_never_twice():
    cube = synthetic_cube(planted=0, delta=1.0)
    ledger = InMemoryLedger()
    run_stages(
        [constant_formula("planted", 0)],
        CampaignWindows(cube, PROTOCOL.windows, "c" * 64),
        ledger,
        PROTOCOL,
        campaign_id="a",
    )
    with pytest.raises(ValueError, match="already consumed"):
        run_stages(
            [constant_formula("planted", 0)],
            CampaignWindows(cube, PROTOCOL.windows, "c" * 64),
            ledger,
            PROTOCOL,
            campaign_id="b",
        )


def test_duplicates_are_deduplicated_by_pick_overlap():
    cube = synthetic_cube(planted=0, delta=1.0)
    outcome = run_stages(
        [constant_formula("a", 0), constant_formula("b", 0)],
        CampaignWindows(cube, PROTOCOL.windows, "c" * 64),
        InMemoryLedger(),
        PROTOCOL,
        campaign_id="d",
    )
    assert len(outcome["carried"]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_stages.py -q`
Expected: FAIL with `ImportError: cannot import name 'CampaignWindows'`.

- [ ] **Step 3: Implement the stages (append to `campaign.py`)**

Add imports at the top of `campaign.py` (merged with the existing ones):

```python
from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np

from agentic_trader.research.pooled.cube import CubeView, LabelCube
from agentic_trader.research.pooled.formula import Picks, select_picks
from agentic_trader.research.pooled.stats import (
    bootstrap_draws,
    leg_mean_test,
    paired_edge_test,
    session_table,
    trimmed_mean,
)
from agentic_trader.research.setups.study import holm
```

Extend `__all__` with `"CampaignWindows"`, `"InMemoryLedger"`, `"Ledger"`, `"ScoredFormula"`, `"run_stages"`, then append:

```python
@dataclass(frozen=True)
class ScoredFormula:
    formula_id: str
    nodes: int
    # (scores[S, N], allowed[S, N]) for the given view's sessions.
    panel: Callable[[CubeView], tuple[np.ndarray, np.ndarray]]


class Ledger(Protocol):
    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None: ...


class InMemoryLedger:
    """Power checks and tests: the same single-use rule as the journal ledger, in memory."""

    def __init__(self) -> None:
        self.consumed: list[dict] = []

    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None:
        start, end = interval
        for item in self.consumed:
            if item["cohort_sha256"] == cohort_sha256 and not (
                end < item["interval"][0] or item["interval"][1] < start
            ):
                raise ValueError(f"confirmation interval already consumed by campaign {item['campaign_id']}")
        self.consumed.append(
            {"cohort_sha256": cohort_sha256, "interval": interval, "campaign_id": campaign_id, "candidates": candidates}
        )


class CampaignWindows:
    """Opens the stage windows in order; confirmation only after its consumption is recorded."""

    def __init__(self, cube: LabelCube, windows: StageWindows, cohort_sha256: str):
        self._cube = cube
        self._windows = windows
        self._cohort_sha256 = cohort_sha256
        self._opened: list[str] = []

    @property
    def opened(self) -> tuple[str, ...]:
        return tuple(self._opened)

    def discovery(self) -> CubeView:
        self._opened.append("discovery")
        return self._cube.window(*self._windows.discovery)

    def selection(self) -> CubeView:
        self._opened.append("selection")
        return self._cube.window(*self._windows.selection)

    def confirmation(self, ledger: Ledger, *, campaign_id: str, candidates: tuple[str, ...]) -> CubeView:
        ledger.consume_confirmation(
            cohort_sha256=self._cohort_sha256,
            interval=self._windows.confirmation,
            campaign_id=campaign_id,
            candidates=candidates,
        )
        self._opened.append("confirmation")
        return self._cube.window(*self._windows.confirmation)


def _picks(formula: ScoredFormula, view: CubeView, k: int) -> Picks:
    scores, allowed = formula.panel(view)
    return select_picks(scores, allowed, view, k)


def _restrict(picks: Picks, lo: int, hi: int) -> Picks:
    keep = (picks.session_idx >= lo) & (picks.session_idx < hi)
    return Picks(picks.session_idx[keep] - lo, picks.symbol_idx[keep])


def _draws(view: CubeView, count: int, protocol: CampaignProtocol):
    return bootstrap_draws(len(view.sessions), protocol.bootstrap.block_mean, count, protocol.bootstrap.seed)


def _jaccard(a: set, b: set) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _discovery(
    formulas: Sequence[ScoredFormula], view: CubeView, protocol: CampaignProtocol
) -> tuple[list[dict], list[str]]:
    gate = protocol.discovery_gate
    draws = _draws(view, protocol.bootstrap.discovery_draws, protocol)
    bounds = np.linspace(0, len(view.sessions), gate.blocks + 1).astype(int)
    results, cells = [], {}
    for formula in formulas:
        picks = _picks(formula, view, protocol.k)
        table = session_table(picks, view, purge=True)
        edge = paired_edge_test(table, draws)
        leg = leg_mean_test(table, draws)
        block_means = []
        for lo, hi in zip(bounds[:-1], bounds[1:], strict=True):
            sub = session_table(_restrict(picks, lo, hi), view.sub(lo, hi), purge=True)
            weight = np.isfinite(sub.edge)
            block_means.append(float(sub.edge[weight].mean()) if weight.any() else float("nan"))
        positive = sum(1 for m in block_means if np.isfinite(m) and m > 0)
        t = edge["t"]
        passes = bool(
            np.isfinite(t)
            and t >= gate.min_t
            and positive >= gate.min_positive_blocks
            and np.isfinite(leg["mean"])
            and leg["mean"] > 0
            and edge["n_sessions"] >= gate.min_sessions
        )
        fitness = (t if np.isfinite(t) else float("-inf")) - protocol.complexity_penalty_per_node * formula.nodes
        results.append(
            {
                "formula_id": formula.formula_id,
                "edge": edge,
                "leg": leg,
                "block_means": block_means,
                "passes": passes,
                "fitness": fitness,
            }
        )
        cells[formula.formula_id] = picks.cells()
    passing = sorted((r for r in results if r["passes"]), key=lambda r: (-r["fitness"], r["formula_id"]))
    kept: list[dict] = []
    for candidate in passing:
        if all(
            _jaccard(cells[candidate["formula_id"]], cells[k["formula_id"]]) < protocol.dedupe_jaccard for k in kept
        ):
            kept.append(candidate)
    return results, [r["formula_id"] for r in kept[: gate.carry]]


def _selection(
    formulas: Sequence[ScoredFormula],
    carried: Sequence[str],
    discovery: Sequence[dict],
    view: CubeView,
    protocol: CampaignProtocol,
) -> tuple[list[dict], list[str]]:
    gate = protocol.selection_gate
    draws = _draws(view, protocol.bootstrap.selection_draws, protocol)
    by_id = {f.formula_id: f for f in formulas}
    discovered = {r["formula_id"]: r["edge"]["mean"] for r in discovery}
    results = []
    for formula_id in carried:
        table = session_table(_picks(by_id[formula_id], view, protocol.k), view, purge=True)
        edge = paired_edge_test(table, draws)
        keep = bool(
            np.isfinite(edge["mean"])
            and edge["mean"] > 0
            and edge["mean"] >= gate.min_fraction_of_discovery * discovered[formula_id]
            and edge["n_sessions"] >= gate.min_sessions
        )
        results.append({"formula_id": formula_id, "edge": edge, "kept": keep})
    return results, [r["formula_id"] for r in results if r["kept"]]


def _confirmation(
    formulas: Sequence[ScoredFormula], frozen: Sequence[str], view: CubeView, protocol: CampaignProtocol
) -> tuple[list[dict], list[str]]:
    gate = protocol.confirmation_gate
    draws = _draws(view, protocol.bootstrap.confirmation_draws, protocol)
    by_id = {f.formula_id: f for f in formulas}
    recent_from = _months_before(view.sessions[-1], gate.recent_months)
    recent_lo = next(i for i, d in enumerate(view.sessions) if d > recent_from)
    results = {}
    for formula_id in frozen:
        picks = _picks(by_id[formula_id], view, protocol.k)
        table = session_table(picks, view, purge=False)
        recent = table.edge[recent_lo:]
        recent = recent[np.isfinite(recent)]
        results[formula_id] = {
            "edge": paired_edge_test(table, draws),
            "leg": leg_mean_test(table, draws),
            "trimmed_mean": trimmed_mean(table.pick_rows["r_cost"].to_numpy(float), gate.trim_fraction),
            "recent_edge": float(recent.mean()) if recent.size else float("nan"),
        }
    adjusted = holm({fid: r["edge"]["p_one_sided"] for fid, r in results.items()})
    rows, confirmed = [], []
    for formula_id, r in results.items():
        passes = bool(
            adjusted[formula_id] <= gate.alpha
            and np.isfinite(r["leg"]["ci90"][0])
            and r["leg"]["ci90"][0] > 0
            and np.isfinite(r["trimmed_mean"])
            and r["trimmed_mean"] > 0
            and np.isfinite(r["recent_edge"])
            and r["recent_edge"] > 0
            and r["edge"]["n_sessions"] >= gate.min_sessions
        )
        rows.append({"formula_id": formula_id, **r, "holm_p": adjusted[formula_id], "passes": passes})
        if passes:
            confirmed.append(formula_id)
    return rows, confirmed


def _months_before(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, 28))


def run_stages(
    formulas: Sequence[ScoredFormula],
    windows: CampaignWindows,
    ledger: Ledger,
    protocol: CampaignProtocol,
    *,
    campaign_id: str,
) -> dict:
    discovery, carried = _discovery(formulas, windows.discovery(), protocol)
    outcome: dict = {
        "discovery": discovery,
        "carried": carried,
        "selection": [],
        "frozen": [],
        "confirmation": [],
        "confirmed": [],
    }
    if not carried:
        return {**outcome, "status": "no_finalists"}
    selection, frozen = _selection(formulas, carried, discovery, windows.selection(), protocol)
    outcome.update(selection=selection, frozen=frozen)
    if not frozen:
        return {**outcome, "status": "no_confirmation_candidates"}
    view = windows.confirmation(ledger, campaign_id=campaign_id, candidates=tuple(frozen))
    confirmation, confirmed = _confirmation(formulas, frozen, view, protocol)
    outcome.update(confirmation=confirmation, confirmed=confirmed)
    return {**outcome, "status": "confirmed" if confirmed else "none_confirmed"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS. (The confirmation stage uses 10,000 draws; the draw matrix is cached per window length.)

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/campaign.py tests/research/pooled/test_campaign_stages.py
```

---

### Task 8: Literature entries — model, pass rule and the two frozen files

**Files:**
- Create: `agentic_trader/research/pooled/entry.py`
- Create: `config/research/pooled/high52-v1.json`, `config/research/pooled/reversal-lowmax-v1.json`
- Test: `tests/research/pooled/test_entry.py`

**Interfaces:**
- Consumes: `Formula` (Task 4); `CubeSpec`, `BracketSpec`, `CoverageSpec` (Task 3); `UniverseSpec` (Task 2); `SessionTable`, `paired_edge_test`, `leg_mean_test`, `trimmed_mean` (Task 5); `load_campaign_protocol` (Task 6).
- Produces: `REPO_ROOT`, `PooledEntry` (`.cube_spec()`), `LoadedEntry(entry, sha256, path)`, `load_pooled_entry(path) -> LoadedEntry`, `evaluate_entry(table, entry, draws) -> dict` (keys `p1`–`p4`, `passes`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_entry.py
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.entry import REPO_ROOT, evaluate_entry, load_pooled_entry
from agentic_trader.research.pooled.formula import Picks
from agentic_trader.research.pooled.stats import bootstrap_draws, session_table


ENTRIES = ["config/research/pooled/high52-v1.json", "config/research/pooled/reversal-lowmax-v1.json"]


@pytest.mark.parametrize("relative", ENTRIES)
def test_committed_entries_share_the_campaign_cube_and_pins(relative):
    loaded = load_pooled_entry(REPO_ROOT / relative)
    protocol = load_campaign_protocol(REPO_ROOT / "config/research/pooled/campaign-v1.json")
    assert loaded.entry.cube_spec() == protocol.protocol.cube_spec()
    assert loaded.entry.campaign_protocol_sha256 == protocol.sha256
    assert loaded.entry.cohort_sha256 == load_cohort(REPO_ROOT / loaded.entry.cohort).sha256
    assert loaded.entry.formula.k == 3


def view_with(edge_per_session: float, n_sessions: int, start=date(2021, 1, 4)) -> tuple[CubeView, Picks]:
    days, d = [], start
    while len(days) < n_sessions:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    rng = np.random.default_rng(3)
    r = rng.normal(0.0, 1.0, (n_sessions, 10))
    r[:, 0] += edge_per_session
    view = CubeView(
        sessions=tuple(days),
        symbols=tuple(f"S{j}" for j in range(10)),
        offset=0,
        eligible=np.ones(r.shape, bool),
        labelled=np.ones(r.shape, bool),
        r_gross=r,
        r_cost=r,
        holding=np.ones(r.shape, np.int16),
        hit=np.ones(r.shape, np.int8),
        tiebreak=np.zeros(r.shape, np.uint64),
        dollar_volume=np.ones(r.shape),
    )
    return view, Picks(np.arange(n_sessions), np.zeros(n_sessions, dtype=int))


def test_pass_rule_accepts_a_clear_edge_and_rejects_a_short_sample():
    entry = load_pooled_entry(REPO_ROOT / ENTRIES[0]).entry
    view, picks = view_with(1.0, 1500, start=date(2020, 1, 1))
    table = session_table(picks, view, purge=False)
    result = evaluate_entry(table, entry, bootstrap_draws(1500, 20, 300, 1))
    assert result["passes"]
    short_view, short_picks = view_with(1.0, 200, start=date(2020, 1, 1))
    short = evaluate_entry(session_table(short_picks, short_view, purge=False), entry, bootstrap_draws(200, 20, 300, 1))
    assert not short["p4"]["holds"] and not short["passes"]


def test_pass_rule_rejects_no_edge():
    entry = load_pooled_entry(REPO_ROOT / ENTRIES[0]).entry
    view, picks = view_with(0.0, 1500, start=date(2020, 1, 1))
    result = evaluate_entry(session_table(picks, view, purge=False), entry, bootstrap_draws(1500, 20, 300, 1))
    assert not result["p2"]["holds"] or not result["p1"]["holds"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_entry.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `entry.py`**

```python
# agentic_trader/research/pooled/entry.py
"""A literature entry: one frozen formula tested once on the pooled lane (PEAD's P1-P4 bar).

The file pins the cohort and the campaign protocol by SHA-256 so the campaign is provably
frozen before any literature result exists. It uses the same cube spec as the campaign.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from agentic_trader.research.pooled.cohort import UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CoverageSpec, CubeSpec
from agentic_trader.research.pooled.formula import Formula
from agentic_trader.research.pooled.stats import SessionTable, leg_mean_test, paired_edge_test, trimmed_mean


__all__ = ["REPO_ROOT", "LoadedEntry", "PooledEntry", "evaluate_entry", "load_pooled_entry"]

REPO_ROOT = Path(__file__).resolve().parents[3]


class EntryWindow(BaseModel, frozen=True, extra="forbid"):
    decisions: tuple[date, date]
    bars_from: date
    bars_through: date
    recent_from: date

    @model_validator(mode="after")
    def _ordered(self) -> EntryWindow:
        start, end = self.decisions
        if not self.bars_from < start < self.recent_from <= end < self.bars_through:
            raise ValueError("window dates must satisfy bars_from < start < recent_from <= end < bars_through")
        return self


class EntryBootstrap(BaseModel, frozen=True, extra="forbid"):
    block_mean: float = Field(gt=1)
    draws: int = Field(ge=100)
    seed: int


class EntryPassRule(BaseModel, frozen=True, extra="forbid"):
    min_sessions: int = Field(ge=1)
    min_recent_sessions: int = Field(ge=1)
    trim_fraction: float = Field(ge=0, lt=0.5)


class PooledEntry(BaseModel, frozen=True, extra="forbid"):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    version: int = Field(ge=1)
    title: str
    references: tuple[str, ...]
    hypothesis: str
    decision_rule: str
    cohort: str
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    window: EntryWindow
    formula: Formula
    bracket: BracketSpec
    universe: UniverseSpec
    decision_cost_bps: float = Field(ge=0)
    coverage: CoverageSpec
    bootstrap: EntryBootstrap
    pass_rule: EntryPassRule

    def cube_spec(self) -> CubeSpec:
        return CubeSpec(
            feed=self.feed,
            bars_from=self.window.bars_from,
            decisions=self.window.decisions,
            bars_through=self.window.bars_through,
            bracket=self.bracket,
            universe=self.universe,
            decision_cost_bps=self.decision_cost_bps,
        )


@dataclass(frozen=True)
class LoadedEntry:
    entry: PooledEntry
    sha256: str
    path: Path


def load_pooled_entry(path: Path) -> LoadedEntry:
    raw = path.read_bytes()
    return LoadedEntry(entry=PooledEntry.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)


def evaluate_entry(table: SessionTable, entry: PooledEntry, draws: np.ndarray) -> dict:
    rule = entry.pass_rule
    p1 = leg_mean_test(table, draws)
    p2 = paired_edge_test(table, draws)
    recent = np.array([d >= entry.window.recent_from for d in table.sessions])
    recent_edges = table.edge[recent & np.isfinite(table.edge)]
    trimmed = trimmed_mean(table.pick_rows["r_cost"].to_numpy(float), rule.trim_fraction)
    recent_edge = float(recent_edges.mean()) if recent_edges.size else float("nan")
    p3 = {
        "trimmed_mean": trimmed,
        "recent_edge": recent_edge,
        "holds": bool(np.isfinite(trimmed) and trimmed > 0 and np.isfinite(recent_edge) and recent_edge > 0),
    }
    sessions = int(np.isfinite(table.edge).sum())
    p4 = {
        "sessions": sessions,
        "recent_sessions": int(recent_edges.size),
        "holds": sessions >= rule.min_sessions and recent_edges.size >= rule.min_recent_sessions,
    }
    return {"p1": p1, "p2": p2, "p3": p3, "p4": p4, "passes": all(p["holds"] for p in (p1, p2, p3, p4))}
```

- [ ] **Step 4: Write the two entry files**

Compute `COHORT_SHA256` (`shasum -a 256 config/research/pooled/cohort-v1.json`) and `CAMPAIGN_SHA256` (`shasum -a 256 config/research/pooled/campaign-v1.json`), then write:

`config/research/pooled/high52-v1.json`:

```json
{
  "id": "high52",
  "version": 1,
  "title": "Pooled literature entry: proximity to the 52-week high (long top 3 per session)",
  "references": ["George, T. J. & Hwang, C.-Y. (2004). The 52-Week High and Momentum Investing. Journal of Finance 59(5)."],
  "hypothesis": "Names trading closest to their 52-week high continue to outperform the eligible cohort over the next 20 sessions; investors anchor on the high and under-react to news that pushes prices toward it.",
  "decision_rule": "Passes only if P1 (leg mean R after 5 bps, CI lower bound > 0), P2 (session-paired edge over all eligible names, CI lower bound > 0), P3 (1%-trimmed leg mean > 0 and paired edge since 2023-01-01 > 0) and P4 (>= 500 sessions with a pick, >= 150 since 2023-01-01) all hold. A pass makes the entry eligible for a capped Part 2 paper probe; nothing else.",
  "cohort": "config/research/pooled/cohort-v1.json",
  "cohort_sha256": "<COHORT_SHA256>",
  "campaign_protocol_sha256": "<CAMPAIGN_SHA256>",
  "feed": "alpaca:sip",
  "window": {"decisions": ["2016-08-01", "2026-07-31"], "bars_from": "2016-01-01", "bars_through": "2026-09-01", "recent_from": "2023-01-01"},
  "formula": {"score": "close / ts_max(high, 252)", "filters": [], "k": 3},
  "bracket": {"decision_time_et": "10:35", "stop_atr_multiple": 2.0, "atr_window": 14, "target_r": 3.0, "max_hold_sessions": 20},
  "universe": {"min_price": 10.0, "static_percentile": 0.25, "min_eligible_names": 30},
  "decision_cost_bps": 5.0,
  "coverage": {"max_no_reference_fraction": 0.02, "max_unlabelled_fraction_per_year": 0.05},
  "bootstrap": {"block_mean": 20, "draws": 2000, "seed": 20260929},
  "pass_rule": {"min_sessions": 500, "min_recent_sessions": 150, "trim_fraction": 0.01}
}
```

`config/research/pooled/reversal-lowmax-v1.json`: identical except

```json
  "id": "reversal-lowmax",
  "title": "Pooled literature entry: one-month reversal among low-MAX names (long top 3 per session)",
  "references": [
    "Jegadeesh, N. (1990). Evidence of Predictable Behavior of Security Returns. Journal of Finance 45(3).",
    "Bali, T. G., Cakici, N. & Whitelaw, R. F. (2011). Maxing Out: Stocks as Lotteries and the Cross-Section of Expected Returns. Journal of Financial Economics 99(2)."
  ],
  "hypothesis": "Among names whose largest daily return over the past month is at or below the cohort median (not lottery-like), last month's biggest losers outperform the eligible cohort over the next 20 sessions; lottery-like losers are excluded because they keep underperforming.",
  "formula": {"score": "-1.0 * roc(close, 21)", "filters": [{"expression": "ts_max(returns, 21)", "max_quantile": 0.5}], "k": 3},
  "bootstrap": {"block_mean": 20, "draws": 2000, "seed": 20260929}
```

(and the same `decision_rule` text with no changes).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/entry.py config/research/pooled/high52-v1.json config/research/pooled/reversal-lowmax-v1.json tests/research/pooled/test_entry.py
```

---

### Task 9: Bar acquisition and cached cube build

**Files:**
- Create: `agentic_trader/research/pooled/runner.py`
- Test: `tests/research/pooled/test_runner.py`

**Interfaces:**
- Consumes: `LoadedCohort` (Task 1); `point_in_time_eligibility` (Task 2); `CubeSpec`, `build_cube`, `save_cube`, `load_cube` (Task 3); `_claim_cache_range`, `_fetch_cached`, `BarSource`, `CalendarSource` (`setups/runner`).
- Produces: `CubeBuild(cube, trading_days, adjusted, bar_failures, static_used)`, `build_cube_inputs(cohort, spec, *, bars, calendar, cache_dir, static_symbols, pace) -> CubeBuild`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_runner.py
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import Cohort, CohortSource, LoadedCohort, UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CubeSpec
from agentic_trader.research.pooled.runner import build_cube_inputs


@dataclass
class Day:
    date: date
    is_trading_day: bool


class FakeCalendar:
    async def get_calendar_range(self, start, end):
        out, d = [], start
        while d <= end:
            out.append(Day(d, d.weekday() < 5))
            d += timedelta(days=1)
        return out


class FakeBars:
    def __init__(self):
        self.calls = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment="all", **kwargs):
        self.calls.append((symbol, timeframe, adjustment))
        days, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        if timeframe == "1h":
            index = [datetime.combine(x, time(h), tzinfo=ET_TZ).astimezone(UTC) for x in days for h in range(9, 16)]
        else:
            index = [datetime.combine(x, time(0), tzinfo=ET_TZ).astimezone(UTC) for x in days]
        n = len(index)
        price = np.full(n, 50.0)
        return pd.DataFrame(
            {"Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": price, "Volume": 1e6},
            index=pd.DatetimeIndex(index),
        )


SPEC = CubeSpec(
    feed="alpaca:sip",
    bars_from=date(2021, 1, 1),
    decisions=(date(2021, 3, 1), date(2021, 3, 5)),
    bars_through=date(2021, 5, 1),
    bracket=BracketSpec(
        decision_time_et="10:35", stop_atr_multiple=2.0, atr_window=14, target_r=3.0, max_hold_sessions=5
    ),
    universe=UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=1),
    decision_cost_bps=5.0,
)


def cohort() -> LoadedCohort:
    c = Cohort(
        id="pooled-cohort",
        version=1,
        survivorship="test",
        sources=(CohortSource(kind="config_groups", description="t", identity="x", symbols=("AAA", "BBB")),),
        excluded={},
        symbols=("AAA", "BBB"),
    )
    return LoadedCohort(cohort=c, sha256="c" * 64, path=None)


async def _no_pace():
    return None


def test_build_fetches_once_and_reuses_the_cached_cube(tmp_path):
    bars = FakeBars()
    static = [f"R{i:02d}" for i in range(20)]  # the live gate needs 20 reference names
    kwargs = dict(bars=bars, calendar=FakeCalendar(), cache_dir=tmp_path, static_symbols=static, pace=_no_pace)
    first = asyncio.run(build_cube_inputs(cohort(), SPEC, **kwargs))
    assert first.cube.window(date(2021, 3, 1), date(2021, 3, 5)).eligible.all()
    calls = len(bars.calls)
    second = asyncio.run(build_cube_inputs(cohort(), SPEC, **kwargs))
    assert second.cube.sha256 == first.cube.sha256
    assert len(bars.calls) == calls  # bars and the cube came from the cache
    assert {adj for _, tf, adj in bars.calls if tf == "1d"} == {"all", "raw"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_runner.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `runner.py`**

```python
# agentic_trader/research/pooled/runner.py
"""Provider access for the pooled lane: trading calendar and SIP bars, then the cached cube.

Called only from inside an executor's ``build`` callable, after its manifest exists.
Adjusted and raw daily bars are fetched once per cohort and static symbol over the whole
span; hourly bars only for symbols with at least one eligible session. Everything reuses
the setup study's immutable ``.npz`` bar cache with its range claim (raw bars in the
sibling ``bars_raw`` directory). The cube is cached next to the bars under a name that
binds it to the cohort and spec, and is verified by hash on load.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from agentic_trader.research.pooled.cohort import LoadedCohort, point_in_time_eligibility
from agentic_trader.research.pooled.cube import CubeSpec, LabelCube, build_cube, load_cube, save_cube
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range, _fetch_cached


__all__ = ["CubeBuild", "build_cube_inputs"]

_HOURLY_PAD = timedelta(days=7)
_HOURLY_TAIL = timedelta(days=45)


@dataclass(frozen=True)
class CubeBuild:
    cube: LabelCube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]
    bar_failures: Mapping[str, str]
    static_used: tuple[str, ...]


async def _bars(symbol, timeframe, bars, cache_dir, start, end, pace, *, adjustment="all", **kwargs):
    try:
        frame = await _fetch_cached(symbol, timeframe, bars, cache_dir, start, end, adjustment, pace, **kwargs)
    except Exception as exc:
        return None, f"{timeframe}/{adjustment}: {type(exc).__name__}: {exc}"
    if frame.empty:
        return None, f"{timeframe}/{adjustment}: empty"
    return frame, None


async def build_cube_inputs(
    cohort: LoadedCohort,
    spec: CubeSpec,
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    static_symbols: Sequence[str],
    pace: Callable[[], Awaitable[None]],
) -> CubeBuild:
    days = await calendar.get_calendar_range(spec.bars_from, spec.bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))
    start = datetime.combine(spec.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    bar_dir, raw_dir = cache_dir / "bars", cache_dir / "bars_raw"
    _claim_cache_range(bar_dir, start, end)
    _claim_cache_range(raw_dir, start, end)

    symbols = tuple(cohort.cohort.symbols)
    adjusted: dict[str, pd.DataFrame] = {}
    raw: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for symbol in sorted({*symbols, *static_symbols}):
        frame, failure = await _bars(symbol, "1d", bars, bar_dir, start, end, pace, chunk=end - start)
        if frame is None:
            failures[symbol] = failure or "daily unavailable"
        else:
            adjusted[symbol] = frame
        frame, failure = await _bars(symbol, "1d", bars, raw_dir, start, end, pace, adjustment="raw", chunk=end - start)
        if frame is None:
            failures[symbol] = (
                f"{failures[symbol]}; {failure}" if symbol in failures else (failure or "raw unavailable")
            )
        else:
            raw[symbol] = frame
    static_used = tuple(s for s in static_symbols if s in raw)
    eligibility = await asyncio.to_thread(
        point_in_time_eligibility,
        trading_days,
        spec.decisions,
        symbols,
        adjusted,
        raw,
        {s: raw[s] for s in static_used},
        universe=spec.universe,
        atr_window=spec.bracket.atr_window,
    )

    cube_path = cache_dir / f"cube-{cohort.sha256[:16]}-{spec.identity[:16]}.npz"
    if cube_path.exists():
        cube = await asyncio.to_thread(load_cube, cube_path)
        if cube.spec_identity != spec.identity or cube.cohort_sha256 != cohort.sha256:
            raise ValueError(f"cached cube {cube_path} does not match this cohort/spec")
    else:
        hourly: dict[str, pd.DataFrame] = {}
        for col, symbol in enumerate(symbols):
            rows = eligibility.eligible[:, col].nonzero()[0]
            if rows.size == 0:
                continue
            span_start = datetime.combine(eligibility.sessions[rows[0]], time.min, tzinfo=UTC) - _HOURLY_PAD
            span_end = min(datetime.combine(eligibility.sessions[rows[-1]], time.max, tzinfo=UTC) + _HOURLY_TAIL, end)
            frame, failure = await _bars(symbol, "1h", bars, bar_dir, span_start, span_end, pace)
            if frame is None:
                failures[symbol] = (
                    f"{failures[symbol]}; {failure}" if symbol in failures else (failure or "hourly unavailable")
                )
            else:
                hourly[symbol] = frame
        cube = await asyncio.to_thread(build_cube, eligibility, hourly, spec, cohort_sha256=cohort.sha256)
        await asyncio.to_thread(save_cube, cube, cube_path)
    return CubeBuild(
        cube=cube, trading_days=trading_days, adjusted=adjusted, bar_failures=failures, static_used=static_used
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/runner.py tests/research/pooled/test_runner.py
```

---

### Task 10: Literature-study executor

**Files:**
- Create: `agentic_trader/research/pooled/study.py`
- Test: `tests/research/pooled/test_study.py`

**Interfaces:**
- Consumes: `LoadedEntry`, `evaluate_entry` (Task 8); `CubeBuild` (Task 9); `check_coverage` (Task 3); `evaluate_panel`, `allowed_mask`, `select_picks` (Task 4); `session_table`, `bootstrap_draws`, `two_way_clustered`, `calendar_time_newey_west`, `design_effect` (Task 5); `save_json_report`, `_finite_json`; `LoadedCohort` (Task 1).
- Produces: `check_power_gate(power_result, *, cohort_sha256, campaign_protocol_sha256, cube_sha256=None) -> None`, `execute_pooled_study(loaded, directory, *, cohort, build, power_result, environment) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_study.py
import asyncio
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.pooled.cohort import Cohort, CohortSource, LoadedCohort
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.entry import REPO_ROOT, load_pooled_entry
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.study import check_power_gate, execute_pooled_study


LOADED = load_pooled_entry(REPO_ROOT / "config/research/pooled/reversal-lowmax-v1.json")
COHORT = LoadedCohort(
    cohort=Cohort(
        id="pooled-cohort",
        version=1,
        survivorship="t",
        sources=(
            CohortSource(
                kind="config_groups", description="t", identity="x", symbols=tuple(f"S{j:02d}" for j in range(40))
            ),
        ),
        excluded={},
        symbols=tuple(f"S{j:02d}" for j in range(40)),
    ),
    sha256=LOADED.entry.cohort_sha256,
    path=None,
)


def _build() -> CubeBuild:
    days = []
    d = date(2016, 1, 4)
    while d <= date(2026, 7, 31):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    sessions = tuple(x for x in days if x >= LOADED.entry.window.decisions[0])
    rng = np.random.default_rng(0)
    s, n = len(sessions), 40
    r = rng.normal(0, 1, (s, n))
    arrays = {
        "eligible": np.ones((s, n), bool),
        "labelled": np.ones((s, n), bool),
        "r_gross": r,
        "r_cost": r,
        "holding": np.full((s, n), 5, np.int16),
        "hit": np.ones((s, n), np.int8),
        "tiebreak": rng.integers(0, 2**62, (s, n)).astype(np.uint64),
        "dollar_volume": np.ones((s, n)),
    }
    cube = LabelCube(
        spec_identity=LOADED.entry.cube_spec().identity,
        cohort_sha256=COHORT.sha256,
        sessions=sessions,
        symbols=COHORT.cohort.symbols,
        arrays=arrays,
        coverage={"sessions": s, "skipped_sessions": {}, "eligible_by_year": {}, "unlabelled_by_year": {}},
    )
    from datetime import UTC, datetime, time
    from agentic_trader.market.session import ET_TZ

    index = pd.DatetimeIndex([datetime.combine(x, time(0), tzinfo=ET_TZ).astimezone(UTC) for x in days])
    adjusted = {}
    for j, symbol in enumerate(COHORT.cohort.symbols):
        close = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        adjusted[symbol] = pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=index
        )
    return CubeBuild(cube=cube, trading_days=tuple(days), adjusted=adjusted, bar_failures={}, static_used=())


def _power(cube_sha256: str | None = None) -> dict:
    return {
        "status": "passed",
        "cohort_sha256": LOADED.entry.cohort_sha256,
        "campaign_protocol_sha256": LOADED.entry.campaign_protocol_sha256,
        "cube_sha256": cube_sha256,
    }


def test_power_gate_refuses_a_failed_or_mismatched_result():
    kwargs = dict(
        cohort_sha256=LOADED.entry.cohort_sha256, campaign_protocol_sha256=LOADED.entry.campaign_protocol_sha256
    )
    check_power_gate(_power(), **kwargs)
    for bad in ({**_power(), "status": "gate_failed"}, {**_power(), "cohort_sha256": "0" * 64}):
        with pytest.raises(ValueError, match="power"):
            check_power_gate(bad, **kwargs)
    with pytest.raises(ValueError, match="cube"):
        check_power_gate(_power("a" * 64), **kwargs, cube_sha256="b" * 64)


def test_study_writes_manifest_first_and_a_complete_result(tmp_path):
    build = _build()
    seen = {}

    async def fake_build():
        seen["manifest_existed"] = (tmp_path / "out" / "manifest.json").exists()
        return build

    result = asyncio.run(
        execute_pooled_study(
            LOADED,
            tmp_path / "out",
            cohort=COHORT,
            build=fake_build,
            power_result=_power(build.cube.sha256),
            environment={"test": True},
        )
    )
    assert seen["manifest_existed"]
    assert result["status"] == "completed", result.get("error")
    assert result["decision"] in ("eligible_for_probe", "failed")
    assert set(result["pass_rule"]) == {"p1", "p2", "p3", "p4", "passes"}
    assert "two_way_clustered" in result["cross_checks"]
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["authorizes_promotion"] is False
    assert manifest["campaign_protocol_sha256"] == LOADED.entry.campaign_protocol_sha256
    assert (tmp_path / "out" / "picks.csv.gz").exists()


def test_study_records_failure_instead_of_raising(tmp_path):
    async def broken():
        raise RuntimeError("provider down")

    result = asyncio.run(
        execute_pooled_study(
            LOADED, tmp_path / "out", cohort=COHORT, build=broken, power_result=_power(), environment={}
        )
    )
    assert result["status"] == "failed" and "provider down" in result["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_study.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `study.py`**

```python
# agentic_trader/research/pooled/study.py
"""Executor for a pooled literature entry (research only; grants no credit).

Writes ``protocol.json`` and ``manifest.json`` before calling ``build`` (the only provider
access) and records any failure as ``status: failed`` instead of raising. Refuses to test
unless power check A passed for the same cohort, cube and campaign protocol.
"""

from __future__ import annotations

import asyncio
import gzip
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import HIT_CODES, check_coverage
from agentic_trader.research.pooled.entry import LoadedEntry, evaluate_entry
from agentic_trader.research.pooled.formula import allowed_mask, evaluate_panel, select_picks
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.stats import (
    bootstrap_draws,
    calendar_time_newey_west,
    design_effect,
    session_table,
    two_way_clustered,
)
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["check_power_gate", "execute_pooled_study"]

_HIT_NAMES = {code: str(hit) for hit, code in HIT_CODES.items()}


def check_power_gate(
    power_result: Mapping, *, cohort_sha256: str, campaign_protocol_sha256: str, cube_sha256: str | None = None
) -> None:
    if power_result.get("status") != "passed":
        raise ValueError(f"power check A has not passed (status {power_result.get('status')!r})")
    if power_result.get("cohort_sha256") != cohort_sha256:
        raise ValueError("power check A was run on a different cohort")
    if power_result.get("campaign_protocol_sha256") != campaign_protocol_sha256:
        raise ValueError("power check A was run under a different campaign protocol")
    if cube_sha256 is not None and power_result.get("cube_sha256") != cube_sha256:
        raise ValueError("power check A was run on a different cube")


def _save_frame(frame: pd.DataFrame, path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as target:
        target.write(frame.to_csv(index=False).encode())


def _mean_n(values: np.ndarray) -> dict:
    finite = values[np.isfinite(values)]
    return {"n": int(finite.size), "mean": float(finite.mean()) if finite.size else float("nan")}


def _diagnostics(table, rows: pd.DataFrame, view, cohort: LoadedCohort) -> dict:
    years = np.array([d.year for d in table.sessions])
    by_year = {int(y): _mean_n(table.edge[years == y]) for y in sorted(set(years.tolist()))}
    terciles = {}
    if len(rows) >= 3:
        volume = view.dollar_volume[rows["session_idx"].to_numpy(), rows["symbol_idx"].to_numpy()]
        # pick_rows has a RangeIndex, so the tercile Series aligns with it by position.
        buckets = pd.qcut(pd.Series(volume).rank(method="first"), 3, labels=["low", "mid", "high"])
        terciles = {str(k): _mean_n(g.to_numpy(float)) for k, g in rows["residual"].groupby(buckets, observed=True)}
    sources = pd.Series(
        ["+".join(cohort.cohort.source_of(view.symbols[j])) for j in rows["symbol_idx"]], dtype=object
    ).value_counts()
    per_session = table.pick_n[table.pick_n > 0]
    return {
        "edge_by_year": by_year,
        "residual_by_liquidity_tercile": terciles,
        "hits": {_HIT_NAMES[int(k)]: int(v) for k, v in rows["hit"].value_counts().items()},
        "picks_per_session": {"mean": float(per_session.mean()) if per_session.size else 0.0},
        "picks_by_source": {str(k): int(v) for k, v in sources.items()},
        "mean_r_gross": float(rows["r_gross"].mean()) if len(rows) else float("nan"),
    }


async def execute_pooled_study(
    loaded: LoadedEntry,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    power_result: Mapping,
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
            "cohort_sha256": cohort.sha256,
            "campaign_protocol_sha256": entry.campaign_protocol_sha256,
            "cube_spec_identity": entry.cube_spec().identity,
            "power_cube_sha256": power_result.get("cube_sha256"),
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        if cohort.sha256 != entry.cohort_sha256:
            raise ValueError("cohort file does not match the entry's cohort_sha256")
        check_power_gate(
            power_result, cohort_sha256=entry.cohort_sha256, campaign_protocol_sha256=entry.campaign_protocol_sha256
        )
        built = await build()
        check_coverage(built.cube, entry.coverage)
        check_power_gate(
            power_result,
            cohort_sha256=entry.cohort_sha256,
            campaign_protocol_sha256=entry.campaign_protocol_sha256,
            cube_sha256=built.cube.sha256,
        )
        view = built.cube.window(*entry.window.decisions)
        formula = entry.formula
        scores = await asyncio.to_thread(
            evaluate_panel, formula.score, built.adjusted, built.trading_days, view.sessions, view.symbols
        )
        filters = [
            await asyncio.to_thread(
                evaluate_panel, f.expression, built.adjusted, built.trading_days, view.sessions, view.symbols
            )
            for f in formula.filters
        ]
        allowed = allowed_mask(formula, filters, view.eligible)
        picks = select_picks(scores, allowed, view, formula.k)
        table = session_table(picks, view, purge=False)
        draws = bootstrap_draws(
            len(view.sessions), entry.bootstrap.block_mean, entry.bootstrap.draws, entry.bootstrap.seed
        )
        verdict = evaluate_entry(table, entry, draws)
        rows = table.pick_rows
        picks_frame = rows.assign(
            session=[view.sessions[i].isoformat() for i in rows["session_idx"]],
            symbol=[view.symbols[j] for j in rows["symbol_idx"]],
        )
        await asyncio.to_thread(_save_frame, picks_frame, directory / "picks.csv.gz")
        result = {
            "status": "completed",
            "decision": "eligible_for_probe" if verdict["passes"] else "failed",
            "pass_rule": verdict,
            "cross_checks": {
                "two_way_clustered": two_way_clustered(rows),
                "calendar_time_newey_west": calendar_time_newey_west(rows, len(view.sessions), lag=19),
                "design_effect": design_effect(rows),
            },
            "diagnostics": _diagnostics(table, rows, view, cohort),
            "picks": {
                "kept": int(len(rows)),
                "dropped_unlabelled": table.dropped_unlabelled,
                "dropped_purged": table.dropped_purged,
            },
            "cube": {"sha256": built.cube.sha256, "coverage": built.cube.coverage},
            "bar_failures": dict(built.bar_failures),
            "static_used": list(built.static_used),
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/study.py tests/research/pooled/test_study.py
```

---

### Task 11: Power check A

**Files:**
- Create: `agentic_trader/research/pooled/power.py`
- Test: `tests/research/pooled/test_power.py`

**Interfaces:**
- Consumes: `LoadedProtocol`, `CampaignWindows`, `InMemoryLedger`, `ScoredFormula`, `run_stages` (Tasks 6–7); `LabelCube`, `CubeView`, `check_coverage` (Task 3); `select_picks` (Task 4); `_stationary_index_draws`; `CubeBuild` (Task 9); `LoadedCohort` (Task 1).
- Produces: `synthetic_cube(base, calendar, rng, block_mean) -> LabelCube`, `ar1_scores(shape, phi, seed) -> np.ndarray`, `run_power(protocol, base, calendar, *, cohort_sha256, replicates=None, progress=None) -> dict`, `execute_power_check(loaded, directory, *, cohort, build, environment) -> dict` (result keys `status` ∈ {`passed`, `gate_failed`, `failed`}, `curve`, `cohort_sha256`, `cube_sha256`, `campaign_protocol_sha256`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_power.py
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.power import ar1_scores, run_power, synthetic_cube


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol


def calendar() -> tuple[date, ...]:
    out, d = [], PROTOCOL.windows.discovery[0]
    while d <= PROTOCOL.windows.confirmation[1]:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def base_view(n_sessions=300, n_names=40, seed=0) -> CubeView:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 1.2, (n_sessions, n_names))
    return CubeView(
        sessions=calendar()[:n_sessions],
        symbols=tuple(f"S{j}" for j in range(n_names)),
        offset=0,
        eligible=np.ones(r.shape, bool),
        labelled=np.ones(r.shape, bool),
        r_gross=r,
        r_cost=r,
        holding=np.full(r.shape, 3, np.int16),
        hit=np.ones(r.shape, np.int8),
        tiebreak=rng.integers(0, 2**62, r.shape).astype(np.uint64),
        dollar_volume=np.ones(r.shape),
    )


def test_synthetic_cube_resamples_whole_sessions_onto_the_calendar():
    base = base_view()
    cube = synthetic_cube(base, calendar(), np.random.default_rng(1), block_mean=20)
    view = cube.window(calendar()[0], calendar()[-1])
    assert view.sessions == calendar()
    rows = {tuple(np.round(base.r_cost[i], 9)) for i in range(len(base.sessions))}
    assert all(tuple(np.round(view.r_cost[i], 9)) in rows for i in range(0, len(view.sessions), 97))


def test_ar1_scores_are_reproducible_and_persistent():
    a = ar1_scores((500, 10), 0.95, seed=7)
    assert np.array_equal(a, ar1_scores((500, 10), 0.95, seed=7))
    lag1 = np.corrcoef(a[1:, 0], a[:-1, 0])[0, 1]
    assert 0.85 < lag1 < 0.99


def test_power_detects_a_huge_edge_and_rejects_the_null_on_a_small_run():
    protocol = PROTOCOL.model_copy(
        update={
            "power": PROTOCOL.power.model_copy(
                update={"replicates": 2, "null_formulas": 5, "deltas": (0.0, 1.5), "detection_delta": 1.5}
            )
        }
    )
    result = run_power(protocol, base_view(), calendar(), cohort_sha256="c" * 64)
    assert result["curve"]["1.5"]["detection"] == 1.0
    assert result["curve"]["0.0"]["detection"] == 0.0
    assert result["status"] in ("passed", "gate_failed")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_power.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `power.py`**

```python
# agentic_trader/research/pooled/power.py
"""Power check A: can the campaign's stages detect a planted pooled edge, and reject noise?

Only discovery-window cells are read. Each replicate block-resamples whole discovery
sessions (keeping the real same-day dependence, eligibility and bracket outcomes) onto the
campaign's full session calendar, scores 200 null formulas and one planted formula as
per-name AR(1) fields, adds delta R to the planted formula's picks, and runs the
campaign's ``run_stages`` unchanged with an in-memory ledger.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    InMemoryLedger,
    LoadedProtocol,
    ScoredFormula,
    run_stages,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import _ARRAYS, CubeView, LabelCube, check_coverage
from agentic_trader.research.pooled.formula import select_picks
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.setups.study import _finite_json, _stationary_index_draws
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["ar1_scores", "execute_power_check", "run_power", "synthetic_cube"]

PLANTED = "planted"


def synthetic_cube(base: CubeView, calendar: Sequence[date], rng: np.random.Generator, block_mean: float) -> LabelCube:
    order = _stationary_index_draws(len(base.sessions), block_mean, 1, int(rng.integers(0, 2**31)))[0]
    reps = int(np.ceil(len(calendar) / len(order)))
    index = np.concatenate([order] * reps)[: len(calendar)]
    arrays = {name: getattr(base, name)[index] for name in _ARRAYS}
    return LabelCube(
        spec_identity="synthetic",
        cohort_sha256="synthetic",
        sessions=tuple(calendar),
        symbols=base.symbols,
        arrays=arrays,
        coverage={},
    )


def ar1_scores(shape: tuple[int, int], phi: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(shape)
    out = np.empty(shape)
    out[0] = noise[0]
    scale = np.sqrt(1 - phi**2)
    for t in range(1, shape[0]):
        out[t] = phi * out[t - 1] + scale * noise[t]
    return out


def _formula(formula_id: str, seed: int, phi: float, total_shape: tuple[int, int]) -> ScoredFormula:
    def panel(view: CubeView):
        scores = ar1_scores(total_shape, phi, seed)[view.offset : view.offset + len(view.sessions)]
        return scores, np.ones(view.eligible.shape, dtype=bool)

    return ScoredFormula(formula_id=formula_id, nodes=3, panel=panel)


def run_power(
    protocol: CampaignProtocol,
    base: CubeView,
    calendar: Sequence[date],
    *,
    cohort_sha256: str,
    progress: Callable[[str], None] | None = None,
) -> dict:
    spec = protocol.power
    shape = (len(calendar), len(base.symbols))
    tallies = {delta: {"detected": 0, "false": 0} for delta in spec.deltas}
    for rep in range(spec.replicates):
        rng = np.random.default_rng([spec.seed, rep])
        cube = synthetic_cube(base, calendar, rng, protocol.bootstrap.block_mean)
        seeds = rng.integers(0, 2**31, spec.null_formulas + 1)
        planted = _formula(PLANTED, int(seeds[0]), spec.ar_phi, shape)
        nulls = [_formula(f"null-{i:03d}", int(s), spec.ar_phi, shape) for i, s in enumerate(seeds[1:])]
        rows, cols = [], []
        for start, end in (protocol.windows.discovery, protocol.windows.selection, protocol.windows.confirmation):
            view = cube.window(start, end)
            scores, allowed = planted.panel(view)
            picks = select_picks(scores, allowed, view, protocol.k)
            rows.append(picks.session_idx + view.offset)
            cols.append(picks.symbol_idx)
        cells = (np.concatenate(rows), np.concatenate(cols))
        for delta in spec.deltas:
            shifted = cube.with_shift(*cells, delta) if delta else cube
            windows = CampaignWindows(shifted, protocol.windows, cohort_sha256=cohort_sha256)
            outcome = run_stages(
                [planted, *nulls], windows, InMemoryLedger(), protocol, campaign_id=f"power-{rep}-{delta}"
            )
            confirmed = set(outcome["confirmed"])
            tallies[delta]["detected"] += PLANTED in confirmed
            tallies[delta]["false"] += bool(confirmed - {PLANTED})
        if progress is not None:
            progress(f"replicate {rep + 1}/{spec.replicates}")
    curve = {
        f"{delta:g}" if delta else "0.0": {
            "detection": tallies[delta]["detected"] / spec.replicates,
            "false_acceptance": tallies[delta]["false"] / spec.replicates,
        }
        for delta in spec.deltas
    }
    detection = tallies[spec.detection_delta]["detected"] / spec.replicates
    false_acceptance = tallies[0.0]["false"] / spec.replicates
    passed = detection >= spec.min_detection and false_acceptance <= spec.max_false_acceptance
    return {
        "status": "passed" if passed else "gate_failed",
        "curve": curve,
        "detection_at_gate": detection,
        "false_acceptance_at_zero": false_acceptance,
        "replicates": spec.replicates,
        "null_formulas": spec.null_formulas,
    }


async def execute_power_check(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    protocol = loaded.protocol
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "power_a",
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "reads": "discovery window cells only",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {
        "cohort_sha256": cohort.sha256,
        "campaign_protocol_sha256": loaded.sha256,
        "authorizes_promotion": False,
    }
    try:
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        view = built.cube.window(*protocol.windows.discovery)
        calendar = built.cube.sessions
        outcome = await asyncio.to_thread(
            run_power, protocol, view, calendar, cohort_sha256=cohort.sha256, progress=progress
        )
        result = {**outcome, **base, "cube_sha256": built.cube.sha256}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", **base}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

Note: `run_power` must not accept an unused `replicates` argument; the spec's replicate count comes from the frozen protocol.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/power.py tests/research/pooled/test_power.py
```

---

### Task 12: CLI — `copilot alpha pooled power|study`

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (add a `pooled` sub-group after `alpha_apriori_study_cmd`)
- Test: `tests/cli/test_alpha_pooled_cli.py`

**Interfaces:**
- Consumes: `_AprioriClients` (same module: `bars`, `calendar`, `static_symbols`, `pace`); `research_environment` (same module); `load_cohort` (Task 1); `load_campaign_protocol` (Task 6); `load_pooled_entry`, `REPO_ROOT` (Task 8); `build_cube_inputs` (Task 9); `execute_pooled_study`, `check_power_gate` (Task 10); `execute_power_check` (Task 11).
- Produces: `copilot alpha pooled power PROTOCOL --output DIR [--cache DIR]` and `copilot alpha pooled study ENTRY --power POWER_DIR --output DIR [--cache DIR]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/cli/test_alpha_pooled_cli.py
import json

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha as alpha_cli
from agentic_trader.research.pooled.entry import REPO_ROOT


ENTRY = str(REPO_ROOT / "config/research/pooled/high52-v1.json")
PROTOCOL = str(REPO_ROOT / "config/research/pooled/campaign-v1.json")


def test_study_refuses_an_existing_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = CliRunner().invoke(
        alpha_cli.alpha_group, ["pooled", "study", ENTRY, "--power", str(tmp_path), "--output", str(out)]
    )
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def test_study_refuses_without_a_passed_power_result_before_any_provider_access(tmp_path, monkeypatch):
    power = tmp_path / "power"
    power.mkdir()
    (power / "result.json").write_text(json.dumps({"status": "gate_failed"}))

    def boom(*args, **kwargs):
        raise AssertionError("provider clients must not be created")

    monkeypatch.setattr(alpha_cli, "_apriori_clients", boom)
    result = CliRunner().invoke(
        alpha_cli.alpha_group, ["pooled", "study", ENTRY, "--power", str(power), "--output", str(tmp_path / "out")]
    )
    assert result.exit_code != 0 and "power" in result.output
    assert not (tmp_path / "out").exists()


def test_power_refuses_an_existing_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = CliRunner().invoke(alpha_cli.alpha_group, ["pooled", "power", PROTOCOL, "--output", str(out)])
    assert result.exit_code != 0 and "refusing to overwrite" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/cli/test_alpha_pooled_cli.py -q`
Expected: FAIL (`No such command 'pooled'`).

- [ ] **Step 3: Implement the commands**

Add imports near the other research imports in `agentic_trader/cli/commands/alpha.py`:

```python
from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.entry import REPO_ROOT, load_pooled_entry
from agentic_trader.research.pooled.power import execute_power_check
from agentic_trader.research.pooled.runner import build_cube_inputs
from agentic_trader.research.pooled.study import check_power_gate, execute_pooled_study
```

Append after `alpha_apriori_study_cmd`:

```python
@alpha_group.group("pooled")
def alpha_pooled_group():
    """Pooled alpha mining: one formula across the frozen cohort (research only)."""


def _pooled_build(clients, cohort, spec, cache_dir):
    async def build():
        return await build_cube_inputs(
            cohort,
            spec,
            bars=clients.bars,
            calendar=clients.calendar,
            cache_dir=cache_dir,
            static_symbols=clients.static_symbols,
            pace=clients.pace,
        )

    return build


@alpha_pooled_group.command("power")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option("--cache", type=click.Path(path_type=Path), default=None, help="Bar/cube cache; defaults to OUTPUT/raw")
@coro
async def alpha_pooled_power_cmd(protocol_path, output, cache):
    """Power check A: detect a planted pooled edge and reject noise (discovery cells only)."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded = await asyncio.to_thread(load_campaign_protocol, protocol_path)
    cohort = await asyncio.to_thread(load_cohort, REPO_ROOT / loaded.protocol.cohort)
    if cohort.sha256 != loaded.protocol.cohort_sha256:
        raise click.ClickException("Cohort file does not match the protocol's cohort_sha256")
    cache_dir = cache if cache is not None else output / "raw"
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_power_check(
            loaded,
            output,
            cohort=cohort,
            build=_pooled_build(clients, cohort, loaded.protocol.cube_spec(), cache_dir),
            environment=environment,
            progress=lambda message: click.echo(message, err=True),
        )
    click.echo(json.dumps({k: result.get(k) for k in ("status", "curve", "error")}, indent=2, default=str))
    if result.get("status") != "passed":
        raise click.ClickException(f"Power check A {result.get('status')}; see result.json")


@alpha_pooled_group.command("study")
@click.argument("entry_path", type=click.Path(exists=True, path_type=Path))
@click.option("--power", "power_dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option("--cache", type=click.Path(path_type=Path), default=None, help="Bar/cube cache; defaults to OUTPUT/raw")
@coro
async def alpha_pooled_study_cmd(entry_path, power_dir, output, cache):
    """Run a frozen pooled literature entry (research only; grants no credit)."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded = await asyncio.to_thread(load_pooled_entry, entry_path)
    cohort = await asyncio.to_thread(load_cohort, REPO_ROOT / loaded.entry.cohort)
    result_path = power_dir / "result.json"
    if not result_path.exists():
        raise click.ClickException(f"No power result at {result_path}")
    power_result = json.loads(await asyncio.to_thread(result_path.read_text))
    try:
        check_power_gate(
            power_result,
            cohort_sha256=loaded.entry.cohort_sha256,
            campaign_protocol_sha256=loaded.entry.campaign_protocol_sha256,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if cohort.sha256 != loaded.entry.cohort_sha256:
        raise click.ClickException("Cohort file does not match the entry's cohort_sha256")
    cache_dir = cache if cache is not None else output / "raw"
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_pooled_study(
            loaded,
            output,
            cohort=cohort,
            build=_pooled_build(clients, cohort, loaded.entry.cube_spec(), cache_dir),
            power_result=power_result,
            environment=environment,
        )
    click.echo(json.dumps({k: result.get(k) for k in ("status", "decision", "error")}, indent=2, default=str))
    if result.get("status") == "failed":
        raise click.ClickException("Pooled study failed; see result.json for the reason")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/cli/test_alpha_pooled_cli.py tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/cli/commands/alpha.py tests/cli/test_alpha_pooled_cli.py
```

---

### Task 13: Documentation

**Files:**
- Create: `docs/alpha-pooled-mining.md`
- Create: `docs/alpha-pooled-mining-research-2026-09-28.md` (copy of `~/agentic-trader-research/reports/Pooled alpha mining for sparse trades.md`, links made repository-relative)
- Modify: `docs/apriori-alphas.md`, `docs/alpha-roadmap.md`, `CLAUDE.md`

**Interfaces:** none (docs only; ships in this PR).

- [ ] **Step 1: Copy the research report and fix its links**

```bash
cp "/Users/adamhadani/agentic-trader-research/reports/Pooled alpha mining for sparse trades.md" docs/alpha-pooled-mining-research-2026-09-28.md
sed -i '' 's#(/Users/adamhadani/Development/agentic-trader/docs/#(#g' docs/alpha-pooled-mining-research-2026-09-28.md
grep -n "/Users/" docs/alpha-pooled-mining-research-2026-09-28.md || echo "no absolute paths left"
```

Expected: `no absolute paths left`. Add one line under the title: `*Literature pass, 2026-09-28. Power and hurdle tables are the author's arithmetic from the cited formulas; see the lane contract in [alpha-pooled-mining.md](alpha-pooled-mining.md).*`

- [ ] **Step 2: Write `docs/alpha-pooled-mining.md`**

It must cover, in this order, with the exact values from the Global Constraints: purpose (why per-symbol mining cannot detect 0.05–0.15R edges; link the research report and the spec); the cohort (`cohort-v1.json`, sources, survivorship caveat); point-in-time eligibility (raw bars at D−1, $10, 25th percentile, ≥ 30 names); the label cube (bracket, 10:35 decision, `label_bracket`, window guard, cache file name `cube-<cohort16>-<spec16>.npz`); formulas (dimensionless score, quantile filters, k = 3, D−1 bars, hold-skipping, hash tie-break); statistics (paired edge, leg mean, session-block bootstrap, cross-checks including the calendar-time approximation); literature entries and P1–P4; the campaign protocol (windows, families, exclusions, forbidden operators, gates) and that stage logic exists but the campaign runner, journal ledger and search check B arrive in Part 1b; power check A (procedure and acceptance); the CLI (`copilot alpha pooled power|study`, the power gate); what it never does (no DB, registry, broker, Telegram, trial or promotion credit).

- [ ] **Step 3: Update the index, roadmap and handoff**

- `docs/apriori-alphas.md`: add a "Pooled literature entries" section listing `high52-v1` and `reversal-lowmax-v1` (status "frozen, not yet run"), linking `alpha-pooled-mining.md`.
- `docs/alpha-roadmap.md`, under "Alpha expansion workstreams (September 23)", add item **6. Pooled mining lane (September 28)**: the diagnosis (512 trials on 2026-09-26, 0 finalists, family count 8,879), the operator decisions (own confirmation ledger with 2024-01-02 → 2026-07-31, both tracks, cohort = scan equities + 300 snapshot), Part 1a delivered in this PR, Part 1b next; and **the weekly ETF32 miner pause (2026-09-28)**: `launchctl disable` + `bootout` of `com.agentictrader.alphaminer` (plist kept), with the resume command `launchctl enable gui/$(id -u)/com.agentictrader.alphaminer && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentictrader.alphaminer.plist`, and why (it charged 512 trials a week to the family that deflates every future qualification, with ~zero discovery chance). Also record the operator's September 24 decision that `vol_20`/`sector_rel_mom_60` are prospective-only if not already present.
- `CLAUDE.md`: after the a priori catalog paragraph, add: "The [pooled mining lane](docs/alpha-pooled-mining.md) evaluates one dimensionless DSL formula across a frozen cohort (`config/research/pooled/cohort-v1.json`) on a hash-identified long-bracket label cube, testing a session-paired edge with a session-block bootstrap. `alpha pooled power` (power check A) must pass before `alpha pooled study`; campaigns confirm once on their own ledger's window. Research only: no DB, registry, broker, Telegram or promotion credit."

- [ ] **Step 4: Verify docs build hygiene**

Run: `env -u VIRTUAL_ENV uv run pre-commit run --files docs/alpha-pooled-mining.md docs/alpha-pooled-mining-research-2026-09-28.md docs/apriori-alphas.md docs/alpha-roadmap.md CLAUDE.md`
Expected: all hooks pass.

- [ ] **Step 5: Stage**

```bash
git add docs/alpha-pooled-mining.md docs/alpha-pooled-mining-research-2026-09-28.md docs/apriori-alphas.md docs/alpha-roadmap.md CLAUDE.md
```

---

### Task 14 (controller): Full verification, the real runs and their results

Performed by the controller after the final whole-branch review, not by an implementer.

- [ ] **Step 1: Full suite and hooks**

Run: `env -u VIRTUAL_ENV uv run pytest -q` and `env -u VIRTUAL_ENV uv run pre-commit run --all-files`
Expected: all pass.

- [ ] **Step 2: Power check A on the real discovery cells** (credentials from the installed checkout's `.envrc`, read-only; never print it)

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled
set -a; source /Users/adamhadani/Development/agentic-trader/.envrc; set +a
env -u VIRTUAL_ENV uv run copilot alpha pooled power config/research/pooled/campaign-v1.json \
  --output ~/agentic-trader-research/pooled-power-a-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 \
  > ~/agentic-trader-research/pooled-power-a.log 2>&1
```

Expected: `status: passed`. If `gate_failed`: stop, write `docs/alpha-pooled-power-<date>.md` with the curve, and report to the operator for redesign; do not run the studies.

- [ ] **Step 3: The two literature studies**

```bash
for entry in high52-v1 reversal-lowmax-v1; do
  env -u VIRTUAL_ENV uv run copilot alpha pooled study config/research/pooled/$entry.json \
    --power ~/agentic-trader-research/pooled-power-a-<date> \
    --output ~/agentic-trader-research/pooled-$entry-<date> --cache ~/agentic-trader-research/pooled-cache-v1
done
```

- [ ] **Step 4: Result documents**

Write `docs/alpha-pooled-power-<date>.md` (protocol, curve table, acceptance, runtime, caveats) and `docs/alpha-pooled-high52-<date>.md`, `docs/alpha-pooled-reversal-lowmax-<date>.md` (scale, P1–P4 table, cross-checks, diagnostics by year and liquidity tercile, picks by source, caveats, decision). Update `docs/apriori-alphas.md` statuses and the roadmap item. Commit on the branch, open the PR.
