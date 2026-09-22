# Wider Scan Universe and Daily Suggestion Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scan about 160 liquid US equities and ETFs with the existing built-in strategies at two session-aligned times a day, rank the candidates, and send at most one card per scan and two per session to Telegram, so the desk produces a steady, bounded flow of suggestions on the Alpaca paper account.

**Architecture:** A validated `universe:` config key expands into equity contracts and sector correlation groups at load time. `run_scan` is restructured into three phases: a bounded, paced concurrent fetch; a sequential per-symbol phase (shadow observation, strategy scan, dedup, deterministic risk evaluation) that collects candidates; and a rank-and-send phase that spends a per-scan/per-session/per-sector budget, calling the LLM only for the candidates it will send. Two cron jobs in New York time drive the suggestion scans; the intraday job is restricted to the pre-existing instruments; an end-of-session digest is published through the existing outbox. No admission, bracket, sizing-formula, halt or macro-lockout logic changes.

**Tech Stack:** Python 3.14, `uv`, Pydantic v2, APScheduler (AsyncIOScheduler), pandas, pytest (async auto mode), Click.

**Spec:** `docs/superpowers/specs/2026-09-21-scan-universe-and-suggestion-budget-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-scan-universe` (branch `feat/scan-universe`). Never touch the installed checkout `/Users/adamhadani/Development/agentic-trader`; a live daemon and a watchdog run from it. Never run `launchctl` or `scripts/launchd.sh`.
- Universe decided with the operator: ETF32 + about 65 mega-caps + the 64-name screened research cohort (about 160 names). Paper caps decided with the operator: `portfolio.max_concurrent_positions: 8`, `sizing.max_trade_notional_cap: 7500`. `max_notional_exposure` (60000), `max_equity_exposure` (40000) and `max_stop_risk_pct` (0.02) are unchanged.
- Defaults (verbatim from the spec): scan times `["10:35", "14:35"]` New York; `max_cards_per_scan = 1`; `max_cards_per_session = 2`; one card per correlation group per session; `max_llm_evaluations_per_scan = 4`; `min_bar_coverage = 0.8` over the last 10 sessions against SPY; `scan_concurrency = 8`; `max_requests_per_minute = 150`.
- The per-session card count is DERIVED from the signals table for the current New York trading day (this environment and execution mode, excluding quarantined rows), never stored in a counter.
- Explicit `contracts:` entries win over `universe:` entries for the same symbol. Futures stay in `contracts:`.
- The 15-minute intraday job must NOT scan universe-sourced contracts; it keeps the pre-existing instrument set (contracts not sourced from `universe:`).
- A failed or empty fetch excludes that name from the current scan only, is counted and logged, and never aborts the scan. The scan never fails because of the coverage gate, the digest, or metrics.
- `setup_quality` is a transparent prioritisation heuristic in [0, 1], stored in `decision_provenance`; it is never presented as validated alpha.
- No change to `execution/admission.py`, brackets, `position_sizing.py`, halt behaviour, the macro lockout, `ValidationPolicy`, or any Alembic migration (schema head stays `008_alpha_pipeline`).
- With `universe: {}` and default `scan:` values, the scan's candidate set for the existing 13 instruments must be unchanged; the only behavioural differences in that configuration are the phases (fetch first), the deterministic-then-LLM evaluation order, and the budget (which defaults on for scheduled scans).
- Tests: temporary SQLite, blocked network, calendar-independent (derive times from the real clock or pass explicit clocks/`now`; never compare a fixed date with real now). Pre-existing tests pass unmodified.
- Commit process: `uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader`, focused tests, then one `git commit`; the commit hook is the pre-commit gate (ruff, mypy, impacted-tests pytest). Never `--no-verify`. Commit messages end with: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

## File Structure

| File | Responsibility |
| --- | --- |
| `agentic_trader/constants.py` (modify) | `CRYPTO_SYMBOL_PREFIXES` |
| `agentic_trader/market/session.py` (modify) | use the shared prefix constant |
| `agentic_trader/config.py` (modify) | `UniverseEntry`, `UniverseConfig`, `ScanConfig`, `ScanBudget`, new fields on `SchedulerConfig`/`MarketDataConfig`/`AppConfig`, expansion in `load_config` |
| `config/config.yaml` (modify) | `universe:` groups, `scan:`, scheduler times, paper caps |
| `agentic_trader/data/pacing.py` (create) | `RequestPacer` sliding-window limiter |
| `agentic_trader/data/market_data.py` (modify) | pacer on `fetch_data`; remove dead cache |
| `agentic_trader/screeners/coverage.py` (create) | hourly-bar coverage gate |
| `agentic_trader/screeners/base.py`, `strategies.py`, `formulaic.py` (modify) | `setup_quality` |
| `agentic_trader/storage/db.py` (modify) | `signals_since(cutoff)` |
| `agentic_trader/agent/copilot.py` (modify) | three-phase `run_scan`, budget, digest |
| `agentic_trader/cli/commands/service.py` (modify) | cron suggestion scans, intraday scope, digest trigger |
| `agentic_trader/cli/commands/scan.py` (modify) | `--no-budget` |
| `tests/config/test_universe_config.py`, `tests/data/test_pacing.py`, `tests/screeners/test_coverage.py`, `tests/screeners/test_setup_quality.py`, `tests/agent/test_scan_budget.py`, `tests/agent/test_scan_fetch_phase.py`, `tests/cli/test_service_scheduling.py` (create) | Tests |

---

### Task 1: Universe and scan configuration

**Files:**
- Modify: `agentic_trader/constants.py` (near `DEFAULT_RESEARCH_SYMBOL`)
- Modify: `agentic_trader/market/session.py:1058-1061` (`_resolve_provider`)
- Modify: `agentic_trader/config.py` (after `ContractConfig` ~line 96; `SchedulerConfig` ~174; `MarketDataConfig` ~384; `AppConfig` ~529; `load_config` construction ~765)
- Modify: `config/config.yaml`
- Test: `tests/config/test_universe_config.py`

**Interfaces:**
- Produces:
  - `constants.CRYPTO_SYMBOL_PREFIXES: tuple[str, ...] = ("BTC", "ETH", "SOL", "DOGE")`
  - `config.UniverseEntry(symbol: str, sector: str = "unknown")`
  - `config.UniverseConfig(groups: dict[str, list[UniverseEntry]], max_symbols: int = 250)` with `.symbols -> tuple[str, ...]`, `.contract_documents() -> dict[str, dict]`, `.sector_groups() -> dict[str, list[str]]`
  - `config.ScanBudget(StrEnum)`: `FULL = "full"`, `SESSION = "session"`, `NONE = "none"`
  - `config.ScanConfig(max_cards_per_scan=1, max_cards_per_session=2, max_cards_per_group_per_session=1, max_llm_evaluations_per_scan=4, min_bar_coverage=0.8, coverage_sessions=10, coverage_reference_symbol="SPY")`
  - `SchedulerConfig.suggestion_scan_times_et: list[str] = ["10:35", "14:35"]`
  - `MarketDataConfig.scan_concurrency: int = 8`, `.max_requests_per_minute: int = 150`
  - `AppConfig.universe: UniverseConfig`, `AppConfig.scan: ScanConfig`, `AppConfig.non_universe_contracts -> list[str]` (property)
  - `load_config` merges universe contracts (explicit wins) and sector groups into `portfolio.correlation_groups`.

- [ ] **Step 1: Write the failing tests**

Create `tests/config/test_universe_config.py`:

```python
import textwrap

import pytest

from agentic_trader.config import ScanBudget, ScanConfig, UniverseConfig, UniverseEntry, load_config
from agentic_trader.constants import AssetClass


def test_universe_expands_to_equity_contracts_and_sector_groups():
    universe = UniverseConfig(
        groups={
            "etfs": [UniverseEntry(symbol="XLK", sector="etf_sector"), UniverseEntry(symbol="SPY", sector="etf_broad")],
            "stocks": [UniverseEntry(symbol="AAPL", sector="technology"), UniverseEntry(symbol="ZZZ")],
        }
    )
    assert universe.symbols == ("AAPL", "SPY", "XLK", "ZZZ")
    assert universe.contract_documents()["AAPL"] == {"ticker": "AAPL", "name": "AAPL", "asset_class": "equity"}
    assert universe.sector_groups() == {
        "sector_etf_broad": ["SPY"],
        "sector_etf_sector": ["XLK"],
        "sector_technology": ["AAPL"],
    }  # "unknown" never forms a group


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ({"a": [{"symbol": "SPY"}], "b": [{"symbol": "SPY"}]}, "Duplicate"),
        ({"a": [{"symbol": "SOLV"}]}, "crypto"),
        ({"a": [{"symbol": "spy"}]}, "pattern"),
        ({"a": [{"symbol": "TOOLONGX"}]}, "pattern"),
        ({"a": [{"symbol": "SPY", "sector": "Not Snake"}]}, "pattern"),
    ],
)
def test_invalid_universes_are_rejected(groups, message):
    with pytest.raises(ValueError, match=message):
        UniverseConfig(groups=groups)


def test_universe_size_is_bounded():
    entries = [{"symbol": symbol} for symbol in ("AAA", "BBB", "CCC")]
    with pytest.raises(ValueError, match="max_symbols"):
        UniverseConfig(groups={"a": entries}, max_symbols=2)


def test_scan_config_defaults_match_the_spec():
    assert ScanConfig().model_dump() == {
        "max_cards_per_scan": 1,
        "max_cards_per_session": 2,
        "max_cards_per_group_per_session": 1,
        "max_llm_evaluations_per_scan": 4,
        "min_bar_coverage": 0.8,
        "coverage_sessions": 10,
        "coverage_reference_symbol": "SPY",
    }
    assert [b.value for b in ScanBudget] == ["full", "session", "none"]


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_load_config_merges_universe_contracts_with_explicit_contracts_winning(tmp_path):
    path = write(
        tmp_path,
        """
        contracts:
          "SPY":
            ticker: "SPY"
            name: "SPDR S&P 500 ETF Trust"
            asset_class: "equity"
            target_risk_dollars: 400.0
          "/MES":
            ticker: "MES=F"
            name: "Micro E-mini S&P 500"
            multiplier: 5.0
            tick_size: 0.25
        universe:
          groups:
            mega_caps:
              - {symbol: AAPL, sector: technology}
              - {symbol: SPY, sector: etf_broad}
        scheduler:
          suggestion_scan_times_et: ["10:35", "14:35"]
        """,
    )
    config = load_config(path, environ={"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""})
    assert set(config.contracts) == {"SPY", "/MES", "AAPL"}
    assert config.contracts["SPY"].name == "SPDR S&P 500 ETF Trust"  # explicit entry won
    assert config.contracts["SPY"].target_risk_dollars == 400.0
    assert config.contracts["AAPL"].asset_class == AssetClass.EQUITY
    assert config.contracts["AAPL"].tick_size == 0.01 and config.contracts["AAPL"].multiplier == 1.0
    assert config.portfolio.correlation_groups["sector_technology"] == ["AAPL"]
    assert "SPY" in config.portfolio.correlation_groups["us_broad_market"]  # defaults retained
    assert config.non_universe_contracts == ["/MES", "SPY"]
    assert config.scheduler.suggestion_scan_times_et == ["10:35", "14:35"]


def test_load_config_without_universe_is_unchanged(tmp_path):
    path = write(tmp_path, 'contracts:\n  "SPY":\n    ticker: "SPY"\n    name: "SPY"\n    asset_class: "equity"\n')
    config = load_config(path, environ={"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""})
    assert set(config.contracts) == {"SPY"}
    assert config.universe.symbols == ()
    assert config.non_universe_contracts == ["SPY"]


@pytest.mark.parametrize("times", [["25:00"], ["9:5"], ["10:35", "10:35"], []])
def test_invalid_suggestion_scan_times_are_rejected(times, tmp_path):
    from agentic_trader.config import SchedulerConfig

    with pytest.raises(ValueError):
        SchedulerConfig(suggestion_scan_times_et=times)
```

If `load_config` requires other environment keys in production mode (read its body), pass the minimum that lets it load a YAML path without touching `.envrc`: the test passes `COPILOT_ENV_FILE=""` so dotenv loading is disabled; if it still needs a database setting, add the smallest `database:` block to the YAML in `write`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/config/test_universe_config.py -q`
Expected: `ImportError: cannot import name 'UniverseConfig'`.

- [ ] **Step 3: Implement the constant and the session refactor**

`agentic_trader/constants.py` (near `DEFAULT_RESEARCH_SYMBOL`):

```python
# Symbol prefixes the session router treats as crypto; equity tickers must not collide.
CRYPTO_SYMBOL_PREFIXES: tuple[str, ...] = ("BTC", "ETH", "SOL", "DOGE")
```

`agentic_trader/market/session.py:_resolve_provider` — replace the literal tuple with `CRYPTO_SYMBOL_PREFIXES` (import from `agentic_trader.constants`). Behaviour identical.

- [ ] **Step 4: Implement the config models**

In `agentic_trader/config.py`, after `InstrumentConfig = ContractConfig`:

```python
class UniverseEntry(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z.]{0,5}$")
    sector: str = Field(default="unknown", pattern=r"^[a-z][a-z0-9_]{0,31}$")


class UniverseConfig(BaseModel):
    """Named groups of equity/ETF symbols expanded into contracts at load time."""

    groups: dict[str, list[UniverseEntry]] = Field(default_factory=dict)
    max_symbols: int = Field(default=250, ge=1, le=500)

    @model_validator(mode="after")
    def validated(self):
        owner: dict[str, str] = {}
        for group, entries in self.groups.items():
            for entry in entries:
                if entry.symbol in owner:
                    raise ValueError(f"Duplicate universe symbol {entry.symbol} in {group} and {owner[entry.symbol]}")
                if entry.symbol.startswith(CRYPTO_SYMBOL_PREFIXES):
                    raise ValueError(f"{entry.symbol} would be routed to the crypto session provider")
                owner[entry.symbol] = group
        if len(owner) > self.max_symbols:
            raise ValueError(f"Universe has {len(owner)} symbols; max_symbols is {self.max_symbols}")
        return self

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(entry.symbol for entries in self.groups.values() for entry in entries))

    def contract_documents(self) -> dict[str, dict[str, Any]]:
        return {
            entry.symbol: {"ticker": entry.symbol, "name": entry.symbol, "asset_class": "equity"}
            for entries in self.groups.values()
            for entry in entries
        }

    def sector_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for entries in self.groups.values():
            for entry in entries:
                if entry.sector != "unknown":
                    groups.setdefault(f"sector_{entry.sector}", []).append(entry.symbol)
        return {name: sorted(members) for name, members in sorted(groups.items())}


class ScanBudget(StrEnum):
    FULL = "full"  # scheduled scans: per-scan, per-session and per-group limits
    SESSION = "session"  # operator scans: per-session and per-group limits only
    NONE = "none"  # explicit --no-budget or dry runs


class ScanConfig(BaseModel):
    max_cards_per_scan: int = Field(default=1, ge=0)
    max_cards_per_session: int = Field(default=2, ge=0)
    max_cards_per_group_per_session: int = Field(default=1, ge=1)
    max_llm_evaluations_per_scan: int = Field(default=4, ge=1)
    min_bar_coverage: float = Field(default=0.8, ge=0, le=1)
    coverage_sessions: int = Field(default=10, ge=1)
    coverage_reference_symbol: str = Field(default="SPY", pattern=r"^[A-Z][A-Z.]{0,5}$")
```

Import `StrEnum`, `re` (if not already imported) and `CRYPTO_SYMBOL_PREFIXES`. In `SchedulerConfig` add:

```python
suggestion_scan_times_et: list[str] = Field(default_factory=lambda: ["10:35", "14:35"])


@field_validator("suggestion_scan_times_et")
@classmethod
def valid_times(cls, value: list[str]) -> list[str]:
    if not value or len(set(value)) != len(value):
        raise ValueError("suggestion_scan_times_et requires at least one distinct HH:MM time")
    for item in value:
        if not re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", item):
            raise ValueError(f"Invalid HH:MM time: {item}")
    return value
```

In `MarketDataConfig` add:

```python
    scan_concurrency: int = Field(default=8, ge=1, le=32)
    max_requests_per_minute: int = Field(default=150, ge=1, le=1000)
```

In `AppConfig` add fields `universe: UniverseConfig = Field(default_factory=UniverseConfig)` and `scan: ScanConfig = Field(default_factory=ScanConfig)`, plus:

```python
@property
def non_universe_contracts(self) -> list[str]:
    """Instruments configured explicitly under contracts:, i.e. the pre-universe scan set."""
    universe = set(self.universe.symbols) - set(self.explicit_contracts)
    return sorted(key for key in self.contracts if key not in universe)


explicit_contracts: tuple[str, ...] = ()
```

Then in `load_config`, before the `AppConfig(` call:

```python
    universe = UniverseConfig(**cfg_dict.get("universe", {}))
    explicit_contracts = dict(cfg_dict.get("contracts", {}))
    contract_documents = {**universe.contract_documents(), **explicit_contracts}  # explicit wins
    portfolio_dict = dict(cfg_dict.get("portfolio", {}))
    groups = {k: list(v) for k, v in dict(portfolio_dict.get("correlation_groups", DEFAULT_CORRELATION_GROUPS)).items()}
    for name, members in universe.sector_groups().items():
        groups[name] = sorted(set(groups.get(name, [])) | set(members))
    portfolio_dict["correlation_groups"] = groups
```

and in the call replace `portfolio=PortfolioConfig(**cfg_dict.get("portfolio", {}))` with `portfolio=PortfolioConfig(**portfolio_dict)`, replace the `contracts=` line with `contracts={k: ContractConfig(**v) for k, v in contract_documents.items()}`, and add `universe=universe, scan=ScanConfig(**cfg_dict.get("scan", {})), explicit_contracts=tuple(explicit_contracts)`.

- [ ] **Step 5: Author the universe YAML**

Write a throwaway script in the scratchpad directory (NOT in the repo) that prints the `universe:` block: groups `etf32` (the 32 symbols in `agentic_trader/research/alpha/universe.py:ETF_RESEARCH_UNIVERSE`, sector = `etf_<group>` from that file's group names, e.g. `etf_broad_equity`, `etf_sector`, `etf_international`, `etf_rates_credit`, `etf_metals`), `mega_caps` (the 65 tickers listed in the September 21 spike: AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V MA UNH XOM LLY JNJ PG HD COST ABBV MRK CVX KO PEP WMT BAC CRM ORCL AMD NFLX ADBE CSCO TMO ACN MCD ABT LIN DHR TXN QCOM INTC INTU AMGN IBM GE CAT HON UNP LOW SPGI GS MS BLK AXP BKNG NKE SBUX DIS PFE T VZ CMCSA BA C WFC PM) and `research_cohort` (the 64 `symbols` in `config/research/prospective-equity-panel-iex-v1.json`). For equities, fetch the sector once with `yfinance.Ticker(sym).info.get("sector")`, convert to snake_case (`"Consumer Cyclical"` → `consumer_cyclical`), and use `unknown` when missing; this is an authoring step with network, run once, and the script is not committed. Symbols already present under `contracts:` (SPY QQQ IWM GLD TLT AAPL AMD MSFT NVDA) may appear in the universe too — the explicit entry wins. Paste the block into `config/config.yaml`, review it by eye (spot-check five sectors), and add:

```yaml
scan:
  max_cards_per_scan: 1
  max_cards_per_session: 2
  max_cards_per_group_per_session: 1
  max_llm_evaluations_per_scan: 4
  min_bar_coverage: 0.8
  coverage_sessions: 10
  coverage_reference_symbol: "SPY"
```

Under `scheduler:` add `suggestion_scan_times_et: ["10:35", "14:35"]`. Under `market_data:` add `scan_concurrency: 8` and `max_requests_per_minute: 150`. Change `portfolio.max_concurrent_positions: 4` → `8` and `sizing.max_trade_notional_cap` → `7500.0` (add the key if absent), each with a one-line comment "paper desk cap (September 22)". Verify with `uv run python -c "from agentic_trader.config import load_config; c=load_config(); print(len(c.contracts), len(c.universe.symbols), c.portfolio.max_concurrent_positions, c.sizing.max_trade_notional_cap)"` run with `COPILOT_ENV_FILE=""` so no secrets are read — expected about 170, about 160, 8, 7500.0.

- [ ] **Step 6: Run**

Run: `uv run pytest tests/config -q tests/market -q`
Expected: all pass, including every pre-existing config and session test.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/constants.py agentic_trader/market/session.py agentic_trader/config.py config/config.yaml tests/config/test_universe_config.py
git commit -m "feat(config): add a validated scan universe, scan budget settings and paper caps

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Paced, bounded, accounted fetch phase

**Files:**
- Create: `agentic_trader/data/pacing.py`
- Modify: `agentic_trader/data/market_data.py:60-76` (constructor) and `fetch_data`
- Modify: `agentic_trader/agent/copilot.py` (`run_scan`, the per-contract loop ~371-395)
- Test: `tests/data/test_pacing.py`, `tests/agent/test_scan_fetch_phase.py`

**Interfaces:**
- Produces:
  - `data.pacing.RequestPacer(max_per_minute: int, *, clock=time.monotonic, sleep=time.sleep)` with `.acquire() -> None` (thread-safe).
  - `MarketDataFetcher(..., pacer: RequestPacer | None = None)`; `fetch_data` calls `self.pacer.acquire()` before every `provider.fetch_bars`.
  - `TradingCopilot._fetch_universe(instruments: list[tuple[str, ContractConfig]], *, include_fifteen_min: bool) -> dict[str, ContractMarketData | BaseException]`
  - `run_scan` gains `include_fifteen_min: bool | None = None` (default: only when `timeframe == "15m"`) and records `scan_completed` (log) and `trader_scan_duration_seconds` (histogram) with counts `scanned`, `fetch_failed`, `insufficient`, `skipped_closed_session`.

- [ ] **Step 1: Write the failing tests**

Create `tests/data/test_pacing.py`:

```python
from agentic_trader.data.pacing import RequestPacer


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_pacer_allows_a_burst_up_to_the_limit_then_waits_for_the_window():
    clock = FakeClock()
    pacer = RequestPacer(3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        pacer.acquire()
    assert clock.slept == []
    pacer.acquire()  # fourth request within the same minute must wait for the oldest to age out
    assert len(clock.slept) == 1 and 59.9 <= clock.slept[0] <= 60.0


def test_pacer_frees_capacity_as_the_window_slides():
    clock = FakeClock()
    pacer = RequestPacer(2, clock=clock, sleep=clock.sleep)
    pacer.acquire()
    clock.now += 30
    pacer.acquire()
    clock.now += 31  # first request is now 61 s old
    pacer.acquire()
    assert clock.slept == []


def test_pacer_is_safe_across_threads():
    import threading

    clock = FakeClock()
    lock = threading.Lock()

    def sleep(seconds):
        with lock:
            clock.sleep(seconds)

    pacer = RequestPacer(5, clock=clock, sleep=sleep)
    threads = [threading.Thread(target=pacer.acquire) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(clock.slept) >= 1
```

Create `tests/agent/test_scan_fetch_phase.py`. First locate the `scan_desk` fixture in `tests/agent/test_probe_scan_integration.py` (lines ~63-95); move it into `tests/agent/conftest.py` (create the file if absent, keeping its imports) so both modules share it, and leave `test_probe_scan_integration.py` importing nothing new (pytest discovers conftest fixtures). Then:

```python
import asyncio
from types import SimpleNamespace

import pandas as pd

from agentic_trader.constants import AssetClass


def frame(rows=30):
    return pd.DataFrame({"Close": [100.0] * rows, "Volume": [1000] * rows})


def instrument(symbol, asset_class="EQUITY"):
    return SimpleNamespace(name=symbol, ticker=symbol, asset_class=asset_class)


async def test_fetch_phase_is_bounded_concurrent_and_accounts_failures(scan_desk, app_config):
    app_config.contracts = {s: instrument(s) for s in ("AAA", "BBB", "CCC", "DDD")}
    app_config.market_data.scan_concurrency = 2
    import threading
    import time

    counter = {"in_flight": 0, "peak": 0}
    lock = threading.Lock()

    def fetch(contract, ticker, include_fifteen_min=True):
        # asyncio.to_thread runs this in a worker thread; guard the counters with a real lock
        with lock:
            counter["in_flight"] += 1
            counter["peak"] = max(counter["peak"], counter["in_flight"])
        time.sleep(0.05)
        with lock:
            counter["in_flight"] -= 1
        if contract == "BBB":
            raise ConnectionError("boom")
        if contract == "CCC":
            return SimpleNamespace(daily=pd.DataFrame(), four_hour=pd.DataFrame(), hourly=pd.DataFrame())
        return SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())

    scan_desk.data_fetcher.fetch_data.side_effect = fetch
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    assert counter["peak"] <= 2
    calls = scan_desk.data_fetcher.fetch_data.call_args_list
    assert len(calls) == 4
    assert all(call.kwargs.get("include_fifteen_min") is False for call in calls)
    summary = scan_desk.last_scan_summary
    assert summary["fetch_failed"] == ["BBB"] and summary["insufficient"] == ["CCC"]
    assert summary["scanned"] == 2


async def test_fifteen_minute_scans_still_fetch_fifteen_minute_bars(scan_desk, app_config):
    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=True, timeframe="15m")
    assert scan_desk.data_fetcher.fetch_data.call_args.kwargs.get("include_fifteen_min") is True


async def test_closed_equity_session_skips_equity_fetches_but_not_futures(scan_desk, app_config):
    app_config.contracts = {"AAA": instrument("AAA"), "/MES": instrument("/MES", "FUTURES")}

    async def is_session_active(instrument_type="all", timestamp=None):
        return (instrument_type != "equity", "test")

    scan_desk.session_provider.is_session_active.side_effect = is_session_active
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    fetched = {call.args[0] for call in scan_desk.data_fetcher.fetch_data.call_args_list}
    assert fetched == {"/MES"}
    assert scan_desk.last_scan_summary["skipped_closed_session"] == ["AAA"]


async def test_scan_duration_is_recorded(scan_desk, app_config):
    from unittest.mock import MagicMock

    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.metrics = MagicMock()
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    names = [call.args[0] for call in scan_desk.metrics.observe_histogram.call_args_list]
    assert "trader_scan_duration_seconds" in names
```

The `scan_desk` fixture's `is_session_active` mock returns `(True, "Open")` for any instrument type; the third test overrides it. Read `run_scan`'s existing session gate (it calls `is_session_active(instrument_type=asset_class or "all")` once) before writing the implementation so the new equity check is an additional call, not a replacement.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/data/test_pacing.py tests/agent/test_scan_fetch_phase.py -q`
Expected: `ModuleNotFoundError: agentic_trader.data.pacing` and `AttributeError: ... last_scan_summary`.

- [ ] **Step 3: Implement the pacer and fetcher changes**

Create `agentic_trader/data/pacing.py`:

```python
"""Client-side request pacing shared across the scan's worker threads."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS = 60.0


class RequestPacer:
    """Sliding-window limiter: at most ``max_per_minute`` acquisitions in any 60 s window."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_per_minute < 1:
            raise ValueError("max_per_minute must be positive")
        self.max_per_minute = max_per_minute
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._stamps: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= WINDOW_SECONDS:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_per_minute:
                    self._stamps.append(now)
                    return
                wait = WINDOW_SECONDS - (now - self._stamps[0])
            self._sleep(max(wait, 0.0))
```

`agentic_trader/data/market_data.py`: add `pacer: RequestPacer | None = None` to `MarketDataFetcher.__init__`, store `self.pacer = pacer or RequestPacer((config or cfg).market_data.max_requests_per_minute)` — read the constructor: `cfg` exists only in the `else` branch, so compute the limit as `(config.market_data.max_requests_per_minute if config else DEFAULT_MAX_REQUESTS_PER_MINUTE)` with `DEFAULT_MAX_REQUESTS_PER_MINUTE = 150` defined in `agentic_trader/constants.py`. Delete the dead `self._cache: dict[str, ContractMarketData] = {}` line (keep the `cache_ttl_seconds` parameter if any caller passes it — `grep -rn "cache_ttl_seconds" agentic_trader tests`). In `fetch_data`, call `self.pacer.acquire()` immediately before each of the three `self.provider.fetch_bars(...)` calls.

- [ ] **Step 4: Implement the fetch phase in `run_scan`**

In `agentic_trader/agent/copilot.py` add to `run_scan`'s signature `include_fifteen_min: bool | None = None`, and at the start of the body (inside the lock, after the halt/session/lockout gates) set:

```python
            started = time.monotonic()
            if include_fifteen_min is None:
                include_fifteen_min = (timeframe or "").strip().lower() == "15m"
            summary: dict[str, Any] = {
                "scanned": 0,
                "fetch_failed": [],
                "insufficient": [],
                "skipped_closed_session": [],
                "coverage_excluded": [],
                "candidates": 0,
                "approved": 0,
                "sent": 0,
                "runners_up": [],
            }
            equity_open = True
            if not dry_run and not bypass_session_filter:
                equity_open, _ = await self.session_provider.is_session_active(instrument_type="equity")
```

Replace the head of the per-contract loop with a selection + fetch phase:

```python
            selected: list[tuple[str, Any]] = []
            for contract, info in self.config.contracts.items():
                clean_contract = contract.strip("/").upper()
                if target_syms and (contract.upper() not in target_syms and clean_contract not in target_syms):
                    continue
                inst_class = getattr(info, "asset_class", AssetClass.FUTURES)
                inst_class_norm = normalize_asset_class(str(inst_class))
                req_class_norm = normalize_asset_class(asset_class)
                if asset_class and asset_class.lower() != "all" and inst_class_norm != req_class_norm:
                    continue
                if inst_class_norm == normalize_asset_class("equity") and not equity_open:
                    summary["skipped_closed_session"].append(contract)
                    continue
                selected.append((contract, info))
            datasets = await self._fetch_universe(selected, include_fifteen_min=include_fifteen_min)
            for contract, info in selected:
                data = datasets.get(contract)
                if isinstance(data, BaseException) or data is None:
                    summary["fetch_failed"].append(contract)
                    logger.warning("Fetch failed for %s: %s", contract, data, extra={"event": "scan_fetch_failed", "contract": contract})
                    continue
                if data.daily.empty or data.four_hour.empty:
                    summary["insufficient"].append(contract)
                    logger.warning(f"Insufficient data for {contract}, skipping.")
                    continue
                summary["scanned"] += 1
                inst_class = getattr(info, "asset_class", AssetClass.FUTURES)
                logger.info(f"Scanning contract {contract} ({info.name} - {info.ticker}) [{inst_class}]...")
                try:
                    if not dry_run:
                        await self.alpha_shadow.observe(alpha_snapshot, data)
                    candidates = await asyncio.to_thread(
                        self.strategy_engine.scan_contract, data, asset_class=inst_class,
                        override_strategy=strategy, override_mode=strategy_mode, timeframe=timeframe,
                    )
                    ...  # the existing candidate loop continues unchanged in this task
```

Add the method:

```python
async def _fetch_universe(self, instruments: list[tuple[str, Any]], *, include_fifteen_min: bool) -> dict[str, Any]:
    """Fetch every instrument's bars with bounded concurrency; failures are returned, not raised."""
    semaphore = asyncio.Semaphore(self.config.market_data.scan_concurrency)

    async def one(contract: str, info: Any):
        async with semaphore:
            return await asyncio.to_thread(
                self.data_fetcher.fetch_data, contract, info.ticker, include_fifteen_min=include_fifteen_min
            )

    results = await asyncio.gather(*(one(c, i) for c, i in instruments), return_exceptions=True)
    return {contract: result for (contract, _), result in zip(instruments, results, strict=True)}
```

At the end of the scan (replacing the `=== Scan Complete` log), record and expose the summary:

```python
summary["duration_seconds"] = round(time.monotonic() - started, 3)
self.last_scan_summary = summary
self.metrics.observe_histogram(
    "trader_scan_duration_seconds", summary["duration_seconds"], help_text="Wall-clock duration of one universe scan"
)
logger.info(
    "=== Scan Complete: %d candidates evaluated, %d alerts emitted ===",
    summary["candidates"],
    summary["sent"],
    extra={"event": "scan_completed", **{k: (len(v) if isinstance(v, list) else v) for k, v in summary.items()}},
)
```

Keep `total_candidates`/`total_alerts` feeding `summary["candidates"]`/`summary["sent"]` for now (Task 5 restructures the candidate loop). Initialise `self.last_scan_summary: dict[str, Any] = {}` in `__init__`. Confirm `self.metrics` exists on the copilot (it is used at `copilot.py:276`); import `time`.

- [ ] **Step 5: Run**

Run: `uv run pytest tests/data tests/agent -q`
Expected: all pass, including `tests/agent/test_probe_scan_integration.py`, `test_timeframe_dedup.py`, `test_account_risk.py` unmodified (their fake `fetch_data` may not accept `include_fifteen_min`; `MagicMock` does — if a test uses a plain function fake without that keyword, that is a pre-existing fake to update in the same commit and say so).

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/data/pacing.py agentic_trader/data/market_data.py agentic_trader/constants.py agentic_trader/agent/copilot.py tests/data/test_pacing.py tests/agent/conftest.py tests/agent/test_scan_fetch_phase.py tests/agent/test_probe_scan_integration.py
git commit -m "feat(scan): pace and bound the fetch phase, account failures, record scan duration

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Data-quality coverage gate

**Files:**
- Create: `agentic_trader/screeners/coverage.py`
- Modify: `agentic_trader/agent/copilot.py` (after the fetch phase)
- Test: `tests/screeners/test_coverage.py`

**Interfaces:**
- Produces: `coverage.active_hourly_bars(frame: pd.DataFrame, sessions: int) -> int`; `coverage.coverage_exclusions(datasets: dict[str, Any], *, reference: str, sessions: int, min_ratio: float, equities: set[str]) -> tuple[set[str], str | None]` (excluded symbols, note when the gate is skipped).

- [ ] **Step 1: Write the failing tests**

Create `tests/screeners/test_coverage.py`:

```python
from types import SimpleNamespace

import numpy as np
import pandas as pd

from agentic_trader.screeners.coverage import active_hourly_bars, coverage_exclusions


def hourly(days, bars_per_day=7, volume=1000):
    idx = pd.date_range("2026-08-03 09:30", periods=days * 24, freq="h", tz="America/New_York")
    idx = idx[idx.indexer_between_time("09:30", "15:30")][: days * bars_per_day]
    assert len(idx) == days * bars_per_day
    return pd.DataFrame({"Close": 100.0, "Volume": volume}, index=idx)


def test_active_bars_counts_positive_volume_in_the_last_sessions_only():
    frame = hourly(12)
    frame.iloc[:7, frame.columns.get_loc("Volume")] = 0  # first session silent, but it is outside the last 10
    assert active_hourly_bars(frame, sessions=10) == 10 * 7
    frame.iloc[-3:, frame.columns.get_loc("Volume")] = 0
    assert active_hourly_bars(frame, sessions=10) == 10 * 7 - 3


def test_thin_names_are_excluded_relative_to_the_reference():
    datasets = {
        "SPY": SimpleNamespace(hourly=hourly(12)),
        "GOOD": SimpleNamespace(hourly=hourly(12)),
        "THIN": SimpleNamespace(hourly=hourly(12, volume=0)),
        "/MES": SimpleNamespace(hourly=hourly(12, volume=0)),
    }
    excluded, note = coverage_exclusions(
        datasets, reference="SPY", sessions=10, min_ratio=0.8, equities={"SPY", "GOOD", "THIN"}
    )
    assert excluded == {"THIN"} and note is None  # futures are never gated


def test_missing_reference_skips_the_gate_with_a_note():
    datasets = {"GOOD": SimpleNamespace(hourly=hourly(12))}
    excluded, note = coverage_exclusions(datasets, reference="SPY", sessions=10, min_ratio=0.8, equities={"GOOD"})
    assert excluded == set() and "SPY" in note


def test_failed_reference_frame_skips_the_gate():
    datasets = {"SPY": ConnectionError("boom"), "GOOD": SimpleNamespace(hourly=hourly(12))}
    excluded, note = coverage_exclusions(
        datasets, reference="SPY", sessions=10, min_ratio=0.8, equities={"GOOD", "SPY"}
    )
    assert excluded == set() and note
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/screeners/test_coverage.py -q` — expected `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `agentic_trader/screeners/coverage.py`:

```python
"""Feed-agnostic data-quality gate: hourly bars with volume, relative to a reference name."""

from __future__ import annotations

from typing import Any

import pandas as pd


def active_hourly_bars(frame: pd.DataFrame, sessions: int) -> int:
    if frame is None or frame.empty or "Volume" not in frame.columns:
        return 0
    days = pd.DatetimeIndex(frame.index).normalize()
    recent = sorted(set(days))[-sessions:]
    mask = days.isin(recent) & (frame["Volume"].to_numpy() > 0)
    return int(mask.sum())


def coverage_exclusions(
    datasets: dict[str, Any], *, reference: str, sessions: int, min_ratio: float, equities: set[str]
) -> tuple[set[str], str | None]:
    """Return equities whose active-bar count is below ``min_ratio`` of the reference's, or skip with a note."""
    ref = datasets.get(reference)
    if ref is None or isinstance(ref, BaseException) or getattr(ref, "hourly", None) is None:
        return set(), f"Coverage gate skipped: reference {reference} unavailable"
    ref_count = active_hourly_bars(ref.hourly, sessions)
    if ref_count == 0:
        return set(), f"Coverage gate skipped: reference {reference} has no active hourly bars"
    excluded = set()
    for symbol in equities:
        data = datasets.get(symbol)
        if data is None or isinstance(data, BaseException) or symbol == reference:
            continue
        if active_hourly_bars(getattr(data, "hourly", pd.DataFrame()), sessions) < min_ratio * ref_count:
            excluded.add(symbol)
    return excluded, None
```

In `run_scan`, after `datasets = await self._fetch_universe(...)` and before the sequential loop:

```python
equities = {
    c
    for c, i in selected
    if normalize_asset_class(str(getattr(i, "asset_class", ""))) == normalize_asset_class("equity")
}
excluded, coverage_note = coverage_exclusions(
    datasets,
    reference=self.config.scan.coverage_reference_symbol,
    sessions=self.config.scan.coverage_sessions,
    min_ratio=self.config.scan.min_bar_coverage,
    equities=equities,
)
summary["coverage_excluded"] = sorted(excluded)
summary["coverage_note"] = coverage_note
if coverage_note:
    logger.warning(coverage_note, extra={"event": "coverage_gate_skipped"})
```

and in the sequential loop skip `contract in excluded` right after the insufficiency check. Add a copilot-level test to `tests/agent/test_scan_fetch_phase.py`: with SPY full-volume and one name zero-volume, `strategy_engine.scan_contract` is never called for the thin name and `last_scan_summary["coverage_excluded"] == ["THIN"]`.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/screeners/test_coverage.py tests/agent -q` — expected pass.

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/screeners/coverage.py agentic_trader/agent/copilot.py tests/screeners/test_coverage.py tests/agent/test_scan_fetch_phase.py
git commit -m "feat(scan): exclude thin names with a reference-relative coverage gate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `setup_quality` on every candidate

**Files:**
- Modify: `agentic_trader/screeners/base.py` (`ScreenerCandidate`)
- Modify: `agentic_trader/screeners/strategies.py` (trend-pullback ~lines 60-176; squeeze `evaluate_timeframe` ~200-296)
- Modify: `agentic_trader/screeners/formulaic.py` (candidate construction ~129-150)
- Test: `tests/screeners/test_setup_quality.py`

**Interfaces:**
- Produces: `ScreenerCandidate.setup_quality: float = Field(default=0.0, ge=0.0, le=1.0)`; `screeners.base.clamp01(x: float) -> float`; trend-pullback, squeeze and formulaic candidates populate it.

- [ ] **Step 1: Write the failing tests**

Create `tests/screeners/test_setup_quality.py`:

```python
import pytest

from agentic_trader.screeners.base import ScreenerCandidate, clamp01
from agentic_trader.screeners.strategies import squeeze_setup_quality, trend_pullback_setup_quality


@pytest.mark.parametrize(
    ("value", "expected"), [(-1.0, 0.0), (0.0, 0.0), (0.4, 0.4), (1.0, 1.0), (7.0, 1.0), (float("nan"), 0.0)]
)
def test_clamp01(value, expected):
    assert clamp01(value) == expected


def test_candidate_defaults_to_zero_quality_and_rejects_out_of_range():
    kwargs = dict(
        contract="X",
        timeframe="4h",
        strategy="s",
        direction="LONG",
        current_price=1.0,
        ema_20=1.0,
        ema_50=1.0,
        ema_200=1.0,
        rsi_14=50.0,
        atr_14=1.0,
        candle_timestamp="t",
        recent_swing_low=0.9,
        recent_swing_high=1.1,
        trigger_detail="d",
    )
    assert ScreenerCandidate(**kwargs).setup_quality == 0.0
    with pytest.raises(ValueError):
        ScreenerCandidate(**kwargs, setup_quality=1.5)


def test_trend_pullback_quality_is_monotone_in_each_component():
    base = dict(
        daily_fast=105.0,
        daily_slow=100.0,
        dist_to_trigger=0.5,
        tolerance=1.0,
        rsi_now=45.0,
        rsi_extreme=35.0,
        recovery_span=15.0,
    )
    q = trend_pullback_setup_quality(**base)
    assert 0.0 < q < 1.0
    assert trend_pullback_setup_quality(**{**base, "daily_fast": 110.0}) > q  # stronger trend
    assert trend_pullback_setup_quality(**{**base, "dist_to_trigger": 0.1}) > q  # closer to the trigger EMA
    assert trend_pullback_setup_quality(**{**base, "rsi_now": 50.0}) > q  # bigger RSI recovery
    assert (
        trend_pullback_setup_quality(
            daily_fast=100.0,
            daily_slow=100.0,
            dist_to_trigger=1.0,
            tolerance=1.0,
            rsi_now=35.0,
            rsi_extreme=35.0,
            recovery_span=15.0,
        )
        == 0.0
    )
    assert (
        trend_pullback_setup_quality(
            daily_fast=200.0,
            daily_slow=100.0,
            dist_to_trigger=0.0,
            tolerance=1.0,
            rsi_now=80.0,
            rsi_extreme=35.0,
            recovery_span=15.0,
        )
        == 1.0
    )


def test_squeeze_quality_is_monotone_in_squeeze_length_and_volume():
    q = squeeze_setup_quality(squeeze_bars=8, volume_ratio=1.8, volume_factor=1.3)
    assert 0.0 < q < 1.0
    assert squeeze_setup_quality(squeeze_bars=16, volume_ratio=1.8, volume_factor=1.3) > q
    assert squeeze_setup_quality(squeeze_bars=8, volume_ratio=2.5, volume_factor=1.3) > q
    assert squeeze_setup_quality(squeeze_bars=40, volume_ratio=5.0, volume_factor=1.3) == 1.0


def test_formulaic_candidates_map_z_to_quality():
    from agentic_trader.screeners.formulaic import alpha_setup_quality

    assert alpha_setup_quality(0.0) == 0.0
    assert alpha_setup_quality(1.5) == 0.5
    assert alpha_setup_quality(-3.0) == 1.0
    assert alpha_setup_quality(9.0) == 1.0
```

Add an integration assertion to the existing trend-pullback and squeeze strategy tests (`grep -rln "TrendPullbackStrategy\|SqueezeBreakoutStrategy" tests/screeners`): for each candidate a triggering fixture produces, `0.0 < candidate.setup_quality <= 1.0`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/screeners/test_setup_quality.py -q` — expected `ImportError: cannot import name 'clamp01'`.

- [ ] **Step 3: Implement**

`agentic_trader/screeners/base.py`:

```python
def clamp01(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(1.0, float(value)))
```

and on `ScreenerCandidate`: `setup_quality: float = Field(default=0.0, ge=0.0, le=1.0)`.

`agentic_trader/screeners/strategies.py` — module-level helpers:

```python
def trend_pullback_setup_quality(
    *,
    daily_fast: float,
    daily_slow: float,
    dist_to_trigger: float,
    tolerance: float,
    rsi_now: float,
    rsi_extreme: float,
    recovery_span: float,
) -> float:
    """Transparent prioritisation heuristic in [0, 1]; not validated alpha."""
    trend = clamp01(abs(daily_fast - daily_slow) / max(abs(daily_slow), 1e-9) / 0.10)
    proximity = clamp01(1.0 - dist_to_trigger / max(tolerance, 1e-9))
    recovery = clamp01(abs(rsi_now - rsi_extreme) / max(recovery_span, 1e-9))
    return round((trend + proximity + recovery) / 3.0, 4)


def squeeze_setup_quality(*, squeeze_bars: int, volume_ratio: float, volume_factor: float) -> float:
    length = clamp01(squeeze_bars / 20.0)
    expansion = clamp01((volume_ratio - volume_factor) / 1.7)
    return round((length + expansion) / 2.0, 4)
```

In the trend-pullback long branch pass `setup_quality=trend_pullback_setup_quality(daily_fast=daily_fast, daily_slow=daily_slow, dist_to_trigger=dist_to_trigger, tolerance=cfg.trigger_atr_distance_mult * atr_4h, rsi_now=rsi_current, rsi_extreme=min_recent_rsi, recovery_span=15.0)`; in the short branch use `rsi_extreme=max_recent_rsi`. In `evaluate_timeframe` (squeeze), both branches: `setup_quality=squeeze_setup_quality(squeeze_bars=prior_squeeze_count, volume_ratio=volume / volume_sma, volume_factor=self.cfg.volume_factor)` — read the method to use its actual local names for the squeeze count and volume ratio.

`agentic_trader/screeners/formulaic.py`:

```python
def alpha_setup_quality(z: float) -> float:
    return clamp01(abs(z) / 3.0)
```

and pass `setup_quality=alpha_setup_quality(latest_z)` in the candidate.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/screeners -q` — expected pass.

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/screeners tests/screeners
git commit -m "feat(screeners): rank candidates with a transparent setup-quality heuristic

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Collect, rank, send — the suggestion budget

**Files:**
- Modify: `agentic_trader/storage/db.py` (after `is_duplicate_recent`)
- Modify: `agentic_trader/agent/copilot.py` (`run_scan` candidate loop and tail; `run_scan_summary_html`)
- Modify: `agentic_trader/cli/commands/scan.py`
- Test: `tests/agent/test_scan_budget.py`, additions to `tests/storage/test_db.py` and `tests/cli/test_cli_smoke.py` (or the module that tests `scan`)

**Interfaces:**
- Consumes: `ScanBudget`, `ScanConfig`, `setup_quality`.
- Produces:
  - `SignalDatabase.signals_since(cutoff: datetime) -> list[dict]` (each `{"contract", "strategy", "timestamp"}`, scoped, non-quarantined).
  - `run_scan(..., budget: ScanBudget = ScanBudget.SESSION)`; scheduled callers pass `ScanBudget.FULL`.
  - `TradingCopilot.session_start_et(now: datetime | None = None) -> datetime`
  - `TradingCopilot.correlation_groups_of(symbol: str) -> set[str]`
  - `decision_provenance` gains `setup_quality`, `rank`, `candidates_considered`, `budget`.
  - `last_scan_summary["runners_up"]`: `[{"contract", "strategy", "direction", "setup_quality", "reason"}]`.
  - `copilot scan --no-budget` → `ScanBudget.NONE`; dry runs always `NONE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/storage/test_db.py`:

```python
async def test_signals_since_is_scoped_and_ignores_quarantined_rows(temp_db):
    from datetime import UTC, datetime, timedelta

    await temp_db.init_db()
    sid = await temp_db.record_signal("SPY", "s", "LONG", 100, 98, 104, 2, asset_class="EQUITY", quantity=1)
    await temp_db.record_signal("QQQ", "s", "LONG", 100, 98, 104, 2, asset_class="EQUITY", quantity=1)
    rows = await temp_db.signals_since(datetime.now(UTC) - timedelta(minutes=5))
    assert {r["contract"] for r in rows} == {"SPY", "QQQ"}
    await temp_db.quarantine_signal(sid, reason="test")  # read the real signature first
    rows = await temp_db.signals_since(datetime.now(UTC) - timedelta(minutes=5))
    assert {r["contract"] for r in rows} == {"QQQ"}
    assert await temp_db.signals_since(datetime.now(UTC) + timedelta(minutes=1)) == []
```

Create `tests/agent/test_scan_budget.py`:

```python
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from agentic_trader.config import ScanBudget
from agentic_trader.constants import AssetClass
from agentic_trader.screeners.base import ScreenerCandidate


def frame(rows=30):
    return pd.DataFrame({"Close": [100.0] * rows, "Volume": [1000] * rows})


def instrument(symbol):
    return SimpleNamespace(name=symbol, ticker=symbol, asset_class="EQUITY")


def candidate(symbol, quality, strategy="TREND_PULLBACK", direction="LONG"):
    return ScreenerCandidate(
        contract=symbol,
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy=strategy,
        direction=direction,
        current_price=100.0,
        ema_20=1.0,
        ema_50=1.0,
        ema_200=1.0,
        rsi_14=50.0,
        atr_14=1.0,
        candle_timestamp="2026-09-22T00:00:00+00:00",
        recent_swing_low=95.0,
        recent_swing_high=105.0,
        trigger_detail="t",
        setup_quality=quality,
    )


def evaluation(candidate_, approved=True):
    return SimpleNamespace(
        approved=approved,
        rejection_reason=None if approved else "risk",
        contract=candidate_.contract,
        direction=candidate_.direction,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        risk_dollars=2.0,
        reward_dollars=4.0,
        notional_value=100.0,
        asset_class=AssetClass.EQUITY,
        quantity=1.0,
        model_dump_json=lambda: "{}",
        model_dump=lambda mode=None: {},
    )


@pytest.fixture
def budget_desk(scan_desk, app_config):
    app_config.contracts = {s: instrument(s) for s in ("AAA", "BBB", "CCC", "DDD")}
    app_config.portfolio.correlation_groups = {"sector_x": ["AAA", "BBB"], "sector_y": ["CCC"]}
    scan_desk.data_fetcher.fetch_data.side_effect = lambda contract, ticker, include_fifteen_min=True: SimpleNamespace(
        contract=contract, daily=frame(), four_hour=frame(), hourly=frame()
    )
    qualities = {"AAA": 0.9, "BBB": 0.8, "CCC": 0.7, "DDD": 0.6}
    scan_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, qualities[data.contract])
    ]

    async def evaluate(cand, **kwargs):
        return evaluation(cand)

    scan_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    scan_desk.db.is_duplicate_recent = AsyncMock(return_value=False)
    return scan_desk


async def test_full_budget_sends_only_the_top_card_and_calls_the_llm_only_for_it(budget_desk, temp_db):
    await budget_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)
    sent = await temp_db.get_recent_signals(limit=10)
    assert [s["contract"] for s in sent] == ["AAA"]
    calls = budget_desk.evaluator.evaluate_candidate.call_args_list
    assert sum(1 for c in calls if c.kwargs.get("use_llm")) == 1  # deterministic pass for all, LLM for the winner only
    provenance = sent[0]["decision_provenance"]
    assert provenance["setup_quality"] == 0.9 and provenance["rank"] == 1 and provenance["candidates_considered"] == 4
    assert [r["contract"] for r in budget_desk.last_scan_summary["runners_up"]] == ["BBB", "CCC", "DDD"]


async def test_session_budget_is_derived_from_recorded_signals_and_survives_a_restart(budget_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 5
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    assert len(await temp_db.get_recent_signals(limit=10)) == 2  # max_cards_per_session
    budget_desk.last_scan_summary = {}  # "restart": nothing in memory
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    assert len(await temp_db.get_recent_signals(limit=10)) == 2


async def test_one_card_per_correlation_group_per_session(budget_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 5
    app_config.scan.max_cards_per_session = 5
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    sent = [s["contract"] for s in await temp_db.get_recent_signals(limit=10)]
    assert set(sent) == {"AAA", "CCC", "DDD"}  # BBB shares sector_x with AAA
    assert any(r["contract"] == "BBB" and "group" in r["reason"] for r in budget_desk.last_scan_summary["runners_up"])


async def test_llm_rejection_falls_through_to_the_next_ranked_candidate(budget_desk, temp_db):
    async def evaluate(cand, use_llm=False, **kwargs):
        return evaluation(cand, approved=not (use_llm and cand.contract == "AAA"))

    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await budget_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)
    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["BBB"]


async def test_llm_evaluation_budget_bounds_the_fallthrough(budget_desk, temp_db, app_config):
    app_config.scan.max_llm_evaluations_per_scan = 2

    async def evaluate(cand, use_llm=False, **kwargs):
        return evaluation(cand, approved=not use_llm)

    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await budget_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)
    assert await temp_db.get_recent_signals(limit=10) == []
    assert sum(1 for c in budget_desk.evaluator.evaluate_candidate.call_args_list if c.kwargs.get("use_llm")) == 2


async def test_no_budget_records_every_approved_candidate(budget_desk, temp_db):
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.NONE)
    assert len(await temp_db.get_recent_signals(limit=10)) == 4


async def test_session_budget_ignores_the_per_scan_cap(budget_desk, temp_db):
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    assert len(await temp_db.get_recent_signals(limit=10)) == 2


def test_session_start_is_new_york_midnight(scan_desk):
    from zoneinfo import ZoneInfo

    now = datetime(2026, 9, 22, 18, 30, tzinfo=UTC)  # 14:30 New York
    start = scan_desk.session_start_et(now)
    assert start == datetime(2026, 9, 22, 0, 0, tzinfo=ZoneInfo("America/New_York"))
```

`get_recent_signals` returns dicts with `decision_provenance` already JSON-decoded (`SignalRecord.to_dict`, `storage/models.py:~96`); confirm and adapt. `temp_db.quarantine_signal`'s real signature: `grep -n "async def quarantine_signal" -A6 agentic_trader/storage/db.py`. Extend the `scan` CLI test module: `copilot scan --no-budget --dry-run` passes `budget=ScanBudget.NONE` and a plain `copilot scan` passes `ScanBudget.SESSION` (patch `run_scan` with an `AsyncMock` and inspect kwargs, following that module's existing pattern for patching `get_copilot_and_config`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agent/test_scan_budget.py tests/storage/test_db.py -q` — expected `TypeError: run_scan() got an unexpected keyword argument 'budget'` and `AttributeError: signals_since`.

- [ ] **Step 3: Implement the storage helper**

`agentic_trader/storage/db.py`, after `is_duplicate_recent`:

```python
    async def signals_since(self, cutoff: datetime) -> list[dict[str, Any]]:
        """Signals recorded at or after ``cutoff`` in this scope; the per-session card budget is derived from them."""
        async with self.session_factory() as session:
            rows = await session.execute(
                select(SignalRecord.contract, SignalRecord.strategy, SignalRecord.timestamp)
                .where(*self._scope())
                .where(SignalRecord.timestamp >= cutoff)
                .order_by(SignalRecord.timestamp)
            )
            return [{"contract": c, "strategy": s, "timestamp": t} for c, s, t in rows]
```

- [ ] **Step 4: Restructure the candidate loop**

In `agentic_trader/agent/copilot.py` add `budget: ScanBudget = ScanBudget.SESSION` to `run_scan`; at the top of the body: `if dry_run: budget = ScanBudget.NONE`. Add helpers:

```python
def session_start_et(self, now: datetime | None = None) -> datetime:
    current = (now or datetime.now(UTC)).astimezone(ET_TZ)
    return datetime.combine(current.date(), time(0, 0), ET_TZ)


def correlation_groups_of(self, symbol: str) -> set[str]:
    keys = {symbol.upper(), symbol.strip("/").upper()}
    groups = set()
    for name, members in self.config.portfolio.correlation_groups.items():
        norm = {m.strip("/").upper() for m in members} | {m.upper() for m in members}
        if keys & norm:
            groups.add(name)
    return groups
```

Import `ET_TZ` from `agentic_trader.market.session` and `time` from `datetime`. Replace the body of the per-candidate loop (from the dedup check through the exposure bookkeeping) with a COLLECT phase that appends `(candidate, account_risk)` to `collected` after dedup and a deterministic evaluation `det = await self.evaluator.evaluate_candidate(candidate, current_open_notional=current_exposure, use_llm=False, active_positions=active_positions, current_drawdown_pct=..., current_equity=...)`; on `det.approved` append `(candidate, det, account_risk)` to `approved`, else log `candidate_rejected` as today. Keep `summary["candidates"] += 1` per candidate. After the sequential loop, add the RANK-AND-SEND phase:

```python
ranked = sorted(approved, key=lambda item: (-item[0].setup_quality, item[0].contract, item[0].strategy))
summary["approved"] = len(ranked)
cfg = self.config.scan
if budget == ScanBudget.NONE:
    remaining_scan = remaining_session = len(ranked)
    groups_used: dict[str, int] = {}
else:
    today = await self.db.signals_since(self.session_start_et())
    remaining_session = max(0, cfg.max_cards_per_session - len(today))
    remaining_scan = cfg.max_cards_per_scan if budget == ScanBudget.FULL else len(ranked)
    groups_used = {}
    for row in today:
        for group in self.correlation_groups_of(row["contract"]):
            groups_used[group] = groups_used.get(group, 0) + 1
llm_budget = cfg.max_llm_evaluations_per_scan
for rank, (candidate, _det, account_risk) in enumerate(ranked, 1):
    reason = None
    if remaining_scan <= 0:
        reason = "per-scan budget spent"
    elif remaining_session <= 0:
        reason = "per-session budget spent"
    elif budget != ScanBudget.NONE and any(
        groups_used.get(g, 0) >= cfg.max_cards_per_group_per_session
        for g in self.correlation_groups_of(candidate.contract)
    ):
        reason = "correlation group already has a card this session"
    elif llm_budget <= 0:
        reason = "LLM evaluation budget spent"
    if reason:
        summary["runners_up"].append(self._runner_up(candidate, reason))
        continue
    llm_budget -= 1
    eval_res = await self.evaluator.evaluate_candidate(
        candidate,
        current_open_notional=current_exposure,
        use_llm=use_llm,
        active_positions=active_positions,
        current_drawdown_pct=float(account_risk.drawdown_pct) if account_risk else 0.0,
        current_equity=float(account_risk.equity) if account_risk else None,
    )
    if not eval_res.approved:
        summary["runners_up"].append(self._runner_up(candidate, f"rejected: {eval_res.rejection_reason}"))
        logger.info(
            "Candidate rejected: %s",
            eval_res.rejection_reason,
            extra={
                "event": "candidate_rejected",
                "contract": candidate.contract,
                "rejection_reason": eval_res.rejection_reason,
            },
        )
        continue
    if dry_run:
        print(
            format_terminal_card(
                eval_res, candidate.strategy, self.config.portfolio.cash, regime_summary=regime.summary_text
            )
        )
        continue
    sig_id = await self.db.record_signal(
        ...  # exactly today's arguments, plus in decision_provenance:
        #   "setup_quality": candidate.setup_quality, "rank": rank,
        #   "candidates_considered": len(ranked), "budget": str(budget),
    )
    ...  # today's signal_approved log
    summary["sent"] += 1
    remaining_scan -= 1
    remaining_session -= 1
    for group in self.correlation_groups_of(candidate.contract):
        groups_used[group] = groups_used.get(group, 0) + 1
    current_exposure += eval_res.notional_value
    active_positions.append({...})  # exactly today's dict
```

with

```python
@staticmethod
def _runner_up(candidate, reason: str) -> dict[str, Any]:
    return {
        "contract": candidate.contract,
        "strategy": candidate.strategy,
        "direction": candidate.direction,
        "setup_quality": candidate.setup_quality,
        "reason": reason,
    }
```

`self._session_scan_stats.setdefault(self.session_start_et().date().isoformat(), []).append(summary)` at the end (initialise the dict in `__init__`; Task 6 reads it). Keep the `dry_run` prints and every log event name unchanged.

`agentic_trader/cli/commands/scan.py`: add `@click.option("--no-budget", is_flag=True, help="Record every approved candidate; ignore the per-scan and per-session suggestion budget")` and `scan_kwargs["budget"] = ScanBudget.NONE if no_budget else ScanBudget.SESSION`. `run_scan_summary_html` (Telegram `/scan`) keeps its call unchanged (default `SESSION`).

- [ ] **Step 5: Run**

Run: `uv run pytest tests/agent tests/storage tests/cli -q` — expected pass, including `test_probe_scan_integration.py::test_probe_signal_provenance_and_notification_risk_cap` unmodified (its single candidate is rank 1 under the default budget).

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/storage/db.py agentic_trader/agent/copilot.py agentic_trader/cli/commands/scan.py tests/agent/test_scan_budget.py tests/storage/test_db.py tests/cli
git commit -m "feat(scan): rank approved candidates and spend a per-scan, per-session, per-group card budget

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Session-aligned scans, intraday scope and the digest

**Files:**
- Modify: `agentic_trader/cli/commands/service.py` (job registration ~352-400)
- Modify: `agentic_trader/agent/copilot.py` (`publish_scan_digest`)
- Test: `tests/cli/test_service_scheduling.py`, addition to `tests/agent/test_scan_budget.py`

**Interfaces:**
- Produces: `service.register_suggestion_scans(scheduler, copilot, config, *, use_llm)`; `service.register_intraday_scan(...)` passes `symbols=config.non_universe_contracts`; `TradingCopilot.publish_scan_digest(et_date: str | None = None) -> str` (returns the text; publishes via `self.outbox.publish_message(text, key=f"scan-digest/{et_date}")`).

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_service_scheduling.py`:

```python
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from agentic_trader.cli.commands import service
from agentic_trader.config import ScanBudget


def test_suggestion_scans_are_cron_jobs_in_new_york_on_weekdays(config):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_suggestion_scans(scheduler, copilot, config, use_llm=True)
    calls = scheduler.add_job.call_args_list
    assert len(calls) == 2
    for call, (hour, minute) in zip(calls, [(10, 35), (14, 35)], strict=True):
        assert call.args[1] == "cron"
        assert call.kwargs["hour"] == hour and call.kwargs["minute"] == minute
        assert call.kwargs["day_of_week"] == "mon-fri"
        assert call.kwargs["timezone"] == ZoneInfo("America/New_York")
    assert calls[0].kwargs["kwargs"] == {"digest": False} and calls[1].kwargs["kwargs"] == {"digest": True}


async def test_suggestion_scan_runs_full_budget_only_when_the_equity_session_is_open(config):
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.publish_scan_digest = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(False, "holiday"))
    job = service.make_suggestion_scan(copilot, use_llm=True)
    await job(digest=True)
    copilot.run_scan.assert_not_awaited()
    copilot.publish_scan_digest.assert_awaited_once()  # a quiet day still reports
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    await job(digest=False)
    copilot.run_scan.assert_awaited_once_with(use_llm=True, dry_run=False, asset_class="equity", budget=ScanBudget.FULL)


def test_intraday_scan_is_restricted_to_non_universe_contracts(config):
    from agentic_trader.config import UniverseConfig, UniverseEntry

    config.universe = UniverseConfig(groups={"g": [UniverseEntry(symbol="AAPL"), UniverseEntry(symbol="MSFT")]})
    config.contracts = {"SPY": MagicMock(), "AAPL": MagicMock(), "MSFT": MagicMock(), "/MES": MagicMock()}
    config.explicit_contracts = ("SPY", "/MES")
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_intraday_scan(scheduler, copilot, config, use_llm=True)
    job = scheduler.add_job.call_args.args[0]  # a functools.partial; its keywords are inspectable
    assert job.keywords["symbols"] == ["/MES", "SPY"]
```

`register_intraday_scan` registers a `functools.partial(run_intraday_scan, symbols=[...])`, so the test inspects `job.keywords`. Append to `tests/agent/test_scan_budget.py`:

```python
async def test_digest_summarises_the_session_and_is_published_once(budget_desk, app_config):
    budget_desk.outbox = AsyncMock()
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    text = await budget_desk.publish_scan_digest()
    assert "1 card" in text and "3 runners-up" in text and "BBB" in text
    budget_desk.outbox.publish_message.assert_awaited_once()
    assert budget_desk.outbox.publish_message.call_args.kwargs["key"].startswith("scan-digest/")


async def test_digest_without_scans_says_so(scan_desk):
    scan_desk.outbox = AsyncMock()
    text = await scan_desk.publish_scan_digest()
    assert "no suggestion scans" in text.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_service_scheduling.py tests/agent/test_scan_budget.py -q` — expected `AttributeError: module ... has no attribute 'register_suggestion_scans'` and `publish_scan_digest`.

- [ ] **Step 3: Implement the scheduler functions**

In `agentic_trader/cli/commands/service.py`:

```python
def make_suggestion_scan(copilot, *, use_llm: bool):
    async def run_suggestion_scan(digest: bool = False) -> None:
        active, reason = await copilot.session_provider.is_session_active(instrument_type="equity")
        if active:
            await copilot.run_scan(use_llm=use_llm, dry_run=False, asset_class="equity", budget=ScanBudget.FULL)
        else:
            logger.info(
                "Suggestion scan skipped: %s", reason, extra={"event": "suggestion_scan_skipped", "reason": reason}
            )
        if digest:
            await copilot.publish_scan_digest()

    return run_suggestion_scan


def register_suggestion_scans(scheduler, copilot, config, *, use_llm: bool) -> None:
    job = make_suggestion_scan(copilot, use_llm=use_llm)
    times = config.scheduler.suggestion_scan_times_et
    for index, item in enumerate(times):
        hour, minute = (int(part) for part in item.split(":"))
        scheduler.add_job(
            job,
            "cron",
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            timezone=ET_TZ,
            id=f"suggestion_scan_{index}",
            kwargs={"digest": index == len(times) - 1},
        )
    logger.info("Scheduled suggestion scans at %s New York on weekdays.", ", ".join(times))


def register_intraday_scan(scheduler, copilot, config, *, use_llm: bool) -> None:
    async def run_intraday_scan(*, symbols: list[str]) -> None:
        session_active, reason = await copilot.session_provider.is_session_active(instrument_type="all")
        if session_active:
            await copilot.run_scan(
                use_llm=use_llm,
                dry_run=False,
                asset_class="all",
                timeframe="15m",
                symbols=symbols,
                budget=ScanBudget.FULL,
            )
        else:
            logger.debug("Intraday scan skipped outside market session: %s", reason)

    scheduler.add_job(
        functools.partial(run_intraday_scan, symbols=config.non_universe_contracts),
        "interval",
        minutes=config.scheduler.intraday_interval_minutes,
        id="intraday_scan",
        next_run_time=datetime.now(UTC),
    )
```

Replace the inline intraday block in the daemon with `register_intraday_scan(...)` (keep the `if config.scheduler.intraday_scan_enabled:` guard and its log line) and call `register_suggestion_scans(scheduler, copilot, config, use_llm=not no_llm)` next to it. The existing interval `swing_scan` job stays but passes `budget=ScanBudget.FULL` via `kwargs`. Import `functools`, `ET_TZ`, `ScanBudget`.

- [ ] **Step 4: Implement the digest**

In `agentic_trader/agent/copilot.py`:

```python
    async def publish_scan_digest(self, et_date: str | None = None) -> str:
        et_date = et_date or self.session_start_et().date().isoformat()
        scans = self._session_scan_stats.get(et_date, [])
        if not scans:
            text = f"📋 Scan digest {et_date}: no suggestion scans ran this session."
        else:
            candidates = sum(s.get("candidates", 0) for s in scans)
            sent = sum(s.get("sent", 0) for s in scans)
            runners = [r for s in scans for r in s.get("runners_up", [])]
            failed = sorted({c for s in scans for c in s.get("fetch_failed", [])})
            excluded = sorted({c for s in scans for c in s.get("coverage_excluded", [])})
            durations = ", ".join(f"{s.get('duration_seconds', 0):.0f}s" for s in scans)
            top = ", ".join(f"{r['contract']} {r['direction']} ({r['setup_quality']:.2f})" for r in runners[:5])
            text = (
                f"📋 Scan digest {et_date}: {len(scans)} scan(s), {candidates} candidates, {sent} card(s) sent, "
                f"{len(runners)} runners-up" + (f" — top: {top}" if top else "") + ". "
                f"Fetch failures: {len(failed)}" + (f" ({', '.join(failed[:8])})" if failed else "") + "; "
                f"coverage excluded: {len(excluded)}; scan durations: {durations}."
            )
        await self.outbox.publish_message(text, key=f"scan-digest/{et_date}")
        return text
```

`publish_message` marks the text as formatted HTML; symbols match `^[A-Z][A-Z.]{0,5}$` and reasons are our own literals, so no escaping is needed — say so in a comment.

- [ ] **Step 5: Run and commit**

Run: `uv run pytest tests/cli tests/agent -q` — expected pass, including `tests/agent/test_async_responsiveness.py` unmodified (it patches `get_copilot_and_config`; if it asserts the exact list of scheduled job ids, extend that assertion in the same commit and say so).

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/cli/commands/service.py agentic_trader/agent/copilot.py tests/cli/test_service_scheduling.py tests/agent/test_scan_budget.py
git commit -m "feat(daemon): schedule session-aligned suggestion scans, scope the intraday job, publish a digest

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Documentation, spec amendment and the full gate

**Files:**
- Modify: `docs/superpowers/specs/2026-09-21-scan-universe-and-suggestion-budget-design.md`, `docs/production.md`, `docs/cli-reference.md`, `docs/alpha-roadmap.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Amend the spec**

In §2 replace "The 15-minute intraday job keeps its current, explicitly configured symbol scope and is not widened" with: "The 15-minute intraday job has no symbol scope of its own today; it scans every configured contract. It is therefore restricted to the contracts configured explicitly under `contracts:` (`AppConfig.non_universe_contracts`), so widening the universe does not multiply its request rate." Add to §2: "The interval swing scan continues to cover the whole universe whenever it lands inside the equity session; the budget governs what it may send." Add to §1: "Sectors are authored once from yfinance metadata and reviewed by eye; a name whose sector is unknown joins no correlation group and is counted in the digest." Add a "Decisions" line: `--no-budget` records every approved candidate; dry runs always run without a budget.

- [ ] **Step 2: Update the living docs**

- `docs/production.md`: a "Suggestion scans" subsection — the two New York cron times, what the digest reports, the `scan:` keys and their defaults, the paper caps (8 positions, $7,500 per trade), the rollback (`universe: {}`), and the first-two-sessions watch list (scan duration, fetch failures, coverage exclusions). State that a restart is required for config changes to take effect.
- `docs/cli-reference.md`: `copilot scan --no-budget`; budget semantics for `copilot scan` (session limit only) and `/scan` (same).
- `docs/alpha-roadmap.md`: mark the scan-universe workstream implemented (September 22), restate the north-star metric (suggestions per session) and that `setup_quality` is a prioritisation heuristic to be evaluated against realised R, not validated alpha; list follow-ups: session-bounded entries for built-in strategies; universe-wide mined alphas over the same names; evaluating `setup_quality`; fixing the interval scan's drift from candle closes.
- `CLAUDE.md` and `AGENTS.md`: one paragraph in the operations contracts: "Suggestion scans run at configured New York times and spend a derived per-session card budget (signals table, ET trading day); `PENDING` cards reserve no capacity; the intraday job scans only `non_universe_contracts`; `setup_quality` orders cards and is stored in provenance — it is not validated alpha. Never widen the intraday job to the universe or store the budget in a counter."

- [ ] **Step 3: Full verification**

```bash
uv run pytest
uv run mypy agentic_trader
uv run pre-commit run --all-files
COPILOT_ENV_FILE="" uv run python -c "from agentic_trader.config import load_config; c=load_config(); print(len(c.contracts), len(c.universe.symbols), len(c.non_universe_contracts), c.portfolio.max_concurrent_positions, c.sizing.max_trade_notional_cap, c.scheduler.suggestion_scan_times_et)"
```

Expected: full suite green; mypy clean; pre-commit clean; the config line prints about 170, about 160, 13, 8, 7500.0, `['10:35', '14:35']`.

- [ ] **Step 4: Commit**

```bash
git add docs CLAUDE.md AGENTS.md
git commit -m "docs: record the scan universe, suggestion budget and operator contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## After this plan (operator-approved, not part of these tasks)

Deploy with the documented controlled restart. Then watch the first two sessions' digests: scan duration (expect two to three minutes), fetch failures (expect a handful of thin cohort names), coverage exclusions, and cards sent. Only then consider changing `max_cards_per_session` or the universe.
