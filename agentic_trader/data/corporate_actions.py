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
from datetime import UTC, date, datetime, timedelta
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
            raise ValueError(f"corporate action {kind.value}: missing or invalid {raw_field}")  # noqa: TRY004 - one ValueError contract for every bad record
        rates[model_field] = float(value)
    related = raw.get(related_field) if related_field else None
    fields: dict[str, Any] = {
        "id": identity,
        "kind": kind,
        "symbol": symbol.upper(),
        "related_symbol": str(related).upper() if related else None,
        "effective_on": _as_date(raw.get(effective_field), kind=kind, field=effective_field),
        "special": bool(raw.get("special", False)),
        "foreign": bool(raw.get("foreign", False)),
        **{name: _optional_date(raw.get(name)) for name in _OPTIONAL_DATES},
        **rates,
    }
    return CorporateAction.model_validate(fields)


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
