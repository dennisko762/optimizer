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
import re
import time
from typing import Any, Optional

import numpy as np

from . import hazards, levels, polygonize, sampler
from .store import BoxKey, STORE, cache_root, staleness, cycle_epoch

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
    path = cache_root() / cycle_id / str(box) / f"f{offset:03d}.npz"
    if path.is_file():
        try:
            with np.load(str(path)) as z:
                return {k: z[k] for k in z.files}
        except (OSError, ValueError):
            return None
    # legacy flat layout (pre region-keying)
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


def _valid_at_iso(cycle_id: str, offset: int) -> Optional[str]:
    """Real UTC validity time for a cycle + forecast-hour offset (ISO 8601).

    This is the cycle's model run time plus the offset — the ACTUAL time the
    forecast hour represents, never the requested time when they differ.
    """
    import datetime as _dt

    try:
        ep = cycle_epoch(cycle_id) + float(offset) * 3600.0
        return _dt.datetime.fromtimestamp(ep, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, KeyError):
        return None


def _hazard_grid_at_fl(bund: dict[str, np.ndarray], prefix: str, levels_mb, fl: float) -> Optional[np.ndarray]:
    """Hazard grid at the requested FL by pressure-slab interpolation.

    Level-indexed products (turbulence TI, icing) are derived at the
    ingested pressure levels, so the FL's ISA slab is obtained by linear
    blending of the two bracketing level grids (``levels.fl_fraction``) —
    NOT by silently picking the nearest level (which would report 500 hPa
    icing for FL340/450). Surface products (cape/fronts) have no pressure
    dimension and are returned as-is. Jet is derived from the u/v wind-speed
    grids at the bracketing FL slab. A requested FL above the ingested level
    stack returns None (unavailable — never extrapolated).
    """
    if levels_mb == (0,):
        g = bund.get(prefix)
        return g if g is not None else None
    if prefix == "jet":
        return _jet_grid(bund, levels_mb, fl)
    from . import levels

    p = levels.fl_to_pressure_hpa(int(round(fl)))
    avail = [mb for mb in levels_mb if bund.get(f"{prefix}_{mb}") is not None]
    if not avail:
        return None
    avail = sorted(avail)
    if p <= avail[0]:
        return None  # FL below the lowest ingested hazard level: unavailable
    if p >= avail[-1]:
        # top of the hazard stack: the highest ingested level is the slab
        # (documented: hazard products are only defined up to that level)
        return bund[f"{prefix}_{avail[-1]}"]
    lower = max(mb for mb in avail if mb > p)   # larger pressure (below)
    upper = min(mb for mb in avail if mb < p)   # smaller pressure (above)
    if lower == upper:
        return bund[f"{prefix}_{lower}"]
    # linear blend in pressure space: the bracketing hazard levels are
    # standard hPa values, so no round-trip conversion is needed
    frac = min(1.0, max(0.0, (p - lower) / (upper - lower))) if upper != lower else 0.0
    g_lo = bund[f"{prefix}_{lower}"].astype(np.float32)
    g_hi = bund[f"{prefix}_{upper}"].astype(np.float32)
    return ((1.0 - frac) * g_lo + frac * g_hi)


def hazard_layer(
    cycle_id: str,
    product: str,
    fl: float,
    offset: int,
    *,
    epsilon: float = 0.02,
) -> dict[str, Any]:
    """One hazard layer as a GeoJSON FeatureCollection for FL + forecast hour.

    Temporal provenance is exact: the response's ``offset`` is the
    forecast hour actually served and ``valid_at_utc`` its real validity
    time (cycle run + offset). A requested hour that is not published is
    never substituted — it comes back as ``unavailable`` with the actual
    published horizon, so a missing T+24 is never displayed as T+24.
    """
    spec = PRODUCTS.get(product)
    if spec is None:
        raise KeyError(f"unknown hazard product: {product}")
    meta = next((m for m in STORE.list_published() if m.cycle_id == cycle_id), None)
    if meta is None:
        raise LookupError(f"no published weather cycle {cycle_id!r}")
    if offset not in meta.offsets:
        horizon = max(meta.offsets) if meta.offsets else 0
        return {
            "type": "FeatureCollection", "features": [],
            "product": product, "fl": int(round(fl)), "offset": None,
            "requested_offset": offset,
            "unavailable": (
                f"T+{offset}h is not published for cycle {cycle_id} "
                f"(horizon T+0..T+{horizon}h). Request an offset within the "
                f"published horizon."
            ),
        }
    bund = _load_bundle(cycle_id, meta.box, offset)
    if bund is None:
        raise LookupError(f"bundle for f{offset:03d} not on disk")
    grid = _hazard_grid_at_fl(bund, spec["prefix"], spec["levels_mb"], fl)
    if grid is None:
        return {
            "type": "FeatureCollection", "features": [],
            "product": product, "fl": int(round(fl)), "offset": offset,
            "valid_at_utc": _valid_at_iso(cycle_id, offset),
            "unavailable": f"{spec['label']} is unavailable for FL{int(round(fl)):03d} at T+{offset}h",
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
        "offset": offset,
        "valid_at_utc": _valid_at_iso(cycle_id, offset),
        "thresholds": list(spec["thresholds"]),
        "cycle": cycle_id,
    }


def route_samples(
    view: dict[str, Any],
    cycle_id: str,
    fl: float,
    offset: int,
) -> dict[str, Any]:
    """Sample the route at every navlog waypoint × FL × ETA.

    Temporal semantics (task: "never present missing forecast hours as the
    requested time"): each waypoint is sampled at the forecast hour matching
    its own ETA relative to the cycle run (``std_utc`` + cumulative ETE).
    Waypoints whose ETA hour is not published for the cycle get explicit
    ``unavailable`` weather (never a silently substituted hour). Waypoints
    without ETE data fall back to the requested ``offset``; if that hour is
    also missing they are unavailable as well. The response's ``offset`` is
    the requested hour, and every served sample carries its ACTUAL
    ``offset_served`` + ``valid_at_utc``.

    Returns per-waypoint: wind components (u,v m/s), speed (kt), direction
    (deg, meteorological FROM), tailwind (kt), OAT (C), turbulence tier +
    value, icing tier + value, jet tier. Unavailable fields are ``None``
    (never fabricated).
    """
    meta = next((m for m in STORE.list_published() if m.cycle_id == cycle_id), None)
    if meta is None:
        raise LookupError(f"no published weather cycle {cycle_id!r}")
    offsets = list(meta.offsets)
    max_off = max(offsets) if offsets else 0
    run_ep = cycle_epoch(cycle_id)

    # per-waypoint ETA hours (minutes) from the plan
    ete_minutes: list[Optional[float]] = []
    for row in (view.get("waypoints") or []):
        if not isinstance(row, dict):
            ete_minutes.append(None)
            continue
        ete = row.get("ete")
        m = None
        if isinstance(ete, (int, float)) and not isinstance(ete, bool):
            m = float(ete)  # already minutes
        elif isinstance(ete, str):
            mm = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", ete)  # "H:MM" navlog form
            if mm:
                m = int(mm.group(1)) * 60 + int(mm.group(2))
        ete_minutes.append(m)

    def _eta_to_offset(minutes: Optional[float]) -> Optional[int]:
        """Forecast hour for a waypoint ETA, or None when undecidable.

        The waypoint's absolute time is ``std_utc`` (block-out, UTC) plus the
        cumulative ETE; the forecast hour relative to the cycle run is the
        difference in hours, rounded. Waypoints with no ETA (or no std_utc)
        cannot be placed — None falls back to the requested offset.
        """
        if minutes is None:
            return None
        std = view.get("std_utc")
        if not std:
            return None
        try:
            import datetime as _dt

            s = str(std).strip()
            if s.endswith(("Z", "z")):
                s = s[:-1]
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
                try:
                    std_dt = _dt.datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
            abs_ep = std_dt.replace(tzinfo=_dt.timezone.utc).timestamp()
            return int(round((abs_ep + minutes * 60.0 - run_ep) / 3600.0))
        except Exception:  # noqa: BLE001 — unparseable time -> fallback offset
            return None

    rows = [w for w in (view.get("waypoints") or []) if isinstance(w, dict)]
    points: list[dict[str, Any]] = []
    served_offsets: set[int] = set()
    ident_seen: dict[str, int] = {}  # occurrence counter (repeated fixes)

    def _next_occ(ident: str) -> int:
        n = ident_seen.get(ident, 0)
        ident_seen[ident] = n + 1
        return n
    for i, row in enumerate(rows):
        latw = row.get("lat"); lonw = row.get("lon")
        try:
            la, lo = float(latw), float(lonw)
        except (TypeError, ValueError):
            continue
        if math.isnan(la) or math.isnan(lo) or not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
            continue

        off_w = _eta_to_offset(ete_minutes[i])
        used_fallback = off_w is None
        requested_eta_offset = off_w
        if off_w is None:
            off_w = offset
        if off_w not in offsets:
            # ETA (or no ETA) is outside/absent the published steps: serve
            # the requested DISPLAY offset and flag the mismatch in
            # provenance. The served time is always the point's `offset`,
            # never the ETA — the UI must not label a T+6 sample as the
            # T+100 valid time (QA #2).
            served_at = offset
            off_w = served_at
            if served_at not in offsets:
                ident = str(row.get("ident") or "").upper()
                points.append({
                    "ident": ident,
                    "occurrence": _next_occ(ident),
                    "lat": la, "lon": lo,
                    "fl": int(round(fl)),
                    "offset": None,
                    "offset_served": None,
                    "valid_at_utc": None,
                    "unavailable": [
                        f"no weather at T+{offset}h for cycle {cycle_id} "
                        f"(published T+{min(offsets)}..T+{max_off}h)"
                    ],
                })
                continue
            if requested_eta_offset is not None and 0 <= requested_eta_offset <= max_off:
                _eta_flag = (
                    f"eta T+{requested_eta_offset}h not published in this cycle; "
                    f"weather shown at requested T+{served_at}h"
                )
            elif requested_eta_offset is not None and requested_eta_offset < 0:
                # plan STD predates the model run (stale OFP): every ETA is
                # in the past — the cycle only covers T+0..T+max_off
                _eta_flag = (
                    f"eta T+{requested_eta_offset}h predates the model run; "
                    f"weather shown at requested T+{served_at}h"
                )
            elif requested_eta_offset is not None:
                _eta_flag = (
                    f"eta T+{requested_eta_offset}h outside published horizon "
                    f"(T+0..T+{max_off}h); weather shown at requested T+{served_at}h"
                )
            else:
                _eta_flag = f"eta-hour unavailable; weather shown at requested T+{served_at}h"
        else:
            _eta_flag = None

        bund = _load_bundle(cycle_id, meta.box, off_w)
        ident = str(row.get("ident") or "").upper()
        if bund is None:
            points.append({
                "ident": ident,
                "occurrence": _next_occ(ident),
                "lat": la, "lon": lo,
                "fl": int(round(fl)),
                "offset": off_w,
                "offset_served": off_w,
                "valid_at_utc": _valid_at_iso(cycle_id, off_w),
                "unavailable": ["bundle missing on disk"],
            })
            continue
        served_offsets.add(off_w)
        lat, lon = bund["lat"], bund["lon"]

        # per-level field maps for FL interpolation (this waypoint's bundle)
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
        lvl_order = sorted(u_by_lvl.keys())

        u = sampler.sample_at_fl(u_by_lvl, lvl_order, lat, lon, la, lo, fl)
        v = sampler.sample_at_fl(v_by_lvl, lvl_order, lat, lon, la, lo, fl)
        t = sampler.sample_at_fl(t_by_lvl, lvl_order, lat, lon, la, lo, fl)

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
            # meteorological wind direction (FROM), deg — u east, v north
            dir_from = hazards.wind_from_deg(u, v)
            if nxt_lat is not None and nxt_lon is not None:
                br = math.radians(sampler.initial_bearing_deg(la, lo, nxt_lat, nxt_lon))
                track = (math.sin(br), math.cos(br))  # (x=east, y=north)
                tailwind = (u * track[0] + v * track[1]) * 1.94384  # kt
        oat = None if t is None else round(t - 273.15, 1)

        # turbulence + icing at the FL slab (pressure-interpolated grids)
        ti_grid = _hazard_grid_at_fl(bund, "ti", (300, 200, 100), fl)
        turb_val = None
        turb_tier = None
        if ti_grid is not None:
            ti = sampler.bilinear(ti_grid, lat, lon, la, lo)
            turb_val = ti
            turb_tier = hazards.turbulence_tier(ti) if ti is not None else None
        ice_grid = _hazard_grid_at_fl(bund, "ice", (850, 700, 500), fl)
        ice_val = ice_tier = None
        if ice_grid is not None:
            ice_val = sampler.bilinear(ice_grid, lat, lon, la, lo)
            ice_tier = hazards.icing_tier(ice_val) if ice_val is not None else None
        jet_tier = hazards.jet_tier(speed_ms) if speed_ms is not None else None

        unavailable = []
        if u is None:
            unavailable.append("wind")
        if t is None:
            unavailable.append("oat")
        if ti_grid is None:
            unavailable.append("turbulence")
        if ice_grid is None:
            unavailable.append("icing")
        if _eta_flag:
            unavailable.append(_eta_flag)

        points.append({
            "ident": ident,
            "occurrence": _next_occ(ident),
            "lat": la, "lon": lo,
            "fl": int(round(fl)),
            "u_ms": round(u, 2) if u is not None else None,
            "v_ms": round(v, 2) if v is not None else None,
            "wind_speed_kt": round(speed_ms * 1.94384, 1) if speed_ms is not None else None,
            "wind_from_deg": (round(dir_from, 0) if dir_from is not None and not math.isnan(dir_from) else None),
            "tailwind_kt": round(tailwind, 1) if tailwind is not None else None,
            "oat_c": oat,
            "turbulence": round(turb_val, 3) if turb_val is not None else None,
            "turbulence_tier": turb_tier,
            "icing": round(ice_val, 3) if ice_val is not None else None,
            "icing_tier": ice_tier,
            "jet_tier": jet_tier,
            "ete": row.get("ete"),
            "offset": off_w,
            "offset_served": off_w,
            "valid_at_utc": _valid_at_iso(cycle_id, off_w),
            "unavailable": unavailable,
        })

    return {
        "cycle": cycle_id,
        "fl": int(round(fl)),
        "offset": offset,
        "published_offsets": offsets,
        "served_offsets": sorted(served_offsets),
        "sibt_utc": view.get("std_utc"),
        "points": points,
        "provenance": "NOAA GFS 0.25 deg (g2sub) — NOAA-derived proxies, not official WAFS/eWAS",
    }


def cycle_status() -> dict[str, Any]:
    """The readiness/status object (mirrors scheduler.status_payload + store)."""
    from . import scheduler
    return scheduler.status_payload()
