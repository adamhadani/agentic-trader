# Scan Bars on Delayed SIP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live scan bars and every consumer in the same units use SIP with the request end clamped 16 minutes
back. Broker quotes stay on real-time IEX.

**Architecture:** Two pieces carry the change:
- one effective-feed definition on `MarketDataConfig` (`bars_feed`, `bars_end_delay`);
- one end clamp in `AlpacaDataProvider` (`end_delay`).

Each scan-path consumer switches from `market_data.alpaca_feed` to that definition.

**Tech Stack:** Python 3.14, pydantic config, alpaca-py, pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-scan-sip-bars-design.md`

## Global Constraints

- **Rollout:** code default `market_data.scan_bars: configured`, which keeps today's behaviour;
  `config/config.yaml` ships `sip_delayed`. `sip_delay_minutes` defaults to 16 and accepts 16–60.
- **Must not change:**
  - broker latest trade and quote (`alpaca_data_feed`);
  - `AlpacaDataProvider.fetch_latest_price`;
  - research CLIs in `cli/commands/alpha.py`;
  - the `alpha_pipeline` observation and decision feeds;
  - `build_daily_bar_source`'s explicit `feed="sip"` with `DelayedBars`.
- **The clamp:** it only ever moves an end earlier. An explicit earlier end is kept, and a missing end
  becomes `now − delay`.
- **Housekeeping:** no migration. Tests offline with injected fakes and clocks. Ruff C408/PLC0415 strict and
  mypy. Implementers stage only; the controller commits.

## Review Focus

1. **Dynamic universe units.** The static reference and the dynamic candidates' dollar volume must come from
   the same feed, or every dynamic name is excluded. A cached IEX reference must be ignored, not reused.
2. **A plain `ALPACA_DATA_FEED=iex` env** with `scan_bars: sip_delayed` must still give SIP bars and IEX quotes.
3. **No SIP request with a recent end anywhere** in the scan path: fetch_bars, fetch_daily_many and the doctor
   probe.
4. **`configured`** must reproduce today's behaviour exactly: no clamp, feed `alpaca_feed`.

---

### Task 1: Effective bar feed, end clamp and consumers

**Files:**
- Modify:
  - `agentic_trader/config.py`: `MarketDataConfig.scan_bars`, `sip_delay_minutes`, and the `bars_feed` and
    `bars_end_delay` properties.
  - `agentic_trader/data/providers.py`: the `AlpacaDataProvider(end_delay=…)` clamp in `fetch_bars` and
    `fetch_daily_many`.
  - `agentic_trader/data/market_data.py`: the fetcher uses `bars_feed` and `bars_end_delay`.
  - `agentic_trader/research/setups/sources.py`: `build_bar_source` defaults to `bars_feed` and
    `bars_end_delay`; an explicit `feed=` gets no delay unless asked.
  - `agentic_trader/agent/copilot.py`: the two dynamic-universe feed reads (about :1350 and :4559) use
    `bars_feed`.
  - `agentic_trader/research/setups/card_stats_worker.py` (about :148): `bars_feed`.
  - `agentic_trader/diagnostics/doctor.py` (about :192–205): probe `bars_feed`, end `now − delay` under
    `sip_delayed`, with a message naming delayed SIP.
  - `config/config.yaml`: `market_data.scan_bars: sip_delayed` and `sip_delay_minutes: 16`, with a dated
    comment.
  - Docs:
    - `docs/production.md`: the market-data section.
    - `CLAUDE.md` contract 8: one sentence at a sentence boundary.
    - `docs/alpha-roadmap.md`: one line under the pre-event program or the current priorities, pointing to
      `docs/native-supply-2026-11-07.md`.
- Test: `tests/data/` provider tests, `tests/config/`, the doctor tests, the copilot dynamic-universe tests and
  the shipped-config test.

**Interfaces:**
- Produces:
  - `MarketDataConfig.scan_bars: Literal["configured", "sip_delayed"] = "configured"`
  - `sip_delay_minutes: int = Field(16, ge=16, le=60)`
  - `@property bars_feed -> str`
  - `@property bars_end_delay -> timedelta | None`
  - `AlpacaDataProvider.__init__(…, end_delay: timedelta | None = None)`
  - `build_bar_source(config, *, feed=None)`, which applies `bars_end_delay` only when `feed is None`.

- [ ] **Step 1: Write failing tests.**
  - Config:
    - defaults reproduce today's behaviour;
    - `sip_delayed` gives `bars_feed == "sip"` and `bars_end_delay == timedelta(minutes=16)`;
    - bounds are enforced;
    - `load_config` with env `ALPACA_DATA_FEED=iex` and YAML `scan_bars: sip_delayed` gives
      `bars_feed == "sip"` while `alpaca_data_feed == "iex"`.
  - Provider, with a fake stock client capturing `StockBarsRequest`:
    - `end_delay=16 min` with `end=None` sends `end == now − 16 min`;
    - an explicit later end is clamped;
    - an explicit earlier end is kept;
    - `fetch_daily_many` is clamped the same way;
    - no `end_delay` leaves `end` untouched;
    - `fetch_latest_price` is unaffected.
  - Fetcher and source:
    - `MarketDataFetcher(config=sip_delayed config)` builds an Alpaca provider with feed `sip` and the delay;
    - `build_bar_source(config)` does the same;
    - `build_bar_source(config, feed="sip")` gets no delay;
    - under `configured`, both match today.
  - Copilot: the dynamic block's `feed` and the reference-feed match use `bars_feed`. Under `sip_delayed`, a
    cached reference with `feed: "iex"` is ignored.
  - Doctor: under `sip_delayed`, the probe request has feed SIP and an end of `now − 16 min`; under
    `configured`, it is unchanged.
  - Shipped config: `scan_bars == "sip_delayed"` and `sip_delay_minutes == 16`.
- [ ] **Step 2:** Run the new tests. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the new tests, the affected suites, the full unit suite and
  `uv run pre-commit run --files <changed>`. Expected: PASS.
- [ ] **Step 5:** Stage only.
