"""Check the running paper desk without submitting orders or sending messages.

Reads Alpaca, Telegram identity/chat/commands, daemon health and startup audit.
Building the shared position report records a valuation audit entry.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request

from telegram import Bot

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import BrokerPosition
from agentic_trader.config import load_config
from agentic_trader.constants import AuditEventType, ExecutionMode, RuntimeEnvironment, SystemStateKey
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter, TerminalFormatter
from agentic_trader.runtime import runtime_identity
from agentic_trader.storage.db import SignalDatabase


class SnapshotBroker(AlpacaBroker):
    """Keep the exact broker snapshot used by the production report builder."""

    snapshot: list[BrokerPosition]

    async def get_positions(self) -> list[BrokerPosition]:
        self.snapshot = await super().get_positions()
        return self.snapshot


async def main() -> None:
    config = load_config()
    if (
        config.environment != RuntimeEnvironment.PRODUCTION
        or config.execution_mode != ExecutionMode.ALPACA
        or not config.alpaca_paper
    ):
        raise RuntimeError("Verification expects the production Alpaca PAPER configuration")
    broker = SnapshotBroker(config)
    if not await broker.connect():
        raise RuntimeError("Broker connection failed")
    db = SignalDatabase(config=config)
    try:
        if await db.get_state(SystemStateKey.TRADING_HALTED) == "true":
            raise RuntimeError("Trading halt is active")
        copilot = TradingCopilot(config, db=db, broker=broker, notifier=TelegramNotifier(None, None))
        report = await copilot.get_positions_report()
        actual = {p.symbol: p for p in broker.snapshot}
        if len(report.positions) != len(actual):
            raise RuntimeError("Report duplicates or omits a broker position")
        for pos in report.positions:
            bp = actual[pos.contract]
            if (pos.quantity, pos.entry_price, pos.current_price, pos.unrealized_pnl) != (
                bp.quantity,
                bp.entry_price,
                bp.current_price,
                bp.unrealized_pnl,
            ):
                raise RuntimeError(f"Report differs from broker snapshot for {pos.contract}")
        rendered = (
            TerminalFormatter.format_positions_table(report),
            TelegramHtmlFormatter.format_positions_html(report),
        )
        if not all(report.total_pnl_str in text for text in rendered) and report.positions:
            raise RuntimeError("CLI/Telegram report rendering differs")
        events = await db.get_audit_events(limit=1, event_type=AuditEventType.RUNTIME_STARTED)
        startup = events[0]
        identity = startup["payload"]
        if identity["revision"] != runtime_identity()["revision"] or "dirty" in identity["revision"]:
            raise RuntimeError("Daemon source revision differs from the clean checkout")
        os.kill(identity["pid"], 0)
        response = await asyncio.to_thread(urllib.request.urlopen, "http://127.0.0.1:9108/healthz", timeout=5)
        with response:
            if response.status != 200:
                raise RuntimeError("Health endpoint failed")
        response = await asyncio.to_thread(urllib.request.urlopen, "http://127.0.0.1:9108/metrics", timeout=5)
        with response:
            metrics = {
                line.split()[0]: float(line.split()[1])
                for line in response.read().decode().splitlines()
                if line and not line.startswith("#") and "{" not in line
            }
        poll_age = time.time() - metrics.get("trader_telegram_last_poll_success_timestamp_seconds", 0)
        if metrics.get("trader_telegram_poll_healthy") != 1 or poll_age > 2 * (
            config.telegram.poll_timeout_seconds + config.telegram.read_timeout_seconds
        ):
            raise RuntimeError(f"Daemon Telegram polling is unhealthy or stale ({poll_age:.1f}s)")
        if not config.telegram_bot_token or not config.telegram_chat_id:
            raise RuntimeError("Telegram is not configured")
        async with Bot(config.telegram_bot_token) as bot:
            me = await bot.get_me()
            await bot.get_chat(config.telegram_chat_id)
            commands = {cmd.command for cmd in await bot.get_my_commands()}
            if not {"positions", "perf", "macro", "gex"}.issubset(commands) or "regime" in commands or not me.is_bot:
                raise RuntimeError("Telegram identity or commands invalid")
        stats = await db.get_closed_positions_stats()
        print(
            json.dumps(
                {
                    "healthy": True,
                    "runtime": identity,
                    "alpaca_paper": True,
                    "telegram_identity_and_chat": "verified",
                    "telegram_poll_age_seconds": round(poll_age, 2),
                    "event_loop_lag_seconds": metrics.get("trader_event_loop_lag_seconds"),
                    "commands": sorted(commands),
                    "positions_match_broker": True,
                    "source": report.source,
                    "as_of": report.as_of,
                    "positions": [p.model_dump(mode="json") for p in broker.snapshot],
                    "total_unrealized_pnl": report.total_unrealized_pnl,
                    "confirmed_closed_pnl": stats["total_pnl"],
                    "confirmed_closed_trades": stats["total_trades"],
                    "unverified_closed_count": stats["unverified_closed_count"],
                    "tracking_notes": report.notes,
                },
                indent=2,
            )
        )
    finally:
        await db.engine.dispose()
        await broker.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
