import argparse
import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentic_trader.agent.calendar import EconomicCalendar
from agentic_trader.agent.evaluator import LLMTradeEvaluation, RiskEvaluator
from agentic_trader.config import AppConfig, load_config
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_terminal_card
from agentic_trader.screeners.strategies import StrategyEngine
from agentic_trader.storage.db import SignalDatabase


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("copilot")


class FuturesCopilot:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db = SignalDatabase(config.db_path)
        self.data_fetcher = MarketDataFetcher()
        self.strategy_engine = StrategyEngine(config)
        self.calendar = EconomicCalendar(finnhub_api_key=config.finnhub_api_key)
        self.evaluator = RiskEvaluator(config, calendar=self.calendar)
        self.notifier = TelegramNotifier(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            db=self.db,
            portfolio_cash=config.portfolio.cash,
        )

    async def run_scan(self, use_llm: bool = True, dry_run: bool = False):
        logger.info("=== Starting Quantitative Scan ===")
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_contract_count()
        logger.info(
            f"Portfolio Status: {active_count} active contracts | "
            f"Open Notional: ${current_exposure:,.2f} / ${self.config.portfolio.max_notional_exposure:,.2f} max"
        )

        in_lockout, lock_event = await self.calendar.is_in_lockout_window(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
        if in_lockout and lock_event:
            logger.warning(
                f"Macro Lockout Active: '{lock_event.title}' at {lock_event.timestamp.strftime('%H:%M UTC')}. "
                "No entry alerts will be emitted during this window."
            )
            return

        total_candidates = 0
        total_alerts = 0

        for contract, info in self.config.contracts.items():
            logger.info(f"Scanning contract {contract} ({info.name} - {info.ticker})...")
            try:
                data = self.data_fetcher.fetch_data(contract, info.ticker)
                if data.daily.empty or data.four_hour.empty:
                    logger.warning(f"Insufficient data for {contract}, skipping.")
                    continue

                candidates = self.strategy_engine.scan_contract(data)
                for candidate in candidates:
                    total_candidates += 1
                    logger.info(
                        f"Found setup: {candidate.contract} {candidate.direction} "
                        f"via {candidate.strategy} at {candidate.current_price:.2f}"
                    )

                    # Deduplication check
                    is_dup = await self.db.is_duplicate_recent(
                        candidate.contract,
                        candidate.strategy,
                        hours=self.config.risk.deduplication_hours,
                    )
                    if is_dup:
                        logger.info(
                            f"Skipping duplicate signal: {candidate.contract} {candidate.strategy} "
                            f"already alerted within {self.config.risk.deduplication_hours} hours."
                        )
                        continue

                    # Risk evaluation
                    eval_res = await self.evaluator.evaluate_candidate(
                        candidate,
                        current_open_notional=current_exposure,
                        use_llm=use_llm,
                    )

                    if not eval_res.approved:
                        logger.info(f"Candidate rejected by risk engine: {eval_res.rejection_reason}")
                        continue

                    if dry_run:
                        logger.info("[DRY RUN] Approved signal would be emitted:")
                        print(format_terminal_card(eval_res, candidate.strategy, self.config.portfolio.cash))
                        continue

                    # Record to SQLite database
                    sig_id = await self.db.record_signal(
                        contract=eval_res.contract,
                        strategy=candidate.strategy,
                        direction=eval_res.direction,
                        entry_price=eval_res.entry_price,
                        stop_loss=eval_res.stop_loss,
                        take_profit=eval_res.take_profit,
                        risk_dollars=eval_res.risk_dollars,
                        reward_dollars=eval_res.reward_dollars,
                        notional_value=eval_res.notional_value,
                        status="PENDING",
                        raw_response=eval_res.model_dump_json(),
                    )

                    # Dispatch alert
                    await self.notifier.send_signal_alert(
                        eval_res=eval_res,
                        strategy=candidate.strategy,
                        signal_id=sig_id,
                    )
                    total_alerts += 1
                    # Update exposure in memory for subsequent checks in this run
                    current_exposure += eval_res.notional_value

            except Exception:
                logger.exception(f"Error scanning {contract}")

        logger.info(f"=== Scan Complete: {total_candidates} candidates evaluated, {total_alerts} alerts emitted ===")

    async def show_status(self):
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_contract_count()
        eff_leverage = current_exposure / self.config.portfolio.cash

        print("=" * 65)
        print("CASH-PLUS FUTURES COPILOT: PORTFOLIO & RISK STATUS")
        print("=" * 65)
        print(f"Cash Base:            ${self.config.portfolio.cash:,.2f}")
        print(f"Max Notional Ceiling: ${self.config.portfolio.max_notional_exposure:,.2f} (0.6x max leverage)")
        print(f"Active Exposure:      ${current_exposure:,.2f} ({eff_leverage:.2f}x effective leverage)")
        print(f"Active Position Count:{active_count} contracts")
        print(f"Telegram Configured:  {self.notifier.is_configured()}")
        print(f"LLM Model Configured: {self.config.llm_model}")
        print("-" * 65)

        macro_summary = await self.calendar.get_macro_summary_for_prompt()
        print("Macro Calendar Context:")
        print(macro_summary)
        print("-" * 65)

        signals = await self.db.get_recent_signals(limit=10)
        print(f"Recent Signals ({len(signals)}):")
        if not signals:
            print("  (No signals in database)")
        else:
            for s in signals:
                print(
                    f"  #{s['id']} [{s['status']}] {s['timestamp']} | {s['contract']} {s['direction']} "
                    f"via {s['strategy']} @ {s['entry_price']} (Risk: ${s['risk_dollars']:.2f})"
                )
        print("=" * 65)

    async def send_test_alert(self):
        print("Sending synthetic test alert card...")
        test_eval = LLMTradeEvaluation(
            approved=True,
            rejection_reason=None,
            contract="/MES",
            direction="LONG",
            entry_price=5812.50,
            stop_loss=5769.75,
            take_profit=5898.00,
            stop_distance_points=42.75,
            target_distance_points=85.50,
            risk_reward_ratio=2.0,
            risk_dollars=213.75,
            reward_dollars=427.50,
            notional_value=29062.50,
            effective_leverage=0.29,
            macro_clearance=True,
            thesis_summary="Daily trend is bullish (Price > 50 > 200 EMA). 4h RSI dipped to 42 and bounced off 20 EMA.",
        )
        sig_id = await self.db.record_signal(
            contract=test_eval.contract,
            strategy="TREND_PULLBACK_TEST",
            direction=test_eval.direction,
            entry_price=test_eval.entry_price,
            stop_loss=test_eval.stop_loss,
            take_profit=test_eval.take_profit,
            risk_dollars=test_eval.risk_dollars,
            reward_dollars=test_eval.reward_dollars,
            notional_value=test_eval.notional_value,
            status="PENDING",
        )
        await self.notifier.send_signal_alert(test_eval, "TREND_PULLBACK", sig_id)


async def async_main():
    parser = argparse.ArgumentParser(description="Cash-Plus Futures Copilot")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    scan_parser = subparsers.add_parser("scan", help="Run an immediate quantitative scan")
    scan_parser.add_argument("--no-llm", action="store_true", help="Disable LLM evaluation (use deterministic rules)")
    scan_parser.add_argument("--dry-run", action="store_true", help="Scan without persisting or emitting alerts")

    subparsers.add_parser("status", help="Display current portfolio exposure and recent signals")
    subparsers.add_parser("test-alert", help="Send a synthetic test alert to verify Telegram and formatting")
    subparsers.add_parser("listen", help="Start Telegram Bot callback listener only")

    daemon_parser = subparsers.add_parser("daemon", help="Start continuous scheduler and Telegram listener")
    daemon_parser.add_argument("--no-llm", action="store_true", help="Disable LLM evaluation")

    args = parser.parse_args()
    config = load_config()
    copilot = FuturesCopilot(config)

    if args.command == "scan":
        await copilot.run_scan(use_llm=not args.no_llm, dry_run=args.dry_run)
    elif args.command == "status":
        await copilot.show_status()
    elif args.command == "test-alert":
        await copilot.send_test_alert()
    elif args.command == "listen":
        if not copilot.notifier.is_configured() or not copilot.notifier.app or not copilot.notifier.app.updater:
            logger.error("Telegram is not configured in .envrc")
            return
        logger.info("Starting Telegram Bot listener... (press Ctrl+C to stop)")
        await copilot.notifier.app.initialize()
        await copilot.notifier.app.start()
        await copilot.notifier.app.updater.start_polling()
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt, SystemExit:
            logger.info("Stopping listener...")
            await copilot.notifier.app.updater.stop()
            await copilot.notifier.app.stop()
            await copilot.notifier.app.shutdown()
    elif args.command == "daemon":
        scheduler = AsyncIOScheduler()
        interval = config.scheduler.cron_hour_interval
        # Schedule regular scans
        scheduler.add_job(
            copilot.run_scan,
            "interval",
            hours=interval,
            args=[not args.no_llm, False],
            next_run_time=datetime.now(UTC),
        )
        scheduler.start()
        logger.info(f"Scheduler started: scanning every {interval} hours.")

        if copilot.notifier.is_configured() and copilot.notifier.app and copilot.notifier.app.updater:
            logger.info("Starting Telegram Bot listener for interactive callbacks...")
            await copilot.notifier.app.initialize()
            await copilot.notifier.app.start()
            await copilot.notifier.app.updater.start_polling()

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt, SystemExit:
            logger.info("Shutting down daemon...")

            scheduler.shutdown()
            if copilot.notifier.app and copilot.notifier.app.updater:
                await copilot.notifier.app.updater.stop()
                await copilot.notifier.app.stop()
                await copilot.notifier.app.shutdown()
    else:
        parser.print_help()


def main():
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(async_main())


if __name__ == "__main__":
    main()
