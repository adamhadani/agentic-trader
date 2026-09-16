"""Pure cash/inventory reconciliation; Alpaca remains the cost-basis authority.

Gross realized = fill cash flows + signed remaining broker cost basis. This
avoids inventing FIFO lots that disagree with Alpaca's intraday weighted average.
Unknown activities fail closed; these totals are not tax-lot accounting.
"""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, Field


Money = Annotated[Decimal, Field(allow_inf_nan=False)]
FILL = "FILL"
CASH_TRANSFERS = frozenset({"CSD", "CSW", "JNLC"})
FEES = frozenset({"FEE", "CFEE", "DIVFEE"})
INCOME = frozenset({"DIV", "INT"})
BUY_SIDES = frozenset({"buy"})
SELL_SIDES = frozenset({"sell", "sell_short"})


class AccountPosition(BaseModel):
    qty: Money
    cost_basis: Money
    unrealized_pl: Money
    asset_class: str


class AccountSnapshot(BaseModel):
    account_id: str = Field(min_length=1)
    cash: Money
    positions: dict[str, AccountPosition]
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def accounting_identity(self) -> tuple:
        """Exclude changing marks; execution/cash/basis changes invalidate the read."""
        return self.account_id, self.cash, sorted((s, p.qty, p.cost_basis) for s, p in self.positions.items())


class LedgerReport(BaseModel):
    observed_at: datetime
    activity_count: int
    fill_count: int
    issues: list[str]
    cash_delta: Money
    quantity_deltas: dict[str, Money]
    gross_realized: Money | None = None
    fees: Money
    income: Money
    net_realized: Money | None = None
    unrealized: Money

    @property
    def ready(self) -> bool:
        return not self.issues and self.gross_realized is not None and self.net_realized is not None


def decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite amount")
    return result


def reconcile(
    activities: list[dict[str, Any]], snapshot: AccountSnapshot, *, cash_tolerance: Decimal = Decimal("0.01")
) -> LedgerReport:
    snapshot = AccountSnapshot.model_validate(snapshot.model_dump())
    inventory: defaultdict[str, Decimal] = defaultdict(Decimal)
    fill_cash = cash = fees = income = Decimal(0)
    issues: list[str] = []
    ids: set[str] = set()
    fills = 0
    for activity in activities:
        try:
            activity_id = activity["id"]
            if not activity_id or activity_id in ids:
                raise ValueError("Missing/duplicate activity ID")
            ids.add(activity_id)
            kind = activity["activity_type"]
            if kind == FILL:
                qty, price = decimal(activity["qty"]), decimal(activity["price"])
                side, symbol = activity["side"], activity["symbol"]
                if qty <= 0 or price <= 0 or not symbol or not activity["order_id"] or not activity["transaction_time"]:
                    raise ValueError("Incomplete/nonpositive execution")
                if side not in BUY_SIDES | SELL_SIDES:
                    raise ValueError("Unknown execution side")
                if "/" in symbol or activity.get("asset_class", "us_equity") != "us_equity":
                    raise ValueError("Unsupported execution asset")
                direction = Decimal(1 if side in SELL_SIDES else -1)
                fill_cash += direction * qty * price
                cash += direction * qty * price
                inventory[symbol] -= direction * qty
                fills += 1
            elif kind in CASH_TRANSFERS | FEES | INCOME:
                if activity.get("status", "executed") != "executed":
                    raise ValueError("Non-executed cash activity")
                if kind in CASH_TRANSFERS and decimal(activity.get("qty") or "0") != 0:
                    raise ValueError("Unsupported share adjustment")
                amount = decimal(activity["net_amount"])
                cash += amount
                if kind in FEES:
                    fees += amount
                elif kind in INCOME:
                    income += amount
            else:
                issues.append(f"Unsupported activity type: {kind}")
        except KeyError, ValueError, InvalidOperation, TypeError:
            issues.append("Invalid activity; inspect persisted evidence")
    deltas = {
        symbol: inventory[symbol] - (snapshot.positions[symbol].qty if symbol in snapshot.positions else Decimal(0))
        for symbol in inventory.keys() | snapshot.positions.keys()
    }
    deltas = {s: q for s, q in deltas.items() if q}
    cash_delta = cash - snapshot.cash
    if abs(cash_delta) > cash_tolerance:
        issues.append(f"Cash mismatch: {cash_delta}")
    if deltas:
        issues.append("Quantity mismatch: " + ", ".join(sorted(deltas)))
    if any(
        p.asset_class != "us_equity" or (p.qty != 0 and p.qty * p.cost_basis <= 0) or (p.qty == 0 and p.cost_basis != 0)
        for p in snapshot.positions.values()
    ):
        issues.append("Unsupported asset or inconsistent signed cost basis")
    gross = None if issues else fill_cash + sum((p.cost_basis for p in snapshot.positions.values()), Decimal(0))
    return LedgerReport(
        observed_at=snapshot.observed_at,
        activity_count=len(activities),
        fill_count=fills,
        issues=sorted(set(issues)),
        cash_delta=cash_delta,
        quantity_deltas=deltas,
        gross_realized=gross,
        fees=fees,
        income=income,
        net_realized=None if gross is None else gross + fees + income,
        unrealized=sum((p.unrealized_pl for p in snapshot.positions.values()), Decimal(0)),
    )
