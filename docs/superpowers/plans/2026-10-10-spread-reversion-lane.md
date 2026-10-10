# Spread-Reversion Lane (pairs v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A research-only, predeclared pairs-trading protocol (Engle-Granger formation, z-score trading, one-use confirmation window) with its own power and null checks, run by three `copilot alpha spread-*` commands.

**Architecture:** New package `agentic_trader/research/spread/` of pure functions over aligned daily close/open panels (`protocol → schedule → formation → trading → evaluate`), one executor module that writes manifests before I/O and never raises, a lane-generic one-use confirmation interval in `AlphaRepository`, and a thin CLI module reusing the a priori lane's SIP clients and the pooled bar cache. Nothing touches the registry, broker, Telegram or `/pairs`.

**Tech Stack:** Python 3.14, pydantic v2 frozen models, numpy/pandas, `statsmodels.tsa.stattools.coint`, existing `_fetch_cached`/`_claim_cache_range`, `_stationary_index_draws`, `save_json_report`, click + `coro`, pytest (`temp_db` SQLite fixture, `CliRunner`).

**Spec:** `docs/superpowers/specs/2026-10-10-spread-reversion-lane-design.md`

## Global Constraints

- Research only: no registry, broker, Telegram, order, `/pairs` or scheduler change; every result dict carries `"authorizes_promotion": False`.
- Protocol and cohort files are frozen: pydantic `frozen=True, extra="forbid"`, identity = SHA-256 of file bytes; `pairs-v1.json` pins `cohort_sha256`; the loaders refuse a mismatch.
- Manifest before I/O: each executor does `directory.mkdir(mode=0o700, parents=True, exist_ok=False)`, writes `protocol.json` then `manifest.json`, then calls `build()`; failures become `{"status": "failed", "error": "TypeName: message"}` in `result.json`, never a raise.
- The confirmation window is read only after `consume_lane_confirmation` returns; discovery never reads a confirmation session.
- Causality: formation uses only formation sessions; `α, β, σ_f` are frozen for the trading window; fills at the next session's open; eligibility uses only formation data.
- No `family/all` trial charge. No `BarEvidenceStore`. No new migrations (schema head stays `008_alpha_pipeline`).
- Secrets, DSNs and chat identifiers never appear in manifests, results or logs; journal identity is `{scope, dialect, database}` only.
- Heavy work runs in `asyncio.to_thread`; the executors are `async`.
- Lint is strict: `ruff` (C408 — no `dict()` literals, PLC0415 — top-level imports only, RUF059, PLR0133), `mypy`, `pre-commit run --all-files` green before each commit. Python 3.14: `env -u VIRTUAL_ENV uv run ...`.
- Tests: no network, no PostgreSQL (SQLite `temp_db` fixture), deterministic seeds, each task commits its tests with its implementation.
- Docs ship in this PR (no docs-only PRs): `docs/alpha-spread-lane.md`, CLAUDE.md, roadmap, strategies.md, cli-reference.md.

## Review Focus

1. **A trading session with a missing bar on one leg while a pair is open** — the pair must be marked with the last available close (0 P&L that day), emit no signal, and still exit correctly later; a window-end close-out on a missing last bar uses the last available close. (Task 3 tests `test_gap_inside_open_position`, `test_window_end_with_missing_last_bar`.)
2. **A formation window with fewer than `min_eligible_names`** — the stage must fail closed as `coverage_failed`, not run on a thin panel. (Task 7 `test_stage_fails_closed_on_thin_coverage`.)
3. **A discovery pass followed by a confirmation refusal (interval already consumed)** — the study must record `failed` with the refusal reason and must not have read confirmation bars. (Task 7 `test_study_records_confirmation_refusal_without_reading_confirmation`.)
4. **σ_f of zero or a non-finite `coint` statistic** (identical series, constant prices) — `fit_pair` returns an ineligible fit, never NaN z-scores downstream. (Task 2 `test_degenerate_pair_is_ineligible`.)
5. **The lane series when fewer than `top_pairs` pairs qualify** — flat slots earn 0 and the divisor stays `top_pairs` (committed capital), so a single lucky pair cannot carry the lane. (Task 3 `test_lane_series_divides_by_slots`.)

## File map

| File | Responsibility |
| --- | --- |
| `agentic_trader/research/spread/__init__.py` | package docstring only |
| `agentic_trader/research/spread/protocol.py` | frozen models, loaders, `same_sector_pairs` |
| `agentic_trader/research/spread/schedule.py` | `Window`, `stage_windows` |
| `agentic_trader/research/spread/formation.py` | `PairFit`, `half_life`, `fit_pair`, `eligible_symbols`, `select_pairs` |
| `agentic_trader/research/spread/trading.py` | `Trade`, `PairSimulation`, `simulate_pair`, `lane_series`, `trade_net_return` |
| `agentic_trader/research/spread/evaluate.py` | `evaluate_stage` (S1–S4, descriptives) |
| `agentic_trader/research/spread/panel.py` | `SpreadPanel`, `PanelBuild`, `build_spread_panel`, `shift_panel`, `synthetic_panel` |
| `agentic_trader/research/spread/study.py` | `run_stage`, `spread_gate`, `execute_spread_power`, `execute_spread_null`, `execute_spread_study` |
| `agentic_trader/storage/alpha.py` | `consume_lane_confirmation` |
| `agentic_trader/cli/commands/alpha_spread.py` + `cli/main.py` | `spread-power`, `spread-null`, `spread-study` |
| `config/research/spread/cohort-v1.json`, `pairs-v1.json` | frozen files |
| `tests/research/spread/` | one test module per source module |
| docs listed above | documentation |

---

### Task 1: Protocol models and frozen files

**Files:**
- Create: `agentic_trader/research/spread/__init__.py`, `agentic_trader/research/spread/protocol.py`
- Create: `config/research/spread/cohort-v1.json`, `config/research/spread/pairs-v1.json`
- Test: `tests/research/spread/__init__.py`, `tests/research/spread/test_protocol.py`

**Interfaces:**
- Produces: `SpreadCohort` (`.symbols`, `.pairs()` → `tuple[tuple[str, str, str], ...]` of `(y, x, sector)` with `y < x`), `LoadedSpreadCohort(cohort, sha256, path)`, `load_spread_cohort(path)`, `SpreadProtocol` with nested `BarRange(start, through)`, `StageWindows(discovery, confirmation)`, `Schedule(formation_sessions, trading_sessions)`, `FormationRule(coint_max_lag, p_value, half_life_sessions, hedge_ratio_abs, top_pairs, min_eligible_names)`, `TradingRule(z_entry, z_exit, z_stop, fill)`, `PassRules(min_closed_trades, min_windows, min_positive_window_fraction, max_abs_market_beta, trim_fraction)`, `PowerSpec(seeds, min_pass, planted_pairs, half_life_sessions, innovation_std, hedge_ratio, market_vol, sector_vol, idiosyncratic_vol, overnight_vol)`, `NullSpec(seeds, max_pass, shift_block_sessions)`, `LoadedSpreadProtocol(protocol, sha256, path)`, `load_spread_protocol(path)`. `BootstrapSpec` is reused from `agentic_trader.research.apriori.catalog`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_protocol.py
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.spread.protocol import (
    SpreadCohort,
    SpreadProtocol,
    load_spread_cohort,
    load_spread_protocol,
)


REPO = Path(__file__).resolve().parents[3]
COHORT_PATH = REPO / "config/research/spread/cohort-v1.json"
PROTOCOL_PATH = REPO / "config/research/spread/pairs-v1.json"


def _cohort(**overrides) -> dict:
    base = {
        "id": "spread-cohort",
        "version": 1,
        "survivorship": "test",
        "market": "SPY",
        "sectors": {"tech": ["AAPL", "MSFT", "NVDA"], "energy": ["XOM", "CVX"]},
    }
    return {**base, **overrides}


def test_cohort_pairs_are_same_sector_sorted_and_unique():
    cohort = SpreadCohort.model_validate(_cohort())
    assert cohort.symbols == ("AAPL", "CVX", "MSFT", "NVDA", "XOM")
    assert cohort.pairs() == (
        ("CVX", "XOM", "energy"),
        ("AAPL", "MSFT", "tech"),
        ("AAPL", "NVDA", "tech"),
        ("MSFT", "NVDA", "tech"),
    )


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"sectors": {"tech": ["AAPL"], "energy": ["XOM", "CVX"]}}, "at least two"),
        ({"sectors": {"tech": ["AAPL", "XOM"], "energy": ["XOM", "CVX"]}}, "more than one sector"),
        ({"market": "AAPL"}, "market"),
        ({"sectors": {"tech": ["aapl", "MSFT"]}}, "symbol"),
        ({"extra": 1}, "[Ee]xtra"),
    ],
)
def test_cohort_rejects_invalid_shapes(overrides, match):
    with pytest.raises(ValidationError, match=match):
        SpreadCohort.model_validate(_cohort(**overrides))


def test_frozen_files_load_and_pin_each_other():
    cohort = load_spread_cohort(COHORT_PATH)
    loaded = load_spread_protocol(PROTOCOL_PATH)
    assert cohort.sha256 == hashlib.sha256(COHORT_PATH.read_bytes()).hexdigest()
    assert loaded.protocol.cohort_sha256 == cohort.sha256
    assert loaded.protocol.cohort == "config/research/spread/cohort-v1.json"
    assert len(cohort.cohort.symbols) == 127 and len(cohort.cohort.sectors) == 11
    assert len(cohort.cohort.pairs()) == 927
    assert cohort.cohort.market == "SPY" and "SPY" not in cohort.cohort.symbols
    p = loaded.protocol
    assert p.windows.discovery == (date(2017, 1, 3), date(2023, 12, 29))
    assert p.windows.confirmation == (date(2024, 1, 2), date(2026, 7, 31))
    assert p.schedule.formation_sessions == 252 and p.schedule.trading_sessions == 126
    assert p.formation.top_pairs == 20 and p.formation.coint_max_lag == 1
    assert p.trading.z_entry == 2.0 and p.trading.z_exit == 0.5 and p.trading.z_stop == 4.0
    assert p.decision_cost_bps == 5.0 and p.decision_cost_bps in p.costs_bps_per_side
    assert p.pass_rules.min_closed_trades == 100 and p.confirmation_rules.min_closed_trades == 30


def _protocol_doc() -> dict:
    return json.loads(PROTOCOL_PATH.read_text())


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["windows"].update(confirmation=["2023-06-01", "2026-07-31"]), "confirmation"),
        (lambda d: d["windows"].update(discovery=["2015-01-05", "2023-12-29"]), "bars"),
        (lambda d: d["trading"].update(z_exit=2.5), "z_exit"),
        (lambda d: d["formation"].update(half_life_sessions=[42, 5]), "half_life"),
        (lambda d: d.update(decision_cost_bps=7.0), "costs_bps_per_side"),
        (lambda d: d["power"].update(min_pass=11), "min_pass"),
        (lambda d: d["null_check"].update(max_pass=10), "max_pass"),
        (lambda d: d.update(cohort_sha256="0" * 63), "cohort_sha256"),
    ],
)
def test_protocol_cross_field_rules(mutate, match):
    doc = _protocol_doc()
    mutate(doc)
    with pytest.raises(ValidationError, match=match):
        SpreadProtocol.model_validate(doc)


def test_loader_rejects_cohort_hash_mismatch(tmp_path):
    doc = _protocol_doc()
    doc["cohort_sha256"] = "0" * 64
    path = tmp_path / "p.json"
    path.write_text(json.dumps(doc))
    loaded = load_spread_protocol(path)
    cohort = load_spread_cohort(COHORT_PATH)
    assert loaded.protocol.cohort_sha256 != cohort.sha256  # the executor refuses this pair (Task 7)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_protocol.py -q`
Expected: FAIL with `ModuleNotFoundError: agentic_trader.research.spread`.

- [ ] **Step 3: Write the models**

```python
# agentic_trader/research/spread/__init__.py
"""Spread-reversion lane: a predeclared, research-only pairs protocol on daily SIP bars."""
```

```python
# agentic_trader/research/spread/protocol.py
"""Frozen protocol and cohort files for the spread-reversion lane.

Identity is the SHA-256 of the file bytes; a changed file is a new version. The protocol
pins its cohort's hash; executors refuse a cohort whose hash differs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.apriori.catalog import BootstrapSpec


__all__ = [
    "BarRange",
    "FormationRule",
    "LoadedSpreadCohort",
    "LoadedSpreadProtocol",
    "NullSpec",
    "PassRules",
    "PowerSpec",
    "Schedule",
    "SpreadCohort",
    "SpreadProtocol",
    "StageWindows",
    "TradingRule",
    "load_spread_cohort",
    "load_spread_protocol",
]

SYMBOL = re.compile(r"^[A-Z]{1,5}$")
LANE = "spread"


class SpreadCohort(BaseModel, frozen=True, extra="forbid"):
    id: Literal["spread-cohort"]
    version: int = Field(ge=1)
    survivorship: str = Field(min_length=1)
    market: str
    sectors: dict[str, tuple[str, ...]]

    @field_validator("market")
    @classmethod
    def _market_symbol(cls, value: str) -> str:
        if not SYMBOL.match(value):
            raise ValueError(f"market symbol {value!r} is not an upper-case ticker")
        return value

    @model_validator(mode="after")
    def _well_formed(self) -> SpreadCohort:
        seen: dict[str, str] = {}
        for sector, members in self.sectors.items():
            if len(members) < 2:
                raise ValueError(f"sector {sector!r} needs at least two symbols")
            for symbol in members:
                if not SYMBOL.match(symbol):
                    raise ValueError(f"symbol {symbol!r} in sector {sector!r} is not an upper-case ticker")
                if symbol in seen:
                    raise ValueError(f"symbol {symbol!r} appears in more than one sector ({seen[symbol]}, {sector})")
                seen[symbol] = sector
        if self.market in seen:
            raise ValueError("market symbol must not be a cohort member")
        return self

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(s for members in self.sectors.values() for s in members))

    def pairs(self) -> tuple[tuple[str, str, str], ...]:
        """Same-sector pairs ``(y, x, sector)`` with ``y < x``; sectors and pairs in sorted order."""
        out: list[tuple[str, str, str]] = []
        for sector in sorted(self.sectors):
            members = sorted(self.sectors[sector])
            for i, y in enumerate(members):
                out.extend((y, x, sector) for x in members[i + 1 :])
        return tuple(out)


@dataclass(frozen=True)
class LoadedSpreadCohort:
    cohort: SpreadCohort
    sha256: str
    path: Path


def load_spread_cohort(path: Path) -> LoadedSpreadCohort:
    raw = path.read_bytes()
    return LoadedSpreadCohort(SpreadCohort.model_validate_json(raw), hashlib.sha256(raw).hexdigest(), path)


class BarRange(BaseModel, frozen=True, extra="forbid"):
    start: date
    through: date

    @model_validator(mode="after")
    def _ordered(self) -> BarRange:
        if self.start >= self.through:
            raise ValueError("bars.start must precede bars.through")
        return self


class StageWindows(BaseModel, frozen=True, extra="forbid"):
    discovery: tuple[date, date]
    confirmation: tuple[date, date]

    @model_validator(mode="after")
    def _ordered(self) -> StageWindows:
        for name, (start, end) in (("discovery", self.discovery), ("confirmation", self.confirmation)):
            if start >= end:
                raise ValueError(f"{name} window must be ordered (start < end)")
        if self.discovery[1] >= self.confirmation[0]:
            raise ValueError("confirmation window must start after the discovery window ends")
        return self


class Schedule(BaseModel, frozen=True, extra="forbid"):
    formation_sessions: int = Field(ge=20)
    trading_sessions: int = Field(ge=5)


class FormationRule(BaseModel, frozen=True, extra="forbid"):
    coint_max_lag: int = Field(ge=0)
    p_value: float = Field(gt=0, lt=1)
    half_life_sessions: tuple[float, float]
    hedge_ratio_abs: tuple[float, float]
    top_pairs: int = Field(ge=1)
    min_eligible_names: int = Field(ge=2)

    @model_validator(mode="after")
    def _ranges(self) -> FormationRule:
        if not 0 < self.half_life_sessions[0] < self.half_life_sessions[1]:
            raise ValueError("half_life_sessions must be an ordered positive range")
        if not 0 < self.hedge_ratio_abs[0] < self.hedge_ratio_abs[1]:
            raise ValueError("hedge_ratio_abs must be an ordered positive range")
        return self


class TradingRule(BaseModel, frozen=True, extra="forbid"):
    z_entry: float = Field(gt=0)
    z_exit: float = Field(ge=0)
    z_stop: float = Field(gt=0)
    fill: Literal["next_open"]

    @model_validator(mode="after")
    def _bands(self) -> TradingRule:
        if not self.z_exit < self.z_entry < self.z_stop:
            raise ValueError("bands must satisfy z_exit < z_entry < z_stop")
        return self


class PassRules(BaseModel, frozen=True, extra="forbid"):
    min_closed_trades: int = Field(ge=1)
    min_windows: int = Field(ge=1)
    min_positive_window_fraction: float = Field(gt=0, le=1)
    max_abs_market_beta: float = Field(gt=0)
    trim_fraction: float = Field(ge=0, lt=0.5)


class PowerSpec(BaseModel, frozen=True, extra="forbid"):
    seeds: int = Field(ge=1)
    min_pass: int = Field(ge=1)
    planted_pairs: int = Field(ge=1)
    half_life_sessions: float = Field(gt=0)
    innovation_std: float = Field(gt=0)
    hedge_ratio: tuple[float, float]
    market_vol: float = Field(gt=0)
    sector_vol: float = Field(gt=0)
    idiosyncratic_vol: float = Field(gt=0)
    overnight_vol: float = Field(gt=0)

    @model_validator(mode="after")
    def _bounds(self) -> PowerSpec:
        if self.min_pass > self.seeds:
            raise ValueError("min_pass cannot exceed seeds")
        if not 0 < self.hedge_ratio[0] <= self.hedge_ratio[1]:
            raise ValueError("hedge_ratio must be an ordered positive range")
        return self


class NullSpec(BaseModel, frozen=True, extra="forbid"):
    seeds: int = Field(ge=1)
    max_pass: int = Field(ge=0)
    shift_block_sessions: int = Field(ge=1)

    @model_validator(mode="after")
    def _bounds(self) -> NullSpec:
        if self.max_pass >= self.seeds:
            raise ValueError("max_pass must be below seeds")
        return self


class SpreadProtocol(BaseModel, frozen=True, extra="forbid"):
    """The frozen pairs protocol (lane ``spread``, entry ``spread-pairs``)."""

    id: Literal["spread-pairs"]
    version: int = Field(ge=1)
    title: str = Field(min_length=1)
    references: tuple[str, ...]
    hypotheses: str = Field(min_length=1)
    decision_rule: str = Field(min_length=1)
    cohort: str = Field(min_length=1)
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    bars: BarRange
    windows: StageWindows
    schedule: Schedule
    formation: FormationRule
    trading: TradingRule
    costs_bps_per_side: tuple[float, ...] = Field(min_length=1)
    decision_cost_bps: float = Field(ge=0)
    bootstrap: BootstrapSpec
    pass_rules: PassRules
    confirmation_rules: PassRules
    power: PowerSpec
    null_check: NullSpec

    @model_validator(mode="after")
    def _consistent(self) -> SpreadProtocol:
        if any(c < 0 for c in self.costs_bps_per_side):
            raise ValueError("costs_bps_per_side must be non-negative")
        if self.decision_cost_bps not in self.costs_bps_per_side:
            raise ValueError("decision_cost_bps must be one of costs_bps_per_side")
        if not self.bars.start <= self.windows.discovery[0]:
            raise ValueError("discovery window must start inside the bars range")
        if not self.windows.confirmation[1] <= self.bars.through:
            raise ValueError("confirmation window must end inside the bars range")
        return self

    def stage(self, name: Literal["discovery", "confirmation"]) -> tuple[date, date]:
        return self.windows.discovery if name == "discovery" else self.windows.confirmation

    def rules(self, name: Literal["discovery", "confirmation"]) -> PassRules:
        return self.pass_rules if name == "discovery" else self.confirmation_rules


@dataclass(frozen=True)
class LoadedSpreadProtocol:
    protocol: SpreadProtocol
    sha256: str
    path: Path


def load_spread_protocol(path: Path) -> LoadedSpreadProtocol:
    raw = path.read_bytes()
    return LoadedSpreadProtocol(SpreadProtocol.model_validate_json(raw), hashlib.sha256(raw).hexdigest(), path)
```

- [ ] **Step 4: Generate the cohort file (one-off script, run once, do not commit the script)**

```bash
mkdir -p config/research/spread
env -u VIRTUAL_ENV uv run python - <<'PY'
import json, yaml
from pathlib import Path
pooled = set(json.load(open("config/research/pooled/cohort-v2.json"))["symbols"])
universe = yaml.safe_load(open("config/config.yaml"))["universe"]
tagged: dict[str, str] = {}
def walk(node):
    if isinstance(node, dict):
        if "symbol" in node and "sector" in node:
            tagged[node["symbol"]] = node["sector"]
        else:
            for value in node.values():
                walk(value)
    elif isinstance(node, list):
        for value in node:
            walk(value)
walk(universe)
sectors: dict[str, list[str]] = {}
for symbol, sector in sorted(tagged.items()):
    if sector.startswith("etf_") or symbol not in pooled:
        continue
    sectors.setdefault(sector, []).append(symbol)
doc = {
    "id": "spread-cohort",
    "version": 1,
    "survivorship": "config/config.yaml universe equities (groups mega_caps + research_cohort) that are members of config/research/pooled/cohort-v2.json, with config.yaml sector tags; listed and active on 2026-10-10; not point-in-time",
    "market": "SPY",
    "sectors": {sector: sorted(members) for sector, members in sorted(sectors.items())},
}
Path("config/research/spread/cohort-v1.json").write_text(json.dumps(doc, indent=2) + "\n")
print({s: len(m) for s, m in doc["sectors"].items()}, sum(len(m) for m in doc["sectors"].values()))
PY
shasum -a 256 config/research/spread/cohort-v1.json
```

Expected: 127 symbols over 11 sectors (technology 26, financial_services 19, consumer_cyclical 15, industrials 15, healthcare 14, communication_services 9, consumer_defensive 9, energy 8, basic_materials 8, real_estate 2, utilities 2). If the counts differ, stop and report (the spec and tests assume these).

- [ ] **Step 5: Write the protocol file**, substituting the printed cohort hash for `COHORT_SHA256`:

```json
{
  "id": "spread-pairs",
  "version": 1,
  "title": "Same-sector pairs: Engle-Granger formation, z-score reversion trading (Gatev-style 252/126 tiles)",
  "references": [
    "Gatev, Goetzmann and Rouwenhorst (2006), Pairs Trading: Performance of a Relative-Value Arbitrage Rule, RFS 19(3)",
    "Engle and Granger (1987), Co-integration and Error Correction, Econometrica 55(2)",
    "Do and Faff (2010), Does Simple Pairs Trading Still Work?, FAJ 66(4)",
    "Vidyamurthy (2004), Pairs Trading: Quantitative Methods and Analysis"
  ],
  "hypotheses": "Among same-sector US large-cap equities, pairs whose log prices are cointegrated over the previous 252 sessions (Engle-Granger, p<0.05, half-life 5-42 sessions) have spreads that revert over the next 126 sessions, so that entering at |z|>=2 and exiting at |z|<=0.5 (stop |z|>=4, window-end close-out), dollar-neutral at the formation hedge ratio, earns a positive mean return after 5 bp per side per leg with market beta near zero.",
  "decision_rule": "Discovery (trading windows 2017-01-03..2023-12-29) passes when S1 (lane mean daily return > 0, stationary-bootstrap ci90 lower bound > 0), S2 (closed-trade mean and 1%-trimmed mean > 0), S3 (>=100 closed trades, >=8 windows, >=60% of windows positive) and S4 (|beta to SPY| <= 0.2) all hold at 5 bp per side. Only then is the confirmation window (2024-01-02..2026-07-31) journaled as consumed and read; the same rule with S3 relaxed to >=30 trades and >=4 windows decides confirmed vs failed_confirmation. Nothing promotes.",
  "cohort": "config/research/spread/cohort-v1.json",
  "cohort_sha256": "COHORT_SHA256",
  "feed": "alpaca:sip",
  "adjustment": "all",
  "bars": {"start": "2016-01-04", "through": "2026-08-31"},
  "windows": {"discovery": ["2017-01-03", "2023-12-29"], "confirmation": ["2024-01-02", "2026-07-31"]},
  "schedule": {"formation_sessions": 252, "trading_sessions": 126},
  "formation": {
    "coint_max_lag": 1,
    "p_value": 0.05,
    "half_life_sessions": [5, 42],
    "hedge_ratio_abs": [0.25, 4.0],
    "top_pairs": 20,
    "min_eligible_names": 60
  },
  "trading": {"z_entry": 2.0, "z_exit": 0.5, "z_stop": 4.0, "fill": "next_open"},
  "costs_bps_per_side": [0.0, 5.0, 10.0],
  "decision_cost_bps": 5.0,
  "bootstrap": {"block_mean": 10, "draws": 2000, "seed": 20261010},
  "pass_rules": {
    "min_closed_trades": 100,
    "min_windows": 8,
    "min_positive_window_fraction": 0.6,
    "max_abs_market_beta": 0.2,
    "trim_fraction": 0.01
  },
  "confirmation_rules": {
    "min_closed_trades": 30,
    "min_windows": 4,
    "min_positive_window_fraction": 0.6,
    "max_abs_market_beta": 0.2,
    "trim_fraction": 0.01
  },
  "power": {
    "seeds": 10,
    "min_pass": 8,
    "planted_pairs": 30,
    "half_life_sessions": 20.0,
    "innovation_std": 0.008,
    "hedge_ratio": [0.6, 1.6],
    "market_vol": 0.010,
    "sector_vol": 0.007,
    "idiosyncratic_vol": 0.012,
    "overnight_vol": 0.003
  },
  "null_check": {"seeds": 10, "max_pass": 1, "shift_block_sessions": 63}
}
```

- [ ] **Step 6: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_protocol.py -q`
Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/research/spread/__init__.py agentic_trader/research/spread/protocol.py tests/research/spread/__init__.py tests/research/spread/test_protocol.py config/research/spread/cohort-v1.json config/research/spread/pairs-v1.json
git add agentic_trader/research/spread/__init__.py agentic_trader/research/spread/protocol.py tests/research/spread/__init__.py tests/research/spread/test_protocol.py config/research/spread/cohort-v1.json config/research/spread/pairs-v1.json
git commit -m "Spread lane: frozen pairs protocol and same-sector cohort (127 names, 927 pairs)"
```

---

### Task 2: Schedule and formation

**Files:**
- Create: `agentic_trader/research/spread/schedule.py`, `agentic_trader/research/spread/formation.py`
- Test: `tests/research/spread/test_schedule.py`, `tests/research/spread/test_formation.py`

**Interfaces:**
- Consumes: `FormationRule` (Task 1).
- Produces: `Window(index, formation, trading)` with half-open position ranges into a session sequence; `stage_windows(sessions, stage, *, formation_sessions, trading_sessions) -> tuple[Window, ...]`; `CoverageError(ValueError)`; `PairFit(y, x, sector, alpha, beta, sigma, t_stat, p_value, half_life, eligible, reason)`; `half_life(residual) -> float`; `fit_pair(log_y, log_x, rule, *, y, x, sector) -> PairFit`; `eligible_symbols(closes, *, min_eligible) -> tuple[str, ...]`; `select_pairs(closes_formation, pairs, rule) -> tuple[tuple[PairFit, ...], dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_schedule.py
from datetime import date, timedelta

from agentic_trader.research.spread.schedule import Window, stage_windows


def weekdays(start: date, end: date) -> tuple[date, ...]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


SESSIONS = weekdays(date(2016, 1, 4), date(2026, 8, 31))


def test_discovery_tiles_are_whole_contiguous_and_inside_the_stage():
    stage = (date(2017, 1, 3), date(2023, 12, 29))
    windows = stage_windows(SESSIONS, stage, formation_sessions=252, trading_sessions=126)
    assert windows and all(isinstance(w, Window) for w in windows)
    first = windows[0]
    assert SESSIONS[first.trading[0]] >= stage[0]
    assert first.formation == (first.trading[0] - 252, first.trading[0])
    for a, b in zip(windows, windows[1:], strict=False):
        assert a.trading[1] == b.trading[0] and b.index == a.index + 1
        assert b.formation[1] == b.trading[0] and b.formation[1] - b.formation[0] == 252
    last = windows[-1]
    assert last.trading[1] - last.trading[0] == 126
    assert SESSIONS[last.trading[1] - 1] <= stage[1]
    # the partial tail is dropped, so one more window would cross the stage end
    assert last.trading[1] + 126 > sum(1 for s in SESSIONS if s <= stage[1])
    assert 12 <= len(windows) <= 15


def test_confirmation_tiles_start_at_the_stage_start_and_reach_back_for_formation():
    stage = (date(2024, 1, 2), date(2026, 7, 31))
    windows = stage_windows(SESSIONS, stage, formation_sessions=252, trading_sessions=126)
    assert SESSIONS[windows[0].trading[0]] == date(2024, 1, 2)
    assert SESSIONS[windows[0].formation[0]] < date(2024, 1, 2)
    assert 4 <= len(windows) <= 6


def test_insufficient_history_starts_at_the_first_session_with_enough_formation():
    windows = stage_windows(
        SESSIONS, (date(2016, 1, 4), date(2017, 12, 29)), formation_sessions=252, trading_sessions=126
    )
    assert windows[0].trading[0] == 252 and windows[0].formation == (0, 252)


def test_a_stage_too_short_for_one_window_has_no_windows():
    assert (
        stage_windows(SESSIONS, (date(2026, 7, 1), date(2026, 8, 31)), formation_sessions=252, trading_sessions=126)
        == ()
    )
```

```python
# tests/research/spread/test_formation.py
import math

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.spread.formation import (
    CoverageError,
    PairFit,
    eligible_symbols,
    fit_pair,
    half_life,
    select_pairs,
)
from agentic_trader.research.spread.protocol import FormationRule


RULE = FormationRule(
    coint_max_lag=1,
    p_value=0.05,
    half_life_sessions=(5, 42),
    hedge_ratio_abs=(0.25, 4.0),
    top_pairs=20,
    min_eligible_names=2,
)


def random_walk(rng: np.random.Generator, n: int, vol: float = 0.01, start: float = math.log(100.0)) -> np.ndarray:
    return start + np.cumsum(rng.normal(0.0, vol, n))


def ou(rng: np.random.Generator, n: int, half_life_sessions: float, innovation_std: float) -> np.ndarray:
    phi = 0.5 ** (1.0 / half_life_sessions)
    out = np.empty(n)
    out[0] = 0.0
    for t in range(1, n):
        out[t] = phi * out[t - 1] + rng.normal(0.0, innovation_std)
    return out


def planted(rng: np.random.Generator, n: int = 252, beta: float = 1.2, alpha: float = 0.5, hl: float = 15.0):
    x = random_walk(rng, n)
    return alpha + beta * x + ou(rng, n, hl, 0.01), x


def test_half_life_recovers_a_known_decay():
    rng = np.random.default_rng(1)
    e = ou(rng, 4000, 20.0, 0.01)
    assert 15 < half_life(e) < 26
    assert half_life(np.cumsum(rng.normal(size=400))) == math.inf  # a random walk never reverts
    assert half_life(np.array([0.0, 1.0, 0.0])) == math.inf  # too short


def test_fit_pair_recovers_a_planted_cointegrated_pair():
    log_y, log_x = planted(np.random.default_rng(2))
    fit = fit_pair(log_y, log_x, RULE, y="AAA", x="BBB", sector="s")
    assert isinstance(fit, PairFit) and fit.eligible and fit.reason == ""
    assert abs(fit.beta - 1.2) < 0.15 and abs(fit.alpha - 0.5) < 0.8
    assert fit.p_value < 0.05 and fit.t_stat < -3.0
    assert 5 <= fit.half_life <= 42 and fit.sigma > 0


def test_independent_random_walks_rarely_pass():
    passes = 0
    for seed in range(200):
        rng = np.random.default_rng(1000 + seed)
        fit = fit_pair(random_walk(rng, 252), random_walk(rng, 252), RULE, y="A", x="B", sector="s")
        passes += fit.eligible
    assert passes <= 30  # nominal 5% plus half-life/hedge filters; a loose bound against flakiness


@pytest.mark.parametrize(
    "make, reason",
    [
        (lambda: (np.full(252, 4.6), np.full(252, 4.6)), "constant"),
        (lambda: (random_walk(np.random.default_rng(3), 252),) * 2, "degenerate"),
        (
            lambda: (
                np.r_[random_walk(np.random.default_rng(4), 251), np.nan],
                random_walk(np.random.default_rng(5), 252),
            ),
            "incomplete",
        ),
        (lambda: (random_walk(np.random.default_rng(6), 12), random_walk(np.random.default_rng(7), 12)), "incomplete"),
    ],
)
def test_degenerate_pair_is_ineligible(make, reason):
    log_y, log_x = make()
    fit = fit_pair(log_y, log_x, RULE, y="A", x="B", sector="s")
    assert not fit.eligible and fit.reason == reason
    assert not math.isnan(fit.sigma) or reason in ("constant", "incomplete")


def test_filters_name_the_failing_rule():
    log_y, log_x = planted(np.random.default_rng(8), beta=6.0)
    fit = fit_pair(log_y, log_x, RULE, y="A", x="B", sector="s")
    assert not fit.eligible and fit.reason == "hedge_ratio"
    log_y, log_x = planted(np.random.default_rng(9), hl=1.5)
    fit = fit_pair(log_y, log_x, RULE, y="A", x="B", sector="s")
    assert not fit.eligible and fit.reason == "half_life"


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(next(iter(columns.values()))))
    return pd.DataFrame({k: np.exp(v) for k, v in columns.items()}, index=index)


def test_eligible_symbols_requires_complete_positive_closes():
    rng = np.random.default_rng(10)
    frame = _frame({"A": random_walk(rng, 50), "B": random_walk(rng, 50), "C": random_walk(rng, 50)})
    frame.loc[frame.index[7], "B"] = np.nan
    frame.loc[frame.index[3], "C"] = 0.0
    assert eligible_symbols(frame, min_eligible=1) == ("A",)
    with pytest.raises(CoverageError, match="1 eligible names"):
        eligible_symbols(frame, min_eligible=2)


def test_select_pairs_ranks_by_t_stat_and_counts():
    rng = np.random.default_rng(11)
    log_y, log_x = planted(rng)
    frame = _frame(
        {
            "AAA": log_y,
            "BBB": log_x,
            "CCC": random_walk(rng, 252),
            "DDD": random_walk(rng, 252),
            "EEE": random_walk(rng, 252),
        }
    )
    frame.loc[frame.index[0], "EEE"] = np.nan
    pairs = (("AAA", "BBB", "tech"), ("AAA", "CCC", "tech"), ("BBB", "CCC", "tech"), ("DDD", "EEE", "energy"))
    selected, counts = select_pairs(frame, pairs, RULE.model_copy(update={"top_pairs": 1}))
    assert [(f.y, f.x) for f in selected] == [("AAA", "BBB")]
    assert counts["eligible_names"] == 4 and counts["pairs_tested"] == 3 and counts["selected"] == 1
    assert counts["pairs_passing"] >= 1 and set(counts["reasons"]) <= {
        "p_value",
        "half_life",
        "hedge_ratio",
        "degenerate",
        "constant",
        "coint_failed",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_schedule.py tests/research/spread/test_formation.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# agentic_trader/research/spread/schedule.py
"""Non-overlapping formation/trading tiles over a session calendar (Gatev-style)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


__all__ = ["Window", "stage_windows"]


@dataclass(frozen=True)
class Window:
    """Half-open position ranges into the session sequence: formation ends where trading starts."""

    index: int
    formation: tuple[int, int]
    trading: tuple[int, int]


def stage_windows(
    sessions: Sequence[date], stage: tuple[date, date], *, formation_sessions: int, trading_sessions: int
) -> tuple[Window, ...]:
    """Trading tiles of ``trading_sessions`` inside ``stage`` (inclusive dates), each preceded by
    ``formation_sessions`` sessions. The first tile starts at the first stage session that has enough
    history; whole tiles only, so a tile that would cross the stage end is dropped."""
    start_pos = max(bisect_left(sessions, stage[0]), formation_sessions)
    end_pos = bisect_right(sessions, stage[1])
    windows: list[Window] = []
    pos = start_pos
    while pos + trading_sessions <= end_pos:
        windows.append(Window(len(windows), (pos - formation_sessions, pos), (pos, pos + trading_sessions)))
        pos += trading_sessions
    return tuple(windows)
```

```python
# agentic_trader/research/spread/formation.py
"""Engle-Granger pair formation on a formation window's log closes (causal: nothing after it)."""

from __future__ import annotations

import math
import warnings
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

from agentic_trader.research.spread.protocol import FormationRule


__all__ = ["CoverageError", "PairFit", "eligible_symbols", "fit_pair", "half_life", "select_pairs"]

_MIN_HALF_LIFE_OBSERVATIONS = 10
_MIN_FIT_OBSERVATIONS = 20


class CoverageError(ValueError):
    """Too few names with complete formation bars; the stage fails closed."""


@dataclass(frozen=True)
class PairFit:
    y: str
    x: str
    sector: str
    alpha: float
    beta: float
    sigma: float
    t_stat: float
    p_value: float
    half_life: float
    eligible: bool
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.y, self.x)


def half_life(residual: np.ndarray) -> float:
    """AR(1) half-life of a residual in sessions: ``Δe_t = c + θ e_{t-1}``; ``inf`` unless ``-1 < θ < 0``."""
    e = np.asarray(residual, dtype=float)
    e = e[np.isfinite(e)]
    if e.size < _MIN_HALF_LIFE_OBSERVATIONS:
        return math.inf
    lag = e[:-1]
    design = np.column_stack([np.ones_like(lag), lag])
    coefficients, *_ = np.linalg.lstsq(design, np.diff(e), rcond=None)
    theta = float(coefficients[1])
    if not math.isfinite(theta) or theta >= 0.0 or theta <= -1.0:
        return math.inf
    return math.log(2.0) / -math.log1p(theta)


def _rejected(y: str, x: str, sector: str, reason: str, **values: float) -> PairFit:
    nan = float("nan")
    return PairFit(
        y=y,
        x=x,
        sector=sector,
        alpha=values.get("alpha", nan),
        beta=values.get("beta", nan),
        sigma=values.get("sigma", nan),
        t_stat=values.get("t_stat", nan),
        p_value=values.get("p_value", nan),
        half_life=values.get("half_life", nan),
        eligible=False,
        reason=reason,
    )


def fit_pair(log_y: np.ndarray, log_x: np.ndarray, rule: FormationRule, *, y: str, x: str, sector: str) -> PairFit:
    """OLS hedge ratio, Engle-Granger ``coint`` (MacKinnon p-values), AR(1) half-life, then the filters.

    The regression direction is fixed by the caller (``y`` is the alphabetically earlier name);
    it is never chosen by fit.
    """
    ly = np.asarray(log_y, dtype=float)
    lx = np.asarray(log_x, dtype=float)
    n = ly.size
    if (
        n != lx.size
        or n < _MIN_FIT_OBSERVATIONS + rule.coint_max_lag
        or not (np.isfinite(ly).all() and np.isfinite(lx).all())
    ):
        return _rejected(y, x, sector, "incomplete")
    if np.ptp(lx) == 0.0 or np.ptp(ly) == 0.0:
        return _rejected(y, x, sector, "constant")
    design = np.column_stack([np.ones(n), lx])
    coefficients, *_ = np.linalg.lstsq(design, ly, rcond=None)
    alpha, beta = float(coefficients[0]), float(coefficients[1])
    residual = ly - alpha - beta * lx
    sigma = float(residual.std(ddof=1))
    if not (math.isfinite(sigma) and sigma > 0.0):
        return _rejected(y, x, sector, "degenerate", alpha=alpha, beta=beta, sigma=sigma)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t_stat, p_value, _ = coint(ly, lx, trend="c", maxlag=rule.coint_max_lag, autolag=None)
    except ValueError, np.linalg.LinAlgError:
        return _rejected(y, x, sector, "coint_failed", alpha=alpha, beta=beta, sigma=sigma)
    t_stat, p_value = float(t_stat), float(p_value)
    if not (math.isfinite(t_stat) and math.isfinite(p_value)):
        return _rejected(y, x, sector, "coint_failed", alpha=alpha, beta=beta, sigma=sigma)
    hl = half_life(residual)
    reason = ""
    if not p_value < rule.p_value:
        reason = "p_value"
    elif not rule.half_life_sessions[0] <= hl <= rule.half_life_sessions[1]:
        reason = "half_life"
    elif not rule.hedge_ratio_abs[0] <= abs(beta) <= rule.hedge_ratio_abs[1]:
        reason = "hedge_ratio"
    return PairFit(
        y=y,
        x=x,
        sector=sector,
        alpha=alpha,
        beta=beta,
        sigma=sigma,
        t_stat=t_stat,
        p_value=p_value,
        half_life=hl,
        eligible=reason == "",
        reason=reason,
    )


def eligible_symbols(closes: pd.DataFrame, *, min_eligible: int) -> tuple[str, ...]:
    """Names with a finite, positive close on every row of ``closes``; too few fail closed."""
    values = closes.to_numpy(dtype=float)
    complete = np.isfinite(values) & (values > 0.0)
    names = tuple(sorted(str(c) for c, ok in zip(closes.columns, complete.all(axis=0), strict=True) if ok))
    if len(names) < min_eligible:
        raise CoverageError(f"{len(names)} eligible names below the protocol minimum of {min_eligible}")
    return names


def select_pairs(
    closes_formation: pd.DataFrame, pairs: Sequence[tuple[str, str, str]], rule: FormationRule
) -> tuple[tuple[PairFit, ...], dict]:
    """Fit every pair with both names eligible; keep the ``top_pairs`` eligible fits ranked by the
    cointegration t-statistic (most negative first, ties by name)."""
    names = eligible_symbols(closes_formation, min_eligible=rule.min_eligible_names)
    eligible = set(names)
    logs = {name: np.log(closes_formation[name].to_numpy(dtype=float)) for name in names}
    fits: list[PairFit] = []
    reasons: Counter[str] = Counter()
    for y, x, sector in pairs:
        if y not in eligible or x not in eligible:
            continue
        fit = fit_pair(logs[y], logs[x], rule, y=y, x=x, sector=sector)
        fits.append(fit)
        if not fit.eligible:
            reasons[fit.reason] += 1
    passing = sorted((f for f in fits if f.eligible), key=lambda f: (f.t_stat, f.y, f.x))
    selected = tuple(passing[: rule.top_pairs])
    counts = {
        "eligible_names": len(names),
        "pairs_tested": len(fits),
        "pairs_passing": len(passing),
        "selected": len(selected),
        "reasons": dict(sorted(reasons.items())),
    }
    return selected, counts
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_schedule.py tests/research/spread/test_formation.py -q`
Expected: PASS. If `test_independent_random_walks_rarely_pass` or the planted bounds fail, inspect the numbers before touching the thresholds: the planted test uses 252 sessions, half-life 15, innovation 1%; the bound 30/200 is deliberately loose.

- [ ] **Step 5: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/research/spread/schedule.py agentic_trader/research/spread/formation.py tests/research/spread/test_schedule.py tests/research/spread/test_formation.py
git add agentic_trader/research/spread/schedule.py agentic_trader/research/spread/formation.py tests/research/spread/test_schedule.py tests/research/spread/test_formation.py
git commit -m "Spread lane: formation/trading tiles and Engle-Granger pair formation (coint, half-life, filters, top-K)"
```

---

### Task 3: Pair trading simulation and lane series

**Files:**
- Create: `agentic_trader/research/spread/trading.py`
- Test: `tests/research/spread/test_trading.py`

**Interfaces:**
- Consumes: `PairFit` (Task 2), `TradingRule` (Task 1).
- Produces: `Trade(window, y, x, sector, side, entry_pos, exit_pos, entry_z, exit_z, exit_reason, gross_return, holding_sessions, beta)`; `PairSimulation(fit, window, trades, daily, turnover)`; `simulate_pair(fit, closes_y, closes_x, opens_y, opens_x, rule, *, window) -> PairSimulation`; `trade_net_return(trade, cost_bps) -> float`; `net_daily(simulation, cost_bps) -> np.ndarray`; `lane_series(simulations, *, slots, cost_bps, length) -> np.ndarray`.

Conventions (from the spec §4.3): positions are indices into the trading window; `z_t = (log Y_t − α − β log X_t)/σ`; side `+1` = long spread (long Y, short X when β > 0), `−1` = short spread; the X leg's sign is `−side · sign(β)`; dollar weights `w_Y = 1/(1+|β|)`, `w_X = |β|/(1+|β|)`; fixed shares, so the daily mark is `Σ_leg sign·w·(P_t − P_prev)/P_entry`; fills at the next session's open (delayed to the next session with both opens when one is missing); a session with a missing close on either leg emits no signal and marks with the last available prices; the window's last session closes any open position at its (last available) close; no entry signal on the last session; `turnover` is 1.0 on each fill session (two legs in or two legs out); net of cost `c` bp per side, a round trip costs `2c/10⁴` of gross.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_trading.py
import math

import numpy as np
import pytest

from agentic_trader.research.spread.formation import PairFit
from agentic_trader.research.spread.protocol import TradingRule
from agentic_trader.research.spread.trading import (
    PairSimulation,
    Trade,
    lane_series,
    net_daily,
    simulate_pair,
    trade_net_return,
)


RULE = TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open")


def fit(beta: float = 1.0) -> PairFit:
    return PairFit(
        y="Y",
        x="X",
        sector="s",
        alpha=0.0,
        beta=beta,
        sigma=1.0,
        t_stat=-4.0,
        p_value=0.01,
        half_life=10.0,
        eligible=True,
        reason="",
    )


def paths(z: list[float], *, x_close: float = 100.0):
    """X constant at ``x_close`` (opens equal closes), Y chosen so that z_t = log Y_t − log X_t."""
    cz = np.array(z, dtype=float)
    cx = np.full(cz.size, x_close)
    cy = x_close * np.exp(cz)
    return cy.copy(), cx.copy(), cy.copy(), cx.copy()  # closes_y, closes_x, opens_y, opens_x


def test_entry_fills_at_next_open_and_exits_on_the_band():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.4, -0.3, 0.0, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=3)
    assert isinstance(sim, PairSimulation) and len(sim.trades) == 1
    trade = sim.trades[0]
    assert isinstance(trade, Trade)
    assert trade.side == 1 and trade.entry_pos == 2 and trade.exit_pos == 5 and trade.exit_reason == "reverted"
    assert (
        trade.entry_z == -2.5
        and trade.exit_z == pytest.approx(-0.3)
        and trade.holding_sessions == 3
        and trade.window == 3
    )
    assert trade.sector == "s" and trade.beta == 1.0
    expected = 0.5 * (oy[5] / oy[2] - 1.0)  # w_Y = 1/(1+|β|) = 0.5; X leg is flat
    assert trade.gross_return == pytest.approx(expected)
    assert sim.daily.sum() == pytest.approx(expected)
    assert sim.turnover.tolist() == [0, 0, 1.0, 0, 0, 1.0, 0, 0]
    assert sim.daily[:2].tolist() == [0.0, 0.0] and sim.daily[2] == pytest.approx(0.5 * (cy[2] / oy[2] - 1.0))


def test_short_spread_and_stop_exit():
    cy, cx, oy, ox = paths([0.0, 2.2, 2.3, 4.1, 4.5, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.side == -1 and trade.entry_pos == 2 and trade.exit_pos == 4 and trade.exit_reason == "stop"
    assert trade.gross_return == pytest.approx(-0.5 * (oy[4] / oy[2] - 1.0))
    assert trade.gross_return < 0


def test_window_end_closes_an_open_position_at_the_last_close():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.5, -2.5])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.exit_reason == "window_end" and trade.exit_pos == 4 and trade.exit_z == pytest.approx(-2.5)
    assert trade.gross_return == pytest.approx(0.5 * (cy[4] / oy[2] - 1.0))
    assert sim.turnover[4] == 1.0


def test_no_entry_signal_on_the_last_session():
    cy, cx, oy, ox = paths([0.0, 0.0, -2.5])
    assert simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0).trades == ()


def test_gap_inside_open_position_marks_flat_and_emits_no_signal():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -0.1, -2.5, -0.2, 0.0, 0.0])
    cy[3] = np.nan  # the band would have been hit here; the gap must not exit
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert sim.daily[3] == 0.0 and trade.exit_pos == 6 and trade.exit_reason == "reverted"
    assert sim.daily.sum() == pytest.approx(trade.gross_return)


def test_window_end_with_missing_last_bar_uses_the_last_available_close():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.4, -2.3])
    cy[4] = np.nan
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.exit_reason == "window_end" and trade.exit_pos == 4 and math.isnan(trade.exit_z)
    assert trade.gross_return == pytest.approx(0.5 * (cy[3] / oy[2] - 1.0))


def test_fill_is_delayed_past_a_missing_open():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.5, 0.0, 0.0, 0.0])
    oy[2] = np.nan
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    assert sim.trades[0].entry_pos == 3 and sim.turnover[2] == 0.0 and sim.turnover[3] == 1.0


def test_re_entry_after_an_exit_within_the_window():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, 0.0, 0.0, 2.5, 2.5, 0.0, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    assert [t.side for t in sim.trades] == [1, -1]
    assert sim.trades[1].entry_pos == 6 and sim.trades[1].exit_pos == 8


def test_negative_beta_flips_the_x_leg():
    # β = -1: spread = log Y + log X; long spread is long both legs
    cy = np.array([100.0, 100.0, 100.0, 100.0, 110.0, 110.0])
    cx = np.array([100.0, 100.0, 100.0, 100.0, 110.0, 110.0])
    oy, ox = cy.copy(), cx.copy()
    sigma = 1.0
    alpha = math.log(100.0) + math.log(100.0) - (-2.5 * sigma)  # makes z_0 = -2.5 at both legs at 100
    f = PairFit(
        y="Y",
        x="X",
        sector="s",
        alpha=alpha,
        beta=-1.0,
        sigma=sigma,
        t_stat=-4.0,
        p_value=0.01,
        half_life=10.0,
        eligible=True,
        reason="",
    )
    sim = simulate_pair(f, cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.side == 1 and trade.entry_pos == 1
    assert trade.gross_return > 0  # both legs rose 10%; a long-long position gains


def test_cost_arithmetic_and_lane_series_divides_by_slots():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -0.3, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade_net_return(trade, 5.0) == pytest.approx(trade.gross_return - 2 * 5.0 / 1e4)
    net = net_daily(sim, 5.0)
    assert net.sum() == pytest.approx(trade_net_return(trade, 5.0))
    lane = lane_series([sim, sim], slots=20, cost_bps=5.0, length=6)
    assert lane.shape == (6,) and lane.sum() == pytest.approx(2 * trade_net_return(trade, 5.0) / 20)
    assert lane_series([], slots=20, cost_bps=5.0, length=6).tolist() == [0.0] * 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_trading.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# agentic_trader/research/spread/trading.py
"""Spread trading over one trading window with frozen formation parameters (causal, next-open fills)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from agentic_trader.research.spread.formation import PairFit
from agentic_trader.research.spread.protocol import TradingRule


__all__ = ["EXIT_REASONS", "PairSimulation", "Trade", "lane_series", "net_daily", "simulate_pair", "trade_net_return"]

EXIT_REASONS = ("reverted", "stop", "window_end")


@dataclass(frozen=True)
class Trade:
    window: int
    y: str
    x: str
    sector: str
    side: int
    entry_pos: int
    exit_pos: int
    entry_z: float
    exit_z: float
    exit_reason: str
    gross_return: float
    holding_sessions: int
    beta: float


@dataclass(frozen=True)
class PairSimulation:
    fit: PairFit
    window: int
    trades: tuple[Trade, ...]
    daily: np.ndarray
    turnover: np.ndarray


def trade_net_return(trade: Trade, cost_bps: float) -> float:
    """Two legs in and two legs out on gross notional 1: a round trip costs ``2c/10⁴``."""
    return trade.gross_return - 2.0 * cost_bps / 1e4


def net_daily(simulation: PairSimulation, cost_bps: float) -> np.ndarray:
    return simulation.daily - simulation.turnover * cost_bps / 1e4


def lane_series(simulations: Sequence[PairSimulation], *, slots: int, cost_bps: float, length: int) -> np.ndarray:
    """Committed-capital lane return: each of ``slots`` pair slots holds 1/slots of capital; flat slots earn 0."""
    out = np.zeros(length, dtype=float)
    for simulation in simulations:
        out += net_daily(simulation, cost_bps)
    return out / float(slots)


@dataclass
class _Position:
    side: int
    entry_pos: int
    entry_z: float
    entry_y: float
    entry_x: float
    last_y: float
    last_x: float


def _weights(beta: float) -> tuple[float, float]:
    return 1.0 / (1.0 + abs(beta)), abs(beta) / (1.0 + abs(beta))


def simulate_pair(
    fit: PairFit,
    closes_y: np.ndarray,
    closes_x: np.ndarray,
    opens_y: np.ndarray,
    opens_x: np.ndarray,
    rule: TradingRule,
    *,
    window: int,
) -> PairSimulation:
    cy, cx = np.asarray(closes_y, dtype=float), np.asarray(closes_x, dtype=float)
    oy, ox = np.asarray(opens_y, dtype=float), np.asarray(opens_x, dtype=float)
    n = cy.size
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (np.log(cy) - fit.alpha - fit.beta * np.log(cx)) / fit.sigma
    w_y, w_x = _weights(fit.beta)
    x_sign = -math.copysign(1.0, fit.beta)
    daily = np.zeros(n, dtype=float)
    turnover = np.zeros(n, dtype=float)
    trades: list[Trade] = []
    position: _Position | None = None
    pending_entry: tuple[int, float] | None = None  # (side, signal z)
    pending_exit: tuple[str, float] | None = None  # (reason, signal z)

    def mark(pos: _Position, price_y: float, price_x: float) -> float:
        value = pos.side * w_y * (price_y - pos.last_y) / pos.entry_y
        value += pos.side * x_sign * w_x * (price_x - pos.last_x) / pos.entry_x
        pos.last_y, pos.last_x = price_y, price_x
        return value

    def close(pos: _Position, t: int, reason: str, exit_z: float) -> None:
        gross = pos.side * w_y * (pos.last_y / pos.entry_y - 1.0)
        gross += pos.side * x_sign * w_x * (pos.last_x / pos.entry_x - 1.0)
        turnover[t] += 1.0
        trades.append(
            Trade(
                window=window,
                y=fit.y,
                x=fit.x,
                sector=fit.sector,
                side=pos.side,
                entry_pos=pos.entry_pos,
                exit_pos=t,
                entry_z=pos.entry_z,
                exit_z=exit_z,
                exit_reason=reason,
                gross_return=gross,
                holding_sessions=t - pos.entry_pos,
                beta=fit.beta,
            )
        )

    for t in range(n):
        opens_ok = math.isfinite(oy[t]) and math.isfinite(ox[t])
        # 1. fills at the open
        if position is not None and pending_exit is not None and opens_ok:
            daily[t] += mark(position, oy[t], ox[t])
            close(position, t, pending_exit[0], pending_exit[1])
            position, pending_exit = None, None
        if position is None and pending_entry is not None and opens_ok:
            side, signal_z = pending_entry
            position = _Position(side, t, signal_z, oy[t], ox[t], oy[t], ox[t])
            turnover[t] += 1.0
            pending_entry = None
        # 2. mark at the close with the last available prices
        if position is not None:
            price_y = cy[t] if math.isfinite(cy[t]) else position.last_y
            price_x = cx[t] if math.isfinite(cx[t]) else position.last_x
            daily[t] += mark(position, price_y, price_x)
        last = t == n - 1
        # 3. signals at the close (a missing close on either leg emits none)
        if math.isfinite(z[t]):
            if position is not None and pending_exit is None:
                if abs(z[t]) <= rule.z_exit:
                    pending_exit = ("reverted", float(z[t]))
                elif abs(z[t]) >= rule.z_stop:
                    pending_exit = ("stop", float(z[t]))
            elif position is None and pending_entry is None and not last:
                if z[t] <= -rule.z_entry:
                    pending_entry = (1, float(z[t]))
                elif z[t] >= rule.z_entry:
                    pending_entry = (-1, float(z[t]))
        # 4. the window's last session closes out at its (last available) close
        if last and position is not None:
            close(position, t, "window_end", float(z[t]) if math.isfinite(z[t]) else float("nan"))
            position = None
    return PairSimulation(fit=fit, window=window, trades=tuple(trades), daily=daily, turnover=turnover)
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_trading.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/research/spread/trading.py tests/research/spread/test_trading.py
git add agentic_trader/research/spread/trading.py tests/research/spread/test_trading.py
git commit -m "Spread lane: pair trading simulation (next-open fills, band/stop/window-end exits, gaps, fixed-share marks) and committed-capital lane series"
```

---

### Task 4: Stage evaluation (S1–S4 and descriptives)

**Files:**
- Create: `agentic_trader/research/spread/evaluate.py`
- Test: `tests/research/spread/test_evaluate.py`

**Interfaces:**
- Consumes: `Trade`, `trade_net_return` (Task 3); `SpreadProtocol`, `PassRules` (Task 1); `_stationary_index_draws` (`agentic_trader.research.setups.study`), `_ci90`, `_p_one_sided_positive` (`agentic_trader.research.setups.baserates`), `trimmed_mean` (`agentic_trader.research.pooled.stats`).
- Produces: `evaluate_stage(trades, lane_by_cost, market, *, windows, protocol, rules) -> dict` with keys `s1, s2, s3, s4` (each with `holds: bool` and its numbers), `passes: bool`, `descriptives` (per-cost table, Sharpe, hit rate, holding, exit reasons, per-window and per-sector tables). `lane_by_cost` maps each cost in `protocol.costs_bps_per_side` to the lane's daily net series; `market` is the market's daily close-to-close return aligned to the lane (NaN allowed).

Rule definitions (predeclared): **S1** mean of the decision-cost lane > 0 and the stationary-bootstrap 90% CI lower bound > 0 (`_stationary_index_draws(n, block_mean, draws, seed)`, mean per draw). **S2** closed-trade mean net return > 0 and `trimmed_mean(nets, trim_fraction)` > 0. **S3** closed trades ≥ `min_closed_trades`; windows with at least one closed trade ≥ `min_windows`; among those windows, the share with positive mean net return ≥ `min_positive_window_fraction`. **S4** |OLS slope| of lane on market over sessions where both are finite ≤ `max_abs_market_beta`; fewer than 30 such sessions → slope NaN and S4 fails. `passes` = all four.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_evaluate.py
import math

import numpy as np
import pytest

from agentic_trader.research.spread.evaluate import evaluate_stage
from agentic_trader.research.spread.protocol import load_spread_protocol
from agentic_trader.research.spread.trading import Trade
from tests.research.spread.test_protocol import PROTOCOL_PATH


PROTOCOL = load_spread_protocol(PROTOCOL_PATH).protocol
RULES = PROTOCOL.pass_rules


def trade(window: int, gross: float, sector: str = "tech", reason: str = "reverted") -> Trade:
    return Trade(
        window=window,
        y="A",
        x="B",
        sector=sector,
        side=1,
        entry_pos=1,
        exit_pos=4,
        entry_z=-2.1,
        exit_z=0.2,
        exit_reason=reason,
        gross_return=gross,
        holding_sessions=3,
        beta=1.0,
    )


def lanes(values: np.ndarray) -> dict[float, np.ndarray]:
    return {cost: values - cost * 1e-6 for cost in PROTOCOL.costs_bps_per_side}


def good_trades(n: int = 120, windows: int = 10) -> list[Trade]:
    return [trade(i % windows, 0.02 if i % 5 else -0.01) for i in range(n)]


def test_all_rules_hold_on_a_clearly_positive_stage():
    rng = np.random.default_rng(0)
    lane = 0.0008 + rng.normal(0.0, 0.002, 1200)
    market = rng.normal(0.0, 0.01, 1200)
    result = evaluate_stage(good_trades(), lanes(lane), market, windows=10, protocol=PROTOCOL, rules=RULES)
    assert result["passes"] and all(result[k]["holds"] for k in ("s1", "s2", "s3", "s4"))
    assert result["s1"]["ci90"][0] > 0 and result["s1"]["n_sessions"] == 1200
    assert result["s3"]["closed_trades"] == 120 and result["s3"]["windows_with_trades"] == 10
    assert result["s3"]["positive_window_fraction"] == 1.0
    assert abs(result["s4"]["beta"]) < 0.2 and math.isfinite(result["s4"]["se_hac"])
    d = result["descriptives"]
    assert set(d["by_cost"]) == {"0.0", "5.0", "10.0"} and d["by_cost"]["5.0"]["closed_trades"] == 120
    assert d["by_cost"]["5.0"]["mean_trade_net"] < d["by_cost"]["0.0"]["mean_trade_net"]
    assert d["exit_reasons"] == {"reverted": 120} and d["mean_holding_sessions"] == 3.0
    assert len(d["windows"]) == 10 and d["windows"][0]["window"] == 0 and d["windows"][0]["trades"] == 12
    assert d["sectors"]["tech"]["trades"] == 120 and d["stage_windows"] == 10
    assert math.isfinite(d["sharpe_annualised"]) and d["hit_rate"] == pytest.approx(0.8)


def test_s1_fails_on_a_zero_mean_lane():
    lane = np.tile([0.001, -0.001], 600)
    result = evaluate_stage(good_trades(), lanes(lane), np.zeros(1200), windows=10, protocol=PROTOCOL, rules=RULES)
    assert not result["s1"]["holds"] and not result["passes"]


def test_s2_requires_both_mean_and_trimmed_mean_positive():
    trades = [trade(i % 10, 0.01) for i in range(99)] + [trade(0, -5.0)]
    lane = np.full(1200, 0.001)
    result = evaluate_stage(trades, lanes(lane), np.zeros(1200), windows=10, protocol=PROTOCOL, rules=RULES)
    assert result["s2"]["trimmed_mean"] > 0 > result["s2"]["mean"] and not result["s2"]["holds"]


def test_s3_counts_trades_windows_and_positive_fraction():
    trades = [
        trade(w, 0.01 if w < 4 else -0.01) for w in range(8) for _ in range(13)
    ]  # 104 trades, 8 windows, 50% positive
    lane = np.full(1200, 0.001)
    result = evaluate_stage(trades, lanes(lane), np.zeros(1200), windows=12, protocol=PROTOCOL, rules=RULES)
    s3 = result["s3"]
    assert s3["closed_trades"] == 104 and s3["windows_with_trades"] == 8 and s3["positive_window_fraction"] == 0.5
    assert not s3["holds"]
    few = evaluate_stage(trades[:50], lanes(lane), np.zeros(1200), windows=12, protocol=PROTOCOL, rules=RULES)
    assert not few["s3"]["holds"] and few["s3"]["closed_trades"] == 50


def test_s4_fails_on_market_exposure_and_on_too_few_observations():
    rng = np.random.default_rng(1)
    market = rng.normal(0.0, 0.01, 1200)
    exposed = 0.5 * market + 0.001
    result = evaluate_stage(good_trades(), lanes(exposed), market, windows=10, protocol=PROTOCOL, rules=RULES)
    assert result["s4"]["beta"] == pytest.approx(0.5, abs=0.02) and not result["s4"]["holds"]
    sparse = np.full(1200, np.nan)
    sparse[:20] = market[:20]
    result = evaluate_stage(good_trades(), lanes(exposed), sparse, windows=10, protocol=PROTOCOL, rules=RULES)
    assert math.isnan(result["s4"]["beta"]) and not result["s4"]["holds"] and result["s4"]["n_sessions"] == 20


def test_empty_stage_is_a_clean_failure():
    result = evaluate_stage([], lanes(np.zeros(0)), np.zeros(0), windows=0, protocol=PROTOCOL, rules=RULES)
    assert not result["passes"] and result["s3"]["closed_trades"] == 0 and math.isnan(result["s1"]["mean"])
    assert result["descriptives"]["windows"] == [] and result["descriptives"]["exit_reasons"] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_evaluate.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# agentic_trader/research/spread/evaluate.py
"""Stage evaluation: the four predeclared pass rules and descriptive tables (never gates)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

import numpy as np
import statsmodels.api as sm

from agentic_trader.research.pooled.stats import trimmed_mean
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive
from agentic_trader.research.setups.study import _stationary_index_draws
from agentic_trader.research.spread.protocol import PassRules, SpreadProtocol
from agentic_trader.research.spread.trading import Trade, trade_net_return


__all__ = ["evaluate_stage"]

_MIN_BETA_OBSERVATIONS = 30
_HAC_LAGS = 5
_SESSIONS_PER_YEAR = 252


def _nan() -> float:
    return float("nan")


def _s1(lane: np.ndarray, protocol: SpreadProtocol) -> dict:
    n = lane.size
    if n == 0:
        return {"mean": _nan(), "ci90": (_nan(), _nan()), "p_one_sided": _nan(), "n_sessions": 0, "holds": False}
    mean = float(lane.mean())
    draws = _stationary_index_draws(n, protocol.bootstrap.block_mean, protocol.bootstrap.draws, protocol.bootstrap.seed)
    boot = lane[draws].mean(axis=1)
    ci90 = _ci90(boot)
    return {
        "mean": mean,
        "ci90": ci90,
        "p_one_sided": _p_one_sided_positive(boot),
        "n_sessions": int(n),
        "holds": bool(mean > 0 and math.isfinite(ci90[0]) and ci90[0] > 0),
    }


def _s2(nets: np.ndarray, rules: PassRules) -> dict:
    if nets.size == 0:
        return {"mean": _nan(), "trimmed_mean": _nan(), "holds": False}
    mean = float(nets.mean())
    trimmed = trimmed_mean(nets, rules.trim_fraction)
    return {"mean": mean, "trimmed_mean": trimmed, "holds": bool(mean > 0 and math.isfinite(trimmed) and trimmed > 0)}


def _window_means(trades: Sequence[Trade], nets: np.ndarray) -> dict[int, float]:
    sums: dict[int, list[float]] = {}
    for trade, net in zip(trades, nets, strict=True):
        sums.setdefault(trade.window, []).append(float(net))
    return {w: float(np.mean(v)) for w, v in sorted(sums.items())}


def _s3(trades: Sequence[Trade], nets: np.ndarray, rules: PassRules) -> dict:
    means = _window_means(trades, nets)
    positive = sum(1 for m in means.values() if m > 0)
    fraction = positive / len(means) if means else 0.0
    return {
        "closed_trades": len(trades),
        "windows_with_trades": len(means),
        "positive_window_fraction": fraction,
        "holds": bool(
            len(trades) >= rules.min_closed_trades
            and len(means) >= rules.min_windows
            and fraction >= rules.min_positive_window_fraction
        ),
    }


def _s4(lane: np.ndarray, market: np.ndarray, rules: PassRules) -> dict:
    mask = np.isfinite(lane) & np.isfinite(market)
    n = int(mask.sum())
    if n < _MIN_BETA_OBSERVATIONS:
        return {"beta": _nan(), "se_hac": _nan(), "n_sessions": n, "holds": False}
    x, y = market[mask], lane[mask]
    if np.ptp(x) == 0.0:
        return {"beta": _nan(), "se_hac": _nan(), "n_sessions": n, "holds": False}
    fit = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HAC", cov_kwds={"maxlags": _HAC_LAGS, "use_correction": True})
    beta = float(fit.params[1])
    return {
        "beta": beta,
        "se_hac": float(fit.bse[1]),
        "n_sessions": n,
        "holds": bool(math.isfinite(beta) and abs(beta) <= rules.max_abs_market_beta),
    }


def _by_cost(trades: Sequence[Trade], lane_by_cost: Mapping[float, np.ndarray]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cost, lane in lane_by_cost.items():
        nets = np.array([trade_net_return(t, cost) for t in trades], dtype=float)
        std = float(lane.std(ddof=1)) if lane.size > 1 else _nan()
        out[str(float(cost))] = {
            "closed_trades": len(trades),
            "mean_trade_net": float(nets.mean()) if nets.size else _nan(),
            "hit_rate": float((nets > 0).mean()) if nets.size else _nan(),
            "lane_mean_daily": float(lane.mean()) if lane.size else _nan(),
            "sharpe_annualised": float(lane.mean() / std * math.sqrt(_SESSIONS_PER_YEAR))
            if std and std > 0
            else _nan(),
        }
    return out


def evaluate_stage(
    trades: Sequence[Trade],
    lane_by_cost: Mapping[float, np.ndarray],
    market: np.ndarray,
    *,
    windows: int,
    protocol: SpreadProtocol,
    rules: PassRules,
) -> dict:
    cost = protocol.decision_cost_bps
    lane = np.asarray(lane_by_cost[cost], dtype=float)
    market = np.asarray(market, dtype=float)
    nets = np.array([trade_net_return(t, cost) for t in trades], dtype=float)
    s1, s2, s3, s4 = _s1(lane, protocol), _s2(nets, rules), _s3(trades, nets, rules), _s4(lane, market, rules)
    window_means = _window_means(trades, nets)
    window_counts = Counter(t.window for t in trades)
    sectors: dict[str, list[float]] = {}
    for trade, net in zip(trades, nets, strict=True):
        sectors.setdefault(trade.sector, []).append(float(net))
    by_cost = _by_cost(trades, lane_by_cost)
    descriptives = {
        "decision_cost_bps": cost,
        "by_cost": by_cost,
        "sharpe_annualised": by_cost[str(float(cost))]["sharpe_annualised"],
        "hit_rate": by_cost[str(float(cost))]["hit_rate"],
        "mean_holding_sessions": float(np.mean([t.holding_sessions for t in trades])) if trades else _nan(),
        "exit_reasons": dict(sorted(Counter(t.exit_reason for t in trades).items())),
        "stage_windows": windows,
        "windows": [
            {"window": w, "trades": window_counts[w], "mean_net": m, "positive": m > 0} for w, m in window_means.items()
        ],
        "sectors": {s: {"trades": len(v), "mean_net": float(np.mean(v))} for s, v in sorted(sectors.items())},
    }
    return {
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "s4": s4,
        "passes": bool(s1["holds"] and s2["holds"] and s3["holds"] and s4["holds"]),
        "descriptives": descriptives,
    }
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_evaluate.py -q`
Expected: PASS. (`_ci90` returns a 2-tuple; check its signature in `agentic_trader/research/setups/baserates.py` before assuming.)

- [ ] **Step 5: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/research/spread/evaluate.py tests/research/spread/test_evaluate.py
git add agentic_trader/research/spread/evaluate.py tests/research/spread/test_evaluate.py
git commit -m "Spread lane: stage evaluation (S1 lane bootstrap, S2 trade means, S3 counts, S4 market beta) and descriptive tables"
```

---

### Task 5: Panels — cached SIP acquisition, null shift, synthetic power world

**Files:**
- Create: `agentic_trader/research/spread/panel.py`
- Test: `tests/research/spread/test_panel.py`

**Interfaces:**
- Consumes: `_fetch_cached`, `_claim_cache_range`, `BarSource`, `CalendarSource` (`agentic_trader.research.setups.runner`); `ET_TZ` (`agentic_trader.market.session`); `SpreadCohort`, `PowerSpec` (Task 1); `fit_pair`, `FormationRule` for the test only.
- Produces: `SpreadPanel(sessions, closes, opens)` (frames indexed by a naive `DatetimeIndex` of session dates, one column per symbol, NaN for missing bars) with `.symbols`; `PanelBuild(panel, bar_failures)`; `async build_spread_panel(symbols, *, bars, calendar, cache_dir, start, through, adjustment, pace) -> PanelBuild`; `shift_panel(panel, *, seed, block_sessions) -> SpreadPanel`; `synthetic_panel(cohort, sessions, spec, *, seed) -> tuple[SpreadPanel, tuple[tuple[str, str], ...]]`.

Acquisition: sessions are the calendar's trading days in `[start, through]`; each symbol's daily frame comes from `_fetch_cached(symbol, "1d", bars, cache_dir, start_dt, end_dt, adjustment, pace)` with `start_dt = datetime.combine(start, time.min, UTC)` and `end_dt = datetime.combine(through, time.max, UTC)` after `_claim_cache_range(cache_dir, start_dt, end_dt)`; bar timestamps (tz-aware) convert to `ET_TZ` and reduce to the session date (duplicates keep the last); a raised fetch records `type(exc).__name__`, an empty frame records `"empty"`; the symbol's columns are then all-NaN. Nothing else is raised. The market symbol is acquired like any other.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_panel.py
import asyncio
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.research.setups import runner
from agentic_trader.research.spread.formation import fit_pair
from agentic_trader.research.spread.panel import (
    PanelBuild,
    SpreadPanel,
    build_spread_panel,
    shift_panel,
    synthetic_panel,
)
from agentic_trader.research.spread.protocol import FormationRule, PowerSpec, SpreadCohort
from tests.research.spread.test_schedule import weekdays


SESSIONS = weekdays(date(2020, 1, 6), date(2021, 12, 31))


class FakeCalendar:
    async def get_calendar_range(self, start_date, end_date):
        out, d = [], start_date
        while d <= end_date:
            out.append(MarketCalendarDay(date=d, is_trading_day=d.weekday() < 5, is_early_close=False))
            d += timedelta(days=1)
        return out


class FakeBars:
    def __init__(self):
        self.calls: list[tuple[str, datetime, datetime]] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, start, end))
        assert timeframe == "1d" and adjustment == "all"
        if symbol == "BAD":
            raise RuntimeError("provider down")
        if symbol == "EMPTY":
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([], tz=UTC))
        days = [d for d in SESSIONS if start.date() <= d <= end.date()]
        index = pd.DatetimeIndex([datetime(d.year, d.month, d.day, 5, tzinfo=UTC) for d in days])
        base = 100.0 if symbol == "AAA" else 50.0
        closes = base + np.arange(len(days), dtype=float)
        frame = pd.DataFrame(
            {"Open": closes - 0.5, "High": closes + 1, "Low": closes - 1, "Close": closes, "Volume": 1e6}, index=index
        )
        if symbol == "GAPPY":
            frame = frame.drop(frame.index[10])
        return frame


async def pace():
    return None


def test_build_panel_aligns_to_sessions_records_failures_and_uses_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_RETRY_DELAYS", ())  # a raised fetch must not sleep through the retry delays
    bars = FakeBars()
    build = asyncio.run(
        build_spread_panel(
            ["AAA", "GAPPY", "BAD", "EMPTY"],
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2021, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    assert isinstance(build, PanelBuild) and isinstance(build.panel, SpreadPanel)
    panel = build.panel
    assert panel.sessions == SESSIONS and panel.symbols == ("AAA", "BAD", "EMPTY", "GAPPY")
    assert panel.closes.shape == (len(SESSIONS), 4) and panel.opens.shape == panel.closes.shape
    assert panel.closes.index.tz is None and panel.closes.index[0] == pd.Timestamp(SESSIONS[0])
    assert panel.closes["AAA"].iloc[0] == 100.0 and panel.opens["AAA"].iloc[0] == 99.5
    assert np.isnan(panel.closes["GAPPY"].iloc[10]) and panel.closes["GAPPY"].notna().sum() == len(SESSIONS) - 1
    assert build.bar_failures == {"BAD": "RuntimeError", "EMPTY": "empty"}
    assert panel.closes["BAD"].isna().all() and panel.closes["EMPTY"].isna().all()
    assert (tmp_path / "cache_range.json").exists() and (tmp_path / "AAA_1d").exists()
    before = len(bars.calls)
    asyncio.run(
        build_spread_panel(
            ["AAA"],
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2021, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    assert len(bars.calls) == before  # served from the cache


def test_build_panel_refuses_a_range_outside_the_cache_claim(tmp_path):
    bars = FakeBars()
    asyncio.run(
        build_spread_panel(
            ["AAA"],
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=tmp_path,
            start=date(2020, 1, 6),
            through=date(2020, 12, 31),
            adjustment="all",
            pace=pace,
        )
    )
    with pytest.raises(ValueError, match="fresh cache directory"):
        asyncio.run(
            build_spread_panel(
                ["AAA"],
                bars=bars,
                calendar=FakeCalendar(),
                cache_dir=tmp_path,
                start=date(2020, 1, 6),
                through=date(2021, 12, 31),
                adjustment="all",
                pace=pace,
            )
        )


def _panel_from(log_closes: dict[str, np.ndarray], sessions=SESSIONS) -> SpreadPanel:
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in sessions[: len(next(iter(log_closes.values())))]])
    closes = pd.DataFrame({k: np.exp(v) for k, v in log_closes.items()}, index=index)
    opens = closes.shift(1).fillna(closes.iloc[0]) * 1.001
    return SpreadPanel(sessions=tuple(d.date() for d in index), closes=closes, opens=opens)


def test_shift_panel_preserves_marginals_and_breaks_comovement():
    rng = np.random.default_rng(3)
    common = np.cumsum(rng.normal(0, 0.01, 400))
    a = common + np.cumsum(rng.normal(0, 0.002, 400))
    b = common + np.cumsum(rng.normal(0, 0.002, 400))
    panel = _panel_from({"A": a, "B": b})
    panel.closes.loc[panel.closes.index[50], "B"] = np.nan
    panel.opens.loc[panel.opens.index[50], "B"] = np.nan
    shifted = shift_panel(panel, seed=7, block_sessions=21)
    assert shifted.sessions == panel.sessions and list(shifted.closes.columns) == ["A", "B"]
    for name in ("A", "B"):
        original = np.diff(np.log(panel.closes[name].dropna().to_numpy()))
        moved = np.diff(np.log(shifted.closes[name].dropna().to_numpy()))
        assert np.allclose(np.sort(original), np.sort(moved), atol=1e-9)
        assert shifted.closes[name].iloc[0] == panel.closes[name].iloc[0]
    assert np.isnan(shifted.closes["B"].iloc[50]) and np.isnan(shifted.opens["B"].iloc[50])
    ra, rb = np.diff(np.log(panel.closes["A"])), np.diff(np.log(panel.closes["B"]))
    sa, sb = np.diff(np.log(shifted.closes["A"])), np.diff(np.log(shifted.closes["B"]))
    assert np.corrcoef(ra, rb)[0, 1] > 0.9
    assert abs(np.corrcoef(sa, sb)[0, 1]) < 0.3
    other = shift_panel(panel, seed=8, block_sessions=21)
    assert not np.allclose(other.closes["A"].to_numpy(), shifted.closes["A"].to_numpy())


COHORT = SpreadCohort.model_validate(
    {
        "id": "spread-cohort",
        "version": 1,
        "survivorship": "t",
        "market": "SPY",
        "sectors": {"tech": [f"T{i:02d}" for i in range(8)], "energy": [f"E{i:02d}" for i in range(6)]},
    }
)
SPEC = PowerSpec(
    seeds=1,
    min_pass=1,
    planted_pairs=4,
    half_life_sessions=20.0,
    innovation_std=0.008,
    hedge_ratio=(0.6, 1.6),
    market_vol=0.01,
    sector_vol=0.007,
    idiosyncratic_vol=0.012,
    overnight_vol=0.003,
)
RULE = FormationRule(
    coint_max_lag=1,
    p_value=0.05,
    half_life_sessions=(5, 42),
    hedge_ratio_abs=(0.25, 4.0),
    top_pairs=20,
    min_eligible_names=2,
)


def test_synthetic_panel_plants_recoverable_same_sector_pairs():
    sessions = weekdays(date(2016, 1, 4), date(2019, 12, 31))
    panel, planted = synthetic_panel(COHORT, sessions, SPEC, seed=11)
    assert panel.symbols == tuple(sorted([*COHORT.symbols, "SPY"])) and len(panel.sessions) == len(sessions)
    assert len(planted) == 4 and len({s for pair in planted for s in pair}) == 8  # disjoint names
    sector_of = {s: sec for sec, members in COHORT.sectors.items() for s in members}
    assert all(sector_of[y] == sector_of[x] and y < x for y, x in planted)
    assert (panel.closes > 0).all().all() and (panel.opens > 0).all().all()
    logs = np.log(panel.closes.iloc[-252:])
    recovered = sum(
        fit_pair(logs[y].to_numpy(), logs[x].to_numpy(), RULE, y=y, x=x, sector="s").eligible for y, x in planted
    )
    assert recovered >= 3
    unplanted = [(y, x) for y, x, _ in COHORT.pairs() if (y, x) not in planted][:20]
    false = sum(
        fit_pair(logs[y].to_numpy(), logs[x].to_numpy(), RULE, y=y, x=x, sector="s").eligible for y, x in unplanted
    )
    assert false <= 6
    other, _ = synthetic_panel(COHORT, sessions, SPEC, seed=12)
    assert not np.allclose(other.closes.to_numpy(), panel.closes.to_numpy())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_panel.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# agentic_trader/research/spread/panel.py
"""Aligned daily close/open panels: cached SIP acquisition, the null shift and the synthetic power world."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range, _fetch_cached
from agentic_trader.research.spread.protocol import PowerSpec, SpreadCohort


__all__ = ["PanelBuild", "SpreadPanel", "build_spread_panel", "shift_panel", "synthetic_panel"]

_TIMEFRAME = "1d"


@dataclass(frozen=True)
class SpreadPanel:
    """Closes and opens on one session index (naive dates); NaN where a symbol has no bar."""

    sessions: tuple[date, ...]
    closes: pd.DataFrame
    opens: pd.DataFrame

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(str(c) for c in self.closes.columns)


@dataclass(frozen=True)
class PanelBuild:
    panel: SpreadPanel
    bar_failures: dict[str, str]


def _session_index(sessions: Sequence[date]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in sessions])


def _by_session(frame: pd.DataFrame, column: str, index: pd.DatetimeIndex) -> pd.Series:
    stamps = pd.DatetimeIndex(frame.index)
    stamps = stamps.tz_localize(UTC) if stamps.tz is None else stamps
    dates = pd.DatetimeIndex(stamps.tz_convert(ET_TZ).normalize().tz_localize(None))
    series = pd.Series(frame[column].to_numpy(dtype=float), index=dates)
    series = series[~series.index.duplicated(keep="last")]
    return series.reindex(index)


async def build_spread_panel(
    symbols: Sequence[str],
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    start: date,
    through: date,
    adjustment: str,
    pace: Callable[[], Awaitable[None]],
) -> PanelBuild:
    """One cached daily frame per symbol, reduced to session dates; a failed name is recorded, never raised."""
    days = await calendar.get_calendar_range(start, through)
    sessions = tuple(d.date for d in days if d.is_trading_day)
    index = _session_index(sessions)
    start_dt = datetime.combine(start, time.min, UTC)
    end_dt = datetime.combine(through, time.max, UTC)
    _claim_cache_range(cache_dir, start_dt, end_dt)
    closes: dict[str, pd.Series] = {}
    opens: dict[str, pd.Series] = {}
    failures: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        try:
            frame = await _fetch_cached(symbol, _TIMEFRAME, bars, cache_dir, start_dt, end_dt, adjustment, pace)
        except Exception as exc:
            failures[symbol] = type(exc).__name__
            frame = pd.DataFrame()
        if frame.empty:
            failures.setdefault(symbol, "empty")
            closes[symbol] = pd.Series(np.nan, index=index)
            opens[symbol] = pd.Series(np.nan, index=index)
            continue
        closes[symbol] = _by_session(frame, "Close", index)
        opens[symbol] = _by_session(frame, "Open", index)
    panel = SpreadPanel(
        sessions=sessions, closes=pd.DataFrame(closes, index=index), opens=pd.DataFrame(opens, index=index)
    )
    return PanelBuild(panel=panel, bar_failures=dict(sorted(failures.items())))


def shift_panel(panel: SpreadPanel, *, seed: int, block_sessions: int) -> SpreadPanel:
    """Null world: each symbol's close-to-close log returns and overnight gaps are circularly shifted by
    its own random multiple of ``block_sessions`` and the price paths rebuilt from the first close, so
    marginal dynamics survive while every contemporaneous relation is destroyed. Missing bars stay missing."""
    rng = np.random.default_rng(seed)
    closes = panel.closes.to_numpy(dtype=float).copy()
    opens = panel.opens.to_numpy(dtype=float).copy()
    for j in range(closes.shape[1]):
        c, o = closes[:, j], opens[:, j]
        valid = np.flatnonzero(np.isfinite(c) & np.isfinite(o) & (c > 0) & (o > 0))
        m = valid.size
        if m < 2 * block_sessions:
            continue
        cv, ov = c[valid], o[valid]
        returns = np.diff(np.log(cv))
        gaps = np.log(ov[1:]) - np.log(cv[:-1])
        blocks = (m - 1) // block_sessions
        shift = int(rng.integers(1, blocks)) * block_sessions if blocks > 1 else block_sessions
        returns, gaps = np.roll(returns, shift), np.roll(gaps, shift)
        new_c = np.empty(m)
        new_o = np.empty(m)
        new_c[0], new_o[0] = cv[0], ov[0]
        for i in range(1, m):
            new_c[i] = new_c[i - 1] * math.exp(returns[i - 1])
            new_o[i] = new_c[i - 1] * math.exp(gaps[i - 1])
        c[valid], o[valid] = new_c, new_o
    index = panel.closes.index
    return SpreadPanel(
        sessions=panel.sessions,
        closes=pd.DataFrame(closes, index=index, columns=panel.closes.columns),
        opens=pd.DataFrame(opens, index=index, columns=panel.opens.columns),
    )


def _ou(rng: np.random.Generator, n: int, half_life_sessions: float, innovation_std: float) -> np.ndarray:
    phi = 0.5 ** (1.0 / half_life_sessions)
    stationary_std = innovation_std / math.sqrt(1.0 - phi * phi)
    out = np.empty(n)
    out[0] = rng.normal(0.0, stationary_std)
    noise = rng.normal(0.0, innovation_std, n)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + noise[t]
    return out


def synthetic_panel(
    cohort: SpreadCohort, sessions: Sequence[date], spec: PowerSpec, *, seed: int
) -> tuple[SpreadPanel, tuple[tuple[str, str], ...]]:
    """Power world: market + sector + idiosyncratic random walks for every cohort name and the market
    symbol, with ``spec.planted_pairs`` disjoint same-sector pairs replaced by cointegrated OU spreads."""
    rng = np.random.default_rng(seed)
    n = len(sessions)
    market = np.cumsum(rng.normal(0.0, spec.market_vol, n))
    logs: dict[str, np.ndarray] = {cohort.market: math.log(400.0) + market}
    for sector in sorted(cohort.sectors):
        factor = np.cumsum(rng.normal(0.0, spec.sector_vol, n))
        for symbol in sorted(cohort.sectors[sector]):
            logs[symbol] = math.log(100.0) + market + factor + np.cumsum(rng.normal(0.0, spec.idiosyncratic_vol, n))
    candidates = list(cohort.pairs())
    rng.shuffle(candidates)
    planted: list[tuple[str, str]] = []
    used: set[str] = set()
    for y, x, _ in candidates:
        if len(planted) == spec.planted_pairs:
            break
        if y in used or x in used:
            continue
        beta = float(rng.uniform(*spec.hedge_ratio))
        logs[y] = (
            math.log(100.0) * (1.0 - beta) + beta * logs[x] + _ou(rng, n, spec.half_life_sessions, spec.innovation_std)
        )
        planted.append((y, x))
        used.update((y, x))
    index = _session_index(sessions)
    closes = pd.DataFrame({s: np.exp(v) for s, v in sorted(logs.items())}, index=index)
    overnight = rng.normal(0.0, spec.overnight_vol, closes.shape)
    opens = closes.shift(1) * np.exp(overnight)
    opens.iloc[0] = closes.iloc[0]
    return SpreadPanel(sessions=tuple(sessions), closes=closes, opens=opens), tuple(planted)
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_panel.py -q`
Expected: PASS. (`_fetch_cached` chunks a request into 365-day fetches; the fake returns the bars inside each chunk, so the first call for one symbol makes two provider calls and the second build makes none.)

- [ ] **Step 5: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/research/spread/panel.py tests/research/spread/test_panel.py
git add agentic_trader/research/spread/panel.py tests/research/spread/test_panel.py
git commit -m "Spread lane: session-aligned close/open panels from the cached SIP daily bars, the null shift and the synthetic power world"
```

---

### Task 6: Lane-generic one-use confirmation interval in the alpha journal

**Files:**
- Modify: `agentic_trader/storage/alpha.py` (add `LANE_NAME`, `consume_lane_confirmation` next to `consume_pooled_confirmation`)
- Test: `tests/research/spread/test_journal.py`

**Interfaces:**
- Produces: `async AlphaRepository.consume_lane_confirmation(lane, *, protocol_sha256, cohort_sha256, interval, detail) -> dict` writing key `f"{lane}/confirmation"` (`{"intervals": [...]}`) under the alpha lock as `EventKind.ALPHA_RESEARCH` by `research_worker`; refuses an overlapping interval already consumed for that lane, a lane name not matching `^[a-z][a-z0-9-]{1,31}$`, the lane `pooled`, a non-hex hash, or `start > end`. Never touches `family/all`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_journal.py
from datetime import date

import pytest

from agentic_trader.storage.alpha import AlphaRepository


INTERVAL = (date(2024, 1, 2), date(2026, 7, 31))


@pytest.fixture
async def repository(temp_db):
    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def test_confirmation_is_journaled_once_per_lane_and_overlaps_are_refused(repository):
    record = await repository.consume_lane_confirmation(
        "spread",
        protocol_sha256="p" * 64,
        cohort_sha256="c" * 64,
        interval=INTERVAL,
        detail={"stage": "discovery_passed"},
    )
    assert (
        record["start"] == "2024-01-02"
        and record["end"] == "2026-07-31"
        and record["detail"] == {"stage": "discovery_passed"}
    )
    stored = await repository.get("spread/confirmation")
    assert len(stored["intervals"]) == 1 and stored["intervals"][0]["protocol_sha256"] == "p" * 64
    with pytest.raises(ValueError, match="already consumed"):
        await repository.consume_lane_confirmation(
            "spread",
            protocol_sha256="q" * 64,
            cohort_sha256="c" * 64,
            interval=(date(2026, 7, 1), date(2027, 6, 30)),
            detail={},
        )
    await repository.consume_lane_confirmation(
        "spread",
        protocol_sha256="q" * 64,
        cohort_sha256="c" * 64,
        interval=(date(2026, 8, 3), date(2027, 7, 30)),
        detail={},
    )
    assert len((await repository.get("spread/confirmation"))["intervals"]) == 2
    assert await repository.get("family/all") is None
    assert await repository.get("pooled/confirmation") is None  # lanes do not share a ledger
    await repository.consume_lane_confirmation(
        "basket", protocol_sha256="r" * 64, cohort_sha256="c" * 64, interval=INTERVAL, detail={}
    )


@pytest.mark.parametrize(
    "lane, kwargs, match",
    [
        ("pooled", {}, "pooled"),
        ("Spread", {}, "lane"),
        ("s", {}, "lane"),
        ("spread", {"protocol_sha256": "zz"}, "sha256"),
        ("spread", {"interval": (date(2026, 7, 31), date(2024, 1, 2))}, "ordered"),
    ],
)
async def test_invalid_requests_are_refused_before_any_write(repository, lane, kwargs, match):
    base = {"protocol_sha256": "p" * 64, "cohort_sha256": "c" * 64, "interval": INTERVAL, "detail": {}}
    with pytest.raises(ValueError, match=match):
        await repository.consume_lane_confirmation(lane, **{**base, **kwargs})
    assert await repository.get(f"{lane}/confirmation") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_journal.py -q`
Expected: FAIL with `AttributeError: 'AlphaRepository' object has no attribute 'consume_lane_confirmation'`.

- [ ] **Step 3: Implement** (place after `consume_pooled_confirmation`; add `import re` if missing and the constant beside `POOLED_CONFIRMATION_KEY`)

```python
# near the other module constants
LANE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
```

```python
    async def consume_lane_confirmation(
        self,
        lane: str,
        *,
        protocol_sha256: str,
        cohort_sha256: str,
        interval: tuple[date, date],
        detail: Mapping,
    ) -> dict:
        """Journal a single-protocol lane's one-use confirmation interval before it is read.

        Overlapping intervals are refused lane-wide; lanes never share a ledger and never touch
        ``family/all``. The pooled lane keeps its campaign-bound ``consume_pooled_confirmation``.
        """
        if not LANE_NAME.match(lane) or lane == "pooled":
            raise ValueError(f"lane name {lane!r} is not a valid single-protocol lane (or is the pooled lane)")
        if not (_SHA256.match(protocol_sha256) and _SHA256.match(cohort_sha256)):
            raise ValueError("protocol_sha256 and cohort_sha256 must be hex sha256 digests")
        start, end = interval
        if start > end:
            raise ValueError("confirmation interval must be ordered (start <= end)")
        key = f"{lane}/confirmation"
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            consumed = await self._get(session, key) or {"intervals": []}
            for item in consumed["intervals"]:
                if not (end < date.fromisoformat(item["start"]) or date.fromisoformat(item["end"]) < start):
                    raise ValueError(
                        f"{lane} confirmation interval already consumed on {item['consumed_at']} "
                        f"by protocol {item['protocol_sha256'][:16]}"
                    )
            record = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "protocol_sha256": protocol_sha256,
                "cohort_sha256": cohort_sha256,
                "detail": dict(detail),
                "consumed_at": datetime.now(UTC).isoformat(),
            }
            consumed["intervals"].append(record)
            await self._append(session, key, consumed, EventKind.ALPHA_RESEARCH, "research_worker")
            return record
```

- [ ] **Step 4: Run the tests and the existing journal suites**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_journal.py tests/research/pooled/test_ledger.py tests/research/test_alpha_journal.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/storage/alpha.py tests/research/spread/test_journal.py
git add agentic_trader/storage/alpha.py tests/research/spread/test_journal.py
git commit -m "Alpha journal: lane-generic one-use confirmation interval (spread lane); overlaps refused lane-wide, never family/all"
```

---

### Task 7: Stage runner, gates and the three executors

**Files:**
- Create: `agentic_trader/research/spread/study.py`
- Test: `tests/research/spread/world.py` (small test world), `tests/research/spread/test_study.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5; `require_clean_revision` (`agentic_trader.research.pooled.campaign_run`); `save_json_report` (`agentic_trader.storage.artifacts`); `_finite_json` (`agentic_trader.research.setups.study`).
- Produces: `StageRun(status, error, evaluation, trades, lane_by_cost, market, windows)`; `truncate_panel(panel, *, through) -> SpreadPanel`; `run_stage(panel, protocol, cohort, stage, *, planted=(), progress=None) -> StageRun`; `spread_gate(directory, *, check, protocol_sha256, cohort_sha256, revision) -> dict`; `async execute_spread_power(loaded, directory, *, cohort, environment, progress=None) -> dict`; `async execute_spread_null(loaded, directory, *, cohort, build, environment, progress=None) -> dict`; `async execute_spread_study(loaded, directory, *, cohort, build, environment, power_dir, null_dir, journal, confirm) -> dict` where `build() -> Awaitable[PanelBuild]` and `confirm(interval, detail) -> Awaitable[Mapping]`.

Contracts: manifests are written before `build()`; results are `_finite_json`-sanitised and saved as `result.json`; the power check uses a weekday calendar (`pd.bdate_range`) from `bars.start` to `windows.discovery[1]`; the null check and the discovery stage run on `truncate_panel(panel, through=windows.discovery[1])` so no confirmation session is ever read before `confirm` returns; `run_stage` fails closed with `status="coverage_failed"` on `CoverageError`; the market series is the market symbol's close-to-close return over each trading window (the first session's return uses the formation window's last close); the lane's `windows` entries record `index`, `trading` dates, the formation counts, each selected pair (`y, x, sector, beta, t_stat, half_life`) and, when `planted` is given, `planted_recall` (share of planted pairs selected) and `planted_share` (share of selected pairs that are planted).

- [ ] **Step 1: Write the test world and the failing tests**

```python
# tests/research/spread/world.py
"""A small spread-lane world: 14 names in two sectors, 60/30 tiles, two short stages."""

import hashlib
import json
from datetime import date
from pathlib import Path

from agentic_trader.research.spread.protocol import (
    LoadedSpreadCohort,
    LoadedSpreadProtocol,
    load_spread_cohort,
    load_spread_protocol,
)


REPO = Path(__file__).resolve().parents[3]
PROTOCOL_V1 = json.loads((REPO / "config/research/spread/pairs-v1.json").read_text())

COHORT_DOC = {
    "id": "spread-cohort",
    "version": 1,
    "survivorship": "test world",
    "market": "SPY",
    "sectors": {"tech": [f"T{i:02d}" for i in range(8)], "energy": [f"E{i:02d}" for i in range(6)]},
}


def write_world(directory: Path, **overrides) -> tuple[LoadedSpreadProtocol, LoadedSpreadCohort]:
    """Writes cohort and protocol files under ``directory`` and returns both loaded; ``overrides`` patch top-level protocol keys."""
    cohort_path = directory / "cohort.json"
    cohort_path.write_text(json.dumps(COHORT_DOC))
    doc = {
        **PROTOCOL_V1,
        "cohort": str(cohort_path),
        "cohort_sha256": hashlib.sha256(cohort_path.read_bytes()).hexdigest(),
        "bars": {"start": "2016-01-04", "through": "2019-12-31"},
        "windows": {"discovery": ["2016-06-01", "2018-12-31"], "confirmation": ["2019-01-02", "2019-12-31"]},
        "schedule": {"formation_sessions": 60, "trading_sessions": 30},
        "formation": {**PROTOCOL_V1["formation"], "top_pairs": 6, "min_eligible_names": 4},
        "bootstrap": {"block_mean": 5, "draws": 300, "seed": 1},
        "pass_rules": {**PROTOCOL_V1["pass_rules"], "min_closed_trades": 5, "min_windows": 2},
        "confirmation_rules": {**PROTOCOL_V1["confirmation_rules"], "min_closed_trades": 2, "min_windows": 1},
        "power": {**PROTOCOL_V1["power"], "seeds": 2, "min_pass": 1, "planted_pairs": 4},
        "null_check": {**PROTOCOL_V1["null_check"], "seeds": 2, "max_pass": 1, "shift_block_sessions": 21},
        **overrides,
    }
    protocol_path = directory / "protocol.json"
    protocol_path.write_text(json.dumps(doc))
    return load_spread_protocol(protocol_path), load_spread_cohort(cohort_path)


ENVIRONMENT = {"runtime": {"revision": "abc1234"}, "python": "3.14"}


def gate_dir(
    directory: Path,
    *,
    check: str,
    loaded: LoadedSpreadProtocol,
    cohort: LoadedSpreadCohort,
    status: str = "passed",
    revision: str = "abc1234",
) -> Path:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps({"check": check, "environment": {"runtime": {"revision": revision}}})
    )
    (directory / "result.json").write_text(
        json.dumps({"status": status, "protocol_sha256": loaded.sha256, "cohort_sha256": cohort.sha256, "passes": 2})
    )
    return directory
```

```python
# tests/research/spread/test_study.py
import asyncio
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.spread.panel import PanelBuild, SpreadPanel, synthetic_panel
from agentic_trader.research.spread.study import (
    StageRun,
    execute_spread_null,
    execute_spread_power,
    execute_spread_study,
    run_stage,
    spread_gate,
    truncate_panel,
)
from tests.research.spread.test_schedule import weekdays
from agentic_trader.research.spread.protocol import load_spread_cohort
from tests.research.spread.world import COHORT_DOC, ENVIRONMENT, gate_dir, write_world


SESSIONS = weekdays(date(2016, 1, 4), date(2019, 12, 31))


@pytest.fixture
def world(tmp_path):
    return write_world(tmp_path / "world")


def planted_world(loaded, cohort, seed=5):
    return synthetic_panel(cohort.cohort, SESSIONS, loaded.protocol.power, seed=seed)


def test_run_stage_on_a_planted_world_passes_and_reports_windows(world):
    loaded, cohort = world
    panel, planted = planted_world(loaded, cohort)
    run = run_stage(
        truncate_panel(panel, through=date(2018, 12, 31)), loaded.protocol, cohort.cohort, "discovery", planted=planted
    )
    assert isinstance(run, StageRun) and run.status == "completed" and run.error is None
    assert run.evaluation["passes"], run.evaluation
    assert len(run.windows) >= 15 and run.windows[0]["index"] == 0
    first = run.windows[0]
    assert set(first) >= {"index", "trading", "formation_counts", "selected", "planted_recall", "planted_share"}
    assert first["trading"][0] >= "2016-06-01" and run.windows[-1]["trading"][1] <= "2018-12-31"
    assert np.mean([w["planted_share"] for w in run.windows]) > 0.5
    length = sum(1 for _ in run.market)
    assert all(len(v) == length for v in run.lane_by_cost.values()) and set(run.lane_by_cost) == {0.0, 5.0, 10.0}
    assert run.trades and all(t.exit_reason in ("reverted", "stop", "window_end") for t in run.trades)


def test_truncate_panel_keeps_only_sessions_through_the_date(world):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    cut = truncate_panel(panel, through=date(2018, 12, 31))
    assert cut.sessions[-1] <= date(2018, 12, 31) < panel.sessions[-1]
    assert len(cut.closes) == len(cut.sessions) == len(cut.opens)


def test_stage_fails_closed_on_thin_coverage(world):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    closes = panel.closes.copy()
    closes.loc[closes.index[100], [c for c in closes.columns if c != "SPY"][:11]] = np.nan  # 3 names remain complete
    thin = SpreadPanel(sessions=panel.sessions, closes=closes, opens=panel.opens)
    run = run_stage(thin, loaded.protocol, cohort.cohort, "discovery")
    assert (
        run.status == "coverage_failed"
        and "eligible names" in (run.error or "")
        and not run.evaluation.get("passes", False)
    )


def test_power_check_writes_manifest_first_and_passes_on_the_planted_world(world, tmp_path):
    loaded, cohort = world
    out = tmp_path / "power"
    result = asyncio.run(execute_spread_power(loaded, out, cohort=cohort, environment=ENVIRONMENT))
    manifest = json.loads((out / "manifest.json").read_text())
    assert (
        manifest["check"] == "power_a"
        and manifest["protocol_sha256"] == loaded.sha256
        and manifest["authorizes_promotion"] is False
    )
    assert result["status"] == "passed" and result["passes"] >= 1 and len(result["seeds"]) == 2
    assert json.loads((out / "result.json").read_text())["check"] == "power_a"
    assert result["authorizes_promotion"] is False
    with pytest.raises(FileExistsError):
        asyncio.run(execute_spread_power(loaded, out, cohort=cohort, environment=ENVIRONMENT))


def test_power_check_records_a_cohort_mismatch_as_failed(world, tmp_path):
    loaded, _ = world
    other_path = tmp_path / "other-cohort.json"
    other_path.write_text(json.dumps({**COHORT_DOC, "survivorship": "another cohort"}))
    other_cohort = load_spread_cohort(other_path)
    result = asyncio.run(execute_spread_power(loaded, tmp_path / "p2", cohort=other_cohort, environment=ENVIRONMENT))
    assert result["status"] == "failed" and result["error"].startswith("ValueError")


def _build(panel):
    async def build():
        return PanelBuild(panel=panel, bar_failures={"ZZZ": "empty"})

    return build


def test_null_check_runs_on_shifted_discovery_bars_only(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    poisoned = panel.closes.copy()
    poisoned.loc[poisoned.index > pd.Timestamp("2018-12-31")] = -1.0  # confirmation sessions must never be read
    result = asyncio.run(
        execute_spread_null(
            loaded,
            tmp_path / "null",
            cohort=cohort,
            build=_build(SpreadPanel(panel.sessions, poisoned, panel.opens)),
            environment=ENVIRONMENT,
        )
    )
    assert result["status"] in ("passed", "failed") and result["check"] == "null_c" and len(result["seeds"]) == 2
    assert result["bar_failures"] == {"ZZZ": "empty"} and "error" not in result
    manifest = json.loads((tmp_path / "null" / "manifest.json").read_text())
    assert manifest["check"] == "null_c" and manifest["reads"].startswith("discovery")


def test_spread_gate_checks_kind_status_hashes_and_revision(world, tmp_path):
    loaded, cohort = world
    good = gate_dir(tmp_path / "g", check="power_a", loaded=loaded, cohort=cohort)
    assert (
        spread_gate(
            good, check="power_a", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="abc1234"
        )["status"]
        == "passed"
    )
    with pytest.raises(ValueError, match="not 'null_c'"):
        spread_gate(
            good, check="null_c", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="abc1234"
        )
    with pytest.raises(ValueError, match="revision"):
        spread_gate(
            good, check="power_a", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="def5678"
        )
    with pytest.raises(ValueError, match="protocol"):
        spread_gate(good, check="power_a", protocol_sha256="0" * 64, cohort_sha256=cohort.sha256, revision="abc1234")
    failed = gate_dir(tmp_path / "f", check="null_c", loaded=loaded, cohort=cohort, status="failed")
    with pytest.raises(ValueError, match="not passed"):
        spread_gate(
            failed, check="null_c", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="abc1234"
        )


class Confirm:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.error = error

    async def __call__(self, interval, detail):
        self.calls.append((interval, detail))
        if self.error:
            raise self.error
        return {"start": interval[0].isoformat(), "end": interval[1].isoformat(), "consumed_at": "t"}


def _gates(tmp_path, loaded, cohort):
    return gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort), gate_dir(
        tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort
    )


def test_study_confirms_after_journaling_and_records_both_stages(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    confirm = Confirm()
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={"scope": "test", "dialect": "sqlite", "database": "x"},
            confirm=confirm,
        )
    )
    assert result["status"] == "completed", result
    assert result["decision"] in ("confirmed", "failed_confirmation") and result["discovery"]["evaluation"]["passes"]
    assert confirm.calls == [((date(2019, 1, 2), date(2019, 12, 31)), confirm.calls[0][1])]
    assert confirm.calls[0][1]["protocol_sha256"] == loaded.sha256 and "discovery" in confirm.calls[0][1]
    assert (
        result["confirmation_record"]["consumed_at"] == "t"
        and result["confirmation"]["evaluation"]["s1"]["n_sessions"] > 0
    )
    manifest = json.loads((tmp_path / "study" / "manifest.json").read_text())
    assert (
        manifest["journal"] == {"scope": "test", "dialect": "sqlite", "database": "x"}
        and manifest["authorizes_promotion"] is False
    )
    assert (tmp_path / "study" / "discovery-trades.csv.gz").exists() and (
        tmp_path / "study" / "confirmation-lane.csv.gz"
    ).exists()
    assert result["gates"]["power"]["status"] == "passed" and result["authorizes_promotion"] is False


def test_study_records_confirmation_refusal_without_reading_confirmation(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    confirm = Confirm(ValueError("spread confirmation interval already consumed"))
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=confirm,
        )
    )
    assert (
        result["status"] == "failed" and result["error"] == "ValueError: spread confirmation interval already consumed"
    )
    assert result.get("confirmation") is None and len(confirm.calls) == 1
    assert (tmp_path / "study" / "discovery-trades.csv.gz").exists() and not (
        tmp_path / "study" / "confirmation-lane.csv.gz"
    ).exists()


def test_study_failing_discovery_never_calls_confirm(tmp_path):
    strict = {
        "min_closed_trades": 100000,
        "min_windows": 2,
        "min_positive_window_fraction": 0.6,
        "max_abs_market_beta": 0.2,
        "trim_fraction": 0.01,
    }
    loaded, cohort = write_world(tmp_path / "strict", pass_rules=strict)
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    confirm = Confirm()
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=confirm,
        )
    )
    assert result["status"] == "completed" and result["decision"] == "failed_discovery" and confirm.calls == []
    assert result["discovery"]["evaluation"]["s3"]["holds"] is False and result["confirmation"] is None


def test_study_refuses_dirty_revision_and_mismatched_gates_before_building(world, tmp_path):
    loaded, cohort = world
    power, null = _gates(tmp_path, loaded, cohort)
    built = []

    async def build():
        built.append(1)
        raise AssertionError("must not build")

    dirty = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "d",
            cohort=cohort,
            build=build,
            environment={"runtime": {"revision": "abc1234-dirty"}},
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=Confirm(),
        )
    )
    assert dirty["status"] == "failed" and "dirty" in dirty["error"] and built == []
    wrong = gate_dir(tmp_path / "wrong", check="null_c", loaded=loaded, cohort=cohort, revision="other")
    mismatched = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "m",
            cohort=cohort,
            build=build,
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=wrong,
            journal={},
            confirm=Confirm(),
        )
    )
    assert mismatched["status"] == "failed" and "revision" in mismatched["error"] and built == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_study.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# agentic_trader/research/spread/study.py
"""Stage runner, check gates and the three executors of the spread-reversion lane.

Executors write ``protocol.json`` and ``manifest.json`` before any input is built, record every
failure in ``result.json`` instead of raising, and never read a confirmation session before the
journal has recorded the interval's single use.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from agentic_trader.research.pooled.campaign_run import require_clean_revision
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.research.spread.evaluate import evaluate_stage
from agentic_trader.research.spread.formation import CoverageError, PairFit, select_pairs
from agentic_trader.research.spread.panel import PanelBuild, SpreadPanel, shift_panel, synthetic_panel
from agentic_trader.research.spread.protocol import (
    LANE,
    LoadedSpreadCohort,
    LoadedSpreadProtocol,
    SpreadCohort,
    SpreadProtocol,
)
from agentic_trader.research.spread.schedule import Window, stage_windows
from agentic_trader.research.spread.trading import PairSimulation, Trade, lane_series, simulate_pair
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "StageRun",
    "execute_spread_null",
    "execute_spread_power",
    "execute_spread_study",
    "run_stage",
    "spread_gate",
    "truncate_panel",
]

Stage = Literal["discovery", "confirmation"]
POWER_CHECK = "power_a"
NULL_CHECK = "null_c"


@dataclass
class StageRun:
    status: str
    error: str | None
    evaluation: dict
    trades: list[Trade]
    lane_by_cost: dict[float, np.ndarray]
    market: np.ndarray
    windows: list[dict] = field(default_factory=list)


def truncate_panel(panel: SpreadPanel, *, through: date) -> SpreadPanel:
    """Sessions on or before ``through`` only: the discovery stage and the null check never see later bars."""
    keep = np.array([s <= through for s in panel.sessions], dtype=bool)
    return SpreadPanel(
        sessions=tuple(s for s, k in zip(panel.sessions, keep, strict=True) if k),
        closes=panel.closes.loc[keep],
        opens=panel.opens.loc[keep],
    )


def _selected_record(fit: PairFit) -> dict:
    return {
        "y": fit.y,
        "x": fit.x,
        "sector": fit.sector,
        "beta": fit.beta,
        "t_stat": fit.t_stat,
        "half_life": fit.half_life,
    }


def _window_record(
    window: Window, sessions: Sequence[date], counts: dict, selected: Sequence[PairFit], planted: set
) -> dict:
    record = {
        "index": window.index,
        "formation": (sessions[window.formation[0]].isoformat(), sessions[window.formation[1] - 1].isoformat()),
        "trading": (sessions[window.trading[0]].isoformat(), sessions[window.trading[1] - 1].isoformat()),
        "formation_counts": counts,
        "selected": [_selected_record(f) for f in selected],
    }
    if planted:
        chosen = {f.key for f in selected}
        record["planted_recall"] = len(chosen & planted) / len(planted)
        record["planted_share"] = len(chosen & planted) / len(chosen) if chosen else 0.0
    return record


def run_stage(
    panel: SpreadPanel,
    protocol: SpreadProtocol,
    cohort: SpreadCohort,
    stage: Stage,
    *,
    planted: Sequence[tuple[str, str]] = (),
    progress: Callable[[str], None] | None = None,
) -> StageRun:
    """Formation, trading and evaluation over the stage's tiles; fails closed on thin coverage."""
    sessions = panel.sessions
    windows = stage_windows(
        sessions,
        protocol.stage(stage),
        formation_sessions=protocol.schedule.formation_sessions,
        trading_sessions=protocol.schedule.trading_sessions,
    )
    names = [s for s in cohort.symbols if s in panel.closes.columns]
    pairs = cohort.pairs()
    planted_set = set(planted)
    trades: list[Trade] = []
    lanes: dict[float, list[np.ndarray]] = {c: [] for c in protocol.costs_bps_per_side}
    market_parts: list[np.ndarray] = []
    records: list[dict] = []
    market_closes = panel.closes[cohort.market].to_numpy(dtype=float) if cohort.market in panel.closes else None
    for window in windows:
        f0, f1 = window.formation
        t0, t1 = window.trading
        try:
            selected, counts = select_pairs(panel.closes.iloc[f0:f1][names], pairs, protocol.formation)
        except CoverageError as exc:
            return StageRun("coverage_failed", f"window {window.index}: {exc}", {}, trades, {}, np.zeros(0), records)
        simulations: list[PairSimulation] = []
        for fit in selected:
            cy = panel.closes[fit.y].to_numpy(dtype=float)[t0:t1]
            cx = panel.closes[fit.x].to_numpy(dtype=float)[t0:t1]
            oy = panel.opens[fit.y].to_numpy(dtype=float)[t0:t1]
            ox = panel.opens[fit.x].to_numpy(dtype=float)[t0:t1]
            simulations.append(simulate_pair(fit, cy, cx, oy, ox, protocol.trading, window=window.index))
        for cost in protocol.costs_bps_per_side:
            lanes[cost].append(
                lane_series(simulations, slots=protocol.formation.top_pairs, cost_bps=cost, length=t1 - t0)
            )
        if market_closes is not None:
            prices = market_closes[t0 - 1 : t1]
            with np.errstate(invalid="ignore", divide="ignore"):
                market_parts.append(prices[1:] / prices[:-1] - 1.0)
        else:
            market_parts.append(np.full(t1 - t0, np.nan))
        for simulation in simulations:
            trades.extend(simulation.trades)
        records.append(_window_record(window, sessions, counts, selected, planted_set))
        if progress is not None:
            progress(
                f"{stage} window {window.index + 1}/{len(windows)}: {counts['selected']} pairs, {len(trades)} trades so far"
            )
    lane_by_cost = {c: (np.concatenate(parts) if parts else np.zeros(0)) for c, parts in lanes.items()}
    market = np.concatenate(market_parts) if market_parts else np.zeros(0)
    evaluation = evaluate_stage(
        trades, lane_by_cost, market, windows=len(windows), protocol=protocol, rules=protocol.rules(stage)
    )
    return StageRun("completed", None, evaluation, trades, lane_by_cost, market, records)


def spread_gate(directory: Path, *, check: str, protocol_sha256: str, cohort_sha256: str, revision: str) -> dict:
    """A check's passed result for this protocol and cohort at this exact code revision."""
    manifest = json.loads((directory / "manifest.json").read_text())
    result = json.loads((directory / "result.json").read_text())
    if manifest.get("check") != check:
        raise ValueError(f"{directory} holds a {manifest.get('check')!r} result, not {check!r}")
    if result.get("status") != "passed":
        raise ValueError(f"{check} has not passed (status {result.get('status')!r})")
    if result.get("protocol_sha256") != protocol_sha256:
        raise ValueError(f"{check} was run under a different protocol")
    if result.get("cohort_sha256") != cohort_sha256:
        raise ValueError(f"{check} was run on a different cohort")
    ran_at = manifest.get("environment", {}).get("runtime", {}).get("revision")
    if ran_at != revision:
        raise ValueError(f"{check} ran at code revision {ran_at!r}; this study runs at {revision!r}")
    return result


def _start(
    loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort, directory: Path, manifest: dict, environment: dict
) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**loaded.protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "lane": LANE,
            "entry_id": loaded.protocol.id,
            "version": loaded.protocol.version,
            "protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
            **manifest,
        },
        directory / "manifest.json",
    )


def _require_cohort(loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort) -> None:
    if cohort.sha256 != loaded.protocol.cohort_sha256:
        raise ValueError("cohort file does not match the protocol's cohort_sha256")


def _holds(evaluation: dict) -> dict:
    return {k: bool(evaluation.get(k, {}).get("holds", False)) for k in ("s1", "s2", "s3", "s4")}


def _seed_summary(seed: int, run: StageRun) -> dict:
    return {
        "seed": seed,
        "status": run.status,
        "error": run.error,
        "passes": bool(run.evaluation.get("passes", False)),
        "holds": _holds(run.evaluation),
        "closed_trades": len(run.trades),
        "planted_recall": float(np.mean([w.get("planted_recall", np.nan) for w in run.windows]))
        if run.windows
        else float("nan"),
        "lane_mean_daily": run.evaluation.get("s1", {}).get("mean", float("nan")),
    }


def _finish(directory: Path, result: dict) -> dict:
    save_json_report(_finite_json(result), directory / "result.json")
    return result


def _base(loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort, check: str | None) -> dict:
    base = {
        "lane": LANE,
        "protocol_sha256": loaded.sha256,
        "cohort_sha256": cohort.sha256,
        "authorizes_promotion": False,
    }
    return {**base, "check": check} if check else base


async def execute_spread_power(
    loaded: LoadedSpreadProtocol,
    directory: Path,
    *,
    cohort: LoadedSpreadCohort,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Check A: the discovery pipeline on synthetic worlds with planted cointegrated pairs."""
    protocol = loaded.protocol
    _start(loaded, cohort, directory, {"check": POWER_CHECK, "reads": "synthetic panels only"}, environment)
    base = _base(loaded, cohort, POWER_CHECK)
    try:
        _require_cohort(loaded, cohort)
        sessions = tuple(d.date() for d in pd.bdate_range(protocol.bars.start, protocol.windows.discovery[1]))
        seeds: list[dict] = []
        for seed in range(protocol.power.seeds):
            panel, planted = await asyncio.to_thread(
                synthetic_panel, cohort.cohort, sessions, protocol.power, seed=seed
            )
            run = await asyncio.to_thread(
                run_stage, panel, protocol, cohort.cohort, "discovery", planted=planted, progress=progress
            )
            seeds.append(_seed_summary(seed, run))
        passes = sum(1 for s in seeds if s["passes"])
        result = {
            **base,
            "status": "passed" if passes >= protocol.power.min_pass else "failed",
            "calendar": "weekdays",
            "seeds": seeds,
            "passes": passes,
            "required": protocol.power.min_pass,
        }
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return _finish(directory, result)


async def execute_spread_null(
    loaded: LoadedSpreadProtocol,
    directory: Path,
    *,
    cohort: LoadedSpreadCohort,
    build: Callable[[], Awaitable[PanelBuild]],
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Check C: the discovery pipeline on per-symbol shifted discovery bars (no comovement to find)."""
    protocol = loaded.protocol
    _start(
        loaded,
        cohort,
        directory,
        {"check": NULL_CHECK, "reads": "discovery sessions only, shifted per symbol"},
        environment,
    )
    base = _base(loaded, cohort, NULL_CHECK)
    try:
        _require_cohort(loaded, cohort)
        built = await build()
        panel = truncate_panel(built.panel, through=protocol.windows.discovery[1])
        seeds: list[dict] = []
        for seed in range(protocol.null_check.seeds):
            shifted = await asyncio.to_thread(
                shift_panel, panel, seed=seed, block_sessions=protocol.null_check.shift_block_sessions
            )
            run = await asyncio.to_thread(run_stage, shifted, protocol, cohort.cohort, "discovery", progress=progress)
            seeds.append(_seed_summary(seed, run))
        passes = sum(1 for s in seeds if s["passes"])
        result = {
            **base,
            "status": "passed" if passes <= protocol.null_check.max_pass else "failed",
            "seeds": seeds,
            "passes": passes,
            "allowed": protocol.null_check.max_pass,
            "bar_failures": dict(built.bar_failures),
        }
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return _finish(directory, result)


def _save_frame(frame: pd.DataFrame, path: Path) -> None:
    with gzip.open(path, "wt", newline="") as handle:
        frame.to_csv(handle, index=False)


def _stage_payload(run: StageRun) -> dict:
    return {
        "status": run.status,
        "error": run.error,
        "evaluation": run.evaluation,
        "windows": run.windows,
        "closed_trades": len(run.trades),
    }


def _persist_stage(directory: Path, stage: str, run: StageRun) -> None:
    _save_frame(pd.DataFrame([asdict(t) for t in run.trades]), directory / f"{stage}-trades.csv.gz")
    lane = pd.DataFrame({f"lane_{c:g}bps": v for c, v in run.lane_by_cost.items()})
    lane["market"] = run.market
    _save_frame(lane, directory / f"{stage}-lane.csv.gz")


async def execute_spread_study(
    loaded: LoadedSpreadProtocol,
    directory: Path,
    *,
    cohort: LoadedSpreadCohort,
    build: Callable[[], Awaitable[PanelBuild]],
    environment: dict,
    power_dir: Path,
    null_dir: Path,
    journal: Mapping,
    confirm: Callable[[tuple[date, date], Mapping], Awaitable[Mapping]],
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Discovery, then (only after the journal records the interval) confirmation; never promotes."""
    protocol = loaded.protocol
    _start(
        loaded,
        cohort,
        directory,
        {"journal": dict(journal), "gates": {"power": str(power_dir), "null": str(null_dir)}},
        environment,
    )
    base = _base(loaded, cohort, None)
    try:
        revision = require_clean_revision(environment)
        _require_cohort(loaded, cohort)
        gates = {
            "power": spread_gate(
                power_dir,
                check=POWER_CHECK,
                protocol_sha256=loaded.sha256,
                cohort_sha256=cohort.sha256,
                revision=revision,
            ),
            "null": spread_gate(
                null_dir,
                check=NULL_CHECK,
                protocol_sha256=loaded.sha256,
                cohort_sha256=cohort.sha256,
                revision=revision,
            ),
        }
        built = await build()
        discovery_panel = truncate_panel(built.panel, through=protocol.windows.discovery[1])
        discovery = await asyncio.to_thread(
            run_stage, discovery_panel, protocol, cohort.cohort, "discovery", progress=progress
        )
        await asyncio.to_thread(_persist_stage, directory, "discovery", discovery)
        result: dict = {
            **base,
            "status": "completed",
            "code_revision": revision,
            "gates": {k: {"status": v["status"], "passes": v.get("passes")} for k, v in gates.items()},
            "bar_failures": dict(built.bar_failures),
            "discovery": _stage_payload(discovery),
            "confirmation": None,
            "confirmation_record": None,
        }
        if discovery.status != "completed" or not discovery.evaluation["passes"]:
            result["decision"] = "failed_discovery"
            return _finish(directory, result)
        detail = {
            "protocol_sha256": loaded.sha256,
            "code_revision": revision,
            "discovery": {k: discovery.evaluation[k] for k in ("s1", "s2", "s3", "s4")},
        }
        record = await confirm(protocol.windows.confirmation, detail)
        confirmation = await asyncio.to_thread(
            run_stage, built.panel, protocol, cohort.cohort, "confirmation", progress=progress
        )
        await asyncio.to_thread(_persist_stage, directory, "confirmation", confirmation)
        result["confirmation_record"] = dict(record)
        result["confirmation"] = _stage_payload(confirmation)
        passed = confirmation.status == "completed" and confirmation.evaluation["passes"]
        result["decision"] = "confirmed" if passed else "failed_confirmation"
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "confirmation": None}
    return _finish(directory, result)
```

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_study.py -q`
Expected: PASS. The planted-world tests depend on the synthetic edge being strong enough for the small world; if `test_run_stage_on_a_planted_world_passes_and_reports_windows` fails, print `run.evaluation` and report which rule failed rather than loosening the protocol — the small world's rules (`min_closed_trades` 5, `min_windows` 2) are already relaxed.

- [ ] **Step 5: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run pre-commit run --files agentic_trader/research/spread/study.py tests/research/spread/world.py tests/research/spread/test_study.py
git add agentic_trader/research/spread/study.py tests/research/spread/world.py tests/research/spread/test_study.py
git commit -m "Spread lane: stage runner, check gates and executors (power A on synthetic worlds, null C on shifted bars, study with journaled one-use confirmation)"
```

---

### Task 8: CLI commands and documentation

**Files:**
- Create: `agentic_trader/cli/commands/alpha_spread.py`
- Modify: `agentic_trader/cli/main.py` (import and `alpha_group.add_command` for the three commands, next to `liquidity_study_cmd`)
- Create: `docs/alpha-spread-lane.md`
- Modify: `CLAUDE.md` (one paragraph after the pooled-lane paragraph), `docs/alpha-roadmap.md` (item 1 bracket status, item 2 status), `docs/strategies.md` §4 (caveat paragraph), `docs/cli-reference.md` (three commands)
- Test: `tests/research/spread/test_cli.py`

**Interfaces:**
- Consumes: `_apriori_clients`, `alpha_repository`, `research_environment`, `_require_journal`, `_uncommitted_research_files` (`agentic_trader.cli.commands.alpha`); `journal_identity`, `require_clean_revision` (`agentic_trader.research.pooled.campaign_run`); `REPO_ROOT` (`agentic_trader.research.pooled.entry`); Task 5 `build_spread_panel`; Task 7 executors; Task 6 `consume_lane_confirmation`.
- Produces: `copilot alpha spread-power PROTOCOL --output DIR`, `copilot alpha spread-null PROTOCOL --output DIR --cache DIR`, `copilot alpha spread-study PROTOCOL --power DIR --null-check DIR --output DIR --cache DIR --journal-scope SCOPE`.

Behaviour: every command refuses an existing `--output`; the cohort path in the protocol resolves against `REPO_ROOT` when relative; `spread-study` refuses, before any client or build, uncommitted files under `agentic_trader`, `config` or `tests` and a dirty/unknown revision, then opens the repository, checks `_require_journal(repository, scope)`, and passes `journal_identity(repository)` and a `confirm` closure over `repository.consume_lane_confirmation("spread", ...)` to the executor. The panel request always covers `bars.start..bars.through` (the executors truncate). Each command echoes `{status, decision?, passes?, error?}` as JSON and raises `ClickException` on `status == "failed"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/spread/test_cli.py
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_spread
from agentic_trader.cli.main import cli
from agentic_trader.research.spread.panel import PanelBuild, synthetic_panel
from tests.research.spread.test_schedule import weekdays
from tests.research.spread.world import ENVIRONMENT, gate_dir, write_world


SESSIONS = weekdays(date(2016, 1, 4), date(2019, 12, 31))


@pytest.fixture
def world(tmp_path):
    return write_world(tmp_path / "world")


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(alpha_spread, "research_environment", lambda: ENVIRONMENT)
    monkeypatch.setattr(alpha_spread, "_uncommitted_research_files", lambda: "")


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.store = SimpleNamespace(
            scope="test/paper",
            db=SimpleNamespace(
                engine=SimpleNamespace(
                    dialect=SimpleNamespace(name="postgresql"), url=SimpleNamespace(database="test_x")
                )
            ),
        )

    async def consume_lane_confirmation(self, lane, **kwargs):
        self.calls.append((lane, kwargs))
        return {
            "start": kwargs["interval"][0].isoformat(),
            "end": kwargs["interval"][1].isoformat(),
            "consumed_at": "t",
        }


@pytest.fixture
def fakes(monkeypatch, world):
    loaded, cohort = world
    panel, _ = synthetic_panel(cohort.cohort, SESSIONS, loaded.protocol.power, seed=5)
    requests = []

    async def fake_build_spread_panel(symbols, **kwargs):
        requests.append((tuple(symbols), kwargs["start"], kwargs["through"], kwargs["adjustment"]))
        return PanelBuild(panel=panel, bar_failures={})

    @contextmanager
    def fake_clients():
        yield SimpleNamespace(bars=object(), calendar=object(), pace=None)

    repository = FakeRepository()

    @asynccontextmanager
    async def fake_repository():
        yield repository

    monkeypatch.setattr(alpha_spread, "build_spread_panel", fake_build_spread_panel)
    monkeypatch.setattr(alpha_spread, "_apriori_clients", fake_clients)
    monkeypatch.setattr(alpha_spread, "alpha_repository", fake_repository)
    return SimpleNamespace(requests=requests, repository=repository)


def test_spread_power_runs_and_refuses_an_existing_output(world, tmp_path, clean):
    loaded, _ = world
    out = tmp_path / "power"
    result = CliRunner().invoke(cli, ["alpha", "spread-power", str(loaded.path), "--output", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(next(line for line in result.output.splitlines() if line.startswith("{")))
    assert payload["status"] == "passed" and (out / "result.json").exists()
    again = CliRunner().invoke(cli, ["alpha", "spread-power", str(loaded.path), "--output", str(out)])
    assert again.exit_code != 0 and "already exists" in again.output


def test_spread_null_requests_the_full_bar_range_and_writes_a_result(world, tmp_path, clean, fakes):
    loaded, cohort = world
    out = tmp_path / "null"
    result = CliRunner().invoke(
        cli, ["alpha", "spread-null", str(loaded.path), "--output", str(out), "--cache", str(tmp_path / "cache")]
    )
    assert result.exit_code == 0, result.output
    assert fakes.requests == [((*cohort.cohort.symbols, "SPY"), date(2016, 1, 4), date(2019, 12, 31), "all")]
    assert json.loads((out / "result.json").read_text())["check"] == "null_c"


def test_spread_study_journals_and_completes(world, tmp_path, clean, fakes):
    loaded, cohort = world
    power = gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort)
    null = gate_dir(tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort)
    out = tmp_path / "study"
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "spread-study",
            str(loaded.path),
            "--power",
            str(power),
            "--null-check",
            str(null),
            "--output",
            str(out),
            "--cache",
            str(tmp_path / "cache"),
            "--journal-scope",
            "test/paper",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads((out / "result.json").read_text())
    assert payload["status"] == "completed" and payload["decision"] in ("confirmed", "failed_confirmation")
    assert fakes.repository.calls and fakes.repository.calls[0][0] == "spread"
    assert fakes.repository.calls[0][1]["protocol_sha256"] == loaded.sha256
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["journal"] == {"scope": "test/paper", "dialect": "postgresql", "database": "test_x"}


def test_spread_study_refuses_a_journal_scope_mismatch_before_building(world, tmp_path, clean, fakes):
    loaded, cohort = world
    power = gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort)
    null = gate_dir(tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort)
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "spread-study",
            str(loaded.path),
            "--power",
            str(power),
            "--null-check",
            str(null),
            "--output",
            str(tmp_path / "s"),
            "--cache",
            str(tmp_path / "cache"),
            "--journal-scope",
            "production/alpaca:paper",
        ],
    )
    assert (
        result.exit_code != 0
        and "does not match" in result.output
        and fakes.requests == []
        and not (tmp_path / "s").exists()
    )


def test_spread_study_refuses_uncommitted_files_and_dirty_revisions(world, tmp_path, monkeypatch, fakes):
    loaded, cohort = world
    power = gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort)
    null = gate_dir(tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort)
    args = [
        "alpha",
        "spread-study",
        str(loaded.path),
        "--power",
        str(power),
        "--null-check",
        str(null),
        "--output",
        str(tmp_path / "s"),
        "--cache",
        str(tmp_path / "cache"),
        "--journal-scope",
        "test/paper",
    ]
    monkeypatch.setattr(alpha_spread, "research_environment", lambda: ENVIRONMENT)
    monkeypatch.setattr(alpha_spread, "_uncommitted_research_files", lambda: " M agentic_trader/x.py")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0 and "uncommitted" in result.output
    monkeypatch.setattr(alpha_spread, "_uncommitted_research_files", lambda: "")
    monkeypatch.setattr(alpha_spread, "research_environment", lambda: {"runtime": {"revision": "abc1234-dirty"}})
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0 and "dirty" in result.output and fakes.requests == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_cli.py -q`
Expected: FAIL with `ImportError` on `alpha_spread`.

- [ ] **Step 3: Implement the CLI module and register it**

```python
# agentic_trader/cli/commands/alpha_spread.py
"""Spread-reversion lane commands: power check A, null check C and the predeclared pairs study."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import (
    _apriori_clients,
    _require_journal,
    _uncommitted_research_files,
    alpha_repository,
    research_environment,
)
from agentic_trader.cli.utils import coro
from agentic_trader.research.pooled.campaign_run import journal_identity, require_clean_revision
from agentic_trader.research.pooled.entry import REPO_ROOT
from agentic_trader.research.spread.panel import PanelBuild, build_spread_panel
from agentic_trader.research.spread.protocol import (
    LANE,
    LoadedSpreadCohort,
    LoadedSpreadProtocol,
    load_spread_cohort,
    load_spread_protocol,
)
from agentic_trader.research.spread.study import execute_spread_null, execute_spread_power, execute_spread_study


_OUTPUT_HELP = "New private directory; no overwrite"
_CACHE_HELP = "Daily bar cache directory (the pooled cache's `bars` directory holds the cohort already)"


def _progress(message: str) -> None:
    click.echo(f"[spread] {message}", err=True)


def _refuse_existing(output: Path) -> None:
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")


def _load(protocol_path: Path) -> tuple[LoadedSpreadProtocol, LoadedSpreadCohort]:
    loaded = load_spread_protocol(protocol_path)
    cohort_path = Path(loaded.protocol.cohort)
    if not cohort_path.is_absolute():
        cohort_path = REPO_ROOT / cohort_path
    return loaded, load_spread_cohort(cohort_path)


def _echo(result: dict, failure: str) -> None:
    click.echo(
        json.dumps({k: result.get(k) for k in ("status", "decision", "passes", "error") if k in result}, default=str)
    )
    if result.get("status") == "failed":
        raise click.ClickException(failure)


def _panel_builder(loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort, clients, cache: Path):
    protocol = loaded.protocol

    async def build() -> PanelBuild:
        return await build_spread_panel(
            [*cohort.cohort.symbols, cohort.cohort.market],
            bars=clients.bars,
            calendar=clients.calendar,
            cache_dir=cache,
            start=protocol.bars.start,
            through=protocol.bars.through,
            adjustment=protocol.adjustment,
            pace=clients.pace,
        )

    return build


@click.command("spread-power")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help=_OUTPUT_HELP)
@coro
async def spread_power_cmd(protocol_path: Path, output: Path) -> None:
    """Check A: the spread discovery pipeline on synthetic worlds with planted pairs (no provider access)."""
    _refuse_existing(output)
    loaded, cohort = await asyncio.to_thread(_load, protocol_path)
    environment = await asyncio.to_thread(research_environment)
    result = await execute_spread_power(loaded, output, cohort=cohort, environment=environment, progress=_progress)
    _echo(result, "Spread power check failed; see result.json for the reason")


@click.command("spread-null")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help=_OUTPUT_HELP)
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_CACHE_HELP)
@coro
async def spread_null_cmd(protocol_path: Path, output: Path, cache: Path) -> None:
    """Check C: the spread discovery pipeline on per-symbol shifted real bars (false-acceptance rate)."""
    _refuse_existing(output)
    loaded, cohort = await asyncio.to_thread(_load, protocol_path)
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_spread_null(
            loaded,
            output,
            cohort=cohort,
            build=_panel_builder(loaded, cohort, clients, cache),
            environment=environment,
            progress=_progress,
        )
    _echo(result, "Spread null check failed; see result.json for the reason")


@click.command("spread-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--power", "power_dir", type=click.Path(exists=True, path_type=Path), required=True, help="Check A's directory"
)
@click.option(
    "--null-check", "null_dir", type=click.Path(exists=True, path_type=Path), required=True, help="Check C's directory"
)
@click.option("--output", type=click.Path(path_type=Path), required=True, help=_OUTPUT_HELP)
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_CACHE_HELP)
@click.option(
    "--journal-scope", required=True, help="The journal scope the one-use confirmation is recorded in (must match)"
)
@coro
async def spread_study_cmd(
    protocol_path: Path, power_dir: Path, null_dir: Path, output: Path, cache: Path, journal_scope: str
) -> None:
    """The predeclared pairs study: discovery, then a journaled one-use confirmation. Research only; never promotes."""
    _refuse_existing(output)
    uncommitted = await asyncio.to_thread(_uncommitted_research_files)
    if uncommitted:
        raise click.ClickException(f"refusing to run with uncommitted research files:\n{uncommitted}")
    environment = await asyncio.to_thread(research_environment)
    try:
        require_clean_revision(environment)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    loaded, cohort = await asyncio.to_thread(_load, protocol_path)
    async with alpha_repository() as repository:
        _require_journal(repository, journal_scope)
        journal = journal_identity(repository)

        async def confirm(interval, detail):
            return await repository.consume_lane_confirmation(
                LANE, protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, interval=interval, detail=detail
            )

        with _apriori_clients() as clients:
            result = await execute_spread_study(
                loaded,
                output,
                cohort=cohort,
                build=_panel_builder(loaded, cohort, clients, cache),
                environment=environment,
                power_dir=power_dir,
                null_dir=null_dir,
                journal=journal,
                confirm=confirm,
                progress=_progress,
            )
    _echo(result, "Spread study failed; see result.json for the reason")
```

In `agentic_trader/cli/main.py`, next to the `alpha_liquidity` import and registration:

```python
from agentic_trader.cli.commands.alpha_spread import spread_null_cmd, spread_power_cmd, spread_study_cmd

...
alpha_group.add_command(spread_power_cmd)
alpha_group.add_command(spread_null_cmd)
alpha_group.add_command(spread_study_cmd)
```

- [ ] **Step 4: Run the CLI tests and the whole spread suite**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread -q`
Expected: PASS.

- [ ] **Step 5: Write `docs/alpha-spread-lane.md`**

Sections, each a short paragraph or table (take values from the spec; do not restate code):
1. **Purpose and status** — desk-direction item 2; research only; `authorizes_promotion: false`; no probe or execution contract exists; `/pairs` stays display-only.
2. **Cohort** — the 127-name same-sector cohort, where it comes from, the survivorship caveat, hash pinning.
3. **Protocol** — the table from spec §4 (windows, schedule, formation, trading, costs, bootstrap, pass rules, confirmation rules).
4. **Causality and accounting** — formation-only estimation, frozen `α, β, σ_f`, next-open fills, gap handling, fixed-share marks, dollar weights, cost formula `2c/10⁴`, committed-capital lane.
5. **Pass rules S1–S4 and decisions** — `failed_discovery`, `failed_confirmation`, `confirmed`; what each permits (nothing beyond specifying a probe).
6. **Checks A and C** — what each simulates, pass criteria, and that the study refuses without both at the same revision.
7. **Journal** — `spread/confirmation` one-use interval, lane-wide refusal of overlaps, no `family/all` charge.
8. **Running it** — the three commands from spec §10 with the pooled cache path, output naming convention, runtime estimate, and the rule that runs stay outside the 10:35/14:35 NY scan windows.
9. **Known limits** — spec §8 verbatim in prose.
10. **Relation to the display module** — the `pairs` package's in-sample fit and plain-ADF p-values are unsuitable as evidence; the lane uses `coint` with MacKinnon p-values and frozen formation parameters.

- [ ] **Step 6: Update the other docs**

- `CLAUDE.md`: after the paragraph ending "The next alpha source is an operator decision.", add:

  > The [spread-reversion lane](docs/alpha-spread-lane.md) (desk-direction item 2) is a predeclared, research-only pairs protocol on daily SIP bars: same-sector pairs from a frozen 127-name cohort, Engle-Granger formation (`coint`, MacKinnon p-values) over 252 sessions, z-score trading over the next 126 with frozen `α, β, σ_f` and next-open fills, four pass rules (lane bootstrap, trade means, counts, SPY beta) and a one-use confirmation window journaled under `spread/confirmation` before it is read. `alpha spread-study` refuses without passing `spread-power` (A) and `spread-null` (C) results at the same clean revision; nothing promotes, no probe or two-leg execution contract exists, and `/pairs` stays display-only.

- `docs/alpha-roadmap.md` item 1: append a *Status (October 10)* line: bracket construction is closed by evidence — the October 8 counterfactual grid and the October 10 refresh (runner-ups 30% target / 70% stop, −0.17R; sent 20% / 80%, −0.47R; `setup_quality` within noise of random) show no bracket geometry or time exit with non-negative EV, so the construction-side control is `card_policy` (operator: `off → preview → enforce`), not bracket tuning. Item 2: *Status (October 10)*: the lane is implemented ([spread lane](alpha-spread-lane.md)); checks A and C and the study run after merge; results are bundled with the next workstream PR.
- `docs/strategies.md` §4: add one paragraph after the formulations: the display screener fits and scores in-sample with plain ADF p-values and is not research evidence; the research protocol lives in `docs/alpha-spread-lane.md`.
- `docs/cli-reference.md`: add `copilot alpha spread-power`, `spread-null`, `spread-study` with their options next to the other `alpha` research commands.

- [ ] **Step 7: Full suite, lint and commit**

```bash
env -u VIRTUAL_ENV uv run pytest -q -x
env -u VIRTUAL_ENV uv run pre-commit run --all-files
git add agentic_trader/cli/commands/alpha_spread.py agentic_trader/cli/main.py tests/research/spread/test_cli.py docs/alpha-spread-lane.md CLAUDE.md docs/alpha-roadmap.md docs/strategies.md docs/cli-reference.md
git commit -m "Spread lane: alpha spread-power/spread-null/spread-study commands; lane docs, roadmap status (bracket construction closed by evidence; item 2 shipped), strategies caveat, CLI reference"
```
