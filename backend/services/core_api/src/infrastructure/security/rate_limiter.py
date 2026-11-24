"""In-memory sliding-window rate limiter.

Used to cap how often one client IP address may call the public registration
endpoints. It is per API process: with several API replicas each keeps its own
count, and a restart forgets them. A shared store (Redis) can replace it later behind
the same interface. The per-email-address limits are separate and are enforced in
PostgreSQL, so they always hold.
"""

import math
import time
from collections import deque
from collections.abc import Callable

_MAX_KEYS = 10_000


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit must be at least 1 and window_seconds positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> int | None:
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            self._make_room(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(1, math.ceil(hits[0] + self._window - now))
        hits.append(now)
        return None

    def _make_room(self, now: float) -> None:
        if len(self._hits) < _MAX_KEYS:
            return
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self._window]:
            del self._hits[key]
        while len(self._hits) >= _MAX_KEYS:  # still full: forget the oldest keys
            del self._hits[next(iter(self._hits))]
