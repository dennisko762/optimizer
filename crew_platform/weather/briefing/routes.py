"""Thin FastAPI routes for the aggregated aviation briefing layer.

Mounted under ``/api/crew/weather/briefing``. All fetch/cache logic lives in
:mod:`crew_platform.weather.briefing.service` and its adapters — browsers
hit only these routes, never the upstream services directly.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from . import service

router = APIRouter(prefix="/api/crew/weather/briefing", tags=["crew-platform"])


@router.get("/metar")
async def metar(ids: str = Query(..., description="Comma-separated ICAO idents")) -> dict[str, Any]:
    idents = [s for s in ids.split(",") if s.strip()]
    if not idents:
        raise HTTPException(status_code=422, detail="ids must contain at least one ICAO ident")
    return {"products": await asyncio.to_thread(service.metar_briefing, idents)}


@router.get("/taf")
async def taf(ids: str = Query(..., description="Comma-separated ICAO idents")) -> dict[str, Any]:
    idents = [s for s in ids.split(",") if s.strip()]
    if not idents:
        raise HTTPException(status_code=422, detail="ids must contain at least one ICAO ident")
    return {"products": await asyncio.to_thread(service.taf_briefing, idents)}


@router.get("/sigmet")
async def sigmet(intl: bool = Query(default=True)) -> dict[str, Any]:
    return {"products": await asyncio.to_thread(service.sigmet_briefing, intl=intl)}


@router.get("/atis/{icao}")
async def atis(icao: str) -> dict[str, Any]:
    """The three-way ATIS/briefing resolution for one station (never
    conflates VATSIM ATIS / Real-world D-ATIS / METAR briefing)."""
    return await asyncio.to_thread(service.atis_for_station, icao)


@router.get("/sigwx")
async def sigwx() -> dict[str, Any]:
    return await asyncio.to_thread(service.sigwx_briefing)


@router.get("/lightning")
async def lightning(
    leftlon: float = Query(...),
    rightlon: float = Query(...),
    bottomlat: float = Query(...),
    toplat: float = Query(...),
) -> dict[str, Any]:
    box = (leftlon, rightlon, bottomlat, toplat)
    return await asyncio.to_thread(service.lightning_briefing, box)


@router.get("/airport/{icao}")
async def airport(icao: str) -> dict[str, Any]:
    meta = await asyncio.to_thread(service.airport_metadata, icao)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"No OurAirports metadata for {icao!r}")
    return meta
