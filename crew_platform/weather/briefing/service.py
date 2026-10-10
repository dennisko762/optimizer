"""Aggregation facade: one call per briefing product kind, server-side only.

Thin routes (:mod:`routes`) call into this module; this module calls the
individual adapters. Keeping the aggregation here (not in routes.py)
matches the existing pattern in :mod:`crew_platform.weather.service`.
"""
from __future__ import annotations

from typing import Any

from . import aviationweather, eumetsat_lightning, ourairports, real_datis, sigwx_wafs, vatsim_atis
from .models import Product


def metar_briefing(idents: list[str]) -> list[dict[str, Any]]:
    return [p.to_dict() for p in aviationweather.fetch_metars(idents)]


def taf_briefing(idents: list[str]) -> list[dict[str, Any]]:
    return [p.to_dict() for p in aviationweather.fetch_tafs(idents)]


def sigmet_briefing(*, intl: bool = True) -> list[dict[str, Any]]:
    return [p.to_dict() for p in aviationweather.fetch_sigmets(intl=intl)]


def vatsim_atis_briefing(icao: str) -> dict[str, Any]:
    return vatsim_atis.fetch_atis(icao).to_dict()


def real_datis_briefing(icao: str) -> dict[str, Any]:
    return real_datis.fetch_datis(icao).to_dict()


def sigwx_briefing() -> dict[str, Any]:
    return sigwx_wafs.fetch_sigwx().to_dict()


def lightning_briefing(box: tuple[float, float, float, float]) -> dict[str, Any]:
    return eumetsat_lightning.fetch_lightning(box).to_dict()


def airport_metadata(icao: str) -> dict[str, Any] | None:
    return ourairports.lookup(icao)


def atis_for_station(icao: str) -> dict[str, Any]:
    """Resolve the one ATIS/briefing product to show for a station,
    in priority order, WITHOUT ever conflating the three labels:

    1. real-world D-ATIS (if an authorized provider is configured) ->
       label ``Real-world D-ATIS — <provider>``
    2. VATSIM ATIS (if the production gate is on and a controller is
       online) -> label ``VATSIM ATIS``
    3. METAR briefing (always available as the honest fallback) ->
       label ``METAR briefing``

    The caller gets all three normalized candidates plus a ``selected``
    key naming which one is primary, so the UI can still show the
    disabled/unavailable states for the others rather than hiding them.
    """
    real = real_datis.fetch_datis(icao)
    vatsim = vatsim_atis.fetch_atis(icao)
    metars = aviationweather.fetch_metars([icao])
    metar = metars[0] if metars else Product.unavailable(
        "metar", "AviationWeather.gov", "METAR", "METAR briefing", "no METAR returned"
    )
    if real.state.value == "ok":
        selected = "real_world_datis"
    elif vatsim.state.value == "ok":
        selected = "vatsim_atis"
    else:
        selected = "metar_briefing"
    return {
        "station": icao,
        "selected": selected,
        "real_world_datis": real.to_dict(),
        "vatsim_atis": vatsim.to_dict(),
        "metar_briefing": metar.to_dict(),
    }
