from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import litellm
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from pydantic import BaseModel, Field
from telegram import Bot

from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import APP_DISPLAY_NAME
from agentic_trader.market.session import CompositeMarketCalendar, FinnhubCalendarProvider
from agentic_trader.resilience.fallback import AllFallbacksExhaustedError, RetryPolicy, RunnableWithFallbacks
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.transport.alpaca import BoundedStockDataClient, BoundedTradingClient


logger = logging.getLogger(__name__)
RECENT_ACCESS_WINDOW = timedelta(minutes=1)


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
    """Check database persistence layer and table connectivity."""
    try:
        db = await asyncio.to_thread(SignalDatabase, config=config)
        try:
            active_count = await db.get_active_position_count()
            exposure = await db.get_active_notional_exposure()
            alpha = await AlphaRepository(db.workflows, config.alpha_pipeline).status()
        finally:
            await db.engine.dispose()
        return ComponentHealth(
            name="database",
            status="OK",
            message="Database connection verified; schema migrated to head",
            details={
                "active_positions": active_count,
                "alpha_pipeline": alpha,
                "open_notional": exposure,
                "backend": "postgresql" if config.resolved_db_url.startswith("postgresql") else "sqlite",
            },
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status="ERROR",
            message=f"Database check failed: {type(e).__name__}",
            details={"error": type(e).__name__},
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
        async with Bot(token=token) as bot:
            me = await bot.get_me()
        return ComponentHealth(
            name="telegram",
            status="OK",
            message="Telegram bot identity verified",
            details={"configured": True, "is_bot": me.is_bot},
        )
    except Exception as e:
        return ComponentHealth(
            name="telegram",
            status="ERROR",
            message=f"Telegram bot validation failed: {type(e).__name__}",
            details={"error": type(e).__name__},
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
            message=f"Alpaca authentication error: {type(e).__name__}",
            details={"error": type(e).__name__},
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
            message=f"Tradovate error: {type(e).__name__}",
            details={"error": type(e).__name__},
        )


async def _probe_bar_access(
    config: AppConfig, client: StockHistoricalDataClient | None, *, feed: str, end: datetime, name: str
) -> dict[str, Any]:
    """One exact one-minute bar request on ``feed`` ending at ``end``, with no retry or fallback.

    Returns ``{access: True, observations}`` or ``{access: False, error_type, http_status}``. An empty
    successful response proves request access, not current price coverage. An owned client closes inside the
    worker, even when the caller deadline expires.
    """
    request = StockBarsRequest(
        symbol_or_symbols=config.market_data.probe_symbol,
        timeframe=TimeFrame.Minute,
        start=end - RECENT_ACCESS_WINDOW,
        end=end,
        limit=1,
        feed=DataFeed(feed),
        adjustment=Adjustment.RAW,
    )

    def probe():
        reader = (
            client
            if client is not None
            else BoundedStockDataClient(
                config.alpaca_api_key, config.alpaca_api_secret, request_timeout=config.market_data.timeout_seconds
            )
        )
        try:
            bars = reader.get_stock_bars(request)
            if not isinstance(bars, BarSet):
                raise TypeError("Typed Alpaca bar response required")
            return sum(len(rows) for rows in bars.data.values())
        finally:
            if client is None:
                reader._session.close()

    try:
        observations = await RunnableWithFallbacks(
            probe,
            primary_name=name,
            retry_policy=RetryPolicy(max_retries=0, timeout_seconds=config.market_data.timeout_seconds),
        ).ainvoke()
        return {"access": True, "observations": observations}
    except AllFallbacksExhaustedError as failure:
        error = failure.errors[-1]
        return {"access": False, "error_type": type(error).__name__, "http_status": getattr(error, "status_code", None)}


async def check_market_data(
    config: AppConfig, *, client: StockHistoricalDataClient | None = None, now: datetime | None = None
) -> ComponentHealth:
    """Probe exact Alpaca data access, without fallback or a freshness claim.

    Under ``scan_bars: configured`` one probe: a recent request on the configured feed, which is both the scan
    bars and (with ``ALPACA_DATA_FEED`` setting both) the stand-in for broker quotes; ``details["recent_access"]``
    is its result. Under ``sip_delayed`` two probes in this one component, run together: the scan bars (delayed
    SIP, ending ``sip_delay_minutes`` before now) and a recent request on ``alpaca_data_feed``, standing in for
    admission's real-time latest trade and quote. The status is the worse of the two, the message names both,
    and ``details["access"]`` holds each probe's result with its feed and a ``delayed`` flag.
    """
    market_data = config.market_data
    feed = f"alpaca:{market_data.bars_feed}"
    if client is None and not (config.alpaca_api_key and config.alpaca_api_secret):
        return ComponentHealth(name="market_data", status="DISABLED", message="Alpaca data credentials not configured")
    now = now or datetime.now(UTC)
    delay = market_data.bars_end_delay
    if delay is None:
        result = await _probe_bar_access(
            config, client, feed=market_data.bars_feed, end=now, name="alpaca_recent_access"
        )
        details: dict[str, Any] = {"feed": feed, "recent_access": result.pop("access"), "freshness_verified": False}
        details.update(result)
        if details["recent_access"]:
            return ComponentHealth(
                name="market_data",
                status="OK",
                message=f"Recent {feed} request permitted; price freshness requires separate observations",
                details=details,
            )
        return ComponentHealth(
            name="market_data",
            status="ERROR",
            message=f"Recent {feed} request failed; verify feed entitlement and transport. No fallback used",
            details=details,
        )

    minutes = delay // timedelta(minutes=1)
    quotes_feed = f"alpaca:{config.alpaca_data_feed}"
    scan_bars, quotes = await asyncio.gather(
        _probe_bar_access(config, client, feed=market_data.bars_feed, end=now - delay, name="alpaca_delayed_access"),
        _probe_bar_access(config, client, feed=config.alpaca_data_feed, end=now, name="alpaca_recent_access"),
    )
    details = {
        "feed": feed,
        "end_delay_minutes": minutes,
        "freshness_verified": False,
        "access": {
            "scan_bars": {"feed": feed, "delayed": True, **scan_bars},
            "quotes": {"feed": quotes_feed, "delayed": False, **quotes},
        },
    }
    probed = (
        f"Delayed {feed} request (ending {minutes} min ago) {'permitted' if scan_bars['access'] else 'failed'}; "
        f"recent {quotes_feed} request {'permitted' if quotes['access'] else 'failed'}"
    )
    if scan_bars["access"] and quotes["access"]:
        return ComponentHealth(
            name="market_data",
            status="OK",
            message=f"{probed}; price freshness requires separate observations",
            details=details,
        )
    return ComponentHealth(
        name="market_data",
        status="ERROR",
        message=f"{probed}; verify feed entitlement and transport. No fallback used",
        details=details,
    )


async def check_finnhub(config: AppConfig) -> ComponentHealth:
    """Check the actual Finnhub exchange-holiday provider, without fallback."""
    key = config.finnhub_api_key
    if not key or key.startswith(("your_", "YOUR_")):
        return ComponentHealth(
            name="finnhub",
            status="WARNING",
            message="FINNHUB_API_KEY missing; Finnhub exchange-holiday provider unavailable",
            details={"configured": False},
        )

    try:
        calendar = FinnhubCalendarProvider(api_key=key)
        today = datetime.now(UTC).date()
        # Include a weekday even when diagnostics run over a weekend.
        days = await calendar.get_calendar_range(today, today + timedelta(days=7))
        return ComponentHealth(
            name="finnhub",
            status="OK",
            message="Finnhub exchange-holiday API verified",
            details={"calendar_days": len(days), "provider": "finnhub_api"},
        )
    except Exception as e:
        return ComponentHealth(
            name="finnhub",
            status="ERROR",
            message=f"Finnhub API call failed: {type(e).__name__}",
            details={"error": type(e).__name__},
        )


async def check_llm(config: AppConfig) -> ComponentHealth:
    """Check LiteLLM model completion and API credentials."""
    model = config.llm_model
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                api_key=config.llm_api_key,
                model=model,
                messages=[{"role": "user", "content": "Respond with 'OK'"}],
                max_tokens=5,
                drop_params=True,
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
            message=f"LLM check failed for {model}: {type(e).__name__}",
            details={"model": model, "error": type(e).__name__},
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
            message=f"Risk capital: ${cash:,.0f} | Notional Cap: ${ceiling:,.0f} ({lev:.1f}x max lev) | Sizing: {mode}",
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
            message=f"Risk limits invalid: {type(e).__name__}",
            details={"error": type(e).__name__},
        )


async def check_market_calendar(config: AppConfig) -> ComponentHealth:
    """Check exchange holiday and session calendar provider resolution."""
    try:
        alpaca_client = None
        if config.alpaca_api_key and config.alpaca_api_secret and not config.alpaca_api_key.startswith("your_"):
            try:
                alpaca_client = BoundedTradingClient(
                    request_timeout=config.market_data.timeout_seconds,
                    api_key=config.alpaca_api_key,
                    secret_key=config.alpaca_api_secret,
                    paper=config.alpaca_paper,
                )
            except Exception:
                alpaca_client = None

        finnhub_key = None
        if config.finnhub_api_key and not config.finnhub_api_key.startswith("your_"):
            finnhub_key = config.finnhub_api_key

        cal = CompositeMarketCalendar(
            alpaca_client=alpaca_client,
            finnhub_api_key=finnhub_key,
            primary_provider=config.session.calendar_provider,
            fallback_providers=config.session.fallback_providers,
        )
        today = datetime.now(UTC).date()
        day_info = await cal.get_trading_day(today)
        return ComponentHealth(
            name="market_calendar",
            status="OK",
            message=f"Calendar resolved date {today} via {day_info.source} (is_trading_day={day_info.is_trading_day})",
            details={
                "provider": day_info.source,
                "is_trading_day": day_info.is_trading_day,
                "is_early_close": day_info.is_early_close,
                "holiday_name": day_info.holiday_name,
            },
        )
    except Exception as e:
        return ComponentHealth(
            name="market_calendar",
            status="ERROR",
            message=f"Market calendar resolution failed: {type(e).__name__}",
            details={"error": type(e).__name__},
        )


async def run_diagnostics(config: AppConfig | None = None) -> DiagnosticReport:
    """Run concurrent health checks across all trading copilot subsystems."""
    if config is None:
        config = load_config()

    # Execute all checks concurrently with timeout
    results = await asyncio.gather(
        check_database(config),
        check_risk_limits(config),
        check_market_calendar(config),
        check_telegram(config),
        check_alpaca(config),
        check_market_data(config),
        check_tradovate(config),
        check_finnhub(config),
        check_llm(config),
        return_exceptions=True,
    )

    components: dict[str, ComponentHealth] = {}
    has_error = False
    has_warning = False

    names = [
        "database",
        "risk_limits",
        "market_calendar",
        "telegram",
        "alpaca",
        "market_data",
        "tradovate",
        "finnhub",
        "llm",
    ]
    for name, res in zip(names, results, strict=False):
        if isinstance(res, BaseException):
            comp = ComponentHealth(
                name=name,
                status="ERROR",
                message=f"Unhandled diagnostic probe error: {type(res).__name__}",
                details={"exception": type(res).__name__},
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
        f"🏥 {APP_DISPLAY_NAME.upper()}: PRE-FLIGHT SYSTEM DOCTOR",
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
        lines.append("🚀 ACTIVE PROBES PASSED. CHECK DAEMON READINESS AND ENTRY CONDITIONS SEPARATELY.")
    elif report.overall_status == "DEGRADED":
        lines.append("⚠️ SYSTEM DEGRADED: Some optional subsystems have warnings (review above).")
    else:
        lines.append("❌ SYSTEM UNHEALTHY: Critical subsystems failed. Resolve errors before trading.")
    lines.append("=" * 70)

    return "\n".join(lines)
