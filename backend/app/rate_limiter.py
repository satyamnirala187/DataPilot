"""Two small in-memory limits for POST /query, both protecting the Gemini quota.

RateLimiter (per client, sliding window): a client may make at most max_requests requests in any
window_seconds period. It stops one client from sending rapid repeated questions.

DailyLimit (global, per Pacific day): at most `limit` questions per calendar day from all clients
together. It is a safety brake on total AI usage that IP rotation or many clients cannot get round.
Its day starts at midnight Pacific time, when Gemini's requests-per-day quota resets.

Both are kept in this process's memory. With several backend processes or instances each keeps
its own counts, and a restart clears them. That is enough for one small demo instance; a shared
store (e.g. Redis) would be needed to limit across instances, and v1 deliberately has none.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Bounds memory if many different clients appear; idle clients are forgotten first.
MAX_TRACKED_CLIENTS = 10_000

# Gemini's requests-per-day quota resets at midnight Pacific time, so the daily cap does too.
# A named zone, not a fixed offset, so PST/PDT changes are followed. Loaded at import: if the
# system time zone database were missing, the app would fail at startup rather than per request.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")
LONGEST_DAY_SECONDS = 25 * 3600  # the day daylight saving time ends


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


class DailyLimit:
    """At most `limit` admissions per Pacific calendar day, across all clients. Best effort: the
    count lives in this process's memory, so it starts again at zero after a restart."""

    def __init__(self, limit: int, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.limit = limit
        self._clock = clock  # timezone-aware wall-clock time: the limit follows calendar days
        self._day = None
        self._used = 0
        self._lock = threading.Lock()  # sync endpoints run in a thread pool

    def acquire(self) -> int | None:
        """Use one of today's admissions. Return None if allowed, else seconds until the next day."""
        now = self._clock()
        today = now.astimezone(QUOTA_TIMEZONE).date()
        with self._lock:
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.limit:
                return seconds_until_next_quota_day(now)
            self._used += 1
            return None


def seconds_until_next_quota_day(now: datetime) -> int:
    """Whole seconds from now (timezone-aware) until the next midnight Pacific time."""
    tomorrow = now.astimezone(QUOTA_TIMEZONE).date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=QUOTA_TIMEZONE)
    # Subtract in UTC: Python subtracts two times in the same zone by wall clock, ignoring DST.
    seconds = (midnight.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    return min(LONGEST_DAY_SECONDS, max(1, math.ceil(seconds)))
