"""Route sampler: interpolate grid fields at arbitrary lat/lon/FL/ETA.

Bilinear interpolation on the ingested 0.25 deg grid (longitude axis wraps
at the antimeridian), plus great-circle helpers for route geometry. Pure
python so the interpolation and antimeridian cases are unit-testable.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

EARTH_RADIUS_NM = 3440.065


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2.0) ** 2
    )
    return EARTH_RADIUS_NM * 2.0 * math.asin(math.sqrt(min(1.0, h)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from (1) to (2), 0..360 deg."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(r2)
    x = math.cos(r1) * math.sin(r2) - math.sin(r1) * math.cos(r2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _wrap_lon(lon: float) -> float:
    """Wrap to -180..180."""
    return ((lon + 180.0) % 360.0) - 180.0


def bilinear(
    grid: Sequence[Sequence[float]],
    lat: Sequence[float],
    lon: Sequence[float],
    la: float,
    lo: float,
) -> Optional[float]:
    """Bilinear sample of a ``[lat][lon]`` grid at (la, lo).

    The longitude axis is periodic: if the grid spans the full globe (or its
    lon range wraps), the column index is computed modulo ``cols`` so points
    near the antimeridian (e.g. lon 179.8 vs -179.8 on a grid spanning
    179.75..-179.75) interpolate correctly. Returns None when the point is
    outside the grid (lat always; lon only when the grid does not wrap).
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if rows < 2 or cols < 2:
        return None
    lat_min, lat_max = float(lat[0]), float(lat[-1])
    if la < lat_min or la > lat_max:
        return None
    lon0, lon1 = float(lon[0]), float(lon[1])
    # True eastward per-step spacing, resolving the antimeridian jump:
    # a grid stored as [..., 179.75, -179.75] has step 0.5, not -359.5.
    step = (lon1 - lon0) % 360.0
    if step <= 0.0:
        return None
    periodic = cols * step >= 360.0 - 2.0 * step  # covers (nearly) the globe
    rel = (lo - lon0) % 360.0
    fx = rel / step
    if periodic:
        fx %= cols  # last interval wraps back to column 0
    else:
        # finite window: the point must sit inside it
        if fx > cols - 1.0 + 1e-9:
            return None
        fx = min(fx, cols - 1.0 - 1e-12)
    fy = (la - lat_min) / (lat_max - lat_min) * (rows - 1)
    fy = min(max(fy, 0.0), rows - 1.0 - 1e-12)
    x0 = int(math.floor(fx))
    if x0 >= cols:
        x0 = cols - 1
    x1 = (x0 + 1) % cols if periodic else min(x0 + 1, cols - 1)
    y0 = int(math.floor(fy))
    y1 = min(y0 + 1, rows - 1)
    tx = fx - x0
    ty = fy - y0
    v00, v10 = grid[y0][x0], grid[y0][x1]
    v01, v11 = grid[y1][x0], grid[y1][x1]
    if any(v is None for v in (v00, v10, v01, v11)):
        return None
    # GRIB2 can carry a per-point count/extra dim; collapse to the first value
    def _sc(v):
        try:
            import numpy as _np
            a = _np.asarray(v)
            if a.size != 1:
                a = a.reshape(-1)[0]
            return float(a)
        except (TypeError, ValueError):
            return None
    v00, v10, v01, v11 = map(_sc, (v00, v10, v01, v11))
    if None in (v00, v10, v01, v11):
        return None
    top = v00 + (v10 - v00) * tx
    bot = v01 + (v11 - v01) * tx
    return float(top + (bot - top) * ty)


def sample_at_fl(
    field_by_level: dict[float, Sequence[Sequence[float]]],
    level_order: Sequence[float],
    lat: Sequence[float],
    lon: Sequence[float],
    la: float,
    lo: float,
    fl: float,
) -> Optional[float]:
    """Sample a field at a flight level by linear pressure interpolation
    between the two bracketing ingested levels (see ``levels.py``).

    ``field_by_level`` maps hPa level -> grid; ``level_order`` is the
    ascending list of ingested levels present in the dataset.
    """
    from . import levels

    p_upper, p_lower = levels.fl_pressure_band(fl)
    t = levels.fl_fraction(fl)
    avail = [l for l in level_order if l in field_by_level]
    if not avail:
        return None
    # A level sitting on a standard ingested level samples it directly.
    if p_upper == p_lower:
        key = p_upper
        if key not in field_by_level:
            # fall back to the closest available level
            key = min(avail, key=lambda l: abs(l - key))
        return bilinear(field_by_level[key], lat, lon, la, lo)
    # Otherwise bracket with the two standard levels; if the dataset lacks
    # one of them, use the closest available levels on each side.
    lo_lvl = p_lower if p_lower in field_by_level else min(
        (l for l in avail if l >= p_lower), default=None
    )
    up_lvl = p_upper if p_upper in field_by_level else max(
        (l for l in avail if l <= p_upper), default=None
    )
    if lo_lvl is None or up_lvl is None or lo_lvl == up_lvl:
        return None
    a = bilinear(field_by_level[lo_lvl], lat, lon, la, lo)   # larger pressure
    b = bilinear(field_by_level[up_lvl], lat, lon, la, lo)   # smaller pressure
    if a is None or b is None:
        return None
    return levels.lerp(a, b, t)


def route_geometry(
    points: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Augment ordered route points with great-circle leg/distance facts.

    ``points`` must be in route order: each has ``lat``/``lon`` (and an
    ``ident``). Returns the same list plus per-point ``dist_nm`` (distance
    from the previous point), ``cum_nm`` (cumulative from the first), and
    ``bearing_deg`` (bearing of the *incoming* leg; first point is None).
    Antimeridian crossings are handled by haversine (pure distance); the
    *drawn* line segment choice belongs to the client (see
    ``split_around_antimeridian``).
    """
    out: list[dict[str, Any]] = []
    prev: Optional[tuple[float, float]] = None
    cum = 0.0
    for i, p in enumerate(points):
        lat, lon = float(p["lat"]), _wrap_lon(float(p["lon"]))
        q = dict(p)
        q["lat"] = lat
        q["lon"] = lon
        if prev is not None:
            d = haversine_nm(prev[0], prev[1], lat, lon)
            q["dist_nm"] = round(d, 1)
            cum += d
            q["bearing_deg"] = round(initial_bearing_deg(prev[0], prev[1], lat, lon), 1)
        else:
            q["dist_nm"] = 0.0
            q["bearing_deg"] = None
        q["cum_nm"] = round(cum, 1)
        out.append(q)
        prev = (lat, lon)
    return out


def split_around_antimeridian(
    points: Sequence[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Split a route into sub-polyline segments at antimeridian crossings.

    A consecutive pair whose longitude jumps by > 180 deg is a crossing;
    the segment list is split there so each sub-polyline can be drawn
    without a spurious 180-deg chord. Points keep their original (wrapped)
    coordinates; the client draws each sub-segment and may render a short
    connector if desired.
    """
    if not points:
        return []
    segments: list[list[dict[str, Any]]] = [[dict(points[0])]]
    for prev, cur in zip(points, points[1:]):
        lo_prev = _wrap_lon(float(prev["lon"]))
        lo_cur = _wrap_lon(float(cur["lon"]))
        if abs(lo_cur - lo_prev) > 180.0:
            # crossing: the crossing point terminates the previous segment and
            # starts the next, so each sub-polyline is self-contained.
            segments[-1].append(dict(cur))
            segments.append([dict(cur)])
        else:
            segments[-1].append(dict(cur))
    return segments
