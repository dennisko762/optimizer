"""Tests for simbrief_fetcher.py — OFP normalisation and client behaviour."""

from __future__ import annotations

import pytest

from src.adapters.lufthansa_virtual.simbrief_fetcher import (
    OFPData,
    OFPWaypoint,
    SimBriefFetcherError,
    SimBriefOFPFetcher,
    _normalise,
    _parse_pm,
    _safe_float,
    _safe_int,
)


# ------------------------------------------------------------------
# Helper / parsing unit tests
# ------------------------------------------------------------------


class TestParsePM:
    def test_positive(self):
        assert _parse_pm("P017") == 17.0

    def test_negative(self):
        assert _parse_pm("M025") == -25.0

    def test_none(self):
        assert _parse_pm(None) is None

    def test_empty(self):
        assert _parse_pm("") is None

    def test_single_char(self):
        assert _parse_pm("P") is None


class TestSafeConversions:
    def test_safe_float_valid(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_bad(self):
        assert _safe_float("abc") is None

    def test_safe_int_valid(self):
        assert _safe_int("350") == 350

    def test_safe_int_none(self):
        assert _safe_int(None) is None


# ------------------------------------------------------------------
# Normalisation tests
# ------------------------------------------------------------------

MINIMAL_SIMBRIEF_JSON = {
    "general": {
        "flight_number": "DLH456",
        "icao_airline": "DLH",
        "route_distance": "1234",
        "initial_altitude": "35000",
        "costindex": "80",
        "avg_wind_comp": "M015",
        "avg_temp_dev": "P03",
    },
    "origin": {
        "icao_code": "EDDF",
        "pos_lat": "50.0333",
        "pos_long": "8.5706",
    },
    "destination": {
        "icao_code": "KJFK",
        "pos_lat": "40.6399",
        "pos_long": "-73.7787",
    },
    "alternate": {"icao_code": "KEWR"},
    "fuel": {
        "plan_ramp": "50000",
        "enroute_burn": "40000",
        "reserve": "5000",
    },
    "weights": {
        "est_tow": "230000",
        "est_zfw": "180000",
        "pax_count": "250",
        "cargo": "5000",
    },
    "atc": {"initial_spd_mach": "0.84"},
    "times": {
        "sched_out": "1200",
        "sched_in": "2000",
        "sched_block": "480",
    },
    "params": {},
    "navlog": {
        "fix": [
            {
                "ident": "EDDF",
                "pos_lat": "50.0333",
                "pos_long": "8.5706",
                "altitude_feet": "0",
                "wind_dir": "270",
                "wind_spd": "15",
                "oat": "10",
            },
            {
                "ident": "NATOLA",
                "pos_lat": "52.0",
                "pos_long": "-20.0",
                "altitude_feet": "35000",
                "wind_dir": "280",
                "wind_spd": "80",
                "oat": "-55",
            },
            {
                "ident": "KJFK",
                "pos_lat": "40.6399",
                "pos_long": "-73.7787",
                "altitude_feet": "0",
            },
        ]
    },
}


class TestNormalise:
    def test_basic_fields(self):
        ofp = _normalise(MINIMAL_SIMBRIEF_JSON)
        assert ofp.flight_number == "DLH456"
        assert ofp.airline_icao == "DLH"
        assert ofp.origin == "EDDF"
        assert ofp.destination == "KJFK"
        assert ofp.alternate == "KEWR"
        assert ofp.route_distance_nm == pytest.approx(1234.0)
        assert ofp.planned_cruise_fl == 35000
        assert ofp.planned_cruise_mach == pytest.approx(0.84)
        assert ofp.cost_index == 80

    def test_fuel_weights(self):
        ofp = _normalise(MINIMAL_SIMBRIEF_JSON)
        assert ofp.tow_kg == pytest.approx(230000)
        assert ofp.zfw_kg == pytest.approx(180000)
        assert ofp.block_fuel_kg == pytest.approx(50000)
        assert ofp.trip_fuel_kg == pytest.approx(40000)
        assert ofp.reserve_fuel_kg == pytest.approx(5000)
        assert ofp.pax_count == 250
        assert ofp.cargo_kg == pytest.approx(5000)

    def test_wind_deviation(self):
        ofp = _normalise(MINIMAL_SIMBRIEF_JSON)
        assert ofp.avg_wind_component_kt == pytest.approx(-15.0)
        assert ofp.avg_isa_deviation_c == pytest.approx(3.0)

    def test_waypoints(self):
        ofp = _normalise(MINIMAL_SIMBRIEF_JSON)
        assert len(ofp.waypoints) == 3
        assert ofp.waypoints[0].ident == "EDDF"
        assert ofp.waypoints[1].wind_speed_kt == pytest.approx(80.0)

    def test_coordinates(self):
        ofp = _normalise(MINIMAL_SIMBRIEF_JSON)
        assert ofp.origin_lat == pytest.approx(50.0333)
        assert ofp.destination_lon == pytest.approx(-73.7787)

    def test_empty_navlog(self):
        raw = {**MINIMAL_SIMBRIEF_JSON, "navlog": {}}
        ofp = _normalise(raw)
        assert ofp.waypoints == []

    def test_missing_sections(self):
        ofp = _normalise({})
        assert ofp.origin is None
        assert ofp.waypoints == []


# ------------------------------------------------------------------
# Client construction tests (no network)
# ------------------------------------------------------------------


class TestSimBriefOFPFetcher:
    def test_requires_identity(self):
        fetcher = SimBriefOFPFetcher()
        with pytest.raises(ValueError, match="username or user_id"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(fetcher.fetch())
