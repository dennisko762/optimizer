"""Open-Meteo weather integration — wind data for cruise altitude.

Fetches GFS-based wind forecasts along the route from the free Open-Meteo
API (no key required).  The adapter converts pressure-level winds to a
headwind/tailwind component relative to the track heading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import httpx

# Mapping of common cruise flight levels to the closest pressure level (hPa)
# available in the Open-Meteo pressure-level endpoint.
_FL_TO_PRESSURE: dict[int, int] = {
    250: 300,
    260: 300,
    270: 300,
    280: 300,
    290: 300,
    300: 250,
    310: 250,
    320: 250,
    330: 250,
    340: 225,
    350: 225,
    360: 200,
    370: 200,
    380: 200,
    390: 175,
    400: 175,
    410: 150,
}


@dataclass(frozen=True)
class WindPoint:
    """Wind observation / forecast at a single point."""

    lat: float
    lon: float
    wind_u_ms: float  # U-component (eastward), m/s
    wind_v_ms: float  # V-component (northward), m/s

    @property
    def wind_speed_kt(self) -> float:
        return math.hypot(self.wind_u_ms, self.wind_v_ms) * 1.94384

    @property
    def wind_dir_deg(self) -> float:
        """Meteorological wind direction (where the wind comes FROM)."""
        return (270.0 - math.degrees(math.atan2(self.wind_v_ms, self.wind_u_ms))) % 360.0

    def headwind_component_kt(self, track_deg: float) -> float:
        """Positive = headwind, negative = tailwind."""
        wind_from = math.radians(self.wind_dir_deg)
        track = math.radians(track_deg)
        return self.wind_speed_kt * math.cos(wind_from - track)


def _track_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from (lat1,lon1) to (lat2,lon2) in degrees."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360.0


@dataclass(frozen=True)
class RouteWindSummary:
    """Aggregated wind data along a route."""

    points: list[WindPoint]
    avg_headwind_kt: float  # positive = headwind, negative = tailwind
    max_headwind_kt: float
    max_tailwind_kt: float  # stored as positive tailwind magnitude
    track_deg: float


class OpenMeteoWindService:
    """Fetch upper-level winds from Open-Meteo for route waypoints."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds

    def _pressure_level(self, flight_level: int) -> int:
        if flight_level in _FL_TO_PRESSURE:
            return _FL_TO_PRESSURE[flight_level]
        # Fallback: pick the closest defined FL
        closest = min(_FL_TO_PRESSURE, key=lambda fl: abs(fl - flight_level))
        return _FL_TO_PRESSURE[closest]

    async def get_winds_along_route(
        self,
        waypoints: Sequence[tuple[float, float]],
        flight_level: int = 350,
    ) -> RouteWindSummary:
        """Fetch winds at each (lat, lon) waypoint for the given FL.

        *waypoints* is a sequence of (lat, lon) tuples.
        Returns a RouteWindSummary with per-point data and aggregated stats.
        """
        if len(waypoints) < 2:
            raise ValueError("Need at least 2 waypoints for a route.")

        pressure = self._pressure_level(flight_level)
        u_var = f"wind_u_component_{pressure}hPa"
        v_var = f"wind_v_component_{pressure}hPa"

        points: list[WindPoint] = []

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            for lat, lon in waypoints:
                params: dict[str, str] = {
                    "latitude": f"{lat:.4f}",
                    "longitude": f"{lon:.4f}",
                    "hourly": f"{u_var},{v_var}",
                    "forecast_days": "1",
                    "timezone": "UTC",
                }
                resp = await http.get(self.BASE_URL, params=params)
                if resp.status_code != 200:
                    # Graceful degradation: skip waypoint on API failure
                    continue
                data = resp.json()
                hourly = data.get("hourly") or {}
                u_vals = hourly.get(u_var) or []
                v_vals = hourly.get(v_var) or []
                if u_vals and v_vals:
                    # Use first hour as representative
                    points.append(
                        WindPoint(
                            lat=lat,
                            lon=lon,
                            wind_u_ms=float(u_vals[0]) if u_vals[0] is not None else 0.0,
                            wind_v_ms=float(v_vals[0]) if v_vals[0] is not None else 0.0,
                        )
                    )

        # Compute track heading from first to last waypoint
        first, last = waypoints[0], waypoints[-1]
        track = _track_between(first[0], first[1], last[0], last[1])

        headwinds = [p.headwind_component_kt(track) for p in points] if points else [0.0]

        return RouteWindSummary(
            points=points,
            avg_headwind_kt=sum(headwinds) / len(headwinds),
            max_headwind_kt=max(headwinds),
            max_tailwind_kt=abs(min(headwinds)) if min(headwinds) < 0 else 0.0,
            track_deg=track,
        )
