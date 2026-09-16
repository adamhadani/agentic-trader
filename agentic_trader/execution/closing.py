"""Broker-independent close coordination, persistent claims and read-only previews."""

from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderResult, PositionCloseRequest
from agentic_trader.constants import (
    BROKER_QUANTITY_TOLERANCE,
    AuditEventType,
    CloseRequestStatus,
    Direction,
    SignalStatus,
)
from agentic_trader.storage.db import SignalDatabase


class PositionCloseService:
    def __init__(self, broker: BaseBroker, db: SignalDatabase):
        self.broker = broker
        self.db = db

    async def preview(self, signal_id: int | None = None) -> str:
        positions = await self.broker.get_positions()
        if signal_id is not None:
            signal = await self.db.get_signal_by_id(signal_id)
            if not signal or signal["status"] != SignalStatus.EXECUTED:
                return f"Signal #{signal_id} is not an active position."
            positions = [p for p in positions if p.symbol == signal["contract"].strip("/").upper()]
        lines = ["[DRY RUN] Close preview — no orders changed"]
        lines.extend(
            f"• {position.symbol} {position.direction} {position.quantity:g}: "
            "cancel symbol orders, wait for confirmation, submit market close during an eligible session."
            for position in positions
        )
        if not positions:
            lines.append("No open broker positions.")
        lines.append("Trading halt state is unchanged. Unfilled entry orders in other symbols are not cancelled.")
        return "\n".join(lines)

    async def _save_result(self, record: dict[str, Any], result: OrderResult) -> str:
        status = result.close_status or (
            CloseRequestStatus.SUBMITTED
            if result.success and result.order_id
            else CloseRequestStatus.UNKNOWN
            if result.submission_uncertain
            else CloseRequestStatus.FAILED
        )
        if status == CloseRequestStatus.COMPLETED and (
            result.fill_price is None
            or result.fill_timestamp is None
            or result.filled_quantity is None
            or not math.isclose(
                result.filled_quantity, record["quantity"], rel_tol=0, abs_tol=BROKER_QUANTITY_TOLERANCE
            )
        ):
            status = CloseRequestStatus.UNKNOWN
        if result.order_id and record.get("signal_id"):
            if status == CloseRequestStatus.FAILED:
                await self.db.clear_failed_exit_request(record["signal_id"], result.order_id)
            else:
                await self.db.record_exit_request(record["signal_id"], result.order_id)
        detail = (
            result.error_message
            or {
                CloseRequestStatus.SUBMITTED: "Close submitted; awaiting a confirmed broker fill.",
                CloseRequestStatus.COMPLETED: "Broker close fill confirmed; trade accounting reconciles separately.",
                CloseRequestStatus.UNKNOWN: "Close outcome uncertain; retained for reconciliation, never automatically resubmitted.",
                CloseRequestStatus.FAILED: "Close rejected; no fill confirmed.",
            }[status]
        )
        await self.db.update_close_request(record["id"], status, detail, result.order_id)
        return detail

    async def recover(self) -> None:
        """Resolve exact persisted client IDs without submitting or cancelling anything."""
        for record in await self.db.active_close_requests():
            try:
                result = await self.broker.find_position_close(record["id"])
                if result:
                    await self._save_result(record, result)
            except Exception as exc:
                await self.db.record_audit(
                    AuditEventType.CLOSE_BROKER_STEP,
                    {
                        "request_id": record["id"],
                        "phase": "recovery_failed",
                        "error_type": type(exc).__name__,
                    },
                    record["signal_id"],
                )

    async def close(
        self, position: BrokerPosition, signal: dict[str, Any] | None = None, *, allow_queued: bool = False
    ) -> str:
        claimed, record = await self.db.claim_close_request(
            {
                "id": f"close-{uuid4().hex}",
                "symbol": position.symbol,
                "direction": str(position.direction),
                "quantity": position.quantity,
                "signal_id": signal["id"] if signal else None,
            }
        )
        if not claimed:
            return "A close request is already active; awaiting broker reconciliation. No duplicate order submitted."
        request = PositionCloseRequest(
            symbol=position.symbol,
            direction=Direction(position.direction),
            quantity=position.quantity,
            client_order_id=record["id"],
            entry_order_id=signal.get("broker_order_id") if signal else None,
            allow_queued=allow_queued,
        )

        async def observe(payload: dict[str, Any]) -> None:
            await self.db.record_audit(
                AuditEventType.CLOSE_BROKER_STEP, {"request_id": record["id"], **payload}, record["signal_id"]
            )

        try:
            result = await self.broker.submit_position_close(request, observe)
        except Exception as exc:
            result = OrderResult(
                success=False,
                submission_uncertain=True,
                error_message=f"Close outcome uncertain ({type(exc).__name__}); inspect broker orders.",
            )
        return await self._save_result(record, result)

    async def close_signal(self, signal_id: int, *, allow_queued: bool = False) -> str:
        await self.recover()
        signal = await self.db.get_signal_by_id(signal_id)
        if not signal or signal["status"] != SignalStatus.EXECUTED:
            return f"Signal #{signal_id} is not an active position."
        if signal.get("broker_exit_order_id"):
            return "Close already submitted; awaiting confirmed broker reconciliation."
        positions = await self.broker.get_positions()
        symbol = signal["contract"].strip("/").upper()
        position = next((p for p in positions if p.symbol == symbol), None)
        if position is None:
            return "No open broker position; awaiting reconciliation of existing orders."
        tracked = [p for p in await self.db.get_active_positions() if p["contract"].strip("/").upper() == symbol]
        if len(tracked) != 1 or not self.matches(signal, position):
            return "Tracked quantity/direction is ambiguous or differs from the broker; no orders changed."
        return await self.close(position, signal, allow_queued=allow_queued)

    @staticmethod
    def matches(signal: dict[str, Any], position: BrokerPosition) -> bool:
        return str(signal["direction"]) == str(position.direction) and math.isclose(
            float(signal["quantity"]),
            position.quantity,
            rel_tol=0,
            abs_tol=BROKER_QUANTITY_TOLERANCE,
        )

    async def flatten(self) -> str:
        """Close the broker snapshot, including untracked positions, without changing halt state."""
        if not self.broker.authoritative_positions:
            return "Flatten requires an authoritative broker position snapshot; this adapter is unsupported."
        await self.recover()
        positions = await self.broker.get_positions()
        tracked = await self.db.get_active_positions()
        lines = ["Flatten results"]
        await self.db.record_audit(
            AuditEventType.FLATTEN,
            {
                "phase": "started",
                "positions": [p.model_dump(mode="json") for p in positions],
            },
        )
        for position in positions:
            matches = [p for p in tracked if p["contract"].strip("/").upper() == position.symbol]
            signal = matches[0] if len(matches) == 1 and self.matches(matches[0], position) else None
            try:
                if matches and signal is None:
                    detail = "Tracking mismatch; no orders changed. Reconcile this position before closing."
                elif signal and signal.get("broker_exit_order_id"):
                    detail = "Close already submitted; awaiting confirmed broker reconciliation."
                else:
                    detail = await self.close(position, signal)
                lines.append(f"• {position.symbol}: {detail}")
            except Exception as exc:
                lines.append(f"• {position.symbol}: close failed ({type(exc).__name__}); inspect broker state.")
                await self.db.record_audit(
                    AuditEventType.FLATTEN,
                    {
                        "phase": "symbol_failed",
                        "symbol": position.symbol,
                        "error_type": type(exc).__name__,
                    },
                )
        if not positions:
            lines.append("No open broker positions.")
        lines.append(
            "Trading halt state unchanged. Confirm remaining positions with /positions; "
            "unfilled entry orders in other symbols remain working."
        )
        await self.db.record_audit(
            AuditEventType.FLATTEN, {"phase": "completed", "positions_requested": len(positions)}
        )
        return "\n".join(lines)
