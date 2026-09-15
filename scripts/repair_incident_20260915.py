"""Audited, idempotent repair for confirmed test contamination on 2026-09-15.

Preview: uv run python scripts/repair_incident_20260915.py --snapshot /private/path/snapshot.json
Apply: add --apply after inspecting the preview; pause the daemon during repair.
No broker or Telegram calls. Original rows and the source SQLite database are preserved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from agentic_trader.config import load_config
from agentic_trader.constants import SignalStatus
from agentic_trader.runtime import RUN_ID
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import SignalRecord


INCIDENT = "2026-09-15-test-contamination"
QUARANTINE_IDS = (1, 2, 6, 7, 8, 9, 10, 11)
ENTRY_ID = "880db997-12a8-43f1-9404-dda56e609f75"
EXIT_ID = "3b18b9f9-5c72-4ac8-a799-5412e20402d4"


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def build_plan(snapshot: dict, legacy: dict) -> dict:
    if snapshot["alpaca_paper"] is not True or not snapshot["observed_at"].startswith("2026-09-15"):
        raise ValueError("Expected the reviewed September 15 Alpaca paper snapshot")
    signals = {row["id"]: row for row in snapshot["signals"]}
    quarantines = []
    expected_shapes = {
        1: ("SPY", "ALPHA_TEST_PG", 1, None),
        2: ("SPY", "ALPHA_TEST_PG", 1, None),
        6: ("SPY", "ALPHA_TEST_PG", 1, None),
        11: ("SPY", "ALPHA_TEST_PG", 1, None),
        7: ("SPY", "trend_pullback", 10, "ALP-ENTRY-999"),
        8: ("QQQ", "squeeze_breakout", 5, None),
        9: ("SPY", "trend_pullback", 10, "ENTRY-123"),
        10: ("/MES", "TREND_PULLBACK", 1, None),
    }
    for sid in QUARANTINE_IDS:
        row = signals[sid]
        if tuple(row[k] for k in ("contract", "strategy", "quantity", "broker_order_id")) != expected_shapes[sid]:
            raise ValueError(f"Signal #{sid} does not match the reviewed fixture fingerprint")
        if not row["timestamp"].startswith("2026-09-15"):
            raise ValueError("Unexpected signal date")
        row["timestamp"] = row["timestamp"][:19]
        quarantines.append(
            {
                "id": sid,
                "expected": {
                    k: row[k]
                    for k in (
                        "timestamp",
                        "contract",
                        "strategy",
                        "direction",
                        "quantity",
                        "entry_price",
                        "broker_order_id",
                        "status",
                        "realized_pnl",
                    )
                },
            }
        )
    orders = {row["id"]: row for row in snapshot["broker_orders"]}
    entry, exit_order = orders[ENTRY_ID], orders[EXIT_ID]
    if (
        legacy["broker_order_id"] != ENTRY_ID
        or legacy["contract"] != "SPY"
        or legacy["quantity"] != 39
        or entry["symbol"] != "SPY"
        or exit_order["symbol"] != "SPY"
        or entry["side"] != "buy"
        or exit_order["side"] != "sell"
        or entry["status"] != "filled"
        or exit_order["status"] != "filled"
        or float(entry["filled_qty"]) != 39
        or float(exit_order["filled_qty"]) != 39
        or timestamp(exit_order["filled_at"]) <= timestamp(entry["filled_at"])
    ):
        raise ValueError("Historical trade does not match the explicitly reviewed entry and exit IDs")
    pnl = (float(exit_order["filled_avg_price"]) - float(entry["filled_avg_price"])) * 39
    return {
        "incident": INCIDENT,
        "quarantine": quarantines,
        "historical_entry": entry,
        "historical_exit": exit_order,
        "historical_pnl": pnl,
        "legacy": legacy,
    }


async def apply_plan(plan: dict, snapshot_hash: str) -> dict:
    config = load_config()
    if config.environment != "production" or config.execution_mode != "alpaca" or not config.alpaca_paper:
        raise ValueError("Repair requires the production Alpaca PAPER configuration")
    db = SignalDatabase(config=config)
    try:
        # Validate every row before making any change; quarantine itself is transactional.
        async with db.session_factory() as session:
            for item in plan["quarantine"]:
                rec = await session.get(SignalRecord, item["id"])
                if not rec or any(rec.to_dict()[k] != v for k, v in item["expected"].items()):
                    raise ValueError(f"Current signal #{item['id']} differs from reviewed snapshot")
        changed = [
            item["id"]
            for item in plan["quarantine"]
            if await db.quarantine_signal(item["id"], f"{INCIDENT}; snapshot SHA256 {snapshot_hash}", item["expected"])
        ]
        async with db.session_factory() as session:
            exists = (
                await session.execute(select(SignalRecord).where(SignalRecord.broker_order_id == ENTRY_ID))
            ).scalar_one_or_none()
            if exists is None:
                old, entry, exit_order = plan["legacy"], plan["historical_entry"], plan["historical_exit"]
                price, qty = float(entry["filled_avg_price"]), float(entry["filled_qty"])
                record = SignalRecord(
                    timestamp=timestamp(old["timestamp"]),
                    contract="SPY",
                    strategy=old["strategy"],
                    direction="LONG",
                    entry_price=price,
                    stop_loss=old["stop_loss"],
                    take_profit=old["take_profit"],
                    risk_dollars=abs(price - old["stop_loss"]) * qty,
                    notional_value=price * qty,
                    status=SignalStatus.CLOSED_LOSS,
                    asset_class="EQUITY",
                    quantity=qty,
                    environment=config.environment,
                    execution_mode=db.execution_mode,
                    run_id=RUN_ID,
                    executed_at=timestamp(entry["filled_at"]),
                    broker_order_id=ENTRY_ID,
                    broker_exit_order_id=EXIT_ID,
                    exit_price=float(exit_order["filled_avg_price"]),
                    exit_timestamp=timestamp(exit_order["filled_at"]),
                    exit_reason="MANUAL_CLOSE",
                    realized_pnl=plan["historical_pnl"],
                )
                session.add(record)
                await session.flush()
                session.add(
                    db._audit(
                        "historical_trade_restored",
                        record.id,
                        {
                            "incident": INCIDENT,
                            "snapshot_sha256": snapshot_hash,
                            "source": "legacy SQLite; explicitly reviewed broker entry/exit IDs",
                            "original": old,
                            "entry": entry,
                            "exit": exit_order,
                            "corrected": record.to_dict(),
                        },
                    )
                )
                await session.commit()
                restored_id = record.id
            else:
                restored_id = exists.id
        await db.record_audit(
            "incident_repair",
            {
                "incident": INCIDENT,
                "snapshot_sha256": snapshot_hash,
                "quarantined": changed,
                "historical_signal_id": restored_id,
            },
        )
        return {
            "quarantined": changed,
            "historical_signal_id": restored_id,
            "performance": await db.get_closed_positions_stats(),
        }
    finally:
        await db.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--legacy-db", type=Path, default=Path("data/signals.db"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    payload = args.snapshot.read_bytes()
    snapshot = json.loads(payload)
    with sqlite3.connect(f"file:{args.legacy_db.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE broker_order_id = ?", (ENTRY_ID,)).fetchone()
        if row is None:
            raise ValueError("Verified historical entry missing from legacy database")
        plan = build_plan(snapshot, dict(row))
    summary = {
        "quarantine_ids": [q["id"] for q in plan["quarantine"]],
        "restored_trade": "SPY LONG 39",
        "broker_realized_pnl": plan["historical_pnl"],
        "original_recorded_pnl": plan["legacy"]["realized_pnl"],
        "applied": args.apply,
    }
    print(json.dumps(summary, indent=2))
    if args.apply:
        print(json.dumps(asyncio.run(apply_plan(plan, hashlib.sha256(payload).hexdigest())), indent=2, default=str))


if __name__ == "__main__":
    main()
