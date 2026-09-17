from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agentic_trader.constants import (
    DEFAULT_DATA_MAX_RETRIES,
    DEFAULT_DATA_RETRY_BACKOFF_FACTOR,
    DEFAULT_DATA_TIMEOUT_SECONDS,
)
from agentic_trader.resilience.reads import (
    BoundedReadExecutor,
    ReadCapacityExceeded,
    ReadDeadlineExceeded,
    discard_late_read,
    provider_reads,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class FallbackError(Exception):
    """Base exception for fallback runner failures."""


class AllFallbacksExhaustedError(FallbackError):
    """Raised when primary and all fallback providers fail."""

    def __init__(self, message: str, errors: list[Exception]):
        super().__init__(message)
        self.errors = errors


@dataclass
class RetryPolicy:
    """Configurable retry and timeout policy."""

    max_retries: int = DEFAULT_DATA_MAX_RETRIES
    backoff_factor: float = DEFAULT_DATA_RETRY_BACKOFF_FACTOR
    timeout_seconds: float = DEFAULT_DATA_TIMEOUT_SECONDS
    exceptions_to_retry: tuple[type[Exception], ...] = field(default_factory=lambda: (Exception,))


class RunnableWithFallbacks[T, R]:
    """
    LangChain-inspired execution wrapper that runs a primary callable and cascades
    to an ordered list of fallback callables upon failure, with configurable timeout,
    retries, exponential backoff, and structured logging.
    """

    def __init__(
        self,
        primary: Callable[..., R | Awaitable[R]],
        fallbacks: Sequence[Callable[..., R | Awaitable[R]]] | None = None,
        retry_policy: RetryPolicy | None = None,
        primary_name: str = "primary",
        fallback_names: list[str] | None = None,
        read_executor: BoundedReadExecutor | None = None,
    ):
        self.primary = primary
        self.fallbacks = list(fallbacks) if fallbacks else []
        self.retry_policy = retry_policy or RetryPolicy()
        self.primary_name = primary_name
        self.read_executor = read_executor if read_executor is not None else provider_reads

        if fallback_names and len(fallback_names) == len(self.fallbacks):
            self.fallback_names = fallback_names
        else:
            self.fallback_names = [getattr(fb, "__name__", f"fallback_{i + 1}") for i, fb in enumerate(self.fallbacks)]

    def _execute_sync_with_timeout(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> R:
        timeout = self.retry_policy.timeout_seconds
        if timeout <= 0:
            return func(*args, **kwargs)  # type: ignore[no-any-return]

        future = self.read_executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)  # type: ignore[no-any-return]
        except concurrent.futures.TimeoutError as err:
            if future.done():
                raise
            discard_late_read(future)
            raise ReadDeadlineExceeded(f"Read exceeded {timeout}s; late result discarded") from err

    async def _execute_async_with_timeout(self, func: Callable[..., R | Awaitable[R]], *args: Any, **kwargs: Any) -> R:
        timeout = self.retry_policy.timeout_seconds
        future = None

        async def execute():
            nonlocal future
            if inspect.iscoroutinefunction(func):
                result = func(*args, **kwargs)
            else:
                future = self.read_executor.submit(func, *args, **kwargs)
                result = await asyncio.wrap_future(future)
            return await result if inspect.isawaitable(result) else result

        deadline = asyncio.timeout(timeout if timeout > 0 else None)
        try:
            async with deadline:
                return await execute()  # type: ignore[no-any-return]
        except asyncio.CancelledError:
            if future is not None:
                discard_late_read(future)
            raise
        except TimeoutError:
            if deadline.expired():
                if future is not None:
                    discard_late_read(future)
                raise ReadDeadlineExceeded(f"Read exceeded {timeout}s; late result discarded") from None
            raise

    def invoke(self, *args: Any, **kwargs: Any) -> R:
        """Synchronously execute primary with fallback cascade."""
        all_runners = [(self.primary_name, self.primary)] + list(zip(self.fallback_names, self.fallbacks, strict=False))
        errors: list[Exception] = []

        for idx, (runner_name, runner) in enumerate(all_runners):
            for attempt in range(self.retry_policy.max_retries + 1):
                try:
                    return self._execute_sync_with_timeout(runner, *args, **kwargs)
                except self.retry_policy.exceptions_to_retry as e:
                    errors.append(e)
                    is_last_attempt = attempt == self.retry_policy.max_retries or isinstance(
                        e, (ReadDeadlineExceeded, ReadCapacityExceeded)
                    )
                    next_target = all_runners[idx + 1][0] if idx + 1 < len(all_runners) else "none (all exhausted)"

                    if not is_last_attempt:
                        delay = self.retry_policy.backoff_factor * (2**attempt)
                        logger.warning(
                            "Provider '%s' attempt %d/%d failed: %s. Retrying in %.2fs...",
                            runner_name,
                            attempt + 1,
                            self.retry_policy.max_retries + 1,
                            e,
                            delay,
                            extra={
                                "event": "provider_retry",
                                "provider": runner_name,
                                "attempt": attempt + 1,
                                "delay": delay,
                                "error": str(e),
                            },
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "Provider '%s' failed all retries. Cascading to fallback '%s'. Error: %s",
                            runner_name,
                            next_target,
                            e,
                            extra={
                                "event": "fallback_triggered",
                                "failed_provider": runner_name,
                                "next_provider": next_target,
                                "error": str(e),
                            },
                        )
                        break

        raise AllFallbacksExhaustedError(
            f"All providers ({[name for name, _ in all_runners]}) exhausted. Errors: {[str(e) for e in errors]}",
            errors,
        )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> R:
        """Asynchronously execute primary with fallback cascade."""
        all_runners = [(self.primary_name, self.primary)] + list(zip(self.fallback_names, self.fallbacks, strict=False))
        errors: list[Exception] = []

        for idx, (runner_name, runner) in enumerate(all_runners):
            for attempt in range(self.retry_policy.max_retries + 1):
                try:
                    return await self._execute_async_with_timeout(runner, *args, **kwargs)
                except self.retry_policy.exceptions_to_retry as e:
                    errors.append(e)
                    is_last_attempt = attempt == self.retry_policy.max_retries or isinstance(
                        e, (ReadDeadlineExceeded, ReadCapacityExceeded)
                    )
                    next_target = all_runners[idx + 1][0] if idx + 1 < len(all_runners) else "none (all exhausted)"

                    if not is_last_attempt:
                        delay = self.retry_policy.backoff_factor * (2**attempt)
                        logger.warning(
                            "Provider '%s' attempt %d/%d failed: %s. Retrying in %.2fs...",
                            runner_name,
                            attempt + 1,
                            self.retry_policy.max_retries + 1,
                            e,
                            delay,
                            extra={
                                "event": "provider_retry",
                                "provider": runner_name,
                                "attempt": attempt + 1,
                                "delay": delay,
                                "error": str(e),
                            },
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "Provider '%s' failed all retries. Cascading to fallback '%s'. Error: %s",
                            runner_name,
                            next_target,
                            e,
                            extra={
                                "event": "fallback_triggered",
                                "failed_provider": runner_name,
                                "next_provider": next_target,
                                "error": str(e),
                            },
                        )
                        break

        raise AllFallbacksExhaustedError(
            f"All providers ({[name for name, _ in all_runners]}) exhausted. Errors: {[str(e) for e in errors]}",
            errors,
        )
