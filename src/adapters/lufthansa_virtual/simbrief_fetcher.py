"""Fetch OFP (Operational Flight Plan) data from SimBrief for a given pilot.

Wraps the existing SimBrief JSON v2 API and extracts the fields relevant to
the Lufthansa Virtual workflow: route, fuel, weights, cruise parameters, and
per-waypoint wind data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


class SimBriefFetcherError(RuntimeError):
    """Raised when the SimBrief API returns an error or unreachable."""


@dataclass(frozen=True)
class OFPWaypoint:
    ident: str
    lat: float
    lon: float
    altitude_ft: int
    wind_dir_deg: Optional[float] = None
    wind_speed_kt: Optional[float] = None
    oat_c: Optional[float] = None


@dataclass(frozen=True)
class OFPData:
    """Normalised OFP extract — immutable snapshot of one flight plan."""

    flight_number: Optional[str] = None
    airline_icao: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    alternate: Optional[str] = None

    # Coordinates for weather lookups
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None
    destination_lat: Optional[float] = None
    destination_lon: Optional[float] = None

    route_distance_nm: Optional[float] = None
    planned_cruise_fl: Optional[int] = None
    planned_cruise_mach: Optional[float] = None
    cost_index: Optional[int] = None

    tow_kg: Optional[float] = None
    zfw_kg: Optional[float] = None
    block_fuel_kg: Optional[float] = None
    trip_fuel_kg: Optional[float] = None
    reserve_fuel_kg: Optional[float] = None

    pax_count: Optional[int] = None
    cargo_kg: Optional[float] = None

    planned_departure_utc: Optional[str] = None
    planned_arrival_utc: Optional[str] = None
    planned_block_time_min: Optional[float] = None

    avg_wind_component_kt: Optional[float] = None
    avg_isa_deviation_c: Optional[float] = None

    waypoints: list[OFPWaypoint] = field(default_factory=list)


def _safe_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _safe_int(raw: Any) -> Optional[int]:
    v = _safe_float(raw)
    return int(v) if v is not None else None


def _parse_pm(value: str | None) -> Optional[float]:
    """Parse SimBrief P/M notation: 'P017' → +17.0, 'M025' → -25.0."""
    if not value or len(value) < 2:
        return None
    sign = 1.0 if value[0].upper() == "P" else -1.0
    try:
        return sign * float(value[1:])
    except ValueError:
        return None


def _normalise(raw: dict[str, Any]) -> OFPData:
    """Turn raw SimBrief JSON v2 into an OFPData."""
    general = raw.get("general") or {}
    origin_info = raw.get("origin") or {}
    dest_info = raw.get("destination") or {}
    alt_info = raw.get("alternate") or {}
    fuel = raw.get("fuel") or {}
    weights = raw.get("weights") or {}
    atc = raw.get("atc") or {}
    times = raw.get("times") or {}
    params = raw.get("params") or {}

    # Waypoints
    nav_raw = raw.get("navlog") or {}
    fix_list: list[dict[str, Any]] = nav_raw.get("fix") or []
    waypoints: list[OFPWaypoint] = []
    for fix in fix_list:
        ident = fix.get("ident") or fix.get("name", "???")
        lat = _safe_float(fix.get("pos_lat"))
        lon = _safe_float(fix.get("pos_long"))
        alt = _safe_int(fix.get("altitude_feet")) or 0
        if lat is not None and lon is not None:
            waypoints.append(
                OFPWaypoint(
                    ident=ident,
                    lat=lat,
                    lon=lon,
                    altitude_ft=alt,
                    wind_dir_deg=_safe_float(fix.get("wind_dir")),
                    wind_speed_kt=_safe_float(fix.get("wind_spd")),
                    oat_c=_safe_float(fix.get("oat")),
                )
            )

    return OFPData(
        flight_number=general.get("flight_number"),
        airline_icao=general.get("icao_airline"),
        origin=origin_info.get("icao_code"),
        destination=dest_info.get("icao_code"),
        alternate=alt_info.get("icao_code") if alt_info else None,
        origin_lat=_safe_float(origin_info.get("pos_lat")),
        origin_lon=_safe_float(origin_info.get("pos_long")),
        destination_lat=_safe_float(dest_info.get("pos_lat")),
        destination_lon=_safe_float(dest_info.get("pos_long")),
        route_distance_nm=_safe_float(general.get("route_distance")),
        planned_cruise_fl=_safe_int(general.get("initial_altitude"))
        if general.get("initial_altitude")
        else None,
        planned_cruise_mach=_safe_float(atc.get("initial_spd_mach")),
        cost_index=_safe_int(general.get("costindex")),
        tow_kg=_safe_float(weights.get("est_tow")),
        zfw_kg=_safe_float(weights.get("est_zfw")),
        block_fuel_kg=_safe_float(fuel.get("plan_ramp")),
        trip_fuel_kg=_safe_float(fuel.get("enroute_burn")),
        reserve_fuel_kg=_safe_float(fuel.get("reserve")),
        pax_count=_safe_int(weights.get("pax_count")),
        cargo_kg=_safe_float(weights.get("cargo")),
        planned_departure_utc=times.get("sched_out"),
        planned_arrival_utc=times.get("sched_in"),
        planned_block_time_min=_safe_float(times.get("sched_block")),
        avg_wind_component_kt=_parse_pm(general.get("avg_wind_comp")),
        avg_isa_deviation_c=_parse_pm(general.get("avg_temp_dev")),
        waypoints=waypoints,
    )


class SimBriefOFPFetcher:
    """Async client that retrieves and normalises SimBrief OFP data."""

    BASE_URL = "https://www.simbrief.com/api/xml.fetcher.php"

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._timeout = timeout_seconds

    async def fetch(
        self,
        *,
        username: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> OFPData:
        """Fetch the latest OFP for the given SimBrief pilot.

        Either *username* or *user_id* is required.
        Returns a normalised OFPData snapshot.
        """
        if not username and not user_id:
            raise ValueError("Either username or user_id must be provided.")

        params: dict[str, str] = {"json": "v2"}
        if username:
            params["username"] = username
        if user_id:
            params["userid"] = str(user_id)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self.BASE_URL, params=params)

        if resp.status_code != 200:
            raise SimBriefFetcherError(
                f"SimBrief HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise SimBriefFetcherError(
                f"SimBrief non-JSON response: {resp.text[:300]}"
            ) from exc

        fetch_info = data.get("fetch") or {}
        if str(fetch_info.get("status", "")).lower() == "error":
            raise SimBriefFetcherError(
                fetch_info.get("message", "Unknown SimBrief error")
            )

        return _normalise(data)
