"""Client-side request pacing shared across the scan's worker threads."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


WINDOW_SECONDS = 60.0


class RequestPacer:
    """Sliding-window limiter: at most ``max_per_minute`` acquisitions in any 60 s window."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_per_minute < 1:
            raise ValueError("max_per_minute must be positive")
        self.max_per_minute = max_per_minute
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._stamps: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= WINDOW_SECONDS:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_per_minute:
                    self._stamps.append(now)
                    return
                wait = WINDOW_SECONDS - (now - self._stamps[0])
            self._sleep(max(wait, 0.0))
