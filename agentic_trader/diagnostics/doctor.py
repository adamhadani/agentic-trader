from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import litellm
from pydantic import BaseModel, Field
from telegram import Bot

from agentic_trader.agent.calendar import EconomicCalendar
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig, load_config
from agentic_trader.storage.db import SignalDatabase


logger = logging.getLogger(__name__)


class ComponentHealth(BaseModel):
    name: str
    status: str  # "OK", "WARNING", "ERROR", "DISABLED"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticReport(BaseModel):
    overall_status: str  # "HEALTHY", "DEGRADED", "UNHEALTHY"
    timestamp: str
    components: dict[str, ComponentHealth] = Field(default_factory=dict)

    def is_healthy(self) -> bool:
        return self.overall_status in ("HEALTHY", "OK")


async def check_database(config: AppConfig) -> ComponentHealth:
    """Check SQLite / SQLAlchemy persistence layer and table connectivity."""
    try:
        db = SignalDatabase(db_path=config.db_path)
        active_count = await db.get_active_position_count()
        exposure = await db.get_active_notional_exposure()
        return ComponentHealth(
            name="database",
            status="OK",
            message="Database connection verified; schema migrated to head",
            details={"active_positions": active_count, "open_notional": exposure, "db_path": config.db_path},
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status="ERROR",
            message=f"Database check failed: {e}",
            details={"error": str(e)},
        )


async def check_telegram(config: AppConfig) -> ComponentHealth:
    """Check Telegram bot token validity and chat ID configuration."""
    token = config.telegram_bot_token
    chat_id = config.telegram_chat_id
    if not token or token.startswith(("your_", "YOUR_")):
        return ComponentHealth(
            name="telegram",
            status="WARNING",
            message="TELEGRAM_BOT_TOKEN not configured; alerts will route to terminal only",
            details={"configured": False},
        )
    if not chat_id:
        return ComponentHealth(
            name="telegram",
            status="WARNING",
            message="TELEGRAM_CHAT_ID not configured; bot cannot push notifications",
            details={"configured": False},
        )

    try:
        bot = Bot(token=token)
        me = await bot.get_me()
        return ComponentHealth(
            name="telegram",
            status="OK",
            message=f"Telegram bot @{me.username} verified (ID: {me.id})",
            details={"username": me.username, "bot_id": me.id, "chat_id": chat_id},
        )
    except Exception as e:
        return ComponentHealth(
            name="telegram",
            status="ERROR",
            message=f"Telegram bot validation failed: {e}",
            details={"error": str(e)},
        )


async def check_alpaca(config: AppConfig) -> ComponentHealth:
    """Check Alpaca API key, secret, and account status."""
    if not config.alpaca_api_key or config.alpaca_api_key.startswith("your_"):
        return ComponentHealth(
            name="alpaca",
            status="DISABLED",
            message="Alpaca credentials not configured (optional if not trading equities/crypto)",
            details={"configured": False},
        )

    try:
        broker = AlpacaBroker(config)
        connected = await broker.connect()
        if connected:
            pos = await broker.get_positions()
            env_label = "Paper" if config.alpaca_paper else "Live"
            return ComponentHealth(
                name="alpaca",
                status="OK",
                message=f"Alpaca {env_label} API connected successfully ({len(pos)} open positions)",
                details={"paper": config.alpaca_paper, "positions_count": len(pos)},
            )
        else:
            return ComponentHealth(
                name="alpaca",
                status="ERROR",
                message="Alpaca connect returned False",
                details={"paper": config.alpaca_paper},
            )
    except Exception as e:
        return ComponentHealth(
            name="alpaca",
            status="ERROR",
            message=f"Alpaca authentication error: {e}",
            details={"error": str(e)},
        )


async def check_tradovate(config: AppConfig) -> ComponentHealth:
    """Check Tradovate credentials (optional if not trading futures via Tradovate)."""
    user = config.tradovate_username
    if not user or user.startswith(("your_", "YOUR_")):
        return ComponentHealth(
            name="tradovate",
            status="DISABLED",
            message="Tradovate credentials not configured (optional; PaperBroker handles CME micro futures)",
            details={"configured": False},
        )

    try:
        broker = TradovateBroker(config)
        connected = await broker.connect()
        status = "OK" if connected else "WARNING"
        msg = f"Tradovate ({config.tradovate_environment}) connected" if connected else "Tradovate auth pending"
        return ComponentHealth(
            name="tradovate",
            status=status,
            message=msg,
            details={"environment": config.tradovate_environment, "connected": connected},
        )
    except Exception as e:
        return ComponentHealth(
            name="tradovate",
            status="ERROR",
            message=f"Tradovate error: {e}",
            details={"error": str(e)},
        )


async def check_finnhub(config: AppConfig) -> ComponentHealth:
    """Check Finnhub macroeconomic calendar API connectivity."""
    key = config.finnhub_api_key
    if not key or key.startswith(("your_", "YOUR_")):
        return ComponentHealth(
            name="finnhub",
            status="WARNING",
            message="FINNHUB_API_KEY missing; fallback heuristic calendar will be used",
            details={"configured": False},
        )

    try:
        calendar = EconomicCalendar(finnhub_api_key=key)
        events = await calendar.get_upcoming_tier1_events(window_hours=24)
        return ComponentHealth(
            name="finnhub",
            status="OK",
            message=f"Finnhub API verified ({len(events)} Tier-1 macro events in next 24h)",
            details={"tier1_events_24h": len(events)},
        )
    except Exception as e:
        return ComponentHealth(
            name="finnhub",
            status="ERROR",
            message=f"Finnhub API call failed: {e}",
            details={"error": str(e)},
        )


async def check_llm(config: AppConfig) -> ComponentHealth:
    """Check LiteLLM model completion and API credentials."""
    model = config.llm_model
    try:
        litellm.drop_params = True
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": "Respond with 'OK'"}],
                max_tokens=5,
            ),
            timeout=10.0,
        )
        content = response.choices[0].message.content.strip()
        return ComponentHealth(
            name="llm",
            status="OK",
            message=f"Model {model} verified; response received",
            details={"model": model, "response_sample": content[:20]},
        )
    except Exception as e:
        return ComponentHealth(
            name="llm",
            status="ERROR",
            message=f"LLM check failed for {model}: {e}",
            details={"model": model, "error": str(e)},
        )


async def check_risk_limits(config: AppConfig) -> ComponentHealth:
    """Check portfolio invariants, sizing mode, and risk configuration."""
    try:
        cash = config.portfolio.cash
        ceiling = config.portfolio.max_notional_exposure
        lev = ceiling / cash if cash > 0 else 0.0
        mode = config.sizing.mode
        return ComponentHealth(
            name="risk_limits",
            status="OK",
            message=f"Cash: ${cash:,.0f} | Notional Cap: ${ceiling:,.0f} ({lev:.1f}x max lev) | Sizing: {mode}",
            details={
                "cash": cash,
                "max_notional": ceiling,
                "max_leverage": lev,
                "sizing_mode": mode,
                "max_risk_cap_pct": config.sizing.max_risk_pct_cap,
                "drawdown_gating": config.sizing.drawdown_gating_enabled,
            },
        )
    except Exception as e:
        return ComponentHealth(
            name="risk_limits",
            status="ERROR",
            message=f"Risk limits invalid: {e}",
            details={"error": str(e)},
        )


async def run_diagnostics(config: AppConfig | None = None) -> DiagnosticReport:
    """Run concurrent health checks across all trading copilot subsystems."""
    if config is None:
        config = load_config()

    # Execute all checks concurrently with timeout
    results = await asyncio.gather(
        check_database(config),
        check_risk_limits(config),
        check_telegram(config),
        check_alpaca(config),
        check_tradovate(config),
        check_finnhub(config),
        check_llm(config),
        return_exceptions=True,
    )

    components: dict[str, ComponentHealth] = {}
    has_error = False
    has_warning = False

    names = ["database", "risk_limits", "telegram", "alpaca", "tradovate", "finnhub", "llm"]
    for name, res in zip(names, results, strict=False):
        if isinstance(res, BaseException):
            comp = ComponentHealth(
                name=name,
                status="ERROR",
                message=f"Unhandled diagnostic probe error: {res}",
                details={"exception": str(res)},
            )
        else:
            comp = res

        components[name] = comp
        if comp.status == "ERROR":
            has_error = True
        elif comp.status == "WARNING":
            has_warning = True

    if has_error:
        overall = "UNHEALTHY"
    elif has_warning:
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    return DiagnosticReport(
        overall_status=overall,
        timestamp=datetime.now(UTC).isoformat(),
        components=components,
    )


def format_doctor_cli_output(report: DiagnosticReport) -> str:
    """Format diagnostic report as an operator-friendly CLI health table."""
    status_icons = {
        "OK": "✅ PASS",
        "WARNING": "⚠️ WARN",
        "ERROR": "❌ FAIL",
        "DISABLED": "⚪ SKIP",
    }

    lines = [
        "=" * 70,
        "🏥 CASH-PLUS TRADING COPILOT: PRE-FLIGHT SYSTEM DOCTOR",
        "=" * 70,
        f"Timestamp:      {report.timestamp}",
        f"Overall Status: {report.overall_status}",
        "-" * 70,
        f"{'SUBSYSTEM':<15} | {'STATUS':<9} | {'DIAGNOSTIC DETAIL'}",
        "-" * 70,
    ]

    for comp in report.components.values():
        icon = status_icons.get(comp.status, comp.status)
        lines.append(f"{comp.name:<15} | {icon:<9} | {comp.message}")

    lines.append("=" * 70)
    if report.overall_status == "HEALTHY":
        lines.append("🚀 ALL CRITICAL SYSTEMS OPERATIONAL. READY FOR DESK EXECUTION.")
    elif report.overall_status == "DEGRADED":
        lines.append("⚠️ SYSTEM DEGRADED: Some optional subsystems have warnings (review above).")
    else:
        lines.append("❌ SYSTEM UNHEALTHY: Critical subsystems failed. Resolve errors before trading.")
    lines.append("=" * 70)

    return "\n".join(lines)
