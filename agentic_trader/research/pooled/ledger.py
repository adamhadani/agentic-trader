"""The pooled campaign's journal ledger: ``AlphaRepository`` calls made from the campaign's thread.

The campaign's stages run in a worker thread (``asyncio.to_thread``). Each ledger call is
scheduled on the event loop that owns the repository and waited for, so a charge is
committed before its formula is evaluated, and the confirmation consumption is committed
before its window is read.

**Abort.** A worker thread cannot be cancelled. When the campaign is cancelled it calls
``abort()``: from then on no journal write starts, and the worker's next ledger call raises
``CampaignAborted``. A write that had already started is allowed to finish, bounded by
``timeout``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine, Mapping
from datetime import date
from typing import Any

from agentic_trader.storage.alpha import AlphaRepository


__all__ = ["JOURNAL_CALL_TIMEOUT", "CampaignAborted", "JournalLedger"]

JOURNAL_CALL_TIMEOUT = 300.0  # seconds one journal write may take once it has started


class CampaignAborted(Exception):
    """The campaign was cancelled: this journal write was refused before it started."""


class JournalLedger:
    """The campaign ``Ledger`` and ``Charger`` over the journal, for one campaign id."""

    def __init__(
        self,
        repository: AlphaRepository,
        loop: asyncio.AbstractEventLoop,
        campaign_id: str,
        *,
        timeout: float = JOURNAL_CALL_TIMEOUT,
    ):
        self._repository = repository
        self._loop = loop
        self._campaign_id = campaign_id
        self._timeout = timeout
        self._aborted = threading.Event()

    def abort(self) -> None:
        """Refuse every later journal write; one already started may finish."""
        self._aborted.set()

    @property
    def aborted(self) -> bool:
        return self._aborted.is_set()

    def _call(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            coroutine.close()
            raise RuntimeError("JournalLedger must be called from a worker thread, not its event loop")
        if self._aborted.is_set():
            coroutine.close()
            raise CampaignAborted(f"campaign {self._campaign_id} was cancelled; no further journal write starts")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(
                f"a journal write for campaign {self._campaign_id} did not finish within {self._timeout:g} s"
            ) from exc

    def reserve_family(self, family_id: str, budget: int) -> None:
        self._call(self._repository.reserve_pooled_family(self._campaign_id, family_id, budget))

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None:
        self._call(
            self._repository.charge_pooled_formula(
                self._campaign_id, family_id, formula_id=formula_id, expression=expression, nodes=nodes
            )
        )

    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None:
        if campaign_id != self._campaign_id:
            raise ValueError(f"this ledger belongs to campaign {self._campaign_id}, not {campaign_id}")
        self._call(
            self._repository.consume_pooled_confirmation(
                campaign_id=campaign_id, cohort_sha256=cohort_sha256, interval=interval, candidates=candidates
            )
        )

    def advance(self, status: str, detail: Mapping | None = None) -> None:
        self._call(self._repository.advance_pooled_campaign(self._campaign_id, status, detail))
