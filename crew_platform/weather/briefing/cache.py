"""Small process-wide TTL cache for upstream briefing responses.

Task requirement: "upstream caching prevents per-client fan-out" — many
crew PAD browsers hitting the same `/api/crew/weather/...` route must
share one upstream fetch per TTL window, never one fetch per client.
Keyed by an arbitrary hashable key (e.g. ``(source, station, product)``).
Thread-safe (the scheduler and request threads can share it).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                return None
            return value

    def get_stale(self, key: Any) -> Optional[Any]:
        """Return a value even if expired (last-good fallback)."""
        with self._lock:
            entry = self._data.get(key)
            return entry[1] if entry is not None else None

    def set(self, key: Any, value: Any, ttl_s: float) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl_s, value)

    def is_fresh(self, key: Any) -> bool:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            return time.time() < entry[0]

    def get_or_fetch(self, key: Any, ttl_s: float, fetch: Callable[[], T]) -> T:
        """Single-flight-ish fetch: serve a fresh cached value, else fetch.

        Not a strict single-flight lock across concurrent misses (two
        near-simultaneous cold requests may both fetch once) — acceptable
        here since the TTL window is minutes and the scheduler primes the
        cache in the background; it still eliminates per-request fan-out
        for the overwhelmingly common warm-cache path.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        value = fetch()
        self.set(key, value, ttl_s)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


#: process-wide singleton shared by every briefing adapter + the scheduler.
CACHE = TTLCache()
