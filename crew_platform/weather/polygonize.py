"""Grid → GeoJSON polygonization for hazard tier bands.

Pure-python marching squares (linear edge interpolation) with contour
assembly into closed rings, plus Douglas-Peucker simplification. No
scientific-stack dependency so the geometry is unit-testable on tiny grids.

Coordinate convention: grids are ``[lat][lon]`` row-major with the ``lat``
axis increasing northward (index 0 == southernmost) and ``lon`` axis
increasing eastward (index 0 == westernmost). GFS 0.25 deg global grids are
221 x 288; subsets are rectangular boxes on the same convention.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

# ---------------------------------------------------------------------------
# Band helpers
# ---------------------------------------------------------------------------

def value_to_band(value: Optional[float], thresholds: Sequence[float]) -> Optional[int]:
    """Band index for a scalar: the highest band whose min threshold is
    exceeded. ``thresholds[i]`` is the *inclusive lower bound* of band i.
    Returns None when the value is missing or below band 0."""
    if value is None:
        return None
    band: Optional[int] = None
    for i, thr in enumerate(thresholds):
        if value >= thr:
            band = i
    return band


def grid_stats(values: Sequence[Sequence[float]]) -> dict[str, Any]:
    """min/max/count over a grid (NaN and missing = None entries skipped)."""
    mn: Optional[float] = None
    mx: Optional[float] = None
    n = 0
    for row in values:
        for v in row:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            n += 1
            if mn is None or v < mn:
                mn = v
            if mx is None or v > mx:
                mx = v
    return {"min": mn, "max": mx, "count": n}


# ---------------------------------------------------------------------------
# Marching squares (per-cell segments)
# ---------------------------------------------------------------------------

def _interp(a: float, b: float, level: float) -> float:
    """Fraction (0..1) along an edge where the value crosses ``level``."""
    if b == a:
        return 0.5
    t = (level - a) / (b - a)
    return min(1.0, max(0.0, t))


def marching_squares_segments(
    values: Sequence[Sequence[float]],
    lat: Sequence[float],
    lon: Sequence[float],
    level: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Line segments where the grid crosses ``level`` (marching squares).

    Returns a list of ``((lon0,lat0),(lon1,lat1))`` segments. Empty grid /
    all-below -> empty list.
    """
    rows = len(values)
    cols = len(values[0]) if rows else 0
    if rows < 2 or cols < 2:
        return []
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i in range(rows - 1):
        lat0, lat1 = float(lat[i]), float(lat[i + 1])
        for j in range(cols - 1):
            lon0, lon1 = float(lon[j]), float(lon[j + 1])
            # corners: v00 SW, v10 SE, v11 NE, v01 NW
            v00 = values[i][j]
            v10 = values[i][j + 1]
            v11 = values[i + 1][j + 1]
            v01 = values[i + 1][j]
            if None in (v00, v10, v11, v01):
                continue
            case = (
                (1 if v00 >= level else 0)
                | (2 if v10 >= level else 0)
                | (4 if v11 >= level else 0)
                | (8 if v01 >= level else 0)
            )
            if case in (0, 15):
                continue

            def p_bottom() -> tuple[float, float]:
                return (lon0 + _interp(v00, v10, level) * (lon1 - lon0), lat0)

            def p_right() -> tuple[float, float]:
                return (lon1, lat0 + _interp(v10, v11, level) * (lat1 - lat0))

            def p_top() -> tuple[float, float]:
                return (lon0 + _interp(v01, v11, level) * (lon1 - lon0), lat1)

            def p_left() -> tuple[float, float]:
                return (lon0, lat0 + _interp(v00, v01, level) * (lat1 - lat0))

            # segment endpoints per case (ambiguity 5/10 by center average)
            center = (v00 + v10 + v11 + v01) / 4.0
            if case == 1:
                segs.append((p_left(), p_bottom()))
            elif case == 2:
                segs.append((p_bottom(), p_right()))
            elif case == 3:
                segs.append((p_left(), p_right()))
            elif case == 4:
                segs.append((p_right(), p_top()))
            elif case == 5:
                # ambiguous (SW+NE above): center above -> the above region is
                # the SW/NE band, isolating the below corners (SE, NW)
                if center >= level:
                    segs.append((p_left(), p_top()))
                    segs.append((p_bottom(), p_right()))
                else:
                    segs.append((p_left(), p_bottom()))
                    segs.append((p_right(), p_top()))
            elif case == 6:
                segs.append((p_bottom(), p_top()))
            elif case == 7:
                segs.append((p_left(), p_top()))
            elif case == 8:
                segs.append((p_top(), p_left()))
            elif case == 9:
                segs.append((p_top(), p_bottom()))
            elif case == 10:
                # ambiguous (SE+NW above): center above -> the above region is
                # the SE/NW band, isolating the below corners (SW, NE)
                if center >= level:
                    segs.append((p_left(), p_bottom()))
                    segs.append((p_top(), p_right()))
                else:
                    segs.append((p_bottom(), p_right()))
                    segs.append((p_left(), p_top()))
            elif case == 11:
                # SW+SE+NW above, NE below: crossings on the TOP and RIGHT
                # edges only (the bottom edge is fully above -> no crossing).
                segs.append((p_top(), p_right()))
            elif case == 12:
                segs.append((p_right(), p_left()))
            elif case == 13:
                segs.append((p_bottom(), p_right()))
            elif case == 14:
                segs.append((p_bottom(), p_left()))
    return segs


def assemble_rings(
    segments: Sequence[tuple[tuple[float, float], tuple[float, float]]],
    eps: float = 1e-9,
) -> list[list[tuple[float, float]]]:
    """Join segments into closed loops (rings of (lon,lat) tuples).

    Endpoints are matched by rounded position (the same interpolated point
    appears in both segments that share an edge). Each undirected edge is
    traversed at most once; a loop is complete when it returns to its start
    vertex with no unused neighbour left. Fragments that never close (open
    contour ends at a grid boundary) are dropped.
    """
    def key(p: tuple[float, float]) -> tuple[float, float]:
        return (round(p[0], 9), round(p[1], 9))

    adj: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for a, b in segments:
        ka, kb = key(a), key(b)
        adj.setdefault(ka, []).append(kb)
        adj.setdefault(kb, []).append(ka)

    # unique undirected edges, in first-seen order
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    edge_set: set[frozenset[tuple[float, float]]] = set()
    for a, b in segments:
        e = frozenset((key(a), key(b)))
        if e not in edge_set:
            edge_set.add(e)
            edges.append((key(a), key(b)))

    used: set[frozenset[tuple[float, float]]] = set()
    rings: list[list[tuple[float, float]]] = []
    for s in list(adj):
        if any(s in u for u in used):
            continue  # this vertex is already consumed by a ring
        if not adj.get(s):
            continue
        ring = [s]
        prev = None
        cur = s
        closed = False
        for _ in range(len(edges) + 2):
            options = [n for n in adj.get(cur, [])
                       if frozenset((cur, n)) not in used]
            if not options:
                break
            nxt = options[0]
            used.add(frozenset((cur, nxt)))
            prev, cur = cur, nxt
            if cur == s:
                if not any(frozenset((s, x)) not in used for x in adj.get(s, ())):
                    closed = True
                    break
                continue  # pass through the start, don't re-append it
            ring.append(cur)
        if closed and len(ring) >= 3:
            rings.append(ring)
    return rings


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------

def _point_seg_dist(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = min(1.0, max(0.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def douglas_peucker(
    ring: Sequence[tuple[float, float]], epsilon: float
) -> list[tuple[float, float]]:
    """RDP simplification of a closed ring (first point == last point kept)."""
    if epsilon <= 0 or len(ring) < 4:
        return list(ring)
    # open the closed ring at its middle index — RDP on the open polyline
    # keeps the geometry of both halves
    far = len(ring) // 2
    open_ring = list(ring[far:]) + list(ring[:far + 1])
    n = len(open_ring)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        if e <= s + 1:
            continue
        dmax = -1.0
        idx = -1
        for i in range(s + 1, e):
            d = _point_seg_dist(open_ring[i], open_ring[s], open_ring[e])
            if d > dmax:
                dmax, idx = d, i
        if dmax > epsilon:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    simplified = [p for p, k in zip(open_ring, keep) if k]
    # re-close relative to the original start point
    out = list(simplified)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------

def polygonize_grid(
    values: Sequence[Sequence[float]],
    lat: Sequence[float],
    lon: Sequence[float],
    thresholds: Sequence[float],
    *,
    epsilon: float = 0.02,
) -> list[dict[str, Any]]:
    """All threshold bands -> GeoJSON polygon features.

    For each threshold ``t_i`` the cumulative hazard region ``{z >= t_i}`` is
    contoured with the pure-python :func:`marching_squares_segments` +
    :func:`assemble_rings` path and returned as one or more Polygon features
    (each with its holes). Bands are nested (higher thresholds sit inside
    lower ones), which is exactly how tiered hazards are drawn. Returns a list
    of features ``{"type": "Polygon", "coordinates": [outer, hole, ...],
    "properties": {"band": i, "min": t_i}}``.

    No scientific-stack dependency: the grid is padded with a one-cell frame
    valued below the threshold so that hazard regions touching the box edge
    still close into a polygon (they extend one grid step past the box, which
    is invisible on a map and correctly shows the hazard continuing). Missing
    data (None/NaN) counts as "below threshold" — a hazard is never drawn over
    a cell we have no value for. ``epsilon`` is the RDP tolerance in degrees
    (0 off).
    """
    rows = len(values)
    cols = len(values[0]) if rows else 0
    if rows < 2 or cols < 2:
        return []
    dlat = (float(lat[-1]) - float(lat[0])) / (rows - 1)
    dlon = (float(lon[-1]) - float(lon[0])) / (cols - 1)
    vmax = grid_stats(values)["max"]
    features: list[dict[str, Any]] = []
    for band, thr in enumerate(thresholds):
        thr = float(thr)
        if vmax is None or vmax < thr:
            continue
        # sentinel strictly below the threshold (and below all real data)
        sentinel = (vmax if vmax is not None else thr) - max(1.0, abs(thr))
        pvals, plat, plon = _pad_frame(values, lat, lon, dlat, dlon, sentinel)
        segs = marching_squares_segments(pvals, plat, plon, thr)
        rings = assemble_rings(segs)
        for outer, holes in _nest_rings(rings):
            if epsilon > 0:
                outer = douglas_peucker(outer, epsilon)
                holes = [douglas_peucker(h, epsilon) for h in holes]
            if len(outer) < 4:
                continue
            crings = [_close_ring(outer)] + [_close_ring(h) for h in holes if len(h) >= 4]
            features.append({
                "type": "Polygon",
                "coordinates": crings,
                "properties": {"band": band, "min": thr},
            })
    return features


def _pad_frame(
    values: Sequence[Sequence[float]],
    lat: Sequence[float],
    lon: Sequence[float],
    dlat: float,
    dlon: float,
    sentinel: float,
) -> tuple[list[list[float]], list[float], list[float]]:
    """Wrap the grid in a one-cell frame of ``sentinel`` and extend lat/lon.

    The frame forces contours that would otherwise run off the grid edge to
    turn around and close, so hazard regions touching the box boundary still
    produce a closed polygon (extending one step past the box).
    """
    rows = len(values)
    cols = len(values[0]) if rows else 0
    frame: list[list[float]] = []
    top_row = [sentinel] * (cols + 2)
    bot_row = [sentinel] * (cols + 2)
    frame.append(top_row)
    for row in values:
        frame.append([sentinel] + list(row) + [sentinel])
    frame.append(bot_row)
    plat = [float(lat[0]) - dlat] + [float(x) for x in lat] + [float(lat[-1]) + dlat]
    plon = [float(lon[0]) - dlon] + [float(x) for x in lon] + [float(lon[-1]) + dlon]
    return frame, plat, plon


# ---------------------------------------------------------------------------
# Ring nesting (holes) + GeoJSON emission
# ---------------------------------------------------------------------------

def _ring_area(ring: Sequence[tuple[float, float]]) -> float:
    """Shoelace signed area (deg^2) — magnitude only, sign ignored."""
    a = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _point_in_ring(p: tuple[float, float], ring: Sequence[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon (lon/lat degrees; box does not wrap)."""
    x, y = p
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def _nest_rings(
    rings: Sequence[Sequence[tuple[float, float]]],
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """Group closed contour rings into (outer, [holes]) pairs by containment.

    Sorted by descending area, each ring is a hole of the smallest already-placed
    ring that contains one of its vertices; rings with no containing parent are
    outers. Handles the common outer+hole (donut) case and stays correct for
    deeper nesting.
    """
    order = sorted(range(len(rings)), key=lambda i: _ring_area(rings[i]), reverse=True)
    placed: list[dict[str, Any]] = []  # {ring, holes, depth}
    for idx in order:
        ring = [pt for pt in rings[idx]]
        parent = None
        for pl in placed:
            if _point_in_ring(ring[0], pl["ring"]):
                if parent is None or _ring_area(pl["ring"]) < _ring_area(parent["ring"]):
                    parent = pl
        if parent is None:
            placed.append({"ring": ring, "holes": [], "depth": 0})
        else:
            parent["holes"].append(ring)
    return [(pl["ring"], pl["holes"]) for pl in placed]


def _close_ring(ring: Sequence[tuple[float, float]]) -> list[list[float]]:
    coords = [[float(round(lo, 5)), float(round(la, 5))] for (lo, la) in ring]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords
