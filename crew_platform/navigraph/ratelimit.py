"""Client-side rate limiting for the Navigraph API.

Navigraph's Terms of Service forbid bulk retrieval, and the API enforces
undisclosed per-client limits. Rather than discovering those limits the
hard way, the connector self-limits with a token bucket and treats an
upstream `429` as authoritative (honouring `Retry-After`).

The bucket is deliberately conservative: 60 requests/minute with a burst
of 10 by default (both overridable via env, see config.py).
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class RateLimitExceeded(RuntimeError):
    """Raised when a request would exceed the local rate budget.

    ``retry_after`` is the number of seconds the caller should wait.
    Callers in the request path convert this into a cached/stale response
    instead of propagating an error to the cockpit.
    """

    def __init__(self, retry_after: float, scope: str = "local"):
        super().__init__(
            f"Navigraph rate limit reached ({scope}); retry in "
            f"{retry_after:.1f}s"
        )
        self.retry_after = max(0.0, float(retry_after))
        self.scope = scope


class RateLimiter:
    """Token bucket, refilled continuously at ``rpm / 60`` tokens/second."""

    def __init__(
        self,
        rpm: int = 60,
        burst: int = 10,
        clock=time.monotonic,
    ):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._rate = rpm / 60.0
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._updated = clock()
        self._lock = threading.RLock()
        # Set while an upstream 429 back-off is in effect.
        self._blocked_until: Optional[float] = None
        self.rpm = rpm
        self.burst = burst

    # -- internals --------------------------------------------------------

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        if elapsed:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

    # -- public API -------------------------------------------------------

    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def acquire(self, cost: float = 1.0) -> None:
        """Consume ``cost`` tokens or raise :class:`RateLimitExceeded`.

        Never blocks: a live EFB must not stall on a rate limit, it must
        fall back to cached data immediately.
        """
        with self._lock:
            now = self._clock()
            if self._blocked_until is not None:
                if now < self._blocked_until:
                    raise RateLimitExceeded(self._blocked_until - now, scope="upstream")
                self._blocked_until = None
            self._refill()
            if self._tokens >= cost:
                self._tokens -= cost
                return
            deficit = cost - self._tokens
            raise RateLimitExceeded(deficit / self._rate, scope="local")

    def penalise(self, retry_after: Optional[float]) -> float:
        """Record an upstream 429.

        Empties the bucket and blocks all requests for ``retry_after``
        seconds (default 60 when the server sends no header). Returns the
        effective back-off.
        """
        delay = 60.0
        if retry_after is not None:
            try:
                delay = max(1.0, float(retry_after))
            except (TypeError, ValueError):
                delay = 60.0
        with self._lock:
            self._tokens = 0.0
            self._updated = self._clock()
            self._blocked_until = self._updated + delay
        return delay

    def status(self) -> dict[str, object]:
        with self._lock:
            self._refill()
            blocked_for = 0.0
            if self._blocked_until is not None:
                blocked_for = max(0.0, self._blocked_until - self._clock())
            return {
                "rpm": self.rpm,
                "burst": self.burst,
                "tokens_available": round(self._tokens, 2),
                "backing_off": blocked_for > 0,
                "backoff_seconds_remaining": round(blocked_for, 1),
            }
