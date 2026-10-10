"""Route data for the interactive map — sourced from the saved SimBrief OFP.

The *exact* route (origin, every navlog fix in order, destination) comes
straight from the active saved flightplan view (``optimizer.api
.flightplan_service``). This module turns it into map-ready data:

* validates every coordinate (lat ∈ [-90,90], lon ∈ [-180,180]) and collects
  **unresolved fixes** (missing/NaN coordinates) as explicit data errors — a
  fix is never silently dropped or guessed;
* computes great-circle geometry (per-leg distance, cumulative, bearing) via
  :func:`crew_platform.weather.sampler.route_geometry`;
* splits the route at antimeridian crossings so the client can draw each
  sub-polyline without a spurious 180-deg chord;
* emits the route as a GeoJSON (Multi)LineString preserving full order.

Pure and testable: the plan view is injected, so the antimeridian, order, and
unresolved-fix cases are unit-tested without any SimBrief fetch.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .sampler import route_geometry, split_around_antimeridian


class RouteDataError(ValueError):
    """The plan has no usable route (no coordinates at all)."""


def _parse_coord(lat: Any, lon: Any) -> Optional[tuple[float, float]]:
    """Validate + parse a (lat, lon) pair; ``None`` if missing/invalid/NaN."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if math.isnan(la) or math.isnan(lo):
        return None
    if -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0:
        return (la, lo)
    return None


def _occ(counters: dict[str, int], ident: str) -> int:
    """Occurrence index of a (possibly repeated) navlog fix, in order.

    Duplicates keep their position so the UI can join weather samples to the
    EXACT fix occurrence, not just its ident.
    """
    n = counters.get(ident, 0)
    counters[ident] = n + 1
    return n


def extract_route(view: dict[str, Any]) -> dict[str, Any]:
    """Build map-ready route data from a SimBrief flightplan view.

    Returns a dict with:
      points    — ordered list (origin, fixes, destination) each with
                  ident, lat, lon, alt (FL), ete_min, stage, airway, fir,
                  wind, plus geometry (dist_nm, cum_nm, bearing_deg)
      unresolved — fixes with missing/invalid coordinates (ident + reason)
      segments  — antimeridian-split sub-polyline index groups
      geojson   — route as a GeoJSON FeatureCollection (LineStrings)
      origin, destination, cruise_fl, total_nm, point_count
    Raises :class:`RouteDataError` when no point has a valid coordinate.
    """
    rows = [w for w in (view.get("waypoints") or []) if isinstance(w, dict)]
    origin = str(view.get("origin") or "").upper() or None
    dest = str(view.get("destination") or "").upper() or None

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    occ_counters: dict[str, int] = {}
    for i, row in enumerate(rows):
        ident = str(row.get("ident") or "").upper() or f"IDX{i}"
        lat, lon = row.get("lat"), row.get("lon")
        coord = _parse_coord(lat, lon)
        if coord is None:
            reason = "missing coordinates" if (
                lat is None or lon is None
            ) else "invalid coordinates"
            unresolved.append({
                "index": i,
                "ident": ident,
                "stage": row.get("stage"),
                "reason": reason,
            })
            continue
        la, lo = coord
        resolved.append({
            "index": i,
            "ident": ident,
            "occurrence": _occ(occ_counters, ident),
            "name": row.get("name"),
            "lat": la,
            "lon": lo,
            "alt": row.get("alt"),
            "ete": row.get("ete"),
            "stage": str(row.get("stage") or "").upper() or None,
            "airway": row.get("airway"),
            "fir": row.get("fir"),
            "wind": row.get("wind"),
            "is_origin": ident == origin,
            "is_dest": ident == dest,
        })

    if not resolved:
        raise RouteDataError(
            f"Route has no resolvable waypoints (origin={origin} dest={dest}); "
            f"{len(unresolved)} fix(es) lack coordinates."
        )

    geo = route_geometry(resolved)

    # index groups for antimeridian sub-polylines
    seg_points = split_around_antimeridian(geo)
    segments: list[list[int]] = []
    for seg in seg_points:
        segments.append([p["index"] for p in seg])

    total_nm = geo[-1]["cum_nm"] if geo else 0.0

    geojson: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "ident": p["ident"],
                    "is_origin": p["is_origin"],
                    "is_dest": p["is_dest"],
                    "stage": p["stage"],
                    "alt": p["alt"],
                    "fir": p["fir"],
                    "airway": p["airway"],
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(p["lon"], 5), round(p["lat"], 5)],
                },
            }
            for p in geo
        ],
    }
    # the drawn route line(s), split at the antimeridian
    for si, seg in enumerate(seg_points):
        geojson["features"].append({
            "type": "Feature",
            "properties": {"segment": si, "kind": "route-line"},
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(p["lon"], 5), round(p["lat"], 5)] for p in seg
                ],
            },
        })

    return {
        "origin": origin,
        "destination": dest,
        "cruise_fl": view.get("cruise_fl"),
        "route": view.get("route"),
        "callsign": view.get("callsign"),
        "aircraft": view.get("aircraft"),
        "route_distance_nm": view.get("route_distance_nm") or total_nm,
        "total_nm": round(total_nm, 1),
        "points": geo,
        "unresolved": unresolved,
        "segments": segments,
        "point_count": len(geo),
        "geojson": geojson,
    }
