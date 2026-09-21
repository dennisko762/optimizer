"""Tests for unified_flight_data.py — merge logic and builder."""

from __future__ import annotations

import asyncio

import pytest

from src.adapters.lufthansa_virtual.simbrief_fetcher import OFPData, OFPWaypoint
from src.adapters.lufthansa_virtual.vamsys_client import BookedFlight, PilotProfile
from src.adapters.lufthansa_virtual.weather_integration import (
    RouteWindSummary,
    WindPoint,
)
from src.adapters.lufthansa_virtual.unified_flight_data import (
    UnifiedFlightData,
    _merge_booking,
    _merge_ofp,
    _merge_profile,
    _merge_winds,
)


# ------------------------------------------------------------------
# Merge helpers
# ------------------------------------------------------------------

SAMPLE_OFP = OFPData(
    flight_number="DLH456",
    airline_icao="DLH",
    origin="EDDF",
    destination="KJFK",
    alternate="KEWR",
    route_distance_nm=3400.0,
    planned_cruise_fl=350,
    planned_cruise_mach=0.84,
    cost_index=80,
    tow_kg=230000.0,
    zfw_kg=180000.0,
    block_fuel_kg=50000.0,
    trip_fuel_kg=40000.0,
    reserve_fuel_kg=5000.0,
    pax_count=250,
    cargo_kg=5000.0,
    planned_departure_utc="1200",
    planned_arrival_utc="2000",
    planned_block_time_min=480.0,
    avg_wind_component_kt=-15.0,
    avg_isa_deviation_c=3.0,
    waypoints=[
        OFPWaypoint(ident="EDDF", lat=50.03, lon=8.57, altitude_ft=0),
        OFPWaypoint(ident="KJFK", lat=40.64, lon=-73.78, altitude_ft=0),
    ],
)


class TestMergeOFP:
    def test_basic_fields(self):
        ufd = UnifiedFlightData()
        _merge_ofp(ufd, SAMPLE_OFP)
        assert ufd.flight_number == "DLH456"
        assert ufd.origin == "EDDF"
        assert ufd.destination == "KJFK"
        assert ufd.cost_index == 80
        assert ufd.source_simbrief is True

    def test_fuel_weights(self):
        ufd = UnifiedFlightData()
        _merge_ofp(ufd, SAMPLE_OFP)
        assert ufd.tow_kg == 230000.0
        assert ufd.trip_fuel_kg == 40000.0

    def test_wind_from_ofp(self):
        ufd = UnifiedFlightData()
        _merge_ofp(ufd, SAMPLE_OFP)
        assert ufd.avg_headwind_kt == -15.0
        assert ufd.avg_isa_deviation_c == 3.0


class TestMergeBooking:
    def test_fills_va_fields(self):
        ufd = UnifiedFlightData()
        booking = BookedFlight(
            booking_id="BK100",
            flight_number="DLH456",
            departure_icao="EDDF",
            arrival_icao="KJFK",
            aircraft_icao="B77W",
            status="booked",
        )
        _merge_booking(ufd, booking)
        assert ufd.va_booking_id == "BK100"
        assert ufd.va_aircraft_icao == "B77W"
        assert ufd.source_vamsys is True

    def test_does_not_overwrite_simbrief(self):
        ufd = UnifiedFlightData()
        _merge_ofp(ufd, SAMPLE_OFP)
        booking = BookedFlight(
            booking_id="BK100",
            flight_number="DIFFERENT",
            departure_icao="EDDM",
            arrival_icao="EGLL",
        )
        _merge_booking(ufd, booking)
        # SimBrief data takes priority
        assert ufd.flight_number == "DLH456"
        assert ufd.origin == "EDDF"

    def test_fills_missing_fields(self):
        ufd = UnifiedFlightData()
        booking = BookedFlight(
            booking_id="BK200",
            flight_number="DLH789",
            departure_icao="EDDM",
            arrival_icao="EGLL",
        )
        _merge_booking(ufd, booking)
        assert ufd.flight_number == "DLH789"
        assert ufd.origin == "EDDM"


class TestMergeProfile:
    def test_sets_pilot_info(self):
        ufd = UnifiedFlightData()
        profile = PilotProfile(
            pilot_id="42",
            callsign="DLH42",
            rank="Captain",
            hours_total=1500.0,
            airline_icao="DLH",
        )
        _merge_profile(ufd, profile)
        assert ufd.pilot_callsign == "DLH42"
        assert ufd.pilot_rank == "Captain"
        assert ufd.airline_icao == "DLH"


class TestMergeWinds:
    def test_overrides_ofp_winds(self):
        ufd = UnifiedFlightData()
        _merge_ofp(ufd, SAMPLE_OFP)
        assert ufd.avg_headwind_kt == -15.0  # from OFP

        winds = RouteWindSummary(
            points=[
                WindPoint(lat=50, lon=8, wind_u_ms=15, wind_v_ms=0),
                WindPoint(lat=40, lon=-73, wind_u_ms=20, wind_v_ms=5),
            ],
            avg_headwind_kt=-25.0,
            max_headwind_kt=5.0,
            max_tailwind_kt=35.0,
            track_deg=285.0,
        )
        _merge_winds(ufd, winds)
        assert ufd.avg_headwind_kt == -25.0  # overridden by live
        assert ufd.source_open_meteo is True


# ------------------------------------------------------------------
# UnifiedFlightData construction
# ------------------------------------------------------------------


class TestUnifiedFlightData:
    def test_defaults(self):
        ufd = UnifiedFlightData()
        assert ufd.flight_number is None
        assert ufd.source_simbrief is False
        assert ufd.source_vamsys is False
        assert ufd.source_open_meteo is False

    def test_full_merge_sequence(self):
        """Simulate the full merge pipeline without network."""
        ufd = UnifiedFlightData()
        _merge_ofp(ufd, SAMPLE_OFP)
        _merge_profile(
            ufd,
            PilotProfile(
                pilot_id="42", callsign="DLH42", rank="Captain",
                airline_icao="DLH",
            ),
        )
        _merge_booking(
            ufd,
            BookedFlight(
                booking_id="BK300",
                aircraft_icao="B77W",
                status="booked",
            ),
        )
        winds = RouteWindSummary(
            points=[],
            avg_headwind_kt=-20.0,
            max_headwind_kt=5.0,
            max_tailwind_kt=30.0,
            track_deg=285.0,
        )
        _merge_winds(ufd, winds)

        assert ufd.source_simbrief is True
        assert ufd.source_vamsys is True
        assert ufd.source_open_meteo is True
        assert ufd.flight_number == "DLH456"
        assert ufd.va_booking_id == "BK300"
        assert ufd.pilot_callsign == "DLH42"
        assert ufd.avg_headwind_kt == -20.0
