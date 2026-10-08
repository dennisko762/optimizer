"""Tests for weather_integration.py — wind computation and data models."""

from __future__ import annotations


import pytest

from src.adapters.lufthansa_virtual.weather_integration import (
    OpenMeteoWindService,
    RouteWindSummary,
    WindPoint,
    _track_between,
)


# ------------------------------------------------------------------
# WindPoint tests
# ------------------------------------------------------------------


class TestWindPoint:
    def test_wind_speed_kt(self):
        # Pure eastward wind at 10 m/s ≈ 19.4 kt
        wp = WindPoint(lat=0, lon=0, wind_u_ms=10.0, wind_v_ms=0.0)
        assert wp.wind_speed_kt == pytest.approx(19.4384, rel=1e-3)

    def test_wind_dir_westerly(self):
        # U=10 (eastward), V=0 → wind is blowing FROM the west (270°)
        wp = WindPoint(lat=0, lon=0, wind_u_ms=10.0, wind_v_ms=0.0)
        assert wp.wind_dir_deg == pytest.approx(270.0, abs=0.5)

    def test_wind_dir_southerly(self):
        # U=0, V=10 (northward) → wind FROM the south (180°)
        wp = WindPoint(lat=0, lon=0, wind_u_ms=0.0, wind_v_ms=10.0)
        assert wp.wind_dir_deg == pytest.approx(180.0, abs=0.5)

    def test_headwind_component_pure_headwind(self):
        # Wind from 270, track heading 270 → full headwind
        wp = WindPoint(lat=0, lon=0, wind_u_ms=10.0, wind_v_ms=0.0)
        hw = wp.headwind_component_kt(track_deg=270.0)
        assert hw == pytest.approx(wp.wind_speed_kt, rel=1e-2)

    def test_headwind_component_pure_tailwind(self):
        # Wind from 270, track heading 90 → full tailwind (negative)
        wp = WindPoint(lat=0, lon=0, wind_u_ms=10.0, wind_v_ms=0.0)
        hw = wp.headwind_component_kt(track_deg=90.0)
        assert hw == pytest.approx(-wp.wind_speed_kt, rel=1e-2)

    def test_headwind_component_crosswind(self):
        # Wind from 270, track heading 0 (north) → crosswind ≈ 0
        wp = WindPoint(lat=0, lon=0, wind_u_ms=10.0, wind_v_ms=0.0)
        hw = wp.headwind_component_kt(track_deg=0.0)
        assert abs(hw) < 0.5  # near zero

    def test_zero_wind(self):
        wp = WindPoint(lat=0, lon=0, wind_u_ms=0.0, wind_v_ms=0.0)
        assert wp.wind_speed_kt == 0.0
        assert wp.headwind_component_kt(180.0) == 0.0


# ------------------------------------------------------------------
# Track bearing tests
# ------------------------------------------------------------------


class TestTrackBetween:
    def test_eastward(self):
        # From (0,0) to (0,10) → heading ~90°
        track = _track_between(0, 0, 0, 10)
        assert track == pytest.approx(90.0, abs=1.0)

    def test_westward(self):
        track = _track_between(0, 10, 0, 0)
        assert track == pytest.approx(270.0, abs=1.0)

    def test_northward(self):
        track = _track_between(0, 0, 10, 0)
        assert track == pytest.approx(0.0, abs=1.0)

    def test_southward(self):
        track = _track_between(10, 0, 0, 0)
        assert track == pytest.approx(180.0, abs=1.0)

    def test_eddf_to_kjfk(self):
        # Frankfurt (50.03, 8.57) → JFK (40.64, -73.78) → roughly 285°
        track = _track_between(50.03, 8.57, 40.64, -73.78)
        assert 275 < track < 310  # westbound North Atlantic


# ------------------------------------------------------------------
# RouteWindSummary tests
# ------------------------------------------------------------------


class TestRouteWindSummary:
    def test_construction(self):
        pts = [
            WindPoint(lat=50, lon=8, wind_u_ms=15, wind_v_ms=0),
            WindPoint(lat=45, lon=-40, wind_u_ms=20, wind_v_ms=5),
        ]
        s = RouteWindSummary(
            points=pts,
            avg_headwind_kt=-10.0,
            max_headwind_kt=5.0,
            max_tailwind_kt=25.0,
            track_deg=285.0,
        )
        assert len(s.points) == 2
        assert s.avg_headwind_kt == -10.0


# ------------------------------------------------------------------
# Service construction tests
# ------------------------------------------------------------------


class TestOpenMeteoWindService:
    def test_pressure_level_fl350(self):
        svc = OpenMeteoWindService()
        assert svc._pressure_level(350) == 225

    def test_pressure_level_fl300(self):
        svc = OpenMeteoWindService()
        assert svc._pressure_level(300) == 250

    def test_pressure_level_fallback(self):
        svc = OpenMeteoWindService()
        # FL 345 not in map → should pick closest (350 → 225)
        assert svc._pressure_level(345) == 225

    def test_requires_two_waypoints(self):
        svc = OpenMeteoWindService()
        with pytest.raises(ValueError, match="at least 2"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                svc.get_winds_along_route([(50.0, 8.0)], flight_level=350)
            )
