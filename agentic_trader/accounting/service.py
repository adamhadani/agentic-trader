"""Read-only broker importer; remote I/O never holds a storage transaction."""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ValidationError

from agentic_trader.accounting.ledger import AccountSnapshot, LedgerReport, reconcile
from agentic_trader.accounting.risk import AccountRiskSnapshot, require_risk_checkpoint
from agentic_trader.broker.base import BaseBroker
from agentic_trader.config import AccountingConfig
from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.storage.ledger import LedgerStore


if TYPE_CHECKING:
    from agentic_trader.storage.corporate_actions import CorporateActionsStore

logger = logging.getLogger(__name__)

CHANGED_DURING_IMPORT = "Broker accounting changed during import; retry on next refresh"
FEED_BEHIND = "Activity feed behind a recent account change; retry on next refresh"
# The only issues a lagging activity feed produces; any other issue cannot be cleared by re-reading.
_LAG_ISSUES = ("Cash mismatch:", "Quantity mismatch:")


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

    async def _committed_identity(self, account_id: str) -> tuple | None:
        """The accounting identity of the last committed snapshot of this account; None without a usable one."""
        raw = (await self.store.status()).get("snapshot")
        try:
            snapshot = AccountSnapshot.model_validate(raw) if raw else None
        except ValidationError:
            return None
        return snapshot.accounting_identity() if snapshot and snapshot.account_id == account_id else None

    async def refresh(self) -> LedgerReport:
        """Import the activity history, reconcile it against the account and persist one checkpoint.

        A fill reaches the account a moment before the activity feed, so an import that reads across it is
        torn (2026-11-23: CHYM's fill refused the next two auto-entries on such a checkpoint). While a read
        crosses an account change (its two snapshots differ, or the account moved since the last committed
        checkpoint) and shows only cash or quantity mismatches, the whole import is re-read (GETs only), up to
        ``import_attempts`` reads ``import_retry_delay_seconds`` apart, under the same lock and fencing token.
        Only the last read is committed, so no reader sees a torn one. Exceptions are never retried here.
        """
        async with self._lock:
            # Fence even the first remote read, then pin identity before importing.
            token = await self.store.begin()
            try:
                actions = await self._corporate_actions()
                origin = None
                for attempt in range(1, self.config.import_attempts + 1):
                    before = await self.broker.account_snapshot()
                    await self.store.bind(token, before.account_id)
                    if origin is None:
                        origin = await self._committed_identity(before.account_id) or before.accounting_identity()
                    activities = await self.broker.account_activities(max_pages=self.config.max_pages)
                    after = await self.broker.account_snapshot()
                    if before.account_id != after.account_id:
                        raise ValueError("Broker account changed during import")
                    report = await asyncio.to_thread(
                        reconcile,
                        activities,
                        after,
                        cash_tolerance=Decimal(str(self.config.cash_tolerance)),
                        corporate_actions=actions,
                    )
                    changed = before.accounting_identity() != after.accounting_identity()
                    if not changed and report.ready:
                        break
                    lagging = (changed or after.accounting_identity() != origin) and all(
                        issue.startswith(_LAG_ISSUES) for issue in report.issues
                    )
                    if changed:
                        report.issues.append(CHANGED_DURING_IMPORT)
                        report.gross_realized = report.net_realized = None
                    if not lagging:
                        break
                    if attempt == self.config.import_attempts:
                        if not changed:
                            report.issues.append(FEED_BEHIND)
                        break
                    logger.info(
                        "Ledger import read across an account change; re-reading",
                        extra={"event": "ledger_import_retried", "attempt": attempt, "torn": changed},
                    )
                    await asyncio.sleep(self.config.import_retry_delay_seconds)
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
