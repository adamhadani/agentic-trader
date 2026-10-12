# Corporate-actions ingestion (desk item 3, part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the paper desk typed, journaled knowledge of corporate actions (splits, dividends, mergers, name changes) from Alpaca, consumed by a preview-mode entry rule, `/positions` notes and the ledger's unsupported-activity text, plus a cached research acquisition for the later event leg.

**Architecture:** A pure model/normaliser and an Alpaca source (`agentic_trader/data/corporate_actions.py`) feed a once-daily daemon worker that appends one capture event per content change on the existing event store (no projection table, no migration). Every consumer reads one frozen `CorporateActionsLookup` built from the latest capture and its freshness; the risk rule lives in `agentic_trader/risk` like every other entry rule.

**Tech Stack:** Python 3.14, pydantic v2 frozen models, alpaca-py 0.44 `CorporateActionsClient` (raw data) behind `BoundedTransport`, SQLAlchemy event store (`WorkflowStore`), click CLI, pytest with the repo's `temp_db`/`app_config` fixtures.

**Spec:** `docs/superpowers/specs/2026-10-11-corporate-actions-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-events` (branch `feature/corporate-actions`).
- No schema migration: head stays `008_alpha_pipeline`. `DomainEventRecord.kind` is a free `String`, so new `EventKind` members need no migration.
- pydantic models use `frozen=True, extra="forbid"`; config classes use `model_config = {"extra": "forbid"}`.
- ruff is strict: no `dict(...)` calls (C408), no imports inside functions (PLC0415; use `TYPE_CHECKING` for type-only imports), no unused unpacked names (RUF059). Run `uv run ruff check` and `uv run ruff format` before each commit; `uv run pre-commit run --files <changed files>` must pass.
- Readiness details, Telegram incidents and journal reasons carry exception **type names only**, never DSNs, URLs or provider text.
- Nothing here touches the broker, the halt state, sizing or admission. `entry_block` ships `preview`.
- Dates in New York: `from agentic_trader.market.session import ET_TZ`; `today = now.astimezone(ET_TZ).date()`.
- Test databases: the `temp_db` fixture (temporary SQLite). Never relax network guards; sources are faked in tests.
- Commit after each task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A symbol with a dot or digit (`BRK.B`, `BF.B`) must survive normalisation, the symbol union and `extra_symbols` validation — Task 1 `test_dotted_symbols_round_trip`, Task 2 config test.
2. An action effective **today** must block at tap time in `enforce` and annotate in `preview` — Task 4 `test_rule_boundaries` (offset 0) and the tap-gate test.
3. YAML `entry_block: off` parses as `False` — Task 2 `test_yaml_off_spelling`.
4. A disabled feature (`enabled: false`) leaves `corporate_actions` null in provenance, renders no card line and adds no `/positions` note — Task 4 `test_disabled_leaves_no_block`, Task 5 `test_notes_absent_when_disabled`.
5. A capture that is present but older than `max_age_seconds` must never refuse an entry and must say `stale` in provenance and `/positions` — Task 4 `test_stale_lookup_never_refuses`, Task 5 `test_stale_capture_adds_one_note`.

---

### Task 1: Model, normaliser, queries and the Alpaca source

**Files:**
- Create: `agentic_trader/data/corporate_actions.py`
- Modify: `agentic_trader/transport/alpaca.py` (add `BoundedCorporateActionsClient` after `BoundedScreenerClient`)
- Test: `tests/data/test_corporate_actions.py`

**Interfaces:**
- Produces: `CorporateActionKind`, `CorporateAction` (fields and methods below), `normalize_actions(data) -> tuple[CorporateAction, ...]`, `CorporateActionsPage`, `CorporateActionsSource` (Protocol), `AlpacaCorporateActionsSource`, `actions_for`, `between`, `blocking_corporate_action`, `relative_days`, `position_note`, `CorporateActionsLookup`.
- Consumes: `agentic_trader.data.pacing.RequestPacer`, `agentic_trader.transport.alpaca.BoundedTransport`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/data/test_corporate_actions.py
"""Typed corporate actions: normalisation of every Alpaca shape, queries, the source and the lookup."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from agentic_trader.data.corporate_actions import (
    AlpacaCorporateActionsSource,
    CorporateAction,
    CorporateActionKind,
    CorporateActionsLookup,
    actions_for,
    between,
    blocking_corporate_action,
    normalize_actions,
    position_note,
    relative_days,
)


# Public market data observed through the Alpaca data API on 2026-10-11 (ids shortened).
RAW = {
    "cash_dividends": [
        {
            "id": "div-aapl-1",
            "corporate_action_type": "cash_dividends",
            "symbol": "AAPL",
            "cusip": "037833100",
            "rate": 0.24,
            "special": False,
            "foreign": False,
            "process_date": "2024-02-15",
            "ex_date": "2024-02-09",
            "record_date": "2024-02-12",
            "payable_date": "2024-02-15",
            "due_bill_on_date": None,
            "due_bill_off_date": None,
        }
    ],
    "forward_splits": [
        {
            "id": "split-nvda",
            "corporate_action_type": "forward_splits",
            "symbol": "NVDA",
            "cusip": "67066G104",
            "new_rate": 10.0,
            "old_rate": 1.0,
            "process_date": "2024-06-10",
            "ex_date": "2024-06-10",
            "record_date": "2024-06-07",
            "payable_date": "2024-06-10",
            "due_bill_redemption_date": "2024-06-10",
        }
    ],
    "cash_mergers": [
        {
            "id": "merger-vzio",
            "corporate_action_type": "cash_mergers",
            "acquirer_symbol": "WMT",
            "acquirer_cusip": "931142103",
            "acquiree_symbol": "VZIO",
            "acquiree_cusip": "92858V101",
            "rate": 11.5,
            "process_date": "2024-12-03",
            "effective_date": "2024-12-03",
            "payable_date": None,
        }
    ],
    "stock_mergers": [
        {
            "id": "merger-pxd",
            "corporate_action_type": "stock_mergers",
            "acquirer_symbol": "XOM",
            "acquirer_cusip": "30231G102",
            "acquirer_rate": 2.3234,
            "acquiree_symbol": "PXD",
            "acquiree_cusip": "723787107",
            "acquiree_rate": 1.0,
            "process_date": "2024-05-03",
            "effective_date": "2024-05-03",
            "payable_date": "2024-05-03",
        }
    ],
}

SYNTHETIC = {
    "reverse_splits": [
        {
            "id": "rs",
            "symbol": "TINY",
            "old_cusip": "1",
            "new_cusip": "2",
            "new_rate": 1.0,
            "old_rate": 10.0,
            "process_date": "2026-01-05",
            "ex_date": "2026-01-05",
        }
    ],
    "unit_splits": [
        {
            "id": "us",
            "old_symbol": "SPACU",
            "old_cusip": "1",
            "old_rate": 1.0,
            "new_symbol": "SPAC",
            "new_cusip": "2",
            "new_rate": 1.0,
            "alternate_symbol": "SPACW",
            "alternate_cusip": "3",
            "alternate_rate": 0.5,
            "process_date": "2026-01-06",
            "effective_date": "2026-01-07",
        }
    ],
    "stock_dividends": [
        {
            "id": "sd",
            "symbol": "SDIV",
            "cusip": "1",
            "rate": 0.05,
            "process_date": "2026-01-08",
            "ex_date": "2026-01-08",
        }
    ],
    "spin_offs": [
        {
            "id": "so",
            "source_symbol": "PARENT",
            "source_cusip": "1",
            "source_rate": 1.0,
            "new_symbol": "CHILD",
            "new_cusip": "2",
            "new_rate": 0.25,
            "process_date": "2026-01-09",
            "ex_date": "2026-01-09",
        }
    ],
    "stock_and_cash_mergers": [
        {
            "id": "scm",
            "acquirer_symbol": "BIG",
            "acquirer_cusip": "1",
            "acquirer_rate": 0.5,
            "acquiree_symbol": "SMALL",
            "acquiree_cusip": "2",
            "acquiree_rate": 1.0,
            "cash_rate": 3.25,
            "process_date": "2026-01-10",
            "effective_date": "2026-01-12",
        }
    ],
    "redemptions": [{"id": "rd", "symbol": "PFD", "cusip": "1", "rate": 25.0, "process_date": "2026-01-13"}],
    "name_changes": [
        {
            "id": "nc",
            "old_symbol": "FB",
            "old_cusip": "1",
            "new_symbol": "META",
            "new_cusip": "2",
            "process_date": "2026-01-14",
        }
    ],
    "worthless_removals": [{"id": "wr", "symbol": "GONE", "cusip": "1", "process_date": "2026-01-15"}],
    "rights_distributions": [
        {
            "id": "rt",
            "source_symbol": "BRK.B",
            "source_cusip": "1",
            "new_symbol": "BRK.R",
            "new_cusip": "2",
            "rate": 0.1,
            "process_date": "2026-01-16",
            "ex_date": "2026-01-16",
            "payable_date": "2026-02-01",
        }
    ],
}


def test_normalises_observed_shapes_sorted_by_effective_date():
    actions = normalize_actions(RAW)
    assert [a.id for a in actions] == ["div-aapl-1", "merger-pxd", "split-nvda", "merger-vzio"]
    div, pxd, nvda, vzio = actions
    assert (div.kind, div.symbol, div.effective_on, div.rate, div.payable_date) == (
        CorporateActionKind.CASH_DIVIDEND,
        "AAPL",
        date(2024, 2, 9),
        0.24,
        date(2024, 2, 15),
    )
    assert (nvda.kind, nvda.effective_on, nvda.old_rate, nvda.new_rate) == (
        CorporateActionKind.FORWARD_SPLIT,
        date(2024, 6, 10),
        1.0,
        10.0,
    )
    assert nvda.price_factor() == pytest.approx(0.1)
    assert (vzio.symbol, vzio.related_symbol, vzio.cash_rate, vzio.effective_on) == (
        "VZIO",
        "WMT",
        11.5,
        date(2024, 12, 3),
    )
    assert (pxd.symbol, pxd.related_symbol, pxd.old_rate, pxd.new_rate) == ("PXD", "XOM", 1.0, 2.3234)
    assert not div.is_structural and nvda.is_structural and vzio.is_structural and pxd.is_structural


def test_normalises_every_other_shape_and_describes_it():
    by_kind = {a.kind: a for a in normalize_actions(SYNTHETIC)}
    assert len(by_kind) == 9
    assert by_kind[CorporateActionKind.REVERSE_SPLIT].describe() == "reverse split 1-for-10"
    assert by_kind[CorporateActionKind.REVERSE_SPLIT].price_factor() == pytest.approx(10.0)
    assert (by_kind[CorporateActionKind.UNIT_SPLIT].symbol, by_kind[CorporateActionKind.UNIT_SPLIT].effective_on) == (
        "SPACU",
        date(2026, 1, 7),
    )
    assert by_kind[CorporateActionKind.STOCK_DIVIDEND].price_factor() == pytest.approx(1 / 1.05)
    assert by_kind[CorporateActionKind.STOCK_DIVIDEND].describe() == "stock dividend 0.05/share"
    assert by_kind[CorporateActionKind.SPIN_OFF].describe() == "spin-off of CHILD (0.25 per 1)"
    assert by_kind[CorporateActionKind.STOCK_AND_CASH_MERGER].describe() == (
        "stock-and-cash merger into BIG (0.5 per 1 plus $3.25)"
    )
    assert by_kind[CorporateActionKind.REDEMPTION].describe() == "redemption at $25.00"
    assert by_kind[CorporateActionKind.NAME_CHANGE].describe() == "name change to META"
    assert by_kind[CorporateActionKind.NAME_CHANGE].effective_on == date(2026, 1, 14)
    assert by_kind[CorporateActionKind.WORTHLESS_REMOVAL].describe() == "worthless removal"
    assert by_kind[CorporateActionKind.RIGHTS_DISTRIBUTION].describe() == "rights distribution of BRK.R (0.1 per share)"
    assert normalize_actions(RAW)[0].describe() == "cash dividend $0.24/share"
    assert normalize_actions(RAW)[2].describe() == "forward split 10-for-1"


def test_dotted_symbols_round_trip():
    action = normalize_actions(SYNTHETIC)[-1]
    assert action.symbol == "BRK.B" and action.related_symbol == "BRK.R"
    assert actions_for(normalize_actions(SYNTHETIC), "brk.b") == (action,)


def test_accepts_date_objects_and_rejects_bad_input():
    typed = {
        "cash_dividends": [{**RAW["cash_dividends"][0], "ex_date": date(2024, 2, 9), "process_date": date(2024, 2, 15)}]
    }
    assert normalize_actions(typed)[0].effective_on == date(2024, 2, 9)
    with pytest.raises(ValueError, match="unknown corporate-action type 'buybacks'"):
        normalize_actions({"buybacks": [{"id": "x", "symbol": "A", "process_date": "2026-01-01"}]})
    with pytest.raises(ValueError, match="forward_split.*ex_date"):
        normalize_actions({"forward_splits": [{**RAW["forward_splits"][0], "ex_date": None}]})
    with pytest.raises(ValueError, match="cash_dividend.*rate"):
        normalize_actions({"cash_dividends": [{k: v for k, v in RAW["cash_dividends"][0].items() if k != "rate"}]})
    with pytest.raises(ValueError, match="cash_dividend.*id"):
        normalize_actions({"cash_dividends": [{k: v for k, v in RAW["cash_dividends"][0].items() if k != "id"}]})
    assert normalize_actions({}) == ()


def _action(kind, symbol, effective, **extra):
    base = {"id": f"{kind}-{symbol}-{effective}", "kind": kind, "symbol": symbol, "effective_on": effective}
    if kind in (CorporateActionKind.FORWARD_SPLIT, CorporateActionKind.REVERSE_SPLIT):
        base.update(old_rate=1.0, new_rate=2.0)
    if kind == CorporateActionKind.CASH_DIVIDEND:
        base.update(rate=0.5)
    return CorporateAction.model_validate({**base, **extra})


TODAY = date(2026, 10, 12)


def test_queries_between_and_blocking():
    split = _action(CorporateActionKind.FORWARD_SPLIT, "NVDA", TODAY + timedelta(days=3))
    old_split = _action(CorporateActionKind.FORWARD_SPLIT, "NVDA", TODAY - timedelta(days=6))
    dividend = _action(CorporateActionKind.CASH_DIVIDEND, "NVDA", TODAY + timedelta(days=1))
    other = _action(CorporateActionKind.FORWARD_SPLIT, "AAPL", TODAY)
    actions = (old_split, dividend, split, other)
    assert between(actions, "NVDA", TODAY, TODAY + timedelta(days=7)) == (dividend, split)
    assert blocking_corporate_action(actions, "NVDA", TODAY, pre_days=7, post_days=5) == split
    assert blocking_corporate_action(actions, "NVDA", TODAY, pre_days=2, post_days=5) is None  # dividend never blocks
    assert blocking_corporate_action(actions, "NVDA", TODAY, pre_days=2, post_days=6) == old_split
    assert blocking_corporate_action(actions, "AAPL", TODAY, pre_days=0, post_days=0) == other
    assert blocking_corporate_action(actions, "MSFT", TODAY, pre_days=30, post_days=30) is None


def test_relative_days_and_position_notes():
    assert relative_days(TODAY, TODAY) == "today"
    assert relative_days(TODAY + timedelta(days=1), TODAY) == "in 1 day"
    assert relative_days(TODAY - timedelta(days=3), TODAY) == "3 days ago"
    dividend = _action(
        CorporateActionKind.CASH_DIVIDEND,
        "XOM",
        TODAY + timedelta(days=2),
        rate=0.99,
        payable_date=TODAY + timedelta(days=30),
    )
    assert (
        position_note(dividend, TODAY) == "XOM: cash dividend $0.99/share, ex 2026-10-14 (in 2 days), pays 2026-11-11."
    )
    split = _action(CorporateActionKind.FORWARD_SPLIT, "NVDA", TODAY - timedelta(days=3), old_rate=1.0, new_rate=10.0)
    assert position_note(split, TODAY) == (
        "NVDA: forward split 10-for-1 effective 2026-10-09 (3 days ago); tracked quantity, stop and target predate it until reconciled."
    )


class FakeClient:
    """A raw-data corporate-actions client: records requests, replays canned pages."""

    def __init__(self, pages_by_chunk, *, fail_on=None):
        self.pages_by_chunk, self.fail_on, self.requests = pages_by_chunk, fail_on, []
        self.observer = None

    @contextmanager
    def observe_responses(self, observer):
        self.observer = observer
        try:
            yield
        finally:
            self.observer = None

    def get_corporate_actions(self, request):
        self.requests.append(request)
        key = tuple(request.symbols)
        if self.fail_on is not None and self.fail_on in key:
            raise RuntimeError("provider down")
        page = self.pages_by_chunk[key]
        self.observer(SimpleNamespace(response={"corporate_actions": page, "next_page_token": None}))
        return page


def test_source_chunks_symbols_digests_pages_and_merges():
    client = FakeClient(
        {
            ("AAPL", "NVDA"): {"cash_dividends": RAW["cash_dividends"], "forward_splits": RAW["forward_splits"]},
            ("PXD",): {"stock_mergers": RAW["stock_mergers"]},
        }
    )
    source = AlpacaCorporateActionsSource(client, chunk_size=2)
    page = source.fetch(["nvda", "AAPL", "PXD", "AAPL"], date(2024, 1, 1), date(2024, 12, 31))
    assert [tuple(r.symbols) for r in client.requests] == [("AAPL", "NVDA"), ("PXD",)]
    assert (client.requests[0].start, client.requests[0].end, client.requests[0].limit) == (
        date(2024, 1, 1),
        date(2024, 12, 31),
        1000,
    )
    assert [a.id for a in page.actions] == ["div-aapl-1", "merger-pxd", "split-nvda"]
    assert page.pages == 2 and len(page.page_sha256) == 2 and len(set(page.page_sha256)) == 2
    assert page.symbols == 3 and page.window == (date(2024, 1, 1), date(2024, 12, 31))
    assert page.requested_at <= page.received_at


def test_source_fails_whole_fetch_on_a_failed_chunk_and_skips_empty_requests():
    client = FakeClient({("AAPL",): {}}, fail_on="PXD")
    source = AlpacaCorporateActionsSource(client, chunk_size=1)
    with pytest.raises(RuntimeError):
        source.fetch(["AAPL", "PXD"], date(2024, 1, 1), date(2024, 1, 31))
    empty = source.fetch([], date(2024, 1, 1), date(2024, 1, 31))
    assert empty.actions == () and empty.pages == 0 and len(client.requests) == 2


def test_lookup_freshness():
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    actions = [a.model_dump(mode="json") for a in normalize_actions(RAW)]
    capture = {"content_sha256": "abc", "as_of": "2026-10-12", "actions": actions}
    check = {"checked_at": (now - timedelta(hours=1)).isoformat(), "as_of": "2026-10-12", "content_sha256": "abc"}
    fresh = CorporateActionsLookup.build(capture, check, now=now, max_age_seconds=3600 * 4)
    assert fresh.status == "ok" and fresh.as_of == date(2026, 10, 12) and len(fresh.actions) == 4
    stale = CorporateActionsLookup.build(
        capture, {**check, "checked_at": (now - timedelta(days=5)).isoformat()}, now=now, max_age_seconds=3600
    )
    assert stale.status == "stale" and stale.as_of == date(2026, 10, 12) and stale.actions == ()
    assert CorporateActionsLookup.build(None, None, now=now, max_age_seconds=3600).status == "unavailable"
    assert (
        CorporateActionsLookup.build(
            capture, {**check, "content_sha256": "other"}, now=now, max_age_seconds=3600
        ).status
        == "unavailable"
    )
    future = {**check, "checked_at": (now + timedelta(hours=1)).isoformat()}
    assert CorporateActionsLookup.build(capture, future, now=now, max_age_seconds=3600).status == "stale"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/data/test_corporate_actions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.data.corporate_actions'`

- [ ] **Step 3: Add the bounded client**

In `agentic_trader/transport/alpaca.py`, import `from alpaca.data.historical.corporate_actions import CorporateActionsClient` beside the other SDK client imports and append after `BoundedScreenerClient`:

```python
class BoundedCorporateActionsClient(BoundedTransport, CorporateActionsClient):
    """Historical corporate actions (``/v1/corporate-actions``) under the same socket bounds."""
```

Add the name to the module's `__all__` if one exists.

- [ ] **Step 4: Write the module**

```python
# agentic_trader/data/corporate_actions.py
"""Typed corporate actions from Alpaca's ``/v1/corporate-actions`` (docs/corporate-actions.md).

One normalised ``CorporateAction`` covers the provider's thirteen shapes. ``effective_on`` is the
first session whose prices reflect the action: the ex-date for splits, dividends, spin-offs and
rights; the effective date for mergers and unit splits; the process date for name changes,
redemptions and worthless removals. The provider's ``start``/``end`` filter matches
``process_date`` only (measured 2026-10-11), which is the payable date for cash dividends.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from alpaca.data.requests import CorporateActionsRequest
from pydantic import BaseModel, Field

from agentic_trader.data.pacing import RequestPacer
from agentic_trader.transport.alpaca import BoundedCorporateActionsClient


__all__ = [
    "AlpacaCorporateActionsSource",
    "CorporateAction",
    "CorporateActionKind",
    "CorporateActionsLookup",
    "CorporateActionsPage",
    "CorporateActionsSource",
    "LookupStatus",
    "actions_for",
    "between",
    "blocking_corporate_action",
    "normalize_actions",
    "position_note",
    "relative_days",
]

LookupStatus = Literal["ok", "stale", "unavailable"]


class CorporateActionKind(StrEnum):
    FORWARD_SPLIT = "forward_split"
    REVERSE_SPLIT = "reverse_split"
    UNIT_SPLIT = "unit_split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPIN_OFF = "spin_off"
    CASH_MERGER = "cash_merger"
    STOCK_MERGER = "stock_merger"
    STOCK_AND_CASH_MERGER = "stock_and_cash_merger"
    REDEMPTION = "redemption"
    NAME_CHANGE = "name_change"
    WORTHLESS_REMOVAL = "worthless_removal"
    RIGHTS_DISTRIBUTION = "rights_distribution"


# kind -> (symbol field, related-symbol field, effective-date field) in the provider's record.
_SHAPES: dict[CorporateActionKind, tuple[str, str | None, str]] = {
    CorporateActionKind.FORWARD_SPLIT: ("symbol", None, "ex_date"),
    CorporateActionKind.REVERSE_SPLIT: ("symbol", None, "ex_date"),
    CorporateActionKind.UNIT_SPLIT: ("old_symbol", "new_symbol", "effective_date"),
    CorporateActionKind.CASH_DIVIDEND: ("symbol", None, "ex_date"),
    CorporateActionKind.STOCK_DIVIDEND: ("symbol", None, "ex_date"),
    CorporateActionKind.SPIN_OFF: ("source_symbol", "new_symbol", "ex_date"),
    CorporateActionKind.CASH_MERGER: ("acquiree_symbol", "acquirer_symbol", "effective_date"),
    CorporateActionKind.STOCK_MERGER: ("acquiree_symbol", "acquirer_symbol", "effective_date"),
    CorporateActionKind.STOCK_AND_CASH_MERGER: ("acquiree_symbol", "acquirer_symbol", "effective_date"),
    CorporateActionKind.REDEMPTION: ("symbol", None, "process_date"),
    CorporateActionKind.NAME_CHANGE: ("old_symbol", "new_symbol", "process_date"),
    CorporateActionKind.WORTHLESS_REMOVAL: ("symbol", None, "process_date"),
    CorporateActionKind.RIGHTS_DISTRIBUTION: ("source_symbol", "new_symbol", "ex_date"),
}
# kind -> {model field: provider field} for the required rates.
_RATES: dict[CorporateActionKind, dict[str, str]] = {
    CorporateActionKind.FORWARD_SPLIT: {"old_rate": "old_rate", "new_rate": "new_rate"},
    CorporateActionKind.REVERSE_SPLIT: {"old_rate": "old_rate", "new_rate": "new_rate"},
    CorporateActionKind.UNIT_SPLIT: {"old_rate": "old_rate", "new_rate": "new_rate"},
    CorporateActionKind.CASH_DIVIDEND: {"rate": "rate"},
    CorporateActionKind.STOCK_DIVIDEND: {"rate": "rate"},
    CorporateActionKind.SPIN_OFF: {"old_rate": "source_rate", "new_rate": "new_rate"},
    CorporateActionKind.CASH_MERGER: {"cash_rate": "rate"},
    CorporateActionKind.STOCK_MERGER: {"old_rate": "acquiree_rate", "new_rate": "acquirer_rate"},
    CorporateActionKind.STOCK_AND_CASH_MERGER: {
        "old_rate": "acquiree_rate",
        "new_rate": "acquirer_rate",
        "cash_rate": "cash_rate",
    },
    CorporateActionKind.REDEMPTION: {"cash_rate": "rate"},
    CorporateActionKind.NAME_CHANGE: {},
    CorporateActionKind.WORTHLESS_REMOVAL: {},
    CorporateActionKind.RIGHTS_DISTRIBUTION: {"rate": "rate"},
}
_PLURAL = {f"{kind.value}s": kind for kind in CorporateActionKind}
_OPTIONAL_DATES = ("record_date", "payable_date", "process_date")


class CorporateAction(BaseModel, frozen=True, extra="forbid"):
    id: str = Field(min_length=1)
    kind: CorporateActionKind
    symbol: str = Field(min_length=1)
    related_symbol: str | None = None
    effective_on: date
    record_date: date | None = None
    payable_date: date | None = None
    process_date: date | None = None
    rate: float | None = None
    old_rate: float | None = None
    new_rate: float | None = None
    cash_rate: float | None = None
    special: bool = False
    foreign: bool = False
    source: Literal["alpaca"] = "alpaca"

    @property
    def is_structural(self) -> bool:
        """Everything but a cash dividend changes the instrument or its price scale."""
        return self.kind != CorporateActionKind.CASH_DIVIDEND

    def price_factor(self) -> float | None:
        """Multiply pre-action prices by this to compare them with post-action prices."""
        if self.kind in (CorporateActionKind.FORWARD_SPLIT, CorporateActionKind.REVERSE_SPLIT):
            assert self.old_rate is not None and self.new_rate is not None
            return self.old_rate / self.new_rate
        if self.kind == CorporateActionKind.STOCK_DIVIDEND:
            assert self.rate is not None
            return 1.0 / (1.0 + self.rate)
        return None

    def describe(self) -> str:
        kind, related = self.kind, self.related_symbol or "?"
        if kind in (CorporateActionKind.FORWARD_SPLIT, CorporateActionKind.REVERSE_SPLIT):
            label = "forward split" if kind == CorporateActionKind.FORWARD_SPLIT else "reverse split"
            return f"{label} {self.new_rate:g}-for-{self.old_rate:g}"
        if kind == CorporateActionKind.UNIT_SPLIT:
            return f"unit split into {related} ({self.new_rate:g} per {self.old_rate:g})"
        if kind == CorporateActionKind.CASH_DIVIDEND:
            return f"cash dividend ${self.rate:.2f}/share" + (" (special)" if self.special else "")
        if kind == CorporateActionKind.STOCK_DIVIDEND:
            return f"stock dividend {self.rate:g}/share"
        if kind == CorporateActionKind.SPIN_OFF:
            return f"spin-off of {related} ({self.new_rate:g} per {self.old_rate:g})"
        if kind == CorporateActionKind.CASH_MERGER:
            by = f" by {related}" if self.related_symbol else ""
            return f"cash merger{by} at ${self.cash_rate:.2f}"
        if kind == CorporateActionKind.STOCK_MERGER:
            return f"stock merger into {related} ({self.new_rate:g} per {self.old_rate:g})"
        if kind == CorporateActionKind.STOCK_AND_CASH_MERGER:
            return f"stock-and-cash merger into {related} ({self.new_rate:g} per {self.old_rate:g} plus ${self.cash_rate:.2f})"
        if kind == CorporateActionKind.REDEMPTION:
            return f"redemption at ${self.cash_rate:.2f}"
        if kind == CorporateActionKind.NAME_CHANGE:
            return f"name change to {related}"
        if kind == CorporateActionKind.RIGHTS_DISTRIBUTION:
            return f"rights distribution of {related} ({self.rate:g} per share)"
        return "worthless removal"

    def summary(self) -> dict[str, Any]:
        """The journal/card view: identity, dates and the operator text; never the raw record."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "symbol": self.symbol,
            "related_symbol": self.related_symbol,
            "effective_on": self.effective_on.isoformat(),
            "payable_date": self.payable_date.isoformat() if self.payable_date else None,
            "describe": self.describe(),
        }


def _as_date(value: Any, *, kind: CorporateActionKind, field: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    raise ValueError(f"corporate action {kind.value}: missing or invalid {field}")


def _optional_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _normalize_one(kind: CorporateActionKind, raw: Mapping[str, Any]) -> CorporateAction:
    symbol_field, related_field, effective_field = _SHAPES[kind]
    identity = raw.get("id")
    if not isinstance(identity, str) or not identity:
        raise ValueError(f"corporate action {kind.value}: missing id")
    symbol = raw.get(symbol_field)
    if not isinstance(symbol, str) or not symbol:
        raise ValueError(f"corporate action {kind.value}: missing {symbol_field}")
    rates: dict[str, float] = {}
    for model_field, raw_field in _RATES[kind].items():
        value = raw.get(raw_field)
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"corporate action {kind.value}: missing or invalid {raw_field}")
        rates[model_field] = float(value)
    related = raw.get(related_field) if related_field else None
    return CorporateAction(
        id=identity,
        kind=kind,
        symbol=symbol.upper(),
        related_symbol=str(related).upper() if related else None,
        effective_on=_as_date(raw.get(effective_field), kind=kind, field=effective_field),
        special=bool(raw.get("special", False)),
        foreign=bool(raw.get("foreign", False)),
        **{name: _optional_date(raw.get(name)) for name in _OPTIONAL_DATES},
        **rates,
    )


def normalize_actions(data: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[CorporateAction, ...]:
    """The provider's ``{plural type: [records]}`` payload as sorted typed actions; fails closed on any record."""
    actions: list[CorporateAction] = []
    for key, rows in data.items():
        kind = _PLURAL.get(key)
        if kind is None:
            raise ValueError(f"unknown corporate-action type {key!r}")
        actions.extend(_normalize_one(kind, row) for row in rows)
    return tuple(sorted(actions, key=lambda a: (a.effective_on, a.symbol, a.id)))


def actions_for(actions: Sequence[CorporateAction], symbol: str) -> tuple[CorporateAction, ...]:
    wanted = symbol.strip().upper()
    return tuple(a for a in actions if a.symbol == wanted)


def between(actions: Sequence[CorporateAction], symbol: str, start: date, end: date) -> tuple[CorporateAction, ...]:
    """The symbol's actions with ``start <= effective_on <= end``, in effective-date order."""
    return tuple(a for a in actions_for(actions, symbol) if start <= a.effective_on <= end)


def blocking_corporate_action(
    actions: Sequence[CorporateAction], symbol: str, today: date, *, pre_days: int, post_days: int
) -> CorporateAction | None:
    """The first structural action effective within ``[today - post_days, today + pre_days]``; dividends never block."""
    window = between(actions, symbol, today - timedelta(days=post_days), today + timedelta(days=pre_days))
    return next((a for a in window if a.is_structural), None)


def relative_days(effective_on: date, today: date) -> str:
    days = (effective_on - today).days
    if days == 0:
        return "today"
    unit = "day" if abs(days) == 1 else "days"
    return f"in {days} {unit}" if days > 0 else f"{-days} {unit} ago"


def position_note(action: CorporateAction, today: date) -> str:
    """One ``/positions`` note for a held symbol's action."""
    when = f"{action.effective_on.isoformat()} ({relative_days(action.effective_on, today)})"
    if not action.is_structural:
        pays = f", pays {action.payable_date.isoformat()}" if action.payable_date else ""
        return f"{action.symbol}: {action.describe()}, ex {when}{pays}."
    return (
        f"{action.symbol}: {action.describe()} effective {when}; "
        "tracked quantity, stop and target predate it until reconciled."
    )


@dataclass(frozen=True)
class CorporateActionsPage:
    actions: tuple[CorporateAction, ...]
    requested_at: datetime
    received_at: datetime
    symbols: int
    window: tuple[date, date]
    pages: int
    page_sha256: tuple[str, ...]


class CorporateActionsSource(Protocol):
    def fetch(self, symbols: Sequence[str], start: date, end: date) -> CorporateActionsPage: ...


def _page_digest(response: Any) -> str:
    return hashlib.sha256(json.dumps(response, sort_keys=True, default=str).encode()).hexdigest()


class AlpacaCorporateActionsSource:
    """Blocking source over a raw-data ``BoundedCorporateActionsClient``; callers offload it to a thread.

    Symbols are de-duplicated, upper-cased and requested in chunks; the SDK paginates each chunk.
    A failed chunk fails the whole fetch: a capture is complete or it does not exist.
    """

    def __init__(
        self,
        client: BoundedCorporateActionsClient,
        *,
        pacer: RequestPacer | None = None,
        chunk_size: int = 100,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.client, self.pacer, self.chunk_size, self.clock = client, pacer, chunk_size, clock

    def fetch(self, symbols: Sequence[str], start: date, end: date) -> CorporateActionsPage:
        ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
        requested_at = self.clock()
        digests: list[str] = []
        merged: dict[str, list[Mapping[str, Any]]] = {}

        def observe(page: Any) -> None:
            digests.append(_page_digest(page.response))

        for index in range(0, len(ordered), self.chunk_size):
            chunk = ordered[index : index + self.chunk_size]
            if self.pacer is not None:
                self.pacer.acquire()
            request = CorporateActionsRequest(symbols=chunk, start=start, end=end, limit=1000)
            with self.client.observe_responses(observe):
                data = self.client.get_corporate_actions(request)
            if not isinstance(data, Mapping):
                raise TypeError("Corporate-actions response is not a mapping; construct the client with raw_data=True")
            for key, rows in data.items():
                merged.setdefault(key, []).extend(rows)
        return CorporateActionsPage(
            actions=normalize_actions(merged),
            requested_at=requested_at,
            received_at=self.clock(),
            symbols=len(ordered),
            window=(start, end),
            pages=len(digests),
            page_sha256=tuple(digests),
        )


@dataclass(frozen=True)
class CorporateActionsLookup:
    """What one scan or report sees: the latest capture's actions and whether it is fresh."""

    status: LookupStatus
    as_of: date | None
    actions: tuple[CorporateAction, ...]

    @staticmethod
    def build(
        capture: Mapping[str, Any] | None, check: Mapping[str, Any] | None, *, now: datetime, max_age_seconds: int
    ) -> CorporateActionsLookup:
        if capture is None or check is None or check.get("content_sha256") != capture.get("content_sha256"):
            return CorporateActionsLookup("unavailable", None, ())
        as_of = date.fromisoformat(str(capture["as_of"]))
        age = (now - datetime.fromisoformat(str(check["checked_at"]))).total_seconds()
        if not 0 <= age <= max_age_seconds:
            return CorporateActionsLookup("stale", as_of, ())
        actions = tuple(CorporateAction.model_validate(row) for row in capture["actions"])
        return CorporateActionsLookup("ok", as_of, actions)
```

Add `from datetime import UTC, date, datetime, timedelta` (the `timedelta` import is used by `blocking_corporate_action`).

- [ ] **Step 5: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/data/test_corporate_actions.py tests/transport -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run ruff check agentic_trader/data/corporate_actions.py agentic_trader/transport/alpaca.py tests/data/test_corporate_actions.py && env -u VIRTUAL_ENV uv run ruff format agentic_trader/data/corporate_actions.py tests/data/test_corporate_actions.py
git add agentic_trader/data/corporate_actions.py agentic_trader/transport/alpaca.py tests/data/test_corporate_actions.py
git commit -m "Corporate actions: typed model, normaliser, queries and bounded Alpaca source

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Config, event kinds and the capture store

**Files:**
- Modify: `agentic_trader/config.py` (new `CorporateActionsConfig` after `BookSizingConfig`; new `AppConfig.corporate_actions` field beside `book_sizing`)
- Modify: `agentic_trader/execution/durable.py:32-62` (`EventKind` gains `CORPORATE_ACTIONS_CAPTURED = "corporate_actions_captured"` and `CORPORATE_ACTIONS_CHECKED = "corporate_actions_checked"`)
- Create: `agentic_trader/storage/corporate_actions.py`
- Test: `tests/config/test_corporate_actions_config.py`, `tests/storage/test_corporate_actions_store.py`

**Interfaces:**
- Consumes: Task 1 `CorporateAction`, `CorporateActionsPage`, `CorporateActionsLookup`; `agentic_trader.storage.workflow.WorkflowStore` (`events(stream, limit=)`, `lock(session, resource=)`, `append(session, *, stream, kind, payload, key=)`, `encode`).
- Produces: `CorporateActionsConfig` (fields in the spec §3.4 plus `max_requests_per_minute`), `AppConfig.corporate_actions`, `build_capture(page, *, as_of, symbols) -> dict`, `CorporateActionsStore(store, *, max_actions)` with `latest_capture()`, `latest_check()`, `record(capture) -> bool`, `lookup(*, now, max_age_seconds) -> CorporateActionsLookup`; stream names `CAPTURE_STREAM = "corporate-actions/capture"`, `CHECK_STREAM = "corporate-actions/check"`.

- [ ] **Step 1: Write the failing config tests**

```python
# tests/config/test_corporate_actions_config.py
import pytest
from pydantic import ValidationError

from agentic_trader.config import AppConfig, CorporateActionsConfig


def test_defaults_ship_enabled_in_preview():
    policy = AppConfig().corporate_actions
    assert policy.enabled is True and policy.entry_block == "preview"
    assert (policy.pre_days, policy.post_days, policy.notes_horizon_days) == (7, 5, 10)
    assert (policy.lookback_days, policy.lookahead_days) == (45, 120)
    assert (policy.max_symbols, policy.chunk_size, policy.max_actions) == (600, 100, 5000)
    assert (policy.poll_seconds, policy.max_age_seconds, policy.max_requests_per_minute) == (300, 345600, 60)
    assert policy.extra_symbols == []


def test_yaml_off_spelling():
    assert CorporateActionsConfig(entry_block=False).entry_block == "off"
    with pytest.raises(ValidationError):
        CorporateActionsConfig(entry_block=True)


@pytest.mark.parametrize("symbols", [["AAPL", "aapl"], ["BAD SYMBOL"], ["BTC/USD"], [""]])
def test_extra_symbols_must_be_distinct_upper_case_equity_symbols(symbols):
    with pytest.raises(ValidationError):
        CorporateActionsConfig(extra_symbols=symbols)


def test_dotted_symbols_and_ordering_rules():
    assert CorporateActionsConfig(extra_symbols=["BRK.B", "BF.B"]).extra_symbols == ["BRK.B", "BF.B"]
    with pytest.raises(ValidationError, match="max_age_seconds must exceed poll_seconds"):
        CorporateActionsConfig(poll_seconds=600, max_age_seconds=600)
    with pytest.raises(ValidationError):
        CorporateActionsConfig(unknown=1)
```

- [ ] **Step 2: Write the failing store tests**

```python
# tests/storage/test_corporate_actions_store.py
"""Capture store: one capture event per content change, one check per run, bounded, fresh lookups."""

from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.corporate_actions import CAPTURE_STREAM, CHECK_STREAM, CorporateActionsStore, build_capture
from agentic_trader.storage.workflow import WorkflowStore
from tests.data.test_corporate_actions import RAW


NOW = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


def page(data, *, received=NOW):
    return CorporateActionsPage(
        actions=normalize_actions(data),
        requested_at=received - timedelta(seconds=2),
        received_at=received,
        symbols=3,
        window=(date(2026, 8, 28), date(2027, 2, 9)),
        pages=1,
        page_sha256=("deadbeef",),
    )


@pytest.fixture
async def store(temp_db):
    return CorporateActionsStore(WorkflowStore(temp_db), max_actions=5)


def test_build_capture_is_deterministic_and_counts_kinds():
    first = build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL", "NVDA", "PXD", "VZIO"])
    again = build_capture(
        page(RAW, received=NOW + timedelta(hours=1)), as_of=date(2026, 10, 13), symbols=["VZIO", "AAPL", "NVDA", "PXD"]
    )
    assert first["content_sha256"] == again["content_sha256"]  # content, not clock or order
    assert first["symbols_sha256"] == again["symbols_sha256"]
    assert first["counts_by_kind"] == {"cash_dividend": 1, "cash_merger": 1, "forward_split": 1, "stock_merger": 1}
    assert first["window"] == ["2026-08-28", "2027-02-09"] and first["as_of"] == "2026-10-12"
    assert first["captured_at"] == NOW.isoformat() and first["pages"] == 1 and first["page_sha256"] == ["deadbeef"]
    assert [a["id"] for a in first["actions"]] == ["div-aapl-1", "merger-pxd", "split-nvda", "merger-vzio"]


async def test_record_appends_capture_once_per_content_and_a_check_every_run(store, temp_db):
    capture = build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL"])
    assert await store.record(capture) is True
    same_day = build_capture(page(RAW, received=NOW + timedelta(hours=2)), as_of=date(2026, 10, 12), symbols=["AAPL"])
    assert await store.record(same_day) is False
    captures = await temp_db.workflows.events(CAPTURE_STREAM)
    checks = await temp_db.workflows.events(CHECK_STREAM)
    assert (
        len(captures) == 1
        and captures[0]["kind"] == EventKind.CORPORATE_ACTIONS_CAPTURED == "corporate_actions_captured"
    )
    assert len(checks) == 2 and checks[-1]["kind"] == EventKind.CORPORATE_ACTIONS_CHECKED
    assert (await store.latest_check())["checked_at"] == same_day["captured_at"]
    assert (await store.latest_capture())["content_sha256"] == capture["content_sha256"]


async def test_changed_content_appends_and_a_return_to_old_content_appends_again(store):
    a = build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL"])
    b = build_capture(
        page({"forward_splits": RAW["forward_splits"]}, received=NOW + timedelta(days=1)),
        as_of=date(2026, 10, 13),
        symbols=["AAPL"],
    )
    a_again = build_capture(page(RAW, received=NOW + timedelta(days=2)), as_of=date(2026, 10, 14), symbols=["AAPL"])
    assert [await store.record(c) for c in (a, b, a_again)] == [True, True, True]
    assert (await store.latest_capture())["as_of"] == "2026-10-14"


async def test_capture_bound_fails_closed(store):
    many = {"cash_dividends": [{**RAW["cash_dividends"][0], "id": f"d{i}", "symbol": f"S{i}"} for i in range(6)]}
    with pytest.raises(ValueError, match="exceeds 5 actions"):
        await store.record(build_capture(page(many), as_of=date(2026, 10, 12), symbols=["AAPL"]))
    assert await store.latest_capture() is None and await store.latest_check() is None


async def test_lookup_reads_capture_and_check(store):
    assert (await store.lookup(now=NOW, max_age_seconds=3600)).status == "unavailable"
    await store.record(build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL"]))
    fresh = await store.lookup(now=NOW + timedelta(minutes=5), max_age_seconds=3600)
    assert fresh.status == "ok" and len(fresh.actions) == 4 and fresh.as_of == date(2026, 10, 12)
    assert (await store.lookup(now=NOW + timedelta(days=5), max_age_seconds=3600)).status == "stale"
```

- [ ] **Step 3: Run both test files to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/config/test_corporate_actions_config.py tests/storage/test_corporate_actions_store.py -q`
Expected: FAIL with `ImportError` (no `CorporateActionsConfig`, no `agentic_trader.storage.corporate_actions`).

- [ ] **Step 4: Add the config class and field**

In `agentic_trader/config.py`, after `BookSizingConfig`:

```python
EntryBlockMode = Literal["off", "preview", "enforce"]


class CorporateActionsConfig(BaseModel):
    """Corporate-action ingestion and its consumers (docs/corporate-actions.md).

    The daemon captures the desk's symbols once per New York date over a rolling
    ``process_date`` window. ``entry_block`` ships ``preview``: a structural action (anything but a
    cash dividend) effective within ``post_days`` behind or ``pre_days`` ahead is journaled on the
    card and shown as a line; ``enforce`` refuses the card. Stale or missing evidence never refuses.
    """

    model_config = {"extra": "forbid"}

    enabled: bool = True
    entry_block: EntryBlockMode = "preview"
    pre_days: int = Field(default=7, ge=0, le=60)
    post_days: int = Field(default=5, ge=0, le=60)
    notes_horizon_days: int = Field(default=10, ge=0, le=90)
    lookback_days: int = Field(default=45, ge=1, le=365)
    lookahead_days: int = Field(default=120, ge=1, le=365)
    max_symbols: int = Field(default=600, ge=1, le=2000)
    chunk_size: int = Field(default=100, ge=1, le=500)
    max_actions: int = Field(default=5000, ge=1)
    poll_seconds: int = Field(default=300, ge=10, le=3600)
    max_age_seconds: int = Field(default=345600, gt=0)
    max_requests_per_minute: int = Field(default=60, ge=1, le=200)
    extra_symbols: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("entry_block", mode="before")
    @classmethod
    def yaml_off(cls, value: Any) -> Any:
        # YAML 1.1 reads a bare `off` as False; that spelling means "off". `on`/True stays invalid.
        return "off" if value is False else value

    @field_validator("extra_symbols")
    @classmethod
    def equity_symbols(cls, symbols: list[str]) -> list[str]:
        if len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", s) for s in symbols):
            raise ValueError("corporate_actions.extra_symbols must be distinct upper-case equity symbols")
        return symbols

    @model_validator(mode="after")
    def age_exceeds_poll(self) -> CorporateActionsConfig:
        if self.max_age_seconds <= self.poll_seconds:
            raise ValueError("corporate_actions.max_age_seconds must exceed poll_seconds")
        return self
```

In `AppConfig`, directly after `book_sizing: BookSizingConfig = Field(default_factory=BookSizingConfig)` add
`corporate_actions: CorporateActionsConfig = Field(default_factory=CorporateActionsConfig)`.

- [ ] **Step 5: Add the event kinds**

In `agentic_trader/execution/durable.py`, after `PEAD_DECISION = "pead_decision"`:

```python
    CORPORATE_ACTIONS_CAPTURED = "corporate_actions_captured"
    CORPORATE_ACTIONS_CHECKED = "corporate_actions_checked"
```

- [ ] **Step 6: Write the store**

```python
# agentic_trader/storage/corporate_actions.py
"""Corporate-action captures on the event store: one capture per content change, one check per run."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from agentic_trader.data.corporate_actions import CorporateActionsLookup, CorporateActionsPage
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.workflow import WorkflowStore, encode


__all__ = ["CAPTURE_STREAM", "CHECK_STREAM", "CorporateActionsStore", "build_capture"]

CAPTURE_STREAM = "corporate-actions/capture"
CHECK_STREAM = "corporate-actions/check"


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def build_capture(page: CorporateActionsPage, *, as_of: date, symbols: Sequence[str]) -> dict[str, Any]:
    """The journal payload for one fetch; ``content_sha256`` covers the sorted actions only."""
    actions = [action.model_dump(mode="json") for action in page.actions]
    counts = Counter(action.kind.value for action in page.actions)
    return {
        "captured_at": page.received_at.isoformat(),
        "requested_at": page.requested_at.isoformat(),
        "as_of": as_of.isoformat(),
        "symbols_count": len(symbols),
        "symbols_sha256": _sha256(sorted({s.upper() for s in symbols})),
        "window": [page.window[0].isoformat(), page.window[1].isoformat()],
        "actions": actions,
        "counts_by_kind": dict(sorted(counts.items())),
        "content_sha256": _sha256(actions),
        "pages": page.pages,
        "page_sha256": list(page.page_sha256),
    }


class CorporateActionsStore:
    def __init__(self, store: WorkflowStore, *, max_actions: int = 5000):
        if max_actions < 1:
            raise ValueError("max_actions must be positive")
        self.store, self.max_actions = store, max_actions

    async def _latest(self, stream: str) -> dict[str, Any] | None:
        rows = await self.store.events(stream, limit=1)
        return rows[0]["payload"] if rows else None

    async def latest_capture(self) -> dict[str, Any] | None:
        return await self._latest(CAPTURE_STREAM)

    async def latest_check(self) -> dict[str, Any] | None:
        return await self._latest(CHECK_STREAM)

    async def record(self, capture: dict[str, Any]) -> bool:
        """Append the check always and the capture only when its content changed; True when it did."""
        if len(capture["actions"]) > self.max_actions:
            raise ValueError(f"Corporate-action capture exceeds {self.max_actions} actions")
        latest = await self.latest_capture()
        changed = latest is None or latest["content_sha256"] != capture["content_sha256"]
        check = {
            "checked_at": capture["captured_at"],
            "as_of": capture["as_of"],
            "content_sha256": capture["content_sha256"],
            "symbols_count": capture["symbols_count"],
            "pages": capture["pages"],
            "changed": changed,
        }
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="corporate-actions")
            if changed:
                await self.store.append(
                    session,
                    stream=CAPTURE_STREAM,
                    kind=EventKind.CORPORATE_ACTIONS_CAPTURED,
                    payload=capture,
                    key=f"corporate-actions/capture/{capture['as_of']}/{capture['content_sha256']}",
                )
            await self.store.append(
                session, stream=CHECK_STREAM, kind=EventKind.CORPORATE_ACTIONS_CHECKED, payload=check
            )
        return changed

    async def lookup(self, *, now: datetime, max_age_seconds: int) -> CorporateActionsLookup:
        return CorporateActionsLookup.build(
            await self.latest_capture(), await self.latest_check(), now=now, max_age_seconds=max_age_seconds
        )
```

- [ ] **Step 7: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/config/test_corporate_actions_config.py tests/storage/test_corporate_actions_store.py tests/config -q`
Expected: all PASS (the existing config tests still pass with the new field).

- [ ] **Step 8: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run ruff check agentic_trader/config.py agentic_trader/execution/durable.py agentic_trader/storage/corporate_actions.py tests/config/test_corporate_actions_config.py tests/storage/test_corporate_actions_store.py && env -u VIRTUAL_ENV uv run ruff format agentic_trader/storage/corporate_actions.py tests/config/test_corporate_actions_config.py tests/storage/test_corporate_actions_store.py
git add agentic_trader/config.py agentic_trader/execution/durable.py agentic_trader/storage/corporate_actions.py tests/config/test_corporate_actions_config.py tests/storage/test_corporate_actions_store.py
git commit -m "Corporate actions: config, event kinds and the capture store

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Daemon worker, service wiring, readiness component

**Files:**
- Create: `agentic_trader/data/corporate_actions_worker.py`
- Modify: `agentic_trader/diagnostics/readiness.py:17-50,71-90` (`HealthComponent.CORPORATE_ACTIONS = "corporate_actions"`, `corporate_actions_enabled` flag, age limit)
- Modify: `agentic_trader/cli/commands/service.py` (new `run_corporate_actions_worker`, started beside `card_stats_task`)
- Modify: `agentic_trader/agent/copilot.py` (constructor: `corporate_actions` store attribute and kwarg; `_corporate_actions_lookup` helper; pass `corporate_actions_enabled` where `ReadinessService(` is constructed — find it with `grep -rn "ReadinessService(" agentic_trader`)
- Test: `tests/agent/test_corporate_actions_worker.py`, append to `tests/workflows/test_journal_readiness.py`

**Interfaces:**
- Consumes: Task 1 `AlpacaCorporateActionsSource`, `CorporateActionsSource`, `CorporateActionsLookup`; Task 2 `CorporateActionsStore`, `build_capture`, `AppConfig.corporate_actions`; `in_scan_window` from `agentic_trader.research.setups.card_stats_worker`.
- Produces: `CorporateActionsWorker(source, store, config, *, symbols, on_progress=None)` with `run_once(now=None) -> str` in `{"scan_window", "present", "captured", "unchanged"}`; `TradingCopilot.corporate_actions: CorporateActionsStore`; `TradingCopilot._corporate_actions_lookup(now) -> CorporateActionsLookup | None` (None when disabled); `service.run_corporate_actions_worker(copilot, config, readiness)`; `service.corporate_action_symbols(copilot, config) -> list[str]`.

- [ ] **Step 1: Write the failing worker tests**

```python
# tests/agent/test_corporate_actions_worker.py
"""Once per New York date, outside scan windows, bounded symbols, fail-closed captures, readiness."""

from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from agentic_trader.data.corporate_actions_worker import CorporateActionsWorker
from agentic_trader.storage.corporate_actions import CAPTURE_STREAM, CHECK_STREAM, CorporateActionsStore
from agentic_trader.storage.workflow import WorkflowStore
from tests.data.test_corporate_actions import RAW


DUE = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)  # Monday 09:00 New York
SCAN = datetime(2026, 10, 12, 14, 40, tzinfo=UTC)  # 10:40 New York, inside the 10:35 window


class FakeSource:
    def __init__(self, data=RAW, *, fail=False):
        self.data, self.fail, self.calls = data, fail, []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        if self.fail:
            raise ConnectionError("provider down")
        now = datetime.now(UTC)
        return CorporateActionsPage(normalize_actions(self.data), now, now, len(symbols), (start, end), 1, ("p",))


def worker_for(db, config, source=None, symbols=("NVDA", "AAPL"), **kwargs):
    source = source or FakeSource()
    store = CorporateActionsStore(WorkflowStore(db), max_actions=config.corporate_actions.max_actions)

    async def provide():
        return list(symbols)

    return CorporateActionsWorker(source, store, config, symbols=provide, **kwargs), source, store


async def test_captures_once_per_date_with_the_process_date_window(temp_db, app_config):
    worker, source, store = worker_for(temp_db, app_config)
    assert await worker.run_once(DUE) == "captured"
    assert source.calls == [(("AAPL", "NVDA"), date(2026, 8, 28), date(2027, 2, 9))]  # today-45 .. today+120
    assert await worker.run_once(DUE + timedelta(hours=3)) == "present"
    assert len(source.calls) == 1
    assert len(await temp_db.workflows.events(CAPTURE_STREAM)) == 1
    capture = await store.latest_capture()
    assert capture["as_of"] == "2026-10-12" and capture["symbols_count"] == 2


async def test_unchanged_content_next_day_records_only_a_check(temp_db, app_config):
    worker, source, store = worker_for(temp_db, app_config)
    assert await worker.run_once(DUE) == "captured"
    assert await worker.run_once(DUE + timedelta(days=1)) == "unchanged"
    assert len(source.calls) == 2
    assert len(await temp_db.workflows.events(CAPTURE_STREAM)) == 1
    assert len(await temp_db.workflows.events(CHECK_STREAM)) == 2
    assert (await store.latest_check())["as_of"] == "2026-10-13"


async def test_restart_on_a_checked_date_is_present(temp_db, app_config):
    first, _, _ = worker_for(temp_db, app_config)
    assert await first.run_once(DUE) == "captured"
    restarted, source, _ = worker_for(temp_db, app_config)
    assert await restarted.run_once(DUE + timedelta(hours=1)) == "present" and source.calls == []


async def test_scan_window_skips_without_reading(temp_db, app_config):
    worker, source, _ = worker_for(temp_db, app_config)
    assert await worker.run_once(SCAN) == "scan_window" and source.calls == []


async def test_failure_records_nothing_and_retries(temp_db, app_config):
    worker, source, store = worker_for(temp_db, app_config, source=FakeSource(fail=True))
    with pytest.raises(ConnectionError):
        await worker.run_once(DUE)
    assert await store.latest_check() is None
    source.fail = False
    assert await worker.run_once(DUE + timedelta(minutes=5)) == "captured"


async def test_symbol_cap_fails_closed_and_symbols_are_normalised(temp_db, app_config):
    config = app_config.model_copy(
        update={"corporate_actions": app_config.corporate_actions.model_copy(update={"max_symbols": 2})}
    )
    worker, source, _ = worker_for(temp_db, config, symbols=("nvda", "AAPL", "", "NVDA"))
    assert await worker.run_once(DUE) == "captured" and source.calls[0][0] == ("AAPL", "NVDA")
    worker, source, _ = worker_for(temp_db, config, symbols=("A", "B", "C"))
    with pytest.raises(ValueError, match="exceed max_symbols 2"):
        await worker.run_once(DUE + timedelta(days=1))
    assert source.calls == []


async def test_progress_callback_reports_the_symbol_count(temp_db, app_config):
    seen = []

    async def progress(detail):
        seen.append(detail)

    worker, _, _ = worker_for(temp_db, app_config, on_progress=progress)
    await worker.run_once(DUE)
    assert seen == ["capturing 2 symbols"]
```

- [ ] **Step 2: Write the failing readiness test** (append to `tests/workflows/test_journal_readiness.py`, mirroring the `card_stats` test just above it)

```python
@pytest.mark.parametrize("mode", ["disabled", "missing", "fresh", "long-weekend", "stale", "failure"])
async def test_corporate_actions_readiness_spans_a_long_weekend(store, app_config, mode):
    readiness = ReadinessService(
        store, app_config, MetricsCollector(), run_id="current", corporate_actions_enabled=mode != "disabled"
    )
    if mode not in ("disabled", "missing"):
        await store.record_health(HealthComponent.CORPORATE_ACTIONS, mode != "failure", run_id="current")
    offset = {"long-weekend": timedelta(days=3, hours=23), "stale": timedelta(days=4, seconds=1)}.get(mode, timedelta())
    checks = (await readiness.report(now=datetime.now(UTC) + offset))["checks"]
    if mode == "disabled":
        assert "corporate_actions" not in checks
    else:
        assert checks["corporate_actions"]["ready"] == (mode in ("fresh", "long-weekend"))
        assert checks["corporate_actions"]["max_age_seconds"] == 345600
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_corporate_actions_worker.py tests/workflows/test_journal_readiness.py -q`
Expected: FAIL (`ModuleNotFoundError`, `AttributeError: CORPORATE_ACTIONS`, unexpected keyword `corporate_actions_enabled`).

- [ ] **Step 4: Write the worker**

```python
# agentic_trader/data/corporate_actions_worker.py
"""The daemon's once-daily corporate-action capture (docs/corporate-actions.md#daemon-worker).

Each poll asks whether this is outside every suggestion-scan window and whether today's New York
date has been checked (in memory, or by the latest journaled check). When neither holds it fetches
the desk's symbols over ``[today - lookback_days, today + lookahead_days]`` on ``process_date`` in a
worker thread and records the capture. A failed fetch records nothing; the next poll retries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats_worker import in_scan_window
from agentic_trader.storage.corporate_actions import build_capture


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.data.corporate_actions import CorporateActionsSource
    from agentic_trader.storage.corporate_actions import CorporateActionsStore

__all__ = ["CorporateActionsWorker"]


class CorporateActionsWorker:
    def __init__(
        self,
        source: CorporateActionsSource,
        store: CorporateActionsStore,
        config: AppConfig,
        *,
        symbols: Callable[[], Awaitable[Sequence[str]]],
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.source, self.store, self.config = source, store, config
        self.symbols, self.on_progress = symbols, on_progress
        self._checked: date | None = None

    async def run_once(self, now: datetime | None = None) -> str:
        """One poll: ``scan_window``, ``present``, ``captured`` or ``unchanged``; raises on a failed run."""
        now = now or datetime.now(UTC)
        now_et = now.astimezone(ET_TZ)
        today = now_et.date()
        if in_scan_window(now_et, self.config.scheduler.suggestion_scan_times_et):
            return "scan_window"
        if self._checked == today:
            return "present"
        check = await self.store.latest_check()
        if check is not None and check.get("as_of") == today.isoformat():
            self._checked = today
            return "present"
        policy = self.config.corporate_actions
        symbols = sorted({s.strip().upper() for s in await self.symbols() if s and s.strip()})
        if len(symbols) > policy.max_symbols:
            raise ValueError(f"Corporate-action symbols {len(symbols)} exceed max_symbols {policy.max_symbols}")
        if self.on_progress is not None:
            await self.on_progress(f"capturing {len(symbols)} symbols")
        start = today - timedelta(days=policy.lookback_days)
        end = today + timedelta(days=policy.lookahead_days)
        page = await asyncio.to_thread(self.source.fetch, symbols, start, end)
        changed = await self.store.record(build_capture(page, as_of=today, symbols=symbols))
        self._checked = today
        return "captured" if changed else "unchanged"
```

- [ ] **Step 5: Readiness component**

In `agentic_trader/diagnostics/readiness.py`: add `CORPORATE_ACTIONS = "corporate_actions"` to `HealthComponent` after `CARD_STATS`; add the constructor parameter `corporate_actions_enabled: bool = False` after `card_stats_enabled` and store it as `self.corporate_actions_enabled`; in `report`, after the `card_stats` limit block add:

```python
        if self.corporate_actions_enabled:
            # Once-daily work: the limit spans weekends and holidays (corporate_actions.max_age_seconds).
            limits[HealthComponent.CORPORATE_ACTIONS] = self.config.corporate_actions.max_age_seconds
```

Where `ReadinessService(` is constructed with `card_stats_enabled=...` (grep), pass
`corporate_actions_enabled=corporate_actions_live(config)` using this helper in `agentic_trader/config.py` next to `CorporateActionsConfig`:

```python
def corporate_actions_live(config: AppConfig) -> bool:
    """The capture worker runs only with the feature on and Alpaca data credentials present."""
    return config.corporate_actions.enabled and bool(config.alpaca_api_key and config.alpaca_api_secret)
```

(Place it after the `AppConfig` class definition so the annotation resolves; `from __future__ import annotations` is already in effect in `config.py` — verify, otherwise quote the annotation.)

- [ ] **Step 6: Copilot store and lookup**

In `TradingCopilot.__init__`, add the keyword parameter `corporate_actions: CorporateActionsStore | None = None` beside `card_stats`, and directly after the `self.card_stats = …` line:

```python
        # Corporate-action captures (docs/corporate-actions.md); the daemon's worker writes, scans and reports read.
        self.corporate_actions = (
            corporate_actions
            if corporate_actions is not None
            else CorporateActionsStore(self.db.workflows, max_actions=config.corporate_actions.max_actions)
        )
```

Add this method near `_earnings_blackout_reason`:

```python
    async def _corporate_actions_lookup(self, now: datetime) -> CorporateActionsLookup | None:
        """The latest capture as one frozen read; None when the feature is off; a read failure is ``unavailable``."""
        policy = self.config.corporate_actions
        if not policy.enabled:
            return None
        try:
            return await self.corporate_actions.lookup(now=now, max_age_seconds=policy.max_age_seconds)
        except Exception:
            logger.exception("Corporate-action lookup failed", extra={"event": "corporate_actions_lookup_failed"})
            return CorporateActionsLookup("unavailable", None, ())
```

Imports: `from agentic_trader.data.corporate_actions import CorporateActionsLookup` and `from agentic_trader.storage.corporate_actions import CorporateActionsStore`.

- [ ] **Step 7: Service wiring**

In `agentic_trader/cli/commands/service.py`, after `run_card_stats_worker`:

```python
async def corporate_action_symbols(copilot, config) -> list[str]:
    """The configured universe, tracked equity positions and the configured extras, de-duplicated."""
    positions = await copilot.db.get_active_positions()
    held = {str(p["contract"]).strip().upper() for p in positions if not str(p["contract"]).startswith("/")}
    return sorted(set(config.universe.symbols) | held | set(config.corporate_actions.extra_symbols))


async def run_corporate_actions_worker(copilot, config, readiness) -> None:
    """Capture corporate actions once per New York date; readiness is worker progress.

    Every successful poll (idle or not) observes ``corporate_actions`` ready with the worker's
    status; a failed run records nothing and observes it failed with the exception type name
    only, so the previous capture stays authoritative until it ages out. Only shutdown ends the loop.
    """
    component = HealthComponent.CORPORATE_ACTIONS
    policy = config.corporate_actions
    shutdown = copilot._shutdown_event
    client = BoundedCorporateActionsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    source = AlpacaCorporateActionsSource(
        client, pacer=RequestPacer(policy.max_requests_per_minute), chunk_size=policy.chunk_size
    )

    async def progress(detail: str) -> None:
        await readiness.observe(component, True, detail)

    worker = CorporateActionsWorker(
        source,
        copilot.corporate_actions,
        config,
        symbols=lambda: corporate_action_symbols(copilot, config),
        on_progress=progress,
    )
    while not shutdown.is_set():
        try:
            status = await worker.run_once()
            await readiness.observe(component, True, status)
        except Exception as exc:
            logger.exception("Corporate-actions worker failed", extra={"event": "corporate_actions_failed"})
            try:
                await readiness.observe(component, False, type(exc).__name__)
            except Exception:
                logger.exception(
                    "Corporate-actions readiness could not be recorded",
                    extra={"event": "corporate_actions_readiness_failed"},
                )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=policy.poll_seconds)
```

Imports at the top of `service.py`: `from agentic_trader.config import corporate_actions_live`, `from agentic_trader.data.corporate_actions import AlpacaCorporateActionsSource`, `from agentic_trader.data.corporate_actions_worker import CorporateActionsWorker`, `from agentic_trader.data.pacing import RequestPacer`, `from agentic_trader.transport.alpaca import BoundedCorporateActionsClient`.

Where the daemon creates `card_stats_task = asyncio.create_task(run_card_stats_worker(...))`, add immediately after:

```python
    corporate_actions_task = (
        asyncio.create_task(run_corporate_actions_worker(copilot, config, copilot.readiness))
        if corporate_actions_live(config)
        else None
    )
```

Then mirror every later use of `card_stats_task` (cancellation/await at shutdown) for `corporate_actions_task`, guarding `None`.

- [ ] **Step 8: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_corporate_actions_worker.py tests/workflows/test_journal_readiness.py tests/agent/test_card_stats_worker.py tests/cli -q`
Expected: all PASS.

- [ ] **Step 9: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run ruff check agentic_trader tests/agent/test_corporate_actions_worker.py tests/workflows/test_journal_readiness.py && env -u VIRTUAL_ENV uv run ruff format agentic_trader/data/corporate_actions_worker.py agentic_trader/cli/commands/service.py agentic_trader/diagnostics/readiness.py agentic_trader/agent/copilot.py tests/agent/test_corporate_actions_worker.py
env -u VIRTUAL_ENV uv run mypy agentic_trader/data/corporate_actions_worker.py agentic_trader/cli/commands/service.py
git add agentic_trader/data/corporate_actions_worker.py agentic_trader/diagnostics/readiness.py agentic_trader/cli/commands/service.py agentic_trader/agent/copilot.py agentic_trader/config.py tests/agent/test_corporate_actions_worker.py tests/workflows/test_journal_readiness.py
git commit -m "Corporate actions: once-daily capture worker, daemon wiring and readiness component

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Entry rule, evaluator gate, tap gate, provenance and card line

**Files:**
- Modify: `agentic_trader/risk/rules.py` (`RiskRule.CORPORATE_ACTION`, `corporate_action_block`), `agentic_trader/risk/__init__.py` (export)
- Modify: `agentic_trader/agent/evaluator.py` (`LLMTradeEvaluation.corporate_actions` field; `evaluate_candidate(..., corporate_actions=None)`; step 2c)
- Modify: `agentic_trader/agent/copilot.py` (build the lookup once per scan and pass it; provenance key; `_corporate_action_reason`; tap gate)
- Modify: `agentic_trader/notifier/telegram_bot.py` (`_corporate_action_line`, rendered on both cards)
- Test: `tests/risk/test_rules.py` (append), `tests/agent/test_evaluator.py` (append), `tests/notifier/test_corporate_action_line.py`, `tests/agent/test_card_freshness_tap.py` (append one tap test; read that file's existing fixtures first and mirror the nearest earnings-blackout tap test)

**Interfaces:**
- Consumes: Task 1 `CorporateAction`, `CorporateActionKind`, `CorporateActionsLookup`, `blocking_corporate_action`, `between`, `relative_days`; Task 3 `TradingCopilot._corporate_actions_lookup`.
- Produces: `corporate_action_block(actions, symbol, today, *, pre_days, post_days) -> Rejection | None`; `RiskRule.CORPORATE_ACTION = "corporate_action"`; `LLMTradeEvaluation.corporate_actions: dict[str, Any] | None` with keys `status`, `mode`, `as_of`, `would_block`, `action`, `reason`, `note`; `telegram_bot._corporate_action_line(block, *, markup) -> str`.

- [ ] **Step 1: Write the failing rule tests** (append to `tests/risk/test_rules.py`; add `corporate_action_block` to its import list and `from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind`)

```python
def _corporate(kind, symbol, effective, **extra):
    base = {"id": f"{kind}-{symbol}-{effective}", "kind": kind, "symbol": symbol, "effective_on": effective}
    if kind in (CorporateActionKind.FORWARD_SPLIT, CorporateActionKind.REVERSE_SPLIT):
        base.update(old_rate=1.0, new_rate=10.0)
    if kind == CorporateActionKind.CASH_DIVIDEND:
        base.update(rate=0.5)
    return CorporateAction.model_validate({**base, **extra})


CA_TODAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    ("offset", "blocks"),
    [(-6, False), (-5, True), (0, True), (7, True), (8, False)],
)
def test_corporate_action_rule_boundaries(offset, blocks):
    split = _corporate(CorporateActionKind.FORWARD_SPLIT, "NVDA", CA_TODAY + timedelta(days=offset))
    rejection = corporate_action_block((split,), "nvda", CA_TODAY, pre_days=7, post_days=5)
    assert (rejection is not None) is blocks
    if rejection is not None:
        assert rejection.rule == RiskRule.CORPORATE_ACTION == "corporate_action"
        assert str(rejection).startswith("Corporate action: NVDA forward split 10-for-1 effective ")
        assert str(rejection).endswith("— within the 7/5-day blackout.")


def test_corporate_action_rule_text_and_dividends():
    split = _corporate(CorporateActionKind.FORWARD_SPLIT, "NVDA", CA_TODAY + timedelta(days=3))
    assert str(corporate_action_block((split,), "NVDA", CA_TODAY, pre_days=7, post_days=5)) == (
        "Corporate action: NVDA forward split 10-for-1 effective 2026-10-15 (in 3 days) — within the 7/5-day blackout."
    )
    dividend = _corporate(CorporateActionKind.CASH_DIVIDEND, "NVDA", CA_TODAY)
    assert corporate_action_block((dividend,), "NVDA", CA_TODAY, pre_days=7, post_days=5) is None
    assert corporate_action_block((), "NVDA", CA_TODAY, pre_days=7, post_days=5) is None
    assert corporate_action_block((split,), "AAPL", CA_TODAY, pre_days=7, post_days=5) is None
```

- [ ] **Step 2: Write the failing evaluator tests** (append to `tests/agent/test_evaluator.py`; import `CorporateAction, CorporateActionKind, CorporateActionsLookup` from `agentic_trader.data.corporate_actions`)

```python
def _lookup(*actions, status="ok", as_of=None):
    return CorporateActionsLookup(status, as_of or datetime.now(UTC).date(), tuple(actions))


def _split(symbol="AAPL", days=3):
    effective = datetime.now(UTC).astimezone(ET_TZ).date() + timedelta(days=days)
    return CorporateAction(
        id=f"split-{symbol}",
        kind=CorporateActionKind.FORWARD_SPLIT,
        symbol=symbol,
        effective_on=effective,
        old_rate=1.0,
        new_rate=4.0,
    )


def _dividend(symbol="AAPL", days=2):
    effective = datetime.now(UTC).astimezone(ET_TZ).date() + timedelta(days=days)
    return CorporateAction(
        id=f"div-{symbol}", kind=CorporateActionKind.CASH_DIVIDEND, symbol=symbol, effective_on=effective, rate=0.25
    )


def _with_mode(config, mode):
    return config.model_copy(
        update={"corporate_actions": config.corporate_actions.model_copy(update={"entry_block": mode})}
    )


async def test_corporate_action_preview_annotates_without_refusing(config, evaluator_factory):
    evaluator = evaluator_factory(config=_with_mode(config, "preview"))
    eval_res = await evaluator.evaluate_candidate(
        create_equity_candidate(), use_llm=False, corporate_actions=_lookup(_split())
    )
    assert eval_res.approved is True
    block = eval_res.corporate_actions
    assert (block["status"], block["mode"], block["would_block"]) == ("ok", "preview", True)
    assert block["action"]["describe"] == "forward split 4-for-1" and block["reason"].startswith(
        "Corporate action: AAPL"
    )
    assert block["note"] is None


async def test_corporate_action_enforce_refuses(config, evaluator_factory):
    evaluator = evaluator_factory(config=_with_mode(config, "enforce"))
    eval_res = await evaluator.evaluate_candidate(
        create_equity_candidate(), use_llm=False, corporate_actions=_lookup(_split())
    )
    assert eval_res.approved is False
    assert eval_res.rejection_rule == RiskRule.CORPORATE_ACTION
    assert eval_res.rejection_reason.startswith("Corporate action: AAPL forward split 4-for-1 effective ")
    assert eval_res.corporate_actions["would_block"] is True


async def test_dividend_within_pre_days_is_a_note_not_a_block(config, evaluator_factory):
    evaluator = evaluator_factory(config=_with_mode(config, "enforce"))
    eval_res = await evaluator.evaluate_candidate(
        create_equity_candidate(), use_llm=False, corporate_actions=_lookup(_dividend())
    )
    assert eval_res.approved is True
    block = eval_res.corporate_actions
    assert block["would_block"] is False and block["action"] is None
    assert block["note"]["describe"] == "cash dividend $0.25/share"


@pytest.mark.parametrize("status", ["stale", "unavailable"])
async def test_stale_lookup_never_refuses(config, evaluator_factory, status):
    evaluator = evaluator_factory(config=_with_mode(config, "enforce"))
    eval_res = await evaluator.evaluate_candidate(
        create_equity_candidate(), use_llm=False, corporate_actions=_lookup(_split(), status=status)
    )
    assert eval_res.approved is True
    assert eval_res.corporate_actions == {"status": status, "mode": "enforce"}


async def test_disabled_leaves_no_block(config, evaluator_factory):
    off = evaluator_factory(config=_with_mode(config, "off"))
    eval_res = await off.evaluate_candidate(
        create_equity_candidate(), use_llm=False, corporate_actions=_lookup(_split())
    )
    assert eval_res.approved is True and eval_res.corporate_actions is None
    none = evaluator_factory(config=_with_mode(config, "enforce"))
    eval_res = await none.evaluate_candidate(create_equity_candidate(), use_llm=False, corporate_actions=None)
    assert eval_res.approved is True and eval_res.corporate_actions is None


async def test_other_symbols_actions_do_not_touch_the_candidate(config, evaluator_factory):
    evaluator = evaluator_factory(config=_with_mode(config, "enforce"))
    eval_res = await evaluator.evaluate_candidate(
        create_equity_candidate(), use_llm=False, corporate_actions=_lookup(_split("NVDA"))
    )
    assert eval_res.approved is True and eval_res.corporate_actions["would_block"] is False
```

Check how `evaluator_factory` takes `config`: it closes over the `config` fixture and builds `RiskEvaluator(config, **overrides)`. If `build(**overrides)` has no `config` override, extend the factory: `def build(config=config, **overrides): … RiskEvaluator(config, **{…})`.

- [ ] **Step 3: Write the failing card-line tests**

```python
# tests/notifier/test_corporate_action_line.py
"""The corporate-action card line: preview and enforce blocks, dividend notes, nothing otherwise."""

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card
from tests.notifier.test_book_line import eval_res as base_eval_res  # noqa: F401  (fixture)


ACTION = {
    "id": "s",
    "kind": "forward_split",
    "symbol": "NVDA",
    "related_symbol": None,
    "effective_on": "2026-10-15",
    "payable_date": None,
    "describe": "forward split 10-for-1",
}
NOTE = {
    "id": "d",
    "kind": "cash_dividend",
    "symbol": "NVDA",
    "related_symbol": None,
    "effective_on": "2026-10-14",
    "payable_date": "2026-11-11",
    "describe": "cash dividend $0.98/share",
}


def block(**overrides):
    return {
        "status": "ok",
        "mode": "preview",
        "as_of": "2026-10-12",
        "would_block": False,
        "action": None,
        "reason": None,
        "note": None,
        **overrides,
    }


def renderers(eval_res, corporate):
    with_block = eval_res.model_copy(update={"corporate_actions": corporate})
    return [format_alert_card(with_block, "TREND_PULLBACK"), format_terminal_card(with_block, "TREND_PULLBACK")]


def test_preview_block_line(base_eval_res):
    for text in renderers(base_eval_res, block(would_block=True, action=ACTION, reason="x")):
        assert "🏷 Corporate action: NVDA forward split 10-for-1 effective 2026-10-15 — preview, card unchanged" in text


def test_enforce_block_line_has_no_preview_suffix(base_eval_res):
    for text in renderers(base_eval_res, block(mode="enforce", would_block=True, action=ACTION, reason="x")):
        assert "🏷 Corporate action: NVDA forward split 10-for-1 effective 2026-10-15" in text and "preview" not in text


def test_dividend_note_line(base_eval_res):
    for text in renderers(base_eval_res, block(note=NOTE)):
        assert "🏷 Ex-dividend 2026-10-14 (cash dividend $0.98/share)" in text


@pytest.mark.parametrize(
    "corporate",
    [
        None,
        {"status": "stale", "mode": "preview"},
        {"status": "ok", "mode": "preview", "would_block": False, "action": None, "note": None},
    ],
)
def test_nothing_to_say_renders_no_line(base_eval_res, corporate):
    for text in renderers(base_eval_res, corporate):
        assert "🏷" not in text


def test_old_payload_without_the_field_still_validates(base_eval_res):
    payload = base_eval_res.model_dump(mode="json")
    payload.pop("corporate_actions", None)
    assert LLMTradeEvaluation.model_validate(payload).corporate_actions is None
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/risk/test_rules.py tests/agent/test_evaluator.py tests/notifier/test_corporate_action_line.py -q -k "corporate or dividend or stale_lookup or disabled_leaves or other_symbols"`
Expected: FAIL with `ImportError`/`AttributeError` (`corporate_action_block`, `RiskRule.CORPORATE_ACTION`, unexpected keyword `corporate_actions`).

- [ ] **Step 5: The rule**

In `agentic_trader/risk/rules.py`: add `CORPORATE_ACTION = "corporate_action"` to `RiskRule` after `REGIME_BREAKOUT`; import at module top `from agentic_trader.data.corporate_actions import blocking_corporate_action, relative_days` and under `TYPE_CHECKING` `from agentic_trader.data.corporate_actions import CorporateAction`; append after `earnings_days_out`:

```python
def corporate_action_block(
    actions: Sequence[CorporateAction], symbol: str, today: date, *, pre_days: int, post_days: int
) -> Rejection | None:
    """Rule ``corporate_action``: the first structural action (anything but a cash dividend) on ``symbol``
    effective within ``[today - post_days, today + pre_days]`` refuses the entry; raw bars straddle it or the
    instrument is about to change. The one predicate behind the scan (evaluator) gate, the tap gate and
    re-evaluate; admission does not apply it (as with ``earnings_blackout``).
    """
    action = blocking_corporate_action(actions, symbol, today, pre_days=pre_days, post_days=post_days)
    if action is None:
        return None
    return Rejection(
        RiskRule.CORPORATE_ACTION,
        f"Corporate action: {action.symbol} {action.describe()} effective {action.effective_on.isoformat()} "
        f"({relative_days(action.effective_on, today)}) — within the {pre_days}/{post_days}-day blackout.",
    )
```

Export `corporate_action_block` from `agentic_trader/risk/__init__.py` (import list and `__all__`). If importing `agentic_trader.data.corporate_actions` from `risk` creates an import cycle (`ImportError` at test collection), move `blocking_corporate_action` and `relative_days` into the rule module's own private helpers operating on the `CorporateAction` protocol attributes and have `data/corporate_actions.py` import them from `risk` instead; record the ruling in the ledger.

- [ ] **Step 6: Evaluator**

In `agentic_trader/agent/evaluator.py`:

1. `LLMTradeEvaluation`: after `earnings_note: str | None = None` add
   `corporate_actions: dict[str, Any] | None = None  # the scan's corporate-action block (docs/corporate-actions.md)`.
2. `evaluate_candidate` signature: add `corporate_actions: CorporateActionsLookup | None = None` after `current_equity`; import `CorporateActionsLookup`, `CorporateActionKind`, `blocking_corporate_action`, `between` from `agentic_trader.data.corporate_actions`, `corporate_action_block` from `agentic_trader.risk`, and `ET_TZ` from `agentic_trader.market.session`.
3. After step 2b (the earnings block) and before step 3 insert:

```python
# 2c. Corporate actions (agentic_trader.risk.corporate_action_block; equities only). A missing
# or stale capture never refuses; preview annotates; enforce refuses a structural action.
corporate_block: dict[str, Any] | None = None
corporate_policy = self.config.corporate_actions
if asset_class == AssetClass.EQUITY and corporate_actions is not None and corporate_policy.entry_block != "off":
    if corporate_actions.status != "ok":
        corporate_block = {"status": corporate_actions.status, "mode": corporate_policy.entry_block}
    else:
        today = evaluated_at.astimezone(ET_TZ).date()
        rejection = corporate_action_block(
            corporate_actions.actions,
            candidate.contract,
            today,
            pre_days=corporate_policy.pre_days,
            post_days=corporate_policy.post_days,
        )
        action = blocking_corporate_action(
            corporate_actions.actions,
            candidate.contract,
            today,
            pre_days=corporate_policy.pre_days,
            post_days=corporate_policy.post_days,
        )
        note = next(
            (
                a
                for a in between(
                    corporate_actions.actions,
                    candidate.contract,
                    today,
                    today + timedelta(days=corporate_policy.pre_days),
                )
                if a.kind == CorporateActionKind.CASH_DIVIDEND
            ),
            None,
        )
        corporate_block = {
            "status": "ok",
            "mode": corporate_policy.entry_block,
            "as_of": corporate_actions.as_of.isoformat() if corporate_actions.as_of else None,
            "would_block": rejection is not None,
            "action": action.summary() if action is not None else None,
            "reason": rejection.reason if rejection is not None else None,
            "note": note.summary() if note is not None else None,
        }
        if rejection is not None and corporate_policy.entry_block == "enforce":
            return _rejected(
                rejection.reason,
                rejection.rule,
                thesis=f"Rejected: corporate action for {candidate.contract}.",
                earnings_note=earnings_note_value,
                corporate_actions=corporate_block,
            )
```

4. Every later `_rejected(...)` call and the approved-result constructor that pass `earnings_note=earnings_note_value` also pass `corporate_actions=corporate_block` (grep `earnings_note=earnings_note_value` in the file and add the keyword beside each).

- [ ] **Step 7: Copilot — scan, provenance, tap**

In `run_scan` (around the `evaluate_candidate` call at the per-candidate loop), build the lookup once before the loop:

```python
        corporate_lookup = await self._corporate_actions_lookup(datetime.now(UTC))
```

and pass `corporate_actions=corporate_lookup,` into `self.evaluator.evaluate_candidate(...)`. In the `decision_provenance` dict beside `"book_sizing": book_block,` add `"corporate_actions": eval_res.corporate_actions,`. The second `evaluate_candidate` call (the re-evaluation path) passes `corporate_actions=await self._corporate_actions_lookup(datetime.now(UTC))`.

Add beside `_earnings_blackout_reason`:

```python
    async def _corporate_action_reason(self, contract: str, now: datetime) -> str | None:
        """The evaluator's corporate-action gate at tap time: enforce refuses, preview and stale evidence never do."""
        policy = self.config.corporate_actions
        if policy.entry_block != "enforce":
            return None
        lookup = await self._corporate_actions_lookup(now)
        if lookup is None or lookup.status != "ok":
            return None
        rejection = corporate_action_block(
            lookup.actions, contract, now.astimezone(ET_TZ).date(), pre_days=policy.pre_days, post_days=policy.post_days
        )
        return rejection.reason if rejection is not None else None
```

and in `_tap_gate_reason` replace the equity branch with:

```python
        if asset_class.upper() == AssetClass.EQUITY:
            reason = await self._earnings_blackout_reason(sig["contract"], now)
            return reason or await self._corporate_action_reason(sig["contract"], now)
        return None
```

Import `corporate_action_block` from `agentic_trader.risk`. Add a tap test to `tests/agent/test_card_freshness_tap.py` mirroring its earnings-blackout tap test: with `entry_block="enforce"` and `copilot.corporate_actions` replaced by a fake whose `lookup()` returns a fresh lookup holding a split effective today for the card's symbol, the tap is refused with a reason starting `Corporate action:`; with `entry_block="preview"` the same tap proceeds.

- [ ] **Step 8: Card line**

In `agentic_trader/notifier/telegram_bot.py` after `_book_line`:

```python
def _corporate_action_line(block: dict[str, Any] | None, *, markup: bool) -> str:
    """The corporate-action line, newline-terminated; empty without a fresh block or anything to say."""
    if not block or block.get("status") != "ok":
        return ""
    action, note = block.get("action"), block.get("note")
    if block.get("would_block") and isinstance(action, dict):
        text = f"🏷 Corporate action: {action['symbol']} {action['describe']} effective {action['effective_on']}"
        if block.get("mode") == "preview":
            text += " — preview, card unchanged"
    elif isinstance(note, dict):
        text = f"🏷 Ex-dividend {note['effective_on']} ({note['describe']})"
    else:
        return ""
    return (html.escape(text) if markup else text) + "\n"
```

In both `format_alert_card` and `format_terminal_card`, extend the `evidence_block` expression:
`… + _book_line(book_sizing, markup=…) + _corporate_action_line(eval_res.corporate_actions, markup=…)`.

- [ ] **Step 9: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/risk tests/agent/test_evaluator.py tests/agent/test_evaluator_risk_policy.py tests/agent/test_tap_risk_policy.py tests/agent/test_card_freshness_tap.py tests/notifier -q`
Expected: all PASS.

- [ ] **Step 10: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run ruff check agentic_trader tests/risk tests/agent tests/notifier && env -u VIRTUAL_ENV uv run ruff format agentic_trader/risk/rules.py agentic_trader/agent/evaluator.py agentic_trader/agent/copilot.py agentic_trader/notifier/telegram_bot.py tests/notifier/test_corporate_action_line.py
env -u VIRTUAL_ENV uv run mypy agentic_trader/risk agentic_trader/agent/evaluator.py agentic_trader/notifier/telegram_bot.py
git add agentic_trader/risk agentic_trader/agent/evaluator.py agentic_trader/agent/copilot.py agentic_trader/notifier/telegram_bot.py tests/risk/test_rules.py tests/agent/test_evaluator.py tests/agent/test_card_freshness_tap.py tests/notifier/test_corporate_action_line.py
git commit -m "Corporate actions: corporate_action entry rule (preview by default), evaluator and tap gates, provenance and card line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `/positions` notes and the ledger's named reason

**Files:**
- Modify: `agentic_trader/agent/copilot.py:2574-2684` (`get_positions_report`, broker branch)
- Modify: `agentic_trader/accounting/ledger.py` (`reconcile(..., corporate_actions=())`, `unsupported_activity_issue`)
- Modify: `agentic_trader/accounting/service.py` (`AccountLedgerService(..., corporate_actions=None)`, pass actions to `reconcile`), and the `AccountLedgerService(` construction in `agentic_trader/agent/copilot.py` (pass `corporate_actions=self.corporate_actions`; construct `self.corporate_actions` before it)
- Test: `tests/accounting/test_ledger.py` (append), `tests/accounting/test_service_corporate_actions.py`, `tests/agent/test_positions_corporate_actions.py`

**Interfaces:**
- Consumes: Task 1 `CorporateAction`, `between`, `position_note`; Task 3 `TradingCopilot._corporate_actions_lookup`, `TradingCopilot.corporate_actions`; Task 2 `CorporateActionsStore.latest_capture`.
- Produces: `unsupported_activity_issue(kind, activity, corporate_actions) -> str`; `reconcile` keyword `corporate_actions: Sequence[CorporateAction] = ()`; `AccountLedgerService.__init__(broker, store, config, *, corporate_actions: CorporateActionsStore | None = None)`.

- [ ] **Step 1: Write the failing ledger tests** (append to `tests/accounting/test_ledger.py`; read its helpers first and reuse its snapshot/activity builders where they exist)

```python
from datetime import date as _date

from agentic_trader.accounting.ledger import unsupported_activity_issue
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind


SPLIT = CorporateAction(
    id="s",
    kind=CorporateActionKind.FORWARD_SPLIT,
    symbol="NVDA",
    effective_on=_date(2024, 6, 10),
    old_rate=1.0,
    new_rate=10.0,
)
DIVIDEND = CorporateAction(
    id="d", kind=CorporateActionKind.CASH_DIVIDEND, symbol="NVDA", effective_on=_date(2024, 6, 12), rate=0.01
)


def test_unsupported_activity_names_a_matching_structural_action():
    activity = {
        "id": "a1",
        "activity_type": "SSP",
        "symbol": "NVDA",
        "date": "2024-06-10",
        "qty": "90",
        "net_amount": "0",
    }
    assert unsupported_activity_issue("SSP", activity, [DIVIDEND, SPLIT]) == (
        "Unsupported activity type: SSP (forward split 10-for-1 NVDA effective 2024-06-10 in corporate-action evidence)"
    )


@pytest.mark.parametrize(
    "activity",
    [
        {"activity_type": "SSP", "symbol": "NVDA", "date": "2024-07-15"},  # outside ten days
        {"activity_type": "SSP", "symbol": "AAPL", "date": "2024-06-10"},  # other symbol
        {"activity_type": "SSP", "symbol": "NVDA", "date": "not-a-date"},
        {"activity_type": "SSP"},
    ],
)
def test_unsupported_activity_without_a_match_keeps_the_plain_text(activity):
    assert unsupported_activity_issue("SSP", activity, [SPLIT]) == "Unsupported activity type: SSP"
    assert unsupported_activity_issue("SSP", activity, []) == "Unsupported activity type: SSP"


def test_reconcile_threads_actions_into_the_issue_and_still_withholds_totals():
    snapshot = AccountSnapshot(account_id="acct", cash=Decimal("1000"), positions={})
    activities = [
        {"id": "a1", "activity_type": "SSP", "symbol": "NVDA", "date": "2024-06-10", "qty": "90", "net_amount": "0"},
    ]
    report = reconcile(activities, snapshot, corporate_actions=[SPLIT])
    assert report.issues == [
        "Unsupported activity type: SSP (forward split 10-for-1 NVDA effective 2024-06-10 in corporate-action evidence)"
    ]
    assert report.gross_realized is None and report.ready is False
    assert reconcile(activities, snapshot).issues == ["Unsupported activity type: SSP"]
```

(Adjust the `AccountSnapshot`/`reconcile` imports to the file's existing ones; `Decimal` and `pytest` are already imported there.)

- [ ] **Step 2: Write the failing service test**

```python
# tests/accounting/test_service_corporate_actions.py
"""The ledger service passes the latest capture's actions to reconcile; a store failure degrades to none."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from agentic_trader.accounting.ledger import AccountSnapshot
from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.config import AccountingConfig


def _broker(activities):
    broker = MagicMock()
    broker.account_snapshot = AsyncMock(
        return_value=AccountSnapshot(account_id="acct", cash=Decimal("1000"), positions={})
    )
    broker.account_activities = AsyncMock(return_value=activities)
    return broker


def _store():
    store = MagicMock()
    store.begin = AsyncMock(return_value="token")
    store.bind = AsyncMock()
    store.commit = AsyncMock(return_value=True)
    store.fail = AsyncMock()
    return store


CAPTURE = {
    "content_sha256": "x",
    "as_of": "2024-06-10",
    "actions": [
        {
            "id": "s",
            "kind": "forward_split",
            "symbol": "NVDA",
            "effective_on": "2024-06-10",
            "old_rate": 1.0,
            "new_rate": 10.0,
        }
    ],
}
SSP = {"id": "a1", "activity_type": "SSP", "symbol": "NVDA", "date": "2024-06-10", "qty": "90", "net_amount": "0"}


async def test_refresh_names_the_action_from_the_latest_capture():
    actions = MagicMock()
    actions.latest_capture = AsyncMock(return_value=CAPTURE)
    service = AccountLedgerService(_broker([SSP]), _store(), AccountingConfig(), corporate_actions=actions)
    report = await service.refresh()
    assert report.issues == [
        "Unsupported activity type: SSP (forward split 10-for-1 NVDA effective 2024-06-10 in corporate-action evidence)"
    ]


async def test_refresh_without_a_store_or_with_a_failing_store_keeps_plain_text():
    plain = await AccountLedgerService(_broker([SSP]), _store(), AccountingConfig()).refresh()
    assert plain.issues == ["Unsupported activity type: SSP"]
    failing = MagicMock()
    failing.latest_capture = AsyncMock(side_effect=RuntimeError("db down"))
    degraded = await AccountLedgerService(
        _broker([SSP]), _store(), AccountingConfig(), corporate_actions=failing
    ).refresh()
    assert degraded.issues == ["Unsupported activity type: SSP"]
    assert datetime.now(UTC) >= degraded.observed_at
```

- [ ] **Step 3: Write the failing positions tests**

```python
# tests/agent/test_positions_corporate_actions.py
"""`/positions` notes for held symbols' corporate actions; stale/missing evidence is one note; disabled adds none."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind, CorporateActionsLookup


def _position(symbol):
    position = MagicMock()
    position.symbol, position.direction, position.quantity = symbol, "LONG", 10.0
    position.entry_price, position.current_price, position.unrealized_pnl = 100.0, 101.0, 10.0
    position.model_dump = MagicMock(return_value={"symbol": symbol})
    return position


@pytest.fixture
def copilot(config, temp_db):
    broker = MagicMock()
    broker.authoritative_positions = True
    broker.get_positions = AsyncMock(return_value=[_position("XOM"), _position("NVDA")])
    bot = TradingCopilot(config=config, db=temp_db, broker=broker, notifier=MagicMock())
    bot.db.get_closed_positions_stats = AsyncMock(return_value={"total_pnl": 0})
    return bot


def _lookup(copilot, status, *actions):
    copilot.corporate_actions = MagicMock()
    today = datetime.now(UTC).date()
    copilot.corporate_actions.lookup = AsyncMock(return_value=CorporateActionsLookup(status, today, tuple(actions)))
    return today


async def test_notes_name_upcoming_and_recent_actions_for_held_symbols(copilot):
    today = _lookup(
        copilot,
        "ok",
        CorporateAction(
            id="d",
            kind=CorporateActionKind.CASH_DIVIDEND,
            symbol="XOM",
            effective_on=today_plus(2),
            rate=0.99,
            payable_date=today_plus(30),
        ),
        CorporateAction(
            id="s",
            kind=CorporateActionKind.FORWARD_SPLIT,
            symbol="NVDA",
            effective_on=today_plus(-3),
            old_rate=1.0,
            new_rate=10.0,
        ),
        CorporateAction(
            id="far",
            kind=CorporateActionKind.FORWARD_SPLIT,
            symbol="NVDA",
            effective_on=today_plus(40),
            old_rate=1.0,
            new_rate=2.0,
        ),
        CorporateAction(
            id="other",
            kind=CorporateActionKind.FORWARD_SPLIT,
            symbol="AAPL",
            effective_on=today_plus(1),
            old_rate=1.0,
            new_rate=2.0,
        ),
    )
    report = await copilot.get_positions_report()
    assert (
        f"XOM: cash dividend $0.99/share, ex {today_plus(2).isoformat()} (in 2 days), pays {today_plus(30).isoformat()}."
        in report.notes
    )
    assert "NVDA: forward split 10-for-1 effective" in report.notes and "predate it until reconciled" in report.notes
    assert "2-for-1" not in report.notes and "AAPL" not in report.notes
    assert "Corporate-action evidence" not in report.notes


@pytest.mark.parametrize("status", ["stale", "unavailable"])
async def test_stale_capture_adds_one_note(copilot, status):
    _lookup(copilot, status)
    report = await copilot.get_positions_report()
    assert report.notes.count("Corporate-action evidence") == 1 and f"evidence {status}." in report.notes


async def test_notes_absent_when_disabled(copilot):
    copilot.config = copilot.config.model_copy(
        update={"corporate_actions": copilot.config.corporate_actions.model_copy(update={"enabled": False})}
    )
    copilot.corporate_actions = MagicMock()
    copilot.corporate_actions.lookup = AsyncMock(side_effect=AssertionError("must not be read"))
    report = await copilot.get_positions_report()
    assert "Corporate-action" not in report.notes and "NVDA:" not in report.notes


def today_plus(days: int) -> date:
    return datetime.now(UTC).date() + timedelta(days=days)
```

If `TradingCopilot(config=…, db=…, broker=…, notifier=…)` needs further keyword arguments to construct offline (see `tests/agent/test_card_stats_worker.py` and `tests/broker/test_incident_regressions.py` for how they build one), mirror the nearest existing fixture; the positions test only needs `get_positions_report`.

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/accounting tests/agent/test_positions_corporate_actions.py -q`
Expected: FAIL (`ImportError: unsupported_activity_issue`, unexpected keyword `corporate_actions`, missing notes).

- [ ] **Step 5: Ledger**

In `agentic_trader/accounting/ledger.py` add `from collections.abc import Mapping, Sequence`, `from datetime import UTC, date, datetime` and `from agentic_trader.data.corporate_actions import CorporateAction`; add:

```python
def unsupported_activity_issue(
    kind: str, activity: Mapping[str, Any], corporate_actions: Sequence[CorporateAction]
) -> str:
    """The ledger's unsupported-activity issue, naming a structural corporate action on the same symbol within
    ten days when the capture holds one. Totals stay withheld either way; this explains, it does not resolve."""
    base = f"Unsupported activity type: {kind}"
    symbol = str(activity.get("symbol") or "").strip().upper()
    raw_date = str(activity.get("date") or activity.get("transaction_time") or "")[:10]
    try:
        when = date.fromisoformat(raw_date)
    except ValueError:
        return base
    for action in corporate_actions:
        if action.is_structural and action.symbol == symbol and abs((action.effective_on - when).days) <= 10:
            return f"{base} ({action.describe()} {symbol} effective {action.effective_on.isoformat()} in corporate-action evidence)"
    return base
```

Change the `reconcile` signature to
`def reconcile(activities, snapshot, *, cash_tolerance: Decimal = Decimal("0.01"), corporate_actions: Sequence[CorporateAction] = ()) -> LedgerReport:`
and replace `issues.append(f"Unsupported activity type: {kind}")` with `issues.append(unsupported_activity_issue(kind, activity, corporate_actions))`. Add `unsupported_activity_issue` to the module's exports if it has `__all__`.

- [ ] **Step 6: Ledger service**

In `agentic_trader/accounting/service.py`:

```python
def __init__(
    self,
    broker: BaseBroker,
    store: LedgerStore,
    config: AccountingConfig,
    *,
    corporate_actions: CorporateActionsStore | None = None,
):
    self.broker, self.store, self.config = broker, store, config
    self.corporate_actions = corporate_actions
    self._lock = asyncio.Lock()


async def _corporate_actions(self) -> tuple[CorporateAction, ...]:
    """The latest capture's actions for the issue text; a read failure degrades to none (type name logged)."""
    if self.corporate_actions is None:
        return ()
    try:
        capture = await self.corporate_actions.latest_capture()
    except Exception as exc:
        logger.warning(
            "Corporate-action capture unavailable to the ledger",
            extra={"event": "ledger_corporate_actions_unavailable", "reason": type(exc).__name__},
        )
        return ()
    return tuple(CorporateAction.model_validate(row) for row in (capture or {}).get("actions", []))
```

In `refresh`, before the `reconcile` thread call: `actions = await self._corporate_actions()`, and pass `corporate_actions=actions` to `reconcile`. Imports: `CorporateAction` from `agentic_trader.data.corporate_actions`, `CorporateActionsStore` under `TYPE_CHECKING` from `agentic_trader.storage.corporate_actions`, `logger = logging.getLogger(__name__)` if the module has none.

In `agentic_trader/agent/copilot.py` the `AccountLedgerService(self.broker, LedgerStore(self.db.workflows), config.accounting)` construction becomes `AccountLedgerService(self.broker, LedgerStore(self.db.workflows), config.accounting, corporate_actions=self.corporate_actions)`; `self.corporate_actions` is assigned earlier in the constructor (Task 3), confirm the order.

- [ ] **Step 7: Positions notes**

In `get_positions_report`, inside the `if self.broker.authoritative_positions:` branch after the `notes.extend(... "tracked order has no open broker position" ...)` statement:

```python
            lookup = await self._corporate_actions_lookup(datetime.now(UTC))
            if lookup is not None:
                if lookup.status != "ok":
                    notes.append(f"Corporate-action evidence {lookup.status}.")
                else:
                    policy = self.config.corporate_actions
                    today = datetime.now(ET_TZ).date()
                    for bp in broker_positions:
                        window = between(
                            lookup.actions,
                            bp.symbol,
                            today - timedelta(days=policy.post_days),
                            today + timedelta(days=policy.notes_horizon_days),
                        )
                        notes.extend(position_note(action, today) for action in window)
```

Import `between`, `position_note` from `agentic_trader.data.corporate_actions`.

- [ ] **Step 8: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/accounting tests/agent/test_positions_corporate_actions.py tests/broker/test_incident_regressions.py -q`
Expected: all PASS.

- [ ] **Step 9: Lint and commit**

```bash
env -u VIRTUAL_ENV uv run ruff check agentic_trader/accounting agentic_trader/agent/copilot.py tests/accounting tests/agent/test_positions_corporate_actions.py && env -u VIRTUAL_ENV uv run ruff format agentic_trader/accounting agentic_trader/agent/copilot.py tests/accounting tests/agent/test_positions_corporate_actions.py
env -u VIRTUAL_ENV uv run mypy agentic_trader/accounting
git add agentic_trader/accounting agentic_trader/agent/copilot.py tests/accounting tests/agent/test_positions_corporate_actions.py
git commit -m "Corporate actions: /positions notes for held symbols and a named reason in the ledger's unsupported-activity issue

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Research acquisition, CLI and documentation

**Files:**
- Create: `agentic_trader/research/corporate_actions.py`, `agentic_trader/cli/commands/alpha_corporate_actions.py`
- Modify: the module that registers `universe_snapshot_cmd` on the alpha group (find with `grep -rn "universe_snapshot_cmd" agentic_trader/cli`), register `corporate_actions_cmd` the same way
- Create: `docs/corporate-actions.md`
- Modify: `docs/risk-policy.md` (rule table row after `earnings_blackout`), `docs/production.md` (worker paragraph after the card-statistics worker paragraph), `docs/architecture-review.md:90` (P1 row), `CLAUDE.md` (one paragraph after the spread-lane paragraph; one entry in the code map's "Market data and sessions" row), `docs/alpha-roadmap.md` (item 3 status)
- Test: `tests/research/test_corporate_actions_cache.py`, `tests/cli/test_alpha_corporate_actions.py`

**Interfaces:**
- Consumes: Task 1 `CorporateAction`, `CorporateActionsSource`, `normalize_actions`; `agentic_trader.storage.workflow.encode`; `agentic_trader.cli.commands.alpha.research_environment`; `agentic_trader.cli.utils.coro`.
- Produces: `fetch_corporate_actions_cached(source, symbols, start, end, cache_dir, *, pace) -> tuple[tuple[CorporateAction, ...], dict]`; CLI `copilot alpha corporate-actions`.

- [ ] **Step 1: Write the failing cache tests**

```python
# tests/research/test_corporate_actions_cache.py
"""Research acquisition: one hash-named cache file per request, digest-checked on reuse, no provider on a hit."""

import json
from datetime import UTC, date, datetime

import pytest

from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from agentic_trader.research.corporate_actions import fetch_corporate_actions_cached
from tests.data.test_corporate_actions import RAW


class CountingSource:
    def __init__(self):
        self.calls = 0

    def fetch(self, symbols, start, end):
        self.calls += 1
        now = datetime.now(UTC)
        return CorporateActionsPage(normalize_actions(RAW), now, now, len(symbols), (start, end), 1, ("p",))


async def _pace():
    return None


async def test_fetch_writes_a_hash_named_file_and_reuses_it(tmp_path):
    source = CountingSource()
    args = (source, ["NVDA", "aapl", "AAPL"], date(2024, 1, 1), date(2024, 12, 31), tmp_path)
    actions, manifest = await fetch_corporate_actions_cached(*args, pace=_pace)
    assert len(actions) == 4 and source.calls == 1
    files = list((tmp_path / "corporate-actions").glob("*.json"))
    assert len(files) == 1 and files[0].stem == manifest["request_sha256"]
    assert manifest["count"] == 4 and manifest["counts_by_kind"]["cash_dividend"] == 1
    assert manifest["symbols"] == ["AAPL", "NVDA"] and manifest["cache_file"] == str(files[0])
    again, manifest_again = await fetch_corporate_actions_cached(*args, pace=_pace)
    assert again == actions and source.calls == 1 and manifest_again["content_sha256"] == manifest["content_sha256"]
    assert manifest_again["cached"] is True and manifest["cached"] is False


async def test_a_different_request_is_a_different_file_and_a_corrupt_cache_is_refused(tmp_path):
    source = CountingSource()
    await fetch_corporate_actions_cached(source, ["AAPL"], date(2024, 1, 1), date(2024, 12, 31), tmp_path, pace=_pace)
    await fetch_corporate_actions_cached(source, ["AAPL"], date(2024, 1, 1), date(2025, 12, 31), tmp_path, pace=_pace)
    files = sorted((tmp_path / "corporate-actions").glob("*.json"))
    assert len(files) == 2 and source.calls == 2
    document = json.loads(files[0].read_text())
    document["actions"][0]["rate"] = 9.99
    files[0].write_text(json.dumps(document))
    with pytest.raises(ValueError, match="does not match its recorded SHA-256"):
        await fetch_corporate_actions_cached(
            source, ["AAPL"], date(2024, 1, 1), date(2024, 12, 31), tmp_path, pace=_pace
        )
```

- [ ] **Step 2: Write the failing CLI test**

```python
# tests/cli/test_alpha_corporate_actions.py
"""`alpha corporate-actions`: symbols from --symbol and --cohort, private output files, no database."""

import json
from datetime import UTC, datetime

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_corporate_actions as cmd
from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from tests.data.test_corporate_actions import RAW


class FakeSource:
    def __init__(self):
        self.requests = []

    def fetch(self, symbols, start, end):
        self.requests.append((tuple(symbols), start, end))
        now = datetime.now(UTC)
        return CorporateActionsPage(normalize_actions(RAW), now, now, len(symbols), (start, end), 1, ("p",))


def test_command_writes_actions_and_manifest(tmp_path, monkeypatch):
    source = FakeSource()
    monkeypatch.setattr(cmd, "_build_source", lambda: source)
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"sectors": {"energy": ["XOM"], "technology": ["NVDA", "AAPL"]}}))
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cmd.corporate_actions_cmd,
        [
            "--symbol",
            "pxd",
            "--cohort",
            str(cohort),
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert source.requests[0][0] == ("AAPL", "NVDA", "PXD", "XOM")
    actions = json.loads((output / "actions.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(actions) == 4 and manifest["count"] == 4 and manifest["symbols"] == ["AAPL", "NVDA", "PXD", "XOM"]
    assert manifest["request_sha256"] and manifest["content_sha256"] and "environment" in manifest
    assert "4 corporate actions" in result.output


def test_command_refuses_an_existing_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cmd, "_build_source", lambda: FakeSource())
    output = tmp_path / "out"
    output.mkdir()
    result = CliRunner().invoke(
        cmd.corporate_actions_cmd,
        [
            "--symbol",
            "AAPL",
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0 and "exists" in result.output
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_corporate_actions_cache.py tests/cli/test_alpha_corporate_actions.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Research acquisition**

```python
# agentic_trader/research/corporate_actions.py
"""Cached corporate-action acquisition for research (docs/corporate-actions.md#research-acquisition).

One JSON file per request under ``cache_dir/corporate-actions/<sha256 of the request>.json`` holding the
normalised actions, the receipt and the content digest; a reuse verifies the digest and refuses a corrupt
file. Research only: no database, registry, broker or Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionsSource
from agentic_trader.storage.workflow import encode


__all__ = ["fetch_corporate_actions_cached"]


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


async def fetch_corporate_actions_cached(
    source: CorporateActionsSource,
    symbols: Sequence[str],
    start: date,
    end: date,
    cache_dir: Path,
    *,
    pace: Callable[[], Awaitable[None]],
) -> tuple[tuple[CorporateAction, ...], dict[str, Any]]:
    ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
    request = {"source": "alpaca", "symbols": ordered, "start": start.isoformat(), "end": end.isoformat()}
    request_sha = _sha256(request)
    path = Path(cache_dir) / "corporate-actions" / f"{request_sha}.json"
    if path.exists():
        document = json.loads(await asyncio.to_thread(path.read_text))
        if _sha256(document["actions"]) != document["content_sha256"]:
            raise ValueError(
                f"Corporate-action cache {path.name} does not match its recorded SHA-256; refusing a corrupt cache"
            )
        actions = tuple(CorporateAction.model_validate(row) for row in document["actions"])
        cached = True
    else:
        await pace()
        page = await asyncio.to_thread(source.fetch, ordered, start, end)
        actions = page.actions
        rows = [action.model_dump(mode="json") for action in actions]
        document = {
            "request": request,
            "request_sha256": request_sha,
            "actions": rows,
            "content_sha256": _sha256(rows),
            "requested_at": page.requested_at.isoformat(),
            "received_at": page.received_at.isoformat(),
            "pages": page.pages,
            "page_sha256": list(page.page_sha256),
        }
        await asyncio.to_thread(_write_private, path, json.dumps(document, indent=2, sort_keys=True))
        cached = False
    counts = Counter(action.kind.value for action in actions)
    manifest = {
        "request_sha256": request_sha,
        "content_sha256": document["content_sha256"],
        "symbols": ordered,
        "window": [start.isoformat(), end.isoformat()],
        "count": len(actions),
        "counts_by_kind": dict(sorted(counts.items())),
        "cache_file": str(path),
        "cached": cached,
        "requested_at": document["requested_at"],
        "received_at": document["received_at"],
    }
    return actions, manifest
```

- [ ] **Step 5: CLI**

```python
# agentic_trader/cli/commands/alpha_corporate_actions.py
"""Research-only corporate-action acquisition for a symbol list or a frozen cohort file."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.data.corporate_actions import AlpacaCorporateActionsSource, CorporateActionsSource
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.research.corporate_actions import fetch_corporate_actions_cached
from agentic_trader.transport.alpaca import BoundedCorporateActionsClient


def _build_source() -> CorporateActionsSource:
    config = load_config()
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise click.ClickException("Alpaca data credentials are required")
    client = BoundedCorporateActionsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    return AlpacaCorporateActionsSource(
        client,
        pacer=RequestPacer(config.corporate_actions.max_requests_per_minute),
        chunk_size=config.corporate_actions.chunk_size,
    )


def _cohort_symbols(path: Path) -> list[str]:
    document = json.loads(path.read_text())
    if "sectors" in document:
        return [symbol for members in document["sectors"].values() for symbol in members]
    if "symbols" in document:
        return list(document["symbols"])
    raise click.ClickException("Cohort file needs a 'sectors' mapping or a 'symbols' list")


async def _noop_pace() -> None:
    return None


@click.command("corporate-actions")
@click.option("--symbol", "symbols", multiple=True, help="A symbol; repeatable.")
@click.option(
    "--cohort", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="A frozen cohort JSON file."
)
@click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--cache", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@coro
async def corporate_actions_cmd(symbols, cohort: Path | None, start, end, cache: Path, output: Path):
    """Fetch and cache corporate actions (process_date window) for research; no database, no trading."""
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    wanted = list(symbols) + (_cohort_symbols(cohort) if cohort else [])
    if not wanted:
        raise click.ClickException("Pass --symbol and/or --cohort")
    start_date, end_date = date.fromisoformat(start.strftime("%Y-%m-%d")), date.fromisoformat(end.strftime("%Y-%m-%d"))
    if end_date < start_date:
        raise click.ClickException("--end must not precede --start")
    actions, manifest = await fetch_corporate_actions_cached(
        _build_source(), wanted, start_date, end_date, cache, pace=_noop_pace
    )
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(parents=True)
    (output / "actions.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in actions], indent=2, sort_keys=True)
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    click.echo(f"{len(actions)} corporate actions for {len(manifest['symbols'])} symbols; {output / 'manifest.json'}")
```

Register `corporate_actions_cmd` on the alpha group exactly where `universe_snapshot_cmd` is registered.

- [ ] **Step 6: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_corporate_actions_cache.py tests/cli/test_alpha_corporate_actions.py tests/cli -q`
Expected: all PASS.

- [ ] **Step 7: Documentation**

Create `docs/corporate-actions.md` with these sections, written from the spec and the shipped code (no placeholders): **Purpose** (the three gaps, the "pays twice" rationale, part 2 pointer); **Data** (the thirteen Alpaca shapes, `effective_on` per kind, the measured `process_date` filter and history depth, the 2026-10-11 probe numbers); **Model and queries** (`CorporateAction`, `is_structural`, `price_factor`, `describe`, `between`, `blocking_corporate_action`); **Daemon worker** (once per New York date, the window arithmetic, symbol union and caps, one capture per content change plus one check per run, `copilot db events --stream corporate-actions/capture --limit 1`, readiness `corporate_actions` with the four-day limit, fail-closed runs); **Entry rule** (`corporate_action`, `pre_days`/`post_days`, `off`/`preview`/`enforce`, the provenance block shape, the card line, stale never refuses, admission does not apply it); **`/positions` notes**; **Ledger** (explains, does not resolve); **Research acquisition** (cache file, digest check, the CLI); **Configuration** (the table from spec §3.4 plus `max_requests_per_minute`); **Operations** (deploy sequence, preview week, enforce decision); **Known limits** (raw live bars; no automatic adjustments; special dividends treated as ordinary; provider dependence).

Edit the other documents:

- `docs/risk-policy.md`: add after the `earnings_blackout` row:
  `| corporate_action | the latest fresh corporate-action capture, the New York date, corporate_actions.pre_days / post_days (predicate blocking_corporate_action) | evaluator gate, equities; preview annotates, enforce refuses; stale or missing evidence never refuses | tap gate, equities, enforce only | — | Corporate action: {SYMBOL} {describe} effective {YYYY-MM-DD} ({in n day(s) | n day(s) ago | today}) — within the {pre}/{post}-day blackout. | risk/test_rules.py, agent/test_evaluator.py, agent/test_card_freshness_tap.py |` (use the table's pipe-escaped spelling for the alternatives, as the `earnings_blackout` row does). Also add `corporate_action` to the "Order of evaluation per layer" section after the earnings blackout for the scan and tap layers.
- `docs/production.md`: after the card-statistics worker paragraph, a **Corporate-actions worker (October 11).** paragraph: when it runs, what it writes, the readiness component and limit, what a failure looks like (`/readyz` detail is the exception type name), and the inspection command.
- `docs/architecture-review.md` line 90: change the P1 row's third cell to begin "Corporate-action evidence is now captured daily and named in the ledger's unsupported-activity issue, on `/positions` and on cards ([corporate actions](corporate-actions.md)); exact typed activity semantics and replay fixtures remain, so totals stay withheld and sliced trading disabled; never assign by symbol."
- `CLAUDE.md`: in the code map row "Market data and sessions" add `data/corporate_actions.py`; after the spread-lane paragraph add one paragraph: what the capture is, the `corporate_action` rule and its default `preview`, that stale or missing evidence never refuses, that the ledger explains rather than resolves, raw live bars unchanged, link to `docs/corporate-actions.md`.
- `docs/alpha-roadmap.md` item 3: add `*Status (October 11):* corporate-actions ingestion is implemented ([corporate actions](corporate-actions.md)): …` summarising the shipped parts and that the event-leg entry (part 2) follows with the acquisition's counts.

- [ ] **Step 8: Full checks and commit**

```bash
env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format agentic_trader/research/corporate_actions.py agentic_trader/cli/commands/alpha_corporate_actions.py tests/research/test_corporate_actions_cache.py tests/cli/test_alpha_corporate_actions.py
env -u VIRTUAL_ENV uv run mypy agentic_trader
env -u VIRTUAL_ENV uv run pre-commit run --files $(git diff --name-only HEAD; git ls-files --others --exclude-standard)
git add agentic_trader/research/corporate_actions.py agentic_trader/cli/commands/alpha_corporate_actions.py agentic_trader/cli docs/corporate-actions.md docs/risk-policy.md docs/production.md docs/architecture-review.md CLAUDE.md docs/alpha-roadmap.md tests/research/test_corporate_actions_cache.py tests/cli/test_alpha_corporate_actions.py
git commit -m "Corporate actions: cached research acquisition, alpha corporate-actions CLI, documentation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes (written with the spec open)

- Spec §3.1 → Task 1; §3.2 and §3.4 → Task 2; §3.3 → Task 3; §3.5 → Task 4; §3.6 and §3.7 → Task 5; §3.8 and §2 item 5 (docs) → Task 6. §4's failure table is pinned by: worker failure (Task 3 `test_failure_records_nothing_and_retries`), stale/unavailable (Task 4 `test_stale_lookup_never_refuses`, Task 5 `test_stale_capture_adds_one_note`), bounds (Task 2 `test_capture_bound_fails_closed`, Task 3 `test_symbol_cap_fails_closed…`), disabled (Task 4 `test_disabled_leaves_no_block`, Task 5 `test_notes_absent_when_disabled`, Task 3 readiness `disabled`).
- Names used across tasks: `CorporateActionsLookup.build(capture, check, *, now, max_age_seconds)`; `CorporateActionsStore.lookup(*, now, max_age_seconds)`; `TradingCopilot._corporate_actions_lookup(now)`; `corporate_action_block(actions, symbol, today, *, pre_days, post_days)`; `LLMTradeEvaluation.corporate_actions`; `position_note(action, today)`; `between(actions, symbol, start, end)`. Task 4's evaluator test imports `ET_TZ` from `agentic_trader.market.session`; add it to the test module's imports.
- The `card_evidence`-style threading through the notification payload is unnecessary because the block travels inside `eval_res`; old payloads validate with `corporate_actions=None` (Task 4 `test_old_payload_without_the_field_still_validates`).
