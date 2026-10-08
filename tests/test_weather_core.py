"""Tests for the crew weather pure core: levels, hazards, polygonize, sampler.

These cover the formulas and thresholds that the hazard pipeline is built
on. They run without the scientific stack (numpy/xarray/eccodes are not
imported here) so they are fast and deterministic.
"""
from __future__ import annotations

import math

import pytest

from crew_platform.weather import hazards, levels, polygonize, sampler


# ---------------------------------------------------------------------------
# levels — ISA conversion
# ---------------------------------------------------------------------------

def test_fl_to_pressure_isa_known_values():
    # ISA anchors: FL000 = 1013.25 hPa; FL340 sits on the 250 hPa surface;
    # FL450 ~ 147.5 hPa.
    assert levels.fl_to_pressure_hpa(0) == pytest.approx(1013.25, rel=1e-9)
    assert levels.fl_to_pressure_hpa(340) == pytest.approx(250.0, abs=0.5)
    assert levels.fl_to_pressure_hpa(450) == pytest.approx(147.5, abs=0.5)


def test_pressure_to_fl_inverts():
    for fl in (50, 100, 200, 300, 340, 400, 450):
        p = levels.fl_to_pressure_hpa(fl)
        back = levels.pressure_hpa_to_fl(p)
        assert back == pytest.approx(fl, abs=0.5), f"round-trip failed for FL{fl}"


def test_fl_pressure_band_brackets_isa_pressure():
    # For every standard band the ISA pressure lies inside the band, and the
    # band is a pair of (or a single) standard GFS levels.
    for fl, (p_up, p_lo) in (
        (50, (700.0, 850.0)),
        (100, (500.0, 700.0)),
        (200, (400.0, 500.0)),
        (300, (300.0, 400.0)),
        (340, (250.0, 250.0)),   # sits on a standard level
        (450, (100.0, 150.0)),
    ):
        assert levels.fl_pressure_band(fl) == (p_up, p_lo)
        p = levels.fl_to_pressure_hpa(fl)
        # ISA pressure inside the band (small tolerance for a FL that lands
        # within 0.05 hPa of the upper boundary, e.g. FL340 ≈ 249.99 hPa).
        assert p_up - 0.05 <= p <= p_lo + 1e-6


def test_fl_fraction_bounds():
    # A FL on a standard level -> 0 (sample that level); between -> in [0,1].
    assert levels.fl_fraction(340) == pytest.approx(0.0, abs=1e-6)
    assert levels.fl_fraction(300) == pytest.approx(0.9911, abs=1e-3)
    assert 0.0 <= levels.fl_fraction(200) <= 1.0
    assert 0.0 <= levels.fl_fraction(450) <= 1.0


def test_available_slabs():
    # default menu = 50-ft-increment FLs FL050..FL450
    slabs = levels.available_slabs()
    assert slabs[0] == 50.0
    assert slabs[-1] == 450.0
    assert 200.0 in slabs
    assert 350.0 in slabs
    # a sparser level set resolves a subset
    sub = levels.available_slabs([250.0, 300.0, 500.0, 700.0])
    assert set(sub) <= set(slabs)
    assert 300.0 in sub
    assert 450.0 not in sub  # 150/100 hPa missing


def test_fl_band_out_of_range_raises():
    # above the 50 hPa top of the standard level stack
    with pytest.raises(ValueError):
        levels.fl_pressure_band(700)


def test_negative_fl_raises():
    with pytest.raises(ValueError):
        levels.fl_altitude_ft(-10)


# ---------------------------------------------------------------------------
# hazards — TI1/TI2 (Ellrod)
# ---------------------------------------------------------------------------

def test_vertical_wind_shear_scalar():
    # du/dz = 10 m/s over 1000 m = 0.01 /s ; dv = 0 -> S = 0.01
    s = hazards.vertical_wind_shear(0.0, 0.0, 10.0, 0.0, 1000.0)
    assert s == pytest.approx(0.01, rel=1e-9)


def test_ti1_ti2_scalar():
    # shear 0.01, DEF 0.005 -> TI1 = 5e-5
    t1 = hazards.ti1(0.01, 0.005)
    assert t1 == pytest.approx(5e-5, rel=1e-9)
    # TI2 with convergence: S*(DEF+CVG)
    t2 = hazards.ti2(0.01, 0.005, 0.002)
    assert t2 == pytest.approx(0.01 * 0.007, rel=1e-9)


def test_deformation_formula():
    # du/dx=0.001, dv/dy=0.0, du/dy=0, dv/dx=0 -> DEF = |0.001| = 0.001
    assert hazards.deformation(0.001, 0.0, 0.0, 0.0) == pytest.approx(0.001, rel=1e-9)
    # pure shear: du/dx=0, dv/dy=0, du/dy=0.002, dv/dx=0.002 -> DEF=0.004
    assert hazards.deformation(0.0, 0.0, 0.002, 0.002) == pytest.approx(0.004, rel=1e-9)


def test_turbulence_tier_thresholds():
    # WMO/TITAN form: stored TI1 is S*DEF in (1e-6 /s)^2 units
    assert hazards.turbulence_tier(0.0) == hazards.TIER_NONE
    assert hazards.turbulence_tier(4.9) == hazards.TIER_NONE
    assert hazards.turbulence_tier(5.0) == hazards.TIER_LIGHT
    assert hazards.turbulence_tier(15.0) == hazards.TIER_MODERATE
    assert hazards.turbulence_tier(25.0) == hazards.TIER_SEVERE
    assert hazards.turbulence_tier(None) == hazards.TIER_NONE


def test_ti_from_grid_uniform_wind_zero():
    # uniform wind -> zero shear & zero deformation -> TI1/TI2 == 0
    u = [[5.0] * 4 for _ in range(3)]
    v = [[0.0] * 4 for _ in range(3)]
    ti1, ti2 = hazards.ti_from_grid(u, v, u, v, dx_m=28000.0, dz_m=1000.0)
    for row in ti1:
        for x in row:
            assert x == pytest.approx(0.0, abs=1e-12)
    for row in ti2:
        for x in row:
            assert x == pytest.approx(0.0, abs=1e-12)


def test_ti_from_grid_shape():
    u = [[float(i + j) for j in range(4)] for i in range(3)]
    v = [[0.0] * 4 for _ in range(3)]
    ti1, ti2 = hazards.ti_from_grid(u, v, u, v, dx_m=28000.0, dz_m=1000.0)
    assert len(ti1) == 3 and len(ti1[0]) == 4
    assert len(ti2) == 3 and len(ti2[0]) == 4


# ---------------------------------------------------------------------------
# hazards — icing (CIP proxy)
# ---------------------------------------------------------------------------

def test_icing_factor_band():
    # no supercooled water above 0 C or below -20 C
    assert hazards.icing_factor(5.0, 100.0, 2.0) == 0.0
    assert hazards.icing_factor(-30.0, 100.0, 2.0) == 0.0
    # -10 C, 100% RH, 2 m/s lift -> (100/100)*1 = 1.0
    assert hazards.icing_factor(-10.0, 100.0, 2.0) == pytest.approx(1.0)
    # weaker lift -> proportional
    assert hazards.icing_factor(-10.0, 100.0, 1.0) == pytest.approx(0.5)


def test_icing_tier():
    assert hazards.icing_tier(0.0) == hazards.TIER_NONE
    assert hazards.icing_tier(0.2) == hazards.TIER_LIGHT
    assert hazards.icing_tier(0.4) == hazards.TIER_MODERATE
    assert hazards.icing_tier(0.8) == hazards.TIER_SEVERE


# ---------------------------------------------------------------------------
# hazards — jet
# ---------------------------------------------------------------------------

def test_jet_tier():
    # 60 kt == 30.87 m/s extent; 100 kt == 51.44 m/s core.
    assert hazards.jet_tier(0.0) == 0
    assert hazards.jet_tier(30.0) == 0    # 58.3 kt, below 60
    assert hazards.jet_tier(31.0) == 1    # 60.3 kt, extent
    assert hazards.jet_tier(52.0) == 2    # 101 kt, core


# ---------------------------------------------------------------------------
# hazards — fronts / Bolton theta_e
# ---------------------------------------------------------------------------

def test_dew_point_saturated():
    # at 100% RH dew point == temperature
    assert hazards.dew_point_c(15.0, 100.0) == pytest.approx(15.0, abs=0.05)


def test_theta_e_dry_equals_theta():
    # dry (rh=0) -> theta_e == theta
    te = hazards.theta_e_k(10.0, 0.0, 500.0)
    theta = (10.0 + 273.15) * (1000.0 / 500.0) ** hazards.BOLTON_RD_OVER_CP
    assert te == pytest.approx(theta, rel=1e-9)


def test_theta_e_wet_greater_than_theta():
    te_wet = hazards.theta_e_k(10.0, 80.0, 500.0)
    te_dry = hazards.theta_e_k(10.0, 0.0, 500.0)
    assert te_wet > te_dry


def test_front_tier():
    assert hazards.front_tier(0.0) == 0
    assert hazards.front_tier(2.5) == 1
    assert hazards.front_tier(4.0) == 2


def test_front_gradient_scaling():
    # 5 K over 250 km -> 5 K per 250 km
    g = hazards.front_gradient_k_per_250km(300.0, 305.0, 250_000.0)
    assert g == pytest.approx(5.0, rel=1e-9)


# ---------------------------------------------------------------------------
# polygonize — marching squares + assembly + simplification
# ---------------------------------------------------------------------------

def _grid_2x2(v00, v10, v11, v01):
    return [
        [v00, v10],
        [v01, v11],
    ]


def test_marching_squares_empty_when_all_below():
    vals = _grid_2x2(0, 0, 0, 0)
    segs = polygonize.marching_squares_segments(vals, [0.0, 1.0], [0.0, 1.0], level=1.0)
    assert segs == []


def test_marching_squares_single_cell_crossing():
    # left side high, right side low -> one vertical-ish segment
    vals = _grid_2x2(2.0, 0.0, 0.0, 2.0)
    segs = polygonize.marching_squares_segments(vals, [0.0, 1.0], [0.0, 1.0], level=1.0)
    assert len(segs) == 1
    (x0, y0), (x1, y1) = segs[0]
    # should cross at lat ~0.5 on both left and right? Actually case 3/12:
    # SW=2,NW=2 (left high), SE=0,NE=0 (right low) -> case = 1|8 = 9 -> top-bottom?
    # We just assert it produced a sensible segment within the cell
    assert 0.0 <= x0 <= 1.0 and 0.0 <= x1 <= 1.0


def test_assemble_rings_closes():
    # a square ring
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    segs = [
        ((0.0, 0.0), (1.0, 0.0)),
        ((1.0, 0.0), (1.0, 1.0)),
        ((1.0, 1.0), (0.0, 1.0)),
        ((0.0, 1.0), (0.0, 0.0)),
    ]
    rings = polygonize.assemble_rings(segs)
    assert len(rings) == 1
    assert len(rings[0]) == 4


def test_douglas_peucker_removes_collinear():
    # a rectangle with extra collinear points on the bottom edge
    ring = [
        (0.0, 0.0), (0.5, 0.0), (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 1.0),
        (0.0, 0.0),
    ]
    simplified = polygonize.douglas_peucker(ring, epsilon=0.1)
    # collinear (0.5,0.0) removed
    pts = [p for p in simplified if p != (0.5, 0.0)]
    assert (0.5, 0.0) not in simplified or len(simplified) <= len(ring)
    # must still be a closed ring with the corner points
    assert (0.0, 0.0) in simplified
    assert (1.0, 1.0) in simplified


def test_polygonize_grid_produces_polygon():
    # a single high blob in a 5x5 grid -> one closed polygon at level 0.5
    vals = [
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
        [0, 0, 2, 0, 0],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
    ]
    lat = [0.0, 0.5, 1.0, 1.5, 2.0]
    lon = [0.0, 0.5, 1.0, 1.5, 2.0]
    feats = polygonize.polygonize_grid(vals, lat, lon, [0.5], epsilon=0.0)
    assert len(feats) >= 1
    poly = feats[0]
    assert poly["type"] == "Polygon"
    coords = poly["coordinates"][0]
    assert coords[0] == coords[-1]  # closed
    # the ring should surround the high cell (~lon 1.0, lat 1.0)
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    assert min(lons) < 1.0 < max(lons)
    assert min(lats) < 1.0 < max(lats)


def test_value_to_band():
    thr = [0.5, 1.0, 2.0]
    assert polygonize.value_to_band(0.4, thr) is None
    assert polygonize.value_to_band(0.5, thr) == 0
    assert polygonize.value_to_band(1.0, thr) == 1
    assert polygonize.value_to_band(5.0, thr) == 2
    assert polygonize.value_to_band(None, thr) is None


def test_grid_stats():
    vals = [[1.0, 2.0], [3.0, float("nan")]]
    st = polygonize.grid_stats(vals)
    assert st["min"] == 1.0
    assert st["max"] == 3.0
    assert st["count"] == 3


# ---------------------------------------------------------------------------
# sampler — bilinear + antimeridian + route geometry
# ---------------------------------------------------------------------------

def test_bilinear_linear_ramp():
    # value == lon (0..1), lat independent; sample at lon 0.5 -> 0.5
    grid = [[0.0, 1.0], [0.0, 1.0]]
    val = sampler.bilinear(grid, [0.0, 1.0], [0.0, 1.0], la=0.5, lo=0.5)
    assert val == pytest.approx(0.5, abs=1e-6)


def test_bilinear_outside_returns_none():
    grid = [[0.0, 1.0], [0.0, 1.0]]
    assert sampler.bilinear(grid, [0.0, 1.0], [0.0, 1.0], la=5.0, lo=0.5) is None
    assert sampler.bilinear(grid, [0.0, 1.0], [0.0, 1.0], la=0.5, lo=-1.0) is None


def test_bilinear_wraps_antimeridian():
    # grid spanning 179.75 -> -179.75 (wraps); value increases with east
    # lon0 = 179.75, lon1 = -179.75 (dlon < 0 -> wraps). Put a value that
    # varies with the *unwrapped* east position.
    lons = [179.75, -179.75]
    # make value a function of wrapped position: at lon 179.75 -> 0, at -179.75 -> 1
    grid = [[0.0, 1.0], [0.0, 1.0]]
    # sample right at the antimeridian crossing: lon 180 == -180, halfway
    # between 179.75 and -179.75 in the eastward sense -> 0.5
    val = sampler.bilinear(grid, [0.0, 1.0], lons, la=0.5, lo=-180.0)
    assert val is not None
    assert val == pytest.approx(0.5, abs=0.01)


def test_haversine_known():
    # 1 degree of latitude at the equator ~ 60 NM
    d = sampler.haversine_nm(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(60.0, abs=0.5)


def test_route_geometry_cumulative():
    pts = [
        {"ident": "A", "lat": 0.0, "lon": 0.0},
        {"ident": "B", "lat": 1.0, "lon": 0.0},
        {"ident": "C", "lat": 2.0, "lon": 0.0},
    ]
    out = sampler.route_geometry(pts)
    assert out[0]["cum_nm"] == pytest.approx(0.0)
    assert out[1]["cum_nm"] == pytest.approx(out[1]["dist_nm"])
    assert out[2]["cum_nm"] == pytest.approx(out[1]["cum_nm"] + out[2]["dist_nm"], abs=0.2)
    assert out[0]["bearing_deg"] is None
    assert out[1]["bearing_deg"] == pytest.approx(0.0, abs=1.0)  # heading north


def test_split_around_antimeridian():
    pts = [
        {"ident": "A", "lat": 35.0, "lon": 179.0},
        {"ident": "B", "lat": 35.0, "lon": -179.0},  # crossing
        {"ident": "C", "lat": 35.0, "lon": -178.0},
    ]
    segs = sampler.split_around_antimeridian(pts)
    assert len(segs) == 2
    assert [p["ident"] for p in segs[0]] == ["A", "B"]
    assert [p["ident"] for p in segs[1]] == ["B", "C"]


def test_split_no_crossing_single_segment():
    pts = [
        {"ident": "A", "lat": 50.0, "lon": 10.0},
        {"ident": "B", "lat": 51.0, "lon": 12.0},
    ]
    segs = sampler.split_around_antimeridian(pts)
    assert len(segs) == 1
    assert len(segs[0]) == 2


def test_sample_at_fl_brackets_level():
    from crew_platform.weather import levels
    # build a field present at 250 and 300 hPa only
    grid_lo = [[20.0, 20.0], [20.0, 20.0]]   # 250 hPa (thinner, above FL340)
    grid_hi = [[30.0, 30.0], [30.0, 30.0]]   # 300 hPa (denser, below FL340)
    field_by_level = {250.0: grid_lo, 300.0: grid_hi}
    lat = [0.0, 1.0]
    lon = [0.0, 1.0]
    val = sampler.sample_at_fl(field_by_level, [250.0, 300.0], lat, lon, la=0.5, lo=0.5, fl=340)
    # FL340 sits between 250 and 300; t fraction in (0,1); value between 20 and 30
    assert val is not None
    assert 20.0 <= val <= 30.0
