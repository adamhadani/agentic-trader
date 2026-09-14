from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agentic_trader.resilience.fallback import (
    AllFallbacksExhaustedError,
    RetryPolicy,
    RunnableWithFallbacks,
)


def test_primary_succeeds_immediately():
    primary = MagicMock(return_value="primary_success")
    fallback = MagicMock(return_value="fallback_success")

    runner = RunnableWithFallbacks(
        primary=primary,
        fallbacks=[fallback],
        retry_policy=RetryPolicy(max_retries=1, backoff_factor=0.01),
        primary_name="primary",
        fallback_names=["fallback"],
    )

    result = runner.invoke("arg1", key="val")
    assert result == "primary_success"
    primary.assert_called_once_with("arg1", key="val")
    fallback.assert_not_called()


def test_primary_retries_and_succeeds():
    calls = []

    def flaky_primary(*args, **kwargs):
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionError("Network blip")
        return "recovered"

    fallback = MagicMock()
    runner = RunnableWithFallbacks(
        primary=flaky_primary,
        fallbacks=[fallback],
        retry_policy=RetryPolicy(max_retries=2, backoff_factor=0.01),
    )

    result = runner.invoke()
    assert result == "recovered"
    assert len(calls) == 2
    fallback.assert_not_called()


def test_primary_fails_cascades_to_fallback():
    primary = MagicMock(side_effect=ValueError("Invalid data format"))
    fallback = MagicMock(return_value="fallback_result")

    runner = RunnableWithFallbacks(
        primary=primary,
        fallbacks=[fallback],
        retry_policy=RetryPolicy(max_retries=1, backoff_factor=0.01),
        primary_name="primary",
        fallback_names=["fallback_one"],
    )

    result = runner.invoke(123)
    assert result == "fallback_result"
    assert primary.call_count == 2
    fallback.assert_called_once_with(123)


def test_all_fallbacks_fail_raises_exhausted():
    primary = MagicMock(side_effect=RuntimeError("Primary down"))
    fallback1 = MagicMock(side_effect=RuntimeError("Fallback 1 down"))
    fallback2 = MagicMock(side_effect=RuntimeError("Fallback 2 down"))

    runner = RunnableWithFallbacks(
        primary=primary,
        fallbacks=[fallback1, fallback2],
        retry_policy=RetryPolicy(max_retries=1, backoff_factor=0.01),
        primary_name="primary",
        fallback_names=["fb1", "fb2"],
    )

    with pytest.raises(AllFallbacksExhaustedError) as exc_info:
        runner.invoke()

    assert len(exc_info.value.errors) == 6  # 2 attempts per runner * 3 runners
    assert "exhausted" in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_fallback_execution():
    async def async_primary():
        raise TimeoutError("Alpaca rate limited")

    async def async_fallback():
        return {"source": "yfinance", "data": [1, 2, 3]}

    runner = RunnableWithFallbacks(
        primary=async_primary,
        fallbacks=[async_fallback],
        retry_policy=RetryPolicy(max_retries=1, backoff_factor=0.01),
        primary_name="alpaca",
        fallback_names=["yfinance"],
    )

    result = await runner.ainvoke()
    assert result == {"source": "yfinance", "data": [1, 2, 3]}


@pytest.mark.asyncio
async def test_async_timeout_triggers_fallback():
    async def slow_primary():
        await asyncio.sleep(0.5)
        return "too_late"

    async def fast_fallback():
        return "fast_result"

    runner = RunnableWithFallbacks(
        primary=slow_primary,
        fallbacks=[fast_fallback],
        retry_policy=RetryPolicy(max_retries=0, timeout_seconds=0.05),
        primary_name="slow",
        fallback_names=["fast"],
    )

    result = await runner.ainvoke()
    assert result == "fast_result"
