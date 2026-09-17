"""Bounded capacity for blocking, read-only provider calls.

Python cannot kill a running thread. A caller deadline discards the late result,
while the worker retains its capacity slot until the transport actually finishes.
Socket deadlines remain mandatory; this is never an order-mutation retry mechanism.
"""

from __future__ import annotations

import inspect
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore
from typing import Any


DEFAULT_READ_WORKERS = 4
logger = logging.getLogger(__name__)


class ReadDeadlineExceeded(TimeoutError):
    """An unfinished read must not be replayed by the fallback wrapper."""


class ReadCapacityExceeded(RuntimeError):
    """All workers are still busy; fail fast instead of accumulating a queue."""


class BoundedReadExecutor:
    def __init__(self, workers: int = DEFAULT_READ_WORKERS):
        self._slots = BoundedSemaphore(workers)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="provider-read")

    def submit(self, function, /, *args, **kwargs) -> Future[Any]:
        if not self._slots.acquire(blocking=False):
            raise ReadCapacityExceeded("Read capacity exhausted by unfinished provider calls")
        try:
            future = self._executor.submit(copy_context().run, function, *args, **kwargs)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _: self._slots.release())
        return future

    def shutdown(self):
        """Drain owned readers during explicit shutdown, never during a timeout."""
        self._executor.shutdown(wait=True, cancel_futures=True)


def discard_late_read(future: Future[Any]) -> None:
    future.cancel()

    def completed(done: Future[Any]):
        if not done.cancelled():
            error = done.exception()
            result = done.result() if error is None else None
            if inspect.iscoroutine(result):
                result.close()
            logger.info(
                "Discarded late provider read result",
                extra={"event": "provider_read_discarded", "error_type": type(error).__name__ if error else None},
            )

    future.add_done_callback(completed)


# Shared process capacity: constructing a fallback for each symbol cannot create
# an unbounded set of worker pools. Tests can inject and explicitly drain a pool.
provider_reads = BoundedReadExecutor()
