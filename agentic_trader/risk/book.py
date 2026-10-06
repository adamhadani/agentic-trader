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
