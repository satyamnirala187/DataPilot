"""A small in-memory, per-client rate limiter for POST /query.

Sliding window: a client may make at most max_requests requests in any window_seconds period.
It protects the Gemini quota from rapid repeated questions.

Limits are kept in this process's memory. With several backend processes or instances each
keeps its own counts, and a restart clears them. That is enough for one small demo instance;
a shared store (e.g. Redis) would be needed to limit across instances, and v1 deliberately has none.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

# Bounds memory if many different clients appear; idle clients are forgotten first.
MAX_TRACKED_CLIENTS = 10_000


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()  # sync endpoints run in a thread pool

    def check(self, client: str) -> int | None:
        """Record a request from client. Return None if allowed, else seconds until it may retry."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] <= now - self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return max(1, math.ceil(hits[0] + self.window_seconds - now))
            hits.append(now)
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._forget_idle_clients(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _forget_idle_clients(self, now: float) -> None:
        idle = [client for client, hits in self._hits.items() if not hits or hits[-1] <= now - self.window_seconds]
        for client in idle:
            del self._hits[client]
