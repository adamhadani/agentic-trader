"""Read-only broker importer; remote I/O never holds a storage transaction."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from agentic_trader.accounting.ledger import LedgerReport, reconcile
from agentic_trader.accounting.risk import AccountRiskSnapshot, require_risk_checkpoint
from agentic_trader.broker.base import BaseBroker
from agentic_trader.config import AccountingConfig
from agentic_trader.storage.ledger import LedgerStore


class AccountLedgerService:
    def __init__(self, broker: BaseBroker, store: LedgerStore, config: AccountingConfig):
        self.broker, self.store, self.config = broker, store, config
        self._lock = asyncio.Lock()

    async def refresh(self) -> LedgerReport:
        async with self._lock:
            # Fence even the first remote read, then pin identity before importing.
            token = await self.store.begin()
            try:
                before = await self.broker.account_snapshot()
                await self.store.bind(token, before.account_id)
                activities = await self.broker.account_activities(max_pages=self.config.max_pages)
                after = await self.broker.account_snapshot()
                report = await asyncio.to_thread(
                    reconcile, activities, after, cash_tolerance=Decimal(str(self.config.cash_tolerance))
                )
                if before.accounting_identity() != after.accounting_identity():
                    report.issues.append("Broker accounting changed during import; retry on next refresh")
                    report.gross_realized = report.net_realized = None
                if before.account_id != after.account_id:
                    raise ValueError("Broker account changed during import")
                accepted = await self.store.commit(
                    token,
                    activities,
                    {"snapshot": after.model_dump(mode="json"), "report": report.model_dump(mode="json")},
                )
                if not accepted:
                    raise RuntimeError("Ledger refresh superseded by another importer")
                return report
            except Exception as exc:
                await self.store.fail(token, type(exc).__name__)
                raise

    async def current(self) -> tuple[LedgerReport | None, str]:
        status = await self.store.status()
        if status.get("error"):
            return None, f"Last activity import failed: {status['error']}"
        if not status.get("report"):
            return None, "Awaiting account activity import"
        report = LedgerReport.model_validate(status["report"])
        age = (datetime.now(UTC) - report.observed_at).total_seconds()
        if not 0 <= age <= self.config.max_age_seconds:
            return None, "Account activity reconciliation is stale"
        return report, ""

    async def current_risk(self) -> AccountRiskSnapshot:
        """Fresh account risk evidence for entry admission; unavailable evidence raises."""
        return require_risk_checkpoint(await self.store.status(), max_age_seconds=self.config.max_age_seconds)
