"""Tests for the exact-route extraction (routes_data).

Covers the hard acceptance cases: exact order of every navlog waypoint,
antimeridian splitting, and unresolved (missing/invalid coordinate) fixes
surfaced as explicit data errors — never silently dropped or guessed.
"""
from __future__ import annotations

import pytest

from crew_platform.weather.routes_data import RouteDataError, extract_route


def _view(waypoints, origin="EDDF", dest="RJAA", cruise=340):
    return {
        "origin": origin,
        "destination": dest,
        "cruise_fl": cruise,
        "route": "EDDF QTR RJAA",
        "callsign": "QTR815",
        "waypoints": waypoints,
    }


def test_order_and_geometry_preserved():
    rows = [
        {"ident": "EDDF", "lat": 50.0, "lon": 8.5, "alt": None, "stage": "DEP"},
        {"ident": "AMZ", "lat": 50.5, "lon": 10.0, "alt": 250, "airway": "UN861"},
        {"ident": "RJAA", "lat": 35.7, "lon": 140.0, "alt": None, "stage": "ARR"},
    ]
    out = extract_route(_view(rows))
    assert [p["ident"] for p in out["points"]] == ["EDDF", "AMZ", "RJAA"]
    assert out["point_count"] == 3
    assert out["origin"] == "EDDF"
    assert out["destination"] == "RJAA"
    assert out["points"][0]["is_origin"] is True
    assert out["points"][-1]["is_dest"] is True
    # geometry: cumulative distance strictly increases
    cums = [p["cum_nm"] for p in out["points"]]
    assert cums[0] == 0.0
    assert all(b > a for a, b in zip(cums, cums[1:]))
    # every fix appears in the GeoJSON in the same order
    pts = [f for f in out["geojson"]["features"] if f["geometry"]["type"] == "Point"]
    assert [f["properties"]["ident"] for f in pts] == ["EDDF", "AMZ", "RJAA"]


def test_antimeridian_route_splits_into_segments():
    rows = [
        {"ident": "EDDF", "lat": 50.0, "lon": 8.5, "stage": "DEP"},
        {"ident": "A", "lat": 40.0, "lon": 170.0},
        {"ident": "B", "lat": 45.0, "lon": -170.0},   # crosses the antimeridian
        {"ident": "C", "lat": 50.0, "lon": -140.0},
    ]
    out = extract_route(_view(rows, dest="C"))
    # two sub-polyline segments (split between A and B)
    assert len(out["segments"]) == 2
    # the crossing point (B, index 2) terminates seg0 AND starts seg1
    assert out["segments"][0][-1] == 2
    assert out["segments"][1][0] == 2
    # the geojson has exactly two route-line features
    lines = [f for f in out["geojson"]["features"] if f["geometry"]["type"] == "LineString"]
    assert len(lines) == 2


def test_unresolved_fix_surfaced_not_dropped():
    rows = [
        {"ident": "EDDF", "lat": 50.0, "lon": 8.5, "stage": "DEP"},
        {"ident": "MYSTERY", "lat": None, "lon": None},   # unresolved
        {"ident": "RJAA", "lat": 35.7, "lon": 140.0, "stage": "ARR"},
    ]
    out = extract_route(_view(rows))
    # the resolved route still has the two valid fixes, in order
    assert [p["ident"] for p in out["points"]] == ["EDDF", "RJAA"]
    # the unresolved fix is reported explicitly
    assert len(out["unresolved"]) == 1
    assert out["unresolved"][0]["ident"] == "MYSTERY"
    assert "coordinates" in out["unresolved"][0]["reason"]


def test_nan_coordinates_treated_as_unresolved():
    rows = [
        {"ident": "EDDF", "lat": 50.0, "lon": 8.5},
        {"ident": "BAD", "lat": float("nan"), "lon": 10.0},
    ]
    out = extract_route(_view(rows, dest="EDDF"))
    assert [p["ident"] for p in out["points"]] == ["EDDF"]
    assert [u["ident"] for u in out["unresolved"]] == ["BAD"]


def test_no_valid_coordinates_raises():
    rows = [{"ident": "X", "lat": None, "lon": None}]
    with pytest.raises(RouteDataError):
        extract_route(_view(rows))


def test_empty_waypoints_raises():
    with pytest.raises(RouteDataError):
        extract_route(_view([]))


# repeated navlog fixes must keep occurrence identity (#7: sample joining)
def test_repeated_fixes_keep_occurrence_identity():
    rows = [
        {"ident": "EDDF", "lat": 50.0, "lon": 8.5},
        {"ident": "ALDOX", "lat": 55.0, "lon": 60.0},
        {"ident": "ALDOX", "lat": 60.0, "lon": 100.0},  # same fix again
        {"ident": "RJAA", "lat": 35.0, "lon": 140.0},
    ]
    out = extract_route(_view(rows))
    occ = [(p["ident"], p["occurrence"]) for p in out["points"]]
    assert occ == [("EDDF", 0), ("ALDOX", 0), ("ALDOX", 1), ("RJAA", 0)]
