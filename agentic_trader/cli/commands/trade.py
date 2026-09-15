from __future__ import annotations

import os

import click

from agentic_trader.agent.macro_explainer import MacroExplainer
from agentic_trader.cli.utils import coro, get_copilot_and_config
from agentic_trader.config import load_config
from agentic_trader.constants import RuntimeEnvironment
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TerminalFormatter


@click.command("status", help="Show open positions, broker connection status, cash balance")
@coro
async def status() -> None:
    """Show open positions, broker connection status, cash balance."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    await copilot.show_status()


@click.command("positions", help="List open positions in database")
@coro
async def positions() -> None:
    """List open positions in database."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    await copilot.show_positions()


@click.command("close", help="Close an active position manually with an exit price")
@click.argument("signal_id", type=int)
@click.option(
    "--price",
    type=float,
    required=True,
    help="Exit price for the trade closure",
)
@coro
async def close(signal_id: int, price: float) -> None:
    """Close an active position manually with an exit price."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    res = await copilot.close_position_manual(signal_id, price)
    clean_text = res.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
    click.echo(clean_text)


@click.command("execute", help="Execute a staged signal manually by signal_id")
@click.argument("signal_id", type=int)
@click.option(
    "--qty",
    type=float,
    default=None,
    help="Override order quantity (contracts or shares) with custom sizing tier",
)
@coro
async def execute(signal_id: int, qty: float | None = None) -> None:
    """Execute a staged signal manually by signal_id."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    _success, res = await copilot.execute_signal_by_id(signal_id, quantity=qty)
    clean_text = (
        res.replace("<b>", "")
        .replace("</b>", "")
        .replace("<code>", "")
        .replace("</code>", "")
        .replace("<i>", "")
        .replace("</i>", "")
        .replace("• ", "  * ")
    )
    click.echo(clean_text)


@click.command("panic", help="Emergency kill switch: cancel resting orders, liquidate positions & halt trading")
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Bypass confirmation prompt and immediately execute emergency liquidation & halt",
)
@click.option(
    "--reason",
    default="CLI manual emergency panic trigger",
    help="Reason string for emergency audit log",
)
@coro
async def panic(confirm: bool, reason: str) -> None:
    """Emergency kill switch: cancel resting orders, liquidate positions & halt trading."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()

    if not confirm:
        active_count = await copilot.db.get_active_position_count()
        exposure = await copilot.db.get_active_notional_exposure()
        click.secho("⚠️  EMERGENCY KILL SWITCH WARNING ⚠️", fg="red", bold=True)
        click.echo(f"Active Positions: {active_count} | Total Open Notional: ${exposure:,.2f}")
        click.echo("This will CANCEL all resting orders, LIQUIDATE all open positions at market, and HALT trading.")
        if not click.confirm("Are you sure you want to proceed with emergency liquidation?", default=False):
            click.echo("Emergency kill switch aborted.")
            return

    view = await copilot.emergency_panic_halt(reason=reason)
    click.echo(TerminalFormatter.format_panic_report(view))


@click.command("resume", help="Clear emergency halt and restore automated trading operations")
@coro
async def resume() -> None:
    """Clear emergency halt and restore automated trading operations."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    res = await copilot.resume_trading()
    click.secho("🟢 Trading operations resumed successfully.", fg="green", bold=True)
    click.echo(res.get("message", ""))


@click.command("test-alert", help="Preview a diagnostic notification, or send to a dedicated test bot")
@click.option("--send", is_flag=True, help="Send using TELEGRAM_TEST_BOT_TOKEN and TELEGRAM_TEST_CHAT_ID")
@coro
async def test_alert(send: bool = False) -> None:

    message = "[TEST] Synthetic notification check. No signal or order was created."
    if not send:
        click.echo(message)
        return
    config = load_config()
    token, chat = os.environ.get("TELEGRAM_TEST_BOT_TOKEN"), os.environ.get("TELEGRAM_TEST_CHAT_ID")
    if not token or not chat or token == config.telegram_bot_token or chat == config.telegram_chat_id:
        raise click.ClickException(
            "Configure a dedicated TELEGRAM_TEST_BOT_TOKEN and TELEGRAM_TEST_CHAT_ID, different from production."
        )
    notifier = TelegramNotifier(token, chat, environment=RuntimeEnvironment.TEST)
    if not await notifier.send_message(message):
        raise click.ClickException("Test notification delivery failed.")


@click.command("explain-macro", help="Generate an educational macro tutorial and indicator breakdown")
@coro
async def explain_macro() -> None:
    """Generate an educational macro tutorial and indicator breakdown."""
    copilot, config = get_copilot_and_config()
    click.echo("\n🔍 Evaluating institutional macro indicators and generating briefing...")
    report = await copilot.regime_detector.macro_engine.get_macro_report()
    explainer = MacroExplainer(config=config)
    explanation = await explainer.explain(report, format_mode="text")
    click.echo("\n" + explanation + "\n")
