"""Operations notifications — weather-change event engine.

Polls a WeatherProvider for departure/arrival ICAO weather,
detects significant changes, and emits deduplicated events.

Thresholds:
- visibility_km: >= 1 km change
- wind_dir_deg: >= 30 deg shift
- wind_speed_kt: >= 10 kt change
- temp_c: >= 3 C change

Dedup: max 1 event per (type, icao) per 15-minute window.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeatherSnapshot:
    """A point-in-time weather observation for a single ICAO."""

    visibility_km: float
    wind_dir_deg: float
    wind_speed_kt: float
    temp_c: float


@dataclass
class NotificationEvent:
    """A single notification event emitted by the engine."""

    id: str
    type: str  # visibility | wind_direction | wind_speed | temperature
    icao: str
    summary: str
    provenance: str
    timestamp: float
    observed: dict[str, Any]


# ---------------------------------------------------------------------------
# WeatherProvider interface (injectable)
# ---------------------------------------------------------------------------


class WeatherProvider(ABC):
    """Abstract weather data source — injectable for testing."""

    @abstractmethod
    async def fetch_weather(self, icao: str) -> Optional[WeatherSnapshot]:
        """Fetch the current weather for *icao*. Return None if unavailable."""


# ---------------------------------------------------------------------------
# NotificationFeed — session-scoped event store
# ---------------------------------------------------------------------------


class NotificationFeed:
    """Session-scoped notification event store."""

    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    def get_events(self) -> list[NotificationEvent]:
        """Return events newest-first."""
        return sorted(self.events, key=lambda e: e.timestamp, reverse=True)

    def clear(self) -> None:
        self.events.clear()


# Module-level feed registry (session_id -> feed)
_feeds: dict[str, NotificationFeed] = {}


def get_or_create_feed(session_id: str) -> NotificationFeed:
    """Get or create a NotificationFeed for a session."""
    if session_id not in _feeds:
        _feeds[session_id] = NotificationFeed()
    return _feeds[session_id]


def remove_feed(session_id: str) -> None:
    """Remove a feed (e.g. on session destroy)."""
    _feeds.pop(session_id, None)


# ---------------------------------------------------------------------------
# Change detection thresholds
# ---------------------------------------------------------------------------

_THRESHOLDS: dict[str, tuple[str, float]] = {
    # field_name -> (event_type, threshold)
    "visibility_km": ("visibility", 1.0),
    "wind_dir_deg": ("wind_direction", 30.0),
    "wind_speed_kt": ("wind_speed", 10.0),
    "temp_c": ("temperature", 3.0),
}

_DEDUP_WINDOW_S = 15 * 60  # 15 minutes


def _angular_diff(a: float, b: float) -> float:
    """Minimum angular difference (handles wrap-around)."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


# ---------------------------------------------------------------------------
# NotificationEngine
# ---------------------------------------------------------------------------


class NotificationEngine:
    """Polls a weather provider and emits change-detection events."""

    def __init__(self, provider: WeatherProvider, provenance: str = "open-meteo") -> None:
        self._provider = provider
        self._provenance = provenance
        # Last stored snapshot per ICAO
        self._last: dict[str, WeatherSnapshot] = {}
        # Dedup: (type, icao) -> expiry timestamp
        self._dedup_expiry: dict[tuple[str, str], float] = {}

    async def poll(self, icao: str, feed: NotificationFeed) -> list[NotificationEvent]:
        """Poll weather for *icao*, detect changes, emit events into *feed*.

        Returns the list of new events emitted (may be empty).
        """
        snap = await self._provider.fetch_weather(icao)
        if snap is None:
            return []

        prev = self._last.get(icao)
        self._last[icao] = snap

        if prev is None:
            # First poll — baseline, no events
            return []

        new_events: list[NotificationEvent] = []
        now = time.time()

        for field_name, (event_type, threshold) in _THRESHOLDS.items():
            old_val = getattr(prev, field_name)
            new_val = getattr(snap, field_name)

            # Compute delta
            if field_name == "wind_dir_deg":
                delta = _angular_diff(old_val, new_val)
            else:
                delta = abs(new_val - old_val)

            if delta < threshold:
                continue

            # Dedup check
            key = (event_type, icao)
            if key in self._dedup_expiry and now < self._dedup_expiry[key]:
                continue

            # Emit event
            summary = _make_summary(event_type, icao, old_val, new_val)
            event = NotificationEvent(
                id=uuid.uuid4().hex[:12],
                type=event_type,
                icao=icao,
                summary=summary,
                provenance=self._provenance,
                timestamp=now,
                observed={field_name: new_val},
            )
            feed.events.append(event)
            new_events.append(event)

            # Set dedup window
            self._dedup_expiry[key] = now + _DEDUP_WINDOW_S

        return new_events


def _make_summary(event_type: str, icao: str, old: float, new: float) -> str:
    """Human-readable summary for a weather change event."""
    labels = {
        "visibility": f"Visibility at {icao} changed from {old:.1f} to {new:.1f} km",
        "wind_direction": f"Wind direction at {icao} shifted from {old:.0f} to {new:.0f} deg",
        "wind_speed": f"Wind speed at {icao} changed from {old:.1f} to {new:.1f} kt",
        "temperature": f"Temperature at {icao} changed from {old:.1f} to {new:.1f} C",
    }
    return labels.get(event_type, f"{event_type} change at {icao}")


# ---------------------------------------------------------------------------
# Open-Meteo provider (stdlib-only, keyless)
# ---------------------------------------------------------------------------

# ICAO -> approximate (lat, lon) for common airports
_ICAO_COORDS: dict[str, tuple[float, float]] = {
    "EDDF": (50.0379, 8.5622),
    "KJFK": (40.6413, -73.7781),
    "OMDB": (25.2532, 55.3657),
    "OMAA": (24.4331, 54.6511),
    "EGLL": (51.4700, -0.4543),
    "LFPG": (49.0097, 2.5479),
    "VHHH": (22.3080, 113.9185),
    "WSSS": (1.3644, 103.9915),
    "KLAX": (33.9425, -118.4081),
    "KATL": (33.6407, -84.4277),
}


class OpenMeteoWeatherProvider(WeatherProvider):
    """Keyless Open-Meteo weather provider for MVP.

    Fetches current weather for an ICAO code using the Open-Meteo API.
    No API key required.
    """

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def fetch_weather(self, icao: str) -> Optional[WeatherSnapshot]:
        coords = _ICAO_COORDS.get(icao.upper())
        if coords is None:
            return None

        lat, lon = coords

        try:
            import httpx

            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,wind_direction_10m,visibility",
                "wind_speed_unit": "kn",
                "timezone": "UTC",
            }
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self.BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            current = data.get("current", {})
            return WeatherSnapshot(
                visibility_km=current.get("visibility", 10000.0) / 1000.0,
                wind_dir_deg=current.get("wind_direction_10m", 0.0),
                wind_speed_kt=current.get("wind_speed_10m", 0.0),
                temp_c=current.get("temperature_2m", 15.0),
            )
        except Exception:
            return None
