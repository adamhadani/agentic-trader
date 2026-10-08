# Honest cards (desk-direction item 1, PR A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every native suggestion card states its own measured record (base rate, mean R after cost, implied EV, sample size, the "not validated alpha" caveat) from a daily journaled `card_stats` snapshot, and one operator switch, `card_policy` (shipped `off`), can preview and then enforce withholding setups whose measured EV is below a threshold; an optional `next_session_close` card validity is available but off.

**Architecture:** A pure `compute_card_stats` turns the existing outcome labeller's frame into one snapshot payload; a daemon polling worker (`card_stats`) persists it once per New York date as an idempotent journal event. `run_scan` reads the newest snapshot once per scan through an injected `CardStatsRepository`, attaches a `CardEvidence` block to every native card (provenance and notification), and evaluates the pure `decide(policy, evidence)` before the LLM. Rendering, the policy and validity are independent layers over the same evidence; risk rules, admission, the FIFO, the broker and the LLM prompt are untouched.

**Tech Stack:** Python 3.14, `uv`, pydantic v2, pandas, SQLAlchemy async (SQLite in tests), APScheduler-free asyncio polling task, pytest (+ pytest-asyncio auto mode), python-telegram-bot formatting helpers.

**Spec:** `docs/superpowers/specs/2026-10-08-honest-cards-design.md` (code survey: `.superpowers/honest-cards-survey.md`, untracked)

Run every command from `/Users/adamhadani/Development/agentic-trader-cards` as `env -u VIRTUAL_ENV uv run …`.
Implementers stage and report; the controller runs each commit (operator memory: subagents stall on backgrounded commits).

## Global Constraints

- Python 3.14 and `uv`; ruff line length 120; `mypy agentic_trader` must stay clean (pre-commit runs it).
- No schema change and no migration: schema head stays `008_alpha_pipeline`. New journal kind only: `EventKind.CARD_STATS_SNAPSHOT = "card_stats_snapshot"` (the `kind` column is a plain string).
- No LLM prompt change (`agentic_trader/agent/prompts.py` untouched). The macro invariant is code after the LLM response, not prompt text.
- No change to strategies, screeners, `setup_quality`, the ranking key, risk rules (`agentic_trader/risk`), admission, the FIFO, the shadow optimiser or broker adapters. Shadow weights never reach orders. Book-aware sizing is PR B.
- No automatic enabling: `card_policy.mode` ships `"off"`, `card_policy.validity` ships `"session_close"`.
- Exact values: `mode` `"off"` | `"preview"` | `"enforce"`; `min_measured_ev` `None`; `min_mature_cards` `20`; `stats_window_days` `90`; `stats_time_et` `"08:30"`; `stats_poll_seconds` `300`; `stats_max_age_seconds` `345600`; `validity` `"session_close"` | `"next_session_close"`; outcome `card_policy_withheld`; stream `card_stats`; key `card_stats/{et_date}`; kind `card_stats_snapshot`; readiness component `card_stats`; labeller protocol `setup-outcomes-v1`; scan-window avoidance `slot − 5 min` to `slot + 20 min`; header `📋 SETUP:`; runner-up reason `card policy: measured EV {ev:+.2f}R over {n} < {threshold:+.2f}R`; tap reply `⏳ Market closed; card valid until {valid_until}. Next regular open {next_open UTC}.`; outcome `"live card pending"`.
- No broker or Telegram mutation in tests. Tests use the fixture config (`tests/fixtures/config.yaml`), temporary SQLite (`temp_db`), stripped credentials and blocked Python/libcurl network I/O (`tests/conftest.py:isolated_runtime`). Never relax these guards. Bar sources in tests are fakes.
- Inject explicit config and storage: `CardStatsRepository` reaches `TradingCopilot` as a constructor kwarg; the daemon never imports CLI code (shared helpers live in `agentic_trader/research/setups/sources.py`).
- Every journal, provenance and notification number is finite or `None` (`allow_nan=False` encoders).
- Notifications queued before this change (no `card_evidence` key) must still deliver; every new notification key is a defaulted `send_signal_alert` kwarg.
- Before each commit: the task's tests, the regression gate `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/notifier tests/execution tests/research/setups tests/cli tests/config tests/workflows tests/market`, then `env -u VIRTUAL_ENV uv run pre-commit run --all-files` (re-stage anything ruff rewrote and re-run until clean).
- Docs are updated in the same task that changes the behaviour; docstrings and docs describe final behaviour only.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **`mode: off` written unquoted in YAML** parses as boolean `false` (YAML 1.1); it must load as `"off"`, and `on` must be rejected, never crash the daemon at start or silently pick another mode — pinned in Task 2 (`test_an_unquoted_yaml_off_means_off`, `test_an_unquoted_yaml_on_is_rejected`).
2. **A provider outage at the stats due time** (every bar fetch fails) must write no snapshot and leave yesterday's authoritative, instead of persisting an all-`fetch_failed` snapshot that turns every card "insufficient" and silently disarms `enforce` — pinned in Task 5 (`test_a_provider_outage_writes_nothing_and_keeps_the_previous_snapshot`).
3. **Notifications queued across the deploy** — a SIGNAL row without `card_evidence`, or one whose `card_evidence` no longer validates — must still deliver through the real outbox, never dead-letter — pinned in Task 3 (`test_a_queued_card_without_evidence_delivers_unchanged`, `test_malformed_evidence_delivers_as_no_statistics`).
4. **A Friday or pre-holiday card under `next_session_close`** must be valid until the next trading day's (possibly early) close, and a weekend tap must wait rather than expire — pinned in Task 6 (`test_the_next_close_skips_a_holiday_and_keeps_an_early_close`, `test_a_friday_card_is_valid_until_monday_close`, `test_a_closed_market_tap_waits_and_leaves_the_card_live`).
5. **Daemon shutdown during a labelling run** (minutes of provider reads in a worker thread) must not hang the launchd restart — pinned in Task 5 (`test_shutdown_cancels_an_in_flight_labelling_run`).

## File Map

| File | Responsibility | Task |
| --- | --- | --- |
| `agentic_trader/research/setups/sources.py` (new) | `scan_ranked_events`, `build_bar_source`, shared by CLI and daemon | 1 |
| `agentic_trader/research/setups/card_stats.py` (new) | `CardKeyStats`, `CardStatsSnapshot`, `compute_card_stats`, `card_stats_key` (pure) | 1 |
| `agentic_trader/execution/durable.py` | `EventKind.CARD_STATS_SNAPSHOT` (1), `RankedOutcome.CARD_POLICY_WITHHELD` (4) | 1, 4 |
| `agentic_trader/cli/commands/cards.py` | imports the shared helpers | 1 |
| `agentic_trader/config.py`, `config/config.yaml` | `CardPolicyConfig`, `AppConfig.card_policy`, `load_config` wiring | 2 |
| `agentic_trader/execution/card_evidence.py` (new) | `CardEvidence`, `lookup`, `parse_card_evidence`, `format_evidence_lines`, `CardStatsRepository` | 3 |
| `agentic_trader/notifier/telegram_bot.py` | header, evidence block (3), valid-until date (6) | 3, 6 |
| `agentic_trader/agent/evaluator.py` | `macro_clearance` invariant | 3 |
| `agentic_trader/agent/copilot.py` | snapshot read, evidence (3), policy and journal (4), readiness flag (5), validity, guard, WAITING (6) | 3–6 |
| `agentic_trader/execution/card_policy.py` (new) | `CardPolicyDecision`, `decide`, `journal_block` | 4 |
| `agentic_trader/research/setups/outcomes.py` | `card_policy_withheld`, `would_withhold`, `summarize.card_policy` | 4 |
| `agentic_trader/research/setups/card_stats_worker.py` (new) | `CardStatsWorker`, `in_scan_window`, `CardStatsUnavailable` | 5 |
| `agentic_trader/diagnostics/readiness.py` | `HealthComponent.CARD_STATS`, `card_stats_enabled` | 5 |
| `agentic_trader/cli/commands/service.py` | `run_card_stats_worker`, daemon registration | 5 |
| `agentic_trader/market/session.py` | `next_regular_close_after` | 6 |
| `agentic_trader/execution/freshness.py` | `CardOutcome.WAITING`, `effective_validity`, `assess_card(validity=)` | 6 |
| `agentic_trader/storage/db.py` | `live_signal_id(contract, *, strategy=None)` | 6 |
| `docs/card-evidence.md` (new) | the contract; created in Task 2, one section per task | 2–7 |
| `docs/production.md`, `docs/risk-policy.md`, `CLAUDE.md`, `docs/alpha-roadmap.md` | operator docs, contract 8, roadmap status | 3–7 |
| `tests/execution/card_stats_fixtures.py` (new, not collected) | `key_stats`, `make_snapshot`, `FakeCardStats`, `real_evaluation` | 1, 3 |

---

### Task 1: Shared sources module, pure `compute_card_stats`, `CardStatsSnapshot`, `EventKind.CARD_STATS_SNAPSHOT`

**Files:**
- Create: `agentic_trader/research/setups/sources.py`, `agentic_trader/research/setups/card_stats.py`, `tests/execution/card_stats_fixtures.py`, `tests/research/setups/test_card_stats.py`, `tests/research/setups/test_sources.py`
- Modify: `agentic_trader/execution/durable.py:31-61` (`EventKind`), `agentic_trader/cli/commands/cards.py:12-67` (imports; delete local `build_bar_source` and `_scan_ranked_events`; `outcomes_cmd` call site; `_pead_decision_events` docstring)

**Interfaces:**
- Produces:
  - `scan_ranked_events(db: SignalDatabase, days: int, *, now: datetime) -> list[dict[str, Any]]`
  - `build_bar_source(config: AppConfig) -> AlpacaDataProvider`
  - `CARD_STATS_STREAM = "card_stats"`, `LABELLER_PROTOCOL = "setup-outcomes-v1"`, `AGGREGATE = "*"`, `card_stats_key(et_date: date) -> str` (`"card_stats/2026-10-08"`)
  - `CardKeyStats` (frozen pydantic: `strategy, direction, n_mature, n_immature, n_fetch_failed, target_rate, stop_rate, timeout_rate, mean_r_cost, mean_timeout_r, median_holding_sessions, first_decided_at, last_decided_at`)
  - `CardStatsSnapshot` (frozen pydantic: `computed_at: AwareDatetime, window_start: date, window_end: date, feed: str, cost_bps_per_side: float, max_hold_sessions: int, labeller_protocol: str, code_revision: str, events_considered: int, rows_labelled: int, keys: tuple[CardKeyStats, ...]`; property `snapshot_key -> str`; method `stats(strategy, direction) -> CardKeyStats | None`)
  - `compute_card_stats(frame, *, window_start, window_end, feed, cost_bps, max_hold_sessions, code_revision, now, events_considered) -> dict[str, Any]`
  - `EventKind.CARD_STATS_SNAPSHOT == "card_stats_snapshot"`
  - Test helpers `key_stats(strategy="TREND_PULLBACK", direction="LONG", **overrides) -> dict` and `make_snapshot(*, keys=None, computed_at=None) -> CardStatsSnapshot`

- [ ] **Step 1: Write the failing tests**

`tests/execution/card_stats_fixtures.py` (a helper module, not a test module):

```python
"""Shared card-statistics fixtures: snapshot builders (more helpers join in Task 3)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import CardStatsSnapshot


def key_stats(strategy: str = "TREND_PULLBACK", direction: str = "LONG", **overrides: Any) -> dict[str, Any]:
    """One snapshot key row: 30 mature, 20% target / 70% stop / 10% timeout, mean -0.35R, timeouts +0.5R."""
    row = {
        "strategy": strategy,
        "direction": direction,
        "n_mature": 30,
        "n_immature": 4,
        "n_fetch_failed": 0,
        "target_rate": 0.2,
        "stop_rate": 0.7,
        "timeout_rate": 0.1,
        "mean_r_cost": -0.35,
        "mean_timeout_r": 0.5,
        "median_holding_sessions": 3.0,
        "first_decided_at": "2026-07-15T14:35:00+00:00",
        "last_decided_at": "2026-10-07T18:35:00+00:00",
    }
    return {**row, **overrides}


def make_snapshot(
    *, keys: list[dict[str, Any]] | None = None, computed_at: datetime | None = None
) -> CardStatsSnapshot:
    computed_at = computed_at or datetime.now(UTC)
    window_end = computed_at.astimezone(ET_TZ).date()
    return CardStatsSnapshot.model_validate(
        {
            "computed_at": computed_at.isoformat(),
            "window_start": (window_end - timedelta(days=89)).isoformat(),
            "window_end": window_end.isoformat(),
            "feed": "iex",
            "cost_bps_per_side": 5.0,
            "max_hold_sessions": 20,
            "labeller_protocol": "setup-outcomes-v1",
            "code_revision": "test-rev",
            "events_considered": 40,
            "rows_labelled": 120,
            "keys": keys if keys is not None else [key_stats()],
        }
    )
```

`tests/research/setups/test_card_stats.py`:

```python
"""Pure per-(strategy, direction) card statistics from a labelled frame."""

import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

import agentic_trader.research.setups.outcomes as outcomes_module
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups.card_stats import (
    AGGREGATE,
    CARD_STATS_STREAM,
    LABELLER_PROTOCOL,
    CardStatsSnapshot,
    card_stats_key,
    compute_card_stats,
)
from agentic_trader.research.setups.outcomes import label_journaled
from agentic_trader.research.setups.sources import scan_ranked_events
from tests.research.setups.test_outcomes import (
    AAPL_BARS,
    DECIDED_AT,
    NOW,
    SPY_BARS,
    FakeBarSource,
    _candidate,
    _event,
)


COMPUTED_AT = datetime(2026, 10, 8, 12, 31, tzinfo=UTC)


def _labelled(rows):
    """Rows of (strategy, direction, hit, r_cost, holding_sessions, decided_at) in label_journaled's columns."""
    records = [
        {
            **dict.fromkeys(outcomes_module._COLUMNS),
            "strategy": strategy,
            "direction": direction,
            "hit": hit,
            "r_cost": r_cost,
            "holding_sessions": holding,
            "decided_at": pd.Timestamp(decided_at),
        }
        for strategy, direction, hit, r_cost, holding, decided_at in rows
    ]
    return pd.DataFrame(records, columns=list(outcomes_module._COLUMNS))


def _compute(frame, **overrides):
    kwargs = {
        "window_start": date(2026, 7, 11),
        "window_end": date(2026, 10, 8),
        "feed": "iex",
        "cost_bps": 5.0,
        "max_hold_sessions": 20,
        "code_revision": "test-rev",
        "now": COMPUTED_AT,
        "events_considered": 12,
    }
    return compute_card_stats(frame, **{**kwargs, **overrides})


FIRST, MID, LAST = "2026-07-01T14:35:00+00:00", "2026-08-01T14:35:00+00:00", "2026-09-30T18:35:00+00:00"
TP = [
    ("TREND_PULLBACK", "LONG", "target", 1.9, 2, FIRST),
    ("TREND_PULLBACK", "LONG", "target", 1.9, 2, MID),
    ("TREND_PULLBACK", "LONG", "target", 1.9, 2, MID),
    *[("TREND_PULLBACK", "LONG", "stop", -1.1, 1, MID)] * 5,
    ("TREND_PULLBACK", "LONG", "timeout", 0.3, 20, MID),
    ("TREND_PULLBACK", "LONG", "timeout", -0.1, 20, MID),
    ("TREND_PULLBACK", "LONG", "immature", None, 0, MID),
    ("TREND_PULLBACK", "LONG", "fetch_failed", None, 0, LAST),
]
SQ = [("SQUEEZE_BREAKOUT", "SHORT", "stop", -1.05, 1, MID)]


def _by_key(payload):
    return {(row["strategy"], row["direction"]): row for row in payload["keys"]}


def test_rates_are_per_key_over_mature_rows_with_fetch_failures_excluded():
    keys = _by_key(_compute(_labelled([*TP, *SQ])))
    tp = keys[("TREND_PULLBACK", "LONG")]
    assert (tp["n_mature"], tp["n_immature"], tp["n_fetch_failed"]) == (10, 1, 1)
    assert tp["target_rate"] == pytest.approx(0.3)
    assert tp["stop_rate"] == pytest.approx(0.5)
    assert tp["timeout_rate"] == pytest.approx(0.2)
    assert tp["mean_r_cost"] == pytest.approx(0.04)  # (3*1.9 - 5*1.1 + 0.3 - 0.1) / 10
    assert tp["mean_timeout_r"] == pytest.approx(0.1)  # the timeouts' partial R
    assert tp["median_holding_sessions"] == pytest.approx(1.5)
    assert tp["first_decided_at"] == FIRST and tp["last_decided_at"] == LAST
    sq = keys[("SQUEEZE_BREAKOUT", "SHORT")]
    assert sq["n_mature"] == 1 and sq["stop_rate"] == 1.0 and sq["mean_timeout_r"] is None


def test_the_aggregate_key_covers_every_row():
    agg = _by_key(_compute(_labelled([*TP, *SQ])))[(AGGREGATE, AGGREGATE)]
    assert (agg["n_mature"], agg["n_immature"], agg["n_fetch_failed"]) == (11, 1, 1)
    assert agg["target_rate"] == pytest.approx(3 / 11)
    assert agg["stop_rate"] == pytest.approx(6 / 11)
    assert agg["mean_r_cost"] == pytest.approx(-0.65 / 11)


def test_a_row_without_a_strategy_counts_only_in_the_aggregate():
    payload = _compute(_labelled([*SQ, (None, "LONG", "stop", -1.0, 1, MID)]))
    keys = _by_key(payload)
    assert set(keys) == {("SQUEEZE_BREAKOUT", "SHORT"), (AGGREGATE, AGGREGATE)}
    assert keys[(AGGREGATE, AGGREGATE)]["n_mature"] == 2


def test_output_is_nan_free_and_a_key_without_finite_r_reports_none():
    payload = _compute(_labelled([("X", "LONG", "stop", float("nan"), 1, MID), *SQ]))
    json.dumps(payload, allow_nan=False)  # the journal encoder's own rule
    assert _by_key(payload)[("X", "LONG")]["mean_r_cost"] is None


def test_a_key_with_only_immature_rows_has_no_rates():
    row = _by_key(_compute(_labelled([("X", "LONG", "immature", None, 0, MID)])))[("X", "LONG")]
    assert row["n_mature"] == 0 and row["n_immature"] == 1
    assert row["target_rate"] is None and row["mean_r_cost"] is None and row["median_holding_sessions"] is None


def test_an_empty_frame_gives_no_keys_and_full_provenance():
    payload = _compute(pd.DataFrame(columns=list(outcomes_module._COLUMNS)))
    assert payload["keys"] == []
    assert payload["rows_labelled"] == 0 and payload["events_considered"] == 12
    assert payload["labeller_protocol"] == LABELLER_PROTOCOL == "setup-outcomes-v1"
    assert payload["computed_at"] == COMPUTED_AT.isoformat()
    assert (payload["window_start"], payload["window_end"]) == ("2026-07-11", "2026-10-08")
    assert (payload["feed"], payload["cost_bps_per_side"], payload["max_hold_sessions"]) == ("iex", 5.0, 20)
    snapshot = CardStatsSnapshot.model_validate(payload)
    assert snapshot.snapshot_key == card_stats_key(date(2026, 10, 8)) == "card_stats/2026-10-08"


def test_the_snapshot_reads_back_and_finds_a_key():
    snapshot = CardStatsSnapshot.model_validate(_compute(_labelled(TP)))
    assert snapshot.stats("TREND_PULLBACK", "LONG").n_mature == 10
    assert snapshot.stats("TREND_PULLBACK", "SHORT") is None
    assert snapshot.computed_at.utcoffset() is not None


def test_vocabulary():
    assert CARD_STATS_STREAM == "card_stats"
    assert EventKind.CARD_STATS_SNAPSHOT == "card_stats_snapshot"


async def test_journaled_candidates_round_trip_into_a_snapshot(temp_db):
    """Integration: the real SQLite journal -> scan_ranked_events -> label_journaled -> compute -> model."""
    event = _event(
        "scan-1",
        DECIDED_AT,
        [
            _candidate(contract="AAPL", rank=1, outcome="sent"),
            _candidate(contract="MSFT", rank=2, outcome="per-scan budget spent"),
        ],
    )
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        await temp_db.workflows.append(
            session,
            stream=event["stream"],
            kind=EventKind.SCAN_CANDIDATES_RANKED,
            payload=event["payload"],
            key="scan_candidates_ranked/scan-1",
        )
    events = await scan_ranked_events(temp_db, 3, now=NOW)
    frame = label_journaled(events, FakeBarSource({"AAPL": AAPL_BARS, "SPY": SPY_BARS}, raise_for={"MSFT"}), now=NOW)
    payload = compute_card_stats(
        frame,
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 2),
        feed="iex",
        cost_bps=5.0,
        max_hold_sessions=20,
        code_revision="test-rev",
        now=NOW,
        events_considered=len(events),
    )
    snapshot = CardStatsSnapshot.model_validate(json.loads(json.dumps(payload, allow_nan=False)))
    stats = snapshot.stats("BREAKOUT", "LONG")
    assert stats.n_mature == 1 and stats.n_fetch_failed == 1 and stats.target_rate == 1.0
    assert snapshot.events_considered == 1 and snapshot.rows_labelled == 2
```

`tests/research/setups/test_sources.py`:

```python
"""The journal walk and bar source shared by `copilot cards outcomes` and the daemon."""

from datetime import UTC, datetime

import agentic_trader.cli.commands.cards as cards_module
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups import sources


async def _append(db, stream, kind, payload, key):
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(session, stream=stream, kind=kind, payload=payload, key=key)


async def test_scan_ranked_events_walks_each_et_date_and_keeps_only_ranked_events(temp_db):
    await _append(
        temp_db, "scan/2026-03-02", EventKind.SCAN_CANDIDATES_RANKED, {"scan_id": "a"}, "scan_candidates_ranked/a"
    )
    await _append(temp_db, "scan/2026-03-02", EventKind.PEAD_DECISION, {"scan_id": "p"}, "pead_decision/p")
    await _append(
        temp_db, "scan/2026-02-01", EventKind.SCAN_CANDIDATES_RANKED, {"scan_id": "old"}, "scan_candidates_ranked/old"
    )
    # 2026-03-03 15:00 UTC is 10:00 New York: two ET dates back are 03-03 and 03-02.
    events = await sources.scan_ranked_events(temp_db, 2, now=datetime(2026, 3, 3, 15, 0, tzinfo=UTC))
    assert [event["payload"]["scan_id"] for event in events] == ["a"]


def test_the_cli_uses_the_shared_helpers():
    assert cards_module.build_bar_source is sources.build_bar_source
    assert cards_module.scan_ranked_events is sources.scan_ranked_events
    assert not hasattr(cards_module, "_scan_ranked_events")
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/setups/test_card_stats.py tests/research/setups/test_sources.py`
Expected: FAIL — `ModuleNotFoundError: agentic_trader.research.setups.card_stats` / `sources`.

- [ ] **Step 3: Implement**

`agentic_trader/research/setups/sources.py`:

```python
"""Read paths for journaled suggestion-scan evidence, shared by the CLI and the daemon.

``copilot cards outcomes`` and the daemon's ``card_stats`` worker both read the
``scan_candidates_ranked`` journal and label it on the configured feed; neither imports the other.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from agentic_trader.data.evidence import BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.runtime import state_directory


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.storage.db import SignalDatabase

__all__ = ["build_bar_source", "scan_ranked_events"]


def build_bar_source(config: AppConfig) -> AlpacaDataProvider:
    """The suggestion scan's own market-data provider path: raw adjustment, the configured feed."""
    return AlpacaDataProvider(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        feed=config.market_data.alpaca_feed,
        request_timeout=config.market_data.timeout_seconds,
        evidence=BarEvidenceStore(state_directory() / "market-data", config.market_data.evidence),
    )


async def scan_ranked_events(db: SignalDatabase, days: int, *, now: datetime) -> list[dict[str, Any]]:
    """Every ``scan_candidates_ranked`` event journaled on each ET calendar date in the window.

    ``_journal_scan_ranking`` stamps one event per scan under stream ``scan/{et_date}`` (a date
    may hold more than one, since a session may run more than one suggestion scan); the reader
    has no range/prefix query, so this walks each date's exact stream.
    """
    et_today = now.astimezone(ET_TZ).date()
    events: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        events.extend(event for event in day_events if event.get("kind") == EventKind.SCAN_CANDIDATES_RANKED)
    return events
```

`agentic_trader/cli/commands/cards.py`: delete `build_bar_source` and `_scan_ranked_events`; replace the imports
`from agentic_trader.config import AppConfig, load_config`, `from agentic_trader.data.evidence import BarEvidenceStore` and `from agentic_trader.runtime import state_directory` with `from agentic_trader.config import load_config` and add `from agentic_trader.research.setups.sources import build_bar_source, scan_ranked_events` (keep `AlpacaDataProvider`, `EventKind`, `ET_TZ`, `timedelta`: the PEAD section uses them). In `outcomes_cmd`: `events = await scan_ranked_events(db, days, now=now)`. In `_pead_decision_events`' docstring, replace "Mirrors ``_scan_ranked_events``'" with "Mirrors ``scan_ranked_events``'".

`agentic_trader/execution/durable.py`, in `EventKind` after `PEAD_DECISION`:

```python
    CARD_STATS_SNAPSHOT = "card_stats_snapshot"
```

`agentic_trader/research/setups/card_stats.py`:

```python
"""Measured outcomes of journaled suggestion-scan candidates, per ``(strategy, direction)``.

Pure: a frame labelled by ``label_journaled`` in, one JSON-safe snapshot payload out. The
daemon's ``card_stats`` worker persists it once per New York date; cards read it back through
``agentic_trader.execution.card_evidence``. Nothing here fetches bars, writes or ranks.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from pydantic import AwareDatetime, BaseModel

from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.outcomes import FETCH_FAILED_HIT
from agentic_trader.research.setups.ranker import finite_or_none


__all__ = [
    "AGGREGATE",
    "CARD_STATS_STREAM",
    "LABELLER_PROTOCOL",
    "CardKeyStats",
    "CardStatsSnapshot",
    "card_stats_key",
    "compute_card_stats",
]

CARD_STATS_STREAM = "card_stats"
# The outcome labeller's frozen protocol (config/research/setup-outcomes-v1.json).
LABELLER_PROTOCOL = "setup-outcomes-v1"
# The all-candidates key: strategy and direction are both "*".
AGGREGATE = "*"


def card_stats_key(et_date: date) -> str:
    """The idempotent journal key of one New York date's snapshot."""
    return f"card_stats/{et_date.isoformat()}"


class CardKeyStats(BaseModel, frozen=True):
    """One ``(strategy, direction)`` row. Rates are over mature labels; fetch failures are excluded."""

    strategy: str
    direction: str
    n_mature: int
    n_immature: int
    n_fetch_failed: int
    target_rate: float | None
    stop_rate: float | None
    timeout_rate: float | None
    mean_r_cost: float | None
    mean_timeout_r: float | None
    median_holding_sessions: float | None
    first_decided_at: AwareDatetime | None
    last_decided_at: AwareDatetime | None


class CardStatsSnapshot(BaseModel, frozen=True):
    """The persisted ``card_stats_snapshot`` payload. Unknown keys are ignored, so a rollback still reads it."""

    computed_at: AwareDatetime
    window_start: date
    window_end: date
    feed: str
    cost_bps_per_side: float
    max_hold_sessions: int
    labeller_protocol: str
    code_revision: str
    events_considered: int
    rows_labelled: int
    keys: tuple[CardKeyStats, ...] = ()

    @property
    def snapshot_key(self) -> str:
        return card_stats_key(self.window_end)

    def stats(self, strategy: str, direction: str) -> CardKeyStats | None:
        return next((row for row in self.keys if row.strategy == strategy and row.direction == direction), None)


def _key_stats(rows: pd.DataFrame, strategy: str, direction: str) -> dict[str, Any]:
    hit = rows["hit"].astype(str)
    fetch_failed = hit == FETCH_FAILED_HIT
    immature = hit == BracketHit.IMMATURE.value
    mature = rows.loc[~(fetch_failed | immature)]
    mature_hit = mature["hit"].astype(str)
    n_mature = len(mature)
    r_cost = pd.to_numeric(mature["r_cost"], errors="coerce")
    timeout_r = r_cost[mature_hit == BracketHit.TIMEOUT.value]
    holding = pd.to_numeric(mature["holding_sessions"], errors="coerce")
    decided = pd.to_datetime(rows["decided_at"], utc=True).dropna()

    def rate(outcome: BracketHit) -> float | None:
        return finite_or_none((mature_hit == outcome.value).mean()) if n_mature else None

    return {
        "strategy": strategy,
        "direction": direction,
        "n_mature": n_mature,
        "n_immature": int(immature.sum()),
        "n_fetch_failed": int(fetch_failed.sum()),
        "target_rate": rate(BracketHit.TARGET),
        "stop_rate": rate(BracketHit.STOP),
        "timeout_rate": rate(BracketHit.TIMEOUT),
        "mean_r_cost": finite_or_none(r_cost.mean()) if n_mature else None,
        "mean_timeout_r": finite_or_none(timeout_r.mean()) if len(timeout_r) else None,
        "median_holding_sessions": finite_or_none(holding.median()) if n_mature else None,
        "first_decided_at": decided.min().isoformat() if len(decided) else None,
        "last_decided_at": decided.max().isoformat() if len(decided) else None,
    }


def compute_card_stats(
    frame: pd.DataFrame,
    *,
    window_start: date,
    window_end: date,
    feed: str,
    cost_bps: float,
    max_hold_sessions: int,
    code_revision: str,
    now: datetime,
    events_considered: int,
) -> dict[str, Any]:
    """One snapshot payload from a ``label_journaled`` frame: every ``(strategy, direction)`` plus ``("*", "*")``.

    Rows without a strategy or direction count only in the aggregate; an empty frame gives no
    keys. The payload is validated as a ``CardStatsSnapshot`` before it is returned, so the writer
    never persists what the reader would refuse, and every number is finite or None (the journal
    encodes with ``allow_nan=False``).
    """
    keys = [
        _key_stats(rows, str(strategy), str(direction))
        for (strategy, direction), rows in frame.groupby(["strategy", "direction"], sort=True)
    ]
    if not frame.empty:
        keys.append(_key_stats(frame, AGGREGATE, AGGREGATE))
    payload: dict[str, Any] = {
        "computed_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "feed": feed,
        "cost_bps_per_side": float(cost_bps),
        "max_hold_sessions": int(max_hold_sessions),
        "labeller_protocol": LABELLER_PROTOCOL,
        "code_revision": code_revision,
        "events_considered": int(events_considered),
        "rows_labelled": len(frame),
        "keys": keys,
    }
    CardStatsSnapshot.model_validate(payload)
    return payload
```

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/setups/test_card_stats.py tests/research/setups/test_sources.py tests/cli/test_cli_smoke.py::test_cli_outputs_table_with_fake_sources tests/research/setups/test_outcomes.py`
Expected: PASS (the CLI smoke test still patches `cards_module.build_bar_source`, which is now the imported shared name).

- [ ] **Step 5: Regression gate, pre-commit, stage; the controller commits**

```bash
git add agentic_trader/research/setups/sources.py agentic_trader/research/setups/card_stats.py \
  agentic_trader/execution/durable.py agentic_trader/cli/commands/cards.py \
  tests/execution/card_stats_fixtures.py tests/research/setups/test_card_stats.py tests/research/setups/test_sources.py
git commit -m "Card statistics: shared journal/bar sources and a pure per-(strategy, direction) snapshot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `CardPolicyConfig`, `AppConfig.card_policy`, `load_config` wiring, shipped YAML block

**Files:**
- Modify: `agentic_trader/config.py` (module constant `HHMM_PATTERN`; `SchedulerConfig.valid_times` uses it; new `CardValidity`, `CardPolicyMode`, `CardPolicyConfig` after `ScanConfig` at :193-215; `AppConfig` field; `load_config` `AppConfig(...)` call at :938-1003), `config/config.yaml` (new top-level block after `scan:`)
- Create: `docs/card-evidence.md` (purpose and configuration), `tests/config/test_card_policy_config.py`, `tests/agent/test_card_policy_wiring.py` (the integration test needs `scan_desk` from `tests/agent/conftest.py`, which `tests/config` cannot see)

**Interfaces:**
- Produces: `CardValidity = Literal["session_close", "next_session_close"]`, `CardPolicyMode = Literal["off", "preview", "enforce"]`, `CardPolicyConfig` (`model_config = {"extra": "forbid"}`; fields and defaults exactly as the spec table), `AppConfig.card_policy: CardPolicyConfig`, `load_config` builds it from `cfg_dict.get("card_policy") or {}`.

- [ ] **Step 1: Write the failing tests**

`tests/config/test_card_policy_config.py`:

```python
"""The operator's card policy: off by default, preview before enforce, wired through load_config."""

import textwrap
from pathlib import Path

import pytest
import yaml

from agentic_trader.config import AppConfig, CardPolicyConfig, load_config


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}
REPO = Path(__file__).resolve().parents[2]


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_defaults_are_off_and_unchanged_validity():
    policy = CardPolicyConfig()
    assert policy.mode == "off" and policy.min_measured_ev is None
    assert policy.min_mature_cards == 20 and policy.stats_window_days == 90
    assert policy.stats_time_et == "08:30" and policy.stats_poll_seconds == 300
    assert policy.stats_max_age_seconds == 345600
    assert policy.validity == "session_close"
    assert AppConfig().card_policy == policy


@pytest.mark.parametrize("mode", ["preview", "enforce"])
def test_an_active_mode_requires_a_threshold(mode):
    with pytest.raises(ValueError, match="min_measured_ev"):
        CardPolicyConfig(mode=mode)
    assert CardPolicyConfig(mode=mode, min_measured_ev=0.0).min_measured_ev == 0.0


@pytest.mark.parametrize(
    "fields",
    [
        {"unknown_key": 1},
        {"mode": "on"},
        {"validity": "forever"},
        {"stats_time_et": "8:30"},
        {"stats_time_et": "24:00"},
        {"min_mature_cards": 0},
        {"min_measured_ev": float("nan"), "mode": "enforce"},
        {"stats_poll_seconds": 5},
        {"stats_max_age_seconds": 300},  # not longer than the poll interval
    ],
)
def test_invalid_blocks_are_rejected(fields):
    with pytest.raises(ValueError):
        CardPolicyConfig(**fields)


def test_load_config_round_trips_the_card_policy_block(tmp_path):
    path = write(
        tmp_path,
        """
        card_policy:
          mode: preview
          min_measured_ev: -0.05
          min_mature_cards: 25
          stats_window_days: 60
          stats_time_et: "07:45"
          stats_poll_seconds: 120
          stats_max_age_seconds: 259200
          validity: next_session_close
        """,
    )
    config = load_config(path, environ=PRODUCTION)
    assert config.card_policy == CardPolicyConfig(
        mode="preview",
        min_measured_ev=-0.05,
        min_mature_cards=25,
        stats_window_days=60,
        stats_time_et="07:45",
        stats_poll_seconds=120,
        stats_max_age_seconds=259200,
        validity="next_session_close",
    )


def test_an_unquoted_yaml_off_means_off(tmp_path):
    # YAML 1.1 reads a bare `off` as false; it must still mean "off".
    config = load_config(write(tmp_path, "card_policy:\n  mode: off\n"), environ=PRODUCTION)
    assert config.card_policy.mode == "off"


def test_an_unquoted_yaml_on_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, "card_policy:\n  mode: on\n  min_measured_ev: 0.0\n"), environ=PRODUCTION)


def test_unknown_keys_fail_the_load(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, "card_policy:\n  min_measured_r: 0.0\n"), environ=PRODUCTION)


@pytest.mark.parametrize("body", ["card_policy:\n", "contracts: {}\n"])
def test_a_bare_or_missing_block_loads_the_defaults(tmp_path, body):
    assert load_config(write(tmp_path, body), environ=PRODUCTION).card_policy == CardPolicyConfig()


def test_the_shipped_config_keeps_the_policy_off():
    shipped = yaml.safe_load((REPO / "config" / "config.yaml").read_text())["card_policy"]
    assert CardPolicyConfig(**shipped) == CardPolicyConfig()
```

`tests/agent/test_card_policy_wiring.py`:

```python
"""The default card policy reaches a real TradingCopilot and changes no scan result."""

from agentic_trader.config import CardPolicyConfig, ScanBudget
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)


async def test_the_default_policy_leaves_the_scan_unchanged(budget_desk, temp_db, app_config):  # noqa: F811
    """Integration: the fixture config loads the default block; a real TradingCopilot on temp_db still sends the top card."""
    assert app_config.card_policy == CardPolicyConfig()
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/config/test_card_policy_config.py tests/agent/test_card_policy_wiring.py`
Expected: FAIL — `ImportError: cannot import name 'CardPolicyConfig'`.

- [ ] **Step 3: Implement**

`agentic_trader/config.py` — after the imports (before `SchedulerConfig`), add the shared pattern and reuse it in `SchedulerConfig.valid_times` (replace its inline regex with `HHMM_PATTERN`):

```python
# A New York wall-clock time, "HH:MM" on the 24-hour clock.
HHMM_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
```

After `ScanConfig`:

```python
CardValidity = Literal["session_close", "next_session_close"]
CardPolicyMode = Literal["off", "preview", "enforce"]


class CardPolicyConfig(BaseModel):
    """Measured-evidence card policy, statistics worker and card validity (docs/card-evidence.md).

    Ships off: the evidence block is always rendered, but nothing is withheld until the operator
    sets ``preview`` (journal ``would_withhold``, still send) and then ``enforce``. It withholds
    only on measured evidence and is not a risk rule.
    """

    model_config = {"extra": "forbid"}

    mode: CardPolicyMode = "off"
    min_measured_ev: float | None = Field(default=None, allow_inf_nan=False)
    min_mature_cards: int = Field(default=20, ge=1)
    stats_window_days: int = Field(default=90, ge=1, le=365)
    stats_time_et: str = "08:30"
    stats_poll_seconds: int = Field(default=300, ge=10, le=3600)
    stats_max_age_seconds: int = Field(default=345600, gt=0)
    validity: CardValidity = "session_close"

    @field_validator("mode", mode="before")
    @classmethod
    def yaml_off(cls, value: Any) -> Any:
        # YAML 1.1 reads a bare `off` as False; that spelling means "off". `on`/True stays invalid.
        return "off" if value is False else value

    @field_validator("stats_time_et")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if not re.fullmatch(HHMM_PATTERN, value):
            raise ValueError(f"Invalid HH:MM time: {value}")
        return value

    @model_validator(mode="after")
    def threshold_when_active(self):
        if self.mode != "off" and self.min_measured_ev is None:
            raise ValueError("card_policy.min_measured_ev is required when mode is preview or enforce")
        if self.stats_max_age_seconds <= self.stats_poll_seconds:
            raise ValueError("card_policy.stats_max_age_seconds must exceed stats_poll_seconds")
        return self
```

`AppConfig`, after `scan: ScanConfig = ...`:

```python
    card_policy: CardPolicyConfig = Field(default_factory=CardPolicyConfig)
```

`load_config`, in the explicit `AppConfig(...)` call after `scan=ScanConfig(...)`:

```python
card_policy = (CardPolicyConfig(**(cfg_dict.get("card_policy") or {})),)
```

`config/config.yaml`, after the `scan:` block:

```yaml
# Honest cards (docs/card-evidence.md): measured-evidence send policy, statistics worker, card validity.
# Quote "off": YAML reads a bare off as false (the loader maps false back to "off").
card_policy:
  mode: "off"             # off -> preview -> enforce, an operator decision after reading the evidence
  min_measured_ev: null   # R after cost; required for preview and enforce
  min_mature_cards: 20
  stats_window_days: 90
  stats_time_et: "08:30"
  stats_poll_seconds: 300
  stats_max_age_seconds: 345600
  validity: "session_close"
```

`docs/card-evidence.md` (new):

```markdown
# Card evidence and the card policy

Desk-direction item 1, PR A ([roadmap](alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8),
[design](superpowers/specs/2026-10-08-honest-cards-design.md)). Every native suggestion card states its
own measured record, and one operator-configured switch, `card_policy`, can stop sending setups whose
measured expected value is negative. Nothing here claims or creates edge: it measures, displays and,
only when the operator turns it on, withholds. `card_policy` is not a risk rule
([risk policy](risk-policy.md#what-is-not-a-risk-rule)); it never touches the book, admission, the
FIFO or the broker.

## Configuration

Top-level `card_policy:` (`CardPolicyConfig`; unknown keys fail the load):

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `"off"` | `off`: the policy is not evaluated (the evidence block is still rendered); `preview`: evaluate and journal `would_withhold`, still send; `enforce`: withhold |
| `min_measured_ev` | `null` | Threshold on the measured mean R after cost; required when `mode` is not `off` |
| `min_mature_cards` | `20` | Mature labels a `(strategy, direction)` needs before the policy may withhold; also the card's "insufficient evidence" floor |
| `stats_window_days` | `90` | Labeller window, in New York calendar days |
| `stats_time_et` | `"08:30"` | Daily due time of the statistics worker (HH:MM New York) |
| `stats_poll_seconds` | `300` | Worker poll interval |
| `stats_max_age_seconds` | `345600` | Evidence older than this is `stale` on cards and in `card_stats` readiness (four days covers weekends and holidays) |
| `validity` | `"session_close"` | Card validity; see [Card validity](#card-validity) |

Quote `"off"` in YAML. YAML 1.1 reads a bare `off` as `false`; the loader maps `false` back to `"off"`
and rejects `on`/`true`.
```

(Task 3 appends `## Evidence block` and `## Limits`; Tasks 4 and 6 insert their sections before `## Limits`; Task 5 inserts its section right after `## Configuration`.)

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/config tests/agent/test_card_policy_wiring.py`
Expected: PASS (including `tests/config/test_universe_config.py::test_invalid_suggestion_scan_times_are_rejected`, which now goes through `HHMM_PATTERN`).

- [ ] **Step 5: Regression gate, pre-commit, stage; the controller commits**

```bash
git add agentic_trader/config.py config/config.yaml docs/card-evidence.md tests/config/test_card_policy_config.py \
  tests/agent/test_card_policy_wiring.py
git commit -m "Card policy config: validated card_policy block (ships off), load_config wiring and shipped YAML

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Card evidence — model, repository, lookup, rendering, scan wiring, macro invariant

**Files:**
- Create: `agentic_trader/execution/card_evidence.py`, `tests/execution/test_card_evidence.py`, `tests/notifier/test_card_evidence_rendering.py`, `tests/agent/test_scan_card_evidence.py`
- Modify: `tests/execution/card_stats_fixtures.py` (add `FakeCardStats`, `real_evaluation`, `EVIDENCE`)
- Modify: `agentic_trader/notifier/telegram_bot.py:99-253` (`format_alert_card`), `:256-379` (`format_terminal_card`), `:1200-1250` (`send_signal_alert`), new helper `_evidence_block`
- Modify: `agentic_trader/agent/copilot.py:200-230` (`__init__` kwarg), `:1105-1110` (after `signal_ids_by_rank`: snapshot read + `evidence_by_rank`), `:1216-1291` (dry-run print, provenance, notification), new `_card_stats_snapshot`, `:2963-2988` (`_replacement_card` notification)
- Modify: `agentic_trader/agent/evaluator.py:686-694` (hard invariants)
- Modify tests: `tests/notifier/test_telegram_interactive.py` (two header assertions), `tests/notifier/test_drift_card.py` (one header assertion), `tests/agent/test_card_freshness_tap.py` (new replacement test), `tests/agent/test_evaluator_llm_verdict.py` (two macro tests)
- Docs: `docs/card-evidence.md` (Evidence block, Limits), `docs/production.md` (new paragraph after "**Card rendering.**")

**Interfaces:**
- Consumes: `CardStatsSnapshot`, `CARD_STATS_STREAM`, `card_stats_key` (Task 1); `CardPolicyConfig.stats_max_age_seconds`, `.min_mature_cards` (Task 2); `EventKind.CARD_STATS_SNAPSHOT`.
- Produces:
  - `EvidenceStatus = Literal["measured", "insufficient", "stale", "unavailable"]`
  - `CardEvidence` (frozen pydantic: `status, strategy, direction, n_mature: int | None, min_mature: int = 0, target_rate, stop_rate, timeout_rate, mean_r_cost, mean_timeout_r, window_start: date | None, window_end: date | None, feed: str | None, cost_bps_per_side: float | None, snapshot_key: str | None, computed_at: datetime | None`; method `implied_ev(rr: float) -> float | None`)
  - `lookup(snapshot: CardStatsSnapshot | None, strategy: str, direction: str, *, now: datetime, max_age: timedelta, min_mature: int) -> CardEvidence`
  - `parse_card_evidence(raw: Any, *, strategy: str, direction: str) -> CardEvidence | None`
  - `format_evidence_lines(evidence: CardEvidence, rr: float, *, html: bool) -> list[str]`
  - `CardStatsRepository(workflows: WorkflowStore)` with `async latest() -> CardStatsSnapshot | None`, `async exists(et_date: date) -> bool`, `async record(payload: dict[str, Any]) -> None`
  - `card_evidence: dict[str, Any] | None = None` kwarg on `format_alert_card`, `format_terminal_card`, `TelegramNotifier.send_signal_alert`
  - `TradingCopilot(..., card_stats: CardStatsRepository | None = None)` → `self.card_stats`; `TradingCopilot._card_stats_snapshot(summary) -> CardStatsSnapshot | None`
  - In `run_scan`: `evidence_by_rank: list[CardEvidence | None]` (None for drift); provenance and notification key `card_evidence` (`CardEvidence.model_dump(mode="json")`) on native cards; scan summary key `card_stats_error` on a failed read.
  - Test helpers: `FakeCardStats(snapshot=None, *, error=None)` with `reads: int` and `async latest()`; `real_evaluation(candidate, *, rr=2.0) -> LLMTradeEvaluation`; `EVIDENCE` (a measured evidence dict).

- [ ] **Step 1: Write the failing tests**

In `tests/execution/card_stats_fixtures.py`, add these imports at the top of the module (ruff E402 forbids them mid-file):

```python
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AssetClass
from agentic_trader.execution.card_evidence import lookup
```

and append:

```python
class FakeCardStats:
    """A ``CardStatsRepository`` stand-in for ``run_scan``: a fixed snapshot, counting reads."""

    def __init__(self, snapshot: CardStatsSnapshot | None = None, *, error: Exception | None = None):
        self.snapshot, self.error, self.reads = snapshot, error, 0

    async def latest(self) -> CardStatsSnapshot | None:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


def real_evaluation(candidate: Any, *, rr: float = 2.0) -> LLMTradeEvaluation:
    """A deliverable evaluation (the outbox validates it): entry 100, stop 98, target at ``rr``."""
    return LLMTradeEvaluation(
        approved=True,
        contract=candidate.contract,
        direction=candidate.direction,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=100.0 + 2.0 * rr,
        stop_distance_points=2.0,
        target_distance_points=2.0 * rr,
        risk_reward_ratio=rr,
        risk_dollars=2.0,
        reward_dollars=2.0 * rr,
        notional_value=100.0,
        effective_leverage=0.001,
        macro_clearance=True,
        thesis_summary="fixture thesis",
        quantity=1.0,
        asset_class=AssetClass.EQUITY,
    )


EVIDENCE = lookup(
    make_snapshot(), "TREND_PULLBACK", "LONG", now=datetime.now(UTC), max_age=timedelta(days=4), min_mature=20
).model_dump(mode="json")
```

`tests/execution/test_card_evidence.py`:

```python
"""Card evidence: lookup statuses, the shared line formatter and the card_stats journal stream."""

import html as html_module
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.execution.card_evidence import (
    CardEvidence,
    CardStatsRepository,
    format_evidence_lines,
    lookup,
    parse_card_evidence,
)
from agentic_trader.execution.durable import EventKind
from tests.execution.card_stats_fixtures import key_stats, make_snapshot


NOW = datetime(2026, 10, 8, 14, 35, tzinfo=UTC)  # 10:35 New York
MAX_AGE = timedelta(seconds=345600)
FRESH = make_snapshot(computed_at=NOW - timedelta(hours=6))


def _lookup(snapshot=FRESH, strategy="TREND_PULLBACK", direction="LONG", **kw):
    return lookup(snapshot, strategy, direction, now=NOW, max_age=MAX_AGE, min_mature=kw.get("min_mature", 20))


def test_measured_evidence_carries_the_key_and_the_snapshot_provenance():
    evidence = _lookup()
    assert evidence.status == "measured" and evidence.n_mature == 30 and evidence.min_mature == 20
    assert (evidence.target_rate, evidence.stop_rate, evidence.timeout_rate) == (0.2, 0.7, 0.1)
    assert evidence.mean_r_cost == -0.35 and evidence.mean_timeout_r == 0.5
    assert evidence.snapshot_key == "card_stats/2026-10-08" and evidence.feed == "iex"
    assert evidence.cost_bps_per_side == 5.0 and evidence.window_start == date(2026, 7, 11)


@pytest.mark.parametrize(
    ("snapshot", "direction", "status", "n_mature"),
    [
        (make_snapshot(keys=[key_stats(n_mature=19)], computed_at=NOW), "LONG", "insufficient", 19),
        (FRESH, "SHORT", "insufficient", 0),  # no row for the key
        (make_snapshot(computed_at=NOW - MAX_AGE - timedelta(seconds=1)), "LONG", "stale", None),
        (make_snapshot(computed_at=NOW - MAX_AGE), "LONG", "measured", 30),  # exactly at the limit
        (None, "LONG", "unavailable", None),
    ],
)
def test_lookup_statuses(snapshot, direction, status, n_mature):
    evidence = _lookup(snapshot, direction=direction)
    assert evidence.status == status and evidence.n_mature == n_mature


def test_implied_ev_uses_the_cards_own_ratio_and_the_timeouts_r():
    evidence = _lookup()
    assert evidence.implied_ev(2.0) == pytest.approx(0.2 * 2.0 - 0.7 + 0.1 * 0.5)
    assert evidence.model_copy(update={"mean_timeout_r": None}).implied_ev(3.0) == pytest.approx(0.6 - 0.7)
    assert _lookup(None).implied_ev(2.0) is None


def test_measured_lines_in_both_renderers():
    html_lines = format_evidence_lines(_lookup(), 2.0, html=True)
    assert html_lines == [
        "• <b>Measured record</b> (TREND_PULLBACK, LONG): 30 mature cards since 2026-07-11: "
        "20% target / 70% stop / 10% timeout, mean -0.35R after cost",
        "• <b>Implied EV at 2.0:1:</b> -0.25R",
        "<i>Not validated alpha. Record measured on journaled candidates' deterministic brackets at the next "
        "hourly open (iex, 5 bp/side).</i>",
    ]
    text_lines = format_evidence_lines(_lookup(), 2.0, html=False)
    # The same facts, from the same helper: stripping the markup gives the terminal lines.
    assert [html_module.unescape(re.sub(r"</?[bi]>", "", line)) for line in html_lines] == text_lines


@pytest.mark.parametrize(
    ("evidence", "line"),
    [
        (
            _lookup(make_snapshot(keys=[key_stats(n_mature=7)], computed_at=NOW)),
            "• <b>Measured record:</b> insufficient evidence (7/20 mature cards)",
        ),
        (
            _lookup(make_snapshot(computed_at=datetime(2026, 10, 2, 12, 31, tzinfo=UTC))),
            "• <b>Measured record:</b> statistics stale (last computed 2026-10-02)",
        ),
        (_lookup(None), "• <b>Measured record:</b> no statistics in this scope"),
    ],
)
def test_other_status_lines_and_the_caveat_always_last(evidence, line):
    lines = format_evidence_lines(evidence, 2.0, html=True)
    assert lines[0] == line and lines[-1].startswith("<i>Not validated alpha.")
    if evidence.status == "unavailable":
        assert lines[-1].endswith("at the next hourly open.</i>")  # no feed or cost without a snapshot


def test_strategy_text_is_html_escaped():
    evidence = _lookup().model_copy(update={"strategy": "<b>x</b>"})
    assert "(&lt;b&gt;x&lt;/b&gt;, LONG)" in format_evidence_lines(evidence, 2.0, html=True)[0]


def test_parse_card_evidence_never_raises():
    assert parse_card_evidence(None, strategy="S", direction="LONG") is None
    assert parse_card_evidence(_lookup().model_dump(mode="json"), strategy="S", direction="LONG") == _lookup()
    for malformed in ({"status": "bogus"}, "not a dict", {"strategy": "S"}):
        fallback = parse_card_evidence(malformed, strategy="S", direction="LONG")
        assert fallback == CardEvidence(status="unavailable", strategy="S", direction="LONG")


async def test_repository_records_reads_and_is_idempotent(temp_db):
    repository = CardStatsRepository(temp_db.workflows)
    assert await repository.latest() is None and not await repository.exists(date(2026, 10, 8))
    payload = FRESH.model_dump(mode="json")
    await repository.record(payload)
    await repository.record(payload)  # same key: the journal keeps one event
    events = await temp_db.workflows.events(stream="card_stats")
    assert len(events) == 1 and events[0]["kind"] == EventKind.CARD_STATS_SNAPSHOT
    assert await repository.exists(date(2026, 10, 8))
    assert await repository.latest() == FRESH


async def test_an_unreadable_snapshot_is_no_evidence_not_an_error(temp_db):
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        await temp_db.workflows.append(
            session, stream="card_stats", kind=EventKind.CARD_STATS_SNAPSHOT, payload={"bogus": 1}, key="card_stats/x"
        )
    assert await CardStatsRepository(temp_db.workflows).latest() is None
```

`tests/notifier/test_card_evidence_rendering.py`:

```python
"""Both card renderers: the SETUP header and the measured-record block inside the plain card."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_alert_card, format_terminal_card
from tests.execution.card_stats_fixtures import EVIDENCE
from tests.notifier.test_drift_card import DRIFT


@pytest.fixture
def eval_res():
    return LLMTradeEvaluation(
        approved=True,
        contract="AAPL",
        direction="LONG",
        entry_price=190.0,
        stop_loss=186.0,
        take_profit=198.0,
        stop_distance_points=4.0,
        target_distance_points=8.0,
        risk_reward_ratio=2.0,
        risk_dollars=100.0,
        reward_dollars=200.0,
        notional_value=1900.0,
        effective_leverage=0.19,
        macro_clearance=True,
        thesis_summary="Pullback thesis",
        quantity=10.0,
        asset_class=AssetClass.EQUITY,
    )


def test_both_cards_say_setup_not_trade_signal(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    term = format_terminal_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    assert "📋 <b>SETUP: 10 shares AAPL (LONG)</b>" in card and "TRADE SIGNAL" not in card
    assert "📋 SETUP: 10 shares AAPL (LONG)" in term and "TRADE SIGNAL" not in term


def test_the_block_sits_between_the_target_and_the_thesis_in_both_renderers(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    assert card.index("<b>Target (2.0:1):</b>") < card.index("Measured record") < card.index("Implied EV")
    assert card.index("Not validated alpha") < card.index("Risk &amp; Portfolio Context") < card.index("Thesis")
    term = format_terminal_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    assert term.index("• Target:") < term.index("• Measured record (") < term.index("Not validated alpha")
    assert term.index("Not validated alpha") < term.index("Risk & Portfolio Context") < term.index("Thesis")


def test_a_card_without_evidence_renders_no_block(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK")
    assert "Measured record" not in card and "Not validated alpha" not in card
    assert "(+8.00 pts | +$200.00)\n\n🛡️" in card  # the old layout, byte for byte around the gap


def test_probe_and_drift_cards_still_end_with_the_plain_card(eval_res):
    plain = format_alert_card(eval_res, "alpha_x", card_evidence=EVIDENCE)
    assert format_alert_card(eval_res, "alpha_x", probe_risk_cap=100.0, card_evidence=EVIDENCE).endswith(plain)
    drift_plain = format_alert_card(eval_res, "pead_long", card_evidence=EVIDENCE)
    drift = format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift=DRIFT, card_evidence=EVIDENCE)
    assert drift.endswith(drift_plain)


async def _dispatch(temp_db, eval_res, notification_extra):
    await temp_db.record_signal(
        contract="AAPL",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=190.0,
        stop_loss=186.0,
        take_profit=198.0,
        risk_dollars=100.0,
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
        notification={
            "eval_res": eval_res.model_dump(mode="json"),
            "strategy": "TREND_PULLBACK",
            "regime_summary": "calm",
            "probe_risk_cap": None,
            **notification_extra,
        },
    )
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=7))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    [item] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    return item, send.await_args.kwargs["text"]


async def test_a_queued_card_without_evidence_delivers_unchanged(temp_db, eval_res):
    item, text = await _dispatch(temp_db, eval_res, {})
    assert item.status == WorkStatus.DELIVERED
    assert "📋 <b>SETUP:" in text and "Measured record" not in text


async def test_a_card_with_evidence_delivers_its_block(temp_db, eval_res):
    item, text = await _dispatch(temp_db, eval_res, {"card_evidence": EVIDENCE})
    assert item.status == WorkStatus.DELIVERED
    assert "• <b>Implied EV at 2.0:1:</b> -0.25R" in text


async def test_malformed_evidence_delivers_as_no_statistics(temp_db, eval_res):
    item, text = await _dispatch(temp_db, eval_res, {"card_evidence": {"status": "bogus"}})
    assert item.status == WorkStatus.DELIVERED
    assert "• <b>Measured record:</b> no statistics in this scope" in text
```

`tests/agent/test_scan_card_evidence.py`:

```python
"""run_scan reads the card-statistics snapshot once and every native card carries its evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.config import ScanBudget, load_config
from agentic_trader.execution.durable import WorkKind
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import FakeCardStats, make_snapshot, real_evaluation


async def test_a_sent_card_carries_its_evidence_through_the_real_outbox(budget_desk, temp_db, capsys):  # noqa: F811
    stats = FakeCardStats(make_snapshot())
    budget_desk.card_stats = stats
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    assert stats.reads == 1  # once per scan, never per candidate
    [signal] = await temp_db.get_recent_signals(limit=10)
    evidence = signal["decision_provenance"]["card_evidence"]
    assert evidence["status"] == "measured" and evidence["n_mature"] == 30
    assert evidence["snapshot_key"] == stats.snapshot.snapshot_key
    [item] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert item.payload["arguments"]["card_evidence"] == evidence

    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=9))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    text = send.await_args.kwargs["text"]
    since = stats.snapshot.window_start.isoformat()
    assert "📋 <b>SETUP: 1 shares DDD (LONG)</b>" in text
    assert f"• <b>Measured record</b> (TREND_PULLBACK, LONG): 30 mature cards since {since}:" in text
    assert "• <b>Implied EV at 2.0:1:</b> -0.25R" in text
    assert f"• Measured record (TREND_PULLBACK, LONG): 30 mature cards since {since}:" in capsys.readouterr().out


async def test_a_failed_statistics_read_never_blocks_a_card(budget_desk, temp_db):  # noqa: F811
    budget_desk.card_stats = FakeCardStats(error=RuntimeError("journal down"))
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["card_evidence"]["status"] == "unavailable"
    assert budget_desk.last_scan_summary["card_stats_error"] == "RuntimeError: journal down"


async def test_dry_run_cards_state_no_statistics_in_this_scope(budget_desk, temp_db, capsys):  # noqa: F811
    # The real repository on the empty test database, as a dry scan's empty temporary database.
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))
    await budget_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL)
    out = capsys.readouterr().out
    assert out.count("• Measured record: no statistics in this scope") == 5
    assert out.count("📋 SETUP:") == 5
```

In `tests/agent/test_card_freshness_tap.py` add:

```python
async def test_a_repriced_card_carries_the_original_evidence_block(tap_desk, temp_db):
    evidence = {
        "status": "insufficient",
        "strategy": "TREND_PULLBACK",
        "direction": "LONG",
        "n_mature": 4,
        "min_mature": 20,
    }
    sid = await record_card(temp_db, age_seconds=3600, provenance_extra={"card_evidence": evidence})
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # +0.4R -> REPRICE

    await tap_desk.execute_signal_by_id(sid)

    [notification] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    arguments = notification.payload["arguments"]
    assert arguments["card_evidence"] == evidence
    assert (await temp_db.get_signal_by_id(arguments["signal_id"]))["decision_provenance"]["card_evidence"] == evidence
    rebuilt = LLMTradeEvaluation.model_validate(arguments["eval_res"])
    inspect.signature(TelegramNotifier.send_signal_alert).bind(None, **{**arguments, "eval_res": rebuilt})
```

In `tests/agent/test_evaluator_llm_verdict.py` add:

```python
async def test_macro_line_is_the_deterministic_gate_not_the_llm(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(
        monkeypatch, approved=True, rejection_reason=None, stop_loss=49.9, take_profit=60.0, macro_clearance=False
    )

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.llm_verdict is not None  # the LLM path, not the deterministic fallback
    assert result.macro_clearance is True


async def test_an_llm_answer_without_macro_clearance_is_not_a_fallback(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    document = {**LLM_VETO, "approved": True, "rejection_reason": None, "stop_loss": 49.9, "take_profit": 60.0}
    del document["macro_clearance"]
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(document)))])
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", AsyncMock(return_value=response))

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.llm_verdict is not None and result.macro_clearance is True
    assert not result.thesis_summary.startswith("Automated thesis")
```

(`LLM_VETO` is imported from `tests.agent.test_evaluator_catalog` next to `drift_candidate`; add it to that import.)

Existing tests whose assertions change (header):

- `tests/notifier/test_telegram_interactive.py::test_format_alert_card_renders_updated_card_prefix_when_reprices_set`: `assert card.index("UPDATED CARD") < card.index("📋 <b>SETUP:")`
- `tests/notifier/test_telegram_interactive.py::test_format_terminal_card_renders_updated_card_prefix_when_reprices_set`: `assert card.index("UPDATED CARD") < card.index("📋 SETUP:")`
- `tests/notifier/test_drift_card.py::test_drift_card_shows_the_event_and_the_time_exit`: `assert text.index("PAPER PROBE") < text.index(EVENT_LINE) < text.index(HOLD_LINE) < text.index("📋 <b>SETUP:")`

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/execution/test_card_evidence.py tests/notifier/test_card_evidence_rendering.py tests/agent/test_scan_card_evidence.py tests/agent/test_evaluator_llm_verdict.py tests/agent/test_card_freshness_tap.py::test_a_repriced_card_carries_the_original_evidence_block`
Expected: FAIL — `ModuleNotFoundError: agentic_trader.execution.card_evidence`; the macro tests fail (`macro_clearance` False / fallback thesis).

- [ ] **Step 3: Implement**

`agentic_trader/execution/card_evidence.py`:

```python
"""What a native suggestion card states about its own measured record (docs/card-evidence.md).

``lookup``, ``parse_card_evidence`` and ``format_evidence_lines`` are pure; ``CardStatsRepository``
reads and writes the ``card_stats`` journal stream in this scope. Evidence only: nothing here
ranks, sizes or gates a card (the operator's switch is ``agentic_trader.execution.card_policy``).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import CARD_STATS_STREAM, CardStatsSnapshot, card_stats_key
from agentic_trader.storage.models import DomainEventRecord


if TYPE_CHECKING:
    from agentic_trader.storage.workflow import WorkflowStore

logger = logging.getLogger(__name__)

__all__ = [
    "CAVEAT",
    "CardEvidence",
    "CardStatsRepository",
    "EvidenceStatus",
    "format_evidence_lines",
    "lookup",
    "parse_card_evidence",
]

EvidenceStatus = Literal["measured", "insufficient", "stale", "unavailable"]
CAVEAT = "Not validated alpha. Record measured on journaled candidates' deterministic brackets at the next hourly open"


class CardEvidence(BaseModel, frozen=True):
    """What one card displays; stored in provenance and in its notification as ``card_evidence``."""

    status: EvidenceStatus
    strategy: str
    direction: str
    n_mature: int | None = None
    min_mature: int = Field(default=0, ge=0)
    target_rate: float | None = None
    stop_rate: float | None = None
    timeout_rate: float | None = None
    mean_r_cost: float | None = None
    mean_timeout_r: float | None = None
    window_start: date | None = None
    window_end: date | None = None
    feed: str | None = None
    cost_bps_per_side: float | None = None
    snapshot_key: str | None = None
    computed_at: datetime | None = None

    def implied_ev(self, rr: float) -> float | None:
        """``target_rate·rr − stop_rate + timeout_rate·mean_timeout_r`` (no timeouts' R counts as 0); None unless measured."""
        if self.status != "measured" or self.target_rate is None or self.stop_rate is None or self.timeout_rate is None:
            return None
        return self.target_rate * rr - self.stop_rate + self.timeout_rate * (self.mean_timeout_r or 0.0)


def lookup(
    snapshot: CardStatsSnapshot | None,
    strategy: str,
    direction: str,
    *,
    now: datetime,
    max_age: timedelta,
    min_mature: int,
) -> CardEvidence:
    """The evidence a card for ``(strategy, direction)`` displays, from the latest snapshot in this scope."""
    if snapshot is None:
        return CardEvidence(status="unavailable", strategy=strategy, direction=direction, min_mature=min_mature)
    provenance: dict[str, Any] = {
        "strategy": strategy,
        "direction": direction,
        "min_mature": min_mature,
        "window_start": snapshot.window_start,
        "window_end": snapshot.window_end,
        "feed": snapshot.feed,
        "cost_bps_per_side": snapshot.cost_bps_per_side,
        "snapshot_key": snapshot.snapshot_key,
        "computed_at": snapshot.computed_at,
    }
    if now - snapshot.computed_at > max_age:
        return CardEvidence(status="stale", **provenance)
    stats = snapshot.stats(strategy, direction)
    n_mature = stats.n_mature if stats is not None else 0
    if stats is None or n_mature < min_mature or stats.mean_r_cost is None:
        return CardEvidence(status="insufficient", n_mature=n_mature, **provenance)
    return CardEvidence(
        status="measured",
        n_mature=n_mature,
        target_rate=stats.target_rate,
        stop_rate=stats.stop_rate,
        timeout_rate=stats.timeout_rate,
        mean_r_cost=stats.mean_r_cost,
        mean_timeout_r=stats.mean_timeout_r,
        **provenance,
    )


def parse_card_evidence(raw: Any, *, strategy: str, direction: str) -> CardEvidence | None:
    """A notification's ``card_evidence``: None when absent; ``unavailable`` when it does not validate.

    A card recorded before the evidence block existed renders without one; a malformed payload
    must never fail a delivery, so it renders as "no statistics".
    """
    if raw is None:
        return None
    try:
        return CardEvidence.model_validate(raw)
    except ValidationError:
        logger.warning(
            "Card evidence does not validate; rendered as unavailable", extra={"event": "card_evidence_invalid"}
        )
        return CardEvidence(status="unavailable", strategy=strategy, direction=direction)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def format_evidence_lines(evidence: CardEvidence, rr: float, *, html: bool) -> list[str]:
    """The card's evidence block, one line per item; the Telegram (``html``) and terminal cards share it."""

    def bold(text: str) -> str:
        return f"<b>{text}</b>" if html else text

    def text(value: object) -> str:
        return escape(str(value), quote=False) if html else str(value)

    ev = evidence.implied_ev(rr)
    if evidence.status == "measured" and ev is not None and evidence.mean_r_cost is not None:
        lines = [
            f"• {bold('Measured record')} ({text(evidence.strategy)}, {text(evidence.direction)}): "
            f"{evidence.n_mature} mature cards since {evidence.window_start}: {_pct(evidence.target_rate)} target / "
            f"{_pct(evidence.stop_rate)} stop / {_pct(evidence.timeout_rate)} timeout, "
            f"mean {evidence.mean_r_cost:+.2f}R after cost",
            f"• {bold(f'Implied EV at {rr:.1f}:1:')} {ev:+.2f}R",
        ]
    elif evidence.status in ("measured", "insufficient"):
        lines = [
            f"• {bold('Measured record:')} insufficient evidence "
            f"({evidence.n_mature or 0}/{evidence.min_mature} mature cards)"
        ]
    elif evidence.status == "stale":
        computed = evidence.computed_at.astimezone(ET_TZ).date().isoformat() if evidence.computed_at else "unknown"
        lines = [f"• {bold('Measured record:')} statistics stale (last computed {computed})"]
    else:
        lines = [f"• {bold('Measured record:')} no statistics in this scope"]
    source = (
        f" ({evidence.feed}, {evidence.cost_bps_per_side:g} bp/side)."
        if evidence.feed is not None and evidence.cost_bps_per_side is not None
        else "."
    )
    caveat = CAVEAT + source
    lines.append(f"<i>{escape(caveat, quote=False)}</i>" if html else caveat)
    return lines


class CardStatsRepository:
    """The ``card_stats`` journal stream in this scope: one idempotent snapshot per New York date."""

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    async def latest(self) -> CardStatsSnapshot | None:
        """The newest snapshot, or None when there is none or it does not validate (never raises on a payload)."""
        rows = await self.workflows.events(stream=CARD_STATS_STREAM, limit=1)
        if not rows:
            return None
        try:
            return CardStatsSnapshot.model_validate(rows[0]["payload"])
        except ValidationError:
            logger.warning(
                "Card statistics event %s does not validate; treated as no statistics",
                rows[0]["id"],
                extra={"event": "card_stats_invalid", "event_id": rows[0]["id"]},
            )
            return None

    async def exists(self, et_date: date) -> bool:
        key = card_stats_key(et_date)
        async with self.workflows.db.session_factory() as session:
            found = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope, DomainEventRecord.event_key == key
                )
            )
        return found is not None

    async def record(self, payload: dict[str, Any]) -> None:
        """Append one snapshot under its date key, in its own locked transaction; a repeat is a no-op."""
        snapshot = CardStatsSnapshot.model_validate(payload)
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session)
            await self.workflows.append(
                session,
                stream=CARD_STATS_STREAM,
                kind=EventKind.CARD_STATS_SNAPSHOT,
                payload=payload,
                key=snapshot.snapshot_key,
            )
```

`agentic_trader/notifier/telegram_bot.py` — import `from agentic_trader.execution.card_evidence import format_evidence_lines, parse_card_evidence`, then add:

```python
def _evidence_block(card_evidence: Any, strategy: str, eval_res: LLMTradeEvaluation, *, markup: bool) -> str:
    """The card's measured-record lines, newline-terminated; empty for a card recorded without evidence."""
    evidence = parse_card_evidence(card_evidence, strategy=strategy, direction=eval_res.direction)
    if evidence is None:
        return ""
    return "".join(f"{line}\n" for line in format_evidence_lines(evidence, eval_res.risk_reward_ratio, html=markup))
```

`format_alert_card`: add the parameter `card_evidence: dict[str, Any] | None = None` after `drift`, extend the docstring ("``card_evidence`` (the scan's ``CardEvidence`` dump) renders the measured-record block right after the target line; None renders none"), compute `evidence_block = _evidence_block(card_evidence, strategy, eval_res, markup=True)` before `text = (`, and in `text` replace

```python
        f"🚨 <b>TRADE SIGNAL: {qty_str} {html.escape(eval_res.contract)} ({html.escape(eval_res.direction)})</b>\n"
```

with

```python
        f"📋 <b>SETUP: {qty_str} {html.escape(eval_res.contract)} ({html.escape(eval_res.direction)})</b>\n"
```

and replace the target line's trailing `\n\n` with one newline plus the block:

```python
        f"• <b>Target ({eval_res.risk_reward_ratio:.1f}:1):</b> <code>{eval_res.take_profit:,.2f}</code> (+{eval_res.target_distance_points:.2f} pts | +${eval_res.reward_dollars:,.2f})\n"
        f"{evidence_block}\n"
```

`format_terminal_card`: add `card_evidence: dict[str, Any] | None = None` after `first_issued_at`, compute `evidence_block = _evidence_block(card_evidence, strategy, eval_res, markup=False)`, change the header line to `{updated_card_prefix}📋 SETUP: {qty_str} {eval_res.contract} ({eval_res.direction})`, and replace the blank line between the target line and `🛡️ Risk & Portfolio Context` with `{evidence_block}` (so an empty block keeps the old blank line):

```
• Target:      {eval_res.take_profit:,.2f} (+{eval_res.target_distance_points:.2f} pts | +${eval_res.reward_dollars:,.2f} [{eval_res.risk_reward_ratio:.1f}:1])
{evidence_block}
🛡️ Risk & Portfolio Context
```

`TelegramNotifier.send_signal_alert`: add `card_evidence: dict[str, Any] | None = None` after `drift`, pass `card_evidence=card_evidence` to both `format_terminal_card(...)` and `format_alert_card(...)`, and extend the leading comment: "``card_evidence`` (native scan cards and their replacements) renders the measured-record block on both cards; payloads queued before it existed omit it."

`agentic_trader/agent/evaluator.py`, in "Enforce hard invariants over LLM values" after `data["asset_class"] = asset_class`:

```python
            # A candidate that reached the LLM already passed the deterministic macro lockout gate
            # (step 2 above); the card's Macro Check states that gate, never the LLM's own value.
            data["macro_clearance"] = True
```

`agentic_trader/agent/copilot.py`:

- imports: `from agentic_trader.execution.card_evidence import CardEvidence, CardStatsRepository, lookup` and `from agentic_trader.research.setups.card_stats import CardStatsSnapshot`.
- `__init__`: add the kwarg `card_stats: CardStatsRepository | None = None,` after `earnings_drift`, and after `self.alpha_shadow = AlphaShadowService(self.alpha_repository)`:

```python
        # Read side of the daemon's daily card statistics; tests inject a fake with a fixed snapshot.
        self.card_stats = card_stats if card_stats is not None else CardStatsRepository(self.db.workflows)
```

- new method next to `_shadow_blocks`:

```python
    async def _card_stats_snapshot(self, summary: dict[str, Any]) -> CardStatsSnapshot | None:
        """The latest ``card_stats`` snapshot in this scope, read once per scan.

        Evidence only: a failed read is recorded as ``card_stats_error`` and every card then
        states that no statistics are available; it never blocks a card.
        """
        try:
            return await self.card_stats.latest()
        except Exception as exc:
            summary["card_stats_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Card statistics unavailable for this scan: %s",
                summary["card_stats_error"],
                extra={"event": "card_stats_read_failed"},
            )
            return None
```

- in `run_scan`, right after `signal_ids_by_rank: list[int | None] = [None] * len(ranked)`:

```python
            # EVIDENCE: one snapshot read per scan; every native card states its own (strategy,
            # direction) record. Drift cards keep their PEAD block and carry none.
            card_policy = self.config.card_policy
            snapshot = await self._card_stats_snapshot(summary)
            evidence_by_rank: list[CardEvidence | None] = [
                None
                if getattr(candidate, "catalog_event", None) is not None
                else lookup(
                    snapshot,
                    candidate.strategy,
                    candidate.direction,
                    now=decided_at,
                    max_age=timedelta(seconds=card_policy.stats_max_age_seconds),
                    min_mature=card_policy.min_mature_cards,
                )
                for candidate, _det_res, _account_risk in ranked
            ]
```

- in the send loop, immediately before `if dry_run:`:

```python
                    evidence = evidence_by_rank[rank - 1]
                    card_evidence = evidence.model_dump(mode="json") if evidence is not None else None
                    evidence_keys: dict[str, Any] = {"card_evidence": card_evidence} if card_evidence is not None else {}
```

- the dry-run print gains `card_evidence=card_evidence` in its `format_terminal_card(...)` call;
- `decision_provenance={...}` gains `**evidence_keys,` after `"llm_verdict": llm_by_rank[rank - 1],`;
- `notification={...}` gains `**({"card_evidence": card_evidence} if card_evidence is not None else {}),` before `**validity,`.
- `_replacement_card`, in `notification = {...}` after the `valid_until` entry:

```python
            # The original card's evidence, unchanged (it is decision-time evidence, not re-looked-up).
            **({"card_evidence": old_provenance["card_evidence"]} if old_provenance.get("card_evidence") else {}),
```

`docs/card-evidence.md` — append at the end of the file:

```markdown
## Evidence block

`TradingCopilot.run_scan` reads the newest `card_stats` snapshot in this scope once per scan
(`CardStatsRepository.latest`) and looks up each native candidate's `(strategy, direction)`
(`lookup`). The result, `CardEvidence`, is stored in the signal's `decision_provenance["card_evidence"]`
and in its outbox notification, so the Telegram card, the terminal card, the dry-run print and a
re-priced replacement render the same facts from one helper (`format_evidence_lines`). PEAD drift
cards keep their own block and carry none. The block sits inside the card, right after the target
line:

| Status | When | Line |
| --- | --- | --- |
| `measured` | snapshot fresh, at least `min_mature_cards` mature labels | `• Measured record (STRATEGY, DIRECTION): N mature cards since WINDOW_START: T% target / S% stop / O% timeout, mean ±X.XXR after cost`, then `• Implied EV at R.R:1: ±Y.YYR` |
| `insufficient` | fewer mature labels, or no row for the key | `• Measured record: insufficient evidence (N/MIN mature cards)` |
| `stale` | snapshot older than `stats_max_age_seconds` | `• Measured record: statistics stale (last computed DATE)` |
| `unavailable` | no snapshot in this scope (every dry scan), a failed read or an unreadable payload | `• Measured record: no statistics in this scope` |

The last line is always, in italics: "Not validated alpha. Record measured on journaled candidates'
deterministic brackets at the next hourly open (FEED, C bp/side)." The parenthesis is omitted when no
snapshot exists.

`mean` is the directly measured quantity: the mean cost-adjusted R of the key's mature labels at the
journaled bracket. The implied EV is `target_rate·rr − stop_rate + timeout_rate·mean_timeout_r` at the
card's own ratio `rr` (no timeouts' R counts as 0); it is labelled implied because the card's ratio can
differ from the journaled bracket.

The card title reads `📋 SETUP:` (it read `🚨 TRADE SIGNAL:`). `Macro Check` states the deterministic
lockout gate: a candidate that reached the LLM already passed it, so the evaluator sets
`macro_clearance` to true after the LLM answers; the LLM's own value is not displayed and the prompt is
unchanged. A notification queued before this change has no `card_evidence` and renders without a
block; a malformed one renders the `unavailable` line and still delivers.

## Limits

- **Proxy bracket.** Labels use the journaled deterministic COLLECT bracket (not the LLM-edited bracket
  that was sent), enter at the open of the first regular hourly bar at or after the scan's `decided_at`
  (not the limit price, not the tap), cost a fixed 5 bp per side, and use the configured feed (IEX in
  production, with its hourly gaps). The record is the setup's, not this card's own win rate.
- **Censoring bias.** A label stays immature until the stop or target hits or 20 regular sessions pass,
  so early samples over-represent fast resolutions. The sample floor does not remove this.
- **Coverage.** Only scheduled suggestion scans journal `scan_candidates_ranked`. Swing, intraday,
  `/scan` and Re-evaluate cards show evidence and obey the policy but are never labelled.
- **Scope.** Snapshots are per `{environment}/{execution_mode}`: simulator and Alpaca paper statistics
  never mix, and a dry scan (an empty temporary database) always says "no statistics in this scope".
```

`docs/production.md` — new paragraph immediately after the "**Card rendering.**" paragraph (which ends "…expires on the New York date change instead."):

```markdown
**Card wording and measured record (October 8).** A card's title reads `📋 SETUP: …` (it was
`🚨 TRADE SIGNAL: …`) in both the Telegram and terminal cards. Directly under the target line a
native card states the measured record of its own `(strategy, direction)` from the latest
`card_stats` snapshot in this scope — mature count since the window start, target/stop/timeout
rates, mean R after cost and the implied EV at the card's own ratio — or says
`insufficient evidence (n/20 mature cards)`, `statistics stale (last computed DATE)` or
`no statistics in this scope` (every dry scan). The block always ends with the italic caveat "Not
validated alpha. …". The scan reads the snapshot once; a failed read records `card_stats_error` in
the scan summary and the cards state no statistics. `Macro Check` is the deterministic lockout gate:
a card that reached the LLM passed it, so it reads Cleared whatever the LLM wrote. Contract and
limits: [card evidence](card-evidence.md#evidence-block).
```

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/execution tests/notifier tests/agent/test_scan_card_evidence.py tests/agent/test_card_freshness_tap.py tests/agent/test_evaluator_llm_verdict.py tests/agent/test_scan_budget.py tests/agent/test_scan_drift.py`
Expected: PASS, including the three updated header assertions and `test_no_drift_is_byte_identical_to_the_ordinary_card`.

- [ ] **Step 5: Regression gate, pre-commit, stage; the controller commits**

```bash
git add agentic_trader/execution/card_evidence.py agentic_trader/notifier/telegram_bot.py \
  agentic_trader/agent/copilot.py agentic_trader/agent/evaluator.py \
  tests/execution/card_stats_fixtures.py tests/execution/test_card_evidence.py \
  tests/notifier/test_card_evidence_rendering.py tests/notifier/test_telegram_interactive.py tests/notifier/test_drift_card.py \
  tests/agent/test_scan_card_evidence.py tests/agent/test_card_freshness_tap.py tests/agent/test_evaluator_llm_verdict.py \
  docs/card-evidence.md docs/production.md
git commit -m "Honest cards: measured-record evidence block on every native card, SETUP header, deterministic macro line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Card policy — `decide`, `card_policy_withheld`, `run_scan` integration, journal and labeller

**Files:**
- Create: `agentic_trader/execution/card_policy.py`, `tests/execution/test_card_policy.py`, `tests/agent/test_scan_card_policy.py`
- Modify: `agentic_trader/execution/durable.py:62-71` (`RankedOutcome`), `agentic_trader/agent/copilot.py` (send loop refusal chain, provenance, `_journal_scan_ranking` at :1587-1649 and its call at :1352-1364), `agentic_trader/research/setups/outcomes.py` (`_COLUMNS`, `_row`, `label_journaled` post-processing, `summarize`)
- Modify tests: `tests/agent/test_evaluator_llm_verdict.py::test_ranked_outcome_vocabulary`, `tests/research/setups/test_outcomes.py` (`test_summary_empty_frame`, new tests)
- Docs: `docs/card-evidence.md` (Send policy), `docs/production.md` (card policy paragraph), `docs/risk-policy.md` ("What is not a risk rule"), `CLAUDE.md` (contract 8, one sentence)

**Interfaces:**
- Consumes: `CardEvidence`, `evidence_by_rank`, `snapshot`, `card_policy` (Task 3 locals in `run_scan`); `CardPolicyConfig` (Task 2).
- Produces:
  - `CardPolicyDecision` (frozen pydantic: `withhold: bool, would_withhold: bool, reason: str | None, measured_ev: float | None, n_mature: int | None`)
  - `decide(policy: CardPolicyConfig, evidence: CardEvidence) -> CardPolicyDecision`
  - `journal_block(decision: CardPolicyDecision | None, mode: str) -> dict[str, Any] | None` → `{"measured_ev", "n_mature", "would_withhold"}` (`would_withhold` None while `mode == "off"`)
  - `RankedOutcome.CARD_POLICY_WITHHELD == "card_policy_withheld"`
  - `_journal_scan_ranking(..., card_policy_by_rank: list[CardPolicyDecision | None], snapshot_key: str | None, ...)`; payload top-level `card_policy: {mode, min_measured_ev, min_mature_cards, snapshot_key}`, per candidate `card_policy`
  - Labeller columns `card_policy_withheld: bool`, `would_withhold: bool | None`; `summarize(frame)["card_policy"] == {withheld, would_withhold, withheld_mean_r_cost, would_withhold_mean_r_cost}`

- [ ] **Step 1: Write the failing tests**

`tests/execution/test_card_policy.py`:

```python
"""The pure card policy: a switch on measured evidence, never a fail-closed rule."""

import pytest

from agentic_trader.config import CardPolicyConfig
from agentic_trader.execution.card_evidence import CardEvidence
from agentic_trader.execution.card_policy import decide, journal_block


def evidence(status="measured", n_mature=30, mean_r_cost=-0.4):
    measured = status == "measured"
    return CardEvidence(
        status=status,
        strategy="TREND_PULLBACK",
        direction="LONG",
        n_mature=n_mature,
        min_mature=10,
        target_rate=0.2 if measured else None,
        stop_rate=0.8 if measured else None,
        timeout_rate=0.0 if measured else None,
        mean_r_cost=mean_r_cost if measured else None,
    )


@pytest.mark.parametrize(
    ("mode", "threshold", "status", "n_mature", "mean_r_cost", "withhold", "would"),
    [
        ("off", None, "measured", 30, -0.4, False, False),
        ("preview", 0.0, "measured", 30, -0.4, False, True),
        ("enforce", 0.0, "measured", 30, -0.4, True, True),
        ("enforce", 0.0, "measured", 30, 0.0, False, False),  # equal to the threshold is not below it
        ("enforce", 0.0, "measured", 19, -0.4, False, False),  # below min_mature_cards (20)
        ("enforce", -0.2, "measured", 30, -0.1, False, False),  # above a negative threshold
        ("enforce", 0.0, "insufficient", 5, None, False, False),
        ("enforce", 0.0, "stale", None, None, False, False),
        ("enforce", 0.0, "unavailable", None, None, False, False),
    ],
)
def test_truth_table(mode, threshold, status, n_mature, mean_r_cost, withhold, would):
    decision = decide(CardPolicyConfig(mode=mode, min_measured_ev=threshold), evidence(status, n_mature, mean_r_cost))
    assert (decision.withhold, decision.would_withhold) == (withhold, would)


def test_the_reason_names_the_measured_ev_the_sample_and_the_threshold():
    decision = decide(CardPolicyConfig(mode="enforce", min_measured_ev=0.0), evidence(mean_r_cost=-0.39))
    assert decision.reason == "card policy: measured EV -0.39R over 30 < +0.00R"
    assert (decision.measured_ev, decision.n_mature) == (-0.39, 30)


def test_unmeasured_evidence_records_no_ev_and_no_count():
    decision = decide(CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence("insufficient", 5))
    assert decision.reason is None and decision.measured_ev is None and decision.n_mature is None


def test_journal_block():
    decision = decide(CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence())
    assert journal_block(decision, "preview") == {"measured_ev": -0.4, "n_mature": 30, "would_withhold": True}
    assert journal_block(decision, "off")["would_withhold"] is None
    assert journal_block(None, "enforce") is None
```

`tests/agent/test_scan_card_policy.py`:

```python
"""run_scan applies the configured card policy before the LLM, journals it and keeps labelling."""

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from agentic_trader.config import CardPolicyConfig, ScanBudget
from agentic_trader.execution.durable import EventKind, RankedOutcome
from agentic_trader.research.setups.outcomes import label_journaled, summarize
from tests.agent.test_scan_budget import budget_desk, candidate  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import FakeCardStats, key_stats, make_snapshot


QUALITIES = {"AAA": 0.6, "BBB": 0.7, "CCC": 0.8, "DDD": 0.9, "EEE": 0.5}
NEGATIVE = key_stats(
    "TREND_PULLBACK", "LONG", target_rate=0.23, stop_rate=0.77, timeout_rate=0.0, mean_r_cost=-0.39, mean_timeout_r=None
)
REASON = "card policy: measured EV -0.39R over 30 < +0.00R"


@pytest.fixture
def policy_desk(shadow_desk):  # noqa: F811
    """DDD (rank 1) is TREND_PULLBACK, measured at -0.39R over 30; every other name is SQUEEZE_BREAKOUT with 5 mature."""
    shadow_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(
            data.contract,
            QUALITIES[data.contract],
            strategy="TREND_PULLBACK" if data.contract == "DDD" else "SQUEEZE_BREAKOUT",
        )
    ]
    shadow_desk.card_stats = FakeCardStats(
        make_snapshot(keys=[NEGATIVE, key_stats("SQUEEZE_BREAKOUT", "LONG", n_mature=5, mean_r_cost=0.1)])
    )
    return shadow_desk


async def _ranked(db):
    [event] = [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    return event["payload"], {c["contract"]: c for c in event["payload"]["candidates"]}


def _llm_contracts(desk):
    return [c.args[0].contract for c in desk.evaluator.evaluate_candidate.call_args_list if c.kwargs.get("use_llm")]


async def test_off_sends_and_journals_evidence_without_a_verdict(policy_desk, temp_db):
    await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
    payload, by_contract = await _ranked(temp_db)
    assert payload["card_policy"] == {
        "mode": "off",
        "min_measured_ev": None,
        "min_mature_cards": 20,
        "snapshot_key": policy_desk.card_stats.snapshot.snapshot_key,
    }
    assert by_contract["DDD"]["card_policy"] == {"measured_ev": -0.39, "n_mature": 30, "would_withhold": None}
    assert by_contract["CCC"]["card_policy"] == {"measured_ev": None, "n_mature": None, "would_withhold": None}


async def test_preview_sends_and_journals_would_withhold(policy_desk, temp_db, app_config, caplog):
    app_config.card_policy = CardPolicyConfig(mode="preview", min_measured_ev=0.0)
    with caplog.at_level(logging.INFO, logger="copilot"):
        await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["card_policy"] == {
        "measured_ev": -0.39,
        "n_mature": 30,
        "would_withhold": True,
    }
    _, by_contract = await _ranked(temp_db)
    assert by_contract["DDD"]["outcome"] == "sent" and by_contract["DDD"]["card_policy"]["would_withhold"] is True
    assert by_contract["CCC"]["card_policy"]["would_withhold"] is False
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "card_policy_would_withhold"]
    assert record.contract == "DDD" and record.measured_ev == -0.39


async def test_enforce_withholds_before_the_llm_and_falls_through(policy_desk, temp_db, app_config):
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["CCC"]
    assert _llm_contracts(policy_desk) == ["CCC"]  # the withheld rank spent no LLM budget
    assert len(await temp_db.signals_since(policy_desk.session_start_et())) == 1  # nor card budget
    reasons = {r["contract"]: r["reason"] for r in policy_desk.last_scan_summary["runners_up"]}
    assert reasons["DDD"] == REASON
    payload, by_contract = await _ranked(temp_db)
    assert payload["card_policy"]["mode"] == "enforce" and payload["card_policy"]["min_measured_ev"] == 0.0
    ddd = by_contract["DDD"]
    assert ddd["outcome"] == RankedOutcome.CARD_POLICY_WITHHELD == "card_policy_withheld"
    assert ddd["signal_id"] is None and ddd["llm"] is None
    assert ddd["card_policy"] == {"measured_ev": -0.39, "n_mature": 30, "would_withhold": True}
    [signal] = await temp_db.get_recent_signals(limit=10)
    provenance = signal["decision_provenance"]
    assert provenance["card_policy"] == {"measured_ev": None, "n_mature": None, "would_withhold": False}
    assert provenance["card_evidence"]["status"] == "insufficient"


@pytest.mark.parametrize("kind", ["insufficient", "stale", "unavailable"])
async def test_enforce_never_withholds_without_measured_evidence(policy_desk, temp_db, app_config, kind):
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    snapshot = {
        "insufficient": make_snapshot(keys=[{**NEGATIVE, "n_mature": 19}]),
        "stale": make_snapshot(keys=[NEGATIVE], computed_at=datetime.now(UTC) - timedelta(days=5)),
        "unavailable": None,
    }[kind]
    policy_desk.card_stats = FakeCardStats(snapshot)

    await policy_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
    _, by_contract = await _ranked(temp_db)
    assert by_contract["DDD"]["outcome"] == "sent" and by_contract["DDD"]["card_policy"]["would_withhold"] is False


class _StaleBars:
    """Bars entirely before the scan's decision time: every candidate is IMMATURE."""

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        index = pd.date_range("2020-01-01", periods=2, freq="h", tz="UTC")
        return pd.DataFrame({"Open": [1.0] * 2, "High": [1.0] * 2, "Low": [1.0] * 2, "Close": [1.0] * 2}, index=index)


async def test_withheld_candidates_stay_journaled_and_keep_being_labelled(shadow_desk, temp_db, app_config):  # noqa: F811
    """Integration: run_scan journal -> label_journaled -> summarize, every candidate below the threshold."""
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    shadow_desk.card_stats = FakeCardStats(make_snapshot(keys=[NEGATIVE]))  # all five are TREND_PULLBACK LONG

    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert await temp_db.get_recent_signals(limit=10) == []
    assert _llm_contracts(shadow_desk) == []
    events = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    frame = label_journaled(events, _StaleBars())
    assert len(frame) == 5 and frame["card_policy_withheld"].all() and (frame["would_withhold"] == True).all()  # noqa: E712
    block = summarize(frame)["card_policy"]
    assert block == {
        "withheld": 5,
        "would_withhold": 5,
        "withheld_mean_r_cost": None,
        "would_withhold_mean_r_cost": None,
    }
```

Append to `tests/research/setups/test_outcomes.py`:

```python
def test_card_policy_columns_and_summary_block():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {
                    **_candidate(rank=1, outcome="card_policy_withheld"),
                    "card_policy": {"measured_ev": -0.4, "n_mature": 30, "would_withhold": True},
                },
                {
                    **_candidate(rank=2, outcome="sent"),
                    "card_policy": {"measured_ev": None, "n_mature": None, "would_withhold": False},
                },
                _candidate(rank=3, outcome="per-scan budget spent"),  # journaled before the policy existed
            ],
        )
    ]
    frame = label_journaled(events, bars, now=NOW).sort_values("rank")
    assert frame["card_policy_withheld"].tolist() == [True, False, False]
    assert frame["would_withhold"].tolist() == [True, False, None]
    summary = summarize(frame)
    withheld_r = frame.loc[frame["rank"] == 1, "r_cost"].iloc[0]
    assert summary["card_policy"] == {
        "withheld": 1,
        "would_withhold": 1,
        "withheld_mean_r_cost": pytest.approx(withheld_r),
        "would_withhold_mean_r_cost": pytest.approx(withheld_r),
    }
    # The existing blocks are unchanged: a withheld candidate is still a runner-up there.
    assert summary["counts"]["runner_up"]["mature"] == 2
```

Existing tests whose assertions change:

- `tests/research/setups/test_outcomes.py::test_summary_empty_frame` — add:
  `assert summary["card_policy"] == {"withheld": 0, "would_withhold": 0, "withheld_mean_r_cost": None, "would_withhold_mean_r_cost": None}`
- `tests/agent/test_evaluator_llm_verdict.py::test_ranked_outcome_vocabulary` — add:
  `assert RankedOutcome.CARD_POLICY_WITHHELD == "card_policy_withheld"`

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/execution/test_card_policy.py tests/agent/test_scan_card_policy.py tests/research/setups/test_outcomes.py tests/agent/test_evaluator_llm_verdict.py::test_ranked_outcome_vocabulary`
Expected: FAIL — `ModuleNotFoundError: agentic_trader.execution.card_policy`; `KeyError: 'card_policy_withheld'`.

- [ ] **Step 3: Implement**

`agentic_trader/execution/card_policy.py`:

```python
"""The operator's measured-evidence send policy for native suggestion cards (docs/card-evidence.md).

Pure: ``decide`` never reads the journal, the book or the broker. It is a switch on measured
evidence, not a risk rule: insufficient, stale or unavailable evidence never withholds.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agentic_trader.config import CardPolicyConfig
from agentic_trader.execution.card_evidence import CardEvidence


__all__ = ["CardPolicyDecision", "decide", "journal_block"]


class CardPolicyDecision(BaseModel, frozen=True):
    withhold: bool
    would_withhold: bool
    reason: str | None
    measured_ev: float | None
    n_mature: int | None


def decide(policy: CardPolicyConfig, evidence: CardEvidence) -> CardPolicyDecision:
    """``would_withhold`` on measured evidence over the sample floor below the threshold; ``withhold`` only under ``enforce``."""
    measured = evidence.status == "measured"
    measured_ev = evidence.mean_r_cost if measured else None
    n_mature = evidence.n_mature if measured else None
    threshold = policy.min_measured_ev
    would = (
        policy.mode != "off"
        and threshold is not None
        and measured_ev is not None
        and n_mature is not None
        and n_mature >= policy.min_mature_cards
        and measured_ev < threshold
    )
    reason = (
        f"card policy: measured EV {measured_ev:+.2f}R over {n_mature} < {threshold:+.2f}R"
        if would and measured_ev is not None and threshold is not None
        else None
    )
    return CardPolicyDecision(
        withhold=would and policy.mode == "enforce",
        would_withhold=would,
        reason=reason,
        measured_ev=measured_ev,
        n_mature=n_mature,
    )


def journal_block(decision: CardPolicyDecision | None, mode: str) -> dict[str, Any] | None:
    """The per-candidate ``card_policy`` block (journal and provenance); ``would_withhold`` is None while off."""
    if decision is None:
        return None
    return {
        "measured_ev": decision.measured_ev,
        "n_mature": decision.n_mature,
        "would_withhold": None if mode == "off" else decision.would_withhold,
    }
```

`agentic_trader/execution/durable.py`:

```python
class RankedOutcome(StrEnum):
    """Fixed outcomes of a ranked suggestion-scan candidate (``scan_candidates_ranked``).

    Free-text reasons (``"per-scan budget spent"``, ``"rejected: …"``) stay plain strings;
    these are the outcomes a reader must recognise without parsing text.
    """

    SENT = "sent"
    LLM_VETOED = "llm_vetoed"
    CARD_POLICY_WITHHELD = "card_policy_withheld"
```

`agentic_trader/agent/copilot.py`:

- import `from agentic_trader.execution.card_policy import CardPolicyDecision, decide, journal_block`.
- after the Task 3 `evidence_by_rank` list:

```python
            policy_by_rank: list[CardPolicyDecision | None] = [
                decide(card_policy, evidence) if evidence is not None else None for evidence in evidence_by_rank
            ]
```

- in the send loop: initialise `fixed_outcome: str | None = None` next to `reason = None`, and right after the existing `if/elif` refusal chain (before `if reason:`):

```python
                    # CARD POLICY (native only, after the budget refusals, before the LLM): a switch on
                    # measured evidence. Enforce withholds and falls through to the next rank like a
                    # veto, spending no card or LLM budget; preview logs and sends.
                    decision = policy_by_rank[rank - 1]
                    if reason is None and decision is not None and decision.would_withhold:
                        if decision.withhold:
                            reason, fixed_outcome = decision.reason, RankedOutcome.CARD_POLICY_WITHHELD
                        else:
                            logger.info(
                                "Card policy preview: would withhold %s %s (%s)",
                                candidate.contract,
                                candidate.strategy,
                                decision.reason,
                                extra={
                                    "event": "card_policy_would_withhold",
                                    "contract": candidate.contract,
                                    "strategy": candidate.strategy,
                                    "direction": candidate.direction,
                                    "measured_ev": decision.measured_ev,
                                    "n_mature": decision.n_mature,
                                    "rank": rank,
                                },
                            )
                    if reason:
                        outcomes[rank - 1] = fixed_outcome or reason
                        summary["runners_up"].append(self._runner_up(candidate, reason))
                        continue
```

- replace the Task 3 `evidence_keys` line with:

```python
                    evidence_keys: dict[str, Any] = (
                        {"card_evidence": card_evidence, "card_policy": journal_block(decision, card_policy.mode)}
                        if card_evidence is not None
                        else {}
                    )
```

- the `_journal_scan_ranking(...)` call gains `card_policy_by_rank=policy_by_rank[:native_count], snapshot_key=snapshot.snapshot_key if snapshot is not None else None,`; the method gains the parameters `card_policy_by_rank: list[CardPolicyDecision | None]` and `snapshot_key: str | None`; each candidate dict gains `"card_policy": journal_block(card_policy_by_rank[rank - 1], policy.mode),` and the payload gains (with `policy = self.config.card_policy` at the top of the `try`):

```python
                "card_policy": {
                    "mode": policy.mode,
                    "min_measured_ev": policy.min_measured_ev,
                    "min_mature_cards": policy.min_mature_cards,
                    "snapshot_key": snapshot_key,
                },
```

`agentic_trader/research/setups/outcomes.py`:

- `_COLUMNS`: insert `"card_policy_withheld",` and `"would_withhold",` after `"llm_stop_loss",`.
- new helpers above `_row`:

```python
def _would_withhold(entry: dict[str, Any]) -> bool | None:
    """The journaled card-policy verdict, or None when the scan predates the policy or it was off."""
    block = entry.get("card_policy")
    value = block.get("would_withhold") if isinstance(block, dict) else None
    return value if isinstance(value, bool) else None


def _mean_r_cost(rows: pd.DataFrame) -> float | None:
    if rows.empty:
        return None
    value = float(pd.to_numeric(rows["r_cost"], errors="coerce").mean())
    return value if math.isfinite(value) else None
```

- `_row`, after `"llm_stop_loss": _applied_llm_stop(llm),`:

```python
        "card_policy_withheld": outcome == RankedOutcome.CARD_POLICY_WITHHELD,
        "would_withhold": _would_withhold(entry),
```

- `label_journaled`, after the `llm_r_cost` normalisation:

```python
    frame["would_withhold"] = frame["would_withhold"].astype(object).where(frame["would_withhold"].notna(), None)
```

- `summarize`: in the empty-frame return add `"card_policy": {"withheld": 0, "would_withhold": 0, "withheld_mean_r_cost": None, "would_withhold_mean_r_cost": None},`; in the main path, before `return {`:

```python
    withheld = frame["card_policy_withheld"].astype(bool)
    would = frame["would_withhold"].map(lambda value: value is True).astype(bool)
    card_policy = {
        "withheld": int(withheld.sum()),
        "would_withhold": int(would.sum()),
        "withheld_mean_r_cost": _mean_r_cost(mature.loc[withheld.loc[mature.index]]),
        "would_withhold_mean_r_cost": _mean_r_cost(mature.loc[would.loc[mature.index]]),
    }
```

  and add `"card_policy": card_policy,` to the returned dict. Update the module docstring's summary sentence to mention the card-policy counts.

`docs/card-evidence.md` — insert before `## Limits`:

```markdown
## Send policy

`decide(policy, evidence)` (`agentic_trader/execution/card_policy.py`) is pure:

    would_withhold = mode != "off" and evidence.status == "measured"
                     and n_mature >= min_mature_cards and mean_r_cost < min_measured_ev
    withhold       = would_withhold and mode == "enforce"

It runs in `run_scan`'s send loop for native candidates only, after the card-budget refusals and before
the LLM. A withheld candidate gets the fixed outcome `card_policy_withheld`
(`RankedOutcome.CARD_POLICY_WITHHELD`), spends no scan, session or LLM budget, becomes a runner-up with
reason `card policy: measured EV -0.39R over 30 < +0.00R`, and the next rank is considered, like an LLM
veto. Insufficient, stale or unavailable evidence never withholds: the policy is a switch on measured
evidence, not a fail-closed rule. PEAD drift and catalog cards are outside it. Every scan applies it
(suggestion, swing, intraday, `/scan`, Re-evaluate); only scheduled suggestion scans are labelled.

Evidence recorded:

- `scan_candidates_ranked` gains top-level `card_policy: {mode, min_measured_ev, min_mature_cards,
  snapshot_key}` and, per candidate, `card_policy: {measured_ev, n_mature, would_withhold}`
  (`measured_ev`/`n_mature` null unless the evidence is measured; `would_withhold` null while the mode is
  `off`). `would_withhold` is the policy's verdict on every native candidate, whether or not it reached
  the send step.
- A sent card's `decision_provenance` carries the same `card_policy` block and its `card_evidence`.
- `preview` logs one `card_policy_would_withhold` line for each candidate that reaches the check.
- `copilot cards outcomes` adds the `card_policy_withheld` and `would_withhold` columns and the summary
  block `card_policy: {withheld, would_withhold, withheld_mean_r_cost, would_withhold_mean_r_cost}`
  (counts over every row, means over mature rows). Withheld candidates stay in the journal and keep
  being labelled, so a strategy below the threshold keeps accruing evidence and can recover. The
  existing summary blocks are unchanged; a withheld candidate is still a runner-up there. The
  end-of-session digest counts it among the runners-up; the reason shows in the scan summary and in a
  `/scan SYMBOL` reply.

Operate it in order: `off` until a snapshot exists; `preview` with a threshold, reading `would_withhold`
for several sessions; then `enforce`. Each change is a config edit and the controlled restart.
```

`docs/production.md` — new paragraph after the paragraph ending "None of it ranks, gates or sizes a card.":

```markdown
**Card policy (October 8).** `card_policy.mode` is the one gate derived from these outcomes, and it
ships `off`. Operate it in three steps: leave `off` until a `card_stats` snapshot exists; set
`preview` with `min_measured_ev` (mean R after cost, for example `0.0`) and read `would_withhold` in
`cards outcomes` (summary block `card_policy`) and the `card_policy_would_withhold` log lines for a few
sessions; only then set `enforce`. Under `enforce`, a native candidate whose `(strategy, direction)`
has at least `min_mature_cards` (20) mature labels and a measured mean below the threshold is skipped
before the LLM with the fixed outcome `card_policy_withheld` (runner-up reason `card policy: measured
EV …R over N < …R`); it spends no card or LLM budget and the next rank is considered. Insufficient,
stale or unavailable evidence never withholds. Withheld candidates stay journaled and keep being
labelled, so a strategy can recover. A mode change is a config edit plus the controlled restart. See
[card evidence](card-evidence.md#send-policy).
```

`docs/risk-policy.md` — new bullet at the end of "## What is not a risk rule":

```markdown
- **The card policy:** `card_policy` ([card evidence](card-evidence.md#send-policy)). In `enforce`
  mode it withholds a native card only on measured evidence (status `measured`, at least
  `min_mature_cards` mature labels, mean R after cost below `min_measured_ev`); insufficient, stale or
  unavailable evidence never withholds. It is a switch on measured outcomes, not a fail-closed rule,
  and it never reads or changes the book, admission, the FIFO or the broker.
```

`CLAUDE.md` contract 8 — append after "…these are descriptive, never gates.":

```markdown
   The one gate derived from measured outcomes is `card_policy` ([card evidence](docs/card-evidence.md)):
   operator-configured, default `off`, `preview` before `enforce`, withholding only on measured evidence
   above a sample floor (`card_policy_withheld`), and never touching risk, admission or the broker.
```

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/execution tests/agent/test_scan_card_policy.py tests/agent/test_scan_llm_verdict.py tests/agent/test_scan_shadow_ranker.py tests/agent/test_scan_budget.py tests/research/setups tests/cli/test_cli_smoke.py`
Expected: PASS.

- [ ] **Step 5: Regression gate, pre-commit, stage; the controller commits**

```bash
git add agentic_trader/execution/card_policy.py agentic_trader/execution/durable.py agentic_trader/agent/copilot.py \
  agentic_trader/research/setups/outcomes.py tests/execution/test_card_policy.py tests/agent/test_scan_card_policy.py \
  tests/research/setups/test_outcomes.py tests/agent/test_evaluator_llm_verdict.py \
  docs/card-evidence.md docs/production.md docs/risk-policy.md CLAUDE.md
git commit -m "Card policy: off/preview/enforce on measured EV before the LLM, card_policy_withheld outcome, journal and labeller

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `card_stats` worker, readiness component, daemon registration

**Files:**
- Create: `agentic_trader/research/setups/card_stats_worker.py`, `tests/agent/test_card_stats_worker.py`
- Modify: `agentic_trader/diagnostics/readiness.py:17-26` (enum), `:29-47` (constructor), `:67-90` (limits); `agentic_trader/agent/copilot.py:440-449` (`ReadinessService(..., card_stats_enabled=True)`); `agentic_trader/cli/commands/service.py` (import, `run_card_stats_worker` after `run_daily_panel_worker`, daemon task after the daily-panel block, cancellation in the shutdown block)
- Modify tests: `tests/workflows/test_journal_readiness.py` (new test)
- Docs: `docs/card-evidence.md` (Card statistics snapshot), `docs/production.md` (worker paragraph)

**Interfaces:**
- Consumes: `scan_ranked_events`, `build_bar_source`, `compute_card_stats`, `card_stats_key` (Task 1); `CardPolicyConfig` (Task 2); `CardStatsRepository.exists/record`, `TradingCopilot.card_stats` (Task 3).
- Produces:
  - `SCAN_WINDOW_BEFORE = timedelta(minutes=5)`, `SCAN_WINDOW_AFTER = timedelta(minutes=20)`, `in_scan_window(now_et: datetime, scan_times_et: Sequence[str]) -> bool`
  - `class CardStatsUnavailable(RuntimeError)`
  - `CardStatsWorker(db, repository, config, *, revision: str, bar_source: Callable[[AppConfig], BarSource] = build_bar_source, on_progress: Callable[[str], Awaitable[None]] | None = None)` with `async run_once(now: datetime | None = None) -> str` returning `"not_due" | "scan_window" | "present" | "recorded"`
  - `HealthComponent.CARD_STATS == "card_stats"`; `ReadinessService(..., card_stats_enabled: bool = False)`
  - `run_card_stats_worker(copilot, config, readiness) -> None` (service.py)

- [ ] **Step 1: Write the failing tests**

`tests/agent/test_card_stats_worker.py`:

```python
"""The daemon's once-daily card-statistics snapshot: due time, idempotence, scan windows, failure, shutdown."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.cli.commands import service
from agentic_trader.config import ScanBudget
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.execution.card_evidence import CardStatsRepository, lookup
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats_worker import CardStatsUnavailable, CardStatsWorker, in_scan_window
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import make_snapshot
from tests.research.setups.test_outcomes import AAPL_BARS, DECIDED_AT, SPY_BARS, FakeBarSource, _candidate, _event


DUE = datetime(2026, 3, 3, 13, 31, tzinfo=UTC)  # 08:31 New York (EST)
NOT_DUE = datetime(2026, 3, 3, 13, 29, tzinfo=UTC)  # 08:29


async def plant_scan(db, *, decided_at=DECIDED_AT, scan_id="scan-1", candidates=None):
    event = _event(scan_id, decided_at, candidates or [_candidate(contract="AAPL", rank=1, outcome="sent")])
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(
            session,
            stream=f"scan/{decided_at.astimezone(ET_TZ).date().isoformat()}",
            kind=EventKind.SCAN_CANDIDATES_RANKED,
            payload=event["payload"],
            key=f"scan_candidates_ranked/{scan_id}",
        )


def worker_for(db, config, bars=None, **kwargs):
    bars = bars or FakeBarSource({"AAPL": AAPL_BARS, "SPY": SPY_BARS})
    return CardStatsWorker(
        db, CardStatsRepository(db.workflows), config, revision="test-rev", bar_source=lambda _config: bars, **kwargs
    ), bars


@pytest.mark.parametrize(
    ("hhmm", "inside"),
    [("10:29", False), ("10:30", True), ("10:54", True), ("10:55", False), ("14:31", True), ("08:30", False)],
)
def test_scan_windows_span_five_minutes_before_to_twenty_after(hhmm, inside):
    hour, minute = (int(part) for part in hhmm.split(":"))
    now_et = datetime(2026, 3, 3, hour, minute, tzinfo=ET_TZ)
    assert in_scan_window(now_et, ["10:35", "14:35"]) is inside


async def test_not_due_before_the_configured_time(temp_db, app_config):
    await plant_scan(temp_db)
    worker, bars = worker_for(temp_db, app_config)
    assert await worker.run_once(NOT_DUE) == "not_due"
    assert bars.calls == [] and await worker.repository.latest() is None


async def test_due_records_one_snapshot_per_new_york_date(temp_db, app_config):
    await plant_scan(temp_db)
    worker, bars = worker_for(temp_db, app_config)

    assert await worker.run_once(DUE) == "recorded"
    calls = len(bars.calls)
    assert await worker.run_once(DUE + timedelta(minutes=5)) == "present"  # idempotent: no second labelling
    assert len(bars.calls) == calls

    events = await temp_db.workflows.events(stream="card_stats")
    assert len(events) == 1 and events[0]["kind"] == EventKind.CARD_STATS_SNAPSHOT == "card_stats_snapshot"
    snapshot = await worker.repository.latest()
    assert snapshot.snapshot_key == "card_stats/2026-03-03"
    assert snapshot.window_start.isoformat() == "2025-12-04"  # 90 ET dates ending 2026-03-03
    assert (snapshot.feed, snapshot.code_revision) == (app_config.market_data.alpaca_feed, "test-rev")
    assert (snapshot.events_considered, snapshot.rows_labelled) == (1, 1)
    assert snapshot.stats("BREAKOUT", "LONG").target_rate == 1.0
    assert await worker.run_once(DUE + timedelta(days=1)) == "recorded"
    assert (await worker.repository.latest()).snapshot_key == "card_stats/2026-03-04"


async def test_a_due_time_inside_a_scan_window_waits_for_it_to_pass(temp_db, app_config):
    app_config.card_policy.stats_time_et = "10:31"
    app_config.scheduler.suggestion_scan_times_et = ["10:35"]
    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config)
    assert await worker.run_once(datetime(2026, 3, 3, 15, 40, tzinfo=UTC)) == "scan_window"  # 10:40 NY
    assert await worker.run_once(datetime(2026, 3, 3, 15, 56, tzinfo=UTC)) == "recorded"  # 10:56 NY


async def test_a_provider_outage_writes_nothing_and_keeps_the_previous_snapshot(temp_db, app_config):
    repository = CardStatsRepository(temp_db.workflows)
    previous = make_snapshot(computed_at=DUE - timedelta(days=1))
    await repository.record(previous.model_dump(mode="json"))
    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, bars=FakeBarSource({}, raise_for={"AAPL", "SPY"}))

    with pytest.raises(CardStatsUnavailable, match="every bar fetch failed"):
        await worker.run_once(DUE)

    assert await repository.latest() == previous
    assert not await repository.exists(DUE.astimezone(ET_TZ).date())


async def test_labelling_runs_off_the_event_loop(temp_db, app_config):
    entered, release = threading.Event(), threading.Event()

    class BlockingBars:
        def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
            entered.set()
            assert release.wait(2), "labelling blocked the event loop"
            return AAPL_BARS if symbol == "AAPL" else SPY_BARS

    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, bars=BlockingBars())
    task = asyncio.create_task(worker.run_once(DUE))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0)  # the loop still runs while the labeller waits
    finally:
        release.set()
    assert await asyncio.wait_for(task, 5) == "recorded"


async def test_progress_is_observed_before_a_long_labelling_run(temp_db, app_config):
    progress = AsyncMock()
    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, on_progress=progress)
    await worker.run_once(DUE)
    progress.assert_awaited_once_with("labelling 1 scan events")


@pytest.mark.parametrize(
    ("result", "observed"), [("not_due", (True, "not_due")), (RuntimeError("x"), (False, "RuntimeError"))]
)
async def test_every_poll_observes_readiness(monkeypatch, app_config, temp_db, result, observed):
    stop = asyncio.Event()

    class OnePoll:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, now=None):
            stop.set()
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(service, "CardStatsWorker", OnePoll)
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    readiness = SimpleNamespace(observe=AsyncMock())
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=stop)
    await asyncio.wait_for(service.run_card_stats_worker(copilot, app_config, readiness), 2)
    readiness.observe.assert_awaited_once_with(HealthComponent.CARD_STATS, *observed)


async def test_shutdown_cancels_an_in_flight_labelling_run(monkeypatch, app_config, temp_db):
    entered, release = threading.Event(), threading.Event()

    class Blocking:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, now=None):
            await asyncio.to_thread(lambda: (entered.set(), release.wait(5)))
            return "recorded"

    monkeypatch.setattr(service, "CardStatsWorker", Blocking)
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    readiness = SimpleNamespace(observe=AsyncMock())
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=asyncio.Event())
    task = asyncio.create_task(service.run_card_stats_worker(copilot, app_config, readiness))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        copilot._shutdown_event.set()
        task.cancel()  # what the daemon's shutdown does
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    finally:
        release.set()
    readiness.observe.assert_not_awaited()


def test_the_copilots_readiness_tracks_card_stats(app_config, temp_db, mock_notifier):
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
    )
    assert copilot.readiness.card_stats_enabled is True


async def test_a_recorded_snapshot_reaches_the_next_cards_evidence(budget_desk, temp_db, app_config):  # noqa: F811
    """Integration: journal -> worker -> card_stats stream -> TradingCopilot.run_scan -> card provenance."""
    app_config.card_policy.stats_time_et = "00:00"
    app_config.scheduler.suggestion_scan_times_et = []
    now = datetime.now(UTC)
    await plant_scan(
        temp_db,
        decided_at=now - timedelta(hours=1),
        candidates=[_candidate(contract="AAPL", strategy="TREND_PULLBACK", rank=1, outcome="sent")],
    )
    worker, _ = worker_for(temp_db, app_config)
    assert await worker.run_once(now) == "recorded"

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    [signal] = await temp_db.get_recent_signals(limit=10)
    evidence = signal["decision_provenance"]["card_evidence"]
    snapshot = await CardStatsRepository(temp_db.workflows).latest()
    assert evidence["snapshot_key"] == snapshot.snapshot_key
    assert evidence["status"] == "insufficient" and evidence["n_mature"] == 0  # the planted label is immature
    assert (
        lookup(snapshot, "TREND_PULLBACK", "LONG", now=now, max_age=timedelta(days=4), min_mature=20).status
        == "insufficient"
    )
```

Append to `tests/workflows/test_journal_readiness.py`:

```python
@pytest.mark.parametrize("mode", ["disabled", "missing", "fresh", "long-weekend", "stale", "failure"])
async def test_card_stats_readiness_spans_a_long_weekend(store, app_config, mode):
    readiness = ReadinessService(
        store, app_config, MetricsCollector(), run_id="current", card_stats_enabled=mode != "disabled"
    )
    if mode not in ("disabled", "missing"):
        await store.record_health(HealthComponent.CARD_STATS, mode != "failure", run_id="current")
    offset = {"long-weekend": timedelta(days=3, hours=23), "stale": timedelta(days=4, seconds=1)}.get(mode, timedelta())
    checks = (await readiness.report(now=datetime.now(UTC) + offset))["checks"]
    if mode == "disabled":
        assert "card_stats" not in checks
    else:
        assert checks["card_stats"]["ready"] == (mode in ("fresh", "long-weekend"))
        assert checks["card_stats"]["max_age_seconds"] == 345600
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_card_stats_worker.py tests/workflows/test_journal_readiness.py`
Expected: FAIL — `ModuleNotFoundError: agentic_trader.research.setups.card_stats_worker`; `AttributeError: CARD_STATS`.

- [ ] **Step 3: Implement**

`agentic_trader/research/setups/card_stats_worker.py`:

```python
"""The daemon's once-daily card-statistics snapshot (docs/card-evidence.md#card-statistics-snapshot).

Each poll asks whether ``card_policy.stats_time_et`` has passed in New York, whether this is
outside every suggestion-scan window, and whether today's key ``card_stats/{et_date}`` is
missing. When all hold it labels the journaled candidates of the last ``stats_window_days`` ET
dates in a worker thread and appends one idempotent ``card_stats_snapshot`` event. A provider
failure writes nothing: the previous snapshot stays authoritative until it ages out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import compute_card_stats
from agentic_trader.research.setups.outcomes import (
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    FETCH_FAILED_HIT,
    label_journaled,
)
from agentic_trader.research.setups.sources import build_bar_source, scan_ranked_events


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.execution.card_evidence import CardStatsRepository
    from agentic_trader.research.setups.runner import BarSource
    from agentic_trader.storage.db import SignalDatabase

__all__ = ["SCAN_WINDOW_AFTER", "SCAN_WINDOW_BEFORE", "CardStatsUnavailable", "CardStatsWorker", "in_scan_window"]

# The suggestion scans' bursts share the provider: never start labelling inside a slot's window.
SCAN_WINDOW_BEFORE = timedelta(minutes=5)
SCAN_WINDOW_AFTER = timedelta(minutes=20)


class CardStatsUnavailable(RuntimeError):
    """Labelling produced no usable evidence (every bar fetch failed); nothing is written."""


def in_scan_window(now_et: datetime, scan_times_et: Sequence[str]) -> bool:
    """True from five minutes before to twenty minutes after any New York suggestion-scan slot."""
    for slot in scan_times_et:
        due = datetime.combine(now_et.date(), time.fromisoformat(slot), tzinfo=ET_TZ)
        if due - SCAN_WINDOW_BEFORE <= now_et < due + SCAN_WINDOW_AFTER:
            return True
    return False


class CardStatsWorker:
    def __init__(
        self,
        db: SignalDatabase,
        repository: CardStatsRepository,
        config: AppConfig,
        *,
        revision: str,
        bar_source: Callable[[AppConfig], BarSource] = build_bar_source,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.db, self.repository, self.config = db, repository, config
        self.revision, self.bar_source, self.on_progress = revision, bar_source, on_progress

    async def run_once(self, now: datetime | None = None) -> str:
        """One poll: ``not_due``, ``scan_window``, ``present`` or ``recorded``; raises on a failed run."""
        now = now or datetime.now(UTC)
        now_et = now.astimezone(ET_TZ)
        policy = self.config.card_policy
        if now_et.time() < time.fromisoformat(policy.stats_time_et):
            return "not_due"
        if in_scan_window(now_et, self.config.scheduler.suggestion_scan_times_et):
            return "scan_window"
        if await self.repository.exists(now_et.date()):
            return "present"
        events = await scan_ranked_events(self.db, policy.stats_window_days, now=now)
        if self.on_progress is not None:
            await self.on_progress(f"labelling {len(events)} scan events")
        payload = await asyncio.to_thread(self._snapshot, events, now)
        await self.repository.record(payload)
        return "recorded"

    def _snapshot(self, events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
        """Blocking (provider reads, pacing sleeps, pandas): always runs in a worker thread."""
        frame = label_journaled(
            events,
            self.bar_source(self.config),
            max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
            cost_bps=DEFAULT_COST_BPS_PER_SIDE,
            now=now,
            max_requests_per_minute=self.config.market_data.max_requests_per_minute,
        )
        if len(frame) and bool((frame["hit"] == FETCH_FAILED_HIT).all()):
            reasons = sorted({str(reason) for reason in frame["reason"].dropna()})[:3]
            raise CardStatsUnavailable(f"every bar fetch failed for {len(frame)} candidates: {'; '.join(reasons)}")
        window_end = now.astimezone(ET_TZ).date()
        return compute_card_stats(
            frame,
            window_start=window_end - timedelta(days=self.config.card_policy.stats_window_days - 1),
            window_end=window_end,
            feed=self.config.market_data.alpaca_feed,
            cost_bps=DEFAULT_COST_BPS_PER_SIDE,
            max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
            code_revision=self.revision,
            now=now,
            events_considered=len(events),
        )
```

`agentic_trader/diagnostics/readiness.py`: add `CARD_STATS = "card_stats"` to `HealthComponent`; add the constructor parameter `card_stats_enabled: bool = False` (after `alpha_registry_report`) stored as `self.card_stats_enabled`; in `report()` after the daily-panel limit:

```python
        if self.card_stats_enabled:
            # Once-daily work: the limit spans weekends and holidays (card_policy.stats_max_age_seconds).
            limits[HealthComponent.CARD_STATS] = self.config.card_policy.stats_max_age_seconds
```

`agentic_trader/agent/copilot.py`: `ReadinessService(...)` gains `card_stats_enabled=True,` (the daemon always runs the worker).

`agentic_trader/cli/commands/service.py`: import `from agentic_trader.research.setups.card_stats_worker import CardStatsWorker`; add after `run_daily_panel_worker`:

```python
async def run_card_stats_worker(copilot, config, readiness) -> None:
    """Persist one ``card_stats`` snapshot per New York date; readiness is worker progress.

    Polls every ``card_policy.stats_poll_seconds``. An idle poll (not yet due, inside a
    suggestion-scan window, or today's snapshot present), the start of a labelling run and a
    recorded snapshot observe ``card_stats`` ready; a failure (for example every bar fetch
    failing) writes nothing and observes it failed, so the previous snapshot stays
    authoritative until it ages out. Labelling runs in a worker thread; the daemon's shutdown
    cancels this task rather than waiting for provider reads.
    """
    component = HealthComponent.CARD_STATS
    shutdown = copilot._shutdown_event
    revision = str((await asyncio.to_thread(runtime_identity))["revision"])
    worker = CardStatsWorker(
        copilot.db,
        copilot.card_stats,
        config,
        revision=revision,
        on_progress=lambda detail: readiness.observe(component, True, detail),
    )
    while not shutdown.is_set():
        try:
            status = await worker.run_once()
            await readiness.observe(component, True, status)
        except Exception as exc:
            logger.exception("Card statistics worker failed", extra={"event": "card_stats_failed"})
            await readiness.observe(component, False, type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=config.card_policy.stats_poll_seconds)
```

In `daemon`, after the `if config.alpha_pipeline.daily_panel.enabled:` block:

```python
    # One card-statistics snapshot per New York date for the cards' evidence block and the card
    # policy (docs/card-evidence.md); cancelled at shutdown, never awaited through provider reads.
    card_stats_task = asyncio.create_task(run_card_stats_worker(copilot, config, copilot.readiness))
```

and in the shutdown block, right after `for task in session_tasks: await task`:

```python
        card_stats_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await card_stats_task
```

`docs/card-evidence.md` — insert directly after `## Configuration` (before `## Evidence block`):

```markdown
## Card statistics snapshot

The daemon task `run_card_stats_worker` (readiness component `card_stats`) polls every
`stats_poll_seconds`. When the New York time is at or after `stats_time_et`, outside every
suggestion-scan window (each `scheduler.suggestion_scan_times_et` slot from 5 minutes before to 20
minutes after), and today's key `card_stats/{et_date}` is missing, it reads every
`scan_candidates_ranked` event of the last `stats_window_days` ET dates
(`research/setups/sources.scan_ranked_events`), labels them in a worker thread with the outcome
labeller's protocol defaults (`label_journaled`: 20 sessions, 5 bp per side, SPY proxy) on the
configured feed, and appends one `card_stats_snapshot` event to stream `card_stats` with key
`card_stats/{et_date}` (idempotent; retention keeps it). It runs every calendar day; a non-trading
day's snapshot simply repeats the previous content.

Payload (`CardStatsSnapshot`, `agentic_trader/research/setups/card_stats.py`):

- provenance: `computed_at`, `window_start`, `window_end` (ET dates), `feed`, `cost_bps_per_side`,
  `max_hold_sessions`, `labeller_protocol` (`setup-outcomes-v1`), `code_revision`,
  `events_considered`, `rows_labelled`;
- `keys`: one row per `(strategy, direction)` plus the aggregate `("*", "*")`, each with `n_mature`,
  `n_immature`, `n_fetch_failed`, `target_rate`, `stop_rate`, `timeout_rate`, `mean_r_cost`,
  `mean_timeout_r`, `median_holding_sessions`, `first_decided_at`, `last_decided_at`. Rates are over
  mature rows; fetch failures are counted and excluded from every rate; rows without a strategy or
  direction count only in the aggregate. Every number is finite or null.

Readiness: every successful poll (detail `not_due`, `scan_window`, `present` or `recorded`) and the
start of a labelling run (`labelling N scan events`) observe `card_stats` ready; a failure observes
it failed with the exception type. If every candidate's bar fetch fails, nothing is written
(`CardStatsUnavailable`) and the previous snapshot stays authoritative until it is older than
`stats_max_age_seconds`, when cards say "statistics stale". Shutdown cancels the task instead of
waiting for a labelling run's provider reads; the next start recomputes. The labeller paces its own
reads at `market_data.max_requests_per_minute`, apart from the scan's read pool, which is why it
avoids the scan windows.
```

`docs/production.md` — new paragraph after the Task 4 "**Card policy (October 8).**" paragraph:

```markdown
**Card statistics worker (October 8).** The daemon runs `card_stats` beside the research workers:
once per New York date after `card_policy.stats_time_et` (08:30) and outside the suggestion-scan
windows, it labels the last 90 days of journaled candidates and appends one `card_stats_snapshot`
event (`copilot db events --stream card_stats`). `/readyz` includes `card_stats` with a four-day
limit, so weekends and holidays stay ready. After a restart on a day without a snapshot the first
poll labels before it reports `recorded`; it observes `labelling N scan events` first. A failed run
(every bar fetch failed) writes nothing and turns `card_stats` not ready; cards keep the previous
snapshot until it is four days old and then say `statistics stale`. Details:
[card evidence](card-evidence.md#card-statistics-snapshot).
```

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_card_stats_worker.py tests/workflows/test_journal_readiness.py tests/agent/test_daily_panel_worker.py tests/cli/test_service_scheduling.py tests/agent/test_account_risk.py`
Expected: PASS (existing readiness tests construct `ReadinessService` without `card_stats_enabled`, so their component sets are unchanged).

- [ ] **Step 5: Regression gate, pre-commit, stage; the controller commits**

```bash
git add agentic_trader/research/setups/card_stats_worker.py agentic_trader/diagnostics/readiness.py \
  agentic_trader/agent/copilot.py agentic_trader/cli/commands/service.py \
  tests/agent/test_card_stats_worker.py tests/workflows/test_journal_readiness.py docs/card-evidence.md docs/production.md
git commit -m "Card statistics worker: daily idempotent card_stats snapshot off the event loop, card_stats readiness

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Optional `next_session_close` validity — helper, WAITING tap, live-card guard, dated rendering

**Files:**
- Create: `tests/market/test_next_regular_close.py`, `tests/agent/test_card_validity.py`
- Modify: `agentic_trader/market/session.py` (new `next_regular_close_after` after `CompositeMarketCalendar`), `agentic_trader/execution/freshness.py` (`CardOutcome.WAITING`, `effective_validity`, `assess_card(validity=)`), `agentic_trader/storage/db.py:717-729` (`live_signal_id`), `agentic_trader/agent/copilot.py` (`LIVE_CARD_PENDING`, `_extends_validity`, send-loop guard and `session_closes` cache, `_card_valid_until(extend=)` at :2771-2803, `_next_open_sentence`, `_assess_card_tap` at :3040-3176), `agentic_trader/notifier/telegram_bot.py` (`_valid_until_text`, `_issued_at`, `issued_at` kwarg on both formatters, `send_signal_alert` reads the row once)
- Modify tests: `tests/execution/test_card_freshness.py` (`_assess` helper; two tests made explicit; new tests), `tests/agent/test_card_freshness_tap.py::test_card_tapped_outside_rth_expires`, `tests/notifier/test_telegram_interactive.py` (new rendering tests)
- Docs: `docs/card-evidence.md` (Card validity), `docs/production.md` (WAITING row, five outcomes, validity paragraph), `docs/risk-policy.md` (tap step 1 and the `valid_until` paragraph)

**Interfaces:**
- Consumes: `CardValidity`, `CardPolicyConfig.validity` (Task 2); `PAPER_PROBE_TAG`.
- Produces:
  - `async next_regular_close_after(calendar: Any, now: datetime, *, horizon_days: int = 10) -> datetime | None` (UTC)
  - `CardOutcome.WAITING == "waiting"`; `effective_validity(validity: CardValidity, *, asset_class: str, policy_locked: bool, probe: bool, drift: bool) -> CardValidity`; `assess_card(..., validity: CardValidity = "session_close")`
  - `SignalDatabase.live_signal_id(contract: str, *, strategy: str | None = None) -> int | None`
  - `TradingCopilot._extends_validity(candidate, *, drift: bool) -> bool`; `_card_valid_until(contract: str, *, extend: bool = False) -> str | None`; `LIVE_CARD_PENDING = "live card pending"`
  - `issued_at: str | None = None` kwarg on `format_alert_card` and `format_terminal_card` (rendering input; no new notification key)

- [ ] **Step 1: Write the failing tests**

`tests/market/test_next_regular_close.py`:

```python
"""The next trading day's regular close: weekends, holidays and early closes from the calendar."""

from datetime import UTC, datetime, timedelta

from agentic_trader.market.session import DeterministicCalendarProvider, MarketCalendarDay, next_regular_close_after


async def test_the_next_close_skips_a_holiday_and_keeps_an_early_close():
    # Wednesday 2026-11-25 15:00 NY: Thanksgiving is closed and Friday closes at 13:00 NY.
    now = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)
    assert await next_regular_close_after(DeterministicCalendarProvider(), now) == datetime(
        2026, 11, 27, 18, 0, tzinfo=UTC
    )


async def test_a_friday_card_is_valid_until_monday_close():
    now = datetime(2026, 10, 9, 18, 35, tzinfo=UTC)  # Friday 14:35 NY
    assert await next_regular_close_after(DeterministicCalendarProvider(), now) == datetime(
        2026, 10, 12, 20, 0, tzinfo=UTC
    )


async def test_a_failing_calendar_or_no_trading_day_gives_none():
    class Failing:
        async def get_calendar_range(self, start, end):
            raise RuntimeError("calendar down")

    class Closed:
        async def get_calendar_range(self, start, end):
            days = (end - start).days + 1
            return [
                MarketCalendarDay(date=start + timedelta(days=i), is_trading_day=False, is_early_close=False)
                for i in range(days)
            ]

    now = datetime(2026, 10, 9, 18, 35, tzinfo=UTC)
    assert await next_regular_close_after(Failing(), now) is None
    assert await next_regular_close_after(Closed(), now) is None
    assert await next_regular_close_after(DeterministicCalendarProvider(), now.replace(tzinfo=None)) is None
```

`tests/execution/test_card_freshness.py` — the helper gains the argument (`validity="session_close"` in `_assess`'s signature, passed as `validity=validity` to `assess_card`), and these existing tests now pass it explicitly:

```python
def test_expired_when_the_session_refuses_entries():
    result = _assess(now=ISSUED_AT, price=LONG_ENTRY, session_open=False, validity="session_close")
    assert result.outcome == CardOutcome.EXPIRED


@pytest.mark.parametrize("enforce_rth", [True, False])
def test_an_extended_hours_session_expires_the_card_only_when_rth_is_enforced(enforce_rth):
    # The caller decides with the shared session rule; ``assess_card`` only applies the verdict.
    session_open = entry_session_open(is_open=True, is_rth=False, enforce_rth=enforce_rth) is None
    result = _assess(now=ISSUED_AT, price=LONG_ENTRY, session_open=session_open, validity="session_close")
    assert (result.outcome == CardOutcome.EXPIRED) is enforce_rth
    if not enforce_rth:
        assert result.outcome == CardOutcome.EXECUTE
```

New tests in the same module (import `effective_validity` alongside the existing imports):

```python
DAY2_CLOSE = datetime(2026, 9, 24, 20, 0, 0, tzinfo=UTC)  # Thu 24 16:00 NY
OVERNIGHT = datetime(2026, 9, 23, 22, 0, 0, tzinfo=UTC)  # 18:00 NY, after the issuing session


def test_a_next_session_card_waits_while_closed_before_valid_until():
    result = _assess(now=OVERNIGHT, valid_until=DAY2_CLOSE, session_open=False, validity="next_session_close")
    assert result.outcome == CardOutcome.WAITING == "waiting"
    assert result.reason == "Market closed; card valid until Thu 24 16:00 NY."
    assert result.price is None and result.r_consumed is None


def test_a_next_session_card_expires_once_valid_until_has_passed():
    now = DAY2_CLOSE + timedelta(seconds=1)
    assert (
        _assess(now=now, valid_until=DAY2_CLOSE, session_open=False, validity="next_session_close").outcome
        == CardOutcome.EXPIRED
    )


def test_session_close_never_waits():
    result = _assess(now=OVERNIGHT, valid_until=DAY2_CLOSE, session_open=False, validity="session_close")
    assert result.outcome == CardOutcome.EXPIRED


def test_without_valid_until_the_legacy_rule_applies():
    result = _assess(now=ISSUED_AT, valid_until=None, session_open=False, validity="next_session_close")
    assert result.outcome == CardOutcome.EXPIRED


@pytest.mark.parametrize("age_hours", [0, 1, 20])
@pytest.mark.parametrize("price", [96.0, 100.0, 103.0, 150.0, 210.0])
def test_validity_changes_nothing_while_the_session_is_open(age_hours, price):
    kwargs = {
        "now": ISSUED_AT + timedelta(hours=age_hours),
        "valid_until": DAY2_CLOSE,
        "price": price,
        "session_open": True,
    }
    assert _assess(**kwargs, validity="next_session_close") == _assess(**kwargs, validity="session_close")


def test_a_day_two_tap_reprices_or_misses_against_the_same_bracket():
    day2 = datetime(2026, 9, 24, 14, 0, 0, tzinfo=UTC)  # 10:00 NY, the next session
    assert (
        _assess(now=day2, valid_until=DAY2_CLOSE, price=103.0, validity="next_session_close").outcome
        == CardOutcome.REPRICE
    )
    assert (
        _assess(now=day2, valid_until=DAY2_CLOSE, price=94.0, validity="next_session_close").outcome
        == CardOutcome.MISSED
    )


def test_the_sweep_keeps_a_next_session_card_overnight():
    provenance = {"valid_until": DAY2_CLOSE.isoformat()}
    assert not card_is_stale(issued_at=ISSUED_AT, decision_provenance=provenance, now=OVERNIGHT)
    assert card_is_stale(issued_at=ISSUED_AT, decision_provenance=provenance, now=DAY2_CLOSE)


@pytest.mark.parametrize(
    ("asset_class", "locked", "probe", "drift", "expected"),
    [
        ("EQUITY", False, False, False, "next_session_close"),
        ("FUTURES", False, False, False, "session_close"),
        ("EQUITY", True, False, False, "session_close"),
        ("EQUITY", False, True, False, "session_close"),
        ("EQUITY", False, False, True, "session_close"),
    ],
)
def test_only_native_unlocked_equity_cards_extend(asset_class, locked, probe, drift, expected):
    assert (
        effective_validity(
            "next_session_close", asset_class=asset_class, policy_locked=locked, probe=probe, drift=drift
        )
        == expected
    )
    assert (
        effective_validity("session_close", asset_class=asset_class, policy_locked=locked, probe=probe, drift=drift)
        == "session_close"
    )
```

`tests/agent/test_card_freshness_tap.py::test_card_tapped_outside_rth_expires` — make the default explicit:

```python
async def test_card_tapped_outside_rth_expires(tap_desk, temp_db, app_config):
    app_config.card_policy.validity = "session_close"
    sid = await record_card(temp_db)
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "Next regular open" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
```

`tests/agent/test_card_validity.py`:

```python
"""next_session_close: recorded close, live-card guard, WAITING taps and dated rendering."""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.config import ScanBudget, load_config
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider, next_regular_close_after
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.models import SignalRecord
from tests.agent.test_card_freshness_tap import record_card, session_info, tap_desk, tap_events  # noqa: F401
from tests.agent.test_scan_budget import budget_desk, candidate  # noqa: F401
from tests.execution.card_stats_fixtures import real_evaluation


@pytest.fixture
def extended(budget_desk, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    today_close = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=3)
    budget_desk.session_provider.get_session_info.return_value = session_info(next_close=today_close)
    budget_desk.session_provider.calendar = DeterministicCalendarProvider()
    budget_desk.today_close = today_close
    return budget_desk


async def _valid_untils(db):
    return [
        datetime.fromisoformat(s["decision_provenance"]["valid_until"]) for s in await db.get_recent_signals(limit=10)
    ]


async def test_native_equity_cards_record_the_next_trading_close(extended, temp_db):
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    expected = await next_regular_close_after(DeterministicCalendarProvider(), datetime.now(UTC))
    untils = await _valid_untils(temp_db)
    assert len(untils) == 2 and all(until == expected for until in untils)
    assert expected.astimezone(ET_TZ).date() > datetime.now(ET_TZ).date()


@pytest.mark.parametrize(
    "change",
    [
        lambda desk, config: setattr(config.card_policy, "validity", "session_close"),
        lambda desk, config: setattr(config.execution.card_freshness, "enabled", False),
        lambda desk, config: setattr(
            desk.session_provider,
            "calendar",
            SimpleNamespace(get_calendar_range=AsyncMock(side_effect=RuntimeError("down"))),
        ),
        lambda desk, config: setattr(
            desk.strategy_engine.scan_contract,
            "side_effect",
            lambda data, **kw: [candidate(data.contract, 0.5).model_copy(update={"probe": True})],
        ),
        lambda desk, config: setattr(
            desk.strategy_engine.scan_contract,
            "side_effect",
            lambda data, **kw: [candidate(data.contract, 0.5).model_copy(update={"alpha_version": "alpha:x:v3"})],
        ),
    ],
    ids=["default", "freshness-off", "calendar-down", "probe", "policy-locked"],
)
async def test_every_other_card_keeps_todays_close(extended, temp_db, app_config, change):
    change(extended, app_config)
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    untils = await _valid_untils(temp_db)
    assert untils and all(until == extended.today_close for until in untils)


async def _plant(db, contract, strategy, when):
    async with db.session_factory() as session, session.begin():
        session.add(
            SignalRecord(
                timestamp=when,
                contract=contract,
                strategy=strategy,
                direction="LONG",
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=104.0,
                risk_dollars=2.0,
                environment=db.environment,
                execution_mode=db.execution_mode,
            )
        )


@pytest.mark.parametrize(
    ("validity", "strategy", "sent"),
    [
        ("next_session_close", "TREND_PULLBACK", ["CCC"]),  # DDD already has a live card for this setup
        ("next_session_close", "SQUEEZE_BREAKOUT", ["DDD"]),  # another strategy's card does not block
        ("session_close", "TREND_PULLBACK", ["DDD"]),  # the default path is unchanged
    ],
)
async def test_live_card_guard(extended, temp_db, app_config, validity, strategy, sent):
    app_config.card_policy.validity = validity
    await _plant(temp_db, "DDD", strategy, datetime.now(UTC) - timedelta(days=1))  # PENDING, outside dedup and today
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    recent = datetime.now(UTC) - timedelta(hours=1)  # signal rows carry naive UTC timestamps
    fresh = [
        s["contract"]
        for s in await temp_db.get_recent_signals(limit=10)
        if datetime.fromisoformat(s["timestamp"]).replace(tzinfo=UTC) > recent
    ]
    assert fresh == sent
    if sent == ["CCC"]:
        reasons = {r["contract"]: r["reason"] for r in extended.last_scan_summary["runners_up"]}
        assert reasons["DDD"] == "live card pending"


async def test_an_extended_card_renders_its_close_date_through_the_real_outbox(extended, temp_db):
    extended.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=5))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    assert re.search(r"• <b>Valid until:</b> [A-Z][a-z]{2} \d{2} \d{2}:\d{2} NY", send.await_args.kwargs["text"])


async def test_a_closed_market_tap_waits_and_leaves_the_card_live(tap_desk, temp_db, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    valid_until = datetime.now(UTC) + timedelta(hours=20)
    sid = await record_card(temp_db, valid_until=valid_until)
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    until = valid_until.astimezone(ET_TZ)
    assert reply == ExecutionReply(
        False,
        f"⏳ Market closed; card valid until {until:%a %d %H:%M} NY. Next regular open 2026-09-24 13:30 UTC.",
        retryable=True,
    )
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "waiting" and event["payload"]["applied"] is False
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_a_policy_locked_card_keeps_todays_rule_when_closed(tap_desk, temp_db, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    sid = await record_card(temp_db, valid_until=datetime.now(UTC) + timedelta(hours=20), alpha_version="alpha:x:v3")
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.retryable is False and (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED


async def test_a_day_two_tap_reprices_and_the_replacement_keeps_the_close(tap_desk, temp_db, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    sid = await record_card(temp_db, age_seconds=20 * 3600, valid_until=datetime.now(UTC) + timedelta(hours=3))
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0

    await tap_desk.execute_signal_by_id(sid)

    old = await temp_db.get_signal_by_id(sid)
    [new] = [s for s in await temp_db.get_recent_signals(limit=10) if s["status"] == SignalStatus.PENDING]
    assert old["status"] == SignalStatus.EXPIRED
    assert new["decision_provenance"]["valid_until"] == old["decision_provenance"]["valid_until"]
```

In `tests/notifier/test_telegram_interactive.py` add (imports: `from sqlalchemy import update`, `from agentic_trader.storage.models import SignalRecord`):

```python
DAY2_UTC = "2026-09-24T20:00:00+00:00"  # Thu 24 16:00 NY


def test_valid_until_names_the_day_when_the_close_is_after_the_issue_date(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK", valid_until=DAY2_UTC, issued_at=FIRST_ISSUED_UTC)
    assert "• <b>Valid until:</b> Thu 24 16:00 NY" in card
    term = format_terminal_card(eval_res, "TREND_PULLBACK", valid_until=DAY2_UTC, issued_at=FIRST_ISSUED_UTC)
    assert "• Valid until:      Thu 24 16:00 NY" in term


def test_valid_until_on_the_issue_date_stays_time_only(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK", valid_until=VALID_UNTIL_UTC, issued_at=FIRST_ISSUED_UTC)
    assert f"• <b>Valid until:</b> {VALID_UNTIL_NY_HHMM} NY" in card


@pytest.mark.asyncio
async def test_send_signal_alert_dates_valid_until_from_the_signal_row(temp_db, eval_res, capsys):
    signal_id = await temp_db.record_signal(
        contract="AAPL",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=190.0,
        stop_loss=186.0,
        take_profit=198.0,
        risk_dollars=100.0,
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
    )
    async with temp_db.session_factory() as session, session.begin():
        await session.execute(
            update(SignalRecord)
            .where(SignalRecord.id == signal_id)
            .values(timestamp=datetime(2026, 9, 23, 18, 35, tzinfo=UTC))
        )
    notifier = TelegramNotifier(bot_token=None, chat_id=None, db=temp_db)
    await notifier.send_signal_alert(eval_res, strategy="TREND_PULLBACK", signal_id=signal_id, valid_until=DAY2_UTC)
    assert "Valid until:      Thu 24 16:00 NY" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/market/test_next_regular_close.py tests/execution/test_card_freshness.py tests/agent/test_card_validity.py tests/notifier/test_telegram_interactive.py`
Expected: FAIL — `ImportError: next_regular_close_after` / `effective_validity`; `TypeError: unexpected keyword argument 'validity'` / `'issued_at'`.

- [ ] **Step 3: Implement**

`agentic_trader/market/session.py`, after `class CompositeMarketCalendar`:

```python
async def next_regular_close_after(calendar: Any, now: datetime, *, horizon_days: int = 10) -> datetime | None:
    """The next trading day's regular close after ``now``'s New York date, in UTC.

    Walks ``calendar.get_calendar_range`` over the following ``horizon_days`` New York dates,
    skipping non-trading days, and returns the first trading day's ``close_time`` (an early close
    included). None when ``now`` is naive, the calendar fails or the horizon holds no trading
    day with a close: callers keep today's close.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        return None
    today = now.astimezone(ET_TZ).date()
    try:
        days = await calendar.get_calendar_range(today + timedelta(days=1), today + timedelta(days=horizon_days))
    except Exception:
        logger.warning("Market calendar unavailable for the next regular close", exc_info=True)
        return None
    for day in sorted(days, key=lambda item: item.date):
        if day.date > today and day.is_trading_day and day.close_time is not None:
            return datetime.combine(day.date, day.close_time, ET_TZ).astimezone(UTC)
    return None
```

`agentic_trader/execution/freshness.py`: imports become `from agentic_trader.config import CardFreshnessConfig, CardValidity` and `from agentic_trader.constants import AssetClass, Direction`; add `WAITING = "waiting"` to `CardOutcome` with the comment "A ``next_session_close`` card tapped while the session refuses entries, before ``valid_until``: retryable, the card stays PENDING."; add:

```python
def effective_validity(
    validity: CardValidity, *, asset_class: str, policy_locked: bool, probe: bool, drift: bool
) -> CardValidity:
    """The validity a card actually gets: ``next_session_close`` only for native, non-policy-locked equity cards.

    A registry alpha or catalog card (``alpha_version``/``alpha_policy``), a paper probe and a
    PEAD drift card keep ``session_close``: admission refuses their original signal after
    ``execution.signal_max_age_seconds`` and a drift card needs its same-session event.
    """
    eligible = str(asset_class).upper() == AssetClass.EQUITY and not (policy_locked or probe or drift)
    return validity if eligible else "session_close"
```

`assess_card` gains the keyword `validity: CardValidity = "session_close"` (last parameter; docstring: "``validity`` is the card's ``effective_validity``; under ``next_session_close`` a closed session before ``valid_until`` is ``WAITING`` rather than ``EXPIRED``.") and, right after `age_seconds = ...`, before the EXPIRED check:

```python
    if (
        validity == "next_session_close"
        and not session_open
        and valid_until is not None
        and now_et < _aware_et(valid_until)
    ):
        reason = f"Market closed; card valid until {_aware_et(valid_until):%a %d %H:%M} NY."
        return CardAssessment(CardOutcome.WAITING, reason, None, age_seconds, None)
```

`agentic_trader/storage/db.py`:

```python
    async def live_signal_id(self, contract: str, *, strategy: str | None = None) -> int | None:
        """The newest PENDING or SUBMITTING signal for ``contract`` (and ``strategy`` when given) in this scope."""
        conditions = [
            *self._scope(),
            SignalRecord.contract == contract,
            SignalRecord.status.in_((SignalStatus.PENDING, SignalStatus.SUBMITTING)),
        ]
        if strategy is not None:
            conditions.append(SignalRecord.strategy == strategy)
        async with self.session_factory() as session:
            return await session.scalar(
                select(SignalRecord.id).where(*conditions).order_by(SignalRecord.id.desc()).limit(1)
            )
```

`agentic_trader/agent/copilot.py`:

- imports: add `effective_validity` to the `agentic_trader.execution.freshness` import and `next_regular_close_after` to the `agentic_trader.market.session` import.
- module constant after `DRIFT_POSITION_CAP_REACHED`:

```python
# A native candidate skipped because its (contract, strategy) already has a live PENDING card; only
# under card_policy.validity == "next_session_close", where a card can outlive the duplicate window.
LIVE_CARD_PENDING = "live card pending"
```

- new method next to `_card_valid_until`:

```python
    def _extends_validity(self, candidate: Any, *, drift: bool) -> bool:
        """Whether this candidate's card is valid until the next session's close (docs/card-evidence.md#card-validity).

        Only with tap re-assessment on: without it, admission's signal-age bound refuses a next-day tap.
        """
        validity = effective_validity(
            self.config.card_policy.validity,
            asset_class=str(getattr(candidate, "asset_class", "")),
            policy_locked=bool(getattr(candidate, "alpha_version", None) or getattr(candidate, "alpha_policy", None)),
            probe=bool(getattr(candidate, "probe", False)),
            drift=drift,
        )
        return self.config.execution.card_freshness.enabled and validity == "next_session_close"
```

- in the send loop's refusal chain, after the `"LLM evaluation budget spent"` branch:

```python
                    elif (
                        not is_drift
                        and self._extends_validity(candidate, drift=False)
                        and await self.db.live_signal_id(candidate.contract, strategy=candidate.strategy) is not None
                    ):
                        # A carried card outlives the duplicate window: one live card per setup.
                        reason = LIVE_CARD_PENDING
```

- the `session_closes` cache becomes `session_closes: dict[tuple[str, bool], str | None] = {}` and the lookup:

```python
extend = self._extends_validity(candidate, drift=is_drift)
if (candidate.contract, extend) not in session_closes:
    session_closes[(candidate.contract, extend)] = await self._card_valid_until(candidate.contract, extend=extend)
valid_until = session_closes[(candidate.contract, extend)]
```

- `_card_valid_until(self, contract: str, *, extend: bool = False)`: docstring gains "With ``extend`` the recorded close is the next trading day's regular close (``next_regular_close_after``); an unavailable calendar keeps today's close."; the final branch becomes:

```python
        now = datetime.now(UTC)
        if not (session_open and isinstance(close, datetime) and close.utcoffset() is not None and close > now):
            return None
        if extend:
            later = await next_regular_close_after(getattr(self.session_provider, "calendar", None), now)
            if later is not None and later > close:
                return later.isoformat()
            logger.warning(
                "Next regular close unavailable for %s; the card keeps today's close",
                contract,
                extra={"event": "card_next_close_unavailable", "contract": contract},
            )
        return close.astimezone(UTC).isoformat()
```

- new static helper next to `_next_open_text`:

```python
    @staticmethod
    def _next_open_sentence(info: Any) -> str:
        """The WAITING reply's tail: the next regular open in UTC, or an explicit unavailability."""
        next_open = getattr(info, "next_open", None)
        if isinstance(next_open, datetime) and next_open.utcoffset() is not None:
            return f"Next regular open {next_open.astimezone(UTC):%Y-%m-%d %H:%M} UTC."
        return "Next regular open unavailable."
```

- `_assess_card_tap`: before `assessment = assess_card(...)`:

```python
        raw_provenance = sig.get("decision_provenance")
        provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
        validity = effective_validity(
            self.config.card_policy.validity,
            asset_class=asset_class,
            policy_locked=policy_locked,
            probe=bool(provenance.get(PAPER_PROBE_TAG)),
            drift=provenance.get("pead_event") is not None,
        )
```

  pass `validity=validity` to `assess_card(...)`, and right after it:

```python
        if assessment.outcome == CardOutcome.WAITING:
            # Nothing changes: the card stays PENDING and the button is restored for a later tap.
            await self._journal_card_tap(signal_id, assessment, tapped_at, applied=False, policy_locked=policy_locked)
            return ExecutionReply(
                False, f"⏳ {html.escape(assessment.reason)} {self._next_open_sentence(info)}", retryable=True
            )
```

`agentic_trader/notifier/telegram_bot.py` (import `UTC` from `datetime`):

```python
def _valid_until_text(valid_until: str | None, issued_at: str | None) -> str | None:
    """``HH:MM NY`` when the close is on the card's issue date (or the issue date is unknown), else ``Www DD HH:MM NY``."""
    until = _ny_datetime(valid_until)
    if until is None:
        return None
    issued = _ny_datetime(issued_at)
    if issued is not None and issued.date() != until.date():
        return f"{until:%a %d %H:%M} NY"
    return f"{until:%H:%M} NY"


def _issued_at(row: dict[str, Any] | None) -> str | None:
    """A signal row's issue time (naive UTC ``YYYY-MM-DD HH:MM:SS``) as an aware ISO string, or None."""
    raw = (row or {}).get("timestamp")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).isoformat()
```

Both formatters gain `issued_at: str | None = None` (after `card_evidence`); `format_alert_card` replaces its `valid_until_hhmm` lines with

```python
    valid_until_text = _valid_until_text(valid_until, issued_at)
    valid_until_line = f"• <b>Valid until:</b> {valid_until_text}\n" if valid_until_text else ""
```

and `format_terminal_card` with

```python
    valid_until_text = _valid_until_text(valid_until, issued_at)
    valid_until_line = f"• Valid until:      {valid_until_text}\n" if valid_until_text else ""
```

`send_signal_alert` reads the row once, before the terminal print, and reuses it for the status guard:

```python
        row = await self.db.get_signal_by_id(signal_id) if self.db else None
        issued_at = _issued_at(row)
```

pass `issued_at=issued_at` to both formatter calls, and replace
`current_status = (await self.db.get_signal_by_id(signal_id) or {}).get("status") if self.db else None`
with `current_status = (row or {}).get("status") if self.db else None`.

`docs/card-evidence.md` — insert before `## Limits`:

```markdown
## Card validity

`card_policy.validity` is `session_close` by default: a card is valid until the close of the session it
was issued in (today's rule). `next_session_close` extends only native, non-policy-locked equity cards
(no `alpha_version`/`alpha_policy`, no paper probe, no PEAD event), and only while
`execution.card_freshness.enabled`: such a card records the next trading day's regular close as
`valid_until` (`market/session.py:next_regular_close_after`, which skips weekends and holidays and keeps
early closes; an unavailable calendar keeps today's close). Every other card keeps today's rule:
admission refuses a policy-locked card's original signal after `execution.signal_max_age_seconds`, and
a drift card needs its same-session event.

- A tap while the session refuses entries and before `valid_until` is `WAITING`: retryable, the card
  stays `PENDING`, the tap is journaled `waiting` (`applied: false`), and the reply is "⏳ Market closed;
  card valid until Www DD HH:MM NY. Next regular open YYYY-MM-DD HH:MM UTC.". After `valid_until` the tap
  is `EXPIRED` as before.
- A next-session tap is never fresh (`fresh_seconds`), so it re-prices or misses at the current price
  against the same stop and target and the required ratio; the re-pricing gate is unchanged, and the
  replacement's new timestamp satisfies admission's signal age.
- `run_scan` skips a native candidate whose `(contract, strategy)` already has a live `PENDING` or
  `SUBMITTING` card (outcome and runner-up reason `live card pending`), so the 12-hour duplicate window
  cannot produce a second live card the next morning. The guard runs only under `next_session_close`.
- `Valid until:` shows `HH:MM NY` when the close is on the card's issue date (the signal row's
  timestamp) and `Www DD HH:MM NY` otherwise.
- Accepted limit: a carried card is a `PENDING` signal from an earlier session, so it does not count in
  the new session's card budget (`signals_since`); the live-card guard bounds it to one card per
  `(contract, strategy)`. Measured statistics never include next-day entries: labels enter at the first
  hourly bar after the scan.
```

`docs/production.md`: change "A tap resolves to exactly one of four outcomes:" to "A tap resolves to exactly one of five outcomes:"; append to the `EXPIRED` row's text: " A `next_session_close` card expires this way only once `valid_until` has passed; before that a closed-session tap is `WAITING`."; add the row after `EXPIRED`:

```markdown
| `WAITING` | Only for a card whose validity is `next_session_close` ([card evidence](card-evidence.md#card-validity)): the session refuses entries but `now < valid_until`. Nothing changes: the card stays `PENDING`, the tap is journaled `waiting`, the reply is "⏳ Market closed; card valid until Www DD HH:MM NY. Next regular open YYYY-MM-DD HH:MM UTC." and Telegram restores the tapped button. |
```

and, after the paragraph ending "…since the others are not reconstructable from the reply alone.":

```markdown
**Card validity option (October 8).** `card_policy.validity: next_session_close` keeps a native,
non-policy-locked equity card valid until the next trading day's regular close (holidays skipped,
early closes kept) instead of today's; the card shows `Valid until: Www DD HH:MM NY`. A tap while the
market is closed then answers `WAITING` and the card stays live; the next session's tap re-prices or
misses as usual, and a scan skips a setup that already has a live card (`live card pending`). The
default `session_close` keeps every existing tap result. See
[card evidence](card-evidence.md#card-validity).
```

`docs/risk-policy.md`, "### Tap and re-pricing" step 1 becomes:

```markdown
1. Session: `assess_card(session_open=entry_session_open(...) is None, validity=effective_validity(...))`.
   A refusal expires the card, except that a `next_session_close` card tapped before its `valid_until`
   is `WAITING` (retryable; the card stays `PENDING`). Card validity is not a risk rule
   ([card evidence](card-evidence.md#card-validity)).
```

and the paragraph beginning "Re-evaluate and `/scan` refuse while…" gains, after "…timezone-aware and still in the future.": " Under `card_policy.validity: next_session_close` a native, non-policy-locked equity card records the next trading day's close instead."

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/market tests/execution tests/agent/test_card_validity.py tests/agent/test_card_freshness_tap.py tests/agent/test_card_expiry_sweep.py tests/agent/test_tap_risk_policy.py tests/notifier tests/storage`
Expected: PASS (every pre-existing tap and freshness test keeps its result under the default `session_close`).

- [ ] **Step 5: Regression gate, pre-commit, stage; the controller commits**

```bash
git add agentic_trader/market/session.py agentic_trader/execution/freshness.py agentic_trader/storage/db.py \
  agentic_trader/agent/copilot.py agentic_trader/notifier/telegram_bot.py \
  tests/market/test_next_regular_close.py tests/agent/test_card_validity.py tests/execution/test_card_freshness.py \
  tests/agent/test_card_freshness_tap.py tests/notifier/test_telegram_interactive.py \
  docs/card-evidence.md docs/production.md docs/risk-policy.md
git commit -m "Card validity option: next_session_close for native equity cards, WAITING taps, live-card guard, dated Valid until

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Cross-cutting docs, full suite and pre-commit

**Files:**
- Modify: `docs/alpha-roadmap.md` (desk direction item 1: PR A status), `docs/card-evidence.md` (proofread; verify every anchor resolves: `#card-validity`, `#send-policy`, `#evidence-block`, `#card-statistics-snapshot`)
- Verify: `CLAUDE.md` contract 8 sentence (Task 4), `docs/production.md` links (Tasks 3–6)

**Interfaces:** none new.

- [ ] **Step 1: Roadmap status**

In `docs/alpha-roadmap.md`, "Desk direction after the fixed-set lane (October 8)", append to ordered item 1 ("Native funnel (now).") as a new indented paragraph:

```markdown
   *Status (October 8):* PR A, honest cards, is implemented ([card evidence](card-evidence.md)): every
   native card states its measured `(strategy, direction)` record, implied EV, sample size and the
   not-validated caveat from a daily `card_stats` snapshot; `card_policy` ships `off` (the operator moves
   it to `preview`, then `enforce`, after reading the evidence); `card_policy.validity:
   next_session_close` is available and off. Bracket construction and book-aware sizing (PR B) remain.
```

- [ ] **Step 2: Anchor and wording check**

Run: `grep -n "card-evidence.md#" docs/*.md CLAUDE.md` and confirm each fragment matches a `## ` heading in `docs/card-evidence.md` (GitHub slugs: "Card validity" → `#card-validity`, "Send policy" → `#send-policy`, "Evidence block" → `#evidence-block`, "Card statistics snapshot" → `#card-statistics-snapshot`). Run `grep -rn "TRADE SIGNAL" agentic_trader tests docs/production.md` and confirm no hit remains outside historical docs.

- [ ] **Step 3: Full suite and pre-commit**

```bash
env -u VIRTUAL_ENV uv run pytest -q
env -u VIRTUAL_ENV uv run pre-commit run --all-files
```

Expected: both clean. No PostgreSQL run is required (no persistence schema change; the new journal kind is a string value in the existing table).

- [ ] **Step 4: Stage; the controller commits**

```bash
git add docs/alpha-roadmap.md docs/card-evidence.md
git commit -m "docs: honest cards status on the roadmap; card evidence contract cross-checked

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### After the plan (controller, not a task)

PR (the v4 results doc and the roadmap direction already on this branch ship in it) → operator merge →
controlled deployment per `docs/production.md` (pause the watchdog, then the daemon; update the installed
checkout; restart) → verify: `/readyz` shows `card_stats` ready (after 08:30 ET the first poll labels,
reporting `labelling N scan events` first), `copilot db events --stream card_stats` shows today's
`card_stats_snapshot`, the next suggestion card reads `📋 SETUP:` with the measured-record block and the
caveat in Telegram and `data/copilot.log`, `copilot cards outcomes --days 30` prints the `card_policy`
block, and `scripts/verify_runtime.py` passes. `card_policy` stays `off`; the operator decides on
`preview` after reading a few days of evidence.

---

## Self-Review

**1. Spec coverage**

| Spec item | Task |
| --- | --- |
| D1 source: `label_journaled` over 90 ET days, protocol defaults, configured feed; helpers moved to `sources.py`, CLI imports them | 1, 5 |
| D1 statistics per key and aggregate, rates over mature, fetch-failed excluded and counted, finite or None | 1 |
| D1 provenance fields (`computed_at` … `rows_labelled`, `labeller_protocol` `setup-outcomes-v1`) | 1 |
| D1 persistence: stream `card_stats`, kind `card_stats_snapshot`, key `card_stats/{et_date}`, locked own transaction, per scope, retained | 1, 3 (`record`), 5 |
| D1 scheduling: poll `stats_poll_seconds`, due `stats_time_et`, catch-up after sleep/restart, `to_thread`, scan-window avoidance −5/+20 | 5 |
| D1 readiness `card_stats`, True on every poll, False on failure, `stats_max_age_seconds` limit; provider failure writes nothing | 5 |
| D1 read side: `CardStatsRepository.latest`, pure `lookup`, injected kwarg, read once per scan | 3 |
| D2 `CardEvidence` fields; transport kwarg on three functions; provenance; `_replacement_card` copy; dry-run print; old payloads | 3 |
| D2 header `📋 SETUP:`; block after the target line in both renderers; measured/insufficient/stale/unavailable wording; italic caveat; implied EV formula | 3 |
| D2 macro invariant after the LLM, no prompt change | 3 |
| D2 HTML escaped, one helper for both renderers | 3 |
| D3 `CardPolicyConfig` table, `extra="forbid"`, `AppConfig` + `load_config`, round-trip test | 2 |
| D3 where (send loop, native only, before LLM, beside budget refusals), pure `decide`, rule, outcome, runner-up reason, fall-through, no budget | 4 |
| D3 journal top-level and per-candidate blocks; provenance `card_policy` + `card_evidence` | 4 |
| D3 labeller `card_policy_withheld`, `would_withhold`, `summarize.card_policy`; existing blocks unchanged; withheld keep being labelled | 4 |
| D3 digest unchanged; preview structured log line | 4 |
| D3 CLAUDE.md contract 8 amendment | 4 |
| D4 `validity` option and scope; `next_regular_close_after` (holiday, early close, None fallback) | 6 |
| D4 WAITING branch and reply; EXPIRED after `valid_until` or under `session_close`; tests updated for explicit `validity` | 6 |
| D4 day-2 taps re-price/miss; live-card guard (`live card pending`); dated rendering; budget limit documented | 6 |
| D5 `docs/card-evidence.md`, `docs/production.md`, `docs/risk-policy.md`, roadmap, CLAUDE.md | 2–7 |
| Testing list (stats, worker, evidence, policy, labeller, validity, regression) | 1–7 |
| Acceptance: default-off changes only text, macro, worker/readiness; `session_close` preserves tap results; full suite + pre-commit | 2 (`test_the_default_policy_leaves_the_scan_unchanged`), 4 (off test), 6 (`test_validity_changes_nothing_while_the_session_is_open`, unchanged tap tests), 7 |

No gaps found.

**2. Placeholder scan.** No "TBD", "TODO", "similar to Task N" or prose-only code steps. Every code step shows the code; every doc step shows the text. Edits to existing functions name the exact line to replace and show the replacement.

**3. Type consistency.** `CardStatsSnapshot.snapshot_key` (property) and `stats(strategy, direction)` are used identically in Tasks 3–5. `lookup(snapshot, strategy, direction, *, now, max_age: timedelta, min_mature)` matches all call sites (`run_scan`, tests). `format_evidence_lines(evidence, rr, *, html)` is called only through `_evidence_block(..., markup=)`. `decide(policy, evidence) -> CardPolicyDecision` and `journal_block(decision | None, mode)` are used by `run_scan`, `_journal_scan_ranking` and the tests with the same names. `CardStatsRepository.latest/exists/record` match `CardStatsWorker` and `FakeCardStats.latest`. `run_card_stats_worker(copilot, config, readiness)` matches the daemon call and the tests. `effective_validity(...)` has the same keyword set in `_extends_validity`, `_assess_card_tap` and the tests. `assess_card(..., validity=)` and `_card_valid_until(contract, *, extend=)` are consistent. `RankedOutcome.CARD_POLICY_WITHHELD`, `HealthComponent.CARD_STATS`, `EventKind.CARD_STATS_SNAPSHOT`, `CardOutcome.WAITING` are each defined once and referenced by value in tests.

**4. Review Focus.** Each of the five lines has its test in the owning task: YAML `off`/`on` (Task 2), provider outage (Task 5), queued/malformed notifications (Task 3), Friday/holiday validity and the weekend WAITING tap (Task 6), shutdown during labelling (Task 5). Other spec-implied inputs are covered by tests inside the tasks rather than listed: snapshot read failure (Task 3), unreadable journal payload (Task 3), stale/insufficient/unavailable never withholding (Task 4), everything withheld still labelled (Task 4), scan-window avoidance (Task 5), policy-locked and probe cards keeping today's rule (Task 6), HTML in the strategy name (Task 3).
