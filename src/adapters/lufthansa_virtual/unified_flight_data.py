"""Unified flight data model — merges SimBrief OFP, vAMSYS booking, and
Open-Meteo wind data into a single coherent snapshot used by the optimizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .simbrief_fetcher import OFPData, SimBriefOFPFetcher
from .vamsys_client import BookedFlight, PilotProfile, VamsysClient
from .weather_integration import (
    OpenMeteoWindService,
    RouteWindSummary,
)


@dataclass
class UnifiedFlightData:
    """Single-object view of everything the optimizer needs for one flight."""

    # --- Identity -------------------------------------------------------
    flight_number: Optional[str] = None
    airline_icao: Optional[str] = None
    pilot_callsign: Optional[str] = None
    pilot_rank: Optional[str] = None

    # --- Route ----------------------------------------------------------
    origin: Optional[str] = None
    destination: Optional[str] = None
    alternate: Optional[str] = None
    route_distance_nm: Optional[float] = None

    # --- Performance plan ------------------------------------------------
    planned_cruise_fl: Optional[int] = None
    planned_cruise_mach: Optional[float] = None
    cost_index: Optional[int] = None

    # --- Weights (kg) ---------------------------------------------------
    tow_kg: Optional[float] = None
    zfw_kg: Optional[float] = None
    block_fuel_kg: Optional[float] = None
    trip_fuel_kg: Optional[float] = None
    reserve_fuel_kg: Optional[float] = None
    pax_count: Optional[int] = None
    cargo_kg: Optional[float] = None

    # --- Timing ---------------------------------------------------------
    scheduled_departure_utc: Optional[str] = None
    scheduled_arrival_utc: Optional[str] = None
    planned_block_time_min: Optional[float] = None

    # --- Wind (from Open-Meteo or OFP) ----------------------------------
    avg_headwind_kt: Optional[float] = None
    max_headwind_kt: Optional[float] = None
    max_tailwind_kt: Optional[float] = None
    route_track_deg: Optional[float] = None
    avg_isa_deviation_c: Optional[float] = None

    # --- VA booking metadata -------------------------------------------
    va_booking_id: Optional[str] = None
    va_booking_status: Optional[str] = None
    va_aircraft_icao: Optional[str] = None

    # --- Raw sources (for debugging) ------------------------------------
    _ofp: Optional[OFPData] = field(default=None, repr=False)
    _booking: Optional[BookedFlight] = field(default=None, repr=False)
    _wind_summary: Optional[RouteWindSummary] = field(default=None, repr=False)

    # --- Source flags ---------------------------------------------------
    source_simbrief: bool = False
    source_vamsys: bool = False
    source_open_meteo: bool = False


def _merge_ofp(ufd: UnifiedFlightData, ofp: OFPData) -> None:
    """Populate UFD fields from SimBrief OFP data."""
    ufd.flight_number = ofp.flight_number
    ufd.airline_icao = ofp.airline_icao
    ufd.origin = ofp.origin
    ufd.destination = ofp.destination
    ufd.alternate = ofp.alternate
    ufd.route_distance_nm = ofp.route_distance_nm
    ufd.planned_cruise_fl = ofp.planned_cruise_fl
    ufd.planned_cruise_mach = ofp.planned_cruise_mach
    ufd.cost_index = ofp.cost_index
    ufd.tow_kg = ofp.tow_kg
    ufd.zfw_kg = ofp.zfw_kg
    ufd.block_fuel_kg = ofp.block_fuel_kg
    ufd.trip_fuel_kg = ofp.trip_fuel_kg
    ufd.reserve_fuel_kg = ofp.reserve_fuel_kg
    ufd.pax_count = ofp.pax_count
    ufd.cargo_kg = ofp.cargo_kg
    ufd.scheduled_departure_utc = ofp.planned_departure_utc
    ufd.scheduled_arrival_utc = ofp.planned_arrival_utc
    ufd.planned_block_time_min = ofp.planned_block_time_min
    ufd.avg_headwind_kt = ofp.avg_wind_component_kt
    ufd.avg_isa_deviation_c = ofp.avg_isa_deviation_c
    ufd._ofp = ofp
    ufd.source_simbrief = True


def _merge_booking(ufd: UnifiedFlightData, booking: BookedFlight) -> None:
    """Overlay VA booking information onto UFD."""
    ufd.va_booking_id = booking.booking_id
    ufd.va_booking_status = booking.status
    ufd.va_aircraft_icao = booking.aircraft_icao
    # VA flight number / route only if SimBrief didn't set them
    if not ufd.flight_number:
        ufd.flight_number = booking.flight_number
    if not ufd.origin:
        ufd.origin = booking.departure_icao
    if not ufd.destination:
        ufd.destination = booking.arrival_icao
    if not ufd.scheduled_departure_utc:
        ufd.scheduled_departure_utc = booking.scheduled_departure_utc
    if not ufd.scheduled_arrival_utc:
        ufd.scheduled_arrival_utc = booking.scheduled_arrival_utc
    ufd.source_vamsys = True


def _merge_profile(ufd: UnifiedFlightData, profile: PilotProfile) -> None:
    ufd.pilot_callsign = profile.callsign
    ufd.pilot_rank = profile.rank
    if not ufd.airline_icao:
        ufd.airline_icao = profile.airline_icao


def _merge_winds(ufd: UnifiedFlightData, winds: RouteWindSummary) -> None:
    """Overlay live Open-Meteo winds (overrides OFP averages)."""
    ufd.avg_headwind_kt = winds.avg_headwind_kt
    ufd.max_headwind_kt = winds.max_headwind_kt
    ufd.max_tailwind_kt = winds.max_tailwind_kt
    ufd.route_track_deg = winds.track_deg
    ufd._wind_summary = winds
    ufd.source_open_meteo = True


async def build_unified_flight_data(
    *,
    simbrief_username: Optional[str] = None,
    simbrief_user_id: Optional[str] = None,
    vamsys_client: Optional[VamsysClient] = None,
    vamsys_booking_id: Optional[str] = None,
    fetch_live_winds: bool = True,
    wind_service: Optional[OpenMeteoWindService] = None,
) -> UnifiedFlightData:
    """Build a UnifiedFlightData by fetching from all available sources.

    Sources are best-effort — if one fails or is not configured, the
    remaining sources still populate the result.
    """
    ufd = UnifiedFlightData()
    ofp: Optional[OFPData] = None

    # 1) SimBrief OFP
    if simbrief_username or simbrief_user_id:
        fetcher = SimBriefOFPFetcher()
        try:
            ofp = await fetcher.fetch(
                username=simbrief_username, user_id=simbrief_user_id
            )
            _merge_ofp(ufd, ofp)
        except Exception:
            pass  # graceful degradation

    # 2) vAMSYS
    if vamsys_client is not None:
        try:
            profile = await vamsys_client.get_pilot_profile()
            _merge_profile(ufd, profile)
        except Exception:
            pass

        if vamsys_booking_id:
            try:
                bookings = await vamsys_client.get_booked_flights()
                match = next(
                    (b for b in bookings if b.booking_id == vamsys_booking_id),
                    None,
                )
                if match:
                    _merge_booking(ufd, match)
                    ufd._booking = match
            except Exception:
                pass

    # 3) Live winds from Open-Meteo
    if fetch_live_winds and ofp and ofp.waypoints:
        svc = wind_service or OpenMeteoWindService()
        wps = [(w.lat, w.lon) for w in ofp.waypoints]
        fl = ofp.planned_cruise_fl or 350
        if len(wps) >= 2:
            try:
                winds = await svc.get_winds_along_route(wps, flight_level=fl)
                _merge_winds(ufd, winds)
            except Exception:
                pass

    return ufd
