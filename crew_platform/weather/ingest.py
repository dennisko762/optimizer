"""GFS cycle ingest: fetch → parse → hazard grids → atomic publish.

One call per (cycle, region box) ingests every requested forecast hour as a
bounded g2sub subset, extracts the standard fields, and derives the hazard
grids (turbulence TI, icing, CAPE, frontal θe-gradient) that the Route map
consumes. Publishing is atomic: the cycle's ``meta.json`` is swapped in only
once every forecast hour is on disk (see :mod:`crew_platform.weather.store`).

The parsed grids are also persisted as a compressed ``.npz`` bundle next to
the raw GRIB so later loads skip the cfgrib parse (sub-100 ms vs ~0.5 s).

All network I/O uses :mod:`httpx` with a size bound, a timeout, and
exponential backoff on transient failures (the public endpoint returns
intermittent HTTP 500 under rapid request sequences).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np

from . import cycle as gfs
from . import hazards, levels
from .cycle import GfsConfig, GfsCycleNotFound, GfsDownloadError, build_region_box
from .store import BoxKey, CycleMeta, CycleStore, cache_root

LOG = logging.getLogger("crew_weather.ingest")

#: pressure levels (hPa) where the turbulence/icing hazard fields are computed,
#: and the level *above* each (for vertical shear) from the ingested set.
_HAZARD_LEVELS: tuple[tuple[int, int], ...] = (
    (850, 700),
    (700, 500),
    (500, 400),
    (400, 300),
    (300, 250),
    (250, 200),
    (200, 150),
    (150, 100),
    (100, 50),
)

#: the TI / ICE levels surfaced on the map (per the task: TI at 300/200/100,
#: icing at 850/700/500, frontal θe-gradient across 500–850).
TI_LEVELS_MB = (300, 200, 100)
ICE_LEVELS_MB = (850, 700, 500)
FRONT_LO_MB, FRONT_HI_MB = 850, 500

#: retry policy for transient (500/timeout) failures on the public endpoint.
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 4.0


@dataclass
class IngestRequest:
    cycle_id: str          # YYYYMMDD_HH
    leftlon: float
    rightlon: float
    toplat: float
    bottomlat: float
    offsets: Sequence[int]


def request_from_route(
    lats: Sequence[float], lons: Sequence[float], cycle_id: str,
    pad_deg: float = 3.0,
) -> IngestRequest:
    """Build an :class:`IngestRequest` from a route's lat/lon cloud.

    The caller must split antimeridian-crossing routes into sub-routes before
    calling (the g2sub box cannot wrap).
    """
    ll, rl, tp, bl = build_region_box(lats, lons, pad_deg)
    return IngestRequest(cycle_id, ll, rl, tp, bl, offsets=tuple(range(0, 37)))


def _box_of(req: IngestRequest) -> BoxKey:
    return BoxKey(req.leftlon, req.rightlon, req.toplat, req.bottomlat)


def _npz_path(cycle_id: str, box: BoxKey, offset: int):
    return cache_root() / cycle_id / f"f{offset:03d}.npz"


def _backoff(attempt: int) -> float:
    return _BACKOFF_BASE_S * (2 ** attempt) + float(os.environ.get("GFS_RETRY_JITTER", "0"))


def _fetch_with_retry(
    config: GfsConfig, date: str, hour: str, offset: int, req: IngestRequest
) -> bytes:
    last: Exception = GfsDownloadError("no attempt made")
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return gfs.download_subset(
                config, date, hour, offset,
                leftlon=req.leftlon, rightlon=req.rightlon,
                toplat=req.toplat, bottomlat=req.bottomlat,
            )
        except GfsCycleNotFound:
            raise  # a missing cycle/file will not fix itself mid-run
        except GfsDownloadError as exc:
            last = exc
            if attempt < _MAX_ATTEMPTS - 1:
                wait = _backoff(attempt)
                LOG.warning("GFS f%03d transient failure (%s); retry %d in %.0fs",
                            offset, exc, attempt + 1, wait)
                time.sleep(wait)
    raise GfsDownloadError(f"GFS f{offset:03d} failed after {_MAX_ATTEMPTS} attempts") from last


def _grid(ds: Any, var: str, mb: int) -> Optional[np.ndarray]:
    """Extract a 2-D (lat, lon) grid for one var at one pressure level."""
    try:
        arr = ds[var].sel(isobaricInhPa=mb, method="nearest")
        g = np.asarray(arr.values, dtype=np.float32)
        if g.shape != (2,):
            g = np.atleast_2d(g)
        return g
    except Exception:
        return None


def _lon_wrap(lon: np.ndarray) -> np.ndarray:
    """Normalize longitudes to (-180, 180], keeping grid order."""
    out = (np.asarray(lon, dtype=np.float32) + 180.0) % 360.0 - 180.0
    return out


def _extract(ds: Any) -> tuple[np.ndarray, np.ndarray, dict[tuple[str, int], np.ndarray]]:
    """(lat, lon, {(var, mb): grid}) from a cfgrib Dataset for one offset."""
    lat = np.asarray(ds["latitude"].values, dtype=np.float32)
    lon = _lon_wrap(ds["longitude"].values)
    fields: dict[tuple[str, int], np.ndarray] = {}
    for mb in (50, 100, 150, 200, 250, 300, 400, 500, 700, 850, 925):
        for var in ("u", "v", "t", "r", "w", "gh"):
            g = _grid(ds, var, mb)
            if g is not None and g.size:
                fields[(var, mb)] = g
    # surface fields
    for var, key in (("cape", "cape"), ("slp", "slp")):
        try:
            arr = ds[var]
            g = np.atleast_2d(np.asarray(arr.values, dtype=np.float32))
            fields[(var, 0)] = g
        except Exception:
            continue
    return lat, lon, fields


def _cell_meters(lat: np.ndarray) -> tuple[float, float]:
    """(dx, dz) in meters for the (lon, lat) grid spacing at the mid-lat."""
    if lat.size < 2:
        return (27777.0, 27777.0)
    mid = float(lat[len(lat) // 2])
    dlat = float(abs(lat[1] - lat[0]))
    dz = dlat * 110574.0
    dx = float(abs((np.cos(np.radians(mid))))) * dlat * 111319.0
    return (max(dx, 1000.0), max(dz, 1000.0))


def _hazard_grids(
    lat: np.ndarray,
    fields: dict[tuple[str, int], np.ndarray],
) -> dict[str, np.ndarray]:
    """Derive hazard grids for one offset from its extracted fields.

    Returns named grids the map endpoint polygonizes/samples:
      ti_{300,200,100}  — TITAN TI value at the level (Ellrod & Knapp 1992)
      ice_{850,700,500} — icing index 0..1
      cape              — surface-based CAPE (J/kg)
      front             — frontal θe-gradient (K / 250 km) across 850→500
    """
    out: dict[str, np.ndarray] = {}
    dx_east, dx_north = _cell_meters(lat)

    # turbulence per TI level: TI1 (deformation + vertical wind shear).
    # The vertical shear MUST use the pressure-level spacing (metres between
    # the two isobaric surfaces), NOT a horizontal cell size.
    for mb in TI_LEVELS_MB:
        u, v, u_hi, v_hi = (
            fields.get(("u", mb)), fields.get(("v", mb)),
            fields.get(("u", mb - 50)), fields.get(("v", mb - 50)),
        )
        upper_mb = mb - 50
        if any(g is None for g in (u, v, u_hi, v_hi)):
            # fall back to the nearest available upper level
            for d in (100, 150):
                uh, vh = fields.get(("u", mb - d)), fields.get(("v", mb - d))
                if uh is not None and vh is not None:
                    u_hi, v_hi = uh, vh
                    upper_mb = mb - d
                    break
        if any(g is None for g in (u, v, u_hi, v_hi)):
            continue
        dz_level = hazards.LEVEL_PAIR_SPACING_M.get((float(mb), float(upper_mb)), 2000.0)
        ti1, _ti2 = hazards.ti_from_grid(
            u, v, u_hi, v_hi, dx_east, dz_level, dx_lat_m=dx_north,
        )
        # Store the WMO/TITAN-form index: S*DEF in (1e-6 /s)^2 units. The raw
        # S*DEF is ~1e-6..1e-5 /s^2, i.e. 1..15 on the 1e6 scale, which is what
        # the operational WMO/TITAN thresholds (5/15/25, see
        # hazards.TI_TIER_THRESHOLDS) expect.
        out[f"ti_{mb}"] = (np.asarray(ti1, dtype=np.float32) * 1.0e6)

    # icing at 850/700/500 from temp + rh + vertical velocity (vectorized)
    for mb in ICE_LEVELS_MB:
        t = fields.get(("t", mb)); r = fields.get(("r", mb)); w = fields.get(("w", mb))
        if any(g is None for g in (t, r, w)):
            continue
        out[f"ice_{mb}"] = hazards.icing_grid(t - 273.15, r, w).astype(np.float32)

    cape = fields.get(("cape", 0))
    if cape is not None:
        out["cape"] = cape

    # frontal strength: horizontal gradient of (theta_e_850 - theta_e_500),
    # scaled to K per 250 km (Hewson & Järvi 1995).
    t_lo = fields.get(("t", 850)); r_lo = fields.get(("r", 850))
    t_hi = fields.get(("t", 500)); r_hi = fields.get(("r", 500))
    if all(g is not None for g in (t_lo, r_lo, t_hi, r_hi)):
        te_lo = hazards.theta_e_grid(t_lo - 273.15, r_lo, 850.0)
        te_hi = hazards.theta_e_grid(t_hi - 273.15, r_hi, 500.0)
        dte = te_lo - te_hi  # (lat, lon)
        with np.errstate(invalid="ignore", divide="ignore"):
            g_east = np.gradient(dte, axis=1) / dx_east     # K/m
            g_north = np.gradient(dte, axis=0) / dx_north   # K/m
        grad_km = np.hypot(g_east, g_north) * 250_000.0     # K / 250 km
        out["front"] = np.nan_to_num(grad_km, nan=0.0, posinf=0.0).astype(np.float32)
    return out


def _save_bundle(
    cycle_id: str, box: BoxKey, offset: int,
    lat: np.ndarray, lon: np.ndarray,
    fields: dict[tuple[str, int], np.ndarray],
    hz: dict[str, np.ndarray],
) -> None:
    payload: dict[str, np.ndarray] = {"lat": lat, "lon": lon}
    for (var, mb), g in fields.items():
        payload[f"{var}_{mb}"] = g
    for name, g in hz.items():
        payload[name] = g
    np.savez_compressed(_npz_path(cycle_id, box, offset), **payload)


def ingest_cycle(
    config: GfsConfig,
    req: IngestRequest,
    store: Optional[CycleStore] = None,
) -> CycleMeta:
    """Fetch + derive + atomically publish one (cycle, box) dataset.

    Returns the published :class:`CycleMeta`. Raises
    :class:`gfs.GfsCycleNotFound` if the cycle is absent and
    :class:`gfs.GfsDownloadError` if a subset cannot be fetched.
    """
    from . import store as _store
    store = store or _store.STORE
    date, hour = req.cycle_id.split("_")
    box = _box_of(req)

    # 1) download every forecast-hour subset (with retry), stage on disk
    for off in req.offsets:
        data = _fetch_with_retry(config, date, hour, off, req)
        store.write_raw(req.cycle_id, off, data)

    # 2) parse + derive + persist a fast bundle per hour
    import xarray as xr

    field_stems: set[str] = set()
    lat = lon = None
    for off in req.offsets:
        raw = cache_root() / req.cycle_id / f"f{off:03d}.grb2"
        # the subset mixes isobaric and surface (CAPE) levels; cfgrib refuses
        # to build one dataset from a mixed typeOfLevel, so open each family
        # separately and merge the extracted fields.
        merged: dict[tuple[str, int], np.ndarray] = {}
        for level_filter in ("isobaricInhPa", "surface"):
            ds = xr.open_dataset(
                str(raw), engine="cfgrib",
                filter_by_keys={"typeOfLevel": level_filter},
            )
            try:
                la, lo, fields = _extract(ds)
            finally:
                ds.close()
            if lat is None:
                lat, lon = la, lo
            merged.update(fields)
        hz = _hazard_grids(lat, merged)
        _save_bundle(req.cycle_id, box, off, lat, lon, merged, hz)
        field_stems |= {k[0] for k in merged} | {n.split("_")[0] for n in hz}

    meta = CycleMeta(
        cycle_id=req.cycle_id, run_date=date, run_hour=hour, box=box,
        offsets=sorted(int(o) for o in req.offsets),
        fields=sorted(field_stems),
        n_lat=int(lat.size), n_lon=int(lon.size), fetched_at=time.time(),
    )
    store.publish(meta)
    LOG.info("published weather cycle %s box %s offsets %s", req.cycle_id, box, meta.offsets)
    return meta
