"""Weather service: load a published cycle and serve map-ready products.

Sits between the atomic disk cache (store) and the thin API routes. Loads
the fast ``.npz`` bundle for a (cycle, box, forecast-hour), builds the hazard
GeoJSON layer for the selected flight level, and samples the route at every
navlog waypoint × FL × ETA. Missing fields are returned as explicit
``null``/``unavailable`` — never synthesized.

All functions are synchronous (numpy/xarray) and meant to run from
``asyncio.to_thread`` in the routes.
"""
from __future__ import annotations

import math
import time
from typing import Any, Optional

import numpy as np

from . import hazards, levels, polygonize, sampler
from .store import BoxKey, STORE, cache_root, staleness

# hazard product registry: key -> (grid prefix, thresholds, unit, label)
HazardProduct = dict[str, Any]
PRODUCTS: dict[str, HazardProduct] = {
    "turbulence": {
        "prefix": "ti", "levels_mb": (300, 200, 100),
        # WMO/TITAN-form TI1 (S*DEF in (1e-6 /s)^2); operational tiers
        "thresholds": [5.0, 15.0, 25.0],
        "unit": "TITAN TI1 (x1e-6/s^2)",
        "label": "Clear-air turbulence (Ellrod/TITAN TI1 — NOAA proxy)",
    },
    "icing": {
        "prefix": "ice", "levels_mb": (850, 700, 500),
        "thresholds": [0.10, 0.30, 0.60],  # CIP light/mod/severe proxy
        "unit": "index 0..1",
        "label": "Icing (CIP-style proxy from T/RH/W — not official WAFS)",
    },
    "cape": {
        "prefix": "cape", "levels_mb": (0,),
        "thresholds": [500.0, 2500.0, 5000.0],  # J/kg convection
        "unit": "J/kg",
        "label": "Surface-based CAPE (convection/possible severe wx)",
    },
    "fronts": {
        "prefix": "front", "levels_mb": (0,),
        "thresholds": [2.0, 3.5],  # K/250km potential/strong front
        "unit": "K / 250 km",
        "label": "Thermal front (Hewson θe-gradient, Bolton-1980 θe)",
    },
    "jet": {
        "prefix": "jet", "levels_mb": (200, 300),
        "thresholds": [30.0, 50.0],  # m/s extent/core
        "unit": "m/s (wind speed)",
        "label": "Jet-stream extent (upper-level wind)",
    },
}


def _box_key(cycle_id: str) -> Optional[BoxKey]:
    metas = [m for m in STORE.list_published() if m.cycle_id == cycle_id]
    if not metas:
        return None
    return metas[0].box


def _load_bundle(cycle_id: str, box: BoxKey, offset: int) -> Optional[dict[str, np.ndarray]]:
    path = cache_root() / cycle_id / f"f{offset:03d}.npz"
    if not path.is_file():
        return None
    try:
        with np.load(str(path)) as z:
            return {k: z[k] for k in z.files}
    except (OSError, ValueError):
        return None


def _nearest_offset(meta_offsets: list[int], wanted: int) -> Optional[int]:
    if not meta_offsets:
        return None
    return min(meta_offsets, key=lambda o: abs(o - wanted))


def _jet_grid(bund: dict[str, np.ndarray], levels_mb, fl: float) -> Optional[np.ndarray]:
    """Jet-stream speed grid: |wind| (m/s) at the level bracketing the FL.

    GFS bundles carry u/v per pressure level but no dedicated jet grid, so
    the jet layer is derived from the horizontal wind speed — consistent
    with the route-sample jet tier (hazards.jet_tier on |u,v|).
    """
    p = levels.fl_to_pressure_hpa(int(round(fl)))
    best_mb = min(levels_mb, key=lambda mb: abs(mb - p))
    u = bund.get(f"u_{best_mb}")
    v = bund.get(f"v_{best_mb}")
    if u is None or v is None:
        return None
    with np.errstate(invalid="ignore"):
        return np.hypot(u, v)


def _grid_for(bund: dict[str, np.ndarray], prefix: str, levels_mb, fl: float) -> Optional[np.ndarray]:
    """Pick the grid for a product+FL.

    For level-indexed products (turbulence/icing) the FL's ISA pressure picks
    the nearest ingested level. For surface products (cape/fronts) the grid is
    at the declared level. Jet is derived from the u/v wind speed grids
    (see _jet_grid). Returns a (lat,lon) float grid or None.
    """
    if prefix == "jet":
        return _jet_grid(bund, levels_mb, fl)
    if levels_mb == (0,):
        g = bund.get(prefix)
        return g if g is not None else None
    # choose the level whose pressure brackets the FL best
    p = levels.fl_to_pressure_hpa(int(round(fl)))
    best_mb = min(levels_mb, key=lambda mb: abs(mb - p))
    g = bund.get(f"{prefix}_{best_mb}")
    return g


def hazard_layer(
    cycle_id: str,
    product: str,
    fl: float,
    offset: int,
    *,
    epsilon: float = 0.02,
) -> dict[str, Any]:
    """One hazard layer as a GeoJSON FeatureCollection for FL + forecast hour."""
    spec = PRODUCTS.get(product)
    if spec is None:
        raise KeyError(f"unknown hazard product: {product}")
    meta = next((m for m in STORE.list_published() if m.cycle_id == cycle_id), None)
    if meta is None:
        raise LookupError(f"no published weather cycle {cycle_id!r}")
    off = _nearest_offset(meta.offsets, offset)
    if off is None:
        raise LookupError("cycle has no forecast hours")
    bund = _load_bundle(cycle_id, meta.box, off)
    if bund is None:
        raise LookupError(f"bundle for f{off:03d} not on disk")
    grid = _grid_for(bund, spec["prefix"], spec["levels_mb"], fl)
    if grid is None:
        return {
            "type": "FeatureCollection", "features": [],
            "product": product, "fl": int(round(fl)), "offset": off,
            "unavailable": f"{spec['label']} is unavailable for FL{int(round(fl)):03d} at T+{off}h",
        }
    lat = bund["lat"]
    lon = bund["lon"]
    feats = polygonize.polygonize_grid(
        grid, lat, lon, spec["thresholds"], epsilon=epsilon
    )
    return {
        "type": "FeatureCollection",
        "features": feats,
        "product": product,
        "label": spec["label"],
        "unit": spec["unit"],
        "fl": int(round(fl)),
        "offset": off,
        "thresholds": list(spec["thresholds"]),
        "cycle": cycle_id,
    }


def _wind_tier(speed_ms: float) -> Optional[int]:
    return hazards.jet_tier(speed_ms)


def route_samples(
    view: dict[str, Any],
    cycle_id: str,
    fl: float,
    offset: int,
) -> dict[str, Any]:
    """Sample the route at every navlog waypoint × FL × forecast hour.

    Returns per-waypoint: wind components (u,v m/s), speed (kt), direction
    (deg, FROM), tailwind (kt), OAT (C), turbulence tier + value, icing tier
    + value, jet tier. Unavailable fields are ``None`` (never fabricated).
    ``sibt_utc`` + waypoint ETA offset gives the absolute sample valid-time.
    """
    meta = next((m for m in STORE.list_published() if m.cycle_id == cycle_id), None)
    if meta is None:
        raise LookupError(f"no published weather cycle {cycle_id!r}")
    off = _nearest_offset(meta.offsets, offset)
    bund = _load_bundle(cycle_id, meta.box, off) if off is not None else None
    if bund is None:
        return {
            "cycle": cycle_id, "fl": int(round(fl)), "offset": None,
            "points": [], "unavailable": "no weather bundle for this cycle/FL",
        }
    lat, lon = bund["lat"], bund["lon"]

    # per-level field maps for FL interpolation
    def fmap(var: str) -> dict[float, list[list[float]]]:
        out: dict[float, list[list[float]]] = {}
        for mb in (50, 100, 150, 200, 250, 300, 400, 500, 700, 850, 925):
            g = bund.get(f"{var}_{mb}")
            if g is not None:
                out[float(mb)] = g.tolist()
        return out

    u_by_lvl = fmap("u")
    v_by_lvl = fmap("v")
    t_by_lvl = fmap("t")
    r_by_lvl = fmap("r")
    w_by_lvl = fmap("w")
    lvl_order = sorted(u_by_lvl.keys())

    rows = [w for w in (view.get("waypoints") or []) if isinstance(w, dict)]
    points: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        latw = row.get("lat"); lonw = row.get("lon")
        try:
            la, lo = float(latw), float(lonw)
        except (TypeError, ValueError):
            continue
        if math.isnan(la) or math.isnan(lo):
            continue
        u = sampler.sample_at_fl(u_by_lvl, lvl_order, lat, lon, la, lo, fl)
        v = sampler.sample_at_fl(v_by_lvl, lvl_order, lat, lon, la, lo, fl)
        t = sampler.sample_at_fl(t_by_lvl, lvl_order, lat, lon, la, lo, fl)
        r = sampler.sample_at_fl(r_by_lvl, lvl_order, lat, lon, la, lo, fl)
        w = sampler.sample_at_fl(w_by_lvl, lvl_order, lat, lon, la, lo, fl)

        speed_ms = None
        dir_from = None
        tailwind = None
        # outgoing-leg bearing (to the next fix); used for the tailwind component
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        nxt_lat = nxt_lon = None
        if nxt is not None:
            try:
                nxt_lat, nxt_lon = float(nxt.get("lat")), float(nxt.get("lon"))
            except (TypeError, ValueError):
                nxt_lat = nxt_lon = None
        if u is not None and v is not None:
            speed_ms = math.hypot(u, v)
            # meteorological wind direction (FROM), deg
            dir_from = (math.degrees(math.atan2(u, -v)) + 360.0) % 360.0
            if nxt_lat is not None and nxt_lon is not None:
                br = math.radians(sampler.initial_bearing_deg(la, lo, nxt_lat, nxt_lon))
                track = (math.sin(br), math.cos(br))  # (x=east, y=north)
                tailwind = (u * track[0] + v * track[1]) * 1.94384  # kt
        oat = None if t is None else round(t - 273.15, 1)

        # turbulence tier at the selected FL via the nearest TI grid
        ti_grid = _grid_for(bund, "ti", (300, 200, 100), fl)
        turb_val = None
        turb_tier = None
        if ti_grid is not None:
            ti = sampler.bilinear(ti_grid, lat, lon, la, lo)
            turb_val = ti
            turb_tier = hazards.turbulence_tier(ti) if ti is not None else None
        ice_grid = _grid_for(bund, "ice", (850, 700, 500), fl)
        ice_val = ice_tier = None
        if ice_grid is not None:
            ice_val = sampler.bilinear(ice_grid, lat, lon, la, lo)
            ice_tier = hazards.icing_tier(ice_val) if ice_val is not None else None
        jet_tier = _wind_tier(speed_ms) if speed_ms is not None else None

        points.append({
            "ident": str(row.get("ident") or "").upper(),
            "lat": la, "lon": lo,
            "fl": int(round(fl)),
            "u_ms": round(u, 2) if u is not None else None,
            "v_ms": round(v, 2) if v is not None else None,
            "wind_speed_kt": round(speed_ms * 1.94384, 1) if speed_ms is not None else None,
            "wind_from_deg": round(dir_from, 0) if dir_from is not None else None,
            "tailwind_kt": round(tailwind, 1) if tailwind is not None else None,
            "oat_c": oat,
            "turbulence": round(turb_val, 3) if turb_val is not None else None,
            "turbulence_tier": turb_tier,
            "icing": round(ice_val, 3) if ice_val is not None else None,
            "icing_tier": ice_tier,
            "jet_tier": jet_tier,
            "ete": row.get("ete"),
            "unavailable": (
                [] if (u is not None and t is not None)
                else ["wind" if u is None else "", "oat" if t is None else ""]
            ),
        })

    return {
        "cycle": cycle_id,
        "fl": int(round(fl)),
        "offset": off,
        "sibt_utc": view.get("std_utc"),
        "points": points,
        "provenance": "NOAA GFS 0.25 deg (g2sub) — NOAA-derived proxies, not official WAFS/eWAS",
    }


def cycle_status() -> dict[str, Any]:
    """The readiness/status object (mirrors scheduler.status_payload + store)."""
    from . import scheduler
    return scheduler.status_payload()
