"""Provider-specific rate limiting + a circuit breaker.

- IntervalLimiter: minimum spacing between calls, optional random jitter.
- TokenBucket: burst capacity refilling at a steady rate.
- CircuitBreaker: trips after N consecutive failures, blocks during a cooldown,
  then half-opens to retry (protects against DuckDuckGo 429 storms).
"""
from __future__ import annotations

import random
import threading
import time


class IntervalLimiter:
    def __init__(self, min_interval: float, jitter: float = 0.0) -> None:
        self._min = min_interval
        self._jitter = jitter
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            extra = random.uniform(0, self._jitter) if self._jitter else 0.0
            self._next = time.monotonic() + self._min + extra


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self._rate = max(rate_per_sec, 0.001)
        self._capacity = max(capacity, 1)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) / self._rate
            time.sleep(min(deficit, 1.0))


class CircuitBreaker:
    """Trips OPEN after `threshold` consecutive failures; blocks during
    `cooldown`; then HALF-OPEN allows one trial. A success closes it."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, threshold: int, cooldown: float, on_trip=None) -> None:
        self._threshold = threshold
        self._cooldown = cooldown
        self._on_trip = on_trip
        self._lock = threading.Lock()
        self._failures = 0
        self._state = self.CLOSED
        self._opened_at = 0.0

    def allow(self) -> bool:
        with self._lock:
            if self._state == self.OPEN:
                if time.monotonic() - self._opened_at >= self._cooldown:
                    self._state = self.HALF_OPEN
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        tripped = False
        with self._lock:
            self._failures += 1
            if self._state == self.HALF_OPEN or self._failures >= self._threshold:
                if self._state != self.OPEN:
                    tripped = True
                self._state = self.OPEN
                self._opened_at = time.monotonic()
        if tripped and self._on_trip:
            self._on_trip()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state
