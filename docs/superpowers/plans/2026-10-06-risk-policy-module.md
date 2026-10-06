# Risk-policy module (L2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One implementation per entry-risk rule in the `agentic_trader/risk` package, with typed inputs and results, called by every layer that checks a card (evaluator, sizing, tap gate, re-pricing, admission, preflight).

**Architecture:** `agentic_trader/risk.py` becomes a package: `limits.py` (a frozen snapshot of every limit the rules read), `book.py` (typed positions/reservations/cards), `capital.py` (capital, drawdown, macro and the one per-trade budget formula), `rules.py` (one pure function per rule plus two fixed-order composites). Consumers build a `RiskLimits`, a `Book`, an `EntryIntent` and a `RiskBudget` and call the composites; I/O (calendar, session provider, regime, DB) stays in the callers.

**Tech Stack:** Python 3.14, `uv`, dataclasses, pydantic (existing models), numpy (adversarial generator), pytest (`asyncio_mode=auto`), temp SQLite fixtures; PostgreSQL integration for the final gate.

**Spec:** `docs/superpowers/specs/2026-10-06-risk-policy-module-design.md` (decisions table is binding). Survey of today's sites: `.superpowers/sdd/2026-10-06-risk-policy-module/survey.md`.

Run every command from `/Users/adamhadani/Development/agentic-trader-risk-policy` with `env -u VIRTUAL_ENV uv run …`.

## Global Constraints

- Approved behaviour changes, and only these: (3) deterministic target at `max(config, regime)` reward/risk; (5) scan-time concurrent-positions and aggregate-stop-risk pre-checks; (7) `enforce_rth=False` allows extended hours at every entry gate; (1) no fallback to `DEFAULT_CORRELATION_GROUPS` when `correlation_groups` is empty; (2) macro factor applies to every sizing tier; (6) macro lockout at admission on both broker branches; (4) FX and unconfigured classes explicitly uncapped. Anything else that changes a live decision is a defect.
- No new config key, no Alembic migration (head `008_alpha_pipeline`), no new Telegram message.
- Rules never read `AppConfig`; they read `RiskLimits`. Rule functions never raise on valid typed inputs; `EntryIntent`/`BookPosition` constructors raise `ValueError` on non-finite or negative numbers; `risk_capital`/`macro_risk_factor` raise on non-finite inputs.
- The public import path is `agentic_trader.risk` only. Update every internal caller to the canonical names; no compatibility aliases, no re-export of moved functions from their old modules (`execution/freshness.py` imports `meets_min_reward_risk` from `agentic_trader.risk`).
- `reservation_rejection(request, positions, config, *, current_drawdown_pct, current_equity, current_price) -> str | None` keeps its signature.
- Tests pinning old reason strings are updated to the new strings or, where the API exposes it, to `RiskRule` ids; no assertion is deleted.
- TDD per task: failing test first (RED recorded), then code (GREEN), then the regression gate before committing: `env -u VIRTUAL_ENV uv run pytest -q tests/risk tests/agent tests/execution tests/workflows tests/storage tests/cli tests/notifier tests/broker tests/market`.
- Docstrings and comments describe the final behaviour and name the rule they delegate to; none describes pre-L2 behaviour.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **A row with `notional_value` or `risk_dollars` `None` in the scan's book** must reject with `exposure_unknown`, not crash or silently pass (admission already behaves so) — pinned in Task 2 (`exposure_known`) and Task 4 (evaluator uses `book_gates`).
2. **A correlation group containing `/MES` while the book holds `MES`** must count as the same instrument at both layers — pinned in Task 2 (`normalize_symbol` table) and Task 6 scenario (c).
3. **A regime multiplier of 0 or NaN at tap time** must not raise inside `_regime_gate`; NaN → `ValueError` is caught by the tap's existing exception path and reported as "checks failed" — pinned in Task 2 (`macro_risk_factor`) and Task 5 test.
4. **`current_price` None at admission** must price notional at `entry` — pinned in Task 2 (`EntryIntent.exposure_price`) and Task 3.
5. **A card built under an elevated regime** must pass its own tap gate on reward/risk — pinned in Task 6 scenario (a).

---

### Task 1: The `agentic_trader/risk` package — limits, book, capital

**Files:**
- Delete: `agentic_trader/risk.py` (contents move)
- Create: `agentic_trader/risk/__init__.py`, `agentic_trader/risk/limits.py`, `agentic_trader/risk/book.py`, `agentic_trader/risk/capital.py`
- Modify: `agentic_trader/cli/commands/db.py` (queue default), `agentic_trader/agent/position_sizing.py:14` (stale import)
- Test: `tests/risk/__init__.py`, `tests/risk/test_limits.py`, `tests/risk/test_book.py`, `tests/risk/test_capital.py`

**Interfaces:**
- Produces: `RiskLimits.from_config`, `normalize_symbol`, `BookPosition`, `Book`, `RiskBudget`, `per_trade_risk_budget`, `macro_risk_factor`, plus the moved `requires_account_risk`, `risk_capital`, `drawdown_risk_factor`, all importable from `agentic_trader.risk`.
- Consumers today of `agentic_trader.risk`: `agent/copilot.py:106`, `agent/position_sizing.py:9`, `execution/admission.py:11`, `execution/capacity.py:30`, `execution/entries.py:25`, `storage/workflow.py:42` — they keep working because the package exposes the same names.

- [ ] **Step 1: Write the failing tests**

`tests/risk/test_limits.py`:

```python
from types import MappingProxyType

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import RiskLimits, normalize_symbol


@pytest.mark.parametrize(("raw", "key"), [("/MES", "MES"), ("mes", "MES"), (" SPY ", "SPY"), ("BTC/USD", "BTC/USD")])
def test_normalize_symbol(raw, key):
    assert normalize_symbol(raw) == key


def test_limits_snapshot_normalises_groups_and_caps():
    config = AppConfig()
    config.portfolio.correlation_groups = {"index": ["/MES", "spy"], "empty": []}
    config.portfolio.max_crypto_exposure = None
    limits = RiskLimits.from_config(config)
    assert limits.correlation_groups == {"index": frozenset({"MES", "SPY"}), "empty": frozenset()}
    assert isinstance(limits.correlation_groups, MappingProxyType)
    assert set(limits.asset_class_caps) == {"EQUITY", "FUTURES"}  # crypto uncapped when None; FX never configured
    assert limits.cash == config.portfolio.cash and limits.max_risk_pct_cap == config.sizing.max_risk_pct_cap
    assert limits.min_risk_reward_ratio == config.risk.min_risk_reward_ratio
    assert (limits.lockout_pre_minutes, limits.lockout_post_minutes) == (
        config.risk.lockout_pre_event_minutes,
        config.risk.lockout_post_event_minutes,
    )
    assert limits.earnings_blackout_days == config.risk.earnings_blackout_days
    assert limits.enforce_rth is config.session.enforce_rth


def test_empty_groups_mean_disabled_not_default():
    config = AppConfig()
    config.portfolio.correlation_groups = {}
    assert RiskLimits.from_config(config).correlation_groups == {}
```

`tests/risk/test_book.py`:

```python
import math

import pytest

from agentic_trader.risk import Book, BookPosition


def test_position_validity():
    assert BookPosition("AAPL", "LONG", "EQUITY", 1000.0, 50.0).valid
    assert not BookPosition("AAPL", "LONG", "EQUITY", None, 50.0).valid
    assert not BookPosition("AAPL", "LONG", "EQUITY", 1000.0, math.nan).valid
    assert not BookPosition("AAPL", "LONG", "EQUITY", -1.0, 50.0).valid
    assert BookPosition("/MES", "LONG", "FUTURES", 1.0, 1.0).key == "MES"


def test_from_signal_rows_normalises_and_flags_reservations():
    rows = [
        {
            "contract": "aapl",
            "direction": "long",
            "asset_class": "equity",
            "notional_value": 1000,
            "risk_dollars": 50,
            "status": "EXECUTED",
        },
        {
            "symbol": "/MES",
            "direction": "SHORT",
            "asset_class": "FUTURES",
            "notional_value": "2500.5",
            "risk_dollars": None,
            "status": "SUBMITTING",
        },
    ]
    book = Book.from_signal_rows(rows, reservations=True)
    assert [p.key for p in book.positions] == ["AAPL", "MES"]
    assert book.positions[0].direction == "LONG" and book.positions[0].asset_class == "EQUITY"
    assert book.positions[1].reservation is True and book.positions[0].reservation is False
    assert book.positions[1].notional == 2500.5 and book.positions[1].planned_risk is None
    assert [p.key for p in book.invalid] == ["MES"]
    assert Book.from_signal_rows(rows, reservations=False).positions[1].reservation is False


def test_book_aggregates():
    book = Book.from_signal_rows(
        [
            {
                "contract": "AAPL",
                "direction": "LONG",
                "asset_class": "EQUITY",
                "notional_value": 1000,
                "risk_dollars": 50,
            },
            {
                "contract": "MSFT",
                "direction": "SHORT",
                "asset_class": "EQUITY",
                "notional_value": 500,
                "risk_dollars": 25,
            },
            {
                "contract": "/MES",
                "direction": "LONG",
                "asset_class": "FUTURES",
                "notional_value": 2000,
                "risk_dollars": 100,
            },
        ],
        reservations=False,
    )
    assert book.count == 3
    assert book.notional() == 3500 and book.notional_for("EQUITY") == 1500 and book.planned_risk() == 175
    assert book.holds("mes") and not book.holds("TSLA")
    assert [p.key for p in book.same_direction_in(frozenset({"AAPL", "MSFT", "MES"}), "LONG")] == ["AAPL", "MES"]
    extended = book.with_position(BookPosition("TSLA", "LONG", "EQUITY", 10.0, 1.0))
    assert extended.count == 4 and book.count == 3


def test_invalid_numbers_are_not_coerced_to_zero():
    book = Book.from_signal_rows(
        [{"contract": "X", "direction": "LONG", "asset_class": "EQUITY", "notional_value": "abc", "risk_dollars": 1}],
        reservations=False,
    )
    assert book.positions[0].notional is None and not book.positions[0].valid
```

`tests/risk/test_capital.py`:

```python
import math

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import RiskLimits, macro_risk_factor, per_trade_risk_budget


@pytest.mark.parametrize(("multiplier", "factor"), [(1.0, 1.0), (1.5, 1.0), (0.5, 0.5), (0.0, 0.10), (-3.0, 0.10)])
def test_macro_factor_clamps_down_only(multiplier, factor):
    assert macro_risk_factor(multiplier) == factor


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_macro_factor_rejects_non_finite(bad):
    with pytest.raises(ValueError):
        macro_risk_factor(bad)


def test_per_trade_budget_is_one_formula():
    config = AppConfig()
    config.portfolio.cash = 100_000.0
    config.sizing.max_risk_pct_cap = 0.01
    limits = RiskLimits.from_config(config)
    plain = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0)
    assert (plain.capital, plain.drawdown_factor, plain.macro_factor, plain.dollars) == (100_000.0, 1.0, 1.0, 1_000.0)
    # Observed equity below the mandate lowers capital; drawdown and macro scale the budget.
    stressed = per_trade_risk_budget(limits, equity=80_000.0, drawdown_pct=0.045, macro_multiplier=0.5)
    assert stressed.capital == 80_000.0
    assert 0.10 <= stressed.drawdown_factor < 1.0 and stressed.macro_factor == 0.5
    assert stressed.dollars == pytest.approx(80_000.0 * 0.01 * stressed.drawdown_factor * 0.5)
    assert stressed.drawdown_pct == 0.045
    halted = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.06)
    assert halted.drawdown_factor == 0.0 and halted.dollars == 0.0


def test_moved_functions_are_importable_from_the_package():
    from agentic_trader.risk import drawdown_risk_factor, requires_account_risk, risk_capital

    assert risk_capital(100.0, 50.0) == 50.0
    assert requires_account_risk(AppConfig()) in (True, False)
    assert drawdown_risk_factor(0.0, AppConfig().sizing) == 1.0
```

Also pin the `db queue` fix in `tests/cli/test_cli_smoke.py` (append):

```python
def test_db_queue_default_kind_is_a_valid_choice(runner: CliRunner):
    result = runner.invoke(cli, ["db", "queue"])
    assert "is not one of" not in result.output, result.output
```

(Expected to exercise only argument parsing before the DB call; if the command reaches the DB in this fixture and fails for another reason, assert on the absence of the `BadParameter` text only.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/risk tests/cli/test_cli_smoke.py -k "limits or book or capital or queue"`
Expected: FAIL — `ImportError` (no package), and the queue test prints `is not one of 'entry', 'entry_cancel'`.

- [ ] **Step 3: Implement**

`agentic_trader/risk/limits.py`:

```python
"""A frozen snapshot of every limit the risk rules read.

Rules never read ``AppConfig``: they read this object, so a rule's inputs are visible in
its signature and a test can build limits without a whole config.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.constants import AssetClass

__all__ = ["RiskLimits", "normalize_symbol"]


def normalize_symbol(symbol: str) -> str:
    """``/MES``, ``mes`` and ``MES`` are one instrument for every risk rule."""
    return str(symbol).strip().lstrip("/").upper()


@dataclass(frozen=True)
class RiskLimits:
    cash: float
    sizing: PositionSizingConfig
    max_risk_pct_cap: float
    max_trade_notional_cap: float
    max_shares_per_trade: int
    max_contracts_per_trade: int
    max_stop_risk_pct: float
    max_notional_exposure: float
    asset_class_caps: Mapping[str, float]  # a class absent here is uncapped (FX, or a cap configured as None)
    max_concurrent_positions: int
    max_correlated_positions: int
    correlation_groups: Mapping[str, frozenset[str]]  # normalised symbols; empty mapping disables the rule
    min_risk_reward_ratio: float
    lockout_pre_minutes: int
    lockout_post_minutes: int
    earnings_blackout_days: int
    enforce_rth: bool

    @classmethod
    def from_config(cls, config: AppConfig) -> RiskLimits:
        portfolio, sizing, risk = config.portfolio, config.sizing, config.risk
        raw_caps = {
            str(AssetClass.EQUITY): getattr(portfolio, "max_equity_exposure", None),
            str(AssetClass.FUTURES): getattr(portfolio, "max_futures_exposure", None),
            str(AssetClass.CRYPTO): getattr(portfolio, "max_crypto_exposure", None),
        }
        groups = {
            name: frozenset(normalize_symbol(member) for member in members)
            for name, members in (portfolio.correlation_groups or {}).items()
        }
        return cls(
            cash=float(portfolio.cash),
            sizing=sizing,
            max_risk_pct_cap=float(sizing.max_risk_pct_cap),
            max_trade_notional_cap=float(sizing.max_trade_notional_cap),
            max_shares_per_trade=int(sizing.max_shares_per_trade),
            max_contracts_per_trade=int(sizing.max_contracts_per_trade),
            max_stop_risk_pct=float(portfolio.max_stop_risk_pct),
            max_notional_exposure=float(portfolio.max_notional_exposure),
            asset_class_caps=MappingProxyType({k: float(v) for k, v in raw_caps.items() if v is not None}),
            max_concurrent_positions=int(portfolio.max_concurrent_positions),
            max_correlated_positions=int(portfolio.max_correlated_positions),
            correlation_groups=MappingProxyType(groups),
            min_risk_reward_ratio=float(risk.min_risk_reward_ratio),
            lockout_pre_minutes=int(risk.lockout_pre_event_minutes),
            lockout_post_minutes=int(risk.lockout_post_event_minutes),
            earnings_blackout_days=int(risk.earnings_blackout_days),
            enforce_rth=bool(getattr(config.session, "enforce_rth", True)),
        )
```

Check how `AssetClass` renders with `str(...)`; the evaluator compares `str(asset_class).upper()`, so use whatever form yields `"EQUITY"`; if `str(AssetClass.EQUITY)` is `"AssetClass.EQUITY"`, use `.value`. The keys must be the upper-case class names.

`agentic_trader/risk/book.py`:

```python
"""Typed view of what the desk already holds: open positions, reservations, this scan's cards.

Each layer builds the book it is responsible for (the scan: open positions plus cards sent
earlier in the scan; admission: open positions plus queued/checking/submitting
reservations). The rules only see this type.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agentic_trader.risk.limits import normalize_symbol

__all__ = ["Book", "BookPosition"]

RESERVATION_STATUS = "SUBMITTING"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


@dataclass(frozen=True)
class BookPosition:
    symbol: str
    direction: str
    asset_class: str
    notional: float | None
    planned_risk: float | None
    reservation: bool = False

    @property
    def key(self) -> str:
        return normalize_symbol(self.symbol)

    @property
    def valid(self) -> bool:
        """Exposure is known: both numbers present, finite and non-negative."""
        return all(v is not None and math.isfinite(v) and v >= 0 for v in (self.notional, self.planned_risk))


@dataclass(frozen=True)
class Book:
    positions: tuple[BookPosition, ...] = ()

    @classmethod
    def from_signal_rows(cls, rows: Iterable[Mapping[str, Any]], *, reservations: bool) -> Book:
        """Build from ``signals`` row dicts (``to_dict()`` shape or the scan's in-run dicts).

        ``reservations=True`` marks ``SUBMITTING`` rows as reservations; the admission
        layer passes True, the scan passes False (it never sees reservations).
        """
        positions = []
        for row in rows:
            status = str(row.get("status") or "").upper()
            positions.append(
                BookPosition(
                    symbol=str(row.get("contract") or row.get("symbol") or ""),
                    direction=str(row.get("direction") or "").upper(),
                    asset_class=str(row.get("asset_class") or "").upper(),
                    notional=_number(row.get("notional_value")),
                    planned_risk=_number(row.get("risk_dollars")),
                    reservation=reservations and status == RESERVATION_STATUS,
                )
            )
        return cls(tuple(positions))

    def with_position(self, position: BookPosition) -> Book:
        return Book((*self.positions, position))

    @property
    def count(self) -> int:
        return len(self.positions)

    @property
    def invalid(self) -> tuple[BookPosition, ...]:
        return tuple(p for p in self.positions if not p.valid)

    def notional(self) -> float:
        return sum(p.notional or 0.0 for p in self.positions)

    def notional_for(self, asset_class: str) -> float:
        wanted = str(asset_class).upper()
        return sum(p.notional or 0.0 for p in self.positions if p.asset_class == wanted)

    def planned_risk(self) -> float:
        return sum(p.planned_risk or 0.0 for p in self.positions)

    def holds(self, symbol: str) -> bool:
        key = normalize_symbol(symbol)
        return any(p.key == key for p in self.positions)

    def same_direction_in(self, keys: frozenset[str], direction: str) -> tuple[BookPosition, ...]:
        wanted = str(direction).upper()
        return tuple(p for p in self.positions if p.key in keys and p.direction == wanted)
```

`agentic_trader/risk/capital.py`: move the three functions from `risk.py` verbatim (keep their docstrings and `ValueError`s), then add:

```python
MACRO_FACTOR_FLOOR = 0.10


def macro_risk_factor(multiplier: float) -> float:
    """Clamp a regime risk multiplier to ``[MACRO_FACTOR_FLOOR, 1.0]``: it scales risk down, never up."""
    value = float(multiplier)
    if not math.isfinite(value):
        raise ValueError("Macro risk multiplier must be finite")
    return max(MACRO_FACTOR_FLOOR, min(1.0, value))


@dataclass(frozen=True)
class RiskBudget:
    """The per-trade risk budget every layer shares (spec decision 2)."""

    capital: float
    drawdown_pct: float
    drawdown_factor: float
    macro_factor: float
    dollars: float


def per_trade_risk_budget(
    limits: RiskLimits, *, equity: float | None, drawdown_pct: float, macro_multiplier: float = 1.0
) -> RiskBudget:
    """``min(cash, equity) × max_risk_pct_cap × drawdown_factor × macro_factor``."""
    capital = risk_capital(limits.cash, equity)
    drawdown = drawdown_risk_factor(drawdown_pct, limits.sizing)
    macro = macro_risk_factor(macro_multiplier)
    return RiskBudget(
        capital=capital,
        drawdown_pct=float(drawdown_pct),
        drawdown_factor=drawdown,
        macro_factor=macro,
        dollars=capital * limits.max_risk_pct_cap * drawdown * macro,
    )
```

`agentic_trader/risk/__init__.py` re-exports: `RiskLimits, normalize_symbol, Book, BookPosition, RiskBudget, per_trade_risk_budget, macro_risk_factor, requires_account_risk, risk_capital, drawdown_risk_factor` with `__all__`. (Task 2 adds the rules.) Delete `agentic_trader/risk.py` with `git rm`.

`cli/commands/db.py`: `default=WorkKind.ENTRY` → `default=str(WorkKind.ENTRY)`. `agent/position_sizing.py`: remove the `from agentic_trader.scanner.models import ScreenerCandidate` line under `TYPE_CHECKING` and import `ScreenerCandidate` from `agentic_trader.screeners.base` there instead (type-only).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/risk tests/cli/test_cli_smoke.py tests/execution/test_position_sizing.py tests/execution/test_drawdown_policy.py`
Expected: PASS.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add -A agentic_trader/risk agentic_trader/cli/commands/db.py agentic_trader/agent/position_sizing.py tests/risk tests/cli/test_cli_smoke.py
git rm -q agentic_trader/risk.py
git commit -m "risk: package with RiskLimits, Book and the one per-trade budget formula; fix db queue default

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The rules, the composites, the adversarial and consistency suites

**Files:**
- Create: `agentic_trader/risk/rules.py`
- Modify: `agentic_trader/risk/__init__.py` (exports), `agentic_trader/execution/freshness.py` (import `meets_min_reward_risk` from the package; delete its local definition)
- Test: `tests/risk/test_rules.py`, `tests/risk/test_adversarial.py`, `tests/risk/test_consistency.py`

**Interfaces:**
- Produces: `RiskRule`, `Rejection`, `EntryIntent`, every rule function, `required_reward_risk`, `meets_min_reward_risk`, `in_lockout_window`, `earnings_days_out`, `entry_session_open`, `book_gates`, `admission_gates` (signatures as in the spec).
- Reason texts (one place, used by every layer):
  - `exposure_unknown`: "Existing exposure is unknown or invalid; reconcile it before new risk."
  - `drawdown_halt`: f"Account drawdown {dd:.1%} reaches the configured sizing halt; new entries blocked."
  - `per_trade_risk`: "Order exceeds the drawdown- and macro-adjusted per-trade risk cap; request a fresh scan for smaller sizing." when `budget.drawdown_factor < 1 or budget.macro_factor < 1`, else "Order exceeds the configured per-trade risk cap."
  - `per_trade_notional`: "Order exceeds the configured per-trade notional cap."
  - `quantity_cap`: "Order exceeds the configured quantity cap."
  - `reward_risk`: f"Reward/risk {rr:.2f} is below the required {required:.2f}." (`rr` is `reward/risk` rounded to two decimals, or 0.00 when `risk <= 0`)
  - `aggregate_stop_risk`: "Order would breach the aggregate planned stop-risk budget."
  - `concurrent_positions`: f"Maximum concurrent positions ({limit}) reached."
  - `same_symbol`: "Symbol already has a position or entry reservation; adding/netting requires a separate reviewed plan."
  - `portfolio_notional`: f"Portfolio notional would reach ${total:,.0f}, above the ${cap:,.0f} ceiling."
  - `asset_class_notional`: f"{asset_class} notional would reach ${total:,.0f}, above the ${cap:,.0f} ceiling."
  - `correlation_group`: f"Correlation group '{group}' already has {n} {direction} position(s) ({symbols}); max {limit}."
  - `macro_lockout`: f"Macro event lockout: {title} at {HH:MM} UTC."
  - `session_closed`: f"Market session closed{(': ' + detail) if detail else ''}."
  - `session_not_rth`: "Outside regular trading hours."
  - `regime_breakout`: "Volatility/macro policy suppresses breakout entries."

- [ ] **Step 1: Write the failing tests**

`tests/risk/test_rules.py` (table-driven; one `parametrize` per rule). Fixtures at the top:

```python
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import (
    Book,
    BookPosition,
    EntryIntent,
    Rejection,
    RiskLimits,
    RiskRule,
    admission_gates,
    aggregate_stop_risk,
    asset_class_notional,
    book_gates,
    concurrent_positions,
    correlation_group,
    drawdown_halt,
    earnings_days_out,
    entry_session_open,
    exposure_known,
    in_lockout_window,
    macro_lockout,
    meets_min_reward_risk,
    per_trade_notional,
    per_trade_risk,
    per_trade_risk_budget,
    portfolio_notional,
    quantity_cap,
    regime_breakout,
    required_reward_risk,
    reward_risk,
    same_symbol,
)


def limits(**overrides) -> RiskLimits:
    config = AppConfig()
    config.portfolio.cash = 100_000.0
    config.portfolio.max_notional_exposure = 60_000.0
    config.portfolio.max_equity_exposure = 40_000.0
    config.portfolio.max_concurrent_positions = 4
    config.portfolio.max_correlated_positions = 1
    config.portfolio.max_stop_risk_pct = 0.02
    config.portfolio.correlation_groups = {"index": ["/MES", "SPY"], "tech": ["AAPL", "MSFT"]}
    config.sizing.max_risk_pct_cap = 0.01
    config.sizing.max_trade_notional_cap = 30_000.0
    config.sizing.max_shares_per_trade = 500
    config.sizing.max_contracts_per_trade = 4
    config.risk.min_risk_reward_ratio = 2.0
    for key, value in overrides.items():
        section, _, name = key.partition("__")
        setattr(getattr(config, section), name, value)
    return RiskLimits.from_config(config)


def intent(**overrides) -> EntryIntent:
    base = dict(
        symbol="AAPL", direction="LONG", asset_class="EQUITY", quantity=10.0, entry=100.0, stop=99.0, target=102.0
    )
    return EntryIntent(**{**base, **overrides})


def position(symbol="MSFT", direction="LONG", asset_class="EQUITY", notional=1000.0, risk=50.0, reservation=False):
    return BookPosition(symbol, direction, asset_class, notional, risk, reservation)


BUDGET = per_trade_risk_budget(limits(), equity=None, drawdown_pct=0.0)
```

Then the tables (each case names the expected `RiskRule` or `None`):

```python
def test_intent_derived_values_and_validation():
    i = intent(current_price=101.0, multiplier=2.0)
    assert (i.key, i.risk_distance, i.reward_distance) == ("AAPL", 1.0, 2.0)
    assert i.risk_dollars == 20.0 and i.exposure_price == 101.0 and i.notional == 2020.0
    assert intent(direction="SHORT", stop=101.0, target=98.0).risk_distance == 1.0
    assert intent(current_price=None).exposure_price == 100.0
    for bad in (
        {"quantity": 0.0},
        {"entry": math.nan},
        {"stop": -1.0},
        {"multiplier": math.inf},
        {"direction": "FLAT"},
    ):
        with pytest.raises(ValueError):
            intent(**bad)


def test_exposure_known_rejects_any_invalid_position():
    assert exposure_known(Book((position(),))) is None
    rejection = exposure_known(Book((position(), position("X", notional=None))))
    assert rejection is not None and rejection.rule is RiskRule.EXPOSURE_UNKNOWN


def test_drawdown_halt_only_at_zero_factor():
    assert drawdown_halt(BUDGET) is None
    halted = per_trade_risk_budget(limits(), equity=None, drawdown_pct=0.06)
    assert drawdown_halt(halted).rule is RiskRule.DRAWDOWN_HALT and "6.0%" in drawdown_halt(halted).reason


@pytest.mark.parametrize(
    ("quantity", "macro", "expected"),
    [
        (999.0, 1.0, None),
        (1000.0, 1.0, None),
        (1001.0, 1.0, RiskRule.PER_TRADE_RISK),
        (600.0, 0.5, RiskRule.PER_TRADE_RISK),
        (500.0, 0.5, None),
    ],
)
def test_per_trade_risk_against_the_budget(quantity, macro, expected):
    # risk per share 1.0; budget 1,000 at full macro, 500 at macro 0.5; equal to the budget passes
    budget = per_trade_risk_budget(limits(), equity=None, drawdown_pct=0.0, macro_multiplier=macro)
    result = per_trade_risk(intent(quantity=quantity, stop=99.0), budget)
    assert (result.rule if result else None) == expected
    if result and macro < 1:
        assert "macro-adjusted" in result.reason


@pytest.mark.parametrize(
    ("quantity", "price", "expected"),
    [(300.0, None, None), (301.0, None, RiskRule.PER_TRADE_NOTIONAL), (299.0, 101.0, RiskRule.PER_TRADE_NOTIONAL)],
)
def test_per_trade_notional_uses_the_exposure_price(quantity, price, expected):
    result = per_trade_notional(intent(quantity=quantity, current_price=price), limits())
    assert (result.rule if result else None) == expected


@pytest.mark.parametrize(
    ("asset_class", "quantity", "expected"),
    [
        ("EQUITY", 500.0, None),
        ("EQUITY", 501.0, RiskRule.QUANTITY_CAP),
        ("FUTURES", 4.0, None),
        ("FUTURES", 5.0, RiskRule.QUANTITY_CAP),
    ],
)
def test_quantity_cap_by_asset_class(asset_class, quantity, expected):
    result = quantity_cap(intent(asset_class=asset_class, quantity=quantity, stop=99.0, target=1000.0), limits())
    assert (result.rule if result else None) == expected


def test_required_reward_risk_is_the_max_of_config_and_regime():
    assert required_reward_risk(limits(), None) == 2.0
    assert required_reward_risk(limits(), 2.2) == 2.2
    assert required_reward_risk(limits(), 1.5) == 2.0


@pytest.mark.parametrize(
    ("target", "required", "expected"),
    [(102.0, 2.0, None), (101.99, 2.0, RiskRule.REWARD_RISK), (102.0, 2.2, RiskRule.REWARD_RISK), (102.2, 2.2, None)],
)
def test_reward_risk_rounds_like_the_evaluator(target, required, expected):
    result = reward_risk(intent(target=target), required)
    assert (result.rule if result else None) == expected
    assert meets_min_reward_risk(2.0, 0.0, 2.0) is False  # degenerate bracket never passes


def test_inverted_bracket_is_a_reward_risk_rejection_not_an_exception():
    assert reward_risk(intent(stop=101.0, target=102.0), 2.0).rule is RiskRule.REWARD_RISK


@pytest.mark.parametrize(
    ("existing", "quantity", "expected"), [(1500.0, 500.0, None), (1500.0, 501.0, RiskRule.AGGREGATE_STOP_RISK)]
)
def test_aggregate_stop_risk_counts_the_whole_book(existing, quantity, expected):
    # budget 100,000 × 2% = 2,000; intent risk = quantity × 1.0
    book = Book((position(risk=existing),))
    result = aggregate_stop_risk(intent(quantity=quantity, target=200.0), book, BUDGET, limits())
    assert (result.rule if result else None) == expected


def test_concurrent_positions_counts_reservations_too():
    full = Book(tuple(position(f"S{i}", reservation=i % 2 == 0) for i in range(4)))
    assert concurrent_positions(full, limits()).rule is RiskRule.CONCURRENT_POSITIONS
    assert concurrent_positions(Book(full.positions[:3]), limits()) is None


def test_same_symbol_is_normalised():
    assert same_symbol(intent(symbol="/MES"), Book((position("MES"),))).rule is RiskRule.SAME_SYMBOL
    assert same_symbol(intent(), Book((position("MSFT"),))) is None


@pytest.mark.parametrize(
    ("book_notional", "quantity", "expected"), [(59_000.0, 10.0, None), (59_000.0, 11.0, RiskRule.PORTFOLIO_NOTIONAL)]
)
def test_portfolio_notional(book_notional, quantity, expected):
    result = portfolio_notional(intent(quantity=quantity), Book((position(notional=book_notional),)), limits())
    assert (result.rule if result else None) == expected


def test_asset_class_notional_and_uncapped_classes():
    equity_book = Book((position(notional=39_500.0),))
    assert asset_class_notional(intent(quantity=5.0), equity_book, limits()) is None
    assert asset_class_notional(intent(quantity=6.0), equity_book, limits()).rule is RiskRule.ASSET_CLASS_NOTIONAL
    fx = intent(symbol="EURUSD", asset_class="FX", quantity=100_000.0, target=200.0)
    assert asset_class_notional(fx, Book(), limits()) is None  # no cap configured → uncapped, never KeyError
    assert (
        asset_class_notional(
            intent(asset_class="CRYPTO", quantity=1.0), Book(), limits(portfolio__max_crypto_exposure=None)
        )
        is None
    )


@pytest.mark.parametrize(
    ("book", "expected"),
    [
        (Book((position("MSFT", "LONG"),)), RiskRule.CORRELATION_GROUP),  # same group, same direction
        (Book((position("MSFT", "SHORT"),)), None),  # same group, opposite direction
        (Book((position("msft", "LONG"),)), RiskRule.CORRELATION_GROUP),  # case-normalised
        (Book((position("SPY", "LONG"),)), None),  # other group
        (Book(), None),
    ],
)
def test_correlation_group_same_direction_normalised(book, expected):
    result = correlation_group(intent(symbol="AAPL"), book, limits())
    assert (result.rule if result else None) == expected


def test_correlation_group_slash_prefix_and_empty_config():
    assert (
        correlation_group(
            intent(symbol="/MES", asset_class="FUTURES", quantity=1.0), Book((position("SPY"),)), limits()
        ).rule
        is RiskRule.CORRELATION_GROUP
    )
    assert (
        correlation_group(intent(symbol="AAPL"), Book((position("MSFT"),)), limits(portfolio__correlation_groups={}))
        is None
    )


def test_lockout_window_is_inclusive():
    event = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
    assert in_lockout_window(event - timedelta(minutes=60), event, 60, 30)
    assert in_lockout_window(event + timedelta(minutes=30), event, 60, 30)
    assert not in_lockout_window(event - timedelta(minutes=61), event, 60, 30)
    assert not in_lockout_window(event + timedelta(minutes=31), event, 60, 30)
    inside = macro_lockout("CPI", event, event, limits())
    assert inside.rule is RiskRule.MACRO_LOCKOUT and "CPI" in inside.reason and "12:30" in inside.reason
    assert macro_lockout(None, None, event, limits()) is None


@pytest.mark.parametrize(
    ("event", "today", "days", "expected"),
    [
        (date(2026, 10, 10), date(2026, 10, 6), 7, 4),
        (date(2026, 10, 13), date(2026, 10, 6), 7, 7),
        (date(2026, 10, 14), date(2026, 10, 6), 7, None),
        (date(2026, 10, 5), date(2026, 10, 6), 7, None),
        (None, date(2026, 10, 6), 7, None),
        (date(2026, 10, 7), date(2026, 10, 6), 0, None),
    ],
)
def test_earnings_days_out(event, today, days, expected):
    assert earnings_days_out(event, today, days) == expected


@pytest.mark.parametrize(
    ("is_open", "is_rth", "enforce", "expected"),
    [
        (True, True, True, None),
        (True, False, True, RiskRule.SESSION_NOT_RTH),
        (True, False, False, None),
        (False, False, False, RiskRule.SESSION_CLOSED),
        (False, True, True, RiskRule.SESSION_CLOSED),
    ],
)
def test_entry_session_open(is_open, is_rth, enforce, expected):
    result = entry_session_open(is_open, is_rth, enforce)
    assert (result.rule if result else None) == expected


def test_regime_breakout_only_for_squeeze_breakouts():
    assert regime_breakout("SQUEEZE_BREAKOUT", False).rule is RiskRule.REGIME_BREAKOUT
    assert regime_breakout("SQUEEZE_BREAKOUT", True) is None and regime_breakout("TREND_PULLBACK", False) is None


def test_composites_fix_the_order():
    bad_book = Book((position("X", notional=None), *[position(f"S{i}") for i in range(4)]))
    assert [r.rule for r in book_gates(intent(), bad_book, BUDGET, limits())][0] is RiskRule.EXPOSURE_UNKNOWN
    assert admission_gates(intent(), bad_book, BUDGET, limits()).rule is RiskRule.EXPOSURE_UNKNOWN
    full = Book(tuple(position(f"S{i}") for i in range(4)))
    assert book_gates(intent(), full, BUDGET, limits())[0].rule is RiskRule.CONCURRENT_POSITIONS
    assert (
        admission_gates(intent(quantity=2000.0, target=200.0), Book(), BUDGET, limits()).rule is RiskRule.PER_TRADE_RISK
    )
    assert admission_gates(intent(), Book(), BUDGET, limits(), regime_min_rr=2.2).rule is RiskRule.REWARD_RISK
    assert (
        admission_gates(intent(), Book(), BUDGET, limits()) is None
        and book_gates(intent(), Book(), BUDGET, limits()) == ()
    )
    assert str(Rejection(RiskRule.SAME_SYMBOL, "x")) == "x"
```

`tests/risk/test_adversarial.py`:

```python
"""Hostile inputs: constructors reject, rules never raise on valid inputs, results are order-independent and monotone."""

import math

import numpy as np
import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import (
    Book,
    BookPosition,
    EntryIntent,
    RiskLimits,
    RiskRule,
    admission_gates,
    book_gates,
    per_trade_risk_budget,
)

SEED, CASES = 20261006, 2000
HOSTILE_NUMBERS = [math.nan, math.inf, -math.inf, -1.0, 0.0]
SYMBOLS = ["AAPL", "aapl", "/MES", "MES", "mes ", "SPY", "BTC/USD", "", "X" * 40]
CLASSES = ["EQUITY", "equity", "FUTURES", "CRYPTO", "FX", "BOND", ""]
DIRECTIONS = ["LONG", "SHORT", "long", "FLAT", ""]


def rng():
    return np.random.default_rng(SEED)


def random_limits(r) -> RiskLimits:
    config = AppConfig()
    config.portfolio.cash = float(r.choice([1_000.0, 100_000.0, 1e9]))
    config.portfolio.max_concurrent_positions = int(r.integers(0, 6))
    config.portfolio.max_correlated_positions = int(r.integers(0, 3))
    config.portfolio.correlation_groups = r.choice(
        [{}, {"g": ["/MES", "SPY", "aapl"]}, {"a": ["AAPL"], "b": ["AAPL", "MSFT"]}]
    )
    config.portfolio.max_crypto_exposure = r.choice([None, 20_000.0])
    return RiskLimits.from_config(config)


def random_position(r) -> BookPosition:
    return BookPosition(
        symbol=str(r.choice(SYMBOLS)),
        direction=str(r.choice(DIRECTIONS)),
        asset_class=str(r.choice(CLASSES)),
        notional=r.choice([None, float(r.uniform(0, 50_000)), *HOSTILE_NUMBERS]),
        planned_risk=r.choice([None, float(r.uniform(0, 2_000)), *HOSTILE_NUMBERS]),
        reservation=bool(r.integers(0, 2)),
    )


def valid_intent(r) -> EntryIntent:
    entry = float(r.uniform(1, 500))
    direction = str(r.choice(["LONG", "SHORT"]))
    stop = entry * (0.98 if direction == "LONG" else 1.02)
    target = entry * float(r.uniform(0.9, 1.1))  # may be an inverted bracket on purpose
    return EntryIntent(
        symbol=str(r.choice(SYMBOLS[:7])),
        direction=direction,
        asset_class=str(r.choice(CLASSES[:5])),
        quantity=float(r.uniform(1, 2000)),
        entry=entry,
        stop=stop,
        target=target,
        multiplier=float(r.choice([1.0, 5.0, 50.0])),
        current_price=r.choice([None, entry * float(r.uniform(0.95, 1.05))]),
    )


def test_constructors_reject_hostile_numbers():
    for bad in HOSTILE_NUMBERS[:3] + [-5.0, 0.0]:
        with pytest.raises(ValueError):
            EntryIntent("AAPL", "LONG", "EQUITY", quantity=bad, entry=100.0, stop=99.0, target=102.0)
    with pytest.raises(ValueError):
        EntryIntent("AAPL", "SIDEWAYS", "EQUITY", quantity=1.0, entry=100.0, stop=99.0, target=102.0)
    for bad in (math.nan, math.inf):
        with pytest.raises(ValueError):
            per_trade_risk_budget(
                RiskLimits.from_config(AppConfig()), equity=None, drawdown_pct=0.0, macro_multiplier=bad
            )


def test_rules_never_raise_and_fail_closed_on_unknown_exposure():
    r = rng()
    for _ in range(CASES):
        limits = random_limits(r)
        book = Book(tuple(random_position(r) for _ in range(int(r.integers(0, 6)))))
        budget = per_trade_risk_budget(
            limits,
            equity=r.choice([None, float(r.uniform(100, 1e6))]),
            drawdown_pct=float(r.choice([0.0, 0.02, 0.045, 0.06, 0.5])),
            macro_multiplier=float(r.choice([0.0, 0.3, 1.0, 2.0])),
        )
        intent = valid_intent(r)
        scan = book_gates(intent, book, budget, limits)
        admission = admission_gates(intent, book, budget, limits, regime_min_rr=float(r.choice([1.0, 2.0, 2.5])))
        if book.invalid:
            assert scan and scan[0].rule is RiskRule.EXPOSURE_UNKNOWN
            assert admission is not None and admission.rule is RiskRule.EXPOSURE_UNKNOWN
        for rejection in (*scan, admission):
            assert rejection is None or (isinstance(rejection.reason, str) and rejection.reason)


def test_results_are_order_independent_and_monotone():
    r = rng()
    for _ in range(CASES // 4):
        limits = random_limits(r)
        positions = [
            BookPosition(
                str(r.choice(SYMBOLS[:6])),
                str(r.choice(["LONG", "SHORT"])),
                "EQUITY",
                float(r.uniform(0, 20_000)),
                float(r.uniform(0, 500)),
            )
            for _ in range(int(r.integers(0, 5)))
        ]
        budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0)
        intent = valid_intent(r)
        forward = {x.rule for x in book_gates(intent, Book(tuple(positions)), budget, limits)}
        backward = {x.rule for x in book_gates(intent, Book(tuple(reversed(positions))), budget, limits)}
        assert forward == backward
        larger = Book((*positions, BookPosition("ZZZ", intent.direction, "EQUITY", 1.0, 1.0)))
        assert forward <= {x.rule for x in book_gates(intent, larger, budget, limits)}
```

`tests/risk/test_consistency.py`:

```python
"""The scan's book gates and admission agree on every shared rule, and admission's string API agrees with the typed API."""

import numpy as np

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, OrderSide
from agentic_trader.execution.admission import reservation_rejection
from agentic_trader.risk import (
    Book,
    BookPosition,
    EntryIntent,
    RiskLimits,
    RiskRule,
    admission_gates,
    book_gates,
    per_trade_risk_budget,
)

SHARED = {
    RiskRule.EXPOSURE_UNKNOWN,
    RiskRule.DRAWDOWN_HALT,
    RiskRule.AGGREGATE_STOP_RISK,
    RiskRule.CONCURRENT_POSITIONS,
    RiskRule.PORTFOLIO_NOTIONAL,
    RiskRule.ASSET_CLASS_NOTIONAL,
    RiskRule.CORRELATION_GROUP,
}


def test_scan_and_admission_agree_on_shared_rules():
    r = np.random.default_rng(7)
    config = AppConfig()
    config.portfolio.correlation_groups = {"g": ["AAPL", "MSFT"]}
    limits = RiskLimits.from_config(config)
    for _ in range(1500):
        rows = [
            {
                "contract": str(r.choice(["AAPL", "MSFT", "SPY"])),
                "direction": str(r.choice(["LONG", "SHORT"])),
                "asset_class": "EQUITY",
                "notional_value": float(r.uniform(0, 30_000)),
                "risk_dollars": float(r.uniform(0, 1_000)),
                "status": "EXECUTED",
            }
            for _ in range(int(r.integers(0, 5)))
        ]
        book = Book.from_signal_rows(rows, reservations=False)
        budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=float(r.choice([0.0, 0.04, 0.06])))
        intent = EntryIntent(
            "AAPL", "LONG", "EQUITY", quantity=float(r.uniform(1, 400)), entry=100.0, stop=99.0, target=102.0
        )
        scan_first = next((x.rule for x in book_gates(intent, book, budget, limits) if x.rule in SHARED), None)
        admission = admission_gates(intent, book, budget, limits)
        admission_shared = admission.rule if admission and admission.rule in SHARED else None
        # Admission runs per-trade rules first; when its first rejection is per-trade, compare the next shared one.
        if admission is not None and admission.rule not in SHARED:
            continue
        assert scan_first == admission_shared, (rows, intent, scan_first, admission)


def test_reservation_rejection_matches_the_typed_api():
    config = AppConfig()
    config.portfolio.correlation_groups = {"g": ["AAPL", "MSFT"]}
    config.portfolio.max_correlated_positions = 1
    config.contracts = {}
    limits = RiskLimits.from_config(config)
    rows = [
        {
            "contract": "MSFT",
            "direction": "LONG",
            "asset_class": "EQUITY",
            "notional_value": 1000.0,
            "risk_dollars": 10.0,
            "status": "EXECUTED",
        }
    ]
    request = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=102.0,
    )
    expected = admission_gates(
        EntryIntent("AAPL", "LONG", "EQUITY", 10.0, 100.0, 99.0, 102.0),
        Book.from_signal_rows(rows, reservations=True),
        per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0),
        limits,
    )
    assert expected is not None and expected.rule is RiskRule.CORRELATION_GROUP
    assert reservation_rejection(request, rows, config) == expected.reason
    rows[0]["direction"] = "SHORT"
    assert reservation_rejection(request, rows, config) is None
```

(`test_consistency.py::test_reservation_rejection_matches_the_typed_api` goes RED in this task and GREEN in Task 3; mark it `@pytest.mark.xfail(strict=True, reason="admission rewires in Task 3")` here and remove the marker in Task 3.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/risk`
Expected: FAIL — `ImportError: cannot import name 'EntryIntent'`.

- [ ] **Step 3: Implement `agentic_trader/risk/rules.py`**

```python
"""One pure function per entry-risk rule; every layer calls these and nothing else.

Inputs are the typed objects from this package (``EntryIntent``, ``Book``, ``RiskBudget``,
``RiskLimits``). A rule returns a ``Rejection`` or ``None`` and never performs I/O: the
calendar, session provider, regime detector and database stay with the callers, which pass
in what they observed. Layers differ only in the book they pass (the scan: open positions
plus this scan's cards; admission: open positions plus reservations).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from agentic_trader.risk.book import Book
from agentic_trader.risk.capital import RiskBudget
from agentic_trader.risk.limits import RiskLimits, normalize_symbol

__all__ = [...]  # every public name below

SQUEEZE_BREAKOUT = "SQUEEZE_BREAKOUT"


class RiskRule(StrEnum):
    EXPOSURE_UNKNOWN = "exposure_unknown"
    DRAWDOWN_HALT = "drawdown_halt"
    PER_TRADE_RISK = "per_trade_risk"
    PER_TRADE_NOTIONAL = "per_trade_notional"
    QUANTITY_CAP = "quantity_cap"
    REWARD_RISK = "reward_risk"
    AGGREGATE_STOP_RISK = "aggregate_stop_risk"
    CONCURRENT_POSITIONS = "concurrent_positions"
    SAME_SYMBOL = "same_symbol"
    PORTFOLIO_NOTIONAL = "portfolio_notional"
    ASSET_CLASS_NOTIONAL = "asset_class_notional"
    CORRELATION_GROUP = "correlation_group"
    MACRO_LOCKOUT = "macro_lockout"
    EARNINGS_BLACKOUT = "earnings_blackout"
    SESSION_CLOSED = "session_closed"
    SESSION_NOT_RTH = "session_not_rth"
    REGIME_BREAKOUT = "regime_breakout"


@dataclass(frozen=True)
class Rejection:
    rule: RiskRule
    reason: str

    def __str__(self) -> str:
        return self.reason


def _positive(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


@dataclass(frozen=True)
class EntryIntent:
    """What is about to be risked, validated once on construction."""

    symbol: str
    direction: str
    asset_class: str
    quantity: float
    entry: float
    stop: float
    target: float
    multiplier: float = 1.0
    current_price: float | None = None
    strategy: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", str(self.direction).upper())
        object.__setattr__(self, "asset_class", str(self.asset_class).upper())
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError("direction must be LONG or SHORT")
        for name in ("quantity", "entry", "stop", "target", "multiplier"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.current_price is not None:
            object.__setattr__(self, "current_price", _positive("current_price", self.current_price))

    @property
    def key(self) -> str:
        return normalize_symbol(self.symbol)

    @property
    def sign(self) -> float:
        return 1.0 if self.direction == "LONG" else -1.0

    @property
    def risk_distance(self) -> float:
        return (self.entry - self.stop) * self.sign

    @property
    def reward_distance(self) -> float:
        return (self.target - self.entry) * self.sign

    @property
    def risk_dollars(self) -> float:
        return max(self.risk_distance, 0.0) * self.quantity * self.multiplier

    @property
    def exposure_price(self) -> float:
        return max(self.entry, self.current_price) if self.current_price is not None else self.entry

    @property
    def notional(self) -> float:
        return self.exposure_price * self.quantity * self.multiplier
```

Then the rule functions exactly as named in the spec, each a few lines, using the reason texts listed under Interfaces. Notes for specific ones:

- `meets_min_reward_risk(reward, risk, minimum)`: move the function body verbatim from `execution/freshness.py:114-131` (two-decimal rounding, `risk <= 0` → False); in `freshness.py` replace the definition with `from agentic_trader.risk import meets_min_reward_risk` (keep its use in `assess_card`).
- `reward_risk(intent, required)`: `rr = round(intent.reward_distance / intent.risk_distance, 2) if intent.risk_distance > 0 else 0.0`; reject unless `meets_min_reward_risk(intent.reward_distance, intent.risk_distance, required)`.
- `required_reward_risk(limits, regime_min_rr)`: `max(limits.min_risk_reward_ratio, float(regime_min_rr))` when `regime_min_rr` is not None and finite, else the config value.
- `aggregate_stop_risk`: `book.planned_risk() + intent.risk_dollars > budget.capital * limits.max_stop_risk_pct * budget.drawdown_factor`.
- `asset_class_notional`: `cap = limits.asset_class_caps.get(intent.asset_class)`; `None` → return None.
- `correlation_group`: for each `(group, keys)` with `intent.key in keys`: `held = book.same_direction_in(keys, intent.direction)`; reject when `len(held) >= limits.max_correlated_positions`. Empty groups mapping → None.
- `in_lockout_window(now, event_at, pre, post)`: `event_at - timedelta(minutes=pre) <= now <= event_at + timedelta(minutes=post)`.
- `macro_lockout(title, event_at, now, limits)`: None when `event_at is None` or outside the window; else the rejection with `event_at.strftime("%H:%M")`.
- `earnings_days_out(event_date, today, blackout_days)`: None when `blackout_days <= 0` or `event_date is None`; `days = (event_date - today).days`; return `days` when `0 <= days <= blackout_days`, else None.
- `entry_session_open(is_open, is_rth, enforce_rth, detail="")`: closed → `SESSION_CLOSED`; `enforce_rth and not is_rth` → `SESSION_NOT_RTH`; else None.
- `regime_breakout(strategy, breakout_allowed)`: reject only when `str(strategy).upper().endswith(SQUEEZE_BREAKOUT) and not breakout_allowed` (the live enum's string form is `StrategyType.SQUEEZE_BREAKOUT`; check how `str(StrategyType.SQUEEZE_BREAKOUT)` renders and compare by `.value` where needed so both forms match).
- Composites: `book_gates` returns a tuple of every rejection in the spec's order (skipping rules that pass); `admission_gates` returns the first in the spec's order.

Export everything from `agentic_trader/risk/__init__.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/risk tests/execution/test_card_freshness.py`
Expected: PASS (the one `xfail(strict=True)` consistency test is reported as xfailed).

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/risk tests/risk agentic_trader/execution/freshness.py
git commit -m "risk: one pure function per entry rule, fixed-order composites, adversarial and consistency suites

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Admission, preflight, workflow and calendar/earnings delegate to the module

**Files:**
- Modify: `agentic_trader/execution/admission.py` (`reservation_rejection` body), `agentic_trader/execution/entries.py` (`_preflight`: macro check before the branch split), `agentic_trader/agent/calendar.py:106-121` (`is_in_lockout_window` uses `in_lockout_window`), `agentic_trader/agent/earnings.py:292-311` (`earnings_blackout_reason` uses `earnings_days_out`)
- Modify tests: `tests/workflows/test_entries.py`, `tests/workflows/test_durable.py`, `tests/broker/test_broker.py`, `tests/execution/test_entry_capacity.py`, `tests/execution/test_drawdown_policy.py`, `tests/integration/test_postgres.py` (strings only), `tests/risk/test_consistency.py` (remove the xfail marker)
- Test: `tests/workflows/test_entries_macro.py` (new: simulated-branch lockout)

**Interfaces:**
- Consumes: `EntryIntent`, `Book.from_signal_rows(rows, reservations=True)`, `per_trade_risk_budget`, `admission_gates`, `in_lockout_window`, `earnings_days_out`.
- Produces: `reservation_rejection` unchanged signature; returns `str(rejection)` or `None`. Preflight rejects a simulated entry during a lockout with the macro check's reason.

- [ ] **Step 1: Write the failing tests**

Remove the `xfail` marker in `tests/risk/test_consistency.py`. Add `tests/workflows/test_entries_macro.py`:

```python
"""Preflight runs the macro check on the simulated branch too (spec decision 6)."""

from unittest.mock import AsyncMock

import pytest


async def test_simulated_preflight_rejects_during_macro_lockout(entry_service_factory):
    # Build on the fixture used by tests/workflows/test_entries.py for a simulated entry service:
    # read that module and reuse its factory/fixture names; the only new ingredient is a
    # macro_check that returns a reason.
    service = entry_service_factory(macro_check=AsyncMock(return_value="Macro event lockout: CPI at 12:30 UTC."))
    item = await service_enqueue_approved_card(service)  # helper from test_entries.py, or inline the same steps
    rejection, _context = await service._preflight(item, request_for(item), None)
    assert rejection == "Macro event lockout: CPI at 12:30 UTC."
```

The implementer adapts fixture names to what `tests/workflows/test_entries.py` provides (read it first); the assertion stays. For the reason-string updates, change each pinned old string to the new text from Task 2's table (e.g. `"Configured correlated-position limit reached."` → the `correlation_group` text; `"Maximum concurrent positions (4) reached, including reservations."` → `"Maximum concurrent positions (4) reached."`; `"Bracket direction or configured risk/reward requirement is no longer valid."` → the `reward_risk` text with its numbers; the per-trade cap texts as listed). Run the affected tests first to collect the exact failures (RED), then update the expectations.

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/risk/test_consistency.py tests/workflows/test_entries_macro.py`
Expected: FAIL (admission still returns the old strings; the simulated branch skips the macro check).

- [ ] **Step 3: Implement**

`admission.py` — keep the direction/side check and the `info is None and FUTURES` multiplier check, then:

```python
try:
    intent = EntryIntent(
        symbol=request.symbol,
        direction=str(request.direction),
        asset_class=str(request.asset_class),
        quantity=request.quantity,
        entry=request.entry_price,
        stop=request.stop_loss,
        target=request.take_profit,
        multiplier=multiplier,
        current_price=current_price,
    )
except TypeError, ValueError:
    if current_price is not None and (not math.isfinite(current_price) or current_price <= 0):
        return "Current exposure price must be finite and positive."
    return "Quantity and bracket prices must be finite and positive."
limits = RiskLimits.from_config(config)
try:
    budget = per_trade_risk_budget(limits, equity=current_equity, drawdown_pct=current_drawdown_pct)
except ValueError as exc:
    return f"Account risk inputs invalid: {exc}."
book = Book.from_signal_rows(positions, reservations=True)
rejection = admission_gates(intent, book, budget, limits)
return str(rejection) if rejection else None
```

Remove the now-unused imports (`drawdown_risk_factor`, `risk_capital`, `meets_min_reward_risk`, `Direction`/`OrderSide` only if no longer used). Admission runs `required_reward_risk(limits, None)` through `admission_gates`'s default; the regime threshold is applied at the tap gate (Task 5), where the regime is known.

`entries.py` `_preflight`: move `if reason := await self.macro_check(request, signal): return reason, context` to directly after `context = await self.broker.entry_market_context(...)` (before `if isinstance(context, BrokerEntryContext)`), guarded by `if self.macro_check is not None`. Read how `macro_check` is injected (constructor) to keep the attribute name; the Alpaca branch loses its own call.

`calendar.py:118-120`: replace the two `timedelta` lines and the comparison with `if in_lockout_window(now, e.timestamp, pre_minutes, post_minutes): return True, e`. `earnings.py:292-311`: replace the day arithmetic with `days_out = earnings_days_out(lookup.event.date if lookup.event else None, today, blackout_days)`; `None` → return None; keep the text. Import both from `agentic_trader.risk`.

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/risk tests/workflows tests/execution tests/broker tests/agent/test_earnings.py tests/agent/test_evaluator.py -k "not integration"`
Expected: PASS after the string updates.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/execution/admission.py agentic_trader/execution/entries.py agentic_trader/agent/calendar.py agentic_trader/agent/earnings.py tests/
git commit -m "admission/preflight: delegate every shared rule to agentic_trader.risk; macro check on both broker branches

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Evaluator and sizing delegate to the module

**Files:**
- Modify: `agentic_trader/agent/evaluator.py` (`calculate_levels_deterministic` gains `min_reward_risk: float | None = None`; `evaluate_candidate` blocks 1–1c, 2, 2b, 3, 4 replaced; LLM clamp uses the required ratio; one local `_rejected(...)` builder replaces the eight repeated `LLMTradeEvaluation(...)` rejection literals), `agentic_trader/agent/position_sizing.py` (`calculate_dynamic_sizing` uses `per_trade_risk_budget`; macro applies to the max tier), `agentic_trader/agent/copilot.py` (`run_scan`'s in-scan `active_positions.append` adds `"risk_dollars": eval_res.risk_dollars`)
- Modify tests: `tests/agent/test_evaluator.py`, `tests/execution/test_risk_budgeting.py`, `tests/execution/test_position_sizing.py`, `tests/agent/test_probe_sizing.py` (strings/tiers)
- Test: `tests/agent/test_evaluator_risk_policy.py` (new)

**Interfaces:**
- Consumes: `RiskLimits`, `Book`, `EntryIntent`, `per_trade_risk_budget`, `book_gates`, `macro_lockout`, `entry_session_open`, `regime_breakout`, `required_reward_risk`, `earnings_blackout_reason` (already delegating).
- Produces: a candidate rejected by a shared rule carries `rejection_reason == str(rejection)` and the evaluation's existing fields; a new `rejection_rule: str | None` field on `LLMTradeEvaluation` records the `RiskRule` value (None for LLM vetoes and sizing blocks).

- [ ] **Step 1: Write the failing tests**

`tests/agent/test_evaluator_risk_policy.py` (reuse `evaluator_factory` from `tests/agent/test_evaluator.py` and a candidate helper from there):

```python
"""The evaluator's gates are the shared risk rules (spec decisions 1, 3, 4, 5, 7)."""

import pytest

from agentic_trader.risk import RiskRule
from tests.agent.test_evaluator import evaluator_factory, make_candidate  # noqa: F401  (adapt names to that module)


def book_row(symbol, direction="LONG", notional=1000.0, risk=50.0, asset_class="EQUITY"):
    return {
        "contract": symbol,
        "symbol": symbol,
        "direction": direction,
        "asset_class": asset_class,
        "notional_value": notional,
        "risk_dollars": risk,
    }


async def test_full_book_is_rejected_at_scan_time(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.portfolio.max_concurrent_positions = 2
    result = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A"), book_row("B")]
    )
    assert result.approved is False and result.rejection_rule == RiskRule.CONCURRENT_POSITIONS
    assert result.rejection_reason == "Maximum concurrent positions (2) reached."


async def test_exhausted_stop_risk_budget_is_rejected_at_scan_time(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.portfolio.cash = 100_000.0
    evaluator.config.portfolio.max_stop_risk_pct = 0.02
    result = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A", risk=1_990.0)]
    )
    assert result.rejection_rule == RiskRule.AGGREGATE_STOP_RISK


async def test_opposite_direction_in_group_is_allowed_and_empty_groups_disable(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.portfolio.correlation_groups = {"g": [make_candidate().contract, "OTHER"]}
    evaluator.config.portfolio.max_correlated_positions = 1
    same = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("OTHER", "LONG")]
    )
    assert same.rejection_rule == RiskRule.CORRELATION_GROUP
    opposite = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("OTHER", "SHORT")]
    )
    assert opposite.approved is True
    evaluator.config.portfolio.correlation_groups = {}
    disabled = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("OTHER", "LONG")]
    )
    assert disabled.approved is True  # no fallback to DEFAULT_CORRELATION_GROUPS


async def test_unknown_exposure_fails_closed(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    result = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A", notional=None)]
    )
    assert result.rejection_rule == RiskRule.EXPOSURE_UNKNOWN


async def test_target_is_built_at_the_regime_adjusted_ratio(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.risk.min_risk_reward_ratio = 2.0
    # Make the (mocked) regime detector report an elevated minimum; read evaluator_factory to see how the regime is stubbed.
    set_regime_min_rr(evaluator, 2.2)
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert result.approved is True
    assert round(result.target_distance_points / result.stop_distance_points, 2) >= 2.2


async def test_extended_hours_allowed_when_rth_not_enforced(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    set_session(evaluator, is_open=True, is_rth=False)
    evaluator.config.session.enforce_rth = True
    assert (
        await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    ).rejection_rule == RiskRule.SESSION_NOT_RTH
    evaluator.config.session.enforce_rth = False
    assert (await evaluator.evaluate_candidate(make_candidate(), use_llm=False)).approved is True
```

`set_regime_min_rr` and `set_session` are small helpers the implementer writes against the factory's stubs (read `tests/agent/test_evaluator.py` first). Also extend `tests/execution/test_position_sizing.py` with:

```python
def test_macro_factor_scales_the_max_tier_too():
    # Same inputs with macro 1.0 vs 0.5: the max tier's risk dollars halve (spec decision 2).
    full = calculate_dynamic_sizing(
        entry=100.0,
        stop_distance=1.0,
        target_distance=2.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=config,
        macro_risk_multiplier=1.0,
    )
    stressed = calculate_dynamic_sizing(
        entry=100.0,
        stop_distance=1.0,
        target_distance=2.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=config,
        macro_risk_multiplier=0.5,
    )
    assert stressed.max_tier.risk_dollars == pytest.approx(full.max_tier.risk_dollars * 0.5, rel=0.02)
```

(adapt `config` to that module's fixture).

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_evaluator_risk_policy.py tests/execution/test_position_sizing.py -k "macro_factor_scales or risk_policy"`
Expected: FAIL (`rejection_rule` missing; old strings; macro not applied to the max tier).

- [ ] **Step 3: Implement**

`position_sizing.py`: replace the `portfolio_cash = risk_capital(...)`, `macro_factor = max(0.10, min(1.0, …))`, `drawdown_factor = drawdown_risk_factor(...)` and `max_risk_dollars = portfolio_cash * sizing_cfg.max_risk_pct_cap * drawdown_factor` lines with

```python
limits = RiskLimits.from_config(config)
budget = per_trade_risk_budget(
    limits, equity=current_equity, drawdown_pct=current_drawdown_pct, macro_multiplier=macro_risk_multiplier
)
portfolio_cash, drawdown_factor, macro_factor = budget.capital, budget.drawdown_factor, budget.macro_factor
...
max_risk_dollars = budget.dollars  # one formula for every layer (agentic_trader.risk.per_trade_risk_budget)
```

keeping the gating-reason texts and the probe cap logic.

`evaluator.py`:
1. `LLMTradeEvaluation` gains `rejection_rule: str | None = None`.
2. `calculate_levels_deterministic(..., min_reward_risk: float | None = None)`: `ratio = min_reward_risk if min_reward_risk is not None else self.config.risk.min_risk_reward_ratio`; use `ratio` in both `target_distance = round(stop_distance * ratio, 2)` lines. `evaluate_candidate` passes `min_reward_risk=required_reward_risk(limits, regime.min_rr_threshold)` and the LLM clamp (`min_required_rr`) uses the same value.
3. Add a local builder inside `evaluate_candidate` (or a private method) `_rejected(reason, rule=None, *, stop=entry, target=entry, …)` that fills the `LLMTradeEvaluation` rejection shape the eight literals share; each rejection path becomes one call.
4. Replace blocks 1, 1b, 1c with: `book = Book.from_signal_rows(active_positions or [], reservations=False)`; `intent = EntryIntent(symbol=candidate.contract, direction=…, asset_class=str(asset_class), quantity=quantity, entry=entry, stop=stop_loss, target=take_profit, multiplier=multiplier, strategy=str(candidate.strategy))`; `budget = per_trade_risk_budget(limits, equity=current_equity, drawdown_pct=current_drawdown_pct, macro_multiplier=regime.risk_multiplier)`; `gates = book_gates(intent, book, budget, limits)`; if `gates`: return `_rejected(gates[0].reason, gates[0].rule)`. Keep block 1d (statistical correlation) as is after it. Note `current_open_notional` is no longer needed for the cap (the book's notional is); keep the parameter for sizing's notional ceiling.
5. Block 2 → `event` from `is_in_lockout_window`; `rejection = macro_lockout(lock_event.title, lock_event.timestamp, evaluated_at, limits) if in_lockout else None`. Block 3 → `regime_breakout(candidate.strategy, regime.breakout_allowed)`. Block 4 → `entry_session_open(session_info.is_open, session_info.is_rth, limits.enforce_rth, detail=str(session_info.details))`; the `macro_clearance` field stays False only for the macro rejection as today.
6. `copilot.py` in-scan append: add `"risk_dollars": eval_res.risk_dollars`.

Update the string pins in `tests/agent/test_evaluator.py` and `tests/execution/test_risk_budgeting.py` to the new texts or to `rejection_rule`.

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/execution tests/risk`
Expected: PASS.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/agent/evaluator.py agentic_trader/agent/position_sizing.py agentic_trader/agent/copilot.py tests/
git commit -m "evaluator/sizing: shared book gates with scan-time concurrent and stop-risk checks; regime-adjusted target; macro on every tier

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Tap gate, re-pricing and session checks in the copilot

**Files:**
- Modify: `agentic_trader/agent/copilot.py` (`_regime_gate`, `_capped_replacement_quantity`, tap `session_is_rth=` call, re-evaluate and `/scan` session checks, `_card_valid_until`), `agentic_trader/execution/freshness.py` (`assess_card(session_is_rth=…)` → `session_open: bool`, `card_session_over` wording if it names RTH)
- Modify tests: `tests/agent/test_regime_gate.py`, `tests/agent/test_card_freshness_tap.py`, `tests/execution/test_card_freshness.py`, `tests/agent/test_symbol_scan.py` (parameter rename and strings)
- Test: `tests/agent/test_tap_risk_policy.py` (new)

**Interfaces:**
- Consumes: `EntryIntent`, `per_trade_risk_budget`, `per_trade_risk`, `reward_risk`, `required_reward_risk`, `regime_breakout`, `entry_session_open`.
- Produces: `_regime_gate` returns the rule's reason text; `_capped_replacement_quantity` uses `budget.dollars`; every entry-side session decision is `entry_session_open(...) is None`.

- [ ] **Step 1: Write the failing tests**

`tests/agent/test_tap_risk_policy.py` (build on the desk fixtures in `tests/agent/test_regime_gate.py`):

```python
from types import SimpleNamespace

import pytest

from agentic_trader.risk import RiskLimits, per_trade_risk_budget


def test_regime_gate_uses_the_shared_budget_and_ratio(desk, request_factory, signal_factory):
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.2, risk_multiplier=0.5)
    request = request_factory(quantity=10.0, entry_price=100.0, stop_loss=99.0, take_profit=102.0)  # rr 2.0
    assert (
        desk._regime_gate(request, signal_factory(strategy="TREND_PULLBACK"), regime)
        == "Reward/risk 2.00 is below the required 2.20."
    )
    limits = RiskLimits.from_config(desk.config)
    budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0, macro_multiplier=0.5)
    too_big = request_factory(quantity=budget.dollars / 1.0 + 1, entry_price=100.0, stop_loss=99.0, take_profit=102.2)
    assert desk._regime_gate(too_big, signal_factory(strategy="TREND_PULLBACK"), regime).startswith(
        "Order exceeds the drawdown- and macro-adjusted"
    )
    ok = request_factory(quantity=10.0, entry_price=100.0, stop_loss=99.0, take_profit=102.2)
    assert desk._regime_gate(ok, signal_factory(strategy="TREND_PULLBACK"), regime) is None


def test_regime_gate_with_nan_multiplier_raises_value_error_for_the_tap_to_report(
    desk, request_factory, signal_factory
):
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=float("nan"))
    with pytest.raises(ValueError):
        desk._regime_gate(request_factory(), signal_factory(), regime)


def test_replacement_quantity_uses_the_budget(desk):
    regime = SimpleNamespace(risk_multiplier=0.5)
    limits = RiskLimits.from_config(desk.config)
    budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0, macro_multiplier=0.5)
    capped = desk._capped_replacement_quantity(
        10_000.0, price=100.0, stop=99.0, multiplier=1.0, asset_class="EQUITY", regime=regime
    )
    assert capped == float(
        int(
            min(
                budget.dollars / 1.0,
                desk.config.sizing.max_shares_per_trade,
                desk.config.sizing.max_trade_notional_cap / 100.0,
            )
        )
    )
```

Plus, in `tests/execution/test_card_freshness.py`, rename `session_is_rth=` to `session_open=` in every `assess_card` call and add one case: `session_open=True` with a non-RTH session (the caller decided via `entry_session_open`) is not EXPIRED.

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_tap_risk_policy.py tests/execution/test_card_freshness.py`
Expected: FAIL (old strings/arithmetic; `assess_card` has no `session_open`).

- [ ] **Step 3: Implement**

`_regime_gate`:

```python
def _regime_gate(self, request: OrderRequest, signal: dict[str, Any], regime: Any) -> str | None:
    """Tap/admission-time regime gate: the shared breakout, reward/risk and per-trade budget rules.

    The budget uses configured cash and the regime multiplier (``per_trade_risk_budget``);
    admission then re-checks it with observed equity and drawdown on the same formula.
    """
    if rejection := regime_breakout(signal["strategy"], regime.breakout_allowed):
        return rejection.reason
    info = self.config.contracts.get(request.symbol)
    intent = EntryIntent(
        symbol=request.symbol,
        direction=str(request.direction),
        asset_class=str(request.asset_class),
        quantity=request.quantity,
        entry=request.entry_price,
        stop=request.stop_loss,
        target=request.take_profit,
        multiplier=info.multiplier if info else 1.0,
    )
    limits = RiskLimits.from_config(self.config)
    if rejection := reward_risk(intent, required_reward_risk(limits, regime.min_rr_threshold)):
        return rejection.reason
    budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0, macro_multiplier=regime.risk_multiplier)
    rejection = per_trade_risk(intent, budget)
    return rejection.reason if rejection else None
```

`_capped_replacement_quantity`: replace the `risk_scale`/cash line with `budget = per_trade_risk_budget(RiskLimits.from_config(self.config), equity=None, drawdown_pct=0.0, macro_multiplier=getattr(regime, "risk_multiplier", 1.0))` and `caps.append(budget.dollars / unit_risk)`. Update its docstring.

Session: the tap passes `session_open=entry_session_open(info.is_open, info.is_rth, limits.enforce_rth) is None`; `freshness.assess_card` renames the parameter and its internal use; re-evaluate (`copilot.py:3188`) and `/scan` (`:3267`) replace `not (info.is_open and info.is_rth)` with `entry_session_open(info.is_open, info.is_rth, limits.enforce_rth) is not None`; `_card_valid_until` records `valid_until` when `entry_session_open(...) is None` and `next_close` is aware. Also pass the `min_reward_risk=max(config, regime)` already present at the tap through `required_reward_risk(limits, regime.min_rr_threshold)`.

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/execution tests/risk`
Expected: PASS.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/agent/copilot.py agentic_trader/execution/freshness.py tests/
git commit -m "copilot: tap gate, re-pricing and session checks delegate to agentic_trader.risk

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Integration scenarios on the temp database

**Files:**
- Test: `tests/agent/test_risk_policy_integration.py` (new)

**Interfaces:**
- Consumes: the real `run_scan`, `_assess_card_tap`/tap handler, `WorkflowStore.enqueue_entry` and `EntryExecutionService._preflight` paths through the existing desk fixtures (`scan_desk`, `budget_desk`, the lifecycle fixtures in `tests/agent/test_card_lifecycle_e2e.py`, the regime stubs in `tests/agent/test_regime_gate.py`).

- [ ] **Step 1: Write the five scenarios**

Read `tests/agent/test_card_lifecycle_e2e.py`, `tests/agent/test_scan_budget.py` and `tests/agent/test_regime_gate.py` first and reuse their fixtures; write each scenario so a failure names the rule:

```python
"""End to end: the same risk rules at scan, tap and admission (spec §Testing, integration)."""

from agentic_trader.risk import RiskRule


async def test_a_card_built_under_an_elevated_regime_passes_its_own_tap_gate(...):
    # regime stub: vix_regime ELEVATED, min_rr_threshold 2.2, breakout_allowed True, risk_multiplier 1.0
    # run_scan(use_llm=False, budget=FULL) → one card; its target/stop >= 2.2
    # desk._regime_gate(OrderRequest built from the card, signal row, same regime) is None


async def test_a_full_book_stops_the_card_at_scan_time(...):
    # plant max_concurrent_positions EXECUTED signals with notional/risk; run_scan → zero cards;
    # last_scan_summary runners_up reason == "rejected: Maximum concurrent positions (N) reached."


async def test_empty_correlation_groups_disable_the_rule_at_both_layers(...):
    # correlation_groups = {}; an EXECUTED same-sector LONG exists; run_scan sends the card;
    # enqueue_entry for it is accepted (no correlation rejection)


async def test_simulated_admission_rejects_during_a_macro_lockout(...):
    # EXECUTION_MODE paper (simulator); calendar stub returns a tier-1 event now; a queued card's
    # preflight resolves REJECTED with "Macro event lockout: …"


async def test_tap_outside_rth_is_allowed_when_rth_is_not_enforced(...):
    # session stub is_open True / is_rth False; enforce_rth False → tap proceeds past the session check
    # (outcome not EXPIRED with "session has ended"); enforce_rth True → EXPIRED
```

Each scenario asserts the rule by reason text (or `rejection_rule` where the evaluation is reachable); the implementer fills fixture plumbing, never weakens the assertion. If a scenario cannot be driven through the real path with existing fixtures, report BLOCKED with the missing seam rather than mocking the rule.

- [ ] **Step 2: Run, iterate to GREEN (the production code should already satisfy them; a failure here is a defect in Tasks 3–5 — fix the production code in this task and say so in the report)**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_risk_policy_integration.py`

- [ ] **Step 3: Regression gate, then commit**

```bash
git add tests/agent/test_risk_policy_integration.py agentic_trader/
git commit -m "tests: end-to-end risk-policy scenarios across scan, tap and admission

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Documentation, comments, full suite, PostgreSQL integration

**Files:**
- Create: `docs/risk-policy.md`
- Modify: `docs/production.md` (sizing/caps paragraphs: one pointer sentence each), `docs/entry-capacity.md` (admission delegates shared rules), `docs/architecture-review.md` (new row under "Remaining findings, ranked": resolved, with the PR), `docs/alpha-roadmap.md` (L2 row → `Implemented — PR (pending)`), `CLAUDE.md` (code-map row "Signal evaluation and risk" adds `risk/`; contract 1 gains the sentence below)
- Review every docstring/comment touched in Tasks 1–5 for pre-L2 wording (grep `DEFAULT_CORRELATION_GROUPS`, `cash *`, `risk_multiplier`, `is_rth` in comments) and fix.

- [ ] **Step 1: `docs/risk-policy.md`**

Sections: Purpose (one implementation per rule; layers differ by book); Types (`RiskLimits`, `Book`/`BookPosition`, `EntryIntent`, `RiskBudget`, `Rejection`/`RiskRule`); The budget formula; Rule table with columns `rule id | inputs | scan | tap/reprice | admission | reason text | test`; Books per layer; Behaviour changes on 2026-10-06 (decisions 1–7 with one line each); Adversarial and consistency guarantees (what `tests/risk/test_adversarial.py` and `test_consistency.py` pin); What is not a risk rule (card budgets, PEAD cap, statistical correlation, capacity evidence).

- [ ] **Step 2: Other docs**

`CLAUDE.md` contract 1, append after "…acceptance is not a fill.":

```markdown
   Every entry-risk rule (per-trade budget, notional and class caps, concurrent positions,
   aggregate stop risk, correlation group, reward/risk, macro lockout, session, earnings,
   regime) has one implementation in `agentic_trader/risk`; the evaluator, tap gate,
   re-pricing and admission differ only in the book they pass ([risk policy](docs/risk-policy.md)).
```

Code map row: `| Signal evaluation and risk | \`agent/evaluator.py\`, \`position_sizing.py\`, \`regime.py\`, \`macro.py\`, \`calendar.py\`, \`risk/\` |`.

- [ ] **Step 3: Full suite, pre-commit, PostgreSQL**

```bash
env -u VIRTUAL_ENV uv run pytest -q
env -u VIRTUAL_ENV uv run pre-commit run --all-files
TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest -q tests/integration --run-postgres
```

All three must pass; record counts in the report. Acceptance grep (must return only files under `agentic_trader/risk/`): `grep -rn "max_risk_pct_cap \*\|DEFAULT_CORRELATION_GROUPS\|is_open and .*is_rth\|timedelta(minutes=pre" agentic_trader --include='*.py'` (allow `config.py`'s definition of the default groups).

- [ ] **Step 4: Commit**

```bash
git add docs/risk-policy.md docs/production.md docs/entry-capacity.md docs/architecture-review.md docs/alpha-roadmap.md CLAUDE.md agentic_trader/
git commit -m "docs: risk policy contract, one implementation per rule; L2 implemented

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
