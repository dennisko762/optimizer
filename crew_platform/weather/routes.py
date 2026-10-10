"""Thin FastAPI routes for the SkyNexus-style live eWAS weather map.

Mounted under ``/api/crew/weather``. These routes are deliberately thin — all
logic lives in :mod:`crew_platform.weather` (cycle/store/ingest/scheduler/
service/routes_data). They:

* report readiness + explicit degraded states (not configured / downloading /
  stale / no-cycle) via ``GET /status``;
* serve the exact route + per-waypoint FL/ETA samples from the active saved
  SimBrief OFP via ``GET /route``;
* serve one hazard layer as GeoJSON via ``GET /layer/{product}``;
* allow a manual ingest (test/admin) via ``POST /ingest``;
* push a Server-Sent-Events refresh feed via ``GET /events`` so open maps
  update automatically when a new completed cycle lands.

Data provenance: every response carries the ``source`` + a ``proxy`` note.
The products are NOAA-derived *proxies*, never official WAFS/eWAS.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from . import service
from .cycle import GfsConfig, GfsCycleNotFound, GfsDownloadError
from .ingest import IngestRequest, ingest_cycle
from .routes_data import extract_route
from .store import STORE
from . import scheduler

router = APIRouter(prefix="/api/crew/weather", tags=["crew-platform"])

#: exact mirror of crew_platform.weather.store._cycle_dir's format check —
#: validated here so a malformed value is rejected with a clear 4xx instead
#: of surfacing as an uncaught ValueError -> 500 from the store layer.
_CYCLE_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{2}$")


def _active_plan() -> dict[str, Any]:
    """The active saved SimBrief flightplan view (or raise 404/503)."""
    from optimizer.api import flightplan_service as fps

    listing = fps.list_plans()
    key = listing.get("last_plan")
    if not key:
        # fall back to the live OFP when a SimBrief user is configured
        if fps.has_credentials():
            view = fps.fetch_flightplan_view()
            return view
        raise HTTPException(
            status_code=404,
            detail="No active SimBrief flightplan. Import a plan first "
                   "(Flightplan tile → Import New Plan) or configure SIMBRIEF_USER.",
        )
    plan = fps.get_plan(key)
    if not plan or "flightplan" not in plan:
        raise HTTPException(status_code=404, detail="Active flightplan is missing.")
    return plan["flightplan"]


def _route_box(view: dict[str, Any]) -> tuple[float, float, float, float]:
    """(leftlon, rightlon, toplat, bottomlat) around the plan's route."""
    lats, lons = [], []
    for w in view.get("waypoints") or []:
        try:
            la, lo = float(w.get("lat")), float(w.get("lon"))
        except (TypeError, ValueError):
            continue
        if la == la and lo == lo:  # not NaN
            lats.append(la)
            lons.append(lo)
    for k in ("origin_lat", "destination_lat"):
        v = view.get(k)
        if v is not None:
            lats.append(float(v))
    for k in ("origin_lon", "destination_lon"):
        v = view.get(k)
        if v is not None:
            lons.append(float(v))
    if not lats or not lons:
        raise HTTPException(status_code=422, detail="Active plan has no coordinates.")
    from .cycle import build_region_box
    return build_region_box(lats, lons, GfsConfig().region_pad_deg)


# ---------------------------------------------------------------------------
# Status / readiness
# ---------------------------------------------------------------------------


@router.get("/status")
async def status() -> dict[str, Any]:
    """Readiness + live cycle status (cycle / valid at / fetched at / stale)."""
    st = await asyncio.to_thread(service.cycle_status)
    # add active-plan state so the UI can show "no route" degraded state
    try:
        plan = await asyncio.to_thread(_active_plan)
        st["has_plan"] = True
        st["plan_key"] = plan.get("callsign") or plan.get("origin")
    except HTTPException:
        st["has_plan"] = False
    return st


@router.get("/cycles")
async def cycles() -> dict[str, Any]:
    """Published (completed) weather cycles, most recent first."""
    metas = await asyncio.to_thread(STORE.list_published)
    return {
        "cycles": [m.to_dict() for m in metas[:10]],
        "count": len(metas),
    }


# ---------------------------------------------------------------------------
# Route + samples (from the active SimBrief OFP)
# ---------------------------------------------------------------------------


@router.get("/route")
async def route(
    fl: float = Query(default=340.0, description="Flight level to sample"),
    offset: int = Query(default=0, description="Forecast-hour offset (0..36)"),
) -> dict[str, Any]:
    """Exact route GeoJSON + per-waypoint FL/ETA weather samples."""
    view = await asyncio.to_thread(_active_plan)
    route_data = await asyncio.to_thread(extract_route, view)
    # point the background scheduler at this route box so it stays current
    active_box = None
    try:
        ll, rl, tp, bl = await asyncio.to_thread(_route_box, view)
        await asyncio.to_thread(scheduler.set_active_box, ll, rl, tp, bl)
        active_box = await asyncio.to_thread(scheduler.active_box_key)
    except HTTPException:
        pass
    samples = await asyncio.to_thread(
        service.route_samples, view, _latest_cycle(active_box), fl, offset,
        box=active_box,
    )
    return {**route_data, "samples": samples, "fl": int(round(fl)), "offset": offset}


def _latest_cycle(box: Optional[Any] = None) -> str:
    """Newest published cycle visible to ``box`` (None = any box).

    503 (degraded, not an error) when no dataset usable for the selected box
    has landed yet.
    """
    metas = service.visible_metas(box)
    if not metas:
        scope = "for the selected route box" if box is not None else "yet"
        raise HTTPException(
            status_code=503,
            detail=f"No completed weather cycle available {scope}. The "
                   "scheduler is downloading the newest GFS 0.25 deg cycle in "
                   "the background (see /api/crew/weather/status).",
        )
    return metas[0].cycle_id


# ---------------------------------------------------------------------------
# Hazard layers (GeoJSON)
# ---------------------------------------------------------------------------


@router.get("/layer/{product}")
async def layer(
    product: str,
    fl: float = Query(default=340.0),
    offset: int = Query(default=0),
) -> dict[str, Any]:
    """One hazard product polygonized to a GeoJSON FeatureCollection."""
    if product not in service.PRODUCTS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown product {product!r}. Available: {sorted(service.PRODUCTS)}",
        )
    active_box = await asyncio.to_thread(scheduler.active_box_key)
    cycle = _latest_cycle(active_box)
    try:
        out = await asyncio.to_thread(
            service.hazard_layer, cycle, product, fl, offset, box=active_box
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    out["cycle"] = cycle
    out["source"] = "NOAA GFS 0.25 deg (g2sub) — NOAA-derived proxy, not official WAFS/eWAS"
    return out


# ---------------------------------------------------------------------------
# Manual ingest (test/admin trigger)
# ---------------------------------------------------------------------------


@router.post("/ingest")
async def ingest(
    cycle_id: Optional[str] = Query(default=None, description="YYYYMMDD_HH; default newest"),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Trigger a (cycle, active-route-box) ingest now.

    Used by the test suite and for on-demand refresh. The scheduler normally
    does this automatically; this endpoint lets a developer pin a specific
    cycle and force a re-ingest.
    """
    view = await asyncio.to_thread(_active_plan)
    ll, rl, tp, bl = await asyncio.to_thread(_route_box, view)
    if cycle_id is None:
        config = GfsConfig.from_env()
        cycle_id = await asyncio.to_thread(_discover_newest, config)
    elif not _CYCLE_ID_RE.match(cycle_id):
        raise HTTPException(
            status_code=422,
            detail=f"Malformed cycle_id {cycle_id!r}; expected YYYYMMDD_HH "
                   "(e.g. '20261005_18').",
        )
    req = IngestRequest(cycle_id, ll, rl, tp, bl, offsets=tuple(range(0, 37)))
    config = GfsConfig.from_env()

    def _run() -> dict[str, Any]:
        # share the scheduler's single-ingest lock so a manual job can never
        # run a cycle concurrently with the background one
        if not scheduler.acquire_job():
            raise HTTPException(
                status_code=409,
                detail="A weather ingest is already in progress (scheduler or "
                       "another manual job). Try again shortly.",
            )
        try:
            if not force and STORE.load_meta(cycle_id, req_box(req)) is not None:
                return {"cycle": cycle_id, "already_published": True}
            meta = ingest_cycle(config, req, store=STORE)
            return meta.to_dict()
        finally:
            scheduler.release_job()

    try:
        out = await asyncio.to_thread(_run)
    except GfsCycleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GfsDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        # defense in depth: any other malformed-cycle_id ValueError surfacing
        # from the store/ingest layer is still a client error, never a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return out


def req_box(req: IngestRequest):
    from .store import BoxKey
    return BoxKey(req.leftlon, req.rightlon, req.toplat, req.bottomlat)


def _discover_newest(config: GfsConfig) -> str:
    cyc = _discover_cycles(config)
    if not cyc:
        raise HTTPException(status_code=503, detail="No GFS cycles discovered.")
    date = cyc[0]["date"]
    hours = _discover_hours(config, date)
    if not hours:
        raise HTTPException(status_code=503, detail="No run hours for the newest cycle.")
    return f"{date}_{hours[0]}"


def _discover_cycles(config: GfsConfig):
    from . import cycle as gfs
    return gfs.list_cycles(config)


def _discover_hours(config: GfsConfig, date: str):
    from . import cycle as gfs
    return gfs.list_cycle_hours(config, date)


# ---------------------------------------------------------------------------
# SSE refresh feed — open maps update when a new cycle lands
# ---------------------------------------------------------------------------


@router.get("/events")
async def events():
    """Server-Sent-Events: a `cycle` event fires when a new cycle completes."""
    async def gen():
        last_ts = 0.0
        yield f"event: hello\ndata: {json.dumps({'t': time.time()})}\n\n"
        while True:
            ev = await asyncio.to_thread(scheduler.wait_for_event, 30.0, last_ts)
            if ev is not None:
                last_ts = ev[1]
                yield f"event: cycle\ndata: {json.dumps({'cycle': ev[0], 't': ev[1]})}\n\n"
            else:
                yield ": keep-alive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
