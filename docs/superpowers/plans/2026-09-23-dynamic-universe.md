# Dynamic Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Scheduled suggestion scans add up to 20 filtered, liquid, in-play US equities from Alpaca's screener.

**Architecture:**
- A new `agentic_trader/screeners/dynamic_universe.py` holds:
  - the pure filter and liquidity gate;
  - a small `DynamicUniverseSource` adapter, which fetches screener lists and a per-NY-date cached asset list.
- The copilot adds dynamic contracts only on `shadow_evidence=True` scans (the scheduled suggestion scans), after which all existing gates apply.

**Spec:** `docs/superpowers/specs/2026-09-23-dynamic-universe-design.md`

## Global Constraints

- Worktree `/Users/adamhadani/Development/agentic-trader-dynamic` (branch `feat/dynamic-universe`). Never touch `/Users/adamhadani/Development/agentic-trader`.
- Run everything as `env -u VIRTUAL_ENV uv run …`. Tests do no real network I/O.
- No migration. `config.contracts` is never mutated. The intraday job and manual scans are unchanged. `universe.dynamic.enabled: false` gives the legacy behaviour.
- Use TDD with a real RED run recorded.
- Before reporting, run `ruff check`, `ruff format --check`, the full `pytest -q` and `pre-commit run --all-files`, all in the foreground with a large timeout.
- Stage only your own files. Do NOT commit. No subagents.

### Task 1: Dynamic universe module + config

**Files:**
- Create: `agentic_trader/screeners/dynamic_universe.py`
- Modify: `agentic_trader/config.py`. Add `DynamicUniverseConfig` with the spec's fields and defaults, as `UniverseConfig.dynamic`.
- Modify: `config/config.yaml`
- Test: `tests/screeners/test_dynamic_universe.py`

**Produces:**
```python
@dataclass(frozen=True)
class ScreenerEntry: symbol: str; source: str; rank: int; price: float | None; percent_change: float | None
@dataclass(frozen=True)
class AssetInfo: symbol: str; name: str; asset_class: str; exchange: str; status: str; tradable: bool
@dataclass(frozen=True)
class DynamicSelection:
    members: tuple[ScreenerEntry, ...]          # ordered, <= max_candidates
    reasons: dict[str, int]                       # filter reason -> count
    raw_counts: dict[str, int]                    # source -> entries received
def select_dynamic(entries: Sequence[ScreenerEntry], assets: Mapping[str, AssetInfo],
                   static_symbols: Collection[str], cfg: DynamicUniverseConfig) -> DynamicSelection
def liquidity_gate(daily_by_symbol: Mapping[str, pd.DataFrame], members: Sequence[ScreenerEntry],
                   cfg: DynamicUniverseConfig) -> tuple[list[ScreenerEntry], dict[str, str]]   # (kept <= max_symbols in order, excluded symbol -> reason)
def synthetic_contract(symbol: str, name: str) -> ContractConfig   # EQUITY, multiplier 1, tick 0.01, ticker=symbol
class DynamicUniverseSource:
    def __init__(self, config: AppConfig, *, screener_client=None, trading_client=None, clock=None)  # clients injected in tests; built from credentials otherwise (read-only)
    async def entries(self) -> list[ScreenerEntry]      # GETs via asyncio.to_thread; one list per configured source
    async def assets(self) -> dict[str, AssetInfo]     # get_all_assets(us_equity, active) once per NY date, cached
```

The order of filters and the reason keys come from the spec:
- `shape`, `static`, `asset_missing`, `asset_class`, `inactive`, `untradable`, `exchange`, `instrument` (warrant/rights/units by name), `leveraged`, `price`, `cap`;
- in the liquidity gate: `insufficient_bars`, `dollar_volume`, `cap`.

Movers are ordered by the absolute value of `percent_change` after the most-actives entries. A symbol seen in both sources keeps its first (most-actives) entry. The liquidity median uses the last 20 completed daily rows of `Close * Volume`; fewer than 20 rows gives `insufficient_bars`.

- [ ] **Step 1: Failing tests.**
  - Every filter reason, including warrant and unit shapes (`NIVFW`, `HVIIU`, `SLND.WS`) and leveraged names ("Direxion Daily Semiconductor Bear 3X Shares", "ProShares UltraPro QQQ").
  - A duplicate across sources, the ordering and the caps.
  - The liquidity boundary (exactly the minimum passes) and too few bars.
  - `synthetic_contract` fields.
  - Asset caching: a second call on the same NY date makes no new request, and a new date refetches.
  - Source failure: `entries()` raises, and the caller handles it in Task 2.
- [ ] **Step 2:** RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** GREEN.
- [ ] **Step 5:** `git add`.

### Task 2: Scan integration, audit event, digest, docs

**Files:**
- Modify: `agentic_trader/agent/copilot.py` (`run_scan`, `correlation_groups_of` or the RANK group-cap logic, provenance, digest)
- Modify: `agentic_trader/execution/durable.py`: `EventKind.DYNAMIC_UNIVERSE_BUILT = "dynamic_universe_built"`
- Modify: `docs/production.md` (suggestion scans: dynamic universe) and `docs/alpha-roadmap.md` (WS3 delivered)
- Test: `tests/agent/test_dynamic_universe_scan.py`, reusing the fixtures of `tests/agent/test_scan_budget.py` / `test_scan_shadow_ranker.py`

**Behaviour:**
- Construct `self.dynamic_universe = DynamicUniverseSource(config)` in `TradingCopilot.__init__`, with an injectable override for tests.
- In `run_scan`, when `shadow_evidence and cfg.universe.dynamic.enabled and not symbols and timeframe is None`, before `selected` is built:
  1. `entries = await self.dynamic_universe.entries()`;
  2. `assets = await self.dynamic_universe.assets()`;
  3. `selection = select_dynamic(entries, assets, static_symbols=self.config.contracts.keys(), cfg)`.

  On any exception, log `dynamic_universe_unavailable` once and continue static-only. Append each member as `(symbol, synthetic_contract(symbol, assets[symbol].name))` to `selected`, subject to the same asset-class, executability and session filters.
- After the fetch and the coverage gate, apply `liquidity_gate` to the dynamic members (using `datasets[...].daily`). Drop the excluded names from strategy scanning (like `coverage_excluded`) and record `summary["dynamic"] = {"members": [...kept], "excluded": {...}, "reasons": selection.reasons, "raw_counts": ...}`.
- **Correlation:** a dynamic contract belongs to group `"dynamic"` for the scan's group cap. Today's recorded signals whose provenance has `dynamic: true` count toward group `"dynamic"`. Implement this where `groups_used` is computed and checked, without changing static-name behaviour.
- **Provenance and journal:** recorded signals for dynamic names get `decision_provenance["dynamic"] = True` and `["dynamic_source"] = entry.source`. `scan_candidates_ranked` candidates get the same two keys, which are absent for static names.
- **Audit:** one `dynamic_universe_built` event per scan that attempted dynamic selection, including when it was unavailable (`{"available": false, "error": ...}`). Use the `_journal_scan_ranking` pattern (scope lock, own transaction, contained).
- **Digest:** add "N dynamic names scanned (M excluded)" when present.
- **Tests:**
  - dynamic names are scanned on a `shadow_evidence=True` scan and absent on manual, intraday, restricted and disabled scans;
  - a screener failure gives a static-only scan plus an unavailable event;
  - the liquidity gate excludes;
  - the dynamic group caps to one card per session;
  - provenance and journal carry the flags;
  - the digest line;
  - a dynamic card tapped through `execute_signal_by_id` with freshness and a real `EntryExecutionService` queues a bracket with quantity from multiplier 1;
  - `config.contracts` is unchanged after a scan.
- [ ] **Step 1:** RED.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** GREEN.
- [ ] **Step 4:** `git add`.

After Task 2, the controller runs a dry funnel check on real data, then the final review, PR, merge, deploy and verification.
