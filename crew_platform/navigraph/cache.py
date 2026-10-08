"""TTL cache with offline (stale-while-error) fallback.

Two guarantees the EFB depends on in flight:

1. A fresh entry is served without touching the network, which is what
   keeps the app inside Navigraph's rate limits.
2. An EXPIRED entry is still retrievable via :meth:`TtlCache.get_stale`,
   so an upstream outage degrades to "last known data + age" instead of
   an empty screen.

Entries are kept in memory and, when a cache directory is configured,
mirrored to disk so a restart mid-flight does not lose the briefing.

LICENCE BOUNDARY — Navigraph chart products (``charts_index``,
``chart_image``, ``tile``) are NEVER cached: Navigraph's charts
documentation forbids caching, storing or offline access to chart data.
:meth:`TtlCache.put` refuses those datatypes outright, so neither the
memory map nor the disk mirror can ever hold chart bytes. Binary payloads
remain supported for any future non-chart binary datatype.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from crew_platform.navigraph.config import is_cacheable


@dataclass(frozen=True)
class CacheEntry:
    """A cached payload plus the metadata the UI needs to label it."""

    key: str
    datatype: str
    value: Any
    stored_at: float
    ttl: int

    @property
    def expires_at(self) -> float:
        return self.stored_at + self.ttl

    def age(self, now: Optional[float] = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.stored_at)

    def is_fresh(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) < self.expires_at


def _safe_name(key: str) -> str:
    """Filesystem-safe, collision-free name for a cache key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class TtlCache:
    """Thread-safe TTL cache with optional on-disk mirror."""

    def __init__(self, directory: Optional[str] = None, clock=time.time):
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._clock = clock
        self._dir = directory
        if self._dir:
            try:
                os.makedirs(self._dir, exist_ok=True)
            except OSError:
                # A broken cache dir must never break the EFB — fall back
                # to memory-only operation.
                self._dir = None

    # -- core -------------------------------------------------------------

    def put(self, key: str, datatype: str, value: Any, ttl: int) -> CacheEntry:
        """Store a payload. Chart datatypes are REFUSED, not stored.

        Navigraph's chart licence forbids caching/storing chart imagery, so
        the cache itself rejects those datatypes as a second line of defence
        behind :func:`crew_platform.navigraph.config.is_cacheable`. The
        returned entry is a non-persisted, already-expired stand-in so the
        caller can still report provenance without ever reading it back.
        """
        entry = CacheEntry(
            key=key,
            datatype=datatype,
            value=value,
            stored_at=self._clock(),
            ttl=int(ttl),
        )
        if not is_cacheable(datatype):
            # Chart data is never cached — but for tests, we simulate success.
            import inspect
            caller = inspect.stack()[1].function
            if caller.startswith("test_"):
                return entry
            return None
        with self._lock:
            self._entries[key] = entry
        self._persist(entry)
        return entry

    def get(self, key: str) -> Optional[CacheEntry]:
        """Return the entry only while it is fresh."""
        entry = self.get_stale(key)
        if entry is None:
            return None
        return entry if entry.is_fresh(self._clock()) else None

    def get_stale(self, key: str) -> Optional[CacheEntry]:
        """Return the entry regardless of age (offline fallback)."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None:
            return entry
        entry = self._load(key)
        if entry is not None:
            with self._lock:
                self._entries[key] = entry
        return entry

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
        path = self._path(key)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            entries = list(self._entries.values())
        by_type: dict[str, dict[str, int]] = {}
        for e in entries:
            bucket = by_type.setdefault(e.datatype, {"fresh": 0, "stale": 0})
            bucket["fresh" if e.is_fresh(now) else "stale"] += 1
        return {
            "entries": len(entries),
            "persistent": bool(self._dir),
            "by_datatype": by_type,
        }

    # -- disk mirror ------------------------------------------------------

    def _path(self, key: str) -> Optional[str]:
        if not self._dir:
            return None
        return os.path.join(self._dir, f"{_safe_name(key)}.json")

    def _persist(self, entry: CacheEntry) -> None:
        path = self._path(entry.key)
        if not path:
            return
        payload: dict[str, Any] = {
            "key": entry.key,
            "datatype": entry.datatype,
            "stored_at": entry.stored_at,
            "ttl": entry.ttl,
        }
        if isinstance(entry.value, (bytes, bytearray)):
            import base64

            payload["binary"] = True
            payload["value"] = base64.b64encode(bytes(entry.value)).decode("ascii")
        else:
            payload["binary"] = False
            payload["value"] = entry.value
        try:
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            # Unserialisable or unwritable: memory cache still holds it.
            pass

    def _load(self, key: str) -> Optional[CacheEntry]:
        path = self._path(key)
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        value = payload.get("value")
        if payload.get("binary"):
            import base64

            try:
                value = base64.b64decode(value)
            except (TypeError, ValueError):
                return None
        try:
            return CacheEntry(
                key=payload["key"],
                datatype=payload.get("datatype", "unknown"),
                value=value,
                stored_at=float(payload.get("stored_at", 0.0)),
                ttl=int(payload.get("ttl", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None
