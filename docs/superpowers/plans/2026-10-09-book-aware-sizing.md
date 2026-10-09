# Book-aware sizing (PR B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every native equity suggestion card's size is checked against the existing book's daily variance: a closed-form factor in [0, 1] (preview by default, enforce on request) with the arithmetic cross-checked by the shadow CVXPY optimiser, all journaled and shown on the card.

**Architecture:** A pure rule `book_vol_factor` in `agentic_trader/risk/book_vol.py`; a covariance estimator in `agentic_trader/research/setups/covariance.py` (Ledoit-Wolf on completed daily sessions); a `BookSizer` service in `agentic_trader/execution/book_sizing.py` that `run_scan` calls once per scan (`prepare`) and once per approved candidate (`decide`) at the L3 seam between `evaluate_candidate` and `record_signal`; `position_sizing.scale_sizing` owns the tier arithmetic; the decision rides in provenance, the notification payload, the ranked journal and `cards outcomes`.

**Tech Stack:** Python 3.14, `env -u VIRTUAL_ENV uv run pytest`, pydantic v2 frozen models, numpy/pandas, scikit-learn `LedoitWolf`, cvxpy (shadow only), existing desk fixtures (`shadow_desk`, `budget_desk`, `evaluation()`).

**Spec:** `docs/superpowers/specs/2026-10-09-book-aware-sizing-design.md`

## Global Constraints

- The factor is never above 1 and never raises a tier's quantity, risk or notional (spec D3).
- Admission, tap gate, re-pricing, backtest and replay are untouched; the optimiser's output never reaches size or orders (spec D4).
- Rules in `agentic_trader/risk/` are pure: no config reads, no I/O (`docs/risk-policy.md`).
- Journal/provenance/incident details carry type names only on failures, never exception text or DSNs.
- No migration (head `008_alpha_pipeline`); new config only under `book_sizing:` (`extra="forbid"`).
- Covariance uses completed sessions only (`session_date <= as_of` where `as_of` is the previous completed session date, the `features._closed_frame` rule) and raw Alpaca bars; a frame whose `attrs.get("adjustment")` is set and is not `"raw"` is treated as missing.
- Default mode `preview`: with the shipped defaults nothing changes a card's size; only new journal/provenance keys and one card line appear.
- Tests use the existing fixtures; no network, no PostgreSQL; `uv run pre-commit run --all-files` clean before each commit; commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A book position whose daily bars cannot be fetched must make the whole decision `unavailable` (factor 1, never 0) — never silently drop the position from the book vector (Task 4 test `test_missing_book_symbol_is_unavailable_not_dropped`).
2. In `enforce`, a later candidate in the same scan must see the earlier card at its scaled notional (Task 5 test `test_later_card_sees_scaled_book`).
3. `preview` must leave `eval_res` byte-identical, including `sizing_tiers` order (Task 5 test `test_preview_leaves_eval_res_identical`).
4. A short candidate against a long book must be allowed to *reduce* vol: factor 1 even when its own vol exceeds the budget (Task 1 test `test_hedging_candidate_is_not_scaled`).
5. Old notification payloads (no `book_sizing` key) must still deliver and render (Task 5 test `test_old_payload_renders_without_book_line`).

---

### Task 1: `risk/book_vol.py` — the closed-form rule

**Files:**
- Create: `agentic_trader/risk/book_vol.py`
- Modify: `agentic_trader/risk/__init__.py` (export `BookVolInputs`, `BookVolResult`, `book_vol_factor`)
- Test: `tests/risk/test_book_vol.py`

**Interfaces:**
- Produces: `BookVolInputs(weights: Mapping[str, float], candidate: str, candidate_notional: float, covariance: pd.DataFrame, budget_dollars: float)`, `BookVolResult(factor, vol_before, vol_after_full, vol_after_scaled, marginal_vol_full)`, `book_vol_factor(inputs) -> BookVolResult`; raises `ValueError` on invalid inputs (missing symbol, non-finite/negative variance, budget ≤ 0).

- [ ] **Step 1: Write the failing tests**

```python
# tests/risk/test_book_vol.py
import math

import numpy as np
import pandas as pd
import pytest

from agentic_trader.risk import BookVolInputs, book_vol_factor


def cov(symbols, vols, corr):
    """Daily covariance from per-unit-notional vols and one common correlation."""
    n = len(symbols)
    c = np.full((n, n), corr, dtype=float)
    np.fill_diagonal(c, 1.0)
    v = np.array(vols, dtype=float)
    return pd.DataFrame(np.outer(v, v) * c, index=symbols, columns=symbols)


def portfolio_vol(weights, covariance):
    w = np.array([weights.get(s, 0.0) for s in covariance.index])
    return math.sqrt(float(w @ covariance.to_numpy() @ w))


def test_empty_book_scales_by_the_candidate_alone():
    inputs = BookVolInputs(
        weights={},
        candidate="AAA",
        candidate_notional=10_000.0,
        covariance=cov(["AAA"], [0.02], 0.0),
        budget_dollars=100.0,
    )
    result = book_vol_factor(inputs)
    assert result.vol_before == 0.0
    assert math.isclose(result.vol_after_full, 200.0)
    assert math.isclose(result.factor, 0.5)
    assert math.isclose(result.vol_after_scaled, 100.0, rel_tol=1e-9)


def test_within_budget_is_factor_one():
    inputs = BookVolInputs(
        weights={"BBB": 5_000.0},
        candidate="AAA",
        candidate_notional=1_000.0,
        covariance=cov(["AAA", "BBB"], [0.01, 0.01], 0.0),
        budget_dollars=1_000.0,
    )
    result = book_vol_factor(inputs)
    assert result.factor == 1.0
    assert result.vol_after_scaled == result.vol_after_full


def test_book_already_over_budget_is_factor_zero():
    inputs = BookVolInputs(
        weights={"BBB": 50_000.0},
        candidate="AAA",
        candidate_notional=1_000.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.5),
        budget_dollars=100.0,
    )
    result = book_vol_factor(inputs)
    assert result.factor == 0.0
    assert result.vol_before > inputs.budget_dollars


def test_hedging_candidate_is_not_scaled():
    """A short against a correlated long book lowers vol: factor 1 even though the candidate's own vol
    exceeds the budget."""
    inputs = BookVolInputs(
        weights={"BBB": 20_000.0},
        candidate="AAA",
        candidate_notional=-15_000.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.9),
        budget_dollars=380.0,
    )
    result = book_vol_factor(inputs)
    assert result.vol_after_full < result.vol_before
    assert result.factor == 1.0


def test_root_matches_numeric_solution():
    covariance = cov(["AAA", "BBB", "CCC"], [0.015, 0.02, 0.025], 0.4)
    weights = {"BBB": 12_000.0, "CCC": -4_000.0}
    inputs = BookVolInputs(
        weights=weights, candidate="AAA", candidate_notional=20_000.0, covariance=covariance, budget_dollars=400.0
    )
    result = book_vol_factor(inputs)
    assert 0.0 < result.factor < 1.0
    scaled = {**weights, "AAA": result.factor * 20_000.0}
    assert math.isclose(portfolio_vol(scaled, covariance), 400.0, rel_tol=1e-9)
    assert math.isclose(result.marginal_vol_full, result.vol_after_full - result.vol_before)


@pytest.mark.parametrize("budget", [100.0, 300.0, 1_000.0, 5_000.0])
def test_factor_is_monotone_in_budget_and_bounded(budget):
    covariance = cov(["AAA", "BBB"], [0.02, 0.02], 0.3)
    base = dict(weights={"BBB": 8_000.0}, candidate="AAA", candidate_notional=30_000.0, covariance=covariance)
    lo = book_vol_factor(BookVolInputs(**base, budget_dollars=budget)).factor
    hi = book_vol_factor(BookVolInputs(**base, budget_dollars=budget * 2)).factor
    assert 0.0 <= lo <= hi <= 1.0


@pytest.mark.parametrize(
    "bad",
    [
        dict(weights={"ZZZ": 1.0}),  # symbol missing from the covariance
        dict(candidate="ZZZ"),
        dict(budget_dollars=0.0),
        dict(covariance=pd.DataFrame([[float("nan")]], index=["AAA"], columns=["AAA"]), weights={}),
        dict(covariance=pd.DataFrame([[-1e-4]], index=["AAA"], columns=["AAA"]), weights={}),
    ],
)
def test_invalid_inputs_raise_value_error(bad):
    base = dict(
        weights={},
        candidate="AAA",
        candidate_notional=1_000.0,
        covariance=cov(["AAA"], [0.02], 0.0),
        budget_dollars=100.0,
    )
    with pytest.raises(ValueError):
        book_vol_factor(BookVolInputs(**{**base, **bad}))
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/risk/test_book_vol.py -q`
Expected: FAIL with `ImportError: cannot import name 'BookVolInputs'`.

- [ ] **Step 3: Implement**

```python
# agentic_trader/risk/book_vol.py
"""Book-aware size factor: the largest fraction of a card's notional that keeps the book's daily
dollar volatility within a budget (docs/risk-policy.md, docs/card-evidence.md).

Pure: no config, no I/O. Consumed by one layer (card construction in ``run_scan``) by design --
admission never shrinks a tier the operator was offered -- and never by backtest or replay.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["BookVolInputs", "BookVolResult", "book_vol_factor"]


@dataclass(frozen=True)
class BookVolInputs:
    weights: Mapping[str, float]  # signed dollar notional per book symbol (long +, short -)
    candidate: str
    candidate_notional: float  # signed dollar notional at the proposed quantity
    covariance: pd.DataFrame  # daily-return covariance per unit notional; index == columns
    budget_dollars: float  # allowed portfolio daily dollar-vol after the card


@dataclass(frozen=True)
class BookVolResult:
    factor: float
    vol_before: float
    vol_after_full: float
    vol_after_scaled: float
    marginal_vol_full: float


def _validate(inputs: BookVolInputs) -> np.ndarray:
    cov = inputs.covariance
    if list(cov.index) != list(cov.columns):
        raise ValueError("covariance index and columns differ")
    missing = [s for s in (*inputs.weights, inputs.candidate) if s not in cov.index]
    if missing:
        raise ValueError(f"symbols missing from covariance: {sorted(set(missing))}")
    matrix = cov.to_numpy(dtype=float)
    if not np.all(np.isfinite(matrix)):
        raise ValueError("covariance has non-finite entries")
    if np.any(np.diag(matrix) < 0):
        raise ValueError("covariance has negative variances")
    if not (math.isfinite(inputs.budget_dollars) and inputs.budget_dollars > 0):
        raise ValueError("budget_dollars must be positive and finite")
    if not math.isfinite(inputs.candidate_notional):
        raise ValueError("candidate_notional must be finite")
    return matrix


def book_vol_factor(inputs: BookVolInputs) -> BookVolResult:
    """``vol(f)^2 = a + 2 b f + c f^2`` with the book fixed; the largest ``f`` in [0, 1] with ``vol(f) <= budget``."""
    matrix = _validate(inputs)
    symbols = list(inputs.covariance.index)
    w = np.array([inputs.weights.get(s, 0.0) for s in symbols], dtype=float)
    unit = np.array([1.0 if s == inputs.candidate else 0.0 for s in symbols])
    x = inputs.candidate_notional
    a = float(w @ matrix @ w)
    b = float(x * (unit @ matrix @ w))
    c = float(x * x * (unit @ matrix @ unit))
    vol_before = math.sqrt(max(a, 0.0))
    vol_after_full = math.sqrt(max(a + 2 * b + c, 0.0))
    budget_sq = inputs.budget_dollars**2
    if vol_after_full <= inputs.budget_dollars or x == 0.0:
        factor = 1.0
    elif c <= 0.0:
        factor = 0.0
    else:
        # Largest root of c f^2 + 2 b f + (a - budget^2) = 0, clipped to [0, 1].
        disc = b * b - c * (a - budget_sq)
        factor = 0.0 if disc < 0 else min(1.0, max(0.0, (-b + math.sqrt(disc)) / c))
    vol_after_scaled = math.sqrt(max(a + 2 * b * factor + c * factor * factor, 0.0))
    return BookVolResult(
        factor=factor,
        vol_before=vol_before,
        vol_after_full=vol_after_full,
        vol_after_scaled=vol_after_scaled,
        marginal_vol_full=vol_after_full - vol_before,
    )
```

Add to `agentic_trader/risk/__init__.py`: `from agentic_trader.risk.book_vol import BookVolInputs, BookVolResult, book_vol_factor` and the three names in `__all__`.

- [ ] **Step 4: Run tests** — `env -u VIRTUAL_ENV uv run pytest tests/risk -q` → PASS (the existing `tests/risk` suites too).

- [ ] **Step 5: Commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --all-files
git add agentic_trader/risk tests/risk/test_book_vol.py
git commit -m "Risk: book_vol_factor — closed-form book-aware size factor against a daily dollar-vol budget

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `research/setups/covariance.py` — completed-session returns and Ledoit-Wolf

**Files:**
- Create: `agentic_trader/research/setups/covariance.py`
- Test: `tests/research/setups/test_covariance.py`

**Interfaces:**
- Produces: `RAW_ADJUSTMENTS = frozenset({None, "raw"})`; `is_raw_frame(frame) -> bool`; `daily_returns(frames: Mapping[str, pd.DataFrame], *, as_of: date, lookback_sessions: int, min_observations: int) -> tuple[pd.DataFrame, tuple[str, ...]]` (aligned simple close-to-close returns, and the symbols dropped); `shrunk_covariance(returns: pd.DataFrame) -> tuple[pd.DataFrame, float]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/setups/test_covariance.py
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.covariance import daily_returns, is_raw_frame, shrunk_covariance


def frame(seed: int, sessions: int = 200, start: str = "2026-01-02") -> pd.DataFrame:
    index = pd.bdate_range(start=start, periods=sessions)
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, sessions)))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Volume": 1e6}, index=index)


def test_returns_use_completed_sessions_only_and_align_on_dates():
    a, b = frame(1), frame(2, sessions=180)  # b starts the same day, ends earlier
    as_of = a.index[-1].date()
    returns, dropped = daily_returns({"AAA": a, "BBB": b}, as_of=as_of, lookback_sessions=500, min_observations=60)
    assert dropped == ()
    assert list(returns.columns) == ["AAA", "BBB"]
    assert len(returns) == 179  # intersection of 180 closes -> 179 returns
    expected = a["Close"].pct_change().dropna().loc[returns.index]
    assert np.allclose(returns["AAA"].to_numpy(), expected.to_numpy())


def test_bars_on_or_after_as_of_are_excluded():
    a = frame(3)
    as_of = a.index[-5].date()  # pretend the last 4 sessions are not completed
    returns, _ = daily_returns({"AAA": a}, as_of=as_of, lookback_sessions=500, min_observations=10)
    assert returns.index.max().date() <= as_of  # the strict < as_of rule is in the spec: see implementation


def test_lookback_window_limits_rows():
    returns, _ = daily_returns({"AAA": frame(4)}, as_of=date(2027, 1, 1), lookback_sessions=50, min_observations=10)
    assert len(returns) == 50


def test_symbols_below_min_observations_are_dropped_and_reported():
    short = frame(5, sessions=30)
    returns, dropped = daily_returns(
        {"AAA": frame(6), "BBB": short}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60
    )
    assert dropped == ("BBB",)
    assert list(returns.columns) == ["AAA"]


def test_adjusted_frame_is_not_raw():
    adjusted = frame(7)
    adjusted.attrs["adjustment"] = "yfinance_auto_adjust"
    raw = frame(8)
    raw.attrs["adjustment"] = "raw"
    assert not is_raw_frame(adjusted)
    assert is_raw_frame(raw)
    assert is_raw_frame(frame(9))  # no attr -> raw by default


def test_shrunk_covariance_is_symmetric_psd_with_shrinkage_in_unit_interval():
    returns, _ = daily_returns(
        {s: frame(i) for i, s in enumerate(("AAA", "BBB", "CCC"))},
        as_of=date(2027, 1, 1),
        lookback_sessions=500,
        min_observations=60,
    )
    covariance, shrinkage = shrunk_covariance(returns)
    matrix = covariance.to_numpy()
    assert np.allclose(matrix, matrix.T)
    assert np.linalg.eigvalsh(matrix).min() >= -1e-12
    assert 0.0 <= shrinkage <= 1.0
    assert list(covariance.index) == ["AAA", "BBB", "CCC"]


def test_single_symbol_covariance_is_its_variance():
    returns, _ = daily_returns({"AAA": frame(10)}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60)
    covariance, shrinkage = shrunk_covariance(returns)
    assert covariance.shape == (1, 1)
    assert np.isclose(covariance.iloc[0, 0], returns["AAA"].var(ddof=0))
    assert shrinkage == 0.0


def test_empty_returns_raise():
    with pytest.raises(ValueError):
        shrunk_covariance(pd.DataFrame())
```

- [ ] **Step 2: Run to verify failure** — `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_covariance.py -q` → ImportError.

- [ ] **Step 3: Implement**

```python
# agentic_trader/research/setups/covariance.py
"""Daily-return covariance for book-aware sizing (docs/card-evidence.md).

Completed sessions only (a bar dated on or after ``as_of`` is not completed), raw Alpaca prices only,
Ledoit-Wolf shrinkage. Research-grade estimation code: the sizing rule itself is
``agentic_trader.risk.book_vol``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

__all__ = ["RAW_ADJUSTMENTS", "daily_returns", "is_raw_frame", "shrunk_covariance"]

RAW_ADJUSTMENTS: frozenset[str | None] = frozenset({None, "raw"})


def is_raw_frame(frame: pd.DataFrame) -> bool:
    """True when the frame carries no adjustment tag or the raw one (never yfinance auto-adjust)."""
    return frame.attrs.get("adjustment") in RAW_ADJUSTMENTS


def _closes_before(frame: pd.DataFrame, as_of: date) -> pd.Series:
    session_dates = pd.DatetimeIndex(frame.index).date
    closes = frame.loc[session_dates < as_of, "Close"].astype(float)
    closes.index = pd.DatetimeIndex(closes.index).normalize()
    return closes[~closes.index.duplicated(keep="last")]


def daily_returns(
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: date,
    lookback_sessions: int,
    min_observations: int,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Aligned simple close-to-close returns over the last ``lookback_sessions`` completed sessions.

    Symbols with fewer than ``min_observations`` aligned returns are dropped and returned second.
    """
    series = {symbol: _closes_before(frame, as_of).pct_change().dropna() for symbol, frame in frames.items()}
    kept = {s: r for s, r in series.items() if len(r) >= min_observations}
    dropped = tuple(sorted(s for s in series if s not in kept))
    if not kept:
        return pd.DataFrame(), dropped
    aligned = pd.concat(kept, axis=1, join="inner").sort_index()
    aligned = aligned.iloc[-lookback_sessions:]
    aligned.columns = list(kept)
    short = tuple(sorted(c for c in aligned.columns if aligned[c].notna().sum() < min_observations))
    if short:
        aligned = aligned.drop(columns=list(short))
        dropped = tuple(sorted({*dropped, *short}))
    return aligned.dropna(), dropped


def shrunk_covariance(returns: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Ledoit-Wolf covariance (daily, per unit notional) and the fitted shrinkage."""
    if returns.empty or returns.shape[1] == 0:
        raise ValueError("no returns to estimate a covariance from")
    if returns.shape[1] == 1:
        variance = float(np.var(returns.iloc[:, 0].to_numpy(dtype=float)))
        return pd.DataFrame([[variance]], index=returns.columns, columns=returns.columns), 0.0
    model = LedoitWolf().fit(returns.to_numpy(dtype=float))
    covariance = pd.DataFrame(model.covariance_, index=returns.columns, columns=returns.columns)
    return covariance, float(model.shrinkage_)
```

Note on `test_bars_on_or_after_as_of_are_excluded`: the implementation keeps sessions strictly before `as_of`; make the test assert `returns.index.max().date() < as_of`.

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit** (`git add agentic_trader/research/setups/covariance.py tests/research/setups/test_covariance.py`, message "Research: completed-session daily returns and Ledoit-Wolf covariance for book-aware sizing").

---

### Task 3: Config and tier arithmetic

**Files:**
- Modify: `agentic_trader/config.py` (after `CardPolicyConfig`: `BookSizingMode`, `BookSizingConfig`; `AppConfig.book_sizing`; `load_config` wiring next to `card_policy=` ~997)
- Modify: `config/config.yaml` (a `book_sizing:` block after `card_policy:`)
- Modify: `agentic_trader/agent/position_sizing.py` (add `scale_sizing`)
- Test: `tests/config/test_book_sizing_config.py`, `tests/execution/test_position_sizing.py` (append)

**Interfaces:**
- Produces: `BookSizingMode = Literal["off", "preview", "enforce"]`; `BookSizingConfig(mode="preview", max_portfolio_daily_vol_pct=0.008, lookback_sessions=120, min_observations=60, shadow_optimizer=True)` with `extra="forbid"`, YAML `off`→"off" (copy `CardPolicyConfig.yaml_off`), validators: `0 < max_portfolio_daily_vol_pct <= 0.1`, `lookback_sessions >= min_observations >= 20`; `AppConfig.book_sizing`; `scale_sizing(eval_res: LLMTradeEvaluation, factor: float, *, min_units: float, portfolio_cash: float) -> LLMTradeEvaluation | None` (None when the default tier falls below `min_units`).

- [ ] **Step 1: Failing config tests**

```python
# tests/config/test_book_sizing_config.py
import pytest
from pydantic import ValidationError

from agentic_trader.config import BookSizingConfig


def test_defaults_are_preview_with_the_agreed_budget():
    cfg = BookSizingConfig()
    assert cfg.mode == "preview"
    assert cfg.max_portfolio_daily_vol_pct == 0.008
    assert cfg.lookback_sessions == 120
    assert cfg.min_observations == 60
    assert cfg.shadow_optimizer is True


def test_yaml_false_means_off():
    assert BookSizingConfig(mode=False).mode == "off"


@pytest.mark.parametrize(
    "bad",
    [
        {"max_portfolio_daily_vol_pct": 0.0},
        {"max_portfolio_daily_vol_pct": 0.5},
        {"lookback_sessions": 30, "min_observations": 60},
        {"min_observations": 5},
        {"unknown": 1},
    ],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        BookSizingConfig(**bad)
```

Also add to `tests/config/test_universe_config.py` (where `load_config` fixtures already exist) one test that a YAML with `book_sizing: {mode: enforce}` loads `config.book_sizing.mode == "enforce"` and that the shipped `config/config.yaml` loads with `book_sizing.mode == "preview"` (follow the file's existing pattern for loading the shipped YAML).

- [ ] **Step 2: Failing tier-arithmetic tests** (append to `tests/execution/test_position_sizing.py`; read its imports and how an `LLMTradeEvaluation`/tier dict is built there or in `tests/agent/test_scan_budget.py::evaluation`)

```python
def _eval_with_tiers():
    tiers = [
        {
            "tier_id": "half",
            "label": "Conservative (0.5x)",
            "quantity": 20.0,
            "risk_dollars": 36.0,
            "reward_dollars": 72.0,
            "notional_dollars": 2_000.0,
            "effective_leverage": 0.02,
            "is_default": False,
        },
        {
            "tier_id": "base",
            "label": "Standard (1.0x)",
            "quantity": 40.0,
            "risk_dollars": 72.0,
            "reward_dollars": 144.0,
            "notional_dollars": 4_000.0,
            "effective_leverage": 0.04,
            "is_default": True,
        },
        {
            "tier_id": "max",
            "label": "Max Permissible",
            "quantity": 75.0,
            "risk_dollars": 135.0,
            "reward_dollars": 270.0,
            "notional_dollars": 7_500.0,
            "effective_leverage": 0.075,
            "is_default": False,
        },
    ]
    return LLMTradeEvaluation(
        approved=True,
        contract="AAA",
        direction="LONG",
        entry_price=100.0,
        stop_loss=98.2,
        take_profit=103.6,
        stop_distance_points=1.8,
        target_distance_points=3.6,
        risk_reward_ratio=2.0,
        risk_dollars=72.0,
        reward_dollars=144.0,
        notional_value=4_000.0,
        effective_leverage=0.04,
        macro_clearance=True,
        thesis_summary="t",
        quantity=40.0,
        asset_class=AssetClass.EQUITY,
        sizing_tiers=tiers,
    )


def test_scale_sizing_scales_every_tier_with_whole_units_and_never_raises():
    scaled = scale_sizing(_eval_with_tiers(), 0.6, min_units=1.0, portfolio_cash=100_000.0)
    assert [t["quantity"] for t in scaled.sizing_tiers] == [12.0, 24.0, 45.0]
    assert scaled.quantity == 24.0
    assert scaled.risk_dollars == pytest.approx(1.8 * 24)
    assert scaled.reward_dollars == pytest.approx(3.6 * 24)
    assert scaled.notional_value == pytest.approx(2_400.0)
    assert scaled.effective_leverage == pytest.approx(0.024)
    assert all(t["is_default"] for t in scaled.sizing_tiers if t["tier_id"] == "base")


def test_scale_sizing_factor_one_is_identity():
    original = _eval_with_tiers()
    assert scale_sizing(original, 1.0, min_units=1.0, portfolio_cash=100_000.0) == original


def test_scale_sizing_drops_sub_minimum_tiers_and_blocks_when_default_vanishes():
    scaled = scale_sizing(_eval_with_tiers(), 0.04, min_units=1.0, portfolio_cash=100_000.0)
    assert [t["tier_id"] for t in scaled.sizing_tiers] == ["max"]  # half -> 0, base -> 1 (floor 1.6 = 1) ... see note
    assert scale_sizing(_eval_with_tiers(), 0.01, min_units=1.0, portfolio_cash=100_000.0) is None
```

Note: compute the expected quantities with `math.floor(qty * factor)`; with factor 0.04: half 0.8→0 (dropped), base 1.6→1 (kept, default), max 3.0→3. Correct the first assertion to `["base", "max"]` and `scaled.quantity == 1.0`. With 0.01: half 0.2→0, base 0.4→0 (default gone) → None regardless of max 0.75→0.

- [ ] **Step 3: Implement**

`config.py` (after `CardPolicyConfig`):

```python
BookSizingMode = Literal["off", "preview", "enforce"]


class BookSizingConfig(BaseModel):
    """Book-aware sizing: a card's size against the book's daily dollar-vol budget (docs/card-evidence.md).

    ``preview`` (default) journals the factor and shows it on the card without changing size;
    ``enforce`` scales every tier by it. The factor never raises size; missing covariance never
    blocks. Equities only; the shadow optimiser cross-check never reaches size or orders.
    """

    model_config = {"extra": "forbid"}

    mode: BookSizingMode = "preview"
    max_portfolio_daily_vol_pct: float = Field(default=0.008, gt=0.0, le=0.1, allow_inf_nan=False)
    lookback_sessions: int = Field(default=120, ge=20, le=750)
    min_observations: int = Field(default=60, ge=20, le=750)
    shadow_optimizer: bool = True

    @field_validator("mode", mode="before")
    @classmethod
    def yaml_off(cls, value: Any) -> Any:
        return "off" if value is False else value

    @model_validator(mode="after")
    def window_covers_floor(self) -> BookSizingConfig:
        if self.lookback_sessions < self.min_observations:
            raise ValueError("book_sizing.lookback_sessions must be at least min_observations")
        return self
```

`AppConfig`: `book_sizing: BookSizingConfig = Field(default_factory=BookSizingConfig)`; `load_config`: `book_sizing=BookSizingConfig(**(cfg_dict.get("book_sizing") or {})),`. `config/config.yaml`:

```yaml
book_sizing:
  mode: preview            # off | preview | enforce (docs/card-evidence.md "Book-aware sizing")
  max_portfolio_daily_vol_pct: 0.008
  lookback_sessions: 120
  min_observations: 60
  shadow_optimizer: true
```

`position_sizing.py`:

```python
def scale_sizing(
    eval_res: LLMTradeEvaluation, factor: float, *, min_units: float, portfolio_cash: float
) -> LLMTradeEvaluation | None:
    """Every tier and the headline size scaled by ``factor`` in [0, 1] with whole units.

    Per-unit risk/reward/notional come from the evaluation's own bracket (``stop_distance_points``,
    ``target_distance_points``, ``entry_price``), so the arithmetic matches ``calculate_dynamic_sizing``.
    Tiers that round below ``min_units`` are dropped; when the default tier is dropped the card
    cannot be sized and None is returned. ``factor == 1`` returns the evaluation unchanged.
    """
    if not 0.0 <= factor <= 1.0:
        raise ValueError("factor must be in [0, 1]")
    if factor == 1.0:
        return eval_res
    per_unit_risk = eval_res.stop_distance_points
    per_unit_reward = eval_res.target_distance_points
    unit_notional = eval_res.entry_price

    def scaled_tier(tier: dict[str, Any]) -> dict[str, Any] | None:
        qty = float(math.floor(float(tier["quantity"]) * factor))
        if qty < min_units:
            return None
        return {
            **tier,
            "quantity": qty,
            "risk_dollars": round(per_unit_risk * qty, 2),
            "reward_dollars": round(per_unit_reward * qty, 2),
            "notional_dollars": round(unit_notional * qty, 2),
            "effective_leverage": round(unit_notional * qty / portfolio_cash, 2),
        }

    tiers = [t for t in (scaled_tier(t) for t in eval_res.sizing_tiers or []) if t is not None]
    default = next((t for t in tiers if t.get("is_default")), None)
    if eval_res.sizing_tiers and default is None:
        return None
    qty = float(default["quantity"]) if default else float(math.floor(eval_res.quantity * factor))
    if qty < min_units:
        return None
    return eval_res.model_copy(
        update={
            "quantity": qty,
            "risk_dollars": round(per_unit_risk * qty, 2),
            "reward_dollars": round(per_unit_reward * qty, 2),
            "notional_value": round(unit_notional * qty, 2),
            "effective_leverage": round(unit_notional * qty / portfolio_cash, 2),
            "sizing_tiers": tiers or None,
        }
    )
```

Import `LLMTradeEvaluation` under `TYPE_CHECKING` if `position_sizing.py` must not import the evaluator at runtime (check for a cycle: `evaluator.py` imports `position_sizing`); `math` and `Any` imports as needed. Futures multiply by the contract multiplier in `calculate_dynamic_sizing`; `scale_sizing` is equities-only in PR B — assert `eval_res.asset_class == AssetClass.EQUITY` and raise `ValueError` otherwise, with a test.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/config tests/execution/test_position_sizing.py -q` → PASS. **Step 5: Commit** ("Config and sizing: BookSizingConfig (preview default) and scale_sizing tier arithmetic").

---

### Task 4: `execution/book_sizing.py` — `BookSizer`, decision, shadow cross-check, outcome

**Files:**
- Create: `agentic_trader/execution/book_sizing.py`
- Modify: `agentic_trader/execution/durable.py` (`RankedOutcome.BOOK_SIZING_BLOCKED = "book_sizing_blocked"`)
- Test: `tests/execution/test_book_sizing.py`

**Interfaces:**
- Consumes: Task 1 `book_vol_factor`, Task 2 `daily_returns`/`shrunk_covariance`/`is_raw_frame`, Task 3 `BookSizingConfig`/`scale_sizing`, `agentic_trader.research.alpha.optimizer.ConvexAlphaPortfolioOptimizer`, `AlpacaDataProvider.fetch_daily_many(symbols, start, end, adjustment="raw")`.
- Produces:

```python
BOOK_SIZING_FETCH_TIMEOUT_SECONDS = 20.0

class BookSizingStatus(StrEnum): APPLIED, UNCHANGED, BLOCKED, UNAVAILABLE, NOT_APPLICABLE  # values as in the spec

class BookSizingDecision(BaseModel, frozen=True):  # fields exactly as spec D3 (pydantic, allow_inf_nan=False on floats)

class BookContext(BaseModel, frozen=True, arbitrary_types_allowed=True):
    status: Literal["ready", "unavailable", "off"]
    reason: str | None
    covariance: pd.DataFrame | None
    shrinkage: float | None
    n_observations: int | None
    weights: dict[str, float]          # signed equity notional per symbol from the book rows
    book_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]

class BookSizer:
    def __init__(self, config: BookSizingConfig, bar_source: AlpacaDataProvider | None, *, portfolio_cash: float, min_units: float) -> None
    async def prepare(self, active_positions: list[dict[str, Any]], datasets: Mapping[str, Any], *, as_of: date, candidates: Sequence[str]) -> BookContext
    def decide(self, context: BookContext, *, candidate: Any, eval_res: LLMTradeEvaluation, risk_capital: float, dry_run: bool) -> tuple[BookSizingDecision, LLMTradeEvaluation | None]
    @staticmethod
    def journal_block(decision: BookSizingDecision | None) -> dict[str, Any]
```

`decide` returns the decision and the evaluation to record: unchanged `eval_res` for preview/unchanged/unavailable/not_applicable, the scaled one for `applied`, `None` for `blocked`.

- [ ] **Step 1: Failing tests** (`tests/execution/test_book_sizing.py`). Build `BookSizingConfig` directly; fake `bar_source` as `MagicMock(fetch_daily_many=...)`; frames from a local `frame(seed)` like Task 2's; `eval_res` via `_eval_with_tiers()` from Task 3 (import it or copy); `candidate` as `SimpleNamespace(contract="AAA", asset_class=AssetClass.EQUITY, catalog_event=None, alpha_version=None, alpha_policy=None, probe=False)`; book rows as dicts `{"contract": "BBB", "direction": "LONG", "asset_class": "EQUITY", "notional_value": 20_000.0, "risk_dollars": 300.0, "status": "EXECUTED"}`. Tests:

  - `test_off_mode_is_not_applicable_and_never_fetches`
  - `test_prepare_uses_scan_datasets_and_fetches_only_missing_book_symbols` (assert `fetch_daily_many` called with exactly the missing symbols, `adjustment="raw"`)
  - `test_missing_book_symbol_is_unavailable_not_dropped` (fetch returns no frame for BBB → `context.status == "unavailable"`, `missing_symbols == ("BBB",)`, `decide` → `UNAVAILABLE`, factor None, `eval_res` unchanged)
  - `test_fetch_timeout_is_unavailable_with_type_name_only` (fetch sleeps past a monkeypatched `BOOK_SIZING_FETCH_TIMEOUT_SECONDS=0.01` → reason `"TimeoutError"`)
  - `test_adjusted_frame_counts_as_missing`
  - `test_preview_records_would_scale_and_leaves_eval_res_identical` (correlated book, budget tiny → `factor < 1`, `would_scale True`, status `UNCHANGED`, returned eval is the same object)
  - `test_enforce_scales_every_tier_and_reports_applied`
  - `test_enforce_blocks_when_default_tier_vanishes` (status `BLOCKED`, returned eval None, `reason` starts with `"book sizing: portfolio vol "`)
  - `test_non_equity_candidate_and_dry_run_are_not_applicable`
  - `test_policy_locked_and_drift_candidates_are_not_applicable`
  - `test_shadow_optimizer_block_matches_closed_form` (`shadow["status"] == "ok"`, `abs(shadow["vol_after_scaled_pct"] - decision.vol_after_scaled_pct) < 1e-6`)
  - `test_shadow_optimizer_failure_never_changes_the_decision` (monkeypatch `ConvexAlphaPortfolioOptimizer.optimize` to raise → `shadow == {"status": "failed", "reason": "RuntimeError"}`, decision otherwise identical to a run with `shadow_optimizer=False`)
  - `test_journal_block_shape` (keys: mode, status, factor, would_scale, vol_before_pct, vol_after_full_pct, quantity_before, quantity_after; None decision → all None except mode)

- [ ] **Step 2: Run to verify failure** → ImportError.

- [ ] **Step 3: Implement** `agentic_trader/execution/book_sizing.py`. Key points (write the full module):

  - `prepare`: if `config.mode == "off"` → `BookContext(status="off", ...)`. Book vector from rows with `asset_class` `EQUITY` (compare `str(row.get("asset_class")).upper().endswith("EQUITY")`) and valid `notional_value`: `weights[symbol] += ±notional` by direction (`LONG` +, `SHORT` −). Symbols needed = book symbols ∪ `candidates`. Frames: for each symbol, `datasets[symbol].daily` when present and `is_raw_frame`; the rest via `await asyncio.wait_for(asyncio.to_thread(self._bar_source.fetch_daily_many, missing, start, end, adjustment="raw"), BOOK_SIZING_FETCH_TIMEOUT_SECONDS)` with `start = as_of - timedelta(days=int(config.lookback_sessions * 1.6) + 10)`, `end = as_of`. Any book symbol still without a frame → `status="unavailable"`, `reason="missing_bars"`, `missing_symbols`. Then `daily_returns(...)` and `shrunk_covariance(...)`; a dropped book symbol (too few observations) → `unavailable`, `reason="insufficient_observations"`; a dropped *candidate* only removes that candidate (its `decide` is `unavailable`). Any exception → `unavailable` with `type(exc).__name__`; never raises. `n_observations = len(returns)`.
  - `decide`: exemptions first (`dry_run` → `NOT_APPLICABLE` reason `dry_run`; `context.status == "off"`; `candidate.catalog_event is not None`; `candidate.alpha_version or candidate.alpha_policy or candidate.probe`; `eval_res.asset_class != EQUITY`). Then `context.status == "unavailable"` or candidate not in covariance → `UNAVAILABLE`, factor None, `reason=context.reason or "candidate_missing"`. Else `budget = config.max_portfolio_daily_vol_pct * risk_capital`; `signed = eval_res.notional_value * (1 if direction == LONG else -1)`; `result = book_vol_factor(BookVolInputs(weights=context.weights, candidate=symbol, candidate_notional=signed, covariance=context.covariance, budget_dollars=budget))` (a `ValueError` → `UNAVAILABLE`, reason `ValueError`). Percentages: `vol / risk_capital`. `would_scale = result.factor < 1`. Preview → `UNCHANGED`, eval unchanged. Enforce: `factor == 1` → `UNCHANGED`; else `scaled = scale_sizing(eval_res, result.factor, min_units=self._min_units, portfolio_cash=self._portfolio_cash)`; `None` → `BLOCKED` with reason `f"book sizing: portfolio vol {vol_after_full_pct:.2%} > budget {budget_pct:.2%}/day"`; else `APPLIED`. Shadow block (D4) when `config.shadow_optimizer` and covariance available, computed at the **scaled** weight (`factor × signed` or 0 when blocked), via `_shadow_check(...)`: weights as fractions of `risk_capital`; `ConvexAlphaPortfolioOptimizer(gross_leverage_limit=10.0, max_position_weight=10.0, long_only=False, solver_seconds=2).optimize(alpha, covariance, current_weights=w, lower_bounds=w, upper_bounds=w)` where `alpha` is 1.0 on the candidate and 0 elsewhere (as pandas Series aligned to the covariance index; read `optimize`'s accepted types at `research/alpha/optimizer.py:85-133` first); `vol = sqrt(result.portfolio_variance)`; block `{"status": "ok", "vol_after_scaled_pct": vol, "closed_form_pct": decision value, "abs_diff_pct": ..., "solver_message": result.message, "seconds": ...}`; mismatch `> 1e-6` logs WARNING `book_sizing_shadow_mismatch`; any exception → `{"status": "failed", "reason": type name}`.
  - `journal_block(decision)`: the eight keys above.
  - Add `BOOK_SIZING_BLOCKED = "book_sizing_blocked"` to `RankedOutcome` with a docstring line.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/execution/test_book_sizing.py tests/execution -q` → PASS. **Step 5: Commit** ("Execution: BookSizer — book-aware size decision with shadow optimiser cross-check; book_sizing_blocked outcome").

---

### Task 5: The L3 seam in `run_scan`, card line, notification, journal, provenance

**Files:**
- Modify: `agentic_trader/agent/copilot.py` (constructor: `self.book_sizer`; `run_scan`: `prepare` once after `_fetch_universe`/before the send loop, `decide` after `evaluate_candidate`; provenance, notification, book join, `_journal_scan_ranking` candidate dict; `_replacement_card` carries the stored `book_sizing` through unchanged)
- Modify: `agentic_trader/notifier/telegram_bot.py` (`book_sizing` kwarg on `format_alert_card`, `format_terminal_card`, `send_signal_alert`; `_book_line(book_sizing, *, markup)`)
- Modify: `agentic_trader/cli/commands/service.py` only if the sizer needs a bar source built at startup (use `build_bar_source(config)` from `research/setups/sources.py`; the CLI `copilot scan` path builds the same; a dry run passes `bar_source=None`)
- Test: `tests/agent/test_scan_book_sizing.py`, `tests/notifier/test_book_line.py`

**Interfaces:**
- Consumes Task 4's `BookSizer`, `BookSizingDecision`, `journal_block`.
- Produces: provenance key `decision_provenance["book_sizing"]` (decision `model_dump(mode="json")`), notification key `book_sizing`, journal per-candidate key `"book_sizing"`, outcome `book_sizing_blocked`, card line format (spec D6).

- [ ] **Step 1: Failing integration tests** on `shadow_desk` (read `tests/agent/test_scan_budget.py` for `evaluation()` and how `budget_desk` wires the evaluator; make `evaluation()` return an equity `LLMTradeEvaluation` with `quantity`, `notional_value`, `sizing_tiers` and `stop/target distances`; insert an EXECUTED `SignalRecord` for `BBB` with `notional_value=20_000` via `temp_db` as `test_scan_shadow_ranker.py` already imports `SignalRecord`; set `app_config.book_sizing.mode` and a tiny `max_portfolio_daily_vol_pct` to force `factor < 1`; the sizer's `bar_source` is a `MagicMock` whose `fetch_daily_many` returns GBM frames for symbols not in the scan):

  - `test_preview_leaves_eval_res_identical` — recorded signal's `quantity`/`raw_response` equal to the `off` run; provenance `book_sizing.status == "unchanged"`, `would_scale True`, `factor < 1`.
  - `test_enforce_scales_the_card_and_journals_applied` — recorded `quantity` equals `floor(q × factor)`; `book_sizing.status == "applied"`; notification payload `book_sizing` present; journal candidate `book_sizing.quantity_after < quantity_before`.
  - `test_enforce_blocks_and_falls_through` — budget so small the default tier vanishes: rank 1 outcome `book_sizing_blocked`, rank 2 sent, scan/session budget charged once.
  - `test_later_card_sees_scaled_book` — two cards in one scan (`max_cards_per_scan=2`): the second decision's `vol_before_pct` equals the first's `vol_after_scaled_pct` (within 1e-9).
  - `test_unavailable_never_blocks` — fetch raises → every card `unavailable`, all sent as before.
  - `test_dry_run_is_not_applicable` and `test_off_mode_adds_no_keys_but_null_block` (provenance `book_sizing.status == "not_applicable"` with mode `off`).
  - `test_old_payload_renders_without_book_line` (notifier test: `format_alert_card(...)` without `book_sizing` has no `📐`).
  - `tests/notifier/test_book_line.py`: the three line shapes of spec D6 (applied/preview/unavailable) for both renderers.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

  - Constructor (`copilot.py` near `self.card_stats` ~274): `self.book_sizer = book_sizer if book_sizer is not None else BookSizer(config.book_sizing, bar_source, portfolio_cash=config.portfolio.cash, min_units=config.sizing.min_shares)` with new optional constructor kwargs `book_sizer=None, bar_source=None`; when `bar_source is None` and mode is not `off`, build it lazily with `build_bar_source(self.config)` inside `BookSizer.prepare`'s first fetch (guarded; a dry run never fetches because `prepare` is skipped under `dry_run`).
  - `run_scan`: after `ranked` is final and before the send loop: `book_context = await self.book_sizer.prepare(active_positions, datasets, as_of=self.session_start_et(decided_at).date(), candidates=[c.contract for c, _, _ in ranked[:native_count]]) if not dry_run else None`; `book_by_rank: list[BookSizingDecision | None] = [None] * len(ranked)`.
  - In the send loop, right after `eval_res = await self.evaluator.evaluate_candidate(...)` and its existing approval check (only for approved native, non-drift candidates):

```python
                    risk_capital_now = float(account_risk.equity) if account_risk else float(self.config.portfolio.cash)
                    book_decision, sized = self.book_sizer.decide(
                        book_context, candidate=candidate, eval_res=eval_res, risk_capital=risk_capital_now, dry_run=dry_run
                    )
                    book_by_rank[rank - 1] = book_decision
                    if sized is None:
                        outcomes[rank - 1] = RankedOutcome.BOOK_SIZING_BLOCKED
                        summary["runners_up"].append(self._runner_up(candidate, book_decision.reason or "book sizing"))
                        continue
                    eval_res = sized
```

    (`decide` must be pure/synchronous; `book_context` None → `decide` returns `NOT_APPLICABLE`.) The book-sizing step runs **after** the LLM, so the LLM budget was spent for a blocked card while the scan/session card budgets were not — document in the method comment.
  - Provenance: `"book_sizing": book_decision.model_dump(mode="json")`; notification: `"book_sizing": book_decision.model_dump(mode="json")`; book join uses `eval_res` (already the sized one); `_journal_scan_ranking` gains `book_by_rank` and each candidate dict gets `"book_sizing": BookSizer.journal_block(book_by_rank[rank - 1])`.
  - `_replacement_card` (tap-time re-pricing) passes the stored `book_sizing` from the original notification payload through unchanged (read how it carries `card_evidence`, ~3057-3170).
  - `telegram_bot.py`: `_book_line(book_sizing: dict | None, *, markup: bool) -> str` returning `""` for None/`not_applicable`; otherwise the D6 text (percentages `:.2%`; factor `×{factor:.2f}`; preview with factor < 1 appends ` — preview, size unchanged`; unavailable: `📐 Book: portfolio vol unavailable (covariance: {reason})`). Render directly under the evidence block in both renderers; `send_signal_alert(..., book_sizing: dict[str, Any] | None = None)` forwards it.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/agent tests/notifier tests/execution tests/workflows tests/cli -q` → PASS (fix any test asserting the exact provenance/notification key set). **Step 5: Commit** ("Scan: book-aware sizing at the L3 seam — decision in provenance, payload, journal; card line; book_sizing_blocked fall-through").

---

### Task 6: Outcomes block, docs, full suite

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py` (`_COLUMNS` + row builder: `book_sizing_status`, `book_factor`, `book_would_scale`; `summarize`: `"book_sizing"` block), `agentic_trader/cli/commands/cards.py` only if it prints the summary keys explicitly
- Modify docs: `docs/card-evidence.md` (new "Book-aware sizing" section: rule, inputs, modes, line, limitations: raw bars/no corporate actions, equities only, repeated setups, LLM saw pre-scale size), `docs/risk-policy.md` (rule table row + "applied at construction only" note), `docs/production.md` (config block, card line, `cards outcomes` block, verification steps), `CLAUDE.md` contract 1 (one clause: "Book-aware sizing (`book_sizing`, default `preview`) scales native equity cards by a closed-form factor ≤ 1 against a daily dollar-vol budget at card construction only; the shadow optimiser cross-check never reaches size or orders."), `docs/alpha-roadmap.md` item 1 status (PR B shipped in preview; enforce is an operator decision after reading factors)
- Test: `tests/research/setups/test_outcomes.py` (append)

- [ ] **Step 1: Failing outcomes tests**: events whose candidates carry `book_sizing` blocks (applied/unchanged with `would_scale`/unavailable/None for older events) → frame columns present (None for older), `summarize()["book_sizing"] == {"by_status": {...counts...}, "would_scale": n, "mean_factor_scaled": float|None, "mean_r_cost": {"would_scale": ..., "not_scaled": ...}}`; empty frame → zeros/None with the same shape.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the columns, row fields (`entry.get("book_sizing") or {}`), and the block (`would_scale` rows = `book_would_scale is True` or status `applied`).
- [ ] **Step 4: Docs** as listed; keep statements factual to the shipped defaults (preview).
- [ ] **Step 5: Full suite and pre-commit**: `env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider` (record counts) and `env -u VIRTUAL_ENV uv run pre-commit run --all-files`.
- [ ] **Step 6: Commit** ("Outcomes and docs: book_sizing block in cards outcomes; card-evidence, risk-policy, production, CLAUDE.md contract 1, roadmap item 1").
