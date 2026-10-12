"""Read-only broker importer; remote I/O never holds a storage transaction."""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from agentic_trader.accounting.ledger import LedgerReport, reconcile
from agentic_trader.accounting.risk import AccountRiskSnapshot, require_risk_checkpoint
from agentic_trader.broker.base import BaseBroker
from agentic_trader.config import AccountingConfig
from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.storage.ledger import LedgerStore


if TYPE_CHECKING:
    from agentic_trader.storage.corporate_actions import CorporateActionsStore

logger = logging.getLogger(__name__)


class AccountLedgerService:
    def __init__(
        self,
        broker: BaseBroker,
        store: LedgerStore,
        config: AccountingConfig,
        *,
        corporate_actions: CorporateActionsStore | None = None,
    ):
        self.broker, self.store, self.config = broker, store, config
        self.corporate_actions = corporate_actions
        self._lock = asyncio.Lock()

    async def _corporate_actions(self) -> tuple[CorporateAction, ...]:
        """The latest capture's actions for the issue text.

        A read or parse failure degrades to none (type name logged): the evidence only explains an
        issue, so it must never fail ``refresh()``, which runs before every Alpaca entry preflight.
        """
        if self.corporate_actions is None:
            return ()
        try:
            capture = await self.corporate_actions.latest_capture()
            return tuple(CorporateAction.model_validate(row) for row in (capture or {}).get("actions", []))
        except Exception as exc:
            logger.warning(
                "Corporate-action capture unavailable to the ledger",
                extra={"event": "ledger_corporate_actions_unavailable", "reason": type(exc).__name__},
            )
            return ()

    async def refresh(self) -> LedgerReport:
        async with self._lock:
            # Fence even the first remote read, then pin identity before importing.
            token = await self.store.begin()
            try:
                before = await self.broker.account_snapshot()
                await self.store.bind(token, before.account_id)
                activities = await self.broker.account_activities(max_pages=self.config.max_pages)
                after = await self.broker.account_snapshot()
                actions = await self._corporate_actions()
                report = await asyncio.to_thread(
                    reconcile,
                    activities,
                    after,
                    cash_tolerance=Decimal(str(self.config.cash_tolerance)),
                    corporate_actions=actions,
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
