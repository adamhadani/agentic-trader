# Intraday pooled mining lane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the pooled lane score formulas on hourly bars read through the first completed bar of the decision session, on the cached cohort-v2 label cube, via a frozen fixed-set protocol `intraday-v1`; retire the per-symbol weekly miner.

**Architecture:** `IntradayCampaignProtocol` subclasses `CampaignProtocol` (fields `id`, `score_inputs`, `score_read`); the loader dispatches on `id`. `formula.prepare_hourly`/`evaluate_prepared_hourly` evaluate the unchanged DSL over regular-session hourly bars and read, per session, the last bar whose close is at or before the decision instant. `ScoreBook.hourly(...)` builds a book with the same interface; `ScoreBook.freeze(expressions)` returns a `FrozenPanels` book for the null-check's process pool. `CubeBuild.hourly` is populated when `load_hourly=True` (build or cache hit); `score_book(protocol, built)` chooses the book. The frozen protocol copies v4's cube values so the cached cube is a hit. `scripts/launchd.sh` retires the miner.

**Tech Stack:** Python 3.14, `uv`, pandas, numpy, pydantic v2 (frozen), click, pytest (network blocked).

**Spec:** `docs/superpowers/specs/2026-10-14-pooled-intraday-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-intraday`; never touch `/Users/adamhadani/Development/agentic-trader`.
- ruff strict (C408, PLC0415, RUF059, A002, UP038, RUF046; sorted `__all__`); mypy clean on touched modules; pristine tests. `env -u VIRTUAL_ENV uv run pre-commit run --all-files` before each commit (ruff-format reflows fenced Markdown code; re-add).
- **v1–v4 identities unchanged:** `CampaignProtocol`'s fields and `model_dump` are untouched (a test pins the SHA-256 of `config/research/pooled/campaign-v4.json` loaded through `load_campaign_protocol` and `protocol.model_dump(mode="json")` equal to their values on `main`); the cached cube spec identity for the intraday protocol must equal `f1c835a3d3364d2b`.
- Research only: no registry, broker, Telegram; no change to labels, the cube, bracket, cohort, windows, gates or stage logic; `features.py` untouched; no runtime change.
- No lookahead: a score for session D reads nothing stamped at or after the decision instant on D; the 10:30 bar (close 11:30) is never read for a 10:35 decision.
- Implementers STAGE and report; never commit; never dispatch subagents.

## Review Focus

1. A session whose first regular bar is stamped after the decision instant (late open) → NaN score, never the previous session's last bar re-used as "today" (Task 2 `test_hourly_read_is_nan_when_no_bar_closed_before_the_decision`).
2. Extended-hours bars in the cache (04:00–20:00) → filtered out before any windowed operator sees them (Task 2 `test_prepare_hourly_keeps_regular_session_bars_only`).
3. `FrozenPanels` asked for an expression it does not hold → `ValueError`, never a silent daily evaluation (Task 2 `test_frozen_panels_refuse_unknown_expressions`).
4. Loading a v4 file through the dispatching loader → the base class, identical SHA-256 and `model_dump` (Task 1 `test_v4_identity_is_unchanged_by_the_dispatching_loader`).
5. `launchd.sh install-miner` → prints the retirement notice, exits 0, writes no plist (Task 4 `test_launchd_install_miner_is_retired`).

---

### Task 1: `IntradayCampaignProtocol`, `score_inputs`, loader dispatch

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py` (`CampaignProtocol` ~line 224; `load_campaign_protocol` ~line 385; `__all__`)
- Test: `tests/research/pooled/test_campaign_protocol.py` (append)

**Interfaces:**
- Produces: `ScoreRead(bars_before_decision: int)`, `IntradayCampaignProtocol(CampaignProtocol)` with `id: Literal["pooled-intraday-campaign"]`, `score_inputs: Literal["hourly"]`, `score_read: ScoreRead`; `CampaignProtocol.score_inputs` property → `"daily"`; `load_campaign_protocol(path)` returns the subclass when the document's `id` is `pooled-intraday-campaign`.

- [ ] **Step 1: Write the failing tests** (append; reuse the module's existing v4-document helper if one exists, else build the document with `json.loads(Path("config/research/pooled/campaign-v4.json").read_text())`):

```python
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    IntradayCampaignProtocol,
    ScoreRead,
    load_campaign_protocol,
)

V4_PATH = Path("config/research/pooled/campaign-v4.json")
V4_SHA256 = "a72f2e65"  # the recorded prefix of the v4 file's SHA-256 (docs/alpha-pooled-checks-v4-2026-10-07.md)


def _intraday_document() -> dict:
    doc = json.loads(V4_PATH.read_text())
    doc["id"] = "pooled-intraday-campaign"
    doc["version"] = 1
    doc["score_inputs"] = "hourly"
    doc["score_read"] = {"bars_before_decision": 1}
    return doc


def test_v4_identity_is_unchanged_by_the_dispatching_loader():
    loaded = load_campaign_protocol(V4_PATH)
    assert type(loaded.protocol) is CampaignProtocol
    assert loaded.sha256.startswith(V4_SHA256)
    assert hashlib.sha256(V4_PATH.read_bytes()).hexdigest() == loaded.sha256
    assert loaded.protocol.score_inputs == "daily"
    assert "score_inputs" not in loaded.protocol.model_dump(mode="json")


def test_intraday_protocol_is_a_subclass_with_its_three_fields(tmp_path):
    path = tmp_path / "intraday.json"
    path.write_text(json.dumps(_intraday_document()))
    loaded = load_campaign_protocol(path)
    protocol = loaded.protocol
    assert isinstance(protocol, IntradayCampaignProtocol) and isinstance(protocol, CampaignProtocol)
    assert protocol.score_inputs == "hourly" and protocol.score_read == ScoreRead(bars_before_decision=1)
    assert protocol.cube_spec().identity == CampaignProtocol(**json.loads(V4_PATH.read_text())).cube_spec().identity
    assert protocol.gates == (("power", "power_a"), ("null_check", "null_check"))
    dumped = protocol.model_dump(mode="json")
    assert dumped["id"] == "pooled-intraday-campaign" and dumped["score_read"] == {"bars_before_decision": 1}


def test_intraday_protocol_refuses_genetic_mode_and_bad_reads():
    doc = _intraday_document()
    with pytest.raises(ValidationError, match="fixed_set"):
        IntradayCampaignProtocol(
            **{
                **doc,
                "search_mode": "genetic",
                "power_search": {"families": ["reversal_5"], "seeds": 10, "delta": 0.1, "min_recovered": 5, "seed": 1},
            }
        )
    with pytest.raises(ValidationError):
        IntradayCampaignProtocol(**{**doc, "score_read": {"bars_before_decision": 0}})
    with pytest.raises(ValidationError):
        IntradayCampaignProtocol(**{**doc, "score_inputs": "daily"})
    with pytest.raises(ValidationError):
        CampaignProtocol(**doc)  # the base refuses the intraday id and the extra fields


def test_loader_refuses_an_unknown_id(tmp_path):
    doc = _intraday_document()
    doc["id"] = "pooled-something"
    path = tmp_path / "x.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="id"):
        load_campaign_protocol(path)
```

- [ ] **Step 2: Run to verify failure**: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_protocol.py -q -k "intraday or dispatching_loader or unknown_id"` → ImportError.

- [ ] **Step 3: Implement** in `campaign.py`:

```python
class ScoreRead(BaseModel, frozen=True, extra="forbid"):
    """Which completed hourly bar a session's score is read at: 1 = the last bar whose close is at or
    before the decision instant (the 09:30 bar for a 10:35 decision)."""

    bars_before_decision: int = Field(ge=1)
```

On `CampaignProtocol` add, beside `selection_rule`:

```python
    @property
    def score_inputs(self) -> str:
        """What a formula reads: adjusted daily bars at D-1 for every daily protocol (v1-v4)."""
        return "daily"
```

After `CampaignProtocol`:

```python
class IntradayCampaignProtocol(CampaignProtocol):
    """A pooled protocol whose formulas read regular-session hourly bars through the first completed
    bar of the decision session (spec 2026-10-14). Everything else -- cohort, cube, gates, stages,
    ledger -- is the daily protocol's, inherited; v1-v4 documents and hashes are untouched."""

    id: Literal["pooled-intraday-campaign"]  # type: ignore[assignment]
    score_inputs: Literal["hourly"]  # type: ignore[assignment]
    score_read: ScoreRead

    @model_validator(mode="after")
    def _intraday(self) -> IntradayCampaignProtocol:
        if self.search_mode != "fixed_set":
            raise ValueError(
                "an intraday protocol is fixed_set in v1: a genetic search on hourly inputs needs check B and its own spec"
            )
        return self
```

(If mypy rejects the property/field shadowing, declare `score_inputs` on the base as a `@property` and on the subclass as a field with `# type: ignore[assignment]`, as shown; if pydantic rejects overriding a `Literal` field, use `Literal["pooled-intraday-campaign"]` with `Field(default=...)` only if required — keep it required.)

`load_campaign_protocol`:

```python
_PROTOCOL_CLASSES: dict[str, type[CampaignProtocol]] = {
    "pooled-campaign": CampaignProtocol,
    "pooled-intraday-campaign": IntradayCampaignProtocol,
}


def load_campaign_protocol(path: Path) -> LoadedProtocol:
    raw = path.read_bytes()
    try:
        identity = json.loads(raw).get("id")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not a JSON protocol") from exc
    cls = _PROTOCOL_CLASSES.get(identity)
    if cls is None:
        raise ValueError(f"{path}: unknown protocol id {identity!r}")
    return LoadedProtocol(protocol=cls.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
```

(add `import json`; add `IntradayCampaignProtocol`, `ScoreRead` to `__all__`, sorted).

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q` → PASS (every v2/v3/v4 test unchanged).
- [ ] **Step 5: Lint/mypy, stage** `agentic_trader/research/pooled/campaign.py tests/research/pooled/test_campaign_protocol.py`. Commit message: `Intraday campaign protocol subclass and id-dispatching loader`.

---

### Task 2: Hourly score inputs — `prepare_hourly`, `evaluate_prepared_hourly`, `ScoreBook.hourly`, `FrozenPanels`

**Files:**
- Modify: `agentic_trader/research/pooled/formula.py`, `agentic_trader/research/pooled/scoring.py`
- Test: `tests/research/pooled/test_formula.py` (append), `tests/research/pooled/test_scoring.py` (append)

**Interfaces:**
- Produces: `prepare_hourly(hourly: Mapping[str, DataFrame]) -> dict[str, DataFrame]`; `evaluate_prepared_hourly(expression, prepared, sessions, symbols, *, decision_time_et: str, bars_before_decision: int) -> np.ndarray`; `ScoreBook.hourly(hourly, sessions, symbols, *, decision_time_et, bars_before_decision, capacity=DEFAULT_CAPACITY)`; `ScoreBook.freeze(expressions) -> FrozenPanels`; `FrozenPanels.panel/formula` (same contract; unknown expression → `ValueError`).
- Consumes: `agentic_trader.research.setups.labels.regular_session_bars`, `AlphaExpressionEvaluator`, `ET_TZ`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/research/pooled/test_formula.py
from agentic_trader.research.pooled.formula import evaluate_prepared_hourly, prepare_hourly


def hourly(
    days: list[date], *, closes_by_bar: Sequence[float] | None = None, hours=(9, 10, 11, 12, 13, 14, 15), extended=False
) -> pd.DataFrame:
    """One bar per regular hour per day, stamped at the bar OPEN in New York time (09:30, 10:30, ...).
    ``extended`` adds 04:00 and 16:00 bars that must be filtered out."""
    stamps, rows = [], []
    k = 0
    for d in days:
        bar_hours = [(h, 30) for h in hours]
        if extended:
            bar_hours = [(4, 0), *bar_hours, (16, 0)]
        for h, m in bar_hours:
            stamps.append(datetime.combine(d, time(h, m), tzinfo=ET_TZ).astimezone(UTC))
            c = 100.0 + k if closes_by_bar is None else closes_by_bar[k]
            rows.append(
                {"Open": c - 0.5, "High": c + 1.0, "Low": c - 1.0, "Close": c, "Volume": 1000.0 + k, "VWAP": c - 0.25}
            )
            k += 1
    return pd.DataFrame(rows, index=pd.DatetimeIndex(stamps))


def test_prepare_hourly_keeps_regular_session_bars_only():
    days = weekdays(date(2024, 1, 2), 3)
    prepared = prepare_hourly({"AAA": hourly(days, extended=True), "BBB": pd.DataFrame(), "CCC": None})
    assert set(prepared) == {"AAA"}
    frame = prepared["AAA"]
    assert len(frame) == 21 and frame.index.tz is not None and frame.index.is_monotonic_increasing
    assert set(frame.columns) >= {"open", "high", "low", "close", "volume", "vwap"}
    assert all(ts.astimezone(ET_TZ).hour in range(9, 16) for ts in frame.index)


def test_hourly_read_takes_the_last_bar_closed_before_the_decision():
    days = weekdays(date(2024, 1, 2), 3)
    prepared = prepare_hourly({"AAA": hourly(days)})
    out = evaluate_prepared_hourly(
        "close", prepared, days, ("AAA", "ZZZ"), decision_time_et="10:35", bars_before_decision=1
    )
    # Day 1 bars close at 100..106 (09:30 bar = 100); at 10:35 the 09:30 bar (close 10:30) is the last closed one.
    assert out.shape == (3, 2)
    assert out[:, 0].tolist() == [100.0, 107.0, 114.0]
    assert np.isnan(out[:, 1]).all()
    two = evaluate_prepared_hourly("close", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=2)
    # The bar before the 09:30 bar is the previous session's 15:30 bar; day 1 has none.
    assert np.isnan(two[0, 0]) and two[1, 0] == 106.0 and two[2, 0] == 113.0
    late = evaluate_prepared_hourly("close", prepared, days, ("AAA",), decision_time_et="11:35", bars_before_decision=1)
    assert late[:, 0].tolist() == [101.0, 108.0, 115.0]


def test_hourly_read_is_nan_when_no_bar_closed_before_the_decision():
    days = weekdays(date(2024, 1, 2), 2)
    frame = hourly(days)
    # Drop day 2's 09:30 bar: at 10:35 on day 2 nothing has closed yet today.
    day2_open = datetime.combine(days[1], time(9, 30), tzinfo=ET_TZ).astimezone(UTC)
    frame = frame.drop(index=pd.Timestamp(day2_open))
    out = evaluate_prepared_hourly(
        "close", prepare_hourly({"AAA": frame}), days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert out[0, 0] == 100.0 and np.isnan(out[1, 0])


def test_hourly_dsl_fields_are_bar_over_bar():
    days = weekdays(date(2024, 1, 2), 2)
    prepared = prepare_hourly({"AAA": hourly(days)})
    gap = evaluate_prepared_hourly(
        "open_gap", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    # Day 2's 09:30 bar opens at 106.5 after day 1's last close 106: open_gap = 106.5/106 - 1.
    assert np.isnan(gap[0, 0]) and gap[1, 0] == pytest.approx(106.5 / 106.0 - 1.0)
    oc = evaluate_prepared_hourly(
        "oc_spread", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert oc[0, 0] == pytest.approx(0.5 / 100.0)
    windowed = evaluate_prepared_hourly(
        "volume / ts_mean(volume, 7)", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert np.isnan(windowed[0, 0]) and np.isfinite(windowed[1, 0])


def test_hourly_read_never_sees_bars_at_or_after_the_decision():
    """Mutating every bar stamped at or after 10:30 (close 11:30) must not change the 10:35 read."""
    days = weekdays(date(2024, 1, 2), 3)
    base = hourly(days)
    poisoned = base.copy()
    late = [ts for ts in poisoned.index if ts.astimezone(ET_TZ).time() >= time(10, 30)]
    poisoned.loc[late, ["Open", "High", "Low", "Close", "Volume", "VWAP"]] = 1e9
    a = evaluate_prepared_hourly(
        "ts_mean(close, 3)",
        prepare_hourly({"AAA": base}),
        days,
        ("AAA",),
        decision_time_et="10:35",
        bars_before_decision=1,
    )
    b = evaluate_prepared_hourly(
        "ts_mean(close, 3)",
        prepare_hourly({"AAA": poisoned}),
        days,
        ("AAA",),
        decision_time_et="10:35",
        bars_before_decision=1,
    )
    # Day 1's read uses only the 09:30 bar window (NaN for a 3-bar mean); later days' windows include
    # earlier sessions' late bars, which ARE in the past, so only the first session is identical by
    # construction: assert the day-1 reads agree and that no 1e9 leaks into day 2/3 through the
    # current session's own late bars by comparing with a frame where only the CURRENT day's late
    # bars are poisoned.
    current_only = base.copy()
    for d in days:
        late_today = [
            ts
            for ts in current_only.index
            if ts.astimezone(ET_TZ).date() == d and ts.astimezone(ET_TZ).time() >= time(10, 30)
        ]
        current_only.loc[late_today, "Close"] = 1e9
    c = evaluate_prepared_hourly(
        "close", prepare_hourly({"AAA": current_only}), days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert c[:, 0].tolist() == [100.0, 107.0, 114.0]
    assert np.isnan(a[0, 0]) and np.isnan(b[0, 0])
```

```python
# append to tests/research/pooled/test_scoring.py
from agentic_trader.research.pooled.formula import evaluate_prepared_hourly, prepare_hourly
from agentic_trader.research.pooled.scoring import FrozenPanels
from tests.research.pooled.test_formula import hourly


HDAYS = weekdays(date(2021, 1, 4), 30)
HSESSIONS = tuple(HDAYS[5:])
HOURLY = {"AAA": hourly(HDAYS), "BBB": hourly(HDAYS, closes_by_bar=list(np.linspace(300, 100, 30 * 7)))}


def test_hourly_book_matches_the_evaluator_and_is_label_blind():
    book = ScoreBook.hourly(HOURLY, HSESSIONS, SYMBOLS, decision_time_et="10:35", bars_before_decision=1)
    formula = book.formula("oc_spread")
    scores, allowed = formula.panel(BlindView(HSESSIONS[3:9], SYMBOLS, offset=3))
    expected = evaluate_prepared_hourly(
        "oc_spread", prepare_hourly(HOURLY), HSESSIONS[3:9], SYMBOLS, decision_time_et="10:35", bars_before_decision=1
    )
    np.testing.assert_array_equal(np.nan_to_num(scores, nan=-1), np.nan_to_num(expected, nan=-1))
    assert allowed.all() and np.isnan(scores[:, 2]).all()  # CCC has no bars
    assert book.panel("oc_spread") is book.panel("oc_spread")


def test_frozen_panels_serve_only_the_frozen_expressions():
    book = ScoreBook.hourly(HOURLY, HSESSIONS, SYMBOLS, decision_time_et="10:35", bars_before_decision=1)
    frozen = book.freeze(("oc_spread", "open_gap"))
    assert isinstance(frozen, FrozenPanels)
    np.testing.assert_array_equal(
        np.nan_to_num(frozen.panel("oc_spread"), nan=-1), np.nan_to_num(book.panel("oc_spread"), nan=-1)
    )
    scores, _ = frozen.formula("open_gap").panel(BlindView(HSESSIONS[2:5], SYMBOLS, offset=2))
    assert scores.shape == (3, 3)
    assert frozen.sessions == book.sessions and frozen.symbols == book.symbols


def test_frozen_panels_refuse_unknown_expressions():
    frozen = ScoreBook.hourly(HOURLY, HSESSIONS, SYMBOLS, decision_time_et="10:35", bars_before_decision=1).freeze(
        ("oc_spread",)
    )
    with pytest.raises(ValueError, match="frozen"):
        frozen.panel("open_gap")
    with pytest.raises(ValueError, match="frozen"):
        frozen.formula("roc(close, 5)")
    # Canonical form, not the literal string, is the key.
    assert frozen.panel("oc_spread ") is frozen.panel("oc_spread")


def test_daily_book_is_unchanged():
    book = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS)
    assert book.formula("roc(close, 5)").formula_id == formula_id("roc(close, 5)")
```

- [ ] **Step 2: Run to verify failure** (`ImportError`).

- [ ] **Step 3: Implement**

`formula.py` (imports: `from datetime import date, datetime, time, timedelta`, `from agentic_trader.market.session import ET_TZ`, `from agentic_trader.research.setups.labels import regular_session_bars`):

```python
_BAR = pd.Timedelta("1h")


def prepare_hourly(hourly: Mapping[str, pd.DataFrame | None]) -> dict[str, pd.DataFrame]:
    """Regular-session hourly bars, sorted, tz-aware UTC, lower-case DSL columns; empty frames dropped."""
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, frame in hourly.items():
        if frame is None or frame.empty:
            continue
        index = pd.DatetimeIndex(frame.index)
        if index.tz is None:
            index = index.tz_localize("UTC")
        regular = regular_session_bars(frame.set_axis(index.tz_convert("UTC"))).sort_index()
        regular = regular[~regular.index.duplicated(keep="last")]
        if regular.empty:
            continue
        prepared[symbol] = regular.rename(columns={c: str(c).lower() for c in regular.columns})
    return prepared


def _decision_instants(sessions: Sequence[date], decision_time_et: str) -> pd.DatetimeIndex:
    hour, minute = (int(part) for part in decision_time_et.split(":"))
    return pd.DatetimeIndex(
        [datetime.combine(day, time(hour, minute), tzinfo=ET_TZ).astimezone(UTC) for day in sessions]
    )


def evaluate_prepared_hourly(
    expression: str,
    prepared: Mapping[str, pd.DataFrame],
    sessions: Sequence[date],
    symbols: Sequence[str],
    *,
    decision_time_et: str,
    bars_before_decision: int,
) -> np.ndarray:
    """[S, N] values known at each decision: the expression over the symbol's regular hourly bars,
    read at the ``bars_before_decision``-th last bar whose close (open stamp + 1h) is at or before
    the session's decision instant, and only if that bar belongs to the session's own New York date
    (an earlier session's bar is never "today's" read). No such bar: NaN."""
    if bars_before_decision < 1:
        raise ValueError("bars_before_decision must be at least 1")
    instants = _decision_instants(sessions, decision_time_et)
    out = np.full((len(sessions), len(symbols)), np.nan)
    evaluator = AlphaExpressionEvaluator()
    for col, symbol in enumerate(symbols):
        keyed = prepared.get(symbol)
        if keyed is None:
            continue
        series = evaluator.evaluate(expression, keyed)
        closes = keyed.index + _BAR
        dates = keyed.index.tz_convert(ET_TZ).date
        positions = np.searchsorted(closes.values, instants.values, side="right")  # bars closed by the instant
        for row, (pos, day) in enumerate(zip(positions, sessions, strict=True)):
            idx = pos - bars_before_decision
            if idx < 0:
                continue
            if bars_before_decision == 1 and dates[idx] != day:
                continue
            out[row, col] = series.iloc[idx]
    out[~np.isfinite(out)] = np.nan
    return out
```

(Add both to `__all__`. For `bars_before_decision > 1` the earlier bar may belong to the previous session by design — `bars_before_decision=2` means "the bar before today's first closed bar"; keep the same-day guard only for the first read, as the docstring says.)

`scoring.py`: refactor `ScoreBook.__init__` to take a `prepared` dict and an evaluator callable; add the constructors and `FrozenPanels`:

```python
class ScoreBook:
    def __init__(self, adjusted, trading_days, sessions, symbols, *, capacity=DEFAULT_CAPACITY):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._trading_days = tuple(trading_days)
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        self._capacity = capacity
        self._panels: OrderedDict[str, np.ndarray] = OrderedDict()
        prepared = prepare_daily(adjusted)
        self._evaluate = lambda expression: evaluate_prepared(expression, prepared, self._trading_days, self._sessions, self._symbols)
        self.score_inputs = "daily"

    @classmethod
    def hourly(cls, hourly, sessions, symbols, *, decision_time_et, bars_before_decision, capacity=DEFAULT_CAPACITY) -> ScoreBook:
        """A book scoring on regular-session hourly bars read before the decision (spec 2026-10-14 §3.2)."""
        book = cls.__new__(cls)
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        book._trading_days = ()
        book._sessions = tuple(sessions)
        book._symbols = tuple(symbols)
        book._capacity = capacity
        book._panels = OrderedDict()
        prepared = prepare_hourly(hourly)
        book._evaluate = lambda expression: evaluate_prepared_hourly(
            expression, prepared, book._sessions, book._symbols,
            decision_time_et=decision_time_et, bars_before_decision=bars_before_decision,
        )
        book.score_inputs = "hourly"
        return book

    def panel(self, expression):   # body as today but `values = self._evaluate(canonical)`
        ...

    def freeze(self, expressions: Iterable[str]) -> FrozenPanels:
        """Precomputed panels for exactly these expressions (canonical), for process pools that must
        not pickle the bars."""
        panels = {canonical_expression(e): self.panel(e) for e in expressions}
        return FrozenPanels(panels, self._sessions, self._symbols, score_inputs=self.score_inputs)


class FrozenPanels:
    """A score book that serves only the panels it was frozen with; anything else raises."""

    def __init__(self, panels, sessions, symbols, *, score_inputs):
        self._panels = dict(panels)
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        self.score_inputs = score_inputs

    sessions / symbols properties as ScoreBook's

    def panel(self, expression):
        canonical = canonical_expression(expression)
        try:
            return self._panels[canonical]
        except KeyError:
            raise ValueError(f"expression not in the frozen panels: {canonical!r}") from None

    def formula(self, expression):   # same as ScoreBook.formula, using self.panel
        ...
```

(Share the `formula` implementation between the two classes through a small mixin or module function `_scored_formula(book, expression)` rather than duplicating it. `score_inputs` is a plain attribute on both.)

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q -W error::FutureWarning` → PASS.
- [ ] **Step 5: Lint/mypy, stage** the four files. Commit message: `Hourly score inputs: prepare_hourly, pre-decision bar reads, ScoreBook.hourly and frozen panels`.

---

### Task 3: Runner `hourly`/`load_hourly`, `score_book`, null-check pool on frozen panels, CLI plumbing

**Files:**
- Modify: `agentic_trader/research/pooled/runner.py` (`CubeBuild`, `build_cube_inputs`), `agentic_trader/research/pooled/scoring.py` (`score_book`), `agentic_trader/research/pooled/null_check.py`, `agentic_trader/research/pooled/campaign_run.py`, `agentic_trader/research/pooled/power.py` (manifest fields only), `agentic_trader/cli/commands/alpha.py` (`_pooled_build` + the three pooled commands)
- Test: `tests/research/pooled/test_runner.py`, `test_null_check.py`, `test_campaign_run.py` (append), `tests/research/pooled/mini_world.py` (only if `CubeBuild(...)` constructions there need the new field — give `hourly` a default of an empty mapping so they do not)

**Interfaces:**
- Produces: `CubeBuild.hourly: Mapping[str, pd.DataFrame] = {}`; `build_cube_inputs(..., load_hourly: bool = False)`; `scoring.score_book(protocol, built) -> ScoreBook`; `null_check.run_null_check(..., book_factory=...)` or equivalent so the pool receives `FrozenPanels` for hourly protocols; manifests gain `score_inputs` and `score_read` (power, null-check, campaign).

- [ ] **Step 1: Write the failing tests**

Runner (`test_runner.py`, using its `FakeBars`/`FakeCalendar`): `test_build_cube_inputs_keeps_hourly_frames_when_asked` — a build with `load_hourly=True` returns `built.hourly` with the cohort symbols' regular hourly frames (non-empty) and with `load_hourly=False` returns an empty mapping; `test_cache_hit_reloads_hourly_from_the_bar_cache` — a second call on the same cache (cube exists) with `load_hourly=True` returns the same hourly frames and makes no provider hourly request beyond the cache (assert on the fake's call log).

Scoring (`test_scoring.py`): `test_score_book_dispatches_on_protocol_inputs` — `score_book(daily_protocol, built)` returns a book with `score_inputs == "daily"`; `score_book(intraday_protocol, built_with_hourly)` returns `"hourly"`; `score_book(intraday_protocol, built_without_hourly)` raises `ValueError("hourly frames")`.

Null check (`test_null_check.py`): `test_hourly_protocol_pool_uses_frozen_seed_panels` — with `workers=2`, monkeypatch `_init_worker` to record its `book` argument type: `FrozenPanels` for an intraday protocol, `ScoreBook` for a daily one; and the single-process path produces the same `summarize_null` result as the pooled path on a tiny intraday protocol (mini-world style).

Campaign run (`test_campaign_run.py`): `test_campaign_manifest_records_score_inputs` — the campaign manifest (or protocol.json/result) of a tiny intraday protocol records `score_inputs: "hourly"` and `score_read`, and `first_finite_session` per formula.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

- `runner.py`: `CubeBuild` gets `hourly: Mapping[str, pd.DataFrame] = field(default_factory=dict)` (use `dataclasses.field`); `build_cube_inputs(..., load_hourly: bool = False)`: on the build path keep the `hourly` dict already fetched (`hourly=hourly if load_hourly else {}`); on the cache-hit path, when `load_hourly`, fetch `"1h"` for every cohort symbol from the bar cache with the same `hourly_start`/`end` the build used (the cache holds them; `acquisition.fetch` reads the cache first) and `raise_on_errors()`.
- `scoring.py`:

```python
def score_book(protocol: CampaignProtocol, built: CubeBuild) -> ScoreBook:
    """The book a protocol's formulas read from: daily bars at D-1, or hourly bars before the decision."""
    if protocol.score_inputs == "hourly":
        if not built.hourly:
            raise ValueError(
                "this protocol scores hourly bars but the build carries no hourly frames; build with load_hourly=True"
            )
        read = protocol.score_read  # type: ignore[attr-defined]
        return ScoreBook.hourly(
            built.hourly,
            built.cube.sessions,
            built.cube.symbols,
            decision_time_et=protocol.bracket.decision_time_et,
            bars_before_decision=read.bars_before_decision,
        )
    return ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
```

  (import `CubeBuild` under `TYPE_CHECKING` to avoid a cycle if needed.)
- `null_check.py`: `run_null_check(protocol, discovery, calendar, cohort_sha256, *, book: ScoreBook, progress, workers)` — replace the `adjusted/trading_days/symbols` trio with the book built by the caller via `score_book`; for `workers > 1` pass `book.freeze(protocol.seed_expressions)` when `protocol.score_inputs == "hourly"`, else keep the daily behaviour by passing the frozen seeds too (simplest: always freeze for the pool — the null replicate of a fixed-set protocol evaluates only the seeds; for a GENETIC daily protocol the pool needs the full book, so: `pool_book = book.freeze(seeds) if protocol.search_mode == "fixed_set" else <the daily initargs as today>`; keep the daily genetic path byte-for-byte). `_init_worker` takes `book` directly (a `FrozenPanels` pickles cheaply); the daily genetic path keeps constructing `ScoreBook` in the worker from `adjusted` as today.
- `campaign_run.py`: both `ScoreBook(...)` constructions → `score_book(protocol, built)`; the campaign manifest/result gains `"score_inputs": protocol.score_inputs`, `"score_read": protocol.score_read.model_dump() if hourly else None`, and after preflight `"first_finite_session": {seed: first session with a finite score or None}` computed from `book.panel(seed)` over the discovery view (label-blind).
- `power.py`: manifest gains `"score_inputs": protocol.score_inputs` (no other change: check A uses synthetic formulas).
- `alpha.py`: `_pooled_build(clients, cohort, spec, cache_dir, *, require_cached=False, load_hourly=False)` passes `load_hourly` through; the `power`, `null-check` and `campaign` commands pass `load_hourly=loaded.protocol.score_inputs == "hourly"`; the `null-check` command builds the book with `score_book` and passes it to `execute_null_check`/`run_null_check` per the new signature.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/pooled tests/cli/test_cli_smoke.py -q -W error::FutureWarning` → PASS.
- [ ] **Step 5: Lint/mypy, stage.** Commit message: `Pooled runner keeps hourly frames on request; score_book dispatch; null-check pool on frozen seed panels; CLI plumbing`.

---

### Task 4: Frozen protocol `intraday-v1.json`, miner retirement, docs

**Files:**
- Create: `config/research/pooled/intraday-v1.json`
- Modify: `scripts/launchd.sh` (`install-miner`, `run-miner`, `status`, `install`), `docs/alpha-pooled-mining.md`, `docs/alpha-roadmap.md`, `CLAUDE.md`, `docs/production.md`
- Test: `tests/research/pooled/test_campaign_v4.py` → new `tests/research/pooled/test_intraday_v1.py`; `tests/scripts/test_launchd_miner_retired.py` (new; `subprocess.run(["zsh", "scripts/launchd.sh", "install-miner"], env=...)` with `HOME` set to a tmp dir, assert exit 0, the notice in stdout, and no plist under `tmp/Library/LaunchAgents`)

- [ ] **Step 1: Tests**

```python
# tests/research/pooled/test_intraday_v1.py
import json
from pathlib import Path

from agentic_trader.research.pooled.campaign import CampaignProtocol, IntradayCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.formula import require_dimensionless

V4 = Path("config/research/pooled/campaign-v4.json")
V1 = Path("config/research/pooled/intraday-v1.json")
EXPECTED_SCORES = {
    "first_hour_momentum": "oc_spread",
    "overnight_persistence": "open_gap",
    "overnight_vs_intraday": "open_gap - oc_spread",
    "relative_volume": "volume / ts_mean(volume, 140)",
    "range_expansion": "hl_spread / ts_mean(hl_spread, 140)",
    "range_location": "(close - low) / (high - low)",
    "vwap_strength": "(close - vwap) / close",
    "intraday_high_proximity": "close / ts_max(high, 140)",
}


def test_intraday_v1_is_a_fixed_set_on_v4s_cube():
    loaded = load_campaign_protocol(V1)
    protocol = loaded.protocol
    assert isinstance(protocol, IntradayCampaignProtocol)
    assert protocol.version == 1 and protocol.search_mode == "fixed_set"
    assert protocol.score_inputs == "hourly" and protocol.score_read.bars_before_decision == 1
    v4 = CampaignProtocol(**json.loads(V4.read_text()))
    assert protocol.cube_spec().identity == v4.cube_spec().identity
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")
    assert protocol.cohort_sha256 == v4.cohort_sha256 and protocol.windows == v4.windows
    assert protocol.selection == v4.selection and protocol.bootstrap == v4.bootstrap
    assert protocol.discovery_gate == v4.discovery_gate and protocol.confirmation_gate == v4.confirmation_gate
    assert protocol.power == v4.power and protocol.null_check == v4.null_check
    assert {f.id: f.seeds[0] for f in protocol.families} == EXPECTED_SCORES
    assert protocol.formula_budget == 8 and all(not f.mutation_operators for f in protocol.families)
    for seed in protocol.seed_expressions:
        require_dimensionless(seed)
    assert set(protocol.search.forbidden_operators) == {"realized_vol", "ts_std", "ts_mad"}
    assert protocol.power_search is None
```

- [ ] **Step 2: Write the protocol file** by copying `campaign-v4.json`, then setting: `"id": "pooled-intraday-campaign"`, `"version": 1`, `"title": "Intraday pooled campaign v1: cohort v2, hourly score inputs read at the first completed bar of the decision session, predeclared fixed set of eight intraday formulas (checks A and C), one-shot lane-wide confirmation"`, `"rationale"` (two sentences: the signal space and why fixed set), `"score_inputs": "hourly"`, `"score_read": {"bars_before_decision": 1}`, `"families"` = the eight entries `{id, rationale (one sentence with the citation from the spec table), seeds: [score], mutation_operators: []}` in the table's order, `"excluded_families"` = v4's plus `{"id": "reversal_5", "reason": "daily reversal; tested in v4"}`-style entries only if v4's families are NOT kept — v1 keeps NONE of v4's families, so list each v4 family id with reason "daily-bar family, scored in campaign v4 (no finalists)". `formula_budget: 8`. Everything else verbatim from v4. Keep `search.seed` and `bootstrap`/`power`/`null_check` seeds as v4 (the checks are reproducible per protocol hash).

- [ ] **Step 3: `scripts/launchd.sh`**: `install-miner` and `run-miner` → `echo "Alpha miner retired 2026-10-14: the per-symbol ETF32 miner produced no finalist in 62 runs; mining runs as predeclared pooled campaigns under operator approval (docs/alpha-pooled-mining.md)."; exit 0`; `install` no longer generates/loads the miner plist (copilot + watchdog only) and says so; `status` prints "Alpha Miner: retired (see docs/alpha-pooled-mining.md)"; `uninstall`/`uninstall-miner` keep removing a stale plist; remove `generate_alphaminer_plist` only if nothing else calls it (otherwise leave it unused with a comment).

- [ ] **Step 4: Docs** — `docs/alpha-pooled-mining.md`: new section "## Intraday score inputs (protocol `pooled-intraday-campaign`)" after "Formulas and picks": what a formula reads (regular hourly bars; the 09:30 bar for 10:35; bar-over-bar field semantics; windows in bars; NaN early in discovery), the subclass/loader, `load_hourly`, frozen panels for the null-check pool, the fixed set table (copy from the spec §3.4), the run order and that the campaign needs operator approval; "Commands" unchanged except a sentence that every command accepts either protocol id. `docs/alpha-roadmap.md` desk item 5: replace "Deferred" with the operator's 2026-10-14 decisions (intraday first on historical SIP, events second, live parity later) and this lane's status "built; checks A and C to run; campaign on approval". `CLAUDE.md`: in the pooled paragraph add two sentences: "`IntradayCampaignProtocol` (`id: pooled-intraday-campaign`) scores regular-session hourly bars read at the first completed bar of the decision session on the same cohort-v2 cube; v1 is a fixed set of eight intraday formulas under checks A and C; the weekly per-symbol ETF32 miner is retired (`launchd.sh install-miner` installs nothing)." `docs/production.md`: the miner section states the retirement.

- [ ] **Step 5: Run, lint, stage**: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_intraday_v1.py tests/scripts -q && env -u VIRTUAL_ENV uv run pre-commit run --files <touched>`; stage. Commit message: `Frozen intraday fixed-set protocol v1, miner retirement, docs`.
